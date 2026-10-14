"""ALT_LINE_70 minimum-data gate (rule: docs/prereg/ALT_LINE_70.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.alt_line_70_gate \
        --data-root <checkout holding data/ and reports/>

Counts only. No outcome column (``y``) is read from any frame, no hit rate, no return, no EV is
computed: the gate sizes the bet sets and the confidence intervals before the rule is frozen
(CLAUDE.md non-negotiable 8). The harness that scores the rule does not exist yet and is written
only after the freeze.

Inputs (read-only; season <= 2024 asserted before any read):
* ``reports/odds_history/model_rows_{2023,2024}.parquet``: the stored production walk-forward
  context-residual forecast, ``p_ge[k-1] = P(Y >= k)`` on the integer support (played rows only);
* ``data/odds/odds_history.duckdb``: book quotes (table ``odds_history``), snapshots ``t5``
  (primary: the stored capture at or after the model's T-60 cutoff and before tip) and ``t60``
  (sensitivity: before the cutoff);
* ``data/ops/model_clv_pairs/pairs_{2023,2024}.parquet``: only ``game_id`` and ``tipoff_utc``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

MAX_SEASON = 2024
SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
#: the model-based "main line" distance unit in stat points (pts: 2, others: 1)
DIST_UNIT = {"pts": 2.0, "reb": 1.0, "ast": 1.0, "fg3m": 1.0}
THRESHOLD = 0.70
BINS: tuple[tuple[float, float], ...] = (
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.85),
    (0.85, 0.90),
    (0.90, 0.95),
    (0.95, 1.0001),
)
BAND = (0.70, 0.90)
#: secondary family (v3): four disjoint P bins labelled by their midpoint; the PRIMARY question is
#: still the single set P >= 0.70
SECONDARY_BINS: tuple[tuple[str, float, float], ...] = (
    ("55", 0.50, 0.60),
    ("65", 0.60, 0.70),
    ("75", 0.70, 0.80),
    ("85", 0.80, 0.90),
)
DIST_EDGES: tuple[tuple[float, float], ...] = (
    (-1e9, 0.0),
    (0.0, 1.0),
    (1.0, 2.0),
    (2.0, 4.0),
    (4.0, 1e9),
)
CUTOFF_MIN = 60  # the model's report cutoff: real tip minus this many minutes
PRIMARY_KIND = "t5"  # price snapshot at or after the cutoff, before tip
SENSITIVITY_KIND = "t60"  # price snapshot before the cutoff (model may know newer news)
EPS = 1e-12
LADDER_MIN_POINTS = 3
LADDER_MIN_SHARE = 0.10
PRIMARY_BOOK = "betrivers"
DUPLICATE_BOOK = "unibet_us"
PLAN_BETS_PER_WEEK = 10
INPUT_FILES = (
    "reports/odds_history/model_rows_2023.parquet",
    "reports/odds_history/model_rows_2024.parquet",
    "data/ops/model_clv_pairs/pairs_2023.parquet",
    "data/ops/model_clv_pairs/pairs_2024.parquet",
    "data/odds/odds_history.duckdb",
)


def survival_matrix(p_ge: list[list[float]]) -> np.ndarray:
    """Ragged ``P(Y >= k)`` lists to a zero-padded ``[n, K]`` matrix (column ``k-1`` holds
    ``P(Y >= k)``)."""
    width = max((len(v) for v in p_ge), default=0)
    out = np.zeros((len(p_ge), width), dtype=np.float64)
    for i, v in enumerate(p_ge):
        out[i, : len(v)] = v
    return out


def p_over_at(surv: np.ndarray, row: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Model ``P(Y > point)`` for half-point lines: ``point = k - 0.5`` gives ``P(Y >= k)``."""
    k = np.rint(point + 0.5).astype(int)
    width = surv.shape[1]
    inside = (k >= 1) & (k <= width)
    vals = surv[row, np.clip(k - 1, 0, max(width - 1, 0))]
    return np.where(inside, vals, np.where(k > width, 0.0, 1.0))


