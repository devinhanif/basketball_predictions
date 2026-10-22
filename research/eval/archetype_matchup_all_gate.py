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
import polars as pl

from nba.features.injury_report import OFFICIAL_REPORT_SOURCE, ReportTriggerConfig
from nba.props.context_residual import flagged_from_availability
from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_features as af
from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_gate as g6
from research.eval import archetype_matchup_rule as t506
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


def insert_future_rows(mu: pd.DataFrame, cut: int, S: int, seed: int) -> pd.DataFrame:
    """S3: a copy of ``mu`` with FUTURE structure planted (rows on or after day ``cut`` of season S): (1) 3,000 copies of future rows whose
    defender is a NEW player id with 4x the minutes; (2) 3,000 other future rows whose defending team is changed to a different team. Nothing
    on or before the day before the cut is touched."""
    rng = np.random.default_rng(seed)
    d = feat.to_day(mu.game_date)
    fut = np.flatnonzero((d >= cut) & (mu.season.to_numpy() == S))
    out = mu.copy()
    teams = np.unique(mu.def_team.to_numpy())
    chg = rng.choice(fut, size=min(3000, len(fut)), replace=False)
    new_team = rng.choice(teams, size=len(chg))
    dt = out.columns.get_loc("def_team")
    out.iloc[chg, dt] = new_team
    add = out.iloc[rng.choice(fut, size=min(3000, len(fut)), replace=False)].copy()
    add["def_player_id"] = 9_000_000 + rng.integers(0, 50, size=len(add))
    add["mm"] = add["mm"].to_numpy(float) * 4.0
    return pd.concat([out, add], ignore_index=True)


