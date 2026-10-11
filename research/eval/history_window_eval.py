# ruff: noqa: E501
"""Training-window extension (2019-20 .. 2021-22) under the FROZEN rule ``docs/HISTORY_WINDOW.md``.

    python -W ignore -m research.eval.history_window_eval gate  --sha <64 hex>   # G0.1-G0.5 + coverage audit (G2/G3)
    python -W ignore -m research.eval.history_window_eval smoke --sha <64 hex>   # one month block, pts, all arms
    python -W ignore -m research.eval.history_window_eval run   --sha <64 hex>   # G0.6 + arms W0..W3 (+W1r) + H2
    python -W ignore -m research.eval.history_window_eval report --sha <64 hex>  # reports/history_window.md

Common flags: ``--db nba.duckdb --history-db data/history/nba_history.duckdb --doc docs/HISTORY_WINDOW.md``.
Both databases are opened ``read_only=True`` (never written); every query carries ``season <= 2024``
(the history DB is further limited to 2019-2021) so season 2025 is never loaded.

Order is enforced in code. ``--sha`` must equal the frozen-section hash computed from ``--doc`` AND the
module constant AND the value recorded in the ledger notice (2026-10-09); otherwise the program refuses.
``gate`` writes ``gate.json`` and ``audit.json`` and logs the audit's sha256; ``run`` refuses unless the
gate passed, the audit file still hashes to the logged value, and ``--spot-check-done`` was given (the
20-date PDF spot check of audit item 4 is manual). ``run`` fits W0 on pts first and STOPs if it does not
reproduce production pts integer CRPS 3.1528 / 3.1884 to 1e-4 (G0.6). No arm is fitted after any STOP.

Research module only: ``nba/`` is untouched. The production props harness is reused as written
(``build_features``, ``ContextResidualModel`` fit semantics, ``raw_quantiles``, ``arm_scores``,
``month_blocks``, hyper-parameters, seeds); the only new fit path is a ``sample_weight`` subclass used
by W2, which falls through to the production ``fit`` whenever no weight column is present.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import random
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl
import yaml
from sklearn.metrics import roc_auc_score

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    bh_adjust,
    boot_p_value,
    month_blocks,
)
from nba.eval.injury_elo_eval import walk_forward_probs
from nba.eval.lower_tail_eval import LOW_THRESHOLDS, arm_scores
from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
from nba.features.injury_report import (
    OFFICIAL_REPORT_SOURCE,
    latest_pretip_flagged,
    usable_report_rows,
)
from nba.ingest.era_flags import compute_era_flags
from nba.models.injury_elo import (
    build_injury_features,
    feature_config_from,
    sequential_elo_logits,
)
from nba.parse.rebuild_lineups import heavy_lock
from nba.parse.reconcile import team_box_from_player_game_stats
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _params,
    build_features,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from nba.props.metrics import paired_score_delta_ci

ROOT = Path(__file__).resolve().parents[2]
DOC = Path("docs/HISTORY_WINDOW.md")
LEDGER = ROOT / "docs" / "TEST_LEDGER.md"
MARKER = "=== RESULTS BELOW ==="
#: sha256 of the frozen section, recorded in the ledger notice of 2026-10-09 (prefix 9a1090be).
FROZEN_SHA = "9a1090be94f69d755e9d0e9bd7c56c07cbc677a9aa2e9a9659ad92208d1f164a"
OUT_DIR = Path("reports/history_window")
REPORT_MD = Path("reports/history_window.md")
CACHE = Path("data/history_window")
HIST_SCHEDULE = Path("data/history/schedule")
HIST_GAMES = Path("data/history/games")
MANIFESTS = Path("data/history/availability_backfill")
HIST_LOG = Path("data/history/history_injury_fetch.log")
ERA_FLAGS_PY = ROOT / "nba" / "ingest" / "era_flags.py"

# --- frozen constants (docs/HISTORY_WINDOW.md)
HIST_SEASONS: tuple[int, ...] = (2019, 2020, 2021)
CUR_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
ALL_SEASONS: tuple[int, ...] = HIST_SEASONS + CUR_SEASONS
SELECT_SEASON, REPORT_SEASON = 2023, 2024
TEST_SEASONS: tuple[int, ...] = (SELECT_SEASON, REPORT_SEASON)
CUR_WINDOW_START = dt.date(2022, 10, 18)
FLAG_COLS: tuple[str, ...] = ("covid_bubble", "no_fans", "limited_fans", "shortened_season")
FLAG_WEIGHT = 0.5
ARMS: tuple[str, ...] = ("W0", "W1", "W2", "W3", "W1r")
FAMILY_ARMS: tuple[str, ...] = ("W1", "W2")
N_BOOT = 2000
FLOOR = -0.005
HARM = 0.005
BIAS_TOL = 0.5
COV_LO, COV_HI = 0.75, 0.85
SLICE_MIN_N = 300
SLICE_TOL = 0.01
MIN_TRAIN = 1500  # hard-coded in the production month-block loop
REPRO_PTS = {2023: 3.1528, 2024: 3.1884}
REPRO_TOL = 1e-4
H2_FLOOR = -0.002
# gate thresholds
G01_GAMES_BOX = 0.995
G01_FIELDS = 0.99
G01_DNP_PP = 0.03
G03_MAE = 3.0
G04_NAME_PP = 0.01
G05_TIPS = 0.99
G2_COV_PP = 0.02
G3_GAP_PP = 0.05
G3_PHI = 0.05
G3_AUC = 0.75
MANIFEST_DATES = 707
REPORT_COLS: tuple[str, ...] = (
    "has_report",
    "n_out_rot",
    "vac_min",
    "vac_pts",
    "vac_reb",
    "vac_ast",
    "vac_fg3m",
    "since_flag",
    "flag_dir",
    "gap_days",
)
PROD_PGS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "starter",
)
PGS_SQL = (
    "s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter, "
    "s.stl, s.blk, s.tov, s.pf, s.fta, s.fga, s.oreb"
)

#: Choices the frozen document leaves open; fixed BEFORE any arm is fitted and printed in the report.
IMPLEMENTATION_CHOICES: tuple[str, ...] = (
    "Outputs: the rule names no outputs section; used reports/history_window/ (gate.json, audit.json, "
    "results.json, STOP.json), reports/history_window.md and the resumable cache data/history_window/.",
    "Tip-off times: current seasons via nba.features.game_tipoff.build_game_tipoff() (data/schedule), history "
    "seasons via build_game_tipoff(data/history/schedule); union keeps the production row on overlap. History "
    "report rows of a game with no real tip are DROPPED (no proxy fallback); current rows keep the production "
    "behaviour. The extended flagged sets are asserted equal to W0's on every current game.",
    "Era flags are read from game_era_flags of the DB the game lives in (both DBs, read-only), validated "
    "against nba.ingest.era_flags.compute_era_flags, and asserted all-False on 2022+ rows.",
    "Verdict: an arm passes when ONE stat passes on BOTH 2023 and 2024 (same-stat reading); the looser "
    "'any stat each season' reading is reported descriptively.",
    "Coverage audit definitions: games = games with box rows; usable snapshot = report row with as_of <= real "
    "tip - 60 min; snapshots/game = distinct usable as_of per game (mean over all games and over covered games); "
    "median lead = median over covered games of (tip - latest usable as_of) in minutes.",
    "Audit item 3 NULL pattern: report-derived features (n_out_rot, vac_*) are NULL iff the game has no usable "
    "report (identical to has_report == 0); gap_days is NULL iff the player has no earlier played game in the "
    "extended window; since_flag and flag_dir are never NULL (phi undefined). phi is taken against 'minutes IS "
    "NULL' on rostered rows; each history season is compared with the pooled 2022-24 value.",
    "Audit item 3 distinguishability test: LightGBM classifier (100 trees, 15 leaves, seed 0) on the report "
    "columns of eligible rows, history (2019-21) vs current (2022-24), game-level split (hash % 3 == 0 held out). "
    "Sign test: Pearson correlation of each report column with the residual label of each stat in each era; a "
    "sign difference counts only when both |r| exceed 2/sqrt(n) (noise floor).",
    "G0.1 expected game list = game_id of data/history/games/*.parquet. G0.3 uses the production estimate "
    "FGA + 0.44 FTA + TOV - OREB against the history DB possessions table, per season. G0.4 'done' = at least "
    "one official-report row on the manifest date; the 20-date PDF spot check is manual (--spot-check-done).",
    "True rookie = first played season in the union history equals the evaluated season and draft_year is "
    "null or >= season - 1. n_prior < 20 and n_out_rot slices use the CURRENT-window (W0) values, identical "
    "across arms; the <82-games slice uses the union-history count.",
    "W1r drops history training rows (season <= 2021) whose game has no usable report; it is fitted only when "
    "the audit finds a history season's game-level coverage more than 2 pp from the pooled 2022-24 value.",
    "H2: month-block walk-forward of nba.eval.injury_elo_eval.walk_forward_probs (ridge 5.0, min_signal 150, "
    "configs/injury_elo.yaml) on each window's own Elo replay; scored per game on 2023 and 2024; "
    "nba.features.game_tipoff.build_game_tipoff is patched for the call so history games gate on real tips.",
)


class Stop(RuntimeError):
    """A frozen STOP / kill condition fired: no further arm is fitted."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"STOP[{code}]: {detail}")
        self.code = code
        self.detail = detail


def _num(x: Any) -> float:
    return float("nan") if x is None else float(x)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- hash / freeze


def frozen_sha256(path: Path = DOC) -> str:
    """sha256 of the lines above ``=== RESULTS BELOW ===`` (python twin of the awk + shasum recipe)."""
    keep: list[str] = []
    for line in path.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line if line.endswith("\n") else line + "\n")
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def ledger_recorded_sha(ledger: Path = LEDGER) -> str | None:
    if not ledger.exists():
        return None
    for line in ledger.read_text().splitlines():
        m = re.search(
            r"HISTORY_WINDOW\.md\]\(HISTORY_WINDOW\.md\) frozen-section sha256 `([0-9a-f]{64})`",
            line,
        )
        if m:
            return m.group(1)
    return None


