# mypy: ignore-errors
# ruff: noqa: E501
# Colab-executed job script (torch on the Colab VM); kept out of strict type checking.
"""joint_game_set: set transformer over all rostered players of a game, joint NLL, parlay sampling.

Self-contained (numpy / pandas / pyarrow / torch only). PRE-REGISTERED protocol and keep rule:
docs/JOINT_GAME_SET.md and nba/eval/joint_game_set_eval.py (applied LOCALLY after
``make colab-pull``; this script only trains and writes predictions).

DATA (staged by nba.features.game_sets + nba.eval.joint_game_set_eval --stage)
  game_sets.npz           per game: 32 player tokens (as-of features only), 51+13 features, labels,
                          PIT bounds against the pre-game marginal, game margin/total z-scores
  eval_parlays/legs/singles/games.parquet   the 2024 synthetic same-game parlays of joint_eval
  Season 2025 is refused. Season 2024 is report-only; hyper-parameters and the epoch count are
  selected on a time-ordered slice (last 20% by date) of season 2023.

MODEL
  Tokens: [game token] + up to 32 player tokens (side + data-source embeddings; no played flag, no
  minutes, no stats of the game: the set cannot reveal who played).
  Joint head: a mixture of M low-rank-plus-diagonal Gaussians over the normal-score vector
  z (4 stats x 32 slots + margin + total):  z | state k  ~  N(mu_k, D_k + L_k L_k^T),
  state weights pi from the game token, mu/d/L per player token. Exact NLL (Woodbury), missing
  dims (DNP / no marginal) are marginalised exactly. The mixture over discrete game states gives
  non-Gaussian, state-dependent dependence (blowout vs close game, usage sharing, star nights).
  Auxiliary head: p_play per token (BCE). Loss = per-dim joint NLL + lam * BCE.
  Sampling: pick state, draw factor + noise: joint draws of every leg of every parlay in a game.
  Variants evaluated: set_full (the model's own marginals) and set_copula (probability-integral
  transform each dim through its own mixture marginal, then the PRODUCTION marginal: dependence only).

BUDGET (env NBA_BUDGET): full (default; thorough) | fast | smoke (local CPU, tiny sample).
CRASH SAFETY: every stage checkpoints to <run folder>/checkpoints/<hash>/ (mid-trial/mid-fit state
every few epochs, trial results, final models, prediction chunks). Re-run the same notebook to resume
(log lines RESUMED). Small artifacts are written first; metrics.json is written LAST.
Forced-crash test hook: NBA_FORCE_CRASH_AT="label=n" (see ``maybe_crash``).
Device-agnostic: cuda if available (T4/L4/A100/...), else cpu; the device and GPU are printed.
"""

import hashlib
import json
import math
import os
import platform
import subprocess
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = 20261008
CODE_VERSION = "jgs-1"
EXPERIMENT_TAG = "joint_game_set_v1"
STATS = ("pts", "reb", "ast", "fg3m")
FROZEN_SEASON = 2025
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SLICE_FRAC = 0.2  # last 20% of 2023 games (by date) = early-stopping / selection slice
TAIL_W = 0.01
Z_CLIP = 1e-6
D_MIN = 0.05
LAM_SEL = 0.2  # fixed weight of the p_play BCE in the selection criterion
LOG2PI = math.log(2.0 * math.pi)

BUDGETS = {
    "smoke": dict(
        trials=2,
        max_epochs=3,
        patience=2,
        batch=16,
        K=2,
        n_sims=2000,
        ft_epochs=1,
        ckpt_every=1,
        chunk=8,
        wf=True,
        sample_frac=0.05,
        wf_max_blocks=2,
        est="~1-3 min on a CPU (5% sample)",
    ),
    "fast": dict(
        trials=8,
        max_epochs=60,
        patience=10,
        batch=32,
        K=3,
        n_sims=40000,
        ft_epochs=6,
        ckpt_every=5,
        chunk=100,
        wf=True,
        sample_frac=1.0,
        wf_max_blocks=99,
        est="~25-40 min on a T4/L4 (estimate, not measured on GPU)",
    ),
    "full": dict(
        trials=24,
        max_epochs=100,
        patience=14,
        batch=32,
        K=5,
        n_sims=100000,
        ft_epochs=8,
        ckpt_every=5,
        chunk=100,
        wf=True,
        sample_frac=1.0,
        wf_max_blocks=99,
        est="~60-100 min on a T4/L4, ~40-60 min on an A100 (ESTIMATE from CPU step timings; not measured on GPU)",
    ),
}
STAGE_ESTIMATES_FULL = {  # minutes, T4-class GPU, ESTIMATES
    "load + standardise": 0.3,
    "search: 24 trials x <=100 epochs (~1-2 s/epoch, early stopped)": 30.0,
    "final fits: 5 seeds x best_epoch epochs": 8.0,
    "static predictions: 1315 games x 5 models x 20k draws": 8.0,
    "walk-forward refits: 9 monthly blocks x 5 seeds x 8 epochs + predictions": 25.0,
    "write artifacts": 0.5,
}


class ForcedCrash(RuntimeError):
    """Raised by the test hook ``NBA_FORCE_CRASH_AT``."""


_CRASH_COUNT = {}


def maybe_crash(label):
    """``NBA_FORCE_CRASH_AT=label=n`` raises ForcedCrash the n-th time ``label`` is reached."""
    spec = os.environ.get("NBA_FORCE_CRASH_AT")
    if not spec:
        return
    want, n = spec.split("=")
    _CRASH_COUNT[label] = _CRASH_COUNT.get(label, 0) + 1
    if label == want and _CRASH_COUNT[label] == int(n):
        raise ForcedCrash(f"forced crash at {label}={n}")


class Ctx:
    pass


C = Ctx()


def log(*a):
    print(f"[{time.monotonic() - C.t0:7.1f}s]", *a, flush=True)


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(jsonable(obj), indent=1))
    os.replace(tmp, path)


# ------------------------------------------------------------------------------ setup / device


def setup():
    import torch

    C.t0 = time.monotonic()
    _CRASH_COUNT.clear()
    C.torch = torch
    C.budget_name = os.environ.get("NBA_BUDGET", "full")
    C.B = BUDGETS[C.budget_name]
    C.device = "cuda" if torch.cuda.is_available() else "cpu"
    if C.device == "cpu":
        torch.set_num_threads(
            1
        )  # avoid OpenMP clashes with lightgbm/xgboost loaded in the same process
    info = {"device": C.device, "torch": torch.__version__, "python": platform.python_version()}
    if C.device == "cuda":
        p = torch.cuda.get_device_properties(0)
        info.update(
            gpu=p.name,
            gpu_mem_gb=round(p.total_memory / 1e9, 1),
            capability=f"{p.major}.{p.minor}",
            n_gpus=torch.cuda.device_count(),
        )
    C.device_info = info
    print("DEVICE:", json.dumps(info))
    print(f"BUDGET={C.budget_name}  expected runtime: {C.B['est']}")
    if C.budget_name == "full":
        for k, v in STAGE_ESTIMATES_FULL.items():
            print(f"   {k:<78} ~{v:.1f} min")
        print(
            f"   TOTAL ESTIMATE ~{sum(STAGE_ESTIMATES_FULL.values()):.0f} min (estimates; per-stage wall times print as the run proceeds)"
        )
    try:
        C.git = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        C.git = ""


