# mypy: ignore-errors
# ruff: noqa: E501
# Colab-executed job script (torch on the Colab VM); kept out of strict type checking.
"""pbp_gpt: decoder-only transformer over tokenized NBA play-by-play ("basketball GPT").

Self-contained (numpy / pandas / pyarrow / torch only; no ``nba`` imports). PRE-REGISTERED protocol and
keep rules: docs/PBP_GPT.md and research/eval/pbp_gpt_eval.py (applied LOCALLY after ``make colab-pull``;
this script only trains, samples and writes predictions).

DATA (written by ``uv run python -m research.features.pbp_tokens``): tokens.npz (int16 tokens, int8 slot
codes, offsets), games.parquet (split train / val / report; val = last 20% of 2023, report = 2024),
ckpt.npz (per game: 5 checkpoint states = tip / start Q2 / start Q3 / start Q4 / Q4 5:00 -- score,
token clock, on-court rosters, box so far, final box, as-of player means), vocab.json (token layout +
event tables). Season 2025 is never in the input.

MODEL: pre-norm GPT, learned absolute positions, grouped-query attention (small KV cache, which is what
makes sampling thousands of continuations affordable), weight-tied head, dropout, optional player-token
dropout to NEW_k (cold-start robustness). Loss: next-token cross-entropy on the LEARNED tokens (DT, EVT,
ACTOR, ASSIST, SUB_OUT, SUB_IN); header / state-block / lineup-refresh tokens are forced (deterministic
given history) and excluded from the loss. Early stopping and trial selection on the 2023 val slice only.

SAMPLING: ancestral sampling (temperature 1, no top-k) from a checkpoint state with a vectorised twin of
``research.features.pbp_tokens.StreamState`` (grammar, constrained actors drawn from the on-court / bench of the
header roster, forced state blocks, score / clock / foul bookkeeping). Prefix KV is shared across the S
continuations of a game; per-row KV grows only with generated tokens. Throughput is measured and reported.

BUDGET (env NBA_BUDGET): full (default; thorough) | fast | smoke (local CPU, tiny sample).
CRASH SAFETY: every epoch (per-trial last.pt), every finished trial, the selected model, and every sampled
chunk is written to <run folder>/checkpoints/<hash>/. Re-run the same notebook to resume (log lines
RESUMED). Small artifacts first; metrics.json is written LAST (metrics_partial.json before that).
Forced-crash hook for tests: NBA_FORCE_CRASH_AT="label=n" (see ``maybe_crash``).
Device-agnostic: cuda if available (T4/L4/A100/...), else cpu; the device and GPU are printed.
"""

import hashlib
import json
import math
import os
import platform
import random
import subprocess
import time
import warnings
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = 20261008
CODE_VERSION = "pbp-gpt-1"
EXPERIMENT_TAG = "pbp_gpt_v1"
FROZEN_SEASON = 2025
CKPT_NAMES = ("tip", "q2", "q3", "q4", "q4_5min")
SL_NONE, SL_DT, SL_EVT, SL_ACTOR, SL_ASSIST, SL_OUT, SL_IN = 0, 1, 2, 3, 4, 5, 6
CATS = ("dt", "evt", "actor", "actor_new", "assist", "sub_out", "sub_in")  # loss categories
N_CAT = len(CATS)

BUDGETS = {
    "smoke": dict(
        trials=2,
        max_epochs=2,
        patience=2,
        batch=4,
        tiny=True,
        n_live=2,
        n_tip=2,
        sims=(8, 8, 8, 8, 8),
        max_rows=64,
        kv_gb=0.5,
        est="~1-2 min on a CPU (tiny model, a few games)",
    ),
    "fast": dict(
        trials=3,
        max_epochs=20,
        patience=4,
        batch=8,
        tiny=False,
        n_live=100,
        n_tip=50,
        sims=(128, 128, 128, 256, 512),
        max_rows=1024,
        kv_gb=6.0,
        est="~40-70 min on a T4/L4 (ESTIMATE, not measured on GPU)",
    ),
    "full": dict(
        trials=6,
        max_epochs=40,
        patience=5,
        batch=8,
        tiny=False,
        n_live=300,
        n_tip=150,
        sims=(256, 256, 256, 512, 1024),
        max_rows=1024,
        kv_gb=6.0,
        est="~2-3 h on a T4/L4, ~1-1.5 h on an A100 (ESTIMATE from FLOP / bandwidth arithmetic; a measured projection prints after epoch 1 and after the first sampled chunk)",
    ),
}
STAGE_ESTIMATES_FULL = {  # minutes, T4-class GPU, ESTIMATES
    "load": 0.5,
    "search: 6 trials x <=40 epochs (~0.5-1.5 min/epoch, early stopped)": 75.0,
    "val / report next-token loss by category": 2.0,
    "sampling: tip 150 games x 256, q2/q3 300 x 256, q4 300 x 512, q4_5min 300 x 1024": 40.0,
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
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(jsonable(obj), indent=1))
    os.replace(tmp, path)


def atomic_save(torch, obj, path):
    tmp = str(path) + ".tmp"
    torch.save(obj, tmp)
    os.replace(tmp, path)


# ------------------------------------------------------------------------------ setup / device


