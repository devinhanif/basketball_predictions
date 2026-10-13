# ruff: noqa: E501
"""The steward's writes to ``forward_avg_meta`` (the only table here a human writes): the installed bundle, each proof's verdict with its evidence, the counting start.

    python -m nba.daily.avg_shadow.meta bundle <bundle dir>             # weights shas, model_id, gate fit_through and sha, W_ALL list, data_version
    python -m nba.daily.avg_shadow.meta proof P2 pass --evidence reports/prereg_avg_shadow_live/p2.json
    python -m nba.daily.avg_shadow.meta counting-start 2026-10-20       # REFUSES unless P1a, P1b, P2, P3, P4 are all recorded as pass

Single-writer DuckDB: this takes the write lock on ``nba.duckdb``; run it when no pipeline job holds it. Nothing here is called by the daily job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from nba.daily.avg_shadow import bundle as B
from nba.daily.avg_shadow import store


def record_bundle(con: duckdb.DuckDBPyConnection, b: B.Bundle, at: datetime) -> None:
    m = b.manifest
    prefix = f"bundle_{b.month_block}"
    for key, value in (
        ("model_id", b.model_id), ("fit_through", str(b.fit_through)), ("installed_at", str(b.installed_at)), ("gate_id", b.gate_id),
        ("gate_fit_through", str(b.gate_fit_through)), ("weights_sha256", json.dumps(m["files"], sort_keys=True)),
        ("w_all_seasons", str(m.get("w_all_seasons", "2019-20 on"))), ("data_version", str(m.get("data_version"))),
        ("export_npz_sha256", str(m.get("export_npz_sha256"))),
    ):  # fmt: skip
        store.set_meta(con, f"{prefix}.{key}", value, at)


def record_proof(
    con: duckdb.DuckDBPyConnection, proof: str, verdict: str, evidence: Path | None, at: datetime
) -> None:
    if proof not in store.PROOFS:
        raise ValueError(f"unknown proof {proof!r}; expected one of {store.PROOFS}")
    if verdict not in ("pass", "fail"):
        raise ValueError("verdict is pass or fail")
    store.set_meta(con, f"proof_{proof}", verdict, at)
    if evidence is not None:
        store.set_meta(
            con,
            f"proof_{proof}_evidence",
            f"{evidence}:{hashlib.sha256(evidence.read_bytes()).hexdigest()}",
            at,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundle")
    b.add_argument("path", type=Path)
    p = sub.add_parser("proof")
    p.add_argument("name")
    p.add_argument("verdict")
    p.add_argument("--evidence", type=Path, default=None)
    c = sub.add_parser("counting-start")
    c.add_argument("date")
    a = ap.parse_args(argv)
    now = datetime.now(UTC).replace(tzinfo=None)
    con = duckdb.connect(a.db)
    try:
        if a.cmd == "bundle":
            record_bundle(con, B.verify(a.path), now)
        elif a.cmd == "proof":
            record_proof(con, a.name, a.verdict, a.evidence, now)
        else:
            store.set_counting_start(con, a.date, now)
        print(json.dumps(store.get_meta(con), indent=1, sort_keys=True))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
