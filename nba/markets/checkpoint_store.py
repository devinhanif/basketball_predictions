# ruff: noqa: E501
"""Append-only paper table for the in-game checkpoint scorer (its own DuckDB file; one writer).

A SIBLING of ``halftime_store`` (the halftime table keeps its (event_id, ticker) key and its rows are
never touched): the same row content plus the checkpoint columns, keyed (event_id, ticker,
checkpoint). Rows are never updated or deleted (a test greps this module for UPDATE / DELETE), a
second score of the same quote at the same checkpoint is a no-op, and there is no outcome column.

The store refuses a path inside ``data/kalshi``, ``nba.duckdb`` and the halftime store, and a replay
into the configured live path. The mode is written once on first open and is the lock (as the
halftime store's).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoint_scored (
    written_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', current_timestamp),
    mode VARCHAR NOT NULL,
    min_prior INTEGER NOT NULL,
    checkpoint INTEGER NOT NULL,          -- nominal elapsed game minutes: 5, 10, ..., 45
    e_used DOUBLE NOT NULL,               -- actual elapsed game minutes of the state scored (>= checkpoint)
    model_support VARCHAR NOT NULL,       -- 'trained' (6 <= e <= 42) | 'extrapolated' (outside T508's checkpoint range)
    period INTEGER NOT NULL,
    clock VARCHAR NOT NULL,
    made_at TIMESTAMP NOT NULL,
    known_at TIMESTAMP NOT NULL,          -- max(quote page, state summary) received_at: when every input existed
    slate_date DATE NOT NULL,
    event_id VARCHAR NOT NULL,
    game VARCHAR NOT NULL,
    series VARCHAR NOT NULL,
    stat VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    player_name VARCHAR NOT NULL,
    player_id INTEGER,
    team VARCHAR NOT NULL,
    k INTEGER NOT NULL,
    ask_c INTEGER NOT NULL,
    bid_c INTEGER,
    ask_size DOUBLE NOT NULL,
    depth_5usd BOOLEAN NOT NULL,
    fee_c INTEGER NOT NULL,
    fee_model VARCHAR NOT NULL,
    fee_coefficient DOUBLE NOT NULL,
    fee_multiplier DOUBLE NOT NULL,
    p_model DOUBLE NOT NULL,
    p_glm_e DOUBLE NOT NULL,              -- glm_e is fitted at e = 24 only: off its support at every other checkpoint
    edge_model DOUBLE NOT NULL,
    edge_glm_e DOUBLE NOT NULL,
    cur INTEGER NOT NULL,
    minutes INTEGER NOT NULL,
    starter INTEGER NOT NULL,
    fouls INTEGER NOT NULL,
    margin INTEGER NOT NULL,
    pregame_mean DOUBLE NOT NULL,
    rec_minutes DOUBLE NOT NULL,
    n_prior INTEGER NOT NULL,
    quote_received_at TIMESTAMP NOT NULL,
    quote_file VARCHAR NOT NULL,
    state_received_at TIMESTAMP NOT NULL,
    state_file VARCHAR NOT NULL,
    last_play_at TIMESTAMP,               -- ESPN's wallclock of the newest play in the state summary
    play_age_s DOUBLE,                    -- known_at - last_play_at
    lateness_s DOUBLE NOT NULL,           -- elapsed seconds of the state past the nominal checkpoint
    within_prereg_window BOOLEAN NOT NULL,
    artifact_digest VARCHAR NOT NULL,
    PRIMARY KEY (event_id, ticker, checkpoint)
);
CREATE TABLE IF NOT EXISTS checkpoint_meta (
    mode VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', current_timestamp)
);
CREATE TABLE IF NOT EXISTS checkpoint_skipped (
    mode VARCHAR NOT NULL,
    seen_at TIMESTAMP NOT NULL,
    event_id VARCHAR NOT NULL,
    series VARCHAR NOT NULL,
    checkpoint INTEGER NOT NULL,
    reason VARCHAR NOT NULL,
    n INTEGER NOT NULL
);
"""

