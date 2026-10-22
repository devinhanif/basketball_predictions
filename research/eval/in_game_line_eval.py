# ruff: noqa: E501
"""IN_GAME_LINE_MODEL evaluation harness (rule: docs/prereg/IN_GAME_LINE_MODEL.md, DRAFT -- NOT FROZEN).

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.in_game_line_eval digests
    NBA_DATA_ROOT=... .venv/bin/python -W ignore -m research.eval.in_game_line_eval run --sha <sha256 of the frozen rule text>

``run`` REFUSES unless the rule is frozen (`prereg(IN_GAME_LINE_MODEL): <sha>` commit), the harness is pinned (`harness(...)` commit) and every
declared input digest equals its recomputed value; all of that happens BEFORE any final box score is read (``read_finals`` is first called after
``verify_inputs``). No arm is fitted on anything in this pass. Arms: B0 (linear pace, period-aware minutes), B1 (pre-game NegBin conditioned on the
current stat: recency mean, and the stored A0 grid truncated where stored), M (two-stage LightGBM: remaining minutes, then a monotone binary model
on the state with B0's logit as a feature). Every arm gets the same 2-parameter recalibration fitted on training rows only (the stored A0 grid is
production-calibrated and is scored with no recalibration, reported as its own baseline).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import in_game_line_features as feat
from research.eval import in_game_line_gate as gate
from research.eval import in_game_line_rule as rule

N_LEAVES = 8
N_TREES = 200
LEARNING_RATE = 0.05
MIN_DATA_IN_LEAF = 200
LGBM_SEED = 0
RECENCY_GAMES = 10
MIN_PRIOR = 5
OUT = Path("reports/prereg_in_game_line/results.json")
FEATURES_A = [
    "e",
    "minutes",
    "starter",
    "fouls",
    "margin",
    "abs_margin",
    "q4_rem",
    "blow_x_q4",
    "rec_minutes",
]
FEATURES_B = [
    "line",
    "need",
    "cur",
    "minutes",
    "e",
    "fouls",
    "margin",
    "abs_margin",
    "starter",
    "pregame_mean",
    "rem_min_hat",
    "q4_rem",
    "blow_x_q4",
    "absm_x_q4",
    "b0_logit",
]
MONOTONE_B = {"need": -1, "pregame_mean": 1, "rem_min_hat": 1, "b0_logit": 1}


def read_finals(root: Path, seasons: tuple[int, ...]) -> pl.DataFrame:
    """Final box-score lines of played regular-season games (the OUTCOME; first called after ``verify_inputs``). ``season <= 2024`` in the SQL."""
    out = []
    for s in seasons:
        rule.check_season(s)
        c = duckdb.connect(str(feat.db_path(s, root)), read_only=True)
        try:
            df = c.execute(
                "select p.game_id, p.player_id, p.team_id, g.game_date, p.minutes, p.pts, p.reb, p.ast, p.fg3m from player_game_stats p "
                "join games g on g.game_id = p.game_id where g.season = ? and g.season <= 2024 and p.game_id like '002%' and p.minutes > 0",
                [s],
            ).pl()
        finally:
            c.close()
        out.append(df)
    return pl.concat(out)


def recency(finals: pl.DataFrame) -> pl.DataFrame:
    """Per player-game: n_prior and the mean of each stat and of minutes over the previous RECENCY_GAMES played games (strictly earlier)."""
    f = finals.sort("player_id", "game_date")
    cols = [*rule.STATS, "minutes"]
    return (
        f.with_columns(
            [
                pl.col(c)
                .cast(pl.Float64)
                .shift(1)
                .rolling_mean(RECENCY_GAMES, min_samples=1)
                .over("player_id")
                .alias(f"m_{c}")
                for c in cols
            ]
            + [pl.col("game_id").cum_count().over("player_id").alias("n_prior")]
        )
        .with_columns((pl.col("n_prior") - 1).alias("n_prior"))
        .select("game_id", "player_id", "n_prior", *[f"m_{c}" for c in cols])
    )


def a0_tables(root: Path, season: int, stat: str) -> pl.DataFrame:
    rule.check_season(season)
    p = root / "reports" / "odds_history" / f"model_rows_{season}_{stat}.parquet"
    return pl.read_parquet(p, columns=["game_id", "player_id", "p_ge"])


def a0_prob(p_ge: list[list[float]], line: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """B1 from the stored grid: P(Y >= line) / P(Y >= cur) with p_ge[k-1] = P(Y >= k); beyond the stored width the survival is 0; cur <= 0 -> 1."""
    out = np.empty(len(line))
    for i, (v, L, c) in enumerate(zip(p_ge, line, cur, strict=True)):
        num = v[int(L) - 1] if int(L) - 1 < len(v) else 0.0
        den = 1.0 if c <= 0 else (v[int(c) - 1] if int(c) - 1 < len(v) else 0.0)
        out[i] = min(num / den, 1.0) if den > 1e-12 else 0.0
    return out


def add_derived(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        (pl.col("line") - pl.col("cur")).alias("need"),
        pl.col("margin").abs().alias("abs_margin"),
        (48 - pl.col("e")).clip(upper_bound=12).alias("q4_rem"),
    ).with_columns(
        ((pl.col("abs_margin") >= rule.BLOWOUT_MARGIN).cast(pl.Float64) * pl.col("q4_rem")).alias(
            "blow_x_q4"
        ),
        (pl.col("abs_margin") * pl.col("q4_rem") / 12.0).alias("absm_x_q4"),
    )


def fit_minutes_stage(train: pl.DataFrame, seed: int = LGBM_SEED) -> Any:
    """Stage A: remaining regulation minutes from the state (one row per player-checkpoint), LightGBM, fixed settings."""
    import lightgbm as lgb

    m = lgb.LGBMRegressor(
        n_estimators=N_TREES,
        learning_rate=LEARNING_RATE,
        num_leaves=N_LEAVES,
        min_child_samples=MIN_DATA_IN_LEAF,
        random_state=seed,
        deterministic=True,
        force_row_wise=True,
        verbose=-1,
        n_jobs=1,
    )
    m.fit(train.select(FEATURES_A).to_numpy(), train["y_rem_min"].to_numpy())
    return m


def fit_prob_stage(x: np.ndarray, y: np.ndarray, seed: int = LGBM_SEED) -> Any:
    """Stage B: binary model on the state; monotone in need, pregame mean, predicted remaining minutes and B0's logit."""
    import lightgbm as lgb

    mono = [MONOTONE_B.get(c, 0) for c in FEATURES_B]
    m = lgb.LGBMClassifier(
        n_estimators=N_TREES,
        learning_rate=LEARNING_RATE,
        num_leaves=N_LEAVES,
        min_child_samples=MIN_DATA_IN_LEAF,
        monotone_constraints=mono,
        random_state=seed,
        deterministic=True,
        force_row_wise=True,
        verbose=-1,
        n_jobs=1,
    )
    m.fit(x, y)
    return m


