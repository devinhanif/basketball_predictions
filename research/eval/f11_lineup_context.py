"""F11 lineup context (docs/prereg/F11_LINEUP_CONTEXT.md, frozen).

    M="-m research.eval.f11_lineup_context"
    .venv/bin/python $M checks   # minimum data checks 1-4, 6 (light)
    .venv/bin/python $M repro    # check 5: A0 reproduces LOWER_TAIL pts (heavy.lock)
    .venv/bin/python $M arms     # A0-A4 x 4 stats (heavy; holds heavy.lock)
    .venv/bin/python $M report   # reports/prereg_f11.md from the JSON

Research module only: ``nba/`` is untouched, so the ``ContextResidualConfig.lineup_context`` flag of
the doc does not exist; the five ``lc_*`` columns are computed here, joined on the production
feature frame and passed through an explicit ``names`` list (``stat_feature_names(stat) + columns``)
exactly like ``nba.eval.f9b_young_pick``. Production code paths are therefore byte-identical by
construction (A0 is the unmodified production list).

Read-only on ``nba.duckdb`` (``read_only=True``), season <= 2024 only (asserted in SQL and in
memory); season 2025 is the frozen holdout and is never loaded. A failed minimum-data check stops
the study with no model result.

Implementation readings fixed before any arm was fitted (they are readings of the frozen text, not
rule changes; each is repeated in the report):

* Co-play window = the team's prior 10 games in chronological order (it may cross a season
  boundary), weights ``0.5 ** ((games_ago - 1) / 10)``; "stint seconds in the window" is the raw
  (unweighted) floor seconds of p.
* Eligible teammates = players with stint seconds for the team in that window AND whose latest
  played game strictly before the game date was for this team (departed players drop out). The
  pre-tip roster of the doc is not available as a feed; players who have not yet played for the new
  team cannot be known and are absent.
* The stint path needs p's window seconds >= 300 and positive stint mass over the non-OUT eligible
  teammates; otherwise the whole row uses the ``min10`` fallback (``lc_cover`` = 1 / 0).
* Lift guard: decayed on-court possessions < 1 or off-court possessions < 50 -> lift 0.
* ``lc_top3``: q* = argmax of as-of 3PM per game (HL 40) over ALL eligible teammates incl. OUT
  ones; the column is 0 when q* is OUT or q* = p.
* Rows with no available teammate carry NaN in every ``lc_*`` column.
* Games without a usable pre-tip report are treated as "nobody OUT" for the lc columns.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import EPS, LOW_THRESHOLDS, attach_realised, randomized_pit
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from nba.props.metrics import paired_score_delta_ci

ROOT = Path(__file__).resolve().parents[2]
DOC = Path("docs/prereg/F11_LINEUP_CONTEXT.md")
OUT_DIR = Path("reports/prereg_f11")
REPORT_MD = Path("reports/prereg_f11.md")
CACHE_DIR = Path("data/ops/f11_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
HEAVY_LOCK = ROOT / "data" / "ops" / "heavy.lock"
FROZEN_PREFIX = "2e263279"
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
N_BOOT = 2000

# --- frozen constants (docs/prereg/F11_LINEUP_CONTEXT.md, "Fixed design")
WIN_GAMES = 10
HL_COPLAY = 10.0
HL_TRAIT = 40.0
HL_MIN = 10.0
PC_MINUTES = 300.0
PC_POSS = 1500.0
MIN_STINT_SEC = 300.0
SUM_C = 4.0
MIN_OFF_POSS = 50.0  # implementation reading (lift guard)
LG3_PRIOR, LGR_PRIOR = 1.4 / 36.0, 0.18  # only used before the first game of the data

LC_COLS: tuple[str, ...] = ("lc_thr3", "lc_lift", "lc_rpm", "lc_top3", "lc_cover")
LC_NO_LIFT: tuple[str, ...] = ("lc_thr3", "lc_rpm", "lc_top3", "lc_cover")
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "A4")

# --- thresholds of the minimum data checks and the pass rule
MIN_COVERAGE = 0.95
MAX_NULL_GAP_PP = 2.0
FLOOR = -0.005
ATTACK_FLOOR = -0.003
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
PIT_TOL = 0.02
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
A4_KEEP = 0.5
CHECK6_TOL = 1e-9


def arm_names(stat: str, arm: str, drop_lift: bool = False) -> list[str]:
    """Explicit feature list for ``arm`` (A0 = the production list)."""
    full = LC_NO_LIFT if drop_lift else LC_COLS
    extra: dict[str, tuple[str, ...]] = {
        "A0": (),
        "A1": full,
        "A2": full,
        "A3": full,
        "A4": LC_NO_LIFT,
    }
    return stat_feature_names(stat, extra=extra[arm])


def frozen_sha256(doc: Path = DOC) -> str:
    """sha256 of the doc text above the ``=== RESULTS BELOW ===`` line (the freeze evidence)."""
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


# --------------------------------------------------------------------------- inputs


def load_lineup_inputs(db_path: str = "nba.duckdb") -> tuple[pl.DataFrame, pl.DataFrame]:
    """(stints, possessions) for season <= 2024; read-only. Possessions carry only the columns
    the builder needs (game_id, off_team, off_players, pts)."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        stints = con.execute(
            "SELECT s.game_id, s.team_id, s.period, s.start_clock, s.end_clock, s.players "
            f"FROM stints s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        poss = con.execute(
            "SELECT p.game_id, p.off_team, p.off_players, COALESCE(p.pts, 0) AS pts "
            f"FROM possessions p JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON} "
            "AND p.off_players IS NOT NULL"
        ).pl()
    finally:
        con.close()
    return stints, poss


# --------------------------------------------------------------------------- aggregation


def aggregate_possessions(poss: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(on-court per game/team/player: n_on, pts_on; team totals per game/team: n_tot, pts_tot).
    Offense possessions only: team points per possession."""
    base = poss.select(
        "game_id",
        pl.col("off_team").alias("team_id"),
        pl.col("off_players").cast(pl.List(pl.Int64)).alias("player_id"),
        pl.col("pts").cast(pl.Float64),
    )
    tot = base.group_by(["game_id", "team_id"]).agg(
        pl.len().alias("n_tot"), pl.col("pts").sum().alias("pts_tot")
    )
    on = (
        base.explode("player_id", empty_as_null=True)
        .drop_nulls("player_id")
        .group_by(["game_id", "player_id"])
        .agg(pl.len().alias("n_on"), pl.col("pts").sum().alias("pts_on"))
    )
    return on, tot


def stint_frames(stints: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(solo seconds per game/team/player; ordered pair seconds per game/team/p/q)."""
    empty_solo = pl.DataFrame(
        schema={"game_id": pl.Utf8, "team_id": pl.Int64, "p": pl.Int64, "sec": pl.Float64}
    )
    empty_pair = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "p": pl.Int64,
            "q": pl.Int64,
            "sec": pl.Float64,
        }
    )
    if stints.height == 0:
        return empty_solo, empty_pair
    arr = np.asarray(stints["players"].to_list(), dtype=np.int64).reshape(-1, 5)
    secs = np.clip((stints["start_clock"] - stints["end_clock"]).to_numpy().astype(float), 0, None)
    gid = stints["game_id"]
    tid = stints["team_id"].cast(pl.Int64)
    keys = ["game_id", "team_id"]
    solo_parts: list[pl.DataFrame] = []
    pair_parts: list[pl.DataFrame] = []
    for i in range(5):
        solo_parts.append(
            pl.DataFrame({"game_id": gid, "team_id": tid, "p": arr[:, i], "sec": secs})
            .group_by([*keys, "p"])
            .agg(pl.col("sec").sum())
        )
        for j in range(5):
            if i == j:
                continue
            pair_parts.append(
                pl.DataFrame(
                    {"game_id": gid, "team_id": tid, "p": arr[:, i], "q": arr[:, j], "sec": secs}
                )
                .filter(pl.col("p") != pl.col("q"))
                .group_by([*keys, "p", "q"])
                .agg(pl.col("sec").sum())
            )
    solo = pl.concat(solo_parts).group_by([*keys, "p"]).agg(pl.col("sec").sum())
    pair = pl.concat(pair_parts).group_by([*keys, "p", "q"]).agg(pl.col("sec").sum())
    return solo, pair


# --------------------------------------------------------------------------- as-of traits


