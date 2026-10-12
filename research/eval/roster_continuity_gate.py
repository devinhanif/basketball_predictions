# ruff: noqa: E501
"""ROSTER_CONTINUITY minimum-data gate (docs/prereg/ROSTER_CONTINUITY.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.roster_continuity_gate

Two parts:
(a) Elo: per-team season carryover as a function of returning-minutes share. The as-of opening roster
    must be knowable before game 1. The gate first checks whether such a source exists for 2023-24 in
    the repo (it does not: ``data/rosters`` holds only 2026-10-10 pulls; a season-end CommonTeamRoster
    includes later trades, docs/OPENING_WEEK_ROSTERS.md). For SIZING ONLY it then uses a box-score proxy
    (players with a box row, played or DNP, in the team's first game; NOT knowable pre-tip, never a
    feature) and computes, outcome-free, the expected log-loss gain KL(p_arm || p_prod) of a hypothetical
    team-specific carryover ``carry_T = carry + beta * (c_T - mean c)`` (beta scenarios 0.5 and 1.0).
(b) Props: early-season (team game index < 25) spread widening about the median for the bottom
    continuity tercile; outcome-free expected integer-CRPS gain for widening factors 1.05 and 1.10, per-row
    sd and design effect from the stored FULL-minus-A0 deltas, and the descriptive anchor (ratio of mean
    absolute error of the stored A0 median, low tercile vs the rest, early rows: the same look as the
    corr -0.24 already seen).
Nothing is fitted or scored. Writes ``reports/prereg_roster_continuity/gate.json``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_roster_continuity/gate.json"
ELO_CFG = C.ROOT / "configs/mov_elo_tuned.yaml"
EARLY_GAMES = 25
FLOOR_WIN = -0.004  # win log loss (ELO_HISTORY_WINDOW)
FLOOR_PROPS = -0.005
BETAS = (0.5, 1.0)
WIDEN = (1.05, 1.10)


def continuity_share(box: pl.DataFrame, tg: pl.DataFrame, roster: pl.DataFrame) -> pl.DataFrame:
    """Per (team_id, season): share of the team's PREVIOUS-season minutes played by players on ``roster``
    (team_id, season, player_id: the opening roster for that season). Previous-season minutes are counted
    for that same team only. Seasons without a previous season in ``box`` get null."""
    prev = (
        box.filter(pl.col("minutes").fill_null(0) > 0)
        .group_by("team_id", "season", "player_id")
        .agg(pl.col("minutes").sum().alias("m"))
        .with_columns((pl.col("season") + 1).alias("season"))
    )
    tot = prev.group_by("team_id", "season").agg(pl.col("m").sum().alias("tot"))
    ret = (
        roster.join(prev, on=["team_id", "season", "player_id"], how="inner")
        .group_by("team_id", "season")
        .agg(pl.col("m").sum().alias("ret"))
    )
    out = (
        roster.select("team_id", "season")
        .unique()
        .join(tot, on=["team_id", "season"], how="left")
        .join(ret, on=["team_id", "season"], how="left")
    )
    return out.with_columns(
        (pl.col("ret").fill_null(0.0) / pl.col("tot")).alias("continuity")
    ).select("team_id", "season", "continuity")


def first_game_roster_proxy(box: pl.DataFrame, tg: pl.DataFrame) -> pl.DataFrame:
    """SIZING PROXY ONLY (post-hoc box membership, not knowable pre-tip): players with a box row in the team's first game."""
    first = tg.filter(pl.col("k") == 0).select("team_id", "season", "game_id")
    return (
        box.join(first, on=["team_id", "season", "game_id"])
        .select("team_id", "season", "player_id")
        .unique()
    )


