# ruff: noqa: E501
"""ARCHETYPE_MATCHUP as-of features (rule: docs/prereg/ARCHETYPE_MATCHUP.md, DRAFT).

Read-only. The loaders, the as-of decayed sums, the team-at helper, the OUT-on-opposing-team function and the fixed-k archetype fitting are
the T505 ones (``matchup_availability_features``), imported not copied. Every SQL carries ``season <= 2024`` (through those loaders);
season 2025 is never queried. ``player_game_matchups`` is POST-GAME data of its own game: it enters a feature of game g only through games
strictly before g. The OUT flags are the usable pre-tip official-report OUT set (``tip_source="real"``) the production model sees.

What is built, per A0 row (scorer i, game g, opposing team T):
  roster(T, g)  = defenders who defended for T in T's previous ROSTER_GAMES games within ROSTER_DAYS, whose last known team is T, whose normal
                  defensive minutes per game played (as-of, decayed) are >= MIN_W. ("normal utilization of each defender")
  mix(i; set S) = over j in S: weight w_j * lift[a(i), b(j)] (w_j = normal minutes; lift = how much more than the league base rate an
                  offensive archetype a is guarded by defensive archetype b; "who would defend you, given archetype"), summed by b, normalised.
  usual = mix over the whole roster; tonight = mix over the roster minus the pre-tip OUT players.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.eval import archetype_matchup_rule as rule
from research.eval import matchup_availability_features as t505

# re-exported T505 pieces (one definition of each)
to_day = t505.to_day
read_matchups = t505.read_matchups
read_games = t505.read_games
read_flagged = t505.read_flagged
build_archetypes = t505.build_archetypes
decayed_asof = t505.decayed_asof
team_asof = t505.team_asof
team_keys = t505.team_keys
team_at = t505.team_at
out_opposing = t505.out_opposing
pair_rates = t505.pair_rates


def arch_index(ids: Any, arch: pd.Series, unknown: int) -> np.ndarray:
    """Map player ids to archetype indices 0..k-1; anyone not clustered (below the minutes bar, new, no static data) is ``unknown``."""
    return np.asarray(pd.Series(np.asarray(ids)).map(arch).fillna(unknown).astype(int).to_numpy())


def prior_tables(
    mu: pd.DataFrame, off_arch: pd.Series, def_arch: pd.Series, prior_season: int
) -> dict[str, Any]:
    """Season ``prior_season`` only. A[a, b] = share of offensive archetype a's matchup minutes taken by defensive archetype b ("normal
    share"); pi[b] = defensive archetype b's share of all matchup minutes; lift = clip(A / pi); R[a, b] = allowed points per partial
    possession (thin cells -> league rate, as T505); lg = league rate."""
    rule.check_season(prior_season)
    m = mu[mu.season == prior_season]
    a = arch_index(m.off_player_id, off_arch, rule.UNK_OFF)
    b = arch_index(m.def_player_id, def_arch, rule.UNK_DEF)
    mat = np.zeros((rule.N_OFF, rule.N_DEF))
    np.add.at(mat, (a, b), m.mm.to_numpy(float))
    tot_b = mat.sum(0)
    pi = tot_b / tot_b.sum() if tot_b.sum() > 0 else np.full(rule.N_DEF, 1.0 / rule.N_DEF)
    rows = mat.sum(1, keepdims=True)
    A = np.where(rows > 0, mat / np.where(rows > 0, rows, 1.0), pi[None, :])
    lift = np.clip(A / np.where(pi > 0, pi, 1.0)[None, :], *rule.LIFT_CLIP)
    pr, lg = pair_rates(mu, off_arch, def_arch, prior_season)
    R = np.full((rule.N_OFF, rule.N_DEF), lg)
    for aa, bb, rt in zip(pr.a, pr.b, pr.rate, strict=True):
        R[rule.UNK_OFF if aa < 0 else int(aa), rule.UNK_DEF if bb < 0 else int(bb)] = rt
    return {"A": A, "pi": pi, "lift": lift, "R": R, "lg": float(lg)}


def defender_games(mu: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, defender): team, date, season, total matchup minutes defended (post-game; used only through earlier games)."""
    g = (
        mu.assign(
            mm64=mu.mm.astype("float64")
        )  # float64 sums regardless of the source dtype (Amendment 1)
        .groupby(["game_id", "def_player_id", "def_team"], sort=False)
        .agg(dm=("mm64", "sum"), game_date=("game_date", "first"), season=("season", "first"))
        .reset_index()
    )
    g["day"] = to_day(g.game_date)
    g["j"] = g.def_player_id.astype("int64")
    g["team"] = g.def_team.astype("int64")
    return g[["game_id", "j", "team", "day", "season", "dm"]]


