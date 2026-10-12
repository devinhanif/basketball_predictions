# ruff: noqa: E501
"""Real-data gates for the production-state lookup (docs/prereg/RECENT_ROLE.md). Light: no model is fitted."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from nba.props.context_residual import _CI, _DI, player_states
from research.recent_role import prod_state as PS

PARITY_TOL = 1e-9
N_DNP_SAMPLE = 300


def parity_vs_build_features(box: pl.DataFrame, feats: pl.DataFrame) -> dict[str, Any]:
    """prod_state_rows on every PLAYED row of the production feature frame must equal build_features' columns."""
    q = feats.select("game_id", "player_id", "team_id", "season", "game_date")
    got = PS.prod_state_rows(box, q)
    worst, worst_col, bad_rows = 0.0, "", 0
    bad = np.zeros(feats.height, dtype=bool)
    for c in PS.ALL_STATE_COLS:
        a, b = got[c].to_numpy().astype(float), feats[c].to_numpy().astype(float)
        both_nan = np.isnan(a) & np.isnan(b)
        d = np.where(both_nan, 0.0, np.abs(a - b))
        d = np.where(np.isnan(d), np.inf, d)
        bad |= d > PARITY_TOL
        if float(d.max()) > worst:
            worst, worst_col = float(d.max()), c
    bad_rows = int(bad.sum())
    return {
        "n_rows": feats.height,
        "max_abs_diff": worst,
        "worst_col": worst_col,
        "rows_off": bad_rows,
        "ok": bad_rows == 0,
    }


def dnp_truncation_truth(
    box: pl.DataFrame, dnp: pl.DataFrame, n: int = N_DNP_SAMPLE, seed: int = 0
) -> dict[str, Any]:
    """For a sample of DNP rows, the lookup equals the forward-path truth: the player's played history before the date plus a
    placeholder row at the query date, run through the unchanged ``player_states``."""
    samp = dnp.sample(n=min(n, dnp.height), seed=seed)
    got = PS.prod_state_rows(
        box, samp.select("game_id", "player_id", "team_id", "season", "game_date")
    )
    played = box.filter(pl.col("minutes") > 0).select(PS.PLAYED_COLS)
    checked, worst = 0, 0.0
    for r, g in zip(samp.iter_rows(named=True), got.iter_rows(named=True), strict=True):
        hist = played.filter(
            (pl.col("player_id") == r["player_id"]) & (pl.col("game_date") < r["game_date"])
        )
        if hist.height == 0:
            continue
        probe = pl.DataFrame({"player_id": [r["player_id"]], "team_id": [r["team_id"]], "season": [r["season"]], "game_date": [r["game_date"]],
                              "game_id": ["~probe"], "pts": [0], "reb": [0], "ast": [0], "fg3m": [0], "minutes": [1.0], "starter": [False]}).cast(hist.schema)  # fmt: skip
        both = pl.concat([hist, probe], how="vertical_relaxed").sort(
            ["player_id", "game_date", "game_id"]
        )
        st = player_states(both)
        i = both.height - 1
        pre = st["pre"][i]
        want = {"n_prior": float(st["n_prior"][i]), "n_season": float(st["n_season"][i]), "gap_days": float(st["gap_days"][i]), "since_flag": float(st["since_flag"][i]),
                "flag_dir": float(st["flag_dir"][i]), "since_team": float(st["since_team"][i]), "min5": pre[_DI[5.0], _CI["min"]], "min10": pre[_DI[10.0], _CI["min"]],
                "min40": pre[_DI[40.0], _CI["min"]], "starter10": pre[_DI[10.0], _CI["starter"]], "starter40": pre[_DI[40.0], _CI["starter"]],
                "last_starter": float(both["starter"].cast(pl.Float64).to_numpy()[i - 1])}  # fmt: skip
        for c, v in want.items():
            worst = max(worst, 0.0 if (np.isnan(v) and np.isnan(g[c])) else abs(g[c] - v))
        checked += 1
    return {
        "n_checked": checked,
        "max_abs_diff": worst,
        "ok": checked >= 0.9 * min(n, dnp.height) and worst <= PARITY_TOL,
    }


def state_gates(aux: dict[str, Any], fr: pl.DataFrame) -> list[dict[str, Any]]:
    from research.eval import four_factors_eval as F
    from research.eval.workload_eval import DB, _check

    study = F.load_study(DB)
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    par = parity_vs_build_features(aux["box"], feats)
    dnp = fr.filter(pl.col("played") == 0.0)
    trunc = dnp_truncation_truth(aux["box"], dnp)
    return [
        _check(
            "production-state lookup equals build_features on every played row",
            par,
            f"max abs diff <= {PARITY_TOL}, 0 rows off",
            par["ok"],
        ),
        _check(
            "production-state lookup equals the forward placeholder truth on sampled DNP rows",
            trunc,
            f"{N_DNP_SAMPLE} sampled, max abs diff <= {PARITY_TOL}",
            trunc["ok"],
        ),
    ]
