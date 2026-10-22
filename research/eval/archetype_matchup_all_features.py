# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL features, Amendment 2 (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

Adds to the reused T506 construction (which is NOT edited): (M2) archetype label alignment across seasons by Hungarian matching on cluster
centroids; the aligned season builder; the identity feature ``e_lg``; the missingness audit against REALISED minutes (the minutes array is an
argument, never read here). Read-only; every SQL is the T505/T506 loaders' (``season <= 2024``); season 2025 is never queried.

Why alignment (adversary M2). ``assign_archetypes`` orders labels by the cluster mean of its first column and re-clusters every season, so the
raw ``off_arch`` integer changes meaning between 2023 and 2024 (measured label-free by the reviewer: offense labels agree for 0.622 of the 336
players clustered in both seasons, labels 4 and 5 swapped), while arm M trains on 2023 rows for 2024. The reference season is the SELECT season
(2023, whose labels come from the 2022 clustering); the REPORT season's labels are mapped onto it. Defender labels are mapped the same way for the
agreement report only; the defender-label permutation is a relabelling the six features are invariant to (tested), so it cannot change a score.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_gate as g6
from research.eval import archetype_matchup_rule as t506
from research.eval import matchup_availability_features as t505

POS_COLS = ["is_guard", "is_forward", "is_center"]


def centroid_table(frame: pd.DataFrame, labels: pd.Series, cols: list[str]) -> pd.DataFrame:
    """Mean of the raw clustering columns (+ position fractions) by label. ``labels`` is indexed by player_id (as ``assign_archetypes``)."""
    use = cols + POS_COLS
    x = frame.set_index("player_id").loc[labels.index, use]
    return x.groupby(labels.to_numpy()).mean().sort_index()


def hungarian_map(ref: pd.DataFrame, cur: pd.DataFrame, scale: np.ndarray) -> dict[int, int]:
    """Map each current label to a reference label (a bijection) minimising the total scaled Euclidean distance between centroids."""
    a = cur.to_numpy(float) / scale
    b = ref.to_numpy(float) / scale
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    r, c = linear_sum_assignment(cost)
    ref_lab = ref.index.to_numpy()
    cur_lab = cur.index.to_numpy()
    return {int(cur_lab[i]): int(ref_lab[j]) for i, j in zip(r, c, strict=True)}


def apply_map(labels: pd.Series, mapping: dict[int, int]) -> pd.Series:
    return pd.Series(
        [mapping[int(v)] for v in labels.to_numpy()], index=labels.index, name=labels.name
    )


