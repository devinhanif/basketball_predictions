# ruff: noqa: E501
"""Halftime scorer: model P and glm_e P for every on-grid Kalshi player-prop Yes quote at the half.

Reads the live capture (``nba.markets.halftime_entry``), the stored artifact
(``nba.markets.halftime_model``) and a recency table from ``nba.duckdb`` opened READ ONLY, and appends
rows to its own append-only store (``nba.markets.halftime_store``). Paper and display only: no order,
no credential, no network; it reads no outcome and settles nothing.

    uv run python -m nba.markets.halftime_scorer replay --night data/raw/live_2026-10-26 \\
        --artifact data/models/halftime_ingame/<date>/artifact.json --nba-db nba.duckdb \\
        --min-game-date none

``--min-game-date`` is required: a date limits the recency window, ``none`` allows prior seasons (the
T508 window; holdout-log row 105a6dc). ``replay`` stamps each (game, series) at the earliest instant all
its inputs existed (``max(quote, state)`` received_at) into an in-memory store unless ``--db`` is given;
its rows are mode 'replay', never in the prereg window, and it is refused into the live store path. The JARVIS tick
uses ``score_live`` with the clock instead.

Fee: the verified ``FeeModel`` object from configs/parlay.yaml, one-contract taker order, rounded up
to the cent once; refused unless it matches the pin in configs/jarvis.yaml ``halftime_overs.fee_pin``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.markets import halftime_entry as he
from nba.markets.halftime_model import STATS, Artifact, load_artifact
from nba.markets.halftime_store import HalftimeStore, SecondHalfStartedError
from nba.parlay.config import FeeModel
from nba.parlay.ev import order_fee

MIN_PRIOR = 5  # T508: pregame mean needs at least 5 earlier games
FEED_MAX_AGE_S = 180.0  # live: a scoreboard older than this cannot prove the half is still on
ESPN_TO_NBA = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


class FeeNotVerifiedError(ValueError):
    pass


def check_fee(fee: FeeModel, pin: dict[str, Any], series: tuple[str, ...]) -> None:
    """Refuse an unverified or drifted fee object (configs/parlay.yaml vs the pin in jarvis.yaml)."""
    if not fee.verified:
        raise FeeNotVerifiedError("fee_model.verified is not true")
    if fee.formula != pin["formula"] or abs(fee.coefficient - float(pin["coefficient"])) > 1e-12:
        raise FeeNotVerifiedError(
            f"fee {fee.formula} {fee.coefficient} != pinned {pin['formula']} {pin['coefficient']}"
        )
    for s in series:
        if abs(fee.multiplier(s) - float(pin["multiplier"])) > 1e-12:
            raise FeeNotVerifiedError(f"fee multiplier for {s} is {fee.multiplier(s)}")


@dataclass(frozen=True)
class Recency:
    player_id: int
    n_prior: int
    means: dict[str, float]
    rec_minutes: float


class RecencyProvider:
    """Last-10-played-games means as of a date, from ``nba.duckdb`` opened read-only.

    As-of rule: ``game_date < slate_date``, regular season, minutes > 0 (training's definition). Name
    to id goes through the reviewed resolver restricted to players whose latest game was for the ESPN
    team; an ambiguous or unknown name is NOT scored (counted, listed), never guessed.

    DISCLOSURE (open choice C3 of the draft rule): in October-November the window includes 2025-26
    games, i.e. season-2025 final lines are read here as FEATURES of a forward forecast. That is not an
    evaluation (no label, no score), but it is a read of the frozen holdout season; ``min_game_date``
    restricts the window (e.g. ``2026-10-01`` = 2026-27 games only, which scores almost nothing before
    early November).
    """

    def __init__(
        self,
        nba_db: Path,
        slate_date: date,
        resolver: Any | None = None,
        *,
        min_game_date: date | None,
    ) -> None:
        self.slate_date = slate_date
        con = duckdb.connect(str(nba_db), read_only=True)
        try:
            rows = con.execute(
                """
                WITH g AS (
                    SELECT p.player_id, p.team_id, p.minutes, p.pts, p.reb, p.ast, p.fg3m,
                           row_number() OVER (PARTITION BY p.player_id
                                              ORDER BY gm.game_date DESC, p.game_id DESC) AS rn
                    FROM player_game_stats_played p JOIN games gm USING (game_id)
                    WHERE gm.game_date < ? AND gm.game_date >= ?
                      AND p.game_id LIKE '002%' AND p.minutes > 0
                )
                SELECT player_id, any_value(team_id) FILTER (rn = 1), count(*),
                       avg(pts) FILTER (rn <= 10), avg(reb) FILTER (rn <= 10),
                       avg(ast) FILTER (rn <= 10), avg(fg3m) FILTER (rn <= 10),
                       avg(minutes) FILTER (rn <= 10)
                FROM g GROUP BY player_id
                """,
                [slate_date, min_game_date or date(1900, 1, 1)],
            ).fetchall()
        finally:
            con.close()
        self._by_id = {int(r[0]): (int(r[1]), int(r[2]), [float(x) for x in r[3:8]]) for r in rows}
        self._by_team: dict[int, set[int]] = {}
        for pid, (tid, _n, _m) in self._by_id.items():
            self._by_team.setdefault(tid, set()).add(pid)
        self._resolver = resolver
        self._team_ids: dict[str, int] | None = None
        self.unresolved: Counter[str] = Counter()

    def _team_id(self, espn_abbr: str) -> int | None:
        if self._team_ids is None:
            from nba_api.stats.static import teams

            self._team_ids = {t["abbreviation"]: int(t["id"]) for t in teams.get_teams()}
        return self._team_ids.get(ESPN_TO_NBA.get(espn_abbr, espn_abbr))

    def lookup(self, espn_name: str, espn_team: str) -> Recency | None:
        if self._resolver is None:
            from nba.parlay.assistant.names import NameResolver

            self._resolver = NameResolver.default()
        tid = self._team_id(espn_team)
        cands = self._by_team.get(tid, set()) if tid is not None else set()
        try:
            pid = self._resolver.resolve(espn_name, cands)
        except Exception:  # PlayerResolutionError: unknown / ambiguous / not on the team
            self.unresolved[f"{espn_name} ({espn_team})"] += 1
            return None
        _t, n, v = self._by_id[pid]
        return Recency(pid, n, dict(zip(STATS, v[:4], strict=True)), v[4])


@dataclass
class ScoreReport:
    rows: list[dict[str, Any]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    refused: list[str] = field(default_factory=list)
    stored: int = 0


def score_snapshot(
    snap: he.Snapshot,
    art: Artifact,
    rec: RecencyProvider,
    fee: FeeModel,
    made_at: datetime,
    slate: date,
    counts: Counter[str],
    min_prior: int = MIN_PRIOR,
) -> list[dict[str, Any]]:
    """Score every candidate of one snapshot. A candidate with no usable history is counted, not scored."""
    usable: list[tuple[he.Candidate, Recency]] = []
    for c in snap.candidates:
        r = rec.lookup(c.espn_name, c.team)
        if r is None:
            counts["x_player_unresolved"] += 1
        elif r.n_prior < min_prior:
            counts["x_insufficient_history"] += 1
        else:
            usable.append((c, r))
    out: list[dict[str, Any]] = []
    for stat in STATS:
        grp = [(c, r) for c, r in usable if c.stat == stat]
        if not grp:
            continue
        cols = {
            "e": np.full(len(grp), 24.0),
            "minutes": np.array([c.minutes for c, _ in grp], float),
            "starter": np.array([c.starter for c, _ in grp], float),
            "fouls": np.array([c.fouls for c, _ in grp], float),
            "margin": np.array([c.margin for c, _ in grp], float),
            "cur": np.array([c.cur for c, _ in grp], float),
            "line": np.array([c.k for c, _ in grp], float),
            "pregame_mean": np.array([r.means[stat] for _, r in grp], float),
            "rec_minutes": np.array([r.rec_minutes for _, r in grp], float),
        }
        p_m, p_g = art.score(stat, cols)
        for i, (c, r) in enumerate(grp):
            fee_c = round(100 * order_fee(c.ask_c / 100.0, fee, 1, series=c.series))
            ask = c.ask_c / 100.0
            out.append(
                {
                    "made_at": made_at,
                    "slate_date": slate,
                    "event_id": snap.event_id,
                    "game": snap.game,
                    "series": c.series,
                    "stat": stat,
                    "ticker": c.ticker,
                    "player_name": c.player_name,
                    "player_id": r.player_id,
                    "team": c.team,
                    "k": c.k,
                    "ask_c": c.ask_c,
                    "bid_c": c.bid_c,
                    "ask_size": c.ask_size,
                    "depth_5usd": c.ask_size * ask >= 5.0,
                    "fee_c": fee_c,
                    "fee_model": fee.name,
                    "fee_coefficient": fee.coefficient,
                    "fee_multiplier": fee.multiplier(c.series),
                    "p_model": float(p_m[i]),
                    "p_glm_e": float(p_g[i]),
                    "edge_model": float(p_m[i]) - ask - fee_c / 100.0,
                    "edge_glm_e": float(p_g[i]) - ask - fee_c / 100.0,
                    "cur": c.cur,
                    "minutes": c.minutes,
                    "starter": c.starter,
                    "fouls": c.fouls,
                    "margin": c.margin,
                    "pregame_mean": r.means[stat],
                    "rec_minutes": r.rec_minutes,
                    "n_prior": r.n_prior,
                    "t_ht": snap.t_ht,
                    "t_star": snap.t_star,
                    "quote_received_at": snap.quote_received_at,
                    "quote_file": snap.quote_file,
                    "state_received_at": snap.state_received_at,
                    "state_file": snap.state_file,
                    "artifact_digest": art.digest,
                }
            )
    return out


def score_night(
    night: Path,
    art: Artifact,
    rec: RecencyProvider,
    fee: FeeModel,
    store: HalftimeStore,
    slate: date,
    now: datetime | None,
    fee_pin: dict[str, Any],
    event_ids: set[str] | None = None,
    only_games: set[str] | None = None,
    min_prior: int = MIN_PRIOR,
) -> ScoreReport:
    """Score and store the halftime snapshots of one capture night.

    ``now`` given (live): the capture is read as of ``now`` and every row is stamped ``now``; a stale
    feed or a started second half refuses. ``now`` None (replay): each snapshot is stamped at the
    earliest instant all its inputs existed. ``only_games`` ("AWAY@HOME") limits the pass.
    """
    check_fee(fee, fee_pin, tuple(he.SERIES))
    if (now is None) != (store.mode == "replay"):
        raise ValueError("a live store needs the clock (now); a replay store must not get one")
    rep = ScoreReport()
    as_of = None if now is None else he.utc(now)
    reader = he.NightReader(night, as_of)
    if as_of is not None:
        last = _last_scoreboard(night, as_of)
        if last is None or (as_of - last).total_seconds() > FEED_MAX_AGE_S:
            rep.counts["refused_feed_stale"] += 1
            return rep
    for eid, ht in sorted(reader.hts.items()):
        if event_ids is not None and eid not in event_ids:
            continue
        if only_games is not None and ht.game not in only_games:
            continue
        rep.counts["games_halftime_seen"] += 1
        for series in he.SERIES:
            if store.has(eid, series):
                continue
            c: Counter[str] = Counter()
            snap = reader.snapshot(ht, series, c)
            rep.counts.update(c)
            for reason, n in c.items():
                store.note_skip(as_of or ht.ht_first, eid, series, reason, n)
            if snap is None:
                continue
            made = as_of if as_of is not None else snap.known_at
            if ht.q3_first is not None and ht.q3_first <= made:
                rep.refused.append(f"{ht.game} {series}")
                rep.counts["refused_second_half_started"] += 1
                if snap.candidates:
                    with contextlib.suppress(SecondHalfStartedError):
                        store.append(
                            [{"event_id": eid, "series": series, "game": ht.game, "made_at": made}],
                            made,
                            True,
                        )
                continue
            for k, v in snap.excluded.items():
                if not k.startswith("unmatched_names:"):
                    rep.counts[k] += v
                else:
                    rep.counts["unmatched_names"] += v
            sc_counts: Counter[str] = Counter()
            rows = score_snapshot(snap, art, rec, fee, made, slate, sc_counts, min_prior)
            rep.counts.update(sc_counts)
            for reason, n in [*sc_counts.items(), *snap.excluded.items()]:
                if not reason.startswith("unmatched_names:") and reason != "scored_candidates":
                    store.note_skip(made, eid, series, reason, n)
            rep.rows.extend(rows)
            rep.stored += store.append(rows, made, False, min_prior)
    return rep


def _last_scoreboard(night: Path, as_of: datetime) -> datetime | None:
    man = he.load_manifest(night, as_of)
    sb = man.get("espn_scoreboard", [])
    return sb[-1][0] if sb else None


def _min_game_date(v: str) -> date | None:
    """ISO date, or the word ``none``: an explicit choice to read prior-season games as features."""
    return None if v.strip().lower() in ("none", "null") else date.fromisoformat(v)


def load_fee_pin() -> dict[str, Any]:
    """The fee pin from configs/jarvis.yaml ``halftime_overs.fee_pin`` (one source for live and replay)."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "configs" / "jarvis.yaml").read_text())
    return dict(raw["halftime_overs"]["fee_pin"])


def replay_main(argv: list[str] | None = None) -> int:
    from nba.parlay.config import load_config

    ap = argparse.ArgumentParser(prog="halftime_scorer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="score a stored capture night (no network, no send)")
    r.add_argument("--night", type=Path, required=True)
    r.add_argument("--artifact", type=Path, required=True)
    r.add_argument("--nba-db", type=Path, required=True)
    r.add_argument("--db", default=":memory:")
    r.add_argument("--parlay-config", type=Path, default=Path("configs/parlay.yaml"))
    r.add_argument("--top", type=int, default=5)
    r.add_argument(
        "--min-game-date",
        type=_min_game_date,
        required=True,
        help="first game date of the recency window (YYYY-MM-DD), or 'none' to allow prior seasons "
        "(season-2025 rows as FEATURES; holdout-log row 105a6dc)",
    )
    r.add_argument(
        "--min-prior",
        type=int,
        default=MIN_PRIOR,
        help="T508 needs 5. A smaller value is a WIRING DEMO only: pregame means outside the model's support",
    )
    a = ap.parse_args(argv)
    slate = date.fromisoformat(a.night.name.removeprefix("live_"))
    pin = load_fee_pin()
    cfg = load_config(a.parlay_config)
    art = load_artifact(a.artifact)
    store = HalftimeStore.open(a.db, mode="replay")
    rec = RecencyProvider(a.nba_db, slate, min_game_date=a.min_game_date)
    rep = score_night(a.night, art, rec, cfg.fee, store, slate, None, pin, min_prior=a.min_prior)
    out = {
        "night": a.night.name,
        "min_prior": a.min_prior,
        "valid_for_f": a.min_prior >= MIN_PRIOR,
        "counts": dict(rep.counts),
        "stored": rep.stored,
        "refused": rep.refused,
        "f": store.share_f(),
        "unresolved_players": dict(rec.unresolved),
        "top": [
            {k: r[k] for k in ("game", "player_name", "stat", "k", "ask_c", "fee_c", "p_model")}
            | {"p_glm_e": r["p_glm_e"], "edge": r["edge_model"]}
            for r in sorted(rep.rows, key=lambda x: -x["edge_model"])[: a.top]
        ],
    }
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(replay_main())
