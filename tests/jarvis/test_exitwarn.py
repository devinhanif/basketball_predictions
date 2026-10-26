"""DON'T SELL warning: thresholds from config, numguard-clean text, halftime wiring, read-only."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.jarvis import cli
from nba.jarvis.config import JarvisConfig
from nba.jarvis.exitwarn import allowed_numbers, exit_warning, venue_fair
from nba.jarvis.explain import prepare
from nba.jarvis.runner import Runtime, run_slate, run_tick
from nba.parlay.assistant.numguard import check_prose
from tests.jarvis.conftest import SLATE, TIP1, Sent
from tests.jarvis.test_features import live_files


def test_the_devin_case_bid_zero_fair_meaningful_warns(cfg: JarvisConfig) -> None:
    w = exit_warning(cfg, 0.0, 0.31, 10)
    assert w is not None
    assert "DON'T SELL: bid 0c vs fair 31c" in w.text and "~$3.10" in w.text
    w2 = exit_warning(cfg, None, 0.31, 10)  # no bid posted at all
    assert w2 is not None and w2.text.startswith("DON'T SELL: no bid vs fair 31c")


def test_bid_4c_vs_fair_31c(cfg: JarvisConfig) -> None:
    w = exit_warning(cfg, 0.04, 0.31, 10)
    assert w is not None and "bid 4c vs fair 31c" in w.text and "~$2.70" in w.text


def test_threshold_edges_come_from_config(cfg: JarvisConfig) -> None:
    f = float(cfg.exit_warning["bid_fraction"])
    assert exit_warning(cfg, 0.30 * f - 0.001, 0.30) is not None  # just under f x fair
    assert exit_warning(cfg, 0.30 * f, 0.30) is None  # at the threshold: not far below
    assert exit_warning(cfg, 0.28, 0.30) is None  # ordinary spread
    assert exit_warning(cfg, 0.0, 0.03) is None  # fair below min_fair: not meaningful
    assert exit_warning(cfg, 0.0, 0.40, 0.2) is None  # gives away < min_giveaway_usd
    assert exit_warning(cfg, 0.0, None) is None
    off = {**cfg.exit_warning, "enabled": False}
    assert exit_warning(replace(cfg, exit_warning=off), 0.0, 0.31, 10) is None


def test_text_passes_numguard_and_never_suggests_an_order(cfg: JarvisConfig) -> None:
    for bid, fair, n in [(0.04, 0.31, 10), (None, 0.46, 7), (0.0, 0.155, 3), (0.012, 0.333, 25)]:
        w = exit_warning(cfg, bid, fair, n)
        assert w is not None
        text = prepare(w.text)  # the same cents normalisation the explanation path applies
        out, removed = check_prose(text, allowed_numbers(w))
        assert removed == 0 and out == text
        assert not re.search(r"\b(place|buy|order|bet \$)", w.text, re.I)


def test_venue_fair() -> None:
    fair, src = venue_fair(0.40, 0.44) or (0.0, "")
    assert fair == pytest.approx(0.42) and src == "venue mid"
    assert venue_fair(None, 0.31) == (0.31, "ask, no bid posted")
    assert venue_fair(0.4, None) is None


def _set_price(cfg: JarvisConfig, bid: float | None, ask: float | None, hours: float) -> None:
    con = duckdb.connect(str(cfg.paths["kalshi_db"]))
    ts = datetime(2026, 10, 28, 19, 58) + timedelta(hours=hours)
    for (t,) in con.execute("SELECT ticker FROM kalshi_markets").fetchall():
        con.execute("INSERT INTO kalshi_prices VALUES (?,?,?,?,NULL,0,0,'live')", [t, ts, bid, ask])
    con.close()


def test_halftime_check_in_warns_when_bid_collapsed(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    run_slate(rt, SLATE)
    _set_price(cfg, 0.01, 0.60, hours=4.0)  # newest row: both sides of every market
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: TIP1 + timedelta(minutes=70)
    run_tick(rt, SLATE)
    txt = [t for t in sent.texts if "halftime" in t][0]
    assert "DON'T SELL" in txt and "gives away" in txt


def test_halftime_check_in_quiet_when_bid_is_near_fair(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    run_slate(rt, SLATE)
    _set_price(cfg, 0.56, 0.60, hours=4.0)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: TIP1 + timedelta(minutes=70)
    run_tick(rt, SLATE)
    txt = [t for t in sent.texts if "halftime" in t][0]
    assert "DON'T SELL" not in txt


def test_cli_exit_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["exit-check", "--bid", "4", "--fair", "31", "--contracts", "10"]) == 0
    assert "DON'T SELL: bid 4c vs fair 31c" in capsys.readouterr().out
    assert cli.main(["exit-check", "--bid", "30", "--fair", "31"]) == 0
    assert "no warning" in capsys.readouterr().out


def test_module_is_read_only_no_order_or_credential_code() -> None:
    src = (Path(cli.__file__).parent / "exitwarn.py").read_text().lower()
    for banned in ("requests", "httpx", "urllib", ".post(", "api_key", "getenv", "environ"):
        assert banned not in src