def cluster_halfwidth(p: float, per_game: np.ndarray) -> float:
    """Conservative 95% half-width of a mean of Bernoulli(p) draws when every row of a game is
    perfectly correlated with the others of that game:
    ``1.96 sqrt(p(1-p)) sqrt(sum_g n_g^2) / N``."""
    n = float(per_game.sum())
    if n == 0:
        return float("nan")
    return float(1.96 * np.sqrt(p * (1 - p)) * np.sqrt((per_game**2).sum()) / n)


def decimal_odds(american: np.ndarray) -> np.ndarray:
    a = american.astype(float)
    return np.where(a > 0, 1.0 + a / 100.0, 1.0 + 100.0 / np.abs(a))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------ Part 1 counts


def part1_counts(stat: str, game_id: np.ndarray, surv: np.ndarray) -> dict[str, Any]:
    """Line-rows with model P(over) >= 0.70, by P bin and (band only) distance from the model's
    own main line (the half-point line whose P is closest to 0.5, ties to the lower line)."""
    _, gi = np.unique(game_id, return_inverse=True)
    n_games = int(gi.max()) + 1 if gi.size else 0
    elig = surv >= THRESHOLD - EPS
    k_main = np.argmin(np.abs(surv - 0.5), axis=1) + 1
    ks = np.arange(1, surv.shape[1] + 1)[None, :]
    dist = (k_main[:, None] - ks) / DIST_UNIT[stat]

    def cell(mask: np.ndarray) -> dict[str, Any]:
        per_game = np.bincount(gi, weights=mask.sum(axis=1), minlength=n_games)
        n = int(mask.sum())
        p = float(surv[mask].mean()) if n else float("nan")
        return {
            "line_rows": n,
            "player_games": int(mask.any(axis=1).sum()),
            "games": int((per_game > 0).sum()),
            "mean_p": p,
            "halfwidth_cons": cluster_halfwidth(p, per_game) if n else float("nan"),
        }

    out: dict[str, Any] = {
        "player_games": int(surv.shape[0]),
        "games": n_games,
        "K": int(surv.shape[1]),
        "all_p70": cell(elig),
        "p_equals_1": int((surv >= 1 - EPS).sum()),
        "bins": {},
        "band_70_90": cell(elig & (surv < BAND[1])),
        "band_by_distance_units": {},
    }
    for lo, hi in BINS:
        out["bins"][f"{lo:.2f}-{min(hi, 1.0):.2f}"] = cell(elig & (surv >= lo - EPS) & (surv < hi))
    band = elig & (surv < BAND[1])
    for a, b in DIST_EDGES:
        label = f"({a:g},{b:g}]" if a > -1e8 else f"(-inf,{b:g}]"
        out["band_by_distance_units"][label] = cell(band & (dist > a) & (dist <= b))
    return out


def secondary_part1(game_id: np.ndarray, surv: np.ndarray) -> dict[str, Any]:
    """Line-rows per secondary bin ([lo, hi), every line of the support) with the conservative
    hit-rate half-width (game clusters; a game's lines across bins share the same clusters)."""
    _, gi = np.unique(game_id, return_inverse=True)
    n_games = int(gi.max()) + 1 if gi.size else 0
    out: dict[str, Any] = {}
    for label, lo, hi in SECONDARY_BINS:
        mask = (surv >= lo - EPS) & (surv < hi - EPS)
        per_game = np.bincount(gi, weights=mask.sum(axis=1), minlength=n_games)
        n = int(mask.sum())
        p = float(surv[mask].mean()) if n else float("nan")
        out[label] = {
            "line_rows": n,
            "player_games": int(mask.any(axis=1).sum()),
            "games": int((per_game > 0).sum()),
            "mean_p": p,
            "halfwidth_cons": cluster_halfwidth(p, per_game) if n else float("nan"),
        }
    return out


