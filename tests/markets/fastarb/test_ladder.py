"""Within-venue ladder arbitrage: payoff proofs, gates, confirm, runner, JARVIS source."""

from __future__ import annotations

import itertools
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from nba.jarvis.models import Game
from nba.live.http import Response
from nba.markets.fastarb import ladder, ladder_dryrun, sinks, venues
from nba.markets.fastarb.__main__ import main as cli_main
from nba.markets.fastarb.config import load_config
from nba.markets.fastarb.ladder import (
    LadderScanner,
    find_ladder_violations,
    is_ladder_alertable,
    kalshi_ladder_quotes,
    kalshi_prop_quotes,
    kalshi_team_total_quotes,
    leg_fee_fn,
)
from nba.markets.fastarb.ladder_source import confirmed_ladder_arbs
from nba.markets.fastarb.polymarket_us import PolymarketUSClient
from nba.markets.fastarb.props import team_id
from nba.markets.fastarb.runner import Runner
from nba.markets.fastarb.venues import PM_US, K
from nba.parlay.ev import order_fee

from .test_fastarb import GAME, FakeFetcher, load

EV = "26OCT25DETPHI"  # away DET, home PHI
FX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def cfg() -> Any:
    return load_config()


def km(
    ticker: str,
    title: str,
    strike: float | None,
    yes_ask: float,
    yes_bid: float,
    *,
    series: str | None = None,
    ask_sz: float = 500.0,
    bid_sz: float = 500.0,
    status: str = "active",
) -> dict[str, Any]:
    """A Kalshi market body (the fields the parsers read)."""
    series = series or ticker.split("-")[0]
    return {
        "ticker": ticker,
        "event_ticker": f"{series}-{EV}",
        "title": title,
        "floor_strike": strike,
        "strike_type": "greater" if strike is not None else "structured",
        "status": status,
        "yes_ask_dollars": f"{yes_ask:.4f}",
        "yes_bid_dollars": f"{yes_bid:.4f}",
        "no_ask_dollars": f"{1 - yes_bid:.4f}",
        "no_bid_dollars": f"{1 - yes_ask:.4f}",
        "yes_ask_size_fp": f"{ask_sz:.2f}",
        "yes_bid_size_fp": f"{bid_sz:.2f}",
        "updated_time": "2026-10-25T20:00:00Z",
    }


def ml(team: str, ask: float, bid: float, **kw: Any) -> dict[str, Any]:
    return km(f"KXNBAGAME-{EV}-{team}", f"{team} wins", None, ask, bid, **kw)


def spread(team: str, n: float, ask: float, bid: float, **kw: Any) -> dict[str, Any]:
    t = f"KXNBASPREAD-{EV}-{team}{int(n + 0.5)}"
    return km(t, f"{team} wins by over {n} points", n, ask, bid, **kw)


def total(line: float, ask: float, bid: float, **kw: Any) -> dict[str, Any]:
    return km(f"KXNBATOTAL-{EV}-{int(line + 0.5)}", f"Over {line}", line, ask, bid, **kw)


def team_total(team: str, line: float, ask: float, bid: float, **kw: Any) -> dict[str, Any]:
    t = f"KXNBATEAMTOTAL-{EV}-{team}{int(line + 0.5)}"
    return km(t, f"{team} over {line} points scored", line, ask, bid, **kw)


# ------------------------------------------------------------------------ payoff proofs
# An independent evaluator of each market's ENGLISH contract over every final score, then a check
# that every canonical pair c1 <= c2 pays at least $1 per pair in every outcome.

SCORES = [(a, h) for a in range(60, 90) for h in range(60, 90) if a != h]  # the NBA never ties


def settle_yes(ticker: str, strike: float | None, home: int, away: int) -> bool:
    series = ticker.split("-")[0]
    team = ticker.rsplit("-", 1)[-1]
    if series == "KXNBAGAME":
        return (home > away) if team == "PHI" else (away > home)
    if series == "KXNBASPREAD":
        assert strike is not None
        margin = (home - away) if team.startswith("PHI") else (away - home)
        return margin > strike
    if series == "KXNBATOTAL":
        assert strike is not None
        return home + away > strike
    if series == "KXNBATEAMTOTAL":
        assert strike is not None
        return (home if team.startswith("PHI") else away) > strike
    raise AssertionError(series)


