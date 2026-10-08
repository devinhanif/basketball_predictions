"""Is "legs from different games are independent" true? Three date-clustered checks.

(a) Cross-game same-date residual correlation for player-prop legs. Each row's outcome is turned
    into a normal score through its own predictive quantile grid (randomized PIT, seeded);
    rows are averaged within (game, stat) and the correlation of DIFFERENT games' averages on
    the SAME date is estimated.
(b) The same for game-result legs: standardized residual (y - p) / sqrt(p(1-p)) of the home-win
    probability (deterministic, primary) and the probit of the randomized Bernoulli PIT.
(c) Joint calibration of synthetic CROSS-GAME parlays (2-4 legs from distinct games on one
    date, moneylines and prop N+ legs mixed): independence-product probability vs observed hit
    rate, log loss / Brier and reliability bins.

Estimator for (a)/(b): with one centered value x_g per game and n_d games on date d,
    r = sum_d sum_{i != j} x_i x_j / sum_d (n_d - 1) sum_i x_i^2,
the pair-weighted correlation of different games on one date. Uncertainty: percentile bootstrap
resampling whole DATES (games on one night share a shock, so games are not the exchangeable
unit). Dates with a single game contribute nothing. Pure functions over arrays/frames; the
callers supply historical OOF or forward predictions. Read-only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml
from numpy.typing import NDArray
from scipy import stats

from nba.parlay.config import DEFAULT_PATH
from nba.parlay.qdist import QuantileGridDist

STATS = ("pts", "reb", "ast", "fg3m")
LADDERS: dict[str, tuple[int, ...]] = {
    "pts": (10, 15, 20, 25, 30, 35),
    "reb": (4, 6, 8, 10, 12),
    "ast": (2, 4, 6, 8, 10),
    "fg3m": (1, 2, 3, 4, 5),
}
WIN_MODEL = "rung0_injury_elo"
PROPS_MODEL = "props_context_residual"
_EPS = 1e-6


@dataclass(frozen=True)
class IndependenceCfg:
    min_forward_dates: int = 30
    flag_abs_r: float = 0.05
    n_boot: int = 2000
    ci_level: float = 0.95
    parlays_per_date: int = 25


def load_independence_cfg(path: str | Path = DEFAULT_PATH) -> IndependenceCfg:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    d = raw.get("independence_check") or {}
    base = IndependenceCfg()
    return IndependenceCfg(
        int(d.get("min_forward_dates", base.min_forward_dates)),
        float(d.get("flag_abs_r", base.flag_abs_r)),
        int(d.get("n_boot", base.n_boot)),
        float(d.get("ci_level", base.ci_level)),
        int(d.get("parlays_per_date", base.parlays_per_date)),
    )


@dataclass(frozen=True)
class CorrCI:
    r: float
    lo: float
    hi: float
    n_games: int
    n_dates: int  # dates with >= 2 games (the ones that carry information)
    n_pairs: int

    @property
    def excludes_zero(self) -> bool:
        return bool(np.isfinite(self.lo) and (self.lo > 0.0 or self.hi < 0.0))

    def flagged(self, abs_r: float) -> bool:
        return bool(np.isfinite(self.r) and self.excludes_zero and abs(self.r) > abs_r)


def _codes(x: Sequence[Any] | NDArray[Any]) -> NDArray[np.int64]:
    _, inv = np.unique(np.asarray(x), return_inverse=True)
    return np.asarray(inv, dtype=np.int64).ravel()


def cross_game_corr(
    values: NDArray[np.float64],
    dates: Sequence[Any] | NDArray[Any],
    *,
    n_boot: int = 2000,
    ci_level: float = 0.95,
    seed: int = 0,
) -> CorrCI:
    """Pair-weighted correlation of different games' values on the same date (see module doc).

    ``values`` holds ONE value per game (already game-averaged); ``dates`` the game dates.
    """
    x = np.asarray(values, dtype=float)
    ok = np.isfinite(x)
    x = x[ok]
    inv = _codes(np.asarray(dates)[ok])
    nan = float("nan")
    if x.size < 3:
        return CorrCI(nan, nan, nan, int(x.size), 0, 0)
    x = x - x.mean()
    nd = int(inv.max()) + 1
    cnt = np.bincount(inv, minlength=nd).astype(float)
    s1 = np.bincount(inv, weights=x, minlength=nd)
    s2 = np.bincount(inv, weights=x * x, minlength=nd)
    num = s1 * s1 - s2
    den = np.maximum(cnt - 1.0, 0.0) * s2
    use = cnt >= 2
    if not use.any() or den.sum() <= 0:
        return CorrCI(nan, nan, nan, int(x.size), 0, 0)
    num, den = num[use], den[use]
    r = float(num.sum() / den.sum())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, num.size, size=(n_boot, num.size))
    bd = den[idx].sum(axis=1)
    reps = num[idx].sum(axis=1) / np.where(bd > 0, bd, np.nan)
    a = (1.0 - ci_level) / 2.0
    lo, hi = np.nanquantile(reps, [a, 1.0 - a])
    n_pairs = int((cnt[use] * (cnt[use] - 1) / 2).sum())
    return CorrCI(r, float(lo), float(hi), int(x.size), int(use.sum()), n_pairs)


def prop_normal_scores(
    q_grids: Sequence[Sequence[float]], y: Sequence[float], seed: int = 0
) -> Any:
    """Randomized-PIT normal score of each integer outcome under its own quantile-grid dist."""
    rng = np.random.default_rng(seed)
    out = np.empty(len(y), dtype=float)
    for i, (q, yy) in enumerate(zip(q_grids, y, strict=True)):
        lo, hi = QuantileGridDist.from_grid(q).pit_bounds(float(yy))
        v = lo + rng.random() * (hi - lo)
        out[i] = stats.norm.ppf(min(max(v, 1e-9), 1 - 1e-9))
    return out


def win_residuals(p: NDArray[np.float64], y: NDArray[np.float64], seed: int = 0) -> Any:
    """(standardized residual, probit of randomized Bernoulli PIT) for home-win outcomes."""
    pp = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    yy = np.asarray(y, dtype=float)
    std = (yy - pp) / np.sqrt(pp * (1 - pp))
    u = np.random.default_rng(seed).random(pp.size)
    pit = np.where(yy > 0.5, (1 - pp) + u * pp, u * (1 - pp))
    return std, stats.norm.ppf(np.clip(pit, 1e-9, 1 - 1e-9))


def props_cross_game(props: pl.DataFrame, cfg: IndependenceCfg, seed: int = 0) -> dict[str, CorrCI]:
    """``props``: game_id, game_date, stat, q_grid (19 floats), y. One CorrCI per stat."""
    out: dict[str, CorrCI] = {}
    for st in STATS:
        d = props.filter(pl.col("stat") == st)
        if d.height == 0:
            continue
        z = prop_normal_scores(d["q_grid"].to_list(), d["y"].to_list(), seed)
        g = (
            d.select("game_id", "game_date")
            .with_columns(pl.Series("z", z))
            .group_by("game_id")
            .agg(pl.col("z").mean(), pl.col("game_date").first())
        )
        out[st] = cross_game_corr(
            g["z"].to_numpy(),
            g["game_date"].to_numpy(),
            n_boot=cfg.n_boot,
            ci_level=cfg.ci_level,
            seed=seed,
        )
    return out


def wins_cross_game(wins: pl.DataFrame, cfg: IndependenceCfg, seed: int = 0) -> dict[str, CorrCI]:
    """``wins``: game_id, game_date, p (home win prob), y (1 if home won)."""
    if wins.height == 0:
        return {}
    std, pit = win_residuals(wins["p"].to_numpy(), wins["y"].to_numpy(), seed)
    dts = wins["game_date"].to_numpy()

    def cc(v: NDArray[np.float64]) -> CorrCI:
        return cross_game_corr(v, dts, n_boot=cfg.n_boot, ci_level=cfg.ci_level, seed=seed)

    return {"std_resid": cc(std), "probit_pit": cc(pit)}


# ---------------------------------------------------------------- (c) synthetic parlays


@dataclass(frozen=True)
class ParlayCalibration:
    n_parlays: int
    n_dates: int
    mean_pred: float
    observed: float
    gap: float  # observed - mean_pred
    gap_lo: float
    gap_hi: float
    log_loss: float
    log_loss_lo: float
    log_loss_hi: float
    brier: float
    brier_lo: float
    brier_hi: float
    by_legs: dict[str, dict[str, float]]
    reliability: list[dict[str, Any]]


def _cluster_mean_ci(
    v: NDArray[np.float64], dates: NDArray[np.int64], n_boot: int, level: float, seed: int
) -> tuple[float, float, float]:
    nd = int(dates.max()) + 1
    s = np.bincount(dates, weights=v, minlength=nd)
    c = np.bincount(dates, minlength=nd).astype(float)
    keep = c > 0
    s, c = s[keep], c[keep]
    idx = np.random.default_rng(seed).integers(0, s.size, size=(n_boot, s.size))
    m = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    a = (1 - level) / 2
    lo, hi = np.quantile(m, [a, 1 - a])
    return float(v.mean()), float(lo), float(hi)


def build_cross_game_parlays(
    wins: pl.DataFrame,
    props: pl.DataFrame,
    per_date: int,
    seed: int = 0,
    legs_range: tuple[int, int] = (2, 4),
    p_band: tuple[float, float] = (0.05, 0.95),
) -> pl.DataFrame:
    """Sample cross-game parlays. One leg per distinct game on a date: moneyline (random side)
    or a prop N+ leg (random player-stat, random ladder line with marginal inside ``p_band``).
    Columns: game_date, n_legs, p_indep (product of leg marginals), y (all legs hit), kinds."""
    rng = np.random.default_rng(seed)
    ml: dict[Any, dict[str, tuple[float, float]]] = {}
    for r in wins.iter_rows(named=True):
        ml.setdefault(r["game_date"], {})[r["game_id"]] = (float(r["p"]), float(r["y"]))
    pr: dict[Any, dict[str, list[int]]] = {}
    pdf = props.to_dict(as_series=False)
    for i, (gid, dt) in enumerate(zip(pdf["game_id"], pdf["game_date"], strict=True)):
        pr.setdefault(dt, {}).setdefault(gid, []).append(i)
    rows: list[dict[str, Any]] = []
    for dt in sorted(set(ml) | set(pr)):
        games = sorted(set(ml.get(dt, {})) | set(pr.get(dt, {})))
        if len(games) < legs_range[0]:
            continue
        for _ in range(per_date):
            k = int(rng.integers(legs_range[0], min(legs_range[1], len(games)) + 1))
            pick = rng.choice(len(games), size=k, replace=False)
            p_all, hit, kinds = 1.0, True, []
            for gi in pick:
                g = games[int(gi)]
                has_ml, has_pr = g in ml.get(dt, {}), g in pr.get(dt, {})
                use_ml = has_ml and (not has_pr or rng.random() < 0.4)
                leg: tuple[float, int] | None = None
                if use_ml:
                    ph, yh = ml[dt][g]
                    home = rng.random() < 0.5
                    leg = (ph, int(yh > 0.5)) if home else (1 - ph, int(yh < 0.5))
                    kinds.append("ml")
                else:
                    for _try in range(6):
                        ix = pr[dt][g][int(rng.integers(len(pr[dt][g])))]
                        st = pdf["stat"][ix]
                        t = LADDERS[st][int(rng.integers(len(LADDERS[st])))]
                        p = QuantileGridDist.from_grid(pdf["q_grid"][ix]).p_ge(t)
                        if p_band[0] <= p <= p_band[1]:
                            leg = (p, int(pdf["y"][ix] >= t))
                            kinds.append(st)
                            break
                if leg is None:
                    break
                p_all *= leg[0]
                hit = hit and bool(leg[1])
            else:
                rows.append(
                    {
                        "game_date": dt,
                        "n_legs": k,
                        "p_indep": p_all,
                        "y": int(hit),
                        "kinds": "+".join(sorted(kinds)),
                    }
                )
    schema = {
        "game_date": pl.Date,
        "n_legs": pl.Int64,
        "p_indep": pl.Float64,
        "y": pl.Int64,
        "kinds": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def parlay_calibration(
    df: pl.DataFrame, cfg: IndependenceCfg, seed: int = 0
) -> ParlayCalibration | None:
    if df.height == 0:
        return None
    p = np.clip(df["p_indep"].to_numpy(), _EPS, 1 - _EPS)
    y = df["y"].to_numpy().astype(float)
    dc = _codes(df["game_date"].to_numpy())

    def ci(v: NDArray[np.float64]) -> tuple[float, float, float]:
        return _cluster_mean_ci(v, dc, cfg.n_boot, cfg.ci_level, seed)

    gap = ci(y - p)
    ll = ci(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
    br = ci((p - y) ** 2)
    nl = df["n_legs"].to_numpy()
    by_legs = {
        str(k): {
            "n": float((nl == k).sum()),
            "mean_pred": float(p[nl == k].mean()),
            "observed": float(y[nl == k].mean()),
        }
        for k in sorted(set(nl.tolist()))
    }
    edges = [0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 1.01]
    rel = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & (p < hi)
        if m.any():
            rel.append(
                {
                    "bin": f"[{lo:.2f},{min(hi, 1):.2f})",
                    "n": int(m.sum()),
                    "mean_pred": float(p[m].mean()),
                    "observed": float(y[m].mean()),
                }
            )
    return ParlayCalibration(
        df.height, int(dc.max()) + 1, float(p.mean()), float(y.mean()), gap[0], gap[1], gap[2],
        ll[0], ll[1], ll[2], br[0], br[1], br[2], by_legs, rel,
    )  # fmt: skip


# ---------------------------------------------------------------- orchestration


def run_checks(
    wins: pl.DataFrame,
    props: pl.DataFrame,
    cfg: IndependenceCfg,
    *,
    with_parlays: bool,
    seed: int = 0,
) -> dict[str, Any]:
    """All checks on one (wins, props) pair of frames. JSON-serializable."""
    pc = props_cross_game(props, cfg, seed)
    wc = wins_cross_game(wins, cfg, seed)
    res: dict[str, Any] = {
        "props": {k: asdict(v) | {"flagged": v.flagged(cfg.flag_abs_r)} for k, v in pc.items()},
        "wins": {k: asdict(v) | {"flagged": v.flagged(cfg.flag_abs_r)} for k, v in wc.items()},
        "n_prop_rows": props.height,
        "n_win_games": wins.height,
    }
    res["flagged"] = [
        f"{grp}:{k}"
        for grp in ("props", "wins")
        for k, v in res[grp].items()
        if v["flagged"] and (grp == "props" or k == "std_resid")
    ]
    if with_parlays:
        par = build_cross_game_parlays(wins, props, cfg.parlays_per_date, seed)
        cal = parlay_calibration(par, cfg, seed)
        res["parlays"] = None if cal is None else asdict(cal)
    return res


def _fmt(c: dict[str, Any]) -> str:
    return (
        f"{c['r']:+.4f} [{c['lo']:+.4f}, {c['hi']:+.4f}] "
        f"(games={c['n_games']}, dates={c['n_dates']}, pairs={c['n_pairs']})"
        + (" **FLAG**" if c["flagged"] else "")
    )


def render_md(res: dict[str, Any], title: str = "Cross-game independence check") -> str:
    L = [f"## {title}", ""]
    L.append(
        "Correlation of DIFFERENT games' residuals on the SAME date; 95% CI = date-cluster "
        "bootstrap. Flag = CI excludes 0 and |r| above the configured floor."
    )
    L += ["", "Props (game-mean normal-score residual per stat):"]
    L += [f"- {k}: {_fmt(v)}" for k, v in res["props"].items()] or ["- none"]
    L += ["", "Game results (home-win residual; std_resid is the primary):"]
    L += [f"- {k}: {_fmt(v)}" for k, v in res["wins"].items()] or ["- none"]
    p = res.get("parlays")
    if p:
        L += [
            "",
            f"Synthetic cross-game parlays (n={p['n_parlays']}, dates={p['n_dates']}; legs mixed "
            "moneyline + prop N+):",
            f"- independence-product mean pred {p['mean_pred']:.4f} vs observed "
            f"{p['observed']:.4f}; "
            f"observed - pred {p['gap']:+.5f} [{p['gap_lo']:+.5f}, {p['gap_hi']:+.5f}]",
            f"- log loss {p['log_loss']:.4f} [{p['log_loss_lo']:.4f}, {p['log_loss_hi']:.4f}]; "
            f"Brier {p['brier']:.5f} [{p['brier_lo']:.5f}, {p['brier_hi']:.5f}]",
            "",
            "| legs | n | mean pred | observed |",
            "|---|---|---|---|",
        ]
        L += [
            f"| {k} | {int(v['n'])} | {v['mean_pred']:.4f} | {v['observed']:.4f} |"
            for k, v in p["by_legs"].items()
        ]
        L += ["", "| pred bin | n | mean pred | observed |", "|---|---|---|---|"]
        L += [
            f"| {r['bin']} | {r['n']} | {r['mean_pred']:.4f} | {r['observed']:.4f} |"
            for r in p["reliability"]
        ]
        L.append("(parlay legs share a date, so the bootstrap resamples dates, not parlays.)")
    L += ["", f"Flagged: {res['flagged'] or 'none'}"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- historical loader


def load_historical(
    db_path: str | Path, ctx_oof: str | Path, elo_oof: str | Path, seasons: Sequence[int]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(wins, props) frames for ``seasons``. Props are conditional on the player playing."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, (home_pts > away_pts)::INT AS y FROM games "
            "WHERE home_pts IS NOT NULL AND home_pts > 0"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.pts, s.reb, s.ast, s.fg3m FROM player_game_stats s "
            "JOIN games g USING (game_id) WHERE s.minutes > 0"
        ).pl()
    finally:
        con.close()
    ss = list(seasons)
    wins = (
        pl.read_parquet(elo_oof)
        .filter((pl.col("model") == WIN_MODEL) & pl.col("season").is_in(ss))
        .select("game_id", "p")
        .join(games.select("game_id", "game_date", pl.col("y").cast(pl.Float64)), on="game_id")
    )
    props = (
        pl.read_parquet(ctx_oof)
        .filter(pl.col("season").is_in(ss))
        .select("game_id", "player_id", pl.col("target").alias("stat"), "q_grid")
        .join(
            pgs.unpivot(
                index=["game_id", "player_id"], on=list(STATS), variable_name="stat", value_name="y"
            ),
            on=["game_id", "player_id", "stat"],
        )
        .join(games.select("game_id", "game_date"), on="game_id")
        .with_columns(pl.col("y").cast(pl.Float64))
    )
    return wins, props


def run_historical(
    db_path: str | Path,
    ctx_oof: str | Path,
    elo_oof: str | Path,
    out_dir: str | Path | None,
    cfg: IndependenceCfg,
    *,
    seasons: Sequence[int] = (2023, 2024),
    seed: int = 0,
) -> dict[str, Any]:
    """Per-season and pooled checks on the OOF files. Writes independence_check.{md,json}."""
    wins, props = load_historical(db_path, ctx_oof, elo_oof, seasons)
    out: dict[str, Any] = {"cfg": asdict(cfg), "seasons": {}}
    md = ["# Cross-game independence check (historical OOF)", ""]
    for label, ss in [*[(str(s), [s]) for s in seasons], ("pooled", list(seasons))]:
        if label == "pooled" and len(seasons) < 2:
            continue
        w, p = load_season_slice(wins, props, ss)
        res = run_checks(w, p, cfg, with_parlays=True, seed=seed)
        out["seasons"][label] = res
        md.append(render_md(res, f"Season {label}"))
    if out_dir is not None:
        o = Path(out_dir)
        o.mkdir(parents=True, exist_ok=True)
        (o / "independence_check.json").write_text(
            json.dumps(out, indent=1, sort_keys=True, default=str)
        )
        (o / "independence_check.md").write_text("\n".join(md))
    out["md"] = "\n".join(md)
    return out


def load_season_slice(
    wins: pl.DataFrame, props: pl.DataFrame, seasons: Sequence[int]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Restrict frames to seasons by NBA game-id season code (e.g. 0022300061 -> 2023)."""

    def keep(df: pl.DataFrame) -> pl.DataFrame:
        s = df["game_id"].str.slice(3, 2).cast(pl.Int64) + 2000
        return df.filter(s.is_in(list(seasons)))

    return keep(wins), keep(props)


