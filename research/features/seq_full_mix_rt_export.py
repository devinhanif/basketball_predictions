# ruff: noqa: E501
"""Real-tip sequence exports for SEQ_FULL_MIX_RT (docs/prereg/SEQ_FULL_MIX_RT.md).

Same builder as ENSEMBLE / SEQ_FULL_MIX with three changes, all fixed in the rule before any result:
* **Real-tip report tokens.** A report snapshot is usable for a game iff ``as_of <= real scheduled tip - 60 min`` (``tip_source="real"``; tips from
  ``data/schedule/raw_<season>.parquet`` for 2022+ and ``data/history/schedule/raw_<season>.parquet`` for 2019-2021, the same sources and the same
  ``nba.features.game_tipoff`` reader production uses). A game with no real tip has NO report token (``has_report = 0``), never the 19:00 ET proxy.
* **Era tokens** (``era_bubble``, ``era_nofans``, ``era_limited``): three ctx channels from ``game_era_flags``; a game without a flags row gets zeros and is counted;
  never imputed. Appended to the ctx for every export so every window sees the same input width.
* **History (window WALL):** seasons 2019-2021 from ``data/history/nba_history.duckdb`` (read-only), real-tip gated reports for 2019-20 and 2020-21; the
  2021-22 report tokens are DROPPED (no-report token, box rows kept) because that season's archive was hourly and its snapshots are ~2 h fresher than every
  other season (reports/prereg_history_design/gate.json).

Selection exports (``selection``) refuse any season > 2024 in code. The held-out export (``export25``) is the SEQ_FULL_MIX export with the real-tip
tokens: guarded by the freeze and the committed holdout row of SEQ_FULL_MIX_RT.

    uv run python -m research.features.seq_full_mix_rt_export selection                  # 2022-2024 + 2019-2021, nothing later
    uv run python -m research.features.seq_full_mix_rt_export hist                       # 2019-2021 only (WALL input of the held-out job)
    uv run python -m research.features.seq_full_mix_rt_export export25 [--sha <64 hex>]   # 2022-2025; refuses without freeze + committed row
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import ReportTriggerConfig
from research.eval import seq_full_mix_rt_rule as RULE
from research.features import player_sequences as P
from research.features import seq_ensemble_export as X
from research.features import seq_full_mix_export as X25

ERA_CTX = ("era_bubble", "era_nofans", "era_limited")
CUR_SCHEDULE = RULE.ROOT / "data/schedule"
HIST_SCHEDULE = RULE.ROOT / "data/history/schedule"
HIST_DB = RULE.ROOT / "data/history/nba_history.duckdb"
CUR_DB = "nba.duckdb"
SEL_MAX_SEASON = 2024
HIST_SEASONS = (2019, 2020, 2021)
NO_REPORT_HIST = (2021,)
OUT_DIR = RULE.ROOT / "data/colab/seq_full_mix_rt"
HOLDOUT = 2025


def team_map_arrays(team_id: Any, team_idx: Any) -> dict[int, int]:
    """team_id -> team_idx as actually used by rows (one index per team, or the export is inconsistent)."""
    m: dict[int, int] = {}
    for t, i in {(int(t), int(i)) for t, i in zip(team_id, team_idx, strict=True)}:
        if m.setdefault(t, i) != i:
            raise RuntimeError(f"team {t} has two indices in one export")
    return m


def team_map(exp: P.SeqExport) -> dict[int, int]:
    return team_map_arrays(exp.meta["team_id"], exp.arrays["team_idx"])


def assert_same_vocabulary(a: P.SeqExport, b: P.SeqExport) -> None:
    """The history and current exports share the team index mapping, the stat scale and the feature names (the Colab concat takes these from the history export)."""
    ma, mb = team_map(a), team_map(b)
    bad = {t: (ma[t], mb[t]) for t in ma.keys() & mb.keys() if ma[t] != mb[t]}
    if bad:
        raise RuntimeError(f"team index mapping differs between the exports: {bad}")
    if (
        not np.array_equal(a.arrays["stat_scale"], b.arrays["stat_scale"])
        or a.step_features != b.step_features
        or a.ctx_features != b.ctx_features
    ):
        raise RuntimeError(
            "stat_scale or feature names differ between the history and the current export"
        )


def assert_hist_files_match(exp: P.SeqExport, out: Path) -> None:
    """Held-out export vs the history export already on disk (npz + meta): same team mapping, stat scale and feature names."""
    npz, meta = out / "seq_ensemble_rt_hist.npz", out / "seq_ensemble_rt_hist_meta.parquet"
    if not (npz.exists() and meta.exists()):
        raise RuntimeError(
            f"{npz} / {meta} missing: build the history export first (`... seq_full_mix_rt_export hist`)"
        )
    z = np.load(npz)
    import pandas as pd

    mh = team_map_arrays(pd.read_parquet(meta)["team_id"], z["team_idx"])
    mc = team_map(exp)
    bad = {t: (mh[t], mc[t]) for t in mh.keys() & mc.keys() if mh[t] != mc[t]}
    if (
        bad
        or not np.array_equal(z["stat_scale"], exp.arrays["stat_scale"])
        or list(z["ctx_features"]) != list(exp.ctx_features)
        or list(z["step_features"]) != list(exp.step_features)
    ):
        raise RuntimeError(
            f"the history export on disk and the current export disagree (team mapping {bad}, stat scale or feature names)"
        )


def assert_selection_seasons(exp: P.SeqExport) -> None:
    """The selection exports hold nothing after 2024 (the held-out season is never in run #1)."""
    if int(exp.meta["season"].max()) > SEL_MAX_SEASON:
        raise RuntimeError(
            f"selection export holds season {int(exp.meta['season'].max())} > {SEL_MAX_SEASON}"
        )


def real_cfg(**kw: Any) -> P.SeqConfig:
    return P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **kw)


