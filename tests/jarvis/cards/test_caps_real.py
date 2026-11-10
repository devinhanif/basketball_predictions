"""The cap filter is driven by REAL ``single_candidate`` output for each cap, so rewording a note in
nba/jarvis/slate.py (which we deliberately did not touch) fails here instead of silently letting a
capped over onto a card. Also: the quote age shown on a card over."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from nba.jarvis.cards import embed as E
from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.models import Play
from nba.jarvis.runner import Runtime, build_state
from nba.jarvis.slate import single_candidate
from tests.jarvis.cards.test_review_fixes import KEY, _fresh_kalshi
from tests.jarvis.cards.test_run import Ids, at, make_rt, run
from tests.jarvis.conftest import NOW, SLATE


def lebron(rt: Runtime, now: datetime | None = None, model: bool = True) -> Play:
    if now is not None:
        at(rt, now)
    st = build_state(rt, SLATE)
    if not model:
        st.model = None
    q = next(x for x in st.quotes if x.ticker == KEY)
    p = single_candidate(rt.cfg, st, q, "yes")
    assert p is not None and p.edge_cents == p.edge_cents
    return p


def test_a_fresh_priced_over_is_not_capped(rt: Runtime) -> None:
    p = lebron(rt)
    assert not F.is_capped(p), p.notes


def test_no_p_plays_cap_is_recognised_from_real_output(rt: Runtime) -> None:
    p = lebron(rt, model=False)
    assert any("no P(plays)" in n for n in p.notes), p.notes  # the wording we match on
    assert F.is_capped(p)


def test_old_pinnacle_cap_is_recognised_from_real_output(rt: Runtime) -> None:
    later = NOW + timedelta(minutes=150)
    _fresh_kalshi(rt, later - timedelta(minutes=1))
    p = lebron(rt, later)
    assert any(n.startswith("Pinnacle line is") for n in p.notes), p.notes
    assert F.is_capped(p)


def test_pinnacle_newer_than_the_price_cap_is_recognised_from_real_output(rt: Runtime) -> None:
    con = duckdb.connect(str(rt.cfg.paths["kalshi_db"]))
    con.execute("DELETE FROM kalshi_prices WHERE ticker = ?", [KEY])
    con.execute(
        "INSERT INTO kalshi_prices VALUES (?,?,0.40,0.44,NULL,0,0,'live')",
        [KEY, NOW - timedelta(minutes=40)],  # Pinnacle (19:30) is 10 min newer than this price
    )
    con.close()
    p = lebron(rt)
    assert any("min newer than this price" in n for n in p.notes), p.notes
    assert F.is_capped(p)


# ------------------------------------------------------------------------------ quote age


def test_a_price_older_than_five_minutes_says_how_old_it_is(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(at(rt, NOW + timedelta(minutes=10)))  # the 19:58 quote is 12 minutes old
    post = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    assert "(priced 12 min ago)" in post["fields"][0]["value"]


def test_a_fresh_price_says_nothing_and_the_age_never_triggers_an_edit() -> None:
    from tests.jarvis.cards.test_embed_text import card, over

    young = over(quote_age_min=2.0)
    assert "priced" not in T.why_line(young, T.Style())
    a = E.build_embed(card(overs=(over(quote_age_min=7.0),)), "LAL", T.Style())
    b = E.build_embed(card(overs=(over(quote_age_min=19.0),)), "LAL", T.Style())
    assert "priced 7 min ago" in a["fields"][0]["value"]
    assert E.content_hash(a) == E.content_hash(b)  # ages tick every minute and are not news
    stale = T.why_line(over(quote_age_min=30.0, stale="price now stale"), T.Style())
    assert "priced" not in stale  # a frozen over shows its stale marker instead
