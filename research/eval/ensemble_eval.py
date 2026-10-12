# ruff: noqa: E501
"""ENSEMBLE: minutes-gated XGBoost routing (E1), SEQ transformer replication (E2), with history (E3), both (E4).

The rule is docs/prereg/ENSEMBLE.md (canonical; this module implements it and changes nothing in it).
Season 2025 is never loaded; ``nba.duckdb`` and the history DB are opened read-only.

Stages (``uv run python -m research.eval.ensemble_eval <stage>``):
  gate   G3b (positions), P-in (era from the zero pattern of the inputs), g AUC, missingness audit g8.
         ``--allow-draft`` runs it on an unfrozen document. No arm is fitted.
  cpu    E1 (and the descriptive oracle / hard-route versions) on the stored XGBoost A1 and A0 grids.
         Refuses unless the document is FROZEN and its sha256 matches ``--sha``.
  score  After ``make colab-pull JOB=seq_ensemble``: every cell, guard, verdict (``--run <dir>``).
  smoke  Pure-function plumbing check on synthetic arrays (no data).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, bh_adjust, boot_p_value, crps_from_quantiles
from nba.eval.lower_tail_eval import arm_grid, load_components, randomized_pit
from nba.props.context_residual import CRPS_TAUS, to_integer_support
from nba.props.metrics import paired_score_delta_ci

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/ENSEMBLE.md"
OUT_DIR = ROOT / "reports/prereg_ensemble"
CACHE = ROOT / "data/ensemble"
LT_DIR = ROOT / "reports/lower_tail"
EXPORT_DIR = ROOT / "data/colab/seq_ensemble"
A1_ALIGNED = CACHE / "a1_pts_aligned.npz"
DK_CACHE = ROOT / "data/ops/xgb_pts_vs_lt_cache/dk_t60_pts.parquet"
MARKER = "=== RESULTS BELOW ==="

STATS = ("pts", "reb", "ast", "fg3m")
SEASONS = (2023, 2024)
KEYS = ["game_id", "player_id"]
PHI = 1610612755
# --- constants of the rule (changing one changes the rule)
FLOOR = -0.005
KO_FLOOR = -0.0025
SHUFFLE_MIN = -0.001
N_BOOT = 2000
BOOT_SEED = 0
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
TEAM_SHARE_MAX = 0.25
TLL_MARGIN = 0.002
TLL_FIXED_MAX = 0.005
THRESHOLDS = (20, 25, 30)
PROXY_HOUR_ET = 19.0
NULL_GAP_PP = 2.0
G_AUC_FLOOR = 0.70
G_MIN_SHORT_ROWS = 1000
PIN_FLOOR = 0.505
PEMB_MARGIN = 0.02
GATE_HARD = 0.9
EPS = 0.5 / 199
BUCKETS = ("<5", "5-20", ">20")
REPORT_CTX = (
    "has_report", "st_available", "st_probable", "st_questionable", "st_doubtful", "st_out",
    "n_rep_out", "vac_rep", "n_return", "vac_return",
)  # fmt: skip


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze


def head_text(doc: Path = DOC) -> str:
    out = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        out.append(line)
    return "".join(out)


def frozen_sha(doc: Path = DOC) -> str:
    return hashlib.sha256(head_text(doc).encode()).hexdigest()


def doc_status(doc: Path = DOC) -> str:
    for line in head_text(doc).splitlines():
        if line.startswith("Status:"):
            return line.split()[1].strip("*:").upper()
    return "UNKNOWN"


def require_frozen(sha: str | None, allow_draft: bool, doc: Path = DOC) -> str:
    got = frozen_sha(doc)
    if allow_draft:
        return got
    if doc_status(doc) != "FROZEN":
        raise SystemExit("STOP: the rule document is not FROZEN")
    if sha is None or got != sha:
        raise SystemExit(f"STOP: rule sha256 {got} != expected {sha}")
    return got


# --------------------------------------------------------------------------- pure helpers


def ext19(q19: np.ndarray) -> np.ndarray:
    """19 quantiles (tau 0.05..0.95) to the 199 grid: linear inside, linear-slope extrapolation outside,
    clipped at 0 and made monotone (the ``ext_tails`` of the XGBoost arm, on the CRPS grid)."""
    q19 = np.sort(np.asarray(q19, dtype=float), axis=1)
    t19 = np.arange(1, 20) / 20.0
    t = CRPS_TAUS
    k = np.clip(np.searchsorted(t19, t) - 1, 0, 17)
    w = (t - t19[k]) / 0.05
    q = q19[:, k] * (1 - w) + q19[:, k + 1] * w
    lo, hi = t < 0.05, t > 0.95
    s_lo = np.maximum(q19[:, 1] - q19[:, 0], 0.0) / 0.05
    s_hi = np.maximum(q19[:, 18] - q19[:, 17], 0.0) / 0.05
    q[:, lo] = q19[:, :1] + s_lo[:, None] * (t[lo] - 0.05)[None, :]
    q[:, hi] = q19[:, 18:19] + s_hi[:, None] * (t[hi] - 0.95)[None, :]
    return np.asarray(np.maximum.accumulate(np.clip(q, 0.0, None), axis=1))


def grid19(q199: np.ndarray) -> np.ndarray:
    """199-grid to the 19 levels 0.05..0.95 by linear interpolation in tau (for the learned gate)."""
    t19 = np.arange(1, 20) / 20.0
    pos = np.interp(t19, CRPS_TAUS, np.arange(len(CRPS_TAUS)))
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, len(CRPS_TAUS) - 1)
    w = pos - lo
    return np.asarray(q199[:, lo] * (1 - w) + q199[:, hi] * w)


def survival_at(mu: np.ndarray, sigma: np.ndarray, x: float = 5.0, hi: float = 48.0) -> np.ndarray:
    """P(minutes >= x | plays) of the production truncated-normal minutes distribution (as-of mu, sigma)."""
    from scipy import stats

    sg = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    return np.asarray(stats.truncnorm.sf(x, (0 - mu) / sg, (hi - mu) / sg, loc=mu, scale=sg))


def int_pmf(qi: np.ndarray, k: int) -> np.ndarray:
    """Equal-mass integer grids (n, 199) to pmfs over 0..k-1 (values >= k are lumped into k - 1)."""
    v = np.clip(np.asarray(qi).astype(np.int64), 0, k - 1)
    out = np.zeros((v.shape[0], k))
    np.add.at(out, (np.repeat(np.arange(v.shape[0]), v.shape[1]), v.ravel()), 1.0 / v.shape[1])
    return out


def mix_integer_quantiles(qa: np.ndarray, qb: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Integer 199-grid of the pmf mixture ``w * pmf(qa) + (1 - w) * pmf(qb)`` (weights per row)."""
    k = int(max(np.max(qa), np.max(qb))) + 2
    cdf = np.cumsum(w[:, None] * int_pmf(qa, k) + (1.0 - w)[:, None] * int_pmf(qb, k), axis=1)
    out = np.empty((cdf.shape[0], len(CRPS_TAUS)), dtype=np.int16)
    for i in range(0, cdf.shape[0], 4096):
        c = cdf[i : i + 4096]
        out[i : i + 4096] = (c[:, :, None] < (CRPS_TAUS - 1e-12)[None, None, :]).sum(axis=1)
    return out


