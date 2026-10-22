# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL scoring, summaries, verdict and the refusing entrypoint (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

``run`` refuses unless (1) the rule is frozen (a commit ``prereg(ARCHETYPE_MATCHUP_ALL): <sha256 of the text above the results marker>`` exists and
the text on disk hashes to it) AND (2) the harness is pinned (a later commit ``harness(ARCHETYPE_MATCHUP_ALL): <pin>`` and every file in
``archetype_matchup_all_rule.HARNESS_FILES`` byte-identical at both commits and on disk). Only then does it read an outcome. This file is the ONLY
module of the rule that names the outcome columns; the arm, the features and the gate receive outcomes as arguments or never see them.

Command the maintainer runs after the freeze and the pin commits (an 8 GB box; one process; checkpoints per season and stat):

    mkdir data/ops/heavy.lock && NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore \
        -m research.eval.archetype_matchup_all_eval run --sha <64-hex sha256 of the frozen text>; rmdir data/ops/heavy.lock
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_arm as arm
from research.eval import archetype_matchup_all_features as af
from research.eval import archetype_matchup_all_gate as gate
from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_gate as g6
from research.eval import role_prior_salary_eval as ev

MARKER = rule.MARKER
DOC = Path(__file__).resolve().parents[2] / "docs/prereg/ARCHETYPE_MATCHUP_ALL.md"
OUT_DIR = Path("reports/prereg_archetype_matchup_all")
OUTCOME_COLS = ["y", "minutes"]  # the one place the outcome columns are named
Y_COL, MINUTES_COL = OUTCOME_COLS


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def text_sha(text: str) -> str:
    return rule.text_sha(text)


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def require_frozen(sha: str | None, doc: Path = DOC) -> str:
    """Refuse unless the draft's text above the marker hashes to ``sha`` AND a commit titled ``prereg(ARCHETYPE_MATCHUP_ALL): <sha>`` exists."""
    if not sha:
        raise SystemExit(
            "ARCHETYPE_MATCHUP_ALL is a DRAFT: run refuses without --sha of a frozen rule"
        )
    if MARKER not in doc.read_text():
        raise SystemExit("rule text has no results marker: still a draft")
    if frozen_sha(doc) != sha:
        raise SystemExit("the text above the marker does not hash to --sha")
    if rule.freeze_commit(sha) is None:
        raise SystemExit(f"no commit 'prereg({rule.ID}): {sha}' found: not frozen")
    return sha


def require_ready(sha: str | None, doc: Path = DOC) -> dict[str, str]:
    """The single preflight before any outcome is read: frozen text AND pinned harness."""
    s = require_frozen(sha, doc)
    try:
        return rule.require_harness_pinned(s)
    except RuntimeError as e:
        raise SystemExit(f"harness not pinned: {e}") from e


# --------------------------------------------------------------------------- summaries and verdict (pure; exercised on synthetic data)


