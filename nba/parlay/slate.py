"""Build per-game contexts for a slate from ``forward_predictions`` (read-only).

Win probability: production ``rung0_injury_elo`` (fallback ``rung0_mov_elo``, flagged).
Props: production ``props_context_residual`` (conditional-on-playing quantile grid + p_play).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import duckdb

from nba.parlay.game_model import GameModelParams, slate_total_feature
from nba.parlay.joint import GameCtx
from nba.parlay.qdist import QuantileGridDist

WIN_MODELS = ("rung0_injury_elo", "rung0_mov_elo")
PROPS_MODEL = "props_context_residual"
ET = ZoneInfo("America/New_York")


@dataclass
class SlateInfo:
    ctxs: dict[str, GameCtx] = field(default_factory=dict)
    games_by_key: dict[tuple[date, int, int], str] = field(default_factory=dict)
    win_model: dict[str, str] = field(default_factory=dict)
    tipoff: dict[str, datetime] = field(default_factory=dict)  # naive UTC, per slate game
    notes: list[str] = field(default_factory=list)


def load_slate(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    params: GameModelParams,
    prefix: str = "",
    as_of: datetime | None = None,
) -> SlateInfo:
    """``prefix`` is the attached-catalog prefix (e.g. ``'nba.'``) for the forward tables.

    ``as_of`` (naive UTC) drops forward rows made after it, so a replayed or back-dated run sees
    only what existed at that clock (default: no filter, the live behaviour)."""
    rows = con.execute(
        f"SELECT game_id, tipoff, model_name, target, player_id, prediction, made_at "
        f"FROM {prefix}forward_predictions ORDER BY made_at"
    ).fetchall()
    win: dict[tuple[str, str], dict[str, object]] = {}
    props: dict[tuple[str, int, str], dict[str, object]] = {}
    tips: dict[str, datetime] = {}
    for gid, tipoff, model, target, pid, pred, _made in rows:
        if as_of is not None and _made > as_of:
            continue
        et = tipoff.replace(tzinfo=UTC).astimezone(ET).date()
        if et != slate:
            continue
        tips[str(gid)] = tipoff
        d = json.loads(pred) if isinstance(pred, str) else pred
        if target == "win_prob_home" and model in WIN_MODELS:
            win[(str(gid), model)] = d  # later made_at overwrites earlier
        elif model == PROPS_MODEL and d.get("q_grid"):
            props[(str(gid), int(pid), str(target))] = d
    info = SlateInfo()
    gids = sorted({g for g, _ in win})
    if not gids:
        info.notes.append(f"no win-probability predictions for {slate} in forward_predictions")
        return info
    team_df = con.execute(
        f"SELECT s.player_id, arg_max(s.team_id, g.game_date) AS team_id "
        f"FROM {prefix}player_game_stats s JOIN {prefix}games g USING (game_id) "
        f"WHERE g.game_date < ? GROUP BY s.player_id",
        [slate],
    ).fetchall()
    latest_team = {int(p): int(t) for p, t in team_df}
    hist = con.execute(
        f"SELECT game_id, game_date, home_team, away_team, home_pts, away_pts "
        f"FROM {prefix}games WHERE game_date < ? AND home_pts IS NOT NULL",
        [slate],
    ).pl()
    for gid in gids:
        w, model = None, ""
        for m in WIN_MODELS:
            if (gid, m) in win:
                w, model = win[(gid, m)], m
                break
        assert w is not None
        if model != WIN_MODELS[0]:
            info.notes.append(f"{gid}: injury-Elo missing, used {model}")
        home, away = int(w["home_team"]), int(w["away_team"])  # type: ignore[call-overload]
        p = float(w["p_home"])  # type: ignore[arg-type]
        feat = slate_total_feature(hist, params, home, away, slate)
        dists: dict[tuple[int, str], QuantileGridDist] = {}
        team_of: dict[int, int] = {}
        p_play: dict[int, float] = {}
        for (g2, pid, stat), d in props.items():
            if g2 != gid:
                continue
            t = latest_team.get(pid)
            if t not in (home, away):
                continue
            assert t is not None
            dists[(pid, stat)] = QuantileGridDist.from_grid(d["q_grid"])  # type: ignore[arg-type]
            team_of[pid] = t
            p_play[pid] = float(d.get("p_play", 1.0))  # type: ignore[arg-type]
        info.ctxs[gid] = GameCtx(
            gid,
            home,
            away,
            params.margin_mu(p),
            params.margin_sd,
            params.total_mu(feat),
            params.total_sd,
            dists,
            team_of,
            p_play,
        )
        info.games_by_key[(slate, away, home)] = gid
        info.win_model[gid] = model
        info.tipoff[gid] = tips[gid]
    return info
