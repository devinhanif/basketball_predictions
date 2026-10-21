# ruff: noqa: E501
"""PRESEASON_ROLE_PRIOR harness (rule: docs/prereg/PRESEASON_ROLE_PRIOR.md).

Subcommands (all read-only on the databases; nothing here opens a write connection or loads season 2025):
  gate       label-free counts of the minimum-data gate (movers, rookies, coverage, Pinnacle legs); equals reports/prereg_preseason_role_prior/gate.json
  calibrate  kappa_starter / kappa_bench from season 2022 only (the one label-using freeze step) -> calibrate.json
  check      label-free preflight: pins, row sets, known-at, planted future, grid reconstruction, library versions; plus the A0 reproduction
  rehearse   six synthetic worlds x seeds, no data, no fit, returns each verdict word
  run        the real arms on 2023 / 2024; refuses unless `require_ready(--sha)` passes (frozen rule + pinned harness)

Freeze and pin (a hand that can commit):  python -m research.eval.preseason_role_prior freeze | pin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ID = "PRESEASON_ROLE_PRIOR"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/PRESEASON_ROLE_PRIOR.md"
DOC = ROOT / DOC_REL
OUT_DIR = ROOT / "reports/prereg_preseason_role_prior"
# Inputs are read by absolute path from the data root (a worktree has no nba.duckdb).
DATA = Path(os.environ.get("NBA_DATA_ROOT", "/Users/devin/Downloads/nba-prediction"))
DB = DATA / "nba.duckdb"
HIST_DB = DATA / "data/history/nba_history.duckdb"
ODDS = DATA / "reports/odds_history"
PAIRS = DATA / "data/ops/model_clv_pairs"

MAX_SEASON = 2024  # season 2025 is the frozen holdout: never loaded
CALIB_SEASON = 2022
SELECT, REPORT = 2023, 2024
STATS = ("pts", "reb", "ast", "fg3m")
K_MAIN = 10
K_SENS = (5, 10, 20)
M_MIN = 60.0  # minutes weight constant (w_m = m_pre / (m_pre + 60))
M_RATE = 120.0
M_SHRINK = 100.0
MIN_PREV = 300.0
LAM_CLIP = (0.5, 2.0)
COVER_MIN_GAMES = 2
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
MIN_ROWS = 400
MIN_COVER = 0.60
NQ = 199
N_BOOT = 2000
SEED = 0
ALPHA = 0.05
PLACEBO_SHARE = 0.5
COV_TOL = 0.02
TAU_IDX = {"0.10": 19, "0.90": 179}
EPS = 0.5 / 199

HARNESS_FILES: tuple[str, ...] = (
    "nba/props/distributions.py",
    "nba/props/metrics.py",
    "nba/truth/metrics.py",
    "research/eval/preseason_role_prior.py",
)
LIB_NAMES = ("python", "numpy", "pandas", "duckdb")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze / pin refusals (as WOWY_STRUCT)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=ROOT).returncode == 0
    )


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (same bytes as the awk one-liner)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, run: Callable[..., str] = git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, run) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carrying this rule in git log: the rule is not frozen"
        )
    return sha


def pin_of(blobs: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.fullmatch(rf"harness\({ID}\): ([0-9a-f]{{64}})", subj.strip())
        if m:
            return h, m.group(1)
    return None


def require_harness_pinned(
    sha: str,
    run: Callable[..., str] = git,
    root: Path = ROOT,
    ok: Callable[..., bool] = git_ok,
) -> dict[str, str]:
    fc = freeze_commit(sha, run)
    if fc is None:
        raise RuntimeError("the rule is not frozen")
    pc = pin_commit(run)
    if pc is None:
        raise RuntimeError(f"no `harness({ID}): <pin>` commit: the harness is not pinned")
    h, pin = pc
    out: dict[str, str] = {}
    for rel in HARNESS_FILES:
        frozen = run("show", f"{h}:{rel}")
        if frozen != (root / rel).read_text():
            raise RuntimeError(f"{rel} differs from its version at pin commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(frozen.encode()).hexdigest()
        if run("show", f"{fc}:{rel}") != frozen:
            raise RuntimeError(
                f"{rel} differs between the freeze commit and the pin commit: refusing"
            )
    if pin_of(out) != pin:
        raise RuntimeError("the pin in the commit subject does not match the harness blobs")
    if not ok("merge-base", "--is-ancestor", fc, h):
        raise RuntimeError("pin commit does not descend from the freeze commit")
    return out


def require_ready(
    sha: str | None, run: Callable[..., str] = git, ok: Callable[..., bool] = git_ok
) -> dict[str, str]:
    """The single preflight the real-label entrypoint calls before reading any real outcome."""
    s = require_frozen(sha, None, run)
    return require_harness_pinned(s, run, ROOT, ok)


def declared(doc: Path = DOC) -> dict[str, str]:
    """`NAME: <value>` pin lines above the marker (64-hex input hashes and LIB_* versions)."""
    out: dict[str, str] = {}
    for line in doc.read_text().splitlines():
        if line.rstrip() == MARKER:
            break
        m = re.fullmatch(r"([A-Z0-9_]+): ([0-9a-f]{64}|[0-9][0-9.]*)", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def versions() -> dict[str, str]:
    import duckdb

    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "duckdb": duckdb.__version__,
    }


# --------------------------------------------------------------------------- data access (read-only; seasons <= 2024)


def check_season(season: int) -> int:
    if season > MAX_SEASON:
        raise ValueError(
            f"season {season}: 2025 is the frozen holdout, never loaded by this harness"
        )
    return season


def connect(path: Path) -> Any:
    import duckdb

    err: Exception | None = None
    for _ in range(5):
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as e:  # lock error: wait and retry, never a write connection
            err = e
            _log(f"lock on {path.name}: {e}; sleeping 30 s")
            time.sleep(30)
    raise RuntimeError(f"could not open {path} read-only: {err}")


_REG_Q = (
    "select s.player_id, s.team_id, g.season, g.game_date, s.game_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m "
    "from player_game_stats s join games g using(game_id) "
    "where substr(s.game_id,1,3)='002' and s.minutes>0 and g.season {w}"
)
_EVER_Q = (
    "select s.player_id, g.season from player_game_stats s join games g using(game_id) "
    "where substr(s.game_id,1,3) in ('002','004','005') and s.minutes>0 and g.season {w}"
)


def load_reg(c: Any, h: Any) -> pd.DataFrame:
    a = pd.concat(
        [
            h.execute(_REG_Q.format(w="<=2021")).df(),
            c.execute(_REG_Q.format(w=f"between 2022 and {MAX_SEASON}")).df(),
        ]
    )
    a = a.drop_duplicates(["player_id", "game_id"]).sort_values(
        ["player_id", "game_date", "game_id"]
    )
    return a.reset_index(drop=True)


def load_first_ever(c: Any, h: Any) -> pd.Series:
    ap = pd.concat(
        [
            h.execute(_EVER_Q.format(w="<=2021")).df(),
            c.execute(_EVER_Q.format(w=f"between 2022 and {MAX_SEASON}")).df(),
        ]
    )
    return ap.groupby("player_id").season.min()


def load_pre_rows(c: Any, season: int) -> pd.DataFrame:
    """Preseason player-games with minutes > 0 for one season <= 2024 (game_date kept for the known-at assertion)."""
    check_season(season)
    return c.execute(
        "select s.player_id, s.team_id, g.game_date, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter "
        "from preseason_player_game_stats s join preseason_games g using(game_id) "
        "where g.season=? and g.season<=? and s.minutes>0",
        [season, MAX_SEASON],
    ).df()


def pre_agg(pre: pd.DataFrame) -> pd.DataFrame:
    """Per (player, team): n_pre games, m_pre minutes, stat totals, any-start flag."""
    g = pre.assign(minutes=pre["minutes"].astype("float64")).groupby(["player_id", "team_id"])
    out = g.agg(
        pre_n=("minutes", "size"),
        pre_m=("minutes", "sum"),
        pre_pts=("pts", "sum"),
        pre_reb=("reb", "sum"),
        pre_ast=("ast", "sum"),
        pre_fg3m=("fg3m", "sum"),
        pre_start=("starter", "max"),
    ).reset_index()
    out["pre_start"] = out["pre_start"].fillna(False).astype(bool)
    return out


# --------------------------------------------------------------------------- slices (shared by gate and arms; as-of the row)


def slice_frame(
    mr: pd.DataFrame, reg: pd.DataFrame, first_ever: pd.Series, season: int
) -> pd.DataFrame:
    """Label rows: games_with_team (`gwt`), slice (mover / rookie / returner_gap / stayer / inseason_arrival_or_other).
    `mr` carries player_id, team_id, game_id. Only games before the row enter `gwt`; slice uses the player's first
    team of the season and last team of the previous season (both known at tip of the row's own game)."""
    ps = (
        reg.groupby(["player_id", "season"])
        .agg(first_team=("team_id", "first"), last_team=("team_id", "last"))
        .reset_index()
    )
    prev = ps.assign(season=ps.season + 1).rename(columns={"last_team": "prev_last"})[
        ["player_id", "season", "prev_last"]
    ]
    ps = ps.merge(prev, on=["player_id", "season"], how="left")
    cum = reg.assign(gwt=reg.groupby(["player_id", "season", "team_id"]).cumcount())
    mr = mr.merge(
        ps[ps.season == season][["player_id", "first_team", "prev_last"]],
        on="player_id",
        how="left",
    )
    mr = mr.merge(
        cum[cum.season == season][["player_id", "game_id", "gwt"]],
        on=["player_id", "game_id"],
        how="left",
    )
    prior_any = first_ever.reindex(mr.player_id).to_numpy() < season
    isnew = (mr.team_id == mr.first_team).to_numpy()
    has_prev = mr.prev_last.notna().to_numpy()
    diff_team = (mr.prev_last != mr.first_team).to_numpy()
    sl = np.where(
        ~prior_any,
        "rookie",
        np.where(
            has_prev & isnew & diff_team, "mover", np.where(~has_prev, "returner_gap", "stayer")
        ),
    )
    sl = np.where((sl == "stayer") & ~isnew, "inseason_arrival_or_other", sl)
    mr = mr.copy()
    mr["slice"] = sl
    return mr


def _a0_cols(path: Path) -> list[str]:
    return ["game_id", "player_id", "team_id", "game_date", "n_prior", "min10", "starter10"]


def read_rows(stat: str, season: int, cols: list[str] | None = None) -> pd.DataFrame:
    check_season(season)
    return pd.read_parquet(ODDS / f"model_rows_{season}_{stat}.parquet", columns=cols)


def sha_table(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(
        pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).to_numpy().tobytes()
    )
    return h.hexdigest()


# --------------------------------------------------------------------------- gate


def gate_season(season: int, c: Any, reg: pd.DataFrame, first_ever: pd.Series) -> dict[str, Any]:
    check_season(season)
    mr = read_rows("pts", season, _a0_cols(Path()))
    mr = slice_frame(mr, reg, first_ever, season)
    p = pre_agg(load_pre_rows(c, season))
    mr = mr.merge(
        p[["player_id", "team_id", "pre_n", "pre_m"]], on=["player_id", "team_id"], how="left"
    )
    mr["pre_n"] = mr.pre_n.fillna(0)
    mr["pre_m"] = mr.pre_m.fillna(0)
    d: dict[str, Any] = {
        "model_rows_rows_per_stat": int(len(mr)),
        "games": int(mr.game_id.nunique()),
    }
    d["slice_all_rows"] = mr["slice"].value_counts().to_dict()
    mv = mr[mr["slice"] == "mover"]
    d["movers_distinct"] = int(mv.player_id.nunique())
    d["movers_distinct_ge2_pre"] = int(mv[mv.pre_n >= 2].player_id.nunique())
    d["movers_distinct_ge1_pre"] = int(mv[mv.pre_n >= 1].player_id.nunique())
    for K in K_SENS:
        kk: dict[str, Any] = {}
        for name in ("mover", "rookie", "returner_gap"):
            x = mr[(mr["slice"] == name) & (mr.gwt < K)]
            kk[name] = {
                "rows": int(len(x)),
                "players": int(x.player_id.nunique()),
                "games": int(x.game_id.nunique()),
                "rows_pre_ge1": int((x.pre_n >= 1).sum()),
                "rows_pre_ge2": int((x.pre_n >= 2).sum()),
                "frac_pre_ge1": float((x.pre_n >= 1).mean()) if len(x) else None,
                "frac_pre_ge2": float((x.pre_n >= 2).mean()) if len(x) else None,
                "median_pre_minutes_when_ge2": float(x[x.pre_n >= 2].pre_m.median())
                if (x.pre_n >= 2).any()
                else None,
            }
        x = mr[(mr["slice"].isin(["mover", "rookie"])) & (mr.gwt < K)]
        kk["mover_plus_rookie"] = {
            "rows": int(len(x)),
            "rows_pre_ge2": int((x.pre_n >= 2).sum()),
            "frac_pre_ge2": float((x.pre_n >= 2).mean()),
        }
        kk["stayer_first_K_team_games_rows"] = int(((mr["slice"] == "stayer") & (mr.gwt < K)).sum())
        d[f"K{K}"] = kk
    pr = pd.read_parquet(
        PAIRS / f"pairs_{season}.parquet",
        columns=["game_id", "player_id", "stat", "book", "snapshot_kind", "_dnp"],
    )
    d["pairs_snapshot_kinds"] = pr.snapshot_kind.value_counts().to_dict()
    d["pairs_books"] = pr.book.value_counts().to_dict()
    aff = mr[(mr["slice"].isin(["mover", "rookie"])) & (mr.gwt < K_MAIN) & (mr.pre_n >= 2)][
        ["game_id", "player_id", "slice"]
    ]
    pj = pr[(pr.book == "pinnacle") & (~pr._dnp)].merge(aff, on=["game_id", "player_id"])
    d["pinnacle_legs_affected_K10_pre_ge2"] = (
        pj.groupby(["slice", "stat", "snapshot_kind"])
        .size()
        .rename("n")
        .reset_index()
        .to_dict("records")
    )
    key = mr.set_index(["game_id", "player_id"]).index.sort_values()
    for st in ("reb", "ast", "fg3m"):
        o = read_rows(st, season, ["game_id", "player_id"])
        d[f"rowset_equal_pts_{st}"] = bool(
            len(o) == len(mr)
            and o.set_index(["game_id", "player_id"]).index.sort_values().equals(key)
        )
    return d


def run_gate() -> dict[str, Any]:
    c, h = connect(DB), connect(HIST_DB)
    reg, fe = load_reg(c, h), load_first_ever(c, h)
    return {str(s): gate_season(s, c, reg, fe) for s in (SELECT, REPORT)}


# --------------------------------------------------------------------------- the blend (pure functions; no outcome is an argument)


def bh_adjust(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (step-up), same order as the input."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        run = min(run, p[i] * n / rank)
        adj[i] = run
    return adj


def assert_known_at(pre: pd.DataFrame, first_row_date: Any) -> None:
    """Every preseason game used must be dated strictly before the first scored row of the season (the opener)."""
    if len(pre) and pd.to_datetime(pre["game_date"]).max() >= pd.to_datetime(first_row_date):
        raise ValueError(
            "a preseason game dated on or after the first scored row: known-at violated"
        )


def build_ctx(
    rows: pd.DataFrame,
    reg: pd.DataFrame,
    first_ever: pd.Series,
    pre: pd.DataFrame,
    season: int,
) -> pd.DataFrame:
    """Per-row context for one season. `rows` = stored production rows (model_rows, one stat or the pts file) WITHOUT outcome columns.
    Adds slice, gwt, preseason aggregates of the row's own team, the previous season's per-minute rates (regular season S-1) and the
    previous season's last-10 games (placebo A3). Everything is as-of the row: preseason < opener, S-1 < season S."""
    check_season(season)
    assert_known_at(pre, rows["game_date"].min())
    cols = ["game_id", "player_id", "team_id", "game_date", "min10", "starter10"]
    mr = slice_frame(rows[cols].copy(), reg, first_ever, season)
    p = pre_agg(pre)
    mr = mr.merge(p, on=["player_id", "team_id"], how="left")
    mr["pre_n"] = mr["pre_n"].fillna(0)
    mr["pre_m"] = mr["pre_m"].fillna(0.0)
    mr["pre_start"] = mr["pre_start"].fillna(False).astype(bool)
    for s in STATS:
        mr[f"pre_{s}"] = mr[f"pre_{s}"].fillna(0.0)
    prev = reg[reg.season == season - 1]
    agg = (
        prev.groupby("player_id")
        .agg(prev_min=("minutes", "sum"), **{f"prev_{s}": (s, "sum") for s in STATS})
        .reset_index()
    )
    mr = mr.merge(agg, on="player_id", how="left")
    last10 = (
        prev.groupby("player_id")
        .tail(10)
        .groupby("player_id")
        .agg(
            l10_min=("minutes", "sum"),
            l10_n=("minutes", "size"),
            **{f"l10_{s}": (s, "sum") for s in STATS},
        )
        .reset_index()
    )
    return mr.merge(last10, on="player_id", how="left")


def kappa_for(ctx: pd.DataFrame, kappa: dict[str, float]) -> np.ndarray:
    return np.where(ctx["pre_start"].to_numpy(), kappa["starter"], kappa["bench"])


def lambdas(
    ctx: pd.DataFrame,
    m_recency: np.ndarray,
    stat: str,
    arm: str,
    kappa: dict[str, float],
    K: int = K_MAIN,
    kind: str = "mover",
    perm: np.ndarray | None = None,
) -> np.ndarray:
    """Multiplier lambda per row (1.0 = A0). `arm` in A1, A2, A3, A4. No outcome column is read.
    A4 needs `perm`, an index permutation of the covered affected rows (values move, weights stay)."""
    n = len(ctx)
    lam = np.ones(n)
    g = ctx["gwt"].to_numpy(dtype=float)
    min10 = ctx["min10"].to_numpy(dtype=float)
    pre_n = ctx["pre_n"].to_numpy(dtype=float)
    m_pre = ctx["pre_m"].to_numpy(dtype=float)
    aff = (ctx["slice"].to_numpy() == kind) & (g < K) & (pre_n >= COVER_MIN_GAMES) & (min10 >= 1.0)
    idx = np.flatnonzero(aff)
    if len(idx) == 0:
        return lam
    fade = np.clip(1.0 - g[idx] / K, 0.0, 1.0)
    mp = m_pre[idx]
    w_m = fade * mp / (mp + M_MIN)
    w_r = fade * mp / (mp + M_RATE)
    mpg_pre = mp / pre_n[idx]
    mpg_adj = kappa_for(ctx, kappa)[idx] * mpg_pre
    rho_pre = ctx[f"pre_{stat}"].to_numpy(dtype=float)[idx] / mp
    mrec = np.asarray(m_recency, dtype=float)[idx]
    mn = min10[idx]
    rho_a0 = mrec / np.maximum(mn, 1.0)
    prev_min = ctx["prev_min"].to_numpy(dtype=float)[idx]
    prev_stat = ctx[f"prev_{stat}"].to_numpy(dtype=float)[idx]
    rho_prev = np.where(
        prev_min >= MIN_PREV, prev_stat / np.where(prev_min > 0, prev_min, 1.0), rho_a0
    )
    values_min, values_rho = mpg_adj, rho_pre
    if arm == "A3":
        l10_n = ctx["l10_n"].to_numpy(dtype=float)[idx]
        l10_min = ctx["l10_min"].to_numpy(dtype=float)[idx]
        ok = (l10_n >= 5) & (l10_min > 0)
        values_min = np.where(ok, l10_min / np.where(ok, l10_n, 1.0), np.nan)
        values_rho = np.where(
            ok, ctx[f"l10_{stat}"].to_numpy(dtype=float)[idx] / np.where(ok, l10_min, 1.0), np.nan
        )
    elif arm == "A4":
        if perm is None or len(perm) != len(idx):
            raise ValueError("A4 needs a permutation of the covered affected rows")
        values_min, values_rho = mpg_adj[perm], rho_pre[perm]
    elif arm not in ("A1", "A2"):
        raise ValueError(f"unknown arm {arm}")
    min_blend = (1.0 - w_m) * mn + w_m * values_min
    lam_min = min_blend / mn
    if arm == "A1":
        out = lam_min
    else:
        rho_ps = (mp * values_rho + M_SHRINK * rho_prev) / (mp + M_SHRINK)
        rho_blend = (1.0 - w_r) * rho_a0 + w_r * rho_ps
        ratio = np.where(rho_a0 > 0, rho_blend / np.where(rho_a0 > 0, rho_a0, 1.0), 1.0)
        out = lam_min * ratio
    out = np.where(np.isfinite(out), np.clip(out, *LAM_CLIP), 1.0)
    lam[idx] = out
    return lam


def a4_perm(ctx: pd.DataFrame, K: int, kind: str, seed: int) -> np.ndarray:
    """Permutation of the covered affected rows, within starter flag (one draw, seed fixed)."""
    g = ctx["gwt"].to_numpy(dtype=float)
    aff = (
        (ctx["slice"].to_numpy() == kind)
        & (g < K)
        & (ctx["pre_n"].to_numpy() >= COVER_MIN_GAMES)
        & (ctx["min10"].to_numpy() >= 1.0)
    )
    idx = np.flatnonzero(aff)
    start = ctx["pre_start"].to_numpy()[idx]
    rng = np.random.default_rng(seed)
    perm = np.arange(len(idx))
    for flag in (True, False):
        pos = np.flatnonzero(start == flag)
        perm[pos] = pos[rng.permutation(len(pos))]
    return perm


# --------------------------------------------------------------------------- the stored grid and the integer-support score


def block_zq(c: np.ndarray, s: np.ndarray, pge: list[Any]) -> dict[str, Any]:
    """Bracket the block's 199 sorted standardized grid values from the stored integer survival counts (LINE_CALIBRATION's method)."""
    lens = np.array([len(p) for p in pge])
    flat = np.concatenate([np.asarray(p, dtype=float) for p in pge])
    rows = np.repeat(np.arange(len(c)), lens)
    k = np.concatenate([np.arange(1, n + 1) for n in lens])
    N = np.rint(flat * NQ).astype(np.int64)
    assert np.abs(flat * NQ - N).max() < 1e-6, "survival values are not counts out of 199"
    t = (k - 0.5 - c[rows]) / s[rows]
    mx = np.full(NQ + 1, -np.inf)
    np.maximum.at(mx, N, t)
    mn = np.full(NQ + 1, np.inf)
    np.minimum.at(mn, N, t)
    suf = np.maximum.accumulate(mx[::-1])[::-1]
    pre = np.minimum.accumulate(mn)
    j = np.arange(1, NQ + 1)
    lo, hi = suf[NQ + 1 - j], pre[NQ - j]
    mid = np.where(
        np.isfinite(lo) & np.isfinite(hi), (lo + hi) / 2, np.where(np.isfinite(lo), lo, hi)
    )
    return {"lo": lo, "hi": hi, "mid": mid, "inconsistent": int(np.sum(hi < lo))}


def grid_q(c: np.ndarray, s: np.ndarray, zq: np.ndarray) -> np.ndarray:
    """Production's grid: q_j = max.acc(clip(c + s zq_j, 0))."""
    return np.asarray(
        np.maximum.accumulate(np.maximum(c[:, None] + s[:, None] * zq[None, :], 0.0), axis=1)
    )


def int_cdf_counts(q: np.ndarray, kmax: int) -> np.ndarray:
    """(n, kmax) matrix, entry k = number of grid points with ceil(q - 0.5) <= k, i.e. q <= k + 0.5."""
    return np.stack([(q <= k + 0.5).sum(axis=1) for k in range(kmax)], axis=1)


def crps_int_q(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Integer-support CRPS of the served integer grid ceil(q - 0.5): sum_k (F(k) - 1[y <= k])^2 over k = 0..kmax."""
    y = np.asarray(y, dtype=float)
    kmax = int(max(np.ceil(q.max() - 0.5), np.nanmax(y))) + 2
    F = int_cdf_counts(q, kmax) / NQ
    ind = (y[:, None] <= np.arange(kmax)[None, :]).astype(float)
    return np.asarray(((F - ind) ** 2).sum(axis=1))


def crps_int_pge(pge: list[Any], y: np.ndarray) -> np.ndarray:
    """The same score from the stored survival vectors p_ge[k] = P(Y >= k + 1)."""
    L = max(len(p) for p in pge)
    S = np.zeros((len(pge), L + 2))
    for i, p in enumerate(pge):
        S[i, : len(p)] = p
    F = 1.0 - S  # F(k) = P(Y <= k) = 1 - P(Y >= k + 1)
    y = np.asarray(y, dtype=float)
    kmax = max(F.shape[1], int(np.nanmax(y)) + 2)
    if F.shape[1] < kmax:
        F = np.hstack([F, np.ones((len(F), kmax - F.shape[1]))])
    ind = (y[:, None] <= np.arange(F.shape[1])[None, :]).astype(float)
    return np.asarray(((F - ind) ** 2).sum(axis=1))


def month_blocks(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """zq per month block from the stored c, s, p_ge of ALL rows of the stat (production's one-grid-per-block construction)."""
    mo = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m").to_numpy()
    out: dict[str, np.ndarray] = {}
    for m in np.unique(mo):
        sel = np.flatnonzero(mo == m)
        z = block_zq(
            df["c"].to_numpy()[sel], df["s"].to_numpy()[sel], df["p_ge"].iloc[sel].tolist()
        )
        if z["inconsistent"]:
            raise RuntimeError(f"block {m}: contradictory survival brackets")
        out[str(m)] = z["mid"]
    return out


def quantile_grid(df: pd.DataFrame, rows: np.ndarray, blocks: dict[str, np.ndarray]) -> np.ndarray:
    mo = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m").to_numpy()[rows]
    q = np.zeros((len(rows), NQ))
    for m in np.unique(mo):
        sel = np.flatnonzero(mo == m)
        r = rows[sel]
        q[sel] = grid_q(df["c"].to_numpy()[r], df["s"].to_numpy()[r], blocks[str(m)])
    return q


def coverage(q: np.ndarray, y: np.ndarray) -> dict[str, float]:
    return {t: float(np.mean(y <= np.ceil(q[:, j] - 0.5))) for t, j in TAU_IDX.items()}


# --------------------------------------------------------------------------- inference and words


def boot(values: np.ndarray, clusters: np.ndarray) -> dict[str, float]:
    from nba.props.metrics import _clustered_boot_means

    rng = np.random.default_rng(SEED)
    b = _clustered_boot_means(np.asarray(values, dtype=float), np.asarray(clusters), N_BOOT, rng)
    return {
        "point": float(np.mean(values)),
        "lo": float(np.quantile(b, ALPHA / 2)),
        "hi": float(np.quantile(b, 1 - ALPHA / 2)),
        "p": float((1 + np.sum(b >= 0)) / (1 + N_BOOT)),
        "n": int(len(values)),
    }


def evaluate(res: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """res[season][stat] = {point, lo, hi, p, a4_point, cov_dev (max |cov A2 - cov A0| over tau .10/.90)} for A2 - A0 on the affected slice.
    Returns the overall word and the per-stat, per-season table. BH over the 4 stats within a season."""
    seasons = sorted(res)
    table: dict[str, dict[str, Any]] = {}
    for s in seasons:
        adj = bh_adjust(np.array([res[s][st]["p"] for st in STATS]))
        for st, pa in zip(STATS, adj, strict=True):
            r = res[s][st]
            clears = r["point"] <= -FLOORS[st] and r["hi"] < 0 and pa <= ALPHA
            gain, a4gain = -r["point"], -r["a4_point"]
            table.setdefault(s, {})[st] = {
                **r,
                "p_bh": float(pa),
                "clears": bool(clears),
                "placebo": bool(clears and a4gain >= PLACEBO_SHARE * gain),
                "g1": bool(r["point"] - r["a4_point"] <= -0.5 * FLOORS[st]),
                "g3": bool(r["cov_dev"] <= COV_TOL),
                "harm": bool(r["lo"] > 0),
            }
    both = [st for st in STATS if all(table[s][st]["clears"] for s in seasons)]
    any_clear = [st for st in STATS if any(table[s][st]["clears"] for s in seasons)]
    g2 = not any(table[s][st]["harm"] for s in seasons for st in STATS)
    good = [
        st
        for st in both
        if g2
        and all(
            not table[s][st]["placebo"] and table[s][st]["g1"] and table[s][st]["g3"]
            for s in seasons
        )
    ]
    if good:
        word = "PASS" if "pts" in good else "PASS_OTHER_STAT"
    elif both:
        word = (
            "PLACEBO_TOO"
            if any(table[s][st]["placebo"] for s in seasons for st in both)
            else "FAIL_GUARD"
        )
    elif any_clear:
        word = "FAIL_ONE_SEASON"
    else:
        word = "FAIL_NO_GAIN"
    return {"word": word, "g2_no_harm": g2, "cleared_both": both, "good": good, "table": table}


# --------------------------------------------------------------------------- calibrate (season 2022 only)


def calibrate(c: Any | None = None) -> dict[str, Any]:
    """kappa_c = (regular-season minutes in a player's first <= 20 games for his first team) / (his preseason mpg x the same number of games),
    pooled over players with n_pre >= 2 and >= 10 regular games, by preseason-starter flag. Season 2022 only."""
    c = c or connect(DB)
    S = CALIB_SEASON
    reg = (
        c.execute(
            "select s.player_id, s.team_id, g.game_date, s.game_id, s.minutes from player_game_stats s join games g using(game_id) "
            "where substr(s.game_id,1,3)='002' and s.minutes>0 and g.season=?",
            [S],
        )
        .df()
        .sort_values(["player_id", "game_date", "game_id"])
    )
    first_team = reg.groupby("player_id").team_id.first().rename("first_team")
    reg = reg.merge(first_team, on="player_id")
    reg = reg[reg.team_id == reg.first_team]
    reg = reg.assign(order=reg.groupby("player_id").cumcount())
    f20 = (
        reg[reg.order < 20]
        .groupby("player_id")
        .agg(reg_min=("minutes", "sum"), reg_n=("minutes", "size"))
        .reset_index()
    )
    p = pre_agg(load_pre_rows(c, S)).merge(first_team.reset_index(), on="player_id")
    p = p[(p.team_id == p.first_team) & (p.pre_n >= COVER_MIN_GAMES)]
    m = p.merge(f20, on="player_id")
    m = m[m.reg_n >= 10]
    m = m.assign(pre_scaled=m.pre_m / m.pre_n * m.reg_n)
    out: dict[str, Any] = {
        "season": S,
        "definition": "sum(regular minutes, first <=20 games, first team) / sum(preseason mpg x same games)",
    }
    for name, flag in (("starter", True), ("bench", False)):
        x = m[m.pre_start == flag]
        out[name] = float(x.reg_min.sum() / x.pre_scaled.sum())
        out[f"n_{name}"] = int(len(x))
    return out


# --------------------------------------------------------------------------- rehearse (no data)


GATE_N = {"2023": (1202, 487), "2024": (1025, 468)}  # mover K=10 rows, games (gate.json)
SD_DIFF = {"pts": 0.174, "reb": 0.072, "ast": 0.052, "fg3m": 0.027}  # label-free power sim
WORLDS: dict[str, dict[str, Any]] = {
    "pass": {
        "eff": {"pts": 0.03, "reb": 0.012, "ast": 0.008, "fg3m": 0.004},
        "seasons": ("2023", "2024"),
        "keep": 0.10,
        "cov_dev": 0.0,
        "word": "PASS",
    },
    "other_stat": {
        "eff": {"pts": 0.0, "reb": 0.0, "ast": 0.008, "fg3m": 0.004},
        "seasons": ("2023", "2024"),
        "keep": 0.10,
        "cov_dev": 0.0,
        "word": "PASS_OTHER_STAT",
    },
    "placebo_too": {
        "eff": {"pts": 0.03, "reb": 0.012, "ast": 0.008, "fg3m": 0.004},
        "seasons": ("2023", "2024"),
        "keep": 1.0,
        "cov_dev": 0.0,
        "word": "PLACEBO_TOO",
    },
    "guard": {
        "eff": {"pts": 0.03, "reb": 0.012, "ast": 0.008, "fg3m": 0.004},
        "seasons": ("2023", "2024"),
        "keep": 0.10,
        "cov_dev": 0.04,
        "word": "FAIL_GUARD",
    },
    "one_season": {
        "eff": {"pts": 0.03, "reb": 0.012, "ast": 0.008, "fg3m": 0.004},
        "seasons": ("2023",),
        "keep": 0.10,
        "cov_dev": 0.0,
        "word": "FAIL_ONE_SEASON",
    },
    "no_gain": {
        "eff": {"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0},
        "seasons": ("2023", "2024"),
        "keep": 0.0,
        "cov_dev": 0.0,
        "word": "FAIL_NO_GAIN",
    },
}


def synth_cell(world: str, season: str, stat: str, rng: np.random.Generator) -> dict[str, Any]:
    w = WORLDS[world]
    n, ng = GATE_N[season]
    games = rng.integers(0, ng, n)
    u = rng.standard_normal(ng)
    sd = SD_DIFF[stat]
    eff = w["eff"][stat] if season in w["seasons"] else 0.0
    noise = sd * (np.sqrt(0.3) * u[games] + np.sqrt(0.7) * rng.standard_normal(n))
    d2 = -eff + noise
    d4 = -w["keep"] * eff + sd * (np.sqrt(0.3) * u[games] + np.sqrt(0.7) * rng.standard_normal(n))
    b = boot(d2, games)
    return {**b, "a4_point": float(d4.mean()), "cov_dev": float(w["cov_dev"])}


def rehearse(seeds: int = 10, worlds: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"seeds": seeds, "worlds": {}}
    for wname in worlds or list(WORLDS):
        words = []
        for sd in range(seeds):
            rng = np.random.default_rng(1000 * (list(WORLDS).index(wname) + 1) + sd)
            res = {s: {st: synth_cell(wname, s, st, rng) for st in STATS} for s in ("2023", "2024")}
            words.append(evaluate(res)["word"])
        want = WORLDS[wname]["word"]
        out["worlds"][wname] = {"want": want, "words": words, "ok": sum(w == want for w in words)}
        _log(f"world {wname}: {out['worlds'][wname]['ok']}/{seeds} {want}")
    out["total_ok"] = sum(v["ok"] for v in out["worlds"].values())
    out["total"] = seeds * len(out["worlds"])
    return out


def false_pass_rate(seeds: int = 200) -> dict[str, Any]:
    """no_gain world: how often does any cell clear a season / does the overall word leave FAIL_NO_GAIN."""
    n_word = 0
    n_cell = 0
    words: dict[str, int] = {}
    for sd in range(seeds):
        rng = np.random.default_rng(777000 + sd)
        res = {s: {st: synth_cell("no_gain", s, st, rng) for st in STATS} for s in ("2023", "2024")}
        ev = evaluate(res)
        n_word += ev["word"] != "FAIL_NO_GAIN"
        words[ev["word"]] = words.get(ev["word"], 0) + 1
        n_cell += any(ev["table"][s][st]["clears"] for s in ev["table"] for st in STATS)
    return {
        "seeds": seeds,
        "word_not_no_gain": int(n_word),
        "any_cell_clears": int(n_cell),
        "words": words,
    }


# --------------------------------------------------------------------------- check (label-free preflight + A0 reproduction)


def fingerprints(c: Any, h: Any) -> dict[str, str]:
    pre = c.execute(
        "select g.season, g.game_date, s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter "
        "from preseason_player_game_stats s join preseason_games g using(game_id) where g.season<=? order by 3,4",
        [MAX_SEASON],
    ).df()
    reg = load_reg(c, h)[
        ["player_id", "team_id", "season", "game_id", "minutes", "pts", "reb", "ast", "fg3m"]
    ]
    return {
        "FP_PRESEASON_LE2024": sha_table(pre),
        "FP_REG_LE2024": sha_table(reg.sort_values(["game_id", "player_id"])),
    }


def a0_reproduction(season: int) -> list[dict[str, Any]]:
    """Recompute the integer CRPS of the stored production forecast (from p_ge, and from the rebuilt grid) and compare with the stored a0 JSON."""
    out = []
    for st in STATS:
        df = read_rows(st, season)
        ref = json.loads((ODDS / f"model_rows_{season}_{st}.a0.json").read_text())
        y = df["y"].to_numpy(dtype=float)
        c1 = float(crps_int_pge(df["p_ge"].tolist(), y).mean())
        blocks = month_blocks(df)
        q = quantile_grid(df, np.arange(len(df)), blocks)
        c2 = float(crps_int_q(q, y).mean())
        pge_mat = np.stack([np.pad(np.asarray(p, float), (0, 90 - len(p))) for p in df["p_ge"]])
        mism = 0
        for k in range(1, 85):
            mism += int(np.sum(np.abs((q > k - 0.5).sum(axis=1) / NQ - pge_mat[:, k - 1]) > 1e-9))
        out.append(
            {
                "season": season,
                "stat": st,
                "n": len(df),
                "n_ref": ref["n"],
                "crps_pge": c1,
                "crps_grid": c2,
                "crps_ref": ref["crps_int"],
                "grid_vs_stored_pge_cells_differing": mism,
                "ok": bool(
                    abs(c1 - ref["crps_int"]) < 1e-9
                    and abs(c2 - ref["crps_int"]) < 1e-9
                    and mism == 0
                    and len(df) == ref["n"]
                ),
            }
        )
    return out


def run_check(with_a0: bool = True) -> dict[str, Any]:
    c, h = connect(DB), connect(HIST_DB)
    reg, fe = load_reg(c, h), load_first_ever(c, h)
    out: dict[str, Any] = {"versions": versions(), "fingerprints": fingerprints(c, h), "inputs": {}}
    pins = declared()
    out["pins_declared"] = sorted(pins)
    for s in (SELECT, REPORT):
        for st in STATS:
            f = ODDS / f"model_rows_{s}_{st}.parquet"
            out["inputs"][f"MODEL_ROWS_{s}_{st.upper()}_SHA256"] = file_sha256(f)
            out["inputs"][f"A0_REF_{s}_{st.upper()}_SHA256"] = file_sha256(
                ODDS / f"model_rows_{s}_{st}.a0.json"
            )
    for s in (SELECT, REPORT):
        out["inputs"][f"PAIRS_SHA256_{s}"] = file_sha256(PAIRS / f"pairs_{s}.parquet")
    out["mismatch_vs_rule"] = {
        k: [pins.get(k), v]
        for k, v in {**out["inputs"], **out["fingerprints"]}.items()
        if pins.get(k) != v
    }
    out["lib_mismatch"] = {
        k: [pins.get(f"LIB_{k.upper()}"), v]
        for k, v in out["versions"].items()
        if pins.get(f"LIB_{k.upper()}") != v
    }
    known: dict[str, Any] = {}
    for s in (SELECT, REPORT):
        pre = load_pre_rows(c, s)
        opener = reg[reg.season == s].game_date.min()
        known[str(s)] = {
            "max_preseason_date": str(pd.to_datetime(pre.game_date).max().date()),
            "opener": str(pd.to_datetime(opener).date()),
            "ok": bool(pd.to_datetime(pre.game_date).max() < pd.to_datetime(opener)),
        }
    out["known_at"] = known
    # planted future: drop every regular-season row of the season dated after the row's game and the context of the first game must not change
    s = SELECT
    rows = read_rows(
        "pts",
        s,
        ["game_id", "player_id", "team_id", "game_date", "min10", "starter10", "m_recency"],
    )
    pre = load_pre_rows(c, s)
    ctx = build_ctx(rows, reg, fe, pre, s)
    kappa = {"starter": 1.0, "bench": 1.0}
    lam = lambdas(ctx, rows["m_recency"].to_numpy(), "pts", "A2", kappa)
    cut = pd.Timestamp(rows.game_date.min()) + pd.Timedelta(days=30)
    early = pd.to_datetime(rows.game_date) <= cut
    reg_cut = reg[~((reg.season == s) & (pd.to_datetime(reg.game_date) > cut))]
    ctx2 = build_ctx(rows[early.to_numpy()].reset_index(drop=True), reg_cut, fe, pre, s)
    lam2 = lambdas(ctx2, rows["m_recency"].to_numpy()[early.to_numpy()], "pts", "A2", kappa)
    out["planted_future"] = {
        "rows_compared": int(early.sum()),
        "max_abs_diff": float(np.abs(lam[early.to_numpy()] - lam2).max()),
        "ok": bool(np.array_equal(lam[early.to_numpy()], lam2)),
    }
    out["lambda_identity_A0"] = {
        "all_ones_when_weights_zero": bool(
            np.all(
                lambdas(ctx.assign(pre_n=0), rows["m_recency"].to_numpy(), "pts", "A2", kappa)
                == 1.0
            )
        )
    }
    if with_a0:
        out["a0_reproduction"] = a0_reproduction(SELECT) + a0_reproduction(REPORT)
    return out


# --------------------------------------------------------------------------- run (real arms; behind the frozen rule only)


def score_cells(
    season: int,
    K: int,
    kind: str,
    kappa: dict[str, float],
    reg: pd.DataFrame,
    fe: pd.Series,
    c: Any,
) -> dict[str, Any]:
    pre = load_pre_rows(c, season)
    cells: dict[str, Any] = {}
    for st in STATS:
        df = read_rows(st, season)
        ctx = build_ctx(df, reg, fe, pre, season)
        aff_mask = (ctx["slice"].to_numpy() == kind) & (ctx["gwt"].to_numpy() < K)
        rows = np.flatnonzero(aff_mask)
        blocks = month_blocks(df)
        q0 = quantile_grid(df, rows, blocks)
        y = df["y"].to_numpy(dtype=float)[rows]
        gid = df["game_id"].to_numpy()[rows]
        base = crps_int_q(q0, y)
        perm = a4_perm(ctx, K, kind, SEED)
        arms: dict[str, Any] = {}
        for arm in ("A1", "A2", "A3", "A4"):
            lam = lambdas(
                ctx,
                df["m_recency"].to_numpy(),
                st,
                arm,
                kappa,
                K,
                kind,
                perm if arm == "A4" else None,
            )[rows]
            qa = q0 * lam[:, None]
            sc = crps_int_q(qa, y)
            arms[arm] = {"delta": boot(sc - base, gid), "coverage": coverage(qa, y)}
        cov0 = coverage(q0, y)
        cov_dev = max(abs(arms["A2"]["coverage"][t] - cov0[t]) for t in cov0)
        covered = ctx["pre_n"].to_numpy()[rows] >= COVER_MIN_GAMES
        cells[st] = {
            "n": int(len(rows)),
            "games": int(len(np.unique(gid))),
            "crps_A0": float(base.mean()),
            "coverage_A0": cov0,
            "arms": arms,
            "cov_dev": float(cov_dev),
            "covered_n": int(covered.sum()),
            "covered_A2_delta": boot(
                (
                    crps_int_q(
                        q0
                        * lambdas(ctx, df["m_recency"].to_numpy(), st, "A2", kappa, K, kind)[rows][
                            :, None
                        ],
                        y,
                    )
                    - base
                )[covered],
                gid[covered],
            )
            if covered.any()
            else None,
            "A2_vs_A4": boot(
                crps_int_q(
                    q0
                    * lambdas(ctx, df["m_recency"].to_numpy(), st, "A2", kappa, K, kind)[rows][
                        :, None
                    ],
                    y,
                )
                - crps_int_q(
                    q0
                    * lambdas(ctx, df["m_recency"].to_numpy(), st, "A4", kappa, K, kind, perm)[
                        rows
                    ][:, None],
                    y,
                ),
                gid,
            ),
        }
    return cells


def run_real(sha: str | None, kappa_path: Path | None = None) -> dict[str, Any]:
    require_ready(sha)  # frozen rule + pinned harness BEFORE any outcome is read
    pins = declared()
    for k, v in versions().items():
        if pins.get(f"LIB_{k.upper()}") != v:
            raise RuntimeError(
                f"library {k} {v} differs from the pinned {pins.get(f'LIB_{k.upper()}')}"
            )
    kappa_doc = json.loads((kappa_path or OUT_DIR / "calibrate.json").read_text())
    if hashlib.sha256(
        (kappa_path or OUT_DIR / "calibrate.json").read_bytes()
    ).hexdigest() != pins.get("KAPPA_JSON_SHA256"):
        raise RuntimeError("calibrate.json does not hash to KAPPA_JSON_SHA256 in the rule")
    kappa = {"starter": kappa_doc["starter"], "bench": kappa_doc["bench"]}
    c, h = connect(DB), connect(HIST_DB)
    reg, fe = load_reg(c, h), load_first_ever(c, h)
    out: dict[str, Any] = {
        "rule_sha256": sha,
        "kappa": kappa,
        "K10": {},
        "sensitivity": {},
        "rookies": {},
    }
    for s in (SELECT, REPORT):
        _log(f"season {s} K=10 movers")
        out["K10"][str(s)] = score_cells(s, K_MAIN, "mover", kappa, reg, fe, c)
    res = {
        s: {
            st: {
                **out["K10"][s][st]["arms"]["A2"]["delta"],
                "a4_point": out["K10"][s][st]["arms"]["A4"]["delta"]["point"],
                "cov_dev": out["K10"][s][st]["cov_dev"],
            }
            for st in STATS
        }
        for s in out["K10"]
    }
    out["verdict"] = evaluate(res)
    for K in (5, 20):
        for s in (SELECT, REPORT):
            out["sensitivity"][f"K{K}_{s}"] = score_cells(s, K, "mover", kappa, reg, fe, c)
    for s in (SELECT, REPORT):
        out["rookies"][str(s)] = score_cells(s, K_MAIN, "rookie", kappa, reg, fe, c)
    return out


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="preseason_role_prior")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze")
    sub.add_parser("pin")
    g = sub.add_parser("gate")
    g.add_argument("--out", default=None)
    sub.add_parser("calibrate").add_argument("--out", default=str(OUT_DIR / "calibrate.json"))
    ck = sub.add_parser("check")
    ck.add_argument("--no-a0", action="store_true")
    ck.add_argument("--out", default=None)
    rh = sub.add_parser("rehearse")
    rh.add_argument("--world", default="all")
    rh.add_argument("--seeds", type=int, default=10)
    rh.add_argument("--out", default=None)
    r = sub.add_parser("run")
    r.add_argument("--sha", default=None)
    r.add_argument("--out", default=str(OUT_DIR / "results.json"))
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        sha = frozen_sha()
        print(f"rule sha256 {sha}")
        print(f'git commit --allow-empty -m "prereg({ID}): {sha}"')
        return 0
    if a.cmd == "pin":
        blobs = disk_blobs()
        pin = pin_of(blobs)
        for k in sorted(blobs):
            print(f"{blobs[k]}  {k}")
        print(f"pin {pin}")
        print(f'git commit --allow-empty -m "harness({ID}): {pin}"')
        return 0
    if a.cmd == "gate":
        res = run_gate()
        text = json.dumps(res, indent=1, sort_keys=True, default=str)
        if a.out:
            Path(a.out).write_text(text + "\n")
        print(text)
        return 0
    if a.cmd == "calibrate":
        res = calibrate()
        Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        print(json.dumps(res, indent=1, sort_keys=True))
        return 0
    if a.cmd == "check":
        res = run_check(with_a0=not a.no_a0)
        text = json.dumps(res, indent=1, sort_keys=True, default=str)
        if a.out:
            Path(a.out).write_text(text + "\n")
        print(text)
        return 0
    if a.cmd == "rehearse":
        res = rehearse(a.seeds, None if a.world == "all" else [a.world])
        res["false_pass_no_gain_200"] = false_pass_rate(200)
        text = json.dumps(res, indent=1, sort_keys=True)
        if a.out:
            Path(a.out).write_text(text + "\n")
        print(text)
        return 0 if res["total_ok"] == res["total"] else 1
    res = run_real(a.sha)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
