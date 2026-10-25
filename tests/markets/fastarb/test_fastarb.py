"""fastarb: parsing, fees, arbitrage and lag maths, sinks, news, the tick, Underdog (fixtures)."""

from __future__ import annotations

import copy
import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from nba.live.http import Response
from nba.markets.fastarb import news, sinks, underdog, venues
from nba.markets.fastarb.__main__ import main as cli_main
from nba.markets.fastarb.arb import (
    fee_functions,
    find_arbs,
    find_lags,
    is_alertable,
)
from nba.markets.fastarb.config import (
    DEFAULT_PATH,
    FeeSchedule,
    UnverifiedFeeError,
    load_config,
)
from nba.markets.fastarb.pinnacle import pinnacle_probs
from nba.markets.fastarb.props import Ask, GameKey, PropKey, QuoteBook, is_half_point, team_id
from nba.markets.fastarb.runner import Runner
from nba.markets.fastarb.venues import PM_INTL, PM_US, K
from nba.odds.theoddsapi import NameResolver

FX = Path(__file__).parent / "fixtures"
GAME = GameKey(away=team_id("DET") or 0, home=team_id("PHI") or 0, day="2026-10-25")
ML = PropKey(GAME, "margin", 0.0)


def load(name: str) -> Any:
    return json.loads((FX / name).read_text())


@pytest.fixture(scope="module")
def cfg() -> Any:
    return load_config()


# ----------------------------------------------------------------------------- config and fees


def test_fee_objects_come_from_config_and_are_verified(cfg: Any) -> None:
    assert cfg.kalshi_fee.verified and cfg.us_fee.verified
    assert not cfg.intl_fee.verified  # the poller must refuse the international venue
    cfg.require_verified_fees()
    with pytest.raises(UnverifiedFeeError):
        cfg.require_verified_fees(intl=True)


def test_polymarket_rounding_is_bankers_per_order() -> None:
    f = FeeSchedule("x", 0.0695, True)
    assert f.order_fee(0.5, 20) == 0.35  # 0.34750 -> 0.35 (round half even only at exact ties)
    assert f.order_fee(0.5, 2) == 0.03  # 0.034750 -> 0.03
    assert FeeSchedule("y", 0.05, True).order_fee(0.5, 10) == 0.12  # 0.125 -> 0.12 (half to even)


