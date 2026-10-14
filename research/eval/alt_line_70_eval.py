"""ALT_LINE_70 run harness (rule: docs/prereg/ALT_LINE_70.md, FROZEN on main, sha a72bdff2...).

    .venv/bin/python -W ignore -m research.eval.alt_line_70_eval \
        --data-root <checkout holding data/ and reports/> [--check-only]

The harness scores exactly what the frozen rule says and nothing else. It REFUSES to run unless:
* the rule text above ``=== RESULTS BELOW ===`` hashes to the pinned frozen sha, a commit
  ``prereg(ALT_LINE_70): <sha>`` carries it, and its status line says FROZEN;
* ``docs/prereg/ALT_LADDER_HIST.md`` is FROZEN too (a ``Status: **FROZEN`` line and a
  ``prereg(ALT_LADDER_HIST): <sha>`` commit that carries its current text): the rule says
  ALT_LADDER_HIST's decision words are frozen BEFORE this harness runs, so these results cannot
  tune them (ALT_LINE_70 section "Follow-ups"; pre-freeze review B1, N2);
* the stored inputs match the rule's data_version and its gate counts (model rows and tip tables by
  sha256; the odds store by the counts of the rule's bet sets and, when ``--pre-alt-backup`` is
  given, by a content fingerprint of the rows the rule reads against the pre-alternate backup);
* ``max(season) <= 2024`` (asserted before any file is read).

``--check-only`` runs every check and the count comparison WITHOUT reading the outcome column ``y``
(the model-row read omits it) and exits; it is the dry run. Without it the harness reads ``y`` and
writes ``reports/alt_line_70/results.json``. Nothing is appended to the rule file: the main
session writes the result note below the line.

Seeds and conventions are the rule's: game bootstrap ``default_rng(2026)``, date bootstrap
``default_rng(2027)``, B = 5,000, one resample matrix per season shared by every cell, decision
interval = union of the two percentile intervals, p = max of the two clusterings, plan draws
``default_rng(2026)`` with R = 2,000. Details the rule leaves open are listed in ``UNSTATED``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import alt_harness_common as hc
from research.eval.alt_line_70_gate import (
    BAND,
    BINS,
    DIST_EDGES,
    DIST_UNIT,
    EPS,
    MAX_SEASON,
    PRIMARY_BOOK,
    PRIMARY_KIND,
    SEASONS,
    SECONDARY_BINS,
    SENSITIVITY_KIND,
    STATS,
    THRESHOLD,
    best_price,
    cluster_halfwidth,
    decimal_odds,
    dedupe_dropping,
    p_over_at,
    pool_summaries,
    priced_rungs,
    summarize_bets,
    survival_matrix,
)

RULE_ID = "ALT_LINE_70"
RULE_PATH = "docs/prereg/ALT_LINE_70.md"
RULE_SHA = "a72bdff245ac5df645778224d41c478a6c7147fcda57f6b2e9ca30349aeceb5f"
ADH_ID = "ALT_LADDER_HIST"
ADH_PATH = "docs/prereg/ALT_LADDER_HIST.md"
GATE_JSON = "reports/prereg_alt_line_70/gate.json"
OUT_DEFAULT = "reports/alt_line_70/results.json"
DUPLICATE_BOOK = "unibet_us"
MODEL_INPUTS = (
    "reports/odds_history/model_rows_2023.parquet",
    "reports/odds_history/model_rows_2024.parquet",
    "data/ops/model_clv_pairs/pairs_2023.parquet",
    "data/ops/model_clv_pairs/pairs_2024.parquet",
)
ODDS_DB = "data/odds/odds_history.duckdb"
MAIN_MARKETS = ("player_points", "player_rebounds", "player_assists", "player_threes")

# headline thresholds of the rule's gate: Part 1 headline cell, secondary cell, Part 2 pooled
P1_HEAD = {"games": 300, "rows": 5_000, "hw": 0.03}
P1_SEC = {"games": 300, "rows": 4_000, "hw": 0.03}
P2_POOLED = {"rungs": 1_500, "games": 300, "hw": 0.05}
CAL_POINT, CAL_MARGIN = 0.02, 0.03
ROI_FLOOR = 0.02
DIST_MIN_ROWS, DIST_MIN_GAMES = 1_000, 300

#: counts of the rule's own gate tables (ALT_LINE_70 "Minimum-data gate"); the harness stops on a
#: mismatch because the data are then not the data the rule was frozen on
EXPECTED: dict[str, Any] = {
    "band_rows": {
        2023: {"pts": 76_170, "reb": 32_385, "ast": 16_848, "fg3m": 8_987},
        2024: {"pts": 76_389, "reb": 31_871, "ast": 18_304, "fg3m": 10_371},
    },
    "p70_rows": {
        2023: {"pts": 200_430, "reb": 73_150, "ast": 38_886, "fg3m": 13_672},
        2024: {"pts": 199_996, "reb": 74_011, "ast": 40_176, "fg3m": 15_566},
    },
    "games": {2023: 1_318, 2024: 1_315},
    "arm_a_rungs": {2023: 3_199, 2024: 1_347},
    "arm_a_units": {2023: 2_496, 2024: 1_132},
    "arm_a_games": {2023: 967, 2024: 482},
    "arm_a_by_stat": {
        2023: {"pts": 2_461, "reb": 236, "ast": 221, "fg3m": 281},
        2024: {"pts": 876, "reb": 137, "ast": 170, "fg3m": 164},
    },
    "bin_rungs": {
        "55": {2023: 24_024, 2024: 17_391},
        "65": {2023: 10_909, 2024: 6_009},
        "75": {2023: 2_836, 2024: 1_168},
        "85": {2023: 331, 2024: 164},
    },
    "t60_arm_a_rungs": {2023: 3_070, 2024: 1_207},
}

UNSTATED = [
    "A fresh numpy Generator is created for each plan (arm A, then each secondary bin); within a "
    "plan seasons ascend, weeks ascend, and inside a week the unit draw precedes the rung draw.",
    "Stat-level ROI words use the two-sided p (the larger of the two clusterings); the bin words "
    "use the one-sided p for ROI <= 0, as the rule writes for the bins.",
    "The C2 comparator is the ROI of ALL BetRivers t5 rungs, which is the expectation of a random "
    "rung draw of the same count.",
    "Bootstrap resampling draws cluster INDICES with replacement from the sorted unique game ids "
    "(dates: sorted unique dates) of the season's model rows; the cluster set is the same for "
    "every cell and arm, rows of other clusters contribute zero.",
    "A cell whose gate fails gets the word 'NO WORD (gate)'; percentiles are 2.5 and 97.5.",
    "Break-even interval for a realised hit rate is the union of the two clusterings' percentile "
    "intervals of the hit rate.",
]


# ------------------------------------------------------------------------------ loading


def load_model(data_root: Path, season: int, with_y: bool) -> pl.DataFrame:
    """Stored production forecast rows for ``season``, played rows only. ``with_y=False`` never
    requests the outcome column."""
    assert season <= MAX_SEASON
    cols = ["game_id", "player_id", "stat", "game_date", "minutes", "starter10", "p_ge"]
    if with_y:
        cols.append("y")
    m = pl.read_parquet(
        data_root / f"reports/odds_history/model_rows_{season}.parquet", columns=cols
    )
    if not with_y:
        assert "y" not in m.columns
    return m.filter(pl.col("minutes").is_not_null() & (pl.col("minutes") > 0))


def load_tips(data_root: Path) -> pl.DataFrame:
    return pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "tipoff_utc"],
            )
            for s in SEASONS
        ]
    ).unique("game_id")


class StatFrame:
    """One (season, stat) of model rows as arrays: survival matrix, hit matrix, cluster indices."""

    def __init__(
        self, d: pl.DataFrame, game_ids: np.ndarray, dates: np.ndarray, with_y: bool
    ) -> None:
        self.surv = survival_matrix(d["p_ge"].to_list())
        self.n, self.k = self.surv.shape
        self.game_idx = np.searchsorted(game_ids, d["game_id"].to_numpy())
        self.date_idx = np.searchsorted(dates, d["game_date"].to_numpy().astype("datetime64[D]"))
        self.starter = (
            (d["starter10"].fill_null(0.0).to_numpy() >= 0.5) if d.height else np.zeros(0, bool)
        )
        self.player_id = d["player_id"].to_numpy()
        ks = np.arange(1, self.k + 1)[None, :]
        self.hit: np.ndarray | None = None
        if with_y:
            y = d["y"].to_numpy()
            self.hit = y[:, None] >= ks
        km = np.argmin(np.abs(self.surv - 0.5), axis=1) + 1
        self.dist = (km[:, None] - ks) / 1.0  # divided by the stat unit by the caller
        self.n_games = len(game_ids)
        self.n_dates = len(dates)


# ------------------------------------------------------------------------------ Part 1


def p1_cell(
    sf: StatFrame, mask: np.ndarray, mg: np.ndarray, md: np.ndarray, with_y: bool = True
) -> dict[str, Any]:
    """One calibration cell: line-rows ``mask`` ([n, K]) of one (season, stat). gap = hit rate -
    mean P (ratio of cluster totals), game- and date-clustered percentile bootstrap, conservative
    half-width, break-even prices at the claim and at the realised rate."""
    rows_pg = mask.sum(axis=1).astype(np.float64)
    n = int(rows_pg.sum())
    n_g = hc.cluster_totals(sf.game_idx, sf.n_games, rows_pg)
    out: dict[str, Any] = {
        "line_rows": n,
        "player_games": int((rows_pg > 0).sum()),
        "games": int((n_g > 0).sum()),
        "dates": int((hc.cluster_totals(sf.date_idx, sf.n_dates, rows_pg) > 0).sum()),
    }
    if n == 0:
        return out
    psum = (mask * sf.surv).sum(axis=1)
    mean_p = float(psum.sum() / n)
    out["mean_p"] = mean_p
    out["halfwidth_cons"] = cluster_halfwidth(mean_p, n_g[n_g > 0])
    if not with_y:
        return out
    assert sf.hit is not None
    hits_pg = (mask & sf.hit).sum(axis=1).astype(np.float64)
    hit_rate = float(hits_pg.sum() / n)
    out["hit_rate"] = hit_rate
    out["gap"] = hit_rate - mean_p
    out["break_even"] = hc.break_even(mean_p, hit_rate)
    gam = hc.cluster_totals(sf.game_idx, sf.n_games, hits_pg - psum)
    dat = hc.cluster_totals(sf.date_idx, sf.n_dates, hits_pg - psum)
    den_d = hc.cluster_totals(sf.date_idx, sf.n_dates, rows_pg)
    _, bg = hc.boot_ratio([(mg, gam, n_g)])
    _, bd = hc.boot_ratio([(md, dat, den_d)])
    out.update(hc.decide(out["gap"], bg, bd))
    # hit-rate interval for the break-even table
    hg = hc.cluster_totals(sf.game_idx, sf.n_games, hits_pg)
    hd = hc.cluster_totals(sf.date_idx, sf.n_dates, hits_pg)
    _, hbg = hc.boot_ratio([(mg, hg, n_g)])
    _, hbd = hc.boot_ratio([(md, hd, den_d)])
    out["hit_rate_interval"] = [
        float(min(hc.percentile_interval(hbg)[0], hc.percentile_interval(hbd)[0])),
        float(max(hc.percentile_interval(hbg)[1], hc.percentile_interval(hbd)[1])),
    ]
    return out


def calibration_word(cell: dict[str, Any], q: float, gate: dict[str, float]) -> str:
    """CALIBRATED / OVERCONFIDENT / UNDERCONFIDENT / INCONCLUSIVE (rule Part 1), or NO WORD when the
    cell fails its minimum-data gate."""
    if (
        cell["games"] < gate["games"]
        or cell["line_rows"] < gate["rows"]
        or cell.get("halfwidth_cons", 9.0) > gate["hw"]
    ):
        return "NO WORD (gate)"
    gap, lo, hi = cell["gap"], cell["lo"], cell["hi"]
    if abs(gap) <= CAL_POINT and lo >= -CAL_MARGIN and hi <= CAL_MARGIN:
        return "CALIBRATED"
    if gap < -CAL_POINT and hi < 0 and q < 0.05:
        return "OVERCONFIDENT"
    if gap > CAL_POINT and lo > 0 and q < 0.05:
        return "UNDERCONFIDENT"
    return "INCONCLUSIVE"


def stat_word(w2023: str, w2024: str) -> str:
    return w2023 if w2023 == w2024 else "MIXED"


def model_word(stat_words: dict[str, str]) -> str:
    """'calibrated at 70%+' iff all four stat words are CALIBRATED; 'OVERCONFIDENT at 70%+ for
    <stats>' if any is OVERCONFIDENT; else 'NOT SHOWN calibrated' with the list."""
    if all(w == "CALIBRATED" for w in stat_words.values()):
        return "calibrated at 70%+"
    over = [s for s, w in stat_words.items() if w == "OVERCONFIDENT"]
    if over:
        return "OVERCONFIDENT at 70%+ for " + ", ".join(over)
    return "NOT SHOWN calibrated (" + ", ".join(f"{s}: {w}" for s, w in stat_words.items()) + ")"


