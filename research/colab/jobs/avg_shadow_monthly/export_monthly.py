# ruff: noqa: E501
"""Mac step 1 of the AVG_SHADOW_LIVE_2 monthly refit: the live season's settled rows as a training export (docs/prereg/AVG_SHADOW_LIVE_2.md, "What the steward builds" 3).

    .venv/bin/python research/colab/jobs/avg_shadow_monthly/export_monthly.py --month 2026-11 [--db nba.duckdb]

Targets = every played row of every game of the season (2026 = 2026-27) dated STRICTLY BEFORE the first day of ``--month``. Built with the LIVE exporter
(``nba.daily.avg_shadow.sequences.build_export``, pinned to T478's by proof P2: bit-identical arrays, and adding later games changes no earlier row), real
tips, reports ``as_of <= tip - 60``, three zero era channels (T478 export layout, as the live path serves). Writes to ``data/colab/avg_shadow_monthly/<month>/``:
``seq_ensemble_rt_live.npz`` (+ ``_meta.parquet``) and ``export_info_live.json`` (month_block, before_date, targets, max game date, data_version, tip coverage, sha256).
Nothing is scored. It refuses an empty season, a row on or after the month start, and rows without a real tip. The 2022-2025 and history exports are the
rebuild's files, unchanged (``data/colab/avg_shadow_rebuild/``).

``--season`` / ``--db`` exist so the SAME function can be proven against the T478 export on a past season (<= 2024): ``parity_vs_t478`` below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/colab/avg_shadow_monthly"
LIVE_SEASON = 2026
ERA_NAMES = ("era_bubble", "era_nofans", "era_limited")


def month_start(month: str) -> date:
    y, m = (int(x) for x in month.split("-"))
    return date(y, m, 1)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_month_export(
    db: str, season: int, before: date, schedule_dir: Path | None = None
) -> tuple[Any, dict[str, Any]]:
    """The season's played rows before ``before`` as a ``SeqExport`` (zero era channels appended) and the tip-coverage counts."""
    import polars as pl

    from nba.daily.avg_shadow import sequences as L
    from nba.features.injury_report import ReportTriggerConfig
    from nba.props.context_residual import asof_elo_margin
    from research.features import seq_full_mix_rt_export as X

    raw = L.load_raw(db, max_season=season, before_date=before.isoformat())
    g = pl.from_pandas(raw.games).with_columns(pl.col("game_date").cast(pl.Date))
    raw.elo = asof_elo_margin(g, L.load_elo()).to_pandas()
    raw.tips, cover = X.tip_table(schedule_dir or X.CUR_SCHEDULE, raw.games)
    ids = {str(i) for i in raw.games.loc[raw.games["season"] == season, "game_id"]}
    if not ids:
        raise RuntimeError(f"no game of season {season} dated before {before}: nothing to export")
    missing = [i for i in ids if i not in set(raw.tips["game_id"].to_list())]
    if missing:
        raise RuntimeError(
            f"{len(missing)} target games have no real tip (first {missing[:3]}); a proxy is never used"
        )
    cfg = L.SeqConfig(report=ReportTriggerConfig(tip_source="real"), max_season=season)
    exp = L.build_export(raw, cfg, target_game_ids=ids)
    if len(exp.meta) == 0:
        raise RuntimeError("no played row among the target games")
    if set(int(s) for s in exp.meta["season"].unique()) != {season}:
        raise RuntimeError("export holds a season other than the target season")
    exp.arrays["ctx"] = np.hstack(
        [exp.arrays["ctx"], np.zeros((len(exp.meta), len(ERA_NAMES)), dtype=np.float32)]
    ).astype(np.float32)
    exp.ctx_features = (*exp.ctx_features, *ERA_NAMES)
    last = pd.Timestamp(exp.meta["game_date"].max()).date()
    if last >= before:
        raise RuntimeError(f"a target row dated {last} is not before {before}")
    return exp, cover


def write_export(exp: Any, out: Path, name: str = "live") -> Path:
    """Same layout as ``research.features.seq_ensemble_export.write`` (arrays + step/ctx feature names + date_str), so the rebuild's loader reads it."""
    out.mkdir(parents=True, exist_ok=True)
    arrays = dict(exp.arrays)
    arrays["step_features"] = np.array(exp.step_features)
    arrays["ctx_features"] = np.array(exp.ctx_features)
    arrays["date_str"] = np.array(
        pd.to_datetime(exp.meta["game_date"]).dt.strftime("%Y-%m-%d").to_numpy(), dtype="U10"
    )
    path = out / f"seq_ensemble_rt_{name}.npz"
    np.savez_compressed(path, **arrays)
    exp.meta.to_parquet(out / f"seq_ensemble_rt_{name}_meta.parquet", index=False)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument(
        "--month",
        required=True,
        help="the month block being refit, YYYY-MM (rows strictly before its first day)",
    )
    ap.add_argument("--db", default=str(ROOT / "nba.duckdb"))
    ap.add_argument("--season", type=int, default=LIVE_SEASON)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    before = month_start(a.month)
    exp, cover = build_month_export(a.db, a.season, before)
    out = a.out or OUT / a.month
    npz = write_export(exp, out)
    from nba.daily.stamps import current_data_version

    info = {
        "month_block": a.month, "before_date": before.isoformat(), "season": a.season, "targets": int(len(exp.meta)),
        "games": int(exp.meta["game_id"].nunique()), "min_date": str(pd.Timestamp(exp.meta["game_date"].min()).date()),
        "max_date": str(pd.Timestamp(exp.meta["game_date"].max()).date()), "tip_coverage": cover, "data_version": current_data_version(),
        "npz": str(npz), "npz_sha256": file_sha256(npz), "n_ctx": int(exp.arrays["ctx"].shape[1]), "n_step": int(exp.arrays["seq"].shape[2]),
    }  # fmt: skip
    (out / "export_info_live.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
