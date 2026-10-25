"""Dated data-quality exclusions for the forward log (read-only).

``ops/forward_exclusions.yaml`` lists entries; this module turns an entry into the exact
``market_at_prediction`` keys it excludes, by rule, against ``market_asof.duckdb`` opened read-only.
Nothing here writes, withdraws a forecast or touches an outcome score: an exclusion removes a row
from the model-versus-market comparison only (docs/DAILY_PIPELINE.md, "Data-quality exclusions").

    python -m nba.ops.exclusions check            # per entry: rows selected, by kind and game
    python -m nba.ops.exclusions keys --id ID     # the keys, one JSON object per line
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXCLUSIONS = ROOT / "ops" / "forward_exclusions.yaml"
MARKET_DB = ROOT / "data" / "markets" / "market_asof.duckdb"
GAME_TARGET = "win_prob_home"


@dataclass(frozen=True)
class Exclusion:
    id: str
    reason: str
    slate_date: date
    max_quote_age_s: float
    applies_to: tuple[str, ...]


def load_exclusions(path: Path = EXCLUSIONS) -> list[Exclusion]:
    raw = yaml.safe_load(path.read_text()) or {}
    out = []
    for e in raw.get("exclusions", []):
        out.append(
            Exclusion(
                id=str(e["id"]),
                reason=str(e["reason"]),
                slate_date=e["slate_date"]
                if isinstance(e["slate_date"], date)
                else date.fromisoformat(str(e["slate_date"])),
                max_quote_age_s=float(e["max_quote_age_s"]),
                applies_to=tuple(e.get("applies_to", ())),
            )
        )
    ids = [e.id for e in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate exclusion ids in {path}")
    return out


def _min_prop_age(prop_json: str | None) -> float | None:
    if not prop_json:
        return None
    ages = [q["age_s"] for q in json.loads(prop_json) if q.get("age_s") is not None]
    return min(ages) if ages else None


def stale_price_keys(exclusion: Exclusion, market_db: Path = MARKET_DB) -> list[dict[str, Any]]:
    """Keys of the forward ``market_at_prediction`` rows of the slate whose newest quote is too old.

    Rows with no quote at all are not "stale": they carry their own ``reason`` and are not priced.
    """
    con = duckdb.connect(str(market_db), read_only=True)
    try:
        rows = con.execute(
            """SELECT run_id, game_id, model_name, version, target, player_id, made_at, ml_age_s,
                      prop_json
               FROM market_at_prediction
               WHERE capture_kind = 'forward' AND event_date = ?
               ORDER BY game_id, made_at, model_name, target, player_id""",
            [exclusion.slate_date],
        ).fetchall()
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for run_id, gid, model, version, target, pid, made_at, ml_age, prop_json in rows:
        age = ml_age if target == GAME_TARGET else _min_prop_age(prop_json)
        if age is not None and age > exclusion.max_quote_age_s:
            out.append(
                {
                    "exclusion": exclusion.id,
                    "reason": exclusion.reason,
                    "run_id": run_id,
                    "game_id": gid,
                    "model_name": model,
                    "version": version,
                    "target": target,
                    "player_id": pid,
                    "made_at": made_at.isoformat(),
                    "quote_age_s": round(float(age), 1),
                }
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["check", "keys"])
    ap.add_argument("--id", default=None)
    ap.add_argument("--file", default=str(EXCLUSIONS))
    ap.add_argument("--market-db", default=str(MARKET_DB))
    a = ap.parse_args(argv)
    entries = [e for e in load_exclusions(Path(a.file)) if a.id in (None, e.id)]
    if not entries:
        print("no matching exclusion")
        return 1
    for e in entries:
        keys = stale_price_keys(e, Path(a.market_db))
        if a.cmd == "keys":
            for k in keys:
                print(json.dumps(k, sort_keys=True))
            continue
        games = sorted({k["game_id"] for k in keys})
        kinds = {
            "game": sum(1 for k in keys if k["target"] == GAME_TARGET),
            "prop": sum(1 for k in keys if k["target"] != GAME_TARGET),
        }
        print(f"{e.id} ({e.reason}, slate {e.slate_date}): {len(keys)} rows {kinds} games={games}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
