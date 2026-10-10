# ruff: noqa: E501
"""Referee-crew experiment under the FROZEN pre-registration ``docs/REFEREES.md``.

    uv run python -m research.eval.referees_eval g0       # hash check + missingness audit + gate G0
    uv run python -m research.eval.referees_eval run      # arms (heavy; holds data/ops/heavy.lock)
    uv run python -m research.eval.referees_eval report   # reports/referees.md from the JSON

Order is enforced in code: ``g0`` verifies the frozen sha256 against the ledger, runs the
missingness audit and G0 (items 1-3), writes ``reports/referees/g0.json`` and logs the sha256 of
that file; ``run`` refuses unless ``g0.json`` exists, passed, and still hashes to the logged value;
the first arm fitted is A0 on pts, whose reproduction of production (G0 item 4 / kill criterion)
is checked before anything else is fitted. Nothing here changes after a result: constants below are
the frozen ones; choices the document left open are listed in ``IMPLEMENTATION_CHOICES`` and
printed in the report.

Research module only: ``nba/`` is untouched (the crew columns enter through the existing
``stat_feature_names(stat, extra=...)`` hook). Read-only on ``nba.duckdb`` (``read_only=True``),
season <= 2024 asserted everywhere; season 2025 is the frozen holdout and is never loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl
import yaml
from scipy import stats as sps
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import EPS, arm_scores
from nba.parse.rebuild_lineups import heavy_lock
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    asof_elo_margin,
    build_features,
    flagged_from_availability,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from nba.props.metrics import paired_score_delta_ci

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "REFEREES.md"
LEDGER = ROOT / "docs" / "TEST_LEDGER.md"
OUT_DIR = Path("reports/referees")
REPORT_MD = Path("reports/referees.md")
MARKER = "=== RESULTS BELOW ==="
#: sha256 of the frozen section, recorded in the ledger notice of 2026-10-09 (prefix 14613c3a).
FROZEN_SHA = "14613c3a48d788e9c8dff0af2539b55667acee0c45a9e1bfb8f539403728975f"
SELECT_SEASON, REPORT_SEASON = 2023, 2024

# --- frozen constants (docs/REFEREES.md)
K_SHRINK = 30.0
CREW_COLS: tuple[str, ...] = ("crew_pf100", "crew_fta100", "crew_pace")
QUANTITIES: tuple[str, ...] = ("pf100_g", "fta100_g", "pace_g", "home_margin")
MIN_OFFICIALS = 2
N_BOOT = 2000
PROP_FLOOR = -0.005
TOTAL_FLOOR = -0.05
PROP_BIAS_MAX, TOTAL_BIAS_MAX = 0.5, 1.0
COV_LO, COV_HI = 0.75, 0.85
SLICE_WORSE_PROPS, SLICE_WORSE_TOTAL = 0.01, 0.10
SLICE_MIN_ROWS, SLICE_MIN_GAMES = 300, 100
A2_MAX_SHARE = 0.5
REPRO_PTS = {2023: 3.1528, 2024: 3.1884}
REPRO_TOL = 1e-4
CONFIRMATORY: tuple[str, ...] = ("pts", "fg3m", "total")
#: G0 thresholds
G0_ALL, G0_SEASON, G0_MONTH, G0_FEW = 0.98, 0.97, 0.95, 0.01
G0_NGAMES = 3953
G0_FIELDS = 0.995
AUDIT_AUC = (0.44, 0.56)
AUDIT_SD_FRAC = 0.1
AUDIT_DNP_PP = 0.02
AUDIT_MIN_MISSING = 20
MDE_Z = 2.8  # 80% power, two-sided 5%

#: Choices the frozen document leaves open; fixed here BEFORE any arm is fitted and printed in the
#: report so the reader can see them (none was tuned or looked at against a result).
IMPLEMENTATION_CHOICES: tuple[str, ...] = (
    "Official ids are used as stored; the four same-name id pairs are NOT merged (merging would be "
    "a rule change).",
    "Games with any NULL game-level quantity (no box score) are excluded from the league and "
    "official running means (none exist in 2022-2024).",
    "Total head: scale-head floor 1.0 point; calibration window = newest 15% of training games cut "
    "on a date boundary (no min_cal_rows rule); a block is fitted only with >= 400 training games; "
    "recency weight 0.5 ** (games_ago / 10) with games_ago = 0 for the most recent prior game; "
    "the as-of league mean for the recency shrinkage is the mean points per team-game over ALL "
    "games strictly before the date (all loaded seasons). Elo margins are the MOV-Elo "
    "(configs/mov_elo_tuned.yaml) expected margin that production props use.",
    "Total head game type = game_id prefix (002 regular, 004 playoff, 005 play-in); day-of-season "
    "= days since the first game of the season in the schedule.",
    "80% interval coverage guard = share of randomized, continuity-corrected PIT values on the "
    "integer grid inside [0.1, 0.9]; bias = mean(predictive mean - outcome).",
    "Pass rule (5) A2 noise control: gain(A2) <= 0.5 * gain(A1), gain = -(arm - A0) mean CRPS. "
    "A2 and A3 are fitted for pts and fg3m only (the confirmatory props); reb and ast get A0/A1.",
    "Quartiles for the crew_pace slices are computed per evaluation season over covered rows.",
    "Win sanity: crew_home_margin is centred by the as-of league mean home margin; missing -> 0; "
    "ridge lambda 5.0 and min_signal_games 150 as configs/injury_elo.yaml.",
    "Power: SE is taken from the game-clustered bootstrap CI half-width; MDE = 2.8 * SE.",
)


# --------------------------------------------------------------------------- hash / provenance


def frozen_sha256(path: Path = DOC) -> str:
    """sha256 of the frozen section: the lines before ``=== RESULTS BELOW ===`` (the python
    equivalent of the awk + shasum recipe in the document header)."""
    keep: list[str] = []
    for line in path.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line if line.endswith("\n") else line + "\n")
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def ledger_recorded_sha(ledger: Path = LEDGER) -> str | None:
    """The REFEREES frozen-section sha256 recorded in the ledger notice (None if absent)."""
    for line in ledger.read_text().splitlines():
        if "REFEREES" in line and "frozen-section sha256" in line:
            m = re.search(
                r"REFEREES\.md\]\(REFEREES\.md\) frozen-section sha256 `([0-9a-f]{64})`", line
            )
            if m:
                return m.group(1)
    return None


def freeze_commit(path: Path = DOC) -> str | None:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %ad", "--date=iso", "--", str(path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def hash_check() -> dict[str, Any]:
    cur, rec = frozen_sha256(), ledger_recorded_sha()
    return {
        "doc_frozen_sha256": cur,
        "ledger_recorded_sha256": rec,
        "constant_in_module": FROZEN_SHA,
        "match": bool(cur == rec == FROZEN_SHA),
        "freeze_commit": freeze_commit(),
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _f(x: Any) -> float:
    return float("nan") if x is None else float(x)


def assert_no_holdout(df: pl.DataFrame) -> None:
    if df.height and int(df["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 is the frozen holdout and must never be loaded")


# --------------------------------------------------------------------------- data


def load_referee_inputs(
    db_path: str = "nba.duckdb",
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, player rows with the box columns the doc lists, officials); read-only; season <= 2024."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, "
            "s.fga, s.fta, s.oreb, s.tov, s.pf, s.starter "
            f"FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        officials = con.execute(
            "SELECT o.game_id, o.official_id, o.name, o.jersey FROM game_officials o "
            f"JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert_no_holdout(games)
    return games, pgs, officials


# --------------------------------------------------------------------------- game-level quantities


def game_quantities(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """Per game, both teams combined (team sums over rows with ``minutes > 0``): ``poss_g``,
    ``pf100_g``, ``fta100_g``, ``pace_g`` (= ``poss_g``), plus ``home_margin`` and ``total``."""
    p = pgs.filter(pl.col("minutes") > 0)
    g = games.select(
        "game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"
    )
    tot = p.group_by("game_id").agg(
        pl.col("fga").sum().alias("fga"),
        pl.col("fta").sum().alias("fta"),
        pl.col("tov").sum().alias("tov"),
        pl.col("oreb").sum().alias("oreb"),
        pl.col("pf").sum().alias("pf"),
        pl.col("pf").null_count().alias("pf_null"),
        pl.col("fta").null_count().alias("fta_null"),
    )
    # possessions per team then summed: 0.5 * [(FGA + 0.44 FTA + TOV - OREB)_h + (...)_a]
    # = 0.5 * (FGA_sum + 0.44 FTA_sum + TOV_sum - OREB_sum) over both teams.
    out = g.join(tot, on="game_id", how="left").with_columns(
        (0.5 * (pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov") - pl.col("oreb"))).alias(
            "poss_g"
        )
    )
    return out.with_columns(
        (100.0 * pl.col("pf") / (2.0 * pl.col("poss_g"))).alias("pf100_g"),
        (100.0 * pl.col("fta") / (2.0 * pl.col("poss_g"))).alias("fta100_g"),
        pl.col("poss_g").alias("pace_g"),
        (pl.col("home_pts") - pl.col("away_pts")).cast(pl.Float64).alias("home_margin"),
        (pl.col("home_pts") + pl.col("away_pts")).cast(pl.Float64).alias("total"),
    ).drop("fga", "fta", "tov", "oreb", "pf", "pf_null", "fta_null")


# --------------------------------------------------------------------------- crew features


def crew_features(
    gq: pl.DataFrame,
    officials: pl.DataFrame,
    k: float = K_SHRINK,
    inclusive: bool = False,
) -> pl.DataFrame:
    """As-of crew summaries (frozen definition).

    For game ``G`` on date ``d`` and each non-NULL official ``o`` of ``G``: over the games ``o``
    worked with ``game_date < d`` (strictly earlier DATE), ``x_o = (n*mean_o + k*mean_league) /
    (n + k)`` with ``mean_league`` the unweighted mean of ALL games strictly before ``d``; an
    official never seen gets ``mean_league``. The crew feature is the unweighted mean of ``x_o``
    over the non-NULL officials, NaN (null) when fewer than 2, or before the first prior game.
    ``inclusive=True`` is the A3 tripwire only: the cut becomes ``game_date <= d`` (the game itself
    and same-day games enter). Columns: game_id, the three frozen crew columns, ``crew_home_margin``
    (same construction on the home margin, win sanity), ``league_home_margin`` (the as-of league
    mean), ``crew_n`` (non-NULL officials).
    """
    g = gq.sort(["game_date", "game_id"])
    gids = g["game_id"].to_list()
    dates = g["game_date"].to_list()
    vals = g.select(QUANTITIES).to_numpy().astype(float)
    crews: dict[str, list[int]] = defaultdict(list)
    for gid, oid in officials.select("game_id", "official_id").iter_rows():
        if oid is not None:
            crews[str(gid)].append(int(oid))
    league_sum = np.zeros(len(QUANTITIES))
    league_n = 0
    off_n: dict[int, int] = defaultdict(int)
    off_sum: dict[int, np.ndarray] = defaultdict(lambda: np.zeros(len(QUANTITIES)))
    out = np.full((len(gids), len(QUANTITIES)), np.nan)
    league_out = np.full(len(gids), np.nan)
    crew_n = np.zeros(len(gids), dtype=np.int64)

    def add(i: int) -> None:
        nonlocal league_n
        if np.isnan(vals[i]).any():
            return
        league_sum[:] += vals[i]
        league_n += 1
        for o in set(crews[str(gids[i])]):
            off_n[o] += 1
            off_sum[o] += vals[i]

    i = 0
    n = len(gids)
    while i < n:
        j = i
        while j < n and dates[j] == dates[i]:
            j += 1
        if inclusive:
            for r in range(i, j):
                add(r)
        lg = league_sum / league_n if league_n else np.full(len(QUANTITIES), np.nan)
        for r in range(i, j):
            ids = sorted(set(crews[str(gids[r])]))
            crew_n[r] = len(ids)
            league_out[r] = lg[3]
            if len(ids) < MIN_OFFICIALS or np.isnan(lg).any():
                continue
            xs = []
            for o in ids:
                cnt = off_n.get(o, 0)
                if cnt == 0 or np.isinf(k):
                    xs.append(lg)
                else:
                    xs.append((off_sum[o] + k * lg) / (cnt + k))
            out[r] = np.mean(xs, axis=0)
        if not inclusive:
            for r in range(i, j):
                add(r)
        i = j
    cols = [*CREW_COLS, "crew_home_margin"]
    data: dict[str, Any] = {"game_id": pl.Series("game_id", gids, dtype=pl.Utf8)}
    for ci, c in enumerate(cols):
        data[c] = pl.Series(c, out[:, ci], nan_to_null=True)
    data["league_home_margin"] = pl.Series("league_home_margin", league_out, nan_to_null=True)
    data["crew_n"] = pl.Series("crew_n", crew_n)
    return pl.DataFrame(data)


def permute_crew(crew: pl.DataFrame, games: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """A2 / T2 noise control: the three crew columns (and the margin column) are permuted JOINTLY
    across games within season (whole rows move together; column marginals per season are kept)."""
    cols = [*CREW_COLS, "crew_home_margin"]
    j = crew.join(games.select("game_id", "season"), on="game_id", how="left").sort("game_id")
    rng = np.random.default_rng(seed)
    pieces = []
    for season in sorted(j["season"].unique().to_list()):
        sub = j.filter(pl.col("season") == season)
        perm = rng.permutation(sub.height)
        moved = sub.select(cols)[perm.tolist()]
        pieces.append(sub.drop(cols).hstack(moved))
    return pl.concat(pieces).drop("season")


def attach_crew(
    feats: pl.DataFrame, crew: pl.DataFrame, cols: tuple[str, ...] = CREW_COLS
) -> pl.DataFrame:
    """Left-join crew columns on game_id; the row set and order of ``feats`` never change."""
    out = feats.join(crew.select("game_id", *cols), on="game_id", how="left")
    if out.height != feats.height:
        raise ValueError("crew join changed the row count")
    return out


# --------------------------------------------------------------------------- missingness audit + G0


def games_with_crews(
    gq: pl.DataFrame, officials: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(per-game frame with distinct non-NULL official count, per-id/name frame)."""
    n_off = (
        officials.filter(pl.col("official_id").is_not_null())
        .group_by("game_id")
        .agg(pl.col("official_id").n_unique().alias("n_off"))
    )
    gg = gq.join(n_off, on="game_id", how="left").with_columns(pl.col("n_off").fill_null(0))
    return gg, officials


