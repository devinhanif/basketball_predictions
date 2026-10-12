# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""Parts B (period markets vs our period forecasts) and C (gates) of the pace/period study. Descriptive, 2023-24 only.

Market: the-odds-api t60 snapshots in ``data/odds/odds_history.duckdb`` (read-only, season <= 2024).
De-vig: each book's two prices -> proportional (multiplicative) de-vig; Pinnacle if it quotes the game/market, else the mean over books.
Period scores: play-by-play cumulative scoreboard (``confusion_report.build_periods`` cache); games whose pbp final disagrees with the box are dropped.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import confusion_report as CR
from research.eval.odds_history_eval import model_p_over

ROOT = Path(__file__).resolve().parents[2]
ODDS = ROOT / "data/odds/odds_history.duckdb"
DB = ROOT / "nba.duckdb"
HP = ROOT / "data/ops/half_points_cache"
N_BOOT = 2000
SEED = 20261012
PER = ["q1", "q2", "q3", "q4", "h1", "h2"]
SEASONS = (2023, 2024)


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def boot_stat(
    gids: np.ndarray, num: dict[str, np.ndarray], fn: Any, seed: int = SEED
) -> list[float]:
    """Game-clustered bootstrap of fn(sums) where each array in ``num`` is summed per game; returns the 95% percentile CI."""
    u, inv = np.unique(gids, return_inverse=True)
    s = {k: np.bincount(inv, weights=v.astype(float), minlength=len(u)) for k, v in num.items()}
    rng = np.random.default_rng(seed)
    out = np.empty(N_BOOT)
    for b in range(N_BOOT):
        pk = rng.integers(0, len(u), len(u))
        out[b] = fn({k: v[pk].sum() for k, v in s.items()})
    return [float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))]


def mean_ci(v: np.ndarray, g: np.ndarray) -> dict[str, Any]:
    ones = np.ones(len(v))
    return {
        "mean": float(v.mean()),
        "ci": boot_stat(g, {"s": v, "n": ones}, lambda d: d["s"] / d["n"]),
        "n": int(len(v)),
        "games": int(len(np.unique(g))),
    }


# ---------------------------------------------------------------------------------------------- market loading
def amer(px: pl.Expr) -> pl.Expr:
    return pl.when(px < 0).then(-px / (-px + 100.0)).otherwise(100.0 / (px + 100.0))


def two_way_probs(market: str, a_side: str, b_side: str) -> pl.DataFrame:
    """Per game: Pinnacle de-vigged P(a_side), consensus (mean over books) P(a_side), n books, for a two-sided market (no line)."""
    o = duckdb.connect(str(ODDS), read_only=True)
    d = o.execute(
        "select game_id, season, book, side, price_american from odds_history where market=? and snapshot_kind='t60' and season<=2024 and side in (?,?)",
        [market, a_side, b_side],
    ).pl()
    o.close()
    a = (
        d.filter(pl.col("side") == a_side)
        .group_by("game_id", "season", "book")
        .agg(pl.col("price_american").first().alias("pa"))
    )
    b = (
        d.filter(pl.col("side") == b_side)
        .group_by("game_id", "book")
        .agg(pl.col("price_american").first().alias("pb"))
    )
    j = a.join(b, on=["game_id", "book"]).with_columns(
        (amer(pl.col("pa")) / (amer(pl.col("pa")) + amer(pl.col("pb")))).alias("p")
    )
    pin = j.filter(pl.col("book") == "pinnacle").select("game_id", pl.col("p").alias("p_pin"))
    con = j.group_by("game_id", "season").agg(
        pl.col("p").mean().alias("p_cons"), pl.len().alias("n_books")
    )
    return con.join(pin, on="game_id", how="left").with_columns(
        pl.coalesce("p_pin", "p_cons").alias("p_mkt"), pl.col("p_pin").is_null().alias("fallback")
    )


