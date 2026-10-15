"""ALT_LADDER_HIST run harness (rule: docs/prereg/ALT_LADDER_HIST.md, FROZEN, sha ef316f3d...).

    .venv/bin/python -W ignore -m research.eval.alt_ladder_hist_eval \
        --data-root <checkout holding data/, reports/ and nba.duckdb> [--check-only]

Scores exactly what the frozen rule states, on the stored alternate rungs of DraftKings (primary),
FanDuel and Fanatics (replicates) at ``t60``, seasons 2023 and 2024 only. It REFUSES to run unless
(the rule's "Scoring" paragraph plus the pins of the other two harnesses):
* the rule text hashes to the pinned frozen sha, ``prereg(ALT_LADDER_HIST): <sha>`` is in history
  exactly once as a distinct sha, and the status line says FROZEN;
* that sha is the one the ALT_LINE_70 result recorded (``reports/alt_line_70/results.json``);
* ``alt_ladder_hist_gate.py`` and ``alt_line_70_gate.py`` hash to the values recorded at the freeze,
  the gate json hashes to the quoted sha, and the five input files match the gate's input shas;
* the odds store fingerprint equals the pinned one (fails closed; ``ODDS_FINGERPRINT`` of the
  ALT_LINE_70 harness);
* every scored row has ``snapshot_ts`` and ``last_update`` at or before ``tipoff_utc - 60 min``;
* the window and lead-cap game counts, the ladder-integrity exclusion counts and the primary-arm
  P >= 0.70 rung counts equal the gate tables (``gate.json``).
``--check-only`` runs all of that WITHOUT reading ``y``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import alt_harness_common as hc
from research.eval import alt_ladder_hist_gate as ag
from research.eval.alt_line_70_eval import (
    CAL_MARGIN,
    CAL_POINT,
    ODDS_DB,
    ODDS_FINGERPRINT,
    Boots,
    add_indices,
    break_even_table,
    calibration_word,
    model_word,
    money_word,
    odds_fingerprint,
    plan_block,
    stat_word,
    with_outcome,
)
from research.eval.alt_line_70_gate import (
    BAND,
    BINS,
    DIST_EDGES,
    DIST_UNIT,
    EPS,
    MAX_SEASON,
    SEASONS,
    SECONDARY_BINS,
    STATS,
    THRESHOLD,
    best_price,
    pool_summaries,
    survival_matrix,
)

RULE_ID = "ALT_LADDER_HIST"
RULE_PATH = "docs/prereg/ALT_LADDER_HIST.md"
LINE70_RULE_ID = "ALT_LINE_70"
GATE_JSON = "reports/prereg_alt_ladder_hist/gate.json"
GATE_JSON_SHA = "005919d445e67edc73ee3b95978238800041a0ebea037f9df6ac01beb8f7b750"
LINE70_RESULT = "reports/alt_line_70/results.json"
CODE_SHA = {
    "research/eval/alt_ladder_hist_gate.py": (
        "3297ec8c94ecaf66449a16a3c17442b0023780c3df4949a4b36e35264b03b1ee"
    ),
    "research/eval/alt_line_70_gate.py": (
        "8da31748003500f52f219ccc8c7bea71ee5ee91d56e30bc966112433e6413277"
    ),
}
INPUT_FILES = ag.INPUT_FILES
OUT_DEFAULT = "reports/alt_ladder_hist/results.json"
PRIMARY = "draftkings"
REPLICATES = ("fanduel", "fanatics")
P1_GATE = {"games": ag.P1_MIN_GAMES, "rows": ag.P1_MIN_ROWS, "hw": ag.P1_MAX_HW}
P2_GATE = {"rungs": ag.P2_MIN_RUNGS, "games": ag.P2_MIN_GAMES, "hw": ag.P2_MAX_HW}
ROI_FLOOR = 0.02
TAIL = ag.TAIL_BINS
DIST_MIN_ROWS, DIST_MIN_GAMES = 1_000, 300

UNSTATED = [
    "Part 1 cells are rung-level: one row per (game, player, stat, point) of the book's primary "
    "arm; gap = mean(hit - P); bootstrap clusters are games and dates drawn once per season "
    "(default_rng(2026) / default_rng(2027), B = 5,000) and shared by every cell, bin, stat, arm "
    "and book; decision interval = union of the percentile intervals; p = max of the two.",
    "Part 1S cells are pooled over both seasons per (bin, stat); CALIBRATED needs only the pooled "
    "cell, OVERCONFIDENT / UNDERCONFIDENT also need the same per-season sign (rule, N5).",
    "A cell failing its minimum-data gate gets 'NO WORD (gate)'. By-stat words are not gated.",
    "The model main line for the distance slice is the half-point line with P closest to 0.5 (ties "
    "to the lower line); distance = (main line - L) / u, u = 2 for pts and 1 otherwise.",
    "Arms: primary = no window game, lead <= 66 min, no excluded rung/ladder; S1 = window games "
    "included; S2 = no lead cap; S3 = excluded rungs and ladders added back. Each differs from the "
    "primary in one respect only.",
    "A fresh default_rng(2026) per plan (arm A, then each bin), seasons ascending, weeks "
    "ascending.",
    "Attack (ix) (FanDuel first listing from the raw files) and the quote-age part of (v) beyond "
    "null counts are not automated; they are run by hand only if a word is PROFITABLE.",
]


# ------------------------------------------------------------------------------ loading


def load_model_rows(data_root: Path, season: int, with_y: bool) -> pl.DataFrame:
    """All stored rows (played only) as the gate read them; ``y`` only when asked."""
    assert season <= MAX_SEASON
    cols = ["game_id", "player_id", "stat", "game_date", "starter10", "p_ge"]
    if with_y:
        cols.append("y")
    m = pl.read_parquet(
        data_root / f"reports/odds_history/model_rows_{season}.parquet", columns=cols
    )
    if not with_y:
        assert "y" not in m.columns
    return m


def attach_model_columns(t: pl.DataFrame, model: pl.DataFrame) -> pl.DataFrame:
    """Per rung: the model main-line index ``k_main`` (P closest to 0.5, ties lower) and starter."""
    parts: list[pl.DataFrame] = []
    for st in STATS:
        d = model.filter(pl.col("stat") == st)
        surv = survival_matrix(d["p_ge"].to_list())
        km = np.argmin(np.abs(surv - 0.5), axis=1) + 1
        starter = d["starter10"].fill_null(0.0).to_numpy() >= 0.5
        sub = t.filter(pl.col("stat") == st)
        mi = sub["mi"].to_numpy()
        parts.append(
            sub.with_columns(pl.Series("k_main", km[mi]), pl.Series("starter", starter[mi]))
        )
    return pl.concat(parts)


def build_season(
    data_root: Path, season: int, tips: pl.DataFrame, stamps: pl.DataFrame, with_y: bool
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    """All books' alternate over rungs of a season with the exclusion flags, the model rows and a
    meta dict of the counts the gate recorded."""
    odds_db = data_root / ODDS_DB
    model = load_model_rows(data_root, season, with_y)
    raw = ag.load_alt_quotes(odds_db, season)
    t, meta = ag.alt_over_rungs(raw, tips, model, ag.load_main_lines(odds_db, season))
    snap = (
        raw.group_by("game_id")
        .agg(pl.col("snapshot_ts").max().alias("snapshot_ts"))
        .join(tips, on="game_id", how="inner")
    )
    wg = ag.window_games(stamps, snap.select("game_id", "tipoff_utc", "snapshot_ts"))
    glead = (snap["tipoff_utc"] - snap["snapshot_ts"]).dt.total_seconds().to_numpy() / 60.0
    meta["games_with_alt_quotes"] = int(snap.height)
    meta["games_with_report_stamp_in_window"] = wg.height
    meta["games_with_lead_gt_66"] = int((glead > ag.LEAD_CAP_MIN).sum())
    t = t.with_columns(pl.col("game_id").is_in(wg["game_id"].to_list()).alias("in_window"))
    t = attach_model_columns(t, model)
    game_ids = np.sort(model["game_id"].unique().to_numpy())
    dates = np.sort(model["game_date"].unique().to_numpy().astype("datetime64[D]"))
    t = add_indices(t, game_ids, dates)
    if with_y:
        t = with_outcome(t, model)
        t = t.with_columns(
            (pl.col("hit").cast(pl.Float64) - pl.col("pm")).alias("res"),
            pl.col("hit").cast(pl.Float64).alias("hitf"),
        )
    return t, model, meta


def arms_of(t: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """The primary arm and the four sensitivity arms (each differs in one respect)."""
    cap = pl.col("lead_min") <= ag.LEAD_CAP_MIN
    ok = ~pl.col("in_window")
    keep = ~pl.col("ladder_off")
    return {
        "primary": t.filter(ok & cap & keep),
        "S1_window_games_included": t.filter(cap & keep),
        "S2_no_lead_cap": t.filter(ok & keep),
        "S3_excluded_added_back": t.filter(ok & cap),
        "window_games_only": t.filter(~ok & cap & keep),
    }


# ------------------------------------------------------------------------------ cells


def rows_of(per: dict[int, pl.DataFrame], mask: pl.Expr) -> dict[int, pl.DataFrame]:
    return {s: df.filter(mask) for s, df in per.items()}


def band_expr(lo: float, hi: float) -> pl.Expr:
    return (pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi - EPS)


def rung_cell(
    rows: dict[int, pl.DataFrame], bx: Boots, seasons: list[int] | None = None
) -> dict[str, Any]:
    """Calibration cell on rungs: n, games, hit rate, mean P, gap with the union interval and p,
    conservative half-width (pooled across seasons), per-season gaps, break-even prices."""
    seasons = [s for s in (seasons or sorted(rows)) if s in rows]
    frames = {s: rows[s] for s in seasons}
    n = sum(f.height for f in frames.values())
    out: dict[str, Any] = {"line_rows": n, "games": 0}
    if n == 0:
        return out
    cells = [ag.p1_cell(f) for f in frames.values() if f.height]
    pooled = ag.pool_p1(cells)
    out["games"] = int(pooled["games"])
    out["halfwidth_cons"] = pooled["halfwidth_cons"]
    allf = pl.concat([f for f in frames.values() if f.height])
    mean_p = float(allf["pm"].mean())  # type: ignore[arg-type]
    gap = bx.ratio(frames, "res", seasons)
    hit = bx.ratio(frames, "hitf", seasons)
    out.update(
        {
            "mean_p": mean_p,
            "hit_rate": hit["point"],
            "hit_rate_interval": [hit["lo"], hit["hi"]],
            "gap": gap["point"],
            "lo": gap["lo"],
            "hi": gap["hi"],
            "p_two_sided": gap["p_two_sided"],
            "break_even": hc.break_even(mean_p, hit["point"]),
            "by_season_gap": {
                str(s): float(f["res"].mean())  # type: ignore[arg-type]
                for s, f in frames.items()
                if f.height
            },
        }
    )
    return out


def secondary_word(cell: dict[str, Any], q: float) -> str:
    """Part 1S words: CALIBRATED on the pooled cell; OVERCONFIDENT / UNDERCONFIDENT also need the
    same sign in each season and BH q < 0.05."""
    if (
        cell["games"] < P1_GATE["games"]
        or cell["line_rows"] < P1_GATE["rows"]
        or cell.get("halfwidth_cons", 9.0) > P1_GATE["hw"]
    ):
        return "NO WORD (gate)"
    gap, lo, hi = cell["gap"], cell["lo"], cell["hi"]
    signs = {np.sign(v) for v in cell["by_season_gap"].values() if v != 0}
    same = len(signs) <= 1
    if abs(gap) <= CAL_POINT and lo >= -CAL_MARGIN and hi <= CAL_MARGIN:
        return "CALIBRATED"
    if gap < -CAL_POINT and hi < 0 and same and q < 0.05:
        return "OVERCONFIDENT"
    if gap > CAL_POINT and lo > 0 and same and q < 0.05:
        return "UNDERCONFIDENT"
    return "INCONCLUSIVE"


def part1(per: dict[int, pl.DataFrame], bx: Boots, label: str) -> dict[str, Any]:
    """Headline band cells (8, BH over 8), reliability bins, distance, tail, starter / bench,
    and the secondary Part 1S (16 pooled cells, BH over 16)."""
    out: dict[str, Any] = {"book": label, "headline": {}, "bins": {}, "distance": {}, "tail": {}}
    keys: list[tuple[int, str]] = []
    cells: list[dict[str, Any]] = []
    for s in sorted(per):
        for st in STATS:
            m = (pl.col("stat") == st) & band_expr(*BAND)
            keys.append((s, st))
            cells.append(rung_cell(rows_of(per, m), bx, [s]))
    qs = hc.bh_q([c.get("p_two_sided", 1.0) for c in cells])
    words: dict[tuple[int, str], str] = {}
    for (s, st), c, q in zip(keys, cells, qs, strict=True):
        c["bh_q"] = q
        c["word"] = calibration_word(c, q, P1_GATE) if c["line_rows"] else "NO WORD (gate)"
        words[(s, st)] = c["word"]
        out["headline"].setdefault(str(s), {})[st] = c
    if len(per) == 2:
        sw = {st: stat_word(words[(2023, st)], words[(2024, st)]) for st in STATS}
        out["stat_words"] = sw
        mw = model_word(sw)
        out["model_word"] = mw.replace("70%+", "70%+ on posted DraftKings rungs", 1)
    for s in sorted(per):
        for st in STATS:
            base = pl.col("stat") == st
            d1 = out["bins"].setdefault(str(s), {}).setdefault(st, {})
            for lo, hi in BINS:
                lab = f"{lo:.2f}-{min(hi, 1.0):.2f}"
                d1[lab] = rung_cell(
                    rows_of(per, base & (pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi)), bx, [s]
                )
            d1["P=1.0"] = rung_cell(rows_of(per, base & (pl.col("pm") >= 1 - EPS)), bx, [s])
            unit = DIST_UNIT[st]
            dd = out["distance"].setdefault(str(s), {}).setdefault(st, {})
            dist = (pl.col("k_main") - (pl.col("point") + 0.5)) / unit
            for a, b in DIST_EDGES:
                lab = f"({a:g},{b:g}]" if a > -1e8 else f"(-inf,{b:g}]"
                c = rung_cell(
                    rows_of(per, base & band_expr(*BAND) & (dist > a) & (dist <= b)), bx, [s]
                )
                c["insufficient"] = bool(
                    c["line_rows"] < DIST_MIN_ROWS or c["games"] < DIST_MIN_GAMES
                )
                dd[lab] = c
            tt = out["tail"].setdefault(str(s), {}).setdefault(st, {})
            for lab, lo, hi in TAIL:
                tt[lab] = rung_cell(rows_of(per, base & band_expr(lo, hi)), bx, [s])
            band = base & band_expr(*BAND)
            out.setdefault("starter_bench", {}).setdefault(str(s), {})[st] = {
                "starter": rung_cell(rows_of(per, band & pl.col("starter")), bx, [s]),
                "bench": rung_cell(rows_of(per, band & ~pl.col("starter")), bx, [s]),
            }
    sec_keys: list[tuple[str, str]] = []
    sec: list[dict[str, Any]] = []
    for lab, lo, hi in SECONDARY_BINS:
        for st in STATS:
            sec_keys.append((lab, st))
            sec.append(rung_cell(rows_of(per, (pl.col("stat") == st) & band_expr(lo, hi)), bx))
    q16 = hc.bh_q([c.get("p_two_sided", 1.0) for c in sec])
    out["secondary"] = {}
    for (lab, st), c, q in zip(sec_keys, sec, q16, strict=True):
        c["bh_q"] = q
        c["word"] = secondary_word(c, q) if c["line_rows"] else "NO WORD (gate)"
        out["secondary"].setdefault(lab, {})[st] = c
    return out


# ------------------------------------------------------------------------------ Part 2


def gate_roi(per: dict[int, pl.DataFrame]) -> dict[str, Any]:
    """The rule's Part 2 gate: pooled rungs, games and conservative ROI half-width."""
    parts = [ag.summarize(df) for _, df in sorted(per.items()) if df.height]
    pooled: dict[str, Any] = pool_summaries(parts) if parts else {"rungs": 0}
    pooled["ok"] = bool(
        pooled.get("rungs", 0) >= P2_GATE["rungs"]
        and pooled.get("games", 0) >= P2_GATE["games"]
        and pooled.get("roi_halfwidth_cons", 9.0) <= P2_GATE["hw"]
    )
    return pooled


