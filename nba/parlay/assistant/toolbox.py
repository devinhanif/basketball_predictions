"""Engine-backed tools. Thin wrappers: all math is the engine's (joint, ev, shadow, slate).

Databases are attached READ_ONLY. The one writable file is the paper DB, opened only inside
``log_paper_trade`` (off unless ``allow_paper_log``) and closed again immediately.
Every tool returns a JSON-able dict; bad input yields ``{"error": ...}``, never a guess.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date
from pathlib import Path
from typing import Any

import duckdb

from nba.parlay.__main__ import (
    DEFAULT_MODEL,
    ENGINE_KIND,
    ROOT,
    _load_model,
    load_open_markets,
    map_markets,
    price_contract,
    price_singles,
    side_quote,
)
from nba.parlay.assistant.names import NameResolver, PlayerResolutionError
from nba.parlay.assistant.tools import PAPER_LOG_SPEC, TOOL_SPECS, ArgError, validate
from nba.parlay.config import ParlayConfig
from nba.parlay.ev import NO_POSITIVE_EV, Recommendation, prob_interval
from nba.parlay.joint import DNP_POLICIES, GameCtx, JointModel, leg_marginal, p_all_play
from nba.parlay.kalshi_map import MarketRow, load_team_abbr
from nba.parlay.legs import Leg
from nba.parlay.papertrade import log_trade
from nba.parlay.shadow import TrackRecord, ensure_schema, track_record
from nba.parlay.slate import ET, PROPS_MODEL, WIN_MODELS, SlateInfo, load_slate

NOT_EVALUABLE = "not_evaluable_no_price"
PRODUCT_BASIS = "product_of_leg_asks_NOT_A_TRADABLE_PRICE"
_UNSTORED = ("q_grid", "p_ge_uncond", "dist_params")


class ToolError(ValueError):
    """Bad or unsupported tool request (reported to the model as an error result)."""


@dataclass(frozen=True)
class EnginePaths:
    nba_db: Path = ROOT / "nba.duckdb"
    kalshi_db: Path = ROOT / "data" / "kalshi" / "kalshi.duckdb"
    paper_db: Path = ROOT / "data" / "parlay" / "paper_trades.duckdb"
    joint_model: Path = DEFAULT_MODEL


@dataclass
class _Slate:
    info: SlateInfo
    mapped: dict[str, tuple[MarketRow, Leg]]
    skipped: dict[str, int]
    n_markets: int
    props_raw: dict[tuple[str, int, str], dict[str, Any]]
    win_raw: dict[str, dict[str, Any]]
    made_at: dict[tuple[str, int, str], str] = field(default_factory=dict)


def _finite(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


class Toolbox:
    def __init__(
        self,
        paths: EnginePaths,
        cfg: ParlayConfig,
        resolver: NameResolver,
        *,
        default_date: date,
        engine: str | None = None,
        dnp_policy: str = "void",
        allow_paper_log: bool = False,
        top_n: int = 5,
    ) -> None:
        self.paths = paths
        self.cfg = cfg
        self.resolver = resolver
        self.default_date = default_date
        self.engine_name = engine or cfg.engine
        if self.engine_name not in ENGINE_KIND:
            raise ValueError(f"engine {self.engine_name!r} not supported; use {list(ENGINE_KIND)}")
        if dnp_policy not in DNP_POLICIES:
            raise ValueError(f"dnp_policy must be one of {DNP_POLICIES}")
        self.dnp_policy = dnp_policy
        self.allow_paper_log = allow_paper_log
        self.top_n = top_n
        self._abbr = load_team_abbr()
        self._abbr_of = {v: k for k, v in self._abbr.items()}
        self._slates: dict[date, _Slate] = {}
        self._joint: tuple[Any, Any] | None = None
        self._analyses: dict[date, Any] = {}

    # ------------------------------------------------------------------ plumbing
    def tool_specs(self) -> list[dict[str, Any]]:
        specs = list(TOOL_SPECS.values())
        if self.allow_paper_log:
            specs.append(PAPER_LOG_SPEC)
        return specs

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate and dispatch. Never raises for bad input; returns ``{"error": msg}``."""
        specs = {s["function"]["name"]: s["function"] for s in self.tool_specs()}
        if name not in specs:
            return {"error": f"unknown tool {name!r}; available: {sorted(specs)}"}
        try:
            validate(specs[name]["parameters"], args)
            return getattr(self, f"tool_{name}")(**args)  # type: ignore[no-any-return]
        except (ArgError, ToolError, PlayerResolutionError) as e:
            return {"error": str(e)}

    def _date(self, d: str | None) -> date:
        if d is None:
            return self.default_date
        try:
            return date.fromisoformat(d)
        except ValueError as e:
            raise ToolError(f"bad date {d!r}; use YYYY-MM-DD") from e

    def _engine_models(self) -> tuple[Any, Any]:
        if self._joint is None:
            try:
                self._joint = _load_model(self.paths.joint_model)
            except SystemExit as e:
                raise ToolError(str(e)) from e
        return self._joint

    def _model(self, kind: str | None = None) -> JointModel:
        _, pc = self._engine_models()
        k = kind or ENGINE_KIND[self.engine_name]
        return JointModel(k, pc, self.cfg.n_sims, self.cfg.seed, self.cfg.t_df)

    def _slate(self, d: date) -> _Slate:
        if d in self._slates:
            return self._slates[d]
        params, _ = self._engine_models()
        if not self.paths.nba_db.exists():
            raise ToolError(f"missing {self.paths.nba_db}")
        con = duckdb.connect(":memory:")
        try:
            con.execute(f"ATTACH '{self.paths.nba_db}' AS nba (READ_ONLY)")
            have_k = self.paths.kalshi_db.exists()
            if have_k:
                con.execute(f"ATTACH '{self.paths.kalshi_db}' AS kal (READ_ONLY)")
            info = load_slate(con, d, params, prefix="nba.")
            markets = load_open_markets(con) if have_k else []
            raw = con.execute(
                "SELECT game_id, tipoff, model_name, target, player_id, prediction, made_at "
                "FROM nba.forward_predictions ORDER BY made_at"
            ).fetchall()
        finally:
            con.close()
        mapped, skipped = map_markets(markets, info)
        props: dict[tuple[str, int, str], dict[str, Any]] = {}
        win: dict[str, dict[str, Any]] = {}
        made: dict[tuple[str, int, str], str] = {}
        for gid, tip, model, target, pid, pred, made_at in raw:
            if tip.replace(tzinfo=UTC).astimezone(ET).date() != d:
                continue
            blob = json.loads(pred) if isinstance(pred, str) else pred
            if model == PROPS_MODEL and blob.get("q_grid"):
                props[(str(gid), int(pid), str(target))] = blob
                made[(str(gid), int(pid), str(target))] = str(made_at)
            elif target == "win_prob_home" and model in WIN_MODELS:
                if model == WIN_MODELS[0] or str(gid) not in win:
                    win[str(gid)] = blob
        sl = _Slate(info, mapped, skipped, len(markets), props, win, made)
        self._slates[d] = sl
        return sl

    def _need_games(self, d: date) -> _Slate:
        sl = self._slate(d)
        if not sl.info.ctxs:
            raise ToolError(f"no forward predictions for {d}. " + " ".join(sl.info.notes))
        return sl

    def _track(self) -> TrackRecord:
        if not self.paths.paper_db.exists():
            return TrackRecord(0, None, None, 0.0)
        con = duckdb.connect(str(self.paths.paper_db), read_only=True)
        try:
            return track_record(con)
        except duckdb.CatalogException:
            return TrackRecord(0, None, None, 0.0)
        finally:
            con.close()

    def _matchup(self, ctx: GameCtx) -> str:
        away = self._abbr_of.get(ctx.away_team, ctx.away_team)
        home = self._abbr_of.get(ctx.home_team, ctx.home_team)
        return f"{away}@{home}"

    # ------------------------------------------------------------------ leg parsing
    def _games_for(self, sl: _Slate, spec: dict[str, Any], team: int | None) -> list[str]:
        gids = list(sl.info.ctxs)
        g = spec.get("game")
        if g:
            gl = str(g).lower()
            gids = [
                x for x in gids if x.lower() == gl or self._matchup(sl.info.ctxs[x]).lower() == gl
            ]
        if team is not None:
            gids = [
                x for x in gids if team in (sl.info.ctxs[x].home_team, sl.info.ctxs[x].away_team)
            ]
        return gids

    def _parse_leg(self, spec: dict[str, Any], sl: _Slate) -> Leg:
        stat = str(spec["stat"])
        side = "no" if spec.get("side") == "no" else "yes"
        thr = spec.get("threshold")
        team: int | None = None
        if spec.get("team") is not None:
            team = self._abbr.get(str(spec["team"]).upper())
            if team is None:
                raise ToolError(f"unknown team abbreviation {spec['team']!r}")
        if stat in ("win", "spread", "total"):
            if spec.get("player") is not None:
                raise ToolError(f"{stat} legs take no player")
            if stat != "win" and thr is None:
                raise ToolError(f"{stat} leg needs a threshold")
            if stat in ("win", "spread") and team is None:
                raise ToolError(f"{stat} leg needs a team abbreviation")
            gids = self._games_for(sl, spec, team)
            if len(gids) != 1:
                raise ToolError(f"{stat} leg matches {len(gids)} games on this slate; add 'game'")
            lt = None if stat == "total" else team
            return Leg(gids[0], 0, stat, 0.0 if thr is None else float(thr), side, lt)  # type: ignore[arg-type]
        if spec.get("player") is None or thr is None:
            raise ToolError(f"{stat} leg needs player and threshold")
        have = {p for (_g, p, s) in sl.props_raw if s == stat}
        pid = self.resolver.resolve(spec["player"], have)
        gids = [g for g in self._games_for(sl, spec, None) if (g, pid, stat) in sl.props_raw]
        gids = [g for g in gids if pid in sl.info.ctxs[g].team_of]
        if len(gids) != 1:
            raise ToolError(
                f"no unique prediction for {self.resolver.display(pid)} {stat} on this slate"
            )
        ctx = sl.info.ctxs[gids[0]]
        return Leg(gids[0], pid, stat, float(thr), side, ctx.team_of[pid])  # type: ignore[arg-type]

    def _describe(self, leg: Leg, sl: _Slate) -> str:
        ctx = sl.info.ctxs[leg.game_id]
        ab = self._abbr_of.get(leg.team_id or -1, "?")
        if leg.stat == "win":
            core = f"{ab} win"
        elif leg.stat == "spread":
            core = f"{ab} win by more than {leg.threshold:g}"
        elif leg.stat == "total":
            core = f"{self._matchup(ctx)} total points >= {leg.threshold:g}"
        else:
            core = f"{self.resolver.display(leg.player_id)} {leg.stat} >= {leg.threshold:g}"
        return core if leg.side == "yes" else f"NOT ({core})"

    def _market_for(self, leg: Leg, sl: _Slate) -> MarketRow | None:
        for m, ml in sl.mapped.values():
            if (ml.game_id, ml.player_id, ml.stat, ml.threshold, ml.team_id) == (
                leg.game_id,
                leg.player_id,
                leg.stat,
                leg.threshold,
                leg.team_id,
            ):
                return m
        return None

    # ------------------------------------------------------------------ pricing
    def _fee_info(self) -> dict[str, Any]:
        f = self.cfg.fee
        return {
            "name": f.name,
            "verified": f.verified,
            "taker_coefficient": f.coefficient,
            "order_type": self.cfg.order_type,
            "source": f.source,
        }

    def _price_legs(
        self, legs: list[Leg], sl: _Slate, ask_override: float | None
    ) -> dict[str, Any]:
        ctxs = {leg.game_id: sl.info.ctxs[leg.game_id] for leg in legs}
        tr = self._track()
        model = self._model()
        mkts = [self._market_for(leg, sl) for leg in legs]
        series = next((m.series for m in mkts if m is not None), "")
        ask: float | None = None
        bid: float | None = None
        basis = ""
        if ask_override is not None:
            if not 0.0 < ask_override < 1.0:
                raise ToolError("ask must be strictly between 0 and 1")
            ask, basis = ask_override, "user_supplied_ask"
        elif len(legs) == 1 and mkts[0] is not None:
            ask, bid = side_quote(mkts[0], legs[0].side)
            basis = "kalshi_ask"
        elif len(legs) > 1 and all(m is not None for m in mkts):
            ask = 1.0
            for leg, m in zip(legs, mkts, strict=True):
                assert m is not None
                a, _ = side_quote(m, leg.side)
                ask = None if a is None or ask is None else ask * a
            basis = PRODUCT_BASIS
        legs_txt = [self._describe(leg, sl) for leg in legs]
        # Probability pieces (conditional on every player playing), copula vs independence.
        if len(legs) == 1:
            p_cond = leg_marginal(legs[0], ctxs[legs[0].game_id])
            hits, n, p_nov = 0, 0, p_all_play(legs, ctxs)
        else:
            out = model.joint(legs, ctxs)
            p_cond, hits, n, p_nov = out.p_cond, out.hits, out.n_sims, out.p_nov
        ind = self._model("independence").joint(legs, ctxs)
        base: dict[str, Any] = {
            "legs_text": legs_txt,
            "n_legs": len(legs),
            "n_games": len(ctxs),
            "price_basis": basis or "none",
            "market_tickers": [m.ticker if m else None for m in mkts],
            "engine": "marginal" if len(legs) == 1 else self.engine_name,
            "dnp_policy": self.dnp_policy,
            "joint_prob_if_all_play": p_cond,
            "independent_prob_if_all_play": ind.p_cond,
            "correlation_effect": p_cond - ind.p_cond,
            "p_all_play": p_nov,
            "leg_marginals": list(ind.marginals),
            "cross_game_assumption": "legs in different games are priced as independent "
            "(see independence check)"
            if len(ctxs) > 1
            else "single game",
            "fee_model": self._fee_info(),
            "min_settled": self.cfg.min_settled,
            "interval_level": 1.0 - self.cfg.alpha,
            "n_sims": n,
        }
        if ask is None or not 0.0 < ask < 1.0:
            lo, hi = prob_interval(
                hits if n else round(p_cond * 1000),
                n or 1000,
                self.cfg.alpha,
                self.cfg.calibration_logit_sd,
                self.cfg.n_draws,
                0,
            )
            return base | {
                "priced": False,
                "model_prob": p_cond,
                "prob_interval": [lo, hi],
                "price": None,
                "fees": None,
                "ev": None,
                "ev_low": None,
                "verdict": NOT_EVALUABLE,
                "note": "no Kalshi market/ask for every leg; pass 'ask' to price a hypothetical. "
                "Probabilities are conditional on all listed players playing.",
                "n_settled_track": tr.n_settled,
            }
        rec = price_contract(
            legs,
            ctxs,
            model,
            ask,
            bid,
            self.cfg,
            self.dnp_policy,
            series=series,
            n_settled=tr.n_settled,
            skill=tr.gated_skill(self.cfg.min_settled_dates),
            engine_name="marginal" if len(legs) == 1 else self.engine_name,
        )
        d = rec.to_dict()
        d.pop("legs")
        return base | d | {"priced": True, "track_n_settled": tr.n_settled}

    def _legs_from(self, specs: list[dict[str, Any]], sl: _Slate) -> list[Leg]:
        legs = [self._parse_leg(s, sl) for s in specs]
        if len(set(legs)) != len(legs):
            raise ToolError("duplicate legs")
        return legs

    # ------------------------------------------------------------------ budget tools
    def _analysis(self, d: date) -> Any:
        from nba.parlay.analysis import build_analysis

        if d not in self._analyses:
            try:
                self._analyses[d] = build_analysis(
                    d,
                    None,
                    nba_db=self.paths.nba_db,
                    kalshi_db=self.paths.kalshi_db,
                    paper_db=self.paths.paper_db,
                    model_path=self.paths.joint_model,
                    engine=self.engine_name,
                    dnp_policy=self.dnp_policy,
                    names=self.resolver,
                )
            except SystemExit as e:
                raise ToolError(str(e)) from e
        return self._analyses[d]

    def tool_best_for_budget(self, budget_usd: float, date: str | None = None) -> dict[str, Any]:
        from nba.parlay.budget import best_for_budget_from

        an = self._analysis(self._date(date))
        if not an.info.ctxs:
            raise ToolError(f"no forward predictions for {an.slate}")
        return best_for_budget_from(an, budget_usd)

    def tool_best_for_target(
        self, budget_usd: float, target_profit_usd: float, date: str | None = None
    ) -> dict[str, Any]:
        from nba.parlay.budget import best_for_target_from

        an = self._analysis(self._date(date))
        if not an.info.ctxs:
            raise ToolError(f"no forward predictions for {an.slate}")
        return best_for_target_from(an, budget_usd, target_profit_usd)

    # ------------------------------------------------------------------ tools
    def tool_list_slate(self, date: str | None = None) -> dict[str, Any]:
        d = self._date(date)
        sl = self._slate(d)
        games: list[dict[str, Any]] = []
        for gid, ctx in sorted(sl.info.ctxs.items()):
            home = Leg(gid, 0, "win", 0.0, "yes", ctx.home_team)
            players = sorted(
                {p for (g, p, _s) in sl.props_raw if g == gid and p in ctx.team_of},
                key=lambda p: -float(sl.props_raw.get((gid, p, "pts"), {}).get("mean", 0.0)),
            )
            games.append(
                {
                    "game_id": gid,
                    "matchup": self._matchup(ctx),
                    "p_home_win": leg_marginal(home, ctx),
                    "win_model": sl.info.win_model.get(gid, ""),
                    "total_mean": ctx.mu_t,
                    "n_players_with_predictions": len(players),
                    "top_players": [
                        {
                            "player_id": p,
                            "name": self.resolver.display(p),
                            "team": self._abbr_of.get(ctx.team_of[p], "?"),
                            "p_play": ctx.p_play.get(p, 1.0),
                            "pts_mean": sl.props_raw.get((gid, p, "pts"), {}).get("mean"),
                        }
                        for p in players[:8]
                    ],
                }
            )
        return {
            "date": d.isoformat(),
            "n_games": len(games),
            "games": games,
            "n_open_kalshi_markets": sl.n_markets,
            "n_mapped_markets": len(sl.mapped),
            "notes": sl.info.notes,
        }

    def tool_evaluate_slate(
        self, date: str | None = None, top_n: int | None = None, stat: str | None = None
    ) -> dict[str, Any]:
        d = self._date(date)
        sl = self._need_games(d)
        tr = self._track()
        skipped = dict(sl.skipped)
        mapped = {k: v for k, v in sl.mapped.items() if stat is None or v[1].stat == stat}
        contracts = price_singles(
            mapped, sl.info, self._model(), self.cfg, self.dnp_policy, tr, skipped
        )
        rows = [
            c.rec.to_dict()
            | {
                "ticker": c.ticker,
                "side": c.side,
                "title": c.market.title,
                "price_ts": c.market.price_ts,
            }
            for c in contracts
        ]
        n_pos = sum(1 for r in rows if r["verdict"] != NO_POSITIVE_EV)
        rows.sort(key=lambda r: -r["raw_ev_low"])
        keep = top_n or self.top_n
        top = []
        for r in rows[:keep]:
            r = dict(r)
            r["legs_text"] = [self._describe(Leg.from_dict(x), sl) for x in r.pop("legs")]
            top.append(r)
        return {
            "date": d.isoformat(),
            "n_games": len(sl.info.ctxs),
            "n_open_markets": sl.n_markets,
            "n_mapped": len(mapped),
            "skipped": skipped,
            "n_contracts_evaluated": len(rows),
            "n_flagged_positive_ev": n_pos,
            "no_positive_ev_found": n_pos == 0,
            "verdict_for_rest": NO_POSITIVE_EV,
            "n_settled_track": tr.n_settled,
            "min_settled": self.cfg.min_settled,
            "model_weight_zero": tr.n_settled < self.cfg.min_settled,
            "ranked_by": "raw_ev_low (unshrunk model EV at the low bound; hypothesis only)",
            "interval_level": 1.0 - self.cfg.alpha,
            "fee_model": self._fee_info(),
            "top": top,
        }

    def tool_price_parlay(
        self, legs: list[dict[str, Any]], date: str | None = None, ask: float | None = None
    ) -> dict[str, Any]:
        sl = self._need_games(self._date(date))
        return self._price_legs(self._legs_from(legs, sl), sl, ask)

    def tool_explain_leg(
        self, player: str, stat: str, threshold: float, date: str | None = None
    ) -> dict[str, Any]:
        d = self._date(date)
        sl = self._need_games(d)
        have = {p for (_g, p, s) in sl.props_raw if s == stat}
        pid = self.resolver.resolve(player, have)
        keys = [
            k
            for k in sl.props_raw
            if k[1] == pid and k[2] == stat and pid in sl.info.ctxs[k[0]].team_of
        ]
        if len(keys) != 1:
            raise ToolError(f"no unique {stat} prediction for {self.resolver.display(pid)}")
        gid = keys[0][0]
        ctx = sl.info.ctxs[gid]
        raw = sl.props_raw[keys[0]]
        leg = Leg(gid, pid, stat, float(threshold), "yes", ctx.team_of[pid])
        p_cond = leg_marginal(leg, ctx)
        p_play = ctx.p_play.get(pid, 1.0)
        stored = {k: v for k, v in raw.items() if k not in _UNSTORED}
        win = sl.win_raw.get(gid, {})
        mkt = self._market_for(leg, sl)
        return {
            "player": self.resolver.display(pid),
            "player_id": pid,
            "team": self._abbr_of.get(ctx.team_of[pid], "?"),
            "matchup": self._matchup(ctx),
            "stat": stat,
            "threshold": float(threshold),
            "p_ge_if_plays": p_cond,
            "p_play": p_play,
            "p_ge_unconditional_no_refund": p_play * p_cond,
            "stored_prediction": stored,
            "made_at": sl.made_at.get(keys[0]),
            "routed_to": raw.get("routed_to"),
            "fallback_reason": raw.get("fallback_reason"),
            "game_context": {
                k: win.get(k)
                for k in ("d_out", "d_doubt", "report_as_of_et", "fallback_reason", "n_train_games")
                if k in win
            },
            "teammates_out_note": "forward_predictions stores aggregate injury load (d_out, "
            "d_doubt) and n_games_with_report, not named teammates",
            "kalshi": None
            if mkt is None
            else {
                "ticker": mkt.ticker,
                "yes_bid": mkt.yes_bid,
                "yes_ask": mkt.yes_ask,
                "price_ts": mkt.price_ts,
            },
            "kalshi_note": None if mkt else "no mapped Kalshi market at exactly this threshold",
        }

    def tool_what_if(
        self,
        legs: list[dict[str, Any]],
        add: dict[str, Any] | None = None,
        remove: list[int] | None = None,
        date: str | None = None,
        ask: float | None = None,
    ) -> dict[str, Any]:
        sl = self._need_games(self._date(date))
        base_legs = self._legs_from(legs, sl)
        for i in remove or []:
            if i >= len(base_legs):
                raise ToolError(f"remove index {i} out of range for {len(base_legs)} legs")
        if add is None and not remove:
            raise ToolError("give 'add' and/or 'remove'")
        new_legs = [leg for i, leg in enumerate(base_legs) if i not in set(remove or [])]
        if add is not None:
            new_legs.append(self._parse_leg(add, sl))
        if not new_legs:
            raise ToolError("the change leaves no legs")
        if len(set(new_legs)) != len(new_legs):
            raise ToolError("duplicate legs after change")
        a = self._price_legs(base_legs, sl, ask)
        b = self._price_legs(new_legs, sl, ask)

        def delta(k: str) -> float | None:
            x, y = a.get(k), b.get(k)
            return None if x is None or y is None else float(y) - float(x)

        return {
            "before": a,
            "after": b,
            "delta": {
                k: delta(k)
                for k in (
                    "joint_prob_if_all_play",
                    "independent_prob_if_all_play",
                    "correlation_effect",
                    "model_prob",
                    "price",
                    "ev",
                    "ev_low",
                )
            },
        }

    def tool_track_record(self, stat: str = "all") -> dict[str, Any]:
        out: dict[str, Any] = {"stat": stat, "min_settled": self.cfg.min_settled}
        if not self.paths.paper_db.exists():
            return out | {
                "n_settled": 0,
                "insufficient_sample": True,
                "message": _insufficient(0, self.cfg.min_settled),
            }
        con = duckdb.connect(str(self.paths.paper_db), read_only=True)
        try:
            try:
                rows = con.execute(
                    "SELECT legs, raw_model_prob, market_mid, outcome FROM shadow_predictions "
                    "WHERE settled AND outcome IS NOT NULL AND side = 'yes'"
                ).fetchall()
                n_open = con.execute(
                    "SELECT count(*) FROM shadow_predictions WHERE NOT settled"
                ).fetchone()
                trades = con.execute(
                    "SELECT legs, expected_value, realized_pnl FROM paper_trades "
                    "WHERE settled AND realized_pnl IS NOT NULL"
                ).fetchall()
            except duckdb.CatalogException:
                rows, n_open, trades = [], (0,), []
        finally:
            con.close()

        def keep(legs_json: str) -> bool:
            return stat == "all" or all(x.get("stat") == stat for x in json.loads(legs_json))

        sel = [r for r in rows if keep(r[0])]
        n = len(sel)
        out |= {"n_settled": n, "n_open_unsettled": int(n_open[0]) if n_open else 0}
        if n:
            hit = sum(1 for r in sel if r[3]) / n
            lo, hi = _wilson(sum(1 for r in sel if r[3]), n)
            out |= {
                "mean_model_prob": sum(r[1] for r in sel) / n,
                "mean_market_mid": sum(r[2] for r in sel) / n,
                "observed_hit_rate": hit,
                "observed_hit_rate_ci95": [lo, hi],
            }
        if n < self.cfg.min_settled:
            out |= {
                "insufficient_sample": True,
                "message": _insufficient(n, self.cfg.min_settled),
            }
        else:
            bm = sum((r[1] - float(r[3])) ** 2 for r in sel) / n
            bk = sum((r[2] - float(r[3])) ** 2 for r in sel) / n
            out |= {
                "insufficient_sample": False,
                "brier_model": bm,
                "brier_market": bk,
                "skill_vs_market": (1.0 - bm / bk) if bk > 0 else None,
            }
        st = [t for t in trades if keep(t[0])]
        out["paper_trades_settled"] = len(st)
        if st:
            out["paper_expected_value_sum"] = sum(float(t[1]) for t in st)
            out["paper_realized_pnl_sum"] = sum(float(t[2]) for t in st)
        return out

    def tool_log_paper_trade(
        self, legs: list[dict[str, Any]], confirm: bool, date: str | None = None
    ) -> dict[str, Any]:
        if not self.allow_paper_log:
            raise ToolError("paper logging is disabled (start with --allow-paper-log)")
        if not confirm:
            raise ToolError("confirm must be true; only log when the user explicitly asked")
        d = self._date(date)
        sl = self._need_games(d)
        parsed = self._legs_from(legs, sl)
        res = self._price_legs(parsed, sl, None)
        if not res["priced"] or res["price_basis"] == PRODUCT_BASIS:
            raise ToolError("only a single priced Kalshi contract can be paper-logged")
        rec = Recommendation(
            legs=[leg.to_dict() for leg in parsed],
            model_prob=res["model_prob"],
            prob_interval=(res["prob_interval"][0], res["prob_interval"][1]),
            price=res["price"],
            fees=res["fees"],
            ev=res["ev"],
            ev_low=res["ev_low"],
            verdict=res["verdict"],
        )
        tid = (
            "assistant|"
            + d.isoformat()
            + "|"
            + hashlib.sha1(json.dumps(rec.legs, sort_keys=True).encode()).hexdigest()[:12]
        )
        self.paths.paper_db.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self.paths.paper_db))
        try:
            ensure_schema(con)
            con.execute("DELETE FROM paper_trades WHERE trade_id = ? AND NOT settled", [tid])
            log_trade(con, rec, self.cfg.fee, trade_id=tid)
        finally:
            con.close()
        return {"logged": True, "trade_id": tid, "verdict": rec.verdict, "paper_only": True}


def _insufficient(n: int, k: int) -> str:
    return f"insufficient sample: n={n} settled < min_settled={k}; no claim about skill is made"


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


__all__ = ["NOT_EVALUABLE", "PRODUCT_BASIS", "EnginePaths", "ToolError", "Toolbox"]
