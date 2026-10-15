"""Hindsight fixture for top_parlays/2 (illustration true): one 2024-25 date from stored OOF rows.

Inputs: the MODEL_CLV T-60 pairs and the ODDS_HISTORY OOF rows (seasons 2023 and 2024 only; season
2025 is the frozen holdout and is refused). Each leg is a DraftKings main-line over or under priced
at its T-60 snapshot with the production out-of-fold forecast. The CAL map used is the harness's
walk-forward fit for that date's calendar-month block (rows strictly before the first of the
month, the rule's own block), so the map never sees the
slate's own outcomes. The output is a HINDSIGHT ILLUSTRATION: outcomes are attached afterwards, only
players who played appear (DNP legs were voided upstream), and nothing here is a forecast of a
future date.

    python -m nba.parlay.top_history --date 2025-01-27 --pairs-dir <dir> --model-dir <dir> \\
        --model <joint_model.json> --out app/fixtures/top_parlays_example.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.explain import names
from nba.parlay import line_cal as lc
from nba.parlay import line_cal_build as lb
from nba.parlay.config import load_config
from nba.parlay.joint import corr_from_json
from nba.parlay.top_parlays import (
    Gate,
    LegCand,
    guard_text,
    make_leg,
)
from nba.parlay.top_parlays import build_report as _build_report

BOOK = "draftkings"
HINDSIGHT_LABEL = (
    "Hindsight illustration, not a forecast: a 2024-25 regular-season date rebuilt from stored "
    "out-of-fold rows and DraftKings T-60 lines. Only players who played appear. Outcomes are "
    "attached after the fact."
)


def legs_for_day(
    d: pl.DataFrame, day: date, calmap: lc.CalMap, book: str = BOOK
) -> tuple[list[LegCand], dict[tuple[str, int, str], float]]:
    """Over and under legs of one date and book, plus the realised ``over`` label per leg key."""
    s = d.filter(
        (pl.col("game_date") == day)
        & (pl.col("book") == book)
        & pl.col("game_id").str.starts_with("002")
    )
    if s.is_empty():
        raise ValueError(f"no {book} T-60 rows on {day}")
    teams: dict[str, set[int]] = {}
    for gid, tid in s.select("game_id", "team_id").unique().iter_rows():
        teams.setdefault(gid, set()).add(int(tid))
    legs: list[LegCand] = []
    over_label: dict[tuple[str, int, str], float] = {}
    for stat in lb.STATS:
        st = s.filter(pl.col("stat") == stat)
        if st.is_empty():
            continue
        p_raw = np.clip(st["p_model"].to_numpy(), lc.EPS, 1 - lc.EPS)
        p_cal = calmap.apply_many(
            book, stat, st["p_model"].to_numpy(), st["line"].to_numpy(), st["med"].to_numpy(),
            st["spr"].to_numpy(), st["starter10"].to_numpy(),
        )  # fmt: skip
        for i, r in enumerate(st.iter_rows(named=True)):
            gid, pid, tid = r["game_id"], int(r["player_id"]), int(r["team_id"])
            others = sorted(teams[gid] - {tid})
            opp_id = others[0] if others else 0
            over_label[(gid, pid, stat)] = float(r["over"])
            for side, price in (("over", r["price_over"]), ("under", r["price_under"])):
                if price is None:
                    continue
                legs.append(
                    make_leg(
                        p_over_raw=float(p_raw[i]),
                        p_over_cal=float(p_cal[i]),
                        side=side,
                        game_id=gid,
                        tipoff=r["tipoff_utc"],
                        player_id=pid,
                        player_name=names.player_name(pid),
                        team_id=tid,
                        opp_id=opp_id,
                        team=names.team_abbr(tid),
                        opp=names.team_abbr(opp_id) if opp_id else "?",
                        stat=stat,
                        line=float(r["line"]),
                        book=book,
                        price_american=int(price),
                        price_at=r["snapshot_ts"],
                        model_source="production",
                        drivers={
                            "mean": round(float(r["mean_int"]), 2),
                            "recent_avg": round(float(r["m_recency"]), 2),
                            "minutes": round(float(r["min10"]), 1),
                            "minutes_kind": "recent",
                            "n_out": None if r["n_out_rot"] is None else int(r["n_out_rot"]),
                        },
                    )
                )
    return legs, over_label


def annotate_hindsight(
    report: dict[str, Any], over_label: dict[tuple[str, int, str], float]
) -> None:
    """Attach realised outcomes to every ticket (fixture only). A hit is annotated with the
    ticket's own EV; a miss gets no annotation, so the displayed odds read the same either way."""
    for tickets in report["sizes"].values():
        for t in tickets:
            hits = []
            for leg in t["legs"]:
                over = over_label[(leg["game_id"], leg["player_id"], leg["stat"])] > 0.5
                hits.append(over if leg["side"] == "over" else not over)
            hit = all(hits)
            note = None
            if hit:
                ev = t["ev_per_dollar"]
                lead = "This one hit; it was still a" if ev <= 0 else "This one hit; it was a"
                note, _ = guard_text(f"{lead} {100.0 * ev:+.0f}% bet.", t)
            t["hindsight"] = {"legs_hit": hits, "all_legs_hit": hit, "note": note}


