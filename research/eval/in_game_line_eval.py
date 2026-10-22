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
    stage_a: Any,
    shuffle_seed: int | None = None,
) -> dict[str, np.ndarray]:
    """Raw and recalibrated probabilities of B0, B1 (recency) and M on ``test``; calibration and M fitted on ``train`` only. ``shuffle_seed`` permutes
    the training LABELS (the shuffled control)."""
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


# --------------------------------------------------------------------------- the pipeline (Amendment 2): outcomes enter only through ``finals_fn``
FinalsFn = Callable[[Path, tuple[int, ...], dict[int, pl.DataFrame]], pl.DataFrame]
SECONDARY_LABEL = "secondary"
SLICES = {
    "blowout_q3q4": (pl.col("margin").abs() >= rule.BLOWOUT_MARGIN) & (pl.col("e") >= 30),
    "last6": pl.col("e") == 42,
}


def load_states(root: Path, seasons: tuple[int, ...]) -> dict[int, pl.DataFrame]:
    """State frames WITH the stint-derived remaining-minutes target (label-free); one per season."""
    out = {}
    for s in seasons:
        t = time.time()
        out[s] = feat.state_frame(s, root, True)
        feat.log(f"state {s}: {out[s].height} rows ({time.time() - t:.1f}s)")
    return out


def stat_units(
    season: int, stat: str, states: dict[int, pl.DataFrame], rec: pl.DataFrame, finals: pl.DataFrame
) -> pl.DataFrame:
    """Units of one stat and season with pregame mean, recent minutes, the outcome ``y`` (final >= line) and ``season``. Rows need n_prior >= MIN_PRIOR and
    a final box-score row (same rows for every arm)."""
    u = feat.expand_units(states[season].drop("y_rem_min"), stat)
    r = rec.select(
        "game_id",
        "player_id",
        "n_prior",
        pl.col(f"m_{stat}").alias("pregame_mean"),
        pl.col("m_minutes").alias("rec_minutes"),
    )
    u = u.join(r, on=["game_id", "player_id"], how="inner").filter(pl.col("n_prior") >= MIN_PRIOR)
    f = finals.select("game_id", "player_id", pl.col(stat).alias("final"))
    u = u.join(f, on=["game_id", "player_id"], how="inner")
    return u.with_columns(
        (pl.col("final") >= pl.col("line")).cast(pl.Float64).alias("y"),
        pl.lit(season).alias("season"),
    ).sort("game_id", "player_id", "e", "line")


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
        )
        parts.append(
            add_derived(st.filter(pl.col("n_prior") >= MIN_PRIOR)).select(*FEATURES_A, "y_rem_min")
        )
    return pl.concat(parts)


def attach_a0(test: pl.DataFrame, root: Path, season: int, stat: str) -> pl.DataFrame:
    """Test rows that have a stored A0 row, with ``b1_a0`` (the stored grid truncated at the current stat)."""
    a0 = a0_tables(root, season, stat)
    j = test.join(a0, on=["game_id", "player_id"], how="inner")
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
    shuffle: bool = False,
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    train = pl.concat([stat_units(s, stat, states, rec, finals) for s in train_seasons])
    test = attach_a0(stat_units(test_season, stat, states, rec, finals), root, test_season, stat)
    feat.log(
        f"{stat} {test_season}: train {train.height} rows, test {test.height} rows (A0-intersected), shuffle={shuffle}"
    )
    probs = arm_probs(train, test, stat, stage_a, shuffle_seed=0 if shuffle else None)
    return test, probs


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