def clustered(delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    c = paired_score_delta_ci(
        delta, np.zeros_like(delta), n_boot=n_boot, seed=BOOT_SEED, cluster_ids=gid
    )
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def thr_ll(qi: np.ndarray, y: np.ndarray, n: np.ndarray) -> np.ndarray:
    p = np.clip((qi >= n[:, None]).mean(axis=1), EPS, 1.0 - EPS)
    ev = y >= n
    return np.asarray(-np.where(ev, np.log(p), np.log(1.0 - p)))


def calibration(qi: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    from nba.eval.lower_tail_eval import guard

    pit = randomized_pit(qi.astype(float), y, 0)
    return {
        "bias": float((y - qi.mean(axis=1)).mean()),
        "cov80": float(((pit > 0.10) & (pit <= 0.90)).mean()),
        "guard": guard(pit),
    }


def team_share(delta: np.ndarray, team: np.ndarray) -> dict[str, Any]:
    """Share of the summed gain (-delta) per team; largest first. Only teams with a gain count."""
    gain = -delta
    tot = float(gain.sum())
    teams = np.unique(team)
    per = {int(t): float(gain[team == t].sum()) for t in teams}
    top = max(per, key=lambda t: per[t])
    share = per[top] / tot if tot > 0 else float("nan")
    return {"top_team": int(top), "top_share": float(share), "total_gain": tot}


def auc(score: np.ndarray, label: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(label, score))


def game_hash_mod3(game_ids: np.ndarray) -> np.ndarray:
    u, inv = np.unique(game_ids, return_inverse=True)
    h = np.array([int(hashlib.sha256(g.encode()).hexdigest(), 16) % 3 for g in u])
    return np.asarray(h[inv])


def boot_auc(
    score: np.ndarray, label: np.ndarray, gid: np.ndarray, n_boot: int = 200, seed: int = 0
) -> tuple[float, float, float]:
    """AUC and a game-clustered 95% percentile interval (resample games)."""
    point = auc(score, label)
    rng = np.random.default_rng(seed)
    u, inv = np.unique(gid, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(len(u) + 1))
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(u), len(u))
        idx = np.concatenate([order[bounds[g] : bounds[g + 1]] for g in pick])
        lab = label[idx]
        if lab.min() == lab.max():
            continue
        vals.append(auc(score[idx], lab))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return point, float(lo), float(hi)


# --------------------------------------------------------------------------- era probes


def p_in_features(ctx: np.ndarray, names: list[str]) -> tuple[np.ndarray, list[str]]:
    """0/1 'channel exactly zero' indicators of every non-report context channel."""
    keep = [i for i, n in enumerate(names) if n not in REPORT_CTX]
    return (ctx[:, keep] == 0).astype(np.float32), [names[i] for i in keep]


def pair_auc(
    x: np.ndarray, era: np.ndarray, gid: np.ndarray, a: int, b: int, kind: str
) -> dict[str, float]:
    """Game-level hold-out AUC of a classifier telling era ``a`` from era ``b`` (label 1 = a)."""
    m = (era == a) | (era == b)
    xs, ys, gs = x[m], (era[m] == a).astype(int), gid[m]
    test = game_hash_mod3(gs) == 0
    clf: Any
    if kind == "lgbm":
        import lightgbm as lgb

        clf = lgb.LGBMClassifier(n_estimators=100, num_leaves=15, random_state=0, verbose=-1)
    else:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=300))
    clf.fit(xs[~test], ys[~test])
    sc = clf.predict_proba(xs[test])[:, 1]
    p, lo, hi = boot_auc(sc, ys[test], gs[test])
    return {"auc": p, "lo": lo, "hi": hi, "n_test": int(test.sum())}


