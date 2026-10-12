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
# SEQ_FULL_MIX training programs (docs/prereg/SEQ_FULL_MIX.md). This file is the TAIL appended by
# build_notebook.py to the model code of ../seq_props/seq_props_train.py (everything before its
# own `main`), so the transformer, loss, fit and predict are exactly the specified ones. The walk-forward
# (`run_walk_forward`) is ENSEMBLE's E2 program line for line with ONE change: the test seasons are
# 2023, 2024 AND 2025. Programs:
#   E2   the real sequence transformer (cold fit on 2022 before April, warm refit before every month 2023-10 .. 2025 season)
#   E2P  past-only placebo: every row's model inputs (window, context, opponent, team) are those of ONE uniformly drawn
#        STRICTLY EARLIER row of the same player-season (the row itself when it has none); labels and `base` stay put
#   E2S  ENSEMBLE's shuffled-input guard: inputs permuted among the players of one date
# 3 seeds each, full budgets, no arm cut. Season 2025 INPUTS include box scores of earlier games only; the warm refit
# before each 2025 month trains on every row dated strictly before that month (earlier 2025 months' outcomes as labels).
# ----------------------------------------------------------------------------------------
import hashlib

RULE_SHA = "2fed975a45cedd4f68dc91e5146f0f2ff5610cf09968decacd2700b9e6e49cb9"  # replaced by build_notebook.py from the frozen document
RULE_ID = "SEQ_FULL_MIX"
RULE_DOC = "SEQ_FULL_MIX.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"