def g0_checks(gq: pl.DataFrame, pgs: pl.DataFrame, officials: pl.DataFrame) -> dict[str, Any]:
    """Gate G0 items 1-3 (item 4, the A0 reproduction, needs a fitted arm and is a kill criterion
    evaluated at the first arm fit)."""
    gg, off = games_with_crews(gq, officials)
    gg = gg.with_columns(
        (pl.col("n_off") >= 3).alias("ge3"),
        pl.col("n_off").is_between(1, 2).alias("few"),
        pl.col("game_date").dt.strftime("%Y-%m").alias("ym"),
    )
    n_games = gg.height
    share_ge3 = _f(gg["ge3"].mean())
    by_season = {
        int(s): {"n_games": int(n), "share_ge3": float(sh)}
        for s, n, sh in gg.group_by("season")
        .agg(pl.len(), pl.col("ge3").mean())
        .sort("season")
        .iter_rows()
    }
    by_month = {
        str(ym): {"n_games": int(n), "share_ge3": float(sh)}
        for ym, n, sh in gg.group_by("ym")
        .agg(pl.len(), pl.col("ge3").mean())
        .sort("ym")
        .iter_rows()
    }
    share_few = _f(gg["few"].mean())
    c1: dict[str, Any] = {
        "n_games": n_games,
        "n_games_expected": G0_NGAMES,
        "n_games_matches_doc": n_games == G0_NGAMES,
        "share_ge3_officials": share_ge3,
        "threshold_overall": G0_ALL,
        "by_season": by_season,
        "threshold_season": G0_SEASON,
        "min_month_share": float(min(v["share_ge3"] for v in by_month.values())),
        "worst_month": min(by_month, key=lambda m: by_month[m]["share_ge3"]),
        "by_month": by_month,
        "threshold_month": G0_MONTH,
        "share_1_or_2_officials": share_few,
        "threshold_few_max": G0_FEW,
    }
    c1["pass"] = bool(
        share_ge3 >= G0_ALL
        and all(v["share_ge3"] >= G0_SEASON for v in by_season.values())
        and c1["min_month_share"] >= G0_MONTH
        and share_few <= G0_FEW
    )
    # item 2: name <-> official_id 1:1
    ids = off.filter(pl.col("official_id").is_not_null())
    id_names = ids.group_by("official_id").agg(pl.col("name").n_unique().alias("n_names"))
    ids_two_names = id_names.filter(pl.col("n_names") > 1)["official_id"].to_list()
    name_ids = ids.group_by("name").agg(
        pl.col("official_id").unique().sort().alias("ids"),
        pl.col("official_id").n_unique().alias("n_ids"),
    )
    dup = name_ids.filter(pl.col("n_ids") > 1).sort("name")
    dup_rows: list[dict[str, Any]] = []
    unresolved = 0
    for name, idl in dup.select("name", "ids").iter_rows():
        per_id: dict[int, dict[str, Any]] = {}
        for oid in idl:
            sub = ids.filter((pl.col("name") == name) & (pl.col("official_id") == oid))
            per_id[int(oid)] = {
                "jerseys": sorted(
                    {str(j).strip() for j in sub["jersey"].to_list() if j is not None}
                ),
                "n_slots": int(sub.height),
            }
        jersey_sets = [set(v["jerseys"]) for v in per_id.values()]
        disjoint = all(
            not (jersey_sets[a] & jersey_sets[b])
            for a in range(len(jersey_sets))
            for b in range(a + 1, len(jersey_sets))
        ) and all(jersey_sets)
        unresolved += 0 if disjoint else 1
        dup_rows.append({"name": name, "ids": per_id, "resolved_by_jersey": bool(disjoint)})
    jersey_changes = int(
        ids.group_by("official_id")
        .agg(pl.col("jersey").n_unique().alias("nj"))
        .filter(pl.col("nj") > 1)
        .height
    )
    c2: dict[str, Any] = {
        "ids_with_two_names": [int(i) for i in ids_two_names],
        "names_with_multiple_ids": dup_rows,
        "n_names_with_multiple_ids": len(dup_rows),
        "n_names_unresolved_by_jersey": unresolved,
        "n_slots_on_non_primary_duplicate_ids": int(
            sum(
                sum(v["n_slots"] for v in r["ids"].values())
                - max(v["n_slots"] for v in r["ids"].values())
                for r in dup_rows
            )
        ),
        "ids_with_two_jerseys_informational": jersey_changes,
        "n_distinct_ids": int(ids["official_id"].n_unique()),
        "n_distinct_names": int(ids["name"].n_unique()),
    }
    c2["pass"] = bool(not ids_two_names and unresolved == 0)
    # item 3: pf and fta non-NULL for rows with minutes
    keyed = pgs.join(gq.select("game_id"), on="game_id", how="inner")
    has_min = keyed.filter(pl.col("minutes").is_not_null() & (pl.col("minutes") > 0))
    non_null_min = keyed.filter(pl.col("minutes").is_not_null())
    both = has_min.filter(pl.col("pf").is_not_null() & pl.col("fta").is_not_null())
    both_nn = non_null_min.filter(pl.col("pf").is_not_null() & pl.col("fta").is_not_null())
    c3: dict[str, Any] = {
        "rows_minutes_gt0": has_min.height,
        "share_pf_fta_non_null_minutes_gt0": float(both.height / max(has_min.height, 1)),
        "rows_minutes_non_null": non_null_min.height,
        "share_pf_fta_non_null_minutes_non_null": float(
            both_nn.height / max(non_null_min.height, 1)
        ),
        "threshold": G0_FIELDS,
    }
    c3["pass"] = bool(
        c3["share_pf_fta_non_null_minutes_gt0"] >= G0_FIELDS
        and c3["share_pf_fta_non_null_minutes_non_null"] >= G0_FIELDS
    )
    return {"1_officials_coverage": c1, "2_name_id_1to1": c2, "3_pf_fta_fields": c3}


