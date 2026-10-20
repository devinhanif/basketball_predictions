# ruff: noqa: E501
"""Live forward scorecard: a diagnostic report on settled ``season=2026`` forward rows.

Not a model, not a claim test, and not the pre-registered comparison in
``docs/FORWARD_PREREG_2026_27.md``. Every number carries n and a 95% game-clustered bootstrap CI
(cluster = ``game_id``, 1000 resamples, fixed seed). A claim drawn from it needs its own rule.

Read-only: ``nba.duckdb`` and ``market_asof.duckdb`` are opened with ``read_only=True`` (retry on a
writer's lock). Every score / box / market query is filtered to ``game_date >= 2026-10-01``; the
2025-26 season (``season=2025``, the frozen holdout) is never read for scores, stats or minutes. The one
exception is the new-team flag, which needs each player's team in his last game before this season:
that lookup reads ``team_id`` and ``game_date`` only (no stat, no minutes, no outcome), is isolated in
``prior_teams`` and can be switched off with ``--no-prior-team``.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.parlay.top_source import p_over

SEASON_START = date(2026, 10, 1)  # nothing before this date is read (scores, boxes, lines)
DEFAULT_SINCE = date(2026, 10, 20)
PROD = "props_context_residual"
STATS = ("pts", "reb", "ast", "fg3m")
WIN_MODELS = ("rung0_injury_elo", "rung0_mov_elo")
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
N_BOOT = 1000
SEED = 20261020
MIN_N = 8
NAN = float("nan")
NOLINE = "no line"

NBA_DB = Path("/Users/devin/Downloads/nba-prediction/nba.duckdb")
MARKET_DB = Path("/Users/devin/Downloads/nba-prediction/data/markets/market_asof.duckdb")


# ------------------------------------------------------------------ small pure helpers


def american_to_prob(price: float) -> float:
    """Raw implied probability of an American price (vig included)."""
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def devig(p_side: float, p_other: float) -> float:
    """Two-way proportional de-vig: p_side / (p_over + p_under)."""
    return p_side / (p_side + p_other)


def lean_side(mean: float, line: float | None) -> str:
    if line is None:
        return NOLINE
    return "over" if mean > line else "under"


def p_side_from_over(p_o: float, side: str) -> float:
    return p_o if side == "over" else 1.0 - p_o


def bucket_minutes(m: float | None) -> str:
    if m is None:
        return "no proj minutes"
    return "<15" if m < 15 else "15-25" if m < 25 else "25-32" if m < 32 else "32+"


def bucket_pplay(p: float | None) -> str:
    if p is None:
        return "no p_play"
    return "<0.75" if p < 0.75 else "0.75-0.9" if p < 0.9 else "0.9+"


def bucket_prior(n: int) -> str:
    return "0" if n == 0 else "1-5" if n <= 5 else "6+"


def team_flag(prior: int | None, tonight: int | None) -> str:
    if prior is None or tonight is None:
        return "no history"
    return "new team" if prior != tonight else "same team"


def decompose(y: float, mean: float, proj_min: float, act_min: float) -> dict[str, float]:
    """y - mean = miss_minutes + miss_rate exactly (cross is 0 in this form).

    miss_minutes = (act_min - proj_min) * proj_rate ; miss_rate = (act_rate - proj_rate) * act_min,
    rate = stat per minute, proj_rate = mean / proj_min. In squared error the cross term is
    2 * miss_minutes * miss_rate. ``cross`` below is the residual of the identity (0 up to rounding).
    """
    proj_rate = mean / proj_min
    act_rate = y / act_min
    mm = (act_min - proj_min) * proj_rate
    mr = (act_rate - proj_rate) * act_min
    return {"miss_minutes": mm, "miss_rate": mr, "cross": (y - mean) - mm - mr}


def cluster_ci(
    gids: Sequence[str],
    cols: np.ndarray,
    fn: Callable[[np.ndarray], Any],
    *,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> tuple[float, float, float]:
    """(point, lo, hi): ``fn`` of the column sums, bootstrapped over whole games (clusters).

    ``fn`` maps sums with trailing axis k to a value (works on a (B, k) array). With a single
    game the CI is undefined and returned as NaN.
    """
    cols = np.asarray(cols, dtype=float).reshape(len(gids), -1)
    uniq = sorted(set(gids))
    pos = {g: i for i, g in enumerate(uniq)}
    per_game = np.zeros((len(uniq), cols.shape[1]))
    for g, row in zip(gids, cols, strict=True):
        per_game[pos[g]] += row
    with np.errstate(divide="ignore", invalid="ignore"):
        point = float(fn(per_game.sum(axis=0)))
        if len(uniq) < 2:
            return point, NAN, NAN
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
        draws = np.asarray(fn(per_game[idx].sum(axis=1)), dtype=float)
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return point, NAN, NAN
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


def mean_fn(s: np.ndarray) -> Any:
    return s[..., 0] / s[..., 1]


def ratio_fn(i: int, j: int) -> Callable[[np.ndarray], Any]:
    def f(s: np.ndarray) -> Any:
        return s[..., i] / s[..., j]

    return f


def _num(x: float, d: int) -> str:
    return "n/a" if x != x else f"{x:.{d}f}"


def fmt_cell(res: dict[str, Any] | None, d: int = 3) -> str:
    """'value [lo, hi] n=..' ; 'too few' under MIN_N rows; CI 'n/a' when one game only."""
    if res is None:
        return "-"
    ci = "[n/a]" if res["lo"] != res["lo"] else f"[{_num(res['lo'], d)}, {_num(res['hi'], d)}]"
    txt = f"{_num(res['value'], d)} {ci} n={res['n']} ({res['games']}g)"
    return txt + (" too few" if res["n"] < MIN_N else "")


def cell(
    rows: list[dict[str, Any]], colfn: Callable[[dict[str, Any]], list[float]], fn: Any
) -> Any:
    if not rows:
        return None
    gids = [r["game_id"] for r in rows]
    cols = np.array([colfn(r) for r in rows], dtype=float)
    v, lo, hi = cluster_ci(gids, cols, fn)
    return {"value": v, "lo": lo, "hi": hi, "n": len(rows), "games": len(set(gids))}


# ------------------------------------------------------------------ data access


def connect_ro(path: Path, *, wait_s: float = 600.0, retry_s: float = 30.0) -> Any:
    """Open read-only; a writer's lock is retried every ``retry_s`` up to ``wait_s``."""
    t0 = time.monotonic()
    while True:
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:
            if time.monotonic() - t0 + retry_s > wait_s:
                raise
            print(f"{path.name}: locked ({str(exc)[:80]}); retry in {retry_s:.0f}s")
            time.sleep(retry_s)