def b0_column(df: pl.DataFrame, stat: str) -> np.ndarray:
    return gate.b0_for(df, stat)


def arm_probs(
    train: pl.DataFrame,
    test: pl.DataFrame,
    stat: str,
    minutes_train: pl.DataFrame,
    shuffle_seed: int | None = None,
) -> dict[str, np.ndarray]:
    """Raw and recalibrated probabilities of B0, B1 (recency) and M on ``test``; calibration and M fitted on ``train`` only. ``shuffle_seed`` permutes
    the training LABELS (the shuffled control)."""
    stage_a = fit_minutes_stage(add_derived(minutes_train))
    out: dict[str, np.ndarray] = {}
    y_tr = train["y"].to_numpy().astype(float)
    if shuffle_seed is not None:
        y_tr = np.random.default_rng(shuffle_seed).permutation(y_tr)
    parts = {}
    for name, df in (("train", train), ("test", test)):
        d = add_derived(df).with_columns(
            pl.Series("rem_min_hat", stage_a.predict(add_derived(df).select(FEATURES_A).to_numpy()))
        )
        d = d.with_columns(
            pl.Series("b0", b0_column(d, stat)),
            pl.Series(
                "b1",
                rule.b1_prob(
                    stat, d["line"].to_numpy(), d["cur"].to_numpy(), d["pregame_mean"].to_numpy()
                ),
            ),
        )
        d = d.with_columns(pl.Series("b0_logit", rule.logit(d["b0"].to_numpy())))
        parts[name] = d
    cal = {k: rule.platt_fit(parts["train"][k].to_numpy(), y_tr) for k in ("b0", "b1")}
    for k in ("b0", "b1"):
        out[k] = rule.platt_apply(parts["test"][k].to_numpy(), cal[k])
    mdl = fit_prob_stage(parts["train"].select(FEATURES_B).to_numpy(), y_tr)
    raw = mdl.predict_proba(parts["test"].select(FEATURES_B).to_numpy())[:, 1]
    ab = rule.platt_fit(mdl.predict_proba(parts["train"].select(FEATURES_B).to_numpy())[:, 1], y_tr)
    out["m"] = rule.platt_apply(raw, ab)
    return out


