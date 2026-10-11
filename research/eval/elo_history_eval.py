# ruff: noqa: E501
"""ELO_HISTORY_WINDOW evaluation (rule: docs/prereg/ELO_HISTORY_WINDOW.md).

    python -W ignore -m research.eval.elo_history_eval gate --doc <rule.md> --allow-draft
    python -W ignore -m research.eval.elo_history_eval smoke --doc ... --sha <64 hex>
    python -W ignore -m research.eval.elo_history_eval run   --doc ... --sha <64 hex>
    python -W ignore -m research.eval.elo_history_eval report --doc ... --sha <64 hex>

``--allow-draft`` (gate stage only) skips the hash equality check and records the computed hash, so
the minimum-data checks can be measured on a DRAFT (non-negotiable 8). Every other stage refuses
unless ``--sha`` equals the hash of everything above the ``=== RESULTS BELOW ===`` line.

Arms (all scored on the same keys: every 2023 and 2024 game with a result):
  E0  production GA-tuned MOV-Elo (configs/mov_elo_tuned.yaml), replay from 2022-10-18
  E1  same parameters, replay from 2019-10-22 (production season carry-over at each boundary)
  E2  GA re-tuned on 2019-20 .. 2022-23 (T182 budget: seed 0, maxiter 40, popsize 15);
      replayed like E1
  E3  E2 with bubble / no-fans games weighted 0.5 in the tuning objective (descriptive)
Sample weights exist ONLY in the E3 tuning objective; scoring is always unweighted.

Both databases are opened read-only (``connect_with_retry``; a lock held > 5 minutes prints LOCKED
and exits 3). ``nba.duckdb`` queries carry ``season <= 2024`` (asserted); season 2025 is never
loaded. The history database is limited to seasons 2019-2021. ``run`` takes
``data/ops/heavy.lock`` (retry 120 s, released in ``finally``). The GA result is checkpointed in
``data/elo_history_window/``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml
from scipy.optimize import differential_evolution

from nba.db.connect import connect_with_retry, is_lock_error
from nba.eval.injury_elo_eval import EARLY_GAMES, game_numbers
from nba.ingest.era_flags import compute_era_flags
from nba.models.injury_elo import ELO_TO_LOGIT, sequential_elo_logits, sigmoid
from nba.props.metrics import _clustered_boot_means
from nba.truth.metrics import brier_score, calibration_curve, log_loss
from research.eval.ga_tune import DEFAULT_BOUNDS, PARAM_NAMES, params_from_vector

DEFAULT_DOC = Path("docs/prereg/ELO_HISTORY_WINDOW.md")
PREREG = DEFAULT_DOC
MARKER = "=== RESULTS BELOW ==="
OUT_DIR = Path("reports/prereg_elo_history")
REPORT_PATH = Path("reports/prereg_elo_history.md")
CACHE = Path("data/elo_history_window")
LOCK = Path("data/ops/heavy.lock")
MOV_CONFIG = Path("configs/mov_elo_tuned.yaml")
HIST_GAMES_DIR = Path("data/history/games")
GAME_ROWS_DIR = Path("reports/odds_history")  # production injury-Elo v2 OOF p (reference only)

MAX_SEASON = 2024  # season 2025 is the frozen holdout: never loaded
HIST_SEASONS = (2019, 2020, 2021)
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEASONS = (SELECT_SEASON, REPORT_SEASON)
E0_START = "2022-10-18"
E1_START = "2019-10-22"
TUNE_SEASONS = (2019, 2020, 2021, 2022)  # 2019-20 .. 2022-23

# T182 / registry run (registry_store/rung0_injury_elo/v2/results.json, key mov_elo): pooled 2022-24
STORED_N = 3951
STORED_LOGLOSS = 0.6245452958318027
STORED_BRIER = 0.2180259696470721
STORED_ECE = 0.0166039727047398
REPRO_TOL = 1e-6
HIST_N = 3637

# GA budget = T182 (configs/mov_elo_tuned.yaml header)
GA_SEED, GA_MAXITER, GA_POPSIZE = 0, 40, 15
N_BOOT = 2000
BOOT_SEED = 0
BLOCK_DAYS = 14
FLOOR = -0.004
ECE_TOL = 0.005
EARLY_TOL = 0.01
CLOSE_ELO = 50.0
ARMS = ("E0", "E1", "E2", "E3")
FAMILY = ("E1", "E2")
BUBBLE_WEIGHT = 0.5
INFEASIBLE = 1.0e6


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze + lock


def frozen_sha(path: Path | None = None) -> str:
    """sha256 of everything above the marker line (the awk command in the doc header)."""
    h = hashlib.sha256()
    for line in (path or PREREG).read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        h.update(line.encode())
    return h.hexdigest()


def configure(doc: Path) -> None:
    global PREREG
    PREREG = doc


def require_frozen(expected: str | None, allow_draft: bool = False) -> str:
    sha = frozen_sha(PREREG)
    if allow_draft:
        return sha
    if not expected or len(expected) != 64:
        raise SystemExit("STOP: --sha <64 hex> (the committed freeze hash) is required")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


class HeavyLock:
    """``mkdir data/ops/heavy.lock`` with retry every 120 s; ``rmdir`` on exit."""

    def __enter__(self) -> HeavyLock:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                LOCK.mkdir()
                break
            except FileExistsError:
                _log("heavy.lock held; retry in 120 s")
                time.sleep(120)
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(OSError):
            LOCK.rmdir()


class Locked(Exception):
    """A database stayed write-locked past the wait limit."""


# --------------------------------------------------------------------------- data

GAME_COLS = ("game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts")


def _open_ro(path: str | Path, wait_s: float) -> duckdb.DuckDBPyConnection:
    try:
        return connect_with_retry(path, read_only=True, max_wait_s=wait_s)
    except duckdb.IOException as exc:
        if is_lock_error(exc):
            raise Locked(f"{path}: {exc}") from exc
        raise


def _games_frame(df: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns(pl.col("game_id").cast(pl.Utf8))
    return df.sort(["game_date", "game_id"])


def assert_seasons(df: pl.DataFrame, max_season: int) -> None:
    if df.height and int(df["season"].max()) > max_season:  # type: ignore[arg-type]
        raise ValueError(f"season > {max_season} present: the frozen holdout must not be loaded")


def load_side(path: str | Path, seasons: tuple[int, ...], wait_s: float) -> dict[str, pl.DataFrame]:
    """Games (scores may be null; ``season`` limited to ``seasons``) and era flags, read-only."""
    lo, hi = min(seasons), max(seasons)
    assert hi <= MAX_SEASON
    con = _open_ro(path, wait_s)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season >= {lo} AND season <= {hi}"
        ).pl()
        flags = con.execute(
            "SELECT game_id, season, covid_bubble, no_fans, limited_fans, shortened_season "
            f"FROM game_era_flags WHERE season >= {lo} AND season <= {hi}"
        ).pl()
    finally:
        con.close()
    assert_seasons(games, MAX_SEASON)
    assert_seasons(flags, MAX_SEASON)
    return {
        "games": _games_frame(games),
        "flags": flags.with_columns(pl.col("game_id").cast(pl.Utf8)),
    }


def load_world(
    db: str = "nba.duckdb",
    history_db: str = "data/history/nba_history.duckdb",
    wait_s: float = 300.0,
) -> dict[str, pl.DataFrame]:
    cur = load_side(db, (2022, 2023, 2024), wait_s)
    hist = load_side(history_db, HIST_SEASONS, wait_s)
    return {
        "cur": cur["games"],
        "hist": hist["games"],
        "cur_flags": cur["flags"],
        "hist_flags": hist["flags"],
    }


def with_result(games: pl.DataFrame) -> pl.DataFrame:
    """Games with both scores (the production ``load_games_frame`` filter) and the label ``y``."""
    g = games.filter(pl.col("home_pts").is_not_null() & pl.col("away_pts").is_not_null())
    return g.with_columns((pl.col("home_pts") > pl.col("away_pts")).cast(pl.Int64).alias("y"))


def load_params(path: Path = MOV_CONFIG) -> dict[str, float]:
    cfg = yaml.safe_load(path.read_text())
    return {k: float(cfg[k]) for k in PARAM_NAMES}


# --------------------------------------------------------------------------- replay


def production_logits(games: pl.DataFrame, params: dict[str, float]) -> np.ndarray:
    """Pre-game home logit of every game in row order: the production replay, unmodified."""
    return np.asarray(sequential_elo_logits(games, params), dtype=float)


def prepare_arrays(games: pl.DataFrame) -> dict[str, Any]:
    """Plain-Python arrays for the fast replay (rows sorted by date, game_id)."""
    g = games.sort(["game_date", "game_id"])
    dates = g["game_date"].to_list()
    return {
        "game_id": g["game_id"].to_list(),
        "date": dates,
        "season": g["season"].to_list(),
        "home": [int(x) for x in g["home_team"].to_list()],
        "away": [int(x) for x in g["away_team"].to_list()],
        "hp": [float(x) for x in g["home_pts"].to_list()],
        "ap": [float(x) for x in g["away_pts"].to_list()],
    }


def fast_logits(arr: dict[str, Any], params: dict[str, float]) -> np.ndarray:
    """Same arithmetic as ``MovEloBaseline`` / ``sequential_elo_logits`` without the per-call
    overhead (the GA calls it ~3,000 times). Equality with the production replay is a gate test."""
    k, ha, co = params["k_factor"], params["home_advantage_elo"], params["season_carryover"]
    mc, md = params["mov_c"], params["mov_div"]
    init = 1500.0
    home, away, hp, ap = arr["home"], arr["away"], arr["hp"], arr["ap"]
    dates, seasons = arr["date"], arr["season"]
    n = len(home)
    out = np.zeros(n, dtype=float)
    rating: dict[int, float] = {}
    cur_season: object = None
    i = 0
    while i < n:
        j = i
        while j < n and dates[j] == dates[i]:
            j += 1
        for r in range(i, j):
            diff = rating.get(home[r], init) + ha - rating.get(away[r], init)
            out[r] = diff * ELO_TO_LOGIT
        for r in range(i, j):
            if cur_season is not None and seasons[r] != cur_season:
                for t in rating:
                    rating[t] = co * rating[t] + (1.0 - co) * init
            cur_season = seasons[r]
            h, a = home[r], away[r]
            hr, ar = rating.get(h, init), rating.get(a, init)
            diff = (hr + ha) - ar
            e = min(max(-diff / 400.0, -200.0), 200.0)
            p_home = 1.0 / (1.0 + 10.0**e)
            actual = 1.0 if hp[r] > ap[r] else 0.0
            wd = diff if actual >= 0.5 else -diff
            mult = math.log(abs(hp[r] - ap[r]) + 1.0) * (mc / (wd * md + mc))
            upd = k * mult
            upd *= actual - p_home
            rating[h] = hr + upd
            rating[a] = ar - upd
        i = j
    return out


# --------------------------------------------------------------------------- arms + tuning


@dataclass(frozen=True)
class ArmSpec:
    name: str
    start: str  # replay start (ISO date)
    tuned: bool  # parameters re-tuned by the GA
    weighted: bool  # bubble / no-fans down-weighting in the TUNING objective only


ARM_SPECS: dict[str, ArmSpec] = {
    "E0": ArmSpec("E0", E0_START, False, False),
    "E1": ArmSpec("E1", E1_START, False, False),
    "E2": ArmSpec("E2", E1_START, True, False),
    "E3": ArmSpec("E3", E1_START, True, True),
}


def tuning_weights(game_ids: list[str], flags: pl.DataFrame, weighted: bool) -> np.ndarray:
    """Per-game objective weights. Always ones except for the weighted (E3) arm."""
    w = np.ones(len(game_ids), dtype=float)
    if not weighted:
        return w
    hit = flags.filter(pl.col("covid_bubble").fill_null(False) | pl.col("no_fans").fill_null(False))
    bad = set(hit["game_id"].to_list())
    return np.array([BUBBLE_WEIGHT if g in bad else 1.0 for g in game_ids], dtype=float)


def combined_games(world: dict[str, pl.DataFrame], start: str) -> pl.DataFrame:
    """History + current games with a result, from ``start`` on (E0: current only)."""
    cur = with_result(world["cur"])
    if start == E0_START:
        return cur.filter(pl.col("game_date") >= pl.lit(start).str.to_date())
    hist = with_result(world["hist"])
    both = pl.concat([hist.select(cur.columns), cur], how="vertical")
    both = both.sort(["game_date", "game_id"])
    return both.filter(pl.col("game_date") >= pl.lit(start).str.to_date())


def tune_frame(world: dict[str, pl.DataFrame]) -> pl.DataFrame:
    g = combined_games(world, E1_START)
    return g.filter(pl.col("season").is_in(list(TUNE_SEASONS)))


def tuning_objective(
    arr: dict[str, Any], y: np.ndarray, weights: np.ndarray, first_date_mask: np.ndarray
) -> Any:
    """Walk-forward OOF (weighted) log loss of a parameter vector; the first date is not scored,
    as in the fold harness. Same clip as ``nba.truth.metrics.log_loss``."""
    from nba.truth.metrics import EPS

    def objective(x: np.ndarray) -> float:
        try:
            logit = fast_logits(arr, params_from_vector(x))
        except (ZeroDivisionError, OverflowError, ValueError):
            return INFEASIBLE
        p = np.clip(sigmoid(logit), EPS, 1 - EPS)
        ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
        keep = ~first_date_mask
        loss = float(np.sum(weights[keep] * ll[keep]) / np.sum(weights[keep]))
        return loss if np.isfinite(loss) else INFEASIBLE

    return objective


def run_ga(
    tune: pl.DataFrame,
    weights: np.ndarray,
    seed: int = GA_SEED,
    maxiter: int = GA_MAXITER,
    popsize: int = GA_POPSIZE,
    label: str = "GA",
) -> tuple[dict[str, float], float]:
    """Differential evolution with the T182 settings (research/eval/ga_tune.py)."""
    arr = prepare_arrays(tune)
    y = np.array([1.0 if h > a else 0.0 for h, a in zip(arr["hp"], arr["ap"], strict=True)])
    first = np.array([d == arr["date"][0] for d in arr["date"]])
    obj = tuning_objective(arr, y, weights, first)
    gen = {"n": 0}

    def cb(xk: np.ndarray, convergence: float = 0.0) -> bool:
        gen["n"] += 1
        if gen["n"] % 5 == 0 or gen["n"] == 1:
            _log(f"{label} generation {gen['n']}/{maxiter} best={obj(xk):.5f}")
        return False

    res = differential_evolution(
        obj,
        [DEFAULT_BOUNDS[k] for k in PARAM_NAMES],
        seed=seed,
        maxiter=maxiter,
        popsize=popsize,
        polish=False,
        updating="deferred",
        workers=1,
        init="latinhypercube",
        callback=cb,
    )
    return params_from_vector(np.asarray(res.x)), float(res.fun)


def tuned_params(
    name: str, world: dict[str, pl.DataFrame], cache: Path | None = None, **ga_kw: Any
) -> dict[str, Any]:
    """E2/E3 parameters, checkpointed (resume on rerun)."""
    spec = ARM_SPECS[name]
    cache = cache or CACHE
    f = cache / f"ga_{name}.json"
    if f.exists():
        _log(f"{name}: GA checkpoint loaded")
        out: dict[str, Any] = json.loads(f.read_text())
        return out
    tune = tune_frame(world)
    flags = pl.concat([world["hist_flags"], world["cur_flags"]], how="vertical")
    w = tuning_weights(
        tune.sort(["game_date", "game_id"])["game_id"].to_list(), flags, spec.weighted
    )
    t0 = time.time()
    params, loss = run_ga(tune, w, label=name, **ga_kw)
    out = {
        "params": params,
        "tune_loss": loss,
        "n_tune_games": tune.height,
        "seconds": time.time() - t0,
    }
    cache.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, indent=1))
    return out


# --------------------------------------------------------------------------- evaluation keys


def eval_keys(cur: pl.DataFrame) -> pl.DataFrame:
    """Every 2023 and 2024 game with a result (playoffs included, as the stored OOF is)."""
    return (
        with_result(cur)
        .filter(pl.col("season").is_in(list(SEASONS)))
        .sort(["game_date", "game_id"])
    )


def arm_probs(
    name: str,
    world: dict[str, pl.DataFrame],
    params: dict[str, float],
) -> pl.DataFrame:
    """Pre-game home win probability of ``name`` on the evaluation keys (game_id, p, elo_logit)."""
    games = combined_games(world, ARM_SPECS[name].start)
    logit = production_logits(games, params)
    frame = games.select("game_id").with_columns(pl.Series("elo_logit", logit))
    keys = eval_keys(world["cur"]).select("game_id")
    out = keys.join(frame, on="game_id", how="left")
    return out.with_columns(pl.Series("p", np.clip(sigmoid(out["elo_logit"].to_numpy()), 0.0, 1.0)))


def assert_same_keys(frames: dict[str, pl.DataFrame]) -> int:
    ref: list[str] | None = None
    for name, f in frames.items():
        ids = f["game_id"].to_list()
        if f["p"].is_null().any():
            raise ValueError(f"{name}: null probabilities on the evaluation keys")
        if ref is None:
            ref = ids
        elif ids != ref:
            raise ValueError(f"{name}: evaluation keys differ from the first arm")
    return len(ref or [])


# --------------------------------------------------------------------------- gate


def check_history_complete(
    world: dict[str, pl.DataFrame], games_dir: Path = HIST_GAMES_DIR
) -> dict[str, Any]:
    """Check 1: history games vs the cached list; scores non-null."""
    h = world["hist"]
    ids = h["game_id"].to_list()
    cached: set[str] = set()
    if games_dir.exists():
        for f in sorted(games_dir.glob("*.parquet")):
            cached |= set(pl.read_parquet(f)["game_id"].cast(pl.Utf8).to_list())
    scored = h.filter(pl.col("home_pts").is_not_null() & pl.col("away_pts").is_not_null())
    ties = int((scored["home_pts"] == scored["away_pts"]).sum())
    detail = {
        "db_games": len(ids),
        "unique_ids": len(set(ids)),
        "cached_list": len(cached),
        "db_minus_cached": len(set(ids) - cached),
        "cached_minus_db": len(cached - set(ids)),
        "scores_non_null": scored.height,
        "ties": ties,
        "by_season": {str(s): int((h["season"] == s).sum()) for s in HIST_SEASONS},
    }
    ok = bool(
        detail["db_games"] == HIST_N
        and detail["unique_ids"] == HIST_N
        and detail["cached_list"] == HIST_N
        and detail["db_minus_cached"] == 0
        and detail["cached_minus_db"] == 0
        and detail["scores_non_null"] == HIST_N
        and ties == 0
    )
    return {"ok": ok, "detail": detail}


def pooled_scores(logit: np.ndarray, games: pl.DataFrame) -> dict[str, Any]:
    """The registry scoring: seasons 2022-24, first date dropped, clip(sigmoid(logit))."""
    frame = games.with_columns(pl.Series("elo_logit", logit)).sort(["game_date", "game_id"])
    first = frame["game_date"].min()
    keep = (frame["season"].is_in([2022, 2023, 2024]) & (frame["game_date"] > first)).to_numpy()
    y = frame["y"].to_numpy().astype(float)[keep]
    p = np.clip(sigmoid(frame["elo_logit"].to_numpy()), 0.0, 1.0)[keep]
    out: dict[str, Any] = {
        "n": int(keep.sum()),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score(y, p)),
        "ece": float(calibration_curve(y, p).ece),
    }
    for s in SEASONS:
        m = (frame["season"].to_numpy()[keep]) == s
        out[f"log_loss_{s}"] = float(log_loss(y[m], p[m]))
        out[f"n_{s}"] = int(m.sum())
    return out


def registry_excluded_ids(cur: pl.DataFrame, rows_dir: Path = GAME_ROWS_DIR) -> list[str]:
    """Current 2023/2024 games that the registry-era OOF run did not have (not in its
    ``game_rows_<season>.parquet`` key set): added to ``games`` after 2026-10-08."""
    out: list[str] = []
    for s in SEASONS:
        f = rows_dir / f"game_rows_{s}.parquet"
        if not f.exists():
            continue
        have = set(pl.read_parquet(f)["game_id"].cast(pl.Utf8).to_list())
        g = with_result(cur).filter(pl.col("season") == s)
        out += [x for x in g["game_id"].to_list() if x not in have]
    return sorted(out)


def check_e0_repro(world: dict[str, pl.DataFrame], params: dict[str, float]) -> dict[str, Any]:
    """Check 2: E0 reproduces the stored pooled MOV-Elo OOF (2022-24; per-season values are not
    stored anywhere, so the per-season 2023/2024 numbers below are new measurements, not tests).

    ``ok`` is the check as drafted (current data, 1e-6). ``ok_registry_keys`` repeats it on the
    registry run's game set (games added since are dropped from the replay), which separates a
    wrong engine from a changed ``games`` table."""
    g = combined_games(world, E0_START)
    logit = production_logits(g, params)
    sc = pooled_scores(logit, g)
    arr = prepare_arrays(g)
    fast_diff = float(np.max(np.abs(fast_logits(arr, params) - logit)))
    g1 = combined_games(world, E1_START)
    l1 = production_logits(g1, params)
    fast_diff_e1 = float(np.max(np.abs(fast_logits(prepare_arrays(g1), params) - l1)))
    excl = registry_excluded_ids(world["cur"])
    gr = g.filter(~pl.col("game_id").is_in(excl))
    scr = pooled_scores(production_logits(gr, params), gr)

    def _match(m: dict[str, Any]) -> bool:
        return bool(
            m["n"] == STORED_N
            and abs(m["log_loss"] - STORED_LOGLOSS) <= REPRO_TOL
            and abs(m["brier"] - STORED_BRIER) <= REPRO_TOL
            and abs(m["ece"] - STORED_ECE) <= REPRO_TOL
        )

    engine_ok = bool(fast_diff <= 1e-9 and fast_diff_e1 <= 1e-9)
    return {
        "ok": bool(_match(sc) and engine_ok),
        "ok_registry_keys": bool(_match(scr) and engine_ok),
        "detail": {
            "stored": {
                "n": STORED_N,
                "log_loss": STORED_LOGLOSS,
                "brier": STORED_BRIER,
                "ece": STORED_ECE,
                "source": "registry_store/rung0_injury_elo/v2/results.json: mov_elo (pooled 2022-24)",
                "per_season_stored": False,
            },
            "measured_current_data": sc,
            "abs_diff_log_loss": abs(sc["log_loss"] - STORED_LOGLOSS),
            "abs_diff_brier": abs(sc["brier"] - STORED_BRIER),
            "abs_diff_ece": abs(sc["ece"] - STORED_ECE),
            "games_added_since_registry_run": excl,
            "measured_registry_keys": scr,
            "abs_diff_log_loss_registry_keys": abs(scr["log_loss"] - STORED_LOGLOSS),
            "abs_diff_brier_registry_keys": abs(scr["brier"] - STORED_BRIER),
            "abs_diff_ece_registry_keys": abs(scr["ece"] - STORED_ECE),
            "fast_vs_production_max_abs_E0": fast_diff,
            "fast_vs_production_max_abs_E1": fast_diff_e1,
        },
    }


def _plant(games: pl.DataFrame, cutoff: Any, rng: np.random.Generator) -> pl.DataFrame:
    """Replace the scores of every game dated on/after ``cutoff`` (including its own date)."""
    late = (games["game_date"] >= cutoff).to_numpy()
    hp = games["home_pts"].to_numpy().copy()
    ap = games["away_pts"].to_numpy().copy()
    hp[late] = rng.integers(60, 160, int(late.sum()))
    ap[late] = rng.integers(60, 160, int(late.sum()))
    return games.with_columns(pl.Series("home_pts", hp), pl.Series("away_pts", ap))


def planted_future(
    games: pl.DataFrame, params: dict[str, float], n_probe: int = 6, seed: int = 7
) -> dict[str, Any]:
    """Check 3: scores edited on/after an evaluated game's date change no logit used for any game
    dated on/before it (so neither its own score nor later scores feed its rating); a positive
    control shows the same edit does move later games."""
    rng = np.random.default_rng(seed)
    base = production_logits(games, params)
    dates = games["game_date"].to_numpy()
    uniq = np.unique(dates)
    picks = rng.choice(uniq[5:-5], size=min(n_probe, len(uniq) - 10), replace=False)
    unchanged: list[bool] = []
    moved: list[bool] = []
    for d in picks:
        cutoff = games["game_date"][int(np.searchsorted(dates, d))]
        edited = _plant(games, cutoff, rng)
        new = production_logits(edited, params)
        before = dates <= d
        unchanged.append(bool(np.array_equal(base[before], new[before])))
        after = dates > d
        moved.append(bool(after.any() and not np.array_equal(base[after], new[after])))
    # a single edited score changes its own game's logit not at all, and later logits do move
    return {
        "ok": bool(all(unchanged) and all(moved)),
        "probes": len(picks),
        "prior_logits_byte_identical": unchanged,
        "positive_control_later_logits_move": moved,
    }


def check_era_flags(world: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Check 4: era flags for 100% of history games, equal to the documented rules."""
    h, f = world["hist"], world["hist_flags"]
    have = set(f["game_id"].to_list())
    missing = [g for g in h["game_id"].to_list() if g not in have]
    cols = ["covid_bubble", "no_fans", "limited_fans", "shortened_season"]
    nulls = {c: int(f[c].is_null().sum()) for c in cols}
    exp = compute_era_flags(h.select("game_id", "game_date", "season", "home_team"))
    j = f.join(exp, on="game_id", how="inner", suffix="_rule")
    disagree = 0
    for c in cols:
        disagree += int((j[c].fill_null(False) != j[f"{c}_rule"].fill_null(False)).sum())
    cur_have = set(world["cur_flags"]["game_id"].to_list())
    cur_missing = sum(1 for g in world["cur"]["game_id"].to_list() if g not in cur_have)
    bub = f.filter(pl.col("covid_bubble").fill_null(False) | pl.col("no_fans").fill_null(False))
    detail = {
        "history_games": h.height,
        "history_flag_rows": f.height,
        "missing": len(missing),
        "null_cells": nulls,
        "rule_disagreements": disagree,
        "e3_downweighted_games": bub.height,
        "limited_fans_games": int(f["limited_fans"].fill_null(False).sum()),
        "shortened_games": int(f["shortened_season"].fill_null(False).sum()),
        "current_games_without_flags_info": cur_missing,
    }
    ok = bool(
        h.height == HIST_N
        and not missing
        and f.height == h.height
        and all(v == 0 for v in nulls.values())
        and disagree == 0
    )
    return {"ok": ok, "detail": detail}


