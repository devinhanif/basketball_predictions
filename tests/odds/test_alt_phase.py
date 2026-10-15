"""History phase ``alt`` (2026-10-14): the player ``*_alternate`` ladders for seasons 2023 and 2024
ONLY, t60 only, own ledger and cap, season 2025 refused in code, existing rows never touched.
Fake HTTP; no network, no key, no ``nba.duckdb``."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from nba.odds import history_pull as hp
from nba.odds import the_odds_api as api
from nba.odds.__main__ import main
from nba.odds.theoddsapi import NameResolver
from tests.odds.test_the_odds_api import GAME, FakeHttp, make_client

ALT = (
    "player_points_alternate",
    "player_rebounds_alternate",
    "player_assists_alternate",
)


@pytest.fixture
def acfg(tmp_path: Path) -> api.HistoryConfig:
    return api.HistoryConfig(
        min_interval_s=0.0,
        retry_backoff_s=0.0,
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "budget" / "ledger.json",
        state_path=tmp_path / "state.jsonl",
        out_db=tmp_path / "odds_history.duckdb",
        games_db=tmp_path / "nba_missing.duckdb",
        schedule_dir=tmp_path / "sched",
        alt_markets=ALT,
        alt_ledger_path=tmp_path / "budget" / "alt.json",
        alt_max_credits=1_000,
    )


def kw(cfg: api.HistoryConfig, **extra: Any) -> dict[str, Any]:
    return {
        "cfg": cfg, "games": [GAME], "all_season_games": {2023: [], 2024: [GAME], 2025: []},
        "resolver": api.default_resolver(), **extra,
    }  # fmt: skip


def test_alt_estimate_is_t60_only_us_eu_and_on_its_own_cap(acfg: api.HistoryConfig) -> None:
    rep = hp.run_pull(2024, "alt", dry_run=True, **kw(acfg))
    # one snapshot (t60) x (events 1 + 10 credits x 3 markets x 2 regions {us, eu}) = 61
    assert rep.estimate["credits_total"] == 61 and rep.estimate["odds_calls"] == 1
    assert rep.refused is None
    tight = replace(acfg, alt_max_credits=60)
    assert hp.run_pull(2024, "alt", **kw(tight)).refused  # 61 > the alt cap, not 1.75M
    assert hp.run_pull(2024, "props", dry_run=True, **kw(tight)).refused is None


def test_season_2025_is_refused_in_code_before_anything_is_read(acfg: api.HistoryConfig) -> None:
    # games_db does not exist: a refusal AFTER loading games would raise a DuckDB error instead
    with pytest.raises(api.HoldoutRefused, match="frozen holdout"):
        hp.run_pull(2025, "alt", dry_run=True, cfg=acfg)
    with pytest.raises(api.HoldoutRefused):
        hp.run_pull(2025, "alt", cfg=acfg, games=[GAME])  # not a dry run either
    with pytest.raises(api.HoldoutRefused):
        hp.run_pull(2022, "alt", dry_run=True, cfg=acfg)  # outside alt_seasons
    with pytest.raises(api.HoldoutRefused):
        replace(acfg, alt_seasons=(2023, 2024, 2025))  # the config itself cannot name it


def test_config_file_naming_2025_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"alt_seasons": [2023, 2024, 2025], "alt_markets": ["x"]}))
    with pytest.raises(api.HoldoutRefused):
        api.load_config(p)


def test_cli_refuses_2025_alt_with_the_informational_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["history-pull", "--phase", "alt", "--season", "2025", "--dry-run"])
    assert rc == 4  # RC_INFORMATIONAL: refused, nothing requested
    assert "HoldoutRefused" in capsys.readouterr().err


def test_alt_refuses_marketless_config_and_markets_override(acfg: api.HistoryConfig) -> None:
    with pytest.raises(ValueError, match="alt_markets"):
        hp.run_pull(2024, "alt", dry_run=True, **kw(replace(acfg, alt_markets=())))
    with pytest.raises(ValueError, match="--markets"):
        hp.run_pull(2024, "alt", dry_run=True, markets=list(ALT[:1]), **kw(acfg))


def test_alt_pull_uses_own_ledger_state_key_regions_and_resumes(acfg: api.HistoryConfig) -> None:
    fake = FakeHttp(regions=2)
    ac = replace(
        acfg, regions=acfg.alt_regions, us_ex_from=None, ledger_path=acfg.alt_ledger_path,
        max_credits=acfg.alt_max_credits,
    )  # fmt: skip
    rep = hp.run_pull(2024, "alt", client=make_client(ac, fake), **kw(acfg))
    assert rep.items_done == 1 and not rep.errors and not rep.match_failures
    (odds_call,) = [c for c in fake.calls if str(c["url"]).endswith("/odds")]
    assert odds_call["params"]["regions"] == "us,eu"  # us_ex is not requested
    assert odds_call["params"]["markets"] == ",".join(ALT)
    st = hp.PullState(acfg.state_path)
    assert st.done(hp.item_key(GAME.game_id, "t60", "alt"))
    assert not st.done(hp.item_key(GAME.game_id, "t5", "alt"))  # one snapshot per game
    assert acfg.alt_ledger_path.exists() and not acfg.ledger_path.exists()  # own ledger
    fake2 = FakeHttp(regions=2)
    again = hp.run_pull(2024, "alt", client=make_client(ac, fake2), **kw(acfg))
    assert again.items_skipped_done == 1 and fake2.calls == []


def test_alt_write_never_deletes_other_markets(acfg: api.HistoryConfig) -> None:
    store = hp.OddsStore(acfg.out_db)
    main_body = {
        "timestamp": "2025-01-15T23:10:00Z",
        "data": {
            "id": "e1", "home_team": "New York Knicks", "away_team": "Philadelphia 76ers",
            "bookmakers": [{"key": "fanduel", "markets": [
                {"key": "player_points", "outcomes": [
                    {"name": "Over", "description": "Jalen Brunson", "price": -110, "point": 25.5},
                    {"name": "Under", "description": "Jalen Brunson", "price": -110, "point": 25.5},
                ]},
                {"key": "player_points_alternate", "outcomes": [
                    {"name": "Over", "description": "Jalen Brunson", "price": 250, "point": 35.5},
                ]},
            ]}],
        },
    }  # fmt: skip
    res = NameResolver(teams={}, players={"jalen brunson": 1628973})
    when = datetime(2025, 1, 15, 23, 10, tzinfo=UTC)

    def parsed(markets: tuple[str, ...]) -> Any:
        pr = api.parse_event_odds(
            main_body, season=2024, game_id=GAME.game_id, snapshot_kind="t60",
            requested_at=when, raw_file="x", resolver=res,
        )  # fmt: skip
        return pr.rows.filter(pr.rows["market"].is_in(list(markets)))

    store.replace(GAME.game_id, "t60", ["player_points"], parsed(("player_points",)))
    store.replace(GAME.game_id, "t60", list(ALT), parsed(("player_points_alternate",)))
    store.replace(GAME.game_id, "t60", list(ALT), parsed(("player_points_alternate",)))  # rerun
    con = duckdb.connect(str(acfg.out_db), read_only=True)
    try:
        got = con.execute(
            "SELECT market, count(*) FROM odds_history GROUP BY 1 ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    assert got == [("player_points", 2), ("player_points_alternate", 1)]  # idempotent, additive


def test_real_config_lists_the_alt_markets_for_2023_and_2024_only() -> None:
    c = api.load_config()
    assert len(c.alt_markets) == 11 and all(m.endswith("_alternate") for m in c.alt_markets)
    assert {
        "player_points_alternate", "player_rebounds_alternate", "player_assists_alternate",
        "player_threes_alternate", "player_blocks_alternate", "player_steals_alternate",
        "player_turnovers_alternate", "player_points_assists_alternate",
        "player_points_rebounds_alternate", "player_rebounds_assists_alternate",
        "player_points_rebounds_assists_alternate",
    } == set(c.alt_markets)  # fmt: skip
    assert c.alt_seasons == (2023, 2024) and 2025 not in c.alt_seasons
    assert c.alt_snapshots == ("t60",) and c.alt_regions == ("us", "eu")
    assert c.alt_max_credits == 1_000_000  # the "stop and ask" line
    assert c.cost_per_market_region * len(c.alt_markets) * len(c.alt_regions) == 220
