# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined"
"""PPLAY_MINUTES_VALID harness (docs/prereg/PPLAY_MINUTES_VALID.md, frozen sha 91d82de7, freeze commit 8e15012).

A valid test of the P(play) / minutes model production serves, head to head with P(play) R:

    P0   production as served (build_minutes_features(use_game_context=False) -> predict_minutes, MinutesModelConfig defaults)
    P0c  P0 with a walk-forward Platt map that never sees the scored rows (era-flagged games excluded)
    R19  RECENT_ROLE's R columns + four era channels, LightGBM, refit before every calendar month on every earlier row from 2019-20 on

    .venv/bin/python -W ignore -m research.eval.pplay_minutes_valid check                       # freeze + loader-review refusals only
    .venv/bin/python -W ignore -m research.eval.pplay_minutes_valid smoke                       # CPU, tiny SYNTHETIC world, every stage, no real data
    .venv/bin/python -W ignore -m research.eval.pplay_minutes_valid gate --sha <64 hex>         # frame + G1 + G2 + parity + reproduction + era + holdout (no arm)
    .venv/bin/python -W ignore -m research.eval.pplay_minutes_valid run  --sha <64 hex>         # gate -> arms -> G3/G4/ECE/slices -> G5 (K=50) -> secondaries -> report

``gate``, ``run`` and ``report`` refuse unless (a) ``--sha`` equals the hash of the rule above the marker, (b) a commit ``prereg(PPLAY_MINUTES_VALID): <sha>`` carries that
text, and (c) a committed loader review after the freeze says ``Verdict: APPROVED`` for exactly this code (``pplay_minutes_valid_rule.require_review``).
``run`` takes ``data/ops/heavy.lock`` itself (retry 120 s, removes only its own). Both databases are opened read-only with ``season <= 2024`` in SQL; the frozen holdout
season has no path in this harness. A failed guard or gate is NO VERDICT, never a result.

Reuses (does not copy): ``pplay_context_eval`` (P0 rows, terms, snapshots, win-pct priors), ``recent_role_eval`` (arm columns, the R22 walk, production state),
``minutes_v2_eval`` (production minutes table, M2 frame, minutes scoring), ``history_window_eval`` (the union world, real-tip report rows, validated era flags),
``workload`` as-of loads, ``pplay_into_props_live`` (row deltas), ``workload_eval`` (null audit).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, month_blocks
from nba.props.context_residual import ContextResidualConfig, stat_frame
from research.eval import history_window_eval as HW
from research.eval import minutes_v2_eval as MV
from research.eval import pplay_context_eval as PC
from research.eval import pplay_minutes_valid_core as C
from research.eval import pplay_minutes_valid_rule as RULE
from research.eval import recent_role_eval as RR
from research.recent_role import prod_state as PS
from research.workload import asof as W

OUT_DIR = Path("reports/prereg_pplay_minutes_valid")
REPORT_MD = Path("reports/prereg_pplay_minutes_valid.md")
CACHE = Path("data/ops/pplay_minutes_valid_cache")
DB = "nba.duckdb"
HISTORY_DB = "data/history/nba_history.duckdb"
LOCK = Path("data/ops/heavy.lock")
MANIFEST = Path("docs/data_manifests/latest.json")
HISTORY_MANIFEST = Path("docs/data_manifests/history_latest.json")
STORED_RR_SCORES = Path("data/ops/recent_role_cache/scores.npz")

#: The data versions the harness asserts (history at the freeze evidence; production at the harness build, 2026-10-12). A different version stops the run
#: unless ``--accept-data-version <12 hex>`` names it, in which case the drift is stamped into the results.
EXPECTED_DATA_VERSIONS = {"history": "9e66461ad129", "production": "a55164376845"}

GAME_COLS = ("game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts")
PGS_COLS = (*HW.PROD_PGS, "pf")
STATIC_COLS = ("player_id", "position", "birth_date", "draft_year")
STATS = ("pts", "reb", "ast", "fg3m")


# --------------------------------------------------------------------------- lock, stamps
class HeavyLock:
    """``mkdir data/ops/heavy.lock`` with a retry every 120 s; ``rmdir`` only if THIS process created it."""

    def __init__(self, path: Path = LOCK, retry_s: float = 120.0) -> None:
        self.path, self.retry_s, self.held = path, retry_s, False

    def __enter__(self) -> HeavyLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.path.mkdir()
                self.held = True
                return self
            except FileExistsError:
                C._log("heavy.lock held; retry in 120 s")
                time.sleep(self.retry_s)

    def __exit__(self, *exc: object) -> None:
        if self.held:
            with contextlib.suppress(OSError):
                self.path.rmdir()
            self.held = False


def data_stamp(
    manifest: Path = MANIFEST,
    history_manifest: Path = HISTORY_MANIFEST,
    db: str = DB,
    hdb: str = HISTORY_DB,
    accept: str | None = None,
    hash_files: bool = True,
) -> dict[str, Any]:
    """Data versions and DB file hashes stamped into the results. Raises unless both versions equal ``EXPECTED_DATA_VERSIONS`` (or ``accept`` names the production one)."""
    prod = json.loads(manifest.read_text())["data_version"]
    hist = json.loads(history_manifest.read_text())["data_version"]
    drift: dict[str, str] = {}
    if hist != EXPECTED_DATA_VERSIONS["history"]:
        raise RuntimeError(
            f"history data_version {hist} != expected {EXPECTED_DATA_VERSIONS['history']}: refusing"
        )
    if prod != EXPECTED_DATA_VERSIONS["production"]:
        if accept != prod:
            raise RuntimeError(
                f"production data_version {prod} != expected {EXPECTED_DATA_VERSIONS['production']}; re-run with --accept-data-version {prod} to stamp the drift"
            )
        drift["production"] = prod
    out: dict[str, Any] = {
        "data_version_production": prod,
        "data_version_history": hist,
        "drift_accepted": drift,
    }
    if hash_files:
        out["sha256_nba_duckdb"] = RULE.file_sha256(Path(db))
        out["sha256_history_duckdb"] = RULE.file_sha256(Path(hdb))
    return out


# --------------------------------------------------------------------------- the union world (polars only; every edit for G1 is a frame edit)
def _full_table(path: str, sql: str) -> pl.DataFrame:
    con = HW._ro(path)
    try:
        return con.execute(sql).pl()
    finally:
        con.close()


def _union(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in a.columns if c in b.columns]
    return pl.concat([a.select(cols), b.select(cols)], how="vertical_relaxed")


def _coalesce_static(frames: list[pl.DataFrame]) -> pl.DataFrame:
    cols = sorted(set.intersection(*[set(f.columns) for f in frames]))
    allf = pl.concat([f.select(cols) for f in frames], how="vertical_relaxed")
    return allf.group_by("player_id", maintain_order=True).agg(
        [pl.col(c).drop_nulls().first().alias(c) for c in cols if c != "player_id"]
    )


def make_world(db: str = DB, hdb: str = HISTORY_DB) -> dict[str, Any]:
    """Games, box, statics, real-tip report rows and era flags of seasons 2019-2024, both databases read-only, ``season <= 2024`` in every query."""
    hw = HW.load_world(db, hdb)
    cur_s = ",".join(str(s) for s in HW.CUR_SEASONS)
    his_s = ",".join(str(s) for s in HW.HIST_SEASONS)

    def games_sql(ss: str) -> str:
        return f"SELECT * FROM games WHERE season IN ({ss}) AND season <= {C.MAX_SEASON}"

    def pgs_sql(ss: str) -> str:
        return f"SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season IN ({ss}) AND g.season <= {C.MAX_SEASON}"

    games_full = _union(_full_table(db, games_sql(cur_s)), _full_table(hdb, games_sql(his_s)))
    pgs_full = _union(_full_table(db, pgs_sql(cur_s)), _full_table(hdb, pgs_sql(his_s)))
    static = _coalesce_static(
        [
            _full_table(db, "SELECT * FROM players_static"),
            _full_table(hdb, "SELECT * FROM players_static"),
        ]
    )
    flags = HW.era_flag_frame(hw)
    games_all = hw.games_all
    snaps = HW.rows_with_real_tips(
        HW.report_rows(hw.avail_all, games_all, hw.cfg.report.sources), hw.tips_all, hw.hist_ids
    )
    world = {
        "games_full": games_full.with_columns(pl.col("game_date").cast(pl.Date)),
        "pgs_full": pgs_full,
        "static": static,
        "team_context": pl.DataFrame(schema={"game_id": pl.Utf8, "team_id": pl.Int64}),
        "snaps": snaps,
        "flags": flags.with_columns(*[pl.col(c).cast(pl.Boolean) for c in HW.FLAG_COLS]),
        "cfg": hw.cfg,
        "elo_params": hw.elo_params,
        "hist_ids": hw.hist_ids,
    }
    check_world(world)
    return world


def check_world(world: dict[str, Any]) -> None:
    C.assert_holdout(world["games_full"])
    C.assert_holdout(world["flags"])
    ids = set(world["games_full"]["game_id"].to_list())
    if len(ids) != world["games_full"].height:
        raise ValueError("duplicate game_id in the union games")
    if not set(world["pgs_full"]["game_id"].unique().to_list()) <= ids:
        raise ValueError("box rows for games outside the union games")


def scratch(world: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    sc = duckdb.connect(":memory:")
    for name, df in (
        ("games", world["games_full"]),
        ("player_game_stats", world["pgs_full"]),
        ("team_context", world["team_context"]),
        ("players_static", world["static"]),
    ):
        sc.register("_src", df)
        sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
        sc.unregister("_src")
    return sc


def flagged_union(world: dict[str, Any]) -> dict[str, set[int]]:
    """OUT sets: the latest snapshot at or before tip - 60 min, except 2021-22 where it is tip - 180 min (the 2022-24 median lead, rule 4)."""
    from nba.features.injury_report import latest_pretip_flagged

    rows, games = world["snaps"], world["games_full"]
    cfg = world["cfg"]
    ids21 = games.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)["game_id"].to_list()
    f60, _ = latest_pretip_flagged(rows.filter(~pl.col("game_id").is_in(ids21)), cfg.report)
    f180, _ = latest_pretip_flagged(
        rows.filter(pl.col("game_id").is_in(ids21)),
        dataclasses.replace(cfg.report, lead_minutes=180),
    )
    return {**f60, **f180}


def legacy_world(world: dict[str, Any], sc: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """The dict shape ``pplay_context_eval.build_base`` / ``minutes_v2_eval.build_frame`` expect, from the union polars frames."""
    games = world["games_full"].select([c for c in GAME_COLS if c in world["games_full"].columns])
    pgs = world["pgs_full"].select([c for c in PGS_COLS if c in world["pgs_full"].columns])
    static = world["static"].select([c for c in STATIC_COLS if c in world["static"].columns])
    return {
        "games": games,
        "pgs": pgs,
        "static": static,
        "flagged": flagged_union(world),
        "sc": sc,
        "elo_params": world["elo_params"],
        "cfg": world["cfg"],
    }


# --------------------------------------------------------------------------- frames
def r_columns() -> list[str]:
    return [*RR.arm_columns()["R"], *C.ERA_COLS]


def arm_sets() -> dict[str, list[str]]:
    """R19 (primary) and the descriptive decomposition arms C19 / C'19 (T458's C and C' on the same window and channels)."""
    a = RR.arm_columns()
    return {
        "R19": [*a["R"], *C.ERA_COLS],
        "Cp19": [*a["Cp"], *C.ERA_COLS],
        "C19": [*a["base"], *C.ERA_COLS],
    }