# ---------------------------------------------------------------- forward loader


def load_forward(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(wins, props) from forward_predictions joined to scored forward_scores (latest per key)."""
    wrows = con.execute(
        "SELECT game_id, game_date, pred, y FROM forward_scores "
        "WHERE target = 'win_prob_home' AND status = 'scored' AND model_name = ?",
        [WIN_MODEL],
    ).fetchall()
    wins = pl.DataFrame(
        {
            "game_id": [r[0] for r in wrows],
            "game_date": [r[1] for r in wrows],
            "p": [float(r[2]) for r in wrows],
            "y": [float(r[3]) for r in wrows],
        },
        schema={"game_id": pl.String, "game_date": pl.Date, "p": pl.Float64, "y": pl.Float64},
    )
    prows = con.execute(
        """
        WITH latest AS (
            SELECT *, row_number() OVER (PARTITION BY game_id, model_name, target, player_id
                                         ORDER BY made_at DESC, run_id DESC) rn
            FROM forward_predictions WHERE model_name = ? AND target IN ('pts','reb','ast','fg3m'))
        SELECT s.game_id, s.game_date, s.target, l.prediction, s.y
        FROM forward_scores s JOIN latest l ON l.rn = 1 AND l.game_id = s.game_id
          AND l.model_name = s.model_name AND l.target = s.target AND l.player_id = s.player_id
        WHERE s.status = 'scored' AND s.model_name = ?
        """,
        [PROPS_MODEL, PROPS_MODEL],
    ).fetchall()
    keep = [(r, json.loads(r[3]).get("q_grid")) for r in prows]
    keep = [(r, q) for r, q in keep if q is not None and len(q) == 19]
    props = pl.DataFrame(
        {
            "game_id": [r[0] for r, _ in keep],
            "game_date": [r[1] for r, _ in keep],
            "stat": [r[2] for r, _ in keep],
            "q_grid": [list(map(float, q)) for _, q in keep],
            "y": [float(r[4]) for r, _ in keep],
        },
        schema={
            "game_id": pl.String,
            "game_date": pl.Date,
            "stat": pl.String,
            "q_grid": pl.List(pl.Float64),
            "y": pl.Float64,
        },
    )
    return wins, props


def forward_check(
    con: duckdb.DuckDBPyConnection, cfg: IndependenceCfg, seed: int = 0
) -> dict[str, Any]:
    """Recompute (a)/(b) on forward data once >= cfg.min_forward_dates dates are scored."""
    wins, props = load_forward(con)
    dts = set(wins["game_date"].to_list()) | set(props["game_date"].to_list())
    if len(dts) < cfg.min_forward_dates:
        return {"status": "insufficient", "n_dates": len(dts), "min": cfg.min_forward_dates}
    res = run_checks(wins, props, cfg, with_parlays=False, seed=seed)
    return {"status": "ok", "n_dates": len(dts), **res}


def forward_section(
    con: duckdb.DuckDBPyConnection, cfg: IndependenceCfg | None = None
) -> list[str]:
    """Markdown lines for the daily report."""
    c = cfg or load_independence_cfg()
    res = forward_check(con, c)
    head = ["", "## Cross-game independence check (forward)"]
    if res["status"] != "ok":
        return head + [
            f"- waiting: {res['n_dates']} scored forward dates < {res['min']} required; "
            "legs from different games remain priced as independent."
        ]
    body = render_md(res, "Forward").splitlines()[2:]
    out = head + body
    if res["flagged"]:
        out.append(
            f"- **WARNING: cross-game dependence detected ({res['flagged']}); the independence "
            "assumption for cross-game parlay legs is not supported by forward data.**"
        )
    return out
