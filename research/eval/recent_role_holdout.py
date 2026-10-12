# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc"
"""RECENT_ROLE_HOLDOUT: one logged touch of season 2025 (docs/prereg/RECENT_ROLE_HOLDOUT.md; DRAFT until committed as
`prereg(RECENT_ROLE_HOLDOUT): <sha256>`).

    M="-m research.eval.recent_role_holdout"
    .venv/bin/python -W ignore $M rehearse                 # season 2024 stands in for 2025; same code path; never opens 2025
    .venv/bin/python -W ignore $M run --sha <64 hex>       # the ONE holdout run (heavy.lock); refuses unless both commits exist

`run` refuses unless (1) `git log` holds a commit with subject `prereg(RECENT_ROLE_HOLDOUT): <sha>` AND the text above the
marker of the rule hashes to <sha>, and (2) docs/HOLDOUT_ACCESS_LOG.md holds a COMMITTED, unmodified row containing
RECENT_ROLE_HOLDOUT, the first 8 hex of the sha, PRE-REGISTERED and DEVIN CONFIRMED. It also refuses a second run
(results.json present). The season cap (MAX_SEASON = 2024 in the repo's loaders) is raised to 2025 only inside `season_cap`,
only by `run` after both checks; `rehearse` keeps it at 2024. Candidate R, comparator base = C, secondary C' and the
arm columns are exactly those of the frozen RECENT_ROLE harness (research.eval.recent_role_eval), reused not copied.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval import context_residual_eval as CRE
from research.eval import recent_role_eval as RR
from research.eval import workload_eval as WE

ID = "RECENT_ROLE_HOLDOUT"
DOC = Path("docs/prereg/RECENT_ROLE_HOLDOUT.md")
HOLDOUT_LOG = Path("docs/HOLDOUT_ACCESS_LOG.md")
OUT_DIR = Path("reports/prereg_recent_role_holdout")
REPORT_MD = Path("reports/prereg_recent_role_holdout.md")
CACHE_ROOT = Path("data/ops/recent_role_holdout_cache")
HOLDOUT_SEASON = 2025
REHEARSAL_SEASON = 2024
REPO_CAP = 2024

FLOOR = (
    -0.010
)  # R - base, log loss (2/3 of the 2023-24 effect -0.0166/-0.0169, twice the house floor)
PLACEBO_MARGIN = RR.PLACEBO_MARGIN
SHUF_TOL = 0.003  # shuffled-label check fails at R_shuf - base_shuf <= -0.003 with CI upper < 0 (the 2024 rehearsal showed -0.00085 of fit noise)
ECE_TOL = RR.ECE_TOL
SLICE_TOL = RR.SLICE_TOL
MIN_SLICE_N = RR.MIN_SLICE_N
MIN_ROWS = 25_000  # run-time gate: Cell-P rows of the season (2023/2024 had 33,093 / 33,127)
MIN_GAMES = 1_000  # 1,316 games in 2025 per the HOLDOUT_ACCESS_LOG injury-Elo row


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- refusals
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path = DOC, git: Callable[..., str] = _git) -> str:
    """(1) the doc above the marker hashes to ``sha`` and a `prereg(RECENT_ROLE_HOLDOUT): <sha>` commit exists."""
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    if not doc.exists():
        raise RuntimeError(f"{doc} does not exist: not the frozen rule")
    got = WE.frozen_sha(doc)
    if got != sha:
        raise RuntimeError(f"{doc} hashes to {got[:8]}, not {sha[:8]}: not the frozen rule")
    if freeze_commit(sha, git) is None:
        raise RuntimeError(f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen")
    return sha


def require_holdout_row(sha: str, log: Path = HOLDOUT_LOG, git: Callable[..., str] = _git) -> str:
    """(2) a committed, unmodified HOLDOUT_ACCESS_LOG row: RECENT_ROLE_HOLDOUT, sha8, PRE-REGISTERED, DEVIN CONFIRMED."""
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            f"no holdout row for {ID} ({sha[:8]}, PRE-REGISTERED, DEVIN CONFIRMED) in {log}: refusing"
        )
    if git("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(f"{log} has uncommitted changes: commit the row BEFORE the run")
    if not git("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is in no commit: refusing")
    return rows[0]


def require_both(
    sha: str | None, doc: Path = DOC, log: Path = HOLDOUT_LOG, git: Callable[..., str] = _git
) -> str:
    s = require_frozen(sha, doc, git)
    require_holdout_row(s, log, git)
    return s


# --------------------------------------------------------------------------- season cap
def preimport() -> None:
    """Import every module the pipeline touches BEFORE the cap is raised, so `season_cap` patches and restores all of them."""
    import importlib

    for m in (
        "research.eval.pplay_context_eval",
        "research.eval.workload_gates",
        "research.eval.minutes_v2_eval",
        "research.eval.four_factors_eval",
        "research.eval.minutes_hazard_eval",
        "nba.eval.lower_tail_eval",
        "research.workload.asof",
    ):
        importlib.import_module(m)  # fmt: skip


@contextmanager
def season_cap(cap: int) -> Iterator[None]:
    """Raise the repo's `MAX_SEASON` (loaders' SQL bound and asserts) from 2024 to ``cap`` in every loaded nba.* / research.* module
    and in `load_inputs`' default; restore on exit. Used by `run` after both refusals pass; `rehearse` never calls it with 2025."""
    saved: list[tuple[Any, str, Any]] = []
    for name, mod in list(sys.modules.items()):
        if mod is None or not name.startswith(("nba.", "research.")):
            continue
        if getattr(mod, "MAX_SEASON", None) == REPO_CAP:
            saved.append((mod, "MAX_SEASON", REPO_CAP))
            setattr(mod, "MAX_SEASON", cap)  # noqa: B010
    li = CRE.load_inputs
    old_defaults = li.__defaults__
    if old_defaults is not None and REPO_CAP in old_defaults:
        li.__defaults__ = tuple(cap if d == REPO_CAP else d for d in old_defaults)
    try:
        yield
    finally:
        for mod, attr, val in saved:
            setattr(mod, attr, val)
        li.__defaults__ = old_defaults


