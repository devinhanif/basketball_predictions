"""F8 lineup-rebounding features (docs/prereg/F8_LINEUP_REBOUNDING.md, frozen sha256 2a8e581e).

Research module: ``nba/`` is untouched, so the ``ContextResidualConfig.lineup_reb`` flag of the doc
does not exist; the ``lr_*`` columns are computed here, joined onto the production feature frame and
passed through an explicit feature-name list (``stat_feature_names(stat, extra=...)``).

Everything A1 reads about game g is dated strictly before g's date, except the roster of g itself
(who is listed for the team; never minutes / stats), the real-tip-gated pre-tip OUT list
``flagged``, and (T-30 columns only, the secondary snapshot) tonight's box-score starter flags,
the documented proxy of the announced five (docs/LINEUPS_KNOWN.md).

Implementation readings fixed before any arm was scored (each is repeated in the results section
of the prereg as "unstated detail, chosen before scoring"):

* big = ``height_in >= 82`` or position contains ``Center`` (the settled definition (b) of the
  doc header), used for ``is_big_p``, ``is_big_q`` and the slices alike.
* ``c(p, q)`` is the F11 construction (prior 10 team games, HL 10, eligible = stint seconds in
  the window and last team before the date = this team, OUT removed, minutes-share fallback when
  p has < 300 window seconds), reimplemented here; ``sum_q c = 4``.
* S(p) (T-60) = p + the four available teammates with the largest ``c`` (ties: more starts in the
  prior 10 team games, then lower player id); with fewer than four available it is shorter.
* T-30 S(p): tonight's box-score starters (when exactly five are flagged for the team-game,
  else the T-60 S). A starter p: the five starters; a bench p: p + the four starters with the
  largest prior-10 co-play weight with p (same tie-breaks). ``c`` is NOT re-weighted at T-30:
  groups A, C and ``lr_nbig`` are identical across snapshots, only ``lr_dbl``, ``lr_h5``,
  ``lr_h2`` change (``lr30_*``).
* Group C: OREB% = sum(n_oreb) / sum(n_oreb + n_dreb) over the possessions the player was on the
  floor on offence (every rebound event of those possessions is a rebound chance); DREB% the same
  on defence. Cumulative over all prior games (no decay), pseudo-count 1,500 chances toward the
  as-of team-season rate (league as-of rate when the team has no earlier game that season).
* ``lr_p_gap`` baseline = as-of (strictly earlier dates) mean ``lr_nbig`` of played rows with the
  same position code (G/F/C/other) in the same season (all seasons when < 200 rows, NaN if none).
* ``lr_chg`` = ``lr_nbig`` minus the mean ``lr_nbig`` of that team's played rows over its previous
  10 games (NaN when it has no previous game).
* Rows with no available teammate carry NaN in every ``lr_*`` column.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from research.eval.f11_lineup_context import _team_games, stint_frames

HL_COPLAY = 10.0
HL_TRAIT = 40.0
HL_MIN = 10.0
WIN_GAMES = 10
PC_MINUTES = 300.0
PC_CHANCES = 1500.0
MIN_STINT_SEC = 300.0
SUM_C = 4.0
BIG_HEIGHT = 82.0
GUARD_HEIGHT = 78.0
PROJ_BIG_MIN = 20.0
LG_O_PRIOR, LG_PM_PRIOR = 0.27, 0.05
HOU = 1610612745

LR_A: tuple[str, ...] = ("lr_oreb_sum", "lr_dreb_sum")
LR_B: tuple[str, ...] = ("lr_nbig", "lr_dbl", "lr_h5", "lr_h2", "lr_p_gap", "lr_chg")
LR_C: tuple[str, ...] = ("lr_oreb_pct", "lr_dreb_pct", "lr_cover")
LR_COLS: tuple[str, ...] = (*LR_A, *LR_B, *LR_C)
LR30: tuple[str, ...] = ("lr30_dbl", "lr30_h5", "lr30_h2")
LR_COLS_T30: tuple[str, ...] = tuple(
    {"lr_dbl": "lr30_dbl", "lr_h5": "lr30_h5", "lr_h2": "lr30_h2"}.get(c, c) for c in LR_COLS
)
AUDIT_COLS: tuple[str, ...] = (
    "lr_csum",
    "lr_navail",
    "lr_prior_poss",
    "lr_has_h",
    "is_big_p",
    "height_in",
    "big_out_proj",
    "big_out_proj_any",
)
_NAN = float("nan")


def is_big_expr() -> pl.Expr:
    return (
        (pl.col("height_in") >= BIG_HEIGHT)
        | pl.col("position").cast(pl.Utf8).str.contains("Center").fill_null(False)
    ).fill_null(False)


def pos_code_expr() -> pl.Expr:
    return (
        pl.col("position")
        .cast(pl.Utf8)
        .str.slice(0, 1)
        .replace_strict({"G": 0, "F": 1, "C": 2}, default=-1)
    )


# --------------------------------------------------------------------------- possession tallies


def poss_tallies(poss: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(per game/team totals, per game/player on-court tallies).

    ``poss``: game_id, off_team, def_team, off_players, def_players, n_oreb, n_dreb.
    Team: t_off_oreb, t_off_ch (own offence), t_def_dreb, t_def_ch (own defence).
    Player: off_oreb, off_ch, def_dreb, def_ch and n_on (on-court possessions, both ends)."""
    base = poss.with_columns(
        pl.col("n_oreb").fill_null(0).cast(pl.Float64),
        pl.col("n_dreb").fill_null(0).cast(pl.Float64),
    ).with_columns((pl.col("n_oreb") + pl.col("n_dreb")).alias("ch"))
    t_off = base.group_by(["game_id", "off_team"]).agg(
        pl.col("n_oreb").sum().alias("t_off_oreb"), pl.col("ch").sum().alias("t_off_ch")
    )
    t_off = t_off.rename({"off_team": "team_id"})
    t_def = base.group_by(["game_id", "def_team"]).agg(
        pl.col("n_dreb").sum().alias("t_def_dreb"), pl.col("ch").sum().alias("t_def_ch")
    )
    t_def = t_def.rename({"def_team": "team_id"})
    team = t_off.join(t_def, on=["game_id", "team_id"], how="full", coalesce=True).with_columns(
        pl.col("t_off_oreb", "t_off_ch", "t_def_dreb", "t_def_ch").fill_null(0.0)
    )
    off_p = (
        base.filter(pl.col("off_players").is_not_null())
        .select(
            "game_id",
            pl.col("off_players").cast(pl.List(pl.Int64)).alias("player_id"),
            "n_oreb",
            "ch",
        )
        .explode("player_id", empty_as_null=True)
        .drop_nulls("player_id")
        .group_by(["game_id", "player_id"])
        .agg(
            pl.col("n_oreb").sum().alias("off_oreb"),
            pl.col("ch").sum().alias("off_ch"),
            pl.len().alias("off_n"),
        )
    )
    def_p = (
        base.filter(pl.col("def_players").is_not_null())
        .select(
            "game_id",
            pl.col("def_players").cast(pl.List(pl.Int64)).alias("player_id"),
            "n_dreb",
            "ch",
        )
        .explode("player_id", empty_as_null=True)
        .drop_nulls("player_id")
        .group_by(["game_id", "player_id"])
        .agg(
            pl.col("n_dreb").sum().alias("def_dreb"),
            pl.col("ch").sum().alias("def_ch"),
            pl.len().alias("def_n"),
        )
    )
    player = (
        off_p.join(def_p, on=["game_id", "player_id"], how="full", coalesce=True)
        .with_columns(pl.all().exclude("game_id", "player_id").fill_null(0.0))
        .with_columns((pl.col("off_n") + pl.col("def_n")).alias("n_on"))
    )
    return team, player


