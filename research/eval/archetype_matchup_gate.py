# ruff: noqa: E501
"""ARCHETYPE_MATCHUP minimum-data gate (rule: docs/prereg/ARCHETYPE_MATCHUP.md, DRAFT). Label-free.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.archetype_matchup_gate [--out reports/prereg_archetype_matchup/gate.json]

Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every SQL; season 2025 is never queried. The stored A0 files
(``reports/odds_history/model_rows_{season}_{stat}.parquet``) are read for ``game_id, player_id, team_id, game_date, min10, n_prior``
(pre-game covariates) and, for the power simulation, the stored forecast ``c, s, p_ge`` (the served distribution, never an outcome). The
label columns ``y`` and realised ``minutes`` are NEVER selected. The power simulation draws outcomes from A0's own stored grid
(role_prior_salary_eval.sim_cell / noise_arm, reused unchanged).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_rule as rule
from research.eval import role_prior_salary_eval as ev

OUT = Path("reports/prereg_archetype_matchup/gate.json")
ROW_COLS = ["game_id", "player_id", "team_id", "game_date", "n_prior", "min10"]
GRID_COLS = ["game_id", "player_id", "team_id", "game_date", "c", "s", "p_ge"]
LABEL_COLS = {"y", "minutes", "mean_prod", "m_recency"}
SIM_SIGMAS = (0.10, 0.20)
AUDIT_COLS = ("off_arch", "e_usual", "delta_rate", "shift_l1", "retained", "def_unknown_share")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def weeks(df: pd.DataFrame, season: int) -> np.ndarray:
    return np.array([f"{season}-{w}" for w in pd.to_datetime(df.game_date).dt.strftime("%G-%V")])


def cluster_counts(df: pd.DataFrame, season: int) -> dict[str, int]:
    return {
        "rows": int(len(df)),
        "player_seasons": int(df.player_id.nunique()),
        "games": int(df.game_id.nunique()),
        "weeks": int(len(np.unique(weeks(df, season)))) if len(df) else 0,
    }


def power_cell(stat: str, season: int, sl: pd.DataFrame, sims: bool) -> dict[str, Any]:
    """Noise-arm (and, if ``sims``, planted-effect) simulation on the test's rows of A0's stored grid (ROLE_PRIOR_SALARY power method)."""
    df = arch.read_rows(stat, season, GRID_COLS)
    key = set(zip(sl.game_id, sl.player_id, strict=True))
    sel = np.flatnonzero([(g, p) in key for g, p in zip(df.game_id, df.player_id, strict=True)])
    blocks = arch.month_blocks(df)
    q0 = arch.quantile_grid(df, sel, blocks)
    sub = df.iloc[sel]
    ps = np.array([f"{season}-{p}" for p in sub.player_id.to_numpy()])
    gid = sub.game_id.to_numpy()
    wk = weeks(sub, season)
    res = {
        f"noise_sigma{rule.NOISE_SIGMA:.2f}": ev.noise_arm(
            q0, ps, gid, wk, rule.NOISE_SIGMA, 1, arch.crps_int_q
        )
    }
    if sims:
        for sg in SIM_SIGMAS:
            res[f"half_recovery_sigma{sg:.2f}"] = ev.sim_cell(
                q0, ps, gid, wk, sg, 0.5, 2, arch.crps_int_q
            )
            res[f"oracle_sigma{sg:.2f}"] = ev.sim_cell(q0, ps, gid, wk, sg, 1.0, 3, arch.crps_int_q)
    cell: dict[str, Any] = {"n_rows": int(len(q0)), "floor": rule.FLOORS[stat]}
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
        }
    nz = cell[f"noise_sigma{rule.NOISE_SIGMA:.2f}"]
    cell["detectable_effect"] = nz["binding_half_width"]
    cell["mde80_descriptive"] = nz["binding_half_width"] * rule.MDE80_FACTOR
    return cell


def build_season(
    S: int, mu: pd.DataFrame, games: pd.DataFrame, flagged: dict[str, set[int]], c: Any
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    rule.check_season(S)
    rows = arch.read_rows("pts", S, ROW_COLS)
    assert not (set(rows.columns) & LABEL_COLS)
    n_stored = int(len(rows))
    rows = rows[rule.is_regular(rows.game_id)].reset_index(drop=True)
    same = {}
    for st in ("ast", "fg3m"):
        o = arch.read_rows(st, S, ["game_id", "player_id"])
        o = o[rule.is_regular(o.game_id)]
        same[st] = bool(
            set(zip(o.game_id, o.player_id, strict=True))
            == set(zip(rows.game_id, rows.player_id, strict=True))
        )
    off_a, def_a = feat.build_archetypes(c, S)
    tables = feat.prior_tables(mu, off_a, def_a, S - 1)
    mf = feat.mix_frame(rows, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a)
    sf = pd.concat([rows.reset_index(drop=True), mf], axis=1)
    sf["bucket"] = [rule.minutes_bucket(m) for m in sf.min10.fillna(0.0)]
    meta = {
        "rows_pts_stored_all_game_types": n_stored,
        "rows_pts": int(len(sf)),
        "games": int(sf.game_id.nunique()),
        "row_sets_identical_across_stats": same,
        "archetype_sizes": {
            "off": {int(k): int(v) for k, v in off_a.value_counts().sort_index().items()},
            "def": {int(k): int(v) for k, v in def_a.value_counts().sort_index().items()},
            "players_clustered": int(len(off_a)),
        },
        "lg_rate_prior_season": tables["lg"],
        "share_rows_no_roster": float(sf.no_roster.mean()),
        "share_rows_game_has_usable_report": float(sf.has_report.mean()),
        "n_roster_mean": float(sf.n_roster.mean()),
        "n_out_mean_when_report": float(sf[sf.has_report].n_out.mean()),
        "share_rows_any_opposing_out": float((sf.n_out > 0).mean()),
        "shift_l1_quantiles": {
            str(q): float(sf.shift_l1.quantile(q)) for q in (0.5, 0.75, 0.9, 0.95, 0.99)
        },
        "share_shift_zero": float((sf.shift_l1 == 0).mean()),
        "delta_rate_abs_mean": float(sf.delta_rate.abs().mean()),
        "off_arch_unknown_share": float((sf.off_arch == rule.UNK_OFF).mean()),
    }
    return sf, meta, tables


def missingness(sf: pd.DataFrame) -> dict[str, Any]:
    """Neutral-or-missing share of every new column by min10 bucket (the 10-08 leak signature is a NULL pattern that tracks minutes)."""
    neutral = {
        "off_arch": sf.off_arch == rule.UNK_OFF,
        "e_usual": sf.no_roster,
        "delta_rate": sf.delta_rate == 0,
        "shift_l1": sf.shift_l1 == 0,
        "retained": sf.retained == 1.0,
        "def_unknown_share": sf.def_unknown_share >= 0.5,
        "has_report_false": ~sf.has_report,
    }
    out: dict[str, Any] = {}
    for name, m in neutral.items():
        by = {
            b: float(m[sf.bucket == b].mean())
            for b in ("lt10", "10to20", "20to28", "ge28")
            if (sf.bucket == b).any()
        }
        gap = abs(by.get("lt10", np.nan) - by.get("ge28", np.nan))
        out[name] = {
            "neutral_share_by_min10_bucket": by,
            "gap_lt10_vs_ge28": float(gap),
            "flagged": bool(gap >= rule.MISSING_FLAG_GAP),
        }
    out["nan_cells_any_feature"] = int(sf[list(AUDIT_COLS)].isna().sum().sum())
    return out


def planted_future(
    sf: pd.DataFrame,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    S: int,
    tables: dict[str, Any],
    off_a: pd.Series,
    def_a: pd.Series,
) -> dict[str, Any]:
    """Scale every matchup event of season S on or after a cut day by a random factor (mm, poss, pts); the six features of every row
    strictly before the cut must not move."""
    day = feat.to_day(sf.game_date)
    cut = int(np.median(day))
    before = sf[day < cut]
    sub = before.iloc[:: max(1, len(before) // 1500)][ROW_COLS].reset_index(drop=True)
    pert = mu.copy()
    d = feat.to_day(pert.game_date)
    fut = (d >= cut) & (pert.season == S)
    rng = np.random.default_rng(rule.SEED)
    for col in ("mm", "poss", "pts"):
        v = pert[col].to_numpy(float).copy()
        v[fut.to_numpy()] = v[fut.to_numpy()] * rng.uniform(0.0, 5.0, int(fut.sum()))
        pert[col] = v
    a = feat.mix_frame(sub, mu, games, flagged, S, tables, off_a, def_a)
    b = feat.mix_frame(sub, pert, games, flagged, S, tables, off_a, def_a)
    same = bool(
        all(
            np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), rtol=0, atol=1e-9)
            for c in rule.FEATURES
        )
    )
    return {
        "queries": int(len(sub)),
        "cut_day": cut,
        "events_perturbed": int(fut.sum()),
        "identical": same,
    }


def run(out: Path = OUT) -> dict[str, Any]:
    t0 = time.time()
    c = arch.connect(arch.DB)
    mu = feat.read_matchups(c)
    games = feat.read_games(c)
    flagged = feat.read_flagged(c, games)
    log(f"loaded matchups {len(mu)} games {len(games)} flagged games {len(flagged)}")
    h = arch.connect(arch.HIST_DB)
    hist_rows = int(
        h.execute(
            "select count(*) from player_game_matchups m join games g using(game_id) where g.season <= 2021"
        ).fetchone()[0]
    )
    h.close()
    gate: dict[str, Any] = {
        "id": rule.ID,
        "status": "DRAFT gate, label-free (y and realised minutes never selected)",
        "pins": {
            f"{s}_{st}": arch.file_sha256(arch.ODDS / f"model_rows_{s}_{st}.parquet")
            for s in (rule.SELECT, rule.REPORT)
            for st in rule.STATS
        },
        "bars": {
            "MIN_ROWS": rule.MIN_ROWS,
            "MIN_PLAYER_SEASONS": rule.MIN_PLAYER_SEASONS,
            "MIN_GAMES": rule.MIN_GAMES,
            "MIN_WEEKS": rule.MIN_WEEKS,
            "detectable_effect_bar_pts": rule.DE_BAR["pts"],
            "noise_sigma": rule.NOISE_SIGMA,
            "T2_QUANTILE": rule.T2_QUANTILE,
            "SHIFT_FLOOR": rule.SHIFT_FLOOR,
        },
        "capture_audit": {
            "matchup_rows_by_season_nba_duckdb": {
                str(k): int(v) for k, v in mu.groupby("season").size().items()
            },
            "matchup_games_by_season": {
                str(k): int(v) for k, v in mu.groupby("season").game_id.nunique().items()
            },
            "matchup_rows_history_db_seasons_le_2021": hist_rows,
            "games_by_season_regular": {
                str(k): int(v) for k, v in games.groupby("season").size().items()
            },
            "report_games_flagged_by_season": {
                str(s): int(games[games.season == s].game_id.isin(list(flagged)).sum())
                for s in range(2022, 2025)
            },
        },
        "seasons": {},
        "missingness": {},
        "power": {},
    }
    frames: dict[int, pd.DataFrame] = {}
    for S in (rule.SELECT, rule.REPORT):
        sf, meta, tables = build_season(S, mu, games, flagged, c)
        off_a, def_a = feat.build_archetypes(c, S)
        meta["planted_future"] = planted_future(
            sf, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a
        )
        gate["seasons"][str(S)] = meta
        gate["missingness"][str(S)] = missingness(sf)
        frames[S] = sf
        log(
            f"season {S} features done: rows {len(sf)} shift q90 {meta['shift_l1_quantiles']['0.9']:.4f}"
        )
    cut = float(frames[rule.SELECT].shift_l1.quantile(rule.T2_QUANTILE))
    gate["t2_cut_from_select_season"] = {"quantile_cut": cut, "used": max(cut, rule.SHIFT_FLOOR)}
    for S in (rule.SELECT, rule.REPORT):
        sf = frames[S]
        m2 = rule.t2_mask(sf.shift_l1.to_numpy(), cut)
        gate["seasons"][str(S)]["T1"] = cluster_counts(sf, S)
        gate["seasons"][str(S)]["T2"] = cluster_counts(sf[m2], S)
        gate["seasons"][str(S)]["T2_complement"] = cluster_counts(sf[~m2], S)
        for name, sub in (("T1", sf), ("T2", sf[m2]), ("T2c", sf[~m2])):
            for st in rule.STATS:
                if name == "T2c" and st != "pts":
                    continue
                full = st == "pts" and name != "T2c"
                gate["power"][f"{S}|{name}|{st}"] = power_cell(st, S, sub, sims=full)
                log(f"power {S} {name} {st} done")
    g = gate
    g["cells"] = {}
    ok_t: dict[str, bool] = {}
    for tname in ("T1", "T2"):
        ok_all = True
        for S in (rule.SELECT, rule.REPORT):
            cc = dict(g["seasons"][str(S)][tname])
            cc["detectable_effect_pts"] = g["power"][f"{S}|{tname}|pts"]["detectable_effect"]
            chks = rule.gate_test_checks(cc)
            g["cells"][f"{S}|{tname}"] = {**cc, "checks": chks, "pass": all(chks.values())}
            ok_all = ok_all and all(chks.values())
        ok_t[tname] = ok_all
    g["checks"] = checks(g)
    g["verdict"] = rule.verdict_word(ok_t["T1"], ok_t["T2"])
    g["t1_only_means"] = (
        "T1 can bear a claim; T2 is reported with its CI but is descriptive and leaves the BH family (3 tests, not 6)"
    )
    g["wall_seconds"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(g, indent=1, default=str) + "\n")
    c.close()
    return g


def checks(g: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for S in ("2023", "2024"):
        pf = g["seasons"][S]["planted_future"]["identical"]
        out.append(
            chk(
                f"planted future {S}: features of rows before the cut unchanged when later events are scaled",
                pf,
                "True",
                pf,
            )
        )
        same = g["seasons"][S]["row_sets_identical_across_stats"]
        out.append(
            chk(f"row sets identical across pts/ast/fg3m {S}", same, "True", all(same.values()))
        )
        nan = g["missingness"][S]["nan_cells_any_feature"]
        out.append(chk(f"no NaN feature cell {S}", nan, "0", nan == 0))
        for tn in ("T1", "T2"):
            cell = g["cells"][f"{S}|{tn}"]
            for k, v in cell["checks"].items():
                val = cell["detectable_effect_pts"] if k == "detectable_effect" else cell[k]
                out.append(chk(f"{S} {tn} {k}", val, k, v))
        pc = g["power"][f"{S}|T1|pts"]["oracle_sigma0.20"]
        out.append(
            chk(
                f"positive control {S} T1 pts: oracle sigma 0.20 clears",
                pc["binding_ci"],
                "hi < 0 and point <= -0.005",
                pc["binding_ci"][1] < 0 and pc["point"] <= -0.005,
            )
        )
    out.append(chk("season 2025 loaded", False, "never", True))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    g = run(Path(a.out))
    for ck in g["checks"]:
        print(("PASS " if ck["pass"] else "FAIL ") + ck["check"], ck["value"])
    print("VERDICT", g["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
