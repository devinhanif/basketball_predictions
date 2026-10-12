# ruff: noqa: E501
"""RETURNING_TEAMMATE minimum-data gate (docs/prereg/RETURNING_TEAMMATE.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.returning_teammate_gate

Builds the as-of "returning teammate" columns for every stored A0 row (``reports/lower_tail``), measures
how often they fire, how often a flagged returner actually plays (knowability; the 2026-10-10 T-60 flag
played 16.5% of the time), and sizes the rule from the stored FULL-minus-A0 per-row deltas (variance and
design effect) and the descriptive effect already seen (T476: -0.81 pts on return game 1). Outcome-free
except for the returner's own play flag (not the target). No candidate arm is fitted or scored.
Writes ``reports/prereg_returning_teammate/gate.json``.

Feature definition (as-of, for player i of team T in game g; ``k`` = T's game order in the season):
* returner j of T for g: j played for T earlier this season; j played none of T's last ``MIN_MISSED`` (3)
  games (streak = games since j's last played T game >= 3); j played for no other team since (as-of the
  dates); j is listed (any status) on the latest T-60 snapshot of T's previous game (``require_listed``,
  variant B; variant A drops this); and j is NOT ``out`` or ``doubtful`` on the latest snapshot stamped
  <= g's real tip - 60 (doubtful added after the first gate run showed doubtful returners play 1%; disclosed).
* ``ret_min``: sum over returners (excluding i) of j's mean minutes over his last 10 played games before g.
* ``ret_min_star``: the same, only returners with that mean >= 30.
* ``n_ret``: count of returners.
* ``ret_own_gain``: i's mean minutes over his played games in the window (T's last min(5, longest streak)
  games before g) minus i's mean over his last 40 played games before the window; 0 when either is empty
  or nobody returns.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_returning_teammate/gate.json"
MIN_MISSED = 3
STAR_MIN = 30.0
ROTATION_MIN = 10.0
PRE_WINDOW = 10
OWN_WINDOW_MAX = 5
OWN_BASE = 40
FLOOR = -0.005
DESC_EFFECT_STAR_GAME1 = (
    -0.81
)  # T476, pts actual-minus-median vs baseline, star back game 1 (post-hoc, played)
DESC_STAR_MPG = (
    33.0  # scale: a 33-mpg returner carries the full descriptive effect (assumption, disclosed)
)
FEATURES = ("ret_min", "ret_min_star", "n_ret", "ret_own_gain")


def build_returning(
    box: pl.DataFrame,
    tg: pl.DataFrame,
    status: pl.DataFrame,
    require_listed: bool = True,
    exclude: tuple[str, ...] = ("out", "doubtful"),
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """As-of returning-teammate columns.

    ``box``: game_id, team_id, player_id, minutes (NULL or 0 = did not play), game_date.
    ``tg``: team_id, season, game_id, game_date, k (``team_game_index``).
    ``status``: game_id, player_id, status (``latest_t60``: the latest snapshot <= tip - 60).
    Returns (features per (game_id, team_id, player_id) for every box player of the team in g, and the
    flagged returners per (game_id, team_id, returner_id, streak, pre_mpg)). For game g only box rows of
    T's earlier games, other teams' games dated strictly before g, the T-60 snapshot of T's previous
    game and g's own T-60 snapshot are read; g's own box row is used only to enumerate who gets a row.
    """
    played = box.with_columns(pl.col("minutes").fill_null(0.0).cast(pl.Float64)).join(
        tg.select("team_id", "game_id", "season", "k"), on=["team_id", "game_id"], how="inner"
    )
    st = {
        (str(gid), int(pid)): s
        for gid, pid, s in status.select("game_id", "player_id", "status").iter_rows()
    }
    # each player's played games across teams, by date (for "played elsewhere since" and minute means)
    pl_hist: dict[int, list[tuple[Any, int, float]]] = defaultdict(list)
    for d, team, pid, m in (
        played.filter(pl.col("minutes") > 0)
        .sort("game_date")
        .select("game_date", "team_id", "player_id", "minutes")
        .iter_rows()
    ):
        pl_hist[int(pid)].append((d, int(team), float(m)))

    feats: list[tuple[str, int, int, float, float, int, float]] = []
    rets: list[tuple[str, int, int, int, float]] = []
    for (team, _season), grp in played.group_by(["team_id", "season"], maintain_order=True):
        team = int(team)
        games = grp.select("k", "game_id", "game_date").unique().sort("k").rows()
        by_k: dict[int, dict[int, float]] = defaultdict(dict)
        for k, pid, m in grp.select("k", "player_id", "minutes").iter_rows():
            by_k[int(k)][int(pid)] = float(m)
        last_played: dict[int, int] = {}
        for k, gid, gdate in games:
            k = int(k)
            prev_gid = games[k - 1][1] if k >= 1 else None
            # returners as of g: streak >= MIN_MISSED, still with T, listed on the previous game's snapshot
            flagged: list[tuple[int, int, float]] = []
            for j, lk in last_played.items():
                streak = k - 1 - lk
                if streak < MIN_MISSED:
                    continue
                hist = [h for h in pl_hist[j] if h[0] < gdate]
                if hist and hist[-1][1] != team:
                    continue  # played for another team since: not a returner
                if require_listed and (prev_gid is None or (str(prev_gid), j) not in st):
                    continue
                if st.get((str(gid), j)) in exclude:
                    continue
                pre = [h[2] for h in hist][-PRE_WINDOW:]
                pre_mpg = float(np.mean(pre)) if pre else 0.0
                flagged.append((j, streak, pre_mpg))
                rets.append((str(gid), team, j, streak, pre_mpg))
            w = min(OWN_WINDOW_MAX, max((f[1] for f in flagged), default=0))
            win_ks = set(range(k - w, k))
            win_dates = {games[x][2] for x in win_ks if x >= 0}
            first_win = min(win_dates) if win_dates else gdate
            for i in by_k[k]:
                others = [f for f in flagged if f[0] != i]
                own = 0.0
                if others:
                    hi = [h for h in pl_hist[i] if h[0] < gdate]
                    in_w = [h[2] for h in hi if h[1] == team and h[0] in win_dates]
                    before = [h[2] for h in hi if h[0] < first_win][-OWN_BASE:]
                    if in_w and before:
                        own = float(np.mean(in_w) - np.mean(before))
                feats.append(
                    (
                        str(gid),
                        team,
                        int(i),
                        float(sum(f[2] for f in others)),
                        float(sum(f[2] for f in others if f[2] >= STAR_MIN)),
                        len(others),
                        own,
                    )
                )
            for pid, m in by_k[k].items():
                if m > 0:
                    last_played[pid] = k
    f = pl.DataFrame(
        feats,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "ret_min": pl.Float64,
            "ret_min_star": pl.Float64,
            "n_ret": pl.Int64,
            "ret_own_gain": pl.Float64,
        },
        orient="row",
    )
    r = pl.DataFrame(
        rets,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "returner_id": pl.Int64,
            "streak": pl.Int64,
            "pre_mpg": pl.Float64,
        },
        orient="row",
    )
    return f, r


def _a0_rows(stat: str) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components
    from research.eval import recent_role_full_eval as RF

    c = load_components(stat, RF.LT_DIR)
    if int(c["row_season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in stored components")
    return c


def main() -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    box = C.load_box(con)
    tg = C.team_game_index(box, games)
    status = C.latest_t60(C.load_report(con), games)
    out: dict[str, Any] = {"measured_at": C.now_cdt(), "seasons": list(C.SEASONS), "variants": {}}
    feats: dict[str, pl.DataFrame] = {}
    for name, req in (("B_listed_prev_game", True), ("A_brief_literal", False)):
        f, r = build_returning(box, tg, status, require_listed=req)
        print(
            f"[{time.time() - t0:5.1f}s] built variant {name}: {f.height} rows, {r.height} returner-games",
            flush=True,
        )
        r = r.join(games.select("game_id", "season"), on="game_id").filter(
            pl.col("season").is_in(C.SEASONS)
        )
        pb = box.select(
            "game_id", "player_id", pl.col("minutes").fill_null(0.0).alias("ret_minutes_tonight")
        )
        r = r.join(
            pb.rename({"player_id": "returner_id"}), on=["game_id", "returner_id"], how="left"
        )
        r = r.with_columns((pl.col("ret_minutes_tonight").fill_null(0.0) > 0).alias("plays"))
        st = r.join(
            status.rename({"player_id": "returner_id"}), on=["game_id", "returner_id"], how="left"
        )
        v: dict[str, Any] = {
            "returner_games": r.height,
            "play_rate": float(np.mean(r["plays"].to_numpy())),
            "play_rate_rotation(pre_mpg>=10)": float(
                np.mean(r.filter(pl.col("pre_mpg") >= ROTATION_MIN)["plays"].to_numpy())
            ),
            "play_rate_star(pre_mpg>=30)": float(
                np.mean(r.filter(pl.col("pre_mpg") >= STAR_MIN)["plays"].to_numpy())
            ),
            "n_star_returner_games": r.filter(pl.col("pre_mpg") >= STAR_MIN).height,
            "n_rotation_returner_games": r.filter(pl.col("pre_mpg") >= ROTATION_MIN).height,
            "play_rate_by_status_tonight": {
                str(k): [float(p), int(n)]
                for k, p, n in st.with_columns(pl.col("status").fill_null("unlisted"))
                .group_by("status")
                .agg(pl.col("plays").mean(), pl.len())
                .sort("status")
                .rows()
            },
        }
        out["variants"][name] = v
        feats[name] = f
    # attach to the stored A0 rows (pts components define the row set; all four stats share it)
    c = _a0_rows("pts")
    keys = pl.DataFrame(
        {"game_id": c["row_gid"].astype(str), "player_id": c["row_pid"].astype(np.int64)}
    )
    pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
    dates = dict(games.select("game_id", "game_date").rows())
    day = np.array([str(dates[g]) for g in c["row_gid"].astype(str)])
    gid = c["row_gid"].astype(str)
    season = c["row_season"]
    fv = keys.join(feats["B_listed_prev_game"], on=["game_id", "player_id"], how="left")
    out["attach"] = {
        "a0_rows": keys.height,
        "rows_with_feature_row": int(fv["ret_min"].is_not_null().sum()),
        "null_rows": int(fv["ret_min"].is_null().sum()),
    }
    fv = fv.with_columns(pl.col(x).fill_null(0) for x in FEATURES)
    ret_min = fv["ret_min"].to_numpy()
    ret_star = fv["ret_min_star"].to_numpy()
    rotation = c["row_min10"] >= ROTATION_MIN
    flag = ret_min > 0
    star_flag = (ret_star > 0) & rotation
    # missingness / firing by realised-minutes bucket (a NULL or firing pattern that tracks tonight's minutes is a leak)
    mins = c["row_minutes"]
    buckets = {
        "<5": mins < 5,
        "5-10": (mins >= 5) & (mins < 10),
        "10-20": (mins >= 10) & (mins < 20),
        ">=20": mins >= 20,
    }
    out["firing_by_realised_minutes_bucket"] = {
        b: float(flag[m].mean()) for b, m in buckets.items()
    }
    # sizing: per-row sd and deff from the stored FULL-minus-A0 deltas (a GBM refit with extra columns moves every row)
    sizing: dict[str, Any] = {}
    p_star = out["variants"]["B_listed_prev_game"]["play_rate_star(pre_mpg>=30)"]
    p_rot = out["variants"]["B_listed_prev_game"]["play_rate_rotation(pre_mpg>=10)"]
    for stat in ("pts", "reb", "ast", "fg3m"):
        cs = _a0_rows(stat)
        y = cs["row_y"]
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        q1 = RF.arm_qi(
            stat, RF.load_heads(stat, "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
        )
        d = PR.crps_int(q1, y) - PR.crps_int(
            q0, y
        )  # stored FULL - A0 (already frozen and scored, T467)
        per: dict[str, Any] = {}
        for s in C.SEASONS:
            m = season == s
            cg = C.cluster_stats(d[m], gid[m])
            cd = C.cluster_stats(d[m], day[m])
            deff = max(cg["deff"], cd["deff"])
            fm = m & flag
            cgf = C.cluster_stats(d[fm], gid[fm])
            e_all = e_slice = None
            scen: dict[str, Any] = {}
            if stat == "pts":
                grid = arm_grid("mixture", cs, pic, stat)
                for sc, p in (
                    ("generous_flag_as_good_as_posthoc", 1.0),
                    ("realistic_star_play_rate", p_star),
                    ("realistic_rotation_play_rate", p_rot),
                ):
                    b = (
                        DESC_EFFECT_STAR_GAME1
                        * p
                        * np.minimum(ret_min / DESC_STAR_MPG, 2.0)
                        * rotation
                    )
                    sel = m & (b != 0)
                    gain = np.zeros(len(y))
                    if sel.any():
                        qn = to_integer_support(grid[sel] + b[sel, None])
                        gain[sel] = C.expected_crps_gain(q0[sel].astype(float), qn)
                    scen[sc] = {
                        "p_returner_plays": p,
                        "rows_shifted": int(sel.sum()),
                        "expected_delta_all_rows": float(-gain[m].mean()),
                        "expected_delta_flagged_rows": float(-gain[fm].mean()) if fm.any() else 0.0,
                    }
                e_all = scen["realistic_star_play_rate"]["expected_delta_all_rows"]
                e_slice = scen["realistic_star_play_rate"]["expected_delta_flagged_rows"]
            per[str(s)] = {
                "n": int(m.sum()),
                "games": int(cg["clusters"]),
                "flag_rows": int(fm.sum()),
                "flag_share": float(flag[m].mean()),
                "star_flag_rows(rotation teammate)": int((m & star_flag).sum()),
                "deff_game": cg["deff"],
                "deff_date": cd["deff"],
                "primary_all_rows": C.sizing_row(
                    "all rows, floor -0.005", cg["sd_row"], deff, m.sum(), FLOOR, e_all
                ),
                "slice_flag_rows": C.sizing_row(
                    "flagged rows, floor -0.005",
                    cgf["sd_row"],
                    cgf["deff"],
                    fm.sum(),
                    FLOOR,
                    e_slice,
                ),
                "scenarios": scen,
            }
        sizing[stat] = per
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    out["sizing"] = sizing
    # gate verdict: pts primary (all rows, floor -0.005) must have HW <= |floor| AND power >= 0.5 per season at the realistic effect
    g: list[dict[str, Any]] = []
    for s in C.SEASONS:
        p = sizing["pts"][str(s)]["primary_all_rows"]
        g.append(
            {
                "check": f"pts {s} half-width <= 0.005",
                "value": p["halfwidth95"],
                "pass": p["hw_le_floor"],
            }
        )
        g.append(
            {
                "check": f"pts {s} power >= 0.5 at realistic effect",
                "value": p["power_one_season"],
                "pass": p["power_one_season"] >= 0.5,
            }
        )
    for s in C.SEASONS:
        p = sizing["pts"][str(s)]["slice_flag_rows"]
        g.append(
            {
                "check": f"(alt design, flagged rows) pts {s} power >= 0.5 at realistic effect",
                "value": p["power_one_season"],
                "pass": p["power_one_season"] >= 0.5,
                "decides": False,
            }
        )
    pr = out["variants"]["B_listed_prev_game"]["play_rate_star(pre_mpg>=30)"]
    g.append(
        {
            "check": "star returner plays >= 0.5 of flagged games (knowable at T-60)",
            "value": pr,
            "pass": pr >= 0.5,
        }
    )
    out["gate"] = g
    out["gate_pass"] = all(x["pass"] for x in g if x.get("decides", True))
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(
        json.dumps(
            {"gate": g, "variants": out["variants"], "attach": out["attach"]},
            indent=1,
            default=float,
        )
    )
    return out


if __name__ == "__main__":
    main()


__all__ = ["build_returning", "main", "FEATURES", "Path"]