# ------------------------------------------------------------------------------ data


def load():
    path = Path(os.environ["NBA_PARQUET"])
    C.dir = path.parent
    with np.load(path, allow_pickle=False) as z:
        A = {k: z[k] for k in z.files}
    meta = json.loads((C.dir / "game_sets_meta.json").read_text())
    if int(A["season"].max()) >= FROZEN_SEASON:
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")
    legs = pd.read_parquet(C.dir / "eval_legs.parquet")
    parl = pd.read_parquet(
        C.dir / "eval_parlays.parquet", columns=["parlay_id", "game_idx", "n_legs"]
    )
    sing = pd.read_parquet(C.dir / "eval_singles.parquet")
    egam = pd.read_parquet(C.dir / "eval_games.parquet", columns=["game_idx"])
    G = len(A["season"])
    keep = np.ones(G, bool)
    if (
        C.budget_name == "smoke"
    ):  # tiny sample, stratified by season, always keeps a few 2024 eval games
        rng = np.random.default_rng(SEED)
        keep = np.zeros(G, bool)
        ev = np.array(sorted(set(egam["game_idx"].tolist())))
        for s in np.unique(A["season"]):
            idx = np.nonzero(A["season"] == s)[0]
            n = max(24, int(len(idx) * C.B["sample_frac"]))
            if s == REPORT_SEASON:
                idx = np.array(sorted(set(idx) & set(ev.tolist()))) if len(ev) else idx
                n = min(n, 40, len(idx))
            keep[rng.choice(idx, size=min(n, len(idx)), replace=False)] = True
    C.orig_idx = np.nonzero(keep)[
        0
    ]  # kept-game position -> original index (outputs use ORIGINAL indices)
    new_idx = -np.ones(G, np.int64)
    new_idx[keep] = np.arange(keep.sum())
    for k, v in list(A.items()):
        if isinstance(v, np.ndarray) and v.shape[:1] == (G,) and k not in ("tok_names",):
            A[k] = v[keep]
    for df in (legs, parl, sing, egam):
        df["game_idx_old"] = df["game_idx"]
        df["game_idx"] = new_idx[df["game_idx_old"].to_numpy()]
    legs = legs[legs["game_idx"] >= 0]
    sing = sing[sing["game_idx"] >= 0]
    parl = parl[parl["game_idx"] >= 0]
    # parlays need all their legs
    ok = (
        legs.groupby("parlay_id").size().reindex(parl["parlay_id"]).fillna(0).to_numpy()
        == parl["n_legs"].to_numpy()
    )
    parl = parl[ok]
    legs = legs[legs["parlay_id"].isin(set(parl["parlay_id"]))]
    C.A, C.meta, C.legs, C.parl, C.sing = A, meta, legs, parl, sing
    C.G = int(keep.sum())
    C.T = A["tok_mask"].shape[1]
    C.D = C.T * 4 + 2
    C.dates = A["game_date"]
    C.season = A["season"]
    log(f"loaded {C.G} games (of {G}); parlays {len(parl)}, legs {len(legs)}, singles {len(sing)}")
    # splits
    s23 = np.nonzero(C.season == SELECT_SEASON)[0]
    cut = np.quantile(C.dates[s23], 1.0 - SLICE_FRAC)
    C.cut_date = int(cut)
    C.idx22 = np.nonzero(C.season == 2022)[0]
    C.idx23 = s23
    C.idx23_tr = s23[C.dates[s23] < cut]
    C.idx23_va = s23[C.dates[s23] >= cut]
    C.idx24 = np.nonzero(C.season == REPORT_SEASON)[0]
    # standardisation fitted on the selection-training tokens only (2022 + early 2023)
    sel_tr = np.concatenate([C.idx22, C.idx23_tr])
    X = A["tok_x"][sel_tr][A["tok_mask"][sel_tr]]
    C.x_mu, C.x_sd = X.mean(0), X.std(0) + 1e-6
    GX = A["game_x"][sel_tr]
    C.g_mu, C.g_sd = GX.mean(0), GX.std(0) + 1e-6
    C.data = build_tensors()
    C.tok_names = list(meta["tok_features"])
    log(
        f"splits: 2022 {len(C.idx22)} | 2023 train {len(C.idx23_tr)} / slice {len(C.idx23_va)} (cut day {C.cut_date}) | 2024 {len(C.idx24)}"
    )


def build_tensors():
    torch = C.torch
    A, dev = C.A, C.device
    X = np.clip((A["tok_x"] - C.x_mu) / C.x_sd, -6, 6).astype(np.float32)
    X = X * A["tok_mask"][..., None]
    GX = np.clip((A["game_x"] - C.g_mu) / C.g_sd, -6, 6).astype(np.float32)
    # INPUTS ONLY: features, roster mask, home/away side, data source of the season (a per-game flag).
    # No played flag, no per-token marginal indicator, no stats/minutes of the game.
    src_game = np.repeat((A["season"] >= 2023).astype(np.int64)[:, None], C.T, axis=1)
    d = {
        "X": torch.tensor(X, device=dev),
        "mask": torch.tensor(A["tok_mask"], device=dev),
        "side": torch.tensor(A["tok_side"].astype(np.int64), device=dev),
        "src": torch.tensor(src_game, device=dev),
        "GX": torch.tensor(GX, device=dev),
        # targets (never inputs)
        "played": torch.tensor(A["tok_played"].astype(np.float32), device=dev),
        "lo": torch.tensor(A["tok_pit_lo"], device=dev),
        "hi": torch.tensor(A["tok_pit_hi"], device=dev),
        "obs": torch.tensor(A["tok_obs"], device=dev),
        "z": torch.tensor(A["tok_z"], device=dev),
        "zm": torch.tensor(A["z_margin"].astype(np.float32), device=dev),
        "zt": torch.tensor(A["z_total"].astype(np.float32), device=dev),
    }
    d["is22"] = torch.tensor((A["season"] == 2022).astype(np.float32), device=dev)
    return d


MODEL_INPUT_KEYS = ("X", "mask", "side", "src", "GX")


def data_hash():
    A = C.A
    blob = json.dumps(
        [
            C.G,
            C.budget_name,
            CODE_VERSION,
            EXPERIMENT_TAG,
            SEED,
            sorted((k, str(v)) for k, v in C.B.items()),
            C.meta.get("built_at"),
            float(A["margin"].sum()),
            float(A["tok_x"].sum()),
            int(A["tok_obs"].sum()),
        ],
        default=str,
    )
    return hashlib.sha1(blob.encode()).hexdigest()


