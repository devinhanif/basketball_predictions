"""CLI for the T510 / T510b scorer. Not in the nightly path; read-only; no network.

    python -m research.line_move verify
    python -m research.line_move dryrun [--draws 1000] [--planted-draws 100] [--record PATH]
    python -m research.line_move preflight --date 2026-10-21 --nba-db <COPY> [--aliases PATH]
    python -m research.line_move score --rule T510|T510b --nba-db <COPY>
    python -m research.line_move family
    python -m research.line_move mark-unscoreable --rule T510|T510b --reason TEXT

``preflight`` prints counts and selection totals only: it never forms a movement. ``score`` forms
movement statistics only once the rule's horizon (>= 300 part-1 qualifying markets AND >= 14
complete dates) is reached, only after a passing dryrun record for the current code exists, only on
the pinned alias file, and writes its result record once (it refuses to overwrite). T510b needs
T510's record first.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from research.line_move import analysis, dryrun, loaders, selection, spec

REPO = spec.REPO
NY = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(UTC)


def _capture_dirs(root: Path) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(root / "data" / "raw" / "live_*")))]


def _load(
    nba_db: Path, aliases: Path, dates: set[date] | None, root: Path
) -> tuple[list[selection.PSG], dict[str, int]]:
    fc = loaders.load_forecasts(nba_db)
    nm = loaders.load_name_map({k[0] for k in fc}, aliases)
    want = dates if dates is not None else {k[2] for k in fc if k[2] >= spec.FIRST_DATE}
    dirs = _capture_dirs(root)
    psgs, drops = loaders.build_psgs(
        loaders.load_snapshots(dirs), fc, nm, want, loaders.load_file_times(dirs)
    )
    return psgs, dict(drops)


def _nba_api_version() -> str:
    try:
        return version("nba_api")
    except Exception:
        return "unknown"


def _aliases_sha(path: Path) -> str:
    return spec.sha256_file(path)


class Refused(RuntimeError):
    pass


def _check_pinned_aliases(path: Path) -> str:
    got = _aliases_sha(path)
    if got != spec.ALIASES_SHA256:
        raise Refused(
            f"alias file {path} sha256 {got[:8]} != pinned {spec.ALIASES_SHA256[:8]}; "
            "the rule is scored on the pinned file only"
        )
    return got


def cmd_verify(ns: argparse.Namespace) -> int:
    for doc, h in spec.verify_texts().items():
        print(f"{doc}: sha256 {h[:8]} ok")
    cfg = _aliases_sha(REPO / "configs" / "kalshi_aliases.yaml")
    pin = "matches" if cfg == spec.ALIASES_SHA256 else "DIFFERS from"
    print(f"configs/kalshi_aliases.yaml sha256 {cfg[:8]} {pin} the pin {spec.ALIASES_SHA256[:8]}")
    print(f"nba_api {_nba_api_version()}; code sha256 {spec.code_sha256()[:8]}")
    return 0


def cmd_dryrun(ns: argparse.Namespace) -> int:
    rec = dryrun.run_dryrun(ns.draws, ns.planted_draws, stress=not ns.no_stress)
    path = Path(ns.record)
    path.parent.mkdir(parents=True, exist_ok=True)
    dryrun.write_record(rec, path)
    print(json.dumps(rec, indent=2))
    print(f"record written to {path}")
    return 0 if rec["T510_dryrun_pass"] and rec["T510b_dryrun_pass"] else 1


def cmd_preflight(ns: argparse.Namespace) -> int:
    dryrun.require_pass(Path(ns.record), "T510")
    d = date.fromisoformat(ns.date)
    asha = _aliases_sha(Path(ns.aliases))
    psgs, d0 = _load(Path(ns.nba_db), Path(ns.aliases), {d}, REPO)
    sel, log = selection.select_all(psgs)
    a1, a2, f0 = analysis.qualifying_counts(sel, "T510")
    b1, b2, f1 = analysis.qualifying_counts(sel, "T510b")
    both = int((f0 & f1).sum())
    flips = sum(
        1
        for s, x, y in zip(sel, f0, f1, strict=True)
        if x
        and y
        and (selection.valuation("T510", s.pc_m, s.play_m, s.mid0) - s.mid0)
        * (selection.valuation("T510b", s.pc_m, s.play_m, s.mid0) - s.mid0)
        < 0
    )
    got = {
        "markets_scored": len(sel),
        "p1_T510": a1,
        "p1_T510b": b1,
        "p1_both": both,
        "only_T510": a1 - both,
        "only_T510b": b1 - both,
        "sign_flips": flips,
        "p2_T510": a2,
        "p2_T510b": b2,
        "pplay_lt_0.9_scored": sum(s.play_m < 0.9 for s in sel),
    }
    print(f"PREFLIGHT {d} (counts and selection totals only; no movement formed)")
    print(f"aliases file: {ns.aliases} sha256 {asha[:8]}; nba_api {_nba_api_version()}")
    print(f"load drops: {d0}")
    print(
        f"selection drops: {dict(log.drops)}; psg seen {log.psg_seen}, "
        f"with an S0 rung {log.psg_with_s0_rung}"
    )
    print(f"counts: {got}")
    if d == spec.GATE_NIGHT:
        if asha == spec.ALIASES_SHA256_FREEZE:
            want, label = spec.GATE_21, "freeze-time aliases"
        elif asha == spec.ALIASES_SHA256:
            want, label = spec.GATE_21_PINNED, "pinned aliases"
        else:
            print("alias file matches neither pin; no comparison made")
            return 1
        ok = got == want
        print(f"expected ({label}): {want}")
        print(f"match: {ok}")
        return 0 if ok else 1
    return 0


def _fmt_part(name: str, p: analysis.PartResult) -> list[str]:
    out = [f"  {name}: n={p.n} dates={p.n_dates} mean={p.mean:+.4f} (floor {p.floor})"]
    for lv, (lo, hi) in p.pct.items():
        out.append(
            f"    {100 * lv:.1f}% percentile CI [{lo:+.4f}, {hi:+.4f}]  "
            f"t lower {p.t_lower[lv]:+.4f}  clears: {p.passed[lv]}"
        )
    return out


def _diagnostics(table: analysis.Table) -> list[str]:
    """Printed right under the verdict (adversary F5). They decide nothing."""
    out = ["DIAGNOSTICS (information vs a common drift times the model's lean; decide nothing)"]
    for part, (obs, pm, lo, hi) in analysis.placebo(table).items():
        out.append(
            f"  within-date placebo {part}: observed {obs:+.4f}; placebo mean {pm:+.4f} "
            f"[{lo:+.4f}, {hi:+.4f}]"
        )
    for part, groups in analysis.by_sign(table).items():
        for sg, (n, mean, lo, hi) in groups.items():
            out.append(
                f"  by sign {part} s={sg:+d}: n={n} mean={mean:+.4f} 95% date-resampled CI "
                f"[{lo:+.4f}, {hi:+.4f}]"
            )
    return out


def _result_path(out_dir: Path, rule: str) -> Path:
    return out_dir / f"{rule}_result.json"


def _write_once(path: Path, rec: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as fh:  # refuses to overwrite: a rule is scored once
            json.dump(rec, fh, indent=2, default=str)
    except FileExistsError as e:
        raise Refused(
            f"{path} exists: a rule is scored once; the result record is never rewritten"
        ) from e


def _part_json(p: analysis.PartResult) -> dict[str, object]:
    return {
        "n": p.n,
        "n_dates": p.n_dates,
        "mean": p.mean,
        "floor": p.floor,
        "percentile_bounds": {str(k): list(v) for k, v in p.pct.items()},
        "t_lower": {str(k): v for k, v in p.t_lower.items()},
        "passed": {str(k): v for k, v in p.passed.items()},
    }


def cmd_score(ns: argparse.Namespace) -> int:
    rule = ns.rule
    out_dir = Path(ns.out_dir)
    dryrun.require_pass(Path(ns.record), rule)
    asha = _check_pinned_aliases(Path(ns.aliases))
    if _result_path(out_dir, rule).exists():
        raise Refused(f"{_result_path(out_dir, rule)} exists: a rule is scored once")
    if rule == "T510b":
        p510 = _result_path(out_dir, "T510")
        if not p510.exists():
            raise Refused("T510b is never scored before T510: no T510_result.json")
        if json.loads(p510.read_text())["verdict"] == "UNSCOREABLE":
            raise Refused("T510 is unscoreable; T510b is not scored")
    psgs, d0 = _load(Path(ns.nba_db), Path(ns.aliases), None, REPO)
    sel, log = selection.select_all(psgs)
    now = _now()
    now_s = int(now.timestamp())
    today = now.astimezone(NY).date()
    last_tip: dict[date, int] = {}
    for p in psgs:
        last_tip[p.day] = max(last_tip.get(p.day, 0), p.tip_s)
    ok_days = analysis.complete_days(last_tip, now_s, today)
    sel = [s for s in sel if s.day >= spec.FIRST_DATE and s.day != spec.GATE_NIGHT]
    n_incomplete = len({s.day for s in sel} - ok_days)
    sel = [s for s in sel if s.day in ok_days]
    own = analysis.horizon_date(sel, rule)
    t510 = analysis.horizon_date(sel, "T510")
    n_q = analysis.qualifying_counts(sel, rule)[0]
    print(
        f"{rule}: part-1 qualifying {n_q}, complete dates {len({s.day for s in sel})} "
        f"({n_incomplete} incomplete dates excluded); drops {dict(log.drops)}; load drops {d0}"
    )
    end = analysis.window_end(rule, own, t510)
    if end is None:
        print("HORIZON NOT REACHED: no movement statistic is formed (scored once, at the horizon).")
        return 3
    window = {s.day for s in sel if s.day <= end}
    analysis.assert_window_complete(window, last_tip, now_s)
    rows = [s for s in sel if s.day in window]
    table = analysis.build_table(rows, rule)
    res = analysis.evaluate(table)
    print(
        f"{rule} scored on window {min(window)}..{end} ({len(window)} dates), verdict {res.verdict}"
    )
    for line in _diagnostics(table):
        print(line)
    print(f"decision level {res.level}; passes at 95% on these markets: {res.passes_at_95}")
    for line in _fmt_part("part 1 (m_M)", res.part1) + _fmt_part("part 2 (m_M - m_B)", res.part2):
        print(line)
    print("REPORTED (not decisive)")
    for k, v in analysis.reported(table).items():
        print(f"  {k}: {v}")
    dirs = _capture_dirs(REPO)
    texts = loaders.rules_secondary_texts(dirs, {s.ticker for s in rows})
    print(
        f"  Kalshi rules_secondary over the window's tickers (stat masked): {len(texts)} distinct"
    )
    for txt, n in texts.items():
        print(f"    [{n} tickers] {txt!r}")
    if rule == "T510b":
        print(f"  three sets (n, mean m_M, mean m_M'): {analysis.three_sets(sel, window)}")
        inactive = loaders.inactive_pids(Path(ns.nba_db), {(s.game_id, s.pid) for s in rows})
        n_in, mc = analysis.inactive_mid_close(table, inactive)
        print(f"  part-1 markets of later-inactive players: {n_in}; mean mid_close {mc:+.4f}")
    print(
        "  drops by reason/sign: "
        + str({f"{k[0]}|{k[1]}": dict(v) for k, v in log.drop_signs.items()})
    )
    print(f"  drops with p_play < 0.9: {dict(log.drop_pplay_lt_09)}")
    rec: dict[str, object] = {
        "rule": rule,
        "verdict": res.verdict,
        "decision_level": res.level,
        "passes_at_95": res.passes_at_95,
        "window_first": min(window),
        "window_last": end,
        "n_dates": len(window),
        "part1": _part_json(res.part1),
        "part2": _part_json(res.part2),
        "code_sha256": spec.code_sha256(),
        "code_files": [p.name for p in spec.code_files()],
        "aliases_sha256": asha,
        "nba_api": _nba_api_version(),
        "text_sha256": spec.verify_texts(),
        "db": loaders.db_stamp(Path(ns.nba_db)),
        "capture_files": loaders.capture_file_count(dirs),
        "scored_at": now.isoformat(),
    }
    _write_once(_result_path(out_dir, rule), rec)
    print(f"result record written once to {_result_path(out_dir, rule)}")
    return 0


def cmd_mark_unscoreable(ns: argparse.Namespace) -> int:
    rec: dict[str, object] = {
        "rule": ns.rule,
        "verdict": "UNSCOREABLE",
        "reason": ns.reason,
        "code_sha256": spec.code_sha256(),
        "marked_at": _now().isoformat(),
    }
    _write_once(_result_path(Path(ns.out_dir), ns.rule), rec)
    print(f"{ns.rule} marked unscoreable")
    return 0


def cmd_family(ns: argparse.Namespace) -> int:
    out_dir = Path(ns.out_dir)
    p510, p510b = _result_path(out_dir, "T510"), _result_path(out_dir, "T510b")
    if not p510.exists():
        print("T510 has no result record yet; the family is not read")
        return 3
    a = json.loads(p510.read_text())
    if not p510b.exists():
        if a["verdict"] == "UNSCOREABLE":
            print(analysis.family_reading("UNSCOREABLE", "UNSCOREABLE", False))
            return 0
        print("T510b has no result record yet; the family is not read")
        return 3
    b = json.loads(p510b.read_text())
    reading = analysis.family_reading(
        a["verdict"], b["verdict"], bool(b.get("passes_at_95", False))
    )
    print(f"T510: {a['verdict']}; T510b: {b['verdict']} (passes at 95%: {b.get('passes_at_95')})")
    print(reading)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research.line_move")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    d = sub.add_parser("dryrun")
    d.add_argument("--draws", type=int, default=spec.DRYRUN_DRAWS)
    d.add_argument("--planted-draws", type=int, default=100)
    d.add_argument("--no-stress", action="store_true")
    d.add_argument("--record", default=str(spec.DRYRUN_RECORD))
    d.set_defaults(fn=cmd_dryrun)
    for name, fn in (("preflight", cmd_preflight), ("score", cmd_score)):
        p = sub.add_parser(name)
        p.add_argument("--nba-db", required=True, help="a COPY of nba.duckdb")
        p.add_argument("--aliases", default=str(REPO / "configs" / "kalshi_aliases.yaml"))
        p.add_argument("--record", default=str(spec.DRYRUN_RECORD))
        if name == "preflight":
            p.add_argument("--date", required=True)
        else:
            p.add_argument("--rule", required=True, choices=["T510", "T510b"])
            p.add_argument("--out-dir", default=str(spec.REPORT_DIR))
        p.set_defaults(fn=fn)
    f = sub.add_parser("family")
    f.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    f.set_defaults(fn=cmd_family)
    u = sub.add_parser("mark-unscoreable")
    u.add_argument("--rule", required=True, choices=["T510", "T510b"])
    u.add_argument("--reason", required=True)
    u.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    u.set_defaults(fn=cmd_mark_unscoreable)
    ns = ap.parse_args(argv)
    try:
        return int(ns.fn(ns))
    except (spec.FrozenTextChanged, dryrun.DryrunMissing, Refused, analysis.IncompleteWindow) as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
