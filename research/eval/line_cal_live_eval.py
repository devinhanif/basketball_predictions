# ruff: noqa: E501
"""LINE_CAL_LIVE harness (rule: docs/prereg/LINE_CAL_LIVE.md).

    M="-m research.eval.line_cal_live_eval"
    .venv-1/bin/python -W ignore $M status [--sha <64 hex>]           # counts only: games, legs, look date
    .venv-1/bin/python -W ignore $M dryrun                            # synthetic outcomes end to end, no y read
    .venv-1/bin/python -W ignore $M run --sha <64 hex>                # the one look, after `prereg(LINE_CAL_LIVE): <sha>`

RAW (production's integer-grid P(over)) against CAL (the frozen LINE_CALIBRATION map, payload sha
443743a1, Pinnacle cell, applied as served; no refit) on scored, played production legs at Pinnacle's
t60 main x.5 line (lead >= 55 min). Side by RAW < 0.5. Window: slate dates strictly after the ET date of
the freeze commit (read from git); 2026-10-20..freeze is reported once, at the look, as an EXPOSED
labelled secondary. One look: the first slate date with >= 200 games and >= 5,000 legs in the window;
backstop 2027-01-31; fewer than 150 games is INSUFFICIENT_DATA. ``run`` uses the real ET clock only,
requires every window game settled (postponed and unforecast games listed, not blocking), and refuses
unless the executing modules are byte-identical to the freeze commit. It is meant to run from a checkout
of the freeze commit (``git worktree add <dir> <freeze commit>``) with ``--data-root`` the main checkout;
later edits on main to a pinned file are listed in the results as a serving difference, not a stop.

Read-only on ``nba.duckdb`` and ``data/markets/market_asof.duckdb``. Season 2026 only in every live
query (``season = 2026``); the 2023-24 Pinnacle and DraftKings t60 legs (pinned pairs frames) are read for C0
and the x band (DraftKings for S3 only).
Season 2025 is never loaded. ``status`` and ``dryrun`` never read an outcome (no ``y``, no score column);
``run`` reads outcomes only after the look is reached and settled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl

from nba.forward import scorecard as served_scorecard
from nba.parlay import line_cal as lc
from nba.parlay import top_source as served_top_source
from research.eval import line_cal_live_gate as gate

RULE = "LINE_CAL_LIVE"
REPO = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/LINE_CAL_LIVE.md"
MARKER = "=== RESULTS BELOW ==="
OUT_REL = "reports/prereg_line_cal_live/results.json"
BOOK = "pinnacle"
DK = "draftkings"
STATS = gate.STATS
PRE_START = date(2026, 10, 20)
LOOK_GAMES = 200
LOOK_LEGS = 5000
MIN_VERDICT_GAMES = 150
BACKSTOP = date(2027, 1, 31)
FLOOR = -0.0015
SIDE_GAP = 0.03
POOLED_GAP = 0.015
MAX_LEAD_DROP = 0.05
B = 2000
SEED_GAME = 0
SEED_DATE = 1
MKT_EPS = 1e-9
C0_BLOCK = date(2025, 7, 1)
HALF_LIFE = 365.0
PAIRS_SHA = {
    2023: "050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569",
    2024: "560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c",
}
ET = ZoneInfo("America/New_York")
MINUTES_SHORT = -5.0
MINUTES_LONG = 5.0
POSTPONED_AFTER_H = 36.0
#: The operational rule: these files must be byte-identical to the freeze commit when ``run`` executes.
PINNED = (
    "research/eval/line_cal_live_eval.py",
    "research/eval/line_cal_live_gate.py",
    "nba/parlay/line_cal.py",
    "nba/parlay/line_cal_map_v1.json",
    "nba/parlay/top_source.py",
    "nba/forward/scorecard.py",
)
PASS_WORDS = ("checks", "verdict_core", "failing")

Frame = dict[str, np.ndarray]


# ------------------------------------------------------------------ rule guards


def rule_text_sha(text: str) -> str:
    """sha256 of every line above the marker, as `awk '/^MARKER$/{exit} {print}' | shasum -a 256`."""
    out: list[str] = []
    for ln in text.splitlines():
        if ln == MARKER:
            break
        out.append(ln + "\n")
    return hashlib.sha256("".join(out).encode()).hexdigest()


def is_frozen(text: str) -> bool:
    return any(ln.startswith("Status: **FROZEN") for ln in text.splitlines()[:6])


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def freeze_commit(repo: Path, sha: str) -> tuple[str, date]:
    """(commit, ET date) of the oldest commit whose message carries `prereg(LINE_CAL_LIVE): <sha>` and
    whose rule text hashes to ``sha`` with a FROZEN status line."""
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("--sha must be the 64-hex sha256 of the rule text")
    log = _git(
        repo, "log", "--all", "--fixed-strings", f"--grep=prereg({RULE}): {sha}", "--format=%H\t%cI"
    ).split("\n")
    rows = [ln.split("\t") for ln in log if ln.strip()]
    if not rows:
        raise SystemExit(f"refused: no commit 'prereg({RULE}): {sha}' (the rule is not frozen)")
    commit, when = rows[-1]
    text = _git(repo, "show", f"{commit}:{DOC_REL}")
    if rule_text_sha(text) != sha or not is_frozen(text):
        raise SystemExit(
            "refused: the rule text at the freeze commit does not match the sha or is not FROZEN"
        )
    return commit, datetime.fromisoformat(when).astimezone(ET).date()


def check_doc(repo: Path, sha: str) -> None:
    text = (repo / DOC_REL).read_text()
    if not is_frozen(text):
        raise SystemExit("refused: docs/prereg/LINE_CAL_LIVE.md is a DRAFT")
    if rule_text_sha(text) != sha:
        raise SystemExit(
            "refused: the rule text in the tree differs from --sha (edited after the freeze?)"
        )


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ look


@dataclass(frozen=True)
class Look:
    kind: str  # "look" | "backstop" | "not_reached"
    date: date | None
    games: int
    legs: int


def per_date_counts(legs: list[gate.Leg]) -> list[tuple[date, int, int]]:
    games: dict[date, set[str]] = {}
    n: dict[date, int] = {}
    for x in legs:
        games.setdefault(x.game_date, set()).add(x.game_id)
        n[x.game_date] = n.get(x.game_date, 0) + 1
    return [(d, len(games[d]), n[d]) for d in sorted(n)]


def find_look(counts: list[tuple[date, int, int]], start: date, today: date) -> Look:
    """First slate date d (start <= d < today) at which the window [start, d] holds >= LOOK_GAMES games
    and >= LOOK_LEGS legs; else the backstop once today > BACKSTOP; else not reached."""
    g = n = 0
    for d, gd, nd in counts:
        if d < start or d >= today or d > BACKSTOP:
            continue
        g += gd
        n += nd
        if g >= LOOK_GAMES and n >= LOOK_LEGS:
            return Look("look", d, g, n)
    if today > BACKSTOP:
        return Look("backstop", BACKSTOP, g, n)
    return Look("not_reached", None, g, n)


def real_today() -> date:
    """The real ET calendar date (``run`` accepts no other clock)."""
    return datetime.now(ET).date()


@dataclass(frozen=True)
class GameState:
    game_id: str
    date: date
    tip_utc: datetime | None
    final: bool
    predicted: bool
    scored: bool


def classify_settlement(games: list[GameState], now_utc: datetime) -> dict[str, list[str]]:
    """Settled, for every game on a window date: a game with a final score AND production predictions must
    have forward_scores rows (else ``pending``, which blocks). A game with no final score 36 h after its
    tip is ``unplayed_or_postponed`` (listed, not blocking); before that it is ``pending``. A final game
    with no production prediction is ``unforecast`` (listed, not blocking)."""
    out: dict[str, list[str]] = {
        "settled": [],
        "pending": [],
        "unplayed_or_postponed": [],
        "unforecast": [],
    }
    for g in sorted(games, key=lambda x: (x.date, x.game_id)):
        if g.final and not g.predicted:
            out["unforecast"].append(g.game_id)
        elif g.final and g.scored:
            out["settled"].append(g.game_id)
        elif g.final:
            out["pending"].append(g.game_id)
        else:
            tip = g.tip_utc or datetime(
                g.date.year, g.date.month, g.date.day, 23, 59, tzinfo=ET
            ).astimezone(ZoneInfo("UTC"))
            if now_utc - tip > timedelta(hours=POSTPONED_AFTER_H):
                out["unplayed_or_postponed"].append(g.game_id)
            else:
                out["pending"].append(g.game_id)
    return out


def load_game_states(root: Path, start: date, end: date) -> list[GameState]:
    """Every game on an ET date in [start, end]: the schedule file's games plus ``games`` rows (season 2026)."""
    s = pl.read_parquet(root / "data/schedule/live_tips_2026-27.parquet").with_columns(
        pl.col("gameDateTimeEst").str.slice(0, 10).str.to_date().alias("d")
    )
    sched = {
        r[0]: (r[2], datetime.fromisoformat(r[1].replace("Z", "+00:00")))
        for r in s.select("gameId", "gameDateTimeUTC", "d").iter_rows()
        if start <= r[2] <= end
    }
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    g_rows = con.execute(
        "SELECT game_id, game_date, coalesce(home_pts, 0) > 0 AND coalesce(away_pts, 0) > 0 FROM games "
        "WHERE season = ? AND game_date BETWEEN ? AND ?",
        [gate.SEASON, start, end],
    ).fetchall()
    final = {r[0] for r in g_rows if r[2]}
    dates = {r[0]: r[1] for r in g_rows}
    ids = sorted(set(sched) | set(dates))
    predicted: set[str] = set()
    scored: set[str] = set()
    if ids:
        gm = ",".join("?" for _ in ids)
        # a game with box rows is final even if the score ingest lags (an ingest outage is not a postponement)
        final |= {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT game_id FROM player_game_stats WHERE game_id IN ({gm})",  # noqa: S608
                ids,
            ).fetchall()
        }
        predicted = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT game_id FROM forward_predictions WHERE model_name = ? AND game_id IN ({gm})",  # noqa: S608
                [gate.PROD, *ids],
            ).fetchall()
        }
        scored = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT game_id FROM forward_scores WHERE model_name = ? AND season = ? AND game_id IN ({gm})",  # noqa: S608
                [gate.PROD, gate.SEASON, *ids],
            ).fetchall()
        }
    con.close()
    return [
        GameState(
            gid,
            sched[gid][0] if gid in sched else dates[gid],
            sched[gid][1] if gid in sched else None,
            gid in final,
            gid in predicted,
            gid in scored,
        )
        for gid in ids
    ]


