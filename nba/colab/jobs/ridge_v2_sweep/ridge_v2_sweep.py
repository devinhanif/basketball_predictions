# mypy: ignore-errors
# ruff: noqa: E501
"""ridge_v2 sweep (experiment 4): opponent-adjusted ridge variants inside the fixed exp-2 models.

PRE-REGISTERED PROTOCOL (full text: docs/RIDGE_V2.md; rule applied by nba/eval/ridge_v2_eval.py)
  * Data: ``ctxres_v2.parquet`` (+ ``feature_spec.json``) joined with ``ridge_v2.parquet`` (+
    ``ridge_v2_spec.json``, ``base_config.json``), seasons 2022-2024 (2022 = warm-up). Season 2025
    is refused. Walk-forward: every calendar-month block is predicted by models fit on rows dated
    strictly before the block.
  * Base models are HELD FIXED at the experiment-2 per-stat winners with the exp-2 tuned XGBoost
    params (``base_config.json``): pts = 19-quantile XGBoost (``xgb_v12_quantile`` recipe),
    reb/ast/fg3m = XGBoost count:poisson mean + NegBin dispersion (``xgb_v12_poisson_nb`` recipe).
  * An ARM changes only the ridge columns of v2 group (i): every arm uses v1 features + v2 groups
    a-h and j + the arm's ridge columns (``ridge_v2_spec.json``). Arms: ``exp2_ref`` (the exp-2
    ridge columns exactly as exported; NULL when tonight's minutes < 5, a known leak),
    ``ref_fixed`` (same estimator predicted for every row: PRIMARY reference), ``no_ridge`` and the
    variants r1..r6, ladders L1..L4/ALL, drop-one ablations and ``proxy_sel``.
  * Selection uses ONLY season-2023 blocks (per-stat CRPS in ``metrics.json``); season-2024 is
    report-only; the keep rule is applied locally by ``nba.eval.ridge_v2_eval``.
Sharding: ``NBA_ARMS=a,b`` or ``NBA_SHARD=k/n`` (disjoint arm subsets in concurrent sessions; the
eval merges run dirs by variant). Crash-safe: every finished arm is checkpointed.
Budget (env NBA_BUDGET): full (default, strict) | smoke (local CPU, 2% sample).
Seeds are logged; GPU XGBoost is not bit-reproducible.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

STATS = ("pts", "reb", "ast", "fg3m")
THR = {
    "pts": np.array([10, 15, 20, 25, 30]),
    "reb": np.array([4, 6, 8, 10]),
    "ast": np.array([2, 4, 6, 8]),
    "fg3m": np.array([1, 2, 3, 4]),
}
TAUS199 = (np.arange(1, 200) - 0.5) / 199
TAUS19 = np.arange(1, 20) / 20.0
SEED = 20261008
SELECT_SEASON, REPORT_SEASON, FROZEN_SEASON = 2023, 2024, 2025
PLOGMIN = 1e-4
EXPERIMENT_TAG = "ridge_v2_exp4"
CODE_VERSION = "ridge-v2-ckpt-1"
RIDGE_COL = re.compile(r"^rg_[0-9a-f]{8}_(p|o|ox|px|tm)_(pts|reb|ast|fg3m)$")
KNOWN_LEAK_ARMS = ("exp2_ref",)  # NULL-by-tonight's-minutes ridge (documented in docs/RIDGE_V2.md)
FAMILY = {"pts": "quantile", "reb": "poisson_nb", "ast": "poisson_nb", "fg3m": "poisson_nb"}

BUDGETS = {
    "full": dict(arm_s=3600, rounds=600, min_train=1500, min_cal=2000, est_arm_min=6.0),
    "smoke": dict(arm_s=300, rounds=30, min_train=250, min_cal=40, est_arm_min=0.2),
}


# ------------------------------------------------------------------------------ setup


def pip_install(pkg):
    try:
        __import__(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], check=True)


class Ctx:
    """Mutable globals for one run."""


C = Ctx()


def log(*a):
    print(f"[{time.monotonic() - C.t0:7.1f}s]", *a, flush=True)


def setup():
    C.t0 = time.monotonic()
    C.budget_name = os.environ.get("NBA_BUDGET", "full")
    C.smoke = C.budget_name == "smoke"
    C.B = BUDGETS[C.budget_name]
    pip_install("xgboost")
    import torch
    import xgboost as xgb

    C.torch = torch
    C.device = "cuda" if torch.cuda.is_available() else "cpu"
    if C.device == "cpu":
        torch.set_num_threads(1)
    print(f"BUDGET={C.budget_name} device={C.device} xgboost={xgb.__version__}")
    try:
        print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip())
    except OSError:
        print("nvidia-smi not found")
    C.gpu_name = torch.cuda.get_device_name(0) if C.device == "cuda" else "cpu"
    print("GPU:", C.gpu_name)
    if C.device == "cuda":  # prove XGBoost itself runs on the GPU
        t = time.monotonic()
        xgb.XGBRegressor(n_estimators=20, device="cuda", tree_method="hist").fit(
            np.random.rand(2000, 10), np.random.rand(2000)
        )
        print(f"xgboost device=cuda smoke fit OK ({time.monotonic() - t:.2f}s)")
    elif not C.smoke and os.environ.get("NBA_ALLOW_CPU") != "1":
        raise RuntimeError(
            "full budget needs a GPU runtime (Runtime > Change runtime type > T4/A100/L4); set NBA_ALLOW_CPU=1 to override"
        )
    C.rng = np.random.default_rng(SEED)


# ------------------------------------------------------------------------------ data


def load():
    path = Path(os.environ["NBA_PARQUET"])
    d = path.parent
    spec = json.loads((d / "feature_spec.json").read_text())
    rspec = json.loads((d / "ridge_v2_spec.json").read_text())
    base_cfg = json.loads((d / "base_config.json").read_text())
    df = pd.read_parquet(path)
    if int(df["season"].max()) >= FROZEN_SEASON:
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")
    rv = pd.read_parquet(d / "ridge_v2.parquet")
    if len(rv) != len(df):
        raise ValueError(f"ridge_v2.parquet rows {len(rv)} != ctxres_v2.parquet rows {len(df)}")
    df = df.merge(rv, on=["game_id", "player_id"], how="left", validate="one_to_one")
    if len(df) != len(rv):
        raise ValueError("key mismatch between ctxres_v2 and ridge_v2 parquet files")
    df = df.sort_values(["game_date", "game_id", "player_id"], kind="stable")
    if C.smoke:
        games = pd.Series(df["game_id"].unique()).sample(frac=0.02, random_state=SEED)
        df = df[df["game_id"].isin(set(games))]
    df = df.reset_index(drop=True)
    C.df, C.spec, C.rspec, C.base_cfg = df, spec, rspec, base_cfg
    C.dates = df["game_date"].to_numpy().astype("datetime64[D]")
    C.season = df["season"].to_numpy()
    C.gid = df["game_id"].to_numpy()
    ridge_i = {s: set(spec["v2_groups"]["i"][s]) for s in STATS}
    C.base_cols = {}
    for s in STATS:
        v1, v2 = list(spec["v1_features"][s]), list(spec["v2_features"][s])
        C.base_cols[s] = v1 + [c for c in v2 if c not in v1 and c not in ridge_i[s]]
    arms, C.aliases = {}, {}
    for name, a in rspec["arms"].items():
        if a.get("alias_of"):  # identical estimator already in the list: not re-run
            C.aliases[name] = a["alias_of"]
            continue
        arms[name] = {s: list(a["columns"][s]) for s in STATS}
    arms["exp2_ref"] = {s: list(rspec["exp2_columns"][s]) for s in STATS}
    arms["no_ridge"] = {s: [] for s in STATS}
    C.arms = arms
    used = sorted(
        {c for s in STATS for c in C.base_cols[s]}
        | {c for a in arms.values() for cs in a.values() for c in cs}
    )
    audit_names(used, "static")
    C.names = used
    C.ci = {c: i for i, c in enumerate(used)}
    C.X = df[used].to_numpy(dtype=np.float32)
    C.y = {s: df[s].to_numpy(dtype=float) for s in STATS}
    C.m = {s: df[f"base_m_{s}"].to_numpy(dtype=float) for s in STATS}
    C.s = {s: df[f"base_s_{s}"].to_numpy(dtype=float) for s in STATS}
    C.pos = np.nan_to_num(df["pos_code"].to_numpy(dtype=float), nan=-1.0)
    C.dstd = spec["default_std"]
    t0 = int(np.argmax(C.season >= SELECT_SEASON))
    C.t0i, C.nt = t0, len(df) - t0
    fold = df["fold_id"].to_numpy()
    blocks, start = [], t0
    for i in range(t0 + 1, len(df) + 1):
        if i == len(df) or fold[i] != fold[start]:
            blocks.append(
                dict(
                    season=int(C.season[start]),
                    fold=int(fold[start]),
                    a=start,
                    b=i,
                    n=int(np.searchsorted(C.dates, C.dates[start], side="left")),
                )
            )
            start = i
    for k, b in enumerate(blocks):
        b["k"] = k
        b["ok"] = b["n"] >= C.B["min_train"]
    C.blocks = blocks
    C.brank = np.full(C.nt, -1)
    for b in blocks:
        C.brank[b["a"] - t0 : b["b"] - t0] = b["k"]
    C.test_season = C.season[t0:]
    log(
        f"rows={len(df)} test rows={C.nt} blocks={len(blocks)} columns={len(used)} arms={len(arms)}"
    )


def audit_names(names, where):
    """Static leak guard: v1/v2 allow-list or a ridge_v2 column name; none matching same-game names."""
    allow, deny = set(C.spec["allowlist"]), re.compile(C.spec["leak_deny_regex"])
    bad = []
    for n in names:
        ok = n in allow or RIDGE_COL.match(n) is not None
        if not ok or deny.search(n):
            bad.append(n)
    if bad:
        raise RuntimeError(f"LEAK AUDIT FAILED ({where}): non-allow-listed features {bad[:10]}")


def null_label_audit():
    """Dynamic leak detector for the ridge columns: missingness must not predict the label.

    For every ridge column of every arm (except the documented ``exp2_ref``): if >= 0.5% of rows are
    null, the standardized difference of the label mean between null and non-null rows must be < 0.35
    sd, and no column may rank-correlate >= 0.95 with the same-game label. Raises on violation."""
    from scipy.stats import spearmanr

    out = {}
    for arm, per in C.arms.items():
        for s in STATS:
            for c in per[s]:
                v = C.X[:, C.ci[c]]
                nul = np.isnan(v)
                share = float(nul.mean())
                y = C.y[s]
                d = 0.0
                if 0.005 <= share <= 0.995:
                    d = float((y[nul].mean() - y[~nul].mean()) / max(y.std(), 1e-9))
                ok = ~nul
                rc = (
                    float(spearmanr(v[ok][:20000], y[ok][:20000]).statistic)
                    if ok.sum() > 100
                    else 0.0
                )
                out[f"{arm}|{c}"] = dict(null_share=share, std_label_gap=d, label_rank_corr=rc)
                if arm in KNOWN_LEAK_ARMS:
                    continue
                if abs(d) >= 0.35 or abs(rc) >= 0.95:
                    raise RuntimeError(
                        f"LEAK AUDIT FAILED: {arm} column {c}: null-vs-nonnull label gap {d:.2f} sd, rank corr {rc:.2f}"
                    )
    leaky = {k: v for k, v in out.items() if k.split("|")[0] in KNOWN_LEAK_ARMS}
    gaps = [abs(v["std_label_gap"]) for v in leaky.values()]
    log(
        f"null/label audit passed for all new arms; reference exp2_ref gap (documented leak): "
        f"max |gap|={max(gaps) if gaps else 0:.2f} sd"
    )
    return out


def arm_cols(arm, stat):
    return np.array([C.ci[c] for c in C.base_cols[stat] + C.arms[arm][stat]])


# ------------------------------------------------------------------------------ scoring


def crps_rows(q, y):
    d = y[:, None] - q
    return 2.0 * np.where(d >= 0, TAUS199 * d, (TAUS199 - 1.0) * d).mean(axis=1)


def cdf_grid(q, t):
    n = q.shape[0]
    c = (q < t[:, None]).sum(axis=1)
    ar = np.arange(n)
    ql, qh = q[ar, np.clip(c - 1, 0, 198)], q[ar, np.clip(c, 0, 198)]
    frac = np.clip((t - ql) / np.maximum(qh - ql, 1e-9), 0.0, 1.0)
    F = np.where(c == 0, 0.5 / 199, np.where(c >= 199, 1 - 0.5 / 199, (c - 0.5 + frac) / 199))
    return np.clip(F, 0.5 / 199, 1 - 0.5 / 199)


def p_from_grid(q, thr):
    p = np.stack([1.0 - cdf_grid(q, np.full(len(q), n - 0.5)) for n in thr], axis=1)
    return np.clip(p, PLOGMIN, 1 - PLOGMIN)


def tll_rows(p, y, thr):
    ev = y[:, None] >= thr[None, :]
    pc = np.clip(p, PLOGMIN, 1 - PLOGMIN)
    return -(np.where(ev, np.log(pc), np.log(1 - pc))).mean(axis=1)


def normal_q(m, s):
    from scipy.stats import norm

    return m[:, None] + s[:, None] * norm.ppf(TAUS199)[None, :]


def clean_q(q):
    return np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)


def boot_ci(delta, gid, n_boot=400, seed=SEED):
    codes, _ = pd.factorize(gid)
    nc = codes.max() + 1
    s = np.bincount(codes, weights=delta, minlength=nc)
    c = np.bincount(codes, minlength=nc).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, nc, size=(n_boot, nc))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return [float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def new_res():
    return {
        s: dict(
            q=np.full((C.nt, 199), np.nan, np.float32),
            mean=np.full(C.nt, np.nan, np.float32),
            p=np.full((C.nt, len(THR[s])), np.nan, np.float32),
        )
        for s in STATS
    }


def store_pred(res, blk, stat, pred):
    a, b = blk["a"] - C.t0i, blk["b"] - C.t0i
    res[stat]["q"][a:b] = pred["q"]
    res[stat]["mean"][a:b] = pred.get("mean", pred["q"].mean(axis=1))
    p = pred.get("p")
    res[stat]["p"][a:b] = p if p is not None else p_from_grid(pred["q"], THR[stat])


def ok_mask(res, stat):
    return ~np.isnan(res[stat]["p"][:, 0])


def base_crps(stat):
    sl = slice(C.t0i, None)
    return crps_rows(normal_q(C.m[stat][sl], C.s[stat][sl]), C.y[stat][sl])


def summarize(res, st, season, ref=None):
    """Per-stat metrics on one season's rows (+ paired clustered CRPS delta vs ``ref``)."""
    ok = ok_mask(res, st) & (C.test_season == season)
    if ref is not None:
        ok &= ok_mask(ref, st)
    y = C.y[st][C.t0i :][ok]
    out = dict(n=int(ok.sum()))
    if ok.sum() == 0:
        return out
    q = res[st]["q"][ok].astype(float)
    cr = crps_rows(q, y)
    mean = res[st]["mean"][ok].astype(float)
    out.update(
        crps=float(cr.mean()),
        crps_ratio_baseline=float(cr.mean() / base_crps(st)[ok].mean()),
        mae=float(np.abs(mean - y).mean()),
        bias=float((mean - y).mean()),
        cov80=float(((y >= q[:, 19]) & (y <= q[:, 179])).mean()),
        tll=float(tll_rows(res[st]["p"][ok].astype(float), y, THR[st]).mean()),
    )
    if ref is not None:
        out["d_crps_vs_ref"] = boot_ci(
            cr - crps_rows(ref[st]["q"][ok].astype(float), y), C.gid[C.t0i :][ok]
        )
    return out


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


