"""Post text -> zero or more *claimed* facts. Pure: regex and a hand-written synonym table.

Contract (DECISIONS 2026-10-10 "confirm everyone", item 3)
----------------------------------------------------------
* Text is data. Nothing in a post can change behaviour, pick a handle, or alter config: the only
  outputs are :class:`Claim` records with a FIXED schema, and the only inputs are the text, the
  reporter's team scope and two look-up tables the caller loads from reviewed files.
* No LLM anywhere in this module, no I/O, no network, no imports beyond ``re``/``dataclasses``
  and the project's name normaliser (``tests/ingest/test_reporter_parse.py`` checks the imports).
* Regex-first. A phrase the patterns do not recognise yields ``claim_type = "unknown"`` with the
  post text as evidence, never a guess. A prompt-injection string is just such a phrase.
* Player names resolve through the reviewed alias table (``configs/kalshi_aliases.yaml``) and the
  roster of the handle's team. Unresolved stays unresolved (``player_id = None``) with the raw
  name kept; the caller decides what to do with it (the facts writer skips it and reports it).

Claim types: ``minutes_restriction`` (``minutes_limit`` = the cap when a number was given, the
upper end of a range), ``will_play``, ``out``, ``available``, ``game_time_decision``, ``unknown``.
A sentence that negates a phrase ("no minutes restriction", "not available") or asks a question
is ``unknown`` with the span kept, so a human sees it in the briefing instead of a model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from nba.parse.availability import normalize_name

CLAIM_TYPES: tuple[str, ...] = (
    "minutes_restriction",
    "will_play",
    "out",
    "available",
    "game_time_decision",
    "unknown",
)

#: Highest first: when one name matches several patterns in a post, the top one is kept.
_PRIORITY = {
    "minutes_restriction": 0,
    "out": 1,
    "game_time_decision": 2,
    "available": 3,
    "will_play": 4,
    "unknown": 9,
}


@dataclass(frozen=True)
class Claim:
    """One claimed fact. ``evidence`` is the matched span, trimmed to ``MAX_EVIDENCE`` chars."""

    player_name: str | None
    claim_type: str
    minutes_limit: int | None
    confidence: float
    evidence: str
    player_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


MAX_EVIDENCE = 240
MIN_MINUTES, MAX_MINUTES = 1, 48

# ----------------------------------------------------------------------------------------------
# Name tokens and the words that look like names but are not
# ----------------------------------------------------------------------------------------------

_NAME_TOKEN = r"[A-Z][A-Za-z'’.\-]*"
_SUFFIX = r"(?:\s+(?:Jr\.?|Sr\.?|II|III|IV))?"
NAME = rf"(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,2}}{_SUFFIX})"
PAREN = r"(?:\s*\([^()]{1,40}\))?"
_LEAD = rf"{NAME}{PAREN}\s*[:,\-\u2014]?\s*"

#: Capitalised words that are never a player name. Leading stop tokens are stripped from a
#: match ("Rockets Alperen Sengun" -> "Alperen Sengun"); a name made only of them is dropped.
_STOP_WORDS = (
    # team names and cities
    "hawks celtics nets hornets bulls cavaliers cavs mavericks mavs nuggets pistons "
    "warriors rockets pacers clippers lakers grizzlies heat bucks timberwolves wolves "
    "pelicans knicks thunder magic sixers 76ers suns blazers kings spurs raptors jazz "
    "wizards atlanta boston brooklyn charlotte chicago cleveland dallas denver detroit "
    "golden state houston indiana la los angeles memphis miami milwaukee minnesota "
    "new orleans york oklahoma city orlando philadelphia phoenix portland sacramento "
    "san antonio toronto utah washington "
    # days, months, generic post words
    "monday tuesday wednesday thursday friday saturday sunday january february march "
    "april may june july august september october november december tonight today "
    "tomorrow update updates injury report breaking sources source per via icymi coach "
    "the he she they his her no yes and but also so if when official officially nba "
    "espn athletic rt rotation lineup lineups starters bench out is are was will has "
    "have can does do did"
)
STOP_TOKENS: frozenset[str] = frozenset(_STOP_WORDS.split())

# ----------------------------------------------------------------------------------------------
# Phrase patterns (the hand-written synonym table)
# ----------------------------------------------------------------------------------------------

_APPROX = r"(?:~|around|about|roughly|approximately|under|up to|no more than|a max of|max)?\s*"
_NUM = rf"{_APPROX}(?P<lo>\d{{1,2}})(?:\s*(?:-|–|to)\s*(?P<hi>\d{{1,2}}))?"
_MIN = r"\s*(?:-|\s)*(?:min(?:ute)?s?|mins?\b)"
_BE = (
    r"(?:is|will be|is going to be|should be|remains|was|'s|’s|to be|will play|is playing|"
    r"plays|will start|is starting)?\s*"
)

_RESTRICTION = [
    # "on a 24-minute restriction", "on a minutes limit", "under a minutes restriction"
    rf"{_BE}(?:on|under)\s+(?:a\s+|the\s+)?(?:(?P<lo>\d{{1,2}})(?:\s*(?:-|–|to)\s*"
    rf"(?P<hi>\d{{1,2}}))?{_MIN}\s+|min(?:ute)?s?\s+)(?:restriction|limit|cap)\b",
    # "limited to ~20 minutes", "restricted to around 20-22 minutes", "capped at 25 min"
    rf"{_BE}(?:limited|restricted|held|capped)\s+(?:to|at)\s+{_NUM}{_MIN}",
    # "will play around 20 minutes", "expected to see ~18 minutes"
    rf"(?:will|is expected to|is slated to|should|expected to)\s+(?:play|see|get)\s+"
    rf"(?:only\s+)?{_NUM}{_MIN}",
    # "has a minutes restriction", "will have a minutes limit"
    rf"(?:has|will have|is dealing with)\s+(?:a\s+)?(?:(?P<lo>\d{{1,2}}){_MIN}\s+|"
    rf"min(?:ute)?s?\s+)(?:restriction|limit|cap)\b",
]
_OUT = [
    r"(?:is|has been|was|will be|remains|now|been)\s+(?:officially\s+)?(?:ruled\s+)?out\b"
    r"(?=\s*(?:tonight|today|tomorrow|vs|at|against|for|with|due|indefinitely|\(|[.,!;:]|$))",
    r"(?:has been\s+|was\s+|is\s+)?ruled\s+out\b",
    r"(?:will\s+not|won'?t|is\s+not\s+going\s+to|isn'?t\s+going\s+to)\s+(?:play|suit\s+up)\b",
    r"(?:is|has been|was)\s+(?:downgraded\s+to\s+out|sidelined)\b",
    r"(?:is\s+)?not\s+playing\s+(?:tonight|today)\b",
]
_GTD = [
    r"(?:is|will be|remains|is listed as|is considered|listed as)?\s*(?:a\s+)?"
    r"(?:game[-\s]?time\s+(?:decision|call)|GTD)\b",
]
_AVAILABLE = [
    r"(?:(?:is|has been|was|will be|could be|might be|should be|may be)\s+)?"
    r"(?:now\s+|officially\s+|upgraded\s+to\s+|listed\s+as\s+)?"
    r"available\b(?!\s+for\s+(?:comment|interview))",
    r"(?:has been\s+|is\s+|was\s+)?cleared\s+to\s+(?:play|return)\b",
]
_WILL_PLAY = [
    r"(?:will|is going to|is expected to|is set to|is slated to|is cleared to)\s+"
    r"(?:play|suit\s+up|start|be\s+in\s+the\s+lineup)\b",
    r"(?:is|'s|’s)\s+(?:good\s+to\s+go|a\s+go|playing\s+(?:tonight|today)|in\s+tonight|"
    r"in\s+the\s+lineup|active)\b",
]

_TABLE: list[tuple[str, re.Pattern[str], float]] = [
    *[("minutes_restriction", re.compile(_LEAD + rf"(?i:{p})"), 0.8) for p in _RESTRICTION],
    *[("out", re.compile(_LEAD + rf"(?i:{p})"), 0.8) for p in _OUT],
    *[("game_time_decision", re.compile(_LEAD + rf"(?i:{p})"), 0.7) for p in _GTD],
    *[("available", re.compile(_LEAD + rf"(?i:{p})"), 0.7) for p in _AVAILABLE],
    *[("will_play", re.compile(_LEAD + rf"(?i:{p})"), 0.7) for p in _WILL_PLAY],
]

#: Within this many characters before the phrase, these words negate it -> ``unknown``.
_NEGATION = re.compile(
    r"\b(?:no|not|never|without|unlike|isn'?t|doesn'?t|n'?t)\s*(?:his|the|a)?\s+$", re.I
)
_NEG_WORD = re.compile(r"\b(?:no|not|never|n'?t)\b", re.I)
_HEDGE = re.compile(r"\b(?:hoping|hopes|could|might|may|possibly|if|unless|reportedly)\b", re.I)
_RT = re.compile(r"^\s*RT\s+@(?P<handle>[A-Za-z0-9_]{1,15})\b")
_SENTENCE_END = re.compile(r"[.!?]\s|\n")


def retweeted_handle(text: str) -> str | None:
    """The original author's handle when ``text`` is a classic retweet (``RT @name: ...``)."""
    m = _RT.match(text or "")
    return m.group("handle") if m else None