def test_every_pair_pays_at_least_one_dollar_in_every_outcome(cfg: Any) -> None:
    markets = (
        [ml("PHI", 0.5, 0.4), ml("DET", 0.5, 0.4)]
        + [spread(t, n, 0.5, 0.4) for t in ("PHI", "DET") for n in (0.5, 1.5, 4.5, 9.5, 14.5)]
        + [total(x, 0.5, 0.4) for x in (150.5, 155.5, 160.5)]
        + [team_total(t, x, 0.5, 0.4) for t in ("PHI", "DET") for x in (70.5, 75.5, 80.5)]
    )
    by_ticker = {m["ticker"]: m for m in markets}
    book = kalshi_ladder_quotes(markets)
    n_pairs = 0
    kinds: set[str] = set()
    for per in book.quotes.values():
        assert {"yes", "no"} <= set(per[K])
    props = list(book.quotes)
    for lo, hi in itertools.product(props, props):
        if lo.game != hi.game or lo.kind != hi.kind or lo.c > hi.c:
            continue
        yes_leg, no_leg = book.quotes[lo][K]["yes"], book.quotes[hi][K]["no"]
        if yes_leg.ref == no_leg.ref:
            continue
        for away, home in SCORES:
            pay = 0
            for ask, want_yes_of_contract in (
                (yes_leg, "YES" in yes_leg.short),
                (no_leg, "YES" in no_leg.short),
            ):
                m = by_ticker[ask.ref]
                holds = settle_yes(ask.ref, m["floor_strike"], home, away)
                pay += int(holds if want_yes_of_contract else not holds)
            assert pay >= 1, (lo, hi, away, home, pay)
        n_pairs += 1
        kinds.add(lo.kind)
    # moneyline + both teams' spreads (one margin ladder), game totals, both teams' totals
    assert n_pairs >= 60 and len(kinds) == 4  # margin, total, team_total x2


def test_between_the_lines_the_pair_pays_two_and_outside_pays_one() -> None:
    # integer margin M = home - away; lines c1 = 0 (moneyline home) < c2 = 4.5
    for m in range(-20, 21):
        if m == 0:
            continue
        pay = int(m > 0) + int(not (m > 4.5))
        assert pay == (2 if 1 <= m <= 4 else 1)


def test_half_point_lines_cannot_land_on_the_line() -> None:
    for line in (0.5, 4.5, 225.5):
        assert all(abs(x - line) > 0 for x in range(-50, 400))  # integer scores
    book = kalshi_ladder_quotes([spread("PHI", 4.0, 0.5, 0.4), total(225.0, 0.5, 0.4)])
    assert not book.quotes and book.skipped[f"{K}:bad_line"] == 2  # whole numbers are skipped


def test_opposite_team_spreads_are_one_ladder() -> None:
    book = kalshi_ladder_quotes([spread("PHI", 3.5, 0.3, 0.2), spread("DET", 3.5, 0.3, 0.2)])
    assert set(p.c for p in book.quotes) == {3.5, -3.5}  # PHI by over 3.5 / DET by over 3.5


# ------------------------------------------------------------------------- evaluation


def test_planted_violation_net_margin_uses_config_fees(cfg: Any) -> None:
    # home wins (ML) ask 0.40; PHI wins by over 4.5: bid 0.50 -> NO ask 0.50. Cost 0.90.
    mk = [ml("PHI", 0.40, 0.38), spread("PHI", 4.5, 0.55, 0.50)]
    res = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))
    assert len(res) == 1
    r = res[0]
    assert (r.lo.c, r.hi.c) == (0.0, 4.5)
    assert r.gross_gap == pytest.approx(0.10)
    n = r.n_eval
    assert n == 20
    fee = order_fee(0.40, cfg.kalshi_fee, n, series="KXNBAGAME") + order_fee(
        0.50, cfg.kalshi_fee, n, series="KXNBASPREAD"
    )
    assert r.margin == pytest.approx(1 - (0.90 * n + fee) / n)
    assert is_ladder_alertable(r, cfg) == "alert"
    assert r.max_n == 500  # the visible depth, capped at the scan limit


def test_no_violation_when_bid_is_under_the_lower_ask(cfg: Any) -> None:
    mk = [ml("PHI", 0.60, 0.58), spread("PHI", 4.5, 0.45, 0.42)]
    assert not find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))


def test_thin_fee_edge_is_not_alertable(cfg: Any) -> None:
    mk = [ml("PHI", 0.49, 0.47), spread("PHI", 4.5, 0.55, 0.50)]  # gross +1c: fees eat it
    r = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))[0]
    assert r.gross_gap > 0 and r.margin < 0
    assert is_ladder_alertable(r, cfg) == "below_gap"


def test_depth_gate_zero_size_is_unknown_and_never_alerts(cfg: Any) -> None:
    mk = [ml("PHI", 0.40, 0.38, ask_sz=0.0), spread("PHI", 4.5, 0.55, 0.50)]
    r = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))[0]
    assert is_ladder_alertable(r, cfg) == "unknown_depth"


