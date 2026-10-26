"""REPLACEMENT_VALUE_ELO features: value an absence against who actually replaces him.

DRAFT rule ``docs/prereg/REPLACEMENT_VALUE_ELO.md`` (not frozen; nothing here is fitted on an
outcome). Production ``nba.models.injury_elo`` is untouched: it values each OUT / DOUBTFUL player as

    value_i = max(rate_i - 0.80 * r_league, 0) * mins_i

(as-of shrunk per-minute composite ``rate``, as-of shrunk minutes ``mins``). The arms change ONLY
the replacement rate ``0.80 * r_league``; everything else (flag set, snapshot, doubtful weight 0.5,
clip at 0, sign convention away minus home, ``VALUE_SCALE``) is production's:

* ``a0``     production replacement ``0.80 * r_league`` (recomputed here; parity-tested).
* ``pool``   minutes-weighted as-of rate of the team's AVAILABLE as-of roster (eligible players not
             flagged OUT or DOUBTFUL tonight). The minutes are assumed to spread over everyone in
             proportion to their as-of minutes; no single replacement is named (FAN_KNOWLEDGE:
             the absorber is not identifiable pre-tip, same position 43% vs chance 38%).
* ``ladder`` cumulative: the team's vacated minutes ``V = sum w_i mins_i`` (w = 1 OUT, 0.5
             DOUBTFUL) are absorbed by the available roster in proportion to as-of minutes, but no
             one may exceed ``minutes_cap`` (38) in total; the overflow falls down the bench, and
             what nobody can absorb is played at ``0.80 * r_league`` (a call-up). The replacement
             rate is the absorbed-minutes-weighted rate. With one absence it is close to ``pool``;
             with several, the caps bind and the minutes go to weaker players.
* ``pq_pool`` / ``pq_ladder`` placebo: the arm's replacement rate of a DIFFERENT team playing the
             same date (seeded cyclic shift), so the level distribution is kept and the team
             identity is broken.
* ``next``   descriptive only: the single "next man up", the highest-minutes available player
             outside the top ``starters`` by as-of minutes with the absent player's position group
             (``players_static.position``, a current snapshot, not as-of), else the first such
             player of any position.

As-of discipline: the eligible roster for a game on date D is every player with minutes > 0 for the
team in one of its previous ``window_games`` games of the same season whose latest played game
before D was for this team; rosters update only after a whole date is processed. Player state and
the league rate come from games strictly before D (``asof_values`` logic, key D - 1 day). The only
same-game input is the official report snapshot at real tip - 60 min (production rule).
"""

from __future__ import annotations

import datetime as dt
import zlib
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    rotation_flagged_by_team,
)
from nba.models.injury_elo import (
    VALUE_SCALE,
    InjuryFeatureConfig,
    ValueConfig,
    _rotation_state,
    build_value_state,
    league_rate_by_date,
)

ARMS: tuple[str, ...] = ("a0", "pool", "ladder", "pq_pool", "pq_ladder", "next")
FAMILY_ARMS: tuple[str, ...] = ("pool", "ladder")


@dataclass(frozen=True)
class ReplacementConfig:
    base: InjuryFeatureConfig = field(default_factory=InjuryFeatureConfig)
    window_games: int = 10
    minutes_cap: float = 38.0
    starters: int = 5
    placebo_seed: int = 2026


# --------------------------------------------------------------------------
# As-of components (rate, minutes) with production's formulas
# --------------------------------------------------------------------------


def asof_components(
    pairs: pl.DataFrame, state: pl.DataFrame, league: pl.DataFrame, cfg: ValueConfig
) -> pl.DataFrame:
    """``asof_values`` split into its parts, for (game_id, player_id, game_date) rows.

    Returns game_id, player_id, game_date, team_id, n_after, rate, mins, rl (as-of league rate)
    and ``value`` (production's value, identical to ``asof_values``). Players with no prior game
    get n_after 0 and value 0 (rate and mins null)."""
    key = (pl.col("game_date") - pl.duration(days=1)).alias("_key")
    left = pairs.with_columns(key).sort("_key")
    right = state.rename({"game_date": "_prior_date"}).sort("_prior_date")
    j = left.join_asof(
        right, left_on="_key", right_on="_prior_date", by="player_id", strategy="backward"
    )
    lg = league.rename({"game_date": "_ld"}).sort("_ld")
    j = j.sort("_key").join_asof(lg, left_on="_key", right_on="_ld", strategy="backward")
    rl = pl.col("league_rate").fill_null(0.0)
    rate = (pl.col("s_c") + cfg.rate_pseudo_minutes * rl) / (
        pl.col("s_m") + cfg.rate_pseudo_minutes
    )
    mins = (pl.col("s_m") + cfg.minutes_pseudo_games * cfg.prior_minutes) / (
        pl.col("w") + cfg.minutes_pseudo_games
    )
    value = (rate - cfg.replacement_frac * rl).clip(lower_bound=0.0) * mins
    return j.with_columns(
        rate.alias("rate"),
        mins.alias("mins"),
        rl.alias("rl"),
        pl.when(pl.col("n_after").is_null()).then(0.0).otherwise(value).alias("value"),
        pl.col("n_after").fill_null(0),
    ).select(
        ["game_id", "player_id", "game_date", "team_id", "n_after", "rate", "mins", "rl", "value"]
    )


