"""Role steadiness, on/off and without-teammate numbers: the math, the as-of rule, the wording."""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import breakdown as bd
from parlayhitters_api.history import History, Row
from parlayhitters_api.role import player_role
from parlayhitters_api.role_stats import (
    SMALL_N,
    SMALL_POSS,
    STEADY_CV,
    STEADY_MIN_GAMES,
    STEADY_WINDOW,
    onoff,
    per36,
    steadiness,
)
from parlayhitters_api.store import Store

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
P, X, OPP_G = 100, 101, 200
HOME, AWAY = 1, 2
SLATE = "2024-11-12"
TIP = "2024-11-13T00:00:00Z"


def row(minutes: float, *, pts: int = 10, reb: int = 4, ast: int = 2, gid: str = "g") -> Row:
    return Row(
        game_id=gid,
        date="2024-11-01",
        season=2024,
        team=HOME,
        opp=AWAY,
        home=True,
        minutes=minutes,
        starter=True,
        base=(pts, reb, ast, 0, 0, 0),
    )


# ----------------------------------------------------------------------------- steadiness


def test_constant_minutes_is_steady_and_states_n() -> None:
    s = steadiness([row(32.0) for _ in range(15)])
    assert s.label == "Steady"
    assert s.n == 15 and s.sd_minutes == 0.0 and s.cv == 0.0
    assert "varied by about 0.0" in s.say and "last 15 games" in s.say


def test_wide_swings_are_mixed() -> None:
    mins = [36.0, 16.0] * 7 + [26.0]
    s = steadiness([row(m) for m in mins])
    assert s.label == "Mixed"
    assert s.cv is not None and s.cv > STEADY_CV
    assert s.sd_minutes == pytest.approx(statistics.stdev(mins))
    assert s.avg_minutes == pytest.approx(sum(mins) / len(mins))
    assert f"{s.n}" in s.say


def test_cut_is_a_strict_threshold_on_the_spread_share() -> None:
    # mean 30 with a standard deviation just under and just over 20% of it
    base = [30.0] * 13
    under = steadiness([row(m) for m in [*base, 30 - 10.0, 30 + 10.0]])
    over = steadiness([row(m) for m in [*base, 30 - 18.0, 30 + 18.0]])
    assert under.cv is not None and under.cv < STEADY_CV and under.label == "Steady"
    assert over.cv is not None and over.cv > STEADY_CV and over.label == "Mixed"


def test_too_few_games_is_limited_data_with_n_and_no_verdict() -> None:
    s = steadiness([row(30.0) for _ in range(STEADY_MIN_GAMES - 1)])
    assert s.label == "Limited data" and s.n == STEADY_MIN_GAMES - 1
    assert "too few" in s.say and str(s.n) in s.say
    none = steadiness([])
    assert none.label == "Limited data" and none.n == 0 and none.cv is None


def test_only_the_last_window_of_games_counts() -> None:
    old_wild = [row(5.0), row(45.0)] * 10
    recent = [row(30.0) for _ in range(STEADY_WINDOW)]
    s = steadiness(old_wild + recent)
    assert s.label == "Steady" and s.n == STEADY_WINDOW and s.avg_minutes == 30.0


def test_steadiness_wording_is_a_fact_not_an_accuracy_or_betting_claim() -> None:
    for rows in ([row(30.0)] * 15, [row(36.0), row(16.0)] * 8, [row(30.0)] * 3, []):
        s = steadiness(rows)
        text = f"{s.label} {s.say} {s.caveat}".lower()
        for banned in ("easier to bet", "safe", "lock", "edge:", "reliable", "predictable"):
            assert banned not in text
        assert "not" in s.caveat.lower() and "accuracy" in s.caveat.lower()
        assert "accura" not in s.say.lower()


# ----------------------------------------------------------------------------- per 36