def evaluate(test: pl.DataFrame, probs: dict[str, np.ndarray], stat: str) -> dict[str, Any]:
    """All paired contrasts of arm M on one scored season. B1 = the better (lower mean log loss on these rows) of the recency NegBin and the stored grid."""
    y = test["y"].to_numpy()
    ll = {
        k: float(np.mean(rule.log_loss_rows(v, y)))
        for k, v in {**probs, "b1_a0": test["b1_a0"].to_numpy()}.items()
    }
    bs = {
        k: float(np.mean(rule.brier_rows(v, y)))
        for k, v in {**probs, "b1_a0": test["b1_a0"].to_numpy()}.items()
    }
    better = "b1" if ll["b1"] <= ll["b1_a0"] else "b1_a0"
    base = {"b0": probs["b0"], "b1": probs["b1"], "b1_a0": test["b1_a0"].to_numpy()}
    f = rule.FLOORS[stat]
    res: dict[str, Any] = {
        "log_loss": ll,
        "brier": bs,
        "better_b1": better,
        "n_rows": int(len(y)),
        "positive_rate": float(y.mean()),
    }
    res["vs_b0"] = delta_report(test, probs["m"], base["b0"], f)
    res["vs_b1"] = delta_report(test, probs["m"], base[better], f)
    res["vs_b1_other"] = delta_report(
        test, probs["m"], base["b1_a0" if better == "b1" else "b1"], f
    )
    codes = gate.cluster_codes(test)
    d0 = rule.log_loss_rows(probs["m"], y) - rule.log_loss_rows(base["b0"], y)
    d1 = rule.log_loss_rows(probs["m"], y) - rule.log_loss_rows(base[better], y)
    res["top5_share"] = max(
        rule.top5_share(d0, codes["player_season"]), rule.top5_share(d1, codes["player_season"])
    )
    res["calibration_slope"] = rule.calibration_slope(probs["m"], y)
    res["p_vs_b0"] = rule.binding_p(d0, codes)
    res["p_vs_b1"] = rule.binding_p(d1, codes)
    sl: dict[str, Any] = {}
    for name, expr in SLICES.items():
        mask = test.select(expr.alias("m"))["m"].to_numpy()
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
        }
    res["slices"] = sl
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
            }
        )
    res["cells"] = sorted(cells, key=lambda c: (c["bucket"], c["blowout"]))
    return res


