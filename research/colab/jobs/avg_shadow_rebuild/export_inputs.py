# ruff: noqa: E501
"""Inputs of the AVG_SHADOW_LIVE chain rebuild (the maintainer runs this on the Mac; HOLDOUT_ACCESS_LOG row 72 is the systems/training use it needs).

    uv run python research/colab/jobs/avg_shadow_rebuild/export_inputs.py cur     # 2022-2025 with the real-tip tokens of T478: seq_ensemble_rt_cur25.npz
    uv run python research/colab/jobs/avg_shadow_rebuild/export_inputs.py hist    # 2019-2021 only: seq_ensemble_rt_hist.npz

Both use T478's builders unchanged (``research.features.seq_full_mix_rt_export``) except one filter: ``data/history`` now also holds 2013-2018, and W_ALL is
pinned to 2019-20 on, so the history export keeps only games of seasons 2019-2021 (the raw tables are cut before the builder, which then sees exactly the
rows it saw when T478's history export was made). The outputs go to ``data/colab/avg_shadow_rebuild/`` (T478's own files are not overwritten), with
``export_info.json`` recording the data_version of the database the export was built from.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research.features import player_sequences as P
from research.features import seq_ensemble_export as X0
from research.features import seq_full_mix_rt_export as X

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/colab/avg_shadow_rebuild"
W_ALL_FIRST = 2019


def build_hist_w_all(
    elo_config: str = "configs/mov_elo_tuned.yaml",
) -> tuple[P.SeqExport, dict[str, Any]]:
    raw = P.load_raw(str(X.HIST_DB), max_season=max(X.HIST_SEASONS))
    keep = raw.games["season"] >= W_ALL_FIRST
    ids = set(raw.games.loc[keep, "game_id"])
    raw.games = raw.games[keep].reset_index(drop=True)
    for name in ("pgs", "poss_player", "poss_team", "avail"):
        df = getattr(raw, name)
        setattr(raw, name, df[df["game_id"].isin(ids)].reset_index(drop=True))
    got = sorted(int(s) for s in raw.games["season"].unique())
    if got != list(X.HIST_SEASONS):
        raise ValueError(f"history seasons {got} != {list(X.HIST_SEASONS)}")
    raw.static = X0.merge_static(raw.static, X0.cache_static())
    X0._elo(raw, elo_config)
    raw.tips, cover = X.tip_table(X.HIST_SCHEDULE, raw.games)
    raw.no_report_seasons = X.NO_REPORT_HIST
    exp = P.build_export(
        raw, X.real_cfg(min_target_season=min(X.HIST_SEASONS), max_season=max(X.HIST_SEASONS))
    )
    return exp, {
        "tip_coverage": cover,
        "era": X.add_era(exp, X.cur_flags(str(X.HIST_DB), max(X.HIST_SEASONS))),
        "position": X0.residual_position_share(exp),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("which", choices=("cur", "hist"))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    from nba.daily.stamps import current_data_version

    info: dict[str, Any] = {"data_version": current_data_version()}
    if a.which == "hist":
        exp, extra = build_hist_w_all()
        assert int(exp.meta["season"].max()) <= max(X.HIST_SEASONS)
        npz = X0.write(exp, a.out, "rt_hist")
    else:
        exp, extra = X.build_current25_rt()
        if (a.out / "seq_ensemble_rt_hist.npz").exists():
            X.assert_hist_files_match(exp, a.out)
        npz = X0.write(exp, a.out, "rt_cur25")
    info.update(
        which=a.which,
        npz=str(npz),
        targets=int(len(exp.meta)),
        seasons=sorted(int(s) for s in exp.meta["season"].unique()),
        **extra,
    )
    (a.out / f"export_info_{a.which}.json").write_text(json.dumps(info, indent=2, default=str))
    print(json.dumps(info, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