def part2(per: dict[int, pl.DataFrame], bx: Boots) -> dict[str, Any]:
    """Arm A (P >= 0.70), by stat (BH over 4), the break-even table, E2, and the four P bins."""
    arm = rows_of(per, pl.col("pm") >= THRESHOLD - EPS)
    g = gate_roi(arm)
    e1 = bx.roi(arm)
    out: dict[str, Any] = {
        "arm_A": {
            "gate": g,
            "E1": e1,
            "E1u": bx.roi(arm, unit_mean=True),
            "word": money_word(e1, g["ok"]),
        },
        "break_even_by_p_bin": break_even_table(arm, bx),
        "E2_plan": plan_block(arm),
    }
    by_stat = {st: bx.roi(rows_of(arm, pl.col("stat") == st)) for st in STATS}
    qs = hc.bh_q([by_stat[st].get("p_two_sided", 1.0) for st in STATS])
    out["by_stat"] = {}
    for st, q in zip(STATS, qs, strict=True):
        r = by_stat[st]
        r["bh_q"] = q
        r["word"] = money_word(r, True, bh_ok=q < 0.05)
        out["by_stat"][st] = r
    sec: dict[str, Any] = {}
    cells: list[dict[str, Any]] = []
    for lab, lo, hi in SECONDARY_BINS:
        rows = rows_of(per, band_expr(lo, hi))
        cell: dict[str, Any] = {
            "gate": gate_roi(rows),
            "E1": bx.roi(rows),
            "E1u": bx.roi(rows, unit_mean=True),
            "break_even": break_even_table(rows, bx),
            "E2_plan": plan_block(rows),
        }
        sec[lab] = cell
        cells.append(cell)
    qb = hc.bh_q([c["E1"].get("p_le0", 1.0) for c in cells])
    for cell, q in zip(cells, qb, strict=True):
        cell["bh_q"] = q
        cell["word"] = money_word(cell["E1"], cell["gate"]["ok"], bh_ok=q < 0.05)
    out["secondary"] = sec
    return out