def _jl(v: Any) -> dict[str, Any]:
    out = json.loads(v) if isinstance(v, str) else v
    return out if isinstance(out, dict) else {}


def _fnum(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def load_scored(con: Any, since: date, until: date) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scored production prop rows joined to the exact forecast row that was scored."""
    marks = ",".join("?" for _ in STATS)
    raw = con.execute(
        f"""SELECT s.game_id, s.game_date, s.target, s.player_id, s.y, s.crps, s.made_at,
                   p.run_id, CAST(p.prediction AS VARCHAR)
            FROM forward_scores s
            LEFT JOIN forward_predictions p
              ON p.game_id = s.game_id AND p.model_name = s.model_name AND p.target = s.target
             AND p.player_id = s.player_id AND p.made_at = s.made_at
            WHERE s.status = 'scored' AND s.model_name = ? AND s.target IN ({marks})
              AND s.game_date >= ? AND s.game_date <= ? AND s.game_date >= ?
            ORDER BY s.game_id, s.target, s.player_id, p.run_id""",  # noqa: S608
        [PROD, *STATS, since, until, SEASON_START],
    ).fetchall()
    latest: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    for r in raw:  # ordered by run_id: the last wins (latest row per key)
        latest[(r[0], r[2], int(r[3]))] = r
    rows: list[dict[str, Any]] = []
    skipped = {"no_prediction_row": 0, "no_mean": 0}
    for (gid, stat, pid), r in sorted(latest.items()):
        if r[8] is None:
            skipped["no_prediction_row"] += 1
            continue
        pj = _jl(r[8])
        mean = _fnum(pj.get("mean"))
        if mean is None or r[4] is None:
            skipped["no_mean"] += 1
            continue
        rows.append({
            "game_id": gid, "game_date": r[1], "stat": stat, "player_id": pid,
            "y": float(r[4]), "crps": _fnum(r[5]), "mean": mean,
            "proj_min": _fnum(pj.get("proj_minutes")), "p_play": _fnum(pj.get("p_play")),
            "starter_rate": _fnum(pj.get("starter_rate")), "pred": pj,
        })  # fmt: skip
    return rows, skipped


def load_win(con: Any, since: date, until: date) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in WIN_MODELS)
    raw = con.execute(
        f"""SELECT game_id, model_name, brier, log_loss FROM forward_scores
            WHERE status = 'scored' AND target = 'win_prob_home' AND model_name IN ({marks})
              AND game_date >= ? AND game_date <= ? AND game_date >= ?
            ORDER BY game_id, model_name, made_at""",  # noqa: S608
        [*WIN_MODELS, since, until, SEASON_START],
    ).fetchall()
    last = {(g, m): (b, ll) for g, m, b, ll in raw}
    return [
        {"game_id": g, "model": m, "brier": float(b), "log_loss": float(ll)}
        for (g, m), (b, ll) in sorted(last.items())
        if b is not None and ll is not None
    ]


def load_box(con: Any, since: date, until: date) -> dict[tuple[str, int], tuple[float | None, int]]:
    raw = con.execute(
        """SELECT s.game_id, s.player_id, s.minutes, s.team_id FROM player_game_stats s
           JOIN games g USING (game_id)
           WHERE g.game_date >= ? AND g.game_date <= ? AND g.game_date >= ?""",
        [since, until, SEASON_START],
    ).fetchall()
    return {(g, int(p)): (_fnum(m), int(t)) for g, p, m, t in raw if t is not None}


def season_games_before(con: Any, until: date) -> dict[int, list[date]]:
    """Dates of games each player played this season (minutes > 0)."""
    raw = con.execute(
        """SELECT s.player_id, g.game_date FROM player_game_stats s JOIN games g USING (game_id)
           WHERE g.game_date >= ? AND g.game_date <= ? AND s.minutes > 0""",
        [SEASON_START, until],
    ).fetchall()
    out: dict[int, list[date]] = defaultdict(list)
    for p, d in raw:
        out[int(p)].append(d)
    return out


def prior_teams(con: Any, player_ids: Sequence[int]) -> dict[int, int]:
    """Team of each player's last game before this season. Reads team_id and game_date only (the one
    place the 2025-26 rows are touched; no stat, minutes or outcome leaves the query)."""
    if not player_ids:
        return {}
    marks = ",".join("?" for _ in player_ids)
    raw = con.execute(
        f"""SELECT s.player_id, arg_max(s.team_id, g.game_date) FROM player_game_stats s
            JOIN games g USING (game_id)
            WHERE g.game_date < ? AND s.player_id IN ({marks}) GROUP BY s.player_id""",  # noqa: S608
        [SEASON_START, *player_ids],
    ).fetchall()
    return {int(p): int(t) for p, t in raw if t is not None}


def load_closing_lines(
    odds: Any, game_ids: Sequence[str], until: date
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Closing main line per (game, player, stat).

    Per (game, player, market): the ``close`` snapshot rows if any, else the latest snapshots strictly
    before tip. Per book, the newest quote of each (side, point). Consensus point = the point with
    both sides at the most books (tie: smaller gap between the two side probabilities, then lower
    point). Per side the MEDIAN of the books' raw implied probabilities (a median of American prices
    straddling +/-100 is not meaningful), then de-vigged.
    """
    if not game_ids:
        return {}
    gm = ",".join("?" for _ in game_ids)
    mm = ",".join("?" for _ in MARKET_STAT)
    raw = odds.execute(
        f"""SELECT game_id, player_id, market, book, side, point, price_american, received_at,
                   snapshot_kind, tipoff
            FROM live_book_odds
            WHERE game_id IN ({gm}) AND market IN ({mm}) AND player_id IS NOT NULL
              AND point IS NOT NULL AND price_american IS NOT NULL AND side IN ('over', 'under')
              AND slate_date >= ? AND slate_date <= ?""",  # noqa: S608
        [*game_ids, *MARKET_STAT, SEASON_START, until],
    ).fetchall()
    groups: dict[tuple[str, int, str], list[tuple[Any, ...]]] = defaultdict(list)
    for r in raw:
        groups[(r[0], int(r[1]), MARKET_STAT[r[2]])].append(r)
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    for key, rs in groups.items():
        close = [r for r in rs if r[8] == "close"]
        if close:
            pool, kind = close, "close"
        else:
            pool, kind = [r for r in rs if r[9] is not None and r[7] < r[9]], "latest_pre_tip"
        newest: dict[tuple[str, str, float], tuple[datetime, int]] = {}
        for r in pool:
            k = (r[3], r[4], float(r[5]))
            if k not in newest or r[7] > newest[k][0]:
                newest[k] = (r[7], int(r[6]))
        by_point: dict[float, dict[str, dict[str, float]]] = defaultdict(
            lambda: {"over": {}, "under": {}}
        )
        for (book, side, pt), (_, price) in newest.items():
            by_point[pt][side][book] = american_to_prob(price)
        cands = []
        for pt, sides in by_point.items():
            books = set(sides["over"]) & set(sides["under"])
            if not books:
                continue
            po = float(np.median([sides["over"][b] for b in books]))
            pu = float(np.median([sides["under"][b] for b in books]))
            cands.append((-len(books), abs(po - pu), pt, len(books), po, pu))
        if not cands:
            continue
        _, _, pt, nb, po, pu = min(cands)
        out[key] = {
            "line": pt, "n_books": nb, "kind": kind,
            "p_over_raw": po, "p_under_raw": pu, "p_over_devig": devig(po, pu),
        }  # fmt: skip
    return out


