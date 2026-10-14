# ruff: noqa: E501
"""Proof P2 (docs/prereg/AVG_SHADOW_LIVE_2.md): the LIVE sequence exporter equals the T478 exporter, bit for bit.

Reads ONE database (default: a copy of nba.duckdb; never the live file) and only game dates up to 2024-12-31 (season <= 2024):

1. parity   : ``nba.daily.avg_shadow.sequences.build_export`` (the live code, default target selection) vs
              ``research.features.seq_full_mix_rt_export.build_cur_rt(2024)`` (the T478 exporter): every array bit-identical
              (ctx compared on the 31 pre-era columns; the 3 era columns of the T478 export must be all zero on these rows).
2. planted future: rows built with ONLY games before a cutoff D equal the same (game, player) rows built with every later game of the
              season present (adding later games changes no earlier row).
3. live path : the slate of a past date D rebuilt the way the daily job builds it (``build_live_export``: games strictly before D plus
              placeholder rows for the players who then played, real tips, reports with ``as_of <= tip - 60``) equals the T478 rows of D.

    .venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/p2_parity.py --db data/rehearsal/oracle_a_full.duckdb [--date 2024-03-05]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from nba.daily.avg_shadow import sequences as L
from nba.features.game_tipoff import build_game_tipoff
from nba.features.injury_report import ReportTriggerConfig
from research.features import seq_full_mix_rt_export as X
from research.features.player_sequences import SeqExport

ROOT = Path(__file__).resolve().parents[4]
ARRAYS = (
    "seq",
    "mask",
    "opp_step",
    "ctx",
    "player_id",
    "team_idx",
    "opp_idx",
    "date_days",
    "season",
    "game_id",
    "y",
    "base",
    "n_prior",
)
N_PRE_ERA = 31


def key(a: dict[str, np.ndarray]) -> pd.MultiIndex:
    return pd.MultiIndex.from_arrays([a["game_id"].astype(str), a["player_id"].astype(np.int64)])


def compare(
    a: dict[str, np.ndarray], b: dict[str, np.ndarray], label: str, skip: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Row-align b to a by (game_id, player_id) and compare every array exactly (bit pattern for floats)."""
    ka, kb = key(a), key(b)
    pos = kb.get_indexer(ka)
    out: dict[str, Any] = {
        "label": label,
        "n_a": int(len(ka)),
        "n_b": int(len(kb)),
        "missing_in_b": int((pos < 0).sum()),
        "diff": {},
    }
    ok = pos >= 0
    for name in (n for n in ARRAYS if n not in skip):
        x, y = np.asarray(a[name])[ok], np.asarray(b[name])[pos[ok]]
        if name == "ctx":
            x, y = x[:, :N_PRE_ERA], y[:, :N_PRE_ERA]
        same = (
            x.view(np.uint8).tobytes() == y.view(np.uint8).tobytes()
            if x.dtype.kind == "f"
            else np.array_equal(x, y)
        )
        out["diff"][name] = {"identical": bool(same)}
        if not same and x.dtype.kind in "fiu":
            out["diff"][name]["rows_differ"] = int(
                (x.reshape(len(x), -1) != y.reshape(len(y), -1)).any(axis=1).sum()
            )
    out["identical"] = bool(
        out["missing_in_b"] == 0 and all(v["identical"] for v in out["diff"].values())
    )
    return out


def live_style_cfg(max_season: int) -> L.SeqConfig:
    return L.SeqConfig(report=ReportTriggerConfig(tip_source="real"), max_season=max_season)


def mine_parity(db: str, max_season: int) -> SeqExport:
    raw = L.load_raw(db, max_season=max_season)
    import polars as pl

    from nba.props.context_residual import asof_elo_margin

    g = pl.from_pandas(raw.games).with_columns(pl.col("game_date").cast(pl.Date))
    raw.elo = asof_elo_margin(g, L.load_elo()).to_pandas()
    raw.tips, _ = X.tip_table(X.CUR_SCHEDULE, raw.games)
    return L.build_export(raw, live_style_cfg(max_season))  # type: ignore[return-value]