def test_run_refuses_unverified_fee(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw["polymarket_us"]["fee"]["verified"] = False
    p = tmp_path / "fastarb.yaml"
    p.write_text(yaml.safe_dump(raw))
    assert cli_main(["--config", str(p), "run"]) == 2


# ------------------------------------------------------------------------------------ parsing


def test_kalshi_quotes_canonical_sides() -> None:
    kb = venues.kalshi_quotes(load("kalshi_detphi.json"))
    ml = kb.quotes[ML][K]
    # PHI (home) market: YES = home wins; DET (away) market YES = home does not win.
    assert ml["yes"].price == pytest.approx(0.50) or ml["yes"].price < 1
    assert ml["yes"].short.startswith("Kalshi KXNBAGAME-26OCT25DETPHI-")
    assert ml["no"].price < 1
    spread = PropKey(GAME, "margin", 3.5)  # "PHI wins by over 3.5" = home margin > 3.5
    assert spread in kb.quotes
    assert PropKey(GAME, "total", 227.5) in kb.quotes
    assert not kb.skipped


def test_kalshi_unmatched_team_is_skipped_and_counted() -> None:
    mk = copy.deepcopy(load("kalshi_detphi.json"))
    for m in mk:
        m["event_ticker"] = m["event_ticker"].replace("DETPHI", "XXXPHI")
    kb = venues.kalshi_quotes(mk)
    assert not kb.quotes
    assert any("unmatched_team" in k for k in kb.skipped)


def test_us_quotes_map_both_sides_and_depth() -> None:
    ev = load("pm_us_event_detphi.json")
    book = load("pm_us_book_ml.json")
    slug = "aec-nba-det-phi-2026-10-25"
    qb = venues.us_quotes(ev, 0.0695, {slug: book})
    ml = qb.quotes[ML][PM_US]
    # long side = Pistons (away) -> NO on "home wins"; short side = 76ers -> YES.
    assert ml["no"].levels and ml["yes"].levels
    assert ml["no"].price == min(p for p, _ in ml["no"].levels)
    assert all(a[0] <= b[0] for a, b in zip(ml["yes"].levels, ml["yes"].levels[1:], strict=False))
    spread = PropKey(GAME, "margin", 3.5)
    assert spread in qb.quotes and PropKey(GAME, "total", 226.5) in qb.quotes


def test_us_fee_coefficient_mismatch_is_skipped() -> None:
    qb = venues.us_quotes(load("pm_us_event_detphi.json"), 0.05)
    assert not qb.quotes
    assert qb.skipped[f"{PM_US}:fee_mismatch"] > 0


def test_us_levels_do_not_trust_payload_order() -> None:
    payload = {
        "marketData": {
            "offers": [
                {"px": {"value": "0.60"}, "qty": "5"},
                {"px": {"value": "0.55"}, "qty": "7"},
            ],
            "bids": [
                {"px": {"value": "0.40"}, "qty": "3"},
                {"px": {"value": "0.45"}, "qty": "4"},
            ],
        }
    }
    assert venues.us_levels(payload, True) == [(0.55, 7.0), (0.60, 5.0)]
    assert venues.us_levels(payload, False) == [(0.55, 4.0), (0.60, 3.0)]


def test_intl_quotes_second_outcome_ask_is_one_minus_bid() -> None:
    qb = venues.intl_quotes(load("pm_intl_event_detphi.json"))
    ml = qb.quotes[ML][PM_INTL]
    assert ml["yes"].price + ml["no"].price >= 1.0 - 1e-9  # ask(76ers) + ask(Pistons) >= 1
    assert PropKey(GAME, "margin", 3.5) in qb.quotes
    assert PropKey(GAME, "total", 226.5) in qb.quotes
    assert all(
        "not tradable" in a.desc
        for v in qb.quotes.values()
        for s in v[PM_INTL].values()
        for a in [s]
    )


def test_half_point_and_team_aliases() -> None:
    assert is_half_point(3.5) and is_half_point(-3.5) and not is_half_point(3.0)
    assert team_id("ny") == team_id("NYK") and team_id("ZZZ") is None


# ---------------------------------------------------------------------------- arbitrage maths


def _book(k_yes: float, pm_no: float, k_sz: float = 100, pm_sz: float = 100) -> QuoteBook:
    b = QuoteBook()
    b.add(ML, K, "yes", Ask(K, k_yes, "k", "KT", [(k_yes, k_sz)], "Kalshi KT YES"))
    b.add(ML, PM_US, "no", Ask(PM_US, pm_no, "p", "ps", [(pm_no, pm_sz)], "PolyUS ps 'DET'"))
    return b


def test_arb_after_both_fees_and_gap(cfg: Any) -> None:
    fees = fee_functions(cfg)
    # 0.40 + 0.50 = 0.90; fees at 20 ct: Kalshi ceil(0.336)=0.34, Poly bankers(0.3475)=0.35
    r = find_arbs(_book(0.40, 0.50), cfg, fees, (K, PM_US))[0]
    assert r.n_eval == 20
    assert r.margin == pytest.approx(1 - (8.0 + 10.0 + 0.34 + 0.35) / 20)
    assert is_alertable(r, cfg) == "alert"
    assert r.max_n == 100  # depth walk stops at the visible size
    # 0.45 + 0.50: gross 5c but after fees only 1.5c at 20 ct: below the 2c gap
    r2 = find_arbs(_book(0.45, 0.50), cfg, fees, (K, PM_US))[0]
    assert r2.gross_gap == pytest.approx(0.05)
    assert is_alertable(r2, cfg) == "below_gap"


def test_thin_depth_is_counted_not_alerted(cfg: Any) -> None:
    fees = fee_functions(cfg)
    r = find_arbs(_book(0.40, 0.50, k_sz=3, pm_sz=3), cfg, fees, (K, PM_US))[0]
    assert r.max_n <= 3
    assert is_alertable(r, cfg) == "thin"


def test_arb_walks_levels_not_just_the_top(cfg: Any) -> None:
    fees = fee_functions(cfg)
    b = QuoteBook()
    b.add(ML, K, "yes", Ask(K, 0.40, "k", "KT", [(0.40, 10), (0.55, 100)], "Kalshi KT YES"))
    b.add(ML, PM_US, "no", Ask(PM_US, 0.50, "p", "ps", [(0.50, 100)], "PolyUS ps"))
    r = find_arbs(b, cfg, fees, (K, PM_US))[0]
    assert r.max_n < 20  # beyond 10 contracts the Kalshi level at 0.55 kills the margin


def test_no_cross_when_one_side_missing(cfg: Any) -> None:
    b = QuoteBook()
    b.add(ML, K, "yes", Ask(K, 0.4, "k", "KT", [], "x"))
    assert find_arbs(b, cfg, fee_functions(cfg), (K, PM_US)) == []


# --------------------------------------------------------------------------------------- lag

PIN_BODY = {
    "bookmakers": [
        {
            "key": "pinnacle",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": "2026-10-25T20:00:00Z",
                    "outcomes": [
                        {"name": "Philadelphia 76ers", "price": 1.80},
                        {"name": "Detroit Pistons", "price": 2.10},
                    ],
                },
                {
                    "key": "spreads",
                    "last_update": "2026-10-25T20:00:00Z",
                    "outcomes": [
                        {"name": "Philadelphia 76ers", "price": 1.91, "point": -3.5},
                        {"name": "Detroit Pistons", "price": 1.91, "point": 3.5},
                    ],
                },
                {
                    "key": "totals",
                    "last_update": "2026-10-25T20:00:00Z",
                    "outcomes": [
                        {"name": "Over", "price": 1.95, "point": 226.0},
                        {"name": "Under", "price": 1.87, "point": 226.0},
                    ],
                },
            ],
        }
    ]
}


