# ruff: noqa: E501
"""INJURY_REASON harness (rule: docs/prereg/INJURY_REASON.md; DRAFT until frozen).

    M="-m research.eval.injury_reason_eval"
    .venv/bin/python -W ignore $M gate  --allow-draft --doc docs/prereg/INJURY_REASON.md   # checks 1-6, A0 reproductions only
    .venv/bin/python -W ignore $M smoke --doc docs/prereg/INJURY_REASON.md --sha <64 hex>  # one month block, every arm
    .venv/bin/python -W ignore $M run   --doc docs/prereg/INJURY_REASON.md --sha <64 hex>  # gate -> arms -> results.json
    .venv/bin/python -W ignore $M report --doc docs/prereg/INJURY_REASON.md --sha <64 hex> # reports/prereg_injury_reason.md

``--sha`` must equal the sha256 of everything above the ``=== RESULTS BELOW ===`` line (a WHOLE LINE
match); ``--allow-draft`` skips that check and the FROZEN-status check and is for the gate stage only.
``smoke`` / ``run`` / ``report`` refuse unless the doc's ``Status:`` line says FROZEN and the sha matches.
Research module (``nba/`` untouched). ``nba.duckdb`` is opened read-only; every SQL carries
``season <= 2024`` and the frames are asserted; season 2025 is never loaded. Heavy stages take
``data/ops/heavy.lock``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    bh_adjust,
    crps_from_quantiles,
    month_blocks,
)
from nba.features.game_tipoff import build_game_tipoff
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    stat_feature_names,
    stat_frame,
)
from research.eval.f11_lineup_context import _bucket_minutes, _jd, git_sha
from research.eval.four_factors_eval import (
    MIN_SLICE_N,
    N_BOOT,
    _chk,
    _delta_stats,
    row_scores,
)
from research.eval.minutes_hazard_eval import MARKER, HeavyLock
from research.eval.minutes_v2_eval import (
    _ll,
    build_frame,
    ece,
    load_world,
    production_minutes_table,
)
from research.eval.own_tag_eval import collect_arm as collect_pts_arm
from research.eval.pplay_context_eval import build_base
from research.features.injury_reason import (
    BODY_CODE,
    BODY_GROUPS,
    CLASS_CODE,
    CLASSES,
    TYPE_CODE,
    TYPES,
    build_episode_features,
    build_return_features,
    build_snapshot_reasons,
    coverage_report,
    shuffle_within,
)
from research.props.minutes_v2 import V2_MINUTES_FEATURES, MinutesV2Model

DOC = Path("docs/prereg/INJURY_REASON.md")
OUT_DIR = Path("reports/prereg_injury_reason")
REPORT_MD = Path("reports/prereg_injury_reason.md")
CACHE_DIR = Path("data/ops/injury_reason_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
PPLAY_GATE_JSON = Path("reports/prereg_pplay_context/gate.json")
MV2_SCORES = Path("reports/minutes_v2/minutes_scores.npz")
MARKET_ROWS = "reports/odds_history/market_rows_{season}.parquet"
SEEDS = {"model": 0, "bootstrap": 0, "placebo": 0}
KEYS = ["game_id", "player_id"]
SEASONS = (2023, 2024)

# ---- frozen constants (draft values; final at the freeze)
FLOOR_LOGLOSS = -0.005
FLOOR_PTS = -0.005
FLOOR_MIN = (
    -0.10
)  # minutes units (MINUTES_V2 precedent); -0.005 minutes would be below any resolvable effect
PLACEBO_MARGIN = {"t1": -0.003, "pts": -0.003, "min": -0.06}
ECE_TOL = 0.005
SLICE_WORSE = {"t1": 0.01, "pts": 0.01, "min": 0.20}
COVERAGE_MIN = 0.95
NULL_GAP_PP = 2.0
MIN_CELL = 2000
RIDGE_LAMBDA = 1.0
RIDGE_ITERS = 30
MIN_TRAIN_T1 = 300
FLAGGED_CODES = (
    2,
    3,
    4,
)  # probable, questionable, doubtful ('out' excluded; 'available' not in the family)
STATUS_NAME = {1: "available", 2: "probable", 3: "questionable", 4: "doubtful", 5: "out"}
DAY_BINS = (0.0, 1.0, 3.0, 7.0, 14.0)  # days_since_flag bins: [0,1) [1,3) [3,7) [7,14) [14,inf)
EXTRA_COLS: tuple[str, ...] = (
    "rb_game_back",
    "rb_spell_missed",
    "rb_spell_listed",
    "rb_found",
    "rb_cls",
    "rb_body",
    "rb_type",
)
BUCKETS = ("DNP", "<5", "5-10", "10-20", ">20")

UNSTATED: list[str] = [
    "reason rows = player_availability joined to games with season <= 2024 (SQL), official-report source only; the snapshot convention is OWN_TAG's: only rows of the game's LATEST usable (tip - 60, real tip) snapshot count; a player listed twice takes the highest status code (ties: reason text ascending)",
    "the regexes in research/features/injury_reason.py were written looking at the reason STRINGS of the whole table (all seasons, counts only, no outcomes or labels), because the maintainer's question quoted whole-table counts; coverage and the unmatched list are re-measured on 2023-24 report rows only",
    "class priority g_league > injury (the 'Injury/Illness' marker or 'concussion') > not_with_team (also trade pending, return to competition) > suspension (also ineligible) > personal > rest_or_management > illness (health-and-safety, covid, illness); a stray prefix before the marker (e.g. 'Management Injury/Illness-...') never changes the class",
    "injury strings with 'Illness' as body or type, or 'Non-COVID', are class illness / type illness / body none; concussion is class injury, body head_concussion",
    "days_since_flag: an episode continues while consecutive listings (any status) of the same player share the class and are <= 14 calendar days apart; capped at 365; unlisted rows have none",
    "return rows follow production returning_3plus: previous PLAYED row in the same season, missed team games = team_game_no gap - 1 >= 3 -> game_back 1; the next consecutive appearances with no further such gap -> 2 and 3; the spell reason is the LAST listing in (previous appearance, tonight]; spell_found 0 when never listed there",
    "Target 1 universe = MINUTES_V2 secondary P(play) rows (build_minutes_features rows incl. DNP, n_played_prior >= 5, OUT-at-T-60 removed; pplay_context_eval.build_base) with status probable / questionable / doubtful on the latest usable snapshot; label played = minutes > 0",
    "A0t (A0 + ridge on the status tag only) is added to Target 1 as the fair baseline: production P(play) does not read the tag, and PPLAY_CONTEXT showed the tag alone is worth -0.010 / -0.016 log loss; the primary contrast is A1 - A0t, A1 - A0 is reported beside it",
    "ridge-logistic with offset logit(clip(p_raw, 1e-3, 1 - 1e-3)): Newton steps, L2 lambda 1.0 on every non-intercept column, unpenalised intercept, 30 iterations; one-hot status (3), class (8), body (12), type (10), days-since-flag bin (5); no tuning",
    "Target 2 pts: production context-residual learner, integer support, rows = stat_frame(pts) (n_prior >= 5), A1 appends the 7 EXTRA_COLS (codes +1, 0 = not a return row); scored on rows with game_back 1..3 in the test seasons; the all-rows delta is descriptive",
    "Target 2 minutes: the learner A1 modifies is MINUTES_V2's M2 (mixture, V2_MINUTES_FEATURES); the production trunc-normal arm P is reproduced (no fit) and reported as the production gap; primary contrast = A1 - M2 (same learner, same rows), continuous 199-quantile CRPS in minutes (minutes are not counts)",
    "placebo: Target 1 jointly shuffles (class, body, type, days bin) among flagged rows within (game_date, status); Target 2 jointly shuffles the 7 EXTRA_COLS among return rows within game_date; numpy default_rng(0)",
    "check 1b reference = the p_raw log loss and n recorded by the PPLAY_CONTEXT gate on the post-clean-up DB (reports/prereg_pplay_context/gate.json); the older reports/minutes_v2/pplay.npz predates the clean-up and is reported as information",
]


# --------------------------------------------------------------------------- freeze


def frozen_sha(doc: Path) -> str:
    """sha256 of everything above the MARKER line; the marker is a WHOLE LINE (re.M)."""
    import hashlib

    text = doc.read_text()
    m = re.search(rf"^{re.escape(MARKER)}$", text, flags=re.M)
    head = text[: m.start()] if m else text
    return hashlib.sha256(head.encode()).hexdigest()


def status_is_frozen(doc: Path) -> bool:
    m = re.search(r"^Status:\s*(\S+)", doc.read_text(), flags=re.M)
    return bool(m and m.group(1).upper().startswith("FROZEN"))


def require_frozen(doc: Path, expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(doc)
    if allow_draft:
        return sha
    if not status_is_frozen(doc):
        raise SystemExit("STOP: the doc's Status line does not say FROZEN")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def parse_results_block(doc: Path) -> str:
    """Text below the marker line (empty before a run); same whole-line match as the hash."""
    text = doc.read_text()
    m = re.search(rf"^{re.escape(MARKER)}$", text, flags=re.M)
    return text[m.end() :] if m else ""


# --------------------------------------------------------------------------- inputs


def load_reason_rows(db_path: str = "nba.duckdb") -> pl.DataFrame:
    """Official-report rows WITH ``reason`` for games of season <= 2024 (read-only)."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.reason, a.as_of, a.source, g.season "
            "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
            f"WHERE a.game_id IS NOT NULL AND g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert int(df["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    return df