def executing_files() -> dict[str, Path]:
    """The bytes that actually run: each pinned path mapped to the module file the interpreter loaded."""
    return {
        "research/eval/line_cal_live_eval.py": Path(__file__),
        "research/eval/line_cal_live_gate.py": Path(str(gate.__file__)),
        "nba/parlay/line_cal.py": Path(str(lc.__file__)),
        "nba/parlay/line_cal_map_v1.json": Path(lc.MAP_PATH),
        "nba/parlay/top_source.py": Path(str(served_top_source.__file__)),
        "nba/forward/scorecard.py": Path(str(served_scorecard.__file__)),
    }


def executing_vs_freeze(repo: Path, commit: str) -> tuple[dict[str, str], list[str]]:
    """(sha256 of each executing pinned file, the pinned paths whose executing bytes differ from
    ``git show <commit>:<path>``)."""
    shas: dict[str, str] = {}
    bad: list[str] = []
    for rel, f in executing_files().items():
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        shas[rel] = got
        frozen = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"], capture_output=True, check=False
        )
        if frozen.returncode != 0 or hashlib.sha256(frozen.stdout).hexdigest() != got:
            bad.append(rel)
    return shas, bad


def main_commits_touching_pinned(root: Path, commit: str) -> list[str] | str:
    """Commits on the data-root checkout since the freeze that touch a pinned file (a serving
    difference, descriptive)."""
    try:
        out = _git(root, "log", "--format=%h %cI %s", f"{commit}..HEAD", "--", *PINNED)
    except (subprocess.CalledProcessError, OSError):
        return "unknown (data root is not a git checkout holding the freeze commit)"
    return [ln for ln in out.splitlines() if ln.strip()]