def roster_candidates(dg: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """tg: columns tgi, team, day. Returns (tgi, j): defenders of the team in its previous ROSTER_GAMES games within ROSTER_DAYS, STRICTLY
    before the day. Pure function of earlier games."""
    by_team: dict[int, tuple[np.ndarray, list[np.ndarray]]] = {}
    for team, g in dg.groupby("team", sort=False):
        days: list[int] = []
        plist: list[np.ndarray] = []
        for _gid, gg in g.sort_values("day").groupby("game_id", sort=False):
            days.append(int(gg.day.iloc[0]))
            plist.append(gg.j.to_numpy())
        order = np.argsort(days, kind="stable")
        by_team[int(team)] = (np.asarray(days)[order], [plist[k] for k in order])
    out_t: list[np.ndarray] = []
    out_j: list[np.ndarray] = []
    for tgi, team, day in zip(
        tg.tgi.to_numpy(), tg.team.to_numpy(), tg.day.to_numpy(), strict=True
    ):
        ent = by_team.get(int(team))
        if ent is None:
            continue
        days_arr, plist = ent
        pos = int(np.searchsorted(days_arr, day, side="left"))
        lo = max(0, pos - rule.ROSTER_GAMES)
        keep = [k for k in range(lo, pos) if days_arr[k] >= day - rule.ROSTER_DAYS]
        if not keep:
            continue
        js = np.unique(np.concatenate([plist[k] for k in keep]))
        out_t.append(np.full(len(js), tgi, dtype="int64"))
        out_j.append(js.astype("int64"))
    if not out_t:
        return pd.DataFrame({"tgi": np.array([], "int64"), "j": np.array([], "int64")})
    return pd.DataFrame({"tgi": np.concatenate(out_t), "j": np.concatenate(out_j)})


def normal_minutes(
    dg: pd.DataFrame, cand: pd.DataFrame, tg: pd.DataFrame, season: int
) -> pd.DataFrame:
    """Each candidate's normal defensive minutes per game played: decayed (0.97 per own game), as-of strictly before tonight, current and
    previous season. Adds column ``w``; candidates with no earlier game get 0."""
    ev = pd.DataFrame(
        {
            "ent": dg.j.to_numpy(),
            "oth": 0,
            "day": dg.day.to_numpy(),
            "season": dg.season.to_numpy(),
            "dm": dg.dm.to_numpy(float),
            "one": 1.0,
        }
    )
    t = tg.set_index("tgi")
    q = pd.DataFrame(
        {"ent": cand.j.to_numpy(), "day": t.day.reindex(cand.tgi).to_numpy(), "season": season}
    )
    long, _hist = decayed_asof(ev, q, ["dm", "one"])
    w = np.zeros(len(cand))
    if len(long):
        w[long.qidx.to_numpy()] = (long.dm / long.one).to_numpy()
    return cand.assign(w=w)


def permute_within(group: np.ndarray, values: np.ndarray, seed: int) -> np.ndarray:
    """Shuffled control: permute ``values`` among the members of each ``group`` (defender archetype labels within a team-game)."""
    rng = np.random.default_rng(seed)
    out = np.asarray(values).copy()
    order = np.argsort(group, kind="stable")
    g = np.asarray(group)[order]
    bounds = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    for s, e in zip(bounds[:-1], bounds[1:], strict=True):
        idx = order[s:e]
        out[idx] = out[rng.permutation(idx)]
    return out


def mix_frame(
    rows: pd.DataFrame,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    season: int,
    tables: dict[str, Any],
    off_arch: pd.Series,
    def_arch: pd.Series,
    shuffle_seed: int | None = None,
) -> pd.DataFrame:
    """One output row per input row (same order): the six features plus audit columns. Nothing here reads tonight's outcome or minutes."""
    rule.check_season(season)
    r = rows.reset_index(drop=True).copy()
    r = r.merge(games[["game_id", "home_team", "away_team"]], on="game_id", how="left")
    r["opp_team"] = np.where(r.team_id == r.home_team, r.away_team, r.home_team).astype("int64")
    r["day"] = to_day(r.game_date)
    tg = (
        r[["game_id", "opp_team", "day"]]
        .drop_duplicates()
        .rename(columns={"opp_team": "team"})
        .reset_index(drop=True)
    )
    tg["tgi"] = np.arange(len(tg))
    dg = defender_games(mu)
    cand = roster_candidates(dg, tg)
    cand = normal_minutes(dg, cand, tg, season)
    ta = team_asof(mu)
    t = tg.set_index("tgi")
    cand["team"] = t.team.reindex(cand.tgi).to_numpy()
    cand["day"] = t.day.reindex(cand.tgi).to_numpy()
    cand["game_id"] = t.game_id.reindex(cand.tgi).to_numpy()
    on_team = team_at(team_keys(ta), cand.j.to_numpy(), cand.day.to_numpy()) == cand.team.to_numpy()
    cand = cand[on_team & (cand.w >= rule.MIN_W)].reset_index(drop=True)
    pairs = pd.DataFrame(
        {"game_id": cand.game_id, "oth": cand.j, "day": cand.day, "opp_team": cand.team}
    )
    cand["is_out"] = out_opposing(pairs, flagged, ta).to_numpy()
    b = arch_index(cand.j, def_arch, rule.UNK_DEF)
    if shuffle_seed is not None:
        b = permute_within(cand.tgi.to_numpy(), b, shuffle_seed)
    n_tg = len(tg)
    m_full = np.zeros((n_tg, rule.N_DEF))
    m_av = np.zeros((n_tg, rule.N_DEF))
    np.add.at(m_full, (cand.tgi.to_numpy(), b), cand.w.to_numpy())
    np.add.at(m_av, (cand.tgi.to_numpy(), b), np.where(cand.is_out, 0.0, cand.w.to_numpy()))
    key = dict(zip(zip(tg.game_id, tg.team, strict=True), tg.tgi, strict=True))
    ti = np.array([key[(g, o)] for g, o in zip(r.game_id, r.opp_team, strict=True)])
    a = arch_index(r.player_id, off_arch, rule.UNK_OFF)
    lift = tables["lift"][a]
    fu_raw = m_full[ti] * lift
    ft_raw = m_av[ti] * lift
    su = fu_raw.sum(1)
    st = ft_raw.sum(1)
    no_roster = su <= 0
    fu = np.where(su[:, None] > 0, fu_raw / np.where(su > 0, su, 1.0)[:, None], 0.0)
    ft = np.where(st[:, None] > 0, ft_raw / np.where(st > 0, st, 1.0)[:, None], fu)
    rate = tables["R"][a]
    e_u = (fu * rate).sum(1)
    e_t = (ft * rate).sum(1)
    tot_full = m_full[ti].sum(1)
    out = pd.DataFrame(
        {
            "off_arch": a,
            "e_usual": np.where(no_roster, 0.0, e_u - tables["lg"]),
            "delta_rate": np.where(no_roster, 0.0, e_t - e_u),
            "shift_l1": rule.l1_shift(ft, fu),
            "retained": np.where(
                tot_full > 0, m_av[ti].sum(1) / np.where(tot_full > 0, tot_full, 1.0), 1.0
            ),
            "def_unknown_share": ft[:, rule.UNK_DEF],
            "no_roster": no_roster,
            "has_report": r.game_id.isin(list(flagged.keys())).to_numpy(),
            "n_roster": np.bincount(cand.tgi.to_numpy(), minlength=n_tg)[ti],
            "n_out": np.bincount(cand.tgi.to_numpy()[cand.is_out.to_numpy()], minlength=n_tg)[ti],
        }
    )
    return out
