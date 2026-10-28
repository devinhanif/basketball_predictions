"""F12B_POOLED (rule: docs/prereg/F12B_POOLED.md, FROZEN 83a0bd57 at 2c22430): gate and premise.

    .venv/bin/python -m nba.eval.f12b_pooled gate --out reports/prereg_f12b_pooled/gate.json
    .venv/bin/python -m nba.eval.f12b_pooled premise --sha <64 hex> \\
        --out reports/prereg_f12b_pooled/results.json

Two pooled windows, each read-only: SELECT = seasons 2013-2021 from
``data/history/nba_history.duckdb``, REPORT = seasons 2022-2024 from ``nba.duckdb``. Per season:
G1a, G1b, G2-A1, G3 (F12B_CLOSING_RISK's checks, unchanged). Per window: G4 cell counts and G5,
the projected 95% half-width of the pooled young gap (game-clustered bootstrap spread of the
NO-exclusion young gap over every game of the window, already measured in F12_RETEST / F12B's
audit, inflated by sqrt(12 N / sum A_g) over the pooled young x blowout cell). The gate computes
no ratio with garbage time removed. ``premise`` (refuses unless the frozen rule's hash and freeze
commit match ``--sha``) computes SELECT first and REPORT only if SELECT passes, as the rule says.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from nba.eval import f12b_closing_risk as F

WINDOWS: dict[str, tuple[str, tuple[int, ...]]] = {
    "select": ("data/history/nba_history.duckdb", tuple(range(2013, 2022))),
    "report": ("nba.duckdb", (2022, 2023, 2024)),
}
HALF_WIDTH_MAX = 0.04
PER_SEASON_CHECKS = ("G1a", "G1b", "G2-A1", "G3")


def pooled_precision(df: pl.DataFrame, games: list[str]) -> dict[str, Any]:
    """G4 counts and the G5 projection over the pooled rows of one window (spread only)."""
    cs = F.cells(df.filter(pl.col("a_g") >= F.MIN_A))
    cells = {
        f"{g}_{c}": {
            "player_games": sub.height,
            "games": sub["game_id"].n_unique(),
            "sum_A_min": float(sub["a_g"].sum()),
        }
        for (g, c), sub in cs.items()
    }
    cs_all = F.cells(df)
    bs = F.boot_gap(cs_all[("young", "close")], cs_all[("young", "blow")], games, "q4", "q13")
    hw = float((np.quantile(bs, 0.975) - np.quantile(bs, 0.025)) / 2.0)
    yb = cs[("young", "blow")]
    infl = float(np.sqrt(12.0 * yb.height / float(yb["a_g"].sum()))) if yb.height else float("inf")
    return {
        "cells": cells,
        "young_gap_halfwidth_no_exclusion": hw,
        "inflation_sqrt_12_over_A": infl,
        "young_gap_halfwidth_projected": hw * infl,
    }


def run_gate(root: Path = Path(".")) -> dict[str, Any]:
    t0 = time.time()
    out: dict[str, Any] = {"rule": "F12B_POOLED (DRAFT)", "windows": {}, "checks": []}
    for name, (db, seasons) in WINDOWS.items():
        path = str(root / db)
        per = F.run_gate(path, seasons)  # per-season G1-G4 (and per-season G5, reported only)
        keep = [
            c
            for c in per["checks"]
            if any(c["check"].startswith(k + " ") for k in PER_SEASON_CHECKS)
        ]
        df, gm = F.load(path, seasons)
        games = sorted(gm["game_id"].to_list())
        pp = pooled_precision(df, games)
        cmin = min(v["player_games"] for v in pp["cells"].values())
        gmin = min(v["games"] for v in pp["cells"].values())
        a_yb = pp["cells"]["young_blow"]["sum_A_min"]
        checks = [*keep]
        checks.append(
            {
                "check": f"G4 pooled cells >= 500 player-games and >= 150 games ({name})",
                "value": [cmin, gmin],
                "ok": cmin >= 500 and gmin >= 150,
            }
        )
        checks.append(
            {
                "check": f"G4 pooled young x blowout non-garbage Q4 minutes ({name}) >= 1000",
                "value": a_yb,
                "ok": a_yb >= 1000,
            }
        )
        checks.append(
            {
                "check": f"G5 pooled projected young-gap half-width ({name}) <= {HALF_WIDTH_MAX}",
                "value": pp["young_gap_halfwidth_projected"],
                "ok": pp["young_gap_halfwidth_projected"] <= HALF_WIDTH_MAX,
            }
        )
        out["windows"][name] = {
            "db": db,
            "seasons": list(seasons),
            "games": len(games),
            "player_games": df.height,
            "max_season": int(df["season"].max()),  # type: ignore[arg-type]
            "pooled": pp,
            "per_season_info": {
                s: {k: v for k, v in r.items() if k != "young_gap_halfwidth_no_exclusion"}
                for s, r in per["seasons"].items()
            },
        }
        out["checks"] += checks
        print(f"[{time.time() - t0:6.1f}s] {name}: {len(games)} games", flush=True)
    out["all_ok"] = all(c["ok"] for c in out["checks"])
    return out


# ----------------------------------------------------- premise (FROZEN 83a0bd57, commit 2c22430)
ID = "F12B_POOLED"
DOC_REL = "docs/prereg/F12B_POOLED.md"
MARKER = "=== RESULTS BELOW ==="
PRECISION_MAX = 0.05  # realised REPORT young-gap 95% half-width; wider -> INCONCLUSIVE


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def _above_marker(text: str) -> str:
    keep: list[str] = []
    for ln in text.splitlines(keepends=True):
        if ln.rstrip("\n") == MARKER:
            break
        keep.append(ln)
    return "".join(keep)


def require_frozen(sha: str, doc: Path = Path(DOC_REL), git: Any = _git) -> str:
    """Refuse unless the doc hashes to ``sha`` and a commit `prereg(F12B_POOLED): <sha prefix>`
    carries a copy of the doc hashing to it."""
    import re

    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise SystemExit("refused: --sha <64 hex sha256 of the committed rule> is required")
    have = hashlib.sha256(_above_marker(doc.read_text()).encode()).hexdigest()
    if have != sha:
        raise SystemExit(f"refused: {doc} hashes to {have[:8]}, not {sha[:8]}")
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.match(rf"prereg\({ID}\): ([0-9a-f]{{8,64}})\b", subj.strip())
        if m and sha.startswith(m.group(1)):
            blob = git("show", f"{h}:{DOC_REL}")
            if hashlib.sha256(_above_marker(blob).encode()).hexdigest() == sha:
                return str(h)
    raise SystemExit(f"refused: no commit `prereg({ID}): {sha[:8]}` whose doc hashes to {sha[:8]}")


def _gap_row(c: pl.DataFrame, b: pl.DataFrame, games: list[str]) -> dict[str, Any]:
    """Frozen gap R_close / R_blow - 1 (non-garbage normalisation), game-clustered bootstrap
    (F12B's `_boot_ng`, B 2,000, seed 0)."""
    if not c.height or not b.height:
        return {"gap": None, "n_close": c.height, "n_blow": b.height}
    gap = F.ratio_ng(c) / F.ratio_ng(b) - 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        bs = F._boot_ng(c, b, games)
    finite = np.isfinite(bs)
    n_drop = int(
        (~finite).sum()
    )  # review m6: draws with an empty resampled cell (expected 0 on real windows)
    bs = bs[finite]
    lo, hi = float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))
    return {
        "gap": gap, "ci": [lo, hi], "half_width": (hi - lo) / 2.0,
        "n_boot": int(len(finite)), "n_boot_dropped_nonfinite": n_drop,
        "n_close": c.height, "n_blow": b.height,
        "games_close": c["game_id"].n_unique(), "games_blow": b["game_id"].n_unique(),
    }  # fmt: skip


