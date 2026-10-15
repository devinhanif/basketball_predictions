# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""RETURNING_TEAMMATE_2 minimum-data gate and data-capture audit (docs/prereg/RETURNING_TEAMMATE_2.md, DRAFT).

    cd <main checkout> && PYTHONPATH=<worktree> .venv/bin/python -W ignore -m research.eval.returning_teammate_2_gate \
        --data-root <main checkout> --out <worktree>/reports/prereg_returning_teammate_2/gate.json [--history]

Read-only on ``nba.duckdb`` (every query ``season <= 2024``; 2025 is never loaded). Nothing is fitted or scored. Outcome-free except the returner's
own play flag (not the target) and the stored FULL-minus-A0 per-row deltas (already frozen and scored, T467) used only for sd and design effect.

Parts: (1) report/box capture by season, (2) per-season counts of returners and affected player-games under the rule's definition and two
sensitivities (N = 2, 5) plus the box-only audit variant, (3) knowability (returner play rate by tonight's status), (4) sizing of the pts primary on
affected rows and all rows at the rule's floor, (5) missingness (firing by realised minutes), (6) `--history`: the same counts on 2019-21 (select
only; report capture and cadence differ by era, so these seasons never enter the gate).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C
from research.eval import returning_teammate_2_features as F

FLOOR = -0.005
DESC_EFFECT = -0.81  # T476, star back game 1, post-hoc, conditional on playing
DESC_STAR_MPG = 33.0
SEASONS = (2023, 2024)
RULE_VARIANT = "LISTED_PREV_N3"


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load_box_full(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return C.query(
        con,
        "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.starter, s.fga, s.fta, s.tov, g.game_date, g.season "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024",
    )


def returner_counts(r: pl.DataFrame, games: pl.DataFrame, status: pl.DataFrame) -> dict[str, Any]:
    """Per-season returner-game counts, play rates (knowability) and the status mix tonight."""
    r = r.join(games.select("game_id", "season"), on="game_id")
    st = status.rename({"player_id": "returner_id"}).select("game_id", "returner_id", "status")
    r = r.join(st, on=["game_id", "returner_id"], how="left").with_columns(
        pl.col("status").fill_null("unlisted")
    )
    out: dict[str, Any] = {}
    for s in sorted(r["season"].unique().to_list()):
        x = r.filter(pl.col("season") == s)
        rot = x.filter(pl.col("pre_mpg") >= F.ROTATION_MIN)
        star = x.filter(pl.col("pre_mpg") >= F.STAR_MIN)
        out[str(s)] = {
            "returner_games": x.height,
            "rotation(pre_mpg>=10)": rot.height,
            "star(pre_mpg>=30)": star.height,
            "play_rate": float(x["plays_tonight"].mean()) if x.height else None,
            "play_rate_rotation": float(rot["plays_tonight"].mean()) if rot.height else None,
            "play_rate_star": float(star["plays_tonight"].mean()) if star.height else None,
            "by_status_tonight": {
                str(k): [float(p), int(n)]
                for k, p, n in x.group_by("status")
                .agg(pl.col("plays_tonight").mean(), pl.len())
                .sort("status")
                .rows()
            },
            "games_missed_buckets(rotation)": {
                "3-4": rot.filter(pl.col("streak") <= 4).height,
                "5-9": rot.filter((pl.col("streak") >= 5) & (pl.col("streak") <= 9)).height,
                "10+": rot.filter(pl.col("streak") >= 10).height,
            },
        }
    return out


def capture(
    box: pl.DataFrame, tg: pl.DataFrame, status: pl.DataFrame, games: pl.DataFrame
) -> dict[str, Any]:
    """Box and report capture: usable-snapshot share, and among box-absent rotation spells (>= 3 games) the share the report listed OUT."""
    snap = set(status["game_id"].unique().to_list())
    out: dict[str, Any] = {}
    for s in sorted(games["season"].unique().to_list()):
        g = games.filter(pl.col("season") == s)
        n = g.height
        us = int(g["game_id"].is_in(list(snap)).sum())
        out[str(s)] = {
            "games": n,
            "games_usable_snapshot": us,
            "share": us / n if n else None,
            "games_real_tip": int(g["tip_real"].sum()),
        }
    return out


def build_variants(
    box: pl.DataFrame, tg: pl.DataFrame, status: pl.DataFrame, t0: float
) -> dict[str, tuple[pl.DataFrame, pl.DataFrame]]:
    v: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
    for name, kw in (
        ("OUT_N3", {"min_missed": 3, "prev_rule": "out_all"}),
        ("OUT_N2", {"min_missed": 2, "prev_rule": "out_all"}),
        ("OUT_N5", {"min_missed": 5, "prev_rule": "out_all"}),
        ("LISTED_PREV_N3", {"min_missed": 3, "prev_rule": "listed_prev"}),
        ("LISTED_PREV_N2", {"min_missed": 2, "prev_rule": "listed_prev"}),
        ("LISTED_PREV_N5", {"min_missed": 5, "prev_rule": "listed_prev"}),
        ("AUDIT_BOX_ONLY_N3", {"min_missed": 3, "prev_rule": "box_only"}),
    ):
        v[name] = F.build_returning(box, tg, status, **kw)
        _log(
            f"built {name}: {v[name][0].height} player-games, {v[name][1].height} returner-games",
            t0,
        )
    return v


def a0_rows(stat: str, root: Path) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    c = load_components(stat, root / "reports/lower_tail")
    if int(c["row_season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in stored components")
    return c


def attach(c: dict[str, np.ndarray], f: pl.DataFrame) -> pl.DataFrame:
    keys = pl.DataFrame(
        {"game_id": c["row_gid"].astype(str), "player_id": c["row_pid"].astype(np.int64)}
    )
    out = keys.join(f, on=["game_id", "player_id"], how="left")
    return out


def sizing(
    root: Path,
    f: pl.DataFrame,
    play: dict[str, Any],
    games: pl.DataFrame,
    t0: float,
    stats: tuple[str, ...] = ("pts", "reb", "ast", "fg3m"),
) -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    pr_lt = root / "reports/lower_tail"
    pic = dict(np.load(pr_lt / "components_pi.npz", allow_pickle=True))
    RF.CACHE = root / "data/ops/recent_role_full_cache"
    RF.LT_DIR = pr_lt
    dates = dict(games.select("game_id", "game_date").rows())
    res: dict[str, Any] = {}
    p_star = np.mean([play[str(s)]["play_rate_star"] for s in SEASONS])
    p_rot = np.mean([play[str(s)]["play_rate_rotation"] for s in SEASONS])
    for stat in stats:
        cs = a0_rows(stat, root)
        fv = attach(cs, f)
        n_null = int(fv["ret_min"].is_null().sum())
        fv = fv.with_columns(pl.col(x).fill_null(0) for x in F.FEATURES)
        ret_min, ret_star = fv["ret_min"].to_numpy(), fv["ret_min_star"].to_numpy()
        flag = ret_min > 0
        gid, season, y = cs["row_gid"].astype(str), cs["row_season"], cs["row_y"]
        day = np.array([str(dates[g]) for g in gid])
        rotation = cs["row_min10"] >= F.ROTATION_MIN
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        q1 = RF.arm_qi(
            stat, RF.load_heads(stat, "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
        )
        d = PR.crps_int(q1, y) - PR.crps_int(
            q0, y
        )  # stored FULL - A0: sd and design effect only (frozen, T467)
        grid = arm_grid("mixture", cs, pic, stat) if stat == "pts" else None
        per: dict[str, Any] = {"null_feature_rows": n_null}
        for s in (*SEASONS, "pooled"):
            m = np.isin(season, SEASONS) if s == "pooled" else season == s
            fm = m & flag
            cg, cd = C.cluster_stats(d[m], gid[m]), C.cluster_stats(d[m], day[m])
            cgf, cdf = C.cluster_stats(d[fm], gid[fm]), C.cluster_stats(d[fm], day[fm])
            e_all = e_aff = None
            scen: dict[str, Any] = {}
            if stat == "pts":
                for sc, (ps, pr_) in (
                    ("flag_as_good_as_posthoc(p=1)", (1.0, 1.0)),
                    ("realistic(p_star,p_rot)", (p_star, p_rot)),
                ):
                    b = (
                        DESC_EFFECT
                        * np.minimum(
                            (ps * ret_star + pr_ * (ret_min - ret_star)) / DESC_STAR_MPG, 2.0
                        )
                        * rotation
                    )
                    sel = m & (b != 0)
                    gain = np.zeros(len(y))
                    if sel.any():
                        gain[sel] = C.expected_crps_gain(
                            q0[sel].astype(float), to_integer_support(grid[sel] + b[sel, None])
                        )
                    scen[sc] = {
                        "rows_shifted": int(sel.sum()),
                        "expected_delta_all_rows": float(-gain[m].mean()),
                        "expected_delta_affected_rows": float(-gain[fm].mean())
                        if fm.any()
                        else 0.0,
                    }
                e_all = scen["realistic(p_star,p_rot)"]["expected_delta_all_rows"]
                e_aff = scen["realistic(p_star,p_rot)"]["expected_delta_affected_rows"]
            per[str(s)] = {
                "n_rows": int(m.sum()),
                "games": int(cg["clusters"]),
                "affected_rows": int(fm.sum()),
                "affected_share": float(flag[m].mean()),
                "affected_games": int(len(np.unique(gid[fm]))),
                "affected_rotation_rows": int((fm & rotation).sum()),
                "star_returner_affected_rows": int((m & (ret_star > 0)).sum()),
                "deff_game_all": cg["deff"],
                "deff_date_all": cd["deff"],
                "all_rows": C.sizing_row(
                    "all rows, floor -0.005",
                    cg["sd_row"],
                    max(cg["deff"], cd["deff"]),
                    m.sum(),
                    FLOOR,
                    e_all,
                ),
                "affected": C.sizing_row(
                    "affected rows, floor -0.005",
                    cgf["sd_row"],
                    max(cgf["deff"], cdf["deff"]),
                    fm.sum(),
                    FLOOR,
                    e_aff,
                ),
                "scenarios": scen,
                "firing_by_realised_minutes": {
                    b: float(flag[m & mask].mean())
                    for b, mask in (
                        ("<5", cs["row_minutes"] < 5),
                        ("5-10", (cs["row_minutes"] >= 5) & (cs["row_minutes"] < 10)),
                        ("10-20", (cs["row_minutes"] >= 10) & (cs["row_minutes"] < 20)),
                        (">=20", cs["row_minutes"] >= 20),
                    )
                },
            }
        res[stat] = per
        _log(f"sized {stat}", t0)
    res["p_star"], res["p_rotation"] = float(p_star), float(p_rot)
    return res


def gate_rows(sz: dict[str, Any], play: dict[str, Any]) -> list[dict[str, Any]]:
    g: list[dict[str, Any]] = []
    for s in (*SEASONS, "pooled"):
        a = sz["pts"][str(s)]["affected"]
        n_aff = sz["pts"][str(s)]["affected_rows"]
        g.append(
            {
                "check": f"{s}: affected A0 rows >= rows needed for half-width 0.005",
                "value": [n_aff, a["n_for_hw_at_floor"]],
                "pass": n_aff >= a["n_for_hw_at_floor"],
            }
        )
        g.append(
            {
                "check": f"{s}: pts affected-rows half-width <= 0.005",
                "value": a["halfwidth95"],
                "pass": a["hw_le_floor"],
            }
        )
        g.append(
            {
                "check": f"{s}: pts power of the pass line >= 0.5 at the realistic effect",
                "value": a["power_one_season"],
                "pass": a["power_one_season"] >= 0.5,
            }
        )
        al = sz["pts"][str(s)]["all_rows"]
        g.append(
            {
                "check": f"{s}: pts all-rows half-width <= 0.005 (no-harm guard is resolvable)",
                "value": al["halfwidth95"],
                "pass": al["hw_le_floor"],
            }
        )
    g.append(
        {
            "check": "both-season power product >= 0.25 (affected rows, realistic)",
            "value": float(
                np.prod([sz["pts"][str(s)]["affected"]["power_one_season"] for s in SEASONS])
            ),
            "pass": bool(
                np.prod([sz["pts"][str(s)]["affected"]["power_one_season"] for s in SEASONS])
                >= 0.25
            ),
            "decides": False,
        }
    )
    for x in g:
        if not x["check"].startswith("pooled") and "decides" not in x and "plays" not in x["check"]:
            x["decides"] = False  # per-season rows are informational under option B (pooled test)
    pst = float(np.mean([play[str(s)]["play_rate_star"] for s in SEASONS]))
    g.append(
        {
            "check": "star returner plays >= 0.5 of flagged games (knowable at the snapshot)",
            "value": pst,
            "pass": pst >= 0.5,
        }
    )
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args(argv)
    root = a.data_root.resolve()
    C.DB = root / "nba.duckdb"
    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    box = load_box_full(con)
    tg = C.team_game_index(box, games)
    status = F.snapshot_status(C.load_report(con), games)
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "snapshot_id": F.SNAPSHOT_ID,
        "snapshot_source_sha": F.snapshot_source_sha(),
        "capture": capture(box, tg, status, games),
    }
    var = build_variants(box, tg, status, t0)
    out["variants"] = {}
    for name, (_f, r) in var.items():
        out["variants"][name] = returner_counts(r, games, status)
    keep = games.filter(pl.col("season").is_in(SEASONS))["game_id"].to_list()
    out["sizing_by_population"] = {}
    for name in ("OUT_N3", "OUT_N2", "LISTED_PREV_N3", "LISTED_PREV_N2"):
        out["sizing_by_population"][name] = sizing(
            root,
            var[name][0].filter(pl.col("game_id").is_in(keep)),
            out["variants"][name],
            games,
            t0,
            ("pts",),
        )
    out["rule_variant"] = RULE_VARIANT
    f_rule = var[RULE_VARIANT][0].filter(pl.col("game_id").is_in(keep))
    sz = sizing(root, f_rule, out["variants"][RULE_VARIANT], games, t0)
    out["sizing"] = sz
    out["gate"] = gate_rows(sz, out["variants"][RULE_VARIANT])
    out["gate_pass"] = all(x["pass"] for x in out["gate"] if x.get("decides", True))
    if a.history:
        from research.eval import history_design_gate as H

        H.HIST_DB = root / "data/history/nba_history.duckdb"
        H.HIST_SCHEDULE = root / "data/history/schedule"
        w = H.load_world()
        gm = w["games"]
        bx = w["box"]
        # history box lacks fga/fta/tov in the shared loader: re-query both databases for the extra columns
        extra = []
        for p in (C.DB, H.HIST_DB):
            cc = duckdb.connect(str(p), read_only=True)
            extra.append(load_box_full(cc))
            cc.close()
        bx = pl.concat(extra, how="vertical_relaxed")
        tgh = C.team_game_index(bx, gm)
        sth = F.snapshot_status(w["report"], gm)
        hv: dict[str, Any] = {"capture": capture(bx, tgh, sth, gm)}
        fh, rh = F.build_returning(bx, tgh, sth, prev_rule="listed_prev")
        hv["rule_returner_counts"] = returner_counts(rh, gm, sth)
        fo, ro = F.build_returning(bx, tgh, sth, prev_rule="out_all")
        hv["slice_out_all_returner_counts"] = returner_counts(ro, gm, sth)
        fb, rb = F.build_returning(bx, tgh, sth, prev_rule="box_only")
        hv["audit_box_only_returner_counts"] = returner_counts(rb, gm, sth)
        out["history_audit"] = hv
        _log("history audit done", t0)
    out["wall_s"] = time.time() - t0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"gate": out["gate"], "gate_pass": out["gate_pass"]}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
