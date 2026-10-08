"""Cold-start method 2: archetype priors via clustering.

Cluster players on height, weight, position, and prior-season tendencies;
a new/cold player's prior is their cluster's mean rate. Cluster count is
picked by backtest (:func:`select_n_clusters_by_backtest`), not by eye, per
CLAUDE.md -- though on data too small to compare candidates meaningfully
this degrades to the documented default
(``nba.coldstart.config.DEFAULT_N_CLUSTERS``) with a note.

## As-of DB-driven archetype builder (:func:`build_player_archetypes`)

The functions above (``fit_archetypes``/``predict_archetype_prior``) take
plain feature arrays and are used by the rookie-prior / shrinkage machinery.
:func:`build_player_archetypes` is the higher-level, DuckDB-driven entry
point this module adds: given an ``as_of_date``, it assembles one feature
row per player from

- ``players_static``: position (categorical), height_in, weight_lb, and age
  computed from ``birth_date`` as of ``as_of_date`` (never the player's
  *current* age -- this is itself an as-of feature);
- as-of playing tendencies: each player's **latest row with
  ``game_date < as_of_date``** from
  ``nba.features.player_possession_features.build_player_shot_rates``
  (shot_share, zone_mix_above3) and
  ``nba.features.player_rebound_assist_features.build_player_reb_ast_rates``
  (orb_rate, drb_rate, ast_rate). Every column consumed from those two
  modules is already a cumulative window aggregate over strictly earlier
  games than its own row (see their module docstrings), so taking the
  latest such row before ``as_of_date`` is itself leakage-free: no feature
  used here is ever computed from a game on or after ``as_of_date``.

A player with zero games before ``as_of_date`` (a true rookie / not yet
debuted) gets the hardcoded league-wide default for every tendency column
(the same documented constants ``player_possession_features`` /
``player_rebound_assist_features`` already use as their own cold-start
fallback) rather than being dropped -- this lets a brand-new player still
be assigned an archetype from demographics alone (CLAUDE.md cold-start
method 2 exists precisely to give such a player *some* prior). A player
missing ``players_static`` physical attributes entirely (no height/weight
row) is excluded; there is no demographic signal to cluster on.

### Mixed categorical + numeric features, without ``kmodes``

``kmodes`` (true k-prototypes) is not an installed dependency and CLAUDE.md
says not to add it. Position is one-hot encoded into three fractional
membership columns (``is_guard``/``is_forward``/``is_center``, hybrids like
"Guard-Forward" splitting 0.5/0.5) via :func:`encode_position_fractions`,
concatenated with the standardized numeric columns, and clustered with
plain ``KMeans`` -- the documented "one-hot the categoricals + standardize
numerics + KMeans" alternative CLAUDE.md explicitly allows in place of a
Gower-distance implementation.

### k selection

``k`` is chosen by :func:`select_k_by_silhouette` (mean silhouette score
over candidate k's, scored on the same standardized feature matrix used to
fit), not picked by eye. Degrades to the documented default
(``nba.coldstart.config.DEFAULT_N_CLUSTERS``) with a note when there are
too few players to compare candidates meaningfully (silhouette requires
at least 2 clusters and more points than clusters) -- the committed fixture
(2-3 players per game, no ``players_static`` rows at all) is exactly this
case, so a maintainer run on the real DB is required to see a real k
selected; see this module's unit tests for the degrade-gracefully proof and
the agent's status/report for the exact command.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from nba.coldstart.config import DEFAULT_N_CLUSTERS
from nba.features.player_possession_features import (
    LEAGUE_SHOT_SHARE_DEFAULT,
    LEAGUE_ZONE_MIX_DEFAULT,
    build_player_shot_rates,
)
from nba.features.player_rebound_assist_features import (
    LEAGUE_AST_RATE_DEFAULT,
    LEAGUE_DRB_RATE_DEFAULT,
    LEAGUE_ORB_RATE_DEFAULT,
    build_player_reb_ast_rates,
)

#: Fallback age (years) when ``players_static.birth_date`` is null -- a
#: league-average-ish veteran age, reusing the same documented constant
#: ``nba.coldstart.config.DEFAULT_PEAK_AGE`` already uses for the carryover
#: age curve's peak, rather than inventing a new number.
_DEFAULT_AGE = 27.0

#: Numeric feature columns fed into the scaler/KMeans, in a fixed order
#: (documents exactly what "archetype" is clustering on).
_NUMERIC_FEATURE_COLUMNS = [
    "height_in",
    "weight_lb",
    "age",
    "is_guard",
    "is_forward",
    "is_center",
    "shot_share",
    "zone_mix_above3",
    "orb_rate",
    "drb_rate",
    "ast_rate",
]


def encode_position_fractions(position: str | None) -> tuple[float, float, float]:
    """Fractional (guard, forward, center) membership for a position string.

    Handles both the abbreviated forms this module already used
    (``"G"``, ``"F"``, ``"C"``, ``"G-F"``, ...) and the full ``nba_api``
    ``commonplayerinfo.POSITION`` forms (``"Guard"``, ``"Forward"``,
    ``"Center"``, ``"Guard-Forward"``, ...), case-insensitively, by token:
    split on ``-``/``/``, match each token's first letter, average the
    matched tokens. Unknown/empty/null position: neutral 1/3-1/3-1/3 (no
    information either way -- not a silent assumption of one position).
    """
    if not position:
        return (1 / 3, 1 / 3, 1 / 3)
    tokens = [t.strip().lower() for t in position.replace("/", "-").split("-") if t.strip()]
    if not tokens:
        return (1 / 3, 1 / 3, 1 / 3)
    g = f = c = 0.0
    matched = 0
    for tok in tokens:
        first = tok[0]
        if first == "g":
            g += 1.0
            matched += 1
        elif first == "f":
            f += 1.0
            matched += 1
        elif first == "c":
            c += 1.0
            matched += 1
    if matched == 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return (g / matched, f / matched, c / matched)


#: Coarse numeric encoding for position strings, used as one of the
#: clustering features. Combo positions average their components.
POSITION_ENCODING: dict[str, float] = {
    "G": 0.0,
    "G-F": 0.5,
    "F": 1.0,
    "F-G": 0.5,
    "F-C": 1.5,
    "C-F": 1.5,
    "C": 2.0,
}


def encode_position(position: str | None) -> float:
    if position is None:
        return 1.0  # unknown -> neutral (forward-ish) midpoint
    return POSITION_ENCODING.get(position, 1.0)


@dataclass
class ArchetypeModel:
    n_clusters: int
    scaler: StandardScaler
    kmeans: KMeans
    cluster_target_means: np.ndarray  # shape (n_clusters, n_targets)
    seed: int


def fit_archetypes(
    features: np.ndarray,
    targets: np.ndarray,
    n_clusters: int,
    seed: int = 0,
) -> ArchetypeModel:
    """Fit k-means on ``features`` (e.g. height, weight, position, prior
    tendencies) and compute each cluster's mean of ``targets`` (the rate
    stat(s) to use as the archetype prior). Deterministic given ``seed``.
    """
    if features.ndim != 2:
        raise ValueError("features must be a 2-D array (n_players, n_features)")
    if targets.ndim == 1:
        targets = targets.reshape(-1, 1)
    n_clusters = max(1, min(n_clusters, features.shape[0]))

    scaler = StandardScaler()
    X = scaler.fit_transform(features)
    kmeans = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
    labels = kmeans.fit_predict(X)

    cluster_means = np.zeros((n_clusters, targets.shape[1]))
    overall_mean = targets.mean(axis=0)
    for c in range(n_clusters):
        mask = labels == c
        cluster_means[c] = targets[mask].mean(axis=0) if mask.any() else overall_mean

    return ArchetypeModel(
        n_clusters=n_clusters,
        scaler=scaler,
        kmeans=kmeans,
        cluster_target_means=cluster_means,
        seed=seed,
    )


def predict_archetype_prior(model: ArchetypeModel, features: np.ndarray) -> np.ndarray:
    """Prior for each row of ``features`` = its assigned cluster's mean target."""
    X = model.scaler.transform(features)
    labels = model.kmeans.predict(X)
    return np.asarray(model.cluster_target_means[labels], dtype=float)