# ------------------------------------------------------------------------------ models (fixed exp-2 families)


def make_xgb(params, rounds, objective, extra=None):
    import xgboost as xgb

    kw = dict(
        n_estimators=rounds,
        tree_method="hist",
        device=C.device,
        objective=objective,
        random_state=SEED,
        verbosity=0,
        early_stopping_rounds=20,
        **params,
    )
    kw.update(extra or {})
    return xgb.XGBRegressor(**kw)


def fit_es(h, X, y):
    nv = max(int(len(y) * 0.12), 40)
    h.fit(X[:-nv], y[:-nv], eval_set=[(X[-nv:], y[-nv:])], verbose=False)
    return h


def split_fit_cal(n, cal_frac=0.15):
    cut = int(n * (1.0 - cal_frac))
    cut = min(cut, max(n - C.B["min_cal"], 1))
    return int(np.searchsorted(C.dates[:n], C.dates[cut], side="left"))


def ext_tails(q19):
    """19 quantiles (tau 0.05..0.95) -> 199-grid with linear tail extrapolation."""
    q19 = np.sort(q19, axis=1)
    t = TAUS199
    k = np.clip(np.searchsorted(TAUS19, t) - 1, 0, 17)
    w = (t - TAUS19[k]) / 0.05
    q = q19[:, k] * (1 - w) + q19[:, k + 1] * w
    lo, hi = t < 0.05, t > 0.95
    s_lo = np.maximum(q19[:, 1] - q19[:, 0], 0.0) / 0.05
    s_hi = np.maximum(q19[:, 18] - q19[:, 17], 0.0) / 0.05
    q[:, lo] = q19[:, :1] + s_lo[:, None] * (t[lo] - 0.05)[None, :]
    q[:, hi] = q19[:, 18:19] + s_hi[:, None] * (t[hi] - 0.95)[None, :]
    return clean_q(q)


