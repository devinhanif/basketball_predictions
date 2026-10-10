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


# --------------------------------------------------------------------------- main


def oof_frame(d, rows, mean, q, thru, arm) -> pd.DataFrame:  # noqa: ANN001
    days = d["date_days"][rows].astype("datetime64[D]")
    q = np.maximum.accumulate(np.clip(q, 0.0, None), axis=2)
    frames = []
    for si, s in enumerate(STATS):
        frames.append(
            pd.DataFrame(
                {
                    "model": f"seq_props_{arm}",
                    "fold_id": d["season"][rows].astype(np.int32),
                    "game_id": d["game_id"][rows].astype(str),
                    "player_id": d["player_id"][rows].astype(np.int32),
                    "target": s,
                    "game_date": pd.to_datetime(days),
                    "season": d["season"][rows].astype(np.int32),
                    "mean": np.clip(mean[:, si], 0.0, None).astype(float),
                    "q_grid": list(q[:, si, :].astype(float)),
                    "made_with_data_through": pd.to_datetime(thru),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def main() -> dict[str, Any]:
    budget = os.environ.get("NBA_BUDGET", "fast")
    cfg = dict(PRESETS[budget])
    smoke = os.environ.get("NBA_SAMPLE_ROWS")
    if os.environ.get("NBA_EPOCHS"):
        cfg["epochs"] = int(os.environ["NBA_EPOCHS"])
    if smoke:
        cfg.update(seeds=(0,), arms=cfg["arms"][:2], epochs=min(cfg["epochs"], 3), batch=256)
    hidden = int(os.environ.get("NBA_HIDDEN", 32 if smoke else 64))
    print(f"BUDGET={budget} arms={cfg['arms']} seeds={cfg['seeds']} refit={cfg['refit']}")
    print(f"expected runtime: {cfg['est']}")
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    path = Path(os.environ.get("NBA_PARQUET", "data/colab/seq_props/seq_props.npz"))
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    out_dir = root / datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    d = load_npz(path)
    if smoke:
        d = subsample(d, int(smoke))
    n = len(d["y"])
    date = d["date_days"].astype("datetime64[D]")
    season = d["season"]
    val_start = np.datetime64(VAL_START)
    fit_m = (season <= 2023) & (date < val_start) & (d["n_prior"] >= 3)
    val_m = (season == 2023) & (date >= val_start) & (d["n_prior"] >= 3)
    test_m = season == TEST_SEASON
    assert not (season >= FROZEN_SEASON).any()
    print(f"rows {n} | fit {fit_m.sum()} val {val_m.sum()} test(2024) {test_m.sum()}")
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr_rows = torch.as_tensor(np.flatnonzero(fit_m), device=dev)
    va_rows = torch.as_tensor(np.flatnonzero(val_m), device=dev)
    test_idx = np.flatnonzero(test_m)
    scale = d["stat_scale"]
    n_step, n_ctx = d["seq"].shape[2], d["ctx"].shape[1]
    base_val = baseline_grid(d, np.flatnonzero(fit_m), np.flatnonzero(val_m))
    base_pin = pinball(base_val, d["y"][val_m]).mean(0)
    thru_once = pd.Timestamp(date[fit_m | val_m].max())
    months = pd.Series(date[test_idx]).dt.to_period("M")
    arms_out: dict[str, Any] = {}
    states: dict[str, Any] = {}
    for arm in cfg["arms"]:
        nets, vals = [], []
        for sd in cfg["seeds"]:
            seed_everything(sd)
            net = SeqNet(n_step, n_ctx, len(vocab), scale, arm, d=hidden).to(dev)
            print(f"[{arm} seed {sd}] fit ({sum(p.numel() for p in net.parameters())} params)")
            best, _ = fit(net, ts, tr_rows, va_rows, cfg, dev, 1e-3, cfg["epochs"], cfg["patience"])
            nets.append(net)
            vals.append(best)
        vp = ensemble([predict(m, ts, va_rows, cfg["batch"], dev) for m in nets])
        net_pin = pinball(vp[1], d["y"][val_m]).mean(0)
        val_heads = {
            s: {"net": float(net_pin[i]), "baseline": float(base_pin[i]),
                "skill": float(1 - net_pin[i] / base_pin[i])}
            for i, s in enumerate(STATS)
        }  # fmt: skip
        frames = []
        if cfg["refit"] == "once":
            m, q = ensemble(
                [
                    predict(x, ts, torch.as_tensor(test_idx, device=dev), cfg["batch"], dev)
                    for x in nets
                ]
            )
            frames.append(oof_frame(d, test_idx, m, q, thru_once, arm))
        else:  # warm-start walk-forward: refit through the day before each month block
            for per in sorted(months.unique()):
                blk = test_idx[(months == per).to_numpy()]
                start = np.datetime64(per.start_time.date())
                pool = np.flatnonzero((date < start) & (d["n_prior"] >= 3))
                prow = torch.as_tensor(pool, device=dev)
                for k_, net in enumerate(nets):
                    seed_everything(1000 + k_)
                    fit(net, ts, prow, None, cfg, dev, 3e-4, 3, 99)
                m, q = ensemble(
                    [
                        predict(x, ts, torch.as_tensor(blk, device=dev), cfg["batch"], dev)
                        for x in nets
                    ]
                )
                thru = pd.Timestamp(start) - pd.Timedelta(days=1)
                frames.append(oof_frame(d, blk, m, q, thru, arm))
                print(f"  walk-forward block {per}: n={len(blk)} through {thru.date()}")
        oof = pd.concat(frames, ignore_index=True)
        oof.to_parquet(out_dir / f"oof_2024_{arm}.parquet", index=False)
        arms_out[arm] = {"val_pinball_mean": float(np.mean(vals)), "val_heads": val_heads,
                         "oof_rows": int(len(oof))}  # fmt: skip
        states[arm] = [{k: v.cpu() for k, v in m.state_dict().items()} for m in nets]
        print(
            f"[{arm}] val skill vs recency+const-spread:",
            {s: f"{v['skill']:+.3%}" for s, v in val_heads.items()},
        )
    best_arm = min(arms_out, key=lambda a: arms_out[a]["val_pinball_mean"])
    pd.read_parquet(out_dir / f"oof_2024_{best_arm}.parquet").to_parquet(
        out_dir / "oof_2024.parquet", index=False
    )
    torch.save({"states": states, "vocab": vocab, "arm_selected": best_arm}, out_dir / "weights.pt")
    config = {
        "budget": budget, **{k: v for k, v in cfg.items() if k != "est"},
        "hidden": hidden, "val_start": VAL_START, "train_through": str(thru_once.date()),
        "refit_mode": cfg["refit"], "seeds_logged": list(cfg["seeds"]), "k": int(d["seq"].shape[1]),
        "step_features": [str(x) for x in d["step_features"]],
        "ctx_features": [str(x) for x in d["ctx_features"]],
        "residual_over_recency": True, "season_2025_used": False, "sample_rows": smoke,
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(config, indent=2, default=str))
    metrics = {
        "arm_selected": best_arm, "arms": arms_out,
        "val_heads": arms_out[best_arm]["val_heads"], "n_fit": int(fit_m.sum()),
        "n_val": int(val_m.sum()), "n_test_2024": int(test_m.sum()),
        "runtime_s": round(time.monotonic() - t0, 1),
    }  # fmt: skip
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print("artifacts ->", out_dir, "| runtime", metrics["runtime_s"], "s")
    return metrics


if __name__ == "__main__":
    main()
