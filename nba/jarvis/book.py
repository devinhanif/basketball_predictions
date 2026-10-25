"""The paper book: JARVIS's own small DuckDB (``data/jarvis/paper_book.duckdb``), not nba.duckdb.

Tables
  plays      one row per emitted LEG (an arbitrage is two rows sharing ``bundle_id``)
  scores     one row per scored leg: outcome, P&L, closing price and closing-line value
  quote_log  venue prices JARVIS itself read (the closing line for venues the snapshot job misses)
  sends      every delivery decision with the hashes of its inputs (sent / failed)

CLV convention (declared before any data): closing price = the MID of the venue's last quote
strictly before tip, on the side we bought; ``clv_cents = (close_mid - entry_ask) * 100``
(positive = we paid less than the market's last view). ``clv_vs_ask_cents`` uses the closing ask
instead. The entry is the ask we would have paid, so a half-spread of cost is built into every
CLV figure.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.jarvis.config import LABELS, JarvisConfig
from nba.jarvis.edge import order_cost
from nba.jarvis.models import Play, Quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
  play_id VARCHAR PRIMARY KEY, bundle_id VARCHAR, leg_idx INTEGER, ts TIMESTAMP,
  slate_date DATE, game_id VARCHAR, matchup VARCHAR, tipoff TIMESTAMP, source VARCHAR,
  venue VARCHAR, ticker VARCHAR, side VARCHAR, kind VARCHAR, team_id INTEGER, player_id INTEGER,
  stat VARCHAR, threshold DOUBLE, price DOUBLE, max_price DOUBLE, contracts INTEGER,
  stake_usd DOUBLE, edge_label VARCHAR, edge_cents DOUBLE, fair_prob DOUBLE, fee_cents DOUBLE,
  model_prob DOUBLE, reason VARCHAR, explanation VARCHAR, dedupe_key VARCHAR, input_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS scores (
  play_id VARCHAR PRIMARY KEY, scored_at TIMESTAMP, status VARCHAR, won BOOLEAN, pnl_usd DOUBLE,
  close_ts TIMESTAMP, close_mid DOUBLE, close_ask DOUBLE, close_source VARCHAR,
  clv_cents DOUBLE, clv_vs_ask_cents DOUBLE
);
CREATE TABLE IF NOT EXISTS quote_log (
  venue VARCHAR, ticker VARCHAR, ts TIMESTAMP, yes_bid DOUBLE, yes_ask DOUBLE
);
CREATE TABLE IF NOT EXISTS sends (
  send_id VARCHAR PRIMARY KEY, ts TIMESTAMP, kind VARCHAR, mode VARCHAR, slate_date DATE,
  dedupe_keys VARCHAR, play_ids VARCHAR, text_hash VARCHAR, input_hash VARCHAR, chars INTEGER,
  note VARCHAR
);
"""

PLAY_COLS = (
    "play_id bundle_id leg_idx ts slate_date game_id matchup tipoff source venue ticker side kind "
    "team_id player_id stat threshold price max_price contracts stake_usd edge_label edge_cents "
    "fair_prob fee_cents model_prob reason explanation dedupe_key input_hash"
)
PLAY_COLS_L = PLAY_COLS.split()  # noqa: SIM905 - one readable column string
INSERT_PLAY = (
    f"INSERT INTO plays ({','.join(PLAY_COLS_L)}) VALUES ({','.join('?' * len(PLAY_COLS_L))})"
)


