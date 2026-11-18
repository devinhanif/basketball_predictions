# ruff: noqa: E501
"""Nightly combo payload (``docs/contracts/combo_export_v1.md``) from the app export's own tables.

Read-only plumbing: it opens a COPY of ``export.duckdb`` with ``read_only=True`` and reads only the
production forecast rows (``props_context_residual``), the primary win probability and the stored
``line_cal`` main line of the requested slate date (season 2026 only; a date before the 2026-27 season
is refused before any query, because season 2025 is the frozen holdout). It reads no price column of
any table, applies the frozen LINE_CALIBRATION map through ``combo_build.calibrate_surv_banded`` and
changes none of the math, correlations, script parameters or the map. No orders, no credentials.

Known-at (contract section 3): a game block is built only from production rows stamped at or before
the block's ``made_at`` and before tip, with the main line pair (``line_cal.price_at``) stamped at or
before that same ``made_at``, and never once tip has passed at the export's own ``made_at`` (``--asof``).
``made_at`` of the block is the newest production row stamp used. A row of a player that only an
earlier run carried is used as that run stamped it (still at or before ``made_at``).

``mu_t`` is the game model's total mean from completed SEASON-2026 games only (the export holds no
pre-computed one; season 2025 is never read), so early in the season it leans on the league mean:
``game.mu_t_source`` says which. It is rebuilt from the export's ``box_scores`` team totals, not from the
``games`` table (which holds only the last week), so it equals the live app's number only where
``box_scores`` covers every completed game. Per-player script history (``hist``) is in ``combo_hist``.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.parlay import combo_build as cb
from nba.parlay import combo_hist as ch
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts
from nba.parlay.combo import SCHEMA, STATS

PRODUCTION_MODEL = "props_context_residual"
SEASON_START = "2026-10-01"  # a slate date before this is season 2025 (frozen holdout): refused
N_SAMPLES = 199  # the production grid resolution the banded calibration assumes
MAX_RAW_BYTES = 300_000
MAX_GZ_BYTES = 60_000
TOL = 1.0e-4  # rounding of the stored 4-decimal grid plus float noise
PRICE_TOKENS = frozenset(
    [
        "price",
        "prices",
        "odds",
        "fee",
        "fees",
        "stake",
        "stakes",
        "kelly",
        "ev",
        "vig",
        "juice",
        "american",
        "implied",
        "payout",
        "bankroll",
    ]
)
PARAMS_PATH = Path(__file__).resolve().parent / "combo_params_v1.json"
EVAL_PATH = Path(__file__).resolve().parent / "combo_eval.py"


class NightRefusal(ValueError):
    """A request the builder refuses outright (holdout date, bad inputs)."""


def iso_z(ts_: datetime) -> str:
    return ts_.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _sha8(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:8]


def code_version() -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(Path(__file__).resolve().parent)],
            capture_output=True, text=True, timeout=20, check=False,
            cwd=Path(__file__).resolve().parent,
        )  # fmt: skip
        sha = out.stdout.strip()
        return sha[:40] if out.returncode == 0 and sha else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def provenance(cal: lc.CalMap, params: Mapping[str, Any], params_bytes: bytes) -> dict[str, Any]:
    tree = ast.parse(EVAL_PATH.read_text())
    doc = ast.get_docstring(tree, clean=False) or ""
    return {
        "map": lc.RULE,
        "map_sha8": str(cal.meta.get("map_sha256", ""))[:8],
        "fit_through": str(cal.meta.get("fit_through", "")),
        "corr_fit_seasons": list(params["corr_fit_seasons"]),
        "corr_n_games": int(params["n_games_corr"]),
        "combo_params_sha8": _sha8(params_bytes),
        "rule_sha8": _sha8(doc.encode()),
        "code_version": code_version(),
    }


# ----------------------------------------------------------------------------- export reads


def export_asof(con: duckdb.DuckDBPyConnection) -> datetime:
    row = con.execute("SELECT value FROM meta WHERE key = 'made_at'").fetchone()
    if row is None:
        raise NightRefusal("export meta has no made_at; pass --asof")
    return datetime.fromisoformat(str(row[0]).replace("Z", "")).replace(tzinfo=None)


def _prop_rows(con: duckdb.DuckDBPyConnection, game_ids: Sequence[str]) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in game_ids)
    cur = con.execute(
        f"""SELECT game_id, player_id, stat, team_id, made_at, tipoff, run_id, q_grid,
                   p_ge_full_counts, p_ge_full_n, p_play, p_play_primary, prediction_json,
                   line_cal, line_cal_null_reason
            FROM prop_grids
            WHERE model_name = ? AND NOT coalesce(comparison_only, FALSE) AND game_id IN ({marks})""",  # noqa: S608
        [PRODUCTION_MODEL, *game_ids],
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def newest_rows(
    rows: Sequence[Mapping[str, Any]], asof: datetime
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Newest production row per (game, player, stat) stamped at or before ``asof`` and before tip."""
    best: dict[tuple[str, int, str], dict[str, Any]] = {}
    for r in rows:
        if not (r["made_at"] <= asof and r["made_at"] < r["tipoff"]):
            continue
        k = (str(r["game_id"]), int(r["player_id"]), str(r["stat"]))
        if k not in best or (r["made_at"], r["run_id"]) > (best[k]["made_at"], best[k]["run_id"]):
            best[k] = dict(r)
    return best


