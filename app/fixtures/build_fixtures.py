#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Build the app fixtures from stored, read-only project data (pre-2025 seasons only).

Run from the repo root (or anywhere; paths are resolved from this file):

    uv run --script app/fixtures/build_fixtures.py
    uv run --script app/fixtures/build_fixtures.py --dates 2024-11-12 2024-11-20

Reads, never writes:
  nba.duckdb                      games, player_game_stats (box-score outcomes)
  data/stack/oof.duckdb           walk-forward out-of-fold forecasts: rung0_injury_elo win
                                  probabilities and props_context_residual 19-quantile grids
  data/odds/odds_history.duckdb   the-odds-api historical DraftKings T-60 snapshots (lines, prices)
  data/schedule/raw_2024-25.parquet  scheduled tip-off (UTC), team names and tricodes
  reports/odds_history.md         published model-vs-market results (copied, not recomputed)
  reports/props_analysis/<date>.json  (git HEAD blob) the engine's committed "no positive EV" state (copied)
  docs/data_manifests/latest.json the source data_version

Wall rules (docs/APP_ARCHITECTURE.md section 2): this script lives under app/, imports nothing from
nba/, opens every database read_only=True (copying to a temp file if another job holds the lock),
and every query that touches games or forecasts carries `season <= 2024`. Season 2025 (the frozen
holdout) is never selected; `assert_pre_2025` runs on every game it loads.

Output (app/fixtures/): manifest.json, slates/<date>.json, parlay_of_day.json, evidence.json.
The queries are listed verbatim in manifest.json so the build is auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # repo root

MAX_SEASON = 2024  # 2024 = the 2024-25 season. 2025 is the frozen holdout: never read.
DEFAULT_DATES = ("2024-11-12", "2024-11-20")
STAT_MARKETS = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
TAUS = [round(0.05 * i, 2) for i in range(1, 20)]
DISCLAIMER_REPORT_DATE = "2026-10-11"

SQL_GAMES = """
SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts
FROM games
WHERE season <= ? AND game_date = ?
ORDER BY game_id
"""
SQL_BOX = """
SELECT game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, starter
FROM player_game_stats
WHERE game_id IN (SELECT game_id FROM games WHERE season <= ? AND game_date = ?)
"""
SQL_WIN = """
SELECT game_id, p, made_with_data_through, meta
FROM oof_predictions
WHERE model = 'rung0_injury_elo' AND target = 'win' AND season <= ? AND game_id = ANY(?)
"""
SQL_PROPS = """
SELECT game_id, player_id, target AS stat, q_grid, mean, made_with_data_through
FROM oof_predictions
WHERE model = 'props_context_residual' AND model_version = 'v1'
  AND target IN ('pts','reb','ast','fg3m') AND season <= ? AND game_id = ANY(?)
"""
SQL_LINES = """
SELECT game_id, player_id, any_value(player_name) AS player_name, market, point,
       max(CASE WHEN side = 'over'  THEN price_american END) AS over_price,
       max(CASE WHEN side = 'under' THEN price_american END) AS under_price,
       max(snapshot_ts) AS snapshot_ts
FROM odds_history
WHERE season <= ? AND snapshot_kind = 't60' AND book = 'draftkings'
  AND market IN ('player_points','player_rebounds','player_assists','player_threes')
  AND player_id IS NOT NULL AND game_id = ANY(?)
GROUP BY game_id, player_id, market, point
"""
SQL_MARKET_H2H = """
SELECT game_id, implied_prob, snapshot_ts
FROM odds_history
WHERE season <= ? AND snapshot_kind = 't60' AND book = 'draftkings' AND market = 'h2h'
  AND side = 'home' AND game_id = ANY(?)
"""
SQL_SCHEDULE = """
SELECT gameId AS game_id, gameDateTimeUTC AS tip_utc, arenaName AS arena,
       homeTeam_teamId AS home_id, homeTeam_teamTricode AS home_abbr,
       homeTeam_teamCity AS home_city, homeTeam_teamName AS home_name,
       awayTeam_teamId AS away_id, awayTeam_teamTricode AS away_abbr,
       awayTeam_teamCity AS away_city, awayTeam_teamName AS away_name
FROM read_parquet(?)
WHERE gameId = ANY(?)
"""
SQL_SANITY_WIN = """
SELECT avg(pow(w.p - CASE WHEN g.home_pts > g.away_pts THEN 1.0 ELSE 0.0 END, 2)) AS brier, count(*) AS n
FROM oof_predictions w JOIN games_ref g USING (game_id)
WHERE w.model = 'rung0_injury_elo' AND w.target = 'win' AND w.season <= ?
"""