def sha(obj: Any) -> str:
    """Stable short hash of any JSON-able object (inputs of a play or a send)."""
    blob = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PaperBook:
    """Short-lived connections: open, act, close, so no write handle outlives a call."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _con(self) -> duckdb.DuckDBPyConnection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self.path))
        con.execute(SCHEMA)
        return con

    def ensure(self) -> None:
        self._con().close()

    def _rows(self, sql: str, params: list[object] | None = None) -> list[tuple[object, ...]]:
        """Read-only query; an absent book (nothing recorded yet) reads as empty, not created."""
        if not self.path.exists():
            return []
        con = duckdb.connect(str(self.path), read_only=True)
        try:
            return con.execute(sql, params or []).fetchall()
        finally:
            con.close()

    # ------------------------------------------------------------------ plays
    def recent_key(self, key: str, minutes: float, now: datetime | None = None) -> bool:
        """Was this play (same key) already recorded within ``minutes``?"""
        cut = (now or now_naive()) - timedelta(minutes=minutes)
        r = self._rows("SELECT count(*) FROM plays WHERE dedupe_key = ? AND ts > ?", [key, cut])
        return bool(r and r[0][0])

    def first_stake(self, key: str, slate: date) -> tuple[int, float] | None:
        """(contracts, total stake of the bundle) of the FIRST emission of ``key`` that date."""
        r = self._rows(
            "SELECT bundle_id FROM plays WHERE dedupe_key = ? AND slate_date = ? "
            "ORDER BY ts, leg_idx LIMIT 1",
            [key, slate],
        )
        if not r:
            return None
        s = self._rows(
            "SELECT max(contracts), sum(stake_usd) FROM plays WHERE bundle_id = ?", [r[0][0]]
        )
        return (int(s[0][0]), float(s[0][1])) if s and s[0][0] is not None else None  # type: ignore[call-overload,arg-type]

    def spent_tonight(self, slate: date) -> float:
        """Stake of the FIRST emission of every distinct play of the slate date (re-emissions of the
        same play are not new money)."""
        r = self._rows(
            "SELECT coalesce(sum(stake_usd), 0) FROM plays p WHERE slate_date = ? AND ts = ("
            "SELECT min(ts) FROM plays q WHERE q.dedupe_key = p.dedupe_key "
            "AND q.slate_date = p.slate_date)",
            [slate],
        )
        return float(r[0][0]) if r and r[0][0] is not None else 0.0  # type: ignore[arg-type]

    def record(
        self,
        plays: Sequence[Play],
        *,
        source: str,
        slate: date,
        explanations: dict[str, str] | None = None,
        dedupe_minutes: float = 30.0,
        now: datetime | None = None,
    ) -> list[Play]:
        """Insert plays not already recorded within the dedupe window; return the NEW ones."""
        ts = now or now_naive()
        fresh: list[Play] = []
        con = self._con()
        try:
            for p in plays:
                cut = ts - timedelta(minutes=dedupe_minutes)
                r = con.execute(
                    "SELECT count(*) FROM plays WHERE dedupe_key = ? AND ts > ?", [p.key, cut]
                ).fetchone()
                if r and r[0]:
                    continue
                bundle = sha([p.key, ts.isoformat()])
                ih = sha(p.inputs)
                for i, leg in enumerate(p.legs):
                    cost = order_cost_leg(p, i)
                    con.execute(
                        INSERT_PLAY,
                        [
                            sha([bundle, i]),
                            bundle,
                            i,
                            ts,
                            slate,
                            p.game_id,
                            p.matchup,
                            p.tipoff,
                            source,
                            leg.venue,
                            leg.ticker,
                            leg.side,
                            leg.kind,
                            leg.team_id,
                            leg.player_id,
                            leg.stat,
                            leg.threshold,
                            leg.price,
                            p.max_price,
                            p.contracts,
                            cost,
                            p.label,
                            p.edge_cents,
                            p.fair_prob,
                            p.fee_cents,
                            p.model_prob,
                            p.reason,
                            (explanations or {}).get(p.key, ""),
                            p.key,
                            ih,
                        ],
                    )
                fresh.append(p)
        finally:
            con.close()
        return fresh

    def log_quotes(self, quotes: Iterable[Quote]) -> int:
        rows = [
            (q.venue, q.ticker, q.ts, q.yes_bid, q.yes_ask)
            for q in quotes
            if not q.indicative and (q.yes_bid is not None or q.yes_ask is not None)
        ]
        if not rows:
            return 0
        con = self._con()
        try:
            con.executemany("INSERT INTO quote_log VALUES (?,?,?,?,?)", rows)
        finally:
            con.close()
        return len(rows)

    # ------------------------------------------------------------------ sends
    def record_send(
        self,
        *,
        kind: str,
        mode: str,
        slate: date,
        keys: list[str],
        play_ids: list[str],
        text: str,
        inputs: Any,
        note: str = "",
        now: datetime | None = None,
    ) -> str:
        ts = now or now_naive()
        sid = sha([kind, ts.isoformat(), text])
        con = self._con()
        try:
            con.execute(
                "INSERT INTO sends VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    sid,
                    ts,
                    kind,
                    mode,
                    slate,
                    json.dumps(keys),
                    json.dumps(play_ids),
                    sha(text),
                    sha(inputs),
                    len(text),
                    note,
                ],
            )
        finally:
            con.close()
        return sid

    def sent_recently(self, key: str, minutes: float, now: datetime | None = None) -> bool:
        """Was a real or dry-run send that carried ``key`` made within ``minutes``?"""
        cut = (now or now_naive()) - timedelta(minutes=minutes)
        rows = self._rows(
            "SELECT dedupe_keys FROM sends WHERE ts > ? AND mode IN ('sent','dry_run')", [cut]
        )
        return any(key in json.loads(str(r[0])) for r in rows)

    def slate_summary_sent(self, slate: date) -> bool:
        r = self._rows(
            "SELECT count(*) FROM sends WHERE kind = 'slate' AND slate_date = ? "
            "AND mode IN ('sent','dry_run')",
            [slate],
        )
        return bool(r and r[0][0])

    def open_tickers(self, slate: date) -> set[str]:
        """Tickers of every play recorded for the slate date (their prices are logged for CLV)."""
        rows = self._rows("SELECT DISTINCT ticker FROM plays WHERE slate_date = ?", [slate])
        return {str(r[0]) for r in rows}


def order_cost_leg(p: Play, i: int) -> float:
    """Dollar cost of leg ``i`` (contracts x price + order fee) recorded in ``p.inputs``."""
    costs = p.inputs.get("leg_costs")
    if isinstance(costs, list) and i < len(costs):
        return float(costs[i])
    return 0.0


__all__ = ["LABELS", "PaperBook", "order_cost", "sha"]


# ------------------------------------------------------------------------------ scoring


@dataclass(frozen=True)
class Outcome:
    won: bool | None  # None = unresolved
    status: str  # settled | void | unresolved


class OutcomeSource:
    """Settlement truth: Kalshi's own ``result`` first, else the box score / final score."""

    def __init__(self, cfg: JarvisConfig) -> None:
        self.cfg = cfg

    def of(self, row: dict[str, Any]) -> Outcome:
        if row["venue"] == "kalshi":
            o = self._kalshi(row["ticker"], row["side"])
            if o.status != "unresolved":
                return o
        if row["kind"] == "game_win":
            return self._game(row)
        if row["kind"] == "prop":
            return self._prop(row)
        return Outcome(None, "unresolved")

    def _kalshi(self, ticker: str, side: str) -> Outcome:
        path = self.cfg.paths["kalshi_db"]
        if not path.exists():
            return Outcome(None, "unresolved")
        from nba.jarvis.sources import open_ro

        con = open_ro(path, copy_ok=True)
        try:
            r = con.execute(
                "SELECT result FROM kalshi_markets WHERE ticker = ? AND result IS NOT NULL",
                [ticker],
            ).fetchone()
        finally:
            con.close()
        if not r or str(r[0]).lower() not in ("yes", "no"):
            return Outcome(None, "unresolved")
        yes = str(r[0]).lower() == "yes"
        return Outcome(yes if side == "yes" else not yes, "settled")

    def _final(self, game_id: str) -> tuple[int, int, int, int] | None:
        path = self.cfg.paths["nba_db"]
        if not path.exists():
            return None
        from nba.jarvis.sources import open_ro

        con = open_ro(path)
        try:
            r = con.execute(
                "SELECT home_team, away_team, home_pts, away_pts FROM games "
                "WHERE game_id = ? AND season = 2026 AND home_pts > 0 AND away_pts > 0",
                [game_id],
            ).fetchone()
        finally:
            con.close()
        return None if r is None else (int(r[0]), int(r[1]), int(r[2]), int(r[3]))

    def _game(self, row: dict[str, Any]) -> Outcome:
        f = self._final(row["game_id"])
        if f is None or row["team_id"] is None:
            return Outcome(None, "unresolved")
        home, away, hp, ap = f
        winner = home if hp > ap else away
        yes = winner == int(row["team_id"])
        return Outcome(yes if row["side"] == "yes" else not yes, "settled")

    def _prop(self, row: dict[str, Any]) -> Outcome:
        if self._final(row["game_id"]) is None:
            return Outcome(None, "unresolved")
        path = self.cfg.paths["nba_db"]
        from nba.jarvis.sources import open_ro

        stat = str(row["stat"])
        if stat not in ("pts", "reb", "ast", "fg3m"):
            return Outcome(None, "unresolved")
        con = open_ro(path)
        try:
            r = con.execute(
                f"SELECT {stat}, minutes FROM player_game_stats "
                "WHERE game_id = ? AND player_id = ?",
                [row["game_id"], row["player_id"]],
            ).fetchone()
        finally:
            con.close()
        if r is None or r[1] is None or float(r[1]) <= 0:
            return Outcome(None, "void")  # did not play: venue rules decide; never guessed here
        yes = float(r[0]) >= float(row["threshold"])
        return Outcome(yes if row["side"] == "yes" else not yes, "settled")


