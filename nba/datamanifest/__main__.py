"""CLI: ``python -m nba.datamanifest {snapshot,diff,check}``."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from nba.datamanifest.diff import diff_manifests, leak_candidates, render_diff_md
from nba.datamanifest.manifest import (
    REPO_ROOT,
    DataManifestLocked,
    build_manifest,
    load_config,
    summary_view,
)

DEFAULT_CONFIG = REPO_ROOT / "configs" / "datamanifest.yaml"


def _manifest_files(manifest_dir: Path) -> list[Path]:
    return sorted(manifest_dir.glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(path.read_text())
    return out


def _resolve(spec: str | None, files: list[Path], default_idx: int) -> dict[str, Any] | None:
    if spec is not None:
        p = Path(spec)
        if not p.exists():
            matches = [f for f in files if spec in f.name]
            if not matches:
                raise SystemExit(f"manifest not found: {spec}")
            p = matches[-1]
        return _load(p)
    if len(files) >= -default_idx:
        return _load(files[default_idx])
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.datamanifest")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = p.add_subparsers(dest="cmd", required=True)
    sn = sub.add_parser("snapshot", help="write a manifest + committed summary (read-only on DB)")
    sn.add_argument("--db", default=None)
    sn.add_argument("--parquet", action="append", default=[], help="also scan a parquet file")
    sn.add_argument("--no-fingerprint", action="store_true")
    df = sub.add_parser("diff", help="markdown diff between two manifests")
    df.add_argument("--from", dest="old", default=None)
    df.add_argument("--to", dest="new", default=None)
    sub.add_parser("check", help="exit 1 on flagged issues (latest vs previous + leak scan)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.config))
    mdir = REPO_ROOT / cfg["manifest_dir"]
    if args.cmd == "snapshot":
        try:
            m = build_manifest(
                args.db or REPO_ROOT / cfg["db_path"],
                cfg,
                parquet_paths=[Path(x) for x in args.parquet],
                fingerprint=not args.no_fingerprint,
                data_dir=REPO_ROOT / cfg["data_dir"],
            )
        except DataManifestLocked as exc:
            print(f"LOCKED: {exc}", file=sys.stderr)
            return 2
        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        mdir.mkdir(parents=True, exist_ok=True)
        out = mdir / f"{ts}_{m['data_version']}.json"
        out.write_text(json.dumps(m, indent=1, sort_keys=True))
        summ = REPO_ROOT / cfg["summary_path"]
        summ.parent.mkdir(parents=True, exist_ok=True)
        summ.write_text(json.dumps(summary_view(m), indent=1, sort_keys=True) + "\n")
        print(f"data_version={m['data_version']} tables={len(m['tables'])} -> {out}")
        return 0
    files = _manifest_files(mdir)
    if args.cmd == "diff":
        new = _resolve(args.new, files, -1)
        old = _resolve(args.old, files, -2)
        if new is None or old is None:
            print("need at least two manifests in " + str(mdir))
            return 0
        print(render_diff_md(diff_manifests(old, new, cfg), old, new))
        return 0
    # check
    new = _resolve(None, files, -1)
    if new is None:
        print("no manifest; run `snapshot` first")
        return 1
    old = _resolve(None, files, -2)
    flags: list[dict[str, Any]] = []
    if old is not None:
        flags += diff_manifests(old, new, cfg)["flags"]
    flags += leak_candidates(new, cfg)
    seen: set[str] = set()
    for f in flags:
        line = f"FLAG {f['kind']} {f.get('table', '')}.{f.get('column', '')}: {f['detail']}"
        if line not in seen:
            seen.add(line)
            print(line)
    print(f"{len(seen)} flag(s); data_version={new['data_version']}")
    return 1 if seen else 0


if __name__ == "__main__":
    raise SystemExit(main())
