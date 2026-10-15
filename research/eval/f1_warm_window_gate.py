# mypy: disable-error-code="no-any-return,no-untyped-call,attr-defined,index,arg-type,assignment,operator,union-attr,misc"
# ruff: noqa: E501
"""F1_WARM_WINDOW minimum-data gate (docs/prereg/F1_WARM_WINDOW.md): features and row counts only.

No model is fitted and no outcome statistic is printed. The frames carry the box-score labels because a feature build needs the box score;
only counts, NULL shares and feature-versus-feature differences leave this module. Both databases are opened read-only with season <= 2024 in SQL
(``load_inputs``). Season 2025 is never loaded.

    cd <worktree or main> && F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD \\
        /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.eval.f1_warm_window_gate out.json

``F1_ROOT`` is the checkout that holds ``nba.duckdb`` and ``data/`` (a worktree has neither); configs are read from this checkout's own ``configs/`` (the pinned ones), never from the cwd.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from math import erf, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

import nba.features.game_tipoff as GT
from nba.eval.context_residual_eval import MAX_SEASON, load_inputs, month_blocks
from nba.props.context_residual import (
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    stat_frame,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"  # the pinned configs, whatever the cwd
STATS = ("pts", "reb", "ast", "fg3m")
KEY = ["game_id", "player_id"]
FLOOR = {"pts": 0.005, "reb": 0.0025, "ast": 0.0025, "fg3m": 0.0025}
# Half-widths of the 95% game-clustered CIs of T420 / T421 (HISTORY_WINDOW_3 A1 minus W0: same evaluation rows, same clustering).
# Only the WIDTHS are used, never the centres; scaled by SE_INFLATION because the F1 treatment (three more seasons) is larger.
HALF_WIDTH = {
    "pts": (0.00485, 0.00365),
    "reb": (0.00195, 0.0014),
    "ast": (0.0015, 0.0012),
    "fg3m": (0.0009, 0.00085),
}
SE_INFLATION = 1.25
WARM_START = dt.date(2019, 10, 1)
COLD_START = dt.date(2022, 10, 1)
PLANT_FROM = dt.date(2023, 1, 15)
TOL = 1e-9
MAG_COLS = (
    "exp_margin",
    "vac_min",
    "vac_pts",
    "n_out_rot",
    "min40",
    "m100_pts",
    "m40_pts",
    "m10_pts",
    "min10",
    "n_prior",
    "rate_pts",
    "since_flag",
)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def power_at(effect: float, se: float, floor: float) -> float:
    """P(a season passes) when the true improvement is ``effect`` (positive = better): the estimate must reach the floor AND its 95% interval
    must exclude 0, i.e. estimate improvement >= max(floor, 1.96 se)."""
    need = max(floor, 1.96 * se)
    return _phi((effect - need) / se)


def sizing() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for st, (h23, h24) in HALF_WIDTH.items():
        for yr, h in (("2023", h23), ("2024", h24)):
            se = h / 1.96 * SE_INFLATION
            f = FLOOR[st]
            out[f"{st}_{yr}"] = {
                "se": round(se, 5),
                "floor": f,
                "need_improvement": round(max(f, 1.96 * se), 5),
                "power_at_floor": round(power_at(f, se, f), 3),
                "power_at_1.5_floor": round(power_at(1.5 * f, se, f), 3),
                "power_at_2_floor": round(power_at(2 * f, se, f), 3),
                "mde_80": round(max(f, 1.96 * se) + 0.8416 * se, 4),
            }
    return out


def build(
    root: Path, dbs: list[str], scramble_from: dt.date | None = None
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, set[int]]]:
    elo = {
        k: float(v)
        for k, v in yaml.safe_load((CONFIG_DIR / "mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load((CONFIG_DIR / "context_residual.yaml").read_text()).get("model", {})
    )
    parts = [load_inputs(str(root / d), None) for d in dbs]
    games = pl.concat([p[0] for p in parts], how="vertical_relaxed")
    if int(games["season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 loaded: frozen holdout")
    pgs = pl.concat([p[1] for p in parts], how="vertical_relaxed")
    static = pl.concat([p[2] for p in parts], how="vertical_relaxed").unique(
        "player_id", keep="last"
    )
    avail = pl.concat([p[3] for p in parts], how="vertical_relaxed")
    if scramble_from is not None:
        late = set(
            games.with_columns(pl.col("game_date").cast(pl.Date))
            .filter(pl.col("game_date") >= scramble_from)["game_id"]
            .to_list()
        )
        mask = pgs["game_id"].is_in(list(late)).to_numpy()
        idx = np.where(mask)[0]
        rng = np.random.default_rng(7)
        for c in ("minutes", "pts", "reb", "ast", "fg3m"):
            v = pgs[c].cast(pl.Float64).to_numpy().copy()
            v[idx] = v[rng.permutation(idx)]
            # NaN back to NULL: polars treats NaN as greater than 0, which would turn every DNP row into a played row
            pgs = pgs.with_columns(
                pl.Series(c, v, nan_to_null=True).cast(pgs[c].dtype, strict=False)
            )
    dirs = [root / "data/schedule"] + ([root / "data/history/schedule"] if len(dbs) > 1 else [])
    orig = GT.build_game_tipoff

    def tips(schedule_dir: Any = None, max_season: int | None = None) -> pl.DataFrame:
        return pl.concat([orig(d, max_season) for d in dirs], how="vertical_relaxed").unique(
            "game_id", keep="last"
        )

    GT.build_game_tipoff = tips
    try:
        flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    finally:
        GT.build_game_tipoff = orig
    return build_features(games, pgs, static, flagged, elo), games, flagged


def stamp_exposure(root: Path) -> dict[str, Any]:
    """BEST_PRACTICES 2026-10-15 (report time = the time printed in the PDF): hour-only report files are stamped 30 minutes early. For each game
    with a usable report, flag it when the SNAPSHOT USED has a whole-hour stamp (minute 0, assumed hour-only) and stamp + 30 min > tip - 60 min,
    i.e. the report was probably published after the cutoff. An upper bound: it assumes every minute-0 stamp is an hour-only file. Features and
    stamps only; no outcome is touched."""
    from nba.features.injury_report import latest_pretip_flagged, usable_report_rows  # noqa: F401

    cfg = ContextResidualConfig(
        **yaml.safe_load((CONFIG_DIR / "context_residual.yaml").read_text()).get("model", {})
    )
    dbs = ["data/history/nba_history.duckdb", "nba.duckdb"]
    parts = [load_inputs(str(root / d), None) for d in dbs]
    games = pl.concat([p[0] for p in parts], how="vertical_relaxed")
    avail = pl.concat([p[3] for p in parts], how="vertical_relaxed")
    dirs = [root / "data/schedule", root / "data/history/schedule"]
    orig = GT.build_game_tipoff

    def tips(schedule_dir: Any = None, max_season: int | None = None) -> pl.DataFrame:
        return pl.concat([orig(d, max_season) for d in dirs], how="vertical_relaxed").unique(
            "game_id", keep="last"
        )

    GT.build_game_tipoff = tips
    try:
        rows = (
            avail.filter(
                pl.col("source").is_in(list(cfg.report.sources)) & pl.col("player_id").is_not_null()
            )
            .join(games.select(["game_id", "game_date"]), on="game_id")
            .select(
                "game_id",
                "player_id",
                pl.col("status").str.to_lowercase().alias("status"),
                pl.col("as_of").cast(pl.Datetime("us")),
                "game_date",
            )
        )
        rows = GT.attach_real_tips(rows, max_season=MAX_SEASON)
    finally:
        GT.build_game_tipoff = orig
    _, used = latest_pretip_flagged(rows, cfg.report)
    tip = rows.group_by("game_id").agg(pl.col("tip_et").first())
    season = games.select(["game_id", "season"])
    d = (
        pl.DataFrame({"game_id": list(used), "used_as_of": list(used.values())})
        .join(tip, on="game_id")
        .join(season, on="game_id")
        .with_columns(
            (
                (pl.col("used_as_of").dt.minute() == 0)
                & (
                    pl.col("used_as_of") + pl.duration(minutes=30)
                    > pl.col("tip_et") - pl.duration(minutes=int(cfg.report.lead_minutes))
                )
            ).alias("exposed"),
            (pl.col("used_as_of").dt.minute() == 0).alias("whole_hour"),
        )
    )
    out: dict[str, Any] = {}
    for s in range(2019, 2025):
        f = d.filter(pl.col("season") == s)
        out[str(s)] = {
            "exposed_game_ids": sorted(f.filter(pl.col("exposed"))["game_id"].to_list()),
            "games_with_report": f.height,
            "whole_hour_stamp_share": round(float(f["whole_hour"].mean()), 4),
            "exposed_games": int(f["exposed"].sum()),
            "exposed_share": round(float(f["exposed"].mean()), 4),
        }
    return out


def _by_season(df: pl.DataFrame) -> dict[int, int]:
    d = df.group_by("season").len().sort("season")
    return {int(s): int(n) for s, n in zip(d["season"], d["len"], strict=True)}


def _numeric(frame: pl.DataFrame) -> list[str]:
    ok = (
        pl.Float64,
        pl.Float32,
        pl.Int64,
        pl.Int32,
        pl.UInt32,
        pl.Int8,
        pl.Int16,
        pl.Boolean,
        pl.UInt8,
    )
    return [c for c in frame.columns if c not in (*KEY, "season") and frame.schema[c] in ok]


def run(root: Path, out_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"root": str(root), "sizing": sizing()}
    t0 = time.time()
    cold, _games_c, _ = build(root, ["nba.duckdb"])
    warm, games_w, fl_w = build(root, ["data/history/nba_history.duckdb", "nba.duckdb"])
    print(f"[{time.time() - t0:.0f}s] frames cold {cold.height} warm {warm.height}", flush=True)
    out["frame_rows"] = {"cold": cold.height, "warm": warm.height}
    out["played_rows_by_season"] = {"cold": _by_season(cold), "warm": _by_season(warm)}
    out["a0_rows_by_season"] = {
        f"{n}_{st}": _by_season(stat_frame(fr, st))
        for n, fr in (("cold", cold), ("warm", warm))
        for st in STATS
    }
    keys: dict[str, Any] = {}
    for st in STATS:
        sc = (
            stat_frame(cold, st)
            .filter(pl.col("season").is_in([2023, 2024]))
            .select([*KEY, "season"])
        )
        sw = (
            stat_frame(warm, st)
            .filter(pl.col("season").is_in([2023, 2024]))
            .select([*KEY, "season"])
        )
        extra = sw.join(sc, on=KEY, how="anti")
        keys[st] = {
            "cold": sc.height,
            "warm": sw.height,
            "cold_not_in_warm": sc.join(sw, on=KEY, how="anti").height,
            "warm_not_in_cold": extra.height,
            "warm_not_in_cold_by_season": _by_season(extra),
        }
    out["eval_keys"] = keys
    # training rows at each month block (same for the four stats: the row set does not depend on the stat)
    scold, swarm = stat_frame(cold, "pts"), stat_frame(warm, "pts")
    test = scold.filter(pl.col("season").is_in([2023, 2024]))
    blocks = []
    for s, e in month_blocks(test):
        blocks.append(
            {
                "start": str(s),
                "test_rows": test.filter(
                    (pl.col("game_date") >= s) & (pl.col("game_date") < e)
                ).height,
                "train_A_cold_2022": scold.filter(pl.col("game_date") < s).height,
                "train_B_warm_2022": swarm.filter(
                    (pl.col("game_date") < s) & (pl.col("game_date") >= COLD_START)
                ).height,
                "train_C_warm_2019": swarm.filter(
                    (pl.col("game_date") < s) & (pl.col("game_date") >= WARM_START)
                ).height,
            }
        )
    out["train_rows_pts_by_block"] = blocks
    # feature shift: cold versus warm on the same (game, player) rows
    cols = [c for c in MAG_COLS if c in cold.columns and c in warm.columns]
    shift: dict[str, Any] = {}
    for s in (2022, 2023, 2024):
        j = (
            cold.filter(pl.col("season") == s)
            .select([*KEY, *cols])
            .join(warm.select([*KEY, *cols]), on=KEY, suffix="_w")
        )
        r: dict[str, Any] = {"rows": j.height}
        for c in cols:
            x, y = j[c].cast(pl.Float64).to_numpy(), j[c + "_w"].cast(pl.Float64).to_numpy()
            d = np.abs(np.nan_to_num(x) - np.nan_to_num(y))
            r[c] = {
                "share_diff_gt_1e-3": round(float((d > 1e-3).mean()), 4),
                "median": round(float(np.median(d)), 5),
                "p90": round(float(np.quantile(d, 0.9)), 4),
                "null_mismatch": int((np.isnan(x) ^ np.isnan(y)).sum()),
            }
        shift[str(s)] = r
    out["feature_shift_cold_vs_warm"] = shift
    out["n_prior_mean"] = {
        n: {
            str(s): round(float(f.filter(pl.col("season") == s)["n_prior"].mean()), 1)
            for s in (2022, 2023, 2024)
        }
        for n, f in (("cold", cold), ("warm", warm))
    }
    # NULL / NaN share of every column of the stat frame by season (the NULL-pattern audit)
    sfw = stat_frame(warm, "pts")
    null_rows = []
    for c in sfw.columns:
        if c in (*KEY, "season", "game_date"):
            continue
        sh = []
        for s in range(2019, 2025):
            v = sfw.filter(pl.col("season") == s)[c]
            nan = float(v.is_nan().mean()) if v.dtype in (pl.Float64, pl.Float32) else 0.0
            sh.append(float(v.is_null().mean()) + nan)
        null_rows.append(
            (
                c,
                max(sh) - min(sh),
                abs(sum(sh[:3]) / 3 - sum(sh[3:]) / 3),
                [round(x, 4) for x in sh],
            )
        )
    null_rows.sort(key=lambda r: -r[1])
    out["null_share_by_season_2019_2024"] = {
        "columns": len(null_rows),
        "max_range_across_seasons": round(null_rows[0][1], 4),
        "columns_with_history_vs_current_gap_gt_0.01": [r[0] for r in null_rows if r[2] > 0.01],
        "top": [[r[0], round(r[1], 4), r[3]] for r in null_rows[:8]],
    }
    # report coverage and era descriptives
    desc = []
    for s in range(2013, 2025):
        f = warm.filter(pl.col("season") == s)
        r2: dict[str, Any] = {
            "season": s,
            "rows": f.height,
            "has_report_share": round(float(f["has_report"].mean()), 4),
        }
        hr = f.filter(pl.col("has_report") == 1)
        if hr.height:
            r2.update(
                n_out_rot_mean=round(float(hr["n_out_rot"].mean()), 3),
                n_out_rot_ge1_share=round(float((hr["n_out_rot"] >= 1).mean()), 4),
                vac_min_mean=round(float(hr["vac_min"].mean()), 2),
            )
        r2["pos_code_null_share"] = round(float(f["pos_code"].is_null().mean()), 4)
        desc.append(r2)
    out["warm_report_descriptives"] = desc
    gdate = games_w.with_columns(pl.col("game_date").cast(pl.Date))
    out["games_with_usable_report"] = {
        str(s): [
            sum(1 for g in gdate.filter(pl.col("season") == s)["game_id"].to_list() if g in fl_w),
            gdate.filter(pl.col("season") == s).height,
        ]
        for s in range(2019, 2025)
    }
    out["games_without_usable_report_2019"] = sorted(
        str(d)
        for g, d in zip(
            gdate.filter(pl.col("season") == 2019)["game_id"],
            gdate.filter(pl.col("season") == 2019)["game_date"],
            strict=True,
        )
        if g not in fl_w
    )
    # planted future on the warm frame: scramble every box row dated on or after PLANT_FROM; features of earlier rows must not move
    planted, _, _ = build(
        root, ["data/history/nba_history.duckdb", "nba.duckdb"], scramble_from=PLANT_FROM
    )
    num = _numeric(warm)
    early = (
        warm.filter(pl.col("game_date") < PLANT_FROM)
        .select([*KEY, *num])
        .join(
            planted.filter(pl.col("game_date") < PLANT_FROM).select([*KEY, *num]),
            on=KEY,
            suffix="_p",
        )
    )
    worst, bad = 0.0, []
    for c in num:
        x, y = early[c].cast(pl.Float64).to_numpy(), early[c + "_p"].cast(pl.Float64).to_numpy()
        m = float(np.abs(np.nan_to_num(x) - np.nan_to_num(y)).max()) if len(x) else 0.0
        nn = int((np.isnan(x) ^ np.isnan(y)).sum())
        if m > TOL or nn:
            bad.append([c, m, nn])
        worst = max(worst, m)
    late_a = (
        warm.filter(pl.col("game_date") >= PLANT_FROM)
        .select([*KEY, *num])
        .join(
            planted.filter(pl.col("game_date") >= PLANT_FROM).select([*KEY, *num]),
            on=KEY,
            suffix="_p",
        )
    )
    moved = sum(
        1
        for c in num
        if (
            np.abs(
                np.nan_to_num(late_a[c].cast(pl.Float64).to_numpy())
                - np.nan_to_num(late_a[c + "_p"].cast(pl.Float64).to_numpy())
            )
            > TOL
        ).any()
    )
    out["planted_future"] = {
        "rows_compared": early.height,
        "columns_compared": len(num),
        "max_abs_diff": worst,
        "columns_differing": bad,
        "late_columns_that_moved": moved,
        "planted_from": str(PLANT_FROM),
    }
    out["stamp_exposure"] = stamp_exposure(root)
    out_path.write_text(json.dumps(out, indent=1, default=str))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(os.environ.get("F1_ROOT", Path(__file__).resolve().parents[2]))
    res = run(root, Path(argv[0] if argv else "f1_gate.json"))
    print(json.dumps(res["sizing"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