def _clean_name(raw: str) -> str | None:
    tokens = raw.replace("’", "'").split()
    while tokens and tokens[0].lower().strip(".:'") in STOP_TOKENS:
        tokens = tokens[1:]
    if not tokens:
        return None
    name = " ".join(tokens).strip(" .:")
    if not name or all(t.lower().strip(".:'") in STOP_TOKENS for t in tokens):
        return None
    return name


def _sentence_of(text: str, start: int, end: int) -> str:
    left = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    right = _SENTENCE_END.search(text, end)
    return text[left : right.end() if right else len(text)].strip()


def _minutes(m: re.Match[str]) -> int | None:
    gd = m.groupdict()
    lo, hi = gd.get("lo"), gd.get("hi")
    if lo is None:
        return None
    val = int(hi) if hi is not None else int(lo)
    return val if MIN_MINUTES <= val <= MAX_MINUTES else None


def parse_post(text: str) -> list[Claim]:
    """Claims in ``text``. Empty text or a retweet yields nothing; text with no recognised
    phrase yields one ``unknown`` claim whose evidence is the (trimmed) text."""
    text = (text or "").strip()
    if not text or retweeted_handle(text):
        return []
    found: dict[str, Claim] = {}
    for claim_type, pattern, base in _TABLE:
        for m in pattern.finditer(text):
            name = _clean_name(m.group("name"))
            if name is None:
                continue
            key = normalize_name(name)
            sentence = _sentence_of(text, m.start(), m.end())
            before = text[max(0, m.start() - 24) : m.start()] + " "
            inside = text[m.end("name") : m.end()]
            negated = bool(_NEGATION.search(before)) or (
                claim_type != "out" and bool(_NEG_WORD.search(inside))
            )
            question = sentence.endswith("?")
            if negated or question:
                claim = Claim(name, "unknown", None, 0.0, sentence[:MAX_EVIDENCE])
            else:
                conf = base - (0.2 if _HEDGE.search(sentence) else 0.0)
                limit = _minutes(m) if claim_type == "minutes_restriction" else None
                claim = Claim(name, claim_type, limit, round(conf, 2), sentence[:MAX_EVIDENCE])
            prev = found.get(key)
            if prev is None or _PRIORITY[claim.claim_type] < _PRIORITY[prev.claim_type]:
                found[key] = claim
    if not found:
        return [Claim(None, "unknown", None, 0.0, text[:MAX_EVIDENCE])]
    return list(found.values())


