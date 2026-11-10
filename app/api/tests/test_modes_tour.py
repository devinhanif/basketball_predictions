"""Phase B: the track record (calibration, guess deck), the tip-off timeline, the uncertainty map
and the minutes facts behind the Explore lens. Fixtures and the small synthetic export only; all
dates are 2024-25 or earlier.
"""

from __future__ import annotations

import json
import random
from typing import Any

from fastapi.testclient import TestClient

from parlayhitters_api.knows import news_items
from parlayhitters_api.main import get_store
from parlayhitters_api.minutes import moments
from parlayhitters_api.trackrecord import MIN_GAMES, MIN_N, Case, binned


def _rng() -> random.Random:
    return random.Random(1)  # noqa: S311 - a reproducible bootstrap in a test


def _keys(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            out |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys(v)
    return out


def _no_prices(payload: Any) -> None:
    """Learn and Explore payloads carry no price, expected value, vig or fee field."""
    bad = {
        k
        for k in _keys(payload)
        if "price" in k.lower() or k.lower() in {"ev", "ev_low", "vig", "fee", "fees", "edge"}
    }
    assert not bad, bad


# ------------------------------------------------------------------- slate list


def test_slate_list_counts_finished_games(client: TestClient) -> None:
    rows = client.get("/api/slates").json()
    assert rows
    for r in rows:
        assert r["finished"] == r["games"]  # the fixtures are two played nights


def test_slate_list_on_an_export_separates_played_from_tonight(export_client: TestClient) -> None:
    rows = {r["date"]: r for r in export_client.get("/api/slates").json()}
    assert rows["2024-11-12"]["games"] == 2
    assert rows["2024-11-12"]["finished"] == 0  # tonight: nothing to reveal yet
    assert rows["2024-11-11"]["finished"] == rows["2024-11-11"]["games"] == 2


# ------------------------------------------------------------------- calibration


def test_calibration_bins_add_up_and_carry_n(export_client: TestClient) -> None:
    d = export_client.get("/api/track-record/calibration").json()
    assert d["mode"] == "export"
    assert d["date_to"] <= "2024-11-11"  # tonight's unplayed games are not settled data
    for name in ("props", "winners"):
        s = d[name]
        assert sum(b["n"] for b in s["bins"]) == s["n"] == s["overall"]["n"]
        assert s["interval"].startswith("95%")
        for b in s["bins"]:
            assert b["enough"] == (b["n"] >= MIN_N and b["n_games"] >= MIN_GAMES)
            if b["enough"]:
                assert 0 <= b["lo95"] <= b["hi95"] <= 1
            else:
                assert b["lo95"] is None and b["hi95"] is None
    assert d["winners"]["n_games"] == d["winners"]["n"]  # one case per game


def test_calibration_is_deterministic_and_has_no_prices(export_client: TestClient) -> None:
    a = export_client.get("/api/track-record/calibration").json()
    b = export_client.get("/api/track-record/calibration").json()
    assert a == b
    _no_prices(a)


def test_calibration_counts_what_it_leaves_out(export_client: TestClient) -> None:
    d = export_client.get("/api/track-record/calibration").json()
    ex = d["props"]["excluded"]
    assert set(ex) == {"no_posted_line", "whole_number_line", "did_not_play"}
    store = get_store()
    scored = sum(
        1
        for s in store.slates.values()
        for g in s["games"]
        if g.get("result") is not None
        for pl in g["players"]
        for pr in pl["props"].values()
    )
    assert d["props"]["n"] + sum(ex.values()) == scored


def test_calibration_works_on_the_fixtures(client: TestClient) -> None:
    d = client.get("/api/track-record/calibration").json()
    assert d["mode"] == "fixture_replay"
    assert d["props"]["n"] > 0
    assert d["n_games_finished"] > 0


def test_an_interval_from_whole_games_is_wider_than_one_from_independent_cases() -> None:
    # 12 games, 5 cases each, every case in a game does the same thing: half the games happen.
    cases = [Case(f"g{g}", 0.5, g % 2 == 0) for g in range(12) for _ in range(5)]
    (b,) = binned(cases, [0.0, 1.0], _rng())
    assert b.n == 60 and b.n_games == 12 and b.enough
    assert b.lo95 is not None and b.hi95 is not None
    iid_half_width = 1.96 * (0.25 / 60) ** 0.5  # what 60 independent cases would suggest
    assert (b.hi95 - b.lo95) / 2 > 1.8 * iid_half_width


def test_a_thin_bin_gets_no_interval() -> None:
    cases = [Case(f"g{g}", 0.55, True) for g in range(MIN_N - 1)]
    (b,) = binned(cases, [0.0, 1.0], _rng())
    assert not b.enough and b.lo95 is None and b.said == 0.55 and b.happened == 1.0


def test_few_games_is_not_enough_even_with_many_cases() -> None:
    cases = [Case(f"g{g % (MIN_GAMES - 1)}", 0.55, True) for g in range(200)]
    (b,) = binned(cases, [0.0, 1.0], _rng())
    assert b.n == 200 and not b.enough


def test_an_empty_bin_says_nothing() -> None:
    bins = binned([], [0.0, 0.5, 1.0], _rng())
    assert [b.n for b in bins] == [0, 0] and bins[0].said is None and bins[0].happened is None


# ------------------------------------------------------------------- guess deck


def test_the_deck_hides_the_outcome_and_the_forecast(client: TestClient) -> None:
    d = client.get("/api/track-record/guess?seed=4&n=6").json()
    assert len(d["cards"]) == 6 and d["reason"] == "ok"
    banned = {"actual", "model_p_over", "model_call", "went_over", "p_over", "mean", "q"}
    assert not (_keys(d) & banned)
    _no_prices(d)
    for c in d["cards"]:
        assert c["line"] % 1 == 0.5 and c["stat"] in {"pts", "reb", "ast", "fg3m"}


def test_the_deck_is_the_same_for_the_same_seed_and_differs_for_another(
    client: TestClient,
) -> None:
    a = client.get("/api/track-record/guess?seed=4&n=8").json()
    b = client.get("/api/track-record/guess?seed=4&n=8").json()
    c = client.get("/api/track-record/guess?seed=5&n=8").json()
    assert a == b
    assert [x["prop_id"] for x in a["cards"]] != [x["prop_id"] for x in c["cards"]]


def test_the_reveal_matches_the_stored_result_and_the_model_number(client: TestClient) -> None:
    store = get_store()
    deck = client.get("/api/track-record/guess?seed=2&n=10").json()
    for card in deck["cards"]:
        gid, pid, stat = card["prop_id"].split(":")
        r = client.get(f"/api/track-record/guess/{gid}/{pid}/{stat}").json()
        g = store.game(gid)
        assert g is not None
        pl = next(p for p in g["players"] if p["player_id"] == int(pid))
        assert r["actual"] == pl["props"][stat]["actual"]
        assert r["went_over"] == (r["actual"] > card["line"])
        assert r["model_call"] == ("over" if r["model_p_over"] >= 0.5 else "under")
        assert r["model_right"] == ((r["model_call"] == "over") == r["went_over"])
        assert 0 <= r["model_p_over"] <= 1


def test_the_reveal_refuses_a_game_that_has_not_been_played(
    export_client: TestClient,
) -> None:
    store = get_store()
    g = store.slate("2024-11-12")["games"][0]  # type: ignore[index]
    pl = g["players"][0]
    stat = next(iter(pl["props"]))
    assert (
        export_client.get(
            f"/api/track-record/guess/{g['game_id']}/{pl['player_id']}/{stat}"
        ).status_code
        == 404
    )
    assert export_client.get("/api/track-record/guess/nope/1/pts").status_code == 404


def test_the_deck_on_the_export_only_holds_played_games(export_client: TestClient) -> None:
    d = export_client.get("/api/track-record/guess?seed=1&n=30").json()
    store = get_store()
    for c in d["cards"]:
        assert store.game(c["game_id"]).get("result") is not None  # type: ignore[union-attr]
        assert c["date"] <= "2024-11-11"


def test_a_deck_with_no_posted_lines_says_so(v1_client: TestClient) -> None:
    d = v1_client.get("/api/track-record/guess?seed=1&n=5").json()
    assert d["cards"] == [] and d["reason"] == "no_props" and "nothing to guess" in d["note"]


def test_deck_size_is_bounded(client: TestClient) -> None:
    assert client.get("/api/track-record/guess?n=500").status_code == 422
    assert client.get("/api/track-record/guess?seed=-1").status_code == 422


# ------------------------------------------------------------------- the timeline


def _two_snapshot_prop() -> tuple[str, int, str]:
    store = get_store()
    for key, snaps in store.snapshots.items():
        g = store.game(key[0])
        if (
            len(snaps) >= 2
            and g is not None
            and any(p["player_id"] == key[1] for p in g["players"])
        ):
            return key
    raise AssertionError("the small export holds no prop with two snapshots")


def test_a_prop_with_two_forecasts_gets_a_timeline_in_order(export_client: TestClient) -> None:
    gid, pid, stat = _two_snapshot_prop()
    d = export_client.get(f"/api/knows/{gid}/{pid}/{stat}").json()
    snaps = d["snapshots"]
    assert len(snaps) >= 2
    assert [s["made_at"] for s in snaps] == sorted(s["made_at"] for s in snaps)
    assert sum(1 for s in snaps if s["shown"]) == 1
    assert all(s["p10"] <= s["p50"] <= s["p90"] and 0 <= s["p_over"] <= 1 for s in snaps)
    assert all(s["lead_minutes"] > 0 for s in snaps)  # nothing after tip
    _no_prices(d)


def test_the_shown_snapshot_is_the_forecast_the_game_page_shows(
    export_client: TestClient,
) -> None:
    gid, pid, stat = _two_snapshot_prop()
    d = export_client.get(f"/api/knows/{gid}/{pid}/{stat}").json()
    page = export_client.get(f"/api/games/{gid}").json()
    prop = next(p for p in page["players"] if p["player_id"] == pid)["props"][stat]
    shown = next(s for s in d["snapshots"] if s["shown"])
    assert abs(shown["p_over"] - prop["model_p_over"]) < 1e-9
    assert abs(shown["p50"] - prop["q"][9]) < 1e-9


def test_news_lists_changes_and_marks_first_lines(export_client: TestClient) -> None:
    store = get_store()
    gid = next(iter(store.injury))
    d = export_client.get(f"/api/knows/{gid}").json()
    assert d["n_reports"] >= 1
    for n in d["news"]:
        assert n["status"] in {"out", "doubtful", "questionable", "probable", "available"}
        assert (n["previous"] is None) == n["first_report"]
        assert n["at_utc"] <= d["tip_utc"].replace("Z", "+00:00") or n["lead_minutes"] is not None


def test_a_status_change_is_news_and_a_repeat_is_not() -> None:
    class Fake:
        names = {1: "A Player", 2: "B Player"}
        injury = {
            "g": [
                {"at_utc": "2024-11-12T17:00:00+00:00", "player_id": 1, "team_id": 10,
                 "status": "questionable", "reason": "Injury/Illness-LeftAnkleSprain"},
                {"at_utc": "2024-11-12T17:00:00+00:00", "player_id": 2, "team_id": 10,
                 "status": "available", "reason": None},
                {"at_utc": "2024-11-12T20:00:00+00:00", "player_id": 1, "team_id": 10,
                 "status": "questionable", "reason": "Injury/Illness-LeftAnkleSprain"},
                {"at_utc": "2024-11-12T20:00:00+00:00", "player_id": 2, "team_id": 10,
                 "status": "out", "reason": "Injury/Illness-Illness"},
                {"at_utc": "2024-11-12T21:00:00+00:00", "player_id": 1, "team_id": 10,
                 "status": "out", "reason": "Injury/Illness-LeftAnkleSprain"},
            ]
        }  # fmt: skip

    game = {
        "game_id": "g",
        "tip_utc": "2024-11-13T00:00:00Z",
        "home": {"id": 10, "abbr": "AAA"},
        "away": {"id": 11, "abbr": "BBB"},
    }
    items = news_items(Fake(), game, 10, 1)
    got = [(n.player_id, n.previous, n.status, n.first_report, n.himself) for n in items]
    assert got == [
        (1, None, "questionable", True, True),  # first line, not available: listed
        (2, "available", "out", False, False),  # available, then out: news
        (1, "questionable", "out", False, True),  # changed
    ]  # the repeated questionable and the first "available" are not news
    assert items[0].reason == "Injury/illness, left ankle sprain"
    assert items[0].lead_minutes == 420.0
    assert news_items(Fake(), game, 11, None) == []  # another team's report


def test_the_game_timeline_lists_biggest_movers_first(export_client: TestClient) -> None:
    store = get_store()
    gids = {k[0] for k, v in store.snapshots.items() if len(v) >= 2 and k[2] == "pts"}
    assert gids
    for gid in gids:
        d = export_client.get(f"/api/knows/{gid}").json()
        deltas = [abs(m["delta"]) for m in d["movers"]]
        assert deltas == sorted(deltas, reverse=True)
        for m in d["movers"]:
            assert m["n_snapshots"] >= 2 and m["first_at"] < m["last_at"]


def test_fixtures_say_there_is_no_timeline(client: TestClient) -> None:
    g = client.get("/api/slates").json()[0]
    game = client.get(f"/api/slates/{g['date']}").json()["games"][0]
    kp = game["key_props"][0]
    d = client.get(f"/api/knows/{game['game_id']}/{kp['player_id']}/pts").json()
    assert d["snapshots"] == [] and d["news"] == []
    assert "no timeline" in d["note"]
    gk = client.get(f"/api/knows/{game['game_id']}").json()
    assert gk["movers"] == [] and gk["news"] == [] and "no earlier forecasts" in gk["note"]


def test_the_timeline_404s_cleanly(client: TestClient) -> None:
    assert client.get("/api/knows/nope").status_code == 404
    g = client.get("/api/slates").json()[0]
    game = client.get(f"/api/slates/{g['date']}").json()["games"][0]
    assert client.get(f"/api/knows/{game['game_id']}/1/pts").status_code == 404
    assert client.get(f"/api/knows/{game['game_id']}/1/blk").status_code == 404


# ------------------------------------------------------------------- uncertainty and minutes


def test_the_uncertainty_notes_use_only_data_fields(export_client: TestClient) -> None:
    gid, pid, stat = _two_snapshot_prop()
    u = export_client.get(f"/api/knows/{gid}/{pid}/{stat}").json()["uncertainty"]
    keys = [i["key"] for i in u["items"]]
    assert keys[0] == "range"
    assert set(keys) <= {"range", "minutes", "share", "play", "role", "opponent", "teammates"}
    assert u["p10"] <= u["p90"]
    text = " ".join(i["text"] for i in u["items"])
    assert "guarantee" not in text and "lock" not in text.lower()


def test_minutes_share_is_between_zero_and_one_and_the_range_narrows(
    client: TestClient,
) -> None:
    g = client.get("/api/slates").json()[0]
    game = client.get(f"/api/slates/{g['date']}").json()["games"][0]
    seen = 0
    for kp in game["key_props"]:
        m = client.get(f"/api/card/{game['game_id']}/{kp['player_id']}/pts").json()["minutes"]
        assert m["recent_n"] >= 0 and "fixed" in m["method"]
        if m["minutes_share"] is not None:
            seen += 1
            assert 0 <= m["minutes_share"] <= 1
            lo, hi = m["range_now"]
            klo, khi = m["range_minutes_known"]
            assert klo >= lo - 1e-9 and khi <= hi + 1e-9
            assert m["recent_n"] >= 10
        else:
            assert m["range_minutes_known"] is None
        if m["proj_minutes"] is None:
            assert m["baseline_source"] in {"recent", None}  # fixtures hold no projection
    assert seen >= 1


def test_the_export_card_starts_from_the_models_projected_minutes(
    export_client: TestClient,
) -> None:
    store = get_store()
    gid, pid, stat = _two_snapshot_prop()
    m = export_client.get(f"/api/card/{gid}/{pid}/{stat}").json()["minutes"]
    g = store.game(gid)
    pl = next(p for p in g["players"] if p["player_id"] == pid)  # type: ignore[index]
    assert m["proj_minutes"] == pl["props"][stat]["forecast"]["proj_minutes"]
    assert m["baseline_source"] == "model" and m["baseline"] == m["proj_minutes"]


def test_moments_of_a_known_count() -> None:
    # Y is 0, 1, 2 or 3 with probability 0.1, 0.2, 0.3, 0.4: P(Y >= k) = 1, .9, .7, .4
    mean, sd = moments([1.0, 0.9, 0.7, 0.4])
    assert abs(mean - 2.0) < 1e-12
    assert abs(sd - 1.0) < 1e-12
    assert json.dumps([mean, sd])
