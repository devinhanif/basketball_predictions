# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload"
"""PPLAY_MINUTES_VALID: the pure pieces (docs/prereg/PPLAY_MINUTES_VALID.md, frozen 91d82de7, freeze commit 8e15012).

Everything here takes arrays or small frames and returns numbers, so every guard and gate has a unit test on synthetic data:
walk-forward Platt (P0c), the month-block walk (R19 and the descriptive C19 / C'19, one shared label permutation per block), the paired
game-clustered bootstrap with the rule's p-value, the BH family and the verdict, guards G2-G5, the ECE and slice guards, the era channels and the
2021-22 report drop, the returner definition, the player exclusion and the age assertion.

The frame builders and the orchestration live in ``research.eval.pplay_minutes_valid``; the freeze and review refusals in
``research.eval.pplay_minutes_valid_rule``. The frozen holdout season does not exist anywhere in this harness: ``MAX_SEASON`` is 2024 and every
frame goes through ``assert_holdout``.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from nba.eval.context_residual_eval import bh_adjust, month_blocks
from nba.props.context_residual import ContextResidualConfig, _matrix, _params
from research.eval.minutes_v2_eval import ece as ece_bins
from research.eval.pplay_into_props_live import prev_row_p, shuffle_p_within_day
from research.eval.workload_eval import audit_nulls, minutes_bucket

ID = "PPLAY_MINUTES_VALID"
KEYS = ["game_id", "player_id"]
MAX_SEASON = 2024
VERDICT_SEASONS = (2023, 2024)
DESCRIPTIVE_SEASONS = (
    2020,
    2022,
)  # 2020: era-flagged replication; 2022: R's never-scored warm-up season
SCORED_SEASONS = (2020, 2022, 2023, 2024)
TRAIN_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
HISTORY_SEASONS = (2019, 2020, 2021)
REPORT_DROPPED_SEASON = 2021  # 2021-22
N_BOOT = 2000
SEED = 0
MIN_TRAIN = 1500
LGB_THREADS = 4
EXCLUDED_PLAYER = 1626218

FLOOR_LL = -0.010
FLOOR_BRIER = -0.003
HOLDS_LL_LO = -0.005
BH_Q = 0.05
M2_FLOOR = -0.10
ECE_TOL = 0.005
SLICE_TOL = 0.01
MIN_SLICE_N = 300
G4_MARGIN = -0.003
G5_K = 50
G5_MEAN_TOL = -0.001
NULL_GAP_FLOOR_PP = 2.0
NULL_GAP_MULT = 3.0
PARITY_TOL = 1e-12
REPRO_TOL = 1e-9
REPRO_N = {2023: 33093, 2024: 33127}
PLATT_CLIP = 1e-3
LL_CLIP = 1e-6

ERA_COLS = ("era_bubble", "era_nofans", "era_limited", "era_report_dropped")
ERA_FLAG_SOURCE = {
    "era_bubble": "covid_bubble",
    "era_nofans": "no_fans",
    "era_limited": "limited_fans",
}
# P(play) features derived from the official report: NULL on every 2021-22 row (the "no usable report" encoding, rule 4)
REPORT_FEATURE_COLS = (
    "tag60_code",
    "q60",
    "d60",
    "q60_x_terc",
    "d60_x_terc",
    "on_morning_report",
    "on_evening_report",
)
# minutes-head (M2) features derived from the report: NULL on 2021-22 rows, ``has_report`` = 0 there
M2_REPORT_COLS = ("n_out_rot", "vac_reb", "vac_ast", "vac_fg3m", "n_out_starters", "out_max_min40")
META_COLS = ("tag60_code", "starter10", "is_b2b_second", "returner", "n_played_prior", "terc")

#: Every input that may legitimately carry NULLs, with the cause (G2 lists each; a NULL input with no entry stops the run).
NULL_CAUSES: dict[str, str] = {
    "terc": "team strength tercile needs the prior season's standings; 2018-19 is not held, so 2019-20 rows are NULL (team-level, flagged, never imputed)",
    "tag60_code": "no usable T-60 snapshot for the game (0.1-0.2% of games) and every 2021-22 row (report features dropped, rule 4)",
    "q60": "same as tag60_code",
    "d60": "same as tag60_code",
    "q60_x_terc": "tag60_code NULL, or terc NULL (2019-20)",
    "d60_x_terc": "tag60_code NULL, or terc NULL (2019-20)",
    "on_morning_report": "no morning snapshot for the game, and every 2021-22 row",
    "on_evening_report": "no previous-evening snapshot for the game, and every 2021-22 row",
    "exp_margin": "Elo margin undefined before a team's first rated game",
    "dnp_last5": "no earlier box row for the player (never on Cell-P rows)",
    "days_since_played": "no earlier played game for the player (never on Cell-P rows)",
    "gap_days": "no earlier played game for the player",
    "std_minutes_given_played_prior": "fewer than two played games in the trailing window (never on Cell-P rows)",
    "n_out_rot": "the game has no usable report (has_report = 0) and every 2021-22 row (report features dropped, rule 4)",
    "vac_min": "same as n_out_rot",
    "vac_pts": "same as n_out_rot",
    "vac_reb": "same as n_out_rot",
    "vac_ast": "same as n_out_rot",
    "vac_fg3m": "same as n_out_rot",
    "n_out_starters": "same as n_out_rot",
    "out_max_min40": "same as n_out_rot",
    "ret_mean_min": "no return from a long absence in the player's history (as-of, outcome-independent by construction)",
    "ret_ramp": "same as ret_mean_min",
    "wl_min_last": "no earlier played game for the player",
    "wl_mean_min_l3": "no earlier played game for the player",
    "wl_mean_min_l5": "no earlier played game for the player",
}


#: Inputs whose NULL is a deterministic function of other as-of inputs (a return-from-absence window that has no game yet): the NULL implies the stated as-of
#: cause (checked exactly), and the input is exempt from the outcome-group gap test because the cause variable (``ret_n``) is itself an input and legitimately informative.
AS_OF_CAUSE: dict[str, pl.Expr] = {
    "ret_mean_min": (pl.col("ret_n") >= 99) | (pl.col("ret_n") <= 0),
    "ret_ramp": pl.col("ret_mean_min").is_null()
    | pl.col("ret_mean_min").is_nan()
    | pl.col("ret_n").is_between(1, 30),
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def assert_holdout(df: pl.DataFrame, col: str = "season") -> None:
    """Every frame: the frozen holdout season is never present (and this harness has no path that loads it)."""
    if col in df.columns and df.height and int(df[col].max()) > MAX_SEASON:
        raise ValueError("the frozen holdout season is present in a frame: refusing")


# --------------------------------------------------------------------------- metrics
def ll_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    pc = np.clip(p, LL_CLIP, 1.0 - LL_CLIP)
    return np.asarray(-np.where(y > 0.5, np.log(pc), np.log(1.0 - pc)))


def brier_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.asarray((p - y) ** 2)


def ece_of(p: np.ndarray, y: np.ndarray) -> float:
    return float(ece_bins(p, y))


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    return [
        {
            "bin": b,
            "n": int((idx == b).sum()),
            "p_mean": float(p[idx == b].mean()),
            "y_mean": float(y[idx == b].mean()),
        }
        for b in range(bins)
        if (idx == b).any()
    ]


def cluster_boot(
    delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Paired game-clustered percentile bootstrap of the mean of ``delta``: multinomial resample of games (the WORKLOAD scheme), 95% CI,
    two-sided p = 2 x min(share >= 0, share <= 0) with the floor 1 / n_boot (rule, "Unstated details")."""
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    s = np.bincount(inv, weights=delta, minlength=g)
    c = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(g, np.full(g, 1.0 / g), size=n_boot).astype(float)
    means = (w @ s) / np.maximum(w @ c, 1.0)
    p = 2.0 * min(float(np.mean(means >= 0.0)), float(np.mean(means <= 0.0)))
    return {
        "point": float(delta.mean()),
        "lo": float(np.percentile(means, 2.5)),
        "hi": float(np.percentile(means, 97.5)),
        "p": float(min(max(p, 1.0 / n_boot), 1.0)),
        "n": int(len(delta)),
        "n_games": g,
    }


