"""the-odds-api.com historical pull: parsers, client, ledger, redaction, resumability, matching.

All tests use the recorded probe payloads in ``tests/fixtures/odds/the_odds_api/`` and a fake
``http_get``; nothing touches the network or ``nba.duckdb``.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from nba.odds import __main__ as odds_main
from nba.odds import history_pull as hp
from nba.odds import the_odds_api as api
from nba.odds.theoddsapi import NameResolver

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "odds" / "the_odds_api"
KEY = "unit-test-key-ABCDEF0123456789"
PHI, NYK = 1610612755, 1610612752
# NYK @ PHI, vendor commence_time 2025-01-16T00:10:00Z (real probe game).
GAME = api.GameRef("0022400555", 2024, PHI, NYK, datetime(2025, 1, 16, 0, 10))


def probe(name: str) -> dict[str, Any]:
    with gzip.open(FIX / f"{name}.json.gz", "rt") as fh:
        blob = json.load(fh)
    assert isinstance(blob, dict)
    return blob


def body(name: str) -> Any:
    return probe(name)["body"]


@dataclass
class FakeResp:
    status_code: int = 200
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    text_override: str | None = None

    @property
    def text(self) -> str:
        return self.text_override if self.text_override is not None else json.dumps(self.payload)

    def json(self) -> Any:
        if self.text_override is not None:
            raise ValueError("not json")
        return self.payload


class FakeHttp:
    """Routes by URL suffix; records every call. Costs follow the vendor's credit rules."""

    def __init__(self, odds_payload: str = "hist_props_2025-01-15", regions: int = 3) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: list[Any] = []
        self.odds_payload = odds_payload
        self.regions = regions
        self.remaining = 4_999_000
        self.events_payload = "hist_events_density"

    def __call__(self, url: str, **kw: Any) -> FakeResp:
        self.calls.append({"url": url, **kw})
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, FakeResp)
            return item
        if url.endswith("/events"):
            cost = 1
            payload = body(self.events_payload)
        elif url.endswith("/odds"):
            n_markets = len(str(kw["params"]["markets"]).split(","))
            cost = 10 * n_markets * self.regions
            payload = body(self.odds_payload)
        else:
            raise AssertionError(f"unexpected url {url}")
        self.remaining -= cost
        return FakeResp(
            200,
            payload,
            {"x-requests-last": str(cost), "x-requests-remaining": str(self.remaining)},
        )


@pytest.fixture
def cfg(tmp_path: Path) -> api.HistoryConfig:
    return api.HistoryConfig(
        min_interval_s=0.0,
        retry_backoff_s=0.0,
        backoff_429_s=0.0,
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "budget" / "ledger.json",
        state_path=tmp_path / "state.jsonl",
        out_db=tmp_path / "odds_history.duckdb",
        games_db=tmp_path / "nba_missing.duckdb",
        schedule_dir=tmp_path / "sched",
    )


@pytest.fixture
def resolver() -> NameResolver:
    return api.default_resolver()


def make_client(cfg: api.HistoryConfig, fake: FakeHttp, sleeps: list[float] | None = None) -> Any:
    return api.HistoricalClient(
        cfg,
        environ={api.KEY_ENV: KEY},
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        http_get=fake,
    )


def empty_seasons(cfg: api.HistoryConfig, game: api.GameRef) -> dict[int, list[api.GameRef]]:
    return {s: [] for s in cfg.seasons} | {game.season: [game]}


# ----------------------------------------------------------------------------------------------
# Parsers on the real probe payloads
# ----------------------------------------------------------------------------------------------


def parse(name: str, resolver: NameResolver, kind: str = "t60") -> api.ParseResult:
    b = body(name)
    return api.parse_event_odds(
        b,
        season=2024,
        game_id="G1",
        snapshot_kind=kind,
        requested_at=datetime(2025, 1, 15, 23, 0, 0),
        raw_file="2024/G1/odds_t60.json.gz",
        resolver=resolver,
    )