FROZEN_SEASON = 2026  # this rule's input legitimately holds season 2025 (committed, Devin-confirmed holdout row); nothing later
SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice
TEST_SEASONS = (2023, 2024, 2025)
PROGRAMS = ("E2", "E2P", "E2S")
PLACEBO_SEED = 0
REPORT_CTX = (
    "has_report", "st_available", "st_probable", "st_questionable", "st_doubtful", "st_out",
    "n_rep_out", "vac_rep", "n_return", "vac_return",
)  # fmt: skip
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip
INPUT_KEYS = (
    "seq",
    "mask",
    "opp_step",
    "ctx",
    "team_idx",
    "opp_idx",
)  # what the placebo replaces (player_id stays: same player)
EST = "about 15 minutes on a T4 (range 10-30): 3 programs, 3 seeds, 27 monthly warm refits each"


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir):
    """Refuse to run unless (1) the staged rule document is the frozen one this notebook was built for and (2) the staged holdout log carries
    the PRE-REGISTERED, DEVIN CONFIRMED row for this rule (the Mac-side export already verified both against git before producing the input)."""
    p = Path(run_dir) / RULE_DOC
    if not p.exists():
        raise FileNotFoundError(f"{p} not staged: the rule document must travel with the inputs")
    text = p.read_text()
    head = text.split(RESULTS_MARKER)[0]
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise RuntimeError("the rule document is not FROZEN; refusing to fit")
    got = rule_text_sha(text)
    if got != RULE_SHA:
        raise RuntimeError(f"rule sha mismatch: document {got} vs notebook {RULE_SHA}; refusing")
    lg = Path(run_dir) / LOG_DOC
    if not lg.exists():
        raise FileNotFoundError(f"{lg} not staged: the holdout log must travel with the inputs")
    rows = [
        ln for ln in lg.read_text().splitlines()
        if RULE_ID in ln and got[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]  # fmt: skip
    if not rows:
        raise RuntimeError(
            f"no holdout row for {RULE_ID} {got[:8]} (PRE-REGISTERED, DEVIN CONFIRMED) in the staged log; refusing"
        )
    return got


def load_npz_25(path):
    """Like `load_npz` but admits season 2025 (and nothing later)."""
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if int(out["season"].max()) >= FROZEN_SEASON:
        raise ValueError("a season after 2025 is in the input")
    return out


def shuffle_within_date(d, seed=0):
    """Permute the model inputs among the players of one date; labels and base stay put (ENSEMBLE's S arm, unchanged)."""
    rng = np.random.default_rng(seed)
    n = len(d["y"])
    date = d["date_days"]
    d["pid_true"] = d[
        "player_id"
    ].copy()  # bookkeeping only: the OOF rows are keyed by the TARGET's player, not the shuffled input's
    idx = np.arange(n)
    for dd in np.unique(date):
        w = np.flatnonzero(date == dd)
        idx[w] = rng.permutation(w)
    for k in ("seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx"):
        d[k] = d[k][idx]
    return d


def placebo_index(player, season, date, seed=PLACEBO_SEED):
    """Per row, the index of ONE uniformly drawn row of the same player-season dated STRICTLY EARLIER (the row itself when there is none).
    Strictly earlier by date, so a same-day row is never drawn and the draw is as-of."""
    n = len(player)
    order = np.lexsort((date, season, player))
    p, s, dt = player[order], season[order], date[order]
    cut = np.flatnonzero((p[1:] != p[:-1]) | (s[1:] != s[:-1])) + 1
    bounds = np.concatenate([[0], cut, [n]])
    rng = np.random.default_rng(seed)
    out = np.arange(n)
    for a, b in zip(bounds[:-1], bounds[1:]):
        dd = dt[a:b]
        earlier = np.searchsorted(
            dd, dd, side="left"
        )  # rows of the group with a strictly earlier date
        pick = np.floor(rng.random(b - a) * np.maximum(earlier, 1)).astype(np.int64)
        src = np.where(earlier > 0, a + pick, np.arange(a, b))
        out[order[a:b]] = order[src]
    return out


def placebo_inputs(d, seed=PLACEBO_SEED):
    idx = placebo_index(d["player_id"], d["season"], d["date_days"], seed)
    for k in INPUT_KEYS:
        d[k] = d[k][idx]
    d["_placebo_self_share"] = np.array(float((idx == np.arange(len(idx))).mean()))
    return d


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


def run_walk_forward(name, d, dev, hidden, out_dir, smoke):
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
    fit_m = (nprior >= 3) & (season == 2022) & (date < cold_end)
    val_m = (nprior >= 3) & (season == 2022) & (date >= cold_end)
    test_m = np.isin(season, TEST_SEASONS)
    assert not (season >= FROZEN_SEASON).any()
    assert not (season == 2021).any(), "2021-22 must never be present"
    assert set(np.unique(season).tolist()) <= {2022, *TEST_SEASONS}
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
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    return {
        "program": name, "n_fit": int(fit_m.sum()), "n_val": int(val_m.sum()),
        "n_oof": int(len(oof["game_id"])), "blocks": len(blocks), "seeds": list(seeds),
        "val_pinball_cold": vals, "runtime_s": round(time.monotonic() - t0, 1),
        "oof_by_season": {str(int(s)): int((oof["season"] == s).sum()) for s in np.unique(oof["season"])},
        "placebo_self_share": float(d["_placebo_self_share"]) if "_placebo_self_share" in d else None,
        "blocks_detail": detail,
    }  # fmt: skip


def prepare(program, cur):
    base = {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v) for k, v in cur.items()
    }
    if program == "E2P":
        base = placebo_inputs(base)
    if program == "E2S":
        base = shuffle_within_date(base)
    return base


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    programs = tuple(p for p in os.environ.get("NBA_PROGRAMS", ",".join(PROGRAMS)).split(",") if p)
    assert set(programs) <= set(PROGRAMS), programs
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("expected runtime:", EST, flush=True)
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_cur25.npz"))
    run_dir = cur_path.parent
    sha = check_rule(run_dir)
    export_sha = file_sha256(cur_path)
    print("export npz sha256", export_sha, flush=True)
    print("rule sha256", sha, flush=True)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = load_npz_25(cur_path)
    if smoke:
        cur = subsample(cur, int(smoke))
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024, 2025}
    infos = {}
    t0 = time.monotonic()
    for prog in programs:
        if (out_dir / f"oof_{prog}.npz").exists():
            print(f"{prog}: done, skipping")
            continue
        print(f"=== program {prog} ===", flush=True)
        d = prepare(prog, cur)
        infos[prog] = run_walk_forward(prog, d, dev, hidden, out_dir, bool(smoke))
        infos[prog].update(
            hidden=hidden,
            smoke=bool(smoke),
            cold=COLD,
            warm=WARM,
            rule_sha256=sha,
            export_npz_sha256=export_sha,
        )
        (out_dir / f"info_{prog}.json").write_text(json.dumps(infos[prog], indent=2, default=str))
        print(
            prog,
            json.dumps({k: v for k, v in infos[prog].items() if k != "blocks_detail"}),
            flush=True,
        )
        write_metrics(out_dir, sha, t0, hidden, smoke, export_sha)
    write_metrics(out_dir, sha, t0, hidden, smoke, export_sha)
    print("ARTIFACTS WRITTEN TO", out_dir)
    return infos


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_metrics(out_dir, sha, t0, hidden, smoke, export_sha):
    """config.json / metrics.json describe EVERY program in the directory (merged from the per-program info files), never only this session's."""
    infos = {
        p.stem[5:]: json.loads(p.read_text()) for p in sorted(Path(out_dir).glob("info_*.json"))
    }
    cfg = {
        "rule": RULE_ID, "rule_sha256": sha, "seeds": [0] if smoke else list(SEEDS), "cold": COLD, "warm": WARM,
        "cold_end": COLD_END, "hidden": hidden, "smoke": smoke, "test_seasons": list(TEST_SEASONS),
        "season_2025_used": True, "placebo_seed": PLACEBO_SEED, "programs": sorted(infos), "export_npz_sha256": export_sha,
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    metrics = {"programs": infos, "rule_sha256": sha, "runtime_s": round(time.monotonic() - t0, 1)}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
