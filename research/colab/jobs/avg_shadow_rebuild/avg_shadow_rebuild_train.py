# mypy: ignore-errors
# Colab-executed job script (untyped torch on the Colab VM); kept out of strict type checking.
"""Sequence props model (self-contained: numpy + pandas + torch only; runs on Colab).

Input: ``seq_props.npz`` from ``research.features.player_sequences`` (NBA_PARQUET points to it).
Each row is one PLAYED target game with the player's last K prior team games (absent
games kept as flagged tokens) and pre-tip context. Output: 19 monotone quantiles
(taus 0.05..0.95) + a mean for pts/reb/ast/fg3m, as a residual over the shrunk
recency mean (``base``).

BUDGET (single knob, env NBA_BUDGET):
  fast (default): transformer, 2 seeds, train-through-2023 ONCE (early stop on the
      time-ordered 2023 validation slice), predict all of 2024. ~10-15 min on a T4.
  full: transformer + GRU arms, 3 seeds, warm-start walk-forward refit before every
      2024 month block. ~35-50 min on a T4.
Season 2025 (frozen holdout) is refused: the loader raises if it is present.
Seeds are logged in config.json. Mixed precision (autocast + GradScaler) on CUDA.
"""

import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn

STATS = ("pts", "reb", "ast", "fg3m")
N_Q = 19
TAUS = np.arange(1, N_Q + 1) / 20.0
FROZEN_SEASON = 2025
TEST_SEASON = 2024
VAL_START = "2024-02-01"  # 2023-season rows on/after this are the early-stopping slice

PRESETS: dict[str, dict[str, Any]] = {
    "fast": dict(
        arms=("transformer",), seeds=(0, 1), epochs=18, patience=3, batch=1024, refit="once",
        est="~10-15 min on a T4 (about 1 min on CPU smoke at NBA_SAMPLE_ROWS=4000)",
    ),
    "full": dict(
        arms=("transformer", "gru"), seeds=(0, 1, 2), epochs=25, patience=4, batch=1024,
        refit="walk_forward", est="~35-50 min on a T4",
    ),
}  # fmt: skip


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# --------------------------------------------------------------------------- data


def load_npz(path: Path) -> dict[str, np.ndarray]:
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if int(out["season"].max()) >= FROZEN_SEASON:
        raise ValueError("season 2025 is the frozen holdout and must not be in the input")
    return out


def subsample(d: dict[str, np.ndarray], n: int) -> dict[str, np.ndarray]:
    total = len(d["y"])
    if total <= n:
        return d
    idx = np.linspace(0, total - 1, n).astype(np.int64)
    return {k: (v[idx] if v.ndim and len(v) == total else v) for k, v in d.items()}


def build_vocab(player_id: np.ndarray, rows: np.ndarray, min_rows: int = 20) -> dict[int, int]:
    ids, cnt = np.unique(player_id[rows], return_counts=True)
    keep = ids[cnt >= min_rows]
    return {int(p): i + 1 for i, p in enumerate(keep)}  # 0 = OOV / unseen (cold start)


class Tensors:
    """Whole dataset on the device (float16 sequences; cast per batch)."""

    def __init__(self, d: dict[str, np.ndarray], vocab: dict[int, int], dev: torch.device):
        self.n = len(d["y"])
        t = lambda a, dt=None: torch.as_tensor(a if dt is None else a.astype(dt)).to(dev)  # noqa: E731
        self.seq = t(d["seq"])
        self.mask = t(d["mask"], np.bool_)
        self.opp_step = t(d["opp_step"], np.int64)
        self.ctx = t(d["ctx"], np.float32)
        self.pid = t(np.array([vocab.get(int(p), 0) for p in d["player_id"]]), np.int64)
        self.team = t(d["team_idx"], np.int64)
        self.opp = t(d["opp_idx"], np.int64)
        self.y = t(d["y"], np.float32)
        self.base = t(d["base"], np.float32)

    def batch(self, idx: torch.Tensor) -> dict[str, torch.Tensor]:
        return {
            "seq": self.seq[idx].float(), "mask": self.mask[idx], "opp_step": self.opp_step[idx],
            "ctx": self.ctx[idx], "pid": self.pid[idx], "team": self.team[idx],
            "opp": self.opp[idx], "y": self.y[idx], "base": self.base[idx],
        }  # fmt: skip


