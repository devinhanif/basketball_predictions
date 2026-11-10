"""Player checkpoints (Devin's request, 2026-11-10): one silent message per team channel at T-180,
T-30 and T-5 minutes before a game's real tip, one line per likely player.

Each line carries four things: the stored model's projection (mean pts / reb / ast / 3PM and
minutes), the official injury status with the model's P(plays) (and a confirmed starter flag at
T-30 / T-5), the best-EV line per stat from the SAME pricing path the cards use
(``facts.priced_lines`` -> ``single_candidate``), and what changed since the last posted checkpoint.

Delivery and formatting only. Nothing here computes a price, an edge, a fee, a label or a stake,
and nothing changes an alert. The messages are pre-tip only: they are posted once, never edited,
and never given outcomes afterwards (LINE_CAL_LIVE hygiene, DECISIONS 2026-11-10).

Timing is a pure function of (now, tip, checkpoint, tolerance, state): a tick inside
``[T-k, T-k + tolerance]`` posts; a checkpoint missed past the tolerance is logged as skipped and
never posted late; nothing is ever posted at or after tip (real tip time, never a proxy). Known-at
is first-class: the as-of time of the forecasts, the injury report and the lineups is printed in
every message, and every read is bounded by the checkpoint's own clock.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.cards.colors import color
from nba.jarvis.cards.discord import CardClient
from nba.jarvis.cards.embed import DESC_MAX, enforce
from nba.jarvis.cards.state import _append, _read
from nba.jarvis.cards.webhooks import TeamWebhooks
from nba.jarvis.config import JarvisConfig
from nba.jarvis.forecasts import PPlayView
from nba.jarvis.ingame import norm_name
from nba.jarvis.models import Game
from nba.jarvis.slate import SlateState
from nba.jarvis.sources import open_ro

log_names = logging.getLogger(__name__)


def _et_to_utc(ts: datetime) -> datetime:
    """Naive US-Eastern wall clock -> naive UTC."""
    from zoneinfo import ZoneInfo

    return ts.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC).replace(tzinfo=None)


def to_report_dt(ts_utc: datetime) -> datetime:
    """Naive UTC -> naive US-Eastern wall clock (the player_availability.as_of clock)."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    return ts_utc.replace(tzinfo=UTC).astimezone(et).replace(tzinfo=None)


@lru_cache(maxsize=1)
def _local_names() -> dict[int, str]:
    try:
        from nba.explain import local_names

        return dict(local_names.load_local_names().names)
    except Exception as exc:  # noqa: BLE001 - names are cosmetic
        log_names.warning("local player names unavailable (%s)", type(exc).__name__)
        return {}


def _player_name(kb: Any, pid: int) -> str:
    name = str(kb.name(pid))
    if name.lower().startswith("player "):
        return _local_names().get(int(pid), name)
    return name


log = logging.getLogger("nba.jarvis.cards.players")

STATS = ("pts", "reb", "ast", "fg3m")
PROPS_MODEL = "props_context_residual"  # the production props forecast (the one the slate prices)
DISCLAIMER_PCP = "Paper only. Not advice. No stake. Pre-tip only; this message is never edited."
LATE_LOG_HOURS = 2.0  # a missed checkpoint is logged only while the game is this recent
DESC_BUDGET = DESC_MAX - 60  # room for the "+N more" line


# ------------------------------------------------------------------------------ config


@dataclass(frozen=True)
class PcpConfig:
    enabled: bool
    minutes: tuple[int, ...]
    min_p_play: float
    tolerance: dict[int, float]
    after_tip_guard_min: float
    state: Path
    proj_move: dict[str, float]
    p_play_move: float
    price_move_c: float
    starters_from_min: int


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "minutes": [180, 30, 5],
    "min_p_play": 0.5,
    "lateness_tolerance_min": {180: 10, 30: 6, 5: 2},
    "after_tip_guard_min": 1,
    "state": "data/jarvis/player_checkpoints_state.jsonl",
    "proj_move": {"pts": 0.5, "reb": 0.3, "ast": 0.3, "fg3m": 0.2, "minutes": 1.0},
    "p_play_move": 0.05,
    "price_move_c": 2,
    "starters_from_min": 30,
}


