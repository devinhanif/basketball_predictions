"""Team webhook loading, validation and redaction; the Discord card client and its rate limits.

All URLs here are made up (zeros and a fake secret). Nothing touches the network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from nba.jarvis.cards.colors import ABBRS, TEAMS
from nba.jarvis.cards.discord import SILENT, CardClient, RateGate
from nba.jarvis.cards.webhooks import (
    fingerprint,
    load_team_webhooks,
    parse_team_webhooks,
    redact_all,
    webhook_id,
)

SECRET = "FAKE_secret-token_000"


def url(n: int, secret: str = SECRET) -> str:
    return f"https://discord.com/api/webhooks/{100000 + n}/{secret}{n}"


def test_thirty_teams_with_a_colour_each() -> None:
    assert len(ABBRS) == 30 and len(TEAMS) == 30
    assert all(0 <= c <= 0xFFFFFF for _, c in TEAMS.values())


def test_parse_validates_each_url_and_names_teams_not_urls() -> None:
    raw = json.dumps(
        {
            "LAL": url(1),
            "lac": url(2),  # case is normalised
            "BOS": "https://evil.example/api/webhooks/1/abc",  # not a Discord webhook
            "DEN": "",  # empty slot = not configured, not invalid
            "XXX": url(3),  # not a team
            "MIA": 5,  # not a string
        }
    )
    tw = parse_team_webhooks(raw, "env")
    assert sorted(tw.urls) == ["LAC", "LAL"]
    assert sorted(tw.invalid) == ["BOS", "MIA"]
    assert tw.unknown == ["XXX"]
    assert "DEN" in tw.missing and "LAL" not in tw.missing
    line = tw.summary()
    assert "2/30" in line and "BOS" in line
    assert "discord.com" not in line and SECRET not in line


def test_a_bad_json_names_the_exception_type_never_the_text() -> None:
    tw = parse_team_webhooks("{'LAL': 'https://discord.com/api/webhooks/1/" + SECRET, "env")
    assert not tw.urls and tw.error and "JSONDecodeError" in tw.error
    assert SECRET not in (tw.error or "") and SECRET not in tw.summary()
    assert parse_team_webhooks("[1]", "env").error
    assert parse_team_webhooks("", "env").source == "none"


def test_two_teams_on_one_url_are_reported_as_shared() -> None:
    tw = parse_team_webhooks(json.dumps({"LAL": url(1), "LAC": url(1)}), "env")
    assert tw.shared == [["LAC", "LAL"]]


def test_env_wins_and_the_keychain_is_a_fallback_only_when_configured() -> None:
    env = {"V": json.dumps({"LAL": url(1)})}
    calls: list[str] = []

    def keychain(service: str) -> str | None:
        calls.append(service)
        return json.dumps({"BOS": url(2)})

    tw = load_team_webhooks("V", keychain_service="svc", env=env, keychain=keychain)
    assert tw.source == "env" and list(tw.urls) == ["LAL"] and not calls
    tw = load_team_webhooks("V", keychain_service="svc", env={}, keychain=keychain)
    assert tw.source == "keychain" and list(tw.urls) == ["BOS"] and calls == ["svc"]
    calls.clear()
    tw = load_team_webhooks("V", keychain_service="", env={}, keychain=keychain)
    assert tw.source == "none" and not tw.urls and not calls  # no service named: never asked


def test_redact_all_removes_every_url_and_secret() -> None:
    tw = parse_team_webhooks(json.dumps({"LAL": url(1), "LAC": url(2)}), "env")
    msg = (
        f"failed {url(1)} and {url(2)} also https://discordapp.com/api/webhooks/9/ZZZ and {SECRET}2"
    )
    out = tw.redact(msg)
    for u in tw.urls.values():
        assert u not in out
    assert SECRET not in out and "api/webhooks" not in out.replace("<webhook>", "")
    assert redact_all("clean text", []) == "clean text"


def test_fingerprint_is_short_and_does_not_contain_the_secret() -> None:
    f = fingerprint(url(1))
    assert len(f) == 12 and SECRET not in f and f == fingerprint(url(1)) != fingerprint(url(2))
    assert webhook_id(url(7)) == "100007"


# ------------------------------------------------------------------------------ client


class Resp:
    def __init__(self, code: int, body: dict[str, Any] | None = None, headers: dict | None = None):
        self.status_code = code
        self._body = body
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Http:
    """Scripted stand-in for httpx.Client.request."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, target: str, json: dict[str, Any] | None = None) -> Resp:
        self.calls.append((method, target, json or {}))
        r = self.script.pop(0) if self.script else Resp(200, {"id": "999"})
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s

    def now(self) -> float:
        return self.t