def setup():
    import torch

    C.t0 = time.monotonic()
    _CRASH_COUNT.clear()
    C.torch = torch
    C.budget_name = os.environ.get("NBA_BUDGET", "full")
    C.B = dict(BUDGETS[C.budget_name])
    C.device = "cuda" if torch.cuda.is_available() else "cpu"
    if C.device == "cpu":
        torch.set_num_threads(int(os.environ.get("NBA_CPU_THREADS", "1")))
        C.amp = None
    else:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        C.amp = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    info = {"device": C.device, "torch": torch.__version__, "python": platform.python_version()}
    if C.device == "cuda":
        p = torch.cuda.get_device_properties(0)
        info.update(
            gpu=p.name,
            gpu_mem_gb=round(p.total_memory / 1e9, 1),
            capability=f"{p.major}.{p.minor}",
            n_gpus=torch.cuda.device_count(),
            amp_dtype=str(C.amp),
        )
    C.device_info = info
    print("DEVICE:", json.dumps(info))
    print(f"BUDGET={C.budget_name}  expected runtime: {C.B['est']}")
    if C.budget_name == "full":
        for k, v in STAGE_ESTIMATES_FULL.items():
            print(f"   {k:<92} ~{v:.1f} min")
        print(
            f"   TOTAL ESTIMATE ~{sum(STAGE_ESTIMATES_FULL.values()):.0f} min on a T4 (estimates; per-stage wall times print as the run proceeds)"
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
        C.tokens = z["tokens"]
        C.slots = z["slots"]
        C.offsets = z["offsets"]
    C.games = pd.read_parquet(C.dir / "games.parquet")
    with np.load(C.dir / "ckpt.npz", allow_pickle=False) as z:
        C.ck = {k: z[k] for k in z.files}
    C.vocab = json.loads((C.dir / "vocab.json").read_text())
    if int(C.games["season"].max()) >= FROZEN_SEASON:
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")
    if len(C.games) != len(C.offsets) - 1:
        raise ValueError("games.parquet and tokens.npz disagree")
    lay = C.vocab["layout"]
    ev = C.vocab["evt"]
    C.V = SimpleNamespace(size=C.vocab["size"], **lay)
    C.RM = C.vocab["rm"]
    C.CTX = C.vocab["ctx"]
    C.tok_per_sec = float(C.vocab.get("tok_per_sec", 0.6))
    C.idx = {s: np.where(C.games["split"].to_numpy() == s)[0] for s in ("train", "val", "report")}
    log(
        f"games: train {len(C.idx['train'])} val {len(C.idx['val'])} report {len(C.idx['report'])};"
        f" tokens {len(C.tokens):,}; vocab {C.V.size}"
    )
    C.evt = ev


def get_tables():
    """Event / vocabulary lookup tables as tensors on the device."""
    torch = C.torch
    dev = C.device
    ev = C.evt
    V = C.V
    T = SimpleNamespace()
    T.side = torch.tensor(ev["side"], dtype=torch.long, device=dev)
    T.side_c = T.side.clamp(min=0)
    pay = torch.tensor(ev["pay"], dtype=torch.long, device=dev)
    T.pay0, T.pay1 = pay[:, 0], pay[:, 1]
    T.npay = (T.pay0 > 0).long() + (T.pay1 > 0).long()
    T.pts = torch.tensor(ev["pts"], dtype=torch.long, device=dev)
    T.reb = torch.tensor(ev["reb"], dtype=torch.long, device=dev)
    T.foul_d = torch.tensor(ev["foul_d"], dtype=torch.long, device=dev)
    T.pe = torch.tensor(ev["period_end"], dtype=torch.long, device=dev)
    T.pe_idx = int(np.nonzero(np.asarray(ev["period_end"]))[0][0])
    T.dt_rep = torch.tensor(C.vocab["dt_rep"], dtype=torch.float32, device=dev)
    diffs = np.arange(-60, 61)
    a = np.abs(diffs)
    n = np.where(a <= 20, a, np.where(a <= 25, 21, np.where(a <= 35, 22, 23)))
    n = np.where(diffs >= 0, n, -n)
    T.sc_tab = torch.tensor(V.sc0 + n + 23, dtype=torch.long, device=dev)
    return T


# ------------------------------------------------------------------------------ model


def make_model(cfg, vocab_size, ctx):
    torch = C.torch
    nn = torch.nn
    F = torch.nn.functional

    class Block(nn.Module):
        def __init__(self, d, h, hkv, ff, p):
            super().__init__()
            self.h, self.hkv, self.dh = h, hkv, d // h
            self.ln1 = nn.LayerNorm(d)
            self.qkv = nn.Linear(d, (h + 2 * hkv) * self.dh, bias=False)
            self.proj = nn.Linear(d, d, bias=False)
            self.ln2 = nn.LayerNorm(d)
            self.fc1 = nn.Linear(d, ff * d)
            self.fc2 = nn.Linear(ff * d, d)
            self.drop = nn.Dropout(p)
            self.p = p

        def split(self, h):
            qkv = self.qkv(h)
            q, k, v = torch.split(
                qkv, [self.h * self.dh, self.hkv * self.dh, self.hkv * self.dh], -1
            )
            return q, k, v

        def forward(self, x, return_kv=False):
            B, T, D = x.shape
            q, k, v = self.split(self.ln1(x))
            q = q.view(B, T, self.h, self.dh).transpose(1, 2)
            k = k.view(B, T, self.hkv, self.dh).transpose(1, 2)
            v = v.view(B, T, self.hkv, self.dh).transpose(1, 2)
            g = self.h // self.hkv
            kk = k.repeat_interleave(g, 1) if g > 1 else k
            vv = v.repeat_interleave(g, 1) if g > 1 else v
            a = F.scaled_dot_product_attention(
                q, kk, vv, is_causal=True, dropout_p=self.p if self.training else 0.0
            )
            x = x + self.drop(self.proj(a.transpose(1, 2).reshape(B, T, D)))
            x = x + self.drop(self.fc2(F.gelu(self.fc1(self.ln2(x)))))
            return (x, (k, v)) if return_kv else x

    class GPT(nn.Module):
        def __init__(self):
            super().__init__()
            d = cfg["d_model"]
            self.cfg = cfg
            self.tok_emb = nn.Embedding(vocab_size, d)
            self.pos_emb = nn.Embedding(ctx, d)
            self.drop = nn.Dropout(cfg["dropout"])
            self.blocks = nn.ModuleList(
                [
                    Block(d, cfg["heads"], cfg["kv_heads"], cfg["ff"], cfg["dropout"])
                    for _ in range(cfg["layers"])
                ]
            )
            self.ln_f = nn.LayerNorm(d)
            torch.manual_seed(cfg.get("init_seed", 0))
            self.apply(self._init)
            for blk in self.blocks:
                nn.init.normal_(blk.proj.weight, 0.0, 0.02 / math.sqrt(2 * cfg["layers"]))
                nn.init.normal_(blk.fc2.weight, 0.0, 0.02 / math.sqrt(2 * cfg["layers"]))

        @staticmethod
        def _init(m):
            if isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0.0, 0.02)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, 0.0, 0.02)

        def head(self, x):
            return F.linear(self.ln_f(x), self.tok_emb.weight)

        def forward(self, idx, return_kv=False):
            B, T = idx.shape
            x = self.tok_emb(idx) + self.pos_emb(torch.arange(T, device=idx.device))[None]
            x = self.drop(x)
            kvs = []
            for blk in self.blocks:
                if return_kv:
                    x, kv = blk(x, True)
                    kvs.append(kv)
                else:
                    x = blk(x)
            return (self.head(x), kvs, x) if return_kv else self.head(x)

        def step(self, tok, pos, t, kvs, Kn, Vn, G, S, plen):
            """One cached decode step for R = G*S rows (shared prefix KV ``kvs`` per game)."""
            R = tok.shape[0]
            x = self.tok_emb(tok) + self.pos_emb(pos)
            blk0 = self.blocks[0]
            H, Hkv, dh = blk0.h, blk0.hkv, blk0.dh
            grp = H // Hkv
            Tp = kvs[0][0].shape[2]
            pmask = (torch.arange(Tp, device=tok.device)[None] >= plen[:, None])[
                :, None, None, None, :
            ]
            scale = 1.0 / math.sqrt(dh)
            for li, blk in enumerate(self.blocks):
                q, k, v = blk.split(blk.ln1(x))
                Kn[li][:, :, t] = k.view(R, Hkv, dh)
                Vn[li][:, :, t] = v.view(R, Hkv, dh)
                qg = q.view(G, S, Hkv, grp, dh)
                Kp, Vp = kvs[li]
                sp = torch.einsum("gskhd,gktd->gskht", qg, Kp).float() * scale
                sp = sp.masked_fill(pmask, float("-inf"))
                sn = (
                    torch.einsum(
                        "rkhd,rktd->rkht", q.view(R, Hkv, grp, dh), Kn[li][:, :, : t + 1]
                    ).float()
                    * scale
                )
                sp = sp.reshape(R, Hkv, grp, Tp)
                a = torch.softmax(torch.cat([sp, sn], -1), -1).to(x.dtype)
                ap, an = a[..., :Tp], a[..., Tp:]
                op = torch.einsum("gskht,gktd->gskhd", ap.reshape(G, S, Hkv, grp, Tp), Vp).reshape(
                    R, Hkv, grp, dh
                )
                on = torch.einsum("rkht,rktd->rkhd", an, Vn[li][:, :, : t + 1])
                o = (op + on).reshape(R, H * dh)
                x = x + blk.proj(o)
                x = x + blk.fc2(F.gelu(blk.fc1(blk.ln2(x))))
            return self.head(x)

    return GPT()


