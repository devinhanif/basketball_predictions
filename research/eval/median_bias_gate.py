# ruff: noqa: E501
"""MEDIAN_BIAS minimum-data gate and data-capture audit (rule: docs/prereg/MEDIAN_BIAS.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.median_bias_gate --data-root /Users/devin/Downloads/nba-prediction \
        [--a0-labels]

Counts and widths only. Without ``--a0-labels`` no outcome column (``row_y``, ``cal_y``, ``minutes``) is read into any statistic and
no candidate is built. ``--a0-labels`` additionally scores PRODUCTION (A0) alone (integer-support CRPS per stat and season, and
P(Y > A0 continuous median) on the DraftKings-line population) to pin the A0 reproduction numbers the harness must hit; no candidate
quantile is ever scored by this file. Seasons 2023 and 2024 only (asserted before any read); nba.duckdb is opened read-only with
``season <= 2024`` in SQL. Output: ``reports/prereg_median_bias/gate.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
MAX_SEASON = 2024
#: nba.props.lower_tail.MINUTES_EDGES / MIN_BIN, copied (equality asserted in tests/eval/test_median_bias_harness.py)
EDGES = (15.0, 25.0)
MIN_BIN = 200
CELLS = ("lt15", "15to25", "ge25")
H_CELL = 2  # the stratum of the primary calibration endpoint: min10 >= 25
TAUS = (np.arange(1, 200) - 0.5) / 199
GATE_REL = "reports/prereg_median_bias/gate.json"
#: gate thresholds, frozen with the rule
MIN_H_ROWS = 8_000  # per stat and season: stratum H rows (SE of a mid-quantile gap about 0.5 / sqrt(n) = 0.56 pp at 8,000)
MIN_CELL_ROWS = 4_000  # per stat, season and cell
MIN_GAMES = 1_000
MIN_DK_ROWS = 5_000  # per stat and season: rows with a DraftKings t60 line
MAX_FALLBACK_SHARE = 0.10  # share of (block, cell) calibration pools below MIN_BIN
COMP_FILES = {s: f"reports/lower_tail/components_{s}.npz" for s in STATS}
PAIRS_FILES = {y: f"data/ops/model_clv_pairs/pairs_{y}.parquet" for y in SEASONS}
ROW_KEYS = (
    "row_gid",
    "row_pid",
    "row_season",
    "row_c",
    "row_s",
    "row_blk",
    "row_min10",
    "row_n_prior",
)
CAL_KEYS = ("cal_z", "cal_blk", "cal_min10", "cal_c", "cal_s")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def cell_of(min10: np.ndarray) -> np.ndarray:
    """0 = min10 < 15, 1 = [15, 25), 2 = >= 25 (``np.searchsorted(EDGES, x, side='right')``, as lower_tail.minutes_bins)."""
    return np.searchsorted(np.asarray(EDGES), min10, side="right")


def load_comp(root: Path, stat: str, labels: bool = False) -> dict[str, np.ndarray]:
    """Component arrays of one stat. Label arrays (``row_y``, ``row_minutes``) are returned only when ``labels`` is True."""
    z = np.load(root / COMP_FILES[stat], allow_pickle=True)
    keep = list(ROW_KEYS + CAL_KEYS)
    if labels:
        keep += ["row_y"]
    out = {k: z[k] for k in keep}
    assert int(out["row_season"].max()) <= MAX_SEASON, "season 2025 is the frozen holdout"
    return out


def a0_grid(comp: dict[str, np.ndarray], rows: np.ndarray | None = None) -> np.ndarray:
    """A0 continuous 199-grid for the selected rows, block by block: clip(c + s * quantile(z_cal of the block), 0), monotone."""
    idx = np.arange(len(comp["row_c"])) if rows is None else np.flatnonzero(rows)
    out = np.empty((len(idx), len(TAUS)))
    blk = comp["row_blk"][idx]
    for b in np.unique(blk):
        r = blk == b
        zq = np.quantile(comp["cal_z"][comp["cal_blk"] == b], TAUS)
        q = comp["row_c"][idx][r][:, None] + comp["row_s"][idx][r][:, None] * zq[None, :]
        out[r] = np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)
    return out


def to_int(q: np.ndarray) -> np.ndarray:
    """Integer support: ceil(q - 0.5), floored at 0, kept monotone (nba.props.context_residual.to_integer_support)."""
    return np.maximum.accumulate(np.maximum(np.ceil(q - 0.5), 0.0), axis=-1)


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    """CRPS ~ 2 * mean over the 199-grid of the pinball loss (nba.eval.context_residual_eval.crps_from_quantiles)."""
    d = y[:, None] - qi
    pin = np.where(d >= 0, TAUS[None, :] * d, (TAUS[None, :] - 1.0) * d)
    return np.asarray(2.0 * pin.mean(axis=1))


def counts(comp: dict[str, np.ndarray]) -> dict[str, Any]:
    """Label-free sizes of one stat: rows, games, cells, H, and the calibration pools per (block, cell)."""
    out: dict[str, Any] = {}
    cell = cell_of(comp["row_min10"])
    for s in SEASONS:
        m = comp["row_season"] == s
        out[str(s)] = {
            "rows": int(m.sum()),
            "games": int(np.unique(comp["row_gid"][m]).size),
            "cells": {c: int((m & (cell == k)).sum()) for k, c in enumerate(CELLS)},
            "H_rows": int((m & (cell == H_CELL)).sum()),
            "blocks": int(np.unique(comp["row_blk"][m]).size),
        }
    pools: list[dict[str, Any]] = []
    for b in np.unique(comp["row_blk"]):
        cm = comp["cal_blk"] == b
        cc = cell_of(comp["cal_min10"][cm])
        n = [int((cc == k).sum()) for k in range(3)]
        pools.append(
            {
                "blk": int(b),
                "season": int(comp["row_season"][comp["row_blk"] == b][0]),
                "cal_n": int(cm.sum()),
                "cell_n": n,
                "fallback_cells": int(sum(v < MIN_BIN for v in n)),
            }
        )
    out["pools"] = pools
    out["fallback_share"] = float(np.mean([p["fallback_cells"] / 3 for p in pools]))
    out["min_pool_cell_n"] = int(min(min(p["cell_n"]) for p in pools))
    return out


def dk_population(root: Path) -> dict[tuple[str, int, str], bool]:
    """(game_id, player_id, stat) of every DraftKings t60 main-line leg. A population label known before tip; never a model input."""
    import pandas as pd

    keys: dict[tuple[str, int, str], bool] = {}
    for y in SEASONS:
        pr = pd.read_parquet(
            root / PAIRS_FILES[y], columns=["game_id", "player_id", "stat", "book", "snapshot_kind"]
        )
        pr = pr[(pr.book == "draftkings") & (pr.snapshot_kind == "t60")].drop_duplicates(
            ["game_id", "player_id", "stat"]
        )
        keys.update(
            dict.fromkeys(
                zip(pr.game_id.astype(str), pr.player_id.astype(int), pr.stat, strict=True), True
            )
        )
    return keys


def dk_mask(
    comp: dict[str, np.ndarray], stat: str, keys: dict[tuple[str, int, str], bool]
) -> np.ndarray:
    return np.array(
        [
            (str(g), int(p), stat) in keys
            for g, p in zip(comp["row_gid"], comp["row_pid"], strict=True)
        ]
    )


def capture_audit(root: Path, comps_n: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Every season held, per source: production DB (<= 2024), history DB, played-row equality with the components, name gaps."""
    import duckdb

    out: dict[str, Any] = {}
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    try:
        rows = con.execute(
            "WITH p AS (SELECT g.season, p.player_id, row_number() OVER (PARTITION BY p.player_id ORDER BY g.game_date, g.game_id) rn "
            "FROM player_game_stats_played p JOIN games g USING (game_id) WHERE g.season <= 2024 AND p.minutes > 0) "
            "SELECT season, count(*), count(*) FILTER (WHERE rn > 5) FROM p GROUP BY 1 ORDER BY 1"
        ).fetchall()
        out["production_db_played_rows_by_season"] = {
            str(r[0]): {"played": int(r[1]), "n_prior_ge_5": int(r[2])} for r in rows
        }
        gm = con.execute(
            "SELECT season, count(*) FROM games WHERE season <= 2024 GROUP BY 1 ORDER BY 1"
        ).fetchall()
        out["production_db_games"] = {str(r[0]): int(r[1]) for r in gm}
    finally:
        con.close()
    out["components_rows_equal_db_n_prior_ge_5"] = {
        s: comps_n[s]
        == {y: out["production_db_played_rows_by_season"][y]["n_prior_ge_5"] for y in comps_n[s]}
        for s in comps_n
    }
    hcon = duckdb.connect(str(root / "data/history/nba_history.duckdb"), read_only=True)
    try:
        hr = hcon.execute(
            "SELECT g.season, count(*), count(DISTINCT g.game_id) FROM player_game_stats_played p JOIN games g USING (game_id) GROUP BY 1 ORDER BY 1"
        ).fetchall()
        out["history_db_played_rows_games_by_season"] = {
            str(r[0]): {"played": int(r[1]), "games": int(r[2])} for r in hr
        }
    finally:
        hcon.close()
    out["market_unresolved_names"] = {
        str(y): len(
            (root / f"reports/odds_history/market_unresolved_names_{y}.csv")
            .read_text()
            .splitlines()
        )
        - 1
        for y in SEASONS
    }
    return out