def pcp_config(cfg: JarvisConfig) -> PcpConfig:
    raw = {**DEFAULTS, **dict(cfg.team_cards.get("player_checkpoints") or {})}
    mins = tuple(int(m) for m in raw["minutes"])
    tol = {int(k): float(v) for k, v in dict(raw["lateness_tolerance_min"]).items()}
    path = Path(str(raw["state"]))
    return PcpConfig(
        enabled=bool(raw["enabled"]), minutes=mins, min_p_play=float(raw["min_p_play"]),
        tolerance={m: tol.get(m, 0.0) for m in mins},
        after_tip_guard_min=float(raw["after_tip_guard_min"]),
        state=path if path.is_absolute() else cfg.root / path,
        proj_move={k: float(v) for k, v in dict(raw["proj_move"]).items()},
        p_play_move=float(raw["p_play_move"]), price_move_c=float(raw["price_move_c"]),
        starters_from_min=int(raw["starters_from_min"]),
    )  # fmt: skip


def validate(raw: dict[str, Any]) -> None:
    """Called by config validation when the block is enabled; raises ValueError."""
    mins = [int(m) for m in raw.get("minutes", [])]
    if not mins or any(m <= 0 for m in mins) or mins != sorted(set(mins), reverse=True):
        raise ValueError("player_checkpoints.minutes must be distinct positive, descending")
    tol = {int(k): float(v) for k, v in dict(raw.get("lateness_tolerance_min", {})).items()}
    gaps = [a - b for a, b in zip(mins, mins[1:], strict=False)]
    if any(m not in tol or tol[m] <= 0 for m in mins):
        raise ValueError("player_checkpoints.lateness_tolerance_min needs a value > 0 per minute")
    if any(tol[a] >= g for a, g in zip(mins, gaps, strict=False)):
        raise ValueError("a lateness tolerance must be shorter than the gap to the next checkpoint")
    if not 0.0 <= float(raw.get("min_p_play", 0.5)) <= 1.0:
        raise ValueError("player_checkpoints.min_p_play must be in [0, 1]")
    if not raw.get("state"):
        raise ValueError("player_checkpoints.state is required")


# ------------------------------------------------------------------------------ timing (pure)


def window_state(
    now: datetime, tip: datetime, minute: int, tol_min: float, guard_min: float, recorded: bool
) -> str:
    """done | over | wait | due | late. ``over`` = tip has passed and nothing was recorded;
    ``late`` = the window closed before tip and nothing was recorded."""
    if recorded:
        return "done"
    if now >= tip:
        return "over"
    start = tip - timedelta(minutes=minute)
    end = min(start + timedelta(minutes=tol_min), tip - timedelta(minutes=guard_min))
    if now < start:
        return "wait"
    return "due" if now <= end else "late"


# ------------------------------------------------------------------------------ state


class PcpState:
    """Append-only JSON lines, one record per (slate, game, team, minute): ``posted`` (with the
    per-player snapshot the next checkpoint diffs against) or ``skipped_late``. Read once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.skipped = 0
        self._by: dict[tuple[str, str, str, int], dict[str, Any]] | None = None

    def _load(self) -> dict[tuple[str, str, str, int], dict[str, Any]]:
        if self._by is None:
            by: dict[tuple[str, str, str, int], dict[str, Any]] = {}
            recs, bad = _read(self.path)
            self.skipped += bad
            for r in recs:
                try:
                    key = (str(r["slate"]), str(r["game_id"]), str(r["team"]), int(r["minute"]))
                    if r["kind"] not in ("posted", "skipped_late"):
                        raise ValueError(r["kind"])
                except (KeyError, TypeError, ValueError):
                    self.skipped += 1
                    continue
                by.setdefault(key, r)  # first record wins: a key is written once
            self._by = by
        return self._by

    def get(self, slate: object, game_id: str, team: str, minute: int) -> dict[str, Any] | None:
        return self._load().get((str(slate), game_id, team, minute))

    def previous_posted(
        self, slate: object, game_id: str, team: str, minute: int
    ) -> dict[str, Any] | None:
        """The nearest earlier checkpoint actually posted (a skipped one is passed over)."""
        best: dict[str, Any] | None = None
        for (s, g, t, m), r in self._load().items():
            if (s, g, t) != (str(slate), game_id, team) or m <= minute or r["kind"] != "posted":
                continue
            if best is None or m < int(best["minute"]):
                best = r
        return best

    def write(self, rec: dict[str, Any]) -> None:
        key = (str(rec["slate"]), str(rec["game_id"]), str(rec["team"]), int(rec["minute"]))
        by = self._load()
        if key in by:
            return  # idempotent: never a second record for a checkpoint
        _append(self.path, rec)
        by[key] = rec


# ------------------------------------------------------------------------------ data


@dataclass(frozen=True)
class Line:
    line: float
    ask_c: float
    p_model: float | None
    ev_c: float
    fee_c: float


@dataclass(frozen=True)
class PRow:
    pid: int
    name: str
    team: str
    status: str | None  # out | doubtful | questionable | probable | available | None (not listed)
    reason: str
    p_play: float | None
    minutes: float | None
    means: dict[str, float]
    starter: bool | None  # True confirmed starter, False confirmed bench, None not known
    lines: dict[str, Line]
    has_forecast: bool
    unmeasured: tuple[str, ...] = ()  # stats Kalshi prices but with no usable sharp line (no EV)
    unders: dict[str, Line] = field(default_factory=dict)  # best-EV Kalshi No per stat (worst case)


@dataclass
class GameData:
    """Everything one game's messages need, with the as-of time of each source."""

    game: Game
    rows: dict[str, list[PRow]] = field(default_factory=dict)  # team -> every known player
    report: dict[str, dict[int, str]] = field(default_factory=dict)  # team -> pid -> status
    forecast_asof: datetime | None = None  # newest forecast row used (naive UTC)
    report_asof: datetime | None = None
    lineup_asof: datetime | None = None
    under_worst_case: bool = False  # True while Kalshi's DNP rule is unverified (a DNP = No loss)
    lineup_note: dict[str, str] = field(default_factory=dict)
    unplaced: int = 0


