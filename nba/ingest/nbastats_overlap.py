"""Overlap check: downloaded nbastats play-by-play vs the copy we fetched ourselves (history).

Run once before the 2015-2019 seasons are trusted (DECISIONS 2026-10-12 17:41). For every game in
the downloaded 2019-20 / 2020-21 files (regular season + playoffs) it reports, per season:

* games only in ours / only in theirs; event rows matched by ``(game_id, action_number)``;
* per-field disagreement on matched rows (period, clock exact and within 1 s, team, player,
  action type, sub type, shot result/value/distance, score, location, description text);
* parser equivalence: ``parse_possessions`` on the adapted frame vs on ours (row counts and every
  analysed column), and the lineup tracker (same starters) on a seeded sample of games.

Read-only on data/history; writes one JSON report. Pure CPU, about 3 minutes for both seasons.

    uv run python -m nba.ingest.nbastats_overlap --extracted <dir> --out
    reports/history_overlap.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.ingest.nbastats_pbp import duplicate_keys, load_subtype_map, read_nbastats, to_v3
from nba.parse.lineups import track_lineups_with_report
from nba.parse.possessions import parse_possessions

OVERLAP_FILES = {
    2019: ["nbastats_2019", "nbastats_po_2019"],
    2020: ["nbastats_2020", "nbastats_po_2020"],
}
FIELDS = [
    "period",
    "team_id",
    "player_id",
    "action_type",
    "sub_type",
    "shot_result",
    "shot_value",
    "shot_distance",
    "is_field_goal",
    "score_home",
    "score_away",
    "location",
    "description",
    "player_name",
]
POSS_COLS = [
    "period",
    "off_team",
    "def_team",
    "score_diff",
    "outcome",
    "shooter_id",
    "shot_zone",
    "assister_id",
    "oreb",
    "fta",
    "pts",
    "n_oreb",
    "n_dreb",
]
STINT_SAMPLE = 150


def _secs(clock: str) -> int:
    return int(clock[2:4]) * 60 + round(float(clock[5:-1]))


def _neq(a: pl.Series, b: pl.Series) -> int:
    return int((a.cast(pl.Utf8).fill_null("") != b.cast(pl.Utf8).fill_null("")).sum())


def _season_of(game_id: str) -> int:
    return 2000 + int(game_id[3:5])


def season_report(
    extracted: Path, history: Path, season: int, sample_seed: int, db_path: Path
) -> dict[str, Any]:
    paths = [extracted / n / f"{n}.csv" for n in OVERLAP_FILES[season]]
    raw = pl.concat([pl.read_csv(p, infer_schema_length=0, null_values=["", "NaN"]) for p in paths])
    n_raw = raw.height
    theirs = read_nbastats(paths)
    subtypes = load_subtype_map()
    parts = theirs.partition_by("GAME_ID", as_dict=True)
    ours_ids = {
        p.stem
        for p in (history / "pbp").glob(f"00[245]{str(season)[2:]}*.parquet")
        if p.stem[3:5] == str(season)[2:]
    }
    theirs_ids = {f"{int(k[0]):010d}" for k in parts}

    acc: dict[str, int] = defaultdict(int)
    con = duckdb.connect(str(db_path), read_only=True)
    box = con.execute(
        "SELECT game_id, player_id, team_id, starter, minutes FROM player_game_stats "
        f"WHERE game_id LIKE '%{str(season)[2:]}%'"
    ).pl()
    con.close()
    rng = random.Random(sample_seed)
    keys = sorted(parts)
    sample = set(rng.sample(keys, min(STINT_SAMPLE, len(keys))))
    stat_unmapped: dict[str, int] = {}
    assist_ours_null_theirs_set = 0
    assist_theirs_null_ours_set = 0
    assist_both_set_differ = 0
    for k in keys:
        v3 = to_v3(parts[k], subtypes, stat_unmapped)
        gid = v3["game_id"][0]
        if gid not in ours_ids:
            continue
        ours_full = pl.read_parquet(history / "pbp" / f"{gid}.parquet")
        ours = ours_full.filter(pl.col("action_type") != "")
        acc["games_common"] += 1
        acc["ours_primary_rows"] += ours.height
        acc["theirs_rows"] += v3.height
        j = v3.join(ours, on="action_number", how="inner", suffix="_o")
        acc["rows_matched"] += j.height
        acc["theirs_rows_unmatched"] += v3.height - j.height
        acc["ours_primary_unmatched"] += ours.height - j.height
        for c in FIELDS:
            acc[f"diff_{c}"] += _neq(j[c], j[f"{c}_o"])
        cs = [(_secs(a), _secs(b)) for a, b in zip(j["clock"], j["clock_o"], strict=True)]
        acc["clock_exact_diff"] += sum(a != b for a, b in cs)
        acc["clock_gt1s_diff"] += sum(abs(a - b) > 1 for a, b in cs)
        acc["clock_theirs_lower_by_1"] += sum(a - b == -1 for a, b in cs)
        pa, po = parse_possessions(v3), parse_possessions(ours_full)
        acc["poss_rows_theirs"] += pa.height
        acc["poss_rows_ours"] += po.height
        if pa.height == po.height:
            acc["poss_games_same_count"] += 1
            diffs = {c: _neq(pa[c], po[c]) for c in POSS_COLS}
            for c, d in diffs.items():
                acc[f"poss_diff_{c}"] += d
            a_null = pa["assister_id"].is_null()
            o_null = po["assister_id"].is_null()
            assist_ours_null_theirs_set += int((o_null & ~a_null).sum())
            assist_theirs_null_ours_set += int((a_null & ~o_null).sum())
            assist_both_set_differ += int(
                (~a_null & ~o_null & (pa["assister_id"] != po["assister_id"])).sum()
            )
            if sum(d for c, d in diffs.items() if c != "assister_id") == 0:
                acc["poss_games_identical_ex_assister"] += 1
        if k in sample:
            b = box.filter(pl.col("game_id") == gid)
            st = {
                int(t[0]): g["player_id"].to_list()
                for t, g in b.filter(pl.col("starter")).group_by("team_id")
            }
            if len(st) == 2 and all(len(v) == 5 for v in st.values()):
                ro = {
                    int(t[0]): g["player_id"].to_list()
                    for t, g in b.filter(pl.col("minutes") > 0).group_by("team_id")
                }
                sa, ra = track_lineups_with_report(v3, st, ro)
                so, rr = track_lineups_with_report(ours_full, st, ro)
                acc["stint_games"] += 1
                acc["stint_unresolved_theirs"] += len(ra.unresolved)
                acc["stint_unresolved_ours"] += len(rr.unresolved)

                def key(s: pl.DataFrame) -> list[Any]:
                    return sorted(
                        (r["team_id"], r["period"], tuple(sorted(r["players"])))
                        for r in s.iter_rows(named=True)
                    )

                if key(sa) == key(so):
                    acc["stint_games_identical_lineups"] += 1
    return {
        "season": season,
        "raw_rows_in_files": n_raw,
        "exact_duplicate_rows_dropped": n_raw - theirs.height,
        "duplicate_keys_after_dedupe": duplicate_keys(theirs),
        "games_theirs": len(theirs_ids),
        "games_ours": len(ours_ids),
        "games_only_ours": sorted(ours_ids - theirs_ids),
        "games_only_theirs": sorted(theirs_ids - ours_ids),
        "assister_ours_null_theirs_set": assist_ours_null_theirs_set,
        "assister_theirs_null_ours_set": assist_theirs_null_ours_set,
        "assister_both_set_differ": assist_both_set_differ,
        "adapter_unmapped": stat_unmapped,
        **dict(acc),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.nbastats_overlap")
    ap.add_argument("--extracted", type=Path, required=True)
    ap.add_argument("--history", type=Path, default=Path("data/history"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args(argv)
    db = args.history / "nba_history.duckdb"
    rep = [season_report(args.extracted, args.history, s, args.seed, db) for s in (2019, 2020)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, indent=1, default=str))
    for r in rep:
        print(json.dumps({k: v for k, v in r.items() if not isinstance(v, list)}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
