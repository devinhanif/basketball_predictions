# ruff: noqa: E501
"""TEAMMATE_SITS minimum-data gate (docs/prereg/TEAMMATE_SITS.md, DRAFT; non-negotiable 8).

Two stages, both read-only on ``nba.duckdb`` and capped at ``season <= 2024`` (2025 is never loaded):

* ``counts`` (light, label-free; the only label-like input is the played flag ``minutes > 0`` that defines the scored
  rows, exactly as production scoring does): starter-out events and teammate rows per season, clusters, and the
  production tie (the event table's per-team sums reproduce ``vacated_features`` exactly).

      python -W ignore -m research.teammate_sits.gate counts --data-root <main checkout> --out <json>

* ``size`` (heavy, maintainer; fits one context-residual head per stat on the rows before each season start, which
  reads TRAINING labels only, never a test-row outcome): the two arms' integer grids on the event teammate rows and
  the label-free sizing (synthetic outcomes drawn from each arm's own grid).

      mkdir data/ops/heavy.lock && (python -W ignore -m research.teammate_sits.gate size --data-root <main checkout> \\
          --out reports/prereg_teammate_sits/size.json; rmdir data/ops/heavy.lock)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.teammate_sits import scenario as S

SEASONS = (2023, 2024)
SELECT, REPORT = 2023, 2024
MAX_K = 3  # absence games 1..3 (T476's window)
FLOOR = -0.005
HW_BAR = 0.005
SIZE_SEED = 20261110  # synthetic-outcome draws for sizing only (the rule's bootstrap seed is 2026)
SLOPE_SE_BAR = 0.25
ORACLE_BAR = FLOOR
POWER_BAR = 0.5


def _num(x: Any) -> float:
    """A polars scalar aggregate as a float (polars' stubs type it as a wide union)."""
    return float(x)


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load(root: Path, t0: float) -> dict[str, Any]:
    import yaml

    import nba.features.game_tipoff as gt
    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import (
        ContextResidualConfig,
        flagged_from_availability,
        player_states,
    )

    gt.SCHEDULE_DIR = (
        root / "data" / "schedule"
    )  # real tips from the main checkout's schedule files
    games, pgs, static, avail = load_inputs(str(root / "nba.duckdb"), None, S.MAX_SEASON)
    if _num(games["season"].max()) > S.MAX_SEASON:
        raise RuntimeError("season 2025 is the frozen holdout")
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=S.MAX_SEASON)
    _log(
        t0,
        f"inputs: {games.height} games, {pgs.height} player-games, {len(flagged)} reported games",
    )
    played = S.played_frame(games, pgs)
    states = player_states(played)
    return {
        "games": games,
        "pgs": pgs,
        "static": static,
        "flagged": flagged,
        "played": played,
        "states": states,
        "info": S.game_info_of(games),
        "cfg": cfg,
    }