def part1(
    frames: dict[int, dict[str, StatFrame]],
    mg: dict[int, np.ndarray],
    md: dict[int, np.ndarray],
    posted: dict[int, dict[str, np.ndarray]],
) -> dict[str, Any]:
    out: dict[str, Any] = {"headline": {}, "bins": {}, "distance": {}, "secondary": {}}
    head_keys: list[tuple[int, str]] = []
    head_cells: list[dict[str, Any]] = []
    for s in sorted(frames):
        for st in STATS:
            sf = frames[s][st]
            elig = sf.surv >= THRESHOLD - EPS
            band = elig & (sf.surv < BAND[1])
            cell = p1_cell(sf, band, mg[s], md[s])
            head_keys.append((s, st))
            head_cells.append(cell)
            out["bins"].setdefault(str(s), {}).setdefault(st, {})
            for lo, hi in BINS:
                label = f"{lo:.2f}-{min(hi, 1.0):.2f}"
                mk = elig & (sf.surv >= lo - EPS) & (sf.surv < hi)
                out["bins"][str(s)][st][label] = p1_cell(sf, mk, mg[s], md[s])
            out["bins"][str(s)][st]["P=1.0"] = p1_cell(sf, sf.surv >= 1 - EPS, mg[s], md[s])
            unit = DIST_UNIT[st]
            dist = sf.dist / unit
            out["distance"].setdefault(str(s), {})[st] = {}
            for a, b in DIST_EDGES:
                label = f"({a:g},{b:g}]" if a > -1e8 else f"(-inf,{b:g}]"
                dc = p1_cell(sf, band & (dist > a) & (dist <= b), mg[s], md[s])
                dc["insufficient"] = bool(
                    dc["line_rows"] < DIST_MIN_ROWS or dc["games"] < DIST_MIN_GAMES
                )
                out["distance"][str(s)][st][label] = dc
            ps = posted[s][st]
            starter = sf.starter[:, None]
            parts = {
                "posted_t5": band & ps,
                "not_posted_t5": band & ~ps,
                "starter": band & starter,
                "bench": band & ~starter,
                "posted_starter": band & ps & starter,
                "posted_bench": band & ps & ~starter,
                "not_posted_starter": band & ~ps & starter,
                "not_posted_bench": band & ~ps & ~starter,
            }
            out.setdefault("decomposition", {}).setdefault(str(s), {})[st] = {
                k: p1_cell(sf, v, mg[s], md[s]) for k, v in parts.items()
            }
    q = hc.bh_q([c.get("p_two_sided", 1.0) for c in head_cells])
    words: dict[tuple[int, str], str] = {}
    for (s, st), cell, qq in zip(head_keys, head_cells, q, strict=True):
        cell["bh_q"] = qq
        cell["word"] = calibration_word(cell, qq, P1_HEAD)
        words[(s, st)] = cell["word"]
        out["headline"].setdefault(str(s), {})[st] = cell
    sw = {st: stat_word(words[(2023, st)], words[(2024, st)]) for st in STATS}
    out["stat_words"] = sw
    out["model_word"] = model_word(sw)
    # six-bin family: BH over the 48 cells, descriptive words only
    flat = [
        (s, st, lab, c)
        for s, d1 in out["bins"].items()
        for st, d2 in d1.items()
        for lab, c in d2.items()
        if lab != "P=1.0" and c.get("line_rows", 0) > 0 and "p_two_sided" in c
    ]
    for (_, _, _, c), qq in zip(flat, hc.bh_q([c["p_two_sided"] for *_, c in flat]), strict=True):
        c["bh_q_family48"] = qq
    # secondary Part 1S: 4 bins x 4 stats x 2 seasons = 32 cells, own BH family
    sec_keys: list[tuple[str, int, str]] = []
    sec_cells: list[dict[str, Any]] = []
    for label, lo, hi in SECONDARY_BINS:
        for s in sorted(frames):
            for st in STATS:
                sf = frames[s][st]
                mk = (sf.surv >= lo - EPS) & (sf.surv < hi - EPS)
                sec_keys.append((label, s, st))
                sec_cells.append(p1_cell(sf, mk, mg[s], md[s]))
    q32 = hc.bh_q([c.get("p_two_sided", 1.0) for c in sec_cells])
    sec_words: dict[tuple[str, int, str], str] = {}
    for (label, s, st), cell, qq in zip(sec_keys, sec_cells, q32, strict=True):
        cell["bh_q"] = qq
        cell["word"] = calibration_word(cell, qq, P1_SEC)
        sec_words[(label, s, st)] = cell["word"]
        out["secondary"].setdefault(label, {}).setdefault(str(s), {})[st] = cell
    out["secondary_stat_words"] = {
        label: {
            st: stat_word(sec_words[(label, 2023, st)], sec_words[(label, 2024, st)])
            for st in STATS
        }
        for label, _, _ in SECONDARY_BINS
    }
    return out


