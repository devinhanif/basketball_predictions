"""Build the small test export in app/fixtures/export_small/ with the real `python -m nba.export build`.

Run from the repo root with the project's own interpreter (it has duckdb and the nba package):

    .venv/bin/python app/fixtures/build_export_fixture.py

What it does (nothing is read from the live database, nothing is real):
  1. creates a scratch source DB from nba/db/schema.sql (the SQL text is executed; nothing from
     nba is imported here) and fills it with SYNTHETIC rows: invented player ids (990001 and up),
     real team ids, dates in Oct-Nov 2024 (season 2024; the 2025 holdout is never touched);
  2. creates a scratch odds-capture DB (the live_book_odds table, same columns as the real
     capture store) with SYNTHETIC quotes: two sportsbooks, three exchanges, an alternate ladder
     and a planted Polymarket Under placeholder (the exporter must flag it unusable);
  3. writes a one-line parlay evaluation file (an empty list: nothing priced);
  4. runs the exporter as a subprocess, `python -m nba.export build --season 2024`, which is the
     only way the app side ever meets nba/.

The numbers are invented so the screens can be exercised; they say nothing about any real player.
Random draws use a fixed seed, so the synthetic rows are the same on every run (the manifest's
made_at, git sha and the duckdb file bytes are not).
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "export_small"
DAY = date(2024, 11, 12)  # Eastern slate date of the export
SEASON = 2024
TEAMS = [1610612738, 1610612752, 1610612755, 1610612760]  # real ids: BOS NYK PHI OKC
TAUS = [round(0.05 * i, 2) for i in range(1, 20)]
STAR, SIDEKICK = 990001, 990002  # SIDEKICK is a rotation player who is out on tonight's report
STATS = ("pts", "reb", "ast", "fg3m")
BASE = {"pts": 14.0, "reb": 5.0, "ast": 3.5, "fg3m": 1.6}
SHAPE = {"pts": 5.0, "reb": 2.2, "ast": 1.8, "fg3m": 1.1}  # spread of the invented draws


def roster(team_idx: int) -> list[int]:
    return [990001 + 10 * team_idx + k for k in range(6)]


def schedule() -> list[tuple[date, int, int]]:
    """(date, home idx, away idx): two games every other night from Oct 22 to Nov 12."""
    pairs = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
    out: list[tuple[date, int, int]] = []
    d = date(2024, 10, 22)
    i = 0
    while d <= DAY:
        for h, a in pairs[i % 3]:
            home, away = (h, a) if (i // 3) % 2 == 0 else (a, h)
            out.append((d, home, away))
        d += timedelta(days=2 if d < date(2024, 11, 11) else 1)
        i += 1
    return out


def draw(rng: random.Random, stat: str, scale: float) -> int:
    v = rng.gauss(BASE[stat] * scale, SHAPE[stat])
    return max(0, round(v))


def quantile_samples(rng: random.Random, mean: float, spread: float, n: int = 199) -> list[float]:
    s = sorted(max(0.0, rng.gauss(mean, spread)) for _ in range(n))
    return [round(v, 4) for v in s]


def prediction(rng: random.Random, stat: str, scale: float, minutes: float) -> str:
    mean = BASE[stat] * scale
    samples = quantile_samples(rng, mean, SHAPE[stat])
    grid = [round(samples[int(t * 199) - 1], 4) for t in TAUS]
    kmax = {"pts": 80, "reb": 35, "ast": 30, "fg3m": 18}[stat]
    c: list[int] = []
    for k in range(1, kmax + 1):
        ck = sum(1 for v in samples if v > k - 0.5)
        c.append(ck)
        if ck / 199 <= 1e-3:
            break
    return json.dumps(
        {
            "mean": round(sum(samples) / 199, 4),
            "std": round(SHAPE[stat], 4),
            "p_play": 0.97,
            "proj_minutes": round(minutes, 1),
            "q10": grid[1],
            "q50": grid[9],
            "q90": grid[17],
            "q_grid": grid,
            "p_ge": {
                str(t): round(sum(1 for v in samples if v > t - 0.5) / 199, 4) for t in (1, 5, 10)
            },
            "p_ge_full": {"n": 199, "c": c},
        }
    )


Plan = dict[tuple[str, int, str], tuple[float, datetime]]


def build_source(path: Path) -> Plan:
    """The scratch nba.duckdb; returns (game, player, stat) -> (forecast mean, tipoff) for quotes."""
    plan: Plan = {}
    rng = random.Random(20241112)
    con = duckdb.connect(str(path))
    con.execute((REPO / "nba" / "db" / "schema.sql").read_text())
    con.execute(
        "CREATE TABLE IF NOT EXISTS forward_model_stamps (run_id VARCHAR, model_name VARCHAR, "
        "version VARCHAR, made_at TIMESTAMP, weights_sha256 VARCHAR, weights_scope VARCHAR, "
        "data_version VARCHAR, fit_through DATE, n_train BIGINT, git_sha VARCHAR, "
        "git_dirty BOOLEAN, stamped_at TIMESTAMP)"
    )
    games = schedule()
    team0_games = 0
    sat_out = {3, 6, 9}  # the sidekick's team games he sat out (the 6th as an explicit DNP row)
    for gi, (d, home, away) in enumerate(games):
        gid = f"00224{gi + 1:05d}"
        final = d < DAY
        hp, ap = (rng.randint(98, 124), rng.randint(98, 124)) if final else (None, None)
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
            "VALUES (?,?,?,?,?,?,?)",
            [gid, d, SEASON, TEAMS[home], TEAMS[away], hp, ap],
        )
        if not final:
            continue
        if 0 in (home, away):
            team0_games += 1
        for team in (home, away):
            for k, pid in enumerate(roster(team)):
                if pid == SIDEKICK and team0_games in sat_out:
                    if team0_games == 6:  # an explicit DNP row: NULL minutes, zero stats
                        con.execute(
                            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
                            "reb, ast, fg3m, stl, blk, starter) VALUES (?,?,?,NULL,0,0,0,0,0,0,false)",
                            [gid, pid, TEAMS[team]],
                        )
                    continue
                scale = 1.9 - 0.28 * k
                minutes = round(max(8.0, rng.gauss(34 - 4.5 * k, 3)), 1)
                s = {st: draw(rng, st, scale) for st in STATS}
                con.execute(
                    "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
                    "fg3m, stl, blk, starter) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [gid, pid, TEAMS[team], minutes, s["pts"], s["reb"], s["ast"], s["fg3m"],
                     rng.randint(0, 2), rng.randint(0, 2), k < 5],
                )  # fmt: skip
    # forward predictions: every game from the lookback window, one early run and (tonight) a T-30 run
    for gi, (d, home, away) in enumerate(games):
        if d < DAY - timedelta(days=7):
            continue
        gid = f"00224{gi + 1:05d}"
        tip = datetime.combine(d, datetime.min.time()) + timedelta(hours=24)  # 00:00 UTC next day
        runs = [("r_a_" + d.isoformat(), tip - timedelta(minutes=150))]
        if d == DAY:
            runs.append(("r_b_" + d.isoformat(), tip - timedelta(minutes=30)))
        for run_id, made in runs:
            p_home = round(min(0.92, max(0.08, 0.5 + rng.gauss(0.05, 0.14))), 4)
            con.execute(
                "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
                [run_id, made, gid, tip, "rung0_mov_elo", "v1", "win_prob_home", -1,
                 json.dumps({"p_home": p_home, "home_team": TEAMS[home], "away_team": TEAMS[away],
                             "primary": True, "fallback_reason": None})],
            )  # fmt: skip
            for team in (home, away):
                for k, pid in enumerate(roster(team)):
                    if pid == SIDEKICK and d == DAY:
                        continue  # out tonight: no forecast
                    for st in STATS:
                        plan[(gid, pid, st)] = (BASE[st] * (1.9 - 0.28 * k), tip)
                        con.execute(
                            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
                            [run_id, made, gid, tip, "props_context_residual", "v1", st, pid,
                             prediction(rng, st, 1.9 - 0.28 * k, 34 - 4.5 * k)],
                        )  # fmt: skip
            con.execute(
                "INSERT INTO forward_model_stamps VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [run_id, "props_context_residual", "v1", made, "0" * 12 + run_id[-10:].replace("-", ""),
                 "synthetic fixture", "synthetic", d - timedelta(days=1), 2400, "synthetic", False, made],
            )  # fmt: skip
        if d == DAY:  # a planted row AFTER tip: the exporter must drop it
            con.execute(
                "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
                ["r_late", tip + timedelta(minutes=10), gid, tip, "props_context_residual", "v1",
                 "pts", STAR, prediction(rng, "pts", 9.0, 40)],
            )  # fmt: skip
        # the official report as of 5:00 pm ET on game day (the sidekick is out; the star is fine)
        tip_et = tip - timedelta(hours=5)  # ET naive (November, EST)
        as_of = datetime.combine(d, datetime.min.time()) + timedelta(hours=17)
        if as_of <= tip_et - timedelta(minutes=60):
            for team in (home, away):
                for pid in roster(team):
                    if pid == SIDEKICK:
                        con.execute(
                            "INSERT INTO player_availability VALUES (?,?,?,?,?,?,?)",
                            [pid, as_of, gid, "out", "Left Knee; Soreness", "nba_official", as_of],
                        )
    # settled scores for the games that are over (a few rows, as the nightly settle would write them)
    for gi, (d, _home, _away) in enumerate(games):
        if d >= DAY:
            continue
        gid = f"00224{gi + 1:05d}"
        tip = datetime.combine(d, datetime.min.time()) + timedelta(hours=24)
        con.execute(
            "INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tip + timedelta(hours=14), gid, d, SEASON, "rung0_mov_elo", "v1", "win_prob_home", -1,
             tip - timedelta(minutes=150), 1.0, 0.55, 0.598, 0.2025, None, "scored"],
        )  # fmt: skip
    con.close()
    return plan


ODDS_DDL = """
CREATE TABLE live_book_odds (
    source VARCHAR, game_id VARCHAR, slate_date DATE, snapshot_kind VARCHAR,
    requested_at TIMESTAMP, received_at TIMESTAMP, tipoff TIMESTAMP, commence_time TIMESTAMP,
    event_id VARCHAR, book VARCHAR, market VARCHAR, outcome_name VARCHAR, player_name VARCHAR,
    player_id INT, side VARCHAR, point DOUBLE, price_american INT, implied_prob_raw DOUBLE,
    implied_prob DOUBLE, last_update TIMESTAMP, raw_file VARCHAR
)
"""
MARKET = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists",
          "fg3m": "player_threes"}  # fmt: skip


def raw_prob(price: int) -> float:
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def build_odds(path: Path, plan: Plan) -> None:
    """Synthetic quotes for every planned prop, captured 200 minutes before tip (known to every
    run) and, for tonight's games, re-captured at T-60 (known to the T-30 run only).

    Main line L = floor(mean) + 0.5 at draftkings and fanduel; kalshi posts L + 1 (so the main
    line is the one most books carry); novig posts an alternate over ladder at L + 4 and L + 9;
    polymarket posts an over and a -3233 Under (a placeholder with no bid: flagged unusable).
    The fifth rostered player of each team (index 4) is priced by fanduel at L + 1 only, so the
    draftkings/fanduel disagreement is exercised too. Prices are invented."""
    rng = random.Random(777)
    con = duckdb.connect(str(path))
    con.execute(ODDS_DDL)
    rows: list[list[object]] = []

    def add(gid: str, tip: datetime, kind: str, recv: datetime, pid: int, st: str, book: str,
            market: str, side: str, point: float, price: int) -> None:  # fmt: skip
        rows.append(
            ["the_odds_api_live", gid, tip.date(), kind, recv, recv, tip, tip, "ev", book, market,
             side.title(), "Player", pid, side, point, price, raw_prob(price), None, recv, "raw"]
        )  # fmt: skip

    for (gid, pid, st), (mean, tip) in sorted(plan.items()):
        line = float(int(mean)) + 0.5
        quote_times = [("t180", tip - timedelta(minutes=200))]
        if tip.date() == DAY + timedelta(days=1):
            quote_times.append(("t60", tip - timedelta(minutes=60)))
        for kind, recv in quote_times:
            drift = 0 if kind == "t180" else 5
            over = -118 + rng.randint(-8, 8) + drift
            under = -118 + rng.randint(-8, 8) - drift
            fd_line = line + 1 if pid % 10 == 5 else line
            m = MARKET[st]
            for book, ln in (("draftkings", line), ("fanduel", fd_line), ("kalshi", line + 1)):
                add(gid, tip, kind, recv, pid, st, book, m, "over", ln, over)
                add(gid, tip, kind, recv, pid, st, book, m, "under", ln, under)
            add(gid, tip, kind, recv, pid, st, "polymarket", m, "over", line, 120)
            add(gid, tip, kind, recv, pid, st, "polymarket", m, "under", line, -3233)
            for extra in (4.0, 9.0):
                add(gid, tip, kind, recv, pid, st, "novig", m + "_alternate", "over", line + extra,
                    int(110 * (1 + extra)))  # fmt: skip
    con.executemany("INSERT INTO live_book_odds VALUES (" + ",".join("?" * 21) + ")", rows)
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--python", default=sys.executable, help="interpreter that has the nba package")
    ap.add_argument(
        "--out", type=Path, default=OUT, help="output directory (default: export_small)"
    )
    args = ap.parse_args()
    out = args.out
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "nba.duckdb"
        plan = build_source(src)
        odds = Path(tmp) / "market_asof.duckdb"
        build_odds(odds, plan)
        pdir = Path(tmp) / "parlay"
        pdir.mkdir()
        (pdir / f"evaluate_{DAY.isoformat()}.json").write_text("[]")
        if out.is_dir():
            for f in out.glob("*"):
                f.unlink()
        cmd = [
            args.python, "-m", "nba.export", "build", "--date", DAY.isoformat(), "--out", str(out),
            "--db", str(src), "--season", str(SEASON), "--parlay-dir", str(pdir),
            "--odds-db", str(odds),
        ]  # fmt: skip
        subprocess.run(cmd, cwd=REPO, check=True)  # noqa: S603
    return 0


if __name__ == "__main__":
    sys.exit(main())
