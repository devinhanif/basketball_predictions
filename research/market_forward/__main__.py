"""CLI for the LONGSHOT / UNDERS forward scorer. Not in the nightly path; no launchd job.

    python -m research.market_forward verify
    python -m research.market_forward dryrun [--games 400] [--replicates 60]
    python -m research.market_forward preflight --rule UNDERS_FORWARD --db <COPY of kalshi.duckdb>
    python -m research.market_forward read --rule UNDERS_FORWARD --look 1 --db <COPY>

``preflight`` runs the pipeline end to end on pre-window games (ET 2026-10-20..24) and prints counts
and
pass/fail of the integrity checks only. ``read`` refuses to run before its look date, so no return
on a
real game inside either scoring window is ever computed ahead of the read.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl

from research.market_forward import data as D
from research.market_forward.engine import (
    LookResult,
    preflight,
    schedule_dates_from_tips,
    score_look,
)
from research.market_forward.fee import FeeRefused, PinnedFee, load_pinned_fee
from research.market_forward.report import format_look, format_preflight, look_json
from research.market_forward.score import GuardState, decide, make_frame, pooled
from research.market_forward.spec import (
    LOOK_ALPHA,
    LOOK_READ_DATE,
    RULES,
    SCALAR_LOOKAHEAD_DAYS,
    SETTLE_GAP_DAYS,
    WINDOW_START,
    RuleSpec,
    verify_frozen,
)
from research.market_forward.strata import StrataInputs, load_strata_inputs, map_game_ids
from research.market_forward.synth import (
    draw_outcomes,
    make_world,
    plant_scale,
    world_universe,
)

REPO = Path(__file__).resolve().parents[2]
SCHEDULE = REPO / "data" / "raw" / "schedule_2026-10-23" / "live_tips_2026-27.parquet"
RAW_ROOT = REPO / "data" / "kalshi" / "raw"
#: Pre-window dates the preflight is allowed to touch (outside both scoring windows).
PREFLIGHT_FIRST = date(2026, 10, 20)
PREFLIGHT_LAST = date(2026, 10, 24)


def settled_by_for(read_date: date) -> datetime:
    """C1: 23:59:59 America/New_York on the read date, as naive UTC (the DB's convention)."""
    et = datetime.combine(read_date, time(23, 59, 59), tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(UTC).replace(tzinfo=None)


def _days(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def build_universe(
    spec: RuleSpec,
    db: Path,
    raw_root: Path,
    schedule: Path,
    window_start: date,
    cutoff: date,
    raw_last: date,
    settled_by: datetime | None = None,
    schedule_sha256: str | None = None,
) -> tuple[D.Universe, set[datetime]]:
    con = D.open_copy(db)
    try:
        markets = D.read_markets(con, spec.all_series)
        raw = D.load_raw(
            raw_root, _days(window_start - timedelta(days=1), raw_last), spec.all_series
        )
        have = set(raw.occurrence)
        if any(m.event not in have for m in markets):
            D.load_raw(
                raw_root,
                _days(window_start - timedelta(days=1), raw_last),
                spec.all_series,
                kinds=("open",),
                info=raw,
            )
        tips = D.load_schedule_tips(schedule, schedule_sha256)
        uni = D.assemble(
            spec,
            markets,
            raw,
            tips,
            D.duckdb_quotes(con),
            window_start=window_start,
            cutoff=cutoff,
            settled_by=settled_by,
        )
        uni.db_sha256 = D.file_sha256(db)
        if raw.first_day is not None and raw.last_day is not None:
            uni.raw_range = (raw.first_day, raw.last_day)
    finally:
        con.close()
    return uni, tips


def cmd_verify(_: argparse.Namespace) -> int:
    for spec in RULES.values():
        got = verify_frozen(spec)
        fee = load_pinned_fee(spec.all_series)
        print(f"{spec.rule_id}: frozen sha256 {got[:8]} ok; {fee.describe()}")
    return 0


def cmd_preflight(ns: argparse.Namespace) -> int:
    spec = RULES[ns.rule]
    verify_frozen(spec)
    fee = load_pinned_fee(spec.all_series)
    uni, _ = build_universe(
        spec,
        Path(ns.db),
        Path(ns.raw_root),
        Path(ns.schedule),
        PREFLIGHT_FIRST,
        PREFLIGHT_LAST,
        date.today() + timedelta(days=1),
    )
    if uni.df.height and max(uni.df["date"].to_list()) >= WINDOW_START:
        raise SystemExit("preflight scored a game inside the scoring window; refusing")
    pf = preflight(spec, uni, fee, selftest_k=ns.k, selftest_b=ns.b)
    print(format_preflight(spec.rule_id, pf, fee.describe(), {**uni.game_counts, **uni.counts}))
    return 0 if pf.selftest_ok and pf.identity_failures == 0 else 1


def cmd_read(ns: argparse.Namespace) -> int:
    spec = RULES[ns.rule]
    verify_frozen(spec)
    read_date = LOOK_READ_DATE[ns.look]

    today = datetime.now(ZoneInfo("America/New_York")).date()
    if today < read_date:
        raise SystemExit(
            f"look {ns.look} opens {read_date}; today is {today}. A look is never moved earlier."
        )
    cutoff = read_date - timedelta(days=SETTLE_GAP_DAYS)
    fee = load_pinned_fee(spec.all_series)
    uni, tips = build_universe(
        spec,
        Path(ns.db),
        Path(ns.raw_root),
        Path(ns.schedule),
        WINDOW_START,
        cutoff,
        # C1: raw pages only to read date + 7, and only for scalar VALUES (results are cut at the
        # read date inside assemble).
        read_date + timedelta(days=SCALAR_LOOKAHEAD_DAYS),
        settled_by=settled_by_for(read_date),
    )
    strata: StrataInputs | None = None
    if spec.rule_id == "UNDERS_FORWARD" and ns.nba_db:
        ncon = duckdb.connect(ns.nba_db, read_only=True)
        acon = duckdb.connect(ns.asof_db, read_only=True) if ns.asof_db else None
        try:
            keys = sorted(set(uni.df["game"].to_list()))
            strata = load_strata_inputs(ncon, acon, map_game_ids(ncon, keys))
        finally:
            ncon.close()
            if acon is not None:
                acon.close()
    res = score_look(
        spec,
        uni,
        fee,
        ns.look,
        strata=strata,
        tips=dict(uni.tips),
        schedule_dates=schedule_dates_from_tips(tips),
        window_start=WINDOW_START,
        cutoff=cutoff,
    )
    print(format_look(res))
    if ns.json:
        Path(ns.json).write_text(look_json(res))
    return 0


def _replicates(
    spec: RuleSpec,
    fee: PinnedFee,
    world: pl.DataFrame,
    scale: float,
    n_rep: int,
    look: int,
    b: int,
    seed: int,
) -> dict[str, float]:
    """Verdict frequencies over fresh outcome draws (no self-test; the true E[r_no] is known)."""
    from collections import Counter

    alpha = LOOK_ALPHA[look]
    rng = np.random.default_rng(seed)
    base = make_frame(world.filter(world["in_pool"]), fee)
    mid = base.mid
    truth = float(
        ((1.0 - np.minimum(1.0, scale * mid)) - base.p_no_u / 1e6 - base.fee_no_u / 1e6).mean()
    )
    v: Counter[str] = Counter()
    lo_excl = 0
    hi_excl = 0
    pts = []
    for _ in range(n_rep):
        w = draw_outcomes(world, rng, scale=scale)
        fr = make_frame(w.filter(w["in_pool"]), fee)
        p = pooled(fr, alpha, b, rng)
        pts.append(p.R)
        lo_excl += p.decision.lo > truth
        hi_excl += p.decision.hi < truth
        gs = GuardState(1.0, True, [], 0.0, True, {}, True, True)
        v[decide(look, fr.n_games, fr.n_dates, p.R, p.decision.lo, p.decision.hi, gs).name] += 1
    out = {k: x / n_rep for k, x in v.items()}
    out.update(
        truth=truth,
        mean_point=float(np.mean(pts)),
        lo_excl=lo_excl / n_rep,
        hi_excl=hi_excl / n_rep,
    )
    return out


def cmd_dryrun(ns: argparse.Namespace) -> int:
    """Synthetic only: calibrated null, planted +3c, identity; full self-tests on each world."""
    for spec in RULES.values():
        verify_frozen(spec)
        fee = load_pinned_fee(spec.all_series)
        world = make_world(spec, ns.games, seed=11)
        k_plant = plant_scale(world, fee, 0.03)
        print("=" * 100)
        print(f"{spec.rule_id}: synthetic world {world.height} contracts, {ns.games} games")
        print(f"planted-edge scale k = {k_plant:.4f} (YES prob = k * mid gives E[r_no] = +3.00c)")
        for label, scale in (("CALIBRATED NULL (p = mid)", 1.0), ("PLANTED +3c", k_plant)):
            rng = np.random.default_rng(5)
            uni = world_universe(draw_outcomes(world, rng, scale=scale, dnp_rate=0.004))
            res: LookResult = score_look(
                spec, uni, fee, 1, b=ns.b, selftest_k=ns.k, selftest_b=ns.b
            )
            print("-" * 100)
            print(f"[{label}] single world, look 1, B={ns.b}, K={ns.k}")
            print(format_look(res))
        if ns.replicates:
            print("-" * 100)
            for label, scale in (("null", 1.0), ("planted +3c", k_plant)):
                r = _replicates(spec, fee, world, scale, ns.replicates, 1, ns.rep_b, 77)
                print(f"[replicates {label}] n={ns.replicates} B={ns.rep_b}: {r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research.market_forward")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    for name, fn in (("preflight", cmd_preflight), ("read", cmd_read)):
        p = sub.add_parser(name)
        p.add_argument("--rule", required=True, choices=sorted(RULES))
        p.add_argument("--db", required=True, help="a COPY of data/kalshi/kalshi.duckdb")
        p.add_argument("--raw-root", default=str(RAW_ROOT))
        p.add_argument("--schedule", default=str(SCHEDULE))
        if name == "read":
            p.add_argument("--look", type=int, required=True, choices=[1, 2])
            p.add_argument("--nba-db", default=None, help="a COPY of nba.duckdb (strata)")
            p.add_argument("--asof-db", default=None, help="market_asof.duckdb (strata)")
            p.add_argument("--json", default=None)
        else:
            p.add_argument("--k", type=int, default=200)
            p.add_argument("--b", type=int, default=10_000)
        p.set_defaults(fn=fn)
    d = sub.add_parser("dryrun")
    d.add_argument("--games", type=int, default=400)
    d.add_argument("--k", type=int, default=200)
    d.add_argument("--b", type=int, default=10_000)
    d.add_argument("--replicates", type=int, default=0)
    d.add_argument("--rep-b", type=int, default=1000)
    d.set_defaults(fn=cmd_dryrun)
    ns = ap.parse_args(argv)
    try:
        return int(ns.fn(ns))
    except FeeRefused as e:
        print(f"REFUSED (fee): {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
