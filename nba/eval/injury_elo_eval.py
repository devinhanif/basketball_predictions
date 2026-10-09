"""Walk-forward evaluation of injury-adjusted MOV-Elo vs plain MOV-Elo.

PRE-REGISTERED DECISION RULE (written before any real-data run)
----------------------------------------------------------------
Candidate: ``injury_elo`` = frozen MOV-Elo + fitted ``b`` (OUT value) and
``g`` (DOUBTFUL value, weight 0.5) from the latest official report at least
60 minutes before the 19:00 ET tip proxy. Comparator: ``rung0_mov_elo`` with
the same frozen params. Scope: out-of-fold games of seasons 2022-2024 only
(season 2025 is the burned holdout: never loaded, tuned or scored here).

KEEP iff BOTH:
  1. Overall (all scored games; games without a usable report fall back to
     the MOV-Elo logit) paired log-loss delta (candidate - MOV-Elo) has a
     95% game-level bootstrap CI entirely below 0.
  2. No slice is worse than MOV-Elo by more than 0.005 log loss (point
     delta), over slices: rotation players OUT = 0 / 1 / 2+ (by the
     official report) and season phase = early (either team in its first 15
     games) / rest. Slices with n < 100 are reported but cannot veto.
Otherwise: reject (negative result is logged as such). The ``oracle``
variant (post-hoc box-score absences) is an upper bound only and can never
satisfy this rule. The primary candidate is the 2-coefficient model; the
OUT-only variant is a sensitivity row and cannot be substituted after the
fact.

Maintainer command (real DB, after the backfill finishes)::

    uv run python -m nba.eval.injury_elo_eval --db nba.duckdb \\
        --config configs/injury_elo.yaml --out-dir data/injury_elo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.metrics import (
    brier_score,
    calibration_curve,
    log_loss,
    paired_bootstrap_compare,
)
from nba.models.injury_elo import (
    InjuryFeatureConfig,
    ValueConfig,
    build_injury_features,
    fit_injury_coefs,
    load_games_frame,
    load_stats_frame,
    sequential_elo_logits,
    sigmoid,
)

SLICE_VETO = 0.005
SLICE_MIN_N = 100
EARLY_GAMES = 15


def _ece(y: np.ndarray, p: np.ndarray) -> float:
    return float(calibration_curve(y, p).ece)


def load_config(path: str | Path) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    return cfg


def feature_config_from(cfg: dict[str, Any]) -> InjuryFeatureConfig:
    v = ValueConfig(**cfg["value"])
    r = cfg["report"]
    f = cfg["fit"]
    return InjuryFeatureConfig(
        value=v,
        tipoff_hour_et=float(r["tipoff_hour_et"]),
        lead_minutes=int(r["lead_minutes"]),
        tip_source=str(r.get("tip_source", "proxy19")),
        doubtful_weight=float(r["doubtful_weight"]),
        report_table=str(r["backfill_table"]),
        rotation_min_avg=float(f["rotation_min_avg"]),
        rotation_min_games=int(f["rotation_min_games"]),
    )


def walk_forward_probs(
    frame: pl.DataFrame,
    offset: np.ndarray,
    feature_cols: tuple[str, ...],
    ridge_lambda: float,
    min_signal_games: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Month-block walk-forward: coefs for a block use ONLY games dated before it.

    ``frame`` must be sorted by (game_date, game_id) with ``y`` and the
    feature columns; ``offset`` is the aligned MOV-Elo logit. Until
    ``min_signal_games`` earlier games carry a nonzero signal the coefs stay 0
    (prediction == MOV-Elo).
    """
    dates = frame["game_date"].to_list()
    y = frame["y"].to_numpy().astype(float)
    x = frame.select(list(feature_cols)).to_numpy()
    blocks = [f"{d.year}-{d.month:02d}" for d in dates]
    p = np.empty(len(y))
    log: list[dict[str, Any]] = []
    for blk in dict.fromkeys(blocks):
        idx = np.array([i for i, b in enumerate(blocks) if b == blk])
        start = dates[int(idx[0])]
        train = np.array([i for i, d in enumerate(dates) if d < start], dtype=int)
        signal = int((np.abs(x[train]).sum(axis=1) > 0).sum()) if train.size else 0
        coef = (
            fit_injury_coefs(x[train], offset[train], y[train], ridge_lambda)
            if signal >= min_signal_games
            else np.zeros(x.shape[1])
        )
        p[idx] = sigmoid(offset[idx] + x[idx] @ coef)
        log.append(
            {
                "block": blk,
                "n_train": int(train.size),
                "signal": signal,
                "coef": [float(c) for c in coef],
            }
        )
    return np.clip(p, 0.02, 0.98), log