def nb_pred(mu, alpha):
    from scipy.stats import nbinom

    mu = np.maximum(mu, 1e-3)
    nn = 1.0 / alpha
    pp = nn / (nn + mu)
    q = nbinom.ppf(TAUS199[None, :], nn[:, None], pp[:, None]).astype(np.float32)
    return q, nn, pp


def disp_alpha(y, mu, grp_fit, grp_te, k=300.0):
    a_all = max(float(np.sum((y - mu) ** 2 - mu) / max(np.sum(mu**2), 1e-9)), 1e-3)
    out = np.full(len(grp_te), a_all)
    for g in np.unique(grp_te):
        mk = grp_fit == g
        if mk.sum() >= 30:
            a_g = max(
                float(np.sum((y[mk] - mu[mk]) ** 2 - mu[mk]) / max(np.sum(mu[mk] ** 2), 1e-9)), 1e-3
            )
            out[grp_te == g] = (mk.sum() * a_g + k * a_all) / (mk.sum() + k)
    return out


def stat_pred(blk, arm, st, params, rounds):
    """One stat of one block: the fixed exp-2 family for that stat, arm-specific columns."""
    from scipy.stats import nbinom

    n, ia, ib = blk["n"], blk["a"], blk["b"]
    ci = arm_cols(arm, st)
    if FAMILY[st] == "quantile":
        r = C.y[st][:n] - C.m[st][:n]
        h = make_xgb(params, rounds, "reg:quantileerror", {"quantile_alpha": TAUS19})
        fit_es(h, C.X[:n][:, ci], r)
        q19 = h.predict(C.X[ia:ib][:, ci]).reshape(ib - ia, 19) + C.m[st][ia:ib][:, None]
        return dict(q=ext_tails(q19))
    n_fit = split_fit_cal(n)
    X, y = C.X[:n][:, ci], C.y[st][:n]
    h = make_xgb(params, rounds, "count:poisson")
    fit_es(h, X[:n_fit], y[:n_fit])
    mu_c, mu_t = h.predict(X[n_fit:]), h.predict(C.X[ia:ib][:, ci])
    al = disp_alpha(y[n_fit:], mu_c, C.pos[n_fit:n], C.pos[ia:ib])
    q, nn, pp = nb_pred(mu_t, al)
    p = np.stack([nbinom.sf(N - 1, nn, pp) for N in THR[st]], axis=1)
    return dict(q=q, mean=mu_t, p=np.clip(p, PLOGMIN, 1 - PLOGMIN))