def run_once_guard(repo: Path, root: Path) -> None:
    for base in (repo, root):
        if (base / OUT_REL).exists():
            raise SystemExit(f"refused: {base / OUT_REL} exists (the look runs once)")


def pinned_diff(repo: Path, commit: str) -> list[str]:
    """Pinned files that differ (working tree included) from the freeze commit."""
    out = _git(repo, "diff", "--name-only", commit, "--", *PINNED)
    return [ln for ln in out.splitlines() if ln.strip()]


def strip_pass_words(r: dict[str, Any] | None) -> dict[str, Any] | None:
    """Secondaries (DraftKings, EXPOSED pre-freeze) carry no pass word."""
    if r is None:
        return None
    return {k: v for k, v in r.items() if k not in PASS_WORDS}


# ------------------------------------------------------------------ frames


def pairs_frames(root: Path, book: str = BOOK) -> pl.DataFrame:
    parts = []
    for s in (2023, 2024):
        p = root / f"data/ops/model_clv_pairs/pairs_{s}.parquet"
        if file_sha(p) != PAIRS_SHA[s]:
            raise SystemExit(f"refused: {p.name} differs from its LINE_CALIBRATION pin")
        d = pl.read_parquet(
            p, columns=["book", "stat", "snapshot_kind", "season", "game_date", "over"]
        ).filter((pl.col("book") == book) & (pl.col("snapshot_kind") == "t60"))
        if d["season"].n_unique() != 1 or int(d["season"][0]) != s:
            raise SystemExit("refused: unexpected season in a pairs frame")
        parts.append(d)
    return pl.concat(parts)


def c0_rates(pairs: pl.DataFrame) -> dict[str, float]:
    """The frozen harness's C0 at the static map's block: per stat, the 0.5 ** (age / 365)-weighted over
    rate of the 2023-24 t60 legs of the frame's book, age counted from 2025-07-01, clipped to eps."""
    out: dict[str, float] = {}
    for st in STATS:
        d = pairs.filter((pl.col("stat") == st) & pl.col("over").is_not_null())
        age = np.array([(C0_BLOCK - x).days for x in d["game_date"].to_list()], dtype=float)
        if not np.all(age > 0):
            raise SystemExit("refused: a C0 row on or after the block start")
        w = 0.5 ** (age / HALF_LIFE)
        out[st] = float(np.clip(np.sum(w * d["over"].to_numpy()) / w.sum(), lc.EPS, 1 - lc.EPS))
    return out


def x_band(pairs_root: Path, book: str = BOOK) -> dict[str, list[float]]:
    return gate._pairs_x_band(pairs_root, book)


def legs_frame(legs: list[gate.Leg], band: dict[str, list[float]], c0: dict[str, float]) -> Frame:
    """Label-free arrays (no outcome)."""
    st = np.array([x.stat for x in legs])
    pr = np.array([x.p_raw for x in legs])
    xr = lc.logit(pr)
    out = np.zeros(len(legs), bool)
    for s in STATS:
        k = st == s
        out[k] = (xr[k] < band[s][0]) | (xr[k] > band[s][1])
    nan = float("nan")
    return {
        "game": np.array([x.game_id for x in legs]),
        "date": np.array([x.game_date for x in legs], dtype="datetime64[D]"),
        "stat": st,
        "pid": np.array([x.player_id for x in legs]),
        "p_raw": pr,
        "p_cal": np.array([x.p_cal for x in legs]),
        "p_mkt": np.clip(np.array([x.p_mkt for x in legs]), MKT_EPS, 1 - MKT_EPS),
        "c0": np.array([c0[s] for s in st]),
        "starter": np.array([x.starter for x in legs]),
        "p_play": np.array([nan if x.p_play is None else x.p_play for x in legs]),
        "min_sd": np.array([nan if x.min_sd is None else x.min_sd for x in legs]),
        "proj_min": np.array([nan if x.proj_min is None else x.proj_min for x in legs]),
        "mean": np.array([x.mean for x in legs]),
        "line": np.array([x.line for x in legs]),
        "x_out": out,
        "fallback": np.array([x.fallback for x in legs], dtype=bool),
    }


