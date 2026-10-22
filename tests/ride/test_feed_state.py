from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nba.ride.feed_state import (
    Event,
    classify_play,
    parse_state,
    possession_from_plays,
    substitution,
    timeout_team,
    update_possession,
)

FIX = Path(__file__).parent / "fixtures" / "espn_summary_401909092_q3.json"
H, A = "13", "9"  # LAL home, GS away in the fixture


def play(
    text: str, team: str = "", scoring: bool = False, shooting: bool = False
) -> dict[str, Any]:
    return {
        "type": {"text": text},
        "text": text.lower(),
        "team": {"id": team} if team else None,
        "scoringPlay": scoring,
        "shootingPlay": shooting,
    }


def test_classify_core_kinds() -> None:
    assert classify_play(play("Jump Shot", H, True, True)) == Event("made_fg", H, made=True)
    assert classify_play(play("Jump Shot", H, False, True)).kind == "miss_fg"
    assert classify_play(play("Defensive Rebound", A)).kind == "dreb"
    assert classify_play(play("Offensive Rebound", A)).kind == "oreb"
    assert classify_play(play("Bad Pass\nTurnover", H)).kind == "tov"
    assert classify_play(play("Traveling", H)).kind == "tov"
    assert classify_play(play("Shooting Foul", H)).kind == "foul"
    assert classify_play(play("Substitution", H, shooting=True)).kind == "other"
    assert classify_play(play("End Period")).kind == "reset"


def test_free_throw_last_and_technical() -> None:
    last = classify_play(play("Free Throw - 2 of 2", H, True))
    assert last.kind == "ft" and last.last_ft and last.made
    first = classify_play(play("Free Throw - 1 of 2", H, True))
    assert first.kind == "ft" and not first.last_ft
    assert classify_play(play("Free Throw - Technical", H, True)).kind == "other"


def test_possession_rule() -> None:
    assert update_possession(None, Event("made_fg", H), H, A) == A
    assert update_possession(H, Event("miss_fg", H), H, A) is None
    assert update_possession(None, Event("dreb", A), H, A) == A
    assert update_possession(None, Event("oreb", H), H, A) == H
    assert update_possession(H, Event("tov", H), H, A) == A
    # a missed last free throw leaves the ball loose; a made one gives it to the other team
    assert update_possession(H, Event("ft", H, made=False, last_ft=True), H, A) is None
    assert update_possession(H, Event("ft", H, made=True, last_ft=True), H, A) == A
    assert update_possession(H, Event("ft", H, made=True, last_ft=False), H, A) == H
    # idempotent: the same turnover reported twice does not toggle
    twice = [play("Offensive Foul", H), play("Offensive Foul Turnover", H)]
    assert possession_from_plays(twice, H, A) == A


def test_unknown_team_never_guesses() -> None:
    assert update_possession(H, Event("tov", "77"), H, A) == H  # keeps the old value
    assert update_possession(H, Event("reset", ""), H, A) is None


def test_substitution_and_timeout() -> None:
    p = {
        "type": {"text": "Substitution"},
        "team": {"id": A},
        "participants": [{"athlete": {"id": "1"}}, {"athlete": {"id": "2"}}],
    }
    assert substitution(p) == (A, "1", "2")
    assert substitution({"type": {"text": "Substitution"}, "participants": []}) is None
    assert timeout_team({"type": {"text": "Full Timeout"}, "team": {"id": H}}) == H
    assert timeout_team({"type": {"text": "Jump Shot"}, "team": {"id": H}}) is None


def test_parse_state_fixture() -> None:
    body = json.loads(FIX.read_text())
    s = parse_state(body, "401909092")
    assert s is not None
    assert (s.state, s.period, s.clock_secs, s.secs_left) == ("in", 3, 401.0, 1121.0)
    assert (s.home.score, s.away.score) == (83, 59)
    assert (s.home.timeouts_remaining, s.away.timeouts_remaining) == (5, 3)
    assert (s.home.team_fouls_period, s.away.team_fouls_period) == (2, 2)
    assert s.home.bonus_state == "NONE"
    assert len(s.home.on_court) == 5 and len(s.away.on_court) == 5
    assert s.home.player_fouls["4433137"] == 4
    assert s.possession == "away"  # the home side's last free throw dropped and GS has the ball
    assert s.espn_home_wp == 0.995


def test_parse_state_refuses_unrecognised_body() -> None:
    assert parse_state({}, "x") is None
    assert parse_state({"header": {"competitions": [{}]}}, "x") is None