def select_n_clusters_by_backtest(
    features: np.ndarray,
    targets: np.ndarray,
    candidate_ks: list[int] | None = None,
    seed: int = 0,
    min_rows_per_candidate: int = 10,
    default_k: int = 4,
) -> tuple[int, str]:
    """Pick the cluster count minimizing held-out squared error of the
    archetype prior against ``targets``, via a simple random split
    (repeated with the given seed, so deterministic) -- not "by eye" per
    CLAUDE.md. Degrades to ``default_k`` with a note when there are too few
    rows to hold any out meaningfully.
    """
    candidate_ks = candidate_ks or [2, 3, 4, 6, 8]
    n_rows = features.shape[0]
    if n_rows < min_rows_per_candidate:
        return default_k, (
            f"only {n_rows} players available (<{min_rows_per_candidate}); insufficient data to "
            "select n_clusters by backtest, using documented default"
        )

    rng = np.random.default_rng(seed)
    idx = rng.permutation(n_rows)
    split = max(1, int(n_rows * 0.7))
    train_idx, test_idx = idx[:split], idx[split:]
    if len(test_idx) == 0:
        return default_k, "holdout split was empty; using documented default"

    best_k = default_k
    best_mse = float("inf")
    for k in candidate_ks:
        if k > len(train_idx):
            continue
        model = fit_archetypes(features[train_idx], targets[train_idx], n_clusters=k, seed=seed)
        preds = predict_archetype_prior(model, features[test_idx])
        mse = float(np.mean((preds.reshape(preds.shape[0], -1) - targets[test_idx]) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_k = k
    return best_k, ""


def select_k_by_silhouette(
    X: np.ndarray,
    candidate_ks: list[int] | None = None,
    seed: int = 0,
    default_k: int = DEFAULT_N_CLUSTERS,
) -> tuple[int, float, str]:
    """Pick cluster count by mean silhouette score on standardized features
    ``X`` (already-scaled; caller's responsibility) -- a quantitative
    criterion, not "by eye" per CLAUDE.md.

    Degrades to ``default_k`` (with a note, silhouette score NaN) when
    there are too few rows to compare any candidate meaningfully
    (silhouette needs ``2 <= n_clusters <= n_samples - 1``).
    """
    candidate_ks = candidate_ks or [2, 3, 4, 6, 8]
    n_rows = X.shape[0]
    usable_ks = [k for k in candidate_ks if 2 <= k <= n_rows - 1]
    if not usable_ks:
        return (
            default_k,
            float("nan"),
            f"only {n_rows} players available; insufficient data to compare any "
            "candidate k by silhouette, using documented default",
        )
    best_k = usable_ks[0]
    best_score = -1.0
    for k in usable_ks:
        labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(X)
        if len(set(labels.tolist())) < 2:
            continue
        score = float(silhouette_score(X, labels))
        if score > best_score:
            best_score = score
            best_k = k
    return best_k, best_score, ""


@dataclass
class ArchetypeAssignmentResult:
    """One clustering snapshot, as of a single date."""

    assignments: pl.DataFrame  # columns: player_id, as_of_date, archetype
    k: int
    k_selection_method: str  # "silhouette" | "default (insufficient data)"
    silhouette_score: float
    note: str
    seed: int
    scaler: StandardScaler
    kmeans: KMeans
    feature_columns: list[str]


def _players_static_raw(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Physical/demographic attributes, date-independent part only (no
    age -- that is computed per ``as_of_date`` by the caller). Position
    fractions are precomputed here too since they never depend on
    ``as_of_date`` either. Split out of the old ``_players_static_asof``
    so it can be queried ONCE and reused across many ``as_of_date``
    snapshots instead of re-querying ``players_static`` per date (see
    :class:`PlayerFeatureSource` / perf history)."""
    con.execute(
        """
        SELECT player_id, position, height_in, weight_lb, birth_date
        FROM players_static
        WHERE height_in IS NOT NULL AND weight_lb IS NOT NULL
        """
    )
    rows = con.fetchall()
    df = pl.DataFrame(
        rows,
        schema={
            "player_id": pl.Int64,
            "position": pl.Utf8,
            "height_in": pl.Float64,
            "weight_lb": pl.Float64,
            "birth_date": pl.Date,
        },
        orient="row",
    )
    if df.height == 0:
        return df
    fractions = [encode_position_fractions(p) for p in df.get_column("position").to_list()]
    return df.with_columns(
        pl.Series("is_guard", [f[0] for f in fractions]),
        pl.Series("is_forward", [f[1] for f in fractions]),
        pl.Series("is_center", [f[2] for f in fractions]),
    )


def _players_static_asof(con: duckdb.DuckDBPyConnection, as_of_date: str) -> pl.DataFrame:
    """Physical/demographic features as of ``as_of_date``. Position/
    height/weight are static attributes (no leakage risk); age is
    deliberately computed relative to ``as_of_date``, never "today"."""
    return _apply_asof_age(_players_static_raw(con), as_of_date)


def _apply_asof_age(df: pl.DataFrame, as_of_date: str) -> pl.DataFrame:
    """Add the ``age`` column to the date-independent static frame
    (``_players_static_raw``'s output) for one ``as_of_date``. Pulled out
    of ``_players_static_asof`` so a cached/precomputed static frame can
    be reused across many dates without re-querying DuckDB each time."""
    if df.height == 0:
        return df
    as_of = pl.lit(as_of_date).str.to_date()
    return df.with_columns(
        pl.when(pl.col("birth_date").is_not_null())
        .then((as_of - pl.col("birth_date")).dt.total_days() / 365.25)
        .otherwise(pl.lit(_DEFAULT_AGE))
        .alias("age")
    )


def _latest_tendencies_as_of(
    shot: pl.DataFrame, reb_ast: pl.DataFrame, as_of_date: str
) -> pl.DataFrame:
    """Each player's latest as-of tendency row with ``game_date <
    as_of_date``, given ALREADY-FETCHED full-history ``shot``/``reb_ast``
    frames (see module docstring for why this filter is leakage-free).
    Pulled out of the old ``_latest_prior_tendencies`` so the two
    expensive full-history SQL builds (``build_player_shot_rates`` /
    ``build_player_reb_ast_rates``) can be run ONCE and reused across many
    ``as_of_date`` snapshots -- see :class:`PlayerFeatureSource`."""

    def _latest(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        empty_schema = {"player_id": pl.Int64, **{c: pl.Float64 for c in cols}}
        if df.height == 0:
            return pl.DataFrame(schema=empty_schema)
        sub = df.filter(pl.col("game_date") < pl.lit(as_of_date).str.to_date())
        if sub.height == 0:
            return pl.DataFrame(schema=empty_schema)
        sub = sub.sort(["player_id", "game_date", "game_id"])
        return sub.group_by("player_id", maintain_order=True).agg([pl.col(c).last() for c in cols])

    shot_latest = _latest(shot, ["shot_share_prior", "zone_mix_above3_prior"])
    reb_ast_latest = _latest(reb_ast, ["orb_rate_prior", "drb_rate_prior", "ast_rate_prior"])
    return shot_latest.join(reb_ast_latest, on="player_id", how="full", coalesce=True)


def _latest_prior_tendencies(con: duckdb.DuckDBPyConnection, as_of_date: str) -> pl.DataFrame:
    """Each player's latest as-of tendency row with ``game_date <
    as_of_date`` -- see module docstring for why this is leakage-free.
    Single-date convenience wrapper; see :class:`PlayerFeatureSource` for
    the batch/cached path used when scoring many dates."""
    shot = build_player_shot_rates(con)
    reb_ast = build_player_reb_ast_rates(con)
    return _latest_tendencies_as_of(shot, reb_ast, as_of_date)


@dataclass
class PlayerFeatureSource:
    """Cached, date-independent inputs for :func:`build_player_feature_frame`
    (``static`` attributes/position fractions, full-history shot rates,
    full-history reb/ast rates) -- each is the output of one SQL query
    over the WHOLE DB, so building this once and reusing it across many
    ``as_of_date`` snapshots turns an O(n_dates) set of full-history
    re-scans into O(1). See :func:`build_player_feature_source` /
    :func:`build_player_feature_frame_cached`, and the perf history doc
    for the profiled before/after this replaced (the old per-date path
    called ``build_player_shot_rates``/``build_player_reb_ast_rates``
    fresh inside a loop over every distinct ``as_of_date``)."""

    static_raw: pl.DataFrame
    shot_rates: pl.DataFrame
    reb_ast_rates: pl.DataFrame


def build_player_feature_source(con: duckdb.DuckDBPyConnection) -> PlayerFeatureSource:
    """Run the three date-independent full-history queries ONCE. Pass the
    result to :func:`build_player_feature_frame_cached` for every
    ``as_of_date`` snapshot needed -- see :class:`PlayerFeatureSource`."""
    return PlayerFeatureSource(
        static_raw=_players_static_raw(con),
        shot_rates=build_player_shot_rates(con),
        reb_ast_rates=build_player_reb_ast_rates(con),
    )


def build_player_feature_frame_cached(source: PlayerFeatureSource, as_of_date: str) -> pl.DataFrame:
    """Identical output to ``build_player_feature_frame(con, as_of_date)``,
    but scored against an already-built :class:`PlayerFeatureSource`
    instead of re-querying DuckDB -- the fast path for scoring many dates.
    See module docstring / :func:`build_player_feature_frame`."""
    static = _apply_asof_age(source.static_raw, as_of_date)
    if static.height == 0:
        return pl.DataFrame(
            schema={"player_id": pl.Int64, **{c: pl.Float64 for c in _NUMERIC_FEATURE_COLUMNS}}
        )
    tendencies = _latest_tendencies_as_of(source.shot_rates, source.reb_ast_rates, as_of_date)
    merged = static.join(tendencies, on="player_id", how="left")
    merged = merged.with_columns(
        pl.col("shot_share_prior").fill_null(LEAGUE_SHOT_SHARE_DEFAULT).alias("shot_share"),
        pl.col("zone_mix_above3_prior")
        .fill_null(LEAGUE_ZONE_MIX_DEFAULT["above3"])
        .alias("zone_mix_above3"),
        pl.col("orb_rate_prior").fill_null(LEAGUE_ORB_RATE_DEFAULT).alias("orb_rate"),
        pl.col("drb_rate_prior").fill_null(LEAGUE_DRB_RATE_DEFAULT).alias("drb_rate"),
        pl.col("ast_rate_prior").fill_null(LEAGUE_AST_RATE_DEFAULT).alias("ast_rate"),
    )
    return merged.select(["player_id", *_NUMERIC_FEATURE_COLUMNS])


def build_player_feature_frame(con: duckdb.DuckDBPyConnection, as_of_date: str) -> pl.DataFrame:
    """One row per player with a ``players_static`` row: ``player_id`` plus
    every column in :data:`_NUMERIC_FEATURE_COLUMNS`, computed strictly as
    of ``as_of_date`` (see module docstring). Pulled out of
    :func:`build_player_archetypes` so other callers (e.g.
    ``nba.props.opponent``'s archetype-vs-archetype opponent factor) can
    score a FROZEN, already-fit clustering model (:func:`assign_archetypes`)
    against a different ``as_of_date``'s feature vectors without refitting
    KMeans each time -- the frozen model gives temporally-stable cluster
    *label integers* while each row's own features stay strictly as-of its
    own date (never a later one), which is what keeps this leakage-free.
    Empty frame (``player_id`` + feature columns, zero rows) when no player
    has a ``players_static`` row as of this date.

    Single-date convenience wrapper around :func:`build_player_feature_source`
    + :func:`build_player_feature_frame_cached`; callers scoring MANY dates
    against the same DB (e.g. ``nba.props.opponent``) should build the
    source once and call :func:`build_player_feature_frame_cached` in a
    loop instead -- see that module's perf history for why.
    """
    source = build_player_feature_source(con)
    return build_player_feature_frame_cached(source, as_of_date)


def assign_archetypes(
    model: ArchetypeAssignmentResult, feature_frame: pl.DataFrame
) -> pl.DataFrame:
    """Score ``feature_frame`` (as built by :func:`build_player_feature_frame`,
    any ``as_of_date``) against the already-fit ``model.scaler``/
    ``model.kmeans`` -- i.e. *predict*, never refit. Returns
    ``(player_id, archetype)``, empty when ``feature_frame`` has zero rows
    or ``model.k == 0`` (the "no players_static rows" degenerate case).

    Using one frozen model across many ``as_of_date`` snapshots is what
    makes cluster label integers comparable over time (the per-snapshot
    refit in :func:`build_player_archetypes`/:func:`build_archetype_history`
    does NOT have this property on its own -- see that function's
    docstring on why raw labels aren't comparable across independent fits).
    """
    if feature_frame.height == 0 or model.k == 0:
        return pl.DataFrame(
            {"player_id": [], "archetype": []},
            schema={"player_id": pl.Int64, "archetype": pl.Int64},
        )
    X_raw = feature_frame.select(model.feature_columns).to_numpy()
    X = model.scaler.transform(X_raw)
    labels = model.kmeans.predict(X)
    return feature_frame.select("player_id").with_columns(
        pl.Series("archetype", labels.astype(np.int64))
    )


def build_player_archetypes(
    con: duckdb.DuckDBPyConnection,
    as_of_date: str,
    k: int | None = None,
    seed: int = 0,
    candidate_ks: list[int] | None = None,
) -> ArchetypeAssignmentResult:
    """Cluster every player with a ``players_static`` physical-attribute
    row into ``k`` archetypes, using only data strictly before
    ``as_of_date``. See module docstring for the exact feature set, the
    mixed-type encoding choice, and the k-selection criterion.

    ``k=None`` (default): picked by :func:`select_k_by_silhouette`.
    Passing an explicit ``k`` skips selection entirely (e.g. for the
    archetype-history time series, where every snapshot should use the
    same k so cluster counts are comparable across dates).
    """
    merged = build_player_feature_frame(con, as_of_date)
    if merged.height == 0:
        empty = pl.DataFrame(
            {"player_id": [], "as_of_date": [], "archetype": []},
            schema={"player_id": pl.Int64, "as_of_date": pl.Utf8, "archetype": pl.Int64},
        )
        scaler = StandardScaler()
        kmeans = KMeans(n_clusters=1, random_state=seed, n_init=1)
        return ArchetypeAssignmentResult(
            assignments=empty,
            k=0,
            k_selection_method="n/a",
            silhouette_score=float("nan"),
            note="no players_static rows with height/weight as of this date",
            seed=seed,
            scaler=scaler,
            kmeans=kmeans,
            feature_columns=_NUMERIC_FEATURE_COLUMNS,
        )

    X_raw = merged.select(_NUMERIC_FEATURE_COLUMNS).to_numpy()
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)

    note = ""
    method = "explicit"
    silhouette = float("nan")
    chosen_k = k
    if chosen_k is None:
        chosen_k, silhouette, sil_note = select_k_by_silhouette(X, candidate_ks, seed=seed)
        method = "default (insufficient data)" if sil_note else "silhouette"
        note = sil_note
    chosen_k = max(1, min(chosen_k, X.shape[0]))

    kmeans = KMeans(n_clusters=chosen_k, random_state=seed, n_init=10)
    labels = kmeans.fit_predict(X)

    assignments = merged.select("player_id").with_columns(
        pl.lit(as_of_date).alias("as_of_date"),
        pl.Series("archetype", labels.astype(np.int64)),
    )
    return ArchetypeAssignmentResult(
        assignments=assignments,
        k=chosen_k,
        k_selection_method=method,
        silhouette_score=silhouette,
        note=note,
        seed=seed,
        scaler=scaler,
        kmeans=kmeans,
        feature_columns=_NUMERIC_FEATURE_COLUMNS,
    )


def build_archetype_history(
    con: duckdb.DuckDBPyConnection,
    as_of_dates: list[str],
    k: int | None = None,
    seed: int = 0,
    candidate_ks: list[int] | None = None,
) -> pl.DataFrame:
    """Snapshot archetype assignments at each of ``as_of_dates`` (ascending)
    and stack them into one ``(player_id, as_of_date, archetype)`` time
    series -- CLAUDE.md cold-start ask: make a player's archetype/role
    history visible over time, as a precursor signal to
    ``nba.props.role_change``'s CUSUM changepoint detection.

    IMPORTANT caveat (documented, not hidden): each snapshot independently
    re-fits KMeans, so cluster label *integers* are not stable/comparable
    across snapshots on their own (cluster "0" today and cluster "0" a
    year ago are not necessarily the same archetype). Use
    :func:`flag_archetype_changes` rather than comparing the raw
    ``archetype`` integer across rows -- it re-aligns labels across
    consecutive snapshots via nearest-centroid matching before flagging.
    """
    frames = []
    for as_of_date in as_of_dates:
        result = build_player_archetypes(con, as_of_date, k=k, seed=seed, candidate_ks=candidate_ks)
        frames.append(result.assignments)
    if not frames:
        return pl.DataFrame(
            {"player_id": [], "as_of_date": [], "archetype": []},
            schema={"player_id": pl.Int64, "as_of_date": pl.Utf8, "archetype": pl.Int64},
        )
    return pl.concat(frames, how="vertical")


def _match_labels_to_previous(
    prev: ArchetypeAssignmentResult, curr: ArchetypeAssignmentResult
) -> dict[int, int]:
    """Map each ``curr`` cluster label to the ``prev`` cluster label whose
    centroid (in ``curr``'s own feature space, i.e. ``prev``'s centroids
    re-scaled by ``curr.scaler``) is nearest -- greedy nearest-centroid
    alignment so label integers are comparable across the two snapshots."""
    prev_centroids_raw = prev.scaler.inverse_transform(prev.kmeans.cluster_centers_)
    prev_centroids_in_curr_space = curr.scaler.transform(prev_centroids_raw)
    curr_centroids = curr.kmeans.cluster_centers_
    mapping: dict[int, int] = {}
    for curr_label, centroid in enumerate(curr_centroids):
        dists = np.linalg.norm(prev_centroids_in_curr_space - centroid, axis=1)
        mapping[curr_label] = int(np.argmin(dists))
    return mapping


@dataclass
class ArchetypeChangeFlag:
    player_id: int
    from_date: str
    to_date: str
    from_archetype: int
    to_archetype_aligned: int
    changed: bool


def flag_archetype_changes(
    snapshots: list[ArchetypeAssignmentResult],
) -> list[ArchetypeChangeFlag]:
    """Flag players whose archetype changed between consecutive snapshots
    in ``snapshots`` (ascending ``as_of_date`` order), after aligning each
    snapshot's cluster labels to the previous one's via nearest-centroid
    matching (see :func:`_match_labels_to_previous` -- required because
    raw label integers are not comparable across independent KMeans fits).
    A player absent from either snapshot (no row that date) is skipped,
    not flagged.
    """
    flags: list[ArchetypeChangeFlag] = []
    for prev, curr in zip(snapshots[:-1], snapshots[1:], strict=False):
        prev_assign = prev.assignments
        curr_assign = curr.assignments
        if prev_assign.height == 0 or curr_assign.height == 0:
            continue
        label_map = _match_labels_to_previous(prev, curr)
        prev_map = dict(
            zip(
                prev_assign.get_column("player_id").to_list(),
                prev_assign.get_column("archetype").to_list(),
                strict=True,
            )
        )
        prev_date = prev_assign.get_column("as_of_date")[0]
        curr_date = curr_assign.get_column("as_of_date")[0]
        for pid, curr_label in zip(
            curr_assign.get_column("player_id").to_list(),
            curr_assign.get_column("archetype").to_list(),
            strict=True,
        ):
            if pid not in prev_map:
                continue
            from_label = prev_map[pid]
            to_aligned = label_map.get(int(curr_label), int(curr_label))
            flags.append(
                ArchetypeChangeFlag(
                    player_id=int(pid),
                    from_date=str(prev_date),
                    to_date=str(curr_date),
                    from_archetype=int(from_label),
                    to_archetype_aligned=int(to_aligned),
                    changed=int(from_label) != int(to_aligned),
                )
            )
    return flags