def load_study(db_path: str = "nba.duckdb") -> dict[str, Any]:
    world = load_world(db_path)
    games, pgs, cfg = world["games"], world["pgs"], world["cfg"]
    assert int(games["season"].max()) <= MAX_SEASON
    tips = build_game_tipoff(max_season=MAX_SEASON)
    avail = load_reason_rows(db_path)
    snap = build_snapshot_reasons(avail, games, cfg.report, tips)
    return {
        "world": world,
        "games": games,
        "pgs": pgs,
        "cfg": cfg,
        "tips": tips,
        "avail": avail,
        "snap": snap,
        "ep": build_episode_features(snap),
    }


def day_bin(d: np.ndarray) -> np.ndarray:
    """Bins [0,1) [1,3) [3,7) [7,14) [14,inf) of days_since_flag; NaN (unlisted) -> bin 0."""
    return np.asarray(np.digitize(np.nan_to_num(d, nan=0.0), DAY_BINS[1:]), dtype=np.int64)


def _k64(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.col("player_id").cast(pl.Int64))


def attach_reason(frame: pl.DataFrame, snap: pl.DataFrame, ep: pl.DataFrame) -> pl.DataFrame:
    out = (
        _k64(frame)
        .join(
            _k64(snap).select([*KEYS, "status_code", "cls", "body", "type", "side"]),
            on=KEYS,
            how="left",
        )
        .join(_k64(ep), on=KEYS, how="left")
        .with_columns(pl.col("status_code").fill_null(0))
    )
    assert out.height == frame.height, "reason join changed the row count"
    return out.with_columns(
        pl.Series("day_bin", day_bin(out["days_since_flag"].fill_null(float("nan")).to_numpy()))
    )


def t1_frame(base: pl.DataFrame, snap: pl.DataFrame, ep: pl.DataFrame) -> pl.DataFrame:
    """Target 1 rows: the P(play) universe restricted to probable / questionable / doubtful."""
    f = attach_reason(base, snap, ep)
    f = f.filter(pl.col("status_code").is_in(list(FLAGGED_CODES)))
    return f.sort(["game_date", "game_id", "player_id"])


def t2_frame(feats: pl.DataFrame, played: pl.DataFrame, snap: pl.DataFrame) -> pl.DataFrame:
    """Production feature frame + the seven EXTRA_COLS (0 = not a return row; codes are +1)."""
    ret = build_return_features(
        _k64(played).select("game_id", "player_id", "game_date", "season", "team_game_no"),
        _k64(snap),
    )
    out = _k64(feats).join(ret, on=KEYS, how="left")
    assert out.height == feats.height
    on = (pl.col("game_back") >= 1).fill_null(False)
    return out.with_columns(
        pl.col("game_back").fill_null(0).cast(pl.Float64).alias("rb_game_back"),
        pl.when(on).then(pl.col("spell_missed")).otherwise(0.0).alias("rb_spell_missed"),
        pl.when(on).then(pl.col("spell_listed_games")).otherwise(0.0).alias("rb_spell_listed"),
        pl.when(on).then(pl.col("spell_found").cast(pl.Float64)).otherwise(0.0).alias("rb_found"),
        pl.when(on)
        .then(pl.col("spell_cls").replace_strict(CLASS_CODE, default=0, return_dtype=pl.Int64) + 1)
        .otherwise(0)
        .cast(pl.Float64)
        .alias("rb_cls"),
        pl.when(on)
        .then(pl.col("spell_body").replace_strict(BODY_CODE, default=0, return_dtype=pl.Int64) + 1)
        .otherwise(0)
        .cast(pl.Float64)
        .alias("rb_body"),
        pl.when(on)
        .then(pl.col("spell_type").replace_strict(TYPE_CODE, default=0, return_dtype=pl.Int64) + 1)
        .otherwise(0)
        .cast(pl.Float64)
        .alias("rb_type"),
    )


