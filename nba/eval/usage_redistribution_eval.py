"""Usage redistribution 2A/2B re-run (TEST_PLAN_2026-10-08b section (b), Family UR, m=8).

Mechanisms: 2A (``method="player"``) and 2B (``method="team"``) from
``nba.sim.usage_redistribution``, now TRIGGERED by the official NBA pre-game
injury report (``player_availability`` rows, ``source='nba_official_report'``)
instead of the minutes-model projection that made them inert (prior real-DB
run: restricted n = 0).

## Pre-registered protocol implemented here (do not change without a new plan)

- Seasons ``< 2025`` only. ``max_season >= 2025`` raises: the 2025 touch is a
  separate, logged, first-touch run.
- Signal: status OUT only (primary; ``--statuses out,doubtful`` is a secondary
  sensitivity OUTSIDE the family). For each game the LATEST report whose
  ``as_of <= game_date + tip proxy - 60 min`` is used; later or future rows are
  ignored. ``games`` has no tip time, so the tip is a proxy (default 19:00 ET,
  ``--tip-hour``); matinees are a stated limitation.
- Rotation (as-of) = mean minutes over games played strictly before the game
  date >= 20, with >= 5 such games. Restricted sample = non-OUT teammates of
  an OUT rotation player (box-score rows of that team-game).
- Preconditions are computed and printed BEFORE the A/B; if unmet, no test is
  run: (i) report coverage >= 80% of games in the window (game-level; the
  table has no per-team key, date-level coverage is printed as well),
  (ii) every used report timestamp is >= 60 min before the tip proxy
  (min/median lead printed), (iii) player match: share of OUT players that
  exist in ``player_game_stats`` plus the count of rows with NULL ``game_id``,
  and "mechanism fires": restricted n > 0.
- Cells {2A, 2B} x {pts, reb, ast, fg3m}; metric = CRPS delta (mechanism on -
  off), per player-game, game-clustered bootstrap CI
  (``nba.props.metrics.paired_score_delta_ci`` with ``cluster_ids``). The sim
  has no 3PM head, so the fg3m cells are NOT COMPUTABLE; they still count in
  m = 8 with p = 1 (conservative).
- Ship a cell iff: clustered CI upper < 0, BH-survives (m = 8, q = 0.05),
  |delta| >= 0.005, AND the unrestricted (all player-games of covered games;
  uncovered games have no trigger by construction) clustered CI upper
  <= +0.003. Min n: >= 500 restricted player-games AND >= 150 distinct
  games per stat, else the cell is excluded as underpowered (p = 1 in BH).
- Games without a usable report are never imputed (not restricted).

## Other design notes

- Mechanism OFF (baseline) = the shipped sim: roster = box-score rows, flat
  ``shot_share_prior``. Mechanism ON adds ONLY the OUT players needed to
  identify freed usage (own latest strictly-prior shot rates), applies
  ``apply_usage_redistribution``, then drops them before simulating. Minutes
  of teammates are NOT adjusted (tilt of shot share only); this is why a
  small effect is the expected outcome.
- Boost scalars (A/B) are recomputed per calendar month from games strictly
  before the month start (``game_date < month_start``).
- Games with no OUT rotation player are not simulated: both arms are
  identical there, so their delta is exactly 0 and they enter the unrestricted
  sample as zero-delta rows (clustered by game).
- Both arms set OUT players' projected minutes to 0 (``zero_out_minutes=True``,
  default) so the A/B isolates the 2A/2B shot-share tilt from the (separate,
  unpre-registered) effect of simply knowing a star is out. Real box scores
  carry 0-minute DNP rows, so an OUT player is usually in the roster.
- OUT-listed players are never scored, whether or not they actually played.

Runtime/offline: not run on the real DB by the author (backfill DB locked).
Maintainer command (see ``main``):

    uv run python -m nba.eval.usage_redistribution_eval --db nba.duckdb \\
        --availability-db data/backfill_db/nba_backfill.duckdb --n-sims 2000 \\
        --out reports/usage_redistribution_ur.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.player_possession_features import build_player_shot_rates
from nba.features.player_rebound_assist_features import build_player_reb_ast_rates
from nba.features.possession_features import build_team_possession_rates
from nba.props.metrics import crps_array, paired_score_delta_ci
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.sim.player_attribution import (
    PlayerSimProfile,
    profiles_from_features,
    simulate_game_with_players,
)
from nba.sim.usage_redistribution import (
    ReportTriggerConfig,
    UsageRedistributionConfig,
    apply_usage_redistribution,
    latest_pretip_flagged,
    load_report_rows,
    player_boost_from_rows,
    prior_minutes_state,
    qualifying_rows_for_reference,
    rotation_flagged_by_team,
    team_boost_from_rows,
    usable_report_rows,
)

DEFAULT_N_SIMS = 2000
MAX_ALLOWED_SEASON = 2024
M_FAMILY = 8
Q = 0.05
EFFECT_FLOOR = 0.005
FULL_SAMPLE_CI_UPPER_MAX = 0.003
MIN_ROWS = 500
MIN_GAMES = 150
MIN_COVERAGE = 0.80
MIN_LEAD_MINUTES = 60.0

STATS = ("pts", "reb", "ast", "fg3m")
MECHANISMS = ("2A", "2B")
SIM_STATS = ("pts", "reb", "ast")  # fg3m has no sim head


# ---------------------------------------------------------------------------
# small statistics helpers
# ---------------------------------------------------------------------------


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """BH-adjusted q-values (monotone), same order as the input."""
    m = len(p_values)
    order = np.argsort(p_values)
    q = np.empty(m, dtype=float)
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, p_values[idx] * m / rank)
        q[idx] = running
    return [float(x) for x in q]


def cluster_bootstrap_p(
    delta: np.ndarray, cluster_ids: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> float:
    """Two-sided bootstrap p-value for mean(delta) = 0, resampling clusters.

    p = 2 * min(P(boot <= 0), P(boot >= 0)) with +1 smoothing, capped at 1.
    """
    delta = np.asarray(delta, dtype=float)
    if delta.size == 0:
        return 1.0
    _, inv = np.unique(np.asarray(cluster_ids), return_inverse=True)
    g = int(inv.max()) + 1
    sums = np.bincount(inv, weights=delta, minlength=g)
    counts = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, g, size=(n_boot, g))
    boot = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    le = (np.sum(boot <= 0) + 1) / (n_boot + 1)
    ge = (np.sum(boot >= 0) + 1) / (n_boot + 1)
    return float(min(1.0, 2.0 * min(le, ge)))


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------


@dataclass
class Preconditions:
    n_window_games: int = 0
    n_games_with_usable_report: int = 0
    game_coverage: float = 0.0
    date_coverage: float = 0.0
    n_rows_total: int = 0
    n_rows_after_cutoff_ignored: int = 0
    n_null_game_id_rows: int = 0
    lead_min_minutes: float = float("nan")
    lead_median_minutes: float = float("nan")
    n_flagged_players: int = 0
    player_match_rate: float = float("nan")
    tipoff_hour_et: float = 19.0
    coverage_ok: bool = False
    lead_ok: bool = False
    mechanism_fires: bool = False
    n_restricted_player_games_hint: int = 0
    passed: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"PRECONDITIONS (tip proxy {self.tipoff_hour_et:.2f}h ET, "
            f"lead>={MIN_LEAD_MINUTES:.0f}m)\n"
            f"  coverage: {self.n_games_with_usable_report}/{self.n_window_games} games = "
            f"{self.game_coverage:.1%} (date-level {self.date_coverage:.1%}; need >= "
            f"{MIN_COVERAGE:.0%}) -> {'OK' if self.coverage_ok else 'FAIL'}\n"
            f"  timestamps: lead to tip proxy min={self.lead_min_minutes:.0f}m "
            f"median={self.lead_median_minutes:.0f}m; rows ignored as after cutoff "
            f"{self.n_rows_after_cutoff_ignored}/{self.n_rows_total} -> "
            f"{'OK' if self.lead_ok else 'FAIL'}\n"
            f"  player match: {self.player_match_rate:.1%} of {self.n_flagged_players} flagged "
            f"players exist in player_game_stats; rows with NULL game_id: "
            f"{self.n_null_game_id_rows}\n"
            f"  mechanism fires (rotation OUT teammates exist): "
            f"{'OK' if self.mechanism_fires else 'FAIL'}\n"
            f"  => {'PASS' if self.passed else 'FAIL (no A/B run, nothing ledgered as a null)'}"
        )


def _null_game_id_rows(con: duckdb.DuckDBPyConnection, cfg: ReportTriggerConfig) -> int:
    ph = ", ".join("?" for _ in cfg.sources)
    row = con.execute(
        f"SELECT COUNT(*) FROM {cfg.table} WHERE source IN ({ph}) AND game_id IS NULL",
        list(cfg.sources),
    ).fetchone()
    return int(row[0]) if row else 0


def compute_preconditions(
    con: duckdb.DuckDBPyConnection,
    cfg: ReportTriggerConfig,
    window_game_ids: list[str],
    rows: pl.DataFrame,
    flagged: dict[str, set[int]],
    used: dict[str, dt.datetime],
    game_dates: dict[str, dt.date],
    rot: dict[tuple[str, int], set[int]],
) -> Preconditions:
    """Coverage / timestamp / match checks. Pure given its inputs (+ two counts)."""
    pre = Preconditions(tipoff_hour_et=cfg.tipoff_hour_et)
    pre.n_window_games = len(window_game_ids)
    wset = set(window_game_ids)
    pre.n_games_with_usable_report = sum(1 for g in used if g in wset)
    pre.game_coverage = pre.n_games_with_usable_report / max(pre.n_window_games, 1)
    usable = usable_report_rows(rows, cfg)
    report_dates = set(usable["game_date"].to_list()) if usable.height else set()
    win_dates = {game_dates[g] for g in window_game_ids}
    pre.date_coverage = len(win_dates & report_dates) / max(len(win_dates), 1)
    pre.n_rows_total = rows.height
    pre.n_rows_after_cutoff_ignored = rows.height - usable.height
    try:
        pre.n_null_game_id_rows = _null_game_id_rows(con, cfg)
    except duckdb.Error:
        pre.n_null_game_id_rows = -1
    leads = [
        (
            dt.datetime.combine(game_dates[g], dt.time())
            + dt.timedelta(hours=cfg.tipoff_hour_et)
            - t
        ).total_seconds()
        / 60.0
        for g, t in used.items()
        if g in game_dates
    ]
    if leads:
        pre.lead_min_minutes = float(np.min(leads))
        pre.lead_median_minutes = float(np.median(leads))
    pre.lead_ok = bool(leads) and pre.lead_min_minutes >= MIN_LEAD_MINUTES
    pre.coverage_ok = pre.game_coverage >= MIN_COVERAGE
    flagged_pids = {p for g, ps in flagged.items() if g in wset for p in ps}
    pre.n_flagged_players = len(flagged_pids)
    if flagged_pids:
        known = {
            int(r[0])
            for r in con.execute("SELECT DISTINCT player_id FROM player_game_stats").fetchall()
        }
        pre.player_match_rate = len(flagged_pids & known) / len(flagged_pids)
    pre.mechanism_fires = any(g in wset for (g, _t) in rot)
    pre.passed = pre.coverage_ok and pre.lead_ok and pre.mechanism_fires
    return pre


# ---------------------------------------------------------------------------
# cell decision
# ---------------------------------------------------------------------------


@dataclass
class CellResult:
    mechanism: str
    stat: str
    status: str  # ship | no_ship | underpowered | not_computable
    n_rows: int = 0
    n_games: int = 0
    n_fired_rows: int = 0
    delta: float = float("nan")
    ci_lo: float = float("nan")
    ci_hi: float = float("nan")
    p: float = 1.0
    q_bh: float = 1.0
    full_n: int = 0
    full_delta: float = float("nan")
    full_ci_hi: float = float("nan")
    reasons: list[str] = field(default_factory=list)


def decide_cell(cell: CellResult) -> CellResult:
    """Apply the pre-registered rule given CI/p/q already filled in."""
    reasons: list[str] = []
    if not (cell.ci_hi < 0):
        reasons.append("restricted CI upper >= 0")
    if not (cell.q_bh <= Q):
        reasons.append(f"BH q {cell.q_bh:.3f} > {Q}")
    if not (abs(cell.delta) >= EFFECT_FLOOR):
        reasons.append(f"|delta| < {EFFECT_FLOOR}")
    if not (cell.full_ci_hi <= FULL_SAMPLE_CI_UPPER_MAX):
        reasons.append(f"unrestricted CI upper > {FULL_SAMPLE_CI_UPPER_MAX}")
    cell.status = "ship" if not reasons else "no_ship"
    cell.reasons = reasons
    return cell


@dataclass
class UsageRedistributionEvalResult:
    preconditions: Preconditions
    ran: bool
    cells: list[CellResult] = field(default_factory=list)
    n_games_simulated: int = 0
    n_games_skipped: int = 0
    boost_by_month: dict[str, tuple[float, int, float, int]] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [self.preconditions.summary()]
        if not self.ran:
            return "\n".join(lines)
        lines.append(
            f"A/B: simulated {self.n_games_simulated} games with a rotation OUT player "
            f"({self.n_games_skipped} skipped: missing rates/roster); family m={M_FAMILY}, "
            f"q={Q}, floor={EFFECT_FLOOR}"
        )
        lines.append(
            "mech stat   status          n_rows n_games fired  delta[CI95]                 "
            "p      q_bh   full_delta full_hi"
        )
        for c in self.cells:
            lines.append(
                f"{c.mechanism:4s} {c.stat:5s} {c.status:15s} {c.n_rows:6d} {c.n_games:7d} "
                f"{c.n_fired_rows:5d}  {c.delta:+.4f}[{c.ci_lo:+.4f},{c.ci_hi:+.4f}] "
                f"{c.p:.4f} {c.q_bh:.4f} {c.full_delta:+.5f} {c.full_ci_hi:+.5f}"
                + (f"  <- {'; '.join(c.reasons)}" if c.reasons else "")
            )
        shipped = [f"{c.mechanism}/{c.stat}" for c in self.cells if c.status == "ship"]
        lines.append("SHIP: " + (", ".join(shipped) if shipped else "none"))
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, indent=1)


# ---------------------------------------------------------------------------
# data helpers
# ---------------------------------------------------------------------------


def _q(con: duckdb.DuckDBPyConnection, sql: str, schema: dict[str, Any]) -> pl.DataFrame:
    con.execute(sql)
    rows = con.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def _by_key(df: pl.DataFrame, key: str) -> dict[str, pl.DataFrame]:
    return {
        (k[0] if isinstance(k, tuple) else k): v
        for k, v in df.partition_by(key, as_dict=True).items()
    }


def _bool_changed(new: list[PlayerSimProfile], old: list[PlayerSimProfile]) -> bool:
    return new is not old


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def run_usage_redistribution_eval(
    con: duckdb.DuckDBPyConnection,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int = 0,
    n_boot: int = 2000,
    max_games: int | None = None,
    max_season: int = MAX_ALLOWED_SEASON,
    top_n_usage: int = 2,
    max_boost_fraction: float = 0.3,
    boost_pseudo_count: float = 20.0,
    trigger: ReportTriggerConfig | None = None,
    min_season: int | None = None,
    zero_out_minutes: bool = True,
) -> UsageRedistributionEvalResult:
    """Preconditions, then (only if they pass) the pre-registered A/B.

    ``con`` must hold ``games``/``player_game_stats``/possessions; the
    availability table is read from ``trigger.table`` (default
    ``player_availability``; use ``avail.player_availability`` after
    ``ATTACH ... (READ_ONLY) AS avail``).
    """
    if max_season > MAX_ALLOWED_SEASON:
        raise ValueError(
            f"max_season={max_season} touches the frozen 2025 holdout; the pre-registered "
            f"plan allows season < 2025 here (<= {MAX_ALLOWED_SEASON})."
        )
    cfg = trigger or ReportTriggerConfig()

    games_df = _q(
        con,
        "SELECT game_id, game_date, season, home_team, away_team FROM games",
        {
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "season": pl.Int64,
            "home_team": pl.Int64,
            "away_team": pl.Int64,
        },
    ).filter(pl.col("season") <= max_season)
    if min_season is not None:
        games_df = games_df.filter(pl.col("season") >= min_season)
    games_df = games_df.sort(["game_date", "game_id"])
    if max_games is not None and games_df.height > max_games:
        # Evenly spaced over the date-sorted window (not first-N, which would sit
        # entirely in the opening weeks with no history for the boost references).
        pick = np.linspace(0, games_df.height - 1, max_games).round().astype(int)
        games_df = games_df[np.unique(pick).tolist()]
    window_ids = games_df["game_id"].to_list()
    game_dates = dict(zip(window_ids, games_df["game_date"].to_list(), strict=True))
    game_info = {
        str(r["game_id"]): (r["game_date"], int(r["home_team"]), int(r["away_team"]))
        for r in games_df.iter_rows(named=True)
    }

    rows = load_report_rows(con, cfg, window_ids)
    flagged, used = latest_pretip_flagged(rows, cfg)
    state = prior_minutes_state(con)
    rot = rotation_flagged_by_team(
        {g: p for g, p in flagged.items() if g in game_info}, game_info, state
    )
    pre = compute_preconditions(con, cfg, window_ids, rows, flagged, used, game_dates, rot)
    config_echo = {
        "n_sims": n_sims,
        "seed": seed,
        "n_boot": n_boot,
        "max_season": max_season,
        "statuses": list(cfg.statuses),
        "tipoff_hour_et": cfg.tipoff_hour_et,
        "lead_minutes": cfg.lead_minutes,
        "top_n_usage": top_n_usage,
        "max_boost_fraction": max_boost_fraction,
        "boost_pseudo_count": boost_pseudo_count,
        "zero_out_minutes": zero_out_minutes,
    }
    if not pre.passed:
        return UsageRedistributionEvalResult(pre, ran=False, config=config_echo)

    # ---- features (all as-of by construction) ----
    team_rates = build_team_possession_rates(con)
    shot_rates = build_player_shot_rates(con)
    reb_ast = build_player_reb_ast_rates(con)
    minutes_feats = build_minutes_features(con)
    minutes_dists = predict_minutes(minutes_feats)
    proj = minutes_feats.select(["game_id", "player_id"]).with_columns(
        pl.Series("projected_minutes", [d.mean() for d in minutes_dists])
    )
    actual = _q(
        con,
        "SELECT game_id, player_id, team_id, COALESCE(pts,0) AS pts, COALESCE(reb,0) AS reb, "
        "COALESCE(ast,0) AS ast FROM player_game_stats",
        {
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "pts": pl.Int64,
            "reb": pl.Int64,
            "ast": pl.Int64,
        },
    )
    actual_g = _by_key(actual, "game_id")
    shot_g = _by_key(shot_rates, "game_id")
    reb_g = _by_key(reb_ast, "game_id") if reb_ast.height else {}
    proj_g: dict[str, dict[int, float]] = {}
    for gid, pid, mins in proj.iter_rows():
        proj_g.setdefault(str(gid), {})[int(pid)] = float(mins)
    team_g = _by_key(
        team_rates.select(
            [
                "game_id",
                "team_id",
                "is_home",
                "off_rtg_prior",
                "def_rtg_prior",
                "pace_prior",
                "league_avg_ppp_asof",
            ]
        ),
        "game_id",
    )
    shot_sorted = shot_rates.sort(["player_id", "game_date"])
    shot_player_cache: dict[int, pl.DataFrame] = {}

    def _prior_shot_row(pid: int, gdate: dt.date) -> pl.DataFrame:
        if pid not in shot_player_cache:
            shot_player_cache[pid] = shot_sorted.filter(pl.col("player_id") == pid)
        sub = shot_player_cache[pid]
        return sub.filter(pl.col("game_date") < gdate).tail(1)

    # ---- per-month as-of boost references ----
    window_max = max(game_dates.values())
    ref_rows = qualifying_rows_for_reference(
        con, window_max + dt.timedelta(days=1), top_n_usage, shot_rates
    )
    boost_cache: dict[dt.date, tuple[float, int, float, int]] = {}

    def _boosts(gdate: dt.date) -> tuple[float, int, float, int]:
        ms = gdate.replace(day=1)
        if ms not in boost_cache:
            sub = ref_rows.filter(pl.col("game_date") < ms)
            ba, na = player_boost_from_rows(sub, boost_pseudo_count)
            bb, nb = team_boost_from_rows(sub, boost_pseudo_count)
            boost_cache[ms] = (ba, na, bb, nb)
        return boost_cache[ms]

    cfgs = {
        "2A": UsageRedistributionConfig(
            enabled=True,
            method="player",
            top_n_usage=top_n_usage,
            max_boost_fraction=max_boost_fraction,
            boost_pseudo_count=boost_pseudo_count,
        ),
        "2B": UsageRedistributionConfig(
            enabled=True,
            method="team",
            top_n_usage=top_n_usage,
            max_boost_fraction=max_boost_fraction,
            boost_pseudo_count=boost_pseudo_count,
        ),
    }

    arms = ("base", "2A", "2B")
    dists: dict[str, dict[str, list[Any]]] = {s: {a: [] for a in arms} for s in SIM_STATS}
    ys: dict[str, list[float]] = {s: [] for s in SIM_STATS}
    row_game: list[str] = []
    row_restricted: list[bool] = []
    row_fired: dict[str, list[bool]] = {"2A": [], "2B": []}
    n_sim_games = 0
    n_skipped = 0
    zero_game_ids: list[str] = []  # covered, never simulated: delta exactly 0

    covered = [g for g in window_ids if g in used]
    sim_games = {g for (g, _t) in rot}
    for gid in covered:
        if gid not in sim_games:
            n_rows = actual_g[gid].height if gid in actual_g else 0
            zero_game_ids.extend([gid] * n_rows)
            continue
        gdate, home_team, away_team = game_info[gid]
        roster = actual_g.get(gid)
        trates = team_g.get(gid)
        if roster is None or trates is None:
            n_skipped += 1
            continue
        h = trates.filter(pl.col("is_home"))
        a = trates.filter(~pl.col("is_home"))
        if h.height == 0 or a.height == 0:
            n_skipped += 1
            continue
        side_ids = {
            t: roster.filter(pl.col("team_id") == t)["player_id"].to_list()
            for t in (home_team, away_team)
        }
        if not side_ids[home_team] or not side_ids[away_team]:
            n_skipped += 1
            continue
        gshot = shot_g.get(gid, shot_rates.head(0))
        greb = reb_g.get(gid)
        pmap = proj_g.get(gid, {})
        ba, _na, bb, _nb = _boosts(gdate)
        boosts = {"2A": ba, "2B": bb}

        base_lists: dict[int, list[PlayerSimProfile]] = {}
        with_out: dict[int, list[PlayerSimProfile]] = {}
        extras: dict[int, set[int]] = {}
        out_sets: dict[int, set[int]] = {}
        for t in (home_team, away_team):
            out_set = rot.get((gid, t), set())
            out_sets[t] = out_set
            base = profiles_from_features(gshot, pmap, side_ids[t], greb)
            if zero_out_minutes:
                # Both arms know the player is OUT (zero availability weight); the
                # A/B then isolates the 2A/2B shot-share TILT, not the removal.
                base = [
                    replace(p, projected_minutes=0.0) if p.player_id in out_set else p for p in base
                ]
            base_lists[t] = base
            extra_ids = [p for p in sorted(out_set) if p not in set(side_ids[t])]
            extra_profiles: list[PlayerSimProfile] = []
            for pid in extra_ids:
                prior_row = _prior_shot_row(pid, gdate)
                extra_profiles.extend(profiles_from_features(prior_row, {pid: 0.0}, [pid], None))
            extras[t] = {p.player_id for p in extra_profiles}
            with_out[t] = (base + extra_profiles) if extra_profiles else base

        sim_kwargs: dict[str, Any] = dict(
            home_off_rtg=float(h[0, "off_rtg_prior"]),
            home_def_rtg=float(h[0, "def_rtg_prior"]),
            home_pace=float(h[0, "pace_prior"]),
            away_off_rtg=float(a[0, "off_rtg_prior"]),
            away_def_rtg=float(a[0, "def_rtg_prior"]),
            away_pace=float(a[0, "pace_prior"]),
            league_avg_ppp=float(h[0, "league_avg_ppp_asof"]),
            n_sims=n_sims,
            seed=seed,
        )
        results = {
            "base": simulate_game_with_players(
                base_lists[home_team], base_lists[away_team], **sim_kwargs
            )
        }
        fired: dict[str, dict[int, bool]] = {}
        for mech in MECHANISMS:
            new_lists: dict[int, list[PlayerSimProfile]] = {}
            fired[mech] = {}
            for t in (home_team, away_team):
                adj = apply_usage_redistribution(with_out[t], out_sets[t], cfgs[mech], boosts[mech])
                changed = _bool_changed(adj, with_out[t])
                fired[mech][t] = changed
                new_lists[t] = (
                    [p for p in adj if p.player_id not in extras[t]] if changed else base_lists[t]
                )
            if not any(fired[mech].values()):
                results[mech] = results["base"]
            else:
                results[mech] = simulate_game_with_players(
                    new_lists[home_team], new_lists[away_team], **sim_kwargs
                )
        n_sim_games += 1

        for t, is_home in ((home_team, True), (away_team, False)):
            for pid in side_ids[t]:
                if pid in out_sets[t]:
                    continue  # OUT-listed: never scored
                prow = roster.filter(pl.col("player_id") == pid)
                if prow.height == 0:
                    continue
                per_arm: dict[str, dict[str, Any]] = {}
                ok = True
                for arm in arms:
                    r = results[arm]
                    per_arm[arm] = {
                        "pts": (r.home_player_points if is_home else r.away_player_points).get(pid),
                        "reb": (r.home_player_rebounds if is_home else r.away_player_rebounds).get(
                            pid
                        ),
                        "ast": (r.home_player_assists if is_home else r.away_player_assists).get(
                            pid
                        ),
                    }
                    if any(per_arm[arm][s] is None for s in SIM_STATS):
                        ok = False
                if not ok:
                    continue
                for s in SIM_STATS:
                    ys[s].append(float(prow[0, s]))
                    for arm in arms:
                        dists[s][arm].append(per_arm[arm][s])
                row_game.append(gid)
                row_restricted.append(bool(out_sets[t]))
                for mech in MECHANISMS:
                    row_fired[mech].append(fired[mech][t])

    # ---- scoring ----
    game_arr = np.array(row_game)
    restricted = np.array(row_restricted, dtype=bool)
    zero_clusters = np.array(zero_game_ids)
    crps: dict[str, dict[str, np.ndarray]] = {}
    for s in SIM_STATS:
        y = np.array(ys[s], dtype=float)
        crps[s] = {arm: crps_array(dists[s][arm], y) for arm in arms} if y.size else {}

    cells: list[CellResult] = []
    for mech in MECHANISMS:
        fired_arr = np.array(row_fired[mech], dtype=bool)
        for s in STATS:
            cell = CellResult(mechanism=mech, stat=s, status="not_computable")
            if s not in SIM_STATS or not crps[s]:
                cell.reasons.append("sim has no 3PM head" if s == "fg3m" else "no scored rows")
                cells.append(cell)
                continue
            delta = crps[s][mech] - crps[s]["base"]
            r_delta = delta[restricted]
            r_clu = game_arr[restricted]
            cell.n_rows = int(restricted.sum())
            cell.n_games = int(np.unique(r_clu).size)
            cell.n_fired_rows = int((fired_arr & restricted).sum())
            full_delta = np.concatenate([delta, np.zeros(zero_clusters.size)])
            full_clu = np.concatenate([game_arr, zero_clusters])
            cell.full_n = int(full_delta.size)
            if cell.n_rows > 0 and cell.n_games >= 2:
                ci = paired_score_delta_ci(
                    crps[s][mech][restricted],
                    crps[s]["base"][restricted],
                    n_boot=n_boot,
                    seed=seed,
                    cluster_ids=r_clu,
                )
                cell.delta, cell.ci_lo, cell.ci_hi = ci.point, ci.lo, ci.hi
                cell.p = cluster_bootstrap_p(r_delta, r_clu, n_boot, seed)
            fci = paired_score_delta_ci(
                full_delta,
                np.zeros_like(full_delta),
                n_boot=n_boot,
                seed=seed,
                cluster_ids=full_clu,
            )
            cell.full_delta, cell.full_ci_hi = fci.point, fci.hi
            if cell.n_rows < MIN_ROWS or cell.n_games < MIN_GAMES:
                cell.status = "underpowered"
                cell.reasons.append(
                    f"underpowered: {cell.n_rows} rows (<{MIN_ROWS}) / {cell.n_games} games "
                    f"(<{MIN_GAMES}); p set to 1 in BH"
                )
                cell.p = 1.0
            else:
                cell.status = "pending"
            cells.append(cell)

    qs = benjamini_hochberg([c.p for c in cells])
    for c, q in zip(cells, qs, strict=True):
        c.q_bh = q
        if c.status == "pending":
            decide_cell(c)

    return UsageRedistributionEvalResult(
        pre,
        ran=True,
        cells=cells,
        n_games_simulated=n_sim_games,
        n_games_skipped=n_skipped,
        boost_by_month={k.isoformat(): v for k, v in sorted(boost_cache.items())},
        config=config_echo,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb", help="main DB (opened read-only)")
    ap.add_argument(
        "--availability-db",
        default=None,
        help="DB holding player_availability (ATTACHed read-only); default: --db",
    )
    ap.add_argument("--n-sims", type=int, default=DEFAULT_N_SIMS)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-games", type=int, default=None)
    ap.add_argument("--max-season", type=int, default=MAX_ALLOWED_SEASON)
    ap.add_argument("--min-season", type=int, default=None)
    ap.add_argument("--statuses", default="out", help="comma list; primary = out")
    ap.add_argument("--tip-hour", type=float, default=19.0, help="tip-off proxy, hour ET")
    ap.add_argument("--out", default=None, help="write result JSON here")
    args = ap.parse_args(argv)

    from nba.db.connect import connect

    con = connect(args.db, read_only=True)
    table = "player_availability"
    if args.availability_db and Path(args.availability_db).resolve() != Path(args.db).resolve():
        path = str(args.availability_db).replace("'", "''")
        con.execute(f"ATTACH '{path}' AS avail (READ_ONLY)")
        table = "avail.player_availability"
    trig = ReportTriggerConfig(
        statuses=tuple(s.strip().lower() for s in args.statuses.split(",") if s.strip()),
        tipoff_hour_et=args.tip_hour,
        table=table,
    )
    result = run_usage_redistribution_eval(
        con,
        n_sims=args.n_sims,
        seed=args.seed,
        n_boot=args.n_boot,
        max_games=args.max_games,
        max_season=args.max_season,
        min_season=args.min_season,
        trigger=trig,
    )
    print(result.summary())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(result.to_json())
    con.close()
    return 0


__all__ = [
    "CellResult",
    "Preconditions",
    "UsageRedistributionEvalResult",
    "benjamini_hochberg",
    "cluster_bootstrap_p",
    "compute_preconditions",
    "decide_cell",
    "main",
    "run_usage_redistribution_eval",
]

if __name__ == "__main__":
    raise SystemExit(main())