def totals_market(market: str) -> pl.DataFrame:
    """Per game: Pinnacle total line (else median line over books), its de-vigged P(over) at that line, n books."""
    o = duckdb.connect(str(ODDS), read_only=True)
    d = o.execute(
        "select game_id, season, book, side, point, price_american from odds_history where market=? and snapshot_kind='t60' and season<=2024 and side in ('over','under')",
        [market],
    ).pl()
    o.close()
    ov = d.filter(pl.col("side") == "over").select(
        "game_id", "season", "book", "point", pl.col("price_american").alias("po")
    )
    un = d.filter(pl.col("side") == "under").select(
        "game_id", "book", "point", pl.col("price_american").alias("pu")
    )
    j = ov.join(un, on=["game_id", "book", "point"]).with_columns(
        (amer(pl.col("po")) / (amer(pl.col("po")) + amer(pl.col("pu")))).alias("p_over")
    )
    # one line per (game, book): the one whose de-vigged price is closest to even (the main line, not an alternate)
    j = (
        j.with_columns((pl.col("p_over") - 0.5).abs().alias("dist"))
        .sort("dist")
        .group_by("game_id", "book", maintain_order=True)
        .first()
    )
    pin = j.filter(pl.col("book") == "pinnacle").select(
        "game_id", pl.col("point").alias("line_pin"), pl.col("p_over").alias("pover_pin")
    )
    con = j.group_by("game_id", "season").agg(
        pl.col("point").median().alias("line_cons"), pl.len().alias("n_books")
    )
    # consensus P(over) at the consensus line: mean over books that quote exactly that line
    jj = j.join(con.select("game_id", "line_cons"), on="game_id").filter(
        pl.col("point") == pl.col("line_cons")
    )
    pc = jj.group_by("game_id").agg(pl.col("p_over").mean().alias("pover_cons"))
    out = con.join(pin, on="game_id", how="left").join(pc, on="game_id", how="left")
    return out.with_columns(
        pl.coalesce("line_pin", "line_cons").alias("line"),
        pl.when(pl.col("line_pin").is_not_null())
        .then(pl.col("pover_pin"))
        .otherwise(pl.col("pover_cons"))
        .alias("p_over_mkt"),
        pl.col("line_pin").is_null().alias("fallback"),
    )


# ---------------------------------------------------------------------------------------------- game frame
def game_frame() -> pl.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    g = con.execute("select * from games where season <= 2024").pl()
    con.close()
    pb = pl.read_parquet(CR.PERIODS_CACHE)
    g = g.join(pb, on="game_id", how="left").with_columns(
        (pl.col("home_pts") - pl.col("away_pts")).alias("margin")
    )
    oof = pl.read_parquet(ROOT / "data/injury_elo_realtip/oof_predictions.parquet")
    for mdl, col in (("rung0_injury_elo", "p_inj"), ("rung0_mov_elo", "p_mov")):
        g = g.join(
            oof.filter(pl.col("model") == mdl).select("game_id", pl.col("p").alias(col)),
            on="game_id",
            how="left",
        )
    ok = (
        pl.col("h_fin").is_not_null()
        & (pl.col("h_fin") == pl.col("home_pts"))
        & (pl.col("a_fin") == pl.col("away_pts"))
    )
    g = g.with_columns(ok.alias("pbp_ok"))
    for p in range(1, 5):
        hp = pl.col(f"h_c{p}") - (pl.col(f"h_c{p - 1}") if p > 1 else 0)
        ap = pl.col(f"a_c{p}") - (pl.col(f"a_c{p - 1}") if p > 1 else 0)
        g = g.with_columns(hp.alias(f"hq{p}"), ap.alias(f"aq{p}"))
    g = g.with_columns(
        *[(pl.col(f"hq{p}") - pl.col(f"aq{p}")).alias(f"m_q{p}") for p in range(1, 5)],
        *[(pl.col(f"hq{p}") + pl.col(f"aq{p}")).alias(f"t_q{p}") for p in range(1, 5)],
    ).with_columns(
        (pl.col("m_q1") + pl.col("m_q2")).alias("m_h1"),
        (pl.col("m_q3") + pl.col("m_q4")).alias("m_h2"),
        (pl.col("t_q1") + pl.col("t_q2")).alias("t_h1"),
        (pl.col("t_q3") + pl.col("t_q4")).alias("t_h2"),
    )
    g = g.join(CR.team_totals_baseline(g), on="game_id", how="left")
    return g