# ------------------------------------------------------------------------------ Part 2


def with_outcome(t: pl.DataFrame, model: pl.DataFrame) -> pl.DataFrame:
    """Join the realised stat; hit = Y > point (point = k - 0.5); profit in stake units."""
    y = model.select("game_id", "player_id", "stat", "y")
    t = t.join(y, on=["game_id", "player_id", "stat"], how="left")
    assert t["y"].null_count() == 0, "a priced rung joined to a model row without an outcome"
    return t.with_columns((pl.col("y") > pl.col("point")).alias("hit")).with_columns(
        pl.when(pl.col("hit")).then(pl.col("dec") - 1.0).otherwise(-1.0).alias("profit")
    )


def add_indices(t: pl.DataFrame, game_ids: np.ndarray, dates: np.ndarray) -> pl.DataFrame:
    gi = np.searchsorted(game_ids, t["game_id"].to_numpy())
    di = np.searchsorted(dates, t["game_date"].to_numpy().astype("datetime64[D]"))
    return t.with_columns(pl.Series("gi", gi), pl.Series("di", di))


def roi_parts(
    rows: dict[int, pl.DataFrame],
    boots_g: dict[int, np.ndarray],
    boots_d: dict[int, np.ndarray],
    sizes_g: dict[int, int],
    sizes_d: dict[int, int],
    unit_mean: bool = False,
) -> tuple[list[Any], list[Any]]:
    """Cluster totals for the pooled ratio estimator, one stratum per season in ``boots_g``."""
    pg: list[Any] = []
    pd_: list[Any] = []
    for s in sorted(boots_g):
        df = rows.get(s)
        if df is None or df.is_empty():
            pg.append((boots_g[s], np.zeros(sizes_g[s]), np.zeros(sizes_g[s])))
            pd_.append((boots_d[s], np.zeros(sizes_d[s]), np.zeros(sizes_d[s])))
            continue
        gi = df["gi"].to_numpy()
        di = df["di"].to_numpy()
        if unit_mean:
            u = df.group_by("gi", "di", "player_id", "stat").agg(
                pl.col("profit").mean().alias("um")
            )
            gi_u, di_u, um = u["gi"].to_numpy(), u["di"].to_numpy(), u["um"].to_numpy()
            pg.append(
                (
                    boots_g[s],
                    hc.cluster_totals(gi_u, sizes_g[s], um),
                    hc.cluster_totals(gi_u, sizes_g[s]),
                )
            )
            pd_.append(
                (
                    boots_d[s],
                    hc.cluster_totals(di_u, sizes_d[s], um),
                    hc.cluster_totals(di_u, sizes_d[s]),
                )
            )
        else:
            prof = df["profit"].to_numpy()
            pg.append(
                (
                    boots_g[s],
                    hc.cluster_totals(gi, sizes_g[s], prof),
                    hc.cluster_totals(gi, sizes_g[s]),
                )
            )
            pd_.append(
                (
                    boots_d[s],
                    hc.cluster_totals(di, sizes_d[s], prof),
                    hc.cluster_totals(di, sizes_d[s]),
                )
            )
    return pg, pd_


