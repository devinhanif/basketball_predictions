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
# SEQ_FULL_MIX_RT training programs (docs/prereg/SEQ_FULL_MIX_RT.md). TAIL appended by build_notebook.py to the model code of
# ../seq_props/seq_props_train.py (everything before its own `main`), so transformer, loss, fit and predict are exactly the specified ones.
# One tail, two modes (MODE is replaced at build time):
#   sel   run #1 (NOT a holdout touch): windows W22 and WALL on the REAL-TIP export, scored walk-forward on 2023 and 2024 ONLY; programs E2 and E2S per window;
#         the input may hold nothing after 2024 (the base loader refuses season 2025 and this file asserts it); no frozen document is needed.
#   held  run #2 (the one 2025 touch): the W_ALL construction (all the data), programs E2, E2P, E2S over 2023, 2024 AND 2025; refuses unless the staged rule
#         document is the frozen one this notebook was built for and the staged holdout log carries the PRE-REGISTERED, DEVIN CONFIRMED row.
# Windows: WALL (= W_ALL, the construction) = cold fit and every warm pool include the 2019-20, 2020-21 and 2021-22 rows (real-tip report tokens for the first two,
# no report token for 2021-22, era channels for all) as well as 2022 onward; W22 = cold fit on 2022 before April, warm pool from 2022 (ENSEMBLE E2), a DIAGNOSTIC only.
# ----------------------------------------------------------------------------------------
import hashlib

MODE = "sel"  # sel | held
WINDOW = ""  # held: always WALL (all the data); sel: ignored
RULE_SHA = "none"  # held: sha256 of the frozen document; sel: sha256 of the staged draft's text above the marker (provenance only)
RULE_ID = "SEQ_FULL_MIX_RT"
RULE_DOC = "SEQ_FULL_MIX_RT.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
assert MODE in ("sel", "held"), MODE

FROZEN_SEASON = 2026 if MODE == "held" else 2025  # sel: the base loader keeps refusing season 2025
HIST_SEASONS = (2019, 2020, 2021)
SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice
TEST_SEASONS = (2023, 2024, 2025) if MODE == "held" else (2023, 2024)
PROGRAMS = ("E2", "E2P", "E2S") if MODE == "held" else ("E2", "E2S")
WINDOWS = (WINDOW,) if MODE == "held" else ("WALL", "W22")
PROGRAMS_BY_WINDOW = {
    "WALL": PROGRAMS,
    "W22": ("E2",),
}  # sel: W22 is a one-program DIAGNOSTIC, never selected
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
EST = "sel: about 22 minutes on a T4 (range 16-35): WALL E2 and E2S plus the W22 E2 diagnostic, 3 seeds, 18 monthly warm refits each (WALL pools are about 4x larger); held: about 25 minutes (3 programs on WALL, 27 refits)"


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def provenance(run_dir):
    """sel: the staged draft's text above the marker is recorded (sha256), never required to be frozen."""
    p = Path(run_dir) / RULE_DOC
    return rule_text_sha(p.read_text()) if p.exists() else None


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
        "blocks_detail": detail, "window": window,
    }  # fmt: skip