def build_p_frame(world: dict[str, Any], lw: dict[str, Any]) -> pl.DataFrame:
    """Cell-P rows (rostered, n_played_prior >= 5, not OUT on the pre-tip report) of seasons 2019-2024 with P0 (served), R19's inputs and the era channels."""
    sc, games, pgs = lw["sc"], lw["games"], lw["pgs"]
    base = PC.build_base(
        lw
    )  # P(play) rows, OUT filter, n_played_prior >= 5, label ``played`` (minutes > 0), age, exp_margin
    prod = MV.production_minutes_table(sc).rename(
        {"p_mu": "mu0", "p_sigma": "sigma0", "p_play_raw": "p0"}
    )
    base = base.join(prod, on=C.KEYS, how="left")
    if base.select(pl.col("p0").is_null().any()).item():
        raise ValueError("production table is missing Cell-P rows")
    prior_wp = PC.prior_win_pct(games, None)
    terms = PC.build_terms(
        base.select("game_id", "player_id", "team_id", "season", "game_date"),
        games,
        pgs,
        world["snaps"],
        prior_wp,
    )
    fr = base.join(terms, on=C.KEYS, how="left")
    if fr.height != base.height:
        raise ValueError("term join changed the row count")
    fr = fr.with_columns(pl.col("player_id").cast(pl.Int64))
    played = (
        pgs.join(games.select("game_id", "game_date"), on="game_id")
        .filter(pl.col("minutes") > 0)
        .select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            "team_id",
            "game_date",
            pl.col("minutes").cast(pl.Float64),
            pl.lit(None, dtype=pl.Float64).alias("distance"),
            pl.lit(None, dtype=pl.Float64).alias("speed"),
        )
    )
    load = W.player_load_features(played, fr.select("game_id", "player_id", "game_date"))
    keep = {c[len(W.PREFIX) :]: c for c in RR.R_COLS}
    load = load.select("game_id", "player_id", *[pl.col(k).alias(v) for k, v in keep.items()])
    fr = fr.join(load, on=C.KEYS, how="left")
    box = pgs.join(games.select("game_id", "game_date", "season"), on="game_id")
    st = PS.prod_state_rows(
        box, fr.select("game_id", "player_id", "team_id", "season", "game_date")
    )
    fr = fr.join(st.select(*C.KEYS, *RR.CP_ADD), on=C.KEYS, how="left")
    fr = C.add_era_channels(fr, world["flags"])
    fr = C.drop_report_features(fr, C.REPORT_FEATURE_COLS)
    tg = PC.team_games(games)
    ret = C.returner_flag(pgs, fr.select("game_id", "player_id", "team_id"), tg)
    fr = fr.join(ret, on=C.KEYS, how="left")
    fr, _ = C.exclude_null_age_player(fr)
    fr = fr.sort(["game_date", "game_id", "player_id"])
    C.assert_holdout(fr)
    return fr