# ------------------------------------------------------------------------------ model


def make_model(cfg, f_tok, f_game):
    torch = C.torch
    nn = torch.nn
    M, R, d = cfg["M"], cfg["R"], cfg["d_model"]

    class SetNet(nn.Module):
        def __init__(self):
            super().__init__()
            drop = cfg["dropout"]
            self.M, self.R = M, R
            self.tok_in = nn.Sequential(
                nn.Linear(f_tok, d), nn.GELU(), nn.Dropout(drop), nn.Linear(d, d)
            )
            self.side_emb = nn.Embedding(2, d)
            self.src_emb = nn.Embedding(2, d)
            self.game_in = nn.Sequential(
                nn.Linear(f_game, d), nn.GELU(), nn.Dropout(drop), nn.Linear(d, d)
            )
            self.cls = nn.Parameter(torch.zeros(1, 1, d))
            layer = nn.TransformerEncoderLayer(
                d,
                cfg["heads"],
                d * cfg["ff"],
                drop,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.enc = nn.TransformerEncoder(layer, cfg["layers"], enable_nested_tensor=False)
            self.norm = nn.LayerNorm(d)
            self.play = nn.Sequential(
                nn.Dropout(drop), nn.Linear(d, d // 2), nn.GELU(), nn.Linear(d // 2, 1)
            )
            self.tok_head = nn.Sequential(
                nn.Dropout(drop),
                nn.Linear(d, d),
                nn.GELU(),
                nn.Dropout(drop),
                nn.Linear(d, M * (8 + 4 * R)),
            )
            self.game_head = nn.Sequential(
                nn.Dropout(drop),
                nn.Linear(d, d),
                nn.GELU(),
                nn.Dropout(drop),
                nn.Linear(d, M * (4 + 2 * R)),
            )
            self.mix_head = nn.Sequential(
                nn.Dropout(drop), nn.Linear(d, d // 2), nn.GELU(), nn.Linear(d // 2, M)
            )
            for lin in (self.tok_head[-1], self.game_head[-1], self.mix_head[-1]):
                nn.init.normal_(lin.weight, std=0.01)
                nn.init.zeros_(lin.bias)
            g = torch.Generator().manual_seed(cfg.get("init_seed", 0))
            # break the symmetry between the M states: small random mean offsets
            with torch.no_grad():
                b = self.tok_head[-1].bias.view(M, 8 + 4 * R)
                b[:, :4] = 0.2 * torch.randn(M, 4, generator=g)
                bg = self.game_head[-1].bias.view(M, 4 + 2 * R)
                bg[:, :2] = 0.2 * torch.randn(M, 2, generator=g)
            self.d_shift = math.log(math.expm1(0.95 - D_MIN))  # softplus^-1 so d starts at 0.95

        def forward(self, X, mask, side, src, GX):
            B, T, _ = X.shape
            h = self.tok_in(X) + self.side_emb(side) + self.src_emb(src)
            g = self.game_in(GX)[:, None, :] + self.cls
            seq = torch.cat([g, h], 1)
            pad = torch.cat([torch.zeros(B, 1, dtype=torch.bool, device=X.device), ~mask], 1)
            out = self.norm(self.enc(seq, src_key_padding_mask=pad))
            hg, ht = out[:, 0], out[:, 1:]
            tk = self.tok_head(ht).view(B, T, M, 8 + 4 * R)
            gm = self.game_head(hg).view(B, M, 4 + 2 * R)
            mu_t = 3.0 * torch.tanh(tk[..., :4])
            d_t = D_MIN + torch.nn.functional.softplus(tk[..., 4:8] + self.d_shift)
            L_t = 1.5 * torch.tanh(tk[..., 8:]).view(B, T, M, 4, R)
            mu_g = 3.0 * torch.tanh(gm[..., :2])
            d_g = D_MIN + torch.nn.functional.softplus(gm[..., 2:4] + self.d_shift)
            L_g = 1.5 * torch.tanh(gm[..., 4:]).view(B, M, 2, R)
            mu = torch.cat([mu_t.permute(0, 2, 1, 3).reshape(B, M, T * 4), mu_g], -1)
            dd = torch.cat([d_t.permute(0, 2, 1, 3).reshape(B, M, T * 4), d_g], -1)
            LL = torch.cat([L_t.permute(0, 2, 1, 3, 4).reshape(B, M, T * 4, R), L_g], 2)
            logpi = torch.log_softmax(self.mix_head(hg), -1)
            return {"mu": mu, "d": dd, "L": LL, "logpi": logpi, "play": self.play(ht)[..., 0]}

    return SetNet().to(C.device)


def mixture_logpdf(x, m, mu, d, L, logpi):
    """log sum_k pi_k N(x_obs; mu_k, D_k + L_k L_k^T) over the observed dims (mask m in {0,1}).

    x, m: [B, D]; mu, d: [B, M, D]; L: [B, M, D, R]; logpi: [B, M]. Woodbury + matrix determinant
    lemma; masked dims contribute exactly 0 (variance 1, zero residual, zero loading)."""
    torch = C.torch
    mm = m[:, None, :]
    xm = (x[:, None, :] - mu) * mm
    dv = torch.where(mm > 0, d * d, torch.ones_like(d))
    inv = 1.0 / dv
    Li = L * mm[..., None]
    Lw = Li * inv[..., None]
    a = (xm * xm * inv).sum(-1)
    u = (Lw * xm[..., None]).sum(2)
    R = L.shape[-1]
    Mm = torch.eye(R, device=x.device, dtype=x.dtype) + torch.einsum("bmdr,bmds->bmrs", Lw, Li)
    chol = torch.linalg.cholesky(Mm)
    sol = torch.cholesky_solve(u[..., None], chol)[..., 0]
    quad = a - (u * sol).sum(-1)
    logdet = torch.log(dv).sum(-1) + 2.0 * torch.log(torch.diagonal(chol, dim1=-2, dim2=-1)).sum(-1)
    n_obs = m.sum(-1)[:, None]
    ll = -0.5 * (n_obs * LOG2PI + logdet + quad)
    return torch.logsumexp(logpi + ll, dim=1)


def batch_inputs(data, idx):
    return {k: data[k][idx] for k in MODEL_INPUT_KEYS}


def targets(data, idx, rand, gen=None):
    """z targets [B, D] and observation mask [B, D]. ``rand``: fresh randomised PIT (training);
    else the fixed export draw (validation / test, identical for every engine)."""
    torch = C.torch
    B = len(idx)
    if rand:
        lo, hi = data["lo"][idx], data["hi"][idx]
        u = torch.rand(lo.shape, device=lo.device, generator=gen)
        ut = torch.rand(lo.shape, device=lo.device, generator=gen)
        v = lo + u * (hi - lo)
        top = lo >= 1.0 - 1e-12
        bot = (hi <= 1e-12) & ~top
        v = torch.where(top, 1.0 - TAIL_W * ut, v)
        v = torch.where(bot, TAIL_W * ut, v)
        v = v.clamp(Z_CLIP, 1.0 - Z_CLIP)
        z = torch.special.ndtri(v.double()).float()
    else:
        z = data["z"][idx]
    obs = data["obs"][idx]
    z = torch.where(obs, z, torch.zeros_like(z))
    x = torch.cat([z.reshape(B, -1), data["zm"][idx][:, None], data["zt"][idx][:, None]], 1)
    m = torch.cat([obs.reshape(B, -1).float(), torch.ones(B, 2, device=x.device)], 1)
    return x, m


def loss_fn(model, data, idx, lam, rand, gen=None, weights=None, per_game=False):
    torch = C.torch
    out = model(**batch_inputs(data, idx))
    x, m = targets(data, idx, rand, gen)
    lp = mixture_logpdf(x, m, out["mu"], out["d"], out["L"], out["logpi"])  # [B]
    nll = -lp
    n_obs = m.sum(-1)
    mask = data["mask"][idx]
    bce = torch.nn.functional.binary_cross_entropy_with_logits(
        out["play"], data["played"][idx], reduction="none"
    )
    bce_g = (bce * mask).sum(-1) / mask.sum(-1).clamp(min=1)
    w = torch.ones_like(nll) if weights is None else weights
    nll_dim = (w * nll).sum() / (w * n_obs).sum()
    total = nll_dim + lam * (w * bce_g).sum() / w.sum()
    if per_game:
        return total, nll, n_obs, bce_g
    return total, nll_dim.detach(), (w * bce_g).sum().detach() / w.sum()


def evaluate(model, data, idx, lam, bs=128):
    torch = C.torch
    model.eval()
    tot_nll, tot_obs, tot_bce, n = 0.0, 0.0, 0.0, 0
    with torch.no_grad():
        for i in range(0, len(idx), bs):
            ii = torch.as_tensor(idx[i : i + bs], device=C.device)
            _, nll, n_obs, bce_g = loss_fn(model, data, ii, lam, False, per_game=True)
            tot_nll += float(nll.sum())
            tot_obs += float(n_obs.sum())
            tot_bce += float(bce_g.sum())
            n += len(ii)
    nll_dim = tot_nll / max(tot_obs, 1.0)
    bce = tot_bce / max(n, 1)
    return {
        "nll_per_dim": nll_dim,
        "nll_per_game": tot_nll / max(n, 1),
        "bce": bce,
        "total": nll_dim + lam * bce,
        "sel": nll_dim
        + LAM_SEL * bce,  # config-independent criterion (early stop + cross-trial selection)
    }


def indep_nll_per_dim(data, idx):
    """Reference: the standard-normal independence model on the same fixed z (copula with rho=0)."""
    torch = C.torch
    ii = torch.as_tensor(idx, device=C.device)
    x, m = targets(data, ii, False)
    return float((0.5 * (LOG2PI + x * x) * m).sum() / m.sum())


# ------------------------------------------------------------------------------ training with crash-safe state


def _rng_state(rng, gen):
    torch = C.torch
    s = {"np": rng.bit_generator.state, "gen": gen.get_state(), "cpu": torch.get_rng_state()}
    if C.device == "cuda":
        s["cuda"] = torch.cuda.get_rng_state()
    return s


def _rng_restore(s, rng, gen):
    """RNG states are byte tensors that must live on the CPU even when the checkpoint was loaded
    with ``map_location=cuda`` (``Generator.set_state`` rejects a cuda tensor)."""
    torch = C.torch
    rng.bit_generator.state = s["np"]
    gen.set_state(s["gen"].cpu())
    torch.set_rng_state(s["cpu"].cpu())
    if C.device == "cuda" and "cuda" in s:
        torch.cuda.set_rng_state(s["cuda"].cpu())


def train_model(
    cfg,
    train_idx,
    val_idx,
    *,
    seed,
    state_path,
    max_epochs,
    patience,
    label,
    init_state=None,
    lr_scale=1.0,
    fixed_epochs=None,
    ckpt_every=5,
    weight_fn=None,
):
    """Train one SetNet. Early stop on ``val_idx`` (total = per-dim NLL + lam * BCE) unless
    ``fixed_epochs``. Resumes from ``state_path`` (model, optimiser, schedule, RNG, history)."""
    torch = C.torch
    data = C.data
    lam, bs = cfg["lam"], C.B["batch"]
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    gen = torch.Generator(device=C.device).manual_seed(seed)
    model = make_model({**cfg, "init_seed": seed}, data["X"].shape[-1], data["GX"].shape[-1])
    if init_state is not None:
        model.load_state_dict(init_state)
    opt = torch.optim.AdamW(
        model.parameters(), lr=cfg["lr"] * lr_scale, weight_decay=cfg["weight_decay"]
    )
    n_ep = fixed_epochs if fixed_epochs is not None else max_epochs
    warm = min(2, max(1, n_ep // 10))

    def lr_at(ep):  # linear warm-up then cosine to 10%
        if ep < warm:
            return (ep + 1) / warm
        p = (ep - warm) / max(1, n_ep - warm)
        return 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, p)))

    st = {
        "epoch": 0,
        "best": float("inf"),
        "best_epoch": 0,
        "bad": 0,
        "hist": [],
        "best_state": None,
    }
    if Path(state_path).exists():
        ck = torch.load(state_path, map_location=C.device, weights_only=False)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        st = ck["st"]
        _rng_restore(ck["rng"], rng, gen)
        log(
            f"{label}: RESUMED from epoch {st['epoch']} (best {st['best']:.5f} @ {st['best_epoch']})"
        )
    t_last = time.monotonic()
    w_all = None if weight_fn is None else weight_fn(train_idx)
    early = fixed_epochs is None and val_idx is not None
    while st["epoch"] < n_ep and not (early and st["bad"] >= patience):
        ep = st["epoch"]
        for g_ in opt.param_groups:
            g_["lr"] = cfg["lr"] * lr_scale * lr_at(ep)
        model.train()
        perm = rng.permutation(len(train_idx))
        tl, nb = 0.0, 0
        t0 = time.monotonic()
        for i in range(0, len(perm), bs):
            sel = perm[i : i + bs]
            if len(sel) < 2:
                continue
            ii = torch.as_tensor(train_idx[sel], device=C.device)
            wts = (
                None
                if w_all is None
                else torch.as_tensor(w_all[sel], device=C.device, dtype=torch.float32)
            )
            total, _, _ = loss_fn(model, data, ii, lam, True, gen, wts)
            if not bool(
                torch.isfinite(total)
            ):  # numerical guard: skip a bad batch, never step on NaN
                C.n_nonfinite = getattr(C, "n_nonfinite", 0) + 1
                continue
            opt.zero_grad(set_to_none=True)
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tl += float(total.detach())
            nb += 1
        rec = {"epoch": ep + 1, "train_loss": tl / max(nb, 1), "sec": time.monotonic() - t0}
        if val_idx is not None and fixed_epochs is None:
            v = evaluate(model, data, val_idx, lam)
            rec.update({f"val_{k}": x for k, x in v.items()})
            if v["sel"] < st["best"] - 1e-5:
                st["best"], st["best_epoch"], st["bad"] = v["sel"], ep + 1, 0
                st["best_state"] = {k: x.detach().clone() for k, x in model.state_dict().items()}
            else:
                st["bad"] += 1
        st["hist"].append(rec)
        st["epoch"] = ep + 1
        if ep == 0 or (ep + 1) % 10 == 0:
            log(
                f"{label}: epoch {ep + 1}/{n_ep} train {rec['train_loss']:.4f} "
                + (f"val {rec.get('val_sel', float('nan')):.4f} " if "val_sel" in rec else "")
                + f"({rec['sec']:.1f}s/epoch)"
            )
        stop = early and st["bad"] >= patience
        done = st["epoch"] >= n_ep or stop
        if done or (st["epoch"] % ckpt_every == 0) or time.monotonic() - t_last > 120:
            torch.save(
                {
                    "model": model.state_dict(),
                    "opt": opt.state_dict(),
                    "st": st,
                    "rng": _rng_state(rng, gen),
                },
                str(state_path) + ".tmp",
            )
            os.replace(str(state_path) + ".tmp", state_path)
            t_last = time.monotonic()
            maybe_crash("epoch_ckpt")
        if stop:
            log(
                f"{label}: early stop at epoch {st['epoch']} (best {st['best']:.5f} @ {st['best_epoch']})"
            )
            break
    final = (
        st["best_state"]
        if (fixed_epochs is None and val_idx is not None and st["best_state"] is not None)
        else {k: x.detach().clone() for k, x in model.state_dict().items()}
    )
    return final, st


# ------------------------------------------------------------------------------ search


def sample_cfg(i):
    r = np.random.default_rng(SEED + 1000 + i)
    if i == 0:  # reference configuration
        return dict(
            d_model=64,
            layers=2,
            heads=4,
            ff=2,
            dropout=0.4,
            weight_decay=0.01,
            lr=1e-3,
            M=6,
            R=3,
            lam=0.5,
            use_2022=1,
            w2022=1.0,
        )
    return dict(
        d_model=int(r.choice([64, 96, 128])),
        layers=int(r.choice([1, 2, 3])),
        heads=4,
        ff=2,
        dropout=float(r.choice([0.3, 0.4, 0.5])),
        weight_decay=float(r.choice([1e-3, 1e-2, 5e-2, 1e-1])),
        lr=float(r.choice([3e-4, 6e-4, 1e-3])),
        M=int(r.choice([4, 6, 8, 12])),
        R=int(r.choice([2, 3, 4, 6, 8])),
        lam=float(r.choice([0.3, 0.5, 1.0])),
        use_2022=int(r.choice([0, 1])),
        w2022=float(r.choice([0.5, 1.0])),
    )


def train_indices(cfg, base_idx):
    """Training games for a config: ``base_idx`` (2023 part) plus optionally 2022."""
    return np.concatenate([C.idx22, base_idx]) if cfg["use_2022"] else base_idx


def weight_fn_for(cfg):
    w22 = cfg["w2022"]

    def f(idx):
        return np.where(C.season[idx] == 2022, w22, 1.0).astype(np.float32)

    return f


def run_search(ck):
    res = []
    sp = ck / "search_log.json"
    for i in range(C.B["trials"]):
        tp = ck / f"trial_{i}.json"
        if tp.exists():
            r = json.loads(tp.read_text())
            log(f"trial {i}: RESUMED result val_sel {r['val_sel']:.5f} @ epoch {r['best_epoch']}")
            res.append(r)
            continue
        cfg = sample_cfg(i)
        t0 = time.monotonic()
        tr = train_indices(cfg, C.idx23_tr)
        best_state, st = train_model(
            cfg,
            tr,
            C.idx23_va,
            seed=SEED + i,
            state_path=ck / f"trial_{i}_state.pt",
            max_epochs=C.B["max_epochs"],
            patience=C.B["patience"],
            label=f"trial {i}",
            ckpt_every=C.B["ckpt_every"],
            weight_fn=weight_fn_for(cfg),
        )
        # the final model of the trial is the best-epoch model; recompute its slice metrics exactly
        model = make_model(
            {**cfg, "init_seed": SEED + i}, C.data["X"].shape[-1], C.data["GX"].shape[-1]
        )
        model.load_state_dict(best_state)
        v = evaluate(model, C.data, C.idx23_va, cfg["lam"])
        ind = indep_nll_per_dim(C.data, C.idx23_va)
        r = {
            "trial": i,
            "cfg": cfg,
            "best_epoch": st["best_epoch"],
            "val_total": v["total"],
            "val_sel": v["sel"],
            "val_nll_per_dim": v["nll_per_dim"],
            "val_nll_per_game": v["nll_per_game"],
            "val_bce": v["bce"],
            "val_nll_indep_per_dim": ind,
            "gain_vs_indep_per_game": (ind - v["nll_per_dim"])
            * v["nll_per_game"]
            / max(v["nll_per_dim"], 1e-9),
            "n_epochs_run": st["epoch"],
            "seconds": time.monotonic() - t0,
            "sec_per_epoch": float(np.mean([h["sec"] for h in st["hist"]])),
        }
        write_json(tp, r)
        p = ck / f"trial_{i}_state.pt"
        if p.exists():
            p.unlink()
        res.append(r)
        log(
            f"trial {i}: val_sel {r['val_sel']:.5f} (nll/dim {r['val_nll_per_dim']:.4f} vs indep {ind:.4f}; bce {r['val_bce']:.4f}) best_epoch {r['best_epoch']} {r['seconds']:.0f}s cfg={cfg}"
        )
        if i == 0:
            n_ep = r["n_epochs_run"]
            proj = r["sec_per_epoch"] * min(C.B["max_epochs"], max(n_ep, 30)) * C.B["trials"] / 60.0
            log(
                f"PROJECTION: search stage ~{proj:.0f} min at {r['sec_per_epoch']:.2f} s/epoch on {C.device_info.get('gpu', C.device)}"
            )
        write_json(sp, {"trials": res, "complete": len(res) == C.B["trials"]})
        maybe_crash("trial_done")
    best = min(res, key=lambda r: r["val_sel"])
    return best, res


# ------------------------------------------------------------------------------ final fits


def fit_final(best, ck):
    torch = C.torch
    cfg = best["cfg"]
    epochs = max(3, int(best["best_epoch"]))
    models = []
    tr = train_indices(cfg, C.idx23)
    for k in range(C.B["K"]):
        fp = ck / f"final_{k}.pt"
        if fp.exists():
            models.append(torch.load(fp, map_location=C.device, weights_only=False)["state"])
            log(f"final seed {k}: RESUMED from checkpoint")
            continue
        state, st = train_model(
            cfg,
            tr,
            None,
            seed=SEED + 5000 + k,
            state_path=ck / f"final_{k}_state.pt",
            max_epochs=epochs,
            patience=0,
            label=f"final {k}",
            fixed_epochs=epochs,
            ckpt_every=C.B["ckpt_every"],
            weight_fn=weight_fn_for(cfg),
        )
        torch.save(
            {"state": state, "cfg": cfg, "epochs": epochs, "seed": SEED + 5000 + k},
            str(fp) + ".tmp",
        )
        os.replace(str(fp) + ".tmp", fp)
        p = ck / f"final_{k}_state.pt"
        if p.exists():
            p.unlink()
        models.append(state)
        log(
            f"final seed {k}: done ({epochs} epochs, last train loss {st['hist'][-1]['train_loss']:.4f})"
        )
        maybe_crash("final_done")
    return models


def load_models(cfg, states):
    out = []
    for s in states:
        m = make_model(cfg, C.data["X"].shape[-1], C.data["GX"].shape[-1])
        m.load_state_dict(s)
        m.eval()
        out.append(m)
    return out


# ------------------------------------------------------------------------------ prediction (sampling)


def crps_samples(x, y):
    x = np.sort(np.asarray(x, dtype=np.float64))
    n = len(x)
    i = np.arange(1, n + 1)
    return float(np.mean(np.abs(x - y)) - np.sum((2 * i - n - 1) * x) / (n * n))


def game_params(models, gi_list):
    """Per model, per game parameters (no dropout)."""
    torch = C.torch
    out = [[] for _ in models]
    with torch.no_grad():
        for i in range(0, len(gi_list), 64):
            ii = torch.as_tensor(gi_list[i : i + 64], device=C.device)
            for mi, m in enumerate(models):
                o = m(**batch_inputs(C.data, ii))
                for j in range(len(ii)):
                    out[mi].append({k: v[j] for k, v in o.items()})
    return out


def sample_z(p, n, gen):
    torch = C.torch
    mu, d, L, logpi = p["mu"], p["d"], p["L"], p["logpi"]
    M, D = mu.shape
    comp = torch.multinomial(torch.softmax(logpi, -1), n, replacement=True, generator=gen)
    z = torch.empty(n, D, device=mu.device)
    for k in range(M):
        idx = torch.nonzero(comp == k)[:, 0]
        nk = idx.numel()
        if nk == 0:
            continue
        eps = torch.randn(nk, D, device=mu.device, generator=gen)
        f = torch.randn(nk, L.shape[-1], device=mu.device, generator=gen)
        z[idx] = mu[k] + d[k] * eps + f @ L[k].T
    return z


def mix_cdf(p, dims, z):
    """Marginal CDF of each requested dim under the mixture: z [n, len(dims)] -> u in (0,1)."""
    torch = C.torch
    mu = p["mu"][:, dims]
    s = torch.sqrt(p["d"][:, dims] ** 2 + (p["L"][:, dims, :] ** 2).sum(-1))
    pi = torch.softmax(p["logpi"], -1)
    zz = z[:, None, :]
    cdf = 0.5 * (1 + torch.erf((zz - mu[None]) / (s[None] * math.sqrt(2.0))))
    return (cdf * pi[None, :, None]).sum(1)


def mix_sf(p, dims, thr):
    """P(z_dim > thr) under the mixture, for paired (dim, thr) vectors."""
    torch = C.torch
    mu = p["mu"][:, dims]
    s = torch.sqrt(p["d"][:, dims] ** 2 + (p["L"][:, dims, :] ** 2).sum(-1))
    pi = torch.softmax(p["logpi"], -1)
    sf = 0.5 * (1 - torch.erf((thr[None, :] - mu) / (s * math.sqrt(2.0))))
    return (sf * pi[:, None]).sum(0)


def predict_chunk(models, params, gi_chunk, offset, n_total, tag):
    """Predictions for ``gi_chunk`` games. ``params[m][offset + j]`` is model m's params for game j."""
    torch = C.torch
    A = C.A
    K = len(models)
    n_m = max(200, n_total // K)
    par_rows, sing_rows, game_rows, play_rows = [], [], [], []
    for j, gi in enumerate(gi_chunk):
        ps = [params[m][offset + j] for m in range(K)]
        gl = C.legs_by_game.get(int(gi))
        H_full, H_cop, N = {}, {}, 0
        zs_margin, zs_total = [], []
        for m in range(K):
            gen = torch.Generator(device=C.device).manual_seed(
                (SEED * 31 + int(gi) * 977 + m * 7919) % (2**63 - 1)
            )
            z = sample_z(ps[m], n_m, gen)
            N += n_m
            zs_margin.append(z[:, C.T * 4].cpu().numpy())
            zs_total.append(z[:, C.T * 4 + 1].cpu().numpy())
            if gl is not None:
                dims = torch.as_tensor(gl["dim"], device=C.device)
                zl = z[:, dims]
                dr = torch.as_tensor(gl["dir"], device=C.device, dtype=torch.float32)
                zt = torch.as_tensor(gl["zthr"], device=C.device, dtype=torch.float32)
                no = torch.as_tensor(gl["side_no"], device=C.device).bool()
                yes_f = (dr[None] * (zl - zt[None])) > 0
                u = mix_cdf(ps[m], dims, zl)
                thr_u = 0.5 * (1 + torch.erf(zt / math.sqrt(2.0)))
                yes_c = (dr[None] * (u - thr_u[None])) > 0
                hit_f = yes_f ^ no[None]
                hit_c = yes_c ^ no[None]
                for pid, cols in gl["parlays"]:
                    cc = torch.as_tensor(cols, device=C.device)
                    H_full[pid] = H_full.get(pid, 0) + int(hit_f[:, cc].all(1).sum())
                    H_cop[pid] = H_cop.get(pid, 0) + int(hit_c[:, cc].all(1).sum())
        for pid in H_full:
            par_rows.append(
                {
                    "parlay_id": pid,
                    f"p_set_{tag}full": (H_full[pid] + 0.5) / (N + 1.0),
                    f"p_set_{tag}copula": (H_cop[pid] + 0.5) / (N + 1.0),
                }
            )
        # singles (analytic mixture tail probabilities, averaged over the ensemble)
        sg = C.sing_by_game.get(int(gi))
        if sg is not None:
            dims = torch.as_tensor(sg["dim"], device=C.device)
            thr = torch.as_tensor(sg["zthr"], device=C.device, dtype=torch.float32)
            pr = torch.stack([mix_sf(ps[m], dims, thr) for m in range(K)]).mean(0).cpu().numpy()
            for sid, p_ in zip(sg["single_id"], pr, strict=True):
                sing_rows.append({"single_id": int(sid), f"p_set_{tag}full": float(p_)})
        # game-level: ensemble NLL on the fixed z, CRPS of margin / total from pooled samples
        ii = torch.as_tensor([int(gi)], device=C.device)
        x, mk = targets(C.data, ii, False)
        lds = []
        for m in range(K):
            lp = mixture_logpdf(
                x, mk, ps[m]["mu"][None], ps[m]["d"][None], ps[m]["L"][None], ps[m]["logpi"][None]
            )
            lds.append(float(lp[0]))
        nll = -(float(np.logaddexp.reduce(lds)) - math.log(K))
        zm, zt_ = np.concatenate(zs_margin), np.concatenate(zs_total)
        sub = np.random.default_rng(int(gi)).choice(
            len(zm), size=min(len(zm), 20000), replace=False
        )
        game_rows.append(
            {
                "game_idx": int(gi),
                f"nll_{tag}set": nll,
                f"crps_margin_{tag}set": crps_samples(
                    A["mu_margin"][gi] + A["sd_margin"][gi] * zm[sub], A["margin"][gi]
                ),
                f"crps_total_{tag}set": crps_samples(
                    A["mu_total"][gi] + A["sd_total"][gi] * zt_[sub], A["total"][gi]
                ),
            }
        )
        if not tag:
            game_rows[-1]["nll_ind"] = float(0.5 * ((LOG2PI + x * x) * mk).sum())
        pp = torch.stack([torch.sigmoid(ps[m]["play"]) for m in range(K)]).mean(0).cpu().numpy()
        for s in np.nonzero(A["tok_mask"][gi])[0]:
            play_rows.append(
                {
                    "game_idx": int(gi),
                    "slot": int(s),
                    ("p_set_wf" if tag else "p_set"): float(pp[s]),
                }
            )
    return par_rows, sing_rows, game_rows, play_rows


def prepare_eval_lookup():
    legs, sing = C.legs, C.sing
    C.legs_by_game, C.sing_by_game = {}, {}
    for gi, g in legs.groupby("game_idx"):
        g = g.sort_values(["parlay_id", "leg_pos"]).reset_index(drop=True)
        parl = [(int(pid), list(gg.index)) for pid, gg in g.groupby("parlay_id")]
        C.legs_by_game[int(gi)] = {
            "dim": np.array(g["dim"]),
            "dir": np.array(g["dir"]),
            "zthr": np.array(g["zthr"]),
            "side_no": np.array(g["side_no"]),
            "parlays": parl,
        }
    for gi, g in sing.groupby("game_idx"):
        C.sing_by_game[int(gi)] = {
            "dim": np.array(g["dim"]),
            "zthr": np.array(g["zthr"]),
            "single_id": np.array(g["single_id"]),
        }


def predict_games(models_for_game, game_idx, tag, ck, label):
    """Chunked, resumable predictions. ``models_for_game(chunk_games)`` returns (models, params)."""
    chunks = [game_idx[i : i + C.B["chunk"]] for i in range(0, len(game_idx), C.B["chunk"])]
    outs = {"par": [], "sing": [], "game": [], "play": []}
    for ci, ch in enumerate(chunks):
        fp = ck / f"pred_{label}_{ci}.json"
        if fp.exists():
            r = json.loads(fp.read_text())
            log(f"{label}: chunk {ci + 1}/{len(chunks)} RESUMED")
        else:
            models, params = models_for_game(ch)
            pr, si, ga, pl_ = predict_chunk(models, params, ch, 0, C.B["n_sims"], tag)
            r = {"par": pr, "sing": si, "game": ga, "play": pl_}
            write_json(fp, r)
            log(f"{label}: chunk {ci + 1}/{len(chunks)} done ({len(ch)} games)")
            maybe_crash("pred_chunk")
        for k in outs:
            outs[k] += r[k]
    return {k: pd.DataFrame(v) for k, v in outs.items()}


# ------------------------------------------------------------------------------ walk-forward


def month_blocks():
    d = C.dates[C.idx24].astype("int64")
    dts = pd.to_datetime(d, unit="D")
    ym = dts.year * 100 + dts.month
    blocks = []
    for v in sorted(set(ym)):
        gi = C.idx24[np.asarray(ym) == v]
        blocks.append((int(v), gi, int(C.dates[gi].min())))
    return blocks[: C.B["wf_max_blocks"]]


def wf_models(block_i, block_start, cfg, static_states, ck):
    torch = C.torch
    models = []
    pre = np.nonzero(C.dates < block_start)[0]
    pre = pre[(C.season[pre] >= (2022 if cfg["use_2022"] else 2023))]
    for k, init in enumerate(static_states):
        fp = ck / f"wf_{block_i}_{k}.pt"
        if fp.exists():
            st = torch.load(fp, map_location=C.device, weights_only=False)["state"]
            log(f"wf block {block_i} seed {k}: RESUMED")
        else:
            st, _ = train_model(
                cfg,
                pre,
                None,
                seed=SEED + 9000 + 31 * block_i + k,
                state_path=ck / f"wf_{block_i}_{k}_state.pt",
                max_epochs=C.B["ft_epochs"],
                patience=0,
                label=f"wf {block_i}/{k}",
                init_state=init,
                lr_scale=0.3,
                fixed_epochs=C.B["ft_epochs"],
                ckpt_every=C.B["ckpt_every"],
                weight_fn=weight_fn_for(cfg),
            )
            torch.save({"state": st}, str(fp) + ".tmp")
            os.replace(str(fp) + ".tmp", fp)
            p = ck / f"wf_{block_i}_{k}_state.pt"
            if p.exists():
                p.unlink()
            maybe_crash("wf_fit_done")
        models.append(st)
    return load_models(cfg, models)


# ------------------------------------------------------------------------------ main


def summarize(par, sing, game, static_cfg):
    """Convenience numbers for metrics.json (the official scoring is local)."""
    out = {}

    def ll(p, yy):
        p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
        return float(np.mean(-(yy * np.log(p) + (1 - yy) * np.log(1 - p))))

    df = par.merge(C.parl_ref, on="parlay_id")
    for c in [c for c in df.columns if c.startswith("p_")]:
        out[f"parlay_logloss_{c}"] = ll(df[c].to_numpy(), df["y"].to_numpy())
    out["n_parlays"] = int(len(df))
    out["n_games_scored"] = int(len(game))
    for c in [c for c in game.columns if c.startswith(("nll", "crps"))]:
        out[f"mean_{c}"] = float(game[c].mean())
    s = sing.merge(C.sing_ref, on="single_id")

    def ece(p, yy, nb=10):
        b = np.minimum((p * nb).astype(int), nb - 1)
        return float(
            sum(
                abs(p[b == k].mean() - yy[b == k].mean()) * (b == k).sum()
                for k in range(nb)
                if (b == k).any()
            )
            / len(p)
        )

    out["single_ece_production"] = ece(s["p_marg"].to_numpy(), s["y"].to_numpy())
    out["single_ece_set_full"] = ece(s["p_set_full"].to_numpy(), s["y"].to_numpy())
    out["n_singles"] = int(len(s))
    return out


def main():
    setup()
    load()
    prepare_eval_lookup()
    torch = C.torch
    # reference columns for convenience metrics (all engines come from the staged files)
    pr = pd.read_parquet(
        C.dir / "eval_parlays.parquet",
        columns=["parlay_id", "y", "p_independence", "p_gaussian", "p_gaussian_hi", "p_t"],
    )
    C.parl_ref = pr[pr["parlay_id"].isin(set(C.parl["parlay_id"]))]
    C.sing_ref = C.sing[["single_id", "p_marg", "y"]]
    run_root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "artifacts"))
    ck = run_root.parent / "checkpoints" / data_hash()[:12]
    ck.mkdir(parents=True, exist_ok=True)
    ad = ck / "artifact_dir.txt"
    if not ad.exists():
        ad.write_text(datetime.now().strftime("%Y%m%d_%H%M%S"))
    out = run_root / ad.read_text().strip()
    out.mkdir(parents=True, exist_ok=True)
    log(
        f"checkpoint dir {ck}; artifact dir {out}; {len(list(ck.glob('trial_*.json')))} trials already complete"
    )
    timings = {}
    # ---- small artifacts first
    meta_run = {
        "experiment": EXPERIMENT_TAG,
        "code_version": CODE_VERSION,
        "git": C.git,
        "seed": SEED,
        "budget": C.budget_name,
        "budget_cfg": C.B,
        "device": C.device_info,
        "n_games": C.G,
        "splits": {
            "n_2022": len(C.idx22),
            "n_2023_train": len(C.idx23_tr),
            "n_2023_slice": len(C.idx23_va),
            "n_2024": len(C.idx24),
            "cut_day": C.cut_date,
        },
        "tok_features": C.tok_names,
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(out / "data_summary.json", meta_run)
    write_json(out / "metrics_partial.json", {"complete": False, "stage": "search", **meta_run})
    # ---- stage 1: search
    t0 = time.monotonic()
    best, trials = run_search(ck)
    timings["search_s"] = time.monotonic() - t0
    write_json(out / "search_log.json", {"trials": trials, "best": best})
    write_json(
        out / "best_config.json",
        {
            "cfg": best["cfg"],
            "best_epoch": best["best_epoch"],
            "val_sel": best["val_sel"],
            "seed": SEED,
            "selected_on": "2023 slice only",
        },
    )
    write_json(
        out / "metrics_partial.json",
        {"complete": False, "stage": "final_fits", "best": best, **meta_run},
    )
    cfg = best["cfg"]
    # ---- stage 2: final fits (fit through 2023)
    t0 = time.monotonic()
    states = fit_final(best, ck)
    timings["final_fits_s"] = time.monotonic() - t0
    torch.save(
        {
            "states": states,
            "cfg": cfg,
            "x_mu": C.x_mu,
            "x_sd": C.x_sd,
            "g_mu": C.g_mu,
            "g_sd": C.g_sd,
        },
        out / "weights.pt",
    )
    models = load_models(cfg, states)
    # ---- stage 3: static predictions on 2024
    t0 = time.monotonic()
    g24 = C.idx24

    def static_models(ch):
        return models, game_params(models, ch)

    st = predict_games(static_models, g24, "", ck, "static")
    timings["static_pred_s"] = time.monotonic() - t0
    par, sing, game, play = st["par"], st["sing"], st["game"], st["play"]
    write_json(
        out / "metrics_partial.json",
        {"complete": False, "stage": "walk_forward", "best": best, **meta_run},
    )
    # ---- stage 4: walk-forward monthly refits (secondary arm)
    wf_par, wf_sing, wf_game, wf_play = [], [], [], []
    if C.B["wf"]:
        t0 = time.monotonic()
        blocks = month_blocks()
        for bi, (ym, gi, start) in enumerate(blocks):
            wm = wf_models(bi, start, cfg, states, ck)

            def wf_fn(ch, wm=wm):
                return wm, game_params(wm, ch)

            r = predict_games(wf_fn, gi, "wf_", ck, f"wf{bi}")
            wf_par.append(r["par"])
            wf_sing.append(r["sing"])
            wf_game.append(r["game"])
            wf_play.append(r["play"])
            log(f"wf block {ym} ({len(gi)} games) done")
            maybe_crash("wf_block_done")
        timings["wf_s"] = time.monotonic() - t0
        if wf_par:
            par = par.merge(pd.concat(wf_par), on="parlay_id", how="left")
            sing = sing.merge(pd.concat(wf_sing), on="single_id", how="left")
            game = game.merge(pd.concat(wf_game), on="game_idx", how="left")
            play = play.merge(pd.concat(wf_play), on=["game_idx", "slot"], how="left")
    # ---- big artifacts, then metrics.json last
    game["game_idx"] = C.orig_idx[game["game_idx"].to_numpy()]
    play["game_idx"] = C.orig_idx[play["game_idx"].to_numpy()]
    par.to_parquet(out / "parlay_preds.parquet", index=False)
    sing.to_parquet(out / "single_preds.parquet", index=False)
    game.to_parquet(out / "game_preds.parquet", index=False)
    play.to_parquet(out / "play_preds.parquet", index=False)
    write_json(out / "training_log.json", {"trials": trials, "timings_s": timings})
    summ = summarize(par, sing, game, cfg)
    metrics = {
        "complete": True,
        "experiment": EXPERIMENT_TAG,
        "code_version": CODE_VERSION,
        "git": C.git,
        "seed": SEED,
        "budget": C.budget_name,
        "device": C.device_info,
        "best_cfg": cfg,
        "best_epoch": best["best_epoch"],
        "val_sel_2023_slice": best["val_sel"],
        "val_nll_per_dim_2023_slice": best["val_nll_per_dim"],
        "val_nll_indep_per_dim_2023_slice": best["val_nll_indep_per_dim"],
        "ensemble_size": C.B["K"],
        "n_sims_total": C.B["n_sims"],
        "has_walk_forward": bool(wf_par),
        "timings_s": timings,
        "wall_s": time.monotonic() - C.t0,
        "n_nonfinite_batches_skipped": int(getattr(C, "n_nonfinite", 0)),
        "touches_holdout": False,
        "season_2025_loaded": False,
        "convenience": summ,
    }
    write_json(out / "metrics.json", metrics)
    if (out / "metrics_partial.json").exists():
        (out / "metrics_partial.json").unlink()
    log(f"ARTIFACTS WRITTEN TO {out}")
    return metrics


def entry():
    return main()


if __name__ == "__main__":
    entry()
