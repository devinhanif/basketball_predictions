from __future__ import annotations

from nba.ride.plan import Obs, Position, check


def pos() -> Position:
    return Position("CLE@PHI", "KXNBAGAME-X-PHI", "PHI", 0.97, 5, 0.01, 0.0)


def ob(
    ts: float,
    bid: float | None,
    ours: float = 0.985,
    lead: int = 15,
    secs: float = 400.0,
    final: bool = False,
    won: bool | None = None,
) -> Obs:
    return Obs(ts, bid, ours, lead, secs, final, won)


def test_quiet_game_makes_no_lines() -> None:
    p = pos()
    for i in range(30):
        assert check(p, ob(i * 60.0, 0.97 + (0.005 if i % 2 else 0.0))) == []


def test_stop_below_entry_is_35_percent_of_entry_and_fires_once() -> None:
    p = pos()
    assert p.stop_level(__import__("nba.ride.gate", fromlist=["RideConfig"]).RideConfig()) == round(
        0.35 * 0.97, 4
    )
    lines = check(p, ob(60.0, 0.30))
    assert any(line.startswith("STOP LEVEL") for line in lines)
    assert not any(line.startswith("STOP LEVEL") for line in check(p, ob(120.0, 0.25)))


def test_stop_trails_at_60_percent_of_peak_once_in_profit() -> None:
    from nba.ride.gate import RideConfig

    p = pos()
    check(p, ob(0.0, 0.99))
    assert p.in_profit and p.peak_bid == 0.99
    assert p.stop_level(RideConfig()) == round(0.6 * 0.99, 4)


def test_trend_needs_25_percent_over_about_ten_minutes() -> None:
    p = pos()
    check(p, ob(0.0, 0.97))
    assert not [
        x for x in check(p, ob(300.0, 0.80)) if x.startswith("TREND")
    ]  # too soon for the window
    lines = check(p, ob(660.0, 0.70))
    assert any(line.startswith("TREND") for line in lines)


def test_thesis_broken_late_small_lead_or_low_prob() -> None:
    p = pos()
    assert any(x.startswith("THESIS BROKEN") for x in check(p, ob(0.0, None, ours=0.80, lead=6)))
    q = pos()
    assert any(
        x.startswith("THESIS BROKEN")
        for x in check(q, ob(0.0, None, ours=0.93, lead=8, secs=300.0))
    )
    r = pos()
    assert not check(r, ob(0.0, None, ours=0.97, lead=8, secs=300.0))


def test_settle_win_and_loss_once() -> None:
    p = pos()
    lines = check(p, ob(0.0, None, final=True, won=True))
    assert lines and lines[0].startswith("SETTLED WIN") and p.settled
    assert check(p, ob(1.0, None, final=True, won=True)) == []
    q = pos()
    assert check(q, ob(0.0, None, final=True, won=False))[0].startswith("SETTLED LOSS")
