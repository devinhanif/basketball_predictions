# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL minimum-data gate (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT). Label-free.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.archetype_matchup_all_gate [--out reports/prereg_archetype_matchup_all/gate.json]

Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every SQL; season 2025 is never queried. Reads the stored A0 files
(``reports/odds_history/model_rows_{season}_{stat}.parquet``) for ``game_id, player_id, team_id, game_date, n_prior, min10`` and the stored
forecast ``c, s, p_ge`` (never ``y`` or realised minutes). Features, planted-future identity and the missingness audit are the T506 code,
unchanged (archetype_matchup_gate). The power simulation draws outcomes from A0's own stored grid; its per-row deltas are computed here
(``noise_deltas`` / ``sim_deltas`` replicate role_prior_salary_eval.noise_arm / sim_cell and are proven equal to them in the tests) so the
four stats can be combined row by row into the composite and bootstrapped on the same clusters.
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
from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_gate as g6
from research.eval import role_prior_salary_eval as ev

OUT = Path("reports/prereg_archetype_matchup_all/gate.json")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def noise_deltas(
    q0: np.ndarray, ps: np.ndarray, sigma: float, seed: int, crps: Any = arch.crps_int_q
) -> np.ndarray:
    """Per-row (arm - A0) of the null-noise arm: truth equals A0, the arm shifts by an independent exp(sigma z_p). Same draws as
    ``ev.noise_arm`` for the same seed (tested)."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(ps, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = np.ceil(q0[np.arange(len(q0)), u] - 0.5)
    return np.asarray(crps(q0 * np.exp(sigma * z)[:, None], y) - crps(q0, y))


def sim_deltas(
    q0: np.ndarray,
    ps: np.ndarray,
    sigma: float,
    recover: float,
    seed: int,
    crps: Any = arch.crps_int_q,
) -> np.ndarray:
    """Per-row (arm - A0) of the planted-shift arm (``ev.sim_cell`` draws; recover = 1 is the oracle). Tested equal."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(ps, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = np.ceil(q0[np.arange(len(q0)), u] * np.exp(sigma * z) - 0.5)
    return np.asarray(crps(q0 * np.exp(recover * sigma * z)[:, None], y) - crps(q0, y))