def arm_block(arm, params, rounds):
    def fn(blk):
        def one(st):
            return st, stat_pred(blk, arm, st, params, rounds)

        if C.device == "cuda" and os.environ.get("NBA_STAT_THREADS", "4") != "1":
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=len(STATS)) as ex:
                return dict(ex.map(one, STATS))
        return dict(one(st) for st in STATS)

    return fn


# ------------------------------------------------------------------------------ checkpoints


def data_hash():
    import hashlib

    df = C.df
    blob = json.dumps(
        [
            len(df),
            list(C.names),
            str(df["game_date"].iloc[0]),
            str(df["game_date"].iloc[-1]),
            float(C.y["pts"].sum()),
            C.spec.get("built_at"),
            C.rspec.get("built_at"),
            C.budget_name,
            EXPERIMENT_TAG,
            CODE_VERSION,
            SEED,
            sorted((k, str(v)) for k, v in C.B.items()),
            json.dumps(C.base_cfg, sort_keys=True),
        ],
        default=str,
    )
    return hashlib.sha1(blob.encode()).hexdigest()


class ArmStore:
    """One npz (float32 predictions for the whole test range) + one json per arm."""

    def __init__(self, root):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _npz(self, name):
        return self.dir / f"arm_{name}.npz"

    def _meta(self, name):
        return self.dir / f"arm_{name}.json"

    def exists(self, name):
        return self._npz(name).exists() and self._meta(name).exists()

    def meta(self, name):
        return json.loads(self._meta(name).read_text())

    def put(self, name, res, meta):
        flat = {
            f"{st}__{k}": np.asarray(res[st][k], dtype=np.float32)
            for st in STATS
            for k in ("q", "mean", "p")
        }
        tmp = self.dir / f"arm_{name}.tmp.npz"
        np.savez(tmp, **flat)
        os.replace(tmp, self._npz(name))
        self._meta(name).write_text(json.dumps(jsonable(meta)))  # written last: marks it complete

    def get(self, name):
        with np.load(self._npz(name)) as z:
            return {st: {k: z[f"{st}__{k}"] for k in ("q", "mean", "p")} for st in STATS}


