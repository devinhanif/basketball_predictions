# ruff: noqa: E501
"""BEST_PRICE_FORWARD scorer (rule: docs/prereg/BEST_PRICE_FORWARD.md).

Two entry points:

    # label-free: reproduce the frozen gate (must be byte-identical to the committed gate.json)
    .venv/bin/python -W ignore -m research.eval.best_price_forward_eval gate \\
        --data-root /Users/devin/Downloads/nba-prediction --until 2026-11-08 --out <path>

    # a look (maintainer only, on or after the read date, after the freeze commit exists)
    .venv/bin/python -W ignore -m research.eval.best_price_forward_eval score --read 1 \\
        --freeze-commit <sha> --rule-sha <sha256> --data-root /Users/devin/Downloads/nba-prediction \\
        --out reports/best_price_forward/read1.json

Pick construction is the gate's own code (``best_price_forward_gate.build_picks``), so the scorer and
the gate cannot disagree on which picks exist. ``score`` refuses: an unverified or changed Kalshi fee,
a rule text whose hash or status line does not match, a run before the read date, and any row of a
season other than 2026 or dated before the window (the first ET date after the freeze commit) or after
the read's last tip date. Outcomes (box scores, Kalshi results and scalar pages) are read only inside
``score``. Every DuckDB connection is ``read_only=True``. Season 2025 is never read.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np

from nba.parlay import line_cal as lc
from research.eval import best_price_forward_gate as g

RULE = g.RULE
SEASON = g.SEASON
FLOOR = 0.010
B_BOOT = 10_000
SEED_CI = 2026
SEED_SELFTEST_A = 2027
SEED_SHUFFLE = 2028
SEED_SELFTEST_B = 2029
K_SELFTEST = 1_000
N_SHUFFLE = 1_000
LEVEL = {1: 0.005, 2: 0.02}
READ_DATE = {1: date(2027, 1, 15), 2: date(2027, 4, 14)}
LAST_TIP = {1: g.READ1_LAST_TIP, 2: g.READ2_LAST_TIP}
MINIMUM = {1: (250, 35, 2_500), 2: (500, 70, 6_000)}  # games, dates, non-void picks
SELFTEST_LIMIT = {1: 0.015, 2: 0.035}
T60_MIN_COVERAGE = 0.70
WINDOW_FLOOR = date(
    2026, 11, 10
)  # no scored date can precede the draft; the freeze date sets the real start
ET = ZoneInfo("America/New_York")
PIN_COEF = 0.07
PIN_FORMULA = "p_one_minus_p"
PIN_MULT = 1.0
KALSHI_SERIES = tuple(g.KALSHI_SERIES)


class Refused(RuntimeError):
    """The scorer refuses to run (fee, rule text, window, season, read date)."""


# ----------------------------------------------------------------------------- refusals


def verify_fee(fee: Any) -> None:
    """Rule 2.2: verified, taker 0.07, formula P(1-P), M = 1.0 for every prop series in scope."""
    if not getattr(fee, "verified", False):
        raise Refused("Kalshi fee_model is not verified: true")
    if abs(float(fee.coefficient) - PIN_COEF) > 1e-12:
        raise Refused(f"fee coefficient {fee.coefficient} != pinned 0.07")
    if fee.formula != PIN_FORMULA:
        raise Refused(f"fee formula {fee.formula!r} != pinned p_one_minus_p")
    for s in KALSHI_SERIES:
        if abs(float(fee.multiplier(s)) - PIN_MULT) > 1e-12:
            raise Refused(f"fee multiplier for {s} is {fee.multiplier(s)}, pinned 1.0")


def rule_text_sha(text: str) -> str:
    """sha256 of everything above '=== RESULTS BELOW ===' (the awk in the rule's freeze line)."""
    import hashlib

    out = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        out.append(line)
    return hashlib.sha256("".join(out).encode()).hexdigest()


def verify_rule(text: str, rule_sha: str) -> None:
    if rule_text_sha(text) != rule_sha:
        raise Refused("rule text hash differs from --rule-sha")
    status = next((ln for ln in text.splitlines() if ln.startswith("Status:")), "")
    if "FROZEN" not in status:
        raise Refused("rule status line is not FROZEN")


def window_start_from_iso(committer_iso: str) -> date:
    """Rule 3: slate dates strictly after the ET calendar date of the freeze commit's committer time."""
    t = datetime.fromisoformat(committer_iso)
    if t.tzinfo is None:
        raise Refused("committer time without a timezone")
    return t.astimezone(ET).date() + timedelta(days=1)


def window_start_from_git(root: Path, commit: str) -> date:
    iso = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "show", "-s", "--format=%cI", commit],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return window_start_from_iso(iso)


def check_window(
    games: Sequence[tuple[str, int, date]], window_start: date, last_tip: date
) -> None:
    """Refuse (never filter) any game outside season 2026 or the scoring window."""
    if window_start < WINDOW_FLOOR:
        raise Refused(f"window start {window_start} precedes the floor {WINDOW_FLOOR}")
    for gid, season, gd in games:
        if season != SEASON:
            raise Refused(f"game {gid} has season {season}; only {SEASON} is scored")
        if gd < window_start:
            raise Refused(f"game {gid} on {gd} precedes the window start {window_start}")
        if gd > last_tip:
            raise Refused(f"game {gid} on {gd} is after the read's last tip date {last_tip}")


