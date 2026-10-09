"""Daily props analysis: price every mapped Kalshi contract on a slate (read-only, analysis only).

Reuses the engine (``slate.load_slate``, ``__main__.price_singles/price_contract``, ``ev``, the
copula ``JointModel``, ``shadow.track_record``); no probability or fee math lives here. Databases
are attached READ_ONLY and the paper DB is only ever opened read-only (a missing one means a zero
track record), so nothing here can write to a real file or touch a market.

Shadow arms (``_int``, ``_lt``, ``_t30``) are shown side by side as "shadow, unconfirmed" and are
omitted for slates before ``SEALED_BEFORE`` (replay seasons: docs/HOLDOUT_ACCESS_LOG.md).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.parlay.__main__ import (
    DEFAULT_MODEL,
    ENGINE_KIND,
    ROOT,
    SingleContract,
    _load_model,
    load_open_markets,
    map_markets,
    pretip_only,
    price_contract,
    price_singles,
    side_quote,
)
from nba.parlay.config import ParlayConfig, load_config
from nba.parlay.ev import NO_POSITIVE_EV, Recommendation, prob_interval
from nba.parlay.joint import JointModel
from nba.parlay.kalshi_map import MarketRow, load_team_abbr
from nba.parlay.legs import Leg
from nba.parlay.qdist import QuantileGridDist
from nba.parlay.shadow import TrackRecord, track_record
from nba.parlay.slate import ET, PROPS_MODEL, WIN_MODELS, SlateInfo, load_slate

DISCLAIMER = (
    "Analysis only, not financial advice. Estimated edges are noisy; nothing here places "
    "orders, handles credentials, or recommends real-money bets."
)
SHADOW_ARMS = {
    "int": "props_context_residual_int",
    "lt": "props_context_residual_lt",
    "t30": "props_context_residual_t30",
}
#: slates before this date belong to replay seasons whose shadow-arm deltas are SEALED
SEALED_BEFORE = date(2026, 9, 1)
#: shadow vs primary P(>=N) gap flagged as a material disagreement (descriptive only)
SHADOW_DISAGREE = 0.05
PRODUCT_BASIS = "product_of_leg_asks_NOT_A_TRADABLE_PRICE"
STAT_NAMES = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}


@dataclass(frozen=True)
class Candidate:
    """One priced contract (a single market side) or hypothetical parlay."""

    kind: str  # "single" | "parlay"
    key: str
    side: str
    legs: tuple[Leg, ...]
    text: str
    games: tuple[str, ...]
    ask: float
    bid: float | None
    mid: float
    series: str
    rec: Recommendation
    n_sims: int
    raw_lo: float
    raw_hi: float
    tradable: bool
    price_basis: str
    independence_prob: float | None = None
    tickers: tuple[str, ...] = ()

    @property
    def same_game(self) -> bool:
        return len(self.games) == 1


@dataclass
class Analysis:
    slate: date
    now: datetime
    cfg: ParlayConfig
    tr: TrackRecord
    info: SlateInfo
    singles: list[Candidate]
    rows: list[dict[str, Any]]
    skipped: dict[str, int]
    notes: list[str]
    unmatched: list[str]
    n_markets: int
    engine: str
    dnp_policy: str
    shadow_enabled: bool
    names: Any
    abbr_of: dict[int, str]
    model: JointModel
    ind_model: JointModel
    mapped: dict[str, tuple[MarketRow, Leg]]
    combos: list[Candidate] = field(default_factory=list)

    @property
    def gate_open(self) -> bool:
        c = self.cfg
        return self.tr.n_settled >= c.min_settled and self.tr.n_dates >= c.min_settled_dates

    def gate_dict(self) -> dict[str, Any]:
        c = self.cfg
        return {
            "n_settled": self.tr.n_settled,
            "n_dates": self.tr.n_dates,
            "min_settled": c.min_settled,
            "min_settled_dates": c.min_settled_dates,
            "skill_vs_market": self.tr.skill,
            "gated_skill": self.tr.gated_skill(c.min_settled_dates),
            "gate_open": self.gate_open and self.tr.gated_skill(c.min_settled_dates) > 0,
            "describe": self.tr.describe(),
        }


# --------------------------------------------------------------------------- names
class OverlayNames:
    """Player display names: Kalshi prop titles ("Name: 30+ points") first, then the resolver
    (reviewed aliases + nba_api static list); falls back to ``player <id>`` only if both lack it."""

    def __init__(self, base: Any, overlay: dict[int, str]) -> None:
        self.base, self.overlay = base, overlay

    def display(self, pid: int) -> str:
        if pid in self.overlay:
            return self.overlay[pid]
        return str(self.base.display(pid))


def _title_names(markets: list[MarketRow]) -> dict[int, str]:
    out: dict[int, str] = {}
    for m in markets:
        if m.player_id is not None and ":" in m.title:
            name = m.title.split(":", 1)[0].strip()
            if " " in name and not any(ch.isdigit() for ch in name):
                out.setdefault(m.player_id, name)
    return out


# --------------------------------------------------------------------------- text helpers
def describe_leg(leg: Leg, names: Any, abbr_of: dict[int, str], info: SlateInfo) -> str:
    if leg.stat == "win":
        core = f"{abbr_of.get(leg.team_id or -1, '?')} win"
    elif leg.stat == "spread":
        core = f"{abbr_of.get(leg.team_id or -1, '?')} win by > {leg.threshold:g}"
    elif leg.stat == "total":
        ctx = info.ctxs[leg.game_id]
        core = (
            f"{abbr_of.get(ctx.away_team, '?')}@{abbr_of.get(ctx.home_team, '?')} "
            f"total >= {leg.threshold:g}"
        )
    else:
        core = f"{names.display(leg.player_id)} {leg.stat} >= {leg.threshold:g}"
    return core if leg.side == "yes" else f"NOT ({core})"


def _raw_interval(rec: Recommendation, n_sims: int, cfg: ParlayConfig) -> tuple[float, float]:
    """Unshrunk interval, same call ``ev.evaluate`` makes before market shrinkage."""
    p = rec.raw_model_prob
    if n_sims == 0:
        return prob_interval(
            round(p * 1000), 1000, cfg.alpha, cfg.calibration_logit_sd, cfg.n_draws, 0
        )
    return prob_interval(
        round(p * n_sims), n_sims, cfg.alpha, cfg.calibration_logit_sd, cfg.n_draws, 0
    )


def _combo_ask(parts: list[tuple[MarketRow, Leg]]) -> float | None:
    ask = 1.0
    for m, leg in parts:
        a, _ = side_quote(m, leg.side)
        if a is None or not 0.0 < a < 1.0:
            return None
        ask *= a
    return ask


# --------------------------------------------------------------------------- DB context
def _load_forward(
    con: duckdb.DuckDBPyConnection, slate: date, as_of: datetime
) -> tuple[dict[tuple[str, int, str], dict[str, Any]], dict[str, dict[tuple[str, int, str], Any]]]:
    """Latest (made_at <= as_of) primary blobs and shadow-arm blobs for the slate's games."""
    rows = con.execute(
        "SELECT game_id, tipoff, model_name, target, player_id, prediction, made_at "
        "FROM nba.forward_predictions WHERE made_at <= ? ORDER BY made_at",
        [as_of],
    ).fetchall()
    arm_of = {v: k for k, v in SHADOW_ARMS.items()}
    prim: dict[tuple[str, int, str], dict[str, Any]] = {}
    shadow: dict[str, dict[tuple[str, int, str], Any]] = {k: {} for k in SHADOW_ARMS}
    for gid, tip, model, target, pid, pred, _made in rows:
        if tip.replace(tzinfo=UTC).astimezone(ET).date() != slate:
            continue
        if model != PROPS_MODEL and model not in arm_of:
            continue
        blob = json.loads(pred) if isinstance(pred, str) else pred
        key = (str(gid), int(pid), str(target))
        if model == PROPS_MODEL:
            prim[key] = blob
        else:
            shadow[arm_of[model]][key] = blob
    return prim, shadow


