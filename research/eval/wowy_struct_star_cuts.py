# ruff: noqa: E501
"""WOWY_STRUCT G5 star-out cut-offs: the 0.75 quantile of V_pts and V_ast (production vac_pts / vac_ast, as-of features)
among 2020-21 rows with a usable report and a non-empty rotation OUT set. Features only; seasons 2020-21 only.

    cd <worktree> && NBA_ROOT=<main checkout> .venv/bin/python -W ignore -m research.eval.wowy_struct_star_cuts
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import polars as pl

from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_struct_gate as SG

QUANTILE = 0.75
SEASONS = (2020, 2021)


def star_cuts(feats: pl.DataFrame) -> dict[str, Any]:
    """Quantile cut-offs of V_s over rows of ``SEASONS`` with ``has_report == 1`` and ``n_out_rot > 0``."""
    sel = feats.filter(
        pl.col("season").is_in(list(SEASONS))
        & (pl.col("has_report") == 1)
        & (pl.col("n_out_rot") > 0)
    )
    out: dict[str, Any] = {"n_rows": sel.height, "seasons": list(SEASONS), "quantile": QUANTILE}
    for stat in ("pts", "ast"):
        v = sel[f"vac_{stat}"].drop_nulls().to_numpy()
        out[f"V_{stat}"] = float(np.quantile(v, QUANTILE))
        out[f"n_{stat}"] = int(len(v))
    return out


def main() -> dict[str, Any]:
    feats, _, _ = SG.warm_frame(
        str(G.REAL / "nba.duckdb"), str(G.REAL / "data/history/nba_history.duckdb")
    )
    res = star_cuts(feats.filter(pl.col("season").is_in(list(SEASONS))))
    print(json.dumps(res, indent=1), flush=True)
    return res


if __name__ == "__main__":
    main()