def assert_pre_2025(season: int) -> None:
    if season > MAX_SEASON:
        raise SystemExit(f"refusing to load season {season}: fixtures are season <= {MAX_SEASON}")


def open_ro(path: Path, tmp: Path) -> duckdb.DuckDBPyConnection:
    """Open read_only; on a lock conflict retry, then fall back to a copy."""
    last: Exception | None = None
    for attempt in range(4):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:  # another process holds a write lock
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print(f"lock on {path.name} ({last}); copying to a temp file", file=sys.stderr)
    dst = tmp / path.name
    shutil.copy2(path, dst)
    return duckdb.connect(str(dst), read_only=True)


def r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def prop_id(game_id: str, player_id: int, stat: str) -> str:
    return f"{game_id}:{player_id}:{stat}"


def main_line(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The book's main line: two-sided, most balanced prices (ties: lower line)."""

    def raw_prob(american: int) -> float:
        return 100 / (american + 100) if american > 0 else -american / (-american + 100)

    two = [x for x in rows if x["over_price"] is not None and x["under_price"] is not None]
    if not two:
        return None
    two.sort(
        key=lambda x: (abs(raw_prob(x["over_price"]) - raw_prob(x["under_price"])), x["point"])
    )
    return two[0]


def build_slate(
    date: str,
    nba: duckdb.DuckDBPyConnection,
    oof: duckdb.DuckDBPyConnection,
    odds: duckdb.DuckDBPyConnection,
    sched_path: Path,
) -> dict[str, Any]:
    games = nba.execute(SQL_GAMES, [MAX_SEASON, date]).fetchall()
    if not games:
        raise SystemExit(f"no games on {date} with season <= {MAX_SEASON}")
    for g in games:
        assert_pre_2025(g[2])
    ids = [g[0] for g in games]

    sched = {row[0]: row for row in nba.execute(SQL_SCHEDULE, [str(sched_path), ids]).fetchall()}
    box: dict[tuple[str, int], tuple[Any, ...]] = {
        (b[0], b[1]): b for b in nba.execute(SQL_BOX, [MAX_SEASON, date]).fetchall()
    }
    win = {w[0]: w for w in oof.execute(SQL_WIN, [MAX_SEASON, ids]).fetchall()}
    mkt = {m[0]: m for m in odds.execute(SQL_MARKET_H2H, [MAX_SEASON, ids]).fetchall()}
    props = oof.execute(SQL_PROPS, [MAX_SEASON, ids]).fetchall()
    lines_raw = odds.execute(SQL_LINES, [MAX_SEASON, ids]).fetchall()

    lines: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    names: dict[int, str] = {}
    for gid, pid, pname, market, point, op, up, ts in lines_raw:
        stat = STAT_MARKETS[market]
        lines.setdefault((gid, int(pid), stat), []).append(
            {"point": float(point), "over_price": op, "under_price": up, "ts": ts}
        )
        if pname:
            names[int(pid)] = pname

    players: dict[tuple[str, int], dict[str, Any]] = {}
    dropped = {"no_line": 0, "no_box_row": 0, "no_two_sided": 0}
    for gid, pid, stat, qgrid, mean, through in props:
        pid = int(pid)
        cand = lines.get((gid, pid, stat))
        if not cand:
            dropped["no_line"] += 1
            continue
        ml = main_line(cand)
        if ml is None:
            dropped["no_two_sided"] += 1
            continue
        b = box.get((gid, pid))
        if b is None:
            dropped["no_box_row"] += 1  # DNP: the book voids these; we keep neither side
            continue
        q = json.loads(qgrid) if isinstance(qgrid, str) else list(qgrid)
        assert len(q) == len(TAUS)
        pl = players.setdefault(
            (gid, pid),
            {
                "player_id": pid,
                "name": names.get(pid, f"Player {pid}"),
                "team_id": int(b[2]),
                "result": {
                    "minutes": r(b[3], 1),
                    "starter": bool(b[8]) if b[8] is not None else None,
                },
                "props": {},
            },
        )
        pl["props"][stat] = {
            "prop_id": prop_id(gid, pid, stat),
            "line": ml["point"],
            "over_price": int(ml["over_price"]),
            "under_price": int(ml["under_price"]),
            "line_snapshot_utc": ml["ts"].replace(tzinfo=UTC).isoformat() if ml["ts"] else None,
            "forecast": {
                "mean": r(mean, 2),
                "q": [r(v, 2) for v in q],
                "stamp": f"walk-forward out-of-fold; trained on data through {str(through)[:10]}",
            },
            "actual": int(b[{"pts": 4, "reb": 5, "ast": 6, "fg3m": 7}[stat]]),
        }

    out_games: list[dict[str, Any]] = []
    for gid, gdate, season, home_id, away_id, hpts, apts in games:
        s = sched[gid]
        w = win.get(gid)
        m = mkt.get(gid)
        gplayers = [p for (g2, _), p in sorted(players.items(), key=lambda kv: kv[0]) if g2 == gid]
        for p in gplayers:
            p["side"] = "home" if p["team_id"] == home_id else "away"
            del p["team_id"]
        gplayers.sort(
            key=lambda p: (
                0 if p["side"] == "home" else 1,
                -(p["props"].get("pts", {}).get("forecast", {}).get("mean") or 0),
            )
        )
        out_games.append(
            {
                "game_id": gid,
                "date": str(gdate)[:10],
                "season": int(season),
                "tip_utc": s[1],
                "arena": s[2],
                "home": {"id": int(home_id), "abbr": s[4], "city": s[5], "name": s[6]},
                "away": {"id": int(away_id), "abbr": s[8], "city": s[9], "name": s[10]},
                "win_probability": {
                    "model_home": r(w[1], 4) if w else None,
                    "model_name": "rung0_injury_elo",
                    "model_stamp": (
                        f"walk-forward out-of-fold; data through {str(w[2])[:10]}" if w else None
                    ),
                    "market_home": r(m[1], 4) if m else None,
                    "market_name": "DraftKings moneyline, de-vigged, T-60 snapshot",
                    "market_snapshot_utc": (
                        m[2].replace(tzinfo=UTC).isoformat() if m and m[2] else None
                    ),
                },
                "result": {
                    "home_pts": int(hpts),
                    "away_pts": int(apts),
                    "home_won": bool(hpts > apts),
                },
                "players": gplayers,
            }
        )
    out_games.sort(key=lambda g: g["tip_utc"])
    n_props = sum(len(p["props"]) for g in out_games for p in g["players"])
    return {
        "date": date,
        "season": int(games[0][2]),
        "label": "historical replay (not live)",
        "demo_clock_utc": _demo_clock(out_games),
        "games": out_games,
        "counts": {
            "games": len(out_games),
            "players": sum(len(g["players"]) for g in out_games),
            "props": n_props,
            "dropped_forecast_rows": dropped,
        },
    }


def _demo_clock(games: list[dict[str, Any]]) -> str:
    """Frozen replay clock: 30 minutes before the first scheduled tip of the slate."""
    first = min(datetime.fromisoformat(g["tip_utc"].replace("Z", "+00:00")) for g in games)
    return (first.replace(microsecond=0) - timedelta(minutes=30)).isoformat()


def parse_evidence(report: Path) -> dict[str, Any]:
    """Copy (not recompute) the season-2024 T-60 consensus rows and the games line."""
    text = report.read_text()
    start = text.index("## Season 2024 - report season")
    sect = text[start:]
    h1 = sect[sect.index("### H1 props skill, t60") :]
    h1 = h1[: h1.index("### H2")]
    rows = []
    for ln in h1.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 8 and cells[1] == "consensus":
            m = re.match(r"([+-][\d.]+) \[([+-][\d.]+), ([+-][\d.]+)\]", cells[6])
            assert m, cells
            rows.append(
                {
                    "stat": cells[0],
                    "n": int(cells[2]),
                    "games": int(cells[3]),
                    "log_loss_model": float(cells[4]),
                    "log_loss_market": float(cells[5]),
                    "delta": float(m.group(1)),
                    "ci_lo": float(m.group(2)),
                    "ci_hi": float(m.group(3)),
                    "verdict": cells[9].strip(),
                }
            )
    h3 = sect[sect.index("### H3 games") :]
    line = [x for x in h3.splitlines() if x.startswith("n=")][0]
    m3 = re.search(
        r"n=(\d+).*LL model ([\d.]+), LL market ([\d.]+); delta ([+-][\d.]+) "
        r"\[([+-][\d.]+), ([+-][\d.]+)\]; verdict (\w+)",
        line,
    )
    assert m3, line
    assert len(rows) == 4, rows
    return {
        "source": "reports/odds_history.md, section 'Season 2024 - report season (formal tests)'",
        "rule": "docs/prereg/ODDS_HISTORY.md (frozen)",
        "seasonLabel": "2024-25",
        "snapshot": "T-60 (about one hour before tip)",
        "unit": "paired log-loss difference, model minus market; positive means the market is better",
        "ci_note": "95% percentile bootstrap clustered by game_id",
        "props_consensus_t60": rows,
        "games_t60": {
            "n": int(m3.group(1)),
            "log_loss_model": float(m3.group(2)),
            "log_loss_market": float(m3.group(3)),
            "delta": float(m3.group(4)),
            "ci_lo": float(m3.group(5)),
            "ci_hi": float(m3.group(6)),
            "verdict": m3.group(7),
            "market": "Pinnacle moneyline (consensus fallback), de-vigged",
        },
    }


def committed_report() -> tuple[dict[str, Any], str]:
    """The report as committed at HEAD (the working-tree copy is rewritten by other jobs)."""
    rel = f"reports/props_analysis/{DISCLAIMER_REPORT_DATE}.json"
    blob = subprocess.run(  # noqa: S603
        ["git", "show", f"HEAD:{rel}"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return json.loads(blob), hashlib.sha256(blob).hexdigest()


def build_parlay_state(d: dict[str, Any]) -> dict[str, Any]:
    """The engine's committed state, copied verbatim. State C: no parlay today."""
    return {
        "state": "C",
        "title": "No parlay today",
        "headline_verdict": d["headline_verdict"],
        "verdict_line": "Keep your money.",
        "as_of": d["now"],
        "report_date": d["date"],
        "source": f"reports/props_analysis/{d['date']}.json (copied verbatim, not recomputed)",
        "engine": d["engine"],
        "reason": (
            f"No win-probability forecasts stored for this date ({d['n_games']} games with forward "
            f"predictions); {d['n_markets_read']} open Kalshi markets read, {d['n_mapped']} mapped to "
            f"a game on the slate, {d['n_contracts']} contracts priced on both sides."
        ),
        "counts": {
            "n_games": d["n_games"],
            "n_markets_read": d["n_markets_read"],
            "n_mapped": d["n_mapped"],
            "n_contracts": d["n_contracts"],
            "n_flagged_positive": d["n_flagged_positive"],
        },
        "gate": d["gate"],
        "model_weight": 0.0,
        "model_weight_note": "The engine defers to the market until the gate opens.",
        "fee_model": {
            k: d["fee_model"][k] for k in ("name", "formula", "coefficient", "verified", "source")
        },
        "price_basis": d["parlay_price_basis"],
        "historical_note": (
            "The fixture slates are 2024-25 replays; the parlay engine did not exist then, so "
            "no parlay is priced for them. This card is the engine's real current state."
        ),
        "disclaimer": d["disclaimer"],
    }


def git_info() -> dict[str, str]:
    def run(*a: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *a],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,  # noqa: S607
        ).stdout.strip()

    return {"sha": run("rev-parse", "HEAD"), "commit_time": run("log", "-1", "--format=%cI")}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()

    manifest_src = json.loads((ROOT / "docs/data_manifests/latest.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="app_fixture_"))
    try:
        nba = open_ro(ROOT / "nba.duckdb", tmp)
        oof = open_ro(ROOT / "data/stack/oof.duckdb", tmp)
        odds = open_ro(ROOT / "data/odds/odds_history.duckdb", tmp)
        sched_path = ROOT / "data/schedule/raw_2024-25.parquet"

        # Sanity: win probabilities are P(home wins). Brier must beat the 0.25 coin flip.
        oof.execute("CREATE TEMP TABLE games_ref(game_id VARCHAR, home_pts INT, away_pts INT)")
        ref = nba.execute(
            "SELECT game_id, home_pts, away_pts FROM games WHERE season <= ?", [MAX_SEASON]
        ).fetchall()
        oof.executemany("INSERT INTO games_ref VALUES (?,?,?)", ref)
        brier, n_sanity = oof.execute(SQL_SANITY_WIN, [MAX_SEASON]).fetchone()  # type: ignore[misc]
        assert brier < 0.23, f"win-probability orientation check failed: Brier {brier:.4f}"

        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "slates").mkdir(exist_ok=True)
        slate_meta = []
        for d in args.dates:
            slate = build_slate(d, nba, oof, odds, sched_path)
            path = args.out / "slates" / f"{d}.json"
            path.write_text(json.dumps(slate, separators=(",", ":"), ensure_ascii=False) + "\n")
            slate_meta.append({"date": d, **slate["counts"], "file": f"slates/{d}.json"})
            print(f"{d}: {slate['counts']}")

        parlay_report, parlay_sha = committed_report()
        (args.out / "parlay_of_day.json").write_text(
            json.dumps(build_parlay_state(parlay_report), indent=1, ensure_ascii=False) + "\n"
        )
        (args.out / "evidence.json").write_text(
            json.dumps(parse_evidence(ROOT / "reports/odds_history.md"), indent=1) + "\n"
        )
        manifest = {
            "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "builder": "app/fixtures/build_fixtures.py",
            "repo": git_info(),
            "source_data_version": manifest_src["data_version"],
            "source_data_version_from": "docs/data_manifests/latest.json "
            f"(created_at {manifest_src['created_at']})",
            "max_season": MAX_SEASON,
            "season_note": "season 2024 = the 2024-25 NBA season; season 2025 is never read",
            "quantile_taus": TAUS,
            "slates": slate_meta,
            "sources": {
                "nba.duckdb": "read_only; games, player_game_stats (box-score outcomes)",
                "data/stack/oof.duckdb": (
                    "read_only; oof_predictions: rung0_injury_elo win (P home wins), "
                    "props_context_residual v1 q_grid at taus 0.05..0.95. Out-of-fold rows exist "
                    f"only for players with a box-score row, so forecasts are conditional on "
                    f"playing. Win-probability orientation check: Brier {brier:.4f} over "
                    f"{n_sanity} games, season <= {MAX_SEASON}."
                ),
                "data/odds/odds_history.duckdb": (
                    "read_only; the-odds-api DraftKings T-60 snapshots (props main line = most "
                    "balanced two-sided prices, ties to the lower line; h2h implied_prob is the "
                    "de-vigged probability)"
                ),
                "data/schedule/raw_2024-25.parquet": "scheduled tip (UTC), names, tricodes",
                "reports/odds_history.md": f"sha256 {sha256(ROOT / 'reports/odds_history.md')}",
                f"reports/props_analysis/{DISCLAIMER_REPORT_DATE}.json": (
                    f"git HEAD blob sha256 {parlay_sha} (parlay_of_day.json; the one live-season "
                    "file, a committed no-games report with no player or game data)"
                ),
            },
            "queries": {
                "games": " ".join(SQL_GAMES.split()),
                "box_scores": " ".join(SQL_BOX.split()),
                "win_probability": " ".join(SQL_WIN.split()),
                "prop_forecasts": " ".join(SQL_PROPS.split()),
                "prop_lines": " ".join(SQL_LINES.split()),
                "market_moneyline": " ".join(SQL_MARKET_H2H.split()),
                "schedule": " ".join(SQL_SCHEDULE.split()),
                "orientation_check": " ".join(SQL_SANITY_WIN.split()),
            },
            "stubs": [
                "We do not forecast minutes yet. How long a player really played shows up only as a result.",
                "Prices are DraftKings odds from about an hour before tip, not Kalshi prices.",
                "The parlay-of-the-day card is the engine's real no-parlay state, copied as stored, not recalculated.",
            ],
        }
        (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
