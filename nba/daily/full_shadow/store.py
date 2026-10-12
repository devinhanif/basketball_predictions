# ruff: noqa: E501
"""Shadow tables for the RECENT_ROLE_FULL model: their own tables, never ``forward_predictions``.

``forward_full_shadow`` (append-only, one row per run x game x player x stat, write-on-change) and
``forward_full_scores`` (settled rows). Timestamps are naive UTC. A row whose game has tipped is refused.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS forward_full_shadow (
    run_id VARCHAR,
    made_at TIMESTAMP,          -- known-at: every input was knowable at this instant
    game_id VARCHAR,
    tipoff TIMESTAMP,
    player_id INT,
    team_id INT,
    stat VARCHAR,               -- pts | reb | ast | fg3m
    model_version VARCHAR,      -- full_v1
    q_grid JSON,                -- FULL conditional-on-playing integer-support quantiles, 19 taus 0.05..0.95
    mean_full DOUBLE,           -- FULL conditional mean
    pi_full DOUBLE,             -- FULL P(short minutes | plays), clipped (the classifier with the 5 columns)
    q_grid_prod JSON,           -- production's continuous conditional grid for the same player-stat (same run)
    mean_prod DOUBLE,
    p_play_prod DOUBLE,         -- production hurdle P(play) of the same wide frame
    in_prod_log BOOLEAN,
    train_through DATE,         -- last game date in the training rows
    n_train INT,
    features_through DATE,
    feature_set_sha VARCHAR,    -- recipe fingerprint: ordered head + pi feature names, learner params, lightgbm
    features_sha VARCHAR        -- this row's head + pi feature values
);
CREATE TABLE IF NOT EXISTS forward_full_scores (
    scored_at TIMESTAMP,
    game_id VARCHAR,
    game_date DATE,
    season INT,
    player_id INT,
    stat VARCHAR,
    run_id VARCHAR,
    made_at TIMESTAMP,
    tipoff TIMESTAMP,
    played BOOLEAN,             -- minutes > 0 in the box
    y DOUBLE,                   -- the stat if played
    crps_prod DOUBLE,           -- conditional integer CRPS of production's grid (played rows only)
    crps_full DOUBLE,           -- conditional integer CRPS of the FULL grid (played rows only)
    p_play_prod DOUBLE,
    p_play_r DOUBLE,            -- from forward_pplay_shadow (latest row eligible at T-60); NULL when R has none
    y_uncond DOUBLE,            -- stat if played else 0 (listed DNP)
    crps_joint_prod DOUBLE,     -- production p_play x production grid, mixture CRPS vs y_uncond
    crps_joint_full DOUBLE,     -- p_play_r x FULL grid (what production would serve with both); NULL without R
    crps_joint_rprod DOUBLE,    -- p_play_r x production grid (isolates R from FULL); NULL without R
    in_prod_log BOOLEAN,
    status VARCHAR              -- scored | not_in_box
);
"""

COLUMNS = [
    "run_id", "made_at", "game_id", "tipoff", "player_id", "team_id", "stat", "model_version",
    "q_grid", "mean_full", "pi_full", "q_grid_prod", "mean_prod", "p_play_prod", "in_prod_log",
    "train_through", "n_train", "features_through", "feature_set_sha", "features_sha",
]  # fmt: skip


SIG_COLUMNS = ["q_grid", "mean_full", "pi_full", "q_grid_prod", "p_play_prod", "in_prod_log",
               "feature_set_sha", "features_sha"]  # fmt: skip


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def append_rows(
    con: duckdb.DuckDBPyConnection, rows: list[dict[str, object]], made_at: datetime
) -> tuple[int, int, int]:
    """Append rows; returns ``(written, deduped, refused)``. A row whose game has tipped
    (``made_at >= tipoff``) is refused, never written. Write-on-change: a row equal in content to the
    latest stored row of its (game, player, stat) is skipped."""
    ensure_tables(con)
    ok = [r for r in rows if made_at < r["tipoff"]]  # type: ignore[operator]
    refused = len(rows) - len(ok)
    if not ok:
        return 0, 0, refused
    games = sorted({str(r["game_id"]) for r in ok})
    marks = ",".join("?" for _ in games)
    latest = {
        (str(r[0]), int(r[1]), str(r[2])): tuple(r[3:])
        for r in con.execute(
            f"""SELECT game_id, player_id, stat, CAST(q_grid AS VARCHAR), mean_full, pi_full,
                       CAST(q_grid_prod AS VARCHAR), p_play_prod, in_prod_log, feature_set_sha, features_sha
                FROM (SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat
                                                   ORDER BY made_at DESC, run_id DESC) rn
                      FROM forward_full_shadow WHERE game_id IN ({marks})) WHERE rn = 1""",
            games,
        ).fetchall()
    }

    def js(v: object) -> object:
        return json.dumps(json.loads(v), sort_keys=True) if isinstance(v, str) else v

    def sig(t: tuple[object, ...]) -> tuple[object, ...]:
        # (q_grid, mean_full, pi_full, q_grid_prod, p_play_prod, in_prod_log, feature_set_sha, features_sha)
        return (js(t[0]), t[1], t[2], js(t[3]), *t[4:])

    new: list[dict[str, object]] = []
    for r in ok:
        prev = latest.get((str(r["game_id"]), int(r["player_id"]), str(r["stat"])))  # type: ignore[call-overload]
        cur = tuple(r[c] for c in SIG_COLUMNS)
        if prev is not None and sig(prev) == sig(cur):
            continue
        new.append(r)
    if new:
        marks = ",".join("?" for _ in COLUMNS)
        con.executemany(
            f"INSERT INTO forward_full_shadow ({', '.join(COLUMNS)}) VALUES ({marks})",
            [tuple(r[c] for c in COLUMNS) for r in new],
        )
    return len(new), len(ok) - len(new), refused