def test_props_rows_match_payload_and_have_no_future_snapshot(resolver: NameResolver) -> None:
    res = parse("hist_props_2025-01-15", resolver)
    df = res.rows
    n_payload = sum(
        len(m["outcomes"])
        for bk in body("hist_props_2025-01-15")["data"]["bookmakers"]
        for m in bk["markets"]
    )
    assert n_payload == 428  # the probe's documented outcome count
    assert df.height + res.reasons["duplicate_row_dropped"] == n_payload
    assert df["book"].n_unique() == 7
    assert set(df["market"].unique()) == {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
    }
    assert set(df["side"].unique()) == {"over", "under"}
    assert df["snapshot_ts"].max() == datetime(2025, 1, 15, 22, 55, 38)
    assert (df["snapshot_ts"] <= df["requested_at"]).all()
    assert res.reasons["snapshot_after_request"] == 0
    assert df.select(list(api.DEDUPE_KEY)).is_duplicated().sum() == 0
    assert df["source"].unique().to_list() == ["the_odds_api"]


def test_props_devig_pairs_sum_to_one_and_unpaired_stay_null(resolver: NameResolver) -> None:
    df = parse("hist_props_2025-01-15", resolver).rows
    paired = df.filter(pl.col("implied_prob").is_not_null())
    g = paired.group_by(["book", "market", "player_name", "point"]).agg(
        pl.col("implied_prob").sum().alias("s"), pl.len().alias("n")
    )
    assert g["n"].unique().to_list() == [2]
    assert (g["s"] - 1.0).abs().max() < 1e-9  # type: ignore[operator]
    # de-vigged is never above the vigged probability for both sides of a pair summed over > 1
    assert (paired["implied_prob_raw"].sum()) > paired["implied_prob"].sum()


def test_player_ids_exact_match_only_and_unresolved_counted(resolver: NameResolver) -> None:
    res = parse("hist_props_2025-01-15", resolver)
    df = res.rows
    brunson = df.filter(pl.col("player_name") == "Jalen Brunson")
    assert brunson.height > 0 and brunson["player_id"].unique().to_list() == [1628973]
    nulls = df.filter(pl.col("player_id").is_null())
    assert nulls.height > 0  # the reviewed alias table is small; unknown names stay NULL
    assert res.reasons["unresolved_player"] == nulls.height
    assert set(res.unresolved) == set(nulls["player_name"].unique().to_list())
    assert "Jalen Brunson" not in res.unresolved


def test_game_lines_sides_points_and_devig(resolver: NameResolver) -> None:
    df = parse("hist_game_2025-01-15", resolver).rows
    dk = df.filter(pl.col("book") == "draftkings")
    h2h = dk.filter(pl.col("market") == "h2h").sort("side")
    assert h2h["side"].to_list() == ["away", "home"]  # Knicks away, 76ers home
    assert h2h.filter(pl.col("side") == "home")["price_american"].item() == 185
    assert abs(h2h["implied_prob"].sum() - 1.0) < 1e-9
    sp = dk.filter(pl.col("market") == "spreads")
    assert sorted(sp["point"].to_list()) == [-6.0, 6.0]
    assert sp["implied_prob"].null_count() == 0
    tot = dk.filter(pl.col("market") == "totals")
    assert set(tot["side"].to_list()) == {"over", "under"}
    assert tot["point"].unique().to_list() == [218.5]
    assert df["player_name"].null_count() == df.height
    # betus has no spreads in the probe: only h2h and totals rows
    assert set(df.filter(pl.col("book") == "betus")["market"].unique()) == {"h2h", "totals"}


def test_tipped_event_is_an_empty_not_an_error(resolver: NameResolver) -> None:
    res = parse("hist_props_2024-01-15", resolver)
    assert res.rows.height == 0 and res.rows.schema == pl.Schema(api.ROW_SCHEMA)
    assert res.reasons["no_bookmakers"] == 1