# --------------------------------------------------------------------------- as-of history

_HIST_COLS = (
    "smin",
    "soreb",
    "sdreb",
    "min10",
    "c_oo",
    "c_oc",
    "c_dd",
    "c_dc",
    "c_poss",
)


def trait_history(
    games: pl.DataFrame, pgs: pl.DataFrame, player_tally: pl.DataFrame, team_tally: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Post-game sums per played (player, game) and the league as-of table.

    ``pgs`` needs minutes, oreb, dreb. ``hist``: player_id, game_date, last_team, smin / soreb /
    sdreb (HL 40 decayed sums), min10 (HL 10 weighted mean minutes) and the undecayed on-court
    cumulatives c_oo (own-offence OREB), c_oc (chances), c_dd, c_dc, c_poss. ``league``:
    game_date and cumulative league totals through that date. A target game on date D uses only
    rows with ``game_date < D`` (strict as-of join)."""
    gdate = games.select("game_id", pl.col("game_date").cast(pl.Date))
    played = (
        pgs.filter(pl.col("minutes") > 0)
        .join(gdate, on="game_id")
        .sort(["player_id", "game_date", "game_id"])
        .join(player_tally, on=["game_id", "player_id"], how="left")
        .with_columns(pl.col("off_oreb", "off_ch", "def_dreb", "def_ch", "n_on").fill_null(0.0))
    )
    pid = played["player_id"].to_numpy()
    minutes = played["minutes"].cast(pl.Float64).to_numpy()
    oreb = played["oreb"].fill_null(0).cast(pl.Float64).to_numpy()
    dreb = played["dreb"].fill_null(0).cast(pl.Float64).to_numpy()
    t_oo = played["off_oreb"].to_numpy()
    t_oc = played["off_ch"].to_numpy()
    t_dd = played["def_dreb"].to_numpy()
    t_dc = played["def_ch"].to_numpy()
    t_n = played["n_on"].to_numpy()
    d40 = 0.5 ** (1.0 / HL_TRAIT)
    d10 = 0.5 ** (1.0 / HL_MIN)
    n = played.height
    out = np.zeros((n, len(_HIST_COLS)))
    s = np.zeros(3)
    cum = np.zeros(5)
    m10 = w10 = 0.0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s[:] = 0.0
            cum[:] = 0.0
            m10 = w10 = 0.0
        s[0] = d40 * s[0] + minutes[i]
        s[1] = d40 * s[1] + oreb[i]
        s[2] = d40 * s[2] + dreb[i]
        cum += (t_oo[i], t_oc[i], t_dd[i], t_dc[i], t_n[i])
        m10 = d10 * m10 + minutes[i]
        w10 = d10 * w10 + 1.0
        out[i, :3] = s
        out[i, 3] = m10 / w10
        out[i, 4:] = cum
    hist = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "last_team": played["team_id"].cast(pl.Int64),
            **{c: out[:, j] for j, c in enumerate(_HIST_COLS)},
        }
    ).sort("game_date", maintain_order=True)
    lg_box = (
        played.group_by("game_date")
        .agg(
            pl.col("minutes").cast(pl.Float64).sum().alias("m"),
            pl.col("oreb").fill_null(0).cast(pl.Float64).sum().alias("o"),
            pl.col("dreb").fill_null(0).cast(pl.Float64).sum().alias("d"),
        )
        .sort("game_date")
    )
    tt = (
        team_tally.join(gdate, on="game_id")
        .group_by("game_date")
        .agg(
            pl.col("t_off_oreb").sum().alias("go"),
            pl.col("t_off_ch").sum().alias("gc"),
        )
        .sort("game_date")
    )
    league = (
        lg_box.join(tt, on="game_date", how="full", coalesce=True)
        .sort("game_date")
        .with_columns(pl.all().exclude("game_date").fill_null(0.0))
        .with_columns(
            pl.col("m").cum_sum().alias("cum_min"),
            pl.col("o").cum_sum().alias("cum_oreb"),
            pl.col("d").cum_sum().alias("cum_dreb"),
            pl.col("go").cum_sum().alias("cum_go"),
            pl.col("gc").cum_sum().alias("cum_gc"),
        )
        .select("game_date", "cum_min", "cum_oreb", "cum_dreb", "cum_go", "cum_gc")
    )
    return hist, league


# --------------------------------------------------------------------------- row features


def _top_s(idx: np.ndarray, c: np.ndarray, starts: np.ndarray, k: int = 4) -> np.ndarray:
    """The ``k`` entries of ``idx`` with the largest ``c`` (ties: more starts, then lower id)."""
    order = np.lexsort((idx, -starts[idx], -c))
    return idx[order[:k]]


def _struct(sel: np.ndarray, i: int, big: np.ndarray, h: np.ndarray) -> tuple[float, float, float]:
    s = np.concatenate(([i], sel)).astype(int)
    dbl = float(big[s].sum() >= 2)
    hv = h[s]
    hv = hv[np.isfinite(hv)]
    if hv.size == 0:
        return dbl, _NAN, _NAN
    h5 = float(hv.mean())
    h2 = float(np.sort(hv)[-2:].sum())
    return dbl, h5, h2


def _nan_row() -> dict[str, float | int]:
    r: dict[str, float | int] = {c: _NAN for c in (*LR_COLS, *LR30, "lr_csum")}
    r["lr_navail"] = 0
    return r


def _row(
    i: int,
    elig: np.ndarray,
    out_mask: np.ndarray,
    w_mat: np.ndarray,
    r_sec: np.ndarray,
    min10: np.ndarray,
    oreb_pm: np.ndarray,
    dreb_pm: np.ndarray,
    big: np.ndarray,
    h: np.ndarray,
    starts10: np.ndarray,
    starters: np.ndarray,
    oreb_pct: float,
    dreb_pct: float,
) -> dict[str, float | int]:
    avail = elig & ~out_mask
    avail[i] = False
    idx = np.flatnonzero(avail)
    n_av = int(idx.size)
    if n_av == 0:
        return _nan_row()
    # sums run over the compressed available set: the float result cannot depend on how many
    # unrelated players share the team-level index (byte-identical planted tests)
    wa = w_mat[i, idx]
    wsum = float(wa.sum())
    if r_sec[i] >= MIN_STINT_SEC and wsum > 0:
        c = SUM_C * wa / wsum
        cover = 1.0
    else:
        m = np.maximum(min10[idx], 0.0)
        msum = float(m.sum())
        c = SUM_C * m / msum if msum > 0 else np.full(n_av, SUM_C / n_av)
        cover = 0.0
    sel = _top_s(idx, c, starts10)
    dbl, h5, h2 = _struct(sel, i, big, h)
    dbl30, h530, h230 = dbl, h5, h2
    st = np.flatnonzero(starters)
    if st.size == 5:
        sel30 = st[st != i] if starters[i] else _top_s(st, w_mat[i, st], starts10)
        dbl30, h530, h230 = _struct(sel30, i, big, h)
    return {
        "lr_oreb_sum": float(c @ oreb_pm[idx]),
        "lr_dreb_sum": float(c @ dreb_pm[idx]),
        "lr_nbig": float(big[i] + c @ big[idx]),
        "lr_dbl": dbl,
        "lr_h5": h5,
        "lr_h2": h2,
        "lr_p_gap": _NAN,
        "lr_chg": _NAN,
        "lr_oreb_pct": oreb_pct,
        "lr_dreb_pct": dreb_pct,
        "lr_cover": cover,
        "lr30_dbl": dbl30,
        "lr30_h5": h530,
        "lr30_h2": h230,
        "lr_csum": float(c.sum()),
        "lr_navail": n_av,
    }


def _oracle_row(
    i: int,
    tonight: np.ndarray,
    pair_k: np.ndarray,
    starts10: np.ndarray,
    oreb_pm: np.ndarray,
    dreb_pm: np.ndarray,
    big: np.ndarray,
    h: np.ndarray,
    oreb_pct: float,
    dreb_pct: float,
) -> dict[str, float | int]:
    mask = tonight.copy()
    mask[i] = False
    idx = np.flatnonzero(mask)
    row = pair_k[i, idx]
    rsum = float(row.sum())
    if not tonight[i] or rsum <= 0:
        return _nan_row()
    c = SUM_C * row / rsum
    sel = _top_s(idx, c, starts10)
    dbl, h5, h2 = _struct(sel, i, big, h)
    return {
        "lr_oreb_sum": float(c @ oreb_pm[idx]),
        "lr_dreb_sum": float(c @ dreb_pm[idx]),
        "lr_nbig": float(big[i] + c @ big[idx]),
        "lr_dbl": dbl,
        "lr_h5": h5,
        "lr_h2": h2,
        "lr_p_gap": _NAN,
        "lr_chg": _NAN,
        "lr_oreb_pct": oreb_pct,
        "lr_dreb_pct": dreb_pct,
        "lr_cover": 1.0,
        "lr30_dbl": dbl,
        "lr30_h5": h5,
        "lr30_h2": h2,
        "lr_csum": float(c.sum()),
        "lr_navail": int(idx.size),
    }


# --------------------------------------------------------------------------- builder


def build_lineup_reb(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    stints: pl.DataFrame,
    poss: pl.DataFrame,
    flagged: dict[str, set[int]],
    flagged_any: dict[str, set[int]] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The ``lr_*`` / ``lr30_*`` columns for EVERY (game, team, player) row of ``pgs`` (played or
    not) plus the ORACLE variant (tonight's actual stints; a leak by construction).

    ``pgs``: game_id, player_id, team_id, minutes, oreb, dreb, starter. ``static``: player_id,
    position, height_in. ``poss``: game_id, off_team, def_team, off_players, def_players, n_oreb,
    n_dreb. ``flagged_any`` (default = ``flagged``) feeds only ``big_out_proj_any`` (the
    not-tip-gated coverage count of the gate)."""
    fa = flagged if flagged_any is None else flagged_any
    team_tally, player_tally = poss_tallies(poss)
    hist, league = trait_history(games, pgs, player_tally, team_tally)
    solo, pair = stint_frames(stints)
    tgames = _team_games(games)
    gseason = {str(g): int(s) for g, s in games.select("game_id", "season").iter_rows()}
    solo_by = {int(k[0]): v for k, v in solo.partition_by("team_id", as_dict=True).items()}
    pair_by = {int(k[0]): v for k, v in pair.partition_by("team_id", as_dict=True).items()}
    roster = pgs.select("game_id", pl.col("team_id").cast(pl.Int64), "player_id")
    roster_by = {int(k[0]): v for k, v in roster.partition_by("team_id", as_dict=True).items()}
    pgx = pgs.select(
        "game_id",
        pl.col("team_id").cast(pl.Int64),
        "player_id",
        pl.col("minutes").cast(pl.Float64).fill_null(0.0),
        pl.col("starter").fill_null(False).cast(pl.Float64).alias("started"),
    )
    pgx_by = {int(k[0]): v for k, v in pgx.partition_by("team_id", as_dict=True).items()}
    st_big = static.with_columns(is_big_expr().alias("_big")).select(
        "player_id", "_big", "height_in"
    )
    big_of = {int(p): (float(b), _NAN if h is None else float(h)) for p, b, h in st_big.iter_rows()}
    team_by = {
        int(k[0]): v.sort("game_id")
        for k, v in team_tally.partition_by("team_id", as_dict=True).items()
    }
    a1_rows: list[dict[str, Any]] = []
    or_rows: list[dict[str, Any]] = []
    tg_rows: list[dict[str, Any]] = []
    weights = 0.5 ** (np.arange(WIN_GAMES) / HL_COPLAY)
    for tid, tg in tgames.items():
        ro = roster_by.get(tid)
        if ro is None:
            continue
        gids = tg["game_id"].to_list()
        gdates = tg["game_date"]
        k_n = len(gids)
        gk = {g: i for i, g in enumerate(gids)}
        so = solo_by.get(tid, solo.clear())
        pa = pair_by.get(tid, pair.clear())
        px = pgx_by[tid]
        pl_ids = sorted(
            set(so["p"].to_list()) | set(pa["q"].to_list()) | set(ro["player_id"].to_list())
        )
        p_n = len(pl_ids)
        pidx = {p: i for i, p in enumerate(pl_ids)}
        solo_m = np.zeros((k_n, p_n))
        pair_m = np.zeros((k_n, p_n, p_n))
        min_m = np.zeros((k_n, p_n))
        start_m = np.zeros((k_n, p_n))
        if so.height:
            kk = np.array([gk.get(g, -1) for g in so["game_id"].to_list()])
            ok = kk >= 0
            ii = np.array([pidx[p] for p in so["p"].to_list()])
            solo_m[kk[ok], ii[ok]] = so["sec"].to_numpy()[ok]
        if pa.height:
            kk = np.array([gk.get(g, -1) for g in pa["game_id"].to_list()])
            ok = kk >= 0
            ii = np.array([pidx[p] for p in pa["p"].to_list()])
            jj = np.array([pidx[p] for p in pa["q"].to_list()])
            pair_m[kk[ok], ii[ok], jj[ok]] = pa["sec"].to_numpy()[ok]
        if px.height:
            kk = np.array([gk.get(g, -1) for g in px["game_id"].to_list()])
            ok = kk >= 0
            ii = np.array([pidx[p] for p in px["player_id"].to_list()])
            min_m[kk[ok], ii[ok]] = px["minutes"].to_numpy()[ok]
            start_m[kk[ok], ii[ok]] = px["started"].to_numpy()[ok]
        big = np.array([big_of.get(p, (0.0, _NAN))[0] for p in pl_ids])
        hgt = np.array([big_of.get(p, (0.0, _NAN))[1] for p in pl_ids])
        # as-of traits for every (target game, local player)
        left = pl.DataFrame(
            {
                "game_date": np.repeat(gdates.to_numpy(), p_n) if p_n else np.array([], "<M8[D]"),
                "player_id": np.tile(np.array(pl_ids, dtype=np.int64), k_n),
            }
        ).with_columns(pl.col("game_date").cast(pl.Date), pl.int_range(pl.len()).alias("_i"))
        joined = (
            left.sort("game_date", maintain_order=True)
            .join_asof(
                hist,
                on="game_date",
                by="player_id",
                strategy="backward",
                allow_exact_matches=False,
            )
            .sort("_i")
        )
        lg = (
            pl.DataFrame({"game_date": gdates})
            .join_asof(league, on="game_date", strategy="backward", allow_exact_matches=False)
            .select(
                (pl.col("cum_oreb") / pl.col("cum_min")).fill_null(LG_PM_PRIOR).alias("lgo"),
                (pl.col("cum_dreb") / pl.col("cum_min")).fill_null(LG_PM_PRIOR * 3).alias("lgd"),
                (pl.col("cum_go") / pl.col("cum_gc")).fill_null(LG_O_PRIOR).alias("lg_rate"),
            )
        )
        lgo = np.repeat(lg["lgo"].to_numpy(), p_n).reshape(k_n, p_n)
        lgd = np.repeat(lg["lgd"].to_numpy(), p_n).reshape(k_n, p_n)
        lg_rate = lg["lg_rate"].to_numpy()

        def col(
            name: str, fill: float, _j: pl.DataFrame = joined, _s: tuple[int, int] = (k_n, p_n)
        ) -> np.ndarray:
            return _j[name].fill_null(fill).to_numpy().reshape(_s).astype(float)

        smin, soreb, sdreb = col("smin", 0.0), col("soreb", 0.0), col("sdreb", 0.0)
        oreb_pm_a = (soreb + lgo * PC_MINUTES) / (smin + PC_MINUTES)
        dreb_pm_a = (sdreb + lgd * PC_MINUTES) / (smin + PC_MINUTES)
        min10_a = col("min10", 0.0)
        c_oo, c_oc, c_dd, c_dc = (
            col("c_oo", 0.0),
            col("c_oc", 0.0),
            col("c_dd", 0.0),
            col("c_dc", 0.0),
        )
        c_poss = col("c_poss", 0.0)
        last_team = joined["last_team"].fill_null(-1).to_numpy().reshape(k_n, p_n)
        # as-of team-season rebound rates (own offence OREB%, own defence DREB%)
        tt = tg.select("game_id").join(
            team_by.get(tid, team_tally.clear()), on="game_id", how="left"
        )
        t_oo = tt["t_off_oreb"].fill_null(0.0).to_numpy()
        t_oc = tt["t_off_ch"].fill_null(0.0).to_numpy()
        t_dd = tt["t_def_dreb"].fill_null(0.0).to_numpy()
        t_dc = tt["t_def_ch"].fill_null(0.0).to_numpy()
        team_o = np.zeros(k_n)
        team_d = np.zeros(k_n)
        a_oo = a_oc = a_dd = a_dc = 0.0
        prev_season = None
        for k in range(k_n):
            season_k = gseason.get(gids[k])
            if season_k != prev_season:
                a_oo = a_oc = a_dd = a_dc = 0.0
                prev_season = season_k
            team_o[k] = a_oo / a_oc if a_oc > 0 else lg_rate[k]
            team_d[k] = a_dd / a_dc if a_dc > 0 else 1.0 - lg_rate[k]
            a_oo += t_oo[k]
            a_oc += t_oc[k]
            a_dd += t_dd[k]
            a_dc += t_dc[k]
        oreb_pct_a = (c_oo + PC_CHANCES * team_o[:, None]) / (c_oc + PC_CHANCES)
        dreb_pct_a = (c_dd + PC_CHANCES * team_d[:, None]) / (c_dc + PC_CHANCES)
        ro_g: dict[str, list[int]] = {}
        for g, p in zip(ro["game_id"].to_list(), ro["player_id"].to_list(), strict=True):
            ro_g.setdefault(g, []).append(int(p))
        for k, gid in enumerate(gids):
            rows_p = ro_g.get(gid)
            if not rows_p:
                continue
            w_mat = np.zeros((p_n, p_n))
            r_sec = np.zeros(p_n)
            for a in range(1, min(WIN_GAMES, k) + 1):
                w_mat += weights[a - 1] * pair_m[k - a]
                r_sec += solo_m[k - a]
            lo = max(0, k - WIN_GAMES)
            starts10 = start_m[lo:k].sum(axis=0)
            win_min = min_m[lo:k]
            played_n = (win_min > 0).sum(axis=0)
            avgmin = np.where(played_n > 0, win_min.sum(axis=0) / np.maximum(played_n, 1), 0.0)
            proj_big = (avgmin >= PROJ_BIG_MIN) & (big > 0)
            elig = (r_sec > 0) & (last_team[k] == tid)
            out_mask = np.zeros(p_n, dtype=bool)
            for q in flagged.get(gid, ()):
                if q in pidx:
                    out_mask[pidx[q]] = True
            any_mask = np.zeros(p_n, dtype=bool)
            for q in fa.get(gid, ()):
                if q in pidx:
                    any_mask[pidx[q]] = True
            tg_rows.append(
                {
                    "game_id": gid,
                    "team_id": tid,
                    "big_out_proj": bool((proj_big & out_mask).any()),
                    "big_out_proj_any": bool((proj_big & any_mask).any()),
                }
            )
            starters = start_m[k] > 0
            tonight_p = solo_m[k] > 0
            for p in rows_p:
                i = pidx[p]
                base = {"game_id": gid, "player_id": p, "team_id": tid}
                extra = {
                    "lr_prior_poss": float(c_poss[k, i]),
                    "lr_has_h": float(np.isfinite(hgt[i])),
                    "is_big_p": float(big[i]),
                    "height_in": float(hgt[i]),
                }
                a1_rows.append(
                    {
                        **base,
                        **extra,
                        **_row(
                            i,
                            elig,
                            out_mask,
                            w_mat,
                            r_sec,
                            min10_a[k],
                            oreb_pm_a[k],
                            dreb_pm_a[k],
                            big,
                            hgt,
                            starts10,
                            starters,
                            float(oreb_pct_a[k, i]),
                            float(dreb_pct_a[k, i]),
                        ),
                    }
                )
                or_rows.append(
                    {
                        **base,
                        **_oracle_row(
                            i,
                            tonight_p,
                            pair_m[k],
                            starts10,
                            oreb_pm_a[k],
                            dreb_pm_a[k],
                            big,
                            hgt,
                            float(oreb_pct_a[k, i]),
                            float(dreb_pct_a[k, i]),
                        ),
                    }
                )
    flt = {c: pl.Float64 for c in (*LR_COLS, *LR30, "lr_csum")}
    schema: dict[str, Any] = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "lr_prior_poss": pl.Float64,
        "lr_has_h": pl.Float64,
        "is_big_p": pl.Float64,
        "height_in": pl.Float64,
        **flt,
        "lr_navail": pl.Int64,
    }
    a1 = pl.DataFrame(a1_rows, schema=schema)
    orc = pl.DataFrame(or_rows, schema=schema).drop("lr_prior_poss", "lr_has_h", "height_in")
    tgf = pl.DataFrame(
        tg_rows,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "big_out_proj": pl.Boolean,
            "big_out_proj_any": pl.Boolean,
        },
    )
    a1 = a1.join(tgf, on=["game_id", "team_id"], how="left")
    a1, orc = finish_lr(a1, orc, games, pgs, static)
    return a1.sort(["game_id", "player_id"]), orc.sort(["game_id", "player_id"])


