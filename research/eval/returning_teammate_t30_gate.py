# ruff: noqa: E501
"""RETURNING_TEAMMATE_LIVE_T30 minimum-data gate (docs/prereg/RETURNING_TEAMMATE_LIVE_T30.md, DRAFT). Seasons 2023-24.

    .venv/bin/python -W ignore -m research.eval.returning_teammate_t30_gate

The live rule computes the returning-teammate columns at T-30 from the confirmed lineup snapshot (returner Active)
and the latest official report. Lineup snapshots exist only from 2026-10-09 (data/lineups/lineups.duckdb), so
2023-24 are sized with a PROXY: "active at T-30" is replaced by "played tonight" (box minutes > 0). The proxy
over-states the flag's quality (an Active returner can still sit) and so the expected effect; stated in the rule.
Per-row sd and design effect come from the stored FULL-minus-A0 pts deltas (already frozen and scored, T467) on
the proxy-flagged rows; the expected effect is outcome-free (CRPS divergence of the A0 grid shifted by the post-hoc
T476 -0.81 pts, p = 1). Also measured: sd and design effect of the pts residual (y minus A0 integer median) on the
flagged rows, for the bias-form alternative (a variance only; the mean is not computed). Nothing is fitted or
scored. Writes ``reports/prereg_returning_teammate_t30/gate.json``.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C
from research.eval import returning_teammate_gate as RT

OUT = C.ROOT / "reports/prereg_returning_teammate_t30/gate.json"
FLOOR = -0.005
LOGGED_SHARE = (
    0.85  # FORWARD_PREREG_2026_27 placeholder share of games with a usable confirmed T-30 lineup
)
FWD_GAMES = 1230
SEASON_DAYS = 175  # 2026-10-20 .. 2027-04-11 regular season (about 25 weeks)
SEASON_START = "2026-10-20"
INTERIM_FRAC = 0.5


def proxy_t30_columns(
    r: pl.DataFrame, box: pl.DataFrame
) -> pl.DataFrame:  # per (game_id, team_id, player_id): ret_min_t30
    """Sum over returners who PLAYED tonight (the proxy for Active at T-30) of their pre-absence mpg, excluding the
    row's own player."""
    pb = box.select("game_id", "player_id", pl.col("minutes").fill_null(0.0).alias("m"))
    act = r.join(
        pb.rename({"player_id": "returner_id"}), on=["game_id", "returner_id"], how="left"
    ).filter(pl.col("m").fill_null(0.0) > 0)
    tot = act.group_by("game_id", "team_id").agg(
        pl.col("pre_mpg").sum().alias("tot"), pl.len().alias("n_act")
    )
    own = act.select(
        "game_id",
        "team_id",
        pl.col("returner_id").alias("player_id"),
        pl.col("pre_mpg").alias("own"),
    )
    rows = box.select("game_id", "team_id", "player_id").unique()
    return (
        rows.join(tot, on=["game_id", "team_id"], how="left")
        .join(own, on=["game_id", "team_id", "player_id"], how="left")
        .with_columns(
            (pl.col("tot").fill_null(0.0) - pl.col("own").fill_null(0.0)).alias("ret_min_t30"),
            pl.col("n_act").fill_null(0),
        )
        .select("game_id", "team_id", "player_id", "ret_min_t30", "n_act")
    )


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
    _f, r = RT.build_returning(box, tg, status, require_listed=True)
    cols = proxy_t30_columns(r, box)
    c = RT._a0_rows("pts")
    keys = pl.DataFrame(
        {"game_id": c["row_gid"].astype(str), "player_id": c["row_pid"].astype(np.int64)}
    )
    fv = keys.join(
        cols.drop("team_id").unique(["game_id", "player_id"]),
        on=["game_id", "player_id"],
        how="left",
    )
    ret = fv["ret_min_t30"].fill_null(0.0).to_numpy()
    flag = ret > 0
    gid = c["row_gid"].astype(str)
    season = c["row_season"]
    dates = dict(games.select("game_id", "game_date").rows())
    day = np.array([str(dates[g]) for g in gid])
    pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
    y = c["row_y"]
    q0 = RF.arm_qi("pts", c, pic, pic["row_pi"])
    q1 = RF.arm_qi(
        "pts", RF.load_heads("pts", "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
    )
    d = PR.crps_int(q1, y) - PR.crps_int(q0, y)
    med = q0[:, q0.shape[1] // 2].astype(float)
    res = y - med
    grid = arm_grid("mixture", c, pic, "pts")
    rotation = c["row_min10"] >= RT.ROTATION_MIN
    b = RT.DESC_EFFECT_STAR_GAME1 * np.minimum(ret / RT.DESC_STAR_MPG, 2.0) * rotation
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "seasons": list(C.SEASONS),
        "proxy": "returner played tonight (box) for Active at T-30",
        "by_season": {},
    }
    for s in C.SEASONS:
        m = season == s
        fm = m & flag
        cg = C.cluster_stats(d[fm], gid[fm])
        cd = C.cluster_stats(d[fm], day[fm])
        deff = max(1.0, cg["deff"], cd["deff"])
        sel = fm & (b != 0)
        gain = np.zeros(len(y))
        if sel.any():
            gain[sel] = C.expected_crps_gain(
                q0[sel].astype(float), to_integer_support(grid[sel] + b[sel, None])
            )
        eff = float(-gain[fm].mean())
        rg = C.cluster_stats(res[fm], gid[fm])
        rd = C.cluster_stats(res[fm], day[fm])
        games_s = len(np.unique(gid[m]))
        rows_per_game = float(fm.sum()) / games_s
        n_hw = (1.96 * cg["sd_row"] * math.sqrt(deff) / abs(FLOOR)) ** 2
        fwd_rows = rows_per_game * FWD_GAMES * LOGGED_SHARE
        se_fwd = cg["sd_row"] * math.sqrt(deff / fwd_rows)
        hw_fwd = 1.96 * se_fwd
        line = min(FLOOR, -hw_fwd)
        out["by_season"][str(s)] = {
            "rows": int(m.sum()),
            "games": games_s,
            "flagged_rows": int(fm.sum()),
            "flag_share": float(flag[m].mean()),
            "flagged_rows_per_game": rows_per_game,
            "rotation_flagged_rows": int((fm & rotation).sum()),
            "crps_delta_sd_row": cg["sd_row"],
            "deff_game": cg["deff"],
            "deff_date": cd["deff"],
            "deff_used": deff,
            "expected_effect_flagged_p1": eff,
            "n_flagged_rows_for_hw_le_floor": n_hw,
            "games_for_hw_le_floor": n_hw / (rows_per_game * LOGGED_SHARE),
            "forward_full_season": {
                "logged_flagged_rows": fwd_rows,
                "halfwidth95": hw_fwd,
                "pass_line": line,
                "power_at_p1_effect": float(C.pass_power(eff, line, se_fwd)),
                "power_at_half_effect": float(C.pass_power(eff / 2, line, se_fwd)),
            },
            "pooled_two_seasons": {
                "halfwidth95": hw_fwd / math.sqrt(2),
                "power_at_p1_effect": float(
                    C.pass_power(eff, min(FLOOR, -hw_fwd / math.sqrt(2)), se_fwd / math.sqrt(2))
                ),
            },
            "bias_form": {
                "expected_shift_pts_flagged_p1": float(b[fm].mean()),
                "residual_sd_row": rg["sd_row"],
                "residual_deff": max(1.0, rg["deff"], rd["deff"]),
                "full_season_se_pts": rg["sd_row"]
                * math.sqrt(max(1.0, rg["deff"], rd["deff"]) / fwd_rows),
            },
        }
        print(
            f"[{time.time() - t0:5.1f}s] {s}: flagged {int(fm.sum())}, hw_fwd {hw_fwd:.4f}, eff {eff:.4f}",
            flush=True,
        )
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out["by_season"], indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