def tip_table(schedule_dir: Path, games: Any) -> tuple[pl.DataFrame, dict[str, int]]:
    """Real tips (game_id, tip_et) for the games of ``raw`` and the coverage counts (games without a real tip get no report token)."""
    tips = build_game_tipoff(schedule_dir)
    ids = set(games["game_id"])
    have = tips.filter(pl.col("game_id").is_in(list(ids)))
    return have, {
        "n_games": len(ids),
        "with_real_tip": have.height,
        "without_real_tip": len(ids) - have.height,
    }


def era_matrix(game_ids: np.ndarray, flags: pl.DataFrame) -> tuple[np.ndarray, int]:
    """(n, 3) float era channels per target row's game; games with no flags row -> zeros (counted, not imputed)."""
    f = flags.select(
        "game_id",
        pl.col("covid_bubble").cast(pl.Float32).fill_null(0.0).alias("era_bubble"),
        pl.col("no_fans").cast(pl.Float32).fill_null(0.0).alias("era_nofans"),
        pl.col("limited_fans").cast(pl.Float32).fill_null(0.0).alias("era_limited"),
    ).unique("game_id")
    j = pl.DataFrame({"game_id": np.asarray(game_ids).astype(str)}).join(
        f, on="game_id", how="left"
    )
    miss = int(j["era_bubble"].null_count())
    return j.select(list(ERA_CTX)).fill_null(0.0).to_numpy().astype(np.float32), miss


def add_era(exp: P.SeqExport, flags: pl.DataFrame) -> dict[str, int]:
    era, miss = era_matrix(exp.meta["game_id"].to_numpy(), flags)
    exp.arrays["ctx"] = np.hstack([exp.arrays["ctx"], era]).astype(np.float32)
    exp.ctx_features = (*P.CTX_FEATURES, *ERA_CTX)
    return {"rows": int(len(era)), "rows_without_flags": miss}


def cur_flags(db: str, max_season: int) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            f"SELECT f.game_id, f.covid_bubble, f.no_fans, f.limited_fans FROM game_era_flags f JOIN games g USING (game_id) WHERE g.season <= {int(max_season)}"
        ).pl()
    finally:
        con.close()


def build_cur_rt(
    max_season: int = SEL_MAX_SEASON,
    db: str = CUR_DB,
    elo_config: str = "configs/mov_elo_tuned.yaml",
) -> tuple[P.SeqExport, dict[str, Any]]:
    """Seasons 2022..max_season, real-tip report tokens. ``P.load_raw`` itself refuses any season >= 2025 unless the caller lifted the bound."""
    raw = P.load_raw(db, max_season=max_season)
    X._elo(raw, elo_config)
    raw.tips, cover = tip_table(CUR_SCHEDULE, raw.games)
    exp = P.build_export(raw, real_cfg(max_season=max_season))
    info = {"tip_coverage": cover, "era": add_era(exp, cur_flags(db, max_season))}
    return exp, info


