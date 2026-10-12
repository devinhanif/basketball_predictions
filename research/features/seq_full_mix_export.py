# ruff: noqa: E501
"""The season-2022..2025 sequence export for SEQ_FULL_MIX (docs/prereg/SEQ_FULL_MIX.md).

This is the ONLY code that builds a sequence export containing season 2025, and it refuses unless the rule is frozen AND a committed,
Devin-confirmed HOLDOUT_ACCESS_LOG row exists (research/eval/seq_full_mix_rule.py). It reuses the ENSEMBLE `cur` build unchanged (same builder,
same MOV-Elo config, same proxy19 report tokens as E2 used on 2023/24) with the season bound lifted for the duration of the build only.

What the 2025 rows hold: the model INPUTS (windows of strictly earlier games, as-of context) and, as the training labels the walk-forward needs, the
targets `y` and `base`. As in E2's walk-forward on 2023/24, the warm refit before each month trains on every row dated strictly before that month,
which for 2025 includes the earlier 2025 months' outcomes as training labels (never the month being predicted). Nothing is scored on the Mac side
until the run has produced out-of-fold forecasts.

    uv run python -m research.features.seq_full_mix_export export [--sha <64 hex>]   # default sha: hash of the committed rule document
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.eval import seq_full_mix_rule as RULE
from research.features import player_sequences as P
from research.features import seq_ensemble_export as X

OUT_DIR = RULE.ROOT / "data/colab/seq_full_mix"
STORED_CUR = RULE.ROOT / "data/colab/seq_ensemble/seq_ensemble_cur.npz"
STORED_META = RULE.ROOT / "data/colab/seq_ensemble/seq_ensemble_cur_meta.parquet"
HOLDOUT = 2025
SEASONS = (2022, 2023, 2024, 2025)
IDENTITY_KEYS = ("seq", "mask", "opp_step", "ctx", "team_idx", "opp_idx", "y", "base", "n_prior")


@contextmanager
def season_bound(cap: int) -> Iterator[None]:
    """Lift `player_sequences.FROZEN_SEASON` (the bound `load_raw` and `build_export` enforce) to cap + 1 for the build only; restored on exit."""
    old = P.FROZEN_SEASON
    P.FROZEN_SEASON = cap + 1
    try:
        yield
    finally:
        P.FROZEN_SEASON = old


def build_current25(
    db_path: str = "nba.duckdb", elo_config: str = "configs/mov_elo_tuned.yaml"
) -> P.SeqExport:
    """Seasons 2022-2025, read-only DB. Only call after `require_ready`."""
    with season_bound(HOLDOUT):
        raw = P.load_raw(db_path, max_season=HOLDOUT)
        X._elo(raw, elo_config)
        exp = P.build_export(raw, P.SeqConfig(max_season=HOLDOUT))
    got = sorted(int(s) for s in exp.meta["season"].unique())
    if got != list(SEASONS):
        raise RuntimeError(f"export seasons {got} != {list(SEASONS)}")
    return exp


def identity_report(
    new: P.SeqExport, stored: dict[str, np.ndarray], stored_meta: pd.DataFrame
) -> dict[str, Any]:
    """Do the 2022-2024 rows of the new export equal the stored ENSEMBLE export (the as-of property, and no data drift since)? Reported, never a rule."""
    key = ["game_id", "player_id"]
    nm = new.meta.reset_index(drop=True)[key].assign(i=np.arange(len(new.meta)))
    om = stored_meta.reset_index(drop=True)[key].assign(j=np.arange(len(stored_meta)))
    j = om.merge(nm, on=key, how="left")
    found = j["i"].notna().to_numpy()
    out: dict[str, Any] = {
        "stored_rows": int(len(om)),
        "stored_rows_found": int(found.sum()),
        "new_rows_le_2024": int((new.meta["season"] <= 2024).sum()),
    }
    ii = j.loc[found, "i"].to_numpy().astype(np.int64)
    jj = j.loc[found, "j"].to_numpy().astype(np.int64)
    eq: dict[str, float] = {}
    for k in IDENTITY_KEYS:
        a, b = new.arrays[k][ii], stored[k][jj]
        same = (a == b) | (np.isnan(a) & np.isnan(b)) if a.dtype.kind == "f" else (a == b)
        eq[k] = float(same.reshape(len(ii), -1).all(axis=1).mean()) if len(ii) else float("nan")
    out["share_rows_equal"] = eq
    return out


def export(
    sha: str | None, run: Callable[..., str] = RULE.git, out: Path | None = None
) -> dict[str, Any]:
    sha = sha or RULE.frozen_sha()
    RULE.require_ready(sha, run)
    out = out or OUT_DIR
    exp = build_current25()
    info: dict[str, Any] = {
        "rule_sha256": sha,
        "npz": str(X.write(exp, out, "cur25")),
        "targets": int(len(exp.meta)),
        "rows_by_season": {str(s): int((exp.meta["season"] == s).sum()) for s in SEASONS},
        "games_2025": int(exp.meta.loc[exp.meta["season"] == HOLDOUT, "game_id"].nunique()),
    }
    if STORED_CUR.exists() and STORED_META.exists():
        stored = {k: v for k, v in np.load(STORED_CUR, allow_pickle=False).items()}
        info["identity_vs_stored_export"] = identity_report(
            exp, stored, pd.read_parquet(STORED_META)
        )
    (out / "export_info.json").write_text(json.dumps(info, indent=2, default=str))
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("cmd", choices=("export",))
    ap.add_argument("--sha", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(export(a.sha), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
