"""Bankroll plan: a read-only view of a JSON file. The plan is a plan; Devin places every order.

Nothing here talks to a broker or a market. The derived numbers (floor, risk capital, budget,
month targets) are computed from the file's own fields so every figure traces to the data.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "bankroll.json"

Tier = Literal["SAFE", "MEDIUM", "RISKY"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Move(_Strict):
    id: str
    tier: Tier
    title: str
    stake: float
    source: Literal["bonus", "cash"]
    limit: float
    payout: float
    chance: float
    reason: str
    status: Literal["todo", "placed", "skipped"]


class Position(_Strict):
    id: str
    title: str
    legs: list[str]
    cost: float
    source: Literal["bonus", "cash"]
    max_payout: float
    fair_now: float
    sell_if_offer_at_least: float


class ScoreRow(_Strict):
    date: str
    end_cash: float
    pnl: float
    pnl_by_tier: dict[str, float]
    bonus_converted: float


class Balance(_Strict):
    date: str
    balance: float


class Account(_Strict):
    cash: float
    bonus: float


class BankrollFile(_Strict):
    schema_: str | None = None
    as_of: date
    plan_start: date
    floor_base: float
    floor_monthly_growth: float
    budget_share: float
    budget_override: str | None = None
    month_start: Balance
    account: Account
    moves: list[Move]
    positions: list[Position]
    scorecard: list[ScoreRow]


def months_since(start: date, now: date) -> int:
    """Whole calendar months elapsed from `start` to `now` (never negative)."""
    m = (now.year - start.year) * 12 + (now.month - start.month)
    if now.day < start.day:
        m -= 1
    return max(m, 0)


def build(raw: dict[str, object]) -> dict[str, object]:
    """Validate the file and add the derived account numbers."""
    raw = {("schema_" if k == "schema" else k): v for k, v in raw.items()}
    f = BankrollFile.model_validate(raw)
    months = months_since(f.plan_start, f.as_of)
    floor = round(f.floor_base * (1 + f.floor_monthly_growth) ** months, 2)
    risk = round(max(f.account.cash - floor, 0.0), 2)
    budget = round(f.budget_share * risk, 2)
    base = f.month_start.balance
    cash_planned = round(sum(m.stake for m in f.moves if m.source == "cash"), 2)
    out = f.model_dump(mode="json", exclude={"schema_"})
    out["schema"] = "bankroll.v1"
    out["derived"] = {
        "months": months,
        "floor": floor,
        "risk_capital": risk,
        "budget": budget,
        "cash_planned": cash_planned,
        "target": round(base * 1.01, 2),
        "stretch": round(base * 1.05, 2),
    }
    out["budget_override"] = f.budget_override
    out["note"] = "This is a plan, not an order. Devin places every order himself."
    return out


def load(path: Path | None = None) -> dict[str, object]:
    p = path or Path(os.environ.get("APP_BANKROLL_FILE", str(DEFAULT_FILE)))
    return build(json.loads(p.read_text()))
