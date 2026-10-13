# ruff: noqa: E501
"""T478 era-embedding probe (descriptive; reports/t478_era_probe/PLAN.md).

Reads ``states_WALL_E2.npz`` written by the Colab job ``t478_era_probe`` (head-input states of the final
W_ALL sequence model, up to 6000 rows per season with n_prior >= 5, seasons 2019-2024 only) and measures how well
a logistic regression separates one season's states from another's, with ENSEMBLE's P-emb protocol: game-level
hold-out (sha256(game_id) % 3 == 0), AUC, game-clustered 95% bootstrap interval (B = 200). Nothing here is a
verdict; the numbers have no bearing on T478.

    uv run python -m research.eval.t478_era_probe --states <states_WALL_E2.npz> --out reports/t478_era_probe
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

B = 200
MARGIN = 0.02  # P-emb limit = AUC(2022 v 2024) + MARGIN
MAX_SEASON = 2024
# (name, kind, season_a, season_b, restrict_a_to_era_zero)
PAIRS: tuple[tuple[str, str, int, int, bool], ...] = (
    ("2019v2022", "history", 2019, 2022, False),
    ("2020v2022", "history", 2020, 2022, False),
    ("2021v2022", "history", 2021, 2022, False),
    ("2019z_v2022", "history", 2019, 2022, True),
    ("2022v2023", "placebo", 2022, 2023, False),
    ("2022v2024", "placebo", 2022, 2024, False),
    ("2023v2024", "placebo", 2023, 2024, False),
)


def game_fold(game_ids: np.ndarray, mod: int = 3) -> np.ndarray:
    """sha256(game_id) % mod per row (cached per game)."""
    cache: dict[str, int] = {}
    out = np.empty(len(game_ids), dtype=np.int64)
    for i, g in enumerate(game_ids.astype(str)):
        if g not in cache:
            cache[g] = int(hashlib.sha256(g.encode()).hexdigest(), 16) % mod
        out[i] = cache[g]
    return out


def pair_auc(
    za: np.ndarray, ga: np.ndarray, zb: np.ndarray, gb: np.ndarray, boot: int = B, seed: int = 0
) -> dict[str, Any]:
    """AUC of a logistic regression separating group a (label 0) from group b (label 1) on held-out games."""
    z = np.concatenate([za, zb]).astype(np.float64)
    y = np.concatenate([np.zeros(len(za)), np.ones(len(zb))])
    g = np.concatenate([ga, gb]).astype(str)
    test = game_fold(g) == 0
    tr = ~test
    if min(y[tr].sum(), (1 - y[tr]).sum(), y[test].sum(), (1 - y[test]).sum()) < 20:
        return {
            "auc": None,
            "lo": None,
            "hi": None,
            "n_a": len(za),
            "n_b": len(zb),
            "n_test": int(test.sum()),
        }
    mu, sd = z[tr].mean(0), z[tr].std(0) + 1e-6
    clf = LogisticRegression(C=1.0, max_iter=2000).fit((z[tr] - mu) / sd, y[tr])
    s = clf.decision_function((z[test] - mu) / sd)
    yt, gt = y[test], g[test]
    auc = float(roc_auc_score(yt, s))
    games, inv = np.unique(gt, return_inverse=True)
    rows_of = [np.flatnonzero(inv == k) for k in range(len(games))]
    rng = np.random.default_rng(seed)
    aucs = []
    for _ in range(boot):
        pick = rng.integers(0, len(games), len(games))
        idx = np.concatenate([rows_of[k] for k in pick])
        if 0 < yt[idx].sum() < len(idx):
            aucs.append(float(roc_auc_score(yt[idx], s[idx])))
    lo, hi = (
        (float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5)))
        if aucs
        else (None, None)
    )
    return {
        "auc": auc, "lo": lo, "hi": hi, "n_a": len(za), "n_b": len(zb),
        "n_test": int(test.sum()), "n_test_games": int(len(games)),
    }  # fmt: skip


def run_probe(
    states: dict[str, np.ndarray], boot: int = B, cols: slice | None = None
) -> dict[str, Any]:
    season = states["season"].astype(int)
    if season.max() > MAX_SEASON:
        raise ValueError(
            f"states hold season {season.max()} > {MAX_SEASON}; the probe never reads 2025"
        )
    z, gid = states["z"], states["game_id"]
    if cols is not None:
        z = z[:, cols]
    era_zero = states["era_zero"].astype(bool)
    res: dict[str, Any] = {
        "pairs": {},
        "n_by_season": {str(s): int((season == s).sum()) for s in np.unique(season)},
    }
    for name, kind, sa, sb, zero_only in PAIRS:
        ma = (season == sa) & (era_zero if zero_only else True)
        mb = season == sb
        r = pair_auc(z[ma], gid[ma], z[mb], gid[mb], boot)
        r["kind"] = kind
        res["pairs"][name] = r
    m22 = season == 2022  # null pair: random half of 2022 games against the other half
    half = game_fold(gid[m22], 2)
    res["pairs"]["2022a_v_2022b"] = {
        **pair_auc(
            z[m22][half == 0], gid[m22][half == 0], z[m22][half == 1], gid[m22][half == 1], boot
        ),
        "kind": "null",
    }
    src = "2022v2024" if res["pairs"]["2022v2024"]["auc"] is not None else "2022v2023"
    base = res["pairs"][src]["auc"]
    limit = None if base is None else base + MARGIN
    res["limit"] = limit
    res["limit_from"] = src
    res["rule"] = (
        f"limit = AUC({src}) + 0.02; flagged when the interval LOWER end exceeds it (ENSEMBLE P-emb, descriptive here)"
    )
    for r in res["pairs"].values():
        r["flagged"] = bool(
            limit is not None and r["kind"] == "history" and r["lo"] is not None and r["lo"] > limit
        )
    return res


def to_markdown(res: dict[str, Any]) -> str:
    lines = [
        "| pair | kind | AUC | 95% CI | n_a / n_b | test games | flagged |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, r in res["pairs"].items():
        ci = "n/a" if r["lo"] is None else f"[{r['lo']:.3f}, {r['hi']:.3f}]"
        auc = "n/a" if r["auc"] is None else f"{r['auc']:.3f}"
        lines.append(
            f"| {k} | {r['kind']} | {auc} | {ci} | {r['n_a']} / {r['n_b']} | {r.get('n_test_games', 'n/a')} | {r['flagged']} |"
        )
    lim = "n/a" if res["limit"] is None else f"{res['limit']:.3f}"
    return (
        "\n".join(lines) + f"\n\nLimit (AUC {res['limit_from']} + 0.02): {lim}. Descriptive only.\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", required=True)
    ap.add_argument("--out", default="reports/t478_era_probe")
    a = ap.parse_args()
    d = np.load(a.states, allow_pickle=False)
    st = {k: d[k] for k in d.files}
    half = st["z"].shape[1] // 2  # head input = [target token (d), mean-pooled history (d)]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    allres = {
        "full_head_input": run_probe(st),
        "target_token_only": run_probe(st, cols=slice(0, half)),
    }
    (out / "probe.json").write_text(json.dumps(allres, indent=2))
    md = "\n\n".join(f"## {k}\n\n{to_markdown(v)}" for k, v in allres.items())
    (out / "probe.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