# --------------------------------------------------------------------------- model


class SeqNet(nn.Module):
    def __init__(
        self, n_step: int, n_ctx: int, n_players: int, stat_scale: np.ndarray, arm: str,
        d: int = 64, layers: int = 2, heads: int = 4, dropout: float = 0.15,
        head_dropout: float = 0.4, k: int = 20,
        id_dropout: float = 0.15, residual: bool = True,
    ) -> None:  # fmt: skip
        super().__init__()
        self.arm, self.k, self.id_dropout, self.residual = arm, k, id_dropout, residual
        self.register_buffer("scale", torch.as_tensor(stat_scale, dtype=torch.float32))
        self.step_in = nn.Linear(n_step, d)
        self.opp_emb = nn.Embedding(40, 8, padding_idx=0)
        self.opp_proj = nn.Linear(8, d, bias=False)
        self.pos = nn.Parameter(torch.zeros(k + 1, d))
        self.player_emb = nn.Embedding(n_players + 1, 16)
        self.team_emb = nn.Embedding(40, 8, padding_idx=0)
        self.tgt_opp_emb = nn.Embedding(40, 8, padding_idx=0)
        self.tgt_in = nn.Linear(n_ctx + 16 + 8 + 8, d)
        if arm == "transformer":
            lay = nn.TransformerEncoderLayer(
                d, heads, 2 * d, dropout, batch_first=True, norm_first=True, activation="gelu"
            )
            self.enc = nn.TransformerEncoder(lay, layers, enable_nested_tensor=False)
        elif arm == "gru":
            self.enc = nn.GRU(d, d, batch_first=True)
        else:
            raise ValueError(arm)
        self.head = nn.Sequential(
            nn.LayerNorm(2 * d), nn.Linear(2 * d, 128), nn.GELU(), nn.Dropout(head_dropout),
            nn.Linear(128, len(STATS) * (1 + N_Q)),
        )  # fmt: skip
        with torch.no_grad():  # start near a sensible spread: q0 ~ -1.6 sd, steps ~ 0.2 sd
            b = self.head[-1].bias.view(len(STATS), 1 + N_Q)
            b.zero_()
            b[:, 1] = -1.6
            b[:, 2:] = -1.5
            self.head[-1].weight.mul_(0.1)

    def forward(self, b: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
        pid = b["pid"]
        if self.training and self.id_dropout > 0:
            drop = torch.rand_like(pid, dtype=torch.float32) < self.id_dropout
            pid = torch.where(drop, torch.zeros_like(pid), pid)
        tgt = self.tgt_in(
            torch.cat(
                [
                    b["ctx"],
                    self.player_emb(pid),
                    self.team_emb(b["team"]),
                    self.tgt_opp_emb(b["opp"]),
                ],
                dim=1,
            )
        )  # fmt: skip
        steps = self.step_in(b["seq"]) + self.opp_proj(self.opp_emb(b["opp_step"]))
        x = torch.cat([steps, tgt[:, None, :]], dim=1) + self.pos[None]
        m = b["mask"]
        if self.arm == "transformer":
            pad = torch.cat([~m, torch.zeros_like(m[:, :1])], dim=1)
            h = self.enc(x, src_key_padding_mask=pad)
            hs = h[:, :-1] * m[..., None]
            pooled = hs.sum(1) / m.sum(1, keepdim=True).clamp(min=1)
            z = torch.cat([h[:, -1], pooled], dim=1)
        else:
            out, hn = self.enc(x[:, :-1] * m[..., None])
            z = torch.cat([hn[-1], tgt], dim=1)
        raw = self.head(z).float().view(-1, len(STATS), 1 + N_Q)
        sc = self.scale[None, :]
        base = b["base"] if self.residual else torch.zeros_like(b["base"])
        mean = base + sc * raw[:, :, 0]
        q0 = base + sc * raw[:, :, 1]
        inc = nn.functional.softplus(raw[:, :, 2:]) * sc[:, :, None] * 0.3
        q = torch.cat([q0[:, :, None], q0[:, :, None] + torch.cumsum(inc, dim=2)], dim=2)
        return mean, q  # (B, S), (B, S, 19), monotone by construction


def loss_fn(
    mean: torch.Tensor, q: torch.Tensor, y: torch.Tensor, scale: torch.Tensor
) -> torch.Tensor:
    tau = torch.as_tensor(TAUS, dtype=torch.float32, device=q.device)
    d = y[:, :, None] - q
    pin = torch.maximum(tau * d, (tau - 1.0) * d).mean(2) / scale[None]
    mse = ((mean - y) / scale[None]) ** 2
    return (pin + 0.1 * mse).sum(1).mean()


# --------------------------------------------------------------------------- train / predict


def run_epoch(net, ts, rows, opt, scaler, batch, dev, train: bool):  # noqa: ANN001
    net.train(train)
    perm = rows[torch.randperm(len(rows), device=rows.device)] if train else rows
    tot, cnt = 0.0, 0
    for i in range(0, len(perm), batch):
        idx = perm[i : i + batch]
        b = ts.batch(idx)
        with torch.set_grad_enabled(train):
            with torch.autocast(
                device_type=dev.type, dtype=torch.float16, enabled=dev.type == "cuda"
            ):
                mean, q = net(b)
            loss = loss_fn(mean.float(), q.float(), b["y"], net.scale)
        if train:
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
        tot += float(loss.detach()) * len(idx)
        cnt += len(idx)
    return tot / max(cnt, 1)


def fit(net, ts, train_rows, val_rows, cfg, dev, lr, epochs, patience):  # noqa: ANN001
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=5e-2)
    scaler = torch.amp.GradScaler("cuda", enabled=dev.type == "cuda")
    best, best_state, bad, hist = math.inf, None, 0, []
    for ep in range(epochs):
        tr = run_epoch(net, ts, train_rows, opt, scaler, cfg["batch"], dev, True)
        if val_rows is None:
            hist.append({"epoch": ep, "train": tr})
            continue
        with torch.no_grad():
            va = run_epoch(net, ts, val_rows, opt, scaler, cfg["batch"] * 2, dev, False)
        assert math.isfinite(tr) and math.isfinite(va), "non-finite loss"
        hist.append({"epoch": ep, "train": tr, "val": va})
        print(f"  ep {ep:2d} train {tr:.4f} val {va:.4f}")
        if va < best - 1e-5:
            best, bad = va, 0
            best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    return best, hist


