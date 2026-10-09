"""As-of player tracking / hustle features for the props context-residual screen.

Pre-registered definitions: docs/TRACKING_SCREEN.md (frozen before any tracking
value was read). Hypothesis: strictly-prior tracking and hustle rates carry
information about next-game pts/reb/ast/fg3m beyond the production feature set.

As-of discipline (same contract as ``nba.features.player_possession_features``
and the ``player_game_tracking`` / ``player_game_hustle`` schema notes): the
tracking and hustle tables are POST-GAME data, so for the row of player p in
game g only p's PLAYED games strictly before g (ordered by
``(game_date, game_id)``) contribute. A game's own tracking row never enters
its own feature row; the position-group prior uses only games on calendar
dates strictly before g's date. See ``tests/features/test_tracking_features.py``
for the planted-future-row, planted-same-game, DNP and missingness proofs.

Definitions (fixed, nothing tuned):

* recency weight of an earlier played game = ``0.5 ** (a / 10)``, ``a`` the
  number of played games from that game up to g (previous played game: a = 1);
  a played game with no tracking row has weight 0 but still ages the others.
* count columns -> minutes-weighted per-36 rate ``36 * sum(w x) / sum(w m)``;
  ``speed`` -> minutes-weighted mean ``sum(w x m) / sum(w m)``.
* shrunk toward the position-group (G/F/C from ``players_static``) as-of mean
  with a pseudo-count of 5 games: ``(W r + k mu) / (W + k)``, ``W`` = sum of
  weights (``W = 0`` returns ``mu`` exactly). ``mu`` is NaN only before any
  prior tracked data exist.
* ``trk_neff`` / ``hus_neff`` = ``log1p(W)`` for the source (never NULL).

Pure polars/numpy (the sequential decay recursion is clearer than SQL here);
the only DuckDB access is the thin :func:`load_tracking_frames` loader, which
opens the database read-only and refuses season > 2024.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

HALFLIFE = 10.0
PSEUDO_GAMES = 5.0
PER_MINUTES = 36.0
MAX_SEASON = 2024  # season 2025 is the frozen holdout: never loaded.

TRACKING_RAW: tuple[str, ...] = (
    "passes",
    "touches",
    "secondary_assists",
    "free_throw_assists",
    "rebound_chances_offensive",
    "rebound_chances_defensive",
    "contested_field_goals_attempted",
    "uncontested_field_goals_attempted",
    "defended_at_rim_field_goals_attempted",
    "distance",
    "speed",
)
HUSTLE_RAW: tuple[str, ...] = (
    "offensive_box_outs",
    "defensive_box_outs",
    "deflections",
    "screen_assists",
    "loose_balls_recovered_total",
)
MEAN_TYPE: frozenset[str] = frozenset({"speed"})


@dataclass(frozen=True)
class Family:
    name: str
    tracking_cols: tuple[str, ...]
    hustle_cols: tuple[str, ...]
    targets: tuple[str, ...]
    derived_share: bool = False

    @property
    def needs_hustle(self) -> bool:
        return bool(self.hustle_cols)

    @property
    def feature_names(self) -> tuple[str, ...]:
        names = [f"trk_{c}" for c in self.tracking_cols]
        if self.derived_share:
            names.append("trk_contested_share")
        names += [f"hus_{c}" for c in self.hustle_cols]
        if self.tracking_cols:
            names.append("trk_neff")
        if self.hustle_cols:
            names.append("hus_neff")
        return tuple(names)


FAMILIES: dict[str, Family] = {
    "passing": Family(
        "passing",
        ("passes", "touches", "secondary_assists", "free_throw_assists"),
        (),
        ("ast", "pts"),
    ),
    "rebounding": Family(
        "rebounding",
        ("rebound_chances_offensive", "rebound_chances_defensive"),
        ("offensive_box_outs", "defensive_box_outs"),
        ("reb",),
    ),
    "shooting": Family(
        "shooting",
        (
            "contested_field_goals_attempted",
            "uncontested_field_goals_attempted",
            "defended_at_rim_field_goals_attempted",
        ),
        (),
        ("pts", "fg3m"),
        derived_share=True,
    ),
    "activity": Family(
        "activity",
        ("distance", "speed"),
        ("deflections", "screen_assists", "loose_balls_recovered_total"),
        ("pts", "reb", "ast", "fg3m"),
    ),
}
# pre-declared fallback when hustle is not loaded: tracking-only subsets
FAMILIES_T_ONLY: dict[str, Family] = {
    "rebounding_T": Family("rebounding_T", FAMILIES["rebounding"].tracking_cols, (), ("reb",)),
    "activity_T": Family(
        "activity_T", FAMILIES["activity"].tracking_cols, (), ("pts", "reb", "ast", "fg3m")
    ),
}
ALL_FAMILIES: dict[str, Family] = {**FAMILIES, **FAMILIES_T_ONLY}


def family_cells(names: tuple[str, ...]) -> list[tuple[str, str]]:
    """(family, target stat) cells in declaration order."""
    return [(n, s) for n in names for s in ALL_FAMILIES[n].targets]


def pos_code_map(static: pl.DataFrame) -> pl.DataFrame:
    """player_id -> pos_code (0 G / 1 F / 2 C / -1 unknown), as the production path."""
    if "position" not in static.columns:
        return pl.DataFrame(
            {"player_id": [], "pos_code": []}, schema={"player_id": pl.Int64, "pos_code": pl.Int64}
        )
    code = (
        pl.col("position")
        .cast(pl.Utf8)
        .str.slice(0, 1)
        .replace_strict({"G": 0, "F": 1, "C": 2}, default=-1)
        .cast(pl.Int64)
    )
    return static.select(pl.col("player_id").cast(pl.Int64), code.alias("pos_code")).unique(
        "player_id", keep="first"
    )


def _source_matrix(
    played: pl.DataFrame, src: pl.DataFrame | None, cols: tuple[str, ...], what: str
) -> np.ndarray:
    """(n_rows, len(cols)) values aligned to ``played`` rows; NaN = no observation."""
    if not cols:
        return np.empty((played.height, 0))
    if src is None:
        raise ValueError(f"{what} data required by the requested families but not provided")
    miss = [c for c in cols if c not in src.columns]
    if miss:
        raise ValueError(f"{what} frame lacks columns {miss}")
    s = src.select("game_id", "player_id", *[pl.col(c).cast(pl.Float64) for c in cols]).unique(
        ["game_id", "player_id"], keep="first"
    )
    j = played.select("game_id", "player_id").join(s, on=["game_id", "player_id"], how="left")
    return j.select(cols).to_numpy().astype(float)


def _asof_rates(
    pid: np.ndarray,
    x: np.ndarray,
    mins: np.ndarray,
    mean_mask: np.ndarray,
    lam: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-row prior-only decayed (numerator, minutes-denominator, weight) sums.

    Rows must be sorted by (player, date, game). The feature for row i uses
    state accumulated from rows < i of the same player only (state is read
    BEFORE row i is folded in)."""
    n, c = x.shape
    num = np.zeros((n, c))
    den = np.zeros((n, c))
    wsum = np.zeros((n, c))
    if n == 0:
        return num, den, wsum
    s_num = np.zeros(c)
    s_den = np.zeros(c)
    s_w = np.zeros(c)
    prev = pid[0]
    obs = ~np.isnan(x)
    xv = np.where(obs, x, 0.0)
    xm = np.where(mean_mask[None, :], xv * mins[:, None], xv)
    mo = np.where(obs, mins[:, None], 0.0)
    for i in range(n):
        if pid[i] != prev:
            s_num[:] = 0.0
            s_den[:] = 0.0
            s_w[:] = 0.0
            prev = pid[i]
        num[i], den[i], wsum[i] = s_num, s_den, s_w
        s_num = lam * (s_num + xm[i])
        s_den = lam * (s_den + mo[i])
        s_w = lam * (s_w + obs[i])
    return num, den, wsum


