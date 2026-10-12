# ruff: noqa: E501
"""Where is the market still learning after T-60? (descriptive; read-only on stored prices)

    .venv/bin/python -m nba.markets.market_slow run --pairs-dir data/ops/model_clv_pairs

Pairs each book's T-60 quote with its ``t5`` capture (stamped about tip - 9 min; the closing proxy
MODEL_CLV uses, so this measures roughly 55 minutes of movement, not the real close). Seasons <= 2024
only. For every slice it reports: the share of lines that moved, the size of the price move on unchanged
lines, whether the market got more accurate (Brier of the T-60 price minus Brier of the t5 price on
unchanged lines; positive = the market learned), how often the move direction agreed with the outcome,
and whether the model's T-60 lean predicted the move direction beyond a shuffled-forecast null.
Every interval is a game-clustered bootstrap percentile interval.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.markets.decision_scoring import MAX_SEASON, MIN_N, N_BOOT, SEASONS, STATS, Boot

BOOKS: tuple[str, ...] = ("pinnacle", "draftkings", "fanduel")
N_NULL = 20
STRONG = 0.05  # |p_model - de-vigged price| for a "strong lean"
K3 = ["game_id", "player_id", "stat"]
K4 = [*K3, "book"]
OUT_DIR = Path("reports/market_slow_2026-10-11")
TZ = "America/New_York"


def load(pairs_dir: Path, seasons: tuple[int, ...] = SEASONS) -> dict[str, Any]:
    frames = []
    for s in seasons:
        if s > MAX_SEASON:
            raise ValueError("season 2025 is the frozen holdout")
        frames.append(pl.read_parquet(pairs_dir / f"pairs_{s}.parquet"))
    d = pl.concat(frames, how="diagonal_relaxed").filter(pl.col("book").is_in(list(BOOKS)))
    assert int(d["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    a = d.filter(pl.col("snapshot_kind") == "t60")
    b = d.filter(pl.col("snapshot_kind") == "t5")
    assert a.group_by(K4).len()["len"].max() == 1 and b.group_by(K4).len()["len"].max() == 1
    t5 = b.select([*K4, pl.col("line").alias("line5"), pl.col("p_over_mult").alias("mult5")])
    q = (
        a.join(t5, on=K4, how="left")
        .filter(
            pl.col("p_over_mult").is_not_null()
            & pl.col("p_model").is_not_null()
            & pl.col("over").is_not_null()
        )
        .with_columns(
            pl.col("tipoff_utc")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone(TZ)
            .dt.hour()
            .alias("tip_hour_et")
        )
        .sort(K4)
    )
    # line-size tercile inside each stat (cut points from all T-60 legs of that stat)
    cuts = q.group_by("stat").agg(
        pl.col("line").quantile(1 / 3).alias("c1"), pl.col("line").quantile(2 / 3).alias("c2")
    )
    q = q.join(cuts, on="stat").with_columns(
        pl.when(pl.col("line") <= pl.col("c1"))
        .then(pl.lit("small"))
        .when(pl.col("line") <= pl.col("c2"))
        .then(pl.lit("mid"))
        .otherwise(pl.lit("large"))
        .alias("line_size"),
        pl.when(pl.col("tip_hour_et") < 17)
        .then(pl.lit("afternoon (<5pm ET)"))
        .when(pl.col("tip_hour_et") < 20)
        .then(pl.lit("early evening (5-8pm ET)"))
        .when(pl.col("tip_hour_et") < 22)
        .then(pl.lit("prime (8-10pm ET)"))
        .otherwise(pl.lit("late (10pm+ ET)"))
        .alias("game_time"),
    )
    games = sorted(q["game_id"].unique().to_list())
    gmap = {g: i for i, g in enumerate(games)}

    def f(c: str) -> np.ndarray:
        return q[c].cast(pl.Float64).to_numpy()

    L: dict[str, Any] = {
        "n": q.height,
        "G": len(games),
        "gi": np.array([gmap[g] for g in q["game_id"].to_list()], dtype=np.int64),
        "line": f("line"),
        "line5": f("line5"),
        "mult": f("p_over_mult"),
        "mult5": f("mult5"),
        "pm": f("p_model"),
        "over": f("over"),
        "season": q["season"].to_numpy(),
        "game_id": q["game_id"].to_numpy(),
        "player_id": q["player_id"].to_numpy(),
        "game_date": q["game_date"].to_list(),
        "col": {
            "book": q["book"].to_numpy(),
            "stat": q["stat"].to_numpy(),
            "line_size": q["line_size"].to_numpy(),
            "minutes_tier": q["sl_tier"].to_numpy(),
            "role": q["sl_role"].to_numpy(),
            "report": q["sl_tm"].to_numpy(),
            "game_time": q["game_time"].to_numpy(),
            "season": q["season"].cast(pl.Utf8).to_numpy(),
        },
    }
    gk = q.select(pl.concat_str(["game_id", "stat", "book"], separator="|")).to_series().to_numpy()
    L["shuf_grp"] = np.unique(gk, return_inverse=True)[1]
    return L


def derive(L: dict[str, Any]) -> dict[str, np.ndarray]:
    """Per-leg movement quantities. Direction is +1 toward the over, -1 toward the under, 0 none."""
    joined = np.isfinite(L["line5"]) & np.isfinite(L["mult5"])
    unch = joined & (L["line5"] == L["line"])
    chg = joined & ~unch
    dm = np.where(unch, L["mult5"] - L["mult"], 0.0)
    mdir = np.where(
        unch, np.sign(dm), np.where(chg, np.sign(np.nan_to_num(L["line5"] - L["line"])), 0.0)
    )
    moved = mdir != 0
    went = np.where(L["over"] > 0.5, 1.0, -1.0)
    bs60 = (L["mult"] - L["over"]) ** 2
    bs5 = np.where(unch, (L["mult5"] - L["over"]) ** 2, 0.0)
    return {
        "joined": joined.astype(float),
        "unch": unch.astype(float),
        "chg": chg.astype(float),
        "abs_dm": np.where(unch, np.abs(dm), 0.0),
        "dm": dm,
        "mdir": mdir,
        "moved": moved.astype(float),
        "follow": (moved & (mdir == went)).astype(float),
        "moved_line": (moved & chg).astype(float),
        "follow_line": (moved & chg & (mdir == went)).astype(float),
        "moved_price": (moved & unch).astype(float),
        "follow_price": (moved & unch & (mdir == went)).astype(float),
        "brier_gain": np.where(unch, bs60 - bs5, 0.0),
    }


def null_arrays(L: dict[str, Any], D: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Mean over N_NULL within-(game, stat, book) shuffles of p_model: agreement numerators/denominators."""
    n = L["n"]
    grp = L["shuf_grp"]  # shuffle forecasts across players inside (game, stat, book)
    base = np.lexsort((np.arange(n), grp))
    out = {
        k: np.zeros(n)
        for k in (
            "agree_all",
            "den_all",
            "agree_strong",
            "den_strong",
            "clv_all",
            "cden_all",
            "clv_strong",
            "cden_strong",
        )
    }
    for k in range(N_NULL):
        rng = np.random.default_rng(1000 + k)
        shuf = np.lexsort((rng.random(n), grp))
        pm = np.empty(n)
        pm[base] = L["pm"][shuf]
        add_arrays(out, L, D, pm, 1.0 / N_NULL)
    return out


