# ruff: noqa: E501
"""Sizing data for the context-conditioned copula question (docs/PARLAY_ENGINE.md, "Context and cross-leg dependence").

Builds, for seasons 2020-22 ONLY, the out-of-sample (walk-forward) marginals of the production props model
(``nba.props.context_residual``, same config, same month-block refit, same integer support) and turns each
played player-game into a randomised-PIT normal score plus an "N+ at the model's median line" hit indicator.
Everything is read-only: ``nba.duckdb`` and ``nba_history.duckdb`` are opened ``read_only=True``; the only
write is one parquet per stat under ``out_dir``. Seasons 2023, 2024 (select / report seasons of any later
rule) and 2025 (the frozen holdout) are never loaded: every query carries ``season <= SIZING_MAX_SEASON``
and the loader refuses a frame that contains a later season.

This is a sizing job, not a test: nothing here is frozen, no model is promoted, and the live path is not
touched. The walk-forward reuses the production objects unchanged (``build_features``, ``ContextResidualModel``,
``raw_quantiles``, ``to_integer_support``); the only new code is the PIT bookkeeping and the context columns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.db.connect import connect_with_retry
from nba.eval.context_residual_eval import month_blocks
from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
from nba.features.injury_report import latest_pretip_flagged
from nba.props.combo_copula import KMAX, int_cdf
from nba.props.context_residual import (
    CRPS_TAUS,
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ROTATION_MIN40,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    player_states,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles

#: Hard cap: season 2022 is the newest season this module will load (2023-24 are reserved for a later rule's
#: select/report seasons, 2025 is the frozen holdout).
SIZING_MAX_SEASON = 2022
HIST_SEASONS: tuple[int, ...] = (2019, 2020, 2021)
CUR_SEASONS: tuple[int, ...] = (2022,)
TEST_SEASONS: tuple[int, ...] = (2020, 2021, 2022)
MIN_TRAIN = 1500  # as the production month-block loop
PIT_SEED = 20261014
#: A "leader" of a stat is the highest as-of per-36 rate among rotation players with at least this many long-run minutes.
LEADER_MIN40 = 20.0
PGS_COLS = "s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter"
PROD_PGS = ("game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "starter")


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def assert_sizing_seasons(df: pl.DataFrame, col: str = "season") -> None:
    """Refuse any frame containing a season after ``SIZING_MAX_SEASON``."""
    if df.height and int(df[col].max()) > SIZING_MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("seasons after 2022 are not loaded by the context-copula sizing job")


def _side(path: Path, seasons: tuple[int, ...]) -> dict[str, pl.DataFrame]:
    if max(seasons) > SIZING_MAX_SEASON:
        raise ValueError("seasons after 2022 are not loaded by the context-copula sizing job")
    ss = ",".join(str(int(s)) for s in seasons)
    con = connect_with_retry(path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season IN ({ss}) AND season <= {SIZING_MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            f"SELECT {PGS_COLS} FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season IN ({ss}) AND g.season <= {SIZING_MAX_SEASON}"
        ).pl()
        static = con.execute(
            "SELECT player_id, position, birth_date, draft_year FROM players_static"
        ).pl()
        avail = con.execute(
            "SELECT s.game_id, s.player_id, s.status, s.as_of, s.source FROM player_availability s "
            f"JOIN games g USING (game_id) WHERE g.season IN ({ss}) AND g.season <= {SIZING_MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert_sizing_seasons(games)
    return {"games": games, "pgs": pgs, "static": static, "avail": avail}


@dataclass
class SizingWorld:
    games: pl.DataFrame
    pgs: pl.DataFrame
    static: pl.DataFrame
    flagged: dict[str, set[int]]
    game_info: dict[str, tuple[Any, int, int]]
    elo: dict[str, float]
    cfg: ContextResidualConfig


def load_world(root: Path, config_dir: Path) -> SizingWorld:
    """Both databases read-only, seasons 2019-2022; OUT sets from the official pre-tip report gated on REAL tips
    (history games with no real tip contribute no report rows: no proxy fallback), as in the history-window runs."""
    cur = _side(root / "nba.duckdb", CUR_SEASONS)
    hist = _side(root / "data/history/nba_history.duckdb", HIST_SEASONS)
    overlap = set(cur["games"]["game_id"].to_list()) & set(hist["games"]["game_id"].to_list())
    if overlap:
        raise RuntimeError(f"{len(overlap)} game_id(s) present in both databases")
    games = pl.concat([cur["games"], hist["games"]], how="vertical_relaxed")
    pgs = pl.concat([cur["pgs"], hist["pgs"]], how="vertical_relaxed")
    static = pl.concat([cur["static"], hist["static"]], how="vertical_relaxed").unique(
        "player_id", keep="first", maintain_order=True
    )
    avail = pl.concat([cur["avail"], hist["avail"]], how="vertical_relaxed")
    elo = {
        k: float(v)
        for k, v in yaml.safe_load((config_dir / "mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load((config_dir / "context_residual.yaml").read_text()).get("model", {})
    )
    tips = pl.concat(
        [
            build_game_tipoff(root / "data/schedule"),
            build_game_tipoff(root / "data/history/schedule"),
        ],
        how="vertical_relaxed",
    ).unique("game_id", keep="first", maintain_order=True)
    rows = (
        avail.filter(
            pl.col("source").is_in(list(cfg.report.sources)) & pl.col("player_id").is_not_null()
        )
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            "player_id",
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            "game_date",
        )
    )
    rows = attach_real_tips(rows, tips)
    hist_ids = sorted(set(hist["games"]["game_id"].to_list()))
    rows = rows.filter(~(pl.col("tip_et").is_null() & pl.col("game_id").is_in(hist_ids)))
    flagged, _used = latest_pretip_flagged(rows, cfg.report)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    return SizingWorld(games, pgs, static, flagged, info, elo, cfg)


# --------------------------------------------------------------------------- leader-out context


def out_rotation_rows(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[Any, int, int]],
) -> pl.DataFrame:
    """One row per (game, team, OUT rotation player) with the as-of state the production ``vacated_features`` uses
    (state after the player's latest game strictly before the game date; rotation = ``min40 >= ROTATION_MIN40``
    and ``>= MIN_PRIOR_PLAYED`` prior games). Same selection as ``vacated_features``; identities are kept."""
    post = states["post"]
    from nba.props.context_residual import _CI, _DI

    def sel(h: float, col: str) -> np.ndarray:
        return np.asarray(post[:, _DI[h], _CI[col]])

    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": sel(40.0, "min"),
            "min10": sel(10.0, "min"),
            **{f"v_{s}": sel(10.0, s) for s in PROP_STATS},
        }
    ).sort("game_date")
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    left = pl.DataFrame(
        recs,
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date},
        orient="row",
    )
    left = left.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
    j = left.join_asof(
        st.rename({"game_date": "_pd"}),
        left_on="_key",
        right_on="_pd",
        by="player_id",
        strategy="backward",
    ).filter((pl.col("n_after") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= ROTATION_MIN40))
    gi = pl.DataFrame(
        [(g, v[1], v[2]) for g, v in game_info.items()],
        schema={"game_id": pl.Utf8, "home": pl.Int64, "away": pl.Int64},
        orient="row",
    )
    return j.join(gi, on="game_id").filter(
        (pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away"))
    )


def leader_out_flags(feats: pl.DataFrame, out_rows: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): ``lead_out_<stat>`` = 1 if the team's highest as-of per-36 rate in the stat (among
    rotation players with ``min40 >= LEADER_MIN40``) belongs to an OUT player, 0 if it belongs to a player who
    played, null when the team has no eligible player on either list or no report exists.

    Per-possession rates are not in the feature frame; per-36 minutes is the stand-in (stated in the report)."""
    present = feats.filter(
        (pl.col("n_prior") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= LEADER_MIN40)
    )
    outs: list[pl.DataFrame] = []
    for s in PROP_STATS:
        p_best = present.group_by(["game_id", "team_id"]).agg(
            (36.0 * pl.col(f"m10_{s}") / pl.col("min10").clip(lower_bound=1.0))
            .max()
            .alias(f"p_{s}")
        )
        o = out_rows.filter(pl.col("min40") >= LEADER_MIN40)
        o_best = o.group_by(["game_id", "team_id"]).agg(
            (36.0 * pl.col(f"v_{s}") / pl.col("min10").clip(lower_bound=1.0)).max().alias(f"o_{s}")
        )
        outs.append(p_best.join(o_best, on=["game_id", "team_id"], how="full", coalesce=True))
    base = outs[0]
    for o in outs[1:]:
        base = base.join(o, on=["game_id", "team_id"], how="full", coalesce=True)
    exprs = [
        pl.when(pl.col(f"o_{s}").is_null())
        .then(0)
        .when(pl.col(f"p_{s}").is_null())
        .then(1)
        .otherwise((pl.col(f"o_{s}") > pl.col(f"p_{s}")).cast(pl.Int8))
        .alias(f"lead_out_{s}")
        for s in PROP_STATS
    ]
    return base.select("game_id", "team_id", *exprs)


