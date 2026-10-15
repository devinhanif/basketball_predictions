# ruff: noqa: E501
"""RETURNING_TEAMMATE_2 features: as-of, planted future, planted past, exclusions, missingness."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from research.eval import returning_teammate_2_features as F

N_GAMES = 10
TEAM = 1


def _world(
    mins: dict[int, list[float]],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    d0 = dt.date(2024, 1, 1)
    gids = [f"g{k:02d}" for k in range(N_GAMES)]
    dates = [d0 + dt.timedelta(days=2 * k) for k in range(N_GAMES)]
    rows = []
    for pid, ms in mins.items():
        for gid, d, m in zip(gids, dates, ms, strict=True):
            rows.append((gid, TEAM, pid, m, d, 2024, 10, 2, 1, 1))
    box = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "minutes": pl.Float64,
            "game_date": pl.Date,
            "season": pl.Int64,
            "fga": pl.Int64,
            "fta": pl.Int64,
            "tov": pl.Int64,
            "starter": pl.Int64,
        },
        orient="row",
    )
    tg = pl.DataFrame(
        {
            "team_id": [TEAM] * N_GAMES,
            "season": [2024] * N_GAMES,
            "game_id": gids,
            "game_date": dates,
            "k": list(range(N_GAMES)),
        }
    )
    return box, tg, pl.DataFrame()


def _status(rows: list[tuple[str, int, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema={"game_id": pl.Utf8, "player_id": pl.Int64, "status": pl.Utf8}, orient="row"
    )


def _all_games_snapshot(extra: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    # every game has a usable snapshot (a dummy bystander row 99 keeps the game 'known')
    return [(f"g{k:02d}", 99, "available") for k in range(N_GAMES)] + extra


# star 7 plays g0-g3, misses g4-g6 (listed out), is back at g7; teammate 8 plays always
MINS = {7: [34, 34, 34, 34, 0, 0, 0, 30, 30, 30], 8: [28.0] * N_GAMES}
OUT_RUN = [(f"g{k:02d}", 7, "out") for k in (4, 5, 6)]


def _feat(box: pl.DataFrame, tg: pl.DataFrame, st: pl.DataFrame, **kw: object) -> pl.DataFrame:
    f, _ = F.build_returning(box, tg, st, **kw)  # type: ignore[arg-type]
    return f


def _row(f: pl.DataFrame, gid: str, pid: int) -> dict[str, float]:
    return f.filter((pl.col("game_id") == gid) & (pl.col("player_id") == pid)).to_dicts()[0]


def test_returner_fires_for_teammate_on_return_night_only() -> None:
    box, tg, _ = _world(MINS)
    st = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]))
    f = _feat(box, tg, st)
    r = _row(f, "g07", 8)
    assert (
        r["n_ret"] == 1
        and r["ret_min"] == pytest.approx(34.0)
        and r["ret_min_star"] == pytest.approx(34.0)
    )
    assert (
        r["ret_min_listed"] == pytest.approx(34.0) and r["ret_missed"] == 3 and r["ret_known"] == 1
    )
    assert 0.0 < r["ret_use"] < 1.0
    assert (
        _row(f, "g06", 8)["n_ret"] == 0
    )  # still out: not a returner (needs >= 3 missed AND not out tonight)
    assert _row(f, "g08", 8)["n_ret"] == 0  # he played g07, the flag is the return night only
    assert (
        _row(f, "g07", 7)["n_ret"] == 0
    )  # the returner has no returning teammate (excluded from his own row)


def test_planted_future_changes_nothing_before_the_game() -> None:
    box, tg, _ = _world(MINS)
    st = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]))
    base = _feat(box, tg, st)
    # rewrite tonight's box (g07) and everything after: minutes, plays-or-not; the features of g07 and earlier must not move
    rewritten = box.with_columns(
        pl.when(pl.col("game_id") >= "g07")
        .then(pl.col("minutes") * 0.0 + 5.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    chg = _feat(rewritten, tg, st)
    keep = [c for c in F.FEATURES if c != "ret_own_gain"]
    a = base.filter(pl.col("game_id") <= "g07").sort(["game_id", "player_id"])
    b = chg.filter(pl.col("game_id") <= "g07").sort(["game_id", "player_id"])
    assert a.select(keep).equals(b.select(keep))
    assert a["ret_own_gain"].to_list() == b["ret_own_gain"].to_list()
    # snapshots stamped after the game's own snapshot are not an input: dropping later games' rows leaves g07 unchanged
    st2 = st.filter(pl.col("game_id") <= "g07")
    assert _row(_feat(box, tg, st2), "g07", 8) == _row(base, "g07", 8)


def test_planted_past_moves_the_columns() -> None:
    box, tg, _ = _world(MINS)
    st = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]))
    a = _row(_feat(box, tg, st), "g07", 8)
    lighter = box.with_columns(
        pl.when((pl.col("player_id") == 7) & (pl.col("game_id") < "g04"))
        .then(20.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    b = _row(_feat(lighter, tg, st), "g07", 8)
    assert (
        b["ret_min"] == pytest.approx(20.0)
        and b["ret_min_star"] == 0.0
        and a["ret_min_star"] == pytest.approx(34.0)
    )


@pytest.mark.parametrize("tonight", ["out", "doubtful"])
def test_out_or_doubtful_tonight_is_not_a_returner(tonight: str) -> None:
    box, tg, _ = _world(MINS)
    st = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, tonight)]))
    assert _row(_feat(box, tg, st), "g07", 8)["n_ret"] == 0


def test_return_missing_from_the_report_still_fires_but_not_as_listed() -> None:
    """A return that is not on tonight's report (unlisted): the rule counts him (not OUT tonight) but ret_min_listed stays 0."""
    box, tg, _ = _world(MINS)
    st = _status(_all_games_snapshot(OUT_RUN))
    r = _row(_feat(box, tg, st), "g07", 8)
    assert r["n_ret"] == 1 and r["ret_min"] == pytest.approx(34.0) and r["ret_min_listed"] == 0.0


