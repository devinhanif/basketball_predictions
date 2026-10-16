# ruff: noqa: E501
"""INJURY_TIMING rev 3: the list builders, the one cutoff function, the fingerprints and the minimum-data gate (rule: docs/prereg/INJURY_TIMING.md).

    .venv/bin/python -W ignore -m research.eval.injury_timing_gate --data-root /Users/devin/Downloads/nba-prediction \
        --repaired /Users/devin/Downloads/nba-prediction/data/scratch/injury_intraday_v2.duckdb

Counts, lead times and OUT sets only: no outcome (minutes, points) is read by this file. ``nba.duckdb`` and the scratch duckdb are opened
read-only with ``season <= 2024`` in SQL; the schedule is read with ``max_season=2024``. Output: ``reports/prereg_injury_timing/gate.json``.

The lists (every arm builds its OUT sets with :func:`lists`, one function):

* ``S`` / A0 training: the STORED table exactly as production has it (stamps as stored, cutoff ``tip - 60``).
* ``R``: the header-stamped intraday table UNION the stored table with its stamps moved to the PDF header time (hour-only files are +30 min),
  cutoff :func:`live_cutoff` (B2): the last :20 / :50 ET pretip run at or before ``tip - 60``, minus 1 microsecond.
* ``P`` (placebo): the intraday parse alone (shifted stored rows only for stamps it does not hold), cut at each game's corrected STORED used stamp
  (the freshness of the stored table, from the intraday parse).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.game_tipoff import build_game_tipoff

SEASONS = (2023, 2024)
DECISIVE = (2024,)  # B3 (a): 2024 decides; 2023 is reported
MAX_SEASON = 2024
#: gate thresholds. Rev 3 changed FRESH_LEAD 90 -> 120 and made 2023 informational (reasons in the rule, section 7); the bar was set before any outcome.
FRESH_LEAD = 120
FRESH_LEAD_REV2 = 90
MIN_FRESH_SHARE = 0.80
MIN_GAMES = 1_200
MIN_DIFF_GAMES = 300
RUN_MINUTES = (20, 50)  # the pretip launchd runs, ET wall clock
SCHEMA = pl.Schema(
    [
        ("game_id", pl.Utf8),
        ("player_id", pl.Int64),
        ("status", pl.Utf8),
        ("as_of", pl.Datetime("us")),
        ("season", pl.Int64),
    ]
)


def live_cutoff(tip: dt.datetime) -> dt.datetime:
    """The newest report stamp a T-60-eligible live run may use: the last :20 / :50 ET run at or before ``tip - 60 min``, minus 1 microsecond
    (the run probes slots STRICTLY before itself). ``tip`` is naive ET."""
    t = tip - dt.timedelta(minutes=60)
    if t.minute >= 50:
        run = t.replace(minute=50, second=0, microsecond=0)
    elif t.minute >= 20:
        run = t.replace(minute=20, second=0, microsecond=0)
    else:
        run = t.replace(minute=50, second=0, microsecond=0) - dt.timedelta(hours=1)
    return run - dt.timedelta(microseconds=1)


def prod_cutoff(tip: dt.datetime) -> dt.datetime:
    """Production training rule: ``as_of <= tip - 60 min``."""
    return tip - dt.timedelta(minutes=60)


def rows_from_db(db: Path, seasons: tuple[int, ...] = (2022, 2023, 2024)) -> pl.DataFrame:
    """Official-report rows of games in ``seasons`` (<= 2024) from ``db`` (read-only). Stamps are whatever the table holds."""
    assert max(seasons) <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT a.game_id, a.player_id, lower(a.status), CAST(a.as_of AS TIMESTAMP), g.season "
            "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
            "WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL AND g.season IN ("
            + ", ".join(str(int(s)) for s in seasons)
            + ")"
        ).fetchall()
    finally:
        con.close()
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def stored_header_map(db: Path) -> dict[dt.datetime, dt.datetime]:
    """stored stamp (filename time) -> header time, from the header audit of the STORED PDFs in the re-parsed scratch db."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT filename_dt, header_dt FROM stored_report_headers WHERE status = 'ok'"
        ).fetchall()
    finally:
        con.close()
    return {a: b for a, b in rows}


def shift_stored(stored: pl.DataFrame, hmap: dict[dt.datetime, dt.datetime]) -> pl.DataFrame:
    """Move every stored stamp to its PDF header time. A stamp with no audited file is an error (never guessed)."""
    stamps = set(stored["as_of"].unique().to_list())
    missing = sorted(stamps - set(hmap))
    if missing:
        raise RuntimeError(
            f"{len(missing)} stored stamps have no audited header, e.g. {missing[:3]}"
        )
    m = pl.DataFrame(
        {"as_of": list(hmap), "as_of_hdr": list(hmap.values())},
        schema={"as_of": pl.Datetime("us"), "as_of_hdr": pl.Datetime("us")},
    )
    return (
        stored.join(m, on="as_of", how="left")
        .drop("as_of")
        .rename({"as_of_hdr": "as_of"})
        .select(stored.columns)
    )