class Boots:
    """The shared resample matrices (one per season, game and date clustering)."""

    def __init__(self, games: dict[int, np.ndarray], dates: dict[int, np.ndarray], b: int) -> None:
        self.g = hc.make_boot({s: len(v) for s, v in games.items()}, b, hc.SEED_GAME)
        self.d = hc.make_boot({s: len(v) for s, v in dates.items()}, b, hc.SEED_DATE)
        self.size_g = {s: len(v) for s, v in games.items()}
        self.size_d = {s: len(v) for s, v in dates.items()}
        self.b = b

    def roi(
        self,
        rows: dict[int, pl.DataFrame],
        seasons: list[int] | None = None,
        unit_mean: bool = False,
    ) -> dict[str, Any]:
        """Pooled ROI over ``seasons`` (default all) plus the per-season ROI and the decision."""
        seasons = seasons or sorted(self.g)
        n = sum(rows[s].height for s in seasons if s in rows)
        res: dict[str, Any] = {"rungs": n}
        if n == 0:
            return res
        pg, pd_ = roi_parts(
            {s: rows[s] for s in seasons if s in rows},
            {s: self.g[s] for s in seasons},
            {s: self.d[s] for s in seasons},
            self.size_g,
            self.size_d,
            unit_mean,
        )
        point, bg = hc.boot_ratio(pg)
        _, bd = hc.boot_ratio(pd_)
        res.update(hc.decide(point, bg, bd))
        res["by_season"] = {}
        for s in seasons:
            if s in rows and rows[s].height:
                pgs, pds = roi_parts(
                    {s: rows[s]},
                    {s: self.g[s]},
                    {s: self.d[s]},
                    self.size_g,
                    self.size_d,
                    unit_mean,
                )
                p1, b1 = hc.boot_ratio(pgs)
                _, b2 = hc.boot_ratio(pds)
                res["by_season"][str(s)] = {"rungs": rows[s].height, **hc.decide(p1, b1, b2)}
        return res

    def ratio(
        self, rows: dict[int, pl.DataFrame], col: str, seasons: list[int] | None = None
    ) -> dict[str, Any]:
        """Pooled mean of a numeric column (e.g. the 0/1 hit) with the union interval."""
        seasons = seasons or sorted(self.g)
        parts_g: list[Any] = []
        parts_d: list[Any] = []
        for s in seasons:
            df = rows.get(s)
            if df is None or df.is_empty():
                parts_g.append((self.g[s], np.zeros(self.size_g[s]), np.zeros(self.size_g[s])))
                parts_d.append((self.d[s], np.zeros(self.size_d[s]), np.zeros(self.size_d[s])))
                continue
            v = df[col].cast(pl.Float64).to_numpy()
            parts_g.append(
                (
                    self.g[s],
                    hc.cluster_totals(df["gi"].to_numpy(), self.size_g[s], v),
                    hc.cluster_totals(df["gi"].to_numpy(), self.size_g[s]),
                )
            )
            parts_d.append(
                (
                    self.d[s],
                    hc.cluster_totals(df["di"].to_numpy(), self.size_d[s], v),
                    hc.cluster_totals(df["di"].to_numpy(), self.size_d[s]),
                )
            )
        point, bg = hc.boot_ratio(parts_g)
        _, bd = hc.boot_ratio(parts_d)
        return hc.decide(point, bg, bd)


def money_word(res: dict[str, Any], gate_ok: bool, bh_ok: bool = True) -> str:
    """PROFITABLE / NOT PROFITABLE / INCONCLUSIVE on pooled E1 (rule Part 2); 'NO WORD (gate)' when
    the cell fails its minimum-data gate."""
    if not gate_ok or res.get("rungs", 0) == 0:
        return "NO WORD (gate)"
    seasons = [v["point"] for v in res["by_season"].values()]
    if res["point"] >= ROI_FLOOR and res["lo"] > 0 and all(x > 0 for x in seasons) and bh_ok:
        return "PROFITABLE"
    if res["hi"] < ROI_FLOOR:
        return "NOT PROFITABLE"
    return "INCONCLUSIVE"


