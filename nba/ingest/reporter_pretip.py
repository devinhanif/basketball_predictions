"""Pre-tip claims from post text: filters F1-F8 (docs/prereg/REPORTER_FEED_LIVE.md, 5.2).

Why this module exists
----------------------
``reporter_parse.parse_post`` is built for the briefing: it recognises a phrase and reports it.
It cannot tell "will be on a minutes restriction tonight" from "was limited to 18 minutes in his
return Tuesday", and a next-morning recap then reads as a pre-tip cap whose N is last game's
minutes (reading log 2026-10-27, section 5.1: 3 of 5 recaps parsed as caps). The live rule reads
only the claims this module lets through. Precision over recall: a claim that fails a filter is
dropped, never repaired.

Pure: ``re``, ``dataclasses``, ``datetime`` and ``reporter_parse`` (name patterns). No I/O, no
network, no LLM. A change to this file or to ``reporter_parse.py`` is a new parser version (and a
new rule id once frozen): :func:`parser_fingerprint` hashes both.

Filters (the lexicons below are part of the fingerprint)
-------------------------------------------------------
F1 tense      reject a sentence with a past marker; a cap also needs a prospective marker.
F2 stat line  reject a sentence with a box-score count or a shooting line. A minutes range
              ("20-22 minutes") is not a shooting line (the spec's literal pattern would have
              rejected every range; clarified 2026-10-27).
F3 day        a named weekday or date must be the game's ET date; "tonight/today/this evening"
              needs the post's EVENING date to be the game's; "tomorrow" needs that date + 1 = the
              game date. The evening date is the ET wall-clock date of ``created_at``, minus one day
              before 06:00 ET local (a post written 00:00-06:00 ET belongs to the evening that just
              ended, so a "tonight" posted at 00:30 ET about the coming evening is dropped: a
              recall cost, by design, review m9).
              Without a game date any such reference is rejected.
F3b postgame  (``reporter_facts``) between the previous tip + 3 h and the next 06:00 ET a claim is
              kept only if it NAMES the attached game's day (``day_ref`` tomorrow / weekday / date);
              a postgame sentence with no day word is dropped as ``f3_postgame_no_day``.
F5 hedge      could, might, may, possibly, hoping, if, unless, "we'll see": kept, ``hedged=True``.
F6 number     N anywhere in the sentence (a number bound to the cap phrase first); a range gives
              the upper end; N outside [6, 40] drops the claim; two different unbound numbers give
              an unnumbered cap.
F7 lift       "no longer on a (minutes) restriction", "restriction lifted", "full go", "no
              limitations" become status ``cap_lifted``.
F8 scope      "for the next N games / this week / back-to-back": ``multi_game=True``, attached to
              the next game only.
F4 (the post must come after the previous game's window and not after our fetch) needs the
schedule and the fetch time, so it lives in ``reporter_facts.pretip_facts_for_posts``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from nba.ingest.reporter_parse import _LEAD, _clean_name, parse_post, retweeted_handle
from nba.parse.availability import normalize_name

PARSER_VERSION = "pretip-v1"
MIN_N, MAX_N = 6, 40

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WD = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

#: F1 past markers. "in his return" only counts with a weekday after it ("in his return tonight"
#: is a prospective phrase).
_PAST = re.compile(
    r"\b(?:was|were|played|logged|finished|finishes|went|scored|had|came off)\b|\bended up\b"
    rf"|\bin his return\s+(?:on\s+)?{_WD}\b"
    r"|\blast night\b|\byesterday\b|\bin the (?:win|loss)\b"
    rf"|\bin {_WD}'s\b|\bin {_WD}’s\b"
    r"|\bin game \d\b|\bthrough \d quarters?\b|\b\d+-\d+ (?:win|loss)\b",
    re.I,
)
#: F1 prospective markers (a cap needs one in the same sentence).
_PROSPECTIVE = re.compile(
    r"\b(?:will|is expected to|is set to|is slated to|remains on|is on|is going to|tonight|"
    r"today|this evening)\b",  # "vs" / "against" alone is a headline recap too (review M5)
    re.I,
)
#: F1b (clarification, ratify before freezing): a participle cap ("limited to 18 minutes") needs
#: an auxiliary right before it ("is limited to", "will be capped at"), unless the sentence also
#: carries a noun cue ("restriction", "limit", "cap") or "will play/see/get N minutes". Headline
#: recaps ("Green limited to 18 minutes vs. Denver") carry "vs" and no tense word and would pass F1.
_PARTICIPLE = re.compile(r"\b(?:limited|restricted|capped|held)\s+(?:to|at)\b", re.I)
_AUX_PARTICIPLE = re.compile(
    r"\b(?:is|are|will be|be|remains|should be|'s|’s)\s+(?:only\s+|being\s+)?"
    r"(?:limited|restricted|capped|held)\s+(?:to|at)\b",
    re.I,
)
_NOUN_CAP = re.compile(
    r"\b(?:restriction|limit|cap)\b|\b(?:play|see|get)\s+(?:only\s+)?[~\d]", re.I
)
#: F2.
_STAT_COUNT = re.compile(
    r"\b\d+\s*(?:points|pts|rebounds|boards|reb|assists|ast|steals|blocks|3s|threes)\b"
    r"|\bscores?\s+\d+\b|\b\d+\s+and\s+\d+\b|\bin\s+\d+\s+min(?:ute)?s\b",
    re.I,
)
_SHOOTING = re.compile(r"\b\d+-(?:of-)?\d+\b(?!\s*-?\s*min)", re.I)
#: F3.
_WEEKDAY_REF = re.compile(rf"\b({_WD})\b", re.I)
_MONTH_DAY = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
_NUM_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_TONIGHT = re.compile(r"\b(?:tonight|today|this evening)\b", re.I)
_TOMORROW = re.compile(r"\btomorrow\b", re.I)
#: F5.
_HEDGE = re.compile(r"\b(?:could|might|may|possibly|hoping|if|unless)\b|\bwe(?:'|’)ll see\b", re.I)
#: F8.
_MULTI = re.compile(
    r"\b(?:for|over) the next (?:\w+\s+)?games?\b|\bnext (?:\d+|few|couple|two|three|four|five) "
    r"games\b|\bthis week\b|\bback-to-backs?\b",
    re.I,
)
#: F7.
_LIFT_BODY = (
    r"no\s+longer\s+(?:on|under|has|have|having)?\s*(?:a\s+|an\s+|any\s+)?(?:minutes?[-\s]+)?"
    r"(?:restriction|limit|cap)s?\b"
    r"|(?:minutes?[-\s]+)?(?:restriction|limit)s?\s+(?:has\s+been\s+|have\s+been\s+|is\s+|are\s+)?"
    r"(?:lifted|removed)\b"
    r"|(?:a\s+)?full[-\s]go\b"
    r"|no\s+(?:minutes?\s+)?(?:limitations?|restrictions?|limits?)\b"
)
_LIFT = re.compile(_LEAD + rf"(?i:(?:[a-z'’]+\s+){{0,3}}?(?:{_LIFT_BODY}))")
#: F6.
_APPROX = r"(?:~|around|about|roughly|approximately|under|up to|no more than|a max of|max(?: of)?)?"
_RANGE = r"(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?"
_MIN = r"\s*(?:-|\s)*min(?:ute)?s?\b"
_CUE_NUMS = [
    re.compile(rf"{_RANGE}[-\s]*min(?:ute)?s?[-\s]*(?:restriction|limit|cap)\b", re.I),
    re.compile(
        rf"(?:limited|restricted|capped|held)\s+(?:to|at)\s+{_APPROX}\s*{_RANGE}{_MIN}", re.I
    ),
    re.compile(rf"(?:play|see|get)\s+(?:only\s+)?{_APPROX}\s*{_RANGE}{_MIN}", re.I),
]
_ANY_NUM = re.compile(rf"{_APPROX}\s*{_RANGE}{_MIN}", re.I)

#: a fixed game date, or a function from the player's name to the ET date of the game that
#: player's team plays next (league-wide handles name players of many teams)
GameDate = date | None | Callable[[str], date | None]

_ABBREV_DOT = re.compile(
    r"\b(vs|Jr|Sr|St|Mr|Dr)\.|\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.(?=\s+\d)"
)
_POSSESSIVE = re.compile(r"['’]s$")
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

STATUS_OF_CLAIM = {
    "out": "out",
    "will_play": "will_play",
    "available": "available",
    "game_time_decision": "game_time_decision",
}
STATUSES = ("out", "will_play", "available", "game_time_decision", "cap_lifted")


@dataclass(frozen=True)
class PretipClaim:
    """One claim in one sentence. ``reject`` names the filter that dropped it (None = kept)."""

    player_name: str
    kind: str  # "cap" | "status"
    status: str | None  # cap: None; status: one of STATUSES
    n: int | None  # cap: the upper end of a range
    n_lo: int | None
    n_hi: int | None
    hedged: bool
    multi_game: bool
    sentence: str
    reject: str | None = None
    #: what the sentence says about the day: date | weekday | tomorrow | tonight | none (the
    #: strongest reference present). Stored in value_text so the scorer can audit without the text.
    day_ref: str = "none"

    @property
    def kept(self) -> bool:
        return self.reject is None

    @property
    def qualifies(self) -> bool:
        """Kept and not hedged: the only claims a pass-carrying test reads."""
        return self.reject is None and not self.hedged

    @property
    def predicate(self) -> str:
        return "reporter_minutes_cap" if self.kind == "cap" else "reporter_status"

    @property
    def evidence_sha256(self) -> str:
        return hashlib.sha256(" ".join(self.sentence.split()).encode()).hexdigest()


def sentences(text: str) -> list[str]:
    text = _ABBREV_DOT.sub(lambda m: m.group(1) or m.group(2), text or "")
    return [s.strip() for s in _SPLIT.split(text) if s and s.strip()]


def day_ref(s: str) -> str:
    """The strongest day reference in ``s``: date > weekday > tomorrow > tonight > none."""
    if _MONTH_DAY.search(s) or _NUM_DATE.search(s):
        return "date"
    if _WEEKDAY_REF.search(s):
        return "weekday"
    if _TOMORROW.search(s):
        return "tomorrow"
    return "tonight" if _TONIGHT.search(s) else "none"


NAMES_NEXT_DAY = ("date", "weekday", "tomorrow")  # day_ref values that name a day beyond tonight


def _day_ref_ok(s: str, game_date: date | None, posted_date: date | None) -> bool:
    """F3."""
    wds = [m.group(1).lower() for m in _WEEKDAY_REF.finditer(s)]
    mds = [(m.group(1)[:3].lower(), int(m.group(2))) for m in _MONTH_DAY.finditer(s)]
    nds = [(int(m.group(1)), int(m.group(2))) for m in _NUM_DATE.finditer(s)]
    tonight, tomorrow = bool(_TONIGHT.search(s)), bool(_TOMORROW.search(s))
    if not (wds or mds or nds or tonight or tomorrow):
        return True
    if game_date is None:
        return False
    if any(w != _WEEKDAYS[game_date.weekday()] for w in wds):
        return False
    if any(m != _MONTHS[game_date.month - 1] or d != game_date.day for m, d in mds):
        return False
    if any(m != game_date.month or d != game_date.day for m, d in nds):
        return False
    if tonight and posted_date != game_date:
        return False
    return not (tomorrow and (posted_date is None or posted_date + timedelta(days=1) != game_date))


def _number(sentence: str) -> tuple[int | None, int | None, int | None, bool]:
    """``(n, n_lo, n_hi, in_range)`` per F6; ``n`` None = unnumbered; ``in_range`` False = drop."""
    for pat in _CUE_NUMS:
        found = [(m.group(1), m.group(2)) for m in pat.finditer(sentence)]
        if found:
            break
    else:
        found = [(m.group(1), m.group(2)) for m in _ANY_NUM.finditer(sentence)]
    his = {int(hi or lo) for lo, hi in found}
    if len(his) != 1:
        return None, None, None, True
    lo, hi = found[0]
    n = int(hi or lo)
    if not MIN_N <= n <= MAX_N:
        return None, None, None, False
    return n, int(lo), n, True


def _reject(
    sentence: str, kind: str, game_date: date | None, posted_date: date | None
) -> str | None:
    if _PAST.search(sentence):
        return "f1_past"
    if kind == "cap" and not _PROSPECTIVE.search(sentence):
        return "f1_not_prospective"
    if (
        kind == "cap"
        and _PARTICIPLE.search(sentence)
        and not _AUX_PARTICIPLE.search(sentence)
        and not _NOUN_CAP.search(sentence)
    ):
        return "f1b_bare_participle"
    if _STAT_COUNT.search(sentence) or _SHOOTING.search(sentence):
        return "f2_stat_line"
    if not _day_ref_ok(sentence, game_date, posted_date):
        return "f3_day"
    return None


def _claims_in_sentence(
    sentence: str, game_date_of: GameDate, posted_date: date | None
) -> list[PretipClaim]:
    out: list[PretipClaim] = []

    def game_date(name: str) -> date | None:
        return game_date_of(name) if callable(game_date_of) else game_date_of

    hedged = bool(_HEDGE.search(sentence))
    multi = bool(_MULTI.search(sentence))
    lifted: set[str] = set()
    for m in _LIFT.finditer(sentence):
        name = _clean_name(m.group("name"))
        if name is None:
            continue
        name = _POSSESSIVE.sub("", name)
        lifted.add(normalize_name(name))
        out.append(
            PretipClaim(
                name,
                "status",
                "cap_lifted",
                None,
                None,
                None,
                hedged,
                multi,
                sentence,
                _reject(sentence, "status", game_date(name), posted_date),
            )
        )
    for c in parse_post(sentence):
        if c.player_name is None or c.claim_type in ("unknown", "starting"):
            continue
        if normalize_name(c.player_name) in lifted:
            continue
        if c.claim_type == "minutes_restriction":
            n, lo, hi, in_range = _number(sentence)
            rej = _reject(sentence, "cap", game_date(c.player_name), posted_date)
            if rej is None and not in_range:
                rej = "f6_range"
            out.append(
                PretipClaim(c.player_name, "cap", None, n, lo, hi, hedged, multi, sentence, rej)
            )
        elif c.claim_type in STATUS_OF_CLAIM:
            rej = _reject(sentence, "status", game_date(c.player_name), posted_date)
            out.append(
                PretipClaim(
                    c.player_name,
                    "status",
                    STATUS_OF_CLAIM[c.claim_type],
                    None,
                    None,
                    None,
                    hedged,
                    multi,
                    sentence,
                    rej,
                )
            )
    # day_ref per CLAUSE (round 4, N11): "X is on a 20-minute restriction, and Y will sit tomorrow"
    # must not license X's clause with Y's day word
    return [replace(c, day_ref=day_ref(_clause_of(sentence, c.player_name))) for c in out]


_CLAUSE_SPLIT = re.compile(r"\s*;\s*|,\s+(?:and|but)\s+|\s+but\s+", re.I)


def _clause_of(sentence: str, name: str) -> str:
    """The clause of ``sentence`` that holds ``name`` (split on ';', ', and', ', but', ' but ');
    the whole sentence if the name is in none of them or the sentence is one clause."""
    low = name.lower()
    for part in _CLAUSE_SPLIT.split(sentence):
        if low in part.lower():
            return part
    return sentence


def parse_pretip(
    text: str, *, game_date_et: GameDate, posted_date_et: date | None
) -> list[PretipClaim]:
    """Every recognised claim in ``text`` with the filter that dropped it, if any. Retweets and
    empty text give nothing. ``game_date_et`` is the ET date of the game the post is attached to;
    ``posted_date_et`` the post's EVENING date: the ET wall-clock date of
    ``created_at``, minus one day before 06:00 ET local (``reporter_facts.evening_date``)."""
    text = (text or "").strip()
    if not text or retweeted_handle(text):
        return []
    out: list[PretipClaim] = []
    for s in sentences(text):
        out.extend(_claims_in_sentence(s, game_date_et, posted_date_et))
    return out


#: Every file whose content decides which claim a post becomes and which player and game it is
#: attached to (adversary review M6): the filters, the phrase patterns, the fact builder, the name
#: normaliser, and the two reviewed name tables.
FINGERPRINT_FILES = (
    "nba/ingest/reporter_pretip.py",
    "nba/ingest/reporter_parse.py",
    "nba/ingest/reporter_facts.py",
    "nba/parse/availability.py",
    "nba/kalshi/aliases.py",
    "nba/props/rosters.py",
    "configs/reporter_nicknames.yaml",
    "configs/kalshi_aliases.yaml",
)


def parser_fingerprint(root: Path | None = None) -> str:
    """sha256 over :data:`FINGERPRINT_FILES` (path, length and bytes of each). ``root`` is the repo
    root (default: this checkout). A missing file raises: a frozen parser must be whole."""
    base = root or Path(__file__).resolve().parents[2]
    h = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        data = (base / rel).read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()