def comparators(
    prim: dict[int, pl.DataFrame], all_books: dict[int, pl.DataFrame], bx: Boots
) -> dict[str, Any]:
    """C1 price lean (raw implied 1/d >= 0.70, model ignored), C2 all primary-arm rungs, arm B
    (best over price across books at the exact rung, same eligibility)."""
    c1 = rows_of(prim, (1.0 / pl.col("dec")) >= THRESHOLD - EPS)
    arm_b = {
        s: best_price(df.filter(pl.col("pm") >= THRESHOLD - EPS)) for s, df in all_books.items()
    }
    books_per_rung = 0.0
    n_rung = 0
    for df in all_books.values():
        e = df.filter(pl.col("pm") >= THRESHOLD - EPS)
        if e.height:
            cnt = e.group_by("game_id", "player_id", "stat", "point").agg(pl.col("book").n_unique())
            books_per_rung += float(cnt["book"].sum())
            n_rung += cnt.height
    return {
        "C1_price_lean": bx.roi(c1),
        "C2_all_rungs": bx.roi(prim),
        "arm_B": {
            "E1": bx.roi(arm_b),
            "rungs": sum(df.height for df in arm_b.values()),
            "mean_books_posting_the_rung": books_per_rung / n_rung if n_rung else None,
        },
    }


