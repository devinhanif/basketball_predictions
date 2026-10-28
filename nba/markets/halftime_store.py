# ruff: noqa: E501
"""Append-only paper table for the halftime scorer (its own DuckDB file; one writer).

Every scored row (model P, glm_e P, ask, fee, depth, state, stamps) is appended before the second half
starts; a row stamped after is refused (``SecondHalfStartedError``) and the refusal is counted in a
second table. Rows are never updated or deleted: this module has no UPDATE / DELETE statement
(a test greps for them), and the primary key (event_id, ticker) makes a second score of the same
quote a no-op rather than a rewrite. No outcome column exists: scoring is a later, frozen rule's job.

The store refuses a path inside ``data/kalshi`` and ``nba.duckdb`` (other writers' files).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS halftime_scored (
    written_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', current_timestamp),
    mode VARCHAR NOT NULL,
    min_prior INTEGER NOT NULL,
    made_at TIMESTAMP NOT NULL,
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
    p_glm_e DOUBLE NOT NULL,
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
    t_ht TIMESTAMP NOT NULL,
    t_star TIMESTAMP NOT NULL,
    quote_received_at TIMESTAMP NOT NULL,
    quote_file VARCHAR NOT NULL,
    state_received_at TIMESTAMP NOT NULL,
    state_file VARCHAR NOT NULL,
    within_prereg_window BOOLEAN NOT NULL,
    artifact_digest VARCHAR NOT NULL,
    PRIMARY KEY (event_id, ticker)
);
CREATE TABLE IF NOT EXISTS halftime_refused (
    refused_at TIMESTAMP NOT NULL,
    event_id VARCHAR NOT NULL,
    series VARCHAR NOT NULL,
    reason VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS halftime_skipped (
    mode VARCHAR NOT NULL,
    seen_at TIMESTAMP NOT NULL,
    event_id VARCHAR NOT NULL,
    series VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    n INTEGER NOT NULL
);
"""

#: The columns the caller supplies. ``written_at`` (DuckDB wall clock), ``mode`` (the store's) and
#: ``within_prereg_window`` (computed in SQL from ``written_at`` in live mode) are never caller-set.
COLUMNS = [
    "made_at", "slate_date", "event_id", "game", "series", "stat", "ticker", "player_name",
    "player_id", "team", "k", "ask_c", "bid_c", "ask_size", "depth_5usd", "fee_c", "fee_model",
    "fee_coefficient", "fee_multiplier", "p_model", "p_glm_e", "edge_model", "edge_glm_e", "cur",
    "minutes", "starter", "fouls", "margin", "pregame_mean", "rec_minutes", "n_prior", "t_ht",
    "t_star", "quote_received_at", "quote_file", "state_received_at", "state_file",
    "artifact_digest",
]  # fmt: skip

#: A paper-table edge of at least this much counts toward the prereg gate's share f.
EDGE_THRESHOLD = 0.05
PREREG_WINDOW_S = 180  # a live row is "in window" if written within 180 s of t* (DuckDB clock)
MODES = ("live", "replay")
#: T508 needs a pregame mean over this many games; f counts only rows scored with at least this.
MIN_PRIOR_FOR_F = 5


class SecondHalfStartedError(RuntimeError):
    """A row was offered after the second half started; it is refused, never stored."""