def absence_index(sit: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """k = the team's games after the sitter's last played game up to and including this one (k = 1: he played the
    team's previous game). Uses the team of his last game (the team production attributes his vacancy to)."""
    tg = pl.concat(
        [
            games.select("game_date", pl.col("home_team").alias("team_id")),
            games.select("game_date", pl.col("away_team").alias("team_id")),
        ]
    )
    dates = {
        int(t): np.sort(np.asarray(ds, dtype="datetime64[D]"))
        for t, ds in tg.group_by("team_id").agg(pl.col("game_date")).iter_rows()
    }
    k = [
        int(np.searchsorted(dates[int(t)], np.datetime64(g, "D"), "right"))
        - int(np.searchsorted(dates[int(t)], np.datetime64(lst, "D"), "right"))
        for t, g, lst in sit.select("team_id", "game_date", "last_date").iter_rows()
    ]
    return sit.with_columns(pl.Series("k", k, dtype=pl.Int64))


def events(w: dict[str, Any]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(all regular-starter OUT rows with k and season, primary events: team-games with exactly one regular starter
    OUT at k <= MAX_K)."""
    sit = S.sitter_rows(w["played"], w["states"], w["flagged"], w["info"])
    seas = w["games"].select("game_id", "season")
    sit = absence_index(sit, w["games"]).join(seas, on="game_id")
    reg = sit.filter(pl.col("starter10") >= S.REGULAR_STARTER10)
    fresh = reg.filter(pl.col("k") <= MAX_K)
    per_tg = fresh.group_by("game_id", "team_id").agg(pl.len().alias("n_fresh"))
    primary = fresh.join(per_tg.filter(pl.col("n_fresh") == 1), on=["game_id", "team_id"]).drop(
        "n_fresh"
    )
    return reg, primary


def teammate_rows(w: dict[str, Any], ev: pl.DataFrame) -> pl.DataFrame:
    """Scored rows: teammates who PLAYED with >= MIN_PRIOR_PLAYED prior games (production's stat_frame rows)."""
    from nba.props.context_residual import MIN_PRIOR_PLAYED

    p = w["played"].with_columns(pl.Series("n_prior", w["states"]["n_prior"]))
    p = p.filter(pl.col("n_prior") >= MIN_PRIOR_PLAYED).select(
        "game_id", "team_id", "player_id", "season", "game_date"
    )
    e = ev.select("game_id", "team_id", pl.col("player_id").alias("sitter_id"), "k")
    out: pl.DataFrame = p.join(e, on=["game_id", "team_id"]).filter(
        pl.col("player_id") != pl.col("sitter_id")
    )
    return out


def _week(d: pl.Expr) -> pl.Expr:
    return d.dt.strftime("%G-%V")


def count_block(ev: pl.DataFrame, tm: pl.DataFrame, season: int) -> dict[str, Any]:
    e, t = ev.filter(pl.col("season") == season), tm.filter(pl.col("season") == season)
    return {
        "events_team_games": e.height,
        "events_k1": int((e["k"] == 1).sum()),
        "games": e["game_id"].n_unique(),
        "sitters_player_seasons": e["player_id"].n_unique(),
        "teammate_rows": t.height,
        "teammate_rows_k1": int((t["k"] == 1).sum()),
        "teammate_player_seasons": t["player_id"].n_unique(),
        "weeks": t.select(_week(pl.col("game_date"))).n_unique(),
    }


def production_tie(w: dict[str, Any]) -> dict[str, Any]:
    from nba.props.context_residual import vacated_features

    sit = S.sitter_rows(w["played"], w["states"], w["flagged"], w["info"])
    mine = sit.group_by("game_id", "team_id").agg(
        pl.len().alias("n"), pl.col("min10").sum().alias("vm")
    )
    prod = vacated_features(w["played"], w["states"], w["flagged"], w["info"])
    j = prod.join(mine, on=["game_id", "team_id"], how="full", coalesce=True).fill_null(0)
    bad_n = int((j["n"].cast(pl.Int64) != j["n_out_rot"].cast(pl.Int64)).sum())
    bad_v = int(((j["vm"] - j["vac_min"]).abs() > 1e-9).sum())
    return {
        "team_games": j.height,
        "n_mismatch": bad_n,
        "vac_min_mismatch": bad_v,
        "pass": bad_n == bad_v == 0,
    }


def counts(root: Path) -> dict[str, Any]:
    t0 = time.time()
    w = load(root, t0)
    reg, primary = events(w)
    tm = teammate_rows(w, primary)
    _log(
        t0,
        f"regular-starter OUT rows {reg.height}, primary events {primary.height}, teammate rows {tm.height}",
    )
    tm_all = teammate_rows(w, reg)
    out: dict[str, Any] = {
        "rule": "TEAMMATE_SITS (DRAFT)",
        "made_at": dt.datetime.now().isoformat(timespec="seconds"),
        "definitions": {
            "regular_starter": f"as-of starter10 >= {S.REGULAR_STARTER10}",
            "counted": "production vacated_features filters (n_after >= 5, min40 >= 12, team in game)",
            "primary": f"team-games with exactly one regular starter OUT at absence game k <= {MAX_K}",
            "scored_rows": "teammates who played with n_prior >= 5 (same rows for all four stats)",
        },
        "production_tie": production_tie(w),
        "by_season": {},
    }
    for s in SEASONS:
        blk = count_block(primary, tm, s)
        e_all = reg.filter(pl.col("season") == s)
        blk["all_regular_starter_out_rows"] = e_all.height
        blk["all_k_teammate_rows"] = tm_all.filter(pl.col("season") == s).height
        blk["k_distribution_regular"] = {
            "k1": int((e_all["k"] == 1).sum()),
            "k2": int((e_all["k"] == 2).sum()),
            "k3": int((e_all["k"] == 3).sum()),
            "k4_10": int(((e_all["k"] >= 4) & (e_all["k"] <= 10)).sum()),
            "k_gt10": int((e_all["k"] > 10).sum()),
        }
        fresh = e_all.filter(pl.col("k") <= MAX_K)
        multi = fresh.group_by("game_id", "team_id").len().filter(pl.col("len") > 1)
        blk["fresh_team_games_with_2plus_sitters_excluded"] = multi.height
        out["by_season"][str(s)] = blk
    out["export_size_2024"] = export_size(w, REPORT)
    _log(t0, json.dumps(out["by_season"]))
    _log(t0, json.dumps(out["export_size_2024"]))
    return out


def export_size(w: dict[str, Any], season: int) -> dict[str, Any]:
    """Scenario rows per night for the app contract: candidates per team-game (regular starters not OUT, roster =
    that night's played rows, a stand-in for the official roster) and scored teammates per candidate."""
    g = w["games"].filter(pl.col("season") == season)
    ids = [str(x) for x in g["game_id"].to_list() if str(x) in w["flagged"]]
    roster = (
        w["played"].filter(pl.col("game_id").is_in(ids)).select("game_id", "player_id", "team_id")
    )
    cand = S.candidates(
        w["played"], w["states"], roster.select("game_id", "player_id"), w["flagged"], w["info"]
    )
    per_tg = cand.group_by("game_id", "team_id").len()
    tm = roster.group_by("game_id", "team_id").len().rename({"len": "n_roster"})
    j = cand.select("game_id", "team_id").join(tm, on=["game_id", "team_id"])
    per_date = g.filter(pl.col("game_id").is_in(ids)).group_by("game_date").len()
    cand_mean, roster_mean = _num(per_tg["len"].mean()), _num(j["n_roster"].mean())
    date_p50, date_max = _num(per_date["len"].median()), _num(per_date["len"].max())
    rows_per_game = cand_mean * 2 * (roster_mean - 1) * 4
    return {
        "team_games": per_tg.height,
        "candidates_per_team_game_mean": cand_mean,
        "candidates_per_team_game_max": _num(per_tg["len"].max()),
        "teammates_per_candidate_mean": roster_mean - 1,
        "games_per_date_p50": date_p50,
        "games_per_date_max": date_max,
        "rows_per_game_4_stats": rows_per_game,
        "rows_per_night_p50": rows_per_game * date_p50,
        "rows_per_night_max": rows_per_game * date_max,
    }


def size(root: Path) -> dict[str, Any]:  # pragma: no cover - maintainer (heavy)
    import yaml

    from nba.props.context_residual import build_features

    t0 = time.time()
    w = load(root, t0)
    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    feats = build_features(w["games"], w["pgs"], w["static"], w["flagged"], elo)
    _log(t0, f"features {feats.height}")
    return size_core(w, feats, SEASONS, t0)


def size_core(
    w: dict[str, Any], feats: pl.DataFrame, seasons: tuple[int, ...], t0: float
) -> dict[str, Any]:
    """G2 sizing on a loaded world (tests run it on a synthetic league)."""
    from nba.eval.context_residual_eval import crps_from_quantiles
    from nba.props.context_residual import (
        CRPS_TAUS,
        PROP_STATS,
        ContextResidualModel,
        predict_rows,
        stat_feature_names,
        stat_frame,
        to_integer_support,
    )
    from research.eval import prereg_gates_common as C

    _, primary = events(w)
    sitters = primary.select(
        "game_id", pl.col("player_id").alias("sitter_id"), "team_id", "k", "season"
    )
    vac_u = S.scenario_vacancy(
        w["played"],
        w["states"],
        w["flagged"],
        w["info"],
        sitters.select("game_id", "sitter_id").rows(),
        remove=True,
    )
    ev_games = primary["game_id"].unique().to_list()
    base = feats.filter(pl.col("game_id").is_in(ev_games))
    arm_u = S.scenario_features(base, vac_u, sitters.select("game_id", "sitter_id", "team_id"))
    arm_s = (
        sitters.select("game_id", "sitter_id", "team_id")
        .join(base, on=["game_id", "team_id"])
        .filter(pl.col("player_id") != pl.col("sitter_id"))
        .select(arm_u.columns)
    )
    rng = np.random.default_rng(SIZE_SEED)
    res: dict[str, Any] = {"by_season": {}}
    for season in seasons:
        start = w["games"].filter(pl.col("season") == season)["game_date"].min()
        train_all = feats.filter(pl.col("game_date") < start)
        for stat in PROP_STATS:
            m = ContextResidualModel(stat, w["cfg"], stat_feature_names(stat)).fit(
                stat_frame(train_all, stat).sort(["game_date", "game_id", "player_id"])
            )
            models = {stat: m}
            keys = ["game_id", "player_id", "sitter_id"]
            rs, mean_s, qs = predict_rows(
                models, arm_s.filter(pl.col("season") == season), stat, CRPS_TAUS
            )
            ru, mean_u, qu = predict_rows(
                models, arm_u.filter(pl.col("season") == season), stat, CRPS_TAUS
            )
            assert rs.select(keys).equals(ru.select(keys)), "arms must share rows"
            qs_i, qu_i = to_integer_support(qs), to_integer_support(qu)
            idx = rng.integers(0, qs_i.shape[1], len(qs_i))
            y_s = qs_i[np.arange(len(qs_i)), idx]  # oracle world: the scenario's shift is the truth
            y_u = qu_i[np.arange(len(qu_i)), idx]  # null world: the absence moves nothing
            d_or = crps_from_quantiles(qs_i, y_s) - crps_from_quantiles(qu_i, y_s)
            d_nu = crps_from_quantiles(qs_i, y_u) - crps_from_quantiles(qu_i, y_u)
            gain = C.expected_crps_gain(qu_i, qs_i)
            gid = rs["game_id"].to_numpy().astype(str)
            ps = (rs["player_id"].cast(pl.Utf8) + "_" + str(season)).to_numpy()
            ss = (rs["sitter_id"].cast(pl.Utf8) + "_" + str(season)).to_numpy()
            cl = {
                "game": C.cluster_stats(d_or, gid),
                "player_season": C.cluster_stats(d_or, ps),
                "sitter_season": C.cluster_stats(d_or, ss),
            }
            bind = max(cl.values(), key=lambda c: c["deff"])
            delta = mean_s - mean_u
            # slope of (y - mean_U) on delta, sandwich influence function; y from the S grid (oracle world)
            dc = delta - delta.mean()
            e = (y_s - mean_s) - float(np.mean(y_s - mean_s))
            psi = dc * e / max(float((dc**2).mean()), 1e-12)
            slope_se = max(C.cluster_stats(psi, k)["se"] for k in (gid, ps, ss))
            sz = C.sizing_row(
                stat, bind["sd_row"], bind["deff"], bind["n"], FLOOR, float(-gain.mean())
            )
            res["by_season"].setdefault(str(season), {})[stat] = {
                "n": len(d_or),
                "delta_mean": {
                    "mean": float(delta.mean()),
                    "abs_p50": float(np.median(np.abs(delta))),
                    "abs_p90": float(np.quantile(np.abs(delta), 0.9)),
                },
                "oracle_expected_gain": float(-gain.mean()),
                "oracle_sim_mean": float(d_or.mean()),
                "null_sim_mean": float(d_nu.mean()),
                "clusters": cl,
                "slope_se": slope_se,
                "sizing": sz,
                "g2_pass": bool(
                    sz["halfwidth95"] <= HW_BAR
                    and -gain.mean() <= ORACLE_BAR
                    and sz["power_one_season"] >= POWER_BAR
                    and slope_se <= SLOPE_SE_BAR
                ),
            }
            _log(
                t0,
                f"{season} {stat}: n {len(d_or)} gain {-gain.mean():+.5f} hw {res['by_season'][str(season)][stat]['sizing']['halfwidth95']:.5f}",
            )
    return res


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=("counts", "size"))
    ap.add_argument("--data-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    out = counts(a.data_root) if a.stage == "counts" else size(a.data_root)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
