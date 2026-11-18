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
grid at the main line equals ``line_cal.p_over_cal`` (checked: ``combo_night.validate_payload`` runs on
every payload before it is written, and a payload with an error is not written). Nothing is refit; no
network; no credential. The game model is ``data/parlay/joint_model.json`` (five numbers fitted on
seasons <= 2024) and its as-of total feature is built from the live season's games only
(``history_rows``, the ``games`` table; season 2025 is never read here). That ``mu_t`` is the number
GAME_SCRIPTS_FORWARD scores.

This is the ONE producer of ``combo_<night>.json``. ``nba.parlay.combo_night`` is its offline rebuild
command (from a copy of ``export.duckdb``) and calls ``build`` below; the season-2026 script history
(``hist``) is ``nba.parlay.combo_hist``.

Known-at: a game block uses rows stamped before its tip (and, offline, at or before ``asof``); the
block's ``made_at`` is the newest stamp of every input it used (prop rows, the win row, P(plays) rows),
and a main line is kept only if its pair is stamped at or before that ``made_at``.
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
from nba.parlay import combo_hist as ch
from nba.parlay import game_scripts as GS
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts

log = logging.getLogger(__name__)

SCHEMA = "game_scripts_v1"
COMBO_PARAMS = Path(__file__).resolve().parents[1] / "parlay" / "combo_params_v1.json"
COMBO_EVAL = Path(__file__).resolve().parents[1] / "parlay" / "combo_eval.py"
GRID_N = 199  # the production grid resolution; other resolutions are used as stored and counted
HISTORY_GAMES = "games table (combo_stage.history_rows)"
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
    "verified fee object; the unconditional probability, never the script one. The paper "
    "track record is large but shows no skill yet, so the model defers to the market: the engine shrinks to "
    "the market and the verdict is no_positive_ev_found. Shown: only the conservative ev. Not a stake."
)


@dataclass
class StageResult:
    combo: dict[str, Any] | None
    scripts: dict[str, Any] | None
    info: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    #: (game, player, stat) -> the export's ``line_cal.p_over_cal`` for every exported main line (what
    #: the validator compared the calibrated grid against)
    expected: dict[tuple[str, int, str], float | None] = field(default_factory=dict)


def absent(reason: str, **extra: Any) -> StageResult:
    return StageResult(None, None, {"status": "absent", "reason": reason, **extra})