def season_summary(
    deltas: dict[str, dict[str, np.ndarray]],
    game: np.ndarray,
    ps: np.ndarray,
    week: np.ndarray,
) -> dict[str, Any]:
    """``deltas[arm][stat]`` = per-row (arm - A0) integer CRPS on the same rows. Everything in floor units. M's CM with the three clusterings
    (binding = widest); the shuffled mean and the paired CI of M minus the shuffled mean (descriptive); the knockouts and the identity-only arm."""
    rows = {a: rule.composite_rows({s: d[s] for s in rule.STATS}) for a, d in deltas.items()}
    m = ev.three_way(rows["M"], game, ps, week)
    parts = rule.stat_parts({s: deltas["M"][s] for s in rule.STATS})
    shuf_pts = [float(rows[f"shuffle{s}"].mean()) for s in rule.SHUFFLE_SEEDS]
    shuf_mean_rows = np.mean([rows[f"shuffle{s}"] for s in rule.SHUFFLE_SEEDS], axis=0)
    cm = float(rows["M"].mean())
    knock = {
        name: {
            "cm_point": float(rows[f"ko_{name}"].mean()),
            "keep_share": rule.keep_share(float(rows[f"ko_{name}"].mean()), cm),
        }
        for name in rule.KNOCKOUTS
    }
    ident = float(rows["identity_only"].mean())
    per_stat = {
        s: ev.three_way(deltas["M"][s] / rule.FLOORS[s], game, ps, week)["binding"]
        for s in rule.STATS
    }
    return {
        "n_rows": int(len(game)),
        "n_player_seasons": int(len(np.unique(ps))),
        "cm": m,
        "cm_point": cm,
        "stat_parts": parts,
        "stat_shares": rule.stat_shares(parts),
        "stat_points_floor_units": {s: parts[s] * len(rule.STATS) for s in rule.STATS},
        "per_stat_binding_ci_floor_units_descriptive": per_stat,
        "top5_share": ev.top5_share(rows["M"], ps),
        "shuffled_cm_points": shuf_pts,
        "shuffled_cm_mean": float(np.mean(shuf_pts)),
        "shuffled_keep_share": rule.keep_share(float(np.mean(shuf_pts)), cm),
        "m_minus_shuffled_mean_ci_descriptive": ev.three_way(
            rows["M"] - shuf_mean_rows, game, ps, week
        ),
        "knockouts": knock,
        "identity_only_cm_point": ident,
        "identity_only_keep_share": rule.keep_share(ident, cm),
    }


