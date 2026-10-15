# ruff: noqa: E501
"""The pretip COMBO_STOCKS step: blk / stl NB heads, one six-stat copula joint, RECENT and INDEP comparators, written as a shadow.

Rule: docs/prereg/COMBO_STOCKS_SHADOW.md (FROZEN b9b38cbe), "What the steward builds" 1-2. Runs per slate group at the T-60 tick, after the AVG
shadow, in its own try/except (``pipeline._combo_shadow``). Reads production's rows already written in this pass and the box history dated
before the slate date; writes only ``forward_combo_*``. It never writes ``forward_predictions`` or any scored production table, is never
retried after tip (only games with ``made_at < tipoff`` are passed), and a failure is a ``failed`` heartbeat, never a changed primary row.

Steps (rule 2): (a) the production feature frame with the slate placeholders (as ``nba.daily.full_shadow.run.build_full_frame``) plus the stock
columns, with blk and stl read by a SEPARATE read-only query because ``_history_frames`` does not select them (review B5a; ``_history_frames``
is not edited); (b) ``NBCountModel`` for blk and stl on played rows dated before the slate date (same-date cache); (c) the latest production rows
for pts (lower-tail mixture), reb, ast, fg3m rebuilt to 199-point grids from the stored ``p_ge_full``; (d) ``joint_grids`` with the frozen R and
base normals (one call: every combo from the same draws) and with R = I (INDEP); (e) RECENT from the player's last 40 played games; (f) rows,
refusing any ``made_at >= tipoff``.

Serving rule: regular-season games only (``game_id`` prefix 002) and rows with ``n_prior >= 5``. A player without an on-time context row for each of
pts (lt), reb, ast, fg3m is not servable: counted, never imputed. DIRECT and RECENT_CAL (descriptive arms, droppable per the rule) are not built.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pickle
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl

from nba.daily.combo_shadow import store
from nba.daily.combo_shadow.config import STOCK_TARGETS, TARGETS, ComboConfig, load_config
from nba.daily.pplay_shadow.features import SlateGame
from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME
from nba.props import combo_copula as C
from nba.props import full_support
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    _params,
    build_features,
    flagged_from_availability,
    stat_frame,
    to_integer_support,
)
from nba.props.count_head import NBCountModel
from nba.props.forward import _history_frames, _with_slate_placeholders
from nba.props.stock_features import add_stock_features, stock_feature_names

REGULAR = "002"
MIN_PRIOR = 5
HB_DIR = Path("data/ops/heartbeat")
JOB = "combo_shadow"
NB_BINS = 4
#: wall-time budget (review S1): the step runs inside the pretip database hold, so it must give way to the T-30 tick
BUDGET_S = 420.0
MIN_LEAD_MIN = 35.0
JOINT_BATCH = (
    96  # rows per cooperative check in the sampler; a row's grid does not depend on its batch
)
SORT = ["game_date", "game_id", "player_id"]
#: (stat, production model_name) of the four marginals read from ``forward_predictions`` (config ``marginals``)
PROD_SOURCES: dict[str, str] = {
    "pts": CONTEXT_LT_PROPS_MODEL_NAME,
    "reb": CONTEXT_PROPS_MODEL_NAME,
    "ast": CONTEXT_PROPS_MODEL_NAME,
    "fg3m": CONTEXT_PROPS_MODEL_NAME,
}
PROD_N = (
    199  # context rows store p_ge_full with n = 199 (exact); any other n is a recency-fallback row
)


# --------------------------------------------------------------------------- identity of the model
def model_id_of(cfg: ComboConfig, ccfg: ContextResidualConfig | None = None) -> str:
    """sha256 of (NB head recipe + learner params + lightgbm version, config sha, base-normal sha); the ``model_id`` column."""
    ccfg = ccfg or ContextResidualConfig()
    body = {
        "version": "combo_nb_v1",
        "heads": {s: stock_feature_names(s) for s in STOCK_TARGETS},
        "params": _params(ccfg, "poisson"),
        "bins": NB_BINS,
        "lightgbm": lgb.__version__,
        "config_sha": cfg.config_sha,
        "base_sha": cfg.base_id,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class BudgetExceeded(RuntimeError):
    """The step passed its wall-time budget between two units of work; nothing has been written yet."""


class Budget:
    """Cooperative deadline: ``check()`` between units of work raises ``BudgetExceeded`` once ``limit_s`` has passed (no threads, no kills)."""

    def __init__(
        self, limit_s: float = BUDGET_S, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.limit_s, self.clock, self.t0 = limit_s, clock, clock()

    def check(self) -> None:
        if self.clock() - self.t0 > self.limit_s:
            raise BudgetExceeded(f"budget: step passed {self.limit_s:.0f} s")


# --------------------------------------------------------------------------- heartbeat file
def _heartbeat_file(hb_dir: Path, rec: dict[str, Any], count: str = "keep") -> None:
    """``data/ops/heartbeat/combo_shadow.json`` for the independent watchdog: written at START and END of every run. ``consecutive_bad`` is the
    running count of failed runs (``count="inc"``); a good run resets it (``"reset"``); a run with nothing to serve leaves it alone (``"keep"``)."""
    hb_dir.mkdir(parents=True, exist_ok=True)
    path = hb_dir / f"{JOB}.json"
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    bad = int(prev.get("consecutive_bad", 0))
    bad = bad + 1 if count == "inc" else 0 if count == "reset" else bad
    out = {**prev, **rec, "consecutive_bad": bad, "job": JOB}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, default=str))
    tmp.replace(path)


# --------------------------------------------------------------------------- (a) the feature frame
def build_combo_frame(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    wide: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    ccfg: ContextResidualConfig | None = None,
) -> pl.DataFrame:
    """Production's feature frame (history before ``run_date`` + tonight's NULL-minutes placeholders) with the stock columns added and the blk /
    stl labels attached. Every read is dated before ``run_date``. blk and stl come from their own read-only query (``_history_frames`` does
    not select them); tonight's placeholders carry 0 there, as they do for the four production stats."""
    cfg = ccfg or ContextResidualConfig()
    h_games, h_pgs, static, avail = _history_frames(con, run_date)
    flagged, _ = flagged_from_availability(avail, h_games, cfg.report)
    flagged = {**flagged, **{str(g): set(v) for g, v in report_out.items()}}
    games = pl.DataFrame(
        {
            "game_id": [g.game_id for g in slate],
            "home_team": [g.home_team for g in slate],
            "away_team": [g.away_team for g in slate],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    roster = wide.select("game_id", "player_id", "team_id").unique()
    a_games, a_pgs = _with_slate_placeholders(h_games, h_pgs, run_date, games, roster)
    feats = build_features(a_games, a_pgs, static, flagged, elo_params)
    box = con.execute(
        "SELECT s.game_id, s.player_id, s.blk, s.stl FROM player_game_stats s JOIN games g USING (game_id) WHERE g.game_date < ?",
        [run_date],
    ).pl()
    box = box.with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("blk").cast(pl.Float64),
        pl.col("stl").cast(pl.Float64),
    )
    slate_ids = [g.game_id for g in slate]
    a_pgs = (
        a_pgs.with_columns(pl.col("player_id").cast(pl.Int64))
        .join(box, on=["game_id", "player_id"], how="left")
        .with_columns(
            [
                pl.when(pl.col("game_id").is_in(slate_ids))
                .then(pl.col(c).fill_null(0.0))
                .otherwise(pl.col(c))
                .alias(c)
                for c in ("blk", "stl")
            ]
        )
    )
    feats = add_stock_features(feats, a_games, a_pgs, flagged)
    labels = a_pgs.select("game_id", "player_id", "blk", "stl").unique(["game_id", "player_id"])
    out = feats.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        labels, on=["game_id", "player_id"], how="left"
    )
    if out.height != feats.height:
        raise ValueError("attaching the blk / stl labels changed the row count")
    return out