def pool_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-season ``summarize_bets`` results: the cluster sums add across seasons, so
    ``S_s = (hw_s N_s / 1.96)^2`` and the pooled half-width is ``1.96 sqrt(sum S) / sum N``."""
    parts = [x for x in parts if x.get("rungs", 0) > 0]
    n = sum(int(x["rungs"]) for x in parts)
    if n == 0:
        return {"rungs": 0}
    s_cons = sum((x["roi_halfwidth_cons"] * x["rungs"] / 1.96) ** 2 for x in parts)
    s_ind = sum((x["roi_halfwidth_independent"] * x["rungs"] / 1.96) ** 2 for x in parts)
    return {
        "rungs": n,
        "units_player_game_stat": sum(int(x["units_player_game_stat"]) for x in parts),
        "games": sum(int(x["games"]) for x in parts),
        "dates": sum(int(x["dates"]) for x in parts),
        "roi_halfwidth_cons": round(float(1.96 * np.sqrt(s_cons) / n), 4),
        "roi_halfwidth_independent": round(float(1.96 * np.sqrt(s_ind) / n), 4),
    }


# ------------------------------------------------------------------------------ Part 2 counts


def summarize_bets(e: pl.DataFrame) -> dict[str, Any]:
    """Counts and a model-only half-width for a priced bet set. ``dec`` = decimal over price,
    ``pm`` = model P(over), ``ovr`` = book overround at that rung, ``wk`` = Monday-start week."""
    if e.is_empty():
        return {"rungs": 0}
    sd = (e["dec"] * np.sqrt(e["pm"] * (1 - e["pm"]))).to_numpy()
    by_game = (
        e.with_columns(pl.Series("sd", sd)).group_by("game_id").agg(pl.col("sd").sum().alias("s"))
    )
    wk_units = e.group_by("wk").agg(
        pl.struct("game_id", "player_id", "stat").n_unique().alias("units")
    )["units"]
    return {
        "rungs": e.height,
        "units_player_game_stat": e.select("game_id", "player_id", "stat").unique().height,
        "games": int(e["game_id"].n_unique()),
        "dates": int(e["game_date"].n_unique()),
        "weeks": int(wk_units.len()),
        "weeks_with_ge_10_units": int((wk_units >= PLAN_BETS_PER_WEEK).sum()),
        "decimal_min_med_max": [
            round(float(e["dec"].min()), 3),  # type: ignore[arg-type]
            round(float(e["dec"].median()), 3),  # type: ignore[arg-type]
            round(float(e["dec"].max()), 3),  # type: ignore[arg-type]
        ],
        "weekly_units_median": float(wk_units.median()),  # type: ignore[arg-type]
        "mean_model_p": round(float(e["pm"].mean()), 4),  # type: ignore[arg-type]
        # what the MODEL claims (P x decimal price - 1), not a realised return
        "model_claimed_ev_mean": round(
            float((e["pm"] * e["dec"] - 1.0).mean()),  # type: ignore[arg-type]
            4,
        ),
        "mean_overround": round(float(e["ovr"].mean()), 4),  # type: ignore[arg-type]
        "roi_halfwidth_independent": round(float(1.96 * np.sqrt((sd**2).sum()) / e.height), 4),
        "roi_halfwidth_cons": round(
            float(1.96 * np.sqrt((by_game["s"].to_numpy() ** 2).sum()) / e.height), 4
        ),
        "by_stat": {s: int((e["stat"] == s).sum()) for s in STATS},
    }


def dedupe_dropping(df: pl.DataFrame, key: list[str]) -> tuple[pl.DataFrame, int]:
    """Drop EVERY row of a duplicated key (no arbitrary survivor); return the frame and the count
    of rows dropped."""
    dup = df.select(key).is_duplicated()
    return df.filter(~dup), int(dup.sum())


def snapshot_violations(tw: pl.DataFrame, kind: str) -> int:
    """Rows breaking the timing rule. ``t5``: snapshot must satisfy tip - 60 <= ts < tip (never
    before the model's cutoff). ``t60`` (sensitivity): ts <= tip - 60, the old as-of check."""
    cut = pl.col("tipoff_utc") - pl.duration(minutes=CUTOFF_MIN)
    if kind == PRIMARY_KIND:
        bad = (pl.col("snapshot_ts") < cut) | (pl.col("snapshot_ts") >= pl.col("tipoff_utc"))
    else:
        bad = pl.col("snapshot_ts") > cut
    return tw.filter(pl.col("tipoff_utc").is_null() | bad).height


def priced_rungs(
    odds_db: Path, season: int, tips: pl.DataFrame, model: pl.DataFrame, kind: str = PRIMARY_KIND
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Two-sided half-point rungs of snapshot ``kind`` joined to the stored model P(over); also
    the timing check against the real tip."""
    import duckdb

    assert season <= MAX_SEASON
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        raw: pl.DataFrame = con.execute(
            "select game_id, player_id, market, book, snapshot_ts, side, point, price_american "
            "from odds_history where season = ? and snapshot_kind = ? "
            "and market in ('player_points','player_rebounds','player_assists','player_threes') "
            "and player_id is not null and side in ('over','under')",
            [season, kind],
        ).pl()
    finally:
        con.close()
    raw = raw.with_columns(pl.col("market").replace_strict(MARKET_STAT).alias("stat"))
    key = ["game_id", "player_id", "stat", "book", "point"]
    over = raw.filter(pl.col("side") == "over").rename({"price_american": "po"}).drop("side")
    under = raw.filter(pl.col("side") == "under").select(*key, pl.col("price_american").alias("pu"))
    over, dup_over = dedupe_dropping(over, key)
    under, dup_under = dedupe_dropping(under, key)
    tw = over.join(under, on=key, how="inner")
    n_two_sided = tw.height
    tw = tw.filter(((pl.col("point") * 2).round(6) % 2) == 1)  # half-point lines only
    tw = tw.join(tips, on="game_id", how="left")
    late = snapshot_violations(tw, kind)
    lead = (
        (tw["tipoff_utc"] - tw["snapshot_ts"]).dt.total_seconds().drop_nulls() / 60.0
    ).to_numpy()
    parts: list[pl.DataFrame] = []
    for stat in STATS:
        d = model.filter(pl.col("stat") == stat)
        surv = survival_matrix(d["p_ge"].to_list())
        idx = d.select("game_id", "player_id", "game_date").with_row_index("mi")
        t = tw.filter(pl.col("stat") == stat).join(idx, on=["game_id", "player_id"], how="inner")
        pm = p_over_at(surv, t["mi"].to_numpy(), t["point"].to_numpy())
        parts.append(t.with_columns(pl.Series("pm", pm)))
    t = pl.concat(parts)
    dec_o = decimal_odds(t["po"].to_numpy())
    dec_u = decimal_odds(t["pu"].to_numpy())
    t = t.with_columns(
        pl.Series("dec", dec_o),
        pl.Series("ovr", 1.0 / dec_o + 1.0 / dec_u - 1.0),
        pl.col("game_date").dt.truncate("1w").alias("wk"),
    )
    meta = {
        "two_sided_rungs": n_two_sided,
        "half_point_rungs_joined_to_model": t.height,
        "timing_rule_violations": late,
        "duplicate_key_rows_dropped": {"over": dup_over, "under": dup_under},
        "lead_minutes_min_med_max": [
            round(float(lead.min()), 2),
            round(float(np.median(lead)), 2),
            round(float(lead.max()), 2),
        ],
    }
    return t, meta


def ladder_cells(t: pl.DataFrame) -> list[dict[str, Any]]:
    """(book, stat) cells where >= 10% of player-stat-games have >= 3 distinct half-point lines."""
    per_unit = t.group_by("book", "stat", "game_id", "player_id").agg(
        pl.col("point").n_unique().alias("np")
    )
    cells = (
        per_unit.group_by("book", "stat")
        .agg(
            pl.len().alias("units"),
            (pl.col("np") >= LADDER_MIN_POINTS).mean().alias("share_ge3"),
            pl.col("np").mean().alias("mean_points"),
        )
        .filter(pl.col("share_ge3") >= LADDER_MIN_SHARE)
        .sort("book", "stat")
    )
    return [
        {
            "book": r["book"],
            "stat": r["stat"],
            "units": r["units"],
            "share_ge3": round(r["share_ge3"], 3),
            "mean_points": round(r["mean_points"], 2),
        }
        for r in cells.to_dicts()
    ]


def best_price(e: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, player, stat, line): the highest decimal over price, ties to the
    alphabetically first book. Deterministic; uses no outcome."""
    return e.sort(
        ["game_id", "player_id", "stat", "point", "dec", "book"],
        descending=[False, False, False, False, True, False],
    ).unique(["game_id", "player_id", "stat", "point"], keep="first", maintain_order=True)


# ------------------------------------------------------------------------------ entrypoint


def run_gate(data_root: Path, hash_inputs: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "rule": "docs/prereg/ALT_LINE_70.md (DRAFT)",
        "seasons": list(SEASONS),
    }
    assert max(SEASONS) <= MAX_SEASON
    if hash_inputs:
        result["input_sha256"] = {f: sha256_file(data_root / f) for f in INPUT_FILES}
    tips = pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "tipoff_utc"],
            )
            for s in SEASONS
        ]
    ).unique("game_id")
    part1: dict[str, Any] = {}
    part2: dict[str, Any] = {}
    for season in SEASONS:
        # the outcome column y is deliberately not requested
        m = pl.read_parquet(
            data_root / f"reports/odds_history/model_rows_{season}.parquet",
            columns=["game_id", "player_id", "stat", "game_date", "minutes", "p_ge"],
        )
        assert "y" not in m.columns
        part1[str(season)] = {}
        for stat in STATS:
            d = m.filter(pl.col("stat") == stat)
            surv = survival_matrix(d["p_ge"].to_list())
            c = part1_counts(stat, d["game_id"].to_numpy(), surv)
            c["rows_with_minutes_null_or_le_0"] = int(
                (d["minutes"].is_null() | (d["minutes"] <= 0)).sum()
            )
            c["secondary_bins"] = secondary_part1(d["game_id"].to_numpy(), surv)
            part1[str(season)][stat] = c
        for kind in (PRIMARY_KIND, SENSITIVITY_KIND):
            t, meta = priced_rungs(
                data_root / "data/odds/odds_history.duckdb", season, tips, m, kind
            )
            e = t.filter(pl.col("pm") >= THRESHOLD - EPS)
            arm_a = e.filter(pl.col("book") == PRIMARY_BOOK)
            arm_a_dup = e.filter(pl.col("book") == DUPLICATE_BOOK)
            shared = arm_a.join(
                arm_a_dup.select(
                    "game_id", "player_id", "stat", "point", pl.col("po").alias("po_u")
                ),
                on=["game_id", "player_id", "stat", "point"],
                how="inner",
            )
            sec = {
                label: summarize_bets(
                    t.filter(
                        (pl.col("book") == PRIMARY_BOOK)
                        & (pl.col("pm") >= lo - EPS)
                        & (pl.col("pm") < hi - EPS)
                    )
                )
                for label, lo, hi in SECONDARY_BINS
            }
            part2.setdefault(kind, {})[str(season)] = {
                **meta,
                "secondary_bins_arm_A": sec,
                "ladder_cells": ladder_cells(t),
                "arm_A_primary_book": {"book": PRIMARY_BOOK, **summarize_bets(arm_a)},
                "arm_A_duplicate_book": {"book": DUPLICATE_BOOK, **summarize_bets(arm_a_dup)},
                "primary_vs_duplicate_shared_rungs": shared.height,
                "primary_vs_duplicate_identical_price_share": (
                    round(float((shared["po"] == shared["po_u"]).mean()), 3)  # type: ignore[arg-type]
                    if shared.height
                    else None
                ),
                "arm_B_best_price_any_book": summarize_bets(best_price(e)),
                "rungs_p70_by_book": {
                    r["book"]: r["n"]
                    for r in e.group_by("book").agg(pl.len().alias("n")).sort("n").to_dicts()
                },
            }
    for kind in part2:
        seasons = [part2[kind][str(y)] for y in SEASONS]
        part2[kind]["pooled"] = {
            "arm_A_primary_book": pool_summaries([x["arm_A_primary_book"] for x in seasons]),
            **{
                f"secondary_{label}": pool_summaries(
                    [x["secondary_bins_arm_A"][label] for x in seasons]
                )
                for label, _, _ in SECONDARY_BINS
            },
        }
    result["part1"] = part1
    result["part2"] = part2
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_alt_line_70/gate.json"))
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, hash_inputs=not args.no_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