def test_malformed_pieces_are_counted_not_raised(resolver: NameResolver) -> None:
    bad = {
        "timestamp": "2025-01-15T22:55:38Z",
        "data": {
            "id": "e1",
            "home_team": "Philadelphia 76ers",
            "away_team": "New York Knicks",
            "bookmakers": [
                "junk",
                {"key": "bk", "markets": [{"key": "h2h", "outcomes": [{"name": "?", "price": 5}]}]},
                {
                    "key": "bk2",
                    "markets": [
                        {"key": "player_points", "outcomes": [{"name": "Over", "price": -110}]}
                    ],
                },
            ],
        },
    }
    res = api.parse_event_odds(
        bad,
        season=2024,
        game_id="G",
        snapshot_kind="t5",
        requested_at=datetime(2025, 1, 15, 23, 0),
        raw_file="x",
        resolver=resolver,
    )
    assert res.rows.height == 0
    assert res.reasons["unknown_book_shape"] == 1
    assert res.reasons["bad_outcome"] == 1  # price 5 is not an American price
    assert res.reasons["unparsed_outcome"] == 1  # a prop without a player description


# ----------------------------------------------------------------------------------------------
# Event matching
# ----------------------------------------------------------------------------------------------


def test_all_thirty_vendor_team_names_resolve(resolver: NameResolver) -> None:
    names = """Atlanta Hawks|Boston Celtics|Brooklyn Nets|Charlotte Hornets|Chicago Bulls|
Cleveland Cavaliers|Dallas Mavericks|Denver Nuggets|Detroit Pistons|Golden State Warriors|
Houston Rockets|Indiana Pacers|Los Angeles Clippers|Los Angeles Lakers|Memphis Grizzlies|
Miami Heat|Milwaukee Bucks|Minnesota Timberwolves|New Orleans Pelicans|New York Knicks|
Oklahoma City Thunder|Orlando Magic|Philadelphia 76ers|Phoenix Suns|Portland Trail Blazers|
Sacramento Kings|San Antonio Spurs|Toronto Raptors|Utah Jazz|Washington Wizards""".replace(
        "\n", ""
    ).split("|")
    assert len(names) == 30
    ids = [resolver.team_id(n) for n in names]
    assert None not in ids and len(set(ids)) == 30  # Clippers regression: vendor says "Los Angeles"


def test_match_event_finds_the_one_game(resolver: NameResolver) -> None:
    evs = api.parse_events(body("hist_events_density"))
    m = api.match_event(evs, GAME, resolver)
    assert m.event_id == "8e825a8f4a6264c7983a1fad6d45ad4a"


@pytest.mark.parametrize(
    ("mutate", "n"),
    [
        ("wrong_teams", 0),
        ("outside_window", 0),
        ("duplicate", 2),
        ("empty", 0),
    ],
)
def test_match_event_failure_modes_are_loud(resolver: NameResolver, mutate: str, n: int) -> None:
    evs = api.parse_events(body("hist_events_density"))
    game = replace(GAME, home_team=NYK, away_team=PHI) if mutate == "wrong_teams" else GAME
    if mutate == "outside_window":
        game = replace(GAME, tipoff_utc=datetime(2025, 1, 18, 0, 10))
    if mutate == "duplicate":
        twin = replace(evs[0], event_id="dup")
        for e in evs:
            if e.home_team == "Philadelphia 76ers":
                twin = replace(e, event_id="dup")
        evs = [*evs, twin]
    if mutate == "empty":
        evs = []
    with pytest.raises(api.EventMatchError) as ei:
        api.match_event(evs, game, resolver)
    assert ei.value.n_matches == n and ei.value.game_id == game.game_id


# ----------------------------------------------------------------------------------------------
# Client: raw-first cache, ledger, refusals, redaction, backoff
# ----------------------------------------------------------------------------------------------

AT = datetime(2025, 1, 15, 23, 10)
PROPS = list(api.HistoryConfig().prop_markets)