def _boot_diff(
    values: np.ndarray, flag: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT, seed: int = 0
) -> dict[str, float]:
    """Mean(values | flag) - mean(values | ~flag) with a cluster bootstrap CI."""
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(cluster, return_inverse=True)
    g = len(uniq)
    s1 = np.zeros(g)
    n1 = np.zeros(g)
    s0 = np.zeros(g)
    n0 = np.zeros(g)
    np.add.at(s1, inv, np.where(flag, values, 0.0))
    np.add.at(n1, inv, flag.astype(float))
    np.add.at(s0, inv, np.where(flag, 0.0, values))
    np.add.at(n0, inv, (~flag).astype(float))
    point = float(s1.sum() / n1.sum() - s0.sum() / n0.sum())
    idx = rng.integers(0, g, size=(n_boot, g))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = s1[idx].sum(1) / n1[idx].sum(1) - s0[idx].sum(1) / n0[idx].sum(1)
    d = d[np.isfinite(d)]
    return {"point": point, "lo": float(np.quantile(d, 0.025)), "hi": float(np.quantile(d, 0.975))}


def missingness_audit(
    gq: pl.DataFrame,
    pgs: pl.DataFrame,
    officials: pl.DataFrame,
    strict_crew: pl.DataFrame,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Missingness audit run BEFORE any model (frozen STOP rules). A game has a usable crew iff it
    has >= 2 non-NULL ``official_id``. With zero missing games every difference test is vacuous."""
    gg, _ = games_with_crews(gq, officials)
    gg = gg.with_columns(
        (pl.col("n_off") < MIN_OFFICIALS).alias("missing"),
        pl.col("game_date").dt.strftime("%Y-%m").alias("ym"),
        pl.col("home_margin").abs().alias("abs_margin"),
    )
    n_missing = int(gg["missing"].sum())
    tot = gg["total"].to_numpy()
    q25, q75 = np.nanquantile(tot, [0.25, 0.75])
    out: dict[str, Any] = {
        "n_games": gg.height,
        "n_missing_games": n_missing,
        "share_missing_by_season": {
            int(s): float(v)
            for s, v in gg.group_by("season")
            .agg(pl.col("missing").mean())
            .sort("season")
            .iter_rows()
        },
        "share_missing_by_month": {
            str(s): float(v)
            for s, v in gg.group_by("ym").agg(pl.col("missing").mean()).sort("ym").iter_rows()
        },
        "max_share_missing_by_home_team": _f(
            gg.group_by("home_team").agg(pl.col("missing").mean())["missing"].max()
        ),
        "share_missing_total_top_quartile": _f(gg.filter(pl.col("total") >= q75)["missing"].mean()),
        "share_missing_total_bottom_quartile": _f(
            gg.filter(pl.col("total") <= q25)["missing"].mean()
        ),
    }
    rows = pgs.join(gg.select("game_id", "missing"), on="game_id", how="inner").with_columns(
        (pl.col("minutes").is_not_null() & (pl.col("minutes") > 0)).alias("played"),
        pl.col("starter").fill_null(False).alias("starter_f"),
    )
    out["player_rows"] = {
        "n": rows.height,
        "share_missing_played": _f(rows.filter(pl.col("played"))["missing"].mean()),
        "share_missing_unplayed": _f(rows.filter(~pl.col("played"))["missing"].mean()),
        "share_missing_starter": _f(rows.filter(pl.col("starter_f"))["missing"].mean()),
        "share_missing_non_starter": _f(rows.filter(~pl.col("starter_f"))["missing"].mean()),
    }
    stops: list[str] = []
    tests: dict[str, Any] = {}
    if n_missing >= AUDIT_MIN_MISSING and n_missing <= gg.height - AUDIT_MIN_MISSING:
        flag = gg["missing"].to_numpy()
        gid = gg["game_id"].to_numpy()
        for name, col in (("total", "total"), ("abs_margin", "abs_margin"), ("pf100_g", "pf100_g")):
            v = gg[col].to_numpy().astype(float)
            ok = np.isfinite(v)
            d = _boot_diff(v[ok], flag[ok], gid[ok], n_boot)
            sd = float(np.nanstd(v))
            d["sd"] = sd
            d["flag"] = bool(abs(d["point"]) > AUDIT_SD_FRAC * sd and (d["lo"] > 0 or d["hi"] < 0))
            tests[name] = d
            if d["flag"]:
                stops.append(f"{name} differs by > 0.1 SD with CI excluding 0")
        played = rows["played"].to_numpy()
        dnp = _boot_diff(
            (~played).astype(float), rows["missing"].to_numpy(), rows["game_id"].to_numpy(), n_boot
        )
        dnp["flag"] = bool(abs(dnp["point"]) > AUDIT_DNP_PP)
        tests["dnp_rate"] = dnp
        if dnp["flag"]:
            stops.append("DNP rate differs by > 2 percentage points")
        # AUC of missingness from {total, margin, pace, month, home team}, blocked by season
        x = np.column_stack(
            [
                gg["total"].to_numpy(),
                gg["home_margin"].to_numpy(),
                gg["pace_g"].to_numpy(),
                gg["game_date"].dt.month().to_numpy(),
            ]
        ).astype(float)
        home = gg.select(pl.col("home_team").cast(pl.Utf8)).to_dummies().to_numpy().astype(float)
        xm = np.column_stack([np.nan_to_num(x), home])
        seasons = gg["season"].to_numpy()
        pred = np.full(len(flag), np.nan)
        for s in np.unique(seasons):
            te = seasons == s
            tr = ~te
            if flag[tr].sum() < 2 or (~flag[tr]).sum() < 2:
                continue
            mu, sd_ = xm[tr].mean(0), xm[tr].std(0) + 1e-9
            clf = LogisticRegression(max_iter=1000).fit((xm[tr] - mu) / sd_, flag[tr])
            pred[te] = clf.predict_proba((xm[te] - mu) / sd_)[:, 1]
        ok = np.isfinite(pred)
        auc = (
            float(roc_auc_score(flag[ok], pred[ok]))
            if ok.any() and len(np.unique(flag[ok])) == 2
            else None
        )
        tests["auc"] = {"value": auc, "band": list(AUDIT_AUC)}
        if auc is not None and not (AUDIT_AUC[0] <= auc <= AUDIT_AUC[1]):
            stops.append(f"missingness AUC {auc:.3f} outside {AUDIT_AUC}")
        out["tests"] = tests
        out["tests_status"] = "estimated"
    else:
        out["tests"] = {}
        out["tests_status"] = (
            f"not estimable: {n_missing} games without a usable crew (need >= {AUDIT_MIN_MISSING} "
            f"and as many non-missing); the audit is vacuous, no STOP fires from it"
        )
    # NULL pattern of the crew COLUMNS (warm-up NaN included) against minutes IS NULL
    cn = strict_crew.select("game_id", pl.col("crew_pf100").is_null().alias("crew_null"))
    pr = pgs.join(cn, on="game_id", how="inner").with_columns(
        pl.col("minutes").is_null().alias("min_null")
    )
    n11 = int(pr.filter(pl.col("crew_null") & pl.col("min_null")).height)
    n10 = int(pr.filter(pl.col("crew_null") & ~pl.col("min_null")).height)
    n01 = int(pr.filter(~pl.col("crew_null") & pl.col("min_null")).height)
    n00 = int(pr.filter(~pl.col("crew_null") & ~pl.col("min_null")).height)
    rate_null = n11 / max(n11 + n10, 1)
    rate_ok = n01 / max(n01 + n00, 1)
    fisher_p = float(sps.fisher_exact([[n11, n10], [n01, n00]])[1]) if (n11 + n10) > 0 else None
    out["crew_null_vs_minutes_null"] = {
        "rows_crew_null": n11 + n10,
        "minutes_null_rate_when_crew_null": rate_null,
        "minutes_null_rate_when_crew_present": rate_ok,
        "diff": rate_null - rate_ok,
        "fisher_p": fisher_p,
        "note": "crew columns are NULL only before the first prior league game (opening day 2022, "
        "warm-up) or when < 2 officials; STOP rule applied as |diff| > 2 percentage points",
    }
    if n11 + n10 > 0 and abs(rate_null - rate_ok) > AUDIT_DNP_PP:
        stops.append("crew-column NULL pattern correlated with minutes IS NULL (> 2 pp)")
    out["stops"] = stops
    out["stop"] = bool(stops)
    return out


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n"


def run_g0(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """Hash check, missingness audit (its output hash logged first), gate G0 items 1-3; writes
    ``g0.json`` + ``g0.sha256``. No model of any kind is fitted here."""
    out_dir.mkdir(parents=True, exist_ok=True)
    hc = hash_check()
    if not hc["match"]:
        raise SystemExit(f"STOP: frozen REFEREES hash mismatch: {hc}")
    games, pgs, officials = load_referee_inputs(db_path)
    gq = game_quantities(games, pgs)
    strict = crew_features(gq, officials)
    audit = missingness_audit(gq, pgs, officials, strict, n_boot)
    audit_path = out_dir / "missingness.json"
    audit_path.write_text(canonical_json(audit))
    audit_sha = file_sha256(audit_path)
    (out_dir / "missingness.sha256").write_text(f"{audit_sha}  missingness.json\n")
    print(f"missingness.json sha256 {audit_sha}", flush=True)
    checks = g0_checks(gq, pgs, officials)
    g0_pass = bool(all(c["pass"] for c in checks.values()) and not audit["stop"])
    g0 = {
        "hash_check": hc,
        "checks": checks,
        "item_4_a0_reproduction": {
            "status": "evaluated at the first arm fit (kill criterion); not part of this file's hash",
            "expected_pts_integer_crps": {str(k): v for k, v in REPRO_PTS.items()},
            "tolerance": REPRO_TOL,
        },
        "missingness_audit_sha256": audit_sha,
        "missingness_stop": audit["stop"],
        "g0_pass": g0_pass,
        "failed_items": [k for k, c in checks.items() if not c["pass"]]
        + (["missingness_audit"] if audit["stop"] else []),
    }
    path = out_dir / "g0.json"
    path.write_text(canonical_json(g0))
    sha = file_sha256(path)
    (out_dir / "g0.sha256").write_text(f"{sha}  g0.json\n")
    print(f"g0.json sha256 {sha}  g0_pass={g0_pass}  failed={g0['failed_items']}", flush=True)
    return g0


def require_g0(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Refuse to fit any arm unless g0.json exists, still matches its logged sha256, and passed."""
    path, shaf = out_dir / "g0.json", out_dir / "g0.sha256"
    if not path.exists() or not shaf.exists():
        raise SystemExit("STOP: g0.json / g0.sha256 missing; run the g0 stage first")
    logged = shaf.read_text().split()[0]
    if file_sha256(path) != logged:
        raise SystemExit("STOP: g0.json no longer matches its logged sha256")
    g0: dict[str, Any] = json.loads(path.read_text())
    if not g0["g0_pass"]:
        raise SystemExit(f"STOP: gate G0 failed ({g0['failed_items']}); the experiment is not run")
    if not hash_check()["match"]:
        raise SystemExit("STOP: frozen REFEREES hash mismatch")
    return g0


# --------------------------------------------------------------------------- scoring helpers


@dataclass
class ArmScores:
    """Per-row scores of one arm on identical rows."""

    gid: np.ndarray
    pid: np.ndarray
    season: np.ndarray
    y: np.ndarray
    crps_int: np.ndarray
    crps_cont: np.ndarray
    tll_low: np.ndarray
    pit_int: np.ndarray
    pred_mean: np.ndarray
    cov: dict[str, np.ndarray]

    def save(self, path: Path) -> None:
        d = {
            k: getattr(self, k)
            for k in (
                "gid",
                "pid",
                "season",
                "y",
                "crps_int",
                "crps_cont",
                "tll_low",
                "pit_int",
                "pred_mean",
            )
        }
        d.update({f"cov_{k}": v for k, v in self.cov.items()})
        np.savez_compressed(path, **d)

    @classmethod
    def load(cls, path: Path) -> ArmScores:
        z = np.load(path, allow_pickle=True)
        base = (
            "gid",
            "pid",
            "season",
            "y",
            "crps_int",
            "crps_cont",
            "tll_low",
            "pit_int",
            "pred_mean",
        )
        return cls(
            **{k: z[k] for k in base},
            cov={k[4:]: z[k] for k in z.files if k.startswith("cov_")},
        )


PROP_COV = ("n_prior", "starter10", "team_game_no", "cov_covered", "cov_pace")


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    progress: bool = False,
) -> ArmScores:
    """Month-block walk-forward of one (arm, stat), production protocol: 2022 warm-up, block models
    fitted on games strictly before the block, newest-15% calibration. The scored quantile grid is
    the production construction (``raw_quantiles``) on the 199-grid, mapped to the integers with
    ``ceil(q - 0.5)`` for the primary score (``arm_scores``)."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    keys: dict[str, list[np.ndarray]] = {k: [] for k in ("gid", "pid", "season", "y", "mean")}
    covs: dict[str, list[np.ndarray]] = {k: [] for k in PROP_COV}
    qs: list[np.ndarray] = []
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        assert model.z_sorted is not None
        qs.append(raw_quantiles(c, s, model.z_sorted, CRPS_TAUS))
        keys["gid"].append(block["game_id"].to_numpy())
        keys["pid"].append(block["player_id"].to_numpy())
        keys["season"].append(block["season"].to_numpy())
        keys["y"].append(block["y"].to_numpy())
        keys["mean"].append(np.clip(c + s * float(model.z_sorted.mean()), 0.0, None))
        for k in PROP_COV:
            covs[k].append(block[k].cast(pl.Float64).to_numpy())
        if progress:
            print(f"  {stat} block {b} {start} rows={block.height}", flush=True)
    q = np.vstack(qs)
    y = np.concatenate(keys["y"])
    sc = arm_scores(q, y, stat)
    return ArmScores(
        gid=np.concatenate(keys["gid"]),
        pid=np.concatenate(keys["pid"]),
        season=np.concatenate(keys["season"]),
        y=y,
        crps_int=sc["crps_int"],
        crps_cont=sc["crps_cont"],
        tll_low=sc["tll_low"],
        pit_int=sc["pit_int"],
        pred_mean=np.concatenate(keys["mean"]),
        cov={k: np.concatenate(v) for k, v in covs.items()},
    )


def ci_dict(delta: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=cluster)
    se = (c.hi - c.lo) / (2 * 1.96)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi), "se": float(se)}


def _slice_masks_props(
    cov: dict[str, np.ndarray], season_mask: np.ndarray
) -> dict[str, np.ndarray]:
    pace = cov["cov_pace"]
    ok = season_mask & np.isfinite(pace)
    q25, q75 = np.quantile(pace[ok], [0.25, 0.75]) if ok.any() else (np.nan, np.nan)
    return {
        "cold_start(n_prior<20)": cov["n_prior"] < 20,
        "starter(starter10>=.5)": cov["starter10"] >= 0.5,
        "bench": cov["starter10"] < 0.5,
        "first15_team_games": cov["team_game_no"] < 15,
        "crew_covered": cov["cov_covered"] > 0.5,
        "crew_not_covered": cov["cov_covered"] <= 0.5,
        "crew_pace_top_quartile": ok & (pace >= q75),
        "crew_pace_bottom_quartile": ok & (pace <= q25),
    }


def contrast_props(
    base: ArmScores,
    arm: ArmScores,
    season: int,
    n_boot: int = N_BOOT,
    with_slices: bool = True,
) -> dict[str, Any]:
    """Paired contrast ``arm - base`` on one season (identical rows asserted)."""
    if not (np.array_equal(base.gid, arm.gid) and np.array_equal(base.pid, arm.pid)):
        raise ValueError("arms are not row-aligned")
    m = base.season == season
    gid = base.gid[m]
    d = arm.crps_int[m] - base.crps_int[m]
    res: dict[str, Any] = {
        "season": season,
        "n_rows": int(m.sum()),
        "n_games": int(len(np.unique(gid))),
        "base_crps_int": float(base.crps_int[m].mean()),
        "arm_crps_int": float(arm.crps_int[m].mean()),
        "d_crps_int": ci_dict(d, gid, n_boot),
        "p": boot_p_value(d, gid, n_boot),
        "d_crps_cont": ci_dict(arm.crps_cont[m] - base.crps_cont[m], gid, n_boot),
        "d_tll_low": ci_dict(arm.tll_low[m] - base.tll_low[m], gid, n_boot),
        "bias_arm": float((arm.pred_mean[m] - arm.y[m]).mean()),
        "bias_base": float((base.pred_mean[m] - base.y[m]).mean()),
        "cov80_arm": float(((arm.pit_int[m] > 0.1) & (arm.pit_int[m] <= 0.9)).mean()),
        "cov80_base": float(((base.pit_int[m] > 0.1) & (base.pit_int[m] <= 0.9)).mean()),
    }
    cm = m & (base.cov["cov_covered"] > 0.5)
    if cm.any():
        dc = arm.crps_int[cm] - base.crps_int[cm]
        res["covered_only"] = {"n_rows": int(cm.sum()), **ci_dict(dc, base.gid[cm], n_boot)}
    if with_slices:
        sl: dict[str, Any] = {}
        for name, mask in _slice_masks_props(base.cov, m).items():
            mm = m & mask
            if int(mm.sum()) >= SLICE_MIN_ROWS:
                sl[name] = {
                    "n_rows": int(mm.sum()),
                    "d_crps_int": float((arm.crps_int[mm] - base.crps_int[mm]).mean()),
                }
        res["slices"] = sl
    return res


# --------------------------------------------------------------------------- total head


def recency_features(
    games: pl.DataFrame, halflife: float = 10.0, pseudo: float = 10.0
) -> pl.DataFrame:
    """One row per game: as-of recency baseline ``m`` of the combined score, rest/b2b of both teams,
    prior game counts and team game numbers. Exponentially weighted (``0.5 ** (games_ago /
    halflife)``, newest prior game ``games_ago = 0``), season-scoped, shrunk to the as-of league
    mean with ``pseudo`` games. Everything uses games strictly before the game's DATE."""
    g = games.sort(["game_date", "game_id"])
    rows = g.select(
        "game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"
    ).rows()
    hist_for: dict[tuple[int, int], list[float]] = defaultdict(list)
    hist_ag: dict[tuple[int, int], list[float]] = defaultdict(list)
    last_date: dict[int, Any] = {}
    league_sum, league_n = 0.0, 0
    out: list[tuple[Any, ...]] = []

    def shrunk(vals: list[float], lg: float) -> float:
        if not vals:
            return lg
        arr = np.asarray(vals[::-1])  # newest first -> games_ago = 0, 1, ...
        w = 0.5 ** (np.arange(len(arr)) / halflife)
        return float((np.sum(w * arr) + pseudo * lg) / (np.sum(w) + pseudo))

    i, n = 0, len(rows)
    while i < n:
        j = i
        while j < n and rows[j][1] == rows[i][1]:
            j += 1
        lg = league_sum / league_n if league_n else float("nan")
        for gid, d, season, h, a, _hp, _ap in rows[i:j]:
            kh, ka = (h, season), (a, season)
            f_h, a_h = shrunk(hist_for[kh], lg), shrunk(hist_ag[kh], lg)
            f_a, a_a = shrunk(hist_for[ka], lg), shrunk(hist_ag[ka], lg)
            m = (f_h + a_a) / 2.0 + (f_a + a_h) / 2.0
            n_h = len(hist_for[kh]) + len(hist_for[(h, season - 1)])
            n_a = len(hist_for[ka]) + len(hist_for[(a, season - 1)])
            rest_h = 7.0 if h not in last_date else float(np.clip((d - last_date[h]).days, 0, 7))
            rest_a = 7.0 if a not in last_date else float(np.clip((d - last_date[a]).days, 0, 7))
            out.append(
                (
                    gid,
                    m,
                    rest_h,
                    rest_a,
                    float(rest_h == 1),
                    float(rest_a == 1),
                    n_h,
                    n_a,
                    len(hist_for[kh]),
                    len(hist_for[ka]),
                )
            )
        for _gid, d, season, h, a, hp, ap in rows[i:j]:
            if hp is None or ap is None:
                continue
            hist_for[(h, season)].append(float(hp))
            hist_ag[(h, season)].append(float(ap))
            hist_for[(a, season)].append(float(ap))
            hist_ag[(a, season)].append(float(hp))
            last_date[h] = d
            last_date[a] = d
            league_sum += float(hp) + float(ap)
            league_n += 2
        i = j
    return pl.DataFrame(
        out,
        schema=[
            "game_id",
            "m",
            "rest_h",
            "rest_a",
            "b2b_h",
            "b2b_a",
            "n_prior_h",
            "n_prior_a",
            "gno_h",
            "gno_a",
        ],
        orient="row",
    )


