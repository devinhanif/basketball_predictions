# ruff: noqa: E501
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.jarvis.book import (
    PaperBook,
    cluster_ci,
    render_weekly,
    score_book,
    sha,
    weekly_summary,
)
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Play, PlayLeg
from nba.jarvis.render import scored_line
from tests.jarvis.conftest import G1, LAL, NOW, SLATE, TIP1


def play(
    ticker: str = "KXNBAGAME-26OCT28LALLAC-LAL",
    side: str = "yes",
    price: float = 0.50,
    label: str = "thin",
    edge: float = 1.0,
    contracts: int = 4,
    team: int = LAL,
    venue: str = "kalshi",
) -> Play:
    leg = PlayLeg(venue, ticker, side, price, "game_win", "LAL to win", team_id=team)
    cost = contracts * price + 0.04
    return Play(
        G1, "LAL@LAC", TIP1, (leg,), label, edge, 0.55, 1.75, price, contracts, cost, "r",
        inputs={"leg_costs": [cost], "q": [1]},
    )  # fmt: skip


def test_a_play_is_recorded_once_per_slate_date(tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "b.duckdb")
    p = play()
    assert b.record([p], source="slate", slate=SLATE, now=NOW, delivered="failed") == [p]
    # a re-emission, however much later, is not a new bet and not a new row
    assert b.record([p], source="alert", slate=SLATE, now=NOW + timedelta(minutes=31)) == []
    assert b.record([p], source="alert", slate=SLATE, now=NOW + timedelta(hours=3)) == []
    con = b._con()
    n, dl = con.execute("SELECT count(*), max(delivered) FROM plays").fetchone()
    con.close()
    assert (n, dl) == (1, "sent")  # the failed first delivery was updated when a later post landed
    assert b.spent_tonight(SLATE) == pytest.approx(p.stake_usd)
    assert b.first_stake(p.key, SLATE) == (4, pytest.approx(p.stake_usd))
    # the next slate date is a new bet
    assert b.record([p], source="slate", slate=SLATE + timedelta(days=1), now=NOW) == [p]


def test_book_is_its_own_file_and_has_the_documented_tables(tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "jv" / "b.duckdb")
    b.ensure()
    con = duckdb.connect(str(tmp_path / "jv" / "b.duckdb"), read_only=True)
    names = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    assert {"plays", "scores", "quote_log", "sends"} <= names


def test_send_log_carries_hashes_not_text_or_recipient(tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "b.duckdb")
    b.record_send(
        kind="alert",
        mode="sent",
        slate=SLATE,
        keys=["k1"],
        play_ids=["p"],
        text="body text",
        inputs={"a": 1},
        now=NOW,
    )
    con = b._con()
    row = con.execute("SELECT text_hash, input_hash, chars, mode FROM sends").fetchone()
    con.close()
    assert row == (sha("body text"), sha({"a": 1}), 9, "sent")
    assert b.sent_recently("k1", 30, NOW + timedelta(minutes=5))
    assert not b.sent_recently("k1", 30, NOW + timedelta(minutes=31))


def seed_close(b: PaperBook, ticker: str, bid: float, ask: float, ts: datetime) -> None:
    con = b._con()
    con.execute("INSERT INTO quote_log VALUES ('kalshi',?,?,?,?)", [ticker, ts, bid, ask])
    con.close()


def finish_game(cfg: JarvisConfig, home_pts: int, away_pts: int) -> None:
    con = duckdb.connect(str(cfg.paths["nba_db"]))
    con.execute(
        "UPDATE games SET home_pts = ?, away_pts = ? WHERE game_id = ?", [home_pts, away_pts, G1]
    )
    con.close()


def test_score_outcome_pnl_and_clv(cfg: JarvisConfig, tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "b.duckdb")
    win, lose = play(), play(ticker="KXNBAGAME-26OCT28LALLAC-LAC", team=1610612746, price=0.40)
    b.record([win, lose], source="slate", slate=SLATE, now=NOW)
    seed_close(b, win.legs[0].ticker, 0.55, 0.57, TIP1 - timedelta(minutes=2))  # mid 0.56
    seed_close(b, lose.legs[0].ticker, 0.40, 0.44, TIP1 - timedelta(minutes=2))  # mid 0.42
    seed_close(b, win.legs[0].ticker, 0.01, 0.99, TIP1 + timedelta(minutes=5))  # after tip: ignored
    assert score_book(cfg, b, now=TIP1 + timedelta(hours=3))["waiting"] == 2  # no final yet
    finish_game(cfg, home_pts=100, away_pts=110)  # LAL (away) wins
    counts = score_book(cfg, b, now=TIP1 + timedelta(hours=3))
    assert counts == {"scored": 2, "void": 0, "waiting": 0}
    con = b._con()
    rows = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT p.ticker, s.won, s.pnl_usd, s.clv_cents, s.clv_vs_ask_cents, s.close_source "
            "FROM plays p JOIN scores s USING (play_id)"
        ).fetchall()
    }
    con.close()
    w = rows[win.legs[0].ticker]
    assert w[0] is True and w[1] == pytest.approx(4 - win.stake_usd)
    assert w[2] == pytest.approx((0.56 - 0.50) * 100) and w[3] == pytest.approx(7.0)
    assert w[4] == "quote_log"
    lo = rows[lose.legs[0].ticker]
    assert lo[0] is False and lo[1] == pytest.approx(-lose.stake_usd)
    assert lo[2] == pytest.approx(2.0)  # 0.42 close vs 0.40 entry
    assert score_book(cfg, b, now=TIP1 + timedelta(hours=4)) == {
        "scored": 0,
        "void": 0,
        "waiting": 0,
    }


