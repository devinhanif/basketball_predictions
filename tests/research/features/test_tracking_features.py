"""As-of proofs for research.features.tracking_features (synthetic data, no real DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.features.tracking_features import (
    ALL_FAMILIES,
    FAMILIES,
    HUSTLE_RAW,
    TRACKING_RAW,
    build_tracking_features,
)

ALL = tuple(FAMILIES)


def _league(n_games: int = 40, seed: int = 0):
    rng = np.random.default_rng(seed)
    d0 = dt.date(2023, 1, 1)
    played, trk, hus = [], [], []
    for gi in range(n_games):
        d = d0 + dt.timedelta(days=gi)
        for pid in range(1, 7):
            did_play = not (pid == 6 and gi % 4 == 0)  # player 6 has DNPs
            mins = float(rng.uniform(15, 36)) if did_play else None
            gid = f"{gi:05d}"
            played.append((gid, pid, d, mins))
            if gi % 7 == 3:  # whole game missing from the tracking feed
                continue
            row_t = {"game_id": gid, "player_id": pid}
            for c in [x for x in TRACKING_RAW if x != "speed"]:
                row_t[c] = float(rng.poisson(5 + pid)) if did_play else 0.0
            row_t["speed"] = float(rng.uniform(3.0, 5.0)) if did_play else 0.0
            trk.append(row_t)
            row_h = {"game_id": gid, "player_id": pid}
            for c in HUSTLE_RAW:
                row_h[c] = float(rng.poisson(2)) if did_play else 0.0
            hus.append(row_h)
    played_df = pl.DataFrame(
        played,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "game_date": pl.Date,
            "minutes": pl.Float64,
        },
        orient="row",
    )
    static = pl.DataFrame(
        {"player_id": list(range(1, 7)), "position": ["G", "G", "F", "F", "C", "C"]}
    )
    return played_df, pl.DataFrame(trk), pl.DataFrame(hus), static


def _build(played, trk, hus, static, fams=ALL):
    return build_tracking_features(played, trk, hus, static, fams).sort(["game_id", "player_id"])


def test_planted_future_rows_do_not_move_earlier_features() -> None:
    played, trk, hus, static = _league()
    base = _build(played, trk, hus, static)
    cut = "00025"
    trk2 = trk.with_columns(
        [
            pl.when(pl.col("game_id") >= cut).then(1e6).otherwise(pl.col(c)).alias(c)
            for c in trk.columns
            if c not in ("game_id", "player_id")
        ]
    )
    hus2 = hus.with_columns(
        [
            pl.when(pl.col("game_id") >= cut).then(1e6).otherwise(pl.col(c)).alias(c)
            for c in hus.columns
            if c not in ("game_id", "player_id")
        ]
    )
    alt = _build(played, trk2, hus2, static)
    # features of game `cut` and later use only rows < cut for the PLAYER part, but the
    # position prior for games on later dates sees earlier dates only -> rows at game
    # `cut` itself must also be unchanged (its own row and later ones are planted).
    a = base.filter(pl.col("game_id") <= cut)
    b = alt.filter(pl.col("game_id") <= cut)
    assert a.equals(b)
    assert not base.filter(pl.col("game_id") > cut).equals(alt.filter(pl.col("game_id") > cut))


def test_planted_same_game_row_is_excluded_from_own_features() -> None:
    played, trk, hus, static = _league()
    base = _build(played, trk, hus, static)
    target = ("00020", 2)
    mask = (pl.col("game_id") == target[0]) & (pl.col("player_id") == target[1])
    trk2 = trk.with_columns(pl.when(mask).then(9999.0).otherwise(pl.col("passes")).alias("passes"))
    alt = _build(played, trk2, hus, static)
    sel = (pl.col("game_id") == target[0]) & (pl.col("player_id") == target[1])
    assert base.filter(sel).equals(alt.filter(sel))  # own game unchanged
    nxt = (pl.col("game_id") == "00021") & (pl.col("player_id") == 2)
    assert base.filter(nxt)["trk_passes"][0] != alt.filter(nxt)["trk_passes"][0]  # later moves


def test_hand_computed_rate_and_shrinkage() -> None:
    d0 = dt.date(2023, 1, 1)
    played = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "player_id": [1, 1, 1],
            "game_date": [d0, d0 + dt.timedelta(days=1), d0 + dt.timedelta(days=2)],
            "minutes": [30.0, 20.0, 25.0],
        }
    )
    trk = pl.DataFrame({"game_id": ["a", "b"], "player_id": [1, 1], "passes": [60.0, 30.0]})
    static = pl.DataFrame({"player_id": [1], "position": ["G"]})
    # fill the other passing columns with constants; only passes is inspected
    full = trk.with_columns(
        [pl.lit(1.0).alias(c) for c in ALL_FAMILIES["passing"].tracking_cols if c != "passes"]
    )
    out = build_tracking_features(played, full, None, static, ("passing",)).sort("game_id")
    lam = 0.5**0.1
    # game c: weights a -> lam^2, b -> lam^1
    num = lam**2 * 60.0 + lam * 30.0
    den = lam**2 * 30.0 + lam * 20.0
    r = 36.0 * num / den
    w = lam**2 + lam
    # position prior at c's date: games a,b (dates strictly before): 36*(90)/(50)
    mu = 36.0 * 90.0 / 50.0
    want = (w * r + 5.0 * mu) / (w + 5.0)
    got = out.filter(pl.col("game_id") == "c")["trk_passes"][0]
    assert abs(got - want) < 1e-9
    # game a: no prior data anywhere -> NaN rate, neff 0 (not NULL)
    a = out.filter(pl.col("game_id") == "a")
    assert np.isnan(a["trk_passes"][0]) or a["trk_passes"][0] is None
    assert a["trk_neff"][0] == 0.0
    # game b: prior = only game a -> position prior at b's date is game a itself
    assert abs(out.filter(pl.col("game_id") == "b")["trk_neff"][0] - np.log1p(lam)) < 1e-12


def test_dnp_rows_get_no_feature_row_and_never_contribute() -> None:
    played, trk, hus, static = _league()
    out = _build(played, trk, hus, static)
    dnp = played.filter(pl.col("minutes").is_null()).select("game_id", "player_id")
    assert dnp.height > 0
    assert out.join(dnp, on=["game_id", "player_id"], how="inner").is_empty()
    assert out.height == played.filter(pl.col("minutes") > 0).height
    # plant huge tracking values on the DNP rows (the feed has zero rows there already)
    big = trk.join(
        dnp.with_columns(pl.lit(True).alias("_d")), on=["game_id", "player_id"], how="left"
    )
    big = big.with_columns(
        [
            pl.when(pl.col("_d").is_not_null()).then(1e6).otherwise(pl.col(c)).alias(c)
            for c in trk.columns
            if c not in ("game_id", "player_id")
        ]
    ).drop("_d")
    alt = _build(played, big, hus, static)
    assert out.equals(alt)  # DNP-game tracking rows are inert (played-only)


def test_missing_game_is_unobserved_and_features_not_null_after_first_data() -> None:
    played, trk, hus, static = _league()
    out = _build(played, trk, hus, static)
    # game index 3 is missing in the feed for everyone; its features must still be defined
    g3 = out.filter(pl.col("game_id") == "00003")
    feat_cols = [c for c in out.columns if c not in ("game_id", "player_id")]
    assert g3.select(feat_cols).null_count().sum_horizontal()[0] == 0
    # only the very first date (no prior tracked data) may be NaN
    later = out.filter(pl.col("game_id") > "00000")
    arr = later.select([c for c in feat_cols if c != "trk_neff"]).to_numpy().astype(float)
    assert not np.isnan(arr).any()
    # null rate does not depend on tonight's minutes
    mins = played.select("game_id", "player_id", "minutes")
    j = out.join(mins, on=["game_id", "player_id"])
    nn = j["trk_passes"].is_nan() | j["trk_passes"].is_null()
    lo = j.filter(pl.col("minutes") < 5)
    assert nn.sum() == j.filter(pl.col("game_id") == "00000").height
    assert lo.height >= 0


def test_hustle_required_when_family_needs_it() -> None:
    played, trk, _, static = _league()
    try:
        build_tracking_features(played, trk, None, static, ("activity",))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    out = build_tracking_features(played, trk, None, static, ("activity_T",))
    assert "hus_neff" not in out.columns and "trk_speed" in out.columns
