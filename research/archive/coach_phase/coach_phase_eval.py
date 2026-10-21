# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,unused-ignore,index,union-attr,no-any-return,misc"
"""COACH_PHASE evaluation (docs/prereg/COACH_PHASE.md, FROZEN sha256 8111c026, commit 572f08e; this module implements the rule, changes nothing).

    M="-m research.archive.coach_phase.coach_phase_eval"
    .venv/bin/python $M frame     # build the feature frame on the real data and print its summary; NO fit, NO score (smoke)
    .venv/bin/python $M gate      # G1/G4-G6 re-run, P reproduction, reliability screen, planted checks, G7 audit (light, no fit)
    .venv/bin/python $M arms      # PH, CP, PL, S0, O1 walk-forward; minutes and props scoring; verdict (needs a passed gate)
    .venv/bin/python $M attack    # shuffled labels and single-habit-group knockouts on the 2024 PHASE slice
    .venv/bin/python $M report    # markdown from results.json
    .venv/bin/python $M run       # gate -> arms -> attack -> report under ONE data/ops/heavy.lock

Every fitting stage refuses unless the sha256 of the doc above ``=== RESULTS BELOW ===`` equals :data:`FROZEN_FULL`. The lock is created with
``mkdir`` and a pid file and removed only by its owner. ``nba.duckdb`` is read-only; ``season <= 2024``; season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import bh_adjust, month_blocks
from research.eval.f11_lineup_context import frozen_sha256
from research.eval.team_budget_eval import (
    PAIRS_DIR,
    _write_json,
    decision_p,
    heavy_lock,
    legs_with_p,
    load_world,
    scale_to_int,
)
from research.features.coach_habits import (
    HABIT_COLS,
    HABITS,
    INTER_COLS,
    PHASE_COLS,
    reliability_screen,
    series_prev_game,
    team_game_features,
    team_game_inputs,
)

DOC = Path("docs/prereg/COACH_PHASE.md")
FROZEN_FULL = "8111c02686a3386c9942097b1db98c81ea9392c4a24017004101fe08ae28f9af"
OUT_DIR = Path("reports/prereg_coach_phase")
REPORT_MD = Path("reports/prereg_coach_phase.md")
CACHE_DIR = Path("data/ops/coach_phase_cache")
LT_DIR = Path("reports/lower_tail")
MIN_DIR = Path("reports/minutes_v2")
GATES_JSON = OUT_DIR / "gates.json"
STATS = ("pts", "reb", "ast", "fg3m")
SEASONS = (2023, 2024)
N_BOOT = 2000
ALPHA = 10.0
MIN_TRAIN = 2000
R_LO, R_HI = 0.8, 1.25
MIN_FLOOR = -0.10  # minutes, Family M
PROP_FLOOR = -0.005  # Family R
ATTACK_MIN, ATTACK_PROP = -0.03, -0.003  # arm must beat the placebo by this much
COACH_MIN, COACH_PROP = -0.03, -0.002  # coach claim: CP minus PH
EARLY_TOL_MIN, EARLY_TOL_PROP = 0.03, 0.003
SLICE_WORSE_MIN, SLICE_WORSE_PROP = 0.10, 0.01
MIN_SLICE_N = 300
COV_TOL = 0.03
BIAS_TOL_PROP, BIAS_TOL_MIN = 0.05, 0.5
CARRY = 0.8
CANNOT_CONFIRM_PROP, CANNOT_CONFIRM_MIN = 0.005, 0.10
AUDIT_GAP = 0.02
SCREENED = (
    "top8",
    "starter_min",
    "bench_share",
)  # the frozen text screens these three; depth, top5, blowout are kept by reference
BUCKETS = ((0.0, 5.0), (5.0, 10.0), (10.0, 20.0), (20.0, 1e9))
BASE_COLS: tuple[str, ...] = (*PHASE_COLS, "prev_series_minutes", "prev_series_missing")
S0_COLS: tuple[str, ...] = ("po", "late15")
SEED = 0
ARMS_MODEL = ("PH", "CP", "PL", "S0")
PRIMARY_SLICES = ("all", "po", "late15", "phase", "early")

AMBIGUITIES = {
    "starter10": "the player's start share over his appearances in the team's previous 10 games (research.budget.asof.asof_roster start_rate), 0 when he has none; training rows and scored rows use the same definition (the stored row_starter10 exists only for scored rows)",
    "late15 gate count": "the harness uses the true games_prior >= 67 (the team's last 15 games); research.archive.coach_phase.coach_phase_gates counts with a 1-based cum_count, so the committed gate counts (326 / 330 phase games) cover the last 16 games and overstate LATE15 by about 6%; the gate's own pass conditions are unaffected",
    "late15 feature": "game-level, the SLICE definition: regular season and either team has played >= 67 games; gp_frac is the team's own games_prior / 82 (1.0 in the playoffs and play-in)",
    "play-in (005)": "belongs to neither habit phase nor to PO / LATE15; its rows carry po = 0, gp_frac = 1 and sit in 'all'",
    "league PO fallback": "'fewer than 30 earlier league playoff games' is read as fewer than 30 earlier league playoff TEAM-games (each game contributes two observations)",
    "blowout EWMA": "the two conditional means (|margin| >= 20 and <= 10) each keep their own EWMA over the coach's games of THAT kind and phase (half-life 20 such games, K = 10), each shrunk to the league as-of mean of that kind; the habit is their difference",
    "screen scope": "the frozen text screens top8, starter_min, bench_share; depth and blowout are kept by reference to TEAM_BUDGET and top5 is not named, so it is kept unscreened; a failed habit drops its REG, PO, delta and interaction columns; the screen result for every habit is reported",
    "screen season": "'coaches with >= 60 games in 2022-23' is read as regular-season games of the 2022 season label (2022-23)",
    "training rows": "ridge trains on every played row (minutes > 0, p_mu present) dated before the block, any n_prior; the scored rows are the stored A0 / minutes rows (n_prior >= 5)",
    "role interactions": "phase columns and coach habit columns are entered alone, times z_mu = (p_mu - 24) / 12 and times starter10; the 12 gp / late15 x delta interaction columns are entered alone only",
    "prev_series_minutes": "the player's minutes in the previous game of the same series (0 if he was not in that box); game 1 uses his p_mu with prev_series_missing = 1; both are 0 outside the playoffs",
    "oracle O1": "mu = realised minutes in the minutes arm and r = clip(realised minutes / p_mu, 0.8, 1.25) in the props arm; descriptive",
    "coach claim": "CP minus PH on the PHASE slice in 2024: minutes <= -0.03 or ANY of the four stats <= -0.002, and no single habit-group knockout keeps <= 20% of the gain; it can reach SUGGESTIVE (report season only), never PASS",
    "cannot confirm": "a primary cell whose own 2024 MDE exceeds 0.005 (props) or 0.10 (minutes) is labelled 'cannot confirm'; it can still PASS",
    "planted tolerance": "'byte-identical' is checked to 1e-9 absolute: polars float sums are order-nondeterministic at about 3e-16 between two runs on identical input (measured), so exact equality would fail on the unedited data",
    "attack slice": "the shuffled-label and knockout attacks are scored on the 2024 PHASE slice for minutes and each stat; the minutes cells on PO and LATE15 use the PHASE value as their attack gain",
    "slices omitted": "star-out is omitted from the descriptive slices (it needs the TEAM_BUDGET roster frame); no pass rule uses it",
    "decision score": "P(over) legs restricted to PHASE games; secondary, in a try block, cannot rescue or fail the rule",
}


# --------------------------------------------------------------------------- freeze
def require_frozen(doc: Path = DOC, sha: str = FROZEN_FULL) -> None:
    if not sha or frozen_sha256(doc) != sha:
        raise RuntimeError(
            "COACH_PHASE is not frozen at the expected sha256 (doc changed or unset): no arm may be fitted"
        )


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- columns, ridge, walk-forward (pure)
def drop_suffixes(dropped: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"_{h}" for h in dropped)


def design_matrix(
    frame: pl.DataFrame,
    plain: list[str],
    role: list[str],
    z: np.ndarray,
    s10: np.ndarray,
    dropped: tuple[str, ...] = (),
) -> tuple[np.ndarray, list[str]]:
    """Columns: each ``role`` column alone, times z_mu and times starter10; each ``plain`` column alone. Columns of dropped habits are removed."""
    suf = drop_suffixes(dropped)

    def keep(c: str) -> bool:
        return not any(c.endswith(s) for s in suf)

    names: list[str] = []
    cols: list[np.ndarray] = []
    for c in role:
        if not keep(c):
            continue
        x = frame[c].to_numpy().astype(float)
        for tag, v in ((c, x), (f"{c}*z", x * z), (f"{c}*s", x * s10)):
            names.append(tag)
            cols.append(v)
    for c in plain:
        if keep(c):
            names.append(c)
            cols.append(frame[c].to_numpy().astype(float))
    return np.column_stack(cols), names


def ridge_fit(
    x: np.ndarray, y: np.ndarray, alpha: float = ALPHA
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Standardize columns, centre the target, ridge with penalty ``alpha`` on the standardized scale (intercept free)."""
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    xs = (x - mu) / sd
    ym = float(y.mean())
    a = xs.T @ xs + alpha * np.eye(xs.shape[1])
    w = np.linalg.solve(a, xs.T @ (y - ym))
    return w, mu, sd, ym