def gate_pooled_roi(frames_by_season: dict[int, pl.DataFrame]) -> dict[str, Any]:
    """The rule's Part 2 gate on a bet set: pooled rungs, games and conservative ROI half-width."""
    parts = [summarize_bets(df) for s, df in sorted(frames_by_season.items()) if df.height]
    pooled: dict[str, Any] = pool_summaries(parts) if parts else {"rungs": 0}
    pooled["ok"] = bool(
        pooled.get("rungs", 0) >= P2_POOLED["rungs"]
        and pooled.get("games", 0) >= P2_POOLED["games"]
        and pooled.get("roi_halfwidth_cons", 9.0) <= P2_POOLED["hw"]
    )
    pooled["by_season"] = {str(s): summarize_bets(df) for s, df in sorted(frames_by_season.items())}
    return pooled


def devig_own(t: pl.DataFrame) -> pl.DataFrame:
    """Own de-vigged over probability of a two-sided rung (proportional), from its prices."""
    du = decimal_odds(t["pu"].to_numpy())
    qo = (1.0 / t["dec"].to_numpy()) / ((1.0 / t["dec"].to_numpy()) + (1.0 / du))
    return t.with_columns(pl.Series("q_own", qo))


def market_distance(br: pl.DataFrame) -> pl.DataFrame:
    """Market ruler: per BetRivers (game, player, stat) the main rung is the one whose own
    de-vigged over probability is closest to 0.5 (ties to the lower line); distance = (main line -
    L) / u with u = 2 for pts and 1 otherwise."""
    br = devig_own(br)
    key = ["game_id", "player_id", "stat"]
    main = (
        br.with_columns((pl.col("q_own") - 0.5).abs().alias("gap5"))
        .sort([*key, "gap5", "point"])
        .unique(key, keep="first", maintain_order=True)
        .select(*key, pl.col("point").alias("main_point"))
    )
    unit = pl.col("stat").replace_strict(DIST_UNIT)
    return br.join(main, on=key, how="left").with_columns(
        ((pl.col("main_point") - pl.col("point")) / unit).alias("mdist")
    )


def break_even_table(arm: dict[int, pl.DataFrame], bx: Boots) -> dict[str, Any]:
    """Per P bin of Part 2's bet set: rungs, claimed P, realised hit rate with interval, mean
    decimal price, break-even at the claim and at the realised rate, ROI at the claim beside the
    realised ROI."""
    out: dict[str, Any] = {}
    for lo, hi in BINS:
        label = f"{lo:.2f}-{min(hi, 1.0):.2f}"
        rows = {
            s: df.filter((pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi)) for s, df in arm.items()
        }
        n = sum(r.height for r in rows.values())
        if n == 0:
            out[label] = {"rungs": 0}
            continue
        allr = pl.concat([r for r in rows.values() if r.height])
        hit = bx.ratio(rows, "hit")
        p = float(allr["pm"].mean())  # type: ignore[arg-type]
        out[label] = {
            "rungs": n,
            "mean_claimed_p": p,
            "hit_rate": hit["point"],
            "hit_rate_interval": [hit["lo"], hit["hi"]],
            "mean_decimal": float(allr["dec"].mean()),  # type: ignore[arg-type]
            **hc.break_even(p, hit["point"]),
            "roi_at_claim": float((allr["pm"] * allr["dec"] - 1.0).mean()),  # type: ignore[arg-type]
            "roi_realised": float(allr["profit"].mean()),  # type: ignore[arg-type]
        }
    return out


def plan_block(arm: dict[int, pl.DataFrame]) -> dict[str, Any]:
    """E2 for a bet set: the $2 x 10-a-week plan, R draws per season."""
    weeks: dict[int, list[Any]] = {}
    for s, df in sorted(arm.items()):
        if df.is_empty():
            weeks[s] = []
            continue
        unit = [
            (g, p, st) for g, p, st in zip(df["game_id"], df["player_id"], df["stat"], strict=True)
        ]
        weeks[s] = hc.build_weeks(df["profit"].to_numpy(), df["wk"].to_list(), unit)
    sims = hc.simulate_plan(weeks)
    return {str(s): hc.plan_summary(v["profit"], v["staked"]) for s, v in sims.items()}