# --------------------------------------------------------------------------- Platt (P0c)
def logit(p: np.ndarray) -> np.ndarray:
    pc = np.clip(p, PLATT_CLIP, 1.0 - PLATT_CLIP)
    return np.asarray(np.log(pc / (1.0 - pc)))


def fit_platt(p0: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(a, b) of ``sigmoid(a + b * logit(clip(p0)))``: unpenalised logistic regression (C = 1e6, lbfgs) on the single logit feature."""
    yy = np.asarray(y).astype(int)
    if len(np.unique(yy)) < 2:
        raise ValueError("Platt map needs both classes in the fitting rows")
    m = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000).fit(logit(p0)[:, None], yy)
    return float(m.intercept_[0]), float(m.coef_[0, 0])


def apply_platt(p0: np.ndarray, a: float, b: float) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-(a + b * logit(p0)))))


# --------------------------------------------------------------------------- frame rules
def add_era_channels(frame: pl.DataFrame, flags: pl.DataFrame) -> pl.DataFrame:
    """era_bubble, era_nofans, era_limited from ``game_era_flags`` (game level) and era_report_dropped = 1 exactly on 2021-22 rows; ``era_any`` for the Platt exclusion."""
    f = flags.select(
        "game_id",
        *[pl.col(src).cast(pl.Float64).alias(dst) for dst, src in ERA_FLAG_SOURCE.items()],
    )
    out = frame.join(f.unique("game_id"), on="game_id", how="left")
    if out.height != frame.height:
        raise ValueError("era join changed the row count")
    miss = out.select(
        pl.any_horizontal(*[pl.col(c).is_null() for c in ERA_FLAG_SOURCE]).sum()
    ).item()
    if miss:
        raise ValueError(f"{miss} rows without an era flag row (every history game must have one)")
    out = out.with_columns(
        (pl.col("season") == REPORT_DROPPED_SEASON).cast(pl.Float64).alias("era_report_dropped")
    )
    return out.with_columns(
        (pl.col("era_bubble") + pl.col("era_nofans") + pl.col("era_limited") > 0).alias("era_any")
    )


def drop_report_features(
    frame: pl.DataFrame, cols: tuple[str, ...], zero: tuple[str, ...] = ()
) -> pl.DataFrame:
    """Rule 4: the report-derived columns are NULL on every 2021-22 row (``zero`` columns, e.g. ``has_report``, are set to 0)."""
    is21 = pl.col("season") == REPORT_DROPPED_SEASON
    exprs = [
        pl.when(is21).then(None).otherwise(pl.col(c)).alias(c) for c in cols if c in frame.columns
    ]
    exprs += [
        pl.when(is21).then(0).otherwise(pl.col(c)).alias(c) for c in zero if c in frame.columns
    ]
    return frame.with_columns(exprs) if exprs else frame


def exclude_null_age_player(frame: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Drop the NULL-age rows of ``EXCLUDED_PLAYER`` (rule, "Fixed before the freeze") and assert no other NULL ``age`` remains. Returns (frame, n dropped)."""
    bad = pl.col("age").is_null() | pl.col("age").is_nan()
    dropped = frame.filter(bad & (pl.col("player_id") == EXCLUDED_PLAYER)).height
    out = frame.filter(~(bad & (pl.col("player_id") == EXCLUDED_PLAYER)))
    left = out.filter(bad)
    if left.height:
        raise RuntimeError(
            f"{left.height} training rows have a NULL age beyond player {EXCLUDED_PLAYER} (players {sorted(left['player_id'].unique().to_list())[:10]}): the birth-date leak is not closed; NO VERDICT"
        )
    return out, dropped


def returner_flag(box: pl.DataFrame, rows: pl.DataFrame, team_games: pl.DataFrame) -> pl.DataFrame:
    """``returner`` = missed >= 3 of the team's immediately preceding games while on that team (``k - last_played_k - 1 >= 3``), as-of from box and team schedule.

    ``box``: game_id, player_id, team_id, minutes. ``rows``: game_id, player_id, team_id. ``team_games``: game_id, team_id, tgn (team game index)."""
    played = (
        box.filter(pl.col("minutes") > 0)
        .select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            pl.col("team_id").cast(pl.Int64),
        )
        .join(
            team_games.select("game_id", "team_id", "tgn"), on=["game_id", "team_id"], how="inner"
        )
        .select("player_id", "team_id", pl.col("tgn").alias("last_k"))
        .sort("last_k")
    )
    q = (
        rows.select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            pl.col("team_id").cast(pl.Int64),
        )
        .join(team_games.select("game_id", "team_id", "tgn"), on=["game_id", "team_id"], how="left")
        .sort("tgn")
    )
    j = q.join_asof(
        played,
        left_on="tgn",
        right_on="last_k",
        by=["player_id", "team_id"],
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    return j.select(
        "game_id",
        "player_id",
        ((pl.col("tgn") - pl.col("last_k") - 1) >= 3)
        .fill_null(False)
        .cast(pl.Float64)
        .alias("returner"),
    )


# --------------------------------------------------------------------------- the walk
def walk_p(
    frame: pl.DataFrame,
    arms: dict[str, list[str]],
    cfg: ContextResidualConfig,
    seasons: tuple[int, ...],
    *,
    shuffle_seed: int | None = None,
    ckpt: Path | None = None,
    min_train: int = MIN_TRAIN,
    n_estimators: int | None = None,
    p0_col: str = "p0",
    meta: tuple[str, ...] = (),
    permute: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Calendar-month walk over ``seasons``: every block is fitted on EVERY row dated strictly before it (from 2019-20 on).

    LightGBM binary arms (``arms``: name -> columns, RECENT_ROLE's parameters via ``_params``, seed 0, threads fixed) and P0c, the Platt map of
    ``p0_col`` fitted on the same earlier rows with era-flagged games excluded (the map never sees the scored rows).
    With ``shuffle_seed`` the training labels are permuted ONCE per block (drawn from ``default_rng([seed, block])``, one global permutation over all
    training rows) and that same permuted vector feeds every arm AND the Platt map; scoring uses the real labels."""
    assert_holdout(frame)
    test_all = frame.filter(pl.col("season").is_in(list(seasons)))
    blocks = month_blocks(test_all)
    parts: list[dict[str, np.ndarray]] = []
    hashes: dict[str, list[str]] = {a: [] for a in [*arms, "P0c"]}
    platt: list[tuple[int, str, float, float, int]] = []
    for bi, (start, end) in enumerate(blocks):
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files if not k.startswith("_")})
            for a in hashes:
                hashes[a].append(str(z[f"_hash_{a}"]))
            platt.append(
                (bi, str(start), float(z["_platt_a"]), float(z["_platt_b"]), int(z["_platt_n"]))
            )
            _log(f"block {bi + 1}/{len(blocks)} {start} from checkpoint")
            continue
        block = frame.filter(
            (pl.col("game_date") >= start)
            & (pl.col("game_date") < end)
            & pl.col("season").is_in(list(seasons))
        )
        train = frame.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < min_train:
            _log(f"block {bi + 1}/{len(blocks)} {start} skipped (train {train.height})")
            continue
        ytr = train["played"].to_numpy().astype(int)
        y_fit = ytr
        if shuffle_seed is not None and permute:
            y_fit = ytr[np.random.default_rng([shuffle_seed, bi]).permutation(len(ytr))]
        h = hashlib.sha256(np.ascontiguousarray(y_fit).tobytes()).hexdigest()[:16]
        use = ~train["era_any"].to_numpy()
        a, b = fit_platt(train[p0_col].to_numpy()[use], y_fit[use])
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy().astype(str),
            "pid": block["player_id"].to_numpy().astype(np.int64),
            "season": block["season"].to_numpy().astype(np.int64),
            "date": block["game_date"].cast(pl.Int32).to_numpy().astype(np.int64),
            "y": block["played"].to_numpy().astype(float),
            "blk": np.full(block.height, bi),
            "p_P0": block[p0_col].to_numpy().astype(float),
            "p_P0c": apply_platt(block[p0_col].to_numpy(), a, b),
        }
        if (
            "p_platt" in block.columns
        ):  # lineage only (P0p, the old in-sample map): never an arm, never in the verdict
            part["p_P0p"] = block["p_platt"].to_numpy().astype(float)
        for c in meta:
            part[f"m_{c}"] = block[c].cast(pl.Float64).to_numpy()
        params = _params(cfg, "binary")
        params["n_jobs"] = LGB_THREADS
        if n_estimators is not None:
            params["n_estimators"] = n_estimators
        for arm, names in arms.items():
            clf = lgb.LGBMClassifier(**params).fit(_matrix(train, names), y_fit)
            part[f"p_{arm}"] = np.asarray(clf.predict_proba(_matrix(block, names)))[:, 1]
        for arm in hashes:
            hashes[arm].append(h)
        platt.append((bi, str(start), a, b, int(use.sum())))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                path,
                **part,
                **{f"_hash_{k}": np.array(v[-1]) for k, v in hashes.items()},
                _platt_a=np.array(a),
                _platt_b=np.array(b),
                _platt_n=np.array(int(use.sum())),
            )
        parts.append(part)
        _log(
            f"block {bi + 1}/{len(blocks)} {start} n_test={block.height} train={train.height} platt=({a:.3f},{b:.3f})"
        )
    if not parts:
        raise ValueError("no month block had enough training rows")
    res = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return res, {"label_hashes": hashes, "platt": platt, "n_blocks": len(parts)}


# --------------------------------------------------------------------------- contrasts and the verdict
CONTRASTS: tuple[tuple[str, str, str], ...] = (("D1", "R19", "P0c"), ("D2", "R19", "P0"))
METRICS = ("ll", "brier")


def metric_rows(metric: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return ll_rows(p, y) if metric == "ll" else brier_rows(p, y)


def family_table(res: dict[str, np.ndarray], n_boot: int = N_BOOT) -> list[dict[str, Any]]:
    """The 8 verdict tests (2 contrasts x 2 metrics x 2 seasons) with their BH-adjusted p-values (one family, q = 0.05)."""
    rows: list[dict[str, Any]] = []
    for s in VERDICT_SEASONS:
        m = res["season"] == s
        for name, a, b in CONTRASTS:
            for metric in METRICS:
                d = metric_rows(metric, res[f"p_{a}"][m], res["y"][m]) - metric_rows(
                    metric, res[f"p_{b}"][m], res["y"][m]
                )
                ci = cluster_boot(d, res["gid"][m], n_boot)
                rows.append({"season": s, "contrast": name, "metric": metric, **ci})
    adj = bh_adjust(np.array([r["p"] for r in rows]))
    for r, q in zip(rows, adj, strict=True):
        r["p_bh"] = float(q)
    return rows


def decide(rows: list[dict[str, Any]], guards_ok: bool) -> dict[str, Any]:
    """PRODUCTION_BEATEN: both seasons, D1 and D2, both metrics meet the floor with CI upper < 0 and BH p <= 0.05, all guards clean.
    PRODUCTION_HOLDS: D1 log-loss CI lower > -0.005 in both seasons. Anything else MIXED; a failed guard or gate NO VERDICT."""
    idx = {(r["season"], r["contrast"], r["metric"]): r for r in rows}
    floors = {"ll": FLOOR_LL, "brier": FLOOR_BRIER}
    beaten = all(
        (r := idx[(s, c, m)])["point"] <= floors[m] and r["hi"] < 0 and r["p_bh"] <= BH_Q
        for s in VERDICT_SEASONS
        for c in ("D1", "D2")
        for m in METRICS
    )
    holds = all(idx[(s, "D1", "ll")]["lo"] > HOLDS_LL_LO for s in VERDICT_SEASONS)
    if not guards_ok:
        verdict = "NO VERDICT"
    elif beaten:
        verdict = "PRODUCTION_BEATEN"
    elif holds:
        verdict = "PRODUCTION_HOLDS"
    else:
        verdict = "MIXED"
    return {
        "verdict": verdict,
        "beaten": bool(beaten),
        "holds": bool(holds),
        "guards_ok": bool(guards_ok),
    }


def pair_delta(
    res: dict[str, np.ndarray], season: int, a: str, b: str, metric: str = "ll"
) -> tuple[np.ndarray, np.ndarray]:
    m = res["season"] == season
    d = metric_rows(metric, res[f"p_{a}"][m], res["y"][m]) - metric_rows(
        metric, res[f"p_{b}"][m], res["y"][m]
    )
    return d, res["gid"][m]


# --------------------------------------------------------------------------- guards
def g3_dayshuffle(res: dict[str, np.ndarray], n_boot: int = N_BOOT) -> list[dict[str, Any]]:
    """G3 non-inert scorer: R19's p permuted across the players of one calendar date; LL(R19_dayshuf) - LL(P0c) must have CI lower > 0."""
    out = []
    for s in VERDICT_SEASONS:
        m = res["season"] == s
        sh = shuffle_p_within_day(res["p_R19"][m], res["date"][m], SEED)
        d = ll_rows(sh, res["y"][m]) - ll_rows(res["p_P0c"][m], res["y"][m])
        ci = cluster_boot(d, res["gid"][m], n_boot)
        out.append({"season": s, **ci, "ok": bool(ci["lo"] > 0)})
    return out


def g4_pastonly(res: dict[str, np.ndarray], n_boot: int = N_BOOT) -> list[dict[str, Any]]:
    """G4: R19 against the same player's previous Cell-P row's p_R19 (strictly earlier, same season; rows with no earlier row are not scored):
    D <= -0.003 with CI upper < 0."""
    out = []
    for s in VERDICT_SEASONS:
        m = res["season"] == s
        pid, date, p = res["pid"][m], res["date"][m], res["p_R19"][m]
        # composite key: one "player" per player-season so the first row of a season has no previous row
        comp = pid.astype(np.int64) * 100 + (s - 2000)
        prev = prev_row_p(comp, date, p)
        idx = np.lexsort((date, comp))
        cs = comp[idx]
        has_prev = np.zeros(len(p), dtype=bool)
        has_prev[idx] = np.r_[False, cs[1:] == cs[:-1]]
        y = res["y"][m]
        d = (ll_rows(p, y) - ll_rows(prev, y))[has_prev]
        ci = cluster_boot(d, res["gid"][m][has_prev], n_boot)
        out.append(
            {
                "season": s,
                **ci,
                "n_without_previous": int((~has_prev).sum()),
                "ok": bool(ci["point"] <= G4_MARGIN and ci["hi"] < 0),
            }
        )
    return out


def g5_decide(observed: dict[int, float], shuffled: dict[int, np.ndarray]) -> list[dict[str, Any]]:
    """G5: ``shuffled[s]`` holds s_k = LL(R_shuf) - LL(P0c_shuf) for k = 1..K. Holds iff (i) mean_k s_k >= -0.001 and (ii) observed D1 < min_k s_k."""
    out = []
    for s in VERDICT_SEASONS:
        sk = np.asarray(shuffled[s], dtype=float)
        k = len(sk)
        mean_ok = bool(sk.mean() >= G5_MEAN_TOL)
        perm_ok = bool(observed[s] < sk.min())
        out.append(
            {
                "season": s,
                "k": k,
                "mean_sk": float(sk.mean()),
                "min_sk": float(sk.min()),
                "observed_d1_ll": float(observed[s]),
                "permutation_p_min": 1.0 / (k + 1),
                "mean_ok": mean_ok,
                "observed_below_min": perm_ok,
                "ok": mean_ok and perm_ok,
            }
        )
    return out


def slice_masks(res: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Declared slices (guards: questionable/doubtful at T-60, bench, second night, returners; reported only: playoffs, cold start)."""
    n = len(res["y"])
    tag = res["m_tag60_code"]
    return {
        "all": np.ones(n, dtype=bool),
        "questionable_doubtful": np.isin(tag, (3.0, 4.0)),
        "bench": res["m_starter10"] < 0.5,
        "second_night": res["m_is_b2b_second"] == 1,
        "returner": res["m_returner"] == 1,
        "playoffs": np.char.startswith(res["gid"].astype(str), "004"),
        "cold5_9": (res["m_n_played_prior"] >= 5) & (res["m_n_played_prior"] < 10),
    }


GUARD_SLICES = ("questionable_doubtful", "bench", "second_night", "returner")


def ece_and_slices(res: dict[str, np.ndarray], n_boot: int = N_BOOT) -> dict[str, Any]:
    """ECE(R19) - ECE(P0c) <= +0.005 per verdict season; no guard slice with n >= 300 where D1 (log loss) > +0.01. Also reports every slice's D1."""
    out: dict[str, Any] = {"ece": [], "slices": []}
    masks = slice_masks(res)
    for s in VERDICT_SEASONS:
        m = res["season"] == s
        d = ece_of(res["p_R19"][m], res["y"][m]) - ece_of(res["p_P0c"][m], res["y"][m])
        out["ece"].append({"season": s, "delta": float(d), "ok": bool(d <= ECE_TOL)})
        dd = ll_rows(res["p_R19"], res["y"]) - ll_rows(res["p_P0c"], res["y"])
        for name, mk in masks.items():
            sm = mk & m
            rec: dict[str, Any] = {
                "season": s,
                "slice": name,
                "n": int(sm.sum()),
                "guard": name in GUARD_SLICES,
            }
            if sm.sum() >= MIN_SLICE_N:
                rec.update(cluster_boot(dd[sm], res["gid"][sm], n_boot))
                rec["ok"] = not (name in GUARD_SLICES and rec["point"] > SLICE_TOL)
            else:
                rec["ok"] = True
            out["slices"].append(rec)
    out["ok"] = all(e["ok"] for e in out["ece"]) and all(r["ok"] for r in out["slices"])
    return out


# --------------------------------------------------------------------------- G2: missingness
def null_expr(d: pl.DataFrame, c: str) -> pl.Expr:
    if d[c].dtype in (pl.Float32, pl.Float64):
        return pl.col(c).is_null() | pl.col(c).is_nan()
    return pl.col(c).is_null()


def g2_missingness(
    frame: pl.DataFrame, cols: list[str], dnp_bucket: bool = True, minutes_col: str = "min_real"
) -> dict[str, Any]:
    """G2: for every fitted-arm input, the NULL share by outcome group (DNP; played in minute buckets) must be within max(2 pp, 3 x the permuted-outcome
    gap), measured separately per training season (history seasons 2019-21 = history rows, 2022-24 = modern rows); plus the NULL share by row source
    and era for every input, with each NULL cause listed (an input with NULLs and no listed cause stops the run)."""
    cols = [c for c in cols if c in frame.columns]
    exempt = [c for c in cols if c in AS_OF_CAUSE]
    cause_violations = {
        c: int(frame.filter(null_expr(frame, c) & ~AS_OF_CAUSE[c].fill_null(False)).height)
        for c in exempt
    }
    test_cols = [c for c in cols if c not in AS_OF_CAUSE]
    per: list[dict[str, Any]] = []
    for s in TRAIN_SEASONS:
        sub = frame.filter(pl.col("season") == s)
        if sub.is_empty():
            continue
        a = audit_nulls(sub, test_cols, minutes_bucket(minutes_col, dnp=dnp_bucket))
        per.append(
            {
                "season": s,
                "source": "history" if s in HISTORY_SEASONS else "modern",
                "max_gap_pp": a["max_gap_pp"],
                "worst_col": a["worst_col"],
                "placebo_max_gap_pp": a["placebo_max_gap_pp"],
                "threshold_pp": max(NULL_GAP_FLOOR_PP, NULL_GAP_MULT * a["placebo_max_gap_pp"]),
                "n_rows": a["n_rows"],
                "ok": a["max_gap_pp"]
                <= max(NULL_GAP_FLOOR_PP, NULL_GAP_MULT * a["placebo_max_gap_pp"]),
            }
        )
    table: list[dict[str, Any]] = []
    unexplained: list[str] = []
    for c in cols:
        shares = (
            frame.group_by("season").agg(null_expr(frame, c).mean().alias("share")).sort("season")
        )
        any_null = float(shares["share"].max()) > 0
        table.append(
            {
                "col": c,
                "by_season": {
                    int(r["season"]): float(r["share"]) for r in shares.iter_rows(named=True)
                },
                "cause": NULL_CAUSES.get(c),
            }
        )
        if any_null and c not in NULL_CAUSES:
            unexplained.append(c)
    return {
        "per_season": per,
        "null_share_by_input": table,
        "unexplained_null_inputs": unexplained,
        "as_of_cause_violations": cause_violations,
        "ok": all(p["ok"] for p in per) and not unexplained and not any(cause_violations.values()),
    }


# --------------------------------------------------------------------------- planted future
def frames_identical(
    a: pl.DataFrame,
    b: pl.DataFrame,
    cols: list[str],
    mask_expr: pl.Expr,
    strict_before: pl.Expr | None = None,
) -> dict[str, Any]:
    """Byte-identical comparison of ``cols`` on the rows selected by ``mask_expr`` (keyed and sorted by KEYS); NaN equals NaN.

    ``strict_before`` None: the two row sets must be equal. Given (a frame whose membership is label-defined, e.g. the played-rows-only minutes frame): the row sets must
    be equal on ``strict_before`` and the comparison of values runs on the keys present in both."""
    x = a.filter(mask_expr).sort(KEYS)
    y = b.filter(mask_expr).sort(KEYS)
    if strict_before is not None:
        xs, ys = x.filter(strict_before).select(KEYS), y.filter(strict_before).select(KEYS)
        if xs.height != ys.height or not xs.equals(ys):
            return {
                "n": x.height,
                "n_other": y.height,
                "same_rows": False,
                "differing": ["<row set before the cutoff day>"],
                "identical": False,
            }
        k = x.select(KEYS).join(y.select(KEYS), on=KEYS, how="inner")
        x, y = (
            x.join(k, on=KEYS, how="inner").sort(KEYS),
            y.join(k, on=KEYS, how="inner").sort(KEYS),
        )
    elif x.height != y.height or not x.select(KEYS).equals(y.select(KEYS)):
        return {
            "n": x.height,
            "n_other": y.height,
            "same_rows": False,
            "differing": ["<row set>"],
            "identical": False,
        }
    bad: list[str] = []
    for c in cols:
        if c not in x.columns or c not in y.columns:
            continue
        u, v = x[c], y[c]
        if u.dtype in (pl.Float32, pl.Float64):
            same = np.array_equal(
                u.to_numpy().astype(float), v.to_numpy().astype(float), equal_nan=True
            )
        else:
            same = bool(u.equals(v, null_equal=True))
        if not same:
            bad.append(c)
    return {"n": x.height, "same_rows": True, "differing": bad, "identical": not bad}