def era_probe(
    x: np.ndarray, season: np.ndarray, gid: np.ndarray, kind: str, limit_rule: str
) -> dict[str, Any]:
    """History (2019, 2020, pooled) vs 2022 and the placebo pairs among current seasons.

    ``limit_rule`` 'pin': limit = max(placebo AUC, 0.505) (flag = interval lower end above it; each
    placebo is checked against the other two). 'pemb': limit = AUC(2022 v 2024) + 0.02."""
    era = season.copy()
    pooled = np.where(np.isin(season, (2019, 2020)), 1999, season)
    res: dict[str, Any] = {}
    for name, (a, b, e) in {
        "2019v2022": (2019, 2022, era),
        "2020v2022": (2020, 2022, era),
        "hist_v_2022": (1999, 2022, pooled),
        "2022v2023": (2022, 2023, era),
        "2022v2024": (2022, 2024, era),
        "2023v2024": (2023, 2024, era),
    }.items():
        res[name] = pair_auc(x, e, gid, a, b, kind)
    plac = ["2022v2023", "2022v2024", "2023v2024"]
    flags = {}
    if limit_rule == "pin":
        lim_h = max(max(res[p]["auc"] for p in plac), PIN_FLOOR)
        for h in ("2019v2022", "2020v2022", "hist_v_2022"):
            flags[h] = bool(res[h]["lo"] > lim_h)
        for p in plac:
            lim = max(max(res[o]["auc"] for o in plac if o != p), PIN_FLOOR)
            flags[p] = bool(res[p]["lo"] > lim)
        limit = lim_h
    else:
        limit = res["2022v2024"]["auc"] + PEMB_MARGIN
        for h in ("2019v2022", "2020v2022", "hist_v_2022"):
            flags[h] = bool(res[h]["lo"] > limit)
        for p in plac:
            flags[p] = False
    res["limit"] = float(limit)
    res["flags"] = flags
    res["flagged"] = bool(any(flags[h] for h in ("2019v2022", "2020v2022", "hist_v_2022")))
    res["placebo_self_flag"] = bool(any(flags[p] for p in plac))
    return res


# --------------------------------------------------------------------------- data


def load_a0() -> dict[str, dict[str, Any]]:
    """Per stat: keys, labels and the CONTINUOUS 199-grids of production (off) and the mixture."""
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    out: dict[str, dict[str, Any]] = {}
    for st in STATS:
        comp = load_components(st, LT_DIR)
        if not (
            np.array_equal(comp["row_gid"], pic["row_gid"])
            and np.array_equal(comp["row_pid"], pic["row_pid"])
        ):
            raise RuntimeError("pi components not row-aligned")
        if int(comp["row_season"].max()) > MAX_SEASON:
            raise RuntimeError("season > 2024 in the lower-tail components")
        out[st] = {
            "game_id": comp["row_gid"].astype(str),
            "player_id": comp["row_pid"].astype(np.int64),
            "season": comp["row_season"].astype(np.int64),
            "y": comp["row_y"].astype(float),
            "minutes": comp["row_minutes"].astype(float),
            "starter10": comp["row_starter10"].astype(float),
            "starter_act": comp["row_starter_act"].astype(float),
            "off": arm_grid("off", comp, pic, st),
            "mix": arm_grid("mixture", comp, pic, st),
        }
    return out


def cur_meta() -> pl.DataFrame:
    return (
        pl.read_parquet(EXPORT_DIR / "seq_ensemble_cur_meta.parquet")
        .select(
            "game_id", "player_id", "team_id", "n_prior", "team_game_no", "has_report", "n_rep_out"
        )
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )


def g_scores(a0_pts: dict[str, Any]) -> np.ndarray:
    """g = P(minutes >= 5 | plays) from the production minutes path, per A0 pts row."""
    from research.eval import minutes_v2_eval as M

    w = M.load_world()
    pm = M.production_minutes_table(w["sc"])
    base = pl.DataFrame({"game_id": a0_pts["game_id"], "player_id": a0_pts["player_id"]}).join(
        pm, on=["game_id", "player_id"], how="left"
    )
    if base["p_mu"].null_count():
        raise RuntimeError("production minutes table misses A0 rows")
    return survival_at(base["p_mu"].to_numpy(), base["p_sigma"].to_numpy())


# --------------------------------------------------------------------------- gate stage


def stage_gate(allow_draft: bool, sha: str | None) -> dict[str, Any]:
    require_frozen(sha, allow_draft)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {"doc_status": doc_status(), "doc_sha256": frozen_sha()}
    cur = np.load(EXPORT_DIR / "seq_ensemble_cur.npz", allow_pickle=True)
    his = np.load(EXPORT_DIR / "seq_ensemble_hist.npz", allow_pickle=True)
    names = [str(x) for x in cur["ctx_features"]]
    pos = [names.index(c) for c in ("pos_g", "pos_f", "pos_c")]
    h_none = his["ctx"][:, pos].sum(axis=1) == 0
    c_none = cur["ctx"][:, pos].sum(axis=1) == 0
    rec["G3b_position"] = {
        "history_rows": int(len(h_none)),
        "history_no_position": int(h_none.sum()),
        "history_share": float(h_none.mean()),
        "current_share": float(c_none.mean()),
        "pass": bool(h_none.sum() == 0 or h_none.mean() <= 0.001),
    }
    rep = [names.index(c) for c in REPORT_CTX]
    rec["G3a_report_tokens"] = {
        "history_nonzero_report_channels": int((np.abs(his["ctx"][:, rep]) > 0).sum()),
        "current_reportless_rows": int((cur["ctx"][:, names.index("has_report")] == 0).sum()),
        "pass": bool((np.abs(his["ctx"][:, rep]) > 0).sum() == 0),
    }
    # P-in: zero-pattern of the non-report context channels, rows with n_prior >= 5
    xs, gids, seas = [], [], []
    for d in (his, cur):
        m = d["n_prior"] >= 5
        f, _ = p_in_features(d["ctx"][m], names)
        xs.append(f)
        gids.append(d["game_id"][m].astype(str))
        seas.append(d["season"][m].astype(int))
    x, gid, sea = np.concatenate(xs), np.concatenate(gids), np.concatenate(seas)
    _log(f"P-in rows {len(x)}")
    pin = era_probe(x, sea, gid, "lgbm", "pin")
    rec["P_in"] = {**pin, "pass": bool(not pin["flagged"])}
    # g: AUC against the realised >= 5 minutes, counts
    a0 = load_a0()
    g = g_scores(a0["pts"])
    np.savez(
        CACHE / "g_pts.npz", game_id=a0["pts"]["game_id"], player_id=a0["pts"]["player_id"], g=g
    )
    y5 = (a0["pts"]["minutes"] >= 5).astype(int)
    gt: dict[str, Any] = {}
    for s in SEASONS:
        k = a0["pts"]["season"] == s
        gt[str(s)] = {
            "n": int(k.sum()),
            "rows_lt5": int((y5[k] == 0).sum()),
            "auc": auc(g[k], y5[k]),
            "q": [float(v) for v in np.quantile(g[k], [0.01, 0.05, 0.1, 0.5])],
        }
    rec["G4_gate_score"] = {
        **gt,
        "pass": bool(
            all(v["auc"] >= G_AUC_FLOOR and v["rows_lt5"] >= G_MIN_SHORT_ROWS for v in gt.values())
        ),
    }
    _log("g8 NaN audit")
    rec["G8_missingness"] = nan_audit(a0["pts"])
    rec["gate_pass"] = bool(
        rec["G8_missingness"]["pass"]
        and rec["G3b_position"]["pass"]
        and rec["G3a_report_tokens"]["pass"]
        and rec["P_in"]["pass"]
        and rec["G4_gate_score"]["pass"]
    )
    (OUT_DIR / ("gate_draft.json" if allow_draft else "gate.json")).write_text(
        json.dumps(rec, indent=1, default=float)
    )
    return rec