def gate_checks(world: dict[str, pl.DataFrame], params: dict[str, float]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    res["1_history_complete_scores_non_null"] = check_history_complete(world)
    res["2_e0_reproduces_stored_oof"] = check_e0_repro(world, params)
    g_e0 = combined_games(world, E0_START)
    g_e1 = combined_games(world, E1_START)
    pf0 = planted_future(g_e0, params)
    pf1 = planted_future(g_e1, params)
    res["3_planted_future"] = {"ok": bool(pf0["ok"] and pf1["ok"]), "E0": pf0, "E1": pf1}
    res["4_era_flags_complete"] = check_era_flags(world)
    res["all_ok"] = bool(all(v["ok"] for k, v in res.items() if isinstance(v, dict)))
    res["all_ok_if_check2_on_registry_keys"] = bool(
        res["1_history_complete_scores_non_null"]["ok"]
        and res["2_e0_reproduces_stored_oof"]["ok_registry_keys"]
        and res["3_planted_future"]["ok"]
        and res["4_era_flags_complete"]["ok"]
    )
    return res


# --------------------------------------------------------------------------- analysis


def _per_game_ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    return np.asarray(log_loss(y[:, None], p[:, None], axis=1))


def _ci(
    delta: np.ndarray, cluster: np.ndarray, seed: int = BOOT_SEED
) -> tuple[float, float, float]:
    b = _clustered_boot_means(delta, cluster, N_BOOT, np.random.default_rng(seed))
    return float(delta.mean()), float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))


