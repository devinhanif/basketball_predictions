"""Payload: as-of rule, no market prices, determinism, input cap."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from nba.facts import query as facts_query
from nba.shadow_llm import payload as pl_mod
from nba.shadow_llm.payload import (
    AsOfViolation,
    PayloadRefused,
    assert_as_of,
    build_payload,
    payload_sha256,
)
from tests.shadow_llm.conftest import GAME, NOW, TIP, World


def test_payload_content_and_asof(world: World) -> None:
    p = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    legs = {(L["player_id"], L["stat"]): L for L in p["legs"]}
    # DK main line only (alt 24.5 is not the main); integer line, no-production player dropped
    assert legs[(11, "pts")]["line"] == 20.5 and legs[(11, "pts")]["pinnacle_line"] == 21.5
    assert (13, "ast") not in legs and not any(k[0] == 22 for k in legs)
    for L in p["legs"]:
        assert 0.0 <= L["p_over_prod"] <= 1.0 and L["line"] % 1 == 0.5
        assert {"player_id", "stat", "line", "p_over_prod", "mean_prod"} <= set(L)
    # exact survival: line 20.5, mean 18 sd 6 -> P(Y >= 21)
    assert abs(legs[(11, "pts")]["p_over_prod"] - 0.3325) < 0.02
    # facts: only stamps at or before t; planted post-t and post-tip rows absent
    assert p["facts"] and all(f["known_at"] <= NOW.isoformat() for f in p["facts"])
    states = [f.get("value_text") for f in p["facts"] if f["predicate"] == "listed_status"]
    assert "out" not in states and not any(f["predicate"] == "played_minutes" for f in p["facts"])
    assert assert_as_of(p, NOW) > 5
    # box lines strictly before the game date (ET 2026-10-28), all season 2024 here
    for pl_ in p["players"].values():
        assert pl_["last10"] and all(r[0] < "2026-10-28" for r in pl_["last10"])
        assert len(pl_["last10"]) == 10
    # game market POINTS only
    assert {tuple(r[:3]) for r in p["game_lines"]["rows"]} == {
        ("draftkings", "spreads", "home"),
        ("draftkings", "totals", "over"),
    }


def test_no_prices_or_implied_probabilities_are_sent(world: World) -> None:
    text = json.dumps(build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)).lower()
    for banned in ("price", "american", "devig", "implied", "-110", "over_price", "under_price"):
        assert banned not in text


def test_deterministic_ordering_and_sha(world: World) -> None:
    a = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    b = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    assert payload_sha256(a) == payload_sha256(b) and len(payload_sha256(a)) == 64
    keys = [(L["player_id"], L["stat"]) for L in a["legs"]]
    assert keys == sorted(keys, key=lambda k: (k[0], ("pts", "reb", "ast", "fg3m").index(k[1])))


@pytest.mark.parametrize("minutes_to_tip", [59, 30, 1, -1])
def test_request_after_tip_minus_60_is_refused(world: World, minutes_to_tip: int) -> None:
    with pytest.raises(AsOfViolation):
        build_payload(
            GAME, now=TIP - timedelta(minutes=minutes_to_tip), paths=world.paths, cfg=world.cfg
        )


def test_exactly_tip_minus_60_is_allowed(world: World) -> None:
    # the T-60 lines were received at NOW+30s <= tip-60 (NOW = tip-65): allowed boundary
    build_payload(GAME, now=TIP - timedelta(minutes=60), paths=world.paths, cfg=world.cfg)


def test_planted_post_tip_fact_in_store_output_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: even if the fact store returned a post-t row, the payload refuses."""
    real = facts_query.as_of

    def leaky(con, game_id, t, **kw):  # type: ignore[no-untyped-def]
        df = real(con, game_id, t, **kw)
        row = df.to_dicts()[0] | {
            "fact_id": 999, "subject": "player:11", "predicate": "listed_status",
            "known_at": TIP + timedelta(minutes=5),
        }  # fmt: skip
        return pl.DataFrame([*df.to_dicts(), row], schema=df.schema)

    monkeypatch.setattr(pl_mod.facts_query, "as_of", leaky)
    with pytest.raises(AsOfViolation):
        build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)


