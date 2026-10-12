"""Reconcile a derived (play-by-play) player box against an official one, player-game by
player-game.

Tolerances are the project's existing ones: points exact (``reconcile_points`` is exact, 100% of
team-games), minutes within ``MINUTES_WITHIN_TOL`` (1.0 minute; share target 0.95, floor 0.91,
``nba.parse.lineups``). Counting stats are compared exactly and reported by stat.

Only players with official minutes > 0 enter the minutes comparison; a player the official box
lists as played but the derived box lacks entirely is counted as ``missing_in_derived`` (the known
short-stint gap: players with under a minute and no stint row); derived rows with no official row
are ``extra_in_derived``.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import polars as pl

from nba.parse.lineups import MINUTES_WITHIN_TOL

STATS = [
    "pts",
    "reb",
    "ast",
    "fg3m",
    "fga",
    "fta",
    "fgm",
    "ftm",
    "tov",
    "stl",
    "blk",
    "oreb",
    "dreb",
    "pf",
]


def _f(v: Any) -> float:
    return float(v) if v is not None else float("nan")


def reconcile_box(
    derived: pl.DataFrame, official: pl.DataFrame, *, max_examples: int = 8
) -> dict[str, Any]:
    d = derived.select(["game_id", "player_id", "team_id", "minutes", *STATS])
    o = official.select(["game_id", "player_id", "team_id", "minutes", *STATS])
    j = o.join(d, on=["game_id", "player_id"], how="full", suffix="_d", coalesce=True)
    played_o = j.filter(pl.col("minutes").fill_null(0) > 0)
    missing = played_o.filter(pl.col("team_id_d").is_null())
    extra = j.filter(pl.col("team_id").is_null())
    both = j.filter(pl.col("team_id").is_not_null() & pl.col("team_id_d").is_not_null())
    out: dict[str, Any] = {
        "games": int(j["game_id"].n_unique()),
        "official_rows": o.height,
        "official_played_rows": int((o["minutes"].fill_null(0) > 0).sum()),
        "derived_rows": d.height,
        "missing_in_derived_played": missing.height,
        "missing_in_derived_played_minutes_mean": _f(missing["minutes"].mean())
        if missing.height
        else 0.0,
        "missing_in_derived_played_minutes_max": _f(missing["minutes"].max())
        if missing.height
        else 0.0,
        "missing_examples": missing.select(["game_id", "player_id", "minutes"])
        .head(max_examples)
        .rows(),
        "extra_in_derived": extra.height,
        "extra_in_derived_with_minutes_or_stats": int(
            extra.filter(
                (pl.col("minutes_d").fill_null(0) > 0) | (pl.col("pts_d").fill_null(0) > 0)
            ).height
        ),
        "per_stat": {},
    }
    playedboth = both.filter(pl.col("minutes").fill_null(0) > 0)
    for s in STATS:
        ne = playedboth.filter(pl.col(s).fill_null(0) != pl.col(f"{s}_d").fill_null(0))
        out["per_stat"][s] = {
            "n": playedboth.height,
            "mismatch": ne.height,
            "rate": ne.height / playedboth.height if playedboth.height else None,
            "examples": ne.select(["game_id", "player_id", s, f"{s}_d"]).head(max_examples).rows(),
        }
    diff = (playedboth["minutes_d"].fill_null(0.0) - playedboth["minutes"]).abs()
    n = playedboth.height
    out["minutes"] = {
        "n": n,
        "within_tol": int((diff <= MINUTES_WITHIN_TOL).sum()),
        "share_within_tol": _f((diff <= MINUTES_WITHIN_TOL).mean()) if n else None,
        "mean_abs_diff": _f(diff.mean()) if n else None,
        "p95_abs_diff": _f(diff.quantile(0.95)) if n else None,
        "examples": playedboth.with_columns(diff.alias("ad"))
        .sort("ad", descending=True)
        .select(["game_id", "player_id", "minutes", "minutes_d"])
        .head(max_examples)
        .rows(),
    }
    tp = playedboth.group_by(["game_id", "team_id"]).agg(
        (pl.col("pts") - pl.col("pts_d")).abs().sum().alias("e")
    )
    out["team_game_points_exact"] = {"n": tp.height, "exact": int((tp["e"] == 0).sum())}
    return out


# ---------------------------------------------------------------------------
# validation sample: pick the games, then reconcile against the fetched box scores
# ---------------------------------------------------------------------------


def pick_sample(
    stage: Path, seasons: list[int], n: int, seed: int, extra_ids: list[str]
) -> list[str]:
    """``n`` random games per season (seeded; regular season and playoffs together) plus extras."""
    out: set[str] = set(extra_ids)
    for s in seasons:
        ids = sorted(pl.read_parquet(stage / str(s) / "games.parquet")["game_id"].to_list())
        out |= set(random.Random(seed * 10_000 + s).sample(ids, n))
    return sorted(out)


def reconcile_sample(stage: Path, seasons: list[int], sample_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    all_d, all_o = [], []
    for s in seasons:
        box = pl.read_parquet(stage / str(s) / "box.parquet")
        games = pl.read_parquet(stage / str(s) / "games.parquet")
        have = {p.stem for p in (sample_dir / "boxscore").glob("*.parquet")}
        ids = sorted(set(games["game_id"]) & have)
        if not ids:
            result[str(s)] = {"games_fetched": 0}
            continue
        off = pl.concat([pl.read_parquet(sample_dir / "boxscore" / f"{g}.parquet") for g in ids])
        off = off.with_columns(pl.col("game_id").cast(pl.Utf8))
        der = box.filter(pl.col("game_id").is_in(ids))
        all_d.append(der)
        all_o.append(off)
        rec = reconcile_box(der, off)
        og = (
            off.group_by("game_id", "team_id")
            .agg(pl.col("pts").sum().alias("o"))
            .join(
                pl.concat(
                    [
                        games.select(
                            "game_id",
                            pl.col("home_team").alias("team_id"),
                            pl.col("home_pts").alias("g"),
                        ),
                        games.select(
                            "game_id",
                            pl.col("away_team").alias("team_id"),
                            pl.col("away_pts").alias("g"),
                        ),
                    ]
                ),
                on=["game_id", "team_id"],
                how="left",
            )
        )
        rec["games_fetched"] = len(ids)
        rec["games_in_stage"] = games.height
        rec["games_table_points_ne_official_team_games"] = int((og["o"] != og["g"]).sum())
        rec["games_table_points_examples"] = og.filter(pl.col("o") != pl.col("g")).head(8).rows()
        gap = der.join(
            off.select("game_id", "player_id", pl.col("minutes").alias("om")),
            on=["game_id", "player_id"],
            how="left",
        )
        zero = gap.filter(pl.col("minutes") == 0)
        rec["short_stint_gap"] = {
            "derived_rows_with_no_stint_time": zero.height,
            "of_which_official_minutes_gt_0": int((zero["om"].fill_null(0) > 0).sum()),
            "official_minutes_max": _f(zero["om"].max()) if zero.height else 0.0,
            "official_minutes_mean": _f(zero["om"].mean()) if zero.height else 0.0,
        }
        result[str(s)] = rec
    if all_d:
        result["all"] = reconcile_box(pl.concat(all_d), pl.concat(all_o))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.history_reconcile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick", help="write the validation sample game ids")
    p.add_argument("--stage", type=Path, required=True)
    p.add_argument("--season", type=int, action="append", required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=20261012)
    p.add_argument(
        "--qc", type=Path, default=None, help="QC json: add games whose box != final score"
    )
    p.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("reconcile", help="derived box vs the fetched sample")
    r.add_argument("--stage", type=Path, required=True)
    r.add_argument("--season", type=int, action="append", required=True)
    r.add_argument("--sample-dir", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "pick":
        extra: list[str] = []
        if args.qc:
            qc = json.loads(args.qc.read_text())
            extra = sorted(
                {g for s in args.season for g in qc[str(s)]["box_points_ne_final_score_games"]}
            )
        ids = pick_sample(args.stage, args.season, args.n, args.seed, extra)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(ids) + "\n")
        print(f"sample: {len(ids)} games ({len(extra)} flagged extras) -> {args.out}")
        return 0
    res = reconcile_sample(args.stage, args.season, args.sample_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, default=str))
    for k, v in res.items():
        brief = {
            x: v.get(x)
            for x in (
                "games_fetched",
                "official_played_rows",
                "missing_in_derived_played",
                "extra_in_derived",
            )
        }
        print(
            k,
            brief,
            {s: d["rate"] for s, d in v.get("per_stat", {}).items()},
            v.get("minutes", {}).get("share_within_tol"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