def part2(
    t5: dict[int, pl.DataFrame],
    t60: dict[int, pl.DataFrame],
    bx: Boots,
    model_y: dict[int, pl.DataFrame],
    game_ids: dict[int, np.ndarray],
    dates: dict[int, np.ndarray],
) -> dict[str, Any]:
    """Bet set A, the stat and season cuts, the secondary bins, comparators and sensitivities."""
    out: dict[str, Any] = {}
    seasons = sorted(t5)
    br_all = {s: t5[s].filter(pl.col("book") == PRIMARY_BOOK) for s in seasons}
    arm_a = {s: df.filter(pl.col("pm") >= THRESHOLD - EPS) for s, df in br_all.items()}
    gate_a = gate_pooled_roi(arm_a)
    e1 = bx.roi(arm_a)
    out["arm_A"] = {"gate": gate_a, "E1": e1, "word": money_word(e1, gate_a["ok"])}
    out["arm_A"]["E1u"] = bx.roi(arm_a, unit_mean=True)
    # by stat: BH over the 4 stat cells (two-sided p = max of the clusterings)
    stat_res = {
        st: bx.roi({s: df.filter(pl.col("stat") == st) for s, df in arm_a.items()}) for st in STATS
    }
    qs = hc.bh_q([stat_res[st].get("p_two_sided", 1.0) for st in STATS])
    out["by_stat"] = {}
    for st, qq in zip(STATS, qs, strict=True):
        r = stat_res[st]
        r["bh_q"] = qq
        sg = gate_pooled_roi({s: df.filter(pl.col("stat") == st) for s, df in arm_a.items()})
        r["gate_ok"] = sg["ok"]  # shown; by-stat words are descriptive and are not gated
        r["word"] = money_word(r, True, bh_ok=qq < 0.05)
        out["by_stat"][st] = r
    out["break_even_by_p_bin"] = break_even_table(arm_a, bx)
    out["E2_plan"] = plan_block(arm_a)
    # comparators
    c1 = {s: devig_own(df).filter(pl.col("q_own") >= THRESHOLD - EPS) for s, df in br_all.items()}
    out["C1_market_lean"] = bx.roi(c1)
    out["C2_all_betrivers_rungs"] = bx.roi(br_all)
    md = {s: market_distance(df) for s, df in br_all.items()}
    ruler: dict[str, Any] = {}
    for a, b in DIST_EDGES:
        label = f"({a:g},{b:g}]" if a > -1e8 else f"(-inf,{b:g}]"
        rows = {
            s: md[s].filter(
                (pl.col("pm") >= THRESHOLD - EPS) & (pl.col("mdist") > a) & (pl.col("mdist") <= b)
            )
            for s in seasons
        }
        ruler[label] = bx.roi(rows)
    out["market_ruler_slices"] = ruler
    # arm B: best over price across all books at that exact rung, same eligibility
    arm_b = {s: best_price(df.filter(pl.col("pm") >= THRESHOLD - EPS)) for s, df in t5.items()}
    out["arm_B_best_price_any_book"] = {
        "gate": gate_pooled_roi(arm_b),
        "E1": bx.roi(arm_b),
        "note": "best decimal over all books at the exact (game, player, stat, line)",
    }
    out["unibet_replicate"] = bx.roi(
        {
            s: df.filter((pl.col("book") == DUPLICATE_BOOK) & (pl.col("pm") >= THRESHOLD - EPS))
            for s, df in t5.items()
        }
    )
    # sensitivity: the t60 capture (before the cutoff; descriptive, never decides)
    t60_a = {
        s: df.filter((pl.col("book") == PRIMARY_BOOK) & (pl.col("pm") >= THRESHOLD - EPS))
        for s, df in t60.items()
    }
    out["sensitivity_t60_arm_A"] = {
        "E1": bx.roi(t60_a),
        "rungs": {str(s): df.height for s, df in t60_a.items()},
    }
    # secondary family: four disjoint bins, BH over the 4 bin cells (one-sided p)
    sec: dict[str, Any] = {}
    cells: list[dict[str, Any]] = []
    for label, lo, hi in SECONDARY_BINS:
        rows = {
            s: df.filter((pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi - EPS))
            for s, df in br_all.items()
        }
        g = gate_pooled_roi(rows)
        r = bx.roi(rows)
        cell: dict[str, Any] = {
            "gate": g,
            "E1": r,
            "E1u": bx.roi(rows, unit_mean=True),
            "break_even": break_even_table(rows, bx),
            "E2_plan": plan_block(rows),
        }
        sec[label] = cell
        cells.append(cell)
    qbin = hc.bh_q([c["E1"].get("p_le0", 1.0) for c in cells])
    for cell, qq in zip(cells, qbin, strict=True):
        cell["bh_q"] = qq
        cell["word"] = money_word(cell["E1"], cell["gate"]["ok"], bh_ok=qq < 0.05)
    out["secondary"] = sec
    return out


# ------------------------------------------------------------------------------ attacks


def attacks(
    t5: dict[int, pl.DataFrame],
    bx: Boots,
    model_y: dict[int, pl.DataFrame],
    arm_a: dict[int, pl.DataFrame],
    c2: float,
    seeds: int = 50,
) -> dict[str, Any]:
    """The rule's attack list, run only when the word is PROFITABLE: (i) planted future, (ii)
    shuffle P within (game, stat) among BetRivers rungs, (iii) leave out the best 5% of bets and the
    best week, (iv) arm B / Unibet signs are in Part 2, (vi) ROI minus the C2 baseline.
    (v), (vii), (viii) need extra stores and are listed as manual in the output."""
    out: dict[str, Any] = {}
    # (i) planted future: reassign each rung's model P to a player-game 1,000 rows away (same stat)
    shifted: dict[int, pl.DataFrame] = {}
    for s, df in t5.items():
        br = df.filter(pl.col("book") == PRIMARY_BOOK)
        parts: list[pl.DataFrame] = []
        for st in STATS:
            d = model_y[s].filter(pl.col("stat") == st)
            surv = survival_matrix(d["p_ge"].to_list())
            sub = br.filter(pl.col("stat") == st)
            mi = (sub["mi"].to_numpy() + 1_000) % max(d.height, 1)
            pm = p_over_at(surv, mi, sub["point"].to_numpy())
            parts.append(sub.with_columns(pl.Series("pm", pm)))
        shifted[s] = pl.concat(parts).filter(pl.col("pm") >= THRESHOLD - EPS)
    out["i_planted_future"] = {"E1": bx.roi(shifted), "c2_level": c2}
    # (ii) shuffle P within (game, stat) among BetRivers rungs, 50 seeds
    rois: list[float] = []
    sorted_br: dict[int, pl.DataFrame] = {}
    groups_by_season: dict[int, list[list[int]]] = {}
    for s, df in t5.items():
        br = df.filter(pl.col("book") == PRIMARY_BOOK).sort(["game_id", "stat", "point"])
        groups: dict[Any, list[int]] = {}
        for i, k in enumerate(zip(br["game_id"].to_list(), br["stat"].to_list(), strict=True)):
            groups.setdefault(k, []).append(i)
        sorted_br[s] = br
        groups_by_season[s] = list(groups.values())
    for seed in range(seeds):
        rng = np.random.default_rng([2026, 2, seed])
        rows: dict[int, pl.DataFrame] = {}
        for s, br in sorted_br.items():
            pm = br["pm"].to_numpy().copy()
            for idx in groups_by_season[s]:
                pm[idx] = pm[rng.permutation(idx)]
            rows[s] = br.with_columns(pl.Series("pm", pm)).filter(pl.col("pm") >= THRESHOLD - EPS)
        n = sum(r.height for r in rows.values())
        rois.append(float(sum(r["profit"].sum() for r in rows.values()) / n) if n else float("nan"))
    out["ii_shuffle_p"] = {
        "roi_quantiles_05_50_95": [float(x) for x in np.nanpercentile(rois, [5, 50, 95])],
        "seeds": seeds,
    }
    # (iii) leave out the best 5% of bets, and the best week
    allr = pl.concat([df for df in arm_a.values() if df.height])
    n_top = int(np.ceil(0.05 * allr.height))
    kept = allr.sort("profit").head(allr.height - n_top)
    wk = allr.group_by("wk").agg(pl.col("profit").sum().alias("w")).sort("w", descending=True)
    best_week = wk["wk"][0] if wk.height else None
    nobest = allr.filter(pl.col("wk") != best_week)
    out["iii_leave_out"] = {
        "roi_without_best_5pct": float(kept["profit"].to_numpy().mean()),
        "roi_without_best_week": float(nobest["profit"].to_numpy().mean()),
        "best_week": str(best_week),
    }
    out["vi_overround_sensitivity"] = {"roi_minus_c2": float(allr["profit"].to_numpy().mean()) - c2}
    out["manual"] = [
        "v missingness audit (last_update, quote staleness)",
        "vii t60 + report window",
        "viii pulled units",
    ]
    return out