def test_raw_path_is_deterministic_and_markets_hash_distinguishes_phases(
    cfg: api.HistoryConfig,
) -> None:
    a = api.raw_path(cfg, 2024, "G", "odds", "t60", cfg.regions, cfg.prop_markets)
    b = api.raw_path(cfg, 2024, "G", "odds", "t60", cfg.regions, cfg.game_markets)
    c = api.raw_path(
        cfg,
        2024,
        "G",
        "odds",
        "t60",
        tuple(reversed(cfg.regions)),
        tuple(reversed(cfg.prop_markets)),
    )
    assert a != b and a == c
    assert a.parent == cfg.raw_dir / "2024" / "G"
    assert a.name.startswith("odds_t60_eu-us-us_ex_") and a.name.endswith(".json.gz")
    e = api.raw_path(cfg, 2024, "G", "events", "t5")
    assert e.name == "events_t5_none_none.json.gz"


def test_fetch_writes_raw_first_ledger_costs_and_rerun_is_free(cfg: api.HistoryConfig) -> None:
    fake = FakeHttp()
    cli = make_client(cfg, fake)
    ev = cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    od = cli.event_odds(
        season=2024, game_id="G", snapshot_kind="t60", at=AT, event_id="E", markets=PROPS
    )
    assert (ev.cost, od.cost) == (1, 120)
    assert od.path.exists() and ev.path.exists()
    raw = probe_file(od.path)
    assert raw["params"] == {
        "regions": "us,eu,us_ex",
        "markets": ",".join(PROPS),
        "oddsFormat": "american",
        "date": "2025-01-15T23:10:00Z",
    }
    assert "apiKey" not in raw["params"] and KEY not in od.path.read_bytes().decode("latin1")
    assert fake.calls[0]["params"]["apiKey"] == KEY  # sent on the wire, stored nowhere
    led = api.CreditLedger(cfg.ledger_path, cfg.max_credits, cfg.min_server_remaining)
    assert led.spent() == 121
    lines = [json.loads(x) for x in cfg.ledger_path.with_suffix(".jsonl").read_text().splitlines()]
    assert [x["cost"] for x in lines] == [1, 120]
    assert lines[1]["server_remaining"] == 4_999_000 - 121
    # a brand-new client (new process) finds the files: zero requests, zero credits
    fake2 = FakeHttp()
    cli2 = make_client(cfg, fake2)
    ev2 = cli2.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    od2 = cli2.event_odds(
        season=2024, game_id="G", snapshot_kind="t60", at=AT, event_id="E", markets=PROPS
    )
    assert fake2.calls == [] and (ev2.cost, od2.cost) == (0, 0) and od2.from_cache
    assert od2.body == od.body
    assert led.spent() == 121


