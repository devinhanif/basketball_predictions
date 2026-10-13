# ruff: noqa: E501
"""The pretip AVG step: SEQ inference on CPU for tonight's slate group, AVG = 0.5 pmf(SEQc) + 0.5 pmf(FULL), stored as a shadow.

Runs per slate group at the T-60 tick, after the FULL shadow, in its own try/except (``pipeline._avg_shadow``). It reads production's rows
and the FULL shadow's rows already written in this pass, and writes only ``forward_avg_*``; it never writes ``forward_predictions`` or
any scored production table, never waits for anything the pretip run needs, and is never retried after tip. Heartbeat status:
``ok`` (rows written or nothing to write), ``skipped`` (no weights installed / no regular-season game / flag without inputs),
``failed`` (any exception or a failed inference subprocess; the production rows are unaffected).

Serving rule (hard): AVG exists only for regular-season games (``game_id`` prefix 002) and rows with ``n_prior >= 3``; every other row
is served FULL (no AVG row is written and the count is reported). Rows need a FULL grid and an A0 grid already in the store.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from nba.daily.avg_shadow import bundle as B
from nba.daily.avg_shadow import grids, sequences, store
from nba.daily.injury import to_report_dt
from nba.daily.pplay_shadow.features import SlateGame
from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME

REGULAR = "002"
MIN_PRIOR = 3
ERA_WIDTH = 3  # zero era channels appended to the 31 exporter ctx columns (T478 export layout)
INFER_TIMEOUT_S = 240
HB_DIR = Path("data/ops/heartbeat")
STATS = store.STATS


def _a0_rows(
    con: duckdb.DuckDBPyConnection, game_ids: list[str], made_at: dt.datetime
) -> dict[tuple[str, int, str], tuple[list[float], str]]:
    """{(game, player, stat): (19 quantiles, source model)}: the latest production row with ``made_at <= made_at``; pts from the
    lower-tail variant (the offline A0), the other stats from production's own grid."""
    marks = ",".join("?" for _ in game_ids)
    out: dict[tuple[str, int, str], tuple[list[float], str]] = {}
    for model, targets in (
        (CONTEXT_PROPS_MODEL_NAME, ("reb", "ast", "fg3m")),
        (CONTEXT_LT_PROPS_MODEL_NAME, ("pts",)),
    ):
        tmarks = ",".join("?" for _ in targets)
        for gid, pid, tgt, q in con.execute(
            f"""SELECT game_id, player_id, target, grid FROM (
                    SELECT game_id, player_id, target, CAST(prediction ->> '$.q_grid' AS VARCHAR) AS grid,
                           row_number() OVER (PARTITION BY game_id, player_id, target ORDER BY made_at DESC, run_id DESC) rn
                    FROM forward_predictions
                    WHERE model_name = ? AND target IN ({tmarks}) AND player_id > 0 AND game_id IN ({marks}) AND made_at <= ?)
                WHERE rn = 1""",
            [model, *targets, *game_ids, made_at],
        ).fetchall():
            if q is not None:
                grid = json.loads(q)
                if len(grid) == 19:
                    out[(str(gid), int(pid), str(tgt))] = ([float(v) for v in grid], model)
    return out


def _full_rows(
    con: duckdb.DuckDBPyConnection, game_ids: list[str], made_at: dt.datetime
) -> dict[tuple[str, int, str], list[float]]:
    """The latest ``forward_full_shadow`` grid with ``made_at <= made_at`` (the store is write-on-change, so no same-run pairing)."""
    marks = ",".join("?" for _ in game_ids)
    try:
        rows = con.execute(
            f"""SELECT game_id, player_id, stat, grid FROM (
                    SELECT game_id, player_id, stat, CAST(q_grid AS VARCHAR) AS grid,
                           row_number() OVER (PARTITION BY game_id, player_id, stat ORDER BY made_at DESC, run_id DESC) rn
                    FROM forward_full_shadow WHERE game_id IN ({marks}) AND made_at <= ?) WHERE rn = 1""",
            [*game_ids, made_at],
        ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {(str(g), int(p), str(s)): [float(v) for v in json.loads(q)] for g, p, s, q in rows}


def _heartbeat_file(hb_dir: Path, rec: dict[str, Any], count: str = "keep") -> None:
    """``data/ops/heartbeat/avg_shadow.json`` for the independent watchdog: written at START and END of every run. ``consecutive_bad`` is the running count
    of failed runs and of runs skipped for want of weights (``count="inc"``); a good run resets it (``"reset"``); a run with nothing to serve (no regular-season
    game on the slate) leaves it alone (``"keep"``). The watchdog alerts at 3 and reads this file, never this module's tables."""
    hb_dir.mkdir(parents=True, exist_ok=True)
    path = hb_dir / "avg_shadow.json"
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    bad = int(prev.get("consecutive_bad", 0))
    bad = bad + 1 if count == "inc" else 0 if count == "reset" else bad
    rec = {**prev, **rec, "consecutive_bad": bad, "job": "avg_shadow"}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, default=str))
    tmp.replace(path)