def _drivers_db(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    now: datetime,
    info: SlateInfo,
    names: Any,
) -> tuple[dict[tuple[str, int], list[dict[str, Any]]], dict[int, dict[str, tuple[float, float]]]]:
    """Teammates OUT (with their recent per-game production) per (game, team), and every team's
    last-20 allowed stat totals vs the league mean. Empty on missing tables (not an error)."""
    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    allow: dict[int, dict[str, tuple[float, float]]] = {}
    gids = list(info.ctxs)
    try:
        for gid in gids:
            rows = con.execute(
                "WITH last AS (SELECT player_id, status, row_number() OVER "
                "(PARTITION BY player_id ORDER BY as_of DESC) rn FROM nba.player_availability "
                "WHERE game_id = ? AND as_of <= ?), "
                "tm AS (SELECT s.player_id, arg_max(s.team_id, g.game_date) team_id "
                "FROM nba.player_game_stats s JOIN nba.games g USING (game_id) "
                "WHERE g.game_date < ? GROUP BY s.player_id), "
                "rec AS (SELECT player_id, avg(pts) pts, avg(reb) reb, avg(ast) ast, "
                "avg(minutes) mins FROM (SELECT s.*, row_number() OVER (PARTITION BY s.player_id "
                "ORDER BY g.game_date DESC) r FROM nba.player_game_stats s JOIN nba.games g "
                "USING (game_id) WHERE g.game_date < ? AND s.minutes > 0) WHERE r <= 10 "
                "GROUP BY player_id) "
                "SELECT l.player_id, tm.team_id, rec.pts, rec.reb, rec.ast, rec.mins "
                "FROM last l JOIN tm USING (player_id) LEFT JOIN rec USING (player_id) "
                "WHERE l.rn = 1 AND l.status = 'out'",
                [gid, now, slate, slate],
            ).fetchall()
            ctx = info.ctxs[gid]
            for pid, tid, pts, reb, ast, mins in rows:
                if int(tid) in (ctx.home_team, ctx.away_team):
                    out.setdefault((gid, int(tid)), []).append(
                        {
                            "player_id": int(pid),
                            "name": names.display(int(pid)),
                            "pts": None if pts is None else round(float(pts), 1),
                            "reb": None if reb is None else round(float(reb), 1),
                            "ast": None if ast is None else round(float(ast), 1),
                            "min": None if mins is None else round(float(mins), 1),
                        }
                    )
        trs = con.execute(
            "WITH tot AS (SELECT s.game_id, s.team_id, sum(pts) pts, sum(reb) reb, sum(ast) ast, "
            "sum(fg3m) fg3m FROM nba.player_game_stats s JOIN nba.games g USING (game_id) "
            "WHERE g.game_date < ? GROUP BY 1, 2), "
            "al AS (SELECT a.team_id def_team, b.pts, b.reb, b.ast, b.fg3m, "
            "row_number() OVER (PARTITION BY a.team_id ORDER BY g.game_date DESC) r "
            "FROM tot a JOIN tot b ON a.game_id = b.game_id AND a.team_id <> b.team_id "
            "JOIN nba.games g ON g.game_id = a.game_id) "
            "SELECT def_team, avg(pts), avg(reb), avg(ast), avg(fg3m), count(*) FROM al "
            "WHERE r <= 20 GROUP BY def_team",
            [slate],
        ).fetchall()
    except duckdb.Error:
        return out, allow
    if trs:
        lg = [sum(float(r[i]) for r in trs) / len(trs) for i in (1, 2, 3, 4)]
        for r in trs:
            allow[int(r[0])] = {
                s: (round(float(r[i + 1]), 1), round(lg[i], 1))
                for i, s in enumerate(("pts", "reb", "ast", "fg3m"))
            }
    return out, allow


