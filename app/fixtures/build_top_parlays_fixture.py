"""Writes the ILLUSTRATION fixtures for the top_parlays/2 contract (docs/contracts/top_parlays_v2.md).

Dates and game ids (2024-01-01/02, ILLUS-n) are in no real store, so the illustration never overlays a real slate.
Every number is synthetic (players, lines, prices, probabilities) and the files say so
("illustration": true); dates are 2024, never season 2025. The producer's own hindsight fixture
(top_parlays_example.json) replaces these once it exists. Deterministic: rerun to rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "top_parlays"
CAVEAT_T490 = (
    "Two-leg parlays of alternate-line overs the raw model put at 70% or more (BetRivers) hit 9.8 points "
    "less often than the raw model said (95% CI 6.8 to 12.9, n 3,337 parlays, date-clustered, 2023-24 and "
    "2024-25; T490). Three-leg: 8.7 points (5.5 to 11.9, n 3,360). These were raw chances; the chances "
    "shown here are from calibrated legs."
)

# name, team, opp, stat, side, line, price, p_raw, p_cal, source, game
LEGS = [
    ("Sample Center 1", "BOS", "ATL", "reb", "under", 11.5, -140, 0.72, 0.60, "AVG", "ILLUS-1"),
    ("Sample Guard 2", "DET", "MIA", "ast", "over", 4.5, -125, 0.68, 0.56, "production", "ILLUS-2"),
    ("Sample Wing 3", "ORL", "CHA", "pts", "under", 21.5, -130, 0.66, 0.56, "AVG", "ILLUS-3"),
    ("Sample Forward 4", "PHI", "NYK", "fg3m", "under", 3.5, -150, 0.71, 0.61, "production", "ILLUS-4"),
    ("Sample Guard 5", "BOS", "ATL", "pts", "over", 17.5, -115, 0.64, 0.53, "AVG", "ILLUS-1"),
    ("Sample Wing 6", "DET", "MIA", "reb", "under", 6.5, -120, 0.63, 0.54, "AVG", "ILLUS-2"),
]
TIPS = {
    "ILLUS-1": "2024-01-02T00:00:00Z",
    "ILLUS-2": "2024-01-02T00:00:00Z",
    "ILLUS-3": "2024-01-02T00:00:00Z",
    "ILLUS-4": "2024-01-02T00:30:00Z",
}


def implied(american: int) -> float:
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def payout(american: int) -> float:
    return 100 / -american if american < 0 else american / 100


def leg(i: int, tail_flag: bool = False) -> dict[str, object]:
    n, team, opp, stat, side, line, price, pr, pc, src, gid = LEGS[i]
    out: dict[str, object] = {
        "game_id": gid, "tipoff": TIPS[gid], "player_id": 9000 + i, "player_name": n,
        "team": team, "opp": opp, "stat": stat, "side": side, "line": line, "book": "draftkings",
        "price_american": price, "price_at": "2024-01-01T21:40:00Z", "p_raw": pr, "p_cal": pc,
        "model_source": src,
        "reason": f"The model's raw chance is {round(pr * 100)}%; after calibration it is {round(pc * 100)}%.",
    }
    if tail_flag:
        out["tail_flag"] = True
    return out


def ticket(rank: int, idx: list[int], width: float, flag: int | None = None) -> dict[str, object]:
    legs = [leg(i, i == flag) for i in idx]
    pr = pc = imp = dec = 1.0
    for i in idx:
        pr *= LEGS[i][7]
        pc *= LEGS[i][8]
        imp *= implied(LEGS[i][6])
        dec *= 1 + payout(LEGS[i][6])
    ev = round(pc * dec - 1, 3)
    out: dict[str, object] = {
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
    if flag is not None:
        out["tail_note"] = (
            "One leg is flagged: legs the model put at 65% or more ran high in past tests, so treat its chance "
            "as a ceiling. (Illustration text.)"
        )
    return out


def doc(date: str, sizes: dict[str, list[dict[str, object]]], empty: str | None) -> dict[str, object]:
    return {
        "schema": "top_parlays/2",
        "illustration": True,
        "slate_date": date,
        "made_at": f"{date}T22:10:00Z",
        "calibration": {
            "rule": "LINE_CALIBRATION", "sha8": "a5441242",
            "fit_through": "2025-06-30 (season 2024; season 2025 rows only after a logged training-use row, Devin)",
            "status": "Stage 1 pass (T493); live confirmation pending",
        },
        "gate": {
            "settled": 0, "settled_min": 30, "slate_dates": 0, "dates_min": 14,
            "skill_vs_market": None, "open": False,
        },
        "ranking": "calibrated joint probability, highest first; ties broken by higher EV",
        "verdict": "keep_your_money",
        "empty_reason": empty,
        "sizes": sizes,
        "caveats": [CAVEAT_T490, "ILLUSTRATION: synthetic players, lines and prices; not a real slate."],
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    two = [ticket(1, [0, 3], 0.05, flag=3), ticket(2, [3, 2], 0.05), ticket(3, [0, 2], 0.05)]
    three = [ticket(1, [0, 3, 2], 0.06), ticket(2, [0, 3, 1], 0.06), ticket(3, [0, 3, 5], 0.06)]
    files = {
        "2024-01-01": doc("2024-01-01", {"2": two, "3": three}, None),
        "2024-01-02": doc("2024-01-02", {"2": [], "3": []}, "no priced legs"),
    }
    for d, body in files.items():
        (OUT / f"top_parlays_{d}.json").write_text(json.dumps(body, indent=1) + "\n")


if __name__ == "__main__":
    main()
