# ruff: noqa: E501
"""INJURY_TIMING harness, rev 3 (rule: docs/prereg/INJURY_TIMING.md; DRAFT until frozen and pinned).

    check     label-free preflight: versions, scratch-db pin, G0 header audit, gate, planted future, list fingerprints
    rehearse  the whole scoring and decision machinery on SYNTHETIC outcomes (no real label, no fit)
    run       refuses unless the rule is frozen (prereg commit) and this harness is pinned (harness commit)

Real run (maintainer, from the main checkout so ``configs/`` resolve; wrap in the heavy lock):
    mkdir data/ops/heavy.lock && .venv/bin/python -W ignore -m research.eval.injury_timing_eval run --sha <64 hex> \
        --repaired /Users/devin/Downloads/nba-prediction/data/scratch/injury_intraday_v2.duckdb ; rmdir data/ops/heavy.lock

Order enforced in code for ``run``: (1) require_ready(sha); (2) preflight, label-free (a failing preflight writes ``GUARD_FAIL`` and stops);
(3) refit A0 for the four stats -> must reproduce the pinned integer CRPS (G4), or the result is ``GUARD_FAIL`` and S, M, P are never fitted;
(4) arms S, M, P; (5) score, words. Season 2025 is never queried; ``collect_cross`` raises on a later test season.
Arms (all test blocks are scored on the SAME rows and the live-cutoff list R; they differ in the list used to FIT):
A0 = fit on the stored lists, test on the stored lists (production as backtested); S = fit stored, test R (live analog);
M = fit R, test R (the fix); P = fit on R cut at each game's corrected stored stamp (stored freshness, intraday parse), test R (placebo).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata as md
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import norm

from research.eval import injury_timing_gate as gate
from research.eval import injury_timing_rule as rule

STATS = ("pts", "reb", "ast", "fg3m")
SELECT, REPORT = 2023, 2024
SEASONS = (SELECT, REPORT)
MAX_SEASON = 2024
FLOOR = -0.005  # practical floor, integer CRPS, per stat (the project's floor)
HARM = 0.001  # P2: upper CI of M - S must not exceed this in any stat (2024)
N_BOOT = 2000
SEED = 2026
ALPHA = 0.05
G4_TOL = 1e-6  # refit of A0 vs the pinned stored value (LightGBM refit determinism is NOT assumed to be 1e-9)
#: A0 integer-support CRPS of the stored components (2026-10-11), as pinned in docs/prereg/MEDIAN_BIAS.md section 6
A0_PINS: dict[str, dict[str, float]] = {
    "pts": {"2023": 3.152830, "2024": 3.187553},
    "reb": {"2023": 1.292817, "2024": 1.314247},
    "ast": {"2023": 0.905903, "2024": 0.900215},
    "fg3m": {"2023": 0.569049, "2024": 0.592213},
}
G4_PIN_DIGITS = 6  # the pins above carry six decimals
OUT_REAL = Path("reports/injury_timing")
OUT_REHEARSAL = Path("reports/prereg_injury_timing/rehearsal")
TRUTHS = ("null", "fix", "harm", "partial", "placebo")
PINNED_VERSIONS = {
    "lightgbm": "4.7.0",
    "polars": "1.44.2",
    "numpy": "2.5.3",
    "scipy": "1.18.1",
    "duckdb": "1.5.6",
}
ARMS = ("A0", "S", "M", "P")
SCRATCH_REL = "data/scratch/injury_intraday_v2.duckdb"


def _log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- statistics


def clustered_delta(
    delta: np.ndarray, gid: np.ndarray, seed: int, n_boot: int = N_BOOT
) -> dict[str, float]:
    """Mean of ``delta`` with a game-clustered percentile bootstrap CI and the one-sided p of H1: mean < 0
    (ratio of resampled per-game sums; ``p = (1 + #{boot mean >= 0}) / (n_boot + 1)``)."""
    ug, inv = np.unique(gid, return_inverse=True)
    n = len(ug)
    s = np.bincount(inv, weights=delta, minlength=n)
    c = np.bincount(inv, minlength=n).astype(float)
    rng = np.random.default_rng(seed)
    ii = rng.integers(0, n, size=(n_boot, n))
    m = s[ii].sum(axis=1) / c[ii].sum(axis=1)
    return {
        "point": float(s.sum() / c.sum()),
        "lo": float(np.percentile(m, 2.5)),
        "hi": float(np.percentile(m, 97.5)),
        "p_lt0": float((1 + np.sum(m >= 0.0)) / (n_boot + 1)),
        "n": int(len(delta)),
        "n_games": int(n),
    }


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def crps_int(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Integer-support CRPS per row of a 199-point quantile grid (the production map ceil(q - 0.5), floored at 0)."""
    from nba.props.context_residual import CRPS_TAUS, to_integer_support

    qi = to_integer_support(q)
    d = y[:, None] - qi
    pin = np.where(d >= 0, CRPS_TAUS[None, :] * d, (CRPS_TAUS[None, :] - 1.0) * d)
    return np.asarray(2.0 * pin.mean(axis=1))


# --------------------------------------------------------------------------- scoring and words


def score(arms: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """``arms[season][stat] = {"gid": ..., "A0"|"S"|"M"|"P": per-row integer CRPS, same row order}``."""
    out: dict[str, Any] = {}
    for season, by_stat in arms.items():
        out[season] = {}
        for k, stat in enumerate(STATS):
            a = by_stat[stat]
            seed = (
                SEED + 10 * int(season) + k
            )  # one fixed seed per (season, stat): all contrasts share the resample
            out[season][stat] = {
                "n": int(len(a["gid"])),
                "crps": {arm: float(np.mean(a[arm])) for arm in ARMS},
                "M_minus_S": clustered_delta(a["M"] - a["S"], a["gid"], seed),
                "M_minus_P": clustered_delta(a["M"] - a["P"], a["gid"], seed),
                "P_minus_S": clustered_delta(a["P"] - a["S"], a["gid"], seed),
                "M_minus_A0": clustered_delta(a["M"] - a["A0"], a["gid"], seed),
                "S_minus_A0": clustered_delta(a["S"] - a["A0"], a["gid"], seed),
            }
    return out


def words(sc: dict[str, Any], guards_pass: bool, reason: str | None = None) -> dict[str, Any]:
    """The decision rule of docs/prereg/INJURY_TIMING.md section 4, implemented once. 2024 decides; 2023 is reported, in no word."""
    rep, sel = sc[str(REPORT)], sc[str(SELECT)]
    q_s = bh_adjust(np.array([rep[s]["M_minus_S"]["p_lt0"] for s in STATS]))
    q_p = bh_adjust(np.array([rep[s]["M_minus_P"]["p_lt0"] for s in STATS]))
    p1s: dict[str, bool] = {}
    p1p: dict[str, bool] = {}
    p1: dict[str, bool] = {}
    p2: dict[str, bool] = {}
    for i, s in enumerate(STATS):
        a, b = rep[s]["M_minus_S"], rep[s]["M_minus_P"]
        p1s[s] = bool(a["point"] <= FLOOR and a["hi"] < 0.0 and q_s[i] <= ALPHA)
        p1p[s] = bool(b["point"] <= FLOOR and b["hi"] < 0.0 and q_p[i] <= ALPHA)
        p1[s] = p1s[s] and p1p[s]
        p2[s] = bool(a["hi"] <= HARM)
    n_pass = sum(p1.values())
    no_harm = all(p2.values())
    if not guards_pass:
        word = "GUARD_FAIL"
    elif n_pass >= 2 and no_harm:
        word = "FIX_WORTH_IT"
    elif n_pass == 1 and no_harm:
        word = "PARTIAL"
    else:
        word = "NULL"
    flags = {
        "SKEW_HARMS_LIVE_ANALOG": bool(all(rep[s]["S_minus_A0"]["lo"] > 0.0 for s in STATS)),
        "FRESHER_REPORT_HELPS_ALREADY": bool(
            sum(rep[s]["S_minus_A0"]["hi"] < 0.0 for s in STATS) >= 2
        ),
        "INFO_GAIN_M_VS_A0": bool(sum(rep[s]["M_minus_A0"]["hi"] < FLOOR for s in STATS) >= 2),
        "CRPS_HARM": bool(not no_harm),
        "PLACEBO_EXPLAINS_GAIN": bool(any(p1s[s] and not p1p[s] for s in STATS)),
    }
    return {
        "word": word, "reason": reason, "n_pass": n_pass, "P1_report_2024": p1, "P1_vs_S": p1s, "P1_vs_P": p1p, "P2_no_harm_2024": p2,
        "bh_q_2024_vs_S": {s: float(q_s[i]) for i, s in enumerate(STATS)}, "bh_q_2024_vs_P": {s: float(q_p[i]) for i, s in enumerate(STATS)},
        "reported_2023_M_minus_S_point_lt0": {s: bool(sel[s]["M_minus_S"]["point"] < 0.0) for s in STATS}, "flags": flags,
    }  # fmt: skip


def guard_fail(reason: str) -> dict[str, Any]:
    """A failed preflight or G4 is an outcome, not a crash: the declared word with its reason (no outcome was scored by then)."""
    return {"word": "GUARD_FAIL", "reason": reason}


# --------------------------------------------------------------------------- synthetic rehearsal

#: (A0, S, M, P) centre offsets in units of sd / 2, per truth; "partial" applies the "fix" offsets to pts only
SHIFTS = {
    "null": (0.0, 0.0, 0.0, 0.0),
    "fix": (0.5, 1.0, 0.0, 1.0),
    "harm": (0.0, 0.0, 1.0, 0.0),
    "partial": (0.5, 1.0, 0.0, 1.0),
    "placebo": (
        0.5,
        1.0,
        0.0,
        0.0,
    ),  # the gain is explained by the intraday parse alone: P is as good as M
}


def synthetic_arms(
    truth: str, n_games: int = 400, per_game: int = 14, seed: int = 0
) -> dict[str, Any]:
    """Quantile grids built directly (no fit, no real label): outcomes Poisson(lam); the truth sets which arm's centre is off."""
    from nba.props.context_residual import CRPS_TAUS

    z = norm.ppf(CRPS_TAUS)
    out: dict[str, Any] = {}
    for si, season in enumerate(SEASONS):
        out[str(season)] = {}
        for k, stat in enumerate(STATS):
            rng = np.random.default_rng(seed + 1000 * si + 10 * k)
            n = n_games * per_game
            gid = np.repeat(np.arange(n_games), per_game).astype(str)
            lam = rng.gamma(4.0, 2.0, n) * (3.0 if stat == "pts" else 1.0)
            y = rng.poisson(lam).astype(float)
            sd = np.sqrt(lam) + 0.5
            shift = SHIFTS["null"] if (truth == "partial" and stat != "pts") else SHIFTS[truth]
            arm = {}
            for name, d in zip(ARMS, shift, strict=True):
                q = (lam + d * 0.5 * sd)[:, None] + sd[:, None] * z[None, :]
                arm[name] = crps_int(q, y)
            out[str(season)][stat] = {"gid": gid, **arm}
    return out


# --------------------------------------------------------------------------- planted future


def planted_future_check(
    rows: pl.DataFrame, tips: dict[str, dt.datetime], n_games: int = 50, seed: int = SEED
) -> dict[str, Any]:
    """On the header-stamped rows R: plant a fresh OUT row stamped 1 second AFTER the live cutoff in sampled games; the used OUT set must not
    change in any game. Plant one stamped exactly AT the cutoff: it must enter in every game (the test has power)."""
    cut = {g: gate.live_cutoff(tips[g]) for g in rows["game_id"].unique().to_list() if g in tips}
    base, _ = gate.lists(rows, cut)
    rng = np.random.default_rng(seed)
    gids = sorted(base)
    pick = [gids[i] for i in rng.choice(len(gids), size=min(n_games, len(gids)), replace=False)]

    def planted(delta: dt.timedelta) -> pl.DataFrame:
        extra = pl.DataFrame(
            {
                "game_id": pick,
                "player_id": [-999] * len(pick),
                "status": ["out"] * len(pick),
                "as_of": [cut[g] + delta for g in pick],
                "season": [2024] * len(pick),
            },
            schema=gate.SCHEMA,
        )
        return pl.concat([rows.select(list(gate.SCHEMA)), extra])

    late, _ = gate.lists(planted(dt.timedelta(seconds=1)), cut)
    edge, _ = gate.lists(planted(dt.timedelta(0)), cut)
    leaked = [g for g in pick if late.get(g) != base.get(g)]
    detected = [g for g in pick if -999 in edge.get(g, set())]
    # independent invariant (does not call the cutoff function): no used stamp may lie after tip - 60 minutes by more than the live slack allows
    _, used = gate.lists(rows, cut)
    after_t60 = [g for g, t in used.items() if t > gate.prod_cutoff(tips[g])]
    return {
        "games_planted": len(pick),
        "leaked_after_cutoff": len(leaked),
        "detected_at_cutoff": len(detected),
        "used_after_tip_minus_60": len(after_t60),
        "pass": bool(len(leaked) == 0 and len(detected) == len(pick) and not after_t60),
    }


# --------------------------------------------------------------------------- real fits (maintainer's command)


def collect_cross(
    train_feats: pl.DataFrame,
    test_feats: pl.DataFrame,
    stat: str,
    cfg: Any,
    test_seasons: tuple[int, ...] = SEASONS,
) -> dict[str, np.ndarray]:
    """Production walk-forward (month blocks) with the model FIT on ``train_feats`` rows before the block and PREDICTING the block
    from ``test_feats`` (same rows, other report snapshot). ``train_feats is test_feats`` reproduces ``lower_tail_eval.collect_stat``."""
    from nba.eval.context_residual_eval import month_blocks
    from nba.props.context_residual import (
        ContextResidualModel,
        split_calibration,
        stat_feature_names,
        stat_frame,
    )

    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf_tr, sf_te = stat_frame(train_feats, stat), stat_frame(test_feats, stat)
    key = ["game_id", "player_id"]
    if not sf_tr.select(key).sort(key).equals(sf_te.select(key).sort(key)):
        raise RuntimeError("the two arms do not score the same rows (G3)")
    names = stat_feature_names(stat)
    test_all = sf_te.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk")
    }
    cal: dict[str, list[np.ndarray]] = {"z": [], "blk": []}
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf_te.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf_tr.filter(pl.col("game_date") < start).sort(
            ["game_date", "game_id", "player_id"]
        )
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        for k, v in (
            ("gid", block["game_id"].to_numpy()),
            ("pid", block["player_id"].to_numpy()),
            ("season", block["season"].to_numpy()),
            ("y", block["y"].to_numpy()),
            ("c", c),
            ("s", s),
            ("blk", np.full(n, b)),
        ):
            rows[k].append(v)
        _, cal_df = split_calibration(train, cfg)
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))  # type: ignore[union-attr]
        sc_ = model._scale(xc)
        cal["z"].append((cal_df["resid"].to_numpy() - mu) / sc_)
        cal["blk"].append(np.full(cal_df.height, b))
        _log(f"{stat} block {b}: {n} test rows, {train.height} train rows")
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


