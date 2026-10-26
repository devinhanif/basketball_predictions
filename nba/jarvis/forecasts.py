"""Our stored forecasts as a read-only view (never a refit): win probability and prop P(stat >= N).

Wraps the parlay engine's slate loader (``nba.parlay.assistant.toolbox.Toolbox``), which attaches
``nba.duckdb`` and the Kalshi store read-only. A failure to load is reported once and every lookup
then returns None; JARVIS keeps working on prices alone and says so.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from nba.jarvis.config import PARLAY_PATH, JarvisConfig
from nba.jarvis.models import Quote
from nba.parlay.joint import leg_marginal
from nba.parlay.legs import Leg


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
