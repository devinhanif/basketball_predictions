"""Phase B: the track record (calibration, guess deck), the tip-off timeline, the uncertainty map
and the minutes facts behind the Explore lens. Fixtures and the small synthetic export only; all
dates are 2024-25 or earlier.
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.knows import news_items
from parlayhitters_api.main import get_store
from parlayhitters_api.minutes import moments
from parlayhitters_api.store import load_store
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
    s = d["winners"]
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


def test_no_season_prop_calibration_is_served(
    client: TestClient, export_client: TestClient
) -> None:
    """LINE_CAL_LIVE forbids interim looks at calibrated outcomes: the prop block is the published
    2024-25 record (T493) and a note, identical whatever games the store holds, with no bin,
    count or interval computed from this season's props."""
    a = client.get("/api/track-record/calibration").json()["props"]
    b = export_client.get("/api/track-record/calibration").json()["props"]
    assert a == b  # nothing in it depends on the loaded games
    assert set(a) == {
        "ledger_id", "season_label", "rule_sha8", "verdict", "source", "books", "interval",
        "this_season",
    }  # fmt: skip
    assert a["ledger_id"] == "T493" and a["season_label"] == "2024-25"
    assert "locked test" in a["this_season"] and "Dec 1" in a["this_season"]
    for book in a["books"].values():
        assert book["n"] > 1000 and book["games"] > 100
        assert book["log_loss_cal_minus_raw"]["hi"] < 0
        assert sum(x["n"] for x in book["bins"]) == book["n"]
        assert all(set(x) == {"lo", "n", "said", "happened"} for x in book["bins"])
    # and the settling endpoint pairs no model chance with an outcome
    res = export_client.get("/api/slates/2024-11-11/results").json()
    assert res["rows"] and not any(k.startswith("model") for r in res["rows"] for k in r)


def test_calibration_works_on_the_fixtures(client: TestClient) -> None:
    d = client.get("/api/track-record/calibration").json()
    assert d["mode"] == "fixture_replay"
    assert d["winners"]["n"] > 0
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
        assert "model_right" not in r
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


# ------------------------------------------------------------------- known-at guards


def _fake_game() -> dict[str, Any]:
    return {
        "game_id": "g",
        "tip_utc": "2024-11-13T00:00:00Z",
        "home": {"id": 10, "abbr": "AAA"},
        "away": {"id": 11, "abbr": "BBB"},
        "players": [{"player_id": 1, "side": "home", "name": "A", "props": {}}],
    }


def _snap_row(made: str, lead: float, shown: bool) -> dict[str, Any]:
    return {
        "made_at": made, "lead_minutes": lead, "mean": 10.0, "q": [float(i) for i in range(19)],
        "proj_minutes": 30.0, "p_play": 0.9, "surv": None, "run_id": "r", "shown": shown,
    }  # fmt: skip


def test_rows_at_or_after_tip_are_dropped_and_counted() -> None:
    from parlayhitters_api.knows import pre_tip_reports, pre_tip_snapshots  # noqa: PLC0415

    class Fake:
        names: dict[int, str] = {}
        injury = {
            "g": [
                {
                    "at_utc": "2024-11-12T20:00:00+00:00",
                    "player_id": 1,
                    "team_id": 10,
                    "status": "out",
                    "reason": None,
                },
                {
                    "at_utc": "2024-11-13T00:00:00+00:00",
                    "player_id": 2,
                    "team_id": 10,
                    "status": "out",
                    "reason": None,
                },  # exactly at tip
                {
                    "at_utc": "2024-11-13T01:00:00+00:00",
                    "player_id": 3,
                    "team_id": 10,
                    "status": "out",
                    "reason": None,
                },  # after tip
                {
                    "at_utc": "not a time",
                    "player_id": 4,
                    "team_id": 10,
                    "status": "out",
                    "reason": None,
                },
            ]
        }
        snapshots = {
            ("g", 1, "pts"): [
                _snap_row("2024-11-12T18:00:00Z", 360.0, False),
                _snap_row("2024-11-13T00:05:00Z", -5.0, True),
                _snap_row("2024-11-13T00:00:00Z", 0.0, False),
            ]
        }

    rows, dropped = pre_tip_reports(Fake(), _fake_game())
    assert [r["player_id"] for r in rows] == [1] and dropped == 3
    assert [n.player_id for n in news_items(Fake(), _fake_game(), None, None)] == [1]
    snaps, sdropped = pre_tip_snapshots(Fake(), ("g", 1, "pts"))
    assert len(snaps) == 1 and sdropped == 2


