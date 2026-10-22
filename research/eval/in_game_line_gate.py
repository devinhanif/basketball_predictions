# ruff: noqa: E501
"""IN_GAME_LINE_MODEL minimum-data gate (rule: docs/prereg/IN_GAME_LINE_MODEL.md, DRAFT). Label-free.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.in_game_line_gate [--out reports/prereg_in_game_line/gate.json] [--seasons 2019 ... 2024]

Read-only: ``nba.duckdb`` / ``data/history/nba_history.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every SQL; every pbp path passes
``rule.check_game_id``. No box score is read. The state is play-by-play and stints with ``t <= checkpoint`` only. One item reads minutes AFTER a
checkpoint (the descriptive SHAPE item: starters' Q3/Q4 minutes by margin bucket); it is a minutes description, never a stat target, and no pass
bar depends on it. The power simulation draws outcomes from the B0 state probability itself (comonotone coupling inside a player-game: a
uniform per player-game, ``y = U < p``), so it is conservative about within-player-game dependence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import in_game_line_features as feat
from research.eval import in_game_line_rule as rule

OUT = Path("reports/prereg_in_game_line/gate.json")
log = feat.log


def _m(s: pl.Series) -> float:
    return float(np.mean(s.to_numpy())) if len(s) else float("nan")


def with_cells(units: pl.DataFrame) -> pl.DataFrame:
    return units.with_columns(
        (((pl.col("e") - 1) // 12).cast(pl.Int64)).alias("q"),
        (pl.col("margin").abs() >= rule.BLOWOUT_MARGIN).alias("blow"),
    ).with_columns(
        pl.col("q")
        .replace_strict({i: b for i, b in enumerate(rule.BUCKETS)}, return_dtype=pl.Utf8)
        .alias("bucket")
    )


def cell_counts(units: pl.DataFrame) -> list[dict[str, Any]]:
    u = with_cells(units)
    g = u.group_by("bucket", "blow").agg(
        pl.len().alias("rows"),
        pl.struct("game_id", "team_id", "player_id").n_unique().alias("player_games"),
        pl.col("game_id").n_unique().alias("games"),
    )
    return g.sort("bucket", "blow").to_dicts()


def b0_for(units: pl.DataFrame, stat: str) -> np.ndarray:
    return np.asarray(
        rule.b0_prob(
            stat,
            units["line"].to_numpy(),
            units["cur"].to_numpy(),
            units["minutes"].to_numpy(),
            units["e"].to_numpy(),
            units["starter"].to_numpy(),
            units["margin"].to_numpy(),
        ),
        float,
    )


def cluster_codes(units: pl.DataFrame) -> dict[str, np.ndarray]:
    wk = (
        units["game_date"].cast(pl.Date)
        - pl.Series([__import__("datetime").date(2000, 1, 3)] * units.height).cast(pl.Date)
    ).dt.total_days() // 7
    return {
        "game": feat.np_codes(units["game_id"].to_numpy()),
        "player_season": feat.np_codes(units["player_id"].to_numpy()),
        "week": feat.np_codes(wk.to_numpy()),
    }


def power(
    units: pl.DataFrame, stat: str, p0: np.ndarray, codes: dict[str, np.ndarray]
) -> dict[str, Any]:
    """Null-noise detectable effect (N_SEEDS seeds) and planted-shift positive control (POS_SEEDS seeds) for one stat on one season's units."""
    pg = feat.np_codes(
        (units["game_id"].cast(pl.Utf8) + "_" + units["player_id"].cast(pl.Utf8)).to_numpy()
    )
    ps = codes["player_season"]
    n_pg, n_ps = int(pg.max()) + 1, int(ps.max()) + 1
    ok = (p0 > 1e-4) & (p0 < 1 - 1e-4)
    lp = rule.logit(p0)
    base_idx = rule.STATS.index(stat)
    cod = {k: v[ok] for k, v in codes.items()}
    halves: list[float] = []
    binds: list[str] = []
    bybind: dict[str, list[float]] = {k: [] for k in rule.CLUSTERINGS}
    for k in range(rule.N_SEEDS):
        rng = np.random.default_rng(1000 * (base_idx + 1) + k)
        u = rng.random(n_pg)[pg]
        z = rng.standard_normal(n_ps)[ps]
        y = (u < p0).astype(float)
        p_arm = rule.sigmoid(lp + rule.NOISE_SIGMA * z)
        d = (rule.log_loss_rows(p_arm, y) - rule.log_loss_rows(p0, y))[ok]
        r = rule.binding_ci(d, cod, seed=k, n_boot=rule.N_BOOT)
        halves.append(r["half"])
        binds.append(r["binding"])
        for c in rule.CLUSTERINGS:
            bybind[c].append(r["by"][c])
    pos = []
    for k in range(rule.POS_SEEDS):
        rng = np.random.default_rng(5000 + 1000 * base_idx + k)
        u = rng.random(n_pg)[pg]
        z = rng.standard_normal(n_ps)[ps]
        p_true = rule.sigmoid(lp + rule.POS_SIGMA * z)
        y = (u < p_true).astype(float)
        d = (rule.log_loss_rows(p_true, y) - rule.log_loss_rows(p0, y))[ok]
        r = rule.binding_ci(d, cod, seed=k, n_boot=rule.N_BOOT)
        pos.append(
            {
                "point": r["point"],
                "hi": r["hi"],
                "clears": bool(r["point"] <= -rule.FLOORS[stat] and r["hi"] < 0),
            }
        )
    return {
        "rows": int(ok.sum()),
        "de_mean_half_width": float(np.mean(halves)),
        "de_sd": float(np.std(halves, ddof=1)),
        "de_min": float(np.min(halves)),
        "de_max": float(np.max(halves)),
        "binding_counts": {c: binds.count(c) for c in rule.CLUSTERINGS},
        "half_by_clustering_mean": {c: float(np.mean(v)) for c, v in bybind.items()},
        "positive_control": pos,
        "positive_control_all_clear": all(p["clears"] for p in pos),
    }


