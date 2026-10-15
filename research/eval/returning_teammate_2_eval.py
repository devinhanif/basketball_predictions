# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""RETURNING_TEAMMATE_2 harness (rule: docs/prereg/RETURNING_TEAMMATE_2.md; DRAFT until frozen). Model class: LightGBM context-residual heads.

Run from the repository root (``nba.duckdb``, ``configs/`` and ``data/schedule`` are read by relative path), read-only on ``nba.duckdb``:

    .venv/bin/python -W ignore -m research.eval.returning_teammate_2_eval check                      # label-free preflight (no outcome array is loaded)
    .venv/bin/python -W ignore -m research.eval.returning_teammate_2_eval rehearse [--seeds 20]      # synthetic outcomes drawn from the stored A0 grid (row_y never loaded)
    .venv/bin/python -W ignore -m research.eval.returning_teammate_2_eval run --sha <64 hex>         # real outcomes; heads walks; refuses unless frozen AND pinned

* ``check``: recomputes every pinned count from the database, the snapshot-source hash, the equality of this rule's ``out`` set with production's
  ``flagged_from_availability`` for every game, and the input hashes pinned in the rule. ``row_y`` is never loaded.
* ``rehearse``: the scoring path (paired game-clustered bootstrap, words, guards) on SYNTHETIC outcomes. Truth ``planted``: y drawn from the stored A0 integer
  grid shifted on affected rows by the descriptive effect (T476) with the returner playing with probability p; truth ``null``: y drawn from A0. Candidates:
  ``ORACLE`` (the planted expected shift), ``WRONG`` (the same shifts moved to other affected rows). It measures the harness's own pass rate (a power
  and false-positive check on the gate numbers); a candidate refit is NOT run here (that needs labels).
* ``run``: the real thing. Order: rule frozen and harness pinned -> preflight -> heads CONTROL walks (stored A0 must reproduce to 1e-6, else refuse before any RT
  arm is built) -> RT / PL / NOISE walks -> integer-CRPS scoring. Nothing here touches season 2025 (``MAX_SEASON`` assert in every loader).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import returning_teammate_2_features as F
from research.eval import returning_teammate_2_rule as rule

MAX_SEASON = 2024
SEASONS = (2023, 2024)
REPORT, SELECT = 2024, 2023
STATS = ("pts", "reb", "ast", "fg3m")
FLOOR = -0.005  # pts, RT minus A0, integer CRPS, affected rows
CLOSE_AT = -0.002  # a season whose point estimate is above this closes the rule
NOHARM = 0.001  # upper CI of RT - A0 on ALL rows
PL_MARGIN = -0.003  # RT - PL on affected rows, pts, each season
NOISE_TOL: float | None = (
    None  # NOISE - CTRL on affected rows: a gain this large with CI upper < 0 FAILS. None until `null` (K >= 50) is run and the value is written into the rule; `run` refuses while None.
)
SLICE_TOL = 0.01
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = (
    0.01  # |coverage - tau| of RT may exceed A0's by at most this at each of COV_TAUS (all rows)
)
MIN_SLICE_N = 300
REPRO_TOL = 1e-6
N_BOOT, SEED = 5000, 2026
DESC_EFFECT, DESC_MPG = -0.81, 33.0
ENC = ("pl_", "nz_")  # placebo / noise column prefixes
PINNED: dict[str, Any] = {
    "rows": {"2023": 27_619, "2024": 27_701},
    "affected_rows": {"2023": 8_241, "2024": 8_815},
    "games": {"2023": 1_318, "2024": 1_320},
}
OUT_REAL = Path("reports/returning_teammate_2")
OUT_REHEARSAL = Path("reports/prereg_returning_teammate_2/rehearsal")
CACHE = Path("data/ops/returning_teammate_2_cache")
LT = Path("reports/lower_tail")  # relative to the repository root (cwd)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ statistics


