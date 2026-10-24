"""CLI for the T510 / T510b scorer. Not in the nightly path; read-only; no network.

    python -m research.line_move verify
    python -m research.line_move dryrun [--draws 1000] [--planted-draws 100] [--record PATH]
    python -m research.line_move preflight --date 2026-10-21 --nba-db <COPY> [--aliases PATH]
    python -m research.line_move score --rule T510|T510b --nba-db <COPY>

``preflight`` prints counts and selection totals only: it never forms a movement. ``score`` forms
the
movement statistics only once the rule's horizon (>= 300 part-1 qualifying markets AND >= 14 dates)
is
reached, only after a passing dryrun record for the current code exists, and T510b only after T510.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date
from pathlib import Path

from research.line_move import analysis, dryrun, loaders, selection, spec

REPO = spec.REPO
DEFAULT_RECORD = REPO / "data" / "line_move" / "dryrun_record.json"


def _capture_dirs(root: Path) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(root / "data" / "raw" / "live_*")))]


def _load(
    nba_db: Path, aliases: Path, dates: set[date] | None, root: Path
) -> tuple[list[selection.PSG], dict[str, int]]:
    fc = loaders.load_forecasts(nba_db)
    nm = loaders.load_name_map({k[0] for k in fc}, aliases)
    want = dates if dates is not None else {k[2] for k in fc if k[2] >= spec.FIRST_DATE}
    psgs, drops = loaders.build_psgs(loaders.load_snapshots(_capture_dirs(root)), fc, nm, want)
    return psgs, dict(drops)


def cmd_verify(_: argparse.Namespace) -> int:
    for doc, h in spec.verify_texts().items():
        print(f"{doc}: sha256 {h[:8]} ok")
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
    print(f"aliases file: {ns.aliases}")
    print(f"load drops: {d0}")
    print(
        f"selection drops: {dict(log.drops)}; psg seen {log.psg_seen}, "
        f"with an S0 rung {log.psg_with_s0_rung}"
    )
    print(f"counts: {got}")
    if d == spec.GATE_NIGHT:
        ok = all(got[k] == v for k, v in spec.GATE_21.items())
        print(f"gate counts (10-21): {spec.GATE_21}")
        print(f"match gate: {ok}")
        return 0 if ok else 1
    return 0


def _fmt_part(name: str, p: analysis.PartResult) -> list[str]:
    out = [f"  {name}: n={p.n} dates={p.n_dates} mean={p.mean:+.4f} (floor {p.floor})"]
    for lv, (lo, hi) in p.pct.items():
        out.append(
            f"    {100 * lv:.1f}% percentile CI [{lo:+.4f}, {hi:+.4f}]  "
            f"t lower {p.t_lower[lv]:+.4f}  "
            f"clears: {p.passed[lv]}"
        )
    return out


def cmd_score(ns: argparse.Namespace) -> int:
    rule = ns.rule
    dryrun.require_pass(Path(ns.record), rule)
    psgs, d0 = _load(Path(ns.nba_db), Path(ns.aliases), None, REPO)
    sel, log = selection.select_all(psgs)
    sel = [s for s in sel if s.day >= spec.FIRST_DATE and s.day != spec.GATE_NIGHT]
    own = analysis.horizon_date(sel, rule)
    t510 = analysis.horizon_date(sel, "T510")
    n_q = analysis.qualifying_counts(sel, rule)[0]
    ndates = len({s.day for s in sel})
    print(
        f"{rule}: part-1 qualifying {n_q}, scored dates {ndates}; "
        f"drops {dict(log.drops)}; load drops {d0}"
    )
    end = analysis.window_end(rule, own, t510)
    if end is None:
        print("HORIZON NOT REACHED: no movement statistic is formed (scored once, at the horizon).")
        return 3
    window = {s.day for s in sel if s.day <= end}
    rows = [s for s in sel if s.day in window]
    table = analysis.build_table(rows, rule)
    res = analysis.evaluate(table)
    print(
        f"{rule} scored on window {min(window)}..{end} ({len(window)} dates), verdict {res.verdict}"
    )
    print(f"decision level {res.level}; passes at 95% on these markets: {res.passes_at_95}")
    for line in _fmt_part("part 1 (m_M)", res.part1) + _fmt_part("part 2 (m_M - m_B)", res.part2):
        print(line)
    print("REPORTED (not decisive)")
    for k, v in analysis.reported(table).items():
        print(f"  {k}: {v}")
    if rule == "T510b":
        print(f"  three sets (n, mean m_M, mean m_M'): {analysis.three_sets(sel, window)}")
    print(
        "  drops by reason/sign: "
        + str({f"{k[0]}|{k[1]}": dict(v) for k, v in log.drop_signs.items()})
    )
    print(f"  drops with p_play < 0.9: {dict(log.drop_pplay_lt_09)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research.line_move")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    d = sub.add_parser("dryrun")
    d.add_argument("--draws", type=int, default=spec.DRYRUN_DRAWS)
    d.add_argument("--planted-draws", type=int, default=100)
    d.add_argument("--no-stress", action="store_true")
    d.add_argument("--record", default=str(DEFAULT_RECORD))
    d.set_defaults(fn=cmd_dryrun)
    for name, fn in (("preflight", cmd_preflight), ("score", cmd_score)):
        p = sub.add_parser(name)
        p.add_argument("--nba-db", required=True, help="a COPY of nba.duckdb")
        p.add_argument("--aliases", default=str(REPO / "configs" / "kalshi_aliases.yaml"))
        p.add_argument("--record", default=str(DEFAULT_RECORD))
        if name == "preflight":
            p.add_argument("--date", required=True)
        else:
            p.add_argument("--rule", required=True, choices=["T510", "T510b"])
        p.set_defaults(fn=fn)
    ns = ap.parse_args(argv)
    try:
        return int(ns.fn(ns))
    except (spec.FrozenTextChanged, dryrun.DryrunMissing) as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
