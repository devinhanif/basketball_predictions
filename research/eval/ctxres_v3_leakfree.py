# ruff: noqa: E501
"""Leak-free experiment 3 (per-stat hybrid on the leak-free ridge_v2 features): guards and rule.

PRE-REGISTRATION: docs/CTXRES_V3_LEAKFREE.md (text above its ``<!-- PREREG-END -->`` line, written
before any arm was fit). Season 2024 has ALREADY been seen for this recipe (docs/RIDGE_V2.md sec. 9),
so its result here is a REPLICATION / sanity check, never a confirmation; confirmation needs a
maintainer-approved single 2025 touch or the 2026-27 forward log. Season 2025 is never read here.

Contents
--------
* guards: no ``ridge_*`` (leaky) feature names; ``rg_*`` names must match the ridge_v2 pattern;
  one-hot position for any linear / MLP design (``pos_code`` is categorical); missingness audit of
  the final feature frame through the ``nba.datamanifest`` played/unplayed NULL-asymmetry machinery
  (any column with asymmetry >= the configured 0.5 is a STOP); exclusive heavy-job lock.
* rule: the experiment-2 keep rule (``ctxres_v2_eval.keep_rule``, PIT coverage per Amendment A1.1)
  applied to ``hybrid_lf`` vs ``v1_prod`` on identical rows, once per season (2023 = selection /
  consistency gate, 2024 = replication), plus the pre-registered classification.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.props.metrics import paired_score_delta_ci
from research.eval.ctxres_v2_eval import ece_equal_mass, keep_rule

STATS = ("pts", "reb", "ast", "fg3m")
CANDIDATE = "hybrid_lf"
REFERENCE = "v1_prod"
DESCRIPTIVE_VARIANTS = ("recency_normal", "alt_lf")
SEASONS = (2023, 2024)
THRESHOLDS: dict[str, list[int]] = {
    "pts": [10, 15, 20, 25, 30],
    "reb": [4, 6, 8, 10],
    "ast": [2, 4, 6, 8],
    "fg3m": [1, 2, 3, 4],
}
PREREG_MARKER = "<!-- PREREG-END -->"
RG_COL = re.compile(r"^rg_[0-9a-f]{8}_(p|o|ox|px|tm)_(pts|reb|ast|fg3m)$")
LEAKY_PREFIX = "ridge_"
ASYMMETRY_STOP = 0.5
LOCK_DIR = Path("data/ops/heavy.lock")
SLICE_EXPORT_COLS = [
    "game_id",
    "player_id",
    "has_report",
    "n_out_rot",
    "min_gap",
    "team_game_no",
    "starter10",
]
POS_CATEGORIES = tuple(range(0, 6))


class LeakStop(RuntimeError):
    """A leakage guard fired: the run must not proceed."""


# ---------------------------------------------------------------------------- pre-registration


def prereg_text(path: str | Path) -> str:
    """The pre-registration text: everything up to and including the marker LINE.

    The marker must be a line by itself (the document also mentions it inline in prose)."""
    lines = Path(path).read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == PREREG_MARKER:
            return "".join(lines[: i + 1])
    raise ValueError(f"{path}: marker line {PREREG_MARKER!r} not found")


def prereg_sha256(path: str | Path) -> str:
    return hashlib.sha256(prereg_text(path).encode()).hexdigest()


# ---------------------------------------------------------------------------- feature guards


def assert_leakfree_columns(cols_by_stat: Mapping[str, Iterable[str]]) -> None:
    """No leaky exp-2 ``ridge_*`` column anywhere; every ``rg_*`` name is a ridge_v2 name."""
    bad: list[str] = []
    for stat, cols in cols_by_stat.items():
        for c in cols:
            if c.startswith(LEAKY_PREFIX) or (c.startswith("rg_") and RG_COL.match(c) is None):
                bad.append(f"{stat}:{c}")
    if bad:
        raise LeakStop(f"leaky or malformed ridge columns in feature list: {bad[:10]}")


def onehot_pos(pos_code: np.ndarray, categories: Sequence[int] = POS_CATEGORIES) -> np.ndarray:
    """One-hot matrix ``(n, len(categories) + 1)``; the last column flags unknown / missing codes."""
    p = np.nan_to_num(np.asarray(pos_code, dtype=float), nan=-1.0)
    cols = [(p == c).astype(np.float32) for c in categories]
    known = np.any(np.stack(cols, axis=1), axis=1)
    cols.append((~known).astype(np.float32))
    return np.stack(cols, axis=1)


def linear_design(
    X: np.ndarray, names: Sequence[str], pos_code: np.ndarray
) -> tuple[np.ndarray, list[str]]:
    """Design matrix for a linear / MLP arm: numeric ``pos_code`` removed, one-hot position appended."""
    keep = [i for i, n in enumerate(names) if n != "pos_code"]
    out_names = [names[i] for i in keep]
    oh = onehot_pos(pos_code)
    out_names += [f"pos_oh_{k}" for k in range(oh.shape[1])]
    return np.concatenate([np.asarray(X)[:, keep].astype(np.float32), oh], axis=1), out_names


# ---------------------------------------------------------------------------- missingness audit


def asymmetry_stop(
    table_manifest: Mapping[str, Any], columns: Iterable[str], threshold: float = ASYMMETRY_STOP
) -> dict[str, float]:
    """Played/unplayed NULL-rate asymmetry of ``columns``; raises ``LeakStop`` if any is >= threshold.

    ``table_manifest`` is one table entry of ``nba.datamanifest.manifest`` (``columns`` carry
    ``asymmetry`` when both the played and the unplayed groups are large enough). A column whose
    asymmetry is ``None`` (a group too small to judge) is reported as 0.0 and also listed by the
    caller via ``played_split``. Returns column -> asymmetry."""
    cols = table_manifest.get("columns", {})
    out: dict[str, float] = {}
    for c in columns:
        a = cols.get(c, {}).get("asymmetry")
        out[c] = float(a) if a is not None else 0.0
    stop = {c: a for c, a in out.items() if a >= threshold}
    if stop:
        raise LeakStop(f"played/unplayed NULL asymmetry >= {threshold} (possible leak): {stop}")
    return out


def missingness_audit(
    frame_path: str | Path,
    db_path: str | Path,
    columns: Iterable[str],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the datamanifest played/unplayed split on the final feature frame (a parquet keyed by
    game_id, player_id) against ``player_game_stats`` in the read-only DB; STOP on asymmetry >= 0.5."""
    from nba.datamanifest.manifest import _connect, _describe, _table_manifest, load_config

    cfg = cfg or load_config()
    con = _connect(Path(db_path), cfg)
    try:
        names = {
            str(r[0])
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "player_game_stats" not in names:
            raise LeakStop("player_game_stats missing: cannot split played/unplayed")
        rel = f"read_parquet('{str(frame_path).replace(chr(39), '')}')"
        have_games = "games" in names
        have_cols = [c for c, _ in _describe(con, rel)]
        tm = _table_manifest(
            con,
            Path(frame_path).stem,
            rel,
            cfg,
            pk_cols=["game_id", "player_id"],
            have_games=have_games,
            have_pgs=True,
            fingerprint=False,
        )
    finally:
        con.close()
    if "played_split" not in tm:
        raise LeakStop("feature frame could not be split into played/unplayed rows")
    thr = float(cfg.get("thresholds", {}).get("asymmetry", ASYMMETRY_STOP))
    cols = [c for c in columns if c in have_cols]
    asym = asymmetry_stop(tm, cols, thr)
    worst = sorted(asym.items(), key=lambda kv: -kv[1])[:5]
    return {
        "played_split": tm["played_split"],
        "threshold": thr,
        "n_columns": len(cols),
        "max_asymmetry": worst[0][1] if worst else 0.0,
        "top5": worst,
        "pk_duplicates": tm.get("pk_duplicates"),
    }


# ---------------------------------------------------------------------------- heavy-job lock


@contextlib.contextmanager
def heavy_lock(
    path: str | Path = LOCK_DIR, retry_s: float = 120.0, max_wait_s: float | None = None
) -> Iterator[None]:
    """Exclusive ``mkdir`` lock shared with the other overnight jobs; retries every ``retry_s``;
    the directory is removed in ``finally``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    while True:
        try:
            os.mkdir(p)
            break
        except FileExistsError:
            if max_wait_s is not None and time.monotonic() - t0 > max_wait_s:
                raise TimeoutError(f"heavy lock {p} still held after {max_wait_s:.0f}s") from None
            print(f"heavy lock {p} held; retrying in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(p)


# ---------------------------------------------------------------------------- rule


def load_season_oof(run_dir: str | Path, season: int) -> pl.DataFrame:
    d = Path(run_dir) / f"oof_{season}"
    files = sorted(d.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{d} has no parquet files")
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def classify(r2023: Mapping[str, Any], r2024: Mapping[str, Any]) -> dict[str, Any]:
    """Pre-registered classification: a stat REPLICATES iff it passes the full rule on 2024 and the
    2023 clustered CRPS-delta CI upper bound is < 0. Overall = all four stats."""
    per: dict[str, Any] = {}
    for st in STATS:
        p24 = bool(r2024[st]["keep"])
        d23 = r2023[st]["crps_delta"]
        dir23 = bool(d23[2] < 0)
        per[st] = {"pass_2024": p24, "ci_upper_2023_below_0": dir23, "replicates": p24 and dir23}
    return {"per_stat": per, "replicates_overall": all(v["replicates"] for v in per.values())}


def _delta(
    oof: pl.DataFrame, a: str, b: str, st: str, col: str = "crps", n_boot: int = 2000
) -> dict[str, Any]:
    x = oof.filter((pl.col("variant") == a) & (pl.col("stat") == st))
    y = oof.filter((pl.col("variant") == b) & (pl.col("stat") == st))
    pr = x.join(y, on=["game_id", "player_id"], suffix="_b")
    if pr.is_empty():
        return {"n": 0}
    ci = paired_score_delta_ci(
        pr[col].to_numpy(),
        pr[f"{col}_b"].to_numpy(),
        n_boot=n_boot,
        cluster_ids=pr["game_id"].to_numpy(),
    )
    return {"n": pr.height, "delta": [ci.point, ci.lo, ci.hi]}


def _mat(s: pl.Series) -> np.ndarray:
    return np.array(s.to_list(), dtype=float)


def threshold_metrics(oof: pl.DataFrame, variant: str, st: str) -> dict[str, Any]:
    d = oof.filter((pl.col("variant") == variant) & (pl.col("stat") == st))
    if d.is_empty() or "p_ge" not in d.columns:
        return {"n": 0}
    p = np.clip(_mat(d["p_ge"]), 1e-4, 1 - 1e-4)
    thr = np.array(THRESHOLDS[st])
    ev = d["y"].to_numpy()[:, None] >= thr[None, :]
    return {
        "n": d.height,
        "tll": float(d["tll"].mean()),  # type: ignore[arg-type]
        "brier": float(((p - ev) ** 2).mean()),
        "ece": ece_equal_mass(p, ev),
    }


def evaluate(
    run_dir: str | Path,
    export_path: str | Path,
    n_boot: int = 2000,
    gpu_oof_dir: str | Path | None = None,
    stored_v1_oof: str | Path | None = None,
) -> dict[str, Any]:
    """Apply the pre-registered rule to both seasons and compute the descriptive tables."""
    export = pl.read_parquet(export_path, columns=SLICE_EXPORT_COLS)
    rules: dict[int, dict[str, Any]] = {}
    desc: dict[str, Any] = {}
    for season in SEASONS:
        oof = load_season_oof(run_dir, season)
        rules[season] = keep_rule(oof, export, CANDIDATE, REFERENCE, n_boot=n_boot, coverage="pit")
        d: dict[str, Any] = {"threshold": {}, "vs": {}, "per_fold": {}}
        for st in STATS:
            d["threshold"][st] = {
                v: threshold_metrics(oof, v, st)
                for v in (CANDIDATE, REFERENCE, "alt_lf")
                if v in set(oof["variant"].unique())
            }
            vs: dict[str, Any] = {}
            variants = set(oof["variant"].unique().to_list())
            if "recency_normal" in variants:
                vs["hybrid_lf-recency_normal"] = _delta(
                    oof, CANDIDATE, "recency_normal", st, n_boot=n_boot
                )
                vs["v1_prod-recency_normal"] = _delta(
                    oof, REFERENCE, "recency_normal", st, n_boot=n_boot
                )
            if "alt_lf" in variants:
                vs["alt_lf-hybrid_lf"] = _delta(oof, "alt_lf", CANDIDATE, st, n_boot=n_boot)
            vs["hybrid_lf-v1_prod_tll"] = _delta(
                oof, CANDIDATE, REFERENCE, st, col="tll", n_boot=n_boot
            )
            d["vs"][st] = vs
            pr = oof.filter(pl.col("stat") == st).filter(
                pl.col("variant").is_in([CANDIDATE, REFERENCE])
            )
            wide = pr.pivot(
                on="variant", index=["game_id", "player_id", "fold_id"], values="crps"
            ).drop_nulls()
            if not wide.is_empty():
                g = (
                    wide.group_by("fold_id")
                    .agg(
                        (pl.col(CANDIDATE) - pl.col(REFERENCE)).mean().alias("d"),
                        pl.len().alias("n"),
                    )
                    .sort("fold_id")
                )
                d["per_fold"][st] = [[int(f), float(x), int(n)] for f, x, n in g.iter_rows()]
        if (
            season == 2024
            and gpu_oof_dir is not None
            and Path(gpu_oof_dir, "ref_fixed.parquet").exists()
        ):
            gp = pl.read_parquet(Path(gpu_oof_dir) / "ref_fixed.parquet").with_columns(
                pl.lit("gpu_ref_fixed").alias("variant")
            )
            both = pl.concat(
                [oof.filter(pl.col("variant") == CANDIDATE), gp], how="diagonal_relaxed"
            )
            d["cpu_vs_gpu_ref_fixed"] = {
                st: _delta(both, CANDIDATE, "gpu_ref_fixed", st, n_boot=min(n_boot, 500))
                for st in STATS
            }
        if season == 2024 and stored_v1_oof is not None and Path(stored_v1_oof).exists():
            sv = pl.read_parquet(stored_v1_oof).with_columns(
                pl.lit("stored_v1_prod").alias("variant")
            )
            both = pl.concat(
                [oof.filter(pl.col("variant") == REFERENCE), sv], how="diagonal_relaxed"
            )
            d["local_vs_stored_v1_prod"] = {
                st: _delta(both, REFERENCE, "stored_v1_prod", st, n_boot=min(n_boot, 500))
                for st in STATS
            }
        desc[str(season)] = d
    return {
        "candidate": CANDIDATE,
        "reference": REFERENCE,
        "rules": {str(s): rules[s] for s in SEASONS},
        "classification": classify(rules[2023], rules[2024]),
        "descriptive": desc,
    }


# ---------------------------------------------------------------------------- report


def _f(x: float, n: int = 4) -> str:
    return f"{x:+.{n}f}"


def format_report(res: Mapping[str, Any], header: Mapping[str, Any] | None = None) -> str:
    lines = ["# ctxres_v3 leak-free hybrid -- replication report", ""]
    lines.append(
        "Pre-registration: `docs/CTXRES_V3_LEAKFREE.md`. Season 2024 was already seen for this recipe: a REPLICATION, not a confirmation. Season 2025 untouched."
    )
    if header:
        lines += ["", "Run header: " + ", ".join(f"{k}={v}" for k, v in header.items())]
    for season in ("2024", "2023"):
        r = res["rules"][season]
        lines += [
            "",
            f"## Season {season} -- `hybrid_lf` - `v1_prod` (CRPS, negative = hybrid better)",
            "",
            "| stat | n | dCRPS [95% CI, game-clustered] | BH q | bias | PIT cov80 | naive cov80 | slice regressions | checks failed | PASS |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for st in STATS:
            x = r[st]
            d = x["crps_delta"]
            failed = [k for k, v in x["checks"].items() if not v]
            lines.append(
                f"| {st} | {x['n']} | {_f(d[0])} [{_f(d[1])}, {_f(d[2])}] | {x['bh_adj_p']:.4f} | {x['bias']:+.3f} | {x['cov80_pit']:.3f} | {x['cov80']:.3f} | {', '.join(x['slice_regressions']) or '-'} | {', '.join(failed) or '-'} | {x['keep']} |"
            )
    c = res["classification"]
    lines += [
        "",
        "## Classification (pre-registered)",
        "",
        "| stat | pass 2024 | 2023 CI upper < 0 | replicates |",
        "|---|---|---|---|",
    ]
    for st in STATS:
        v = c["per_stat"][st]
        lines.append(
            f"| {st} | {v['pass_2024']} | {v['ci_upper_2023_below_0']} | {v['replicates']} |"
        )
    lines += [
        "",
        f"**Replicates overall: {c['replicates_overall']}** (a replication of a previously seen result; not a confirmation; no registration / promotion).",
        "",
        "## Slices (n >= 300, 2024, dCRPS)",
        "",
    ]
    for st in STATS:
        sl = [s for s in res["rules"]["2024"][st]["slices"] if s["n"] >= 300]
        lines.append(
            f"* {st}: " + "; ".join(f"{s['slice']} n={s['n']} {_f(s['delta'])}" for s in sl)
        )
    lines += ["", "## Descriptive (no decision)", ""]
    for season in ("2024", "2023"):
        d = res["descriptive"][season]
        lines += [
            f"### {season} threshold metrics (raw)",
            "",
            "| stat | variant | n | thr log loss | Brier | ECE |",
            "|---|---|---|---|---|---|",
        ]
        for st in STATS:
            for v, m in d["threshold"][st].items():
                if m.get("n"):
                    lines.append(
                        f"| {st} | {v} | {m['n']} | {m['tll']:.4f} | {m['brier']:.4f} | {m['ece']:.4f} |"
                    )
        lines += [
            "",
            f"### {season} paired CRPS / log-loss deltas",
            "",
            "| stat | comparison | n | delta [95% CI] |",
            "|---|---|---|---|",
        ]
        for st in STATS:
            for k, m in d["vs"][st].items():
                if m.get("n"):
                    q = m["delta"]
                    lines.append(f"| {st} | {k} | {m['n']} | {_f(q[0])} [{_f(q[1])}, {_f(q[2])}] |")
        for key in ("cpu_vs_gpu_ref_fixed", "local_vs_stored_v1_prod"):
            if key in d:
                lines += ["", f"### {season} {key} (CRPS delta, replication diagnostic)", ""]
                for st in STATS:
                    m = d[key][st]
                    if m.get("n"):
                        q = m["delta"]
                        lines.append(
                            f"* {st}: n={m['n']} {_f(q[0], 5)} [{_f(q[1], 5)}, {_f(q[2], 5)}]"
                        )
        lines += ["", f"### {season} per-block dCRPS (hybrid_lf - v1_prod)", ""]
        for st in STATS:
            lines.append(
                f"* {st}: "
                + ", ".join(f"{f}:{_f(x, 3)}(n={n})" for f, x, n in d["per_fold"].get(st, []))
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--export", default="data/colab/ctxres_v2/ctxres_v2.parquet")
    ap.add_argument("--out", default="reports/ctxres_v3_leakfree.md")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--gpu-oof", default="data/colab/runs/ridge_v2_sweep/20261008_194547/oof")
    ap.add_argument(
        "--stored-v1", default="data/colab/runs/ctxres_v2_sweep/20261008_171446/oof/v1_prod.parquet"
    )
    ap.add_argument("--n-boot", type=int, default=2000)
    a = ap.parse_args(argv)
    res = evaluate(a.run_dir, a.export, a.n_boot, a.gpu_oof, a.stored_v1)
    header_path = Path(a.run_dir) / "run_header.json"
    header = json.loads(header_path.read_text()) if header_path.exists() else None
    keep = {
        k: header[k]
        for k in ("prereg_sha256", "xgboost", "budget", "seed")
        if header and k in header
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(format_report(res, keep))
    Path(a.json_out or Path(a.run_dir) / "verdict.json").write_text(
        json.dumps(res, indent=1, default=float)
    )
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