# --------------------------------------------------------------------------- declared input digests
def compute_input_digests(root: Path) -> dict[str, str]:
    """Label-free digests of every input a number depends on: games, stints and the derived state per season, the stored A0 files, the libraries."""
    out: dict[str, str] = {}
    for s in rule.ALL_SEASONS:
        games = feat.read_games(s, root)
        stints = feat.read_stints(s, root)
        state = feat.state_frame(s, root, False, games, None, stints)
        out[f"PIN_GAMES_{s}"] = feat.sha_frame(games)
        out[f"PIN_STINTS_{s}"] = feat.sha_frame(
            stints.with_columns(
                pl.col("players")
                .cast(pl.List(pl.Int32))
                .list.sort()
                .list.eval(pl.element().cast(pl.Utf8))
                .list.join("-")
            )
        )
        out[f"PIN_STATE_{s}"] = feat.sha_frame(state)
    for s in (rule.SELECT, rule.REPORT):
        for st in rule.STATS:
            out[f"PIN_A0_{s}_{st}"] = rule.file_sha256(
                root / "reports" / "odds_history" / f"model_rows_{s}_{st}.parquet"
            )
    out.update(rule.versions())
    return out


def verify_inputs(decl: dict[str, str], actual: dict[str, str]) -> None:
    bad = rule.input_mismatches(decl, actual)
    if bad:
        raise SystemExit(
            "declared inputs differ from recomputed (refusing before any outcome is read):\n"
            + "\n".join(bad)
        )


def run(sha: str, root: Path, out: Path = OUT) -> dict[str, Any]:
    """The frozen test. Refuses unless frozen and pinned and the inputs match; the outcomes are read only after that. Not exercised on real data
    in the drafting pass (unit tests use synthetic frames)."""
    rule.require_harness_pinned(sha)
    doc = (rule.ROOT / rule.DOC_REL).read_text()
    if rule.text_sha(doc) != sha:
        raise SystemExit("the rule text on disk does not hash to the frozen sha")
    verify_inputs(rule.declared(doc), compute_input_digests(root))
    raise SystemExit(
        "IN_GAME_LINE_MODEL: unit assembly (join finals, recency and the stored A0 grid onto the state units, score, bootstrap) is not "
        "implemented in the drafting pass; the pieces (arm_probs, recency, a0_prob, rule.primary_verdict) are. Complete and re-pin before freezing."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digests")
    r = sub.add_parser("run")
    r.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    root = feat.data_root()
    if a.cmd == "digests":
        for k, v in compute_input_digests(root).items():
            print(f"{k}: {v}")
        return 0
    run(a.sha, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
