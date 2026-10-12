"""History phase ``periods`` (2026-10-12): period markets + q1 player props for 2023/2024 only, t60
only, own ledger and cap, no us_ex, never touches the props/games state or ledger. Fake HTTP."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nba.odds import history_pull as hp
from nba.odds import the_odds_api as api
from tests.odds.test_the_odds_api import GAME, FakeHttp, make_client

PERIODS = (
    "h2h_q1", "spreads_q1", "totals_q1", "team_totals_q1", "player_points_q1",
)  # fmt: skip


@pytest.fixture
def pcfg(tmp_path: Path) -> api.HistoryConfig:
    return api.HistoryConfig(
        min_interval_s=0.0,
        retry_backoff_s=0.0,
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "budget" / "ledger.json",
        state_path=tmp_path / "state.jsonl",
        out_db=tmp_path / "odds_history.duckdb",
        games_db=tmp_path / "nba_missing.duckdb",
        schedule_dir=tmp_path / "sched",
        period_markets=PERIODS,
        period_ledger_path=tmp_path / "budget" / "periods.json",
        period_max_credits=1_000,
    )


def kw(cfg: api.HistoryConfig, **extra: Any) -> dict[str, Any]:
    return {
        "cfg": cfg, "games": [GAME], "all_season_games": {2023: [], 2024: [GAME], 2025: []},
        "resolver": api.default_resolver(), **extra,
    }  # fmt: skip


def test_periods_estimate_is_t60_only_us_eu_and_on_its_own_cap(pcfg: api.HistoryConfig) -> None:
    rep = hp.run_pull(2024, "periods", dry_run=True, **kw(pcfg))
    # one snapshot (t60) x (events 1 + 10 credits x 5 markets x 2 regions {us, eu}) = 101
    assert rep.estimate["credits_total"] == 101 and rep.estimate["odds_calls"] == 1
    assert rep.refused is None
    tight = replace(pcfg, period_max_credits=100)
    assert hp.run_pull(2024, "periods", **kw(tight)).refused  # 101 > the periods cap, not 1.75M
    # props/games are untouched by the periods cap and still plan both snapshots
    other = hp.run_pull(2024, "props", dry_run=True, **kw(tight))
    assert other.refused is None


def test_periods_refuses_other_seasons_and_marketless_config(pcfg: api.HistoryConfig) -> None:
    with pytest.raises(ValueError, match="covers seasons"):
        hp.run_pull(2025, "periods", dry_run=True, **kw(pcfg))
    with pytest.raises(ValueError, match="period_markets"):
        hp.run_pull(2024, "periods", dry_run=True, **kw(replace(pcfg, period_markets=())))
    with pytest.raises(ValueError, match="--markets"):
        hp.run_pull(2024, "periods", dry_run=True, markets=["h2h_q1"], **kw(pcfg))


def test_periods_pull_writes_to_own_ledger_state_key_and_is_resumable(
    pcfg: api.HistoryConfig,
) -> None:
    fake = FakeHttp(regions=2)
    # run_pull re-points cfg at the period regions/ledger; an injected client must carry the same
    pc = replace(
        pcfg, regions=pcfg.period_regions, us_ex_from=None, ledger_path=pcfg.period_ledger_path,
        max_credits=pcfg.period_max_credits,
    )  # fmt: skip
    rep = hp.run_pull(2024, "periods", client=make_client(pc, fake), **kw(pcfg))
    assert rep.items_done == 1 and not rep.errors and not rep.match_failures
    (odds_call,) = [c for c in fake.calls if str(c["url"]).endswith("/odds")]
    assert odds_call["params"]["regions"] == "us,eu"
    assert odds_call["params"]["markets"] == ",".join(PERIODS)
    st = hp.PullState(pcfg.state_path)
    assert st.done(hp.item_key(GAME.game_id, "t60", "periods"))
    assert not st.done(hp.item_key(GAME.game_id, "t5", "periods"))  # no t5 for periods
    assert pcfg.period_ledger_path.exists() and not pcfg.ledger_path.exists()  # own ledger
    fake2 = FakeHttp(regions=2)
    again = hp.run_pull(2024, "periods", client=make_client(pc, fake2), **kw(pcfg))
    assert again.items_skipped_done == 1 and fake2.calls == []


def test_only_games_filter_selects_the_named_game(pcfg: api.HistoryConfig) -> None:
    none = hp.run_pull(2024, "periods", dry_run=True, only_games=["nope"], **kw(pcfg))
    assert none.estimate["odds_calls"] == 0
    one = hp.run_pull(2024, "periods", dry_run=True, only_games=[GAME.game_id], **kw(pcfg))
    assert one.estimate["odds_calls"] == 1


def test_real_config_lists_the_requested_period_markets() -> None:
    c = api.load_config()
    assert len(c.period_markets) == 27 and c.period_seasons == (2023, 2024)
    assert c.period_snapshots == ("t60",) and c.period_regions == ("us", "eu")
    for p in ("q1", "q2", "q3", "q4", "h1", "h2"):
        for m in ("h2h", "spreads", "totals", "team_totals"):
            assert f"{m}_{p}" in c.period_markets
    assert {"player_points_q1", "player_rebounds_q1", "player_assists_q1"} <= set(c.period_markets)
    assert 2025 not in c.period_seasons