def test_depth_of_one_contract_is_enough_and_max_n_reports_it(cfg: Any) -> None:
    mk = [ml("PHI", 0.40, 0.38, ask_sz=1.0), spread("PHI", 4.5, 0.55, 0.50, bid_sz=3.0)]
    r = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))[0]
    assert is_ladder_alertable(r, cfg) == "alert" and r.max_n == 1 and r.n_eval == 1


def test_closed_market_is_not_a_leg(cfg: Any) -> None:
    mk = [ml("PHI", 0.40, 0.38, status="paused"), spread("PHI", 4.5, 0.55, 0.50)]
    assert not find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))


def test_far_pair_is_found_when_neighbours_are_fine(cfg: Any) -> None:
    # neighbours are fine (0.5 vs 5.5, 5.5 vs 10.5) but the far pair 0.5 vs 10.5 is violated
    mk = [
        spread("PHI", 0.5, 0.40, 0.38),
        spread("PHI", 5.5, 0.50, 0.30),
        spread("PHI", 10.5, 0.45, 0.45),
    ]
    res = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))
    pairs = {(r.lo.c, r.hi.c) for r in res}
    assert (0.5, 10.5) in pairs and (0.5, 5.5) not in pairs


def test_total_and_team_total_ladders(cfg: Any) -> None:
    mk = [
        total(225.5, 0.40, 0.38),
        total(227.5, 0.60, 0.52),
        team_total("PHI", 110.5, 0.45, 0.40),
        team_total("PHI", 112.5, 0.60, 0.55),
    ]
    res = find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))
    kinds = {r.kind for r in res}
    assert kinds == {"total", f"team_total:{team_id('PHI')}"}
    assert all(is_ladder_alertable(r, cfg) == "alert" for r in res)
    assert "PHI team total" in res[0].lo.label() or "PHI team total" in res[1].lo.label()


def test_whole_number_team_total_is_skipped() -> None:
    book = kalshi_team_total_quotes([team_total("PHI", 110.0, 0.4, 0.3)])
    assert not book.quotes


# ----------------------------------------------------------------- player props: excluded


def prop(n: float, ask: float, bid: float) -> dict[str, Any]:
    t = f"KXNBAPTS-{EV}-PHITMAXEY0-{int(n + 0.5)}"
    return km(t, f"Tyrese Maxey: {int(n + 0.5)}+ points", n, ask, bid)


def test_player_prop_ladder_is_parsed_but_never_alertable(cfg: Any) -> None:
    mk = [prop(19.5, 0.40, 0.38), prop(24.5, 0.60, 0.50)]
    book = kalshi_prop_quotes(mk)
    assert find_ladder_violations(book, K, cfg, leg_fee_fn(cfg)) == []  # default: not even listed
    res = find_ladder_violations(book, K, cfg, leg_fee_fn(cfg), include_props=True)
    assert len(res) == 1 and res[0].excluded == ladder.EXCLUDED_PROP
    assert is_ladder_alertable(res[0], cfg) == "excluded"
    assert kalshi_ladder_quotes(mk).quotes == {}  # the live universe never contains props


def test_include_player_props_flag_is_off_in_config(cfg: Any) -> None:
    assert cfg.section("ladder")["include_player_props"] is False


# ------------------------------------------------------------------------- confirm


class Pair:
    """A tiny fetcher: GET /markets/{ticker} serves from a dict; a clock hook moves time."""

    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self.m = {m["ticker"]: m for m in markets}
        self.urls: list[str] = []
        self.t = 0.0
        self.step = 0.2
        self.fail: set[str] = set()

    def mono(self) -> float:
        self.t += self.step
        return self.t

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        self.urls.append(url)
        t = url.rsplit("/", 1)[-1]
        if t in self.fail or t not in self.m:
            return Response(404, b"{}", url)
        return Response(200, json.dumps({"market": self.m[t]}).encode(), url)


def scanner(cfg: Any, p: Pair) -> LadderScanner:
    return LadderScanner(cfg, p, PolymarketUSClient(p.get), mono=p.mono)


MK = [ml("PHI", 0.40, 0.38), spread("PHI", 4.5, 0.55, 0.50)]


def first(cfg: Any, mk: list[dict[str, Any]]) -> ladder.LadderResult:
    return find_ladder_violations(kalshi_ladder_quotes(mk), K, cfg, leg_fee_fn(cfg))[0]


def test_confirm_back_to_back_passes(cfg: Any) -> None:
    p = Pair(MK)
    new, info = scanner(cfg, p).confirm(first(cfg, MK))
    assert new is not None and info["result"] == "confirmed"
    assert len(p.urls) == 2  # exactly the two legs, nothing else
    assert all("/markets/KXNBA" in u for u in p.urls)
    assert info["gap_s"] <= cfg.section("alert")["confirm_max_gap_s"]