def build_hist_rt(
    elo_config: str = "configs/mov_elo_tuned.yaml",
) -> tuple[P.SeqExport, dict[str, Any]]:
    """Seasons 2019-2021 from the history DB (read-only), real-tip report tokens for 2019-20 and 2020-21, none for 2021-22."""
    raw = P.load_raw(str(HIST_DB), max_season=max(HIST_SEASONS))
    got = sorted(int(s) for s in raw.games["season"].unique())
    if got != list(HIST_SEASONS):
        raise ValueError(f"history DB seasons {got} != {list(HIST_SEASONS)}")
    raw.static = X.merge_static(raw.static, X.cache_static())
    X._elo(raw, elo_config)
    raw.tips, cover = tip_table(HIST_SCHEDULE, raw.games)
    raw.no_report_seasons = NO_REPORT_HIST
    exp = P.build_export(
        raw, real_cfg(min_target_season=min(HIST_SEASONS), max_season=max(HIST_SEASONS))
    )
    info = {
        "tip_coverage": cover,
        "era": add_era(exp, cur_flags(str(HIST_DB), max(HIST_SEASONS))),
        "position": X.residual_position_share(exp),
    }
    return exp, info


def selection(out: Path | None = None) -> dict[str, Any]:
    """Run #1 inputs: the 2022-2024 export and the 2019-2021 history export, both real-tip. Nothing later than 2024; history read-only."""
    out = out or OUT_DIR
    cur, ci = build_cur_rt(SEL_MAX_SEASON)
    assert_selection_seasons(cur)
    hist, hi = build_hist_rt()
    if int(hist.meta["season"].max()) > max(HIST_SEASONS):
        raise RuntimeError("history export holds a season after 2021")
    assert_same_vocabulary(hist, cur)
    info: dict[str, Any] = {
        "cur": {"npz": str(X.write(cur, out, "rt_cur")), "targets": int(len(cur.meta)), "seasons": sorted(int(s) for s in cur.meta["season"].unique()), **ci},
        "hist": {"npz": str(X.write(hist, out, "rt_hist")), "targets": int(len(hist.meta)), "seasons": sorted(int(s) for s in hist.meta["season"].unique()), **hi},
        "no_report_hist_seasons": list(NO_REPORT_HIST),
    }  # fmt: skip
    for k in ("cur", "hist"):
        info[k]["npz_sha256"] = RULE.file_sha256(Path(info[k]["npz"]))
    (out / "export_info_selection.json").write_text(json.dumps(info, indent=2, default=str))
    return info


def hist_only(out: Path | None = None) -> dict[str, Any]:
    """The 2019-2021 history export alone (read-only from data/history), for the held-out job's WALL input."""
    out = out or OUT_DIR
    hist, hi = build_hist_rt()
    if int(hist.meta["season"].max()) > max(HIST_SEASONS):
        raise RuntimeError("history export holds a season after 2021")
    npz = X.write(hist, out, "rt_hist")
    return {"npz": str(npz), "npz_sha256": RULE.file_sha256(npz), **hi}


def build_current25_rt(
    db: str = CUR_DB, elo_config: str = "configs/mov_elo_tuned.yaml"
) -> tuple[P.SeqExport, dict[str, Any]]:
    """Seasons 2022-2025 with real-tip tokens; the 2025 tips come from data/schedule/raw_2025-26.parquet. Only after `require_ready`."""
    with X25.season_bound(HOLDOUT):
        exp, info = build_cur_rt(HOLDOUT, db, elo_config)
    got = sorted(int(s) for s in exp.meta["season"].unique())
    if got != [2022, 2023, 2024, 2025]:
        raise RuntimeError(f"export seasons {got}")
    return exp, info


def export25(
    sha: str | None, run: Callable[..., str] = RULE.git, out: Path | None = None
) -> dict[str, Any]:
    sha = sha or RULE.frozen_sha()
    RULE.require_ready(sha, run)
    out = out or OUT_DIR
    exp, ci = build_current25_rt()
    assert_hist_files_match(exp, out)
    info: dict[str, Any] = {
        "rule_sha256": sha, "npz": str(X.write(exp, out, "rt_cur25")), "targets": int(len(exp.meta)),
        "rows_by_season": {str(s): int((exp.meta["season"] == s).sum()) for s in (2022, 2023, 2024, 2025)}, **ci,
    }  # fmt: skip
    info["npz_sha256"] = RULE.file_sha256(Path(info["npz"]))
    info["meta_sha256"] = RULE.file_sha256(out / "seq_ensemble_rt_cur25_meta.parquet")
    (out / "export_info.json").write_text(json.dumps(info, indent=2, default=str))
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("cmd", choices=("selection", "hist", "export25"))
    ap.add_argument("--sha", default=None)
    a = ap.parse_args(argv)
    print(
        json.dumps(selection() if a.cmd == "selection" else export25(a.sha), indent=2, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