def add_arrays(
    out: dict[str, np.ndarray],
    L: dict[str, Any],
    D: dict[str, np.ndarray],
    pm: np.ndarray,
    w: float,
) -> None:
    lean = np.where(pm - L["mult"] >= 0, 1.0, -1.0)
    strong = (np.abs(pm - L["mult"]) >= STRONG).astype(float)
    mv = D["moved"]
    ag = ((lean == D["mdir"]) & (mv > 0)).astype(float)
    for tag, s in (("all", np.ones(L["n"])), ("strong", strong)):
        out[f"agree_{tag}"] += w * s * ag
        out[f"den_{tag}"] += w * s * mv
        out[f"clv_{tag}"] += w * s * D["unch"] * lean * D["dm"]
        out[f"cden_{tag}"] += w * s * D["unch"]


def bh(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        q[i] = prev
    return [float(x) for x in q]


def diff(
    boot: Boot, na: np.ndarray, da: np.ndarray, nb: np.ndarray, db: np.ndarray
) -> dict[str, Any]:
    """Cluster-bootstrap of ratio(na/da) - ratio(nb/db); two-sided p from the resample distribution."""
    ga, gd, gb, ge = (boot.per_game(x) for x in (na, da, nb, db))
    if ga.sum() == 0 and gd.sum() == 0:
        return {"est": None}
    est = float(ga.sum() / max(gd.sum(), 1e-12) - gb.sum() / max(ge.sum(), 1e-12))
    A, B_, C, E = boot.W @ ga, boot.W @ gd, boot.W @ gb, boot.W @ ge
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.where((B_ > 0) & (E > 0), A / B_ - C / E, np.nan)
    lo, hi = np.nanpercentile(b, [2.5, 97.5])
    nb_ = int(np.isfinite(b).sum())
    p = 2 * min((1 + np.sum(b <= 0)) / (nb_ + 1), (1 + np.sum(b >= 0)) / (nb_ + 1))
    return {"est": est, "lo": float(lo), "hi": float(hi), "p": float(min(p, 1.0))}


def slice_masks(L: dict[str, Any]) -> list[tuple[str, str, np.ndarray]]:
    out: list[tuple[str, str, np.ndarray]] = [("overall", "all", np.ones(L["n"], dtype=bool))]
    for dim, col in L["col"].items():
        for v in sorted(set(col.tolist())):
            out.append((dim, str(v), col == v))
    return out


def cell_stats(
    boot: Boot,
    mask: np.ndarray,
    D: dict[str, np.ndarray],
    N: dict[str, np.ndarray],
    L: dict[str, Any],
) -> dict[str, Any]:
    m = mask.astype(float)
    r = boot.ratio
    joined = int((m * D["joined"]).sum())
    out: dict[str, Any] = {"n_legs": int(mask.sum()), "n_joined": joined}
    out["changed_share"] = r(m * D["chg"], m * D["joined"])
    out["mean_abs_move_unchanged"] = r(m * D["abs_dm"], m * D["unch"])
    out["brier_gain_unchanged"] = r(m * D["brier_gain"], m * D["unch"])
    out["follow_move_hit"] = r(m * D["follow"], m * D["moved"])
    out["follow_line_move_hit"] = r(m * D["follow_line"], m * D["moved_line"])
    out["follow_price_move_hit"] = r(m * D["follow_price"], m * D["moved_price"])
    lean = np.where(L["pm"] - L["mult"] >= 0, 1.0, -1.0)
    ag = ((lean == D["mdir"]) & (D["moved"] > 0)).astype(float)
    strong = (np.abs(L["pm"] - L["mult"]) >= STRONG).astype(float)
    for tag, s in (("all", np.ones(L["n"])), ("strong", strong)):
        d = diff(boot, m * s * ag, m * s * D["moved"], m * N[f"agree_{tag}"], m * N[f"den_{tag}"])
        d["n_moved"] = int((m * s * D["moved"]).sum())
        d["model_rate"] = r(m * s * ag, m * s * D["moved"])
        d["null_rate"] = float(
            (m * N[f"agree_{tag}"]).sum() / max((m * N[f"den_{tag}"]).sum(), 1e-12)
        )
        out[f"lean_agree_{tag}"] = d
        c = diff(
            boot, m * s * D["unch"] * lean * D["dm"], m * s * D["unch"],
            m * N[f"clv_{tag}"], m * N[f"cden_{tag}"],
        )  # fmt: skip
        c["n_unchanged"] = int((m * s * D["unch"]).sum())
        out[f"lean_clv_{tag}"] = c
    out["status"] = "ok" if joined >= MIN_N else "insufficient sample"
    return out


CONTRASTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("role", "bench", "role", "starter", "bench minus starter"),
    ("line_size", "small", "line_size", "large", "small line minus large line"),
    ("game_time", "afternoon (<5pm ET)", "game_time", "prime (8-10pm ET)", "afternoon minus prime"),
    ("book", "pinnacle", "book", "draftkings", "pinnacle minus draftkings"),
)  # declared before looking at any slice difference