def nan_audit(a0_pts: dict[str, Any]) -> dict[str, Any]:
    """g8: NULL (NaN before the export's ``nan_to_num``) share of every context channel by realised
    minutes bucket on the scored 2023-24 rows, and of the history block vs current, from the real builders."""
    from research.features import player_sequences as P
    from research.features import seq_ensemble_export as X

    cap: dict[str, np.ndarray] = {}
    orig = np.nan_to_num

    def spy(a: Any, *args: Any, **kw: Any) -> Any:
        arr = np.asarray(a)
        if arr.ndim == 2 and arr.shape[1] == len(P.CTX_FEATURES):
            cap["nan"] = np.isnan(arr)
        return orig(a, *args, **kw)

    np.nan_to_num = spy  # type: ignore[assignment]
    try:
        cur = X.build_current()
        cur_nan = cap["nan"]
        X.build_history()
        his_nan = cap["nan"]
    finally:
        np.nan_to_num = orig
    names = list(P.CTX_FEATURES)
    meta = pl.from_pandas(cur.meta[["game_id", "player_id", "season", "game_date"]]).with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("game_id").cast(pl.Utf8)
    )
    mins = pl.DataFrame(
        {
            "game_id": a0_pts["game_id"],
            "player_id": a0_pts["player_id"],
            "minutes": a0_pts["minutes"],
        }
    )
    j = meta.with_row_index("i").join(mins, on=["game_id", "player_id"], how="inner")
    idx = j["i"].to_numpy()
    bucket = np.where(j["minutes"].to_numpy() < 5, 0, np.where(j["minutes"].to_numpy() <= 20, 1, 2))
    month = j["game_date"].dt.strftime("%Y-%m").to_numpy()
    pid = j["player_id"].to_numpy()
    out: dict[str, Any] = {"rows": int(len(idx)), "channels": {}}
    worst = 0.0
    for c, name in enumerate(names):
        col = cur_nan[idx, c].astype(float)
        rates = [float(col[bucket == b].mean()) if (bucket == b).any() else 0.0 for b in range(3)]
        gap = (max(rates) - min(rates)) * 100.0
        const_p = (
            bool(all(len(set(col[pid == p])) <= 1 for p in np.unique(pid[col > 0])))
            if col.any()
            else True
        )
        const_m = (
            bool(all(len(set(col[month == m])) <= 1 for m in np.unique(month)))
            if col.any()
            else True
        )
        exempt = bool(col.any() and (const_p or const_m))
        if not exempt:
            worst = max(worst, gap)
        out["channels"][name] = {
            "null_rate_by_bucket": rates, "gap_pp": gap, "exempt": exempt,
            "hist_null_rate": float(his_nan[:, c].mean()), "cur_null_rate": float(cur_nan[:, c].mean()),
        }  # fmt: skip
    out["max_nonexempt_gap_pp"] = worst
    out["pass"] = bool(worst <= NULL_GAP_PP)
    return out


# --------------------------------------------------------------------------- base frames