def opp_games_prior(frame: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """F2: ``opp_games_prior`` = the number of earlier rows of the opponent in the games table ``team_game_context`` rolls over (sorted by team, date, game id,
    across seasons). The rolling-20 mean needs 5, so ``opp_r20_pts_*`` is NULL exactly when this is below 5. Never a model input."""
    g = games.select("game_id", "game_date", "home_team", "away_team")
    long = (
        pl.concat(
            [
                g.select(
                    "game_id", "game_date", pl.col("home_team").cast(pl.Int64).alias("opp_id")
                ),
                g.select(
                    "game_id", "game_date", pl.col("away_team").cast(pl.Int64).alias("opp_id")
                ),
            ]
        )
        .sort(["opp_id", "game_date", "game_id"])
        .with_columns(
            pl.int_range(pl.len()).over("opp_id").cast(pl.Float64).alias("opp_games_prior")
        )
        .select("game_id", "opp_id", "opp_games_prior")
    )
    out = frame.with_columns(pl.col("opp_id").cast(pl.Int64)).join(
        long, on=["game_id", "opp_id"], how="left"
    )
    if out.height != frame.height:
        raise ValueError("opp_games_prior join changed the row count")
    return out


def build_m_frame_ex(
    world: dict[str, Any], lw: dict[str, Any]
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """MINUTES_V2's frame (production features + V2 group + realised labels) on the union, with the era channels and the 2021-22 report drop, joined to P0's mu / sigma.

    F1: the report-derived columns are DERIVED (the frame is built again with no OUT sets and the differing columns listed), united with the static floor
    ``C.M2_REPORT_COLS``, and every one of them is NULL on 2021-22 rows. Returns (frame, report-derived columns)."""
    from nba.props.context_residual import MIN_PRIOR_PLAYED  # noqa: F401  (stat_frame default)

    f = MV.build_frame(lw)
    f_none = MV.build_frame({**lw, "flagged": {}})
    derived = tuple(c for c in C.derive_report_cols(f, f_none) if c != "has_report")
    cols = tuple(dict.fromkeys([*C.M2_REPORT_COLS, *derived]))
    f = C.add_era_channels(f, world["flags"])
    f = C.drop_report_features(f, cols, zero=("has_report",))
    f = opp_games_prior(f, lw["games"])
    sf = stat_frame(f, "pts").sort(["game_date", "game_id", "player_id"])
    sf, _ = C.exclude_null_age_player(sf)
    prod = MV.production_minutes_table(lw["sc"])
    sf = sf.join(prod, on=C.KEYS, how="left")
    C.assert_holdout(sf)
    return sf, cols


def build_m_frame(world: dict[str, Any], lw: dict[str, Any]) -> pl.DataFrame:
    return build_m_frame_ex(world, lw)[0]


@dataclasses.dataclass
class Built:
    p: pl.DataFrame
    m: pl.DataFrame
    lw_flagged: dict[str, set[int]]
    m_report_cols: tuple[str, ...] = ()  # F1: derived, not hand-listed


def build_all(world: dict[str, Any]) -> Built:
    sc = scratch(world)
    try:
        lw = legacy_world(world, sc)
        p = build_p_frame(world, lw)
        m, m_cols = build_m_frame_ex(world, lw)
        flagged = lw["flagged"]
    finally:
        sc.close()
    return Built(p, m, flagged, m_cols)


# --------------------------------------------------------------------------- run-time gates
def check(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def p_inputs() -> list[str]:
    return sorted(set(r_columns()) | {"p0", "mu0", "sigma0"})


def m_inputs() -> list[str]:
    from research.props.minutes_v2 import V2_MINUTES_FEATURES

    return [*V2_MINUTES_FEATURES, *C.ERA_COLS]


def _cond(frame: pl.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in frame.columns]


def edit_world(world: dict[str, Any], cutoff: dt.date, mode: str) -> dict[str, Any]:
    """``future``: box rows dated on or after ``cutoff`` (the cutoff day's own box included), results of those games, plus schedule facts and report snapshots of games
    dated after ``cutoff`` (tips, national-TV flag, statuses) edited wildly. ``control``: box rows dated 15-30 days BEFORE ``cutoff`` edited, which must change something."""
    w = dict(world)
    games, pgs, snaps = world["games_full"], world["pgs_full"], world["snaps"]
    gdate = games.select("game_id", "game_date")
    pg = pgs.join(gdate, on="game_id", how="left")
    if mode == "future":
        hit = pl.col("game_date") >= cutoff
        gexprs = [
            pl.when(pl.col("game_date") >= cutoff)
            .then(pl.col("away_pts") + 40)
            .otherwise(pl.col("home_pts"))
            .alias("home_pts"),
            pl.when(pl.col("game_date") >= cutoff)
            .then(pl.col("home_pts") - 7)
            .otherwise(pl.col("away_pts"))
            .alias("away_pts"),
        ]
        if "national_tv" in games.columns:
            gexprs.append(
                pl.when(pl.col("game_date") > cutoff)
                .then(pl.lit("ESPN"))
                .otherwise(pl.col("national_tv"))
                .alias("national_tv")
            )
        w["games_full"] = games.with_columns(gexprs)
        sn = snaps.with_columns(pl.col("game_date").cast(pl.Date))
        w["snaps"] = sn.with_columns(
            pl.when(pl.col("game_date") > cutoff)
            .then(pl.lit("out"))
            .otherwise(pl.col("status"))
            .alias("status"),
            pl.when(pl.col("game_date") > cutoff)
            .then(pl.col("tip_et") + pl.duration(hours=5))
            .otherwise(pl.col("tip_et"))
            .alias("tip_et"),
        )
    elif mode == "control":
        hit = (pl.col("game_date") >= cutoff - dt.timedelta(days=30)) & (
            pl.col("game_date") <= cutoff - dt.timedelta(days=15)
        )
    else:
        raise ValueError(mode)
    edited = pg.with_columns(
        pl.when(hit)
        .then(pl.when(pl.col("minutes").is_null()).then(31.0).otherwise(2.0))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(hit)
        .then(~pl.col("starter").fill_null(False))
        .otherwise(pl.col("starter"))
        .alias("starter"),
        pl.when(hit)
        .then(pl.col("pts").fill_null(0) * 3 + 11)
        .otherwise(pl.col("pts"))
        .alias("pts"),
    ).drop("game_date")
    w["pgs_full"] = edited.select(pgs.columns)
    return w


def planted_future_gate(
    world: dict[str, Any], built: Built, cutoff: dt.date | None = None
) -> dict[str, Any]:
    """G1, on the real frame at run start: everything dated after the cutoff (and the cutoff day's own box) edited wildly -> every P0 value, every P0c map and every
    R19 / M2_19 input of rows dated at or before the cutoff is byte-identical; a control edit before the cutoff must change at least one value."""
    games = built.p
    if cutoff is None:
        mid = games.filter(pl.col("season") == C.VERDICT_SEASONS[0])["game_date"].sort()
        cutoff = mid[mid.len() // 2]
    pcols, mcols = p_inputs(), m_inputs()
    mask = pl.col("game_date") <= cutoff
    out: dict[str, Any] = {"cutoff": str(cutoff)}
    for mode in ("future", "control"):
        b2 = build_all(edit_world(world, cutoff, mode))
        rp = C.frames_identical(built.p, b2.p, _cond(built.p, pcols), mask)
        rm = C.frames_identical(
            built.m, b2.m, _cond(built.m, mcols), mask, strict_before=pl.col("game_date") < cutoff
        )
        maps_same = platt_maps_equal(built.p, b2.p, cutoff)
        out[mode] = {"p_inputs": rp, "m2_inputs": rm, "p0c_maps_identical": maps_same}
    fut, ctl = out["future"], out["control"]
    out["ok"] = bool(
        fut["p_inputs"]["identical"]
        and fut["m2_inputs"]["identical"]
        and fut["p0c_maps_identical"]
        and not (
            ctl["p_inputs"]["identical"]
            and ctl["m2_inputs"]["identical"]
            and ctl["p0c_maps_identical"]
        )
    )
    return out


def platt_maps_equal(a: pl.DataFrame, b: pl.DataFrame, cutoff: dt.date) -> bool:
    """Every month block starting at or before ``cutoff``: the P0c map fitted on rows strictly earlier is the same in both frames."""
    test = a.filter(
        pl.col("season").is_in(list(C.SCORED_SEASONS)) & (pl.col("game_date") <= cutoff)
    )
    for start, _ in month_blocks(test):
        maps = []
        for fr in (a, b):
            tr = fr.filter((pl.col("game_date") < start) & ~pl.col("era_any"))
            maps.append(
                C.fit_platt(tr["p0"].to_numpy(), tr["played"].to_numpy())
                if tr.height > 50
                else (0.0, 0.0)
            )
        if maps[0] != maps[1]:
            return False
    return True


def era_and_report_gate(
    frame: pl.DataFrame, m: pl.DataFrame, m_report_cols: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """2021-22: no row carries a report feature; ``era_report_dropped`` is 1 exactly on 2021-22 rows; era channels are 0 on every verdict-season and 2022 row.

    The minutes head is checked on the union of the floor ``C.M2_REPORT_COLS`` and ``m_report_cols`` (every column derived from the OUT sets, F1)."""
    f21 = frame.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)
    leaks = {c: int(f21[c].is_not_null().sum()) for c in C.REPORT_FEATURE_COLS if c in f21.columns}
    m21 = m.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)
    m_cols = tuple(dict.fromkeys([*C.M2_REPORT_COLS, *m_report_cols]))
    leaks_m = {c: int(m21[c].is_not_null().sum()) for c in m_cols if c in m21.columns}
    has_rep = int((m21["has_report"] != 0).sum()) if "has_report" in m21.columns else 0
    dropped_exact = bool(
        ((frame["era_report_dropped"] == 1.0) == (frame["season"] == C.REPORT_DROPPED_SEASON)).all()
        and ((m["era_report_dropped"] == 1.0) == (m["season"] == C.REPORT_DROPPED_SEASON)).all()
    )
    scored = frame.filter(pl.col("season").is_in([2022, *C.VERDICT_SEASONS]))
    era_nonzero = int(
        scored.select(pl.sum_horizontal(*[pl.col(c).abs() for c in C.ERA_COLS]).gt(0).sum()).item()
    )
    return [
        check(
            "2021-22 rows carry no report feature (P(play))",
            leaks,
            "all 0",
            not any(leaks.values()),
        ),
        check(
            "2021-22 rows carry no report feature (minutes head)",
            {**leaks_m, "has_report_nonzero": has_rep, "n_columns_checked": len(m_cols)},
            "all 0",
            not any(leaks_m.values()) and has_rep == 0,
        ),
        check(
            "era_report_dropped is 1 exactly on 2021-22 rows", dropped_exact, "exact", dropped_exact
        ),
        check("era channels are 0 on every 2022-24 scored row", era_nonzero, "0", era_nonzero == 0),
    ]


def holdout_gate(frames: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Every frame asserts ``season <= 2024``; the harness has no path to the frozen holdout season."""
    mx = {k: int(v["season"].max()) for k, v in frames.items()}
    return check(
        "holdout: max season per frame",
        mx,
        f"<= {C.MAX_SEASON}",
        all(v <= C.MAX_SEASON for v in mx.values()),
    )


def nba_only_world(world: dict[str, Any]) -> dict[str, Any]:
    """The same world restricted to the production database's own seasons (2022-24): the 'nba-only scope' P0 is computed on."""
    w = dict(world)
    keep = [g for g in world["games_full"].filter(pl.col("season") >= 2022)["game_id"].to_list()]
    w["games_full"] = world["games_full"].filter(pl.col("game_id").is_in(keep))
    w["pgs_full"] = world["pgs_full"].filter(pl.col("game_id").is_in(keep))
    return w


def p0_table(world: dict[str, Any]) -> pl.DataFrame:
    sc = scratch(world)
    try:
        return MV.production_minutes_table(sc).rename(
            {"p_mu": "mu0", "p_sigma": "sigma0", "p_play_raw": "p0"}
        )
    finally:
        sc.close()


SlateFn = Callable[[dt.date, pl.DataFrame], pl.DataFrame]


def default_slate_fn(db: str = DB) -> SlateFn:
    """``forward.predict_slate`` on the production database, read-only, default ForwardConfig (what is served)."""

    def fn(day: dt.date, slate: pl.DataFrame) -> pl.DataFrame:
        from nba.props.forward import ForwardConfig, predict_slate

        con = duckdb.connect(db, read_only=True)
        try:
            cfg = ForwardConfig()
            if cfg.sim_minutes != "conditional" or cfg.p_play_platt is not None:
                raise RuntimeError(
                    "served ForwardConfig changed: parity comparison needs sim_minutes='conditional' and no Platt map"
                )
            return predict_slate(con, day, slate, config=cfg)
        finally:
            con.close()

    return fn


def parity_gate(
    world: dict[str, Any],
    built: Built,
    slate_fn: SlateFn | None = None,
    n_dates: int = 10,
    season: int = 2024,
) -> dict[str, Any]:
    """F10: on seeded dates of ``season``, ``predict_slate`` and P0 on the nba-only scope agree on every shared (game, player): |p| and |mu| <= 1e-12; no Cell-P row
    carries a rookie prior or a new-team multiplier (structurally: ``official_roster`` is None, so neither path runs). Prints the union-vs-nba-only change in P0."""
    slate_fn = slate_fn or default_slate_fn()
    p0n = p0_table(nba_only_world(world))
    games = world["games_full"].filter(pl.col("season") == season)
    days = sorted(games["game_date"].unique().to_list())
    rng = np.random.default_rng(0)
    pick = sorted(rng.choice(len(days), size=min(n_dates, len(days)), replace=False).tolist())
    worst_p = worst_mu = 0.0
    n_shared: list[int] = []
    for i in pick:
        day = days[i]
        slate = games.filter(pl.col("game_date") == day).select("game_id", "home_team", "away_team")
        out = slate_fn(day, slate)
        if out.is_empty():
            n_shared.append(0)
            continue
        o = out.select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            pl.col("p_play_raw").cast(pl.Float64).alias("p_f"),
            pl.col("proj_minutes").cast(pl.Float64).alias("mu_f"),
        ).unique(["game_id", "player_id"])
        j = o.join(p0n, on=C.KEYS, how="inner")
        n_shared.append(j.height)
        if j.height:
            worst_p = max(worst_p, float((j["p_f"] - j["p0"]).abs().max()))
            worst_mu = max(worst_mu, float((j["mu_f"] - j["mu0"]).abs().max()))
    j2 = (
        built.p.filter(pl.col("season").is_in(list(C.VERDICT_SEASONS)))
        .select(*C.KEYS, pl.col("p0").alias("p_union"))
        .join(p0n.select(*C.KEYS, pl.col("p0").alias("p_nba")), on=C.KEYS, how="inner")
    )
    dp = (j2["p_union"] - j2["p_nba"]).abs()
    scope = {
        "n_rows": j2.height,
        "share_changed": float((dp > 0).mean()) if j2.height else float("nan"),
        "mean_abs_dp": float(dp.mean()) if j2.height else float("nan"),
    }
    print(
        "declared data-scope effect (union vs nba-only P0 on 2023-24 Cell-P rows):",
        scope,
        flush=True,
    )
    ok = bool(
        worst_p <= C.PARITY_TOL
        and worst_mu <= C.PARITY_TOL
        and sum(n_shared) > 0
        and all(n > 0 for n in n_shared)
    )
    return check(
        "parity: forward.predict_slate vs P0 (nba-only scope)",
        {
            "dates": [str(days[i]) for i in pick],
            "n_shared": n_shared,
            "max_abs_dp": worst_p,
            "max_abs_dmu": worst_mu,
            "rookie_prior_rows": 0,
            "new_team_multiplier_rows": 0,
            "data_scope_effect": scope,
        },
        f"<= {C.PARITY_TOL} on every shared (game, player), shared > 0 per date",
        ok,
    )


def load_flag_inputs(db: str = DB) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(games, availability) for the OUT-set parity check: read-only, games ``season <= 2024`` and availability rows of those games dated before the July after the last verdict season, both
    bounded IN SQL (F4: ``context_residual_eval.load_inputs`` read every ``player_availability`` row, later seasons' included)."""
    con = duckdb.connect(db, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {C.MAX_SEASON}"
        ).pl()
        avail = con.execute(
            "SELECT game_id, player_id, status, as_of, source FROM player_availability "
            "WHERE game_id IS NOT NULL "
            f"AND game_id IN (SELECT game_id FROM games WHERE season <= {C.MAX_SEASON}) "
            f"AND as_of < TIMESTAMP '{C.MAX_SEASON + 1}-07-01'"
        ).pl()
    finally:
        con.close()
    return games, avail


def flagged_parity_gate(world: dict[str, Any], db: str = DB) -> dict[str, Any]:
    """The union OUT sets equal production's (``flagged_from_availability`` on nba.duckdb alone) on every 2022-24 game."""
    from nba.props.context_residual import flagged_from_availability

    games, avail = load_flag_inputs(db)
    flagged, _ = flagged_from_availability(avail, games, world["cfg"].report, max_season=MAX_SEASON)
    mine = flagged_union(world)
    ids = set(games.filter(pl.col("season").is_in([2022, 2023, 2024]))["game_id"].to_list())
    a = {g: s for g, s in mine.items() if g in ids}
    b = {g: s for g, s in flagged.items() if g in ids}
    return check(
        "OUT sets on 2022-24 games equal production's",
        {"games": len(ids), "mine": len(a), "production": len(b)},
        "identical",
        a == b,
    )


def repro_gate(
    res19: dict[str, np.ndarray] | None = None, manifest_ctx: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """R22 on the 2022-start frame (the existing RECENT_ROLE construction) equals the stored ``p_R``: max |diff| <= 1e-9, n 33,093 / 33,127. Returns (check, R22 scores)."""
    fr, cfg, _ = RR.build_frame()
    ck = CACHE / "r22_blocks"
    man: dict[str, Any] = {**(manifest_ctx or {}), "kind": "r22", "arms": RR.arm_columns()["R"]}
    if isinstance(fr, pl.DataFrame):
        man["label_hash"] = C.frame_hash(fr, ["played"])
        man["input_hash"] = C.frame_hash(fr, list(RR.arm_columns()["R"]))
    C.guard_cache(
        ck, man
    )  # F6: keyed checkpoints; recent_role_eval.walk's own reader unpickles, so refuse any non-plain array first
    C.assert_no_pickle(ck)
    res, _ = RR.walk(fr, {"R": RR.arm_columns()["R"]}, cfg, ckpt=ck)
    stored = dict(np.load(STORED_RR_SCORES, allow_pickle=False))
    mine = pl.DataFrame(
        {
            "gid": res["gid"].astype(str),
            "pid": res["pid"].astype(np.int64),
            "p": res["p_R"],
            "season": res["season"],
        }
    )
    ref = pl.DataFrame(
        {
            "gid": stored["gid"].astype(str),
            "pid": stored["pid"].astype(np.int64),
            "q": stored["p_R"],
        }
    )
    j = mine.join(ref, on=["gid", "pid"], how="full", coalesce=True)
    diff = float((j["p"] - j["q"]).abs().max()) if j.height else float("inf")
    unmatched = int(j.select((pl.col("p").is_null() | pl.col("q").is_null()).sum()).item())
    n = {int(s): int((mine["season"] == s).sum()) for s in C.VERDICT_SEASONS}
    ok = (
        diff <= C.REPRO_TOL
        and unmatched == 0
        and all(n[s] == C.REPRO_N[s] for s in C.VERDICT_SEASONS)
    )
    chk = check(
        "R22 reproduces stored RECENT_ROLE p_R",
        {"max_abs_diff": diff, "unmatched_rows": unmatched, "n": n},
        f"<= {C.REPRO_TOL}, n {C.REPRO_N}",
        ok,
    )
    return chk, {k: res[k] for k in ("gid", "pid", "season", "p_R")}


def age_check(built: Built) -> dict[str, Any]:
    """R4: no NULL or NaN ``age`` on any training row of either frame (player 1626218's rows are in neither on the real data)."""
    bad = pl.col("age").is_null() | pl.col("age").is_nan()
    n = {k: int(f.filter(bad).height) for k, f in (("p", built.p), ("m", built.m))}
    return check(
        "no NULL or NaN age on any training row (player 1626218's rows excluded)",
        n,
        "0 on both frames",
        sum(n.values()) == 0,
    )


def run_gate(
    world: dict[str, Any],
    built: Built,
    *,
    slate_fn: SlateFn | None = None,
    parity: bool = True,
    repro: bool = True,
    flagged_parity: bool = True,
    planted: bool = True,
    db: str = DB,
    min_rows: int = 25000,
    min_games: int = 1000,
    manifest_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The run-time gates and the data-dependent guards G1 and G2 (stop before scoring; a stop is not a result)."""
    tests: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    tests.append(holdout_gate({"p": built.p, "m2": built.m, "games": world["games_full"]}))
    tests += era_and_report_gate(built.p, built.m, built.m_report_cols)
    counts = {int(s): int((built.p["season"] == s).sum()) for s in C.SCORED_SEASONS}
    games_n = {
        int(s): int(built.p.filter(pl.col("season") == s)["game_id"].n_unique())
        for s in C.SCORED_SEASONS
    }
    tests.append(
        check(
            "Cell-P rows and games per scored season (union recount)",
            {"rows": counts, "games": games_n},
            f"verdict seasons rows >= {min_rows:,}, games >= {min_games:,}",
            all(counts[s] >= min_rows and games_n[s] >= min_games for s in C.VERDICT_SEASONS),
        )
    )
    tests.append(age_check(built))
    if planted:
        g1 = planted_future_gate(world, built)
        detail["g1"] = g1
        tests.append(
            check(
                "G1 planted future (P0, P0c maps, R19 and M2_19 inputs) + non-inert control",
                {"cutoff": g1["cutoff"]},
                "future identical, control changes",
                g1["ok"],
            )
        )
    g2p = C.g2_missingness(built.p, r_columns() + ["p0", "mu0", "sigma0"], dnp_bucket=True)
    g2m = C.g2_missingness(
        built.m,
        m_inputs(),
        dnp_bucket=False,
        minutes_col="minutes",
        extra_causes={
            c: "report-derived (derived from the OUT sets): NULL when the game has no usable report (has_report = 0) and on every 2021-22 row"
            for c in built.m_report_cols
        },
    )
    detail["g2_p"], detail["g2_m2"] = g2p, g2m
    tests.append(
        check(
            "G2 missingness beyond the as-of cause (P(play) inputs)",
            {
                "unexplained": g2p["unexplained_null_inputs"],
                "worst": [
                    (p["season"], round(p["max_gap_pp"], 2), p["worst_col"])
                    for p in g2p["per_season"]
                ],
            },
            "max(2 pp, 3 x permuted gap) per season; every NULL input has a listed cause",
            g2p["ok"],
        )
    )
    tests.append(
        check(
            "G2 missingness beyond the as-of cause (minutes head inputs)",
            {"unexplained": g2m["unexplained_null_inputs"]},
            "same",
            g2m["ok"],
        )
    )
    r22: dict[str, np.ndarray] = {}
    if flagged_parity:
        tests.append(flagged_parity_gate(world, db))
    if parity:
        tests.append(parity_gate(world, built, slate_fn))
    if repro:
        chk, r22 = repro_gate(manifest_ctx=manifest_ctx)
        tests.append(chk)
    gate = {"tests": tests, "gate_ok": all(t["ok"] for t in tests), "detail": detail}
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    gate["_r22"] = r22
    return gate


# --------------------------------------------------------------------------- arms
def run_arms(
    built: Built,
    cfg: ContextResidualConfig,
    *,
    ckpt: Path | None = None,
    min_train: int = C.MIN_TRAIN,
    n_estimators: int | None = None,
    seasons: tuple[int, ...] = C.SCORED_SEASONS,
    manifest_ctx: dict[str, Any] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    arms = arm_sets()
    return C.walk_p(
        built.p,
        arms,
        cfg,
        seasons,
        ckpt=ckpt,
        min_train=min_train,
        n_estimators=n_estimators,
        meta=C.META_COLS,
        manifest_ctx=manifest_ctx,
    )


def run_g5(
    built: Built,
    cfg: ContextResidualConfig,
    observed: dict[int, float],
    *,
    k: int = C.G5_K,
    ckpt: Path | None = None,
    min_train: int = C.MIN_TRAIN,
    n_estimators: int | None = None,
    manifest_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """G5 permutation null: seeds 1..K; each permutes the training labels once per block (one global permutation shared by R19 and P0c's map), scored on real
    labels; s_k = LL(R_shuf) - LL(P0c_shuf) per verdict season. Per-permutation checkpoints."""
    sk: dict[int, list[float]] = {s: [] for s in C.VERDICT_SEASONS}
    arms = {"R19": arm_sets()["R19"]}
    mid = ""
    if ckpt is not None:
        mid = C.guard_cache(
            ckpt,
            {
                **(manifest_ctx or {}),
                "kind": "g5",
                "label_hash": C.frame_hash(built.p, ["played"]),
                "input_hash": C.frame_hash(built.p, [*arms["R19"], "p0", "era_any"]),
                "arms": arms,
                "seasons": list(C.VERDICT_SEASONS),
                "min_train": min_train,
                "n_estimators": n_estimators,
                "lgb_threads": C.LGB_THREADS,
            },
        )
    for seed in range(1, k + 1):
        path = None if ckpt is None else ckpt / f"perm_{seed:03d}.json"
        if path is not None and path.exists():
            rec = json.loads(path.read_text())
            if rec.get("_manifest") != mid or rec.get("_seed") != seed:
                raise RuntimeError(
                    f"checkpoint {path.name}: not written under this run's manifest/seed: refusing to resume"
                )
        else:
            res, _ = C.walk_p(
                built.p,
                arms,
                cfg,
                C.VERDICT_SEASONS,
                shuffle_seed=seed,
                ckpt=None,
                min_train=min_train,
                n_estimators=n_estimators,
            )
            rec = {}
            for s in C.VERDICT_SEASONS:
                m = res["season"] == s
                rec[str(s)] = float(
                    C.ll_rows(res["p_R19"][m], res["y"][m]).mean()
                    - C.ll_rows(res["p_P0c"][m], res["y"][m]).mean()
                )
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({**rec, "_manifest": mid, "_seed": seed}))
        for s in C.VERDICT_SEASONS:
            sk[s].append(rec[str(s)])
        C._log(
            f"G5 permutation {seed}/{k}: "
            + ", ".join(f"{s}: {rec[str(s)]:+.5f}" for s in C.VERDICT_SEASONS)
        )
    rows = C.g5_decide(observed, {s: np.array(v) for s, v in sk.items()})
    return {
        "rows": rows,
        "ok": all(r["ok"] for r in rows),
        "s_k": {str(s): v for s, v in sk.items()},
    }


def observed_d1(res: dict[str, np.ndarray]) -> dict[int, float]:
    out = {}
    for s in C.VERDICT_SEASONS:
        d, _ = C.pair_delta(res, s, "R19", "P0c")
        out[s] = float(d.mean())
    return out


# --------------------------------------------------------------------------- secondaries
def secondary_rows(
    res: dict[str, np.ndarray], r22: dict[str, np.ndarray] | None
) -> list[dict[str, Any]]:
    """Descriptive: calibration share (P0c - P0), decomposition (P0c -> C19 -> C'19 -> R19), history-window effect (R19 - R22 on common rows)."""
    rows: list[dict[str, Any]] = []
    pairs = [
        ("P0c-P0", "P0c", "P0"),
        ("C19-P0c", "C19", "P0c"),
        ("Cp19-C19", "Cp19", "C19"),
        ("R19-Cp19", "R19", "Cp19"),
    ]
    for s in (*C.DESCRIPTIVE_SEASONS, *C.VERDICT_SEASONS):
        for name, a, b in pairs:
            for metric in C.METRICS:
                d, g = C.pair_delta(res, s, a, b, metric)
                rows.append(
                    {"season": s, "contrast": name, "metric": metric, **C.cluster_boot(d, g)}
                )
    if r22 is not None:
        j = pl.DataFrame(
            {
                "gid": res["gid"].astype(str),
                "pid": res["pid"],
                "season": res["season"],
                "date": res["date"],
                "y": res["y"],
                "p19": res["p_R19"],
            }
        ).join(
            pl.DataFrame(
                {
                    "gid": r22["gid"].astype(str),
                    "pid": r22["pid"].astype(np.int64),
                    "p22": r22["p_R"],
                }
            ),
            on=["gid", "pid"],
            how="inner",
        )
        for s in C.VERDICT_SEASONS:
            m = (j["season"] == s).to_numpy()
            y, p19, p22, gid = (j[c].to_numpy()[m] for c in ("y", "p19", "p22", "gid"))
            for metric in C.METRICS:
                d = C.metric_rows(metric, p19, y) - C.metric_rows(metric, p22, y)
                rows.append(
                    {"season": s, "contrast": "R19-R22", "metric": metric, **C.cluster_boot(d, gid)}
                )
            # history-window effect by calendar month (reported only)
            month = (
                j["date"].to_numpy()[m].astype("datetime64[D]").astype("datetime64[M]").astype(str)
            )
            for mo in sorted(set(month.tolist())):
                mm = month == mo
                if mm.sum() >= 50:
                    d = C.ll_rows(p19[mm], y[mm]) - C.ll_rows(p22[mm], y[mm])
                    rows.append(
                        {
                            "season": s,
                            "contrast": f"R19-R22 [{mo}]",
                            "metric": "ll",
                            **C.cluster_boot(d, gid[mm]),
                        }
                    )
    return rows


def m2_walk(
    built: Built,
    cfg: ContextResidualConfig,
    *,
    min_train: int = C.MIN_TRAIN,
    ckpt: Path | None = None,
    manifest_ctx: dict[str, Any] | None = None,
) -> dict[str, np.ndarray]:
    """M2_19 (MINUTES_V2's M2 head, monthly refit on the union with the era channels) vs P0's truncated Normal, minutes | plays, on the verdict seasons' Cell-P played rows."""
    from research.props.minutes_v2 import MinutesV2Model, trunc_normal_quantiles

    sf = built.m
    keys = built.p.select(*C.KEYS)
    names = m_inputs()
    if ckpt is not None:
        C.guard_cache(
            ckpt,
            {
                **(manifest_ctx or {}),
                "kind": "m2",
                "label_hash": C.frame_hash(sf, ["minutes"]),
                "input_hash": C.frame_hash(sf, [*names, "p_mu", "p_sigma"]),
                "cell_p_hash": C.frame_hash(built.p, []),
                "names": names,
                "seasons": list(C.VERDICT_SEASONS),
                "min_train": min_train,
            },
        )
    test_all = sf.filter(pl.col("season").is_in(list(C.VERDICT_SEASONS)))
    acc: dict[str, list[np.ndarray]] = {}
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        path = None if ckpt is None else ckpt / f"m2_block_{start}.npz"
        train = sf.filter(pl.col("game_date") < start)
        train_h = C.array_hash(
            train["game_id"].to_numpy().astype(str),
            train["player_id"].to_numpy().astype(np.int64),
            train["minutes"].cast(pl.Float64).to_numpy(),
        )[:16]
        if path is not None and path.exists():
            with np.load(path, allow_pickle=False) as z:
                if "_train_hash" not in z.files or str(z["_train_hash"]) != train_h:
                    raise RuntimeError(
                        f"checkpoint {path.name}: stored training-label hash differs from this run's: refusing to resume"
                    )
                for k in z.files:
                    if not k.startswith("_"):
                        acc.setdefault(k, []).append(z[k])
            C._log(f"M2 block {bi + 1}/{len(blocks)} {start} from checkpoint")
            continue
        block = sf.filter(
            (pl.col("game_date") >= start)
            & (pl.col("game_date") < end)
            & pl.col("season").is_in(list(C.VERDICT_SEASONS))
        )
        if block.is_empty() or train.height < min_train:
            continue
        tst = block.join(keys, on=C.KEYS, how="inner")
        if tst.is_empty():
            continue
        model = MinutesV2Model(names, "mixture", cfg).fit(train)
        q2 = model.quantiles(tst)
        q0 = trunc_normal_quantiles(tst["p_mu"].to_numpy(), tst["p_sigma"].to_numpy())
        y = tst["minutes"].cast(pl.Float64).to_numpy()
        part: dict[str, np.ndarray] = {
            "gid": tst["game_id"].to_numpy().astype(str),
            "pid": tst["player_id"].to_numpy().astype(np.int64),
            "season": tst["season"].to_numpy().astype(np.int64),
            "y": y,
        }
        for arm, q in (("M2", q2), ("P0", q0)):
            for k, v in MV.score_minutes(q, y).items():
                part[f"{arm}_{k}"] = np.asarray(v)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, **part, _train_hash=np.array(train_h))
        for k, v in part.items():
            acc.setdefault(k, []).append(v)
        C._log(f"M2 block {bi + 1}/{len(blocks)} {start} n_test={tst.height}")
    if not acc:
        raise ValueError("no minutes block had enough training rows")
    return {k: np.concatenate(v) for k, v in acc.items()}


def minutes_report(z: dict[str, np.ndarray]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in C.VERDICT_SEASONS:
        m = z["season"] == s
        d = z["M2_crps"][m] - z["P0_crps"][m]
        ci = C.cluster_boot(d, z["gid"][m])
        pit = z["P0_pit"][m]
        out[str(s)] = {
            "M2_minus_P0_crps": ci,
            "beaten": bool(ci["point"] <= C.M2_FLOOR and ci["hi"] < 0),
            "P0_bias": C.cluster_boot(z["P0_mean"][m] - z["y"][m], z["gid"][m]),
            "P0_coverage80": float(
                np.mean((z["y"][m] >= z["P0_q10"][m]) & (z["y"][m] <= z["P0_q90"][m]))
            ),
            "P0_pit_deciles": np.histogram(pit, bins=10, range=(0, 1))[0].tolist(),
            "n": int(m.sum()),
        }
    return out


def props_descriptive(res: dict[str, np.ndarray], frame: pl.DataFrame) -> dict[str, Any]:
    """Unconditional integer CRPS of pts / reb / ast / fg3m for R19, P0c, P0 on the stored A0 conditional grids (the 2022-start frame's), DNP rows on the
    PPLAY_INTO_PROPS_LIVE proxy (the player's most recent earlier played row's grid). Descriptive only; nothing here reaches the verdict."""
    from research.eval import workload_eval as WE
    from research.eval.pplay_into_props_live import row_delta

    q = pl.DataFrame(
        {
            "game_id": res["gid"],
            "player_id": res["pid"],
            "season": res["season"],
            "p_R19": res["p_R19"],
            "p_P0c": res["p_P0c"],
            "p_P0": res["p_P0"],
        }
    ).join(frame.select(*C.KEYS, "game_date", "played"), on=C.KEYS, how="left")
    pic = WE.load_pic()
    out: dict[str, Any] = {}
    for stat in STATS:
        a0 = WE.a0_arm(stat, pic)
        for s in C.VERDICT_SEASONS:
            m = a0["season"] == s
            A = pl.DataFrame(
                {
                    "game_id": a0["gid"][m],
                    "player_id": a0["pid"][m],
                    "y": a0["y"][m],
                    "_k": np.arange(int(m.sum())),
                }
            )
            qs = q.filter(pl.col("season") == s).join(A, on=C.KEYS, how="left")
            played = qs.filter(pl.col("_k").is_not_null())
            dnp = qs.filter(pl.col("_k").is_null() & (pl.col("played") < 0.5))
            ref = played.select("player_id", pl.col("game_date").alias("ref_date"), "_k").sort(
                "ref_date"
            )
            dn = dnp.sort("game_date").join_asof(
                ref,
                left_on="game_date",
                right_on="ref_date",
                by="player_id",
                strategy="backward",
                allow_exact_matches=False,
            )
            dn = dn.filter(pl.col("_k_right").is_not_null())
            qi_all = a0["qi"][m]
            qi = np.concatenate(
                [qi_all[played["_k"].to_numpy()], qi_all[dn["_k_right"].to_numpy()]]
            )
            gid = np.concatenate([played["game_id"].to_numpy(), dn["game_id"].to_numpy()]).astype(
                str
            )
            y_u = np.concatenate([played["y"].to_numpy().astype(float), np.zeros(dn.height)])
            ps = {
                a: np.concatenate([played[f"p_{a}"].to_numpy(), dn[f"p_{a}"].to_numpy()])
                for a in ("R19", "P0c", "P0")
            }
            rec: dict[str, Any] = {"n_rows": int(len(y_u)), "n_dnp_proxy": int(dn.height)}
            for name, a, b in (
                ("R19-P0c", "R19", "P0c"),
                ("R19-P0", "R19", "P0"),
                ("P0c-P0", "P0c", "P0"),
            ):
                rec[name] = C.cluster_boot(row_delta(qi, ps[a], ps[b], y_u), gid)
            out[f"{stat}_{s}"] = rec
    return out


# --------------------------------------------------------------------------- orchestration
def analyze(res: dict[str, np.ndarray], g5: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    fam = C.family_table(res)
    g3 = C.g3_dayshuffle(res)
    g4 = C.g4_pastonly(res)
    es = C.ece_and_slices(res)
    guards = {
        "gates_and_g1_g2": bool(gate["gate_ok"]),
        "g3": all(r["ok"] for r in g3),
        "g4": all(r["ok"] for r in g4),
        "g5": bool(g5["ok"]),
        "ece_slices": bool(es["ok"]),
    }
    dec = C.decide(fam, all(guards.values()))
    elig = {}
    for s in C.VERDICT_SEASONS:
        m = res["season"] == s
        for a in ("P0", "P0c"):
            p = res[f"p_{a}"][m]
            hi = p >= 0.5
            elig[f"{a}_{s}"] = {
                "n_ge_0.5": int(hi.sum()),
                "played_rate_ge_0.5": float(res["y"][m][hi].mean()) if hi.any() else None,
                "mean_p_ge_0.5": float(p[hi].mean()) if hi.any() else None,
            }
    return {
        "decision": dec,
        "family": fam,
        "guards": guards,
        "g3": g3,
        "g4": g4,
        "g5": g5,
        "ece_slices": es,
        "eligibility_threshold_table": elig,
        "reliability_slices": {
            f"{a}_{s}_{name}": C.reliability(
                res[f"p_{a}"][mk & (res["season"] == s)], res["y"][mk & (res["season"] == s)]
            )
            for name, mk in C.slice_masks(res).items()
            if name != "all" and mk.any()
            for a in ("P0c", "R19")
            for s in C.VERDICT_SEASONS
            if (mk & (res["season"] == s)).sum() >= C.MIN_SLICE_N
        },
        "reliability": {
            f"{a}_{s}": C.reliability(
                res[f"p_{a}"][res["season"] == s], res["y"][res["season"] == s]
            )
            for a in ("P0", "P0c", "R19")
            for s in C.VERDICT_SEASONS
        },
    }


def _f(x: Any, nd: int = 4) -> str:
    return f"{x:.{nd}f}" if isinstance(x, float | np.floating) else str(x)


def render(results: dict[str, Any]) -> str:
    L = [
        "# PPLAY_MINUTES_VALID results",
        "",
        f"Rule {RULE.ID} {results['rule_sha256'][:8]}, freeze {results['freeze_commit'][:8]}, review {results['review_path']} ({results['reviewed_commit'][:8]})",
        "",
        f"**Verdict: {results['decision']['verdict']}**",
        "",
    ]
    L += [
        "| season | contrast | metric | n | games | point | 95% CI | p | p_BH |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results["family"]:
        L.append(
            f"| {r['season']} | {r['contrast']} | {r['metric']} | {r['n']} | {r['n_games']} | {_f(r['point'], 5)} | [{_f(r['lo'], 5)}, {_f(r['hi'], 5)}] | {_f(r['p'], 4)} | {_f(r['p_bh'], 4)} |"
        )
    L += ["", f"Guards: {json.dumps(results['guards'])}", "", "## Gate", ""]
    for t in results["gate"]["tests"]:
        L.append(f"* {'PASS' if t['ok'] else 'FAIL'} {t['test']}: {t['value']} ({t['threshold']})")
    L += [
        "",
        "## Secondaries (descriptive)",
        "",
        "| season | contrast | metric | n | point | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for r in results["secondary"]:
        L.append(
            f"| {r['season']} | {r['contrast']} | {r['metric']} | {r['n']} | {_f(r['point'], 5)} | [{_f(r['lo'], 5)}, {_f(r['hi'], 5)}] |"
        )
    L += [
        "",
        "## Minutes (M2_19 - P0, CRPS)",
        "",
        f"{json.dumps(results.get('minutes'), default=float)}",
        "",
        "## Data",
        "",
        f"{json.dumps(results['stamp'], default=str)}",
    ]
    return "\n".join(L) + "\n"


def _json(o: Any) -> Any:
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set | tuple):
        return list(o)
    if isinstance(o, dt.date):
        return str(o)
    return str(o)


def cache_ctx(stamp: dict[str, Any], gate_info: dict[str, Any]) -> dict[str, Any]:
    """F6: what every checkpoint directory is keyed by besides its own labels and inputs: the rule sha, the data versions and the sha of the reviewed code's git blobs
    (a function of the code commit; a docs-only commit does not invalidate a resume)."""
    return {
        "rule_sha256": gate_info.get("rule_sha256"),
        "data_version_production": stamp.get("data_version_production"),
        "data_version_history": stamp.get("data_version_history"),
        "code_sha": gate_info.get("code_sha"),
    }


def pipeline(
    world: dict[str, Any],
    cfg: ContextResidualConfig,
    stamp: dict[str, Any],
    gate_info: dict[str, Any],
    *,
    out_dir: Path,
    cache: Path,
    min_train: int = C.MIN_TRAIN,
    n_estimators: int | None = None,
    k: int = C.G5_K,
    gate_kwargs: dict[str, Any] | None = None,
    slate_fn: SlateFn | None = None,
) -> dict[str, Any]:
    """Build -> gate (stop on failure) -> arms -> guards -> G5 -> secondaries. ``gate_info`` carries the rule/review stamp."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    C._log("building the union frames")
    built = build_all(world)
    C._log(f"frames: P {built.p.height} rows, M2 {built.m.height} rows ({time.time() - t0:.0f} s)")
    ctx = cache_ctx(stamp, gate_info)
    gate = run_gate(world, built, slate_fn=slate_fn, **{"manifest_ctx": ctx, **(gate_kwargs or {})})
    r22 = gate.pop("_r22", {})
    (out_dir / "gate.json").write_text(
        json.dumps({k_: v for k_, v in gate.items()}, indent=2, default=_json)
    )
    if not gate["gate_ok"]:
        raise RuntimeError(
            "a run-time gate or data guard failed: NO VERDICT (see gate.json); fix plumbing only, the rule does not change"
        )
    res, info = run_arms(
        built,
        cfg,
        ckpt=cache / "arms",
        min_train=min_train,
        n_estimators=n_estimators,
        manifest_ctx=ctx,
    )
    np.savez_compressed(out_dir / "scores.npz", **res)
    g5 = run_g5(
        built,
        cfg,
        observed_d1(res),
        k=k,
        ckpt=cache / "g5",
        min_train=min_train,
        n_estimators=n_estimators,
        manifest_ctx=ctx,
    )
    results = analyze(res, g5, gate)
    results["secondary"] = secondary_rows(res, r22 or None)
    mz = m2_walk(built, cfg, min_train=min_train, ckpt=cache / "m2", manifest_ctx=ctx)
    results["minutes"] = minutes_report(mz)
    if "p_P0p" in res:
        results["lineage_P0p_logloss"] = {
            str(s): float(
                C.ll_rows(res["p_P0p"][res["season"] == s], res["y"][res["season"] == s]).mean()
            )
            for s in C.VERDICT_SEASONS
        }
    try:  # descriptive only: depends on stored artifacts; a failure here is recorded, never a reason to lose the verdict
        results["props"] = props_descriptive(res, built.p)
    except Exception as exc:  # noqa: BLE001
        results["props"] = {"error": repr(exc)}
        C._log(f"props descriptive failed (recorded): {exc!r}")
    results["platt_maps"] = info["platt"]
    results.update({**gate_info, "gate": gate, "stamp": stamp, "seconds": time.time() - t0})
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=_json))
    return results


def _cfg() -> ContextResidualConfig:
    return PC._load_cfg()


def _preload_lazy_modules() -> None:
    """R1: the modules the harness imports lazily (forward, minutes_v2, workload_eval) load now, after the lock is held and before the re-check."""
    import nba.props.forward  # noqa: F401
    import research.eval.workload_eval  # noqa: F401
    import research.props.minutes_v2  # noqa: F401


def review_and_stamp(accept: str | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(review, data stamp, gate_info): the loader review verdict, the data versions and the code stamp (R2: git blob shas of every reviewed file)."""
    rv = RULE.require_review()
    stamp = data_stamp(accept=accept)
    blobs = RULE.module_blobs()
    info = {**rv, "module_blobs": blobs, "code_sha": RULE.code_sha(blobs)}
    return rv, stamp, info


def run_real(sha: str | None, accept: str | None, gate_only: bool = False) -> dict[str, Any]:
    rule_sha, fc = RULE.require_frozen(sha)
    review_and_stamp(accept)  # fail fast, before waiting for the lock
    with HeavyLock():
        _preload_lazy_modules()
        # R1: the wait for heavy.lock can be long; a commit made during it must be seen, so the review gate and the data stamp are taken again now
        _, stamp, gate_info = review_and_stamp(accept)
        gate_info = {**gate_info, "rule_sha256": rule_sha, "freeze_commit": fc}
        world = make_world()
        cfg = _cfg()
        if gate_only:
            built = build_all(world)
            gate = run_gate(world, built, manifest_ctx=cache_ctx(stamp, gate_info))
            gate.pop("_r22", None)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / "gate.json").write_text(
                json.dumps({**gate, "stamp": stamp, "code": gate_info}, indent=2, default=_json)
            )
            return gate
        results = pipeline(world, cfg, stamp, gate_info, out_dir=OUT_DIR, cache=CACHE)
    REPORT_MD.write_text(render(results))
    return results


# --------------------------------------------------------------------------- synthetic smoke (CPU, seconds; no real data)
def synthetic_world(seed: int = 0, n_days: int = 36, per_team: int = 10) -> dict[str, Any]:
    """A tiny world with the real table shapes: 8 real team ids, seasons 2019-2024, ~160 games a season, a history/modern split, report snapshots (hourly in 2021-22),
    era flags from the production rule, and player 1626218 with no birth date in 2019."""
    from nba.features.game_context import TEAM_CONFERENCE
    from nba.ingest.era_flags import compute_era_flags

    rng = np.random.default_rng(seed)
    teams = list(TEAM_CONFERENCE)[:8]
    games: list[dict[str, Any]] = []
    box: list[dict[str, Any]] = []
    snaps: list[dict[str, Any]] = []
    roster = {t: [t * 100 + j for j in range(per_team)] for t in teams}
    roster[teams[0]][0] = C.EXCLUDED_PLAYER
    gnum = 0
    for season in C.TRAIN_SEASONS:
        start = dt.date(season, 10, 22)
        for d in range(n_days):
            day = start + dt.timedelta(days=int(d * 3 + (d % 2)))
            order = rng.permutation(teams)
            for gi in range(4):
                home, away = int(order[2 * gi]), int(order[2 * gi + 1])
                gnum += 1
                gid = f"002{season % 100:02d}{gnum:05d}"
                games.append(
                    {
                        "game_id": gid,
                        "game_date": day,
                        "season": season,
                        "home_team": home,
                        "away_team": away,
                        "home_pts": int(rng.integers(90, 125)),
                        "away_pts": int(rng.integers(90, 125)),
                        "national_tv": "ESPN" if gnum % 4 == 0 else None,
                    }
                )
                tip = dt.datetime(day.year, day.month, day.day, 19, 0)
                for hh in range(
                    4 if season == C.REPORT_DROPPED_SEASON else 1
                ):  # every game has a (non-informative) report
                    snaps.append(
                        {
                            "game_id": gid,
                            "player_id": 999,
                            "status": "available",
                            "as_of": tip - dt.timedelta(hours=6 - hh * 1.5),
                            "game_date": day,
                            "tip_et": tip,
                        }
                    )
                for t in (home, away):
                    for j, pid in enumerate(roster[t]):
                        p_play = 0.97 if j < 5 else (0.8 if j < 8 else 0.55)
                        played = rng.random() < p_play
                        mins = float(np.clip(rng.normal(32 - 3 * j, 5), 4, 46)) if played else None
                        box.append(
                            {
                                "game_id": gid,
                                "player_id": pid,
                                "team_id": t,
                                "minutes": mins,
                                "starter": j < 5 and played,
                                "pts": int(rng.poisson(9)) if played else None,
                                "reb": int(rng.poisson(4)) if played else None,
                                "ast": int(rng.poisson(3)) if played else None,
                                "fg3m": int(rng.poisson(1)) if played else None,
                                "tov": int(rng.poisson(1)) if played else None,
                                "pf": int(rng.poisson(2)) if played else None,
                            }
                        )
                        if not played and rng.random() < 0.6:
                            n_snap = 4 if season == C.REPORT_DROPPED_SEASON else 1
                            for hh in range(n_snap):
                                lead_h = 6 - hh * 1.5
                                snaps.append(
                                    {
                                        "game_id": gid,
                                        "player_id": pid,
                                        "status": "out" if hh >= 0 else "questionable",
                                        "as_of": tip - dt.timedelta(hours=lead_h),
                                        "game_date": day,
                                        "tip_et": tip,
                                    }
                                )
                        elif played and rng.random() < 0.05:
                            snaps.append(
                                {
                                    "game_id": gid,
                                    "player_id": pid,
                                    "status": "questionable",
                                    "as_of": tip - dt.timedelta(hours=3),
                                    "game_date": day,
                                    "tip_et": tip,
                                }
                            )
    g = pl.DataFrame(games).with_columns(pl.col("game_date").cast(pl.Date))
    bx = pl.DataFrame(
        box,
        schema_overrides={
            "minutes": pl.Float64,
            "pts": pl.Int64,
            "reb": pl.Int64,
            "ast": pl.Int64,
            "fg3m": pl.Int64,
            "tov": pl.Int64,
            "pf": pl.Int64,
        },
    )
    pids = sorted({r["player_id"] for r in box})
    static = pl.DataFrame(
        {
            "player_id": pids,
            "position": ["G" if p % 3 == 0 else ("F" if p % 3 == 1 else "C") for p in pids],
            "birth_date": [
                None if p == C.EXCLUDED_PLAYER else dt.date(1990 + p % 10, 1 + p % 12, 1 + p % 27)
                for p in pids
            ],
            "draft_year": [2010 + p % 8 for p in pids],
        }
    ).with_columns(pl.col("birth_date").cast(pl.Date))
    flags = compute_era_flags(g.select("game_id", "game_date", "season", "home_team"))
    import yaml

    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    sn = pl.DataFrame(snaps, schema_overrides={"player_id": pl.Int64}).with_columns(
        pl.col("game_date").cast(pl.Date),
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("tip_et").cast(pl.Datetime("us")),
    )
    return {
        "games_full": g,
        "pgs_full": bx,
        "static": static,
        "team_context": pl.DataFrame(schema={"game_id": pl.Utf8, "team_id": pl.Int64}),
        "snaps": sn,
        "flags": flags.select("game_id", "season", *HW.FLAG_COLS),
        "cfg": cfg,
        "elo_params": {
            k: float(v)
            for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
        },
        "hist_ids": set(g.filter(pl.col("season") <= 2021)["game_id"].to_list()),
    }


SMOKE_CFG = ContextResidualConfig(
    n_estimators=12, min_child_samples=10, min_cal_rows=100, num_leaves=7
)


def smoke(out_dir: Path | None = None) -> dict[str, Any]:
    """Every stage on the synthetic world with tiny budgets: frames, G1, G2, arms, G3-G5 (K = 3), secondaries minus the stored-artifact checks. Not a result."""
    world = synthetic_world()
    out = out_dir or Path(tempfile.mkdtemp(prefix="pplay_minutes_valid_smoke_"))
    built = build_all(world)
    res, _ = run_arms(built, SMOKE_CFG, min_train=300, n_estimators=12)
    g5 = run_g5(built, SMOKE_CFG, observed_d1(res), k=3, min_train=300, n_estimators=12)
    gate = {"gate_ok": True, "tests": []}
    results = analyze(res, g5, gate)
    results["secondary"] = secondary_rows(res, None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "smoke.json").write_text(json.dumps(results, indent=2, default=_json))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PPLAY_MINUTES_VALID harness")
    ap.add_argument("cmd", choices=("check", "smoke", "gate", "run", "report"))
    ap.add_argument("--sha", default=None)
    ap.add_argument("--accept-data-version", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        print(json.dumps(RULE.require_review(), indent=2))
        return 0
    if a.cmd == "smoke":
        r = smoke()
        print("smoke verdict (synthetic, not a result):", r["decision"])
        return 0
    if a.cmd == "report":
        RULE.require_frozen(a.sha)
        RULE.require_review()
        res = json.loads((OUT_DIR / "results.json").read_text())
        REPORT_MD.write_text(render(res))
        return 0
    if not a.sha:
        raise SystemExit("--sha <64 hex> is required")
    out = run_real(a.sha, a.accept_data_version, gate_only=a.cmd == "gate")
    if a.cmd == "run":
        print(out["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