def read_meta(con: Any) -> str:
    try:
        rows = con.execute("SELECT * FROM meta").fetchall()
    except duckdb.Error:
        return "n/a (no meta table in nba.duckdb)"
    return json.dumps([[str(x) for x in r] for r in rows[:5]])


# ------------------------------------------------------------------ assembly


def build_rows(
    scored: list[dict[str, Any]],
    box: dict[tuple[str, int], tuple[float | None, int]],
    lines: dict[tuple[str, int, str], dict[str, Any]],
    prior: dict[int, int] | None,
    played: dict[int, list[date]],
) -> list[dict[str, Any]]:
    out = []
    for r in scored:
        r = dict(r)
        mins, team = box.get((r["game_id"], r["player_id"]), (None, None))
        r["act_min"] = mins if mins is not None and mins > 0 else None
        r["team_id"] = team
        r["team_flag"] = (
            "no history" if prior is None else team_flag(prior.get(r["player_id"]), team)
        )
        r["prior_games"] = bucket_prior(
            sum(1 for d in played.get(r["player_id"], []) if d < r["game_date"])
        )
        ln = lines.get((r["game_id"], r["player_id"], r["stat"]))
        r["line"] = ln["line"] if ln else None
        r["lean"] = lean_side(r["mean"], r["line"])
        r["min_bucket"] = bucket_minutes(r["proj_min"])
        r["pplay_bucket"] = bucket_pplay(r["p_play"])
        r["err"] = r["y"] - r["mean"]
        if r["act_min"] and r["proj_min"] and r["proj_min"] > 0:
            r.update(decompose(r["y"], r["mean"], r["proj_min"], r["act_min"]))
            r["has_decomp"] = True
        else:
            r["has_decomp"] = False
        r["mkt"] = None
        if ln:
            po = p_over(r["pred"], ln["line"])
            if po is not None:
                side = r["lean"]
                hit = float(r["y"] > ln["line"]) if side == "over" else float(r["y"] < ln["line"])
                pm = p_side_from_over(po, side)
                pk = ln["p_over_devig"] if side == "over" else 1.0 - ln["p_over_devig"]
                r["mkt"] = {"side": side, "hit": hit, "p_model": pm, "p_mkt": pk,
                            "b_model": (pm - hit) ** 2, "b_mkt": (pk - hit) ** 2}  # fmt: skip
        out.append(r)
    return out