def attach_outcomes(root: Path, legs: list[gate.Leg], F: Frame) -> Frame:
    """Outcomes for ``run`` only: y and version of the same scored row, actual minutes from the box."""
    if not legs:
        return F
    gids = sorted({x.game_id for x in legs})
    lo, hi = min(x.game_date for x in legs), max(x.game_date for x in legs)
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    rows = con.execute(
        """SELECT game_id, target, player_id, made_at, y, version FROM forward_scores
           WHERE status = 'scored' AND model_name = ? AND season = ? AND game_date BETWEEN ? AND ?""",
        [gate.PROD, gate.SEASON, lo, hi],
    ).fetchall()
    gm = ",".join("?" for _ in gids)
    mins = {
        (r[0], int(r[1])): float(r[2])
        for r in con.execute(
            f"""SELECT s.game_id, s.player_id, s.minutes FROM player_game_stats s JOIN games g USING (game_id)
                WHERE g.season = ? AND s.game_id IN ({gm})""",  # noqa: S608
            [gate.SEASON, *gids],
        ).fetchall()
        if r[2] is not None
    }
    con.close()
    latest: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    for r in rows:
        k = (r[0], r[1], int(r[2]))
        if k not in latest or r[3] > latest[k][3]:
            latest[k] = r
    y, ver, act = [], [], []
    for x in legs:
        hit = latest.get((x.game_id, x.stat, x.player_id))
        if hit is None or hit[3] != x.made_at or hit[4] is None:
            raise SystemExit("refused: an outcome row does not match its leg's forecast row")
        y.append(float(hit[4]))
        ver.append(str(hit[5]))
        act.append(mins.get((x.game_id, x.player_id), float("nan")))
    G = dict(F)
    G["y"] = np.array(y)
    G["o"] = (G["y"] > G["line"]).astype(float)
    G["version"] = np.array(ver)
    G["act_min"] = np.array(act)
    return G


def take(F: Frame, k: np.ndarray) -> Frame:
    return {key: v[k] for key, v in F.items()}


# ------------------------------------------------------------------ statistics


class Boot:
    """Paired percentile bootstrap of a leg mean over clusters (one multinomial matrix, shared by every
    statistic). ``p_lt0`` = (1 + #{boot >= 0}) / (B + 1): one-sided p for 'the contrast is below 0'."""

    def __init__(self, clusters: np.ndarray, seed: int, b: int = B) -> None:
        u, self.idx = np.unique(clusters, return_inverse=True)
        self.g = len(u)
        rng = np.random.default_rng(seed)
        self.W = rng.multinomial(self.g, np.full(self.g, 1.0 / self.g), size=b).astype(np.float64)
        self.b = b

    def mean(self, v: np.ndarray, mask: np.ndarray | None = None) -> dict[str, Any]:
        m = np.ones(len(v), bool) if mask is None else mask
        if not m.any():
            return {"point": None, "lo": None, "hi": None, "n": 0, "clusters": 0, "p_lt0": None}
        s = np.bincount(self.idx[m], v[m], self.g)
        n = np.bincount(self.idx[m], None, self.g)
        num, den = self.W @ s, self.W @ n
        with np.errstate(invalid="ignore", divide="ignore"):
            bs = num / den
        bs = bs[np.isfinite(bs)]
        return {
            "point": float(v[m].mean()),
            "lo": float(np.percentile(bs, 2.5)),
            "hi": float(np.percentile(bs, 97.5)),
            "n": int(m.sum()),
            "clusters": int((n > 0).sum()),
            "p_lt0": float((1 + np.sum(bs >= 0)) / (len(bs) + 1)),
        }


def brier(p: np.ndarray, o: np.ndarray) -> np.ndarray:
    return np.asarray((p - o) ** 2)


def logloss(p: np.ndarray, o: np.ndarray, eps: float = lc.EPS) -> np.ndarray:
    q = np.clip(p, eps, 1 - eps)
    return np.asarray(-(o * np.log(q) + (1 - o) * np.log1p(-q)))