# --------------------------------------------------------------------------
# As-of eligible rosters
# --------------------------------------------------------------------------


def eligible_rosters(
    stats: pl.DataFrame, games: pl.DataFrame, window_games: int = 10
) -> dict[tuple[str, int], set[int]]:
    """Per (game_id, team_id): players eligible tonight, from strictly earlier dates only.

    Eligible = minutes > 0 for the team in one of its previous ``window_games`` games of the same
    season, and the player's latest played game before tonight's date was for this team. Tonight's
    box enters only AFTER the whole date has been assigned (planted-future safe)."""
    played: dict[tuple[str, int], set[int]] = {}
    for pid, team, gid in (
        stats.filter(pl.col("minutes") > 0).select(["player_id", "team_id", "game_id"]).iter_rows()
    ):
        played.setdefault((str(gid), int(team)), set()).add(int(pid))
    recent: dict[tuple[int, int], deque[set[int]]] = {}
    last_team: dict[int, int] = {}
    out: dict[tuple[str, int], set[int]] = {}
    ordered = games.sort(["game_date", "game_id"]).select(
        ["game_id", "game_date", "season", "home_team", "away_team"]
    )
    rows = ordered.rows()
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j][1] == rows[i][1]:
            j += 1
        group = rows[i:j]
        for gid, _d, season, h, a in group:
            for team in (int(h), int(a)):
                window = recent.get((int(season), team))
                cand: set[int] = set().union(*window) if window else set()
                out[(str(gid), team)] = {p for p in cand if last_team.get(p) == team}
        for gid, _d, season, h, a in group:
            for team in (int(h), int(a)):
                tonight = played.get((str(gid), team), set())
                if tonight:
                    recent.setdefault((int(season), team), deque(maxlen=window_games)).append(
                        tonight
                    )
                    for p in tonight:
                        last_team[p] = team
        i = j
    return out


# --------------------------------------------------------------------------
# Absorption
# --------------------------------------------------------------------------


def capped_absorption(m: np.ndarray, vacated: float, cap: float) -> tuple[np.ndarray, float]:
    """Spread ``vacated`` minutes over players with as-of minutes ``m`` in proportion to ``m``,
    no player above ``cap`` in total (water-filling). Returns (extra minutes, unabsorbed)."""
    m = np.asarray(m, dtype=float)
    extra = np.zeros_like(m)
    remaining = float(vacated)
    active = m < cap
    for _ in range(len(m) + 1):
        if remaining <= 1e-12 or not active.any():
            break
        w = m[active]
        share = remaining * w / w.sum() if w.sum() > 0 else np.full(w.size, remaining / w.size)
        head = cap - m[active] - extra[active]
        take = np.minimum(share, head)
        extra[active] += take
        remaining -= float(take.sum())
        active = active & (cap - m - extra > 1e-12)
    return extra, max(remaining, 0.0)


def _placebo_partner(
    keys: list[tuple[str, int]], date: dt.date, seed: int
) -> dict[tuple[str, int], tuple[str, int]]:
    """Cyclic shift by k in [1, n-1] of the sorted team-games of one date (a derangement)."""
    ks = sorted(keys)
    n = len(ks)
    if n < 2:
        return {k: k for k in ks}
    k = 1 + zlib.crc32(f"{date.isoformat()}|{seed}".encode()) % (n - 1)
    return {ks[i]: ks[(i + k) % n] for i in range(n)}


def _position_group(pos: str | None) -> str | None:
    if not pos:
        return None
    return str(pos).split("-")[0].strip().lower() or None


# --------------------------------------------------------------------------
# Per-game features
# --------------------------------------------------------------------------


def _flags(
    rows: pl.DataFrame, cfg: InjuryFeatureConfig
) -> tuple[dict[str, set[int]], dict[str, set[int]], dict[str, dt.datetime]]:
    def rc(status: str) -> ReportTriggerConfig:
        return ReportTriggerConfig(
            statuses=(status,),
            tipoff_hour_et=cfg.tipoff_hour_et,
            lead_minutes=cfg.lead_minutes,
            table=cfg.report_table,
            tip_source=cfg.tip_source,
        )

    out_flag, used = latest_pretip_flagged(rows, rc("out"))
    dbt_flag, _ = latest_pretip_flagged(rows, rc("doubtful"))
    return out_flag, dbt_flag, used