def _shadow_p(blob: Any, thr: float) -> float | None:
    if not isinstance(blob, dict):
        return None
    try:
        if blob.get("q_grid"):
            return float(QuantileGridDist.from_grid(blob["q_grid"]).p_ge(thr))
        pg = blob.get("p_ge") or {}
        k = str(int(thr)) if float(thr).is_integer() else str(thr)
        return float(pg[k]) if k in pg else None
    except (ValueError, KeyError, TypeError):
        return None


# --------------------------------------------------------------------------- build
def build_analysis(
    slate: date,
    now: datetime | None = None,
    *,
    nba_db: Path | str = ROOT / "nba.duckdb",
    kalshi_db: Path | str = ROOT / "data" / "kalshi" / "kalshi.duckdb",
    paper_db: Path | str = ROOT / "data" / "parlay" / "paper_trades.duckdb",
    model_path: Path | str = DEFAULT_MODEL,
    config: Path | str | None = None,
    engine: str | None = None,
    dnp_policy: str = "void",
    names: Any = None,
    shadow: bool = True,
) -> Analysis:
    """Price every mapped, pre-tip single-leg contract (both sides) for ``slate``."""
    cfg = load_config(config) if config else load_config()
    now = now or datetime.now(UTC).replace(tzinfo=None)
    engine_name = engine or cfg.engine
    if engine_name not in ENGINE_KIND:
        raise SystemExit(f"engine {engine_name!r} not supported; use {list(ENGINE_KIND)}")
    params, pc = _load_model(Path(model_path))
    if names is None:
        from nba.parlay.assistant.names import NameResolver

        names = NameResolver.default()
    abbr_of = {v: k for k, v in load_team_abbr().items()}
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{nba_db}' AS nba (READ_ONLY)")
    have_k = Path(kalshi_db).exists()
    if have_k:
        con.execute(f"ATTACH '{kalshi_db}' AS kal (READ_ONLY)")
    try:
        info = load_slate(con, slate, params, prefix="nba.", as_of=now)
        markets = load_open_markets(con, before=now) if have_k else []
        prim, shadow_blobs = _load_forward(con, slate, now)
        out_by_team, allow = _drivers_db(con, slate, now, info, names)
    finally:
        con.close()
    names = OverlayNames(names, _title_names(markets))
    for lst in out_by_team.values():
        for o in lst:
            o["name"] = names.display(o["player_id"])
    notes = list(info.notes)
    if not have_k:
        notes.append(f"no Kalshi DB at {kalshi_db}: no contracts to price")
    shadow_on = shadow and slate >= SEALED_BEFORE
    if shadow and not shadow_on:
        notes.append(
            "shadow arms omitted: replay-season slate (shadow-arm deltas are SEALED, "
            "docs/HOLDOUT_ACCESS_LOG.md 2026-10-09)"
        )
    tr = TrackRecord(0, None, None, 0.0)
    if Path(paper_db).exists():
        pcon = duckdb.connect(str(paper_db), read_only=True)
        try:
            tr = track_record(pcon)
        except duckdb.CatalogException:
            pass
        finally:
            pcon.close()
    model = JointModel(ENGINE_KIND[engine_name], pc, cfg.n_sims, cfg.seed, cfg.t_df)
    ind_model = JointModel("independence", pc, cfg.n_sims, cfg.seed, cfg.t_df)
    unmatched: list[str] = []
    mapped, skipped = map_markets(markets, info, unmatched)
    mapped = pretip_only(mapped, info, now, skipped)
    singles: list[Candidate] = []
    rows: list[dict[str, Any]] = []
    for sc in price_singles(mapped, info, model, cfg, dnp_policy, tr, skipped):
        cand = _single_candidate(sc, cfg, info, names, abbr_of)
        singles.append(cand)
        rows.append(
            _contract_row(
                cand, sc, info, names, abbr_of, prim, shadow_blobs, shadow_on, out_by_team, allow
            )
        )
    return Analysis(
        slate, now, cfg, tr, info, singles, rows, skipped, notes, unmatched, len(markets),
        engine_name, dnp_policy, shadow_on, names, abbr_of, model, ind_model, mapped,
    )  # fmt: skip