def test_confirm_refuses_when_the_gap_is_gone_after_refetch(cfg: Any) -> None:
    p = Pair([ml("PHI", 0.49, 0.47), spread("PHI", 4.5, 0.55, 0.50)])
    new, info = scanner(cfg, p).confirm(first(cfg, MK))
    assert new is None and info["reason"].startswith("after_refetch")


def test_confirm_refuses_legs_more_than_five_seconds_apart(cfg: Any) -> None:
    p = Pair(MK)
    p.step = 6.0
    new, info = scanner(cfg, p).confirm(first(cfg, MK))
    assert new is None and info["reason"] == "legs_too_far_apart"


def test_confirm_refuses_when_a_leg_cannot_be_reread_or_closes(cfg: Any) -> None:
    p = Pair(MK)
    p.fail = {MK[1]["ticker"]}
    assert scanner(cfg, p).confirm(first(cfg, MK))[1]["reason"] == "refetch_failed"
    closed = [ml("PHI", 0.40, 0.38, status="closed"), MK[1]]
    new, info = scanner(cfg, Pair(closed)).confirm(first(cfg, MK))
    assert new is None and info["reason"] == "side_missing_or_unsized_after_refetch"


def test_confirm_refuses_when_depth_vanishes(cfg: Any) -> None:
    thin = [ml("PHI", 0.40, 0.38, ask_sz=0.0), MK[1]]
    new, info = scanner(cfg, Pair(thin)).confirm(first(cfg, MK))
    assert new is None and info["reason"] == "side_missing_or_unsized_after_refetch"


# -------------------------------------------------------------------- Polymarket US ladder


def pm_book(slug: str, offers: list[tuple[float, float]], bids: list[tuple[float, float]]) -> Any:
    def row(p: float, q: float) -> dict[str, Any]:
        return {"px": {"value": f"{p:.4f}", "currency": "USD"}, "qty": f"{q:.4f}"}

    return {
        "marketData": {
            "marketSlug": slug,
            "state": "MARKET_STATE_OPEN",
            "bids": [row(*b) for b in bids],
            "offers": [row(*o) for o in offers],
        }
    }


class PMFetcher:
    def __init__(self, books: dict[str, Any]) -> None:
        self.books = books
        self.urls: list[str] = []
        self.t = 0.0

    def mono(self) -> float:
        self.t += 0.3
        return self.t

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        self.urls.append(url)
        slug = url.rsplit("/", 2)[-2]
        if url.endswith("/book") and slug in self.books:
            return Response(200, json.dumps(self.books[slug]).encode(), url)
        return Response(404, b"{}", url)


def test_polymarket_us_spread_ladder_found_and_confirmed(cfg: Any) -> None:
    ev = load("pm_us_event_detphi.json")
    # Spread markets are +/-N on (DET long, PHI short). PHI -1.5 ("home margin > 1.5", YES)
    # and PHI -4.5 (home margin > 4.5): buy YES@1.5 via the SHORT side at 1 - bid, and NO@4.5
    # via the LONG side (DET +4.5) at its offer.
    s15, s45 = "asc-nba-det-phi-2026-10-25-pos-1pt5", "asc-nba-det-phi-2026-10-25-pos-4pt5"
    books = {
        s15: pm_book(s15, offers=[(0.60, 50)], bids=[(0.55, 40)]),  # short ask = 1 - 0.55 = 0.45
        s45: pm_book(s45, offers=[(0.38, 30)], bids=[(0.30, 30)]),  # long ask = 0.38
    }
    qb = venues.us_quotes(ev, cfg.us_fee.coefficient, books)
    res = find_ladder_violations(qb, PM_US, cfg, leg_fee_fn(cfg))
    r = next(x for x in res if (x.lo.c, x.hi.c) == (1.5, 4.5))
    assert r.yes.ref == s15 and r.no.ref == s45
    assert r.gross_gap == pytest.approx(1 - 0.45 - 0.38)
    assert r.max_n == 30  # the thinner leg
    fee = cfg.us_fee.order_fee(0.45, 10) + cfg.us_fee.order_fee(0.38, 10)
    assert pair_margin_at(r, cfg, 10) == pytest.approx(1 - (0.83 * 10 + fee) / 10)
    cfg = verified(cfg)  # Polymarket US ladders confirm only with the rulebook verified
    f = PMFetcher(books)
    sc = LadderScanner(cfg, f, PolymarketUSClient(f.get), mono=f.mono)
    new, info = sc.confirm(r, ev)
    assert new is not None and info["result"] == "confirmed"
    assert [u.rsplit("/", 2)[-2] for u in f.urls] == [s15, s45]  # two book reads, in leg order
    text = sinks.ladder_text(new, gap_s=info["gap_s"])
    assert "Polymarket US" in text and s15 in text and s45 in text