def test_no_side_clv_uses_the_complement(cfg: JarvisConfig, tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "b.duckdb")
    p = play(side="no", price=0.45)
    b.record([p], source="slate", slate=SLATE, now=NOW)
    seed_close(
        b, p.legs[0].ticker, 0.50, 0.54, TIP1 - timedelta(minutes=1)
    )  # yes mid .52 -> no .48
    finish_game(cfg, 100, 110)
    score_book(cfg, b, now=TIP1 + timedelta(hours=3))
    con = b._con()
    clv, won = con.execute("SELECT s.clv_cents, s.won FROM scores s").fetchone()
    con.close()
    assert clv == pytest.approx(3.0) and won is False  # LAL won, so NO lost


def test_kalshi_result_is_the_settlement_truth(cfg: JarvisConfig, tmp_path: Path) -> None:
    con = duckdb.connect(str(cfg.paths["kalshi_db"]))
    con.execute(
        "UPDATE kalshi_markets SET result = 'yes' WHERE ticker = 'KXNBAGAME-26OCT28LALLAC-LAC'"
    )
    con.close()
    b = PaperBook(tmp_path / "b.duckdb")
    p = play(ticker="KXNBAGAME-26OCT28LALLAC-LAC", team=1610612746)
    b.record([p], source="slate", slate=SLATE, now=NOW)
    score_book(cfg, b, now=TIP1 + timedelta(hours=3))
    con = b._con()
    assert con.execute("SELECT won FROM scores").fetchone() == (True,)
    con.close()


def test_cluster_ci_needs_two_dates_and_is_deterministic() -> None:
    d1, d2, d3 = date(2026, 10, 25), date(2026, 10, 26), date(2026, 10, 27)
    assert cluster_ci([(d1, 1.0), (d1, 2.0)]) is None
    vals = [(d1, 1.0), (d1, 2.0), (d2, -1.0), (d3, 3.0), (d3, 0.0)]
    a, b = cluster_ci(vals), cluster_ci(vals)
    assert a == b and a is not None
    mean, lo, hi = a
    assert mean == pytest.approx(1.0) and lo <= mean <= hi


def test_weekly_summary_reports_insufficient_sample(cfg: JarvisConfig, tmp_path: Path) -> None:
    b = PaperBook(tmp_path / "b.duckdb")
    for i in range(3):
        p = play(ticker=f"T{i}", label="thin" if i < 2 else "none")
        b.record([p], source="slate", slate=SLATE, now=NOW + timedelta(minutes=40 * i))
        con = b._con()
        con.execute(
            "INSERT INTO scores SELECT play_id, ?, 'settled', ?, ?, NULL, NULL, NULL, '', ?, NULL "
            "FROM plays WHERE ticker = ?",
            [NOW, i == 0, 1.0 if i == 0 else -2.0, 0.5 * i, f"T{i}"],
        )
        con.close()
    s = weekly_summary(cfg, b, until=SLATE)
    assert (
        s["labels"]["thin"]["n"] == 2
        and s["labels"]["none"]["n"] == 1
        and s["labels"]["all"]["n"] == 3
    )
    assert s["labels"]["all"]["claim_ok"] is False  # n=3 < the configured minimum
    assert s["labels"]["all"]["clv_ci95_cents"] is None  # one date cluster: no CI claimed
    txt = render_weekly(s)
    assert "insufficient sample" in txt and "n/a (<2 dates)" in txt
    assert s["labels"]["arbitrage"]["n"] == 0


def test_a_hit_is_annotated_and_a_miss_is_not(cfg: JarvisConfig) -> None:
    base = {"slate_date": "2026-10-28", "matchup": "LAL@LAC", "venue": "kalshi", "side": "yes",
            "leg_desc": "LAL to win", "price": 0.5, "edge_label": "none", "edge_cents": -3.0}  # fmt: skip
    hit = scored_line({**base, "won": True, "pnl_usd": 2.0})
    miss = scored_line({**base, "won": False, "pnl_usd": -2.0})
    assert "this one hit; it was still a -6% bet" in hit
    assert "this one hit" not in miss
    # same layout for both: identical text up to the pnl field
    assert hit.split(" pnl ")[0] == miss.split(" pnl ")[0]