def _mean_of_ratios(c: pl.DataFrame, b: pl.DataFrame) -> float | None:
    """Descriptive: per player-game (q4ng / A_g) / (q13 / 36), mean per cell (q13 > 0 rows),
    close / blow - 1."""
    r = [
        sub.filter(pl.col("q13") > 0)
        .select(((pl.col("q4ng") / pl.col("a_g")) / (pl.col("q13") / 36.0)).mean())
        .item()
        for sub in (c, b)
    ]
    return None if r[0] is None or not r[1] else float(r[0]) / float(r[1]) - 1.0


def window_premise(db: str, seasons: tuple[int, ...]) -> dict[str, Any]:
    df, gm = F.load(db, seasons)
    if int(df["season"].max()) > F.MAX_SEASON:  # type: ignore[arg-type]
        raise SystemExit("refused: season > 2024 loaded")
    games = sorted(gm["game_id"].to_list())
    d = df.filter(pl.col("a_g") >= F.MIN_A)
    cs = F.cells(d)
    gaps = {g: _gap_row(cs[(g, "close")], cs[(g, "blow")], games) for g in ("young", "vet")}
    p1 = gaps["vet"]["gap"] is not None and gaps["vet"]["gap"] >= F.BAND
    p2 = gaps["young"]["gap"] is not None and abs(gaps["young"]["gap"]) <= F.BAND
    desc: dict[str, Any] = {"note": "descriptive only; cannot rescue or fail the rule"}
    per: dict[str, Any] = {}
    for s in seasons:
        ds = d.filter(pl.col("season") == s)
        gs = sorted(gm.filter(pl.col("season") == s)["game_id"].to_list())
        c2 = F.cells(ds)
        per[str(s)] = {g: _gap_row(c2[(g, "close")], c2[(g, "blow")], gs) for g in ("young", "vet")}
    desc["per_season_unpowered"] = per
    desc["mean_of_ratios_gap"] = {
        g: _mean_of_ratios(cs[(g, "close")], cs[(g, "blow")]) for g in ("young", "vet")
    }
    ca = F.cells(df)
    desc["no_exclusion_gap"] = {
        g: _no_exclusion(ca[(g, "close")], ca[(g, "blow")]) for g in ("young", "vet")
    }
    mid = d.filter((pl.col("idx") >= 3) & (pl.col("idx") <= 4))
    close, blow = pl.col("margin") <= F.CLOSE_MAX, pl.col("margin") >= F.BLOW_MIN
    desc["idx_3_4_gap"] = _gap_row(mid.filter(close), mid.filter(blow), games)
    desc["young_starters_gap"] = _young_starters(db, d, games, seasons)
    desc["draft_year"] = _draft_year_note(db, df)
    return {
        "seasons": list(seasons),
        "games": len(games),
        "gaps": gaps,
        "P1": p1,
        "P2": p2,
        "descriptive": desc,
    }