def test_per36_is_pooled_total_over_total_minutes() -> None:
    p = per36([row(30.0, pts=15, reb=6, ast=3), row(10.0, pts=5, reb=2, ast=1)])
    assert p.n == 2 and p.avg_minutes == 20.0
    assert p.pts36 == pytest.approx(36 * 20 / 40)
    assert p.reb36 == pytest.approx(36 * 8 / 40)
    assert p.ast36 == pytest.approx(36 * 4 / 40)
    assert p.small is True


def test_per36_with_no_games_has_no_numbers() -> None:
    p = per36([])
    assert p.n == 0 and p.pts36 is None and p.ast36 is None and p.small


def test_per36_small_flag_clears_at_ten_games() -> None:
    assert per36([row(30.0)] * (SMALL_N - 1)).small
    assert not per36([row(30.0)] * SMALL_N).small


# ----------------------------------------------------------------------------- on / off


def _hist(
    games: list[tuple[str, tuple[int, int, int, int], tuple[int, int, int, int]]],
    *,
    extra_box: dict[str, list[list[Any]]] | None = None,
    availability: dict[str, list[list[Any]]] | None = None,
    with_onoff: bool = True,
) -> History:
    """P plays for HOME in every game (date, team totals, P on-floor totals), one game each."""
    gdict: dict[str, list[Any]] = {}
    box: dict[str, list[list[Any]]] = {}
    team: dict[str, dict[str, list[int]]] = {}
    player: dict[str, dict[str, list[int]]] = {}
    for i, (d, tot, on) in enumerate(games):
        gid = f"g{i:03d}"
        gdict[gid] = [d, 2024, HOME, AWAY]
        box[gid] = [
            [P, HOME, 30.0, 20, 6, 4, 1, 0, 0, 1],
            [OPP_G, AWAY, 28.0, 12, 4, 3, 1, 1, 0, 1],
        ]
        team[gid] = {str(HOME): list(tot)}
        player[gid] = {str(P): list(on)}
    for gid, rows in (extra_box or {}).items():
        box[gid].extend(rows)
    data: dict[str, Any] = {
        "window": {"from_season": 2023, "to_date": "2024-11-30", "max_season": 2024},
        "box_columns": [],
        "games": gdict,
        "box": box,
        "teams": {str(HOME): "AAA", str(AWAY): "BBB"},
        "players": {
            str(P): {"name": "Pat Player", "pos": "Guard"},
            str(X): {"name": "Xavier Out", "pos": "Forward"},
            str(OPP_G): {"name": "Opp Guard", "pos": "Guard"},
        },
        "availability": availability or {},
        "prices": {},
    }
    if with_onoff:
        data["onoff"] = {"team": team, "player": player}
    return History(data)


def _prior(h: History, before: str = SLATE) -> list[Row]:
    return [r for r in h.logs[P] if r.date < before]


def test_onoff_matches_hand_arithmetic() -> None:
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)
    games = [(f"2024-11-0{d}", tot, on) for d in range(1, 5)]
    games.append((SLATE, (100, 999, 100, 1), (60, 999, 60, 1)))  # tonight: must not count
    games.append(("2024-11-13", (100, 999, 100, 1), (60, 999, 60, 1)))  # tomorrow
    h = _hist(games)
    o = onoff(h, P, _prior(h), 2024)
    w = o.this_season
    assert o.available and w.games == 4
    assert (w.on.off_poss, w.on.def_poss, w.on.poss) == (240, 240, 480)
    assert w.on.ortg == pytest.approx(100 * 280 / 240)
    assert w.on.drtg == pytest.approx(100 * 220 / 240)
    assert w.on.net == pytest.approx(100 * 280 / 240 - 100 * 220 / 240)  # 25.0
    # off = team minus on: (160, 160, 160, 180) over 4 games
    assert (w.off.off_poss, w.off.def_poss, w.off.poss) == (160, 160, 320)
    assert w.off.net == pytest.approx(100 * 160 / 160 - 100 * 180 / 160)  # -12.5
    assert w.diff == pytest.approx(37.5)
    assert w.small  # 4 games, fewer than 10
    assert "small sample" in w.note.lower() and "4 games" in w.note
    # every game is identical, so the interval has no width
    assert w.ci_lo == pytest.approx(37.5) and w.ci_hi == pytest.approx(37.5)