def _jload(v: Any) -> dict[str, Any]:
    d = json.loads(v) if isinstance(v, str) else v
    return d if isinstance(d, dict) else {}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


Store = tuple[
    dict[tuple[str, int], dict[str, Any]],
    dict[tuple[str, int], tuple[str, str]],
    dict[str, datetime],
]


def read_store(cfg: JarvisConfig, gids: list[str], now: datetime) -> Store:
    """Forecast rows and injury statuses as known at ``now`` (naive UTC), read-only.

    Returns (forecast[(game, pid)] = {made, p_play, minutes, means}, status[(game, pid)] =
    (status, reason), report_asof[game])."""
    path = cfg.paths["nba_db"]
    if not gids:
        return {}, {}, {}
    ph = ",".join("?" for _ in gids)
    con = open_ro(path)
    try:
        f_rows = con.execute(
            "SELECT game_id, player_id, target, arg_max(prediction, made_at), max(made_at) "
            f"FROM forward_predictions WHERE model_name = ? AND made_at <= ? AND game_id IN ({ph}) "
            "AND target IN ('pts', 'reb', 'ast', 'fg3m') GROUP BY game_id, player_id, target",
            [PROPS_MODEL, now, *gids],
        ).fetchall()
        a_rows = con.execute(
            "SELECT game_id, player_id, arg_max(status, as_of), arg_max(reason, as_of), max(as_of) "
            f"FROM player_availability WHERE as_of <= ? AND game_id IN ({ph}) "
            "GROUP BY game_id, player_id",
            [
                to_report_dt(now),
                *gids,
            ],  # as_of is naive US-Eastern wall clock (nba/explain/data.py)
        ).fetchall()
    finally:
        con.close()
    fwd: dict[tuple[str, int], dict[str, Any]] = {}
    for gid, pid, target, pred, made in f_rows:
        d = _jload(pred)
        cur = fwd.setdefault(
            (str(gid), int(pid)), {"made": made, "p_play": None, "minutes": None, "means": {}}
        )
        cur["made"] = max(cur["made"], made)
        mean = _f(d.get("mean"))
        if mean is not None:
            cur["means"][str(target)] = mean
        cur["p_play"] = _f(d.get("p_play")) if cur["p_play"] is None else cur["p_play"]
        cur["minutes"] = _f(d.get("proj_minutes")) if cur["minutes"] is None else cur["minutes"]
    status: dict[tuple[str, int], tuple[str, str]] = {}
    asof: dict[str, datetime] = {}
    for gid, pid, st_, reason, as_of_et in a_rows:
        status[(str(gid), int(pid))] = (str(st_), str(reason or ""))
        as_of = _et_to_utc(as_of_et)
        if str(gid) not in asof or as_of > asof[str(gid)]:
            asof[str(gid)] = as_of
    return fwd, status, asof