def _gate_weights(
    gate: dict[str, dict[str, list[float]]], stat: str, meta: pd.DataFrame, sel: np.ndarray
) -> np.ndarray:
    g = gate[stat]
    x = np.column_stack(
        [
            np.log1p(meta["n_prior"].to_numpy(dtype=float)),
            meta["starter10"].to_numpy(dtype=float),
            meta["n_rep_out"].to_numpy(dtype=float),
            meta["has_report"].to_numpy(dtype=float),
            meta["team_game_no"].to_numpy(dtype=float),
        ]
    )[sel]
    return grids.softmax_gate(
        x, np.array(g["med"]), np.array(g["mu"]), np.array(g["sd"]), np.array(g["coef"])
    )


def run_avg_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date,
    slate: list[SlateGame],
    made_at: dt.datetime,
    run_id: str,
    *,
    season: int,
    bundle_root: Path = B.DEFAULT_ROOT,
    hb_dir: Path = HB_DIR,
    elo_params: dict[str, float] | None = None,
    timeout: int = INFER_TIMEOUT_S,
    work_dir: Path | None = None,
    team_hint: dict[tuple[str, int], int] | None = None,
) -> dict[str, Any]:
    """One inference run for ``slate`` (games that have not tipped). Never raises: a failure is a ``failed`` heartbeat."""
    t0 = time.perf_counter()
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    info: dict[str, Any] = {"status": "ok", "rows": 0, "games": len(slate)}
    model_id: str | None = None
    rc = 0
    try:
        store.ensure_tables(con)
        _heartbeat_file(
            hb_dir,
            {
                "start_ts": started.isoformat(),
                "end_ts": None,
                "rc": None,
                "status": "running",
                "slate_date": str(run_date),
            },
        )
        reg = [g for g in slate if g.game_id[:3] == REGULAR and made_at < g.tipoff]
        info["routed_full_not_regular"] = len(slate) - len(reg)
        b, stale = B.select_bundle(bundle_root, run_date, made_at)
        if not reg:  # nothing to serve is not a missing-weights problem: checked first
            info.update(status="skipped", detail="no regular-season game to serve", count="keep")
        elif b is None:
            info.update(status="skipped", detail="no weights installed", count="inc")
        else:
            model_id = b.model_id
            info.update(
                _serve(
                    con,
                    run_date,
                    reg,
                    made_at,
                    run_id,
                    season,
                    b,
                    stale,
                    elo_params,
                    timeout,
                    work_dir,
                    team_hint,
                )
            )
            info["count"] = {"ok": "reset", "no_full": "inc"}.get(str(info["status"]), "keep")
    except Exception as exc:
        info.update(status="failed", detail=f"{type(exc).__name__}: {exc}"[:400], count="inc")
        rc = 1
    finished = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    info["seconds"] = round(time.perf_counter() - t0, 1)
    try:
        store.write_heartbeat(
            con, slate_date=run_date, run_id=run_id, started_at=started, finished_at=finished, games_seen=len(slate),
            rows_written=int(info.get("written", 0)), model_id=model_id, status=str(info["status"]), rc=rc,
            detail=str(info.get("detail", "")),
        )  # fmt: skip
    except (
        Exception
    ) as exc:  # the heartbeat table itself failing must not raise into the pretip run
        info["heartbeat_error"] = f"{type(exc).__name__}: {exc}"[:200]
    end_rec: dict[str, Any] = {
        "start_ts": started.isoformat(), "end_ts": finished.isoformat(), "rc": rc, "status": info["status"],
        "slate_date": str(run_date), "rows_written": int(info.get("written", 0)),
    }  # fmt: skip
    if model_id is not None:  # a non-serving run keeps the last serving bundle's identity
        end_rec.update(
            model_id=model_id,
            month_block=b.month_block if b else None,
            stale_model=bool(info.get("stale_model", False)),
        )
    _heartbeat_file(hb_dir, end_rec, count=str(info.get("count", "keep")))
    return info