def _starter_rate(row: Mapping[str, Any]) -> float | None:
    try:
        sr = json.loads(row["prediction_json"]).get("starter_rate")
    except (TypeError, ValueError, AttributeError):
        return None
    return None if sr is None else float(sr)


def _main_line(row: Mapping[str, Any], made_at: datetime) -> tuple[float | None, str | None]:
    """(main_line, reason). The line is the stored ``line_cal.main_line`` and only if its pair stamp
    is at or before the block's ``made_at``."""
    raw = row.get("line_cal")
    if not raw:
        return None, str(row.get("line_cal_null_reason") or "no_line_cal")
    d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    at = datetime.fromisoformat(str(d["price_at"]).replace("Z", "")).replace(tzinfo=None)
    if at > made_at:
        return None, "line_after_made_at"
    return float(d["main_line"]), None


# ----------------------------------------------------------------------------- builder


def _calibrate_rows(cal: lc.CalMap, items: Sequence[dict[str, Any]]) -> None:
    """Fill ``cal``, ``raw``, ``cal_k`` on each item. Rows are calibrated in groups of equal grid
    length so each row's rungs are exactly its own (no padding from other players)."""
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        groups[(it["stat"], len(it["counts"]))].append(it)
    for (stat, _k_len), grp in groups.items():
        raw = np.asarray([it["counts"] for it in grp], dtype=float) / float(N_SAMPLES)
        med = np.asarray([it["q"][1] for it in grp], dtype=float)
        spr = np.asarray([(it["q"][2] - it["q"][0]) / lc.IQR_TO_SD for it in grp], dtype=float)
        st10 = np.asarray([it["starter_rate"] for it in grp], dtype=float)
        surv, lo, hi = cb.calibrate_surv_banded(cal, stat, raw, med, spr, st10)
        for i, it in enumerate(grp):
            it["cal"], it["raw"] = surv[i], raw[i]
            it["cal_k"] = (int(lo[i]), int(hi[i]))


def _pad(a: np.ndarray, n: int) -> np.ndarray:
    return a if len(a) >= n else np.concatenate([a, np.zeros(n - len(a))])