def pair_margin_at(r: Any, cfg: Any, n: int) -> float:
    m = ladder.pair_margin(r.yes, r.no, n, cfg, leg_fee_fn(cfg))
    assert m is not None
    return m


def test_polymarket_us_candidate_slugs_use_event_quotes_only_to_choose(cfg: Any) -> None:
    ev = load("pm_us_event_detphi.json")
    f = PMFetcher({})
    sc = LadderScanner(cfg, f, PolymarketUSClient(f.get))
    assert not f.urls  # selecting candidates reads no network
    slugs = sc.pm_candidate_slugs(ev)
    assert len(slugs) <= cfg.section("ladder")["max_books_per_game"]


def test_polymarket_us_event_quote_leg_without_book_is_unknown_depth(cfg: Any) -> None:
    ev = load("pm_us_event_detphi.json")
    for m in ev["markets"]:
        if m["slug"].endswith("pos-4pt5"):
            m["marketSides"][0]["quote"]["value"] = "0.30"  # a stale-looking cheap DET +4.5
    qb = venues.us_quotes(ev, cfg.us_fee.coefficient)  # no books at all
    res = find_ladder_violations(qb, PM_US, cfg, leg_fee_fn(cfg))
    assert res and all(is_ladder_alertable(r, cfg) in ("unknown_depth", "below_gap") for r in res)
    assert not any(is_ladder_alertable(r, cfg) == "alert" for r in res)


# --------------------------------------------------------------------------- alert text


def test_alert_text_names_both_buys_profit_depth_and_dedups(cfg: Any, tmp_path: Path) -> None:
    r = first(cfg, MK)
    text = sinks.ladder_text(r, gap_s=0.4)
    assert text.startswith("LADDER DET@PHI margin>0 vs margin>4.5: BUY Kalshi KXNBAGAME-")
    assert "BUY Kalshi KXNBASPREAD-26OCT25DETPHI-PHI5 NO @0.50" in text
    assert "locks +" in text and "/pair after fees" in text and "depth ~" in text
    assert "pays >= $1" in text and "void risk" in text
    assert "stake" not in text.lower() and len(text) < 420
    sink = sinks.AlertSink(tmp_path / "ALERTS.md", tmp_path / "d.json", 30)
    t0 = datetime(2026, 10, 25, 20, 0)
    key = sinks.ladder_key(r)
    assert sink.emit(key, text, t0, "2026-10-25 16:00")
    assert not sink.emit(key, text, t0 + timedelta(minutes=29), "x")
    assert sink.emit(key, text, t0 + timedelta(minutes=31), "y")
    assert (tmp_path / "ALERTS.md").read_text().count("[fastarb] LADDER") == 2


# ------------------------------------------------------------------------- runner tick


def _tick_runner(cfg: Any, tmp: Path, extra: list[dict[str, Any]]) -> tuple[Runner, FakeFetcher]:
    tip = datetime(2026, 10, 25, 23, 30)
    f = FakeFetcher(load("pm_us_event_detphi.json"), load("kalshi_detphi.json") + extra)
    r = Runner(
        cfg,
        f,  # type: ignore[arg-type]
        raw=sinks.RawWriter(tmp, "2026-10-25"),
        sink=sinks.AlertSink(tmp / "ALERTS.md", tmp / "d.json", 30),
        clock=lambda: tip - timedelta(minutes=30),
        tips=lambda day: {(GAME.away, GAME.home): tip},
    )
    return r, f


def test_tick_on_recorded_slate_has_no_confirmed_ladder(cfg: Any, tmp_path: Path) -> None:
    r, _ = _tick_runner(cfg, tmp_path, [])
    rep = r.tick(date(2026, 10, 25))
    assert rep.ladders_alertable == 0 and not any(a.startswith("LADDER") for a in rep.alerts)


def test_tick_alerts_a_planted_team_total_ladder_once(cfg: Any, tmp_path: Path) -> None:
    extra = [team_total("PHI", 110.5, 0.45, 0.40), team_total("PHI", 112.5, 0.60, 0.55)]
    r, f = _tick_runner(cfg, tmp_path, extra)
    rep = r.tick(date(2026, 10, 25))
    assert rep.ladders_alertable == 1 and rep.ladders_apparent >= 1
    alerts = (tmp_path / "ALERTS.md").read_text()
    assert alerts.count("LADDER DET@PHI PHI team total>110.5 vs >112.5") == 1
    assert "KXNBATEAMTOTAL-26OCT25DETPHI-PHI111 YES" in alerts
    assert "KXNBATEAMTOTAL-26OCT25DETPHI-PHI113 NO" in alerts
    r.tick(date(2026, 10, 25))  # same key inside the window: suppressed
    assert (tmp_path / "ALERTS.md").read_text().count("LADDER") == 1
    cands = (tmp_path / "fastarb_2026-10-25" / "candidates.jsonl").read_text()
    assert '"type": "ladder"' in cands and '"result": "confirmed"' in cands
    assert all(
        u.startswith(("https://api.elections.kalshi.com", "https://gateway.polymarket.us"))
        for u in f.urls
    )