def _serve(
    con: duckdb.DuckDBPyConnection, run_date: dt.date, reg: list[SlateGame], made_at: dt.datetime, run_id: str, season: int,
    b: B.Bundle, stale: bool, elo_params: dict[str, float] | None, timeout: int, work_dir: Path | None,
    team_hint: dict[tuple[str, int], int] | None,
) -> dict[str, Any]:  # fmt: skip
    gids = [g.game_id for g in reg]
    a0 = _a0_rows(con, gids, made_at)
    full = _full_rows(con, gids, made_at)
    tip = {g.game_id: g.tipoff for g in reg}
    roster = pd.DataFrame(sorted({(g, p) for (g, p, _s) in a0}), columns=["game_id", "player_id"])
    if roster.empty:
        return {"status": "skipped", "detail": "no production rows for the slate", "written": 0}
    teams = _team_ids(con, roster, reg, team_hint or {}, run_date)
    roster["team_id"] = [
        teams.get((str(g), int(p)), -1)
        for g, p in zip(roster["game_id"], roster["player_id"], strict=True)
    ]
    dropped_team = int((roster["team_id"] < 0).sum())
    roster = roster[roster["team_id"] >= 0]
    slate_df = pd.DataFrame(
        {
            "game_id": gids,
            "home_team": [g.home_team for g in reg],
            "away_team": [g.away_team for g in reg],
        }
    )
    exp = sequences.build_live_export(
        con, run_date, season, slate_df, {g.game_id: to_report_dt(g.tipoff) for g in reg}, roster, to_report_dt(made_at),
        elo_params or sequences.load_elo(),
    )  # fmt: skip
    arr, meta = exp.arrays, exp.meta.reset_index(drop=True)
    n = len(meta)
    pad = b.n_ctx - arr["ctx"].shape[1]
    if pad != ERA_WIDTH:
        raise ValueError(
            f"schema mismatch: the live exporter makes {arr['ctx'].shape[1]} ctx columns, the bundle expects {b.n_ctx} "
            f"(= exporter + {ERA_WIDTH} zero era channels)"
        )
    ctx = np.hstack([arr["ctx"], np.zeros((n, pad), dtype=np.float32)])
    wd = Path(tempfile.mkdtemp(prefix="avg_shadow_", dir=work_dir))
    try:
        np.savez(
            wd / "in.npz", seq=arr["seq"], mask=arr["mask"], opp_step=arr["opp_step"], ctx=ctx, player_id=arr["player_id"],
            team_idx=arr["team_idx"], opp_idx=arr["opp_idx"], base=arr["base"],
        )  # fmt: skip
        cp = subprocess.run(
            [sys.executable, "-m", "nba.daily.avg_shadow.infer", "--bundle", str(b.path), "--in", str(wd / "in.npz"), "--out", str(wd / "out.npz")],
            capture_output=True, text=True, timeout=timeout,
        )  # fmt: skip
        if cp.returncode != 0:
            raise RuntimeError(f"inference rc={cp.returncode}: {cp.stderr.strip()[-300:]}")
        with np.load(wd / "out.npz") as out:
            seq_q = out["q"]  # (n, 4, 19)
            infer_s = float(out["seconds"])
    finally:
        for f in wd.glob("*"):
            f.unlink()
        wd.rmdir()
    gate = b.gate()
    rows: list[dict[str, object]] = []
    n_no_full = n_no_a0 = n_prior_lt3 = 0
    for si, stat in enumerate(STATS):
        keep, a0q, fq = [], [], []
        for i in range(n):
            key = (str(meta["game_id"][i]), int(meta["player_id"][i]), stat)
            if int(meta["n_prior"][i]) < MIN_PRIOR:
                n_prior_lt3 += 1
                continue
            if key not in a0:
                n_no_a0 += 1
                continue
            if key not in full:
                n_no_full += 1
                continue
            keep.append(i)
            a0q.append(a0[key][0])
            fq.append(full[key])
        if not keep:
            continue
        sel = np.zeros(n, dtype=bool)
        sel[keep] = True
        w = _gate_weights(gate, stat, meta, sel)
        arms = grids.build_arms(
            np.array(a0q),
            np.array(fq),
            seq_q[keep, si, :].astype(float),
            w,
            np.ones(len(keep), dtype=bool),
        )
        avg_q19, seqc_q19 = grids.q19_of_grid199(arms["AVG"]), grids.q19_of_grid199(arms["SEQc"])
        for j, i in enumerate(keep):
            gid, pid = str(meta["game_id"][i]), int(meta["player_id"][i])
            rows.append(
                {
                    "made_at": made_at, "run_id": run_id, "game_id": gid, "player_id": pid, "stat": stat, "tipoff_utc": tip[gid],
                    "tip_source": "real", "in_prod_log": True, "n_prior": int(meta["n_prior"][i]), "model_id": b.model_id,
                    "fit_through": b.fit_through, "month_block": b.month_block, "installed_at": b.installed_at, "gate_id": b.gate_id,
                    "gate_fit_through": b.gate_fit_through, "stale_model": bool(stale),
                    "avg_pmf199": json.dumps([int(v) for v in arms["AVG"][j]]), "seqc_pmf199": json.dumps([int(v) for v in arms["SEQc"][j]]),
                    "avg_q19": json.dumps([int(v) for v in avg_q19[j]]), "seqc_q19": json.dumps([int(v) for v in seqc_q19[j]]),
                    "seq_q19": json.dumps([float(v) for v in seq_q[i, si, :]]), "full_q19": json.dumps(fq[j]),
                    "a0_q19": json.dumps(a0q[j]), "a0_source": a0[(gid, pid, stat)][1],
                }
            )  # fmt: skip
    written, deduped, refused = store.append_rows(con, rows, made_at)
    return {
        "status": "ok" if full else "no_full", "seq_rows": n, "rows": len(rows), "written": written, "deduped": deduped, "refused_after_tip": refused,
        "no_full": n_no_full, "no_a0": n_no_a0, "n_prior_lt3": n_prior_lt3, "dropped_no_team": dropped_team,
        "stale_model": bool(stale), "model_id": b.model_id, "infer_seconds": round(infer_s, 1),
    }  # fmt: skip


