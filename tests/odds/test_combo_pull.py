"""COMBO_LINE_CAL R1 probe / pull: sampling, success bar, caps, holdout, secrets, resume, no
execution endpoints. Fake HTTP and synthetic bodies in the vendor's shape (the live shape is the
recorded payloads of the existing pull); nothing touches the network, a key or ``nba.duckdb``."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from nba.odds import combo_pull as cp
from nba.odds import the_odds_api as api
from tests.odds.test_the_odds_api import GAME, KEY, FakeResp

PRA, PR, PA, RA, BLK, BS = cp.load_combo_config().markets
BARS = (PRA, PR, PA, RA)


def cc_for(tmp: Path, **kw: Any) -> cp.ComboConfig:
    return cp.ComboConfig(
        **{
            **cp.load_combo_config().__dict__,
            "data_root": tmp,
            **kw,
        }
    )


def lines(market: str, book: str, sides: tuple[str, ...] = ("Over", "Under")) -> dict[str, Any]:
    outs = [{"name": s, "description": "A Player", "price": -110, "point": 30.5} for s in sides]
    return {"key": book, "markets": [{"key": market, "outcomes": outs}]}


def body(books: list[dict[str, Any]]) -> dict[str, Any]:
    return {"timestamp": "t", "data": {"id": "e1", "bookmakers": books}}


def test_two_sided_non_alternate_only() -> None:
    assert cp.two_sided_lines(body([lines(PRA, "fanduel")]), PRA, "fanduel") == (1, 0)
    assert cp.two_sided_lines(body([lines(PRA, "fanduel", ("Over",))]), PRA, "fanduel") == (0, 1)
    alt = lines(PRA + "_alternate", "fanduel")
    assert cp.two_sided_lines(body([alt]), PRA, "fanduel") == (0, 0)
    assert cp.two_sided_lines(body([lines(PRA, "betmgm")]), PRA, "fanduel") == (0, 0)
    assert cp.two_sided_lines(None, PRA, "fanduel") == (0, 0)


def write_probe(cc: cp.ComboConfig, raw: Path, item: cp.ProbeItem, bodies: Any) -> None:
    h = api.HistoryConfig(raw_dir=raw, regions=cc.regions)
    p = api.odds_path(
        h, item.game.season, item.game.game_id, cc.snapshot, cc.markets, item.game.tipoff_utc
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as fh:
        json.dump({"body": bodies}, fh)


def test_bar_needs_all_four_markets_and_ten_dates(tmp_path: Path) -> None:
    cc = cc_for(tmp_path)
    full = body([lines(m, "draftkings") for m in BARS])
    items = []
    for i in range(12):
        g = api.GameRef(f"00224{i:05d}", 2024, 1, 2, datetime(2025, 1, 1 + i, 1, 0))
        items.append(cp.ProbeItem(date(2024, 12, 31 + i) if i == 0 else date(2025, 1, i), g))
    raw = tmp_path / "probe"
    for i, it in enumerate(items):
        # dates 0-9 quote all four; date 10 lacks 'ra'; date 11 has no raw file at all
        if i < 10:
            write_probe(cc, raw, it, full)
        elif i == 10:
            write_probe(cc, raw, it, body([lines(m, "fanduel") for m in (PRA, PR, PA)]))
    res = cp.evaluate_bar(cc, raw, items)
    assert (res.n_pass, res.n_dates, res.met) == (10, 12, True)
    write_probe(cc, raw, items[0], body([lines(m, "draftkings") for m in (PRA, PR, PA)]))
    res2 = cp.evaluate_bar(cc, raw, items)
    assert (res2.n_pass, res2.met) == (9, False)
    assert res2.per_market[RA] == 9 and res2.dates[11]["raw_present"] is False


def synthetic_games(season: int, n_days: int) -> list[api.GameRef]:
    out = []
    for d in range(n_days):
        for j in range(3):
            tip = datetime(2024 if season == 2023 else 2025, 1, 1 + d % 28, 0 + j, 0)
            out.append(api.GameRef(f"002{season % 100}{d:03d}{j}", season, 1, 2, tip))
    return out


def test_sample_is_deterministic_spread_and_regular_season_only(tmp_path: Path) -> None:
    cc = cc_for(
        tmp_path,
        probe_season_start={2023: date(2023, 12, 7), 2024: date(2024, 10, 22)},
    )
    games = {s: synthetic_games(s, 28) for s in (2023, 2024)}
    games[2023].append(api.GameRef("0042300101", 2023, 1, 2, datetime(2024, 1, 20, 0, 0)))
    for gl in games.values():
        for g in gl:
            d = cc.old_raw_dir / str(g.season) / g.game_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "events_t60_none_none.json.gz").write_bytes(b"x")
    a = cp.sample_probe(cc, games)
    b = cp.sample_probe(cc, games)
    assert a == b and len(a) == 12 and len({i.day for i in a}) == 12
    assert all(i.game.game_id.startswith("002") for i in a)
    assert sum(i.game.season == 2023 for i in a) == 6
    assert all(i.day >= cc.probe_season_start[i.game.season] for i in a)
    assert cp.sample_record(a) == cp.sample_record(b)
    other = cc_for(tmp_path, probe_seed=1, probe_season_start=cc.probe_season_start)
    assert cp.sample_record(cp.sample_probe(other, games)) != cp.sample_record(a)


class Fake:
    def __init__(self, cost: int = 60) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cost = cost
        self.remaining = 1_800_000

    def __call__(self, url: str, **kw: Any) -> FakeResp:
        self.calls.append({"url": url, **kw})
        assert "apiKey" in kw["params"]
        if url.endswith("/events"):
            payload: Any = {
                "data": [
                    {
                        "id": "ev1",
                        "commence_time": "2025-01-16T00:10:00Z",
                        "home_team": "Philadelphia 76ers",
                        "away_team": "New York Knicks",
                    }
                ]
            }
            cost = 1
        else:
            payload = body([lines(m, "fanduel") for m in BARS])
            cost = self.cost
        self.remaining -= cost
        return FakeResp(
            200,
            payload,
            {"x-requests-last": str(cost), "x-requests-remaining": str(self.remaining)},
        )


def client(cc: cp.ComboConfig, raw: Path, cap: int, fake: Fake) -> api.HistoricalClient:
    h = cp.history_config(cc, raw, raw / "ledger.json", cap)
    from dataclasses import replace

    h = replace(h, min_interval_s=0.0, retry_backoff_s=0.0)
    return api.HistoricalClient(h, environ={api.KEY_ENV: KEY}, sleep=lambda _s: None, http_get=fake)


def test_pull_cost_cap_resume_and_no_key_on_disk(tmp_path: Path) -> None:
    cc = cc_for(tmp_path)
    raw = tmp_path / "pull"
    fake = Fake()
    games = [GAME, api.GameRef("0022400556", 2024, 1610612755, 1610612752, GAME.tipoff_utc)]
    # cap 60: events (1) + odds (60) = 61 credits exceed a 60 cap -> the guard stops
    cli = client(cc, raw, 60, fake)
    st = cp.pull_games(
        cc, games[:1], raw_dir=raw, ledger_path=raw / "ledger.json", cap=60, client=cli
    )
    assert st.stopped and "credit guard" in st.stopped and st.fetched == 0
    # a cap of 61 per game: first fetches (events 1 + odds 60), second is refused at the cap
    raw2 = tmp_path / "pull2"
    cli2 = client(cc, raw2, 61, fake)
    st2 = cp.pull_games(
        cc, games, raw_dir=raw2, ledger_path=raw2 / "ledger.json", cap=61, client=cli2
    )
    assert st2.fetched == 1 and st2.stopped and st2.credits == 61
    spent = api.CreditLedger(raw2 / "ledger.json", 61, 0).spent()
    assert spent == 61
    # resume: the done game costs nothing
    n_calls = len(fake.calls)
    cli3 = client(cc, raw2, 61, fake)
    st3 = cp.pull_games(
        cc, games[:1], raw_dir=raw2, ledger_path=raw2 / "ledger.json", cap=61, client=cli3
    )
    assert st3.from_cache == 1 and len(fake.calls) == n_calls
    for p in raw2.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if p.suffix == ".gz":
                data = gzip.decompress(data)
            assert KEY.encode() not in data
    m = cp.build_manifest(raw2, stage="t", extra={})
    assert m["n_files"] >= 2 and len(m["tree_sha256"]) == 64


def test_cost_above_expected_stops(tmp_path: Path) -> None:
    cc = cc_for(tmp_path)
    raw = tmp_path / "pull"
    fake = Fake(cost=120)
    cli = client(cc, raw, 10_000, fake)
    st = cp.pull_games(
        cc, [GAME], raw_dir=raw, ledger_path=raw / "ledger.json", cap=10_000, client=cli
    )
    assert st.stopped and "exceeded the expected" in st.stopped


def test_season_2025_refused_and_config_cannot_name_it(tmp_path: Path) -> None:
    cc = cc_for(tmp_path)
    g25 = api.GameRef("0022500001", 2025, 1, 2, datetime(2025, 10, 22, 0, 0))
    with pytest.raises(api.HoldoutRefused):
        cp.pull_games(cc, [g25], raw_dir=tmp_path / "r", ledger_path=tmp_path / "l.json", cap=10)
    with pytest.raises(api.HoldoutRefused):
        cc_for(tmp_path, seasons=(2023, 2024, 2025))


def test_full_refused_when_bar_not_met(tmp_path: Path) -> None:
    cc = cc_for(tmp_path)
    sample = tmp_path / "sample.yaml"
    sample.write_text("games:\n- {date: '2025-01-15', season: 2024, game_id: '0022400555'}\n")
    with pytest.raises(cp.BarNotMet):
        cp.run_full(
            cc, "d", "d", games_by_season={2023: [], 2024: [GAME]}, sample_path=sample, lock=False
        )


def test_heavy_lock_never_breaks_a_foreign_lock(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    lock.mkdir()
    (lock / "pid").write_text("1\nother\n")
    with pytest.raises(cp.LockHeld), cp.HeavyLock(lock, "t", wait_s=0.0, retry_s=0.0):
        pass
    assert lock.exists() and (lock / "pid").exists()
    free = tmp_path / "free.lock"
    with cp.HeavyLock(free, "t"):
        assert free.exists()
    assert not free.exists()


def test_committed_sample_matches_config_and_no_execution_code() -> None:
    items = cp.committed_sample()
    assert items is not None and len(items) == 12
    assert {r["season"] for r in items} == {2023, 2024}
    assert min(r["date"] for r in items if r["season"] == 2023) >= "2023-12-07"
    src = Path(cp.__file__).read_text().lower()
    for word in ("/orders", "portfolio", "/exchange", "place_order", "post("):
        assert word not in src
