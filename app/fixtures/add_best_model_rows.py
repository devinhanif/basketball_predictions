"""Add best-model rows to the small test export (best_model_schema 1), in place.

The committed export_small was built by the real exporter from a synthetic source database
(build_export_fixture.py). The exporter on `main` does not write the best-model columns yet (they
are on agent/steward-export-best-model), so this script adds what that exporter writes, to the
same synthetic data, so the app can be exercised end to end:

  * prop_grids gains source_table, is_primary, primary_source, primary_detail, bundle_id,
    bundle_made_at, bundle_month, freshness, freshness_reason, p_play_primary, p_play_source;
  * tonight (2024-11-12) game 0022400023 gets a FRESH AVG row (props_avg_live2 / avg-live2) for
    every prop except one planted gap (fallback_missing);
    game 0022400024: home-team players get a STALE AVG row (fallback_stale), away-team players get
    none (fallback_missing); earlier days have no AVG rows (fallback_missing);
  * a p_play table for tonight's players: arm R where it has a number, production otherwise;
  * meta best_model_schema = 1; the manifest is resealed (sha256, bytes, row counts).

Every number is synthetic (fixed seed) and says nothing about any real player. Run from the
repo root:  app/api/.venv/bin/python app/fixtures/add_best_model_rows.py   (needs duckdb).
Re-running is safe: it starts from the already-augmented file only if the columns are absent.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
OUT = HERE / "export_small"
NEW_COLS = [
    ("source_table", "VARCHAR"), ("is_primary", "BOOLEAN"), ("primary_source", "VARCHAR"),
    ("primary_detail", "VARCHAR"), ("bundle_id", "VARCHAR"), ("bundle_made_at", "TIMESTAMP"),
    ("bundle_month", "VARCHAR"), ("freshness", "VARCHAR"), ("freshness_reason", "VARCHAR"),
    ("p_play_primary", "DOUBLE"), ("p_play_source", "VARCHAR"), ("lines_run_id", "VARCHAR"),
]  # fmt: skip
P_PLAY_DDL = """
CREATE TABLE p_play (game_id VARCHAR, player_id INTEGER, team_id INTEGER, run_id VARCHAR,
  made_at TIMESTAMP, tipoff TIMESTAMP, lead_minutes DOUBLE, p_play_primary DOUBLE,
  p_play_source VARCHAR, p_play_detail VARCHAR, p_play_r DOUBLE, p_play_prod DOUBLE,
  model_version VARCHAR, proj_minutes DOUBLE, r_train_through DATE, r_n_train INTEGER)