def ridge_predict(
    model: tuple[np.ndarray, np.ndarray, np.ndarray, float], x: np.ndarray
) -> np.ndarray:
    w, mu, sd, ym = model
    return ((x - mu) / sd) @ w + ym


def walk_forward_delta(
    x: np.ndarray,
    y: np.ndarray,
    day: np.ndarray,
    season: np.ndarray,
    train_ok: np.ndarray,
    block_edges: list[tuple[int, int]],
    seasons: tuple[int, ...] = SEASONS,
    alpha: float = ALPHA,
    permute: bool = False,
    seed: int = SEED,
    min_train: int = MIN_TRAIN,
) -> np.ndarray:
    """Out-of-fold prediction of ``y`` for rows of ``seasons``: each month block is predicted by a ridge fit on rows dated STRICTLY before the
    block (``train_ok`` rows only). ``permute`` shuffles the training target within the block's training set (the shuffled-label attack)."""
    out = np.full(len(y), np.nan)
    rng = np.random.default_rng(seed)
    in_test = np.isin(season, seasons)
    for lo, hi in block_edges:
        te = in_test & (day >= lo) & (day < hi)
        if not te.any():
            continue
        tr = train_ok & (day < lo)
        if tr.sum() < min_train:
            continue
        yt = y[tr]
        if permute:
            yt = rng.permutation(yt)
        out[te] = ridge_predict(ridge_fit(x[tr], yt, alpha), x[te])
    return out


def day_numbers(dates: list[Any]) -> np.ndarray:
    import datetime as dt

    return np.array([(d - dt.date(1970, 1, 1)).days for d in dates], dtype=np.int64)


def block_day_edges(dates: list[Any], seasons_mask: np.ndarray) -> list[tuple[int, int]]:
    sub = pl.DataFrame({"game_date": [d for d, m in zip(dates, seasons_mask, strict=True) if m]})
    edges = month_blocks(sub)
    return [(int(day_numbers([a])[0]), int(day_numbers([b])[0])) for a, b in edges]


def prop_factor(p_mu: np.ndarray, delta: np.ndarray) -> np.ndarray:
    """r = clip((p_mu + delta) / p_mu, 0.8, 1.25); rows without a prediction keep r = 1 (gamma = 1)."""
    r = np.clip((p_mu + np.nan_to_num(delta, nan=0.0)) / np.maximum(p_mu, 0.5), R_LO, R_HI)
    return np.where(np.isnan(delta), 1.0, r)