def test_pinnacle_devig_and_canonical_props() -> None:
    probs, upd, skipped = pinnacle_probs(PIN_BODY, GAME, NameResolver.default())
    ia, ib = 1 / 1.80, 1 / 2.10
    assert probs[ML] == pytest.approx(ia / (ia + ib))
    assert probs[PropKey(GAME, "margin", 3.5)] == pytest.approx(0.5)  # PHI -3.5 even odds
    assert skipped["totals:whole_line"] == 1  # 226.0 has a push: not compared
    assert upd[ML] == "2026-10-25T20:00:00Z"


def test_lag_flags_a_side_three_cents_under_pinnacle(cfg: Any) -> None:
    probs, upd, _ = pinnacle_probs(PIN_BODY, GAME, NameResolver.default())
    p = probs[ML]
    b = QuoteBook()
    b.add(ML, K, "yes", Ask(K, round(p - 0.05, 2), "k", "KT", [], "Kalshi KT YES"))
    b.add(ML, K, "no", Ask(K, round(1 - p + 0.02, 2), "k", "KT", [], "Kalshi KT NO"))
    lags = find_lags(b, probs, cfg, fee_functions(cfg), (K,), upd)
    assert [x.side for x in lags] == ["yes"]
    assert lags[0].edge == pytest.approx(0.05, abs=0.011)
    assert lags[0].net_edge < lags[0].edge  # the venue fee is taken off


# -------------------------------------------------------------------------------------- sinks


def test_raw_files_are_write_once(tmp_path: Path) -> None:
    w = sinks.RawWriter(tmp_path, "2026-10-25")
    ts = datetime(2026, 10, 25, 20, 0, 0)
    a = w.write(ts, "kalshi", "x", {"v": 1})
    b = w.write(ts, "kalshi", "x", {"v": 2})
    assert a != b
    with gzip.open(a) as fh:
        assert json.loads(fh.read()) == {"v": 1}
    assert a.parent.name == "fastarb_2026-10-25"


