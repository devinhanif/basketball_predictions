# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""LINEUPS_KNOWN_REBASE harness (rule: docs/prereg/LINEUPS_KNOWN_REBASE.md, FROZEN a48ef7b3 at 31c94e1; Amendment 1 sha256 876e5804). DESCRIPTIVE.

N1 T30 - P0 (raw readout, integer CRPS), N2 T30F - FULL (FULL pi, mixture), N3 overlap = 1 - (T30F - FULL) / (T30M - A0), N4 P(play) RT - R
(log loss; all rows and bench tonight). Each four ways: headline (box-score flag), excl-late (slice), pess2 (rebuilt in train and test), P2S
(trained on headline flags, served pess2 flags on test rows). Stops: reproduction + frame identity (check 1) and plants (check 3). Flags:
starter-flag shuffle (check 2), bridge vs T085 (check 4), missingness (check 5), FORWARD tripwire.

    M="-m research.eval.lineups_known_rebase"
    .venv/bin/python -W ignore $M pins                # the two frame pins only (no fit, light)
    .venv/bin/python -W ignore $M run --sha <64 hex>  # gate -> arms -> score -> report under ONE data/ops/heavy.lock
    .venv/bin/python -W ignore $M gate|arms|score|report --sha <64 hex>
    .venv/bin/python -W ignore $M dryrun --out <dir>  # synthetic world, every stage end to end; nothing frozen read or written

Refuses unless the doc above `=== RESULTS BELOW ===` hashes to `--sha` and the commit `prereg(LINEUPS_KNOWN_REBASE): <sha prefix>` carries a
copy of the doc with that hash. `nba.duckdb` read-only; season <= 2024 asserted; every real-tip read capped at season 2024 (`tip_cap`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import lightgbm as lgb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, crps_from_quantiles, month_blocks
from nba.eval.lower_tail_eval import collect_pi, production_grid, randomized_pit
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    stat_feature_names,
    to_integer_support,
)
from nba.props.lineup_features import (
    MIN_WINDOW_GAMES,
    T30_FEATURES,
    USUAL_START_FRAC,
    WINDOW_GAMES,
    build_lineup_features,
)
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_eval as RR
from research.eval import recent_role_full_eval as RRF
from research.eval import workload_eval as WE

ID = "LINEUPS_KNOWN_REBASE"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/LINEUPS_KNOWN_REBASE.md"
DOC = ROOT / DOC_REL
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
OUT_DIR = ROOT / "reports/lineups_known_rebase"
REPORT_MD = ROOT / "reports/lineups_known_rebase.md"
CACHE = ROOT / "data/ops/lineups_known_rebase_cache"
LT_DIR = ROOT / "reports/lower_tail"
FULL_CACHE = ROOT / "data/ops/recent_role_full_cache"
RR_SCORES = ROOT / "data/ops/recent_role_cache/scores.npz"
DB = str(ROOT / "nba.duckdb")

STATS: tuple[str, ...] = tuple(PROP_STATS)
SEASONS = (2023, 2024)
KEYS = ["game_id", "player_id"]
SEED = 0
#: Amendment 1 pins (recipe with t30_vac_starter_min rounded to 1e-9; superseding the raw 9359bcf4 / 9a38c78a, which no rebuild can reproduce)
PROPS_PIN = "231e864858a8b067ecf9c462d104d5a20246cd29bfb5ff88271376e41c92b24a"
PPLAY_PIN = "372dacf34f275f6743d4a79d717de582d86dafcdfddfeda587c0552ac4c14ef1"
PROPS_ROWS, PPLAY_ROWS = 55_320, 66_220
LATE_FROZEN = {2022: 572, 2023: 536, 2024: 564}  # gate re-measure 19:05 (reported, not a stop)
#: t30_vac_starter_min is not bit-reproducible (polars group_by sum order in nba/props/lineup_features.py, jitter about 3e-14); Amendment 1
#: rounds it to VAC_DECIMALS in the pin recipe and in check 3's plant comparison only. Fitted features are never rounded.
VAC = "t30_vac_starter_min"
VAC_DECIMALS = 9


def canon(df: pl.DataFrame) -> pl.DataFrame:
    """Amendment 1 identity canonicalisation: t30_vac_starter_min rounded to 1e-9 (if present); every other column untouched."""
    return df.with_columns(pl.col(VAC).round(VAC_DECIMALS)) if VAC in df.columns else df


R5: tuple[str, ...] = PR.R_COLS
T30: tuple[str, ...] = tuple(T30_FEATURES)
P2 = "p2_"  # pess2 t30 columns
SH = "sh_"  # starter-flag shuffle t30 columns
FLOOR = -0.005
REPRO_TOL = 1e-6
SHUF_FLAG = -0.002  # check 2: flag if point <= -0.002 with CI upper < 0
OVERLAP_DEN = -0.002  # N3 reported only if T30M - A0 CI upper < -0.002
T085_PTS_2024 = -0.0412
BRIDGE_GAP = 0.008
TRIPWIRE = {
    "pts": -0.041,
    "reb": -0.018,
    "ast": -0.0089,
}  # FORWARD section 2 direction check (frozen there)
L60_DATES, Z_LOOK, LOGGED_SHARE = 60, 2.36, 0.85  # FORWARD section 6 method
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
MIN_SLICE_N = 300
WAYS = ("headline", "pess2", "P2S")

# props heads arm -> (feature list builder, serve map: test rows read the mapped column in place of the named one)
SERVE_P2 = {c: P2 + c for c in T30}


def heads_names(stat: str, arm: str) -> list[str]:
    base = stat_feature_names(stat)
    return {
        "P0C": base,
        "FULLC": [*base, *R5],
        "T30": [*base, *T30],
        "T30F": [*base, *R5, *T30],
        "T30_P2": [*base, *(P2 + c for c in T30)],
        "T30F_P2": [*base, *R5, *(P2 + c for c in T30)],
        "T30_P2S": [*base, *T30],
        "T30F_P2S": [*base, *R5, *T30],
        "T30SH": [*base, *(SH + c for c in T30)],
    }[arm]


SERVED = {"T30_P2S": SERVE_P2, "T30F_P2S": SERVE_P2, "RT_P2S": SERVE_P2}
GATE_HEADS = ("P0C", "FULLC")
CLAIM_HEADS = ("T30", "T30F", "T30_P2", "T30F_P2", "T30_P2S", "T30F_P2S", "T30SH")


def pplay_arms() -> dict[str, list[str]]:
    r = RR.arm_columns()["R"]
    return {
        "R": r,
        "RT": [*r, *T30],
        "RT_P2": [*r, *(P2 + c for c in T30)],
        "RT_SH": [*r, *(SH + c for c in T30)],
        "RT_P2S": [*r, *T30],
    }


