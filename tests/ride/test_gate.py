from __future__ import annotations

from nba.ride.gate import GateInput, RideConfig, evaluate


def gi(**kw: object) -> GateInput:
    base: dict[str, object] = dict(
        state="in",
        leader="home",
        ours=0.985,
        espn=0.982,
        heartbeat_age_s=30.0,
        data_age_s=40.0,
        quote="ok",
        already_alerted=False,
    )
    base.update(kw)
    return GateInput(**base)  # type: ignore[arg-type]


def test_ride_when_everything_agrees() -> None:
    v = evaluate(gi())
    assert v.level == "RIDE" and v.reasons == ()


def test_watch_band_logs_only() -> None:
    assert evaluate(gi(ours=0.70)).level == "WATCH"
    assert evaluate(gi(ours=0.50)).level == "NONE"
    assert evaluate(gi(state="pre")).level == "NONE"
    assert evaluate(gi(state="post")).level == "NONE"


def test_stale_heartbeat_blocks_and_says_why() -> None:
    v = evaluate(gi(heartbeat_age_s=121.0))
    assert v.level == "WATCH" and "stale_heartbeat" in v.reasons
    assert "heartbeat_missing" in evaluate(gi(heartbeat_age_s=None)).reasons


def test_stale_game_data_blocks() -> None:
    assert "stale_game_data" in evaluate(gi(data_age_s=400.0)).reasons


def test_espn_disagreement_and_missing() -> None:
    v = evaluate(gi(espn=0.90))
    assert v.level == "WATCH" and "espn_disagrees" in v.reasons
    assert evaluate(gi(espn=0.955)).level == "RIDE"  # exactly within 3 points
    assert "espn_missing" in evaluate(gi(espn=None)).reasons


def test_one_alert_per_game_and_quote_rules() -> None:
    assert "already_alerted" in evaluate(gi(already_alerted=True)).reasons
    assert evaluate(gi(quote="no_room")).level == "WATCH"
    assert evaluate(gi(ours=0.979)).level == "WATCH"
    assert evaluate(gi(), RideConfig(ride_p=0.99)).level == "WATCH"


def test_heads_up_once_fresh_and_espn_agrees() -> None:
    from nba.ride.gate import GateInput, RideConfig, heads_up

    cfg = RideConfig(heads_up_p=0.75)
    ok = GateInput("in", "home", 0.76, 0.74, 30.0, 40.0, "ok", False)
    assert heads_up(ok, cfg, already=False)
    assert not heads_up(ok, cfg, already=True)
    assert not heads_up(GateInput("in", "home", 0.74, 0.74, 30.0, 40.0, "ok", False), cfg, False)
    assert not heads_up(GateInput("in", "home", 0.80, 0.70, 30.0, 40.0, "ok", False), cfg, False)
    assert not heads_up(GateInput("in", "home", 0.80, 0.80, 300.0, 40.0, "ok", False), cfg, False)
    assert not heads_up(GateInput("in", "home", 0.80, None, 30.0, 40.0, "ok", False), cfg, False)
    assert not heads_up(ok, RideConfig(), already=False)