def repaired_union(stored_corrected: pl.DataFrame, intraday: pl.DataFrame) -> pl.DataFrame:
    """R = every report we hold, on the header clock."""
    return pl.concat([stored_corrected, intraday.select(stored_corrected.columns)]).unique()


def placebo_rows(stored_corrected: pl.DataFrame, intraday: pl.DataFrame) -> pl.DataFrame:
    """Rows for the placebo P: the intraday parse alone wherever it holds the stamp; the shifted stored rows only for stamps the intraday table
    does not hold (2022 games, 11:30 reports). Without this P would be the union and identical to the stored lists."""
    have = intraday["as_of"].unique().to_list()
    keep = stored_corrected.filter(~pl.col("as_of").is_in(have))
    return pl.concat([keep, intraday.select(stored_corrected.columns)]).unique()


def lists(
    rows: pl.DataFrame, cut: dict[str, dt.datetime]
) -> tuple[dict[str, set[int]], dict[str, dt.datetime]]:
    """Per game with a cutoff in ``cut``: the OUT set of the latest stamp ``<= cut[game]`` (and that stamp). Only rows of THAT stamp count;
    a game with no usable stamp is absent (never imputed). The one list builder of the rule."""
    c = pl.DataFrame(
        {"game_id": list(cut), "_cut": list(cut.values())},
        schema={"game_id": pl.Utf8, "_cut": pl.Datetime("us")},
    )
    usable = rows.join(c, on="game_id").filter(pl.col("as_of") <= pl.col("_cut"))
    if usable.height == 0:
        return {}, {}
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_l"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_l"))
    flagged: dict[str, set[int]] = {g: set() for g in latest["game_id"].to_list()}
    for g, p, s in snap.select(["game_id", "player_id", "status"]).iter_rows():
        if s == "out":
            flagged[str(g)].add(int(p))
    return flagged, {str(g): t for g, t in latest.iter_rows()}


def cuts(tips: dict[str, dt.datetime], games: list[str], fn: Any) -> dict[str, dt.datetime]:
    return {g: fn(tips[g]) for g in games if g in tips}


def fingerprint(flagged: dict[str, set[int]], used: dict[str, dt.datetime], games: set[str]) -> str:
    """sha256 of the sorted per-game ``game|used stamp|sorted OUT ids`` lines."""
    lines = sorted(
        f"{g}|{used[g].isoformat()}|{','.join(map(str, sorted(flagged[g])))}"
        for g in flagged
        if g in games
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def known_players(db: Path) -> list[int]:
    """Players with at least one box-score row in a season <= 2024. Every arm's lists are restricted to them: the features can only use a
    player with a history, and the intraday resolver cannot match the 2025-26 debutants (Topic, Holmes II, Jones-Garcia) that the stored
    parse resolved later, so without this S and R would differ by players no feature can read."""
    assert MAX_SEASON == 2024
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT s.player_id FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024"
        ).fetchall()
    finally:
        con.close()
    return [int(r[0]) for r in rows]


def season_games(db: Path, seasons: tuple[int, ...] = SEASONS) -> dict[int, list[str]]:
    assert max(seasons) <= MAX_SEASON
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT season, game_id FROM games WHERE season IN ("
            + ", ".join(str(int(s)) for s in seasons)
            + ")"
        ).fetchall()
    finally:
        con.close()
    out: dict[int, list[str]] = {int(s): [] for s in seasons}
    for s, g in rows:
        out[int(s)].append(str(g))
    return out


def build_all(root: Path, scratch: Path, seasons: tuple[int, ...] = SEASONS) -> dict[str, Any]:
    """Everything the gate, the fingerprints and the harness need, label-free: stored, R, the three list sets per game and the tips."""
    tips_df = build_game_tipoff(root / "data/schedule", max_season=MAX_SEASON)
    tips = {g: t for g, t in tips_df.select(["game_id", "tip_et"]).iter_rows()}
    stored = rows_from_db(root / "nba.duckdb")
    known = known_players(root / "nba.duckdb")
    stored = stored.filter(pl.col("player_id").is_in(known))
    hmap = stored_header_map(scratch)
    stored_c = shift_stored(stored, hmap)
    intraday = rows_from_db(scratch).filter(pl.col("player_id").is_in(known))
    r = repaired_union(stored_c, intraday)
    games = season_games(root / "nba.duckdb", (2022, *seasons))
    allg = [g for v in games.values() for g in v]
    s_fl, s_used = lists(stored, cuts(tips, allg, prod_cutoff))
    r_fl, r_used = lists(r, cuts(tips, allg, live_cutoff))
    # P: R rows cut at the corrected stored used stamp of each game (the stored table's freshness, intraday parse)
    p_cut = {g: hmap[u] for g, u in s_used.items()}
    p_fl, p_used = lists(placebo_rows(stored_c, intraday), p_cut)
    return {
        "tips": tips,
        "games": games,
        "stored": stored,
        "R": r,
        "S": (s_fl, s_used),
        "R_live": (r_fl, r_used),
        "P": (p_fl, p_used),
        "hmap": hmap,
    }


