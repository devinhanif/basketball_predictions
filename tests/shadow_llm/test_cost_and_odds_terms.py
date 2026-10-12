"""Cost controls and the URL/title odds flag (Devin 2026-10-12, "yes to 2 and 3"), against the FAKE
``claude`` binary and the stub (no network): at most 3 web searches per call (a 4th discards the
call as ``search_cap_exceeded``); a deterministic, unpickable sample of at most 5 games per night
(sha256(game_id + salt) rank, the rest ``not_sampled`` with no call); betting terms in a result URL
path or title flag the call (``odds_url_terms``)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from nba.shadow_llm import run as run_mod
from nba.shadow_llm.calls import open_shadow, record_call
from nba.shadow_llm.claude_code import SYSTEM_PROMPT_WEB, odds_terms_in_url_or_title
from nba.shadow_llm.config import load_config
from nba.shadow_llm.provider import StubProvider
from nba.shadow_llm.run import DEGRADED, run, sample_rank, sampled_games
from tests.shadow_llm.conftest import GAME, NOW, World
from tests.shadow_llm.test_claude_code import (
    AT,
    calls,
    fake_claude,
    first_pred,
    go,
    good_answer,
    make_provider,
    stream,
)

SI_URL = (
    "https://www.si.com/nba/spurs/news/"
    "utah-jazz-vs-san-antonio-spurs-gameday-preview-how-to-watch-betting-odds-injury-report"
)
ENV = {"NBA_LLM_SHADOW": "1"}


# --- search cap -------------------------------------------------------------------------------


def test_prompt_caps_searches_at_three_and_config_matches() -> None:
    assert "AT MOST 3 times" in SYSTEM_PROMPT_WEB
    assert load_config().max_searches_per_call == 3


def test_three_searches_ok_fourth_is_discarded_and_degraded(world: World) -> None:
    q3 = ("a injury", "b rest", "c lineup")
    exe = fake_claude(world.tmp, stream(good_answer(world), queries=q3))
    assert go(world, make_provider(world, exe, AT), AT)["status"] == "ok"

    other = World({**world.paths, "shadow": world.tmp / "s2" / "s.duckdb"}, world.cfg, world.tmp)
    exe = fake_claude(world.tmp, stream(good_answer(world), queries=(*q3, "d minutes")))
    out = go(other, make_provider(other, exe, AT), AT)
    assert out["status"] == "search_cap_exceeded" and out["rows_written"] == 0
    assert "4 searches > 3" in out["detail"]
    rows = calls(other, "status, n_searches, provider")
    assert rows == [("search_cap_exceeded", 4, "claude_code")]  # one row, audit kept, no retry
    assert "search_cap_exceeded" in DEGRADED


# --- nightly sample ---------------------------------------------------------------------------


def test_sample_is_deterministic_salted_and_capped() -> None:
    ids = [f"00226{i:05d}" for i in range(12)]
    cfg = load_config()
    assert cfg.max_games_per_night == 5 and cfg.sample_salt == "nba-llm-shadow-2026-27-sample-v1"
    a = sampled_games(ids, cfg)
    assert len(a) == 5 and a == sampled_games(list(reversed(ids)), cfg)  # input order irrelevant
    assert a == set(sample_rank(ids, cfg.sample_salt)[:5])
    assert sampled_games(ids, replace(cfg, sample_salt="other")) != a  # the salt decides, not us
    assert sampled_games(ids, replace(cfg, max_games_per_night=0)) == set(ids)
    assert sampled_games(ids[:3], cfg) == set(ids[:3])


def _salt_excluding(game: str, others: list[str], k: int) -> str:
    for i in range(1000):
        salt = f"test-salt-{i}"
        if game not in sample_rank([game, *others], salt)[:k]:
            return salt
    raise AssertionError("no salt found")


def test_game_outside_the_sample_is_not_sampled_once_and_never_called(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    others = [f"00226{i:05d}" for i in range(500, 509)]
    salt = _salt_excluding(GAME, others, 5)
    cfg = replace(world.cfg, sample_salt=salt, max_games_per_night=5)
    monkeypatch.setattr(run_mod, "slate_games", lambda con, slate: sorted([GAME, *others]))

    class Boom(StubProvider):
        def forecast(self, game_payload: dict) -> dict:  # type: ignore[type-arg]
            raise AssertionError("a game outside the sample must never be called")

    for _ in range(2):  # two ticks inside the window: one not_sampled row only
        s = run(NOW.date(), provider=Boom(), cfg=cfg, paths=world.paths, allow_stub_write=True,
                environ=ENV, now=NOW, clock=lambda: NOW + timedelta(seconds=5))  # fmt: skip
        assert [r.status for r in s.results] == ["not_sampled"] and s.rc == 0
    assert calls(world, "status, provider_real, tokens_in") == [("not_sampled", False, 0)]
    con = duckdb.connect(str(world.paths["forward"]), read_only=True)
    n = con.execute("SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow'")
    assert n.fetchone() == (0,)
    con.close()


def test_hard_cap_holds_when_the_sample_already_has_k_called_games(world: World) -> None:
    cfg = replace(world.cfg, max_games_per_night=1)
    con = open_shadow(Path(world.paths["shadow"]))
    record_call(con, game_id="0022600001", slate=NOW.date(), requested_at=NOW, received_at=NOW,
                status="ok", provider_real=True)  # fmt: skip
    con.close()
    s = run(NOW.date(), provider=StubProvider(), cfg=cfg, paths=world.paths, allow_stub_write=True,
            environ=ENV, now=NOW, clock=lambda: NOW + timedelta(seconds=5))  # fmt: skip
    assert [r.status for r in s.results] == ["not_sampled"]
    assert "cap 1 reached" in s.results[0].detail


def test_sampled_game_is_called_normally(world: World) -> None:
    s = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths,
            allow_stub_write=True, environ=ENV, now=NOW,
            clock=lambda: NOW + timedelta(seconds=5))  # fmt: skip
    assert [r.status for r in s.results] == ["ok"]


# --- odds terms in URL path / title -----------------------------------------------------------


def test_odds_terms_helper_hits_and_misses() -> None:
    assert odds_terms_in_url_or_title([SI_URL], []) == ["betting", "odds"]
    hits = {
        "https://x.com/nba/knicks-moneyline-pick": "moneyline",
        "https://x.com/a/point-spread-tonight": "spread",
        "https://x.com/a/over-under-224": "over-under",
        "https://x.com/a/nba-prop-bets": "prop-bet",
        "https://x.com/a/nba-picks-tonight": "picks",
        "https://x.com/best-bets/monday": "best-bets",
        "https://x.com/a/same-game-parlay": "parlay",
        "https://x.com/a/sportsbook-review": "sportsbook",
        "https://x.com/a/line-movement-alert": "line-movement",
    }
    for url, term in hits.items():
        assert odds_terms_in_url_or_title([url], []) == [term], url
    assert odds_terms_in_url_or_title([], ["Spurs vs Jazz: Over/Under and Best Bets"]) == [
        "best-bets", "over-under",
    ]  # fmt: skip
    for clean in (
        "https://x.com/a/spreadsheet-of-minutes",
        "https://x.com/a/oddsmaker-profile",  # not the word "odds"
        "https://x.com/a/2027-draft-picks-trade",
        "https://www.nba.com/news/injury-report?ref=odds",  # the query string is not the path
    ):
        assert odds_terms_in_url_or_title([clean], []) == [], clean


def test_si_betting_odds_url_from_the_dry_run_flags_the_call(world: World) -> None:
    exe = fake_claude(
        world.tmp,
        stream(good_answer(world), queries=("Spurs Jazz injury report",),
               result_urls=(SI_URL, "https://www.nba.com/news/x")),
    )  # fmt: skip
    out = go(world, make_provider(world, exe, AT), AT)
    assert out["status"] == "ok"
    r = calls(world, "odds_domain_flag, odds_domains, odds_url_terms")[0]
    assert r[0] is True and json.loads(r[1]) == [] and json.loads(r[2]) == ["betting", "odds"]
    assert first_pred(world)["odds_domain_flag"] is True


def test_title_only_hit_flags_and_clean_call_does_not(world: World, tmp_path: Path) -> None:
    exe = fake_claude(world.tmp, stream(good_answer(world), queries=("q",),
                                        result_urls=("https://www.nba.com/news/x",)))  # fmt: skip
    assert go(world, make_provider(world, exe, AT), AT)["status"] == "ok"
    assert calls(world, "odds_domain_flag, odds_url_terms")[0] == (False, "[]")
    links = 'Links: [{"title":"Knicks vs Wolves Picks and Parlay","url":"u"}]'
    titled = stream(good_answer(world), queries=("q",), snippet=links)
    other = World({**world.paths, "shadow": tmp_path / "s3.duckdb"}, world.cfg, world.tmp)
    exe = fake_claude(world.tmp, titled)
    go(other, make_provider(other, exe, AT), AT)
    r = calls(other, "odds_domain_flag, odds_url_terms")[0]
    assert r[0] is True and json.loads(r[1]) == ["parlay", "picks"]