class Boot:
    """Game-clustered bootstrap: ratio of resampled per-game sums, one weight matrix per season (rows of the same game move together)."""

    def __init__(self, gid: np.ndarray, seed: int = SEED, b: int = N_BOOT) -> None:
        u, self.code = np.unique(gid, return_inverse=True)
        self.G, self.B = len(u), b
        self.W = (
            np.random.default_rng(seed)
            .multinomial(self.G, np.full(self.G, 1.0 / self.G), size=b)
            .astype(float)
        )

    def ci(self, v: np.ndarray, m: np.ndarray) -> dict[str, float]:
        if int(m.sum()) == 0:
            return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
        s = np.bincount(self.code[m], weights=v[m], minlength=self.G)
        c = np.bincount(self.code[m], minlength=self.G).astype(float)
        den = self.W @ c
        rep = (self.W @ s) / np.where(den > 0, den, np.nan)
        lo, hi = np.nanpercentile(rep, [2.5, 97.5])
        return {
            "point": float(v[m].sum() / m.sum()),
            "lo": float(lo),
            "hi": float(hi),
            "n": int(m.sum()),
        }

    def p_le0(self, v: np.ndarray, m: np.ndarray) -> float:
        """One-sided bootstrap p for 'mean < 0' (share of replicates >= 0), floored at 1 / B."""
        s = np.bincount(self.code[m], weights=v[m], minlength=self.G)
        c = np.bincount(self.code[m], minlength=self.G).astype(float)
        den = self.W @ c
        rep = (self.W @ s) / np.where(den > 0, den, np.nan)
        return float(max(np.nanmean(rep >= 0.0), 1.0 / self.B))


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


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    from research.eval import pi_recent_role_eval as PR

    return np.asarray(PR.crps_int(qi, y))


# ------------------------------------------------------------------------------------ scoring