def _no_exclusion(c: pl.DataFrame, b: pl.DataFrame) -> float | None:
    """Descriptive: F12's no-exclusion ratio 3 sum q4 / sum q13, close / blow - 1 (all rows)."""
    if not c.height or not b.height:
        return None
    rc = 3.0 * float(c["q4"].sum()) / float(c["q13"].sum())
    rb = 3.0 * float(b["q4"].sum()) / float(b["q13"].sum())
    return rc / rb - 1.0


def _draft_year_note(db: str, df: pl.DataFrame) -> dict[str, Any]:
    """Review m2: undrafted players carry a null draft_year by design (excluded from the idx
    cells, as F12 did); the threat the rule names is a DRAFTED player-game with a null year.
    Both shares are reported separately (descriptive)."""
    import duckdb

    out: dict[str, Any] = {
        "null_draft_year_share_all_player_games": float(
            cast(float, df["draft_year"].is_null().mean())
        ),
        "note": "undrafted players: null draft_year by design, excluded from the cells",
    }
    con = duckdb.connect(db, read_only=True)
    try:
        ps = con.execute(
            "SELECT DISTINCT b.player_id, p.draft_status FROM player_game_stats b "
            "JOIN games g USING (game_id) JOIN players_static p USING (player_id) "
            "WHERE g.season <= 2024"
        ).pl()
    except duckdb.Error as exc:
        out["draft_status"] = f"unavailable: {exc!r}"
        return out
    finally:
        con.close()
    j = df.select("player_id", "draft_year").join(ps, on="player_id", how="left")
    drafted = j.filter(pl.col("draft_status") == "drafted")
    out["drafted_player_games"] = drafted.height
    out["null_draft_year_share_of_drafted_player_games"] = (
        float(cast(float, drafted["draft_year"].is_null().mean())) if drafted.height else None
    )
    out["undrafted_share_of_player_games"] = (
        float(cast(float, (j["draft_status"] == "undrafted").mean())) if j.height else None
    )
    out["null_status_share_of_player_games"] = (
        float(cast(float, j["draft_status"].is_null().mean())) if j.height else None
    )
    return out