def probe_file(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt") as fh:
        out = json.load(fh)
    assert isinstance(out, dict)
    return out


def test_ledger_refuses_before_spending_when_cap_would_be_exceeded(cfg: api.HistoryConfig) -> None:
    small = replace(cfg, max_credits=150)
    fake = FakeHttp()
    cli = make_client(small, fake)
    cli.event_odds(
        season=2024, game_id="G1", snapshot_kind="t60", at=AT, event_id="E", markets=PROPS
    )  # 120 <= 150
    with pytest.raises(api.CreditBudgetExceeded, match="max_credits"):
        cli.event_odds(
            season=2024, game_id="G2", snapshot_kind="t60", at=AT, event_id="E", markets=PROPS
        )  # 120 + 120 > 150
    assert len(fake.calls) == 1  # refused BEFORE the second HTTP call
    assert api.CreditLedger(small.ledger_path, 150, 0).spent() == 120


def test_ledger_refuses_below_server_floor(cfg: api.HistoryConfig) -> None:
    fake = FakeHttp()
    fake.remaining = 100_100  # one 120-credit call would land below the 100_000 floor
    cli = make_client(cfg, fake)
    cli.events(season=2024, game_id="G1", snapshot_kind="t60", at=AT)  # cost 1, remaining 100_099
    cli.ledger.note_headers(100_050, None)
    with pytest.raises(api.CreditBudgetExceeded, match="floor"):
        cli.event_odds(
            season=2024, game_id="G1", snapshot_kind="t60", at=AT, event_id="E", markets=PROPS
        )
    assert len(fake.calls) == 1


def test_key_is_never_in_repr_errors_logs_or_raw_files(
    cfg: api.HistoryConfig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeHttp()
    cli = make_client(cfg, fake)
    assert KEY not in repr(cli) and KEY not in str(api.read_key({api.KEY_ENV: KEY}))
    fake.script = [FakeResp(422, text_override=f"bad request url=...?apiKey={KEY}&date=x")]
    with pytest.raises(api.RequestRejected) as ei:
        cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    assert KEY not in str(ei.value) and "<secret>" in str(ei.value)
    fake.script = [FakeResp(401, text_override=f"key {KEY} invalid")]
    with pytest.raises(api.AuthError) as ai:
        cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    assert KEY not in str(ai.value)
    fake.script = [httpx.ConnectError(f"boom {KEY}")]
    fake.script.append(FakeResp(200, {"data": []}, {"x-requests-last": "1"}))
    cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    assert KEY not in caplog.text
    for f in cfg.raw_dir.rglob("*"):
        if f.is_file():
            assert KEY.encode() not in f.read_bytes()
            with gzip.open(f, "rb") as fh:
                assert KEY.encode() not in fh.read()
    assert KEY not in cfg.ledger_path.read_text()
    assert api.redact(f"https://x/y?apiKey={KEY}&a=1") == "https://x/y?apiKey=<secret>&a=1"


def test_missing_key_fails_before_any_io(cfg: api.HistoryConfig) -> None:
    with pytest.raises(api.KeyMissingError):
        api.HistoricalClient(cfg, environ={})
    assert not cfg.raw_dir.exists()


def test_429_backs_off_then_succeeds_and_gives_up(cfg: api.HistoryConfig) -> None:
    sleeps: list[float] = []
    fake = FakeHttp()
    cli = make_client(replace(cfg, backoff_429_s=5.0), fake, sleeps)
    ok = FakeResp(200, {"data": []}, {"x-requests-last": "1"})
    fake.script = [FakeResp(429), FakeResp(429), ok]
    cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    assert sleeps == [5.0, 10.0]
    # a 429 that never clears aborts the run (fatal), after max_429_waits waits
    fake.script = [FakeResp(429)] * 20
    with pytest.raises(api.CreditBudgetExceeded, match="429"):
        cli.events(season=2024, game_id="G2", snapshot_kind="t60", at=AT)


def test_5xx_retries_are_bounded(cfg: api.HistoryConfig) -> None:
    fake = FakeHttp()
    cli = make_client(cfg, fake)
    fake.script = [FakeResp(503)] * 3
    with pytest.raises(api.UnreachableError):
        cli.events(season=2024, game_id="G", snapshot_kind="t60", at=AT)
    assert len(fake.calls) == cfg.max_attempts
    assert api.CreditLedger(cfg.ledger_path, 10, 0).spent() == 0  # failed calls cost nothing


# ----------------------------------------------------------------------------------------------
# Pull orchestration (injected schedule; fake HTTP; real parse + DuckDB store)
# ----------------------------------------------------------------------------------------------


def test_dry_run_estimate_and_refusal(cfg: api.HistoryConfig, resolver: NameResolver) -> None:
    rep = hp.run_pull(
        2024,
        "props",
        dry_run=True,
        cfg=cfg,
        games=[GAME],
        all_season_games=empty_seasons(cfg, GAME),
    )
    # 2 snapshots x (events 1 + props 10 x 4 markets x 3 regions) = 2 x 121
    assert rep.estimate["credits_total"] == 242 and rep.estimate["events_calls"] == 2
    assert rep.full_plan_estimate["credits_total"] == 2 * (
        1 + 120 + 90
    )  # events lists shared by phases
    assert rep.refused is None and not cfg.raw_dir.exists()  # dry run touches nothing
    tight = replace(cfg, max_credits=300)
    rep2 = hp.run_pull(
        2024, "props", cfg=tight, games=[GAME], all_season_games=empty_seasons(tight, GAME)
    )
    assert rep2.refused and "exceeds max_credits" in rep2.refused
    assert rep2.requests == 0 and rep2.items_done == 0


def test_pull_end_to_end_rows_state_and_idempotent_rerun(
    cfg: api.HistoryConfig, resolver: NameResolver
) -> None:
    fake = FakeHttp()
    cli = make_client(cfg, fake)
    kw: dict[str, Any] = {
        "cfg": cfg,
        "games": [GAME],
        "all_season_games": empty_seasons(cfg, GAME),
        "resolver": resolver,
    }
    rep = hp.run_pull(2024, "props", client=cli, **kw)
    assert rep.items_done == 2 and rep.credits_spent == 2 * 121 and rep.requests == 4
    assert not rep.match_failures and not rep.errors and rep.aborted is None
    store = hp.OddsStore(cfg.out_db)
    n1 = store.read("SELECT count(*) FROM odds_history")[0][0]
    assert n1 + rep.reasons["duplicate_row_dropped"] == 2 * 428
    kinds = dict(store.read("SELECT snapshot_kind, count(*) FROM odds_history GROUP BY 1"))
    assert set(kinds) == {"t60", "t5"} and kinds["t60"] == kinds["t5"]
    # requested_at honours the real tip: 00:10Z - 60 min and - 5 min
    got = dict(
        store.read("SELECT snapshot_kind, any_value(requested_at) FROM odds_history GROUP BY 1")
    )
    assert got["t60"] == datetime(2025, 1, 15, 23, 10) and got["t5"] == datetime(2025, 1, 16, 0, 5)
    assert all(
        r[0] <= r[1] for r in store.read("SELECT snapshot_ts, requested_at FROM odds_history")
    )
    state = hp.PullState(cfg.state_path)
    assert state.done(hp.item_key(GAME.game_id, "t60", "props")) and len(state.items) == 2
    # rerun: nothing pending, no HTTP, no credits, identical table
    fake2 = FakeHttp()
    rep2 = hp.run_pull(2024, "props", client=make_client(cfg, fake2), **kw)
    assert rep2.items_skipped_done == 2 and fake2.calls == [] and rep2.credits_spent == 0
    # even with the state file gone, the raw cache means 0 requests and no duplicate rows
    cfg.state_path.unlink()
    fake3 = FakeHttp()
    rep3 = hp.run_pull(2024, "props", client=make_client(cfg, fake3), **kw)
    assert fake3.calls == [] and rep3.credits_spent == 0 and rep3.items_done == 2
    assert store.read("SELECT count(*) FROM odds_history")[0][0] == n1
    # reparse re-reads raw only
    rep4 = hp.run_pull(2024, "props", client=make_client(cfg, FakeHttp()), reparse=True, **kw)
    assert rep4.requests == 0 and store.read("SELECT count(*) FROM odds_history")[0][0] == n1
    lines = hp.status_lines(cfg)
    assert any(x.startswith("ledger: spent=242") for x in lines)
    assert any("unresolved player rows=" in x for x in lines)


def test_game_phase_reuses_cached_events_lists(
    cfg: api.HistoryConfig, resolver: NameResolver
) -> None:
    fake = FakeHttp()
    kw: dict[str, Any] = {
        "cfg": cfg,
        "games": [GAME],
        "all_season_games": empty_seasons(cfg, GAME),
        "resolver": resolver,
    }
    hp.run_pull(2024, "props", client=make_client(cfg, fake), **kw)
    spent_props = api.CreditLedger(cfg.ledger_path, 10**9, 0).spent()
    fake_g = FakeHttp(odds_payload="hist_game_2025-01-15")
    rep = hp.run_pull(2024, "games", client=make_client(cfg, fake_g), **kw)
    # events lists were cached by the props phase: only the two 90-credit odds calls are paid
    assert [c["url"].rsplit("/", 1)[-1] for c in fake_g.calls] == ["odds", "odds"]
    assert rep.credits_spent == 2 * 90
    assert api.CreditLedger(cfg.ledger_path, 10**9, 0).spent() == spent_props + 180
    store = hp.OddsStore(cfg.out_db)
    mk = {r[0] for r in store.read("SELECT DISTINCT market FROM odds_history")}
    assert {"h2h", "spreads", "totals", "player_points"} <= mk  # phases coexist, no clobbering


def test_event_match_failure_is_counted_recorded_and_not_done(
    cfg: api.HistoryConfig, resolver: NameResolver
) -> None:
    fake = FakeHttp()
    fake.events_payload = "hist_events_2023-05-10"  # a different night: no PHI-NYK event
    rep = hp.run_pull(
        2024,
        "props",
        client=make_client(cfg, fake),
        cfg=cfg,
        games=[GAME],
        all_season_games=empty_seasons(cfg, GAME),
        resolver=resolver,
    )
    assert len(rep.match_failures) == 2 and rep.items_done == 0 and rep.aborted is None
    assert rep.credits_spent == 2  # only the two events lists were paid; no odds call was made
    assert all(c["url"].endswith("/events") for c in fake.calls)
    state = hp.PullState(cfg.state_path)
    assert {r["status"] for r in state.items.values()} == {"match_failed"}
    assert not state.done(hp.item_key(GAME.game_id, "t60", "props"))  # retried on the next run
    assert any("MATCH FAILURE" in line for line in rep.lines())


def test_run_aborts_on_server_floor_and_keeps_progress(
    cfg: api.HistoryConfig, resolver: NameResolver
) -> None:
    fake = FakeHttp()
    fake.remaining = 100_200  # snapshot 1 costs 121 -> 100_079; snapshot 2's odds call breaches
    rep = hp.run_pull(
        2024,
        "props",
        client=make_client(cfg, fake),
        cfg=cfg,
        games=[GAME],
        all_season_games=empty_seasons(cfg, GAME),
        resolver=resolver,
    )
    assert rep.refused is None and rep.aborted and "floor" in rep.aborted
    assert rep.items_done == 1 and rep.credits_spent == 122  # t60 paid in full, t5 events list only
    state = hp.PullState(cfg.state_path)
    assert state.done(hp.item_key(GAME.game_id, "t60", "props"))
    assert not state.done(hp.item_key(GAME.game_id, "t5", "props"))


def test_attach_tips_excludes_games_without_a_real_tip() -> None:
    games = pl.DataFrame(
        {"game_id": ["A", "B"], "season": [2024, 2024], "home_team": [1, 2], "away_team": [3, 4]}
    )
    tips = pl.DataFrame(
        {
            "game_id": ["A"],
            "tipoff_utc": [datetime(2025, 1, 1, 0, 0)],
            "tip_et": [datetime(2024, 12, 31, 19, 0)],
        }
    )
    out, missing = hp._attach_tips(games, tips)
    assert [g.game_id for g in out] == ["A"] and missing == 1


def test_cli_parses_history_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(season: int, phase: str | None, **kw: Any) -> hp.PullReport:
        seen.update(season=season, phase=phase, **kw)
        rep = hp.PullReport(season=season, phases=["props"], dry_run=True)
        rep.full_plan_estimate = {"credits_total": 7}
        return rep

    monkeypatch.setattr(hp, "run_pull", fake_run)
    rc = odds_main.main(
        ["history-pull", "--season", "2024", "--phase", "props", "--dry-run", "--max-games", "5"]
    )
    assert rc == 0 and seen["season"] == 2024 and seen["phase"] == "props"
    assert seen["dry_run"] is True and seen["max_games"] == 5
    assert "history-pull season=2024" in capsys.readouterr().out
