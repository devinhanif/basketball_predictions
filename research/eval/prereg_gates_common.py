# ruff: noqa: E501
"""Shared helpers for the 2026-10-12 minimum-data gates (non-negotiable 8).

Used by ``returning_teammate_gate``, ``expected_absence_gate``, ``roster_continuity_gate`` and
``questionable_streak_gate``. Everything here is outcome-free or reads stored forecasts only; no
candidate arm is fitted or scored. ``nba.duckdb`` is opened read-only and every query caps
``season <= 2024`` (2025 is the frozen holdout and is never loaded).

Sizing conventions (one place, so the four drafts agree):

* ``cluster_stats``: per-row sd, design effect by cluster, cluster count (ratio-of-sums form of the
  clustered standard error, as ``full_shadow_live_rehearse``).
* ``halfwidth``: two-sided 95% half-width ``1.96 * sd * sqrt(deff / n)``.
* ``pass_power``: P(point estimate <= threshold) when the true effect is ``effect`` and the standard
  error is ``se`` (normal approximation). ``sizing_row`` uses threshold ``min(floor, -halfwidth)``:
  the pass line needs the floor AND a CI upper bound below 0, so this is the power of the
  pre-registered pass line in one season; both seasons must pass, so the rule's power is roughly
  the square.
* ``expected_crps_gain``: if the hypothesised forecast ``G`` were the truth, the expected integer
  CRPS gain of ``G`` over the comparator ``F`` equals ``sum_k (F(k) - G(k))^2`` on the integer
  support; computed here with ``G``'s own quantile atoms as the outcome sample (no outcomes read).
* ``bernoulli_gain``: the same for a probability, ``KL(p_new || p_old)`` per row, plus the per-row
  sd of the log-loss difference under ``y ~ Bernoulli(p_new)``.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.stats import norm

from nba.props.context_residual import CRPS_TAUS

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "nba.duckdb"
MAX_SEASON = 2024
SEASONS = (2023, 2024)
REPORT_SOURCE = "nba_official_report"
LEAD_MINUTES = 60
PROXY_TIP_HOUR = 19


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB), read_only=True)


def query(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> pl.DataFrame:
    """Run ``sql`` and return polars; refuses SQL that does not cap the season at 2024."""
    if "season <= 2024" not in sql:
        raise RuntimeError("every gate query must cap `season <= 2024` (2025 is the holdout)")
    return con.execute(sql, params or []).pl()


def load_games(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """games (season <= 2024) with the real scheduled tip (naive ET), proxy 19:00 ET where missing."""
    from nba.features.game_tipoff import build_game_tipoff

    g = query(
        con,
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE season <= 2024",
    )
    tips = build_game_tipoff().select("game_id", "tip_et")
    g = g.join(tips, on="game_id", how="left")
    proxy = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(hours=PROXY_TIP_HOUR)
    return g.with_columns(
        pl.col("tip_et").is_not_null().alias("tip_real"),
        pl.coalesce(pl.col("tip_et"), proxy).alias("tip"),
    )


def load_report(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Every official-report row of a season <= 2024 game: game_id, player_id, status, as_of."""
    return query(
        con,
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
        "CAST(a.as_of AS TIMESTAMP) AS as_of FROM player_availability a "
        "JOIN games g ON g.game_id = a.game_id "
        f"WHERE a.source = '{REPORT_SOURCE}' AND a.player_id IS NOT NULL AND g.season <= 2024",
    )


def latest_t60(report: pl.DataFrame, games: pl.DataFrame, lead: int = LEAD_MINUTES) -> pl.DataFrame:
    """Per (game, player): status in the LATEST snapshot stamped ``<= tip - lead`` for that game.

    Same rule as ``nba.features.injury_report.latest_pretip_flagged``: only rows of the latest usable
    snapshot count (a player absent from it is unlisted, not out). Games with no usable snapshot are
    absent (never imputed). Columns: game_id, player_id, status, snap_as_of."""
    r = report.join(games.select("game_id", "tip"), on="game_id", how="inner")
    r = r.filter(pl.col("as_of") <= pl.col("tip") - pl.duration(minutes=lead))
    latest = r.group_by("game_id").agg(pl.col("as_of").max().alias("snap_as_of"))
    snap = r.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("snap_as_of"))
    return snap.select("game_id", "player_id", "status", "snap_as_of").unique(
        ["game_id", "player_id"], keep="last"
    )