def test_tick_never_reports_a_player_prop_ladder(cfg: Any, tmp_path: Path) -> None:
    r, f = _tick_runner(cfg, tmp_path, [prop(19.5, 0.40, 0.38), prop(24.5, 0.60, 0.50)])
    rep = r.tick(date(2026, 10, 25))
    assert rep.ladders_alertable == 0
    assert not any("KXNBAPTS" in u for u in f.urls)  # the poller does not even fetch props


def test_ladders_can_be_switched_off(cfg: Any, tmp_path: Path) -> None:
    raw = json.loads(json.dumps(cfg.raw))
    raw["ladder"]["enabled"] = False
    off = type(cfg)(raw, cfg.kalshi_fee, cfg.us_fee, cfg.intl_fee)
    extra = [team_total("PHI", 110.5, 0.45, 0.40), team_total("PHI", 112.5, 0.60, 0.55)]
    r, f = _tick_runner(off, tmp_path, extra)
    rep = r.tick(date(2026, 10, 25))
    assert rep.ladders_apparent == 0 and not any("KXNBATEAMTOTAL" in u for u in f.urls)


# ----------------------------------------------------------------------- JARVIS source


def _game() -> Game:
    return Game(
        game_id="0022600001",
        home_team=team_id("PHI") or 0,
        away_team=team_id("DET") or 0,
        tipoff=datetime(2026, 10, 25, 23, 30),
        home_abbr="PHI",
        away_abbr="DET",
    )


def _httpx_get(f: FakeFetcher) -> Any:
    def get(url: str, timeout: float = 10.0, **_: Any) -> Any:
        from urllib.parse import parse_qs, urlparse

        u = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        resp = f.get(f"{u.scheme}://{u.netloc}{u.path}", params)
        assert resp is not None
        return SimpleNamespace(status_code=resp.status, content=resp.body)

    return get


def test_confirmed_ladder_arbs_returns_the_pair_and_nothing_else(cfg: Any) -> None:
    extra = [team_total("PHI", 110.5, 0.45, 0.40), team_total("PHI", 112.5, 0.60, 0.55)]
    f = FakeFetcher(load("pm_us_event_detphi.json"), load("kalshi_detphi.json") + extra)
    now = datetime(2026, 10, 25, 20, 0)
    out = confirmed_ladder_arbs(
        SimpleNamespace(confirmed_max_gap_s=5.0),
        [_game()],
        date(2026, 10, 25),
        now,
        get=_httpx_get(f),
        config=cfg,
        clock=lambda: now,
    )
    assert len(out) == 1
    a = out[0]
    assert a.yes_leg.side == "YES" and a.no_leg.side == "NO"
    assert (a.yes_leg.venue, a.no_leg.venue) == (K, K)
    assert a.yes_leg.ticker.endswith("PHI111") and a.no_leg.ticker.endswith("PHI113")
    assert (
        a.net_per_pair > cfg.section("ladder")["min_gap"]
        and a.cost_per_pair == pytest.approx(1.0 - 0.10 - (0.05 + 0.0) + 0.0)
        or a.cost_per_pair < 1
    )
    assert a.leg_gap_s <= 5.0 and a.ts == now
    assert "fair price" in a.note


def test_confirmed_ladder_arbs_empty_on_recorded_slate_and_after_tip(cfg: Any) -> None:
    f = FakeFetcher(load("pm_us_event_detphi.json"), load("kalshi_detphi.json"))
    now = datetime(2026, 10, 25, 20, 0)
    args = (SimpleNamespace(confirmed_max_gap_s=5.0), [_game()], date(2026, 10, 25))
    kw: dict[str, Any] = {"get": _httpx_get(f), "config": cfg}
    assert confirmed_ladder_arbs(*args, now, **kw) == []
    extra = [team_total("PHI", 110.5, 0.45, 0.40), team_total("PHI", 112.5, 0.60, 0.55)]
    f2 = FakeFetcher(load("pm_us_event_detphi.json"), load("kalshi_detphi.json") + extra)
    late = datetime(2026, 10, 25, 23, 31)  # past the tip: no pre-tip quote
    assert confirmed_ladder_arbs(*args, late, get=_httpx_get(f2), config=cfg) == []
    strict = SimpleNamespace(confirmed_max_gap_s=1.0)  # consumer window tighter than ours
    assert (
        confirmed_ladder_arbs(
            strict, [_game()], date(2026, 10, 25), now, get=_httpx_get(f2), config=cfg
        )
        == []
    )