def shape_item(state: pl.DataFrame) -> dict[str, Any]:
    """Starters' minutes by |margin| bucket x period (descriptive; Devin 2026-10-21 23:40). Q3 minutes by the margin at the end of Q2 (e = 24);
    Q4 minutes by the margin at the end of Q3 (e = 36). Reads minutes after the checkpoint, never a stat."""
    e24 = state.filter((pl.col("e") == 24) & (pl.col("starter") == 1)).select(
        "game_id",
        "team_id",
        "player_id",
        pl.col("minutes").alias("m24"),
        pl.col("margin").alias("mar24"),
    )
    e36 = state.filter(pl.col("e") == 36).select(
        "game_id",
        "team_id",
        "player_id",
        pl.col("minutes").alias("m36"),
        pl.col("margin").alias("mar36"),
        pl.col("y_rem_min").alias("q4min"),
        "starter",
    )
    q3 = e24.join(e36, on=["game_id", "team_id", "player_id"], how="inner").with_columns(
        (pl.col("m36") - pl.col("m24")).alias("q3min")
    )
    q4 = e36.filter(pl.col("starter") == 1)

    def table(df: pl.DataFrame, mcol: str, vcol: str) -> list[dict[str, Any]]:
        out = []
        for lo, hi in rule.SHAPE_BUCKETS:
            a = df.filter((pl.col(mcol).abs() >= lo) & (pl.col(mcol).abs() < hi))
            row: dict[str, Any] = {
                "bucket": f"{lo}-{hi}",
                "n": a.height,
                "mean_minutes": _m(a[vcol]) if a.height else None,
            }
            if lo >= rule.BLOWOUT_MARGIN:
                for side, cond in (("leading", pl.col(mcol) > 0), ("trailing", pl.col(mcol) < 0)):
                    b = a.filter(cond)
                    row[side] = {
                        "n": b.height,
                        "mean_minutes": _m(b[vcol]) if b.height else None,
                    }
            out.append(row)
        return out

    t3, t4 = table(q3, "mar24", "q3min"), table(q4, "mar36", "q4min")

    def ratio(t: list[dict[str, Any]]) -> float | None:
        hi_, lo_ = t[2]["mean_minutes"], t[0]["mean_minutes"]
        return None if hi_ is None or not lo_ else float(hi_ / lo_)

    return {
        "q3_by_margin_at_e24": t3,
        "q4_by_margin_at_e36": t4,
        **rule.shape_check(ratio(t3), ratio(t4)),
    }