def test_last_season_is_separate_from_this_season() -> None:
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)
    h = _hist([("2024-11-01", tot, on), ("2024-11-02", tot, on)])
    h.games["g000"] = ("2023-12-01", 2023, HOME, AWAY)
    h.logs[P][0] = Row(**{**h.logs[P][0].__dict__, "date": "2023-12-01", "season": 2023})
    o = onoff(h, P, _prior(h), 2024)
    assert o.this_season.games == 1 and o.since_last_season.games == 2


def test_interval_is_close_to_a_game_cluster_bootstrap() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded test data, not a secret
    games = []
    for i in range(60):
        team = (100, 100 + rng.randint(-15, 25), 100, 100 + rng.randint(-15, 25))
        on_o = rng.randint(55, 90)
        on = (on_o, rng.randint(55, 100), on_o, rng.randint(55, 100))
        games.append((f"2024-{10 + i // 28:02d}-{i % 28 + 1:02d}", team, on))
    h = _hist(games)
    prior = h.logs[P]
    w = onoff(h, P, prior, 2024).this_season
    quads = [
        (
            h.onoff.player[r.game_id][P],  # type: ignore[union-attr]
            h.onoff.team[r.game_id][HOME],  # type: ignore[union-attr]
        )
        for r in prior
    ]

    def diff(idx: list[int]) -> float:
        def net(parts: list[tuple[int, int, int, int]]) -> float:
            o, pf, d, pa = (sum(p[k] for p in parts) for k in range(4))
            return 100 * pf / o - 100 * pa / d

        on = [quads[i][0] for i in idx]
        off = [tuple(t - o for t, o in zip(quads[i][1], quads[i][0], strict=True)) for i in idx]
        return net(on) - net([(a, b, c, d) for a, b, c, d in off])

    boot = [diff([rng.randrange(len(quads)) for _ in quads]) for _ in range(1500)]
    assert w.ci_lo is not None and w.ci_hi is not None and w.diff is not None
    half = (w.ci_hi - w.ci_lo) / 2
    assert half == pytest.approx(1.96 * statistics.stdev(boot), rel=0.3)
    assert w.ci_lo < w.diff < w.ci_hi


def test_one_game_has_no_interval_and_no_games_has_no_rate() -> None:
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)
    h = _hist([("2024-11-01", tot, on), (SLATE, tot, on)])
    w = onoff(h, P, _prior(h), 2024).this_season
    assert w.games == 1 and w.diff is not None and w.ci_lo is None and w.ci_hi is None
    none = onoff(h, P, [], 2024)
    assert not none.available and none.this_season.on.net is None and none.this_season.games == 0


def test_a_player_on_the_floor_for_every_possession_has_no_off_rate() -> None:
    tot = (100, 110, 100, 100)
    h = _hist([("2024-11-01", tot, tot), (SLATE, tot, tot)])
    w = onoff(h, P, _prior(h), 2024).this_season
    assert w.off.poss == 0 and w.off.net is None and w.diff is None and w.small


def test_small_possession_flag_clears_only_with_enough_of_both_sides() -> None:
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)  # 80 possessions a game while he sat

    def window(n_games: int) -> bool:
        h = _hist([(f"2024-10-{i + 1:02d}", tot, on) for i in range(n_games)] + [(SLATE, tot, on)])
        return onoff(h, P, _prior(h), 2024).this_season.small

    assert window(SMALL_N) is True  # ten games but only 800 possessions off
    assert window(SMALL_POSS // 80 + 1) is False
    assert window(SMALL_N - 1) is True


def test_no_onoff_table_says_so() -> None:
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)
    h = _hist([("2024-11-01", tot, on)], with_onoff=False)
    o = onoff(h, P, _prior(h), 2024)
    assert not o.available and "no play-by-play" in o.note