# ------------------------------------------------------------------------------ preflight


def odds_fingerprint(db: Path) -> dict[str, Any]:
    """Order-independent fingerprint of the rows ALT_LINE_70 reads (main player markets, both price
    snapshots, seasons <= 2024): the row count and the sum of 64-bit row hashes."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        mk = ", ".join(f"'{m}'" for m in MAIN_MARKETS)
        n, h = con.execute(
            "select count(*), sum(hash(game_id, player_id, market, book, snapshot_kind, side, "
            "point, price_american)::hugeint) from odds_history where season <= ? "
            f"and market in ({mk}) and snapshot_kind in ('t5','t60')",
            [MAX_SEASON],
        ).fetchone()  # type: ignore[misc]
    finally:
        con.close()
    return {"rows": int(n), "hash_sum": str(h)}


def betrivers_duplicate_keys(db: Path, season: int, kind: str) -> int:
    import duckdb

    assert season <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        mk = ", ".join(f"'{m}'" for m in MAIN_MARKETS)
        (n,) = con.execute(
            "select count(*) from (select 1 from odds_history where season = ? and "
            f"snapshot_kind = ? and book = ? and market in ({mk}) and player_id is not null "
            "group by game_id, player_id, market, side, point having count(*) > 1)",
            [season, kind, PRIMARY_BOOK],
        ).fetchone()  # type: ignore[misc]
    finally:
        con.close()
    return int(n)


def betrivers_two_sided_half_point(db: Path, season: int, kind: str) -> int:
    """BetRivers two-sided half-point rungs of a snapshot BEFORE the join to a model row (same
    de-duplication and two-sided rule as ``priced_rungs``); the difference to the joined count is
    the rule's ``void_or_unmatched`` (a DNP or an unmatched player)."""
    import duckdb

    assert season <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        mk = ", ".join(f"'{m}'" for m in MAIN_MARKETS)
        raw: pl.DataFrame = con.execute(
            "select game_id, player_id, market, side, point from odds_history where season = ? "
            f"and snapshot_kind = ? and book = ? and market in ({mk}) and player_id is not null "
            "and side in ('over','under')",
            [season, kind, PRIMARY_BOOK],
        ).pl()
    finally:
        con.close()
    key = ["game_id", "player_id", "market", "point"]
    over, _ = dedupe_dropping(raw.filter(pl.col("side") == "over").select(key), key)
    under, _ = dedupe_dropping(raw.filter(pl.col("side") == "under").select(key), key)
    both = over.join(under, on=key, how="inner")
    return both.filter(((pl.col("point") * 2).round(6) % 2) == 1).height


def run_preflight(
    pf: hc.Preflight,
    data_root: Path,
    repo: Path,
    pre_alt_backup: Path | None = None,
    lookup: Any = hc.git_prereg_shas,
    expected: dict[str, Any] | None = EXPECTED,
) -> None:
    """Rule, ALT_LADDER_HIST freeze, season guard, pinned input hashes and the data fingerprint.
    Count comparisons against the rule's gate tables run inside ``run_scoring``."""
    assert max(SEASONS) <= MAX_SEASON  # before any file is read
    pf.add("seasons <= 2024 (holdout never loaded)", max(SEASONS) <= MAX_SEASON, str(SEASONS))
    hc.check_rule(pf, repo / RULE_PATH, RULE_ID, repo, RULE_SHA, lookup)
    hc.check_rule(pf, repo / ADH_PATH, ADH_ID, repo, None, lookup)
    gate = data_root / GATE_JSON
    frozen: dict[str, str] = {}
    if gate.exists():
        frozen = json.loads(gate.read_text()).get("input_sha256", {})
    pf.add("frozen gate.json readable", bool(frozen), str(gate))
    for f in MODEL_INPUTS:
        path = data_root / f
        if not path.exists() or f not in frozen:
            pf.add(f"input {f} matches the frozen sha256", False, "missing")
            continue
        got = hc.file_sha256(path)
        pf.add(
            f"input {f} matches the frozen sha256",
            got == frozen[f],
            f"{got[:12]} vs {frozen[f][:12]}",
        )
    odds = data_root / ODDS_DB
    pf.add("odds store exists", odds.exists(), str(odds))
    if odds.exists() and pre_alt_backup is not None:
        a, b = odds_fingerprint(odds), odds_fingerprint(pre_alt_backup)
        pf.add("odds rows the rule reads equal the pre-alternate backup", a == b, f"{a} vs {b}")
    elif odds.exists():
        pf.add(
            "odds store content check (counts only; no --pre-alt-backup given)",
            True,
            "NOT VERIFIED by fingerprint; the rule's gate counts are asserted in the scoring run",
        )


# ------------------------------------------------------------------------------ run


def posted_matrices(
    t5: pl.DataFrame, frames: dict[str, StatFrame], book: str = PRIMARY_BOOK
) -> dict[str, np.ndarray]:
    """Boolean ``[n, K]`` per stat: line-rows that ``book`` posted (two-sided, half-point) at the
    primary capture."""
    out: dict[str, np.ndarray] = {}
    for st in STATS:
        sf = frames[st]
        m = np.zeros((sf.n, sf.k), dtype=bool)
        sub = t5.filter((pl.col("book") == book) & (pl.col("stat") == st))
        k = np.rint(sub["point"].to_numpy() + 0.5).astype(int)
        ok = (k >= 1) & (k <= sf.k)
        m[sub["mi"].to_numpy()[ok], k[ok] - 1] = True
        out[st] = m
    return out