def build_night(
    con: duckdb.DuckDBPyConnection,
    night: str,
    *,
    cal: lc.CalMap,
    params: Mapping[str, Any],
    game_params: Mapping[str, Any],
    asof: datetime | None = None,
) -> dict[str, Any]:
    """The ``combo_export_v1`` payload for slate date ``night`` (YYYY-MM-DD)."""
    if night < SEASON_START:
        raise NightRefusal(
            f"{night} is before the 2026-27 season; season 2025 is the frozen holdout"
        )
    asof = asof if asof is not None else export_asof(con)
    games = con.execute(
        "SELECT game_id, tipoff, home_team, away_team FROM games WHERE game_date = ? AND season >= 2026 ORDER BY tipoff, game_id",
        [night],
    ).fetchall()
    log: dict[str, Any] = {"asof": iso_z(asof), "games": [], "n_players": 0, "n_stat_entries": 0}
    gm = ch.game_model_params(game_params)
    hist_games = ch.past_games(con, night)
    names = dict(con.execute("SELECT player_id, full_name FROM players").fetchall())
    out_games: list[dict[str, Any]] = []
    open_games = []
    for gid, tip, home, away in games:
        gid = str(gid)
        if tip <= asof:
            log["games"].append(
                {
                    "game_id": gid,
                    "status": "left_out",
                    "reason": "tipped_at_or_before_asof",
                    "tip_utc": iso_z(tip),
                }
            )
        else:
            open_games.append((gid, tip, int(home), int(away)))
    rows = _prop_rows(con, [g[0] for g in open_games]) if open_games else []
    for gid, tip, home, away in open_games:
        entry: dict[str, Any] = {"game_id": gid, "tip_utc": iso_z(tip)}
        best = {k: v for k, v in newest_rows(rows, min(asof, tip)).items() if k[0] == gid}
        if not best:
            entry.update(status="left_out", reason="no_production_row_before_tip_and_asof")
            log["games"].append(entry)
            continue
        made_at = max(v["made_at"] for v in best.values())
        wp = con.execute(
            "SELECT made_at, p_home, model_name FROM win_probs WHERE game_id = ? AND is_primary AND made_at <= ? AND made_at < ? ORDER BY made_at DESC, run_id DESC LIMIT 1",
            [gid, made_at, tip],
        ).fetchone()
        if wp is None or wp[1] is None:
            entry.update(
                status="left_out", reason="no_primary_win_probability_at_or_before_made_at"
            )
            log["games"].append(entry)
            continue
        mu_m = float(game_params["margin_b"]) + float(game_params["margin_s"]) * float(
            _logit(float(wp[1]))
        )
        mu_t, mu_t_src = ch.tonight_mu_t(hist_games, gm, home, away, night)
        skipped: dict[str, int] = defaultdict(int)
        items: list[dict[str, Any]] = []
        for (_g, pid, stat), r in best.items():
            counts = r["p_ge_full_counts"]
            qg = r["q_grid"]
            sr = _starter_rate(r)
            if pid not in names:
                skipped["no_player_name"] += 1
            elif stat not in STATS:
                skipped["unknown_stat"] += 1
            elif not counts or r["p_ge_full_n"] != N_SAMPLES:
                skipped["no_survival_grid_or_n_not_199"] += 1
            elif sr is None:
                skipped["no_starter_rate"] += 1
            else:
                q = ts.quartiles({"pred": {"q_grid": list(qg) if qg is not None else None}})
                if q is None:
                    skipped["no_quartiles"] += 1
                    continue
                line, why = _main_line(r, made_at)
                if why:
                    skipped["main_line_null:" + why] += 1
                items.append({"row": r, "pid": pid, "stat": stat, "counts": list(counts), "q": q,
                              "starter_rate": sr, "main_line": line})  # fmt: skip
        _calibrate_rows(cal, items)
        by_player: dict[int, dict[str, Any]] = {}
        for it in items:
            r = it["row"]
            p = by_player.setdefault(
                it["pid"],
                {"player_id": it["pid"], "name": names[it["pid"]], "team": int(r["team_id"]),
                 "p_play": None, "starter": int(it["starter_rate"] >= 0.5), "stats": {},
                 "_pp": (r["made_at"], r["p_play_primary"], r["p_play"])},
            )  # fmt: skip
            if (r["made_at"],) > p["_pp"][:1]:
                p["_pp"] = (r["made_at"], r["p_play_primary"], r["p_play"])
                p["starter"] = int(it["starter_rate"] >= 0.5)
            ml = it["main_line"]
            k_need = int(np.floor(ml)) + 1 if ml is not None else 0
            p["stats"][it["stat"]] = {
                "cal": _pad(it["cal"], k_need), "raw": _pad(it["raw"], k_need),
                "main_line": ml, "cal_k": it["cal_k"],
            }  # fmt: skip
        players = []
        for p in by_player.values():
            _t, ppp, pp = p.pop("_pp")
            chosen = ppp if ppp is not None else pp
            p["p_play"] = float(chosen) if chosen is not None else 0.0
            p["_null_p_play"] = chosen is None
            players.append(p)
        players.sort(key=lambda p: (p["team"], p["name"], p["player_id"]))
        block = cb.build_game_payload(
            game_id=gid, date=night, home_team=home, away_team=away, tip_utc=iso_z(tip),
            made_at=iso_z(made_at), mu_m=mu_m, sd_m=float(game_params["margin_sd"]),
            mu_t=mu_t, sd_t=float(game_params["total_sd"]),
            players=players,
        )  # fmt: skip
        for bp, src in zip(block["players"], players, strict=True):
            if src["_null_p_play"]:
                bp["p_play"] = None
        block["mu_t_source"] = mu_t_src
        out_games.append(block)
        n_stat = sum(len(p["stats"]) for p in players)
        log["n_players"] += len(players)
        log["n_stat_entries"] += n_stat
        entry.update(
            status="built", made_at=iso_z(made_at), win_prob_model=str(wp[2]),
            n_players=len(players), n_stat_entries=n_stat,
            mu_t_source=mu_t_src,
            main_line_null_reasons=dict(sorted(skipped.items())),
        )  # fmt: skip
        log["games"].append(entry)
    params_bytes = PARAMS_PATH.read_bytes()
    shared = cb.build_shared(
        params["corr"], params["script_params"], params["script_status"],
        provenance(cal, params, params_bytes), params["definitions"],
    )  # fmt: skip
    payload = cb.nightly(shared, out_games, night)
    log["hist"] = ch.attach_hist(con, payload, night, gm, params["definitions"])
    payload["build_log"] = log
    return payload