TOTAL_BASE_FEATURES: tuple[str, ...] = (
    "m", "rest_h", "rest_a", "b2b_h", "b2b_a", "abs_margin", "exp_margin",
    "n_out_h", "n_out_a", "day_of_season", "game_type",
)  # fmt: skip
TOTAL_MIN_PRIOR = 10
TOTAL_MIN_TRAIN = 400
TOTAL_SCALE_FLOOR = 1.0


def build_total_frame(
    games: pl.DataFrame,
    props_feats: pl.DataFrame,
    elo_params: dict[str, float],
) -> pl.DataFrame:
    """One row per game with the T0 features, the target ``T`` and the eligibility flag (both teams
    have >= 10 prior games in the season or the prior season)."""
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    rec = recency_features(games)
    elo = asof_elo_margin(games, elo_params)
    out_by_team = props_feats.group_by(["game_id", "team_id"]).agg(pl.col("n_out_rot").first())
    sides = games.select("game_id", "home_team", "away_team")
    hh = sides.join(
        out_by_team, left_on=["game_id", "home_team"], right_on=["game_id", "team_id"], how="left"
    ).select("game_id", pl.col("n_out_rot").alias("n_out_h"))
    aa = sides.join(
        out_by_team, left_on=["game_id", "away_team"], right_on=["game_id", "team_id"], how="left"
    ).select("game_id", pl.col("n_out_rot").alias("n_out_a"))
    first = games.group_by("season").agg(pl.col("game_date").min().alias("_s0"))
    fr = (
        games.join(rec, on="game_id", how="left")
        .join(elo, on="game_id", how="left")
        .join(hh, on="game_id", how="left")
        .join(aa, on="game_id", how="left")
        .join(first, on="season", how="left")
        .with_columns(
            pl.col("exp_margin_home").alias("exp_margin"),
            pl.col("exp_margin_home").abs().alias("abs_margin"),
            (pl.col("game_date") - pl.col("_s0"))
            .dt.total_days()
            .cast(pl.Float64)
            .alias("day_of_season"),
            pl.col("game_id")
            .str.slice(0, 3)
            .replace_strict({"002": 0, "004": 1, "005": 2}, default=3)
            .cast(pl.Float64)
            .alias("game_type"),
            (pl.col("home_pts") + pl.col("away_pts")).cast(pl.Float64).alias("T"),
            (
                (pl.col("n_prior_h") >= TOTAL_MIN_PRIOR)
                & (pl.col("n_prior_a") >= TOTAL_MIN_PRIOR)
                & pl.col("m").is_not_nan()
                & pl.col("home_pts").is_not_null()
            ).alias("eligible"),
        )
        .drop("_s0", "exp_margin_home")
    )
    for c in ("n_out_h", "n_out_a"):
        fr = fr.with_columns(pl.col(c).cast(pl.Float64))
    return fr.sort(["game_date", "game_id"])