def book_arms(
    t_by_season: dict[int, pl.DataFrame], book: str
) -> dict[str, dict[int, pl.DataFrame]]:
    arms: dict[str, dict[int, pl.DataFrame]] = {}
    for s, t in t_by_season.items():
        for name, df in arms_of(t.filter(pl.col("book") == book)).items():
            arms.setdefault(name, {})[s] = df
    return arms


# ------------------------------------------------------------------------------ attacks


def attacks(
    prim: dict[int, pl.DataFrame],
    bx: Boots,
    models: dict[int, pl.DataFrame],
    c2: float,
    s1: dict[int, pl.DataFrame],
    window_only: dict[int, pl.DataFrame],
    s3: dict[int, pl.DataFrame],
    fd_prim: dict[int, pl.DataFrame],
    arm_b_roi: dict[str, Any],
    seeds: int = 50,
) -> dict[str, Any]:
    """Run only when the primary word is PROFITABLE. A failed attack is reported as 'PROFITABLE, not
    believed (attack <x> failed)'. Attack (ix) needs the raw files and is manual."""
    from research.eval.alt_line_70_gate import p_over_at

    out: dict[str, Any] = {}
    elig = {s: df.filter(pl.col("pm") >= THRESHOLD - EPS) for s, df in prim.items()}
    base = bx.roi(elig)
    shifted: dict[int, pl.DataFrame] = {}
    for s, df in prim.items():
        parts = []
        for st in STATS:
            d = models[s].filter(pl.col("stat") == st)
            surv = survival_matrix(d["p_ge"].to_list())
            sub = df.filter(pl.col("stat") == st)
            mi = (sub["mi"].to_numpy() + 1_000) % max(d.height, 1)
            parts.append(
                sub.with_columns(pl.Series("pm", p_over_at(surv, mi, sub["point"].to_numpy())))
            )
        shifted[s] = pl.concat(parts).filter(pl.col("pm") >= THRESHOLD - EPS)
    i_roi = bx.roi(shifted)
    out["i_planted_future"] = {"E1": i_roi, "passes": bool(i_roi["point"] <= c2 + 0.02)}
    rois = []
    groups: dict[int, list[list[int]]] = {}
    srt: dict[int, pl.DataFrame] = {}
    for s, df in prim.items():
        d2 = df.sort(["game_id", "stat", "point"])
        gg: dict[Any, list[int]] = {}
        for i, k in enumerate(zip(d2["game_id"].to_list(), d2["stat"].to_list(), strict=True)):
            gg.setdefault(k, []).append(i)
        srt[s], groups[s] = d2, list(gg.values())
    for seed in range(seeds):
        rng = np.random.default_rng([2026, 4, seed])
        tot, n = 0.0, 0
        for s, d2 in srt.items():
            pm = d2["pm"].to_numpy().copy()
            for idx in groups[s]:
                pm[idx] = pm[rng.permutation(idx)]
            e = d2.with_columns(pl.Series("pm", pm)).filter(pl.col("pm") >= THRESHOLD - EPS)
            tot += float(e["profit"].sum())
            n += e.height
        rois.append(tot / n if n else float("nan"))
    q95 = float(np.nanpercentile(rois, 95))
    out["ii_shuffle_p"] = {
        "roi_p05_p50_p95": [float(x) for x in np.nanpercentile(rois, [5, 50, 95])],
        "passes": bool(q95 < base["hi"]),
    }
    allr = pl.concat([df for df in elig.values() if df.height])
    n_top = int(np.ceil(0.05 * allr.height))
    kept = allr.sort("profit").head(allr.height - n_top)
    wk = allr.group_by("wk").agg(pl.col("profit").sum().alias("w")).sort("w", descending=True)
    nobest = allr.filter(pl.col("wk") != wk["wk"][0])
    iii: dict[str, Any] = {
        "roi_without_best_5pct": float(kept["profit"].to_numpy().mean()),
        "roi_without_best_week": float(nobest["profit"].to_numpy().mean()),
    }
    iii["passes"] = bool(min(iii["roi_without_best_5pct"], iii["roi_without_best_week"]) > 0)
    out["iii_leave_out"] = iii
    fd_e = bx.roi(rows_of(fd_prim, pl.col("pm") >= THRESHOLD - EPS))
    out["iv_replicates"] = {
        "fanduel": fd_e,
        "arm_B": arm_b_roi,
        "passes": bool(fd_e.get("point", -1) > 0 and arm_b_roi.get("point", -1) > 0),
    }
    out["vi_roi_minus_c2"] = base["point"] - c2
    s1e = bx.roi(rows_of(s1, pl.col("pm") >= THRESHOLD - EPS))
    out["vii_window_games"] = {
        "S1_E1": s1e,
        "window_games_only_E1": bx.roi(rows_of(window_only, pl.col("pm") >= THRESHOLD - EPS)),
        "passes": bool(s1e.get("point", -1) > 0),
    }
    s3e = bx.roi(rows_of(s3, pl.col("pm") >= THRESHOLD - EPS))
    out["x_S3"] = {"E1": s3e}
    nulls = {
        "last_update_null": int(allr["last_update"].null_count()),
        "quote_age_min_max": float(allr["quote_age_min"].max()),  # type: ignore[arg-type]
    }
    out["v_missingness"] = nulls
    out["manual"] = ["ix FanDuel first listing (raw files)"]
    return out