def a0_pins(root: Path, keys: dict[tuple[str, int, str], bool]) -> dict[str, Any]:
    """A0 ONLY: integer-support CRPS per stat and season, and P(Y > A0 continuous median) on the DraftKings population."""
    out: dict[str, Any] = {"crps_int": {}, "dk_over_cont_median": {}}
    num = den = 0.0
    for st in STATS:
        comp = load_comp(root, st, labels=True)
        y = comp["row_y"].astype(float)
        q = a0_grid(comp)
        qi = to_int(q)
        cr = crps_int(qi, y)
        out["crps_int"][st] = {str(s): float(cr[comp["row_season"] == s].mean()) for s in SEASONS}
        dk = dk_mask(comp, st, keys)
        med = q[:, 99]
        hit = (y > med).astype(float)
        out["dk_over_cont_median"][st] = {"n": int(dk.sum()), "p": float(hit[dk].mean())}
        num += float(hit[dk].sum())
        den += float(dk.sum())
    out["dk_over_cont_median"]["pooled"] = {"n": int(den), "p": num / den}
    return out


def build(root: Path, a0_labels: bool) -> dict[str, Any]:
    res: dict[str, Any] = {
        "rule": "docs/prereg/MEDIAN_BIAS.md",
        "a0_labels_read": a0_labels,
        "thresholds": {
            "MIN_H_ROWS": MIN_H_ROWS,
            "MIN_CELL_ROWS": MIN_CELL_ROWS,
            "MIN_GAMES": MIN_GAMES,
            "MIN_DK_ROWS": MIN_DK_ROWS,
            "MIN_BIN": MIN_BIN,
            "MAX_FALLBACK_SHARE": MAX_FALLBACK_SHARE,
        },
    }
    res["input_sha256"] = {
        rel: sha256_file(root / rel) for rel in [*COMP_FILES.values(), *PAIRS_FILES.values()]
    }
    keys = dk_population(root)
    res["dk_legs"] = len(keys)
    st_out: dict[str, Any] = {}
    comps_n: dict[str, dict[str, int]] = {}
    for st in STATS:
        comp = load_comp(root, st)
        c = counts(comp)
        dk = dk_mask(comp, st, keys)
        c["dk_rows"] = {str(s): int((dk & (comp["row_season"] == s)).sum()) for s in SEASONS}
        st_out[st] = c
        comps_n[st] = {str(s): c[str(s)]["rows"] for s in SEASONS}
    res["stats"] = st_out
    checks: list[dict[str, Any]] = []
    for st in STATS:
        for s in SEASONS:
            c = st_out[st][str(s)]
            checks += [
                {
                    "check": f"{st} {s} H rows >= {MIN_H_ROWS}",
                    "value": c["H_rows"],
                    "pass": c["H_rows"] >= MIN_H_ROWS,
                },
                {
                    "check": f"{st} {s} smallest cell >= {MIN_CELL_ROWS}",
                    "value": min(c["cells"].values()),
                    "pass": min(c["cells"].values()) >= MIN_CELL_ROWS,
                },
                {
                    "check": f"{st} {s} games >= {MIN_GAMES}",
                    "value": c["games"],
                    "pass": c["games"] >= MIN_GAMES,
                },
                {
                    "check": f"{st} {s} DK-line rows >= {MIN_DK_ROWS}",
                    "value": st_out[st]["dk_rows"][str(s)],
                    "pass": st_out[st]["dk_rows"][str(s)] >= MIN_DK_ROWS,
                },
            ]
        checks.append(
            {
                "check": f"{st} fallback share of (block, cell) pools <= {MAX_FALLBACK_SHARE}",
                "value": round(st_out[st]["fallback_share"], 4),
                "pass": st_out[st]["fallback_share"] <= MAX_FALLBACK_SHARE,
            }
        )
    res["checks"] = checks
    res["gate_pass"] = all(c["pass"] for c in checks)
    res["capture_audit"] = capture_audit(root, comps_n)
    if a0_labels:
        res["a0_pins"] = a0_pins(root, keys)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--a0-labels", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = build(a.data_root, a.a0_labels)
    out = a.out or (Path(__file__).resolve().parents[2] / GATE_REL)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for c in res["checks"]:
        print(("PASS " if c["pass"] else "FAIL ") + f"{c['check']}: {c['value']}")
    print(f"gate_pass {res['gate_pass']}; wrote {out}")
    return 0 if res["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