def minutes_mu(p_mu: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return np.clip(p_mu + np.nan_to_num(delta, nan=0.0), 0.0, 48.0)


# --------------------------------------------------------------------------- frame
def build_universe(world: dict[str, Any], pm: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Universe of played (game, player) rows 2022-2024 with production p_mu / p_sigma and every feature. No arm is fitted here."""
    from research.budget.asof import asof_roster

    games, box, coach = world["games"], world["box"], world["coach"]
    tgf = team_game_features(games, coach.select("game_id", "team_id", "coach_key"), box)
    tgf_pl = team_game_features(
        games, coach.select("game_id", "team_id", "coach_key"), box, placebo_seed=SEED
    ).select(
        "game_id", "team_id", *[pl.col(c).alias(f"pl_{c}") for c in (*HABIT_COLS, *INTER_COLS)]
    )
    tg = games.select("game_id", "game_date", pl.col("home_team").alias("team_id")).vstack(
        games.select("game_id", "game_date", pl.col("away_team").alias("team_id"))
    )
    ros = (
        asof_roster(box, tg)
        .select("game_id", "team_id", "player_id", "start_rate")
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )
    prev = series_prev_game(games.filter(pl.col("game_id").str.starts_with("004")))
    b = box.filter(pl.col("minutes") > 0).with_columns(pl.col("player_id").cast(pl.Int64))
    pm2 = pm.select("game_id", pl.col("player_id").cast(pl.Int64), "p_mu", "p_sigma").drop_nulls()
    u = (
        b.join(pm2, on=["game_id", "player_id"], how="inner")
        .join(games.select("game_id", "season", "game_date", "home_team"), on="game_id")
        .join(tgf, on=["game_id", "team_id"], how="left")
        .join(tgf_pl, on=["game_id", "team_id"], how="left")
        .join(ros, on=["game_id", "team_id", "player_id"], how="left")
        .join(prev, on="game_id", how="left")
    )
    pm_prev = b.select(
        pl.col("game_id").alias("prev_game_id"), "player_id", pl.col("minutes").alias("_pm")
    )
    u = u.join(pm_prev, on=["prev_game_id", "player_id"], how="left")
    po = pl.col("po") > 0.5
    u = u.with_columns(
        pl.col("start_rate").fill_null(0.0).alias("s10"),
        ((pl.col("p_mu") - 24.0) / 12.0).alias("z_mu"),
        pl.when(po & pl.col("prev_game_id").is_null())
        .then(1.0)
        .otherwise(0.0)
        .alias("prev_series_missing"),
        pl.when(po & pl.col("prev_game_id").is_null())
        .then(pl.col("p_mu"))
        .when(po)
        .then(pl.col("_pm").fill_null(0.0))
        .otherwise(0.0)
        .alias("prev_series_minutes"),
        pl.col("game_id").str.slice(0, 3).alias("stage"),
        (pl.col("home_team") == pl.col("team_id")).cast(pl.Float64).alias("home_game"),
    ).drop("_pm")
    u = u.sort(["game_date", "game_id", "player_id"])
    dup = u.height - u.select("game_id", "player_id").unique().height
    if dup:
        raise ValueError(f"{dup} duplicated (game, player) rows in the universe")
    return {"universe": u}


def build_all() -> dict[str, Any]:
    from research.eval.minutes_v2_eval import production_minutes_table

    world = load_world()
    _log("world loaded; production minutes table ...")
    pm = production_minutes_table(world["sc"])
    fr: dict[str, Any] = dict(build_universe(world, pm))
    fr["world"] = world
    return fr


def arm_columns(
    arm: str, dropped: tuple[str, ...] = (), knockout: str | None = None
) -> tuple[list[str], list[str]]:
    """(role columns, plain columns) of an arm, in the frame."""
    if arm == "S0":
        return list(S0_COLS), []
    base = list(BASE_COLS)
    if arm == "PH":
        return base, []
    drop = (*dropped, knockout) if knockout else dropped
    suf = drop_suffixes(tuple(d for d in drop if d))
    hab = [c for c in HABIT_COLS if not any(c.endswith(s) for s in suf)]
    inter = [c for c in INTER_COLS if not any(c.endswith(s) for s in suf)]
    if arm == "PL":
        return base + [f"pl_{c}" for c in hab], [f"pl_{c}" for c in inter]
    return base + hab, inter


def arm_delta(
    u: pl.DataFrame,
    arm: str,
    dropped: tuple[str, ...] = (),
    knockout: str | None = None,
    permute: bool = False,
) -> np.ndarray:
    """Walk-forward delta_hat (minutes - p_mu, rows that played) of one arm for every universe row of the test seasons (NaN elsewhere)."""
    role, plain = arm_columns(arm, dropped, knockout)
    x, _ = design_matrix(u, plain, role, u["z_mu"].to_numpy(), u["s10"].to_numpy(), ())
    y = u["minutes"].to_numpy().astype(float) - u["p_mu"].to_numpy()
    day = day_numbers(u["game_date"].to_list())
    season = u["season"].to_numpy()
    edges = block_day_edges(u["game_date"].to_list(), np.isin(season, SEASONS))
    return walk_forward_delta(x, y, day, season, np.ones(len(y), bool), edges, permute=permute)


# --------------------------------------------------------------------------- scoring
def slice_masks(meta: pl.DataFrame) -> dict[str, np.ndarray]:
    """Slices of one row set. ``meta`` columns: po, late15, stage, s10, series_game, elim_game, home_game, round, clinched_6, elim_6,
    elim_10, coach_n_reg, coach_n_po, b2b (nullable columns become False)."""

    def m(e: pl.Expr) -> np.ndarray:
        return meta.select(e.fill_null(False)).to_series().to_numpy().astype(bool)

    po = m(pl.col("po") > 0.5)
    reg = m(pl.col("stage") == "002")
    late = reg & m(pl.col("late15") > 0.5)
    out: dict[str, np.ndarray] = {
        "all": np.ones(meta.height, bool), "po": po, "late15": late, "phase": po | late, "early": reg & ~late,
        "starter": m(pl.col("s10") >= 0.5), "bench": m(pl.col("s10") < 0.5),
        "po_home": po & m(pl.col("home_game") > 0.5), "po_away": po & m(pl.col("home_game") < 0.5),
        "elim_game": po & m(pl.col("elim_game") > 0.5),
        "late_clinched_6": late & m(pl.col("clinched_6") > 0.5), "late_elim_6": late & m(pl.col("elim_6") > 0.5),
        "late_elim_10": late & m(pl.col("elim_10") > 0.5),
        "coach_new": m((pl.col("coach_n_reg") + pl.col("coach_n_po")) < 20),
        "b2b": m(pl.col("b2b") > 0.5),
    }  # fmt: skip
    for g in range(1, 8):
        out[f"series_game_{g}"] = po & m(pl.col("series_game") == g)
    for r in range(1, 5):
        out[f"round_{r}"] = po & m(pl.col("round") == r)
    return out


def boot_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    from research.eval import four_factors_eval as F

    return F._delta_stats(delta, gid)


def score_target(
    name: str,
    scores: dict[str, dict[str, np.ndarray]],
    gid: np.ndarray,
    season: np.ndarray,
    masks: dict[str, np.ndarray],
    base: str,
    floor_tol: float,
) -> list[dict[str, Any]]:
    """Rows per (season, arm, slice): CRPS, paired delta vs ``base`` (bootstrap CI / p / MDE on the primary slices, point only on descriptive
    slices), paired contrasts vs PL / PH / O1 / S0, bias, cov80. ``scores[arm]`` has crps, pit, resid."""
    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        for arm, sc in scores.items():
            for sl, mk in masks.items():
                k = mk & sm
                n = int(k.sum())
                if n == 0:
                    continue
                row: dict[str, Any] = {
                    "target": name, "season": s, "arm": arm, "slice": sl, "n_rows": n, "n_games": int(len(np.unique(gid[k]))),
                    "crps": float(sc["crps"][k].mean()), "bias": float(sc["resid"][k].mean()),
                    "cov80": float(((sc["pit"][k] > 0.10) & (sc["pit"][k] <= 0.90)).mean()),
                }  # fmt: skip
                if arm != base:
                    d = sc["crps"][k] - scores[base]["crps"][k]
                    row["dcrps"] = float(d.mean())
                    row["base_cov80"] = float(
                        ((scores[base]["pit"][k] > 0.10) & (scores[base]["pit"][k] <= 0.90)).mean()
                    )
                    row["base_bias"] = float(scores[base]["resid"][k].mean())
                    if sl in PRIMARY_SLICES and n >= 30 and len(np.unique(gid[k])) > 1:
                        row.update(
                            {
                                kk: v
                                for kk, v in boot_stats(d, gid[k]).items()
                                if kk in ("ci_lo", "ci_hi", "p", "mde")
                            }
                        )
                        for other in ("PL", "PH", "O1", "S0"):
                            if other in scores and other != arm:
                                row[f"minus_{other}"] = float(
                                    (sc["crps"][k] - scores[other]["crps"][k]).mean()
                                )
                rows.append(row)
    return rows


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH within Family M (minutes, arm in PH/CP x slice po/late15) and Family R (props, arm in PH/CP x 4 stats on phase), per season."""
    for season in SEASONS:
        fam_m = [
            r
            for r in rows
            if r["target"] == "minutes"
            and r["season"] == season
            and r["arm"] in ("PH", "CP")
            and r["slice"] in ("po", "late15")
            and "p" in r
        ]
        fam_r = [
            r
            for r in rows
            if r["target"] in STATS
            and r["season"] == season
            and r["arm"] in ("PH", "CP")
            and r["slice"] == "phase"
            and "p" in r
        ]
        for fam in (fam_m, fam_r):
            if fam:
                adj = bh_adjust(np.array([r["p"] for r in fam]))
                for r, a in zip(fam, adj, strict=True):
                    r["p_bh"] = float(a)


def evaluate(
    res: dict[str, Any],
    tests_ok: bool,
    audit_ok: bool,
    attack: dict[str, Any] | None = None,
    coaches_ok_2024: bool = True,
) -> dict[str, Any]:
    """The frozen pass rule per primary cell. ``res['rows']`` are score_target rows (all targets)."""
    tab = {(r["target"], r["season"], r["arm"], r["slice"]): r for r in res["rows"]}
    cells: dict[str, Any] = {}
    plan = [("minutes", sl) for sl in ("po", "late15")] + [(st, "phase") for st in STATS]
    for target, sl in plan:
        is_min = target == "minutes"
        floor = MIN_FLOOR if is_min else PROP_FLOOR
        for arm in ("PH", "CP"):
            a23, a24 = tab.get((target, 2023, arm, sl)), tab.get((target, 2024, arm, sl))
            if a23 is None or a24 is None or "ci_hi" not in a23 or "ci_hi" not in a24:
                continue
            sel = bool(a23["dcrps"] <= floor and a23["ci_hi"] < 0.0)
            r1 = bool(a24["dcrps"] <= floor and a24["ci_hi"] < 0.0 and a24.get("p_bh", 1.0) <= 0.05)
            early = tab.get((target, 2024, arm, "early"))
            early_ok = bool(
                early is None or early["dcrps"] <= (EARLY_TOL_MIN if is_min else EARLY_TOL_PROP)
            )
            worse = {
                r["slice"]: r["dcrps"]
                for r in res["rows"]
                if r["target"] == target and r["season"] == 2024 and r["arm"] == arm and "dcrps" in r
                and r["n_rows"] >= MIN_SLICE_N and r["dcrps"] > (SLICE_WORSE_MIN if is_min else SLICE_WORSE_PROP)
            }  # fmt: skip
            guards = {
                "cov80": bool(abs(a24["cov80"] - 0.8) <= abs(a24["base_cov80"] - 0.8) + COV_TOL),
                "bias": bool(
                    abs(a24["bias"])
                    <= abs(a24["base_bias"]) + (BIAS_TOL_MIN if is_min else BIAS_TOL_PROP)
                ),
                "early_not_harmed": early_ok,
                "no_slice_worse": not worse,
            }
            beats_pl = bool(a24.get("minus_PL", 0.0) <= (ATTACK_MIN if is_min else ATTACK_PROP))
            o1 = tab.get((target, 2024, "O1", sl))
            not_beat_oracle = bool(o1 is None or a24["dcrps"] >= o1["dcrps"])
            cell: dict[str, Any] = {
                "select_2023": sel, "rule1_2024": r1, "guards": guards, "slices_worse": worse, "beats_placebo": beats_pl,
                "tests_ok": bool(tests_ok), "audit_ok": bool(audit_ok), "not_beating_oracle": not_beat_oracle,
                "mde_2024": a24["mde"], "cannot_confirm": bool(a24["mde"] > (CANNOT_CONFIRM_MIN if is_min else CANNOT_CONFIRM_PROP)),
            }  # fmt: skip
            cell["pass_before_attack"] = bool(
                sel
                and r1
                and all(guards.values())
                and beats_pl
                and tests_ok
                and audit_ok
                and not_beat_oracle
            )
            if attack is not None:
                sh = attack.get("shuffled", {}).get(arm, {}).get(target)
                cell["shuffled_ok"] = bool(sh is not None and sh["gain"] >= -a24["mde"])
                cell["PASS"] = bool(cell["pass_before_attack"] and cell["shuffled_ok"])
            cells[f"{arm}/{target}/{sl}"] = cell
    verdict: dict[str, Any] = {
        "cells": cells,
        "close": not any(c["select_2023"] for c in cells.values()),
    }
    verdict["coach_claim"] = coach_claim(tab, attack, coaches_ok_2024)
    return verdict


def coach_claim(
    tab: dict[Any, Any], attack: dict[str, Any] | None, coaches_ok_2024: bool
) -> dict[str, Any]:
    """CP minus PH on the 2024 PHASE slice. At most SUGGESTIVE (the 2023 select-season coach gate failed), never PASS."""
    out: dict[str, Any] = {"status": "none", "metrics": {}}
    sat = []
    for target in ("minutes", *STATS):
        sl = "phase"
        r = tab.get((target, 2024, "CP", sl))
        if r is None or "minus_PH" not in r:
            continue
        thr = COACH_MIN if target == "minutes" else COACH_PROP
        out["metrics"][target] = {
            "cp_minus_ph": r["minus_PH"],
            "threshold": thr,
            "meets": bool(r["minus_PH"] <= thr),
        }
        if r["minus_PH"] <= thr:
            sat.append(target)
    ko = (attack or {}).get("knockout", {})
    carried = {t: any(v.get(t, {}).get("carries", False) for v in ko.values()) for t in sat}
    out["one_group_carries"] = carried
    if sat and coaches_ok_2024 and attack is not None and not any(carried.values()):
        out["status"] = "SUGGESTIVE (report season only; never PASS)"
    return out


def carries(gain_full: float, gain_drop: float) -> bool:
    """Dropping a habit group leaves <= 20% of the full gain (gains are positive improvements)."""
    return bool(gain_full > 0 and gain_drop <= (1.0 - CARRY) * gain_full)


# --------------------------------------------------------------------------- row sets
def minutes_rows(u: pl.DataFrame) -> dict[str, Any]:
    z = np.load(MIN_DIR / "minutes_scores.npz", allow_pickle=True)
    keys = pl.DataFrame(
        {
            "game_id": z["row_gid"].astype(str),
            "player_id": z["row_pid"].astype(np.int64),
            "_i": np.arange(len(z["row_gid"])),
        }
    )
    j = keys.join(u.with_row_index("_u"), on=["game_id", "player_id"], how="left")
    if j["_u"].null_count():
        raise RuntimeError(f"{j['_u'].null_count()} minutes rows are missing from the universe")
    j = j.sort("_i")
    uu = u[j["_u"].to_numpy()]
    y = z["row_y"].astype(float)
    if not np.allclose(uu["minutes"].to_numpy().astype(float), y, atol=1e-4):
        raise RuntimeError("minutes row alignment: realised minutes differ from the stored rows")
    meta = uu.with_columns(
        pl.Series("b2b", z["row_b2b"].astype(float)), pl.Series("_season", z["row_season"])
    )
    return {
        "uidx": j["_u"].to_numpy(),
        "y": y,
        "gid": z["row_gid"].astype(str),
        "season": z["row_season"].astype(int),
        "meta": meta,
        "p_crps": z["P_crps"],
        "p_pit": z["P_pit"],
        "p_mean": z["P_mean"],
    }


def prop_rows(u: pl.DataFrame, stat: str) -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid, load_components

    comp = load_components(stat, LT_DIR)
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    keys = pl.DataFrame(
        {
            "game_id": comp["row_gid"].astype(str),
            "player_id": comp["row_pid"].astype(np.int64),
            "_i": np.arange(len(comp["row_gid"])),
        }
    )
    j = keys.join(u.with_row_index("_u"), on=["game_id", "player_id"], how="left").sort("_i")
    ok = j["_u"].is_not_null().to_numpy()
    uidx = j["_u"].fill_null(0).to_numpy()
    meta = u[uidx].with_columns(pl.Series("b2b", comp["row_b2b"].astype(float)))
    return {
        "grid": arm_grid("mixture", comp, pic, stat),
        "y": comp["row_y"].astype(float),
        "gid": comp["row_gid"].astype(str),
        "season": comp["row_season"].astype(int),
        "uidx": uidx,
        "ok": ok,
        "meta": meta,
    }


def minutes_scores_for(
    rows: dict[str, Any], u: pl.DataFrame, deltas: dict[str, np.ndarray]
) -> dict[str, dict[str, np.ndarray]]:
    """Per-arm (crps, pit, resid) of the minutes distribution trunc-normal(mu_arm, p_sigma). P and O1 included."""
    from research.eval.minutes_v2_eval import score_minutes
    from research.props.minutes_v2 import trunc_normal_quantiles

    idx = rows["uidx"]
    mu0 = u["p_mu"].to_numpy()[idx]
    sg = u["p_sigma"].to_numpy()[idx]
    y = rows["y"]
    out: dict[str, dict[str, np.ndarray]] = {}
    for arm, d in {"P": np.zeros(len(idx)), **{a: x[idx] for a, x in deltas.items()}}.items():
        mu = minutes_mu(mu0, d)
        if arm == "O1":
            mu = np.clip(y, 0.0, 48.0)
        s = score_minutes(trunc_normal_quantiles(mu, sg), y)
        out[arm] = {"crps": s["crps"], "pit": s["pit"], "resid": y - s["mean"]}
    return out


def prop_scores_for(
    rows: dict[str, Any], u: pl.DataFrame, stat: str, deltas: dict[str, np.ndarray]
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, np.ndarray]]:
    from research.eval import four_factors_eval as F

    idx, ok, y = rows["uidx"], rows["ok"], rows["y"]
    p_mu = u["p_mu"].to_numpy()[idx]
    minutes = u["minutes"].to_numpy().astype(float)[idx]
    facs = {"A0": np.ones(len(idx))}
    for arm, d in deltas.items():
        if arm == "O1":
            r = np.clip(minutes / np.maximum(p_mu, 0.5), R_LO, R_HI)
        else:
            r = prop_factor(p_mu, d[idx])
        facs[arm] = np.where(ok, r, 1.0)
    out: dict[str, dict[str, np.ndarray]] = {}
    qis: dict[str, np.ndarray] = {}
    for arm, r in facs.items():
        qi = scale_to_int(rows["grid"], r)
        qis[arm] = qi
        s = F.row_scores(qi, y, stat)
        out[arm] = {"crps": s["crps"], "pit": s["pit"], "resid": s["resid"]}
    return out, qis


# --------------------------------------------------------------------------- stages
def _screen_dropped() -> tuple[str, ...]:
    g = json.loads((OUT_DIR / "gate_frozen.json").read_text())
    return tuple(h for h, v in g["habit_screen"].items() if h in SCREENED and not v["keep"])


def _cached(tag: str, n: int, make: Any) -> np.ndarray:
    p = CACHE_DIR / f"{tag}.npy"
    if p.exists():
        a = np.load(p)
        if len(a) == n:
            return np.asarray(a)
    out = np.asarray(make())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(p, out)
    return out


def planted_checks(world: dict[str, Any], n_games: int = 24) -> dict[str, Any]:
    """Checks 2-3 on the real data at the team-game feature level: edit target games' box, minutes, starters, score and coach: every
    feature of those games is identical, the NEXT game of a team moves (non-inert control); delete every game dated >= D: every earlier row
    is byte-identical."""
    games, box, coach = (
        world["games"],
        world["box"],
        world["coach"].select("game_id", "team_id", "coach_key"),
    )
    base = team_game_features(games, coach, box)
    g24 = games.filter(pl.col("season") == 2024).sort("game_date")
    reg = g24.filter(pl.col("game_id").str.starts_with("002"))["game_id"].to_list()
    po = g24.filter(pl.col("game_id").str.starts_with("004"))["game_id"].to_list()
    tg = (
        reg[200 :: max(1, len(reg) // (n_games - 4))][: n_games - 4]
        + po[10 :: max(1, len(po) // 4)][:4]
    )
    box2 = box.with_columns(
        pl.when(pl.col("game_id").is_in(tg))
        .then(pl.col("minutes").reverse().over("game_id", "team_id"))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(pl.col("game_id").is_in(tg))
        .then(~pl.col("starter"))
        .otherwise(pl.col("starter"))
        .alias("starter"),
    )
    games2 = games.with_columns(
        pl.when(pl.col("game_id").is_in(tg))
        .then(pl.col("home_pts") + 30)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    coach2 = coach.with_columns(
        pl.when(pl.col("game_id").is_in(tg))
        .then(pl.lit("PLANTED"))
        .otherwise(pl.col("coach_key"))
        .alias("coach_key")
    )
    new = team_game_features(games2, coach2, box2)
    cols = [c for c in base.columns if c not in ("game_id", "team_id", "prev_coach")]
    key = ["game_id", "team_id"]
    a, b = base.sort(key), new.sort(key)

    def same(x: pl.DataFrame, y: pl.DataFrame) -> bool:
        return x.shape == y.shape and all(
            np.allclose(
                np.nan_to_num(x[c].to_numpy().astype(float), nan=-9e9),
                np.nan_to_num(y[c].to_numpy().astype(float), nan=-9e9),
                rtol=0.0,
                atol=1e-9,
            )
            for c in cols
        )

    first = games.filter(pl.col("game_id").is_in(tg))["game_date"].min()
    # a planted game legitimately feeds LATER planted games (career-pooled habits, standings), so the identity test covers the
    # planted games dated up to and including the EARLIEST one (as in TEAM_BUDGET)
    own = a.filter(pl.col("game_id").is_in(tg) & (pl.col("game_date") <= first))
    own2 = b.filter(pl.col("game_id").is_in(tg) & (pl.col("game_date") <= first))
    early = a.filter(pl.col("game_date") <= first)
    early2 = b.filter(pl.col("game_date") <= first)
    later = a.filter(pl.col("game_date") > first)
    later2 = b.filter(pl.col("game_date") > first)
    control = not same(later, later2)
    cut = games.filter(pl.col("season") == 2024).sort("game_date")["game_date"][len(reg) // 2]
    games3 = games.filter(pl.col("game_date") < cut)
    keep = games3["game_id"].to_list()
    fut = team_game_features(
        games3,
        coach.filter(pl.col("game_id").is_in(keep)),
        box.filter(pl.col("game_id").is_in(keep)),
    ).sort(key)
    prior = a.filter(pl.col("game_date") < cut)
    out = {
        "n_target_games": len(tg), "target_games_identical": bool(same(own, own2)), "up_to_first_plant_identical": bool(same(early, early2)),
        "control_later_games_move": bool(control), "planted_future_identical": bool(same(prior, fut)),
    }  # fmt: skip
    out["ok"] = bool(
        out["target_games_identical"]
        and out["up_to_first_plant_identical"]
        and out["control_later_games_move"]
        and out["planted_future_identical"]
    )
    return out


def audit_missingness(u: pl.DataFrame, rows: dict[str, Any]) -> dict[str, Any]:
    """G7: by realised-minutes bucket of the scored minutes rows, the rate of coach columns at their league fallback (no earlier game of the coach in
    the phase) and of prev_series_missing among playoff rows; the gap across buckets must be < 2 percentage points."""
    m = rows["meta"]
    y = rows["y"]
    out: dict[str, Any] = {"buckets": [f"{lo:g}-{hi:g}" for lo, hi in BUCKETS]}
    rates: dict[str, list[float | None]] = {
        "coach_fallback_reg": [],
        "coach_fallback_po": [],
        "prev_series_missing_po": [],
    }
    po = m["po"].to_numpy() > 0.5
    for lo, hi in BUCKETS:
        k = (y >= lo) & (y < hi)
        rates["coach_fallback_reg"].append(
            float((m["coach_n_reg"].to_numpy()[k] == 0).mean()) if k.any() else None
        )
        rates["coach_fallback_po"].append(
            float((m["coach_n_po"].to_numpy()[k] == 0).mean()) if k.any() else None
        )
        kp = k & po
        rates["prev_series_missing_po"].append(
            float((m["prev_series_missing"].to_numpy()[kp] > 0.5).mean()) if kp.any() else None
        )
    out["rates"] = rates
    # descriptive alternative reading (not the pass condition): the coach fallback rates among PLAYOFF rows only
    alt: dict[str, list[float | None]] = {"coach_fallback_po_on_po_rows": []}
    for lo, hi in BUCKETS:
        kp = (y >= lo) & (y < hi) & po
        alt["coach_fallback_po_on_po_rows"].append(
            float((m["coach_n_po"].to_numpy()[kp] == 0).mean()) if kp.any() else None
        )
    out["alt_reading_po_rows_only"] = {
        "rates": alt,
        "n_po_rows_by_bucket": [int(((y >= lo) & (y < hi) & po).sum()) for lo, hi in BUCKETS],
        "gaps": {
            k: max(x for x in v if x is not None) - min(x for x in v if x is not None)
            for k, v in alt.items()
        },
    }
    gaps = {
        k: (max(x for x in v if x is not None) - min(x for x in v if x is not None))
        for k, v in rates.items()
    }
    out["gaps"] = gaps
    out["pass_gap_lt_2pp"] = bool(all(g < AUDIT_GAP for g in gaps.values()))
    return out


def run_gate(out_dir: Path | None = None) -> dict[str, Any]:
    """Gates G1, G4-G6 re-run (same numbers as the committed gates.json), P reproduction, reliability screen, planted checks, G7 audit."""
    from research.archive.coach_phase import coach_phase_gates as G

    require_frozen()
    out_dir = out_dir or OUT_DIR
    res: dict[str, Any] = {"frozen_sha256": frozen_sha256(DOC)}
    ref = json.loads(GATES_JSON.read_text())
    import duckdb

    con = duckdb.connect("nba.duckdb", read_only=True)
    try:
        a0 = G.gate_a0()
        coach = G.gate_coach(con)
        sc = G.gate_series_and_clinch(con)
    finally:
        con.close()
    res["g1_a0"] = {"max_abs_diff": a0["max_abs_diff"], "pass": a0["pass"]}
    res["g4_coach"] = {
        "same_as_committed": json.loads(json.dumps(coach, default=float)) == ref["coach"],
        **{k: coach[k] for k in ("pass_report_season_coaches", "pass_select_season_coaches")},
    }
    res["g5_g6_series_clinch_same_as_committed"] = (
        json.loads(json.dumps(sc, default=float)) == ref["series_clinch"]
    )
    fr = build_all()
    u, world = fr["universe"], fr["world"]
    rows = minutes_rows(u)
    p = minutes_scores_for(rows, u, {})["P"]["crps"]
    res["p_reproduction"] = {
        "max_abs_diff": float(np.abs(p - rows["p_crps"]).max()),
        "n": int(len(p)),
    }
    res["p_reproduction"]["pass"] = bool(res["p_reproduction"]["max_abs_diff"] < 1e-6)
    inp = (
        team_game_inputs(world["box"], world["games"])
        .join(
            world["coach"].select("game_id", "team_id", "coach_key"),
            on=["game_id", "team_id"],
            how="left",
        )
        .join(world["games"].select("game_id", "season"), on="game_id")
    )
    res["habit_screen"] = reliability_screen(inp)
    res["planted"] = planted_checks(world)
    res["missingness_audit"] = audit_missingness(u, rows)
    res["gate_ok"] = bool(
        a0["pass"] and res["g4_coach"]["same_as_committed"] and res["g5_g6_series_clinch_same_as_committed"] and res["p_reproduction"]["pass"]
        and res["planted"]["ok"] and res["missingness_audit"]["pass_gap_lt_2pp"] and coach["pass_report_season_coaches"]
    )  # fmt: skip
    _write_json(out_dir / "gate_frozen.json", res)
    return res


def run_arms(out_dir: Path | None = None) -> dict[str, Any]:
    require_frozen()
    out_dir = out_dir or OUT_DIR
    gate = json.loads((out_dir / "gate_frozen.json").read_text())
    if not gate.get("gate_ok") or gate.get("frozen_sha256") != FROZEN_FULL:
        raise RuntimeError("the frozen gate has not passed: no arm may be fitted")
    dropped = _screen_dropped()
    fr = build_all()
    u = fr["universe"]
    n = u.height
    _log(f"universe: {n} played rows; dropped habits {dropped}")
    deltas: dict[str, np.ndarray] = {}
    for arm in ARMS_MODEL:
        deltas[arm] = _cached(f"delta_{arm}", n, lambda arm=arm: arm_delta(u, arm, dropped))
        _log(f"{arm} fitted")
    deltas["O1"] = u["minutes"].to_numpy().astype(float) - u["p_mu"].to_numpy()
    res: dict[str, Any] = {"rows": [], "ambiguities": AMBIGUITIES, "dropped_habits": list(dropped)}
    mr = minutes_rows(u)
    ms = minutes_scores_for(mr, u, deltas)
    res["rows"] += score_target(
        "minutes", ms, mr["gid"], mr["season"], slice_masks(mr["meta"]), "P", 0.0
    )
    _log("minutes scored")
    qis: dict[str, dict[str, np.ndarray]] = {}
    pr: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        pr[stat] = prop_rows(u, stat)
        sc, qi = prop_scores_for(pr[stat], u, stat, deltas)
        qis[stat] = qi
        pr[stat].pop("grid")
        res["rows"] += score_target(
            stat, sc, pr[stat]["gid"], pr[stat]["season"], slice_masks(pr[stat]["meta"]), "A0", 0.0
        )
        _log(f"{stat} scored")
    add_bh(res["rows"])
    res["verdict"] = evaluate(
        res,
        bool(gate["planted"]["ok"]),
        bool(gate["missingness_audit"]["pass_gap_lt_2pp"]),
        None,
        bool(gate["g4_coach"]["pass_report_season_coaches"]),
    )
    try:
        res["decision_score"] = decision_scores(pr, qis, u)
    except Exception as exc:  # secondary; cannot rescue or fail the rule
        res["decision_score"] = {"error": repr(exc)}
    _write_json(out_dir / "results.json", res)
    return res


def decision_scores(
    pr: dict[str, dict[str, Any]], qis: dict[str, dict[str, np.ndarray]], u: pl.DataFrame
) -> dict[str, Any]:
    from types import SimpleNamespace

    from nba.markets import decision_scoring as DS

    legs = DS.load_legs(PAIRS_DIR)
    phase_games = set(
        u.filter((pl.col("po") > 0.5) | ((pl.col("stage") == "002") & (pl.col("late15") > 0.5)))[
            "game_id"
        ].to_list()
    )
    worlds = {
        s: SimpleNamespace(
            keys=pl.DataFrame(
                {
                    "game_id": pr[s]["gid"],
                    "team_id": u["team_id"].to_numpy()[pr[s]["uidx"]],
                    "player_id": u["player_id"].to_numpy()[pr[s]["uidx"]],
                }
            )
        )
        for s in pr
    }
    out: dict[str, Any] = {}
    for arm in ("A0", "PH", "CP"):
        p = decision_p(legs, worlds, qis, arm)
        keep = ~np.isnan(p) & np.array([g in phase_games for g in legs["game_id"]])
        lg = {
            k: (v[keep] if isinstance(v, np.ndarray) and v.shape[:1] == keep.shape else v)
            for k, v in legs.items()
        }
        gm = {g: i for i, g in enumerate(np.unique(lg["game_id"]))}
        lg["gi"], lg["G"] = np.array([gm[g] for g in lg["game_id"]]), len(gm)
        o = DS.run_all(legs_with_p(lg, p[keep]))
        out[arm] = {
            "n_legs": int(keep.sum()),
            "cells": [
                {
                    "season": c["season"],
                    "stat": c["stat"],
                    "by_threshold": [
                        {"threshold": t["threshold"], "model": t["model"]}
                        for t in c["by_threshold"]
                    ],
                }
                for c in o["cells"]
            ],
        }
    return out


def phase_gain_2024(
    u: pl.DataFrame, mr: dict[str, Any], prs: dict[str, dict[str, Any]], delta: np.ndarray
) -> dict[str, float]:
    """Mean (arm - base) CRPS on the 2024 PHASE slice for minutes and each stat, for one delta vector (cheap: only that slice is scored)."""
    from research.eval import four_factors_eval as F
    from research.eval.minutes_v2_eval import score_minutes
    from research.props.minutes_v2 import trunc_normal_quantiles

    out: dict[str, float] = {}
    mk = slice_masks(mr["meta"])["phase"] & (mr["season"] == 2024)
    idx = mr["uidx"][mk]
    mu0, sg, y = u["p_mu"].to_numpy()[idx], u["p_sigma"].to_numpy()[idx], mr["y"][mk]
    a = score_minutes(trunc_normal_quantiles(minutes_mu(mu0, delta[idx]), sg), y)["crps"]
    b = score_minutes(trunc_normal_quantiles(mu0, sg), y)["crps"]
    out["minutes"] = float((a - b).mean())
    for stat, pr in prs.items():
        mk = slice_masks(pr["meta"])["phase"] & (pr["season"] == 2024) & pr["ok"]
        idx = pr["uidx"][mk]
        r = prop_factor(u["p_mu"].to_numpy()[idx], delta[idx])
        g = pr["grid"][mk]
        ya = pr["y"][mk]
        ca = F.row_scores(scale_to_int(g, r), ya, stat)["crps"]
        cb = F.row_scores(scale_to_int(g, np.ones(len(r))), ya, stat)["crps"]
        out[stat] = float((ca - cb).mean())
    return out


def run_attack(out_dir: Path | None = None) -> dict[str, Any]:
    require_frozen()
    out_dir = out_dir or OUT_DIR
    res = json.loads((out_dir / "results.json").read_text())
    dropped = _screen_dropped()
    fr = build_all()
    u = fr["universe"]
    mr = minutes_rows(u)
    prs = {s: prop_rows(u, s) for s in STATS}
    out: dict[str, Any] = {"shuffled": {}, "knockout": {}}
    for arm in ("PH", "CP"):
        d = _cached(
            f"delta_{arm}_shuffled",
            u.height,
            lambda arm=arm: arm_delta(u, arm, dropped, permute=True),
        )
        out["shuffled"][arm] = {t: {"gain": g} for t, g in phase_gain_2024(u, mr, prs, d).items()}
    full = phase_gain_2024(
        u, mr, prs, _cached("delta_CP", u.height, lambda: arm_delta(u, "CP", dropped))
    )
    ph = phase_gain_2024(
        u, mr, prs, _cached("delta_PH", u.height, lambda: arm_delta(u, "PH", dropped))
    )
    gain_full = {t: ph[t] - full[t] for t in full}  # positive = CP better than PH
    for h in HABITS:
        if h in dropped:
            continue
        d = _cached(
            f"delta_CP_KO_{h}", u.height, lambda h=h: arm_delta(u, "CP", dropped, knockout=h)
        )
        g = phase_gain_2024(u, mr, prs, d)
        out["knockout"][h] = {
            t: {
                "gain_drop": ph[t] - g[t],
                "gain_full": gain_full[t],
                "carries": carries(gain_full[t], ph[t] - g[t]),
            }
            for t in g
        }
    gate = json.loads((out_dir / "gate_frozen.json").read_text())
    res["verdict"] = evaluate(
        res,
        bool(gate["planted"]["ok"]),
        bool(gate["missingness_audit"]["pass_gap_lt_2pp"]),
        out,
        bool(gate["g4_coach"]["pass_report_season_coaches"]),
    )
    _write_json(out_dir / "attack.json", out)
    _write_json(out_dir / "results.json", res)
    return out


def run_frame() -> dict[str, Any]:
    """Smoke: build the universe and print its summary. No fit, no score."""
    require_frozen()
    fr = build_all()
    u = fr["universe"]
    s = {
        "rows": u.height,
        "by_season_stage": {
            f"{a}/{b}": n
            for a, b, n in u.group_by("season", "stage").len().sort("season", "stage").iter_rows()
        },
        "null_counts_feature_cols": {
            c: int(u[c].null_count())
            for c in (*BASE_COLS, *HABIT_COLS, "s10", "p_mu", "p_sigma")
            if u[c].null_count()
        },
        "po_rows_prev_series_missing": float(
            u.filter(pl.col("po") > 0.5)["prev_series_missing"].mean()
        ),
    }
    print(json.dumps(s, indent=1, default=float))
    return s


def render_report(out_dir: Path | None = None) -> str:
    out_dir = out_dir or OUT_DIR
    res = json.loads((out_dir / "results.json").read_text())
    ln = [
        "# COACH_PHASE results", "", f"Frozen sha256 {FROZEN_FULL[:8]} (docs/prereg/COACH_PHASE.md). Season 2025 never loaded.", "",
        "| target | season | arm | slice | n rows / games | CRPS | dCRPS [CI] | p_bh | MDE | cov80 | bias |", "|---|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for x in res["rows"]:
        if x["slice"] not in PRIMARY_SLICES:
            continue
        d = (
            f"{x['dcrps']:+.4f} [{x['ci_lo']:+.4f}, {x['ci_hi']:+.4f}]"
            if "ci_lo" in x
            else (f"{x['dcrps']:+.4f}" if "dcrps" in x else "")
        )
        ln.append(
            f"| {x['target']} | {x['season']} | {x['arm']} | {x['slice']} | {x['n_rows']} / {x['n_games']} | {x['crps']:.4f} | {d} | {x.get('p_bh', float('nan')):.3f} | {x.get('mde', float('nan')):.4f} | {x['cov80']:.3f} | {x['bias']:+.3f} |"
        )
    ln += [
        "",
        "## Verdict",
        "",
        "```json",
        json.dumps(res["verdict"], indent=1),
        "```",
        "",
        "## Readings of ambiguities in the frozen text",
        "",
    ]
    ln += [f"* {k}: {v}" for k, v in res["ambiguities"].items()]
    return "\n".join(ln)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["frame", "gate", "arms", "attack", "report", "run"])
    a = ap.parse_args(argv)
    if a.cmd == "report":
        REPORT_MD.write_text(render_report())
        print(REPORT_MD)
        return 0
    require_frozen()
    with heavy_lock():
        if a.cmd == "frame":
            run_frame()
            return 0
        if a.cmd in ("gate", "run") and not run_gate()["gate_ok"]:
            return 1
        if a.cmd in ("arms", "run"):
            run_arms()
        if a.cmd in ("attack", "run"):
            run_attack()
        if a.cmd == "run":
            REPORT_MD.write_text(render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
