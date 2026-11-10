# ruff: noqa: E501
"""The nightly game-scripts step of the export (``docs/contracts/game_scripts_v1.md``).

Two files per night, written beside ``export.duckdb`` by ``nba.export.build`` inside ONE guard (a
failure here is a manifest note, never a failed export), from the rows the export already holds:

* ``combo_<night>.json``: ``combo_export_v1`` (price-free) plus additive per-game keys (``home_abbr``,
  ``away_abbr``, ``scripts``, ``sits``). Nothing market-derived except the posted main LINE.
* ``game_scripts_<night>.json``: best picks per script; the ONLY file with a ``market`` block, and
  only where a current Kalshi YES ask exists for the leg. EV comes from ``nba.parlay.ev.evaluate``
  (the fee object of ``configs/parlay.yaml``), never from a second formula, and from the
  UNCONDITIONAL probability.

Inputs are the production (``props_context_residual``) rows only, as ``line_cal`` uses; the calibrated
grid at the main line equals ``line_cal.p_over_cal``. Nothing is refit; no network; no credential.
The game model is ``data/parlay/joint_model.json`` (five numbers fitted on seasons <= 2024) and its
as-of total feature is built from the live season's games only (season 2025 is never read here).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from nba.export import rung_cal_fields as rcf
from nba.parlay import combo as C
from nba.parlay import combo_build as cb
from nba.parlay import game_scripts as GS
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts

log = logging.getLogger(__name__)

SCHEMA = "game_scripts_v1"
COMBO_PARAMS = Path(__file__).resolve().parents[1] / "parlay" / "combo_params_v1.json"
GAME_MODEL = Path(__file__).resolve().parents[2] / "data" / "parlay" / "joint_model.json"
LOCK_FROM = date(
    2026, 10, 30
)  # LINE_CAL_LIVE: no prop prediction next to an outcome from this slate
LOCK_NOTE = (
    "LINE_CAL_LIVE (DECISIONS 2026-11-10 02:51): for slates from 2026-10-30 the app must not show a prop "
    "prediction next to its outcome; this file holds no outcome and no raw probability."
)
STATS = ("pts", "reb", "ast", "fg3m")
WIN_MODELS = ("rung0_injury_elo", "rung0_mov_elo")
SITS_PTS_MIN = 0.05  # |delta| below this is not exported (sub-0.05-point shifts are rounding)
MIN_P_PLAY_EXPORT = 0.02  # a player this unlikely to play is not exported (size)
MARKET_NOTE = (
    "EV = P(plays) * P(over | plays) - ask - fee per 1 contract, the parlay engine's evaluate() with the "
    "verified fee object; the unconditional probability, never the script one. Without a settled track "
    "record the engine shrinks to the market and the verdict is no_positive_ev_found. Not a stake."
)


@dataclass
class StageResult:
    combo: dict[str, Any] | None
    scripts: dict[str, Any] | None
    info: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def absent(reason: str, **extra: Any) -> StageResult:
    return StageResult(None, None, {"status": "absent", "reason": reason, **extra})


def _iso_z(ts: datetime) -> str:
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def load_shared(path: Path, cal: lc.CalMap, code_version: str | None) -> dict[str, Any]:
    d = json.loads(path.read_text())
    prov = {
        "map": lc.RULE,
        "map_sha8": str(cal.meta.get("map_sha256", ""))[:8],
        "fit_through": str(cal.meta.get("fit_through", "")),
        "corr_fit_seasons": d["corr_fit_seasons"],
        "corr_n_games": d["n_games_corr"],
        "combo_params_sha8": str(d["sha256"])[:8],
        "rule_commit": d["eval_rule_commit"],
        "code_version": code_version,
    }
    return cb.build_shared(
        d["corr"], d["script_params"], d["script_status"], prov, d["definitions"]
    )


def _win_p(win_probs: Sequence[Mapping[str, Any]]) -> dict[str, tuple[float, str]]:
    """game_id -> (p_home, model): the production win row (injury-Elo, else MOV-Elo), newest pre-tip."""
    best: dict[str, tuple[tuple[int, datetime, str], float, str]] = {}
    for w in win_probs:
        if w.get("comparison_only") or w.get("p_home") is None or w["model_name"] not in WIN_MODELS:
            continue
        key = (-WIN_MODELS.index(w["model_name"]), w["made_at"], str(w["run_id"]))
        g = str(w["game_id"])
        if g not in best or key > best[g][0]:
            best[g] = (key, float(w["p_home"]), str(w["model_name"]))
    return {g: (v[1], v[2]) for g, v in best.items()}


def _hist_frame(rows: Sequence[Sequence[Any]]) -> Any:
    import polars as pl

    return pl.DataFrame(
        {
            "game_id": [str(r[0]) for r in rows],
            "game_date": [r[1] for r in rows],
            "home_team": [int(r[2]) for r in rows],
            "away_team": [int(r[3]) for r in rows],
            "home_pts": [float(r[4]) for r in rows],
            "away_pts": [float(r[5]) for r in rows],
        },
        schema={
            "game_id": pl.Utf8, "game_date": pl.Date, "home_team": pl.Int64,
            "away_team": pl.Int64, "home_pts": pl.Float64, "away_pts": pl.Float64,
        },
    )  # fmt: skip


def history_rows(src: Any, day: date, season: int) -> list[tuple[Any, ...]]:
    """Completed games of the live season dated before ``day`` (the as-of total feature's input);
    season 2025 is never read: the season filter is in the SQL."""
    return list(
        src.execute(
            "SELECT game_id, game_date, home_team, away_team, home_pts, away_pts FROM games "
            "WHERE season = ? AND game_date < ? AND home_pts IS NOT NULL AND home_pts > 0 "
            "AND game_id NOT LIKE '001%' AND game_id NOT LIKE '003%' ORDER BY game_date, game_id",
            [season, day],
        ).fetchall()
    )


def _player_inputs(
    prod: Mapping[tuple[str, int, str], Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per stat: raw survival matrix, median, spread, starter rate, keys. Rows missing an input are
    counted, never guessed."""
    out: dict[str, dict[str, Any]] = {
        s: {"keys": [], "raw": [], "med": [], "spr": [], "st10": []} for s in STATS
    }
    for key, row in sorted(prod.items()):
        game, pid, stat = key
        if stat not in out:
            continue
        counts, n = row.get("p_ge_full_counts") or [], int(row.get("p_ge_full_n") or 0)
        sr = rcf._starter_rate(dict(row))
        qs = ts.quartiles({"pred": _pred(row)})
        if not counts or not n or sr is None or qs is None:
            continue
        a = out[stat]
        a["keys"].append(key)
        a["raw"].append(np.asarray(counts, dtype=float) / float(n))
        a["med"].append(qs[1])
        a["spr"].append((qs[2] - qs[0]) / lc.IQR_TO_SD)
        a["st10"].append(sr)
    return out


def _pred(row: Mapping[str, Any]) -> dict[str, Any]:
    from nba.export.line_cal_fields import _pred as p

    return p(dict(row))


def calibrated(
    inputs: dict[str, dict[str, Any]], cal: lc.CalMap
) -> dict[tuple[str, int, str], tuple[np.ndarray, np.ndarray, tuple[int, int], float]]:
    """key -> (calibrated survival row, raw row, (cal_lo, cal_hi), starter rate)."""
    out: dict[tuple[str, int, str], tuple[np.ndarray, np.ndarray, tuple[int, int], float]] = {}
    for stat, a in inputs.items():
        if not a["keys"]:
            continue
        k_max = max(len(r) for r in a["raw"])
        raw = np.zeros((len(a["keys"]), k_max))
        for i, r in enumerate(a["raw"]):
            raw[i, : len(r)] = r
        med, spr, st10 = (np.asarray(a[k], dtype=float) for k in ("med", "spr", "st10"))
        surv, lo, hi = cb.calibrate_surv_banded(cal, stat, raw, med, spr, st10)
        for i, key in enumerate(a["keys"]):
            out[key] = (surv[i], raw[i], (int(lo[i]), int(hi[i])), float(st10[i]))
    return out


def _pplay_of(pplay: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], float]:
    newest: dict[tuple[str, int], tuple[datetime, float]] = {}
    for r in pplay:
        v = r.get("p_play_primary")
        if v is None:
            continue
        k = (str(r["game_id"]), int(r["player_id"]))
        if k not in newest or r["made_at"] > newest[k][0]:
            newest[k] = (r["made_at"], float(v))
    return {k: v[1] for k, v in newest.items()}


