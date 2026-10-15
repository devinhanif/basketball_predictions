# ruff: noqa: E501
# mypy: ignore-errors
"""INJURY_TIMING step 1c: does the Q/D tag mean the same thing at T-180 and at T-60? (2021-22 hourly season; 2023-24 used-snapshot reference).
P(played) = box-score minutes > 0 (a listed player with no box row counts as not played). Game-clustered bootstrap. Seasons <= 2024 only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from injury_timing_descriptive import SEED, B, load, sn_dict, snap

OUT = Path(__file__).resolve().parents[2] / "reports/injury_timing/tagshift.json"


def rate(vals, games, rng):
    ug, inv = np.unique(games, return_inverse=True)
    n = len(ug)
    s = np.bincount(inv, weights=vals, minlength=n)
    c = np.bincount(inv, minlength=n).astype(float)
    ii = rng.integers(0, n, size=(B, n))
    m = s[ii].sum(1) / c[ii].sum(1)
    return [
        float(s.sum() / c.sum()),
        float(np.percentile(m, 2.5)),
        float(np.percentile(m, 97.5)),
        int(c.sum()),
    ]


def played_map(con, season):
    return {
        (g, p): float(m or 0) > 0
        for g, p, m in con.execute(
            "SELECT s.game_id, s.player_id, s.minutes FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season = ?",
            [season],
        ).fetchall()
    }


def main():
    rng = np.random.default_rng(SEED)
    res = {}
    for season, hist in ((2021, True), (2023, False), (2024, False)):
        con, games, df, tips, state = load(season, hist)
        pm = played_map(con, season)
        s60 = snap(df, 60)
        a60 = sn_dict(s60)
        arms = {"T60": a60}
        if season == 2021:
            a180 = sn_dict(snap(df, 180))
            arms["T180"] = a180
        out = {}
        for arm, d in arms.items():
            for stt in ("questionable", "doubtful", "probable", "available"):
                keys = [k for k, v in d.items() if v == stt]
                y = np.array([float(pm.get(k, False)) for k in keys])
                g = np.array([k[0] for k in keys])
                if len(y):
                    out[f"{arm}:{stt}"] = rate(y, g, rng)
        if season == 2021:
            # Q at T-180 split by what the T-60 report says
            for t60 in ("questionable", "out", "available", "absent"):
                keys = [
                    k
                    for k, v in a180.items()
                    if v == "questionable" and a60.get(k, "absent") == t60
                ]
                if keys:
                    y = np.array([float(pm.get(k, False)) for k in keys])
                    g = np.array([k[0] for k in keys])
                    out[f"Q@T180 then {t60}@T60"] = rate(y, g, rng)
        res[str(season)] = out
        con.close()
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