# --------------------------------------------------------------------------- (b) the NB heads
def fit_nb(
    feats: pl.DataFrame, as_of: dt.date, ccfg: ContextResidualConfig
) -> tuple[dict[str, NBCountModel], dt.date]:
    """One ``NBCountModel`` per stock stat on PLAYED rows with ``game_date < as_of`` (``fit_for_date``'s rule); returns the models and the
    last training date."""
    hist = feats.filter(pl.col("game_date") < as_of)
    models: dict[str, NBCountModel] = {}
    for s in STOCK_TARGETS:
        train = stat_frame(hist, s).filter(pl.col("y").is_not_null()).sort(SORT)
        models[s] = NBCountModel(s, ccfg, stock_feature_names(s), n_bins=NB_BINS).fit(train)
    last = hist["game_date"].max()
    assert isinstance(last, dt.date)
    return models, last


def load_or_fit_nb(
    feats: pl.DataFrame,
    as_of: dt.date,
    ccfg: ContextResidualConfig,
    model_id: str,
    cache_dir: Path | None,
) -> tuple[dict[str, NBCountModel], dt.date, bool]:
    """Same-date cache like production's: key = row count + newest date + recipe. Returns ``(models, fit_through, cache_hit)``."""
    hist = feats.filter(pl.col("game_date") < as_of)
    fp: dict[str, Any] = {
        "n_rows": hist.height,
        "max_date": str(hist["game_date"].max()),
        "model_id": model_id,
        "tip_source": ccfg.report.tip_source,
    }
    fp["cfg"] = {k: v for k, v in asdict(ccfg).items() if k != "report"}
    fp = json.loads(json.dumps(fp, default=str))
    if cache_dir is not None:
        meta_p, model_p = cache_dir / "meta.json", cache_dir / "models.pkl"
        if meta_p.exists() and model_p.exists():
            meta = json.loads(meta_p.read_text())
            if meta.get("fingerprint") == fp:
                with model_p.open("rb") as f:
                    return pickle.load(f), dt.date.fromisoformat(meta["fit_through"]), True  # noqa: S301
    models, last = fit_nb(feats, as_of, ccfg)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with (cache_dir / "models.pkl").open("wb") as f:
            pickle.dump(models, f)
        (cache_dir / "meta.json").write_text(
            json.dumps({"fingerprint": fp, "fit_through": str(last)}, sort_keys=True)
        )
    return models, last, False