def sits_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """game_id -> the who-sits support: pts numbers and reb direction only (the display rule of
    T526-a / teammate_sits_v1: pts HELPS, reb HELPS wounded = direction, ast / fg3m never)."""
    slots: dict[str, dict[tuple[int, int], dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        stat, d = str(r["stat"]), r.get("delta_mean")
        if stat not in ("pts", "reb") or d is None or abs(float(d)) < SITS_PTS_MIN:
            continue
        slot = slots[str(r["game_id"])].setdefault(
            (int(r["team_id"]), int(r["sitter_id"])),
            {"status": str(r.get("sitter_status") or ""), "pts": [], "reb_dir": []},
        )
        if stat == "pts":
            slot["pts"].append([int(r["player_id"]), round(float(d), 2)])
        else:
            slot["reb_dir"].append([int(r["player_id"]), 1 if float(d) > 0 else -1])
    out: dict[str, dict[str, Any]] = {}
    for g, sitters in slots.items():
        out[g] = {
            "rule": {"pts": "HELPS", "reb": "HELPS (direction only; size uncertain)"},
            "sitters": [
                {"sitter": sitter, "team": team, "status": v["status"], "pts": sorted(v["pts"]),
                 "reb_dir": sorted(v["reb_dir"])}
                for (team, sitter), v in sorted(sitters.items())
            ],
        }  # fmt: skip
    return out


MAX_ASK_AGE_MIN = 60.0  # a quote older than this at build time is not shown as a current ask


def read_markets(path: Path) -> list[dict[str, Any]]:
    """Open Kalshi markets with their latest price (read-only; the engine's ``load_open_markets`` SQL).
    A locked store is retried, then read through a private copy (``nba.markets.asof``)."""
    from nba.markets.asof import open_read_only

    con = open_read_only(path, copy_fallback=True)
    try:
        rows = con.execute(
            "SELECT m.ticker, m.series_ticker, m.event_ticker, m.title, m.player_id, m.stat, "
            "m.threshold, p.yes_bid, p.yes_ask, p.ts FROM kalshi_markets m "
            "LEFT JOIN (SELECT ticker, arg_max(yes_bid, ts) yes_bid, arg_max(yes_ask, ts) yes_ask, "
            "max(ts) ts FROM kalshi_prices GROUP BY ticker) p USING (ticker) WHERE m.result IS NULL"
        ).fetchall()
    finally:
        con.close()
    cols = ("ticker", "series_ticker", "event_ticker", "title", "player_id", "stat", "threshold",
            "yes_bid", "yes_ask", "ts")  # fmt: skip
    return [dict(zip(cols, r, strict=True)) for r in rows]


def ask_index(
    markets: Sequence[Mapping[str, Any]],
    abbr: Mapping[str, int],
    day: date,
    games: Sequence[Mapping[str, Any]],
    props: Sequence[Mapping[str, Any]],
    now: datetime,
) -> tuple[dict[tuple[str, int, str, int], dict[str, Any]], dict[str, int]]:
    """(game, player, stat, k) -> Kalshi YES quote, through ``kalshi_map.map_market`` (the engine's own
    mapping; an unmapped market is dropped, never guessed) and the counts of what was left out. A quote
    must be stamped before its game's tip and at most ``MAX_ASK_AGE_MIN`` before ``now``."""
    from nba.parlay.kalshi_map import MarketRow, map_market

    keys = {
        (day, int(g["away_team"]), int(g["home_team"])): str(g["game_id"])
        for g in games
        if g.get("game_date") == day
        and g.get("home_team") is not None
        and g.get("away_team") is not None
    }
    tips = {str(g["game_id"]): g["tipoff"] for g in games}
    team_of = {
        (str(p["game_id"]), int(p["player_id"])): int(p["team_id"])
        for p in props
        if p.get("team_id") is not None
    }
    out: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    left: dict[str, int] = defaultdict(int)
    for m in markets:
        row = MarketRow.from_row(dict(m))
        leg, why = map_market(row, dict(abbr), keys, team_of)
        if leg is None:
            left[why or "unmapped"] += 1
            continue
        if leg.side != "yes" or leg.stat not in STATS or leg.player_id == 0:
            continue
        if row.yes_ask is None or not 0.0 < row.yes_ask < 1.0 or row.price_ts is None:
            left["no_ask"] += 1
            continue
        k = int(leg.threshold)
        ts_ = datetime.fromisoformat(str(row.price_ts)).replace(tzinfo=None)
        tip = tips[leg.game_id].replace(tzinfo=None)
        if abs(leg.threshold - k) > 1e-9:
            left["non_integer_threshold"] += 1
        elif ts_ >= tip:
            left["quote_after_tip"] += 1
        elif (now.replace(tzinfo=None) - ts_).total_seconds() > MAX_ASK_AGE_MIN * 60.0:
            left["quote_stale"] += 1
        else:
            out[(leg.game_id, leg.player_id, leg.stat, k)] = {
                "ticker": row.ticker, "series": row.series, "ask": row.yes_ask,
                "bid": row.yes_bid, "price_ts": _iso_z(ts_),
            }  # fmt: skip
    return out, dict(left)


def load_track(path: Path | None) -> Any:
    """The paper-trade track record (read-only); an absent store is the empty record, which keeps the
    engine on the market's side by construction."""
    from nba.parlay.shadow import TrackRecord, track_record

    empty = TrackRecord(0, None, None, 0.0)
    if path is None or not Path(path).exists():
        return empty
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        return track_record(con)
    except duckdb.CatalogException:
        return empty
    finally:
        con.close()


def make_market_fn(
    game_id: str,
    game: Mapping[str, Any],
    asks: Mapping[tuple[str, int, str, int], Mapping[str, Any]],
    cfg: Any,
    track: Any,
) -> GS.MarketFn:
    """The pick's ``market`` block for an over leg at Kalshi threshold ``k``: ``nba.parlay.ev.evaluate``
    on P(plays) * P(over | plays) (Kalshi's DNP rule is unverified, so a DNP counts as a miss, as the
    cards price it). None when there is no ask or no P(plays)."""
    from nba.parlay.ev import evaluate
    from nba.parlay.legs import Leg

    def fn(pid: int, stat: str, k: int) -> dict[str, Any] | None:
        q = asks.get((game_id, pid, stat, k))
        if q is None:
            return None
        pl = C._player(game, pid)
        pp = pl.get("p_play")
        if pp is None:
            return {"ticker": q["ticker"], "reason": "no_p_play", "verdict": None}
        p_over = C.surv_at(pl["stats"][stat], k)
        joint = float(pp) * p_over
        rec = evaluate(
            [Leg(game_id, pid, stat, float(k), "yes")], joint, 0, 0, float(q["ask"]), cfg,
            bid=None if q["bid"] is None else float(q["bid"]), engine="game_scripts",
            n_settled=int(track.n_settled), skill=float(track.skill), seed=20261110,
            series=str(q["series"]),
        )  # fmt: skip
        lo = rec.raw_ev_low + float(q["ask"]) + rec.fees
        return {
            "ticker": q["ticker"], "threshold": k, "ask": round(float(q["ask"]), 4),
            "bid": None if q["bid"] is None else round(float(q["bid"]), 4),
            "spread": None if rec.spread is None else round(rec.spread, 4),
            "price_ts": q["price_ts"], "fee": round(rec.fees, 4),
            "p_over": round(p_over, 4), "p_model_incl_dnp": round(joint, 4),
            "p_low": round(lo, 4), "ev_raw": round(rec.raw_ev, 4), "ev_raw_low": round(rec.raw_ev_low, 4),
            "ev": round(rec.ev, 4), "ev_low": round(rec.ev_low, 4), "verdict": rec.verdict,
            "model_weight": round(rec.model_weight, 4), "n_settled_track": rec.n_settled_track,
        }  # fmt: skip

    return fn


def build(
    day: date,
    games: Sequence[Mapping[str, Any]],
    win_probs: Sequence[Mapping[str, Any]],
    props: Sequence[Mapping[str, Any]],
    pplay: Sequence[Mapping[str, Any]],
    sits_rows: Sequence[Mapping[str, Any]],
    hist_rows: Sequence[Sequence[Any]],
    names: Mapping[int, str],
    team_abbr: Mapping[int, str],
    *,
    made_at: datetime,
    code_version: str | None = None,
    cal: lc.CalMap | None = None,
    params_path: Path = COMBO_PARAMS,
    game_model_path: Path | None = None,
    asks: Mapping[tuple[str, int, str, int], Mapping[str, Any]] | None = None,
    cfg: Any = None,
    track: Any = None,
) -> StageResult:
    """The two payloads for ``day``. ``props`` are the export's prop rows after ``team_id`` was set."""
    from nba.parlay.game_model import GameModelParams, slate_total_feature

    cal = cal if cal is not None else lc.load_map()
    shared = load_shared(params_path, cal, code_version)
    gm = GameModelParams.from_json(
        json.dumps(json.loads((game_model_path or GAME_MODEL).read_text())["params"])
    )
    prod = rcf._production_rows([dict(p) for p in props])
    cal_rows = calibrated(_player_inputs(prod), cal)
    p_play = _pplay_of(pplay)
    win = _win_p(win_probs)
    hist = _hist_frame(hist_rows) if hist_rows else None
    sits = sits_block(sits_rows)
    notes: list[str] = []
    by_game: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for gid, pid, stat in cal_rows:
        by_game[gid].append((pid, stat))
    out_games: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)
    for g in games:
        gid = str(g["game_id"])
        if g.get("game_date") != day:
            continue
        home, away = g.get("home_team"), g.get("away_team")
        if home is None or away is None or gid not in win or gid not in by_game:
            skipped["no_win_row_or_teams_or_props" if gid not in win else "no_props"] += 1
            continue
        feat = None
        if hist is not None:
            feat = slate_total_feature(hist, gm, int(home), int(away), day)
        mu_t = gm.total_mu(feat)
        mu_m = gm.margin_mu(win[gid][0])
        players: dict[int, dict[str, Any]] = {}
        newest: datetime | None = None
        for pid, stat in sorted(by_game[gid]):
            row = prod[(gid, pid, stat)]
            tid = row.get("team_id")
            if tid is None or int(tid) not in (int(home), int(away)):
                skipped["player_team_not_in_game"] += 1
                continue
            pp = p_play.get((gid, pid))
            if pp is None and row.get("p_play") is not None:
                pp = float(row["p_play"])
            if pp is not None and pp < MIN_P_PLAY_EXPORT:
                continue
            surv, raw, ck, sr = cal_rows[(gid, pid, stat)]
            ml = _main_line(row)
            ent = players.setdefault(
                pid,
                {"player_id": pid, "name": names.get(pid, f"Player {pid}"), "team": int(tid),
                 "p_play": 0.0 if pp is None else pp, "starter": 1 if sr >= 0.5 else 0, "stats": {}},
            )  # fmt: skip
            if pp is None:
                ent["p_play"] = None
            ent["stats"][stat] = {"cal": surv, "raw": raw, "main_line": ml, "cal_k": ck}
            newest = row["made_at"] if newest is None else max(newest, row["made_at"])
        if not players:
            skipped["no_players"] += 1
            continue
        payload = cb.build_game_payload(
            game_id=gid, date=day.isoformat(), home_team=int(home), away_team=int(away),
            tip_utc=_iso_z(g["tipoff"]), made_at=_iso_z(newest or made_at),
            mu_m=mu_m, sd_m=gm.margin_sd, mu_t=mu_t, sd_t=gm.total_sd,
            players=[{**p, "p_play": 0.0 if p["p_play"] is None else p["p_play"]} for p in players.values()],
        )  # fmt: skip
        for pp_out, pr in zip(payload["players"], players.values(), strict=True):
            if pr["p_play"] is None:
                pp_out["p_play"] = None
        payload["home_abbr"] = team_abbr.get(int(home))
        payload["away_abbr"] = team_abbr.get(int(away))
        pri = GS.script_priors(payload, shared)
        rules = GS.script_rules(payload, shared)
        payload["scripts"] = {
            s: {"p": round(pri[s], 4), "rule": rules[s]["rule"], "status": GS.STATUS}
            for s in GS.SCRIPTS
        }
        payload["win_model"] = win[gid][1]
        payload["mu_t_source"] = "live-season games only" if hist is not None else "league fallback"
        if gid in sits:
            payload["sits"] = sits[gid]
        out_games.append(payload)
    if not out_games:
        return absent("no_games_built", skipped=dict(skipped))
    combo = cb.nightly(shared, out_games, day.isoformat())
    combo["generated_at"] = _iso_z(made_at)
    asks = asks or {}
    gs_games = []
    n_priced = 0
    verdicts: dict[str, int] = defaultdict(int)
    for gm_payload in out_games:
        gid = gm_payload["game_id"]
        ask_ks: dict[tuple[int, str], list[int]] = defaultdict(list)
        for g2, pid, stat, k in asks:
            if g2 == gid:
                ask_ks[(pid, stat)].append(k)
        mfn = (
            make_market_fn(gid, gm_payload, asks, cfg, track)
            if (cfg is not None and track is not None and asks)
            else None
        )
        picks = GS.best_picks(gm_payload, shared, market_fn=mfn, ask_ks=ask_ks)
        for s in GS.SCRIPTS:
            for leg in picks[s]["legs"]:
                m = leg.get("market")
                if m is not None:
                    n_priced += 1
                    verdicts[str(m.get("verdict"))] += 1
        gs_games.append({
            "game_id": gid, "home_team": gm_payload["home_team"], "away_team": gm_payload["away_team"],
            "home_abbr": gm_payload["home_abbr"], "away_abbr": gm_payload["away_abbr"],
            "tip_utc": gm_payload["tip_utc"], "made_at": gm_payload["made_at"],
            "pregame": gm_payload["game"], "picks": picks,
        })  # fmt: skip
    scripts = {
        "schema": SCHEMA, "night": day.isoformat(), "generated_at": _iso_z(made_at),
        "status": GS.STATUS, "lock": {"line_cal_live": day >= LOCK_FROM, "note": LOCK_NOTE},
        "market_note": MARKET_NOTE, "script_status": shared["scripts"]["status"],
        "definitions": shared["scripts"]["definitions"], "games": gs_games,
    }  # fmt: skip
    info = {
        "status": "ok", "n_games": len(out_games), "n_players": sum(len(g["players"]) for g in out_games),
        "n_priced_picks": n_priced, "verdicts": dict(verdicts), "skipped": dict(skipped),
        "n_asks": len(asks), "sits_games": len(sits),
    }  # fmt: skip
    return StageResult(combo, scripts, info, notes)


def _main_line(row: Mapping[str, Any]) -> float | None:
    lcal = row.get("line_cal")
    if isinstance(lcal, dict) and lcal.get("main_line") is not None:
        return float(lcal["main_line"])
    return None


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