def cfg_from(tr):
    """Model config dict for a trial description."""
    heads = max(1, tr["d_model"] // 64)
    return dict(
        d_model=tr["d_model"],
        layers=tr["layers"],
        heads=heads,
        kv_heads=max(1, heads // 3) if heads >= 3 else 1,
        ff=4,
        dropout=tr["dropout"],
        init_seed=tr.get("init_seed", 0),
    )


def trial_list():
    B = C.B
    if B["tiny"]:
        return [
            dict(
                name="tiny0",
                d_model=64,
                layers=2,
                dropout=0.1,
                lr=2e-3,
                wd=0.05,
                p_oov=0.0,
                init_seed=0,
            ),
            dict(
                name="tiny1",
                d_model=64,
                layers=2,
                dropout=0.2,
                lr=1e-3,
                wd=0.1,
                p_oov=0.05,
                init_seed=1,
            ),
        ][: B["trials"]]
    base = [
        dict(
            name="default",
            d_model=384,
            layers=6,
            dropout=0.2,
            lr=6e-4,
            wd=0.1,
            p_oov=0.05,
            init_seed=0,
        ),
        dict(
            name="large",
            d_model=512,
            layers=10,
            dropout=0.3,
            lr=4e-4,
            wd=0.1,
            p_oov=0.05,
            init_seed=1,
        ),
    ]
    rng = random.Random(SEED)
    out = list(base)
    i = 2
    while len(out) < B["trials"]:
        out.append(
            dict(
                name=f"rand{i}",
                d_model=rng.choice([256, 384]),
                layers=rng.choice([4, 6, 8]),
                dropout=rng.choice([0.1, 0.2, 0.3]),
                lr=rng.choice([3e-4, 6e-4, 1e-3]),
                wd=rng.choice([0.05, 0.1]),
                p_oov=rng.choice([0.0, 0.05]),
                init_seed=i,
            )
        )
        i += 1
    return out[: B["trials"]]


# ------------------------------------------------------------------------------ batching / loss


def make_batch(idx, augment_p=0.0, salt=0):
    """(x, y, loss_mask, slots) for game indices ``idx``; right-padded with PAD."""
    torch = C.torch
    T = min(max(int(C.offsets[i + 1] - C.offsets[i]) for i in idx), C.CTX)
    B = len(idx)
    toks = np.full((B, T), C.V.pad, dtype=np.int64)
    sl = np.zeros((B, T), dtype=np.int64)
    for r, i in enumerate(idx):
        a, b = int(C.offsets[i]), int(C.offsets[i + 1])
        n = min(b - a, T)
        toks[r, :n] = C.tokens[a : a + n]
        sl[r, :n] = C.slots[a : a + n]
    t = torch.from_numpy(toks)
    if augment_p > 0:
        h = (t * 2654435761 + (torch.arange(B)[:, None] + 1 + salt) * 40503) & 0xFFFF
        rep = (t >= C.V.pl0) & (h < int(augment_p * 65536))
        t = torch.where(rep, C.V.new0 + (t % (C.V.n_new - 1)), t)
    s = torch.from_numpy(sl)
    dev = C.device
    return t[:, :-1].to(dev), t[:, 1:].to(dev), (s[:, 1:] > 0).to(dev), s[:, 1:].to(dev)


def category_ids(slots, y):
    """Loss-category index per target token (-1 = not learned)."""
    torch = C.torch
    cat = torch.full_like(slots, -1)
    cat = torch.where(slots == SL_DT, 0, cat)
    cat = torch.where(slots == SL_EVT, 1, cat)
    is_new = (y >= C.V.new0) & (y < C.V.pl0)
    cat = torch.where(slots == SL_ACTOR, torch.where(is_new, 3, 2), cat)
    cat = torch.where(slots == SL_ASSIST, 4, cat)
    cat = torch.where(slots == SL_OUT, 5, cat)
    cat = torch.where(slots == SL_IN, 6, cat)
    return cat


def evaluate_nll(model, game_idx, bs=8):
    """Per-game, per-category summed NLL and counts. Returns (sums [n,N_CAT], counts [n,N_CAT])."""
    torch = C.torch
    model.eval()
    sums = np.zeros((len(game_idx), N_CAT))
    cnts = np.zeros((len(game_idx), N_CAT))
    with torch.no_grad():
        for s in range(0, len(game_idx), bs):
            chunk = game_idx[s : s + bs]
            x, y, m, sl = make_batch(chunk)
            ctx = (
                torch.autocast(C.device, dtype=C.amp)
                if C.amp is not None
                else torch.autocast("cpu", enabled=False)
            )
            with ctx:
                logits = model(x)
            nll = torch.nn.functional.cross_entropy(
                logits.float().transpose(1, 2), y, reduction="none"
            )
            cat = category_ids(sl, y)
            for c in range(N_CAT):
                sel = (cat == c).float()
                sums[s : s + len(chunk), c] = (nll * sel).sum(1).cpu().numpy()
                cnts[s : s + len(chunk), c] = sel.sum(1).cpu().numpy()
    return sums, cnts


# ------------------------------------------------------------------------------ training


def data_hash():
    blob = json.dumps(
        [
            len(C.games),
            C.budget_name,
            CODE_VERSION,
            EXPERIMENT_TAG,
            SEED,
            sorted((k, str(v)) for k, v in C.B.items()),
            int(C.tokens.astype(np.int64).sum()),
            int(C.offsets[-1]),
            C.vocab["size"],
        ],
        default=str,
    )
    return hashlib.sha1(blob.encode()).hexdigest()


def train_trial(ti, tr, ck):
    """Train one config with per-epoch crash-safe checkpoints; returns its result dict."""
    torch = C.torch
    tdir = ck / f"trial_{ti}"
    tdir.mkdir(exist_ok=True)
    done_path = ck / f"trial_{ti}.json"
    if done_path.exists():
        log(f"trial {ti} ({tr['name']}) already complete: RESUMED from {done_path.name}")
        return json.loads(done_path.read_text())
    cfg = cfg_from(tr)
    model = make_model(cfg, C.V.size, C.CTX).to(C.device)
    n_par = sum(p.numel() for p in model.parameters())
    decay = [p for n, p in model.named_parameters() if p.ndim >= 2]
    no_decay = [p for n, p in model.named_parameters() if p.ndim < 2]
    opt = torch.optim.AdamW(
        [{"params": decay, "weight_decay": tr["wd"]}, {"params": no_decay, "weight_decay": 0.0}],
        lr=tr["lr"],
        betas=(0.9, 0.95),
    )
    scaler = torch.amp.GradScaler(enabled=(C.amp == torch.float16))
    B = C.B
    train_idx = C.idx["train"]
    val_idx = C.idx["val"]
    bs = B["batch"]
    spe = max(1, math.ceil(len(train_idx) / bs))
    total = spe * B["max_epochs"]
    warm = max(1, int(0.05 * total))

    def lr_at(step):
        if step < warm:
            return tr["lr"] * (step + 1) / warm
        pr = (step - warm) / max(1, total - warm)
        return tr["lr"] * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(1.0, pr))))

    state = dict(epoch=0, best_val=float("inf"), best_epoch=-1, bad=0, history=[], step=0)
    last = tdir / "last.pt"
    if last.exists():
        ckpt = torch.load(last, map_location=C.device, weights_only=False)
        model.load_state_dict(ckpt["model"])
        opt.load_state_dict(ckpt["opt"])
        scaler.load_state_dict(ckpt["scaler"])
        state = ckpt["state"]
        log(
            f"trial {ti} ({tr['name']}): RESUMED at epoch {state['epoch']} (best {state['best_val']:.4f})"
        )
    log(
        f"trial {ti} {tr['name']}: {n_par / 1e6:.1f}M params cfg={cfg} lr={tr['lr']} p_oov={tr['p_oov']}"
    )
    best_path = tdir / "best.pt"
    t_trial = time.monotonic()
    while state["epoch"] < B["max_epochs"] and state["bad"] < B["patience"]:
        ep = state["epoch"]
        t0 = time.monotonic()
        model.train()
        order = np.random.default_rng(SEED + 1000 * ti + ep).permutation(train_idx)
        tot, cnt = 0.0, 0
        for bi in range(spe):
            chunk = order[bi * bs : (bi + 1) * bs]
            if len(chunk) == 0:
                continue
            for g in opt.param_groups:
                g["lr"] = lr_at(state["step"])
            x, y, m, _ = make_batch(chunk, tr["p_oov"], salt=ep * 7919 + bi)
            ctx = (
                torch.autocast(C.device, dtype=C.amp)
                if C.amp is not None
                else torch.autocast("cpu", enabled=False)
            )
            with ctx:
                logits = model(x)
            nll = torch.nn.functional.cross_entropy(
                logits.float().transpose(1, 2), y, reduction="none"
            )
            loss = (nll * m).sum() / m.sum().clamp(min=1)
            opt.zero_grad(set_to_none=True)
            if not torch.isfinite(loss):
                C.n_nonfinite = getattr(C, "n_nonfinite", 0) + 1
                continue
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            state["step"] += 1
            tot += float(loss) * float(m.sum())
            cnt += float(m.sum())
        sums, cnts = evaluate_nll(model, val_idx)
        val = float(sums.sum() / max(1.0, cnts.sum()))
        tr_loss = tot / max(1.0, cnt)
        dt = time.monotonic() - t0
        state["epoch"] = ep + 1
        state["history"].append(dict(epoch=ep + 1, train=tr_loss, val=val, sec=dt))
        improved = val < state["best_val"] - 1e-4
        if improved:
            state.update(best_val=val, best_epoch=ep + 1, bad=0)
            atomic_save(torch, {"model": model.state_dict(), "cfg": cfg}, best_path)
        else:
            state["bad"] += 1
        log(
            f"  trial {ti} ep {ep + 1:>2}: train {tr_loss:.4f} val {val:.4f} (ppl {math.exp(val):.3f}) {dt:.0f}s {'*' if improved else ''}"
        )
        if ti == 0 and ep == 0:
            proj = dt * B["max_epochs"] * 0.6 * B["trials"]
            log(
                f"  MEASURED PROJECTION: ~{dt:.0f}s/epoch -> search ~{proj / 60:.0f} min if each trial runs ~60% of {B['max_epochs']} epochs"
            )
        atomic_save(
            torch,
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "scaler": scaler.state_dict(),
                "state": state,
            },
            last,
        )
        maybe_crash("epoch_done")
    res = dict(
        index=ti,
        trial=tr,
        cfg=cfg,
        n_params=n_par,
        best_val=state["best_val"],
        best_epoch=state["best_epoch"],
        epochs_run=state["epoch"],
        history=state["history"],
        sec=time.monotonic() - t_trial,
    )
    write_json(done_path, res)
    if last.exists():
        last.unlink()
    return res


