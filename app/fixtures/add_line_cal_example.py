"""Plant three synthetic line_cal rows into slates/2024-11-12.json, in place (idempotent).

docs/contracts/line_cal_export_v1.md adds, per prop row, `line_cal` (JSON or null) and
`line_cal_null_reason`. No real export carries them yet, so this plants representative rows on Jaylen
Brown's props of the first game (run add_rung_cal_example.py first; it gives his points prop its
exact table and rung_cal):

  * pts   line_cal set at the book main line. p_over_raw is read from the prop's own exact table
          (P(Y >= 24)); p_over_cal is SYNTHETIC (raw minus 0.04) and says nothing about the
          real map. The sha8 and fit_through are the contract's example values.
  * ast   line_cal null, reason no_book_line (no book_main_line either)
  * reb   line_cal null, reason grid_mismatch (a book main line exists, the value could not be reproduced)

Run from the repo root:  uv run --script app/fixtures/add_line_cal_example.py
"""
# /// script
# requires-python = ">=3.12"
# ///

from __future__ import annotations

import json
import math
from pathlib import Path

PATH = Path(__file__).parent / "slates" / "2024-11-12.json"


def main() -> None:
    d = json.loads(PATH.read_text())
    brown = d["games"][0]["players"][1]
    props = brown["props"]

    pts = props["pts"]
    main_line = float(pts["line"])
    counts = pts["forecast"]["surv"]["c"]
    n = pts["forecast"]["surv"]["n"]
    raw = counts[math.floor(main_line)] / n  # P(Y >= floor(line) + 1)
    pts["line_cal"] = {
        "book": "draftkings",
        "main_line": main_line,
        "price_at": pts["line_snapshot_utc"].replace("+00:00", "Z"),
        "p_over_raw": raw,
        "p_over_cal": round(raw - 0.04, 4),
        "map": "LINE_CALIBRATION",
        "map_sha8": "443743a1",
        "fit_through": "2025-06-30",
        "synthetic": True,
    }
    pts["line_cal_null_reason"] = None

    props["ast"]["line_cal"] = None
    props["ast"]["line_cal_null_reason"] = "no_book_line"

    props["reb"]["line_cal"] = None
    props["reb"]["line_cal_null_reason"] = "grid_mismatch"
    props["reb"]["book_main_line"] = float(props["reb"]["line"])

    PATH.write_text(json.dumps(d, separators=(",", ":")))
    print("planted line_cal: pts raw", raw, "cal", pts["line_cal"]["p_over_cal"], "| ast no_book_line | reb grid_mismatch")


if __name__ == "__main__":
    main()