def _logit(p: float) -> float:
    pp = min(max(p, 1e-6), 1 - 1e-6)
    return float(np.log(pp / (1 - pp)))


# ----------------------------------------------------------------------------- validator


def _walk_keys(o: Any) -> list[str]:
    out: list[str] = []
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(str(k))
            out.extend(_walk_keys(v))
    elif isinstance(o, list):
        for v in o:
            out.extend(_walk_keys(v))
    return out


def size_bytes(payload: Mapping[str, Any]) -> tuple[int, int]:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return len(raw), len(gzip.compress(raw, 9, mtime=0))


def surv_at(entry: Mapping[str, Any], k: int) -> float:
    k0, vals = int(entry["k0"]), entry["surv"]
    if k < k0:
        return 1.0
    j = k - k0
    return float(vals[j]) if j < len(vals) else 0.0


def load_expected(
    con: duckdb.DuckDBPyConnection, payload: Mapping[str, Any]
) -> dict[tuple[str, int, str], float | None]:
    """The export's stored ``line_cal.p_over_cal`` for every prop of the payload (the production row
    stamped at or before the game's ``made_at`` and before tip), keyed (game, player, stat). Raises on a
    stored line stamped after ``made_at``."""
    exp: dict[tuple[str, int, str], float | None] = {}
    for g in payload["games"]:
        made = datetime.fromisoformat(g["made_at"].replace("Z", ""))
        rows = _prop_rows(con, [g["game_id"]])
        best = newest_rows(rows, made)
        for (gid, pid, stat), r in best.items():
            raw = r.get("line_cal")
            if not raw:
                exp[(gid, pid, stat)] = None
                continue
            d = json.loads(raw)
            at = datetime.fromisoformat(str(d["price_at"]).replace("Z", ""))
            if at > made:
                exp[(gid, pid, stat)] = None
                continue
            exp[(gid, pid, stat)] = float(d["p_over_cal"])
    return exp