def planted_post_tip_out(c: Any, games: pd.DataFrame, max_season: int = 2024) -> dict[str, Any]:
    """S3/S5: planted official-report OUT rows through the production ``flagged_from_availability(..., tip_source="real")``.
    (1) Blunt probe: an ``as_of`` 27 h after the game date on games with a usable report (set unchanged) and without one (stays without).
    (2) The T-60 edge, at the REAL tip: a row at tip + 1 min must not count (set unchanged); a row at tip - 61 min must count, on every sampled game
    whose latest real usable snapshot is earlier than tip - 61 min (the planted player is then in the set). (3) Positive control: a row 36 h before
    a game with no usable report creates a report for it."""
    from nba.features import game_tipoff

    g = pl.from_pandas(games.assign(game_date=pd.to_datetime(games.game_date).dt.date))
    c.register("_allowed_games", games[["game_id"]])
    avail = c.execute(
        "select game_id, player_id, status, as_of, source from player_availability "
        "where game_id in (select game_id from _allowed_games)"
    ).pl()
    cfg = ReportTriggerConfig(tip_source="real")
    base, _ = flagged_from_availability(avail, g, cfg, max_season=max_season)
    with_rep = sorted(base)[:: max(1, len(base) // 300)]
    empty_all = sorted(set(games.game_id) - set(base))
    empty = empty_all[:: max(1, len(empty_all) // 300)] if empty_all else []
    tmpl = avail.filter(pl.col("source") == OFFICIAL_REPORT_SOURCE).head(1)
    tips = game_tipoff.build_game_tipoff(max_season=max_season).select(["game_id", "tip_et"])

    def planted(
        ids: list[str], first_pid: int, hours: float = 0.0, from_tip_minutes: float | None = None
    ) -> pl.DataFrame:
        if not ids:
            return pl.DataFrame(avail.head(0))
        df = pl.concat([tmpl] * len(ids)).with_columns(
            pl.Series("game_id", ids).cast(avail.schema["game_id"]),
            pl.Series("player_id", np.arange(len(ids)) + first_pid).cast(avail.schema["player_id"]),
            pl.lit("Out").alias("status"),
        )
        df = df.join(g.select(["game_id", "game_date"]), on="game_id")
        if from_tip_minutes is None:
            at = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(hours=int(hours))
        else:
            df = df.join(tips, on="game_id")
            at = pl.col("tip_et").cast(pl.Datetime("us")) + pl.duration(
                minutes=int(from_tip_minutes)
            )
        return pl.DataFrame(
            df.with_columns(at.cast(avail.schema["as_of"]).alias("as_of")).select(avail.columns)
        )

    post_rows = pl.concat([planted(with_rep, 90_000_000, 27), planted(empty, 91_000_000, 27)])
    post, _ = flagged_from_availability(
        pl.concat([avail, post_rows]), g, cfg, max_season=max_season
    )
    ctrl, _ = flagged_from_availability(
        pl.concat([avail, planted(empty, 92_000_000, -36)]), g, cfg, max_season=max_season
    )
    detected = sum(
        1 for gid in empty if gid in ctrl and (92_000_000 <= min(ctrl[gid]) < 93_000_000)
    )

    # (2) real-tip edge, on games that have a real tip
    has_tip = set(tips["game_id"].to_list())
    edge_ids = [x for x in with_rep if x in has_tip]
    cut = avail.join(tips, on="game_id").filter(
        (pl.col("source") == OFFICIAL_REPORT_SOURCE)
        & (pl.col("as_of") <= pl.col("tip_et").cast(pl.Datetime("us")) - pl.duration(minutes=60))
    )
    latest = cut.group_by("game_id").agg(
        pl.col("as_of").max().alias("_l"), pl.col("tip_et").first().alias("_t")
    )
    clear = {
        str(r[0])
        for r in latest.filter(
            pl.col("_l") < pl.col("_t").cast(pl.Datetime("us")) - pl.duration(minutes=61)
        )
        .select("game_id")
        .iter_rows()
    }
    must_count = [x for x in edge_ids if x in clear]
    plus1, _ = flagged_from_availability(
        pl.concat([avail, planted(edge_ids, 93_000_000, from_tip_minutes=1)]),
        g,
        cfg,
        max_season=max_season,
    )
    minus61, _ = flagged_from_availability(
        pl.concat([avail, planted(must_count, 94_000_000, from_tip_minutes=-61)]),
        g,
        cfg,
        max_season=max_season,
    )
    counted = sum(
        1
        for x in must_count
        if x in minus61 and any(94_000_000 <= p < 95_000_000 for p in minus61[x])
    )
    plus1_unchanged = all(plus1.get(x) == base.get(x) for x in edge_ids)
    return {
        "games_with_report_planted": len(with_rep),
        "games_without_report_planted": len(empty),
        "post_tip_sets_unchanged": bool(post == base),
        "pre_tip_control_games_created": int(detected),
        "pre_tip_control_detected": bool(len(empty) == 0 or detected == len(empty)),
        "real_tip_edge_games_with_tip": len(edge_ids),
        "tip_plus_1min_sets_unchanged": bool(plus1_unchanged),
        "tip_minus_61min_expected_to_count": len(must_count),
        "tip_minus_61min_counted": int(counted),
        "tip_minus_61min_all_counted": bool(counted == len(must_count) and len(must_count) > 0),
    }


def planted_future_full(
    sf: pd.DataFrame,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    S: int,
    tables: dict[str, Any],
    off_a: pd.Series,
    def_a: pd.Series,
    c: Any,
) -> dict[str, Any]:
    """S3. (a) T506's value check (events scaled), (b) planted ROWS (new defenders, team changes) after the cut, (c) planted post-tip OUT rows.
    ``identical`` needs all three plus the positive control: the same inserted rows MUST move features of rows after the cut."""
    a = g6.planted_future(sf, mu, games, flagged, S, tables, off_a, def_a)
    day = feat.to_day(sf.game_date)
    cut = int(np.median(day))
    before = sf[day < cut]
    sub = before.iloc[:: max(1, len(before) // 1500)][g6.ROW_COLS].reset_index(drop=True)
    pert = insert_future_rows(mu, cut, S, rule.SEED)
    f0 = feat.mix_frame(sub, mu, games, flagged, S, tables, off_a, def_a)
    f1 = feat.mix_frame(sub, pert, games, flagged, S, tables, off_a, def_a)
    rows_same = bool(
        all(
            np.allclose(f0[k].to_numpy(float), f1[k].to_numpy(float), rtol=0, atol=1e-9)
            for k in rule.FEATURES
        )
    )
    after = sf[day >= cut + 20]
    sub2 = after.iloc[:: max(1, len(after) // 800)][g6.ROW_COLS].reset_index(drop=True)
    pert_all = pert[feat.to_day(pert.game_date) < cut + 20]
    g0 = feat.mix_frame(
        sub2, mu[feat.to_day(mu.game_date) < cut + 20], games, flagged, S, tables, off_a, def_a
    )
    g1 = feat.mix_frame(sub2, pert_all, games, flagged, S, tables, off_a, def_a)
    moved = bool(
        any(
            not np.allclose(g0[k].to_numpy(float), g1[k].to_numpy(float), rtol=0, atol=1e-9)
            for k in rule.FEATURES
        )
    )
    out = planted_post_tip_out(c, games)
    ok = bool(
        a["identical"]
        and rows_same
        and moved
        and out["post_tip_sets_unchanged"]
        and out["pre_tip_control_detected"]
        and out["tip_plus_1min_sets_unchanged"]
        and out["tip_minus_61min_all_counted"]
    )
    return {
        "values_check": a,
        "inserted_rows_before_cut_identical": rows_same,
        "inserted_rows_move_later_features_positive_control": moved,
        "queries_before_cut": int(len(sub)),
        "rows_inserted": int(len(pert) - len(mu)),
        "post_tip_out": out,
        "identical": ok,
    }


def season_assertions(
    mu: pd.DataFrame, games: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> dict[str, Any]:
    """S3: a real replacement of the old always-true "season 2025 loaded" line: the largest season/date actually read."""
    last = max(pd.to_datetime(f.game_date).max() for f in frames.values())
    return {
        "max_season_matchups": int(mu.season.max()),
        "max_season_games": int(games.season.max()),
        "max_game_date_model_rows": str(last.date()),
        "ok": bool(
            mu.season.max() <= 2024
            and games.season.max() <= 2024
            and last < pd.Timestamp("2025-07-01")
        ),
    }


def run(out: Path = OUT, label: str = "Run") -> dict[str, Any]:
    t0 = time.time()
    rule.check_floors()
    c = arch.connect(arch.DB)
    mu = feat.read_matchups(c)
    games = feat.read_games(c)
    flagged = feat.read_flagged(c, games)
    log(f"loaded matchups {len(mu)} games {len(games)} flagged games {len(flagged)}")
    gate: dict[str, Any] = {
        "id": rule.ID,
        "status": f"DRAFT gate {label}, label-free (y and realised minutes never selected)",
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
            "ALIGN_MIN_AGREE": rule.ALIGN_MIN_AGREE,
            "FLOORS": rule.FLOORS,
        },
        "seasons": {},
        "missingness": {},
        "power": {},
    }
    seen: dict[str, pd.DataFrame] = {}
    for S in (rule.SELECT, rule.REPORT):
        off_a, def_a, align_rep = af.build_aligned_archetypes(c, S)
        keys, lost = common_keys(S)
        pts_rows = arch.read_rows("pts", S, g6.ROW_COLS)
        seen[str(S)] = pts_rows
        sf, tables = af.season_frame(S, keys, pts_rows, mu, games, flagged, off_a, def_a)
        meta: dict[str, Any] = {
            "rows": int(len(sf)),
            "games": int(sf.game_id.nunique()),
            "alignment": align_rep,
            "archetype_sizes": {
                "off": {int(k): int(v) for k, v in off_a.value_counts().sort_index().items()},
                "def": {int(k): int(v) for k, v in def_a.value_counts().sort_index().items()},
                "players_clustered": int(len(off_a)),
            },
            "lg_rate_prior_season": tables["lg"],
            "share_rows_no_roster": float(sf.no_roster.mean()),
            "share_rows_game_has_usable_report": float(sf.has_report.mean()),
            "off_arch_unknown_share": float((sf.off_arch == t506.UNK_OFF).mean()),
            "shift_l1_quantiles": {
                str(q): float(sf.shift_l1.quantile(q)) for q in (0.5, 0.75, 0.9, 0.95, 0.99)
            },
        }
        meta["planted_future"] = planted_future_full(
            sf, mu[mu.season <= S], games, flagged, S, tables, off_a, def_a, c
        )
        meta["row_sets_four_stats"] = {
            "intersection_rows": int(len(keys)),
            "pts_rows": int(len(sf)),
            "dropped_by_intersection": lost,
            "identical": bool(all(v == 0 for v in lost.values())),
        }
        gate["seasons"][str(S)] = meta
        gate["missingness"][str(S)] = g6.missingness(sf)
        log(f"season {S} features done; four-stat rows {len(keys)}")
        gate["power"][str(S)] = power_season(S, keys)
        del sf
    gate["season_assertions"] = season_assertions(mu, games, seen)
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
                    pw["clusters"][k],
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
    al = g["seasons"]["2024"]["alignment"]["offense"]
    out.append(
        g6.chk(
            "M2 offense labels 2024 aligned to 2023: agreement over players clustered in both",
            al["agreement_after"],
            f">= {rule.ALIGN_MIN_AGREE}",
            al["agreement_after"] >= rule.ALIGN_MIN_AGREE,
        )
    )
    sa = g["season_assertions"]
    out.append(
        g6.chk(
            "no season after 2024 read (max matchup season, max games season, max model_rows date)",
            f"{sa['max_season_matchups']} / {sa['max_season_games']} / {sa['max_game_date_model_rows']}",
            "<= 2024, < 2025-07-01",
            sa["ok"],
        )
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument(
        "--label", default="Run", help="the run number as written in the artifact, e.g. 'Run 4'"
    )
    a = ap.parse_args(argv)
    g = run(Path(a.out), a.label)
    for ck in g["checks"]:
        print(("PASS " if ck["pass"] else "FAIL ") + ck["check"], ck["value"])
    print("VERDICT", g["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
