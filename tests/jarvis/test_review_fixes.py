# ruff: noqa: E501
"""Tests for the adversary review's blockers and majors (docs/reviews/jarvis_paper_review_2026-10-25.md)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from nba.jarvis import sources
from nba.jarvis.book import PaperBook, kill_switch, weekly_summary
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Play, PlayLeg, Quote
from nba.jarvis.render import play_lines
from nba.jarvis.runner import Runtime, run_tick, supports_alert
from nba.jarvis.slate import (
    PinnacleRefs,
    SlateState,
    arbitrage_candidate,
    best_per_game,
    gather,
    single_candidate,
    size_plays,
)
from nba.jarvis.sources import load_games
from tests.jarvis.conftest import G1, LAC, LAL, NOW, SLATE, TIP1, Sent, StubModel


def state(cfg: JarvisConfig, quotes: list[Quote], now=NOW) -> SlateState:  # type: ignore[no-untyped-def]
    return SlateState(SLATE, now, load_games(cfg, SLATE), quotes, PinnacleRefs())


def wq(venue: str, tid: int, bid: float, ask: float, age_min: float, **kw) -> Quote:  # type: ignore[no-untyped-def]
    ab = "LAL" if tid == LAL else "LAC"
    return Quote(
        venue, f"{venue}:{ab}", G1, "game_win", NOW - timedelta(minutes=age_min), bid, ask,
        team_id=tid, label=f"{ab} to win", label_no=f"{'LAC' if ab == 'LAL' else 'LAL'} to win", **kw,
    )  # fmt: skip


# ---------------------------------------------------------------------------------- B1


def test_a_stale_kalshi_leg_plus_a_fresh_polymarket_leg_is_not_an_arbitrage(
    cfg: JarvisConfig,
) -> None:
    k = wq("kalshi", LAL, 0.48, 0.50, age_min=29)
    p = wq("polymarket", LAC, 0.44, 0.45, age_min=0)
    st = state(cfg, [k, p])
    play = arbitrage_candidate(cfg, st, st.games[0], [k, p])
    assert play is not None and play.label == "arb_candidate"
    assert "arbitrage" not in play.label and "unconfirmed" in " ".join(play.notes)
    assert any(n.startswith("RESOLUTION RISK") for n in play.notes)
    assert not supports_alert(cfg, replace(play, contracts=3))  # arb_candidate never alerts
    text = "\n".join(play_lines(replace(play, contracts=3, stake_usd=2.0), cfg.display_tz))
    assert "ARB CANDIDATE" in text and "UNCONFIRMED" in text and "RESOLUTION RISK" in text


def test_only_a_confirmed_live_source_can_make_an_arbitrage(cfg: JarvisConfig) -> None:
    k = replace(wq("kalshi", LAL, 0.48, 0.50, age_min=0), confirmed=True)
    p = replace(wq("polymarket", LAC, 0.44, 0.45, age_min=0), confirmed=True)
    st = state(cfg, [k, p])
    play = arbitrage_candidate(cfg, st, st.games[0], [k, p])
    assert play is not None and play.label == "arbitrage"
    assert supports_alert(cfg, replace(play, contracts=3))
    assert E_stake(cfg, "arbitrage") == 10.0


def E_stake(cfg: JarvisConfig, label: str) -> float:
    from nba.jarvis import edge as E

    return E.stake_for(cfg, label)


def test_an_unconfirmed_candidate_is_sized_as_thin_at_most(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    k, p = wq("kalshi", LAL, 0.40, 0.42, age_min=2), wq("polymarket", LAC, 0.50, 0.52, age_min=0)
    st = state(cfg, [k, p])
    play = arbitrage_candidate(cfg, st, st.games[0], [k, p])
    assert play is not None
    sized = size_plays(cfg, [play], PaperBook(tmp_path / "b.duckdb"), SLATE)[0]
    assert 0 < sized.stake_usd <= cfg.ladder["thin"]


# ---------------------------------------------------------------------------------- B2


def test_a_stale_quote_against_a_fresher_pinnacle_move_is_thin_and_never_texts(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    # Kalshi snapshot 29 minutes old, Pinnacle read after it: the price we "beat" may be gone
    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop")
    old = replace(prop, ts=NOW - timedelta(minutes=29))
    p = single_candidate(cfg, st, old, "yes")
    assert p is not None and p.label == "thin"
    assert any("capped at thin" in n or "needs a fresh one" in n for n in p.notes)
    assert not supports_alert(cfg, replace(p, contracts=2))
    # and through the whole tick: a 10-minute-old snapshot cannot alert either
    rt.clock = lambda: NOW + timedelta(minutes=10)
    run_tick(rt, SLATE)
    assert not any("ALERT" in t for t in sent.texts)


def test_pinnacle_newer_than_the_quote_caps_the_label(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop")
    older = replace(prop, ts=NOW - timedelta(minutes=45))  # Pinnacle (19:30) is 15 min NEWER
    p = single_candidate(cfg, st, older, "yes")
    assert (
        p is not None and p.label == "thin" and any("newer than this price" in n for n in p.notes)
    )


# ---------------------------------------------------------------------------------- B3


def mkplay(label: str, key: str, edge: float, price: float = 0.5, fair: float | None = 0.5) -> Play:
    leg = PlayLeg("kalshi", key, "yes", price, "game_win", f"{key} to win", team_id=LAL)
    return Play(G1, "LAL@LAC", TIP1, (leg,), label, edge, fair, 2.0, price, 0, 0.0, "r")


def test_stake_is_non_decreasing_in_the_label(cfg: JarvisConfig, tmp_path: Path) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    out = {
        p.label: p
        for p in (
            size_plays(cfg, [mkplay(lb, lb, e)], book, SLATE)[0]
            for lb, e in (("none", -0.2), ("thin", 0.7), ("strong", 3.2), ("strong", 12.0))
        )
    }
    assert out["none"].stake_usd <= out["thin"].stake_usd <= out["strong"].stake_usd
    assert out["none"].contracts <= cfg.none_max_contracts
    assert out["strong"].stake_usd > out["thin"].stake_usd > out["none"].stake_usd


def test_entertainment_cannot_spend_the_cap_reserved_for_a_strong_edge(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    # eight near-fair plays recorded earlier tonight (the T-90 preview), $1.25 each = $10
    nones = [
        replace(
            mkplay("none", f"N{i}", -0.2), inputs={"leg_costs": [1.25]}, contracts=2, stake_usd=1.25
        )
        for i in range(8)
    ]
    book.record(nones, source="slate", slate=SLATE, now=NOW)
    assert book.spent_split(SLATE) == (0.0, pytest.approx(10.0))
    late_none = size_plays(cfg, [mkplay("none", "N9", -0.2)], book, SLATE)[0]
    assert late_none.contracts == 0  # the non-reserved $10 is used
    strong = size_plays(cfg, [mkplay("strong", "S", 10.2, price=0.44, fair=0.58)], book, SLATE)[0]
    assert strong.stake_usd >= 3.0 and strong.contracts >= 1  # the reserve is still there for it


def test_kill_switch_drops_every_stake_to_the_none_size(cfg: JarvisConfig, tmp_path: Path) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    for i in range(cfg.kill_min_bets):
        p = replace(
            mkplay("thin", f"T{i}", 1.0), inputs={"leg_costs": [1.0]}, contracts=2, stake_usd=1.0
        )
        book.record(
            [p],
            source="slate",
            slate=SLATE - timedelta(days=1 + i // 5),
            now=NOW + timedelta(minutes=i),
        )
    con = book._con()
    con.execute(
        "INSERT INTO scores SELECT play_id, ?, 'settled', TRUE, 0.5, NULL, NULL, NULL, '', -0.8, NULL FROM plays",
        [NOW],
    )
    con.close()
    ks = kill_switch(cfg, book)
    assert ks.active and ks.n == cfg.kill_min_bets and ks.mean_clv is not None and ks.mean_clv < 0
    sized = size_plays(
        cfg, [mkplay("strong", "S", 10.0, price=0.44, fair=0.58)], book, SLATE, none_level=True
    )[0]
    assert sized.stake_usd <= 1.0 + 1e-9 and sized.contracts <= cfg.none_max_contracts
    # fewer than the minimum number of bets: not active, whatever the mean
    assert not kill_switch(cfg, PaperBook(tmp_path / "empty.duckdb")).active


# ---------------------------------------------------------------------------------- M1, M5, M6


def test_every_leg_is_phrased_as_the_outcome_to_buy(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    q = next(q for q in st.quotes if q.kind == "game_win" and q.team_id == LAL)
    no = single_candidate(cfg, st, q, "no")
    assert no is not None
    line = [
        ln
        for ln in play_lines(replace(no, contracts=2, stake_usd=1.0), cfg.display_tz)
        if ln.startswith("PLAY")
    ][0]
    assert "BUY LAC to win on Kalshi (NO contract)" in line and "to lose" not in line


def test_a_polymarket_us_cover_leg_never_says_no(cfg: JarvisConfig) -> None:
    k = wq("kalshi", LAL, 0.48, 0.50, age_min=1)
    p = wq("polymarket", LAC, 0.44, 0.45, age_min=0)
    st = state(cfg, [k, p])
    play = arbitrage_candidate(cfg, st, st.games[0], [k, p])
    assert play is not None
    body = "\n".join(play_lines(replace(play, contracts=2, stake_usd=1.9), cfg.display_tz))
    assert (
        "BUY LAC to win on Polymarket US at 45c" in body
        and "NO contract" not in body.split("Polymarket")[1]
    )


def test_a_measured_pick_says_it_is_the_best_of_n(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    best = best_per_game(cfg, st)[G1]
    assert any(n.startswith("best of ") and "priced bets" in n for n in best.notes)


def test_a_yes_prop_is_capped_at_thin_without_a_p_plays(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)  # no model: no P(plays)
    prop = next(q for q in st.quotes if q.kind == "prop")
    p = single_candidate(cfg, st, prop, "yes")
    assert p is not None and p.label == "thin" and any("DNP rule unverified" in n for n in p.notes)
    verified = type(cfg)(**{**cfg.__dict__, "kalshi_prop_dnp_rule_verified": True})
    assert single_candidate(verified, st, prop, "yes").label == "strong"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------- M2, M3


def seed_scores(book: PaperBook, rows: list[tuple[str, str, float | None, float]]) -> None:
    """rows: (ticker, label, clv, pnl) -> one settled score per play."""
    con = book._con()
    for t, _lb, clv, pnl in rows:
        con.execute(
            "INSERT INTO scores SELECT play_id, ?, 'settled', ?, ?, NULL, NULL, NULL, '', ?, NULL "
            "FROM plays WHERE ticker = ?",
            [NOW, pnl > 0, pnl, clv, t],
        )
    con.close()


def test_a_two_leg_play_and_a_reemission_count_once_in_the_summary(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    legs = (
        PlayLeg("kalshi", "A", "yes", 0.4, "game_win", "A", team_id=LAL),
        PlayLeg("polymarket", "B", "yes", 0.5, "game_win", "B", team_id=LAC),
    )
    two = Play(
        G1,
        "LAL@LAC",
        TIP1,
        legs,
        "arb_candidate",
        2.0,
        None,
        3.0,
        0.4,
        2,
        1.8,
        "r",
        inputs={"leg_costs": [0.8, 1.0]},
    )
    book.record([two], source="slate", slate=SLATE, now=NOW)
    book.record([two], source="alert", slate=SLATE, now=NOW + timedelta(hours=1))  # re-emission
    seed_scores(book, [("A", "arb_candidate", 1.0, 0.1), ("B", "arb_candidate", 3.0, 0.1)])
    s = weekly_summary(cfg, book, until=SLATE)
    d = s["labels"]["arb_candidate"]
    assert (
        d["n"] == 1
        and d["pnl_usd"] == pytest.approx(0.2)
        and d["clv_mean_cents"] == pytest.approx(2.0)
    )
    assert s["labels"]["all"]["n"] == 1


def test_a_one_sided_close_has_no_clv_and_the_null_rate_is_reported(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    from nba.jarvis.book import closing_quote, score_book, two_sided

    assert two_sided(0.4, 0.6) and not two_sided(0.0, 0.3) and not two_sided(0.4, 1.0)
    book = PaperBook(tmp_path / "b.duckdb")
    p = replace(
        mkplay("thin", "KXNBAGAME-26OCT28LALLAC-LAL", 1.0),
        inputs={"leg_costs": [1.0]},
        contracts=2,
        stake_usd=1.0,
    )
    book.record([p], source="slate", slate=SLATE, now=NOW)
    con = book._con()
    con.execute(
        "INSERT INTO quote_log VALUES ('kalshi',?,?,0.0,0.30)",
        [p.legs[0].ticker, TIP1 - timedelta(minutes=1)],
    )
    # the newer Kalshi snapshot (19:58) is older than the quote_log row, so quote_log is the close
    cq = closing_quote(cfg, con, {"venue": "kalshi", "ticker": p.legs[0].ticker, "tipoff": TIP1})
    con.close()
    assert cq is not None and cq[3] == "quote_log"
    d = duckdb.connect(str(cfg.paths["nba_db"]))
    d.execute("UPDATE games SET home_pts = 100, away_pts = 110 WHERE game_id = ?", [G1])
    d.close()
    score_book(cfg, book, now=TIP1 + timedelta(hours=3))
    s = weekly_summary(cfg, book, until=SLATE)
    assert s["labels"]["thin"]["n_clv"] == 0 and s["labels"]["thin"]["clv_null_rate"] == 1.0


def test_the_closing_quote_is_the_latest_across_both_sources(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    from nba.jarvis.book import closing_quote

    book = PaperBook(tmp_path / "b.duckdb")
    con = book._con()
    t = "KXNBAGAME-26OCT28LALLAC-LAL"
    con.execute(
        "INSERT INTO quote_log VALUES ('kalshi',?,?,0.40,0.44)", [t, TIP1 - timedelta(minutes=60)]
    )
    kc = duckdb.connect(str(cfg.paths["kalshi_db"]))
    kc.execute(
        "INSERT INTO kalshi_prices VALUES (?,?,0.45,0.49,NULL,0,0,'live')",
        [t, TIP1 - timedelta(minutes=5)],
    )
    kc.close()
    cq = closing_quote(cfg, con, {"venue": "kalshi", "ticker": t, "tipoff": TIP1})
    con.close()
    assert cq is not None and cq[3] == "kalshi_prices"  # T-5 beats the earlier T-60 row


# ---------------------------------------------------------------------------------- M7


def test_the_copy_fallback_leaves_nothing_behind(
    cfg: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = duckdb.connect
    calls = {"n": 0}

    def locked_first(path, *a, **k):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] <= 2 and "scratch" not in str(path):
            raise duckdb.IOException("Could not set lock")
        return real(path, *a, **k)

    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("jarvis_ro_*"))
    monkeypatch.setattr(sources.duckdb, "connect", locked_first)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    con = sources.open_ro(cfg.paths["kalshi_db"], retries=2, copy_ok=True)
    assert con.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0] > 0
    con.close()
    assert not list(sources.SCRATCH.glob("*.duckdb*"))  # unlinked as soon as it was open
    assert set(Path(tempfile.gettempdir()).glob("jarvis_ro_*")) == before  # no mkdtemp leak


def test_the_large_odds_store_is_never_copied(
    cfg: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_locked(path, *a, **k):  # type: ignore[no-untyped-def]
        raise duckdb.IOException("Could not set lock")

    games = load_games(cfg, SLATE)
    monkeypatch.setattr(sources.duckdb, "connect", always_locked)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    st = sources.load_pinnacle(cfg, games, SLATE, NOW)
    assert st.win == {} and st.prop == {}
    assert not list(sources.SCRATCH.glob("market_asof*"))
