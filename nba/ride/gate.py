"""Alert gating: pure functions. WATCH at 65/35 (log only); RIDE needs ours >= 98%, ESPN within
3 points on the same side, a fresh heartbeat and fresh game data, a quote, and no earlier alert."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RideConfig:
    watch_p: float = 0.65
    ride_p: float = 0.98
    espn_tol: float = 0.03  # |ours - ESPN| on the leader's side
    heartbeat_max_age_s: float = 120.0
    data_max_age_s: float = 150.0  # newest ESPN summary of the game
    credit: float = 10.84
    # plan (Devin's calm-calls rule)
    trend_move: float = 0.25
    trend_window_s: float = 600.0
    stop_profit_frac: float = 0.60  # of peak bid once in profit
    stop_entry_frac: float = 0.35  # of entry otherwise
    broken_lead: int = 10  # lead under this ...
    broken_secs_left: float = 600.0  # ... with this little time left (and ours < 95%)
    broken_prob: float = 0.85


@dataclass(frozen=True)
class GateInput:
    state: str  # pre | in | post
    leader: str  # home | away
    ours: float  # leader's win prob by our model
    espn: float | None  # ESPN's win prob for the SAME leader (None = missing)
    heartbeat_age_s: float | None
    data_age_s: float
    quote: str  # ok | none (no book/listing) | no_room (ask at or above $1.00 or missing)
    already_alerted: bool


@dataclass(frozen=True)
class Verdict:
    level: str  # RIDE | WATCH | NONE
    reasons: tuple[str, ...] = field(default_factory=tuple)  # why a >= ride_p game was NOT a RIDE


def evaluate(g: GateInput, cfg: RideConfig | None = None) -> Verdict:
    cfg = cfg or RideConfig()
    if g.state != "in":
        return Verdict("NONE", (f"state_{g.state}",))
    if g.ours < cfg.watch_p:
        return Verdict("NONE")
    if g.ours < cfg.ride_p:
        return Verdict("WATCH")
    why: list[str] = []
    if g.already_alerted:
        why.append("already_alerted")
    if g.heartbeat_age_s is None:
        why.append("heartbeat_missing")
    elif g.heartbeat_age_s > cfg.heartbeat_max_age_s:
        why.append("stale_heartbeat")
    if g.data_age_s > cfg.data_max_age_s:
        why.append("stale_game_data")
    if g.espn is None:
        why.append("espn_missing")
    elif abs(g.ours - g.espn) > cfg.espn_tol + 1e-9:
        why.append("espn_disagrees")
    if g.quote != "ok":
        why.append(f"quote_{g.quote}")
    return Verdict("RIDE" if not why else "WATCH", tuple(why))