DESIGN = {
    "pess2_demotion": "k late-resolved usual starters in a team-game demote the k box-score starters with the fewest prior starts this season (the player's starts in earlier games of the same season, any team), ties lowest player_id",
    "shuffle": "starter flags permuted among ALL box rows of each (game, team) (keeps the count), one draw per team-game from default_rng(0) in (game_id, team_id) order",
    "bench_tonight_N4": "bench tonight = the flag the arm is served (headline t30_starter for RT/RT_SH, pess2 flag for RT_P2/RT_P2S); the headline-flag bench row is also printed",
    "range_low_end": "the rule's [min(pess2, P2S), headline]: the low end is read as the WEAKER worst case (the gain smaller in magnitude, i.e. the numerically larger of the two); the literal numerical min is printed beside it",
    "l60_power": "P(point <= -0.005 and 98.18% CI upper < 0) = Phi((min(-0.005, -2.36 SE) - effect) / SE), SE = date-clustered 2024 SE x sqrt(D / 60) / sqrt(0.85); FORWARD's Phi(|effect|/SE - 2.36) printed beside it",
    "slices": "N1/N2 slices use the headline flags (status changed, starter / bench tonight); late-resolved team-games from the gate definition",
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


# --------------------------------------------------------------------------- identity, freeze, tip cap
def frame_sha(df: pl.DataFrame, cols: list[str]) -> str:
    """Frozen recipe: rows sorted by (game_id, player_id); keys as newline-joined text; other columns in name order cast to Float64 (null ->
    NaN), little-endian bytes, each prefixed by its name and a NUL."""
    d = df.with_columns(pl.col("game_id").cast(pl.Utf8)).sort(KEYS)
    h = hashlib.sha256()
    for k in KEYS:
        h.update(k.encode() + b"\0" + "\n".join(map(str, d[k].to_list())).encode())
    for c in sorted(set(cols) - set(KEYS)):
        a = d[c].cast(pl.Float64, strict=False).to_numpy().astype("<f8")
        a = np.where(np.isnan(a), np.float64("nan"), a)
        h.update(c.encode() + b"\0" + a.tobytes())
    return h.hexdigest()


def key_hash(gid: np.ndarray, pid: np.ndarray) -> str:
    """L5: order-free hash of a scored row set."""
    lines = sorted(
        f"{g}\t{int(p)}" for g, p in zip(np.asarray(gid).astype(str), np.asarray(pid), strict=True)
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def props_hash_cols() -> list[str]:
    return sorted(
        {c for st in STATS for c in stat_feature_names(st, lineups_known=True)}
        | set(R5)
        | {*STATS, "minutes"}
    )


def keyed(fr: pl.DataFrame) -> pl.DataFrame:
    return fr.with_columns(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64))


def rows_of(fr: pl.DataFrame, gid: np.ndarray, pid: np.ndarray) -> pl.DataFrame:
    keys = pl.DataFrame(
        {"game_id": np.asarray(gid).astype(str), "player_id": np.asarray(pid).astype(np.int64)}
    )
    j = keys.join(keyed(fr), on=KEYS, how="left")
    if j.height != keys.height:
        raise RuntimeError("keys are not unique in the frame")
    return j


def frozen_sha(doc: Path) -> str:
    return WE.frozen_sha(doc)


def freeze_commit(sha: str, git: Callable[..., str] = _git, doc_rel: str = DOC_REL) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.match(rf"prereg\({re.escape(ID)}\): ([0-9a-f]{{8,64}})\b", subj.strip())
        if not m or not sha.startswith(m.group(1)):
            continue
        keep: list[str] = []
        for ln in git("show", f"{h}:{doc_rel}").splitlines(keepends=True):
            if ln.rstrip("\n") == MARKER:
                break
            keep.append(ln)
        if hashlib.sha256("".join(keep).encode()).hexdigest() == sha:
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, git: Callable[..., str] = _git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha[:8]}` whose doc hashes to {sha[:8]}: not frozen; no arm may be fitted"
        )
    return sha


def freeze_time(sha: str, git: Callable[..., str] = _git) -> float | None:
    h = freeze_commit(sha, git)
    out = git("log", "-1", "--format=%ct", h).strip() if h else ""
    return float(out) if out else None


@contextmanager
def tip_cap(cap: int = MAX_SEASON) -> Iterator[list[int | None]]:
    """Every real-tip read inside goes through `build_game_tipoff(max_season <= 2024)`: None is capped to 2024, above 2024 raises."""
    from nba.features import game_tipoff as GT

    orig = GT.build_game_tipoff
    calls: list[int | None] = []

    def capped(schedule_dir: Any = None, max_season: int | None = None) -> pl.DataFrame:
        calls.append(max_season)
        if max_season is not None and max_season > cap:
            raise RuntimeError(
                f"tip read with max_season={max_season} > {cap}: holdout schedule file"
            )
        return orig(schedule_dir, max_season=cap if max_season is None else max_season)

    with mock.patch.object(GT, "build_game_tipoff", capped):
        yield calls


# --------------------------------------------------------------------------- late-resolved, pess2, shuffle (the starter-flag variants)
def usual_absences(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """Usual starters (>= 50% of the team's previous <= 10 games of the season, >= 3 games) absent from tonight's box-score five."""
    box = pgs.select(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
        pl.col("starter").fill_null(False).cast(pl.Boolean),
    )
    box = box.join(
        games.select(pl.col("game_id").cast(pl.Utf8), pl.col("game_date").cast(pl.Date), "season"),
        on="game_id",
    )
    tg = (
        box.select("game_id", "team_id", "game_date", "season")
        .unique()
        .sort(["team_id", "season", "game_date", "game_id"])
        .with_columns(pl.int_range(pl.len()).over(["team_id", "season"]).alias("tg_no"))
    )
    st = box.filter(pl.col("starter")).join(
        tg.select("game_id", "team_id", "tg_no"), on=["game_id", "team_id"]
    )
    rows: list[tuple[str, int, int, int]] = []
    starts_by: dict[tuple[int, int], dict[int, set[int]]] = {}
    for team, season, t, p in st.select("team_id", "season", "tg_no", "player_id").iter_rows():
        starts_by.setdefault((team, season), {}).setdefault(t, set()).add(p)
    for team, season, gid, t in tg.select("team_id", "season", "game_id", "tg_no").iter_rows():
        win = list(range(max(0, t - WINDOW_GAMES), t))
        if len(win) < MIN_WINDOW_GAMES:
            continue
        sb = starts_by.get((team, season), {})
        cnt: dict[int, int] = {}
        for k in win:
            for p in sb.get(k, ()):
                cnt[p] = cnt.get(p, 0) + 1
        for p in sorted(p for p, c in cnt.items() if c >= USUAL_START_FRAC * len(win)):
            if p not in sb.get(t, set()):
                rows.append((gid, team, season, p))
    return pl.DataFrame(
        rows,
        schema={"game_id": pl.Utf8, "team_id": pl.Int64, "season": pl.Int64, "player_id": pl.Int64},
        orient="row",
    )


def late_resolved(
    games: pl.DataFrame, pgs: pl.DataFrame, flagged: dict[str, set[int]], pretip: pl.DataFrame
) -> pl.DataFrame:
    """Rule definition: a usual starter absent from tonight's box five, NOT OUT on the real-tip T-60 report, who did NOT play: dressed with 0
    minutes, or no box row AND a pre-tip `player_availability` row for this game (`pretip`: game_id, player_id). Post-tip by construction."""
    ab = usual_absences(games, pgs)
    box = pgs.select(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        (pl.col("minutes").fill_null(0.0) > 0).alias("played"),
        pl.lit(True).alias("in_box"),
    )
    ab = ab.join(box, on=KEYS, how="left").with_columns(
        pl.col("played").fill_null(False), pl.col("in_box").fill_null(False)
    )
    out60 = pl.DataFrame(
        [(g, int(p)) for g, ps in flagged.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    ).with_columns(pl.lit(True).alias("out_t60"))
    pre = (
        pretip.select(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64))
        .unique()
        .with_columns(pl.lit(True).alias("pretip_row"))
    )
    ab = (
        ab.join(out60, on=KEYS, how="left")
        .join(pre, on=KEYS, how="left")
        .with_columns(pl.col("out_t60").fill_null(False), pl.col("pretip_row").fill_null(False))
    )
    cat = (
        pl.when(pl.col("out_t60")).then(pl.lit("out_t60")).when(pl.col("played")).then(pl.lit("benched_played"))
        .when(pl.col("in_box")).then(pl.lit("late_resolved_dressed_dnp")).when(pl.col("pretip_row")).then(pl.lit("late_resolved_nobox_pretip_row"))
        .otherwise(pl.lit("excluded_nobox_no_pretip_row"))
    )  # fmt: skip
    return ab.with_columns(cat.alias("cat")).with_columns(
        pl.col("cat").str.starts_with("late_resolved").alias("is_late")
    )


def pess2_pgs(games: pl.DataFrame, pgs: pl.DataFrame, late: pl.DataFrame) -> pl.DataFrame:
    """Red-team worst case: every late-resolved usual starter is flagged an announced starter (a DNP starter row with 0 minutes is added for
    one without a box row) and, for each, the box-score starter of that team-game with the fewest prior starts this season (ties: lowest
    player_id) is flagged bench-announced. Added rows feed the `t30_*` team features only (they are never scored rows)."""
    lt = late.filter(pl.col("is_late")).select("game_id", "team_id", "player_id")
    base = pgs.with_columns(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
        pl.col("starter").fill_null(False).cast(pl.Boolean),
    )
    if lt.is_empty():
        return base
    dated = base.join(
        games.select(pl.col("game_id").cast(pl.Utf8), pl.col("game_date").cast(pl.Date), "season"),
        on="game_id",
    )
    prior = dated.sort(["player_id", "season", "game_date", "game_id"]).with_columns(
        (
            pl.col("starter").cast(pl.Int64).cum_sum().over(["player_id", "season"])
            - pl.col("starter").cast(pl.Int64)
        ).alias("prior_starts")
    )
    k = lt.group_by(["game_id", "team_id"]).len().rename({"len": "k"})
    cand = (
        prior.filter(pl.col("starter"))
        .join(k, on=["game_id", "team_id"])
        .sort(["game_id", "team_id", "prior_starts", "player_id"])
        .with_columns(pl.int_range(pl.len()).over(["game_id", "team_id"]).alias("_r"))
    )
    demote = cand.filter(pl.col("_r") < pl.col("k")).select(
        "game_id", "player_id", pl.lit(True).alias("_demote")
    )
    promote = lt.select("game_id", "player_id", pl.lit(True).alias("_promote"))
    out = base.join(demote, on=KEYS, how="left").join(promote, on=KEYS, how="left")
    out = out.with_columns(
        pl.when(pl.col("_promote").fill_null(False))
        .then(True)
        .when(pl.col("_demote").fill_null(False))
        .then(False)
        .otherwise(pl.col("starter"))
        .alias("starter")
    ).drop("_demote", "_promote")
    missing = lt.join(base.select(KEYS), on=KEYS, how="anti")
    if missing.height:
        add = missing.with_columns(pl.lit(0.0).alias("minutes"), pl.lit(True).alias("starter"))
        add = add.with_columns(
            [
                pl.lit(None, dtype=out.schema[c]).alias(c)
                for c in out.columns
                if c not in add.columns
            ]
        ).select(out.columns)
        out = pl.concat([out, add.cast(out.schema)], how="vertical")
    return out


def shuffle_pgs(pgs: pl.DataFrame, seed: int = SEED) -> pl.DataFrame:
    """Starter flags permuted within each (game, team) among all box rows (the count is kept)."""
    a = pgs.with_columns(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        pl.col("starter").fill_null(False).cast(pl.Boolean),
    ).sort(["game_id", "team_id", "player_id"])
    gi, ti = a["game_id"].to_numpy(), a["team_id"].to_numpy()
    grp = (
        np.cumsum(np.r_[True, (gi[1:] != gi[:-1]) | (ti[1:] != ti[:-1])])
        if a.height
        else np.zeros(0, int)
    )
    u = np.random.default_rng(seed).random(a.height)
    order = np.lexsort((u, grp))
    flags = a["starter"].to_numpy()
    new = np.empty_like(flags)
    new[order] = flags
    return a.with_columns(pl.Series("starter", new))


def t30_cols(games: pl.DataFrame, pgs: pl.DataFrame, prefix: str = "") -> pl.DataFrame:
    t = build_lineup_features(games, pgs).drop("team_id")
    t = t.with_columns(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64))
    return t.rename({c: prefix + c for c in T30}) if prefix else t


# --------------------------------------------------------------------------- world
@dataclass
class Ctx:
    out_dir: Path
    cache: Path
    cfg: ContextResidualConfig | None = None
    pcfg: ContextResidualConfig | None = None
    world_fn: Callable[[], dict[str, Any]] | None = None
    comps: dict[str, dict[str, np.ndarray]] = field(
        default_factory=dict
    )  # stored A0 components per stat
    pic: dict[str, np.ndarray] | None = None
    full_heads: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    full_pi: np.ndarray | None = None
    rr: dict[str, np.ndarray] | None = None  # stored RECENT_ROLE scores (p_R)
    props_pin: str = PROPS_PIN
    pplay_pin: str = PPLAY_PIN
    pins_rows: tuple[int, int] = (PROPS_ROWS, PPLAY_ROWS)
    holdout_text: str = ""
    real_plants: bool = True
    frozen_at: float | None = None
    sha: str = "dryrun"
    stamp: dict[str, str] = field(
        default_factory=dict
    )  # review m3: gate stamps (whole-frame hashes + harness hash)
    _w: Any = None

    def world(self) -> dict[str, Any]:
        if self._w is None:
            assert self.world_fn is not None
            self._w = self.world_fn()
        return self._w


def build_world() -> dict[str, Any]:
    """Real inputs (read-only, season <= 2024, tips capped): props frame (production + R5 + headline / pess2 / shuffled `t30_*`), P(play)
    R frame (+ the same three `t30_*` sets), the late-resolved table, games and box rows for the plants."""
    from nba.eval.context_residual_eval import load_inputs
    from nba.features.game_tipoff import attach_real_tips
    from nba.props.context_residual import flagged_from_availability
    from research.eval import four_factors_eval as F
    from research.workload import asof as W

    with tip_cap():
        study = F.load_study(DB)
        games, pgs, _static, avail = load_inputs(DB, None)
        cfg = study["cfg"]
        flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
        av = avail.filter(pl.col("player_id").is_not_null()).select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            pl.col("as_of").cast(pl.Datetime("us")),
        )
        av = attach_real_tips(av, max_season=MAX_SEASON)
        src = WE.load_sources(DB)
        pfr, pcfg, _ = RR.build_frame()
    if int(games["season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 loaded")
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    pretip = (
        av.filter(
            pl.col("tip_et").is_not_null()
            & (pl.col("as_of") < pl.col("tip_et").cast(pl.Datetime("us")))
        )
        .select(KEYS)
        .unique()
    )
    feats = keyed(study["feats"])
    load = W.player_load_features(src["played"], feats.select("game_id", "player_id", "game_date"))
    load = keyed(
        load.rename({c: W.PREFIX + c for c in load.columns if c in W.ALL_COLS}).select(*KEYS, *R5)
    )
    props = feats.with_row_index("_ord").join(load, on=KEYS, how="left").sort("_ord").drop("_ord")
    return assemble(
        games,
        pgs,
        props,
        keyed(pfr),
        flagged,
        pretip,
        cfg,
        pcfg,
        {"null_tip_avail_rows": int(av["tip_et"].is_null().sum()), "src": src},
    )


def assemble(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    props: pl.DataFrame,
    pplay: pl.DataFrame,
    flagged: dict[str, set[int]],
    pretip: pl.DataFrame,
    cfg: ContextResidualConfig,
    pcfg: ContextResidualConfig,
    extra: dict[str, Any],
) -> dict[str, Any]:
    late = late_resolved(games, pgs, flagged, pretip)
    t_h, t_p2, t_sh = (
        t30_cols(games, pgs),
        t30_cols(games, pess2_pgs(games, pgs, late), P2),
        t30_cols(games, shuffle_pgs(pgs), SH),
    )
    late_tg = (
        late.filter(pl.col("is_late"))
        .select("game_id", "team_id")
        .unique()
        .with_columns(pl.lit(True).alias("late_tg"))
    )
    teams = keyed(pgs.select("game_id", "player_id", pl.col("team_id").cast(pl.Int64))).unique(KEYS)

    def attach(fr: pl.DataFrame) -> pl.DataFrame:
        """Left joins with the input row order restored explicitly (walk order feeds LightGBM bagging)."""
        n = fr.height
        dup = sorted(set(T30) & set(fr.columns))
        out = keyed(fr.drop(dup)).with_row_index("_ord")
        if "team_id" in out.columns:
            out = out.with_columns(pl.col("team_id").cast(pl.Int64))
        else:
            out = out.join(teams, on=KEYS, how="left")
        out = (
            out.join(t_h, on=KEYS, how="left")
            .join(t_p2, on=KEYS, how="left")
            .join(t_sh, on=KEYS, how="left")
        )
        out = out.join(late_tg, on=["game_id", "team_id"], how="left").with_columns(
            pl.col("late_tg").fill_null(False)
        )
        if out.height != n:
            raise RuntimeError("attaching t30 columns changed the rows")
        return out.sort("_ord").drop("_ord")

    props_fr, pplay_fr = attach(props), attach(pplay)
    WE.assert_seasons(props_fr)
    WE.assert_seasons(pplay_fr)
    return {
        "games": games,
        "pgs": pgs,
        "props": props_fr,
        "pplay": pplay_fr,
        "late": late,
        "cfg": cfg,
        "pcfg": pcfg,
        **extra,
    }


def real_ctx(sha: str | None) -> Ctx:
    def load(p: Path) -> dict[str, np.ndarray]:
        z = np.load(p, allow_pickle=True)
        return {k: z[k] for k in z.files}

    return Ctx(
        out_dir=OUT_DIR, cache=CACHE, world_fn=build_world,
        comps={st: load(LT_DIR / f"components_{st}.npz") for st in STATS}, pic=load(LT_DIR / "components_pi.npz"),
        full_heads={st: load(FULL_CACHE / f"heads_{st}_R.npz") for st in STATS}, full_pi=np.load(FULL_CACHE / "pi_R.npz")["pi"], rr=load(RR_SCORES),
        holdout_text=HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else "", frozen_at=None if sha is None else freeze_time(sha), sha=sha or "none",
    )  # fmt: skip


# --------------------------------------------------------------------------- walks
def _served_model(serve: dict[str, str]) -> type[ContextResidualModel]:
    class Served(ContextResidualModel):
        """Fit unchanged (training rows read the named columns); TEST rows are predicted with each named column replaced by its served column."""

        def predict_components(self, df: pl.DataFrame) -> Any:
            return super().predict_components(
                df.with_columns([pl.col(v).alias(k) for k, v in serve.items()])
            )

    return Served


def heads_walk(
    fr: pl.DataFrame, stat: str, cfg: ContextResidualConfig, arm: str, log: bool = True, **kw: Any
) -> dict[str, Any]:
    """`recent_role_full_eval.collect_heads` with the arm's feature list; P2S arms run the same walk with test rows served pess2 columns."""
    names = heads_names(stat, arm)
    if arm not in SERVED:
        return RRF.collect_heads(fr, stat, cfg, names, log=log, **kw)
    with mock.patch.object(RRF, "ContextResidualModel", _served_model(SERVED[arm])):
        return RRF.collect_heads(fr, stat, cfg, names, log=log, **kw)


def pplay_walk_served(
    frame: pl.DataFrame,
    arms: dict[str, list[str]],
    serve: dict[str, dict[str, str]],
    cfg: ContextResidualConfig,
    min_train: int = RR.MIN_TRAIN,
) -> dict[str, np.ndarray]:
    """`recent_role_eval.walk` (no shuffle) where an arm in `serve` predicts its test block with the served columns. With an empty `serve` it is
    bit-identical to `recent_role_eval.walk` (unit-tested)."""
    WE.assert_seasons(frame)
    test_all = frame.filter(pl.col("season").is_in(list(RR.TEST_SEASONS)))
    parts: list[dict[str, np.ndarray]] = []
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        block = frame.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(RR.TEST_SEASONS))
        )
        train = frame.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < min_train:
            continue
        ytr = train["played"].to_numpy().astype(int)
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy().astype(str),
            "pid": block["player_id"].to_numpy().astype(np.int64),
            "season": block["season"].to_numpy(),
            "y": block["played"].to_numpy(),
            "blk": np.full(block.height, bi),
        }
        for arm, names in arms.items():
            clf = lgb.LGBMClassifier(**_params(cfg, "binary")).fit(_matrix(train, names), ytr)
            tb = (
                block.with_columns([pl.col(v).alias(k) for k, v in serve.get(arm, {}).items()])
                if arm in serve
                else block
            )
            part[f"p_{arm}"] = np.asarray(clf.predict_proba(_matrix(tb, names)))[:, 1]
        parts.append(part)
        _log(
            f"pplay block {bi + 1}/{len(blocks)} {start} n_test={block.height} train={train.height}"
        )
    if not parts:
        raise ValueError("no month block had enough training rows")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def align(
    res: dict[str, np.ndarray], gid: np.ndarray, pid: np.ndarray, cols: list[str]
) -> dict[str, np.ndarray]:
    mine = pl.DataFrame(
        {
            "game_id": res["gid"].astype(str),
            "player_id": res["pid"].astype(np.int64),
            **{c: res[c] for c in cols},
        }
    )
    want = pl.DataFrame(
        {"game_id": np.asarray(gid).astype(str), "player_id": np.asarray(pid).astype(np.int64)}
    )
    j = want.join(mine, on=KEYS, how="left")
    if j.height != want.height or any(j[c].null_count() for c in cols):
        raise RuntimeError("the walk's rows do not cover the stored R rows")
    return {c: j[c].to_numpy() for c in cols}


STAMP_KEYS = ("props_all_sha256", "pplay_all_sha256", "harness_git_hash")


def canon_all(df: pl.DataFrame) -> pl.DataFrame:
    """Secondary-hash canonicalisation: every copy of t30_vac_starter_min (headline, pess2, shuffled) rounded to 1e-9 (same jitter)."""
    cols = [c for c in (VAC, P2 + VAC, SH + VAC) if c in df.columns]
    return df.with_columns([pl.col(c).round(VAC_DECIMALS) for c in cols])


def whole_hash(df: pl.DataFrame, cols: list[str]) -> str:
    """Review m3: the frozen pins cover the scored rows only (the frozen recipe; not extended); this SECONDARY hash covers every row
    (training-only rows included) and every column an arm reads, plus season and date. Written to gate.json and every checkpoint."""
    d = canon_all(df)
    if "game_date" in d.columns:
        d = d.with_columns(pl.col("game_date").cast(pl.Date).cast(pl.Int32))
    use = sorted({c for c in [*cols, "season", "game_date"] if c in d.columns} - set(KEYS))
    return frame_sha(d.select(*KEYS, *use), use)


def harness_hash(path: Path | None = None) -> str:
    """`git hash-object` of this file (blob sha1), computed without git."""
    data = (path or Path(__file__)).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def world_stamp(w: dict[str, Any]) -> dict[str, str]:
    pcols = [*props_hash_cols(), *(P2 + c for c in T30), *(SH + c for c in T30)]
    rcols = [*pplay_arms()["R"], "played", *T30, *(P2 + c for c in T30), *(SH + c for c in T30)]
    return {
        "props_all_sha256": whole_hash(w["props"], pcols),
        "pplay_all_sha256": whole_hash(w["pplay"], rcols),
        "harness_git_hash": harness_hash(),
    }


def save(ctx: Ctx, path: Path, obj: dict[str, Any]) -> None:
    if set(ctx.stamp) != set(STAMP_KEYS):
        raise RuntimeError("no gate stamp in this context: run the gate first")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {**obj, **{f"_stamp_{k}": np.array(v) for k, v in ctx.stamp.items()}}
    np.savez_compressed(path, **payload)


def check_stamp(ctx: Ctx, path: Path, z: dict[str, np.ndarray]) -> None:
    for k in STAMP_KEYS:
        got = str(z[f"_stamp_{k}"]) if f"_stamp_{k}" in z else None
        if got != ctx.stamp.get(k):
            raise RuntimeError(
                f"{path}: checkpoint stamp {k}={got and got[:12]} != gate {str(ctx.stamp.get(k))[:12]}: stale checkpoint (different frame or harness); delete the cache and rerun"
            )


def load(ctx: Ctx, path: Path) -> dict[str, np.ndarray]:
    z = np.load(path, allow_pickle=True)
    d = {k: z[k] for k in z.files}
    check_stamp(ctx, path, d)
    return {k: v for k, v in d.items() if not k.startswith("_stamp_")}


def checkpoint_ok(ctx: Ctx, path: Path) -> bool:
    """Reuse only a checkpoint written after the freeze AND stamped with the gate's whole-frame hashes and harness hash; raise otherwise."""
    if not RRF.check_checkpoint(path, ctx.frozen_at):
        return False
    z = np.load(path, allow_pickle=True)
    check_stamp(ctx, path, {k: z[k] for k in z.files})
    return True


def hp(ctx: Ctx, stat: str, arm: str) -> Path:
    return ctx.cache / f"heads_{stat}_{arm}.npz"


# --------------------------------------------------------------------------- gate
def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return WE._check(name, value, threshold, ok)


def inputs_identical(new: dict[str, np.ndarray], stored: dict[str, np.ndarray]) -> bool:
    if len(new["row_gid"]) != len(stored["row_gid"]) or not RRF.same_rows(new, stored):
        return False
    return all(
        k in stored
        and np.array_equal(np.asarray(new[k], float), np.asarray(stored[k], float), equal_nan=True)
        for k in ("row_y", "row_m", "row_minutes", "row_n_prior")
    )


def pins(ctx: Ctx) -> dict[str, Any]:
    w = ctx.world()
    c0 = ctx.comps[STATS[0]]
    a0 = rows_of(w["props"], c0["row_gid"], c0["row_pid"])
    cols = props_hash_cols()
    props_sha = frame_sha(canon(a0).select(*KEYS, *cols), cols)
    assert ctx.rr is not None
    rr = rows_of(w["pplay"], ctx.rr["gid"], ctx.rr["pid"])
    pplay_sha = frame_sha(canon(rr).select(*KEYS, *T30), list(T30))
    rcols = sorted({*pplay_arms()["R"], "played", *T30})
    full_sha = frame_sha(canon(rr).select(*KEYS, *rcols), rcols)
    ok = (
        props_sha == ctx.props_pin
        and pplay_sha == ctx.pplay_pin
        and (a0.height, rr.height) == ctx.pins_rows
    )
    out = {"props_sha256": props_sha, "props_pin": ctx.props_pin, "props_rows": a0.height, "pplay_keys_t30_sha256": pplay_sha, "pplay_pin": ctx.pplay_pin, "pplay_rows": rr.height, "pplay_R_frame_sha256": full_sha, "ok": ok}  # fmt: skip
    out["raw_unrounded"] = {
        "props_sha256": frame_sha(a0.select(*KEYS, *cols), cols),
        "note": "diagnosis only; varies run to run (Amendment 1)",
    }
    return out


def _maxdiff(x: pl.Series, y: pl.Series) -> float:
    a, b = x.cast(pl.Float64).to_numpy(), y.cast(pl.Float64).to_numpy()
    both = np.isnan(a) & np.isnan(b)
    return float(np.max(np.where(both, 0.0, np.abs(a - b)), initial=0.0))


def plants(games: pl.DataFrame, pgs: pl.DataFrame) -> list[dict[str, Any]]:
    """Check 3 on the real tables: editing every game dated after a mid-2024 cutoff (starter flags flipped, minutes 48, a fake future game added)
    leaves the `t30_*` of every row dated on or before the cutoff bit-identical; flipping tonight's flags (cutoff day) changes tonight's rows."""
    g = games.with_columns(pl.col("game_id").cast(pl.Utf8), pl.col("game_date").cast(pl.Date))
    p = pgs.with_columns(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        pl.col("starter").fill_null(False).cast(pl.Boolean),
    )
    d24 = g.filter(pl.col("season") == 2024).sort("game_date")["game_date"]
    cut = d24[d24.len() // 2]
    after = set(g.filter(pl.col("game_date") > cut)["game_id"].to_list())
    today = set(g.filter(pl.col("game_date") == cut)["game_id"].to_list())
    base = t30_cols(g, p)
    pe = p.with_columns(
        pl.when(pl.col("game_id").is_in(list(after)))
        .then(~pl.col("starter"))
        .otherwise(pl.col("starter"))
        .alias("starter"),
        pl.when(pl.col("game_id").is_in(list(after)))
        .then(48.0)
        .otherwise(pl.col("minutes").cast(pl.Float64))
        .alias("minutes"),
    )
    tm = p.filter(pl.col("game_id") == sorted(today)[0]).head(10)
    fake_g = g.filter(pl.col("game_id") == sorted(today)[0]).with_columns(
        pl.lit("0099999999").alias("game_id"),
        (pl.col("game_date") + dt.timedelta(days=1)).alias("game_date"),
    )
    fake_p = tm.with_columns(pl.lit("0099999999").alias("game_id"), pl.lit(True).alias("starter"))
    fut = t30_cols(pl.concat([g, fake_g]), pl.concat([pe.cast(p.schema), fake_p.cast(p.schema)]))
    early = g.filter(pl.col("game_date") <= cut).select("game_id")
    a = base.join(early, on="game_id").sort(KEYS)
    b = fut.join(early, on="game_id").sort(KEYS)
    ca, cb = canon(a), canon(b)
    same = a.height == b.height and all(
        np.array_equal(
            ca[c].to_numpy().astype(float), cb[c].to_numpy().astype(float), equal_nan=True
        )
        for c in T30
    )  # Amendment 1: bit-identical on the canonical columns (t30_vac_starter_min rounded to 1e-9)
    # diagnosis: per-column raw max |diff| next to an UNEDITED rebuild's jitter
    a2 = t30_cols(g, p).join(early, on="game_id").sort(KEYS)
    diag = (
        {
            c: {
                "edited_max_abs_diff": _maxdiff(a[c], b[c]),
                "unedited_rebuild_jitter": _maxdiff(a[c], a2[c]),
            }
            for c in T30
        }
        if a.height == b.height == a2.height
        else {}
    )
    pf = p.with_columns(
        pl.when(pl.col("game_id").is_in(list(today)))
        .then(~pl.col("starter"))
        .otherwise(pl.col("starter"))
        .alias("starter")
    )
    tb = t30_cols(g, pf).filter(pl.col("game_id").is_in(list(today))).sort(KEYS)
    ta = base.filter(pl.col("game_id").is_in(list(today))).sort(KEYS)
    moved = (
        int((ta["t30_starter"].to_numpy() != tb["t30_starter"].to_numpy()).sum())
        if ta.height == tb.height
        else -1
    )
    return [
        _chk(
            "plant: edit every game after the cutoff + a fake future game; t30_* of rows on/before the cutoff bit-identical (t30_vac_starter_min rounded to 1e-9, Amendment 1)",
            {"cutoff": str(cut), "n_rows": a.height, "by_column": diag},
            "identical and n > 0",
            same and a.height > 0,
        ),
        _chk(
            "control: flipping the cutoff day's starter flags changes that day's t30_starter",
            {"n_rows": ta.height, "n_changed": moved},
            "> 0",
            moved > 0,
        ),
    ]


def run_gate(ctx: Ctx) -> dict[str, Any]:
    t0 = time.time()
    tests: list[dict[str, Any]] = []
    w = ctx.world()
    cfg = ctx.cfg or w["cfg"]
    pcfg = ctx.pcfg or w["pcfg"]
    pn = pins(ctx)
    tests.append(
        _chk(
            "check 1 frame identity: props A0 x 81 columns and P(play) R keys + t30_* equal the frozen pins (full R-frame hash recorded)",
            pn,
            f"props == {ctx.props_pin[:8]}, pplay == {ctx.pplay_pin[:8]}",
            pn["ok"],
        )
    )
    if not pn["ok"]:
        return _write_gate(
            ctx,
            tests,
            pn,
            t0,
            {},
            stop="a frame differs from its frozen pin: the data changed after the freeze; stop before any arm",
        )
    ctx.stamp = world_stamp(
        w
    )  # review m3: secondary whole-frame hashes + harness hash (not the frozen pins)
    fr, pfr = w["props"], w["pplay"]
    # L5: the scored row sets (A0 / R) are unchanged by the pess2 rows (added rows feed team features only)
    c0 = ctx.comps[STATS[0]]
    assert ctx.rr is not None and ctx.pic is not None and ctx.full_pi is not None
    a0j = rows_of(fr, c0["row_gid"], c0["row_pid"])
    rrj = rows_of(pfr, ctx.rr["gid"], ctx.rr["pid"])
    l5 = {
        "a0_rows_with_p2": int(a0j["p2_t30_starter"].is_not_null().sum()),
        "a0_rows": a0j.height,
        "R_rows_with_p2": int(rrj["p2_t30_starter"].is_not_null().sum()),
        "R_rows": rrj.height,
    }
    tests.append(
        _chk(
            "L5: pess2 / shuffled t30_* attach to every A0 and R row; no added DNP row enters a scored set (key hashes checked per arm in score)",
            l5,
            "all rows",
            l5["a0_rows_with_p2"] == l5["a0_rows"] and l5["R_rows_with_p2"] == l5["R_rows"],
        )
    )
    if not tests[-1]["ok"]:
        return _write_gate(ctx, tests, pn, t0, {}, stop="L5 row-set check failed before any fit")
    # reproduction (check 1)
    ctx.cache.mkdir(parents=True, exist_ok=True)
    rep: dict[str, Any] = {}
    drift = False
    for st in STATS:
        p0 = heads_walk(fr, st, cfg, "P0C")
        fu = heads_walk(fr, st, cfg, "FULLC")
        save(ctx, hp(ctx, st, "P0C"), p0)
        save(ctx, hp(ctx, st, "FULLC"), fu)
        a, b = RRF.heads_vs_stored(p0, ctx.comps[st]), RRF.heads_vs_stored(fu, ctx.full_heads[st])
        ok = (
            a["rows_equal"]
            and b["rows_equal"]
            and max(a["max_abs_diff"], b["max_abs_diff"]) <= REPRO_TOL
        )
        rep[st] = {
            "P0C": a["max_abs_diff"],
            "FULLC": b["max_abs_diff"],
            "ok": ok,
            "inputs_identical": inputs_identical(p0, ctx.comps[st])
            and inputs_identical(fu, ctx.full_heads[st]),
        }
        drift = drift or (not ok and not rep[st]["inputs_identical"])
        if not ok and rep[st]["inputs_identical"]:
            tests.append(
                _chk(
                    f"check 1 reproduction {st} with identical stored inputs",
                    rep[st],
                    f"<= {REPRO_TOL}",
                    False,
                )
            )
            return _write_gate(
                ctx,
                tests,
                pn,
                t0,
                {"drift": False, "heads": rep},
                stop="reproduction failed with identical stored inputs: a code defect (stop)",
            )
    pi_f = PR.walk_pi(fr, PR.CAND_COLS, cfg)
    pi_full = (
        PR.align_to_stored(pi_f, ctx.pic["row_gid"], ctx.pic["row_pid"]) if not drift else None
    )
    dpi = (
        float(np.abs(pi_full - ctx.full_pi).max())
        if pi_full is not None and len(pi_full) == len(ctx.full_pi)
        else float("inf")
    )
    heads_ok = all(v["ok"] for v in rep.values())
    pi_defect = dpi > REPRO_TOL and heads_ok and all(v["inputs_identical"] for v in rep.values())
    if pi_defect:  # review M1: pi_R.npz stores no inputs; the heads walks on the SAME frame reproduced with identical inputs -> not drift
        tests.append(
            _chk(
                "check 1 reproduction: FULL pi refit vs pi_R.npz while every heads reproduction holds with identical stored inputs",
                {"pi_full_max_abs_diff": dpi, "pi_mismatch_with_identical_heads_inputs": True},
                f"<= {REPRO_TOL}",
                False,
            )
        )
        return _write_gate(
            ctx,
            tests,
            pn,
            t0,
            {"drift": False, "heads": rep, "pi_full_max_abs_diff": dpi},
            stop="FULL pi reproduction failed while the heads inputs are identical: a code defect (stop)",
        )
    rr_ref = pplay_walk_served(pfr, {"R": pplay_arms()["R"]}, {}, pcfg)
    rr_al = align(rr_ref, ctx.rr["gid"], ctx.rr["pid"], ["p_R", "y"])
    dr = float(np.abs(rr_al["p_R"] - ctx.rr["p_R"]).max())
    r_inputs = bool(np.array_equal(rr_al["y"].astype(float), ctx.rr["y"].astype(float)))
    if dr > REPRO_TOL and r_inputs:
        tests.append(
            _chk(
                "check 1 reproduction: R refit vs stored p_R with identical labels",
                {"max_abs_diff": dr},
                f"<= {REPRO_TOL}",
                False,
            )
        )
        return _write_gate(
            ctx,
            tests,
            pn,
            t0,
            {"drift": drift, "heads": rep},
            stop="R reproduction failed with identical stored inputs: a code defect (stop)",
        )
    drift = drift or dpi > REPRO_TOL or dr > REPRO_TOL
    if drift:
        a0pic = collect_pi(fr, cfg)
        save(ctx, ctx.cache / "a0_pic_refit.npz", a0pic)
        pi_full = PR.align_to_stored(pi_f, a0pic["row_gid"], a0pic["row_pid"])
    save(ctx, ctx.cache / "pi_FULL.npz", {"pi": pi_full})
    save(
        ctx,
        ctx.cache / "pplay_R.npz",
        {
            "gid": rr_ref["gid"],
            "pid": rr_ref["pid"],
            "season": rr_ref["season"],
            "y": rr_ref["y"],
            "p_R": rr_ref["p_R"],
        },
    )
    tests.append(_chk("check 1 reproduction: P0C == stored components, FULL refit == cache (4 stats); FULL pi == pi_R.npz; R refit == stored p_R (or data drift: all refit, reported)", {"heads": rep, "pi_full_max_abs_diff": dpi, "pi_mismatch_with_identical_heads_inputs": False, "R_max_abs_diff": dr, "drift": drift}, f"<= {REPRO_TOL}, or drift with differing inputs", True))  # fmt: skip
    # check 3 plants (stop)
    tests += plants(w["games"], w["pgs"])
    if not all(t["ok"] for t in tests):
        return _write_gate(
            ctx, tests, pn, t0, {"drift": drift, "heads": rep}, stop="check 3 plant failed (stop)"
        )
    # check 5 missingness (flag)
    aud = WE.audit_nulls(
        a0j.filter(pl.col("season").is_in(list(SEASONS))), list(T30), WE.minutes_bucket("minutes")
    )
    tests.append(
        _chk(
            "check 5 (flag, not a stop): missingness of t30_* by realised-minutes bucket (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp", "threshold_pp")},
            "reported; over the limit = a flag beside every number",
            True,
        )
    )
    late = w["late"]
    by = {
        f"{a}|{b}": int(c)
        for a, b, c in late.group_by(["season", "cat"]).len().sort(["season", "cat"]).iter_rows()
    }
    nlate = {
        int(s): int(c)
        for s, c in late.filter(pl.col("is_late"))
        .group_by("season")
        .len()
        .sort("season")
        .iter_rows()
    }
    tests.append(
        _chk(
            "late-resolved absences per season (reported; frozen gate 572 / 536 / 564)",
            {
                "late": nlate,
                "by_cat": by,
                "matches_frozen": all(nlate.get(s) == v for s, v in LATE_FROZEN.items())
                if ctx.real_plants
                else None,
            },
            "reported",
            True,
        )
    )
    tests.append(_chk(f"HOLDOUT_ACCESS_LOG: no row names {ID}; frames <= 2024", {"rows_naming_id": sum(ID in ln for ln in ctx.holdout_text.splitlines()), "max_season": max(int(fr["season"].max()), int(pfr["season"].max())), "null_tip_avail_rows": w.get("null_tip_avail_rows")}, "== 0; <= 2024", ID not in ctx.holdout_text and max(int(fr["season"].max()), int(pfr["season"].max())) <= MAX_SEASON))  # fmt: skip
    return _write_gate(ctx, tests, pn, t0, {"drift": drift, "heads": rep, "aud_ok": aud["ok"]})


def _write_gate(
    ctx: Ctx,
    tests: list[dict[str, Any]],
    pn: dict[str, Any],
    t0: float,
    info: dict[str, Any],
    stop: str | None = None,
) -> dict[str, Any]:
    gate = {
        "tests": tests,
        "gate_ok": stop is None and all(t["ok"] for t in tests),
        "stop": stop,
        "sha256": ctx.sha,
        "pins": pn,
        **ctx.stamp,
        "info": info,
        "seconds": round(time.time() - t0, 1),
    }
    WE.write_json(ctx.out_dir / "gate.json", gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ")
            + f"{t['test']}: {json.dumps(t['value'], default=str)[:400]} ({t['threshold']})",
            flush=True,
        )
    if stop:
        print(f"STOP: {stop}", flush=True)
    return gate


def require_gate(ctx: Ctx) -> dict[str, Any]:
    p = ctx.out_dir / "gate.json"
    if not p.exists():
        raise RuntimeError("no gate.json: run the gate stage first")
    g = json.loads(p.read_text())
    if not g.get("gate_ok") or g.get("sha256") != ctx.sha:
        raise RuntimeError("the frozen gate has not passed for this rule: stop")
    now = pins(ctx)
    if any(
        now[k] != g["pins"][k]
        for k in ("props_sha256", "pplay_keys_t30_sha256", "pplay_R_frame_sha256")
    ):
        raise RuntimeError("a frame differs from the one the gate hashed: refuse")
    if any(k not in g for k in STAMP_KEYS):
        raise RuntimeError("gate.json carries no checkpoint stamp: rerun the gate")
    ctx.stamp = {k: str(g[k]) for k in STAMP_KEYS}
    cur = world_stamp(ctx.world())
    bad = [k for k in STAMP_KEYS if cur[k] != ctx.stamp[k]]
    if bad:
        raise RuntimeError(
            f"whole-frame / harness stamp differs from the gate's ({bad}): refuse (rerun the gate on an empty cache)"
        )
    return g


# --------------------------------------------------------------------------- arms
def run_arms(ctx: Ctx) -> None:
    gate = require_gate(ctx)
    w = ctx.world()
    cfg, pcfg = ctx.cfg or w["cfg"], ctx.pcfg or w["pcfg"]
    drift = bool(gate["info"].get("drift"))
    for st in STATS:
        base = load(ctx, hp(ctx, st, "P0C")) if drift else ctx.comps[st]
        for arm in CLAIM_HEADS:
            p = hp(ctx, st, arm)
            if checkpoint_ok(ctx, p):
                _log(f"{st} heads {arm} from checkpoint")
                continue
            _log(f"{st} heads walk {arm}")
            comp = heads_walk(w["props"], st, cfg, arm)
            if not RRF.same_rows(comp, base):
                raise RuntimeError(f"{st} {arm}: rows do not match the A0 rows")
            save(ctx, p, comp)
    p = ctx.cache / "pplay_arms.npz"
    if not checkpoint_ok(ctx, p):
        arms = pplay_arms()
        _log("pplay walk RT, RT_P2, RT_SH, RT_P2S (served)")
        res = pplay_walk_served(
            w["pplay"],
            {k: arms[k] for k in ("RT", "RT_P2", "RT_SH", "RT_P2S")},
            {"RT_P2S": SERVED["RT_P2S"]},
            pcfg,
        )
        save(ctx, p, res)


# --------------------------------------------------------------------------- score
def raw_qi(comp: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    q = production_grid(comp)
    return to_integer_support(q), q


def mix_qi(
    comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], pi: np.ndarray, stat: str
) -> np.ndarray:
    return WE.mixture_qi(comp, PR.candidate_pic(pic, pi), stat)


def ds(d: np.ndarray, gid: np.ndarray, date: np.ndarray, m: np.ndarray) -> dict[str, Any]:
    if m.sum() < 2:
        return {"n": int(m.sum())}
    out = WE.delta_stats(d[m], gid[m])
    dd = WE.delta_stats(d[m], date[m])
    return {
        **out,
        "n": int(m.sum()),
        "n_games": int(len(np.unique(gid[m]))),
        "date_ci": [dd["ci_lo"], dd["ci_hi"]],
        "n_dates": int(len(np.unique(date[m]))),
    }


def boot_weights(
    gid: np.ndarray, n_boot: int = WE.N_BOOT, seed: int = SEED
) -> tuple[np.ndarray, np.ndarray]:
    """Review m7. The rule names `WE.delta_stats` for every CI (there is no truth/bootstrap.py in this repo; nba/truth/metrics.py's
    bootstrap is row-level). These are exactly `workload_eval._ci`'s draws: games = np.unique order, default_rng(seed), one multinomial
    of size n_games per draw, so N3's overlap interval uses the SAME paired draws as the N2 / N3 delta CIs (unit-tested bit-identical)."""
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    return inv, np.random.default_rng(seed).multinomial(g, np.full(g, 1.0 / g), size=n_boot).astype(
        float
    )


def ratio(
    num: np.ndarray, den: np.ndarray, gid: np.ndarray, n_boot: int = WE.N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """overlap = 1 - mean(num) / mean(den), paired game-clustered percentile interval (the same draws for both means)."""
    inv, wt = boot_weights(gid, n_boot, seed)
    g = wt.shape[1]
    sn, sd, c = (
        np.bincount(inv, num, g),
        np.bincount(inv, den, g),
        np.bincount(inv, minlength=g).astype(float),
    )
    tot = np.maximum(wt @ c, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        sh = 1.0 - ((wt @ sn) / tot) / ((wt @ sd) / tot)
    sh = sh[np.isfinite(sh)]
    return {
        "overlap": 1.0 - float(num.mean()) / float(den.mean()),
        "lo": float(np.percentile(sh, 2.5)),
        "hi": float(np.percentile(sh, 97.5)),
    }


def coverage_bias(qi: np.ndarray, y: np.ndarray, season: np.ndarray) -> dict[str, Any]:
    pit = randomized_pit(qi.astype(float), y.astype(float), seed=SEED)
    return {
        str(s): {
            **{f"cov{t:.2f}": float((pit[season == s] < t).mean()) for t in COV_TAUS},
            "bias": float((qi[season == s].astype(float).mean(axis=1) - y[season == s]).mean()),
        }
        for s in SEASONS
    }


def score_props(ctx: Ctx, gate: dict[str, Any], st: str, meta: pl.DataFrame) -> dict[str, Any]:
    drift = bool(gate["info"].get("drift"))
    assert ctx.pic is not None
    base = load(ctx, hp(ctx, st, "P0C")) if drift else ctx.comps[st]
    pic = load(ctx, ctx.cache / "a0_pic_refit.npz") if drift else ctx.pic
    pi_full = load(ctx, ctx.cache / "pi_FULL.npz")["pi"]
    H = {
        "P0": base,
        "FULL": load(ctx, hp(ctx, st, "FULLC")),
        **{a: load(ctx, hp(ctx, st, a)) for a in CLAIM_HEADS},
    }
    kh0 = key_hash(base["row_gid"], base["row_pid"])
    l5 = {
        a: key_hash(h["row_gid"], h["row_pid"]) == kh0 and RRF.same_rows(h, base)
        for a, h in H.items()
    }
    if not all(l5.values()):
        raise RuntimeError(f"{st}: L5 key-hash check failed {l5}")
    y, gid, season = base["row_y"], base["row_gid"].astype(str), base["row_season"]
    m = rows_of(meta, gid, base["row_pid"])
    date = m["game_date"].cast(pl.Utf8).to_numpy().astype(str)
    late = m["late_tg"].fill_null(False).to_numpy()
    raw = {a: raw_qi(H[a]) for a in ("P0", "T30", "T30_P2", "T30_P2S", "T30SH")}
    craw = {a: crps_from_quantiles(q[0].astype(float), y) for a, q in raw.items()}
    ccont = {a: crps_from_quantiles(raw[a][1], y) for a in ("P0", "T30")}
    mixA = {
        "A0": WE.mixture_qi(base, pic, st),
        **{
            f"T30M{s}": WE.mixture_qi(H[a], pic, st)
            for s, a in (("", "T30"), ("_P2", "T30_P2"), ("_P2S", "T30_P2S"))
        },
    }
    mixF = {a: mix_qi(H[a], pic, pi_full, st) for a in ("FULL", "T30F", "T30F_P2", "T30F_P2S")}
    cm = {a: PR.crps_int(q, y) for a, q in {**mixA, **mixF}.items()}
    n1 = {
        "headline": craw["T30"] - craw["P0"],
        "pess2": craw["T30_P2"] - craw["P0"],
        "P2S": craw["T30_P2S"] - craw["P0"],
    }
    n2 = {
        "headline": cm["T30F"] - cm["FULL"],
        "pess2": cm["T30F_P2"] - cm["FULL"],
        "P2S": cm["T30F_P2S"] - cm["FULL"],
    }
    n3den = {
        "headline": cm["T30M"] - cm["A0"],
        "pess2": cm["T30M_P2"] - cm["A0"],
        "P2S": cm["T30M_P2S"] - cm["A0"],
    }
    sh = craw["T30SH"] - craw["P0"]
    out: dict[str, Any] = {
        "stat": st,
        "N1": {},
        "N2": {},
        "N3": {},
        "shuffle_T30SH_minus_P0": {},
        "slices": [],
        "l5_key_hash": kh0[:16],
    }
    for s in SEASONS:
        sm = season == s
        k = str(s)
        out["N1"][k] = {w_: ds(n1[w_], gid, date, sm) for w_ in WAYS} | {
            "excl-late": ds(n1["headline"], gid, date, sm & ~late)
        }
        out["N2"][k] = {w_: ds(n2[w_], gid, date, sm) for w_ in WAYS} | {
            "excl-late": ds(n2["headline"], gid, date, sm & ~late)
        }
        n3: dict[str, Any] = {}
        for w_ in WAYS:
            den = ds(n3den[w_], gid, date, sm)
            n3[w_] = {
                "T30M_minus_A0": den,
                **(
                    ratio(n2[w_][sm], n3den[w_][sm], gid[sm])
                    if den["ci_hi"] < OVERLAP_DEN
                    else {"overlap": "denominator too small"}
                ),
            }
        mm = sm & ~late
        den = ds(n3den["headline"], gid, date, mm)
        n3["excl-late"] = {
            "T30M_minus_A0": den,
            **(
                ratio(n2["headline"][mm], n3den["headline"][mm], gid[mm])
                if den.get("ci_hi", 0) < OVERLAP_DEN
                else {"overlap": "denominator too small"}
            ),
        }
        out["N3"][k] = n3
        out["shuffle_T30SH_minus_P0"][k] = ds(sh, gid, date, sm)
        masks = {
            "status_changed": m["t30_start_delta"].fill_null(0).to_numpy() != 0, "starter_tonight": m["t30_starter"].to_numpy() == 1,
            "bench_tonight": m["t30_starter"].to_numpy() == 0, "min<5": base["row_minutes"] < 5, "playoffs": np.char.startswith(gid, "004"),
            "cold_start": base["row_n_prior"] < 10, "late_resolved_team_games": late,
        }  # fmt: skip
        for name, mk in masks.items():
            for num, dd in (("N1", n1), ("N2", n2)):
                for w_ in WAYS:
                    r = ds(dd[w_], gid, date, sm & mk)
                    out["slices"].append(
                        {
                            "season": s,
                            "n_cell": num,
                            "way": w_,
                            "slice": name,
                            **{x: r.get(x) for x in ("n", "delta", "ci_lo", "ci_hi")},
                        }
                    )
    if st == "pts":
        b = ccont["T30"] - ccont["P0"]
        r = ds(b, gid, date, season == 2024)
        out["bridge_continuous_T30_minus_P0_2024"] = {
            **r,
            "t085": T085_PTS_2024,
            "gap": abs(r["delta"] - T085_PTS_2024),
            "flag": abs(r["delta"] - T085_PTS_2024) > BRIDGE_GAP,
        }
    out["coverage_bias"] = {
        a: coverage_bias(raw[a][0], y, season) for a in ("P0", "T30", "T30_P2", "T30_P2S")
    } | {a: coverage_bias(q, y, season) for a, q in {"A0": mixA["A0"], **mixF}.items()}
    return out


def score_pplay(ctx: Ctx, gate: dict[str, Any], meta: pl.DataFrame) -> dict[str, Any]:
    assert ctx.rr is not None
    drift = bool(gate["info"].get("drift"))
    ref = load(ctx, ctx.cache / "pplay_R.npz")
    gid0, pid0 = (ref["gid"], ref["pid"]) if drift else (ctx.rr["gid"], ctx.rr["pid"])
    R = (
        align(ref, gid0, pid0, ["p_R", "y", "season"])
        if drift
        else {"p_R": ctx.rr["p_R"], "y": ctx.rr["y"], "season": ctx.rr["season"]}
    )
    arms = load(ctx, ctx.cache / "pplay_arms.npz")
    if key_hash(arms["gid"], arms["pid"]) != key_hash(
        gid0, pid0
    ):  # review m6: assert, as for props
        raise RuntimeError("L5: the P(play) walk's scored row set differs from the R row set")
    A = align(arms, gid0, pid0, [f"p_{a}" for a in ("RT", "RT_P2", "RT_SH", "RT_P2S")] + ["y"])
    if not np.array_equal(A["y"].astype(float), R["y"].astype(float)):
        raise RuntimeError("P(play) labels differ between R and the t30 arms")
    y = R["y"].astype(float)
    gid = np.asarray(gid0).astype(str)
    m = rows_of(meta, gid0, pid0)
    date = m["game_date"].cast(pl.Utf8).to_numpy().astype(str)
    late = m["late_tg"].fill_null(False).to_numpy()
    ll = {
        "R": WE._ll(R["p_R"], y),
        **{a: WE._ll(A[f"p_{a}"], y) for a in ("RT", "RT_P2", "RT_SH", "RT_P2S")},
    }
    bench_h = m["t30_starter"].to_numpy() == 0
    bench_p2 = m["p2_t30_starter"].to_numpy() == 0
    season = np.asarray(R["season"])
    out: dict[str, Any] = {
        "N4": {},
        "shuffle_RT_SH_minus_R": {},
        "l5_key_hash": key_hash(gid0, pid0)[:16],
        "l5_walk_rows_equal_R_rows": key_hash(arms["gid"], arms["pid"]) == key_hash(gid0, pid0),
    }
    for s in SEASONS:
        sm, k = season == s, str(s)
        cell: dict[str, Any] = {}
        for w_, a, bench in (
            ("headline", "RT", bench_h),
            ("pess2", "RT_P2", bench_p2),
            ("P2S", "RT_P2S", bench_p2),
        ):
            d = ll[a] - ll["R"]
            cell[w_] = {
                "all": ds(d, gid, date, sm),
                "bench_tonight": ds(d, gid, date, sm & bench),
                "bench_tonight_headline_flag": ds(d, gid, date, sm & bench_h),
            }
        d = ll["RT"] - ll["R"]
        cell["excl-late"] = {
            "all": ds(d, gid, date, sm & ~late),
            "bench_tonight": ds(d, gid, date, sm & bench_h & ~late),
        }
        out["N4"][k] = cell
        out["shuffle_RT_SH_minus_R"][k] = ds(ll["RT_SH"] - ll["R"], gid, date, sm)
    return out


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def l60_power(effect: float, se_date: float, n_dates: int) -> dict[str, float]:
    se = se_date * math.sqrt(n_dates / L60_DATES) / math.sqrt(LOGGED_SHARE)
    return {
        "effect": effect,
        "se_L60": se,
        "p_clear_floor_and_ci": phi((min(FLOOR, -Z_LOOK * se) - effect) / se),
        "forward_style_power": phi(abs(effect) / se - Z_LOOK) if effect < 0 else 0.0,
    }


def reading(props: dict[str, Any], pplay: dict[str, Any]) -> dict[str, Any]:
    """The frozen reading rule (recommendations, not decisions)."""
    out: dict[str, Any] = {"N1": {}, "N2": {}, "N4": {}, "flags": {}}
    for st, r in props.items():
        c24 = r["N1"]["2024"]
        h, p2, ps = c24["headline"]["delta"], c24["pess2"]["delta"], c24["P2S"]["delta"]
        low_w = "pess2" if p2 >= ps else "P2S"
        low = max(p2, ps)
        rec: dict[str, Any] = {
            "range_2024": [low, h],
            "low_end_way": low_w,
            "literal_numeric_min_pess2_P2S": min(p2, ps),
        }
        if st in ("pts", "reb"):
            cl = c24[low_w]
            se = (cl["date_ci"][1] - cl["date_ci"][0]) / (2 * 1.96)
            rec["l60_power_at_low_end"] = l60_power(low, se, cl["n_dates"])
        rec["expected_to_miss_floor"] = low > FLOOR
        if st in TRIPWIRE:
            rec["tripwire_may_fire_on_honest_data"] = h < TRIPWIRE[st]
            rec["tripwire_threshold"] = TRIPWIRE[st]
        out["N1"][st] = rec
        n2 = r["N2"]
        both = all(
            n2[str(s)][w_]["delta"] <= FLOOR and n2[str(s)][w_]["ci_hi"] < 0
            for s in SEASONS
            for w_ in ("pess2", "P2S")
        )
        if both:
            txt = "the starting five adds beyond recent role; recommend a T30-on-FULL (or AVG) shadow arm under a new rule id"
        elif n2["2024"]["headline"]["delta"] > FLOOR:
            txt = "the offline T-30 gain over FULL is below the floor; recommend no T30-on-FULL arm (the T30 shadow keeps running for timing)"
        else:
            txt = "conditional; the live T30 shadow decides"
        out["N2"][st] = txt
    n4 = pplay["N4"]
    both4 = all(
        n4[str(s)][w_]["bench_tonight"].get("delta", 1) <= FLOOR
        and n4[str(s)][w_]["bench_tonight"].get("ci_hi", 1) < 0
        for s in SEASONS
        for w_ in ("pess2", "P2S")
    )
    out["N4"] = (
        "recommend drafting a T-30 P(play) refresh rule (new id, live only)"
        if both4
        else "no T-30 P(play) work"
    )
    cells = [
        (st, s, r["shuffle_T30SH_minus_P0"][str(s)]) for st, r in props.items() for s in SEASONS
    ] + [("pplay", s, pplay["shuffle_RT_SH_minus_R"][str(s)]) for s in SEASONS]
    fired = [(a, s, c["delta"]) for a, s, c in cells if c["delta"] <= SHUF_FLAG and c["ci_hi"] < 0]
    out["flags"]["starter_shuffle"] = {
        "raised": bool(fired),
        "cells": fired,
        "note": "single seed, reported not judged (null sd unmeasured)",
    }
    if "pts" in props and "bridge_continuous_T30_minus_P0_2024" in props["pts"]:
        out["flags"]["bridge_gap_over_0.008"] = props["pts"]["bridge_continuous_T30_minus_P0_2024"][
            "flag"
        ]
    out["flags"]["tripwire"] = {
        st: v.get("tripwire_may_fire_on_honest_data")
        for st, v in out["N1"].items()
        if st in TRIPWIRE
    }
    return out


def run_score(ctx: Ctx) -> dict[str, Any]:
    gate = require_gate(ctx)
    w = ctx.world()
    meta_cols = ["game_date", "late_tg", "t30_starter", "t30_start_delta", "p2_t30_starter"]
    props = {st: score_props(ctx, gate, st, w["props"].select(*KEYS, *meta_cols)) for st in STATS}
    for st in STATS:
        _log(f"scored {st}")
    pplay = score_pplay(ctx, gate, w["pplay"].select(*KEYS, *meta_cols))
    out = {
        "id": ID,
        "sha256": ctx.sha,
        "descriptive": True,
        "design_choices": DESIGN,
        "props": props,
        "pplay": pplay,
        "reading": reading(props, pplay),
        "gate": gate["tests"],
        "missingness_flag": not gate["info"].get("aud_ok", True),
    }
    WE.write_json(ctx.out_dir / "results.json", out)
    return out


SUPERSEDE_NOTE = (
    "The N1 planning range [min(pess2, P2S), headline] supersedes FORWARD_PREREG_2026_27 section 2's continuous, proxy-tip "
    "planning range in this result note only; FORWARD_PREREG_2026_27 (its direction check and its section 6 power table) is not edited."
)


def render(out: dict[str, Any]) -> str:
    f = PR._f
    lines = [f"# {ID} results (DESCRIPTIVE; per-number 95% intervals, game-clustered; date-clustered in brackets)", "", f"Rule sha256 {str(out['sha256'])[:8]}. Season 2025 never loaded." + (" **Missingness flag raised (check 5).**" if out.get("missingness_flag") else ""), "",
             SUPERSEDE_NOTE, "", f"Range low end: {DESIGN['range_low_end']}.", "",
             "| stat | season | N | way | n | delta | CI (game) | CI (date) |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for st, r in out["props"].items():
        for s in SEASONS:
            for nn in ("N1", "N2"):
                for w_, c in r[nn][str(s)].items():
                    lines.append(
                        f"| {st} | {s} | {nn} | {w_} | {c.get('n')} | {f(c.get('delta'), 5)} | [{f(c.get('ci_lo'), 5)}, {f(c.get('ci_hi'), 5)}] | [{f((c.get('date_ci') or [None, None])[0], 5)}, {f((c.get('date_ci') or [None, None])[1], 5)}] |"
                    )
    lines += ["", "```json", json.dumps({"reading": out["reading"], "N3": {st: r["N3"] for st, r in out["props"].items()}, "N4": out["pplay"]["N4"], "bridge": out["props"].get("pts", {}).get("bridge_continuous_T30_minus_P0_2024")}, indent=1, default=float), "```", ""]  # fmt: skip
    return "\n".join(lines)


def run_report(ctx: Ctx, out: dict[str, Any] | None = None) -> str:
    out = out or json.loads((ctx.out_dir / "results.json").read_text())
    md = render(out)
    (REPORT_MD if ctx.out_dir == OUT_DIR else ctx.out_dir / "report.md").write_text(md)
    return md


def run_all(ctx: Ctx) -> int:
    if not run_gate(ctx)["gate_ok"]:
        return 1
    run_arms(ctx)
    run_report(ctx, run_score(ctx))
    return 0


# --------------------------------------------------------------------------- dryrun (synthetic world)
def synthetic_inputs(seed: int = 0, days: int = 60) -> dict[str, Any]:
    """Games, box rows (with usual starters, OUT-at-T-60, benched-played, dressed-DNP and no-box late absences), the T-60 OUT dict, pre-tip rows,
    a props feature frame and a P(play) frame with every column an arm reads. Labels synthetic: minutes follow tonight's starter flag."""
    rng = np.random.default_rng(seed)
    g_rows: list[tuple[Any, ...]] = []
    b_rows: list[tuple[Any, ...]] = []
    flagged: dict[str, set[int]] = {}
    pre: list[tuple[str, int]] = []
    for season in (2022, 2023, 2024):
        d0 = dt.date(season, 11, 1)
        for k in range(days):
            d = d0 + dt.timedelta(days=2 * k + (k // 15) * 2)
            perm = rng.permutation(6) + 1
            for gi in range(3):
                gid = f"00{2 if k % 9 else 4}{season % 100:02d}{k:03d}{gi:02d}"
                h, a = int(perm[2 * gi]), int(perm[2 * gi + 1])
                g_rows.append((gid, d, season, h, a, 100, 100))
                for team in (h, a):
                    starters = list(range(5))
                    ev = rng.random()
                    absent = int(rng.integers(0, 5)) if ev < 0.25 else None
                    for j in range(10):
                        pid = team * 100 + j
                        st = j in starters and j != absent
                        if absent is not None and j == 5:
                            st = True
                        mins = float(np.clip(rng.normal(32 if st else 14, 5), 1, 44))
                        if j == 9 and rng.random() < 0.4:
                            mins = 0.0
                        if j == absent:
                            kind = (
                                int(ev * 20) % 4
                            )  # 0 OUT at T-60, 1 benched played, 2 dressed DNP, 3 no box row + pre-tip row
                            if kind == 0:
                                flagged.setdefault(gid, set()).add(pid)
                                continue
                            if kind == 2:
                                mins = 0.0
                            if kind == 3:
                                pre.append((gid, pid))
                                continue
                        b_rows.append(
                            (
                                gid,
                                pid,
                                team,
                                mins,
                                *rng.poisson(
                                    np.clip(
                                        [0.45 * mins, 0.18 * mins, 0.1 * mins, 0.05 * mins],
                                        0.05,
                                        None,
                                    )
                                ).tolist(),
                                st,
                            )
                        )
    games = pl.DataFrame(
        g_rows,
        schema={
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "season": pl.Int64,
            "home_team": pl.Int64,
            "away_team": pl.Int64,
            "home_pts": pl.Int64,
            "away_pts": pl.Int64,
        },
        orient="row",
    )
    pgs = pl.DataFrame(
        b_rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "minutes": pl.Float64,
            "pts": pl.Int64,
            "reb": pl.Int64,
            "ast": pl.Int64,
            "fg3m": pl.Int64,
            "starter": pl.Boolean,
        },
        orient="row",
    )
    pretip = pl.DataFrame(pre, schema={"game_id": pl.Utf8, "player_id": pl.Int64}, orient="row")
    box = pgs.join(games.select("game_id", "game_date", "season"), on="game_id").sort(
        ["player_id", "game_date"]
    )
    box = box.with_columns(
        pl.int_range(pl.len()).over("player_id").alias("n_prior_all"),
        (pl.col("minutes") > 0).alias("played"),
    )
    names = (
        {c for st in STATS for c in stat_feature_names(st)}
        | set(pplay_arms()["R"])
        | set(R5)
        | set(PR.CAND_COLS)
    )
    n = box.height
    cols = {c: rng.normal(size=n) for c in sorted(names) if c not in box.columns}
    prev_min = box.select(pl.col("minutes").shift(1).over("player_id").fill_null(20.0))[
        "minutes"
    ].to_numpy()
    prev_st = box.select(
        pl.col("starter")
        .cast(pl.Float64)
        .rolling_mean(10, min_samples=1)
        .shift(1)
        .over("player_id")
        .fill_null(0.5)
    )["starter"].to_numpy()
    cols.update(
        {
            "n_prior": box["n_prior_all"].to_numpy().astype(float),
            "n_played_prior": box["n_prior_all"].to_numpy().astype(float),
            "min10": prev_min,
            "min40": prev_min,
            "wl_min_last": prev_min,
            "starter10": prev_st,
            "n_season": np.full(n, 3.0),
        }
    )
    for st in STATS:
        cols[f"m10_{st}"] = {"pts": 0.45, "reb": 0.18, "ast": 0.1, "fg3m": 0.05}[st] * prev_min
        cols[f"sd10_{st}"] = np.full(n, 3.0)
    extra = {
        "starter_act": box["starter"].cast(pl.Float64).to_numpy(),
        "abs_margin": np.abs(rng.normal(6, 5, n)),
        "final_margin": rng.normal(0, 12, n),
        "b2b": np.zeros(n),
        "gap_days": np.full(n, 2.0),
        "since_flag": np.full(n, 9.0),
    }
    full = box.drop("n_prior_all").with_columns(
        [pl.Series(k, v) for k, v in {**cols, **extra}.items() if k not in box.columns]
    )
    props = full.filter(pl.col("played")).drop("played")
    pplay = full.with_columns(pl.col("played").cast(pl.Int64)).filter(pl.col("n_played_prior") >= 5)
    return {
        "games": games,
        "pgs": pgs,
        "props": props,
        "pplay": pplay,
        "flagged": flagged,
        "pretip": pretip,
    }


def dry_ctx(out: Path) -> Ctx:
    """Synthetic world; the 'stored' components, A0 pi, FULL caches, FULL pi and RECENT_ROLE p_R are produced by this harness's own walks on it."""
    cfg = ContextResidualConfig(n_estimators=10, min_cal_rows=300)
    s = synthetic_inputs()
    w = assemble(
        s["games"],
        s["pgs"],
        s["props"],
        s["pplay"],
        s["flagged"],
        s["pretip"],
        cfg,
        cfg,
        {"null_tip_avail_rows": 0},
    )
    fr, pfr = w["props"], w["pplay"]
    comps = {st: heads_walk(fr, st, cfg, "P0C", log=False) for st in STATS}
    full = {st: heads_walk(fr, st, cfg, "FULLC", log=False) for st in STATS}
    pic = collect_pi(fr, cfg)
    full_pi = PR.align_to_stored(
        PR.walk_pi(fr, PR.CAND_COLS, cfg, log=False), pic["row_gid"], pic["row_pid"]
    )
    rr = pplay_walk_served(pfr, {"R": pplay_arms()["R"]}, {}, cfg)
    c = Ctx(
        out_dir=out,
        cache=out / "cache",
        cfg=cfg,
        pcfg=cfg,
        world_fn=lambda: w,
        comps=comps,
        pic=pic,
        full_heads=full,
        full_pi=full_pi,
        rr=rr,
        real_plants=False,
        sha="dryrun",
    )
    c.props_pin, c.pplay_pin = "x", "x"
    pn = pins(c)
    c.props_pin, c.pplay_pin, c.pins_rows = (
        pn["props_sha256"],
        pn["pplay_keys_t30_sha256"],
        (pn["props_rows"], pn["pplay_rows"]),
    )
    _log(
        f"dryrun world: props {fr.height} rows, pplay {pfr.height} rows, late-resolved {int(w['late']['is_late'].sum())}"
    )
    return c


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pins", "gate", "arms", "score", "report", "run", "dryrun"])
    ap.add_argument("--sha", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "dryrun":
        if not a.out:
            raise SystemExit("--out <scratch dir> is required for dryrun")
        out = Path(a.out)
        if out.resolve() in (OUT_DIR.resolve(), CACHE.resolve()):
            raise SystemExit("dryrun never writes the real output dirs")
        rc = run_all(dry_ctx(out))
        res = json.loads((out / "results.json").read_text())
        print(
            json.dumps(
                {
                    "rc": rc,
                    "reading": res["reading"],
                    "N1_pts_2024": res["props"]["pts"]["N1"]["2024"],
                    "N4_2024": res["pplay"]["N4"]["2024"],
                },
                indent=1,
                default=float,
            )[:6000]
        )
        return rc
    if a.cmd == "pins":
        pn = pins(real_ctx(None))
        print(json.dumps(pn, indent=1))
        return 0 if pn["ok"] else 1
    sha = require_frozen(a.sha)
    ctx = real_ctx(sha)
    if a.cmd == "report":
        run_report(ctx)
        return 0
    with WE.heavy():
        if a.cmd == "gate":
            return 0 if run_gate(ctx)["gate_ok"] else 1
        if a.cmd == "arms":
            run_arms(ctx)
            return 0
        if a.cmd == "score":
            run_report(ctx, run_score(ctx))
            return 0
        return run_all(ctx)


if __name__ == "__main__":
    import research.eval.lineups_known_rebase as _E

    raise SystemExit(_E.main())
