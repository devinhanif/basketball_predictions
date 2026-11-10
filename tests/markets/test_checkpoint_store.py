# ruff: noqa: E501
"""The checkpoint store: sibling schema, append-only, foreign-path and mode locks, label-free counts."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nba.markets import checkpoint_scorer as ck
from nba.markets import checkpoint_store as cs
from nba.markets.checkpoint_store import CheckpointStore, ForeignStoreError
from nba.markets.halftime_store import SCHEMA as HT_SCHEMA
from nba.markets.halftime_store import HalftimeStore
from tests.markets.checkpoint_helpers import build_night, regular
from tests.markets.test_checkpoint_scorer import run

ROOT = Path(__file__).resolve().parents[2]


def test_the_halftime_table_is_unchanged_and_has_no_checkpoint_column() -> None:
    assert "checkpoint" not in HT_SCHEMA and "PRIMARY KEY (event_id, ticker)" in HT_SCHEMA
    con = HalftimeStore.open(":memory:").con
    cols = {r[1] for r in con.execute("PRAGMA table_info('halftime_scored')").fetchall()}
    assert "checkpoint" not in cols and "t_ht" in cols


def test_the_sibling_schema_has_the_same_content_columns_plus_the_checkpoint_ones() -> None:
    con = CheckpointStore.open(":memory:").con
    cols = {r[1] for r in con.execute("PRAGMA table_info('checkpoint_scored')").fetchall()}
    ht = {
        r[1]
        for r in HalftimeStore.open(":memory:")
        .con.execute("PRAGMA table_info('halftime_scored')")
        .fetchall()
    }
    assert (ht - cols) == {"t_ht", "t_star"}  # replaced by the state and tick stamps below
    assert {
        "checkpoint",
        "e_used",
        "model_support",
        "known_at",
        "last_play_at",
        "play_age_s",
        "lateness_s",
        "period",
        "clock",
    } <= cols
    pk = con.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() WHERE table_name = 'checkpoint_scored' AND constraint_type = 'PRIMARY KEY'"
    ).fetchone()
    assert pk is not None and list(pk[0]) == ["event_id", "ticker", "checkpoint"]


def test_there_is_no_outcome_column_and_no_update_or_delete() -> None:
    con = CheckpointStore.open(":memory:").con
    cols = {r[1] for r in con.execute("PRAGMA table_info('checkpoint_scored')").fetchall()}
    assert not {c for c in cols if re.search(r"settle|outcome|result|won|final|pnl|y$", c)}
    for rel in ("nba/markets/checkpoint_store.py", "nba/markets/checkpoint_scorer.py"):
        src = (ROOT / rel).read_text()
        assert not re.search(
            r"UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE", src
        ), rel


@pytest.mark.parametrize("name", ["nba.duckdb", "halftime_overs.duckdb"])
def test_another_writers_files_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ForeignStoreError):
        CheckpointStore.open(tmp_path / name)


def test_data_kalshi_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ForeignStoreError):
        CheckpointStore.open(tmp_path / "data" / "kalshi" / "x.duckdb")


def test_a_replay_may_not_write_the_live_path_and_the_mode_is_a_lock(tmp_path: Path) -> None:
    live = cs.live_store_path()
    assert live.name == "ingame_checkpoints.duckdb" and live != tmp_path
    with pytest.raises(ForeignStoreError):
        CheckpointStore.open(live, mode="replay")  # refused before any file is touched
    db = tmp_path / "x.duckdb"
    CheckpointStore.open(db, mode="live").close()
    with pytest.raises(ForeignStoreError):
        CheckpointStore.open(db, mode="replay")


def test_label_free_counts_per_checkpoint(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, regular()), store, tmp_path)
    rows = store.counts_by_checkpoint()
    assert [r["checkpoint"] for r in rows] == list(ck.CHECKPOINTS)
    assert all(r["games"] == 1 and r["units"] >= 2 and r["rows"] >= r["units"] for r in rows)
    assert all(0.0 <= (r["f_model"] or 0.0) <= 1.0 for r in rows)


def test_latest_by_game_returns_only_the_newest_checkpoint(tmp_path: Path) -> None:
    from tests.markets.test_checkpoint_scorer import SLATE

    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, regular()), store, tmp_path)
    latest = store.latest_by_game(SLATE)
    assert list(latest) == ["DET@PHI"] and {r["checkpoint"] for r in latest["DET@PHI"]} == {45}


def test_no_order_endpoint_or_credential_anywhere_in_the_new_modules() -> None:
    for rel in (
        "nba/markets/checkpoint_store.py",
        "nba/markets/checkpoint_scorer.py",
        "nba/jarvis/ingame_checkpoints.py",
    ):
        src = (ROOT / rel).read_text().lower()
        for word in (
            "/portfolio",
            "/orders",
            "create_order",
            "api_key",
            "private_key",
            "authorization",
            "secret",
        ):
            assert word not in src, (rel, word)


def _doctored(tmp: Path, edit: Callable[[dict[str, Any]], dict[str, Any]]) -> CheckpointStore:
    src = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp, regular()), src, tmp)
    out = CheckpointStore.open(":memory:")
    by: dict[Any, list[dict[str, Any]]] = {}
    for r in rep.rows:
        by.setdefault(r["made_at"], []).append(edit({**r}))
    for made, rows in by.items():
        out.append(rows, made)
    return out


def test_first_trigger_entry_is_one_per_unit_at_the_earliest_clearing_checkpoint(
    tmp_path: Path,
) -> None:
    def edit(r: dict[str, Any]) -> dict[str, Any]:
        # Di Delta clears only at 35 and 40; Ann Alpha clears at 30, 35 and 40; nobody else clears
        who = r["player_name"]
        clears = who == "Ann Alpha" or (who == "Di Delta" and r["checkpoint"] >= 35)
        r["edge_model"] = 0.2 if clears and r["checkpoint"] in (30, 35, 40) else -0.2
        return r

    prof = _doctored(tmp_path, edit).entry_profile((30, 35, 40))
    assert prof["entries"] == 2 and prof["by_checkpoint"] == {30: 1, 35: 1}  # Ann at 30, Di at 35
    assert prof["units"] >= 2 and 0.0 < prof["share_f"] <= 1.0
    assert prof["rms_sd_per_dollar"] > 0.5 and 0.0 < prof["mean_ask"] < 1.0


def test_first_trigger_never_enters_a_unit_at_a_second_checkpoint(tmp_path: Path) -> None:
    prof = _doctored(tmp_path, lambda r: {**r, "edge_model": 0.3}).entry_profile((30, 35, 40))
    assert set(prof["by_checkpoint"]) == {30}  # everything clears at 30: no second entry later
    assert prof["entries"] == prof["units"]  # one entry per (game, player, stat)
    none = _doctored(tmp_path / "n", lambda r: {**r, "edge_model": 0.0499}).entry_profile(
        (30, 35, 40)
    )
    assert none["entries"] == 0 and none["rms_sd_per_dollar"] is None


def test_entry_profile_refuses_an_unknown_column() -> None:
    with pytest.raises(ValueError):
        CheckpointStore.open(":memory:").entry_profile((30,), column="y")
