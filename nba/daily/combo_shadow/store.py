# ruff: noqa: E501
"""Tables of the COMBO_STOCKS shadow (docs/prereg/COMBO_STOCKS_SHADOW.md, "Live tables"). Timestamps are naive UTC.

``forward_combo_shadow``    append-only, one row per run x game x player x target (write-on-change); a row whose game has tipped
                            (``made_at >= tipoff_utc``) is refused, never written.
``forward_combo_scores``    settled rows (the latest row with ``made_at <= tipoff - 60 min``).
``forward_combo_heartbeat`` one row per run: start, finish, games seen, rows written, model_id, status, rc.
``forward_combo_meta``      key/value: counting start, proofs, R / base / config shas. Counting start is written by the steward ONLY
                            after P1-P3 and P4 passed (``set_counting_start`` refuses otherwise).

Not read by ``nba.daily.canonical_dump`` (it reads only ``forward_predictions`` and ``forward_scores``), which is why the replay
oracle cannot move through this writer unless it perturbs production state (proof P3).
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

TARGETS = ("blk", "stl", "pra", "pr", "pa", "ra", "bs")
PROOFS = ("P1", "P2", "P3", "P4")
STATUSES = ("ok", "skipped", "failed")

DDL = """
CREATE TABLE IF NOT EXISTS forward_combo_shadow (   -- append-only, write-on-change, one row per run x game x player x target
    made_at TIMESTAMP, run_id VARCHAR, game_id VARCHAR, player_id INT,
    target VARCHAR,                -- blk | stl | pra | pr | pa | ra | bs
    tipoff_utc TIMESTAMP, tip_source VARCHAR,          -- real, never a proxy; a row with made_at >= tipoff_utc is refused
    in_prod_log BOOLEAN, n_prior INT,
    model_id VARCHAR,              -- sha256 of (NB head recipe + learner params + lightgbm version, configs/combo_stocks_shadow.yaml sha, base-normal sha)
    fit_through DATE, r_id VARCHAR, base_id VARCHAR,   -- sha of R and of the frozen base normals; must equal the config values
    s_pmf199 JSON, s_q19 JSON,     -- S, the scored object; s_q19 = q19_of_grid199(s_pmf199), checked exactly
    indep_pmf199 JSON, recent_pmf199 JSON,             -- comparators; indep only for pra, pr, pa, ra, bs
    direct_pmf199 JSON, recent_cal_pmf199 JSON,        -- optional descriptive arms (NULL when not built)
    src JSON                       -- provenance of the marginals: {pts:{run_id,made_at}, reb:{...}, ast:{...}, fg3m:{...}}
);
CREATE TABLE IF NOT EXISTS forward_combo_scores (    -- settled: the latest row with made_at <= tipoff - 60 min, once the box is in
    scored_at TIMESTAMP, game_id VARCHAR, game_date DATE, season INT, player_id INT, target VARCHAR,
    run_id VARCHAR, made_at TIMESTAMP, tipoff_utc TIMESTAMP, played BOOLEAN, y DOUBLE,
    crps_s DOUBLE, crps_recent DOUBLE, crps_indep DOUBLE, crps_direct DOUBLE, crps_recent_cal DOUBLE, status VARCHAR  -- scored | not_in_box
);
CREATE TABLE IF NOT EXISTS forward_combo_heartbeat (slate_date DATE, run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
    games_seen INT, rows_written INT, model_id VARCHAR, status VARCHAR, rc INT, detail VARCHAR);   -- ok | skipped | failed
CREATE TABLE IF NOT EXISTS forward_combo_meta (key VARCHAR, value VARCHAR, written_at TIMESTAMP); -- counting start, proofs, R sha, base sha, config sha
"""

COLUMNS = [
    "made_at", "run_id", "game_id", "player_id", "target", "tipoff_utc", "tip_source", "in_prod_log", "n_prior",
    "model_id", "fit_through", "r_id", "base_id", "s_pmf199", "s_q19", "indep_pmf199", "recent_pmf199",
    "direct_pmf199", "recent_cal_pmf199", "src",
]  # fmt: skip
JSON_COLUMNS = {
    "s_pmf199",
    "s_q19",
    "indep_pmf199",
    "recent_pmf199",
    "direct_pmf199",
    "recent_cal_pmf199",
    "src",
}
#: content that makes two rows "the same" for write-on-change (provenance ``src`` is excluded: a production re-run with the same grids is not news)
SIG_COLUMNS = ["s_pmf199", "indep_pmf199", "recent_pmf199", "model_id", "r_id", "base_id"]


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def dumps_ints(values: object) -> str:
    """Compact JSON of an integer array (the stored form of every pmf199 / q19 column)."""
    return json.dumps([int(v) for v in values], separators=(",", ":"))  # type: ignore[attr-defined]


def _canon(col: str, v: object) -> object:
    if col in JSON_COLUMNS and isinstance(v, str):
        return json.dumps(json.loads(v), sort_keys=True)
    return v


def append_rows(
    con: duckdb.DuckDBPyConnection, rows: list[dict[str, object]], made_at: datetime
) -> tuple[int, int, int]:
    """Append rows; returns ``(written, deduped, refused)``. A row with ``made_at >= tipoff_utc`` is refused (the store's
    leakage rule). Write-on-change against the latest stored row of the same (game, player, target)."""
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
            f"""SELECT game_id, player_id, target, {cols}
                FROM (SELECT *, row_number() OVER (PARTITION BY game_id, player_id, target
                                                   ORDER BY made_at DESC, run_id DESC) rn
                      FROM forward_combo_shadow WHERE game_id IN ({marks})) WHERE rn = 1""",
            games,
        ).fetchall()
    }
    new: list[dict[str, object]] = []
    for r in ok:
        key = (str(r["game_id"]), int(r["player_id"]), str(r["target"]))  # type: ignore[call-overload]
        sig = tuple(_canon(c, r[c]) for c in SIG_COLUMNS)
        if latest.get(key) == sig:
            continue
        new.append(r)
    if new:
        marks = ",".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT INTO forward_combo_shadow ({', '.join(COLUMNS)}) VALUES ({marks})",
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
        "INSERT INTO forward_combo_heartbeat VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            "SELECT key, arg_max(value, written_at) FROM forward_combo_meta GROUP BY key"
        ).fetchall()
    }


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str, at: datetime) -> None:
    ensure_tables(con)
    con.execute("INSERT INTO forward_combo_meta VALUES (?,?,?)", [key, value, at])


def set_counting_start(con: duckdb.DuckDBPyConnection, start: str, at: datetime) -> None:
    """The steward writes the counting start ONLY after P1, P2, P3 and P4 all passed (rule, "Counting starts only after proofs P1-P3 pass")."""
    meta = get_meta(con)
    missing = [p for p in PROOFS if meta.get(f"proof_{p}") != "pass"]
    if missing:
        raise PermissionError(f"counting cannot start: proofs not passed: {missing}")
    set_meta(con, "counting_start", start, at)
