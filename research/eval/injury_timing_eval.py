# ruff: noqa: E501
"""INJURY_TIMING harness (rule: docs/prereg/INJURY_TIMING.md; DRAFT until frozen and pinned).

    check     preflight only: repaired-table gate, planted-future test, no outcome used
    rehearse  the whole scoring and decision machinery on SYNTHETIC outcomes (no real label, no fit)
    run       refuses unless the rule is frozen (prereg commit) and this harness is pinned (harness commit)

Real run (maintainer, from the main checkout so ``data/schedule`` and ``configs/`` resolve; wrap in the heavy lock):
    mkdir data/ops/heavy.lock && .venv/bin/python -W ignore -m research.eval.injury_timing_eval run --sha <64 hex> \
        --repaired <path to the repaired availability duckdb> ; rmdir data/ops/heavy.lock

Order enforced in code for ``run``: (1) require_ready(sha); (2) preflight (gate on the repaired table, planted future);
(3) build the stored-table feature frame and refit A0 for the four stats -> must reproduce the pinned integer CRPS (G4) or
the run STOPS before any other arm is fitted; (4) arms S and M; (5) score, words. Season 2025 is never queried
(``season <= 2024`` in SQL, ``max_season=2024`` on the schedule); ``collect_cross`` raises on a later test season.
Per-stat checkpoints (npz of per-row integer CRPS) are written as each stat finishes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
HARM = 0.001  # P2: upper CI of M - S must not exceed this in any stat
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
TRUTHS = ("null", "fix", "harm")


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
    """``arms[season][stat] = {"gid": ..., "A0": crps, "S": crps, "M": crps}`` (per-row integer CRPS, same row order)."""
    out: dict[str, Any] = {}
    for season, by_stat in arms.items():
        out[season] = {}
        for k, stat in enumerate(STATS):
            a = by_stat[stat]
            seed = (
                SEED + 10 * int(season) + k
            )  # one fixed seed per (season, stat): the three contrasts share the resample
            out[season][stat] = {
                "n": int(len(a["gid"])),
                "crps": {arm: float(np.mean(a[arm])) for arm in ("A0", "S", "M")},
                "M_minus_S": clustered_delta(a["M"] - a["S"], a["gid"], seed),
                "M_minus_A0": clustered_delta(a["M"] - a["A0"], a["gid"], seed),
                "S_minus_A0": clustered_delta(a["S"] - a["A0"], a["gid"], seed),
            }
    return out


def words(sc: dict[str, Any], guards_pass: bool) -> dict[str, Any]:
    """The decision rule of docs/prereg/INJURY_TIMING.md section 5, implemented once."""
    rep, sel = sc[str(REPORT)], sc[str(SELECT)]
    p = np.array([rep[s]["M_minus_S"]["p_lt0"] for s in STATS])
    q = bh_adjust(p)
    p1: dict[str, bool] = {}
    p3: dict[str, bool] = {}
    p2: dict[str, bool] = {}
    for i, s in enumerate(STATS):
        r, c = rep[s]["M_minus_S"], sel[s]["M_minus_S"]
        p1[s] = bool(r["point"] <= FLOOR and r["hi"] < 0.0 and q[i] <= ALPHA)
        p3[s] = bool(c["point"] < 0.0 and c["hi"] < 0.0)
        p2[s] = bool(r["hi"] <= HARM and c["hi"] <= HARM)
    n_pass = sum(p1[s] and p3[s] for s in STATS)
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
    }
    return {
        "word": word, "n_pass": n_pass, "P1_report_2024": p1, "P3_replication_2023": p3, "P2_no_harm": p2,
        "bh_q_2024": {s: float(q[i]) for i, s in enumerate(STATS)}, "flags": flags,
    }  # fmt: skip


# --------------------------------------------------------------------------- synthetic rehearsal


def synthetic_arms(
    truth: str, n_games: int = 400, per_game: int = 14, seed: int = 0
) -> dict[str, Any]:
    """Quantile grids built directly (no fit): outcomes Poisson(lam); truth sets which arm's centre is off by delta.
    null: A0 = S = M exact; fix: S is biased (+0.6 * sqrt(lam)... in counts), A0 half as much, M unbiased; harm: M biased."""
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
            shift = {"null": (0.0, 0.0, 0.0), "fix": (0.5, 1.0, 0.0), "harm": (0.0, 0.0, 1.0)}[
                truth
            ]  # (A0, S, M) in sd/2 units
            arm = {}
            for name, d in zip(("A0", "S", "M"), shift, strict=True):
                q = (lam + d * 0.5 * sd)[:, None] + sd[:, None] * z[None, :]
                arm[name] = crps_int(q, y)
            out[str(season)][stat] = {"gid": gid, **arm}
    return out


# --------------------------------------------------------------------------- planted future


def planted_future_check(rows: pl.DataFrame, n_games: int = 50, seed: int = SEED) -> dict[str, Any]:
    """Plant a fresh OUT row stamped at tip - 30 minutes (after the cutoff) in sampled games: the used OUT set must not change.
    Plant one stamped at tip - 60 (the cutoff itself): the used set must change (the test has power). ``rows`` carry ``tip_et``."""
    from nba.features.injury_report import ReportTriggerConfig, latest_pretip_flagged

    cfg = ReportTriggerConfig(statuses=("out",), lead_minutes=60, tip_source="real")
    base, _ = latest_pretip_flagged(rows, cfg)
    rng = np.random.default_rng(seed)
    gids = sorted(base)
    pick = [gids[i] for i in rng.choice(len(gids), size=min(n_games, len(gids)), replace=False)]
    tips = {g: t for g, t in rows.group_by("game_id").agg(pl.col("tip_et").first()).iter_rows()}
    cols = rows.columns

    def planted(minutes_before: int) -> pl.DataFrame:
        extra = pl.DataFrame(
            {
                "game_id": pick,
                "player_id": [-999] * len(pick),
                "status": ["out"] * len(pick),
                "as_of": [tips[g] - dt.timedelta(minutes=minutes_before) for g in pick],
                "game_date": [tips[g].date() for g in pick],
                "tip_et": [tips[g] for g in pick],
            }
        ).with_columns(
            pl.col("as_of").cast(pl.Datetime("us")), pl.col("tip_et").cast(pl.Datetime("us"))
        )
        for c in cols:
            if c not in extra.columns:
                extra = extra.with_columns(pl.lit(None).alias(c))
        return pl.concat([rows, extra.select(cols).cast(rows.schema)])

    late, _ = latest_pretip_flagged(planted(30), cfg)
    edge, _ = latest_pretip_flagged(planted(60), cfg)
    leaked = [g for g in pick if late.get(g) != base.get(g)]
    detected = [g for g in pick if -999 in edge.get(g, set())]
    return {
        "games_planted": len(pick),
        "leaked_after_T60": len(leaked),
        "detected_at_T60_edge": len(detected),
        "pass": bool(len(leaked) == 0 and len(detected) == len(pick)),
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


def build_frames(data_root: Path, repaired_db: Path) -> tuple[pl.DataFrame, pl.DataFrame, Any]:
    """(features with the STORED report table, features with the REPAIRED table, config). Both read-only, season <= 2024."""
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
    )

    elo = {
        k: float(v)
        for k, v in yaml.safe_load((data_root / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load((data_root / "configs/context_residual.yaml").read_text()).get("model", {})
    )
    games, pgs, static, avail_s = load_inputs(str(data_root / "nba.duckdb"), None)
    _, _, _, avail_i = load_inputs(str(data_root / "nba.duckdb"), str(repaired_db))
    avail_r = pl.concat(
        [avail_s, avail_i.select(avail_s.columns)]
    ).unique()  # R = stored pair + intraday table
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    fl_s, _ = flagged_from_availability(avail_s, games, cfg.report, max_season=MAX_SEASON)
    fl_r, _ = flagged_from_availability(avail_r, games, cfg.report, max_season=MAX_SEASON)
    fs = attach_realised(build_features(games, pgs, static, fl_s, elo), games, pgs)
    fr = attach_realised(build_features(games, pgs, static, fl_r, elo), games, pgs)
    return fs, fr, cfg


def g4_a0_repro(crps_by_season: dict[str, dict[str, float]]) -> dict[str, Any]:
    bad = {}
    for st in STATS:
        for s in map(str, SEASONS):
            d = abs(round(crps_by_season[st][s], G4_PIN_DIGITS) - A0_PINS[st][s])
            if d > G4_TOL:
                bad[f"{st}_{s}"] = d
    return {"mismatches": bad, "pass": not bad}


def run_real(data_root: Path, repaired_db: Path, out: Path) -> dict[str, Any]:
    fs, fr, cfg = build_frames(data_root, repaired_db)
    out.mkdir(parents=True, exist_ok=True)
    per_stat: dict[str, dict[str, Any]] = {}
    a0_means: dict[str, dict[str, float]] = {}
    # G4 first: A0 for all four stats; stop before S or M is fitted on a mismatch
    for st in STATS:
        comp = collect_cross(fs, fs, st, cfg)
        c = arm_crps(comp)
        per_stat[st] = {"gid": comp["row_gid"], "season": comp["row_season"], "A0": c}
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
        raise RuntimeError(
            f"G4 failed ({g4['mismatches']}): refusing before arms S and M are fitted"
        )
    _log("G4 holds: A0 refit reproduces the pinned integer CRPS")
    for st in STATS:
        ref = np.load(out / f"a0_{st}.npz", allow_pickle=True)
        for arm, (tr, te) in (("S", (fs, fr)), ("M", (fr, fr))):
            comp = collect_cross(tr, te, st, cfg)
            if (
                not (comp["row_gid"] == ref["gid"]).all()
                or not (comp["row_pid"] == ref["pid"]).all()
            ):
                raise RuntimeError("G3: arm rows differ from A0 rows")
            per_stat[st][arm] = arm_crps(comp)
        np.savez_compressed(
            out / f"arms_{st}.npz",
            gid=per_stat[st]["gid"],
            season=per_stat[st]["season"],
            A0=per_stat[st]["A0"],
            S=per_stat[st]["S"],
            M=per_stat[st]["M"],
        )
        _log(f"{st}: arms S and M scored (checkpoint written)")
    arms: dict[str, Any] = {str(s): {} for s in SEASONS}
    for st in STATS:
        for s in SEASONS:
            m = per_stat[st]["season"] == s
            arms[str(s)][st] = {
                "gid": per_stat[st]["gid"][m],
                **{a: per_stat[st][a][m] for a in ("A0", "S", "M")},
            }
    sc = score(arms)
    return {"score": sc, "g4": g4, "decision": words(sc, True)}


# --------------------------------------------------------------------------- preflight and CLI


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight(data_root: Path, repaired_db: Path | None) -> dict[str, Any]:
    """No outcome is read: the repaired-table gate and the planted-future test on real report rows."""
    from nba.features.game_tipoff import build_game_tipoff

    out: dict[str, Any] = {"season_max_allowed": MAX_SEASON}
    if repaired_db is None:
        out["gate"] = {"gate_pass": False, "reason": "no repaired table given (--repaired)"}
        return out
    tips = build_game_tipoff(data_root / "data/schedule", max_season=MAX_SEASON)
    stored = gate.rows_from_db(data_root / "nba.duckdb", tips)
    rep = gate.repaired_union(stored, gate.rows_from_db(repaired_db, tips))
    out["repaired_db_sha256"] = file_sha(repaired_db)
    want = rule.declared().get("REPAIRED_DB_SHA256")
    out["repaired_db_pinned"] = want is not None and want == out["repaired_db_sha256"]
    counts = gate.season_games(data_root / "nba.duckdb")
    blocks = {
        str(s): gate.season_block(
            rep.filter(pl.col("season") == s),
            counts.get(s, 0),
            stored.filter(pl.col("season") == s),
        )
        for s in gate.SEASONS
    }
    out["gate"] = gate.gate_verdict(blocks, True)
    out["planted_future"] = planted_future_check(rep)
    out["pass"] = bool(
        out["gate"]["gate_pass"] and out["planted_future"]["pass"] and out["repaired_db_pinned"]
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
        res = {"real_outcomes": False, "truth": a.truth, "score": sc, "decision": words(sc, True)}
        out = a.out or (rule.ROOT / OUT_REHEARSAL / a.truth)
    else:
        if a.cmd == "run":
            blobs = rule.require_ready(a.sha)
            _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
        pre = preflight(a.data_root, a.repaired)
        if a.cmd == "check":
            print(json.dumps(pre, indent=1, default=str))
            return 0
        if not pre.get("pass"):
            raise RuntimeError(
                f"preflight failed, no outcome was read: {json.dumps(pre, default=str)[:800]}"
            )
        _log("preflight passed; no outcome loaded yet")
        assert a.repaired is not None
        out = a.out or (a.data_root / OUT_REAL)
        res = {"real_outcomes": True, "preflight": pre, **run_real(a.data_root, a.repaired, out)}
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'} word {res['decision']['word']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