def _boot_p(delta: np.ndarray, cluster: np.ndarray, seed: int = BOOT_SEED) -> float:
    b = _clustered_boot_means(delta, cluster, N_BOOT, np.random.default_rng(seed))
    p = 2.0 * min(float(np.mean(b >= 0.0)), float(np.mean(b <= 0.0)))
    return float(min(max(p, 1.0 / (N_BOOT + 1)), 1.0))


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def date_blocks(dates: np.ndarray) -> np.ndarray:
    d0 = dates.min()
    return np.asarray(((dates - d0) / np.timedelta64(1, "D")).astype(int) // BLOCK_DAYS)


def analyze(
    keys: pl.DataFrame, probs: dict[str, np.ndarray], first_game_no: np.ndarray
) -> dict[str, Any]:
    """Paired deltas (arm - E0) per season, game and date-block clustered; BH over {E1,E2}."""
    y = keys["y"].to_numpy().astype(float)
    season = keys["season"].to_numpy()
    gid = keys["game_id"].to_numpy()
    dates = keys["game_date"].to_numpy().astype("datetime64[D]")
    ll = {a: _per_game_ll(y, p) for a, p in probs.items()}
    e0_logit = np.log(probs["E0"] / (1 - probs["E0"]))
    slices = {
        "first15": first_game_no <= EARLY_GAMES,
        "rest": first_game_no > EARLY_GAMES,
        "home_favourite": probs["E0"] >= 0.5,
        "close(|elo diff|<50)": np.abs(e0_logit / ELO_TO_LOGIT) < CLOSE_ELO,
    }
    rows: list[dict[str, Any]] = []
    srows: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        pv: dict[str, float] = {}
        idx = {}
        for a in ARMS:
            p = probs[a][sm]
            cur: dict[str, Any] = {
                "season": s,
                "arm": a,
                "n_games": int(sm.sum()),
                "logloss": float(ll[a][sm].mean()),
                "brier": float(brier_score(y[sm], p)),
                "ece": float(calibration_curve(y[sm], p).ece),
                "reliability": {
                    "mean_pred": calibration_curve(y[sm], p).bin_mean_pred,
                    "observed": calibration_curve(y[sm], p).bin_observed_rate,
                    "count": calibration_curve(y[sm], p).bin_count,
                },
            }
            if a == "E0":
                cur.update(
                    dlogloss=0.0,
                    ci_lo=0.0,
                    ci_hi=0.0,
                    ci_block_lo=0.0,
                    ci_block_hi=0.0,
                    p=float("nan"),
                    p_bh=float("nan"),
                    dece=0.0,
                )
            else:
                d = ll[a][sm] - ll["E0"][sm]
                pt, lo, hi = _ci(d, gid[sm])
                _, blo, bhi = _ci(d, date_blocks(dates[sm]))
                cur.update(
                    dlogloss=pt,
                    ci_lo=lo,
                    ci_hi=hi,
                    ci_block_lo=blo,
                    ci_block_hi=bhi,
                    p=_boot_p(d, gid[sm]),
                    p_bh=float("nan"),
                    dece=cur["ece"] - rows_e0_ece(rows, s),
                )
                if a in FAMILY:
                    pv[a] = cur["p"]
            rows.append(cur)
            idx[a] = len(rows) - 1
        adj = bh_adjust(np.array([pv[a] for a in FAMILY]))
        for a, ap in zip(FAMILY, adj, strict=True):
            rows[idx[a]]["p_bh"] = float(ap)
        for name, mk in slices.items():
            m = mk & sm
            for a in ARMS[1:]:
                d = ll[a][m] - ll["E0"][m]
                pt, lo, hi = _ci(d, gid[m])
                srows.append(
                    {
                        "season": s,
                        "slice": name,
                        "arm": a,
                        "n": int(m.sum()),
                        "dlogloss": pt,
                        "ci_lo": lo,
                        "ci_hi": hi,
                    }
                )
    return {"table": rows, "slices": srows}


def rows_e0_ece(rows: list[dict[str, Any]], s: int) -> float:
    return float(next(r["ece"] for r in rows if r["season"] == s and r["arm"] == "E0"))


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    """The pass rule: every condition on 2023 (select) AND 2024 (confirm), per arm in the family."""
    out: dict[str, Any] = {}
    for a in FAMILY:
        per: dict[str, Any] = {}
        for s in SEASONS:
            r = next(x for x in res["table"] if x["season"] == s and x["arm"] == a)
            f15 = next(
                x
                for x in res["slices"]
                if x["season"] == s and x["arm"] == a and x["slice"] == "first15"
            )
            checks = {
                "floor": r["dlogloss"] <= FLOOR,
                "ci_upper_lt_0": r["ci_hi"] < 0,
                "bh_p_lt_0.05": r["p_bh"] < 0.05,
                "block_ci_upper_lt_0": r["ci_block_hi"] < 0,
                "ece_not_worse": r["dece"] <= ECE_TOL,
                "first15_not_worse": f15["dlogloss"] <= EARLY_TOL,
            }
            per[str(s)] = {"checks": checks, "pass": all(checks.values())}
        out[a] = {"seasons": per, "pass": all(v["pass"] for v in per.values())}
    sel = {a: out[a]["seasons"][str(SELECT_SEASON)]["pass"] for a in FAMILY}
    out["closed_on_select_season"] = not any(sel.values())
    out["any_arm_passes"] = any(out[a]["pass"] for a in FAMILY)
    return out


# --------------------------------------------------------------------------- run / report


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def reference_injury_elo(keys: pl.DataFrame) -> dict[str, Any]:
    """Production injury-Elo v2 OOF log loss on the same games (not an arm)."""
    out: dict[str, Any] = {}
    for s in SEASONS:
        f = GAME_ROWS_DIR / f"game_rows_{s}.parquet"
        if not f.exists():
            continue
        r = pl.read_parquet(f).select("game_id", pl.col("p").alias("p_ref"))
        j = keys.filter(pl.col("season") == s).join(r, on="game_id", how="left")
        ok = j.filter(pl.col("p_ref").is_not_null())
        y, p = ok["y"].to_numpy().astype(float), ok["p_ref"].to_numpy()
        out[str(s)] = {"n": ok.height, "n_keys": j.height, "logloss": float(log_loss(y, p))}
    return out


def do_gate(
    world: dict[str, pl.DataFrame], params: dict[str, float], sha: str, draft: bool
) -> dict[str, Any]:
    res = gate_checks(world, params)
    res["prereg_sha256"] = sha
    res["allow_draft"] = draft
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gate.json").write_text(json.dumps(res, indent=1, default=float))
    for k, v in res.items():
        if isinstance(v, dict) and "ok" in v:
            _log(f"gate {k}: ok={v['ok']}")
    return res


def do_run(world: dict[str, pl.DataFrame], gate: dict[str, Any], sha: str) -> dict[str, Any]:
    if not gate["all_ok"]:
        raise SystemExit("STOP: gate failed; no model result")
    base = load_params()
    keys = eval_keys(world["cur"])
    params: dict[str, dict[str, float]] = {"E0": base, "E1": base}
    ga: dict[str, Any] = {}
    for name in ("E2", "E3"):
        ga[name] = tuned_params(name, world)
        params[name] = ga[name]["params"]
    frames = {a: arm_probs(a, world, params[a]) for a in ARMS}
    n = assert_same_keys(frames)
    _log(f"identical evaluation keys across arms: n={n}")
    probs = {a: frames[a]["p"].to_numpy() for a in ARMS}
    gn = game_numbers(world["cur"])
    first = np.array([gn[g] for g in keys["game_id"].to_list()])
    res = analyze(keys, probs, first)
    res.update(
        sha256=sha,
        git_sha=git_sha(),
        seeds={"ga": GA_SEED, "bootstrap": BOOT_SEED, "n_boot": N_BOOT, "block_days": BLOCK_DAYS},
        ga={
            k: {
                "params": v["params"],
                "tune_loss": v["tune_loss"],
                "n_tune_games": v["n_tune_games"],
            }
            for k, v in ga.items()
        },
        baseline_params=base,
        reference_injury_elo=reference_injury_elo(keys),
        tests=[
            {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
        ],
        verdict=verdict(res),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def _fmt(d: float, lo: float, hi: float) -> str:
    return f"{d:+.4f} [{lo:+.4f},{hi:+.4f}]"


def render(res: dict[str, Any]) -> str:
    o = ["# ELO_HISTORY_WINDOW results (docs/prereg/ELO_HISTORY_WINDOW.md)", ""]
    o += [f"Frozen sha256 `{res['sha256']}`; git {res['git_sha']}; seeds {res['seeds']}.", ""]
    o += [
        "| season | arm | n | logloss | brier | dlogloss [game CI] | block CI | p | p_bh | ece |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["table"]:
        o.append(
            f"| {r['season']} | {r['arm']} | {r['n_games']} | {r['logloss']:.4f} | {r['brier']:.4f} | "
            f"{_fmt(r['dlogloss'], r['ci_lo'], r['ci_hi'])} | [{r['ci_block_lo']:+.4f},{r['ci_block_hi']:+.4f}] | "
            f"{r['p']:.3f} | {r['p_bh']:.3f} | {r['ece']:.4f} |"
        )
    o += [
        "",
        "Slices (arm - E0):",
        "",
        "| season | slice | arm | n | dlogloss [CI] |",
        "|---|---|---|---|---|",
    ]
    for r in res["slices"]:
        o.append(
            f"| {r['season']} | {r['slice']} | {r['arm']} | {r['n']} | {_fmt(r['dlogloss'], r['ci_lo'], r['ci_hi'])} |"
        )
    o += [
        "",
        f"GA parameters: {json.dumps(res['ga'])}",
        "",
        f"Reference injury-Elo v2 (not an arm): {res['reference_injury_elo']}",
        "",
    ]
    v = res["verdict"]
    o.append(
        f"Verdict: any arm passes = {v['any_arm_passes']}; closed on select season = {v['closed_on_select_season']}"
    )
    for a in FAMILY:
        o.append(
            f"* {a}: pass={v[a]['pass']} "
            + json.dumps({s: x["checks"] for s, x in v[a]["seasons"].items()})
        )
    return "\n".join(o) + "\n"


def do_report() -> None:
    res = json.loads((OUT_DIR / "results.json").read_text())
    REPORT_PATH.write_text(render(res))
    _log(f"wrote {REPORT_PATH}")


def do_smoke() -> None:
    """One season replay on the fixture games plus a tiny synthetic GA: wiring only, no claim."""
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db(":memory:")
    try:
        g = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            "FROM games WHERE season <= 2024 ORDER BY game_date, game_id"
        ).pl()
    finally:
        con.close()
    g = _games_frame(g)
    params = load_params()
    prod = production_logits(g, params)
    fast = fast_logits(prepare_arrays(g), params)
    _log(f"fixture games={g.height} max|fast-production|={float(np.max(np.abs(prod - fast))):.2e}")
    w = np.ones(g.height)
    best, loss = run_ga(g, w, maxiter=2, popsize=4, label="smoke")
    _log(f"smoke GA best={best} loss={loss:.5f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    ap.add_argument(
        "--sha", default=None, help="frozen sha256, 64 hex (required except gate --allow-draft)"
    )
    ap.add_argument(
        "--allow-draft", action="store_true", help="gate stage only: skip the hash check"
    )
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--history-db", default="data/history/nba_history.duckdb")
    args = ap.parse_args(argv)
    if args.allow_draft and args.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    configure(args.doc)
    sha = require_frozen(args.sha, args.allow_draft)
    _log(f"prereg sha256 {'(DRAFT, not enforced) ' if args.allow_draft else 'verified '}{sha[:8]}")
    if args.stage == "smoke":
        do_smoke()
        return 0
    if args.stage == "report":
        do_report()
        return 0
    try:
        world = load_world(args.db, args.history_db)
    except Locked as exc:
        print(f"LOCKED: {exc}", flush=True)
        return 3
    params = load_params()
    if args.stage == "gate":
        gate = do_gate(world, params, sha, args.allow_draft)
        return 0 if gate["all_ok"] else 2
    with HeavyLock():
        gate = do_gate(world, params, sha, False)
        if not gate["all_ok"]:
            _log("GATE FAILED: stop, no model result")
            return 2
        res = do_run(world, gate, sha)
        REPORT_PATH.write_text(render(res))
        _log(f"verdict: any arm passes = {res['verdict']['any_arm_passes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