def closing_quote(
    cfg: JarvisConfig, book_con: duckdb.DuckDBPyConnection, row: dict[str, Any]
) -> tuple[datetime, float | None, float | None, str] | None:
    """(ts, bid, ask, source) of the venue's last YES quote strictly before tip, else None."""
    tip = row["tipoff"]
    if tip is None:
        return None
    r = book_con.execute(
        "SELECT ts, yes_bid, yes_ask FROM quote_log WHERE venue = ? AND ticker = ? AND ts < ? "
        "ORDER BY ts DESC LIMIT 1",
        [row["venue"], row["ticker"], tip],
    ).fetchone()
    if r:
        return r[0], r[1], r[2], "quote_log"
    if row["venue"] == "kalshi" and cfg.paths["kalshi_db"].exists():
        from nba.jarvis.sources import open_ro

        kc = open_ro(cfg.paths["kalshi_db"], copy_ok=True)
        try:
            k = kc.execute(
                "SELECT ts, yes_bid, yes_ask FROM kalshi_prices WHERE ticker = ? AND ts < ? "
                "ORDER BY ts DESC LIMIT 1",
                [row["ticker"], tip],
            ).fetchone()
        finally:
            kc.close()
        if k:
            return k[0], k[1], k[2], "kalshi_prices"
    return None