# ----------------------------------------------------------------------------- settlement


@dataclass(frozen=True)
class Settle:
    """Settlement inputs for one pick: the box-score grade (None = void: absent, minutes NULL or 0)
    and Kalshi's YES payoff for the ticker at L (1, 0 or the scalar fair value; None = not settled)."""

    book_over: bool | None
    kalshi_yes: float | None


def grade_book(minutes: float | None, value: float | None, point: float) -> bool | None:
    if minutes is None or minutes <= 0 or value is None:
        return None
    return float(value) > point


def kalshi_yes_payoff(result: str | None, scalar_value: float | None) -> float | None:
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    if result == "scalar":
        return scalar_value  # None when the raw page has no value: excluded, counted, flagged
    return None


def profit_for(side: str, venue: str, dec: float, st: Settle) -> tuple[float, float] | None:
    """(profit, stake) of 1 unit on ``side`` at ``venue`` priced at net decimal ``dec``.
    Books: void -> (0, 0). Kalshi: profit = payoff * dec - 1 (dec = 1/(P + fee)); stake 1.
    None = not settleable (Kalshi unsettled or a scalar with no value)."""
    if venue == g.KALSHI:
        if st.kalshi_yes is None:
            return None
        payoff = st.kalshi_yes if side == "over" else 1.0 - st.kalshi_yes
        return payoff * dec - 1.0, 1.0
    if st.book_over is None:
        return 0.0, 0.0
    win = st.book_over == (side == "over")
    return (dec - 1.0 if win else -1.0), 1.0


def ordered_venues(venues: dict[str, float]) -> list[tuple[str, float]]:
    """Venues best first, with the rule's tie-break (book before Kalshi, then alphabetical)."""
    return sorted(venues.items(), key=lambda kv: (-kv[1], kv[0] == g.KALSHI, kv[0]))


# ----------------------------------------------------------------------------- bootstrap


def _cluster_sums(values: np.ndarray, ids: np.ndarray, n: int) -> np.ndarray:
    return np.bincount(ids, values, n)


def boot_ratio_draws(
    nums: Sequence[np.ndarray],
    den: np.ndarray,
    ids: np.ndarray,
    rng: np.random.Generator,
    b: int,
    chunk: int = 500,
) -> list[np.ndarray]:
    """Percentile cluster bootstrap draws of sum(num)/sum(den) for each numerator, on SHARED
    resampled clusters (so paired differences use the same draws)."""
    n = int(ids.max()) + 1
    num_c = [_cluster_sums(x, ids, n) for x in nums]
    den_c = _cluster_sums(den, ids, n)
    out: list[list[np.ndarray]] = [[] for _ in nums]
    done = 0
    while done < b:
        m = min(chunk, b - done)
        idx = rng.integers(0, n, size=(m, n))
        d = den_c[idx].sum(axis=1)
        for j, nc in enumerate(num_c):
            with np.errstate(invalid="ignore", divide="ignore"):
                out[j].append(nc[idx].sum(axis=1) / d)
        done += m
    return [np.concatenate(o) for o in out]


def _codes(keys: Sequence[Any]) -> np.ndarray:
    _, inv = np.unique(np.asarray([str(k) for k in keys]), return_inverse=True)
    return inv.astype(np.int64)


def decision_ci(
    profit: np.ndarray,
    stake: np.ndarray,
    game: Sequence[Any],
    gdate: Sequence[Any],
    player: Sequence[Any],
    level: float,
    seed: int = SEED_CI,
    b: int = B_BOOT,
    paired: np.ndarray | None = None,
) -> dict[str, Any]:
    """Rule section 8: ROI = sum(profit)/sum(stake); percentile bootstrap of the ratio, B draws, one
    ``default_rng(seed)`` consumed in the order game, date, player; interval [q(level), q(1-level)]
    per clustering; the decision interval is the widest (ties: first in that order). Inputs must
    already be in the section 8 sort order. With ``paired`` (a second profit vector on the same
    stakes), the interval is for ROI(profit) - ROI(paired) on shared draws."""
    profit = np.asarray(profit, dtype=float)
    stake = np.asarray(stake, dtype=float)
    if stake.sum() <= 0:
        return {"n": int(len(profit)), "roi": None, "reason": "no stake"}
    roi = float(profit.sum() / stake.sum())
    if paired is not None:
        roi -= float(np.asarray(paired).sum() / stake.sum())
    rng = np.random.default_rng(seed)
    per: dict[str, Any] = {}
    widest: tuple[float, str] | None = None
    for name, keys in (("game", game), ("date", gdate), ("player", player)):
        ids = _codes(keys)
        nums = [profit] if paired is None else [profit, np.asarray(paired, dtype=float)]
        draws = boot_ratio_draws(nums, stake, ids, rng, b)
        dr = draws[0] if paired is None else draws[0] - draws[1]
        dr = dr[np.isfinite(dr)]
        lo, hi = float(np.quantile(dr, level)), float(np.quantile(dr, 1 - level))
        per[name] = {"lower": lo, "upper": hi, "clusters": int(ids.max()) + 1}
        if widest is None or hi - lo > widest[0]:
            widest = (hi - lo, name)
    assert widest is not None
    w = per[widest[1]]
    return {
        "n": int(len(profit)),
        "n_staked": int((stake > 0).sum()),
        "roi": roi,
        "level_one_sided": level,
        "by_clustering": per,
        "decision": {"clustering": widest[1], "lower": w["lower"], "upper": w["upper"]},
        "seed": seed,
        "b": b,
    }


