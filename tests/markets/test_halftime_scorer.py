# ruff: noqa: E501
"""Halftime scorer end to end on synthetic captures: stamping, refusal, fee, filters, idempotence."""

from __future__ import annotations

import math
from datetime import UTC, date, timedelta
from pathlib import Path

import pytest

from nba.markets import halftime_scorer as sc
from nba.markets.halftime_store import HalftimeStore
from nba.parlay.config import FeeModel
from tests.markets.halftime_helpers import EID, T0, Night, box, market, tiny_artifact

S = timedelta(seconds=1)
SLATE = date(2026, 10, 26)
FEE = FeeModel("kalshi_fee_schedule", "p_one_minus_p", 0.07, True, 0.0175, 1.0, (), "test")
PIN = {"formula": "p_one_minus_p", "coefficient": 0.07, "multiplier": 1.0}


class FakeRecency:
    """Stands in for the nba.duckdb recency read; one name has too little history."""

    def __init__(self) -> None:
        self.unresolved: dict[str, int] = {}

    def lookup(self, espn_name: str, espn_team: str) -> sc.Recency | None:
        if espn_name == "Cy Gamma":
            return sc.Recency(3, 2, dict.fromkeys(("pts", "reb", "ast", "fg3m"), 5.0), 20.0)
        if espn_name == "Zed Zulu":
            return None
        return sc.Recency(
            abs(hash(espn_name)) % 10_000,
            30,
            {"pts": 14.0, "reb": 5.0, "ast": 3.0, "fg3m": 1.5},
            30.0,
        )


def build(
    tmp: Path,
    *,
    q3_at: int | None = 900,
    page_at: int = 125,
    last_tick: int = 840,
    markets: list | None = None,
) -> Path:
    n = Night(tmp / "live_2026-10-26")
    n.predecessor()
    for s in range(0, last_tick + 1, 60):
        n.scoreboard(T0 + s * S, "STATUS_HALFTIME", 2)
    if q3_at is not None:
        n.scoreboard(T0 + q3_at * S, "STATUS_IN_PROGRESS", 3)
    n.summary(
        T0 + 110 * S,
        [
            box("Ann Alpha", "DET", 18, pts=12, reb=3, ast=2, tpm=1, pf=2, starter=True),
            box("Bob Beta", "DET", 0, dnp=True, no_stats=True),
            box("Cy Gamma", "PHI", 14, pts=4, reb=5, ast=1, pf=1),
            box("Di Delta", "PHI", 20, pts=9, reb=2, ast=6, pf=3, starter=True),
        ],
    )
    n.kalshi(
        "KXNBAPTS",
        T0 + page_at * S,
        markets
        if markets is not None
        else [
            market("KXNBAPTS", "Ann Alpha", 15, ask=0.44),
            market("KXNBAPTS", "Ann Alpha", 20, ask=0.10),
            market("KXNBAPTS", "Di Delta", 15, ask=0.30),
            market("KXNBAPTS", "Cy Gamma", 10, ask=0.30),  # n_prior 2: not scored
            market("KXNBAPTS", "Bob Beta", 10, ask=0.30),  # 0 first-half minutes: excluded
            market("KXNBAPTS", "Zed Zulu", 10, ask=0.30),  # not in the box
        ],
    )
    return n.close()


def run(
    night: Path,
    store: HalftimeStore,
    tmp: Path,
    now=None,
    fee: FeeModel = FEE,
    pin: dict | None = None,
    **kw,
) -> sc.ScoreReport:
    return sc.score_night(
        night, tiny_artifact(tmp), FakeRecency(), fee, store, SLATE, now, pin or PIN, **kw
    )  # type: ignore[arg-type]


