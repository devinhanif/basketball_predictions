# ruff: noqa: E501
"""Halftime entry snapshot from the write-once live capture (read-only; no network, no model).

Entry definition of the draft rule ``docs/prereg/HALFTIME_OVERS_VS_PRICE.md`` section 2, exactly:

1. halftime start ``t_ht`` = ``received_at`` of the first ESPN scoreboard file with the game at
   ``STATUS_HALFTIME``; decision time ``t* = t_ht + 120 s``; a game already in period 3 at ``t*`` has
   no entry;
2. quote = the first Kalshi page of a series received in ``[t*, t* + 180 s]`` (and every page of that
   series within 30 s of it);
3. state = the latest ESPN summary received at or before that page, at most 150 s older, whose header
   still says halftime;
4. market filter: ``strike_type == greater``, integer strike, ``status == active``, Yes ask 2-97 c with
   top-of-book size >= 1; player matched by folded name to exactly one box row, first-half minutes > 0,
   not ejected, line not already reached, strike on the T508 grid.

Every time is the manifest ``received_at``, never a body field. ``as_of`` hides every file received
after it, so a live tick sees exactly what existed then and a replay can ask "what was knowable at X".
Over / Yes only: there is no code for the other contract here. This module reads files; it never writes.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nba.markets.halftime_model import GRID

SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
ESPN_COL = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3m": "3PT"}
ESPN_TO_KALSHI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}

#: The rule's constants; changing one is a new rule.
DECISION_DELAY_S = 120
QUOTE_WINDOW_S = 180
STATE_MAX_AGE_S = 150
SAME_TICK_S = 30
TICK_CLOSE_MARGIN_S = 5  # manifest write latency: a page received at +29.7 s may be listed later
ASK_MIN_C, ASK_MAX_C = 2, 97
MIN_DEPTH = 1.0


def utc(ts: datetime) -> datetime:
    """Naive datetimes are UTC (the live capture and the JARVIS clock both are)."""
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts  # noqa: UP017


def parse_ts(s: str) -> datetime:
    return utc(datetime.fromisoformat(s))


def fold(name: str) -> str:
    """Lower-case ASCII letters only, generational suffixes dropped (Jr., Sr., II, III, IV)."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def strike_of(market: dict[str, Any]) -> int | None:
    fs = market.get("floor_strike")
    if fs is None or market.get("strike_type") != "greater":
        return None
    k = float(fs) + 0.5
    return int(k) if abs(k - round(k)) < 1e-9 else None


def player_of(market: dict[str, Any]) -> str | None:
    title = str(market.get("title") or market.get("yes_sub_title") or "")
    m = re.match(r"^(.*?):\s*\d+\+", title)
    return m.group(1).strip() if m else None