def _cmp(
    y: np.ndarray, p_new: np.ndarray, p_base: np.ndarray, n_boot: int, seed: int
) -> dict[str, Any]:
    out: dict[str, Any] = {"n": int(len(y))}
    for name, fn, nb in (
        ("log_loss", log_loss, n_boot),
        ("brier", brier_score, n_boot),
        ("ece", _ece, max(200, n_boot // 4)),
    ):
        c = paired_bootstrap_compare(y, p_new, p_base, fn, name, n_boot=nb, seed=seed)
        out[name] = {
            "cand": c.a_point,
            "base": c.b_point,
            "delta": c.delta.point,
            "lo": c.delta.lo,
            "hi": c.delta.hi,
        }
    return out


def game_numbers(games: pl.DataFrame) -> dict[str, int]:
    """min(home, away) team game number within the season (1-based, as-of count+1)."""
    cnt: dict[tuple[int, int], int] = {}
    out: dict[str, int] = {}
    for gid, season, h, a in (
        games.sort(["game_date", "game_id"])
        .select(["game_id", "season", "home_team", "away_team"])
        .iter_rows()
    ):
        nh = cnt.get((season, h), 0) + 1
        na = cnt.get((season, a), 0) + 1
        out[str(gid)] = min(nh, na)
        cnt[(season, h)] = nh
        cnt[(season, a)] = na
    return out


def decide(overall: dict[str, Any], slices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply the pre-registered rule to the candidate's comparison dicts."""
    c1 = bool(overall["log_loss"]["hi"] < 0.0)
    vetoes = [
        k
        for k, v in slices.items()
        if v["n"] >= SLICE_MIN_N and v["log_loss"]["delta"] > SLICE_VETO
    ]
    return {
        "overall_ci_below_zero": c1,
        "slice_vetoes": vetoes,
        "verdict": "KEEP" if (c1 and not vetoes) else "REJECT",
    }


def run_injury_elo_eval(
    con: duckdb.DuckDBPyConnection,
    cfg: dict[str, Any],
    out_dir: str | Path | None = None,
    confirmatory_holdout: int | None = None,
    preregistered: bool = False,
) -> dict[str, Any]:
    """Run the full OOF comparison on ``con`` (needs the report table attached).

    ``confirmatory_holdout=<season>`` (with ``preregistered=True``) is the ONLY
    way to touch the holdout season: same frozen procedure, but history is
    loaded through that season and ONLY that season is scored (each monthly
    refit still uses all games strictly before the month). Default path
    rejects the holdout season.
    """
    holdout = int(cfg["holdout_season"])
    seasons = [int(s) for s in cfg["oof_seasons"]]
    if holdout in seasons or max(seasons) >= holdout:
        raise ValueError("oof_seasons must all be strictly before the burned holdout season")
    if confirmatory_holdout is not None:
        if not preregistered:
            raise ValueError("confirmatory holdout requires --i-have-preregistered")
        if int(confirmatory_holdout) != holdout:
            raise ValueError("confirmatory holdout must equal the configured holdout_season")
        load_through = holdout
        seasons = [holdout]
    else:
        load_through = max(seasons)
    fcfg = feature_config_from(cfg)
    elo_params = {
        k: float(v)
        for k, v in load_config(cfg["mov_elo_config"]).items()
        if k in ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div")
    }
    games = load_games_frame(con, load_through)
    stats = load_stats_frame(con, load_through)
    if confirmatory_holdout is None:
        assert int(games["season"].max()) < holdout  # type: ignore[arg-type]
    feats = build_injury_features(con, games, stats, fcfg)
    elo_logit = sequential_elo_logits(games, elo_params)
    frame = games.with_columns(pl.Series("elo_logit", elo_logit)).join(
        feats, on="game_id", how="left"
    )
    frame = frame.sort(["game_date", "game_id"])
    offset = frame["elo_logit"].to_numpy()
    fit_cfg = cfg["fit"]
    lam, min_sig = float(fit_cfg["ridge_lambda"]), int(fit_cfg["min_signal_games"])

    p_base = sigmoid(offset)
    variants: dict[str, tuple[np.ndarray, list[dict[str, Any]]]] = {
        "injury_elo": walk_forward_probs(frame, offset, ("d_out", "d_doubt"), lam, min_sig),
        "injury_elo_out_only": walk_forward_probs(frame, offset, ("d_out",), lam, min_sig),
        "ORACLE_not_a_candidate": walk_forward_probs(frame, offset, ("d_oracle",), lam, min_sig),
    }

    # score: OOF seasons, dropping the first date (no prior games), as the fold harness does
    first_date = frame["game_date"].min()
    keep = (frame["season"].is_in(seasons) & (frame["game_date"] > first_date)).to_numpy()
    y = frame["y"].to_numpy().astype(float)[keep]
    base = np.clip(p_base, 0.0, 1.0)[keep]
    gn = game_numbers(games)
    early = np.array([gn[g] <= EARLY_GAMES for g in frame["game_id"].to_list()])[keep]
    nrot = frame["n_rot_out"].to_numpy()[keep]
    has_rep = frame["has_report"].to_numpy()[keep]
    slice_masks = {
        "rot_out=0": nrot == 0,
        "rot_out=1": nrot == 1,
        "rot_out=2+": nrot >= 2,
        "early(<=15g)": early,
        "rest": ~early,
        "has_report": has_rep,
    }
    n_boot = int(cfg["bootstrap"]["n_boot"])
    seed = int(cfg["bootstrap"]["seed"])

    results: dict[str, Any] = {
        "n_scored": int(keep.sum()),
        "seasons": seasons,
        "report_coverage": float(has_rep.mean()),
        "mov_elo": {
            "log_loss": float(log_loss(y, base)),
            "brier": float(brier_score(y, base)),
            "ece": _ece(y, base),
        },
        "variants": {},
    }
    for name, (p_all, coef_log) in variants.items():
        p = p_all[keep]
        res: dict[str, Any] = {
            "log_loss": float(log_loss(y, p)),
            "brier": float(brier_score(y, p)),
            "ece": _ece(y, p),
            "overall": _cmp(y, p, base, n_boot, seed),
            "slices": {
                k: _cmp(y[m], p[m], base[m], max(500, n_boot // 2), seed)
                for k, m in slice_masks.items()
                if int(m.sum()) >= 30
            },
            "final_coef": coef_log[-1]["coef"] if coef_log else [],
        }
        if not name.startswith("ORACLE"):
            res["decision"] = decide(res["overall"], res["slices"])
            if confirmatory_holdout is not None:
                res["confirmed"] = bool(res["overall"]["log_loss"]["hi"] < 0.0)
        results["variants"][name] = res

    results["oof_frame_rows"] = int(keep.sum())
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        sub = frame.filter(pl.Series(keep))
        prev = {
            d: pd
            for d, pd in zip(
                frame["game_date"].unique().sort().to_list()[1:],
                frame["game_date"].unique().sort().to_list()[:-1],
                strict=True,
            )
        }
        pieces = []
        for name, p_all in (
            ("rung0_mov_elo", p_base),
            ("rung0_injury_elo", variants["injury_elo"][0]),
        ):
            pieces.append(_oof_frame(name, sub, p_all[keep], prev))
        oof = pl.concat(pieces)
        oof.write_parquet(out / "oof_predictions.parquet")
        oracle = _oof_frame(
            "ORACLE_injury_elo_NOT_A_CANDIDATE",
            sub,
            variants["ORACLE_not_a_candidate"][0][keep],
            prev,
        )
        oracle.write_parquet(out / "oof_predictions_ORACLE_do_not_load.parquet")
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return results


def _oof_frame(model: str, sub: pl.DataFrame, p: np.ndarray, prev: dict[Any, Any]) -> pl.DataFrame:
    """Columns: model, game_id, game_date, season, p, made_with_data_through."""
    import datetime as dt

    through = []
    for d, snap in zip(sub["game_date"].to_list(), sub["report_as_of"].to_list(), strict=True):
        last_result = dt.datetime.combine(prev[d], dt.time(23, 59, 59))
        through.append(
            max(last_result, snap) if (snap is not None and "injury" in model) else last_result
        )
    return pl.DataFrame(
        {
            "model": [model] * sub.height,
            "game_id": sub["game_id"],
            "game_date": sub["game_date"],
            "season": sub["season"],
            "p": p,
            "made_with_data_through": pl.Series(through, dtype=pl.Datetime("us")),
        }
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument(
        "--backfill-db",
        default=None,
        help="optional separate DB; set report.backfill_table to bf.player_availability",
    )
    ap.add_argument("--config", default="configs/injury_elo.yaml")
    ap.add_argument("--out-dir", default="data/injury_elo")
    ap.add_argument("--confirmatory-holdout", type=int, default=None)
    ap.add_argument("--i-have-preregistered", action="store_true")
    ap.add_argument("--tip-source", choices=("proxy19", "real"), default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.tip_source is not None:
        cfg["report"] = {**cfg["report"], "tip_source": args.tip_source}
    con = duckdb.connect(args.db, read_only=True)
    if args.backfill_db:
        con.execute(f"ATTACH '{args.backfill_db}' AS bf (READ_ONLY)")
    if args.confirmatory_holdout is not None:
        res = run_injury_elo_eval(
            con,
            cfg,
            None,
            confirmatory_holdout=args.confirmatory_holdout,
            preregistered=args.i_have_preregistered,
        )
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"holdout_{args.confirmatory_holdout}.json").write_text(
            json.dumps(res, indent=2, default=str)
        )
        print(json.dumps(res, indent=2, default=str))
        return 0
    res = run_injury_elo_eval(con, cfg, args.out_dir)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