def slice_masks(rt: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Declared slices of the AFFECTED rows (ret_min > 0). ``rt``: arrays aligned to the scored rows."""
    aff = rt["ret_min"] > 0
    return {
        "star_returner(ret_min_star>0)": aff & (rt["ret_min_star"] > 0),
        "rotation_returner_only": aff & (rt["ret_min_star"] <= 0),
        "missed_3-4": aff & (rt["ret_missed"] <= 4),
        "missed_5-9": aff & (rt["ret_missed"] >= 5) & (rt["ret_missed"] <= 9),
        "missed_10+": aff & (rt["ret_missed"] >= 10),
        "listed_tonight(ret_min_listed>0)": aff & (rt["ret_min_listed"] > 0),
        "unlisted_tonight": aff & (rt["ret_min_listed"] <= 0),
        "out_run_N3(report-literal)": aff & (rt["out_run"] > 0),
    }


def score_arms(
    qi: dict[str, dict[str, np.ndarray]],
    y: dict[str, np.ndarray],
    gid: np.ndarray,
    season: np.ndarray,
    rt: dict[str, np.ndarray],
    seed: int = SEED,
) -> dict[str, Any]:
    """Contrasts per stat and season. ``qi[stat][arm]`` integer 199-quantile grids; arms include ``A0`` and ``RT`` (and ``PL``, ``NOISE`` when present)."""
    out: dict[str, Any] = {}
    aff = rt["ret_min"] > 0
    sl = slice_masks(rt)
    for stat in qi:
        per: dict[str, Any] = {}
        sc = {arm: crps_int(q, y[stat]) for arm, q in qi[stat].items()}
        for s in (*SEASONS, "pooled"):
            m = np.isin(season, SEASONS) if s == "pooled" else season == s
            bt = Boot(gid[m], seed + (1 if s == "pooled" else s))
            sub = lambda v, mm=m: v[mm]  # noqa: E731
            ma = aff[m]
            d_rt = sub(sc["RT"] - sc["A0"])
            block: dict[str, Any] = {
                "n_rows": int(m.sum()),
                "n_affected": int(ma.sum()),
                "RT_minus_A0_affected": bt.ci(d_rt, ma),
                "RT_minus_A0_all": bt.ci(d_rt, np.ones(int(m.sum()), bool)),
                "p_one_sided_affected": bt.p_le0(d_rt, ma),
            }
            if "PL" in sc:
                block["RT_minus_PL_affected"] = bt.ci(sub(sc["RT"] - sc["PL"]), ma)
            if "NOISE" in sc:
                block["NOISE_minus_CTRL_affected"] = bt.ci(
                    sub(sc["NOISE"] - sc.get("CTRL", sc["A0"])), ma
                )
            from nba.props.context_residual import CRPS_TAUS

            cov: dict[str, list[float]] = {}
            for tau in COV_TAUS:
                j = int(np.argmin(np.abs(np.asarray(CRPS_TAUS) - tau)))
                cov[str(tau)] = [
                    float((y[stat][m] <= qi[stat][a][m, j]).mean()) for a in ("A0", "RT")
                ]
            block["coverage_A0_RT"] = cov
            sls: dict[str, Any] = {}
            for name, mask in sl.items():
                mm = mask[m]
                if int(mm.sum()) >= MIN_SLICE_N:
                    sls[name] = bt.ci(d_rt, mm)
                else:
                    sls[name] = {"n": int(mm.sum()), "reported": False}
            block["slices"] = sls
            per[str(s)] = block
        out[stat] = per
    # BH over the three secondary stats within each season, on the one-sided affected-rows p
    for s in (*SEASONS, "pooled"):
        sec = [st for st in ("reb", "ast", "fg3m") if st in out]
        if sec:
            adj = bh_adjust(np.array([out[st][str(s)]["p_one_sided_affected"] for st in sec]))
            for st, a in zip(sec, adj, strict=True):
                out[st][str(s)]["p_bh_m3"] = float(a)
    return out


def decide(sc: dict[str, Any]) -> dict[str, Any]:
    """The rule's words from ``score_arms`` output (option B: ONE pooled 2023+2024 test; pts is the claim, guards on all four stats).

    PASS_POOLED = the pooled pts affected-rows contrast <= FLOOR with CI upper < 0 and every guard holds; it means "consistent with T476", never a
    production claim. CLOSED = pooled point > CLOSE_AT. GUARD_FAIL = the contrast passes but a guard fails. Else INCONCLUSIVE. The per-season
    points are reported as ``consistency`` (both negative) and do not vote."""
    pts = sc["pts"]
    b = pts["pooled"]["RT_minus_A0_affected"]
    if b["point"] <= FLOOR and b["hi"] < 0:
        prim = "PASS"
    elif b["point"] > CLOSE_AT:
        prim = "CLOSED"
    else:
        prim = "INCONCLUSIVE"
    guards: dict[str, bool] = {}
    for st in sc:
        guards[f"G1_no_harm_{st}_pooled"] = bool(
            sc[st]["pooled"]["RT_minus_A0_all"]["hi"] <= NOHARM
        )
        for s in SEASONS:
            guards[f"G1_no_harm_{st}_{s}"] = bool(sc[st][str(s)]["RT_minus_A0_all"]["hi"] <= NOHARM)
    for s in (
        "pooled",
    ):  # G2, G3, G5, G6 vote on the pooled test only; per-season rows are reported
        pl_ = pts[str(s)].get("RT_minus_PL_affected")
        guards[f"G2_beats_placebo_{s}"] = bool(pl_ is not None and pl_["point"] <= PL_MARGIN)
        cov = pts[str(s)].get("coverage_A0_RT")
        guards[f"G6_coverage_{s}"] = bool(
            cov is None
            or all(abs(v[1] - float(t)) - abs(v[0] - float(t)) <= COV_TOL for t, v in cov.items())
        )
        nz = pts[str(s)].get("NOISE_minus_CTRL_affected")
        guards[f"G3_noise_does_not_win_{s}"] = not (
            nz is not None and NOISE_TOL is not None and nz["point"] <= NOISE_TOL and nz["hi"] < 0
        )
        bad = [
            k
            for k, v in pts[str(s)]["slices"].items()
            if v.get("reported", True) and v.get("point") is not None and v["point"] > SLICE_TOL
        ]
        guards[f"G5_slices_not_worse_{s}"] = not bad
    consistency = all(pts[str(s)]["RT_minus_A0_affected"]["point"] < 0 for s in SEASONS)
    word = ("PASS_POOLED" if all(guards.values()) else "GUARD_FAIL") if prim == "PASS" else prim
    return {
        "word": word,
        "primary": prim,
        "consistency_both_seasons_negative": consistency,
        "guards": guards,
    }


# ------------------------------------------------------------------------------------ data


def load_world(db: str = "nba.duckdb") -> dict[str, Any]:
    """games (season <= 2024), full box, report snapshot status. Read-only."""
    from research.eval import prereg_gates_common as C
    from research.eval import returning_teammate_2_gate as G

    C.DB = Path(db).resolve()
    con = C.connect()
    games = C.load_games(con)
    box = G.load_box_full(con)
    tg = C.team_game_index(box, games)
    report = C.load_report(con)
    status = F.snapshot_status(report, games)
    con.close()
    return {"games": games, "box": box, "tg": tg, "status": status, "report": report}


def rt_features(w: dict[str, Any]) -> pl.DataFrame:
    """RT columns for every box row plus ``out_run`` (the report-literal slice: ret_min under prev_rule='out_all')."""
    f, _ = F.build_returning(w["box"], w["tg"], w["status"], prev_rule="listed_prev")
    o, _ = F.build_returning(w["box"], w["tg"], w["status"], prev_rule="out_all")
    o = o.select("game_id", "player_id", pl.col("ret_min").alias("out_run"))
    return f.join(o, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("out_run").fill_null(0.0)
    )


def production_tie(w: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """Equality of this rule's snapshot (players with status 'out') with production's ``flagged_from_availability`` for every game."""
    import yaml

    from nba.props.context_residual import ContextResidualConfig, flagged_from_availability
    from research.eval import prereg_gates_common as C

    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    con = C.connect()
    avail = con.execute(
        "SELECT game_id, player_id, status, as_of, source FROM player_availability WHERE player_id IS NOT NULL"
    ).pl()
    games = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team FROM games WHERE season <= 2024"
    ).pl()
    con.close()
    avail = avail.filter(pl.col("game_id").is_in(games["game_id"].to_list())).with_columns(
        pl.col("status").str.to_lowercase(), pl.col("as_of").cast(pl.Datetime("us"))
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    mine = w["status"].filter(pl.col("status") == "out")
    mine_d: dict[str, set[int]] = {}
    for g, p in mine.select("game_id", "player_id").iter_rows():
        mine_d.setdefault(str(g), set()).add(int(p))
    games_with = set(w["status"]["game_id"].unique().to_list())
    prod_games = set(flagged)
    diff = [
        g
        for g in games_with | prod_games
        if (mine_d.get(g, set()) if g in games_with else None)
        != (flagged.get(g, set()) if g in prod_games else None)
    ]
    if limit:
        diff = diff[:limit]
    return {
        "games_compared": len(games_with | prod_games),
        "games_different": len(diff),
        "examples": diff[:5],
    }


def preflight(w: dict[str, Any], f: pl.DataFrame, declared: dict[str, str]) -> dict[str, Any]:
    """Label-free: the snapshot-source hash and the pinned counts. Raises on any difference."""
    if declared.get("SNAPSHOT_SOURCE_SHA256") != F.snapshot_source_sha():
        raise RuntimeError(
            f"snapshot source sha {F.snapshot_source_sha()[:12]} != pinned {declared.get('SNAPSHOT_SOURCE_SHA256', 'missing')[:12]}: the snapshot definition changed; re-register"
        )
    bad = {}
    for name, rel in {
        **{f"COMPONENTS_SHA256_{st.upper()}": f"components_{st}.npz" for st in STATS},
        "COMPONENTS_SHA256_PI": "components_pi.npz",
    }.items():
        got = rule.file_sha256(LT / rel)
        if declared.get(name) != got:
            bad[name] = (got[:12], declared.get(name, "missing")[:12])
    if bad:
        raise RuntimeError(f"pinned input sha256 mismatch (stored A0 components): {bad}")
    games = w["games"]
    out: dict[str, Any] = {}
    for s in SEASONS:
        gids = games.filter(pl.col("season") == s)["game_id"].to_list()
        fs = f.filter(pl.col("game_id").is_in(gids))
        out[str(s)] = {
            "player_games": fs.height,
            "affected_player_games": int((fs["ret_min"] > 0).sum()),
        }
    c = a0_components("pts", with_labels=False)
    rt = align(f, c["row_gid"], c["row_pid"])
    for s in SEASONS:
        m = c["row_season"] == s
        have = {
            "rows": int(m.sum()),
            "affected_rows": int((m & (rt["ret_min"] > 0)).sum()),
            "games": int(np.unique(c["row_gid"][m]).size),
        }
        want = {k: PINNED[k][str(s)] for k in have}
        if have != want:
            raise RuntimeError(f"{s}: counts {have} != pinned {want}")
        out[str(s)]["a0_rows"] = have
    tie = production_tie(w)
    if tie["games_different"]:
        raise RuntimeError(
            f"snapshot differs from production's OUT set in {tie['games_different']} games: {tie['examples']}"
        )
    out["production_tie"] = tie
    return out


def a0_components(stat: str, with_labels: bool) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    c = load_components(stat, LT)
    if int(c["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season 2025 is the frozen holdout")
    if not with_labels:
        c = {k: v for k, v in c.items() if k != "row_y"}
    return c


def align(f: pl.DataFrame, gid: np.ndarray, pid: np.ndarray) -> dict[str, np.ndarray]:
    keys = pl.DataFrame({"game_id": gid.astype(str), "player_id": pid.astype(np.int64)})
    j = keys.join(f, on=["game_id", "player_id"], how="left").with_columns(
        pl.col(c).fill_null(0) for c in (*F.FEATURES_ALL, "out_run")
    )
    return {c: j[c].to_numpy().astype(float) for c in (*F.FEATURES_ALL, "out_run")}


# ------------------------------------------------------------------------------------ rehearsal


def effect_model(
    rt: dict[str, np.ndarray], rotation: np.ndarray, p_star: float, p_rot: float
) -> tuple[np.ndarray, np.ndarray]:
    """(expected shift, play probability) per row under the descriptive effect (the gate's model)."""
    m = np.minimum(rt["ret_min"] / DESC_MPG, 2.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(
            rt["ret_min"] > 0,
            (p_star * rt["ret_min_star"] + p_rot * (rt["ret_min"] - rt["ret_min_star"]))
            / np.where(rt["ret_min"] > 0, rt["ret_min"], 1.0),
            0.0,
        )
    base = DESC_EFFECT * m * rotation
    return base * p, np.where(base != 0, p, 0.0)


def draw_y(qi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return qi[np.arange(len(qi)), rng.integers(0, qi.shape[1], len(qi))].astype(float)


def rehearse(seeds: int, p_star: float = 0.77, p_rot: float = 0.63) -> dict[str, Any]:
    """pts only. Synthetic outcomes from the stored A0 grid; ORACLE and WRONG candidates; the words and guards on each seed."""
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support

    w = load_world()
    f = rt_features(w)
    c = a0_components("pts", with_labels=False)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    rt = align(f, c["row_gid"], c["row_pid"])
    gid, season = c["row_gid"].astype(str), c["row_season"]
    grid = arm_grid(
        "mixture", {**c, "row_y": np.zeros(len(c["row_c"]))}, pic, "pts"
    )  # row_y is only a length carrier here
    q0 = to_integer_support(grid).astype(np.int16)
    rotation = c["row_min10"] >= F.ROTATION_MIN
    shift, p_row = effect_model(rt, rotation, p_star, p_rot)
    aff_idx = np.flatnonzero(rt["ret_min"] > 0)
    out: dict[str, Any] = {"truths": {}}
    for truth in ("null", "planted"):
        words: list[str] = []
        pts_deltas: list[float] = []
        failed: dict[str, int] = {}
        for sd in range(seeds):
            rng = np.random.default_rng(1000 + sd)
            if truth == "null":
                y = draw_y(q0, rng)
            else:
                plays = rng.random(len(q0)) < p_row
                full = DESC_EFFECT * np.minimum(rt["ret_min"] / DESC_MPG, 2.0) * rotation
                y = draw_y(to_integer_support(grid + (full * plays)[:, None]).astype(np.int16), rng)
            q_or = to_integer_support(grid + shift[:, None]).astype(np.int16)
            wrong = np.zeros(len(q0))
            wrong[aff_idx] = shift[rng.permutation(aff_idx)]
            q_wr = to_integer_support(grid + wrong[:, None]).astype(np.int16)
            sc = score_arms(
                {"pts": {"A0": q0, "RT": q_or, "PL": q_wr}},
                {"pts": y},
                gid,
                season,
                {**rt},
                seed=SEED + sd,
            )
            dec = decide(sc)
            words.append(dec["word"])
            for gname, ok in dec["guards"].items():
                if not ok:
                    failed[gname] = failed.get(gname, 0) + 1
            pts_deltas.append(sc["pts"]["2024"]["RT_minus_A0_affected"]["point"])
        out["truths"][truth] = {
            "seeds": seeds,
            "words": {k: words.count(k) for k in sorted(set(words))},
            "mean_2024_oracle_delta_affected": float(np.mean(pts_deltas)),
            "guard_failures": failed,
        }
        _log(f"rehearsal truth {truth}: {out['truths'][truth]['words']}")
    return out


# ------------------------------------------------------------------------------------ the real run


def freeze_time(sha: str) -> float:
    h = rule.freeze_commit(sha)
    if h is None:
        raise RuntimeError("not frozen")
    return float(
        subprocess.run(
            ["git", "show", "-s", "--format=%ct", h],
            capture_output=True,
            text=True,
            check=True,
            cwd=rule.ROOT,
        ).stdout.strip()
    )


def check_checkpoint(path: Path, frozen_at: float | None) -> bool:
    """True iff ``path`` exists and was written after the freeze commit (an older file is refused, never reused)."""
    if not path.exists():
        return False
    if frozen_at is not None and path.stat().st_mtime < frozen_at:
        raise RuntimeError(f"checkpoint {path} predates the freeze: refusing to reuse it")
    return True


def noise_frame(fr: pl.DataFrame, cols: tuple[str, ...], seed: int) -> pl.DataFrame:
    """Columns row-permuted jointly within season (real labels): a non-informative copy of the same marginals."""
    idx = np.arange(fr.height)
    for s in np.unique(fr["season"].to_numpy()):
        sel = np.flatnonzero(fr["season"].to_numpy() == s)
        idx[sel] = sel[np.random.default_rng([seed, int(s)]).permutation(len(sel))]
    return fr.with_columns(*[pl.Series("nz_" + c, fr[c].to_numpy()[idx]) for c in cols])


def null_threshold(deltas: list[float]) -> dict[str, float]:
    """G3 size from K permutation draws of NOISE - CTRL (pooled pts affected rows): mean, sd, and the proposed tolerance
    ``max(0.001, mean - 2 sd)`` negated into a gain threshold (a NOISE gain beyond it fails the guard)."""
    d = np.asarray(deltas, dtype=float)
    mu, sd = float(d.mean()), float(d.std(ddof=1))
    return {
        "k": int(len(d)),
        "mean": mu,
        "sd": sd,
        "min": float(d.min()),
        "tol": float(min(-0.001, mu - 2.0 * sd)),
    }


def null(k: int, out: Path) -> dict[str, Any]:
    """Permutation null for G3. Needs labels (training residuals) but builds NO RT arm: CTRL once, then K walks of the pts heads with the eight
    columns permuted jointly within season (seed SEED + i), scored NOISE_i - CTRL on pooled affected rows. Per-draw checkpoints in ``out``."""
    from nba.props.context_residual import stat_feature_names
    from research.eval import four_factors_eval as FF
    from research.eval import recent_role_full_eval as RF
    from research.eval import workload_eval as WE

    w = load_world()
    f = rt_features(w)
    study = FF.load_study("nba.duckdb")
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    cfg = study["cfg"]
    base = feats.join(
        f.select("game_id", "player_id", *F.FEATURES_ALL), on=["game_id", "player_id"], how="left"
    )
    base = base.with_columns(pl.col(c).fill_null(0.0) for c in F.FEATURES_ALL)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    stored = a0_components("pts", with_labels=True)
    rt = align(f, stored["row_gid"], stored["row_pid"])
    aff = (rt["ret_min"] > 0) & np.isin(stored["row_season"], SEASONS)
    y = stored["row_y"]
    out.mkdir(parents=True, exist_ok=True)
    names0 = stat_feature_names("pts")
    t0 = time.time()
    ctrl = RF.collect_heads(base, "pts", cfg, names0, log=False)
    rep = RF.heads_vs_stored(ctrl, stored)
    if not rep["rows_equal"] or rep["max_abs_diff"] > REPRO_TOL:
        raise RuntimeError(f"G4 failed in the null run: {rep['max_abs_diff']}")
    c_ctrl = crps_int(RF.arm_qi("pts", ctrl, pic, pic["row_pi"]), y)
    _log(f"CTRL walk done {time.time() - t0:.0f}s")
    deltas: list[float] = []
    for i in range(k):
        path = out / f"null_{i:03d}.json"
        if path.exists():
            deltas.append(json.loads(path.read_text())["delta"])
            continue
        fr = noise_frame(base, F.FEATURES_ALL, SEED + i)
        names = [*names0, *("nz_" + c for c in F.FEATURES_ALL)]
        comp = RF.collect_heads(fr, "pts", cfg, names, log=False)
        d = float((crps_int(RF.arm_qi("pts", comp, pic, pic["row_pi"]), y) - c_ctrl)[aff].mean())
        path.write_text(json.dumps({"i": i, "delta": d}))
        deltas.append(d)
        _log(f"null draw {i + 1}/{k}: NOISE - CTRL = {d:+.5f} ({time.time() - t0:.0f}s)")
    return null_threshold(deltas)


def run(sha: str) -> dict[str, Any]:
    from nba.eval.context_residual_eval import TEST_SEASONS
    from nba.props.context_residual import stat_feature_names
    from research.eval import four_factors_eval as FF
    from research.eval import recent_role_full_eval as RF
    from research.eval import workload_eval as WE
    from research.recent_role import placebo as PB

    assert max(TEST_SEASONS) <= MAX_SEASON
    if NOISE_TOL is None:
        raise RuntimeError(
            "G3 is not sized: run `null` (K >= 50 permutation null), put its threshold in the rule and the harness, then re-pin"
        )
    blobs = rule.require_ready(sha)
    _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
    frozen_at = freeze_time(sha)
    w = load_world()
    f = rt_features(w)
    pre = preflight(w, f, rule.declared())
    _log(
        "preflight passed (snapshot source hash, production OUT-set tie, pinned counts); no outcome loaded yet"
    )
    study = FF.load_study("nba.duckdb")
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    cfg = study["cfg"]
    fr = feats.join(
        f.select("game_id", "player_id", *F.FEATURES_ALL), on=["game_id", "player_id"], how="left"
    )
    if fr.height != feats.height:
        raise RuntimeError("attaching RT columns changed the rows")
    fr = fr.with_columns(pl.col(c).fill_null(0.0) for c in F.FEATURES_ALL)
    fr = PB.permute_past_only(fr, F.FEATURES_ALL, SEED, "pl_")
    fr = noise_frame(fr, F.FEATURES_ALL, SEED)
    WE.assert_seasons(fr)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    arms: dict[str, tuple[str, ...]] = {
        "CTRL": (),
        "RT": F.FEATURES_ALL,
        "PL": tuple("pl_" + c for c in F.FEATURES_ALL),
        "NOISE": tuple("nz_" + c for c in F.FEATURES_ALL),
    }
    heads: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for arm in (
        "CTRL",
        "RT",
        "PL",
        "NOISE",
    ):  # CTRL first: the reproduction gate fires before any RT arm is built
        for stat in STATS:
            path = CACHE / f"heads_{stat}_{arm}.npz"
            if not check_checkpoint(path, frozen_at):
                _log(f"{stat} heads walk {arm}")
                comp = RF.collect_heads(fr, stat, cfg, [*stat_feature_names(stat), *arms[arm]])
                np.savez_compressed(path, **comp)
            z = np.load(path, allow_pickle=True)
            heads.setdefault(stat, {})[arm] = {k: z[k] for k in z.files}
            if arm == "CTRL":
                stored = a0_components(stat, with_labels=True)
                rep = RF.heads_vs_stored(heads[stat][arm], stored)
                if not rep["rows_equal"] or rep["max_abs_diff"] > REPRO_TOL:
                    raise RuntimeError(
                        f"G4 failed: {stat} control walk differs from the stored A0 by {rep['max_abs_diff']}: refusing before any RT arm is built"
                    )
    _log("G4 holds: the control walk reproduces the stored A0 for every stat; scoring")
    qi: dict[str, dict[str, np.ndarray]] = {}
    y: dict[str, np.ndarray] = {}
    for stat in STATS:
        stored = a0_components(stat, with_labels=True)
        y[stat] = stored["row_y"]
        qi[stat] = {"A0": RF.arm_qi(stat, stored, pic, pic["row_pi"])}
        for arm in ("CTRL", "RT", "PL", "NOISE"):
            qi[stat][arm] = RF.arm_qi(stat, heads[stat][arm], pic, pic["row_pi"])
    ref = a0_components("pts", with_labels=False)
    rt = align(f, ref["row_gid"], ref["row_pid"])
    sc = score_arms(qi, y, ref["row_gid"].astype(str), ref["row_season"], rt)
    res = {"preflight": pre, "scores": sc, "decision": decide(sc), "pin": rule.pin_of(blobs)}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run", "null"))
    ap.add_argument("--sha", default=None)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.cmd == "check":
        w = load_world()
        f = rt_features(w)
        res = preflight(w, f, rule.declared())
        res["snapshot_source_sha"] = F.snapshot_source_sha()
        print(json.dumps(res, indent=1, default=str))
        return 0
    if a.cmd == "null":
        res = null(max(a.k, 50), a.out or (rule.ROOT / "reports/prereg_returning_teammate_2/null"))
        print(json.dumps(res, indent=1))
        return 0
    if a.cmd == "rehearse":
        res = rehearse(a.seeds)
        out = a.out or (rule.ROOT / OUT_REHEARSAL)
    else:
        res = run(a.sha or "")
        out = a.out or (rule.ROOT / OUT_REAL)
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