def _young_starters(
    db: str, d: pl.DataFrame, games: list[str], seasons: tuple[int, ...]
) -> dict[str, Any]:
    """Descriptive young-starter gap. Review B1: box rows are filtered at the source to the
    window's seasons (<= 2024)."""
    import duckdb

    lst = ", ".join(str(int(x)) for x in seasons)
    if max(seasons) > F.MAX_SEASON:
        raise SystemExit("refused: season > 2024 requested")
    con = duckdb.connect(db, read_only=True)
    try:
        st = con.execute(
            "SELECT b.game_id, b.player_id, b.starter FROM player_game_stats b "
            f"JOIN games g USING (game_id) WHERE g.season IN ({lst}) AND g.season <= 2024"
        ).pl()
    except duckdb.Error as exc:  # descriptive only
        return {"unavailable": repr(exc)}
    finally:
        con.close()
    y = d.join(st, on=["game_id", "player_id"], how="left").filter(
        (pl.col("idx") <= F.YOUNG_MAX_IDX) & pl.col("starter").fill_null(False).cast(pl.Boolean)
    )
    return _gap_row(
        y.filter(pl.col("margin") <= F.CLOSE_MAX), y.filter(pl.col("margin") >= F.BLOW_MIN), games
    )


def decide(select: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    """Frozen rule: SELECT P1 and P2 first (else FAIL, REPORT not computed); then the realised
    REPORT young half-width <= 0.05 (else INCONCLUSIVE, whatever P1 / P2 say: review m3, a label
    choice; both close F12); then REPORT P1 and P2 (else FAIL); else PASS."""
    if not (select["P1"] and select["P2"]):
        return {"verdict": "FAIL", "reason": "SELECT failed P1 or P2; REPORT not computed"}
    assert report is not None
    hw = report["gaps"]["young"].get("half_width")
    if hw is None or hw > PRECISION_MAX:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": f"REPORT young half-width {hw} > {PRECISION_MAX} (INCONCLUSIVE whatever "
            "P1 / P2 say; closes F12 as a FAIL does)",
            "report_P1": report["P1"],
            "report_P2": report["P2"],
        }
    if not (report["P1"] and report["P2"]):
        return {"verdict": "FAIL", "reason": "REPORT failed P1 or P2"}
    return {
        "verdict": "PASS",
        "reason": "P1 and P2 in both windows; precision guard holds (opens only F12B_MODEL)",
    }


def run_premise(sha: str, root: Path = Path("."), git: Any = _git) -> dict[str, Any]:
    commit = require_frozen(sha, root / DOC_REL, git)
    t0 = time.time()
    db_s, seasons_s = WINDOWS["select"]
    sel = window_premise(str(root / db_s), seasons_s)
    print(f"[{time.time() - t0:6.1f}s] SELECT done: P1 {sel['P1']} P2 {sel['P2']}", flush=True)
    rep = None
    if sel["P1"] and sel["P2"]:
        db_r, seasons_r = WINDOWS["report"]
        rep = window_premise(str(root / db_r), seasons_r)
        print(f"[{time.time() - t0:6.1f}s] REPORT done", flush=True)
    return {
        "rule": ID,
        "frozen_sha256": sha,
        "freeze_commit": commit,
        "select": sel,
        "report": rep if rep is not None else "not computed (SELECT failed)",
        **decide(sel, rep),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["gate", "premise"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--sha", default="")
    args = ap.parse_args(argv)
    res = run_gate() if args.stage == "gate" else run_premise(args.sha)
    text = json.dumps(F._round(res), indent=2, default=str)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"sha256 {hashlib.sha256(text.encode()).hexdigest()[:16]} -> {out}")
    if args.stage == "premise":
        print(f"verdict {res['verdict']}: {res['reason']}")
        return 0
    return 0 if res["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
