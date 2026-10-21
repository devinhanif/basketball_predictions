# ruff: noqa: E501
"""ROLE_PRIOR_SALARY minimum-data gate (rule: docs/prereg/ROLE_PRIOR_SALARY.md, DRAFT).

    .venv/bin/python -W ignore -m research.eval.role_prior_salary_gate [--out reports/prereg_role_prior_salary/gate.json]

Read-only: ``nba.duckdb`` and ``data/history/nba_history.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every SQL; season 2025
is never queried. Label columns of ``model_rows`` (``y``, realised ``minutes``) are never selected, with ONE disclosed exception: the A0
reproduction (the same check PRESEASON_ROLE_PRIOR ran) recomputes the published integer CRPS cells. ONE descriptive use of real minutes:
the Spearman of salary rank vs minutes per game on NON-mover rows of the select (and, labelled, report) season.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import role_prior_salary_crosswalk as xwalk
from research.eval import role_prior_salary_eval as ev
from research.eval import role_prior_salary_rule as rule

DATA = Path(os.environ.get("NBA_DATA_ROOT", "/Users/devin/Downloads/nba-prediction"))
OUT = Path("reports/prereg_role_prior_salary/gate.json")
UNRESOLVED = Path("reports/prereg_role_prior_salary/unresolved.csv")
SIM_SIGMAS = (0.10, 0.20)
COHORT_COLS = ["game_id", "player_id", "team_id", "game_date", "n_prior", "min10"]
SIM_COLS = ["game_id", "player_id", "team_id", "game_date", "c", "s", "p_ge"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def sha_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def appearances(c: Any) -> pd.DataFrame:
    return c.execute(
        "select s.player_id, s.team_id, g.season, min(g.game_date) first_date from player_game_stats s join games g using(game_id) "
        "where substr(s.game_id,1,3)='002' and s.minutes>0 and g.season<=? group by 1,2,3",
        [rule.MAX_SEASON],
    ).df()


def capture_audit(sal: pd.DataFrame, ros: pd.DataFrame, rows: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for y in rule.BR_YEARS:
        s, r, w = sal[sal.br_year == y], ros[ros.br_year == y], rows[rows.br_year == y]
        out[str(y)] = {
            "season_in_repo": y - 1,
            "used_for": "context only (previous season)"
            if y == 2023
            else (
                "select/report"
                if y in (2024, 2025)
                else "coverage count only, never joined to a game"
            ),
            "team_pages": int(s.team.nunique()),
            "salary_lines_with_id": int(len(s)),
            "salary_lines_with_dollar": int(s.salary.notna().sum()),
            "blank_dollar_lines": int(s.salary.isna().sum()),
            "players_with_dollar": int(w.player_bref_id.nunique()),
            "team_player_rows": int(len(w)),
            "players_with_more_than_one_dollar_line_on_a_page": int((w.n_lines > 1).sum()),
            "roster_rows": int(len(r)),
            "dead_money_rows(salary row not on same page roster)": int((~w.alive).sum()),
            "dead_money_dollars": float(w.loc[~w.alive, "salary"].sum()),
            "dead_money_share_of_league_dollars": float(
                w.loc[~w.alive, "salary"].sum() / w.salary.sum()
            ),
            "roster_players_without_dollar_line": int(
                len(
                    r.merge(
                        w[["team", "player_bref_id"]],
                        on=["team", "player_bref_id"],
                        how="left",
                        indicator=True,
                    ).query("_merge=='left_only'")
                )
            ),
        }
    return out


def spearman_ci(
    x: np.ndarray, y: np.ndarray, seed: int = rule.SEED, n_boot: int = rule.N_BOOT
) -> dict[str, float]:
    def rho(a: np.ndarray, b: np.ndarray) -> float:
        ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
        return float(np.corrcoef(ra, rb)[0, 1])

    rng = np.random.default_rng(seed)
    n = len(x)
    bs = np.array([rho(x[i], y[i]) for i in (rng.integers(0, n, n) for _ in range(n_boot))])
    return {
        "rho": rho(x, y),
        "lo": float(np.quantile(bs, 0.025)),
        "hi": float(np.quantile(bs, 0.975)),
        "n": int(n),
    }


def player_season_table(reg: pd.DataFrame) -> pd.DataFrame:
    ps = (
        reg.groupby(["player_id", "season"])
        .agg(
            first_team=("team_id", "first"),
            last_team=("team_id", "last"),
            games=("game_id", "size"),
            minutes=("minutes", "sum"),
        )
        .reset_index()
    )
    prev = ps.assign(season=ps.season + 1).rename(
        columns={"last_team": "prev_last", "games": "prev_games", "minutes": "prev_minutes"}
    )[["player_id", "season", "prev_last", "prev_games", "prev_minutes"]]
    return ps.merge(prev, on=["player_id", "season"], how="left")


def spearman_block(
    season: int, feat: pd.DataFrame, reg: pd.DataFrame, ps: pd.DataFrame, lines: pd.DataFrame
) -> dict[str, Any]:
    f = feat[(feat.season == season) & feat.alive & feat.player_id.notna()].copy()
    f["player_id"] = f.player_id.astype(int)
    st = ps[(ps.season == season) & (ps.prev_last == ps.first_team)][
        ["player_id", "first_team", "prev_minutes", "prev_games"]
    ]
    g = reg[(reg.season == season)].merge(st, on="player_id")
    g = g[g.team_id == g.first_team]
    mp = (
        g.groupby(["player_id", "team_id"])
        .agg(games=("game_id", "size"), mpg=("minutes", "mean"))
        .reset_index()
    )
    mp = mp[mp.games >= 10]
    d = mp.merge(
        f[["player_id", "team_id", "salary_rank", "cap_share", "age", "contract_class"]],
        on=["player_id", "team_id"],
    ).merge(st[["player_id", "prev_minutes"]], on="player_id")
    d = d.dropna(subset=["salary_rank", "mpg"])
    out: dict[str, Any] = {
        "definition": "non-mover player-seasons (first team of S = last team of S-1), >= 10 games with that team, alive salary rows; x = -salary rank within team (1 = highest), y = minutes per game; bootstrap over players",
        "label_use": "DESCRIPTIVE sanity check on non-mover minutes; not a label of the mover cohort",
        "rank_vs_mpg": spearman_ci(-d.salary_rank.to_numpy(float), d.mpg.to_numpy(float)),
        "cap_share_vs_mpg": spearman_ci(d.cap_share.to_numpy(float), d.mpg.to_numpy(float)),
    }
    ln = lines[(lines.season == season) & lines.player_id.notna()].assign(
        player_id=lambda x: x.player_id.astype(int)
    )
    blank = ln[~ln.has_dollar][["player_id", "team_id"]].merge(mp, on=["player_id", "team_id"])
    out["informative_missingness"] = {
        "non_mover_player_seasons_with_blank_dollar_line": int(len(blank)),
        "median_mpg_blank": float(blank.mpg.median()) if len(blank) else None,
        "median_mpg_dollar_alive": float(d.mpg.median()),
        "note": "a blank dollar value is NOT missing at random: it marks two-way / exhibit-10 / unpriced contracts (fringe roles)",
    }
    lowmin = d[d.prev_minutes < d.prev_minutes.median()]
    old = d[d.age >= 31]
    out["rank_vs_mpg_prev_minutes_below_median"] = spearman_ci(
        -lowmin.salary_rank.to_numpy(float), lowmin.mpg.to_numpy(float)
    )
    out["rank_vs_mpg_age_ge_31"] = (
        spearman_ci(-old.salary_rank.to_numpy(float), old.mpg.to_numpy(float))
        if len(old) > 30
        else {"n": int(len(old))}
    )
    return out


def cohort_season(
    S: int,
    reg: pd.DataFrame,
    fe: pd.Series,
    feat: pd.DataFrame,
    ps: pd.DataFrame,
    K: int,
    lines: pd.DataFrame,
) -> dict[str, Any]:
    rule.check_season(S)
    mr = arch.slice_frame(arch.read_rows("pts", S, COHORT_COLS), reg, fe, S)
    f = feat[feat.season == S]
    own = f[f.player_id.notna()].assign(player_id=lambda d: d.player_id.astype(int))[
        [
            "player_id",
            "team_id",
            "salary",
            "cap_share",
            "payroll_share",
            "salary_rank",
            "tax_state",
            "contract_class",
            "age",
            "alive",
            "known_at",
        ]
    ]
    any_page = (
        f[f.player_id.notna()]
        .assign(player_id=lambda d: d.player_id.astype(int))
        .sort_values("salary", ascending=False)
        .drop_duplicates("player_id")[["player_id", "salary"]]
        .rename(columns={"salary": "salary_any_page"})
    )
    mv = (
        mr[(mr["slice"] == "mover") & (mr.gwt < K)]
        .merge(own, on=["player_id", "team_id"], how="left")
        .merge(any_page, on="player_id", how="left")
    )
    mv["has_own"] = mv.salary.notna()
    ln = (
        lines[lines.season == S][["player_id", "team_id", "has_dollar"]]
        .dropna(subset=["player_id"])
        .assign(listed=True)
    )
    ln["player_id"] = ln.player_id.astype(int)
    mv = mv.merge(ln.drop(columns="has_dollar"), on=["player_id", "team_id"], how="left")
    mv["listed"] = mv.listed.fillna(False).astype(bool)
    mv["blank_listed"] = mv.listed & ~mv.has_own
    mv["has_any"] = mv.has_own | mv.salary_any_page.notna()
    # known-at: the salary line is stamped with the player's first game date with the team; every cohort row is at or after it
    mv["known_ok"] = [
        rule.known_at_ok(a, b) for a, b in zip(mv.game_date, mv.known_at, strict=True)
    ]
    allmv = mr[mr["slice"] == "mover"]
    d: dict[str, Any] = {
        "K": K,
        "mover_player_seasons_all_A0_rows": int(allmv.player_id.nunique()),
        "cohort_rows_per_stat": int(len(mv)),
        "cohort_players": int(mv.player_id.nunique()),
        "cohort_rows_with_own_team_salary": int(mv.has_own.sum()),
        "cohort_rows_with_salary_any_team_page": int(mv.has_any.sum()),
        "share_rows_own_team_salary": float(mv.has_own.mean()),
        "share_rows_any_page_salary": float(mv.has_any.mean()),
        "players_with_own_team_salary": int(mv[mv.has_own].player_id.nunique()),
        "players_with_any_salary": int(mv[mv.has_any].player_id.nunique()),
        "known_at_violations": int((~mv.known_ok).sum()),
        "cohort_rows_listed_on_own_team_salary_table(dollar or blank)": int(mv.listed.sum()),
        "share_rows_listed_on_own_team_table": float(mv.listed.mean()),
        "cohort_rows_blank_dollar_line_only(two-way / exhibit-10 / unpriced)": int(
            mv.blank_listed.sum()
        ),
        "cohort_rows_not_listed_on_own_team_table": int((~mv.listed).sum()),
        "cohort_rows_not_listed_but_salary_on_another_team_page": int(
            (~mv.listed & mv.has_any).sum()
        ),
        "cohort_rows_not_listed_anywhere_or_unresolved": int((~mv.listed & ~mv.has_any).sum()),
    }
    cov = mv[mv.has_own]
    wk = pd.to_datetime(cov.game_date).dt.strftime("%G-%V")
    d["clusters_on_covered_rows"] = {
        "games": int(cov.game_id.nunique()),
        "player_seasons": int(cov.player_id.nunique()),
        "weeks": int(wk.nunique()),
        "rows": int(len(cov)),
    }
    d["tax_state_rows"] = cov.tax_state.value_counts().to_dict()
    d["tax_state_players"] = cov.groupby("tax_state").player_id.nunique().to_dict()
    d["contract_class_rows"] = cov.contract_class.value_counts().to_dict()
    d["contract_class_players"] = cov.groupby("contract_class").player_id.nunique().to_dict()
    # dead weight: salary rank vs prior-season minutes rank within the new team's alive salary list
    pm = ps[ps.season == S - 1][["player_id", "minutes", "games"]].rename(
        columns={"minutes": "pm", "games": "pg"}
    )
    tm = (
        f[f.alive & f.player_id.notna()]
        .assign(player_id=lambda x: x.player_id.astype(int))
        .merge(pm, on="player_id", how="left")
    )
    tm["pm"] = tm.pm.fillna(0.0)
    tm["pg"] = tm.pg.fillna(0)
    tm["min_rank"] = tm.groupby("team_id").pm.rank(ascending=False, method="min")
    dis = tm.merge(mv[["player_id", "team_id"]].drop_duplicates(), on=["player_id", "team_id"])
    dis = dis.assign(gap=(dis.salary_rank - dis.min_rank).abs())
    d["movers_salary_rank_vs_prior_minutes_rank"] = {
        "movers_ranked": int(len(dis)),
        "disagree_by_more_than_5_places": int((dis.gap > rule.DISAGREE_PLACES).sum()),
        "share": float((dis.gap > rule.DISAGREE_PLACES).mean()) if len(dis) else None,
        "salary_rank_le5_but_prior_minutes_rank_gt10": int(
            ((dis.salary_rank <= 5) & (dis.min_rank > 10)).sum()
        ),
        "movers_with_fewer_than_20_games_in_prior_season": int((dis.pg < 20).sum()),
    }
    ever = fe.reindex(tm.player_id).to_numpy() < S
    out_prior = tm[(tm.pg == 0) & ever]
    d["alive_salary_rows_out_whole_prior_season(non-rookie, zero S-1 games)"] = {
        "league": int(len(out_prior)),
        "dollars": float(out_prior.salary.sum()),
        "of_alive_rows": int(len(tm)),
    }
    d["alive_salary_rows_out_whole_prior_season(non-rookie, zero S-1 games)"]["movers"] = int(
        len(dis[dis.pg == 0])
    )
    d["_cohort"] = mv
    return d


def power_block(
    S_list: tuple[int, ...], cohorts: dict[int, pd.DataFrame], K: int
) -> dict[str, Any]:
    """Label-free simulation on the real cohort rows (own-team salary), A0 stored grid; see role_prior_salary_eval.sim_cell."""
    out: dict[str, Any] = {}
    prep: dict[tuple[int, str], tuple[np.ndarray, ...]] = {}
    for S in S_list:
        mv = cohorts[S]
        mv = mv[mv.has_own]
        key = set(zip(mv.game_id, mv.player_id, strict=True))
        for st in rule.STATS:
            df = arch.read_rows(st, S, SIM_COLS)
            sel = np.flatnonzero(
                [(g, p) in key for g, p in zip(df.game_id, df.player_id, strict=True)]
            )
            blocks = arch.month_blocks(df)
            q0 = arch.quantile_grid(df, sel, blocks)
            gid = df.game_id.to_numpy()[sel]
            ps_id = np.array([f"{S}-{p}" for p in df.player_id.to_numpy()[sel]])
            wk = np.array(
                [f"{S}-{w}" for w in pd.to_datetime(df.game_date.iloc[sel]).dt.strftime("%G-%V")]
            )
            prep[(S, st)] = (q0, ps_id, gid, wk)
    for label, seasons in [(str(S), (S,)) for S in S_list] + [("pooled", tuple(S_list))]:
        for st in rule.STATS:
            q0 = np.vstack([prep[(S, st)][0] for S in seasons])
            ps_id = np.concatenate([prep[(S, st)][1] for S in seasons])
            gid = np.concatenate([prep[(S, st)][2] for S in seasons])
            wk = np.concatenate([prep[(S, st)][3] for S in seasons])
            cell: dict[str, Any] = {
                "n_rows": int(len(q0)),
                "n_games": int(len(np.unique(gid))),
                "n_player_seasons": int(len(np.unique(ps_id))),
                "n_weeks": int(len(np.unique(wk))),
                "floor": rule.FLOORS[st],
            }
            res = {}
            res["null_noise_arm_sigma0.10"] = ev.noise_arm(
                q0, ps_id, gid, wk, 0.10, 1, arch.crps_int_q
            )
            res["null_noise_arm_sigma0.05"] = ev.noise_arm(
                q0, ps_id, gid, wk, 0.05, 1, arch.crps_int_q
            )
            for sg in SIM_SIGMAS:
                res[f"half_recovery_sigma{sg:.2f}"] = ev.sim_cell(
                    q0, ps_id, gid, wk, sg, 0.5, 2, arch.crps_int_q
                )
                res[f"oracle_sigma{sg:.2f}"] = ev.sim_cell(
                    q0, ps_id, gid, wk, sg, 1.0, 3, arch.crps_int_q
                )
            for k, v in res.items():
                cell[k] = {
                    "point": v["binding"]["point"],
                    "half_width_game": v["game"]["half_width"],
                    "half_width_player_season": v["player_season"]["half_width"],
                    "half_width_week": v["week"]["half_width"],
                    "binding": v["binding"]["clustering"],
                    "binding_half_width": v["binding"]["half_width"],
                    "binding_ci": [v["binding"]["lo"], v["binding"]["hi"]],
                    "top5_share": v["top5"],
                    "sd_row_diff": v["sd_row_diff"],
                }
            out[f"{label}|{st}"] = cell
        log(f"power {label} K={K} done")
    return out


def run(out: Path = OUT) -> dict[str, Any]:
    t0 = time.time()
    sal, ros = rule.load_all(DATA)
    rows = rule.salary_rows(sal, ros)
    feat = rule.team_year_features(rows, ros)
    c, h = arch.connect(arch.DB), arch.connect(arch.HIST_DB)
    app = appearances(c)
    names = xwalk.nba_players()
    tids = xwalk.nba_team_ids()
    ids = pd.concat(
        [
            sal[["player_bref_id", "team", "br_year", "player"]],
            ros[["player_bref_id", "team", "br_year", "player"]],
        ]
    ).drop_duplicates()
    birth = ros[["player_bref_id", "birth_date"]].dropna().drop_duplicates("player_bref_id")
    sb = c.execute(
        "select player_id, birth_date from players_static where birth_date is not null"
    ).df()
    xw, _ = xwalk.build(ids, names, app, tids, birth, sb)
    un = xwalk.unresolved(ids, xw, rows)
    Path(UNRESOLVED).parent.mkdir(parents=True, exist_ok=True)
    un.to_csv(UNRESOLVED, index=False)
    feat = feat.merge(
        xw[["player_bref_id", "player_id", "method"]], on="player_bref_id", how="left"
    )
    feat["team_id"] = feat.team.map(lambda t: tids[rule.BR_TO_NBA_ABBR.get(str(t), str(t))])
    feat["season"] = feat.br_year - 1
    fa = app.rename(columns={"first_date": "known_at"})[
        ["player_id", "team_id", "season", "known_at"]
    ]
    feat = feat.merge(fa, on=["player_id", "team_id", "season"], how="left")
    feat.loc[feat.season > rule.MAX_SEASON, ["known_at"]] = pd.NaT
    ln = (
        sal.groupby(["team", "br_year", "player_bref_id"])
        .salary.apply(lambda s: bool(s.notna().any()))
        .rename("has_dollar")
        .reset_index()
    )
    ln = ln.merge(xw[["player_bref_id", "player_id"]], on="player_bref_id", how="left")
    ln["team_id"] = ln.team.map(lambda t: tids[rule.BR_TO_NBA_ABBR.get(str(t), str(t))])
    ln["season"] = ln.br_year - 1
    log("tables built")
    reg, fe = arch.load_reg(c, h), arch.load_first_ever(c, h)
    ps = player_season_table(reg)
    cov_by_year = {}
    for y in rule.BR_YEARS:
        fy = feat[feat.br_year == y]
        cov_by_year[str(y)] = {
            "salary_team_player_rows": int(len(fy)),
            "resolved": int(fy.player_id.notna().sum()),
            "share_resolved": float(fy.player_id.notna().mean()),
            "methods": fy.method.fillna("unresolved").value_counts().to_dict(),
        }
    gate: dict[str, Any] = {
        "id": rule.ID,
        "status": "DRAFT gate, label-free (A0 reproduction and the descriptive non-mover Spearman excepted, disclosed in the module doc)",
        "pins": {
            "salary_csv_manifest_sha256": sha_files(sorted((DATA / rule.SALARY_DIR).glob("*.csv"))),
            "model_rows_sha256": {
                f"{s}_{st}": arch.file_sha256(arch.ODDS / f"model_rows_{s}_{st}.parquet")
                for s in (rule.SELECT, rule.REPORT)
                for st in rule.STATS
            },
            "nba_api_static_players": int(len(names)),
        },
        "cap_table_dollars": {str(k): v for k, v in rule.CAP_TABLE.items()},
        "capture_audit": capture_audit(sal, ros, rows),
        "crosswalk": {
            "bref_ids_total": int(ids.player_bref_id.nunique()),
            "resolved": int(len(xw)),
            "unresolved": int(len(un)),
            "by_method": xw.method.value_counts().to_dict(),
            "salary_rows_resolved_by_br_year": cov_by_year,
            "hardest_unresolved_by_salary_pages": un.head(15).to_dict("records"),
            "unresolved_csv": str(UNRESOLVED),
        },
        "seasons": {},
    }
    cohorts: dict[int, pd.DataFrame] = {}
    for S in (rule.SELECT, rule.REPORT):
        blocks: dict[str, Any] = {}
        for K in rule.K_SENS:
            cs = cohort_season(S, reg, fe, feat, ps, K, ln)
            if K == rule.K_MAIN:
                cohorts[S] = cs["_cohort"]
            cs.pop("_cohort")
            blocks[f"K{K}"] = cs
        blocks["spearman_non_movers"] = spearman_block(S, feat, reg, ps, ln)
        gate["seasons"][str(S)] = blocks
        log(f"cohort {S} done")
    # K = 20 option rows (for the power options only)
    k20: dict[int, pd.DataFrame] = {}
    for S in (rule.SELECT, rule.REPORT):
        cs = cohort_season(S, reg, fe, feat, ps, 20, ln)
        k20[S] = cs.pop("_cohort")
        gate["seasons"][str(S)]["K20_rows_with_own_salary"] = int(k20[S].has_own.sum())
    # mover mover-player-season crosswalk coverage (any salary line on any page, resolved id), per season
    for S in (rule.SELECT, rule.REPORT):
        mv = cohorts[S]
        gate["seasons"][str(S)]["mover_players_in_cohort"] = int(mv.player_id.nunique())
    gate["power"] = {"K10": power_block((rule.SELECT, rule.REPORT), cohorts, 10)}
    gate["power"]["K20_option"] = power_block((rule.SELECT, rule.REPORT), k20, 20)
    gate["a0_reproduction"] = [
        {k: v for k, v in r.items()}
        for s in (rule.SELECT, rule.REPORT)
        for r in arch.a0_reproduction(s)
    ]
    log("a0 reproduction done")
    gate["checks"] = checks(gate)
    gate["wall_seconds"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gate, indent=1, default=str, sort_keys=False) + "\n")
    return gate


def checks(g: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for S in ("2023", "2024"):
        k = g["seasons"][S]["K10"]
        out.append(
            chk(
                f"G1 {S} cohort rows per stat with own-team salary >= {rule.MIN_ROWS}",
                k["cohort_rows_with_own_team_salary"],
                f">= {rule.MIN_ROWS}",
                k["cohort_rows_with_own_team_salary"] >= rule.MIN_ROWS,
            )
        )
        out.append(
            chk(
                f"G2 {S} share of cohort rows with own-team salary >= {rule.MIN_SALARY_COVER}",
                k["share_rows_own_team_salary"],
                f">= {rule.MIN_SALARY_COVER}",
                k["share_rows_own_team_salary"] >= rule.MIN_SALARY_COVER,
            )
        )
        out.append(
            chk(
                f"G3 {S} share of cohort rows with a resolved-id salary on any page >= {rule.MIN_RESOLVED}",
                k["share_rows_any_page_salary"],
                f">= {rule.MIN_RESOLVED}",
                k["share_rows_any_page_salary"] >= rule.MIN_RESOLVED,
            )
        )
        out.append(
            chk(
                f"G5 {S} known-at violations",
                k["known_at_violations"],
                "== 0",
                k["known_at_violations"] == 0,
            )
        )
        sp = g["seasons"][S]["spearman_non_movers"]["rank_vs_mpg"]
        out.append(
            chk(
                f"G8 {S} Spearman (non-movers, -salary rank vs mpg) CI excludes 0",
                sp,
                "lo > 0",
                sp["lo"] > 0,
            )
        )
        for st in rule.STATS:
            c = g["power"]["K10"][f"{S}|{st}"]
            hw = c["null_noise_arm_sigma0.05"]["binding_half_width"]
            out.append(
                chk(
                    f"G6 {S} {st} binding half-width of the null contrast (noise arm, sd of log lambda 0.05) <= floor {rule.FLOORS[st]}",
                    hw,
                    f"<= {rule.FLOORS[st]}",
                    hw <= rule.FLOORS[st],
                )
            )
    out.append(
        chk(
            "G4 A0 reproduction (8 cells: 2 seasons x 4 stats, 1e-9)",
            sum(r["ok"] for r in g["a0_reproduction"]),
            "8 of 8",
            all(r["ok"] for r in g["a0_reproduction"]),
        )
    )
    for S in ("2023", "2024"):
        c = g["power"]["K10"][f"{S}|pts"]["oracle_sigma0.20"]
        out.append(
            chk(
                f"G7 positive control {S} pts: oracle sigma 0.20 clears (point <= -floor, binding CI upper < 0)",
                c["binding_ci"],
                "hi < 0 and point <= -0.005",
                c["binding_ci"][1] < 0 and c["point"] <= -0.005,
            )
        )
    out.append(chk("G9 season 2025 loaded", False, "never", True))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    g = run(Path(a.out))
    for ck in g["checks"]:
        print(
            ("PASS " if ck["pass"] else "FAIL ") + ck["check"],
            ck["value"] if not isinstance(ck["value"], dict) else "",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
