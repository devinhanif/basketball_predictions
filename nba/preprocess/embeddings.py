"""Player co-occurrence embeddings (CLAUDE.md "Sequence embeddings
(word2vec-style)"; cold-start method 5, "kNN similar-player prior").

**Hypothesis**: players who repeatedly share the floor (same 5-man unit,
offense or defense) carry a learnable notion of "plays like"/"plays with"
that height/weight/position archetypes (cold-start method 2) don't fully
capture -- e.g. a small-ball 4 who shares lineups with wings should embed
nearer those wings than near traditional bigs, even if their listed
position says otherwise. **A/B plan (ml-engineer)**: feed this embedding
(or its kNN neighbor average) as an additional input to the rookie/low-
minute cold-start prior (cold-start method 5) and compare walk-forward
log loss / CRPS on cold-start-bucketed games against the existing
archetype-only prior; keep only if it moves the metric outside bootstrap
noise -- an embedding that moves nothing is a documented negative, not a
ship (see "Weak spots" below for the honest prior on that outcome).

## Method

1. **Co-occurrence matrix.** For every possession with non-null 5-man
   ``off_players``/``def_players`` (``nba.parse.lineups.
   attach_lineups_to_possessions`` output), every unordered pair of
   players *within the same 5-man unit* increments a symmetric
   co-occurrence count. Both the offensive and defensive unit count --
   a "unit" here means "shared the floor together", not "same team
   permanently"; a trade or a new lineup combo simply starts
   accumulating fresh co-occurrence from that point on.
2. **PPMI weighting** (positive pointwise mutual information, the
   standard word2vec/GloVe co-occurrence weighting -- see
   ``_positive_pmi``): downweights pairs that co-occur a lot only
   because both players individually appear in a lot of possessions,
   and zeroes out pairs that co-occur less than chance.
3. **Truncated SVD** (``sklearn.decomposition.TruncatedSVD``, seeded) on
   the PPMI matrix gives each player a dense ``dim``-length vector --
   the matrix-factorization equivalent of skip-gram embeddings, at a
   fraction of the compute cost (CLAUDE.md "cheapest possible", no
   PyTorch needed for this). ``n_components`` is silently clipped to
   ``min(dim, vocab_size - 1)`` when the vocabulary is small (documented
   fixture-compatible fallback, not a silent wrong answer).

## As-of discipline (the leakage-critical part)

:func:`build_player_embeddings` takes an explicit ``as_of_date`` and
builds the co-occurrence matrix **only** from possessions belonging to
games with ``game_date < as_of_date`` (strict, matching every other
as-of builder's "strictly prior" convention) -- optionally further
restricted to one ``season`` (per-season embeddings, since a lineup
co-occurrence signal from three seasons ago is a much weaker "plays
like" prior than this season's). An embedding used to featurize a game
must be built with ``as_of_date`` set to that game's own ``game_date``
(or earlier), so it never sees that game's or any later game's lineups.
See ``tests/preprocess/test_embeddings.py::test_planted_future_game_no_leakage``
for the proof.

## Weak spots (honest, not hidden)

- **Low-minute / bench players**: few shared possessions means a noisy,
  near-random embedding -- the co-occurrence signal needs real volume to
  mean anything, which is exactly the population cold-start care about
  most. ``min_count`` filters out players below a possession-count floor
  entirely (returned dict simply omits them) rather than emitting a
  fabricated vector; callers needing *some* vector for those players
  should fall back to the archetype prior (cold-start method 2), not
  this module.
- **Small/fixture data**: with only a handful of games the co-occurrence
  matrix is tiny and SVD components are not meaningful signal, only a
  shape-correct placeholder -- this is a known, documented limitation of
  testing against the committed fixture, not a claim that fixture-scale
  embeddings are useful.
- **No position-play-style disambiguation**: a player sharing the floor
  with a rotation of different lineups (common for a high-minute
  everyone-overlaps-with-them starter) has a "popular" embedding that is
  not necessarily "similar style", a known limitation of raw co-
  occurrence (vs. e.g. a skip-gram negative-sampling objective); PPMI
  partially corrects for raw popularity but not fully.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
from sklearn.decomposition import TruncatedSVD

DEFAULT_SEED = 0
LINEUP_SIZE = 5
_EPS = 1e-12

_LINEUP_GROUPS_SQL = """
SELECT p.off_players AS off_players, p.def_players AS def_players
FROM possessions p
JOIN games g USING (game_id)
WHERE g.game_date < ?
"""
_LINEUP_GROUPS_SQL_SEASON = _LINEUP_GROUPS_SQL + " AND g.season = ?"


def _fetch_lineup_groups(
    con: duckdb.DuckDBPyConnection, as_of_date: dt.date | str, season: int | None
) -> list[list[int]]:
    """Every non-null, exactly-5-player on-court unit (offense or defense)
    from possessions strictly before ``as_of_date`` (and ``season`` if
    given) -- see module docstring "As-of discipline"."""
    if season is None:
        con.execute(_LINEUP_GROUPS_SQL, [as_of_date])
    else:
        con.execute(_LINEUP_GROUPS_SQL_SEASON, [as_of_date, season])
    rows = con.fetchall()
    groups: list[list[int]] = []
    for off_players, def_players in rows:
        for grp in (off_players, def_players):
            if grp is not None and len(grp) == LINEUP_SIZE and all(p is not None for p in grp):
                groups.append([int(p) for p in grp])
    return groups


def build_cooccurrence_counts(
    groups: list[list[int]],
) -> tuple[list[int], np.ndarray, np.ndarray]:
    """Symmetric co-occurrence count matrix from a list of 5-man units.

    Returns ``(vocab, counts, cooc)`` where ``vocab[i]`` is the player_id
    for row/col ``i``, ``counts[i]`` is player ``i``'s total number of
    on-court-unit appearances (its own diagonal mass, used as the PPMI
    marginal), and ``cooc`` is the ``len(vocab) x len(vocab)`` symmetric
    count matrix (diagonal left at 0 -- a player doesn't "co-occur" with
    themselves).
    """
    vocab_set: set[int] = set()
    for grp in groups:
        vocab_set.update(grp)
    vocab = sorted(vocab_set)
    index = {pid: i for i, pid in enumerate(vocab)}
    n = len(vocab)
    cooc = np.zeros((n, n), dtype=np.float64)
    counts = np.zeros(n, dtype=np.float64)
    for grp in groups:
        idxs = [index[pid] for pid in grp]
        for i in idxs:
            counts[i] += 1.0
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                i, j = idxs[a_pos], idxs[b_pos]
                if i == j:
                    continue
                cooc[i, j] += 1.0
                cooc[j, i] += 1.0
    return vocab, counts, cooc


def _positive_pmi(cooc: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Positive PMI weighting of a symmetric co-occurrence matrix (standard
    word2vec/GloVe-style transform -- see module docstring, step 2)."""
    total = float(cooc.sum())
    if total <= 0.0:
        return np.zeros_like(cooc)
    p_ij = cooc / total
    p_i = counts / max(counts.sum(), _EPS)
    denom = np.outer(p_i, p_i)
    with np.errstate(divide="ignore", invalid="ignore"):
        pmi = np.log(np.where(p_ij > 0, p_ij / np.maximum(denom, _EPS), 1.0))
    pmi = np.where(p_ij > 0, pmi, 0.0)
    return np.maximum(pmi, 0.0)