# ---------------------------------------------------------------------------------------------- B1 winners
def winners(g: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    d = g.filter(pl.col("pbp_ok") & pl.col("p_inj").is_not_null())
    for t in PER:
        mk = two_way_probs(f"h2h_{t}", "home", "away")
        res_parts = []
        for sea in SEASONS:
            fit = d.filter(pl.col("season") < sea)
            te = d.filter(pl.col("season") == sea)
            xf, xt = CR.logit(fit["p_inj"].to_numpy()), CR.logit(te["p_inj"].to_numpy())
            a, b, sig = CR.ols_fit(xf, fit[f"m_{t}"].to_numpy().astype(float))
            mu = a + b * xt
            ph = 1 - CR.phi((0.5 - mu) / sig)
            pa = CR.phi((-0.5 - mu) / sig)
            res_parts.append(
                pl.DataFrame(
                    {
                        "game_id": te["game_id"],
                        "season": te["season"],
                        "m": te[f"m_{t}"],
                        "p_model3h": ph,
                        "p_model3a": pa,
                        "p_full": te["p_inj"],
                    }
                )
            )
        r = pl.concat(res_parts).join(
            mk.select("game_id", "p_mkt", "p_pin", "p_cons", "n_books", "fallback"),
            on="game_id",
            how="left",
        )
        n_games = r.height
        n_tie = int((r["m"] == 0).sum())
        r = r.with_columns(
            (pl.col("p_model3h") / (pl.col("p_model3h") + pl.col("p_model3a"))).alias("p_mod")
        )
        s = r.filter((pl.col("m") != 0) & pl.col("p_mkt").is_not_null())
        y = (s["m"].to_numpy() > 0).astype(float)
        pm, pk = s["p_mod"].to_numpy(), s["p_mkt"].to_numpy()
        gid = s["game_id"].to_numpy()
        eps = 1e-6

        def ll(p: np.ndarray, y: np.ndarray = y, eps: float = eps) -> np.ndarray:
            p = np.clip(p, eps, 1 - eps)
            return -(y * np.log(p) + (1 - y) * np.log(1 - p))

        am, ak = ((pm > 0.5) == (y == 1)).astype(float), ((pk > 0.5) == (y == 1)).astype(float)
        lm, lk = ll(pm), ll(pk)
        bm, bk = (pm - y) ** 2, (pk - y) ** 2
        # market calibration
        bins = [0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0001]
        cal = []
        for lo, hi in zip(bins[:-1], bins[1:], strict=True):
            m = (pk >= lo) & (pk < hi)
            if m.sum() >= 1:
                cal.append(
                    {
                        "bin": f"{lo:.1f}-{min(hi, 1):.1f}",
                        "n": int(m.sum()),
                        "mean_p_mkt": float(pk[m].mean()),
                        "home_win_rate": mean_ci(y[m], gid[m])["mean"],
                        "ci": mean_ci(y[m], gid[m])["ci"],
                    }
                )

        # calibration slope: logistic regression of y on logit(p) by Newton
        def cal_slope(p: np.ndarray, y: np.ndarray = y) -> list[float]:
            x = np.column_stack([np.ones(len(p)), CR.logit(p)])
            w = np.zeros(2)
            for _ in range(25):
                z = x @ w
                q = 1 / (1 + np.exp(-z))
                h = (x * (q * (1 - q))[:, None]).T @ x
                w = w + np.linalg.solve(h, x.T @ (y - q))
            return [float(w[0]), float(w[1])]

        out[t] = {
            "games_in_period_frame": n_games,
            "ties_voided": n_tie,
            "n_scored": int(len(y)),
            "n_pinnacle": int((~s["fallback"].to_numpy()).sum()),
            "n_consensus_fallback": int(s["fallback"].sum()),
            "median_books": float(np.median(s["n_books"].to_numpy())),
            "acc_model": mean_ci(am, gid),
            "acc_market": mean_ci(ak, gid),
            "acc_model_minus_market": {
                "mean": float((am - ak).mean()),
                "ci": mean_ci(am - ak, gid)["ci"],
            },
            "logloss_model": mean_ci(lm, gid),
            "logloss_market": mean_ci(lk, gid),
            "logloss_model_minus_market": {
                "mean": float((lm - lk).mean()),
                "ci": mean_ci(lm - lk, gid)["ci"],
            },
            "brier_model": float(bm.mean()),
            "brier_market": float(bk.mean()),
            "home_win_rate_decisive": mean_ci(y, gid),
            "market_calibration_bins": cal,
            "market_calib_intercept_slope": cal_slope(pk),
            "model_calib_intercept_slope": cal_slope(pm),
            "model_p_mean": float(pm.mean()),
            "market_p_mean": float(pk.mean()),
            "pinnacle_only": _pin_only(s, y, gid),
        }
        _log(
            f"winners {t}: n={len(y)} acc model {am.mean():.3f} mkt {ak.mean():.3f} ll {lm.mean():.4f} vs {lk.mean():.4f}"
        )
    # full game for reference
    mk = two_way_probs("h2h", "home", "away")
    s = d.filter(pl.col("season").is_in(list(SEASONS))).join(
        mk.select("game_id", "p_mkt"), on="game_id", how="inner"
    )
    y = (s["margin"].to_numpy() > 0).astype(float)
    pm, pk = s["p_inj"].to_numpy(), s["p_mkt"].to_numpy()
    gid = s["game_id"].to_numpy()
    lm = -(y * np.log(pm) + (1 - y) * np.log(1 - pm))
    lk = -(y * np.log(pk) + (1 - y) * np.log(1 - pk))
    out["full_game"] = {
        "n": int(len(y)),
        "acc_model": mean_ci(((pm > 0.5) == (y == 1)).astype(float), gid),
        "acc_market": mean_ci(((pk > 0.5) == (y == 1)).astype(float), gid),
        "logloss_model": mean_ci(lm, gid),
        "logloss_market": mean_ci(lk, gid),
        "logloss_diff": {"mean": float((lm - lk).mean()), "ci": mean_ci(lm - lk, gid)["ci"]},
    }
    return out


def _pin_only(s: pl.DataFrame, y: np.ndarray, gid: np.ndarray) -> dict[str, Any]:
    m = ~s["fallback"].to_numpy()
    pm, pk = s["p_mod"].to_numpy()[m], s["p_mkt"].to_numpy()[m]
    yy, gg = y[m], gid[m]
    lm = -(yy * np.log(np.clip(pm, 1e-6, 1)) + (1 - yy) * np.log(np.clip(1 - pm, 1e-6, 1)))
    lk = -(yy * np.log(np.clip(pk, 1e-6, 1)) + (1 - yy) * np.log(np.clip(1 - pk, 1e-6, 1)))
    return {
        "n": int(m.sum()),
        "logloss_model_minus_market": {
            "mean": float((lm - lk).mean()),
            "ci": mean_ci(lm - lk, gg)["ci"],
        },
        "acc_model": float(((pm > 0.5) == (yy == 1)).mean()),
        "acc_market": float(((pk > 0.5) == (yy == 1)).mean()),
    }


# ---------------------------------------------------------------------------------------------- B2 totals
def totals(g: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    d = g.filter(pl.col("pbp_ok") & pl.col("total_hat").is_not_null())
    d = d.with_columns(pl.col("home_pts").add(pl.col("away_pts")).alias("t_full"))
    for t in [*PER, "full"]:
        mk = totals_market("totals" if t == "full" else f"totals_{t}")
        col = "t_full" if t == "full" else f"t_{t}"
        parts = []
        for sea in SEASONS:
            fit = d.filter(pl.col("season") < sea)
            te = d.filter(pl.col("season") == sea)
            sh = 1.0 if t == "full" else float(np.mean((fit[col] / fit["total_hat"]).to_numpy()))
            parts.append(
                pl.DataFrame(
                    {
                        "game_id": te["game_id"],
                        "season": te["season"],
                        "y": te[col].cast(pl.Float64),
                        "ours": te["total_hat"].to_numpy() * sh,
                        "share": sh,
                    }
                )
            )
        r = pl.concat(parts).join(
            mk.select("game_id", "line", "p_over_mkt", "fallback", "n_books"),
            on="game_id",
            how="inner",
        )
        y, ours, line = r["y"].to_numpy(), r["ours"].to_numpy(), r["line"].to_numpy()
        gid = r["game_id"].to_numpy()
        ae_o, ae_m = np.abs(y - ours), np.abs(y - line)
        npush = int((y == line).sum())
        v = y != line
        over = (y > line).astype(float)
        ours_over = ours > line
        pk = r["p_over_mkt"].to_numpy()
        lean = v & (np.abs(pk - 0.5) > 1e-9) & ~np.isnan(pk)
        out[t] = {
            "n": int(len(y)),
            "pushes": npush,
            "n_pinnacle": int((~r["fallback"].to_numpy()).sum()),
            "mean_actual": float(y.mean()),
            "mean_line": float(line.mean()),
            "mean_ours": float(ours.mean()),
            "share_used": [float(x) for x in r["share"].unique().to_list()],
            "mae_ours": mean_ci(ae_o, gid),
            "mae_market": mean_ci(ae_m, gid),
            "mae_ours_minus_market": {
                "mean": float((ae_o - ae_m).mean()),
                "ci": mean_ci(ae_o - ae_m, gid)["ci"],
            },
            "bias_line_minus_actual": mean_ci(line - y, gid),
            "bias_ours_minus_actual": mean_ci(ours - y, gid),
            "over_rate_actual": mean_ci(over[v], gid[v]),
            "ou_acc_ours": mean_ci((ours_over[v] == (over[v] == 1)).astype(float), gid[v]),
            "ou_acc_market_lean": mean_ci(
                ((pk[lean] > 0.5) == (over[lean] == 1)).astype(float), gid[lean]
            )
            if lean.sum() > 30
            else None,
            "n_lean": int(lean.sum()),
            "ou_acc_always_under": float(1 - over[v].mean()),
            "sd_actual_minus_line": float((y - line).std()),
            "sd_actual_minus_ours": float((y - ours).std()),
        }
        _log(f"totals {t}: n={len(y)} MAE ours {ae_o.mean():.3f} mkt {ae_m.mean():.3f}")
    return out


# ---------------------------------------------------------------------------------------------- B3 Q1 player points
def q1_player() -> dict[str, Any]:
    z = np.load(HP / "forecasts.npz")
    keep = z["season"] == 2024
    gid, pid = z["gid"].astype(str)[keep], z["pid"].astype(np.int64)[keep]
    q, y = z["q_b3"][keep].astype(float), z["q1"][keep].astype(float)
    fc = pl.DataFrame({"game_id": gid, "player_id": pid, "i": np.arange(len(gid))})
    o = duckdb.connect(str(ODDS), read_only=True)
    d = o.execute(
        "select game_id, player_id, book, side, point, price_american from odds_history where market='player_points_q1' and snapshot_kind='t60' and season=2024 and player_id is not null"
    ).pl()
    o.close()
    ov = d.filter(pl.col("side") == "over").select(
        "game_id", "player_id", "book", "point", pl.col("price_american").alias("po")
    )
    un = d.filter(pl.col("side") == "under").select(
        "game_id", "player_id", "book", "point", pl.col("price_american").alias("pu")
    )
    j = (
        ov.join(un, on=["game_id", "player_id", "book", "point"])
        .with_columns(
            (amer(pl.col("po")) / (amer(pl.col("po")) + amer(pl.col("pu")))).alias("p_mkt")
        )
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )
    j = j.join(fc, on=["game_id", "player_id"], how="inner")
    n_quotes = j.height
    idx = j["i"].to_numpy()
    line = j["point"].to_numpy()
    pge = np.stack([(q[idx] >= k).mean(axis=1) for k in range(1, 41)], axis=1)  # P(Y>=k)
    p_over, over_ev, status = model_p_over(pge, line, y[idx])
    sc = status == 0
    pk = j["p_mkt"].to_numpy()
    yy = over_ev.astype(float)
    gids = j["game_id"].to_numpy()
    lean = sc & (np.abs(pk - 0.5) > 1e-9)
    med = np.median(q[idx], axis=1)
    res: dict[str, Any] = {
        "quotes_with_forecast": int(n_quotes),
        "scored": int(sc.sum()),
        "books": {b: int((j["book"] == b).sum()) for b in j["book"].unique().to_list()},
        "games": int(len(np.unique(gids))),
        "players": int(len(np.unique(j["player_id"].to_numpy()))),
        "mean_line": float(line.mean()),
        "mean_actual_q1": float(y[idx].mean()),
        "mean_model_median": float(med.mean()),
        "mae_model_median": mean_ci(np.abs(y[idx] - med)[sc], gids[sc]),
        "mae_line": mean_ci(np.abs(y[idx] - line)[sc], gids[sc]),
        "over_rate": mean_ci(yy[sc], gids[sc]),
        "acc_model": mean_ci(((p_over[sc] > 0.5) == (yy[sc] == 1)).astype(float), gids[sc]),
        "acc_market_lean": mean_ci(((pk[lean] > 0.5) == (yy[lean] == 1)).astype(float), gids[lean])
        if lean.sum() > 30
        else None,
        "n_lean": int(lean.sum()),
        "acc_always_under": float(1 - yy[sc].mean()),
        "model_p_over_mean": float(p_over[sc].mean()),
        "market_p_over_mean": float(pk[sc].mean()),
    }

    def ll(p: np.ndarray) -> np.ndarray:
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return -(yy[sc] * np.log(p[sc]) + (1 - yy[sc]) * np.log(1 - p[sc]))

    lm, lk = ll(p_over), ll(pk)
    res["logloss_model"] = mean_ci(lm, gids[sc])
    res["logloss_market"] = mean_ci(lk, gids[sc])
    res["logloss_model_minus_market"] = {
        "mean": float((lm - lk).mean()),
        "ci": mean_ci(lm - lk, gids[sc])["ci"],
    }
    return res


def run_periods() -> dict[str, Any]:
    g = game_frame()
    return {"winners": winners(g), "totals": totals(g), "q1_player_points_2024": q1_player()}


# ---------------------------------------------------------------------------------------------- C gates
def run_gates() -> dict[str, Any]:
    from research.eval import pace_periods as A

    tg = A.team_games()
    con = duckdb.connect(str(DB), read_only=True)
    g = con.execute("select game_id, season from games where season<=2024").pl()
    nposs = con.execute(
        "select p.game_id, count(*) n from possessions p join games g using(game_id) where g.season<=2024 group by 1"
    ).pl()
    con.close()
    out: dict[str, Any] = {"seasons": {}}
    gg = g.join(nposs, on="game_id", how="left")
    gm = (
        tg.filter(pl.col("team_id") == pl.col("team_id"))
        .group_by("game_id")
        .agg(
            pl.col("r20_pace").is_not_null().all().alias("both_pace"),
            (pl.col("r20_pf").is_not_null() & pl.col("r20_pa").is_not_null())
            .all()
            .alias("both_eff"),
        )
    )
    gg = gg.join(gm, on="game_id", how="left")
    mk_full = totals_market("totals")
    gf = game_frame()
    pbp = gf.select("game_id", "pbp_ok")
    gg = gg.join(pbp, on="game_id", how="left").join(
        mk_full.select("game_id", "line", "n_books", "fallback"), on="game_id", how="left"
    )
    per_mk = {
        t: totals_market(f"totals_{t}")
        .select("game_id")
        .with_columns(pl.lit(True).alias(f"has_{t}"))
        for t in PER
    }
    for m in per_mk.values():
        gg = gg.join(m, on="game_id", how="left")
    for sea in SEASONS:
        s = gg.filter(pl.col("season") == sea)
        out["seasons"][str(sea)] = {
            "games": s.height,
            "games_with_possessions": int(s["n"].is_not_null().sum()),
            "median_possessions": float(s["n"].median()),
            "games_both_teams_asof_pace_r20": int(s["both_pace"].fill_null(False).sum()),
            "games_both_teams_asof_efficiency_r20": int(s["both_eff"].fill_null(False).sum()),
            "games_with_market_full_total": int(s["line"].is_not_null().sum()),
            "games_with_pinnacle_full_total": int(
                (s["line"].is_not_null() & ~s["fallback"].fill_null(True)).sum()
            ),
            "median_books_full_total": float(s["n_books"].median()),
            "games_pbp_ok": int(s["pbp_ok"].fill_null(False).sum()),
            **{f"games_with_total_{t}": int(s[f"has_{t}"].fill_null(False).sum()) for t in PER},
        }
    # first-5-games drop: games where as-of pace unavailable by season
    return out