# --------------------------------------------------------------------------- (c) production marginals
def prod_survivals(
    con: duckdb.DuckDBPyConnection, game_ids: list[str], made_at: dt.datetime
) -> tuple[
    dict[tuple[str, int], dict[str, np.ndarray]],
    dict[tuple[str, int], dict[str, dict[str, str]]],
    dict[str, int],
]:
    """Per (game, player): the 199-point integer grid of each of pts (lt), reb, ast, fg3m rebuilt from the latest production row with
    ``made_at <=`` this run's stamp, and the provenance of each. Only players with all four are returned; ``missing`` counts the rest by reason."""
    marks = ",".join("?" for _ in game_ids)
    got: dict[str, dict[tuple[str, int], tuple[np.ndarray, dict[str, str]]]] = {}
    bad = {"bad_full": 0}
    for stat, model in PROD_SOURCES.items():
        d: dict[tuple[str, int], tuple[np.ndarray, dict[str, str]]] = {}
        for gid, pid, fs, run_id, made in con.execute(
            f"""SELECT game_id, player_id, fs, run_id, made_at FROM (
                    SELECT game_id, player_id, CAST(prediction ->> '$.p_ge_full' AS VARCHAR) AS fs, run_id, made_at,
                           row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
                    FROM forward_predictions
                    WHERE model_name = ? AND target = ? AND player_id > 0 AND game_id IN ({marks}) AND made_at <= ?)
                WHERE rn = 1""",
            [model, stat, *game_ids, made_at],
        ).fetchall():
            parsed = json.loads(fs) if fs else None
            surv = full_support.survival(parsed)
            if surv is None or parsed is None or int(parsed["n"]) != PROD_N:
                bad["bad_full"] += 1
                continue
            d[(str(gid), int(pid))] = (
                C.grid_from_survival(surv),
                {"run_id": str(run_id), "made_at": str(made)},
            )
        got[stat] = d
    keys = set.intersection(*(set(v) for v in got.values())) if got else set()
    union = set.union(*(set(v) for v in got.values())) if got else set()
    grids = {k: {s: got[s][k][0] for s in PROD_SOURCES} for k in keys}
    src = {k: {s: got[s][k][1] for s in PROD_SOURCES} for k in keys}
    return grids, src, {"missing_stat_row": len(union - keys), **bad}


