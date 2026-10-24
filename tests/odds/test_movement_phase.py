"""History phase ``movement`` (2026-10-23): pre-T-60 line-movement snapshots at named books for
seasons 2023 and 2024 ONLY, books billed as one ``bookmakers`` group, own ledger and cap, season
2025 refused in code, resumable. Fake HTTP; no network, no key, no ``nba.duckdb``."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from nba.odds import history_pull as hp
from nba.odds import the_odds_api as api
from nba.odds.__main__ import main
from tests.odds.test_the_odds_api import GAME, FakeHttp, make_client

MARKETS = ("player_points", "player_rebounds", "h2h")
BOOKS = ("pinnacle", "fanduel", "draftkings")
SNAPS = (("m360", 360), ("m180", 180), ("m90", 90))


@pytest.fixture
def mcfg(tmp_path: Path) -> api.HistoryConfig:
    return api.HistoryConfig(
        min_interval_s=0.0,
        retry_backoff_s=0.0,
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "budget" / "ledger.json",
        state_path=tmp_path / "state.jsonl",
        out_db=tmp_path / "odds_history.duckdb",
        games_db=tmp_path / "nba_missing.duckdb",
        schedule_dir=tmp_path / "sched",
        movement_markets=MARKETS,
        movement_books=BOOKS,
        movement_snapshots=SNAPS,
        movement_ledger_path=tmp_path / "budget" / "movement.json",
        movement_max_credits=10_000,
    )


def kw(cfg: api.HistoryConfig, **extra: Any) -> dict[str, Any]:
    return {
        "cfg": cfg, "games": [GAME], "all_season_games": {2023: [], 2024: [GAME], 2025: []},
        "resolver": api.default_resolver(), **extra,
    }  # fmt: skip


def mclient(cfg: api.HistoryConfig, fake: FakeHttp) -> Any:
    mc = replace(cfg, ledger_path=cfg.movement_ledger_path, max_credits=cfg.movement_max_credits)
    return make_client(mc, fake)


def test_estimate_is_books_as_one_region_per_snapshot(mcfg: api.HistoryConfig) -> None:
    rep = hp.run_pull(2024, "movement", dry_run=True, **kw(mcfg))
    # 3 kinds x (events 1 + 10 credits x 3 markets x ONE billing unit (3 books <= 10)) = 93
    assert rep.estimate["credits_total"] == 93 and rep.estimate["odds_calls"] == 3
    assert rep.refused is None
    assert mcfg.billing_units("movement") == 1
    assert (
        replace(mcfg, movement_books=tuple(f"b{i}" for i in range(11))).billing_units("movement")
        == 2
    )  # 11-20 books bill as two regions


def test_kinds_subset_and_scope_change_the_estimate_and_keys(mcfg: api.HistoryConfig) -> None:
    one = hp.run_pull(2024, "movement", dry_run=True, kinds=["m90"], **kw(mcfg))
    assert one.estimate["credits_total"] == 31 and one.estimate["odds_calls"] == 1
    games = hp.run_pull(
        2024, "movement", dry_run=True, scope="games", **kw(replace(mcfg, game_markets=("h2h",)))
    )
    assert games.estimate["credits_total"] == 3 * (1 + 10)  # 1 market, 1 unit, 3 kinds
    with pytest.raises(ValueError, match="unknown movement snapshot kinds"):
        hp.run_pull(2024, "movement", dry_run=True, kinds=["t60"], **kw(mcfg))
    with pytest.raises(ValueError, match="movement phase only"):
        hp.run_pull(2024, "props", dry_run=True, kinds=["m90"], **kw(mcfg))
    with pytest.raises(ValueError, match="scope"):
        hp.run_pull(2024, "movement", dry_run=True, scope="alt", **kw(mcfg))


def test_season_2025_is_refused_in_code_before_anything_is_read(mcfg: api.HistoryConfig) -> None:
    # games_db does not exist: a refusal AFTER loading games would raise a DuckDB error instead
    with pytest.raises(api.HoldoutRefused, match="frozen holdout"):
        hp.run_pull(2025, "movement", dry_run=True, cfg=mcfg)
    with pytest.raises(api.HoldoutRefused):
        hp.run_pull(2025, "movement", cfg=mcfg, games=[GAME])  # not a dry run either
    with pytest.raises(api.HoldoutRefused):
        hp.run_pull(2022, "movement", dry_run=True, cfg=mcfg)  # outside movement_seasons
    with pytest.raises(api.HoldoutRefused):
        replace(mcfg, movement_seasons=(2023, 2024, 2025))  # the config cannot name it


def test_config_file_naming_2025_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"movement_seasons": [2023, 2025], "movement_markets": ["x"]}))
    with pytest.raises(api.HoldoutRefused):
        api.load_config(p)


def test_cli_refuses_2025_movement_with_the_informational_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["history-pull", "--phase", "movement", "--season", "2025", "--dry-run"])
    assert rc == 4
    assert "HoldoutRefused" in capsys.readouterr().err


def test_cap_guard_uses_the_movement_cap_not_the_games_cap(mcfg: api.HistoryConfig) -> None:
    tight = replace(mcfg, movement_max_credits=92)
    assert hp.run_pull(2024, "movement", **kw(tight)).refused  # 93 > 92, nothing requested
    assert hp.run_pull(2024, "movement", dry_run=True, **kw(tight)).refused
    roomy = replace(tight, movement_max_credits=93)
    assert hp.run_pull(2024, "movement", dry_run=True, **kw(roomy)).refused is None
    # the games/props cap is untouched by the movement cap
    assert hp.run_pull(2024, "props", dry_run=True, **kw(tight)).refused is None


def test_marketless_or_bookless_config_fails_loudly(mcfg: api.HistoryConfig) -> None:
    with pytest.raises(ValueError, match="movement_markets"):
        hp.run_pull(2024, "movement", dry_run=True, **kw(replace(mcfg, movement_books=())))
    with pytest.raises(ValueError, match="--markets"):
        hp.run_pull(2024, "movement", dry_run=True, markets=["player_points"], **kw(mcfg))
    with pytest.raises(ValueError, match="collide|reuse"):
        replace(mcfg, movement_snapshots=(("t60", 90),))


def test_pull_requests_bookmakers_not_regions_uses_own_ledger_and_resumes(
    mcfg: api.HistoryConfig,
) -> None:
    fake = FakeHttp(regions=1)
    rep = hp.run_pull(2024, "movement", client=mclient(mcfg, fake), **kw(mcfg))
    assert rep.items_done == 3 and not rep.errors and not rep.match_failures
    odds_calls = [c for c in fake.calls if str(c["url"]).endswith("/odds")]
    assert len(odds_calls) == 3
    for c in odds_calls:
        assert c["params"]["bookmakers"] == ",".join(BOOKS)
        assert "regions" not in c["params"]
        assert c["params"]["markets"] == ",".join(MARKETS)
    assert sorted(c["params"]["date"] for c in odds_calls) == [  # 360 / 180 / 90 min before tip
        "2025-01-15T18:10:00Z", "2025-01-15T21:10:00Z", "2025-01-15T22:40:00Z",
    ]  # fmt: skip
    st = hp.PullState(mcfg.state_path)
    for kind in ("m360", "m180", "m90"):
        assert st.done(hp.item_key(GAME.game_id, kind, "movement"))
    assert not st.done(hp.item_key(GAME.game_id, "t60", "movement"))
    assert mcfg.movement_ledger_path.exists() and not mcfg.ledger_path.exists()  # own ledger
    assert rep.credits_spent == 93
    fake2 = FakeHttp(regions=1)
    again = hp.run_pull(2024, "movement", client=mclient(mcfg, fake2), **kw(mcfg))
    assert again.items_skipped_done == 3 and fake2.calls == []  # resumable, 0 credits


def test_resume_after_partial_run_only_buys_the_missing_kind(mcfg: api.HistoryConfig) -> None:
    fake = FakeHttp(regions=1)
    hp.run_pull(2024, "movement", kinds=["m180"], client=mclient(mcfg, fake), **kw(mcfg))
    fake2 = FakeHttp(regions=1)
    rest = hp.run_pull(2024, "movement", client=mclient(mcfg, fake2), **kw(mcfg))
    assert rest.items_skipped_done == 1 and rest.items_done == 2
    assert len([c for c in fake2.calls if str(c["url"]).endswith("/odds")]) == 2


def test_scope_variant_never_marks_the_full_variant_done(mcfg: api.HistoryConfig) -> None:
    cfg = replace(mcfg, game_markets=("h2h",))
    fake = FakeHttp(regions=1)
    hp.run_pull(2024, "movement", scope="games", client=mclient(cfg, fake), **kw(cfg))
    fake2 = FakeHttp(regions=1)
    full = hp.run_pull(2024, "movement", client=mclient(cfg, fake2), **kw(cfg))
    assert full.items_skipped_done == 0 and full.items_done == 3  # different market set, new keys


def test_real_config_movement_block() -> None:
    c = api.load_config()
    assert c.movement_seasons == (2023, 2024) and 2025 not in c.movement_seasons
    kinds = [k for k, _ in c.movement_snapshots]
    assert not set(kinds) & {"t60", "t5"} and all(m > 60 for _, m in c.movement_snapshots)
    assert "pinnacle" in c.movement_books and len(c.movement_books) <= 10  # one billing unit
    assert c.billing_units("movement") == 1
    assert set(c.movement_markets) == set(c.prop_markets) | set(c.game_markets)
    # credits per game-snapshot: 10 x 7 markets x 1 unit = 70, plus the events call
    assert c.expected_odds_cost("movement") == 70
    assert c.movement_max_credits <= 1_000_000
    assert c.movement_ledger_path.name == "the_odds_api_movement_ledger.json"