def build_base(a0: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per stat: A0 arrays plus slice columns (team, n_prior, n_rep_out, tip hour) joined by key."""
    from nba.features.game_tipoff import build_game_tipoff

    cm = cur_meta()
    tips = build_game_tipoff().select(
        "game_id",
        (pl.col("tip_et").dt.hour() + pl.col("tip_et").dt.minute() / 60.0).alias("tip_hour"),
    )
    out: dict[str, dict[str, Any]] = {}
    for st, d in a0.items():
        base = (
            pl.DataFrame({"game_id": d["game_id"], "player_id": d["player_id"]})
            .join(cm, on=KEYS, how="left")
            .join(tips, on="game_id", how="left")
        )
        if base["team_id"].null_count():
            raise RuntimeError("scored rows missing from the SEQ export meta")
        out[st] = {
            **d,
            "team_id": base["team_id"].to_numpy(),
            "n_prior": base["n_prior"].to_numpy(),
            "team_game_no": base["team_game_no"].to_numpy(),
            "has_report": base["has_report"].to_numpy(),
            "n_rep_out": base["n_rep_out"].to_numpy(),
            "tip_hour": base["tip_hour"].fill_null(float("nan")).to_numpy(),
            "a0_int": to_integer_support(d["mix"]).astype(np.int16),
            "off_int": to_integer_support(d["off"]).astype(np.int16),
        }
    return out


def slice_masks(b: dict[str, Any], rows: np.ndarray) -> dict[str, np.ndarray]:
    g = np.array([x[:3] for x in b["game_id"][rows]])
    mn = b["minutes"][rows]
    return {
        "all": np.ones(len(rows), dtype=bool),
        "regular": g == "002",
        "postseason": g != "002",
        "starter": b["starter10"][rows] >= 0.5,
        "bench": b["starter10"][rows] < 0.5,
        "cold_start": b["n_prior"][rows] < 20,
        "first15": b["team_game_no"][rows] < 15,
        "teammate_out": b["n_rep_out"][rows] >= 1,
        "minutes<5": mn < 5,
        "minutes5-20": (mn >= 5) & (mn <= 20),
        "minutes>20": mn > 20,
        "tip>=19ET(proxy-safe)": np.nan_to_num(b["tip_hour"][rows], nan=-1.0) >= PROXY_HOUR_ET,
    }


GUARD_SLICES = (
    "regular", "postseason", "starter", "bench", "cold_start", "first15", "teammate_out",
)  # fmt: skip
DESCRIPTIVE_SLICES = ("minutes<5", "minutes5-20", "minutes>20")


def dk_rows(b: dict[str, Any], rows: np.ndarray, season: int) -> tuple[np.ndarray, np.ndarray]:
    """Positions (into ``rows``) and lines of the DK T-60 main lines inside the scored rows."""
    dk = pl.read_parquet(DK_CACHE).filter(pl.col("season") == season)
    cur = pl.DataFrame(
        {
            "game_id": b["game_id"][rows],
            "player_id": b["player_id"][rows],
            "pos": np.arange(len(rows)),
        }
    )
    j = cur.join(dk.select("game_id", "player_id", "line"), on=KEYS, how="inner")
    return j["pos"].to_numpy(), j["line"].to_numpy()


def evaluate_arm(
    stat: str,
    b: dict[str, Any],
    rows: np.ndarray,
    qi: np.ndarray,
    ci_slices: bool,
    dk: bool,
) -> dict[str, dict[str, Any]]:
    """Per season: delta vs A0 on ``rows`` (index into the base arrays), guards' raw ingredients."""
    out: dict[str, dict[str, Any]] = {}
    for s in SEASONS:
        m = b["season"][rows] == s
        r = rows[m]
        q = qi[m]
        y = b["y"][r]
        gid = b["game_id"][r]
        a0q = b["a0_int"][r]
        c_arm = crps_from_quantiles(q.astype(float), y)
        c_a0 = crps_from_quantiles(a0q.astype(float), y)
        d = c_arm - c_a0
        rec: dict[str, Any] = {
            "n": int(len(r)),
            "n_games": int(len(np.unique(gid))),
            "crps_arm": float(c_arm.mean()),
            "crps_a0": float(c_a0.mean()),
            "delta": clustered(d, gid),
            "p": boot_p_value(d, gid, N_BOOT, BOOT_SEED),
            "calibration": calibration(q, y),
        }
        masks = slice_masks(b, r)
        sl: dict[str, Any] = {}
        for name, mk in masks.items():
            n = int(mk.sum())
            e: dict[str, Any] = {"n": n}
            if n >= MIN_SLICE_N or name == "all":
                e["point"] = float(d[mk].mean())
                if ci_slices:
                    e["delta"] = clustered(d[mk], gid[mk])
            sl[name] = e
        rec["slices"] = sl
        ts = team_share(d, b["team_id"][r])
        keep = b["team_id"][r] != ts["top_team"]
        rec["team_share"] = {**ts, "delta_without_top": float(d[keep].mean())}
        if dk and stat == "pts":
            pos, line = dk_rows(b, rows[m], s)
            nthr = np.ceil(line)
            l_arm, l_a0 = (
                thr_ll(q[pos].astype(float), y[pos], nthr),
                thr_ll(a0q[pos].astype(float), y[pos], nthr),
            )
            rec["tll_dk"] = {"n": int(len(pos)), **clustered(l_arm - l_a0, gid[pos])}
            fixed = {}
            for t in THRESHOLDS:
                na = np.full(len(y), float(t))
                fixed[str(t)] = float(
                    (thr_ll(q.astype(float), y, na) - thr_ll(a0q.astype(float), y, na)).mean()
                )
            rec["tll_fixed"] = fixed
        rec["_d"] = d
        rec["_gid"] = gid
        out[str(s)] = rec
    return out


def guards_for(season_rec: dict[str, Any], stat: str, e1_like: bool) -> dict[str, bool]:
    """g1-g5 for one season of one cell (g6 knockout/shuffle and g7/g8 are added by the caller)."""
    cal = season_rec["calibration"]
    g: dict[str, bool] = {
        "g1_calibration": bool(
            abs(cal["bias"]) <= MAX_BIAS and COV_LO <= cal["cov80"] <= COV_HI and cal["guard"]["ok"]
        ),
        "g2_slices": all(
            season_rec["slices"][k].get("point", -1.0) <= SLICE_WORSE
            for k in GUARD_SLICES
            if season_rec["slices"][k]["n"] >= MIN_SLICE_N
        ),
        "g3_concentration": bool(
            (not np.isfinite(season_rec["team_share"]["top_share"]))
            or (
                season_rec["team_share"]["top_share"] <= TEAM_SHARE_MAX
                and season_rec["team_share"]["delta_without_top"] <= KO_FLOOR
            )
        ),
    }
    if stat == "pts" and "tll_dk" in season_rec:
        g["g4_thresholds"] = bool(
            season_rec["tll_dk"]["point"] <= TLL_MARGIN
            and all(v <= TLL_FIXED_MAX for v in season_rec["tll_fixed"].values())
        )
    if e1_like:
        sp = season_rec["slices"]["tip>=19ET(proxy-safe)"]
        g["g5_proxy_safe"] = bool(sp["n"] >= MIN_SLICE_N and sp.get("point", 1.0) <= FLOOR)
    return g


# --------------------------------------------------------------------------- cpu stage (E1)


def e1_arrays(a0pts: dict[str, Any], g: np.ndarray) -> dict[str, np.ndarray]:
    a1 = np.load(A1_ALIGNED, allow_pickle=True)
    key_a1 = {
        (str(a), int(p)): i
        for i, (a, p) in enumerate(zip(a1["game_id"], a1["player_id"], strict=True))
    }
    pos = np.array(
        [
            key_a1.get((str(a), int(p)), -1)
            for a, p in zip(a0pts["game_id"], a0pts["player_id"], strict=True)
        ]
    )
    rows = np.flatnonzero(pos >= 0)
    a0_only = {int(s): int(((pos < 0) & (a0pts["season"] == s)).sum()) for s in SEASONS}
    a1_int = to_integer_support(a1["q"][pos[rows]].astype(float)).astype(np.int16)
    a0_int = to_integer_support(a0pts["mix"][rows]).astype(np.int16)
    gg = g[rows]
    return {
        "rows": rows,
        "a1_int": a1_int,
        "a0_int": a0_int,
        "g": gg,
        "e1": mix_integer_quantiles(a1_int, a0_int, gg),
        "oracle": mix_integer_quantiles(
            a1_int, a0_int, (a0pts["minutes"][rows] >= 5).astype(float)
        ),
        "hard": mix_integer_quantiles(a1_int, a0_int, (gg >= GATE_HARD).astype(float)),
        "a0_only_2023": np.array([a0_only[2023]]),
        "a0_only_2024": np.array([a0_only[2024]]),
    }


def stage_cpu(sha: str | None, allow_draft: bool) -> dict[str, Any]:
    require_frozen(sha, allow_draft)
    gate: dict[str, Any] | None = (
        json.loads((OUT_DIR / "gate.json").read_text())
        if (OUT_DIR / "gate.json").exists()
        else None
    )
    if not allow_draft and not (gate and gate["gate_pass"]):
        raise SystemExit("STOP: gate.json missing or not passing")
    CACHE.mkdir(parents=True, exist_ok=True)
    a0 = load_a0()
    base = build_base(a0)
    gz = np.load(CACHE / "g_pts.npz", allow_pickle=True)
    if not (
        np.array_equal(gz["game_id"], a0["pts"]["game_id"])
        and np.array_equal(gz["player_id"], a0["pts"]["player_id"])
    ):
        raise RuntimeError("cached g is not aligned with A0")
    arr = e1_arrays(a0["pts"], gz["g"])
    np.savez_compressed(CACHE / "e1_pts.npz", **arr)  # type: ignore[arg-type]
    res: dict[str, Any] = {
        "a0_only": {"2023": int(arr["a0_only_2023"][0]), "2024": int(arr["a0_only_2024"][0])}
    }
    for name in ("e1", "oracle", "hard"):
        ev = evaluate_arm("pts", base["pts"], arr["rows"], arr[name], ci_slices=True, dk=True)
        res[name] = {
            s: {k: v for k, v in r.items() if not k.startswith("_")} for s, r in ev.items()
        }
        for s in ("2023", "2024"):
            _log(f"{name} {s} delta {ev[s]['delta']}")
    (OUT_DIR / "e1_results.json").write_text(json.dumps(res, indent=1, default=float))
    return res


# --------------------------------------------------------------------------- score stage (SEQ arms)

SEQ_PROGRAMS = ("E2", "E2K", "E2S", "E3", "E3K", "E3S")
VARIANTS = ("alone", "b50", "learned")


def load_seq(run_dir: Path, prog: str, base: dict[str, dict[str, Any]]) -> dict[str, np.ndarray]:
    """Per stat the continuous 199-grid of ``prog`` aligned to the A0 rows (error if any key is missing)."""
    z = np.load(run_dir / f"oof_{prog}.npz", allow_pickle=True)
    if int(z["season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in a SEQ output")
    kidx = {
        (str(g), int(p)): i
        for i, (g, p) in enumerate(zip(z["game_id"], z["player_id"], strict=True))
    }
    out: dict[str, np.ndarray] = {}
    for si, st in enumerate(STATS):
        b = base[st]
        pos = np.array(
            [
                kidx.get((str(g), int(p)), -1)
                for g, p in zip(b["game_id"], b["player_id"], strict=True)
            ]
        )
        if (pos < 0).any():
            raise RuntimeError(
                f"{prog}/{st}: {(pos < 0).sum()} A0 rows missing from the SEQ output"
            )
        out[st] = ext19(z["q"][pos, si, :])
    return out


def learned_pool(b: dict[str, Any], stat: str, seq199: np.ndarray) -> np.ndarray:
    """Walk-forward softmax gate over [A0, SEQ] (30-day blocks, min 3000 rows, earlier rows only);
    rows before the first fit use 0.5 / 0.5. Returns the pooled continuous 199-grid."""
    import pandas as pd

    from research.stack.data import build_stack_data
    from research.stack.router import SoftmaxGate, WalkForwardConfig, walk_forward

    n = len(b["y"])
    date = pd.to_datetime(
        pl.DataFrame({"g": b["game_id"]})
        .join(
            pl.read_parquet(EXPORT_DIR / "seq_ensemble_cur_meta.parquet")
            .select(pl.col("game_id"), pl.col("game_date").cast(pl.Date))
            .unique("game_id"),
            left_on="g",
            right_on="game_id",
            how="left",
        )["game_date"]
        .to_numpy()
    )
    keys = pd.DataFrame(
        {
            "game_id": b["game_id"],
            "player_id": b["player_id"],
            "game_date": date,
            "season": b["season"],
        }
    )
    frames = {
        "a0": keys.assign(q_grid=list(grid19(b["mix"]))),
        "seq": keys.assign(q_grid=list(grid19(seq199))),
    }
    outcomes = keys[["game_id", "player_id"]].assign(y=b["y"])
    ctx = keys[["game_id", "player_id"]].assign(
        log_n_prior=np.log1p(b["n_prior"]), starter10=b["starter10"], n_rep_out=b["n_rep_out"],
        has_report=b["has_report"], team_game_no=b["team_game_no"],
    )  # fmt: skip
    data = build_stack_data(stat, frames, outcomes, ctx, max_season=2024)
    if len(data.y) != n:
        raise RuntimeError("stack alignment dropped rows")
    res = walk_forward(data, SoftmaxGate, WalkForwardConfig(block_days=30, min_train_rows=3000))
    w = np.where(np.isnan(res.weights), 0.5, res.weights)
    pooled = w[:, :1] * b["mix"] + w[:, 1:2] * seq199
    return np.asarray(pooled)


def seq_arms(
    base: dict[str, dict[str, Any]], seq: dict[str, np.ndarray], stat: str, learned: bool = True
) -> dict[str, np.ndarray]:
    b = base[stat]
    arms = {
        "alone": to_integer_support(seq[stat]).astype(np.int16),
        "b50": to_integer_support(0.5 * b["mix"] + 0.5 * seq[stat]).astype(np.int16),
    }
    if learned:
        arms["learned"] = to_integer_support(learned_pool(b, stat, seq[stat])).astype(np.int16)
    return arms


def p_emb(run_dir: Path) -> dict[str, Any]:
    z = np.load(run_dir / "states_E3.npz", allow_pickle=True)
    return era_probe(
        z["z"].astype(np.float32),
        z["season"].astype(int),
        z["game_id"].astype(str),
        "logreg",
        "pemb",
    )


def stage_score(run_dir: Path, sha: str | None, allow_draft: bool) -> dict[str, Any]:
    sha_ok = require_frozen(sha, allow_draft)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate: dict[str, Any] = (
        json.loads((OUT_DIR / "gate.json").read_text())
        if (OUT_DIR / "gate.json").exists()
        else {"gate_pass": False}
    )
    if not allow_draft and not gate["gate_pass"]:
        raise SystemExit("STOP: gate.json missing or not passing")
    a0 = load_a0()
    base = build_base(a0)
    arms: dict[tuple[str, str, str], np.ndarray] = {}
    for prog in SEQ_PROGRAMS:
        _log(f"align {prog}")
        seq = load_seq(run_dir, prog, base)
        seqs_cache[prog] = seq
        for st in STATS:
            for v, q in seq_arms(base, seq, st).items():
                arms[(prog, v, st)] = q
    pemb = p_emb(run_dir)
    e3_eligible = not pemb["flagged"]
    # E1 (cached by the cpu stage) and E4
    e1 = np.load(CACHE / "e1_pts.npz", allow_pickle=True)
    rows_e1 = e1["rows"]
    # best SEQ for E4: lower 2023 pts b50 CRPS (select season only); E3 only if its era guard passes
    m23 = base["pts"]["season"] == 2023

    def crps23(prog: str) -> float:
        return float(
            crps_from_quantiles(
                arms[(prog, "b50", "pts")][m23].astype(float), base["pts"]["y"][m23]
            ).mean()
        )

    cands = ["E2"] + (["E3"] if e3_eligible else [])
    best = min(cands, key=lambda p: (crps23(p), p != "E2"))
    e4 = to_integer_support(
        0.5 * e1["e1"].astype(float) + 0.5 * seqs_cache[best]["pts"][rows_e1]
    ).astype(np.int16)
    cells: dict[str, dict[str, Any]] = {}
    ev: dict[str, dict[str, dict[str, Any]]] = {}

    def run_cell(
        name: str, stat: str, rows: np.ndarray, q: np.ndarray, e1_like: bool = False
    ) -> None:
        ev[name] = evaluate_arm(stat, base[stat], rows, q, ci_slices=False, dk=True)
        cells[name] = {"stat": stat, "e1_like": e1_like}

    all_rows = {st: np.arange(len(base[st]["y"])) for st in STATS}
    run_cell("E1|pts", "pts", rows_e1, e1["e1"], True)
    run_cell("E4|pts", "pts", rows_e1, e4, True)
    for prog in ("E2", "E3"):
        for v in VARIANTS:
            for st in STATS:
                run_cell(f"{prog}|{v}|{st}", st, all_rows[st], arms[(prog, v, st)])
    for prog in ("E2K", "E3K", "E2S", "E3S"):
        for v in VARIANTS:
            for st in STATS:
                ev[f"{prog}|{v}|{st}"] = evaluate_arm(
                    st, base[st], all_rows[st], arms[(prog, v, st)], False, False
                )
    # BH per season over the 26 cells
    names = list(cells)
    fam: dict[str, dict[str, float]] = {}
    for s in ("2023", "2024"):
        adj = bh_adjust(np.array([ev[n][s]["p"] for n in names]))
        fam[s] = dict(zip(names, (float(a) for a in adj), strict=True))
    verdicts: dict[str, Any] = {}
    for n in names:
        stat, e1_like = cells[n]["stat"], cells[n]["e1_like"]
        parts = n.split("|")
        prog = parts[0]
        seasons: dict[str, Any] = {}
        for s in ("2023", "2024"):
            r = ev[n][s]
            g = guards_for(r, stat, e1_like)
            g["g8_missingness"] = bool(gate.get("G8_missingness", {}).get("pass", False))
            core = {
                "floor": r["delta"]["point"] <= FLOOR,
                "ci_hi<0": r["delta"]["hi"] < 0,
                "bh<0.05": fam[s][n] < 0.05,
            }
            seasons[s] = {
                "core": core,
                "guards": g,
                "pass": bool(all(core.values()) and all(g.values())),
                "bh_p": fam[s][n],
            }
        confirmed = all(seasons[s]["pass"] for s in seasons)
        harm = any(
            ev[n][s]["delta"]["point"] >= -FLOOR and ev[n][s]["delta"]["lo"] > 0
            for s in ("2023", "2024")
        )
        v = (
            "HARM"
            if harm
            else (
                "CONFIRMED"
                if confirmed
                else ("PASS_2023_ONLY" if seasons["2023"]["pass"] else "FAIL")
            )
        )
        extra: dict[str, Any] = {}
        if v == "CONFIRMED" and prog in ("E2", "E3"):
            var, st = parts[1], parts[2]
            ko = [ev[f"{prog}K|{var}|{st}"][s]["delta"]["point"] for s in ("2023", "2024")]
            sh = [ev[f"{prog}S|{var}|{st}"][s]["delta"]["point"] for s in ("2023", "2024")]
            extra = {"knockout_points": ko, "shuffle_points": sh}
            if any(x > KO_FLOOR for x in ko):
                v = "FAIL_KNOCKOUT"
            elif any(x <= SHUFFLE_MIN for x in sh):
                v = "VOID_SHUFFLE"
            elif prog == "E3" and not e3_eligible:
                v = "FAIL_ERA"
        verdicts[n] = {"verdict": v, "seasons": seasons, **extra}
    # secondary D: E3 b50 - E2 b50, and the knockout contrast
    dres: dict[str, Any] = {}
    for s in ("2023", "2024"):
        ps, recs = [], {}
        for st in STATS:
            b = base[st]
            m = b["season"] == int(s)
            y, gid = b["y"][m], b["game_id"][m]
            d = crps_from_quantiles(
                arms[("E3", "b50", st)][m].astype(float), y
            ) - crps_from_quantiles(arms[("E2", "b50", st)][m].astype(float), y)
            dk_ = crps_from_quantiles(
                arms[("E3K", "b50", st)][m].astype(float), y
            ) - crps_from_quantiles(arms[("E2K", "b50", st)][m].astype(float), y)
            recs[st] = {
                "E3-E2": clustered(d, gid),
                "E3K-E2K": clustered(dk_, gid),
                "p": boot_p_value(d, gid, N_BOOT, BOOT_SEED),
            }
            ps.append(recs[st]["p"])
        adj = bh_adjust(np.array(ps))
        for st, a in zip(STATS, adj, strict=True):
            recs[st]["bh_p"] = float(a)
        dres[s] = recs
    d_pass = {
        st: bool(
            all(
                dres[s][st]["E3-E2"]["point"] <= KO_FLOOR
                and dres[s][st]["E3-E2"]["hi"] < 0
                and dres[s][st]["bh_p"] < 0.05
                and dres[s][st]["E3K-E2K"]["point"] <= KO_FLOOR
                for s in ("2023", "2024")
            )
        )
        for st in STATS
    }
    # E4 additivity (descriptive points)
    add = {}
    for s in ("2023", "2024"):
        m = base["pts"]["season"][rows_e1] == int(s)
        r = rows_e1[m]
        y = base["pts"]["y"][r]
        c4 = crps_from_quantiles(e4[m].astype(float), y).mean()
        c1 = crps_from_quantiles(e1["e1"][m].astype(float), y).mean()
        cs = crps_from_quantiles(arms[(best, "b50", "pts")][r].astype(float), y).mean()
        add[s] = {"E4-E1": float(c4 - c1), "E4-best_b50": float(c4 - cs)}
    out = {
        "rule_sha256": sha_ok, "best_seq_for_E4": best, "e3_eligible": e3_eligible, "p_emb": pemb,
        "e2o_descriptive": "see ensemble_eval e2o", "cells": {
            n: {
                "verdict": verdicts[n]["verdict"],
                "2023": {k: v for k, v in ev[n]["2023"].items() if not k.startswith("_")},
                "2024": {k: v for k, v in ev[n]["2024"].items() if not k.startswith("_")},
                "bh_p": {"2023": fam["2023"][n], "2024": fam["2024"][n]},
                "detail": verdicts[n],
            } for n in names
        },
        "secondary_D": {"by_season": dres, "pass": d_pass}, "e4_additive_points": add,
    }  # fmt: skip
    (OUT_DIR / "score_results.json").write_text(json.dumps(out, indent=1, default=float))
    return out


seqs_cache: dict[str, dict[str, np.ndarray]] = {}


def smoke() -> dict[str, Any]:
    """Plumbing on synthetic arrays: helper identities only (no data, no model)."""
    rng = np.random.default_rng(0)
    q19 = np.sort(rng.gamma(2.0, 3.0, size=(50, 19)), axis=1)
    q199 = ext19(q19)
    assert (np.diff(q199, axis=1) >= -1e-12).all()
    assert np.allclose(grid19(q199)[:, 9], q19[:, 9], atol=0.15 * q19[:, 9].max())
    qa = np.sort(rng.integers(0, 40, size=(20, 199)), axis=1)
    qb = np.sort(rng.integers(0, 40, size=(20, 199)), axis=1)
    assert np.array_equal(mix_integer_quantiles(qa, qb, np.ones(20)), qa)
    assert np.array_equal(mix_integer_quantiles(qa, qb, np.zeros(20)), qb)
    return {"ok": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "cpu", "score", "smoke"])
    ap.add_argument("--sha", default=None, help="expected rule sha256 (frozen stages)")
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--run", default=None, help="pulled run dir (score)")
    a = ap.parse_args(argv)
    if a.stage == "smoke":
        print(json.dumps(smoke()))
        return 0
    if a.stage == "gate":
        r = stage_gate(a.allow_draft, a.sha)
        print(json.dumps({"gate_pass": r["gate_pass"]}))
        return 0 if r["gate_pass"] else 1
    if a.stage == "cpu":
        from research.eval.minutes_hazard_eval import HeavyLock

        with HeavyLock():
            stage_cpu(a.sha, a.allow_draft)
        return 0
    if a.run is None:
        raise SystemExit("--run <pulled run dir> is required for score")
    stage_score(Path(a.run), a.sha, a.allow_draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
