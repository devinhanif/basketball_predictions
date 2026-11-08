"""The stale-row gap (2026-11-02) at JARVIS: a player ruled OUT on a report known at serve time is
not offered, even though his forward row (written before the report) still says he plays."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from nba.jarvis import slate as slate_mod
from nba.jarvis.config import JarvisConfig
from nba.jarvis.forecasts import ModelView
from nba.jarvis.models import Quote
from nba.jarvis.slate import gather
from nba.parlay.out_gate import OutNotice
from tests.jarvis.conftest import G1, LEBRON, NOW, SLATE


def _avail(root: Path, rows: list[tuple[int, datetime, str]]) -> None:
    con = duckdb.connect(str(root / "nba.duckdb"))
    for pid, at, status in rows:
        con.execute(
            "INSERT INTO player_availability VALUES (?,?,?,?,'x','nba_official_report',?)",
            [pid, at, G1, status, at],
        )
    con.close()


def _props(st: slate_mod.SlateState) -> list[Quote]:
    return [q for q in st.quotes if q.kind == "prop"]


def test_control_without_a_report_the_prop_is_offered(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert any(q.player_id == LEBRON for q in _props(st))


def test_out_after_the_morning_row_removes_the_prop_but_not_the_game_markets(
    cfg: JarvisConfig, root: Path
) -> None:
    _avail(root, [(LEBRON, NOW - timedelta(hours=2), "out")])
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert not any(q.player_id == LEBRON for q in st.quotes)
    assert any(q.kind == "game_win" for q in st.quotes)
    assert any("ruled OUT" in n for n in st.notes)


def test_a_report_not_yet_known_at_serve_time_does_not_remove_it(
    cfg: JarvisConfig, root: Path
) -> None:
    _avail(root, [(LEBRON, NOW + timedelta(minutes=30), "out")])
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert any(q.player_id == LEBRON for q in _props(st))


@pytest.mark.parametrize("later", ["probable", "available"])
def test_out_then_upgraded_is_offered_again(cfg: JarvisConfig, root: Path, later: str) -> None:
    _avail(
        root,
        [(LEBRON, NOW - timedelta(hours=3), "out"), (LEBRON, NOW - timedelta(hours=1), later)],
    )
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert any(q.player_id == LEBRON for q in _props(st))


def test_doubtful_is_not_treated_as_out(cfg: JarvisConfig, root: Path) -> None:
    _avail(root, [(LEBRON, NOW - timedelta(hours=2), "doubtful")])
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert any(q.player_id == LEBRON for q in _props(st))


def test_an_unreadable_report_store_is_said_loudly(
    cfg: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(slate_mod, "load_out_players", lambda *_a, **_k: (set(), "IOException: x"))
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert any("OUT-player filter is OFF" in n for n in st.notes)


def test_model_view_does_not_price_an_out_player_from_the_stale_row(cfg: JarvisConfig) -> None:
    mv = ModelView(cfg, SLATE)
    ctx = SimpleNamespace(p_play={LEBRON: 0.91}, team_of={LEBRON: 1})
    mv._sl = SimpleNamespace(
        info=SimpleNamespace(ctxs={G1: ctx}, out_on_report={(G1, LEBRON): OutNotice(NOW, None)})
    )
    q = Quote("kalshi", "T", G1, "prop", NOW, 0.4, 0.44, player_id=LEBRON, stat="pts", threshold=25)
    assert mv.p_yes(q) is None
