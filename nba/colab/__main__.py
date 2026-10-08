"""CLI: ``python -m nba.colab {push,status,pull} ...``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nba.colab.drive import DriveNotFoundError, drive_root
from nba.colab.jobs import JobError, list_jobs, load_job
from nba.colab.pull import pull
from nba.colab.push import push
from nba.colab.status import format_status, status


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.colab")
    sub = p.add_subparsers(dest="cmd", required=True)
    pu = sub.add_parser("push", help="stage inputs + run-stamped notebook into Drive")
    pu.add_argument("job")
    pu.add_argument("--no-produce", action="store_true", help="never run producer commands")
    st = sub.add_parser("status", help="list runs and whether artifacts have landed")
    st.add_argument("job", nargs="?")
    pl = sub.add_parser("pull", help="copy artifacts back to data/colab/runs/<job>/<run_id>/")
    pl.add_argument("job")
    pl.add_argument("run_id", nargs="?", help="default: newest run with artifacts")
    pl.add_argument("--register", action="store_true", help="registry import-artifacts (candidate)")
    pl.add_argument("--db-path", help="registry DB (default: repo default)")
    pl.add_argument("--store-dir", help="registry store (default: repo default)")
    sub.add_parser("jobs", help="list defined jobs")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "jobs":
            for name in list_jobs():
                print(name)
            return 0
        root = drive_root()
        if args.cmd == "push":
            print(push(load_job(args.job), root, produce=not args.no_produce).instructions)
        elif args.cmd == "status":
            jobs = [args.job] if args.job else list_jobs()
            print(format_status([r for j in jobs for r in status(root, j)]))
        else:
            res = pull(
                load_job(args.job),
                root,
                args.run_id,
                register=args.register,
                db_path=Path(args.db_path) if args.db_path else None,
                store_dir=Path(args.store_dir) if args.store_dir else None,
            )
            print(res.summary)
        return 0
    except (DriveNotFoundError, JobError, FileNotFoundError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