def bh(p: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values (step-up), in input order."""
    m = len(p)
    order = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return [float(v) for v in q]


def _r(d: dict[str, Any], nd: int = 6) -> dict[str, Any]:
    return {k: (round(v, nd) if isinstance(v, float) else v) for k, v in d.items()}


@dataclass
class Ctx:
    F: Frame
    bg: Boot
    bd: Boot

    def contrast(self, v: np.ndarray, mask: np.ndarray | None = None) -> dict[str, Any]:
        r = self.bg.mean(v, mask)
        d = self.bd.mean(v, mask)
        r["date_lo"], r["date_hi"], r["dates"] = d["lo"], d["hi"], d["clusters"]
        return _r(r)


def fit_line(p: np.ndarray, o: np.ndarray) -> dict[str, float]:
    """Descriptive logistic fit of the outcome on logit p (S8): intercept and slope, no ridge."""
    x = lc.logit(np.clip(p, lc.EPS, 1 - lc.EPS))
    b = lc.fit_logistic(np.column_stack([np.ones_like(x), x]), o, ridge=0.0)
    return {"intercept": round(float(b[0]), 5), "slope": round(float(b[1]), 5)}


def evaluate(F: Frame) -> dict[str, Any]:
    """Every check, secondary, slice and diagnostic of the rule on one frame with outcomes."""
    o = F["o"]
    pr, pc = F["p_raw"], F["p_cal"]
    cx = Ctx(F, Boot(F["game"], SEED_GAME), Boot(F["date"].astype(str), SEED_DATE))
    under = gate.side_under(pr)
    over = ~under
    db = brier(pc, o) - brier(pr, o)
    dl = logloss(pc, o) - logloss(pr, o)
    res: dict[str, Any] = {
        "n": int(len(o)),
        "games": int(len(np.unique(F["game"]))),
        "dates": int(len(np.unique(F["date"]))),
    }
    prim = cx.contrast(db)
    prim_ll = cx.contrast(dl)
    res["primary_brier_cal_minus_raw"] = prim
    res["log_loss_cal_minus_raw"] = prim_ll
    res["brier_mean"] = {"RAW": float(brier(pr, o).mean()), "CAL": float(brier(pc, o).mean())}
    side: dict[str, Any] = {}
    for nm, k in (("under", under), ("over", over)):
        side[nm] = {
            "brier_cal_minus_raw": cx.contrast(db, k),
            "realised_over_rate": float(o[k].mean()) if k.any() else None,
            "mean_cal_p_over": float(pc[k].mean()) if k.any() else None,
            "mean_raw_p_over": float(pr[k].mean()) if k.any() else None,
            "gap_cal_realised_minus_stated": cx.contrast(o - pc, k),
            "gap_raw_realised_minus_stated": cx.contrast(o - pr, k),
        }
    res["side"] = side
    pooled_gap = float(o.mean() - pc.mean())
    per_stat = {s: cx.contrast(db, F["stat"] == s) for s in STATS}
    role = {"starter": cx.contrast(db, F["starter"]), "bench": cx.contrast(db, ~F["starter"])}

    def gap_ok(nm: str) -> bool:
        g = side[nm]["gap_cal_realised_minus_stated"]["point"]
        return g is not None and abs(g) <= SIDE_GAP

    u, ov = side["under"]["brier_cal_minus_raw"], side["over"]["brier_cal_minus_raw"]
    checks = {
        "P1": bool(prim["point"] <= FLOOR and prim["hi"] < 0),
        "P1b": bool(prim_ll["hi"] < 0),
        "P2": bool(u["hi"] is not None and u["hi"] < 0 and gap_ok("under")),
        "P3": bool(ov["lo"] is not None and ov["lo"] <= 0 and gap_ok("over")),
        "P4": bool(abs(pooled_gap) <= POOLED_GAP),
        "P5": bool(all(v["lo"] is None or v["lo"] <= 0 for v in per_stat.values())),
        "P6": bool(
            role["starter"]["point"] is not None
            and role["starter"]["point"] <= 0
            and role["bench"]["point"] is not None
            and role["bench"]["point"] <= 0
        ),
    }
    res["checks"] = checks
    res["pooled_gap_realised_minus_cal"] = pooled_gap
    if not (checks["P1"] and checks["P1b"]):
        core = "NOT_TRANSPORTED"
    elif all(checks.values()):
        core = "TRANSPORTS"
    else:
        core = "PARTIAL"
    res["verdict_core"] = core
    res["failing"] = [k for k, v in checks.items() if not v]
    # ---- secondaries (labels only)
    mk = F["p_mkt"]
    res["S1_vs_market"] = {
        "brier_cal_minus_mkt": cx.contrast(brier(pc, o) - brier(mk, o)),
        "brier_raw_minus_mkt": cx.contrast(brier(pr, o) - brier(mk, o)),
        "ll_cal_minus_mkt": cx.contrast(logloss(pc, o) - logloss(mk, o, MKT_EPS)),
        "ll_raw_minus_mkt": cx.contrast(logloss(pr, o) - logloss(mk, o, MKT_EPS)),
    }
    c0 = F["c0"]
    res["S2_vs_c0"] = {
        "brier_cal_minus_c0": cx.contrast(brier(pc, o) - brier(c0, o)),
        "ll_cal_minus_c0": cx.contrast(logloss(pc, o) - logloss(c0, o)),
    }
    half = np.full(len(o), 0.5)
    res["S4_coin"] = {
        "brier_cal_minus_coin": cx.contrast(brier(pc, o) - brier(half, o)),
        "brier_raw_minus_coin": cx.contrast(brier(pr, o) - brier(half, o)),
    }
    q4 = bh([float(per_stat[s]["p_lt0"]) for s in STATS])
    res["S5_per_stat"] = {s: {**per_stat[s], "bh_q": round(q4[i], 6)} for i, s in enumerate(STATS)}
    cells = [
        (s, nm, (F["stat"] == s) & k) for s in STATS for nm, k in (("under", under), ("over", over))
    ]
    cres = [cx.contrast(db, k) for _, _, k in cells]
    q8 = bh([1.0 if c["p_lt0"] is None else float(c["p_lt0"]) for c in cres])
    res["S5_side_x_stat"] = {
        f"{s}_{nm}": {**c, "bh_q": round(q8[i], 6)}
        for i, ((s, nm, _), c) in enumerate(zip(cells, cres, strict=True))
    }
    pp, msd = F["p_play"], F["min_sd"]
    months = np.array([str(d)[:7] for d in F["date"]])
    sl: dict[str, np.ndarray] = {
        "starter": F["starter"],
        "bench": ~F["starter"],
        "minutes_uncertain": (pp < gate.PPLAY_UNCERTAIN) | (msd >= gate.MIN_SD_HIGH),
        "p_play_lt_0.9": pp < gate.PPLAY_UNCERTAIN,
        "min_sd_ge_6": msd >= gate.MIN_SD_HIGH,
        "min_sd_unknown": np.isnan(msd),
        "x_outside_fit_band": F["x_out"],
        "fallback_rows": F["fallback"],
        "context_model_rows": ~F["fallback"],
    }
    for mo in sorted(set(months)):
        sl[f"month_{mo}"] = months == mo
    for v in sorted(set(F["version"].tolist())):
        sl[f"version_{v}"] = F["version"] == v
    res["S6_slices"] = {k: cx.contrast(db, m) for k, m in sl.items()}
    edges = np.linspace(0, 1, 11)
    rel: dict[str, Any] = {}
    for arm, p in (("RAW", pr), ("CAL", pc)):
        for nm, k in (("all", np.ones(len(o), bool)), ("under", under), ("over", over)):
            b = np.clip(np.digitize(p, edges) - 1, 0, 9)
            rel[f"{arm}_{nm}"] = [
                {"bin": i, "n": int(((b == i) & k).sum()),
                 "mean_p": float(p[(b == i) & k].mean()) if ((b == i) & k).any() else None,
                 "mean_o": float(o[(b == i) & k].mean()) if ((b == i) & k).any() else None}
                for i in range(10)
            ]  # fmt: skip
    res["S7_reliability"] = rel
    res["S8_recal_fit"] = {"RAW": fit_line(pr, o), "CAL": fit_line(pc, o)}
    mu = F["mean"] < F["line"]
    bridge = {}
    for nm, k in (("under", mu), ("over", ~mu)):
        hit = np.where(nm == "under", 1 - o, o)
        bridge[nm] = {
            "n": int(k.sum()),
            "stated_raw": float(np.where(nm == "under", 1 - pr, pr)[k].mean()) if k.any() else None,
            "stated_cal": float(np.where(nm == "under", 1 - pc, pc)[k].mean()) if k.any() else None,
            "hit_rate": float(hit[k].mean()) if k.any() else None,
            "brier_cal_minus_raw": cx.contrast(db, k),
        }
    res["S9_bridge_mean_vs_line"] = bridge
    # ---- diagnostics (descriptive)
    res["D1_minutes_realisation_under"] = d1(cx, F, under)
    res["D2_error_decomposition_under"] = d2(F, under)
    res["D4_location_or_slope"] = {
        "raw_realised_minus_stated": cx.contrast(o - pr),
        "raw_slope": res["S8_recal_fit"]["RAW"],
    }
    return res


def d1(cx: Ctx, F: Frame, under: np.ndarray) -> dict[str, Any]:
    o = F["o"]
    delta = F["act_min"] - F["proj_min"]
    groups = {
        "short": under & (delta <= MINUTES_SHORT),
        "near": under & (delta > MINUTES_SHORT) & (delta < MINUTES_LONG),
        "long": under & (delta >= MINUTES_LONG),
        "no_projection": under & np.isnan(delta),
    }
    out: dict[str, Any] = {}
    for arm, p in (("RAW", F["p_raw"]), ("CAL", F["p_cal"])):
        excess = (1 - p) - (1 - o)  # stated P(under) - hit
        total = float(excess[under].sum())
        out[arm] = {
            g: {
                "n": int(k.sum()),
                "stated_p_under": float((1 - p)[k].mean()) if k.any() else None,
                "under_rate": float((1 - o)[k].mean()) if k.any() else None,
                "gap_stated_minus_hit": cx.contrast(excess, k),
                "share_of_excess": (float(excess[k].sum()) / total)
                if total != 0 and k.any()
                else None,
            }
            for g, k in groups.items()
        }
    return out


def d2(F: Frame, under: np.ndarray) -> dict[str, Any]:
    k = (
        under
        & np.isfinite(F["proj_min"])
        & (F["proj_min"] > 0)
        & np.isfinite(F["act_min"])
        & (F["act_min"] > 0)
    )
    if not k.any():
        return {"n": 0}
    y, mean, pm, am = F["y"][k], F["mean"][k], F["proj_min"][k], F["act_min"][k]
    proj_rate = mean / pm
    mm = (am - pm) * proj_rate
    mr = (y / am - proj_rate) * am
    err = y - mean
    e = float(err.mean())
    return {
        "n": int(k.sum()),
        "mean_error": e,
        "mean_miss_minutes": float(mm.mean()),
        "mean_miss_rate": float(mr.mean()),
        "share_minutes": float(mm.mean() / e) if e != 0 else None,
        "share_rate": float(mr.mean() / e) if e != 0 else None,
        "sq_share_minutes": float(np.sum(mm**2) / np.sum(err**2)) if np.sum(err**2) > 0 else None,
        "sq_share_rate": float(np.sum(mr**2) / np.sum(err**2)) if np.sum(err**2) > 0 else None,
    }


def verdict(core: str, games: int, look_kind: str) -> str:
    if games < MIN_VERDICT_GAMES:
        return "INSUFFICIENT_DATA"
    if look_kind not in ("look", "backstop"):
        raise ValueError("no verdict before the look")
    return core


# ------------------------------------------------------------------ synthetic worlds (dryrun, tests)


def synth_outcomes(F: Frame, world: str, seed: int) -> Frame:
    """Synthetic y, o, act_min, version for a label-free frame. Worlds: 'transport' (truth = CAL),
    'no_transport' (truth = RAW), 'under_gap' (truth = CAL + 0.06 P(over) on under-lean legs: CAL still beats
    RAW but leaves the under side 6 points over-stated, so P2 must fail)."""
    rng = np.random.default_rng(seed)
    if world == "transport":
        pt = F["p_cal"]
    elif world == "no_transport":
        pt = F["p_raw"]
    elif world == "under_gap":
        pt = np.clip(F["p_cal"] + 0.06 * gate.side_under(F["p_raw"]), lc.EPS, 1 - lc.EPS)
    else:
        raise ValueError(world)
    o = (rng.random(len(pt)) < pt).astype(float)
    G = dict(F)
    G["o"] = o
    G["y"] = np.where(o > 0, np.ceil(F["line"]), np.floor(F["line"]))
    pm = np.where(np.isfinite(F["proj_min"]), F["proj_min"], 24.0)
    G["act_min"] = np.clip(pm + rng.normal(0, 4, len(pt)), 1, 48)
    G["version"] = np.full(len(pt), "synthetic")
    return G


EXPECT = {"transport": "TRANSPORTS", "no_transport": "NOT_TRANSPORTED", "under_gap": "PARTIAL"}


def tile(F: Frame, games: int) -> Frame:
    """Repeat a frame under new game ids and shifted dates until it holds >= ``games`` games (dryrun)."""
    g0 = len(np.unique(F["game"]))
    k = max(1, math.ceil(games / max(g0, 1)))
    parts = []
    for i in range(k):
        P = dict(F)
        P["game"] = np.char.add(F["game"].astype(str), f"_t{i}")
        P["date"] = F["date"] + np.timedelta64(100 * i, "D")
        parts.append(P)
    return {key: np.concatenate([p[key] for p in parts]) for key in F}


# ------------------------------------------------------------------ commands


def _load_window(
    root: Path, a: date, b: date, book: str, status: str = "scored"
) -> tuple[list[gate.Leg], dict[str, int]]:
    if b < a:
        return [], {}
    return gate.load_legs(root, a, b, book, status=status)


def cmd_status(
    root: Path, repo: Path, sha: str | None, today: date, assume_freeze: date | None = None
) -> dict[str, Any]:
    """Counts only. Never an outcome."""
    if sha:
        commit, fz = freeze_commit(repo, sha)
        mode = f"FROZEN at {commit[:8]} (ET {fz})"
    else:
        fz = assume_freeze or today - timedelta(days=1)
        mode = f"DRAFT preview (freeze ET date assumed {fz})"
    last = min(today - timedelta(days=1), BACKSTOP)
    legs, why = _load_window(root, PRE_START, last, BOOK)
    counts = per_date_counts(legs)
    start = fz + timedelta(days=1)
    lk = find_look(counts, start, today)
    pre = [c for c in counts if c[0] <= fz]
    win = [c for c in counts if c[0] >= start]
    out = {
        "rule": RULE, "mode": mode, "today": str(today), "window_start": str(start),
        "prefreeze": {"games": sum(c[1] for c in pre), "legs": sum(c[2] for c in pre)},
        "window": {"games": sum(c[1] for c in win), "legs": sum(c[2] for c in win), "dates": len(win)},
        "look": {"kind": lk.kind, "date": None if lk.date is None else str(lk.date),
                 "need_games": LOOK_GAMES, "need_legs": LOOK_LEGS, "backstop": str(BACKSTOP)},
        "drops": why,
    }  # fmt: skip
    return out


def cmd_dryrun(root: Path, today: date) -> tuple[dict[str, Any], bool]:
    legs, why = _load_window(root, PRE_START, min(today - timedelta(days=1), BACKSTOP), BOOK)
    if not legs:
        raise SystemExit("dryrun: no legs")
    pairs = pairs_frames(root)
    F = legs_frame(legs, x_band(root), c0_rates(pairs))
    T = tile(F, LOOK_GAMES)
    out: dict[str, Any] = {"legs_real": len(legs), "games_real": len(np.unique(F["game"])),
                           "games_tiled": int(len(np.unique(T["game"]))), "worlds": {}}  # fmt: skip
    ok = True
    for i, (w, want) in enumerate(EXPECT.items()):
        r = evaluate(synth_outcomes(T, w, seed=100 + i))
        got = verdict(r["verdict_core"], r["games"], "look")
        out["worlds"][w] = {"verdict": got, "expected": want, "failing": r["failing"],
                            "primary": r["primary_brier_cal_minus_raw"]}  # fmt: skip
        ok = ok and got == want
    return out, ok


def cmd_run(root: Path, repo: Path, sha: str, today: date) -> dict[str, Any]:
    """The one look. ``today`` must be the real ET date (a test may pass another date only to see the
    refusal)."""
    if today != real_today():
        raise SystemExit("refused: run uses the real clock only")
    check_doc(repo, sha)
    commit, fz = freeze_commit(repo, sha)
    exec_shas, bad = executing_vs_freeze(repo, commit)
    if bad:
        raise SystemExit(
            f"refused: the executing code differs from the freeze commit {commit[:8]}: {bad} "
            "(run from `git worktree add <dir> <freeze commit>`)"
        )
    moved = pinned_diff(repo, commit)
    if moved:
        raise SystemExit(
            f"refused: pinned files differ from the freeze commit {commit[:8]}: {moved}"
        )
    start = fz + timedelta(days=1)
    lbl, _ = _load_window(root, PRE_START, min(today - timedelta(days=1), BACKSTOP), BOOK)
    lk = find_look(per_date_counts(lbl), start, today)
    if lk.kind == "not_reached" or lk.date is None:
        raise SystemExit(
            f"refused: the look is not reached (window games {lk.games}, legs {lk.legs}); run `status`"
        )
    if not today > lk.date:
        raise SystemExit("refused: the look date has not passed")
    settle = classify_settlement(
        load_game_states(root, start, lk.date), datetime.now(ZoneInfo("UTC"))
    )
    if settle["pending"]:
        raise SystemExit(
            f"refused: {len(settle['pending'])} window games are not settled yet (final but unscored, or not final "
            f"within {POSTPONED_AFTER_H:.0f} h of tip)"
        )
    run_once_guard(repo, root)
    legs, why = _load_window(root, start, lk.date, BOOK)
    lead = why.get("lead_below_55", 0)
    with_line = len(legs) + sum(
        why.get(k, 0)
        for k in ("lead_below_55", "whole_number_line", "no_quartiles_or_starter_rate")
    )
    if with_line and lead / with_line > MAX_LEAD_DROP:
        raise SystemExit(
            "stopped unscored: more than 5% of legs with a t60 main line dropped for lead < 55 min"
        )
    pairs = pairs_frames(root)
    band, c0 = x_band(root), c0_rates(pairs)
    print(f"[run] window {start}..{lk.date}: {len(legs)} legs", flush=True)
    F = attach_outcomes(root, legs, legs_frame(legs, band, c0))
    prim = evaluate(F)
    res: dict[str, Any] = {
        "rule": RULE, "sha": sha, "freeze_commit": commit, "freeze_et_date": str(fz),
        "look": {"kind": lk.kind, "date": str(lk.date)}, "window": [str(start), str(lk.date)],
        "map_sha256": gate.MAP_SHA,
        "pinned_sha256": exec_shas,
        "main_commits_touching_pinned": main_commits_touching_pinned(root, commit),
        "drops": why,
        "settlement": {k: (len(v) if k == "settled" else v) for k, v in settle.items()},
        "verdict": verdict(prim["verdict_core"], prim["games"], lk.kind), "primary": prim,
    }  # fmt: skip
    print("[run] primary done; secondaries", flush=True)
    dk, _ = _load_window(root, start, lk.date, DK)
    if dk:
        dk_band, dk_c0 = x_band(root, DK), c0_rates(pairs_frames(root, DK))
        res["S3_draftkings"] = strip_pass_words(
            evaluate(attach_outcomes(root, dk, legs_frame(dk, dk_band, dk_c0)))
        )
    else:
        res["S3_draftkings"] = None
    pre, _ = _load_window(root, PRE_START, fz, BOOK)
    res["S10_prefreeze_EXPOSED_no_pass"] = (
        strip_pass_words(evaluate(attach_outcomes(root, pre, legs_frame(pre, band, c0))))
        if pre
        else None
    )
    dnp, _ = _load_window(root, start, lk.date, BOOK, status="dnp")
    side = gate.side_under(np.array([x.p_raw for x in dnp])) if dnp else np.zeros(0, bool)
    res["D3_dnp_voided"] = {
        "n": len(dnp), "under_lean": int(side.sum()), "over_lean": int((~side).sum()),
        "by_stat": {s: sum(1 for x in dnp if x.stat == s) for s in STATS},
    }  # fmt: skip
    run_once_guard(repo, root)
    txt = json.dumps(res, indent=1, default=str) + "\n"
    for base in (repo, root):  # the freeze checkout and the main checkout both carry the one result
        out_p = base / OUT_REL
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=("status", "dryrun", "run"))
    ap.add_argument("--data-root", type=Path, default=REPO)
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--sha")
    ap.add_argument(
        "--today", type=date.fromisoformat, help="status and dryrun only; run uses the real clock"
    )
    ap.add_argument(
        "--assume-freeze", type=date.fromisoformat, help="status only, before the freeze"
    )
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.cmd == "status":
        print(
            json.dumps(
                cmd_status(a.data_root, a.repo, a.sha, a.today or real_today(), a.assume_freeze),
                indent=1,
            )
        )
        return 0
    if a.cmd == "dryrun":
        out, ok = cmd_dryrun(a.data_root, a.today or real_today())
        print(json.dumps(out, indent=1, default=str))
        print(f"dryrun {'OK' if ok else 'MISMATCH'} ({time.time() - t0:.1f} s)")
        return 0 if ok else 1
    if a.today is not None or a.assume_freeze is not None:
        print("refused: run takes no --today or --assume-freeze (real clock only)", file=sys.stderr)
        return 2
    if not a.sha:
        print("run needs --sha", file=sys.stderr)
        return 2
    res = cmd_run(a.data_root, a.repo, a.sha, real_today())
    p = res["primary"]
    print(json.dumps({"verdict": res["verdict"], "n": p["n"], "games": p["games"],
                      "primary": p["primary_brier_cal_minus_raw"], "checks": p["checks"]}, indent=1))  # fmt: skip
    print(f"wrote {OUT_REL} ({time.time() - t0:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