def final_verdict(
    select: dict[str, Any],
    report: dict[str, Any],
    planted_ok: bool,
    audit: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """The only claim, from the 2023 select summary and the 2024 report summary (``primary_verdict``)."""
    keeps = {name: report["knockouts"][name]["keep_share"] for name in rule.KNOCKOUTS}
    leak = rule.minutes_leak(audit, keeps)
    v = rule.primary_verdict(
        select["cm_point"],
        report["cm_point"],
        report["cm"]["binding"]["hi"],
        report["stat_parts"],
        report["top5_share"],
        report["shuffled_cm_points"],
        report["identity_only_cm_point"],
        planted_ok,
        leak["void"],
    )
    return {**v, "minutes_leak": leak}


# --------------------------------------------------------------------------- the real-data wiring (runs only behind require_ready)


def read_outcomes(stat: str, S: int, keys: pd.DataFrame) -> pd.DataFrame:
    lab = arch.read_rows(stat, S, ["game_id", "player_id", *OUTCOME_COLS])
    out = keys.merge(lab, on=["game_id", "player_id"], how="left", validate="one_to_one")
    assert len(out) == len(keys) and out[OUTCOME_COLS].notna().all().all()
    return out


def load_season(
    S: int,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    c: Any,
) -> dict[str, Any]:
    off_a, def_a, align_rep = af.build_aligned_archetypes(c, S)
    keys, lost = gate.common_keys(S)
    assert all(v == 0 for v in lost.values()), f"four-stat row sets differ in {S}: {lost}"
    pts_rows = arch.read_rows("pts", S, g6.ROW_COLS)
    sf, tables = af.season_frame(S, keys, pts_rows, mu, games, flagged, off_a, def_a)
    shuffled = {
        s: af.season_frame(S, keys, pts_rows, mu, games, flagged, off_a, def_a, shuffle_seed=s)[0]
        for s in rule.SHUFFLE_SEEDS
    }
    planted = gate.planted_future_full(
        sf, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a, c
    )
    q0: dict[str, np.ndarray] = {}
    y: dict[str, np.ndarray] = {}
    minutes = None
    for st in rule.STATS:
        q, _sub = gate.stat_inputs(st, S, keys)
        lab = read_outcomes(st, S, keys)
        q0[st] = q
        y[st] = lab[Y_COL].to_numpy(float)
        if st == "pts":
            minutes = lab[MINUTES_COL].to_numpy(float)
        log(f"season {S} {st}: grid {q.shape}")
    assert minutes is not None
    return {
        "season": S,
        "sf": sf,
        "shuffled": shuffled,
        "q0": q0,
        "outcome": y,
        "realised_minutes": minutes,
        "month": arm.month_key(sf.game_date),
        "game": sf.game_id.to_numpy(),
        "ps": np.array([f"{S}-{p}" for p in sf.player_id.to_numpy()]),
        "week": g6.weeks(sf, S),
        "alignment": align_rep,
        "planted_future": planted,
    }


def make_pool(stat: str, data: list[dict[str, Any]], score_season: int) -> arm.StatPool:
    cols = [*rule.FEATURES, "e_lg"]
    return arm.StatPool(
        stat=stat,
        q0=next(d for d in data if d["season"] == score_season)["q0"][stat],
        y=np.concatenate([d["outcome"][stat] for d in data]),
        mean=np.concatenate([arm.a0_mean(d["q0"][stat]) for d in data]),
        month=np.concatenate([d["month"] for d in data]),
        is_score=np.concatenate(
            [np.full(len(d["month"]), d["season"] == score_season) for d in data]
        ),
        x=pd.concat([d["sf"][cols] for d in data], ignore_index=True),
        x_shuffled={
            s: pd.concat([d["shuffled"][s][cols] for d in data], ignore_index=True)
            for s in rule.SHUFFLE_SEEDS
        },
    )


def score_season(
    S: int, data: list[dict[str, Any]], out_dir: Path
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    d = next(x for x in data if x["season"] == S)
    deltas: dict[str, dict[str, np.ndarray]] = {}
    for st in rule.STATS:
        ck = out_dir / f"deltas_{S}_{st}.npz"
        if ck.exists():
            z = np.load(ck)
            per = {k: z[k] for k in z.files}
            log(f"season {S} {st}: loaded checkpoint")
        else:
            pool = make_pool(st, [x for x in data if x["season"] <= S], S)
            per = arm.run_arms(pool, log=log)
            np.savez(ck, **per)  # type: ignore[arg-type]
            log(f"season {S} {st}: arms done, checkpoint written")
        for a, v in per.items():
            deltas.setdefault(a, {})[st] = v
    summ = season_summary(deltas, d["game"], d["ps"], d["week"])
    return deltas, summ


def run(sha: str | None, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    pins = require_ready(sha)
    t0 = time.time()
    rule.check_floors()
    out_dir.mkdir(parents=True, exist_ok=True)
    c = arch.connect(arch.DB)
    mu = feat.read_matchups(c)
    games = feat.read_games(c)
    flagged = feat.read_flagged(c, games)
    assert int(mu.season.max()) <= rule.MAX_SEASON and int(games.season.max()) <= rule.MAX_SEASON
    data = [load_season(S, mu, games, flagged, c) for S in (rule.SELECT, rule.REPORT)]
    planted_ok = all(d["planted_future"]["identical"] for d in data)
    res: dict[str, Any] = {
        "id": rule.ID,
        "harness_pins": pins,
        "planted_future": {str(d["season"]): d["planted_future"] for d in data},
        "alignment": {str(d["season"]): d["alignment"] for d in data},
    }
    _, sel = score_season(rule.SELECT, data, out_dir)
    res["select_2023"] = sel
    if sel["cm_point"] > rule.CM_PASS:
        res["report_2024"] = None
        res["verdict"] = {
            "pass": False,
            "failed": ["select_season_point"],
            "note": "2023 CM point above -1.0: 2024 is not scored (the rule's kill criterion)",
        }
    else:
        _, rep = score_season(rule.REPORT, data, out_dir)
        rd = next(d for d in data if d["season"] == rule.REPORT)
        audit = af.missingness_vs_realised(rd["sf"], rd["realised_minutes"])
        res["report_2024"] = rep
        res["missingness_vs_realised_minutes"] = audit
        res["verdict"] = final_verdict(sel, rep, planted_ok, audit)
    res["wall_seconds"] = round(time.time() - t0, 1)
    (out_dir / "results.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    c.close()
    return res


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--sha")
    a = ap.parse_args(argv)
    res = run(a.sha)
    print(json.dumps(res["verdict"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
