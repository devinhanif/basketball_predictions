# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,unused-ignore,union-attr,index,assignment"
"""WORKLOAD minimum-data gates (docs/prereg/WORKLOAD.md; DRAFT). No arm is fitted here.

    M="-m research.eval.workload_gates"
    .venv/bin/python $M tracking   # tracking coverage per season and minutes bucket; units
    .venv/bin/python $M rest       # rest/management absences per season; how many were on the T-60 report
    .venv/bin/python $M arena      # arena coordinates, tz, real tips, travel chain, vs team_context.travel_miles
    .venv/bin/python $M a0         # A0 reproduction from stored components (no fit)
    .venv/bin/python $M missing    # NULL audit of the features on the A0 rows by realised-minutes bucket + as-of replay
    .venv/bin/python $M all

Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every row query, season 2025
never loaded. Light (SQL aggregates, polars, stored components); never takes ``data/ops/heavy.lock``.
Output ``reports/prereg_workload/gates.json`` (merged per stage).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
from nba.features.injury_report import ReportTriggerConfig, usable_report_rows
from research.features.game_location_context import load_team_markets
from research.features.injury_reason import parse_table
from research.workload import asof as W

MAX_SEASON = 2024
SEASONS = (2023, 2024)
OUT = Path("reports/prereg_workload/gates.json")
LT_DIR = Path("reports/lower_tail")
DB = "nba.duckdb"
TEAMS_YAML = "configs/team_markets.yaml"
BUCKETS = ("<5", "5-10", "10-20", ">20")
#: gate limits (frozen with the rule)
TRACK_GAME_MIN = 0.99  # share of games with >= 8 tracking rows
TRACK_PLAYED_MIN_10 = 0.95  # share of played rows with >= 10 min that have a tracking row
NULL_GAP_PP = 2.0  # max NULL-rate gap across minutes buckets, percentage points
REST_EVENTS_MIN = 150  # unannounced-at-T-60 rest/management absences per season for the P(DNP) cell


def _save(stage: str, payload: dict[str, Any]) -> None:
    cur: dict[str, Any] = json.loads(OUT.read_text()) if OUT.exists() else {}
    cur[stage] = payload
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cur, indent=2, default=float))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB, read_only=True)


def bucket_expr(col: str = "minutes") -> pl.Expr:
    m = pl.col(col)
    return (
        pl.when(m < 5)
        .then(pl.lit("<5"))
        .when(m < 10)
        .then(pl.lit("5-10"))
        .when(m < 20)
        .then(pl.lit("10-20"))
        .otherwise(pl.lit(">20"))
        .alias("bucket")
    )


def load_games(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    g = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team FROM games "
        f"WHERE season <= {MAX_SEASON} AND substr(game_id,1,3) IN ('002','004','005')"
    ).pl()
    tips = build_game_tipoff(max_season=MAX_SEASON).select(
        "game_id", pl.col("tipoff_utc").alias("tip_utc"), "tip_et"
    )
    return g.join(tips, on="game_id", how="left").with_columns(pl.col("game_date").cast(pl.Date))


def load_played(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    df = con.execute(
        "SELECT s.game_id, s.player_id, s.team_id, g.game_date, g.season, s.minutes, "
        "t.distance, t.speed FROM player_game_stats s JOIN games g USING (game_id) "
        "LEFT JOIN player_game_tracking t ON t.game_id = s.game_id AND t.player_id = s.player_id "
        f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0"
    ).pl()
    assert int(df["season"].max()) <= MAX_SEASON
    return df.with_columns(
        pl.col("game_date").cast(pl.Date),
        pl.col("player_id").cast(pl.Int64),
        pl.col("distance").cast(pl.Float64),
        pl.col("speed").cast(pl.Float64),
        pl.col("minutes").cast(pl.Float64),
    )


# --------------------------------------------------------------------------- tracking
def gate_tracking(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    games = con.execute(
        "SELECT g.season, g.game_id, count(t.player_id) AS n_trk FROM games g "
        "LEFT JOIN player_game_tracking t USING (game_id) "
        f"WHERE g.season <= {MAX_SEASON} GROUP BY 1, 2"
    ).pl()
    by_season = (
        games.group_by("season")
        .agg(
            pl.len().alias("games"),
            (pl.col("n_trk") >= 8).sum().alias("games_ge8"),
            (pl.col("n_trk") == 0).sum().alias("games_none"),
        )
        .sort("season")
        .to_dicts()
    )
    played = load_played(con)
    played = played.with_columns(bucket_expr(), pl.col("distance").is_not_null().alias("trk"))
    cover = (
        played.group_by(["season", "bucket"])
        .agg(pl.len().alias("n"), pl.col("trk").mean().alias("has_tracking"))
        .sort(["season", "bucket"])
        .to_dicts()
    )
    ge10 = played.filter(pl.col("minutes") >= 10)
    cov10 = {
        int(s): float(ge10.filter(pl.col("season") == s)["trk"].mean()) for s in (2022, 2023, 2024)
    }
    vals = played.filter(pl.col("trk"))
    # tracking minutes vs box minutes, and sanity of units
    chk = con.execute(
        "SELECT avg(abs(t.minutes - s.minutes)), avg(t.distance / nullif(t.minutes,0)), "
        "avg(t.speed), max(t.distance), max(t.speed) FROM player_game_tracking t "
        "JOIN player_game_stats s ON s.game_id=t.game_id AND s.player_id=t.player_id "
        f"JOIN games g ON g.game_id=t.game_id WHERE g.season <= {MAX_SEASON} AND s.minutes > 10"
    ).fetchone()
    zero_dist = float((vals.filter(pl.col("minutes") >= 10)["distance"] == 0).mean())
    out = {
        "games_by_season": by_season,
        "played_rows_with_tracking_by_bucket": cover,
        "has_tracking_ge10min_by_season": cov10,
        "mean_abs_gap_tracking_vs_box_minutes_ge10": float(chk[0]),
        "mean_miles_per_minute_ge10": float(chk[1]),
        "mean_speed_mph": float(chk[2]),
        "max_distance_miles": float(chk[3]),
        "max_speed_mph": float(chk[4]),
        "share_zero_distance_ge10min": zero_dist,
    }
    ok_games = all(r["games_ge8"] / r["games"] >= TRACK_GAME_MIN for r in by_season)
    ok_play = min(cov10.values()) >= TRACK_PLAYED_MIN_10
    out["pass"] = bool(ok_games and ok_play)
    return out


# --------------------------------------------------------------------------- rest absences
def gate_rest(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    games = load_games(con)
    avail = con.execute(
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, a.reason, "
        "CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date "
        "FROM player_availability a JOIN games g USING (game_id) "
        f"WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL AND g.season <= {MAX_SEASON} "
        "AND substr(a.game_id,1,3) = '002'"
    ).pl()
    avail = avail.with_columns(
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("player_id").cast(pl.Int64),
        pl.col("game_date").cast(pl.Date),
    )
    avail = attach_real_tips(avail, games.select("game_id", "tip_et"))
    pt = parse_table(avail["reason"].unique().to_list()).select("reason", "cls")
    avail = avail.join(pt, on="reason", how="left")
    cfg = ReportTriggerConfig(statuses=("out",), tip_source="real")
    usable = usable_report_rows(avail, cfg)
    rest_any = avail.filter(pl.col("cls") == "rest_or_management")
    rest_t60 = usable.filter(pl.col("cls") == "rest_or_management")
    played = (
        con.execute(
            "SELECT s.game_id, s.player_id, g.season FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0 AND substr(s.game_id,1,3) = '002'"
        )
        .pl()
        .with_columns(pl.col("player_id").cast(pl.Int64), pl.lit(True).alias("played"))
    )
    keys = ["game_id", "player_id"]
    ev = (
        rest_any.group_by(keys)
        .agg(pl.col("status").first().alias("any_status"))
        .join(
            rest_t60.group_by(keys).agg(
                (pl.col("status") == "out").any().alias("out_t60"),
                pl.len().alias("n_t60"),
            ),
            on=keys,
            how="left",
        )
        .join(played, on=keys, how="left")
        .join(games.select("game_id", "season"), on="game_id", how="left", suffix="_g")
        .with_columns(
            pl.col("played").fill_null(False),
            pl.col("out_t60").fill_null(False),
            pl.col("n_t60").fill_null(0),
        )
    )
    seas = pl.coalesce(pl.col("season"), pl.col("season_g")).alias("season_f")
    ev = ev.with_columns(seas)
    rows = []
    for s in (2022, 2023, 2024):
        e = ev.filter(pl.col("season_f") == s)
        dnp = e.filter(~pl.col("played"))
        rows.append(
            {
                "season": s,
                "rest_tagged_player_games": e.height,
                "rest_tagged_dnp": dnp.height,
                "dnp_out_on_t60_report": int(dnp["out_t60"].sum()),
                "dnp_not_out_at_t60": int((~dnp["out_t60"]).sum()),
                "dnp_on_t60_report_not_out": int(((dnp["n_t60"] > 0) & ~dnp["out_t60"]).sum()),
                "dnp_first_listed_after_t60": int((dnp["n_t60"] == 0).sum()),
                "rest_tagged_but_played": int(e["played"].sum()),
            }
        )
    # What the box-score DNP rows are, by their latest usable T-60 status (context for the P(play) cell)
    dnp_box = (
        con.execute(
            "SELECT s.game_id, s.player_id, g.season FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND (s.minutes IS NULL OR s.minutes = 0) "
            "AND substr(s.game_id,1,3) = '002'"
        )
        .pl()
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )
    last = (
        usable.sort("as_of")
        .group_by(keys)
        .agg(pl.col("status").last().alias("st"), pl.col("cls").last().alias("cls_last"))
    )
    mix_df = dnp_box.join(last, on=keys, how="left").with_columns(
        pl.col("st").fill_null("unlisted"),
        pl.when(pl.col("st") == "out")
        .then(pl.col("cls_last"))
        .otherwise(pl.lit("-"))
        .alias("cls_out"),
    )
    mix = mix_df.group_by(["season", "st"]).len().sort(["season", "st"]).to_dicts()
    mix_cls = (
        mix_df.filter(pl.col("st") == "out")
        .group_by(["season", "cls_out"])
        .len()
        .sort(["season", "cls_out"])
        .to_dicts()
    )
    # all DNPs of the P(play) population (MINUTES_V2 secondary rows; stored array, no fit)
    pp = np.load("reports/minutes_v2/pplay.npz", allow_pickle=True)
    pop = {}
    for s in (2023, 2024):
        m = pp["season"] == s
        pop[s] = {
            "rows": int(m.sum()),
            "dnp": int((pp["y"][m] == 0).sum()),
            "p_dnp": float((pp["y"][m] == 0).mean()),
        }
    out = {
        "rest_events": rows,
        "box_dnp_by_t60_status": mix,
        "box_dnp_out_by_reason_class": mix_cls,
        "pplay_population": {str(k): v for k, v in pop.items()},
    }
    out["pass"] = bool(
        all(r["dnp_not_out_at_t60"] >= REST_EVENTS_MIN for r in rows if r["season"] in SEASONS)
    )
    return out


# --------------------------------------------------------------------------- arena, tz, tips
def gate_arena(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    teams = load_team_markets(TEAMS_YAML)
    games = load_games(con)
    tips_missing = int(games["tip_utc"].is_null().sum())
    ids = set(games["home_team"].to_list()) | set(games["away_team"].to_list())
    missing_ids = sorted(i for i in ids if i not in teams)
    g = games.filter(pl.col("tip_utc").is_not_null())
    sched = W.team_schedule_features(g, teams)
    # team_context is EMPTY for 2022-24 (0 rows), so the committed table cannot validate distances; use public
    # great-circle reference distances (statute miles, +-5%) between arena cities instead.
    ref = {
        ("BKN", "LAL"): 2450,
        ("BOS", "MIA"): 1258,
        ("DEN", "UTA"): 370,
        ("ATL", "MIA"): 600,
        ("CHI", "NYK"): 710,
    }
    abbr = {str(v["abbr"]): int(k) for k, v in teams.items()}
    ref_err = {}
    for (x, y), miles in ref.items():
        ax, ay = teams[abbr[x]], teams[abbr[y]]
        m = W.haversine_miles(
            float(ax["lat"]), float(ax["lon"]), float(ay["lat"]), float(ay["lon"])
        )
        ref_err[f"{x}-{y}"] = {"model": m, "reference": miles, "rel_err": abs(m - miles) / miles}
    worst = max(v["rel_err"] for v in ref_err.values())
    rd = sched.drop_nulls("rest_days")
    n_tg = sched.height
    out = {
        "teams_in_yaml": len(teams),
        "team_ids_in_games_missing_coords": missing_ids,
        "games": games.height,
        "games_without_real_tip": tips_missing,
        "team_games": n_tg,
        "team_games_with_prior_in_season": int(sched["miles_from_prev"].is_not_null().sum()),
        "team_context_rows": 0,
        "reference_distance_check": ref_err,
        "worst_reference_rel_err": worst,
        "rest_days_mean": float(rd["rest_days"].mean()),
        "share_b2b_second": float(sched["b2b_second"].mean()),
        "share_three_in_four": float(sched["three_in_four"].mean()),
        "share_five_in_seven": float(sched["five_in_seven"].mean()),
        "share_road_trip4plus": float(sched["road_trip4plus"].mean()),
        "share_early_tip": float(sched["early_tip"].mean()),
        "share_eastbound": float(sched["eastbound"].mean()),
        "miles_from_prev_mean": float(sched["miles_from_prev"].mean()),
    }
    out["pass"] = bool(len(teams) == 30 and not missing_ids and tips_missing == 0 and worst < 0.05)
    return out


# --------------------------------------------------------------------------- A0
def gate_a0() -> dict[str, Any]:
    from research.eval.team_budget_gates import gate_a0 as _a0

    return _a0()


# --------------------------------------------------------------------------- missingness and as-of replay
def a0_rows(played: pl.DataFrame) -> pl.DataFrame:
    pic = np.load(LT_DIR / "components_pi.npz", allow_pickle=True)
    rows = pl.DataFrame(
        {"game_id": pic["row_gid"].astype(str), "player_id": pic["row_pid"].astype(np.int64)}
    )
    r = rows.join(
        played.select("game_id", "player_id", "team_id", "game_date", "season", "minutes"),
        on=["game_id", "player_id"],
        how="left",
    )
    assert r["minutes"].null_count() == 0, "A0 rows must all be played rows"
    return r.with_columns(bucket_expr())


def gate_missing(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    teams = load_team_markets(TEAMS_YAML)
    games = load_games(con).filter(pl.col("tip_utc").is_not_null())
    played = load_played(con)
    rows = a0_rows(played)
    _log(f"A0 rows {rows.height}")
    sched = W.team_schedule_features(games, teams)
    t0 = time.time()
    fr = W.build_workload_frame(
        rows.select("game_id", "player_id", "team_id", "game_date"), played, sched
    )
    _log(f"features built in {time.time() - t0:.0f}s")
    fr = fr.join(
        rows.select("game_id", "player_id", "season", "bucket", "minutes"),
        on=["game_id", "player_id"],
    )
    rng = np.random.default_rng(0)
    audit: list[dict[str, Any]] = []
    for c in [W.PREFIX + x for x in W.ALL_COLS]:
        isn = (
            fr[c].is_null() | fr[c].is_nan()
            if fr[c].dtype in (pl.Float64, pl.Float32)
            else fr[c].is_null()
        )
        tmp = fr.select("season", "bucket").with_columns(isn.alias("_n"))
        rate = {b: float(tmp.filter(pl.col("bucket") == b)["_n"].mean()) for b in BUCKETS}
        perm = rng.permutation(fr["bucket"].to_numpy())
        tmp2 = tmp.with_columns(pl.Series("_pb", perm))
        prate = {b: float(tmp2.filter(pl.col("_pb") == b)["_n"].mean()) for b in BUCKETS}
        gap = (max(rate.values()) - min(rate.values())) * 100
        pgap = (max(prate.values()) - min(prate.values())) * 100
        audit.append(
            {
                "col": c,
                "null_rate_by_bucket": rate,
                "gap_pp": gap,
                "placebo_gap_pp": pgap,
                "overall_null": float(tmp["_n"].mean()),
            }
        )
    max_gap = max(float(a["gap_pp"]) for a in audit)
    # as-of replay on real data: features recomputed with every row dated >= D removed equal the full-frame ones
    samp = fr.rename({W.PREFIX + c: c for c in W.ALL_COLS}).sample(n=300, seed=0)
    bad = 0
    for r in samp.iter_rows(named=True):
        d = r["game_date"]
        tgt = pl.DataFrame(
            {"game_id": [r["game_id"]], "player_id": [r["player_id"]], "game_date": [d]}
        )
        cut = W.player_load_features(played.filter(pl.col("game_date") < d), tgt).row(0, named=True)
        for c in W.LOAD_MIN_COLS + W.LOAD_TRACK_COLS:
            a, b = r[c], cut[c]
            if not (a == b or (a is None and b is None) or (np.isnan(a) and np.isnan(b))):
                bad += 1
    g_bad = 0
    n_no_sched = int(fr["wl_road_streak"].is_null().sum())
    for r in samp.filter(pl.col("road_streak").is_not_null()).head(100).iter_rows(named=True):
        d = r["game_date"]
        sub = W.team_schedule_features(games.filter(pl.col("game_date") <= d), teams)
        row = sub.filter(
            (pl.col("game_id") == r["game_id"]) & (pl.col("team_id") == r["team_id"])
        ).row(0, named=True)
        for c in W.SCHEDULE_COLS + W.TRAVEL_COLS:
            a, b = r[c], row[c]
            if not (a == b or (a is None and b is None)):
                g_bad += 1
    flags = {
        "b2b_second": pl.col("wl_b2b_second") == 1,
        "three_in_four": pl.col("wl_three_in_four") == 1,
        "five_in_seven": pl.col("wl_five_in_seven") == 1,
        "road_trip4plus": pl.col("wl_road_trip4plus") == 1,
        "early_tip": pl.col("wl_early_tip") == 1,
        "eastbound_ge2tz": pl.col("wl_tz_cross_hr") >= 2,
        "miles_ge1500": pl.col("wl_miles_from_prev") >= 1500,
        "speed_decline_bottom_decile": pl.col("wl_speed_decline")
        <= pl.col("wl_speed_decline").quantile(0.1),
    }
    slice_n = {}
    for nm, ex in flags.items():
        sub = fr.filter(ex)
        slice_n[nm] = {
            str(s): {
                "rows": int((sub["season"] == s).sum()),
                "games": int(sub.filter(pl.col("season") == s)["game_id"].n_unique()),
            }
            for s in SEASONS
        }
    summ = {
        c: {
            "mean": float(fr[c].drop_nans().drop_nulls().mean()),
            "sd": float(fr[c].drop_nans().drop_nulls().std()),
        }
        for c in (
            "wl_min_7d",
            "wl_dist_7d",
            "wl_speed_l3",
            "wl_speed_decline",
            "wl_miles_from_prev",
            "wl_miles_7d",
        )
    }
    out = {
        "n_rows": fr.height,
        "audit": audit,
        "max_gap_pp": max_gap,
        "max_placebo_gap_pp": max(float(a["placebo_gap_pp"]) for a in audit),
        "cols_over_limit": [
            a["col"] for a in audit if a["gap_pp"] > max(NULL_GAP_PP, 3 * a["placebo_gap_pp"])
        ],
        "asof_replay_mismatches_load": bad,
        "asof_replay_mismatches_schedule": g_bad,
        "asof_replay_rows": 300,
        "rows_without_schedule_features": n_no_sched,
        "rows_by_game_type": {
            k: int(v) for k, v in rows.group_by(pl.col("game_id").str.slice(0, 3)).len().iter_rows()
        },
        "feature_summary": summ,
        "flag_slice_counts": slice_n,
    }
    out["pass"] = bool(not out["cols_over_limit"] and bad == 0 and g_bad == 0)
    return out


def run_stage(st: str, con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    if st == "tracking":
        return gate_tracking(con)
    if st == "rest":
        return gate_rest(con)
    if st == "arena":
        return gate_arena(con)
    if st == "a0":
        return gate_a0()
    return gate_missing(con)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("tracking", "rest", "arena", "a0", "missing", "all"))
    args = ap.parse_args(argv)
    stages = (
        ("tracking", "rest", "arena", "a0", "missing") if args.stage == "all" else (args.stage,)
    )
    ok = True
    for st in stages:
        t0 = time.time()
        con = connect()
        try:
            res = run_stage(st, con)
        finally:
            con.close()
        _save(st, res)
        _log(f"{st}: pass={res.get('pass')} in {time.time() - t0:.0f}s")
        ok = ok and bool(res.get("pass"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
