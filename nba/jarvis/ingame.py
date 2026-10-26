"""Live in-game situations from the live capture (read-only): benched starters, the bench window,
foul trouble and starters returning.

Sources (``data/raw/live_<ET date>/``, written about once a minute by the live capture):
``espn_summary/<espn game id>/*.json.gz`` (score, period, clock, per-player box score) and
``kalshi/KXNBA{PTS,REB,AST,3PT}_p0/*.json.gz`` (live YES bid/ask of the player-prop ladders); the
Kalshi snapshot store is the fallback for prices. All ESPN and Kalshi text is DATA: names are
normalised and matched exactly (an unmatched name is skipped, never guessed), and nothing fetched
changes a number or a decision except through the parsed fields below.

A signal is a fact about the game ("his minutes are frozen"), not an edge. Where a bet is named
it is label ``none`` (entertainment, EV unknown) at the $1 ladder size, with what it pays if
right; nothing here compares a price to fair, so nothing here says edge.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from nba.jarvis.config import JarvisConfig
from nba.jarvis.live import ESPN_ABBR

KALSHI_SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
STAT_WORD = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "threes"}
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name: str) -> str:
    """Casefolded, accent-free, punctuation-free name without a generational suffix."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if t not in _SUFFIX]
    return " ".join(toks)


