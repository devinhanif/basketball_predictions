"""Normalise existing experiment artifacts into the OOF contract (read-only).

Every loader reads a parquet that an earlier, separately-run job produced and
returns a frame accepted by :func:`nba.stack.oof.validate_oof` (19-level
quantile grid at ``TAUS`` for props; ``p`` with ``player_id = -1`` for win).
Nothing here touches ``nba.duckdb`` or season >= 2025; the loaders refuse any
frozen-season row.

Provenance of ``made_with_data_through``:

* context_residual / seq_props: recorded in the artifact itself.
* ctxres_v2 exp-2 arms: month-block walk-forward, each month trained on rows
  strictly before the month, so ``month_start(fold_id) - 1 day``.
* injury / MOV Elo: recorded in the artifact (cast to a date).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from nba.stack import FROZEN_SEASON
from nba.stack.oof import GAME_LEVEL_PLAYER, N_Q

PROP_STATS = ("pts", "reb", "ast", "fg3m")
_ID = ["game_id", "player_id"]


def _guard_seasons(df: pd.DataFrame, what: str) -> None:
    if "season" in df.columns and (df["season"] >= FROZEN_SEASON).any():
        raise ValueError(f"{what}: contains season >= {FROZEN_SEASON} (frozen holdout)")


def _grid_ok(col: pd.Series, what: str) -> None:
    bad = col.map(lambda v: v is None or len(v) != N_Q).sum()
    if bad:
        raise ValueError(f"{what}: {int(bad)} rows without a {N_Q}-level grid")


def _props_frame(df: pd.DataFrame, what: str) -> pd.DataFrame:
    """Keep contract columns, props stats only, played-row grids required."""
    _guard_seasons(df, what)
    out = df[df["target"].isin(PROP_STATS)].copy()
    _grid_ok(out["q_grid"], what)
    keep = [
        *_ID,
        "target",
        "game_date",
        "season",
        "fold_id",
        "made_with_data_through",
        "mean",
        "q_grid",
    ]
    out = out[keep]
    out["game_date"] = pd.to_datetime(out["game_date"])
    out["made_with_data_through"] = pd.to_datetime(out["made_with_data_through"])
    out["q_grid"] = [np.asarray(v, dtype=float) for v in out["q_grid"]]
    return out.reset_index(drop=True)


def context_residual_frame(path: str | Path) -> pd.DataFrame:
    """``reports/context_residual/oof_context_residual.parquet`` (seasons 2023-24)."""
    return _props_frame(pd.read_parquet(path), "context_residual")


def seq_props_frame(path: str | Path) -> pd.DataFrame:
    """``data/colab/runs/seq_props/<run>/oof_2024.parquet`` (season 2024 only)."""
    return _props_frame(pd.read_parquet(path), "seq_props")


def exp2_arm_frame(path: str | Path, dates: pd.DataFrame) -> pd.DataFrame:
    """One ctxres_v2 exp-2 arm parquet (``variant, stat, game_id, player_id, fold_id,
    y, mean, q19``) -> OOF frame. ``dates`` supplies ``game_id, player_id, target,
    game_date, season`` (taken from an already-normalised OOF with the same rows,
    e.g. :func:`context_residual_frame`); arm rows with no date are dropped."""
    raw = pd.read_parquet(path)
    if raw["q19"].isna().any():
        raise ValueError(f"{Path(path).name}: arm stores no q19 grid (light arm); cannot be routed")
    raw = raw.rename(columns={"stat": "target", "q19": "q_grid"})
    d = dates[[*_ID, "target", "game_date", "season"]].drop_duplicates([*_ID, "target"])
    out = raw.merge(d, on=[*_ID, "target"], how="inner", validate="one_to_one")
    month_start = pd.to_datetime(out["fold_id"].astype(str) + "01", format="%Y%m%d")
    out["made_with_data_through"] = month_start - pd.Timedelta(days=1)
    return _props_frame(out, "exp2 arm")


def elo_frames(path: str | Path) -> dict[str, pd.DataFrame]:
    """``data/injury_elo/oof_predictions.parquet`` -> ``{model: win frame}``.

    Season 2022 is kept (Elo warm-up) so the store matches the artifact; the
    router build restricts the fit window itself.
    """
    raw = pd.read_parquet(path)
    _guard_seasons(raw, "elo")
    out: dict[str, pd.DataFrame] = {}
    for model, g in raw.groupby("model"):
        ts = pd.to_datetime(g["made_with_data_through"])
        gd = pd.to_datetime(g["game_date"])
        # Injury Elo stamps its pre-tip report cutoff (17:00 on game day). That is a legitimate
        # pre-tip timestamp but is not date-strictly-before; the date-grain contract records the
        # rating state (previous day) and keeps the real timestamp in ``meta``.
        if (ts > gd + pd.Timedelta(hours=19)).any():
            raise ValueError(f"{model}: data cutoff after the 19:00 tip-off proxy")
        same_day = ts.dt.normalize() >= gd
        thru = ts.dt.normalize().where(~same_day, gd - pd.Timedelta(days=1))
        meta = [
            json.dumps({"cutoff_ts": str(t)}) if sd else "{}"
            for t, sd in zip(ts, same_day, strict=True)
        ]
        frame = pd.DataFrame(
            {
                "game_id": g["game_id"].astype(str).to_numpy(),
                "player_id": GAME_LEVEL_PLAYER,
                "target": "win",
                "game_date": pd.to_datetime(g["game_date"]).to_numpy(),
                "season": g["season"].to_numpy(),
                "made_with_data_through": thru.to_numpy(),
                "meta": meta,
                "p": g["p"].to_numpy(dtype=float),
            }
        )
        out[str(model)] = frame.reset_index(drop=True)
    return out