class ForeignStoreError(ValueError):
    """The path is another job's store, or the wrong mode for this store."""


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def live_store_path() -> Path:
    """The live paper table named in configs/jarvis.yaml ``halftime_overs.store`` (read as plain YAML)."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "configs" / "jarvis.yaml").read_text())
    return (root / str(raw["halftime_overs"]["store"])).resolve()


@dataclass
class HalftimeStore:
    con: duckdb.DuckDBPyConnection
    mode: str = "replay"

    @classmethod
    def open(cls, path: str | Path, mode: str = "replay") -> HalftimeStore:
        """Open a store. ``mode`` is 'live' (the JARVIS tick) or 'replay' (stored captures).

        A replay is refused into the configured live store path and into any store that already holds
        live rows (and a live writer into one that holds replay rows): backdated rows must never mix
        with the forward log.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if str(path) != ":memory:":
            p = Path(path)
            if p.name == "nba.duckdb" or "data/kalshi" in p.as_posix():
                raise ForeignStoreError(f"{p} belongs to another writer; use a halftime store")
            if mode == "replay" and p.resolve() == live_store_path():
                raise ForeignStoreError(
                    f"{p} is the live paper table; a replay may not write to it"
                )
            p.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(SCHEMA)
        other = con.execute(
            "SELECT 1 FROM halftime_scored WHERE mode <> ? LIMIT 1", [mode]
        ).fetchone()
        if other is not None:
            con.close()
            raise ForeignStoreError(f"{path} already holds rows of the other mode than {mode!r}")
        return cls(con, mode)

    def close(self) -> None:
        self.con.close()

    def has(self, event_id: str, series: str) -> bool:
        r = self.con.execute(
            "SELECT 1 FROM halftime_scored WHERE event_id = ? AND series = ? LIMIT 1",
            [event_id, series],
        ).fetchone()
        return r is not None

    def append(
        self,
        rows: Iterable[dict[str, Any]],
        made_at: datetime,
        second_half_started: bool,
        min_prior: int = MIN_PRIOR_FOR_F,
    ) -> int:
        """Insert scored rows; refuse everything when the second half has started.

        ``written_at`` is set by DuckDB. ``within_prereg_window`` is computed in SQL: in live mode it is
        ``written_at <= t_star + 180 s``; a replay row is never in the window (it is not a forward row).
        """
        rows = list(rows)
        if not rows:
            return 0
        if second_half_started:
            r0 = rows[0]
            if not self.con.execute(
                "SELECT 1 FROM halftime_refused WHERE event_id = ? AND series = ? LIMIT 1",
                [r0["event_id"], r0["series"]],
            ).fetchone():
                self.con.execute(
                    "INSERT INTO halftime_refused VALUES (?, ?, ?, ?)",
                    [_naive(made_at), r0["event_id"], r0["series"], "second_half_started"],
                )
            raise SecondHalfStartedError(
                f"{r0['game']} {r0['series']}: second half started before made_at "
                f"{made_at.isoformat()}; {len(rows)} rows refused"
            )
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
            args = [self.mode, min_prior, *vals]
            if self.mode == "live":
                args.append(_naive(r["t_star"]))
            cur = self.con.execute(
                f"INSERT INTO halftime_scored (mode, min_prior, {cols}, within_prereg_window) "
                f"VALUES (?, ?, {marks}, {win}) ON CONFLICT DO NOTHING RETURNING 1",
                args,
            )
            n += len(cur.fetchall())
        return n

    def note_skip(self, seen_at: datetime, event_id: str, series: str, reason: str, n: int) -> None:
        """Persist one counted exclusion per (event, series, reason); a repeat is ignored."""
        seen = self.con.execute(
            "SELECT 1 FROM halftime_skipped WHERE mode = ? AND event_id = ? AND series = ? "
            "AND reason = ? LIMIT 1",
            [self.mode, event_id, series, reason],
        ).fetchone()
        if seen is None:
            self.con.execute(
                "INSERT INTO halftime_skipped VALUES (?, ?, ?, ?, ?, ?)",
                [self.mode, _naive(seen_at), event_id, series, reason, n],
            )

    def rows(
        self, event_id: str | None = None, slate_date: date | None = None
    ) -> list[dict[str, Any]]:
        extra = ["written_at", "mode", "min_prior", "within_prereg_window"]
        q = "SELECT " + ", ".join(COLUMNS + extra) + " FROM halftime_scored WHERE true"
        args: list[Any] = []
        if event_id is not None:
            q += " AND event_id = ?"
            args.append(event_id)
        if slate_date is not None:
            q += " AND slate_date = ?"
            args.append(slate_date)
        q += " ORDER BY made_at, ticker"
        return [
            dict(zip(COLUMNS + extra, r, strict=True)) for r in self.con.execute(q, args).fetchall()
        ]

    def share_f(
        self, threshold: float = EDGE_THRESHOLD, min_prior: int = MIN_PRIOR_FOR_F
    ) -> dict[str, Any]:
        """Count-only f: units (event, player, stat) whose best strike has edge_model >= threshold.

        Counts rows scored with ``min_prior`` of at least ``MIN_PRIOR_FOR_F`` only, and, in a live
        store, only rows written inside the prereg window. A replay store has no window (rows are
        hypothetical stamps); its f is a label-free gate measurement, never a forward claim.
        Reads no outcome (the table has none).
        """
        win = "AND within_prereg_window" if self.mode == "live" else ""
        r = self.con.execute(
            f"""
            WITH u AS (
                SELECT event_id, player_name, stat, max(edge_model) AS best,
                       max(edge_glm_e) AS best_glm
                FROM halftime_scored WHERE min_prior >= ? AND mode = ? {win} GROUP BY 1, 2, 3
            )
            SELECT count(*), count(*) FILTER (best >= ?), count(*) FILTER (best_glm >= ?),
                   count(*) FILTER (best >= ? AND best_glm >= ?)
            FROM u
            """,
            [min_prior, self.mode, *([threshold - 1e-12] * 4)],
        ).fetchone()
        assert r is not None
        n, hit, hit_glm, both = (int(x) for x in r)
        return {
            "mode": self.mode,
            "units": n,
            "units_model_edge_ge": hit,
            "units_glm_edge_ge": hit_glm,
            "units_both": both,
            "f_model": (hit / n) if n else None,
            "f_glm_e": (hit_glm / n) if n else None,
            "threshold": threshold,
        }