#: The columns the caller supplies. ``written_at``, ``mode``, ``min_prior`` and
#: ``within_prereg_window`` are the store's.
COLUMNS = [
    "checkpoint", "e_used", "model_support", "period", "clock", "made_at", "known_at", "slate_date",
    "event_id", "game", "series", "stat", "ticker", "player_name", "player_id", "team", "k", "ask_c",
    "bid_c", "ask_size", "depth_5usd", "fee_c", "fee_model", "fee_coefficient", "fee_multiplier",
    "p_model", "p_glm_e", "edge_model", "edge_glm_e", "cur", "minutes", "starter", "fouls", "margin",
    "pregame_mean", "rec_minutes", "n_prior", "quote_received_at", "quote_file",
    "state_received_at", "state_file", "last_play_at", "play_age_s", "lateness_s", "artifact_digest",
]  # fmt: skip

EDGE_THRESHOLD = 0.05
#: A live row is "in window" if it was written within this many seconds of ``known_at`` (DuckDB clock).
PREREG_WINDOW_S = 180
MODES = ("live", "replay")
MIN_PRIOR_FOR_F = 5


class ForeignStoreError(ValueError):
    """The path is another job's store, or the wrong mode for this store."""


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def live_store_path() -> Path:
    """``configs/jarvis.yaml`` ``ingame_checkpoints.store`` (plain YAML; no JARVIS import)."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "configs" / "jarvis.yaml").read_text())
    return (root / str(raw["ingame_checkpoints"]["store"])).resolve()


def _foreign(p: Path) -> bool:
    return p.name in ("nba.duckdb", "halftime_overs.duckdb") or "data/kalshi" in p.as_posix()


@dataclass
class CheckpointStore:
    con: duckdb.DuckDBPyConnection
    mode: str = "replay"

    @classmethod
    def open(cls, path: str | Path, mode: str = "replay") -> CheckpointStore:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if str(path) != ":memory:":
            p = Path(path)
            if _foreign(p):
                raise ForeignStoreError(f"{p} belongs to another writer; use a checkpoint store")
            if mode == "replay" and p.resolve() == live_store_path():
                raise ForeignStoreError(
                    f"{p} is the live checkpoint table; a replay may not write to it"
                )
            p.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(SCHEMA)
        metas = {r[0] for r in con.execute("SELECT mode FROM checkpoint_meta").fetchall()}
        metas |= {
            r[0] for r in con.execute("SELECT DISTINCT mode FROM checkpoint_scored").fetchall()
        }
        if metas - {mode}:
            con.close()
            raise ForeignStoreError(f"{path} already holds rows of the other mode than {mode!r}")
        if not metas:
            con.execute("INSERT INTO checkpoint_meta (mode) VALUES (?)", [mode])
        return cls(con, mode)

    def close(self) -> None:
        self.con.close()

    def has(self, event_id: str, series: str, checkpoint: int) -> bool:
        """True when this (game, series, checkpoint) already has stored rows: never scored twice."""
        r = self.con.execute(
            "SELECT 1 FROM checkpoint_scored WHERE event_id = ? AND series = ? AND checkpoint = ? LIMIT 1",
            [event_id, series, checkpoint],
        ).fetchone()
        return r is not None

    def noted(self, event_id: str, series: str, checkpoint: int) -> bool:
        """True when this (game, series, checkpoint) has a FINAL skip note: it is closed, not retried."""
        r = self.con.execute(
            "SELECT 1 FROM checkpoint_skipped WHERE mode = ? AND event_id = ? AND series = ? "
            "AND checkpoint = ? LIMIT 1",
            [self.mode, event_id, series, checkpoint],
        ).fetchone()
        return r is not None

    def skipped(self, event_id: str, series: str, checkpoint: int, reason: str) -> bool:
        r = self.con.execute(
            "SELECT 1 FROM checkpoint_skipped WHERE mode = ? AND event_id = ? AND series = ? "
            "AND checkpoint = ? AND reason = ? LIMIT 1",
            [self.mode, event_id, series, checkpoint, reason],
        ).fetchone()
        return r is not None

    def append(
        self, rows: Iterable[dict[str, Any]], made_at: datetime, min_prior: int = MIN_PRIOR_FOR_F
    ) -> int:
        """Insert scored rows (conflicts are no-ops). ``within_prereg_window`` is computed in SQL from
        the database clock: live, ``written_at <= known_at + 180 s``; a replay row is never in it.
        Do not wrap in BEGIN/COMMIT (current_timestamp is the transaction start)."""
        n = 0
        cols = ", ".join(COLUMNS)
        marks = ", ".join("?" for _ in COLUMNS)
        for r in rows:
            if _naive(r["made_at"]) != _naive(made_at):
                raise ValueError("every row carries the stamp of the call that stores it")
            vals = [_naive(r[c]) if isinstance(r[c], datetime) else r[c] for c in COLUMNS]
            win = (
                f"timezone('UTC', current_timestamp) <= ? + INTERVAL {PREREG_WINDOW_S} SECOND"
                if self.mode == "live"
                else "false"
            )
            args: list[Any] = [self.mode, min_prior, *vals]
            if self.mode == "live":
                args.append(_naive(r["known_at"]))
            cur = self.con.execute(
                f"INSERT INTO checkpoint_scored (mode, min_prior, {cols}, within_prereg_window) "
                f"VALUES (?, ?, {marks}, {win}) ON CONFLICT DO NOTHING RETURNING 1",
                args,
            )
            n += len(cur.fetchall())
        return n

    def note_skip(
        self, seen_at: datetime, event_id: str, series: str, checkpoint: int, reason: str, n: int
    ) -> None:
        """One counted exclusion per (event, series, checkpoint, reason); a repeat is ignored."""
        if not self.skipped(event_id, series, checkpoint, reason):
            self.con.execute(
                "INSERT INTO checkpoint_skipped VALUES (?, ?, ?, ?, ?, ?, ?)",
                [self.mode, _naive(seen_at), event_id, series, checkpoint, reason, n],
            )

    def rows(
        self, slate_date: date | None = None, event_id: str | None = None
    ) -> list[dict[str, Any]]:
        extra = ["written_at", "mode", "min_prior", "within_prereg_window"]
        q = "SELECT " + ", ".join(COLUMNS + extra) + " FROM checkpoint_scored WHERE true"
        args: list[Any] = []
        if slate_date is not None:
            q += " AND slate_date = ?"
            args.append(slate_date)
        if event_id is not None:
            q += " AND event_id = ?"
            args.append(event_id)
        q += " ORDER BY known_at, checkpoint, ticker"
        return [
            dict(zip(COLUMNS + extra, r, strict=True)) for r in self.con.execute(q, args).fetchall()
        ]

    def latest_by_game(self, slate_date: date) -> dict[str, list[dict[str, Any]]]:
        """Per game ("AWAY@HOME"), the rows of its newest stored checkpoint (any series), for the card."""
        q = (
            "SELECT " + ", ".join("s." + c for c in COLUMNS) + " FROM checkpoint_scored s JOIN "
            "(SELECT game, max(checkpoint) AS c FROM checkpoint_scored WHERE slate_date = ? GROUP BY game) m "
            "ON s.game = m.game AND s.checkpoint = m.c WHERE s.slate_date = ? ORDER BY s.ticker"
        )
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.con.execute(q, [slate_date, slate_date]).fetchall():
            row = dict(zip(COLUMNS, r, strict=True))
            out[row["game"]].append(row)
        return dict(out)

    def counts_by_checkpoint(
        self, threshold: float = EDGE_THRESHOLD, min_prior: int = MIN_PRIOR_FOR_F
    ) -> list[dict[str, Any]]:
        """Label-free counts per checkpoint: games, units, rows, and the share f of units whose best
        strike has edge_model >= threshold (live stores: in-window rows only). Reads no outcome."""
        win = "AND within_prereg_window" if self.mode == "live" else ""
        rs = self.con.execute(
            f"""
            WITH u AS (
                SELECT checkpoint, event_id, player_name, stat, max(edge_model) AS best,
                       max(edge_glm_e) AS best_glm, count(*) AS n_rows
                FROM checkpoint_scored WHERE min_prior >= ? AND mode = ? {win} GROUP BY ALL
            )
            SELECT checkpoint, count(DISTINCT event_id), count(*), sum(n_rows),
                   count(*) FILTER (best >= ?), count(*) FILTER (best_glm >= ?)
            FROM u GROUP BY checkpoint ORDER BY checkpoint
            """,
            [min_prior, self.mode, threshold - 1e-12, threshold - 1e-12],
        ).fetchall()
        return [
            {
                "checkpoint": int(c), "games": int(g), "units": int(u), "rows": int(n),
                "units_model_edge_ge": int(h), "units_glm_edge_ge": int(hg),
                "f_model": (h / u) if u else None, "f_glm_e": (hg / u) if u else None,
            }
            for c, g, u, n, h, hg in rs
        ]  # fmt: skip

    def entry_profile(
        self,
        checkpoints: tuple[int, ...],
        threshold: float = EDGE_THRESHOLD,
        min_prior: int = MIN_PRIOR_FOR_F,
        column: str = "edge_model",
    ) -> dict[str, Any]:
        """Label-free count of the FIRST-TRIGGER entries of the late-game family (draft rule s12.3).

        Per unit (game, player, stat): at each checkpoint in ``checkpoints`` the strike with the largest
        edge (ties: lower strike); the unit enters at the earliest checkpoint whose best edge clears
        ``threshold``. Returns units, entries, entries by checkpoint, the mean ask, and the RMS null SD of
        P&L per $1 staked, ``sqrt(a (1 - a)) / (a + fee)``, over the entries (the power input). Reads no
        outcome (the table has none); live stores count in-window rows only.
        """
        if column not in ("edge_model", "edge_glm_e"):
            raise ValueError(column)
        win = "AND within_prereg_window" if self.mode == "live" else ""
        marks = ", ".join("?" for _ in checkpoints)
        rs = self.con.execute(
            f"""
            WITH best AS (
                SELECT checkpoint, event_id, player_name, stat, ask_c, fee_c, {column} AS edge,
                       row_number() OVER (PARTITION BY checkpoint, event_id, player_name, stat
                                          ORDER BY {column} DESC, k ASC) AS rn
                FROM checkpoint_scored
                WHERE min_prior >= ? AND mode = ? AND checkpoint IN ({marks}) {win}
            ), trig AS (
                SELECT *, row_number() OVER (PARTITION BY event_id, player_name, stat
                                             ORDER BY checkpoint) AS fire
                FROM best WHERE rn = 1 AND edge >= ?
            ), units AS (
                SELECT count(DISTINCT (event_id, player_name, stat)) AS n FROM best
            )
            SELECT t.checkpoint, count(*), avg(t.ask_c / 100.0),
                   avg(power(sqrt((t.ask_c / 100.0) * (1 - t.ask_c / 100.0)) / (t.ask_c / 100.0 + t.fee_c / 100.0), 2)),
                   (SELECT n FROM units)
            FROM trig t WHERE fire = 1 GROUP BY t.checkpoint ORDER BY t.checkpoint
            """,
            [min_prior, self.mode, *checkpoints, threshold - 1e-12],
        ).fetchall()
        n_units = int(rs[0][4]) if rs else 0
        n = sum(int(r[1]) for r in rs)
        var = sum(float(r[3]) * int(r[1]) for r in rs) / n if n else None
        return {
            "checkpoints": list(checkpoints), "column": column, "threshold": threshold,
            "units": n_units, "entries": n, "share_f": (n / n_units) if n_units else None,
            "by_checkpoint": {int(r[0]): int(r[1]) for r in rs},
            "mean_ask": (sum(float(r[2]) * int(r[1]) for r in rs) / n) if n else None,
            "rms_sd_per_dollar": (var**0.5) if var is not None else None,
        }  # fmt: skip