def test_alert_dedup_within_window_only(tmp_path: Path) -> None:
    s = sinks.AlertSink(tmp_path / "ALERTS.md", tmp_path / "d.json", 30)
    t0 = datetime(2026, 10, 25, 20, 0)
    assert s.emit("k1", "ARB one", t0, "2026-10-25 15:00")
    assert not s.emit("k1", "ARB one", t0 + timedelta(minutes=5), "2026-10-25 15:05")
    assert s.emit("k2", "ARB two", t0 + timedelta(minutes=5), "2026-10-25 15:05")
    assert s.emit("k1", "ARB one", t0 + timedelta(minutes=31), "2026-10-25 15:31")
    lines = (tmp_path / "ALERTS.md").read_text().splitlines()
    assert len(lines) == 3 and lines[0].startswith("- 2026-10-25 15:00 [fastarb] ARB one key=k1")
    again = sinks.AlertSink(
        tmp_path / "ALERTS.md", tmp_path / "d.json", 30
    )  # state survives restart
    assert not again.emit("k2", "ARB two", t0 + timedelta(minutes=6), "x")


# ---------------------------------------------------------------------------------------- news


def test_injury_report_events_only_for_new_files(tmp_path: Path) -> None:
    (tmp_path / "Injury-Report_2026-10-25_11_45AM.pdf").write_bytes(b"%PDF")
    known = news.injury_baseline(tmp_path)
    assert news.injury_events(tmp_path, known) == []
    (tmp_path / "Injury-Report_2026-10-25_12_15PM.pdf").write_bytes(b"%PDF")
    ev = news.injury_events(tmp_path, known)
    assert [e.detail for e in ev] == ["Injury-Report_2026-10-25_12_15PM.pdf"]
    assert ev[0].home is None  # applies to every game
    assert news.injury_events(tmp_path, known) == []


def test_lineup_change_detected_between_snapshots(tmp_path: Path) -> None:
    db = tmp_path / "l.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE lineup_snapshots_dedup (snapshot_id VARCHAR, fetched_at TIMESTAMP, "
        "game_id VARCHAR, "
        "team_id INT, player_id INT, lineup_status VARCHAR, announced_starter BOOLEAN)"
    )
    con.execute(
        "CREATE TABLE lineup_snapshots "
        "(game_id VARCHAR, team_id INT, is_home BOOLEAN, player_id INT)"
    )
    con.execute("INSERT INTO lineup_snapshots VALUES ('G1', 7, true, 1), ('G1', 8, false, 2)")
    t = datetime(2026, 10, 25, 20, 0)
    rows = [
        ("s1", t, "G1", 7, 1, "Expected", False),
        ("s2", t + timedelta(minutes=5), "G1", 7, 1, "Expected", False),  # unchanged
        ("s3", t + timedelta(minutes=10), "G1", 7, 1, "Confirmed", True),  # changed
    ]
    con.executemany("INSERT INTO lineup_snapshots_dedup VALUES (?,?,?,?,?,?,?)", rows)
    con.close()
    ev = news.lineup_events(db, t - timedelta(hours=1))
    assert ev is not None and len(ev) == 1
    assert (ev[0].home, ev[0].away) == (7, 8) and ev[0].ts == t + timedelta(minutes=10)
    assert news.lineup_events(tmp_path / "missing.duckdb", t) is None


# ------------------------------------------------------------------------------- the tick