# ----------------------------------------------------------------------------- words


def evaluate_word(
    read: int,
    t60_coverage: float,
    n_games: int,
    n_dates: int,
    n_nonvoid: int,
    selftests_ok: bool,
    roi: float | None,
    lower: float | None,
    upper: float | None,
    guards_ok: bool,
    rerun_used: bool = False,
) -> tuple[str, str]:
    """Section 8 order of evaluation; exactly one word per look, with the reason."""
    g_min, d_min, p_min = MINIMUM[read]
    if t60_coverage < T60_MIN_COVERAGE or n_games < g_min or n_dates < d_min or n_nonvoid < p_min:
        return (
            "INSUFFICIENT_SAMPLE" if read == 1 else "INCONCLUSIVE"
        ), "minimum or t60 coverage not met"
    if not selftests_ok:
        if read == 2 and rerun_used:
            return "INCONCLUSIVE", "self-test or settlement failed twice at Read 2"
        return "SELFTEST_FAILED", "self-test or settlement agreement failed"
    assert roi is not None and lower is not None and upper is not None
    if roi >= FLOOR and lower > 0 and guards_ok:
        return "PASS", "ROI >= floor, lower bound > 0, all guards and attacks hold"
    if upper < FLOOR:
        return "NO_POSITIVE_EV_FOUND", "upper bound below the floor"
    reason = (
        "nominal pass failed a guard or attack"
        if (roi >= FLOOR and lower > 0)
        else "neither bound decides"
    )
    return ("KEEP_SHADOWING" if read == 1 else "INCONCLUSIVE"), reason


# ----------------------------------------------------------------------------- arms and guards


@dataclass
class Scored:
    pick: g.Pick
    settle: Settle

    @property
    def iso_week(self) -> tuple[int, int]:
        y, w, _ = self.pick.game_date.isocalendar()
        return (y, w)


Price = Callable[[Scored], tuple[str, str, float] | None]  # -> (side, venue, decimal)


def best_price(x: Scored) -> tuple[str, str, float] | None:
    v, d = g.best_venue(x.pick.venues)
    return x.pick.side, v, d


def second_price(x: Scored) -> tuple[str, str, float] | None:
    o = ordered_venues(x.pick.venues)
    return (x.pick.side, o[1][0], o[1][1]) if len(o) >= 2 else None


def venue_price(venue: str) -> Price:
    def f(x: Scored) -> tuple[str, str, float] | None:
        d = x.pick.venues.get(venue)
        return None if d is None else (x.pick.side, venue, d)

    return f


def without_venue(venue: str) -> Price:
    def f(x: Scored) -> tuple[str, str, float] | None:
        rest = {v: d for v, d in x.pick.venues.items() if v != venue}
        if len(rest) < g.MIN_VENUES:  # rule 5.6 re-applied
            return None
        v, d = g.best_venue(rest)
        return x.pick.side, v, d

    return f


def flipped_price(x: Scored) -> tuple[str, str, float] | None:
    if len(x.pick.venues_other) < 1:
        return None
    v, d = g.best_venue(x.pick.venues_other)
    return ("under" if x.pick.side == "over" else "over"), v, d


@dataclass
class Arm:
    profit: np.ndarray
    stake: np.ndarray
    rows: list[Scored]
    n_unsettleable: int

    @property
    def roi(self) -> float | None:
        s = float(self.stake.sum())
        return float(self.profit.sum() / s) if s > 0 else None


def build_arm(rows: Sequence[Scored], price: Price) -> Arm:
    prof, stk, kept = [], [], []
    bad = 0
    for x in rows:
        pr = price(x)
        if pr is None:
            continue
        r = profit_for(pr[0], pr[1], pr[2], x.settle)
        if r is None:
            bad += 1
            continue
        prof.append(r[0])
        stk.append(r[1])
        kept.append(x)
    return Arm(np.asarray(prof, float), np.asarray(stk, float), kept, bad)


def arm_ci(
    arm: Arm, level: float, b: int = B_BOOT, paired: np.ndarray | None = None
) -> dict[str, Any]:
    if not arm.rows:
        return {"n": 0, "roi": None}
    return decision_ci(
        arm.profit,
        arm.stake,
        [x.pick.game_id for x in arm.rows],
        [x.pick.game_date for x in arm.rows],
        [x.pick.player_id for x in arm.rows],
        level,
        b=b,
        paired=paired,
    )


def shuffle_guard(
    rows: Sequence[Scored], observed: float, n: int = N_SHUFFLE, seed: int = SEED_SHUFFLE
) -> dict[str, Any]:
    """10.2: flip each pick's side with probability 0.5, one draw per pick in sort order; PASS needs
    the observed ROI above the 95th percentile of the shuffled ROIs."""
    rng = np.random.default_rng(seed)
    keep = build_arm(rows, best_price)
    flip = build_arm(rows, flipped_price)
    if len(keep.rows) != len(rows) or len(flip.rows) != len(rows):
        return {"ok": False, "reason": "a pick lacks a settleable price on one side"}
    sims = np.empty(n)
    for i in range(n):
        f = rng.random(len(rows)) < 0.5
        p = np.where(f, flip.profit, keep.profit)
        s = np.where(f, flip.stake, keep.stake)
        sims[i] = p.sum() / s.sum() if s.sum() > 0 else np.nan
    q95 = float(np.nanquantile(sims, 0.95))
    return {"ok": bool(observed > q95), "q95": q95, "n": n, "seed": seed}


