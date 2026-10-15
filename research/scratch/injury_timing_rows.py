# ruff: noqa: E501
# mypy: ignore-errors
"""INJURY_TIMING step 1b: production's minutes / pts errors by the status situation at the used snapshot (2023-24 only).

Descriptive. Reads MAIN read-only. Production arms = A0 (props_pts_A0 mean/crps_int) and P (minutes_scores P_*).
Seasons 2023, 2024 only (row_season asserted); box rows only (DNP rows have no outcome, so the scored set is selected on playing).
Output: reports/injury_timing/rows.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from injury_timing_descriptive import MAIN, SEED, B, load, sn_dict, snap  # noqa: E402

from nba.features.injury_report import rotation_flagged_by_team  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "reports/injury_timing/rows.json"


def clustered_diff(x: np.ndarray, g: np.ndarray, m1: np.ndarray, rng) -> list[float]:
    """mean(x | m1) - mean(x | ~m1), game-clustered percentile bootstrap (ratio of resampled per-game sums)."""
    ug, inv = np.unique(g, return_inverse=True)
    n = len(ug)
    s1 = np.bincount(inv, weights=x * m1, minlength=n)
    c1 = np.bincount(inv, weights=m1.astype(float), minlength=n)
    s0 = np.bincount(inv, weights=x * ~m1, minlength=n)
    c0 = np.bincount(inv, weights=(~m1).astype(float), minlength=n)
    pt = s1.sum() / c1.sum() - s0.sum() / c0.sum()
    ii = rng.integers(0, n, size=(B, n))
    d = s1[ii].sum(1) / c1[ii].sum(1) - s0[ii].sum(1) / c0[ii].sum(1)
    return [float(pt), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def clustered_mean(x: np.ndarray, g: np.ndarray, rng) -> list[float]:
    ug, inv = np.unique(g, return_inverse=True)
    n = len(ug)
    s = np.bincount(inv, weights=x, minlength=n)
    c = np.bincount(inv, minlength=n).astype(float)
    ii = rng.integers(0, n, size=(B, n))
    m = s[ii].sum(1) / c[ii].sum(1)
    return [float(s.sum() / c.sum()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


def main() -> None:
    rng = np.random.default_rng(SEED)
    ms = np.load(MAIN / "reports/minutes_v2/minutes_scores.npz", allow_pickle=True)
    pa = np.load(MAIN / "reports/minutes_v2/props_pts_A0.npz", allow_pickle=True)
    assert set(np.unique(ms["row_season"])) <= {2023, 2024}
    assert (pa["gid"] == ms["row_gid"]).all() and (pa["pid"] == ms["row_pid"]).all()
    rows = pl.DataFrame(
        {
            "game_id": ms["row_gid"].astype(str),
            "player_id": ms["row_pid"].astype(np.int64),
            "season": ms["row_season"].astype(np.int64),
            "min_y": ms["row_y"],
            "min_mean": ms["P_mean"],
            "min_crps": ms["P_crps"],
            "pts_y": pa["y"],
            "pts_mean": pa["mean"],
            "pts_crps": pa["crps_int"],
            "n_out_rot": ms["row_n_out_rot"],
            "min10": ms["row_min10"],
        }
    )
    parts = []
    for season in (2023, 2024):
        con, games, df, tips, state = load(season, hist=False)
        info = {g: (d, int(h), int(a)) for g, d, h, a in games}
        used = snap(df, 60)
        st = sn_dict(used)
        team = dict(
            con.execute(
                "SELECT game_id || '_' || player_id, team_id FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season = ?",
                [season],
            ).fetchall()
        )
        qd: dict[str, set[int]] = {}
        for (g, p), s in st.items():
            if s in ("questionable", "doubtful"):
                qd.setdefault(g, set()).add(p)
        rot_qd = rotation_flagged_by_team(qd, info, state)
        open_tg = set(rot_qd)  # (game, team) with >=1 rotation Q/D at the used snapshot
        sub = rows.filter(pl.col("season") == season)
        tm = [team.get(f"{g}_{p}") for g, p in zip(sub["game_id"], sub["player_id"], strict=True)]
        stat = [
            st.get((g, p), "absent") for g, p in zip(sub["game_id"], sub["player_id"], strict=True)
        ]
        sub = sub.with_columns(
            pl.Series("team", tm, dtype=pl.Int64),
            pl.Series("status", stat, dtype=pl.Utf8),
        )
        sub = sub.with_columns(
            pl.Series(
                "open_team",
                [(g, t) in open_tg for g, t in zip(sub["game_id"], sub["team"], strict=True)],
                dtype=pl.Boolean,
            )
        )
        parts.append(sub)
        con.close()
    d = pl.concat(parts)
    g = d["game_id"].to_numpy()
    res: dict = {"n_rows": d.height, "n_games": int(d["game_id"].n_unique()), "seed": SEED, "B": B}
    res["status_counts"] = {k: int(v) for k, v in d.group_by("status").len().iter_rows()}
    res["n_out_rot_share"] = {
        str(k): float(v) / d.height
        for k, v in d.with_columns(pl.col("n_out_rot").fill_nan(-1).clip(-1, 3))
        .group_by("n_out_rot")
        .len()
        .sort("n_out_rot")
        .iter_rows()
    }
    res["open_team_share_rows"] = float(d["open_team"].mean())
    qd_self = (d["status"].is_in(["questionable", "doubtful"])).to_numpy()
    open_t = d["open_team"].to_numpy()
    res["qd_self_rows"] = int(qd_self.sum())
    for name, m in (("self_QD", qd_self), ("open_team_rotation_QD", open_t)):
        res[name] = {"n": int(m.sum())}
        for stat in ("min", "pts"):
            crps = d[f"{stat}_crps"].to_numpy()
            err = (d[f"{stat}_y"] - d[f"{stat}_mean"]).to_numpy()
            ae = np.abs(err)
            res[name][stat] = {
                "crps_in": float(crps[m].mean()),
                "crps_out": float(crps[~m].mean()),
                "crps_diff_in_minus_out": clustered_diff(crps, g, m, rng),
                "bias_in": float(err[m].mean()),
                "bias_out": float(err[~m].mean()),
                "bias_diff": clustered_diff(err, g, m, rng),
                "mae_in": float(ae[m].mean()),
                "mae_out": float(ae[~m].mean()),
            }
    # sensitivity of production's own prediction to the rotation-OUT count (what a flip changes), by min10 stratum, 0 vs 1 vs 2+
    sens = {}
    for lo, hi, lab in ((0, 15, "min10<15"), (15, 25, "15-25"), (25, 99, "25+")):
        s = d.filter((pl.col("min10") >= lo) & (pl.col("min10") < hi))
        o = {}
        for k in (0, 1, 2):
            t = s.filter(pl.col("n_out_rot") == k) if k < 2 else s.filter(pl.col("n_out_rot") >= 2)
            o[str(k) + ("+" if k == 2 else "")] = {
                "n": t.height,
                "min_pred": float(t["min_mean"].mean()),
                "min_real": float(t["min_y"].mean()),
                "pts_pred": float(t["pts_mean"].mean()),
                "pts_real": float(t["pts_y"].mean()),
            }
        sens[lab] = o
    res["sensitivity_by_n_out_rot"] = sens
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