"""
FRESH_GAME, MIXED_GAME = "0022400023", "0022400024"
GAP = (990001 + 10 * 0 + 5, "fg3m")  # one planted missing AVG row in the fresh game, if present
TAUS = [round(0.05 * i, 2) for i in range(1, 20)]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def avg_prediction(rng: random.Random, row: dict) -> tuple[dict, list[float], list[int]]:
    """A synthetic AVG forecast near the production one: 199 samples around its mean, a bit
    tighter, so the screens show a visible but small difference."""
    mean = float(row["mean"]) * 1.04
    spread = max(0.8, float(row["std"]) * 0.95)
    s = sorted(max(0.0, rng.gauss(mean, spread)) for _ in range(199))
    s = [round(v, 4) for v in s]
    grid = [s[int(t * 199) - 1] for t in TAUS]
    kmax = {"pts": 80, "reb": 35, "ast": 30, "fg3m": 18}[row["stat"]]
    counts: list[int] = []
    for k in range(1, kmax + 1):
        ck = sum(1 for v in s if v > k - 0.5)
        counts.append(ck)
        if ck / 199 <= 1e-3:
            break
    pred = {
        "mean": round(sum(s) / 199, 4), "std": round(spread, 4), "q10": grid[1], "q50": grid[9],
        "q90": grid[17], "q_grid": grid,
        "p_ge": {str(t): round(sum(1 for v in s if v > t - 0.5) / 199, 4) for t in (1, 5, 10)},
        "p_ge_full": {"n": 199, "c": counts},
    }  # fmt: skip
    return pred, grid, counts


def main() -> None:
    db = OUT / "export.duckdb"
    con = duckdb.connect(str(db))
    cols = {r[0] for r in con.execute("DESCRIBE prop_grids").fetchall()}
    if "is_primary" in cols:
        raise SystemExit("export_small already has the best-model columns; rebuild it first")
    for name, typ in NEW_COLS:
        con.execute(f"ALTER TABLE prop_grids ADD COLUMN {name} {typ}")
    con.execute(
        "UPDATE prop_grids SET source_table='forward_predictions', is_primary=false, "
        "lines_run_id=run_id, bundle_id=stamp_weights_sha256, bundle_made_at=stamp_stamped_at, freshness='n/a'"
    )
    rng = random.Random(20241112)
    cur = con.execute("SELECT * FROM prop_grids")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    games = {r[0]: r[1] for r in con.execute("SELECT game_id, home_team FROM games").fetchall()}
    latest: dict[tuple, dict] = {}
    for r in rows:
        k = (r["game_id"], r["player_id"], r["stat"])
        if k not in latest or r["made_at"] > latest[k]["made_at"]:
            latest[k] = r
    # chance to play for tonight's players: R where it has a number
    pplay: dict[tuple[str, int], dict] = {}
    for gid in (FRESH_GAME, MIXED_GAME):
        mine = sorted({k[1] for k in latest if k[0] == gid})
        for i, pid in enumerate(mine):
            r = next(v for k, v in latest.items() if k[0] == gid and k[1] == pid)
            use_r = i % 7 != 3  # every seventh player: R stored no number, production stands in
            prod, rv = float(r["p_play"]), round(0.95 - 0.01 * (i % 5), 4)
            pplay[(gid, pid)] = {
                "game_id": gid, "player_id": pid, "team_id": r["team_id"], "run_id": r["run_id"],
                "made_at": r["made_at"], "tipoff": r["tipoff"], "lead_minutes": r["lead_minutes"],
                "p_play_primary": rv if use_r else prod,
                "p_play_source": "r" if use_r else "production",
                "p_play_detail": None if use_r else "no_r_row",
                "p_play_r": rv if use_r else None, "p_play_prod": prod,
                "model_version": "pplay-fixture", "proj_minutes": r["proj_minutes"],
                "r_train_through": datetime(2024, 11, 10).date(), "r_n_train": 5200,
            }  # fmt: skip
    con.execute(P_PLAY_DDL)
    for p in pplay.values():
        con.execute("INSERT INTO p_play VALUES (" + ",".join("?" * 16) + ")", list(p.values()))
    new_rows: list[dict] = []
    for k, prod in sorted(latest.items(), key=lambda kv: kv[0]):
        gid, pid, stat = k
        pp = pplay.get((gid, pid))
        avg: dict | None = None
        fresh = True
        if gid == FRESH_GAME and (pid, stat) != GAP:
            avg = prod
        elif gid == MIXED_GAME and prod["team_id"] == games[gid]:
            avg, fresh = prod, False
        if avg is not None:
            pred, grid, counts = avg_prediction(rng, prod)
            made = prod["made_at"] - timedelta(minutes=2)
            month, installed = ("2024-11", datetime(2024, 11, 1, 9, 0)) if fresh else (
                "2024-10", datetime(2024, 10, 1, 9, 0))  # fmt: skip
            bundle = hashlib.sha256(f"avg-bundle-{month}".encode()).hexdigest()
            new_rows.append(prod | {
                "run_id": prod["run_id"], "made_at": made, "model_name": "props_avg_live2",
                "version": "avg-live2", "mean": pred["mean"], "std": pred["std"],
                "p_play": None if pp is None else pp["p_play_primary"],
                "q10": pred["q10"], "q50": pred["q50"], "q90": pred["q90"], "q_grid": grid,
                "p_ge": json.dumps(pred["p_ge"], sort_keys=True), "p_ge_full_counts": counts,
                "p_ge_full_n": 199, "is_latest": True, "comparison_only": False,
                "stamp_weights_sha256": bundle, "stamp_weights_scope": "avg weights bundle (model_id)",
                "stamp_data_version": None, "stamp_fit_through": installed.date() - timedelta(days=1),
                "stamp_n_train": None, "stamp_git_sha": None, "stamp_git_dirty": None,
                "stamp_stamped_at": installed, "prediction_json": json.dumps(pred, sort_keys=True),
                "source_table": "forward_avg_shadow", "is_primary": fresh, "bundle_id": bundle,
                "bundle_made_at": installed, "bundle_month": month,
                "freshness": "fresh" if fresh else "stale",
                "freshness_reason": None if fresh else "month_block_mismatch",
                "primary_source": "avg" if fresh else None,
                "primary_detail": None,
                "proj_minutes": prod["proj_minutes"],
                "p_play_primary": None if pp is None else pp["p_play_primary"],
                "p_play_source": None if pp is None else pp["p_play_source"],
            })  # fmt: skip
            if fresh:
                source, detail, prim = None, None, False
            else:
                source, detail, prim = "fallback_stale", "bundle stale: month_block_mismatch", True
        else:
            source, detail, prim = "fallback_missing", "no AVG row before tip", True
        # the production row stays; it is primary unless a fresh AVG row took over
        con.execute(
            "UPDATE prop_grids SET is_primary=?, primary_source=?, primary_detail=?, "
            "p_play_primary=?, p_play_source=? WHERE run_id=? AND game_id=? AND player_id=? "
            "AND stat=? AND model_name=?",
            [prim, source if prim else None, detail if prim else None,
             prod["p_play"] if pp is None else pp["p_play_primary"],
             "production" if pp is None else pp["p_play_source"],
             prod["run_id"], gid, pid, stat, prod["model_name"]],
        )  # fmt: skip
    # non-latest production runs keep p_play_primary = their stored value (production)
    con.execute(
        "UPDATE prop_grids SET p_play_primary=p_play, p_play_source='production' "
        "WHERE p_play_primary IS NULL AND p_play IS NOT NULL"
    )
    for r in new_rows:
        con.execute(
            "INSERT INTO prop_grids (" + ",".join(r) + ") VALUES (" + ",".join("?" * len(r)) + ")",
            list(r.values()),
        )
    con.execute("INSERT INTO meta VALUES ('best_model_schema', '1')")
    counts = dict(
        con.execute(
            "SELECT primary_source, count(*) FROM prop_grids WHERE is_primary GROUP BY 1"
        ).fetchall()
    )
    n_grids = con.execute("SELECT count(*) FROM prop_grids").fetchone()[0]
    con.execute("CHECKPOINT")
    con.close()
    mp = OUT / "manifest.json"
    m = json.loads(mp.read_text())
    m["row_counts"]["prop_grids"] = n_grids
    m["row_counts"]["p_play"] = len(pplay)
    m["primary_sources"] = dict(sorted(counts.items()))
    m["best_model_schema"] = 1
    m["fixture_note"] = "best-model columns and rows added by app/fixtures/add_best_model_rows.py"
    for name in ("export.duckdb", "parlay_of_day.json"):
        data = (OUT / name).read_bytes()
        m["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    mp.write_text(json.dumps(m, indent=1, sort_keys=True))
    print("primary sources:", dict(sorted(counts.items())), "prop_grids rows:", n_grids)


if __name__ == "__main__":
    main()
