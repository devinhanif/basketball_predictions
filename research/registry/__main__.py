"""Research CLI: ``python -m research.registry route {show,build,validate,promote} ...``.

The routing table (docs/ROUTING.md, docs/ROUTER_V2.md; T028-T033) reached its verdict: drafts
only, no active route. The ``route`` subcommand moved here from ``python -m nba.registry`` on
2026-10-10 (Wave A of docs/DESIGN_RESTRUCTURE.md) so the live registry CLI no longer imports the
stack. Same flags, same behaviour; ``--db-path`` / ``--store-dir`` are shared with the live CLI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from nba.db.connect import DEFAULT_DB_PATH
from nba.registry.cli_guard import validate_cli_flags
from nba.registry.factory import DEFAULT_STORE_DIR
from nba.registry.local import LocalRegistry
from nba.registry.ops import open_readonly
from research.registry.routing import (
    DEFAULT_DRAFT_DIR,
    DEFAULT_LEDGER,
    ROUTE_FILE,
    fetch_routes,
    format_table,
    promote_route,
    read_spec,
    register_spec,
    route_model_name,
    validate_spec,
    write_spec,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m research.registry")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--store-dir", default=str(DEFAULT_STORE_DIR))
    sub = p.add_subparsers(dest="cmd", required=True)
    _route_parser(sub)
    return p


def _route_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    rt = sub.add_parser("route", help="routing table: which model predicts each target")
    rsub = rt.add_subparsers(dest="route_cmd", required=True)
    drafts = str(DEFAULT_DRAFT_DIR)

    sh = rsub.add_parser("show", help="routing table (registered routes + drafts); read-only")
    sh.add_argument("--target")
    sh.add_argument("--drafts-dir", default=drafts)

    bd = rsub.add_parser("build", help="build specs from the OOF store (2023-24 evidence only)")
    bd.add_argument("--oof", default=None, help="OOF store (default data/stack/oof.duckdb)")
    bd.add_argument("--meta", default="data/colab/seq_props/seq_props_meta.parquet")
    bd.add_argument("--out-dir", default=drafts)
    bd.add_argument("--stats", nargs="+", default=["pts", "reb", "ast", "fg3m"])
    bd.add_argument("--sets", nargs="+", default=["full", "ext"])
    bd.add_argument("--no-thresholds", action="store_true")
    bd.add_argument("--archetypes", action="store_true", help="add archetype/position features")
    bd.add_argument("--candidates-file", default=None, help="JSON adding candidates / sets")
    bd.add_argument(
        "--parent-dir", default=None, help="earlier drafts dir; links parent_route_hash"
    )
    bd.add_argument("--n-boot", type=int, default=1000)
    bd.add_argument("--seed", type=int, default=0)
    bd.add_argument("--register", action="store_true", help="WRITE: log specs as candidates")

    vl = rsub.add_parser("validate", help="structural / leakage / ledger checks on route specs")
    vl.add_argument("paths", nargs="*", help="spec files or dirs (default: drafts dir)")
    vl.add_argument("--drafts-dir", default=drafts)
    vl.add_argument("--oof", default=None, help="also check OOF rows exist and exclude 2025")
    vl.add_argument("--ledger", default=str(DEFAULT_LEDGER))

    rp = rsub.add_parser("promote", help="explicit promotion of a registered route")
    rp.add_argument("target")
    rp.add_argument("version")
    rp.add_argument("--verdict", default=None, help="holdout verdict JSON (routers only)")
    rp.add_argument("--scenario", action="store_true")


def _spec_files(paths: list[str], drafts_dir: str) -> list[Path]:
    roots = [Path(p) for p in paths] or [Path(drafts_dir)]
    out: list[Path] = []
    for r in roots:
        if r.is_file():
            out.append(r)
        else:
            out.extend(sorted(r.rglob(ROUTE_FILE)))
    return out


def _route(args: argparse.Namespace) -> int:
    rc: str = args.route_cmd
    if rc == "promote":
        validate_cli_flags(promote=True, scenario=args.scenario)
        con, reg = _write_registry(args)
        try:
            verdict = Path(args.verdict) if args.verdict else None
            print(promote_route(reg, con, args.target, args.version, verdict_path=verdict))
        finally:
            con.close()
        return 0

    if rc == "build":
        return _route_build(args)

    if rc == "validate":
        ledger = Path(args.ledger)
        files = _spec_files(args.paths, args.drafts_dir)
        if not files:
            print("no route specs found")
            return 1
        bad = 0
        for f in files:
            errs = validate_spec(read_spec(f), ledger=ledger, oof_path=args.oof)
            tag = "OK  " if not errs else "FAIL"
            print(f"{tag} {f}")
            for e in errs:
                print(f"     - {e}")
            bad += bool(errs)
        return 1 if bad else 0

    # show
    items: list[tuple[str, dict[str, object]]] = []
    if Path(args.db_path).exists():
        con = open_readonly(args.db_path)
        try:
            for r in fetch_routes(con, args.target):
                items.append((f"registered {r.version} {r.stage}", r.spec()))
        finally:
            con.close()
    for f in _spec_files([], args.drafts_dir):
        d = read_spec(f)
        if args.target in (None, d["target"]):
            items.append(("draft", d))
    print(format_table(items) if items else "no routes (run: route build)")
    return 0


def _route_build(args: argparse.Namespace) -> int:
    from research.registry.routing import load_extra_candidates
    from research.stack.routebuild import build_specs, joint_parlay_spec, prepare_inputs

    if args.candidates_file:
        load_extra_candidates(Path(args.candidates_file))

    arch = None
    if args.archetypes:
        from research.stack.populate import archetype_position_table

        arch = archetype_position_table(str(args.db_path), [2023, 2024])
    inp = prepare_inputs(str(args.db_path), args.meta, oof_path=args.oof, arch_table=arch)
    specs = build_specs(
        inp,
        oof_path=args.oof,
        stats=tuple(args.stats),
        sets=tuple(args.sets),
        thresholds=not args.no_thresholds,
        n_boot=args.n_boot,
        progress=lambda m: print(f"  building {m}", flush=True),
    )
    specs.append(joint_parlay_spec())
    if args.parent_dir:
        for sp in specs:
            pf = Path(args.parent_dir) / f"{sp.target}_{sp.cset}" / ROUTE_FILE
            if pf.exists():
                sp.parent_route_hash = read_spec(pf)["route_hash"]
    out = Path(args.out_dir)
    for sp in specs:
        write_spec(sp, out / f"{sp.target}_{sp.cset}")
    print(format_table([("draft", sp.to_dict()) for sp in specs]))
    print(f"wrote {len(specs)} draft specs under {out}")
    if args.register:
        con, reg = _write_registry(args)
        try:
            for sp in specs:
                v = register_spec(reg, sp, seed=args.seed, work_dir=out / "_register")
                print(f"registered {route_model_name(sp.target)} {v} (candidate)")
        finally:
            con.close()
    return 0


def _write_registry(args: argparse.Namespace) -> tuple[duckdb.DuckDBPyConnection, LocalRegistry]:
    con = duckdb.connect(str(args.db_path))
    return con, LocalRegistry(con, Path(args.store_dir))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _route(args)
    except (LookupError, PermissionError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