# --------------------------------------------------------------------------- (e) RECENT
def recent_grids(
    con: duckdb.DuckDBPyConnection, run_date: dt.date, keys: list[tuple[str, int]]
) -> dict[str, np.ndarray]:
    """RECENT for all seven targets, rows aligned with ``keys``: the recency-weighted empirical distribution of the quantity over the player's last
    40 PLAYED games dated strictly before ``run_date`` (``recent_windows`` / ``empirical_grid`` of the copula module)."""
    pids = sorted({p for _, p in keys})
    marks = ",".join("?" for _ in pids)
    hist = con.execute(
        f"""SELECT s.player_id, s.game_id, g.game_date, s.pts, s.reb, s.ast, s.blk, s.stl
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE g.game_date < ? AND s.minutes > 0 AND s.player_id IN ({marks})""",
        [run_date, *pids],
    ).pl()
    stats = ("pts", "reb", "ast", "blk", "stl")
    hist = hist.with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("game_date").cast(pl.Date),
        *[pl.col(c).cast(pl.Float64) for c in stats],
    )
    ph = pl.DataFrame(
        {
            "player_id": [p for _, p in keys],
            "game_id": [g for g, _ in keys],
            "game_date": [run_date] * len(keys),
            **{c: [None] * len(keys) for c in stats},
        },
        schema={
            "player_id": pl.Int64,
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            **{c: pl.Float64 for c in stats},
        },
    )
    played = pl.concat([hist.select(ph.columns), ph]).sort(["player_id", "game_date", "game_id"])
    played = played.with_columns(
        (pl.col("pts") + pl.col("reb") + pl.col("ast")).alias("pra"),
        (pl.col("pts") + pl.col("reb")).alias("pr"),
        (pl.col("pts") + pl.col("ast")).alias("pa"),
        (pl.col("reb") + pl.col("ast")).alias("ra"),
        (pl.col("blk") + pl.col("stl")).alias("bs"),
    )
    is_ph = (played["game_date"] == run_date).to_numpy()
    order = {
        (str(g), int(p)): i
        for i, (g, p) in enumerate(
            zip(
                played["game_id"].to_numpy()[is_ph],
                played["player_id"].to_numpy()[is_ph],
                strict=True,
            )
        )
    }
    idx = np.array([order[(g, p)] for g, p in keys], dtype=int)
    out: dict[str, np.ndarray] = {}
    for t in TARGETS:
        win = C.recent_windows(played, t)[is_ph][idx]
        out[t] = C.empirical_grid(win)
    return out