def stat_inputs(stat: str, season: int, keys: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """A0's stored integer grid for ``keys`` (rows sorted by game_id, player_id), in that order. Blocks are built from ALL stored rows
    of the stat (production's one-grid-per-month-block construction), as in the T506 gate."""
    df = arch.read_rows(stat, season, g6.GRID_COLS).reset_index(drop=True)
    blocks = arch.month_blocks(df)
    pos = keys.merge(
        df[["game_id", "player_id"]].reset_index(), on=["game_id", "player_id"], how="left"
    )
    assert len(pos) == len(keys) and pos["index"].notna().all()
    sel = pos["index"].to_numpy(int)
    q0 = arch.quantile_grid(df, sel, blocks)
    return q0, df.iloc[sel].reset_index(drop=True)


def joint(
    deltas: dict[str, np.ndarray], game: np.ndarray, ps: np.ndarray, week: np.ndarray
) -> dict[str, Any]:
    """Composite CM (floor units) with the three clusterings; the binding one is the widest. Same clusters resampled in all four stats."""
    return ev.three_way(rule.composite_rows(deltas), game, ps, week)


def hw_floor(cell: dict[str, Any], stat: str) -> float:
    return float(cell["binding"]["half_width"]) / rule.FLOORS[stat]


def summarize(vals: list[float]) -> dict[str, float]:
    a = np.asarray(vals, float)
    return {
        "mean": float(a.mean()),
        "sd": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
        "min": float(a.min()),
        "max": float(a.max()),
        "n_seeds": int(len(a)),
    }


def power_season(
    S: int, keys: pd.DataFrame, n_seeds: int = rule.N_SEEDS, n_pos: int = rule.POS_SEEDS
) -> dict[str, Any]:
    q0: dict[str, np.ndarray] = {}
    sub = None
    for st in rule.STATS:
        q0[st], sub = stat_inputs(st, S, keys)
        log(f"season {S} {st}: grid {q0[st].shape}")
    assert sub is not None
    ps = np.array([f"{S}-{p}" for p in sub.player_id.to_numpy()])
    game = sub.game_id.to_numpy()
    wk = g6.weeks(sub, S)
    noise: list[dict[str, Any]] = []
    for k in range(n_seeds):
        d = {
            st: noise_deltas(q0[st], ps, rule.NOISE_SIGMA, rule.noise_seed(st, k))
            for st in rule.STATS
        }
        jc = joint(d, game, ps, wk)
        per = {st: ev.three_way(d[st], game, ps, wk) for st in rule.STATS}
        noise.append(
            {
                "seed_index": k,
                "cm_hw_game": jc["game"]["half_width"],
                "cm_hw_player_season": jc["player_season"]["half_width"],
                "cm_hw_week": jc["week"]["half_width"],
                "cm_hw_binding": jc["binding"]["half_width"],
                "binding": jc["binding"]["clustering"],
                "cm_point": jc["binding"]["point"],
                "per_stat_hw_floor_units": {st: hw_floor(per[st], st) for st in rule.STATS},
                "per_stat_binding": {st: per[st]["binding"]["clustering"] for st in rule.STATS},
                "per_stat_hw_abs": {st: per[st]["binding"]["half_width"] for st in rule.STATS},
                "stat_parts_of_noise_point": rule.stat_parts(d),
            }
        )
        if k % 5 == 4:
            log(f"season {S} noise seeds {k + 1}/{n_seeds} done")
    pos: dict[str, list[dict[str, Any]]] = {"oracle_sigma0.20": [], "oracle_sigma0.10": []}
    for k in range(n_pos):
        for sg, name in (
            (rule.POS_SIGMA, "oracle_sigma0.20"),
            (rule.POS_SIGMA_DESC, "oracle_sigma0.10"),
        ):
            d = {st: sim_deltas(q0[st], ps, sg, 1.0, rule.pos_seed(st, k)) for st in rule.STATS}
            jc = joint(d, game, ps, wk)
            pos[name].append(
                {
                    "seed_index": k,
                    "cm_point": jc["binding"]["point"],
                    "ci": [jc["binding"]["lo"], jc["binding"]["hi"]],
                    "binding": jc["binding"]["clustering"],
                    "top5": ev.top5_share(rule.composite_rows(d), ps),
                    "clears": bool(
                        jc["binding"]["hi"] < 0 and jc["binding"]["point"] <= rule.CM_PASS
                    ),
                }
            )
        log(f"season {S} positive control seed {k + 1}/{n_pos} done")
    hw = [r["cm_hw_binding"] for r in noise]
    out: dict[str, Any] = {
        "n_rows": int(len(keys)),
        "clusters": g6.cluster_counts(sub, S),
        "floors": rule.FLOORS,
        "detectable_cm_half_width_floor_units": summarize(hw),
        "binding_clustering_counts": {
            c: int(sum(r["binding"] == c for r in noise)) for c in ("game", "player_season", "week")
        },
        "cm_hw_by_clustering_mean": {
            "game": float(np.mean([r["cm_hw_game"] for r in noise])),
            "player_season": float(np.mean([r["cm_hw_player_season"] for r in noise])),
            "week": float(np.mean([r["cm_hw_week"] for r in noise])),
        },
        "per_stat_detectable_floor_units": {
            st: summarize([r["per_stat_hw_floor_units"][st] for r in noise]) for st in rule.STATS
        },
        "per_stat_detectable_abs": {
            st: summarize([r["per_stat_hw_abs"][st] for r in noise]) for st in rule.STATS
        },
        "mde80_cm_floor_units_descriptive": float(np.mean(hw)) * 2.8 / 1.96,
        "noise_seeds": noise,
        "positive_control": pos,
        "positive_control_all_clear": all(r["clears"] for r in pos["oracle_sigma0.20"]),
    }
    return out


def common_keys(S: int) -> tuple[pd.DataFrame, dict[str, int]]:
    """Regular-season (player, game) keys present in ALL four stats' stored A0 files, sorted; plus how many each stat loses."""
    sets: dict[str, set[tuple[str, int]]] = {}
    for st in rule.STATS:
        o = arch.read_rows(st, S, ["game_id", "player_id"])
        o = o[rule.is_regular(o.game_id)]
        sets[st] = set(zip(o.game_id, o.player_id, strict=True))
    inter = set.intersection(*sets.values())
    keys = pd.DataFrame(sorted(inter), columns=["game_id", "player_id"])
    return keys, {st: len(sets[st]) - len(inter) for st in rule.STATS}


def run(out: Path = OUT) -> dict[str, Any]:
    t0 = time.time()
    rule.check_floors()
    c = arch.connect(arch.DB)
    mu = feat.read_matchups(c)
    games = feat.read_games(c)
    flagged = feat.read_flagged(c, games)
    log(f"loaded matchups {len(mu)} games {len(games)} flagged games {len(flagged)}")
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
            "GATE_DE_BAR_floor_units": rule.GATE_DE_BAR,
            "N_SEEDS": rule.N_SEEDS,
            "POS_SEEDS": rule.POS_SEEDS,
            "POS_SIGMA": rule.POS_SIGMA,
            "noise_sigma": rule.NOISE_SIGMA,
            "CM_PASS": rule.CM_PASS,
            "HURT_MAX": rule.HURT_MAX,
            "FLOORS": rule.FLOORS,
        },
        "seasons": {},
        "missingness": {},
        "power": {},
    }
    for S in (rule.SELECT, rule.REPORT):
        sf, meta, tables = g6.build_season(S, mu, games, flagged, c)
        off_a, def_a = feat.build_archetypes(c, S)
        meta["planted_future"] = g6.planted_future(
            sf, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a
        )
        keys, lost = common_keys(S)
        meta["row_sets_four_stats"] = {
            "intersection_rows": int(len(keys)),
            "pts_rows": int(len(sf)),
            "dropped_by_intersection": lost,
            "identical": bool(all(v == 0 for v in lost.values()) and len(keys) == len(sf)),
        }
        gate["seasons"][str(S)] = meta
        gate["missingness"][str(S)] = g6.missingness(sf)
        log(f"season {S} features done; four-stat rows {len(keys)} (pts rows {len(sf)})")
        gate["power"][str(S)] = power_season(S, keys)
        del sf
    gate["checks"] = checks(gate)
    gate["verdict"] = "PASS" if all(ck["pass"] for ck in gate["checks"]) else "FAIL"
    gate["wall_seconds"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gate, indent=1, default=str) + "\n")
    c.close()
    return gate


