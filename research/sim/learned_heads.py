"""Adapter: draw rung-3 sim possession outcomes from the rung-4 learned step heads.

``nba.sim.engine.simulate_game`` normally builds each team's outcome
distribution from log5 expected PPP + an exponential tilt of the empirical
league outcome shape. This module (default OFF -- nothing imports it unless
asked) replaces that step with the learned ``outcome`` head of a trained
``research.models.rung4_stepheads`` net, via the engine's
``home_probs_override`` / ``away_probs_override`` hook:

    heads = LearnedHeads.from_artifact("data/colab/artifacts/<ts>")
    inputs = build_game_head_inputs(con, game_ids, heads.feature_columns)
    probs = heads.game_outcome_probs(inputs)       # one batched forward pass
    simulate_game(..., outcome_support=OUTCOME_POINTS,
                  home_probs_override=probs[g][0], away_probs_override=probs[g][1])

Prediction-time inputs (no lineup cheating; strictly as-of):

- Team ratings: ``build_team_possession_rates`` rows for the target game
  (already strictly prior).
- Lineups: the sim has no real on-court lineups at tip-off, so each side's
  lineup distribution is its own *previous game's* possession-weighted
  lineups (known before tip), crossed with the opponent's previous-game
  defensive lineups; ``n_pairs`` (offense, defense) pairs are drawn per side
  and their step-head outputs averaged. Each player's stat vector is the
  as-of vector from that previous appearance (so it excludes that game's own
  possessions -- stale by at most one game). Injuries/rest are NOT modeled.
- In-game state is fixed at a neutral mid-game tie (period 2, 6:00,
  score_diff 0) exactly like ``StepHeadsRung.predict``; the net is trained on
  all game states, so this is an approximation of the game-average rate.

Scope notes (honest): the points distribution depends only on the
``outcome`` head (FGM2/FGM3/FT_trip/miss/TOV/other -> 2/3/FT/0). ``make`` is
implied by the outcome class, so :meth:`LearnedHeads.sample_possessions`
draws outcome -> zone (zone head, shot attempts) -> rebound (rebound head,
misses) and reports ``made`` from the outcome; the standalone ``make`` head
is exposed in :meth:`LearnedHeads.head_probabilities` only for diagnostics.
``FT_trip`` points are a constant 1.45 (no FT head), so FT-driven variance is
understated.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import torch

from nba.features.possession_features import (
    build_possession_matchup_features,
    build_team_possession_rates,
)
from nba.sim.engine import GameSimResult, simulate_game
from research.features.possession_step_features import (
    OUTCOME_CLASSES,
    PLAYER_ASOF_STATS,
    PLAYER_POOL_AGGS,
    PLAYER_STAT_PRIORS,
    POSSESSION_STEP_NEUTRAL,
    SHOT_ATTEMPT_OUTCOMES,
    player_asof_sql,
)
from research.models.rung4_stepheads import _OUTCOME_POINT_VALUE_VEC, StepHeadsRung

#: Point value per outcome class (class order = ``OUTCOME_CLASSES``); the
#: ``outcome_support`` handed to the engine when overriding probabilities.
OUTCOME_POINTS: np.ndarray = np.asarray(_OUTCOME_POINT_VALUE_VEC, dtype=float)

_NEUTRAL_PERIOD, _NEUTRAL_CLOCK, _NEUTRAL_SCORE_DIFF = 2.0, 360.0, 0.0
_SHOT_IDX = np.array([OUTCOME_CLASSES.index(o) for o in SHOT_ATTEMPT_OUTCOMES])
_MISS_IDX = OUTCOME_CLASSES.index("FGA_miss")


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return np.asarray(e / e.sum(axis=-1, keepdims=True))


class LearnedHeads:
    """Batched numpy view of a trained step-heads net (no autograd, no per-row loops)."""

    def __init__(self, rung: StepHeadsRung) -> None:
        if rung.net is None or rung.feature_mean_ is None or rung.feature_std_ is None:
            raise ValueError("rung has no trained net")
        self.net = rung.net.eval()
        self.feature_columns: list[str] = list(rung.feature_columns)
        self._mean = rung.feature_mean_
        self._std = rung.feature_std_

    @classmethod
    def from_artifact(cls, artifact_dir: str | Path) -> LearnedHeads:
        return cls(StepHeadsRung.load_weights(artifact_dir))

    def head_probabilities(self, raw: np.ndarray) -> dict[str, np.ndarray]:
        """Head outputs for raw (un-standardized) feature rows ``(N, n_cols)``."""
        x = torch.tensor((raw - self._mean) / self._std, dtype=torch.float32)
        with torch.no_grad():
            out = self.net(x)
        return {
            "outcome": _softmax(out["outcome_logits"].numpy().astype(np.float64)),
            "zone": _softmax(out["zone_logits"].numpy().astype(np.float64)),
            "make": 1.0 / (1.0 + np.exp(-out["make_logit"].numpy().astype(np.float64))),
            "rebound": 1.0 / (1.0 + np.exp(-out["rebound_logit"].numpy().astype(np.float64))),
            "duration": out["duration"].numpy().astype(np.float64),
        }

    def sample_possessions(
        self, raw: np.ndarray, rng: np.random.Generator, n_draws: int = 1
    ) -> dict[str, np.ndarray]:
        """Vectorized draws of outcome / zone / made / oreb for each context row.

        Returns arrays shaped ``(N, n_draws)``: ``outcome`` (class index),
        ``zone`` (index into ``ZONE_CLASSES``, -1 for non-shot rows), ``made``
        (bool, implied by the outcome class), ``oreb`` (bool, only drawn on
        FGA_miss rows, else False).
        """
        hp = self.head_probabilities(raw)
        n = raw.shape[0]
        cdf = np.cumsum(hp["outcome"], axis=1)
        u = rng.random((n, n_draws))
        outcome = (u[:, :, None] > cdf[:, None, :-1]).sum(axis=2)
        zcdf = np.cumsum(hp["zone"], axis=1)
        uz = rng.random((n, n_draws))
        zone = (uz[:, :, None] > zcdf[:, None, :-1]).sum(axis=2)
        is_shot = np.isin(outcome, _SHOT_IDX)
        zone = np.where(is_shot, zone, -1)
        made = is_shot & (outcome != _MISS_IDX)
        oreb = (outcome == _MISS_IDX) & (rng.random((n, n_draws)) < hp["rebound"][:, None])
        return {"outcome": outcome, "zone": zone, "made": made, "oreb": oreb}

    def game_outcome_probs(self, inputs: dict[str, GameHeadInputs]) -> dict[str, np.ndarray]:
        """Per game ``(2, 6)`` array [home, away] of outcome probs (ONE batched forward pass)."""
        ids = list(inputs)
        if not ids:
            return {}
        blocks = []
        for gid in ids:
            gi = inputs[gid]
            blocks.extend([gi.home_rows, gi.away_rows])
        raw = np.concatenate(blocks, axis=0)
        probs = self.head_probabilities(raw)["outcome"]
        out: dict[str, np.ndarray] = {}
        pos = 0
        for gid in ids:
            gi = inputs[gid]
            nh, na = gi.home_rows.shape[0], gi.away_rows.shape[0]
            out[gid] = np.stack(
                [probs[pos : pos + nh].mean(axis=0), probs[pos + nh : pos + nh + na].mean(axis=0)]
            )
            pos += nh + na
        return out


@dataclass
class GameHeadInputs:
    """Raw feature rows (columns = ``feature_columns``) for both sides of one game."""

    home_rows: np.ndarray
    away_rows: np.ndarray


def _pool_lineups(stats: np.ndarray) -> np.ndarray:
    """``(M, 5, n_stats)`` -> ``(M, n_stats * 3)`` mean/max/min, stat-major (column order)."""
    pooled = np.stack([stats.mean(axis=1), stats.max(axis=1), stats.min(axis=1)], axis=2)
    return pooled.reshape(stats.shape[0], -1)


def build_game_head_inputs(
    con: duckdb.DuckDBPyConnection,
    game_ids: list[str],
    feature_columns: list[str],
    n_pairs: int = 24,
    seed: int = 0,
) -> dict[str, GameHeadInputs]:
    """Strictly-as-of feature rows for ``game_ids`` (see module docstring).

    Games whose teams have no previous game with a lineup (season openers
    with no history) are returned with neutral (league-constant) lineups.
    """
    games = con.execute(
        "SELECT game_id, game_date, home_team, away_team FROM games ORDER BY game_date, game_id"
    ).pl()
    long = pl.concat(
        [
            games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    long = long.with_columns(prev_game=pl.col("game_id").shift(1).over("team_id"))
    prev = {(r[0], r[1]): r[2] for r in long.select("game_id", "team_id", "prev_game").iter_rows()}

    want = [g for g in game_ids]
    gsub = games.filter(pl.col("game_id").is_in(want))
    team_rows: dict[tuple[str, int], dict[str, float]] = {
        (r["game_id"], r["team_id"]): r
        for r in build_team_possession_rates(con).iter_rows(named=True)
    }

    # lineups of previous games
    need = set()
    for r in gsub.iter_rows(named=True):
        for t in (r["home_team"], r["away_team"]):
            pg = prev.get((r["game_id"], t))
            if pg is not None:
                need.add((pg, t))
    lineups: dict[tuple[str, int, str], list[tuple[list[int], int]]] = {}
    if need:
        w = pl.DataFrame(sorted(need), schema=["game_id", "team_id"], orient="row")
        con.register("want_lineups", w)
        try:
            for side, tcol, pcol in (
                ("off", "off_team", "off_players"),
                ("def", "def_team", "def_players"),
            ):
                rows = con.execute(
                    f"SELECT p.game_id, p.{tcol}, p.{pcol}, COUNT(*) FROM possessions p "
                    f"JOIN want_lineups w ON w.game_id = p.game_id AND w.team_id = p.{tcol} "
                    f"WHERE p.{pcol} IS NOT NULL GROUP BY 1, 2, 3"
                ).fetchall()
                for gid, tid, pl_, c in rows:
                    lineups.setdefault((gid, tid, side), []).append((list(pl_), int(c)))
        finally:
            con.unregister("want_lineups")
    # player stat table (as-of each player-game)
    pa = con.execute(player_asof_sql()).pl()
    stat_cols = [f"pa_{s}" for s in PLAYER_ASOF_STATS]
    pa_mat = pa.select(stat_cols).to_numpy().astype(np.float64)
    pa_idx = {(g, p): i for i, (g, p) in enumerate(pa.select("game_id", "player_id").iter_rows())}
    prior_vec = np.array([PLAYER_STAT_PRIORS[s] for s in PLAYER_ASOF_STATS])

    def pooled(game_id: str | None, team: int, side: str, rng: np.random.Generator) -> np.ndarray:
        """``(n_pairs, n_stats*3)`` pooled features for ``n_pairs`` weighted lineup draws."""
        lu = lineups.get((game_id, team, side)) if game_id is not None else None
        if not lu:
            return np.tile(
                np.array(
                    [PLAYER_STAT_PRIORS[s] for s in PLAYER_ASOF_STATS for _ in PLAYER_POOL_AGGS]
                ),
                (n_pairs, 1),
            )
        w = np.array([c for _, c in lu], dtype=float)
        pick = rng.choice(len(lu), size=n_pairs, p=w / w.sum())
        stats = np.empty((n_pairs, 5, len(PLAYER_ASOF_STATS)))
        for i, k in enumerate(pick):
            for j, p in enumerate(lu[k][0][:5]):
                ix = pa_idx.get((game_id, p))
                stats[i, j] = pa_mat[ix] if ix is not None else prior_vec
        return _pool_lineups(stats)

    out: dict[str, GameHeadInputs] = {}
    for r in gsub.iter_rows(named=True):
        gid = r["game_id"]
        rng = np.random.default_rng([seed, int.from_bytes(gid.encode()[-6:], "big")])
        sides = {}
        for key, off_t, def_t, is_home in (
            ("home", r["home_team"], r["away_team"], 1.0),
            ("away", r["away_team"], r["home_team"], 0.0),
        ):
            o, d = team_rows[(gid, off_t)], team_rows[(gid, def_t)]
            base = {
                "period": _NEUTRAL_PERIOD,
                "clock_start": _NEUTRAL_CLOCK,
                "score_diff": _NEUTRAL_SCORE_DIFF,
                "off_is_home": is_home,
                "off_off_rtg_prior": o["off_rtg_prior"],
                "off_def_rtg_prior": o["def_rtg_prior"],
                "off_pace_prior": o["pace_prior"],
                "def_off_rtg_prior": d["off_rtg_prior"],
                "def_def_rtg_prior": d["def_rtg_prior"],
                "league_avg_ppp_asof": o["league_avg_ppp_asof"],
            }
            off_pool = pooled(prev.get((gid, off_t)), off_t, "off", rng)
            def_pool = pooled(prev.get((gid, def_t)), def_t, "def", rng)
            cols = []
            for c in feature_columns:
                if c in base:
                    cols.append(np.full(n_pairs, base[c]))
                elif c in POSSESSION_STEP_NEUTRAL:
                    side, rest = c.split("_pa_", 1)
                    stat, agg = rest.rsplit("_", 1)
                    ix = PLAYER_ASOF_STATS.index(stat) * len(
                        PLAYER_POOL_AGGS
                    ) + PLAYER_POOL_AGGS.index(agg)
                    cols.append((off_pool if side == "off" else def_pool)[:, ix])
                else:
                    raise KeyError(c)
            sides[key] = np.column_stack(cols)
        out[gid] = GameHeadInputs(home_rows=sides["home"], away_rows=sides["away"])
    return out


# --------------------------------------------------------------------------
# Validation-window comparison vs the rung-3 sim (directional)
# --------------------------------------------------------------------------


def asof_outcome_points(con: duckdb.DuckDBPyConnection, before_date: str) -> np.ndarray:
    """Mean realized points per trip by outcome class, over games strictly before ``before_date``.

    A trip's ``pts`` is not exactly 2/3 for made shots (and-ones, put-backs
    after an offensive rebound) nor 1.45 for ``FT_trip``; using the as-of
    empirical mean per class keeps the sim's scoring level unbiased. Classes
    with no history fall back to ``OUTCOME_POINTS``.
    """
    rows = con.execute(
        "SELECT p.outcome, AVG(p.pts) FROM possessions p JOIN games g ON g.game_id = p.game_id "
        "WHERE g.game_date < CAST(? AS DATE) AND p.pts IS NOT NULL GROUP BY 1",
        [before_date],
    ).fetchall()
    got = {o: float(v) for o, v in rows}
    return np.array([got.get(c, OUTCOME_POINTS[i]) for i, c in enumerate(OUTCOME_CLASSES)])


def _normal_crps(mu: np.ndarray, sd: np.ndarray, y: np.ndarray) -> np.ndarray:
    from scipy.stats import norm

    sd = np.maximum(sd, 1e-6)
    z = (y - mu) / sd
    return np.asarray(sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi)))


def compare_learned_vs_rung3(
    con: duckdb.DuckDBPyConnection,
    artifact_dirs: list[str],
    start_date: str | None = None,
    max_games: int = 150,
    n_sims: int = 400,
    seed: int = 0,
    n_boot: int = 2000,
) -> dict[str, object]:
    """Paired comparison on validation-window games ONLY (never season >= 2025).

    Games are drawn from ``season < 2025`` with ``game_date >= max(start_date,
    train/val cutoff of every artifact)`` so no head saw them in training
    (they may have been used for early stopping -- the usual validation
    caveat), then strided down to ``max_games``. Arms share each game's seed
    and as-of pace; CRPS uses a Normal(mean, sd) built from each arm's
    simulated margin/total (the engine returns summaries, not draws).
    """
    from nba.models.rung3_sim import PossessionSimRung
    from nba.truth.metrics import bootstrap_ci

    cutoffs = [
        json.loads((Path(a) / "config.json").read_text())["train_val_cutoff_date"]
        for a in artifact_dirs
    ]
    lo = max([start_date or "0000-01-01", *cutoffs])
    games = con.execute(
        "SELECT game_id, game_date, home_pts, away_pts FROM games "
        "WHERE season < 2025 AND game_date >= CAST(? AS DATE) AND home_pts IS NOT NULL "
        "ORDER BY game_date, game_id",
        [lo],
    ).pl()
    if games.height > max_games:
        idx = np.linspace(0, games.height - 1, max_games).round().astype(int)
        games = games[np.unique(idx).tolist()]
    gids = games["game_id"].to_list()
    feats = build_possession_matchup_features(con).filter(pl.col("game_id").is_in(gids))
    feats = games.select("game_id").join(feats, on="game_id", how="inner")
    gids = feats["game_id"].to_list()
    games = games.filter(pl.col("game_id").is_in(gids))
    y_margin = (games["home_pts"] - games["away_pts"]).to_numpy().astype(float)
    y_total = (games["home_pts"] + games["away_pts"]).to_numpy().astype(float)

    arms: dict[str, list[GameSimResult]] = {}
    timing: dict[str, float] = {}
    rung3 = PossessionSimRung(seed=seed, n_sims=n_sims)
    t0 = time.perf_counter()
    arms["rung3"] = rung3.simulate_full(feats)
    timing["rung3"] = (time.perf_counter() - t0) * 1000 / len(gids)

    support = asof_outcome_points(con, lo)
    pace = {
        r["game_id"]: (r["home_pace_prior"], r["away_pace_prior"])
        for r in feats.iter_rows(named=True)
    }
    for a in artifact_dirs:
        heads = LearnedHeads.from_artifact(a)
        t0 = time.perf_counter()
        inputs = build_game_head_inputs(con, gids, heads.feature_columns, seed=seed)
        t_feat = time.perf_counter() - t0
        t0 = time.perf_counter()
        probs = heads.game_outcome_probs(inputs)
        t_fwd = time.perf_counter() - t0
        t0 = time.perf_counter()
        res = []
        for i, r in enumerate(feats.iter_rows(named=True)):
            gid = r["game_id"]
            res.append(
                simulate_game(
                    r["home_off_rtg_prior"], r["home_def_rtg_prior"], pace[gid][0],
                    r["away_off_rtg_prior"], r["away_def_rtg_prior"], pace[gid][1],
                    r["league_avg_ppp_asof"], n_sims=n_sims,
                    seed=rung3._row_seed(gid, i),
                    outcome_support=support,
                    home_probs_override=probs[gid][0],
                    away_probs_override=probs[gid][1],
                )
            )  # fmt: skip
        t_sim = time.perf_counter() - t0
        name = "learned:" + Path(a).name
        arms[name] = res
        timing[name] = (t_sim + t_fwd) * 1000 / len(gids)
        timing[name + ":features(ms/game, SQL+python, one-off)"] = t_feat * 1000 / len(gids)
        timing[name + ":forward(ms/game)"] = t_fwd * 1000 / len(gids)

    def per_game(res: list[GameSimResult]) -> dict[str, np.ndarray]:
        mm = np.array([r.margin_mean for r in res])
        ms = np.array([r.margin_sd for r in res])
        tm = np.array([r.total_mean for r in res])
        ts = np.array([r.total_sd for r in res])
        p = np.clip(np.array([r.win_prob_home for r in res]), 0.01, 0.99)
        win = (y_margin > 0).astype(float)
        return {
            "margin_crps": _normal_crps(mm, ms, y_margin),
            "total_crps": _normal_crps(tm, ts, y_total),
            "win_ll": -(win * np.log(p) + (1 - win) * np.log(1 - p)),
            "margin_err": mm - y_margin,
            "total_err": tm - y_total,
        }

    pg = {k: per_game(v) for k, v in arms.items()}
    out: dict[str, object] = {
        "n_games": len(gids),
        "n_sims": n_sims,
        "window_start": lo,
        "outcome_support_asof": support.round(4).tolist(),
        "window": [str(games["game_date"].min()), str(games["game_date"].max())],
        "ms_per_game": timing,
        "arms": {},
        "paired_vs_rung3": {},
    }
    arms_out: dict[str, dict[str, float]] = {}
    paired_out: dict[str, dict[str, dict[str, float]]] = {}
    for k, v in pg.items():
        wp = np.array([r.win_prob_home for r in arms[k]])
        row = {m: float(v[m].mean()) for m in v}
        row["win_prob_sd"] = float(wp.std())
        row["margin_sd_mean"] = float(np.mean([r.margin_sd for r in arms[k]]))
        arms_out[k] = row
    for k in pg:
        if k == "rung3":
            continue
        paired_out[k] = {}
        for m in ("margin_crps", "total_crps", "win_ll"):
            ci = bootstrap_ci(pg[k][m] - pg["rung3"][m], n_boot=n_boot, seed=seed)
            paired_out[k][m] = {"delta_mean": ci.point, "lo": ci.lo, "hi": ci.hi, "n": ci.n}
    out["arms"] = arms_out
    out["paired_vs_rung3"] = paired_out
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Learned-heads sim vs rung-3 sim (validation window only)."
    )
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--artifact", action="append", required=True, help="artifact dir (repeatable)")
    ap.add_argument("--start-date", default="2025-02-01")
    ap.add_argument("--max-games", type=int, default=150)
    ap.add_argument("--n-sims", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    from nba.db.connect import connect

    con = connect(args.db, read_only=True)
    try:
        res = compare_learned_vs_rung3(
            con, args.artifact, args.start_date, args.max_games, args.n_sims, args.seed
        )
    finally:
        con.close()
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