# ------------------------------------------------------------------------------ guards


def timing_guard(pf: hc.Preflight, t_by_season: dict[int, pl.DataFrame]) -> None:
    """Every scored row: snapshot_ts <= tip - 60 min and last_update <= tip - 60 min."""
    cut = pl.col("tipoff_utc") - pl.duration(minutes=60)
    for s, t in sorted(t_by_season.items()):
        scored = t.filter(pl.col("book").is_in([PRIMARY, *REPLICATES]))
        bad_snap = scored.filter(pl.col("snapshot_ts") > cut).height
        bad_upd = scored.filter(pl.col("last_update") > cut).height
        null_upd = scored["last_update"].null_count()
        pf.add(
            f"{s}: every scored row has snapshot_ts and last_update <= tip - 60 min",
            bad_snap == 0 and bad_upd == 0 and null_upd == 0,
            f"snapshot violations {bad_snap}, last_update violations {bad_upd}, null {null_upd}",
        )


def gate_counts_guard(
    pf: hc.Preflight,
    gate: dict[str, Any],
    metas: dict[int, dict[str, Any]],
    t_by_season: dict[int, pl.DataFrame],
) -> None:
    """Window and lead-cap game counts, ladder-integrity counts and primary-arm rung counts equal
    the gate tables."""
    for s in sorted(metas):
        gm = gate["per_season"][str(s)]["meta"]
        m = metas[s]
        for key in (
            "games_with_alt_quotes",
            "games_with_report_stamp_in_window",
            "games_with_lead_gt_66",
        ):
            pf.add(f"{s}: {key} equals the gate table", m[key] == gm[key], f"{m[key]} vs {gm[key]}")
        a = sorted(m["ladder_integrity_by_book"], key=lambda r: r["book"])
        b = sorted(gm["ladder_integrity_by_book"], key=lambda r: r["book"])
        pf.add(f"{s}: ladder-integrity counts by book equal the gate table", a == b, "")
        t = t_by_season[s]
        got = {
            bk: {
                "all_rungs": int(t.filter((pl.col("book") == bk) & pl.col("ladder_off")).height),
                "by_vendor_window_all_rungs": int(
                    t.filter((pl.col("book") == bk) & pl.col("vendor_window")).height
                ),
            }
            for bk in gm["off_ladder_rungs_by_book"]
        }
        want = {
            bk: {k: v[k] for k in ("all_rungs", "by_vendor_window_all_rungs")}
            for bk, v in gm["off_ladder_rungs_by_book"].items()
        }
        pf.add(f"{s}: excluded rungs by book equal the gate table", got == want, "")
        prim = arms_of(t)["primary"]
        for bk in (PRIMARY, *REPLICATES):
            if bk not in gate["per_season"][str(s)]["books"]:
                continue
            want_n = gate["per_season"][str(s)]["books"][bk]["primary"]["ge70"].get("rungs", 0)
            got_n = prim.filter((pl.col("book") == bk) & (pl.col("pm") >= THRESHOLD - EPS)).height
            pf.add(
                f"{s}: {bk} primary-arm P>=0.70 rungs equal the gate table",
                got_n == want_n,
                f"{got_n} vs {want_n}",
            )
        pf.add(
            f"{s}: no defect-window DraftKings rung remains in the primary arm",
            prim.filter(pl.col("vendor_window")).height == 0,
            "",
        )


