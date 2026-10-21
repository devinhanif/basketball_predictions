# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,unused-ignore,index,union-attr"
"""COACH_PHASE minimum-data gates (docs/prereg/COACH_PHASE.md; DRAFT). Counts and noise scales only: no arm is fitted.

    .venv/bin/python -m research.archive.coach_phase.coach_phase_gates

Stages printed and saved to ``reports/prereg_coach_phase/gates.json``: A0 reproduction (pts, raw and mixture, from the stored
lower-tail components), rows and games per phase slice for the props rows and the minutes rows, a noise-scale MDE proxy per slice,
coach as-of playoff history, series-state derivation, clinch/elimination shares. Read-only: ``nba.duckdb`` opened ``read_only=True``,
``season <= 2024`` in SQL, season 2025 never loaded. Never takes ``data/ops/heavy.lock``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid, arm_scores, load_components
from research.features.phase import clinch_state, series_state

MAX_SEASON = 2024
SEASONS = (2023, 2024)
LATE_FROM = 67  # games_prior >= 67 means one of the team's last 15 regular-season games (82 - 15)
LT_DIR = Path("reports/lower_tail")
MIN_DIR = Path("reports/minutes_v2")
OUT = Path("reports/prereg_coach_phase/gates.json")
MIN_PHASE_GAMES = 200  # gate: games per season in the primary PHASE slice
MIN_COACHES_PO = 8  # gate: coaches with >= 20 as-of playoff games in the report season
A0_TOL = 1e-6
REF = {"pts": {"2023": (3.152830, 3.140236), "2024": (3.187553, 3.176840)}}


def _mean(x: pl.Series) -> float | None:
    return float(x.mean()) if len(x) else None


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def mde(df: pl.DataFrame, col: str) -> float:
    """1.96 x game-clustered SE of the mean of ``col`` (a noise scale for a paired difference)."""
    s = df.group_by("gid").agg(pl.col(col).sum().alias("s"), pl.len().alias("n"))
    n, g = int(s["n"].sum()), s.height
    if g < 2:
        return float("nan")
    m = float(s["s"].sum()) / n
    se = float(np.sqrt(((s["s"] - m * s["n"]) ** 2).sum())) / n * float(np.sqrt(g / (g - 1)))
    return 1.96 * se


def team_game_index(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    g = con.execute(
        f"SELECT game_id, game_date, season, home_team, away_team FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON} AND game_id LIKE '002%'"
    ).pl()
    rows = [(r[0], r[1], r[2], t) for r in g.iter_rows() for t in (r[3], r[4])]
    t = pl.DataFrame(rows, schema=["gid", "d", "season", "team"], orient="row").sort("d", "gid")
    t = t.with_columns((pl.col("gid").cum_count().over("team", "season")).alias("games_prior"))
    return t.group_by("gid").agg(
        pl.col("games_prior").max().alias("gp_max"), pl.col("games_prior").min().alias("gp_min")
    )


def phase_frame(
    gid: np.ndarray, season: np.ndarray, extra: dict[str, np.ndarray], idx: pl.DataFrame
) -> pl.DataFrame:
    f = pl.DataFrame({"gid": gid.astype(str), "season": season, **extra})
    f = f.join(idx, on="gid", how="left").with_columns(pl.col("gid").str.slice(0, 3).alias("stage"))
    return f.with_columns(
        (pl.col("stage") == "004").alias("po"),
        ((pl.col("stage") == "002") & (pl.col("gp_max") >= LATE_FROM)).alias("late15"),
    ).with_columns((pl.col("po") | pl.col("late15")).alias("phase"))


def slice_table(f: pl.DataFrame, col: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in SEASONS:
        d = f.filter(pl.col("season") == s)
        cell: dict[str, Any] = {}
        for k, m in (
            ("all", pl.lit(True)),
            ("playoff", pl.col("po")),
            ("late15", pl.col("late15")),
            ("phase", pl.col("phase")),
        ):
            x = d.filter(m)
            cell[k] = {"rows": x.height, "games": x["gid"].n_unique(), "noise_mde": mde(x, col)}
        out[str(s)] = cell
    return out


def gate_a0() -> dict[str, Any]:
    comp = load_components("pts", LT_DIR)
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    cells = []
    sc = {}
    for kind in ("off", "mixture"):
        sc[kind] = arm_scores(arm_grid(kind, comp, pic, "pts"), comp["row_y"], "pts")["crps_int"]
        for s in SEASONS:
            m = comp["row_season"] == s
            want = REF["pts"][str(s)][0 if kind == "off" else 1]
            got = float(sc[kind][m].mean())
            cells.append(
                {
                    "arm": kind,
                    "season": s,
                    "got": got,
                    "want": want,
                    "abs_diff": abs(got - want),
                    "n": int(m.sum()),
                }
            )
    out: dict[str, Any] = {"cells": cells, "max_abs_diff": max(c["abs_diff"] for c in cells)}
    out["pass"] = out["max_abs_diff"] <= 5e-7
    idx = team_game_index(duckdb.connect("nba.duckdb", read_only=True))
    f = phase_frame(
        comp["row_gid"],
        comp["row_season"],
        {"d": sc["mixture"] - sc["off"], "crps_mix": sc["mixture"]},
        idx,
    )
    out["props_rows"] = slice_table(f, "d")
    out["props_rows_mean_mixture_crps"] = {
        s: {
            k: float(f.filter((pl.col("season") == int(s)) & m)["crps_mix"].mean())
            for k, m in (
                ("playoff", pl.col("po")),
                ("late15", pl.col("late15")),
                ("phase", pl.col("phase")),
            )
        }
        for s in ("2023", "2024")
    }
    return out


def gate_minutes() -> dict[str, Any]:
    z = np.load(MIN_DIR / "minutes_scores.npz", allow_pickle=True)
    idx = team_game_index(duckdb.connect("nba.duckdb", read_only=True))
    f = phase_frame(
        z["row_gid"],
        z["row_season"],
        {"d": z["R_crps"] - z["P_crps"], "P": z["P_crps"], "y": z["row_y"]},
        idx,
    )
    out: dict[str, Any] = {"rows": slice_table(f, "d")}
    out["production_minutes_crps_P"] = {
        s: {
            k: float(f.filter((pl.col("season") == int(s)) & m)["P"].mean())
            for k, m in (
                ("playoff", pl.col("po")),
                ("late15", pl.col("late15")),
                ("phase", pl.col("phase")),
                ("regular_early", ~pl.col("phase") & ~pl.col("po")),
            )
        }
        for s in ("2023", "2024")
    }
    out["mean_minutes_given_plays"] = {
        s: {
            k: float(f.filter((pl.col("season") == int(s)) & m)["y"].mean())
            for k, m in (
                ("playoff", pl.col("po")),
                ("late15", pl.col("late15")),
                ("rest", ~pl.col("phase") & ~pl.col("po")),
            )
        }
        for s in ("2023", "2024")
    }
    return out


def gate_coach(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    d = (
        con.execute(
            f"""SELECT t.game_id, COALESCE(CAST(t.coach_id AS VARCHAR), t.coach_name) k, g.game_date, g.season, substr(g.game_id, 1, 3) st
            FROM team_coach_games t JOIN games g USING (game_id) WHERE g.season BETWEEN 2022 AND {MAX_SEASON}"""
        )
        .pl()
        .sort("game_date", "game_id")
    )
    d = d.with_columns((pl.col("st") == "004").cast(pl.Int32).alias("po"))
    d = d.with_columns((pl.col("po").cum_sum().over("k") - pl.col("po")).alias("prior_po"))
    out: dict[str, Any] = {}
    for s in SEASONS:
        p = d.filter((pl.col("season") == s) & (pl.col("po") == 1))
        out[str(s)] = {
            "playoff_team_games": p.height,
            "coaches": p["k"].n_unique(),
            "share_prior_po_gt0": float((p["prior_po"] > 0).mean()),
            "coaches_with_prior_po_ge20": p.filter(pl.col("prior_po") >= 20)["k"].n_unique(),
            "coaches_with_prior_po_ge10": p.filter(pl.col("prior_po") >= 10)["k"].n_unique(),
            "median_prior_po": float(p["prior_po"].median()),
        }
    out["pass_report_season_coaches"] = out["2024"]["coaches_with_prior_po_ge20"] >= MIN_COACHES_PO
    out["pass_select_season_coaches"] = out["2023"]["coaches_with_prior_po_ge20"] >= MIN_COACHES_PO
    return out


def gate_series_and_clinch(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    po = con.execute(
        f"SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON} AND game_id LIKE '004%'"
    ).pl()
    st = series_state(po)
    out: dict[str, Any] = {
        "playoff_team_games": st.height,
        "max_series_game": int(st["series_game"].max()),
    }
    out["elim_game_team_games_by_season"] = {}
    pj = st.join(po.select("game_id", "season"), on="game_id")
    for s in SEASONS:
        x = pj.filter(pl.col("season") == s)
        out["elim_game_team_games_by_season"][str(s)] = {
            "team_games": x.height,
            "elim_game": int(x["elim_game"].sum()),
            "games_by_series_game": {
                str(k): v
                for k, v in x.group_by("series_game").len().sort("series_game").iter_rows()
            },
        }
    rs = con.execute(
        f"SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON} AND game_id LIKE '002%'"
    ).pl()
    cs = clinch_state(rs).join(rs.select("game_id", "season"), on="game_id")
    idx = team_game_index(con)
    cs = cs.join(idx, left_on="game_id", right_on="gid")
    late = cs.filter(pl.col("gp_max") >= LATE_FROM)
    out["clinch_late15_team_games"] = {
        str(s): {
            "team_games": late.filter(pl.col("season") == s).height,
            **{
                c: _mean(late.filter(pl.col("season") == s)[c])
                for c in ("clinched_6", "clinched_10", "elim_6", "elim_10")
            },
        }
        for s in SEASONS
    }
    return out


def main() -> int:
    con = duckdb.connect("nba.duckdb", read_only=True)
    try:
        res: dict[str, Any] = {}
        for name, fn in (("a0_props", gate_a0), ("minutes", gate_minutes)):
            t0 = time.time()
            res[name] = fn()
            _log(f"{name} done in {time.time() - t0:.0f}s")
        res["coach"] = gate_coach(con)
        res["series_clinch"] = gate_series_and_clinch(con)
    finally:
        con.close()
    ph = res["a0_props"]["props_rows"]
    res["pass"] = {
        "a0_reproduced": bool(res["a0_props"]["pass"]),
        "phase_games_ge_200_each_season": all(
            ph[str(s)]["phase"]["games"] >= MIN_PHASE_GAMES for s in SEASONS
        ),
        "coach_claim_report_season": bool(res["coach"]["pass_report_season_coaches"]),
        "coach_claim_select_season": bool(res["coach"]["pass_select_season_coaches"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
