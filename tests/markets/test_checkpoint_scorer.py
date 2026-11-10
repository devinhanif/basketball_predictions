# ruff: noqa: E501
"""In-game checkpoint scorer on synthetic and recorded captures: firing at boundaries, period ends,
overtime, stale state, known-at refusal, replay of a recorded game, live == replay, idempotence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nba.markets import checkpoint_scorer as ck
from nba.markets import halftime_entry as he
from nba.markets.checkpoint_store import CheckpointStore
from nba.parlay.config import FeeModel
from tests.markets.checkpoint_helpers import T0, S, build_night, first_tick, regular
from tests.markets.halftime_helpers import tiny_artifact
from tests.markets.test_halftime_scorer import FakeRecency

SLATE = date(2026, 10, 26)
NS = len(he.SERIES)  # every checkpoint is decided once per series (state-level refusals count each)
FEE = FeeModel("kalshi_fee_schedule", "p_one_minus_p", 0.07, True, 0.0175, 1.0, (), "test")
PIN = {"formula": "p_one_minus_p", "coefficient": 0.07, "multiplier": 1.0}
FIXTURE = Path(__file__).parent / "fixtures" / "checkpoint_game_401909945.json"


def run(
    night: Path,
    store: CheckpointStore,
    tmp: Path,
    now: datetime | None = None,
    fee: FeeModel = FEE,
    pin: dict | None = None,
) -> ck.CheckpointReport:
    return ck.score_night_checkpoints(
        night, tiny_artifact(tmp), FakeRecency(), fee, store, SLATE, now, pin or PIN
    )  # type: ignore[arg-type]


def by_checkpoint(store: CheckpointStore) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in store.rows():
        out.setdefault(r["checkpoint"], []).append(r)
    return out


# ------------------------------------------------------------------------------ the clock


@pytest.mark.parametrize(
    ("period", "clock", "want"),
    [
        (1, "12:00", 0.0), (1, "7:41", 259.0), (1, "0.0", 720.0), (2, "12:00", 720.0),
        (2, "0.0", 1440.0), (3, "11:00", 1500.0), (4, "12.8", 2867.2), (4, "0.0", 2880.0),
        (5, "5:00", 2880.0), (5, "4:50", 2890.0), (6, "0.0", 3480.0),
    ],
)  # fmt: skip
def test_elapsed_game_seconds(period: int, clock: str, want: float) -> None:
    assert ck.elapsed_seconds(period, clock) == pytest.approx(want)


@pytest.mark.parametrize(("period", "clock"), [(None, "5:00"), (0, "5:00"), (2, "x"), (2, "")])
def test_unreadable_clock_is_none(period: object, clock: str) -> None:
    assert ck.elapsed_seconds(period, clock) is None


def test_the_checkpoints_are_every_five_game_minutes_to_45() -> None:
    assert ck.CHECKPOINTS == (5, 10, 15, 20, 25, 30, 35, 40, 45)
    assert 24 not in ck.CHECKPOINTS  # the half stays the halftime scorer's, unchanged
    assert ck.PRIMARY_MIN == 30


# ------------------------------------------------------------------------------ firing


def test_every_checkpoint_fires_once_at_the_first_state_at_or_after_it(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    store = CheckpointStore.open(":memory:")
    rep = run(night, store, tmp_path)
    got = by_checkpoint(store)
    assert sorted(got) == list(ck.CHECKPOINTS)
    for c, rows in got.items():
        i = first_tick(c)
        assert all(r["e_used"] == pytest.approx(65 * i / 60) for r in rows)
        assert all(r["lateness_s"] == pytest.approx(65 * i - 60 * c) for r in rows)
        assert {r["known_at"].replace(tzinfo=UTC) for r in rows} == {
            T0 + (60 * i + 5) * S
        }  # the page after the state
        assert {r["made_at"].replace(tzinfo=UTC) for r in rows} == {T0 + (60 * i + 5) * S}
        assert {r["state_received_at"].replace(tzinfo=UTC) for r in rows} == {T0 + 60 * i * S}
        assert {(r["player_name"], r["k"]) for r in rows} >= {("Ann Alpha", 15), ("Di Delta", 15)}
        assert all(r["mode"] == "replay" and r["within_prereg_window"] is False for r in rows)
        assert all(
            r["play_age_s"] == pytest.approx(15.0) for r in rows
        )  # 10 s old play + 5 s to the page
    assert rep.stored == len(store.rows())


def test_no_second_game_minute_checkpoint_after_regulation(tmp_path: Path) -> None:
    states = [*regular(45), (60 * 45, 2880.0, "STATUS_FINAL")]
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, states), store, tmp_path)
    assert max(by_checkpoint(store)) == 45


def test_a_row_carries_the_model_support_label(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, regular()), store, tmp_path)
    sup = {c: {r["model_support"] for r in rows} for c, rows in by_checkpoint(store).items()}
    assert sup[5] == {"extrapolated"}  # state at 5.42 game-min: below T508's first checkpoint (6)
    assert sup[10] == sup[30] == {"trained"}
    assert sup[45] == {"extrapolated"}  # 45.17 > 42
    assert ck.support_of(6.0) == ck.support_of(42.0) == "trained"
    assert ck.support_of(5.99) == ck.support_of(42.01) == "extrapolated"


def test_the_score_changes_with_the_elapsed_time_it_is_served_at(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, regular()), store, tmp_path)
    got = by_checkpoint(store)
    pm = {
        c: next(r["p_model"] for r in rows if (r["player_name"], r["k"]) == ("Di Delta", 15))
        for c, rows in got.items()
    }
    assert (
        len(set(pm.values())) > 1
    )  # e is a model input: scoring at a fixed e would give one number


# ------------------------------------------------------------------------------ boundaries


def test_a_period_end_state_can_be_the_first_at_or_after(tmp_path: Path) -> None:
    # checkpoint 10 (600 s): the first state at/after it is the END_PERIOD tick at 720 s (120 s late: inside 150 s)
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 540.0, "STATUS_IN_PROGRESS"),
        (120, 720.0, "STATUS_END_PERIOD"),
    ]
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, states), store, tmp_path)
    r10 = by_checkpoint(store)[10]
    assert {r["lateness_s"] for r in r10} == {120.0} and {r["period"] for r in r10} == {2}


def test_a_state_more_than_150_s_past_the_checkpoint_is_a_missed_tick_never_backfilled(
    tmp_path: Path,
) -> None:
    # 540 s is the first state at/after 300 s (240 s late); 760 s the first at/after 600 s (160 s late)
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 540.0, "STATUS_IN_PROGRESS"),
        (120, 760.0, "STATUS_IN_PROGRESS"),
        (180, 800.0, "STATUS_IN_PROGRESS"),
    ]
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, states), store, tmp_path)
    assert by_checkpoint(store) == {}  # a later state never fills a checkpoint that was missed
    assert (
        rep.by_checkpoint[5]["checkpoint_missed_late"]
        == rep.by_checkpoint[10]["checkpoint_missed_late"]
        == NS
    )
    assert store.noted("401900001", "KXNBAPTS", 5) and store.noted("401900001", "KXNBAPTS", 10)


@pytest.mark.parametrize(("elapsed", "fires"), [(750.0, True), (751.0, False)])
def test_the_lateness_limit_is_150_s_inclusive(tmp_path: Path, elapsed: float, fires: bool) -> None:
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 320.0, "STATUS_IN_PROGRESS"),
        (120, elapsed, "STATUS_IN_PROGRESS"),
    ]
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, states), store, tmp_path)
    got = by_checkpoint(store)
    assert 5 in got and (10 in got) is fires


def test_halftime_is_not_a_checkpoint_state(tmp_path: Path) -> None:
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 1100.0, "STATUS_IN_PROGRESS"),
        (120, 1440.0, "STATUS_HALFTIME"),
    ]
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, states), store, tmp_path)
    got = by_checkpoint(store)
    assert 20 not in got  # first state at/after 1200 s is the halftime state, 240 s late
    assert rep.by_checkpoint[20]["checkpoint_missed_late"] == NS


def test_overtime_is_excluded(tmp_path: Path) -> None:
    # the tick that would fire checkpoint 45 (2700 s) is already in overtime: refused, and nothing fires in OT
    states = [
        (0, 0.0, "STATUS_IN_PROGRESS"),
        (60, 2600.0, "STATUS_IN_PROGRESS"),
        (120, 2890.0, "STATUS_IN_PROGRESS"),
        (180, 3000.0, "STATUS_IN_PROGRESS"),
    ]
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, states), store, tmp_path)
    assert max(by_checkpoint(store), default=0) < 45
    assert rep.by_checkpoint[45]["overtime_excluded"] == NS
    assert all(r["period"] <= 4 for r in store.rows())


def test_a_game_that_reaches_overtime_keeps_its_regulation_checkpoints(tmp_path: Path) -> None:
    states = [
        *regular(45),
        (60 * 45, 2900.0, "STATUS_IN_PROGRESS"),
        (60 * 46, 3000.0, "STATUS_IN_PROGRESS"),
    ]
    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, states), store, tmp_path)
    got = by_checkpoint(store)
    assert sorted(got) == list(ck.CHECKPOINTS)
    assert all(r["period"] <= 4 and r["e_used"] < 48 for rows in got.values() for r in rows)


# ------------------------------------------------------------------------------ stale state, the buzzer


def test_a_state_whose_last_play_is_over_three_minutes_old_is_refused(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, regular(), play_age_s=200), store, tmp_path)
    assert store.rows() == [] and rep.counts["state_stale"] == len(ck.CHECKPOINTS)


def test_the_stale_limit_is_measured_at_known_at_not_at_the_state(tmp_path: Path) -> None:
    # play 170 s old at the state, quote page 5 s later: 175 s at known_at -> fires; 176+ -> refused
    ok = CheckpointStore.open(":memory:")
    run(build_night(tmp_path / "a", regular(), play_age_s=175), ok, tmp_path)
    assert len(by_checkpoint(ok)) == len(ck.CHECKPOINTS)
    bad = CheckpointStore.open(":memory:")
    run(build_night(tmp_path / "b", regular(), play_age_s=176), bad, tmp_path)
    assert bad.rows() == []


def test_a_summary_without_plays_is_refused_not_guessed(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    # strip the plays from every summary file
    import gzip

    for p in (night / "espn_summary").rglob("*.json.gz"):
        with gzip.open(p) as fh:
            body = json.loads(fh.read())
        body["plays"] = []
        with gzip.open(p, "wb") as f:
            f.write(json.dumps(body).encode())
    store = CheckpointStore.open(":memory:")
    rep = run(night, store, tmp_path)
    assert store.rows() == [] and rep.counts["state_no_plays"] == len(ck.CHECKPOINTS)


def test_nothing_is_scored_after_the_final_buzzer(tmp_path: Path) -> None:
    # checkpoint 45 fires on the 2730 s state; the FINAL summary lands before the quote page does
    states = [(0, 0.0, "STATUS_IN_PROGRESS"), (60, 2730.0, "STATUS_IN_PROGRESS")]
    night = build_night(
        tmp_path, states, page_after_s=40, extra=[(70, "STATUS_FINAL", 2880.0, None)]
    )
    store = CheckpointStore.open(":memory:")
    rep = run(night, store, tmp_path)
    assert 45 not in by_checkpoint(store)
    assert rep.by_checkpoint[45]["game_over_before_quote"] == 1


def test_a_game_not_in_play_scores_nothing_live(tmp_path: Path) -> None:
    states = [*regular(45), (60 * 45, 2880.0, "STATUS_FINAL")]
    night = build_night(tmp_path, states)
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(night, store, tmp_path, now=T0 + (60 * 45 + 20) * S)
    assert (
        store.rows() == []
    )  # nothing scored after the buzzer; an open checkpoint is named, not silent
    assert set(rep.counts) - {"fires"} <= {"game_over_during_gap"}


def test_a_state_with_no_page_at_all_is_counted(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, regular(), page_after_s=None), store, tmp_path)
    assert store.rows() == [] and rep.counts["series_no_page_in_window"] == NS * len(ck.CHECKPOINTS)


# ------------------------------------------------------------------------------ known-at (live)


def test_live_rows_are_stamped_with_the_clock_and_known_at_is_the_tick(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    i = first_tick(10)
    known = T0 + (60 * i + 5) * S
    now = known + 45 * S
    store = CheckpointStore.open(":memory:", mode="live")
    run(night, store, tmp_path, now=now)
    got = by_checkpoint(store)
    assert max(got) == 10  # the later checkpoints have not happened at ``now``
    assert {r["made_at"].replace(tzinfo=UTC) for r in got[10]} == {now}
    assert {r["known_at"].replace(tzinfo=UTC) for r in got[10]} == {known}


def test_a_live_write_more_than_180_s_after_known_at_is_refused(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    known = T0 + (60 * first_tick(10) + 5) * S
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(night, store, tmp_path, now=known + 181 * S)
    assert 10 not in by_checkpoint(store)
    assert (
        rep.by_checkpoint[10]["refused_late_write"] == 1
    )  # the one series with a page; the other three have none
    store2 = CheckpointStore.open(":memory:", mode="live")
    run(night, store2, tmp_path, now=known + 180 * S)
    assert 10 in by_checkpoint(store2)


def test_a_tick_still_arriving_waits_and_is_not_noted(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    known = T0 + (60 * first_tick(10) + 5) * S
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(night, store, tmp_path, now=known + 10 * S)
    assert store.rows() == [] and rep.counts["tick_not_closed"] >= 1
    assert not store.noted("401900001", "KXNBAPTS", 10)  # transient: retried next pass
    run(night, store, tmp_path, now=known + 40 * S)
    assert 10 in by_checkpoint(store)


def test_live_never_reads_a_summary_or_page_received_after_now(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    r = ck.CheckpointReader(night, T0 + 700 * S)
    assert all(s.received_at <= T0 + 700 * S for s in r.states("401900001"))
    assert all(p[0] <= he.utc(T0 + 700 * S) for p in r.kalshi["KXNBAPTS"])


def test_live_and_replay_store_the_same_numbers(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    known = T0 + (60 * first_tick(10) + 5) * S
    live = CheckpointStore.open(":memory:", mode="live")
    run(night, live, tmp_path, now=known + 45 * S)
    rep = CheckpointStore.open(":memory:")
    run(night, rep, tmp_path)
    a = {
        (r["ticker"], r["checkpoint"]): (
            r["p_model"],
            r["p_glm_e"],
            r["ask_c"],
            r["fee_c"],
            r["e_used"],
        )
        for r in live.rows()
    }
    b = {
        (r["ticker"], r["checkpoint"]): (
            r["p_model"],
            r["p_glm_e"],
            r["ask_c"],
            r["fee_c"],
            r["e_used"],
        )
        for r in rep.rows()
        if r["checkpoint"] == 10
    }
    assert a == b and a


def test_live_requires_the_clock_and_replay_forbids_it(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    with pytest.raises(ValueError, match="clock"):
        run(night, CheckpointStore.open(":memory:", mode="live"), tmp_path)
    with pytest.raises(ValueError, match="clock"):
        run(night, CheckpointStore.open(":memory:"), tmp_path, now=T0 + 700 * S)


def test_the_fee_object_must_be_verified_and_pinned(tmp_path: Path) -> None:
    from nba.markets.halftime_scorer import FeeNotVerifiedError

    night = build_night(tmp_path, regular())
    bad = FeeModel("x", "p_one_minus_p", 0.07, False, None, 1.0, (), "")
    with pytest.raises(FeeNotVerifiedError):
        run(night, CheckpointStore.open(":memory:"), tmp_path, fee=bad)
    drift = FeeModel("x", "p_one_minus_p", 0.08, True, None, 1.0, (), "")
    with pytest.raises(FeeNotVerifiedError):
        run(night, CheckpointStore.open(":memory:"), tmp_path, fee=drift)


def test_fee_is_the_verified_object_rounded_up_to_the_cent(tmp_path: Path) -> None:
    import math

    store = CheckpointStore.open(":memory:")
    run(build_night(tmp_path, regular()), store, tmp_path)
    by = {(r["player_name"], r["k"]): r for r in by_checkpoint(store)[10]}
    assert by[("Ann Alpha", 15)]["fee_c"] == math.ceil(0.07 * 44 * 56 / 100)
    assert by[("Ann Alpha", 20)]["fee_c"] == math.ceil(0.07 * 10 * 90 / 100)
    r = by[("Ann Alpha", 15)]
    assert r["edge_model"] == pytest.approx(r["p_model"] - r["ask_c"] / 100 - r["fee_c"] / 100)


# ------------------------------------------------------------------------------ idempotence


def test_a_second_pass_over_the_same_capture_stores_nothing(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    store = CheckpointStore.open(":memory:")
    first = run(night, store, tmp_path)
    n = len(store.rows())
    again = run(night, store, tmp_path)
    assert first.stored == n and again.stored == 0 and len(store.rows()) == n


def test_a_restart_on_the_file_store_adds_nothing(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    db = tmp_path / "ck" / "ck.duckdb"
    s1 = CheckpointStore.open(db, mode="live")
    now = T0 + (60 * first_tick(10) + 5 + 45) * S
    run(night, s1, tmp_path, now=now)
    n = len(s1.rows())
    s1.close()
    s2 = CheckpointStore.open(db, mode="live")  # the process died and came back
    rep = run(night, s2, tmp_path, now=now + 20 * S)
    assert rep.stored == 0 and len(s2.rows()) == n
    run(
        night, s2, tmp_path, now=T0 + (60 * first_tick(15) + 5 + 45) * S
    )  # the next checkpoint still lands
    assert {r["checkpoint"] for r in s2.rows()} >= {10, 15}


def test_the_primary_key_makes_a_direct_second_append_a_no_op(tmp_path: Path) -> None:
    store = CheckpointStore.open(":memory:")
    rep = run(build_night(tmp_path, regular()), store, tmp_path)
    before = len(store.rows())
    made = rep.rows[0]["made_at"]
    same = [r for r in rep.rows if r["made_at"] == made]
    assert store.append(same, made) == 0
    assert len(store.rows()) == before


# ------------------------------------------------------------------------------ a recorded game


def test_replay_of_a_recorded_games_summaries(tmp_path: Path) -> None:
    """Real header/clock/score/last-play sequence of PHI@CHI 2026-11-08 (ESPN 401909945) and its real Kalshi
    page times: every checkpoint of the recorded game fires at the first recorded state at/after it."""
    rec = json.loads(FIXTURE.read_text())
    from tests.markets.checkpoint_helpers import markets_for, players_at, summary_body
    from tests.markets.halftime_helpers import Night

    n = Night(tmp_path / "live_2026-10-26")
    t_first = he.parse_ts(rec["summaries"][0]["at"])
    expected: dict[int, float] = {}
    peak = -1.0
    for s in rec["summaries"]:
        at = he.parse_ts(s["at"])
        e = ck.elapsed_seconds(s["period"], s["clock"]) if s["period"] else None
        lp = he.parse_ts(s["last_play"].replace("Z", "+00:00")) if s["last_play"] else None
        body = summary_body(
            players_at(e or 0.0),
            s["status"],
            s["period"],
            s["clock"],
            int(s["away"][1] or 0),
            int(s["home"][1] or 0),
            lp,
        )
        n.add("espn_summary", "401900001", at, body)
        if e is not None:
            for c in ck.CHECKPOINTS:
                if c not in expected and e >= 60 * c and peak < 60 * c:
                    expected[c] = e
            peak = max(peak, e)
    for p in rec["pages"]:
        n.kalshi("KXNBAPTS", he.parse_ts(p), markets_for("KXNBAPTS"))
    night = n.close()
    store = CheckpointStore.open(":memory:")
    rep = run(night, store, tmp_path)
    assert t_first < he.parse_ts(rec["pages"][-1])
    got = by_checkpoint(store)
    # every checkpoint of the recorded game was reached in regulation; each stored one is the first state at/after it
    assert sorted(expected) == list(ck.CHECKPOINTS)
    for c, rows in got.items():
        assert all(r["e_used"] * 60 == pytest.approx(expected[c]) for r in rows)
        assert all(0 <= r["lateness_s"] <= ck.LATE_MAX_S for r in rows)
    refused = {c: rep.by_checkpoint[c] for c in ck.CHECKPOINTS if c not in got}
    for c, cnt in refused.items():  # a checkpoint not stored has a named reason, never silence
        assert sum(v for k, v in cnt.items() if k not in ("fires",)) >= 1, (c, cnt)
    assert len(got) >= 7  # the recorded cadence (60 s) misses a checkpoint only on a real gap
    # known_at is a recorded Kalshi page time, at or after the state and within 90 s of it
    pages = {he.parse_ts(p) for p in rec["pages"]}
    for rows in got.values():
        for r in rows:
            assert r["known_at"].replace(tzinfo=UTC) in pages
            assert (
                0
                <= (r["known_at"] - r["state_received_at"]).total_seconds()
                <= ck.QUOTE_MAX_AFTER_S
            )


# ------------------------------------------------------------------------------ gate counts


def test_gate_counts_are_label_free_and_count_what_fired(tmp_path: Path) -> None:
    out = ck.gate_counts(build_night(tmp_path, regular()))
    c30 = out["by_checkpoint"][30]
    assert c30["reached"] == 1 and c30["fired"] == 4 and c30["valid"] == 1  # one series has pages
    assert (
        c30["strikes"] == 4 and c30["units"] == 3
    )  # Ann 15 and 20, Di 15, Cy Gamma 10 (Bob has no minutes)


def test_the_reader_never_opens_a_final_score_or_outcome_field() -> None:
    src = (Path(ck.__file__)).read_text()
    for word in ("settle", "final_score", "winner", "box_final", "home_win"):
        assert word not in src.lower(), word