def run_pipeline(
    root: Path,
    finals_fn: FinalsFn,
    sha: str | None,
    force_report: bool = False,
    seasons: tuple[int, ...] = rule.ALL_SEASONS,
    stats: tuple[str, ...] = rule.STATS,
) -> dict[str, Any]:
    t0 = time.time()
    res: dict[str, Any] = {
        "rule": rule.ID,
        "sha": sha,
        "started": dt.datetime.now().isoformat(),
        "versions": rule.versions(),
        "stats": {},
    }
    states = load_states(root, seasons)
    finals = finals_fn(root, (2018, *seasons), states)
    rec = recency(finals)
    feat.log(f"finals {finals.height} rows; recency {rec.height} rows")
    sel_train = tuple(s for s in rule.TRAIN_SEASONS if s in seasons)
    rep_train = (*sel_train, rule.SELECT)
    stage_sel = fit_minutes_stage(minutes_rows(sel_train, states, rec))
    stage_rep = fit_minutes_stage(minutes_rows(rep_train, states, rec))
    feat.log(f"stage A fitted ({time.time() - t0:.0f}s)")
    pts_select: dict[str, Any] | None = None
    for stat in stats:
        entry: dict[str, Any] = {}
        if stat == rule.PRIMARY:
            test, probs = score_fit(
                sel_train, rule.SELECT, stat, root, states, rec, finals, stage_sel
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
        if stat == rule.PRIMARY and not sel_ok and not force_report:
            entry["report_2024"] = "not scored: the 2023 select criteria (a) failed"
        else:
            test, probs = score_fit(
                rep_train, rule.REPORT, stat, root, states, rec, finals, stage_rep
            )
            entry["report_2024"] = evaluate(test, probs, stat)
            if stat == rule.PRIMARY:
                _, shuf = score_fit(
                    rep_train, rule.REPORT, stat, root, states, rec, finals, stage_rep, shuffle=True
                )
                y = test["y"].to_numpy()
                entry["shuffled_gain_vs_b0"] = float(
                    np.mean(rule.log_loss_rows(probs["b0"], y))
                    - np.mean(rule.log_loss_rows(shuf["m"], y))
                )
            feat.log(
                f"{stat} report: M-B0 {entry['report_2024']['vs_b0']['point']:.5f} ({time.time() - t0:.0f}s)"
            )
        res["stats"][stat] = entry
        del test, probs
    pf = {str(s): feat.planted_future(s, root) for s in (rule.SELECT, rule.REPORT)}
    pf_ok = all(v["identical_before_cut"] and v["later_rows_differ"] for v in pf.values())
    res["planted_future"] = pf
    prim = res["stats"].get(rule.PRIMARY)
    if prim and isinstance(prim.get("report_2024"), dict) and pts_select is not None:
        r = prim["report_2024"]
        res["primary"] = rule.primary_verdict(
            pts_select["vs_b0"]["point"], pts_select["vs_b1"]["point"], r["vs_b0"], r["vs_b1"], r["top5_share"], r["calibration_slope"],
            {k: v["worst_delta"] for k, v in r["slices"].items()}, prim["shuffled_gain_vs_b0"], pf_ok,
        )  # fmt: skip
    sec = [
        s
        for s in rule.SECONDARY
        if s in res["stats"] and isinstance(res["stats"][s].get("report_2024"), dict)
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


# --------------------------------------------------------------------------- synthetic outcomes (dryrun only)
def synthetic_finals(
    root: Path, seasons: tuple[int, ...], states: dict[int, pl.DataFrame], seed: int = 20261021
) -> pl.DataFrame:
    """Seeded final lines drawn from B0's own distribution at each player's LAST checkpoint (final = current at e = 42 + NegBin remaining), one coherent
    final per player-game and stat that can never be below any earlier current stat, so every line and checkpoint of a player-game is scored
    against the same final. Seasons without state (2018)
    contribute no rows. Not an outcome of any real game."""
    rng = np.random.default_rng(seed)
    parts = []
    for s in sorted(states):
        st = states[s]
        first = (
            st.sort("game_id", "team_id", "player_id", "e")
            .group_by("game_id", "team_id", "player_id", maintain_order=True)
            .last()
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
            draw = rng.negative_binomial(n, n / (n + mu))
            cols[stat] = pl.Series(first[stat].to_numpy() + draw).cast(pl.Int64)
        parts.append(pl.DataFrame(cols))
    return _finals_frame(pl.concat(parts))


def real_finals(
    root: Path, seasons: tuple[int, ...], states: dict[int, pl.DataFrame]
) -> pl.DataFrame:
    return read_finals(root, seasons)


def _no_outcomes(*args: Any, **kwargs: Any) -> pl.DataFrame:
    raise RuntimeError("dryrun: the real outcome reader (read_finals) was called")


def check_finals_schema(root: Path, reader: Callable[..., pl.DataFrame]) -> None:
    for s in (2019, 2023):
        sch = reader(root, (s,), schema_only=True)
        assert sch.height == 0 and dict(sch.schema) == FINALS_DTYPES, dict(sch.schema)


def dryrun(
    root: Path,
    out: Path = Path("reports/prereg_in_game_line/dryrun.json"),
    seasons: tuple[int, ...] = rule.ALL_SEASONS,
    stats: tuple[str, ...] = rule.STATS,
) -> dict[str, Any]:
    """The ENTIRE path on the real units with SYNTHETIC outcomes. ``read_finals`` is replaced by a raiser for the duration (asserted before, during
    and after); the pipeline gets its finals from ``synthetic_finals``. No frozen-rule check (nothing is claimed); ``force_report`` so the
    report fit runs whatever the select result is. Results are meaningless as science and are labelled so."""
    global read_finals
    real = read_finals
    # wiring check of the real reader's SQL and dtypes with ``limit 0`` (no row is read): one history season and one production season must give
    # exactly the dtypes the synthetic provider produces, so the joins in the real run cannot fail on a type mismatch
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
            root, synthetic_finals, None, force_report=True, seasons=seasons, stats=stats
        )
    finally:
        read_finals = real
    assert read_finals is real
    res["synthetic_outcomes_only"] = True
    res["note"] = (
        "SYNTHETIC outcomes drawn from B0's own distribution; wiring proof only; no number here is evidence about the model"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    return res


def run(sha: str, root: Path, out: Path = OUT) -> dict[str, Any]:
    """The frozen test. Refuses unless frozen and pinned and the inputs match; the outcomes are read only after that."""
    rule.require_harness_pinned(sha)
    doc = (rule.ROOT / rule.DOC_REL).read_text()
    if rule.text_sha(doc) != sha:
        raise SystemExit("the rule text on disk does not hash to the frozen sha")
    verify_inputs(rule.declared(doc), compute_input_digests(root))
    res = run_pipeline(
        root, real_finals, sha
    )  # first outcome read happens inside, after every check above
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
