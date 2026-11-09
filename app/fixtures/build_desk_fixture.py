"""Writes SYNTHETIC desk exports that follow docs/contracts/desk_export_v1.md.

Nothing here is real: names are "Sample Player N", prices and results are invented so every screen and every
null state can be exercised before the market agent's real files exist. Each file carries ``"_fixture"`` and
the app shows a "synthetic fixture" label on every desk screen when it is present.

    python app/fixtures/build_desk_fixture.py            # writes desk_fixture_private.json / desk_fixture_shared.json
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent
NOTE = "SYNTHETIC FIXTURE: invented rows for building the screens. Not JARVIS data."
DAYS = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"]
GAMES = [("PHI", "CHI", "0022600196"), ("LAL", "DET", "0022600197"), ("UTA", "MIN", "0022600198"), ("BKN", "DAL", "0022600199")]
STATS = [("pts", "points"), ("reb", "rebounds"), ("ast", "assists"), ("fg3m", "threes")]


def build(private: bool) -> dict:
    rng = random.Random(7)
    plays = []
    n = 0
    for di, day in enumerate(DAYS + ["2026-11-08"]):
        today = day == "2026-11-08"
        for k in range(3 if today else 4):
            n += 1
            away, home, gid = GAMES[(di + k) % len(GAMES)]
            stat, word = STATS[(n * 3) % 4]
            thr = [2, 4, 6, 3][(n) % 4] if stat != "pts" else 20
            price = rng.choice([36, 41, 44, 47, 52])
            fair = round((price + rng.choice([1, 2, 3, 4])) / 100, 3)
            model = round(fair + rng.choice([0.01, 0.02, 0.03]), 3)
            fee = 1
            edge = round(model * 100 - price - fee, 1)
            result = None
            if not today:
                won = rng.random() < 0.46
                close = price + rng.choice([-3, -1, 0, 1, 2])
                clv = round(close - price + rng.choice([-1.5, -0.5, 0.5]), 1)
                result = {
                    "status": "settled", "won": won, "close_cents": float(close), "clv_cents": clv,
                    "pnl_usd": (round((100 - price - fee) / 100 * 2, 2) if won else round(-(price + fee) / 100 * 2, 2)) if private else None,
                }
            plays.append({
                "play_id": f"fx-{n:03d}", "bundle_id": f"fx-bundle-{day}", "slate_date": day,
                "ts": f"{day}T{17 + k}:05:00Z", "game_id": gid, "matchup": f"{away} @ {home}",
                "tipoff": f"{day}T{23 + 0}:00:00Z", "source": "fixture", "venue": "kalshi", "kind": "prop", "side": "yes",
                "player_id": 9000000 + n, "player_name": f"Sample Player {n}", "team": home, "stat": stat,
                "threshold": float(thr), "leg_desc": f"Sample Player {n} {thr}+ {word}", "price_cents": float(price),
                "fee_cents": float(fee) if private else None, "fair_prob": fair, "model_prob": model, "edge_cents": edge,
                "edge_label": "thin" if edge > 0 else "none", "reason": "fixture row", "explanation": None,
                "delivered": "discord", "contracts": 2 if private else None, "stake_usd": round(2 * (price + fee) / 100, 2) if private else None,
                "result": result,
            })
    settled = [p for p in plays if p["result"]]
    clvs = [p["result"]["clv_cents"] for p in settled]
    mean = sum(clvs) / len(clvs)
    by_day: dict[str, list[float]] = {}
    for p in settled:
        by_day.setdefault(p["slate_date"], []).append(p["result"]["clv_cents"])
    dm = [sum(v) / len(v) for v in by_day.values()]
    dmean = sum(dm) / len(dm)
    sd = math.sqrt(sum((x - dmean) ** 2 for x in dm) / (len(dm) - 1))
    half = 2.776 * sd / math.sqrt(len(dm))
    daily = []
    for day, v in sorted(by_day.items()):
        rows = [p for p in settled if p["slate_date"] == day]
        daily.append({
            "slate_date": day, "n": len(rows), "n_won": sum(1 for p in rows if p["result"]["won"]),
            "clv_mean_cents": round(sum(v) / len(v), 2),
            "pnl_usd": round(sum(p["result"]["pnl_usd"] for p in rows), 2) if private else None,
        })
    obs = [
        {"id": "fx-o1", "slate_date": "2026-11-08", "ts": "2026-11-08T21:12:00Z", "game_id": "0022600196", "matchup": "PHI @ CHI",
         "when_text": "Q2 1:20", "kind": "foul", "title": "Sample Player 3 has four fouls", "text": "Four fouls in nine minutes (fixture).",
         "certainty": "reported", "corrects": None},
        {"id": "fx-o2", "slate_date": "2026-11-08", "ts": "2026-11-08T21:13:00Z", "game_id": "0022600196", "matchup": "PHI @ CHI",
         "when_text": "Q2 1:20", "kind": "rotation", "title": "His minutes may be reduced", "text": "Rotation context, not a priced edge (fixture).",
         "certainty": "inference", "corrects": None},
        {"id": "fx-o3", "slate_date": "2026-11-07", "ts": "2026-11-07T03:40:00Z", "game_id": "0022600198", "matchup": "UTA @ MIN",
         "when_text": "Q4 0:27", "kind": "exit", "title": "Two starters may be finished", "text": "Both off the floor for more than three minutes (fixture).",
         "certainty": "inference", "corrects": None},
        {"id": "fx-o4", "slate_date": "2026-11-07", "ts": "2026-11-07T03:52:00Z", "game_id": "0022600198", "matchup": "UTA @ MIN",
         "when_text": "Q4 6:32", "kind": "correction", "title": "Correction: Sample Player 5 returned", "text": "The earlier 'may be finished' inference was reversed (fixture).",
         "certainty": "correction", "corrects": "fx-o3"},
        {"id": "fx-o5", "slate_date": "2026-11-07", "ts": "2026-11-07T01:30:00Z", "game_id": "0022600197", "matchup": "LAL @ DET",
         "when_text": "Halftime", "kind": "halftime", "title": "Two signals at the half", "text": "Home team leads 63-51 (fixture).",
         "certainty": "reported", "corrects": None},
        {"id": "fx-o6", "slate_date": "2026-11-05", "ts": "2026-11-05T20:00:00Z", "game_id": None, "matchup": None,
         "when_text": None, "kind": "risk", "title": "The model is in review", "text": "Closing-line value is below zero across the settled sample (fixture).",
         "certainty": "reported", "corrects": None},
    ]
    alerts = [
        {"id": "fx-a1", "ts": "2026-11-08T21:14:00Z", "slate_date": "2026-11-08", "kind": "foul", "mode": "discord", "title": "Sample Player 3 has four fouls", "text": None, "play_ids": ["fx-021"], "observation_ids": ["fx-o1"]},
        {"id": "fx-a2", "ts": "2026-11-07T03:41:00Z", "slate_date": "2026-11-07", "kind": "exit", "mode": "discord", "title": "Starters may be finished", "text": None, "play_ids": [], "observation_ids": ["fx-o3"]},
        {"id": "fx-a3", "ts": "2026-11-07T03:53:00Z", "slate_date": "2026-11-07", "kind": "correction", "mode": "discord", "title": "Correction posted", "text": None, "play_ids": [], "observation_ids": ["fx-o4"]},
        {"id": "fx-a4", "ts": "2026-11-05T20:01:00Z", "slate_date": "2026-11-05", "kind": "kill_switch", "mode": "discord", "title": "Model in review", "text": None, "play_ids": [], "observation_ids": ["fx-o6"]},
    ]
    return {
        "_fixture": NOTE, "schema": 1, "privacy": "private" if private else "shared",
        "built_at": "2026-11-08T22:00:00Z", "data_as_of": "2026-11-08T21:30:00Z",
        "sources": {"fixture": "synthetic, generated by app/fixtures/build_desk_fixture.py"},
        "status": {"kill_switch": {"on": True, "since": "2026-11-05T20:00:00Z", "n": len(settled), "mean_clv_cents": round(mean, 2), "note": "fixture"},
                   "model_review": True, "w_model": 0.0},
        "balance": {"start_usd": 70.0, "current_usd": 69.0, "floor_usd": 20.0} if private else None,
        "plays": plays,
        "clv_summary": {"n": len(clvs), "mean_cents": round(mean, 2), "ci95": [round(dmean - half, 2), round(dmean + half, 2)],
                        "clustered_on": "slate_date", "reason": None},
        "record": {"n_settled": len(settled), "n_won": sum(1 for p in settled if p["result"]["won"]),
                   "n_lost": sum(1 for p in settled if not p["result"]["won"]), "n_void": 0},
        "daily": daily, "observations": obs, "alerts": alerts,
        "halftime": [{"slate_date": "2026-11-07", "game_id": "0022600197", "matchup": "LAL @ DET", "player_id": 9000099, "player_name": "Sample Player 9",
                      "stat": "reb", "threshold": 4.0, "price_cents": 27.0, "model_prob": 0.43, "edge_cents": None, "result": None}],
        "track_record": {
            "scorecard_date": "2026-11-07",
            "model_vs_market": {"n": 42, "metric": "brier", "diff": 0.0061, "ci95": [-0.004, 0.0162], "clustered_on": "game_id", "reason": None},
            "calibration": [
                {"bin_lo": 0.0, "bin_hi": 0.2, "n": 6, "mean_pred": 0.14, "hit_rate": 0.17},
                {"bin_lo": 0.2, "bin_hi": 0.4, "n": 14, "mean_pred": 0.31, "hit_rate": 0.29},
                {"bin_lo": 0.4, "bin_hi": 0.6, "n": 22, "mean_pred": 0.5, "hit_rate": 0.45},
                {"bin_lo": 0.6, "bin_hi": 0.8, "n": 12, "mean_pred": 0.69, "hit_rate": 0.58},
                {"bin_lo": 0.8, "bin_hi": 1.0, "n": 4, "mean_pred": 0.86, "hit_rate": 0.75},
            ],
        },
    }


if __name__ == "__main__":
    for private, name in ((True, "desk_fixture_private.json"), (False, "desk_fixture_shared.json")):
        (OUT / name).write_text(json.dumps(build(private), indent=1) + "\n", encoding="utf-8")
        print("wrote", OUT / name)