def elo_replay(
    games: pl.DataFrame, params: dict[str, float], carry: Callable[[int, int], float]
) -> np.ndarray:
    """``nba.models.injury_elo.sequential_elo_logits`` with a per-(team, new season) carryover. With a constant
    ``carry`` equal to ``params['season_carryover']`` it reproduces that function exactly (unit-tested)."""
    from nba.models.injury_elo import ELO_TO_LOGIT
    from nba.models.rung0_baselines import MovEloBaseline

    model = MovEloBaseline(seed=0, **params)
    ordered = games.with_row_index("_row").sort(["game_date", "game_id"])
    rows = ordered.select(
        ["_row", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"]
    ).rows()
    out = np.zeros(games.height, dtype=float)
    i, n = 0, len(rows)
    current: object = None
    while i < n:
        j = i
        while j < n and rows[j][1] == rows[i][1]:
            j += 1
        group = rows[i:j]
        for r_idx, _d, _s, h, a, _hp, _ap in group:
            out[r_idx] = (
                model.ratings_.get(h, model.initial_rating)
                + model.home_advantage_elo
                - model.ratings_.get(a, model.initial_rating)
            ) * ELO_TO_LOGIT
        for _r, _d, season, h, a, hp, ap in group:
            if current is not None and season != current:
                for team, rating in model.ratings_.items():
                    c = carry(int(team), int(season))
                    model.ratings_[team] = c * rating + (1.0 - c) * model.initial_rating
            current = season
            hr = model.ratings_.get(h, model.initial_rating)
            ar = model.ratings_.get(a, model.initial_rating)
            p_home = model._win_prob(h, a)
            actual = 1.0 if hp > ap else 0.0
            pre = hr + model.home_advantage_elo
            win_diff = (pre - ar) if actual >= 0.5 else (ar - pre)
            upd = (
                model.k_factor * model._mov_multiplier(float(hp - ap), win_diff) * (actual - p_home)
            )
            model.ratings_[h] = hr + upd
            model.ratings_[a] = ar - upd
        i = j
    return out


def main() -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid, load_components
    from nba.props.context_residual import to_integer_support
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    box = C.load_box(con)
    tg = C.team_game_index(box, games)
    out: dict[str, Any] = {"measured_at": C.now_cdt(), "seasons": list(C.SEASONS)}
    # (0) availability of an as-of opening roster for 2023-24
    roster_dirs = sorted(p.name for p in (C.ROOT / "data/rosters").glob("*") if p.is_dir())
    hist = [d for d in roster_dirs if d < "2025-07-01"]
    out["asof_opening_roster_dirs_2023_24"] = hist
    out["asof_opening_roster_available"] = bool(hist)
    proxy = first_game_roster_proxy(box, tg)
    cont = continuity_share(box, tg, proxy).filter(pl.col("season").is_in(C.SEASONS))
    cv = cont["continuity"].to_numpy()
    out["continuity_proxy"] = {
        "team_seasons": cont.height,
        "mean": float(np.nanmean(cv)),
        "sd": float(np.nanstd(cv)),
        "min": float(np.nanmin(cv)),
        "max": float(np.nanmax(cv)),
    }
    cmap = {(int(t), int(s)): float(c) for t, s, c in cont.rows()}
    cbar = float(np.nanmean(cv))
    # (a) Elo sizing, outcome-free KL
    params = {k: float(v) for k, v in yaml.safe_load(ELO_CFG.read_text()).items()}
    g = games.filter(pl.col("home_pts").is_not_null()).sort("game_date", "game_id")
    z0 = elo_replay(g, params, lambda t, s: params["season_carryover"])
    p0 = 1 / (1 + np.exp(-z0))
    gs = g["season"].to_numpy()
    kidx = tg.select("game_id", "team_id", "k")
    kh = (
        g.select("game_id", "home_team")
        .join(kidx, left_on=["game_id", "home_team"], right_on=["game_id", "team_id"], how="left")[
            "k"
        ]
        .to_numpy()
    )
    ka = (
        g.select("game_id", "away_team")
        .join(kidx, left_on=["game_id", "away_team"], right_on=["game_id", "team_id"], how="left")[
            "k"
        ]
        .to_numpy()
    )
    early = (np.nan_to_num(kh.astype(float), nan=99) < EARLY_GAMES) | (
        np.nan_to_num(ka.astype(float), nan=99) < EARLY_GAMES
    )
    elo: dict[str, Any] = {}
    for beta in BETAS:

        def carry(t: int, s: int, beta: float = beta) -> float:
            c = cmap.get((t, s))
            base = params["season_carryover"]
            return (
                base
                if c is None or np.isnan(c)
                else float(np.clip(base + beta * (c - cbar), 0.2, 0.95))
            )

        z1 = elo_replay(g, params, carry)
        p1 = 1 / (1 + np.exp(-z1))
        bg = C.bernoulli_gain(p0, p1)
        per: dict[str, Any] = {}
        for s in C.SEASONS:
            m = gs == s
            me = m & early
            sd = float(np.sqrt(bg["var"][me].mean()))
            per[str(s)] = {
                "games": int(m.sum()),
                "early_games": int(me.sum()),
                "mean_abs_prob_change_early": float(np.abs(p1 - p0)[me].mean()),
                "mean_abs_prob_change_after_game_25": float(np.abs(p1 - p0)[m & ~early].mean()),
                "expected_delta_early": float(-bg["kl"][me].mean()),
                "expected_delta_all": float(-bg["kl"][m].mean()),
                "early_slice": C.sizing_row(
                    "early games, floor -0.004, sd if arm true",
                    sd,
                    1.0,
                    me.sum(),
                    FLOOR_WIN,
                    float(-bg["kl"][me].mean()),
                ),
            }
        elo[f"beta_{beta}"] = per
    out["elo"] = elo
    print(f"[{time.time() - t0:5.1f}s] elo sized", flush=True)
    # (b) props sizing on stored A0 rows
    c = load_components("pts", RF.LT_DIR)
    if int(c["row_season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in stored components")
    pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
    keys = pl.DataFrame(
        {
            "game_id": c["row_gid"].astype(str),
            "player_id": c["row_pid"].astype(np.int64),
            "season": c["row_season"].astype(np.int64),
        }
    )
    kt = keys.join(
        box.select("game_id", "player_id", pl.col("team_id").cast(pl.Int64)).unique(
            ["game_id", "player_id"]
        ),
        on=["game_id", "player_id"],
        how="left",
    ).join(tg.select("game_id", "team_id", "k"), on=["game_id", "team_id"], how="left")
    kt = kt.join(
        cont.with_columns(pl.col("season").cast(pl.Int64)), on=["team_id", "season"], how="left"
    )
    k = kt["k"].fill_null(99).to_numpy()
    cc = kt["continuity"].fill_null(np.nan).to_numpy()
    y = c["row_y"]
    q0 = RF.arm_qi("pts", c, pic, pic["row_pi"])
    q1 = RF.arm_qi(
        "pts", RF.load_heads("pts", "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
    )
    d = PR.crps_int(q1, y) - PR.crps_int(q0, y)
    grid = arm_grid("mixture", c, pic, "pts")
    med = grid[:, grid.shape[1] // 2]
    gid = c["row_gid"].astype(str)
    season = c["row_season"]
    props: dict[str, Any] = {}
    for s in C.SEASONS:
        m = season == s
        tcut = np.nanquantile(cont.filter(pl.col("season") == s)["continuity"].to_numpy(), 1 / 3)
        low = m & (k < EARLY_GAMES) & (cc <= tcut)
        early_rows = m & (k < EARLY_GAMES)
        rest = early_rows & ~low
        mae_low = float(np.abs(y[low] - med[low]).mean())
        mae_rest = float(np.abs(y[rest] - med[rest]).mean())
        scen: dict[str, Any] = {}
        for w in WIDEN:
            qn = to_integer_support(med[low, None] + w * (grid[low] - med[low, None]))
            gain = C.expected_crps_gain(q0[low].astype(float), qn)
            scen[f"widen_{w}"] = {
                "expected_delta_slice": float(-gain.mean()),
                "expected_delta_all_rows": float(-gain.sum() / m.sum()),
            }
        cl = C.cluster_stats(d[low], gid[low])
        ca = C.cluster_stats(d[m], gid[m])
        props[str(s)] = {
            "rows": int(m.sum()),
            "early_rows": int(early_rows.sum()),
            "low_continuity_early_rows": int(low.sum()),
            "tercile_cut": float(tcut),
            "descriptive_mae_ratio_low_vs_rest": mae_low / mae_rest,
            "scenarios": scen,
            "slice": C.sizing_row(
                "low-continuity early rows, floor -0.005",
                cl["sd_row"],
                max(1.0, cl["deff"]),
                low.sum(),
                FLOOR_PROPS,
                scen["widen_1.1"]["expected_delta_slice"],
            ),
            "all_rows": C.sizing_row(
                "all rows, floor -0.005",
                ca["sd_row"],
                max(1.0, ca["deff"]),
                m.sum(),
                FLOOR_PROPS,
                scen["widen_1.1"]["expected_delta_all_rows"],
            ),
        }
    out["props"] = props
    gate: list[dict[str, Any]] = [
        {"check": "as-of opening roster exists for 2023-24", "value": len(hist), "pass": bool(hist)}
    ]
    for s in C.SEASONS:
        e = elo["beta_1.0"][str(s)]["early_slice"]
        gate.append(
            {
                "check": f"(a) {s} early-games power >= 0.5 at beta 1.0",
                "value": e["power_one_season"],
                "pass": e["power_one_season"] >= 0.5,
            }
        )
        pp = props[str(s)]["slice"]
        gate.append(
            {
                "check": f"(b) {s} low-continuity early rows power >= 0.5 at widen 1.10",
                "value": pp["power_one_season"],
                "pass": pp["power_one_season"] >= 0.5,
            }
        )
    out["gate"] = gate
    out["gate_pass_a"] = all(x["pass"] for x in gate if not x["check"].startswith("(b)"))
    out["gate_pass_b"] = all(x["pass"] for x in gate if not x["check"].startswith("(a)"))
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(
        json.dumps(
            {"gate": gate, "continuity_proxy": out["continuity_proxy"]}, indent=1, default=float
        )
    )
    return out


if __name__ == "__main__":
    main()


__all__ = ["Path", "continuity_share", "elo_replay", "first_game_roster_proxy", "main"]