def agreement(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    """Share of players clustered in both seasons whose label is the same, and how many players that is."""
    both = a.index.intersection(b.index)
    if len(both) == 0:
        return float("nan"), 0
    return float((a.loc[both].to_numpy() == b.loc[both].to_numpy()).mean()), int(len(both))


def align_pair(
    ref_frame: pd.DataFrame,
    ref_labels: pd.Series,
    cur_frame: pd.DataFrame,
    cur_labels: pd.Series,
    cols: list[str],
) -> tuple[pd.Series, dict[str, Any]]:
    """Align ``cur_labels`` to ``ref_labels`` (both from ``assign_archetypes`` on their own frames). Returns the aligned labels and a report
    (agreement before and after, over players present in both, the mapping used)."""
    use = cols + POS_COLS
    scale = ref_frame[use].to_numpy(float).std(axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    mapping = hungarian_map(
        centroid_table(ref_frame, ref_labels, cols),
        centroid_table(cur_frame, cur_labels, cols),
        scale,
    )
    aligned = apply_map(cur_labels, mapping)
    before, n = agreement(ref_labels, cur_labels)
    after, _ = agreement(ref_labels, aligned)
    return aligned, {
        "agreement_before": before,
        "agreement_after": after,
        "n_players_in_both": n,
        "mapping": {str(k): v for k, v in mapping.items()},
    }


def build_aligned_archetypes(c: Any, season: int) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    """(offense, defense) archetypes of ``season`` (clustered on season - 1 stats, as T506), labels aligned to the SELECT season's. The SELECT
    season is the reference and is returned as built. Report: agreement before/after and the mapping, for both sides."""
    t506.check_season(season)
    stats = t505.read_player_season_stats(c, season - 1)
    static = t505.read_static(c)
    frame = t505.archetype_features(stats, static)
    off = t505.assign_archetypes(frame, t505.OFF_FEATURES, t506.K_OFF)
    dfn = t505.assign_archetypes(frame, t505.DEF_FEATURES, t506.K_DEF)
    if season == rule.SELECT:
        return off, dfn, {"reference": True, "season": season, "n_players_clustered": int(len(off))}
    ref_frame = t505.archetype_features(t505.read_player_season_stats(c, rule.SELECT - 1), static)
    ref_off = t505.assign_archetypes(ref_frame, t505.OFF_FEATURES, t506.K_OFF)
    ref_def = t505.assign_archetypes(ref_frame, t505.DEF_FEATURES, t506.K_DEF)
    off_a, rep_off = align_pair(ref_frame, ref_off, frame, off, t505.OFF_FEATURES)
    def_a, rep_def = align_pair(ref_frame, ref_def, frame, dfn, t505.DEF_FEATURES)
    return (
        off_a,
        def_a,
        {
            "reference": False,
            "season": season,
            "reference_season": rule.SELECT,
            "offense": rep_off,
            "defense": rep_def,
            "n_players_clustered": int(len(off)),
        },
    )


def identity_feature(tables: dict[str, Any], off_arch: np.ndarray) -> np.ndarray:
    """``e_lg[a] = sum_b A[a, b] R[a, b] - lg`` (S2): the scorer's expected allowed points per partial possession under the LEAGUE-AVERAGE
    assignment of his archetype (no opponent, no roster, no report). A function of ``off_arch`` and the prior-season tables only."""
    per_arch = (np.asarray(tables["A"]) * np.asarray(tables["R"])).sum(axis=1) - float(tables["lg"])
    return np.asarray(per_arch[np.asarray(off_arch, dtype=int)])


def season_frame(
    S: int,
    keys: pd.DataFrame,
    pts_rows: pd.DataFrame,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    off_a: pd.Series,
    def_a: pd.Series,
    shuffle_seed: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The six features (+ ``e_lg``, audit columns, bucket) for the four-stat intersection ``keys`` (order of ``keys`` kept), with the given
    ALREADY-ALIGNED archetypes. Tables are the prior season's. ``shuffle_seed`` permutes defender labels within each team-game (the control)."""
    t506.check_season(S)
    rows = keys.merge(pts_rows, on=["game_id", "player_id"], how="left", validate="one_to_one")
    assert len(rows) == len(keys) and rows.team_id.notna().all()
    tables = feat.prior_tables(mu, off_a, def_a, S - 1)
    mf = feat.mix_frame(
        rows, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a, shuffle_seed=shuffle_seed
    )
    sf = pd.concat([rows.reset_index(drop=True), mf], axis=1)
    sf["e_lg"] = identity_feature(tables, sf.off_arch.to_numpy())
    sf["bucket"] = [t506.minutes_bucket(m) for m in sf.min10.fillna(0.0)]
    return sf, tables


def neutral_indicators(sf: pd.DataFrame) -> dict[str, pd.Series]:
    """The T506 neutral-or-missing definitions (archetype_matchup_gate.missingness), column by column."""
    return {
        "off_arch": sf.off_arch == t506.UNK_OFF,
        "e_usual": sf.no_roster,
        "delta_rate": sf.delta_rate == 0,
        "shift_l1": sf.shift_l1 == 0,
        "retained": sf.retained == 1.0,
        "def_unknown_share": sf.def_unknown_share >= 0.5,
    }


def missingness_vs_realised(sf: pd.DataFrame, minutes: np.ndarray) -> dict[str, dict[str, float]]:
    """M4 audit input: per feature, ``gap_realised`` = |neutral share(realised minutes < 5) - neutral share(realised minutes >= 28)| and
    ``gap_pre`` = the same gap between pre-tip min10 < 10 and >= 28 (T506's). ``minutes`` is realised minutes (an argument; this module
    never reads an outcome)."""
    mins = np.asarray(minutes, float)
    lo, hi = mins < 5, mins >= 28
    b_lo, b_hi = (sf.bucket == "lt10").to_numpy(), (sf.bucket == "ge28").to_numpy()
    out: dict[str, dict[str, float]] = {}
    for name, m in neutral_indicators(sf).items():
        v = m.to_numpy(bool)

        def share(sel: np.ndarray, v: np.ndarray = v) -> float:
            return float(v[sel].mean()) if sel.any() else float("nan")

        out[name] = {
            "gap_realised": abs(share(lo) - share(hi)),
            "gap_pre": abs(share(b_lo) - share(b_hi)),
            "n_lt5": int(lo.sum()),
            "n_ge28": int(hi.sum()),
        }
    return out


def feature_nan_cells(sf: pd.DataFrame) -> int:
    return int(sf[list(g6.AUDIT_COLS)].isna().sum().sum())