def to_f(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def event_key(event_ticker: str) -> str | None:
    parts = event_ticker.split("-")
    return parts[1] if len(parts) >= 2 else None


def read_gz(p: Path) -> Any:
    with gzip.open(p) as f:
        return json.loads(f.read())


Entry = tuple[datetime, Path, str]  # received_at, file, key


def load_manifest(night: Path, as_of: datetime | None = None) -> dict[str, list[Entry]]:
    by: dict[str, list[Entry]] = defaultdict(list)
    with (night / "manifest.jsonl").open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a torn last line of a file being appended
            if r.get("http_status") != 200:
                continue
            ts = parse_ts(r["received_at"])
            if as_of is not None and ts > utc(as_of):
                continue
            by[r["source"]].append((ts, night / r["path"], r["key"]))
    for v in by.values():
        v.sort(key=lambda t: t[0])
    return by


@dataclass
class Halftime:
    event_id: str
    away: str  # Kalshi abbreviations
    home: str
    away_espn: str
    home_espn: str
    ht_first: datetime
    q3_first: datetime | None
    away_score: int | None
    home_score: int | None
    ht_file: str = ""
    start_observed: bool = (
        True  # an in-progress period-2 tick within 150 s before the halftime tick
    )

    @property
    def t_star(self) -> datetime:
        return self.ht_first + timedelta(seconds=DECISION_DELAY_S)

    @property
    def game(self) -> str:
        return f"{self.away}@{self.home}"


PRIOR_TICK_MAX_S = 150  # the tick before the halftime tick must be this recent (and in period 2)


def clock_is_zero(display_clock: Any) -> bool:
    """ESPN game clock at 0: "0.0", "0:00", "0". Anything else (11:04, 12.8) is not the end of a period."""
    txt = str(display_clock or "").strip()
    try:
        if ":" in txt:
            m, sec = txt.split(":", 1)
            return float(m) * 60 + float(sec) == 0
        return float(txt) == 0
    except ValueError:
        return False


def is_halftime_state(name: Any, period: Any, display_clock: Any) -> bool:
    """The halftime definition (amendment A1): status HALFTIME, period 2, clock 0. ESPN once sent
    STATUS_HALFTIME with period 2 and 11:04 left (a glitch, 2026-10-26 MIN@CLE); that is not halftime."""
    try:
        p = int(period or 0)
    except (TypeError, ValueError):
        return False
    return name == "STATUS_HALFTIME" and p == 2 and clock_is_zero(display_clock)


def halftimes(scoreboard: list[Entry]) -> dict[str, Halftime]:
    out: dict[str, Halftime] = {}
    prev: dict[str, tuple[datetime, int]] = {}
    for ts, path, _ in scoreboard:
        try:
            body = read_gz(path)
        except (OSError, ValueError):
            continue
        for ev in body.get("events", []):
            eid = str(ev.get("id"))
            st = ev.get("status", {})
            name = st.get("type", {}).get("name")
            period = int(st.get("period") or 0)
            if eid not in out and is_halftime_state(name, period, st.get("displayClock")):
                comp = ev["competitions"][0]
                teams = {c["homeAway"]: c for c in comp["competitors"]}
                a = teams["away"]["team"]["abbreviation"]
                h = teams["home"]["team"]["abbreviation"]
                pv = prev.get(eid)
                observed = (
                    pv is not None
                    and pv[1] == 2
                    and (ts - pv[0]).total_seconds() <= PRIOR_TICK_MAX_S
                )
                out[eid] = Halftime(
                    eid,
                    ESPN_TO_KALSHI.get(a, a),
                    ESPN_TO_KALSHI.get(h, h),
                    a,
                    h,
                    ts,
                    None,
                    _int(teams["away"].get("score")),
                    _int(teams["home"].get("score")),
                    str(path.name),
                    observed,
                )
            ht = out.get(eid)
            if ht is not None and ht.q3_first is None and ts > ht.ht_first and period >= 3:
                ht.q3_first = ts  # first later tick with period >= 3: the second half has started
            prev[eid] = (ts, period)
    return out


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def box_players(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """folded name -> box rows (a list: two players with one folded name are ambiguous)."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for team in summary.get("boxscore", {}).get("players", []):
        abbr = team.get("team", {}).get("abbreviation", "")
        for block in team.get("statistics", []):
            labels = block.get("labels") or block.get("names") or []
            for a in block.get("athletes", []):
                stats = a.get("stats") or []
                row = dict(zip(labels, stats, strict=False)) if stats else {}
                out[fold(a["athlete"]["displayName"])].append(
                    {
                        "name": a["athlete"]["displayName"],
                        "team": abbr,
                        "row": row,
                        "starter": bool(a.get("starter")),
                        "dnp": bool(a.get("didNotPlay")) or not stats,
                        "ejected": bool(a.get("ejected")),
                    }
                )
    return out


def stat_value(row: dict[str, str], stat: str) -> int | None:
    v = row.get(ESPN_COL[stat])
    if v in (None, "", "--"):
        return None
    try:
        return int(str(v).split("-")[0])
    except ValueError:
        return None


def int_field(row: dict[str, str], col: str) -> int | None:
    try:
        return int(str(row.get(col)))
    except (TypeError, ValueError):
        return None


def summary_status(summary: dict[str, Any]) -> str | None:
    try:
        return str(summary["header"]["competitions"][0]["status"]["type"]["name"])
    except (KeyError, IndexError, TypeError):
        return None


def summary_is_halftime(summary: dict[str, Any]) -> bool:
    """Status HALFTIME and period 2 and clock 0 on the summary header too (amendment A1)."""
    try:
        st = summary["header"]["competitions"][0]["status"]
        return is_halftime_state(st["type"]["name"], st.get("period"), st.get("displayClock"))
    except (KeyError, IndexError, TypeError):
        return False


@dataclass
class Candidate:
    """One on-grid, tradeable Yes quote on a player who has played in the first half."""

    series: str
    stat: str
    ticker: str
    player_name: str  # Kalshi spelling
    espn_name: str
    team: str  # ESPN team abbreviation
    k: int
    ask_c: int
    bid_c: int | None
    ask_size: float
    cur: int
    minutes: int  # ESPN integer MIN
    starter: int
    fouls: int
    margin: int  # player's team lead at the half (own - opponent)


@dataclass
class Snapshot:
    event_id: str
    game: str
    series: str
    t_ht: datetime
    t_star: datetime
    quote_received_at: datetime
    quote_file: str
    state_received_at: datetime
    state_file: str
    candidates: list[Candidate] = field(default_factory=list)
    excluded: Counter[str] = field(default_factory=Counter)

    @property
    def known_at(self) -> datetime:
        """The earliest instant every input of this snapshot existed."""
        return max(self.quote_received_at, self.state_received_at)


def state_before(summaries: list[Entry], t: datetime) -> Entry | None:
    best = None
    for item in summaries:
        if item[0] <= t:
            best = item
        else:
            break
    return best


class NightReader:
    """Index of one capture night, as of ``as_of`` (None = everything)."""

    def __init__(self, night: Path, as_of: datetime | None = None) -> None:
        self.night = night
        self.as_of = None if as_of is None else utc(as_of)
        man = load_manifest(night, self.as_of)
        self.hts = halftimes(man.get("espn_scoreboard", []))
        self.summaries: dict[str, list[Entry]] = defaultdict(list)
        for e in man.get("espn_summary", []):
            self.summaries[e[2]].append(e)
        self.kalshi: dict[str, list[Entry]] = defaultdict(list)
        for e in man.get("kalshi", []):
            series = e[2].split("_p")[0]
            if series in SERIES:
                self.kalshi[series].append(e)

    def second_half_started(self, ht: Halftime) -> bool:
        return ht.q3_first is not None

    def snapshot(self, ht: Halftime, series: str, counts: Counter[str]) -> Snapshot | None:
        """The entry snapshot of one game and series, or None with the reason counted."""
        t_star = ht.t_star
        if not ht.start_observed:
            counts["ht_start_unobserved"] += 1
            return None
        if ht.q3_first is not None and ht.q3_first <= t_star:
            counts["q3_before_decision_time"] += 1
            return None
        hi = t_star + timedelta(seconds=QUOTE_WINDOW_S)
        pages = [p for p in self.kalshi.get(series, []) if t_star <= p[0] <= hi]
        if not pages:
            counts["series_no_page_in_window"] += 1
            return None
        first_ts = pages[0][0]
        if self.as_of is not None and self.as_of < first_ts + timedelta(
            seconds=SAME_TICK_S + TICK_CLOSE_MARGIN_S
        ):
            counts["tick_not_closed"] += (
                1  # the pages of one tick may still be arriving (amendment A2)
            )
            return None
        tick = [p for p in pages if (p[0] - first_ts).total_seconds() < SAME_TICK_S]
        st = state_before(self.summaries.get(ht.event_id, []), first_ts)
        if st is None or (first_ts - st[0]).total_seconds() > STATE_MAX_AGE_S:
            counts["series_state_stale_or_missing"] += 1
            return None
        summary = read_gz(st[1])
        if not summary_is_halftime(summary):
            counts["series_state_not_halftime"] += 1
            return None
        snap = Snapshot(
            ht.event_id,
            ht.game,
            series,
            ht.ht_first,
            t_star,
            first_ts,
            str(tick[0][1].name),
            st[0],
            str(st[1].name),
        )
        box = box_players(summary)
        stat = SERIES[series]
        for _, path, _key in tick:
            for m in read_gz(path).get("markets", []):
                self._market(ht, m, series, stat, box, snap)
        return snap

    def _market(
        self,
        ht: Halftime,
        m: dict[str, Any],
        series: str,
        stat: str,
        box: dict[str, list[dict[str, Any]]],
        snap: Snapshot,
    ) -> None:
        ek = event_key(str(m.get("event_ticker", "")))
        if ek is None or not ek.endswith(ht.away + ht.home):
            return
        k = strike_of(m)
        name = player_of(m)
        if k is None or name is None:
            return
        x = snap.excluded
        x["markets_at_snapshot"] += 1
        if m.get("status") != "active":
            x["x_not_active"] += 1
            return
        ask = to_f(m.get("yes_ask_dollars"))
        size = to_f(m.get("yes_ask_size_fp")) or 0.0
        if ask is None or not (ASK_MIN_C <= round(100 * ask) <= ASK_MAX_C) or size < MIN_DEPTH:
            x["x_no_tradeable_ask"] += 1
            return
        rows = box.get(fold(name), [])
        if not rows:
            x["x_player_unmatched"] += 1
            x["unmatched_names:" + name] += 1
            return
        if len(rows) > 1:
            x["x_player_ambiguous"] += 1
            return
        p = rows[0]
        mins = int_field(p["row"], "MIN")
        if p["dnp"] or not mins:
            x["x_dnp_first_half"] += 1  # 0 first-half minutes: never scored
            return
        if p["ejected"]:
            x["x_ejected"] += 1
            return
        cur = stat_value(p["row"], stat)
        fouls = int_field(p["row"], "PF")
        if cur is None or fouls is None:
            x["x_stat_missing"] += 1
            return
        if cur >= k:
            x["x_already_resolved"] += 1
            return
        if k not in GRID[stat]:
            x["x_off_t508_grid"] += 1
            return
        lead = self._lead(ht, p["team"])
        if lead is None:
            x["x_score_missing"] += 1
            return
        bid = to_f(m.get("yes_bid_dollars"))
        snap.excluded["scored_candidates"] += 1
        snap.candidates.append(
            Candidate(
                series,
                stat,
                str(m.get("ticker")),
                name,
                str(p["name"]),
                str(p["team"]),
                k,
                round(100 * ask),
                None if bid is None else round(100 * bid),
                size,
                cur,
                mins,
                int(p["starter"]),
                fouls,
                lead,
            )
        )

    @staticmethod
    def _lead(ht: Halftime, team: str) -> int | None:
        if ht.away_score is None or ht.home_score is None:
            return None
        if team == ht.away_espn:
            return ht.away_score - ht.home_score
        if team == ht.home_espn:
            return ht.home_score - ht.away_score
        return None