def score_book(
    cfg: JarvisConfig, book: PaperBook, *, now: datetime | None = None
) -> dict[str, int]:
    """Score every unscored leg whose game has a result: outcome, P&L, CLV. Idempotent."""
    ts = now or now_naive()
    src = OutcomeSource(cfg)
    con = book._con()
    counts = {"scored": 0, "void": 0, "waiting": 0}
    try:
        cur = con.execute(
            "SELECT p.* FROM plays p LEFT JOIN scores s USING (play_id) WHERE s.play_id IS NULL "
            "AND p.tipoff < ?",
            [ts],
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        for row in rows:
            o = src.of(row)
            if o.status == "unresolved":
                counts["waiting"] += 1
                continue
            cq = closing_quote(cfg, con, row)
            mid = ask = cts = None
            csrc = ""
            if cq is not None:
                cts, bid, yask, csrc = cq
                yes = row["side"] == "yes"
                if bid is not None and yask is not None:
                    m = (float(bid) + float(yask)) / 2.0
                    mid = m if yes else 1.0 - m
                if yask is not None and bid is not None:
                    ask = float(yask) if yes else 1.0 - float(bid)
            clv = None if mid is None else (mid - float(row["price"])) * 100.0
            clv_ask = None if ask is None else (ask - float(row["price"])) * 100.0
            if o.status == "settled" and o.won is not None:
                payout = float(row["contracts"]) if o.won else 0.0
                pnl = payout - float(row["stake_usd"])
                counts["scored"] += 1
            else:
                pnl = 0.0  # void: stake returned in the paper book; flagged, excluded from n
                counts["void"] += 1
            con.execute(
                "INSERT INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [row["play_id"], ts, o.status, o.won, pnl, cts, mid, ask, csrc, clv, clv_ask],
            )
    finally:
        con.close()
    return counts


# ------------------------------------------------------------------------------ summary


def cluster_ci(
    values: Sequence[tuple[date, float]],
    *,
    n_boot: int = 2000,
    seed: int = 20261025,
    level: float = 0.95,
) -> tuple[float, float, float] | None:
    """(mean, lo, hi): bootstrap over slate-date clusters (resample dates, pooled mean). None with
    fewer than 2 date clusters (a CI would be a lie)."""
    by: dict[date, list[float]] = {}
    for d, v in values:
        by.setdefault(d, []).append(v)
    if len(by) < 2:
        return None
    keys = sorted(by)
    rng = random.Random(seed)
    allv = [v for _, v in values]
    means = []
    for _ in range(n_boot):
        tot = 0.0
        n = 0
        for _k in keys:
            vs = by[keys[rng.randrange(len(keys))]]
            tot += sum(vs)
            n += len(vs)
        means.append(tot / n)
    means.sort()
    a = (1.0 - level) / 2.0
    lo = means[int(a * n_boot)]
    hi = means[min(n_boot - 1, int((1.0 - a) * n_boot))]
    return sum(allv) / len(allv), lo, hi


def weekly_summary(
    cfg: JarvisConfig, book: PaperBook, *, until: date, days: int = 7
) -> dict[str, Any]:
    since = until - timedelta(days=days - 1)
    con = book._con()
    try:
        rows = con.execute(
            "SELECT p.slate_date, p.edge_label, s.status, s.pnl_usd, s.clv_cents, p.stake_usd "
            "FROM plays p JOIN scores s USING (play_id) WHERE p.slate_date BETWEEN ? AND ?",
            [since, until],
        ).fetchall()
        n_emitted = con.execute(
            "SELECT count(DISTINCT bundle_id) FROM plays WHERE slate_date BETWEEN ? AND ?",
            [since, until],
        ).fetchone()
    finally:
        con.close()
    settled = [r for r in rows if r[2] == "settled"]
    out: dict[str, Any] = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "n_emitted": int(n_emitted[0]) if n_emitted else 0,
        "n_scored": len(settled),
        "n_void": sum(1 for r in rows if r[2] == "void"),
        "labels": {},
        "min_settled": cfg.min_settled,
        "min_dates": cfg.min_settled_dates,
    }
    for label in (*LABELS, "all"):
        sel = settled if label == "all" else [r for r in settled if r[1] == label]
        clv = [(r[0], float(r[4])) for r in sel if r[4] is not None]
        dates = {r[0] for r in sel}
        ci = cluster_ci(clv)
        staked = sum(float(r[5]) for r in sel)
        pnl = sum(float(r[3]) for r in sel)
        out["labels"][label] = {
            "n": len(sel),
            "n_dates": len(dates),
            "n_clv": len(clv),
            "pnl_usd": round(pnl, 2),
            "staked_usd": round(staked, 2),
            "clv_mean_cents": None if not clv else round(sum(v for _, v in clv) / len(clv), 3),
            "clv_ci95_cents": None if ci is None else [round(ci[1], 3), round(ci[2], 3)],
            "claim_ok": len(sel) >= cfg.min_settled and len(dates) >= cfg.min_settled_dates,
        }
    return out


def render_weekly(s: dict[str, Any]) -> str:
    lines = [
        f"JARVIS paper book {s['since']} .. {s['until']}: emitted {s['n_emitted']}, "
        f"scored {s['n_scored']}, void {s['n_void']}",
        "label      n  dates  P&L$    staked$  CLV mean c  CI95 (date-clustered)  claim?",
    ]
    for label, d in s["labels"].items():
        ci = (
            "n/a (<2 dates)"
            if d["clv_ci95_cents"] is None
            else f"[{d['clv_ci95_cents'][0]}, {d['clv_ci95_cents'][1]}]"
        )
        clv = "n/a" if d["clv_mean_cents"] is None else f"{d['clv_mean_cents']}"
        claim = "yes" if d["claim_ok"] else "insufficient sample"
        lines.append(
            f"{label:<9} {d['n']:>3} {d['n_dates']:>5} {d['pnl_usd']:>7} {d['staked_usd']:>8} "
            f"{clv:>10}  {ci:<21} {claim}"
        )
    lines.append(
        f"A claim needs n >= {s['min_settled']} settled over >= {s['min_dates']} dates; below that "
        "these are descriptive and prove nothing."
    )
    return "\n".join(lines)
