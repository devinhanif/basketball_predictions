# ruff: noqa: E501
"""Assembly: planted same-game / future tests on a synthetic league, and hand-computed team pieces."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from research.features import advanced_assemble as A
from research.features import advanced_shots as sh

N_DAYS = 14
TEAMS = (1, 2, 3, 4)
PER_TEAM = 7


def synth(seed: int = 0) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    rng = np.random.default_rng(seed)
    games = []
    for d in range(N_DAYS):
        pairs = [(1, 2), (3, 4)] if d % 2 == 0 else [(1, 3), (2, 4)]
        for j, (h, a) in enumerate(pairs):
            games.append((f"g{d:02d}{j}", dt.date(2024, 1, 1) + dt.timedelta(days=d), 2024, h, a))
    g = pl.DataFrame(
        games, schema=["game_id", "game_date", "season", "home_team", "away_team"], orient="row"
    )
    box, oc, pt, ps, trk, hus = [], [], [], [], [], []
    for gid, _, _, h, a in games:
        for t in (h, a):
            for k in range(PER_TEAM):
                pid = t * 100 + k
                mins = float(rng.integers(0, 38))
                fga = int(rng.integers(0, 18)) if mins else 0
                fgm = int(rng.integers(0, fga + 1))
                fg3a = int(rng.integers(0, fga + 1))
                fta = int(rng.integers(0, 8))
                box.append((gid, pid, t, mins if mins else None, int(2 * fgm + rng.integers(0, 5)), int(rng.integers(0, 9)), int(rng.integers(0, 8)), int(rng.integers(0, min(fg3a, fgm) + 1)),
                            int(rng.integers(0, 3)), int(rng.integers(0, 2)), int(rng.integers(0, 4)), fgm, fga, fg3a, int(rng.integers(0, fta + 1)), fta,
                            int(rng.integers(0, 4)), int(rng.integers(0, 7)), int(rng.integers(0, 5)), ["G", "F", "C"][k % 3]))  # fmt: skip
                oc.append((gid, pid, mins / 1.0, int(fga + rng.integers(20, 40)), int(fgm + rng.integers(8, 20)), 10, 8 + int(fta), 5 + int(rng.integers(0, 6)),
                           int(rng.integers(20, 60)), int(rng.integers(20, 60)), int(rng.integers(3, 12)), int(rng.integers(10, 30)), int(rng.integers(10, 30)), int(rng.integers(3, 12))))  # fmt: skip
                pt.append(
                    (
                        gid,
                        pid,
                        fga,
                        int(rng.integers(0, fga + 1)),
                        int(rng.integers(0, 8)),
                        fgm,
                        int(rng.integers(0, fgm + 1)),
                        int(rng.integers(0, 3)),
                        int(rng.integers(0, 2)),
                    )
                )
                att = rng.integers(0, 6, size=5)
                ps.append(
                    (
                        gid,
                        pid,
                        *[int(x) for x in att],
                        *[int(rng.integers(0, x + 1)) for x in att],
                        *[int(x) for x in rng.integers(0, 4, size=len(sh.SUBTYPES))],
                    )
                )
                trk.append(
                    (
                        gid,
                        pid,
                        t,
                        int(rng.integers(0, 6)),
                        int(rng.integers(0, 6)),
                        int(rng.integers(0, 3)),
                        int(rng.integers(3, 6)),
                    )
                )
                hus.append((gid, pid, int(rng.integers(0, 6)))) if mins and mins > 8 else None
    box_df = pl.DataFrame(
        box,
        schema=[
            "game_id",
            "player_id",
            "team_id",
            "minutes",
            "pts",
            "reb",
            "ast",
            "fg3m",
            "stl",
            "blk",
            "pf",
            "fgm",
            "fga",
            "fg3a",
            "ftm",
            "fta",
            "tov",
            "oreb",
            "dreb",
            "pos",
        ],
        orient="row",
    )
    oc_df = pl.DataFrame(
        oc,
        schema=[
            "game_id",
            "player_id",
            "oc_min",
            "oc_fga",
            "oc_fgm",
            "oc_fg3a",
            "oc_fta",
            "oc_tov",
            "oc_poss_off",
            "oc_poss_def",
            "oc_oreb",
            "oc_opp_dreb",
            "oc_dreb",
            "oc_opp_oreb",
        ],
        orient="row",
    )
    pt_df = pl.DataFrame(
        pt,
        schema=[
            "game_id",
            "player_id",
            "fsp",
            "tr_fsp",
            "tr_pts",
            "mk",
            "ast_mk",
            "pb_att",
            "pb_made",
        ],
        orient="row",
    )
    ps_cols = (
        [f"att_{b}" for b in A.BK] + [f"made_{b}" for b in A.BK] + [f"att_{s}" for s in sh.SUBTYPES]
    )
    ps_df = pl.DataFrame(ps, schema=["game_id", "player_id", *ps_cols], orient="row")
    trk_df = pl.DataFrame(
        trk,
        schema=[
            "game_id",
            "player_id",
            "team_id",
            "contested_fga",
            "uncontested_fga",
            "rim_fgm",
            "rim_fga",
        ],
        orient="row",
    )
    hus_df = pl.DataFrame(hus, schema=["game_id", "player_id", "contested_shots"], orient="row")
    ts, tf, pg = [], [], []
    for gid, _, _, h, a in games:
        for t in (h, a):
            att = rng.integers(5, 30, size=5)
            ts.append((gid, t, *[int(x) for x in att], *[int(rng.integers(0, x + 1)) for x in att]))
            tf.append((gid, t, int(rng.integers(80, 110)), int(rng.integers(30, 60))))
            pg.append((gid, t, int(rng.integers(85, 110)), 4))
    inp = {
        "games": g, "box": box_df, "oc": oc_df, "ptype": pt_df, "pshots": ps_df, "track": trk_df, "hustle": hus_df,
        "tshots": pl.DataFrame(ts, schema=["game_id", "def_team_id", *[f"att_{b}" for b in A.BK], *[f"made_{b}" for b in A.BK]], orient="row"),
        "tfast": pl.DataFrame(tf, schema=["game_id", "def_team_id", "faced", "faced_fast"], orient="row"),
        "poss_tg": pl.DataFrame(pg, schema=["game_id", "team_id", "poss", "max_period"], orient="row"),
    }  # fmt: skip
    rows = box_df.select("game_id", "player_id").with_columns(pl.lit(20.0).alias("min10"))
    return inp, rows


def _same(a: pl.DataFrame, b: pl.DataFrame, keys: pl.DataFrame) -> bool:
    x = a.join(keys, on=["game_id", "player_id"]).sort(["game_id", "player_id"])
    y = b.join(keys, on=["game_id", "player_id"]).sort(["game_id", "player_id"])
    assert x.height == y.height > 0
    for c in x.columns:
        if c in ("game_id", "player_id"):
            continue
        u, v = x[c].to_numpy().astype(float), y[c].to_numpy().astype(float)
        if not np.allclose(u, v, equal_nan=True, rtol=0, atol=1e-12):
            return False
    return True


def test_columns_complete_and_finite_after_history() -> None:
    inp, rows = synth()
    out = A.build_advanced(inp, rows)
    cols = {c for v in A.FAMILY_COLUMNS.values() for c in v}
    assert cols <= set(out.columns)
    assert len(A.CELLS) == 15
    late = out.filter(pl.col("game_id") >= "g100")
    for c in sorted(cols):
        assert np.isfinite(late[c].to_numpy().astype(float)).all(), c


def test_planted_same_game_leaves_the_game_features_unchanged() -> None:
    inp, rows = synth()
    base = A.build_advanced(inp, rows)
    targets = {"g070", "g071"}  # same day: neither feeds the other
    edited = A.build_advanced(A.numeric_perturb(inp, targets), rows)
    keys = rows.filter(pl.col("game_id").is_in(list(targets))).select("game_id", "player_id")
    assert _same(base, edited, keys)  # control below proves the edit is not inert
    later = rows.filter(pl.col("game_id") >= "g100").select("game_id", "player_id")
    assert not _same(base, edited, later)


def test_planted_future_leaves_earlier_rows_unchanged() -> None:
    inp, rows = synth()
    base = A.build_advanced(inp, rows)
    cutoff = dt.date(2024, 1, 9)  # day index 8
    late = set(inp["games"].filter(pl.col("game_date") >= cutoff)["game_id"].to_list())
    edited = A.build_advanced(A.numeric_perturb(inp, late), rows)
    early = rows.join(inp["games"].filter(pl.col("game_date") <= cutoff), on="game_id").select(
        "game_id", "player_id"
    )
    # rows dated ON the cutoff day are themselves edited games and must still not see their own data
    assert _same(base, edited, early)
    after = rows.join(
        inp["games"].filter(pl.col("game_date") > dt.date(2024, 1, 11)), on="game_id"
    ).select("game_id", "player_id")
    assert not _same(base, edited, after)


def test_team_pace_table_and_league_asof_hand_computed() -> None:
    inp, _ = synth()
    tg = A.team_games(inp)
    r = tg.filter(pl.col("game_id") == "g000").sort("team_id")
    assert r["minutes_played"].to_list() == [48.0, 48.0]  # max_period 4: no overtime
    pf = A.pace_factor_table(tg)
    first = pf.filter(pl.col("game_id") == "g000")
    assert first["pace_factor"].to_list() == pytest.approx([1.0, 1.0])  # no history: factor 1
    d1 = tg.filter(pl.col("game_date") == dt.date(2024, 1, 1))
    lg = (d1["poss"] * 48.0 / d1["minutes_played"]).mean()
    second = pf.filter(pl.col("game_id") == "g010").sort("team_id")
    own = tg.filter(pl.col("game_id") == "g010").sort("team_id")
    expect = [lg / (p * 48.0 / m) for p, m in zip(own["poss"], own["minutes_played"], strict=True)]
    assert second["pace_factor"].to_list() == pytest.approx(expect)


def test_league_asof_excludes_same_day() -> None:
    daily = pl.DataFrame(
        {
            "game_date": [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 2)],
            "n": [1.0, 100.0, 300.0],
            "d": [1.0, 1.0, 1.0],
        }
    )
    out = A.league_asof(
        daily,
        {"r": ("n", "d")},
        pl.Series("game_date", [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)]),
    ).sort("game_date")
    r = out["r"].to_list()
    assert r[0] is None and r[1] == pytest.approx(1.0) and r[2] == pytest.approx(401 / 3)


def test_hustle_presence_flag_is_explicit() -> None:
    inp, _ = synth()
    tg = A.team_games(inp)
    assert (tg["hus_min"] <= tg["tot_min"] + 1e-9).all()
    ti = A.team_stat_inputs(tg)
    cov = tg["hus_min"] / tg["tot_min"]
    assert (cov < 1.0).any()  # absent rows are not zero-filled into the denominator
    assert ti["hus_contest__den"].null_count() <= tg.height