class FakeFetcher:
    """Serves recorded fixtures by URL; counts requests like nba.live.http.Fetcher."""

    def __init__(
        self, us_event: dict[str, Any], kalshi: list[dict[str, Any]], book_shift: float = 0.0
    ) -> None:
        self.us_event, self.kalshi = us_event, kalshi
        self.requests = 0
        self.urls: list[str] = []
        self.book = load("pm_us_book_ml.json")
        for row in self.book["marketData"]["offers"]:  # planted arbs lower the Pistons ask
            row["px"]["value"] = f"{float(row['px']['value']) - book_shift:.4f}"

    def begin_tick(self, budget: int, deadline_s: float) -> None:
        pass

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        self.requests += 1
        self.urls.append(url)
        if "/trade-api/v2/markets" in url:
            s = (params or {}).get("series_ticker")
            body: Any = {"markets": [m for m in self.kalshi if m["ticker"].startswith(str(s))]}
        elif "/v1/events/slug/" in url:
            body = {"event": self.us_event}
        elif url.endswith("/book"):
            if "/aec-nba-det-phi-2026-10-25/" not in url:
                return Response(404, b"{}", url)  # only the moneyline book is recorded
            body = self.book
        else:
            return Response(404, b"{}", url)
        return Response(200, json.dumps(body).encode(), url)


def _arb_event() -> dict[str, Any]:
    """The recorded US event with the moneyline made sellable 20c under Kalshi: a planted arb."""
    ev = copy.deepcopy(load("pm_us_event_detphi.json"))
    for m in ev["markets"]:
        if m["sportsMarketType"] == venues.US_WINNER:
            for s in m["marketSides"]:
                s["quote"]["value"] = "0.30"
    out: dict[str, Any] = ev
    return out


def _runner(
    cfg: Any, tmp: Path, ev: dict[str, Any], tip: datetime, shift: float = 0.0
) -> tuple[Runner, FakeFetcher]:
    f = FakeFetcher(ev, load("kalshi_detphi.json"), shift)
    sink = sinks.AlertSink(tmp / "ALERTS.md", tmp / "d.json", 30)
    r = Runner(
        cfg,
        f,  # type: ignore[arg-type]
        raw=sinks.RawWriter(tmp, "2026-10-25"),
        sink=sink,
        clock=lambda: tip - timedelta(minutes=30),
        tips=lambda day: {(GAME.away, GAME.home): tip},
    )
    return r, f


def test_tick_on_recorded_slate_finds_no_arb(cfg: Any, tmp_path: Path) -> None:
    tip = datetime(2026, 10, 25, 23, 30)
    r, f = _runner(cfg, tmp_path, load("pm_us_event_detphi.json"), tip)
    rep = r.tick(date(2026, 10, 25))
    assert list(rep.games) == ["DET@PHI"]
    assert rep.arbs_alertable == 0 and not rep.alerts
    assert rep.games["DET@PHI"].n_props_both_venues >= 3
    assert not (tmp_path / "ALERTS.md").exists()
    assert all(
        u.startswith(("https://api.elections.kalshi.com", "https://gateway.polymarket.us"))
        for u in f.urls
    )
    assert list((tmp_path / "fastarb_2026-10-25").glob("*.json.gz"))  # raw before parse


def test_tick_alerts_once_with_exact_buys_then_dedups(cfg: Any, tmp_path: Path) -> None:
    tip = datetime(2026, 10, 25, 23, 30)
    r, _ = _runner(cfg, tmp_path, _arb_event(), tip, shift=0.21)
    rep = r.tick(date(2026, 10, 25))
    assert rep.arbs_alertable >= 1
    rep2 = r.tick(date(2026, 10, 25))
    text = (tmp_path / "ALERTS.md").read_text()
    lines = [x for x in text.splitlines() if "[fastarb] ARB" in x]
    assert len(lines) == rep.arbs_alertable  # the second tick wrote nothing new
    assert rep2.arbs_alertable >= 1
    first = lines[0]
    assert (
        "BUY Kalshi KXNBAGAME-26OCT25DETPHI-" in first
        and "BUY PolyUS aec-nba-det-phi-2026-10-25" in first
    )
    assert "key=" in first and len(first) < 400
    assert "stake" not in text.lower() and "kelly" not in text.lower()


