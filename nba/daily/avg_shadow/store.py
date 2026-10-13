# ruff: noqa: E501
"""Tables of the AVG shadow (docs/prereg/AVG_SHADOW_LIVE.md, "What the steward builds" 1). Timestamps are naive UTC.

``forward_avg_shadow``   append-only, one row per run x game x player x stat (write-on-change); a row whose
                         game has tipped (``made_at >= tipoff_utc``) is refused, never written.
``forward_avg_scores``   settled rows (the latest row with ``made_at <= tipoff - 60 min``).
``forward_avg_heartbeat`` one row per inference run: start, finish, games seen, rows written, model_id, status, rc.
``forward_avg_meta``     key/value: counting start, proofs P1a-P4, weights shas, the gate's fit_through and sha,
                         the W_ALL season list, the data_version of the rebuild export. Counting start is written
                         by the steward ONLY after every proof passed (``set_counting_start`` refuses otherwise).
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

STATS = ("pts", "reb", "ast", "fg3m")
PROOFS = ("P1a", "P1b", "P2", "P3", "P4")
STATUSES = (
    "ok",
    "skipped",
    "failed",
    "no_full",
)  # no_full: inference ran but no FULL grid exists to mix (counts as bad)

DDL = """
CREATE TABLE IF NOT EXISTS forward_avg_shadow (
    made_at TIMESTAMP,          -- known-at: every input was knowable at this instant
    run_id VARCHAR,
    game_id VARCHAR,
    player_id INT,
    stat VARCHAR,               -- pts | reb | ast | fg3m
    tipoff_utc TIMESTAMP,
    tip_source VARCHAR,         -- real (never a proxy)
    in_prod_log BOOLEAN,
    n_prior INT,
    model_id VARCHAR,           -- sha256 of the weights bundle
    fit_through DATE,           -- last date in the warm pool
    month_block VARCHAR,        -- YYYY-MM the bundle serves
    installed_at TIMESTAMP,
    gate_id VARCHAR,
    gate_fit_through DATE,
    stale_model BOOLEAN,
    avg_pmf199 JSON,            -- 199 equal-mass integer values of the AVG mixture (descriptive; avg_q19 is reproducible from it)
    seqc_pmf199 JSON,           -- 199 equal-mass integer values of SEQc (descriptive)
    avg_q19 JSON,               -- integer quantiles at taus 0.05..0.95, the scored object
    seqc_q19 JSON,
    seq_q19 JSON,               -- the transformer alone (continuous, as exported), descriptive
    full_q19 JSON,              -- the FULL grid used (latest forward_full_shadow row at make time)
    a0_q19 JSON,                -- the A0 grid used (latest production row at make time)
    a0_source VARCHAR           -- props_context_residual_lt (pts) | props_context_residual
);
CREATE TABLE IF NOT EXISTS forward_avg_scores (
    scored_at TIMESTAMP,
    game_id VARCHAR,
    game_date DATE,
    season INT,
    player_id INT,
    stat VARCHAR,
    run_id VARCHAR,
    made_at TIMESTAMP,
    tipoff_utc TIMESTAMP,
    played BOOLEAN,
    y DOUBLE,
    crps_avg DOUBLE,
    crps_full DOUBLE,
    crps_a0 DOUBLE,
    crps_seqc DOUBLE,
    crps_seq DOUBLE,
    stale_model BOOLEAN,
    status VARCHAR              -- scored | not_in_box
);
CREATE TABLE IF NOT EXISTS forward_avg_heartbeat (
    slate_date DATE,
    run_id VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    games_seen INT,
    rows_written INT,
    model_id VARCHAR,
    status VARCHAR,             -- ok | skipped | failed
    rc INT,
    detail VARCHAR
);
CREATE TABLE IF NOT EXISTS forward_avg_meta (
    key VARCHAR,
    value VARCHAR,
    written_at TIMESTAMP
);
"""

COLUMNS = [
    "made_at", "run_id", "game_id", "player_id", "stat", "tipoff_utc", "tip_source", "in_prod_log",
    "n_prior", "model_id", "fit_through", "month_block", "installed_at", "gate_id", "gate_fit_through",
    "stale_model", "avg_pmf199", "seqc_pmf199", "avg_q19", "seqc_q19", "seq_q19", "full_q19", "a0_q19",
    "a0_source",
]  # fmt: skip

#: content that makes two rows "the same" for write-on-change
SIG_COLUMNS = ["avg_q19", "seqc_q19", "seq_q19", "full_q19", "a0_q19", "model_id", "stale_model"]
JSON_COLUMNS = {"avg_pmf199", "seqc_pmf199", "avg_q19", "seqc_q19", "seq_q19", "full_q19", "a0_q19"}


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def _canon(col: str, v: object) -> object:
    return (
        json.dumps(json.loads(v), sort_keys=True)
        if col in JSON_COLUMNS and isinstance(v, str)
        else v
    )


def append_rows(
    con: duckdb.DuckDBPyConnection, rows: list[dict[str, object]], made_at: datetime
) -> tuple[int, int, int]:
    """Append rows; returns ``(written, deduped, refused)``. A row with ``made_at >= tipoff_utc`` is refused.
    Write-on-change against the latest stored row of the same (game, player, stat)."""
    ensure_tables(con)
    ok = [r for r in rows if made_at < r["tipoff_utc"]]  # type: ignore[operator]
    refused = len(rows) - len(ok)
    if not ok:
        return 0, 0, refused
    games = sorted({str(r["game_id"]) for r in ok})
    marks = ",".join("?" for _ in games)
    cols = ", ".join(f"CAST({c} AS VARCHAR)" if c in JSON_COLUMNS else c for c in SIG_COLUMNS)
    latest = {
        (str(r[0]), int(r[1]), str(r[2])): tuple(
            _canon(c, v) for c, v in zip(SIG_COLUMNS, r[3:], strict=True)
        )
        for r in con.execute(
            f"""SELECT game_id, player_id, stat, {cols}
                FROM (SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat
                                                   ORDER BY made_at DESC, run_id DESC) rn
                      FROM forward_avg_shadow WHERE game_id IN ({marks})) WHERE rn = 1""",
            games,
        ).fetchall()
    }
    new = []
    for r in ok:
        key = (str(r["game_id"]), int(r["player_id"]), str(r["stat"]))  # type: ignore[call-overload]
        sig = tuple(_canon(c, r[c]) for c in SIG_COLUMNS)
        if latest.get(key) == sig:
            continue
        new.append(r)
    if new:
        marks = ",".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT INTO forward_avg_shadow ({', '.join(COLUMNS)}) VALUES ({marks})",
            [tuple(r[c] for c in COLUMNS) for r in new],
        )
    return len(new), len(ok) - len(new), refused


def write_heartbeat(
    con: duckdb.DuckDBPyConnection,
    *,
    slate_date: object,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    games_seen: int,
    rows_written: int,
    model_id: str | None,
    status: str,
    rc: int,
    detail: str = "",
) -> None:
    if status not in STATUSES:
        raise ValueError(f"heartbeat status {status!r} not in {STATUSES}")
    ensure_tables(con)
    con.execute(
        "INSERT INTO forward_avg_heartbeat VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            slate_date,
            run_id,
            started_at,
            finished_at,
            games_seen,
            rows_written,
            model_id,
            status,
            rc,
            detail[:500],
        ],
    )


def get_meta(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    ensure_tables(con)
    return {
        str(k): str(v)
        for k, v in con.execute(
            "SELECT key, arg_max(value, written_at) FROM forward_avg_meta GROUP BY key"
        ).fetchall()
    }


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str, at: datetime) -> None:
    ensure_tables(con)
    con.execute("INSERT INTO forward_avg_meta VALUES (?,?,?)", [key, value, at])


def set_counting_start(con: duckdb.DuckDBPyConnection, start: str, at: datetime) -> None:
    """The steward writes the counting start ONLY after P1a, P1b, P2, P3, P4 all passed (rule, "Counting starts")."""
    meta = get_meta(con)
    missing = [p for p in PROOFS if meta.get(f"proof_{p}") != "pass"]
    if missing:
        raise PermissionError(f"counting cannot start: proofs not passed: {missing}")
    set_meta(con, "counting_start", start, at)