# --------------------------------------------------------------------- snapshot dry run


def _mini_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR,"
        " title VARCHAR, player_id INTEGER, stat VARCHAR, threshold FLOAT, open_time TIMESTAMP,"
        " close_time TIMESTAMP, settled_ts TIMESTAMP, result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT,"
        " last FLOAT, volume INTEGER, open_interest INTEGER, source VARCHAR)"
    )
    rows = [
        (f"KXNBAGAME-{EV}-PHI", "KXNBAGAME", f"KXNBAGAME-{EV}", "PHI wins", None),
        (f"KXNBASPREAD-{EV}-PHI5", "KXNBASPREAD", f"KXNBASPREAD-{EV}", "PHI by over 4.5", 4.5),
        (f"KXNBAPTS-{EV}-PHITMAXEY0-20", "KXNBAPTS", f"KXNBAPTS-{EV}", "Maxey: 20+ points", 20.0),
        (f"KXNBAPTS-{EV}-PHITMAXEY0-25", "KXNBAPTS", f"KXNBAPTS-{EV}", "Maxey: 25+ points", 25.0),
    ]
    for t, s, e, ti, th in rows:
        con.execute(
            "INSERT INTO kalshi_markets VALUES (?,?,?,?,NULL,NULL,?,NULL,NULL,NULL,NULL)",
            [t, s, e, ti, th],
        )
    snaps = [
        # (ts, ML bid/ask, spread bid/ask, pts20 bid/ask, pts25 bid/ask)
        (datetime(2026, 10, 25, 18, 0), (0.38, 0.40), (0.50, 0.55), (0.37, 0.40), (0.50, 0.60)),
        (datetime(2026, 10, 25, 18, 15), (0.58, 0.60), (0.40, 0.45), (0.50, 0.55), (0.30, 0.40)),
    ]
    for ts, a, b, c, d in snaps:
        for (t, *_), (bid, ask) in zip(rows, (a, b, c, d), strict=True):
            con.execute(
                "INSERT INTO kalshi_prices VALUES (?,?,?,?,0,0,0,'live')", [t, ts, bid, ask]
            )
    con.close()


def test_snapshot_dry_run_counts_apparent_violations_only(cfg: Any, tmp_path: Path) -> None:
    db = tmp_path / "copy.duckdb"
    _mini_db(db)
    rep = ladder_dryrun.run(db, cfg, date(2026, 10, 25), date(2026, 10, 25))
    assert rep.snapshots == 2
    m, p = rep.by_family["margin"], rep.by_family["prop"]
    assert m.snapshot_pairs_apparent == 1 and m.snapshot_pairs_net_positive == 1
    assert p.snapshot_pairs_apparent == 1  # prop rungs are counted, never alerted
    text = "\n".join(rep.lines())
    assert "APPARENT, UNCONFIRMED" in text and "profit" in text


def test_cli_refuses_the_live_snapshot_db(cfg: Any, capsys: Any) -> None:
    live = cfg.resolve("data/kalshi/kalshi.duckdb")
    rc = cli_main(
        ["ladder-dryrun", "--db", str(live), "--from", "2026-10-20", "--to", "2026-10-25"]
    )
    assert rc == 2 and "COPY" in capsys.readouterr().err


def test_ladder_modules_have_no_order_or_credential_code() -> None:
    root = Path(ladder.__file__).parent
    for name in ("ladder.py", "ladder_source.py", "ladder_dryrun.py"):
        text = (root / name).read_text().lower()
        for banned in (
            "place_order",
            "create_order",
            "/portfolio",
            "/orders",
            "api_key",
            "authorization",
            ".post(",
        ):
            assert banned not in text, (name, banned)


# ------------------------------------------------ review 2026-10-25: Polymarket US gate


S15, S45 = "asc-nba-det-phi-2026-10-25-pos-1pt5", "asc-nba-det-phi-2026-10-25-pos-4pt5"


def verified(cfg: Any, flag: bool = True) -> Any:
    raw = json.loads(json.dumps(cfg.raw))
    raw["settlement_rules_verified"] = flag
    return type(cfg)(raw, cfg.kalshi_fee, cfg.us_fee, cfg.intl_fee)


def _pm_event_with_gap() -> dict[str, Any]:
    ev = load("pm_us_event_detphi.json")
    for m in ev["markets"]:
        if m["slug"] == S15:
            m["marketSides"][1]["quote"]["value"] = "0.45"  # PHI -1.5 (short)
        if m["slug"] == S45:
            m["marketSides"][0]["quote"]["value"] = "0.38"  # DET +4.5 (long)
    out: dict[str, Any] = ev
    return out