def planted_outcome_roi(rows: Sequence[Scored]) -> float | None:
    """10.4: the settled outcome written into p_cal, i.e. always the winning side, at the best price
    on that side. Voids and unsettleable picks are skipped."""
    prof, stk = [], []
    for x in rows:
        over = x.settle.book_over
        if over is None and x.settle.kalshi_yes is not None:
            over = x.settle.kalshi_yes > 0.5
        if over is None:
            continue
        win_side = "over" if over else "under"
        price = best_price(x) if win_side == x.pick.side else flipped_price(x)
        if price is None:
            continue
        r = profit_for(price[0], price[1], price[2], x.settle)
        if r is None or r[1] == 0:
            continue
        prof.append(r[0])
        stk.append(r[1])
    return float(sum(prof) / sum(stk)) if stk else None


def _roi(rows: Sequence[Scored], price: Price = best_price) -> float | None:
    return build_arm(rows, price).roi


def guards(rows: Sequence[Scored], exec_ok: dict[int, bool]) -> dict[str, Any]:
    """Section 10.3, 10.5-10.7, 10.10 (point estimates). ``exec_ok`` maps a row index to whether the
    best venue still offered an equal or better price at its next snapshot (10.3(c))."""
    out: dict[str, Any] = {}
    out["second_best"] = _roi(rows, second_price)
    gap_rows = []
    for x in rows:
        o = ordered_venues(x.pick.venues)
        if o[0][1] - o[1][1] <= 0.10:
            gap_rows.append(x)
    out["gap_le_0.10"] = _roi(gap_rows)
    ex_rows = [x for i, x in enumerate(rows) if exec_ok.get(i, False)]
    out["executable"] = _roi(ex_rows)
    out["executable_fail_share"] = round(1 - len(ex_rows) / len(rows), 4) if rows else None
    by_week: dict[tuple[int, int], list[Scored]] = defaultdict(list)
    for x in rows:
        by_week[x.iso_week].append(x)
    wk = {w: _roi(v) for w, v in by_week.items()}
    best_w = max((w for w in wk if wk[w] is not None), key=lambda w: (wk[w], w), default=None)
    out["without_best_week"] = _roi([x for x in rows if x.iso_week != best_w])
    out["best_week"] = None if best_w is None else f"{best_w[0]}-W{best_w[1]:02d}"
    out["venues_ge3"] = _roi([x for x in rows if len(x.pick.venues) >= 3])
    out["venue_loo"] = {v: _roi(rows, without_venue(v)) for v in g.U1}
    out["stat_loo"] = {s: _roi([x for x in rows if x.pick.stat != s]) for s in g.STATS}
    cnt = Counter(x.pick.player_id for x in rows)
    top5 = {p for p, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:5]}
    out["without_top5_players"] = _roi([x for x in rows if x.pick.player_id not in top5])
    n = len(rows)
    gshare = Counter(x.pick.game_id for x in rows)
    dshare = Counter(x.pick.game_date for x in rows)
    out["concentration"] = {
        "games_over_2pct": {
            k: _roi([x for x in rows if x.pick.game_id != k])
            for k, c in gshare.items()
            if c / n > 0.02
        },
        "dates_over_6pct": {
            str(k): _roi([x for x in rows if x.pick.game_date != k])
            for k, c in dshare.items()
            if c / n > 0.06
        },
    }

    def ge0(v: float | None) -> bool:
        return v is not None and v >= 0

    def gt0(v: float | None) -> bool:
        return v is not None and v > 0

    out["ok"] = bool(
        ge0(out["second_best"])
        and ge0(out["gap_le_0.10"])
        and ge0(out["executable"])
        and ge0(out["without_best_week"])
        and ge0(out["venues_ge3"])
        and all(ge0(v) for v in out["venue_loo"].values())
        and all(gt0(v) for v in out["stat_loo"].values())
        and gt0(out["without_top5_players"])
    )
    return out


def settlement_agreement(rows: Sequence[Scored]) -> dict[str, Any]:
    """10.8: Kalshi yes/no vs the box-score grade on Kalshi-priced picks."""
    both = [x for x in rows if x.settle.book_over is not None and x.settle.kalshi_yes in (0.0, 1.0)]
    dis = sum((x.settle.kalshi_yes == 1.0) != x.settle.book_over for x in both)
    share = dis / len(both) if both else 0.0
    return {"n": len(both), "disagree": dis, "share": share, "ok": share <= 0.01}


# ----------------------------------------------------------------------------- synthetic worlds


