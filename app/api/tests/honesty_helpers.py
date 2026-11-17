"""Shared helpers for the honesty-route tests (record, scorecard, parlay diagnostics, system
extras): a client on a private export copy, direct read-only reads of that copy so every expected
value traces to a stored row, and the settled_scores rows the scorecard tests plant."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import edit_db
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, reset_store

MONEY_KEYS = {"ev", "ev_low", "raw_ev", "raw_ev_low", "kelly", "stake", "balance", "price", "fees"}


def export_api(d: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_EXPORT_DIR", str(d))
    reset_store()
    with TestClient(create_app()) as c:
        yield c
    reset_store()


def db_rows(d: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(d / "export.duckdb"), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def keys_of(x: Any) -> set[str]:
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in keys_of(v)}
    if isinstance(x, list):
        return {k for v in x for k in keys_of(v)}
    return set()


def a_prop(c: TestClient, final: bool) -> tuple[str, int, str]:
    for s in c.get("/api/slates").json():
        for g in c.get(f"/api/slates/{s['date']}").json()["games"]:
            game = c.get(f"/api/games/{g['game_id']}").json()
            if bool(game["final"]) != final:
                continue
            for pl in game["players"]:
                for st in pl["props"]:
                    return g["game_id"], int(pl["player_id"]), st
    raise AssertionError("no prop in the fixture")


PROD, BASE, INT = "props_context_residual", "props_recency_v1", "props_context_residual_int"
DATES = [date(2024, 10, 23) + timedelta(days=i) for i in range(12)]


def plant_row(gid: str, d: date, season: int, model: str, crps: float, status: str = "scored",
         made: str | None = None, pid: int = 7) -> str:  # fmt: skip
    made_at = made or f"{d.isoformat()} 20:00:00"
    c = "NULL" if status == "dnp" else repr(crps)
    return (
        f"('2024-12-01 00:00:00', '{gid}', DATE '{d.isoformat()}', {season}, '{model}', 'v', "
        f"'pts', {pid}, TIMESTAMP '{made_at}', NULL, 1.0, NULL, NULL, {c}, '{status}')"
    )


def plant(d: Path) -> None:
    rows: list[str] = []
    for i, day in enumerate(DATES):
        for j in range(i % 3 + 1):  # unequal slates: 1, 2 or 3 pairs a date
            gid = f"00224009{i:02d}"
            rows.append(plant_row(gid, day, 2024, PROD, 2.0 + 0.1 * j, pid=j))
            rows.append(plant_row(gid, day, 2024, BASE, 2.5 + 0.05 * i, pid=j))
            rows.append(plant_row(gid, day, 2024, INT, 1.0, pid=j))
        rows.append(plant_row(f"00224009{i:02d}", day, 2024, PROD, 0.0, status="dnp", pid=99))
    # never read: the holdout season, a preseason game, and a row made 30 min before its tip
    rows.append(plant_row("0022500001", DATES[0], 2025, PROD, 999.0))
    rows.append(plant_row("0012400001", DATES[0], 2024, PROD, 999.0))
    rows.append(plant_row("0022400015", date(2024, 11, 5), 2024, PROD, 999.0,
                     made="2024-11-05 23:30:00"))  # fmt: skip
    edit_db(d, "DELETE FROM settled_scores", "INSERT INTO settled_scores VALUES " + ",".join(rows))
