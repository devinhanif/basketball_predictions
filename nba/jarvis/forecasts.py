"""Our stored forecasts as a read-only view (never a refit): win probability and prop P(stat >= N).

Wraps the parlay engine's slate loader (``nba.parlay.assistant.toolbox.Toolbox``), which attaches
``nba.duckdb`` and the Kalshi store read-only. A failure to load is reported once and every lookup
then returns None; JARVIS keeps working on prices alone and says so.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from nba.jarvis.config import PARLAY_PATH, JarvisConfig
from nba.jarvis.models import Quote
from nba.parlay.joint import leg_marginal
from nba.parlay.legs import Leg


def stored_p_play(table: dict[int, float] | None, q: Quote) -> float | None:
    """The one lookup of a stored P(plays) for a prop quote: None when there is no usable number
    (not a player quote, no row, not finite, outside (0, 1]); never a default of 1.0, because a
    missing forecast must cap the prop, not price it as a sure starter."""
    if table is None or q.kind != "prop" or q.player_id is None:
        return None
    v = table.get(int(q.player_id))
    if v is None or not math.isfinite(v) or not 0.0 < v <= 1.0:
        return None
    return float(v)


class PPlayView:
    """A model view plus an explicit P(plays) table (the player checkpoints pass the table read as
    of their own clock). Same ``stored_p_play`` lookup as ``ModelView.p_play``: one path."""

    def __init__(self, base: Any, pplay: dict[int, float]) -> None:
        self._base, self._pplay = base, pplay

    def p_yes(self, q: Any) -> float | None:
        return None if self._base is None else self._base.p_yes(q)

    def p_play(self, q: Any) -> float | None:
        return stored_p_play(self._pplay, q)


class ModelView:
    def __init__(self, cfg: JarvisConfig, slate: date, toolbox: Any | None = None) -> None:
        self.cfg = cfg
        self.slate = slate
        self._tb = toolbox
        self.error: str | None = None
        self._sl: Any = None

    def _slate(self) -> Any | None:
        if self._sl is not None or self.error is not None:
            return self._sl
        try:
            tb = self._tb or self._build()
            self._tb = tb
            self._sl = tb._need_games(self.slate)
        except Exception as exc:  # engine files missing, DB locked, no forward rows yet
            self.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return self._sl

    def _build(self) -> Any:
        from nba.parlay.assistant.names import NameResolver
        from nba.parlay.assistant.toolbox import EnginePaths, Toolbox
        from nba.parlay.config import load_config

        root = self.cfg.root
        paths = EnginePaths(
            self.cfg.paths["nba_db"],
            self.cfg.paths["kalshi_db"],
            root / "data" / "parlay" / "paper_trades.duckdb",
            root / "data" / "parlay" / "joint_model.json",
        )
        return Toolbox(
            paths,
            load_config(PARLAY_PATH),
            NameResolver.default(),
            default_date=self.slate,
        )

    @property
    def available(self) -> bool:
        return self._slate() is not None

    def p_yes(self, q: Quote) -> float | None:
        sl = self._slate()
        if sl is None:
            return None
        ctx = sl.info.ctxs.get(q.game_id)
        if ctx is None:
            return None
        if q.kind == "prop" and q.player_id and (q.game_id, q.player_id) in sl.info.out_on_report:
            return None  # ruled OUT on a report known now: the stored (stale) forecast is not used
        try:
            if q.kind == "game_win" and q.team_id is not None:
                return float(leg_marginal(Leg(q.game_id, 0, "win", 0.0, "yes", q.team_id), ctx))
            if q.kind == "prop" and q.player_id and q.stat and q.threshold is not None:
                tid = ctx.team_of.get(q.player_id)
                if tid is None:
                    return None
                p = leg_marginal(Leg(q.game_id, q.player_id, q.stat, q.threshold, "yes", tid), ctx)
                return float(ctx.p_play.get(q.player_id, 1.0) * p)  # DNP counts as a miss
        except (KeyError, ValueError):
            return None
        return None

    def p_play(self, q: Quote) -> float | None:
        """Stored forward P(plays) for the quote's (game, player), from the same forecast rows
        ``p_yes`` reads. None (so the caller caps the prop) when there is no row, the player is
        ruled OUT on a report known now, or any of his rows was made after the game's real tip
        (known-at: nothing made after tip is used). The rows are those present when the view loaded
        (the view is rebuilt every run), the same clock as ``p_yes``."""
        sl = self._slate()
        if sl is None or q.kind != "prop" or q.player_id is None:
            return None
        ctx = sl.info.ctxs.get(q.game_id)
        if ctx is None or q.player_id not in ctx.team_of:
            return None
        if (q.game_id, q.player_id) in sl.info.out_on_report:
            return None
        # the slate builder fills 1.0 when a row has no p_play: only a row that CARRIES one counts
        raw = getattr(sl, "props_raw", {})
        if not any(
            gid == q.game_id and pid == q.player_id and "p_play" in (d or {})
            for (gid, pid, _stat), d in raw.items()
        ):
            return None
        tip = getattr(sl.info, "tipoff", {}).get(q.game_id)
        if tip is not None:
            try:
                for (gid, pid, _stat), made in getattr(sl, "made_at", {}).items():
                    if gid == q.game_id and pid == q.player_id and _naive(made) > tip:
                        return None
            except (
                TypeError,
                ValueError,
            ):  # an unparseable made_at caps the prop; never fails the tick
                return None
        return stored_p_play(ctx.p_play, q)


def _naive(made: Any) -> datetime:
    d = made if isinstance(made, datetime) else datetime.fromisoformat(str(made))
    return d.replace(tzinfo=None)