# ----------------------------------------------------------------------------- teammates out


def _teammate_hist() -> History:
    """15 games before the slate. X plays games 0-5 and 10-14 and sits 6-9; P scores 10 in the
    games X sat and 20 otherwise (minutes 30 always), so the per-36 numbers are checkable."""
    tot, on = (100, 110, 100, 100), (60, 70, 60, 55)
    games = [(f"2024-10-{i + 10}", tot, on) for i in range(15)] + [(SLATE, tot, on)]
    xrow = [X, HOME, 28.0, 6, 5, 4, 0, 0, 0, 1]
    extra = {f"g{i:03d}": [xrow] for i in range(15) if i not in (6, 7, 8, 9)}
    h = _hist(
        games,
        extra_box=extra,
        availability={"g015": [[X, "out", "Knee", "2024-11-12T17:00:00+00:00"]]},
    )
    # P: 10 points / 5 rebounds / 2 assists when X sat, 20 / 6 / 4 otherwise
    new: list[Row] = []
    for r in h.logs[P]:
        i = int(r.game_id[1:])
        sat = i in (6, 7, 8, 9)
        base = (10, 5, 2, 1, 0, 0) if sat else (20, 6, 4, 1, 0, 0)
        new.append(Row(**{**r.__dict__, "base": base}))
    h.logs[P] = new
    return h


GAME = {
    "game_id": "g015",
    "date": SLATE,
    "season": 2024,
    "tip_utc": TIP,
    "home": {"id": HOME, "abbr": "AAA", "city": "A", "name": "Aces"},
    "away": {"id": AWAY, "abbr": "BBB", "city": "B", "name": "Bees"},
}
PLAYER = {"player_id": P, "name": "Pat Player", "side": "home", "props": {}}


def test_per36_without_a_teammate_uses_only_games_he_sat() -> None:
    role = player_role(_teammate_hist(), GAME, PLAYER)
    assert [m.player_id for m in role.teammates_out] == [X]
    m = role.teammates_out[0]
    assert m.without.n == 4 and m.with_.n == 11
    assert m.without.avg_minutes == pytest.approx(30.0)
    assert m.without.pts36 == pytest.approx(36 * 10 / 30)
    assert m.without.reb36 == pytest.approx(36 * 5 / 30)
    assert m.without.ast36 == pytest.approx(36 * 2 / 30)
    assert m.with_.pts36 == pytest.approx(36 * 20 / 30)
    assert m.without.small and "small sample" in m.note.lower()


def test_leg_breakdown_carries_the_same_without_teammate_numbers() -> None:
    h = _teammate_hist()
    b = bd.build(h, GAME, PLAYER, "pts", 14.5, "over")
    t = b.teammates.items[0]
    assert t.per36_without.n == t.without.n == 4
    assert t.per36_without.pts36 == pytest.approx(12.0)
    assert b.steadiness.n == 15 and b.steadiness.label == "Steady"
    assert b.onoff.available and b.onoff.this_season.games == 15


def test_a_later_report_or_future_game_changes_nothing() -> None:
    h = _teammate_hist()
    base = player_role(h, GAME, PLAYER).model_dump()
    # a game the day after tonight with an absurd line, and a late injury report
    h2 = _teammate_hist()
    h2.availability["g015"].append([X, "available", "Knee", "2024-11-13T05:00:00+00:00"])
    assert player_role(h2, GAME, PLAYER).model_dump() == base


# ----------------------------------------------------------------------------- the real fixtures


@pytest.fixture(scope="module")
def store() -> Store:
    return Store()


