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

import duckdb

from nba.db.connect import DEFAULT_DB_PATH
from nba.registry.bootstrap import bootstrap_production
from nba.registry.cli_guard import validate_cli_flags
from nba.registry.factory import DEFAULT_STORE_DIR
from nba.registry.gate import load_tolerances
from nba.registry.local import LocalRegistry
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
    return p


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
