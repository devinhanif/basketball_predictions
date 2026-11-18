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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

# re-exported (explicit ``as``): importers and tests use ``nba.jarvis.book.<name>``
from nba.jarvis.book_summary import KILL_LABELS as KILL_LABELS
from nba.jarvis.book_summary import WEEKLY_LABELS as WEEKLY_LABELS
from nba.jarvis.book_summary import KillState as KillState
from nba.jarvis.book_summary import _bundles as _bundles
from nba.jarvis.book_summary import clear_kill_switch as clear_kill_switch
from nba.jarvis.book_summary import cluster_ci as cluster_ci
from nba.jarvis.book_summary import kill_switch as kill_switch
from nba.jarvis.book_summary import latch_kill_switch as latch_kill_switch
from nba.jarvis.book_summary import render_weekly as render_weekly
from nba.jarvis.book_summary import weekly_summary as weekly_summary
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
  model_prob DOUBLE, reason VARCHAR, explanation VARCHAR, dedupe_key VARCHAR, input_hash VARCHAR,
  delivered VARCHAR, pin_prob DOUBLE, w_model DOUBLE, leg_desc VARCHAR
);
CREATE TABLE IF NOT EXISTS scores (
  play_id VARCHAR PRIMARY KEY, scored_at TIMESTAMP, status VARCHAR, won BOOLEAN, pnl_usd DOUBLE,
  close_ts TIMESTAMP, close_mid DOUBLE, close_ask DOUBLE, close_source VARCHAR,
  clv_cents DOUBLE, clv_vs_ask_cents DOUBLE
);
CREATE TABLE IF NOT EXISTS quote_log (
  venue VARCHAR, ticker VARCHAR, ts TIMESTAMP, yes_bid DOUBLE, yes_ask DOUBLE
);
CREATE TABLE IF NOT EXISTS alert_state (
  slate_date DATE, dedupe_key VARCHAR, edge_cents DOUBLE, ts TIMESTAMP,
  PRIMARY KEY (slate_date, dedupe_key)
);
CREATE TABLE IF NOT EXISTS killswitch_log (
  ts TIMESTAMP, log_date DATE, action VARCHAR, n INTEGER, mean_clv DOUBLE, note VARCHAR
);
CREATE TABLE IF NOT EXISTS ingame_flags (
  slate_date DATE, flag_key VARCHAR, minutes INTEGER, when_text VARCHAR, ts TIMESTAMP,
  PRIMARY KEY (slate_date, flag_key)
);
CREATE TABLE IF NOT EXISTS sink_log (
  sink VARCHAR, text_hash VARCHAR, ts TIMESTAMP, PRIMARY KEY (sink, text_hash)
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
    "fair_prob fee_cents model_prob reason explanation dedupe_key input_hash delivered "
    "pin_prob w_model leg_desc"
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
        for col, typ in (  # a book created by an earlier version gains the newer columns
            ("delivered", "VARCHAR"), ("pin_prob", "DOUBLE"), ("w_model", "DOUBLE"),
            ("leg_desc", "VARCHAR"),
        ):  # fmt: skip
            con.execute(f"ALTER TABLE plays ADD COLUMN IF NOT EXISTS {col} {typ}")
        return con

    def ensure(self) -> None:
        self._con().close()

    def _rows(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        """Read-only query; an absent book (nothing recorded yet) reads as empty, not created."""
        if not self.path.exists():
            return []
        con = duckdb.connect(str(self.path), read_only=True)
        try:
            return con.execute(sql, params or []).fetchall()
        except duckdb.CatalogException:  # a table (or column) from a newer version: reads as empty
            return []
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
        return (int(s[0][0]), float(s[0][1])) if s and s[0][0] is not None else None

    def spent_split(self, slate: date) -> tuple[float, float]:
        """(strong/arbitrage, everything else) stake of the FIRST emission of every distinct play of
        the slate date. Re-emissions are not new money; a pick of Devin's is not JARVIS's money."""
        r = self._rows(
            "SELECT coalesce(sum(CASE WHEN edge_label IN ('strong','arbitrage') "
            "THEN stake_usd END), 0), "
            "coalesce(sum(CASE WHEN edge_label NOT IN ('strong','arbitrage') "
            "THEN stake_usd END), 0) "
            "FROM plays p WHERE slate_date = ? AND source <> 'pick' AND ts = ("
            "SELECT min(ts) FROM plays q WHERE q.dedupe_key = p.dedupe_key "
            "AND q.slate_date = p.slate_date)",
            [slate],
        )
        return (float(r[0][0]), float(r[0][1])) if r else (0.0, 0.0)

    def spent_tonight(self, slate: date) -> float:
        a, b = self.spent_split(slate)
        return a + b

    def record(
        self,
        plays: Sequence[Play],
        *,
        source: str,
        slate: date,
        explanations: dict[str, str] | None = None,
        delivered: str = "sent",
        now: datetime | None = None,
    ) -> list[Play]:
        """Record plays at decision time, once per (slate date, dedupe key): a re-emission is not a
        new bet and never a new row. ``delivered`` is sent / failed / partial; a row first recorded
        as failed is updated when a later send delivers it. Returns the NEW plays."""
        ts = now or now_naive()
        fresh: list[Play] = []
        con = self._con()
        try:
            for p in plays:
                r = con.execute(
                    "SELECT count(*), max(delivered) FROM plays "
                    "WHERE dedupe_key = ? AND slate_date = ?",
                    [p.key, slate],
                ).fetchone()
                if r and r[0]:
                    if delivered in ("sent", "partial") and r[1] == "failed":
                        con.execute(
                            "UPDATE plays SET delivered = ? "
                            "WHERE dedupe_key = ? AND slate_date = ?",
                            [delivered, p.key, slate],
                        )
                    continue
                bundle = sha([p.key, str(slate), ts.isoformat()])
                ih = sha(p.inputs)
                for i, leg in enumerate(p.legs):
                    cost = order_cost_leg(p, i)
                    con.execute(
                        INSERT_PLAY,
                        [
                            sha([bundle, i]), bundle, i, ts, slate, p.game_id, p.matchup, p.tipoff,
                            source, leg.venue, leg.ticker, leg.side, leg.kind, leg.team_id,
                            leg.player_id, leg.stat, leg.threshold, leg.price, p.max_price,
                            p.contracts, cost, p.label, p.edge_cents, p.fair_prob, p.fee_cents,
                            p.model_prob, p.reason, (explanations or {}).get(p.key, ""), p.key, ih,
                            delivered, p.pin_prob, p.w_model, leg.desc,
                        ],
                    )  # fmt: skip
                fresh.append(p)
        finally:
            con.close()
        return fresh

    # ------------------------------------------------------------------ per-sink delivery
    def sink_done(self, sink: str, text_hash: str) -> bool:
        return bool(
            self._rows("SELECT 1 FROM sink_log WHERE sink = ? AND text_hash = ?", [sink, text_hash])
        )

    def mark_sink(self, sink: str, text_hash: str, now: datetime | None = None) -> None:
        con = self._con()
        try:
            con.execute(
                "INSERT OR IGNORE INTO sink_log VALUES (?,?,?)",
                [sink, text_hash, now or now_naive()],
            )
        finally:
            con.close()

    # ------------------------------------------------------------------ in-game flags
    def note_flag(
        self, key: str, slate: date, minutes: int, when: str, dry_run: bool = False,
        now: datetime | None = None,
    ) -> None:  # fmt: skip
        """Remember the MIN and game time at which a BENCHED post flagged a player (for the
        correction post and the nightly flagged-vs-returned count)."""
        if dry_run:
            return
        con = self._con()
        try:
            con.execute(
                "INSERT OR REPLACE INTO ingame_flags VALUES (?,?,?,?,?)",
                [slate, key, minutes, when, now or now_naive()],
            )
        finally:
            con.close()

    def ingame_flags(self, slate: date) -> dict[str, tuple[int, str, datetime]]:
        """key -> (MIN, game time, snapshot time) at which a BENCHED post flagged him."""
        rows = self._rows(
            "SELECT flag_key, minutes, when_text, ts FROM ingame_flags WHERE slate_date = ?",
            [slate],
        )
        return {str(r[0]): (int(r[1]), str(r[2]), r[3]) for r in rows}

    def ingame_counts(self, slate: date) -> tuple[int, int]:
        """(BENCHED flagged, of those corrected as back) for the slate date, from the post log."""
        flagged = self.sent_keys("ingame_benched", slate)
        back = self.sent_keys("ingame_returned", slate)
        return len(flagged), len(back & flagged)

    # ------------------------------------------------------------------ kill switch log
    def kill_rows(self) -> list[tuple[Any, ...]]:
        return self._rows(
            "SELECT ts, log_date, action, n, mean_clv FROM killswitch_log ORDER BY ts"
        )

    def log_kill(
        self, action: str, today: date, n: int, mean: float | None, note: str = "",
        now: datetime | None = None,
    ) -> None:  # fmt: skip
        con = self._con()
        try:
            con.execute(
                "INSERT INTO killswitch_log VALUES (?,?,?,?,?,?)",
                [now or now_naive(), today, action, n, mean, note],
            )
        finally:
            con.close()

    # ------------------------------------------------------------------ alert state
    def realert_due(
        self, key: str, slate: date, edge: float, label_upgrade: bool, min_move: float
    ) -> bool:
        """A play already alerted today alerts again only on a label upgrade (a new key) or when its
        edge moved at least ``min_move`` cents from the edge last alerted."""
        r = self._rows(
            "SELECT edge_cents FROM alert_state WHERE slate_date = ? AND dedupe_key = ?",
            [slate, key],
        )
        if not r:
            return True
        last = float(r[0][0])
        return label_upgrade or abs(edge - last) >= min_move

    def note_alert(self, key: str, slate: date, edge: float, now: datetime | None = None) -> None:
        con = self._con()
        try:
            con.execute(
                "DELETE FROM alert_state WHERE slate_date = ? AND dedupe_key = ?", [slate, key]
            )
            con.execute(
                "INSERT INTO alert_state VALUES (?,?,?,?)", [slate, key, edge, now or now_naive()]
            )
        finally:
            con.close()

    def failed_attempts(self, kind: str, slate: date, key: str | None = None) -> int:
        """Failed or partial sends of this kind today (for the give-up cap)."""
        rows = self._rows(
            "SELECT dedupe_keys FROM sends WHERE kind = ? AND slate_date = ? "
            "AND mode IN ('failed','partial')",
            [kind, slate],
        )
        if key is None:
            return len(rows)
        return sum(1 for r in rows if key in json.loads(str(r[0])))

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
            "SELECT dedupe_keys FROM sends WHERE ts > ? AND mode IN ('sent','partial','dry_run')",
            [cut],
        )
        return any(key in json.loads(str(r[0])) for r in rows)

    def kind_sent(self, kind: str, slate: date, key: str = "") -> bool:
        """Has a post of this kind (and key) gone out, wholly or in part, for the slate date?"""
        rows = self._rows(
            "SELECT dedupe_keys FROM sends WHERE kind = ? AND slate_date = ? "
            "AND mode IN ('sent','partial','dry_run')",
            [kind, slate],
        )
        return bool(rows) if not key else any(key in json.loads(str(r[0])) for r in rows)

    def sent_keys(self, kind: str, slate: date) -> set[str]:
        """Every key that went out (wholly or in part) under this kind for the slate date."""
        rows = self._rows(
            "SELECT dedupe_keys FROM sends WHERE kind = ? AND slate_date = ? "
            "AND mode IN ('sent','partial','dry_run')",
            [kind, slate],
        )
        return {k for r in rows for k in json.loads(str(r[0]))}

    def slate_summary_sent(self, slate: date) -> bool:
        r = self._rows(
            "SELECT count(*) FROM sends WHERE kind = 'slate' AND slate_date = ? "
            "AND mode IN ('sent','partial','dry_run')",
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


__all__ = [
    "LABELS",
    "KillState",
    "PaperBook",
    "clear_kill_switch",
    "kill_switch",
    "latch_kill_switch",
    "order_cost",
    "sha",
]


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
    """(ts, bid, ask, source) of the LATEST YES quote strictly before tip across JARVIS's own
    quote_log and (Kalshi) the snapshot store, else None."""
    tip = row["tipoff"]
    if tip is None:
        return None
    cands: list[tuple[datetime, float | None, float | None, str]] = []
    r = book_con.execute(
        "SELECT ts, yes_bid, yes_ask FROM quote_log WHERE venue = ? AND ticker = ? AND ts < ? "
        "ORDER BY ts DESC LIMIT 1",
        [row["venue"], row["ticker"], tip],
    ).fetchone()
    if r:
        cands.append((r[0], r[1], r[2], "quote_log"))
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
            cands.append((k[0], k[1], k[2], "kalshi_prices"))
    return max(cands, key=lambda c: c[0]) if cands else None


def two_sided(bid: float | None, ask: float | None) -> bool:
    """A close needs both sides: bid > 0 and ask < 1 (a one-sided book has no defined mid)."""
    return bid is not None and ask is not None and float(bid) > 0.0 and float(ask) < 1.0


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
        combos: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row["edge_label"] == "daily_dollar":
                combos.setdefault(row["bundle_id"], []).append(row)
                continue
            o = src.of(row)
            if o.status == "unresolved":
                counts["waiting"] += 1
                continue
            # a live entry has no pre-tip close to compare with: no CLV for in-game plays
            cq = (
                None if str(row["edge_label"]).startswith("live_") else closing_quote(cfg, con, row)
            )
            mid = ask = cts = None
            csrc = ""
            if cq is not None:
                cts, bid, yask, csrc = cq
                yes = row["side"] == "yes"
                if bid is not None and yask is not None and two_sided(bid, yask):
                    m = (float(bid) + float(yask)) / 2.0
                    mid = m if yes else 1.0 - m
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
        for legs in combos.values():
            _score_combo(con, src, legs, ts, counts)
    finally:
        con.close()
    return counts


def _score_combo(
    con: duckdb.DuckDBPyConnection,
    src: OutcomeSource,
    legs: list[dict[str, Any]],
    ts: datetime,
    counts: dict[str, int],
) -> None:
    """A combo ticket wins only if every leg wins: one P&L for the bundle (on leg 0)."""
    outs = [src.of(r) for r in legs]
    if any(o.status == "unresolved" for o in outs) and not any(o.won is False for o in outs):
        counts["waiting"] += 1
        return
    if any(o.status == "void" for o in outs):
        status, won, pnl_total = "void", None, 0.0
    else:
        status = "settled"
        won = all(o.won for o in outs) if all(o.won is not None for o in outs) else False
        stake = sum(float(r["stake_usd"]) for r in legs)
        pnl_total = (float(legs[0]["contracts"]) if won else 0.0) - stake
    counts["void" if status == "void" else "scored"] += 1
    for k, r in enumerate(sorted(legs, key=lambda r: r["leg_idx"])):
        con.execute(
            "INSERT INTO scores VALUES (?,?,?,?,?,NULL,NULL,NULL,'',NULL,NULL)",
            [r["play_id"], ts, status, won, pnl_total if k == 0 else 0.0],
        )