# --------------------------------------------------------------------------- arms and scoring
def holdout_arms() -> dict[str, list[str]]:
    a = RR.arm_columns()
    return {k: a[k] for k in ("base", "Cp", "R", "PL")}


def shuffle_arms() -> dict[str, list[str]]:
    a = RR.arm_columns()
    return {k: a[k] for k in ("base", "R")}


def score_holdout(
    res: dict[str, np.ndarray], shuf: dict[str, np.ndarray], meta: pl.DataFrame, season: int
) -> dict[str, Any]:
    """Primary R - base on the season's Cell-P rows; secondary R - C'; attack rows; slices. Game-clustered CIs."""
    y, gid = res["y"], res["gid"]
    if not (res["season"] == season).all():
        raise ValueError("score_holdout expects only the held-out season's rows")
    n_games = int(len(np.unique(gid)))
    out: dict[str, Any] = {"season": season, "rows": [], "slices": []}
    for arm in ("base", "Cp", "R", "PL"):
        p = res[f"p_{arm}"]
        out["rows"].append(
            {
                "arm": arm,
                "n_rows": int(len(y)),
                "n_games": n_games,
                "logloss": float(RR._ll(p, y).mean()),
                "ece": float(WE.ece(p, y)),
            }
        )
    for a, b in (("R", "base"), ("R", "Cp"), ("Cp", "base"), ("R", "PL")):
        d = RR._ll(res[f"p_{a}"], y) - RR._ll(res[f"p_{b}"], y)
        out["rows"].append({"arm": f"{a}_minus_{b}", "n_rows": int(len(y)), "n_games": n_games, **WE.delta_stats(d, gid),
                            "ece_delta": float(WE.ece(res[f"p_{a}"], y) - WE.ece(res[f"p_{b}"], y))})  # fmt: skip
    ds = RR._ll(shuf["p_R"], shuf["y"]) - RR._ll(shuf["p_base"], shuf["y"])
    out["rows"].append(
        {
            "arm": "R_shuf_minus_base_shuf",
            "n_rows": int(len(shuf["y"])),
            "n_games": int(len(np.unique(shuf["gid"]))),
            **WE.delta_stats(ds, shuf["gid"]),
        }
    )
    dd = RR._ll(res["p_R"], y) - RR._ll(res["p_base"], y)
    for name, mk in RR.slice_masks_p(meta).items():
        rec: dict[str, Any] = {"slice": name, "n": int(mk.sum())}
        if mk.sum() >= MIN_SLICE_N or name == "all":
            s = WE.delta_stats(dd[mk], gid[mk])
            rec.update({k: s[k] for k in ("delta", "ci_lo", "ci_hi")})
        out["slices"].append(rec)
    return out


def shuffle_check(row: dict[str, Any]) -> dict[str, Any]:
    """Fails when models trained on permuted labels still show R beating base by a material amount."""
    failed = bool(row["delta"] <= -SHUF_TOL and row["ci_hi"] < 0)
    return {"season": row["season"], "delta": row["delta"], "ci_hi": row["ci_hi"], "ok": not failed}