# --------------------------------------------------------------------------- walk-forward PIT


def median_line(cdf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per row the integer threshold ``t >= 1`` whose model ``P(Y >= t) = 1 - F(t-1)`` is closest to 0.5, and that probability."""
    sf = 1.0 - np.concatenate(
        [np.zeros((cdf.shape[0], 1)), cdf[:, :-1]], axis=1
    )  # P(Y >= t), t = 0..kmax
    sf[:, 0] = 2.0  # t = 0 is not a leg (always hits)
    t = np.argmin(np.abs(sf - 0.5), axis=1)
    return t, sf[np.arange(len(t)), t]


def walk_stat(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    t0: float | None = None,
) -> pl.DataFrame:
    """Production month-block walk-forward of one stat; one row per scored (game, player).

    Columns: game_id, player_id, season, y, lo, hi (integer-grid CDF just below / at y), z (normal score of the
    randomised PIT, fixed seed), pred_mean, line, p_line, hit (y >= line)."""
    if max(test_seasons) > SIZING_MAX_SEASON:
        raise ValueError("seasons after 2022 are not loaded by the context-copula sizing job")
    t0 = time.time() if t0 is None else t0
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    names = stat_feature_names(stat)
    kmax = KMAX[stat]
    rng = np.random.default_rng(PIT_SEED + PROP_STATS.index(stat))
    parts: list[pl.DataFrame] = []
    from scipy.special import ndtri

    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = test_all.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        _fit, cal = split_calibration(train, cfg)
        if cal.is_empty():
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        assert model.z_sorted is not None
        q = raw_quantiles(c, s, model.z_sorted, CRPS_TAUS)
        qi = to_integer_support(q)
        cdf = int_cdf(qi, kmax)
        y = np.clip(block["y"].to_numpy().astype(np.int64), 0, kmax)
        n = len(y)
        hi = cdf[np.arange(n), y]
        lo = np.where(y > 0, cdf[np.arange(n), np.maximum(y - 1, 0)], 0.0)
        u = np.clip(lo + rng.random(n) * (hi - lo), 1e-6, 1.0 - 1e-6)
        line, p_line = median_line(cdf)
        parts.append(
            pl.DataFrame(
                {
                    "game_id": block["game_id"].to_numpy(),
                    "player_id": block["player_id"].to_numpy(),
                    "season": block["season"].to_numpy(),
                    "y": block["y"].to_numpy(),
                    "lo": lo,
                    "hi": hi,
                    "z": ndtri(u),
                    "pred_mean": np.clip(c + s * float(model.z_sorted.mean()), 0.0, None),
                    "line": line.astype(np.int64),
                    "p_line": p_line,
                    "hit": (block["y"].to_numpy() >= line).astype(np.int8),
                }
            )
        )
        _log(t0, f"{stat} block {b + 1} {start} train={train.height} test={block.height}")
    return pl.concat(parts)


CONTEXT_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "game_date",
    "is_home_f",
    "n_prior",
    "min10",
    "min40",
    "starter10",
    "exp_margin",
    "abs_margin",
    "r20_pts_for",
    "opp_r20_pts_for",
    "has_report",
    "n_out_rot",
    "vac_min",
    *[f"vac_{s}" for s in PROP_STATS],
    *[f"m10_{s}" for s in PROP_STATS],
    *[f"lead_out_{s}" for s in PROP_STATS],
)


def build_all(
    root: Path, config_dir: Path, out_dir: Path, stats: tuple[str, ...] = PROP_STATS
) -> None:
    """Build the context frame and one walk-forward PIT parquet per stat under ``out_dir`` (resumable: a stat
    whose parquet exists is skipped). Writes only under ``out_dir``."""
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    w = load_world(root, config_dir)
    _log(t0, f"loaded games={w.games.height} pgs={w.pgs.height} reported_games={len(w.flagged)}")
    feats = build_features(w.games, w.pgs.select(list(PROD_PGS)), w.static, w.flagged, w.elo)
    assert_sizing_seasons(feats)
    _log(t0, f"features rows={feats.height}")
    g2 = w.games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        w.pgs.select(list(PROD_PGS))
        .join(g2.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = player_states(played)
    out_rows = out_rotation_rows(played, states, w.flagged, w.game_info)
    lead = leader_out_flags(feats, out_rows)
    ctx = (
        feats.join(lead, on=["game_id", "team_id"], how="left")
        .select(list(CONTEXT_COLS))
        .join(
            w.games.select("game_id", "home_team", "home_pts", "away_pts"),
            on="game_id",
            how="left",
        )
    )
    # a reported game with no OUT rotation player: explicit zero; no report: null (never imputed)
    ctx = ctx.with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(f"lead_out_{s}").fill_null(0))
            .otherwise(None)
            .alias(f"lead_out_{s}")
            for s in PROP_STATS
        ]
    )
    ctx.write_parquet(out_dir / "context.parquet")
    _log(t0, f"context rows={ctx.height}")
    for stat in stats:
        path = out_dir / f"pit_{stat}.parquet"
        if path.exists():
            _log(t0, f"skip {stat} (exists)")
            continue
        res = walk_stat(feats, stat, w.cfg, TEST_SEASONS, t0)
        assert_sizing_seasons(res)
        res.write_parquet(path)
        _log(t0, f"wrote {path} rows={res.height}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Build 2020-22 walk-forward PIT residuals (read-only on the stores)."
    )
    ap.add_argument(
        "--root", required=True, help="checkout holding nba.duckdb and data/ (read-only)"
    )
    ap.add_argument("--configs", default="configs")
    ap.add_argument(
        "--out", required=True, help="output directory (parquet files are written here only)"
    )
    ap.add_argument("--stats", default=",".join(PROP_STATS))
    a = ap.parse_args(argv)
    build_all(Path(a.root), Path(a.configs), Path(a.out), tuple(a.stats.split(",")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