def season_block(b: dict[str, Any], season: int) -> dict[str, Any]:
    gs = set(b["games"][season])
    tips = b["tips"]
    s_fl, s_used = b["S"]
    r_fl, r_used = b["R_live"]
    lead = np.array([(tips[g] - r_used[g]).total_seconds() / 60 for g in r_used if g in gs])
    lead_s = np.array(
        [(tips[g] - b["hmap"][s_used[g]]).total_seconds() / 60 for g in s_used if g in gs]
    )
    both = sorted(g for g in gs if g in s_fl and g in r_fl)
    diff = [g for g in both if s_fl[g] != r_fl[g]]
    p_fl = b["P"][0]
    n_stamps = (
        b["R"]
        .filter(pl.col("season") == season)
        .group_by("game_id")
        .agg(pl.col("as_of").n_unique().alias("n"))["n"]
        .cast(pl.Float64)
        .mean()
    )
    return {
        "games_total": len(gs),
        "games_with_usable_snapshot": int(len(lead)),
        "lead_min_p10_p50_p90": [float(np.percentile(lead, q)) for q in (10, 50, 90)],
        "fresh_share_lead_le_120": float((lead <= FRESH_LEAD).mean()),
        "fresh_share_lead_le_90_rev2_bar": float((lead <= FRESH_LEAD_REV2).mean()),
        "stored_lead_corrected_p10_p50_p90": [
            float(np.percentile(lead_s, q)) for q in (10, 50, 90)
        ],
        "stored_share_post_cutoff_games": float(np.mean([hb < 60 for hb in lead_s])),
        "distinct_stamps_per_game_mean": float(n_stamps or 0.0),
        "vs_stored": {"games_in_both": len(both), "games_out_set_differs": len(diff)},
        "vs_placebo": {
            "games_out_set_differs_P_vs_S": sum(1 for g in both if g in p_fl and p_fl[g] != s_fl[g])
        },
    }


def gate_verdict(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for s, b in blocks.items():
        dec = int(s) in DECISIVE
        n = b["vs_stored"]["games_out_set_differs"]
        for name, val, ok in (
            (
                f"{s}: games with usable snapshot >= {MIN_GAMES}",
                b["games_with_usable_snapshot"],
                b["games_with_usable_snapshot"] >= MIN_GAMES,
            ),
            (
                f"{s}: share of games with used lead <= {FRESH_LEAD} min >= {MIN_FRESH_SHARE}",
                b["fresh_share_lead_le_120"],
                b["fresh_share_lead_le_120"] >= MIN_FRESH_SHARE,
            ),
            (
                f"{s}: games whose OUT set differs from stored >= {MIN_DIFF_GAMES}",
                n,
                n >= MIN_DIFF_GAMES,
            ),
        ):
            checks.append({"check": name, "value": val, "pass": bool(ok), "decisive": dec})
    return {
        "checks": checks,
        "gate_pass": bool(checks) and all(c["pass"] for c in checks if c["decisive"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--repaired", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    b = build_all(a.data_root, a.repaired)
    blocks = {str(s): season_block(b, s) for s in SEASONS}
    fps = {}
    for name in ("S", "R_live", "P"):
        fl, used = b[name]
        for s in SEASONS:
            fps[f"FINGERPRINT_{name.replace('_live', 'LIVE')}_{s}"] = fingerprint(
                fl, used, set(b["games"][s])
            )
    res = {
        "rule": "docs/prereg/INJURY_TIMING.md",
        "started": started,
        "ended": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "thresholds": {
            "FRESH_LEAD": FRESH_LEAD,
            "MIN_FRESH_SHARE": MIN_FRESH_SHARE,
            "MIN_GAMES": MIN_GAMES,
            "MIN_DIFF_GAMES": MIN_DIFF_GAMES,
            "DECISIVE_SEASONS": list(DECISIVE),
        },
        "seasons": blocks,
        "fingerprints": fps,
        **gate_verdict(blocks),
    }
    out = a.out or (Path(__file__).resolve().parents[2] / "reports/prereg_injury_timing/gate.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