# --------------------------------------------------------------------------- (d) the joint
def joint_arms(
    grids: dict[str, np.ndarray],
    cfg: ComboConfig,
    chunk: int = 48,
    check: Callable[[], None] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """``(S, INDEP)`` combo grids from the six stat grids (each ``(n, 199)`` integer): the frozen R and the same sampler with R = I, one call each.
    This is the function proof P1 reproduces from the committed reference."""
    cdfs = {s: C.int_cdf(grids[s], C.KMAX[s]) for s in C.STAT_ORDER}
    return joint_from_cdfs(cdfs, cfg, chunk, check)


def joint_from_cdfs(
    cdfs: dict[str, np.ndarray],
    cfg: ComboConfig,
    chunk: int = 48,
    check: Callable[[], None] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """``(S, INDEP)`` from the marginal CDFs. With ``check`` the rows go through the sampler in batches of ``JOINT_BATCH`` and ``check()`` runs
    between batches (a row's grid is independent of its batch: proof P1 reproduces it in two chunkings)."""
    eye = np.eye(len(C.STAT_ORDER))
    if check is None:
        return C.joint_grids(cdfs, cfg.r, cfg.base, chunk=chunk), C.joint_grids(
            cdfs, eye, cfg.base, chunk=chunk
        )
    n = next(iter(cdfs.values())).shape[0]
    s_parts: list[dict[str, np.ndarray]] = []
    i_parts: list[dict[str, np.ndarray]] = []
    for lo in range(0, n, JOINT_BATCH):
        check()
        part = {k: v[lo : lo + JOINT_BATCH] for k, v in cdfs.items()}
        s_parts.append(C.joint_grids(part, cfg.r, cfg.base, chunk=chunk))
        i_parts.append(C.joint_grids(part, eye, cfg.base, chunk=chunk))
    return (
        {c: np.concatenate([p[c] for p in s_parts]) for c in s_parts[0]},
        {c: np.concatenate([p[c] for p in i_parts]) for c in i_parts[0]},
    )


# --------------------------------------------------------------------------- the step
def run_combo_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    made_at: dt.datetime,
    run_id: str,
    wide: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    *,
    hb_dir: Path = HB_DIR,
    cache_root: Path | None = None,
    config: ComboConfig | None = None,
    ccfg: ContextResidualConfig | None = None,
    tip_source: str = "real",
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """One run for ``slate`` (games that have not tipped). Never raises: a failure is a ``failed`` heartbeat.

    Wall-time budget (review S1): the step is skipped, with detail ``budget`` and nothing written, when the earliest upcoming tip is
    within ``MIN_LEAD_MIN`` minutes of ``made_at`` at the start (the T-30 tick needs the database), or when it passes ``budget_s`` seconds
    (checked between units of work, never by killing a fit). A budget skip is a missing row that G-COV counts; it leaves ``consecutive_bad`` alone."""
    t0 = clock()
    budget = Budget(budget_s, clock)
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    info: dict[str, Any] = {"status": "ok", "rows": 0, "games": len(slate)}
    model_id: str | None = None
    rc = 0
    try:
        store.ensure_tables(con)
        _heartbeat_file(
            hb_dir,
            {
                "start_ts": started.isoformat(),
                "end_ts": None,
                "rc": None,
                "status": "running",
                "slate_date": str(run_date),
            },
        )
        reg = [g for g in slate if g.game_id[:3] == REGULAR and made_at < g.tipoff]
        info["routed_not_regular"] = len(slate) - len(reg)
        cfg = config or load_config()  # refuses any config that is not the frozen one
        model_id = model_id_of(cfg, ccfg)
        if not reg:
            info.update(status="skipped", detail="no regular-season game to serve", count="keep")
        elif min(g.tipoff for g in reg) - made_at < dt.timedelta(minutes=MIN_LEAD_MIN):
            info.update(
                status="skipped",
                detail="budget",
                budget_reason="earliest tip within 35 min",
                count="keep",
            )
        else:
            info.update(
                _serve(
                    con,
                    run_date,
                    reg,
                    made_at,
                    run_id,
                    wide,
                    report_out,
                    elo_params,
                    cfg,
                    model_id,
                    cache_root,
                    ccfg,
                    tip_source,
                    budget,
                )
            )
            if "count" not in info:
                info["count"] = "reset" if info["status"] == "ok" else "keep"
    except BudgetExceeded as exc:
        info.update(
            status="skipped", detail="budget", budget_reason=str(exc), count="keep", written=0
        )
    except Exception as exc:
        info.update(status="failed", detail=f"{type(exc).__name__}: {exc}"[:400], count="inc")
        rc = 1
    finished = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    info["seconds"] = round(clock() - t0, 1)
    try:
        store.write_heartbeat(
            con, slate_date=run_date, run_id=run_id, started_at=started, finished_at=finished, games_seen=len(slate),
            rows_written=int(info.get("written", 0)), model_id=model_id, status=str(info["status"]), rc=rc,
            detail=str(info.get("detail", "")),
        )  # fmt: skip
    except (
        Exception
    ) as exc:  # the heartbeat table itself failing must not raise into the pretip run
        info["heartbeat_error"] = f"{type(exc).__name__}: {exc}"[:200]
    end_rec: dict[str, Any] = {
        "start_ts": started.isoformat(), "end_ts": finished.isoformat(), "rc": rc, "status": info["status"],
        "slate_date": str(run_date), "rows_written": int(info.get("written", 0)),
    }  # fmt: skip
    if model_id is not None:
        end_rec["model_id"] = model_id
    _heartbeat_file(hb_dir, end_rec, count=str(info.get("count", "keep")))
    return info


def _serve(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    reg: list[SlateGame],
    made_at: dt.datetime,
    run_id: str,
    wide: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    cfg: ComboConfig,
    model_id: str,
    cache_root: Path | None,
    ccfg: ContextResidualConfig | None = None,
    tip_source: str = "real",
    budget: Budget | None = None,
) -> dict[str, Any]:
    budget = budget or Budget()
    gids = [g.game_id for g in reg]
    tip = {g.game_id: g.tipoff for g in reg}
    prod, src, missing = prod_survivals(con, gids, made_at)
    if not prod:
        return {
            "status": "skipped",
            "detail": "no production rows for the slate",
            "written": 0,
            "count": "inc",  # review M7: a regular-season slate without production rows is a broken join, not a quiet day
            **missing,
        }
    ccfg = ccfg or ContextResidualConfig()
    t_feat = time.perf_counter()
    feats = build_combo_frame(
        con,
        run_date,
        reg,
        wide.filter(pl.col("game_id").is_in(gids)),
        report_out,
        elo_params,
        ccfg,
    )
    t_feat = time.perf_counter() - t_feat
    budget.check()
    cache_dir = None if cache_root is None else cache_root / f"{run_date.isoformat()}_combo"
    models, fit_through, hit = load_or_fit_nb(feats, run_date, ccfg, model_id, cache_dir)
    budget.check()
    slate_feats = feats.filter(pl.col("game_id").is_in(gids))
    nb: dict[str, tuple[pl.DataFrame, np.ndarray]] = {}
    for s in STOCK_TARGETS:
        rows = stat_frame(slate_feats, s)
        _, q199 = models[s].predict(rows, CRPS_TAUS)
        nb[s] = (rows, to_integer_support(q199).astype(np.int16))
    base_rows = nb[STOCK_TARGETS[0]][0]
    for s in STOCK_TARGETS[1:]:
        if not base_rows.select("game_id", "player_id").equals(
            nb[s][0].select("game_id", "player_id")
        ):
            raise ValueError("blk and stl rows are not aligned")
    key_all = [
        (str(g), int(p)) for g, p in zip(base_rows["game_id"], base_rows["player_id"], strict=True)
    ]
    n_prior_all = base_rows["n_prior"].to_numpy()
    keep = [i for i, k in enumerate(key_all) if k in prod]
    info: dict[str, Any] = {
        "slate_rows_n_prior_ge_5": len(key_all), "servable": len(keep), "not_servable_no_feature_row": len(prod) - len(keep),
        **missing, "feature_seconds": round(t_feat, 1), "cache_hit": hit, "fit_through": str(fit_through),
    }  # fmt: skip
    if not keep:
        return {
            "status": "ok",
            "rows": 0,
            "written": 0,
            "deduped": 0,
            "refused_after_tip": 0,
            **info,
        }
    keys = [key_all[i] for i in keep]
    grids: dict[str, np.ndarray] = {s: np.stack([prod[k][s] for k in keys]) for s in PROD_SOURCES}
    for s in STOCK_TARGETS:
        grids[s] = nb[s][1][keep]
    s_arm, i_arm = joint_arms(grids, cfg, check=budget.check)
    budget.check()
    rec = recent_grids(con, run_date, keys)
    budget.check()
    arms: dict[str, np.ndarray] = {**s_arm, **{t: grids[t] for t in STOCK_TARGETS}}
    rows_out: list[dict[str, object]] = []
    for t in TARGETS:
        q19 = C.q19_of_grid199(arms[t])
        for j, (gid, pid) in enumerate(keys):
            r: dict[str, object] = {
                "made_at": made_at, "run_id": run_id, "game_id": gid, "player_id": pid, "target": t, "tipoff_utc": tip[gid],
                "tip_source": tip_source, "in_prod_log": True, "n_prior": int(n_prior_all[keep[j]]), "model_id": model_id,
                "fit_through": fit_through, "r_id": cfg.r_id, "base_id": cfg.base_id, "s_pmf199": store.dumps_ints(arms[t][j]),
                "s_q19": store.dumps_ints(q19[j]), "indep_pmf199": store.dumps_ints(i_arm[t][j]) if t in i_arm else None,
                "recent_pmf199": store.dumps_ints(rec[t][j]), "direct_pmf199": None, "recent_cal_pmf199": None,
                "src": json.dumps(src[(gid, pid)], sort_keys=True),
            }  # fmt: skip
            rows_out.append(r)
    written, deduped, refused = store.append_rows(con, rows_out, made_at)
    total = sum(len(v) for r in rows_out for v in r.values() if isinstance(v, str))
    nbytes = int(
        total * written / max(len(rows_out), 1)
    )  # P4: MB written per slate group, estimated from the JSON text of the rows kept
    return {
        "status": "ok", "rows": len(rows_out), "written": written, "deduped": deduped, "refused_after_tip": refused,
        "mb_written_est": round(nbytes / 1e6, 2), "model_id": model_id, **info,
    }  # fmt: skip
