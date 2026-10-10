"""Descriptive per-cell evidence: which candidate scores best where (never a decision).

Cells are single-dimension groups (cold-start bucket, season phase, starter/bench,
teammates out, minutes trend, archetype, position group, line region ...). No model
is fit here: each candidate's own as-of OOF score is averaged per cell.

Guardrails (pre-registered in docs/ROUTING.md):

* a cell may carry a "best model" claim only if it has >= ``MIN_CELL_ROWS`` rows AND
  >= ``MIN_CELL_GAMES`` distinct games; smaller cells report ``claim = None``;
* the claim is "best candidate in the cell beats the target's overall best single
  candidate in that cell" with a game-clustered paired bootstrap p-value,
  Benjamini-Hochberg adjusted over ALL eligible cells of the target (q < 0.05);
* a cell where the overall champion is already best never yields a claim
  (nothing to route);
* cell evidence is a hypothesis generator. The router is judged as a whole against
  the best single model per target, never cell by cell.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from nba.props.metrics import _clustered_boot_means
from research.stack.data import StackData
from research.stack.evaluate import benjamini_hochberg

MIN_CELL_ROWS = 500
MIN_CELL_GAMES = 150
ALPHA = 0.05


@dataclass
class Cell:
    dimension: str
    group: str
    n: int
    games: int
    scores: dict[str, float]
    best: str
    delta_vs_champion: float  # best minus overall champion in this cell (<= 0)
    p: float
    q: float
    eligible: bool
    claim: str | None  # candidate id that is significantly better than the champion, else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cell_evidence(
    data: StackData,
    slices: dict[str, np.ndarray],
    *,
    n_boot: int = 1000,
    seed: int = 0,
    min_rows: int = MIN_CELL_ROWS,
    min_games: int = MIN_CELL_GAMES,
) -> tuple[str, list[Cell]]:
    """Returns (overall champion id, cells). Scores are mean per-row scores."""
    sc = data.cand_scores()
    cands = data.candidates
    champ_j = int(sc.mean(axis=0).argmin())
    gid = data.keys["game_id"].to_numpy()
    cells: list[Cell] = []
    for dim, lab in slices.items():
        lab = np.asarray(lab).astype(str)
        for g in sorted(set(lab.tolist())):
            m = lab == g
            n = int(m.sum())
            games = int(len(np.unique(gid[m])))
            means = sc[m].mean(axis=0)
            bj = int(means.argmin())
            eligible = n >= min_rows and games >= min_games
            p = 1.0
            if eligible and bj != champ_j:
                d = sc[m, bj] - sc[m, champ_j]
                boot = _clustered_boot_means(d, gid[m], n_boot, np.random.default_rng(seed))
                p = float(
                    min(1.0, max(2.0 * min((boot >= 0).mean(), (boot <= 0).mean()), 1 / n_boot))
                )
            cells.append(
                Cell(
                    dim,
                    g,
                    n,
                    games,
                    {c: float(v) for c, v in zip(cands, means, strict=True)},
                    cands[bj],
                    float(means[bj] - means[champ_j]),
                    p,
                    1.0,
                    eligible,
                    None,
                )
            )
    pool_idx = [i for i, c in enumerate(cells) if c.eligible and c.best != cands[champ_j]]
    if pool_idx:
        qs = benjamini_hochberg(np.array([cells[i].p for i in pool_idx]))
        for i, q in zip(pool_idx, qs, strict=True):
            cells[i].q = float(q)
            if q < ALPHA and cells[i].delta_vs_champion < 0:
                cells[i].claim = cells[i].best
    return cands[champ_j], cells
