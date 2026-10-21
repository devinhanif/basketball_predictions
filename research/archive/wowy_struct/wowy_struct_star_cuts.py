# ruff: noqa: E501
"""WOWY_STRUCT G5 star-out and heavy-absence cut-offs (features only; cut-offs from seasons 2020-21 only).

    cd <worktree> && NBA_ROOT=<main checkout> .venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_struct_star_cuts

* **Star out** = the largest single OUT rotation player's as-of per-game stat (``Vmax_s``, the half-life-10 ``v_s`` that feeds
  production ``vac_s``) is >= the 0.75 quantile of ``v_s`` among individual rotation players (one value per player-game, as of
  the game: the as-of rotation pool of every team-game of 2020-21 with a usable report).
* **Heavy absence** (reported only) = ``V_s`` (``vac_s``, the SUM over the OUT set) >= the 0.75 quantile of ``V_s`` among 2020-21
  rows with a usable report and a non-empty rotation OUT set.
Also reports the per-cell-season slice row counts for 2023 and 2024 (feature columns only; no outcome is used).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import polars as pl

from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import stat_frame
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import wowy_redistribution_gate as G

QUANTILE = 0.75
SEASONS = (2020, 2021)
CHOSEN = {"pts": "struct_u_pts", "ast": "struct_d_ast"}
CELLS = (("pts", "leader_out"), ("pts", "top25_fixed"), ("ast", "leader_out"))


def heavy_cuts(feats: pl.DataFrame) -> dict[str, Any]:
    """0.75 quantile of the SUM ``vac_s`` among ``SEASONS`` rows with a usable report and a non-empty OUT set."""
    sel = feats.filter(
        pl.col("season").is_in(list(SEASONS))
        & (pl.col("has_report") == 1)
        & (pl.col("n_out_rot") > 0)
    )
    out: dict[str, Any] = {"n_rows": sel.height}
    for stat in ("pts", "ast"):
        out[f"V_{stat}"] = float(np.quantile(sel[f"vac_{stat}"].drop_nulls().to_numpy(), QUANTILE))
    return out


def player_cuts(pool_vals: pl.DataFrame) -> dict[str, Any]:
    """0.75 quantile of the as-of ``st_s`` over player-game rows of ``pool_vals``."""
    out: dict[str, Any] = {"n_player_games": pool_vals.height}
    for stat in ("pts", "ast"):
        out[f"v_{stat}"] = float(
            np.quantile(pool_vals[f"st_{stat}"].drop_nulls().to_numpy(), QUANTILE)
        )
    return out


def main() -> dict[str, Any]:
    db = str(G.REAL / "nba.duckdb")
    hdb = str(G.REAL / "data/history/nba_history.duckdb")
    feats, outp, games = SG.warm_frame(db, hdb)
    full = pl.concat([SG.load_box_state(hdb), SG.load_box_state(db)], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = games.select("game_id", "game_date")
    pos = pl.concat([SG.load_positions(hdb), SG.load_positions(db)]).unique("player_id")
    fe = feats.filter(pl.col("season").is_in([2020, 2021, 2022, 2023, 2024]))
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    fr = SA.attach_struct(fe, SA.build_struct(rows, op, pool, states, dates, pos))

    # individual cut: as-of rotation pool of every 2020-21 team-game with a usable report
    g_ok = (
        fr.filter(pl.col("season").is_in(list(SEASONS)) & (pl.col("has_report") == 1))
        .select("game_id")
        .unique()
    )
    recs = [(g, t, k) for (g, t), ks in pool.items() for k in ks]
    pool_df = pl.DataFrame(
        recs, schema={"game_id": pl.Utf8, "team_id": pl.Int64, "player_id": pl.Int64}, orient="row"
    ).join(g_ok, on="game_id", how="inner")
    pv = SA._asof(pool_df.join(dates, on="game_id"), states)
    ind = player_cuts(pv)
    heavy = heavy_cuts(fr)

    f20, f21 = (fr.filter(pl.col("season") == y) for y in (2020, 2021))
    sel = pl.concat(
        [
            f.filter(
                (pl.col("vac_min") >= 24.0)
                & (pl.col("has_report") == 1)
                & (pl.col("n_out_rot") > 0)
            )
            for f in (f20, f21)
        ],
        how="vertical_relaxed",
    )
    cuts: dict[str, tuple[float, float]] = {}
    for stat, col in CHOSEN.items():
        a = np.abs(sel[col].to_numpy())
        a = a[a > 0]
        cuts[stat] = (float(np.quantile(a, 0.75)), float(np.quantile(a, 0.90)))
    counts: dict[str, Any] = {}
    for stat, sn in CELLS:
        for season in (2023, 2024):
            te = stat_frame(
                fr.filter((pl.col("season") == season) & (pl.col("has_report") == 1)), stat
            )
            cell = SG.cell_masks(te, CHOSEN[stat], f"leader_out_{stat}", cuts[stat])[sn]
            star = (te[f"Vmax_{stat}"] >= ind[f"v_{stat}"]).fill_null(False).to_numpy()
            hv = (te[f"V_{stat}"] >= heavy[f"V_{stat}"]).fill_null(False).to_numpy()
            counts[f"{stat}|{sn}|{season}"] = {
                "cell_rows": int(cell.sum()),
                "star_out": int((cell & star).sum()),
                "heavy_absence": int((cell & hv).sum()),
            }
    res = {
        "individual_cut(v_s, rotation player-games 2020-21)": ind,
        "heavy_cut(V_s, 2020-21 OUT rows)": heavy,
        "slice_counts_features_only": counts,
    }
    print(json.dumps(res, indent=1), flush=True)
    return res


if __name__ == "__main__":
    main()
