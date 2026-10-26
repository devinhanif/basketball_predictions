"""DON'T SELL warning: motivating case, book-quality gates, age, combos, fail-soft, read-only."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis import cli, posts
from nba.jarvis.config import JarvisConfig
from nba.jarvis.exitwarn import (
    allowed_numbers,
    exit_check,
    exit_warning,
    venue_fair,
)
from nba.jarvis.posts import leg_exit_line
from nba.jarvis.runner import Runtime, run_slate, run_tick
from nba.parlay.assistant.numguard import check_prose
from tests.jarvis.conftest import G1, SLATE, TIP1, Sent
from tests.jarvis.test_features import live_files

CLOCK = TIP1 + timedelta(minutes=70)


# ---------------------------------------------------------------- the rule itself


def test_motivating_case_all_legs_hit_bid_zero_fair_100c(cfg: JarvisConfig) -> None:
    w = exit_warning(cfg, 0.0, 1.0, 10)
    assert w is not None
    assert "DON'T SELL AT THIS BID: no bid vs fair 100c" in w.text and "~$10.00" in w.text


def test_fair_above_100c_is_clamped_and_nonpositive_rejected(cfg: JarvisConfig) -> None:
    w = exit_warning(cfg, 0.0, 1.5, 10)
    assert w is not None and w.fair == 1.0
    assert exit_check(cfg, 0.0, 0.0, 10).reason == "fair_out_of_range"
    assert exit_check(cfg, 0.0, None, 10).reason == "no_fair"


def test_bid_4c_vs_fair_31c(cfg: JarvisConfig) -> None:
    w = exit_warning(cfg, 0.04, 0.31, 10)
    assert w is not None and "bid 4c vs fair 31c" in w.text and "~$2.70" in w.text


def test_reasons_are_always_given(cfg: JarvisConfig) -> None:
    f = float(cfg.exit_warning["bid_fraction"])
    assert exit_check(cfg, 0.30 * f - 0.001, 0.30).reason == "warn"
    assert exit_check(cfg, 0.30 * f, 0.30).reason == "bid_near_fair"
    assert exit_check(cfg, 0.0, 0.03).reason == "below_min_fair"
    assert exit_check(cfg, 0.0, 0.40, 0.2).reason == "giveaway_small"
    assert exit_check(cfg, 0.0, 0.40, 10, age_min=500).reason == "stale"
    off = replace(cfg, exit_warning={"enabled": False})
    assert exit_check(off, 0.0, 0.31, 10).reason == "disabled"
    assert "No warning:" in exit_check(cfg, 0.28, 0.30).text


def test_text_passes_numguard_raw_and_never_suggests_an_order(cfg: JarvisConfig) -> None:
    for bid, fair, n, age in [
        (0.04, 0.31, 10, 3.0),
        (None, 0.46, 7, 12.4),
        (0.0, 0.155, 3, None),
        (0.012, 0.333, 25, 19.0),
    ]:
        w = exit_warning(cfg, bid, fair, n, age_min=age)
        assert w is not None
        out, removed = check_prose(w.text, allowed_numbers(w))  # raw text, no normalisation
        assert removed == 0 and out == w.text
        assert not re.search(r"\b(place|buy|order|bet \$)", w.text, re.I)


def test_venue_fair_gates() -> None:
    assert venue_fair(0.40, 0.44, 0.25) == pytest.approx(0.42)
    assert venue_fair(0.01, 0.99, 0.25) is None  # 1c/99c placeholder: wide
    assert venue_fair(0.0, 0.31, 0.25) is None  # empty bid
    assert venue_fair(0.40, 1.0, 0.25) is None  # empty ask
    assert venue_fair(0.10, 0.80, 0.25) is None  # wide


# ---------------------------------------------------------------- one leg from a captured book


def _row(side: str = "yes", n: int = 10) -> dict[str, Any]:
    return {
        "venue": "kalshi", "ticker": "T-1", "side": side, "contracts": n, "leg_desc": "X 5+ reb",
    }  # fmt: skip


def _line(cfg: JarvisConfig, bid: float, ask: float, side: str = "yes", age: float = 3.0) -> Any:
    ts = CLOCK - timedelta(minutes=age)
    return leg_exit_line(cfg, _row(side), {"T-1": (bid, ask, ts)}, CLOCK)


def test_yes_leg_collapsed_bid_warns_with_age(cfg: JarvisConfig) -> None:
    ln = _line(cfg, 0.05, 0.30)
    assert ln and "bid 5c vs fair 18c" in ln and "snapshot 3 min old" in ln


def test_no_leg_flip(cfg: JarvisConfig) -> None:
    # YES 0.70/0.95 -> NO bid 0.05, ask 0.30: same warning as the YES case above
    ln = _line(cfg, 0.70, 0.95, side="no")
    assert ln and "bid 5c vs fair 18c" in ln
    assert _line(cfg, 0.70, 0.95, side="yes") is None  # YES side is healthy


def test_empty_bid_says_no_bid_posted_without_a_fair(cfg: JarvisConfig) -> None:
    ln = _line(cfg, 0.0, 0.31)
    assert ln and "NO BID POSTED" in ln and " vs fair" not in ln and "would get $0" in ln
    ln_no = _line(cfg, 0.69, 1.0, side="no")  # NO bid = 1 - YES ask (empty) -> no bid
    assert ln_no and "NO BID POSTED" in ln_no


def test_placeholder_and_empty_books_print_no_fair(cfg: JarvisConfig) -> None:
    assert _line(cfg, 0.01, 0.99) is None
    assert _line(cfg, 0.0, 1.0) is None
    assert _line(cfg, 0.10, 0.80) is None


def test_stale_row_is_suppressed(cfg: JarvisConfig) -> None:
    assert _line(cfg, 0.05, 0.30, age=3.0) is not None
    assert _line(cfg, 0.05, 0.30, age=float(cfg.exit_warning["max_age_min"]) + 5) is None


# ---------------------------------------------------------------- halftime wiring


def _set_price(cfg: JarvisConfig, bid: float, ask: float, age_min: float = 3.0) -> None:
    con = duckdb.connect(str(cfg.paths["kalshi_db"]))
    ts = CLOCK - timedelta(minutes=age_min)
    for (t,) in con.execute("SELECT ticker FROM kalshi_markets").fetchall():
        con.execute("INSERT INTO kalshi_prices VALUES (?,?,?,?,NULL,0,0,'live')", [t, ts, bid, ask])
    con.close()


def _halftime_text(cfg: JarvisConfig, rt: Runtime, sent: Sent, bid: float, ask: float,
                   age_min: float = 3.0) -> str:  # fmt: skip
    run_slate(rt, SLATE)
    side = posts.todays_rows(rt.book, SLATE, G1)[0]["side"]
    yb, ya = (bid, ask) if side == "yes" else (1.0 - ask, 1.0 - bid)  # held side gets bid/ask
    _set_price(cfg, yb, ya, age_min)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: CLOCK
    run_tick(rt, SLATE)
    return [t for t in sent.texts if "halftime" in t][0]


def test_halftime_warns_when_bid_collapsed(cfg: JarvisConfig, rt: Runtime, sent: Sent) -> None:
    txt = _halftime_text(cfg, rt, sent, 0.05, 0.30)
    assert "DON'T SELL AT THIS BID" in txt and "snapshot 3 min old" in txt


def test_halftime_quiet_when_bid_near_fair_stale_or_wide(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    assert "DON'T SELL" not in _halftime_text(cfg, rt, sent, 0.56, 0.60)


def test_halftime_quiet_when_stale(cfg: JarvisConfig, rt: Runtime, sent: Sent) -> None:
    assert "DON'T SELL" not in _halftime_text(cfg, rt, sent, 0.05, 0.30, age_min=90)


def test_halftime_quiet_on_placeholder_book(cfg: JarvisConfig, rt: Runtime, sent: Sent) -> None:
    txt = _halftime_text(cfg, rt, sent, 0.01, 0.99)
    assert "DON'T SELL" not in txt and "NO BID" not in txt


def test_halftime_survives_a_book_read_failure(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise duckdb.IOException("locked")

    monkeypatch.setattr(posts, "_kalshi_book", boom)
    txt = _halftime_text(cfg, rt, sent, 0.05, 0.30)
    assert "halftime check-in" in txt and "DON'T SELL" not in txt


def _lines_for(cfg: JarvisConfig, rows: list[dict[str, Any]]) -> list[str]:
    return posts.halftime_lines(cfg, _LG, rows, None, CLOCK)  # type: ignore[arg-type]


class _LG:
    matchup = "A@B"
    away, home, away_score, home_score = "A", "B", 50, 48


def _leg(bundle: str, idx: int, label: str, ticker: str) -> dict[str, Any]:
    return {
        "bundle_id": bundle, "leg_idx": idx, "venue": "kalshi", "ticker": ticker, "side": "yes",
        "contracts": 33, "leg_desc": f"leg {idx}", "edge_label": label, "kind": "prop",
        "team_id": None, "price": 0.2,
    }  # fmt: skip


def test_daily_dollar_and_combos_get_no_per_leg_warning_but_a_pointer(
    cfg: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = CLOCK - timedelta(minutes=2)
    monkeypatch.setattr(posts, "_kalshi_book", lambda c, t: {"T-0": (0.01, 0.3, ts)})
    dd = [_leg("b1", 0, "daily_dollar", "T-0"), _leg("b1", 1, "daily_dollar", "T-1")]
    out = "\n".join(_lines_for(cfg, dd))
    assert "DON'T SELL" not in out and "exit-check" in out
    combo = [_leg("b2", 0, "strong", "T-0"), _leg("b2", 1, "strong", "T-1")]
    out2 = "\n".join(_lines_for(cfg, combo))
    assert "DON'T SELL" not in out2 and "exit-check" in out2


# ---------------------------------------------------------------- CLI


def test_cli_motivating_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["exit-check", "--bid", "0", "--fair", "100", "--contracts", "10"]) == 0
    out = capsys.readouterr().out
    assert "DON'T SELL AT THIS BID" in out and "~$10.00" in out


def test_cli_all_legs_hit_mode(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["exit-check", "--bid", "0", "--legs-hit", "6", "--legs-total", "6", "--contracts", "10"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "all 6 legs hit" in out and "~$10.00" in out
    assert cli.main(["exit-check", "--legs-hit", "4", "--legs-total", "6"]) == 0
    assert "unsettled" in capsys.readouterr().out


def test_cli_validates_and_explains(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["exit-check", "--bid", "4", "--fair", "150"]) == 2
    assert "CENTS" in capsys.readouterr().err
    assert cli.main(["exit-check", "--bid", "4", "--fair", "0"]) == 2
    assert cli.main(["exit-check", "--bid", "4"]) == 2
    assert cli.main(["exit-check", "--bid", "4", "--fair", "0.31", "--contracts", "10"]) == 0
    cap = capsys.readouterr()
    assert "did you pass dollars" in cap.err and "No warning:" in cap.out
    assert cli.main(["exit-check", "--bid", "30", "--fair", "31"]) == 0
    assert "bid is not far below fair" in capsys.readouterr().out


def test_module_is_read_only_no_order_or_credential_code() -> None:
    src = (Path(cli.__file__).parent / "exitwarn.py").read_text().lower()
    for banned in ("requests", "httpx", "urllib", ".post(", "api_key", "getenv", "environ"):
        assert banned not in src