def test_replay_scores_stamps_and_excludes(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    rep = run(build(tmp_path), store, tmp_path)
    rows = store.rows()
    assert {(r["player_name"], r["k"]) for r in rows} == {
        ("Ann Alpha", 15),
        ("Ann Alpha", 20),
        ("Di Delta", 15),
    }
    assert (
        rep.counts["x_insufficient_history"] == 1
        and rep.counts["x_dnp_first_half"] == 1
        and rep.counts["x_player_unmatched"] == 1
    )
    for r in rows:
        assert (
            r["made_at"].replace(tzinfo=UTC) == T0 + 125 * S
        )  # earliest instant every input existed
        assert (
            r["t_star"].replace(tzinfo=UTC) == T0 + 120 * S
            and r["within_prereg_window"] is False
            and r["mode"] == "replay"
        )
        assert 0.0 < r["p_model"] < 1.0 and 0.0 < r["p_glm_e"] < 1.0
        assert r["edge_model"] == pytest.approx(r["p_model"] - r["ask_c"] / 100 - r["fee_c"] / 100)
        assert r["edge_glm_e"] == pytest.approx(r["p_glm_e"] - r["ask_c"] / 100 - r["fee_c"] / 100)
        assert r["state_received_at"] < r["quote_received_at"]
        assert r["artifact_digest"]


def test_fee_is_the_config_object_rounded_up_once_per_contract(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    run(build(tmp_path), store, tmp_path)
    by = {(r["player_name"], r["k"]): r for r in store.rows()}
    assert by[("Ann Alpha", 15)]["fee_c"] == math.ceil(0.07 * 44 * 56 / 100)  # 2 c
    assert by[("Ann Alpha", 20)]["fee_c"] == math.ceil(0.07 * 10 * 90 / 100)  # 1 c
    assert (
        by[("Ann Alpha", 15)]["fee_coefficient"] == 0.07
        and by[("Ann Alpha", 15)]["fee_model"] == "kalshi_fee_schedule"
    )


def test_a_different_verified_fee_object_flows_through(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    fee2 = FeeModel("alt", "p_one_minus_p", 0.14, True, None, 1.0, (), "t")
    run(build(tmp_path), store, tmp_path, fee=fee2, pin={**PIN, "coefficient": 0.14})
    assert {r["fee_c"] for r in store.rows()} == {
        4,
        2,
        3,
    }  # ceil(2.74*1.4)... 44c->4, 10c->2, 30c->3


@pytest.mark.parametrize(
    "fee",
    [
        FeeModel("x", "p_one_minus_p", 0.07, False, None, 1.0, (), ""),  # unverified
        FeeModel("x", "p_one_minus_p", 0.08, True, None, 1.0, (), ""),  # drifted from the pin
        FeeModel("x", "flat", 0.07, True, None, 1.0, (), ""),
        FeeModel(
            "x", "p_one_minus_p", 0.07, True, None, 1.0, (("KXNBAPTS", 0.5),), ""
        ),  # multiplier != pin
    ],
)
def test_unverified_or_drifted_fee_is_refused(tmp_path: Path, fee: FeeModel) -> None:
    store = HalftimeStore.open(":memory:")
    with pytest.raises(sc.FeeNotVerifiedError):
        run(build(tmp_path), store, tmp_path, fee=fee)
    assert store.rows() == []


def test_live_tick_stamps_with_the_clock_before_the_second_half(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    now = T0 + 200 * S
    rep = run(build(tmp_path), store, tmp_path, now=now)
    assert rep.stored == 3
    assert {r["made_at"].replace(tzinfo=UTC) for r in store.rows()} == {now}


def test_live_window_flag_comes_from_the_database_clock_not_the_stamp(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    run(build(tmp_path), store, tmp_path, now=T0 + 160 * S)  # a stamp inside the window ...
    rows = store.rows()
    assert rows and {r["within_prereg_window"] for r in rows} == {
        False
    }  # ... but written years later
    assert all(r["written_at"] > r["made_at"] + timedelta(hours=1) for r in rows)


def test_refused_once_the_second_half_has_started(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    rep = run(build(tmp_path, q3_at=600), store, tmp_path, now=T0 + 650 * S)
    assert rep.stored == 0 and store.rows() == []
    assert rep.counts["refused_second_half_started"] == 1
    assert store.con.execute("select count(*) from halftime_refused").fetchone() == (1,)


def test_replay_refuses_a_snapshot_known_only_after_the_second_half_started(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    # the page arrives at +200 s, but Q3 began at +150 s: the quote did not exist before the second half
    rep = run(build(tmp_path, q3_at=150, page_at=200), store, tmp_path)
    assert rep.stored == 0 and rep.counts["refused_second_half_started"] == 1


def test_live_stale_feed_scores_nothing(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    rep = run(build(tmp_path, q3_at=None, last_tick=60), store, tmp_path, now=T0 + 400 * S)
    assert rep.stored == 0 and rep.counts["refused_feed_stale"] == 1


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    night = build(tmp_path)
    run(night, store, tmp_path)
    again = run(night, store, tmp_path)
    assert again.stored == 0 and len(store.rows()) == 3


def test_only_games_filter(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    rep = run(build(tmp_path), store, tmp_path, only_games={"BOS@NYK"})
    assert rep.stored == 0
    rep2 = run(build(tmp_path / "b"), store, tmp_path, only_games={"DET@PHI"})
    assert rep2.stored == 3


def test_yes_side_only_rows_have_no_other_side_fields(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    run(build(tmp_path), store, tmp_path)
    keys = set(store.rows()[0])
    assert not [k for k in keys if k.startswith("no_") or k.endswith("_no") or k == "side"]
    assert {r["event_id"] for r in store.rows()} == {EID}


def test_no_order_portfolio_or_credential_words_in_the_new_modules() -> None:
    from nba.markets import halftime_entry, halftime_model, halftime_scorer, halftime_store

    for mod in (halftime_entry, halftime_model, halftime_scorer, halftime_store):
        low = Path(mod.__file__).read_text().lower()
        for word in (
            "/orders",
            "create_order",
            "place_order",
            "submit_order",
            "cancel_order",
            "/portfolio",
            "private_key",
            "api_key",
            "authorization",
            "bearer ",
            "import httpx",
            "import requests",
            "urllib",
            ".post(",
        ):
            assert word not in low, (mod.__name__, word)


def test_store_mode_and_clock_must_agree(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="clock"):
        run(
            build(tmp_path), HalftimeStore.open(":memory:", mode="live"), tmp_path
        )  # live store, no clock
    with pytest.raises(ValueError, match="clock"):
        run(
            build(tmp_path / "b"),
            HalftimeStore.open(":memory:", mode="replay"),
            tmp_path,
            now=T0 + 200 * S,
        )


def test_counted_exclusions_are_persisted_once(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:")
    night = build(tmp_path)
    run(night, store, tmp_path)
    got = dict(
        store.con.execute(
            "select reason, n from halftime_skipped where series = 'KXNBAPTS'"
        ).fetchall()
    )
    assert (
        got["x_dnp_first_half"] == 1
        and got["x_player_unmatched"] == 1
        and got["x_insufficient_history"] == 1
    )
    assert "scored_candidates" not in got and not [
        k for k in got if k.startswith("unmatched_names")
    ]
    run(night, store, tmp_path)  # nothing new to score; no duplicate skip rows
    assert store.con.execute("select count(*) from halftime_skipped").fetchone() == (
        len(got) + _other_series(store, got),
    )


def _other_series(store: HalftimeStore, got: dict) -> int:
    return store.con.execute(
        "select count(*) from halftime_skipped where series <> 'KXNBAPTS'"
    ).fetchone()[0]  # type: ignore[index]


def test_live_pass_inside_an_open_tick_scores_nothing_then_everything(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    night = build(tmp_path)
    early = run(
        night, store, tmp_path, now=T0 + 140 * S
    )  # page at 125 s: the tick may still be arriving
    assert early.stored == 0 and early.counts["tick_not_closed"] >= 1
    later = run(night, store, tmp_path, now=T0 + 160 * S)
    assert later.stored == 3


def test_unobserved_halftime_start_scores_nothing(tmp_path: Path) -> None:
    n = Night(tmp_path / "live_2026-10-26")
    for s in range(0, 900, 60):  # capture started mid-halftime: no in-progress tick before
        n.scoreboard(T0 + s * S, "STATUS_HALFTIME", 2)
    n.scoreboard(T0 + 900 * S, "STATUS_IN_PROGRESS", 3)
    n.summary(T0 + 110 * S, [box("Ann Alpha", "DET", 18, pts=12, pf=2)])
    n.kalshi("KXNBAPTS", T0 + 125 * S, [market("KXNBAPTS", "Ann Alpha", 15)])
    store = HalftimeStore.open(":memory:")
    rep = run(n.close(), store, tmp_path)
    assert rep.stored == 0 and rep.counts["ht_start_unobserved"] == 4


def test_only_final_skip_reasons_are_persisted(tmp_path: Path) -> None:
    store = HalftimeStore.open(":memory:", mode="live")
    night = build(tmp_path)
    run(night, store, tmp_path, now=T0 + 140 * S)  # tick still open
    assert store.con.execute(
        "select count(*) from halftime_skipped where reason = 'tick_not_closed'"
    ).fetchone() == (0,)
    run(night, store, tmp_path, now=T0 + 160 * S)
    reasons = {r[0] for r in store.con.execute("select reason from halftime_skipped").fetchall()}
    assert "markets_at_snapshot" not in reasons and "scored_candidates" not in reasons
    assert "x_dnp_first_half" in reasons
    # the other three series have no page yet inside [t*, t* + 180 s]: transient until the window has passed
    assert "series_no_page_in_window" not in reasons
    run(night, store, tmp_path, now=T0 + 330 * S)
    late = {r[0] for r in store.con.execute("select reason from halftime_skipped").fetchall()}
    assert "series_no_page_in_window" in late