@torch.no_grad()
def predict(net, ts, rows, batch, dev):  # noqa: ANN001
    net.eval()
    means, qs = [], []
    for i in range(0, len(rows), batch * 2):
        b = ts.batch(rows[i : i + batch * 2])
        with torch.autocast(device_type=dev.type, dtype=torch.float16, enabled=dev.type == "cuda"):
            m, q = net(b)
        means.append(m.float().cpu().numpy())
        qs.append(q.float().cpu().numpy())
    if not means:
        return np.zeros((0, len(STATS))), np.zeros((0, len(STATS), N_Q))
    return np.concatenate(means), np.concatenate(qs)


def ensemble(preds: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Average means and quantile grids across seeds (Vincentization keeps monotonicity)."""
    return np.mean([p[0] for p in preds], axis=0), np.mean([p[1] for p in preds], axis=0)


def pinball(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """(n, S, 19), (n, S) -> (n, S) mean pinball over taus."""
    d = y[:, :, None] - q
    return np.maximum(TAUS * d, (TAUS - 1.0) * d).mean(2)


def baseline_grid(d: dict[str, np.ndarray], fit_rows: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Recency base + global residual quantiles of the FIT rows (constant spread)."""
    res = d["y"][fit_rows] - d["base"][fit_rows]
    off = np.quantile(res, TAUS, axis=0).T  # (S, 19)
    return np.maximum.accumulate(np.clip(d["base"][rows][:, :, None] + off[None], 0, None), axis=2)


# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# AVG_SHADOW_LIVE chain rebuild (docs/prereg/AVG_SHADOW_LIVE_2.md). TAIL appended by build_notebook.py to the model code of
# ../seq_props/seq_props_train.py (everything before its own `main`), so transformer, loss, fit and predict are exactly T478's.
# The T478 walk-forward functions (load_npz_25, concat_rows, month_blocks, to_dev, oof_dict, run_walk_forward) are NOT written here:
# build_notebook.py copies them verbatim from ../seq_full_mix_rt/seq_full_mix_rt_tail.py (AST extraction) and splices ONE block into
# run_walk_forward (the P1b weight saves and the install fit, marked "AVG_SHADOW_LIVE install fit"). Anything else differing is a build error.
# Program E2 only, window WALL (W_ALL: 2019-20, 2020-21, 2021-22 rows in the cold fit and every warm pool), 3 seeds, hidden 64.
# ----------------------------------------------------------------------------------------
import hashlib

MODE = "held"  # the T478 held-out construction (test seasons 2023, 2024, 2025); its code paths are the ones copied below
WINDOW = "WALL"
RULE_SHA = "9afbfeb6e16449f334c159ab9457167b760c03e31692962469b85ee32c92fa87"  # sha256 of the frozen rule text above the results marker (baked in at build time, checked on the VM)
RULE_ID = "AVG_SHADOW_LIVE_2"
RULE_SHA_PIN = "9afbfeb6"  # AVG_SHADOW_LIVE_2 (frozen 2026-10-14): written here by hand, not derived from the staged document, so a different rule cannot build or run
RULE_DOC = "AVG_SHADOW_LIVE_2.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
LOG_ROW_KEY = "Colab chain rebuild for AVG_SHADOW_LIVE"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
FROZEN_SEASON = 2026  # the live season: nothing from 2026-27 is in the input
HIST_SEASONS = (2019, 2020, 2021)
SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"
TEST_SEASONS = (2023, 2024, 2025)
INSTALL_BEFORE = (
    "2026-10-01"  # the install fit's pool: every row dated before the month block it serves
)
INSTALL_MONTH = "2026-10"
# proof P1b: weights saved at these month blocks (all predict rows with season label <= 2024): 2023-12, 2024-02 (season 2023), 2024-12, 2025-02 (season 2024)
P1B_MONTHS = ("2023-12", "2024-02", "2024-12", "2025-02")
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip
EST = (
    "about 12 minutes on a T4 (T478 measured 599 s for the whole E2 program); budget 45, ceiling 60"
)


def load_npz_25(path):
    """Like `load_npz` but admits season 2025 (and nothing later)."""
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if int(out["season"].max()) >= FROZEN_SEASON:
        raise ValueError("a season after 2025 is in the input")
    return out


def concat_rows(a, b):
    """History rows (a) then current rows (b). The non-row keys are taken from a, so they must EQUAL b's: the stat scaling and the step / ctx feature names."""
    na, nb = len(a["y"]), len(b["y"])
    # Per-row metadata outside ROW_KEYS (e.g. date_str, unused by training) is concatenated like a row key.
    per_row = [
        k
        for k in a
        if k not in ROW_KEYS
        and k in b
        and np.asarray(a[k]).shape[:1] == (na,)
        and np.asarray(b[k]).shape[:1] == (nb,)
    ]
    for k in a:
        if k in ROW_KEYS or k in per_row:
            continue
        if k not in b or not np.array_equal(np.asarray(a[k]), np.asarray(b[k])):
            raise AssertionError(
                f"non-row key {k!r} differs between the history and the current export"
            )
    out = {k: np.concatenate([a[k], b[k]]) for k in (*ROW_KEYS, *per_row)}
    for k in a:
        if k not in out:
            out[k] = a[k]
    return out


def month_blocks(date, season, rows):
    per = pd.Series(date[rows]).dt.to_period("M")
    for p in sorted(per.unique()):
        yield p, rows[(per == p).to_numpy()]


def to_dev(rows, dev):
    return torch.as_tensor(rows, device=dev)


def oof_dict(d, rows, mean, q):
    return {
        "game_id": d["game_id"][rows].astype("U12"),
        "player_id": d.get("pid_true", d["player_id"])[rows].astype(np.int64),
        "season": d["season"][rows].astype(np.int16),
        "date_days": d["date_days"][rows].astype(np.int32),
        "mean": np.clip(mean, 0.0, None).astype(np.float32),
        "q": np.maximum.accumulate(np.clip(q, 0.0, None), axis=2).astype(np.float32),
    }


def run_walk_forward(name, d, dev, hidden, out_dir, smoke, window):
    """E2 and its P, S variants: cold fit on 2022, warm refit before each 2023-25 month (ENSEMBLE's E2 loop, test seasons extended)."""
    t0 = time.monotonic()
    seeds = (0,) if smoke else SEEDS
    cold = dict(COLD)
    warm = dict(WARM)
    if smoke:
        cold.update(epochs=1, batch=256)
        warm.update(epochs=1, batch=256)
    date = d["date_days"].astype("datetime64[D]")
    season, nprior = d["season"], d["n_prior"]
    cold_end = np.datetime64(COLD_END)
    is_hist = np.isin(season, HIST_SEASONS)
    fit_m = (nprior >= 3) & (
        ((season == 2022) & (date < cold_end)) | (is_hist if window == "WALL" else False)
    )
    val_m = (nprior >= 3) & (season == 2022) & (date >= cold_end)
    test_m = np.isin(season, TEST_SEASONS)
    assert not (season >= FROZEN_SEASON).any()
    assert window in ("W22", "WALL"), window
    assert is_hist.any() == (window == "WALL"), "history rows are present exactly in WALL"
    assert set(np.unique(season).tolist()) <= {
        2022,
        *TEST_SEASONS,
        *(HIST_SEASONS if window == "WALL" else ()),
    }
    assert int(season.max()) <= max(TEST_SEASONS)
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr, va = to_dev(np.flatnonzero(fit_m), dev), to_dev(np.flatnonzero(val_m), dev)
    n_step, n_ctx = d["seq"].shape[2], d["ctx"].shape[1]
    nets, vals = [], []
    for sd in seeds:
        seed_everything(sd)
        net = SeqNet(n_step, n_ctx, len(vocab), d["stat_scale"], "transformer", d=hidden).to(dev)
        print(
            f"[{name} seed {sd}] cold fit rows {int(fit_m.sum())} val {int(val_m.sum())}",
            flush=True,
        )
        best, _ = fit(net, ts, tr, va, cold, dev, cold["lr"], cold["epochs"], cold["patience"])
        nets.append(net)
        vals.append(float(best))
    frames, detail = [], []
    test_rows = np.flatnonzero(test_m)
    blocks = list(month_blocks(date, season, test_rows))
    for bi, (per, blk) in enumerate(blocks):
        start = np.datetime64(per.start_time.date())
        pool = np.flatnonzero((date < start) & (nprior >= 3))
        prow = to_dev(pool, dev)
        for k_, net in enumerate(nets):
            seed_everything(1000 + k_)
            fit(net, ts, prow, None, warm, dev, warm["lr"], warm["epochs"], 99)
        if (
            str(per) in P1B_MONTHS
        ):  # proof P1b: the weights of this block, saved BEFORE it is predicted, so the CPU can score the same weights the GPU scored
            (out_dir / "p1b" / str(per)).mkdir(parents=True, exist_ok=True)
            for k_, net in enumerate(nets):
                torch.save(
                    {n: p.detach().cpu() for n, p in net.state_dict().items()},
                    out_dir / "p1b" / str(per) / f"seed{seeds[k_]}.pt",
                )
        m, q = ensemble([predict(x, ts, to_dev(blk, dev), cold["batch"], dev) for x in nets])
        frames.append(oof_dict(d, blk, m, q))
        detail.append(
            {
                "block": str(per), "n": int(len(blk)), "pool": int(len(pool)),
                "pool_by_season": {str(int(s)): int((season[pool] == s).sum()) for s in np.unique(season[pool])},
                "max_pool_date": str(date[pool].max()), "min_block_date": str(date[blk].min()),
            }
        )  # fmt: skip
        assert date[pool].max() < date[blk].min(), "a pool row is not strictly before the block"
        print(
            f"  [{name}] block {bi + 1}/{len(blocks)} {per}: n={len(blk)} pool={len(pool)} "
            f"through {pd.Timestamp(start) - pd.Timedelta(days=1):%Y-%m-%d} "
            f"elapsed {time.monotonic() - t0:.0f}s",
            flush=True,
        )
    # ---- AVG_SHADOW_LIVE install fit (the ONLY addition to T478's walk): the same warm refit (seed 1000 + k, warm config) on EVERY row dated
    # before INSTALL_BEFORE (the pool of month block INSTALL_MONTH), then SAVE the weights. The walk above is unchanged and its OOF rows are
    # consumed only by the gate fit (fit_gate.py); nothing here scores or reads a forecast.
    install_pool = np.flatnonzero((date < np.datetime64(INSTALL_BEFORE)) & (nprior >= 3))
    assert date[install_pool].max() < np.datetime64(INSTALL_BEFORE), (
        "an install-pool row is not before the block"
    )
    install_rows = to_dev(install_pool, dev)
    for k_, net in enumerate(nets):
        seed_everything(1000 + k_)
        fit(net, ts, install_rows, None, warm, dev, warm["lr"], warm["epochs"], 99)
        torch.save(
            {n: p.detach().cpu() for n, p in net.state_dict().items()},
            out_dir / f"seed{seeds[k_]}.pt",
        )
    (out_dir / "vocab.json").write_text(json.dumps({str(k): int(v) for k, v in vocab.items()}))
    install_fit = {
        "pool_rows": int(len(install_pool)),
        "fit_through": str(date[install_pool].max()),
        "n_vocab": int(len(vocab)),
    }
    print("  [install fit]", install_fit, f"elapsed {time.monotonic() - t0:.0f}s", flush=True)
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    return {
        "program": name,
        "n_fit": int(fit_m.sum()),
        "n_val": int(val_m.sum()),
        "n_oof": int(len(oof["game_id"])),
        "blocks": len(blocks),
        "seeds": list(seeds),
        "val_pinball_cold": vals,
        "runtime_s": round(time.monotonic() - t0, 1),
        "oof_by_season": {
            str(int(s)): int((oof["season"] == s).sum()) for s in np.unique(oof["season"])
        },
        "placebo_self_share": float(d["_placebo_self_share"])
        if "_placebo_self_share" in d
        else None,
        "blocks_detail": detail,
        "window": window,
        "install_fit": install_fit,
    }


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir):
    """Refuse to fit unless (1) the staged rule is the frozen one this notebook was built for and (2) the staged holdout log carries the rebuild row."""
    p = Path(run_dir) / RULE_DOC
    if not p.exists():
        raise FileNotFoundError(f"{p} not staged: the rule document must travel with the inputs")
    text = p.read_text()
    head = text.split(RESULTS_MARKER)[0]
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise RuntimeError("the rule document is not FROZEN; refusing to fit")
    got = rule_text_sha(text)
    if not (got.startswith(RULE_SHA_PIN) and RULE_SHA.startswith(RULE_SHA_PIN)):
        raise RuntimeError(
            f"rule sha {got[:8]} (notebook {RULE_SHA[:8]}) is not {RULE_ID} {RULE_SHA_PIN}; refusing"
        )
    if got != RULE_SHA:
        raise RuntimeError(f"rule sha mismatch: document {got} vs notebook {RULE_SHA}; refusing")
    lg = Path(run_dir) / LOG_DOC
    if not lg.exists():
        raise FileNotFoundError(f"{lg} not staged: the holdout log must travel with the inputs")
    if not [ln for ln in lg.read_text().splitlines() if LOG_ROW_KEY in ln and "2026-10-13" in ln]:
        raise RuntimeError(
            f"no holdout-log row '{LOG_ROW_KEY}' (2026-10-13) in the staged log; refusing"
        )
    return got


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_id_of(files):
    """Same function as nba.daily.avg_shadow.bundle.model_id_of (the Mac verifies it again at install)."""
    return hashlib.sha256("\n".join(f"{k}:{files[k]}" for k in sorted(files)).encode()).hexdigest()


