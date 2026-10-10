"""Lower-tail calibration diagnosis (and, after pre-registration, candidate screen) for the
production context_residual props model.

Pre-registration: docs/LOWER_TAIL.md. Read-only on the DuckDBs; refuses season 2025.

    uv run python -m nba.eval.lower_tail_eval collect     # one walk-forward pass, 4 stats
    uv run python -m nba.eval.lower_tail_eval diagnose    # descriptive lower-tail coverage

``collect`` stores, per month block, production's centre/scale for the test rows, the
held-out calibration arrays and the as-of / realised covariates used for the breakdowns.
Everything downstream is a pure function of that file. Rows are PLAYED player-games
(minutes > 0), so every number is conditional on playing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import (
    fit_short_state,
    lower_quantiles,
    predict_pi,
    raw_quantiles,
    short_flag,
)
from nba.props.metrics import paired_score_delta_ci

OUT_DIR = Path("reports/lower_tail")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
COVER_TAUS: tuple[float, ...] = (0.05, 0.10, 0.20)
SHORT_RATIO = 0.5  # "short" game: realised minutes < SHORT_RATIO * projected (min10)
BLOWOUT_MARGIN = 20
ROW_COVARIATES: tuple[str, ...] = (
    "minutes",
    "min10",
    "min40",
    "starter10",
    "starter_act",
    "abs_margin",
    "final_margin",
    "b2b",
    "gap_days",
    "since_flag",
    "n_prior",
    "m",
)


# --------------------------------------------------------------------------- data


def attach_realised(feats: pl.DataFrame, games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """Add realised minutes, actual starter flag and the final |margin| to the feature frame.
    These are used ONLY as labels / diagnostic strata, never as model inputs."""
    g = games.select(
        "game_id", (pl.col("home_pts") - pl.col("away_pts")).abs().alias("final_margin")
    )
    p = pgs.select("game_id", "player_id", "minutes", pl.col("starter").alias("starter_act"))
    out = feats.join(p, on=["game_id", "player_id"], how="left").join(g, on="game_id", how="left")
    return out.with_columns(pl.col("starter_act").fill_null(False).cast(pl.Float64))


def collect_stat(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, np.ndarray]:
    """Walk-forward (month blocks, production protocol) for one stat."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    names = stat_feature_names(stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk", *ROW_COVARIATES)
    }
    cal: dict[str, list[np.ndarray]] = {
        k: [] for k in ("y", "c", "s", "z", "blk", "minutes", "min10", "m")
    }
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        rows["gid"].append(block["game_id"].to_numpy())
        rows["pid"].append(block["player_id"].to_numpy())
        rows["season"].append(block["season"].to_numpy())
        rows["y"].append(block["y"].to_numpy())
        rows["c"].append(c)
        rows["s"].append(s)
        rows["blk"].append(np.full(n, b))
        for k in ROW_COVARIATES:
            rows[k].append(block[k].cast(pl.Float64).to_numpy())
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        cal["y"].append(cal_df["y"].to_numpy())
        cal["c"].append(cal_df["m"].to_numpy() + mu)
        cal["s"].append(sc)
        cal["z"].append((cal_df["resid"].to_numpy() - mu) / sc)
        cal["blk"].append(np.full(cal_df.height, b))
        for k in ("minutes", "min10", "m"):
            cal[k].append(cal_df[k].cast(pl.Float64).to_numpy())
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


def run_collect(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: Path = OUT_DIR,
    stats: tuple[str, ...] = PROP_STATS,
) -> list[Path]:
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo_params)
    feats = attach_realised(feats, games, pgs)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for stat in stats:
        comp = collect_stat(feats, stat, cfg)
        path = out_dir / f"components_{stat}.npz"
        np.savez_compressed(path, **comp)  # type: ignore[arg-type]
        paths.append(path)
    return paths


def load_components(stat: str, out_dir: Path = OUT_DIR) -> dict[str, np.ndarray]:
    z = np.load(out_dir / f"components_{stat}.npz", allow_pickle=True)
    return {k: z[k] for k in z.files}


# --------------------------------------------------------------------------- production grid


