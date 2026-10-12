# ruff: noqa: E501
"""Shadow tables for the recent-role P(play) model: its own tables, never ``forward_predictions``.

``forward_pplay_shadow`` (append-only, one row per run x game x player) and ``forward_pplay_scores``
(settled rows). The primary tables, the props outputs and ``nba.daily.canonical_dump`` are untouched.
Timestamps are naive UTC.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS forward_pplay_shadow (
    run_id VARCHAR,
    made_at TIMESTAMP,          -- known-at: every input was knowable at this instant
    game_id VARCHAR,
    tipoff TIMESTAMP,
    player_id INT,
    team_id INT,
    model_version VARCHAR,      -- pplay_r_v1
    p_play_prod DOUBLE,         -- production hurdle P(play) (the 'p_play' of forward_predictions rows)
    p_play_r DOUBLE,            -- recent-role model R; NULL when R has no row (see r_reason)
    r_reason VARCHAR,           -- NULL | out_on_report | n_played_prior<5 | no_model | ...
    in_prod_log BOOLEAN,        -- production wrote primary prop rows for this player-game
    prod_drop_reason VARCHAR,   -- NULL when logged; else p_play_raw<min_p_play | proj_minutes<min | roster_cap
    proj_minutes DOUBLE,        -- production conditional minutes projection
    props_cond JSON,            -- {stat: {model, mean, q_grid[19]}} conditional on playing, taus 0.05..0.95
    train_through DATE,         -- last game date in R's training rows
    n_train INT,
    features_through DATE,      -- as-of date of every feature (games dated before this)
    feature_set_sha VARCHAR,    -- recipe fingerprint (ordered feature names, learner params, lightgbm)
    features_sha VARCHAR,       -- this row's feature values (NULL when R has no row)
    t60_report_as_of VARCHAR    -- latest usable official report slot for the game, or NULL
);
CREATE TABLE IF NOT EXISTS forward_pplay_scores (
    scored_at TIMESTAMP,
    game_id VARCHAR,
    game_date DATE,
    season INT,
    player_id INT,
    run_id VARCHAR,
    made_at TIMESTAMP,
    tipoff TIMESTAMP,
    p_play_prod DOUBLE,
    p_play_r DOUBLE,
    y DOUBLE,                   -- 1 played (minutes > 0), 0 listed DNP; NULL when not in the box
    ll_prod DOUBLE,
    ll_r DOUBLE,
    in_prod_log BOOLEAN,
    status VARCHAR              -- scored | not_in_box
);
"""

COLUMNS = [
    "run_id",
    "made_at",
    "game_id",
    "tipoff",
    "player_id",
    "team_id",
    "model_version",
    "p_play_prod",
    "p_play_r",
    "r_reason",
    "in_prod_log",
    "prod_drop_reason",
    "proj_minutes",
    "props_cond",
    "train_through",
    "n_train",
    "features_through",
    "feature_set_sha",
    "features_sha",
    "t60_report_as_of",
]


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def _same(a: tuple[object, ...], b: tuple[object, ...]) -> bool:
    return a == b


def append_rows(
    con: duckdb.DuckDBPyConnection, rows: list[dict[str, object]], made_at: datetime
) -> tuple[int, int, int]:
    """Append rows; returns ``(written, deduped, refused)``. A row whose game has tipped
    (``made_at >= tipoff``) is refused, never written. Write-on-change: a row equal in content to the
    latest stored row of its (game, player) is skipped."""
    ensure_tables(con)
    ok = [r for r in rows if made_at < r["tipoff"]]  # type: ignore[operator]
    refused = len(rows) - len(ok)
    if not ok:
        return 0, 0, refused
    games = sorted({str(r["game_id"]) for r in ok})
    marks = ",".join("?" for _ in games)
    latest = {
        (str(r[0]), int(r[1])): r[2:]
        for r in con.execute(
            f"""SELECT game_id, player_id, p_play_prod, p_play_r, r_reason, features_sha, props_cond,
                       feature_set_sha, in_prod_log
                FROM (SELECT *, row_number() OVER (PARTITION BY game_id, player_id
                                                   ORDER BY made_at DESC, run_id DESC) rn
                      FROM forward_pplay_shadow WHERE game_id IN ({marks})) WHERE rn = 1""",
            games,
        ).fetchall()
    }

    def sig(r: dict[str, object]) -> tuple[object, ...]:
        pc = r["props_cond"]
        pcj = json.dumps(json.loads(pc), sort_keys=True) if isinstance(pc, str) else pc
        return (r["p_play_prod"], r["p_play_r"], r["r_reason"], r["features_sha"], pcj,
                r["feature_set_sha"], r["in_prod_log"])  # fmt: skip

    new: list[dict[str, object]] = []
    for r in ok:
        prev = latest.get((str(r["game_id"]), int(r["player_id"])))  # type: ignore[call-overload]
        if prev is not None:
            pj = json.dumps(json.loads(prev[4]), sort_keys=True) if prev[4] else prev[4]
            if _same(tuple(prev[:4]) + (pj,) + tuple(prev[5:]), sig(r)):
                continue
        new.append(r)
    if new:
        marks = ",".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT INTO forward_pplay_shadow ({', '.join(COLUMNS)}) VALUES ({marks})",
            [tuple(r[c] for c in COLUMNS) for r in new],
        )
    return len(new), len(ok) - len(new), refused