def require_frozen(
    doc: Path, sha: str, expected: str = FROZEN_SHA, ledger: Path | None = LEDGER
) -> str:
    """Refuse (SystemExit) unless ``sha`` == hash of ``doc``'s frozen section == ``expected`` == ledger value."""
    cur = frozen_sha256(doc)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SystemExit("REFUSED: --sha must be the full 64-hex frozen-section hash")
    if cur != sha:
        raise SystemExit(f"REFUSED: frozen-section sha256 of {doc} is {cur}, --sha given {sha}")
    if cur != expected:
        raise SystemExit(f"REFUSED: frozen-section sha256 {cur} != module constant {expected}")
    rec = ledger_recorded_sha(ledger) if ledger is not None else None
    if rec is not None and rec != cur:
        raise SystemExit(f"REFUSED: ledger records {rec}, document hashes to {cur}")
    return cur


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_hash(gid: np.ndarray, pid: np.ndarray) -> str:
    """Order-independent hash of a key set."""
    lines = sorted(f"{g}:{p}" for g, p in zip(gid.tolist(), pid.tolist(), strict=True))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# --------------------------------------------------------------------------- data


def _ro(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Read-only connection (the history DB can be write-locked by the ingest queue: bounded retry)."""
    from nba.db.connect import connect_with_retry

    return connect_with_retry(path, read_only=True)


def assert_no_holdout(df: pl.DataFrame, col: str = "season") -> None:
    if df.height and int(df[col].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 is the frozen holdout and must never be loaded")


FLAG_SCHEMA = {
    "game_id": pl.Utf8,
    "season": pl.Int64,
    **{c: pl.Boolean for c in FLAG_COLS},
}


def load_side(path: str | Path, seasons: tuple[int, ...], current: bool) -> dict[str, pl.DataFrame]:
    """One database, read-only, ``season IN seasons`` and ``<= 2024``: games, pgs, static, avail, flags."""
    if max(seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    ss = ",".join(str(int(s)) for s in seasons)
    con = _ro(path)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season IN ({ss}) AND season <= {MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            f"SELECT {PGS_SQL} FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season IN ({ss}) AND g.season <= {MAX_SEASON}"
        ).pl()
        static = con.execute(
            "SELECT player_id, position, birth_date, draft_year FROM players_static"
        ).pl()
        bound = " AND as_of < TIMESTAMP '2025-07-01'" if current else ""
        avail = con.execute(
            "SELECT game_id, player_id, status, as_of, source FROM player_availability "
            f"WHERE game_id IS NOT NULL{bound}"
        ).pl()
        try:
            flags = con.execute(
                "SELECT game_id, season, covid_bubble, no_fans, limited_fans, shortened_season "
                f"FROM game_era_flags WHERE season IN ({ss}) AND season <= {MAX_SEASON}"
            ).pl()
        except duckdb.Error:
            flags = pl.DataFrame(schema=FLAG_SCHEMA)
    finally:
        con.close()
    assert_no_holdout(games)
    return {"games": games, "pgs": pgs, "static": static, "avail": avail, "flags": flags}


@dataclass
class World:
    cur: dict[str, pl.DataFrame]
    hist: dict[str, pl.DataFrame]
    tips_cur: pl.DataFrame
    tips_hist: pl.DataFrame
    elo_params: dict[str, float]
    cfg: ContextResidualConfig

    @property
    def games_all(self) -> pl.DataFrame:
        return _cat([self.cur["games"], self.hist["games"]])

    @property
    def pgs_all(self) -> pl.DataFrame:
        return _cat([self.cur["pgs"], self.hist["pgs"]])

    @property
    def avail_all(self) -> pl.DataFrame:
        return _cat([self.cur["avail"], self.hist["avail"]])

    @property
    def static_all(self) -> pl.DataFrame:
        return _cat([self.cur["static"], self.hist["static"]]).unique(
            "player_id", keep="first", maintain_order=True
        )

    @property
    def tips_all(self) -> pl.DataFrame:
        return _cat([self.tips_cur, self.tips_hist]).unique(
            "game_id", keep="first", maintain_order=True
        )

    @property
    def hist_ids(self) -> set[str]:
        return set(self.hist["games"]["game_id"].to_list())


def _cat(frames: list[pl.DataFrame]) -> pl.DataFrame:
    frames = [f for f in frames if f.width]
    return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()


def load_config(
    config_path: str, elo_config: str
) -> tuple[ContextResidualConfig, dict[str, float]]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    return cfg, elo


def load_world(
    db: str,
    history_db: str,
    config_path: str = "configs/context_residual.yaml",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    schedule_dir: str | None = None,
    history_schedule: str | Path = HIST_SCHEDULE,
) -> World:
    cfg, elo = load_config(config_path, elo_config)
    cur = load_side(db, CUR_SEASONS, current=True)
    hist = load_side(history_db, HIST_SEASONS, current=False)
    overlap = set(cur["games"]["game_id"].to_list()) & set(hist["games"]["game_id"].to_list())
    if overlap:
        raise Stop("overlap", f"{len(overlap)} game_id(s) present in both databases")
    return World(
        cur=cur,
        hist=hist,
        tips_cur=build_game_tipoff(schedule_dir),
        tips_hist=build_game_tipoff(history_schedule),
        elo_params=elo,
        cfg=cfg,
    )


# --------------------------------------------------------------------------- report rows (real tips)


def report_rows(avail: pl.DataFrame, games: pl.DataFrame, sources: tuple[str, ...]) -> pl.DataFrame:
    return (
        avail.filter(pl.col("source").is_in(list(sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            "player_id",
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            "game_date",
        )
    )


def rows_with_real_tips(
    rows: pl.DataFrame, tips: pl.DataFrame, strict_ids: set[str]
) -> pl.DataFrame:
    """Attach ``tip_et``; rows of ``strict_ids`` games with no real tip are dropped (no proxy fallback)."""
    out = attach_real_tips(rows, tips)
    if strict_ids:
        out = out.filter(
            ~(pl.col("tip_et").is_null() & pl.col("game_id").is_in(sorted(strict_ids)))
        )
    return out


def flagged_real(
    avail: pl.DataFrame,
    games: pl.DataFrame,
    cfg: ContextResidualConfig,
    tips: pl.DataFrame,
    strict_ids: set[str],
) -> tuple[dict[str, set[int]], dict[str, dt.datetime], pl.DataFrame]:
    """Twin of ``flagged_from_availability`` with explicit tips; returns (flagged, used, rows with tips)."""
    if cfg.report.tip_source != "real":
        raise Stop("tip_source", "tip_source must be 'real' in every arm")
    rows = rows_with_real_tips(report_rows(avail, games, cfg.report.sources), tips, strict_ids)
    flagged, used = latest_pretip_flagged(rows, cfg.report)
    return flagged, used, rows


# --------------------------------------------------------------------------- era flags


def era_flag_frame(w: World) -> pl.DataFrame:
    """Flags of every game in both windows, each from the DB the game lives in; validated, 2022+ all-False."""
    out: list[pl.DataFrame] = []
    for side, label in ((w.cur, "current"), (w.hist, "history")):
        games, flags = side["games"], side["flags"]
        missing = set(games["game_id"].to_list()) - set(flags["game_id"].to_list())
        if missing:
            raise Stop(
                "flags_missing", f"{len(missing)} {label} game(s) without a game_era_flags row"
            )
        flags = flags.unique("game_id", keep="last")
        rule = compute_era_flags(games.select("game_id", "game_date", "season", "home_team"))
        j = flags.join(
            rule.select("game_id", *[pl.col(c).alias(f"_r_{c}") for c in FLAG_COLS]), on="game_id"
        )
        for c in FLAG_COLS:
            if j.filter(pl.col(c) != pl.col(f"_r_{c}")).height:
                raise Stop("flags_rule", f"{label} {c} differs from nba.ingest.era_flags rule")
        out.append(flags.select("game_id", "season", *FLAG_COLS))
    allf = pl.concat(out, how="vertical_relaxed")
    cur_any = (
        allf.filter(pl.col("season") >= 2022).select(pl.any_horizontal(*FLAG_COLS).any()).item()
    )
    if cur_any:
        raise Stop("flags_2022", "a 2022+ game carries a True era flag (must be all-False)")
    return allf


# --------------------------------------------------------------------------- feature frames


@dataclass
class Frames:
    f0: pl.DataFrame  # W0: current window only
    fx: pl.DataFrame  # extended window, with the four flag columns (0/1) and ``flagged_any``
    flagged_cur: dict[str, set[int]]
    flagged_ext: dict[str, set[int]]
    used_ext: dict[str, dt.datetime]
    rows_ext: pl.DataFrame
    flags: pl.DataFrame
    cur_start: dt.date


def build_frames(w: World) -> Frames:
    cfg = w.cfg
    flags = era_flag_frame(w)
    flagged_cur, _used0, _ = flagged_real(w.cur["avail"], w.cur["games"], cfg, w.tips_cur, set())
    f0 = build_features(
        w.cur["games"],
        w.cur["pgs"].select(list(PROD_PGS)),
        w.cur["static"],
        flagged_cur,
        w.elo_params,
    )
    games_all = w.games_all
    flagged_ext, used_ext, rows_ext = flagged_real(
        w.avail_all, games_all, cfg, w.tips_all, w.hist_ids
    )
    cur_ids = set(w.cur["games"]["game_id"].to_list())
    a = {g: s for g, s in flagged_ext.items() if g in cur_ids}
    if a != flagged_cur:
        raise Stop("flagged_mismatch", "extended report sets differ from W0 on current games")
    fx = build_features(
        games_all, w.pgs_all.select(list(PROD_PGS)), w.static_all, flagged_ext, w.elo_params
    )
    fx = fx.join(flags.select("game_id", *FLAG_COLS), on="game_id", how="left")
    if fx.select(pl.any_horizontal(*[pl.col(c).is_null() for c in FLAG_COLS]).any()).item():
        raise Stop("flags_null", "feature rows without era flags")
    fx = fx.with_columns(
        [pl.col(c).cast(pl.Float64) for c in FLAG_COLS],
    ).with_columns(pl.any_horizontal(*[pl.col(c) > 0 for c in FLAG_COLS]).alias("flagged_any"))
    cur_start = w.cur["games"]["game_date"].min()
    assert isinstance(cur_start, dt.date)
    return Frames(f0, fx, flagged_cur, flagged_ext, used_ext, rows_ext, flags, cur_start)


def arm_frame(fr: Frames, arm: str) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """(feature frame, extra feature names) of an arm. Flags only in W1/W1r; ``sample_w`` only in W2."""
    if arm == "W0":
        return fr.f0, ()
    base = fr.fx.drop("flagged_any")
    if arm == "W1":
        return base, FLAG_COLS
    if arm == "W2":
        w = pl.when(fr.fx["flagged_any"]).then(FLAG_WEIGHT).otherwise(1.0).alias("sample_w")
        return base.with_columns(w), ()
    if arm == "W3":
        return base, ()
    if arm == "W1r":
        keep = ~((pl.col("season") <= HIST_SEASONS[-1]) & (pl.col("has_report") == 0))
        return base.filter(keep), FLAG_COLS
    raise ValueError(arm)


def era_flags_zero_on_current(fr: Frames) -> bool:
    cur = fr.fx.filter(pl.col("season") >= 2022)
    return not cur.select(pl.any_horizontal(*[pl.col(c) > 0 for c in FLAG_COLS]).any()).item()


# --------------------------------------------------------------------------- fit: weights (W2 only)


def _reg(cfg: ContextResidualConfig) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(**_params(cfg))  # type: ignore[arg-type]


@dataclass
class WeightedContextResidualModel(ContextResidualModel):
    """Production model with LightGBM ``sample_weight`` on the mean and scale heads.

    Weights come from the ``sample_w`` column of the training frame. Without that column this is the
    production ``fit`` verbatim (W0/W1/W3/W1r). The calibration window (newest 15%, cut on a date
    boundary by the production ``split_calibration``) is NEVER weighted. Production fits no mixture or
    classifier head at ``lower_tail="off"``, so there is nothing else to weight."""

    def fit(self, train: pl.DataFrame) -> WeightedContextResidualModel:
        if "sample_w" not in train.columns:
            super().fit(train)
            return self
        if self.cfg.lower_tail != "off":
            raise Stop(
                "lower_tail", "weighted fit supports lower_tail='off' only (production default)"
            )
        from nba.props.context_residual import _matrix

        fit_df, cal_df = split_calibration(train, self.cfg)
        if fit_df.height < 200 or cal_df.height < 50:
            raise ValueError("not enough rows to fit + calibrate")
        x_fit = _matrix(fit_df, self.names)
        r_fit = fit_df["resid"].to_numpy()
        w_fit = fit_df["sample_w"].to_numpy().astype(float)
        self.mean_head = _reg(self.cfg).fit(x_fit, r_fit, sample_weight=w_fit)
        self.scale_head = _reg(self.cfg).fit(x_fit, np.abs(r_fit), sample_weight=w_fit)
        x_cal = _matrix(cal_df, self.names)
        mu = self.mean_head.predict(x_cal)
        s = self._scale(x_cal)
        z = (cal_df["resid"].to_numpy() - mu) / s
        self.z_sorted = np.sort(z)
        self.z_base_sorted = np.sort(
            (cal_df["y"].to_numpy() - cal_df["m"].to_numpy()) / cal_df["s"].to_numpy()
        )
        gain = self.mean_head.booster_.feature_importance("gain")
        tot = float(gain.sum()) or 1.0
        self.importance_ = {n: float(g) / tot for n, g in zip(self.names, gain, strict=True)}
        return self


# --------------------------------------------------------------------------- walk-forward per (arm, stat)

SCORE_KEYS: tuple[str, ...] = (
    "gid",
    "pid",
    "season",
    "y",
    "crps_int",
    "crps_cont",
    "tll_low",
    "pit_int",
    "pred_mean",
)


def walk_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    *,
    keys: pl.DataFrame | None,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    cal_floor: dt.date | None = None,
    only_block: tuple[int, int] | None = None,
    progress: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Production month-block walk-forward of one (arm, stat) on the evaluation rows ``keys``.

    ``keys`` = None (W0) scores every eligible test row; otherwise only the given (game_id, player_id)
    keys, with the block calendar derived from those keys (so blocks are identical across arms).
    Training rows of a block: every eligible row of ``feats`` dated strictly before the block, in
    (date, game, player) order; the newest 15% of them (production split) is the unweighted
    calibration window and must not start before ``cal_floor`` (the current-window start)."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    if cfg.lower_tail != "off":
        raise Stop("lower_tail", "arms run with lower_tail='off' (production default)")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    if keys is not None:
        test_all = test_all.join(
            keys.select("game_id", "player_id"), on=["game_id", "player_id"], how="semi"
        )
    blocks = month_blocks(test_all)
    if only_block is not None:
        blocks = [b for b in blocks if (b[0].year, b[0].month) == only_block]
    acc: dict[str, list[np.ndarray]] = {k: [] for k in SCORE_KEYS}
    pge: dict[int, list[np.ndarray]] = {n: [] for n in LOW_THRESHOLDS[stat]}
    info: dict[str, Any] = {"blocks": [], "skipped": [], "cal_min_date": None}
    weighted = "sample_w" in sf.columns
    for b, (start, end) in enumerate(blocks):
        block = test_all.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < MIN_TRAIN:
            info["skipped"].append(str(start))
            continue
        _fit, cal_df = split_calibration(train, cfg)
        cal_min = cal_df["game_date"].min()
        assert isinstance(cal_min, dt.date)
        if cal_floor is not None and cal_min < cal_floor:
            raise Stop("calibration", f"calibration window starts {cal_min} before {cal_floor}")
        cal_txt = str(cal_min)
        prev_cal = info["cal_min_date"]
        info["cal_min_date"] = cal_txt if prev_cal is None else min(str(prev_cal), cal_txt)
        model = WeightedContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        assert model.z_sorted is not None
        q = raw_quantiles(c, s, model.z_sorted, CRPS_TAUS)
        y = block["y"].to_numpy()
        sc = arm_scores(q, y, stat)
        qi = to_integer_support(q)
        for n in pge:
            pge[n].append((qi >= n).mean(axis=1))
        acc["gid"].append(block["game_id"].to_numpy())
        acc["pid"].append(block["player_id"].to_numpy())
        acc["season"].append(block["season"].to_numpy())
        acc["y"].append(y)
        for k in ("crps_int", "crps_cont", "tll_low", "pit_int"):
            acc[k].append(np.asarray(sc[k]))
        acc["pred_mean"].append(np.clip(c + s * float(model.z_sorted.mean()), 0.0, None))
        info["blocks"].append(
            {
                "start": str(start),
                "n_train": int(train.height),
                "n_test": int(block.height),
                "weighted": weighted,
            }
        )
        if progress:
            _log(
                f"  {stat} block {b + 1}/{len(blocks)} {start} train={train.height} test={block.height}"
            )
    if not acc["y"]:
        return {}, info
    out = {k: np.concatenate(v) for k, v in acc.items()}
    for n, v in pge.items():
        out[f"pge_{n}"] = np.concatenate(v)
    return out, info


# --------------------------------------------------------------------------- arms driver


def stat_names(stat: str, extra: tuple[str, ...]) -> list[str]:
    return stat_feature_names(stat, extra=extra)


def _save(path: Path, sc: dict[str, np.ndarray], info: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"__info": np.array(json.dumps(info)), **sc}
    np.savez_compressed(path, **payload)


def _load(path: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    z = np.load(path, allow_pickle=False)
    info = json.loads(str(z["__info"]))
    return {k: z[k] for k in z.files if k != "__info"}, info


def check_repro(sc: dict[str, np.ndarray], tol: float = REPRO_TOL) -> dict[str, Any]:
    """G0.6: W0 pts integer CRPS must reproduce production 3.1528 / 3.1884 (STOP otherwise)."""
    res: dict[str, Any] = {}
    ok = True
    for season, want in REPRO_PTS.items():
        m = sc["season"] == season
        got = float(sc["crps_int"][m].mean()) if m.any() else float("nan")
        good = bool(abs(got - want) <= tol)
        res[str(season)] = {"got": got, "want": want, "n": int(m.sum()), "ok": good}
        ok = ok and good
    res["ok"] = ok
    return res


def align(base: dict[str, np.ndarray], arm: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Return ``arm`` in the row order of ``base``; STOP when the key sets differ (no silent row loss)."""
    if np.array_equal(base["gid"], arm["gid"]) and np.array_equal(base["pid"], arm["pid"]):
        return arm
    if len(base["gid"]) != len(arm["gid"]):
        raise Stop(
            "keys", f"evaluation key count differs: W0 {len(base['gid'])} vs arm {len(arm['gid'])}"
        )
    idx = pl.DataFrame({"g": arm["gid"], "p": arm["pid"], "i": np.arange(len(arm["gid"]))})
    want = pl.DataFrame({"g": base["gid"], "p": base["pid"]})
    j = want.join(idx, on=["g", "p"], how="left")
    if j["i"].null_count() or j.height != len(base["gid"]):
        raise Stop("keys", "evaluation key sets differ between W0 and an arm")
    order = j["i"].to_numpy()
    return {k: v[order] for k, v in arm.items()}


def run_arms(
    fr: Frames,
    cfg: ContextResidualConfig,
    *,
    stats: tuple[str, ...] = PROP_STATS,
    arms: tuple[str, ...] = ARMS,
    cache: Path | None = None,
    only_block: tuple[int, int] | None = None,
    repro: bool = False,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    progress: bool = True,
) -> dict[str, Any]:
    """Fit every (arm, stat) on identical evaluation keys fixed by W0. Returns scores, infos and key logs."""
    scores: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    infos: dict[str, Any] = {}
    key_log: dict[str, Any] = {}
    order = ["W0", *[a for a in arms if a != "W0"]]
    for stat in stats:
        w0: dict[str, np.ndarray] | None = None
        for arm in order:
            path = None if cache is None else cache / f"arm_{arm}_{stat}.npz"
            if path is not None and path.exists() and only_block is None:
                sc, info = _load(path)
                _log(f"{arm} {stat}: resumed from {path}")
            else:
                feats, extra = arm_frame(fr, arm)
                _log(f"{arm} {stat}: fitting ({feats.height} feature rows)")
                keys = None
                if arm != "W0":
                    assert w0 is not None
                    keys = pl.DataFrame({"game_id": w0["gid"], "player_id": w0["pid"]})
                sc, info = walk_arm(
                    feats,
                    stat,
                    cfg,
                    stat_names(stat, extra),
                    keys=keys,
                    test_seasons=test_seasons,
                    cal_floor=None if arm == "W0" else fr.cur_start,
                    only_block=only_block,
                    progress=progress,
                )
                if not sc:
                    raise Stop("empty", f"{arm} {stat} scored no rows")
                if path is not None and only_block is None:
                    _save(path, sc, info)
            if arm == "W0":
                w0 = sc
                if repro and stat == "pts":
                    rep = check_repro(sc)
                    key_log["repro"] = rep
                    _log(f"G0.6 reproduction: {json.dumps(rep)}")
                    if not rep["ok"]:
                        raise Stop("G0.6", f"W0 does not reproduce production pts CRPS: {rep}")
            else:
                assert w0 is not None
                sc = align(w0, sc)
            scores[(arm, stat)] = sc
            infos[f"{arm}|{stat}"] = info
            key_log[f"{arm}|{stat}"] = {
                "n_rows": int(len(sc["gid"])),
                "n_games": int(len(np.unique(sc["gid"]))),
                "key_hash": key_hash(sc["gid"], sc["pid"]),
            }
            if arm != "W0":
                assert w0 is not None
                if key_log[f"{arm}|{stat}"]["key_hash"] != key_log[f"W0|{stat}"]["key_hash"]:
                    raise Stop("keys", f"{arm} {stat}: key hash differs from W0")
            _log(
                f"{arm} {stat}: n={len(sc['gid'])} key_hash={key_log[f'{arm}|{stat}']['key_hash'][:12]}"
            )
    return {"scores": scores, "infos": infos, "key_log": key_log}


# --------------------------------------------------------------------------- slice metadata (identical across arms)


def slice_meta(fr: Frames, static_all: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """Per evaluation key: labels that are IDENTICAL across arms (never features)."""
    f0 = stat_frame(fr.f0, "pts").select(
        "game_id", "player_id", "n_prior", "starter10", "team_game_no", "n_out_rot", "has_report"
    )
    ext = fr.fx.select("game_id", "player_id", pl.col("n_prior").alias("n_prior_ext"))
    first = fr.fx.group_by("player_id").agg(pl.col("season").min().alias("first_union_season"))
    draft = static_all.select("player_id", "draft_year").unique("player_id", keep="first")
    meta = (
        keys.select("game_id", "player_id")
        .join(f0, on=["game_id", "player_id"], how="left")
        .join(ext, on=["game_id", "player_id"], how="left")
        .join(first, on="player_id", how="left")
        .join(draft, on="player_id", how="left")
    )
    return meta


def slice_masks(meta: pl.DataFrame, season: np.ndarray) -> dict[str, np.ndarray]:
    npr = meta["n_prior"].to_numpy().astype(float)
    nout = meta["n_out_rot"].to_numpy().astype(float)
    hasr = meta["has_report"].to_numpy().astype(float)
    st10 = meta["starter10"].to_numpy().astype(float)
    tgn = meta["team_game_no"].to_numpy().astype(float)
    ext = meta["n_prior_ext"].to_numpy().astype(float)
    first = meta["first_union_season"].to_numpy().astype(float)
    dy = meta["draft_year"].to_numpy().astype(float)
    rookie = (first == season) & (np.isnan(dy) | (dy >= season - 1))
    return {
        "cold_start(n_prior<20, current window)": npr < 20,
        "true_rookie": rookie,
        "hist<82 prior played games": ext < 82,
        "injury_heavy(n_out_rot>=2)": nout >= 2,
        "teammate_out(n_out_rot>0)": (hasr > 0) & (nout > 0),
        "starter(starter10>=.5)": st10 >= 0.5,
        "bench": st10 < 0.5,
        "first15_team_games": tgn < 15,
    }


# --------------------------------------------------------------------------- contrasts


def ci_dict(delta: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=cluster)
    return {
        "point": float(c.point),
        "lo": float(c.lo),
        "hi": float(c.hi),
        "se": float((c.hi - c.lo) / (2 * 1.96)),
    }


def ece(p: np.ndarray, ev: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(float(p[m].mean()) - float(ev[m].mean()))
    return float(tot)


def contrast(
    base: dict[str, np.ndarray],
    arm: dict[str, np.ndarray],
    season: int,
    masks: dict[str, np.ndarray] | None,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Paired ``arm - base`` on one season; identical keys asserted."""
    if not (np.array_equal(base["gid"], arm["gid"]) and np.array_equal(base["pid"], arm["pid"])):
        raise Stop("keys", "contrast on non-identical keys")
    m = base["season"] == season
    gid = base["gid"][m]
    d = arm["crps_int"][m] - base["crps_int"][m]
    res: dict[str, Any] = {
        "season": season,
        "n_rows": int(m.sum()),
        "n_games": int(len(np.unique(gid))),
        "base_crps_int": float(base["crps_int"][m].mean()),
        "arm_crps_int": float(arm["crps_int"][m].mean()),
        "d_crps_int": ci_dict(d, gid, n_boot),
        "p": boot_p_value(d, gid, n_boot),
        "d_crps_cont": ci_dict(arm["crps_cont"][m] - base["crps_cont"][m], gid, n_boot),
        "d_tll_low": ci_dict(arm["tll_low"][m] - base["tll_low"][m], gid, n_boot),
        "bias_arm": float((arm["pred_mean"][m] - arm["y"][m]).mean()),
        "bias_base": float((base["pred_mean"][m] - base["y"][m]).mean()),
        "cov80_arm": float(((arm["pit_int"][m] > 0.1) & (arm["pit_int"][m] <= 0.9)).mean()),
        "cov80_base": float(((base["pit_int"][m] > 0.1) & (base["pit_int"][m] <= 0.9)).mean()),
    }
    ece_rows: dict[str, Any] = {}
    for k in sorted(x for x in arm if x.startswith("pge_")):
        n = int(k[4:])
        ev = arm["y"][m] >= n
        ece_rows[str(n)] = {"arm": ece(arm[k][m], ev), "base": ece(base[k][m], ev)}
    res["ece_pge"] = ece_rows
    if masks is not None:
        sl: dict[str, Any] = {}
        for name, mask in masks.items():
            mm = m & mask
            n = int(mm.sum())
            if n >= 30:
                dd = arm["crps_int"][mm] - base["crps_int"][mm]
                sl[name] = {
                    "n_rows": n,
                    "d_crps_int": float(dd.mean()),
                    "binding": n >= SLICE_MIN_N,
                }
        res["slices"] = sl
    return res


def pass_checks(c: dict[str, Any], bh_p: float) -> dict[str, Any]:
    d = c["d_crps_int"]
    bad = [
        k for k, v in c.get("slices", {}).items() if v["binding"] and v["d_crps_int"] > SLICE_TOL
    ]
    checks = {
        "floor(d<=-0.005)": bool(d["point"] <= FLOOR),
        "ci_upper<0": bool(d["hi"] < 0),
        "bh_p<0.05": bool(bh_p < 0.05),
        "bias<=0.5": bool(abs(c["bias_arm"]) <= BIAS_TOL),
        "cov80_in_[.75,.85]": bool(COV_LO <= c["cov80_arm"] <= COV_HI),
        "no_slice_worse_than_+0.01": not bad,
    }
    return {"checks": checks, "bad_slices": bad, "pass": all(checks.values()), "bh_p": float(bh_p)}


def is_harm(c: dict[str, Any]) -> bool:
    d = c["d_crps_int"]
    return bool(d["point"] >= HARM and d["lo"] > 0)


def arm_verdict(per_stat: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """``per_stat[stat][str(season)] = {"pass": bool, "harm": bool}`` for one arm.

    PASS = at least one stat passes on BOTH seasons (same-stat reading). HARM blocks any recommendation."""
    seasons = [str(s) for s in TEST_SEASONS]
    same = [s for s, v in per_stat.items() if all(v[x]["pass"] for x in seasons)]
    loose = all(any(v[x]["pass"] for v in per_stat.values()) for x in seasons)
    harm = [f"{s}:{x}" for s, v in per_stat.items() for x in seasons if v[x]["harm"]]
    status = "HARM" if harm else ("PASS" if same else "FAIL")
    return {
        "status": status,
        "passing_stats": same,
        "loose_reading_pass": bool(loose),
        "harm": harm,
    }


def analyze(
    run: dict[str, Any],
    fr: Frames,
    static_all: pl.DataFrame,
    arms: tuple[str, ...],
    stats: tuple[str, ...] = PROP_STATS,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    sc = run["scores"]
    first_stat = stats[0]
    w0_first = sc[("W0", first_stat)]
    keys = pl.DataFrame({"game_id": w0_first["gid"], "player_id": w0_first["pid"]})
    for s in stats:
        if key_hash(sc[("W0", s)]["gid"], sc[("W0", s)]["pid"]) != key_hash(
            w0_first["gid"], w0_first["pid"]
        ):
            raise Stop("keys", f"W0 key sets differ between stats ({s})")
    meta = slice_meta(fr, static_all, keys)
    masks = slice_masks(meta, w0_first["season"].astype(float))
    out: dict[str, Any] = {
        "contrasts": {},
        "family": {},
        "verdict": {},
        "w1_vs_w2": {},
        "baseline": {},
    }
    for season in TEST_SEASONS:
        for stat in stats:
            b = sc[("W0", stat)]
            m = b["season"] == season
            out["baseline"][f"{stat}|{season}"] = {
                "n_rows": int(m.sum()),
                "n_games": int(len(np.unique(b["gid"][m]))),
                "crps_int": float(b["crps_int"][m].mean()),
            }
            for arm in arms:
                if arm == "W0":
                    continue
                out["contrasts"][f"{arm}|{stat}|{season}"] = contrast(
                    b, sc[(arm, stat)], season, masks, n_boot
                )
    # confirmatory family: {W1, W2} x stats per season, BH q = 0.05
    verdict_in: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
        a: {s: {} for s in stats} for a in FAMILY_ARMS if a in arms
    }
    for season in TEST_SEASONS:
        fam = [(a, s) for a in FAMILY_ARMS if a in arms for s in stats]
        p = np.array([out["contrasts"][f"{a}|{s}|{season}"]["p"] for a, s in fam])
        adj = bh_adjust(p)
        out["family"][str(season)] = {
            "tests": [f"{a}|{s}" for a, s in fam],
            "p": p.tolist(),
            "bh_adj_p": adj.tolist(),
        }
        for (a, s), q in zip(fam, adj, strict=True):
            c = out["contrasts"][f"{a}|{s}|{season}"]
            pc = pass_checks(c, float(q))
            c["pass_checks"] = pc
            c["harm"] = is_harm(c)
            verdict_in[a][s][str(season)] = {"pass": pc["pass"], "harm": c["harm"]}
    for a in verdict_in:
        out["verdict"][a] = arm_verdict(verdict_in[a])
    # descriptive arms: harm flag only, no pass verdict
    for arm in arms:
        if arm in ("W0", *FAMILY_ARMS):
            continue
        for season in TEST_SEASONS:
            for stat in stats:
                c = out["contrasts"][f"{arm}|{stat}|{season}"]
                c["harm"] = is_harm(c)
    # W1 vs W2 preference
    if "W1" in arms and "W2" in arms:
        for season in TEST_SEASONS:
            for stat in stats:
                m = sc[("W0", stat)]["season"] == season
                d = sc[("W1", stat)]["crps_int"][m] - sc[("W2", stat)]["crps_int"][m]
                out["w1_vs_w2"][f"{stat}|{season}"] = ci_dict(d, sc[("W0", stat)]["gid"][m], n_boot)
        both = [a for a in ("W1", "W2") if out["verdict"][a]["status"] == "PASS"]
        pref = None
        if len(both) == 2:
            beats = any(
                all(out["w1_vs_w2"][f"{s}|{x}"]["hi"] < 0 for x in TEST_SEASONS)
                for s in out["verdict"]["W1"]["passing_stats"]
            )
            pref = "W1" if beats else "W2"
        out["preferred_if_both_pass"] = pref
    out["mde"] = {
        k: float(2.8 * v["d_crps_int"]["se"])
        for k, v in out["contrasts"].items()
        if k.startswith("W1|")
    }
    return out


# --------------------------------------------------------------------------- H2: injury-Elo coefficients (descriptive)


@contextlib.contextmanager
def patched_tips(tips: pl.DataFrame) -> Iterator[None]:
    """``attach_real_tips(rows)`` inside ``build_injury_features`` reads the default schedule; point it at
    the union of current and history tips for the duration of the call."""
    with mock.patch("nba.features.game_tipoff.build_game_tipoff", lambda *a, **k: tips):
        yield


def _stats_frame(pgs: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    return (
        pgs.join(games.select("game_id", "game_date"), on="game_id")
        .select(
            "player_id",
            "team_id",
            "game_id",
            "game_date",
            pl.col("minutes").fill_null(0.0).alias("minutes"),
            (
                pl.col("pts").fill_null(0)
                + pl.col("reb").fill_null(0)
                + pl.col("ast").fill_null(0)
                + pl.col("stl").fill_null(0)
                + pl.col("blk").fill_null(0)
                - pl.col("tov").fill_null(0)
            ).alias("comp"),
        )
        .cast({"minutes": pl.Float64})
    )


def h2_window(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    avail: pl.DataFrame,
    tips: pl.DataFrame,
    strict_ids: set[str],
    elo_params: dict[str, float],
    inj_cfg: dict[str, Any],
) -> pl.DataFrame:
    """Per-game walk-forward win probability of injury-Elo fitted on this window (month blocks)."""
    fcfg = feature_config_from(inj_cfg)
    g = games.filter(pl.col("home_pts").is_not_null()).with_columns(
        (pl.col("home_pts") > pl.col("away_pts")).cast(pl.Int64).alias("y")
    )
    g = g.sort(["game_date", "game_id"])
    stats = _stats_frame(pgs, g)
    rows = rows_with_real_tips(report_rows(avail, g, (OFFICIAL_REPORT_SOURCE,)), tips, strict_ids)
    with patched_tips(tips):
        feats = build_injury_features(
            duckdb.connect(":memory:"), g, stats, fcfg, report_rows=rows, with_oracle=False
        )
    frame = g.join(feats, on="game_id", how="left").sort(["game_date", "game_id"])
    frame = frame.with_columns([pl.col(c).fill_null(0.0) for c in ("d_out", "d_doubt")])
    offset = sequential_elo_logits(frame, elo_params)
    fit = inj_cfg["fit"]
    p, _log_ = walk_forward_probs(
        frame,
        offset,
        ("d_out", "d_doubt"),
        float(fit["ridge_lambda"]),
        int(fit["min_signal_games"]),
    )
    return frame.select("game_id", "season", "y").with_columns(pl.Series("p", p))


def h2_compare(
    w0: pl.DataFrame,
    ext: pl.DataFrame,
    n_boot: int = N_BOOT,
    seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for season in seasons:
        a = w0.filter(pl.col("season") == season).sort("game_id")
        b = ext.filter(pl.col("season") == season).sort("game_id")
        if a["game_id"].to_list() != b["game_id"].to_list():
            raise Stop("h2_keys", f"H2 game sets differ in {season}")
        y = a["y"].to_numpy().astype(float)
        pa, pb = a["p"].to_numpy(), b["p"].to_numpy()
        la = -(y * np.log(pa) + (1 - y) * np.log(1 - pa))
        lb = -(y * np.log(pb) + (1 - y) * np.log(1 - pb))
        gid = a["game_id"].to_numpy()
        ll = ci_dict(lb - la, gid, n_boot)
        br = ci_dict((pb - y) ** 2 - (pa - y) ** 2, gid, n_boot)
        out[str(season)] = {
            "n_games": int(len(y)),
            "logloss_w0": float(la.mean()),
            "logloss_ext": float(lb.mean()),
            "d_logloss": ll,
            "d_brier": br,
            "supported": bool(ll["point"] <= H2_FLOOR and ll["hi"] < 0),
        }
    return out


def run_h2(w: World, inj_config: str, n_boot: int = N_BOOT) -> dict[str, Any]:
    inj = yaml.safe_load(Path(inj_config).read_text())
    tips_all = w.tips_all
    w0 = h2_window(
        w.cur["games"], w.cur["pgs"], w.cur["avail"], w.tips_cur, set(), w.elo_params, inj
    )
    ext = h2_window(w.games_all, w.pgs_all, w.avail_all, tips_all, w.hist_ids, w.elo_params, inj)
    res = h2_compare(w0, ext, n_boot)
    res["note"] = (
        "descriptive, outside the family; bundles coefficient and Elo warm-up effects; no promotion consequence"
    )
    return res


# --------------------------------------------------------------------------- coverage audit (G2 / G3)


def phi(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(bool), b.astype(bool)
    n11 = float((a & b).sum())
    n10 = float((a & ~b).sum())
    n01 = float((~a & b).sum())
    n00 = float((~a & ~b).sum())
    den = ((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00)) ** 0.5
    return float((n11 * n00 - n10 * n01) / den) if den > 0 else float("nan")


def coverage_table(w: World, fr: Frames) -> dict[str, Any]:
    """Audit item 1: per season, report coverage on the real-tip pre-tip rule."""
    cfg = w.cfg.report
    usable = usable_report_rows(fr.rows_ext, cfg)
    snap = usable.group_by("game_id").agg(
        pl.col("as_of").n_unique().alias("n_snap"), pl.col("as_of").max().alias("latest")
    )
    box_ids = fr.fx.select("game_id").unique()
    g = (
        w.games_all.select("game_id", "season", "game_date")
        .join(box_ids, on="game_id")
        .join(snap, on="game_id", how="left")
        .join(w.tips_all.select("game_id", "tip_et"), on="game_id", how="left")
        .with_columns(
            pl.col("n_snap").fill_null(0),
            (
                pl.coalesce(
                    pl.col("tip_et"),
                    pl.col("game_date").cast(pl.Datetime("us"))
                    + pl.duration(hours=int(cfg.tipoff_hour_et)),
                )
                - pl.col("latest")
            )
            .dt.total_minutes()
            .alias("lead_min"),
        )
    )
    rows_share = fr.fx.group_by("season").agg(pl.col("has_report").mean().alias("rows_has_report"))
    table: dict[str, Any] = {}
    for season in ALL_SEASONS:
        s = g.filter(pl.col("season") == season)
        if s.is_empty():
            continue
        cov = s.filter(pl.col("n_snap") > 0)
        rs = rows_share.filter(pl.col("season") == season)["rows_has_report"]
        table[str(season)] = {
            "n_games": int(s.height),
            "n_covered": int(cov.height),
            "share_games_with_report": float(cov.height / s.height),
            "share_rows_has_report": float(rs[0]) if rs.len() else float("nan"),
            "snapshots_per_game_all": _num(s["n_snap"].mean()),
            "snapshots_per_game_covered": _num(cov["n_snap"].mean())
            if cov.height
            else float("nan"),
            "median_lead_min": _num(cov["lead_min"].median()) if cov.height else float("nan"),
        }
    cur = g.filter(pl.col("season").is_in(list(CUR_SEASONS)))
    pooled = float(cur.filter(pl.col("n_snap") > 0).height / max(cur.height, 1))
    gaps = {
        str(s): float(table[str(s)]["share_games_with_report"] - pooled)
        for s in HIST_SEASONS
        if str(s) in table
    }
    w1r = any(abs(v) > G2_COV_PP for v in gaps.values())
    return {
        "table": table,
        "pooled_2022_24_game_coverage": pooled,
        "history_minus_pooled": gaps,
        "w1r_required": bool(w1r),
        "n_snap_by_game": {
            str(k): int(v)
            for k, v in zip(g["game_id"].to_list(), g["n_snap"].to_list(), strict=True)
        },
    }


def label_audit(w: World, fr: Frames, n_snap: dict[str, int]) -> dict[str, Any]:
    """Audit item 3: played-rate gaps, NULL-pattern phi, snapshot-count played rates."""
    pgs = (
        w.pgs_all.select("game_id", "player_id", "minutes")
        .join(w.games_all.select("game_id", "game_date", "season"), on="game_id")
        .sort(["player_id", "game_date", "game_id"])
        .with_columns(
            (pl.col("minutes") > 0).fill_null(False).alias("played"),
            pl.col("minutes").is_null().alias("dnp_null"),
            pl.col("game_id").is_in(list(fr.flagged_ext)).alias("has_rep"),
            pl.col("game_id")
            .replace_strict(n_snap, default=0, return_dtype=pl.Int64)
            .alias("n_snap"),
        )
        .with_columns(
            (
                pl.col("played").cast(pl.Int64).cum_sum().over("player_id")
                - pl.col("played").cast(pl.Int64)
            ).alias("n_prior_played")
        )
    )

    def rate(df: pl.DataFrame, flag: bool) -> tuple[float, int]:
        s = df.filter(pl.col("has_rep") == flag)
        return (float(s["played"].mean()) if s.height else float("nan"), int(s.height))  # type: ignore[arg-type]

    per: dict[str, Any] = {}
    for season in ALL_SEASONS:
        d = pgs.filter(pl.col("season") == season)
        if d.is_empty():
            continue
        r1, n1 = rate(d, True)
        r0, n0 = rate(d, False)
        by_snap = {
            str(k): {"played_rate": float(v["played"].mean()), "n": int(v.height)}  # type: ignore[arg-type]
            for k, v in [(k, d.filter(pl.col("n_snap").clip(0, 3) == k)) for k in range(4)]
            if v.height
        }
        dn = d["dnp_null"].to_numpy()
        per[str(season)] = {
            "played_given_report": r1,
            "n_report": n1,
            "played_given_no_report": r0,
            "n_no_report": n0,
            "gap": r1 - r0 if n1 and n0 else float("nan"),
            "played_rate_by_snapshots": by_snap,
            "phi_no_report_vs_dnp": phi(~d["has_rep"].to_numpy(), dn),
            "phi_gap_days_null_vs_dnp": phi(d["n_prior_played"].to_numpy() == 0, dn),
            "phi_since_flag_flag_dir": "never NULL (undefined)",
        }
    cur = pgs.filter(pl.col("season").is_in(list(CUR_SEASONS)))
    r1, n1 = rate(cur, True)
    r0, n0 = rate(cur, False)
    cur_gap = r1 - r0 if n1 and n0 else float("nan")
    cur_phi = {
        "no_report": phi(~cur["has_rep"].to_numpy(), cur["dnp_null"].to_numpy()),
        "gap_days_null": phi(cur["n_prior_played"].to_numpy() == 0, cur["dnp_null"].to_numpy()),
    }
    stops: list[str] = []
    for season in HIST_SEASONS:
        p = per.get(str(season))
        if p is None:
            continue
        if np.isfinite(p["gap"]) and np.isfinite(cur_gap) and abs(p["gap"] - cur_gap) > G3_GAP_PP:
            stops.append(f"G3.gap season {season}: {p['gap']:.4f} vs 2022-24 {cur_gap:.4f}")
        for key, ck in (
            ("phi_no_report_vs_dnp", "no_report"),
            ("phi_gap_days_null_vs_dnp", "gap_days_null"),
        ):
            if (
                np.isfinite(p[key])
                and np.isfinite(cur_phi[ck])
                and abs(p[key] - cur_phi[ck]) > G3_PHI
            ):
                stops.append(
                    f"G3.phi {ck} season {season}: {p[key]:.4f} vs 2022-24 {cur_phi[ck]:.4f}"
                )
    return {
        "per_season": per,
        "current_gap": cur_gap,
        "current_phi": cur_phi,
        "stops": stops,
    }


def distinguishability(fr: Frames, n_boot_seed: int = 0) -> dict[str, Any]:
    """Audit item 3, third STOP: history-vs-current classifier on the report columns alone, AND a sign
    flip of the column/residual correlation between eras."""
    d = fr.fx.filter(pl.col("n_prior") >= 5)
    hist = (d["season"] <= HIST_SEASONS[-1]).to_numpy()
    x = d.select([pl.col(c).cast(pl.Float64) for c in REPORT_COLS]).to_numpy()
    gid = d["game_id"].to_list()
    held = np.array([int(hashlib.sha256(g.encode()).hexdigest(), 16) % 3 == 0 for g in gid])
    auc = float("nan")
    if hist.any() and (~hist).any() and (held & hist).any() and (held & ~hist).any():
        clf = lgb.LGBMClassifier(
            n_estimators=100,
            num_leaves=15,
            random_state=n_boot_seed,
            n_jobs=4,
            verbose=-1,
            deterministic=True,
            force_row_wise=True,
        )
        clf.fit(x[~held], hist[~held].astype(int))
        auc = float(
            roc_auc_score(hist[held].astype(int), np.asarray(clf.predict_proba(x[held]))[:, 1])
        )
    flips: list[str] = []
    corr: dict[str, Any] = {}
    for stat in PROP_STATS:
        resid = (d[stat].cast(pl.Float64) - d[f"m10_{stat}"]).to_numpy()
        for j, c in enumerate(REPORT_COLS):
            rs: dict[str, float] = {}
            for era, m in (("hist", hist), ("cur", ~hist)):
                v = x[m, j]
                r = resid[m]
                ok = np.isfinite(v) & np.isfinite(r)
                if ok.sum() > 30 and np.std(v[ok]) > 0:
                    rs[era] = float(np.corrcoef(v[ok], r[ok])[0, 1])
                    rs[f"{era}_floor"] = float(2.0 / np.sqrt(ok.sum()))
            corr[f"{stat}|{c}"] = rs
            if (
                "hist" in rs
                and "cur" in rs
                and np.sign(rs["hist"]) != np.sign(rs["cur"])
                and abs(rs["hist"]) > rs["hist_floor"]
                and abs(rs["cur"]) > rs["cur_floor"]
            ):
                flips.append(f"{stat}|{c}: hist {rs['hist']:+.4f} vs cur {rs['cur']:+.4f}")
    stop = bool(np.isfinite(auc) and auc > G3_AUC and flips)
    return {"auc": auc, "sign_flips": flips, "correlations": corr, "stop": stop}


def parsing_parity(hist_db: str, db: str) -> dict[str, Any]:
    def unresolved(path: str, bound: str) -> tuple[float, int]:
        con = _ro(path)
        try:
            r = con.execute(
                f"SELECT avg(CASE WHEN player_id IS NULL THEN 1.0 ELSE 0.0 END), count(*) FROM player_availability WHERE source = ?{bound}",
                [OFFICIAL_REPORT_SOURCE],
            ).fetchone()
        finally:
            con.close()
        assert r is not None
        return (float(r[0]) if r[0] is not None else float("nan"), int(r[1]))

    h, hn = unresolved(hist_db, "")
    c, cn = unresolved(db, " AND as_of < TIMESTAMP '2025-07-01'")
    log = HIST_LOG
    return {
        "history_unresolved": h,
        "history_rows": hn,
        "current_unresolved": c,
        "current_rows": cn,
        "pass": bool(np.isfinite(h) and np.isfinite(c) and h <= c + G04_NAME_PP),
        "name_resolver_log": {
            "path": str(log),
            "exists": log.exists(),
            "sha256": file_sha256(log) if log.exists() else None,
        },
    }


def full_audit(w: World, fr: Frames, hist_db: str, db: str) -> dict[str, Any]:
    cov = coverage_table(w, fr)
    n_snap = cov.pop("n_snap_by_game")
    lab = label_audit(w, fr, n_snap)
    dist = distinguishability(fr)
    stops = list(lab["stops"])
    if dist["stop"]:
        stops.append(
            f"G3.classifier AUC {dist['auc']:.4f} > {G3_AUC} AND sign flips {dist['sign_flips']}"
        )
    return {
        "coverage": cov,
        "labels": lab,
        "distinguishability": dist,
        "parsing": parsing_parity(hist_db, db),
        "stops": stops,
    }


# --------------------------------------------------------------------------- gate G0


def g01(w: World, games_dir: Path) -> dict[str, Any]:
    gh, ph = w.hist["games"], w.hist["pgs"]
    cached: set[str] = set()
    for f in sorted(games_dir.glob("*.parquet")):
        cached |= set(pl.read_parquet(f, columns=["game_id"])["game_id"].to_list())
    ids = gh["game_id"].to_list()
    dup = len(ids) - len(set(ids))
    missing = sorted(cached - set(ids))
    box_games = ph.select("game_id").unique().height
    played = ph.filter(pl.col("minutes") > 0)
    fields = (
        float(
            played.select(
                pl.all_horizontal(
                    pl.col("pf").is_not_null(),
                    pl.col("fta").is_not_null(),
                    pl.col("fga").is_not_null(),
                ).mean()
            ).item()
        )
        if played.height
        else float("nan")
    )
    dnp = ph.filter(pl.col("minutes").is_null())
    dnp_bad = int(
        dnp.filter(
            (pl.col("pts") != 0)
            | (pl.col("reb") != 0)
            | (pl.col("ast") != 0)
            | (pl.col("fg3m") != 0)
        ).height
    )
    zero_min = int(ph.filter(pl.col("minutes") == 0).height)

    def shares(pgs: pl.DataFrame, games: pl.DataFrame) -> dict[int, float]:
        j = pgs.join(games.select("game_id", "season"), on="game_id")
        return {
            int(r[0]): float(r[1])
            for r in j.group_by("season").agg(pl.col("minutes").is_null().mean()).iter_rows()
        }

    sh_h, sh_c = shares(ph, gh), shares(w.cur["pgs"], w.cur["games"])
    lo, hi = (
        (min(sh_c.values()) - G01_DNP_PP, max(sh_c.values()) + G01_DNP_PP)
        if sh_c
        else (float("nan"), float("nan"))
    )
    dnp_ok = all(lo <= v <= hi for v in sh_h.values()) if sh_h else False
    res: dict[str, Any] = {
        "cached_games": len(cached),
        "history_games": len(ids),
        "duplicates": dup,
        "missing_vs_cache": len(missing),
        "missing_examples": missing[:10],
        "games_with_box_share": float(box_games / max(len(cached), 1)),
        "pf_fta_fga_non_null_share": fields,
        "dnp_rows_with_nonzero_stats": dnp_bad,
        "rows_with_zero_minutes": zero_min,
        "dnp_share_history": sh_h,
        "dnp_share_current": sh_c,
    }
    res["pass"] = bool(
        len(cached) > 0
        and dup == 0
        and not missing
        and res["games_with_box_share"] >= G01_GAMES_BOX
        and fields >= G01_FIELDS
        and dnp_bad == 0
        and dnp_ok
    )
    return res


def g02(w: World) -> dict[str, Any]:
    out: dict[str, Any] = {"rule_version_sha256": file_sha256(ERA_FLAGS_PY)}
    ok = True
    for label, side in (("history", w.hist), ("current", w.cur)):
        ids = set(side["games"]["game_id"].to_list())
        have = set(side["flags"]["game_id"].to_list())
        cov = len(ids & have) / max(len(ids), 1)
        out[f"{label}_flag_coverage"] = cov
        ok = ok and cov >= 1.0 and bool(ids)
    try:
        flags = era_flag_frame(w)
        out["flags_valid"] = True
        out["current_all_false"] = (
            not flags.filter(pl.col("season") >= 2022)
            .select(pl.any_horizontal(*FLAG_COLS).any())
            .item()
        )
    except Stop as e:
        out["flags_valid"] = False
        out["flag_error"] = e.detail
        ok = False
    out["pass"] = bool(ok and out.get("current_all_false", False))
    return out


def g03(hist_db: str) -> dict[str, Any]:
    con = _ro(hist_db)
    try:
        try:
            poss = con.execute(
                "SELECT p.game_id, p.off_team AS team_id, count(*) AS n FROM possessions p GROUP BY 1, 2"
            ).pl()
        except duckdb.Error:
            poss = pl.DataFrame(schema={"game_id": pl.Utf8, "team_id": pl.Int64, "n": pl.Int64})
        box = con.execute(
            "SELECT s.game_id, s.team_id, s.fga, s.fta, s.tov, s.oreb FROM player_game_stats s"
        ).pl()
        seasons = con.execute("SELECT game_id, season FROM games").pl()
    finally:
        con.close()
    tb = team_box_from_player_game_stats(box).with_columns(
        (pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov") - pl.col("oreb")).alias("est")
    )
    j = tb.join(poss, on=["game_id", "team_id"], how="inner").join(seasons, on="game_id")
    j = j.with_columns((pl.col("n") - pl.col("est")).abs().alias("ae"))
    per = {
        str(int(r[0])): {"mae": float(r[1]), "n_team_games": int(r[2])}
        for r in j.group_by("season").agg(pl.col("ae").mean(), pl.len()).iter_rows()
    }
    ok = all(str(s) in per and per[str(s)]["mae"] <= G03_MAE for s in HIST_SEASONS)
    return {"per_season": per, "ceiling": G03_MAE, "pass": bool(ok)}


def g04(w: World, hist_db: str, manifest_dir: Path, audit_ok: bool) -> dict[str, Any]:
    dates: list[str] = []
    for f in sorted(manifest_dir.glob("*.json")):
        m = json.loads(f.read_text())
        if m.get("reports"):
            dates.append(str(m["date"]))
    n_files = len(list(manifest_dir.glob("*.json")))
    con = _ro(hist_db)
    try:
        have = {
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT CAST(CAST(as_of AS DATE) AS VARCHAR) FROM player_availability WHERE source = ?",
                [OFFICIAL_REPORT_SOURCE],
            ).fetchall()
        }
    finally:
        con.close()
    missing = sorted(set(dates) - have)
    spot = random.Random(0).sample(sorted(dates), min(20, len(dates))) if dates else []
    return {
        "manifest_files": n_files,
        "expected_calendar": MANIFEST_DATES,
        "dates_with_reports": len(dates),
        "dates_missing_rows": missing[:20],
        "n_dates_missing_rows": len(missing),
        "spot_check_dates": spot,
        "spot_check_manual": "compare 20 dates against raw PDFs; pass --spot-check-done to `run` afterwards",
        "audit_complete": audit_ok,
        "pass": bool(n_files == MANIFEST_DATES and not missing and audit_ok),
    }


def g05(w: World) -> dict[str, Any]:
    ids = w.hist["games"]["game_id"].unique()
    have = w.tips_hist.join(pl.DataFrame({"game_id": ids}), on="game_id").height
    share = have / max(len(ids), 1)
    return {
        "history_games": int(len(ids)),
        "with_real_tip": int(have),
        "share": float(share),
        "proxy_fallback_allowed": False,
        "pass": bool(len(ids) > 0 and share >= G05_TIPS),
    }


def run_gate(
    w: World, fr: Frames, hist_db: str, db: str, out_dir: Path, games_dir: Path, manifest_dir: Path
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    items: dict[str, Any] = {"G0.1": g01(w, games_dir), "G0.2": g02(w), "G0.3": g03(hist_db)}
    audit = full_audit(w, fr, hist_db, db)
    audit_path = out_dir / "audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str, sort_keys=True))
    audit_sha = file_sha256(audit_path)
    _log(f"audit sha256 {audit_sha}")
    items["G0.4"] = g04(w, hist_db, manifest_dir, True)
    items["G0.4"]["parsing_parity_pass"] = audit["parsing"]["pass"]
    items["G0.4"]["pass"] = bool(items["G0.4"]["pass"] and audit["parsing"]["pass"])
    items["G0.5"] = g05(w)
    failed = [k for k, v in items.items() if not v["pass"]]
    gate = {
        "items": items,
        "failed_items": failed,
        "audit_stops": audit["stops"],
        "w1r_required": audit["coverage"]["w1r_required"],
        "audit_sha256": audit_sha,
        "gate_pass": bool(not failed and not audit["stops"]),
        "g06": "evaluated first thing in `run` (W0 pts reproduction)",
    }
    (out_dir / "gate.json").write_text(json.dumps(gate, indent=2, default=str, sort_keys=True))
    return gate


def require_gate(out_dir: Path) -> dict[str, Any]:
    p = out_dir / "gate.json"
    if not p.exists():
        raise SystemExit("REFUSED: run the gate stage first (gate.json missing)")
    gate: dict[str, Any] = json.loads(p.read_text())
    if not gate.get("gate_pass"):
        raise SystemExit(
            f"REFUSED: gate did not pass: {gate.get('failed_items')} {gate.get('audit_stops')}"
        )
    a = out_dir / "audit.json"
    if not a.exists() or file_sha256(a) != gate["audit_sha256"]:
        raise SystemExit(
            "REFUSED: audit.json is missing or no longer hashes to the value logged by the gate"
        )
    return gate


# --------------------------------------------------------------------------- experiment + report


def experiment(
    w: World,
    fr: Frames,
    *,
    gate: dict[str, Any] | None,
    inj_config: str,
    cache: Path | None,
    n_boot: int = N_BOOT,
    stats: tuple[str, ...] = PROP_STATS,
    only_block: tuple[int, int] | None = None,
    repro: bool = True,
    with_h2: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    """Arms (W1r only when the audit demands it), analysis, H2. One run per arm (cache resumes)."""
    w1r = bool(gate and gate.get("w1r_required"))
    arms = tuple(a for a in ARMS if a != "W1r" or w1r)
    if not era_flags_zero_on_current(fr):
        raise Stop("flags_2022", "era flags are not all-False on 2022+ rows")
    run = run_arms(
        fr,
        w.cfg,
        stats=stats,
        arms=arms,
        cache=cache,
        only_block=only_block,
        repro=repro,
        progress=progress,
    )
    if only_block is not None:  # smoke: one month block, no inference
        smoke = {
            f"{a}|{s}": float(run["scores"][(a, s)]["crps_int"].mean()) for a in arms for s in stats
        }
        return {"key_log": run["key_log"], "fold_info": run["infos"], "smoke_mean_crps_int": smoke}
    res = analyze(run, fr, w.static_all, arms, stats, n_boot)
    res["arms_run"] = list(arms)
    res["key_log"] = run["key_log"]
    res["fold_info"] = run["infos"]
    res["w1r_required"] = w1r
    if with_h2:
        res["h2"] = run_h2(w, inj_config, n_boot)
    return res


def _f(x: float, nd: int = 4) -> str:
    return f"{x:+.{nd}f}"


def _ci_txt(c: dict[str, float], nd: int = 4) -> str:
    return f"{_f(c['point'], nd)} [{_f(c['lo'], nd)}, {_f(c['hi'], nd)}]"


def render_report(
    res: dict[str, Any], gate: dict[str, Any] | None, audit: dict[str, Any] | None, sha: str
) -> str:
    L: list[str] = ["# HISTORY_WINDOW: training-window extension (2019-20 .. 2021-22)", ""]
    L.append(
        f"Frozen rule: docs/HISTORY_WINDOW.md, frozen-section sha256 `{sha}`. Harness: research/eval/history_window_eval.py."
    )
    L.append("")
    if gate:
        L += [
            "## Gate G0",
            "",
            f"gate_pass: {gate['gate_pass']}; failed: {gate['failed_items']}; audit stops: {gate['audit_stops']}",
            "",
        ]
    if audit:
        L += ["## Report-coverage audit (G2/G3), sha256 logged before any arm", ""]
        L.append(
            "| season | games | covered | game share | row share has_report | snaps/game (all/covered) | median lead min |"
        )
        L.append("|---|---|---|---|---|---|---|")
        for s, r in audit["coverage"]["table"].items():
            L.append(
                f"| {s} | {r['n_games']} | {r['n_covered']} | {r['share_games_with_report']:.4f} | {r['share_rows_has_report']:.4f} | {r['snapshots_per_game_all']:.2f}/{r['snapshots_per_game_covered']:.2f} | {r['median_lead_min']:.0f} |"
            )
        L += [
            "",
            f"Pooled 2022-24 game coverage {audit['coverage']['pooled_2022_24_game_coverage']:.4f}; W1r required: {audit['coverage']['w1r_required']}.",
            "",
        ]
    if "baseline" not in res:
        return "\n".join(L)
    L += ["## G0.6 reproduction", "", f"`{json.dumps(res['key_log'].get('repro'))}`", ""]
    L += [
        "## Evaluation keys (fixed by W0)",
        "",
        "| arm|stat | rows | games | key hash |",
        "|---|---|---|---|",
    ]
    for k, v in res["key_log"].items():
        if k != "repro":
            L.append(f"| {k} | {v['n_rows']} | {v['n_games']} | `{v['key_hash'][:16]}` |")
    L += [
        "",
        "## Verdict (confirmatory family: {W1, W2} x {pts, reb, ast, fg3m}, BH q=0.05 per season)",
        "",
    ]
    for a, v in res["verdict"].items():
        L.append(
            f"* {a}: **{v['status']}** passing stats {v['passing_stats']}, harm {v['harm']}, loose reading {v['loose_reading_pass']}"
        )
    L.append(f"* preferred if both pass: {res.get('preferred_if_both_pass')}")
    L += [
        "",
        "## Paired integer-support CRPS deltas (arm - W0), game-clustered 95% CI (B=2000)",
        "",
    ]
    L.append(
        "| arm | stat | season | n rows / games | W0 CRPS | d CRPS [CI] | BH p | bias | cov80 | pass | harm |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, c in res["contrasts"].items():
        arm, stat, season = k.split("|")
        pc = c.get("pass_checks")
        L.append(
            f"| {arm} | {stat} | {season} | {c['n_rows']} / {c['n_games']} | {c['base_crps_int']:.4f} | {_ci_txt(c['d_crps_int'])} | "
            f"{(pc['bh_p'] if pc else float('nan')):.3g} | {c['bias_arm']:+.3f} | {c['cov80_arm']:.3f} | {pc['pass'] if pc else 'desc'} | {c['harm']} |"
        )
    L += ["", "## Slices (d CRPS vs W0, binding at n >= 300)", ""]
    for k, c in res["contrasts"].items():
        if not k.startswith(("W1|", "W2|")):
            continue
        for name, sl in c.get("slices", {}).items():
            L.append(
                f"* {k} {name}: n={sl['n_rows']} d={sl['d_crps_int']:+.4f}{'' if sl['binding'] else ' (descriptive)'}"
            )
    L += ["", "## Secondary (descriptive, uncorrected): continuous CRPS and threshold log loss", ""]
    for k, c in res["contrasts"].items():
        L.append(
            f"* {k}: cont {_ci_txt(c['d_crps_cont'])}; tll_low {_ci_txt(c['d_tll_low'])}; ECE P(stat>=N) {json.dumps(c['ece_pge'])}"
        )
    if res.get("w1_vs_w2"):
        L += ["", "## W1 - W2 (underpowered contrast, no claim)", ""]
        for k, c in res["w1_vs_w2"].items():
            L.append(f"* {k}: {_ci_txt(c)}")
    if "h2" in res:
        L += ["", "## H2: injury-Elo win log loss, extended minus current window (descriptive)", ""]
        for s, r in res["h2"].items():
            if s == "note":
                continue
            L.append(
                f"* {s}: n={r['n_games']} games; LL {r['logloss_w0']:.4f} -> {r['logloss_ext']:.4f}; d LL {_ci_txt(r['d_logloss'])}; d Brier {_ci_txt(r['d_brier'], 5)}; supported={r['supported']}"
            )
        L.append(f"* {res['h2']['note']}")
    L += ["", "## Choices the frozen text leaves open", ""] + [
        f"* {c}" for c in IMPLEMENTATION_CHOICES
    ]
    return "\n".join(L) + "\n"


def draft_ledger_rows(res: dict[str, Any], sha: str) -> list[str]:
    rows: list[str] = []
    for k, c in res["contrasts"].items():
        arm, stat, season = k.split("|")
        if arm not in FAMILY_ARMS:
            continue
        d = c["d_crps_int"]
        rows.append(
            f"| T??? | DRAFT | HISTORY_WINDOW (frozen sha256 {sha[:8]}) | {stat} {season} {arm} vs W0, integer-support CRPS, n={c['n_rows']} rows / {c['n_games']} games | "
            f"{d['point']:+.4f} | {d['lo']:+.4f} | {d['hi']:+.4f} | game | BH p {c['pass_checks']['bh_p']:.3g} | MDE {2.8 * d['se']:.4f} | not holdout (<=2024) |"
        )
    return rows


# --------------------------------------------------------------------------- CLI


def _write_stop(out_dir: Path, e: Stop) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "STOP.json").write_text(json.dumps({"code": e.code, "detail": e.detail}, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("gate", "smoke", "run", "report"))
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--history-db", default="data/history/nba_history.duckdb")
    ap.add_argument("--doc", default=str(DOC))
    ap.add_argument("--sha", required=True, help="full 64-hex frozen-section sha256")
    ap.add_argument("--config", default="configs/context_residual.yaml")
    ap.add_argument("--elo-config", default="configs/mov_elo_tuned.yaml")
    ap.add_argument("--injury-config", default="configs/injury_elo.yaml")
    ap.add_argument(
        "--schedule-dir", default=None, help="current-season tips (default data/schedule)"
    )
    ap.add_argument("--history-schedule", default=str(HIST_SCHEDULE))
    ap.add_argument("--games-dir", default=str(HIST_GAMES))
    ap.add_argument("--manifest-dir", default=str(MANIFESTS))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--cache-dir", default=str(CACHE))
    ap.add_argument("--report-md", default=str(REPORT_MD))
    ap.add_argument("--spot-check-done", action="store_true")
    ap.add_argument(
        "--smoke-month",
        default=None,
        help="YYYY-MM of the single block (default: first test block)",
    )
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    a = ap.parse_args(argv)
    sha = require_frozen(Path(a.doc), a.sha)
    out = Path(a.out_dir)
    if a.stage == "report":
        res = json.loads((out / "results.json").read_text())
        gate = json.loads((out / "gate.json").read_text()) if (out / "gate.json").exists() else None
        audit = (
            json.loads((out / "audit.json").read_text()) if (out / "audit.json").exists() else None
        )
        Path(a.report_md).write_text(render_report(res, gate, audit, sha))
        print("wrote", a.report_md)
        return 0
    try:
        if a.stage == "run":
            gate_prev = require_gate(out)
            if not a.spot_check_done:
                raise SystemExit(
                    "REFUSED: audit item 4 needs the manual 20-date PDF spot check; pass --spot-check-done"
                )
        w = load_world(
            a.db, a.history_db, a.config, a.elo_config, a.schedule_dir, a.history_schedule
        )
        fr = build_frames(w)
        if a.stage == "gate":
            gate = run_gate(w, fr, a.history_db, a.db, out, Path(a.games_dir), Path(a.manifest_dir))
            print(
                json.dumps(
                    {
                        k: gate[k]
                        for k in (
                            "gate_pass",
                            "failed_items",
                            "audit_stops",
                            "w1r_required",
                            "audit_sha256",
                        )
                    },
                    indent=1,
                )
            )
            return 0 if gate["gate_pass"] else 3
        if a.stage == "smoke":
            blk = fr.f0.filter(pl.col("season").is_in(list(TEST_SEASONS)))["game_date"].min()
            assert isinstance(blk, dt.date)
            ym = (
                tuple(int(x) for x in a.smoke_month.split("-"))
                if a.smoke_month
                else (blk.year, blk.month)
            )
            res = experiment(
                w,
                fr,
                gate=None,
                inj_config=a.injury_config,
                cache=None,
                n_boot=min(a.n_boot, 200),
                stats=("pts",),
                only_block=(ym[0], ym[1]),
                repro=False,
                with_h2=False,
            )
            print(
                json.dumps(
                    {"key_log": res["key_log"], "mean_crps_int": res["smoke_mean_crps_int"]},
                    indent=1,
                )
            )
            return 0
        with heavy_lock():
            res = experiment(
                w,
                fr,
                gate=gate_prev,
                inj_config=a.injury_config,
                cache=Path(a.cache_dir),
                n_boot=a.n_boot,
            )
        res["spot_check_done"] = True
        res["ledger_rows_draft"] = draft_ledger_rows(res, sha)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
        print(json.dumps(res["verdict"], indent=1))
        return 0
    except Stop as e:
        _write_stop(out, e)
        print(str(e), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
