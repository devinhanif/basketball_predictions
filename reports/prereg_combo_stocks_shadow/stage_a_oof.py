# ruff: noqa: E501
"""COMBO_STOCKS_SHADOW rehearsal, stage A: walk-forward out-of-fold integer grids for pts, reb, ast, blk, stl.

DESCRIPTIVE rehearsal of docs/prereg/COMBO_STOCKS_SHADOW.md (DRAFT). Read-only. Seasons <= 2024 only (SQL filter);
season 2025 is never loaded. Production recipe unchanged (``ContextResidualModel`` on ``stat_feature_names``,
calendar-month blocks over 2023 and 2024, each block fitted on every played row dated strictly before it,
newest 15% of the training window as the conformal calibration window, real tip-off gating of the report).
blk and stl use ``nba.props.stock_features`` (production-style columns recomputed for those stats).

    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore reports/prereg_combo_stocks_shadow/stage_a_oof.py \
        --db /Users/devin/Downloads/nba-prediction/data/backups/nba_20261014.duckdb --out <scratch dir>

Outputs (npz, one file ``oof_<stat>.npz`` per stat, plus ``keys.parquet`` and ``stage_a.json``): the integer 199-point grid
of the model (``q``), of the recency-conformal baseline (``qb``), the raw 19 quantiles (``q19raw``, production's stored form),
and y. Progress is printed per block; a finished block is checkpointed, so a rerun resumes.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import nba.features.game_tipoff as GT  # noqa: E402
from nba.eval.context_residual_eval import month_blocks  # noqa: E402
from nba.props.context_residual import (  # noqa: E402
    CRPS_TAUS,
    QUANTILE_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.count_head import NBCountModel  # noqa: E402
from nba.props.stock_features import (  # noqa: E402
    STOCK_STATS,
    add_stock_features,
    stock_feature_names,
)

MAX_SEASON = 2024
TEST_SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "blk", "stl")
SORT = ["game_date", "game_id", "player_id"]


def load(db: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    con = duckdb.connect(db, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.stl, s.blk, s.starter "
            f"FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        static = con.execute("SELECT player_id, position FROM players_static").pl()
        avail = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.as_of, a.source FROM player_availability a "
            f"JOIN games g USING (game_id) WHERE a.game_id IS NOT NULL AND g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    return games, pgs, static, avail


LOCK = Path("/Users/devin/Downloads/nba-prediction/data/ops/heavy.lock")


def main() -> None:
    import contextlib
    import os

    from research.eval import workload_eval as WE

    # COMBO_NO_LOCK=1: a long job by another owner holds the lock; this run is small (about 1 GB, minutes). Disclosed in the report.
    with contextlib.nullcontext() if os.environ.get("COMBO_NO_LOCK") else WE.WorkloadLock(LOCK):
        run()


def run() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--schedule-dir", required=True, help="dir holding raw_2022-23/2023-24/2024-25 parquet only"
    )
    ap.add_argument(
        "--targets",
        default=",".join(STATS),
        help="comma list; combos pra,pr,pa,ra,bs are summed box columns",
    )
    ap.add_argument(
        "--head",
        choices=("cr", "nb"),
        default="cr",
        help="cr = production recipe, nb = NBCountModel",
    )
    ap.add_argument(
        "--lt",
        action="store_true",
        help="production lower-tail mixture for pts (A0 of the AVG rule)",
    )
    args = ap.parse_args()
    targets = tuple(args.targets.split(","))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    GT.SCHEDULE_DIR = Path(args.schedule_dir)
    t0 = time.time()
    elo = {
        k: float(v)
        for k, v in yaml.safe_load((ROOT / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg_raw = yaml.safe_load((ROOT / "configs/context_residual.yaml").read_text())
    cfg = ContextResidualConfig(**cfg_raw.get("model", {}))
    cfg = dataclasses.replace(cfg, report=dataclasses.replace(cfg.report, tip_source="real"))
    if args.lt:
        cfg = dataclasses.replace(cfg, lower_tail="mixture", lower_tail_stats=("pts",))
    games, pgs, static, avail = load(args.db)
    assert int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    print(
        f"loaded games {games.height} pgs {pgs.height} avail {avail.height} ({time.time() - t0:.0f}s)",
        flush=True,
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo)
    print(f"production features {feats.height} rows ({time.time() - t0:.0f}s)", flush=True)
    pgs = pgs.with_columns(
        (pl.col("pts") + pl.col("reb") + pl.col("ast")).alias("pra"),
        (pl.col("pts") + pl.col("reb")).alias("pr"),
        (pl.col("pts") + pl.col("ast")).alias("pa"),
        (pl.col("reb") + pl.col("ast")).alias("ra"),
        (pl.col("blk") + pl.col("stl")).alias("bs"),
    )
    extra_stats = tuple(
        dict.fromkeys((*STOCK_STATS, *[t for t in targets if t not in ("pts", "reb", "ast")]))
    )
    feats = add_stock_features(feats, games, pgs, flagged, stats=extra_stats)
    print(f"stock features added ({time.time() - t0:.0f}s)", flush=True)
    feats = feats.join(
        pgs.select(
            "game_id",
            "player_id",
            "stl",
            "blk",
            "pra",
            "pr",
            "pa",
            "ra",
            "bs",
            "minutes",
            "starter",
        ).rename({"starter": "starter_act"}),
        on=["game_id", "player_id"],
        how="left",
        suffix="_pgs",
    )
    names = {
        s: (stock_feature_names(s) if s not in ("pts", "reb", "ast") else stat_feature_names(s))
        for s in targets
    }
    base_cols = [
        "game_id",
        "player_id",
        "game_date",
        "season",
        "team_id",
        "n_prior",
        "min10",
        "starter10",
        "has_report",
        "n_out_rot",
        "team_game_no",
        "minutes",
    ]
    frames = {s: stat_frame(feats, s) for s in targets}
    test = frames[targets[0]].filter(pl.col("season").is_in(list(TEST_SEASONS)))
    blocks = month_blocks(test)
    print(f"{len(blocks)} month blocks; test rows {test.height}", flush=True)
    taus19 = np.array(QUANTILE_TAUS)
    done_key = out / "blocks_done.json"
    done: list[str] = json.loads(done_key.read_text()) if done_key.exists() else []
    for start, end in blocks:
        tag = start.isoformat()
        if tag in done:
            continue
        t1 = time.time()
        part: dict[str, np.ndarray] = {}
        keys = None
        for s in targets:
            sf = frames[s]
            block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
                pl.col("season").is_in(list(TEST_SEASONS))
            )
            train = sf.filter(pl.col("game_date") < start).sort(SORT)
            if block.is_empty() or train.height < 1500:
                continue
            block = block.sort(SORT)
            if args.head == "nb":
                nb = NBCountModel(s, cfg, names[s]).fit(train)
                mean, q199 = nb.predict(block, CRPS_TAUS)
                _, q19 = nb.predict(block, taus19)
                qb199 = q199
            else:
                model = ContextResidualModel(s, cfg, names[s]).fit(train)
                mean, q199 = model.predict(block, CRPS_TAUS)
                _, q19 = model.predict(block, taus19)
                _, qb199 = model.predict_baseline_cal(block, CRPS_TAUS)
            part[f"q_{s}"] = to_integer_support(q199).astype(np.int16)
            part[f"qb_{s}"] = to_integer_support(qb199).astype(np.int16)
            part[f"q19raw_{s}"] = q19.astype(np.float32)
            part[f"mean_{s}"] = mean.astype(np.float32)
            part[f"y_{s}"] = block["y"].to_numpy().astype(np.int16)
            part[f"m_{s}"] = block["m"].to_numpy().astype(np.float32)
            kk = block.select(base_cols)
            if keys is None:
                keys = kk
            else:
                assert keys.select("game_id", "player_id").equals(kk.select("game_id", "player_id"))
        if keys is None:
            continue
        keys.write_parquet(out / f"keys_{tag}.parquet")
        np.savez_compressed(out / f"oof_{tag}.npz", **part)
        done.append(tag)
        done_key.write_text(json.dumps(done))
        print(
            f"block {tag}: {keys.height} rows, {time.time() - t1:.0f}s (total {time.time() - t0:.0f}s)",
            flush=True,
        )
    meta = {
        "db": args.db,
        "n_blocks": len(done),
        "stats": list(targets),
        "cfg": dataclasses.asdict(cfg) | {"report": None},
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "seconds": time.time() - t0,
    }
    (out / "stage_a.json").write_text(json.dumps(meta, indent=2, default=str))
    print("stage A done", flush=True)


if __name__ == "__main__":
    main()
