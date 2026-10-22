# ruff: noqa: E501
# mypy: ignore-errors
"""COLDSTART_SHAREBALL_T15 minimum-data gate (rule: docs/prereg/COLDSTART_SHAREBALL_T15.md, DRAFT). No arm is fitted, no outcome is read.

    .venv/bin/python -W ignore -m research.eval.coldstart_shareball_gate [--out reports/prereg_coldstart_shareball/gate.json] [--no-power]

Read-only: ``nba.duckdb``, ``data/history/nba_history.duckdb`` and ``data/lineups/lineups.duckdb`` opened ``read_only=True``, ``season <= 2024``
in every SQL on the regular-season tables; season 2025 is never queried. Label columns of ``model_rows`` (``y``, realised ``minutes``)
are never selected. Disclosed membership reads (inputs, not labels): the box ``starter`` flag of the scored game (the T-30 announced-five
PROXY, the same one LINEUPS_KNOWN used) and the realised ``minutes > 0`` filter that defines A0 rows (A0 rows are rows that played).
Realised minutes are read only for PRIOR games / the PRIOR season, to build as-of rotations and vacated minutes. Stints (realised fives)
are NOT read: a stint five is what happened, not what was announced.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import role_prior_salary_eval as ev
from research.eval import team_usage_cap_gate as tcg

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
K_MAIN, K_SENS = 10, 5
X_DEFAULT = 0.20
PRIOR_MPG_MIN = (
    15.0  # a "departure" is a player with >= 15 mpg (>= 20 games) for the team last season
)
PRIOR_GAMES_MIN = 20
V_ONE_STARTER = 24.0  # vacated minutes per game worth one starter
OUT = Path("reports/prereg_coldstart_shareball/gate.json")
COLS = [
    "game_id",
    "player_id",
    "team_id",
    "game_date",
    "n_prior",
    "min10",
    "starter10",
    "n_out_rot",
    "has_report",
]
SIM_COLS = ["game_id", "player_id", "team_id", "game_date", "c", "s", "p_ge"]
log = tcg.log


def vacated(reg: pd.DataFrame, pre: pd.DataFrame, S: int) -> pd.DataFrame:
    """Per team: minutes per game (prior season S-1) of its >= 15 mpg players who are NOT on its season-S preseason log,
    split by whether the preseason log puts them with ANOTHER team (a confirmed departure) or nowhere (unknown: injury, release, retirement)."""
    prev = reg[reg.season == S - 1]
    ps = (
        prev.groupby(["player_id", "team_id"])
        .agg(g=("minutes", "size"), mpg=("minutes", "mean"))
        .reset_index()
    )
    ps = ps[(ps.g >= PRIOR_GAMES_MIN) & (ps.mpg >= PRIOR_MPG_MIN)]
    p = pre[pre.season == S]
    on_team = set(zip(p.player_id, p.team_id, strict=True))
    elsewhere = p.groupby("player_id").team_id.apply(set).to_dict()
    gone = ps[[(a, b) not in on_team for a, b in zip(ps.player_id, ps.team_id, strict=True)]].copy()
    gone["confirmed_elsewhere"] = [
        bool(elsewhere.get(a, set()) - {b})
        for a, b in zip(gone.player_id, gone.team_id, strict=True)
    ]
    out = gone.groupby("team_id").apply(
        lambda d: pd.Series(
            {
                "V_all": float(d.mpg.sum()),
                "V_confirmed": float(d.loc[d.confirmed_elsewhere, "mpg"].sum()),
                "n_gone": int(len(d)),
                "n_confirmed": int(d.confirmed_elsewhere.sum()),
            }
        )
    )
    return out.reset_index()


def starters_block(con: Any, mr: pd.DataFrame, S: int, scores: pd.DataFrame) -> dict[str, Any]:
    """T-30 reconstruction coverage from the box ``starter`` flag (announced-five PROXY). Counts only."""
    st = con.execute(
        "select b.game_id, b.team_id, b.player_id, b.starter from player_game_stats b join games g using(game_id) "
        "where g.season=? and g.season<=2024 and b.game_id like '002%'",
        [S],
    ).df()
    n5 = st.groupby(["game_id", "team_id"]).starter.sum()
    tgn = len(n5)
    m = mr.merge(st[["game_id", "player_id", "starter"]], on=["game_id", "player_id"], how="left")
    m["asof_starter"] = m.starter10 >= 0.5
    m["box_starter"] = m.starter.fillna(False).astype(bool)
    m["start_delta_nonzero"] = m.asof_starter != m.box_starter
    mv = m[(m["slice"] == "mover") & (m.gwt < K_MAIN)]
    return {
        "team_games": int(tgn),
        "exactly_5_box_starters": int((n5 == 5).sum()),
        "share_exactly_5": float((n5 == 5).mean()),
        "rows_start_flag_null": int(m.starter.isna().sum()),
        "all_rows_start_delta_nonzero_share": float(m.start_delta_nonzero.mean()),
        "mover_rows_K10": int(len(mv)),
        "mover_rows_box_starter_share": float(mv.box_starter.mean()),
        "mover_rows_start_delta_nonzero_share": float(mv.start_delta_nonzero.mean()),
        "mover_rows_start_delta_nonzero": int(mv.start_delta_nonzero.sum()),
        "all_rows_with_report_share": float(m.has_report.mean()) if "has_report" in m else None,
        "all_rows_n_out_rot_gt0_share": float((m.n_out_rot > 0).mean()),
        "mover_rows_n_out_rot_gt0_share": float((mv.n_out_rot > 0).mean()),
    }


def live_capture() -> dict[str, Any]:
    """2026-27 live T-30 / T-15 capture (counts of decisions; not season 2025)."""
    out: dict[str, Any] = {}
    c = duckdb.connect(str(arch.DATA / "nba.duckdb"), read_only=True)
    for t in ("forward_t30_decisions", "forward_t15_decisions"):
        rows = c.execute(f"select outcome, count(*) from {t} group by 1").fetchall()
        out[t] = {str(a): int(b) for a, b in rows}
    return out


def power(
    mr_by: dict[int, pd.DataFrame],
    keysets: dict[str, dict[int, set]],
    keycol: str,
    sigma_list: tuple[float, ...],
    shock: str,
) -> dict[str, Any]:
    """Half-width of an informationless shock (``noise_arm``) on the rows in ``keysets[name][season]``; shock shared within
    ``player-season`` or ``team-game``. Binding = widest of game / unit / week clustering. Pooled = both seasons stacked."""
    out: dict[str, Any] = {}
    for name, ks in keysets.items():
        prep: dict[tuple[int, str], tuple[np.ndarray, ...]] = {}
        for S in SEASONS:
            for st in STATS:
                df = arch.read_rows(st, S, SIM_COLS)
                df = df[df.game_id.str.startswith("002")].reset_index(drop=True)
                kk = ks[S]
                if keycol == "gp":
                    sel = np.flatnonzero(
                        [(g, p) in kk for g, p in zip(df.game_id, df.player_id, strict=True)]
                    )
                else:
                    sel = np.flatnonzero(
                        [(g, t) in kk for g, t in zip(df.game_id, df.team_id, strict=True)]
                    )
                q0 = arch.quantile_grid(df, sel, arch.month_blocks(df))
                gid = df.game_id.to_numpy()[sel]
                unit = (
                    np.array([f"{S}-{p}" for p in df.player_id.to_numpy()[sel]])
                    if shock == "player"
                    else np.array(
                        [f"{g}-{t}" for g, t in zip(gid, df.team_id.to_numpy()[sel], strict=True)]
                    )
                )
                wk = np.array(
                    [
                        f"{S}-{w}"
                        for w in pd.to_datetime(df.game_date.iloc[sel]).dt.strftime("%G-%V")
                    ]
                )
                prep[(S, st)] = (q0, unit, gid, wk)
        for label, seasons in [(str(S), (S,)) for S in SEASONS] + [("pooled", SEASONS)]:
            for st in STATS:
                q0 = np.vstack([prep[(S, st)][0] for S in seasons])
                unit = np.concatenate([prep[(S, st)][1] for S in seasons])
                gid = np.concatenate([prep[(S, st)][2] for S in seasons])
                wk = np.concatenate([prep[(S, st)][3] for S in seasons])
                cell: dict[str, Any] = {
                    "n_rows": int(len(q0)),
                    "n_games": int(len(np.unique(gid))),
                    "n_units": int(len(np.unique(unit))),
                    "n_weeks": int(len(np.unique(wk))),
                    "floor": FLOORS[st],
                }
                for sg in sigma_list:
                    r = ev.noise_arm(q0, unit, gid, wk, sg, 1, arch.crps_int_q)
                    hw = float(r["binding"]["half_width"])
                    cell[f"sigma{sg:.2f}"] = {
                        "binding": r["binding"]["clustering"],
                        "half_width": hw,
                        "rows_needed_for_floor": int(len(q0) * (hw / FLOORS[st]) ** 2),
                        "passes_hw_le_floor": bool(hw <= FLOORS[st]),
                    }
                out[f"{name}|{label}|{st}"] = cell
            log(f"power {name} {label} done")
    return out


def run(out: Path, do_power: bool) -> dict[str, Any]:
    c = duckdb.connect(str(arch.DATA / "nba.duckdb"), read_only=True)
    h = duckdb.connect(str(arch.HIST_DB), read_only=True)
    reg = arch.load_reg(c, h)
    assert int(reg.season.max()) <= 2024
    fe = arch.load_first_ever(c, h)
    games, box, pre = tcg.load(c)
    assert int(games.season.max()) <= 2024
    tg = tcg.team_games(games)
    app = tcg.asof_tables(box)
    scores = tcg.roster_scores(tg, app, pre)
    log(f"scores {len(scores)} team-games")
    res: dict[str, Any] = {"season_2025_loaded": False, "seasons": {}}
    mr_by: dict[int, pd.DataFrame] = {}
    mover_keys: dict[int, set] = {}
    mover5_keys: dict[int, set] = {}
    ns_keys: dict[int, set] = {}
    all_keys: dict[int, set] = {}
    for S in SEASONS:
        r = arch.read_rows("pts", S, COLS)
        r = r[r.game_id.str.startswith("002")].reset_index(drop=True)
        mr = arch.slice_frame(r, reg, fe, S)
        mr_by[S] = mr
        sc = scores[scores.season == S]
        nsk = set(zip(sc.game_id[sc.NS >= X_DEFAULT], sc.team_id[sc.NS >= X_DEFAULT], strict=True))
        in_ns = np.array([(g, t) in nsk for g, t in zip(mr.game_id, mr.team_id, strict=True)])
        mv10 = (mr["slice"] == "mover") & (mr.gwt < K_MAIN)
        mv5 = (mr["slice"] == "mover") & (mr.gwt < K_SENS)
        wk = pd.to_datetime(mr.game_date).dt.strftime("%G-%V")

        def cnt(m, mr=mr, wk=wk):
            d = mr[m]
            return {
                "rows": int(m.sum()),
                "players": int(d.player_id.nunique()),
                "games": int(d.game_id.nunique()),
                "team_seasons": int(d.team_id.nunique()),
                "weeks": int(wk[m].nunique()),
            }

        v = vacated(reg, pre, S)
        vm = mr[mv10].merge(v, on="team_id", how="left").fillna({"V_all": 0.0, "V_confirmed": 0.0})
        vshare = {
            "mover_rows_team_V_confirmed_ge_24mpg": float((vm.V_confirmed >= V_ONE_STARTER).mean()),
            "mover_rows_team_V_confirmed_gt_0": float((vm.V_confirmed > 0).mean()),
            "mover_rows_team_V_all_ge_24mpg": float((vm.V_all >= V_ONE_STARTER).mean()),
            "mover_rows_ge_24_confirmed_n": int((vm.V_confirmed >= V_ONE_STARTER).sum()),
            "teams_V_confirmed_ge_24mpg": int((v.V_confirmed >= V_ONE_STARTER).sum()),
            "teams_with_any_departure": int((v.V_all > 0).sum()),
            "median_V_confirmed_mpg": float(v.V_confirmed.median()) if len(v) else None,
        }
        # mover rows inside the large-roster-change slice
        res["seasons"][str(S)] = {
            "A0_rows_per_stat": int(len(mr)),
            "mover_K10": cnt(mv10),
            "mover_K5": cnt(mv5),
            "large_change_NS_ge_0.20_all_rows": cnt(pd.Series(in_ns, index=mr.index)),
            "large_change_NS_ge_0.20_team_games": int((sc.NS >= X_DEFAULT).sum()),
            "mover_K10_inside_large_change_slice": int((mv10 & in_ns).sum()),
            "vacated_role": vshare,
            "starters_T30_proxy": starters_block(c, mr, S, scores),
            "slice_counts_by_label": {k: int(n) for k, n in mr["slice"].value_counts().items()},
        }
        mover_keys[S] = set(zip(mr.game_id[mv10], mr.player_id[mv10], strict=True))
        mover5_keys[S] = set(zip(mr.game_id[mv5], mr.player_id[mv5], strict=True))
        ns_keys[S] = nsk
        all_keys[S] = set(zip(sc.game_id, sc.team_id, strict=True))
        log(f"season {S} counts done")
    res["live_capture_2026_27"] = live_capture()
    if do_power:
        res["power"] = {}
        res["power"].update(power(mr_by, {"movers_K10": mover_keys}, "gp", (0.05, 0.10), "player"))
        res["power"].update(
            power(mr_by, {"large_change_NS020": ns_keys}, "tg", (0.03, 0.05), "team_game")
        )
        res["power"].update(power(mr_by, {"everyone": all_keys}, "tg", (0.03,), "team_game"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=float))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-power", action="store_true")
    a = ap.parse_args(argv)
    run(Path(a.out), not a.no_power)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