def production_grid(comp: dict[str, np.ndarray], taus: np.ndarray = CRPS_TAUS) -> np.ndarray:
    """Production quantiles ``[n_rows, len(taus)]`` (continuous; not integer-mapped)."""
    out = np.empty((len(comp["row_y"]), len(taus)))
    for b in np.unique(comp["row_blk"]):
        r = comp["row_blk"] == b
        z = comp["cal_z"][comp["cal_blk"] == b]
        out[r] = raw_quantiles(comp["row_c"][r], comp["row_s"][r], z, taus)
    return out


def randomized_pit(q: np.ndarray, y: np.ndarray, seed: int = 0) -> np.ndarray:
    """Continuity-corrected randomized PIT on an equal-mass quantile grid (A1.1): integer
    outcome ``y`` occupies ``[y - 0.5, y + 0.5]``; the PIT is uniform within that mass."""
    rng = np.random.default_rng(seed)
    f_lo = (q < (y - 0.5)[:, None]).mean(axis=1)
    f_hi = (q <= (y + 0.5)[:, None]).mean(axis=1)
    return np.asarray(f_lo + rng.random(len(y)) * (f_hi - f_lo))


def coverage_row(q: np.ndarray, y: np.ndarray, mask: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """Naive P(y <= q_tau) and continuity-corrected P(PIT <= tau) on the masked rows."""
    n = int(mask.sum())
    out: dict[str, Any] = {"n": n}
    if n == 0:
        return out
    qm, ym = q[mask], y[mask]
    pit = randomized_pit(qm, ym, seed)
    for t in COVER_TAUS:
        i = int(np.argmin(np.abs(CRPS_TAUS - t)))
        out[f"naive_{t}"] = float((ym <= qm[:, i]).mean())
        out[f"pit_{t}"] = float((pit <= t).mean())
    return out


def strata(comp: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    mins, m10 = comp["row_minutes"], comp["row_min10"]
    short = mins < SHORT_RATIO * m10
    return {
        "all": np.ones(len(mins), dtype=bool),
        "min<10": mins < 10,
        "min10-20": (mins >= 10) & (mins <= 20),
        "min>20": mins > 20,
        "short(<0.5*proj)": short,
        "not short": ~short,
        "blowout(|m|>=20)": comp["row_final_margin"] >= BLOWOUT_MARGIN,
        "no blowout": comp["row_final_margin"] < BLOWOUT_MARGIN,
        "starter(actual)": comp["row_starter_act"] > 0.5,
        "bench(actual)": comp["row_starter_act"] <= 0.5,
        "proj<15": m10 < 15,
        "proj15-25": (m10 >= 15) & (m10 <= 25),
        "proj>25": m10 > 25,
        "b2b": comp["row_b2b"] > 0.5,
        "gap>=10d": comp["row_gap_days"] >= 10,
    }


def diagnose_stat(comp: dict[str, np.ndarray]) -> dict[str, Any]:
    q = production_grid(comp)
    y = comp["row_y"]
    out: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        sm = comp["row_season"] == season
        res: dict[str, Any] = {}
        for name, m in strata(comp).items():
            res[name] = coverage_row(q, y, sm & m)
        short = strata(comp)["short(<0.5*proj)"]
        res["share_short"] = float((sm & short).sum() / sm.sum())
        # how much of the PIT<=0.10 excess is carried by short games
        pit = randomized_pit(q[sm], y[sm])
        low = pit <= 0.10
        res["share_of_low10_from_short"] = float((low & short[sm]).sum() / max(low.sum(), 1))
        out[str(season)] = res
    return out


# --------------------------------------------------------------------------- pi / short state


def collect_pi(
    feats: pl.DataFrame,
    cfg: ContextResidualConfig,
    stats: tuple[str, ...] = PROP_STATS,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, np.ndarray]:
    """Pre-tip short-minutes probability per test row and per-stat short-ratio quantiles, from a
    walk-forward pass with the same block structure as :func:`collect_stat` (rows align 1:1 with the
    ``components_<stat>.npz`` rows). One classifier per block is shared across stats."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    frames = {st: stat_frame(feats, st) for st in stats}
    sf0 = frames[stats[0]]
    test_all = sf0.filter(pl.col("season").is_in(list(test_seasons)))
    gid: list[np.ndarray] = []
    pid: list[np.ndarray] = []
    pi: list[np.ndarray] = []
    w_q: dict[str, list[np.ndarray]] = {st: [] for st in stats}
    params = _params(cfg, "binary")
    xcols = list(COMMON_FEATURES)
    for start, end in month_blocks(test_all):
        sel = (pl.col("game_date") >= start) & (pl.col("game_date") < end)
        block = sf0.filter(sel).filter(pl.col("season").is_in(list(test_seasons)))
        train0 = sf0.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train0.height < 1500:
            continue
        short = short_flag(train0["minutes"].to_numpy(), train0["min10"].to_numpy())
        x_tr = _matrix(train0, xcols)
        clf = None
        for st in stats:
            tr = frames[st].filter(pl.col("game_date") < start)
            tr = tr.sort(["game_date", "game_id", "player_id"])
            state = fit_short_state(
                x_tr, short, tr["y"].to_numpy(), tr["m"].to_numpy(), CRPS_TAUS, params, clf=clf
            )
            clf = state["pi_model"]
            w_q[st].append(
                np.full(len(CRPS_TAUS), np.nan) if state["w_q"] is None else state["w_q"]
            )
        gid.append(block["game_id"].to_numpy())
        pid.append(block["player_id"].to_numpy())
        pi.append(predict_pi({"pi_model": clf}, _matrix(block, xcols)))
    out = {
        "row_gid": np.concatenate(gid),
        "row_pid": np.concatenate(pid),
        "row_pi": np.concatenate(pi),
    }
    out.update({f"wq_{st}": np.vstack(v) for st, v in w_q.items()})
    return out


def run_collect_pi(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: Path = OUT_DIR,
) -> Path:
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo_params), games, pgs)
    comp = collect_pi(feats, cfg)
    path = out_dir / "components_pi.npz"
    np.savez_compressed(path, **comp)  # type: ignore[arg-type]
    return path


# --------------------------------------------------------------------------- candidate arms

CANDIDATES: tuple[str, ...] = ("mixture", "mondrian_centre", "mondrian_minutes")
LOW_THRESHOLDS: dict[str, tuple[int, int]] = {
    "pts": (10, 15),
    "reb": (4, 6),
    "ast": (2, 4),
    "fg3m": (1, 2),
}
GUARD_TOL = 0.02
GUARD_TAUS: tuple[float, ...] = (0.05, 0.10, 0.20, 0.80, 0.90, 0.95)
REQUIRED_GUARD_TAUS: tuple[float, ...] = (0.10, 0.20, 0.80, 0.90, 0.95)
CRPS_FLOOR = -0.005
N_BOOT = 2000
EPS = 0.5 / 199


def arm_grid(
    kind: str, comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], stat: str
) -> np.ndarray:
    """Continuous 199-grid of arm ``kind`` for every row (block-wise)."""
    out = np.empty((len(comp["row_y"]), len(CRPS_TAUS)))
    wq_all = pic[f"wq_{stat}"]
    blocks = np.unique(comp["row_blk"])
    for i, b in enumerate(blocks):
        r = comp["row_blk"] == b
        cm = comp["cal_blk"] == b
        w = wq_all[i]
        out[r] = lower_quantiles(
            kind,
            z_cal=comp["cal_z"][cm],
            c_cal=comp["cal_c"][cm],
            min10_cal=comp["cal_min10"][cm],
            short_cal=short_flag(comp["cal_minutes"][cm], comp["cal_min10"][cm]),
            c=comp["row_c"][r],
            s=comp["row_s"][r],
            m_row=comp["row_m"][r],
            min10_row=comp["row_min10"][r],
            pi=pic["row_pi"][r],
            w_q=None if np.isnan(w).any() else w,
            taus=CRPS_TAUS,
        )
    return out


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def arm_scores(q: np.ndarray, y: np.ndarray, stat: str) -> dict[str, Any]:
    """Per-row scores on integer support (primary) with continuous-support and coverage extras."""
    qi = to_integer_support(q)
    thr = LOW_THRESHOLDS[stat]
    ll = np.mean([_ll((qi >= n).mean(axis=1), y >= n) for n in thr], axis=0)
    pit = randomized_pit(q, y)
    pit_i = randomized_pit(qi, y)
    return {
        "crps_int": crps_from_quantiles(qi, y),
        "crps_cont": crps_from_quantiles(q, y),
        "tll_low": ll,
        "pit": pit,
        "pit_int": pit_i,
    }


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def guard(pit: np.ndarray) -> dict[str, Any]:
    cov = {str(t): float((pit <= t).mean()) for t in GUARD_TAUS}
    ok = all(abs(cov[str(t)] - t) <= GUARD_TOL for t in REQUIRED_GUARD_TAUS)
    return {"cover": cov, "ok": bool(ok)}


def screen_stat(
    stat: str,
    comp: dict[str, np.ndarray],
    pic: dict[str, np.ndarray],
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    if not (
        np.array_equal(comp["row_gid"], pic["row_gid"])
        and np.array_equal(comp["row_pid"], pic["row_pid"])
    ):
        raise ValueError("pi components are not row-aligned with the stat components")
    y_all = comp["row_y"]
    grids = {k: arm_grid(k, comp, pic, stat) for k in ("off", *CANDIDATES)}
    st = strata(comp)
    res: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        sm = comp["row_season"] == season
        gid, y = comp["row_gid"][sm], y_all[sm]
        sc = {k: arm_scores(g[sm], y, stat) for k, g in grids.items()}
        base = sc["off"]
        season_res: dict[str, Any] = {
            "off": {
                "n": int(sm.sum()),
                "n_games": int(len(np.unique(gid))),
                "crps_int": float(base["crps_int"].mean()),
                "crps_cont": float(base["crps_cont"].mean()),
                "tll_low": float(base["tll_low"].mean()),
                "guard": guard(base["pit"]),
                "guard_int": guard(base["pit_int"]),
            }
        }
        for k in CANDIDATES:
            d_crps = sc[k]["crps_int"] - base["crps_int"]
            d_tll = sc[k]["tll_low"] - base["tll_low"]
            d_cont = sc[k]["crps_cont"] - base["crps_cont"]
            by = {
                name: float(d_crps[mm[sm]].mean())
                for name, mm in st.items()
                if name in ("short(<0.5*proj)", "not short", "proj<15", "proj15-25", "proj>25")
            }
            season_res[k] = {
                "crps_int": float(sc[k]["crps_int"].mean()),
                "d_crps_int": _ci(d_crps, gid, n_boot),
                "p": boot_p_value(d_crps, gid, n_boot),
                "d_crps_cont": _ci(d_cont, gid, n_boot),
                "d_tll_low": _ci(d_tll, gid, n_boot),
                "guard": guard(sc[k]["pit"]),
                "guard_int": guard(sc[k]["pit_int"]),
                "d_crps_int_by_stratum": by,
                "pi_mean": float(pic["row_pi"][sm].mean()) if k == "mixture" else None,
            }
        res[str(season)] = season_res
    return res


def candidate_screen(n_boot: int = N_BOOT, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """12 (candidate x stat) tests per season; BH over them; apply the pre-registered pass rule."""
    pic = {k: v for k, v in np.load(out_dir / "components_pi.npz", allow_pickle=True).items()}
    res: dict[str, Any] = {
        s: screen_stat(s, load_components(s, out_dir), pic, n_boot) for s in PROP_STATS
    }
    for season in (SELECT_SEASON, REPORT_SEASON):
        keys = [(s, k) for s in PROP_STATS for k in CANDIDATES]
        adj = bh_adjust(np.array([res[s][str(season)][k]["p"] for s, k in keys]))
        for (s, k), a in zip(keys, adj, strict=True):
            r = res[s][str(season)][k]
            r["p_bh"] = float(a)
            r["checks"] = {
                "crps_floor": r["d_crps_int"]["point"] <= CRPS_FLOOR,
                "ci_below_0": r["d_crps_int"]["hi"] < 0.0,
                "bh_p": r["p_bh"] < 0.05,
                "guard": r["guard"]["ok"],
            }
            r["pass"] = all(r["checks"].values())
    verdict: dict[str, Any] = {}
    for s in PROP_STATS:
        sel = [k for k in CANDIDATES if res[s][str(SELECT_SEASON)][k]["pass"]]
        chosen = (
            min(sel, key=lambda k: res[s][str(SELECT_SEASON)][k]["d_crps_int"]["point"])
            if sel
            else None
        )
        verdict[s] = {
            "selected_on_2023": chosen,
            "confirmed_on_2024": bool(chosen and res[s][str(REPORT_SEASON)][chosen]["pass"]),
        }
    res["verdict"] = verdict
    return res


def render_report(out_dir: Path = OUT_DIR) -> str:
    """Markdown tables (diagnosis + candidate screen) from the stored JSON results."""
    diag = json.loads((out_dir / "diagnose.json").read_text())
    cand = json.loads((out_dir / "candidates.json").read_text())
    ln = ["# Lower-tail calibration of production props (docs/LOWER_TAIL.md)", ""]
    ln += [
        "## Production diagnosis (PIT = continuity-corrected randomized; nominal .05/.10/.20)",
        "",
    ]
    ln += [
        "| stat | season | stratum | n | naive .05/.10/.20 | PIT .05/.10/.20 |",
        "|---|---|---|---|---|---|",
    ]
    for st_name in PROP_STATS:
        for se in ("2023", "2024"):
            for name in ("all", "not short", "short(<0.5*proj)", "min<10", "min10-20", "min>20"):
                v = diag[st_name][se][name]
                ln.append(
                    f"| {st_name} | {se} | {name} | {v['n']} | "
                    f"{v['naive_0.05']:.3f}/{v['naive_0.1']:.3f}/{v['naive_0.2']:.3f} | "
                    f"{v['pit_0.05']:.3f}/{v['pit_0.1']:.3f}/{v['pit_0.2']:.3f} |"
                )
    ln += [
        "",
        "## Candidates (dCRPS = candidate - production, integer support; lower is better)",
        "",
    ]
    ln += [
        "| stat | season | arm | dCRPS [95% game-clustered CI] | p (BH, 12) | dTLL low thr [CI] "
        "| PIT .10/.20 | guard | pass |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for st_name in PROP_STATS:
        for se in ("2023", "2024"):
            o = cand[st_name][se]["off"]
            ln.append(
                f"| {st_name} | {se} | production (CRPS {o['crps_int']:.4f}, n={o['n']}) | | | | "
                f"{o['guard']['cover']['0.1']:.3f}/{o['guard']['cover']['0.2']:.3f} | "
                f"{'ok' if o['guard']['ok'] else 'FAIL'} | |"
            )
            for k in CANDIDATES:
                r = cand[st_name][se][k]
                d, t = r["d_crps_int"], r["d_tll_low"]
                ln.append(
                    f"| {st_name} | {se} | {k} | "
                    f"{d['point']:+.4f} [{d['lo']:+.4f},{d['hi']:+.4f}] | "
                    f"{r['p_bh']:.3f} | {t['point']:+.4f} [{t['lo']:+.4f},{t['hi']:+.4f}] | "
                    f"{r['guard']['cover']['0.1']:.3f}/{r['guard']['cover']['0.2']:.3f} | "
                    f"{'ok' if r['guard']['ok'] else 'FAIL'} | {'PASS' if r['pass'] else 'no'} |"
                )
    ln += ["", "## Verdict", "", "```", json.dumps(cand["verdict"], indent=1), "```", ""]
    return "\n".join(ln)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("collect", "diagnose", "collect_pi", "candidates", "report"))
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.stage == "collect":
        for p in run_collect(a.db_path, out_dir=out):
            print(p)
        return 0
    if a.stage == "collect_pi":
        print(run_collect_pi(a.db_path, out_dir=out))
        return 0
    if a.stage == "candidates":
        res = candidate_screen(out_dir=out)
        (out / "candidates.json").write_text(json.dumps(res, indent=2))
        print(json.dumps(res["verdict"], indent=1))
        return 0
    if a.stage == "report":
        Path("reports/lower_tail.md").write_text(render_report(out))
        print("wrote reports/lower_tail.md")
        return 0
    res = {s: diagnose_stat(load_components(s, out)) for s in PROP_STATS}
    (out / "diagnose.json").write_text(json.dumps(res, indent=2))
    print("wrote", out / "diagnose.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
