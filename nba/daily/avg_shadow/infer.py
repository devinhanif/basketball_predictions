# mypy: ignore-errors
# ruff: noqa: E501
"""CPU inference of the E2 sequence transformer (3 seeds), run as a SUBPROCESS of the daily process.

    python -m nba.daily.avg_shadow.infer --bundle <dir> --in <arrays.npz> --out <q.npz>

Why a subprocess: torch and lightgbm hang when both are loaded in one process on macOS (BEST_PRACTICES, CI runs two pytest
invocations), and the daily process already fits lightgbm. A child process also gives the step a hard timeout and keeps an inference
crash out of the pretip run. The network class below is ``SeqNet`` of ``research/colab/jobs/seq_props/seq_props_train.py`` (the class
T478 trained), parameter names unchanged so the Colab state dicts load; inference only (no loss, no optimiser, no mixed precision).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

STATS = ("pts", "reb", "ast", "fg3m")
N_Q = 19


def build_net(n_step: int, n_ctx: int, n_players: int, stat_scale: np.ndarray, d: int = 64):  # noqa: ANN201
    import torch
    from torch import nn

    class SeqNet(nn.Module):
        def __init__(
            self, n_step: int, n_ctx: int, n_players: int, stat_scale: np.ndarray,
            d: int = 64, layers: int = 2, heads: int = 4, dropout: float = 0.15,
            head_dropout: float = 0.4, k: int = 20,
        ) -> None:  # fmt: skip
            super().__init__()
            self.k = k
            self.register_buffer("scale", torch.as_tensor(stat_scale, dtype=torch.float32))
            self.step_in = nn.Linear(n_step, d)
            self.opp_emb = nn.Embedding(40, 8, padding_idx=0)
            self.opp_proj = nn.Linear(8, d, bias=False)
            self.pos = nn.Parameter(torch.zeros(k + 1, d))
            self.player_emb = nn.Embedding(n_players + 1, 16)
            self.team_emb = nn.Embedding(40, 8, padding_idx=0)
            self.tgt_opp_emb = nn.Embedding(40, 8, padding_idx=0)
            self.tgt_in = nn.Linear(n_ctx + 16 + 8 + 8, d)
            lay = nn.TransformerEncoderLayer(
                d, heads, 2 * d, dropout, batch_first=True, norm_first=True, activation="gelu"
            )
            self.enc = nn.TransformerEncoder(lay, layers, enable_nested_tensor=False)
            self.head = nn.Sequential(
                nn.LayerNorm(2 * d), nn.Linear(2 * d, 128), nn.GELU(), nn.Dropout(head_dropout),
                nn.Linear(128, len(STATS) * (1 + N_Q)),
            )  # fmt: skip

        def forward(self, b):  # noqa: ANN001, ANN202
            tgt = self.tgt_in(
                torch.cat(
                    [b["ctx"], self.player_emb(b["pid"]), self.team_emb(b["team"]), self.tgt_opp_emb(b["opp"])],
                    dim=1,
                )
            )  # fmt: skip
            steps = self.step_in(b["seq"]) + self.opp_proj(self.opp_emb(b["opp_step"]))
            x = torch.cat([steps, tgt[:, None, :]], dim=1) + self.pos[None]
            m = b["mask"]
            pad = torch.cat([~m, torch.zeros_like(m[:, :1])], dim=1)
            h = self.enc(x, src_key_padding_mask=pad)
            hs = h[:, :-1] * m[..., None]
            pooled = hs.sum(1) / m.sum(1, keepdim=True).clamp(min=1)
            z = torch.cat([h[:, -1], pooled], dim=1)
            raw = self.head(z).float().view(-1, len(STATS), 1 + N_Q)
            sc = self.scale[None, :]
            base = b["base"]
            mean = base + sc * raw[:, :, 0]
            q0 = base + sc * raw[:, :, 1]
            inc = nn.functional.softplus(raw[:, :, 2:]) * sc[:, :, None] * 0.3
            q = torch.cat([q0[:, :, None], q0[:, :, None] + torch.cumsum(inc, dim=2)], dim=2)
            return mean, q

    return SeqNet(n_step, n_ctx, n_players, stat_scale, d=d)


def predict_bundle(
    bundle: Path, arrays: dict[str, np.ndarray], batch: int = 2048
) -> dict[str, np.ndarray]:
    """Seed-averaged (mean, 19 quantiles per stat) for the rows of ``arrays``; the same post-processing as the Colab OOF files."""
    import torch

    torch.set_num_threads(4)
    manifest = json.loads((bundle / "manifest.json").read_text())
    vocab = {int(k): int(v) for k, v in json.loads((bundle / "vocab.json").read_text()).items()}
    pid = np.array([vocab.get(int(p), 0) for p in arrays["player_id"]], dtype=np.int64)
    n = len(pid)
    t = lambda a, dt: torch.as_tensor(np.asarray(a).astype(dt))  # noqa: E731
    seq, mask = t(arrays["seq"], np.float32), t(arrays["mask"], np.bool_)
    opp_step, ctx = t(arrays["opp_step"], np.int64), t(arrays["ctx"], np.float32)
    team, opp, base = (
        t(arrays["team_idx"], np.int64),
        t(arrays["opp_idx"], np.int64),
        t(arrays["base"], np.float32),
    )
    pid_t = torch.as_tensor(pid)
    means, qs = [], []
    for seed in manifest["seeds"]:
        state = torch.load(bundle / f"seed{seed}.pt", map_location="cpu", weights_only=True)
        net = build_net(
            int(state["step_in.weight"].shape[1]),
            int(state["tgt_in.weight"].shape[1]) - 32,
            int(state["player_emb.weight"].shape[0]) - 1,
            np.zeros(len(STATS), dtype=np.float32),
            d=int(state["step_in.weight"].shape[0]),
        )
        net.load_state_dict(state)
        net.eval()
        m_out, q_out = [], []
        with torch.no_grad():
            for i in range(0, n, batch):
                sl = slice(i, i + batch)
                m, q = net(
                    {
                        "seq": seq[sl], "mask": mask[sl], "opp_step": opp_step[sl], "ctx": ctx[sl],
                        "pid": pid_t[sl], "team": team[sl], "opp": opp[sl], "base": base[sl],
                    }
                )  # fmt: skip
                m_out.append(m.float().numpy())
                q_out.append(q.float().numpy())
        means.append(np.concatenate(m_out) if m_out else np.zeros((0, len(STATS)), np.float32))
        qs.append(np.concatenate(q_out) if q_out else np.zeros((0, len(STATS), N_Q), np.float32))
    mean, q = np.mean(means, axis=0), np.mean(qs, axis=0)
    return {
        "mean": np.clip(mean, 0.0, None).astype(np.float32),
        "q": np.maximum.accumulate(np.clip(q, 0.0, None), axis=2).astype(np.float32),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    z = np.load(a.inp, allow_pickle=False)
    out = predict_bundle(a.bundle, {k: z[k] for k in z.files})
    np.savez(a.out, seconds=np.array(time.monotonic() - t0), **out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
