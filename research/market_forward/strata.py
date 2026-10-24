"""UNDERS Section 3 strata (descriptive, fixed at T-60, no decision weight).

* by stat: pts, reb, ast, fg3m, stl, blk, combo (already a column);
* by line position vs our mean: ``z = (line - mean) / sd``, ``sd = (q90 - q10) / 2.563`` from the
latest
  ``prop_predictions`` row with ``made_at <= tip - 60 min``; bins z <= -0.5, (-0.5, 0.5], > 0.5;
  else ``unknown``;
* by line position vs Pinnacle: the latest ``book = 'pinnacle'`` row in ``live_book_odds`` with
  ``received_at <= tip - 60 min`` and age <= 240 min; ``D = line_kalshi - line_pinnacle`` bins D <=
  -1,
  D = 0, D >= +1, else ``no_pinnacle``.

A missing input puts the contract in ``unknown`` (counted), never imputed. Nothing is refit here.
The
stored predictions are only read.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from research.market_forward.spec import ENTRY_LEAD_MIN, ENTRY_MAX_AGE_MIN

SD_DIV = 2.563
PINNACLE_MARKET = {
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "fg3m": "player_threes",
}


@dataclass
class StrataInputs:
    #: (nba game_id, player_id, stat) -> list of (made_at, mean, q10, q50, q90), sorted by made_at
    ours: dict[tuple[str, int, str], list[tuple[datetime, float, float, float, float]]] = field(
        default_factory=dict
    )
    # : (nba game_id, player_id, stat) -> list of (received_at, line, over_prob), sorted by
    # received_at
    pinnacle: dict[tuple[str, int, str], list[tuple[datetime, float, float]]] = field(
        default_factory=dict
    )
    #: Kalshi game key (event suffix) -> nba game_id
    game_ids: dict[str, str] = field(default_factory=dict)


def load_strata_inputs(
    nba_con: duckdb.DuckDBPyConnection | None,
    asof_con: duckdb.DuckDBPyConnection | None,
    game_ids: dict[str, str],
) -> StrataInputs:
    inp = StrataInputs(game_ids=dict(game_ids))
    ids = sorted(set(game_ids.values()))
    if not ids:
        return inp
    ph = ",".join("?" for _ in ids)
    if nba_con is not None:
        rows = nba_con.execute(
            "SELECT game_id, player_id, stat, made_at, mean, q10, q50, q90 FROM prop_predictions "
            f"WHERE game_id IN ({ph}) ORDER BY made_at",
            ids,
        ).fetchall()
        for gid, pid, stat, made, mean, q10, q50, q90 in rows:
            if None in (mean, q10, q50, q90):
                continue
            inp.ours.setdefault((str(gid), int(pid), str(stat)), []).append(
                (made, float(mean), float(q10), float(q50), float(q90))
            )
    if asof_con is not None:
        rev = {v: k for k, v in PINNACLE_MARKET.items()}
        rows = asof_con.execute(
            "SELECT game_id, player_id, market, received_at, point, implied_prob "
            "FROM live_book_odds "
            "WHERE book = 'pinnacle' AND side = 'over' AND player_id IS NOT NULL "
            f"AND game_id IN ({ph}) "
            "ORDER BY received_at",
            ids,
        ).fetchall()
        for gid, pid, mk, rec, pt, pr in rows:
            if mk not in rev or pt is None or pr is None:
                continue
            inp.pinnacle.setdefault((str(gid), int(pid), rev[mk]), []).append(
                (rec, float(pt), float(pr))
            )
    return inp


def map_game_ids(nba_con: duckdb.DuckDBPyConnection, keys: list[str]) -> dict[str, str]:
    """Kalshi game key (``26OCT20OKCSAS``) -> NBA game_id via the games table (date, home, away)."""
    from research.market_forward.strata_map import parse_key

    out: dict[str, str] = {}
    for k in keys:
        parsed = parse_key(k)
        if parsed is None:
            continue
        d, away, home = parsed
        row = nba_con.execute(
            "SELECT game_id FROM games WHERE game_date = ? AND home_team = ? AND away_team = ?",
            [d, home, away],
        ).fetchone()
        if row:
            out[k] = str(row[0])
    return out


def _latest_before(rows: Sequence[Any], cutoff: datetime) -> Any:
    got = None
    for r in rows:
        if r[0] <= cutoff:
            got = r
        else:
            break
    return got


@dataclass(frozen=True)
class StrataCounts:
    z_unknown: int
    pinnacle_none: int
    cross_n: int


def assign_strata(
    df: pl.DataFrame, tips: dict[str, datetime], inp: StrataInputs
) -> tuple[pl.DataFrame, StrataCounts]:
    """Add ``z_bin``, ``line_minus_q50``, ``d_bin``, ``pin_gap`` and ``cross`` to prop rows."""
    z_bin: list[str] = []
    lq: list[float | None] = []
    d_bin: list[str] = []
    gap: list[float | None] = []
    cnt: Counter[str] = Counter()
    for r in df.iter_rows(named=True):
        zb, lqv, db, gp = "unknown", None, "no_pinnacle", None
        gid = inp.game_ids.get(r["game"])
        pid = r["player_id"]
        tip = tips.get(r["game"])
        if gid is not None and pid is not None and tip is not None and r["threshold"] is not None:
            cut = tip - timedelta(minutes=ENTRY_LEAD_MIN)
            key = (gid, int(pid), str(r["stat"]))
            o = _latest_before(inp.ours.get(key, []), cut)
            if o is not None:
                _, mean, q10, q50, q90 = o
                sd = (q90 - q10) / SD_DIV
                if sd > 0:
                    z = (float(r["threshold"]) - mean) / sd
                    zb = "z<=-0.5" if z <= -0.5 else ("-0.5<z<=0.5" if z <= 0.5 else "z>0.5")
                    lqv = float(r["threshold"]) - q50
            p = _latest_before(inp.pinnacle.get(key, []), cut)
            if p is not None and (cut - p[0]).total_seconds() / 60.0 <= ENTRY_MAX_AGE_MIN:
                dd = round(float(r["threshold"]) - p[1])
                db = "D<=-1" if dd <= -1 else ("D=0" if dd == 0 else "D>=+1")
                if dd == 0:
                    gp = p[2] - (r["bid_c"] + r["ask_c"]) / 200.0
        z_bin.append(zb)
        lq.append(lqv)
        d_bin.append(db)
        gap.append(gp)
        cnt["z_unknown"] += zb == "unknown"
        cnt["pin_none"] += db == "no_pinnacle"
        cnt["cross"] += zb == "z>0.5" and db == "D=0"
    out = df.with_columns(
        pl.Series("z_bin", z_bin, dtype=pl.Utf8),
        pl.Series("line_minus_q50", lq, dtype=pl.Float64),
        pl.Series("d_bin", d_bin, dtype=pl.Utf8),
        pl.Series("pin_gap", gap, dtype=pl.Float64),
        pl.Series(
            "cross", [a == "z>0.5" and b == "D=0" for a, b in zip(z_bin, d_bin, strict=True)]
        ),
    )
    return out, StrataCounts(cnt["z_unknown"], cnt["pin_none"], cnt["cross"])


def empty_strata(df: pl.DataFrame) -> tuple[pl.DataFrame, StrataCounts]:
    """No stored inputs: every contract is ``unknown`` / ``no_pinnacle``."""
    return assign_strata(df, {}, StrataInputs())


__all__ = [
    "StrataCounts",
    "StrataInputs",
    "assign_strata",
    "empty_strata",
    "load_strata_inputs",
]