def prepare(program, cur, hist_d, window):
    base0 = concat_rows(hist_d, cur) if window == "WALL" else dict(cur)
    base = {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v)
        for k, v in base0.items()
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
    windows = tuple(w for w in os.environ.get("NBA_WINDOWS", ",".join(WINDOWS)).split(",") if w)
    assert set(windows) <= set(WINDOWS), windows
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print(
        "mode", MODE, "windows", windows, "programs", programs, "expected runtime:", EST, flush=True
    )
    cur_path = Path(
        os.environ.get(
            "NBA_PARQUET",
            "seq_ensemble_rt_cur25.npz" if MODE == "held" else "seq_ensemble_rt_cur.npz",
        )
    )
    run_dir = cur_path.parent
    sha = check_rule(run_dir) if MODE == "held" else (provenance(run_dir) or "none")
    export_sha = file_sha256(cur_path)
    hist_path = run_dir / "seq_ensemble_rt_hist.npz"
    hist_sha = file_sha256(hist_path) if "WALL" in windows else None
    print(
        "rule/draft sha256",
        sha,
        "export npz sha256",
        export_sha,
        "hist sha256",
        hist_sha,
        flush=True,
    )
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = load_npz_25(cur_path)
    hist_d = load_npz_25(hist_path) if "WALL" in windows else None
    if smoke:
        cur = subsample(cur, int(smoke))
        hist_d = subsample(hist_d, int(smoke)) if hist_d is not None else None
    ok = {2022, 2023, 2024} | ({2025} if MODE == "held" else set())
    assert set(np.unique(cur["season"]).tolist()) <= ok, (
        "the export holds a season outside the rule"
    )
    assert int(cur["season"].max()) <= max(TEST_SEASONS)
    if hist_d is not None:
        assert set(np.unique(hist_d["season"]).tolist()) <= set(HIST_SEASONS)
        assert hist_d["ctx"].shape[1] == cur["ctx"].shape[1], (
            "history and current ctx widths differ"
        )
    infos = {}
    t0 = time.monotonic()
    for win in windows:
        for prog in [p_ for p_ in programs if p_ in PROGRAMS_BY_WINDOW[win]]:
            name = f"{win}_{prog}" if MODE == "sel" else prog
            if (out_dir / f"oof_{name}.npz").exists():
                print(f"{name}: done, skipping")
                continue
            print(f"=== {name} ===", flush=True)
            d = prepare(prog, cur, hist_d, win)
            infos[name] = run_walk_forward(name, d, dev, hidden, out_dir, bool(smoke), win)
            infos[name].update(hidden=hidden, smoke=bool(smoke), cold=COLD, warm=WARM, rule_sha256=sha, export_npz_sha256=export_sha, hist_npz_sha256=hist_sha if win == "WALL" else None, mode=MODE)  # fmt: skip
            (out_dir / f"info_{name}.json").write_text(
                json.dumps(infos[name], indent=2, default=str)
            )
            print(
                name,
                json.dumps({k: v for k, v in infos[name].items() if k != "blocks_detail"}),
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
        "rule": RULE_ID, "mode": MODE, "window": WINDOW if MODE == "held" else None, "rule_sha256": sha, "seeds": [0] if smoke else list(SEEDS), "cold": COLD, "warm": WARM,
        "cold_end": COLD_END, "hidden": hidden, "smoke": smoke, "test_seasons": list(TEST_SEASONS),
        "season_2025_used": MODE == "held", "placebo_seed": PLACEBO_SEED, "programs": sorted(infos), "export_npz_sha256": export_sha,
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    metrics = {"programs": infos, "rule_sha256": sha, "runtime_s": round(time.monotonic() - t0, 1)}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))


# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# T478 era-embedding probe (reports/t478_era_probe/PLAN.md). Appended by build_notebook.py AFTER the SEQ_FULL_MIX_RT tail (sel mode), which sits on the model code of
# ../seq_props/seq_props_train.py. The rt tail's `run_walk_forward` is called UNCHANGED (program WALL_E2, seed 0 only, test seasons 2023-2024): the only additions are a
# wrapper around `fit` that remembers the network and tensors, and the dump of head-input states of the FINAL model (after the last warm refit), as ENSEMBLE did for E3.
# Season 2025 cannot be read: the base loader refuses it and this file asserts it again. Scores nothing.
# ----------------------------------------------------------------------------------------
STATE_PER_ERA = 6000
PROBE_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
MAX_SEASON = 2024  # as ENSEMBLE's committed P-emb: select 2023-24 and report-season 2024-25; season 2025 (holdout) is refused
TEST_SEASONS = (2023, 2024)
SEEDS = (
    0,
)  # net 0 of the 3-seed run: same cold init and warm seeds as in run #1 (cold fit seed 0, warm seed 1000)
PLAN_SHA = "3d2cd9e45253c3289c11c760cd141999b2c974eb0d1c43717c2673dd7a4fa15a"
ERA_COLS = ("era_bubble", "era_nofans", "era_limited")

_orig_fit = globals().get("_orig_fit", fit)
_CAP = {}


def fit(net, ts, train_rows, val_rows, cfg, dev, lr, epochs, patience):  # noqa: F811
    _CAP["net"], _CAP["ts"] = net, ts
    return _orig_fit(net, ts, train_rows, val_rows, cfg, dev, lr, epochs, patience)


