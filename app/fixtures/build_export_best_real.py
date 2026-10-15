"""Build export_best_real/: the small export written by the REAL exporter with best-model rows.

Unlike export_small (best-model columns added by add_best_model_rows.py), every byte here is
written by `python -m nba.export build` from a scratch source database that also holds
forward_avg_shadow and forward_pplay_shadow rows. It needs a checkout whose nba/ has
nba/export/best_model.py (branch agent/steward-export-best-model until it is merged):

    REPO=/path/to/that/checkout
    $REPO/.venv/bin/python app/fixtures/build_export_best_real.py --repo $REPO

(nothing is imported from nba here; the exporter runs as a subprocess). Source rows are SYNTHETIC,
dates Oct-Nov 2024, season 2024; the shadow tables carry only the columns the exporter reads.
Scenario, tonight 2024-11-12: game 0022400023 has fresh AVG rows (integer pmf and quantiles, as
the real writer stores them) except one gap; game 0022400024 home players have a stale bundle,
away players none; chance to play: R number, R out on the report, R with no row.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import build_export_fixture as bx
import duckdb

OUT = bx.HERE / "export_best_real"
FRESH, MIXED = "0022400023", "0022400024"
AVG_DDL = """
CREATE TABLE forward_avg_shadow (made_at TIMESTAMP, run_id VARCHAR, game_id VARCHAR,
  player_id INT, stat VARCHAR, tipoff_utc TIMESTAMP, model_id VARCHAR, fit_through DATE,
  month_block VARCHAR, installed_at TIMESTAMP, stale_model BOOLEAN, avg_pmf199 JSON,
  avg_q19 JSON)
"""
PPLAY_DDL = """
CREATE TABLE forward_pplay_shadow (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,
  tipoff TIMESTAMP, player_id INT, team_id INT, model_version VARCHAR, p_play_prod DOUBLE,
  p_play_r DOUBLE, r_reason VARCHAR, proj_minutes DOUBLE, train_through DATE, n_train INT)
"""


def add_shadow(path: Path) -> None:
    rng = random.Random(4242)
    con = duckdb.connect(str(path))
    con.execute(AVG_DDL)
    con.execute(PPLAY_DDL)
    rows = con.execute(
        "SELECT DISTINCT game_id, player_id, target, tipoff FROM forward_predictions "
        "WHERE model_name = 'props_context_residual' AND game_id IN (?, ?) AND player_id > 0",
        [FRESH, MIXED],
    ).fetchall()
    home = dict(con.execute("SELECT game_id, home_team FROM games").fetchall())
    teams = {}
    for gid, pid, _st, _tip in rows:
        teams.setdefault(gid, set()).add(pid)
    for gid, pid, st, tip in rows:
        is_home = pid in {p for k in range(6) for p in [bx.roster(bx.TEAMS.index(home[gid]))[k]]}
        if gid == FRESH and (pid, st) != (990006, "fg3m"):
            month, fresh = "2024-11", True
        elif gid == MIXED and is_home:
            month, fresh = "2024-10", False
        else:
            continue
        mean = bx.BASE[st] * (1.9 - 0.28 * (pid % 10 - 1)) * 1.04
        pmf = sorted(max(0, round(rng.gauss(mean, bx.SHAPE[st]))) for _ in range(199))
        q19 = [pmf[int(-(-t * 199 // 1)) - 1] for t in bx.TAUS]
        installed = datetime(2024, 11, 1, 15) if fresh else datetime(2024, 10, 1, 15)
        con.execute(
            "INSERT INTO forward_avg_shadow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tip - timedelta(minutes=29), "r_avg_" + gid, gid, pid, st, tip,
             ("a" if fresh else "c") * 64, installed.date() - timedelta(days=1), month, installed,
             False, json.dumps(pmf), json.dumps(q19)],
        )  # fmt: skip
    for gid in (FRESH, MIXED):
        for i, pid in enumerate(sorted(teams[gid])):
            tip = next(r[3] for r in rows if r[0] == gid)
            kind = i % 3
            r, why = (0.96 - 0.01 * (i % 4), None) if kind == 0 else (None, None)
            if kind == 1:
                r, why = None, "out_on_report"
            elif kind == 2:
                r, why = None, "n_played_prior<5"
            con.execute(
                "INSERT INTO forward_pplay_shadow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ["r_pp_" + gid, tip - timedelta(minutes=31), gid, tip, pid, None, "pplay_r_v1",
                 0.97 if kind != 1 else 0.4, r, why, 30.0, tip.date() - timedelta(days=3), 5000],
            )  # fmt: skip
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, required=True, help="checkout with nba.export.best_model")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    bx.REPO = args.repo
    python = args.repo / ".venv" / "bin" / "python"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "nba.duckdb"
        plan = bx.build_source(src)
        add_shadow(src)
        odds = Path(tmp) / "market_asof.duckdb"
        bx.build_odds(odds, plan)
        pdir = Path(tmp) / "parlay"
        pdir.mkdir()
        (pdir / f"evaluate_{bx.DAY.isoformat()}.json").write_text("[]")
        args.out.mkdir(exist_ok=True)
        for f in args.out.glob("*"):
            f.unlink()
        cmd = [
            str(python), "-m", "nba.export", "build", "--date", bx.DAY.isoformat(), "--out",
            str(args.out), "--db", str(src), "--season", str(bx.SEASON), "--parlay-dir",
            str(pdir), "--odds-db", str(odds),
        ]  # fmt: skip
        subprocess.run(cmd, cwd=args.repo, check=True)  # noqa: S603
    return 0


if __name__ == "__main__":
    sys.exit(main())