def test_assert_as_of_walks_nested_stamps() -> None:
    ok = {"a": {"known_at": "2026-10-28T20:00:00"}, "b": [{"known_at": "2026-10-28T21:00:00"}]}
    assert assert_as_of(ok, NOW) == 2
    with pytest.raises(AsOfViolation):
        assert_as_of({"x": [{"known_at": (NOW + timedelta(seconds=1)).isoformat()}]}, NOW)
    with pytest.raises(AsOfViolation):
        assert_as_of({"columns": ["k", "known_at"], "rows": [["a", (TIP).isoformat()]]}, NOW)


def test_missing_production_rows_or_lines_refuse(world: World) -> None:
    with pytest.raises(PayloadRefused) as e:
        build_payload(GAME, now=NOW - timedelta(hours=3), paths=world.paths, cfg=world.cfg)
    assert e.value.code == "no_production_rows"
    no_odds = dict(world.paths, market=world.tmp / "absent.duckdb")
    with pytest.raises(PayloadRefused) as e2:
        build_payload(GAME, now=NOW, paths=no_odds, cfg=world.cfg)
    assert e2.value.code == "no_legs"


def test_input_cap_trims_legs_lowest_minutes_first(world: World) -> None:
    full = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    cfg = replace(world.cfg, input_token_cap=int(len(json.dumps(full)) / 3.0 * 0.8))
    try:
        small = build_payload(GAME, now=NOW, paths=world.paths, cfg=cfg)
    except PayloadRefused as exc:
        assert exc.code == "input_cap"
        return
    assert len(small["legs"]) < len(full["legs"])
    assert small["game"]["n_legs_dropped_for_input_cap"] > 0


def _report(p: dict) -> list[dict]:  # type: ignore[type-arg]
    return [f for f in p["facts"] if f["predicate"] == "injury_report_status"]


def test_same_day_injury_report_latest_snapshot_for_both_teams(world: World) -> None:
    p = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    rep = _report(p)
    # latest snapshot at or before t is the 17:00 ET one: only players 12 and 13
    assert {(r["player_id"], r["status"]) for r in rep} == {(12, "Out"), (13, "Available")}
    out12 = next(r for r in rep if r["player_id"] == 12)
    assert out12["reason"] == "Right Ankle; Sprain" and out12["team"] == 1610612738
    # 17:00 ET == 21:00 UTC
    assert out12["known_at"] == "2026-10-28T21:00:00" and out12["known_at"] <= NOW.isoformat()
    # planted rows (as_of after t, after tip) never appear; earlier snapshot rows are superseded
    assert not any("PLANTED" in (r["reason"] or "") for r in rep)
    assert not any(r["player_id"] in (11, 21) for r in rep)
    assert not any(f.get("value_text") == "no_report" for f in p["facts"])


def test_game_without_a_report_gets_an_explicit_no_report_fact(world: World) -> None:
    import duckdb

    con = duckdb.connect(str(world.paths["forward"]))
    con.execute("DELETE FROM player_availability")
    con.close()
    p = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    nr = [f for f in p["facts"] if f.get("value_text") == "no_report"]
    assert len(nr) == 1 and nr[0]["predicate"] == "injury_report" and not _report(p)


def test_reporter_facts_never_reach_the_payload(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planted fact: beat-reporter rows in the as-of set (known before t) are dropped."""
    real = facts_query.as_of
    planted = "Alperen Sengun will be on a 24-minute restriction tonight."

    def with_reporters(con, game_id, t, **kw):  # type: ignore[no-untyped-def]
        df = real(con, game_id, t, **kw)
        base = df.to_dicts()[0]
        rows = [
            base
            | {"fact_id": 900 + i, "subject": "player:11", "predicate": pred,
               "value_text": planted, "source": "x:RocketsBeat",
               "known_at": NOW - timedelta(minutes=30)}
            for i, pred in enumerate(["claimed_minutes_restriction", "reporter_minutes_cap"])
        ]  # fmt: skip
        return pl.DataFrame([*df.to_dicts(), *rows], schema=df.schema)

    monkeypatch.setattr(pl_mod.facts_query, "as_of", with_reporters)
    p = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    assert p["facts"]
    assert not any(f["predicate"].startswith(("claimed_", "reporter_")) for f in p["facts"])
    assert planted not in json.dumps(p) and "x:RocketsBeat" not in json.dumps(p)
