"""Plant one synthetic rung_cal prop into slates/2024-11-12.json, in place (idempotent).

The slate fixtures are built from real out-of-fold data, but the nightly export's rung-calibrated
probabilities (docs/contracts/rung_cal_export_v1.md) do not exist yet. This adds, to the SECOND
player's points prop of the first game (the first player's props stay as built), the three optional export fields so the line slider's calibrated
path can be exercised end to end:

  * forecast.surv     the full-support table P(Y >= k) = c_k / n (n = 1000), read off the prop's own
                      real quantile grid by the same interpolation the app uses (not new information);
  * book_main_line    the prop's posted line;
  * rung_cal          SYNTHETIC: 0.7 x the model's own P(over) + 0.3 x a logistic centred on the book
                      main line, one entry per half-point line from the 5th percentile up to where the
                      model's own P(over) falls below 0.5 (the contract amendment: the map is fit only there). It says nothing about the real map; it only has the right shape
                      (monotone non-increasing) and is marked `synthetic` here.

Run from the repo root:  uv run --script app/fixtures/add_rung_cal_example.py
"""
# /// script
# requires-python = ">=3.12"
# ///

from __future__ import annotations

import json
import math
from pathlib import Path

PATH = Path(__file__).parent / "slates" / "2024-11-12.json"
TAUS = [round(0.05 * i, 2) for i in range(1, 20)]
N = 1000


def cdf(q: list[float], x: float) -> float:
    if x < q[0]:
        return TAUS[0]
    if x >= q[-1]:
        return TAUS[-1]
    i = max(k for k in range(len(q) - 1) if q[k] <= x)
    return TAUS[i] + (TAUS[i + 1] - TAUS[i]) * (x - q[i]) / (q[i + 1] - q[i])


def main() -> None:
    d = json.loads(PATH.read_text())
    prop = next(p["props"]["pts"] for p in d["games"][0]["players"][1:] if "pts" in p["props"])
    q = prop["forecast"]["q"]
    hi_k = math.ceil(q[-1]) + 6
    # Interpolation alone cannot see past the 95th percentile (it clamps), so the table ends at the
    # last whole count the grid reaches and decays the final 5% linearly to zero over six counts;
    # the bottom 5% ramps in linearly from zero.
    ge = []
    for k in range(1, hi_k + 1):
        over_k = 1.0 - cdf(q, k - 0.5)
        if k - 0.5 < q[0]:  # below the 5th percentile the grid clamps; ramp from certain at 0
            over_k = 1.0 - TAUS[0] * k / max(1.0, q[0] + 0.5)
        beyond = k - math.floor(q[-1])
        ge.append(over_k if beyond <= 0 else max(0.0, TAUS[0] * (1 - (beyond - 1) / 6)))
    counts = [round(v * N) for v in ge]
    for i in range(1, len(counts)):
        counts[i] = min(counts[i], counts[i - 1])
    prop["forecast"]["surv"] = {"n": N, "c": counts}
    main_line = float(prop["line"])
    prop["book_main_line"] = main_line
    lo = max(0.5, math.floor(q[0] - 0.5) + 0.5)
    hi = math.ceil(q[-1] - 0.5) + 0.5
    scale = (q[-1] - q[0]) / 6

    def raw_over(line: float) -> float:
        k = math.floor(line) + 1
        return counts[k - 1] / N if 1 <= k <= len(counts) else (1.0 if k <= 0 else 0.0)

    lines = []
    x = lo
    while x <= hi + 1e-9:
        sig = 1 / (1 + math.exp(-(main_line - x) / scale))
        if raw_over(x) < 0.5:  # amendment 2026-10-15: the map is fit only where raw P(over) >= 0.5
            x += 1.0
            continue
        lines.append({"line": x, "p_over": round(0.7 * raw_over(x) + 0.3 * sig, 4)})
        x += 1.0
    prop["rung_cal"] = {
        "book": "draftkings",
        "main_line": main_line,
        "price_at": prop["line_snapshot_utc"].replace("+00:00", "Z"),
        "map_version": "rung_cal_map_v1",
        "map_sha8": "116f4480",
        "lines": lines,
        "synthetic": True,
    }
    PATH.write_text(json.dumps(d, separators=(",", ":")))
    print("planted", prop["prop_id"], "n lines", len(lines), "line", main_line)


if __name__ == "__main__":
    main()