def _teammate_text(c: TestClient, shift_minutes: float) -> tuple[list[str], dict[str, Any]]:
    """Plant one teammate listed out on a report stamped `shift_minutes` after the shown forecast
    was made (negative: before), then read the "why" notes for that prop."""
    from datetime import datetime, timedelta  # noqa: PLC0415

    store = get_store()
    gid, pid, stat = _two_snapshot_prop()
    g = store.game(gid)
    assert g is not None
    shown = next(s for s in store.snapshots[(gid, pid, stat)] if s["shown"])
    side = next(p for p in g["players"] if p["player_id"] == pid)["side"]
    tid = g["home"]["id"] if side == "home" else g["away"]["id"]
    made = datetime.fromisoformat(shown["made_at"].replace("Z", "+00:00"))
    at = (made + timedelta(minutes=shift_minutes)).isoformat()
    tip = datetime.fromisoformat(g["tip_utc"].replace("Z", "+00:00"))
    assert datetime.fromisoformat(at) < tip  # a pre-tip report either way
    store.injury[gid] = [
        {"at_utc": at, "player_id": 424242, "team_id": tid, "status": "out", "reason": None}
    ]
    d = c.get(f"/api/knows/{gid}/{pid}/{stat}").json()
    return [i["text"] for i in d["uncertainty"]["items"] if i["key"] == "teammates"], d


def test_a_report_published_after_the_forecast_is_not_cited_as_why(
    export_client: TestClient,
) -> None:
    texts, d = _teammate_text(export_client, +5.0)
    assert len(texts) == 1 and "published after this forecast" in texts[0]
    assert "Unnamed" not in texts[0] and "listed out" not in texts[0]
    assert d["n_reports"] == 1 and "1 injury report for this game" in d["note"]


def test_a_report_published_before_the_forecast_is_cited(export_client: TestClient) -> None:
    texts, _ = _teammate_text(export_client, -60.0)
    assert len(texts) == 1 and "before this forecast" in texts[0] and "1 teammate" in texts[0]


def test_a_zero_minutes_projection_is_no_projection() -> None:
    from parlayhitters_api.minutes import minutes_info  # noqa: PLC0415

    q = [float(i) for i in range(19)]
    for proj in (0.0, -3.0, None):
        m = minutes_info({"q": q, "mean": 9.0, "proj_minutes": proj}, None)
        assert m.proj_minutes is None and m.baseline is None and m.baseline_source is None
    m = minutes_info({"q": q, "mean": 9.0, "proj_minutes": 31.0}, None)
    assert m.baseline == 31.0 and m.baseline_source == "model"


# ------------------------------------------------------------------- model source


def _check_sources(d: dict[str, Any]) -> None:
    s = d["winners"]
    assert s["sources"]
    assert sum(r["n"] for r in s["sources"]) == s["n"]
    assert all(r["label"] and r["overall"]["n"] == r["n"] for r in s["sources"])


def test_calibration_says_the_model_source_on_the_fixtures(client: TestClient) -> None:
    d = client.get("/api/track-record/calibration").json()
    _check_sources(d)


def test_calibration_says_the_model_source_on_an_export(export_client: TestClient) -> None:
    d = export_client.get("/api/track-record/calibration").json()
    _check_sources(d)


# ------------------------------------------------------------------- a cold start


def test_requests_that_arrive_together_on_a_cold_start_load_the_data_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from parlayhitters_api import main as m

    calls: list[int] = []
    real = load_store

    def slow() -> Any:
        calls.append(1)
        time.sleep(0.2)
        return real()

    m.reset_store()
    monkeypatch.setattr("parlayhitters_api.main.load_store", slow)
    got: list[Any] = []
    threads = [threading.Thread(target=lambda: got.append(m.get_store())) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len({id(s) for s in got}) == 1
    m.reset_store()