def load_box(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Box rows (played and DNP) with date and season, season <= 2024."""
    return query(
        con,
        "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.starter, g.game_date, g.season "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024",
    )


def team_game_index(box: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """(team_id, season, game_id, game_date, tip, k): k = 0-based order of the team's games in season."""
    tg = box.select("team_id", "game_id", "season", "game_date").unique()
    tg = tg.join(games.select("game_id", "tip"), on="game_id", how="left")
    tg = tg.sort(["team_id", "season", "tip", "game_id"])
    return tg.with_columns(pl.int_range(pl.len()).over(["team_id", "season"]).alias("k"))


# --------------------------------------------------------------------------- sizing


def cluster_stats(d: np.ndarray, key: np.ndarray) -> dict[str, float]:
    """sd per row, design effect and cluster count of the clustered mean of ``d``."""
    d = np.asarray(d, dtype=float)
    _, inv = np.unique(np.asarray(key).astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    n, mu = len(d), float(d.mean())
    s = np.bincount(inv, weights=d - mu, minlength=g)
    se = math.sqrt(g / (g - 1) * float((s**2).sum())) / n
    sd = float(d.std(ddof=1))
    deff = (se * math.sqrt(n) / sd) ** 2 if sd > 0 else float("nan")
    return {"n": float(n), "sd_row": sd, "deff": deff, "clusters": float(g), "se": se}


def halfwidth(sd: float, deff: float, n: float) -> float:
    return 1.96 * sd * math.sqrt(deff / n)


def pass_power(effect: float, floor: float, se: float) -> float:
    """P(estimate <= floor) under a normal sampling distribution centred on ``effect``."""
    return float(norm.cdf((floor - effect) / se))


def sizing_row(
    name: str, sd: float, deff: float, n: float, floor: float, effect: float | None
) -> dict[str, Any]:
    se = sd * math.sqrt(deff / n)
    hw = 1.96 * se
    out: dict[str, Any] = {
        "name": name,
        "n": n,
        "sd_row": sd,
        "deff": deff,
        "se": se,
        "halfwidth95": hw,
        "floor": floor,
        "hw_le_floor": bool(hw <= abs(floor)),
        "n_for_hw_at_floor": (1.96 * sd * math.sqrt(deff) / abs(floor)) ** 2,
        "expected_effect": effect,
    }
    if effect is not None:
        # pass line = estimate <= floor AND CI upper < 0, i.e. estimate <= min(floor, -halfwidth)
        p1 = pass_power(effect, min(floor, -hw), se)
        out["power_one_season"] = p1
        out["power_both_seasons"] = p1 * p1
    return out


def expected_crps_gain(q_old: np.ndarray, q_new: np.ndarray, chunk: int = 400) -> np.ndarray:
    """Per row: E_{Y~G}[CRPS(F, Y) - CRPS(G, Y)] with F, G given as integer quantile grids on
    ``CRPS_TAUS`` and Y drawn from G's own quantile atoms (equal mass). Positive = G better if G is
    the truth. Outcome-free."""
    taus = CRPS_TAUS
    out = np.empty(len(q_old))
    for a in range(0, len(q_old), chunk):
        f = q_old[a : a + chunk].astype(float)
        g = q_new[a : a + chunk].astype(float)
        y = g[:, :, None]  # atoms on axis 1, taus on axis 2

        def pin(q: np.ndarray, y: np.ndarray = y) -> np.ndarray:
            d = y - q[:, None, :]
            return np.asarray(np.where(d >= 0, taus * d, (taus - 1.0) * d).mean(axis=2) * 2.0)

        out[a : a + chunk] = (pin(f) - pin(g)).mean(axis=1)
    return out


def bernoulli_gain(
    p_old: np.ndarray, p_new: np.ndarray, eps: float = 1e-6
) -> dict[str, np.ndarray]:
    """Per row, if ``p_new`` were the truth: expected log-loss gain KL(p_new || p_old) (>= 0) and the
    sd of the per-row log-loss difference (new minus old) under y ~ Bernoulli(p_new)."""
    a = np.clip(np.asarray(p_old, float), eps, 1 - eps)
    b = np.clip(np.asarray(p_new, float), eps, 1 - eps)
    l1 = np.log(b / a)  # y = 1: old - new log loss
    l0 = np.log((1 - b) / (1 - a))  # y = 0
    kl = b * l1 + (1 - b) * l0
    var = b * (1 - b) * (l1 - l0) ** 2
    return {"kl": kl, "var": var}


def now_cdt() -> str:
    return dt.datetime.now().astimezone().strftime("%a %b %d %H:%M %Z %Y")