def gather_game(
    cfg: JarvisConfig, st: SlateState, kb: Any, g: Game, now: datetime, minute: int,
    pc: PcpConfig, store: Store, hold: Any = None,
) -> GameData:  # fmt: skip
    """Build every known player of both teams for one game, as of ``now``."""
    fwd, status, asof = store
    gd = GameData(g)
    gd.under_worst_case = not cfg.kalshi_prop_dnp_rule_verified
    gd.forecast_asof = max(
        (v["made"] for (gid, _p), v in fwd.items() if gid == g.game_id), default=None
    )
    gd.report_asof = asof.get(g.game_id)
    pids = {p for (gid, p) in fwd if gid == g.game_id} | {
        p for (gid, p) in status if gid == g.game_id
    }
    teams = kb.player_teams(pids) if pids else {}
    unmeas: set[tuple[int, str]] = set()
    pplay = {
        p: float(v["p_play"]) for (gid, p), v in fwd.items() if gid == g.game_id and v["p_play"]
    }
    shown_at = now.isoformat(timespec="seconds")
    priced = F.priced_lines(
        cfg, replace(st, model=PPlayView(st.model, pplay)), g.game_id, shown_at, kb, hold, unmeas
    )
    pv = replace(st, model=PPlayView(st.model, pplay))
    under_rows = F.priced_unders(cfg, pv, g.game_id, shown_at, kb, hold)
    starters, notes, lu_asof = _lineup(kb, g, minute <= pc.starters_from_min, now)
    gd.lineup_asof, gd.lineup_note = lu_asof, notes
    for t_ab in (g.away_abbr, g.home_abbr):
        gd.rows[t_ab] = []
        gd.report[t_ab] = {}
    for pid in sorted(pids):
        tid = teams.get(pid)
        found = None if tid is None else kb.abbr_of.get(tid)
        if found not in (g.away_abbr, g.home_abbr):
            gd.unplaced += 1
            continue
        ab = str(found)
        fv = fwd.get((g.game_id, pid))
        sv = status.get((g.game_id, pid))
        if sv is not None:
            gd.report[ab][pid] = sv[0]
        name = _player_name(kb, pid)
        out = sv is not None and sv[0] == "out"
        lines = {}
        unders: dict[str, Line] = {}
        for stat in STATS:
            o = priced.get((pid, stat))
            if o is not None and not out:
                lines[stat] = Line(o.line, o.ask * 100.0, o.model, o.edge_c, o.fee_c)
            u = under_rows.get((pid, stat))
            if u is not None and not out:
                unders[stat] = Line(u.line, u.ask * 100.0, None, u.edge_c, u.fee_c)
        starter: bool | None = None
        sset = starters.get(ab)
        if sset is not None:
            nn = norm_name(name)
            starter = True if nn in sset[0] else (False if sset[1] else None)
        gd.rows[ab].append(
            PRow(
                pid=pid, name=name, team=ab, status=None if sv is None else sv[0],
                reason="" if sv is None else F.short_reason(sv[1]),
                p_play=None if fv is None else fv["p_play"],
                minutes=None if fv is None else fv["minutes"],
                means={} if fv is None else dict(fv["means"]),
                starter=starter, lines=lines, unders=unders, has_forecast=fv is not None,
                unmeasured=tuple(
                    s for s in STATS if (pid, s) in unmeas and s not in lines and not out
                ),
            )
        )  # fmt: skip
    return gd


def _lineup(
    kb: Any, g: Game, want: bool, now: datetime
) -> tuple[dict[str, tuple[set[str], bool]], dict[str, str], datetime | None]:
    """team -> (normalised starter names, lineup fully confirmed), a note per team, and the
    snapshot time. Only when ``want`` (T-30 and later) and only confirmed names count."""
    if not want:
        return {}, {}, None
    try:
        lu = kb.lineups(g.game_id)
        if lu.get("error"):
            return {}, {}, None
        names, notes = F._starters(lu.get("starters") or {})
        asof = None
        if lu.get("snapshot_utc"):
            asof = datetime.fromisoformat(str(lu["snapshot_utc"]))
        if asof is not None and asof > now:
            return {}, {}, None  # a replay never reads a snapshot taken after its own clock
    except Exception as exc:  # noqa: BLE001 - lineups are a convenience
        log.warning("player checkpoint lineups unavailable (%s)", type(exc).__name__)
        return {}, {}, None
    out: dict[str, tuple[set[str], bool]] = {}
    for team, ns in names.items():
        if str(notes.get(team, "")).startswith("confirmed"):
            out[team] = ({norm_name(n) for n in ns}, notes.get(team) == "confirmed")
    return out, {t: str(n) for t, n in notes.items()}, asof


# ------------------------------------------------------------------------------ snapshot + diff