# --------------------------------------------------------------------------- p_gap, chg


def finish_lr(
    a1: pl.DataFrame,
    orc: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Fill ``lr_p_gap`` and ``lr_chg`` of both frames from the A1 history (as-of)."""
    g = games.select("game_id", pl.col("game_date").cast(pl.Date), "season")
    mins = pgs.select("game_id", "player_id", pl.col("minutes").cast(pl.Float64).fill_null(0.0))
    pos = static.select("player_id", pos_code_expr().alias("pos_code")).unique("player_id")
    d = (
        a1.select("game_id", "player_id", "team_id", "lr_nbig")
        .join(g, on="game_id")
        .join(mins, on=["game_id", "player_id"], how="left")
        .join(pos, on="player_id", how="left")
        .with_columns(pl.col("pos_code").fill_null(-1))
    )
    played = d.filter(
        (pl.col("minutes") > 0) & pl.col("lr_nbig").is_not_nan() & pl.col("lr_nbig").is_not_null()
    )
    # --- team prior-10 mean of lr_nbig over played rows
    tgm = (
        played.group_by(["team_id", "game_id", "game_date"])
        .agg(pl.col("lr_nbig").mean().alias("m"))
        .sort(["team_id", "game_date", "game_id"])
        .with_columns(
            pl.col("m")
            .shift(1)
            .rolling_mean(WIN_GAMES, min_samples=1)
            .over("team_id")
            .alias("prior10")
        )
        .select("team_id", "game_id", "prior10")
    )
    # --- as-of league mean by (season, pos_code), falling back to all seasons
    daily = (
        played.group_by(["pos_code", "season", "game_date"])
        .agg(pl.col("lr_nbig").sum().alias("s"), pl.len().alias("n"))
        .sort("game_date")
    )
    cs = daily.with_columns(
        pl.col("s").cum_sum().over(["pos_code", "season"]).alias("cs"),
        pl.col("n").cum_sum().over(["pos_code", "season"]).alias("cn"),
    )
    ca = (
        daily.group_by(["pos_code", "game_date"])
        .agg(pl.col("s").sum(), pl.col("n").sum())
        .sort("game_date")
        .with_columns(
            pl.col("s").cum_sum().over("pos_code").alias("as_"),
            pl.col("n").cum_sum().over("pos_code").alias("an"),
        )
    )

    def fill(frame: pl.DataFrame) -> pl.DataFrame:
        f = (
            frame.drop("lr_p_gap", "lr_chg")
            .join(g, on="game_id")
            .join(pos, on="player_id", how="left")
            .with_columns(pl.col("pos_code").fill_null(-1))
            .with_row_index("_ord")
            .sort("game_date")
        )
        f = f.join_asof(
            cs.select("pos_code", "season", "game_date", "cs", "cn"),
            on="game_date",
            by=["pos_code", "season"],
            strategy="backward",
            allow_exact_matches=False,
        ).join_asof(
            ca.select("pos_code", "game_date", "as_", "an"),
            on="game_date",
            by="pos_code",
            strategy="backward",
            allow_exact_matches=False,
        )
        f = (
            f.sort("_ord")
            .with_columns(
                pl.when(pl.col("cn") >= 200)
                .then(pl.col("cs") / pl.col("cn"))
                .otherwise(pl.col("as_") / pl.col("an"))
                .fill_null(_NAN)
                .alias("_base")
            )
            .join(tgm, on=["team_id", "game_id"], how="left")
            .with_columns(
                (pl.col("lr_nbig") - pl.col("_base")).alias("lr_p_gap"),
                (pl.col("lr_nbig") - pl.col("prior10")).alias("lr_chg"),
            )
            .drop(
                "_ord",
                "game_date",
                "season",
                "pos_code",
                "cs",
                "cn",
                "as_",
                "an",
                "_base",
                "prior10",
            )
        )
        return f.select(frame.columns)

    return fill(a1), fill(orc)
