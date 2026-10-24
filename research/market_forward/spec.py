"""The two frozen forward rules, as data, plus the freeze-hash check.

Every number below is transcribed from the FROZEN section of the rule text, not chosen here.
``docs/KALSHI_LONGSHOT_FORWARD.md`` (frozen-section sha256 0b50d17e...) and
``docs/UNDERS_FORWARD.md`` (149be979...). The scorer refuses to run if the frozen section on disk no
longer hashes to the pinned value (the awk command in each freeze record, reimplemented in
:func:`frozen_section_sha256`).

Read-only with respect to markets: nothing in this package places, simulates or sizes an order.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FROZEN_BEGIN = "<!-- FROZEN-BEGIN -->"
FROZEN_END = "<!-- FROZEN-END -->"

#: Section 1.2: the first ET date of the scoring window (first ET date after the freeze commit,
#: not earlier than 2026-10-25). Preseason and every game on or before 2026-10-24 are excluded.
WINDOW_START = date(2026, 10, 25)

#: Section 1.3: tip = occurrence_datetime - 180 minutes.
OCCURRENCE_TO_TIP_MIN = 180
#: Section 1.5: the entry snapshot is the latest live row with ts <= tip - 60 min, age <= 240 min.
ENTRY_LEAD_MIN = 60
ENTRY_MAX_AGE_MIN = 240
#: Sensitivity S2.
S2_MAX_AGE_MIN = 30

#: Section 6 / 7: bootstrap and floor.
BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 2026
FLOOR = 0.010
MIN_GAMES = 300
MIN_DATES = 50
#: Section 1.6: eligibility in integer cents.
MIN_BID_C = 1
MAX_SPREAD_C = 5
#: A game counts only if its ET tip date is at least this many days before the read date.
SETTLE_GAP_DAYS = 2
#: Guard 8.1 / 8.2 / 8.3.
COVERAGE_MIN = 0.70
CONCENTRATION_MAX = 0.03
SCALAR_MISSING_MAX = 0.02
#: Bucket/slice display threshold.
SLICE_MIN_GAMES = 30
#: Guard 8.6: self-test constants.
SELFTEST_K = 200
SELFTEST_ALPHA = 0.05
SELFTEST_NULL_BAND = (0.02, 0.09)
SELFTEST_PLANT_FACTOR = 0.80
SELFTEST_PLANT_TOL = 0.005

#: Section 7: looks (read date, last scored ET tip date, one-sided alpha).
LOOK_READ_DATE = {1: date(2027, 1, 15), 2: date(2027, 4, 14)}
LOOK_ALPHA = {1: 0.005, 2: 0.02}

#: Section 1.7 kinds (LONGSHOT) / Section 1.7 (UNDERS).
PROP_SERIES = (
    "KXNBAPTS",
    "KXNBAREB",
    "KXNBAAST",
    "KXNBA3PT",
    "KXNBASTL",
    "KXNBABLK",
)
COMBO_SERIES = ("KXNBAPRA", "KXNBAPR", "KXNBAPA", "KXNBARA")
GAME_SERIES = ("KXNBAGAME", "KXNBATOTAL", "KXNBATEAMTOTAL")
SPREAD_SERIES = ("KXNBASPREAD",)


@dataclass(frozen=True)
class Bucket:
    """A mid bucket in integer cents: ``lo_sum <(=) bid_c + ask_c <(=) hi_sum`` (2 * mid * 100)."""

    name: str
    lo_sum: int  # exclusive unless lo_incl
    hi_sum: int  # inclusive unless not hi_incl
    lo_incl: bool
    hi_incl: bool

    def contains(self, s: int) -> bool:
        lo_ok = s >= self.lo_sum if self.lo_incl else s > self.lo_sum
        hi_ok = s <= self.hi_sum if self.hi_incl else s < self.hi_sum
        return lo_ok and hi_ok


# mid = (bid_c + ask_c) / 200, so mid in [0.01, 0.05) is sum in [2, 10), and so on.
_B1 = Bucket("B1", 2, 10, True, False)
_B2 = Bucket("B2", 10, 20, True, False)
_B3 = Bucket("B3", 20, 30, True, True)
LONGSHOT_BUCKETS = (_B1, _B2, _B3)
UNDERS_BUCKETS = (
    Bucket("U1", 2, 10, True, False),
    Bucket("U2", 10, 20, True, False),
    Bucket("U3", 20, 30, True, True),
    Bucket("U4", 30, 60, False, True),
    Bucket("U5", 60, 100, False, True),
    Bucket("U6", 100, 140, False, True),
    Bucket("U7", 140, 170, False, True),
    Bucket("U8", 170, 200, False, False),
)


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    doc: str
    frozen_sha256: str
    #: series scored for the decision
    decision_series: tuple[str, ...]
    #: series scored descriptively only (KXNBASPREAD in LONGSHOT)
    descriptive_series: tuple[str, ...]
    #: max (bid_c + ask_c) for eligibility, None = no upper limit on the mid
    max_mid_sum: int | None
    buckets: tuple[Bucket, ...]
    #: guard 8.2 leave-one-out axis: "series" (LONGSHOT) or "stat" (UNDERS)
    loo_axis: str
    #: display name of the bucket band compared with the other rule
    band_buckets: tuple[str, ...]

    @property
    def all_series(self) -> tuple[str, ...]:
        return self.decision_series + self.descriptive_series

    @property
    def doc_path(self) -> Path:
        return REPO / self.doc


LONGSHOT = RuleSpec(
    rule_id="KALSHI_LONGSHOT_FORWARD",
    doc="docs/KALSHI_LONGSHOT_FORWARD.md",
    frozen_sha256="0b50d17ed52119ba192298f062aee5cc260c59c410c13357a13bce280631e4e1",
    decision_series=PROP_SERIES + COMBO_SERIES + GAME_SERIES,
    descriptive_series=SPREAD_SERIES,
    max_mid_sum=30,
    buckets=LONGSHOT_BUCKETS,
    loo_axis="series",
    band_buckets=("B1", "B2", "B3"),
)
UNDERS = RuleSpec(
    rule_id="UNDERS_FORWARD",
    doc="docs/UNDERS_FORWARD.md",
    frozen_sha256="149be979b0a029ae2836b46ba8b3be9c619b379735d5a1adee4c029a93413ad6",
    decision_series=PROP_SERIES + COMBO_SERIES,
    descriptive_series=(),
    max_mid_sum=None,
    buckets=UNDERS_BUCKETS,
    loo_axis="stat",
    band_buckets=("U1", "U2", "U3"),
)
RULES = {LONGSHOT.rule_id: LONGSHOT, UNDERS.rule_id: UNDERS}


def kind_of(series: str) -> str:
    """Section 1.7 (LONGSHOT) kind: prop / combo / game / spread."""
    if series in PROP_SERIES:
        return "prop"
    if series in COMBO_SERIES:
        return "combo"
    if series in GAME_SERIES:
        return "game"
    if series in SPREAD_SERIES:
        return "spread"
    raise KeyError(f"series {series!r} is in neither rule's universe")


def frozen_section_sha256(text: str) -> str:
    """Reimplementation of the freeze record's awk command.

    ``awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' FILE | shasum
    -a 256``
    prints every line strictly between the two marker lines, each followed by a newline.
    """
    out: list[str] = []
    flag = False
    for line in text.split("\n"):
        if line == FROZEN_END:
            flag = False
        if flag:
            out.append(line)
        if line == FROZEN_BEGIN:
            flag = True
    return hashlib.sha256(("\n".join(out) + "\n").encode("utf-8")).hexdigest() if out else ""


class FrozenTextChanged(RuntimeError):
    """The frozen section no longer hashes to the pinned value; the scorer refuses to run."""


def verify_frozen(spec: RuleSpec, path: Path | None = None) -> str:
    p = path or spec.doc_path
    got = frozen_section_sha256(p.read_text(encoding="utf-8"))
    if got != spec.frozen_sha256:
        raise FrozenTextChanged(
            f"{spec.rule_id}: frozen section sha256 {got[:8]} != pinned {spec.frozen_sha256[:8]}"
        )
    return got