def build_replacement_features(
    games: pl.DataFrame,
    stats: pl.DataFrame,
    report_rows: pl.DataFrame,
    cfg: ReplacementConfig,
    positions: dict[int, str] | None = None,
    absorption_out: list[tuple[str, int, int, float, float, float]] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(per-game features, per-team-game diagnostics).

    ``report_rows``: game_id, player_id, status, as_of, game_date (+ ``tip_et`` when
    ``cfg.base.tip_source == "real"``). Per-game columns: game_id, has_report, n_rot_out,
    n_rot_out_home, n_rot_out_away and, for every arm in ``ARMS``, ``d_out_<arm>`` and
    ``d_doubt_<arm>`` (away minus home, / VALUE_SCALE; positive favours home).
    ``absorption_out`` (optional, diagnostics only) receives one row per available player of a
    team-game with vacated minutes: (game_id, team_id, player_id, as-of mins, predicted extra
    minutes under ``pool`` (uncapped proportional), under ``ladder`` (capped))."""
    base = cfg.base
    vcfg = base.value
    positions = positions or {}
    state = build_value_state(stats, vcfg)
    league = league_rate_by_date(stats)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    out_flag, dbt_flag, used = _flags(report_rows, base)
    roster = eligible_rosters(stats, games, cfg.window_games)

    # flagged players: as-of components, kept only if their as-of team plays tonight
    frec = [
        (gid, pid, info[gid][0], st, 1.0 if st == "out" else base.doubtful_weight)
        for st, flag in (("out", out_flag), ("doubtful", dbt_flag))
        for gid, pids in flag.items()
        if gid in info
        for pid in pids
    ]
    fschema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "status": pl.Utf8,
        "w": pl.Float64,
    }
    fpairs = pl.DataFrame(frec, schema=fschema, orient="row")
    fl = asof_components(fpairs.select(["game_id", "player_id", "game_date"]), state, league, vcfg)
    fl = fl.join(
        fpairs.select(["game_id", "player_id", "status", "w"]), on=["game_id", "player_id"]
    )

    # available roster: eligible minus anyone flagged OUT or DOUBTFUL tonight
    arec = []
    for (gid, team), elig in roster.items():
        gone = out_flag.get(gid, set()) | dbt_flag.get(gid, set())
        for pid in elig - gone:
            arec.append((gid, pid, info[gid][0], team))
    aschema = {"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date, "_team": pl.Int64}
    apairs = pl.DataFrame(arec, schema=aschema, orient="row")
    av = asof_components(apairs.drop("_team"), state, league, vcfg).join(
        apairs.select(["game_id", "player_id", "_team"]), on=["game_id", "player_id"]
    )
    avail: dict[tuple[str, int], list[tuple[int, float, float]]] = {}
    for gid, pid, team, rate, mins, n_after in av.select(
        ["game_id", "player_id", "_team", "rate", "mins", "n_after"]
    ).iter_rows():
        if n_after and rate is not None:
            avail.setdefault((str(gid), int(team)), []).append((int(pid), float(rate), float(mins)))

    # league rate per date (as of), for fallbacks
    rl_of: dict[str, float] = {}
    if games.height:
        lk = asof_components(
            games.select(["game_id", "game_date"]).with_columns(
                pl.lit(-1, pl.Int64).alias("player_id")
            ),
            state,
            league,
            vcfg,
        )
        rl_of = {str(g): float(r) for g, r in lk.select(["game_id", "rl"]).iter_rows()}

    # flagged per team-game, as-of team matching tonight's sides
    flagged_tg: dict[tuple[str, int], list[tuple[int, str, float, float, float, float]]] = {}
    for gid, pid, team, status, w, rate, mins, value, n_after in fl.select(
        ["game_id", "player_id", "team_id", "status", "w", "rate", "mins", "value", "n_after"]
    ).iter_rows():
        _d, h, a = info[str(gid)]
        if team is None or int(team) not in (h, a):
            continue
        flagged_tg.setdefault((str(gid), int(team)), []).append(
            (
                int(pid),
                str(status),
                float(w),
                float(rate) if (n_after and rate is not None) else 0.0,
                float(mins) if (n_after and mins is not None) else 0.0,
                float(value),
            )
        )

    # replacement rates per team-game
    diag = []
    r_rep: dict[str, dict[tuple[str, int], float]] = {"pool": {}, "ladder": {}}
    for gid, (_d, h, a) in info.items():
        rl = rl_of.get(gid, 0.0)
        generic = vcfg.replacement_frac * rl
        for team in (h, a):
            key = (gid, team)
            pool = avail.get(key, [])
            fl_t = flagged_tg.get(key, [])
            vac = sum(w * m for _p, _s, w, _r, m, _v in fl_t)
            if pool:
                m = np.array([x[2] for x in pool])
                r = np.array([x[1] for x in pool])
                r_pool = float((m * r).sum() / m.sum())
                extra, over = capped_absorption(m, vac, cfg.minutes_cap)
                n_capped = int(((m + extra) >= cfg.minutes_cap - 1e-9).sum()) if vac > 0 else 0
                r_lad = float(((extra * r).sum() + over * generic) / vac) if vac > 0 else r_pool
                if absorption_out is not None and vac > 0:
                    prop = vac * m / m.sum()
                    for k, (pid, _r, mi) in enumerate(pool):
                        absorption_out.append((gid, team, pid, mi, float(prop[k]), float(extra[k])))
            else:
                r_pool = r_lad = generic
                over, n_capped = vac, 0
            r_rep["pool"][key] = r_pool
            r_rep["ladder"][key] = r_lad
            diag.append(
                (gid, team, len(pool), len(fl_t), vac, r_pool, r_lad, generic, rl, over, n_capped,
                 not pool)
            )  # fmt: skip

    # placebo partners per date
    by_date: dict[dt.date, list[tuple[str, int]]] = {}
    for gid, (d, h, a) in info.items():
        by_date.setdefault(d, []).extend([(gid, h), (gid, a)])
    partner: dict[tuple[str, int], tuple[str, int]] = {}
    for d, keys in by_date.items():
        partner.update(_placebo_partner(keys, d, cfg.placebo_seed))

    # side sums per arm
    sums: dict[str, dict[str, list[float]]] = {arm: {} for arm in ARMS}
    for key, fl_t in flagged_tg.items():
        gid, team = key
        _d, h, a = info[gid]
        side = 0 if team == h else 1
        rl = rl_of.get(gid, 0.0)
        pool_sorted = sorted(avail.get(key, []), key=lambda x: (-x[2], x[0]))
        bench = pool_sorted[cfg.starters :]
        for pid, status, w, rate, mins, value in fl_t:
            reps = {
                "a0": vcfg.replacement_frac * rl,
                "pool": r_rep["pool"][key],
                "ladder": r_rep["ladder"][key],
                "pq_pool": r_rep["pool"][partner[key]],
                "pq_ladder": r_rep["ladder"][partner[key]],
            }
            grp = _position_group(positions.get(pid))
            same = [
                x for x in bench if grp is not None and _position_group(positions.get(x[0])) == grp
            ]
            pick = same[0] if same else (bench[0] if bench else None)
            reps["next"] = pick[1] if pick is not None else vcfg.replacement_frac * rl
            off = 0 if status == "out" else 2
            for arm, rep in reps.items():
                v = value if arm == "a0" else (max(rate - rep, 0.0) * mins if mins > 0 else 0.0)
                slot = sums[arm].setdefault(gid, [0.0, 0.0, 0.0, 0.0])
                slot[off + side] += w * v

    rot = rotation_flagged_by_team(
        out_flag, info, _rotation_state(state), base.rotation_min_avg, base.rotation_min_games
    )
    out_rows = []
    for gid in games["game_id"].to_list():
        gid = str(gid)
        _d, h, a = info[gid]
        nh = len(rot.get((gid, h), set()))
        na = len(rot.get((gid, a), set()))
        rec: list[object] = [gid, gid in used, nh + na, nh, na]
        for arm in ARMS:
            vo_h, vo_a, vd_h, vd_a = sums[arm].get(gid, [0.0, 0.0, 0.0, 0.0])
            rec += [(vo_a - vo_h) / VALUE_SCALE, (vd_a - vd_h) / VALUE_SCALE]
        out_rows.append(tuple(rec))
    schema: dict[str, pl.DataType | type[pl.DataType]] = {
        "game_id": pl.Utf8,
        "has_report": pl.Boolean,
        "n_rot_out": pl.Int64,
        "n_rot_out_home": pl.Int64,
        "n_rot_out_away": pl.Int64,
    }
    for arm in ARMS:
        schema[f"d_out_{arm}"] = pl.Float64
        schema[f"d_doubt_{arm}"] = pl.Float64
    feats = pl.DataFrame(out_rows, schema=schema, orient="row")
    dschema = {
        "game_id": pl.Utf8,
        "team_id": pl.Int64,
        "n_avail": pl.Int64,
        "n_flagged": pl.Int64,
        "vacated_min": pl.Float64,
        "r_pool": pl.Float64,
        "r_ladder": pl.Float64,
        "r_generic": pl.Float64,
        "r_league": pl.Float64,
        "overflow_min": pl.Float64,
        "n_capped": pl.Int64,
        "fallback": pl.Boolean,
    }
    return feats, pl.DataFrame(diag, schema=dschema, orient="row")