def run_preflight(
    pf: hc.Preflight,
    data_root: Path,
    repo: Path,
    lookup: Any = hc.git_prereg_shas,
    adh_sha: str | None = None,
) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON  # before any file is read
    pf.add("seasons <= 2024 (holdout never loaded)", max(SEASONS) <= MAX_SEASON, str(SEASONS))
    sha = adh_sha or hc.ADH_SHA
    hc.check_rule(pf, repo / RULE_PATH, RULE_ID, repo, sha, lookup)
    for f, want in CODE_SHA.items():
        path = repo / f
        got = hc.file_sha256(path) if path.exists() else "missing"
        pf.add(
            f"harness-pinned file {f} equals the sha256 recorded at the freeze",
            got == want,
            got[:12],
        )
    gate_path = data_root / GATE_JSON
    got = hc.file_sha256(gate_path) if gate_path.exists() else "missing"
    pf.add("gate.json equals the sha256 quoted in the rule", got == GATE_JSON_SHA, got[:12])
    gate: dict[str, Any] = json.loads(gate_path.read_text()) if gate_path.exists() else {}
    frozen = gate.get("input_sha256", {})
    for f in INPUT_FILES:
        path = data_root / f
        ok = path.exists() and f in frozen and hc.file_sha256(path) == frozen[f]
        pf.add(f"input {f} matches the gate's sha256", ok, "")
    odds = data_root / ODDS_DB
    if odds.exists():
        live = odds_fingerprint(odds)
        pf.add(
            "odds rows the rule's siblings read equal the pinned fingerprint",
            live == ODDS_FINGERPRINT,
            str(live),
        )
    res70 = repo / LINE70_RESULT  # results are written in the repo checkout (committed)
    if res70.exists():
        rec = json.loads(res70.read_text()).get("adh_sha256")
        pf.add("the ALT_LINE_70 result recorded this ALT_LADDER_HIST sha", rec == sha, f"{rec}")
    else:
        pf.add("the ALT_LINE_70 result recorded this ALT_LADDER_HIST sha", False, "no result file")
    return gate


