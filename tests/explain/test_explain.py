"""The explainer: sections, no external URLs, the miss glyphs, the T-60 rule, read-only."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pytest

from nba.explain import data as D
from nba.explain.__main__ import main
from nba.explain.knowledge import choose
from nba.explain.page import Page, build_page
from nba.explain.render import fan_svg, glyph, pct, render_page
from tests.explain.conftest import GAME, HOU, NAMES, P_BENCH, P_DNP, P_STAR, P_UNFORECAST, P_WING


def _page(replay: Path, odds: Path | None) -> Page:
    con = D.open_ro(replay)
    o = D.open_ro(odds) if odds else None
    page = build_page(con, o, GAME, source_name=replay.name)
    con.close()
    if o:
        o.close()
    assert page is not None
    return page


def _html(replay: Path, odds: Path | None) -> str:
    return render_page(_page(replay, odds))


def _text(html: str) -> str:
    t = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", t)


# ----------------------------------------------------------------------------- structure


def test_every_section_present_in_order(replay_db: Path, odds_db: Path) -> None:
    html = _html(replay_db, odds_db)
    ids = ["game", "out", "players", "scoring", "taught"]
    pos = [html.index(f'<section id="{i}">') for i in ids]
    assert pos == sorted(pos)
    for h in ("1. The game", "2. Who was out", "3. Players", "4. Scoring", "5. What the data"):
        assert h in html


def test_self_contained_no_external_urls(replay_db: Path, odds_db: Path) -> None:
    html = _html(replay_db, odds_db)
    assert not re.search(r"https?://", html)
    assert not re.search(r"\b(src|href)\s*=", html)
    assert "@import" not in html and "url(" not in html
    assert "<script" not in html and "<link" not in html and "<img" not in html
    assert 'name="viewport"' in html  # phone


def test_banned_words_never_appear(replay_db: Path, odds_db: Path) -> None:
    text = _text(_html(replay_db, odds_db)).lower()
    for word in ("lock", "pick", "bet", "bets", "betting", "wager", "stake", "sizing"):
        assert not re.search(rf"\b{word}\b", text), word


def test_first_screen_states_market_and_ours_and_who_was_closer(
    replay_db: Path, odds_db: Path
) -> None:
    text = " ".join(_text(_html(replay_db, odds_db)).split())
    # Pinnacle raw 0.60 / 0.44 -> de-vigged home 0.60 / 1.04 = 57.7%
    assert "The market priced HOU at 58%; we said 62%." in text
    # HOU won 110-102: we gave the winner 62%, Pinnacle 58% -> we were closer
    assert "We were closer on this one." in text
    assert "We favoured HOU" not in text and "We and the market both favoured HOU" in text


def test_market_missing_is_said_not_invented(
    make_replay: Callable[..., Path], tmp_path: Path
) -> None:
    from tests.explain.conftest import build_odds

    odds = tmp_path / "o2.duckdb"
    build_odds(odds, with_pinnacle_moneyline=False)
    text = " ".join(_text(_html(make_replay(), odds)).split())
    assert "No Pinnacle moneyline is stored for this game." in text
    assert "Pinnacle at T-60" not in text


# ---------------------------------------------------------------------- the T-60 rule


def test_late_rows_are_never_shown(replay_db: Path, odds_db: Path) -> None:
    page = _page(replay_db, odds_db)
    assert page.win is not None and abs(page.win.p_home - 0.62) < 1e-9  # not the 0.99 late row
    star = next(r for r in page.rows if r.player_id == P_STAR)
    assert abs(star.cells["pts"].median - 25.0) < 1e-9  # not the 5.0 late row
    assert page.late_only_rows == 2


def test_game_with_no_stored_forecast_says_so(
    make_replay: Callable[..., Path], odds_db: Path
) -> None:
    html = _html(make_replay(with_forecasts=False), odds_db)
    text = " ".join(_text(html).split())
    assert "No forecast for this game was stored before tip-off" in text
    for i in ("game", "out", "players", "scoring", "taught"):
        assert f'<section id="{i}">' in html
    assert "No player forecasts were stored" in text
    assert "No tip-off time is stored" in text  # report rule cannot be applied without a tip


# --------------------------------------------------------------------------- the report


def test_report_uses_latest_snapshot_at_or_before_tip_minus_60(
    replay_db: Path, odds_db: Path
) -> None:
    page = _page(replay_db, odds_db)
    assert page.report.as_of_et == datetime(2025, 11, 3, 17, 0)
    status = {ln.player_id: ln.status for ln in page.report.lines}
    # the 7:30 PM ET stamp ("out") is inside the 60 minutes before the 8 PM tip: ignored
    assert status[P_WING] == "questionable"
    assert status[P_DNP] == "out"
    text = " ".join(_text(render_page(page)).split())
    assert "ratings alone said 55% for HOU; after the report, 62%" in text


def test_report_older_than_36h_is_not_used(make_replay: Callable[..., Path]) -> None:
    p = make_replay()
    con = duckdb.connect(str(p))
    con.execute("DELETE FROM player_availability")
    con.execute(
        "INSERT INTO player_availability VALUES (?, '2025-11-01 17:00', ?, 'out', 'x', 's', NULL)",
        [P_DNP, GAME],
    )
    con.close()
    page = _page(p, None)
    assert page.report.as_of_et is None
    assert "never as nobody being out" in _text(render_page(page))


# ------------------------------------------------------------------ players and glyphs


def test_interval_status_and_glyphs() -> None:
    assert D.interval_status(10, 10, 20) == "inside"  # bounds are inclusive
    assert D.interval_status(20, 10, 20) == "inside"
    assert D.interval_status(21, 10, 20) == "above"
    assert D.interval_status(9, 10, 20) == "below"
    assert "▲ above" in glyph("above")
    assert "▼ below" in glyph("below")
    assert "● inside" in glyph("inside")
    assert "did not play" in glyph(None)
    # a miss is the same size and weight as a hit: one shared class, no extra emphasis
    assert glyph("above").count('class="g above"') == 1
    assert glyph("inside").count('class="g inside"') == 1


def test_players_sorted_by_projected_minutes_with_misses_marked(
    replay_db: Path, odds_db: Path
) -> None:
    page = _page(replay_db, odds_db)
    played = [r.player_id for r in page.rows]
    assert played == [P_STAR, P_WING, P_BENCH]  # 36, 30, 20 projected minutes
    assert [r.player_id for r in page.dnp_rows] == [P_DNP]
    assert page.rows[0].cells["pts"].status == "above"  # 41 > q90 = 40
    assert page.rows[1].cells["pts"].status == "inside"  # 14 in [7.7, 22.4]
    assert page.rows[2].cells["pts"].status == "below"  # 0 < q10 = 4.4
    assert page.pts_cover == (3, 1, 1, 1)
    assert page.best is not None and page.best.name == NAMES[P_WING]
    # bench: |0 - 8| / 4.2 = 1.9 half-widths; star: |41 - 25| / 13.1 = 1.2
    assert page.worst is not None and page.worst.name == NAMES[P_BENCH]
    html = render_page(page)
    assert html.count("▲ above") >= 1 and html.count("▼ below") >= 1
    # played without a stored forecast: said so (no static or book name for a fake id)
    assert f"Player {P_UNFORECAST}" in _text(html)


def test_details_toggle_holds_the_other_three_stats(replay_db: Path, odds_db: Path) -> None:
    html = _html(replay_db, odds_db)
    assert html.count("<details>") == 3  # one per player who played; DNP rows are compact
    for label in ("Rebounds:", "Assists:", "Threes made:"):
        assert label in html


def test_line_preference_pinnacle_then_draftkings_then_consensus(
    replay_db: Path, odds_db: Path
) -> None:
    page = _page(replay_db, odds_db)
    star = page.rows[0].cells["pts"].line
    wing = page.rows[1].cells["pts"].line
    bench = page.rows[2].cells["pts"].line
    assert star is not None and star.book == "pinnacle" and star.line == 24.5  # not 29.5, not t5
    assert star.p_over == pytest.approx(0.5)
    assert wing is not None and wing.book == "draftkings" and wing.line == 13.5
    assert bench is not None and bench.book == "consensus of 3 books" and bench.line == 8.5
    assert bench.p_over == pytest.approx(0.5 / 1.04, rel=1e-6)


def test_p_over_is_on_integer_support() -> None:
    surv = np.array([0.9, 0.6, 0.3, 0.1, 0.0005])  # P(Y>=1..5)
    assert D.p_over(surv, 2.5) == pytest.approx(0.3)  # P(Y>=3)
    assert D.p_over(surv, 3.0) == pytest.approx(0.1)  # integer line: P(Y>=4), push excluded
    assert D.p_over(surv, 0.5) == pytest.approx(0.9)
    assert D.p_over(surv, 40.5) == 0.0  # beyond a complete stored tail
    assert D.p_over(None, 2.5) is None
    assert D.p_over(np.array([0.9, 0.5]), 7.5) is None  # incomplete tail: refuse to guess


def test_crps_matches_the_pipeline_scorer() -> None:
    from nba.props import full_support as fs

    surv = np.array([0.95, 0.8, 0.55, 0.3, 0.12, 0.04, 0.0008])
    for y in (0, 1, 3, 6, 9):
        assert D.crps_int(surv, y) == pytest.approx(fs.crps_int_surv(surv, y))
        assert D.pit_interval(surv, y) == pytest.approx(fs.pit_bounds(surv, y))


# ------------------------------------------------------------------------------ scoring


def test_scoring_has_n_and_ci_per_stat(replay_db: Path, odds_db: Path) -> None:
    page = _page(replay_db, odds_db)
    assert {s.stat for s in page.scoring} == {"pts", "reb", "ast", "fg3m"}
    for s in page.scoring:
        assert s.n == 3  # three forecast players played
        assert s.lo <= s.diff <= s.hi
    text = " ".join(_text(render_page(page)).split())
    assert "The score is CRPS on whole numbers" in text
    assert "Ours minus avg [95% CI]" in text


# ------------------------------------------------------------------- footer rule choice


def test_footer_rules() -> None:
    blow = choose(
        margin=31, forecast_players=set(), bias={}, starter_minutes={"HOU": 24.0, "DAL": 21.0}
    )
    assert blow.rule == "blowout" and "45.4%" in blow.sentence and "n = 40,308" in blow.sentence
    edge = choose(margin=25, forecast_players=set(), bias={})
    assert edge.rule == "blowout"
    just_under = choose(margin=24, forecast_players=set(), bias={})
    assert just_under.rule == "default"
    b = D.PlayerBias(1629029, 31, 2.4, 0.9, 3.9)
    star = choose(margin=5, forecast_players={1629029}, bias={1629029: b})
    assert star.rule == "star" and "n = 31" in star.sentence and "+0.9 to +3.9" in star.sentence
    assert "excludes zero" in star.sentence
    spans = choose(
        margin=5,
        forecast_players={2544},
        bias={2544: D.PlayerBias(2544, 40, 0.5, -0.4, 1.4)},
    )
    assert "spans zero" in spans.sentence
    # blowout outranks star
    assert choose(margin=30, forecast_players={2544}, bias={}).rule == "blowout"


def test_footer_blowout_on_the_page(make_replay: Callable[..., Path], odds_db: Path) -> None:
    text = " ".join(_text(_html(make_replay(home_pts=130, away_pts=100), odds_db)).split())
    assert "30-point margin" in text and 'rule "blowout"' in text


# ----------------------------------------------------------------------------- read-only


def test_open_ro_refuses_writes(replay_db: Path) -> None:
    con = D.open_ro(replay_db)
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE nope(x INT)")
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM games")
    con.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_cli_writes_one_file_and_changes_no_database(
    replay_db: Path, odds_db: Path, tmp_path: Path
) -> None:
    before = (_sha(replay_db), _sha(odds_db))
    out = tmp_path / "sub" / "g.html"
    rc = main(
        ["game", "--game-id", GAME, "--db", str(replay_db), "--odds-db", str(odds_db),
         "--out", str(out)]
    )  # fmt: skip
    assert rc == 0 and out.exists()
    assert (_sha(replay_db), _sha(odds_db)) == before
    assert not list(tmp_path.glob("*.wal"))
    assert out.read_text().startswith("<!DOCTYPE html>")


def test_cli_unknown_game_exits_2(replay_db: Path, tmp_path: Path) -> None:
    rc = main(["game", "--game-id", "nope", "--db", str(replay_db), "--out", str(tmp_path / "x")])
    assert rc == 2


def test_runs_without_an_odds_db(replay_db: Path) -> None:
    text = " ".join(_text(_html(replay_db, None)).split())
    assert "No Pinnacle moneyline is stored for this game." in text
    assert "No market line stored for this stat." in text


def test_pct_never_claims_certainty() -> None:
    assert pct(0.004) == "<1%" and pct(0.996) == ">99%" and pct(0.0) == "0%" and pct(None) == "n/a"
    assert pct(0.585) in ("58%", "59%")


def test_fan_svg_marker_shape_follows_status(replay_db: Path, odds_db: Path) -> None:
    page = _page(replay_db, odds_db)
    above, inside, below = (page.rows[i].cells["pts"] for i in range(3))
    assert "<circle" in fan_svg(inside) and "<polygon" not in fan_svg(inside)
    assert "<polygon" in fan_svg(above) and "<polygon" in fan_svg(below)
    assert fan_svg(above) != fan_svg(below)  # different orientation, not just a color
    assert HOU  # keep the fixture constant imported for readers


def test_golden_page_for_the_synthetic_game(replay_db: Path, odds_db: Path) -> None:
    """Byte-for-byte golden file. Regenerate on purpose with ``UPDATE_GOLDEN=1 pytest``."""
    import os

    golden = Path(__file__).parent / "golden" / "synthetic_game.html"
    html = _html(replay_db, odds_db)
    if os.environ.get("UPDATE_GOLDEN") == "1" or not golden.exists():
        golden.write_text(html, encoding="utf-8")
    assert html == golden.read_text(encoding="utf-8")