def trait_history(
    games: pl.DataFrame, pgs: pl.DataFrame, poss: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Post-game decayed sums per played (player, game) plus the league as-of table.

    Returns ``(hist, league)``. ``hist``: player_id, game_date, team_id, smin, s3, sreb (HL 40
    sums of minutes / 3PM / rebounds), son, spon, soff, spoff (HL 40 sums of on-/off-court
    possessions and team points), min10 (HL 10 weighted mean minutes), fg3pg (HL 40 mean 3PM per
    played game). ``league``: game_date, cum_min, cum_fg3, cum_reb over games dated <= that date.
    A target game on date D uses only rows with ``game_date < D`` (strict as-of join)."""
    on, tot = aggregate_possessions(poss)
    gdate = games.select("game_id", pl.col("game_date").cast(pl.Date), "season")
    played = (
        pgs.filter(pl.col("minutes") > 0)
        .join(gdate, on="game_id")
        .sort(["player_id", "game_date", "game_id"])
        .join(on, on=["game_id", "player_id"], how="left")
        .join(tot, on=["game_id", "team_id"], how="left")
        .with_columns(
            pl.col("n_on").fill_null(0.0).cast(pl.Float64),
            pl.col("pts_on").fill_null(0.0),
            pl.col("n_tot").fill_null(0.0).cast(pl.Float64),
            pl.col("pts_tot").fill_null(0.0),
        )
    )
    # a played game with no on-court possessions for the player (unresolved sub names) adds no
    # lift evidence: otherwise the whole team total would count as "off court"
    has = played["n_on"].to_numpy() > 0
    n_on = played["n_on"].to_numpy()
    pts_on = played["pts_on"].to_numpy()
    n_off = np.where(has, played["n_tot"].to_numpy() - n_on, 0.0)
    pts_off = np.where(has, played["pts_tot"].to_numpy() - pts_on, 0.0)
    pid = played["player_id"].to_numpy()
    minutes = played["minutes"].cast(pl.Float64).to_numpy()
    fg3 = played["fg3m"].cast(pl.Float64).to_numpy()
    reb = played["reb"].cast(pl.Float64).to_numpy()
    d40 = 0.5 ** (1.0 / HL_TRAIT)
    d10 = 0.5 ** (1.0 / HL_MIN)
    n = played.height
    out = np.zeros((n, 9))
    s = np.zeros(7)  # smin s3 sreb son spon soff spoff
    w40 = m10 = w10 = f3 = 0.0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s[:] = 0.0
            w40 = m10 = w10 = f3 = 0.0
        s[0] = d40 * s[0] + minutes[i]
        s[1] = d40 * s[1] + fg3[i]
        s[2] = d40 * s[2] + reb[i]
        s[3] = d40 * s[3] + n_on[i]
        s[4] = d40 * s[4] + pts_on[i]
        s[5] = d40 * s[5] + n_off[i]
        s[6] = d40 * s[6] + pts_off[i]
        w40 = d40 * w40 + 1.0
        f3 = d40 * f3 + fg3[i]
        m10 = d10 * m10 + minutes[i]
        w10 = d10 * w10 + 1.0
        out[i, :7] = s
        out[i, 7] = m10 / w10
        out[i, 8] = f3 / w40
    hist = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"].cast(pl.Int64),
            "smin": out[:, 0],
            "s3": out[:, 1],
            "sreb": out[:, 2],
            "son": out[:, 3],
            "spon": out[:, 4],
            "soff": out[:, 5],
            "spoff": out[:, 6],
            "min10": out[:, 7],
            "fg3pg": out[:, 8],
        }
    ).sort("game_date", maintain_order=True)
    league = (
        played.group_by("game_date")
        .agg(
            pl.col("minutes").cast(pl.Float64).sum().alias("m"),
            pl.col("fg3m").cast(pl.Float64).sum().alias("f"),
            pl.col("reb").cast(pl.Float64).sum().alias("r"),
        )
        .sort("game_date")
        .with_columns(
            pl.col("m").cum_sum().alias("cum_min"),
            pl.col("f").cum_sum().alias("cum_fg3"),
            pl.col("r").cum_sum().alias("cum_reb"),
        )
        .select("game_date", "cum_min", "cum_fg3", "cum_reb")
    )
    return hist, league


def shrunk_traits(
    smin: np.ndarray,
    s3: np.ndarray,
    sreb: np.ndarray,
    son: np.ndarray,
    spon: np.ndarray,
    soff: np.ndarray,
    spoff: np.ndarray,
    lg3: np.ndarray,
    lgr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(thr3, lift, rpm), empirical-Bayes shrunk: rates toward the league as-of rate with a
    pseudo-count of 300 minutes, lift toward 0 with 1,500 on-court possessions."""
    thr3 = 36.0 * (s3 + lg3 * PC_MINUTES) / (smin + PC_MINUTES)
    rpm = (sreb + lgr * PC_MINUTES) / (smin + PC_MINUTES)
    ok = (son >= 1.0) & (soff >= MIN_OFF_POSS)
    raw = np.where(ok, spon / np.maximum(son, 1e-9) - spoff / np.maximum(soff, 1e-9), 0.0)
    lift = np.where(ok, raw * son / (son + PC_POSS), 0.0)
    return thr3, lift, rpm


# --------------------------------------------------------------------------- the builder


def _team_games(games: pl.DataFrame) -> dict[int, pl.DataFrame]:
    g = games.select("game_id", pl.col("game_date").cast(pl.Date), "home_team", "away_team")
    tg = pl.concat(
        [
            g.select("game_id", "game_date", pl.col("home_team").cast(pl.Int64).alias("team_id")),
            g.select("game_id", "game_date", pl.col("away_team").cast(pl.Int64).alias("team_id")),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    return {int(k[0]): v for k, v in tg.partition_by("team_id", as_dict=True).items()}


def build_lineup_context(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    stints: pl.DataFrame,
    poss: pl.DataFrame,
    flagged: dict[str, set[int]],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The five ``lc_*`` columns for EVERY (game, team, player) row of ``pgs`` (played or not),
    plus the ORACLE variant (tonight's actual stints; a leak by construction).

    Returns ``(a1, oracle)`` with columns ``game_id, player_id, team_id, lc_*`` and, for
    ``a1``, the audit columns ``lc_csum`` (sum of c) and ``lc_navail`` (available teammates).

    Everything A1 reads about game g is dated strictly before g's date, except the roster of g
    itself (``pgs`` rows of g: who is listed for the team, never minutes / starters / stats) and
    the pre-tip OUT list ``flagged``."""
    hist, league = trait_history(games, pgs, poss)
    solo, pair = stint_frames(stints)
    tgames = _team_games(games)
    solo_by = {int(k[0]): v for k, v in solo.partition_by("team_id", as_dict=True).items()}
    pair_by = {int(k[0]): v for k, v in pair.partition_by("team_id", as_dict=True).items()}
    roster = pgs.select("game_id", pl.col("team_id").cast(pl.Int64), "player_id")
    roster_by = {int(k[0]): v for k, v in roster.partition_by("team_id", as_dict=True).items()}
    a1_rows: list[dict[str, Any]] = []
    or_rows: list[dict[str, Any]] = []
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
        pl_ids = sorted(
            set(so["p"].to_list()) | set(pa["q"].to_list()) | set(ro["player_id"].to_list())
        )
        p_n = len(pl_ids)
        pidx = {p: i for i, p in enumerate(pl_ids)}
        solo_m = np.zeros((k_n, p_n))
        pair_m = np.zeros((k_n, p_n, p_n))
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
                hist.rename({"team_id": "last_team"}),
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
                (pl.col("cum_fg3") / pl.col("cum_min")).fill_null(LG3_PRIOR).alias("lg3"),
                (pl.col("cum_reb") / pl.col("cum_min")).fill_null(LGR_PRIOR).alias("lgr"),
            )
        )
        lg3 = np.repeat(lg["lg3"].to_numpy(), p_n).reshape(k_n, p_n)
        lgr = np.repeat(lg["lgr"].to_numpy(), p_n).reshape(k_n, p_n)

        def col(
            name: str, fill: float, _j: pl.DataFrame = joined, _s: tuple[int, int] = (k_n, p_n)
        ) -> np.ndarray:
            return _j[name].fill_null(fill).to_numpy().reshape(_s).astype(float)

        thr3_a, lift_a, rpm_a = shrunk_traits(
            col("smin", 0.0),
            col("s3", 0.0),
            col("sreb", 0.0),
            col("son", 0.0),
            col("spon", 0.0),
            col("soff", 0.0),
            col("spoff", 0.0),
            lg3,
            lgr,
        )
        min10_a = col("min10", 0.0)
        fg3pg_a = col("fg3pg", 0.0)
        last_team = joined["last_team"].fill_null(-1).to_numpy().reshape(k_n, p_n)
        ro_g: dict[str, list[int]] = {}
        for g, p in zip(ro["game_id"].to_list(), ro["player_id"].to_list(), strict=True):
            ro_g.setdefault(g, []).append(int(p))
        weights = 0.5 ** (np.arange(WIN_GAMES) / HL_COPLAY)
        for k, gid in enumerate(gids):
            rows_p = ro_g.get(gid)
            if not rows_p:
                continue
            w_mat = np.zeros((p_n, p_n))
            r_sec = np.zeros(p_n)
            for a in range(1, min(WIN_GAMES, k) + 1):
                w_mat += weights[a - 1] * pair_m[k - a]
                r_sec += solo_m[k - a]
            elig = (r_sec > 0) & (last_team[k] == tid)
            out_mask = np.zeros(p_n, dtype=bool)
            for q in flagged.get(gid, ()):
                if q in pidx:
                    out_mask[pidx[q]] = True
            ei = np.flatnonzero(elig)
            qstar = int(ei[np.argmax(fg3pg_a[k][ei])]) if ei.size else -1
            tonight_p = solo_m[k] > 0
            ti = np.flatnonzero(tonight_p)
            qstar_o = int(ti[np.argmax(fg3pg_a[k][ti])]) if ti.size else -1
            for p in rows_p:
                i = pidx[p]
                base = {"game_id": gid, "player_id": p, "team_id": tid}
                a1_rows.append(
                    {
                        **base,
                        **_row_features(
                            i,
                            elig,
                            out_mask,
                            w_mat,
                            r_sec,
                            qstar,
                            thr3_a[k],
                            lift_a[k],
                            rpm_a[k],
                            min10_a[k],
                        ),
                    }
                )
                or_rows.append(
                    {
                        **base,
                        **_oracle_row(
                            i, tonight_p, pair_m[k], qstar_o, thr3_a[k], lift_a[k], rpm_a[k]
                        ),
                    }
                )
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "lc_thr3": pl.Float64,
        "lc_lift": pl.Float64,
        "lc_rpm": pl.Float64,
        "lc_top3": pl.Float64,
        "lc_cover": pl.Float64,
    }
    a1 = pl.DataFrame(a1_rows, schema={**schema, "lc_csum": pl.Float64, "lc_navail": pl.Int64})
    orc = pl.DataFrame(or_rows, schema=schema)
    return a1.sort(["game_id", "player_id"]), orc.sort(["game_id", "player_id"])


_NAN = float("nan")


def _row_features(
    i: int,
    elig: np.ndarray,
    out_mask: np.ndarray,
    w_mat: np.ndarray,
    r_sec: np.ndarray,
    qstar: int,
    thr3: np.ndarray,
    lift: np.ndarray,
    rpm: np.ndarray,
    min10: np.ndarray,
) -> dict[str, float | int]:
    avail = elig & ~out_mask
    avail[i] = False
    n_av = int(avail.sum())
    if n_av == 0:
        return {
            "lc_thr3": _NAN,
            "lc_lift": _NAN,
            "lc_rpm": _NAN,
            "lc_top3": _NAN,
            "lc_cover": _NAN,
            "lc_csum": _NAN,
            "lc_navail": 0,
        }
    # all sums run over the compressed available set so that the float result cannot depend on
    # how many unrelated players happen to share the team-level index (byte-identical planted tests)
    idx = np.flatnonzero(avail)
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
    top3 = (
        0.0
        if (qstar < 0 or qstar == i or out_mask[qstar])
        else float(c[int(np.searchsorted(idx, qstar))])
    )
    return {
        "lc_thr3": float(c @ thr3[idx]),
        "lc_lift": float(c @ lift[idx]),
        "lc_rpm": float(c @ rpm[idx]),
        "lc_top3": top3,
        "lc_cover": cover,
        "lc_csum": float(c.sum()),
        "lc_navail": n_av,
    }


def _oracle_row(
    i: int,
    tonight: np.ndarray,
    pair_k: np.ndarray,
    qstar: int,
    thr3: np.ndarray,
    lift: np.ndarray,
    rpm: np.ndarray,
) -> dict[str, float]:
    mask = tonight.copy()
    mask[i] = False
    idx = np.flatnonzero(mask)
    row = pair_k[i, idx]
    rsum = float(row.sum())
    if not tonight[i] or rsum <= 0:
        return {k: _NAN for k in LC_COLS}
    c = SUM_C * row / rsum
    top3 = 0.0 if (qstar < 0 or qstar == i) else float(c[int(np.searchsorted(idx, qstar))])
    return {
        "lc_thr3": float(c @ thr3[idx]),
        "lc_lift": float(c @ lift[idx]),
        "lc_rpm": float(c @ rpm[idx]),
        "lc_top3": top3,
        "lc_cover": 1.0,
    }


# --------------------------------------------------------------------------- frames


def attach_lc(feats: pl.DataFrame, lc: pl.DataFrame) -> pl.DataFrame:
    """Left-join the five columns onto the production feature frame (played rows)."""
    return feats.join(
        lc.select("game_id", "player_id", *LC_COLS), on=["game_id", "player_id"], how="left"
    )


def add_traded(feats: pl.DataFrame) -> pl.DataFrame:
    """``traded`` = 1 when the player's team differs from his first team of the season (as-of:
    the first team is known from his earlier games), else 0."""
    s = feats.select("game_id", "player_id", "team_id", "season", "game_date").sort(
        ["player_id", "season", "game_date", "game_id"]
    )
    s = s.with_columns(
        (pl.col("team_id") != pl.col("team_id").first().over(["player_id", "season"]))
        .cast(pl.Int8)
        .alias("traded")
    )
    return feats.join(s.select("game_id", "player_id", "traded"), on=["game_id", "player_id"])


def placebo_frame(feats: pl.DataFrame, seed: int = SEEDS["placebo"]) -> pl.DataFrame:
    """A2 placebo: the five-column vector is permuted across rows within team-season (keeps the
    marginals and the joint distribution of the five, breaks the link to the row's lineup)."""
    d = feats.with_row_index("_ord")
    groups = d.group_by(["team_id", "season"], maintain_order=True).agg(pl.col("_ord"))
    rng = np.random.default_rng(seed)
    src = np.arange(d.height)
    for ords in groups["_ord"].to_list():
        o = np.asarray(ords)
        src[o] = o[rng.permutation(len(o))]
    vals = {c: d[c].to_numpy()[src] for c in LC_COLS}
    return d.with_columns([pl.Series(c, v) for c, v in vals.items()]).drop("_ord")


def oracle_frame(feats: pl.DataFrame, oracle: pl.DataFrame) -> pl.DataFrame:
    """A3: the five columns replaced by the oracle construction (tonight's actual stints)."""
    return feats.drop(list(LC_COLS)).join(
        oracle.select("game_id", "player_id", *LC_COLS), on=["game_id", "player_id"], how="left"
    )


# --------------------------------------------------------------------------- checks


def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"check": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def check_1_coverage(
    games: pl.DataFrame, stints: pl.DataFrame, poss: pl.DataFrame
) -> dict[str, Any]:
    """Check 1: share of 2022-24 regular-season games (game_id '002...') with stints / possessions
    for BOTH teams."""
    reg = games.filter(pl.col("game_id").str.starts_with("002") & (pl.col("season") <= MAX_SEASON))
    n = reg.height
    s_ok = (
        stints.group_by("game_id")
        .agg(pl.col("team_id").n_unique().alias("t"))
        .filter(pl.col("t") >= 2)
    )
    p_ok = (
        poss.group_by("game_id")
        .agg(pl.col("off_team").n_unique().alias("t"))
        .filter(pl.col("t") >= 2)
    )
    ns = reg.join(s_ok, on="game_id").height
    npo = reg.join(p_ok, on="game_id").height
    by_season = {
        str(s): {
            "games": int((reg["season"] == s).sum()),
            "stints_both": int(reg.filter(pl.col("season") == s).join(s_ok, on="game_id").height),
            "poss_both": int(reg.filter(pl.col("season") == s).join(p_ok, on="game_id").height),
        }
        for s in sorted(reg["season"].unique().to_list())
    }
    return {
        "n_regular_games": n,
        "stints_cov": ns / n if n else 0.0,
        "poss_cov": npo / n if n else 0.0,
        "by_season": by_season,
    }


def _bucket_minutes(m: pl.Expr) -> pl.Expr:
    return (
        pl.when(m.is_null() | (m <= 0))
        .then(pl.lit("DNP"))
        .when(m < 5)
        .then(pl.lit("<5"))
        .when(m < 10)
        .then(pl.lit("5-10"))
        .when(m <= 20)
        .then(pl.lit("10-20"))
        .otherwise(pl.lit(">20"))
    )


def check_2_missingness(lc: pl.DataFrame, pgs: pl.DataFrame, games: pl.DataFrame) -> dict[str, Any]:
    """Check 2: NULL rate of every lc_* column by realised-minutes bucket and by outcome tercile
    (within season, per stat); max pp gap <= 2. Built for ALL roster rows incl. DNP."""
    d = lc.join(
        pgs.select("game_id", "player_id", "minutes", "pts", "reb", "ast", "fg3m"),
        on=["game_id", "player_id"],
        how="left",
    ).join(games.select("game_id", "season"), on="game_id", how="left")
    d = d.with_columns(_bucket_minutes(pl.col("minutes")).alias("_bucket"))
    audit: list[dict[str, Any]] = []
    gaps: dict[str, float] = {}
    order = ["DNP", "<5", "5-10", "10-20", ">20"]
    for c in LC_COLS:
        rates = []
        for b in order:
            sub = d.filter(pl.col("_bucket") == b)
            # NaN is the builder's NULL
            r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
            audit.append({"col": c, "bucket": b, "null_rate": r, "n": sub.height})
            rates.append(r)
        gaps[f"{c}|minutes"] = (max(rates) - min(rates)) * 100.0
        playedrows = d.filter(pl.col("_bucket") != "DNP")
        for stat in PROP_STATS:
            ter = playedrows.with_columns(
                pl.col(stat).rank("average").over("season").alias("_r"),
                pl.len().over("season").alias("_n"),
            ).with_columns(
                pl.when(pl.col("_r") <= pl.col("_n") / 3)
                .then(pl.lit("T1"))
                .when(pl.col("_r") <= pl.col("_n") * 2 / 3)
                .then(pl.lit("T2"))
                .otherwise(pl.lit("T3"))
                .alias("_ter")
            )
            tr = []
            for t in ("T1", "T2", "T3"):
                sub = ter.filter(pl.col("_ter") == t)
                r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
                audit.append({"col": c, "bucket": f"{stat}:{t}", "null_rate": r, "n": sub.height})
                tr.append(r)
            gaps[f"{c}|{stat}"] = (max(tr) - min(tr)) * 100.0
    return {
        "audit": audit,
        "gaps_pp": gaps,
        "max_gap_pp": max(gaps.values()),
        "overall_null_rate": {
            c: float((d[c].is_null() | d[c].is_nan()).mean())  # type: ignore[arg-type]
            for c in LC_COLS
        },
    }


def check_6_sum_c(a1: pl.DataFrame) -> dict[str, Any]:
    """Check 6: sum_q c(p,q) = 4 within 1e-9 for every row with >= 4 available teammates."""
    d = a1.filter(pl.col("lc_navail") >= 4)
    dev = (d["lc_csum"] - SUM_C).abs()
    return {
        "n_rows_ge4": d.height,
        "max_abs_dev": float(dev.max()) if d.height else None,  # type: ignore[arg-type]
        "n_rows_lt4_with_values": int(
            a1.filter((pl.col("lc_navail") > 0) & (pl.col("lc_navail") < 4)).height
        ),
    }


def lc_equal(a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str] | None = None) -> bool:
    """Byte-identical comparison of the lc columns (NaN == NaN) on the rows of ``game_ids``."""
    key = ["game_id", "player_id"]
    if game_ids is not None:
        a = a.filter(pl.col("game_id").is_in(list(game_ids)))
        b = b.filter(pl.col("game_id").is_in(list(game_ids)))
    a = a.sort(key)
    b = b.sort(key)
    if a.height != b.height or not a.select(key).equals(b.select(key)):
        return False
    return all(np.array_equal(a[c].to_numpy(), b[c].to_numpy(), equal_nan=True) for c in LC_COLS)


def _with_players(stints: pl.DataFrame, arr: np.ndarray) -> pl.DataFrame:
    return stints.with_columns(pl.Series("players", arr, dtype=pl.Array(pl.Int32, 5)))


def plant_same_game(
    pgs: pl.DataFrame, stints: pl.DataFrame, poss: pl.DataFrame, game_ids: set[str]
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Check 3 plant: in the target games replace the stint lineups, insert fake stints, scramble
    possession points, zero the two top-minutes players of each team and flip every starter flag.
    Returns ``(pgs, stints, possessions)``."""
    gl = list(game_ids)
    arr = np.asarray(stints["players"].to_list(), dtype=np.int64).reshape(-1, 5)
    hit = stints["game_id"].is_in(gl).to_numpy()
    st2 = _with_players(stints, np.where(hit[:, None], arr + 7, arr))
    fake = _with_players(
        stints.filter(pl.col("game_id").is_in(gl)).with_columns(
            pl.lit(40.0, dtype=pl.Float32).alias("end_clock")
        ),
        np.tile(np.arange(9_000_001, 9_000_006, dtype=np.int64), (int(hit.sum()), 1)),
    )
    st2 = pl.concat([st2, fake.select(st2.columns)])
    po2 = poss.with_columns(
        pl.when(pl.col("game_id").is_in(gl))
        .then(pl.col("pts") + 3)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    top = (
        pgs.filter(pl.col("game_id").is_in(gl) & (pl.col("minutes") > 0))
        .sort(["game_id", "team_id", "minutes"], descending=[False, False, True])
        .group_by(["game_id", "team_id"], maintain_order=True)
        .head(2)
        .select("game_id", "player_id")
        .with_columns(pl.lit(True).alias("_zero"))
    )
    pg2 = (
        pgs.join(top, on=["game_id", "player_id"], how="left")
        .with_columns(
            pl.when(pl.col("_zero").fill_null(False))
            .then(pl.lit(0.0))
            .otherwise(pl.col("minutes"))
            .cast(pgs.schema["minutes"])
            .alias("minutes"),
            pl.when(pl.col("game_id").is_in(gl))
            .then(~pl.col("starter").fill_null(False))
            .otherwise(pl.col("starter"))
            .alias("starter"),
        )
        .drop("_zero")
    )
    return pg2, st2, po2


def plant_future(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    stints: pl.DataFrame,
    poss: pl.DataFrame,
    cutoff: dt.date,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Check 4 plant: every stint / possession / box row of a game dated >= ``cutoff`` is garbage
    (rotated players, halved end clocks, shifted points, 48 minutes, shifted 3PM)."""
    late = games.filter(pl.col("game_date").cast(pl.Date) >= cutoff)["game_id"].to_list()
    arr = np.asarray(stints["players"].to_list(), dtype=np.int64).reshape(-1, 5)
    hit = stints["game_id"].is_in(late).to_numpy()
    st2 = _with_players(stints, np.where(hit[:, None], np.roll(arr, 1, axis=1), arr)).with_columns(
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("end_clock") * 0.5)
        .otherwise(pl.col("end_clock"))
        .alias("end_clock")
    )
    po2 = poss.with_columns(
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("pts") + 5)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    pg2 = pgs.with_columns(
        pl.when(pl.col("game_id").is_in(late) & (pl.col("minutes") > 0))
        .then(pl.lit(48.0))
        .otherwise(pl.col("minutes"))
        .cast(pgs.schema["minutes"])
        .alias("minutes"),
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("fg3m") + 5)
        .otherwise(pl.col("fg3m"))
        .alias("fg3m"),
    )
    return pg2, st2, po2


# --------------------------------------------------------------------------- shared run state


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def _jd(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_jd))


def load_study_inputs(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    """Games/pgs/stints/possessions (season <= 2024 asserted), flagged, production feature frame."""
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    stints, poss = load_lineup_inputs(db_path)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo)
    feats = attach_realised(feats, games, pgs)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return {
        "games": games,
        "pgs": pgs,
        "avail": avail,
        "stints": stints,
        "poss": poss,
        "flagged": flagged,
        "elo": elo,
        "cfg": cfg,
        "feats": add_traded(feats),
    }


def build_or_load_lc(
    inp: dict[str, Any], refresh: bool = False
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The builder output with an on-disk checkpoint (rebuilt if ``refresh``)."""
    pa, po = CACHE_DIR / "lc_a1.parquet", CACHE_DIR / "lc_oracle.parquet"
    if pa.exists() and po.exists() and not refresh:
        return pl.read_parquet(pa), pl.read_parquet(po)
    a1, orc = build_lineup_context(
        inp["games"], inp["pgs"], inp["stints"], inp["poss"], inp["flagged"]
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    a1.write_parquet(pa)
    orc.write_parquet(po)
    return a1, orc


def run_checks(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Minimum data checks 1-4 and 6 (check 5 is merged in by :func:`run_repro`). Writes
    ``results.json`` and ``checks.json`` (+ ``checks.sha256``) BEFORE any arm may run."""
    t0 = time.time()
    inp = load_study_inputs(db_path)
    games, pgs, stints, poss, flagged = (
        inp["games"],
        inp["pgs"],
        inp["stints"],
        inp["poss"],
        inp["flagged"],
    )
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    c1 = check_1_coverage(games, stints, poss)
    a1, orc = build_or_load_lc(inp, refresh=True)
    print(f"lc built {time.time() - t0:.0f}s ({a1.height} roster rows)", flush=True)
    c2 = check_2_missingness(a1, pgs, games)
    c6 = check_6_sum_c(a1)
    # --- check 3: planted same-game (+ a teammate moved to OUT after the real tip)
    played = pgs.filter(pl.col("minutes") > 0)
    reg24 = games.filter(
        (pl.col("season") == REPORT_SEASON) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list())
    pg2, st2, po2 = plant_same_game(pgs, stints, poss, targets)
    p3, _ = build_lineup_context(games, pg2, st2, po2, flagged)
    upto = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    same_game_ok = lc_equal(a1, p3, upto)
    after_changes = not lc_equal(a1, p3, set(games["game_id"].to_list()) - upto)
    print(f"check 3a {same_game_ok} {time.time() - t0:.0f}s", flush=True)
    late_out, early_out = _post_tip_out_flagged(inp, targets)
    p3b, _ = build_lineup_context(games, pgs, stints, poss, late_out)
    post_tip_ok = lc_equal(a1, p3b)
    p3c, _ = build_lineup_context(games, pgs, stints, poss, early_out)
    control_moves = not lc_equal(a1, p3c, targets)
    print(f"check 3b {post_tip_ok} control {control_moves} {time.time() - t0:.0f}s", flush=True)
    # --- check 4: planted future
    cutoff = dt.date(2024, 1, 15)
    pg4, st4, po4 = plant_future(games, pgs, stints, poss, cutoff)
    p4, _ = build_lineup_context(games, pg4, st4, po4, flagged)
    before = set(games.filter(pl.col("game_date").cast(pl.Date) <= cutoff)["game_id"].to_list())
    future_ok = lc_equal(a1, p4, before)
    differs_after = not lc_equal(a1, p4, set(games["game_id"].to_list()) - before)
    print(
        f"check 4 {future_ok} (later rows differ: {differs_after}) {time.time() - t0:.0f}s",
        flush=True,
    )
    n_played = played.height
    checks = [
        _chk(
            "1 stints cover regular-season games (both teams)",
            c1["stints_cov"],
            f">= {MIN_COVERAGE}",
            c1["stints_cov"] >= MIN_COVERAGE,
        ),
        _chk(
            "1 possessions cover regular-season games (both teams)",
            c1["poss_cov"],
            f">= {MIN_COVERAGE}",
            c1["poss_cov"] >= MIN_COVERAGE,
        ),
        _chk(
            "2 max NULL-rate gap across buckets / terciles (pp)",
            c2["max_gap_pp"],
            f"<= {MAX_NULL_GAP_PP}",
            c2["max_gap_pp"] <= MAX_NULL_GAP_PP,
        ),
        _chk("3 planted same-game: lc_* byte-identical", int(same_game_ok), "== 1", same_game_ok),
        _chk(
            "3 planted same-game is not inert (later games do change)",
            int(after_changes),
            "== 1",
            after_changes,
        ),
        _chk(
            "3 teammate moved to OUT after the real tip: no change",
            int(post_tip_ok),
            "== 1",
            post_tip_ok,
        ),
        _chk(
            "3 control: same teammate OUT BEFORE the tip does change lc_*",
            int(control_moves),
            "== 1",
            control_moves,
        ),
        _chk("4 planted future (>= game date): lc_* unchanged", int(future_ok), "== 1", future_ok),
        _chk(
            "4 planted future is not inert (later games do change)",
            int(differs_after),
            "== 1",
            differs_after,
        ),
        _chk(
            "6 sum_q c(p,q) = 4 for rows with >= 4 available",
            c6["max_abs_dev"],
            f"<= {CHECK6_TOL}",
            c6["n_rows_ge4"] > 0
            and c6["max_abs_dev"] is not None
            and c6["max_abs_dev"] <= CHECK6_TOL,
        ),
    ]
    res: dict[str, Any] = {
        "frozen_sha256": frozen_sha256(),
        "frozen_sha256_prefix_ok": frozen_sha256().startswith(FROZEN_PREFIX),
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "check_1": c1,
        "check_2": {k: v for k, v in c2.items() if k != "audit"},
        "audit": c2["audit"],
        "check_6": c6,
        "check_3_targets": sorted(targets),
        "check_4_cutoff": str(cutoff),
        "n_played_rows": n_played,
        "checks": checks,
        "checks_ok": all(c["ok"] for c in checks) and frozen_sha256().startswith(FROZEN_PREFIX),
        "max_season_loaded": int(inp["feats"]["season"].max()),
    }
    # the check record is hashed and stored before any arm; the arms stage re-verifies the hash
    body = json.dumps(
        {k: res[k] for k in ("frozen_sha256", "checks", "check_1", "check_2", "check_6", "audit")},
        indent=2,
        default=_jd,
        sort_keys=True,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "checks.json").write_text(body)
    res["checks_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    (out_dir / "checks.sha256").write_text(res["checks_sha256"] + "\n")
    prev = out_dir / "results.json"
    if prev.exists():
        old = json.loads(prev.read_text())
        for k in ("check_5", "checks_5_ok"):
            if k in old:
                res[k] = old[k]
    _write_json(prev, res)
    return res


def _post_tip_out_flagged(
    inp: dict[str, Any], targets: set[str]
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """(late, early): ``flagged`` recomputed with an extra OUT row for the top-minutes player of
    each target game stamped 2h AFTER the real tip (the production gate must ignore it), and the
    control with the same row stamped 3h BEFORE the tip (it must count)."""
    from nba.features.game_tipoff import build_game_tipoff

    avail: pl.DataFrame = inp["avail"]
    pgs: pl.DataFrame = inp["pgs"]
    tips = build_game_tipoff().filter(pl.col("game_id").is_in(list(targets)))
    star = (
        pgs.filter(pl.col("game_id").is_in(list(targets)) & (pl.col("minutes") > 0))
        .sort(["game_id", "minutes"], descending=[False, True])
        .group_by("game_id", maintain_order=True)
        .head(1)
        .select("game_id", "player_id")
        .join(tips.select("game_id", "tip_et"), on="game_id")
    )
    keep = ["game_id", "player_id", "status", "as_of", "source"]
    out: list[dict[str, set[int]]] = []
    for delta in (pl.duration(hours=2), pl.duration(hours=-3)):
        extra = star.select(
            "game_id",
            "player_id",
            pl.lit("Out").alias("status"),
            (pl.col("tip_et") + delta).alias("as_of"),
            pl.lit("nba_official_report").alias("source"),
        )
        avail2 = pl.concat(
            [avail.select(keep), extra.cast({c: avail.schema[c] for c in keep})],
            how="vertical_relaxed",
        )
        flagged, _ = flagged_from_availability(avail2, inp["games"], inp["cfg"].report)
        out.append(flagged)
    return out[0], out[1]


# --------------------------------------------------------------------------- arms


META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    "traded",
    *LC_COLS,
)


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, Any]:
    """Walk-forward (month blocks, production protocol, 2022 warm-up) with an explicit feature
    list; integer-support quantile grids ``qi`` ``[n, 199]``, outcomes and row meta."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    qs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for start, end in month_blocks(test_all):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        z = (cal_df["resid"].to_numpy() - mu) / sc
        q = raw_quantiles(c, s, z, CRPS_TAUS)
        qs.append(to_integer_support(q).astype(np.int16))
        ys.append(block["y"].to_numpy())
        metas.append(block.select([c for c in META_COLS if c in block.columns]))
        print(f"  {stat} block {start} done", flush=True)
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def crps_int_by_season(arm: dict[str, Any]) -> dict[int, float]:
    out: dict[int, float] = {}
    for season in TEST_SEASONS:
        sm = (arm["meta"]["season"] == season).to_numpy()
        out[season] = float(crps_from_quantiles(arm["qi"][sm].astype(float), arm["y"][sm]).mean())
    return out


def run_repro(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Check 5: A0 (production names, frame carries the lc columns) reproduces the LOWER_TAIL pts
    integer CRPS 3.1528 (2023) / 3.1884 (2024) to 1e-6, and the A0 feature list holds no lc
    column (the flag is 'off' by construction: nba/ is untouched)."""
    inp = load_study_inputs(db_path)
    a1, _ = build_or_load_lc(inp)
    feats = attach_lc(inp["feats"], a1)
    names = arm_names("pts", "A0")
    assert not set(names) & set(LC_COLS)
    arm = collect_arm(feats, "pts", inp["cfg"], names)
    got = crps_int_by_season(arm)
    ref = json.loads(LOWER_TAIL_JSON.read_text())["pts"]
    doc = {2023: 3.1528, 2024: 3.1884}
    rec: dict[str, Any] = {}
    for season in TEST_SEASONS:
        exact = float(ref[str(season)]["off"]["crps_int"])
        rec[str(season)] = {
            "got": got[season],
            "lower_tail_exact": exact,
            "delta_exact": got[season] - exact,
            "doc_4dp": doc[season],
            "delta_doc_4dp": got[season] - doc[season],
            "n": int((arm["meta"]["season"] == season).sum()),
            "n_ref": int(ref[str(season)]["off"]["n"]),
        }
    ok = all(abs(v["delta_exact"]) <= 1e-6 for v in rec.values())
    res = {"seasons": rec, "ok_1e-6": bool(ok), "a0_has_no_lc_columns": True}
    path = out_dir / "results.json"
    cur = json.loads(path.read_text()) if path.exists() else {}
    cur["check_5"] = res
    cur["checks_5_ok"] = bool(ok)
    chk = [c for c in cur.get("checks", []) if not c["check"].startswith("5 ")]
    for season in TEST_SEASONS:
        chk.append(
            _chk(
                f"5 A0 pts integer CRPS vs LOWER_TAIL {season} (|delta|)",
                abs(rec[str(season)]["delta_exact"]),
                "<= 1e-6",
                abs(rec[str(season)]["delta_exact"]) <= 1e-6,
            )
        )
    chk.append(_chk("5 flag off: A0 feature list contains no lc_* column", 1, "== 1", True))
    cur["checks"] = chk
    cur["all_checks_ok"] = all(c["ok"] for c in chk)
    _write_json(path, cur)
    return res


# --------------------------------------------------------------------------- metrics


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def row_scores(
    qi: np.ndarray, y: np.ndarray, stat: str, seed: int = SEEDS["pit"]
) -> dict[str, Any]:
    """Per-row integer-support scores: CRPS, bias residual, randomized PIT, threshold log loss."""
    qf = qi.astype(float)
    pit = randomized_pit(qf, y, seed)
    thr = LOW_THRESHOLDS[stat]
    tll = np.mean([_ll((qf >= n).mean(axis=1), y >= n) for n in thr], axis=0)
    return {
        "crps": crps_from_quantiles(qf, y),
        "resid": y - qf.mean(axis=1),
        "pit": pit,
        "tll": tll,
    }


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    """Boolean masks over the rows of ``d`` (the six slices of the doc plus ``all``)."""
    dd = add_slice_columns(d)
    return {
        "all": np.ones(d.height, dtype=bool),
        "n_prior<20": (d["n_prior"] < 20).to_numpy(),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "teammate_out": (dd["sl_tm_out"] == "teammate_out").to_numpy(),
        "traded": (d["traded"] == 1).fill_null(False).to_numpy(),
        "lc_cover<0.5": (d["lc_cover"] < 0.5).fill_null(False).to_numpy(),
    }


GUARD_SLICES: tuple[str, ...] = (
    "n_prior<20",
    "starter",
    "bench",
    "first15",
    "teammate_out",
    "traded",
    "lc_cover<0.5",
)
REPORT_SLICES: tuple[str, ...] = ("all", *GUARD_SLICES)


def tercile_masks(d: pl.DataFrame, col: str) -> dict[str, np.ndarray]:
    """Terciles of ``col`` within each season (NaN rows in no tercile)."""
    v = d[col].to_numpy().astype(float)
    season = d["season"].to_numpy()
    out = {f"T{i}": np.zeros(d.height, dtype=bool) for i in (1, 2, 3)}
    for s in np.unique(season):
        m = (season == s) & ~np.isnan(v)
        if not m.any():
            continue
        lo, hi = np.quantile(v[m], [1 / 3, 2 / 3])
        out["T1"] |= m & (v <= lo)
        out["T2"] |= m & (v > lo) & (v <= hi)
        out["T3"] |= m & (v > hi)
    return out


def table_rows(
    stat: str,
    season: int,
    scores: dict[str, dict[str, Any]],
    gid: np.ndarray,
    masks: dict[str, np.ndarray],
    n_boot: int = N_BOOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(arm rows on all rows, A1-vs-A0 slice rows). dcrps = arm - A0 (clustered by game)."""
    arm_rows: list[dict[str, Any]] = []
    all_m = masks["all"]
    for arm in ARMS:
        sc = scores[arm]
        pit = sc["pit"]
        row: dict[str, Any] = {
            "stat": stat,
            "season": season,
            "arm": arm,
            "n_rows": int(all_m.sum()),
            "n_games": int(len(np.unique(gid))),
            "crps_int": float(sc["crps"].mean()),
            "bias": float(sc["resid"].mean()),
            "cov80": float(((pit > 0.10) & (pit <= 0.90)).mean()),
            "pit_q10": float((pit <= 0.10).mean()),
            "pit_q20": float((pit <= 0.20).mean()),
            "tll": float(sc["tll"].mean()),
        }
        if arm == "A0":
            row.update(dcrps=None, ci_lo=None, ci_hi=None, p=None, p_bh=None, mde=None)
        else:
            d = sc["crps"] - scores["A0"]["crps"]
            ci = _ci(d, gid, n_boot)
            row.update(
                dcrps=ci["point"],
                ci_lo=ci["lo"],
                ci_hi=ci["hi"],
                p=boot_p_value(d, gid, n_boot),
                p_bh=None,
                mde=(ci["hi"] - ci["lo"]) / 2.0,
            )
        arm_rows.append(row)
    slice_rows: list[dict[str, Any]] = []
    d1 = scores["A1"]["crps"] - scores["A0"]["crps"]
    for name in REPORT_SLICES:
        m = masks[name]
        n = int(m.sum())
        r: dict[str, Any] = {"stat": stat, "season": season, "slice": name, "n": n}
        if n:
            ci = _ci(d1[m], gid[m], n_boot)
            r.update(dcrps=ci["point"], ci_lo=ci["lo"], ci_hi=ci["hi"])
        slice_rows.append(r)
    return arm_rows, slice_rows


def mechanism_rows(
    stat: str,
    season: int,
    scores: dict[str, dict[str, Any]],
    gid: np.ndarray,
    d: pl.DataFrame,
    n_boot: int = N_BOOT,
) -> list[dict[str, Any]]:
    """Descriptive: A1 - A0 dCRPS by tercile of lc_rpm (reb) / lc_thr3 (pts, fg3m)."""
    col = {"reb": "lc_rpm", "pts": "lc_thr3", "fg3m": "lc_thr3"}.get(stat)
    if col is None:
        return []
    d1 = scores["A1"]["crps"] - scores["A0"]["crps"]
    out = []
    for name, m in tercile_masks(d, col).items():
        n = int(m.sum())
        r: dict[str, Any] = {"stat": stat, "season": season, "by": col, "tercile": name, "n": n}
        if n:
            ci = _ci(d1[m], gid[m], n_boot)
            r.update(dcrps=ci["point"], ci_lo=ci["lo"], ci_hi=ci["hi"])
        out.append(r)
    return out


def attack_rows(
    stat: str,
    season: int,
    scores: dict[str, dict[str, Any]],
    gid: np.ndarray,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Attack-gate inputs (all rows): A1 - A2 (placebo), A1 - A3 (oracle), A1 - A4 (ablation)."""
    out: dict[str, Any] = {"stat": stat, "season": season}
    for other in ("A2", "A3", "A4"):
        d = scores["A1"]["crps"] - scores[other]["crps"]
        out[f"A1_minus_{other}"] = _ci(d, gid, n_boot)
    return out


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the 4 stats' primary A1-vs-A0 p-values (all rows), within each season."""
    for season in TEST_SEASONS:
        tgt = [r for r in rows if r["season"] == season and r["arm"] == "A1"]
        if not tgt:
            continue
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


# --------------------------------------------------------------------------- gates


def evaluate_gates(
    rows: list[dict[str, Any]],
    slices: list[dict[str, Any]],
    attacks: list[dict[str, Any]],
    stat: str,
    season: int,
    tests_ok: bool,
) -> dict[str, Any]:
    """Pass rule 1-3 for one (stat, season); a pure function of the result tables."""
    tab = {(r["stat"], r["season"], r["arm"]): r for r in rows}
    a1, a3, a4 = (tab[(stat, season, k)] for k in ("A1", "A3", "A4"))
    atk = next(a for a in attacks if a["stat"] == stat and a["season"] == season)
    g1 = bool(
        a1["dcrps"] <= FLOOR and a1["ci_hi"] < 0.0 and a1["p_bh"] is not None and a1["p_bh"] < 0.05
    )
    bad = {
        s["slice"]: s["dcrps"]
        for s in slices
        if s["stat"] == stat
        and s["season"] == season
        and s["slice"] != "all"
        and s["n"] >= MIN_SLICE_N
        and s["dcrps"] > SLICE_WORSE
    }
    guards = {
        "abs_bias<=0.5": bool(abs(a1["bias"]) <= MAX_BIAS),
        "cov80_in_[.75,.85]": bool(COV_LO <= a1["cov80"] <= COV_HI),
        "pit_q10_within_.02": bool(abs(a1["pit_q10"] - 0.10) <= PIT_TOL),
        "no_slice_worse_than_+0.01": not bad,
    }
    g2 = all(guards.values())
    gain1 = a1["dcrps"]
    attacks_ok = {
        "A1_minus_A2<=-0.003": bool(atk["A1_minus_A2"]["point"] <= ATTACK_FLOOR),
        "A4_keeps_>=50%_of_A1_gain": bool(gain1 < 0 and a4["dcrps"] <= A4_KEEP * gain1),
        "A1_gain_smaller_than_A3": bool(a3["dcrps"] < gain1),
        "planted_tests_pass": bool(tests_ok),
    }
    g3 = all(attacks_ok.values())
    return {
        "stat": stat,
        "season": season,
        "gate1": g1,
        "gate2": g2,
        "guards": guards,
        "slices_worse": bad,
        "gate3": g3,
        "attacks": attacks_ok,
        "audit_leak_flag_A1_not_worse_than_A3": bool(a3["dcrps"] >= gain1),
        "all": bool(g1 and g2 and g3),
    }


def verdicts(gates: list[dict[str, Any]]) -> dict[str, Any]:
    """2023 selects, 2024 confirms; no stat passing 2023 closes the hypothesis."""
    out: dict[str, Any] = {}
    for stat in PROP_STATS:
        g = {x["season"]: x for x in gates if x["stat"] == stat}
        out[stat] = {
            "passes_2023": bool(g[SELECT_SEASON]["all"]),
            "confirmed_2024": bool(g[SELECT_SEASON]["all"] and g[REPORT_SEASON]["all"]),
            "audit_leak_flag": bool(
                g[SELECT_SEASON]["audit_leak_flag_A1_not_worse_than_A3"]
                or g[REPORT_SEASON]["audit_leak_flag_A1_not_worse_than_A3"]
            ),
        }
    out["any_pass_2023"] = any(out[s]["passes_2023"] for s in PROP_STATS)
    out["close_no_stat_passes_2023"] = not out["any_pass_2023"]
    return out


# --------------------------------------------------------------------------- arms runner


@contextlib.contextmanager
def heavy_lock(lock: Path = HEAVY_LOCK, retry_s: float = 120.0) -> Iterator[None]:
    """mkdir lock (repo convention); retry every 120 s; always removed."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            print(f"heavy.lock held; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)

    def _release(*_: Any) -> None:
        shutil.rmtree(lock, ignore_errors=True)
        raise SystemExit(143)

    old = {s: signal.signal(s, _release) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        shutil.rmtree(lock, ignore_errors=True)


def wait_for_replay(poll_s: float = 60.0) -> None:
    while subprocess.run(["pgrep", "-f", "nba.daily.replay_season"], capture_output=True).stdout:
        print("replay_season running; waiting", flush=True)
        time.sleep(poll_s)


def _arm_cache(stat: str, arm: str) -> Path:
    return CACHE_DIR / f"arm_{stat}_{arm}.npz"


def collect_cached(
    frames: dict[str, pl.DataFrame],
    stat: str,
    arm: str,
    cfg: ContextResidualConfig,
    drop_lift: bool,
) -> dict[str, Any]:
    """One arm with a per-(stat, arm) checkpoint of the integer quantile grid."""
    p = _arm_cache(stat, arm)
    if p.exists():
        z = np.load(p)
        meta = pl.read_parquet(p.with_suffix(".parquet"))
        return {"qi": z["qi"], "y": z["y"], "meta": meta}
    t0 = time.time()
    arm_out = collect_arm(frames[arm], stat, cfg, arm_names(stat, arm, drop_lift))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=arm_out["qi"], y=arm_out["y"])
    arm_out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {arm} done in {time.time() - t0:.0f}s", flush=True)
    return arm_out


def score_stat(
    stat: str, arms: dict[str, dict[str, Any]], n_boot: int = N_BOOT
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(arm rows, slice rows, mechanism rows, attack rows) for one stat from the collected arms."""
    ref = arms["A0"]["meta"]
    for a in ARMS:
        assert (
            arms[a]["meta"]
            .select("game_id", "player_id")
            .equals(ref.select("game_id", "player_id"))
        ), "arms must score identical rows"
        assert np.array_equal(arms[a]["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    mech: list[dict[str, Any]] = []
    attacks: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        scores = {a: row_scores(arms[a]["qi"][sm], arms["A0"]["y"][sm], stat) for a in ARMS}
        masks = slice_masks(d)
        r_s, s_s = table_rows(stat, season, scores, gid, masks, n_boot)
        rows += r_s
        sl += s_s
        mech += mechanism_rows(stat, season, scores, gid, d, n_boot)
        attacks.append(attack_rows(stat, season, scores, gid, n_boot))
    return rows, sl, mech, attacks


def run_arms(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """A0-A4 x 4 stats, select 2023 / report 2024. Refuses to run unless checks 1-6 all passed
    and the stored check record still matches its sha256."""
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    stored = (out_dir / "checks.json").read_bytes()
    if hashlib.sha256(stored).hexdigest() != cur.get("checks_sha256"):
        raise RuntimeError("checks.json does not match its recorded sha256")
    if not (cur.get("checks_ok") and cur.get("checks_5_ok")):
        raise RuntimeError("minimum data checks did not all pass: no arm may be fitted")
    inp = load_study_inputs(db_path)
    a1, orc = build_or_load_lc(inp)
    drop_lift = bool(cur["check_1"]["poss_cov"] < MIN_COVERAGE)
    base = attach_lc(inp["feats"], a1)
    frames = {
        "A0": base,
        "A1": base,
        "A2": placebo_frame(base),
        "A3": oracle_frame(base, orc),
        "A4": base,
    }
    if drop_lift:
        frames = {k: v.drop("lc_lift") for k, v in frames.items()}
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    mech: list[dict[str, Any]] = []
    attacks: list[dict[str, Any]] = []
    for stat in PROP_STATS:
        arms = {a: collect_cached(frames, stat, a, inp["cfg"], drop_lift) for a in ARMS}
        r_s, s_s, m_s, a_s = score_stat(stat, arms, n_boot=n_boot)
        rows += r_s
        sl += s_s
        mech += m_s
        attacks += a_s
        print(f"{stat} scored", flush=True)
        del arms
    add_bh(rows)
    tests_ok = all(c["ok"] for c in cur["checks"])
    gates = [
        evaluate_gates(rows, sl, attacks, st, se, tests_ok)
        for st in PROP_STATS
        for se in TEST_SEASONS
    ]
    res = {
        "table": rows,
        "slices": sl,
        "mechanism": mech,
        "attack": attacks,
        "gates": gates,
        "verdicts": verdicts(gates),
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "frozen_sha256": frozen_sha256(),
        "n_boot": n_boot,
        "lift_dropped_for_all_arms": drop_lift,
    }
    cur["arms"] = res
    _write_json(path, cur)
    return res


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return ""
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    """Markdown from ``results.json``: hash, checks vs thresholds, A0 reproduction, tables, gates,
    proposed ledger rows and draft results-section text (nothing is appended to any doc)."""
    r = json.loads((out_dir / "results.json").read_text())
    sha = r["frozen_sha256"]
    ln = [
        "# F11 lineup context: results (docs/prereg/F11_LINEUP_CONTEXT.md)",
        "",
        "## Hash verification",
        "",
        f"`awk '/^=== RESULTS BELOW ===$/{{exit}} {{print}}' docs/prereg/F11_LINEUP_CONTEXT.md | "
        f"shasum -a 256` = `{sha}`; frozen prefix `{FROZEN_PREFIX}` matches: "
        f"{r.get('frozen_sha256_prefix_ok')}. Freeze commit b5a1dab. git SHA at run: "
        f"`{r['git_sha']}`. Seeds: {r['seeds']}. CPU only. Seasons <= 2024 only (max season "
        f"loaded: {r.get('max_season_loaded')}); nba.duckdb read-only. Check record sha256 "
        f"(written before any arm): `{r.get('checks_sha256')}`.",
        "",
        "## Minimum data checks (each vs its threshold)",
        "",
        "| check | value | threshold | ok |",
        "|---|---|---|---|",
    ]
    for c in r["checks"]:
        v = c["value"]
        vs = f"{v:.6g}" if isinstance(v, float) else str(v)
        ln.append(f"| {c['check']} | {vs} | {c['threshold']} | {'ok' if c['ok'] else 'FAIL'} |")
    ln += ["", "Check 1 detail:", "", "```", json.dumps(r["check_1"], indent=1), "```", ""]
    ln += ["Check 2 (gaps in pp per column|split):", "", "```"]
    ln.append(json.dumps(r["check_2"], indent=1))
    ln += [
        "```",
        "",
        "Check 2 audit, `[col, bucket, null_rate]` (minutes buckets; terciles in results.json):",
        "",
    ]
    ln += ["| col | bucket | null_rate | n |", "|---|---|---|---|"]
    for a in r["audit"]:
        if ":" not in a["bucket"]:
            ln.append(f"| {a['col']} | {a['bucket']} | {a['null_rate']:.5f} | {a['n']} |")
    ln += ["", "Check 6:", "", "```", json.dumps(r["check_6"], indent=1), "```", ""]
    if "check_5" in r:
        ln += [
            "A0 reproduction (check 5):",
            "",
            "```",
            json.dumps(r["check_5"], indent=1),
            "```",
            "",
        ]
    arms = r.get("arms")
    if not arms:
        ln += ["Arms not run (a check failed or the arms stage has not been executed).", ""]
        return "\n".join(ln)
    ln += [
        "## Results (dCRPS = arm - A0, integer support, game-clustered 95% CI, 2000 resamples)",
        "",
        "mde = 1.96 x clustered SE. bias = stat - forecast mean. cov80 = central 80% PIT "
        "coverage (0.10 < PIT <= 0.90). tll = mean threshold log loss at the declared "
        "thresholds (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2). p_bh: BH over the 4 stats of the "
        "A1 all-rows p, per season.",
        "",
        "| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh "
        "| mde | bias | cov80 | pit_q10 | pit_q20 | tll |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in arms["table"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {t['arm']} | {t['n_rows']} | {t['n_games']} | "
            f"{_f(t['crps_int'])} | {_f(t['dcrps'], 4, True)} | {_f(t['ci_lo'], 4, True)} | "
            f"{_f(t['ci_hi'], 4, True)} | {_f(t['p'], 3)} | {_f(t['p_bh'], 3)} | {_f(t['mde'])} | "
            f"{_f(t['bias'], 3, True)} | {_f(t['cov80'], 3)} | {_f(t['pit_q10'], 3)} | "
            f"{_f(t['pit_q20'], 3)} | {_f(t['tll'])} |"
        )
    ln += ["", "## Slices (A1 - A0 dCRPS, game-clustered CI)", ""]
    ln += ["| stat | season | slice | n | dcrps | ci_lo | ci_hi |", "|---|---|---|---|---|---|---|"]
    for s in arms["slices"]:
        ln.append(
            f"| {s['stat']} | {s['season']} | {s['slice']} | {s['n']} | "
            f"{_f(s.get('dcrps'), 4, True)} | {_f(s.get('ci_lo'), 4, True)} | "
            f"{_f(s.get('ci_hi'), 4, True)} |"
        )
    ln += ["", "## Mechanism check (descriptive; expected monotone if the story is real)", ""]
    ln += [
        "| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in arms["mechanism"]:
        ln.append(
            f"| {m['stat']} | {m['season']} | {m['by']} | {m['tercile']} | {m['n']} | "
            f"{_f(m.get('dcrps'), 4, True)} | {_f(m.get('ci_lo'), 4, True)} | "
            f"{_f(m.get('ci_hi'), 4, True)} |"
        )
    ln += ["", "## Attack gate inputs (all rows; paired dCRPS, A1 minus other)", ""]
    ln += ["| stat | season | A1-A2 [CI] | A1-A3 [CI] | A1-A4 [CI] |", "|---|---|---|---|---|"]

    def cell(d: dict[str, float]) -> str:
        return f"{d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]"

    for a in arms["attack"]:
        ln.append(
            f"| {a['stat']} | {a['season']} | {cell(a['A1_minus_A2'])} | "
            f"{cell(a['A1_minus_A3'])} | {cell(a['A1_minus_A4'])} |"
        )
    ln += ["", "## Gates", "", "| stat | season | g1 floor/CI/BH | g2 guards | g3 attacks | pass |"]
    ln.append("|---|---|---|---|---|---|")
    for g in arms["gates"]:
        ln.append(
            f"| {g['stat']} | {g['season']} | {g['gate1']} | {g['gate2']} | {g['gate3']} | "
            f"{g['all']} |"
        )
    ln += ["", "Gate detail:", "", "```"]
    for g in arms["gates"]:
        ln.append(
            f"{g['stat']} {g['season']}: guards={json.dumps(g['guards'])} "
            f"slices_worse={json.dumps(g['slices_worse'])} attacks={json.dumps(g['attacks'])} "
            f"leak_flag={g['audit_leak_flag_A1_not_worse_than_A3']}"
        )
    ln += ["```", "", "## Verdicts", "", "```", json.dumps(arms["verdicts"], indent=1), "```", ""]
    ln += _ledger_text(r)
    ln += _results_section(r)
    reading = out_dir / "reading.txt"
    if reading.exists():
        ln += ["", "## Reading", "", reading.read_text().strip(), ""]
    notes = out_dir / "notes.txt"
    if notes.exists():
        ln += ["## Run notes", "", notes.read_text().strip(), ""]
    return "\n".join(ln)


def _results_section(r: dict[str, Any]) -> list[str]:
    arms = r["arms"]
    tab = {(t["stat"], t["season"], t["arm"]): t for t in arms["table"]}
    pre = r["frozen_sha256"][:8]
    ok = all(c["ok"] for c in r["checks"])
    ln = [
        "",
        "## Draft results-section text (for docs/prereg/F11_LINEUP_CONTEXT.md; not appended)",
        "",
        f"Frozen sha256 (first 8): {pre}. Checks 1-6: {'all passed' if ok else 'FAILED'}; "
        f"seasons <= 2024 only, nba.duckdb read-only, CPU, seeds {r['seeds']}.",
        "",
    ]
    for stat in PROP_STATS:
        parts = []
        for season in TEST_SEASONS:
            a1 = tab[(stat, season, "A1")]
            parts.append(
                f"{season}: n={a1['n_rows']} dCRPS {a1['dcrps']:+.4f} "
                f"[{a1['ci_lo']:+.4f}, {a1['ci_hi']:+.4f}] p_BH {a1['p_bh']:.3f} "
                f"MDE {a1['mde']:.4f}"
            )
        ln.append(f"* {stat}: " + " | ".join(parts))
    v = arms["verdicts"]
    ln += [
        "",
        f"Verdict: confirmed on 2024 = {[s for s in PROP_STATS if v[s]['confirmed_2024']]}; "
        f"passes on 2023 = {[s for s in PROP_STATS if v[s]['passes_2023']]}; kill criterion "
        f"'no stat passes 2023: close' = {v['close_no_stat_passes_2023']}. Nothing promoted.",
        "",
        "Implementation readings fixed before any arm was fitted (not rule changes): see the "
        "module docstring of research/eval/f11_lineup_context.py. The doc's "
        "`ContextResidualConfig.lineup_context` flag was not added (nba/ is out of scope); the "
        "columns enter through explicit feature-name lists, so production is untouched.",
    ]
    return ln


def _ledger_text(r: dict[str, Any]) -> list[str]:
    arms = r["arms"]
    tab = {(t["stat"], t["season"], t["arm"]): t for t in arms["table"]}
    pre = r["frozen_sha256"][:8]
    ln = [
        "## Proposed ledger rows (draft, unnumbered; maintainer reviews and appends)",
        "",
        "Columns follow docs/TEST_LEDGER.md: id | date | test | result | effect | CI lo | CI hi | "
        "cluster | p(BH) | notes | holdout status.",
        "",
    ]
    for stat in PROP_STATS:
        for season in TEST_SEASONS:
            t = tab[(stat, season, "A1")]
            ln.append(
                f"| T### | 2026-10-10 | F11_LINEUP_CONTEXT (frozen sha256 {pre}) | {stat} {season} "
                f"A1 vs A0, integer-support CRPS, n={t['n_rows']} rows / {t['n_games']} games | "
                f"{t['dcrps']:+.4f} | {t['ci_lo']:+.4f} | {t['ci_hi']:+.4f} | game | "
                f"{_f(t['p_bh'], 3)} | MDE {t['mde']:.4f}; "
                f"bias {tab[(stat, season, 'A0')]['bias']:+.3f}"
                f" -> {t['bias']:+.3f}; cov80 {tab[(stat, season, 'A0')]['cov80']:.3f} -> "
                f"{t['cov80']:.3f}; A2 {tab[(stat, season, 'A2')]['dcrps']:+.4f}, A3 oracle "
                f"{tab[(stat, season, 'A3')]['dcrps']:+.4f}, "
                f"A4 {tab[(stat, season, 'A4')]['dcrps']:+.4f} "
                f"| not holdout (<=2024) |"
            )
    conf = [s for s in PROP_STATS if arms["verdicts"][s]["confirmed_2024"]]
    ln += ["", "## Drafted holdout row text (not appended)", ""]
    if conf:
        for s in conf:
            ln.append(
                f"- F11 {s}: confirmed on 2023 and 2024; request ONE logged holdout touch on "
                f"season 2025 for the A1 arm (frozen sha256 {pre}) before running; shadow-log "
                "recommendation only, nothing promoted."
            )
    else:
        ln.append("- No stat confirmed; no holdout row is requested.")
    return ln


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("checks", "repro", "arms", "report"))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "checks":
        res = run_checks(a.db_path)
        print(json.dumps({k: res[k] for k in ("frozen_sha256", "checks", "checks_ok")}, indent=1))
    elif a.stage == "repro":
        wait_for_replay()
        with heavy_lock():
            print(json.dumps(run_repro(a.db_path), indent=1))
    elif a.stage == "arms":
        wait_for_replay()
        with heavy_lock():
            res = run_arms(a.db_path)
        print(json.dumps(res["verdicts"], indent=1))
    else:
        REPORT_MD.write_text(render_report())
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
