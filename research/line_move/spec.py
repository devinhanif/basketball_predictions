"""Constants transcribed from the frozen T510 / T510b texts, and the file-hash check.

T510  = docs/prereg/LINE_MOVE_TOWARD_MODEL.md          (file sha256 881aca57...)
T510b = docs/prereg/LINE_MOVE_TOWARD_MODEL_DNP_FAIR.md (file sha256 68f0ef84...)

Nothing here computes a movement statistic. The scorer refuses to run if either text has changed.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

T510_DOC = "docs/prereg/LINE_MOVE_TOWARD_MODEL.md"
T510B_DOC = "docs/prereg/LINE_MOVE_TOWARD_MODEL_DNP_FAIR.md"
T510_SHA256 = "881aca57600638f67915b5dc6a84b6a56753f026a4eae4a17811d7cee23af1ad"
T510B_SHA256 = "68f0ef844daf0818453d6ae3af98c3106be4e329fda57896e85ffa9657e1ec0b"

# Alias file pin (adversary ruling #1, 2026-10-24; Devin confirms in the morning). One constant, so
# it is
# easy to change before any look. The scorer refuses to run on any other alias file.
ALIASES_SHA256 = "4d176c5381249effa54887cf9eecab88fa733417ae9f32dbbd57e0dbda4cd5d9"  # 57164b8
ALIASES_SHA256_FREEZE = (
    "6544535ab785f6cbae73d239be74e5c6ac5cf75f5e554804cb4d70353f2bfd11"  # 94c56f0
)

REPORT_DIR = REPO / "reports" / "line_move"
# Season end for the "horizon not reached" declaration (adversary R1): the last ET date in the
# pinned copy of the schedule (sha256 below; 1,200 games). Read 2's cutoff of 2027-04-12 is a
# different date (two days before the read).
SCHEDULE_PINNED = REPO / "data" / "raw" / "schedule_2026-10-23" / "live_tips_2026-27.parquet"
SCHEDULE_SHA256 = "9702c830abe987ffea696af800aeb9d1bdc88d275756d4333c19663fca40d86d"
SEASON_END = date(2027, 4, 11)
UNSCOREABLE_REASONS = ("dryrun_failed", "horizon_not_reached")
DRYRUN_RECORD = REPORT_DIR / "dryrun_record.json"
COMPLETE_MARGIN_MIN = 30  # every tip of a window date must be older than now - 30 min

# --- Data / definitions (T510) ---
M_MODEL = "props_context_residual"
B_MODEL = "props_recency_v1"
FORECAST_LEAD_MIN = 60  # M = latest row with made_at <= tipoff - 60 min
S0_LEAD_MIN = 150  # S0 = first snapshot >= max(made_at, tipoff - 150 min)
S1_GAP_MIN = 15  # S1 = first snapshot >= S0 + 15 min
CLOSE_GAP_MIN = 30  # close - S1 >= 30 min
MAX_SPREAD = 0.10  # ask - bid <= 0.10, 0 < bid < ask < 1
QUALIFY_GAP = 0.05
TARGET_MID = 0.50
SERIES_STAT = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}

# --- Test (T510) ---
FIRST_DATE = date(2026, 10, 22)  # inclusive; 2026-10-21 is the excluded gate night
GATE_NIGHT = date(2026, 10, 21)
SEED = 20261022
N_RESAMPLES = 10_000
FLOOR_PART1 = 0.010
FLOOR_PART2 = 0.005
MIN_QUALIFYING = 300
MIN_DATES = 14
LEVEL_T510 = 0.95  # two-sided percentile / t-interval level
LEVEL_T510B = (
    0.975  # Bonferroni level for the two-test family (1.25th percentile, 98.75th t quantile)
)

# --- Dryrun (L-S2 and T510b B4) ---
DRYRUN_DRAWS = 1000
DRYRUN_FALSE_PASS_T510 = 0.05
DRYRUN_FALSE_PASS_T510B = 0.025
DRYRUN_PLANT = 0.02

# --- Gate counts on 2026-10-21 (T510 / T510b texts) ---
GATE_21 = {
    "markets_scored": 431,
    "p1_T510": 324,
    "p1_T510b": 298,
    "p1_both": 276,
    "only_T510": 48,
    "only_T510b": 22,
    "sign_flips": 1,
    "p2_T510": 399,
    "p2_T510b": 403,
    "pplay_lt_0.9_scored": 109,
}


# Gate counts on 2026-10-21 with the pinned alias file (diagnostic regression of the pinned inputs).
GATE_21_PINNED = {
    "markets_scored": 455,
    "p1_T510": 345,
    "p1_T510b": 318,
    "p1_both": 293,
    "only_T510": 52,
    "only_T510b": 25,
    "sign_flips": 1,
    "p2_T510": 420,
    "p2_T510b": 423,
    "pplay_lt_0.9_scored": 133,
}


def code_files() -> list[Path]:
    """Every module of the package (the hash of the scorer's code covers all of them)."""
    return sorted(p for p in Path(__file__).parent.glob("*.py") if p.name != "__init__.py")


def code_sha256() -> str:
    h = hashlib.sha256()
    for p in code_files():
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FrozenTextChanged(RuntimeError):
    """A frozen text does not hash to its pinned value; the scorer refuses to run."""


def verify_texts() -> dict[str, str]:
    out = {}
    for doc, want in ((T510_DOC, T510_SHA256), (T510B_DOC, T510B_SHA256)):
        got = sha256_file(REPO / doc)
        if got != want:
            raise FrozenTextChanged(f"{doc}: sha256 {got[:8]} != pinned {want[:8]}")
        out[doc] = got
    return out
