"""Aligned candidate tensors for stacking."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.stack import FROZEN_SEASON
from research.stack.scoring import score_rows

KEYS = ["game_id", "player_id"]


@dataclass
class StackData:
    target: str
    candidates: list[str]
    keys: pd.DataFrame  # game_id, player_id, game_date (datetime64), season
    y: np.ndarray  # (n,)
    cand: np.ndarray  # props (n, k, 19); win (n, k)
    context: pd.DataFrame  # (n, d), index aligned to keys

    @property
    def n(self) -> int:
        return len(self.y)

    @property
    def dates(self) -> np.ndarray:
        return np.asarray(self.keys["game_date"].to_numpy(dtype="datetime64[D]"))

    def subset(self, mask: np.ndarray) -> StackData:
        return StackData(
            self.target,
            self.candidates,
            self.keys[mask].reset_index(drop=True),
            self.y[mask],
            self.cand[mask],
            self.context[mask].reset_index(drop=True),
        )

    def cand_scores(self) -> np.ndarray:
        """(n, k) per-row score of each single candidate."""
        cols = [
            score_rows(self.target, self.cand[:, j], self.y) for j in range(len(self.candidates))
        ]
        return np.asarray(np.column_stack(cols))


def build_stack_data(
    target: str,
    oof_frames: dict[str, pd.DataFrame],
    outcomes: pd.DataFrame,
    context: pd.DataFrame,
    *,
    max_season: int = FROZEN_SEASON - 1,
) -> StackData:
    """Inner-join candidates (only rows ALL candidates predicted), outcomes and context.

    ``outcomes``: game_id, player_id, y. ``context``: game_id, player_id + numeric/
    categorical features known as-of the game. Rows from seasons > ``max_season``
    are dropped (the frozen season is never loaded for fitting/selection).
    """
    names = list(oof_frames)
    if len(names) < 2:
        raise ValueError("need >= 2 candidates")
    base = outcomes[[*KEYS, "y"]].copy()
    base["game_id"] = base["game_id"].astype(str)
    for name, f in oof_frames.items():
        cols = KEYS + (["p"] if target == "win" else ["q_grid"])
        sub = f[cols].copy()
        sub["game_id"] = sub["game_id"].astype(str)
        sub = sub.rename(columns={cols[-1]: f"_pred_{name}"})
        base = base.merge(sub, on=KEYS, how="inner", validate="one_to_one")
    meta = next(iter(oof_frames.values()))[[*KEYS, "game_date", "season"]].copy()
    meta["game_id"] = meta["game_id"].astype(str)
    base = base.merge(meta, on=KEYS, how="inner")
    ctx = context.copy()
    ctx["game_id"] = ctx["game_id"].astype(str)
    base = base.merge(ctx, on=KEYS, how="inner", validate="one_to_one")
    base["game_date"] = pd.to_datetime(base["game_date"])
    base = base[base["season"] <= max_season]
    if base.empty:
        raise ValueError("no rows common to all candidates / outcomes / context")
    base = base.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    if target == "win":
        cand = np.column_stack([base[f"_pred_{n}"].to_numpy(dtype=float) for n in names])
    else:
        cand = np.stack(
            [np.vstack([np.asarray(v, dtype=float) for v in base[f"_pred_{n}"]]) for n in names],
            axis=1,
        )
    ctx_cols = [c for c in context.columns if c not in KEYS]
    return StackData(
        target,
        names,
        base[[*KEYS, "game_date", "season"]],
        base["y"].to_numpy(dtype=float),
        cand,
        base[ctx_cols].reset_index(drop=True),
    )
