from __future__ import annotations

import pandas as pd

from nba.ride.v2_gate import FOUL_TOKEN, elapsed, to_events

H, A = 1610612738, 1610612751


def test_elapsed_regulation_and_overtime() -> None:
    assert elapsed(1, 720.0) == 0.0
    assert elapsed(2, 700.0) == 720.0 + 20.0
    assert elapsed(4, 0.0) == 2880.0
    assert elapsed(5, 300.0) == 2880.0
    assert elapsed(5, 0.0) == 3180.0


def test_foul_token_team_count_and_penalty() -> None:
    m = FOUL_TOKEN.search("Johnson S.FOUL (P1.T3) (J.Goldenberg)")
    assert m is not None and m.group(1) == "1" and m.group(2) == "T3"
    m = FOUL_TOKEN.search("Smith Jr. P.FOUL (P2.PN) (Z.Zarba)")
    assert m is not None and m.group(2) == "PN"
    m = FOUL_TOKEN.search("Sharpe OFF.Foul (P1) (J.Goldenberg)")
    assert m is not None and m.group(2) is None


def test_to_events_possession_kinds() -> None:
    rows = [
        ("Made Shot", "", H, "Brown 3PT"),
        ("Missed Shot", "", A, "MISS Smith 26' 3PT"),
        ("Rebound", "", H, "Tatum REBOUND (Off:0 Def:1)"),
        ("Free Throw", "Free Throw 2 of 2", H, "MISS Brown Free Throw 2 of 2"),
        ("Timeout", "Regular", 0, "Nets Timeout"),
    ]
    d = pd.DataFrame(
        {
            "period": 1,
            "clock": "PT10M00.00S",
            "action_type": [r[0] for r in rows],
            "sub_type": [r[1] for r in rows],
            "team_id": [r[2] for r in rows],
            "player_id": [0, 0, 0, 0, A],
            "description": [r[3] for r in rows],
        }
    )
    ev = to_events(d, H, A)
    kinds = [e[2].kind for e in ev]
    assert kinds == ["made_fg", "miss_fg", "dreb", "ft", "other"]
    assert ev[3][2].last_ft and not ev[3][2].made
    assert ev[4][3]["timeout_team"] == A
