# ruff: noqa: E501
"""The steward's writes to ``forward_combo_meta`` (the only table here a human writes): the frozen shas, each proof's verdict with its evidence, the counting start.

    python -m nba.daily.combo_shadow.meta record-shas                 # config sha, R id, base id, model id
    python -m nba.daily.combo_shadow.meta proof P1 pass --evidence reports/prereg_combo_stocks_shadow/p1_reference.npz
    python -m nba.daily.combo_shadow.meta counting-start 2026-10-20   # REFUSES unless P1, P2, P3, P4 are all recorded as pass

Single-writer DuckDB: this takes the write lock on ``nba.duckdb``; run it when no pipeline job holds it. Nothing here is called by the daily job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from nba.daily.combo_shadow import store
from nba.daily.combo_shadow.config import load_config
from nba.daily.combo_shadow.run import model_id_of


def record_shas(con: duckdb.DuckDBPyConnection, at: datetime) -> None:
    cfg = load_config()
    for key, value in (
        ("config_sha256", cfg.config_sha), ("r_id", cfg.r_id), ("base_id", cfg.base_id), ("model_id", model_id_of(cfg)),
    ):  # fmt: skip
        store.set_meta(con, key, value, at)


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
    sub.add_parser("record-shas")
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
        if a.cmd == "record-shas":
            record_shas(con, now)
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