def snapshot(rows: list[PRow], report: dict[int, str]) -> dict[str, Any]:
    """What a posted message showed, for the next checkpoint to diff against (plain JSON)."""
    return {
        "players": {
            str(r.pid): {
                "name": r.name, "status": r.status, "p_play": r.p_play, "min": r.minutes,
                "mean": r.means, "starter": r.starter,
                "lines": {s: [ln.line, round(ln.ask_c, 1)] for s, ln in r.lines.items()},
            }
            for r in rows
        },
        "report": {str(p): s for p, s in report.items()},
    }  # fmt: skip


def listed(
    rows: list[PRow], report: dict[int, str], prev: dict[str, Any] | None, pc: PcpConfig
) -> list[PRow]:
    """Likely players (model P(plays) at or above ``min_p_play``) plus anyone whose status changed
    since the last posted checkpoint, by projected minutes."""
    prev_rep = {} if prev is None else dict(prev.get("snap", {}).get("report", {}))
    out: list[PRow] = []
    for r in rows:
        likely = r.has_forecast and (r.p_play is not None and r.p_play >= pc.min_p_play)
        changed = prev is not None and prev_rep.get(str(r.pid)) != report.get(r.pid)
        if likely or changed:
            out.append(r)
    out.sort(key=lambda r: (-(r.minutes or 0.0), r.name))
    return out


def _stat_label(s: str) -> str:
    return T.STAT.get(s, s)


def diff_tags(r: PRow, prev: dict[str, Any], pc: PcpConfig) -> list[str]:
    """Plain words for what moved since the previous posted checkpoint (empty = nothing moved)."""
    snap = prev.get("snap", {})
    old = snap.get("players", {}).get(str(r.pid))
    prev_status = snap.get("report", {}).get(str(r.pid))
    tags: list[str] = []
    if prev_status != r.status:
        tags.append(f"status {prev_status or 'not on report'} -> {r.status or 'not on report'}")
    if old is None:
        tags.append("added to the list")
        return tags
    if r.minutes is not None and old.get("min") is not None:
        d = r.minutes - float(old["min"])
        if abs(d) >= pc.proj_move.get("minutes", 1.0):
            tags.append(f"min {'up' if d > 0 else 'down'} {abs(d):.0f}")
    for s in STATS:
        a, b = r.means.get(s), old.get("mean", {}).get(s)
        if a is not None and b is not None and abs(a - b) >= pc.proj_move.get(s, 0.5):
            tags.append(f"{_stat_label(s)} {'up' if a > b else 'down'} {abs(a - b):.1f}")
    if r.p_play is not None and old.get("p_play") is not None:
        d = r.p_play - float(old["p_play"])
        if abs(d) >= pc.p_play_move:
            tags.append(f"plays {100 * float(old['p_play']):.0f}% -> {100 * r.p_play:.0f}%")
    if r.starter is not None and r.starter != old.get("starter"):
        tags.append("confirmed starter" if r.starter else "confirmed off the bench")
    ol = old.get("lines", {})
    for s in STATS:
        n, o = r.lines.get(s), ol.get(s)
        if n is not None and o is not None:
            if float(o[0]) != n.line:
                tags.append(f"{_stat_label(s)} line {o[0]:g}+ -> {n.line:g}+")
            elif abs(n.ask_c - float(o[1])) >= pc.price_move_c:
                tags.append(f"{_stat_label(s)} ask {float(o[1]):.0f} -> {n.ask_c:.0f}c")
        elif n is None and o is not None:
            tags.append(f"{_stat_label(s)} price gone")
        elif n is not None and o is None:
            tags.append(f"{_stat_label(s)} price appeared")
    return tags


# ------------------------------------------------------------------------------ rendering


def _status_text(r: PRow) -> str:
    if r.status == "out":
        return f"OUT ({r.reason})" if r.reason else "OUT"
    s = "not on report" if r.status is None else r.status + (f" ({r.reason})" if r.reason else "")
    if r.p_play is not None:
        s += f", plays {100 * r.p_play:.0f}%"
    return s