def _single_candidate(
    sc: SingleContract, cfg: ParlayConfig, info: SlateInfo, names: Any, abbr_of: dict[int, str]
) -> Candidate:
    lo, hi = _raw_interval(sc.rec, 0, cfg)
    leg = sc.legs[0]
    bid = side_quote(sc.market, sc.side)[1]
    return Candidate(
        "single", f"{sc.ticker}|{sc.side}", sc.side, tuple(sc.legs),
        describe_leg(leg, names, abbr_of, info), (leg.game_id,), sc.ask, bid, sc.mid,
        sc.market.series, sc.rec, 0, lo, hi, True, "kalshi_ask", None, (sc.ticker,),
    )  # fmt: skip


def _contract_row(
    cand: Candidate,
    sc: SingleContract,
    info: SlateInfo,
    names: Any,
    abbr_of: dict[int, str],
    prim: dict[tuple[str, int, str], dict[str, Any]],
    shadow_blobs: dict[str, dict[tuple[str, int, str], Any]],
    shadow_on: bool,
    out_by_team: dict[tuple[str, int], list[dict[str, Any]]],
    allow: dict[int, dict[str, tuple[float, float]]],
) -> dict[str, Any]:
    leg = cand.legs[0]
    ctx = info.ctxs[leg.game_id]
    row: dict[str, Any] = cand.rec.to_dict() | {
        "ticker": sc.ticker,
        "side": sc.side,
        "text": cand.text,
        "title": sc.market.title,
        "stat": leg.stat,
        "threshold": leg.threshold,
        "player_id": leg.player_id or None,
        "player": names.display(leg.player_id) if leg.player_id else None,
        "game": f"{abbr_of.get(ctx.away_team, '?')}@{abbr_of.get(ctx.home_team, '?')}",
        "mid": cand.mid,
        "ask": cand.ask,
        "bid": cand.bid,
        "price_ts": sc.market.price_ts,
        "raw_prob_interval": [cand.raw_lo, cand.raw_hi],
        "p_play": None,
        "dnp_risk": None,
        "drivers": None,
        "shadow": None,
    }
    if leg.player_id and leg.stat in STAT_NAMES:
        pp = ctx.p_play.get(leg.player_id, 1.0)
        row["p_play"], row["dnp_risk"] = pp, 1.0 - pp
        blob = prim.get((leg.game_id, leg.player_id, leg.stat), {})
        tid = ctx.team_of.get(leg.player_id)
        opp = ctx.away_team if tid == ctx.home_team else ctx.home_team
        outs = [
            o
            for o in out_by_team.get((leg.game_id, tid or -1), [])
            if o["player_id"] != leg.player_id
        ]
        mean, rec_mean = blob.get("mean"), blob.get("mean_recency")
        row["drivers"] = {
            "proj_minutes": blob.get("proj_minutes"),
            "mean_cond": mean,
            "ctx_shift_vs_recency": None
            if mean is None or rec_mean is None or leg.stat != "pts"
            else round(float(mean) - float(rec_mean), 2),
            "n_games_history": blob.get("n_games"),
            "routed_to": blob.get("routed_to"),
            "teammates_out": outs,
            "vacated_per_game": {
                s: round(sum(o[s] or 0.0 for o in outs), 1) for s in ("pts", "reb", "ast")
            },
            "opp_allowed_last20": {
                "team": abbr_of.get(opp, str(opp)),
                "avg": (allow.get(opp, {}).get(leg.stat) or (None, None))[0],
                "league": (allow.get(opp, {}).get(leg.stat) or (None, None))[1],
            },
        }
        if shadow_on:
            sh: dict[str, Any] = {}
            for arm, blobs in shadow_blobs.items():
                p = _shadow_p(blobs.get((leg.game_id, leg.player_id, leg.stat)), leg.threshold)
                if p is not None:
                    p = p if leg.side == "yes" else 1.0 - p
                sh[arm] = p
            ref = cand.rec.raw_model_prob
            row["shadow"] = {
                "label": "shadow, unconfirmed",
                "p": sh,
                "material_disagreement": any(
                    v is not None and abs(v - ref) >= SHADOW_DISAGREE for v in sh.values()
                ),
            }
    return row


