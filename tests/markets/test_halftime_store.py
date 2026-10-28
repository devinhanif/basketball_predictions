# ruff: noqa: E501
"""Append-only paper table: stamping, refusal after the second half starts, no mutation, foreign stores."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from nba.markets import halftime_store as hs

NOW = datetime(2026, 10, 27, 1, 3, 0, tzinfo=UTC)


def row(
    ticker: str = "T1",
    made: datetime = NOW,
    edge: float = 0.08,
    player: str = "Ann Alpha",
    stat: str = "pts",
    k: int = 15,
) -> dict:
    base = dict.fromkeys(hs.COLUMNS, 0)
    base.update(
        made_at=made, slate_date=date(2026, 10, 26), event_id="E1", game="DET@PHI", series="KXNBAPTS", stat=stat,
        ticker=ticker, player_name=player, player_id=1, team="DET", k=k, ask_c=44, bid_c=42, ask_size=10.0,
        depth_5usd=True, fee_c=2, fee_model="kalshi", fee_coefficient=0.07, fee_multiplier=1.0, p_model=0.5,
        p_glm_e=0.5, edge_model=edge, edge_glm_e=0.0, t_ht=NOW - timedelta(seconds=180), t_star=NOW - timedelta(seconds=60),
        quote_received_at=NOW - timedelta(seconds=30), quote_file="q", state_received_at=NOW - timedelta(seconds=40),
        state_file="s", artifact_digest="d",
    )  # fmt: skip
    return base


def test_append_stamps_and_reads_back() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    assert st.append([row("A"), row("B")], NOW, False) == 2
    got = st.rows()
    assert [r["ticker"] for r in got] == ["A", "B"]
    assert all(r["made_at"].replace(tzinfo=UTC) == NOW for r in got)
    assert all(r["mode"] == "live" and r["min_prior"] == 5 for r in got)
    assert st.has("E1", "KXNBAPTS") and not st.has("E1", "KXNBAREB")


def test_refused_after_second_half_started_and_nothing_stored() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    with pytest.raises(hs.SecondHalfStartedError):
        st.append([row("A")], NOW, True)
    assert st.rows() == []
    assert st.con.execute("select reason from halftime_refused").fetchall() == [
        ("second_half_started",)
    ]


def test_row_stamp_must_match_the_call_stamp() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    with pytest.raises(ValueError, match="stamp"):
        st.append([row("A", made=NOW - timedelta(seconds=5))], NOW, False)
    assert st.rows() == []


def test_second_score_of_the_same_quote_is_a_no_op_not_a_rewrite() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    st.append([row("A", edge=0.08)], NOW, False)
    later = NOW + timedelta(seconds=30)
    assert st.append([row("A", made=later, edge=0.5)], later, False) == 0
    (r,) = st.rows()
    assert r["edge_model"] == pytest.approx(0.08) and r["made_at"].replace(tzinfo=UTC) == NOW


def test_module_has_no_update_or_delete_statement() -> None:
    src = Path(hs.__file__).read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for word in (
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "TRUNCATE",
        "INSERT OR REPLACE",
        "ON CONFLICT DO UPDATE",
    ):
        assert word not in code, word


def test_no_outcome_column_exists() -> None:
    cols = {c.lower() for c in hs.COLUMNS}
    for banned in ("outcome", "result", "settled", "settlement", "y", "pnl", "won"):
        assert banned not in cols


def test_foreign_stores_are_refused(tmp_path: Path) -> None:
    for bad in (tmp_path / "nba.duckdb", tmp_path / "data/kalshi/x.duckdb"):
        with pytest.raises(hs.ForeignStoreError):
            hs.HalftimeStore.open(bad)


def test_share_f_counts_units_by_best_strike() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="replay")
    st.append(
        [
            row("A", edge=0.04, k=10),
            row("B", edge=0.06, k=15),  # same unit (Ann, pts): best strike clears 0.05
            row("C", edge=0.049, player="Cy Gamma"),
            row("D", edge=0.05, player="Di Delta"),  # exactly at the threshold counts
        ],
        NOW,
        False,
    )
    f = st.share_f()
    assert (f["units"], f["units_model_edge_ge"]) == (3, 2)
    assert f["f_model"] == pytest.approx(2 / 3)


# ---- B1: written_at, mode, live window, replay refused into the live store ------------------------


def test_written_at_is_set_by_the_database_not_the_caller() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    assert (
        "written_at" not in hs.COLUMNS
        and "within_prereg_window" not in hs.COLUMNS
        and "mode" not in hs.COLUMNS
    )
    old = NOW - timedelta(days=3)
    st.append([row("A", made=old)], old, False)  # a stamp three days old, written now
    (r,) = st.rows()
    assert r["written_at"] > r["made_at"] + timedelta(days=2)


def test_live_window_flag_uses_written_at_against_t_star() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    now = datetime.now(UTC)
    fresh_row = row("A", made=now) | {
        "t_star": now.replace(tzinfo=None) - timedelta(seconds=100)
    }  # written 100 s after t*
    late_row = row("B", made=now) | {
        "t_star": now.replace(tzinfo=None) - timedelta(seconds=400)
    }  # 400 s after t*
    st.append([fresh_row, late_row], now, False)
    by = {r["ticker"]: r["within_prereg_window"] for r in st.rows()}
    assert by == {"A": True, "B": False}


def test_replay_rows_are_never_in_the_window_and_carry_the_mode() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="replay")
    st.append([row("A")], NOW, False, min_prior=3)
    (r,) = st.rows()
    assert (r["mode"], r["within_prereg_window"], r["min_prior"]) == ("replay", False, 3)


def test_replay_into_the_live_store_path_is_refused(tmp_path: Path) -> None:
    live = hs.live_store_path()
    assert live.name == "halftime_overs.duckdb" and live.parent.name == "jarvis"
    existed = live.exists()
    with pytest.raises(hs.ForeignStoreError, match="live paper table"):
        hs.HalftimeStore.open(live, mode="replay")
    assert live.exists() == existed  # refused before anything was created
    with pytest.raises(hs.ForeignStoreError):
        hs.HalftimeStore.open(str(live), mode="replay")


def test_modes_never_mix_in_one_store(tmp_path: Path) -> None:
    p = tmp_path / "h.duckdb"
    a = hs.HalftimeStore.open(p, mode="live")
    a.append([row("A")], NOW, False)
    a.close()
    with pytest.raises(hs.ForeignStoreError, match="other mode"):
        hs.HalftimeStore.open(p, mode="replay")
    hs.HalftimeStore.open(p, mode="live").close()
    q = tmp_path / "r.duckdb"
    b = hs.HalftimeStore.open(q, mode="replay")
    b.append([row("A")], NOW, False)
    b.close()
    with pytest.raises(hs.ForeignStoreError):
        hs.HalftimeStore.open(q, mode="live")


def test_bad_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        hs.HalftimeStore.open(":memory:", mode="backfill")


def test_f_counts_only_in_window_live_rows_and_min_prior() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    now = datetime.now(UTC)
    ts = now.replace(tzinfo=None)
    st.append(
        [
            row("A", made=now, edge=0.09) | {"t_star": ts},
            row("B", made=now, edge=0.09, player="Late")
            | {"t_star": ts - timedelta(seconds=900)},  # out of window
        ],
        now,
        False,
    )
    st.append(
        [row("C", made=now, edge=0.09, player="Thin") | {"t_star": ts}], now, False, min_prior=2
    )
    f = st.share_f()
    assert (f["units"], f["units_model_edge_ge"]) == (
        1,
        1,
    )  # only A: B is late, C was scored on thin history


def test_replay_f_counts_replay_rows_with_enough_history() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="replay")
    st.append([row("A", edge=0.09), row("B", edge=0.01, player="Cy")], NOW, False)
    st.append([row("C", edge=0.2, player="Thin")], NOW, False, min_prior=3)
    f = st.share_f()
    assert (f["units"], f["units_model_edge_ge"], f["mode"]) == (2, 1, "replay")


def test_note_skip_persists_once_per_reason() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    for _ in range(3):
        st.note_skip(NOW, "E1", "KXNBAPTS", "tick_not_closed", 1)
    st.note_skip(NOW, "E1", "KXNBAPTS", "x_off_t508_grid", 14)
    assert st.con.execute("select reason, n from halftime_skipped order by reason").fetchall() == [
        ("tick_not_closed", 1),
        ("x_off_t508_grid", 14),
    ]


def test_second_half_refusal_is_logged_once_per_series() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    for _ in range(3):
        with pytest.raises(hs.SecondHalfStartedError):
            st.append([row("A")], NOW, True)
    assert st.con.execute("select count(*) from halftime_refused").fetchone() == (1,)


# ---- m9 / m13 ----------------------------------------------------------------------------------


def test_mode_is_locked_by_the_meta_row_even_when_the_store_has_no_scored_row(
    tmp_path: Path,
) -> None:
    import os

    p = tmp_path / "Live.duckdb"
    hs.HalftimeStore.open(p, mode="live").close()  # a pass that only skipped: no scored row yet
    assert hs.HalftimeStore.open(p, mode="live").con.execute(
        "select mode from halftime_meta"
    ).fetchall() == [("live",)]
    with pytest.raises(hs.ForeignStoreError, match="other mode"):
        hs.HalftimeStore.open(p, mode="replay")
    link = tmp_path / "other_name.duckdb"
    os.link(p, link)  # a hard link is the same store under another name
    with pytest.raises(hs.ForeignStoreError, match="other mode"):
        hs.HalftimeStore.open(link, mode="replay")
    q = tmp_path / "r.duckdb"
    hs.HalftimeStore.open(q, mode="replay").close()
    with pytest.raises(hs.ForeignStoreError):
        hs.HalftimeStore.open(q, mode="live")


def test_meta_row_is_written_once() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="replay")
    assert st.con.execute("select count(*) from halftime_meta").fetchone() == (1,)


def test_refusals_carry_the_mode_and_a_database_timestamp() -> None:
    st = hs.HalftimeStore.open(":memory:", mode="live")
    with pytest.raises(hs.SecondHalfStartedError):
        st.append([row("A")], NOW - timedelta(days=9), True)
    ((mode, written),) = st.con.execute("select mode, written_at from halftime_refused").fetchall()
    assert mode == "live" and written > NOW.replace(tzinfo=None) - timedelta(
        days=1
    )  # not the caller's stamp
