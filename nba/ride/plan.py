"""Position plan after a RIDE flag (Devin's calm-calls rule): one line per real change.

Alerts fire only on: settlement, a >= 25% bid move over ~10 minutes, a loose stop (60% of peak bid
once in profit, 35% of entry otherwise), or a broken thesis (late and lead under ~10 and ours < 95%,
or ours < 85%). Each fires once (latched); no swing chatter. The bid is the captured YES bid.
This is a monitor of the hypothetical position the flag describes; nothing is ever placed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from nba.ride.gate import RideConfig


@dataclass
class Position:
    game: str  # "AWAY@HOME"
    ticker: str
    side_team: str
    entry_ask: float
    contracts: int
    entry_fee: float
    entry_ts: float  # unix seconds
    peak_bid: float = 0.0
    in_profit: bool = False
    bids: list[tuple[float, float]] = field(default_factory=list)  # (unix ts, bid)
    stop_fired: bool = False
    broken_fired: bool = False
    settled: bool = False
    last_trend_ref: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        d = dict(d)
        d["bids"] = [(float(a), float(b)) for a, b in d.get("bids", [])]
        return cls(**d)

    def stop_level(self, cfg: RideConfig) -> float:
        if self.in_profit:
            return round(cfg.stop_profit_frac * self.peak_bid, 4)
        return round(cfg.stop_entry_frac * self.entry_ask, 4)


@dataclass(frozen=True)
class Obs:
    ts: float
    bid: float | None
    ours_leader: float  # our prob that the position's team wins
    lead: int  # position team's lead (negative = trailing)
    secs_left: float
    final: bool
    won: bool | None  # known only when final


def check(pos: Position, o: Obs, cfg: RideConfig | None = None) -> list[str]:
    """Update latches and return the alert lines (empty when nothing real changed)."""
    cfg = cfg or RideConfig()
    if pos.settled:
        return []
    lines: list[str] = []
    if o.final:
        pos.settled = True
        if o.won:
            gain = pos.contracts * (1.0 - pos.entry_ask) - pos.entry_fee
            lines.append(
                f"SETTLED WIN {pos.game}: {pos.ticker} pays ${pos.contracts:.2f}; "
                f"profit ${gain:.2f} after fee"
            )
        else:
            loss = pos.contracts * pos.entry_ask + pos.entry_fee
            lines.append(f"SETTLED LOSS {pos.game}: {pos.ticker} pays $0; loss ${loss:.2f}")
        return lines
    if o.bid is not None:
        pos.bids.append((o.ts, o.bid))
        pos.bids = [(t, b) for t, b in pos.bids if o.ts - t <= 2 * cfg.trend_window_s]
        if o.bid > pos.entry_ask:
            pos.in_profit = True
        if pos.in_profit:
            pos.peak_bid = max(pos.peak_bid, o.bid)
        if not pos.stop_fired and o.bid <= pos.stop_level(cfg):
            pos.stop_fired = True
            lines.append(
                f"STOP LEVEL {pos.game}: bid {o.bid:.2f} <= stop {pos.stop_level(cfg):.2f} "
                f"(entry {pos.entry_ask:.2f}, peak {pos.peak_bid:.2f}); "
                f"lead {o.lead:+d}, ours {o.ours_leader:.3f}"
            )
        ref = _ref_bid(pos, o.ts, cfg)
        if ref is not None and ref[1] > 0:
            move = (o.bid - ref[1]) / ref[1]
            base = pos.last_trend_ref if pos.last_trend_ref is not None else ref[1]
            if abs(o.bid - base) / base >= cfg.trend_move:
                pos.last_trend_ref = o.bid
                lines.append(
                    f"TREND {pos.game}: bid {ref[1]:.2f} -> {o.bid:.2f} ({move:+.0%}) "
                    f"in {(o.ts - ref[0]) / 60:.0f} min; "
                    f"lead {o.lead:+d}, ours {o.ours_leader:.3f}"
                )
    if not pos.broken_fired:
        late_small = (
            o.secs_left <= cfg.broken_secs_left
            and o.lead < cfg.broken_lead
            and o.ours_leader < 0.95
        )
        if o.ours_leader < cfg.broken_prob or late_small:
            pos.broken_fired = True
            lines.append(
                f"THESIS BROKEN {pos.game}: lead {o.lead:+d} with {o.secs_left / 60:.1f} min left, "
                f"ours {o.ours_leader:.3f}"
            )
    return lines


def _ref_bid(pos: Position, now: float, cfg: RideConfig) -> tuple[float, float] | None:
    """Oldest bid sample at least 8 minutes old (about the trend window), else None."""
    old = [(t, b) for t, b in pos.bids if now - t >= 0.8 * cfg.trend_window_s]
    return old[-1] if old else None