def build_fixture(pairs_dir: Path, model_dir: Path, model_json: Path, day: date) -> dict[str, Any]:
    if day < date(2024, 10, 1) or day > date(2025, 6, 30):
        raise ValueError("fixture dates must be season-2024 (2024-10-01 .. 2025-06-30)")
    cfg = load_config()
    raw_model = model_json.read_bytes()
    blob = json.loads(raw_model)
    pc = corr_from_json(blob["corr"])
    joint_model = {
        "sha256": hashlib.sha256(raw_model).hexdigest(),
        "fit_seasons": blob.get("params", {}).get("fit_seasons"),
        "n_games_corr": blob.get("n_games_corr"),
    }
    d, shas = lb.load_history(pairs_dir, model_dir)
    block = day.replace(day=1)  # the rule's calendar-month block
    cells = lc.fit_cells(lb.to_arrays(d), np.datetime64(block, "D"), (BOOK,), lb.STATS)
    meta = {"rule": lc.RULE, "rule_sha8": lb.RULE_SHA8}
    calmap = lc.CalMap(meta, {(c.book, c.stat): c for c in cells})
    legs, over_label = legs_for_day(d, day, calmap)
    # each leg is stamped at its own T-60; the file's clock sits before the earliest tip
    made_at = min(leg.tipoff for leg in legs) - timedelta(minutes=1)
    calibration = {
        "rule": lc.RULE,
        "sha8": lb.RULE_SHA8,
        "fit_through": f"{(block - timedelta(days=1)).isoformat()} "
        "(the rule's month-block walk-forward for this date: rows strictly before the first of "
        "the month, seasons 2023-2024)",
        "status": "Stage 1 pass (T493); live confirmation pending. p_cal is the map applied to the "
        "production out-of-fold P(over); no AVG row exists for season 2024.",
    }
    rep = _build_report(
        legs,
        slate_date=day.isoformat(),
        made_at=made_at,
        now=made_at,
        cfg=cfg,
        pc=pc,
        calibration=calibration,
        gate=Gate(),
        illustration=True,
        joint_model=joint_model,
        notes=[
            "fixture: made_at is 1 minute before the first tip; legs priced after it are refused",
            "P(play) is not stored for out-of-fold rows; non-players were voided upstream",
            "the 'drivers.minutes' value is the last-10-games average, not a projection",
            "pair correlations were fitted on seasons 2022-2024, which includes this date's season "
            "(in-sample, negligible for the shown tickets)",
        ],
    )
    annotate_hindsight(rep, over_label)
    rep["fixture"] = {
        "kind": "hindsight_illustration",
        "label": HINDSIGHT_LABEL,
        "slate_season": 2024,
        "inputs_sha256": shas,
    }
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--pairs-dir", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument(
        "--model", required=True, help="data/parlay/joint_model.json (pair correlations)"
    )
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rep = build_fixture(
        Path(a.pairs_dir), Path(a.model_dir), Path(a.model), date.fromisoformat(a.date)
    )
    Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=False, default=str) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
