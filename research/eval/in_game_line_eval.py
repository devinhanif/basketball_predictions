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
import datetime as dt
import json
import sys
import time
from collections.abc import Callable
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
# Amendment 3 (M3): the re-expression control M0 sees only what B0 sees, per checkpoint and line
FEATURES_M0 = ["b0_logit", "e", "line", "need"]
MONOTONE_M0 = {"need": -1, "b0_logit": 1}


FINALS_SQL = (
    "select p.game_id, p.player_id, p.team_id, g.game_date, p.minutes, p.pts, p.reb, p.ast, p.fg3m from player_game_stats p "
    "join games g on g.game_id = p.game_id where g.season = ? and g.season <= 2024 and p.game_id like '002%' and p.minutes > 0"
)
FINALS_DTYPES = {
    "game_id": pl.Utf8,
    "player_id": pl.Int32,
    "team_id": pl.Int32,
    "game_date": pl.Date,
    "minutes": pl.Float64,
    "pts": pl.Int64,
    "reb": pl.Int64,
    "ast": pl.Int64,
    "fg3m": pl.Int64,
}


def _finals_frame(df: pl.DataFrame) -> pl.DataFrame:
    return df.select([pl.col(c).cast(t) for c, t in FINALS_DTYPES.items()])


def read_finals(root: Path, seasons: tuple[int, ...], schema_only: bool = False) -> pl.DataFrame:
    """Final box-score lines of played regular-season games (the OUTCOME; first called after ``verify_inputs``). ``season <= 2024`` in the SQL.
    ``schema_only`` appends ``limit 0``: no row is read, only the column types (the dryrun's wiring check of this very query)."""
    out = []
    for s in seasons:
        rule.check_season(s)
        c = duckdb.connect(str(feat.db_path(s, root)), read_only=True)
        try:
            df = c.execute(FINALS_SQL + (" limit 0" if schema_only else ""), [s]).pl()
        finally:
            c.close()
        out.append(_finals_frame(df))
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
    if "line" in df.columns:
        df = df.with_columns((pl.col("line") - pl.col("cur")).alias("need"))
    return df.with_columns(
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


def fit_prob_stage(
    x: np.ndarray,
    y: np.ndarray,
    seed: int = LGBM_SEED,
    features: list[str] | None = None,
    monotone: dict[str, int] | None = None,
) -> Any:
    """Stage B: binary model on the state; monotone in need, pregame mean, predicted remaining minutes and B0's logit. With ``features`` /
    ``monotone`` it is the same learner on another column set (M0)."""
    import lightgbm as lgb

    mono = [
        (monotone if monotone is not None else MONOTONE_B).get(c, 0)
        for c in (features or FEATURES_B)
    ]
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


def per_checkpoint_platt(
    p_tr: np.ndarray, y_tr: np.ndarray, e_tr: np.ndarray, p_te: np.ndarray, e_te: np.ndarray
) -> np.ndarray:
    """Amendment 3 (M4): one 2-parameter recalibration per checkpoint (7 x 2 parameters), fitted on training rows only."""
    out = np.empty(len(p_te))
    for e in rule.CHECKPOINTS:
        tr, te = e_tr == e, e_te == e
        if te.any():
            ab = rule.platt_fit(p_tr[tr], y_tr[tr]) if tr.sum() > 50 else (0.0, 1.0)
            out[te] = rule.platt_apply(p_te[te], ab)
    return out


def arm_probs(
    train: pl.DataFrame, test: pl.DataFrame, stat: str, stage_a: Any
) -> dict[str, np.ndarray]:
    """Probabilities on ``test``; every calibration and model is fitted on ``train`` only.
    ``b0raw``: B0 as defined (no recalibration); ``b0`` / ``b1``: pooled 2-parameter recalibration; ``b0e`` / ``b1e``: recalibration per checkpoint;
    ``m``: arm M (+ pooled recalibration); ``m0``: the re-expression control (same learner on b0_logit, e, line, need only)."""
    y_tr = train["y"].to_numpy().astype(float)
    parts = {}
    for name, df in (("train", train), ("test", test)):
        d = add_derived(df)
        d = d.with_columns(
            pl.Series("rem_min_hat", stage_a.predict(d.select(FEATURES_A).to_numpy()))
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
    tr, te = parts["train"], parts["test"]
    out: dict[str, np.ndarray] = {"b0raw": te["b0"].to_numpy()}
    for k in ("b0", "b1"):
        out[k] = rule.platt_apply(te[k].to_numpy(), rule.platt_fit(tr[k].to_numpy(), y_tr))
        out[k + "e"] = per_checkpoint_platt(
            tr[k].to_numpy(), y_tr, tr["e"].to_numpy(), te[k].to_numpy(), te["e"].to_numpy()
        )
    for name, feats, mono in (("m", FEATURES_B, MONOTONE_B), ("m0", FEATURES_M0, MONOTONE_M0)):
        mdl = fit_prob_stage(tr.select(feats).to_numpy(), y_tr, features=feats, monotone=mono)
        raw = mdl.predict_proba(te.select(feats).to_numpy())[:, 1]
        ab = rule.platt_fit(mdl.predict_proba(tr.select(feats).to_numpy())[:, 1], y_tr)
        out[name] = rule.platt_apply(raw, ab)
    return out


# --------------------------------------------------------------------------- declared input digests
def compute_input_digests(root: Path) -> dict[str, str]:
    """Digests of every input a number depends on: games, stints and the derived state per season, the stored A0 files, the final box-score table
    (S1: ``PIN_FINALS_s`` = sha of ``read_finals`` for 2018 (warm-up) to 2024; a MECHANICAL read -- the rows are hashed, never inspected or
    printed), the libraries."""
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
    for s in (2018, *rule.ALL_SEASONS):
        out[f"PIN_FINALS_{s}"] = feat.sha_frame(read_finals(root, (s,)))
    out.update(rule.versions())
    return out


def verify_inputs(decl: dict[str, str], actual: dict[str, str]) -> None:
    bad = rule.input_mismatches(decl, actual)
    if bad:
        raise SystemExit(
            "declared inputs differ from recomputed (refusing before any outcome is read):\n"
            + "\n".join(bad)
        )


# --------------------------------------------------------------------------- the pipeline: outcomes enter only through ``finals_fn``
FinalsFn = Callable[[Path, tuple[int, ...], dict[int, pl.DataFrame]], pl.DataFrame]
SLICES = {
    "blowout_q3q4": (pl.col("margin").abs() >= rule.BLOWOUT_MARGIN) & (pl.col("e") >= 30),
    "last6": pl.col("e") == 42,
}
CONTESTED = (0.1, 0.9)  # descriptive slice: raw B0 probability inside this interval
MAKE_BASELINES = {"b0": ("b0", "b0e"), "b1": ("b1", "b1e", "b1_a0")}


def load_states(root: Path, seasons: tuple[int, ...]) -> dict[int, pl.DataFrame]:
    """State frames WITH the stint-derived remaining-minutes target (label-free); one per season."""
    out = {}
    for s in seasons:
        t = time.time()
        out[s] = feat.state_frame(s, root, True)
        feat.log(f"state {s}: {out[s].height} rows ({time.time() - t:.1f}s)")
    return out


def synthetic_y(u: pl.DataFrame, stat: str, seed: int) -> pl.DataFrame:
    """Dryrun only (M5): an OUTCOME-BLIND label. y = 1[U < raw B0 probability] with ONE uniform per (player-game, checkpoint) shared by its lines
    (so y is monotone in the line), B0 evaluated at that unit's own state. Nothing after the checkpoint enters; raw B0 is the exact truth."""
    key = (
        u["game_id"] + "_" + u["player_id"].cast(pl.Utf8) + "_" + u["e"].cast(pl.Utf8)
    ).to_numpy()
    codes = feat.np_codes(key)
    rng = np.random.default_rng(seed + 100 * rule.STATS.index(stat))
    p = gate.b0_for(u, stat)
    y = (rng.random(int(codes.max()) + 1)[codes] < p).astype(float)
    return u.with_columns(pl.Series("y", y))


def stat_units(
    season: int,
    stat: str,
    states: dict[int, pl.DataFrame],
    rec: pl.DataFrame,
    finals: pl.DataFrame,
    synth_seed: int | None = None,
) -> pl.DataFrame:
    """Units of one stat and season with pregame mean, recent minutes, the outcome ``y`` (final >= line) and ``season``. Rows need n_prior >=
    MIN_PRIOR and a final box-score row (same rows for every arm). With ``synth_seed`` (dryrun) ``y`` is replaced by ``synthetic_y``."""
    u = feat.expand_units(states[season].drop("y_rem_min"), stat)
    r = rec.select(
        "game_id",
        "player_id",
        "n_prior",
        pl.col(f"m_{stat}").alias("pregame_mean"),
        pl.col("m_minutes").alias("rec_minutes"),
    )
    u = u.join(r, on=["game_id", "player_id"], how="inner", maintain_order="left").filter(
        pl.col("n_prior") >= MIN_PRIOR
    )
    f = finals.select("game_id", "player_id", pl.col(stat).alias("final"))
    u = u.join(f, on=["game_id", "player_id"], how="inner", maintain_order="left")
    u = u.with_columns(
        (pl.col("final") >= pl.col("line")).cast(pl.Float64).alias("y"),
        pl.lit(season).alias("season"),
    ).sort("game_id", "player_id", "e", "line")
    return synthetic_y(u, stat, synth_seed + season) if synth_seed is not None else u


def minutes_rows(
    seasons: tuple[int, ...], states: dict[int, pl.DataFrame], rec: pl.DataFrame
) -> pl.DataFrame:
    """Stage-A training rows: one per player-checkpoint (not per line)."""
    parts = []
    for s in seasons:
        st = states[s].join(
            rec.select("game_id", "player_id", "n_prior", pl.col("m_minutes").alias("rec_minutes")),
            on=["game_id", "player_id"],
            how="inner",
            maintain_order="left",
        )
        parts.append(
            add_derived(st.filter(pl.col("n_prior") >= MIN_PRIOR)).select(*FEATURES_A, "y_rem_min")
        )
    return pl.concat(parts)


def attach_a0(test: pl.DataFrame, root: Path, season: int, stat: str) -> pl.DataFrame:
    """Test rows that have a stored A0 row, with ``b1_a0`` (the stored grid truncated at the current stat)."""
    a0 = a0_tables(root, season, stat)
    j = test.join(a0, on=["game_id", "player_id"], how="inner", maintain_order="left")
    p = a0_prob(j["p_ge"].to_list(), j["line"].to_numpy(), j["cur"].to_numpy())
    return j.drop("p_ge").with_columns(pl.Series("b1_a0", p))


def score_fit(
    train_seasons: tuple[int, ...],
    test_season: int,
    stat: str,
    root: Path,
    states: dict[int, pl.DataFrame],
    rec: pl.DataFrame,
    finals: pl.DataFrame,
    stage_a: Any,
    synth_seed: int | None = None,
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    train = pl.concat([stat_units(s, stat, states, rec, finals, synth_seed) for s in train_seasons])
    test = attach_a0(
        stat_units(test_season, stat, states, rec, finals, synth_seed), root, test_season, stat
    )
    feat.log(
        f"{stat} {test_season}: train {train.height} rows, test {test.height} rows (A0-intersected)"
    )
    return test, arm_probs(train, test, stat, stage_a)


def delta_report(
    test: pl.DataFrame, p_m: np.ndarray, p_base: np.ndarray, floor: float
) -> dict[str, Any]:
    y = test["y"].to_numpy()
    d = rule.log_loss_rows(p_m, y) - rule.log_loss_rows(p_base, y)
    codes = gate.cluster_codes(test)
    r = rule.binding_ci(d, codes)
    r["n_rows"] = int(len(d))
    r["n_games"] = int(codes["game"].max()) + 1
    r["n_player_seasons"] = int(codes["player_season"].max()) + 1
    r["floor"] = floor
    return r


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    """Reliability table of M (deciles of the predicted probability): n, mean prediction, observed rate."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return [
        {
            "bin": int(b),
            "n": int((idx == b).sum()),
            "mean_pred": float(p[idx == b].mean()),
            "observed": float(y[idx == b].mean()),
        }
        for b in range(bins)
        if (idx == b).any()
    ]


def evaluate(test: pl.DataFrame, probs: dict[str, np.ndarray], stat: str) -> dict[str, Any]:
    """All paired contrasts of arm M on one scored season. Binding baselines (Amendment 3, M4): B0 = the better (lower mean log loss on these rows)
    of the pooled and the per-checkpoint recalibrated B0; B1 = the best of the pooled and per-checkpoint recalibrated recency NegBin and the stored
    grid. Raw B0 and every other baseline are reported."""
    y = test["y"].to_numpy()
    allp = {**probs, "b1_a0": test["b1_a0"].to_numpy()}
    ll = {k: float(np.mean(rule.log_loss_rows(v, y))) for k, v in allp.items()}
    bs = {k: float(np.mean(rule.brier_rows(v, y))) for k, v in allp.items()}
    bind = {k: min(names, key=lambda n: ll[n]) for k, names in MAKE_BASELINES.items()}
    f = rule.FLOORS[stat]
    res: dict[str, Any] = {
        "log_loss": ll,
        "brier": bs,
        "binding_baseline": bind,
        "n_rows": int(len(y)),
        "positive_rate": float(y.mean()),
    }
    res["vs_b0"] = delta_report(test, probs["m"], allp[bind["b0"]], f)
    res["vs_b1"] = delta_report(test, probs["m"], allp[bind["b1"]], f)
    res["vs_b0_raw"] = delta_report(test, probs["m"], probs["b0raw"], f)
    res["vs_m0"] = delta_report(test, probs["m"], probs["m0"], rule.M0_MARGIN)
    codes = gate.cluster_codes(test)
    lm = rule.log_loss_rows(probs["m"], y)
    d0 = lm - rule.log_loss_rows(allp[bind["b0"]], y)
    d1 = lm - rule.log_loss_rows(allp[bind["b1"]], y)
    res["top5_share"] = max(
        rule.top5_share(d0, codes["player_season"]), rule.top5_share(d1, codes["player_season"])
    )
    res["calibration_slope"] = rule.calibration_slope(probs["m"], y)
    res["p_vs_b0"] = rule.binding_p(d0, codes)
    res["p_vs_b1"] = rule.binding_p(d1, codes)
    dr = lm - rule.log_loss_rows(probs["b0raw"], y)
    ecol = test["e"].to_numpy()
    res["vs_b0_raw_by_e"] = {
        int(e): {
            "n": int((ecol == e).sum()),
            **{
                k: v
                for k, v in rule.binding_ci(
                    dr[ecol == e], {c: feat.np_codes(codes[c][ecol == e]) for c in codes}
                ).items()
                if k in ("point", "lo", "hi", "half")
            },
        }
        for e in rule.CHECKPOINTS
        if (ecol == e).any()
    }
    e42 = res["vs_b0_raw_by_e"].get(42)
    res["e42_invariant"] = (
        None if e42 is None else {**e42, "ok": bool(e42["point"] >= -2.0 * e42["half"])}
    )
    sl: dict[str, Any] = {}
    masks: dict[str, np.ndarray] = {
        name: test.select(expr.alias("m"))["m"].to_numpy() for name, expr in SLICES.items()
    }
    masks["contested"] = (probs["b0raw"] >= CONTESTED[0]) & (probs["b0raw"] <= CONTESTED[1])
    masks["starters"] = (test["starter"] == 1).to_numpy()
    for name, mask in masks.items():
        if mask.sum() == 0:
            sl[name] = {"n": 0, "worst_delta": float("inf")}
            continue
        sub = {k: v[mask] for k, v in codes.items()}
        r0, r1 = rule.binding_ci(d0[mask], sub), rule.binding_ci(d1[mask], sub)
        sl[name] = {
            "n": int(mask.sum()),
            "vs_b0": r0,
            "vs_b1": r1,
            "worst_delta": max(r0["point"], r1["point"]),
            "criterion": name in SLICES,
        }
    res["slices"] = sl
    res["reliability_m"] = reliability(probs["m"], y)
    cells = []
    bucket = test.with_columns(
        ((pl.col("e") - 1) // 12).alias("q"),
        (pl.col("margin").abs() >= rule.BLOWOUT_MARGIN).alias("blow"),
    )
    for (q, blow), _g in bucket.group_by(["q", "blow"], maintain_order=True):
        idx = np.where((bucket["q"] == q).to_numpy() & (bucket["blow"] == blow).to_numpy())[0]
        cells.append(
            {
                "bucket": rule.BUCKETS[int(q)],
                "blowout": bool(blow),
                "n": int(len(idx)),
                "m_minus_b0": float(d0[idx].mean()),
                "m_minus_b1": float(d1[idx].mean()),
                "m_minus_b0_raw": float(dr[idx].mean()),
            }
        )
    res["cells"] = sorted(cells, key=lambda c: (c["bucket"], c["blowout"]))
    return res


def stint_vs_box(states: dict[int, pl.DataFrame], finals: pl.DataFrame) -> dict[str, Any]:
    """S6: stint regulation minutes (minutes at e = 42 plus the minutes after it) against box minutes, non-overtime games only (team box minutes within
    1 of 240). Reported per season: the share of player-games within 1 minute. Replaces the by-construction 'stints tile' check as the identity check."""
    out = {}
    fin = finals.select("game_id", "player_id", "team_id", pl.col("minutes").alias("box"))
    tot = fin.group_by("game_id", "team_id").agg(pl.col("box").sum().alias("tm"))
    for s, st in states.items():
        reg = st.filter(pl.col("e") == 42).select(
            "game_id",
            "team_id",
            "player_id",
            (pl.col("minutes") + pl.col("y_rem_min")).alias("stint"),
        )
        j = reg.join(fin, on=["game_id", "team_id", "player_id"], how="inner").join(
            tot, on=["game_id", "team_id"], how="inner"
        )
        j = j.filter((pl.col("tm") - 240.0).abs() <= 1.0)
        out[str(s)] = {
            "n": j.height,
            "within_1_min": float(np.mean(np.abs((j["stint"] - j["box"]).to_numpy()) <= 1.0))
            if j.height
            else None,
        }
    return out


def run_pipeline(
    root: Path,
    finals_fn: FinalsFn,
    sha: str | None,
    seasons: tuple[int, ...] = rule.ALL_SEASONS,
    stats: tuple[str, ...] = rule.STATS,
    synth_seed: int | None = None,
    warm_states: bool = False,
) -> dict[str, Any]:
    """The whole test. ``synth_seed`` (dryrun only) replaces every label by an outcome-blind draw; ``warm_states`` also builds the 2018 state so
    the dryrun's synthetic provider can produce 2018 finals (the recency warm-up). In a dryrun ``force`` scoring of 2024 happens whatever the
    select result is; in the real run a failed select criterion means 2024 is not scored."""
    t0 = time.time()
    res: dict[str, Any] = {
        "rule": rule.ID,
        "sha": sha,
        "started": dt.datetime.now().isoformat(),
        "versions": rule.versions(),
        "stats": {},
    }
    states = load_states(root, ((2018,) if warm_states else ()) + seasons)
    finals = finals_fn(root, (2018, *seasons), states)
    states = {s: v for s, v in states.items() if s in seasons}
    rec = recency(finals)
    feat.log(f"finals {finals.height} rows; recency {rec.height} rows")
    res["stint_vs_box"] = stint_vs_box(states, finals)
    sel_train = tuple(s for s in rule.TRAIN_SEASONS if s in seasons)
    rep_train = (*sel_train, rule.SELECT)
    stage_sel = fit_minutes_stage(minutes_rows(sel_train, states, rec))
    stage_rep = fit_minutes_stage(minutes_rows(rep_train, states, rec))
    feat.log(f"stage A fitted ({time.time() - t0:.0f}s)")
    force = synth_seed is not None
    pts_select: dict[str, Any] | None = None
    for stat in stats:
        entry: dict[str, Any] = {}
        if stat == rule.PRIMARY:
            test, probs = score_fit(
                sel_train, rule.SELECT, stat, root, states, rec, finals, stage_sel, synth_seed
            )
            pts_select = evaluate(test, probs, stat)
            entry["select_2023"] = pts_select
            feat.log(
                f"pts select: M-B0 {pts_select['vs_b0']['point']:.5f}, M-B1 {pts_select['vs_b1']['point']:.5f} ({time.time() - t0:.0f}s)"
            )
        sel_ok = stat != rule.PRIMARY or (
            pts_select is not None
            and pts_select["vs_b0"]["point"] <= -rule.FLOORS[stat]
            and pts_select["vs_b1"]["point"] <= -rule.FLOORS[stat]
        )
        if stat == rule.PRIMARY and not sel_ok and not force:
            entry["report_2024"] = "not scored: the 2023 select criteria (a) failed"
        else:
            test, probs = score_fit(
                rep_train, rule.REPORT, stat, root, states, rec, finals, stage_rep, synth_seed
            )
            entry["report_2024"] = evaluate(test, probs, stat)
            feat.log(
                f"{stat} report: M-B0 {entry['report_2024']['vs_b0']['point']:.5f} ({time.time() - t0:.0f}s)"
            )
        res["stats"][stat] = entry
        del test, probs
    pf = {str(s): feat.planted_future(s, root) for s in (rule.SELECT, rule.REPORT)}
    tr = {str(s): feat.truncation_identity(s, root) for s in (rule.SELECT, rule.REPORT)}
    pf_ok = all(v["identical_before_cut"] and v["later_rows_differ"] for v in pf.values())
    tr_ok = all(v["identical"] for v in tr.values())
    res["planted_future"], res["truncation_identity"] = pf, tr
    prim = res["stats"].get(rule.PRIMARY)
    if prim and isinstance(prim.get("report_2024"), dict) and pts_select is not None:
        r = prim["report_2024"]
        res["primary"] = rule.primary_verdict(
            pts_select["vs_b0"]["point"], pts_select["vs_b1"]["point"], r["vs_b0"], r["vs_b1"], r["top5_share"], r["calibration_slope"],
            {k: v["worst_delta"] for k, v in r["slices"].items() if v.get("criterion")}, r["vs_m0"], pf_ok, tr_ok,
        )  # fmt: skip
    sec = [
        s
        for s in rule.SECONDARY
        if s in res["stats"]
        and isinstance(res["stats"][s].get("report_2024"), dict)
        and (s != "ast" or tr_ok)
    ]
    if sec:
        pv = [
            max(
                res["stats"][s]["report_2024"]["p_vs_b0"], res["stats"][s]["report_2024"]["p_vs_b1"]
            )
            for s in sec
        ]
        rej = rule.bh_reject(pv)
        res["secondary"] = {
            s: {
                "p_binding_worse_of_two_baselines": p,
                "bh_reject": r,
                "point_vs_b0": res["stats"][s]["report_2024"]["vs_b0"]["point"],
                "point_vs_b1": res["stats"][s]["report_2024"]["vs_b1"]["point"],
                "claim": bool(r and max(res["stats"][s]["report_2024"]["vs_b0"]["point"], res["stats"][s]["report_2024"]["vs_b1"]["point"]) <= -rule.FLOORS[s]),
            }
            for s, p, r in zip(sec, pv, rej, strict=True)
        }  # fmt: skip
    res["wall_seconds"] = time.time() - t0
    return res


# --------------------------------------------------------------------------- synthetic provider (dryrun only)
def synthetic_finals(
    root: Path,
    seasons: tuple[int, ...],
    states: dict[int, pl.DataFrame],
    seed: int = 20261021,
) -> pl.DataFrame:
    """Seeded presence and recency source for the dryrun (M5, S7). One synthetic final per player-game and stat: current stat at the player's FIRST
    checkpoint plus a draw from B0's own negative binomial at that checkpoint's state, and nothing after it -- no production beyond what the first
    checkpoint's state already is. Seasons are those with a state, 2018 included (the warm-up path). The labels of the dryrun are NOT these finals
    (``synthetic_y`` draws them per unit); these finals only feed ``recency`` / ``n_prior``."""
    rng = np.random.default_rng(seed)
    parts = []
    for s in sorted(states):
        st = states[s]
        first = (
            st.sort("game_id", "team_id", "player_id", "e")
            .group_by("game_id", "team_id", "player_id", maintain_order=True)
            .first()
        )
        rem = np.asarray(
            rule.b0_remaining_minutes(
                first["e"].to_numpy(),
                first["minutes"].to_numpy(),
                first["starter"].to_numpy(),
                first["margin"].to_numpy(),
            )
        )
        cols: dict[str, Any] = {
            "game_id": first["game_id"],
            "player_id": first["player_id"],
            "team_id": first["team_id"],
            "game_date": first["game_date"],
            "minutes": pl.Series(first["minutes"].to_numpy() + rem),
        }
        for stat in rule.STATS:
            mu = np.maximum(
                np.asarray(
                    rule.b0_mean_remaining(
                        stat, first[stat].to_numpy(), first["minutes"].to_numpy(), rem
                    )
                ),
                rule.MEAN_FLOOR,
            )
            n = mu / (rule.VMR[stat] - 1.0)
            cols[stat] = pl.Series(
                first[stat].to_numpy() + rng.negative_binomial(n, n / (n + mu))
            ).cast(pl.Int64)
        parts.append(pl.DataFrame(cols))
    return _finals_frame(pl.concat(parts))


def real_finals(
    root: Path, seasons: tuple[int, ...], states: dict[int, pl.DataFrame]
) -> pl.DataFrame:
    return read_finals(root, seasons)


def _no_outcomes(*args: Any, **kwargs: Any) -> pl.DataFrame:
    raise RuntimeError("dryrun: the real outcome reader (read_finals) was called")


def check_finals_schema(root: Path, reader: Callable[..., pl.DataFrame]) -> None:
    for s in (2018, 2019, 2023):
        sch = reader(root, (s,), schema_only=True)
        assert sch.height == 0 and dict(sch.schema) == FINALS_DTYPES, dict(sch.schema)


def dryrun(
    root: Path,
    out: Path = Path("reports/prereg_in_game_line/dryrun.json"),
    seasons: tuple[int, ...] = rule.ALL_SEASONS,
    stats: tuple[str, ...] = rule.STATS,
) -> dict[str, Any]:
    """The ENTIRE path on the real units with OUTCOME-BLIND synthetic labels (M5). ``read_finals`` is replaced by a raiser for the duration (asserted
    before, during and after). Labels are drawn per unit from raw B0 at that unit's own state, so raw B0 is the exact truth: no honest model can
    beat it, and ``vs_b0_raw`` / ``e42_invariant`` are outcome-free leak tests of the pipeline (M must not beat raw B0 by more than twice its
    bootstrap half-width, at e = 42 and elsewhere). The primary verdict is expected to FAIL here."""
    global read_finals
    real = read_finals
    check_finals_schema(root, real)
    read_finals = _no_outcomes
    try:
        assert read_finals is _no_outcomes
        try:
            read_finals(root, (2023,))
            raise AssertionError("the outcome reader did not raise")
        except RuntimeError:
            pass
        res = run_pipeline(
            root, synthetic_finals, None, seasons, stats, synth_seed=20261022, warm_states=True
        )
    finally:
        read_finals = real
    assert read_finals is real
    res["synthetic_outcomes_only"] = True
    res["note"] = (
        "OUTCOME-BLIND synthetic labels: y ~ Bernoulli(raw B0 at the unit's own state); wiring and leak proof only; no number here is evidence about the model"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    return res


def run(sha: str, root: Path, out: Path = OUT) -> dict[str, Any]:
    """The frozen test. Refuses unless frozen and pinned and the inputs match; the outcomes are read only after that (``compute_input_digests``
    hashes the final table as a declared pin, the one mechanical read before the pipeline)."""
    rule.require_harness_pinned(sha)
    doc = (rule.ROOT / rule.DOC_REL).read_text()
    if rule.text_sha(doc) != sha:
        raise SystemExit("the rule text on disk does not hash to the frozen sha")
    verify_inputs(rule.declared(doc), compute_input_digests(root))
    res = run_pipeline(
        root, real_finals, sha
    )  # the pipeline's outcome reads happen only after every check above
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digests")
    sub.add_parser("dryrun")
    r = sub.add_parser("run")
    r.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    root = feat.data_root()
    if a.cmd == "digests":
        for k, v in compute_input_digests(root).items():
            print(f"{k}: {v}")
        return 0
    if a.cmd == "dryrun":
        res = dryrun(root)
        print(
            json.dumps(
                {
                    "wall_seconds": res["wall_seconds"],
                    "primary": res.get("primary"),
                    "secondary": res.get("secondary"),
                },
                indent=1,
                default=str,
            ),
            file=sys.stderr,
        )
        return 0
    run(a.sha, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
