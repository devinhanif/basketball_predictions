"""Writes the ILLUSTRATION fixtures for the top_parlays/1 contract (docs/contracts/top_parlays_v1.md).

Every number is synthetic (players, lines, prices, probabilities) and the files say so
("illustration": true); dates are 2024, never season 2025. The producer's own hindsight fixture
(top_parlays_example.json) replaces these once it exists. Deterministic: rerun to rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "top_parlays"
CAVEAT_T490 = "Parlays the model called 70%+ hit about 10 points less in 2023-24 (T490)."

# name, team, opp, stat, side, line, price, p_raw, p_cal, source, game
LEGS = [
    ("Sample Center 1", "BOS", "ATL", "reb", "under", 11.5, -140, 0.72, 0.60, "AVG", "0022400001"),
    ("Sample Guard 2", "DET", "MIA", "ast", "over", 4.5, -125, 0.68, 0.56, "production", "0022400002"),
    ("Sample Wing 3", "ORL", "CHA", "pts", "under", 21.5, -130, 0.66, 0.56, "AVG", "0022400003"),
    ("Sample Forward 4", "PHI", "NYK", "fg3m", "under", 3.5, -150, 0.71, 0.61, "production", "0022400004"),
    ("Sample Guard 5", "BOS", "ATL", "pts", "over", 17.5, -115, 0.64, 0.53, "AVG", "0022400001"),
    ("Sample Wing 6", "DET", "MIA", "reb", "under", 6.5, -120, 0.63, 0.54, "AVG", "0022400002"),
]
TIPS = {
    "0022400001": "2024-11-13T00:00:00Z",
    "0022400002": "2024-11-13T00:00:00Z",
    "0022400003": "2024-11-13T00:00:00Z",
    "0022400004": "2024-11-13T00:30:00Z",
}


def implied(american: int) -> float:
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def payout(american: int) -> float:
    return 100 / -american if american < 0 else american / 100


def leg(i: int) -> dict[str, object]:
    n, team, opp, stat, side, line, price, pr, pc, src, gid = LEGS[i]
    return {
        "game_id": gid, "tipoff": TIPS[gid], "player_id": 9000 + i, "player_name": n,
        "team": team, "opp": opp, "stat": stat, "side": side, "line": line, "book": "draftkings",
        "price_american": price, "price_at": "2024-11-12T23:05:00Z", "p_raw": pr, "p_cal": pc,
        "model_source": src,
        "reason": f"The model's raw chance is {round(pr * 100)}%; after calibration it is {round(pc * 100)}%.",
    }


def ticket(rank: int, idx: list[int], width: float) -> dict[str, object]:
    legs = [leg(i) for i in idx]
    pr = pc = imp = dec = 1.0
    for i in idx:
        pr *= LEGS[i][7]
        pc *= LEGS[i][8]
        imp *= implied(LEGS[i][6])
        dec *= 1 + payout(LEGS[i][6])
    ev = round(pc * dec - 1, 3)
    return {
        "rank": rank,
        "same_game": len({LEGS[i][10] for i in idx}) < len(idx),
        "legs": legs,
        "p_joint_raw": round(pr, 3), "p_joint_cal": round(pc, 3),
        "p_joint_cal_80": [round(pc - width, 3), round(pc + width, 3)],
        "book_implied": round(imp, 3), "ev_per_dollar": ev, "ev_low80": round(ev - 2 * width * dec, 3),
        "why": (
            f"All {len(idx)} legs have to hit. The calibrated chance is {round(pc * 100)}% and the "
            f"book's price implies {round(imp * 100)}%. The model is not proven, so this is a paper ticket."
        ),
    }


def doc(date: str, sizes: dict[str, list[dict[str, object]]], empty: str | None) -> dict[str, object]:
    return {
        "schema": "top_parlays/1",
        "illustration": True,
        "slate_date": date,
        "made_at": f"{date}T22:10:00Z",
        "calibration": {
            "rule": "LINE_CALIBRATION", "sha8": "a5441242",
            "fit_through": "2025-06-30 (season 2024; season 2025 rows only after a logged training-use row, Devin)",
            "status": "Stage 1 pass (T493); live confirmation pending",
        },
        "ranking": "calibrated joint probability, highest first; ties broken by higher EV",
        "verdict": "keep_your_money",
        "empty_reason": empty,
        "sizes": sizes,
        "caveats": [CAVEAT_T490, "ILLUSTRATION: synthetic players, lines and prices; not a real slate."],
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    two = [ticket(1, [0, 3], 0.05), ticket(2, [3, 2], 0.05), ticket(3, [0, 2], 0.05)]
    three = [ticket(1, [0, 3, 2], 0.06), ticket(2, [0, 3, 1], 0.06), ticket(3, [0, 3, 5], 0.06)]
    files = {
        "2024-11-12": doc("2024-11-12", {"2": two, "3": three}, None),
        "2024-11-20": doc("2024-11-20", {"2": [], "3": []}, "no priced legs"),
    }
    for d, body in files.items():
        (OUT / f"top_parlays_{d}.json").write_text(json.dumps(body, indent=1) + "\n")


if __name__ == "__main__":
    main()
