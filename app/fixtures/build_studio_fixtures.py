"""Build the Analysis Studio fixtures from nba.duckdb (read-only, seasons <= 2024 only).

Run:  cd app/api && uv run --frozen python ../fixtures/build_studio_fixtures.py /path/to/nba.duckdb
Writes app/web/src/fixtures/studio_shots.json and studio_builds.json.
Season 2025 is never read: every query filters season <= 2024 and the script asserts it.
When nba.export carries these tables the fixtures are replaced by the export; the shapes are the contract
documented in docs/APP_ANALYSIS_STUDIO.md.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import duckdb

OUT = Path(__file__).resolve().parents[1] / "web" / "src" / "fixtures"
SLATE = (
    Path(__file__).resolve().parent / "slates" / "2024-11-12.json"
)  # real names and teams (historical replay)
GAME_IDS = ["0022400001", "0022400004", "0022400007", "0022400008"]
AS_OF = date(2024, 11, 12)  # the slate date: every number below uses games strictly before it
WINDOW_DAYS = 365
MIN_GAMES_LEAGUE = 20  # minimum-n gate for a player-season to enter a league distribution
ZONES = ["Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"]
ZONE_SQL = """CASE WHEN shot_zone_basic IN ('Left Corner 3','Right Corner 3') THEN 'Corner 3'
  WHEN shot_zone_basic IN ('Backcourt') THEN 'Above the Break 3' ELSE shot_zone_basic END"""


def q(c: duckdb.DuckDBPyConnection, sql: str, *args: object) -> list[tuple]:
    return c.execute(sql, list(args)).fetchall()


def pct(sorted_vals: list[float], p: float) -> float:
    i = (len(sorted_vals) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def slate_facts() -> tuple[dict[int, str], dict[int, str], list[dict]]:
    sl = json.loads(SLATE.read_text())
    games = [g for g in sl["games"] if g["game_id"] in GAME_IDS]
    names = {p["player_id"]: p["name"] for g in sl["games"] for p in g["players"]}
    abbr = {t["id"]: t["abbr"] for g in sl["games"] for t in (g["home"], g["away"])}
    return names, abbr, games


def build_shots(c: duckdb.DuckDBPyConnection) -> dict:
    names, abbr, slate_games = slate_facts()
    games = []
    ids = ",".join(f"'{g}'" for g in GAME_IDS)
    for g in slate_games:
        gd = date.fromisoformat(g["date"])
        assert gd < date(2025, 7, 1)
        games.append(
            {
                "game_id": g["game_id"],
                "game_date": g["date"],
                "home": g["home"]["abbr"],
                "away": g["away"]["abbr"],
                "home_pts": g["result"]["home_pts"],
                "away_pts": g["result"]["away_pts"],
                "home_team_id": g["home"]["id"],
                "away_team_id": g["away"]["id"],
            }
        )
    rows = q(
        c,
        f"""select game_id, action_number, period, clock_seconds, player_id, team_id, loc_x, loc_y, shot_distance,
            shot_zone_basic, action_type, shot_type, made, pbp_description
            from shot_events where season <= 2024 and game_id in ({ids})
            order by game_id, period, clock_seconds desc, action_number""",
    )
    shots = []
    for g, a, p, cl, pid, tid, x, y, d, zb, at_, st, made, desc in rows:
        shots.append(
            {
                "game_id": g,
                "action_number": a,
                "period": p,
                "clock_seconds": cl,
                "player_id": pid,
                "player": names.get(pid, desc.split(" ")[1] if desc and " " in desc else str(pid)),
                "team": abbr.get(tid, str(tid)),
                "loc_x": x,
                "loc_y": y,
                "shot_distance": d,
                "zone": "Corner 3" if zb in ("Left Corner 3", "Right Corner 3") else zb,
                "action_type": at_,
                "shot_type": st,
                "made": bool(made),
                "description": desc,
            }
        )
    return {
        "meta": {
            "table": "export.shots (proposed)",
            "source": "nba.duckdb shot_events (season <= 2024)",
            "note": "Real shots from four 2024-25 games (2024-11-12 slate). Fixture until nba.export carries the table.",
            "n_shots": len(shots),
        },
        "games": games,
        "shots": shots,
    }


def build_cards(c: duckdb.DuckDBPyConnection) -> dict:
    names, abbr, slate_games = slate_facts()
    start = date.fromordinal(AS_OF.toordinal() - WINDOW_DAYS)
    base = """select s.player_id, s.team_id, g.game_date, g.season, s.minutes, s.starter, s.pts, s.reb, s.ast, s.fg3m,
              s.stl, s.blk, s.tov from player_game_stats s join games g using (game_id)
              where g.season <= 2024 and s.minutes >= 1"""
    win = q(c, base + " and g.game_date < ? and g.game_date >= ?", AS_OF, start)
    cols = [
        "pid",
        "tid",
        "date",
        "season",
        "min",
        "starter",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "stl",
        "blk",
        "tov",
    ]
    by: dict[int, list[dict]] = {}
    for r in win:
        by.setdefault(r[0], []).append(dict(zip(cols, r, strict=True)))
    stats = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"]

    def per36(rs: list[dict], s: str) -> float:
        return 36.0 * sum(r[s] for r in rs) / sum(r["min"] for r in rs)

    league = {pid: rs for pid, rs in by.items() if len(rs) >= MIN_GAMES_LEAGUE}
    dist = {s: sorted(per36(rs, s) for rs in league.values()) for s in stats}

    # level cut points: quintiles of minutes per game / 48 over player-seasons 2023 and 2024 with >= 20 games
    ps = q(
        c,
        """select s.player_id, g.season, count(*), avg(s.minutes) from player_game_stats s join games g using (game_id)
                 where g.season in (2022, 2023, 2024) and s.minutes >= 1 group by 1, 2 having count(*) >= ?""",
        MIN_GAMES_LEAGUE,
    )
    share = sorted(m / 48.0 for _, se, _, m in ps if se in (2023, 2024))
    cuts = [round(pct(share, k / 5), 4) for k in (1, 2, 3, 4)]

    pstat = {
        r[0]: r
        for r in q(
            c,
            "select player_id, position, birth_date, draft_year, draft_pick, first_season, draft_status from players_static",
        )
    }

    def level(mshare: float) -> int:
        return 1 + sum(mshare > k for k in cuts)

    # candidates: players on the fixture teams
    team_ids = {g["home"]["id"] for g in slate_games} | {g["away"]["id"] for g in slate_games}
    cand = []
    for pid, rs in by.items():
        if pid in names and rs[-1:] and any(r["tid"] in team_ids for r in rs[-3:]):
            cand.append((sum(r["min"] for r in rs), pid))
    # rookies: first_season 2024
    rookies = [p for p in names if pstat.get(p, (0, 0, 0, 0, 0, 0))[5] == 2024]
    ranked = [p for _, p in sorted(cand, reverse=True)]
    chosen: list[int] = ranked[:2] + [ranked[len(ranked) // 4], ranked[len(ranked) // 2]]
    for p in rookies:
        if p in by and any(r["tid"] in team_ids for r in by[p][-1:]) and p not in chosen:
            chosen.append(p)
            break

    # next-season minutes analogs: age at season start +-1 and same level, seasons 2022 -> 2023 and 2023 -> 2024
    pmin = {(pid, se): m for pid, se, _, m in ps}
    analog_pool = []
    for (pid, se), m in pmin.items():
        if se in (2022, 2023) and (pid, se + 1) in pmin and pid in pstat and pstat[pid][2]:
            age = (date(se, 10, 1) - pstat[pid][2]).days / 365.25
            analog_pool.append((age, level(m / 48.0), pmin[(pid, se + 1)]))

    zone_rows = q(
        c,
        f"""select player_id, {ZONE_SQL} z, count(*), sum(made::int) from shot_events
                         where season <= 2024 and game_date < ? and game_date >= ? group by 1, 2""",
        AS_OF,
        start,
    )
    zl: dict[str, list[int]] = {z: [0, 0] for z in ZONES}
    zp: dict[int, dict[str, list[int]]] = {}
    for pid, z, n, mk in zone_rows:
        if z not in zl:
            continue
        zl[z][0] += n
        zl[z][1] += mk
        zp.setdefault(pid, {})[z] = [n, mk]

    # peers for thin samples: same listed position, drafted within 5 picks, their first 20 games in our data
    def peers(pid: int) -> dict | None:
        pos, dp = pstat[pid][1], pstat[pid][4]
        if dp is None:
            return None
        ids = [
            r[0]
            for r in q(
                c,
                "select player_id from players_static where position = ? and draft_pick between ? and ? and first_season in (2022, 2023) and player_id <> ?",
                pos,
                dp - 5,
                dp + 5,
                pid,
            )
        ]
        per: dict[str, list[float]] = {s: [] for s in stats}
        used = 0
        for p in ids:
            first20 = q(
                c,
                "select s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.stl, s.blk, s.tov from player_game_stats s join games g using (game_id) where s.player_id = ? and g.season <= 2024 and s.minutes >= 1 order by g.game_date limit 20",
                p,
            )
            if len(first20) < 10:
                continue
            used += 1
            tm = sum(r[0] for r in first20)
            for i, s in enumerate(stats):
                per[s].append(36.0 * sum(r[i + 1] for r in first20) / tm)
        if used < 3:
            return None
        return {
            "rule": "same listed position, drafted within 5 picks, first season 2022 or 2023, their first 20 games",
            "stand_in_for": "archetype neighbours (MATCHUP_AVAILABILITY clustering, not yet produced)",
            "n_peers": used,
            "per36": {s: round(sum(v) / len(v), 2) for s, v in per.items()},
        }

    cards = []
    for pid in chosen:
        rs = by[pid]
        tot_min = sum(r["min"] for r in rs)
        ms = tot_min / len(rs) / 48.0
        recent = rs[-20:]
        prior = rs[:-20]
        ps_ = pstat[pid]
        age = (AS_OF - ps_[2]).days / 365.25 if ps_[2] else None
        lv = level(ms)
        pool = sorted(
            m for a, lvl, m in analog_pool if age is not None and abs(a - age) <= 1.0 and lvl == lv
        )
        pot = None
        if len(pool) >= 30:
            pot = {
                "metric": "minutes per game next season",
                "p10": round(pct(pool, 0.1), 1),
                "p90": round(pct(pool, 0.9), 1),
                "n_analogs": len(pool),
                "rule": "player-seasons 2022 and 2023 with age within one year and the same level; their next-season minutes per game",
            }
        bars = []
        for s in stats:
            v = per36(rs, s)
            bars.append(
                {
                    "stat": s,
                    "per36": round(v, 2),
                    "percentile": round(100 * sum(x <= v for x in dist[s]) / len(dist[s]), 0),
                    "n_games": len(rs),
                    "n_minutes": round(tot_min),
                }
            )
        zones = []
        for z in ZONES:
            n, mk = zp.get(pid, {}).get(z, [0, 0])
            zones.append(
                {"zone": z, "att": n, "made": mk, "league_att": zl[z][0], "league_made": zl[z][1]}
            )
        cur = [r for r in rs if r["season"] == 2024]
        cards.append(
            {
                "player_id": pid,
                "name": names[pid],
                "team": abbr.get(rs[-1]["tid"], str(rs[-1]["tid"])),
                "position_listed": ps_[1],
                "class": None,
                "class_note": "pending: archetype clustering from MATCHUP_AVAILABILITY",
                "level": lv,
                "minutes_share": round(ms, 4),
                "starter_rate": round(sum(bool(r["starter"]) for r in rs) / len(rs), 3),
                "n_games_window": len(rs),
                "n_games_this_season": len(cur),
                "age": round(age, 1) if age else None,
                "draft_year": ps_[3],
                "draft_pick": ps_[4],
                "draft_status": ps_[6],
                "first_season": ps_[5],
                "seasons_played": 2024 - ps_[5] + 1 if ps_[5] else None,
                "minutes_trend": {
                    "recent_mpg": round(sum(r["min"] for r in recent) / len(recent), 1),
                    "recent_n": len(recent),
                    "prior_mpg": round(sum(r["min"] for r in prior) / len(prior), 1)
                    if prior
                    else None,
                    "prior_n": len(prior),
                },
                "potential": pot,
                "bars": bars,
                "zones": zones,
                "borrowed": peers(pid) if len(rs) < MIN_GAMES_LEAGUE else None,
            }
        )
    return {
        "meta": {
            "as_of": AS_OF.isoformat(),
            "window": f"games in the {WINDOW_DAYS} days before as_of",
            "sources": {
                "games/minutes/per-36": "player_game_stats",
                "zones": "shot_events",
                "draft/age/position": "players_static",
                "percentile pool": f"players with >= {MIN_GAMES_LEAGUE} window games (n = {len(league)})",
            },
            "level_cut_points_minutes_share": cuts,
            "level_rule": f"quintiles of minutes per game / 48 over player-seasons 2023 and 2024 with >= {MIN_GAMES_LEAGUE} games (n = {len(share)})",
            "note": "Real players, as-of 2024-11-12. Fixture until nba.export carries the table.",
        },
        "cards": cards,
    }


def main() -> None:
    db = sys.argv[1]
    c = duckdb.connect(db, read_only=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "studio_shots.json").write_text(json.dumps(build_shots(c), separators=(",", ":")))
    (OUT / "studio_builds.json").write_text(json.dumps(build_cards(c), indent=1))
    print("ok")


if __name__ == "__main__":
    main()
