"""Opening-week replay: recent-games roster vs pre-tip official-roster proxy.

Runs the forward context-residual props path for each slate date of a past season on a
read-only database COPY (``predict_slate`` reads only ``game_date < as_of`` rows, so the full
copy needs no truncation), under several roster arms, and scores each against the box scores
of that day:

* coverage of players who played (count and minutes-weighted), by group (mover / debutant /
  same-team veteran);
* P(play) calibration (mean vs observed rate, Brier, reliability bins);
* minutes error of ``proj_minutes`` on played rows;
* prop CRPS (empirical, from the stored 19-point quantile grid) conditional on playing, on
  the rows covered by BOTH arms (paired, game-clustered bootstrap) and on rows covered only by
  the official arm.

The official arm uses ``proxy_rosters_from_first_games``: an OPTIMISTIC replay proxy built from
the first ``--proxy-days`` days of the season (it knows who actually played later). Coverage is
therefore an upper bound; the measured gains are not a forecast of the live gain.

Usage (maintainer; ~20 s per slate date per arm set, 3 dates ~1.5 min, 14 dates ~6 min; hold
``data/ops/heavy.lock`` for the run)::

    uv run python -m research.props.roster_replay --db data/rehearsal/nba_full_copy.duckdb \\
        --start 2024-10-22 --end 2024-10-24 --out data/rehearsal/roster_replay_3d.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.predict import load_elo_params, slate_report_outs
from nba.daily.settle import quantile_crps
from nba.props.forward import PROP_STATS, QUANTILE_TAUS, ForwardConfig, predict_slate_context
from nba.props.metrics import paired_score_delta_ci
from nba.props.rosters import proxy_rosters_from_first_games

TIP_UTC_HOUR = 23  # 19:00 ET tip proxy, as in the dress rehearsal
LEAD_MIN = 70
MIN_GROUP_N = 2  # smallest group the paired table reports (real runs have dozens+)
ARMS: dict[str, dict[str, Any]] = {
    "recent": {"official": False},
    "official": {"official": True},
    "official_strict": {"official": True, "drop_unlisted_recent": True},
    "official_cap13": {"official": True, "official_max_roster": 13},
    "official_strict_cap13": {
        "official": True,
        "drop_unlisted_recent": True,
        "official_max_roster": 13,
    },
    "official_shrink": {"official": True, "new_team_shrinkage": True},
    "official_noadj": {"official": True, "new_team_shrinkage": False, "rookie_prior": False},
}
GROUPS = ("debutant", "mover", "same_team")
PAIRS = (
    ("official", "recent"),
    ("official_strict", "recent"),
    ("official", "official_noadj"),
    ("official_shrink", "official"),
    ("official", "official_cap13"),
)


def _season_of(d: date) -> int:
    return d.year if d.month >= 8 else d.year - 1


def slate_dates(con: duckdb.DuckDBPyConnection, start: date, end: date) -> list[date]:
    rows = con.execute(
        "SELECT DISTINCT game_date FROM games WHERE game_date BETWEEN ? AND ? "
        "AND game_id LIKE '002%' ORDER BY 1",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def player_groups(con: duckdb.DuckDBPyConnection, d: date, played: pl.DataFrame) -> pl.DataFrame:
    """group per (player_id, game_id) of players who played on ``d``: debutant (no prior played
    game), mover (latest prior game for a different team), else same_team. History only."""
    last = con.execute(
        """
        SELECT player_id, arg_max(team_id, g.game_date) AS last_team
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE g.game_date < ? AND s.minutes > 0 GROUP BY player_id
        """,
        [d],
    ).pl()
    out = played.join(last, on="player_id", how="left").with_columns(
        pl.when(pl.col("last_team").is_null())
        .then(pl.lit("debutant"))
        .when(pl.col("last_team") != pl.col("team_id"))
        .then(pl.lit("mover"))
        .otherwise(pl.lit("same_team"))
        .alias("group")
    )
    return out.select("game_id", "player_id", "group")


def run_arm(
    con: duckdb.DuckDBPyConnection,
    d: date,
    games: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo: dict[str, float],
    arm: str,
    proxy: pl.DataFrame,
    cache_root: Path,
) -> pl.DataFrame:
    spec = ARMS[arm]
    cfg = ForwardConfig(
        new_team_shrinkage=spec.get("new_team_shrinkage", False),
        rookie_prior=spec.get("rookie_prior", True),
        drop_unlisted_recent=spec.get("drop_unlisted_recent", False),
        official_max_roster=spec.get("official_max_roster", 18),
    )
    res = predict_slate_context(
        con,
        d,
        games,
        report_out,
        elo,
        cache_root=cache_root,
        config=cfg,
        official_roster=proxy if spec["official"] else None,
    )
    return res.primary.with_columns(pl.lit(arm).alias("arm"), pl.lit(d).alias("date"))


def score_rows(pred: pl.DataFrame, actual: pl.DataFrame) -> pl.DataFrame:
    """Join predictions to actuals; CRPS (conditional on playing) for played rows."""
    j = pred.join(actual, on=["game_id", "player_id"], how="left")
    j = j.with_columns(
        (pl.col("minutes").fill_null(0.0) > 0).alias("played"),
    )
    crps: list[float | None] = []
    taus = list(QUANTILE_TAUS)
    for r in j.iter_rows(named=True):
        if not r["played"]:
            crps.append(None)
            continue
        y = float(r[r["stat"]])
        crps.append(quantile_crps(taus, [float(v) for v in json.loads(r["q_grid"])], y))
    return j.with_columns(pl.Series("crps", crps, dtype=pl.Float64))


def _mean(s: pl.Series) -> float:
    return float(np.mean(s.to_numpy()))


def _bins(p: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    out = []
    for lo, hi in ((0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        m = (p >= lo) & (p < hi)
        if m.sum():
            out.append(
                {
                    "lo": lo,
                    "hi": min(hi, 1.0),
                    "n": int(m.sum()),
                    "pred": float(p[m].mean()),
                    "obs": float(y[m].mean()),
                }
            )
    return out


def summarize(
    scored: dict[str, pl.DataFrame], played: pl.DataFrame, groups: pl.DataFrame, n_boot: int = 1000
) -> dict[str, Any]:
    """Per-arm coverage / P(play) / minutes / CRPS tables plus paired official-vs-recent CRPS."""
    played_g = played.join(groups, on=["game_id", "player_id"], how="left")
    res: dict[str, Any] = {"n_played_player_games": played.height, "arms": {}}
    keyed: dict[str, pl.DataFrame] = {}
    for arm, df in scored.items():
        one = df.filter(pl.col("stat") == "pts")
        keyed[arm] = df
        have = set(zip(one["game_id"].to_list(), one["player_id"].to_list(), strict=True))
        flag = np.array(
            [
                (g, p) in have
                for g, p in zip(
                    played_g["game_id"].to_list(), played_g["player_id"].to_list(), strict=True
                )
            ]
        )
        mins = played_g["minutes"].to_numpy()
        grp = played_g["group"].to_numpy()
        cov_tbl: dict[str, Any] = {
            "all": {
                "n": int(len(flag)),
                "covered": int(flag.sum()),
                "share": float(flag.mean()),
                "minutes_share": float(mins[flag].sum() / mins.sum()),
            }
        }
        for g in GROUPS:
            m = grp == g
            if m.sum():
                cov_tbl[g] = {
                    "n": int(m.sum()),
                    "covered": int(flag[m].sum()),
                    "share": float(flag[m].mean()),
                    "minutes_share": float(mins[m & flag].sum() / mins[m].sum()),
                }
        p = one["p_play"].to_numpy()
        y = one["played"].cast(pl.Float64).to_numpy()
        pp: dict[str, Any] = {
            "n": int(len(p)),
            "mean_pred": float(p.mean()),
            "obs_rate": float(y.mean()),
            "brier": float(np.mean((p - y) ** 2)),
            "bins": _bins(p, y),
        }
        pl_rows = one.filter(pl.col("played"))
        mae = float(
            np.mean(np.abs(pl_rows["proj_minutes"].to_numpy() - pl_rows["minutes"].to_numpy()))
        )
        crps_stat = {
            s: _mean(df.filter((pl.col("stat") == s) & pl.col("played"))["crps"])
            for s in PROP_STATS
            if df.filter((pl.col("stat") == s) & pl.col("played")).height
        }
        res["arms"][arm] = {
            "n_pred_players": one.height,
            "coverage": cov_tbl,
            "p_play": pp,
            "minutes_mae_played": mae,
            "crps_all_covered": crps_stat,
        }
    for arm, df in keyed.items():
        one = df.filter(pl.col("stat") == "pts")
        by_date: dict[str, Any] = {}
        for d in sorted(set(played["date"].to_list())):
            pl_d = played.filter(pl.col("date") == d)
            have_d = set(
                zip(
                    one.filter(pl.col("date") == d)["game_id"].to_list(),
                    one.filter(pl.col("date") == d)["player_id"].to_list(),
                    strict=True,
                )
            )
            hit = sum(
                (g, p) in have_d for g, p in zip(pl_d["game_id"], pl_d["player_id"], strict=True)
            )
            pr = one.filter(pl.col("date") == d)
            by_date[str(d)] = {
                "played": pl_d.height,
                "covered": hit,
                "p_play_mean": float(pr["p_play"].mean()) if pr.height else None,  # type: ignore[arg-type]
                "obs_rate": float(pr["played"].cast(pl.Float64).mean()) if pr.height else None,  # type: ignore[arg-type]
                "n_pred": pr.height,
            }
        res["arms"][arm]["by_date"] = by_date
    base = keyed.get("recent")
    if base is not None:
        res["paired_vs_recent"] = {}
        for arm, df in keyed.items():
            if arm == "recent":
                continue
            out: dict[str, Any] = {}
            for s in PROP_STATS:
                a = df.filter((pl.col("stat") == s) & pl.col("played")).select(
                    "game_id", "player_id", pl.col("crps").alias("a")
                )
                b = base.filter((pl.col("stat") == s) & pl.col("played")).select(
                    "game_id", "player_id", pl.col("crps").alias("b")
                )
                both = a.join(b, on=["game_id", "player_id"], how="inner")
                only = a.join(b, on=["game_id", "player_id"], how="anti")
                if both.height:
                    ci = paired_score_delta_ci(
                        both["a"].to_numpy(),
                        both["b"].to_numpy(),
                        n_boot=n_boot,
                        cluster_ids=both["game_id"].to_numpy(),
                    )
                    delta = [float(ci.point), float(ci.lo), float(ci.hi)]
                else:
                    delta = []
                out[s] = {
                    "n_both": both.height,
                    "mean_crps_official_on_both": _mean(both["a"]) if both.height else None,
                    "mean_crps_recent_on_both": _mean(both["b"]) if both.height else None,
                    "delta_official_minus_recent": delta,
                    "n_only_official": only.height,
                    "mean_crps_only_official": _mean(only["a"]) if only.height else None,
                }
            res["paired_vs_recent"][arm] = out
    res["paired_by_group"] = {}
    for a_name, b_name in PAIRS:
        if a_name in keyed and b_name in keyed:
            res["paired_by_group"][f"{a_name}_vs_{b_name}"] = _pair_by_group(
                keyed[a_name], keyed[b_name], groups, n_boot
            )
    return res


def _delta(a: np.ndarray, b: np.ndarray, cl: np.ndarray, n_boot: int) -> list[float]:
    ci = paired_score_delta_ci(a, b, n_boot=n_boot, cluster_ids=cl)
    return [float(ci.point), float(ci.lo), float(ci.hi)]


def _pair_by_group(
    a: pl.DataFrame, b: pl.DataFrame, groups: pl.DataFrame, n_boot: int
) -> dict[str, Any]:
    """Rows covered by BOTH arms (players who did not play included for the P(play) Brier):
    P(play) Brier, and on played rows minutes abs-error and per-stat CRPS; deltas are
    a - b (lower is better) with a game-clustered CI, overall and per player group."""
    cols = ["game_id", "player_id"]

    def side(df: pl.DataFrame, tag: str) -> pl.DataFrame:
        pts = df.filter(pl.col("stat") == "pts").select(
            *cols,
            pl.when(pl.col("played"))
            .then((pl.col("proj_minutes") - pl.col("minutes")).abs())
            .alias(f"mae_{tag}"),
            ((pl.col("p_play") - pl.col("played").cast(pl.Float64)) ** 2).alias(f"brier_{tag}"),
        )
        for st in PROP_STATS:
            pts = pts.join(
                df.filter(pl.col("stat") == st).select(*cols, pl.col("crps").alias(f"{st}_{tag}")),
                on=cols,
                how="left",
            )
        return pts

    j = side(a, "a").join(side(b, "b"), on=cols, how="inner").join(groups, on=cols, how="left")
    j = j.with_columns(pl.col("group").fill_null("not_played"))
    out: dict[str, Any] = {}
    for g in ("all", *GROUPS):
        sub = j if g == "all" else j.filter(pl.col("group") == g)
        if sub.height < MIN_GROUP_N:
            out[g] = {"n": sub.height}
            continue
        row: dict[str, Any] = {"n": sub.height}
        for k in ("brier", "mae", *PROP_STATS):
            ka, kb = (f"{k}_a", f"{k}_b")
            ok = sub.filter(pl.col(ka).is_not_null() & pl.col(kb).is_not_null())
            if ok.height < MIN_GROUP_N:
                continue
            row[k] = {
                "n": ok.height,
                "a": _mean(ok[ka]),
                "b": _mean(ok[kb]),
                "delta_a_minus_b": _delta(
                    ok[ka].to_numpy(), ok[kb].to_numpy(), ok["game_id"].to_numpy(), n_boot
                ),
            }
        out[g] = row
    return out


def run_replay(
    db: Path,
    dates: Iterable[date] | None,
    start: date,
    end: date,
    *,
    arms: list[str],
    proxy_days: int,
    cache_root: Path,
    progress: Any = None,
) -> dict[str, Any]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        elo = load_elo_params()
        ds = list(dates) if dates is not None else slate_dates(con, start, end)
        proxy = proxy_rosters_from_first_games(con, _season_of(ds[0]), proxy_days)
        per_arm: dict[str, list[pl.DataFrame]] = {a: [] for a in arms}
        played_all: list[pl.DataFrame] = []
        groups_all: list[pl.DataFrame] = []
        for d in ds:
            gm = con.execute(
                "SELECT game_id, home_team, away_team FROM games "
                "WHERE game_date = ? AND game_id LIKE '002%' ORDER BY game_id",
                [d],
            ).pl()
            if gm.is_empty():
                continue
            tip = datetime(d.year, d.month, d.day, TIP_UTC_HOUR)
            made_at = tip - timedelta(minutes=LEAD_MIN)
            report_out = slate_report_outs(
                con, [(g, tip) for g in gm["game_id"].to_list()], made_at
            )
            actual = con.execute(
                "SELECT game_id, player_id, team_id, minutes, pts, reb, ast, fg3m "
                "FROM player_game_stats WHERE game_id IN (SELECT game_id FROM games "
                "WHERE game_date = ?)",
                [d],
            ).pl()
            played = actual.filter(pl.col("minutes") > 0)
            played_all.append(played.with_columns(pl.lit(d).alias("date")))
            groups_all.append(player_groups(con, d, played))
            for a in arms:
                pred = run_arm(con, d, gm, report_out, elo, a, proxy, cache_root)
                per_arm[a].append(
                    score_rows(pred, actual.select("game_id", "player_id", "minutes", *PROP_STATS))
                )
            if progress:
                progress(f"{d} done ({gm.height} games)")
    finally:
        con.close()
    scored = {a: pl.concat(v, how="diagonal_relaxed") for a, v in per_arm.items() if v}
    played = pl.concat(played_all)
    groups = pl.concat(groups_all)
    out = summarize(scored, played, groups)
    out["config"] = {
        "dates": [d.isoformat() for d in ds],
        "proxy_days": proxy_days,
        "proxy_players": proxy.height,
        "arms": arms,
        "db": str(db),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.props.roster_replay")
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--proxy-days", type=int, default=14)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--cache-root", type=Path, default=Path("data/rehearsal/roster_replay_cache"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.db.name == "nba.duckdb":
        print("refusing to open the production database; pass a copy under data/rehearsal/")
        return 2
    arms = [a for a in args.arms.split(",") if a]
    res = run_replay(
        args.db,
        None,
        args.start,
        args.end,
        arms=arms,
        proxy_days=args.proxy_days,
        cache_root=args.cache_root,
        progress=lambda m: print(m, flush=True),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