def test_game_without_a_usable_snapshot_gets_zeros_and_ret_known_zero() -> None:
    box, tg, _ = _world(MINS)
    rows = [r for r in _all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]) if r[0] != "g07"]
    r = _row(_feat(box, tg, _status(rows)), "g07", 8)
    assert r["ret_known"] == 0 and all(
        r[c] == 0 for c in ("ret_min", "n_ret", "ret_use", "ret_missed", "ret_own_gain")
    )


def test_prev_rules_listed_prev_vs_out_all_vs_box_only() -> None:
    box, tg, _ = _world(MINS)
    # listed only questionable on g06 (not the full out run): listed_prev fires, out_all does not
    st = _status(
        _all_games_snapshot(
            [
                ("g04", 7, "out"),
                ("g05", 7, "out"),
                ("g06", 7, "questionable"),
                ("g07", 7, "probable"),
            ]
        )
    )
    assert _row(_feat(box, tg, st), "g07", 8)["n_ret"] == 1
    assert _row(_feat(box, tg, st, prev_rule="out_all"), "g07", 8)["n_ret"] == 0
    # not listed at all on g06: only box_only fires
    st2 = _status(
        _all_games_snapshot([("g04", 7, "out"), ("g05", 7, "out"), ("g07", 7, "probable")])
    )
    assert _row(_feat(box, tg, st2), "g07", 8)["n_ret"] == 0
    assert _row(_feat(box, tg, st2, prev_rule="box_only"), "g07", 8)["n_ret"] == 1


def test_short_absence_and_traded_player_are_not_returners() -> None:
    box, tg, _ = _world({7: [34, 34, 34, 34, 34, 0, 0, 30, 30, 30], 8: [28.0] * N_GAMES})
    st = _status(
        _all_games_snapshot([("g05", 7, "out"), ("g06", 7, "out"), ("g07", 7, "probable")])
    )
    assert _row(_feat(box, tg, st), "g07", 8)["n_ret"] == 0  # missed only 2 games
    assert _row(_feat(box, tg, st, min_missed=2), "g07", 8)["n_ret"] == 1
    # played for another team (team 2) on g05 date: dropped as a returner
    box2, tg2, _ = _world(MINS)
    other = pl.DataFrame(
        {
            "game_id": ["x1"],
            "team_id": [2],
            "player_id": [7],
            "minutes": [25.0],
            "game_date": [dt.date(2024, 1, 11)],
            "season": [2024],
            "fga": [5],
            "fta": [1],
            "tov": [1],
            "starter": [1],
        }
    )
    box2 = pl.concat([box2, other])
    tg2 = pl.concat(
        [
            tg2,
            pl.DataFrame(
                {
                    "team_id": [2],
                    "season": [2024],
                    "game_id": ["x1"],
                    "game_date": [dt.date(2024, 1, 11)],
                    "k": [0],
                }
            ),
        ]
    )
    st3 = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]))
    assert _row(_feat(box2, tg2, st3), "g07", 8)["n_ret"] == 0


def test_own_gain_uses_only_games_before_tonight() -> None:
    box, tg, _ = _world({7: MINS[7], 8: [20, 20, 20, 20, 30, 30, 30, 22, 22, 22]})
    st = _status(_all_games_snapshot(OUT_RUN + [("g07", 7, "probable")]))
    r = _row(_feat(box, tg, st), "g07", 8)
    assert r["ret_own_gain"] == pytest.approx(
        10.0
    )  # 30 over the 3 absence games minus 20 before them


def test_snapshot_source_hash_is_stable_and_snapshot_picks_latest_usable() -> None:
    assert len(F.snapshot_source_sha()) == 64
    tip = dt.datetime(2024, 1, 1, 19)
    games = pl.DataFrame({"game_id": ["a"], "tip": [tip]})
    rep = pl.DataFrame(
        {
            "game_id": ["a", "a", "a"],
            "player_id": [1, 1, 2],
            "status": ["questionable", "out", "out"],
            "as_of": [
                tip - dt.timedelta(hours=5),
                tip - dt.timedelta(minutes=61),
                tip - dt.timedelta(minutes=30),
            ],
        }
    )
    s = F.snapshot_status(rep, games)
    assert s.to_dicts() == [
        {
            "game_id": "a",
            "player_id": 1,
            "status": "out",
            "snap_as_of": tip - dt.timedelta(minutes=61),
        }
    ]
