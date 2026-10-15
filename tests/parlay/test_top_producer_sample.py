"""A small committed producer run (synthetic legs through build_report), consumed by the app's
validator test (app/api/tests/test_top_parlays_producer.py). Regenerate with WRITE_SAMPLE=1."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from nba.parlay.config import load_config
from nba.parlay.copula import PairCorr
from nba.parlay.top_parlays import Gate, build_report, make_leg

SAMPLE = (
    Path(__file__).resolve().parents[2] / "app" / "fixtures" / "top_parlays_producer_sample.json"
)
# synthetic dates and game ids, in the future of every real store
TIP = datetime(2031, 1, 15, 23, 0)
MADE = TIP - timedelta(minutes=30)
STATS = ("pts", "reb", "ast", "fg3m")


def sample_report() -> dict:
    legs = []
    n = 0
    for g in range(5):
        for p in range(3):
            for s, stat in enumerate(STATS):
                n += 1
                legs.append(
                    make_leg(
                        p_over_raw=0.40 + 0.5 * ((n * 37) % 50) / 50,
                        p_over_cal=0.42 + 0.4 * ((n * 37) % 50) / 50,
                        side="over" if s % 2 else "under",
                        game_id=f"009310000{g}",
                        tipoff=TIP + timedelta(minutes=30 * g),
                        player_id=900000 + 10 * g + p,
                        player_name=f"Sample Player{g}{p}",
                        team_id=1 + p % 2,
                        opp_id=3,
                        team="AAA",
                        opp="BBB",
                        stat=stat,
                        line=10.5 + s,
                        book="draftkings",
                        price_american=-110 - (n % 5) * 10,
                        price_at=MADE - timedelta(minutes=5),
                        model_source="production",
                        drivers={"mean": 11.0 + s, "recent_avg": 10.2 + s, "minutes": 31.5},
                    )
                )
    pc = PairCorr(
        rho={("same_player", "pts", "reb"): 0.3, ("same_player", "ast", "fg3m"): 0.1}, n={}
    )
    rep = build_report(
        legs,
        slate_date="2031-01-15",
        made_at=MADE,
        now=MADE,
        cfg=load_config(),
        pc=pc,
        calibration={
            "rule": "LINE_CALIBRATION",
            "sha8": "a5441242",
            "fit_through": "2025-06-30 (season 2024)",
            "status": "synthetic producer sample",
        },
        gate=Gate(),
    )
    return json.loads(json.dumps(rep))


def test_committed_sample_is_the_current_producer_output() -> None:
    rep = sample_report()
    if os.environ.get("WRITE_SAMPLE"):
        SAMPLE.write_text(json.dumps(rep, indent=1) + "\n")
    assert json.loads(SAMPLE.read_text()) == rep
    assert rep["verdict"] == "keep_your_money" and rep["illustration"] is False
    assert any(t["same_game"] for ts in rep["sizes"].values() for t in ts)
