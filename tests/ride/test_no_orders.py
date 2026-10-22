"""Non-negotiable 5: no order, portfolio or credential code in nba/ride."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r"portfolio|/orders|order_group|place_order|create_order|cancel_order|batch_order|amend_order"
    r"|/fills|/communications|/rfq|exchange/|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|KALSHI-ACCESS|authorization|bearer|\.post\(|\.put\(|\.delete\(|\.patch\(|method=|httpx|requests\.",
    re.IGNORECASE,
)


def test_no_order_portfolio_credential_or_network_code() -> None:
    bad = []
    for path in (ROOT / "nba" / "ride").rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if FORBIDDEN.search(line):
                bad.append(f"{path.name}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)
