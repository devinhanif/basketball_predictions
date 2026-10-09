"""SYSTEMS REPLAY of a past season through the automated daily process, on a DB COPY.

``python -m nba.daily.replay_season --season 2025 --db data/rehearsal/replay2025.duckdb``

Operational test only (docs/HOLDOUT_ACCESS_LOG.md, row 2026-10-09 "systems replay"): nothing
here selects, tunes or promotes anything, and shadow-arm vs production deltas are never
computed or reported by this module. Production primary scores are descriptive.

For every game date of the season, in order:

1. results of that date (and later) are hidden (``nba.daily.rehearsal.truncate_for_replay`` once
   at the start; each date's results are only restored after its predictions are written);
2. the pretip job's ``nba.daily run`` equivalent is called with a fake ``now`` before the tip of
   each distinct tip-off group (the live job runs at :20/:50, so a :00/:30 tip is predicted
   >= 70 min ahead): ``--skip-ingest --skip-injury`` (reports are the ``player_availability``
   rows already in the copy; the pipeline itself filters them by report stamp <= tip - 60 min),
   ``--roster-source recent`` (historical official rosters do not exist), ``--log-int-variant``
   and ``--log-lower-tail-variant``. Tips are the REAL tip-offs (``data/schedule/raw_*.parquet``;
   19:00 ET proxy only where one is missing);
3. T-30 arm only when the lineup collector has snapshots for the date, else recorded as skipped;
4. the read-only parlay shadow ``evaluate`` runs after each pretip run when a Kalshi copy is given;
5. that date's results are restored and the morning job runs: ``settle`` (+ parlay settle).
   The checkpoint (descriptive) and report run once at the end.

Not replayed (network, no 2025 equivalent): referees collect, market capture, post-game ingest.

Resumable (per-date phase in a JSON state file), streaming (state written after every phase)
and memory-bounded (chunks of ``--chunk-size`` dates run in a worker subprocess that holds
``data/ops/heavy.lock``; the lock is released between chunks). Never opens ``nba.duckdb`` for
write, never touches the real ``data/checkpoints`` or ``registry_store/reports``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import resource
import shutil
import subprocess
import sys
import time
import traceback
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.pipeline import RunSummary, run_daily
from nba.daily.rehearsal import restore_results, truncate_for_replay
from nba.daily.schedule import DB_TIP_PROXY_ET, ET, ScheduledGame, ScheduleFn, slate_for_date
from nba.daily.season import season_str
from nba.daily.settle import settle_pending
from nba.db.connect import connect_with_retry
from nba.features.game_tipoff import build_game_tipoff
from nba.ingest.cache import RateLimiter

ROOT = Path(__file__).resolve().parents[2]
HEAVY_LOCK = ROOT / "data" / "ops" / "heavy.lock"
#: the live pretip job runs at these minutes of every hour (ops/nba_daily.sh)
SLOT_MINUTES = (20, 50)
#: a :00/:30 tip must be predicted at least this long ahead so the T-60 comparator is eligible
RUN_LEAD_MIN = 70
EXIT_ALL_DONE = 10
PHASES = ("new", "predicted", "restored", "done")


# ----------------------------------------------------------------------------- schedule/clock


def real_tip_schedule(
    con: duckdb.DuckDBPyConnection, tips: pl.DataFrame | None = None
) -> ScheduleFn:
    """Schedule from the ``games`` table with REAL tip-offs (``build_game_tipoff``); a game with
    no real tip falls back to the 19:00 ET proxy (counted in ``proxy_tip_games``)."""
    from nba.ingest.games import season_to_int

    tip_map: dict[str, datetime] = {}
    t = build_game_tipoff() if tips is None else tips
    for gid, utc in t.select(["game_id", "tipoff_utc"]).iter_rows():
        if utc is not None:
            tip_map[str(gid)] = utc

    def fn(season: str) -> list[ScheduledGame]:
        rows = con.execute(
            "SELECT game_id, game_date, home_team, away_team FROM games WHERE season = ? "
            "ORDER BY game_date, game_id",
            [season_to_int(season)],
        ).fetchall()
        out: list[ScheduledGame] = []
        for gid, gd, home, away in rows:
            tip = tip_map.get(str(gid))
            if tip is None:
                proxy = datetime.combine(gd, DB_TIP_PROXY_ET, tzinfo=ET)
                tip = proxy.astimezone(UTC).replace(tzinfo=None)
            out.append(ScheduledGame(str(gid), tip, int(home), int(away)))
        return out

    fn.tip_map = tip_map  # type: ignore[attr-defined]
    return fn


def slot_floor(t: datetime) -> datetime:
    """Latest live-job slot (:20 or :50) at or before ``t`` (naive UTC; whole-hour offsets only)."""
    base = t.replace(second=0, microsecond=0)
    cands = [base.replace(minute=m) for m in SLOT_MINUTES]
    cands += [(base - timedelta(hours=1)).replace(minute=m) for m in SLOT_MINUTES]
    return max(c for c in cands if c <= t)


def pretip_run_times(slate: list[ScheduledGame]) -> list[datetime]:
    """One run per distinct tip-off group: the last :20/:50 slot >= ``RUN_LEAD_MIN`` before it.

    The live job runs every half hour; each run predicts every game not yet tipped and the
    latest run before tip - 60 min is the one scored, so a group's last eligible run is the one
    that matters for it. Earlier runs of the day are superseded and are not replayed."""
    times = {slot_floor(g.tipoff - timedelta(minutes=RUN_LEAD_MIN)) for g in slate}
    return sorted(times)


# ----------------------------------------------------------------------------- state


@dataclass
class ReplayState:
    path: Path
    data: dict[str, Any] = field(default_factory=lambda: {"dates": {}, "chunks": [], "init": {}})

    @classmethod
    def load(cls, path: Path) -> ReplayState:
        if path.exists():
            return cls(path, json.loads(path.read_text()))
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True, default=str))
        tmp.replace(self.path)

    def entry(self, d: date) -> dict[str, Any]:
        return self.data["dates"].setdefault(d.isoformat(), {"phase": "new"})  # type: ignore[no-any-return]

    def pending(self, dates: list[date]) -> list[date]:
        return [
            d for d in dates if self.data["dates"].get(d.isoformat(), {}).get("phase") != "done"
        ]


@dataclass(frozen=True)
class ReplayConfig:
    season: int
    work_db: Path
    full_db: Path
    out_dir: Path
    model_cache: Path
    kalshi_db: Path | None = None
    lineups_db: Path | None = None
    paper_db: Path | None = None
    with_props: bool = True
    log_variants: bool = True
    n_sims: int = 1000

    @property
    def state_path(self) -> Path:
        return self.out_dir / "state.json"


# ----------------------------------------------------------------------------- init / dates


def init_replay_db(src: Path, cfg: ReplayConfig, first_date: date | None = None) -> dict[str, Any]:
    """Copy ``src`` to the untouched full copy and the work copy, hide the season from its first
    date, and clear forward tables. Refuses the real database as a target."""
    if cfg.work_db.name == "nba.duckdb" or cfg.full_db.name == "nba.duckdb":
        raise SystemExit("refusing: the replay target must be a copy, not nba.duckdb")
    if cfg.work_db.resolve() == src.resolve() or cfg.full_db.resolve() == src.resolve():
        raise SystemExit("refusing: target equals source")
    if Path(f"{src}.wal").exists():
        raise SystemExit(f"{src}.wal present: a plain copy would miss committed rows")
    cfg.work_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, cfg.full_db)
    shutil.copy2(cfg.full_db, cfg.work_db)
    con = duckdb.connect(str(cfg.work_db))
    try:
        d0 = first_date or season_dates(con, cfg.season)[0]
        removed = truncate_for_replay(con, d0)
        for t in ("forward_predictions", "forward_scores", "forward_scores_elig"):
            with contextlib.suppress(duckdb.CatalogException):
                con.execute(f"DELETE FROM {t}")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return {"first_date": d0.isoformat(), "removed_rows": removed, "source": str(src)}


def season_dates(con: duckdb.DuckDBPyConnection, season: int) -> list[date]:
    rows = con.execute(
        "SELECT DISTINCT game_date FROM games WHERE season = ? ORDER BY 1", [season]
    ).fetchall()
    return [r[0] for r in rows]


def lineups_available(lineups_db: Path | None, d: date) -> bool:
    """True when the lineup collector holds at least one snapshot row for game date ``d``."""
    if lineups_db is None or not lineups_db.exists():
        return False
    try:
        con = duckdb.connect(str(lineups_db), read_only=True)
    except duckdb.Error:
        return False
    try:
        row = con.execute(
            "SELECT count(*) FROM lineup_snapshots WHERE game_date = ?", [d]
        ).fetchone()
        return bool(row and row[0] > 0)
    except duckdb.Error:
        return False
    finally:
        con.close()


def make_hidden_kalshi(master: Path, dst: Path) -> dict[str, int]:
    """Working Kalshi copy with every settlement hidden (``result``/``settled_ts`` NULL), so the
    parlay shadow sees markets as open; only prices stamped before the replay clock are read."""
    if master.resolve() == dst.resolve():
        raise SystemExit("refusing: kalshi working copy equals master")
    from nba.kalshi.__main__ import is_real_kalshi_db

    if is_real_kalshi_db(dst):
        raise SystemExit("refusing: kalshi working copy is the live Kalshi DB")
    shutil.copy2(master, dst)
    con = duckdb.connect(str(dst))
    try:
        con.execute("UPDATE kalshi_markets SET result = NULL, settled_ts = NULL")
        row = con.execute("SELECT count(*), count(DISTINCT ticker) FROM kalshi_prices").fetchone()
        n = con.execute("SELECT count(*) FROM kalshi_markets").fetchone()
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return {
        "markets": int(n[0]) if n else 0,
        "price_rows": int(row[0]) if row else 0,
        "priced_tickers": int(row[1]) if row else 0,
    }


# ----------------------------------------------------------------------------- one date


def _rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def _run_summary_dict(s: RunSummary, wall_s: float) -> dict[str, Any]:
    return {
        "run_id": s.run_id,
        "slate": s.n_slate,
        "predicted_games": s.n_predicted_games,
        "refused_after_tipoff": s.n_refused_after_tipoff,
        "rows": s.n_rows_written,
        "rows_deduped": s.n_rows_deduped,
        "props_rows": s.n_props_rows,
        "injury_status": s.injury_status,
        "out_excluded": s.n_out_excluded,
        "models": s.model_status,
        "errors": s.errors,
        "shadow_errors": s.shadow_errors,
        "wall_s": round(wall_s, 2),
    }


def _pretip_run(cfg: ReplayConfig, d: date, rt: datetime) -> RunSummary:
    """What ``nba.daily run`` does for the pretip job, with the replay clock ``rt``."""
    from nba.registry import get_registry

    con = connect_with_retry(cfg.work_db)
    try:
        return run_daily(
            con,
            d,
            schedule_fn=real_tip_schedule(con),
            now=rt,
            model_cache=cfg.model_cache,
            roster_source="recent",
            registry=get_registry(con),
            skip_ingest=True,
            skip_injury=True,
            with_props=cfg.with_props,
            props_model="context",
            log_int_variant=cfg.log_variants,
            log_lower_tail_variant=cfg.log_variants,
            n_sims=cfg.n_sims,
            rate_limiter=RateLimiter(0.0),
            tips_dir=None,
            schedule_cache_dir=None,
        )
    finally:
        con.close()


def _parlay(cfg: ReplayConfig, argv_tail: list[str]) -> tuple[int, str]:
    """Run ``python -m nba.parlay evaluate ...`` in-process against the copies; returns
    (rc, last stdout line). Never touches the real nba/kalshi/paper databases."""
    from nba.parlay.__main__ import main as parlay_main

    assert cfg.kalshi_db is not None and cfg.paper_db is not None
    argv = [
        "evaluate",
        "--nba-db", str(cfg.work_db),
        "--kalshi-db", str(cfg.kalshi_db),
        "--paper-db", str(cfg.paper_db),
        "--out-dir", str(cfg.out_dir / "parlay"),
        *argv_tail,
    ]  # fmt: skip
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = parlay_main(argv)
    return rc, buf.getvalue().strip()


def _t30(cfg: ReplayConfig, d: date, run_times: list[datetime]) -> str:
    """The T-30 shadow arm: only when lineup data exists for the date."""
    if not lineups_available(cfg.lineups_db, d):
        return "skipped: no lineup collector data for this date"
    from nba.daily.t30 import run_t30, schedule_from_lineups
    from nba.lineups.store import connect_lineups

    assert cfg.lineups_db is not None
    lcon = connect_lineups(cfg.lineups_db, read_only=True)
    con = connect_with_retry(cfg.work_db)
    try:
        now = max(run_times) + timedelta(minutes=40) if run_times else datetime.combine(d, dtime())
        s = run_t30(
            con, lcon, d,
            schedule_fn=schedule_from_lineups(lcon),
            now=now,
            model_cache=cfg.model_cache,
            roster_source="recent",
            rate_limiter=RateLimiter(0.0),  # recent rosters: no metered calls
        )  # fmt: skip
        return f"ran: {s}"
    finally:
        con.close()
        lcon.close()


def process_date(
    cfg: ReplayConfig,
    state: ReplayState,
    d: date,
    *,
    pretip: Callable[[ReplayConfig, date, datetime], RunSummary] = _pretip_run,
) -> dict[str, Any]:
    """Advance one date through new -> predicted -> restored -> done. Each phase is saved, and a
    failing step is recorded in ``failures`` instead of aborting the replay."""
    st = state.entry(d)
    st.setdefault("failures", [])
    t_start = time.monotonic()

    def fail(step: str, exc: BaseException) -> None:
        st["failures"].append(
            {"step": step, "error": f"{type(exc).__name__}: {exc}"[:300],
             "trace": traceback.format_exc()[-600:]}
        )  # fmt: skip

    if st["phase"] == "new":
        con = connect_with_retry(cfg.work_db, read_only=True)
        try:
            sched_fn = real_tip_schedule(con)
            slate = slate_for_date(sched_fn(season_str(cfg.season)), d)
            tip_map = sched_fn.tip_map  # type: ignore[attr-defined]
        finally:
            con.close()
        run_times = pretip_run_times(slate)
        st["slate_games"] = len(slate)
        st["proxy_tip_games"] = sum(1 for g in slate if g.game_id not in tip_map)
        st["run_times_utc"] = [t.isoformat() for t in run_times]
        st["runs"] = []
        st["parlay"] = []
        for rt in run_times:
            t0 = time.monotonic()
            try:
                summ = pretip(cfg, d, rt)
                rec = _run_summary_dict(summ, time.monotonic() - t0)
            except BaseException as exc:  # noqa: BLE001 - recorded, replay continues
                if isinstance(exc, KeyboardInterrupt):
                    raise
                fail(f"pretip@{rt.isoformat()}", exc)
                rec = {"error": str(exc)[:200], "wall_s": round(time.monotonic() - t0, 2)}
            st["runs"].append(rec)
            if cfg.kalshi_db is not None and cfg.paper_db is not None:
                t1 = time.monotonic()
                try:
                    rc, out = _parlay(cfg, ["--date", d.isoformat(), "--now", rt.isoformat()])
                    st["parlay"].append(
                        {
                            "rc": rc,
                            "summary": out.splitlines()[:2],
                            "wall_s": round(time.monotonic() - t1, 2),
                        }
                    )
                except BaseException as exc:  # noqa: BLE001
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    fail(f"parlay@{rt.isoformat()}", exc)
        try:
            st["t30"] = _t30(cfg, d, run_times)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            fail("t30", exc)
            st["t30"] = "failed"
        st["phase"] = "predicted"
        state.save()

    if st["phase"] == "predicted":
        con = connect_with_retry(cfg.work_db)
        try:
            st["restored_rows"] = restore_results(con, str(cfg.full_db), d, d)
        finally:
            con.close()
        st["phase"] = "restored"
        state.save()

    if st["phase"] == "restored":
        morning = datetime.combine(d + timedelta(days=1), dtime(14, 0))  # 09:00 CT next morning
        con = connect_with_retry(cfg.work_db)
        try:
            st["settle"] = settle_pending(con, morning)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            fail("settle", exc)
        finally:
            con.close()
        if cfg.kalshi_db is not None and cfg.paper_db is not None:
            try:
                rc, out = _parlay(
                    cfg,
                    ["--date", d.isoformat(), "--settle", "--no-log", "--now", morning.isoformat()],
                )
                st["parlay_settle"] = {"rc": rc, "last": out.splitlines()[-1:] if out else []}
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, KeyboardInterrupt):
                    raise
                fail("parlay_settle", exc)
        st["phase"] = "done"
    st["wall_s"] = round(st.get("wall_s", 0.0) + time.monotonic() - t_start, 2)
    st["rss_mb"] = round(_rss_mb(), 0)
    state.save()
    return st


# ----------------------------------------------------------------------------- chunks


def run_chunk(
    cfg: ReplayConfig,
    max_dates: int,
    *,
    pretip: Callable[..., RunSummary] = _pretip_run,
    start: date | None = None,
    end: date | None = None,
) -> int:
    """Process up to ``max_dates`` pending dates; returns how many dates remain pending."""
    state = ReplayState.load(cfg.state_path)
    con = connect_with_retry(cfg.work_db, read_only=True)
    try:
        dates = [
            d
            for d in season_dates(con, cfg.season)
            if (not start or d >= start) and (not end or d <= end)
        ]
    finally:
        con.close()
    todo = state.pending(dates)
    t0 = time.monotonic()
    for d in todo[:max_dates]:
        st = process_date(cfg, state, d, pretip=pretip)
        print(
            f"{d} runs={len(st.get('runs', []))} wall={st['wall_s']}s rss={st['rss_mb']}MB "
            f"failures={len(st['failures'])}",
            flush=True,
        )
    state.data["chunks"].append(
        {"dates": min(max_dates, len(todo)), "wall_s": round(time.monotonic() - t0, 1),
         "peak_rss_mb": round(_rss_mb(), 0), "at": datetime.now(UTC).isoformat()}
    )  # fmt: skip
    state.save()
    return max(0, len(todo) - max_dates)


def acquire_heavy_lock(lock: Path = HEAVY_LOCK, *, poll_s: float = 60.0) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            lock.mkdir()
            return
        except FileExistsError:
            print(f"waiting for {lock} ...", flush=True)
            time.sleep(poll_s)


def release_heavy_lock(lock: Path = HEAVY_LOCK) -> None:
    with contextlib.suppress(OSError):
        lock.rmdir()


def parlay_pass(cfg: ReplayConfig, runs: str = "all") -> dict[str, Any]:
    """Post-hoc parlay shadow over a finished replay, date by date: ``evaluate`` at each recorded
    pretip run time (``--now`` also clips the forward predictions to ``made_at <= now`` and the
    Kalshi prices to ``ts < now``), then settle against the actuals, like the morning job.
    Equivalent to running ``evaluate`` inline; used when Kalshi history arrived after the
    prediction replay. Resumable per date. ``runs='first'`` evaluates only the first run of each
    date: the shadow log keeps the first prediction of the day per (ticker, side), so later runs
    can only add markets that had no pre-tip price at the first run."""
    assert cfg.kalshi_db is not None and cfg.paper_db is not None
    state = ReplayState.load(cfg.state_path)
    n_done = 0
    for key in sorted(state.data["dates"]):
        st = state.data["dates"][key]
        if st.get("phase") != "done" or "parlay_pass" in st:
            continue
        d = date.fromisoformat(key)
        recs: list[dict[str, Any]] = []
        times = st.get("run_times_utc", [])
        for rt_s in times[:1] if runs == "first" else times:
            t0 = time.monotonic()
            try:
                rc, out = _parlay(cfg, ["--date", key, "--now", rt_s])
                recs.append({"rc": rc, "summary": out.splitlines()[:2],
                             "wall_s": round(time.monotonic() - t0, 2)})  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - recorded, pass continues
                recs.append({"error": f"{type(exc).__name__}: {exc}"[:300]})
        morning = datetime.combine(d + timedelta(days=1), dtime(14, 0))
        try:
            rc, out = _parlay(
                cfg, ["--date", key, "--settle", "--no-log", "--now", morning.isoformat()]
            )
            recs.append({"settle_rc": rc, "last": out.splitlines()[-1:]})
        except Exception as exc:  # noqa: BLE001
            recs.append({"settle_error": f"{type(exc).__name__}: {exc}"[:300]})
        st["parlay_pass"] = recs
        state.save()
        n_done += 1
        print(f"{key} parlay pass runs={len(recs) - 1}", flush=True)
    return {"dates_processed": n_done}


def parlay_summary(cfg: ReplayConfig) -> dict[str, Any]:
    """Descriptive, exploratory summary of the replayed parlay shadow log (game-winner markets;
    one row per market, YES side): model probability vs the pre-tip market mid and ask against
    the settled outcome, plus how many contracts were ever flagged. Not a decision input."""
    assert cfg.paper_db is not None
    con = duckdb.connect(str(cfg.paper_db), read_only=True)
    try:
        tot = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE settled), "
            "count(*) FILTER (WHERE settled AND outcome IS NULL) FROM shadow_predictions"
        ).fetchone()
        rows = con.execute(
            "SELECT raw_model_prob, market_mid, price, outcome, substr(shadow_id, 1, 10) "
            "FROM shadow_predictions WHERE side = 'yes' AND settled AND outcome IS NOT NULL"
        ).fetchall()
        trades = con.execute("SELECT count(*) FROM paper_trades").fetchone()
    finally:
        con.close()
    out: dict[str, Any] = {
        "shadow_rows": tot[0] if tot else None,
        "settled": tot[1] if tot else None,
        "settled_void": tot[2] if tot else None,
        "paper_trades_flagged": trades[0] if trades else None,
    }
    if rows:
        y = np.array([float(r[3]) for r in rows])
        d = np.array([r[4] for r in rows])
        for name, idx in (("model", 0), ("market_mid", 1), ("market_ask", 2)):
            p = np.clip(np.array([float(r[idx]) for r in rows]), 1e-6, 1 - 1e-6)
            ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
            out[name] = {"brier": _ci((p - y) ** 2, d), "log_loss": _ci(ll, d)}
        out["n_markets"] = len(rows)
    return out


# ----------------------------------------------------------------------------- finalize


def _ci(values: np.ndarray, clusters: np.ndarray) -> dict[str, float]:
    from nba.daily.report import cluster_bootstrap_mean

    c = cluster_bootstrap_mean(values, clusters, n_boot=2000, seed=0)
    return {"mean": c.point, "lo": c.lo, "hi": c.hi, "n": c.n, "n_dates": c.n_clusters}


def descriptive_metrics(
    con: duckdb.DuckDBPyConnection, full_db: Path, season: int
) -> dict[str, Any]:
    """PRODUCTION PRIMARY scores only (injury-Elo where a usable report existed, else MOV-Elo;
    props_context_residual). Shadow arms are deliberately not read here."""
    out: dict[str, Any] = {}
    rows = con.execute(
        """
        WITH inj AS (SELECT * FROM forward_scores_elig WHERE model_name = 'rung0_injury_elo'
                     AND target = 'win_prob_home' AND status = 'scored'),
             mov AS (SELECT * FROM forward_scores_elig WHERE model_name = 'rung0_mov_elo'
                     AND target = 'win_prob_home' AND status = 'scored'
                     AND game_id NOT IN (SELECT game_id FROM inj))
        SELECT game_date, log_loss, brier, 'injury' src FROM inj
        UNION ALL SELECT game_date, log_loss, brier, 'mov' FROM mov
        """
    ).fetchall()
    if rows:
        d = np.array([str(r[0]) for r in rows])
        out["win_primary"] = {
            "n_games": len(rows),
            "n_from_injury_elo": sum(1 for r in rows if r[3] == "injury"),
            "log_loss": _ci(np.array([r[1] for r in rows], dtype=float), d),
            "brier": _ci(np.array([r[2] for r in rows], dtype=float), d),
            "naive_log_loss": math.log(2.0),
        }
    props: dict[str, Any] = {}
    for stat in ("pts", "reb", "ast", "fg3m"):
        r = con.execute(
            "SELECT game_date, rps, pred - y, "
            "CASE WHEN y >= q10 AND y <= q90 THEN 1.0 ELSE 0.0 END "
            "FROM forward_scores_elig WHERE model_name = 'props_context_residual' AND target = ? "
            "AND status = 'scored' AND rps IS NOT NULL",
            [stat],
        ).fetchall()
        if not r:
            continue
        d = np.array([str(x[0]) for x in r])
        cnt = con.execute(
            "SELECT status, count(*) FROM forward_scores_elig WHERE model_name = "
            "'props_context_residual' AND target = ? GROUP BY 1",
            [stat],
        ).fetchall()
        props[stat] = {
            "n_scored": len(r),
            "status_counts": {k: int(v) for k, v in cnt},
            "rps_integer_support_crps": _ci(np.array([x[1] for x in r], dtype=float), d),
            "bias_pred_minus_actual": _ci(np.array([x[2] for x in r], dtype=float), d),
            "coverage_q10_q90_inclusive": _ci(np.array([x[3] for x in r], dtype=float), d),
        }
    out["props_primary"] = props
    # coverage: predicted vs played
    n_games = con.execute(
        "SELECT count(*) FROM games WHERE season = ? AND home_pts > 0", [season]
    ).fetchone()
    pred_games = con.execute(
        "SELECT count(DISTINCT game_id) FROM forward_predictions WHERE model_name = 'rung0_mov_elo'"
    ).fetchone()
    pred_pg = con.execute(
        "SELECT count(*) FROM (SELECT DISTINCT game_id, player_id FROM forward_predictions "
        "WHERE model_name = 'props_context_residual' AND target = 'pts')"
    ).fetchone()
    con.execute(f"ATTACH '{full_db}' AS fdb (READ_ONLY)")
    try:
        played = con.execute(
            "SELECT count(*), sum(minutes) FROM fdb.player_game_stats p "
            "JOIN fdb.games g USING (game_id) "
            "WHERE g.season = ? AND p.minutes > 0",
            [season],
        ).fetchone()
        covered = con.execute(
            "SELECT count(*), sum(p.minutes) FROM fdb.player_game_stats p "
            "JOIN fdb.games g USING (game_id) "
            "WHERE g.season = ? AND p.minutes > 0 AND EXISTS (SELECT 1 FROM forward_predictions f "
            "WHERE f.model_name = 'props_context_residual' AND f.target = 'pts' "
            "AND f.game_id = p.game_id AND f.player_id = p.player_id)",
            [season],
        ).fetchone()
    finally:
        con.execute("DETACH fdb")
    out["coverage"] = {
        "games_played": n_games[0] if n_games else None,
        "games_predicted": pred_games[0] if pred_games else None,
        "player_games_played": played[0] if played else None,
        "player_games_predicted_and_played": covered[0] if covered else None,
        "minutes_played": played[1] if played else None,
        "minutes_covered": covered[1] if covered else None,
        "player_games_predicted_total": pred_pg[0] if pred_pg else None,
    }
    return out


def plumbing_counts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Row counts only (no scores, no deltas): predictions and eligibility statuses per model."""
    pred = con.execute(
        "SELECT model_name, target, count(*) FROM forward_predictions GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    elig = con.execute(
        "SELECT model_name, status, count(*) FROM forward_scores_elig GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    fb = con.execute(
        "SELECT json_extract_string(prediction, '$.fallback_reason') r, count(*) FROM "
        "forward_predictions WHERE model_name = 'rung0_mov_elo' AND target = 'win_prob_home' "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    routed = con.execute(
        "SELECT json_extract_string(prediction, '$.routed_to') r, "
        "json_extract_string(prediction, '$.fallback_reason') f, count(*) FROM forward_predictions "
        "WHERE model_name = 'props_context_residual' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12"
    ).fetchall()
    return {
        "predictions": [list(r) for r in pred],
        "elig_status": [list(r) for r in elig],
        "win_fallback_reasons": [list(r) for r in fb],
        "props_routing": [list(r) for r in routed],
    }


def storage(cfg: ReplayConfig, con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    def mb(p: Path) -> float | None:
        return round(p.stat().st_size / 1e6, 1) if p.exists() else None

    n_pred = con.execute(
        "SELECT count(*), sum(length(prediction)) FROM forward_predictions"
    ).fetchone()
    return {
        "work_db_mb": mb(cfg.work_db),
        "full_copy_mb": mb(cfg.full_db),
        "forward_prediction_rows": n_pred[0] if n_pred else None,
        "forward_prediction_json_mb": round((n_pred[1] or 0) / 1e6, 1) if n_pred else None,
        "state_json_mb": mb(cfg.state_path),
        "paper_db_mb": mb(cfg.paper_db) if cfg.paper_db else None,
    }


def finalize(cfg: ReplayConfig) -> dict[str, Any]:
    """End-of-replay: checkpoint (descriptive, into the scratch dir), report, metrics. The
    checkpoint and report outputs contain shadow-arm comparisons and are NOT read here."""
    from nba.daily.checkpoint import evaluate_checkpoint
    from nba.daily.report import build_report

    state = ReplayState.load(cfg.state_path)
    out: dict[str, Any] = {"finalized_at": datetime.now(UTC).isoformat()}
    nolineups = cfg.out_dir / "no_lineups.duckdb"  # never the live collector
    try:
        # build_report creates its tables if missing, so it needs the (copy's) write handle
        wcon = connect_with_retry(cfg.work_db)
        try:
            text = build_report(wcon, cfg.season, lineups_db=nolineups)
        finally:
            wcon.close()
        (cfg.out_dir / "forward_report.md").write_text(text)
        out["report"] = {"wrote": str(cfg.out_dir / "forward_report.md"), "chars": len(text)}
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        out["report"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    con = connect_with_retry(cfg.work_db, read_only=True)
    try:
        now = datetime.combine(date(cfg.season + 1, 7, 1), dtime())
        try:
            snap = evaluate_checkpoint(
                con, "END", out_dir=cfg.out_dir / "checkpoints", lineups_db=nolineups,
                season=cfg.season, descriptive=True, now=now,
            )  # fmt: skip
            out["checkpoint_descriptive"] = {"wrote": str(snap["paths"]["json"])}
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            out["checkpoint_descriptive"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        out["descriptive_production_primary"] = descriptive_metrics(con, cfg.full_db, cfg.season)
        out["plumbing_counts"] = plumbing_counts(con)
        out["storage"] = storage(cfg, con)
    finally:
        con.close()
    ds = state.data["dates"]
    walls = [v.get("wall_s", 0.0) for v in ds.values()]
    fails: dict[str, int] = {}
    for v in ds.values():
        for f in v.get("failures", []):
            fails[f["step"].split("@")[0]] = fails.get(f["step"].split("@")[0], 0) + 1
    run_errs: dict[str, int] = {}
    for v in ds.values():
        for r in v.get("runs", []):
            for e in r.get("errors", []) + r.get("shadow_errors", []):
                run_errs[e[:80]] = run_errs.get(e[:80], 0) + 1
    out["ops"] = {
        "dates": len(ds),
        "dates_done": sum(1 for v in ds.values() if v["phase"] == "done"),
        "runs": sum(len(v.get("runs", [])) for v in ds.values()),
        "wall_total_s": round(sum(walls), 1),
        "wall_per_date_median_s": round(float(np.median(walls)), 2) if walls else None,
        "wall_per_date_max_s": round(max(walls), 2) if walls else None,
        "peak_rss_mb": max((c["peak_rss_mb"] for c in state.data["chunks"]), default=None),
        "chunks": len(state.data["chunks"]),
        "failures_by_step": fails,
        "run_errors": run_errs,
        "proxy_tip_games": sum(v.get("proxy_tip_games", 0) for v in ds.values()),
        "t30": {k: sum(1 for v in ds.values() if str(v.get("t30", "")).startswith(k))
                for k in ("skipped", "ran", "failed")},
    }  # fmt: skip
    (cfg.out_dir / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    return out


# ----------------------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.daily.replay_season", description=__doc__)
    p.add_argument("--season", type=int, required=True, help="start year, 2025 = 2025-26")
    p.add_argument("--db", type=Path, required=True, help="work COPY (created if missing)")
    p.add_argument("--source", type=Path, default=ROOT / "nba.duckdb")
    p.add_argument(
        "--full", type=Path, default=None, help="untouched full copy (default <db>_full)"
    )
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument(
        "--kalshi-db",
        type=Path,
        default=None,
        help="Kalshi master copy (e.g. data/rehearsal/kalshi_2025.duckdb); a results-hidden "
        "working copy is made under the out dir",
    )
    p.add_argument("--lineups-db", type=Path, default=ROOT / "data" / "lineups" / "lineups.duckdb")
    p.add_argument("--chunk-size", type=int, default=20)
    p.add_argument("--max-dates", type=int, default=None, help="stop after N dates in total")
    p.add_argument("--start-date", type=date.fromisoformat, default=None)
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    p.add_argument("--worker", action="store_true", help="internal: run one chunk, no lock")
    p.add_argument("--finalize-only", action="store_true")
    p.add_argument("--parlay-summary", action="store_true", help="print the shadow-log summary")
    p.add_argument("--parlay-runs", choices=["first", "all"], default="all")
    p.add_argument(
        "--parlay-pass",
        action="store_true",
        help="after a finished replay: run the parlay shadow date by date (needs --kalshi-db)",
    )
    p.add_argument("--no-lock", action="store_true", help="skip data/ops/heavy.lock (tests)")
    p.add_argument("--no-props", action="store_true", help="tests only")
    return p


def _config(a: argparse.Namespace) -> ReplayConfig:
    out = a.out_dir or a.db.with_suffix("")
    return ReplayConfig(
        season=a.season,
        work_db=a.db,
        full_db=a.full or a.db.with_name(a.db.stem + "_full.duckdb"),
        out_dir=out,
        model_cache=out / "model_cache",
        kalshi_db=(out / "kalshi_replay.duckdb") if a.kalshi_db else None,
        lineups_db=a.lineups_db,
        paper_db=(out / "paper_trades.duckdb") if a.kalshi_db else None,
        with_props=not a.no_props,
    )


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    cfg = _config(a)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    if a.worker:
        # polars join_asof emits this benign warning on every run (3 sites); keep the log readable
        warnings.filterwarnings("ignore", message="Sortedness of columns cannot be checked")
        remaining = run_chunk(cfg, a.chunk_size, start=a.start_date, end=a.end_date)
        return EXIT_ALL_DONE if remaining == 0 else 0
    state = ReplayState.load(cfg.state_path)
    if not cfg.work_db.exists():
        state.data["init"] = init_replay_db(a.source, cfg)
        state.save()
        print(f"initialised {cfg.work_db}: {state.data['init']['first_date']}", flush=True)
    if a.kalshi_db and cfg.kalshi_db and not cfg.kalshi_db.exists():
        state.data["init"]["kalshi"] = make_hidden_kalshi(a.kalshi_db, cfg.kalshi_db)
        state.save()
    if a.parlay_pass:
        if cfg.kalshi_db is None:
            raise SystemExit("--parlay-pass needs --kalshi-db")
        print(parlay_pass(cfg, a.parlay_runs))
        return 0
    if a.parlay_summary:
        if cfg.kalshi_db is None:
            raise SystemExit("--parlay-summary needs --kalshi-db")
        res = parlay_summary(cfg)
        (cfg.out_dir / "parlay_summary.json").write_text(json.dumps(res, indent=1, default=str))
        print(json.dumps(res, indent=1, default=str))
        return 0
    if a.finalize_only:
        print(json.dumps(finalize(cfg)["ops"], indent=1))
        return 0
    done_total = 0
    while True:
        n = a.chunk_size if a.max_dates is None else min(a.chunk_size, a.max_dates - done_total)
        if n <= 0:
            break
        if not a.no_lock:
            acquire_heavy_lock()
        try:
            cmd = [sys.executable, "-m", "nba.daily.replay_season", "--season", str(a.season),
                   "--db", str(a.db), "--full", str(cfg.full_db), "--out-dir", str(cfg.out_dir),
                   "--lineups-db", str(a.lineups_db), "--chunk-size", str(n),
                   "--worker"]  # fmt: skip
            if a.kalshi_db:
                cmd += ["--kalshi-db", str(a.kalshi_db)]
            if a.start_date:
                cmd += ["--start-date", a.start_date.isoformat()]
            if a.end_date:
                cmd += ["--end-date", a.end_date.isoformat()]
            if a.no_props:
                cmd.append("--no-props")
            rc = subprocess.run(cmd, cwd=ROOT, check=False).returncode  # noqa: S603
        finally:
            if not a.no_lock:
                release_heavy_lock()
        done_total += n
        if rc == EXIT_ALL_DONE:
            break
        if rc != 0:
            print(f"worker failed rc={rc}; state is saved, rerun to resume", file=sys.stderr)
            return rc
        time.sleep(5)  # let other jobs take the lock between chunks
    if a.max_dates is None:
        print(json.dumps(finalize(cfg)["ops"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
