"""Operator CLI: ``python -m nba.registry <command>``.

Read commands (``list``, ``show``, ``compare``, ``gate``) open the DB
read-only. Write commands (``promote``, ``import-artifacts``,
``bootstrap-production``) open a short-lived write connection; run them
when no eval is writing to the DB.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from nba.db.connect import DEFAULT_DB_PATH
from nba.registry.bootstrap import bootstrap_production
from nba.registry.cli_guard import validate_cli_flags
from nba.registry.factory import DEFAULT_STORE_DIR
from nba.registry.local import LocalRegistry
from nba.registry.model_gate import load_tolerances
from nba.registry.ops import (
    check_promotable,
    compare_versions,
    fetch_versions,
    format_list,
    gate_local,
    get_version,
    import_artifacts,
    open_readonly,
    parse_tags,
    promote_checked,
)
from nba.registry.routing import (
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

LOCAL_TOLERANCES = Path(__file__).resolve().parent / "model_gate_local_config.yaml"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.registry")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--store-dir", default=str(DEFAULT_STORE_DIR))
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="models x versions x stage x key metrics")
    ls.add_argument("--model")

    sh = sub.add_parser("show", help="one version in full")
    sh.add_argument("model")
    sh.add_argument("version", nargs="?")
    sh.add_argument("--alias", default=None, help="default: production when no version given")

    cp = sub.add_parser("compare", help="metric deltas (vB - vA)")
    cp.add_argument("model")
    cp.add_argument("va")
    cp.add_argument("vb")

    pr = sub.add_parser("promote", help="explicit, manual promotion to production")
    pr.add_argument("model")
    pr.add_argument("version")
    pr.add_argument("--scenario", action="store_true", help="what-if run; cannot be promoted")

    im = sub.add_parser("import-artifacts", help="register an external artifact dir as candidate")
    im.add_argument("model")
    im.add_argument("dir")
    im.add_argument("--tags", nargs="*", default=[], help="key=value pairs, e.g. rung=4")
    im.add_argument("--seed", type=int, default=0)
    im.add_argument("--scenario", action="store_true")
    im.add_argument("--promote", action="store_true", help="not allowed with --scenario")

    gt = sub.add_parser("gate", help="candidate vs REAL production version (local registry)")
    gt.add_argument("--model")
    gt.add_argument("--candidate", help="version to gate (default: newest candidate)")
    gt.add_argument("--tolerances", default=str(LOCAL_TOLERANCES))

    bp = sub.add_parser("bootstrap-production", help="one-time: register + promote champions")
    bp.add_argument("--dry-run", action="store_true")
    bp.add_argument("--seed", type=int, default=0)
    bp.add_argument("--holdout-season", type=int, default=2025)
    _route_parser(sub)
    return p


def _route_parser(sub: Any) -> None:
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
    from nba.registry.routing import load_extra_candidates
    from nba.stack.routebuild import build_specs, joint_parlay_spec, prepare_inputs

    if args.candidates_file:
        load_extra_candidates(Path(args.candidates_file))

    arch = None
    if args.archetypes:
        from nba.stack.populate import archetype_position_table

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
        return _run(args)
    except (LookupError, PermissionError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    cmd: str = args.cmd
    if cmd == "bootstrap-production":
        for line in bootstrap_production(
            Path(args.db_path),
            Path(args.store_dir),
            holdout_season=args.holdout_season,
            seed=args.seed,
            dry_run=args.dry_run,
        ):
            print(line)
        return 0

    if cmd == "route":
        return _route(args)

    if cmd == "promote":
        validate_cli_flags(promote=True, scenario=args.scenario)
        con, reg = _write_registry(args)
        try:
            print(promote_checked(reg, con, args.model, args.version))
        finally:
            con.close()
        return 0

    if cmd == "import-artifacts":
        validate_cli_flags(promote=args.promote, scenario=args.scenario)
        tags = parse_tags(args.tags)
        if args.scenario:
            tags["scenario"] = True
        con, reg = _write_registry(args)
        try:
            version, created = import_artifacts(
                reg, con, args.model, Path(args.dir), tags, seed=args.seed
            )
            print(
                f"{'imported' if created else 'already imported'}: "
                f"{args.model} {version} (stage=candidate)"
            )
            if args.promote:
                print(promote_checked(reg, con, args.model, version))
        finally:
            con.close()
        return 0

    con = open_readonly(args.db_path)
    try:
        if cmd == "list":
            rows = fetch_versions(con, args.model)
            print(format_list(rows) if rows else "no registered versions")
        elif cmd == "show":
            row = get_version(
                con,
                args.model,
                version=args.version,
                alias=args.alias if not args.version else None,
            )
            ok, why = check_promotable(row)
            print(f"{row.model_name} {row.version}  stage={row.stage}  alias={row.alias}")
            print(f"artifact: {row.artifact_path}")
            print(f"tags: {json.dumps(row.tags)}")
            print(f"metadata: {json.dumps(row.metadata, indent=1)}")
            print(f"metrics: {json.dumps(row.metrics, indent=1)}")
            print(f"promotable: {ok} ({why})")
        elif cmd == "compare":
            a = get_version(con, args.model, version=args.va)
            b = get_version(con, args.model, version=args.vb)
            print(f"{args.model}: {args.va} -> {args.vb}  (delta = {args.vb} - {args.va})")
            for k, va, vb, d, verdict in compare_versions(a, b):
                print(f"  {k:<28} {va:>12.4f} {vb:>12.4f} {d:>+12.4f} {verdict}")
        elif cmd == "gate":
            tol = load_tolerances(Path(args.tolerances))
            failures, notes = gate_local(con, tol, args.model, args.candidate)
            for n in notes:
                print(n)
            if failures:
                print("gate-local FAILED:")
                for f in failures:
                    print(f"  - {f}")
                return 1
            print("gate-local passed (or nothing to compare).")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