def compare_expected(pf: hc.Preflight, name: str, got: Any, want: Any) -> None:
    pf.add(f"count {name} equals the rule's gate table", got == want, f"{got} vs {want}")


def run_scoring(
    data_root: Path,
    pf: hc.Preflight,
    *,
    b: int = hc.B_DEFAULT,
    with_y: bool = True,
    expected: dict[str, Any] | None = EXPECTED,
    run_attacks: bool = True,
) -> dict[str, Any]:
    """Part 1, Part 2 and the secondary family. ``with_y=False`` computes counts only (the
    ``--check-only`` dry run) and compares them with the rule's gate tables."""
    assert max(SEASONS) <= MAX_SEASON
    odds_db = data_root / ODDS_DB
    tips = load_tips(data_root)
    models = {s: load_model(data_root, s, with_y) for s in SEASONS}
    game_ids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in SEASONS}
    dates = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in SEASONS
    }
    frames = {
        s: {
            st: StatFrame(models[s].filter(pl.col("stat") == st), game_ids[s], dates[s], with_y)
            for st in STATS
        }
        for s in SEASONS
    }
    t5: dict[int, pl.DataFrame] = {}
    t60: dict[int, pl.DataFrame] = {}
    meta: dict[str, Any] = {}
    for s in SEASONS:
        for kind, store in ((PRIMARY_KIND, t5), (SENSITIVITY_KIND, t60)):
            t, m = priced_rungs(odds_db, s, tips, models[s], kind)
            pf.add(
                f"{kind} {s}: timing rule violations are 0",
                m["timing_rule_violations"] == 0,
                str(m),
            )
            if kind == PRIMARY_KIND:
                dups = betrivers_duplicate_keys(odds_db, s, kind)
                pf.add(f"BetRivers {kind} {s}: duplicated keys are 0", dups == 0, str(dups))
            t = add_indices(t, game_ids[s], dates[s])
            if with_y:
                t = with_outcome(t, models[s])
            store[s] = t
            meta[f"{kind}_{s}"] = m
            n_two = betrivers_two_sided_half_point(odds_db, s, kind)
            n_joined = t.filter(pl.col("book") == PRIMARY_BOOK).height
            meta[f"betrivers_{kind}_{s}"] = {
                "two_sided_half_point_rungs": n_two,
                "joined_to_model": n_joined,
                "void_or_unmatched": n_two - n_joined,
            }
    # counts against the rule's gate tables (no outcome needed)
    if expected:
        for s in SEASONS:
            br = t5[s].filter(pl.col("book") == PRIMARY_BOOK)
            a = br.filter(pl.col("pm") >= THRESHOLD - EPS)
            compare_expected(pf, f"arm A rungs {s}", a.height, expected["arm_a_rungs"][s])
            compare_expected(
                pf,
                f"arm A units {s}",
                a.select("game_id", "player_id", "stat").unique().height,
                expected["arm_a_units"][s],
            )
            compare_expected(
                pf, f"arm A games {s}", int(a["game_id"].n_unique()), expected["arm_a_games"][s]
            )
            compare_expected(
                pf,
                f"arm A by stat {s}",
                {st: int((a["stat"] == st).sum()) for st in STATS},
                expected["arm_a_by_stat"][s],
            )
            for label, lo, hi in SECONDARY_BINS:
                n = br.filter((pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi - EPS)).height
                compare_expected(pf, f"bin {label} rungs {s}", n, expected["bin_rungs"][label][s])
            t60a = t60[s].filter(
                (pl.col("book") == PRIMARY_BOOK) & (pl.col("pm") >= THRESHOLD - EPS)
            )
            compare_expected(
                pf, f"t60 arm A rungs {s}", t60a.height, expected["t60_arm_a_rungs"][s]
            )
            compare_expected(pf, f"model games {s}", len(game_ids[s]), expected["games"][s])
            for st in STATS:
                sf = frames[s][st]
                elig = sf.surv >= THRESHOLD - EPS
                compare_expected(
                    pf, f"P>=0.70 line-rows {s} {st}", int(elig.sum()), expected["p70_rows"][s][st]
                )
                compare_expected(
                    pf,
                    f"band line-rows {s} {st}",
                    int((elig & (sf.surv < BAND[1])).sum()),
                    expected["band_rows"][s][st],
                )
    if not with_y:
        return {"mode": "check-only", "counts_only": True, "meta": meta}
    pf.require()  # integrity first: any failed check stops before a single estimate is made
    boots = Boots(game_ids, dates, b)
    posted = {s: posted_matrices(t5[s], frames[s]) for s in SEASONS}
    res: dict[str, Any] = {
        "rule": f"{RULE_ID} sha256 {RULE_SHA}",
        "bootstrap": {"B": b, "seeds": {"game": hc.SEED_GAME, "date": hc.SEED_DATE}},
        "unstated_details": UNSTATED,
        "meta": meta,
    }
    res["part1"] = part1(frames, boots.g, boots.d, posted)
    res["part2"] = part2(t5, t60, boots, models, game_ids, dates)
    if run_attacks and res["part2"]["arm_A"]["word"] == "PROFITABLE":
        arm_a = {
            s: df.filter((pl.col("book") == PRIMARY_BOOK) & (pl.col("pm") >= THRESHOLD - EPS))
            for s, df in t5.items()
        }
        res["attacks"] = attacks(
            t5, boots, models, arm_a, res["part2"]["C2_all_betrivers_rungs"]["point"]
        )
    else:
        res["attacks"] = "not run (the primary word is not PROFITABLE)"
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path(OUT_DEFAULT))
    ap.add_argument("--pre-alt-backup", type=Path, default=None)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--b", type=int, default=hc.B_DEFAULT)
    args = ap.parse_args(argv)
    pf = hc.Preflight()
    run_preflight(pf, args.data_root, args.repo_root, args.pre_alt_backup)
    if args.check_only:
        res = run_scoring(args.data_root, pf, with_y=False)
        res["checks"] = pf.as_dicts()
        print(json.dumps(res["checks"], indent=1))
        raise SystemExit(0 if pf.ok else 2)
    pf.require()
    res = run_scoring(args.data_root, pf, b=args.b)
    res["checks"] = pf.as_dicts()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")
    print("model word:", res["part1"]["model_word"], "| arm A word:", res["part2"]["arm_A"]["word"])


if __name__ == "__main__":
    main()