def drop_after(d, last):
    """Remove every row of a season after `last` (the 2024-25 rows of the selection export), from every per-row array."""
    keep = d["season"] <= last
    n = len(keep)
    return {
        k: (
            v[keep] if (k in ROW_KEYS or k == "date_str") and np.asarray(v).shape[:1] == (n,) else v
        )
        for k, v in d.items()
    }


def probe_extract_states(net, ts, rows, batch, dev):
    store = []
    h = net.head[0].register_forward_pre_hook(lambda m, i: store.append(i[0].float().cpu().numpy()))
    try:
        predict(net, ts, rows, batch, dev)
    finally:
        h.remove()
    return np.concatenate(store)


def probe_sample(d, rng):
    keep = []
    for s in PROBE_SEASONS:
        w = np.flatnonzero((d["season"] == s) & (d["n_prior"] >= 5))
        if len(w):
            keep.append(np.sort(rng.choice(w, size=min(len(w), STATE_PER_ERA), replace=False)))
    return np.concatenate(keep) if keep else np.zeros(0, dtype=np.int64)


def main_probe():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print(
        "T478 era probe; plan sha256",
        PLAN_SHA,
        "seed 0 only; expected: about 5 T4 minutes (4-9)",
        flush=True,
    )
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_rt_cur.npz"))
    hist_path = cur_path.parent / "seq_ensemble_rt_hist.npz"
    export_sha, hist_sha = file_sha256(cur_path), file_sha256(hist_path)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur, hist_d = load_npz_25(cur_path), load_npz_25(hist_path)
    if smoke:
        cur, hist_d = subsample(cur, int(smoke)), subsample(hist_d, int(smoke))
    cur = drop_after(cur, MAX_SEASON)
    assert int(cur["season"].max()) <= MAX_SEASON and int(hist_d["season"].max()) <= 2021, (
        "the probe reads seasons <= 2024 only"
    )
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024}
    assert set(np.unique(hist_d["season"]).tolist()) <= set(HIST_SEASONS)
    t0 = time.monotonic()
    d = prepare("E2", cur, hist_d, "WALL")
    assert int(d["season"].max()) <= MAX_SEASON
    info = run_walk_forward("WALL_E2", d, dev, hidden, out_dir, bool(smoke), "WALL")
    net, ts = (
        _CAP["net"],
        _CAP["ts"],
    )  # the final network of the single seed, after its last warm refit
    rng = np.random.default_rng(0)
    sample = probe_sample(d, rng)
    z = probe_extract_states(net, ts, to_dev(sample, dev), 1024, dev)
    names = [str(x) for x in d["ctx_features"]]
    ctx = d["ctx"][sample]
    era = np.stack([ctx[:, names.index(c)] for c in ERA_COLS], axis=1)
    np.savez_compressed(
        out_dir / "states_WALL_E2.npz",
        z=z.astype(np.float16), season=d["season"][sample].astype(np.int16), game_id=d["game_id"][sample].astype("U12"),
        player_id=d["player_id"][sample], era_zero=(era == 0).all(axis=1), has_report=ctx[:, names.index("has_report")] > 0.5,
        n_prior=d["n_prior"][sample].astype(np.int32),
    )  # fmt: skip
    info.update(
        states=int(len(sample)), state_dim=int(z.shape[1]), plan_sha256=PLAN_SHA, export_npz_sha256=export_sha,
        hist_npz_sha256=hist_sha, hidden=hidden, smoke=bool(smoke), cold=COLD, warm=WARM, season_2025_used=False,
        states_by_season={str(int(s)): int((d["season"][sample] == s).sum()) for s in np.unique(d["season"][sample])},
    )  # fmt: skip
    (out_dir / "info_WALL_E2.json").write_text(json.dumps(info, indent=2, default=str))
    (out_dir / "config.json").write_text(
        json.dumps(
            {
                "job": "t478_era_probe",
                "plan_sha256": PLAN_SHA,
                "seeds": [0],
                "season_2025_used": False,
                "max_season": MAX_SEASON,
            },
            indent=2,
        )
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "programs": {"WALL_E2": info},
                "plan_sha256": PLAN_SHA,
                "runtime_s": round(time.monotonic() - t0, 1),
            },
            indent=2,
            default=str,
        )
    )
    print(
        "states", json.dumps(info["states_by_season"]), "ARTIFACTS WRITTEN TO", out_dir, flush=True
    )
    return {"WALL_E2": info}