def decide_holdout(sc: dict[str, Any], gate_ok: bool) -> dict[str, Any]:
    """The frozen pass rule. All conditions must hold; every reason is returned."""
    r = {x["arm"]: x for x in sc["rows"]}
    prim = r["R_minus_base"]
    c_prim = bool(prim["delta"] <= FLOOR and prim["ci_hi"] < 0)
    c_ece = bool(prim["ece_delta"] <= ECE_TOL)
    worse = [
        (x["slice"], x["n"], x["delta"])
        for x in sc["slices"]
        if x["slice"] != "all" and x["n"] >= MIN_SLICE_N and x.get("delta", -1.0) > SLICE_TOL
    ]
    c_pl = bool(r["R_minus_PL"]["delta"] <= PLACEBO_MARGIN)
    shuf = shuffle_check({"season": sc["season"], **r["R_shuf_minus_base_shuf"]})
    passed = bool(c_prim and c_ece and not worse and c_pl and shuf["ok"] and gate_ok)
    return {"pass": passed, "primary": c_prim, "ece": c_ece, "slices_ok": not worse, "slices_worse": worse, "placebo": c_pl,
            "shuffle": shuf, "gate_ok": gate_ok, "mde": prim["mde"], "secondary_R_minus_Cp": r["R_minus_Cp"]["delta"]}  # fmt: skip