def client(http: Http, budget: float = 20.0) -> tuple[CardClient, Clock]:
    clk = Clock()
    return CardClient(http, RateGate(budget, clk.sleep, clk.now), secrets=[url(1)]), clk


def test_post_uses_wait_true_silent_flag_and_no_mentions_and_returns_the_message_id() -> None:
    http = Http(Resp(200, {"id": "555"}))
    c, _ = client(http)
    r = c.post(url(1), {"embeds": [{"title": "t"}]})
    assert (r.ok, r.status, r.message_id) == (True, "posted", "555")
    method, target, body = http.calls[0]
    assert method == "POST" and target == url(1) + "?wait=true"
    assert body["flags"] == SILENT and body["allowed_mentions"] == {"parse": []}


def test_edit_patches_the_message_and_is_not_flagged() -> None:
    http = Http(Resp(200, {"id": "555"}))
    c, _ = client(http)
    r = c.edit(url(1), "555", {"embeds": [{"title": "t2"}]})
    assert (r.ok, r.status, r.message_id) == (True, "edited", "555")
    method, target, body = http.calls[0]
    assert method == "PATCH" and target == f"{url(1)}/messages/555"
    assert "flags" not in body and body["allowed_mentions"] == {"parse": []}


def test_a_429_waits_retry_after_then_succeeds() -> None:
    http = Http(Resp(429, {"retry_after": 1.5, "global": False}), Resp(200, {"id": "7"}))
    c, clk = client(http)
    r = c.post(url(1), {})
    assert r.ok and r.message_id == "7" and clk.slept == [1.5] and c.gate.n_429 == 1


def test_a_429_past_the_tick_budget_is_deferred_not_failed_or_reposted() -> None:
    http = Http(Resp(429, {"retry_after": 30.0}))
    c, clk = client(http, budget=20.0)
    r = c.edit(url(1), "5", {})
    assert (r.ok, r.status, r.code) == (False, "deferred", 429)
    assert clk.slept == [] and len(http.calls) == 1


def test_a_drained_bucket_is_waited_out_before_the_next_call_to_the_same_webhook_only() -> None:
    hdr = {"x-ratelimit-remaining": "0", "x-ratelimit-reset-after": "2.0"}
    http = Http(Resp(200, {"id": "1"}, hdr), Resp(200, {"id": "2"}), Resp(200, {"id": "3"}))
    c, clk = client(http)
    c.post(url(1), {})
    c.post(url(2), {})  # another webhook, another bucket: no wait
    assert clk.slept == []
    c.edit(url(1), "1", {})  # the drained one: waits its reset
    assert clk.slept == [2.0]


def test_a_global_429_holds_every_webhook() -> None:
    http = Http(Resp(429, {"retry_after": 1.0, "global": True}), Resp(200, {"id": "1"}))
    c, clk = client(http)
    assert c.post(url(1), {}).ok
    assert clk.slept == [1.0]
    assert c.gate.wait_needed("another") == 0.0  # the wait was already served


def test_404_is_gone_and_other_4xx_failed_and_5xx_deferred() -> None:
    http = Http(Resp(404, {"code": 10008}), Resp(400), Resp(503))
    c, _ = client(http)
    assert c.edit(url(1), "5", {}).status == "gone"
    assert c.edit(url(1), "5", {}).status == "failed"
    assert c.edit(url(1), "5", {}).status == "deferred"


def test_a_transport_error_is_deferred_and_its_text_never_carries_the_url() -> None:
    http = Http(httpx.ConnectError(f"cannot reach {url(1)}"))
    c, _ = client(http)
    r = c.post(url(1), {})
    assert r.status == "deferred" and "ConnectError" in r.note
    assert url(1) not in r.note and SECRET not in r.note


def test_a_200_without_an_id_is_ok_but_has_no_message_id() -> None:
    http = Http(Resp(204))
    c, _ = client(http)
    r = c.post(url(1), {})
    assert r.ok and r.message_id is None