def player_line(r: PRow, tags: list[str] | None) -> str:
    """ONE line: projection, status and chance to play, lines with EV, and the change."""
    bits = [f"**{r.name}**"]
    if r.status == "out":
        bits.append(_status_text(r))
        bits.append("no projection")
    else:
        if r.minutes is not None:
            bits.append(f"{r.minutes:.0f} min")
        bits.append(
            "/".join("-" if r.means.get(s) is None else f"{r.means[s]:.1f}" for s in STATS)
            if r.means
            else "no projection"
        )
        bits.append(_status_text(r))
    if r.starter is True:
        bits.append("starter")
    elif r.starter is False:
        bits.append("bench")
    if r.lines:
        priced = []
        for s in STATS:
            ln = r.lines.get(s)
            if ln is None:
                continue
            p = "model n/a" if ln.p_model is None else f"model {100 * ln.p_model:.0f}%"
            priced.append(f"{ln.line:g}+ {_stat_label(s)} @{ln.ask_c:.0f}c {p} EV {ln.ev_c:+.1f}c")
        bits.append("; ".join(priced))
    if r.unders:
        us = [
            f"under {ln.line:g} {_stat_label(s)} (Kalshi No) @{ln.ask_c:.0f}c EV {ln.ev_c:+.1f}c"
            for s in STATS
            if (ln := r.unders.get(s)) is not None
        ]
        bits.append("; ".join(us))
    if not r.lines and not r.unders and r.status != "out" and not r.unmeasured:
        bits.append("no Kalshi price")
    if r.unmeasured:
        names = "/".join(_stat_label(s) for s in r.unmeasured)
        bits.append(f"{names}: priced, no usable sharp line, so no EV")
    if tags is not None:
        bits.append("change: " + (", ".join(tags) if tags else "none"))
    return " · ".join(bits)


def _clock(ts: datetime | None, style: T.Style) -> str:
    return "n/a" if ts is None else T.clock(ts, style)