def synthetic_set(
    rng: np.random.Generator,
    base_over: np.ndarray,
    side_over: np.ndarray,
    dec: np.ndarray,
    player: np.ndarray,
    game_player: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Rule 10.9 DGP: player shift N(0, 0.03) (clipped [0.01, 0.99]); latent z = sqrt(0.3) u_(game,
    player) + sqrt(0.7) e; over iff Phi(z) < p. Returns per-pick profit at ``dec`` and the set's
    known true ROI (expectation under the shifted probabilities). No voids are simulated."""
    from scipy.special import ndtr

    n_p, n_gp = int(player.max()) + 1, int(game_player.max()) + 1
    po = np.clip(base_over + rng.normal(0.0, 0.03, n_p)[player], 0.01, 0.99)
    z = math.sqrt(0.3) * rng.standard_normal(n_gp)[game_player] + math.sqrt(
        0.7
    ) * rng.standard_normal(len(po))
    win = (ndtr(z) < po) == side_over
    profit = np.where(win, dec - 1.0, -1.0)
    truth = float(np.mean(np.where(side_over, po, 1 - po) * dec - 1.0))
    return profit, truth


def self_tests(
    picks: Sequence[g.Pick],
    read: int,
    k: int = K_SELFTEST,
    b: int = B_BOOT,
    progress: bool = True,
) -> dict[str, Any]:
    """10.9 (a) calibrated null from p_cons, rng 2027; (b) planted edge p_cons + 0.5 (p_cal - p_cons),
    rng 2029. The decision CI is ``decision_ci`` exactly as at the look (seed 2026, widest of three)."""
    level, limit = LEVEL[read], SELFTEST_LIMIT[read]
    side_over = np.array([x.side == "over" for x in picks])
    dec = np.array([g.best_venue(x.venues)[1] for x in picks])
    games = [x.game_id for x in picks]
    dates = [x.game_date for x in picks]
    players = [x.player_id for x in picks]
    pi = _codes(players)
    gpi = _codes([f"{a}|{b_}" for a, b_ in zip(games, players, strict=True)])
    stake = np.ones(len(picks))
    res: dict[str, Any] = {}
    for name, seed, base in (
        ("a_null", SEED_SELFTEST_A, np.array([x.p_cons for x in picks])),
        (
            "b_planted",
            SEED_SELFTEST_B,
            np.array([x.p_cons + 0.5 * (x.p_cal - x.p_cons) for x in picks]),
        ),
    ):
        rng = np.random.default_rng(seed)
        above = below = covered = 0
        errs = []
        for i in range(k):
            profit, truth = synthetic_set(rng, base, side_over, dec, pi, gpi)
            ci = decision_ci(profit, stake, games, dates, players, level, b=b)
            lo, hi = ci["decision"]["lower"], ci["decision"]["upper"]
            above += lo > truth
            below += hi < truth
            covered += lo <= truth <= hi
            errs.append(ci["roi"] - truth)
            if progress and (i + 1) % 50 == 0:
                print(f"[selftest {name}] {i + 1}/{k}", flush=True)
        if name == "a_null":
            ok = above / k <= limit and below / k <= limit
            res[name] = {
                "k": k,
                "lower_above_truth": above / k,
                "upper_below_truth": below / k,
                "limit": limit,
                "ok": ok,
            }
        else:
            ok = abs(float(np.mean(errs))) <= 0.005 and covered / k >= 0.90
            res[name] = {
                "k": k,
                "mean_error": float(np.mean(errs)),
                "coverage": covered / k,
                "ok": ok,
            }
    res["ok"] = bool(res["a_null"]["ok"] and res["b_planted"]["ok"])
    return res


# ----------------------------------------------------------------------------- loading for a look


def load_settlement(
    nba: duckdb.DuckDBPyConnection,
    kal: duckdb.DuckDBPyConnection,
    root: Path,
    picks: Sequence[g.Pick],
) -> tuple[dict[int, Settle], set[str], dict[str, int]]:
    """Settlement per pick index, the set of games with an unsettled Kalshi pick (excluded whole),
    and counts. Box: season 2026 only. Kalshi: its own result; scalar values from raw settled pages."""
    from research.market_forward.data import load_raw

    gids = sorted({x.game_id for x in picks})
    gm = ",".join("?" for _ in gids)
    box = {
        (r[0], int(r[1])): r[2:]
        for r in nba.execute(
            f"""SELECT s.game_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m
                FROM player_game_stats s JOIN games gg ON gg.game_id = s.game_id
                WHERE gg.season = ? AND s.game_id IN ({gm})""",  # noqa: S608
            [SEASON, *gids],
        ).fetchall()
    }
    col = {"pts": 1, "reb": 2, "ast": 3, "fg3m": 4}
    tickers = sorted({x.kalshi_ticker for x in picks if x.kalshi_ticker})
    res: dict[str, str | None] = {}
    if tickers:
        tm = ",".join("?" for _ in tickers)
        res = {
            t: r
            for t, r in kal.execute(
                f"SELECT ticker, result FROM kalshi_markets WHERE ticker IN ({tm})", tickers
            ).fetchall()
        }  # noqa: S608
    days = sorted({x.game_date + timedelta(days=dd) for x in picks for dd in range(0, 3)})
    raw = load_raw(root / "data/kalshi/raw", days, KALSHI_SERIES)
    out: dict[int, Settle] = {}
    unsettled: set[str] = set()
    cnt: Counter[str] = Counter()
    for i, x in enumerate(picks):
        b = box.get((x.game_id, x.player_id))
        over = None if b is None else grade_book(b[0], b[col[x.stat]], x.point)
        ky: float | None = None
        if x.kalshi_ticker:
            r = res.get(x.kalshi_ticker)
            sv = raw.scalar_micro.get(x.kalshi_ticker)
            ky = kalshi_yes_payoff(r, None if sv is None else sv / 1_000_000)
            if r not in ("yes", "no", "scalar"):
                unsettled.add(x.game_id)
                cnt["kalshi_unsettled"] += 1
            elif r == "scalar" and ky is None:
                cnt["kalshi_scalar_missing_value"] += 1
            elif r == "scalar":
                cnt["kalshi_scalar"] += 1
        if over is None:
            cnt["book_void"] += 1
        out[i] = Settle(over, ky)
    return out, unsettled, dict(cnt)


def load_next_quotes(
    odds: duckdb.DuckDBPyConnection,
    kal: duckdb.DuckDBPyConnection,
    picks: Sequence[g.Pick],
    entries: dict[str, tuple[datetime, datetime]],
) -> dict[int, bool]:
    """10.3(c): did the best venue still quote the pick side at L at an equal or better price at its
    next snapshot before tip (books: the game's t45; Kalshi: the next live row after E)?"""
    ok: dict[int, bool] = {}
    gids = sorted({x.game_id for x in picks})
    # t45 rows are after E by construction (the next snapshot), so they are read here directly
    gm = ",".join("?" for _ in gids)
    mm = ",".join("?" for _ in g.MARKET_STAT)
    rows = odds.execute(
        f"""SELECT game_id, player_id, market, book, side, point, price_american, received_at, last_update, tipoff
            FROM live_book_odds WHERE snapshot_kind = 't45' AND game_id IN ({gm}) AND market IN ({mm})
             AND player_id IS NOT NULL AND received_at < tipoff""",  # noqa: S608
        [*gids, *g.MARKET_STAT],
    ).fetchall()
    nxt: dict[tuple[str, int, str], list[tuple[Any, ...]]] = defaultdict(list)
    for r in rows:
        nxt[(r[0], int(r[1]), g.MARKET_STAT[r[2]])].append(r[:9])
    kn: dict[str, tuple[float, float]] = {}
    for i, x in enumerate(picks):
        v, d = g.best_venue(x.venues)
        e, tip = entries[x.game_id]
        if v == g.KALSHI and x.kalshi_ticker:
            if x.kalshi_ticker not in kn:
                kr = kal.execute(
                    """SELECT min(yes_bid), max(yes_ask) FROM kalshi_prices
                       WHERE ticker = ? AND source = 'live' AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
                         AND ts = (SELECT min(ts) FROM kalshi_prices WHERE ticker = ? AND source = 'live'
                                   AND ts > ? AND ts < ? AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL)""",
                    [x.kalshi_ticker, x.kalshi_ticker, e, tip],
                ).fetchone()  # worse price for the bettor on a tie (as 4.2 / 4.3)
                if kr is not None and kr[0] is not None:
                    kn[x.kalshi_ticker] = (float(kr[0]), float(kr[1]))
            q = kn.get(x.kalshi_ticker)
            ok[i] = q is not None and g.kalshi_decimal(x.side, q[0], q[1]) >= d - 1e-12
        else:
            chosen = g.choose_quotes(nxt.get((x.game_id, x.player_id, x.stat), [])).get(v, {})
            r2 = chosen.get((x.side, x.point))
            ok[i] = r2 is not None and g.american_to_decimal(int(r2[6])) >= d - 1e-12
    return ok


def planted_future(
    odds: duckdb.DuckDBPyConnection,
    preds: Sequence[tuple[Any, ...]],
    entries: dict[str, tuple[datetime, datetime]],
    gdate: dict[str, date],
    since: date,
    until: date,
) -> dict[str, Any]:
    """10.4 (first half): every model row re-stamped E + 1 min and every price taken from the
    ``close`` snapshot must be refused by the same loaders the look uses (zero rows, zero picks)."""
    mem = duckdb.connect(":memory:")
    mem.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT)")
    mem.execute(
        "CREATE TABLE forward_predictions (game_id VARCHAR, model_name VARCHAR, target VARCHAR, player_id INT, made_at TIMESTAMP, prediction JSON)"
    )
    for gk, gd in gdate.items():
        mem.execute("INSERT INTO games VALUES (?, ?, ?)", [gk, gd, SEASON])
    for gid, stat, pid, _m, js in preds:
        mem.execute(
            "INSERT INTO forward_predictions VALUES (?, ?, ?, ?, ?, ?)",
            [gid, g.PROD, stat, pid, entries[gid][0] + timedelta(minutes=1), js],
        )
    rows, _, _ = g.load_predictions(mem, entries, since, until)
    mem.close()
    close_rows, refused = g.load_book_quotes(odds, since, until, entries, snapshot="close")
    n_close = sum(len(v) for v in close_rows.values())
    return {
        "model_rows_admitted": len(rows),
        "close_rows_admitted": n_close,
        "close_rows_refused": refused,
        "ok": len(rows) == 0 and n_close == 0,
    }


# ----------------------------------------------------------------------------- the look


def score(
    root: Path,
    read: int,
    freeze_commit: str,
    rule_sha: str,
    today: date | None = None,
    b: int = B_BOOT,
    k: int = K_SELFTEST,
) -> dict[str, Any]:
    from nba.parlay.config import load_config

    today = today or date.today()
    if today < READ_DATE[read]:
        raise Refused(f"Read {read} is on {READ_DATE[read]}; today is {today}")
    verify_fee(load_config(root / "configs/parlay.yaml").fee)
    verify_rule((root / "docs/prereg/BEST_PRICE_FORWARD.md").read_text(), rule_sha)
    start = window_start_from_git(root, freeze_commit)
    last = LAST_TIP[read]
    level = LEVEL[read]
    odds = g.connect_ro(root / "data/markets/market_asof.duckdb")
    entries = g.load_entry_stamps(odds, start, last)
    books, refused = g.load_book_quotes(odds, start, last, entries)
    nba = g.connect_ro(root / "nba.duckdb")
    preds, gdate, n_sched = g.load_predictions(nba, entries, start, last)
    seasons = nba.execute(
        "SELECT game_id, season, game_date FROM games WHERE game_id IN (SELECT unnest(?))",
        [sorted(gdate)],
    ).fetchall()
    check_window([(r[0], int(r[1]), r[2]) for r in seasons], start, last)
    entries = {gk: v for gk, v in entries.items() if gk in gdate}
    entry_by_pd = {(int(pid), gdate[gid]): (gid, entries[gid][0]) for gid, _s, pid, _m, _j in preds}
    kal = g.connect_ro(root / "data/kalshi/kalshi.duckdb")
    kq = g.load_kalshi(kal, entry_by_pd)
    cmap = lc.load_map()
    if cmap.meta["map_sha256"] != g.MAP_SHA:
        raise Refused("served LINE_CALIBRATION map differs from the frozen one")
    st = g.GateState()
    cal_picks, raw_picks = g.build_picks(preds, gdate, entries, books, kq, cmap, st)
    cal_picks, raw_picks = g.sort_picks(cal_picks), g.sort_picks(raw_picks)
    check_window([(x.game_id, SEASON, x.game_date) for x in cal_picks + raw_picks], start, last)
    pf = planted_future(odds, preds, entries, gdate, start, last)
    settle, unsettled, scnt = load_settlement(nba, kal, root, cal_picks)
    exec_ok_all = load_next_quotes(odds, kal, cal_picks, entries)
    later = load_kalshi_after(kal, cal_picks, entries)
    raw_settle, raw_unsettled, _ = load_settlement(nba, kal, root, raw_picks)
    raw_rows = [
        Scored(x, raw_settle[i]) for i, x in enumerate(raw_picks) if x.game_id not in raw_unsettled
    ]
    nba.close()
    odds.close()
    kal.close()
    keep_idx = [i for i, x in enumerate(cal_picks) if x.game_id not in unsettled]
    rows = [Scored(cal_picks[i], settle[i]) for i in keep_idx]
    exec_ok = {j: exec_ok_all[i] for j, i in enumerate(keep_idx)}
    prim = build_arm(rows, best_price)
    ci = arm_ci(prim, level, b=b)
    n_games = len({x.pick.game_id for x in prim.rows})
    n_dates = len({x.pick.game_date for x in prim.rows})
    n_nonvoid = int((prim.stake > 0).sum())
    t60_cov = len(entries) / n_sched if n_sched else 0.0
    sa = settlement_agreement(rows)
    stests = self_tests([x.pick for x in prim.rows], read, k=k, b=b)
    gd = guards(rows, exec_ok)
    sh = shuffle_guard(rows, prim.roi if prim.roi is not None else -1.0)
    po = planted_outcome_roi(rows)
    attacks_ok = bool(gd["ok"] and sh["ok"] and pf["ok"] and po is not None and po >= 0.30)
    dec = ci.get("decision", {})
    word, why = evaluate_word(
        read,
        t60_cov,
        n_games,
        n_dates,
        n_nonvoid,
        bool(stests["ok"] and sa["ok"]),
        prim.roi,
        dec.get("lower"),
        dec.get("upper"),
        attacks_ok,
    )
    # descriptive arms (no word; S2-S3 enter BH only at Read 2, done by the reader from the printed one-sided p)
    dk_rows = [x for x in rows if "draftkings" in x.pick.venues]
    dk_best, dk_dk = build_arm(dk_rows, best_price), build_arm(dk_rows, venue_price("draftkings"))
    s1 = (
        arm_ci(dk_best, level, b=b, paired=dk_dk.profit)
        if len(dk_best.rows) == len(dk_dk.rows)
        else {"reason": "unaligned"}
    )
    k_rows = [x for x in rows if g.KALSHI in x.pick.venues]
    over_rows = [x for x in rows if x.pick.side == "over"]
    out = {
        "rule": RULE,
        "rule_sha": rule_sha,
        "read": read,
        "window": {"start": str(start), "last_tip": str(last)},
        "word": word,
        "why": why,
        "primary": {
            "ci": ci,
            "n_games": n_games,
            "n_dates": n_dates,
            "n_nonvoid": n_nonvoid,
            "n_void": int((prim.stake == 0).sum()),
            "unsettleable": prim.n_unsettleable,
        },
        "t60_coverage": t60_cov,
        "post_entry_quotes_refused": refused,
        "unsettled_games_excluded": sorted(unsettled),
        "settlement_counts": scnt,
        "settlement_agreement": sa,
        "self_tests": stests,
        "guards": gd,
        "shuffle": sh,
        "planted_future": pf,
        "planted_outcome_roi": po,
        "S1_best_minus_draftkings": s1,
        "S2_kalshi_taker": arm_ci(build_arm(k_rows, venue_price(g.KALSHI)), level, b=b),
        "S3_kalshi_maker": {
            "trade_through_1c": maker_arm(rows, later, False, level, b),
            "at_equality": maker_arm(rows, later, True, level, b),
        },
        "S5_raw": arm_ci(build_arm(raw_rows, best_price), level, b=b),
        "S6_outside_u1": {
            "status": "not computed: no verified fee objects (rule 2.3); descriptive only"
        },
        "D_OVER": {
            "best": arm_ci(build_arm(over_rows, best_price), level, b=b),
            "kalshi": arm_ci(
                build_arm(
                    [x for x in over_rows if g.KALSHI in x.pick.venues], venue_price(g.KALSHI)
                ),
                level,
                b=b,
            ),
        },
        "top5_profit_share": top5_profit_share(prim),
    }
    return out


def load_kalshi_after(
    kal: duckdb.DuckDBPyConnection,
    picks: Sequence[g.Pick],
    entries: dict[str, tuple[datetime, datetime]],
) -> dict[str, list[tuple[float, float]]]:
    """Live Kalshi rows with E < ts < tip per ticker (paper maker fills, rule 6 S3), in ts order."""
    out: dict[str, list[tuple[float, float]]] = {}
    for x in picks:
        if x.kalshi_ticker is None or x.kalshi_maker is None or x.kalshi_ticker in out:
            continue
        e, tip = entries[x.game_id]
        out[x.kalshi_ticker] = [
            (float(r[0]), float(r[1]))
            for r in kal.execute(
                """SELECT yes_bid, yes_ask FROM kalshi_prices WHERE ticker = ? AND source = 'live'
                   AND ts > ? AND ts < ? AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL ORDER BY ts""",
                [x.kalshi_ticker, e, tip],
            ).fetchall()
        ]
    return out


def maker_filled(
    side: str, order_price: float, later: Sequence[tuple[float, float]], at_equality: bool
) -> bool:
    """Rule 6 S3 fill assumption: a later pre-tip snapshot shows the pick side's ask at or below the
    order price minus 1c (trade-through; the sensitivity uses the order price itself)."""
    bar_c = round(100 * order_price) - (0 if at_equality else 1)
    for yb, ya in later:
        side_ask_c = round(100 * ya) if side == "over" else 100 - round(100 * yb)
        if side_ask_c <= bar_c:
            return True
    return False


def maker_arm(
    rows: Sequence[Scored],
    later: dict[str, list[tuple[float, float]]],
    at_equality: bool,
    level: float,
    b: int,
) -> dict[str, Any]:
    prof, stk, kept = [], [], []
    tried = 0
    for x in rows:
        pm, tk = x.pick.kalshi_maker, x.pick.kalshi_ticker
        if pm is None or tk is None:
            continue
        tried += 1
        if not maker_filled(x.pick.side, pm, later.get(tk, []), at_equality):
            continue
        if x.settle.kalshi_yes is None:
            continue
        cost = pm + g.kalshi_fee(pm, g.MAKER_FEE_COEF)
        payoff = x.settle.kalshi_yes if x.pick.side == "over" else 1.0 - x.settle.kalshi_yes
        prof.append((payoff - cost) / cost)
        stk.append(1.0)
        kept.append(x)
    arm = Arm(np.asarray(prof, float), np.asarray(stk, float), kept, 0)
    return {
        "orders": tried,
        "filled": len(kept),
        "fill_rate": (len(kept) / tried) if tried else None,
        "ci": arm_ci(arm, level, b=b),
    }


def top5_profit_share(arm: Arm) -> float | None:
    tot = float(arm.profit.sum())
    if not arm.rows or tot == 0:
        return None
    byp: dict[int, float] = defaultdict(float)
    for x, p in zip(arm.rows, arm.profit, strict=True):
        byp[x.pick.player_id] += float(p)
    top = sorted(byp.values(), key=abs, reverse=True)[:5]
    return float(sum(top) / tot)


# ----------------------------------------------------------------------------- CLI


def reproduce_gate(root: Path, until: date, window_start: date, out: Path) -> str:
    """Label-free: the gate through the scorer's import path, written exactly as the gate writes it."""
    import hashlib

    res = g.run(root, g.SINCE, until, window_start)
    text = json.dumps(res, indent=2, default=str, sort_keys=True) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("gate")
    a1.add_argument("--data-root", type=Path, default=Path("/Users/devin/Downloads/nba-prediction"))
    a1.add_argument("--until", type=date.fromisoformat, required=True)
    a1.add_argument("--window-start", type=date.fromisoformat, default=date(2026, 11, 11))
    a1.add_argument("--out", type=Path, required=True)
    a2 = sub.add_parser("score")
    a2.add_argument("--data-root", type=Path, default=Path("/Users/devin/Downloads/nba-prediction"))
    a2.add_argument("--read", type=int, choices=(1, 2), required=True)
    a2.add_argument("--freeze-commit", required=True)
    a2.add_argument("--rule-sha", required=True)
    a2.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        print(
            f"[eval] gate sha256 {reproduce_gate(a.data_root, a.until, a.window_start, a.out)}",
            flush=True,
        )
        return 0
    res = score(a.data_root, a.read, a.freeze_commit, a.rule_sha)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, default=str, sort_keys=True) + "\n")
    print(f"[eval] Read {a.read}: {res['word']} ({res['why']})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
