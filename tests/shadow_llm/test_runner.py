"""run_game: stub end to end, schema reject, refuse-after-tip, budget guard, retries, key hygiene,
and a mixed forward table that the existing settle path still settles."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from nba.daily.settle import settle_pending
from nba.shadow_llm.config import ShadowConfig
from nba.shadow_llm.payload import build_payload
from nba.shadow_llm.provider import (
    KEY_ENV,
    AnthropicProvider,
    ProviderError,
    StubProvider,
    prompt_sha256,
)
from nba.shadow_llm.runner import SchemaReject, parse_response, run_game
from tests.shadow_llm.conftest import GAME, NOW, TIP, World

FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE123456"


def _clock(at: datetime):  # type: ignore[no-untyped-def]
    return lambda: at


def _rows(world: World, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(world.paths["forward"]), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _calls(world: World) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(world.paths["shadow"]), read_only=True)
    try:
        return con.execute(
            "SELECT status, tokens_in, tokens_out, budget_skip, provider_real, detail "
            "FROM llm_shadow_calls ORDER BY requested_at"
        ).fetchall()
    finally:
        con.close()


class RecordingProvider:
    """Returns a fixed raw_text; counts calls."""

    is_real = False
    model_id = "rec-1"

    def __init__(self, raw: str | None = None, fail_times: int = 0) -> None:
        self.raw, self.fail_times, self.calls = raw, fail_times, 0

    def forecast(self, game_payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError("transport down")
        raw = (
            self.raw if self.raw is not None else StubProvider().forecast(game_payload)["raw_text"]
        )
        return {
            "raw_text": raw,
            "model_id": self.model_id,
            "prompt_sha256": prompt_sha256(),
            "usage": {"tokens_in": 1000, "tokens_out": 200},
        }


def _go(world: World, provider: Any, at: datetime, **kw: Any) -> dict[str, Any]:
    return run_game(
        GAME, provider=provider, now=NOW, paths=world.paths, cfg=kw.pop("cfg", world.cfg),
        clock=_clock(at), sleep=lambda s: None, **kw,
    )  # fmt: skip


def test_stub_end_to_end_rows_and_fields(world: World) -> None:
    out = _go(world, StubProvider(), NOW + timedelta(seconds=40))
    legs = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)["legs"]
    assert out["status"] == "ok" and out["rows_written"] == len(legs) == 4
    rows = _rows(
        world,
        "SELECT made_at, tipoff, version, target, player_id, prediction FROM forward_predictions "
        "WHERE model_name = 'llm_shadow' ORDER BY player_id, target",
    )
    assert len(rows) == len(legs)
    for made_at, tipoff, version, target, pid, pred_json in rows:
        pred = json.loads(pred_json)
        assert version == "v1" and target in {"pts", "reb", "ast", "fg3m"} and pid > 0
        assert made_at < tipoff and made_at <= tipoff - timedelta(minutes=30)
        assert 0.01 <= pred["p_over"] <= 0.99 and pred["line"] % 1 == 0.5
        assert len(pred["reason"]) <= 200 and pred["book"] == "draftkings"
        for k in ("prompt_sha256", "payload_sha256", "model_id", "tokens_in", "tokens_out"):
            assert k in pred
    calls = _calls(world)
    assert calls == [("ok", calls[0][1], calls[0][2], False, False, f"{len(legs)} rows")]
    # idempotent: a second pass writes nothing
    again = _go(world, StubProvider(), NOW + timedelta(minutes=1))
    assert again["status"] == "already_written"
    assert _rows(world, "SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow'")[
        0
    ][0] == len(legs)


@pytest.mark.parametrize("minutes_before_tip", [29, 0.5, -3])
def test_write_after_tip_minus_30_is_refused_and_logged(
    world: World, minutes_before_tip: float, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    at = TIP - timedelta(minutes=minutes_before_tip)
    out = _go(world, StubProvider(), at)
    assert out["status"] == "post_tip_refused" and out["rows_written"] == 0
    assert (
        _rows(world, "SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow'")[0][0]
        == 0
    )
    assert "post_tip_refused" in caplog.text
    assert _calls(world)[0][0] == "post_tip_refused"


def test_store_itself_refuses_a_late_stamp(world: World) -> None:
    from nba.daily.store import ForwardPrediction, LeakageError, append_predictions

    con = duckdb.connect(str(world.paths["forward"]))
    with pytest.raises(LeakageError):
        append_predictions(
            con, "x", TIP, [ForwardPrediction(GAME, TIP, "llm_shadow", "v1", "pts", {}, 11)]
        )
    con.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: "```json\n" + json.dumps(d) + "\n```",  # markdown fence
        lambda d: json.dumps({**d, "extra": 1}),
        lambda d: json.dumps({"forecasts": d["forecasts"][:-1]}),  # missing leg
        lambda d: json.dumps({"forecasts": d["forecasts"] + d["forecasts"][:1]}),  # extra leg
        lambda d: json.dumps(
            {"forecasts": [{**d["forecasts"][0], "p_over": 1.5}, *d["forecasts"][1:]]}
        ),
        lambda d: json.dumps(
            {"forecasts": [{**d["forecasts"][0], "p_over": 0.001}, *d["forecasts"][1:]]}
        ),
        lambda d: json.dumps(
            {"forecasts": [{**d["forecasts"][0], "line": 99.5}, *d["forecasts"][1:]]}
        ),
        lambda d: json.dumps(
            {"forecasts": [{**d["forecasts"][0], "reason": "x" * 201}, *d["forecasts"][1:]]}
        ),
        lambda d: json.dumps(
            {"forecasts": [{**d["forecasts"][0], "p_over": True}, *d["forecasts"][1:]]}
        ),
        lambda d: json.dumps({"forecasts": list(reversed(d["forecasts"]))}),  # wrong order
        lambda d: "Sure! Here you go: " + json.dumps(d),
        lambda d: "not json at all",
    ],
)
def test_schema_reject_writes_nothing_and_is_not_retried(world: World, mutate: Any) -> None:
    good = json.loads(
        StubProvider().forecast(build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg))[
            "raw_text"
        ]
    )
    prov = RecordingProvider(raw=mutate(good))
    out = _go(world, prov, NOW + timedelta(seconds=40))
    assert out["status"] == "schema_violation" and out["rows_written"] == 0
    assert prov.calls == 1  # never retried
    assert (
        _rows(world, "SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow'")[0][0]
        == 0
    )
    status, tin, tout, *_ = _calls(world)[0]
    assert (status, tin, tout) == ("schema_violation", 1000, 200)  # spend is still booked


def test_parse_response_accepts_exact_schema_only() -> None:
    legs = [{"player_id": 1, "stat": "pts", "line": 20.5}]
    ok = '{"forecasts":[{"player_id":1,"stat":"pts","line":20.5,"p_over":0.4,"reason":"r"}]}'
    assert parse_response(ok, legs)[0]["p_over"] == 0.4
    with pytest.raises(SchemaReject):
        parse_response(ok.replace("0.4", "0.995"), legs)


def test_transport_retries_only_before_tip_minus_45(world: World) -> None:
    flaky = RecordingProvider(fail_times=2)
    out = _go(world, flaky, NOW + timedelta(seconds=40))
    assert out["status"] == "ok" and flaky.calls == 3  # 2 retries then success
    never = RecordingProvider(fail_times=99)
    w2 = replace(world, cfg=replace(world.cfg, transport_retries=2))
    out2 = run_game(
        "0022600099", provider=never, now=NOW, paths=w2.paths, cfg=w2.cfg,
        clock=_clock(TIP - timedelta(minutes=44)), sleep=lambda s: None,
    )  # fmt: skip
    # every call fails and the clock is past tip-45: exactly one attempt, no retry
    assert out2["status"] == "provider_error" and never.calls == 1


def _fake_client_factory(behaviour: Any):  # type: ignore[no-untyped-def]
    def factory(**kw: Any) -> Any:
        return SimpleNamespace(messages=SimpleNamespace(create=behaviour))

    return factory


def _ok_response(raw: str, model: str = "m") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=raw)],
        usage=SimpleNamespace(input_tokens=4000, output_tokens=900),
        model=model,
    )


def test_budget_guard_refuses_before_the_call(world: World) -> None:
    def boom(**kw: Any) -> Any:
        raise AssertionError("the provider must not be called")

    env = {KEY_ENV: FAKE_KEY}
    real = AnthropicProvider(world.cfg, client_factory=_fake_client_factory(boom), environ=env)
    # (a) monthly USD cap unset -> real provider refused
    no_cap = replace(world.cfg, monthly_cap_usd=None)
    out = _go(world, real, NOW, cfg=no_cap, environ=env)
    assert out["status"] == "monthly_cap_usd_not_set"
    # (b) nightly ceiling: 145,000 spent + 8,500 planned > 150,000 -> budget_skip, flagged
    from nba.shadow_llm.calls import open_shadow, record_call

    con = open_shadow(world.paths["shadow"])
    record_call(con, game_id="other", slate=NOW.date(), requested_at=NOW, received_at=NOW,
                status="ok", tokens_in=135_000, tokens_out=10_000)  # fmt: skip
    con.close()
    out = _go(world, real, NOW, environ=env)
    assert out["status"] == "budget_skip"
    assert any(c[0] == "budget_skip" and c[3] is True for c in _calls(world))
    # (c) monthly token backstop
    tight = replace(world.cfg, nightly_token_ceiling=10**9, monthly_token_backstop=100_000)
    assert _go(world, real, NOW, cfg=tight, environ=env)["status"] == "monthly_backstop"
    # (d) no key -> nothing sent
    assert _go(world, real, NOW, environ={})["status"] == "key_missing"


def test_stub_spend_never_counts_toward_the_budget(world: World) -> None:
    assert _go(world, StubProvider(), NOW + timedelta(seconds=40))["status"] == "ok"
    from nba.shadow_llm.calls import open_shadow, tokens_day

    con = open_shadow(world.paths["shadow"])
    assert tokens_day(con, NOW.date()) == 0
    con.close()


def test_model_fallback_and_temperature_omission(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    seen: list[dict[str, Any]] = []
    good = StubProvider().forecast(build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg))[
        "raw_text"
    ]

    class NotFound(Exception):
        status_code = 404

    class BadTemp(Exception):
        status_code = 400

    def create(**kw: Any) -> Any:
        seen.append(kw)
        if kw["model"] == world.cfg.model_id:
            raise NotFound("model not found")
        if "temperature" in kw:
            raise BadTemp("temperature is deprecated for this model")
        return _ok_response(good, kw["model"])

    env = {KEY_ENV: FAKE_KEY}
    prov = AnthropicProvider(world.cfg, client_factory=_fake_client_factory(create), environ=env)
    out = _go(world, prov, NOW + timedelta(seconds=40), environ=env)
    assert out["status"] == "ok"
    assert seen[0]["model"] == world.cfg.model_id and seen[0]["temperature"] == 0.0
    assert seen[-1]["model"] == world.cfg.fallback_model_id and "temperature" not in seen[-1]
    assert seen[-1]["max_tokens"] == 3000
    assert "model_fallback" in caplog.text and "temperature rejected" in caplog.text
    # real-provider spend is booked
    assert _calls(world)[-1][1:3] == (4000, 900) and _calls(world)[-1][4] is True


def test_key_never_appears_in_logs_rows_or_errors(
    world: World, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)

    def leaky(**kw: Any) -> Any:
        raise RuntimeError(f"401 invalid x-api-key {FAKE_KEY} (Authorization: Bearer {FAKE_KEY})")

    env = {KEY_ENV: FAKE_KEY}
    prov = AnthropicProvider(world.cfg, client_factory=_fake_client_factory(leaky), environ=env)
    cfg = replace(world.cfg, transport_retries=1)
    soon = _clock(NOW + timedelta(seconds=9))
    out = run_game(GAME, provider=prov, now=NOW, paths=world.paths, cfg=cfg,
                   clock=soon, sleep=lambda s: None, environ=env)  # fmt: skip
    assert out["status"] == "provider_error"
    blob = json.dumps(out) + caplog.text + capsys.readouterr().out + repr(_calls(world))
    assert FAKE_KEY not in blob and "sk-ant" not in blob
    # an answer that echoes the key is scrubbed before it can reach a log or a row
    echo = _ok_response("not json " + FAKE_KEY)
    prov2 = AnthropicProvider(
        world.cfg, client_factory=_fake_client_factory(lambda **kw: echo), environ=env
    )
    out2 = _go(world, prov2, NOW + timedelta(seconds=40), environ=env)
    assert out2["status"] == "schema_violation"
    assert FAKE_KEY not in caplog.text and FAKE_KEY not in repr(_calls(world))


def test_mixed_forward_table_still_settles(world: World) -> None:
    """llm_shadow rows (p_over, no mean/std) beside production rows: settle_pending must not crash,
    scores production, and leaves llm_shadow alone (its companion scorer reads p_over)."""
    assert _go(world, StubProvider(), NOW + timedelta(seconds=40))["status"] == "ok"
    con = duckdb.connect(str(world.paths["forward"]))
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES (?,?,?,?,?,?,?)",
        [GAME, TIP.date(), 2026, 1610612738, 1610612755, 110, 101],
    )
    for pid in (11, 12, 13, 21):
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, "
            "ast, fg3m) VALUES (?,?,?,?,?,?,?,?)",
            [GAME, pid, 1610612738, 30.0, 20, 5, 4, 2],
        )
    res = settle_pending(con, TIP + timedelta(hours=4))
    names = {r[0] for r in con.execute("SELECT DISTINCT model_name FROM forward_scores").fetchall()}
    con.close()
    assert res["prop_scored"] > 0
    assert "llm_shadow" not in names and "props_context_residual" in names


def test_config_default_is_unset_cap() -> None:
    assert ShadowConfig().monthly_cap_usd is None
    assert ShadowConfig().nightly_token_ceiling == 150_000