def build_message(
    gd: GameData, team: str, minute: int, now: datetime, prev: dict[str, Any] | None,
    pc: PcpConfig, style: T.Style, preview: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:  # fmt: skip
    """(embed, snapshot of what it showed). The embed is inside Discord's limits."""
    g = gd.game
    rows, report = gd.rows.get(team, []), gd.report.get(team, {})
    shown = listed(rows, report, prev, pc)
    lines: list[str] = []
    for r in shown:
        lines.append(player_line(r, None if prev is None else diff_tags(r, prev, pc)))
    head = [
        f"⏳ **T-{minute}** player check · tips {T.clock(g.tipoff, style)}"
        + (" · PREVIEW, not sent" if preview else ""),
        f"Forecasts as of {_clock(gd.forecast_asof, style)}; injury report as of "
        f"{_clock(gd.report_asof, style)}; built {_clock(now, style)}."
        + _lineup_text(gd, team, minute, pc, style),
        (
            "First look: no earlier checkpoint was posted for this game."
            if prev is None
            else f"Change since the T-{prev['minute']} post ({_clock(_ts(prev), style)})."
        ),
        "Per player: minutes, then projected pts / reb / ast / 3PM if he plays; status and the "
        "model's chance to play; best-EV line per stat (ask, model chance of the over, EV per $1 "
        "contract in cents after fee and spread). Unders are Kalshi's No; "
        + (
            "priced on the worst case (a no-show is a No loss; Kalshi's rule is unverified), "
            "so a positive EV holds under either rule. Paper, no alert."
            if gd.under_worst_case
            else "a player who sits before tip settles at Kalshi's last fair price (terms)."
        ),
    ]
    head_text = "\n".join(head)
    body: list[str] = []
    used = len(head_text) + 2
    for i, ln in enumerate(lines):
        extra = len(shown) - i
        if used + len(ln) + 1 + (len(f"+{extra} more players") + 1) > DESC_BUDGET:
            body.append(f"+{extra} more players")
            break
        body.append(ln)
        used += len(ln) + 1
    if not lines:
        body.append("No player clears the list rule yet (no forecast with enough chance to play).")
    e = {
        "title": f"{team} players · T-{minute} · {g.away_abbr} @ {g.home_abbr} "
        f"{T.clock(g.tipoff, style)}",
        "description": head_text + "\n\n" + "\n".join(body),
        "color": color(team),
        "fields": [],
        "footer": {"text": DISCLAIMER_PCP},
    }
    return enforce(e), snapshot(shown, report)


def _ts(rec: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(rec.get("ts")))
    except ValueError:
        return None


def _lineup_text(gd: GameData, team: str, minute: int, pc: PcpConfig, style: T.Style) -> str:
    if minute > pc.starters_from_min:
        return " Starters are not flagged this early."
    note = gd.lineup_note.get(team)
    if note is None:
        return " Lineups: no snapshot yet, so no starter is flagged."
    return (
        f" Lineups: snapshot {_clock(gd.lineup_asof, style)} ({note}); "
        "starter / bench flagged only if confirmed."
    )


# ------------------------------------------------------------------------------ the pass


def run_player_checkpoints(
    rt: Any, d: Any, client: CardClient, wh: TeamWebhooks, slate: Any, now: datetime,
    style: T.Style,
) -> dict[str, Any]:  # fmt: skip
    """The pass. Contained: nothing here raises into the card pass. Uses the card pass's own
    client and budgets (``rate_budget_s``, ``tick_budget_s``); a 429 or an exhausted budget
    DEFERS (nothing recorded, so the next tick retries inside the window)."""
    cfg: JarvisConfig = d.cfg
    pc = pcp_config(cfg)
    rep: dict[str, Any] = {}
    if not pc.enabled:
        return {"skipped": "disabled"}
    if not wh.urls:
        return {"skipped": "no team webhooks configured"}
    state = PcpState(pc.state)
    write = bool(d.write)
    counts = {"posted": 0, "skipped_late": 0, "deferred": 0, "failed": 0, "lost_id": 0}
    errors: list[str] = []
    live = _live_status(cfg, slate)
    for g in d.st.games:
        if g.tipoff is None:
            continue  # a real tip time or nothing: never a proxy
        teams = [t for t in (g.away_abbr, g.home_abbr) if t in wh.urls]
        if not teams:
            continue
        started = live.get((g.away_abbr, g.home_abbr), "STATUS_SCHEDULED") != "STATUS_SCHEDULED"
        due: list[int] = []
        for m in pc.minutes:
            for t in teams:
                rec = state.get(slate, g.game_id, t, m)
                ws = window_state(
                    now, g.tipoff, m, pc.tolerance[m], pc.after_tip_guard_min, rec is not None
                )
                if started and ws in ("due", "wait", "late"):
                    ws = "over"  # the scoreboard says it has begun: nothing posts
                if ws == "late" or (
                    ws == "over" and (now - g.tipoff) <= timedelta(hours=LATE_LOG_HOURS)
                ):
                    if write:
                        state.write(_rec(slate, g, t, m, "skipped_late", now))
                    counts["skipped_late"] += 1
                    log.warning(
                        "player checkpoint T-%d %s %s missed its window; skipped (never late)",
                        m, g.matchup, t,
                    )  # fmt: skip
                elif ws == "due" and m not in due:
                    due.append(m)
        for m in due:
            _post_checkpoint(
                rt, d, client, wh, state, slate, g, m, now, style, pc, counts, errors, write
            )
    rep.update({k: v for k, v in counts.items() if v})
    rep["posted"] = counts["posted"]
    if errors:
        rep["errors"] = errors
    if state.skipped:
        rep["state_skipped"] = state.skipped
    return rep


def _live_status(cfg: JarvisConfig, slate: Any) -> dict[tuple[str, str], str]:
    try:
        from nba.jarvis.live import latest_scoreboard

        return {(x.away, x.home): x.status for x in latest_scoreboard(cfg, slate)}
    except Exception as exc:  # noqa: BLE001 - the scoreboard is an extra refusal, never required
        log.warning("player checkpoints: scoreboard unreadable (%s)", type(exc).__name__)
        return {}


def _rec(slate: Any, g: Game, team: str, minute: int, kind: str, now: datetime) -> dict[str, Any]:
    return {
        "slate": str(slate), "game_id": g.game_id, "team": team, "minute": minute, "kind": kind,
        "ts": now.isoformat(timespec="seconds"),
        "tip": None if g.tipoff is None else g.tipoff.isoformat(timespec="seconds"),
    }  # fmt: skip


def _post_checkpoint(
    rt: Any, d: Any, client: CardClient, wh: TeamWebhooks, state: PcpState, slate: Any, g: Game,
    minute: int, now: datetime, style: T.Style, pc: PcpConfig, counts: dict[str, int],
    errors: list[str], write: bool,
) -> None:  # fmt: skip
    teams = [
        t
        for t in (g.away_abbr, g.home_abbr)
        if t in wh.urls and state.get(slate, g.game_id, t, minute) is None
    ]
    if not teams:
        return
    if client.gate.stopped():  # one budget for the whole pass: the rest wait for the next tick
        counts["deferred"] += len(teams)
        return
    try:
        store = read_store(d.cfg, [g.game_id], now)
        gd = gather_game(d.cfg, d.st, rt.kb, g, now, minute, pc, store, d.hold)
    except Exception as exc:  # noqa: BLE001 - one game's data never costs the tick
        log.warning("player checkpoint data for %s failed (%s)", g.matchup, type(exc).__name__)
        errors.append(f"{g.matchup}:{type(exc).__name__}")
        return
    for t in teams:
        if client.gate.stopped():
            counts["deferred"] += 1
            continue
        try:
            prev = state.previous_posted(slate, g.game_id, t, minute)
            embed, snap = build_message(gd, t, minute, now, prev, pc, style)
            if not write:
                counts["would_post"] = counts.get("would_post", 0) + 1
                continue
            real = _real_now(rt, now)
            if g.tipoff is None or (
                window_state(
                    real, g.tipoff, minute, pc.tolerance[minute], pc.after_tip_guard_min, False
                )
                != "due"
            ):  # the tick clock is stale by the time the card pass runs: re-check
                _safe_write(state, _rec(slate, g, t, minute, "skipped_late", real), counts)
                counts["skipped_late"] += 1
                continue
            res = client.post(wh.urls[t], {"embeds": [embed]})
        except Exception as exc:  # noqa: BLE001
            log.warning("player checkpoint %s %s failed (%s)", g.matchup, t, type(exc).__name__)
            log.debug("detail: %s", wh.redact(repr(exc)))
            errors.append(f"{g.matchup}/{t}:{type(exc).__name__}")
            continue
        if res.ok:
            rec = _rec(slate, g, t, minute, "posted", now)
            rec.update(
                message_id=res.message_id or "",
                as_of={
                    "forecasts": _iso(gd.forecast_asof),
                    "report": _iso(gd.report_asof),
                    "lineups": _iso(gd.lineup_asof),
                },
                snap=snap,
            )
            _safe_write(state, rec, counts)
            counts["posted" if res.message_id else "lost_id"] += 1
        elif res.status == "deferred" and _maybe_delivered(res):
            # Discord may have accepted it (response lost): at most once for an informational post
            rec = _rec(slate, g, t, minute, "posted", _real_now(rt, now))
            rec.update(message_id="", snap=snap, note=str(res.note or res.code))
            _safe_write(state, rec, counts)
            counts["lost_id"] += 1
        elif res.status == "deferred":
            counts["deferred"] += 1
        else:
            counts["failed"] += 1


def _real_now(rt: Any, fallback: datetime) -> datetime:
    clock = getattr(rt, "clock", None)
    try:
        return clock() if callable(clock) else fallback
    except Exception:  # noqa: BLE001
        return fallback


_NOT_SENT = ("ConnectError", "ConnectTimeout", "PoolTimeout", "UnsupportedProtocol", "InvalidURL")


def _maybe_delivered(res: Any) -> bool:
    """A 5xx or a transport error after the request may have left (read timeout, dropped
    connection) can hide an accepted post; a connect failure or a budget/429 deferral cannot."""
    note = str(getattr(res, "note", "") or "")
    code = getattr(res, "code", None)
    if code is not None and int(code) >= 500:
        return True
    return note.startswith("transport ") and not any(x in note for x in _NOT_SENT)


def _safe_write(state: PcpState, rec: dict[str, Any], counts: dict[str, int]) -> None:
    try:
        state.write(rec)
    except Exception as exc:  # noqa: BLE001 - a state write never costs the tick
        log.warning("player checkpoint state write failed (%s)", type(exc).__name__)
        counts["state_write_failed"] = counts.get("state_write_failed", 0) + 1


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat(timespec="seconds")


# ------------------------------------------------------------------------------ preview


def render_players(
    rt: Any, st: SlateState, slate: Any, now: datetime, minute: int, game: str | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    """Messages for every game and both teams as of ``now``, ignoring the window; sends nothing,
    writes nothing (the posted-checkpoint state is only read, for the diff)."""
    from nba.jarvis.cards import run as R

    cfg = rt.cfg
    pc = pcp_config(cfg)
    style = R.style_of(cfg)
    state = PcpState(pc.state)
    hold = R._make_hold(rt, now, slate)
    out: list[dict[str, Any]] = []
    for g in st.games:
        if game and g.matchup != game:
            continue
        store = read_store(cfg, [g.game_id], now)
        gd = gather_game(cfg, st, rt.kb, g, now, minute, pc, store, hold)
        embeds = {}
        for t in (g.away_abbr, g.home_abbr):
            prev = state.previous_posted(slate, g.game_id, t, minute)
            embeds[t] = build_message(gd, t, minute, now, prev, pc, style, preview=True)[0]
        out.append(
            {
                "game": g.matchup, "phase": f"players T-{minute}",
                "channels": [g.away_abbr, g.home_abbr], "embeds": embeds,
            }
        )  # fmt: skip
    return out