# --------------------------------------------------------------------------- combos
def pool_legs(singles: list[Candidate], k: int) -> list[Candidate]:
    """One side per market (higher raw EV at the low bound), top ``k`` by that edge."""
    best: dict[str, Candidate] = {}
    for c in singles:
        t = c.tickers[0]
        if t not in best or c.rec.raw_ev_low > best[t].rec.raw_ev_low:
            best[t] = c
    return sorted(best.values(), key=lambda c: (-c.rec.raw_ev_low, c.key))[:k]


def enumerate_combos(
    pool: list[Candidate], max_legs: int, max_combos: int, scope: str = "any"
) -> list[tuple[Candidate, ...]]:
    """Deterministic leg combinations (2..max_legs). ``scope``: same | cross | any.

    A combo never holds two legs on the same (game, player, stat) (nested ladders, opposing
    win legs); ``same`` = one game, ``cross`` = every leg in a different game."""
    out: list[tuple[Candidate, ...]] = []
    for r in range(2, max(2, max_legs) + 1):
        for combo in itertools.combinations(pool, r):
            legs = [c.legs[0] for c in combo]
            if len({(lg.game_id, lg.player_id, lg.stat) for lg in legs}) < r:
                continue
            games = {lg.game_id for lg in legs}
            if (scope == "same" and len(games) != 1) or (scope == "cross" and len(games) != r):
                continue
            out.append(combo)
            if len(out) >= max_combos:
                return out
    return out


