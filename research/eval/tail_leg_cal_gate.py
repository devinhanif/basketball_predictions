# ruff: noqa: E501
"""TAIL_LEG_CAL minimum-data gate (non-negotiable 8): counts, widths and power, measured on the real legs.

    <python> -m research.eval.tail_leg_cal_gate --data-root <main checkout> [--draws 40]

Output ``reports/prereg_tail_leg_cal/gate.json``. No hit rate, gap or loss of any real leg is computed: the real outcomes enter only through
LINE_CALIBRATION's own walk-forward that produces q_cal (the frozen map; its T493 / T495 outputs are public). Widths and power use synthetic labels
drawn from a declared truth: ``smooth`` (gap = -0.5 (q - 0.60), the main oracle), ``step`` (T495's published tail gaps, DraftKings pooled 2023-24:
-0.007 on [0.60, 0.65), -0.040 at q >= 0.65) and ``calibrated``. The only outcome-derived input is that published gap, quoted from
docs/reviews/T495_post_result_2026-10-15.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import parlay_joint_cal_eval as pj
from research.eval import parlay_joint_cal_gate as pgate
from research.eval import tail_leg_cal_eval as ev
from research.eval import tail_leg_cal_rule as rule

OUT = Path("reports/prereg_tail_leg_cal/gate.json")
B_SIM = 1000


def now() -> str:
    return subprocess.run(["date"], capture_output=True, text=True, check=False).stdout.strip()


def other_book_audit(root: Path) -> dict[str, Any]:
    """Non-negotiable 8: the tail counts a CAL fit by the same LINE_CALIBRATION procedure would give on FanDuel (not part of the frozen CAL,
    which fit DraftKings and Pinnacle only). Counts by q band only; the hit column is dropped before anything is computed."""
    old = pj.BOOKS
    pj.BOOKS = ("fanduel",)  # type: ignore[assignment]
    try:
        lab, ident = pj.load_cal(root, True, None)
        legs, _ = pj.book_legs(root, "fanduel", lab, ident)
    finally:
        pj.BOOKS = old
    out: dict[str, Any] = {}
    for s in ev.SEASONS:
        q = legs[s]["q_cal"].to_numpy().astype(float)
        out[str(s)] = {
            "legs": int(len(q)),
            "ge60": int((q >= ev.KNEE).sum()),
            "ge65": int((q >= ev.HIGH).sum()),
            "games": int(legs[s]["game_id"].n_unique()),
        }
    return out


def lead_minutes(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for b in ev.BOOKS:
        for s in ev.SEASONS:
            _, dg = pgate.load_legs(root, s, b)
            out[f"{b}|{s}"] = {"min": dg["lead_min_minutes"], "median": dg["lead_median_minutes"]}
    return out


def widths(d: dict[str, Any], scored: np.ndarray, cl: ev.Clusters) -> dict[str, Any]:
    q = d["q"]
    out: dict[str, Any] = {}
    y = ev.synthetic_hit(d, "calibrated", [2026, 77])
    for name, m in (
        ("r60", scored),
        ("r65", scored & (q >= ev.HIGH)),
        ("r60_65", scored & (q < ev.HIGH)),
    ):
        s = cl.summary(m, y - q)
        out[name] = {
            "n": s["n"],
            "half_width_union": (s["hi"] - s["lo"]) / 2,
            "half_width_game": (s["game"][1] - s["game"][0]) / 2,
            "half_width_date": (s["date"][1] - s["date"][0]) / 2,
            "games": int(len(np.unique(d["game"][m]))),
            "dates": int(len(np.unique(d["date"][m]))),
            "binomial_half_width": float(
                1.96 * np.sqrt(np.mean(q[m] * (1 - q[m])) / max(int(m.sum()), 1))
            ),
        }
    return out


def power(d: dict[str, Any], scored: np.ndarray, draws: int, truth_name: str) -> dict[str, float]:
    """Under the oracle truth, with S synthetic label draws: how often can each word be reached, by a PERFECT map (q' = truth) and by TAIL_H."""
    q = d["q"]
    cl = ev.Clusters(d["game"], d["date"], d["season"], b=B_SIM)
    truth = ev.truth_prob(q, truth_name)
    r60, r65 = scored, scored & (q >= ev.HIGH)
    acc: dict[str, list[float]] = {}

    def put(k: str, v: float) -> None:
        acc.setdefault(k, []).append(float(v))

    for k in range(draws):
        rng = np.random.default_rng([2026, 555, k])  # same draws for every truth
        y = (rng.random(len(q)) < truth).astype(float)
        wf = ev.walk_forward_tail(d["date"], q, y)
        if not np.array_equal(wf["scored"], scored):
            raise RuntimeError("scored mask depends on labels")
        for tag, arm in (("perfect", truth), ("hinge", wf["arms"]["TAIL_H"])):
            g60 = cl.summary(r60, y - arm)
            g65 = cl.summary(r65, y - arm)
            l60 = cl.summary(r60, ev.ll(arm, y) - ev.ll(q, y))
            l65 = cl.summary(r65, ev.ll(arm, y) - ev.ll(q, y))
            put(f"{tag}_gap65_interval_includes_0", g65["lo"] <= 0 <= g65["hi"])
            put(f"{tag}_gap65_point_within_limits", ev.TOP_GAP_LO <= g65["point"] <= ev.TOP_GAP_HI)
            put(
                f"{tag}_P1_gap60",
                abs(g60["point"]) <= ev.GAP_LIMIT
                and g60["lo"] >= -ev.GAP_MARGIN
                and g60["hi"] <= ev.GAP_MARGIN,
            )
            put(
                f"{tag}_P2_ll60_floor_and_p",
                l60["point"] <= ev.FLOOR_LL and l60["p_neg"] < ev.ALPHA_ONE,
            )
            put(
                f"{tag}_P2b_ll65_floor_and_p",
                l65["point"] <= ev.FLOOR_LL_65 and l65["p_neg"] < ev.ALPHA_ONE,
            )
            put(f"{tag}_ll60_point", l60["point"])
            put(f"{tag}_ll60_half_width", (l60["hi"] - l60["lo"]) / 2)
            put(f"{tag}_ll65_point", l65["point"])
            put(f"{tag}_ll65_half_width", (l65["hi"] - l65["lo"]) / 2)
            put(f"{tag}_gap65_point", g65["point"])
        gc65 = cl.summary(r65, y - q)
        gc60 = cl.summary(r60, y - q)
        put("CAL_gap65_interval_excludes_0", gc65["hi"] < 0 or gc65["lo"] > 0)
        put("CAL_gap60_interval_excludes_0", gc60["hi"] < 0 or gc60["lo"] > 0)
        put(
            "CAL_gap60_passes_P1_itself",
            abs(gc60["point"]) <= ev.GAP_LIMIT
            and gc60["lo"] >= -ev.GAP_MARGIN
            and gc60["hi"] <= ev.GAP_MARGIN,
        )
        put("CAL_gap60_point", gc60["point"])
        if (k + 1) % 10 == 0:
            ev._log(f"power draw {k + 1}/{draws}")
    return {k: float(np.mean(v)) for k, v in acc.items()}


def word_rates(legs: dict[str, dict[str, Any]], draws: int, truth_name: str) -> dict[str, Any]:
    """The word-level operating characteristics: the harness's own score_book and evaluate on synthetic labels drawn from the truth, both books,
    k_null 0 and the TOP slice off (it is a label); B = 1,000 resamples."""
    cl = {
        b: ev.Clusters(legs[b]["game"], legs[b]["date"], legs[b]["season"], b=B_SIM)
        for b in ev.BOOKS
    }
    words: dict[str, int] = {}
    betas: list[float] = []
    for k in range(draws):
        sc: dict[str, Any] = {}
        for bi, b in enumerate(ev.BOOKS):
            q = legs[b]["q"]
            y = (
                np.random.default_rng([4242, k, bi]).random(len(q)) < ev.truth_prob(q, truth_name)
            ).astype(float)
            sc[b] = ev.score_book(legs[b], y, cl[b], 0, b, top=False)
        e = ev.evaluate(sc[ev.PRIMARY], sc["pinnacle"])
        words[e["word"]] = words.get(e["word"], 0) + 1
        betas.append(e["beta_median"])
        if (k + 1) % 10 == 0:
            ev._log(f"word draw {k + 1}/{draws} ({truth_name})")
    pos = words.get("TAIL_FIXES", 0) + words.get("TAIL_GAP_FIXED_LL_NOT_SHOWN", 0)
    return {
        "draws": draws,
        "words": words,
        "P_positive": pos / draws,
        "beta_median_p5_p50_p95": [float(np.percentile(betas, v)) for v in (5, 50, 95)],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--draws", type=int, default=40)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    root = Path(a.data_root)
    start = now()
    pre = ev.preflight(root)
    legs, _hits = ev.legs_prefit(root)
    del _hits  # the gate never touches a leg outcome
    res: dict[str, Any] = {
        "date_cdt": start,
        "preflight_counts": pre["counts"],
        "published_equal": pre["published_equal"],
        "pins": {rel: rule.file_sha256(root / rel) for rel in ev.INPUT_PINS},
        "constants": {
            "KNEE": ev.KNEE,
            "HIGH": ev.HIGH,
            "MIN_TAIL": ev.MIN_TAIL,
            "HALF_LIFE": ev.HALF_LIFE,
        },
    }
    res["lead_minutes_t60"] = lead_minutes(root)
    res["audit_fanduel_same_procedure_tail_counts"] = other_book_audit(root)
    books: dict[str, Any] = {}
    for b in ev.BOOKS:
        d = legs[b]
        q = d["q"]
        wf = ev.walk_forward_tail(
            d["date"], q, np.zeros(len(q))
        )  # label-free: the scored mask depends on q and dates only
        scored = wf["scored"]
        ok_blocks = [r["block"] for r in wf["blocks"] if not r["identity"]]
        cl = ev.Clusters(d["game"], d["date"], d["season"])
        t = ev.truth_prob(q, "smooth")
        with np.errstate(divide="ignore", invalid="ignore"):
            kl = np.where(t != q, t * np.log(t / q) + (1 - t) * np.log((1 - t) / (1 - q)), 0.0)
        in_blk = np.isin(d["date"].astype("datetime64[M]").astype(str), ok_blocks)
        info: dict[str, Any] = {
            "tail_legs": int((q >= ev.KNEE).sum()),
            "scored_tail_legs": int(scored.sum()),
            "scored_ge65": int((scored & (q >= ev.HIGH)).sum()),
            "dropped_identity_tail_legs": int(((q >= ev.KNEE) & ~scored).sum()),
            "identity_blocks": [r["block"] for r in wf["blocks"] if r["identity"]],
            "blocks_fit": [
                (r["block"], r["n_fit"], r["n_score"]) for r in wf["blocks"] if not r["identity"]
            ],
            "by_season": {
                str(s): {
                    "scored_tail": int((scored & (d["season"] == s)).sum()),
                    "scored_ge65": int((scored & (d["season"] == s) & (q >= ev.HIGH)).sum()),
                }
                for s in ev.SEASONS
            },
            "by_stat_scored_tail": {
                st: int((scored & (d["stat"] == st)).sum()) for st in ("pts", "reb", "ast", "fg3m")
            },
            "by_side_scored_tail": {
                "over": int((scored & d["over"]).sum()),
                "under": int((scored & ~d["over"]).sum()),
            },
            "widths_synthetic_calibrated": widths(d, scored, cl),
            "oracle_gain_perfect_map": {
                "r60": float(np.mean(kl[scored])),
                "r65": float(np.mean(kl[scored & (q >= ev.HIGH)])),
                "all_legs_in_scored_blocks": float(np.mean(kl[in_blk])),
            },
        }
        if b == ev.PRIMARY:
            ev._log(f"power simulation {b} ({a.draws} draws)")
            info["power"] = {
                tn: power(d, scored, a.draws, tn) for tn in ("smooth", "step", "calibrated")
            }
        books[b] = info
    res["books"] = books
    res["word_level"] = {
        tn: word_rates(legs, a.draws, tn) for tn in ("smooth", "step", "calibrated", "flat")
    }
    res["date_cdt_end"] = now()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    ev._log(f"wrote {out}  start {start}  end {res['date_cdt_end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
