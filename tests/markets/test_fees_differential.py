"""Differential test: nba.markets.fees reproduces every pre-consolidation fee body bit for bit.

The three reference oracles below are the bodies of the old implementations, copied verbatim
before the callers were switched (lane E2b, 2026-11-17):

* ``_oracle_kalshi``      nba/parlay/ev.py ``order_fee`` (float, ceil to the cent per order)
* ``_oracle_fastarb_pm``  nba/markets/fastarb/config.py ``FeeSchedule.order_fee`` (Decimal,
  half-even)
* ``_oracle_jarvis_pm``   nba/jarvis/edge.py ``_order_fee`` bankers branch (Decimal, half-even)

Every comparison is exact: floats by ``float.hex`` (sign of zero included), exceptions by type
and message. Each implementation is checked on at least 100,000 cases, both through the pure
function and through the public wrapper that now calls it.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from nba.jarvis.config import load_config as load_jarvis_config
from nba.jarvis.edge import _order_fee
from nba.markets import fees as F
from nba.markets.fastarb.config import FeeSchedule
from nba.parlay.config import FeeModel
from nba.parlay.ev import order_fee

MIN_CASES = 100_000
SEED = 20261117


# ------------------------------------------------------------------ reference oracles (verbatim)


def _oracle_kalshi(
    price: float, fee: FeeModel, contracts: int = 1, *, order_type: str = "taker", series: str = ""
) -> float:
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be in [0, 1]")
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    rate = fee.rate(order_type)
    if fee.formula == "p_one_minus_p":
        raw = fee.multiplier(series) * rate * contracts * price * (1.0 - price)
    elif fee.formula == "flat":
        raw = fee.multiplier(series) * rate * contracts
    else:
        raise ValueError(f"unknown fee formula {fee.formula!r}")
    return math.ceil(round(raw * 100.0, 9)) / 100.0 if raw > 0 else 0.0


def _oracle_fastarb_pm(coefficient: float, price: float, contracts: int) -> float:
    raw = Decimal(str(coefficient)) * contracts * Decimal(str(price)) * (1 - Decimal(str(price)))
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _oracle_jarvis_pm(fee: FeeModel, price: float, contracts: int, series: str) -> float:
    raw = Decimal(str(fee.multiplier(series))) * Decimal(str(fee.rate("taker"))) * contracts
    raw *= Decimal(str(price)) * (Decimal(1) - Decimal(str(price)))
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


# ------------------------------------------------------------------------------- inputs


def _prices(rng: random.Random) -> list[float]:
    """0, 1, 0.5, every 1/10000, every cent and tenth-cent tick (two float spellings each) and
    their float neighbours, plus seeded uniform draws."""
    out: set[float] = {0.0, 1.0, 0.5}
    out.update(i / 10_000 for i in range(10_001))
    for c in range(101):
        for x in (c / 100, c * 0.01, round(c * 0.01, 2), 1.0 - (100 - c) / 100):
            out.update((x, math.nextafter(x, -1.0), math.nextafter(x, 2.0)))
    for t in range(1001):
        out.update((t / 1000, t * 0.001))
    out.update(rng.random() for _ in range(2_000))
    return sorted(out)


CONTRACTS_GRID = (1, 2, 3, 7, 20, 101, 1_000, 1_000_000)
CONTRACTS_ALL = (1, 2, 3, 4, 5, 7, 10, 13, 20, 25, 50, 99, 100, 101, 250, 500, 1_000, 10**4, 10**6)
RATES = (0.07, 0.0175, 0.0695, 0.05, 0.035, 0.0, 0.1)
MULTS = (1.0, 0.5, 0.25, 2.0, 0.75)
SERIES = ("", "KXNBAGAME", "KXNBAGAME-26NOV17", "KXNBAPTS", "KXNBA", "polymarket_us_winner")
MULT_TABLE = (("KXNBA", 0.5), ("KXNBAGAME", 0.25))


def _out(fn: Callable[..., float], *args: Any, **kw: Any) -> Any:
    """The result's exact float bits, or the exception's type and message."""
    try:
        return float.hex(fn(*args, **kw))
    except Exception as exc:  # noqa: BLE001 - the exception itself is the compared value
        return (type(exc), str(exc))


def _kalshi_cases() -> Iterator[tuple[float, FeeModel, int, str, str]]:
    rng = random.Random(SEED)
    base = FeeModel("kalshi_fee_schedule", "p_one_minus_p", 0.07, True, 0.0175)
    for p in _prices(rng):
        for n in CONTRACTS_GRID:
            yield p, base, n, "taker", ""
    for _ in range(60_000):
        fm = FeeModel(
            "k",
            rng.choice(("p_one_minus_p", "p_one_minus_p", "flat", "bogus")),
            rng.choice((*RATES, rng.random() * 0.2)),
            True,
            rng.choice((None, 0.0175, 0.0, rng.random() * 0.05)),
            rng.choice((*MULTS, rng.random() * 2)),
            rng.choice(((), MULT_TABLE)),
        )
        p = rng.choice((rng.random(), round(rng.random(), 2), rng.random() * 1.2 - 0.1))
        n = rng.choice((*CONTRACTS_ALL, rng.randint(-2, 5_000)))
        yield p, fm, n, rng.choice(("taker", "taker", "maker", "bogus")), rng.choice(SERIES)


def test_kalshi_ceil_cent_matches_parlay_ev_oracle() -> None:
    n_cases = 0
    bad: list[tuple[float, int, str]] = []
    for p, fm, n, ot, s in _kalshi_cases():
        n_cases += 1
        want = _out(_oracle_kalshi, p, fm, n, order_type=ot, series=s)
        if want != _out(order_fee, p, fm, n, order_type=ot, series=s):
            bad.append((p, n, ot))
        valid = 0.0 <= p <= 1.0 and n > 0 and ot in ("taker", "maker")
        if valid and (ot == "taker" or fm.maker_coefficient is not None):
            pure = _out(F.ceil_cent_fee, fm.formula, fm.multiplier(s), fm.rate(ot), n, p)
            if want != pure:
                bad.append((p, n, "pure"))
    assert n_cases >= MIN_CASES, n_cases
    assert not bad, (len(bad), bad[:5])


def _pm_cases() -> Iterator[tuple[float, float, int]]:
    rng = random.Random(SEED + 1)
    prices = _prices(rng)
    for p in prices:
        for n in CONTRACTS_GRID:
            yield 0.0695, p, n
    extra = (-0.1, -0.01, 1.01, 1.5)  # no input validation in either Polymarket body
    for _ in range(60_000):
        coef = rng.choice((*RATES, rng.random() * 0.2))
        p = rng.choice((rng.random(), round(rng.random(), 2), rng.choice(prices), *extra))
        yield coef, p, rng.choice((*CONTRACTS_ALL, rng.randint(0, 5_000)))


def test_polymarket_fastarb_bankers_matches_feeschedule_oracle() -> None:
    n_cases = 0
    bad: list[tuple[float, float, int]] = []
    for coef, p, n in _pm_cases():
        n_cases += 1
        sched = FeeSchedule("polymarket_us_standard_taker", coef, True)
        want = _out(_oracle_fastarb_pm, coef, p, n)
        if want != _out(sched.order_fee, p, n):
            bad.append((coef, p, n))
        if want != _out(F.bankers_cent_fee, coef, n, p):
            bad.append((coef, p, -n))
    assert n_cases >= MIN_CASES, n_cases
    assert not bad, (len(bad), bad[:5])


def test_polymarket_jarvis_bankers_matches_edge_oracle() -> None:
    cfg0 = load_jarvis_config()
    assert cfg0.fee_rounding["polymarket"] == "bankers_cent"
    rng = random.Random(SEED + 2)
    n_cases = 0
    bad: list[tuple[float, float, int, str]] = []
    for i, (coef, p, n) in enumerate(_pm_cases()):
        m = MULTS[i % len(MULTS)] if i % 3 else 1.0
        fm = FeeModel("pm", "p_one_minus_p", coef, True, 0.0, m, MULT_TABLE if i % 7 == 0 else ())
        s = rng.choice(SERIES)
        cfg = replace(cfg0, fees={**cfg0.fees, "polymarket": fm})
        n_cases += 1
        want = _out(_oracle_jarvis_pm, fm, p, n, s)
        if want != _out(_order_fee, cfg, "polymarket", p, n, s):
            bad.append((coef, p, n, s))
        if want != _out(F.bankers_cent_fee_scaled, fm.multiplier(s), fm.rate("taker"), n, p):
            bad.append((coef, p, -n, s))
    assert n_cases >= MIN_CASES, n_cases
    assert not bad, (len(bad), bad[:5])


def test_jarvis_kalshi_branch_still_routes_to_parlay_ev() -> None:
    """Kalshi in JARVIS is ceil_cent: ``_order_fee`` must equal the old ``order_fee`` exactly."""
    cfg = load_jarvis_config()
    kfee = cfg.fee("kalshi")
    rng = random.Random(SEED + 3)
    n_cases = 0
    bad: list[tuple[float, int, str]] = []
    for p in _prices(rng):
        for n in (1, 3, 20):
            for s in ("", "KXNBAGAME"):
                n_cases += 1
                want = _out(_oracle_kalshi, p, kfee, n, series=s)
                if want != _out(_order_fee, cfg, "kalshi", p, n, s):
                    bad.append((p, n, s))
    assert n_cases >= 50_000, n_cases
    assert not bad, (len(bad), bad[:5])