def _position_prior(
    pos: np.ndarray,
    dates: np.ndarray,
    x: np.ndarray,
    mins: np.ndarray,
    mean_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """As-of position-group (numerator, denominator) over rows on calendar dates
    STRICTLY before each row's date; falls back to all positions when the group
    has no prior data. Returns arrays (n, c); denominator 0 => no prior."""
    n, c = x.shape
    obs = ~np.isnan(x)
    xv = np.where(obs, x, 0.0)
    xm = np.where(mean_mask[None, :], xv * mins[:, None], xv)
    mo = np.where(obs, mins[:, None], 0.0)
    d_int = dates.astype("datetime64[D]").astype(np.int64)
    pnum = np.zeros((n, c))
    pden = np.zeros((n, c))

    def _cum(sel: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ud, inv = np.unique(d_int[sel], return_inverse=True)
        gn = np.zeros((len(ud), c))
        gd = np.zeros((len(ud), c))
        np.add.at(gn, inv, xm[sel])
        np.add.at(gd, inv, mo[sel])
        zero = np.zeros((1, c))
        return (
            ud,
            np.vstack([zero, np.cumsum(gn, axis=0)]),
            np.vstack([zero, np.cumsum(gd, axis=0)]),
        )

    ud_all, cn_all, cd_all = _cum(np.ones(n, dtype=bool))
    idx_all = np.searchsorted(ud_all, d_int, side="left")  # dates strictly before
    gnum, gden = cn_all[idx_all], cd_all[idx_all]
    for p in np.unique(pos):
        sel = pos == p
        ud, cn, cd = _cum(sel)
        idx = np.searchsorted(ud, d_int[sel], side="left")
        pnum[sel], pden[sel] = cn[idx], cd[idx]
    use_g = pden <= 0
    return np.where(use_g, gnum, pnum), np.where(use_g, gden, pden)


def build_tracking_features(
    played: pl.DataFrame,
    tracking: pl.DataFrame | None,
    hustle: pl.DataFrame | None,
    static: pl.DataFrame,
    families: tuple[str, ...],
    halflife: float = HALFLIFE,
    pseudo_games: float = PSEUDO_GAMES,
) -> pl.DataFrame:
    """As-of tracking/hustle feature rows keyed by (game_id, player_id).

    ``played``: columns ``game_id, player_id, game_date, minutes`` (rows with
    ``minutes <= 0`` or NULL minutes are dropped: DNPs are never observations
    and never get feature rows). ``tracking`` / ``hustle``: post-game frames
    with ``game_id, player_id`` and the raw columns; a (game, player) with no
    row, or a NULL value, is an unobserved game for that column."""
    fams = [ALL_FAMILIES[f] for f in families]
    t_cols = tuple(dict.fromkeys(c for f in fams for c in f.tracking_cols))
    h_cols = tuple(dict.fromkeys(c for f in fams for c in f.hustle_cols))
    base = (
        played.select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            pl.col("game_date").cast(pl.Date),
            pl.col("minutes").cast(pl.Float64),
        )
        .filter(pl.col("minutes") > 0)
        .join(pos_code_map(static), on="player_id", how="left")
        .with_columns(pl.col("pos_code").fill_null(-1))
        .sort(["player_id", "game_date", "game_id"])
    )
    cols = t_cols + h_cols
    x = np.hstack(
        [
            _source_matrix(base, tracking, t_cols, "tracking"),
            _source_matrix(base, hustle, h_cols, "hustle"),
        ]
    )
    mean_mask = np.array([c in MEAN_TYPE for c in cols], dtype=bool)
    scale = np.where(mean_mask, 1.0, PER_MINUTES)
    mins = base["minutes"].to_numpy()
    lam = 0.5 ** (1.0 / halflife)
    num, den, w = _asof_rates(base["player_id"].to_numpy(), x, mins, mean_mask, lam)
    pnum, pden = _position_prior(
        base["pos_code"].to_numpy(), base["game_date"].to_numpy(), x, mins, mean_mask
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(den > 0, scale * num / den, np.nan)
        mu = np.where(pden > 0, scale * pnum / pden, np.nan)
        shrunk = np.where(
            w > 0,
            (w * np.nan_to_num(r) + pseudo_games * mu) / (w + pseudo_games),
            mu,
        )
    data: dict[str, np.ndarray] = {}
    nt = len(t_cols)
    for i, c in enumerate(cols):
        data[("trk_" if i < nt else "hus_") + c] = shrunk[:, i]
    if t_cols:
        data["trk_neff"] = np.log1p(w[:, 0])
    if h_cols:
        data["hus_neff"] = np.log1p(w[:, nt])
    if any(f.derived_share for f in fams):
        cc = data["trk_contested_field_goals_attempted"]
        uu = data["trk_uncontested_field_goals_attempted"]
        with np.errstate(divide="ignore", invalid="ignore"):
            data["trk_contested_share"] = np.where(cc + uu > 0, cc / (cc + uu), np.nan)
    keep: list[str] = []
    for f in fams:
        keep += [n for n in f.feature_names if n not in keep]
    return base.select("game_id", "player_id").with_columns([pl.Series(k, data[k]) for k in keep])


def own_game_coverage(played: pl.DataFrame, src: pl.DataFrame | None) -> pl.Series:
    """DIAGNOSTIC ONLY (never a feature): does (game, player) have a post-game row?"""
    if src is None:
        return pl.Series("has_row", [False] * played.height)
    keys = src.select("game_id", "player_id").unique()
    j = played.select("game_id", "player_id").join(
        keys.with_columns(pl.lit(True).alias("has_row")), on=["game_id", "player_id"], how="left"
    )
    return j["has_row"].fill_null(False)


def load_tracking_frames(
    db_path: str | Path = "nba.duckdb", with_hustle: bool = True
) -> tuple[pl.DataFrame, pl.DataFrame | None]:
    """(tracking, hustle) for seasons <= 2024 from a READ-ONLY connection."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:

        def q(table: str, cols: tuple[str, ...]) -> pl.DataFrame:
            sel = ", ".join(f"t.{c}" for c in cols)
            return con.execute(
                f"SELECT t.game_id, t.player_id, {sel} FROM {table} t "
                f"JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
            ).pl()

        trk = q("player_game_tracking", TRACKING_RAW)
        hus = q("player_game_hustle", HUSTLE_RAW) if with_hustle else None
    finally:
        con.close()
    return trk, hus
