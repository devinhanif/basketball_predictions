# ruff: noqa: E501
"""The combo payload's validator, and the OFFLINE rebuild of ``combo_<night>.json`` from a copy of the
app export (``docs/contracts/combo_export_v1.md``).

There is ONE producer: ``nba.export.combo_stage.build`` (the nightly export step). This module's command
line reads a COPY of ``export.duckdb`` (``read_only=True``), turns its tables into the stage's inputs and
calls that same ``build``; the stage in turn runs ``validate_payload`` below on every payload before it is
written. No price column or price table is read here (the market block of the picks file needs the Kalshi
store, which this command never opens, so it writes the combo file only). No orders, no credentials.

``mu_t`` (the number GAME_SCRIPTS_FORWARD scores) comes from the ``games`` table of the LIVE season
(``combo_stage.history_rows``). The export's own ``games`` table holds only the last week, so pass
``--history-db`` (a read-only COPY of the source database) to rebuild the app's number. Without it the
command builds the same-shaped rows from the export copy's season-2026 box scores (team score = the sum
of its players' points), equal to the app's number only where those box scores are complete; the file's
``build_log.history_input`` says which. Season 2025 is never read (a slate date before the 2026-27 season
is refused before any query; every history query filters ``season = 2026``).
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.parlay import combo_hist as ch
from nba.parlay.combo import SCHEMA, STATS

PRODUCTION_MODEL = "props_context_residual"
SEASON_START = "2026-10-01"  # a slate date before this is season 2025 (frozen holdout): refused
N_SAMPLES = 199  # largest 1-based rung index ``cal_k`` may name (the stat grids are far shorter)
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
HISTORY_BOX = "export box_scores team sums (offline; equals the app's games table only where box scores are complete)"


class NightRefusal(ValueError):
    """A request the builder refuses outright (holdout date, bad inputs)."""


def iso_z(ts_: datetime) -> str:
    return ts_.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


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


# ----------------------------------------------------------------------------- export copy -> stage inputs


def export_asof(con: duckdb.DuckDBPyConnection) -> datetime:
    row = con.execute("SELECT value FROM meta WHERE key = 'made_at'").fetchone()
    if row is None:
        raise NightRefusal("export meta has no made_at; pass --asof")
    return datetime.fromisoformat(str(row[0]).replace("Z", "")).replace(tzinfo=None)


def _dicts(con: duckdb.DuckDBPyConnection, sql: str, args: Sequence[Any]) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(args))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0]  # type: ignore[index]
    )


def history_rows_from_box(con: duckdb.DuckDBPyConnection, day: date) -> list[tuple[Any, ...]]:
    """``history_rows``-shaped tuples from the export copy's season-2026 box scores (the offline
    fallback): one row per game with exactly two teams, the smaller team id first (the total feature is
    symmetric and the script definitions use |margin| and the total), dated before ``day``."""
    rows = con.execute(
        """SELECT game_id, min(game_date) AS game_date, min(team_id) AS t1, max(team_id) AS t2,
                  count(DISTINCT team_id) AS n_teams,
                  sum(CASE WHEN team_id = t_lo THEN pts END) AS p1,
                  sum(CASE WHEN team_id = t_hi THEN pts END) AS p2
           FROM (SELECT *, min(team_id) OVER (PARTITION BY game_id) AS t_lo,
                           max(team_id) OVER (PARTITION BY game_id) AS t_hi FROM box_scores
                 WHERE season = ? AND game_date < ?)
           GROUP BY game_id ORDER BY game_date, game_id""",
        [ch.LIVE_SEASON, day],
    ).fetchall()
    return [
        (r[0], r[1], r[2], r[3], r[5], r[6])
        for r in rows
        if r[4] == 2 and r[5] and r[6] and ch.season_id_ok(str(r[0]))
    ]


def export_inputs(
    con: duckdb.DuckDBPyConnection, day: date, history: duckdb.DuckDBPyConnection | None = None
) -> dict[str, Any]:
    """The stage's inputs for slate ``day`` from an export copy (and, for ``mu_t``, an optional
    read-only copy of the source database)."""
    from nba.export import combo_stage as cs

    games = _dicts(
        con,
        "SELECT game_id, season, game_date, tipoff, home_team, away_team FROM games "
        "WHERE game_date = ? AND season >= ? ORDER BY tipoff, game_id",
        [day, ch.LIVE_SEASON],
    )
    gids = [str(g["game_id"]) for g in games]
    marks = ",".join("?" for _ in gids) or "NULL"
    props = _dicts(
        con,
        f"""SELECT run_id, made_at, game_id, tipoff, model_name, comparison_only, player_id, stat, team_id,
                   p_play, prediction_json, p_ge_full_counts, p_ge_full_n, line_cal, line_cal_null_reason
            FROM prop_grids WHERE model_name = ? AND game_id IN ({marks})""",  # noqa: S608
        [PRODUCTION_MODEL, *gids],
    )
    for p in props:
        if isinstance(p.get("line_cal"), str):
            p["line_cal"] = json.loads(p["line_cal"])
    win = _dicts(
        con,
        f"SELECT run_id, made_at, game_id, model_name, p_home, comparison_only FROM win_probs WHERE game_id IN ({marks})",  # noqa: S608
        gids,
    )
    pplay = (
        _dicts(
            con,
            f"SELECT game_id, player_id, made_at, tipoff, p_play_primary FROM p_play WHERE game_id IN ({marks})",  # noqa: S608
            gids,
        )
        if _has_table(con, "p_play")
        else []
    )
    sits = (
        _dicts(con, f"SELECT * FROM teammate_sits WHERE game_id IN ({marks})", gids)  # noqa: S608
        if _has_table(con, "teammate_sits")
        else []
    )
    box = _dicts(
        con,
        "SELECT game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, game_date, season FROM box_scores "
        "WHERE season = ? AND game_date < ?",
        [ch.LIVE_SEASON, day],
    )
    if history is not None:
        hist_rows, hist_in = cs.history_rows(history, day, ch.LIVE_SEASON), cs.HISTORY_GAMES
    else:
        hist_rows, hist_in = history_rows_from_box(con, day), HISTORY_BOX
    names = {
        int(a): str(b)
        for a, b in con.execute("SELECT player_id, full_name FROM players").fetchall()
    }
    abbr = (
        {int(a): str(b) for a, b in con.execute("SELECT team_id, abbr FROM teams").fetchall()}
        if _has_table(con, "teams")
        else {}
    )
    return {
        "games": games, "win_probs": win, "props": props, "pplay": pplay, "sits": sits,
        "hist_rows": hist_rows, "history_input": hist_in, "box": box, "names": names, "abbr": abbr,
    }  # fmt: skip


def build_night(
    con: duckdb.DuckDBPyConnection,
    night: str,
    *,
    game_model_path: Path,
    asof: datetime | None = None,
    history: duckdb.DuckDBPyConnection | None = None,
    cal: Any = None,
) -> Any:
    """``combo_stage.build`` on an export copy: returns its ``StageResult`` (``.combo`` is the payload,
    None with ``.info['reason']`` when nothing was built or validation failed)."""
    from nba.export import combo_stage as cs

    if night < SEASON_START:
        raise NightRefusal(
            f"{night} is before the 2026-27 season; season 2025 is the frozen holdout"
        )
    asof = asof if asof is not None else export_asof(con)
    day = date.fromisoformat(night)
    i = export_inputs(con, day, history)
    return cs.build(
        day, i["games"], i["win_probs"], i["props"], i["pplay"], i["sits"], i["hist_rows"],
        i["names"], i["abbr"], made_at=asof, code_version=code_version(), cal=cal,
        game_model_path=game_model_path, box_rows=i["box"], asof=asof,
        history_input=i["history_input"],
    )  # fmt: skip


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


def validate_payload(
    payload: Mapping[str, Any],
    *,
    expected: Mapping[tuple[str, int, str], float | None] | None = None,
    asof: datetime | None = None,
    tol: float = TOL,
    check_size: bool = True,
    raw_at_main_dropped: bool = False,
) -> dict[str, Any]:
    """Check a payload against the contract. Returns ``{"errors": [...], "n_checked": {...},
    "max_abs_diff": float | None, "exempt": {...}}``; ``errors`` empty means it passes. ``expected``:
    the export's ``line_cal.p_over_cal`` per (game, player, stat). ``asof``: also refuse a game that
    tipped at or before it (the nightly stage passes None: a block is built from pre-tip rows only and
    is the same whenever it is rebuilt, and the in-game snapshot needs it). ``check_size=False`` leaves
    the budget to ``combo_stage.enforce_budget`` (which trims and flags); ``raw_at_main_dropped`` accepts
    a main line without ``raw_at_main`` (the budget's first cut)."""
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
    if check_size and raw_b > MAX_RAW_BYTES:
        errs.append(f"raw size {raw_b} > {MAX_RAW_BYTES}")
    if check_size and gz_b > MAX_GZ_BYTES:
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
                if "raw_at_main" not in e and not raw_at_main_dropped:
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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", required=True, help="a COPY of export.duckdb (opened read-only)")
    ap.add_argument("--date", required=True, help="slate date YYYY-MM-DD (2026-27 season only)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--joint-model", required=True, help="copy of data/parlay/joint_model.json")
    ap.add_argument("--asof", default=None, help="UTC ISO cut (default: the export's meta made_at)")
    ap.add_argument(
        "--history-db",
        default=None,
        help="a read-only COPY of the source database: mu_t from its games table, as the app (default: the export copy's box scores)",
    )
    a = ap.parse_args(argv)
    if a.date < SEASON_START:
        raise SystemExit(
            f"refused: {a.date} is before the 2026-27 season (season 2025 is the frozen holdout)"
        )
    asof = datetime.fromisoformat(a.asof.replace("Z", "")).replace(tzinfo=None) if a.asof else None
    con = duckdb.connect(a.export, read_only=True)
    hcon = duckdb.connect(a.history_db, read_only=True) if a.history_db else None
    try:
        res = build_night(con, a.date, game_model_path=Path(a.joint_model), asof=asof, history=hcon)
    finally:
        con.close()
        if hcon is not None:
            hcon.close()
    if res.combo is None:
        print(json.dumps({"wrote": None, "info": res.info}, indent=1, default=str))
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"combo_{a.date}.json"
    path.write_text(json.dumps(res.combo, separators=(",", ":")))
    size = size_bytes(res.combo)
    print(
        json.dumps(
            {
                "wrote": str(path),
                "validation": res.info.get("validation"),
                "budget": res.info.get("budget"),
                "size_bytes": {"raw": size[0], "gzip": size[1]},
                "hist": res.info.get("hist"),
            },
            indent=1,
            default=str,
        )  # fmt: skip
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
