# ruff: noqa: E501
"""Mac step 2 of the AVG_SHADOW_LIVE_2 monthly refit: assemble the folder the Colab notebook reads, checking every input first (read-only on the repo; copies only).

    .venv/bin/python research/colab/jobs/avg_shadow_monthly/stage_monthly.py --month 2026-11 [--drive "<My Drive>/nba_colab/avg_shadow_monthly"]

Refuses unless: the live export for the month exists and its sha256 equals ``export_info_live.json``; the previous month's bundle is INSTALLED and verifies (all file
shas, model_id, rule sha); the staged rule is FROZEN with the pinned sha256 (the rule's own awk command); ``docs/HOLDOUT_ACCESS_LOG.md`` has a row naming
``AVG_SHADOW_LIVE_2 monthly refit`` and the month. Output ``data/colab/avg_shadow_monthly/<month>/run/`` (cur25 + hist exports, live export, info, ``prev_bundle/``,
rule, log); with ``--drive`` the folder is copied to ``<drive>/<month>/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from nba.daily.avg_shadow import RULE_SHA256
from nba.daily.avg_shadow import bundle as B

ROOT = Path(__file__).resolve().parents[4]
REBUILD = ROOT / "data/colab/avg_shadow_rebuild"
OUT = ROOT / "data/colab/avg_shadow_monthly"
BUNDLES = ROOT / "data/avg_shadow/bundles"
RULE = ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md"
LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
LOG_ROW_KEY = "AVG_SHADOW_LIVE_2 monthly refit"
MARK = "=== RESULTS BELOW ===\n"
SMALL = ("seq_ensemble_rt_cur25.npz", "seq_ensemble_rt_hist.npz")


def rule_sha(text: str) -> str:
    head = text.split(MARK)[0]
    if head == text:
        raise SystemExit("results marker missing from the rule document")
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise SystemExit("the rule document is not FROZEN")
    return hashlib.sha256(head.encode()).hexdigest()


def previous_bundle(root: Path, month: str) -> B.Bundle:
    """The newest INSTALLED bundle of a month before ``month`` (it must verify)."""
    found = [B.verify(p) for p in B.list_bundles(root)]
    found = [b for b in found if b.month_block < month and b.installed_at is not None]
    if not found:
        raise SystemExit(f"no installed bundle of a month before {month} under {root}")
    return max(found, key=lambda b: b.month_block)


def stage(
    month: str,
    drive: Path | None,
    *,
    bundles: Path = BUNDLES,
    rebuild: Path = REBUILD,
    out: Path = OUT,
    rule: Path = RULE,
    log: Path = LOG,
) -> Path:
    src = out / month
    info_path = src / "export_info_live.json"
    if not info_path.exists():
        raise SystemExit(f"{info_path} missing: run export_monthly.py --month {month} first")
    info = json.loads(info_path.read_text())
    live = src / "seq_ensemble_rt_live.npz"
    if info["month_block"] != month or info["before_date"] != f"{month}-01":
        raise SystemExit("export_info_live.json is for another month")
    if B.file_sha256(live) != info["npz_sha256"]:
        raise SystemExit("the live export changed after export_info_live.json was written")
    sha = rule_sha(rule.read_text())
    if sha != RULE_SHA256:
        raise SystemExit(f"rule sha {sha[:8]} is not AVG_SHADOW_LIVE_2 {RULE_SHA256[:8]}")
    if not [ln for ln in log.read_text().splitlines() if LOG_ROW_KEY in ln and month in ln]:
        raise SystemExit(
            f"no holdout-log row '{LOG_ROW_KEY}' naming {month} in {log}: the archivist logs it first"
        )
    prev = previous_bundle(bundles, month)
    for n in SMALL:
        if not (rebuild / n).exists():
            raise SystemExit(f"{rebuild / n} missing")
    run = src / "run"
    if run.exists():
        shutil.rmtree(run)
    (run / "prev_bundle").mkdir(parents=True)
    for n in SMALL:
        shutil.copy2(rebuild / n, run / n)
    shutil.copy2(live, run / live.name)
    shutil.copy2(info_path, run / info_path.name)
    for n in (*B.DATA_FILES, "manifest.json"):
        shutil.copy2(prev.path / n, run / "prev_bundle" / n)
    shutil.copy2(rule, run / rule.name)
    shutil.copy2(log, run / log.name)
    B.verify(run / "prev_bundle")
    if drive is not None:
        dst = drive / month
        if dst.exists():
            raise SystemExit(f"{dst} already exists; refusing to overwrite a staged run")
        shutil.copytree(run, dst)
    print(
        json.dumps(
            {
                "run": str(run),
                "drive": str(drive / month) if drive else None,
                "previous_model_id": prev.model_id,
                "previous_month": prev.month_block,
                "live_rows": info["targets"],
                "live_max_date": info["max_date"],
                "rule_sha256": sha[:8],
                "bytes": sum(f.stat().st_size for f in run.rglob("*") if f.is_file()),
            },
            indent=2,
        )
    )
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--month", required=True)
    ap.add_argument("--drive", type=Path, default=None)
    a = ap.parse_args(argv)
    stage(a.month, a.drive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