def prepare(cur, hist_d):
    base0 = concat_rows(hist_d, cur)
    return {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v)
        for k, v in base0.items()
    }


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("rule", RULE_ID, RULE_SHA, "expected runtime:", EST, flush=True)
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_rt_cur25.npz"))
    run_dir = cur_path.parent
    sha = check_rule(run_dir)
    hist_path = run_dir / "seq_ensemble_rt_hist.npz"
    export_sha, hist_sha = file_sha256(cur_path), file_sha256(hist_path)
    print("rule sha256", sha, "export npz", export_sha, "hist npz", hist_sha, flush=True)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur, hist_d = load_npz_25(cur_path), load_npz_25(hist_path)
    if smoke:
        cur, hist_d = subsample(cur, int(smoke)), subsample(hist_d, int(smoke))
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024, 2025}, (
        "the export holds a season outside the rule"
    )
    assert set(np.unique(hist_d["season"]).tolist()) <= set(HIST_SEASONS), (
        "the history export holds a season outside 2019-2021"
    )
    assert hist_d["ctx"].shape[1] == cur["ctx"].shape[1], "history and current ctx widths differ"
    t0 = time.monotonic()
    d = prepare(cur, hist_d)
    info = run_walk_forward("E2", d, dev, hidden, out_dir, bool(smoke), WINDOW)
    info.update(
        hidden=hidden,
        smoke=bool(smoke),
        cold=COLD,
        warm=WARM,
        rule_sha256=sha,
        export_npz_sha256=export_sha,
        hist_npz_sha256=hist_sha,
        mode=MODE,
    )
    (out_dir / "info_E2.json").write_text(json.dumps(info, indent=2, default=str))
    seeds_run = (0,) if smoke else SEEDS
    files = {
        n: file_sha256(out_dir / n) for n in (*(f"seed{s}.pt" for s in seeds_run), "vocab.json")
    }
    parts = {
        "rule_sha256": sha, "weights_sha256": files, "seeds": list(seeds_run), "hidden": hidden,
        "n_step": int(d["seq"].shape[2]), "n_ctx": int(d["ctx"].shape[1]), "month_block": INSTALL_MONTH,
        "fit_through": info["install_fit"]["fit_through"], "install_pool_rows": info["install_fit"]["pool_rows"],
        "export_npz_sha256": export_sha, "hist_npz_sha256": hist_sha, "w_all_seasons": "2019-20 on",
        "oof_sha256": file_sha256(out_dir / "oof_E2.npz"), "smoke": bool(smoke),
    }  # fmt: skip
    (out_dir / "bundle_parts.json").write_text(json.dumps(parts, indent=2))
    cfg = {"rule": RULE_ID, "rule_sha256": sha, "seeds": parts["seeds"], "cold": COLD, "warm": WARM, "cold_end": COLD_END, "hidden": hidden, "smoke": bool(smoke), "test_seasons": list(TEST_SEASONS), "install_before": INSTALL_BEFORE}  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2))
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "programs": {"E2": info},
                "rule_sha256": sha,
                "runtime_s": round(time.monotonic() - t0, 1),
            },
            indent=2,
            default=str,
        )
    )
    print("ARTIFACTS WRITTEN TO", out_dir)
    return info


if __name__ == "__main__":
    main()