def log_mem(tag):
    rss = None
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS"):
                    rss = int(line.split()[1]) / 1024.0
    except OSError:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    gpu = ""
    if C.device == "cuda":
        gpu = f" gpu_alloc={C.torch.cuda.memory_allocated() / 1e6:.0f}MB"
    log(f"mem[{tag}] rss={rss:.0f}MB{gpu}")


def write_oof_variant(out_dir, name, res):
    """Stream one arm's 2024 rows (q19, p_ge and per-row CRPS) to its own parquet file."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df = C.df
    sel = C.test_season == REPORT_SEASON
    idx19 = [int(round(t * 199 - 0.5)) for t in TAUS19]
    tabs = []
    for st in STATS:
        m = sel & ok_mask(res, st)
        n = int(m.sum())
        if n == 0:
            continue
        y = C.y[st][C.t0i :][m]
        q = res[st]["q"][m]
        cols = {
            "variant": pa.array(np.full(n, name)),
            "stat": pa.array(np.full(n, st)),
            "game_id": pa.array(C.gid[C.t0i :][m]),
            "player_id": pa.array(df["player_id"].to_numpy()[C.t0i :][m]),
            "fold_id": pa.array(df["fold_id"].to_numpy()[C.t0i :][m]),
            "y": pa.array(y.astype(np.float32)),
            "mean": pa.array(res[st]["mean"][m].astype(np.float32)),
            "crps": pa.array(crps_rows(q.astype(float), y).astype(np.float32)),
            "tll": pa.array(tll_rows(res[st]["p"][m].astype(float), y, THR[st]).astype(np.float32)),
        }
        flat = np.ascontiguousarray(q[:, idx19], dtype=np.float32).ravel()
        cols["q19"] = pa.ListArray.from_arrays(
            pa.array(np.arange(0, n * 19 + 1, 19, dtype=np.int32)), pa.array(flat)
        )
        k = len(THR[st])
        flatp = np.ascontiguousarray(res[st]["p"][m], dtype=np.float32).ravel()
        cols["p_ge"] = pa.ListArray.from_arrays(
            pa.array(np.arange(0, n * k + 1, k, dtype=np.int32)), pa.array(flatp)
        )
        tabs.append(pa.table(cols))
    pq.write_table(pa.concat_tables(tabs), Path(out_dir) / (name + ".parquet"), compression="zstd")


def run_arm(name, params, results, timings, parts):
    """Walk-forward one arm over all 2023+2024 blocks; STRICT (a missing block raises).

    A finished arm is persisted (predictions npz, per-arm OOF parquet, summary json); an arm
    already in the checkpoint store is RESUMED instead of recomputed."""
    fin = C.store.dir / f"final_{name}.json"
    if C.store.exists(name) and fin.exists() and (parts / f"{name}.parquet").exists():
        results[name] = json.loads(fin.read_text())
        timings[name] = results[name].get("timing", 0.0)
        log(f"arm {name}: RESUMED from checkpoint")
        return
    t = time.monotonic()
    res = new_res()
    todo = [b for b in C.blocks if b["ok"]]
    for i, blk in enumerate(todo):
        if time.monotonic() - t > C.B["arm_s"]:
            raise RuntimeError(f"{name}: hard cap hit after {i}/{len(todo)} blocks (strict)")
        for st, pred in arm_block(name, params, C.B["rounds"])(blk).items():
            store_pred(res, blk, st, pred)
    timing = time.monotonic() - t
    rec = dict(
        timing=timing,
        selection_2023={st: summarize(res, st, SELECT_SEASON) for st in STATS},
        report_2024={st: summarize(res, st, REPORT_SEASON) for st in STATS},
        columns={st: C.arms[name][st] for st in STATS},
    )
    C.store.put(name, res, dict(timing=timing, blocks=len(todo)))
    write_oof_variant(parts, name, res)
    fin.write_text(json.dumps(jsonable(rec)))
    results[name] = rec
    timings[name] = timing
    sel = [rec["selection_2023"][st].get("crps_ratio_baseline", float("nan")) for st in STATS]
    log(
        f"arm {name}: {len(todo)} blocks in {timing:.0f}s | 2023 CRPS/baseline "
        + " ".join(f"{st}={v:.4f}" for st, v in zip(STATS, sel, strict=True))
    )
    del res
    import gc

    gc.collect()
    log_mem(name)
    if os.environ.get("NBA_CRASH_AFTER_ARM") == name:  # test hook: simulate a kernel death
        log(f"NBA_CRASH_AFTER_ARM={name}: exiting hard")
        os._exit(137)


def select_arms():
    order = ["ref_fixed", "exp2_ref", "no_ridge"] + [
        a for a in C.arms if a not in ("ref_fixed", "exp2_ref", "no_ridge")
    ]
    pick = os.environ.get("NBA_ARMS")
    if pick:
        want = [a.strip() for a in pick.split(",") if a.strip()]
        bad = [a for a in want if a not in C.arms]
        if bad:
            raise ValueError(f"unknown arms {bad}; known: {order}")
        return [a for a in order if a in want]
    shard = os.environ.get("NBA_SHARD")
    if shard:
        k, n = (int(x) for x in shard.split("/"))
        if not 0 <= k < n:
            raise ValueError("NBA_SHARD must be k/n with 0 <= k < n")
        return [a for i, a in enumerate(order) if i % n == k]
    if os.environ.get("NBA_MAX_ARMS"):  # local smoke: first N arms
        return order[: int(os.environ["NBA_MAX_ARMS"])]
    return order


def main():
    import shutil

    setup()
    load()
    run_root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "artifacts"))
    ck = run_root.parent / "checkpoints" / data_hash()[:12]
    C.store = ArmStore(ck)
    arms = select_arms()
    per_arm = C.B["est_arm_min"]
    print(f"ARMS ({len(arms)}): {arms}")
    print(
        f"expected runtime: ~{per_arm:.0f} min/arm x {len(arms)} = ~{per_arm * len(arms):.0f} min on a T4 "
        "(pts quantile ~5 min, count stats ~1.5 min each, run in parallel threads; ESTIMATE from exp-2 timings)"
    )
    log(f"checkpoint dir {ck} ({len(list(ck.glob('arm_*.json')))} arms already complete)")
    audit = null_label_audit()
    params = C.base_cfg["params"]
    parts = ck / "oof_parts"
    parts.mkdir(exist_ok=True)
    results, timings = {}, {}
    for name in arms:
        run_arm(name, params, results, timings, parts)
    # paired deltas vs the primary reference (descriptive; the rule is applied by the eval)
    ref = C.store.get("ref_fixed") if C.store.exists("ref_fixed") else None
    for name in arms:
        if ref is not None and name != "ref_fixed":
            cur = C.store.get(name)
            for st in STATS:
                results[name]["report_2024"][st].update(
                    {
                        k: v
                        for k, v in summarize(cur, st, REPORT_SEASON, ref).items()
                        if k.startswith("d_")
                    }
                )
                results[name]["selection_2023"][st].update(
                    {
                        k: v
                        for k, v in summarize(cur, st, SELECT_SEASON, ref).items()
                        if k.startswith("d_")
                    }
                )
            del cur
    out = run_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "best_config.json").write_text(
        json.dumps(
            jsonable(
                dict(
                    experiment_tag=EXPERIMENT_TAG,
                    base_config=C.base_cfg,
                    families=FAMILY,
                    arms_run=arms,
                    arm_columns={a: C.arms[a] for a in arms},
                    budget=C.budget_name,
                    budget_params=C.B,
                    seed=SEED,
                    thresholds={k: v.tolist() for k, v in THR.items()},
                    primary_reference="ref_fixed",
                    aliases=C.aliases,
                )
            ),
            indent=1,
        )
    )
    metrics = dict(
        experiment_tag=EXPERIMENT_TAG,
        complete=False,
        budget=C.budget_name,
        device=C.device,
        gpu=C.gpu_name,
        seeds=dict(seed=SEED),
        select_season=SELECT_SEASON,
        report_season=REPORT_SEASON,
        arms=arms,
        aliases=C.aliases,
        selection_2023={a: results[a]["selection_2023"] for a in arms},
        leaderboard_2024={a: results[a]["report_2024"] for a in arms},
        timings_s=timings,
        n_test_rows=int(C.nt),
        leak_audit=audit,
    )
    (out / "metrics.json").write_text(json.dumps(jsonable(metrics), indent=1))
    shutil.copytree(parts, out / "oof", dirs_exist_ok=True)  # appears only when all is written
    metrics["complete"] = True
    (out / "metrics.json").write_text(json.dumps(jsonable(metrics), indent=1))
    log_mem("done")
    log(f"ARTIFACTS WRITTEN TO {out}")
    return metrics


def entry():
    return main()


if __name__ == "__main__":
    entry()
