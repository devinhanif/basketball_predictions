# ruff: noqa: E501
# mypy: ignore-errors
"""INJURY_TIMING step 1: descriptive measurement (no rule, no fit).

Reads MAIN's data by absolute path, read-only. Seasons <= 2024 only (asserted in SQL). Season label = start year.
Run:  PYTHONPATH=<worktree> <main>/.venv/bin/python research/scratch/injury_timing_descriptive.py
Output: reports/injury_timing/descriptive.json
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import rotation_flagged_by_team

MAIN = Path("/Users/devin/Downloads/nba-prediction")
OUT = Path(__file__).resolve().parents[2] / "reports/injury_timing/descriptive.json"
B = 2000
SEED = 2026


def prior_state(con) -> pl.DataFrame:
    """prior_minutes_state with `season <= 2024` in SQL (the library helper reads every season)."""
    rows = con.execute(
        "SELECT s.player_id, s.team_id, g.game_date, s.minutes FROM player_game_stats s "
        "JOIN games g ON g.game_id = s.game_id WHERE COALESCE(s.minutes, 0) > 0 AND g.season <= 2024"
    ).fetchall()
    df = pl.DataFrame(
        rows,
        schema={
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "game_date": pl.Date,
            "minutes": pl.Float64,
        },
        orient="row",
    ).sort(["player_id", "game_date"])
    df = df.with_columns(
        (
            pl.col("minutes").cum_sum().over("player_id")
            / pl.col("minutes").cum_count().over("player_id")
        ).alias("avg_min_after"),
        pl.col("minutes").cum_count().over("player_id").alias("n_after"),
    )
    return df.select(["player_id", "team_id", "game_date", "avg_min_after", "n_after"])


def load(season: int, hist: bool):
    db = MAIN / ("data/history/nba_history.duckdb" if hist else "nba.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    assert season <= 2024
    games = con.execute(
        "SELECT game_id, game_date, home_team, away_team FROM games WHERE season = ?", [season]
    ).fetchall()
    rows = con.execute(
        "SELECT a.game_id, a.player_id, lower(a.status) status, CAST(a.as_of AS TIMESTAMP) as_of "
        "FROM player_availability a JOIN games g USING (game_id) "
        "WHERE g.season = ? AND a.player_id IS NOT NULL AND a.source='nba_official_report'",
        [season],
    ).fetchall()
    df = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
        },
        orient="row",
    )
    tips = build_game_tipoff(MAIN / "data/schedule", max_season=2024)
    hist_tips = build_game_tipoff(MAIN / "data/history/schedule", max_season=2024)
    tips = pl.concat([tips, hist_tips]).unique("game_id")
    df = df.join(tips.select(["game_id", "tip_et"]), on="game_id", how="inner")
    state = prior_state(con) if not hist else None
    return con, games, df, tips, state


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def stamp_tables(df: pl.DataFrame, n_games: int) -> dict:
    per_game = df.group_by("game_id").agg(
        pl.col("as_of").n_unique().alias("n_all"),
        (pl.col("as_of") <= pl.col("tip_et") - pl.duration(minutes=60)).any().alias("any_usable"),
    )
    usable = df.filter(pl.col("as_of") <= pl.col("tip_et") - pl.duration(minutes=60))
    n_use = usable.group_by("game_id").agg(pl.col("as_of").n_unique().alias("n_use"))
    latest = usable.group_by("game_id").agg(
        pl.col("as_of").max().alias("latest"), pl.col("tip_et").first()
    )
    lead = ((latest["tip_et"] - latest["latest"]).dt.total_minutes()).to_numpy().astype(float)
    clock = Counter(
        s.strftime("%H:%M") for s in df.select("as_of").unique().get_column("as_of").to_list()
    )
    tips_h = latest.with_columns(pl.col("tip_et").dt.hour().alias("h"))
    byhour = {}
    for h in sorted(tips_h["h"].unique().to_list()):
        sel = tips_h.filter(pl.col("h") == h)
        ld = ((sel["tip_et"] - sel["latest"]).dt.total_minutes()).to_numpy().astype(float)
        byhour[int(h)] = {"n": int(len(ld)), "p50": pct(ld, 50)}
    return {
        "games_total": n_games,
        "games_any_report": int(per_game.height),
        "games_usable_T60": int(latest.height),
        "stamps_per_game_all_mean": float(per_game["n_all"].mean()),
        "stamps_per_game_usable_mean": float(n_use["n_use"].mean()),
        "stamp_clock_times_distinct_stamps": dict(clock.most_common(8)),
        "lead_min": {f"p{q}": pct(lead, q) for q in (10, 25, 50, 75, 90)},
        "lead_share": {
            "<=90": float((lead <= 90).mean()),
            ">=120": float((lead >= 120).mean()),
            ">=150": float((lead >= 150).mean()),
            ">=180": float((lead >= 180).mean()),
        },
        "median_lead_by_tip_hour_ET": byhour,
    }


def snap(df: pl.DataFrame, cutoff_min: int) -> pl.DataFrame:
    u = df.filter(pl.col("as_of") <= pl.col("tip_et") - pl.duration(minutes=cutoff_min))
    lat = u.group_by("game_id").agg(pl.col("as_of").max().alias("_l"))
    return u.join(lat, on="game_id").filter(pl.col("as_of") == pl.col("_l")).drop("_l")


def sn_dict(s: pl.DataFrame) -> dict[tuple[str, int], str]:
    return {(g, p): st for g, p, st in s.select(["game_id", "player_id", "status"]).iter_rows()}


def stamp_of(s: pl.DataFrame) -> dict[str, dt.datetime]:
    return {g: t for g, t in s.group_by("game_id").agg(pl.col("as_of").first()).iter_rows()}


def classify(a: str | None, b: str | None) -> list[str]:
    ev = []
    A = a or "absent"
    Bs = b or "absent"
    if A != "out" and Bs == "out":
        ev.append("out_added")
        if A in ("questionable", "doubtful"):
            ev.append("qd_to_out")
    if A == "out" and Bs != "out":
        ev.append("out_removed")
    if A in ("questionable", "doubtful") and Bs in ("available", "probable", "absent"):
        ev.append("qd_cleared")
    if A in ("absent", "available", "probable") and Bs in ("questionable", "doubtful"):
        ev.append("qd_added")
    if Bs != A:
        ev.append("any_change")
    return ev


def transitions(
    old: dict, new: dict, both_games: list[str], games_info: dict, state: pl.DataFrame | None
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], Counter]:
    """Per-game event counts (all players, rotation players) and the P(new|old) matrix counts."""
    idx = {g: i for i, g in enumerate(both_games)}
    keys_by_game: dict[str, set[int]] = {}
    for g, p in list(old) + list(new):
        if g in idx:
            keys_by_game.setdefault(g, set()).add(p)
    evs = ["out_added", "out_removed", "qd_to_out", "qd_cleared", "qd_added", "any_change"]
    allc = {e: np.zeros(len(both_games)) for e in evs}
    rotc = {e: np.zeros(len(both_games)) for e in evs}
    flagged_by_ev: dict[str, dict[str, set[int]]] = {e: {} for e in evs}
    mat: Counter = Counter()
    for g, ps in keys_by_game.items():
        for p in ps:
            a, b = old.get((g, p)), new.get((g, p))
            mat[(a or "absent", b or "absent")] += 1
            for e in classify(a, b):
                allc[e][idx[g]] += 1
                flagged_by_ev[e].setdefault(g, set()).add(p)
    if state is not None:
        for e in evs:
            rot = rotation_flagged_by_team(flagged_by_ev[e], games_info, state)
            for (g, _team), ps in rot.items():
                rotc[e][idx[g]] += len(ps)
    return allc, rotc, mat


def boot_mean(x: np.ndarray, rng: np.random.Generator) -> list[float]:
    n = len(x)
    ii = rng.integers(0, n, size=(B, n))
    m = x[ii].mean(axis=1)
    return [float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


def event_table(allc, rotc, n_games, rng):
    out = {}
    for e in allc:
        out[e] = {
            "per_game_all": boot_mean(allc[e], rng),
            "per_game_rotation": boot_mean(rotc[e], rng),
            "total_all": int(allc[e].sum()),
            "total_rotation": int(rotc[e].sum()),
        }
    out["n_games"] = n_games
    return out


def main() -> None:
    rng = np.random.default_rng(SEED)
    res: dict = {"script": "research/scratch/injury_timing_descriptive.py", "seed": SEED, "B": B}
    t0 = dt.datetime.now().isoformat(timespec="seconds")
    res["run_clock"] = t0
    for season in (2023, 2024):
        con, games, df, tips, state = load(season, hist=False)
        info = {g: (d, int(h), int(a)) for g, d, h, a in games}
        res[f"stamps_{season}"] = stamp_tables(df, len(games))
        used = snap(df, 60)
        # previous stamp: latest stamp strictly before the used one, same game, on the game day at 11:00
        u_t = stamp_of(used)
        prev_rows = df.join(
            pl.DataFrame(
                {"game_id": list(u_t), "_u": list(u_t.values())},
                schema={"game_id": pl.Utf8, "_u": pl.Datetime("us")},
            ),
            on="game_id",
        ).filter(pl.col("as_of") < pl.col("_u"))
        plat = prev_rows.group_by("game_id").agg(pl.col("as_of").max().alias("_p"))
        prev = (
            prev_rows.join(plat, on="game_id")
            .filter(pl.col("as_of") == pl.col("_p"))
            .drop("_p", "_u")
        )
        p_t = stamp_of(prev)
        same_day = [
            g
            for g in u_t
            if g in p_t
            and p_t[g].date() == u_t[g].date()
            and (u_t[g] - p_t[g]) >= dt.timedelta(hours=5)
        ]
        old = {k: v for k, v in sn_dict(prev).items() if k[0] in set(same_day)}
        new = {k: v for k, v in sn_dict(used).items() if k[0] in set(same_day)}
        allc, rotc, mat = transitions(old, new, same_day, info, state)
        res[f"change_1100_to_1700_{season}"] = event_table(allc, rotc, len(same_day), rng)
        res[f"change_1100_to_1700_{season}"]["gap_hours_median"] = float(
            np.median([(u_t[g] - p_t[g]).total_seconds() / 3600 for g in same_day])
        )
        res[f"matrix_1100_to_1700_{season}"] = {f"{a}->{b}": n for (a, b), n in sorted(mat.items())}
        con.close()

    # 2021-22: the only stored season with a full intraday sequence (hourly): exact T-180 vs T-60
    con, games, df, tips, state = load(2021, hist=True)
    info = {g: (d, int(h), int(a)) for g, d, h, a in games}
    res["stamps_2021"] = stamp_tables(df, len(games))
    # rotation as-of state from the history DB for 2021 players
    state_h = prior_state(
        duckdb.connect(str(MAIN / "data/history/nba_history.duckdb"), read_only=True)
    )
    s180, s60 = snap(df, 180), snap(df, 60)
    t180, t60 = stamp_of(s180), stamp_of(s60)
    gs = [g for g in t60 if g in t180 and t180[g] < t60[g]]
    old = {k: v for k, v in sn_dict(s180).items() if k[0] in set(gs)}
    new = {k: v for k, v in sn_dict(s60).items() if k[0] in set(gs)}
    allc, rotc, mat = transitions(old, new, gs, info, state_h)
    res["change_T180_to_T60_2021"] = event_table(allc, rotc, len(gs), rng)
    res["change_T180_to_T60_2021"]["games_with_distinct_snapshots"] = len(gs)
    res["matrix_T180_to_T60_2021"] = {f"{a}->{b}": n for (a, b), n in sorted(mat.items())}
    # hazard reference: same event in the 2023-24 11:00->17:00 pair is 6h; 2021 is exactly 2h by construction
    con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