def per_date(
    db: str, meta: pd.DataFrame, sub: dict[str, np.ndarray], d: pd.Timestamp
) -> dict[str, Any]:
    """Planted-future and live-slate comparisons for one past date ``d`` (season <= 2024)."""
    import polars as pl

    from nba.props.context_residual import asof_elo_margin

    out: dict[str, Any] = {}
    window = meta[(meta["game_date"] < d) & (meta["game_date"] >= d - pd.Timedelta(days=14))]
    ids = set(window["game_id"].astype(str))
    raw = L.load_raw(db, max_season=2024, before_date=d.date().isoformat())
    g = pl.from_pandas(raw.games).with_columns(pl.col("game_date").cast(pl.Date))
    raw.elo = asof_elo_margin(g, L.load_elo()).to_pandas()
    raw.tips, _ = X.tip_table(X.CUR_SCHEDULE, raw.games)
    early = L.build_export(raw, live_style_cfg(2024), target_game_ids=ids)
    m = np.isin(sub["game_id"].astype(str), list(ids))
    out["planted_future"] = compare(
        {k: v[m] for k, v in sub.items()},
        early.arrays,
        f"games before {d.date()} only vs all later games present",
    )
    day = meta[meta["game_date"] == d]
    con = duckdb.connect(db, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, home_team, away_team FROM games WHERE game_date = ?", [d.date()]
        ).df()
        tips = build_game_tipoff(X.CUR_SCHEDULE)
        tips_et = {
            str(r["game_id"]): r["tip_et"]
            for r in tips.filter(
                tips["game_id"].is_in(list(games["game_id"].astype(str)))
            ).iter_rows(named=True)
        }
        t0 = time.time()
        live = L.build_live_export(
            con,
            d.date(),
            int(day["season"].iloc[0]),
            games,
            tips_et,
            day[["game_id", "player_id", "team_id"]],
            pd.Timestamp("2100-01-01"),
            L.load_elo(),
        )
        out["live_slate_seconds"] = round(time.time() - t0, 1)
    finally:
        con.close()
    sub_d = np.isin(sub["game_id"].astype(str), list(day["game_id"].astype(str)))
    out["live_slate"] = compare(
        {k: v[sub_d] for k, v in sub.items()},
        live.arrays,
        f"live placeholder export of {d.date()} vs T478 rows (y is a placeholder 0 live, not compared)",
        skip=("y",),
    )
    out["live_slate"]["tip_source"] = "real"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument(
        "--date",
        action="append",
        default=None,
        help="past slate dates of season <= 2024 (repeatable)",
    )
    ap.add_argument("--out", default=str(ROOT / "reports/prereg_avg_shadow_live/p2.json"))
    a = ap.parse_args(argv)
    a.date = a.date or ["2023-10-25", "2024-01-15", "2024-03-05", "2024-04-10"]
    res: dict[str, Any] = {"db": a.db, "max_season": 2024}
    t0 = time.time()
    theirs, info = X.build_cur_rt(2024, a.db)
    res["t478_seconds"] = round(time.time() - t0, 1)
    assert int(theirs.meta["season"].max()) <= 2024
    res["era_columns_all_zero"] = bool((theirs.arrays["ctx"][:, N_PRE_ERA:] == 0).all())
    t0 = time.time()
    mine = mine_parity(a.db, 2024)
    res["live_code_seconds"] = round(time.time() - t0, 1)
    res["parity"] = compare(theirs.arrays, mine.arrays, "live exporter vs T478 exporter, 2022-2024")

    meta = theirs.meta.assign(game_date=pd.to_datetime(theirs.meta["game_date"]))
    sub = {k: np.asarray(v) for k, v in theirs.arrays.items() if k != "stat_scale"}
    res["dates"] = {}
    for date_s in a.date:
        res["dates"][date_s] = per_date(a.db, meta, sub, pd.Timestamp(date_s))
    res["pass"] = bool(
        res["era_columns_all_zero"]
        and res["parity"]["identical"]
        and all(
            v["planted_future"]["identical"] and v["live_slate"]["identical"]
            for v in res["dates"].values()
        )
    )
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2, default=str))
    print(
        json.dumps(
            {
                k: (
                    v
                    if not isinstance(v, dict)
                    else {kk: vv for kk, vv in v.items() if kk != "diff"}
                )
                for k, v in res.items()
            },
            indent=2,
            default=str,
        )
    )
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