def _iso_z(ts: datetime) -> str:
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _sha8(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()[:8]


def provenance(
    cal: lc.CalMap, params: Mapping[str, Any], params_bytes: bytes, code_version: str | None
) -> dict[str, Any]:
    """The contract's provenance block (combo_export_v1 section 2): ``combo_params_sha8`` is the sha of
    the params FILE (the pin GAME_SCRIPTS_FORWARD checks), ``rule_sha8`` the sha of ``combo_eval.py``'s
    docstring (the evaluation rule), ``rule_commit`` the commit that produced the params."""
    import ast

    doc = ast.get_docstring(ast.parse(COMBO_EVAL.read_text()), clean=False) or ""
    return {
        "map": lc.RULE,
        "map_sha8": str(cal.meta.get("map_sha256", ""))[:8],
        "fit_through": str(cal.meta.get("fit_through", "")),
        "corr_fit_seasons": params["corr_fit_seasons"],
        "corr_n_games": params["n_games_corr"],
        "combo_params_sha8": _sha8(params_bytes),
        "rule_sha8": _sha8(doc.encode()),
        "rule_commit": params["eval_rule_commit"],
        "code_version": code_version,
    }


def load_shared(path: Path, cal: lc.CalMap, code_version: str | None) -> dict[str, Any]:
    raw = path.read_bytes()
    d = json.loads(raw)
    return cb.build_shared(
        d["corr"], d["script_params"], d["script_status"], provenance(cal, d, raw, code_version),
        d["definitions"],
    )  # fmt: skip


def _pretip(
    rows: Sequence[Mapping[str, Any]], tips: Mapping[str, datetime], asof: datetime | None
) -> list[Mapping[str, Any]]:
    """Rows stamped before their game's tip and, when ``asof`` is given (the offline rebuild), at or
    before it. The export's reader already drops post-tip rows; this repeats it where the rows are used."""
    out: list[Mapping[str, Any]] = []
    for r in rows:
        tip = r.get("tipoff") or tips.get(str(r["game_id"]))
        made = r.get("made_at")
        if tip is None or made is None or not made < tip:
            continue
        if asof is not None and made > asof:
            continue
        out.append(r)
    return out


def _win_p(win_probs: Sequence[Mapping[str, Any]]) -> dict[str, tuple[float, str, datetime]]:
    """game_id -> (p_home, model, made_at): the production win row (injury-Elo, else MOV-Elo), newest
    pre-tip (GAME_SCRIPTS_FORWARD rule line 1 takes the same injury-Elo row)."""
    best: dict[str, tuple[tuple[int, datetime, str], float, str, datetime]] = {}
    for w in win_probs:
        if w.get("comparison_only") or w.get("p_home") is None or w["model_name"] not in WIN_MODELS:
            continue
        key = (-WIN_MODELS.index(w["model_name"]), w["made_at"], str(w["run_id"]))
        g = str(w["game_id"])
        if g not in best or key > best[g][0]:
            best[g] = (key, float(w["p_home"]), str(w["model_name"]), w["made_at"])
    return {g: (v[1], v[2], v[3]) for g, v in best.items()}


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


def calibrated(
    prod: Mapping[tuple[str, int, str], Mapping[str, Any]],
    cal: lc.CalMap,
    reasons: dict[str, dict[str, int]],
) -> dict[tuple[str, int, str], tuple[np.ndarray, np.ndarray, tuple[int, int], float]]:
    """key -> (calibrated survival row, raw row, (cal_lo, cal_hi), starter rate). Rows are calibrated in
    groups of equal grid length, so a row's rungs are its own (no zero padding from another player's
    longer grid). The raw survival is ``counts / n`` with the row's OWN ``n`` (as ``line_cal`` computes
    ``p_over``), so a 10,000-sample row is used as stored and counted in ``reasons``. A row missing an
    input is counted per game, never guessed."""
    groups: dict[tuple[str, int], list[tuple[Any, ...]]] = defaultdict(list)
    for key, row in sorted(prod.items()):
        game, _pid, stat = key
        if stat not in STATS:
            continue
        counts, n = row.get("p_ge_full_counts") or [], int(row.get("p_ge_full_n") or 0)
        if not len(counts) or not n:
            reasons[game]["no_survival_grid"] += 1
            continue
        sr = rcf._starter_rate(dict(row))
        if sr is None:
            reasons[game]["no_starter_rate"] += 1
            continue
        qs = ts.quartiles({"pred": _pred(row)})
        if qs is None:
            reasons[game]["no_quartiles"] += 1
            continue
        if n != GRID_N:
            reasons[game][f"grid_n_{n}_used"] += 1
        raw = np.asarray(counts, dtype=float) / float(n)
        groups[(stat, len(raw))].append((key, raw, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, sr))
    out: dict[tuple[str, int, str], tuple[np.ndarray, np.ndarray, tuple[int, int], float]] = {}
    for (stat, _k_len), items in sorted(groups.items()):
        raw_m = np.vstack([it[1] for it in items])
        med, spr, st10 = (np.asarray([it[j] for it in items], dtype=float) for j in (2, 3, 4))
        surv, lo, hi = cb.calibrate_surv_banded(cal, stat, raw_m, med, spr, st10)
        for i, it in enumerate(items):
            out[it[0]] = (surv[i], raw_m[i], (int(lo[i]), int(hi[i])), float(st10[i]))
    return out


def _pred(row: Mapping[str, Any]) -> dict[str, Any]:
    from nba.export.line_cal_fields import _pred as p

    return p(dict(row))


def _pplay_of(pplay: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], tuple[datetime, float]]:
    newest: dict[tuple[str, int], tuple[datetime, float]] = {}
    for r in pplay:
        v = r.get("p_play_primary")
        if v is None:
            continue
        k = (str(r["game_id"]), int(r["player_id"]))
        if k not in newest or r["made_at"] > newest[k][0]:
            newest[k] = (r["made_at"], float(v))
    return newest


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
    tmp_dir: Path | None = None
    try:
        for _seq, _name, f in con.execute("PRAGMA database_list").fetchall():
            if f and Path(str(f)).resolve() != Path(path).resolve():
                tmp_dir = Path(str(f)).resolve().parent  # the private copy of a locked store
        rows = con.execute(
            "SELECT m.ticker, m.series_ticker, m.event_ticker, m.title, m.player_id, m.stat, "
            "m.threshold, p.yes_bid, p.yes_ask, p.ts FROM kalshi_markets m "
            "LEFT JOIN (SELECT ticker, arg_max(yes_bid, ts) yes_bid, arg_max(yes_ask, ts) yes_ask, "
            "max(ts) ts FROM kalshi_prices GROUP BY ticker) p USING (ticker) WHERE m.result IS NULL"
        ).fetchall()
    finally:
        con.close()
        if tmp_dir is not None and tmp_dir.name.startswith("market_ro_"):
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)  # never leave 100 MB behind
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
        return {
            "ticker": q["ticker"], "threshold": k, "ask": round(float(q["ask"]), 4),
            "bid": None if q["bid"] is None else round(float(q["bid"]), 4),
            "spread": None if rec.spread is None else round(rec.spread, 4),
            "price_ts": q["price_ts"], "fee": round(rec.fees, 4),
            "p_over": round(p_over, 4), "p_model_incl_dnp": round(joint, 4),
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
    box_rows: Sequence[Mapping[str, Any]] | None = None,
    asof: datetime | None = None,
    history_input: str = HISTORY_GAMES,
) -> StageResult:
    """The two payloads for ``day``. ``props`` are the export's prop rows after ``team_id`` and
    ``line_cal`` were set. ``hist_rows``: ``history_rows`` of the live season (the ``mu_t`` input and the
    past games of ``hist``). ``box_rows``: season-2026 box rows for the players' game logs (``hist``);
    None leaves ``hist`` out. ``asof``: the offline rebuild's cut (None in the nightly export, whose
    reader already holds only rows stamped before now and before tip)."""
    from nba.parlay import combo_night as cn
    from nba.parlay.game_model import GameModelParams

    cal = cal if cal is not None else lc.load_map()
    shared = load_shared(params_path, cal, code_version)
    gm = GameModelParams.from_json(
        json.dumps(json.loads((game_model_path or GAME_MODEL).read_text())["params"])
    )
    tips = {str(g["game_id"]): g["tipoff"] for g in games if g.get("tipoff") is not None}
    prod = rcf._production_rows([dict(p) for p in _pretip(props, tips, asof)])
    reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cal_rows = calibrated(prod, cal, reasons)
    p_play = _pplay_of(_pretip(pplay, tips, asof))
    win = _win_p(_pretip(win_probs, tips, asof))
    frame = ch.hist_frame(hist_rows) if hist_rows else None
    sits = sits_block(sits_rows)
    notes: list[str] = []
    by_game: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for gid, pid, stat in cal_rows:
        by_game[gid].append((pid, stat))
    out_games: list[dict[str, Any]] = []
    expected: dict[tuple[str, int, str], float | None] = {}
    skipped: dict[str, int] = defaultdict(int)
    blog: dict[str, Any] = {
        "producer": "nba.export.combo_stage", "asof": None if asof is None else _iso_z(asof),
        "history_input": history_input, "games": [], "n_players": 0, "n_stat_entries": 0,
    }  # fmt: skip
    for g in games:
        gid = str(g["game_id"])
        if g.get("game_date") != day:
            continue
        entry: dict[str, Any] = {"game_id": gid, "tip_utc": _iso_z(g["tipoff"])}
        home, away = g.get("home_team"), g.get("away_team")
        if home is None or away is None or gid not in win or gid not in by_game:
            skipped["no_win_row_or_teams_or_props" if gid not in win else "no_props"] += 1
            why = "no_teams" if home is None or away is None else None
            why = why or ("no_win_row_before_tip" if gid not in win else "no_production_props")
            blog["games"].append({**entry, "status": "left_out", "reason": why})
            continue
        mu_t, mu_t_src = ch.game_mu_t(frame, gm, int(home), int(away), day)
        p_home, win_model, win_at = win[gid]
        mu_m = gm.margin_mu(p_home)
        players: dict[int, dict[str, Any]] = {}
        stamps: list[datetime] = [win_at]
        rows_of: dict[tuple[int, str], Mapping[str, Any]] = {}
        for pid, stat in sorted(by_game[gid]):
            row = prod[(gid, pid, stat)]
            tid = row.get("team_id")
            if tid is None or int(tid) not in (int(home), int(away)):
                skipped["player_team_not_in_game"] += 1
                reasons[gid]["player_team_not_in_game"] += 1
                continue
            pp_row = p_play.get((gid, pid))
            pp = None if pp_row is None else pp_row[1]
            if pp is None and row.get("p_play") is not None:
                pp = float(row["p_play"])
            if pp is not None and pp < MIN_P_PLAY_EXPORT:
                reasons[gid]["p_play_below_0.02"] += 1
                continue
            if pp_row is not None:
                stamps.append(pp_row[0])
            surv, raw, ck, sr = cal_rows[(gid, pid, stat)]
            if pid not in names:
                reasons[gid]["no_player_name"] += 1
            ent = players.setdefault(
                pid,
                {"player_id": pid, "name": names.get(pid, f"Player {pid}"), "team": int(tid),
                 "p_play": 0.0 if pp is None else pp, "starter": 1 if sr >= 0.5 else 0, "stats": {}},
            )  # fmt: skip
            if pp is None:
                ent["p_play"] = None
            ent["stats"][stat] = {"cal": surv, "raw": raw, "main_line": None, "cal_k": ck}
            rows_of[(pid, stat)] = row
            stamps.append(row["made_at"])
        if not players:
            skipped["no_players"] += 1
            blog["games"].append({**entry, "status": "left_out", "reason": "no_players"})
            continue
        block_made = max(stamps)
        for (pid, stat), prow in rows_of.items():
            ml, why_ml = main_line(prow, block_made)
            if why_ml:
                reasons[gid]["main_line_null:" + why_ml] += 1
                continue
            players[pid]["stats"][stat]["main_line"] = ml
            expected[(gid, pid, stat)] = _p_over_cal(prow)
        payload = cb.build_game_payload(
            game_id=gid, date=day.isoformat(), home_team=int(home), away_team=int(away),
            tip_utc=_iso_z(g["tipoff"]), made_at=_iso_z(block_made),
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
        payload["win_model"] = win_model
        payload["mu_t_source"] = mu_t_src
        if gid in sits:
            payload["sits"] = sits[gid]
        out_games.append(payload)
        n_stat = sum(len(p["stats"]) for p in players.values())
        blog["n_players"] += len(players)
        blog["n_stat_entries"] += n_stat
        blog["games"].append({
            **entry, "status": "built", "made_at": payload["made_at"], "win_prob_model": win_model,
            "n_players": len(players), "n_stat_entries": n_stat, "mu_t_source": mu_t_src,
            "reasons": dict(sorted(reasons[gid].items())),
        })  # fmt: skip
    if not out_games:
        return absent("no_games_built", skipped=dict(skipped))
    combo = cb.nightly(shared, out_games, day.isoformat())
    combo["generated_at"] = _iso_z(made_at)
    if box_rows is not None:
        ids = sorted({int(p["player_id"]) for g in combo["games"] for p in g["players"]})
        logs = ch.player_logs(box_rows, day, ids)
        blog["hist"] = ch.attach_hist(combo, frame, logs, gm, shared["scripts"]["definitions"], day)
    else:
        blog["hist"] = {"status": "no_box_rows"}
    combo["build_log"] = blog
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
    budget = enforce_budget(combo, scripts)
    check = cn.validate_payload(
        combo, expected=expected, check_size=False,
        raw_at_main_dropped=bool(budget.get("dropped_raw_at_main")),
    )  # fmt: skip
    validation = {
        "errors": len(check["errors"]), "main_line_checked": check["n_checked"]["main_line_checked"],
        "max_abs_diff": None if check["max_abs_diff"] is None else round(check["max_abs_diff"], 7),
    }  # fmt: skip
    if check["errors"]:
        return absent(
            "validation_failed", validation=validation, first_errors=check["errors"][:10],
            skipped=dict(skipped),
        )  # fmt: skip
    mu_src: dict[str, int] = defaultdict(int)
    for g in out_games:
        mu_src[str(g["mu_t_source"])] += 1
    info = {
        "budget": budget, "mu_t_source": dict(mu_src), "status": "ok", "n_games": len(out_games), "n_players": sum(len(g["players"]) for g in out_games),
        "n_priced_picks": n_priced, "verdicts": dict(verdicts), "skipped": dict(skipped),
        "n_asks": len(asks), "sits_games": len(sits), "validation": validation,
        "n_main_lines": sum(1 for v in expected.values() if v is not None),
        "hist": {k: blog["hist"][k] for k in ("n_entries", "past_games") if k in blog["hist"]},
    }  # fmt: skip
    return StageResult(combo, scripts, info, notes, expected)


COMBO_BUDGET = 300_000  # bytes raw per night (combo_export_v1 section 4)
PICKS_BUDGET = 150_000


def _size(obj: Any) -> int:
    return len(dumps(obj)) + 1


def enforce_budget(combo: dict[str, Any], scripts: dict[str, Any]) -> dict[str, Any]:
    """Keep the files inside their budgets and SAY what was cut. Combo: drop ``raw_at_main``, then trim the
    survival tails below 0.001 (the contract's own list). Picks: drop the lowest-ranked pairs first (lowest
    ``p_joint_script``), across games and scripts."""
    out: dict[str, Any] = {"combo_bytes_before": _size(combo), "picks_bytes_before": _size(scripts)}
    if out["combo_bytes_before"] > COMBO_BUDGET:
        for g in combo["games"]:
            for pl in g["players"]:
                for e in pl["stats"].values():
                    e.pop("raw_at_main", None)
        out["dropped_raw_at_main"] = True
    if _size(combo) > COMBO_BUDGET:
        for g in combo["games"]:
            for pl in g["players"]:
                for e in pl["stats"].values():
                    while e["surv"] and e["surv"][-1] < 0.001:
                        e["surv"].pop()
        out["trimmed_surv_below"] = 0.001
    n = 0
    size = _size(scripts)
    while size > PICKS_BUDGET:
        worst: tuple[float, dict[str, Any]] | None = None
        for g in scripts["games"]:
            for blk in g["picks"].values():
                if blk["pairs"]:
                    cand = blk["pairs"][-1]
                    if worst is None or cand["p_joint_script"] < worst[0]:
                        worst = (cand["p_joint_script"], blk)
        if worst is None:
            break
        pair = worst[1]["pairs"].pop()
        size -= _size(pair)
        n += 1
    out["pairs_dropped_for_size"] = n
    out["combo_bytes"], out["picks_bytes"] = _size(combo), _size(scripts)
    out["over_budget"] = out["combo_bytes"] > COMBO_BUDGET or out["picks_bytes"] > PICKS_BUDGET
    return out


def _parse_ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def _line_cal(row: Mapping[str, Any]) -> dict[str, Any] | None:
    lcal = row.get("line_cal")
    if isinstance(lcal, str) and lcal:
        lcal = json.loads(lcal)
    return lcal if isinstance(lcal, dict) else None


def main_line(row: Mapping[str, Any], made_at: datetime) -> tuple[float | None, str | None]:
    """(main_line, None) or (None, reason): the row's stored ``line_cal.main_line``, kept only if its pair
    is stamped (``price_at``) at or before the block's ``made_at``. The export copies one prop's
    ``line_cal`` onto every row of that prop, so an older row (offline, under an earlier ``asof``) can
    carry a line its own run never saw: this check drops it."""
    lcal = _line_cal(row)
    if lcal is None or lcal.get("main_line") is None:
        return None, str(row.get("line_cal_null_reason") or "no_line_cal")
    at = _parse_ts(lcal.get("price_at"))
    if at is None:
        return None, "line_without_price_at"
    if at > made_at.replace(tzinfo=None):
        return None, "line_after_made_at"
    return float(lcal["main_line"]), None


def _p_over_cal(row: Mapping[str, Any]) -> float | None:
    lcal = _line_cal(row)
    v = None if lcal is None else lcal.get("p_over_cal")
    return None if v is None else float(v)


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