def run_search(ck):
    torch = C.torch
    results = []
    for ti, tr in enumerate(trial_list()):
        results.append(train_trial(ti, tr, ck))
        maybe_crash("trial_done")
    best = min(results, key=lambda r: r["best_val"])
    best_src = ck / f"trial_{best['index']}" / "best.pt"
    atomic_save(
        torch, torch.load(best_src, map_location="cpu", weights_only=False), ck / "best_overall.pt"
    )
    return best, results


def load_best(ck):
    torch = C.torch
    obj = torch.load(ck / "best_overall.pt", map_location=C.device, weights_only=False)
    model = make_model(obj["cfg"], C.V.size, C.CTX).to(C.device)
    model.load_state_dict(obj["model"])
    model.eval()
    return model, obj["cfg"]


# ------------------------------------------------------------------------------ batched sampler (twin of pbp_tokens.StreamState)


def init_state(T, rows_game, scal, rost, on, S, plen_tok):
    """Vectorised machine state for G games x S sims (row r -> game r // S)."""
    torch = C.torch
    dev = C.device
    G = len(rows_game)
    R = G * S
    rep = lambda a: a.repeat_interleave(S, 0)  # noqa: E731
    sc = torch.tensor(scal, dtype=torch.float64, device=dev)
    st = SimpleNamespace()
    st.G, st.S, st.R = G, S, R
    st.T = T
    st.rost = rep(
        torch.tensor(rost, dtype=torch.long, device=dev)
    )  # [R,2,RM] (rows may add players)
    st.on = rep(torch.tensor(on, dtype=torch.bool, device=dev))  # [R,2,RM]
    st.score = rep(sc[:, 2:4].long())
    st.period = rep(sc[:, 4].long())
    st.tclock = rep(sc[:, 5].float())
    st.grid = rep(sc[:, 6].long())
    st.lb = rep(sc[:, 7].long())
    st.fouls = rep(sc[:, 8:10].long())
    st.phase = torch.zeros(R, dtype=torch.long, device=dev)
    st.q = torch.zeros(R, 16, dtype=torch.long, device=dev)
    st.qptr = torch.zeros(R, dtype=torch.long, device=dev)
    st.qlen = torch.zeros(R, dtype=torch.long, device=dev)
    st.cur_e = torch.zeros(R, dtype=torch.long, device=dev)
    st.k = torch.zeros(R, dtype=torch.long, device=dev)
    st.out_j = torch.zeros(R, dtype=torch.long, device=dev)
    st.cur_actor = torch.zeros(R, dtype=torch.long, device=dev)
    st.done = torch.zeros(R, dtype=torch.bool, device=dev)
    st.dead = torch.zeros(R, dtype=torch.bool, device=dev)
    st.pstat = torch.zeros(R, 2, C.RM, 3, dtype=torch.int16, device=dev)
    st.game_of = torch.arange(R, device=dev) // S
    return st