def validate_payload(
    payload: Mapping[str, Any],
    *,
    expected: Mapping[tuple[str, int, str], float | None] | None = None,
    asof: datetime | None = None,
    tol: float = TOL,
) -> dict[str, Any]:
    """Check a payload against the contract. Returns ``{"errors": [...], "n_checked": {...},
    "max_abs_diff": float | None, "exempt": {...}}``; ``errors`` empty means it passes."""
    errs: list[str] = []
    n = {"games": 0, "players": 0, "stat_entries": 0, "main_line_checked": 0}
    exempt: dict[str, int] = defaultdict(int)
    max_diff: float | None = None
    worst: tuple[Any, ...] | None = None
    if payload.get("schema") != SCHEMA:
        errs.append(f"schema is {payload.get('schema')!r}, not {SCHEMA!r}")
    for key in ("night", "shared", "games"):
        if key not in payload:
            errs.append(f"missing top-level key {key}")
    if errs:
        return {"errors": errs, "n_checked": n, "max_abs_diff": None, "exempt": {}}
    bad = sorted({k for k in _walk_keys(payload) if PRICE_TOKENS & set(k.lower().split("_"))})
    if bad:
        errs.append(f"price-like keys present: {bad}")
    raw_b, gz_b = size_bytes(payload)
    if raw_b > MAX_RAW_BYTES:
        errs.append(f"raw size {raw_b} > {MAX_RAW_BYTES}")
    if gz_b > MAX_GZ_BYTES:
        errs.append(f"gzipped size {gz_b} > {MAX_GZ_BYTES}")
    sh = payload["shared"]
    for key in ("corr", "scripts", "provenance"):
        if key not in sh:
            errs.append(f"shared missing {key}")
    for g in payload["games"]:
        n["games"] += 1
        gid = g.get("game_id")
        try:
            made = datetime.fromisoformat(g["made_at"].replace("Z", ""))
            tip = datetime.fromisoformat(g["tip_utc"].replace("Z", ""))
        except (KeyError, ValueError):
            errs.append(f"{gid}: made_at / tip_utc missing or unparseable")
            continue
        if g.get("date") != payload["night"]:
            errs.append(f"{gid}: date {g.get('date')} != night {payload['night']}")
        if not made < tip:
            errs.append(f"{gid}: made_at {g['made_at']} not before tip {g['tip_utc']}")
        if asof is not None and not tip > asof:
            errs.append(f"{gid}: built for a game that tipped at or before asof")
        for k in ("mu_m", "sd_m", "mu_t", "sd_t"):
            if k not in g.get("game", {}):
                errs.append(f"{gid}: game.{k} missing")
        for p in g["players"]:
            n["players"] += 1
            pl_id = p.get("player_id")
            pp = p.get("p_play")
            if pp is not None and not 0.0 <= pp <= 1.0:
                errs.append(f"{gid}/{pl_id}: p_play {pp} outside [0, 1]")
            if p.get("starter") not in (0, 1):
                errs.append(f"{gid}/{pl_id}: starter {p.get('starter')}")
            for stat, e in p["stats"].items():
                n["stat_entries"] += 1
                where = f"{gid}/{pl_id}/{stat}"
                vals = e["surv"]
                if stat not in STATS:
                    errs.append(f"{where}: unknown stat")
                if int(e["k0"]) < 1:
                    errs.append(f"{where}: k0 {e['k0']} < 1")
                if any(not 0.0 <= v <= 1.0 for v in vals):
                    errs.append(f"{where}: surv outside [0, 1]")
                if any(b > a + 1e-12 for a, b in zip(vals, vals[1:], strict=False)):
                    errs.append(f"{where}: surv not nonincreasing")
                if vals and (vals[0] >= 1.0 or vals[-1] <= 0.0):
                    errs.append(f"{where}: surv not trimmed (first {vals[0]}, last {vals[-1]})")
                ck = e.get("cal_k")
                if ck is None or not (1 <= ck[0] <= ck[1] <= N_SAMPLES):
                    errs.append(f"{where}: cal_k {ck} out of bounds")
                ml = e.get("main_line")
                if "hist" in e:
                    errs.extend(_hist_errors(where, e["hist"], ml))
                if ml is None:
                    if "raw_at_main" in e:
                        errs.append(f"{where}: raw_at_main without main_line")
                    continue
                if abs(ml - int(ml // 1) - 0.5) > 1e-9:
                    errs.append(f"{where}: main_line {ml} is not x.5")
                    continue
                if "raw_at_main" not in e:
                    errs.append(f"{where}: main_line without raw_at_main")
                if expected is None:
                    continue
                want = expected.get((str(gid), int(pl_id), stat))
                if want is None:
                    errs.append(
                        f"{where}: main_line {ml} but the export has no line_cal value at or before made_at"
                    )
                    continue
                got = surv_at(e, int(ml // 1) + 1)
                d = abs(got - want)
                n["main_line_checked"] += 1
                if max_diff is None or d > max_diff:
                    max_diff, worst = d, (where, got, want)
                if d > tol:
                    reason = _explain(e, int(ml // 1) + 1)
                    exempt[reason] += 1
                    if reason == "unexplained":
                        errs.append(
                            f"{where}: calibrated {got:.4f} at main {ml} vs export {want:.4f} (diff {d:.4f})"
                        )
    return {
        "errors": errs, "n_checked": n, "max_abs_diff": max_diff,
        "worst": list(worst) if worst else None, "exempt_over_tol": dict(exempt),
        "size_bytes": {"raw": raw_b, "gzip": gz_b}, "tol": tol,
    }  # fmt: skip


def _hist_errors(where: str, h: Mapping[str, Any], main_line: float | None) -> list[str]:
    """``hist`` is integer counts only: ``n_all >= k_all >= 0`` and per script ``[n, k]`` with
    ``n_all >= n >= k >= 0``; it exists only where the stat has a main line."""
    out: list[str] = []
    if main_line is None:
        out.append(f"{where}: hist without main_line")
    n_all, k_all = h.get("n_all"), h.get("k_all")
    ints = (
        isinstance(n_all, int)
        and isinstance(k_all, int)
        and not isinstance(n_all, bool)
        and not isinstance(k_all, bool)
    )
    if not ints or not 0 <= k_all <= n_all:  # type: ignore[operator]
        return [*out, f"{where}: hist n_all / k_all invalid ({n_all}, {k_all})"]
    for key, v in h.items():
        if key in ("n_all", "k_all"):
            continue
        ok = (
            key in ("blowout", "close", "shootout", "grind")
            and isinstance(v, list)
            and len(v) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
            and 0 <= v[1] <= v[0] <= n_all
        )
        if not ok:
            out.append(f"{where}: hist[{key}] = {v} invalid")
    return out


def _explain(e: Mapping[str, Any], k: int) -> str:
    """Documented reasons the stored grid can differ from the direct map value at the main line."""
    lo, hi = e["cal_k"]
    if not lo <= k <= hi:
        return "main_line_rung_outside_calibrated_band"
    return "unexplained"


# ----------------------------------------------------------------------------- CLI


def _load_game_params(path: Path) -> dict[str, Any]:
    p = json.loads(path.read_text())["params"]
    for k in ("margin_b", "margin_s", "margin_sd", "total_sd"):
        if k not in p:
            raise NightRefusal(f"{path.name}: params.{k} missing")
    return dict(p)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", required=True, help="a COPY of export.duckdb (opened read-only)")
    ap.add_argument("--date", required=True, help="slate date YYYY-MM-DD (2026-27 season only)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument(
        "--joint-model", required=True, help="copy of data/parlay/joint_model.json (params block)"
    )
    ap.add_argument("--asof", default=None, help="UTC ISO cut (default: the export's meta made_at)")
    a = ap.parse_args(argv)
    if a.date < SEASON_START:
        raise SystemExit(
            f"refused: {a.date} is before the 2026-27 season (season 2025 is the frozen holdout)"
        )
    params_bytes = PARAMS_PATH.read_bytes()
    params = json.loads(params_bytes)
    cal = lc.load_map()
    asof = datetime.fromisoformat(a.asof.replace("Z", "")).replace(tzinfo=None) if a.asof else None
    con = duckdb.connect(a.export, read_only=True)
    try:
        asof = asof if asof is not None else export_asof(con)
        payload = build_night(
            con, a.date, cal=cal, params=params, game_params=_load_game_params(Path(a.joint_model)),
            asof=asof,
        )  # fmt: skip
        exp = load_expected(con, payload)
    finally:
        con.close()
    res = validate_payload(payload, expected=exp, asof=asof)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"combo_{a.date}.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    print(json.dumps({"wrote": str(path), "validation": res}, indent=1, default=str))
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
