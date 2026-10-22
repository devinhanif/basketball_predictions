"""Non-negotiable 5: no order, portfolio or credential code in the capture or paper-trade code."""

from __future__ import annotations

import re
from pathlib import Path

from nba.live import kalshi_pull

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = [ROOT / "nba" / "live", ROOT / "nba" / "papertrade"]
FORBIDDEN = re.compile(
    r"portfolio|/orders|order_group|place_order|create_order|cancel_order|batch_order|amend_order"
    r"|/fills|/communications|/rfq|/quotes|exchange/|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|KALSHI-ACCESS|authorization|bearer|\.post\(|\.put\(|\.delete\(|\.patch\(|method=",
    re.IGNORECASE,
)


def test_no_order_portfolio_or_credential_strings() -> None:
    bad = []
    for pkg in PACKAGES:
        for path in pkg.rglob("*.py"):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if FORBIDDEN.search(line):
                    bad.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_only_market_data_paths_exist() -> None:
    assert kalshi_pull.ALLOWED_PATHS == (
        "/series",
        "/markets",
        "/markets/{ticker}",
        "/markets/{ticker}/orderbook",
    )
    src = (ROOT / "nba" / "live" / "kalshi_pull.py").read_text()
    used = set(re.findall(r"\{BASE\}(/[A-Za-z_/{}]*)", src))
    normalised = {u.replace("{ticker}", "{ticker}") for u in used}
    assert normalised <= set(kalshi_pull.ALLOWED_PATHS)


def test_http_layer_is_get_only() -> None:
    src = (ROOT / "nba" / "live" / "http.py").read_text()
    assert ".get(" in src and not re.search(r"\.(post|put|delete|patch|request|send)\(", src)