def contrasts(L: dict[str, Any], D: dict[str, np.ndarray], boot: Boot) -> list[dict[str, Any]]:
    out = []
    for da, va, db, vb, label in CONTRASTS:
        ma = (L["col"][da] == va).astype(float)
        mb = (L["col"][db] == vb).astype(float)
        rec: dict[str, Any] = {"contrast": label}
        for name, num, den in (
            ("brier_gain_unchanged", "brier_gain", "unch"),
            ("changed_share", "chg", "joined"),
            ("mean_abs_move_unchanged", "abs_dm", "unch"),
        ):
            rec[name] = diff(boot, ma * D[num], ma * D[den], mb * D[num], mb * D[den])
        out.append(rec)
    return out


def example(L: dict[str, Any], D: dict[str, np.ndarray]) -> dict[str, Any]:
    """Median-index DraftKings pts leg with a moved line and a strong model lean (deterministic)."""
    lean = np.where(L["pm"] - L["mult"] >= 0, 1.0, -1.0)
    ok = (
        (L["col"]["book"] == "draftkings") & (L["col"]["stat"] == "pts")
        & (D["chg"] > 0) & (np.abs(L["pm"] - L["mult"]) >= STRONG)
    )  # fmt: skip
    idx = np.flatnonzero(ok)
    k = int(idx[len(idx) // 2])
    return {
        "game_id": str(L["game_id"][k]), "player_id": int(L["player_id"][k]),
        "game_date": str(L["game_date"][k]), "leg_index": k, "game_index": int(L["gi"][k]), "season": int(L["season"][k]),
        "line_t60": float(L["line"][k]), "line_t5": float(L["line5"][k]),
        "devig_over_t60": float(L["mult"][k]), "model_p_over": float(L["pm"][k]),
        "model_lean": "over" if lean[k] > 0 else "under",
        "move_direction": "up" if D["mdir"][k] > 0 else "down",
        "went_over_t60_line": bool(L["over"][k] > 0.5),
        "n_candidate_legs": int(idx.size),
    }  # fmt: skip


def run_all(L: dict[str, Any]) -> dict[str, Any]:
    D = derive(L)
    N = null_arrays(L, D)
    boot = Boot(L["gi"], L["G"])
    res: dict[str, Any] = {"slices": [], "by_season": []}
    fam: list[tuple[int, str]] = []
    for dim, val, mask in slice_masks(L):
        rec = {"dim": dim, "value": val, **cell_stats(boot, mask, D, N, L)}
        res["slices"].append(rec)
        if dim == "season":
            continue
        for tag in ("all", "strong"):
            fam.append((len(res["slices"]) - 1, tag))
    # consistency across seasons: the agreement-minus-null estimate in each season, per slice
    for s in SEASONS:
        sm = L["season"] == s
        for dim, val, mask in slice_masks(L):
            if dim == "season":
                continue
            st = cell_stats(boot, mask & sm, D, N, L)
            res["by_season"].append(
                {
                    "season": s, "dim": dim, "value": val, "n_joined": st["n_joined"],
                    "lean_agree_all": st["lean_agree_all"].get("est"),
                    "lean_agree_strong": st["lean_agree_strong"].get("est"),
                    "brier_gain": st["brier_gain_unchanged"]["est"],
                }
            )  # fmt: skip
    ok = [
        (i, t)
        for i, t in fam
        if res["slices"][i]["status"] == "ok"
        and res["slices"][i][f"lean_agree_{t}"].get("p") is not None
    ]
    qs = bh([res["slices"][i][f"lean_agree_{t}"]["p"] for i, t in ok])
    for (i, t), q in zip(ok, qs, strict=True):
        res["slices"][i][f"lean_agree_{t}"]["q_bh"] = q
    res["contrasts"] = contrasts(L, D, boot)
    res["example"] = example(L, D)
    res["meta"] = {
        "n_legs": L["n"], "n_games": L["G"], "seasons": list(SEASONS), "books": list(BOOKS),
        "closing_proxy": "t5 capture (about tip - 9 min); T-60 about tip - 64 min",
        "n_boot": N_BOOT, "n_null_shuffles": N_NULL, "strong_lean": STRONG, "min_n": MIN_N,
        "bh_family_size": len(ok), "descriptive": True, "stats": list(STATS),
    }  # fmt: skip
    return res


def _pt(x: float | None, nd: int = 2, pct: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{100 * x:+.{nd}f}" if pct else f"{x:.{nd}f}"


def _ci(d: dict[str, Any], nd: int = 2, sign: bool = True) -> str:
    if d.get("est") is None:
        return "n/a"
    spec = f"+.{nd}f" if sign else f".{nd}f"
    e, lo, hi = (format(100 * d[k], spec) for k in ("est", "lo", "hi"))
    return f"{e} [{lo}, {hi}]"


def render(res: dict[str, Any]) -> str:
    L = [
        "| slice | joined legs | lines moved | mean abs price move, unchanged (pts) | market learned: Brier gain x100 [CI] | follow-the-move hit % [CI] | ...line moves only | ...price moves only |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in res["slices"]:
        flag = "" if s["status"] == "ok" else " (insufficient sample)"
        L.append(
            f"| {s['dim']} = {s['value']}{flag} | {s['n_joined']} | {_ci(s['changed_share'], 1, False)}% | "
            f"{_pt(s['mean_abs_move_unchanged']['est'], 2).lstrip('+')} | "
            f"{_ci(s['brier_gain_unchanged'], 3)} | {_ci(s['follow_move_hit'], 1, False)} | {_ci(s['follow_line_move_hit'], 1, False)} | {_ci(s['follow_price_move_hit'], 1, False)} |"
        )
    L += ["", "### Does the model's T-60 lean predict the direction of the move? (agreement minus shuffled-forecast null, percentage points)", "",
          "| slice | moved legs (all) | model - null, all legs [CI] | q (BH) | strong-lean moved legs | model - null, strong [CI] | q (BH) | lean CLV, strong (prob pts, vs null) [CI] |",
          "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for s in res["slices"]:
        a, b, c = s["lean_agree_all"], s["lean_agree_strong"], s["lean_clv_strong"]
        qa = ("<0.01" if a["q_bh"] < 0.01 else f"{a['q_bh']:.2f}") if "q_bh" in a else "-"
        qb = ("<0.01" if b["q_bh"] < 0.01 else f"{b['q_bh']:.2f}") if "q_bh" in b else "-"
        L.append(
            f"| {s['dim']} = {s['value']} | {a.get('n_moved', 0)} | {_ci(a, 2)} | {qa} | {b.get('n_moved', 0)} | {_ci(b, 2)} | {qb} | {_ci(c, 3)} |"
        )
    L += ["", "### Pre-declared contrasts (difference, game-clustered 95% CI; Brier and move size in percentage points x100 of raw units)", "",
          "| contrast | Brier gain diff | lines-moved share diff | mean abs price move diff |", "|---|---|---|---|"]  # fmt: skip
    for c in res["contrasts"]:
        L.append(
            f"| {c['contrast']} | {_ci(c['brier_gain_unchanged'], 3)} | {_ci(c['changed_share'], 1)} | {_ci(c['mean_abs_move_unchanged'], 2)} |"
        )
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--pairs-dir", type=Path, default=Path("data/ops/model_clv_pairs"))
    r.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    L = load(a.pairs_dir)
    res = run_all(L)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "results.json").write_text(json.dumps(res, indent=2))
    (a.out / "tables.md").write_text(render(res))
    print(f"wrote {a.out}/results.json; legs={L['n']} games={L['G']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
