"""Resumable historical odds pull (the-odds-api.com) into ``data/odds/odds_history.duckdb``.

For every game of a season that has a REAL scheduled tip-off (``data/schedule/raw_*.parquet`` via
``nba.features.game_tipoff``), request two snapshots (T-60 and T-5 minutes before tip). Each
snapshot first lists events as of that timestamp to find the vendor event id (our home/away team
ids and a +-12 h commence_time window; 0 or >=2 matches fail loudly and are counted), then pulls
per-event odds for ``props`` (4 markets) or ``games`` (3 markets) over the configured regions.

* Read-only with respect to everything outside ``data/odds``: ``nba.duckdb`` is opened
  ``read_only=True``; rows go to a SEPARATE DuckDB. No model predictions are read or joined, so
  pulling the frozen 2025 season is data acquisition only.
* Resumable and idempotent: raw responses are cached at a path that is a pure function of the
  request, and a JSONL state file records each (game, snapshot, phase) outcome. A rerun skips done
  items and re-reads cached raw files at 0 credits. ``reparse`` rebuilds the table from raw only.
* Per-item failures (event match, rejected request, unreachable) are recorded and the run goes on;
  credit/auth failures abort the run (the state file keeps everything done so far).
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.odds.the_odds_api import (
    ROW_SCHEMA,
    SNAPSHOT_KINDS,
    AuthError,
    CreditBudgetExceeded,
    CreditLedger,
    EventMatchError,
    GameRef,
    HistoricalClient,
    HistoryConfig,
    HistoryError,
    KeyMissingError,
    ParseResult,
    RequestRejected,
    UnreachableError,
    default_resolver,
    load_config,
    load_raw,
    match_event,
    odds_path,
    parse_event_odds,
    parse_events,
    raw_path,
)
from nba.odds.theoddsapi import NameResolver

log = logging.getLogger("nba.odds.history")

PHASES = ("props", "games")
DDL = """
CREATE TABLE IF NOT EXISTS odds_history (
    source VARCHAR, season INTEGER, game_id VARCHAR, event_id VARCHAR, snapshot_kind VARCHAR,
    requested_at TIMESTAMP, snapshot_ts TIMESTAMP, book VARCHAR, market VARCHAR,
    outcome_name VARCHAR, player_name VARCHAR, player_id BIGINT, side VARCHAR, point DOUBLE,
    price_american INTEGER, implied_prob_raw DOUBLE, implied_prob DOUBLE, last_update TIMESTAMP,
    raw_file VARCHAR
)
"""


# ----------------------------------------------------------------------------------------------
# Games with real tips
# ----------------------------------------------------------------------------------------------


def load_games(cfg: HistoryConfig, season: int) -> tuple[list[GameRef], int]:
    """``(games with a real tip sorted by tip, n games without one)`` for ``games.season``.

    Games without a real scheduled tip-off are NOT pulled (no proxy times: the snapshot offsets
    are defined against the real tip). Their count is returned so the caller can report it.
    """
    from nba.features.game_tipoff import build_game_tipoff

    con = duckdb.connect(str(cfg.games_db), read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, season, home_team, away_team FROM games WHERE season = ?", [season]
        ).pl()
    finally:
        con.close()
    return _attach_tips(g, build_game_tipoff(cfg.schedule_dir))


def _attach_tips(games: pl.DataFrame, tips: pl.DataFrame) -> tuple[list[GameRef], int]:
    j = games.join(tips.select(["game_id", "tipoff_utc"]), on="game_id", how="left")
    missing = int(j["tipoff_utc"].null_count())
    have = j.filter(pl.col("tipoff_utc").is_not_null()).sort(["tipoff_utc", "game_id"])
    out = [
        GameRef(
            str(r["game_id"]),
            int(r["season"]),
            int(r["home_team"]),
            int(r["away_team"]),
            r["tipoff_utc"],
        )
        for r in have.iter_rows(named=True)
    ]
    return out, missing


# ----------------------------------------------------------------------------------------------
# State file (JSONL, last line per key wins)
# ----------------------------------------------------------------------------------------------


def item_key(game_id: str, kind: str, phase: str) -> str:
    return f"{game_id}|{kind}|{phase}"


class PullState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a torn last line from a killed process; the item just reruns
                if isinstance(rec, dict) and "k" in rec:
                    self.items[str(rec["k"])] = rec

    def done(self, key: str) -> bool:
        return self.items.get(key, {}).get("status") in ("done", "done_empty")

    def set(self, key: str, status: str, **extra: Any) -> None:
        rec = {"k": key, "status": status, "ts": datetime.now(UTC).isoformat(), **extra}
        self.items[key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def counts(self) -> Counter[str]:
        c: Counter[str] = Counter()
        for k, rec in self.items.items():
            c[f"{k.split('|')[2]}:{rec.get('status')}"] += 1
        return c


# ----------------------------------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------------------------------


class OddsStore:
    """The separate ``odds_history`` DuckDB. Connections are short-lived (open, write, close)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for _ in range(120):  # another pull (other season) may hold the write lock for a moment
            try:
                con = duckdb.connect(str(self.path), read_only=read_only)
                if not read_only:
                    con.execute(DDL)
                return con
            except duckdb.IOException as exc:
                last = exc
                time.sleep(0.5)
        raise HistoryError(f"could not open {self.path.name}: {type(last).__name__}")

    def replace(self, game_id: str, kind: str, markets: list[str], rows: pl.DataFrame) -> int:
        """Idempotent write: drop this game/snapshot's rows for ``markets``, insert ``rows``."""
        con = self._connect()
        try:
            ph = ",".join("?" for _ in markets)
            con.execute(
                f"DELETE FROM odds_history WHERE game_id = ? AND snapshot_kind = ? "
                f"AND market IN ({ph})",
                [game_id, kind, *markets],
            )
            if rows.height:
                arrow = rows.select(list(ROW_SCHEMA)).to_arrow()
                con.register("new_rows", arrow)
                con.execute("INSERT INTO odds_history SELECT * FROM new_rows")
                con.unregister("new_rows")
            return rows.height
        finally:
            con.close()

    def read(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        if not self.path.exists():
            return []
        con = self._connect(read_only=True)
        try:
            return con.execute(sql, params or []).fetchall()
        finally:
            con.close()


# ----------------------------------------------------------------------------------------------
# Plan / estimate
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    game: GameRef
    kind: str
    phase: str

    @property
    def key(self) -> str:
        return item_key(self.game.game_id, self.kind, self.phase)

    def at(self, cfg: HistoryConfig) -> datetime:
        return self.game.tipoff_utc - timedelta(minutes=cfg.minutes_before_tip(self.kind))


def build_items(games: list[GameRef], phases: list[str]) -> list[Item]:
    """Phase-major, then game by tip time, then t60 before t5."""
    return [Item(g, k, ph) for ph in phases for g in games for k in SNAPSHOT_KINDS]


def estimate_credits(cfg: HistoryConfig, items: list[Item]) -> dict[str, int]:
    """Credits still to spend for ``items`` (exact credit rules; files already on disk cost 0).

    Upper bound: empty responses are free and a market the vendor does not return is not charged.
    """
    events: set[Path] = set()
    ev_cost = odds_cost = n_odds = 0
    for it in items:
        epath = raw_path(cfg, it.game.season, it.game.game_id, "events", it.kind)
        opath = odds_path(
            cfg, it.game.season, it.game.game_id, it.kind, cfg.markets_for(it.phase), it.at(cfg)
        )
        if opath.exists():
            continue
        n_odds += 1
        odds_cost += cfg.expected_odds_cost(it.phase, it.at(cfg))
        if not epath.exists() and epath not in events:
            events.add(epath)
            ev_cost += cfg.cost_events_list
    return {
        "items": len(items),
        "odds_calls": n_odds,
        "events_calls": len(events),
        "credits_odds": odds_cost,
        "credits_events": ev_cost,
        "credits_total": odds_cost + ev_cost,
    }


# ----------------------------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------------------------


@dataclass
class PullReport:
    season: int
    phases: list[str]
    dry_run: bool = False
    n_games_total: int = 0
    n_games_no_tip: int = 0
    n_games_selected: int = 0
    estimate: dict[str, int] = field(default_factory=dict)
    full_plan_estimate: dict[str, int] = field(default_factory=dict)
    refused: str | None = None
    aborted: str | None = None
    items_done: int = 0
    items_empty: int = 0
    items_skipped_done: int = 0
    items_no_raw: int = 0
    match_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    credits_spent: int = 0
    requests: int = 0
    cache_hits: int = 0
    rows: int = 0
    books: Counter[str] = field(default_factory=Counter)
    unresolved: Counter[str] = field(default_factory=Counter)
    reasons: Counter[str] = field(default_factory=Counter)

    def lines(self) -> list[str]:
        e, f = self.estimate, self.full_plan_estimate
        out = [
            f"history-pull season={self.season} phases={'+'.join(self.phases)} "
            f"dry_run={self.dry_run}",
            f"games: total={self.n_games_total} no_real_tip={self.n_games_no_tip} "
            f"selected={self.n_games_selected}",
            f"estimate (this run, files on disk cost 0): odds_calls={e.get('odds_calls', 0)} "
            f"events_calls={e.get('events_calls', 0)} credits={e.get('credits_total', 0):,}",
            f"estimate (full plan {'+'.join(PHASES)} x all seasons): "
            f"credits={f.get('credits_total', 0):,}",
        ]
        if self.refused:
            out.append(f"REFUSED: {self.refused}")
        if self.dry_run:
            return out
        out += [
            f"done={self.items_done} (empty={self.items_empty}) skipped_already_done="
            f"{self.items_skipped_done} no_raw_skipped={self.items_no_raw} "
            f"match_failures={len(self.match_failures)} errors={len(self.errors)}",
            f"credits_spent_this_run={self.credits_spent:,} requests={self.requests} "
            f"cache_hits={self.cache_hits} rows={self.rows:,}",
            "books (rows): " + ", ".join(f"{k}={v}" for k, v in self.books.most_common(12)),
            f"unresolved player names: distinct={len(self.unresolved)} "
            f"rows={sum(self.unresolved.values())}; parse reasons: "
            + (", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "none"),
        ]
        out += [f"MATCH FAILURE: {m}" for m in self.match_failures[:10]]
        out += [f"ERROR: {m}" for m in self.errors[:10]]
        if self.aborted:
            out.append(f"ABORTED: {self.aborted}")
        return out


def run_pull(
    season: int,
    phase: str | None = None,
    *,
    dry_run: bool = False,
    max_games: int | None = None,
    reparse: bool = False,
    cfg: HistoryConfig | None = None,
    client: HistoricalClient | None = None,
    games: list[GameRef] | None = None,
    resolver: NameResolver | None = None,
    all_season_games: dict[int, list[GameRef]] | None = None,
) -> PullReport:
    """Pull (or, with ``dry_run``, only estimate) one season; ``phase`` None = props then games.

    ``games``/``all_season_games`` let tests inject the schedule; production reads
    ``nba.duckdb`` read-only plus the schedule parquet.
    """
    cfg = cfg or load_config()
    phases = [phase] if phase else list(PHASES)
    for ph in phases:
        cfg.markets_for(ph)  # validates
    if games is None:
        games, no_tip = load_games(cfg, season)
        total = len(games) + no_tip
    else:
        no_tip, total = 0, len(games)
    rep = PullReport(season=season, phases=phases, dry_run=dry_run)
    rep.n_games_total, rep.n_games_no_tip = total, no_tip

    state = PullState(cfg.state_path)
    all_items = build_items(games, phases)
    pending = [it for it in all_items if reparse or not state.done(it.key)]
    rep.items_skipped_done = len(all_items) - len(pending)
    if max_games is not None:
        keep: list[str] = []
        for it in pending:
            if it.game.game_id not in keep:
                if len(keep) >= max_games:
                    continue
                keep.append(it.game.game_id)
        pending = [it for it in pending if it.game.game_id in keep]
    rep.n_games_selected = len({it.game.game_id for it in pending})
    rep.estimate = estimate_credits(cfg, pending)

    full: list[Item] = []
    by_season = dict(all_season_games or {})
    by_season[season] = games
    for s in cfg.seasons:
        if s not in by_season:
            by_season[s] = load_games(cfg, s)[0]
        full += [
            it
            for it in build_items(by_season[s], list(PHASES))
            if reparse or not state.done(it.key)
        ]
    rep.full_plan_estimate = estimate_credits(cfg, full)

    ledger = CreditLedger(cfg.ledger_path, cfg.max_credits, cfg.min_server_remaining)
    spent = ledger.spent()
    if spent + rep.full_plan_estimate["credits_total"] > cfg.max_credits:
        rep.refused = (
            f"estimate {rep.full_plan_estimate['credits_total']:,} + already spent {spent:,} "
            f"exceeds max_credits {cfg.max_credits:,}"
        )
    if dry_run or rep.refused:
        return rep

    # reparse is cache-only: a keyless client cannot spend, and items without raw are skipped
    cli = client or HistoricalClient(cfg, need_key=not reparse)
    res = resolver or default_resolver()
    store = OddsStore(cfg.out_db)
    started = (cli.requests_made, cli.cache_hits, cli.credits_spent)
    try:
        for n, it in enumerate(pending, 1):
            _process(it, cfg, cli, res, store, state, rep, cache_only=reparse)
            if n % 50 == 0:
                log.info(
                    "progress %d/%d items, credits this run %d", n, len(pending), cli.credits_spent
                )
    except (CreditBudgetExceeded, AuthError, KeyMissingError) as exc:
        rep.aborted = f"{type(exc).__name__}: {exc}"
        log.error("history pull aborted: %s", rep.aborted)
    rep.credits_spent = cli.credits_spent - started[2]
    rep.requests = cli.requests_made - started[0]
    rep.cache_hits = cli.cache_hits - started[1]
    return rep


def _process(
    it: Item,
    cfg: HistoryConfig,
    cli: HistoricalClient,
    res: NameResolver,
    store: OddsStore,
    state: PullState,
    rep: PullReport,
    cache_only: bool = False,
) -> None:
    g, markets = it.game, list(cfg.markets_for(it.phase))
    at = it.at(cfg)
    opath = odds_path(cfg, g.season, g.game_id, it.kind, markets, at)
    credits_before = cli.credits_spent
    if cache_only and not opath.exists():
        rep.items_no_raw += 1
        return
    try:
        if opath.exists():
            fetched_body = load_raw(opath).get("body")
            cli.cache_hits += 1
            raw_rel = str(opath.relative_to(cfg.raw_dir))
        else:
            ev = cli.events(season=g.season, game_id=g.game_id, snapshot_kind=it.kind, at=at)
            event = match_event(parse_events(ev.body), g, res, cfg.event_match_window_h)
            od = cli.event_odds(
                season=g.season,
                game_id=g.game_id,
                snapshot_kind=it.kind,
                at=at,
                event_id=event.event_id,
                markets=markets,
            )
            fetched_body, raw_rel = od.body, str(od.path.relative_to(cfg.raw_dir))
        pr: ParseResult = parse_event_odds(
            fetched_body,
            season=g.season,
            game_id=g.game_id,
            snapshot_kind=it.kind,
            requested_at=at,
            raw_file=raw_rel,
            resolver=res,
        )
        n = store.replace(g.game_id, it.kind, markets, pr.rows)
    except EventMatchError as exc:
        msg = str(exc)
        log.error("EVENT MATCH FAILURE %s %s: %s", g.game_id, it.kind, msg)
        rep.match_failures.append(f"{it.kind} {msg}")
        state.set(it.key, "match_failed", detail=msg)
        return
    except (RequestRejected, UnreachableError) as exc:
        log.error("request failed %s %s %s: %s", g.game_id, it.kind, it.phase, exc)
        rep.errors.append(f"{g.game_id} {it.kind} {it.phase}: {exc}")
        state.set(it.key, "error", detail=str(exc))
        return
    status = "done" if n else "done_empty"
    state.set(it.key, status, rows=n, credits=cli.credits_spent - credits_before)
    rep.items_done += 1
    rep.items_empty += 0 if n else 1
    rep.rows += n
    rep.books.update(pr.books)
    rep.unresolved.update(pr.unresolved)
    rep.reasons.update(pr.reasons)
    log.info(
        "%s %s %s: rows=%d books=%d credits=%d [%s]",
        g.game_id,
        it.kind,
        it.phase,
        n,
        len(pr.books),
        cli.credits_spent - credits_before,
        pr.reasons_line(),
    )


# ----------------------------------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------------------------------


def status_lines(cfg: HistoryConfig | None = None) -> list[str]:
    cfg = cfg or load_config()
    led = CreditLedger(cfg.ledger_path, cfg.max_credits, cfg.min_server_remaining).totals()
    st = PullState(cfg.state_path)
    out = [
        f"ledger: spent={int(led['spent']):,} / max_credits={cfg.max_credits:,} "
        f"n_calls={led['n_calls']} server_remaining={led['server_remaining']} "
        f"(floor {cfg.min_server_remaining:,}) updated={led.get('updated_at')}",
        "state: " + (", ".join(f"{k}={v}" for k, v in sorted(st.counts().items())) or "empty"),
    ]
    store = OddsStore(cfg.out_db)
    try:
        by = store.read(
            "SELECT season, snapshot_kind, count(*), count(DISTINCT game_id) FROM odds_history "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )
        for s, k, n, ng in by:
            out.append(f"rows season={s} {k}: rows={n:,} games={ng}")
        cov = store.read(
            "SELECT book, count(DISTINCT game_id || snapshot_kind) FROM odds_history "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
        )
        tot = store.read("SELECT count(DISTINCT game_id || snapshot_kind) FROM odds_history")
        denom = int(tot[0][0]) if tot else 0
        out.append(
            f"book coverage (game-snapshots with >=1 row, of {denom}): "
            + ", ".join(f"{b}={n}" for b, n in cov)
        )
        for want in ("pinnacle", "novig", "prophetx"):
            hit = store.read(
                "SELECT count(DISTINCT game_id || snapshot_kind) FROM odds_history WHERE book = ?",
                [want],
            )
            out.append(f"  {want}: {hit[0][0] if hit else 0}/{denom}")
        un = store.read(
            "SELECT count(*), count(DISTINCT player_name) FROM odds_history "
            "WHERE player_name IS NOT NULL AND player_id IS NULL"
        )
        pl_rows = store.read("SELECT count(*) FROM odds_history WHERE player_name IS NOT NULL")
        if un:
            out.append(
                f"unresolved player rows={un[0][0]:,} of {pl_rows[0][0]:,}; "
                f"distinct unresolved names={un[0][1]}"
            )
    except (duckdb.Error, HistoryError) as exc:
        out.append(f"odds_history.duckdb: not readable now ({type(exc).__name__})")
    return out


# ----------------------------------------------------------------------------------------------
# Player alias table
# ----------------------------------------------------------------------------------------------


def build_aliases(cfg: HistoryConfig | None = None, *, refresh: bool = False) -> list[str]:
    """(Re)generate ``configs/odds_player_aliases.yaml`` and report name-match rates.

    The rates are computed from the stored vendor ``player_name`` values, so they do not depend on
    the ``player_id`` already in the table: Kalshi-only resolver (before) vs Kalshi + generated
    (after). Run ``history-pull --reparse`` afterwards to write the ids into ``odds_history``.
    """
    from nba.odds import player_aliases as pa

    cfg = cfg or load_config()
    players = pa.load_players(refresh=refresh)
    table = pa.build_table(players)
    base = NameResolver.default()
    before = pa.HistoryResolver(teams=base.teams, players=base.players)
    after = pa.HistoryResolver(
        teams=base.teams,
        players=base.players,
        generated=table.aliases,
        collisions=set(table.collisions),
    )
    store = OddsStore(cfg.out_db)
    counts: Counter[str] = Counter(
        {
            str(n): int(c)
            for n, c in store.read(
                "SELECT player_name, count(*) FROM odds_history "
                "WHERE player_name IS NOT NULL GROUP BY 1"
            )
        }
    )
    unmatched = pa.unmatched_names(counts, after)
    pa.write_table(pa.ALIASES_PATH, table, unmatched, 2022)
    total_rows, total_names = sum(counts.values()), len(counts)
    out = [
        f"players in cache: {players.height}; aliases={len(table.aliases)} "
        f"collisions={len(table.collisions)} (all-time collisions in full list: "
        f"{table.all_time_collisions})",
        f"vendor player names in odds_history: distinct={total_names} rows={total_rows:,}",
    ]
    for label, res in (("before (Kalshi only)", before), ("after (+generated)", after)):
        ok_rows = sum(c for n, c in counts.items() if res.player_id(n) is not None)
        ok_names = sum(1 for n in counts if res.player_id(n) is not None)
        out.append(
            f"name-match {label}: rows {ok_rows:,}/{total_rows:,} = "
            f"{ok_rows / max(1, total_rows):.1%}; distinct names {ok_names}/{total_names} = "
            f"{ok_names / max(1, total_names):.1%}"
        )
    hit_coll = [
        (n, c) for n, c in counts.items() if pa.normalize_player_name(n) in table.collisions
    ]
    out.append(
        f"vendor names hitting a collision: {len(hit_coll)} ({sum(c for _, c in hit_coll):,} rows)"
    )
    out.append(
        "top unmatched vendor names (rows): " + ", ".join(f"{n}={c}" for n, c in unmatched[:30])
    )
    return out