def clean_name(name: str) -> str:
    """Fetched display name -> short printable text (letters of any script, digits, space . ' -).
    Anything else, including URLs and markup, becomes a space; it is data, never an instruction."""
    s = re.sub(r"https?://\S+", " ", str(name))
    s = "".join(c if (c.isalnum() or c in " .'-") else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()[:40]


# ----------------------------------------------------------------------------------- ESPN


@dataclass(frozen=True)
class PlayerLine:
    espn_id: str
    name: str
    team: str  # our abbreviation
    starter: bool
    active: bool
    position: str
    minutes: int | None
    pts: int
    reb: int
    ast: int
    fg3m: int
    pf: int

    def stat(self, key: str) -> int:
        return int(getattr(self, key))


@dataclass(frozen=True)
class Snapshot:
    ts: datetime  # naive UTC, from the file name
    espn_game: str
    away: str
    home: str
    away_score: int
    home_score: int
    period: int
    clock_s: float  # seconds left in the period
    state: str  # pre | in | post
    status: str
    players: tuple[PlayerLine, ...] = field(default_factory=tuple)

    @property
    def margin(self) -> int:
        return abs(self.home_score - self.away_score)

    @property
    def leader(self) -> str:
        return self.home if self.home_score > self.away_score else self.away

    @property
    def elapsed_s(self) -> float:
        """Game seconds played (regulation 4 x 720, overtime 300 each)."""
        if self.period <= 4:
            return (self.period - 1) * 720 + (720 - self.clock_s)
        return 2880 + (self.period - 5) * 300 + (300 - self.clock_s)

    @property
    def remaining_s(self) -> float:
        """Seconds left in regulation or in the current overtime."""
        if self.period < 4:
            return (4 - self.period) * 720 + self.clock_s
        return self.clock_s

    @property
    def matchup(self) -> str:
        return f"{self.away}@{self.home}"

    def when(self) -> str:
        m, s = divmod(int(self.clock_s), 60)
        q = f"Q{self.period}" if self.period <= 4 else f"OT{self.period - 4}"
        return f"{q} {m}:{s:02d}"


def parse_clock(v: Any) -> float:
    """'11:05' -> 665, '42.4' -> 42.4, '0.0' -> 0; anything else -> 0."""
    s = str(v or "").strip()
    try:
        if ":" in s:
            m, sec = s.split(":", 1)
            return int(m) * 60 + float(sec)
        return float(s)
    except ValueError:
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(str(v).split("-")[0])
    except ValueError:
        return 0


def _our(abbr: str) -> str:
    return ESPN_ABBR.get(abbr, abbr)


def parse_snapshot(body: dict[str, Any], ts: datetime, espn_game: str) -> Snapshot | None:
    try:
        comp = body["header"]["competitions"][0]
        by = {c["homeAway"]: c for c in comp["competitors"]}
        st = comp["status"]
        away, home = (
            _our(by["away"]["team"]["abbreviation"]),
            _our(by["home"]["team"]["abbreviation"]),
        )
        players: list[PlayerLine] = []
        for team in (body.get("boxscore") or {}).get("players", []):
            ab = _our(str(team["team"]["abbreviation"]))
            for grp in team.get("statistics", [])[:1]:
                labels = list(grp.get("labels", []))
                idx = {lab: i for i, lab in enumerate(labels)}
                for a in grp.get("athletes", []):
                    stats = a.get("stats") or []
                    if not stats or "MIN" not in idx:
                        continue

                    def g(lab: str, stats: list[str] = stats, idx: dict[str, int] = idx) -> str:
                        i = idx.get(lab)
                        return stats[i] if i is not None and i < len(stats) else ""

                    mins = g("MIN")
                    ath = a["athlete"]
                    players.append(
                        PlayerLine(
                            str(ath["id"]),
                            clean_name(ath["displayName"]),
                            ab,
                            bool(a.get("starter")),
                            bool(a.get("active")),
                            str(ath.get("position", {}).get("abbreviation", "")),
                            int(mins) if mins.isdigit() else None,
                            _int(g("PTS")),
                            _int(g("REB")),
                            _int(g("AST")),
                            _int(g("3PT")),
                            _int(g("PF")),
                        )  # fmt: skip
                    )
        return Snapshot(
            ts, espn_game, away, home,
            _int(by["away"].get("score")), _int(by["home"].get("score")),
            int(st.get("period") or comp.get("status", {}).get("period") or 0),
            parse_clock(st.get("displayClock")),
            str(st.get("type", {}).get("state", "")), str(st.get("type", {}).get("name", "")),
            tuple(players),
        )  # fmt: skip
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def file_ts(name: str) -> datetime | None:
    try:
        return datetime.strptime(name[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def load_snapshots(
    root: Path, slate: date, until: datetime, lookback_min: float
) -> dict[str, list[Snapshot]]:
    """Per ESPN game id, the snapshots in ``(until - lookback, until]``, oldest first. Files after
    ``until`` are ignored, so a replay never sees the future."""
    base = root / "data" / "raw" / f"live_{slate.isoformat()}" / "espn_summary"
    out: dict[str, list[Snapshot]] = {}
    if not base.exists():
        return out
    lo = until.replace(tzinfo=UTC).timestamp() - lookback_min * 60.0
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        snaps: list[Snapshot] = []
        for f in sorted(d.glob("*.json.gz")):
            ts = file_ts(f.name)
            if ts is None or ts > until or ts.replace(tzinfo=UTC).timestamp() < lo:
                continue
            try:
                with gzip.open(f) as fh:
                    body = json.loads(fh.read())
            except (OSError, ValueError):
                continue
            s = parse_snapshot(body, ts, d.name)
            if s is not None:
                snaps.append(s)
        if snaps:
            out[d.name] = snaps
    return out


def load_snapshots_from(
    raw: dict[str, Any], espn_game: str, until: datetime, lookback_min: float
) -> list[Snapshot]:
    """Same window as ``load_snapshots`` over already-parsed JSON bodies keyed ``YYYYmmddTHHMMSS``
    (fixtures and replays)."""
    lo = until.replace(tzinfo=UTC).timestamp() - lookback_min * 60.0
    out = []
    for key in sorted(raw):
        t = datetime.strptime(key[:15], "%Y%m%dT%H%M%S")
        if t > until or t.replace(tzinfo=UTC).timestamp() < lo:
            continue
        s = parse_snapshot(raw[key], t, espn_game)
        if s is not None:
            out.append(s)
    return out


# ----------------------------------------------------------------------------------- Kalshi


@dataclass(frozen=True)
class LiveMarket:
    ticker: str
    name: str  # normalised player name
    display: str
    stat: str
    threshold: int  # "N+" -> N
    yes_bid: float | None
    yes_ask: float | None
    ts: datetime
    source: str  # capture | snapshot_store
    series: str = ""  # KXNBAPTS ...: the series the order fee is priced under


def _price(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0.0 <= x <= 1.0 else None


def kalshi_live(
    root: Path, slate: date, away: str, home: str, until: datetime, max_age_min: float
) -> list[LiveMarket]:
    """The newest capture file at or before ``until`` per prop series, filtered to this game."""
    out: list[LiveMarket] = []
    base = root / "data" / "raw" / f"live_{slate.isoformat()}" / "kalshi"
    for series, stat in KALSHI_SERIES.items():
        d = base / f"{series}_p0"
        if not d.exists():
            continue
        best: tuple[datetime, Path] | None = None
        for f in d.glob("*.json.gz"):
            ts = file_ts(f.name)
            if ts is not None and ts <= until and (best is None or ts > best[0]):
                best = (ts, f)
        if best is None or (until - best[0]).total_seconds() > max_age_min * 60.0:
            continue
        try:
            with gzip.open(best[1]) as fh:
                body = json.loads(fh.read())
        except (OSError, ValueError):
            continue
        for m in body.get("markets", []):
            ev = str(m.get("event_ticker", ""))
            if not ev.endswith(f"{away}{home}") or m.get("status") not in (None, "active", "open"):
                continue
            title = str(m.get("title", ""))
            mm = re.match(r"^(.+?):\s*(\d+)\+", title)
            if not mm:
                continue
            out.append(
                LiveMarket(
                    str(m.get("ticker", "")),
                    norm_name(mm.group(1)),
                    mm.group(1),
                    stat,
                    int(mm.group(2)),
                    _price(m.get("yes_bid_dollars")),
                    _price(m.get("yes_ask_dollars")),
                    best[0],
                    "capture",
                    series,
                )  # fmt: skip
            )
    return out


def kalshi_fallback(
    cfg: JarvisConfig, slate: date, away: str, home: str, until: datetime
) -> list[LiveMarket]:
    """The snapshot store (about every 15 minutes) when the capture has nothing for the game."""
    from nba.jarvis.sources import open_ro

    path = cfg.paths["kalshi_db"]
    if not path.exists():
        return []
    con = open_ro(path, copy_ok=True)
    try:
        rows = con.execute(
            "SELECT m.ticker, m.title, m.stat, p.yes_bid, p.yes_ask, p.ts FROM kalshi_markets m "
            "JOIN (SELECT ticker, "
            "arg_max(struct_pack(b := yes_bid, a := yes_ask), ts) px, max(ts) ts "
            "FROM kalshi_prices WHERE ts <= ? GROUP BY ticker) p USING (ticker) "
            "WHERE m.result IS NULL AND m.event_ticker LIKE ? AND m.series_ticker IN "
            "('KXNBAPTS','KXNBAREB','KXNBAAST','KXNBA3PT')",
            [until, f"%{away}{home}"],
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    out = []
    for ticker, title, stat, bid, ask, ts in rows:
        mm = re.match(r"^(.+?):\s*(\d+)\+", str(title))
        if mm and stat in STAT_WORD:
            out.append(
                LiveMarket(str(ticker), norm_name(mm.group(1)), mm.group(1), str(stat),
                           int(mm.group(2)), _price(bid), _price(ask), ts, "snapshot_store",
                           str(ticker).split("-")[0])
            )  # fmt: skip
    return out


# ----------------------------------------------------------------------------------- signals


@dataclass(frozen=True)
class Signal:
    kind: str  # benched | window | foul | back | returned
    key: str  # dedupe key: espn game id (+ player id)
    snap: Snapshot
    players: tuple[PlayerLine, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


def late_and_lopsided(snap: Snapshot, margin: int, max_clock_s: float) -> bool:
    """Only the last ``max_clock_s`` (6:00) of the 4th quarter or overtime, lead >= ``margin``.
    Earlier than that a starter off the floor is ordinary rest (47% of the old Q3-or-Q4 flags played
    again); the Q3 branch is gone."""
    return (
        snap.state == "in"
        and snap.period >= 4
        and snap.clock_s <= max_clock_s
        and snap.margin >= margin
    )


def frozen_minutes(
    history: list[Snapshot], espn_id: str, min_snaps: int, min_game_s: float
) -> tuple[int, float] | None:
    """(MIN he is stuck on, game seconds frozen), or None. Frozen = unchanged across at least
    ``min_snaps`` consecutive latest snapshots spanning at least ``min_game_s`` of GAME clock. ESPN
    updates MIN in steps, so a player who has just checked back in also reads frozen; the caller
    therefore also requires him to be off the floor now (``active`` false)."""
    run: list[tuple[Snapshot, int]] = []
    for s in reversed(history):
        p = next((x for x in s.players if x.espn_id == espn_id), None)
        if p is None or p.minutes is None:
            break
        if run and p.minutes != run[-1][1]:
            break
        run.append((s, p.minutes))
    if len(run) < min_snaps:
        return None
    span = run[0][0].elapsed_s - run[-1][0].elapsed_s
    return (run[0][1], span) if span >= min_game_s else None


def detect(
    cfg: JarvisConfig,
    history: list[Snapshot],
    already: dict[str, set[str]],
    flags: dict[str, tuple[int, str, datetime]] | None = None,
) -> list[Signal]:
    """Signals for ONE game from its recent snapshots; ``already[kind]`` = keys posted before and
    ``flags[key]`` = (MIN, game time) at which a BENCHED post flagged him (for the correction)."""
    ig = cfg.ingame
    if not history:
        return []
    cur = history[-1]
    if cur.state != "in":
        return []
    gid = cur.espn_game
    out: list[Signal] = []
    frozen: list[PlayerLine] = []
    if late_and_lopsided(cur, int(ig["margin_benched"]), float(ig["benched_max_clock_s"])):
        for p in cur.players:
            if not p.starter or p.active:  # `active` = on the floor right now
                continue
            fm = frozen_minutes(
                history, p.espn_id, int(ig["min_snapshots"]), float(ig["frozen_game_s"])
            )
            if fm is None:
                continue
            frozen.append(p)
            key = f"{gid}:{p.espn_id}"
            if key not in already.get("benched", set()):
                out.append(Signal("benched", key, cur, (p,), {"minutes": fm[0], "frozen_s": fm[1]}))
        if frozen and gid not in already.get("window", set()):
            out.append(Signal("window", gid, cur, tuple(frozen)))
    for p in cur.players:
        key = f"{gid}:{p.espn_id}"
        if (
            p.starter
            and key not in already.get("foul", set())
            and (
                p.pf >= int(ig["foul_starter_pf"])
                or (p.pf >= int(ig["foul_early_pf"]) and cur.period < 4)
            )
        ):  # fmt: skip
            out.append(Signal("foul", key, cur, (p,), {"pf": p.pf}))
    benched_before = {k for k in already.get("benched", set()) if k.startswith(f"{gid}:")}
    for key in sorted(benched_before - already.get("returned", set())):
        pl = next((x for x in cur.players if f"{gid}:{x.espn_id}" == key), None)
        fl = (flags or {}).get(key)
        was, when = (fl[0], fl[1]) if fl else (None, "")
        if fl is not None and cur.ts <= fl[2]:
            continue  # only a snapshot AFTER the flag can correct it (a rewound replay cannot)
        if pl is not None and (pl.active or (was is not None and (pl.minutes or 0) > was)):
            out.append(Signal("returned", key, cur, (pl,), {"was": was, "when": when}))
    if (
        benched_before
        and gid not in already.get("back", set())
        and cur.state == "in"
        and cur.margin < int(ig["margin_back"])
        and cur.remaining_s >= float(ig["back_min_remaining_s"])
    ):
        names = tuple(p for p in cur.players if f"{gid}:{p.espn_id}" in benched_before)
        out.append(Signal("back", gid, cur, names))
    return out