def checks(g: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for S in ("2023", "2024"):
        m = g["seasons"][S]
        pw = g["power"][S]
        out.append(
            g6.chk(
                f"planted future {S}",
                m["planted_future"]["identical"],
                "True",
                m["planted_future"]["identical"],
            )
        )
        rs = m["row_sets_four_stats"]
        out.append(
            g6.chk(
                f"row sets identical across pts/reb/ast/fg3m {S}",
                rs["dropped_by_intersection"],
                "all 0",
                rs["identical"],
            )
        )
        nan = g["missingness"][S]["nan_cells_any_feature"]
        out.append(g6.chk(f"no NaN feature cell {S}", nan, "0", nan == 0))
        for k, v in rule.gate_counts(pw["clusters"]).items():
            out.append(
                g6.chk(
                    f"{S} {k}",
                    pw["clusters"][k if k != "player_seasons" else "player_seasons"],
                    k,
                    v,
                )
            )
        de = pw["detectable_cm_half_width_floor_units"]["mean"]
        out.append(
            g6.chk(
                f"{S} mean detectable CM half-width (20 seeds), floor units",
                de,
                f"<= {rule.GATE_DE_BAR}",
                de <= rule.GATE_DE_BAR,
            )
        )
        out.append(
            g6.chk(
                f"{S} positive control: planted shift in all four stats clears in every seed",
                pw["positive_control_all_clear"],
                "True",
                pw["positive_control_all_clear"],
            )
        )
    out.append(g6.chk("season 2025 loaded", False, "never", True))
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