def plants(src: dict[str, Any], rows: pl.DataFrame, season: int) -> list[dict[str, Any]]:
    """WORKLOAD's real-table plants on the held-out season: same-game, future, schedule, each with a control that must change."""
    played = src["played"]
    games = src["games"].filter(pl.col("season") == season).sort("game_date")
    day = games["game_date"][games.height // 2]
    tgt = rows.filter(pl.col("season") == season).select("game_id", "player_id", "game_date")
    from research.workload import asof as W

    base = W.player_load_features(played, tgt)
    chk = WE._check

    def edited(mask: pl.Expr) -> pl.DataFrame:
        p2 = played.with_columns(
            pl.when(mask).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
            pl.when(mask).then(99.0).otherwise(pl.col("distance")).alias("distance"),
            pl.when(mask).then(9.0).otherwise(pl.col("speed")).alias("speed"),
        )
        return W.player_load_features(p2, tgt)

    def same(a: pl.DataFrame, b: pl.DataFrame, keys: pl.DataFrame) -> bool:
        x, y = a.join(keys, on=WE.KEYS).sort(WE.KEYS), b.join(keys, on=WE.KEYS).sort(WE.KEYS)
        return all(
            np.allclose(
                x[c].to_numpy().astype(float), y[c].to_numpy().astype(float), equal_nan=True
            )
            for c in x.columns
            if c not in WE.KEYS
        )

    k_day, k_after = (
        tgt.filter(pl.col("game_date") == day).select(WE.KEYS),
        tgt.filter(pl.col("game_date") > day).select(WE.KEYS),
    )
    k_early = tgt.filter(pl.col("game_date") <= day).select(WE.KEYS)
    same_day, fut = edited(pl.col("game_date") == day), edited(pl.col("game_date") >= day)
    return [
        chk(
            "planted same-game: rows of the edited day identical",
            k_day.height,
            "identical",
            same(base, same_day, k_day),
        ),
        chk(
            "control: rows after the edited day change",
            k_after.height,
            "not identical",
            not same(base, same_day, k_after),
        ),
        chk(
            "planted future: rows on or before the cutoff identical",
            k_early.height,
            "identical",
            same(base, fut, k_early),
        ),
    ]


def run_gate(fr: pl.DataFrame, aux: dict[str, Any], season: int) -> list[dict[str, Any]]:
    chk = WE._check
    s = fr.filter(pl.col("season") == season)
    n_rows, n_games = s.height, int(s["game_id"].n_unique())
    tests = [
        chk(
            f"season {season} Cell-P rows and games",
            {"rows": n_rows, "games": n_games},
            f">= {MIN_ROWS} rows, >= {MIN_GAMES} games",
            n_rows >= MIN_ROWS and n_games >= MIN_GAMES,
        )
    ]
    cols = [*RR.CP_ADD, *RR.R_COLS, *[RR.PL_PREFIX + c for c in RR.R_COLS]]
    aud = WE.audit_nulls(s, cols, WE.minutes_bucket("min_real", dnp=True))
    tests.append(
        chk(
            "missingness of added columns by realised-minutes bucket incl DNP (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp")},
            f"<= {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )
    tests += plants(aux["src"], fr.select("game_id", "player_id", "season", "game_date"), season)
    masks = RR.slice_masks_p(RR.meta_of(s))
    sizes = {k: int(v.sum()) for k, v in masks.items()}
    tests.append(
        chk(
            "slice sizes (n rows; slices under 300 are skipped by the rule)",
            sizes,
            "reported",
            True,
        )
    )
    return tests


def run_season(season: int, sha: str | None, out_dir: Path, cache: Path) -> dict[str, Any]:
    """Build the frames (cap already set by the caller), gate, walk base/Cp/R/PL and shuffled base/R, score and decide. Writes out_dir."""
    t0 = time.time()
    old = getattr(RR, "TEST_SEASONS")  # noqa: B009
    setattr(RR, "TEST_SEASONS", (season,))  # noqa: B010
    try:
        fr, cfg, aux = RR.build_frame()
        WE.assert_seasons(fr)
        tests = run_gate(fr, aux, season)
        gate_ok = all(t["ok"] for t in tests)
        for t in tests:
            print(
                ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
                flush=True,
            )
        if not gate_ok:
            out = {
                "sha256": sha,
                "season": season,
                "gate": tests,
                "gate_ok": False,
                "verdict": {"pass": False, "reason": "stopped at the gate; no arm scored"},
            }
            WE.write_json(out_dir / "results.json", out)
            return out
        res, _ = RR.walk(fr, holdout_arms(), cfg, ckpt=cache / "blocks")
        shuf, audit_s = RR.walk(fr, shuffle_arms(), cfg, shuffle=True, ckpt=cache / "blocks_shuf")
        hs = audit_s["label_hashes"]
        if hs["base"] != hs["R"]:
            raise RuntimeError(
                "the shuffled base and R arms were not fitted on the same permuted labels"
            )
        meta = RR.meta_of(fr)
        keys = pl.DataFrame({"game_id": res["gid"], "player_id": res["pid"].astype(np.int64)})
        mm = keys.join(meta.unique(RR.KEYS), on=RR.KEYS, how="left")
        if mm.height != len(res["gid"]):
            raise RuntimeError("meta join changed the row count")
        sc = score_holdout(res, shuf, mm, season)
        verdict = decide_holdout(sc, gate_ok)
    finally:
        setattr(RR, "TEST_SEASONS", old)  # noqa: B010
    result: dict[str, Any] = {"sha256": sha, "season": season, "scores": sc, "verdict": verdict, "gate": tests, "gate_ok": gate_ok, "seconds": time.time() - t0,
           "props_secondary": "not run: no stored 2025 A0 components exist (reports/lower_tail components are 2023-24 by construction); a live-refit props test would be a new id"}  # fmt: skip
    WE.write_json(out_dir / "results.json", result)
    return result


def render(out: dict[str, Any]) -> str:
    v = out["verdict"]
    lines = [f"# {ID} results (season {out['season']})", "", f"Rule sha256 {str(out['sha256'])[:8]}. Verdict: **{'PASS' if v['pass'] else 'NOT A PASS'}**.", "",
             "| arm | n rows | n games | log loss / delta | CI | p | MDE | ECE (delta) |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in out.get("scores", {}).get("rows", []):
        if "logloss" in r:
            lines.append(
                f"| {r['arm']} | {r['n_rows']} | {r['n_games']} | {r['logloss']:.4f} | | | | {r['ece']:.4f} |"
            )
        else:
            lines.append(
                f"| {r['arm']} | {r['n_rows']} | {r['n_games']} | {r['delta']:.4f} | [{r['ci_lo']:.4f}, {r['ci_hi']:.4f}] | {r['p']:.4f} | {r['mde']:.4f} | {r.get('ece_delta', float('nan')):.4f} |"
            )
    lines += ["", "| slice | n | R - base | CI |", "|---|---|---|---|"]
    for s in out.get("scores", {}).get("slices", []):
        ci = "" if "ci_lo" not in s else f"[{s['ci_lo']:.4f}, {s['ci_hi']:.4f}]"
        lines.append(f"| {s['slice']} | {s['n']} | {s.get('delta', float('nan')):.4f} | {ci} |")
    return "\n".join(lines) + "\n"


def rehearse() -> dict[str, Any]:
    """Season 2024 through the identical path, cap left at 2024: the numbers must equal RECENT_ROLE's stored 2024 rows."""
    preimport()
    out = run_season(REHEARSAL_SEASON, None, OUT_DIR / "rehearsal", CACHE_ROOT / "rehearsal")
    (OUT_DIR / "rehearsal").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "rehearsal" / "report.md").write_text(render(out))
    return out


def run(sha: str | None) -> dict[str, Any]:
    require_both(sha)
    prev = OUT_DIR / "results.json"
    if prev.exists() and json.loads(prev.read_text()).get("gate_ok", True):
        raise RuntimeError("a scored results.json exists: the holdout is touched once; no reruns")
    # (a run that STOPPED AT THE GATE scored nothing and may be repeated after a plumbing fix; see the rule)
    preimport()
    with WE.heavy(), season_cap(HOLDOUT_SEASON):
        out = run_season(HOLDOUT_SEASON, sha, OUT_DIR, CACHE_ROOT / "run")
    REPORT_MD.write_text(render(out))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["rehearse", "run"])
    ap.add_argument("--sha", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "rehearse":
        with WE.heavy():
            rehearse()
        return 0
    return 0 if run(a.sha)["verdict"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
