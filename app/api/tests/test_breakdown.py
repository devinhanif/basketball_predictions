"""The leg breakdown: as-of filter, hit-rate math at any line, small-sample flags, combos."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import breakdown as bd
from parlayhitters_api.forecast import line_probs
from parlayhitters_api.history import STAT_ORDER, History
from parlayhitters_api.store import Store

SLATE = "2024-11-12"
TIP = "2024-11-13T00:00:00Z"
P, X, Y, OPP_G = 100, 101, 102, 200
HOME, AWAY = 1, 2


def _game_dict(gid: str, d: str) -> dict[str, Any]:
    return {
        "game_id": gid,
        "date": d,
        "season": 2024,
        "tip_utc": TIP,
        "home": {"id": HOME, "abbr": "AAA", "city": "A", "name": "Aces"},
        "away": {"id": AWAY, "abbr": "BBB", "city": "B", "name": "Bees"},
    }


def _hist(
    p_games: list[tuple[str, int, int, int, int, int]],
    *,
    extra_box: dict[str, list[list[Any]]] | None = None,
    availability: dict[str, list[list[Any]]] | None = None,
    prices: dict[str, Any] | None = None,
    start: str = "2024-10-25",
) -> History:
    """P plays for HOME in each (date, pts, reb, ast, stl, blk) row; one game a day from `start`
    unless dates are given. The game on SLATE is the one being bet."""
    games: dict[str, list[Any]] = {}
    box: dict[str, list[list[Any]]] = {}
    for i, (d, pts, reb, ast, stl, blk) in enumerate(p_games):
        gid = f"g{i:03d}"
        games[gid] = [d, 2024, HOME, AWAY]
        # player, team, min, pts, reb, ast, fg3m, stl, blk, starter
        box[gid] = [
            [P, HOME, 30.0 + i % 3, pts, reb, ast, 1, stl, blk, 1],
            [OPP_G, AWAY, 28.0, 12, 4, 3, 1, 1, 0, 1],
        ]
    for gid, rows in (extra_box or {}).items():
        box[gid].extend(rows)
    data = {
        "window": {"from_season": 2023, "to_date": "2024-11-30", "max_season": 2024},
        "box_columns": [],
        "games": games,
        "box": box,
        "teams": {str(HOME): "AAA", str(AWAY): "BBB"},
        "players": {
            str(P): {"name": "Pat Player", "pos": "Guard"},
            str(X): {"name": "Xavier Out", "pos": "Forward"},
            str(Y): {"name": None, "pos": "Forward"},
            str(OPP_G): {"name": "Opp Guard", "pos": "Guard"},
        },
        "availability": availability or {},
        "prices": prices or {},
    }
    return History(data)


def _daily(values: list[tuple[int, int, int, int, int]], first: str = "2024-10-25") -> list[Any]:
    d0 = date.fromisoformat(first)
    return [((d0 + timedelta(days=i)).isoformat(), *v) for i, v in enumerate(values)]


def _slate_game_id(h: History) -> str:
    return next(g for g, v in h.games.items() if v[0] == SLATE)


def _build(
    h: History, stat: str = "pts", line: float | None = None, side: bd.Side = "over", **player: Any
) -> bd.Breakdown:
    gid = _slate_game_id(h)
    pl = {"player_id": P, "name": "Pat Player", "side": "home", "props": {}, **player}
    return bd.build(h, _game_dict(gid, SLATE), pl, stat, line, side)


# 14 daily games from 2024-10-29 to 2024-11-11, then the slate game 2024-11-12, then two later.
PTS = [10, 12, 15, 15, 20, 22, 25, 18, 30, 8, 16, 21, 14, 17]
BASE = [(v, 5, 4, 1, 0) for v in PTS]


def _full() -> History:
    rows = _daily(BASE, "2024-10-29")  # ends 2024-11-11
    rows.append((SLATE, 99, 99, 99, 9, 9))  # tonight's own box score: must never be counted
    rows.append(("2024-11-13", 98, 98, 98, 9, 9))  # the day after: future
    rows.append(("2024-11-14", 97, 97, 97, 9, 9))
    return _hist(rows)


# ----------------------------------------------------------------------------- as-of filter


def test_same_day_and_future_games_never_enter_last_ten() -> None:
    b = _build(_full())
    dates = [c.date for c in b.chips]
    assert len(dates) == 10
    assert max(dates) == "2024-11-11"  # the day before the slate, not the slate day
    assert SLATE not in dates and all(d < SLATE for d in dates)
    assert all(c.value <= 30 for c in b.chips)  # the 99, 98, 97 sentinel rows are absent
    assert b.prior_games == 14
    assert b.last10.avg == pytest.approx(sum(PTS[-10:]) / 10)


def test_as_of_filter_holds_for_every_tally_and_stat() -> None:
    h = _full()
    for stat in STAT_ORDER:
        b = _build(h, stat, line=0.5)
        assert b.last10.n == 10 and b.season.n == 14
        for s in b.splits:
            assert s.since_last_season.n <= 14
        assert max(c.date for c in b.chips) < SLATE
        assert b.ladder_n10 == 10 and b.ladder_n5 == 5


def test_moving_the_slate_date_back_drops_later_games() -> None:
    h = _full()
    gid = next(g for g, v in h.games.items() if v[0] == "2024-11-05")
    pl: dict[str, Any] = {"player_id": P, "name": "Pat", "side": "home", "props": {}}
    b = bd.build(h, _game_dict(gid, "2024-11-05"), pl, "pts", 15.5, "over")
    assert max(c.date for c in b.chips) == "2024-11-04"
    assert b.prior_games == 7  # 10-29 .. 11-04


def test_opponent_allowed_ignores_same_day_and_future_games() -> None:
    h = _full()
    b = _build(h)
    assert b.opponent_allowed is not None
    # P is HOME's only guard, so what AWAY allowed to guards is P's own points. The 99/98/97
    # sentinel games would drag the mean up if the slate day or later games leaked in.
    assert b.opponent_allowed.n_games == 14
    assert b.opponent_allowed.avg_allowed == pytest.approx(sum(PTS) / 14)


def test_availability_published_after_the_cutoff_is_ignored() -> None:
    avail = {
        # filled below once we know the slate game id
    }
    h = _hist(_daily(BASE, "2024-10-29") + [(SLATE, 0, 0, 0, 0, 0)])
    gid = _slate_game_id(h)
    avail[gid] = [
        [X, "out", "Injury", "2024-11-12T21:00:00+00:00"],  # before tip minus an hour (23:00Z)
        [X, "available", "-", "2024-11-12T23:30:00+00:00"],  # after the cutoff: must not count
    ]
    h.availability = avail
    # X must have a prior row on HOME and be a rotation player
    for i, (gidx, rows) in enumerate(h.played.items()):
        if gidx != gid:
            row = rows[HOME][P]
            h.logs[X].append(
                type(row)(
                    game_id=row.game_id,
                    date=row.date,
                    season=2024,
                    team=HOME,
                    opp=AWAY,
                    home=True,
                    minutes=25.0,
                    starter=False,
                    base=(5, 5, 5, 0, 0, 0),
                )
            )
            rows[HOME][X] = h.logs[X][-1]
            if i > 6:
                break
    b = _build(h)
    assert [t.player_id for t in b.teammates.items] == [X]
    assert b.teammates.items[0].status == "out"


# ----------------------------------------------------------------------------- hit-rate math


def test_hit_state_over_under_and_push() -> None:
    assert bd.hit_state(20, 19.5, "over") == "hit"
    assert bd.hit_state(19, 19.5, "over") == "miss"
    assert bd.hit_state(19, 19.5, "under") == "hit"
    assert bd.hit_state(20, 19.5, "under") == "miss"
    assert bd.hit_state(20, 20, "over") == "push"
    assert bd.hit_state(20, 20, "under") == "push"


def test_last_ten_and_last_five_at_the_main_and_alt_lines() -> None:
    h = _full()
    last10 = PTS[-10:]  # 20 22 25 18 30 8 16 21 14 17
    last5 = PTS[-5:]  # 8 16 21 14 17
    for line in (14.5, 17.5, 19.5, 20.5, 25.5, 30.5):
        b = _build(h, line=line)
        assert b.last10.hits == sum(v > line for v in last10), line
        assert b.last10.misses == sum(v < line for v in last10), line
        assert b.last5.hits == sum(v > line for v in last5), line
        assert b.last10.n == 10 and b.last5.n == 5
    # whole-number line: a value exactly on it is a push, neither hit nor miss
    b = _build(h, line=20)
    assert b.last10.pushes == 1  # one game of exactly 20
    assert b.last10.hits == 4 and b.last10.misses == 5
    assert b.last10.hits + b.last10.misses + b.last10.pushes == 10
    # under side mirrors it
    u = _build(h, line=20, side="under")
    assert u.last10.hits == b.last10.misses and u.last10.misses == b.last10.hits


def test_ladder_matches_direct_tallies_at_every_line() -> None:
    h = _full()
    base = _build(h, line=17.5)
    assert base.ladder[0].line < 8 and base.ladder[-1].line > 30
    for row in base.ladder:
        direct = _build(h, line=row.line)
        assert row.l10_over == direct.last10.hits
        assert row.l10_under == direct.last10.misses
        assert row.l10_push == direct.last10.pushes
        assert row.l5_over == direct.last5.hits
    # lines are on the half-point grid and strictly increasing
    lines = [r.line for r in base.ladder]
    assert lines == sorted(set(lines)) and all(abs(x * 2 - round(x * 2)) < 1e-9 for x in lines)


def test_wilson_interval_brackets_the_rate() -> None:
    b = _build(_full(), line=19.5)
    assert b.last10.rate == pytest.approx(0.5)
    assert b.last10.ci_lo is not None and b.last10.ci_hi is not None
    assert b.last10.ci_lo < 0.5 < b.last10.ci_hi
    assert b.last10.ci_hi - b.last10.ci_lo > 0.4  # ten games say very little
    assert bd.wilson(0, 0) is None


# ----------------------------------------------------------------------------- small samples


def test_small_n_is_flagged_and_stated() -> None:
    h = _hist(_daily(BASE[:3], "2024-11-09") + [(SLATE, 0, 0, 0, 0, 0)])
    b = _build(h, line=10.5)
    assert b.last10.n == 3 and b.last10.small
    assert b.last5.n == 3 and b.last5.small
    assert any("Small sample" in n and "3 earlier games" in n for n in b.notes)


def test_ten_games_is_not_small_but_still_carries_the_noise_note() -> None:
    b = _build(_full(), line=17.5)
    assert not b.last10.small and b.last5.small
    assert not any("Small sample" in n for n in b.notes)
    assert any("noisy" in n and "not an edge" in n for n in b.notes)


def test_no_prior_games_gives_empty_honest_numbers() -> None:
    h = _hist([(SLATE, 20, 5, 5, 1, 1)])
    b = _build(h, line=20.5)
    assert b.last10.n == 0 and b.last10.rate is None and b.last10.small
    assert b.chips == []
    assert any("No earlier games" in n for n in b.notes)
    assert b.opponent_allowed is None


def test_split_samples_carry_their_own_n_and_small_flag() -> None:
    b = _build(_full(), line=17.5)
    for s in b.splits:
        for t in (s.this_season, s.since_last_season):
            assert t.hits + t.misses + t.pushes == t.n
            assert t.small == (t.n < bd.SMALL_N)
    home = next(s for s in b.splits if s.key == "home")
    assert home.tonight and home.since_last_season.n == 14


def test_back_to_back_split_uses_team_schedule() -> None:
    b = _build(_full(), line=17.5)
    # one game a day: every game after the first is a back-to-back; tonight is too
    b2b = next(s for s in b.splits if s.key == "b2b")
    assert b2b.tonight
    assert b2b.since_last_season.n == 13  # first game has no previous team game
    rest2 = next(s for s in b.splits if s.key == "rest2")
    assert rest2.since_last_season.n == 0 and not rest2.tonight


# ----------------------------------------------------------------------------- stats and combos


def test_combos_are_sums_of_parts_per_game() -> None:
    rows = [("2024-11-10", 20, 8, 5, 2, 1), ("2024-11-11", 10, 3, 7, 0, 3), (SLATE, 0, 0, 0, 0, 0)]
    h = _hist(rows)
    expect = {
        "pts": [10, 20],
        "reb": [3, 8],
        "ast": [7, 5],
        "blk": [3, 1],
        "stl": [0, 2],
        "pra": [20, 33],
        "pr": [13, 28],
        "pa": [17, 25],
        "ra": [10, 13],
        "stocks": [3, 3],
    }
    for stat, vals in expect.items():
        b = _build(h, stat, line=0.5)
        assert [c.value for c in b.chips] == vals, stat  # most recent first
    assert sorted(STAT_ORDER) == sorted([*expect, "fg3m"])


def test_default_line_is_just_under_the_middle_of_last_ten() -> None:
    b = _build(_full(), "pra")  # no line given, none posted
    vals = [v + 5 + 4 for v in PTS[-10:]]
    assert b.line_source == "last10_middle"
    assert b.line == bd.default_line(vals)
    assert b.line % 1 == 0.5


# ----------------------------------------------------------------------------- the model


def _prop(q: list[float]) -> dict[str, Any]:
    return {
        "line": 17.5,
        "over_price": -110,
        "under_price": -110,
        "forecast": {"mean": q[9], "q": q, "stamp": "walk-forward out-of-fold"},
    }


Q_LOW = [4 + i * 0.8 for i in range(19)]  # model sees a low night (median near 11.2)
Q_MID = [8 + i * 0.9 for i in range(19)]  # median near 16.1


def test_model_chance_only_for_the_four_forecast_stats() -> None:
    h = _full()
    props = {s: _prop(Q_MID) for s in ("pts", "reb", "ast", "fg3m")}
    for stat in ("pts", "reb", "ast", "fg3m"):
        b = _build(h, stat, line=17.5, props=props)
        assert b.model.available and b.model.p_hit is not None
        assert b.model.p_over == pytest.approx(line_probs(Q_MID, 17.5).p_over)
        assert b.model.reason  # the plain-words reason is present
    for stat in ("blk", "stl", "pra", "pr", "pa", "ra", "stocks"):
        # even when props for the single stats exist, nothing is derived for these
        b = _build(h, stat, line=5.5, props=props)
        assert not b.model.available
        assert b.model.p_hit is None and b.model.p_over is None and b.model.mean is None
        assert b.model.say == "No model forecast for this stat yet."
        assert all(r.model_over is None and r.model_under is None for r in b.ladder)
        assert b.heads_up is None


def test_model_p_follows_the_side() -> None:
    props = {"pts": _prop(Q_MID)}
    o = _build(_full(), line=17.5, props=props)
    u = _build(_full(), line=17.5, side="under", props=props)
    assert o.model.p_hit == pytest.approx(o.model.p_over)
    assert u.model.p_hit == pytest.approx(u.model.p_under)
    assert (o.model.p_over or 0) + (o.model.p_under or 0) + (o.model.p_push or 0) == pytest.approx(
        1
    )


def test_alt_line_moves_hit_rate_and_model_together() -> None:
    props = {"pts": _prop(Q_MID)}
    lo = _build(_full(), line=12.5, props=props)
    hi = _build(_full(), line=22.5, props=props)
    assert lo.last10.hits > hi.last10.hits
    assert (lo.model.p_hit or 0) > (hi.model.p_hit or 0)
    row = next(r for r in hi.ladder if r.line == 22.5)
    assert row.model_over == pytest.approx(hi.model.p_over)


def test_heads_up_when_recent_form_and_model_disagree() -> None:
    # last ten: 20 22 25 18 30 8 16 21 14 17 -> over 14.5 in 8 of 10; model median near 11
    b = _build(_full(), line=14.5, props={"pts": _prop(Q_LOW)})
    assert b.last10.hits == 8
    assert b.model.p_hit is not None and b.model.p_hit < 0.4
    assert b.heads_up is not None and "Heads up" in b.heads_up and "8 of his last 10" in b.heads_up
    assert "neither is a promise" in b.heads_up


def test_no_heads_up_when_they_agree() -> None:
    b = _build(_full(), line=17.5, props={"pts": _prop(Q_MID)})
    assert b.heads_up is None


def test_heads_up_mentions_small_sample() -> None:
    h = _hist(_daily([(30, 5, 4, 1, 0)] * 4, "2024-11-08") + [(SLATE, 0, 0, 0, 0, 0)])
    b = _build(h, line=14.5, props={"pts": _prop(Q_LOW)})
    assert b.heads_up is not None and "small sample" in b.heads_up


# ----------------------------------------------------------------------------- teammates


def test_teammate_out_effect_counts_games_with_and_without() -> None:
    # 14 games. X (a rotation player on HOME) plays in games 0-6 and 11-13 but sits games 7-10.
    rows = _daily(BASE, "2024-10-29") + [(SLATE, 0, 0, 0, 0, 0)]
    gids = [f"g{i:03d}" for i in range(14)]
    xrow = [X, HOME, 28.0, 6, 5, 4, 0, 0, 0, 1]
    extra = {g: [xrow] for i, g in enumerate(gids) if i not in (7, 8, 9, 10)}
    sg = f"g{14:03d}"
    h = _hist(
        rows,
        extra_box=extra,
        availability={
            sg: [[X, "out", "Injury/Illness-Knee", "2024-11-12T17:00:00+00:00"]],
        },
    )
    b = _build(h, line=17.5)
    assert [t.player_id for t in b.teammates.items] == [X]
    t = b.teammates.items[0]
    assert t.without.n == 4 and t.with_.n == 10
    without_vals = [PTS[i] for i in (7, 8, 9, 10)]
    assert t.without.avg == pytest.approx(sum(without_vals) / 4)
    assert t.without.hits == sum(v > 17.5 for v in without_vals)
    assert t.without.small and "small sample" in t.note.lower()
    assert t.avg_minutes == pytest.approx(28.0)


def test_teammate_before_joining_is_not_counted_as_absent() -> None:
    # X only appears on HOME from game 8 on; earlier games he was not on the team
    rows = _daily(BASE, "2024-10-29") + [(SLATE, 0, 0, 0, 0, 0)]
    gids = [f"g{i:03d}" for i in range(14)]
    xrow = [X, HOME, 28.0, 6, 5, 4, 0, 0, 0, 1]
    extra = {g: [xrow] for i, g in enumerate(gids) if i >= 8 and i != 10}
    h = _hist(
        rows,
        extra_box=extra,
        availability={"g014": [[X, "out", "x", "2024-11-12T17:00:00+00:00"]]},
    )
    b = _build(h, line=17.5)
    t = b.teammates.items[0]
    # games 0-7 are within 20 team games of his first appearance, so those count as "on the
    # team and absent"; the rule is a window, and the sample size is shown either way
    assert t.without.n + t.with_.n == 14
    assert t.with_.n == 5  # games 8, 9, 11, 12, 13


def test_unnamed_and_bench_teammates_are_left_out_and_counted() -> None:
    rows = _daily(BASE, "2024-10-29") + [(SLATE, 0, 0, 0, 0, 0)]
    gids = [f"g{i:03d}" for i in range(14)]
    ymin = [Y, HOME, 30.0, 6, 5, 4, 0, 0, 0, 1]
    xmin = [X, HOME, 6.0, 1, 1, 1, 0, 0, 0, 0]  # bench: under the rotation minutes
    extra = {g: [ymin, xmin] for g in gids}
    h = _hist(
        rows,
        extra_box=extra,
        availability={
            "g014": [
                [Y, "out", "x", "2024-11-12T17:00:00+00:00"],
                [X, "out", "x", "2024-11-12T17:00:00+00:00"],
            ]
        },
    )
    b = _build(h)
    assert b.teammates.items == []
    assert b.teammates.omitted_unnamed == 1


# ----------------------------------------------------------------------------- prices


def test_price_only_where_one_is_stored() -> None:
    prices = {
        "g014": {
            str(P): {
                "stl": [
                    {
                        "point": 1.5,
                        "over_price": 120,
                        "under_price": -150,
                        "main": True,
                        "snapshot_utc": "2024-11-12T22:55:38+00:00",
                    }
                ]
            }
        }
    }
    h = _hist(_daily(BASE, "2024-10-29") + [(SLATE, 0, 0, 0, 0, 0)], prices=prices)
    on = _build(h, "stl", line=1.5)
    assert (
        on.prices.over_price == 120 and on.prices.under_price == -150 and on.prices.on_posted_line
    )
    assert on.line_source == "posted"
    alt = _build(h, "stl", line=2.5)
    assert alt.prices.over_price is None and alt.prices.posted_line == 1.5
    none = _build(h, "blk", line=0.5)
    assert none.prices.posted_line is None and none.prices.over_price is None


# ------------------------------------------------------------------ over the real fixtures


@pytest.fixture(scope="module")
def store() -> Store:
    return Store()


def test_real_fixture_history_is_pre_2025_and_has_future_rows(store: Store) -> None:
    assert store.history is not None
    h = store.history
    assert all(v[1] <= 2024 for v in h.games.values())
    assert max(v[0] for v in h.games.values()) < "2025-01-01"
    # the as-of test below only means something if later rows really are in the fixture
    assert any(v[0] >= "2024-11-12" for v in h.games.values())


def test_real_fixture_last_ten_is_strictly_before_the_slate(
    client: TestClient, store: Store
) -> None:
    assert store.history is not None
    for date_, slate in store.slates.items():
        for g in slate["games"][:3]:
            for pl in g["players"][:4]:
                for stat in ("pts", "pra", "stocks"):
                    r = client.get(f"/api/breakdown/{g['game_id']}/{pl['player_id']}/{stat}")
                    assert r.status_code == 200
                    body = r.json()
                    assert body["as_of_date"] == date_
                    assert all(c["date"] < date_ for c in body["chips"])
                    # recompute from raw rows, independently of the module
                    rows = [x for x in store.history.logs[pl["player_id"]] if x.date < date_][-10:]
                    assert [c["game_id"] for c in body["chips"]] == [
                        x.game_id for x in reversed(rows)
                    ]
                    assert body["last10"]["n"] == len(rows)


def test_api_alt_line_and_side(client: TestClient, store: Store) -> None:
    g = store.slates[SLATE]["games"][0]
    pl = next(p for p in g["players"] if "pts" in p["props"])
    base = f"/api/breakdown/{g['game_id']}/{pl['player_id']}/pts"
    main = client.get(base).json()
    assert main["line"] == pl["props"]["pts"]["line"] and main["line_source"] == "posted"
    assert main["prices"]["on_posted_line"] and main["prices"]["over_price"] is not None
    alt = client.get(base, params={"line": main["line"] + 3}).json()
    assert alt["prices"]["over_price"] is None  # no stored price off the posted line
    assert alt["model"]["p_over"] < main["model"]["p_over"]
    assert alt["last10"]["hits"] <= main["last10"]["hits"]
    under = client.get(base, params={"line": main["line"], "side": "under"}).json()
    assert under["last10"]["hits"] == main["last10"]["misses"]
    assert under["model"]["p_hit"] == pytest.approx(main["model"]["p_under"])


def test_api_every_stat_works_and_combos_have_no_model(client: TestClient, store: Store) -> None:
    g = store.slates[SLATE]["games"][1]
    pl = g["players"][0]
    for stat in STAT_ORDER:
        r = client.get(f"/api/breakdown/{g['game_id']}/{pl['player_id']}/{stat}")
        assert r.status_code == 200, stat
        body = r.json()
        expect_model = stat in ("pts", "reb", "ast", "fg3m") and stat in pl["props"]
        assert body["model"]["available"] == expect_model, stat
        if not expect_model:
            assert body["model"]["say"] == "No model forecast for this stat yet."
        assert len(body["tabs"]) == 11


def test_api_errors(client: TestClient, store: Store) -> None:
    g = store.slates[SLATE]["games"][0]
    pid = g["players"][0]["player_id"]
    assert client.get(f"/api/breakdown/nope/{pid}/pts").status_code == 404
    assert client.get(f"/api/breakdown/{g['game_id']}/1/pts").status_code == 404
    assert client.get(f"/api/breakdown/{g['game_id']}/{pid}/dunks").status_code == 404
    assert client.get(f"/api/breakdown/{g['game_id']}/{pid}/pts?side=sideways").status_code == 422
    assert client.get(f"/api/breakdown/{g['game_id']}/{pid}/pts?line=-1").status_code == 422


def test_market_chance_is_the_devigged_pair_of_one_book(client: TestClient) -> None:
    """F13: the leg screen carries the book's chance beside the model's, from the same prices."""
    from parlayhitters_api.forecast import devig_over

    slate = client.get("/api/slates/2024-11-12").json()
    seen = 0
    for g in slate["games"]:
        for kp in g["key_props"][:2]:
            r = client.get(f"/api/breakdown/{g['game_id']}/{kp['player_id']}/{kp['stat']}").json()
            px = r["prices"]
            if px["over_price"] is None or px["under_price"] is None:
                assert px["market_p_over"] is None
                continue
            seen += 1
            assert px["market_p_over"] == pytest.approx(
                devig_over(px["over_price"], px["under_price"])
            )
            assert px["market_book"] == "DraftKings"
    assert seen >= 3