def test_game_outside_the_window_is_not_polled(cfg: Any, tmp_path: Path) -> None:
    tip = datetime(2026, 10, 25, 23, 30)
    r, f = _runner(cfg, tmp_path, load("pm_us_event_detphi.json"), tip)
    r.clock = lambda: tip - timedelta(minutes=300)  # before tip - 180
    rep = r.tick(date(2026, 10, 25))
    assert rep.games == {}
    r.clock = lambda: tip + timedelta(seconds=1)  # at/after tip: stop
    assert r.tick(date(2026, 10, 25)).games == {}


# ------------------------------------------------------------------------------------ Underdog


def test_break_even_table() -> None:
    assert underdog.break_even(3.0, 2) == pytest.approx(0.5774, abs=1e-4)
    assert underdog.break_even(6.0, 3) == pytest.approx(0.5503, abs=1e-4)


def test_underdog_flags_by_line_difference_and_reports_break_even(cfg: Any, tmp_path: Path) -> None:
    csv_ = tmp_path / "ud.csv"
    csv_.write_text(
        "player,stat,ud_line,pin_line,pin_over_dec,pin_under_dec\n"
        "A Guard,points,27.5,24.5,1.91,1.91\n"  # 3 points high: under is favoured
        "B Big,rebounds,10.5,10.5,1.91,1.91\n"  # same line: ignored
        "C Wing,assists,5.5,5.0,1.91,1.91\n"  # 0.5 on assists: flagged by the small-stat rule
        "D Wing,points,25.0,24.5,1.91,1.91\n"  # 0.5 on points: below the 1.0 rule
    )
    res = underdog.evaluate(underdog.read_rows(csv_), cfg.section("underdog"))
    by = {x.row.player: x for x in res}
    assert "B Big" not in by
    assert by["A Guard"].flagged and by["A Guard"].side == "under"
    assert by["C Wing"].flagged and not by["D Wing"].flagged
    assert by["A Guard"].p_win > by["C Wing"].p_win
    assert "break-even 0.577" in by["A Guard"].text and "ASSUMED" in by["A Guard"].text


def test_underdog_never_requests_the_network() -> None:
    src = (Path(underdog.__file__)).read_text()
    assert "httpx" not in src and "requests" not in src and "urllib" not in src


def test_cli_underdog_prints_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    csv_ = tmp_path / "ud.csv"
    csv_.write_text(
        "player,stat,ud_line,pin_line,pin_over_dec,pin_under_dec\nA,points,24.5,24.5,1.9,1.9\n"
    )
    assert cli_main(["underdog", "--csv", str(csv_)]) == 0
    assert "no_positive_ev_found" in capsys.readouterr().out


# ------------------------------------------------------------------- Pinnacle client and credits


class _Resp:
    status_code = 200
    text = "{}"

    def __init__(self, body: Any) -> None:
        self._b = body
        self.headers = {"x-requests-last": "3", "x-requests-remaining": "4000000"}

    def json(self) -> Any:
        return self._b


def test_pinnacle_call_is_three_credits_on_the_shared_ledger_and_keyless(tmp_path: Path) -> None:
    from dataclasses import replace

    from nba.markets.fastarb.pinnacle import make_client
    from nba.odds.live_close import load_live_config

    seen: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Resp:
        seen.append({"url": url, **params})
        return _Resp(PIN_BODY)

    cfg = replace(load_live_config(), ledger_dir=tmp_path)
    cli = make_client(cfg, {"THE_ODDS_API_KEY": "sekrit-test-key"})
    cli._http_get = fake_get  # type: ignore[assignment]
    body, cost = cli.core_odds("evt1", load_config().raw)
    assert body is PIN_BODY and cost == 3
    assert seen[0]["bookmakers"] == "pinnacle" and "regions" not in seen[0]
    assert seen[0]["markets"] == "h2h,spreads,totals"
    assert cli.ledger.spent() == 3
    ledger_text = "".join(p.read_text() for p in tmp_path.iterdir())
    assert "sekrit-test-key" not in ledger_text