def arm_crps(comp: dict[str, np.ndarray]) -> np.ndarray:
    from nba.eval.lower_tail_eval import production_grid

    q = production_grid(
        {
            "row_y": comp["row_y"],
            "row_blk": comp["row_blk"],
            "cal_blk": comp["cal_blk"],
            "cal_z": comp["cal_z"],
            "row_c": comp["row_c"],
            "row_s": comp["row_s"],
        }
    )
    return crps_int(q, comp["row_y"])


def build_frames(data_root: Path, built: dict[str, Any]) -> tuple[dict[str, pl.DataFrame], Any]:
    """Feature frames for the three fit lists (stored S, live R, placebo P). Same function for all; only ``flagged`` differs. Read-only."""
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import ContextResidualConfig, build_features

    elo = {
        k: float(v)
        for k, v in yaml.safe_load((data_root / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load((data_root / "configs/context_residual.yaml").read_text()).get("model", {})
    )
    games, pgs, static, _avail = load_inputs(str(data_root / "nba.duckdb"), None)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    frames = {}
    for name, key in (("S", "S"), ("R", "R_live"), ("P", "P")):
        frames[name] = attach_realised(
            build_features(games, pgs, static, built[key][0], elo), games, pgs
        )
    return frames, cfg


def g4_a0_repro(crps_by_season: dict[str, dict[str, float]]) -> dict[str, Any]:
    bad = {}
    for st in STATS:
        for s in map(str, SEASONS):
            d = abs(round(crps_by_season[st][s], G4_PIN_DIGITS) - A0_PINS[st][s])
            if d > G4_TOL:
                bad[f"{st}_{s}"] = d
    return {"mismatches": bad, "pass": not bad}


def run_real(data_root: Path, repaired_db: Path, out: Path) -> dict[str, Any]:
    built = gate.build_all(data_root, repaired_db)
    fr, cfg = build_frames(data_root, built)
    out.mkdir(parents=True, exist_ok=True)
    per_stat: dict[str, dict[str, Any]] = {}
    a0_means: dict[str, dict[str, float]] = {}
    # G4 first: A0 for all four stats; stop before S, M or P is fitted on a mismatch
    for st in STATS:
        comp = collect_cross(fr["S"], fr["S"], st, cfg)
        c = arm_crps(comp)
        per_stat[st] = {
            "gid": comp["row_gid"],
            "pid": comp["row_pid"],
            "season": comp["row_season"],
            "A0": c,
        }
        a0_means[st] = {str(s): float(c[comp["row_season"] == s].mean()) for s in SEASONS}
        np.savez_compressed(
            out / f"a0_{st}.npz",
            gid=comp["row_gid"],
            pid=comp["row_pid"],
            season=comp["row_season"],
            crps=c,
        )
    g4 = g4_a0_repro(a0_means)
    if not g4["pass"]:
        return {
            "g4": g4,
            "decision": guard_fail(
                f"G4: A0 refit does not reproduce the pinned CRPS {g4['mismatches']}; S, M, P were never fitted"
            ),
        }
    _log("G4 holds: A0 refit reproduces the pinned integer CRPS")
    for st in STATS:
        for arm, (tr, te) in (("S", ("S", "R")), ("M", ("R", "R")), ("P", ("P", "R"))):
            comp = collect_cross(fr[tr], fr[te], st, cfg)
            if (
                not (comp["row_gid"] == per_stat[st]["gid"]).all()
                or not (comp["row_pid"] == per_stat[st]["pid"]).all()
            ):
                raise RuntimeError("G3: arm rows differ from A0 rows")
            per_stat[st][arm] = arm_crps(comp)
        np.savez_compressed(
            out / f"arms_{st}.npz",
            gid=per_stat[st]["gid"],
            season=per_stat[st]["season"],
            **{a: per_stat[st][a] for a in ARMS},
        )
        _log(f"{st}: arms S, M, P scored (checkpoint written)")
    arms: dict[str, Any] = {str(s): {} for s in SEASONS}
    for st in STATS:
        for s in SEASONS:
            m = per_stat[st]["season"] == s
            arms[str(s)][st] = {
                "gid": per_stat[st]["gid"][m],
                **{a: per_stat[st][a][m] for a in ARMS},
            }
    sc = score(arms)
    return {"score": sc, "g4": g4, "decision": words(sc, True)}


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def versions() -> dict[str, str]:
    return {k: md.version(k) for k in PINNED_VERSIONS}


def g0_header_audit(scratch: Path) -> dict[str, Any]:
    """G0: every re-parsed report is stamped with the time printed in its PDF header (stored stamp == header_dt == filename + 30 min for
    hour-only files, + 0 for HH_MM files); no error rows; every STORED PDF dated <= 2024-06-30 audited with the same convention."""
    import duckdb

    con = duckdb.connect(str(scratch), read_only=True)
    try:
        n_ok, n_err, n_bad = con.execute(
            "SELECT count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status <> 'ok'), "
            "count(*) FILTER (WHERE status = 'ok' AND (report_dt <> header_dt OR date_diff('minute', filename_dt, header_dt) <> CASE WHEN form = 'hour_only' THEN 30 ELSE 0 END)) "
            "FROM injury_intraday_reports"
        ).fetchone()  # type: ignore[misc]
        s_ok, s_err, s_bad = con.execute(
            "SELECT count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status <> 'ok'), "
            "count(*) FILTER (WHERE status = 'ok' AND date_diff('minute', filename_dt, header_dt) <> CASE WHEN form = 'hour_only' THEN 30 ELSE 0 END) "
            "FROM stored_report_headers"
        ).fetchone()  # type: ignore[misc]
        unmatched = con.execute(
            "SELECT coalesce(sum(n_unmatched), 0) FROM injury_intraday_reports"
        ).fetchone()[0]  # type: ignore[index]
    finally:
        con.close()
    return {
        "intraday_ok": n_ok,
        "intraday_errors": n_err,
        "intraday_stamp_mismatch": n_bad,
        "stored_ok": s_ok,
        "stored_errors": s_err,
        "stored_mismatch": s_bad,
        "unmatched_names_total": int(unmatched),
        "pass": bool(
            n_ok > 0 and n_err == 0 and n_bad == 0 and s_ok > 0 and s_err == 0 and s_bad == 0
        ),
    }


def preflight(data_root: Path, repaired_db: Path | None) -> dict[str, Any]:
    """Label-free. Versions, scratch-db pin, G0, the gate, the planted future and the list fingerprints."""
    out: dict[str, Any] = {"season_max_allowed": MAX_SEASON, "versions": versions()}
    out["versions_pinned"] = out["versions"] == PINNED_VERSIONS
    if repaired_db is None:
        out["gate"] = {"gate_pass": False, "reason": "no repaired table given (--repaired)"}
        out["pass"] = False
        return out
    decl = rule.declared()
    out["repaired_db_sha256"] = file_sha(repaired_db)
    out["repaired_db_pinned"] = decl.get("REPAIRED_DB_SHA256") == out["repaired_db_sha256"]
    out["g0"] = g0_header_audit(repaired_db)
    built = gate.build_all(data_root, repaired_db)
    blocks = {str(s): gate.season_block(built, s) for s in gate.SEASONS}
    out["gate"] = gate.gate_verdict(blocks)
    r_rows = built["R"]
    out["planted_future"] = planted_future_check(r_rows, built["tips"])
    fps = {}
    for name in ("S", "R_live", "P"):
        fl, used = built[name]
        for s in gate.SEASONS:
            fps[f"FINGERPRINT_{name.replace('_live', 'LIVE')}_{s}"] = gate.fingerprint(
                fl, used, set(built["games"][s])
            )
    out["fingerprints"] = fps
    out["fingerprints_pinned"] = all(decl.get(k) == v for k, v in fps.items())
    out["pass"] = bool(
        out["versions_pinned"]
        and out["repaired_db_pinned"]
        and out["g0"]["pass"]
        and out["gate"]["gate_pass"]
        and out["planted_future"]["pass"]
        and out["fingerprints_pinned"]
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--data-root", type=Path, default=Path.cwd())
    ap.add_argument("--repaired", type=Path, default=None)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--truth", choices=TRUTHS, default="null")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.cmd == "rehearse":
        sc = score(synthetic_arms(a.truth))
        res: dict[str, Any] = {
            "real_outcomes": False,
            "truth": a.truth,
            "score": sc,
            "decision": words(sc, True),
        }
        out = a.out or (rule.ROOT / OUT_REHEARSAL / a.truth)
    else:
        if a.cmd == "run":
            blobs = rule.require_ready(a.sha)
            _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
        pre = preflight(a.data_root, a.repaired)
        if a.cmd == "check":
            print(json.dumps(pre, indent=1, default=str))
            return 0
        out = a.out or (a.data_root / OUT_REAL)
        if not pre.get("pass"):
            res = {
                "real_outcomes": False,
                "preflight": pre,
                "decision": guard_fail("preflight failed before any outcome was read"),
            }
        else:
            _log("preflight passed; no outcome loaded yet")
            assert a.repaired is not None
            res = {
                "real_outcomes": True,
                "preflight": pre,
                **run_real(a.data_root, a.repaired, out),
            }
        res["versions"] = versions()
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'} word {res['decision']['word']}")
    return 0 if res["decision"]["word"] != "GUARD_FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