# ----------------------------------------------------------------------------------------------
# Name resolution: reviewed aliases + the handle's team roster. Never guesses.
# ----------------------------------------------------------------------------------------------


def resolve_player(
    name: str,
    roster: list[tuple[str, int]] | None,
    aliases: dict[str, int] | None,
) -> int | None:
    """``player_id`` for ``name`` or None.

    ``roster`` is ``(display name, player_id)`` for the reporter's team (None = league-wide
    handle, no roster scope). ``aliases`` is the reviewed table keyed on normalised names.
    Order: exact roster name; reviewed alias (restricted to the roster when one is given);
    unique surname on the roster; unique "initial + surname" on the roster. Anything ambiguous
    or absent is None.
    """
    key = normalize_name(name)
    if not key:
        return None
    roster_ids = {pid for _, pid in roster} if roster else set()
    if roster:
        exact = {pid for n, pid in roster if normalize_name(n) == key}
        if len(exact) == 1:
            return exact.pop()
    if aliases and key in aliases:
        pid = aliases[key]
        if not roster or pid in roster_ids:
            return pid
    if not roster:
        return None
    parts = key.split()
    surname = parts[-1]
    initial = parts[0][0] if len(parts) == 2 and len(parts[0].rstrip(".")) == 1 else None
    if len(parts) == 1 or initial is not None:
        hits = set()
        for n, pid in roster:
            nk = normalize_name(n).split()
            if not nk or nk[-1] != surname:
                continue
            if initial is not None and nk[0][0] != initial:
                continue
            hits.add(pid)
        if len(hits) == 1:
            return hits.pop()
    return None


def resolve_claims(
    claims: list[Claim],
    roster: list[tuple[str, int]] | None,
    aliases: dict[str, int] | None,
) -> list[Claim]:
    """Attach ``player_id`` where it resolves; unresolved claims keep ``player_name`` as-is."""
    out = []
    for c in claims:
        pid = resolve_player(c.player_name, roster, aliases) if c.player_name else None
        out.append(replace(c, player_id=pid))
    return out