@dataclass
class TotalHead:
    """Mean-residual + |residual| scale heads and empirical standardized residuals on the newest
    15% calibration window; same recipe as ``ContextResidualModel`` with ``resid = T - m``."""

    cfg: ContextResidualConfig
    names: list[str]
    mean_head: lgb.LGBMRegressor | None = None
    scale_head: lgb.LGBMRegressor | None = None
    z_sorted: np.ndarray | None = None

    def fit(self, train: pl.DataFrame) -> TotalHead:
        dates = train["game_date"].to_numpy()
        cut = dates[int(len(train) * (1.0 - self.cfg.cal_frac))]
        fit_df, cal_df = (
            train.filter(pl.col("game_date") < cut),
            train.filter(pl.col("game_date") >= cut),
        )
        resid = fit_df["T"].to_numpy() - fit_df["m"].to_numpy()
        x = _matrix(fit_df, self.names)
        self.mean_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x, resid)  # type: ignore[arg-type]
        self.scale_head = lgb.LGBMRegressor(**_params(self.cfg)).fit(x, np.abs(resid))  # type: ignore[arg-type]
        xc = _matrix(cal_df, self.names)
        mu = np.asarray(self.mean_head.predict(xc))
        s = self._scale(xc)
        self.z_sorted = np.sort((cal_df["T"].to_numpy() - cal_df["m"].to_numpy() - mu) / s)
        return self

    def _scale(self, x: np.ndarray) -> np.ndarray:
        assert self.scale_head is not None
        return np.maximum(self.scale_head.predict(x), TOTAL_SCALE_FLOOR)

    def predict(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(predictive mean, integer-support 199-grid quantiles)."""
        assert self.mean_head is not None and self.z_sorted is not None
        x = _matrix(df, self.names)
        c = df["m"].to_numpy() + np.asarray(self.mean_head.predict(x))
        s = self._scale(x)
        q = to_integer_support(raw_quantiles(c, s, self.z_sorted, CRPS_TAUS))
        return c + s * float(self.z_sorted.mean()), q


def collect_total(
    frame: pl.DataFrame,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, np.ndarray]:
    """Month-block walk-forward of the total head on eligible games; per-game scores."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    el = frame.filter(pl.col("eligible"))
    test_all = el.filter(pl.col("season").is_in(list(test_seasons)))
    acc: dict[str, list[np.ndarray]] = defaultdict(list)
    for start, end in month_blocks(test_all):
        block = el.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = el.filter(pl.col("game_date") < start).sort(["game_date", "game_id"])
        if block.is_empty() or train.height < TOTAL_MIN_TRAIN:
            continue
        head = TotalHead(cfg, names).fit(train)
        mean, q = head.predict(block)
        y = block["T"].to_numpy()
        m_round = np.round(block["m"].to_numpy())
        p_ge = np.clip((q >= m_round[:, None]).mean(axis=1), EPS, 1 - EPS)
        ev = y >= m_round
        acc["crps_int"].append(_crps(q, y))
        acc["pit_int"].append(_pit(q, y))
        acc["logscore"].append(-np.where(ev, np.log(p_ge), np.log(1 - p_ge)))
        acc["abs_err"].append(np.abs(mean - y))
        acc["pred_mean"].append(mean)
        acc["y"].append(y)
        acc["gid"].append(block["game_id"].to_numpy())
        acc["season"].append(block["season"].to_numpy())
        for k in ("gno_h", "gno_a", "n_prior_h", "n_prior_a"):
            acc[k].append(block[k].to_numpy().astype(float))
        for k in ("cov_covered", "cov_pace"):
            acc[k].append(block[k].cast(pl.Float64).to_numpy())
    return {k: np.concatenate(v) for k, v in acc.items()}


def _crps(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    d = y[:, None] - q
    t = CRPS_TAUS[None, :]
    return np.asarray(2.0 * np.where(d >= 0, t * d, (t - 1.0) * d).mean(axis=1))


def _pit(q: np.ndarray, y: np.ndarray, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f_lo = (q < (y - 0.5)[:, None]).mean(axis=1)
    f_hi = (q <= (y + 0.5)[:, None]).mean(axis=1)
    return np.asarray(f_lo + rng.random(len(y)) * (f_hi - f_lo))


def contrast_total(
    base: dict[str, np.ndarray], arm: dict[str, np.ndarray], season: int, n_boot: int = N_BOOT
) -> dict[str, Any]:
    if not np.array_equal(base["gid"], arm["gid"]):
        raise ValueError("total arms are not row-aligned")
    m = base["season"] == season
    gid = base["gid"][m]
    d = arm["crps_int"][m] - base["crps_int"][m]
    res: dict[str, Any] = {
        "season": season,
        "n_rows": int(m.sum()),
        "n_games": int(m.sum()),
        "base_crps_int": float(base["crps_int"][m].mean()),
        "arm_crps_int": float(arm["crps_int"][m].mean()),
        "d_crps_int": ci_dict(d, gid, n_boot),
        "p": boot_p_value(d, gid, n_boot),
        "sd_paired_diff": float(d.std(ddof=1)),
        "d_logscore": ci_dict(arm["logscore"][m] - base["logscore"][m], gid, n_boot),
        "d_mae": ci_dict(arm["abs_err"][m] - base["abs_err"][m], gid, n_boot),
        "bias_arm": float((arm["pred_mean"][m] - arm["y"][m]).mean()),
        "bias_base": float((base["pred_mean"][m] - base["y"][m]).mean()),
        "cov80_arm": float(((arm["pit_int"][m] > 0.1) & (arm["pit_int"][m] <= 0.9)).mean()),
        "cov80_base": float(((base["pit_int"][m] > 0.1) & (base["pit_int"][m] <= 0.9)).mean()),
    }
    cov = base["cov_covered"] > 0.5
    cm = m & cov
    if cm.any():
        res["covered_only"] = {
            "n_rows": int(cm.sum()),
            **ci_dict(arm["crps_int"][cm] - base["crps_int"][cm], base["gid"][cm], n_boot),
        }
    pace = base["cov_pace"]
    ok = m & np.isfinite(pace)
    q25, q75 = np.quantile(pace[ok], [0.25, 0.75]) if ok.any() else (np.nan, np.nan)
    slices = {
        "cold_start(min team prior<20)": np.minimum(base["n_prior_h"], base["n_prior_a"]) < 20,
        "first15_team_games": np.minimum(base["gno_h"], base["gno_a"]) < 15,
        "crew_covered": cov,
        "crew_not_covered": ~cov,
        "crew_pace_top_quartile": ok & (pace >= q75),
        "crew_pace_bottom_quartile": ok & (pace <= q25),
    }
    res["slices"] = {
        name: {
            "n_rows": int((m & mk).sum()),
            "d_crps_int": float((arm["crps_int"][m & mk] - base["crps_int"][m & mk]).mean()),
        }
        for name, mk in slices.items()
        if int((m & mk).sum()) >= SLICE_MIN_GAMES
    }
    return res


# --------------------------------------------------------------------------- win sanity


def win_sanity(
    frame: pl.DataFrame, lam: float, min_signal: int, seasons: tuple[int, ...], n_boot: int = N_BOOT
) -> dict[str, Any]:
    """Injury-Elo logit vs the same plus ``b * crew_home_margin_c`` (ridge-logistic, Elo offset
    fixed, month-block walk-forward). ``frame``: game_id, game_date, season, y, elo_logit, d_out,
    d_doubt, crew_home_margin_c, sorted by (game_date, game_id). Tripwire only."""
    from nba.eval.injury_elo_eval import walk_forward_probs

    offset = frame["elo_logit"].to_numpy()
    p0, _ = walk_forward_probs(frame, offset, ("d_out", "d_doubt"), lam, min_signal)
    p1, _ = walk_forward_probs(
        frame, offset, ("d_out", "d_doubt", "crew_home_margin_c"), lam, min_signal
    )
    y = frame["y"].to_numpy().astype(float)
    ll0 = -(y * np.log(p0) + (1 - y) * np.log(1 - p0))
    ll1 = -(y * np.log(p1) + (1 - y) * np.log(1 - p1))
    first = frame["game_date"].min()
    out: dict[str, Any] = {}
    for s in seasons:
        m = (frame["season"].to_numpy() == s) & (frame["game_date"] > first).to_numpy()
        gid = frame["game_id"].to_numpy()[m]
        out[str(s)] = {
            "n_games": int(m.sum()),
            "log_loss_base": float(ll0[m].mean()),
            "log_loss_arm": float(ll1[m].mean()),
            "d_log_loss": ci_dict(ll1[m] - ll0[m], gid, n_boot),
        }
    out["tripwire_ci_below_zero_both"] = bool(
        all(out[str(s)]["d_log_loss"]["hi"] < 0 for s in seasons)
    )
    return out


# --------------------------------------------------------------------------- pass rules


def pass_checks(
    kind: str,
    c: dict[str, Any],
    a2: dict[str, Any] | None,
    p_bh: float,
) -> dict[str, Any]:
    """Frozen pass rule for one (test, season)."""
    props = kind != "total"
    floor = PROP_FLOOR if props else TOTAL_FLOOR
    d = c["d_crps_int"]
    bias_max = PROP_BIAS_MAX if props else TOTAL_BIAS_MAX
    worse = SLICE_WORSE_PROPS if props else SLICE_WORSE_TOTAL
    bad_slices = [k for k, v in c.get("slices", {}).items() if v["d_crps_int"] > worse]
    gain1 = -d["point"]
    gain2 = -a2["d_crps_int"]["point"] if a2 is not None else None
    checks = {
        "1_floor": bool(d["point"] <= floor),
        "2_ci_upper_below_0": bool(d["hi"] < 0.0),
        "3_bh_p_below_0.05": bool(p_bh < 0.05),
        "4a_bias": bool(abs(c["bias_arm"]) <= bias_max),
        "4b_coverage80": bool(COV_LO <= c["cov80_arm"] <= COV_HI),
        "4c_slices": bool(not bad_slices),
        "5_noise_control": bool(
            gain2 is not None and gain2 <= A2_MAX_SHARE * gain1 if gain1 > 0 else False
        ),
    }
    mde = MDE_Z * d["se"]
    return {
        "checks": checks,
        "pass": bool(all(checks.values())),
        "bad_slices": bad_slices,
        "floor": floor,
        "p_bh": float(p_bh),
        "mde": float(mde),
        "powered": bool(mde <= abs(floor)),
    }


def verdict(tests: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """``tests[test][season]`` -> pass_checks output. PASS needs one confirmatory test passing on
    2023 (selection) AND 2024 (confirmation)."""
    per: dict[str, Any] = {}
    for t in CONFIRMATORY:
        sel = tests[t][str(SELECT_SEASON)]
        rep = tests[t][str(REPORT_SEASON)]
        confirmed = bool(sel["pass"] and rep["pass"])
        underpowered = bool(not confirmed and not (sel["powered"] and rep["powered"]))
        per[t] = {
            "selected_on_2023": bool(sel["pass"]),
            "confirmed_on_2024": confirmed,
            "status": "PASS" if confirmed else ("UNDERPOWERED" if underpowered else "FAIL"),
        }
    any_pass = any(v["confirmed_on_2024"] for v in per.values())
    non_pass = [v["status"] for v in per.values() if v["status"] != "PASS"]
    overall = (
        "PASS"
        if any_pass
        else ("UNDERPOWERED" if all(s == "UNDERPOWERED" for s in non_pass) else "FAIL")
    )
    return {
        "per_test": per,
        "overall": overall,
        "consequence": "shadow logging and red-team only; never promotion" if any_pass else "none",
    }


# --------------------------------------------------------------------------- experiment runner


def run_experiment(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    officials: pl.DataFrame,
    elo_params: dict[str, float],
    cfg: ContextResidualConfig,
    out_dir: Path,
    n_boot: int = N_BOOT,
    enforce_repro: bool = True,
    win_base: pl.DataFrame | None = None,
    win_cfg: tuple[float, int] = (5.0, 150),
    stats_arms: tuple[str, ...] = PROP_STATS,
) -> dict[str, Any]:
    """All arms from in-memory frames. ``pgs`` carries the production columns AND the box columns
    (fga, fta, oreb, tov, pf). Checkpoints each (arm, stat) to ``out_dir/arms``. ``win_base``:
    game_id, game_date, season, y, elo_logit, d_out, d_doubt (sorted) for the win tripwire."""
    assert_no_holdout(games)
    arms_dir = out_dir / "arms"
    arms_dir.mkdir(parents=True, exist_ok=True)
    gq = game_quantities(games, pgs)
    strict = crew_features(gq, officials)
    incl = crew_features(gq, officials, inclusive=True)
    perm = permute_crew(strict, games, seed=0)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    prod_cols = [
        "game_id",
        "player_id",
        "team_id",
        "minutes",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "starter",
    ]
    base = build_features(games, pgs.select(prod_cols), static, flagged, elo_params)
    cov_tbl = strict.select(
        "game_id",
        (pl.col("crew_n") >= MIN_OFFICIALS).cast(pl.Float64).alias("cov_covered"),
        pl.col("crew_pace").alias("cov_pace"),
    )
    base = base.join(cov_tbl, on="game_id", how="left")
    frames = {
        "A0": base,
        "A1": attach_crew(base, strict),
        "A2": attach_crew(base, perm),
        "A3": attach_crew(base, incl),
    }
    results: dict[str, Any] = {"props": {}, "total": {}, "win": None, "repro": None}
    scores: dict[tuple[str, str], ArmScores] = {}

    def arm(a: str, stat: str) -> ArmScores:
        key = (a, stat)
        if key in scores:
            return scores[key]
        path = arms_dir / f"{a}_{stat}.npz"
        if path.exists():
            scores[key] = ArmScores.load(path)
        else:
            names = (
                stat_feature_names(stat) if a == "A0" else stat_feature_names(stat, extra=CREW_COLS)
            )
            print(f"fitting {a} {stat}", flush=True)
            scores[key] = collect_arm(frames[a], stat, cfg, names)
            scores[key].save(path)
        return scores[key]

    # kill criterion first: A0 pts reproduces production (G0 item 4)
    a0 = arm("A0", "pts")
    repro = {str(s): float(a0.crps_int[a0.season == s].mean()) for s in TEST_SEASONS}
    ok = all(abs(repro[str(s)] - REPRO_PTS[s]) <= REPRO_TOL for s in TEST_SEASONS)
    results["repro"] = {
        "observed": repro,
        "expected": {str(k): v for k, v in REPRO_PTS.items()},
        "tol": REPRO_TOL,
        "pass": bool(ok),
    }
    print("A0 pts integer CRPS", repro, "repro_ok", ok, flush=True)
    if enforce_repro and not ok:
        results["stopped"] = "A0 does not reproduce production (G0 item 4 / kill criterion)"
        return results
    for stat in stats_arms:
        arm("A0", stat)
        arm("A1", stat)
        if stat in ("pts", "fg3m"):
            arm("A2", stat)
            arm("A3", stat)
    for stat in stats_arms:
        results["props"][stat] = {}
        for s in TEST_SEASONS:
            r = {"A1-A0": contrast_props(arm("A0", stat), arm("A1", stat), s, n_boot)}
            if stat in ("pts", "fg3m"):
                r["A2-A0"] = contrast_props(arm("A0", stat), arm("A2", stat), s, n_boot, False)
                r["A3-A0"] = contrast_props(arm("A0", stat), arm("A3", stat), s, n_boot, False)
                r["A3-A1"] = contrast_props(arm("A1", stat), arm("A3", stat), s, n_boot, False)
            results["props"][stat][str(s)] = r
    # total head
    tf = build_total_frame(games, base, elo_params)
    tf = tf.join(strict.select("game_id", *CREW_COLS), on="game_id", how="left")
    tf = tf.join(cov_tbl, on="game_id", how="left")
    tf_perm = tf.drop(list(CREW_COLS)).join(
        perm.select("game_id", *CREW_COLS), on="game_id", how="left"
    )
    t0 = collect_total(tf, cfg, list(TOTAL_BASE_FEATURES))
    t1 = collect_total(tf, cfg, [*TOTAL_BASE_FEATURES, *CREW_COLS])
    t2 = collect_total(tf_perm, cfg, [*TOTAL_BASE_FEATURES, *CREW_COLS])
    for s in TEST_SEASONS:
        results["total"][str(s)] = {
            "T1-T0": contrast_total(t0, t1, s, n_boot),
            "T2-T0": contrast_total(t0, t2, s, n_boot),
        }
    if win_base is not None:
        wf = win_base.join(
            strict.select(
                "game_id",
                (pl.col("crew_home_margin") - pl.col("league_home_margin"))
                .fill_null(0.0)
                .alias("crew_home_margin_c"),
            ),
            on="game_id",
            how="left",
        ).with_columns(pl.col("crew_home_margin_c").fill_null(0.0))
        results["win"] = win_sanity(
            wf.sort(["game_date", "game_id"]), win_cfg[0], win_cfg[1], TEST_SEASONS, n_boot
        )
    # pass rules: confirmatory family per season, BH over {pts, fg3m, total}
    tests: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in CONFIRMATORY}
    for s in TEST_SEASONS:
        ps = [
            results["props"]["pts"][str(s)]["A1-A0"]["p"],
            results["props"]["fg3m"][str(s)]["A1-A0"]["p"],
            results["total"][str(s)]["T1-T0"]["p"],
        ]
        adj = bh_adjust(np.array(ps))
        for t, p_adj in zip(CONFIRMATORY, adj, strict=True):
            if t == "total":
                c, a2 = results["total"][str(s)]["T1-T0"], results["total"][str(s)]["T2-T0"]
            else:
                c, a2 = results["props"][t][str(s)]["A1-A0"], results["props"][t][str(s)]["A2-A0"]
            tests[t][str(s)] = pass_checks(t, c, a2, float(p_adj))
    results["tests"] = tests
    results["verdict"] = verdict(tests)
    contam = []
    for t in ("pts", "fg3m"):
        for s in TEST_SEASONS:
            d31 = results["props"][t][str(s)]["A3-A1"]["d_crps_int"]["point"]
            if abs(d31) > abs(PROP_FLOOR) and tests[t][str(s)]["pass"]:
                contam.append(f"{t} {s}: A3-A1 {d31:+.4f}")
    results["contaminated"] = contam
    return results


def build_win_base(db_path: str, elo_params: dict[str, float]) -> pl.DataFrame:
    """Production injury-Elo inputs per game (read-only): y, elo_logit, d_out, d_doubt."""
    from nba.models.injury_elo import (
        build_injury_features,
        feature_config_from,
        load_games_frame,
        load_stats_frame,
        sequential_elo_logits,
    )

    cfg = yaml.safe_load(Path("configs/injury_elo.yaml").read_text())
    con = duckdb.connect(db_path, read_only=True)
    try:
        g = load_games_frame(con, MAX_SEASON)
        st = load_stats_frame(con, MAX_SEASON)
        feats = build_injury_features(con, g, st, feature_config_from(cfg), with_oracle=False)
    finally:
        con.close()
    assert_no_holdout(g)
    g = g.with_columns(pl.Series("elo_logit", sequential_elo_logits(g, elo_params)))
    return (
        g.join(feats.select("game_id", "d_out", "d_doubt"), on="game_id", how="left")
        .with_columns(pl.col("d_out").fill_null(0.0), pl.col("d_doubt").fill_null(0.0))
        .select("game_id", "game_date", "season", "y", "elo_logit", "d_out", "d_doubt")
        .sort(["game_date", "game_id"])
    )


def run_all(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """The real run: refuses unless G0 passed (and its sha256 still matches the log)."""
    require_g0(out_dir)
    elo_params = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    games, _pgs_prod, static, avail = load_inputs(db_path, None)
    _g2, pgs, officials = load_referee_inputs(db_path)
    win_base = build_win_base(db_path, elo_params)
    with heavy_lock():
        res = run_experiment(
            games,
            pgs,
            static,
            avail,
            officials,
            elo_params,
            cfg,
            out_dir,
            n_boot,
            win_base=win_base,
        )
    (out_dir / "results.json").write_text(canonical_json(res))
    return res


# --------------------------------------------------------------------------- report


def _fmt_ci(c: dict[str, float], nd: int = 4) -> str:
    return f"{c['point']:+.{nd}f} [{c['lo']:+.{nd}f}, {c['hi']:+.{nd}f}]"


def _g0_table(g0: dict[str, Any]) -> list[str]:
    c1, c2, c3 = (
        g0["checks"][k] for k in ("1_officials_coverage", "2_name_id_1to1", "3_pf_fta_fields")
    )

    def ok(b: bool) -> str:
        return "PASS" if b else "FAIL"

    ln = [
        "| item | value | threshold | result |",
        "|---|---|---|---|",
        f"| 1a games in 2022-24 | {c1['n_games']} | {c1['n_games_expected']} (doc) | "
        f"{'matches' if c1['n_games_matches_doc'] else 'DIFFERS'} |",
        f"| 1b share of games with >= 3 distinct non-NULL official_id | {c1['share_ge3_officials']:.4f} | "
        f">= {c1['threshold_overall']} | {ok(c1['share_ge3_officials'] >= c1['threshold_overall'])} |",
    ]
    for s, v in c1["by_season"].items():
        ln.append(
            f"| 1c season {s} ({v['n_games']} games) | {v['share_ge3']:.4f} | >= {c1['threshold_season']} | "
            f"{ok(v['share_ge3'] >= c1['threshold_season'])} |"
        )
    ln += [
        f"| 1d worst calendar month ({c1['worst_month']}) | {c1['min_month_share']:.4f} | "
        f">= {c1['threshold_month']} | {ok(c1['min_month_share'] >= c1['threshold_month'])} |",
        f"| 1e share with 1 or 2 officials | {c1['share_1_or_2_officials']:.4f} | <= {c1['threshold_few_max']} | "
        f"{ok(c1['share_1_or_2_officials'] <= c1['threshold_few_max'])} |",
        f"| 2a ids carrying two names | {len(c2['ids_with_two_names'])} | 0 | {ok(not c2['ids_with_two_names'])} |",
        f"| 2b names carrying > 1 official_id | {c2['n_names_with_multiple_ids']} | 0 unresolved by jersey | "
        f"{ok(c2['n_names_unresolved_by_jersey'] == 0)} ({c2['n_names_unresolved_by_jersey']} unresolved) |",
        f"| 3a pf and fta non-NULL, rows with minutes > 0 (n={c3['rows_minutes_gt0']}) | "
        f"{c3['share_pf_fta_non_null_minutes_gt0']:.5f} | >= {c3['threshold']} | "
        f"{ok(c3['share_pf_fta_non_null_minutes_gt0'] >= c3['threshold'])} |",
        f"| 3b same, rows with non-NULL minutes (n={c3['rows_minutes_non_null']}) | "
        f"{c3['share_pf_fta_non_null_minutes_non_null']:.5f} | >= {c3['threshold']} | "
        f"{ok(c3['share_pf_fta_non_null_minutes_non_null'] >= c3['threshold'])} |",
        "| 4 A0 reproduces production pts CRPS 3.1528 / 3.1884 (tol 1e-4) | not evaluated | kill criterion at "
        "the first arm fit | n/a (no arm is fitted) |",
    ]
    return ln


def render_report(out_dir: Path = OUT_DIR, report_date: str = "2026-10-10") -> str:
    g0 = json.loads((out_dir / "g0.json").read_text())
    aud = json.loads((out_dir / "missingness.json").read_text())
    g0_sha = (out_dir / "g0.sha256").read_text().split()[0]
    aud_sha = (out_dir / "missingness.sha256").read_text().split()[0]
    res_path = out_dir / "results.json"
    res = json.loads(res_path.read_text()) if res_path.exists() else None
    hc = g0["hash_check"]
    c2 = g0["checks"]["2_name_id_1to1"]
    ln = [
        "# Referee crew experiment (docs/REFEREES.md, frozen)",
        "",
        f"Run date {report_date}. Module `research/eval/referees_eval.py`. Season 2025 was never loaded "
        "(SQL `season <= 2024`, asserted in memory); `nba.duckdb` opened `read_only=True`; no model "
        "was fitted before gate G0 was written and hashed.",
        "",
        "## 1. Hash verification",
        "",
        "| item | value |",
        "|---|---|",
        f"| frozen section sha256 now (`awk ... | shasum -a 256`, reproduced in python) | `{hc['doc_frozen_sha256']}` |",
        f"| recorded in docs/TEST_LEDGER.md (REFEREES notice, 2026-10-09) | `{hc['ledger_recorded_sha256']}` |",
        f"| constant in the module | `{hc['constant_in_module']}` |",
        f"| freeze commit | `{hc['freeze_commit']}` |",
        f"| match | **{'yes' if hc['match'] else 'NO - STOP'}** |",
        "",
        "## 2. Gate G0 (values vs thresholds)",
        "",
        f"`reports/referees/g0.json` sha256 `{g0_sha}` (logged in `g0.sha256` before any arm); "
        f"missingness audit `reports/referees/missingness.json` sha256 `{aud_sha}` (embedded in g0.json).",
        "",
        *_g0_table(g0),
        "",
        f"**G0 {'PASSED' if g0['g0_pass'] else 'FAILED'}**"
        + ("" if g0["g0_pass"] else f" on: {', '.join(g0['failed_items'])}."),
        "",
    ]
    if c2["n_names_with_multiple_ids"]:
        ln += [
            "Names carrying more than one `official_id` (jersey does not separate any pair, so the "
            "'same-name ids resolved by jersey' clause cannot rescue them):",
            "",
            "| name | official_id : jerseys (slots) | resolved by jersey |",
            "|---|---|---|",
        ]
        for r in c2["names_with_multiple_ids"]:
            ids = "; ".join(
                f"{k} : {','.join(v['jerseys'])} ({v['n_slots']})" for k, v in r["ids"].items()
            )
            ln.append(f"| {r['name']} | {ids} | {'yes' if r['resolved_by_jersey'] else 'no'} |")
        ln += [
            "",
            f"Slots on the minority duplicate ids: {c2['n_slots_on_non_primary_duplicate_ids']} (of "
            "about 12,000 official-game slots; informational only: the gate is binary and the frozen "
            "text says duplicates by name are a stop). "
            f"{c2['ids_with_two_jerseys_informational']} ids carry two jersey numbers (a jersey change; benign).",
            "",
        ]
    ln += [
        "## 3. Missingness audit (before any model)",
        "",
        f"Games {aud['n_games']}, without a usable crew (< 2 non-NULL official_id): **{aud['n_missing_games']}**. "
        f"Audit tests: {aud['tests_status']}. Player-row shares by played/unplayed/starter are all "
        f"{aud['player_rows']['share_missing_played']:.4f}/{aud['player_rows']['share_missing_unplayed']:.4f}/"
        f"{aud['player_rows']['share_missing_starter']:.4f}. Crew-column NULL pattern vs `minutes IS NULL`: "
        f"{aud['crew_null_vs_minutes_null']['rows_crew_null']} player rows with a NULL crew column "
        "(opening-day warm-up only), minutes-NULL rate "
        f"{aud['crew_null_vs_minutes_null']['minutes_null_rate_when_crew_null']:.3f} vs "
        f"{aud['crew_null_vs_minutes_null']['minutes_null_rate_when_crew_present']:.3f} "
        f"(diff {aud['crew_null_vs_minutes_null']['diff']:+.3f}; STOP threshold 0.02). "
        f"STOP from the audit: **{'yes' if aud['stop'] else 'no'}**"
        + (f" ({'; '.join(aud['stops'])})" if aud["stops"] else "")
        + ".",
        "",
    ]
    if res is None:
        ln += _not_run_section(g0)
    else:
        ln += _results_section(res)
    ln += ["", "## Implementation choices the frozen text leaves open (fixed before any fit)", ""]
    ln += [f"* {c}" for c in IMPLEMENTATION_CHOICES]
    ln += ["", *_ledger_and_reading(g0, res)]
    return "\n".join(ln) + "\n"


def _not_run_section(g0: dict[str, Any]) -> list[str]:
    return [
        "## 4. Results",
        "",
        "**Not run.** The frozen text is explicit: if G0 fails the experiment is not run ('not \"run "
        "anyway\"': re-pull and re-audit). No arm (A0-A3, T0-T2, win) was fitted, no CRPS, CI or p-value "
        "exists, and nothing was computed against any outcome. The code for every arm, the total head, "
        "the win tripwire and the pass rules is in the module and unit-tested on synthetic leagues, so a "
        "repaired G0 is followed by one command.",
        "",
        "Maintainer command once G0 passes (not before):",
        "",
        "```",
        "uv run python -m research.eval.referees_eval g0     # re-audit, re-hash",
        "uv run python -m research.eval.referees_eval run    # takes data/ops/heavy.lock itself",
        "uv run python -m research.eval.referees_eval report",
        "```",
    ]


def _results_section(res: dict[str, Any]) -> list[str]:
    ln = ["## 4. Results", ""]
    if res.get("stopped"):
        ln += [
            f"**STOPPED:** {res['stopped']}; observed {res['repro']['observed']} vs expected {res['repro']['expected']}."
        ]
        return ln
    ln += [
        f"A0 pts integer CRPS {res['repro']['observed']} (expected {res['repro']['expected']}, tol "
        f"{res['repro']['tol']}): reproduced = {res['repro']['pass']}.",
        "",
        "Delta = arm - control (negative is better), integer-support CRPS, game-clustered 95% bootstrap CI "
        "(B=2000, seed 0). BH over {pts, fg3m, total} per season; everything else is descriptive.",
        "",
        "| test | season | contrast | n rows (games) | control CRPS | arm CRPS | dCRPS [CI] | p | covered-only dCRPS [CI] (n) | bias arm | cov80 arm |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def row(test: str, season: str, name: str, c: dict[str, Any]) -> str:
        co = c.get("covered_only")
        cos = f"{_fmt_ci(co)} ({co['n_rows']})" if co else "n/a"
        return (
            f"| {test} | {season} | {name} | {c['n_rows']} ({c['n_games']}) | {c['base_crps_int']:.4f} | "
            f"{c['arm_crps_int']:.4f} | {_fmt_ci(c['d_crps_int'])} | {c['p']:.3f} | {cos} | "
            f"{c['bias_arm']:+.3f} | {c['cov80_arm']:.3f} |"
        )

    for stat, by in res["props"].items():
        for season, cs in by.items():
            for name, c in cs.items():
                ln.append(
                    row(
                        stat
                        + (
                            " (confirmatory)" if stat in ("pts", "fg3m") and name == "A1-A0" else ""
                        ),
                        season,
                        name,
                        c,
                    )
                )
    for season, cs in res["total"].items():
        for name, c in cs.items():
            ln.append(
                row("total" + (" (confirmatory)" if name == "T1-T0" else ""), season, name, c)
            )
    ln += [
        "",
        "### Pass rules (confirmatory family)",
        "",
        "| test | season | dCRPS | floor | CI hi | BH p | MDE (2.8 SE) | checks failed | pass |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for t in CONFIRMATORY:
        for s in ("2023", "2024"):
            tt = res["tests"][t][s]
            c = res["total"][s]["T1-T0"] if t == "total" else res["props"][t][s]["A1-A0"]
            failed = [k for k, v in tt["checks"].items() if not v]
            ln.append(
                f"| {t} | {s} | {c['d_crps_int']['point']:+.4f} | {tt['floor']} | {c['d_crps_int']['hi']:+.4f} | "
                f"{tt['p_bh']:.3f} | {tt['mde']:.4f} ({'powered' if tt['powered'] else 'UNDERPOWERED'}) | "
                f"{', '.join(failed) or 'none'}{' ; slices: ' + ', '.join(tt['bad_slices']) if tt['bad_slices'] else ''} | "
                f"{'PASS' if tt['pass'] else 'no'} |"
            )
    v = res["verdict"]
    ln += ["", f"**Verdict: {v['overall']}** " + json.dumps(v["per_test"]), ""]
    if res.get("win"):
        w = res["win"]
        ln += ["### Win sanity (expected null; tripwire only)", ""]
        for s in ("2023", "2024"):
            ln.append(f"* {s}: n={w[s]['n_games']} games, dLogLoss {_fmt_ci(w[s]['d_log_loss'])}")
        ln.append(
            f"* tripwire fired (CI upper < 0 on both seasons): **{w['tripwire_ci_below_zero_both']}**"
        )
    if res.get("contaminated"):
        ln += ["", f"**CONTAMINATED (A3 vs A1 kill criterion):** {res['contaminated']}"]
    return ln


def _ledger_and_reading(g0: dict[str, Any], res: dict[str, Any] | None) -> list[str]:
    sha = g0["hash_check"]["doc_frozen_sha256"][:8]
    c2 = g0["checks"]["2_name_id_1to1"]
    if res is None or res.get("stopped"):
        ledger = (
            f"| T### | 2026-10-10 | REFEREES (frozen sha256 {sha}) gate G0 | G0 FAILED: item 2 name <-> official_id "
            f"1:1 ({c2['n_names_with_multiple_ids']} names carry two official_ids, none separable by jersey; "
            "items 1 and 3 and the missingness audit pass); experiment NOT RUN, no arm fitted, no number "
            "computed | n/a | n/a | n/a | n/a | n/a | gate outcome is a finding; re-pull/re-audit or a new "
            "pre-registration decides what next | not holdout (<=2024; 2025 never loaded) |"
        )
        text = (
            "**Results (2026-10-10).** Gate G0 was computed on the loaded data (3,953 games, 2022-24) and "
            "written to `reports/referees/g0.json` (sha256 logged before any fit). Officials coverage "
            "(100% of games with >= 3 officials, every season and month) and the pf/fta fields "
            "(100% non-NULL) pass; the missingness audit is vacuous (no game lacks a usable crew) and does "
            "not stop; item 2 fails: four officials appear under two `official_id`s each "
            "(Intae Hwang, Biniam Maru, Agon Abazi, Brent Haskill) and the shared jersey numbers do not "
            "separate the ids. Under the frozen rule the experiment is not run; no arm was fitted."
        )
        reading = (
            "The data are complete enough to run (every game has a crew and the box fields are present), "
            "but four officials are stored under two ids each, which the frozen gate treats as a stop; "
            "the affected slots are a handful, so an id-merge or a re-pull would probably clear it, but that is a "
            "decision about the rule's data, not something to do silently. Nothing about referee crews "
            "can be said yet: no effect, no null."
        )
        return [
            "## 5. Proposed ledger rows (draft; maintainer appends, unnumbered)",
            "",
            ledger,
            "",
            "## 6. Draft results-section text for docs/REFEREES.md (not written there)",
            "",
            text,
            "",
            "## 7. Reading",
            "",
            reading,
        ]
    v = res["verdict"]
    rows = []
    for t in CONFIRMATORY:
        for s in ("2023", "2024"):
            c = res["total"][s]["T1-T0"] if t == "total" else res["props"][t][s]["A1-A0"]
            rows.append(
                f"| T### | 2026-10-10 | REFEREES (frozen sha256 {sha}) | {t} {s} A1/T1 vs control, integer-support "
                f"CRPS, n={c['n_rows']} rows / {c['n_games']} games | {c['d_crps_int']['point']:+.4f} | "
                f"{c['d_crps_int']['lo']:+.4f} | {c['d_crps_int']['hi']:+.4f} | game | "
                f"{res['tests'][t][s]['p_bh']:.3f} | verdict {v['per_test'][t]['status']} | not holdout (<=2024) |"
            )
    text = (
        f"**Results (2026-10-10).** Verdict {v['overall']}: "
        + json.dumps(v["per_test"])
        + ". Full tables in "
        "`reports/referees.md`; consequence for a PASS is shadow logging and red-team only."
    )
    reading = (
        f"Verdict {v['overall']}. See the per-test table: a CI spanning 0 on the total head means no power, "
        "not no effect."
    )
    return [
        "## 5. Proposed ledger rows (draft; maintainer appends, unnumbered)",
        "",
        *rows,
        "",
        "## 6. Draft results-section text for docs/REFEREES.md (not written there)",
        "",
        text,
        "",
        "## 7. Reading",
        "",
        reading,
    ]


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("g0", "run", "report"))
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.stage == "g0":
        g0 = run_g0(a.db_path, out)
        print(json.dumps({k: g0[k] for k in ("g0_pass", "failed_items")}, indent=1))
    elif a.stage == "run":
        res = run_all(a.db_path, out)
        print(json.dumps(res.get("verdict", res.get("stopped")), indent=1))
    else:
        REPORT_MD.write_text(render_report(out))
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