class PMLadderFetcher(FakeFetcher):
    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        books = {
            S15: pm_book(S15, offers=[(0.60, 50)], bids=[(0.55, 40)]),
            S45: pm_book(S45, offers=[(0.38, 30)], bids=[(0.30, 30)]),
        }
        for slug, b in books.items():
            if url.endswith(f"/{slug}/book"):
                self.requests += 1
                return Response(200, json.dumps(b).encode(), url)
        return super().get(url, params)


def test_polymarket_ladder_confirms_only_when_rules_are_verified(cfg: Any) -> None:
    ev = _pm_event_with_gap()
    books = {
        S15: pm_book(S15, offers=[(0.60, 50)], bids=[(0.55, 40)]),
        S45: pm_book(S45, offers=[(0.38, 30)], bids=[(0.30, 30)]),
    }
    qb = venues.us_quotes(ev, cfg.us_fee.coefficient, books)
    r = next(x for x in find_ladder_violations(qb, PM_US, cfg, leg_fee_fn(cfg)) if x.lo.c == 1.5)
    assert cfg.settlement_rules_verified is False
    f = PMFetcher(books)
    sc = LadderScanner(cfg, f, PolymarketUSClient(f.get), mono=f.mono)
    new, info = sc.confirm(r, ev)
    assert new is None and info["reason"] == ladder.PM_RULES_UNVERIFIED and not f.urls
    ok = verified(cfg)
    f2 = PMFetcher(books)
    new, info = LadderScanner(ok, f2, PolymarketUSClient(f2.get), mono=f2.mono).confirm(r, ev)
    assert new is not None
    text = sinks.ladder_text(new, gap_s=1.0)
    assert "pays >= $1 if the game is played" in text and "Polymarket US" in text
    assert "either way" not in text
    halted = json.loads(json.dumps(books))
    halted[S45]["marketData"]["state"] = "MARKET_STATE_HALTED"
    f3 = PMFetcher(halted)
    sc3 = LadderScanner(ok, f3, PolymarketUSClient(f3.get), mono=f3.mono)
    assert sc3.confirm(r, ev)[1]["reason"] == "book_not_open"


def test_runner_and_jarvis_emit_no_polymarket_ladder_while_unverified(
    cfg: Any, tmp_path: Path
) -> None:
    tip = datetime(2026, 10, 25, 23, 30)

    def runner(c: Any, d: Path) -> tuple[Runner, FakeFetcher]:
        f = PMLadderFetcher(_pm_event_with_gap(), load("kalshi_detphi.json"))
        r = Runner(
            c,
            f,  # type: ignore[arg-type]
            raw=sinks.RawWriter(d, "2026-10-25"),
            sink=sinks.AlertSink(d / "ALERTS.md", d / "d.json", 30),
            clock=lambda: tip - timedelta(minutes=30),
            tips=lambda day: {(GAME.away, GAME.home): tip},
        )
        return r, f

    r, _ = runner(cfg, tmp_path)
    rep = r.tick(date(2026, 10, 25))
    assert rep.ladders_alertable == 0 and not any(a.startswith("LADDER") for a in rep.alerts)
    assert rep.skipped["ladder:pm_us_rules_unverified"] >= 1
    cands = (tmp_path / "fastarb_2026-10-25" / "candidates.jsonl").read_text()
    assert ladder.PM_RULES_UNVERIFIED in cands
    on = verified(cfg)  # the same planted ladder is found once the flag is true
    r2, _ = runner(on, tmp_path / "on")
    rep2 = r2.tick(date(2026, 10, 25))
    assert rep2.ladders_alertable >= 1 and any(a.startswith("LADDER") for a in rep2.alerts)
    now = datetime(2026, 10, 25, 20, 0)
    args = (SimpleNamespace(confirmed_max_gap_s=5.0), [_game()], date(2026, 10, 25), now)
    f_off = PMLadderFetcher(_pm_event_with_gap(), load("kalshi_detphi.json"))
    assert confirmed_ladder_arbs(*args, get=_httpx_get(f_off), config=cfg) == []
    f_on = PMLadderFetcher(_pm_event_with_gap(), load("kalshi_detphi.json"))
    out = confirmed_ladder_arbs(*args, get=_httpx_get(f_on), config=on)
    assert out and out[0].yes_leg.venue == PM_US
    assert "pays >= $1 if the game is played" in out[0].note


def test_jarvis_missing_gap_attribute_fails_closed(cfg: Any) -> None:
    f = FakeFetcher(load("pm_us_event_detphi.json"), load("kalshi_detphi.json"))
    now = datetime(2026, 10, 25, 20, 0)
    out = confirmed_ladder_arbs(
        SimpleNamespace(), [_game()], date(2026, 10, 25), now, get=_httpx_get(f), config=cfg
    )
    assert out == []
