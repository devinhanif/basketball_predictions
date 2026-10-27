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
        state_file="s", within_prereg_window=True, artifact_digest="d",
    )  # fmt: skip
    return base


def test_append_stamps_and_reads_back() -> None:
    st = hs.HalftimeStore.open(":memory:")
    assert st.append([row("A"), row("B")], NOW, False) == 2
    got = st.rows()
    assert [r["ticker"] for r in got] == ["A", "B"]
    assert all(r["made_at"].replace(tzinfo=UTC) == NOW for r in got)
    assert st.has("E1", "KXNBAPTS") and not st.has("E1", "KXNBAREB")


def test_refused_after_second_half_started_and_nothing_stored() -> None:
    st = hs.HalftimeStore.open(":memory:")
    with pytest.raises(hs.SecondHalfStartedError):
        st.append([row("A")], NOW, True)
    assert st.rows() == []
    assert st.con.execute("select reason from halftime_refused").fetchall() == [
        ("second_half_started",)
    ]


def test_row_stamp_must_match_the_call_stamp() -> None:
    st = hs.HalftimeStore.open(":memory:")
    with pytest.raises(ValueError, match="stamp"):
        st.append([row("A", made=NOW - timedelta(seconds=5))], NOW, False)
    assert st.rows() == []


def test_second_score_of_the_same_quote_is_a_no_op_not_a_rewrite() -> None:
    st = hs.HalftimeStore.open(":memory:")
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
    st = hs.HalftimeStore.open(":memory:")
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