def run_season(season: int, root: Path) -> dict[str, Any]:
    rule.check_season(season)
    games = feat.read_games(season, root)
    ids = games["game_id"].to_list()
    log(f"{season}: {len(ids)} regular-season games; reading pbp")
    pbp = feat.read_pbp(ids, root)
    stints = feat.read_stints(season, root)
    comp = feat.completeness(games, pbp, stints)
    named, resolved_before = feat.ast_unresolved(pbp, feat.stat_events(pbp))
    _, resolved = feat.ast_unresolved(pbp, feat.stat_events(pbp, feat.stint_rows(stints)))
    log(f"{season}: completeness full {_m(comp['full']):.4f}; building state")
    state = feat.state_frame(season, root, True, games, pbp, stints)
    out: dict[str, Any] = {
        "games": len(ids),
        "pbp_files": int(pbp["game_id"].n_unique()),
        "completeness": {
            c: _m(comp[c]) for c in ("pbp_ok", "minutes_ok", "pts_ok", "fouls_ok", "full")
        },
        "ast_named": named,
        "ast_resolved_before_repair": resolved_before,
        "ast_resolved_share_before_repair": resolved_before / named if named else None,
        "ast_resolved": resolved,
        "ast_resolved_share": resolved / named if named else None,
        "state_rows": state.height,
        "shape": shape_item(state),
        "stats": {},
    }
    state_nf = state.drop("y_rem_min")
    pins = {
        f"PIN_GAMES_{season}": feat.sha_frame(games),
        f"PIN_STINTS_{season}": feat.sha_frame(
            stints.with_columns(
                pl.col("players")
                .cast(pl.List(pl.Int32))
                .list.sort()
                .list.eval(pl.element().cast(pl.Utf8))
                .list.join("-")
            )
        ),
        f"PIN_STATE_{season}": feat.sha_frame(state_nf),
    }
    for stat in rule.STATS:
        units = feat.expand_units(state_nf, stat)
        cells = cell_counts(units)
        entry: dict[str, Any] = {"rows": units.height, "cells": cells}
        if season in (rule.SELECT, rule.REPORT):
            p0 = b0_for(units, stat)
            codes = cluster_codes(units)
            log(f"{season} {stat}: {units.height} units; power")
            entry["power"] = power(units, stat, p0, codes)
        out["stats"][stat] = entry
    out["pins"] = pins
    return out


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    chk: dict[str, Any] = {}
    for s, r in res["seasons"].items():
        season = int(s)
        bar = (
            rule.COMPLETE_MIN_REPORT
            if season in (rule.SELECT, rule.REPORT)
            else rule.COMPLETE_MIN_TRAIN
        )
        chk[f"{s}_complete_full>={bar}"] = r["completeness"]["full"] >= bar
        by = {(c["bucket"], bool(c["blow"])): c for c in r["stats"][rule.PRIMARY]["cells"]}
        for key in rule.REQUIRED_CELLS:
            c = by.get(key)
            chk[f"{s}_cell_{key[0]}_{'blow' if key[1] else 'normal'}"] = bool(
                c and rule.gate_cell_ok(c)
            )
        if "power" in r["stats"][rule.PRIMARY]:
            pw = r["stats"][rule.PRIMARY]["power"]
            chk[f"{s}_de<={rule.GATE_DE_BAR}"] = pw["de_mean_half_width"] <= rule.GATE_DE_BAR
            chk[f"{s}_positive_control"] = pw["positive_control_all_clear"]
    for s, tr in res["truncation_identity"].items():
        chk[f"{s}_truncation_identity"] = bool(tr["identical"])
    for s, pf in res["planted_future"].items():
        chk[f"{s}_planted_future"] = bool(pf["identical_before_cut"] and pf["later_rows_differ"])
    return {
        "checks": chk,
        "pass": all(chk.values()),
        "failed": [k for k, v in chk.items() if not v],
        "word": "PASS" if all(chk.values()) else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--seasons", type=int, nargs="*", default=list(rule.ALL_SEASONS))
    a = ap.parse_args(argv)
    root = feat.data_root()
    res: dict[str, Any] = {
        "rule": rule.ID,
        "status": "gate run; label-free; DRAFT rule",
        "seasons": {},
        "planted_future": {},
        "truncation_identity": {},
        "versions": rule.versions(),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for s in a.seasons:
        res["seasons"][str(s)] = run_season(s, root)
        res["truncation_identity"][str(s)] = feat.truncation_identity(s, root)
        if s in (rule.SELECT, rule.REPORT):
            res["planted_future"][str(s)] = feat.planted_future(s, root)
        out.write_text(json.dumps(res, indent=1, default=str))  # per-stage checkpoint
        log(f"{s} done; checkpoint written")
    res["verdict"] = verdict(res)
    out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["verdict"], indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