def _team_ids(
    con: duckdb.DuckDBPyConnection,
    roster: pd.DataFrame,
    reg: list[SlateGame],
    known: dict[tuple[str, int], int],
    run_date: dt.date,
) -> dict[tuple[str, int], int]:
    """Team of each rostered player: the pipeline's wide-roster frame when given (``known``), else the team of his latest box row before
    ``run_date`` if that team plays in the game. A player whose team cannot be established is dropped and counted."""
    playing = {g.game_id: {g.home_team, g.away_team} for g in reg}
    teams = {k: t for k, t in known.items() if t in playing.get(k[0], set())}
    pids = sorted({int(p) for p in roster["player_id"]})
    if not pids:
        return teams
    pm = ",".join("?" for _ in pids)
    last = {
        int(p): int(t)
        for p, t in con.execute(
            f"""SELECT player_id, team_id FROM (
                    SELECT s.player_id, s.team_id, row_number() OVER (PARTITION BY s.player_id ORDER BY g.game_date DESC, g.game_id DESC) rn
                    FROM player_game_stats s JOIN games g USING (game_id)
                    WHERE s.player_id IN ({pm}) AND g.game_date < ?) WHERE rn = 1""",
            [*pids, run_date],
        ).fetchall()
    }
    for g, p in zip(roster["game_id"], roster["player_id"], strict=True):
        k = (str(g), int(p))
        if k not in teams and last.get(int(p)) in playing.get(k[0], set()):
            teams[k] = last[int(p)]
    return teams