def price_combos(an: Analysis, combos: list[tuple[Candidate, ...]]) -> list[Candidate]:
    """Price combos with the configured engine (copula within a game, independent across games)
    at the PRODUCT of leg asks, which is not a tradable price (no combo contracts are ingested)."""
    out: list[Candidate] = []
    for combo in combos:
        legs = [c.legs[0] for c in combo]
        parts = [(an.mapped[c.tickers[0]][0], lg) for c, lg in zip(combo, legs, strict=True)]
        ask = _combo_ask(parts)
        if ask is None:
            continue
        ctxs = {lg.game_id: an.info.ctxs[lg.game_id] for lg in legs}
        series = combo[0].series
        rec = price_contract(
            legs, ctxs, an.model, ask, None, an.cfg, an.dnp_policy, series=series,
            n_settled=an.tr.n_settled, skill=an.tr.gated_skill(an.cfg.min_settled_dates),
            engine_name=an.engine,
        )  # fmt: skip
        n = an.cfg.n_sims
        lo, hi = _raw_interval(rec, n, an.cfg)
        ind = an.ind_model.joint(legs, ctxs)
        text = " AND ".join(c.text for c in combo)
        out.append(
            Candidate(
                "parlay", "+".join(c.key for c in combo), "yes", tuple(legs), text,
                tuple(dict.fromkeys(lg.game_id for lg in legs)), ask, None, ask, series, rec, n,
                lo, hi, False, PRODUCT_BASIS, ind.p_cond, tuple(c.tickers[0] for c in combo),
            )
        )  # fmt: skip
    return out


def with_combos(
    an: Analysis, *, pool_k: int = 12, max_legs: int = 3, max_combos: int = 300, scope: str = "any"
) -> list[Candidate]:
    """Enumerate and price combos (cached on the analysis, appended to ``an.combos``)."""
    pool = pool_legs(an.singles, pool_k)
    priced = price_combos(an, enumerate_combos(pool, max_legs, max_combos, scope))
    an.combos = priced
    return priced


def cand_dict(c: Candidate) -> dict[str, Any]:
    """Contract-shaped dict for a candidate (legs/probabilities/price/fees/ev/ev_low/verdict)."""
    d = c.rec.to_dict()
    d["legs"] = [lg.to_dict() for lg in c.legs]
    d |= {
        "key": c.key,
        "kind": c.kind,
        "text": c.text,
        "tradable": c.tradable,
        "price_basis": c.price_basis,
        "ask": c.ask,
        "bid": c.bid,
        "mid": c.mid,
        "raw_prob_interval": [c.raw_lo, c.raw_hi],
        "independence_prob": c.independence_prob,
    }
    return d


def replace_rec(c: Candidate, rec: Recommendation) -> Candidate:
    return replace(c, rec=rec)


__all__ = [
    "DISCLAIMER",
    "NO_POSITIVE_EV",
    "WIN_MODELS",
    "Analysis",
    "Candidate",
    "build_analysis",
    "cand_dict",
    "enumerate_combos",
    "pool_legs",
    "price_combos",
    "with_combos",
]