# ------------------------------------------------------------------------------ run


def run_scoring(
    data_root: Path,
    pf: hc.Preflight,
    gate: dict[str, Any] | None,
    *,
    nba_db: Path | None = None,
    b: int = hc.B_DEFAULT,
    with_y: bool = True,
    run_attacks: bool = True,
) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON
    tips = pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "tipoff_utc"],
            )
            for s in SEASONS
        ]
    ).unique("game_id")
    stamps = ag.load_report_stamps(nba_db or (data_root / "nba.duckdb"))
    t_by: dict[int, pl.DataFrame] = {}
    models: dict[int, pl.DataFrame] = {}
    metas: dict[int, dict[str, Any]] = {}
    for s in SEASONS:
        t_by[s], models[s], metas[s] = build_season(data_root, s, tips, stamps, with_y)
    timing_guard(pf, t_by)
    if gate is not None:
        gate_counts_guard(pf, gate, metas, t_by)
    if not with_y:
        return {"mode": "check-only", "counts_only": True}
    pf.require()  # integrity first: any failed check stops before an estimate is made
    game_ids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in SEASONS}
    dates = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in SEASONS
    }
    bx = Boots(game_ids, dates, b)
    res: dict[str, Any] = {
        "rule": f"{RULE_ID} sha256 {hc.ADH_SHA}",
        "bootstrap": {"B": b, "seeds": {"game": hc.SEED_GAME, "date": hc.SEED_DATE}},
        "unstated_details": UNSTATED,
        "counts": {str(s): metas[s] for s in SEASONS},
    }
    books_arms = {bk: book_arms(t_by, bk) for bk in (PRIMARY, *REPLICATES)}
    prim = books_arms[PRIMARY]["primary"]
    res["draftkings"] = {
        "part1": part1(prim, bx, PRIMARY),
        "part2": part2(prim, bx),
    }
    res["draftkings"]["comparators"] = comparators(
        prim, {s: arms_of(t)["primary"] for s, t in t_by.items()}, bx
    )
    sens: dict[str, Any] = {}
    for name in (
        "S1_window_games_included",
        "S2_no_lead_cap",
        "S3_excluded_added_back",
        "window_games_only",
    ):
        arm = books_arms[PRIMARY][name]
        elig = rows_of(arm, pl.col("pm") >= THRESHOLD - EPS)
        sens[name] = {
            "E1": bx.roi(elig),
            "headline_gap_by_stat_season": {
                str(s): {
                    st: rung_cell(
                        rows_of({s: arm[s]}, (pl.col("stat") == st) & band_expr(*BAND)), bx, [s]
                    )
                    for st in STATS
                }
                for s in SEASONS
            },
        }
    res["draftkings"]["sensitivity"] = sens
    res["replicates"] = {}
    for bk in REPLICATES:
        per = books_arms[bk]["primary"]
        present = {s: df for s, df in per.items() if df.height}
        res["replicates"][bk] = (
            {
                "note": "descriptive only; the primary word is DraftKings",
                "part1": part1(present, bx, bk),
                "part2": part2(present, bx),
            }
            if present
            else {"note": "no primary-arm rungs"}
        )
    others = [
        bk
        for bk in sorted({x for t in t_by.values() for x in t["book"].unique().to_list()})
        if bk not in (PRIMARY, *REPLICATES)
    ]
    table: dict[str, Any] = {}
    for bk in others:
        arms = book_arms(t_by, bk)["primary"]
        elig = rows_of(arms, pl.col("pm") >= THRESHOLD - EPS)
        table[bk] = {
            "rungs_by_season": {str(s): df.height for s, df in arms.items()},
            "ge70_rungs_by_season": {str(s): df.height for s, df in elig.items()},
            "E1": bx.roi(elig),
        }
    res["other_books_count_table"] = table
    word = res["draftkings"]["part2"]["arm_A"]["word"]
    if run_attacks and word == "PROFITABLE":
        res["attacks"] = attacks(
            prim,
            bx,
            models,
            res["draftkings"]["comparators"]["C2_all_rungs"]["point"],
            books_arms[PRIMARY]["S1_window_games_included"],
            books_arms[PRIMARY]["window_games_only"],
            books_arms[PRIMARY]["S3_excluded_added_back"],
            books_arms["fanduel"]["primary"],
            res["draftkings"]["comparators"]["arm_B"]["E1"],
        )
    else:
        res["attacks"] = "not run (the primary word is not PROFITABLE)"
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--nba-db", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(OUT_DEFAULT))
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--b", type=int, default=hc.B_DEFAULT)
    args = ap.parse_args(argv)
    pf = hc.Preflight()
    gate = run_preflight(pf, args.data_root, args.repo_root)
    if args.check_only:
        run_scoring(args.data_root, pf, gate or None, nba_db=args.nba_db, with_y=False)
        print(json.dumps(pf.as_dicts(), indent=1))
        raise SystemExit(0 if pf.ok else 2)
    pf.require()
    res = run_scoring(args.data_root, pf, gate, nba_db=args.nba_db, b=args.b)
    res["checks"] = pf.as_dicts()
    res["adh_sha256"] = pf.info.get("ALT_LADDER_HIST_sha256")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")
    print("model word:", res["draftkings"]["part1"].get("model_word"))
    print("arm A word:", res["draftkings"]["part2"]["arm_A"]["word"])


if __name__ == "__main__":
    main()