def build_block(st, rows, force_lineup):
    """Block token matrix [n,16] (and lineup flags) for ``rows`` (python StreamState._block twin)."""
    torch = C.torch
    V = C.V
    T = st.T
    diff = (st.score[rows, 0] - st.score[rows, 1]).clamp(-60, 60) + 60
    sc = T.sc_tab[diff]
    per = V.per0 + (st.period[rows].clamp(max=6) - 1)
    clk = V.clk0 + (st.tclock[rows] // 30).long().clamp(max=V.n_clk - 1)
    bon = V.bon0 + (st.fouls[rows, 1] >= 5).long() + 2 * (st.fouls[rows, 0] >= 5).long()
    big = V.size + 10
    sides = []
    for s in (0, 1):
        cand = st.rost[rows, s]
        key = torch.where(st.on[rows, s], cand, torch.full_like(cand, big))
        sides.append(key.sort(1).values[:, :5])
    n = len(rows)
    m_state = torch.full((n,), V.m_state, dtype=torch.long, device=C.device)
    m_line = torch.full((n,), V.m_lineup, dtype=torch.long, device=C.device)
    blk = torch.stack([m_state, sc, per, clk, bon, m_line], 1)
    blk = torch.cat([blk, sides[0], sides[1]], 1)
    lineup = force_lineup | (st.lb[rows] >= 2)
    return blk, lineup


def choose(st, logits, gen, forced_tok=None):
    """Next token for every row: queued forced token, else a constrained draw (or ``forced_tok``)."""
    torch = C.torch
    V = C.V
    T = st.T
    R = st.R
    ar = torch.arange(R, device=C.device)
    queued = (st.qptr < st.qlen) & ~st.done
    qtok = st.q[ar, st.qptr.clamp(max=15)]
    if forced_tok is not None:
        tok = torch.where(queued, qtok, forced_tok)
        return torch.where(st.done, torch.full_like(tok, V.pad), tok), queued
    lg = logits.float()
    out = torch.full_like(lg, float("-inf"))
    neg = float("-inf")
    free = ~queued & ~st.done
    m_dt = (free & (st.phase == 0))[:, None]
    out[:, V.dt0 : V.dt0 + V.n_dt] = torch.where(
        m_dt, lg[:, V.dt0 : V.dt0 + V.n_dt], torch.tensor(neg, device=C.device)
    )
    m_ev = (free & (st.phase == 1))[:, None]
    evl = lg[:, V.evt0 : V.evt0 + V.n_evt]
    ended = (st.tclock <= 0)[:, None]
    pe_only = torch.full_like(evl, neg)
    pe_idx = T.pe_idx
    pe_only[:, pe_idx] = evl[:, pe_idx]
    evl = torch.where(ended, pe_only, evl)
    out[:, V.evt0 : V.evt0 + V.n_evt] = torch.where(m_ev, evl, torch.tensor(neg, device=C.device))
    m_pl = free & (st.phase == 2)
    if bool(m_pl.any()):
        side = T.side_c[st.cur_e]
        code = torch.where(st.k == 0, T.pay0[st.cur_e], T.pay1[st.cur_e])
        cand = st.rost[ar, side]  # [R,RM]
        on_side = st.on[ar, side]  # [R,RM]
        ok_on = on_side.clone()
        ok_in = ~on_side & (cand != V.pad)
        ok = torch.where((code == SL_IN)[:, None], ok_in, ok_on)
        ok = ok & ~((code == SL_ASSIST)[:, None] & (cand == st.cur_actor[:, None]))
        vals = torch.where(ok, lg.gather(1, cand), torch.tensor(neg, device=C.device))
        vals = torch.where(m_pl[:, None], vals, torch.tensor(neg, device=C.device))
        out.scatter_(1, cand, torch.maximum(out.gather(1, cand), vals))
        out[:, V.pad] = neg
    invalid = ~torch.isfinite(out.max(1).values)
    st.dead |= invalid & free  # a free row with no legal token: truncate it (counted)
    out = torch.where(invalid[:, None], torch.zeros_like(out), out)
    probs = torch.softmax(out, -1)
    samp = torch.multinomial(probs, 1, generator=gen)[:, 0]
    tok = torch.where(queued, qtok, samp)
    tok = torch.where(st.done | st.dead, torch.full_like(tok, V.pad), tok)
    return tok, queued


def advance(st, tok, queued):
    """Apply ``tok`` to the machine state (python StreamState.feed twin)."""
    torch = C.torch
    V = C.V
    T = st.T
    R = st.R
    ar = torch.arange(R, device=C.device)
    live = ~st.done & ~st.dead
    # forced tokens: just consume
    fq = queued & live
    st.qptr += fq.long()
    st.done |= fq & (tok == V.eos)
    free = live & ~queued
    m_dt = free & (st.phase == 0)
    m_ev = free & (st.phase == 1)
    mp = free & (st.phase == 2)
    # --- DT
    k = (tok - V.dt0).clamp(0, V.n_dt - 1)
    st.tclock = torch.where(m_dt, (st.tclock - T.dt_rep[k]).clamp(min=0), st.tclock)
    # --- EVT
    e = (tok - V.evt0).clamp(0, V.n_evt - 1)
    st.cur_e = torch.where(m_ev, e, st.cur_e)
    st.k = torch.where(m_ev, torch.zeros_like(st.k), st.k)
    has_pay = T.npay[e] > 0
    fin = m_ev & ~has_pay
    st.phase = torch.where(m_dt, torch.ones_like(st.phase), st.phase)
    st.phase = torch.where(m_ev & has_pay, torch.full_like(st.phase, 2), st.phase)
    # --- payload
    e = st.cur_e
    side = T.side_c[e]
    code = torch.where(st.k == 0, T.pay0[e], T.pay1[e])
    cand = st.rost[ar, side]
    eq = cand == tok[:, None]
    found = eq.any(1)
    j = eq.float().argmax(1)
    is_actor = mp & (code == SL_ACTOR)
    is_ast = mp & (code == SL_ASSIST)
    is_out = mp & (code == SL_OUT)
    is_in = mp & (code == SL_IN)
    # an unseen player entering the game takes a free roster slot (or the slot of who left)
    unk_in = is_in & ~found
    if bool(unk_in.any()):
        free = cand == V.pad
        jj = torch.where(free.any(1), free.float().argmax(1), st.out_j)
        r = ar[unk_in]
        st.rost[r, side[unk_in], jj[unk_in]] = tok[unk_in]
        j = torch.where(unk_in, jj, j)
        found = found | unk_in
    st.cur_actor = torch.where(is_actor, tok, st.cur_actor)
    ok_a = (is_actor & found).long()
    st.pstat[ar, side, j, 0] += (ok_a * T.pts[e]).to(torch.int16)
    st.pstat[ar, side, j, 1] += (ok_a * T.reb[e]).to(torch.int16)
    st.pstat[ar, side, j, 2] += (is_ast & found).to(torch.int16)
    st.out_j = torch.where(is_out & found, j, st.out_j)
    do_swap = is_in & found
    if bool(do_swap.any()):
        r = ar[do_swap]
        st.on[r, side[do_swap], st.out_j[do_swap]] = False
        st.on[r, side[do_swap], j[do_swap]] = True
    st.k = st.k + mp.long()
    fin = fin | (mp & (st.k >= T.npay[e]))
    # --- event completion
    if bool(fin.any()):
        fi = ar[fin]
        sd = T.side_c[st.cur_e[fi]]
        has_side = (T.side[st.cur_e[fi]] >= 0).long()
        st.score[fi, sd] += T.pts[st.cur_e[fi]] * has_side
        st.fouls[fi, sd] += T.foul_d[st.cur_e[fi]] * has_side
        st.phase[fi] = 0
        pe = fin & (T.pe[st.cur_e] > 0)
        end_game = pe & (st.period >= 4) & (st.score[:, 0] != st.score[:, 1])
        newp = pe & ~end_game
        st.period = st.period + newp.long()
        st.tclock = torch.where(newp, torch.where(st.period > 4, 300.0, 720.0).float(), st.tclock)
        st.grid = torch.where(newp, (st.tclock // 120).long(), st.grid)
        st.fouls = torch.where(newp[:, None], torch.zeros_like(st.fouls), st.fouls)
        g = (st.tclock // 120).long()
        trig = fin & ~pe & (g < st.grid)
        st.grid = torch.where(trig, g, st.grid)
        need = newp | trig
        if bool(need.any()):
            rows = ar[need]
            blk, lineup = build_block(st, rows, newp[rows])
            st.q[rows] = blk
            st.qlen[rows] = torch.where(lineup, 16, 5)
            st.qptr[rows] = 0
            st.lb[rows] = torch.where(lineup, torch.zeros_like(st.lb[rows]), st.lb[rows] + 1)
        if bool(end_game.any()):
            rows = ar[end_game]
            st.q[rows, 0] = V.eos
            st.qlen[rows] = 1
            st.qptr[rows] = 0


def sample_units(model, T, prefixes, scal, rost, on, S, max_new, gen, forced=None):
    """Sample ``S`` continuations for each of G prefixes. Returns a dict of tensors/arrays.

    ``prefixes``: list of int arrays (token prefixes, each ending at a checkpoint boundary).
    ``forced``: optional [G*S, >=max_new] token matrix for teacher forcing (tests).
    """
    torch = C.torch
    dev = C.device
    G = len(prefixes)
    R = G * S
    plens = np.array([len(p) for p in prefixes])
    Tp = int(plens.max())
    pad = np.full((G, Tp), C.V.pad, dtype=np.int64)
    for i, p in enumerate(prefixes):
        pad[i, : len(p)] = p
    cdt = next(model.parameters()).dtype
    with torch.no_grad():
        xin = torch.from_numpy(pad).to(dev)
        logits, kvs, hid = model(xin, return_kv=True)
        plen_t = torch.tensor(plens, device=dev)
        last = hid[torch.arange(G, device=dev), plen_t - 1]
        l0 = model.head(last)
        blk0 = model.blocks[0]
        Hkv, dh = blk0.hkv, blk0.dh
        L = len(model.blocks)
        Kn = [torch.zeros(R, Hkv, max_new, dh, dtype=cdt, device=dev) for _ in range(L)]
        Vn = [torch.zeros(R, Hkv, max_new, dh, dtype=cdt, device=dev) for _ in range(L)]
        kvs = [(k.to(cdt), v.to(cdt)) for k, v in kvs]
        st = init_state(T, range(G), scal, rost, on, S, plens)
        logit_rows = l0.repeat_interleave(S, 0)
        plen_rows = plen_t.repeat_interleave(S, 0)
        outs = []
        steps = 0
        for t in range(max_new):
            ft = None if forced is None else forced[:, t]
            tok, queued = choose(st, logit_rows, gen, ft)
            advance(st, tok, queued)
            outs.append(tok)
            steps += 1
            if bool((st.done | st.dead).all()):
                break
            logit_rows = model.step(tok, plen_rows + t, t, kvs, Kn, Vn, G, S, plen_t)
        margin = (st.score[:, 0] - st.score[:, 1]).view(G, S)
        total = (st.score[:, 0] + st.score[:, 1]).view(G, S)
    return dict(
        margin=margin.cpu().numpy(),
        total=total.cpu().numpy(),
        done=st.done.view(G, S).cpu().numpy(),
        dead=st.dead.view(G, S).cpu().numpy(),
        pstat=st.pstat.view(G, S, 2, C.RM, 3).cpu().numpy(),
        steps=steps,
        tokens=torch.stack(outs, 1).cpu().numpy(),
        score=st.score.cpu().numpy(),
        state=st,
    )


# ------------------------------------------------------------------------------ evaluation sampling


def pick_games():
    """Deterministic evaluation game list from the REPORT split (valid for every checkpoint)."""
    g = C.games
    ok = g["ok"].to_numpy() & (g["split"].to_numpy() == "report")
    sc = C.ck["scal"]
    valid = sc[:, :, 0].min(1) > 0
    nr = np.minimum(g["n_roster_h"].to_numpy(), g["n_roster_a"].to_numpy())
    cand = np.where(ok & valid & (nr >= 9))[0]
    rng = np.random.default_rng(SEED)
    order = rng.permutation(cand)
    B = C.B
    live = order[: B["n_live"]]
    tip = live[: B["n_tip"]]
    return live, tip


def max_new_for(scal_rows):
    """Token cap from game time remaining (plus 300 s overtime slack) x train tokens/sec x 1.35."""
    rem = scal_rows[:, 5] + np.clip(4 - scal_rows[:, 4], 0, 4) * 720.0 + 300.0
    return int(min(C.CTX - 8, math.ceil(C.tok_per_sec * float(rem.max()) * 1.35) + 80))


def run_sampling(model, ck, out):
    torch = C.torch
    T = get_tables()
    live, tip = pick_games()
    B = C.B
    sd = ck / "samples"
    sd.mkdir(exist_ok=True)
    cdt = torch.float32
    if C.device == "cuda":
        cdt = C.amp
        model.to(cdt)
    stats = dict(rows=0, tokens=0, sec=0.0, chunks=0, by_ckpt={})
    L = len(model.blocks)
    blk0 = model.blocks[0]
    res_paths = {}
    for c in range(5):
        games = tip if c == 0 else live
        S = B["sims"][c]
        if len(games) == 0:
            continue
        scal_all = C.ck["scal"][games, c]
        mx = max_new_for(scal_all)
        per_row_bytes = L * 2 * blk0.hkv * blk0.dh * mx * (2 if C.device == "cuda" else 4)
        rows_cap = max(1, min(B["max_rows"], int(B["kv_gb"] * 1e9 // per_row_bytes)))
        gpc = max(1, rows_cap // S)
        chunks = [games[i : i + gpc] for i in range(0, len(games), gpc)]
        log(
            f"sampling ckpt {CKPT_NAMES[c]}: {len(games)} games x {S} sims, <= {mx} new tokens, {gpc} games / chunk ({gpc * S} rows)"
        )
        parts = []
        for ci, ch in enumerate(chunks):
            path = sd / f"c{c}_{ci}.npz"
            if path.exists():
                z = np.load(path)
                parts.append({k: z[k] for k in z.files})
                log(f"  chunk {c}/{ci} RESUMED")
                continue
            t0 = time.monotonic()
            prefixes = []
            for gi in ch:
                a = int(C.offsets[gi])
                prefixes.append(C.tokens[a : a + int(C.ck["scal"][gi, c, 1])].astype(np.int64))
            gen = torch.Generator(device=C.device).manual_seed(SEED + 1000 * c + ci)
            mxc = min(max_new_for(C.ck["scal"][ch, c]), C.CTX - max(len(p) for p in prefixes) - 1)
            r = sample_units(
                model,
                T,
                prefixes,
                C.ck["scal"][ch, c],
                C.ck["rost"][ch, c],
                C.ck["on"][ch, c],
                S,
                mxc,
                gen,
            )
            sec = time.monotonic() - t0
            n_rows = len(ch) * S
            ntok = n_rows * r["steps"]
            stats["rows"] += n_rows
            stats["tokens"] += ntok
            stats["sec"] += sec
            stats["chunks"] += 1
            part = dict(
                game_idx=np.asarray(ch),
                margin=r["margin"].astype(np.int16),
                total=r["total"].astype(np.int16),
                done=r["done"],
                dead=r["dead"],
                pstat=np.clip(r["pstat"], 0, 255).astype(np.uint8),
                steps=np.array([r["steps"]]),
            )
            np.savez_compressed(str(path) + ".tmp.npz", **part)
            os.replace(str(path) + ".tmp.npz", path)
            parts.append(part)
            log(
                f"  chunk {c}/{ci}: {len(ch)} games x {S} sims, {r['steps']} steps, {sec:.1f}s, {n_rows / sec:.0f} rows/s, {ntok / sec:,.0f} tok/s"
            )
            if stats["chunks"] == 1:
                n_chunks_total = sum(
                    math.ceil(
                        (len(tip) if cc == 0 else len(live))
                        / max(
                            1,
                            min(B["max_rows"], int(B["kv_gb"] * 1e9 // per_row_bytes))
                            // B["sims"][cc],
                        )
                    )
                    for cc in range(5)
                )
                log(
                    f"  MEASURED PROJECTION: ~{sec * n_chunks_total / 60:.0f} min for all sampling chunks (first chunk scaled; later checkpoints are cheaper)"
                )
            maybe_crash("chunk_done")
        merged = {
            k: np.concatenate([p[k] for p in parts], 0)
            for k in ("game_idx", "margin", "total", "done", "dead", "pstat")
        }
        res_paths[c] = out / f"samples_{CKPT_NAMES[c]}.npz"
        np.savez_compressed(res_paths[c], **merged)
        stats["by_ckpt"][CKPT_NAMES[c]] = dict(
            n_games=int(len(merged["game_idx"])),
            sims=int(S),
            frac_done=float(merged["done"].mean()),
            frac_dead=float(merged["dead"].mean()),
        )
    stats["rows_per_s"] = stats["rows"] / max(1e-9, stats["sec"])
    stats["tokens_per_s"] = stats["tokens"] / max(1e-9, stats["sec"])
    return stats


# ------------------------------------------------------------------------------ main


def per_game_table(model, split):
    idx = C.idx[split]
    sums, cnts = evaluate_nll(model, idx)
    df = pd.DataFrame(
        {"game_idx": idx, "game_id": C.games["game_id"].to_numpy()[idx], "split": split}
    )
    for i, c in enumerate(CATS):
        df[f"nll_{c}"] = sums[:, i]
        df[f"n_{c}"] = cnts[:, i]
    return df


def main():
    setup()
    load()
    torch = C.torch
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
    meta_run = {
        "experiment": EXPERIMENT_TAG,
        "code_version": CODE_VERSION,
        "git": C.git,
        "seed": SEED,
        "budget": C.budget_name,
        "budget_cfg": C.B,
        "device": C.device_info,
        "n_games": len(C.games),
        "splits": {k: int(len(v)) for k, v in C.idx.items()},
        "vocab_size": C.V.size,
        "tokens_total": int(len(C.tokens)),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(out / "data_summary.json", meta_run)
    write_json(out / "metrics_partial.json", {"complete": False, "stage": "search", **meta_run})
    timings = {}
    t0 = time.monotonic()
    best, results = run_search(ck)
    timings["search_s"] = time.monotonic() - t0
    write_json(out / "search_log.json", {"trials": results, "best": best["index"]})
    write_json(
        out / "best_config.json",
        {
            "cfg": best["cfg"],
            "trial": best["trial"],
            "best_epoch": best["best_epoch"],
            "val": best["best_val"],
            "seed": SEED,
            "selected_on": "2023 val slice only",
        },
    )
    write_json(
        out / "metrics_partial.json",
        {"complete": False, "stage": "eval_nll", "best": best["index"], **meta_run},
    )
    model, cfg = load_best(ck)
    atomic_save(
        torch,
        {
            "model": {
                k: v.half() if v.dtype == torch.float32 else v
                for k, v in model.state_dict().items()
            },
            "cfg": cfg,
            "vocab_size": C.V.size,
            "ctx": C.CTX,
        },
        out / "weights.pt",
    )
    t0 = time.monotonic()
    val_df = per_game_table(model, "val")
    rep_df = per_game_table(model, "report")
    pd.concat([val_df, rep_df]).to_parquet(out / "ppl_by_game.parquet", index=False)
    timings["nll_s"] = time.monotonic() - t0
    write_json(out / "training_log.json", {"trials": results, "timings_s": timings})
    write_json(
        out / "metrics_partial.json",
        {"complete": False, "stage": "sampling", "best": best["index"], **meta_run},
    )
    t0 = time.monotonic()
    model.eval()
    samp = run_sampling(model, ck, out)
    timings["sampling_s"] = time.monotonic() - t0
    nll = {}
    for name, df in (("val", val_df), ("report", rep_df)):
        tot = sum(df[f"nll_{c}"].sum() for c in CATS)
        n = sum(df[f"n_{c}"].sum() for c in CATS)
        nll[name] = dict(
            nll=float(tot / max(1, n)),
            ppl=float(math.exp(tot / max(1, n))),
            n_tokens=int(n),
            by_category={c: float(df[f"nll_{c}"].sum() / max(1, df[f"n_{c}"].sum())) for c in CATS},
        )
    # copy the small inputs the local evaluator needs
    metrics = {
        "complete": True,
        "experiment": EXPERIMENT_TAG,
        "code_version": CODE_VERSION,
        "git": C.git,
        "seed": SEED,
        "budget": C.budget_name,
        "device": C.device_info,
        "best_trial": best["trial"],
        "best_cfg": cfg,
        "best_epoch": best["best_epoch"],
        "val_nll_2023_slice": best["best_val"],
        "nll": nll,
        "sampling": samp,
        "timings_s": timings,
        "wall_s": time.monotonic() - C.t0,
        "n_nonfinite_batches_skipped": int(getattr(C, "n_nonfinite", 0)),
        "touches_holdout": False,
        "season_2025_loaded": False,
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
