# ruff: noqa: E501
"""QUESTIONABLE_STREAK minimum-data gate (docs/prereg/QUESTIONABLE_STREAK.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.questionable_streak_gate

Builds the two as-of columns for every stored P(play) R row (``data/ops/recent_role_cache/scores.npz``: rostered,
not OUT at T-60, ``n_played_prior >= 5``; T455/T458) and sizes the rule from the stored per-row R-minus-C' log-loss
deltas (variance and design effect of a P(play) feature contrast already frozen and scored). There is NO prior
descriptive effect for the streak; the expected effect is given as scenarios (probability shifts on streak rows),
outcome-free (KL), and as the minimum detectable effect. No arm is fitted or scored.
Writes ``reports/prereg_questionable_streak/gate.json``.

Columns (as-of, for player i of team T in game g):
* ``q_streak``: number of consecutive team games immediately before g in which i was listed ``questionable`` on that
  game's latest T-60 snapshot AND played (minutes > 0). The walk stops at the first earlier game that fails either.
* ``q_change``: status code on g's latest T-60 snapshot minus the code on the EARLIEST usable snapshot of g that lists
  i (codes: unlisted 0, available 1, probable 2, questionable 3, doubtful 4, out 5); negative = upgraded during the
  day, 0 if listed once or never.
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_questionable_streak/gate.json"
SCORES = C.ROOT / "data/ops/recent_role_cache/scores.npz"
FLOOR = -0.005  # P(play) log loss, house floor (WORKLOAD / RECENT_ROLE / PPLAY_CONTEXT)
CODE = {"available": 1, "probable": 2, "questionable": 3, "doubtful": 4, "out": 5}
SCENARIO_SHIFTS = (
    0.05,
    0.10,
)  # assumed P(play) shift on questionable rows with q_streak >= 1 (no descriptive exists)


def first_listed_status(
    report: pl.DataFrame, games: pl.DataFrame, lead: int = C.LEAD_MINUTES
) -> pl.DataFrame:
    """Per (game, player): status on the earliest snapshot stamped <= tip - lead that lists the player."""
    r = report.join(games.select("game_id", "tip"), on="game_id", how="inner")
    r = r.filter(pl.col("as_of") <= pl.col("tip") - pl.duration(minutes=lead))
    return (
        r.sort("as_of")
        .group_by(["game_id", "player_id"])
        .agg(pl.col("status").first().alias("first_status"))
    )


def build_questionable_streak(
    queries: pl.DataFrame,
    tg: pl.DataFrame,
    t60: pl.DataFrame,
    first: pl.DataFrame,
    box: pl.DataFrame,
) -> pl.DataFrame:
    """``queries``: game_id, player_id, team_id. ``tg``: team_game_index. ``t60``: latest_t60. ``first``:
    first_listed_status. ``box``: game_id, player_id, minutes. Only T's games before g (k' < k), and g's own
    pre-tip snapshots, are read. Returns queries + q_streak, q_change."""
    st = {
        (str(g), int(p)): s for g, p, s in t60.select("game_id", "player_id", "status").iter_rows()
    }
    fs = {
        (str(g), int(p)): s
        for g, p, s in first.select("game_id", "player_id", "first_status").iter_rows()
    }
    mins = {
        (str(g), int(p)): (m or 0.0)
        for g, p, m in box.select("game_id", "player_id", "minutes").iter_rows()
    }
    k_of = {
        (int(t), str(g)): (int(s), int(k))
        for t, s, g, k in tg.select("team_id", "season", "game_id", "k").iter_rows()
    }
    gid_of = {
        (int(t), int(s), int(k)): str(g)
        for t, s, g, k in tg.select("team_id", "season", "game_id", "k").iter_rows()
    }
    streak, change = [], []
    for g, p, t in queries.select("game_id", "player_id", "team_id").iter_rows():
        g, p, t = str(g), int(p), int(t)
        sk = k_of.get((t, g))
        n = 0
        if sk is not None:
            s, k = sk
            j = k - 1
            while j >= 0:
                pg = gid_of[(t, s, j)]
                if st.get((pg, p)) == "questionable" and mins.get((pg, p), 0.0) > 0:
                    n += 1
                    j -= 1
                else:
                    break
        streak.append(n)
        now, f0 = st.get((g, p)), fs.get((g, p))
        change.append(CODE.get(now or "", 0) - CODE.get(f0 or "", 0) if (now and f0) else 0)
    return queries.with_columns(
        pl.Series("q_streak", streak, dtype=pl.Int64), pl.Series("q_change", change, dtype=pl.Int64)
    )


def main() -> dict[str, Any]:
    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    report = C.load_report(con)
    t60 = C.latest_t60(report, games)
    first = first_listed_status(report, games)
    box = C.load_box(con)
    tg = C.team_game_index(box, games)
    z = np.load(SCORES)
    if int(z["season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in the P(play) cache")
    m = np.isin(z["season"], C.SEASONS)
    rows = pl.DataFrame(
        {
            "game_id": z["gid"][m].astype(str),
            "player_id": z["pid"][m].astype(np.int64),
            "season": z["season"][m],
        }
    )
    q = rows.join(
        box.select("game_id", "player_id", pl.col("team_id").cast(pl.Int64)).unique(
            ["game_id", "player_id"]
        ),
        on=["game_id", "player_id"],
        how="left",
    )
    f = build_questionable_streak(q, tg, t60, first, box)
    f = f.join(
        t60.select("game_id", "player_id", "status"), on=["game_id", "player_id"], how="left"
    ).with_columns(pl.col("status").fill_null("unlisted"))
    print(f"[{time.time() - t0:5.1f}s] built {f.height} rows", flush=True)
    y, pc, pr = z["y"][m], z["p_Cp"][m], z["p_R"][m]

    def ll(p: np.ndarray) -> np.ndarray:
        q = np.clip(p, 1e-6, 1 - 1e-6)
        return np.asarray(-(y * np.log(q) + (1 - y) * np.log(1 - q)))

    d = ll(pr) - ll(pc)  # stored R - C' (T455, frozen and scored): only its spread is used
    gid = f["game_id"].to_numpy()
    season = f["season"].to_numpy()
    status = f["status"].to_numpy()
    qs = f["q_streak"].to_numpy()
    qc = f["q_change"].to_numpy()
    qrow = status == "questionable"
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "seasons": list(C.SEASONS),
        "rows": int(m.sum()),
        "team_missing": int(f["team_id"].is_null().sum()),
    }
    per: dict[str, Any] = {}
    for s in C.SEASONS:
        ms = season == s
        mq = ms & qrow
        cg = C.cluster_stats(d[ms], gid[ms])
        cq = C.cluster_stats(d[mq], gid[mq])
        scen: dict[str, Any] = {}
        for dlt in SCENARIO_SHIFTS:
            # questionable rows with q_streak >= 1 play more than R says by dlt; every other row unchanged (assumption; no descriptive)
            pn = np.where(mq & (qs >= 1), np.clip(pr + dlt, 0.01, 0.99), pr)
            kl = C.bernoulli_gain(pr, pn)["kl"]
            scen[f"shift_{dlt}"] = {
                "expected_delta_all_rows": float(-kl[ms].mean()),
                "expected_delta_questionable_rows": float(-kl[mq].mean()),
            }
        e_all = scen["shift_0.1"]["expected_delta_all_rows"]
        e_q = scen["shift_0.1"]["expected_delta_questionable_rows"]
        per[str(s)] = {
            "n": int(ms.sum()),
            "games": int(cg["clusters"]),
            "questionable_rows": int(mq.sum()),
            "q_streak_ge1": int((mq & (qs >= 1)).sum()),
            "q_streak_ge3": int((mq & (qs >= 3)).sum()),
            "q_change_upgraded": int((ms & (qc < 0)).sum()),
            "q_change_downgraded": int((ms & (qc > 0)).sum()),
            "q_change_on_questionable_rows_nonzero": int((mq & (qc != 0)).sum()),
            "primary_all_rows": C.sizing_row(
                "all R rows, floor -0.005",
                cg["sd_row"],
                max(1.0, cg["deff"]),
                ms.sum(),
                FLOOR,
                e_all,
            ),
            "slice_questionable": C.sizing_row(
                "questionable rows, floor -0.005",
                cq["sd_row"],
                max(1.0, cq["deff"]),
                mq.sum(),
                FLOOR,
                e_q,
            ),
            "scenarios": scen,
        }
    out["sizing"] = per
    gate: list[dict[str, Any]] = []
    for s in C.SEASONS:
        p = per[str(s)]["primary_all_rows"]
        gate.append(
            {
                "check": f"{s} all rows: half-width <= 0.005",
                "value": p["halfwidth95"],
                "pass": p["hw_le_floor"],
            }
        )
        gate.append(
            {
                "check": f"{s} all rows: power >= 0.5 at the 0.10-shift scenario",
                "value": p["power_one_season"],
                "pass": p["power_one_season"] >= 0.5,
            }
        )
        gate.append(
            {
                "check": f"{s} q_streak >= 1 on >= 300 questionable rows",
                "value": per[str(s)]["q_streak_ge1"],
                "pass": per[str(s)]["q_streak_ge1"] >= 300,
            }
        )
    out["gate"] = gate
    out["gate_pass"] = all(x["pass"] for x in gate)
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