def placebo_t2(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """Shuffle the seven EXTRA_COLS jointly among return rows within game_date; others untouched."""
    ret = frame.filter(pl.col("rb_game_back") >= 1).select([*KEYS, "game_date", *EXTRA_COLS])
    sh = shuffle_within(ret, list(EXTRA_COLS), ["game_date"], seed)
    base = frame.drop(list(EXTRA_COLS)).join(
        pl.concat([sh.select([*KEYS, *EXTRA_COLS])]), on=KEYS, how="left"
    )
    base = base.with_columns([pl.col(c).fill_null(0.0) for c in EXTRA_COLS])
    return base.select(frame.columns)


def placebo_t1(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    return shuffle_within(
        frame,
        ["cls", "body", "type", "side", "day_bin", "days_since_flag"],
        ["game_date", "status_code"],
        seed,
    )


# --------------------------------------------------------------------------- ridge logistic with offset


def design(frame: pl.DataFrame, with_reason: bool) -> np.ndarray:
    cols: list[np.ndarray] = []
    sc = frame["status_code"].to_numpy()
    for k in FLAGGED_CODES:
        cols.append((sc == k).astype(float))
    if with_reason:
        for name, cats in (("cls", CLASSES), ("body", BODY_GROUPS), ("type", TYPES)):
            v = frame[name].to_numpy()
            for c in cats:
                cols.append((v == c).astype(float))
        db = frame["day_bin"].to_numpy()
        for b in range(len(DAY_BINS)):
            cols.append((db == b).astype(float))
    return np.column_stack(cols)


def fit_offset_logit(
    x: np.ndarray,
    y: np.ndarray,
    off: np.ndarray,
    lam: float = RIDGE_LAMBDA,
    iters: int = RIDGE_ITERS,
) -> np.ndarray:
    """Ridge logistic with offset by Newton steps. Beta[0] is an unpenalised intercept."""
    n, k = x.shape
    xa = np.column_stack([np.ones(n), x])
    beta = np.zeros(k + 1)
    pen = np.full(k + 1, lam)
    pen[0] = 0.0
    for _ in range(iters):
        z = off + xa @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = xa.T @ (p - y) + pen * beta
        w = np.clip(p * (1.0 - p), 1e-6, None)
        h = (xa * w[:, None]).T @ xa + np.diag(pen)
        step = np.linalg.solve(h, g)
        beta = beta - step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta


def predict_offset_logit(beta: np.ndarray, x: np.ndarray, off: np.ndarray) -> np.ndarray:
    z = off + beta[0] + x @ beta[1:]
    return np.asarray(1.0 / (1.0 + np.exp(-np.clip(z, -30, 30))))


def logit_clip(p: np.ndarray) -> np.ndarray:
    pc = np.clip(p, 1e-3, 1 - 1e-3)
    return np.asarray(np.log(pc / (1.0 - pc)))


def walk_t1(
    frame: pl.DataFrame,
    with_reason: bool,
    test_seasons: tuple[int, ...] = SEASONS,
    max_blocks: int | None = None,
) -> np.ndarray:
    """Walk-forward month blocks over the flagged rows; returns p aligned with the TEST rows of
    ``frame`` (sorted game_date, game_id, player_id), NaN where a block had too little training."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    test_mask = frame["season"].is_in(list(test_seasons)).to_numpy()
    out = np.full(frame.height, np.nan)
    x = design(frame, with_reason)
    off = logit_clip(frame["p_raw"].to_numpy())
    y = frame["played"].to_numpy()
    dates = frame["game_date"].to_numpy()
    for b, (start, end) in enumerate(
        month_blocks(frame.filter(pl.col("season").is_in(list(test_seasons))))
    ):
        if max_blocks is not None and b >= max_blocks:
            break
        tr = dates < np.datetime64(start)
        te = test_mask & (dates >= np.datetime64(start)) & (dates < np.datetime64(end))
        if tr.sum() < MIN_TRAIN_T1 or not te.any():
            continue
        beta = fit_offset_logit(x[tr], y[tr], off[tr])
        out[te] = predict_offset_logit(beta, x[te], off[te])
    return out


# --------------------------------------------------------------------------- scoring helpers


def _mean(s: pl.Series) -> float:
    return float(np.asarray(s.cast(pl.Float64).to_numpy()).mean())


def clustered_delta(delta: np.ndarray, cluster: np.ndarray) -> dict[str, float]:
    return _delta_stats(delta, cluster)


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.any():
            out.append(
                {
                    "bin": b,
                    "n": int(m.sum()),
                    "mean_p": float(p[m].mean()),
                    "obs": float(y[m].mean()),
                }
            )
    return out


def score_t1(frame: pl.DataFrame, arms: dict[str, np.ndarray]) -> dict[str, Any]:
    """Paired log-loss deltas; clusters = game_date (cross-game question). ``arms`` hold p on all rows."""
    res: dict[str, Any] = {"rows": [], "slices": [], "reliability": {}}
    for season in SEASONS:
        m = (frame["season"] == season).to_numpy()
        if not m.any():
            continue
        y = frame["played"].to_numpy()[m].astype(bool)
        d = frame.filter(pl.Series(m))
        cluster = d["game_date"].cast(pl.Int32).to_numpy()
        ll = {a: _ll(p[m], y) for a, p in arms.items()}
        for a, p in arms.items():
            r: dict[str, Any] = {
                "target": "t1",
                "season": season,
                "arm": a,
                "n_rows": int(m.sum()),
                "n_dates": int(len(np.unique(cluster))),
                "logloss": float(ll[a].mean()),
                "brier": float(((p[m] - y) ** 2).mean()),
                "ece": ece(p[m], y.astype(float)),
            }
            res["reliability"][f"{season}:{a}"] = reliability(p[m], y.astype(float))
            res["rows"].append(r)
        for a, ref in (("A1", "A0t"), ("A1", "A0"), ("A0t", "A0"), ("P1", "A0t"), ("A1", "P1")):
            if a in ll and ref in ll:
                s = clustered_delta(ll[a] - ll[ref], cluster)
                res["rows"].append(
                    {
                        "target": "t1",
                        "season": season,
                        "arm": f"{a}-{ref}",
                        **s,
                        "n_rows": int(m.sum()),
                    }
                )
        masks: dict[str, np.ndarray] = {
            **{
                f"status:{STATUS_NAME[k]}": (d["status_code"].to_numpy() == k)
                for k in FLAGGED_CODES
            },
            **{f"cls:{c}": (d["cls"].to_numpy() == c) for c in CLASSES},
            **{f"type:{c}": (d["type"].to_numpy() == c) for c in TYPES},
        }
        for name, mk in masks.items():
            n = int(mk.sum())
            rec: dict[str, Any] = {"target": "t1", "season": season, "slice": name, "n": n}
            if n >= MIN_SLICE_N:
                s = clustered_delta((ll["A1"] - ll["A0t"])[mk], cluster[mk])
                rec.update({k: s[k] for k in ("dcrps", "ci_lo", "ci_hi")})
            res["slices"].append(rec)
    return res


def add_bh(rows: list[dict[str, Any]], families: dict[str, str]) -> None:
    """BH within each season over the primary tests named in ``families`` (arm label by target)."""
    for season in SEASONS:
        tgt = [r for r in rows if r["season"] == season and families.get(r["target"]) == r["arm"]]
        if not tgt:
            continue
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


# --------------------------------------------------------------------------- gate


def check_1a_pts(
    world: dict[str, Any], cfg: ContextResidualConfig, feats: pl.DataFrame
) -> dict[str, Any]:
    """A0 pts (production context-residual, integer support) vs reports/lower_tail/candidates.json 'off'."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / "arm_pts_A0.npz"
    if p.exists():
        z = np.load(p)
        a = {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    else:
        a = collect_pts_arm(feats, "pts", cfg, stat_feature_names("pts"))
        np.savez(p, qi=a["qi"], y=a["y"])
        a["meta"].write_parquet(p.with_suffix(".parquet"))
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for season in SEASONS:
        sm = (a["meta"]["season"] == season).to_numpy()
        got = float(crps_from_quantiles(a["qi"][sm].astype(float), a["y"][sm]).mean())
        stored = float(ref["pts"][str(season)]["off"]["crps_int"])
        n_ref = int(ref["pts"][str(season)]["off"]["n"])
        good = abs(got - stored) <= 1e-6 and int(sm.sum()) == n_ref
        ok &= good
        rec[str(season)] = {
            "got": got,
            "stored": stored,
            "delta": got - stored,
            "n": int(sm.sum()),
            "n_ref": n_ref,
            "ok": good,
        }
    return {
        "ok": bool(ok),
        "detail": rec,
        "meta_keys": a["meta"].select([*KEYS, "season", "game_date"]),
    }


def check_1b_pplay(base: pl.DataFrame) -> dict[str, Any]:
    """p_raw log loss and n vs the PPLAY_CONTEXT gate (post-clean-up DB); stored minutes_v2 npz as information."""
    ref = json.loads(PPLAY_GATE_JSON.read_text())["gate"]["1_p0_reproduces"]["detail"]
    test = base.filter(pl.col("season").is_in(list(SEASONS)))
    y = test["played"].to_numpy().astype(bool)
    pr = test["p_raw"].to_numpy()
    season = test["season"].to_numpy()
    rec: dict[str, Any] = {}
    ok = True
    for s in SEASONS:
        m = season == s
        got = float(_ll(pr[m], y[m]).mean())
        stored = float(ref[str(s)]["p_raw"]["mine"])
        n_ref = int(ref[str(s)]["n_mine"])
        good = abs(got - stored) <= 1e-6 and int(m.sum()) == n_ref
        ok &= good
        rec[str(s)] = {
            "got": got,
            "stored": stored,
            "delta": got - stored,
            "n": int(m.sum()),
            "n_ref": n_ref,
            "ok": good,
        }
    npz = Path("reports/minutes_v2/pplay.npz")
    if npz.exists():
        z = np.load(npz, allow_pickle=True)
        for s in SEASONS:
            ms = z["season"] == s
            rec[str(s)]["info_minutes_v2_npz"] = {
                "n_stored": int(ms.sum()),
                "logloss_stored": float(_ll(z["p_raw"][ms], z["y"][ms].astype(bool)).mean()),
            }
    return {"ok": bool(ok), "detail": rec}


def check_1c_minutes_p(world: dict[str, Any]) -> dict[str, Any]:
    """Production trunc-normal minutes (no fit) vs the stored P_crps, per identical (game, player) key."""
    from research.props.minutes_v2 import trunc_normal_quantiles

    prod = production_minutes_table(world["sc"])
    z = np.load(MV2_SCORES, allow_pickle=True)
    st = pl.DataFrame(
        {
            "game_id": z["row_gid"].astype(str),
            "player_id": z["row_pid"].astype(np.int64),
            "season": z["row_season"].astype(np.int64),
            "y": z["row_y"].astype(float),
            "crps_stored": z["P_crps"].astype(float),
        }
    )
    j = st.join(prod, on=KEYS, how="inner")
    q = trunc_normal_quantiles(j["p_mu"].to_numpy(), j["p_sigma"].to_numpy())
    mine = crps_from_quantiles(q, j["y"].to_numpy())
    diff = np.abs(mine - j["crps_stored"].to_numpy())
    rec: dict[str, Any] = {}
    ok = True
    for s in SEASONS:
        m = (j["season"] == s).to_numpy()
        n_st = int((st["season"] == s).sum())
        n_same = int((diff[m] <= 1e-9).sum())
        good = (
            bool(m.sum() == n_st and diff[m].max() <= 1e-6)
            if s == 2023
            else bool(diff[m].mean() <= 0.005 and n_same / max(int(m.sum()), 1) >= 0.99)
        )
        ok &= good
        rec[str(s)] = {
            "n_stored": n_st,
            "n_joined": int(m.sum()),
            "keys_identical_to_1e-9": n_same,
            "max_abs_diff": float(diff[m].max()),
            "mean_abs_diff": float(diff[m].mean()),
            "mean_crps_mine": float(mine[m].mean()),
            "mean_crps_stored": float(j["crps_stored"].to_numpy()[m].mean()),
            "ok": good,
        }
    return {"ok": bool(ok), "detail": rec}


def check_2_coverage(avail: pl.DataFrame, snap: pl.DataFrame, t1: pl.DataFrame) -> dict[str, Any]:
    a = avail.filter(
        pl.col("season").is_in(list(SEASONS)) & (pl.col("source") == "nba_official_report")
    )
    rep = coverage_report(a["reason"], top=50)
    rep["by_season"] = {}
    for s in SEASONS:
        r = coverage_report(a.filter(pl.col("season") == s)["reason"], top=5)
        rep["by_season"][str(s)] = {"n_rows": r["n_rows"], "coverage": r["coverage"]}
    s2 = snap.filter(pl.col("game_date").is_between(dt.date(2023, 10, 1), dt.date(2025, 7, 1)))
    rep["latest_snapshot_rows_2023_24"] = {
        "n": s2.height,
        "non_unknown": float((s2["cls"] != "unknown").mean()),  # type: ignore[arg-type]
    }
    rep["target1_rows"] = {
        "n": t1.height,
        "non_unknown": float((t1["cls"] != "unknown").mean()),  # type: ignore[arg-type]
        "by_class": {k: int(v) for k, v in t1.group_by("cls").len().iter_rows()},
    }
    rep["ok"] = bool(rep["coverage"] >= COVERAGE_MIN)
    return rep


def check_3_missingness(t1: pl.DataFrame, t2: pl.DataFrame) -> dict[str, Any]:
    """Share of rows with an UNKNOWN / NULL reason category, by realised-minutes bucket (DNP included
    for Target 1 where the label is DNP-ness; played buckets for Target 2). Max-min gap in pp."""
    out: dict[str, Any] = {"t1": [], "t2": []}
    gaps: dict[str, float] = {}
    d1 = t1.filter(pl.col("season").is_in(list(SEASONS))).with_columns(
        _bucket_minutes(pl.col("min_real")).alias("_b")
    )
    rates: dict[str, float] = {}
    for b in BUCKETS:
        sub = d1.filter(pl.col("_b") == b)
        v = (
            _mean(
                (sub["cls"] == "unknown") | sub["cls"].is_null() | sub["days_since_flag"].is_null()
            )
            if sub.height
            else float("nan")
        )
        rates[b] = v
        out["t1"].append({"bucket": b, "n": sub.height, "unknown_or_null_rate": v})
    played = [v for k, v in rates.items() if k != "DNP" and not np.isnan(v)]
    gaps["t1_all"] = (np.nanmax(list(rates.values())) - np.nanmin(list(rates.values()))) * 100.0
    gaps["t1_played"] = (max(played) - min(played)) * 100.0
    d2 = t2.filter(
        pl.col("season").is_in(list(SEASONS))
        & (pl.col("rb_game_back") >= 1)
        & (pl.col("n_prior") >= 5)
    ).with_columns(
        pl.col("minutes").alias("_m"),
        pl.when(pl.col("minutes") < 5)
        .then(pl.lit("<5"))
        .when(pl.col("minutes") <= 20)
        .then(pl.lit("5-20"))
        .otherwise(pl.lit(">20"))
        .alias("_b3"),
    )
    r2: dict[str, float] = {}
    for b in ("<5", "5-20", ">20"):
        sub = d2.filter(pl.col("_b3") == b)
        v = _mean(sub["rb_found"] == 0) if sub.height else float("nan")
        r2[b] = v
        out["t2"].append(
            {
                "bucket": b,
                "n": sub.height,
                "unlisted_or_unknown_rate": v,
                "non_none_class_rate": _mean(sub["rb_cls"] > 1) if sub.height else float("nan"),
            }
        )
    gaps["t2"] = (np.nanmax(list(r2.values())) - np.nanmin(list(r2.values()))) * 100.0
    out["gaps_pp"] = gaps
    out["ok"] = bool(max(gaps.values()) <= NULL_GAP_PP)
    return out


def _tag_equal(a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str], cols: list[str]) -> bool:
    ids = list(game_ids)
    x = a.filter(pl.col("game_id").is_in(ids)).sort(KEYS)
    y = b.filter(pl.col("game_id").is_in(ids)).sort(KEYS)
    if x.height != y.height or not x.select(KEYS).equals(y.select(KEYS)):
        return False
    return all(x[c].equals(y[c]) for c in cols)


def _extra_snapshot(
    avail: pl.DataFrame,
    pgs: pl.DataFrame,
    tips: pl.DataFrame,
    gids: set[str],
    minutes_before: int,
    reason: str,
) -> pl.DataFrame:
    r = (
        pgs.filter(pl.col("game_id").is_in(list(gids)))
        .select(KEYS)
        .join(tips.select(["game_id", "tip_et"]), on="game_id")
        .with_columns(
            pl.lit("questionable").alias("status"),
            pl.lit(reason).alias("reason"),
            (pl.col("tip_et") - pl.duration(minutes=minutes_before)).alias("as_of"),
            pl.lit("nba_official_report").alias("source"),
            pl.lit(2024).alias("season"),
        )
        .select(["game_id", "player_id", "status", "reason", "as_of", "source", "season"])
    )
    return pl.concat(
        [avail.select(r.columns), r.cast(avail.select(r.columns).schema, strict=False)]
    )


FEATURE_COLS = ["status_code", "cls", "body", "type", "side", "days_since_flag"]
RET_COLS = [
    "game_back",
    "spell_missed",
    "spell_listed_games",
    "spell_cls",
    "spell_body",
    "spell_type",
    "spell_found",
]


def _all_features(
    avail: pl.DataFrame,
    games: pl.DataFrame,
    cfg: ContextResidualConfig,
    tips: pl.DataFrame,
    played: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    snap = build_snapshot_reasons(avail, games, cfg.report, tips)
    ep = build_episode_features(snap)
    ret = build_return_features(
        played.select("game_id", "player_id", "game_date", "season", "team_game_no"), snap
    )
    return snap.join(ep, on=KEYS, how="left"), ret, snap


def planted_tests(inp: dict[str, Any], played: pl.DataFrame) -> list[dict[str, Any]]:
    games, pgs, avail, tips, cfg = inp["games"], inp["pgs"], inp["avail"], inp["tips"], inp["cfg"]
    base_snap, base_ret, _ = _all_features(avail, games, cfg, tips, played)
    reg24 = games.filter(
        (pl.col("season") == 2024) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list()) & set(
        tips["game_id"].to_list()
    )
    marker = "Injury/Illness-LeftKnee;Surgery"
    late = _extra_snapshot(avail, pgs, tips, targets, 30, marker)
    s_late, r_late, _ = _all_features(late, games, cfg, tips, played)
    same_ok = _tag_equal(base_snap, s_late, targets, FEATURE_COLS) and _tag_equal(
        base_ret, r_late, targets, RET_COLS
    )
    probe = _extra_snapshot(avail, pgs, tips, targets, 59, marker)
    s_p, r_p, _ = _all_features(probe, games, cfg, tips, played)
    probe_ok = _tag_equal(base_snap, s_p, targets, FEATURE_COLS)
    early = _extra_snapshot(avail, pgs, tips, targets, 90, marker)
    s_e, r_e, _ = _all_features(early, games, cfg, tips, played)
    moves = (not _tag_equal(base_snap, s_e, targets, FEATURE_COLS)) or (
        not _tag_equal(base_ret, r_e, targets, RET_COLS)
    )
    # planted future: rewrite the reason of every report row of games on/after the cutoff
    cutoff = dt.date(2024, 1, 15)
    fut = set(games.filter(pl.col("game_date") >= cutoff)["game_id"].to_list())
    av2 = avail.with_columns(
        pl.when(pl.col("game_id").is_in(list(fut)))
        .then(pl.lit(marker))
        .otherwise(pl.col("reason"))
        .alias("reason")
    )
    s_f, r_f, _ = _all_features(av2, games, cfg, tips, played)
    before = set(games.filter(pl.col("game_date") < cutoff)["game_id"].to_list())
    fut_ok = _tag_equal(base_snap, s_f, before, FEATURE_COLS) and _tag_equal(
        base_ret, r_f, before, RET_COLS
    )
    fut_moves = (not _tag_equal(base_snap, s_f, fut, FEATURE_COLS)) or (
        not _tag_equal(base_ret, r_f, fut, RET_COLS)
    )
    # truncation: features of games on/before the day computed from data stopping at that day are identical
    gid_ok = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    avail_t = avail.filter(pl.col("game_id").is_in(list(gid_ok)))
    played_t = played.filter(pl.col("game_id").is_in(list(gid_ok)))
    s_t, r_t, _ = _all_features(avail_t, games, cfg, tips, played_t)
    trunc_ok = _tag_equal(base_snap, s_t, gid_ok, FEATURE_COLS) and _tag_equal(
        base_ret, r_t, gid_ok, RET_COLS
    )
    return [
        _chk(
            "4 PLANT same-game: tip-30 snapshot leaves the day's reason, episode and return features identical",
            int(same_ok),
            "== 1",
            same_ok,
        ),
        _chk("4 PLANT same-game: tip-59 snapshot is refused", int(probe_ok), "== 1", probe_ok),
        _chk(
            "4 PLANT same-game is not inert (tip-90 snapshot changes features)",
            int(moves),
            "== 1",
            moves,
        ),
        _chk(
            "4 PLANT future (>= 2024-01-15) reason rewrite: earlier features unchanged",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk(
            "4 PLANT future is not inert (later features change)", int(fut_moves), "== 1", fut_moves
        ),
        _chk(
            "4 TRUNCATION: features of games <= the mid-2024 day are identical with all later data removed",
            int(trunc_ok),
            "== 1",
            trunc_ok,
        ),
    ]


def check_5_cells(t1: pl.DataFrame, t2: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {"t1": {}, "t2": {}}
    ok_t1 = ok_t2 = True
    for s in SEASONS:
        d1 = t1.filter(pl.col("season") == s)
        n1 = d1.height
        out["t1"][str(s)] = {
            "flagged_rows": n1,
            "by_status": {
                STATUS_NAME[k]: int((d1["status_code"] == k).sum()) for k in FLAGGED_CODES
            },
            "played": int(d1["played"].sum()),
            "n_dates": int(d1["game_date"].n_unique()),
        }
        ok_t1 &= n1 >= MIN_CELL
        d2 = t2.filter(
            (pl.col("season") == s) & (pl.col("n_prior") >= 5) & (pl.col("rb_game_back") >= 1)
        )
        n2 = d2.height
        out["t2"][str(s)] = {
            "return_rows": n2,
            "by_game_back": {str(k): int((d2["rb_game_back"] == k).sum()) for k in (1, 2, 3)},
            "spell_found": int((d2["rb_found"] == 1).sum()),
            "by_spell_type": {
                k: int(v)
                for k, v in d2.with_columns(pl.col("rb_type").cast(pl.Int64))
                .group_by("rb_type")
                .len()
                .sort("rb_type")
                .iter_rows()
            },
            "n_games": int(d2["game_id"].n_unique()),
        }
        ok_t2 &= n2 >= MIN_CELL
    out["t1_ok"], out["t2_ok"] = bool(ok_t1), bool(ok_t2)
    out["min_cell"] = MIN_CELL
    return out


def check_6_keys(t1: pl.DataFrame, t2: pl.DataFrame) -> dict[str, Any]:
    """Arm frames share keys and row order (built, not fitted)."""
    t1p = placebo_t1(t1, SEEDS["placebo"])
    t2p = placebo_t2(t2, SEEDS["placebo"])
    t1_ok = t1.select(KEYS).equals(t1p.select(KEYS)) and t1.height == t1p.height
    t2_ok = t2.select(KEYS).equals(t2p.select(KEYS)) and t2.height == t2p.height
    ret = t2.filter(pl.col("rb_game_back") >= 1)
    retp = t2p.filter(pl.col("rb_game_back") >= 1)
    multiset_ok = sorted(ret["rb_type"].to_list()) == sorted(retp["rb_type"].to_list())
    return {
        "t1_keys_identical": bool(t1_ok),
        "t2_keys_identical": bool(t2_ok),
        "t2_placebo_preserves_multiset": bool(multiset_ok),
        "ok": bool(t1_ok and t2_ok and multiset_ok),
    }


def run_gate(
    db_path: str, doc: Path, sha: str, allow_draft: bool, out_dir: Path = OUT_DIR
) -> dict[str, Any]:
    t0 = time.time()

    def lap(msg: str) -> None:
        print(f"[{time.time() - t0:.0f}s] {msg}", flush=True)

    inp = load_study(db_path)
    lap("inputs loaded")
    world, cfg = inp["world"], inp["cfg"]
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import build_features

    feats = attach_realised(
        build_features(
            inp["games"], inp["pgs"], world["static"], world["flagged"], world["elo_params"]
        ),
        inp["games"],
        inp["pgs"],
    )
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    base = build_base(world)
    assert int(base["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    lap("features and P(play) base built")
    t1 = t1_frame(base, inp["snap"], inp["ep"])
    t2 = t2_frame(feats, feats, inp["snap"])
    lap(f"frames: t1 {t1.height} rows, t2 {t2.height} rows")
    tests: list[dict[str, Any]] = []
    c1a = check_1a_pts(world, cfg, feats)
    lap("1a pts A0")
    c1b = check_1b_pplay(base)
    c1c = check_1c_minutes_p(world)
    lap("1b, 1c")
    for name, c in (
        ("1a A0 pts reproduces candidates.json 'off' (2 seasons, 1e-6, n identical)", c1a),
        ("1b A0 raw P(play) reproduces the PPLAY_CONTEXT post-clean-up log loss and n (1e-6)", c1b),
        (
            "1c production minutes P reproduces stored P_crps on identical keys (2023 exact; 2024 >= 99% keys, mean 0.005)",
            c1c,
        ),
    ):
        tests.append(_chk(name, int(c["ok"]), "== 1", c["ok"]))
    cov = check_2_coverage(inp["avail"], inp["snap"], t1)
    tests.append(
        _chk(
            "2 categoriser coverage of 2023-24 report rows (non-unknown class)",
            cov["coverage"],
            f">= {COVERAGE_MIN}",
            cov["ok"],
        )
    )
    miss = check_3_missingness(t1, t2)
    tests.append(
        _chk(
            "3 max NULL/unknown-reason gap across realised-minutes buckets (pp)",
            max(miss["gaps_pp"].values()),
            f"<= {NULL_GAP_PP}",
            miss["ok"],
        )
    )
    tests += planted_tests(inp, feats)
    lap("plants")
    cells = check_5_cells(t1, t2)
    tests.append(
        _chk(
            "5 Target 1 flagged rows per season",
            min(v["flagged_rows"] for v in cells["t1"].values()),
            f">= {MIN_CELL}",
            cells["t1_ok"],
        )
    )
    tests.append(
        _chk(
            "5 Target 2 return rows per season",
            min(v["return_rows"] for v in cells["t2"].values()),
            f">= {MIN_CELL}",
            cells["t2_ok"],
        )
    )
    keys = check_6_keys(t1, t2)
    tests.append(
        _chk(
            "6 identical row keys across arms (A0 / A0t / A1 / P1)",
            int(keys["ok"]),
            "== 1",
            keys["ok"],
        )
    )
    c1a_out = {k: v for k, v in c1a.items() if k != "meta_keys"}
    res: dict[str, Any] = {
        "status": "GATE_PASSED" if all(t["ok"] for t in tests) else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "doc": str(doc),
        "sha256": sha,
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "check_1a_pts": c1a_out,
        "check_1b_pplay": c1b,
        "check_1c_minutes_p": c1c,
        "check_2_coverage": cov,
        "check_3_missingness": miss,
        "check_5_cells": cells,
        "check_6_keys": keys,
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "max_season_loaded": int(feats["season"].max()),  # type: ignore[arg-type]
        "unstated_details": UNSTATED,
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1, default=_jd, sort_keys=True))
    return res


# --------------------------------------------------------------------------- arms (frozen runs only)


def collect_minutes_arm(
    frame: pl.DataFrame,
    cfg: ContextResidualConfig,
    extra: tuple[str, ...],
    max_blocks: int | None = None,
) -> dict[str, Any]:
    """M2 walk-forward (month blocks, train strictly before) with optional extra columns; continuous CRPS."""
    sf = stat_frame(frame, "pts").sort(["game_date", "game_id", "player_id"])
    test_all = sf.filter(pl.col("season").is_in(list(SEASONS)))
    names = [*V2_MINUTES_FEATURES, *extra]
    crps: list[np.ndarray] = []
    mean: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for b, (start, end) in enumerate(month_blocks(test_all)):
        if max_blocks is not None and b >= max_blocks:
            break
        block = sf.filter(
            (pl.col("game_date") >= start)
            & (pl.col("game_date") < end)
            & pl.col("season").is_in(list(SEASONS))
        )
        train = sf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < 1500:
            continue
        t0 = time.time()
        model = MinutesV2Model(names, "mixture", cfg).fit(train)
        q = model.quantiles(block, CRPS_TAUS)
        y = block["minutes"].cast(pl.Float64).to_numpy()
        crps.append(crps_from_quantiles(q, y))
        mean.append(q.mean(axis=1))
        ys.append(y)
        metas.append(block.select(["game_id", "player_id", "game_date", "season"]))
        print(
            f"  minutes block {start} done ({time.time() - t0:.0f}s, n={block.height})", flush=True
        )
    return {
        "crps": np.concatenate(crps),
        "mean": np.concatenate(mean),
        "y": np.concatenate(ys),
        "meta": pl.concat(metas),
    }


def _cached(name: str, fn: Any) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{name}.npz"
    if p.exists():
        z = np.load(p, allow_pickle=True)
        out = {k: z[k] for k in z.files}
        out["meta"] = pl.read_parquet(p.with_suffix(".parquet"))
        return out
    out = fn()
    np.savez(p, **{k: v for k, v in out.items() if k != "meta"})
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    return out


def _pts_arm(
    frame: pl.DataFrame,
    cfg: ContextResidualConfig,
    extra: tuple[str, ...],
    max_blocks: int | None = None,
) -> dict[str, Any]:
    a = collect_pts_arm(
        frame, "pts", cfg, stat_feature_names("pts", extra=extra), SEASONS, max_blocks
    )
    scores = row_scores(a["qi"], a["y"], "pts")
    return {
        "crps": scores["crps"],
        "mean": a["qi"].astype(float).mean(axis=1),
        "y": a["y"],
        "meta": a["meta"],
    }


def run_arms(
    db_path: str, sha: str, out_dir: Path = OUT_DIR, smoke: bool = False
) -> dict[str, Any]:
    gate = json.loads((out_dir / "gate.json").read_text())
    if gate.get("draft") or not gate.get("gate_ok") or gate.get("sha256") != sha:
        raise RuntimeError("no passing gate recorded for this frozen sha: no arm may be fitted")
    t0 = time.time()
    inp = load_study(db_path)
    world, cfg = inp["world"], inp["cfg"]
    frame = build_frame(world)  # production features + V2 group + realised minutes
    base = build_base(world)
    t1 = t1_frame(base, inp["snap"], inp["ep"])
    t2 = t2_frame(frame, frame, inp["snap"])
    t2p = placebo_t2(t2, SEEDS["placebo"])
    t1p = placebo_t1(t1, SEEDS["placebo"])
    mb = 1 if smoke else None
    print(f"[{time.time() - t0:.0f}s] frames ready", flush=True)
    # ---- Target 1
    arms1 = {
        "A0": t1["p_raw"].to_numpy(),
        "A0t": walk_t1(t1, False, max_blocks=mb),
        "A1": walk_t1(t1, True, max_blocks=mb),
        "P1": walk_t1(t1p, True, max_blocks=mb),
    }
    valid = ~np.isnan(arms1["A1"]) & t1["season"].is_in(list(SEASONS)).to_numpy()
    t1v = t1.filter(pl.Series(valid))
    arms1 = {a: p[valid] for a, p in arms1.items()}
    s1 = score_t1(t1v, arms1)
    print(f"[{time.time() - t0:.0f}s] target 1 scored", flush=True)
    # ---- Target 2
    names_extra = EXTRA_COLS
    pts_arms = {
        "A0": _cached("t2_pts_A0", lambda: _pts_arm(t2, cfg, (), mb))
        if not smoke
        else _pts_arm(t2, cfg, (), mb),
        "A1": _cached("t2_pts_A1", lambda: _pts_arm(t2, cfg, names_extra, mb))
        if not smoke
        else _pts_arm(t2, cfg, names_extra, mb),
        "P1": _cached("t2_pts_P1", lambda: _pts_arm(t2p, cfg, names_extra, mb))
        if not smoke
        else _pts_arm(t2p, cfg, names_extra, mb),
    }
    print(f"[{time.time() - t0:.0f}s] pts arms done", flush=True)
    min_arms = {
        "A0": _cached("t2_min_M2", lambda: collect_minutes_arm(t2, cfg, (), mb))
        if not smoke
        else collect_minutes_arm(t2, cfg, (), mb),
        "A1": _cached("t2_min_A1", lambda: collect_minutes_arm(t2, cfg, names_extra, mb))
        if not smoke
        else collect_minutes_arm(t2, cfg, names_extra, mb),
        "P1": _cached("t2_min_P1", lambda: collect_minutes_arm(t2p, cfg, names_extra, mb))
        if not smoke
        else collect_minutes_arm(t2p, cfg, names_extra, mb),
    }
    print(f"[{time.time() - t0:.0f}s] minutes arms done", flush=True)
    rows2 = score_t2(pts_arms, min_arms, t2)
    rows = s1["rows"] + rows2["rows"]
    add_bh(rows, {"t1": "A1-A0t", "pts": "A1-A0", "min": "A1-A0"})
    desc = descriptive(t1, t2, inp, world)
    rules = evaluate_rules(rows, s1["slices"] + rows2["slices"])
    res = {
        "rows": rows,
        "slices": s1["slices"] + rows2["slices"],
        "reliability": s1["reliability"],
        "descriptive": desc,
        "rules": rules,
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "sha256": sha,
        "n_boot": N_BOOT,
        "arms_seconds": time.time() - t0,
        "smoke": smoke,
    }
    if not smoke:
        (out_dir / "results.json").write_text(json.dumps(res, indent=1, default=_jd))
    return res


def score_t2(
    pts_arms: dict[str, dict[str, Any]], min_arms: dict[str, dict[str, Any]], t2: pl.DataFrame
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for target, arms in (("pts", pts_arms), ("min", min_arms)):
        ref = arms["A0"]["meta"]
        for a, v in arms.items():
            assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
                f"{a}: arms must score identical rows"
            )
            assert np.array_equal(v["y"], arms["A0"]["y"])
        full = _k64(ref).join(
            _k64(t2).select([*KEYS, "rb_game_back", "rb_type", "rb_cls"]), on=KEYS, how="left"
        )
        assert full.height == ref.height
        for season in SEASONS:
            sm = ((ref["season"] == season) & (full["rb_game_back"] >= 1)).to_numpy()
            gid = ref["game_id"].to_numpy()[sm]
            d = full.filter(pl.Series(sm))
            for a, v in arms.items():
                rows.append(
                    {
                        "target": target,
                        "season": season,
                        "arm": a,
                        "n_rows": int(sm.sum()),
                        "n_games": int(len(np.unique(gid))),
                        "crps": float(v["crps"][sm].mean()),
                        "bias": float((v["y"][sm] - v["mean"][sm]).mean()),
                    }
                )
            for a, r in (("A1", "A0"), ("P1", "A0"), ("A1", "P1")):
                s = clustered_delta(arms[a]["crps"][sm] - arms[r]["crps"][sm], gid)
                rows.append(
                    {
                        "target": target,
                        "season": season,
                        "arm": f"{a}-{r}",
                        **s,
                        "n_rows": int(sm.sum()),
                        "n_games": int(len(np.unique(gid))),
                    }
                )
            allm = (ref["season"] == season).to_numpy()
            s_all = clustered_delta(
                arms["A1"]["crps"][allm] - arms["A0"]["crps"][allm], ref["game_id"].to_numpy()[allm]
            )
            rows.append(
                {
                    "target": target,
                    "season": season,
                    "arm": "A1-A0:all_rows",
                    **s_all,
                    "n_rows": int(allm.sum()),
                }
            )
            tcol = d["rb_type"].to_numpy()
            gb = d["rb_game_back"].to_numpy()
            masks = {f"game_back:{k}": gb == k for k in (1, 2, 3)}
            for tname, code in TYPE_CODE.items():
                masks[f"type:{tname}"] = tcol == code + 1
            for name, mk in masks.items():
                n = int(mk.sum())
                rec: dict[str, Any] = {"target": target, "season": season, "slice": name, "n": n}
                if n >= MIN_SLICE_N:
                    s = clustered_delta(
                        (arms["A1"]["crps"][sm] - arms["A0"]["crps"][sm])[mk], gid[mk]
                    )
                    rec.update({k: s[k] for k in ("dcrps", "ci_lo", "ci_hi")})
                slices.append(rec)
    return {"rows": rows, "slices": slices}


def descriptive(
    t1: pl.DataFrame, t2: pl.DataFrame, inp: dict[str, Any], world: dict[str, Any]
) -> dict[str, Any]:
    """Target 3 and the restriction question (no fit). Wilson/date-cluster CIs are computed in the report."""
    out: dict[str, Any] = {}
    q = t1.filter((pl.col("status_code") == 3) & pl.col("season").is_in(list(SEASONS)))
    rates: list[dict[str, Any]] = []
    for key in ("type", "cls"):
        for (season, val), g in q.group_by(["season", key]):
            y = g["played"].to_numpy().astype(float)
            clus = g["game_date"].cast(pl.Int32).to_numpy()
            ci = (
                _delta_stats(y - 0.0, clus)
                if len(y) > 1
                else {"dcrps": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
            )
            rates.append(
                {
                    "by": key,
                    "value": val,
                    "season": int(season),
                    "n": len(y),
                    "play_rate": float(y.mean()),
                    "ci_lo": ci["ci_lo"],
                    "ci_hi": ci["ci_hi"],
                }
            )
    out["questionable_play_rate"] = rates
    prod = _k64(production_minutes_table(world["sc"])).select(*KEYS, "p_mu")
    r1 = (
        _k64(t2)
        .filter(
            (pl.col("rb_game_back") == 1)
            & pl.col("season").is_in(list(SEASONS))
            & (pl.col("n_prior") >= 5)
        )
        .join(prod, on=KEYS, how="left")
    )
    short: list[dict[str, Any]] = []
    for (season, tcode), g in r1.group_by(["season", "rb_type"]):
        g = g.filter(
            pl.col("p_mu").is_not_null()
            & pl.col("minutes").is_not_null()
            & pl.col("min10").is_not_null()
        )
        sh_prod = (g["p_mu"] - g["minutes"]).to_numpy()
        sh_rec = (g["min10"] - g["minutes"]).to_numpy()
        clus = g["game_id"].to_numpy()
        ci = (
            _delta_stats(sh_rec, clus)
            if len(sh_rec) > 1
            else {"dcrps": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
        )
        short.append(
            {
                "season": int(season),
                "type_code": int(tcode),
                "n": len(sh_rec),
                "shortfall_vs_min10": float(sh_rec.mean()) if len(sh_rec) else None,
                "ci_lo": ci["ci_lo"],
                "ci_hi": ci["ci_hi"],
                "shortfall_vs_production_mu": float(sh_prod.mean()) if len(sh_prod) else None,
            }
        )
    out["first_game_back_shortfall"] = short
    over: list[dict[str, Any]] = []
    for season in SEASONS:
        p = Path(MARKET_ROWS.format(season=season))
        if not p.exists():
            continue
        mk = pl.read_parquet(p).filter((pl.col("stat") == "pts") & (pl.col("book") == "draftkings"))
        kinds = mk["snapshot_kind"].unique().to_list()
        first = sorted(kinds)[0] if kinds else None
        if first is None:
            continue
        r = t2.filter((pl.col("rb_game_back") >= 1) & (pl.col("season") == season)).join(
            _k64(mk).filter(pl.col("snapshot_kind") == first).select([*KEYS, "line"]),
            on=KEYS,
            how="inner",
        )
        r = r.filter(pl.col("pts").cast(pl.Float64) != pl.col("line"))
        for tcode, g in r.group_by("rb_type"):
            ov = (g["pts"].cast(pl.Float64) > g["line"]).to_numpy().astype(float)
            clus = g["game_date"].cast(pl.Int32).to_numpy()
            ci = (
                _delta_stats(ov, clus)
                if len(ov) > 1
                else {"ci_lo": float("nan"), "ci_hi": float("nan")}
            )
            over.append(
                {
                    "season": season,
                    "snapshot_kind": first,
                    "type_code": int(tcode[0] if isinstance(tcode, tuple) else tcode),
                    "n": len(ov),
                    "over_rate": float(ov.mean()),
                    "ci_lo": ci["ci_lo"],
                    "ci_hi": ci["ci_hi"],
                }
            )
    out["dk_over_rate_first3_back"] = over
    return out


def evaluate_rules(rows: list[dict[str, Any]], slices: list[dict[str, Any]]) -> dict[str, Any]:
    tab = {(r["target"], r["season"], r["arm"]): r for r in rows}
    primary = {"t1": "A1-A0t", "pts": "A1-A0", "min": "A1-A0"}
    floors = {"t1": FLOOR_LOGLOSS, "pts": FLOOR_PTS, "min": FLOOR_MIN}
    out: dict[str, Any] = {}
    for tgt, arm in primary.items():
        by_season: dict[str, bool] = {}
        for s in SEASONS:
            r = tab[(tgt, s, arm)]
            by_season[str(s)] = bool(
                r["dcrps"] <= floors[tgt] and r["ci_hi"] < 0.0 and r.get("p_bh", 1.0) < 0.05
            )
        t24 = tab[(tgt, 2024, arm)]
        pl_arm = "A1-P1"
        beats = bool(tab[(tgt, 2024, pl_arm)]["dcrps"] <= PLACEBO_MARGIN[tgt])
        bad = {
            f"{s['season']}:{s['slice']}": s["dcrps"]
            for s in slices
            if s["target"] == tgt
            and s["season"] == 2024
            and s.get("dcrps") is not None
            and s["n"] >= MIN_SLICE_N
            and s["dcrps"] > SLICE_WORSE[tgt]
        }
        ece_ok = True
        if tgt == "t1":
            e1, e0 = tab[(tgt, 2024, "A1")]["ece"], tab[(tgt, 2024, "A0t")]["ece"]
            ece_ok = bool(e1 - e0 <= ECE_TOL)
        out[tgt] = {
            "rule1_by_season": by_season,
            "rule1": all(by_season.values()),
            "A1_beats_P1_by_margin_2024": beats,
            "slices_worse": bad,
            "ece_ok": ece_ok,
            "mde_2024": t24.get("mde"),
            "pass": bool(all(by_season.values()) and beats and not bad and ece_ok),
            "pass_2023": bool(by_season["2023"]),
        }
    out["close_no_pass_2023"] = not any(out[t]["pass_2023"] for t in primary)
    return out


def run_smoke(db_path: str, sha: str) -> None:
    res = run_arms(db_path, sha, smoke=True)
    print(
        json.dumps({"rules": res["rules"], "seconds": res["arms_seconds"]}, indent=1, default=_jd)
    )


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    L = [
        f"# INJURY_REASON results (sha256 `{r['sha256']}`, git `{r['git_sha']}`)",
        "",
        "| target | season | arm | n | metric | delta | CI | p_bh |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for x in r["rows"]:
        metric = x.get("logloss", x.get("crps"))
        L.append(
            f"| {x['target']} | {x['season']} | {x['arm']} | {x.get('n_rows')} | {_f(metric)} | {_f(x.get('dcrps'), 4, True)} | [{_f(x.get('ci_lo'), 4, True)}, {_f(x.get('ci_hi'), 4, True)}] | {_f(x.get('p_bh'), 3)} |"
        )
    L += [
        "",
        "## Slices (primary contrast)",
        "",
        "| target | season | slice | n | delta | CI |",
        "|---|---|---|---|---|---|",
    ]
    for x in r["slices"]:
        L.append(
            f"| {x['target']} | {x['season']} | {x['slice']} | {x['n']} | {_f(x.get('dcrps'), 4, True)} | [{_f(x.get('ci_lo'), 4, True)}, {_f(x.get('ci_hi'), 4, True)}] |"
        )
    L += [
        "",
        "## Rules",
        "",
        "```",
        json.dumps(r["rules"], indent=1),
        "```",
        "",
        "## Descriptive (Target 3, restriction question)",
        "",
        "```",
        json.dumps(r["descriptive"], indent=1, default=_jd),
        "```",
        "",
    ]
    return "\n".join(L)


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    body = ["| id | test | result |", "|---|---|---|"]
    tab = {(x["target"], x["season"], x["arm"]): x for x in r["rows"]}
    for tgt, arm in (("t1", "A1-A0t"), ("pts", "A1-A0"), ("min", "A1-A0")):
        for se in SEASONS:
            x = tab[(tgt, se, arm)]
            p = tab[(tgt, se, "A1-P1")]
            body.append(
                f"| T### | INJURY_REASON {tgt} {se} {arm} (n {x['n_rows']}) | d {_f(x['dcrps'], 4, True)} CI [{_f(x['ci_lo'], 4, True)}, {_f(x['ci_hi'], 4, True)}] p_bh {_f(x.get('p_bh'), 3)}; A1-P1 {_f(p['dcrps'], 4, True)} |"
            )
    for tgt in ("t1", "pts", "min"):
        v = r["rules"][tgt]
        body.append(
            f"| T### | INJURY_REASON {tgt} verdict | pass {v['pass']}; rule1 {v['rule1']}; beats placebo {v['A1_beats_P1_by_margin_2024']}; slices worse {len(v['slices_worse'])} |"
        )
    body.append(
        f"| T### | INJURY_REASON close (no pass on 2023) | {r['rules']['close_no_pass_2023']} |"
    )
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--db", default="nba.duckdb")
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.doc, a.sha, a.allow_draft)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "gate":
        with HeavyLock():
            res = run_gate(a.db, a.doc, sha, a.allow_draft)
        print(
            json.dumps({k: res[k] for k in ("sha256", "tests", "gate_ok")}, indent=1, default=_jd)
        )
        return 0 if res["gate_ok"] else 1
    with HeavyLock():
        if a.stage == "smoke":
            run_smoke(a.db, sha)
        elif a.stage == "run":
            g = run_gate(a.db, a.doc, sha, False)
            if not g["gate_ok"]:
                print("GATE FAILED: stop, no model result", flush=True)
                return 2
            res = run_arms(a.db, sha)
            REPORT_MD.write_text(render_report())
            write_ledger()
            print(json.dumps(res["rules"], indent=1, default=_jd))
        else:
            REPORT_MD.write_text(render_report())
            write_ledger()
            print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