DIMS: list[tuple[str, str]] = [
    ("stat", "Stat"),
    ("lean", "Lean vs posted line"),
    ("min_bucket", "Projected minutes"),
    ("pplay_bucket", "p_play"),
    ("team_flag", "Team vs last pre-season game"),
    ("prior_games", "Season games before this one"),
]
ORDER = {
    "stat": list(STATS),
    "lean": ["over", "under", NOLINE],
    "min_bucket": ["<15", "15-25", "25-32", "32+", "no proj minutes"],
    "pplay_bucket": ["<0.75", "0.75-0.9", "0.9+", "no p_play"],
    "team_flag": ["new team", "same team", "no history"],
    "prior_games": ["0", "1-5", "6+"],
    "side": ["over", "under"],
}


def slices(rows: list[dict[str, Any]], dim: str) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(v, [r for r in rows if r[dim] == v]) for v in ORDER[dim]]


def part1(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def metrics(rs: list[dict[str, Any]]) -> dict[str, Any]:
        crps = [r for r in rs if r["crps"] is not None]
        return {
            "crps": cell(crps, lambda r: [r["crps"], 1.0], mean_fn),
            "bias": cell(rs, lambda r: [r["err"], 1.0], mean_fn),
            "mae": cell(rs, lambda r: [abs(r["err"]), 1.0], mean_fn),
        }

    out: dict[str, Any] = {"all": metrics(rows), "slices": {}}
    for dim, _ in DIMS:
        out["slices"][dim] = {v: metrics(rs) for v, rs in slices(rows, dim) if rs}
    return out


def part2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dec = [r for r in rows if r["has_decomp"]]

    def shares(rs: list[dict[str, Any]]) -> dict[str, Any]:
        def col(r: dict[str, Any]) -> list[float]:
            mm, mr = r["miss_minutes"], r["miss_rate"]
            return [mm * mm, mr * mr, 2 * mm * mr, r["err"] ** 2]

        return {
            "minutes_share": cell(rs, col, ratio_fn(0, 3)),
            "rate_share": cell(rs, col, ratio_fn(1, 3)),
            "cross_share": cell(rs, col, ratio_fn(2, 3)),
        }

    out: dict[str, Any] = {"all": shares(dec), "slices": {}, "by_stat_slice": {}}
    for dim, _ in DIMS:
        out["slices"][dim] = {v: shares(rs) for v, rs in slices(dec, dim) if rs}
    for st in STATS:
        sr = [r for r in dec if r["stat"] == st]
        out["by_stat_slice"][st] = {
            dim: {v: shares(rs) for v, rs in slices(sr, dim) if rs}
            for dim, _ in DIMS
            if dim != "stat"
        }
    top = sorted(dec, key=lambda r: (-abs(r["err"]), r["game_id"], r["player_id"], r["stat"]))[:10]
    out["top_misses"] = [
        {
            "player_id": r["player_id"], "stat": r["stat"], "game_id": r["game_id"],
            "forecast": r["mean"], "actual": r["y"], "err": r["err"],
            "proj_min": r["proj_min"], "act_min": r["act_min"],
            "miss_minutes": r["miss_minutes"], "miss_rate": r["miss_rate"],
        }
        for r in top
    ]  # fmt: skip
    return out


def part3(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pr = [r for r in rows if r["mkt"]]
    for r in pr:
        r["side"] = r["mkt"]["side"]

    def m(rs: list[dict[str, Any]]) -> dict[str, Any]:
        def col(key: str) -> Callable[[dict[str, Any]], list[float]]:
            return lambda r: [r["mkt"][key], 1.0]

        return {
            "hit_rate": cell(rs, col("hit"), mean_fn),
            "market_p": cell(rs, col("p_mkt"), mean_fn),
            "model_p": cell(rs, col("p_model"), mean_fn),
            "brier_model": cell(rs, col("b_model"), mean_fn),
            "brier_market": cell(rs, col("b_mkt"), mean_fn),
            "brier_diff": cell(
                rs, lambda r: [r["mkt"]["b_model"] - r["mkt"]["b_mkt"], 1.0], mean_fn
            ),
        }

    dims = [d for d in DIMS if d[0] != "lean"]
    out: dict[str, Any] = {"all": m(pr), "slices": {}}
    for dim, _ in [("side", "Model side"), *dims]:
        out["slices"][dim] = {v: m(rs) for v, rs in slices(pr, dim) if rs}
    return out


def winner(wins: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for mdl in WIN_MODELS:
        rs = [w for w in wins if w["model"] == mdl]
        out[mdl] = {
            "brier": cell(rs, lambda r: [r["brier"], 1.0], mean_fn),
            "log_loss": cell(rs, lambda r: [r["log_loss"], 1.0], mean_fn),
        }
    return out


# ------------------------------------------------------------------ report


def build_report(
    nba_con: Any,
    odds_con: Any | None,
    day: date,
    since: date,
    *,
    use_prior_team: bool = True,
    names: Callable[[int], str] | None = None,
    made_at: datetime | None = None,
) -> dict[str, Any]:
    since = max(since, SEASON_START)
    scored, skipped = load_scored(nba_con, since, day)
    wins = load_win(nba_con, since, day)
    box = load_box(nba_con, since, day)
    gids = sorted({r["game_id"] for r in scored})
    lines = load_closing_lines(odds_con, gids, day) if odds_con is not None else {}
    prior = (
        prior_teams(nba_con, sorted({r["player_id"] for r in scored})) if use_prior_team else None
    )
    played = season_games_before(nba_con, day)
    rows = build_rows(scored, box, lines, prior, played)
    nm = names or (lambda pid: f"Player {pid}")
    p2 = part2(rows)
    for t in p2["top_misses"]:
        t["player"] = nm(t["player_id"])
    return {
        "date": day.isoformat(),
        "since": since.isoformat(),
        "made_at": (made_at or datetime.now()).isoformat(timespec="seconds"),
        "data_version": read_meta(nba_con),
        "fields": {
            "projected_minutes": "forecast JSON 'proj_minutes' (rows without it are skipped in "
            "part 2; no recency fallback, it would read the 2025-26 box)",
            "actual_minutes": "player_game_stats.minutes (> 0)",
            "line": "market_asof.live_book_odds close snapshot (else latest pre-tip), main "
            "markets only, consensus point, median implied prob per side, de-vigged",
            "grid": "nba.parlay.top_source.p_over on forecast p_ge_full (x.5 lines only)",
            "prior_team": "last pre-2026-10-01 team_id" if use_prior_team else "disabled",
        },
        "counts": {
            "rows_scored": len(rows),
            "rows_with_closing_line": sum(1 for r in rows if r["line"] is not None),
            "rows_priced_for_part3": sum(1 for r in rows if r["mkt"]),
            "rows_whole_number_line": sum(
                1 for r in rows if r["line"] is not None and r["mkt"] is None
            ),
            "rows_with_box_minutes": sum(1 for r in rows if r["act_min"] is not None),
            "rows_with_proj_minutes": sum(1 for r in rows if r["proj_min"] is not None),
            "rows_in_decomposition": sum(1 for r in rows if r["has_decomp"]),
            "games": len(gids),
            "win_games": len({w["game_id"] for w in wins}),
            "skipped": skipped,
        },
        "part1": part1(rows),
        "winner": winner(wins),
        "part2": p2,
        "part3": part3(rows),
    }


def _table(title: str, cols: list[str], data: dict[str, dict[str, Any]], d: int) -> list[str]:
    lines = [
        f"**{title}**",
        "",
        "| slice | " + " | ".join(cols) + " |",
        "|---|" + "---|" * len(cols),
    ]
    for k, mets in data.items():
        lines.append(f"| {k} | " + " | ".join(fmt_cell(mets.get(c), d) for c in cols) + " |")
    return [*lines, ""]


def render(rep: dict[str, Any]) -> str:
    c = rep["counts"]
    dim_title = dict(DIMS) | {"side": "Model side"}
    o = [
        f"# Live forward scorecard {rep['date']}",
        "",
        "Diagnostic only. Not a model, not a claim, and NOT the pre-registered comparison in "
        "docs/FORWARD_PREREG_2026_27.md. Cells: `value [95% CI] n=rows (games g)`; CI is a "
        f"game-clustered bootstrap ({N_BOOT} resamples, seed {SEED}); `[n/a]` = one game, no CI; "
        f"`too few` = n < {MIN_N}. Any claim needs its own pre-registered rule.",
        "",
        f"- window {rep['since']} to {rep['date']} ; made_at {rep['made_at']} ; data_version "
        f"{rep['data_version']}",
        f"- rows scored {c['rows_scored']} ({c['games']} games) ; with closing line "
        f"{c['rows_with_closing_line']} (priced for part 3: {c['rows_priced_for_part3']}, whole-number "
        f"line: {c['rows_whole_number_line']}) ; with box minutes {c['rows_with_box_minutes']} ; "
        f"with projected minutes {c['rows_with_proj_minutes']} ; in decomposition "
        f"{c['rows_in_decomposition']}",
        *(f"- {k}: {v}" for k, v in rep["fields"].items()),
        "",
        "## Part 1: accuracy (production props_context_residual)",
        "",
    ]
    p1 = rep["part1"]
    o += _table("Headline", ["crps", "bias", "mae"], {"all stats": p1["all"]}, 3)
    o += [
        "bias = mean(actual - forecast mean); crps is the stored crps column.",
        "",
        *_table("Winner models (Brier, log loss)", ["brier", "log_loss"], rep["winner"], 3),
    ]
    for dim, _ in DIMS:
        o += _table(dim_title[dim], ["crps", "bias", "mae"], p1["slices"][dim], 3)
    p2 = rep["part2"]
    o += [
        "## Part 2: minutes vs per-minute rate",
        "",
        "miss_minutes = (actual_min - proj_min) * proj_rate ; miss_rate = (actual_rate - proj_rate) * "
        "actual_min ; they sum to (actual - forecast) exactly. Shares are of total squared error; "
        "cross = 2 * miss_minutes * miss_rate.",
        "",
        *_table("Headline", ["minutes_share", "rate_share", "cross_share"], {"all": p2["all"]}, 2),
    ]
    for dim, _ in DIMS:
        o += _table(
            dim_title[dim], ["minutes_share", "rate_share", "cross_share"], p2["slices"][dim], 2
        )
    o += ["**Ten biggest misses (raw |actual - forecast|)**", ""]
    o += ["| player | stat | fcst | actual | proj min | act min | from minutes | from rate |"]
    o += ["|---|---|---|---|---|---|---|---|"]
    for t in p2["top_misses"]:
        o.append(
            f"| {t['player']} | {t['stat']} | {t['forecast']:.1f} | {t['actual']:.0f} | "
            f"{t['proj_min']:.1f} | {t['act_min']:.1f} | {t['miss_minutes']:+.1f} | "
            f"{t['miss_rate']:+.1f} |"
        )
    p3 = rep["part3"]
    cols = ["hit_rate", "market_p", "model_p", "brier_model", "brier_market", "brier_diff"]
    o += [
        "",
        "## Part 3: model vs closing market (monitoring only)",
        "",
        "Model side at the closing consensus main line (over iff mean > line). hit_rate = how often "
        "that side won; market_p / model_p = de-vigged closing probability / model P for that side. "
        "brier_diff = model minus market (negative = model better), paired by prop. Closing prices "
        "are a benchmark here; the pre-registered comparison uses prediction-time prices.",
        "",
        *_table("Headline", cols, {"all": p3["all"]}, 3),
    ]
    for dim, sl in p3["slices"].items():
        o += _table(dim_title[dim], cols, sl, 3)
    return "\n".join(o) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    ap.add_argument("--out-dir", default="reports/forward_scorecard")
    ap.add_argument("--nba-db", default=str(NBA_DB))
    ap.add_argument("--market-db", default=str(MARKET_DB))
    ap.add_argument("--no-prior-team", action="store_true")
    a = ap.parse_args(argv)
    day, since = date.fromisoformat(a.date), date.fromisoformat(a.since)
    from nba.explain import names

    nba_con = connect_ro(Path(a.nba_db))
    odds_con = connect_ro(Path(a.market_db)) if Path(a.market_db).exists() else None
    rep = build_report(
        nba_con, odds_con, day, since, use_prior_team=not a.no_prior_team, names=names.player_name
    )
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.date}.json").write_text(json.dumps(rep, indent=2, sort_keys=True, default=str))
    (out / f"{a.date}.md").write_text(render(rep))
    print(f"wrote {out / (a.date + '.md')} rows={rep['counts']['rows_scored']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
