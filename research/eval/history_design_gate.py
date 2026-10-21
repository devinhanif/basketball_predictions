# ruff: noqa: E501
"""Data-capture audit and gate re-measure for six DRAFT rules under the 2026-10-12 history design.

    .venv/bin/python -W ignore -m research.eval.history_design_gate [--fetch] [--cache-dir DIR]

Design (Devin, 2026-10-12; DECISIONS 14:17 CDT): SELECT on seasons 2019-2024 (data/history holds 2019-21,
nba.duckdb 2022-24), REPORT once on season 2025 (one logged touch per rule, later), a pass is a production
proposal for 2026-27. This script never loads season 2025 games, outcomes, forecasts or prices: every SQL
caps ``season <= 2024``; the 2024 season stands in for 2025's size. Nothing is fitted or scored; no arm
exists. It reads both databases read-only and writes ``reports/prereg_history_design/gate.json``.

Parts:
1. Report capture by season and era (2019-24): games with a usable T-60 snapshot on a REAL tip (no proxy
   in history), snapshots per game, the lead of the latest usable snapshot, status mix per game, era flags.
2. Per-rule firing by season on a box-score proxy population (players with >= 5 earlier played games; the
   stored A0 / R row sets exist only for 2023-24): RETURNING_TEAMMATE flagged rows and returner play rate,
   QUESTIONABLE_STREAK streak rows, EXPECTED_ABSENCE listed-not-out players per game.
3. ROSTER_CONTINUITY: as-of year-over-year continuity (``continuity_asof``) from box scores plus preseason
   game logs (nba_api LeagueGameLog, read-only, ``--fetch``), coverage, and the outcome-free Elo carryover
   ceiling on 2019-24 with continuity known before game 1.
4. B2B_ELO: b2b counts by season and the outcome-free KL ceiling on a 2019-24 MOV-Elo replay.
5. ERROR_FEEDBACK: training coverage of the feature by first select block, if walk-forward forecasts were
   generated from season S0 (2020, 2021, 2022, or the stored 2023).
6. Sizing under the new design: pooled select half-width, report half-width with 2024 as the stand-in
   for 2025, power of select AND report, and the forward-log (2026-27) and pooled (2025 + 2026-27)
   alternatives. Per-row sd, design effect and expected effects come from the committed 2023-24 gate
   JSONs (reports/prereg_*/gate.json); they are re-used, not re-measured.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from research.eval import prereg_gates_common as C

HIST_DB = C.ROOT / "data/history/nba_history.duckdb"
HIST_SCHEDULE = C.ROOT / "data/history/schedule"
OUT = C.ROOT / "reports/prereg_history_design/gate.json"
DEFAULT_CACHE = C.ROOT / "data/ops/history_design_cache"
ALL_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
HIST_SEASONS = (2019, 2020, 2021)
SELECT_SEASONS = (2020, 2021, 2022, 2023, 2024)  # 2019 is warm-up for walk-forward fits
N_PRIOR = 5
EARLY_GAMES = 25
FWD_GAMES = 1230  # 2026-27 regular season
FWD_SHARE = FWD_GAMES / 1320  # rows of a full forward log relative to 2024's 1,320 games
PRESEASON = {
    2019: "2019-20",
    2020: "2020-21",
    2021: "2021-22",
    2022: "2022-23",
    2023: "2023-24",
    2024: "2024-25",
    2025: "2025-26",
}


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


# --------------------------------------------------------------------------- loading


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    if "season <= 2024" not in sql:
        raise RuntimeError("every query must cap `season <= 2024` (2025 is the holdout)")
    return con.execute(sql).pl()


def load_world() -> dict[str, pl.DataFrame]:
    """games, box, report and era flags for 2019-2024 from both databases (read-only)."""
    from nba.features.game_tipoff import build_game_tipoff

    games_sql = (
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
        "WHERE season <= 2024"
    )
    box_sql = (
        "SELECT s.game_id, s.team_id, s.player_id, s.minutes, g.game_date, g.season "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024"
    )
    rep_sql = (
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, CAST(a.as_of AS TIMESTAMP) AS as_of "
        "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
        f"WHERE a.source = '{C.REPORT_SOURCE}' AND a.player_id IS NOT NULL AND g.season <= 2024"
    )
    parts: dict[str, list[pl.DataFrame]] = defaultdict(list)
    for path in (C.DB, HIST_DB):
        con = duckdb.connect(str(path), read_only=True)
        try:
            parts["games"].append(_q(con, games_sql))
            parts["box"].append(_q(con, box_sql))
            parts["report"].append(_q(con, rep_sql))
            if path == HIST_DB:
                parts["flags"].append(
                    _q(
                        con,
                        "SELECT game_id, season, covid_bubble, no_fans, limited_fans, shortened_season "
                        "FROM game_era_flags WHERE season <= 2024",
                    )
                )
        finally:
            con.close()
    w = {k: pl.concat(v, how="vertical_relaxed") for k, v in parts.items()}
    if int(w["games"]["season"].max()) > C.MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 loaded")
    tips = pl.concat(
        [
            build_game_tipoff(max_season=C.MAX_SEASON),
            build_game_tipoff(HIST_SCHEDULE, max_season=C.MAX_SEASON),
        ],
        how="vertical_relaxed",
    ).unique("game_id", keep="first")
    w["games"] = (
        w["games"]
        .join(tips.select("game_id", "tip_et"), on="game_id", how="left")
        .with_columns(
            pl.col("tip_et").is_not_null().alias("tip_real"), pl.col("tip_et").alias("tip")
        )
    )
    return w


def prior_played(box: pl.DataFrame) -> pl.DataFrame:
    """box + n_prior (played games strictly before this row's date, any team, across seasons)."""
    b = box.with_columns((pl.col("minutes").fill_null(0) > 0).alias("played")).sort(
        "player_id", "game_date", "game_id"
    )
    by_date = b.group_by("player_id", "game_date").agg(pl.col("played").sum().alias("p_day"))
    by_date = by_date.sort("player_id", "game_date").with_columns(
        (pl.col("p_day").cum_sum().over("player_id") - pl.col("p_day")).alias("n_prior")
    )
    return b.join(
        by_date.select("player_id", "game_date", "n_prior"), on=["player_id", "game_date"]
    )


# --------------------------------------------------------------------------- roster continuity


def continuity_asof(
    box: pl.DataFrame, tg: pl.DataFrame, preseason: pl.DataFrame, prev_minutes: pl.DataFrame
) -> pl.DataFrame:
    """As-of year-over-year continuity per (team_id, game_id).

    ``prev_minutes``: team_id, season, player_id, m = minutes the player played FOR THAT TEAM in season - 1.
    ``preseason``: team_id, season, player_id of players who played for the team in that season's preseason
    (dated before the team's first game). ``box``: game_id, team_id, player_id (any box row = appeared).
    ``tg``: team_id, season, game_id, k (team game order, ``C.team_game_index``).

    c(T, g) = sum of prev-season T minutes of players who appeared for T in the preseason or in T's games with
    k' < k, divided by T's prev-season total. Only strictly earlier games are read; g's own box never enters.
    Columns: team_id, season, game_id, k, continuity (null when no previous season exists), known_players.
    """
    tot = prev_minutes.group_by("team_id", "season").agg(pl.col("m").sum().alias("tot"))
    pm = {
        (int(t), int(s), int(p)): float(m)
        for t, s, p, m in prev_minutes.select("team_id", "season", "player_id", "m").iter_rows()
    }
    tot_d = {(int(t), int(s)): float(v) for t, s, v in tot.rows()}
    pre: dict[tuple[int, int], set[int]] = defaultdict(set)
    for t, s, p in preseason.select("team_id", "season", "player_id").iter_rows():
        pre[(int(t), int(s))].add(int(p))
    k_box = box.join(tg.select("team_id", "game_id", "season", "k"), on=["team_id", "game_id"])
    appear: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    for t, s, k, p in k_box.select("team_id", "season", "k", "player_id").iter_rows():
        appear[(int(t), int(s), int(k))].add(int(p))
    rows: list[tuple[int, int, str, int, float | None, int]] = []
    for (t, s), grp in tg.sort("k").group_by(["team_id", "season"], maintain_order=True):
        t, s = int(t), int(s)
        known = set(pre[(t, s)])
        total = tot_d.get((t, s))
        for gid, k in grp.select("game_id", "k").iter_rows():
            c = None if not total else sum(pm.get((t, s, p), 0.0) for p in known) / total
            rows.append((t, s, str(gid), int(k), c, len(known)))
            known |= appear[(t, s, int(k))]
    return pl.DataFrame(
        rows,
        schema={
            "team_id": pl.Int64,
            "season": pl.Int64,
            "game_id": pl.Utf8,
            "k": pl.Int64,
            "continuity": pl.Float64,
            "known_players": pl.Int64,
        },
        orient="row",
    )


def prev_season_minutes(box: pl.DataFrame, extra: pl.DataFrame | None = None) -> pl.DataFrame:
    """team_id, season (= the NEXT season), player_id, m from played box rows (plus ``extra`` logs)."""
    b = box.filter(pl.col("minutes").fill_null(0) > 0).select(
        "team_id", "season", "player_id", pl.col("minutes").cast(pl.Float64)
    )
    if extra is not None and extra.height:
        b = pl.concat([b, extra.select(b.columns)], how="vertical_relaxed")
    return (
        b.group_by("team_id", "season", "player_id")
        .agg(pl.col("minutes").sum().alias("m"))
        .with_columns((pl.col("season") + 1).alias("season"))
    )


def fetch_logs(cache: Path, sleep_s: float = 2.0) -> dict[str, Any]:
    """Read-only nba_api LeagueGameLog pulls (player mode): preseason 2019-20..2025-26 and the 2018-19
    regular season (previous-season minutes for 2019). Cached as parquet; 2025-26 keeps ids only (no stats)."""
    from nba_api.stats.endpoints import leaguegamelog

    cache.mkdir(parents=True, exist_ok=True)
    log: dict[str, Any] = {}
    jobs = [(s, lbl, "Pre Season") for s, lbl in PRESEASON.items()] + [
        (2018, "2018-19", "Regular Season")
    ]
    for season, label, kind in jobs:
        f = cache / f"{'pre' if kind == 'Pre Season' else 'reg'}_{season}.parquet"
        if f.exists():
            log[f.name] = "cached"
            continue
        r = leaguegamelog.LeagueGameLog(
            season=label, season_type_all_star=kind, player_or_team_abbreviation="P", timeout=60
        )
        df = pl.from_pandas(r.get_data_frames()[0])
        keep = ["PLAYER_ID", "TEAM_ID", "GAME_ID", "GAME_DATE"] + (
            [] if season == 2025 else ["MIN"]
        )
        df = df.select(keep).with_columns(pl.lit(season).alias("season"))
        df.write_parquet(f)
        log[f.name] = df.height
        print(f"fetched {f.name}: {df.height} rows", flush=True)
        time.sleep(sleep_s)
    return log


def load_logs(cache: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(preseason players per team-season for seasons <= 2024 + 2025 coverage frame, 2018-19 minutes)."""
    pre = []
    for f in sorted(cache.glob("pre_*.parquet")):
        d = pl.read_parquet(f)
        if "MIN" in d.columns:
            d = d.filter(pl.col("MIN").fill_null(0) > 0)
        pre.append(
            d.select(
                pl.col("TEAM_ID").cast(pl.Int64).alias("team_id"),
                pl.col("season").cast(pl.Int64),
                pl.col("PLAYER_ID").cast(pl.Int64).alias("player_id"),
                pl.col("GAME_DATE").cast(pl.Utf8).alias("game_date"),
            )
        )
    preseason = (
        pl.concat(pre)
        if pre
        else pl.DataFrame(
            schema={
                "team_id": pl.Int64,
                "season": pl.Int64,
                "player_id": pl.Int64,
                "game_date": pl.Utf8,
            }
        )
    )
    reg_f = cache / "reg_2018.parquet"
    reg = (
        pl.read_parquet(reg_f).select(
            pl.col("TEAM_ID").cast(pl.Int64).alias("team_id"),
            pl.lit(2018).cast(pl.Int64).alias("season"),
            pl.col("PLAYER_ID").cast(pl.Int64).alias("player_id"),
            pl.col("MIN").cast(pl.Float64).alias("minutes"),
        )
        if reg_f.exists()
        else pl.DataFrame(
            schema={
                "team_id": pl.Int64,
                "season": pl.Int64,
                "player_id": pl.Int64,
                "minutes": pl.Float64,
            }
        )
    )
    return preseason, reg


# --------------------------------------------------------------------------- sizing helpers


def design_power(
    sd: float,
    deff: float,
    n_report: float,
    effect: float,
    floor: float,
    n_select: float | None = None,
) -> dict[str, Any]:
    """Report-season (2025, 2024 as stand-in), forward-log and pooled alternatives, and select x report."""
    out: dict[str, Any] = {
        "report_2025": C.sizing_row("report 2025 (2024 size)", sd, deff, n_report, floor, effect),
        "forward_2026_27": C.sizing_row(
            "forward 2026-27 full season", sd, deff, n_report * FWD_SHARE, floor, effect
        ),
        "pooled_2025_plus_forward": C.sizing_row(
            "2025 + 2026-27 pooled", sd, deff, n_report * (1 + FWD_SHARE), floor, effect
        ),
    }
    if n_select is not None:
        sel = C.sizing_row("select pooled", sd, deff, n_select, floor, effect)
        out["select_pooled"] = sel
        out["p_select_and_report"] = (
            sel["power_one_season"] * out["report_2025"]["power_one_season"]
        )
    return out


def mov_elo_logits(games: pl.DataFrame) -> np.ndarray:
    from research.eval.roster_continuity_gate import elo_replay

    params = {
        k: float(v)
        for k, v in yaml.safe_load((C.ROOT / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    return elo_replay(games, params, lambda t, s: params["season_carryover"])


# --------------------------------------------------------------------------- main


def report_capture(w: dict[str, pl.DataFrame]) -> dict[str, Any]:
    games = w["games"]
    real = games.filter(pl.col("tip_real"))
    t60 = C.latest_t60(w["report"], real)
    flags = w["flags"]
    out: dict[str, Any] = {}
    snaps = (
        w["report"]
        .join(real.select("game_id", "tip"), on="game_id")
        .filter(pl.col("as_of") <= pl.col("tip") - pl.duration(minutes=C.LEAD_MINUTES))
        .group_by("game_id")
        .agg(pl.col("as_of").n_unique().alias("n_snap"))
    )
    lead = (
        t60.select("game_id", "snap_as_of")
        .unique()
        .join(real.select("game_id", "tip"), on="game_id")
    )
    lead = lead.with_columns(
        ((pl.col("tip") - pl.col("snap_as_of")).dt.total_minutes()).alias("lead")
    )
    mix = t60.join(games.select("game_id", "season"), on="game_id")
    for s in ALL_SEASONS:
        g = games.filter(pl.col("season") == s)
        ids = g["game_id"]
        gr = g.filter(pl.col("tip_real"))
        cov = t60.filter(pl.col("game_id").is_in(ids))["game_id"].n_unique()
        m = mix.filter(pl.col("season") == s)
        ng = max(cov, 1)
        row: dict[str, Any] = {
            "games": g.height,
            "games_real_tip": gr.height,
            "games_usable_t60_snapshot": int(cov),
            "share": cov / g.height,
            "snapshots_pre_t60_per_game": float(
                _f(snaps.filter(pl.col("game_id").is_in(ids))["n_snap"].mean())
            ),
            "median_lead_min_latest_t60": float(
                _f(lead.filter(pl.col("game_id").is_in(ids))["lead"].median())
            ),
            "listed_per_game": {
                st: m.filter(pl.col("status") == st).height / ng
                for st in ("out", "doubtful", "questionable", "probable", "available")
            },
        }
        if s in HIST_SEASONS:
            f = flags.filter(pl.col("season") == s)
            row["era_flags"] = {
                c: int(f[c].cast(pl.Int64).sum())
                for c in ("covid_bubble", "no_fans", "limited_fans", "shortened_season")
            }
        out[str(s)] = row
    return out


def per_rule_firing(
    w: dict[str, pl.DataFrame], tg: pl.DataFrame, bx: pl.DataFrame, lead: int = C.LEAD_MINUTES
) -> dict[str, Any]:
    from research.eval.questionable_streak_gate import (
        build_questionable_streak,
        first_listed_status,
    )
    from research.eval.returning_teammate_gate import ROTATION_MIN, STAR_MIN, build_returning

    real = w["games"].filter(pl.col("tip_real"))
    t60 = C.latest_t60(w["report"], real, lead)
    first = first_listed_status(w["report"], real, lead)
    out: dict[str, Any] = {}
    for s in ALL_SEASONS:
        t0 = time.time()
        b = bx.filter(pl.col("season") == s)
        tgs = tg.filter(pl.col("season") == s)
        gids = b["game_id"].unique()
        st = t60.filter(pl.col("game_id").is_in(gids))
        f, r = build_returning(
            b.select("game_id", "team_id", "player_id", "minutes", "game_date"), tgs, st
        )
        pop = b.filter(pl.col("n_prior") >= N_PRIOR).select("game_id", "team_id", "player_id")
        fp = pop.join(f, on=["game_id", "team_id", "player_id"], how="left")
        flagged = int((fp["ret_min"].fill_null(0) > 0).sum())
        rplay = r.join(
            b.select("game_id", pl.col("player_id").alias("returner_id"), "minutes"),
            on=["game_id", "returner_id"],
            how="left",
        ).with_columns((pl.col("minutes").fill_null(0) > 0).alias("played"))
        rot = rplay.filter(pl.col("pre_mpg") >= ROTATION_MIN)
        star = rplay.filter(pl.col("pre_mpg") >= STAR_MIN)
        # QS population: proxy of R's rows (>= 5 prior played, not OUT at T-60)
        q = pop.join(
            st.select("game_id", "player_id", "status"), on=["game_id", "player_id"], how="left"
        )
        q = q.filter(pl.col("status").fill_null("") != "out")
        qs = build_questionable_streak(
            q.select("game_id", "player_id", "team_id"),
            tgs,
            st,
            first,
            b.select("game_id", "player_id", "minutes"),
        )
        q_rows = q.filter(pl.col("status") == "questionable").height
        qq = qs.join(
            q.select("game_id", "player_id", "status"), on=["game_id", "player_id"], how="left"
        ).filter(pl.col("status") == "questionable")
        out[str(s)] = {
            "proxy_rows": pop.height,
            "rt_flagged_rows": flagged,
            "rt_flag_share": flagged / max(pop.height, 1),
            "rt_returner_games": r.height,
            "rt_returner_play_rate_all": _f(rplay["played"].mean()),
            "rt_returner_play_rate_rotation": _f(rot["played"].mean()) if rot.height else None,
            "rt_rotation_returners": rot.height,
            "rt_returner_play_rate_star": _f(star["played"].mean()) if star.height else None,
            "rt_star_returners": star.height,
            "qs_rows": q.height,
            "qs_questionable_rows": q_rows,
            "qs_streak_ge1_all_rows": int((qs["q_streak"] >= 1).sum()),
            "qs_streak_ge1": int((qq["q_streak"] >= 1).sum()),
            "qs_streak_ge3": int((qq["q_streak"] >= 3).sum()),
            "qs_change_nonzero": int((qs["q_change"] != 0).sum()),
            "ea_questionable_listed_per_game": st.filter(pl.col("status") == "questionable").height
            / max(gids.len(), 1),
        }
        print(f"[{s}] firing measured in {time.time() - t0:.1f}s", flush=True)
    return out


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="pull preseason/2018-19 logs via nba_api")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    t0 = time.time()
    cache = Path(a.cache_dir)
    out: dict[str, Any] = {"measured_at": C.now_cdt(), "seasons": list(ALL_SEASONS)}
    if a.fetch:
        out["fetch"] = fetch_logs(cache)
    w = load_world()
    games = w["games"]
    box = w["box"]
    tg = C.team_game_index(
        box,
        games.with_columns(
            pl.coalesce("tip", pl.col("game_date").cast(pl.Datetime("us"))).alias("tip")
        ),
    )
    bx = prior_played(box)
    print(
        f"[{time.time() - t0:5.1f}s] loaded {games.height} games, {box.height} box rows", flush=True
    )

    # 1. report capture
    out["report_capture"] = report_capture(w)
    print(f"[{time.time() - t0:5.1f}s] report capture", flush=True)

    # 2. per-rule firing
    out["firing"] = per_rule_firing(w, tg, bx)
    # harmonised cadence: 2022-24 hold ~2.5 pre-tip snapshots with the latest ~180 min before tip, 2021-22 is
    # hourly (latest ~60 min). Using the latest snapshot <= tip - 180 in every season removes that asymmetry.
    out["firing_lead180"] = per_rule_firing(w, tg, bx, lead=180)

    # 3. roster continuity
    preseason, reg2018 = load_logs(cache)
    prev = prev_season_minutes(box, reg2018)
    pres_le24 = preseason.filter(pl.col("season") <= C.MAX_SEASON)
    cont = continuity_asof(box.select("game_id", "team_id", "player_id"), tg, pres_le24, prev)
    rc: dict[str, Any] = {"preseason_coverage": {}}
    nba_teams = set(int(t) for t in games["home_team"].unique().to_list())
    for s in PRESEASON:
        p = preseason.filter(pl.col("season") == s)
        teams = set(int(t) for t in p["team_id"].unique().to_list()) & nba_teams
        rc["preseason_coverage"][str(s)] = {
            "nba_teams_with_preseason_rows": len(teams),
            "player_team_pairs": p.filter(pl.col("team_id").is_in(list(nba_teams)))
            .select("team_id", "player_id")
            .unique()
            .height,
        }
    rc["prev_season_2018_19_rows"] = reg2018.height
    per_c: dict[str, Any] = {}
    for s in ALL_SEASONS:
        c0 = cont.filter((pl.col("season") == s) & (pl.col("k") == 0))
        c25 = cont.filter((pl.col("season") == s) & (pl.col("k") == EARLY_GAMES - 1))
        cv = c0["continuity"].drop_nulls().to_numpy()
        per_c[str(s)] = {
            "teams": c0.height,
            "game1_defined": int(c0["continuity"].is_not_null().sum()),
            "game1_mean": float(cv.mean()) if cv.size else None,
            "game1_sd": float(cv.std()) if cv.size else None,
            "game1_min": float(cv.min()) if cv.size else None,
            "game25_mean": _f(c25["continuity"].drop_nulls().mean()) if c25.height else None,
        }
    rc["continuity_by_season"] = per_c
    # Elo carryover ceiling, continuity at game 1 (preseason roster), 2019-24 replay
    g = games.filter(pl.col("home_pts").is_not_null()).sort("game_date", "game_id")
    z0 = mov_elo_logits(g)
    from research.eval.roster_continuity_gate import elo_replay

    params = {
        k: float(v)
        for k, v in yaml.safe_load((C.ROOT / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    c1 = cont.filter(pl.col("k") == 0).drop_nulls("continuity")
    cmap = {
        (int(t), int(s)): float(c)
        for t, s, c in c1.select("team_id", "season", "continuity").rows()
    }
    cbar = float(np.mean(list(cmap.values()))) if cmap else 0.0
    kidx = tg.select("game_id", "team_id", "k")
    kh = (
        g.select("game_id", "home_team")
        .join(kidx, left_on=["game_id", "home_team"], right_on=["game_id", "team_id"], how="left")[
            "k"
        ]
        .fill_null(99)
        .to_numpy()
    )
    ka = (
        g.select("game_id", "away_team")
        .join(kidx, left_on=["game_id", "away_team"], right_on=["game_id", "team_id"], how="left")[
            "k"
        ]
        .fill_null(99)
        .to_numpy()
    )
    early = (kh < EARLY_GAMES) | (ka < EARLY_GAMES)
    gs = g["season"].to_numpy()
    p0 = 1 / (1 + np.exp(-z0))
    elo: dict[str, Any] = {}
    for beta in (0.5, 1.0):

        def carry(t: int, s: int, beta: float = beta) -> float:
            c = cmap.get((t, s))
            base = params["season_carryover"]
            return base if c is None else float(np.clip(base + beta * (c - cbar), 0.2, 0.95))

        p1 = 1 / (1 + np.exp(-elo_replay(g, params, carry)))
        bg = C.bernoulli_gain(p0, p1)
        per: dict[str, Any] = {}
        for s in ALL_SEASONS:
            me = (gs == s) & early
            per[str(s)] = {
                "early_games": int(me.sum()),
                "expected_delta_early": float(-bg["kl"][me].mean()),
                "sd_if_true": float(np.sqrt(bg["var"][me].mean())),
            }
        sel = np.isin(gs, SELECT_SEASONS) & early
        per["select_pooled"] = {
            "early_games": int(sel.sum()),
            "expected_delta_early": float(-bg["kl"][sel].mean()),
            "sd_if_true": float(np.sqrt(bg["var"][sel].mean())),
        }
        elo[f"beta_{beta}"] = per
    rc["elo_ceiling"] = elo
    # props slice size in 2024 with the as-of continuity (bottom tercile of game-1 continuity)
    b24 = bx.filter((pl.col("season") == 2024) & (pl.col("n_prior") >= N_PRIOR)).join(
        cont.select("team_id", "game_id", "k", "continuity"), on=["team_id", "game_id"], how="left"
    )
    cut = float(np.nanquantile(c1.filter(pl.col("season") == 2024)["continuity"].to_numpy(), 1 / 3))
    rc["props_2024_low_continuity_early_proxy_rows"] = int(
        b24.filter((pl.col("k") < EARLY_GAMES) & (pl.col("continuity") <= cut)).height
    )
    rc["props_2024_tercile_cut_asof"] = cut
    out["roster_continuity"] = rc
    print(f"[{time.time() - t0:5.1f}s] continuity", flush=True)

    # 4. B2B
    from research.eval.b2b_elo_gate import build_b2b

    b2b = build_b2b(g.select("game_id", "game_date", "home_team", "away_team")).join(
        g.select("game_id", "season", "game_date"), on="game_id"
    )
    bd = b2b["b2b_diff"].to_numpy().astype(float)
    p1 = 1 / (1 + np.exp(-(z0 - 0.35 * bd)))
    bg = C.bernoulli_gain(p0, p1)
    flags = w["flags"].select("game_id", "covid_bubble")
    bub = (
        g.select("game_id")
        .join(flags, on="game_id", how="left")["covid_bubble"]
        .fill_null(False)
        .to_numpy()
    )
    b2: dict[str, Any] = {}
    for s in ALL_SEASONS:
        m = gs == s
        b2[str(s)] = {
            "games": int(m.sum()),
            "b2b_diff_nonzero": int((m & (bd != 0)).sum()),
            "bubble_games": int((m & bub).sum()),
            "b2b_diff_nonzero_in_bubble": int((m & bub & (bd != 0)).sum()),
            "expected_delta_beta_-0.35_movelo": float(-bg["kl"][m].mean()),
            "sd_if_true": float(np.sqrt(bg["var"][m].mean())),
        }
    sel = np.isin(gs, SELECT_SEASONS)
    b2["select_pooled_games"] = int(sel.sum())
    out["b2b"] = b2

    # 5. ERROR_FEEDBACK training coverage
    pop = bx.filter(pl.col("n_prior") >= N_PRIOR).select("player_id", "game_date", "season")
    first_block = {s: pop.filter(pl.col("season") == s)["game_date"].min() for s in SELECT_SEASONS}
    ef: dict[str, Any] = {}
    for s0 in (2020, 2021, 2022, 2023):
        st_rows = pop.filter(pl.col("season") >= s0).sort("player_id", "game_date")
        st_rows = st_rows.with_columns(pl.int_range(pl.len()).over("player_id").alias("j"))
        has = st_rows.filter(pl.col("j") >= 3).select("player_id", "game_date")
        row: dict[str, Any] = {}
        for s, d0 in first_block.items():
            if s < s0:
                continue
            train = pop.filter(pl.col("game_date") < d0)
            n_has = has.filter(pl.col("game_date") < d0).height
            row[str(s)] = n_has / max(train.height, 1)
        ef[f"forecasts_from_{s0}"] = row
    out["error_feedback_training_coverage_first_block"] = ef
    out["wall_s"] = time.time() - t0
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out["sizing"] = sizing(out)
    Path(a.out).write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out["sizing"], indent=1, default=float))
    return out


def sizing(out: dict[str, Any]) -> dict[str, Any]:
    """Select/report/forward sizing per rule from the committed 2023-24 gate JSONs (sd, deff, effects)."""
    J = {
        r: json.loads((C.ROOT / f"reports/prereg_{r}/gate.json").read_text())
        for r in (
            "returning_teammate",
            "error_feedback",
            "b2b_elo",
            "questionable_streak",
            "expected_absence",
            "roster_continuity",
        )
    }
    fire = out["firing"]
    res: dict[str, Any] = {}
    # RETURNING_TEAMMATE flagged rows; select n scaled by proxy flagged rows over select seasons
    rt = J["returning_teammate"]["sizing"]["pts"]["2024"]["slice_flag_rows"]
    scale = rt["n"] / max(fire["2024"]["rt_flagged_rows"], 1)
    n_sel = scale * sum(fire[str(s)]["rt_flagged_rows"] for s in SELECT_SEASONS)
    res["returning_teammate"] = {
        "p_star_0.77": design_power(
            rt["sd_row"], rt["deff"], rt["n"], rt["expected_effect"], -0.005, n_sel
        ),
        "p_rotation_0.63": design_power(
            rt["sd_row"],
            rt["deff"],
            rt["n"],
            J["returning_teammate"]["sizing"]["pts"]["2024"]["scenarios"][
                "realistic_rotation_play_rate"
            ]["expected_delta_flagged_rows"],
            -0.005,
            n_sel,
        ),
        "select_flagged_rows_est": n_sel,
    }
    e = J["error_feedback"]["sizing"]
    e24 = e["2024"]["primary"]
    pooled_rows = (
        sum(fire[str(s)]["proxy_rows"] for s in SELECT_SEASONS)
        * e24["n"]
        / max(fire["2024"]["proxy_rows"], 1)
    )
    res["error_feedback"] = {
        "slope_2024": design_power(
            e24["sd_row"], e24["deff"], e24["n"], e24["expected_effect"], -0.005, pooled_rows
        ),
        "slope_2023": design_power(
            e24["sd_row"],
            e24["deff"],
            e24["n"],
            e["2023"]["primary"]["expected_effect"],
            -0.005,
            pooled_rows,
        ),
    }
    b = J["b2b_elo"]["sizing"]["2024"]["beta_-0.35"]["primary_bernoulli_sd"]
    res["b2b_elo"] = {
        "floor_-0.002": design_power(
            b["sd_row"],
            b["deff"],
            b["n"],
            b["expected_effect"],
            -0.002,
            out["b2b"]["select_pooled_games"],
        ),
        "one_sided_floor_0": {
            k: C.pass_power(
                b["expected_effect"],
                -1.645 * b["sd_row"] * math.sqrt(b["deff"] / n),
                b["sd_row"] * math.sqrt(b["deff"] / n),
            )
            for k, n in (
                ("report_2025", b["n"]),
                ("forward", b["n"] * FWD_SHARE),
                ("pooled", b["n"] * (1 + FWD_SHARE)),
            )
        },
    }
    q = J["questionable_streak"]["sizing"]["2024"]
    res["questionable_streak"] = {
        "all_rows": design_power(
            q["primary_all_rows"]["sd_row"],
            q["primary_all_rows"]["deff"],
            q["primary_all_rows"]["n"],
            q["primary_all_rows"]["expected_effect"],
            -0.005,
        ),
        "questionable_slice": design_power(
            q["slice_questionable"]["sd_row"],
            q["slice_questionable"]["deff"],
            q["slice_questionable"]["n"],
            q["slice_questionable"]["expected_effect"],
            -0.005,
        ),
        "streak_rows_by_season": {s: fire[s]["qs_streak_ge1"] for s in fire},
    }
    x = J["expected_absence"]["sizing"]["2024"]["primary_bernoulli_sd"]
    res["expected_absence"] = design_power(
        x["sd_row"], x["deff"], x["n"], x["expected_effect"], -0.002
    )
    rp = J["roster_continuity"]["props"]["2024"]["slice"]
    el = out["roster_continuity"]["elo_ceiling"]["beta_1.0"]["2024"]
    res["roster_continuity"] = {
        "a_elo_beta1": design_power(
            el["sd_if_true"], 1.0, el["early_games"], el["expected_delta_early"], -0.004
        ),
        "b_props_widen_1.10": design_power(
            rp["sd_row"], rp["deff"], rp["n"], rp["expected_effect"], -0.005
        ),
    }
    return res


if __name__ == "__main__":
    main()