def build_player_embeddings(
    con: duckdb.DuckDBPyConnection,
    as_of_date: dt.date | str,
    dim: int = 16,
    season: int | None = None,
    min_count: int = 1,
    seed: int = DEFAULT_SEED,
) -> dict[int, np.ndarray]:
    """Fit a co-occurrence + PPMI + truncated-SVD embedding per player,
    using only possessions strictly before ``as_of_date`` (and ``season``
    if given) -- see module docstring "As-of discipline".

    ``min_count``: players with fewer than this many total on-court-unit
    appearances in the as-of window are dropped from the output entirely
    (see "Weak spots" in the module docstring) rather than given a
    fabricated vector.

    Returns ``{player_id: np.ndarray of shape (k,)}`` where ``k =
    min(dim, vocab_size - 1)`` (documented small-vocab fallback). Returns
    ``{}`` if there is no as-of possession data at all, or fewer than 2
    players survive ``min_count`` (SVD needs at least 2 to be
    well-defined).
    """
    groups = _fetch_lineup_groups(con, as_of_date, season)
    if not groups:
        return {}

    vocab, counts, cooc = build_cooccurrence_counts(groups)
    keep_mask = counts >= float(min_count)
    if keep_mask.sum() < 2:
        return {}
    kept_vocab = [pid for pid, keep in zip(vocab, keep_mask, strict=True) if keep]
    kept_idx = np.where(keep_mask)[0]
    cooc_kept = cooc[np.ix_(kept_idx, kept_idx)]
    counts_kept = counts[kept_idx]

    ppmi = _positive_pmi(cooc_kept, counts_kept)
    n = ppmi.shape[0]
    n_components = max(1, min(int(dim), n - 1))
    if n_components < 1:
        return {}

    svd = TruncatedSVD(n_components=n_components, random_state=seed)
    emb = svd.fit_transform(ppmi)
    return {pid: emb[i].astype(np.float64) for i, pid in enumerate(kept_vocab)}


def k_nearest_players(
    embeddings: dict[int, np.ndarray], player_id: int, k: int = 5
) -> list[tuple[int, float]]:
    """``k`` nearest neighbors of ``player_id`` by cosine similarity, from
    an embedding dict produced by :func:`build_player_embeddings`.

    Returns ``[(neighbor_player_id, cosine_similarity), ...]`` sorted by
    descending similarity, excluding ``player_id`` itself. Returns ``[]``
    if ``player_id`` is not in ``embeddings`` or fewer than one other
    player is present.
    """
    if player_id not in embeddings or len(embeddings) < 2:
        return []
    query = embeddings[player_id]
    query_norm = float(np.linalg.norm(query))
    if query_norm < _EPS:
        return []
    sims: list[tuple[int, float]] = []
    for pid, vec in embeddings.items():
        if pid == player_id:
            continue
        vec_norm = float(np.linalg.norm(vec))
        if vec_norm < _EPS:
            continue
        cos_sim = float(np.dot(query, vec) / (query_norm * vec_norm))
        sims.append((pid, cos_sim))
    sims.sort(key=lambda t: t[1], reverse=True)
    return sims[: max(0, k)]