def test_fixture_onoff_is_pre_2025_and_adds_up(store: Store) -> None:
    raw = json.loads((FIXTURES / "onoff.json").read_text())
    assert raw["window"]["max_season"] == 2024
    assert store.history is not None and store.history.onoff is not None
    h = store.history
    assert set(raw["team"]) <= set(h.games)
    assert all(h.games[g][1] <= 2024 for g in raw["team"])
    # each team's points for are the other team's points against in the same game
    for gid, teams in raw["team"].items():
        (a, qa), (b, qb) = teams.items()
        assert qa[0] == qb[2] and qa[2] == qb[0] and qa[1] == qb[3] and qa[3] == qb[1], gid
    # a player on the floor cannot have more possessions than his team
    for gid, players in raw["player"].items():
        for pid, q in players.items():
            tid = next(r.team for r in h.logs[int(pid)] if r.game_id == gid)
            t = raw["team"][gid][str(tid)]
            assert q[0] <= t[0] and q[2] <= t[2] and q[1] <= t[1] and q[3] <= t[3]


def test_game_page_players_all_carry_a_role_and_it_recomputes(
    client: TestClient, store: Store
) -> None:
    assert store.history is not None
    raw = json.loads((FIXTURES / "onoff.json").read_text())
    checked = 0
    for date_, slate in store.slates.items():
        for g in slate["games"][:2]:
            body = client.get(f"/api/games/{g['game_id']}").json()
            assert body["players"], g["game_id"]
            for pl in body["players"]:
                role = pl["role"]
                st = role["steadiness"]
                prior = [r for r in store.history.logs[pl["player_id"]] if r.date < date_]
                assert st["n"] == min(len(prior), STEADY_WINDOW)
                assert st["label"] in {"Steady", "Mixed", "Limited data"}
                if st["n"] < STEADY_MIN_GAMES:
                    assert st["label"] == "Limited data"
                # on/off recomputed straight from the raw fixture file, strictly before the slate
                own = [0, 0, 0, 0]
                tot = [0, 0, 0, 0]
                n_games = 0
                for r in prior:
                    mine = raw["player"].get(r.game_id, {}).get(str(pl["player_id"]))
                    team = raw["team"].get(r.game_id, {}).get(str(r.team))
                    if mine is None or team is None:
                        continue
                    n_games += 1
                    own = [a + b for a, b in zip(own, mine, strict=True)]
                    tot = [a + b for a, b in zip(tot, team, strict=True)]
                w = role["onoff"]["since_last_season"]
                assert w["games"] == n_games
                assert w["on"]["off_poss"] == own[0] and w["on"]["def_poss"] == own[2]
                assert w["off"]["off_poss"] == tot[0] - own[0]
                if own[0] and own[2]:
                    assert w["on"]["net"] == pytest.approx(
                        100 * own[1] / own[0] - 100 * own[3] / own[2]
                    )
                checked += 1
    assert checked > 20


def test_leg_breakdown_for_a_real_player_has_steadiness_and_onoff(
    client: TestClient, store: Store
) -> None:
    g = next(iter(store.slates.values()))["games"][0]
    pl = g["players"][0]
    b = client.get(f"/api/breakdown/{g['game_id']}/{pl['player_id']}/pts").json()
    assert b["steadiness"]["n"] > 0 and b["onoff"]["available"]
    assert b["onoff"]["since_last_season"]["on"]["poss"] > 0
    assert "either side" in b["steadiness"]["say"] or b["steadiness"]["label"] == "Limited data"


def test_export_has_steadiness_but_says_it_holds_no_onoff(export_client: TestClient) -> None:
    day = export_client.get("/api/slates/2024-11-12").json()
    gid = day["games"][0]["game_id"]
    players = export_client.get(f"/api/games/{gid}").json()["players"]
    assert players
    for pl in players:
        assert pl["role"]["steadiness"]["label"] in {"Steady", "Mixed", "Limited data"}
        assert pl["role"]["onoff"]["available"] is False
        assert "no play-by-play" in pl["role"]["onoff"]["note"]
