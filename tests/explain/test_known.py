"""The "What was known, and when" section: renders from a tiny fact store, counts post-tip
facts, and degrades to one sentence (never fails the page)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from nba.explain import data as D
from nba.explain.__main__ import main
from nba.explain.page import build_page
from nba.explain.render import render_page
from tests.explain.conftest import GAME, HOU, P_STAR, P_WING, TIP_UTC

SCHEMA = Path(__file__).parents[2] / "nba" / "facts" / "schema.sql"
G = f"game:{GAME}"


def _t(h: int, m: int, day: int = 4) -> dt.datetime:
    return dt.datetime(2025, 11, day, h, m)


@pytest.fixture
def facts_db(tmp_path: Path) -> Path:
    p = tmp_path / "facts.duckdb"
    con = duckdb.connect(str(p))
    con.execute(SCHEMA.read_text())
    rows = [
        # (subject, predicate, object, value, value_text, known_at, valid_from)
        (G, "tips_at", None, None, "tip", _t(0, 0, 3), TIP_UTC),
        (G, "home_team", f"team:{HOU}", None, None, _t(0, 0, 3), None),
        (f"player:{P_STAR}", "listed_status", G, None, "Questionable", _t(22, 50, 3), None),
        (f"player:{P_WING}", "listed_status", G, None, "OUT", _t(0, 10), None),  # T-60..T-30
        (f"player:{P_STAR}", "announced_starter", G, 1.0, "Expected", _t(0, 20), None),
        (f"player:{P_STAR}", "listed_status", G, None, "Available", _t(1, 30), None),  # post-tip
        (f"player:{P_STAR}", "played_minutes", G, 30.0, None, _t(3, 30), None),  # post-tip
    ]
    for i, (s, pr, o, v, vt, ka, vf) in enumerate(rows, 1):
        con.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, value, value_text, "
            "known_at, valid_from, source, confidence) VALUES (?,?,?,?,?,?,?,?,'test',1.0)",
            [i, s, pr, o, v, vt, ka, vf],
        )
    con.close()
    return p


def _html(replay: Path, facts: Path | None) -> str:
    con = D.open_ro(replay)
    page = build_page(con, None, GAME, facts_db=facts, with_known=True)
    con.close()
    assert page is not None
    return render_page(page)


def _section(html: str) -> str:
    a = html.index('<section id="known">')
    return html[a : html.index("</section>", a)]


def test_section_renders_timeline(replay_db: Path, facts_db: Path) -> None:
    s = _section(_html(replay_db, facts_db))
    assert "What was known, and when" in s
    assert "Tip-off:" in s and "CST" in s and "US Central" in s  # 7:00 PM CST Nov 3
    assert "60 minutes before tip" in s and "30 minutes before tip" in s
    assert "Questionable" in s and "report published" in s
    assert "Announced starters, snapshot taken" in s and "(expected)" in s
    # the OUT row appears only after the T-60 cutoff, the post-tip "Available" row never
    t60, t30 = s.split("30 minutes before tip")
    assert "OUT" not in t60 and "OUT" in t30
    assert "Available" not in s


def test_post_tip_exclusion_count(replay_db: Path, facts_db: Path) -> None:
    s = _section(_html(replay_db, facts_db))
    assert "Facts known after tip are not shown: 2." in s


@pytest.mark.parametrize("kind", ["missing", "unknown_game"])
def test_no_facts_is_one_sentence_and_page_survives(
    replay_db: Path, tmp_path: Path, kind: str
) -> None:
    if kind == "missing":
        facts: Path = tmp_path / "nope.duckdb"
    else:
        facts = tmp_path / "empty.duckdb"
        con = duckdb.connect(str(facts))
        con.execute(SCHEMA.read_text())
        con.close()
    html = _html(replay_db, facts)
    s = _section(html)
    assert s.count("<p>") == 1 and "no timeline" in s
    assert '<section id="taught">' in html


def test_page_without_the_option_has_no_section(replay_db: Path) -> None:
    con = D.open_ro(replay_db)
    page = build_page(con, None, GAME)
    con.close()
    assert page is not None and 'id="known"' not in render_page(page)


def test_cli_facts_db_option(replay_db: Path, facts_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "g.html"
    rc = main(
        ["game", "--game-id", GAME, "--db", str(replay_db), "--facts-db", str(facts_db),
         "--out", str(out)]
    )  # fmt: skip
    assert rc == 0 and "Facts known after tip" in out.read_text()
