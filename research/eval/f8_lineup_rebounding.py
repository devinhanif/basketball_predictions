# ruff: noqa: E501
"""F8 lineup rebounding (docs/prereg/F8_LINEUP_REBOUNDING.md, frozen sha256 2a8e581e).

    M="-m research.eval.f8_lineup_rebounding"
    .venv/bin/python $M gate       # G1-G4 + planted tests (light-ish; writes reports/prereg_f8/)
    .venv/bin/python $M arms       # A0-A4 (T-60) + A0/A1 (T-30) x {reb, pts}; heavy.lock held
    .venv/bin/python $M knockout   # only if rules 1-4 pass (red-team knockout arms)
    .venv/bin/python $M report     # reports/prereg_f8.md from the JSON
    .venv/bin/python $M smoke      # one month block, A0 vs A1, no files

Research module only: ``nba/`` is untouched (no ``ContextResidualConfig.lineup_reb`` flag); the
columns are built by :mod:`research.features.lineup_rebounding` and passed through explicit
``stat_feature_names(stat, extra=...)`` lists. Read-only on ``nba.duckdb``; every SQL carries
``season <= 2024`` and the loaded frames are asserted; season 2025 is never loaded.

Implementation readings of unstated details are in the module docstring of the feature module and
in the ``UNSTATED`` list below; they were fixed before any arm was scored.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import EPS, attach_realised, randomized_pit
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from research.eval.f11_lineup_context import (
    _bucket_minutes,
    _ci,
    _jd,
    _post_tip_out_flagged,
    _write_json,
    add_traded,
    frozen_sha256,
    git_sha,
    heavy_lock,
    plant_future,
    plant_same_game,
    wait_for_replay,
)
from research.features.lineup_rebounding import (
    GUARD_HEIGHT,
    HOU,
    LR30,
    LR_A,
    LR_COLS,
    LR_COLS_T30,
    build_lineup_reb,
    poss_tallies,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = Path("docs/prereg/F8_LINEUP_REBOUNDING.md")
OUT_DIR = Path("reports/prereg_f8")
REPORT_MD = Path("reports/prereg_f8.md")
CACHE_DIR = Path("data/ops/f8_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
FROZEN_PREFIX = "2a8e581e"
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
N_BOOT = 2000
STATS: tuple[str, ...] = ("reb", "pts")
T60_ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "A4")
T30_ARMS: tuple[str, ...] = ("A0_30", "A1_30")
TLL_THRESHOLDS: dict[str, tuple[int, ...]] = {"reb": (4, 6, 8, 10), "pts": (10, 15, 20)}

# --- frozen thresholds
G1_MIN = 0.90
G2_MIN_TEAM_GAMES = 300
G2_MIN_ROWS = 300
G3_RATIO = (0.95, 1.05)
G3_CORR = 0.95
G4_MAX_GAP_PP = 2.0
MIN_PRIOR_POSS = 500.0
BIGS_MIN_N = 3000
FLOOR = -0.005
NO_HARM_POINT, NO_HARM_HI = 0.001, 0.003
STRUCT_FLOOR = -0.003
ATTACK_FLOOR = -0.003
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
PIT_TOL = 0.02
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
KNOCKOUT_RED_FLAG = 0.8

UNSTATED: list[str] = [
    "big = height_in >= 82 or position contains Center, used for is_big_p, is_big_q and the bigs slice "
    "(header item (b)); guards = height_in < 78; others = every remaining played row (incl. unknown height)",
    "co-play weights c(p,q): F11 construction (prior 10 team games, HL 10, eligible = stint seconds in "
    "the window and last team before the date = this team, OUT removed, minutes-share fallback when "
    "p has < 300 window seconds, sum c = 4)",
    "S(p) T-60 = p + the 4 available teammates with the largest c (ties: more starts in the prior 10 "
    "team games, then lower player id); shorter when fewer than 4 are available",
    "T-30: tonight's box-score starter flags are the proxy of the confirmed five (docs/LINEUPS_KNOWN.md, "
    "optimistic); used only when exactly five are flagged for the team-game, else T-60 S. Starter p: "
    "the five starters; bench p: p + the 4 starters with the largest prior-10 co-play weight. c is NOT "
    "re-weighted at T-30 (groups A, C and lr_nbig identical), only lr_dbl, lr_h5, lr_h2 change",
    "T-30 comparison arm A0_30 = A0 + the production t30_* columns (stat_feature_names(lineups_known=True)); "
    "A1_30 = A0_30 + A1 columns with the three lr30_* replacements; dCRPS at T-30 is A1_30 - A0_30",
    "group C: OREB% = sum n_oreb / sum (n_oreb + n_dreb) over the possessions p was on the floor on "
    "offence (every rebound event of those possessions is a rebound chance), DREB% the same on "
    "defence, cumulative over all prior games (no decay), pseudo-count 1,500 chances toward the as-of "
    "team-season rate (league as-of rate if the team has no earlier game that season)",
    "lr_p_gap baseline = as-of mean lr_nbig of played rows with the same position code (G/F/C/other) "
    "in the same season (all seasons when < 200 rows)",
    "lr_chg = lr_nbig minus the mean lr_nbig of the team's played rows over its previous 10 games",
    "projected big OUT (lineup-change slice) = a big whose mean minutes over the games he played "
    "among the team's previous 10 is >= 20 is on the real-tip-gated OUT list",
    "A2 = A0 + lr_oreb_sum + lr_dreb_sum; A3 permutes all lr_* / lr30_* columns jointly across rows "
    "within team-season (seed 0); A4 uses tonight's actual stints for c and S",
    "BH family {reb, pts} per season: the table p / p_bh use the all-rows A1-A0 p; rule 1 uses the "
    "bigs-slice p with BH over {reb, pts}",
    "rules 2 and 4 (A1 vs A2, A1 vs A3, A1 vs A4) are evaluated on the bigs slice in 2024 (the slice "
    "of rule 1); the all-rows values are reported beside them",
    "rule 3 guards use A1 all rows in 2024 (2023 reported); the slice guard uses the T-60 A1-A0 "
    "slice table (declared slices only, all excluded)",
    "knockout (rule 4) is run only if rules 1-4 otherwise pass; a group 'carries' the gain when the "
    "A1 bigs-slice gain without it is <= 20% of the A1 gain (>= 80% lost)",
    "G2 team-games use the not-tip-gated union of player_availability 'out' rows (coverage count)",
    "G4 gap over the four realised-minutes buckets (<5, 5-10, 10-20, >20) and the three reb terciles "
    "per season; DNP rows are audited but outside the gap",
    "tll = mean threshold log loss of P(y >= n) at reb 4/6/8/10 and pts 10/15/20 on the integer grid",
    "A0 reproduction: integer CRPS vs reports/lower_tail/candidates.json 'off' (n and crps_int) to 1e-6",
]


# --------------------------------------------------------------------------- inputs


def load_extra(db_path: str = "nba.duckdb") -> dict[str, pl.DataFrame]:
    """Box rows (with oreb/dreb), static (position, height), stints and possessions; season <= 2024."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, "
            "s.starter, s.oreb, s.dreb FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON}"
        ).pl()
        static = con.execute("SELECT player_id, position, height_in FROM players_static").pl()
        stints = con.execute(
            "SELECT s.game_id, s.team_id, s.period, s.start_clock, s.end_clock, s.players "
            f"FROM stints s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        poss = con.execute(
            "SELECT p.game_id, p.off_team, p.def_team, p.off_players, p.def_players, "
            "COALESCE(p.pts, 0) AS pts, p.n_oreb, p.n_dreb FROM possessions p "
            f"JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    return {"pgs": pgs, "static": static, "stints": stints, "poss": poss}


def data_version() -> str:
    try:
        from nba.datamanifest.manifest import latest_data_version

        return str(latest_data_version())
    except Exception as exc:  # pragma: no cover
        return f"unavailable ({type(exc).__name__}); doc names 1c5b0901afdf"


def load_study(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    with_t30: bool = True,
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    extra = load_extra(db_path)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = add_traded(
        attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    )
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    out_any: dict[str, set[int]] = {}
    for g, p in (
        avail.filter(
            (pl.col("status").str.to_lowercase() == "out")
            & pl.col("game_id").is_not_null()
            & pl.col("player_id").is_not_null()
        )
        .select("game_id", "player_id")
        .iter_rows()
    ):
        out_any.setdefault(str(g), set()).add(int(p))
    res: dict[str, Any] = {
        "games": games,
        "pgs": pgs,
        "avail": avail,
        "flagged": flagged,
        "flagged_any": out_any,
        "cfg": cfg,
        "feats": feats,
        "pgs2": extra["pgs"],
        "static2": extra["static"],
        "stints": extra["stints"],
        "poss": extra["poss"],
    }
    if with_t30:
        f30 = add_traded(
            attach_realised(
                build_features(games, pgs, static, flagged, elo, lineups_known=True), games, pgs
            )
        )
        assert f30.select("game_id", "player_id").equals(feats.select("game_id", "player_id"))
        res["feats30"] = f30
    return res


def build_lr(inp: dict[str, Any]) -> tuple[pl.DataFrame, pl.DataFrame]:
    return build_lineup_reb(
        inp["games"],
        inp["pgs2"],
        inp["static2"],
        inp["stints"],
        inp["poss"],
        inp["flagged"],
        inp["flagged_any"],
    )


def build_or_load_lr(
    inp: dict[str, Any], refresh: bool = False
) -> tuple[pl.DataFrame, pl.DataFrame]:
    pa, po = CACHE_DIR / "lr_a1.parquet", CACHE_DIR / "lr_oracle.parquet"
    if pa.exists() and po.exists() and not refresh:
        return pl.read_parquet(pa), pl.read_parquet(po)
    t0 = time.time()
    a1, orc = build_lr(inp)
    print(f"lr built in {time.time() - t0:.0f}s ({a1.height} roster rows)", flush=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    a1.write_parquet(pa)
    orc.write_parquet(po)
    return a1, orc


KEEP_COLS: tuple[str, ...] = (
    *LR_COLS,
    *LR30,
    "is_big_p",
    "height_in",
    "big_out_proj",
    "lr_prior_poss",
    "lr_has_h",
)
KEYS = ["game_id", "player_id"]


def attach_lr(feats: pl.DataFrame, a1: pl.DataFrame) -> pl.DataFrame:
    return feats.join(a1.select(*KEYS, *KEEP_COLS), on=KEYS, how="left")


def placebo_frame(feats: pl.DataFrame, seed: int = SEEDS["placebo"]) -> pl.DataFrame:
    """A3: all lineup columns permuted jointly across rows within team-season."""
    d = feats.with_row_index("_ord")
    groups = d.group_by(["team_id", "season"], maintain_order=True).agg(pl.col("_ord"))
    rng = np.random.default_rng(seed)
    src = np.arange(d.height)
    for ords in groups["_ord"].to_list():
        o = np.asarray(ords)
        src[o] = o[rng.permutation(len(o))]
    cols = [*LR_COLS, *LR30]
    return d.with_columns([pl.Series(c, d[c].to_numpy()[src]) for c in cols]).drop("_ord")


def oracle_frame(feats: pl.DataFrame, orc: pl.DataFrame) -> pl.DataFrame:
    cols = [*LR_COLS, *LR30]
    return feats.drop(cols).join(orc.select(*KEYS, *cols), on=KEYS, how="left")


# --------------------------------------------------------------------------- gate


def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def lr_equal(
    a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str] | None, cols: tuple[str, ...]
) -> bool:
    if game_ids is not None:
        a = a.filter(pl.col("game_id").is_in(list(game_ids)))
        b = b.filter(pl.col("game_id").is_in(list(game_ids)))
    a, b = a.sort(KEYS), b.sort(KEYS)
    if a.height != b.height or not a.select(KEYS).equals(b.select(KEYS)):
        return False
    return all(np.array_equal(a[c].to_numpy(), b[c].to_numpy(), equal_nan=True) for c in cols)


def g3_reconcile(poss: pl.DataFrame, pgs: pl.DataFrame) -> dict[str, Any]:
    team, _ = poss_tallies(
        poss.select(
            "game_id", "off_team", "def_team", "off_players", "def_players", "n_oreb", "n_dreb"
        )
    )
    box = (
        pgs.group_by(["game_id", "team_id"])
        .agg(
            pl.col("oreb").fill_null(0).sum().alias("b_oreb"),
            pl.col("dreb").fill_null(0).sum().alias("b_dreb"),
        )
        .with_columns(pl.col("team_id").cast(pl.Int64))
    )
    d = team.join(box, on=["game_id", "team_id"], how="inner")
    out: dict[str, Any] = {"n_team_games": d.height}
    for a, b, k in (("t_off_oreb", "b_oreb", "oreb"), ("t_def_dreb", "b_dreb", "dreb")):
        x, y = d[a].to_numpy(), d[b].to_numpy().astype(float)
        out[f"{k}_ratio"] = float(x.sum() / y.sum())
        out[f"{k}_corr"] = float(np.corrcoef(x, y)[0, 1])
    return out


def missingness(
    a1: pl.DataFrame, pgs: pl.DataFrame, games: pl.DataFrame
) -> tuple[list[dict[str, Any]], float]:
    cols = [*LR_COLS, *LR30]
    d = (
        a1.select(*KEYS, *cols)
        .join(pgs.select(*KEYS, "minutes", "reb"), on=KEYS, how="left")
        .join(games.select("game_id", "season"), on="game_id", how="left")
        .with_columns(_bucket_minutes(pl.col("minutes")).alias("_b"))
    )
    audit: list[dict[str, Any]] = []
    gaps: list[float] = []
    played = (
        d.filter(pl.col("_b") != "DNP")
        .with_columns(
            pl.col("reb").rank("average").over("season").alias("_r"),
            pl.len().over("season").alias("_n"),
        )
        .with_columns(
            pl.when(pl.col("_r") <= pl.col("_n") / 3)
            .then(pl.lit("reb:T1"))
            .when(pl.col("_r") <= pl.col("_n") * 2 / 3)
            .then(pl.lit("reb:T2"))
            .otherwise(pl.lit("reb:T3"))
            .alias("_t")
        )
    )
    for c in cols:
        for label, frame, col_ in (("b", d, "_b"), ("t", played, "_t")):
            rates = []
            order = (
                ["DNP", "<5", "5-10", "10-20", ">20"]
                if label == "b"
                else ["reb:T1", "reb:T2", "reb:T3"]
            )
            for b in order:
                sub = frame.filter(pl.col(col_) == b)
                r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
                audit.append({"col": c, "bucket": b, "null_rate": r, "n": sub.height})
                if b != "DNP":
                    rates.append(r)
            gaps.append((max(rates) - min(rates)) * 100.0)
    return audit, max(gaps)


def run_gate(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    t0 = time.time()
    sha = frozen_sha256(DOC)
    assert sha.startswith(FROZEN_PREFIX), f"frozen sha mismatch: {sha}"
    inp = load_study(db_path, with_t30=False)
    games, pgs2, static2, stints, poss = (
        inp["games"],
        inp["pgs2"],
        inp["static2"],
        inp["stints"],
        inp["poss"],
    )
    flagged, feats = inp["flagged"], inp["feats"]
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    a1, _orc = build_or_load_lr(inp, refresh=True)
    print(f"lr built {time.time() - t0:.0f}s", flush=True)
    tests: list[dict[str, Any]] = []
    # --- G3
    g3 = g3_reconcile(poss, pgs2)
    tests.append(
        _chk(
            "G3 oreb ratio",
            g3["oreb_ratio"],
            "0.95-1.05",
            G3_RATIO[0] <= g3["oreb_ratio"] <= G3_RATIO[1],
        )
    )
    tests.append(
        _chk(
            "G3 dreb ratio",
            g3["dreb_ratio"],
            "0.95-1.05",
            G3_RATIO[0] <= g3["dreb_ratio"] <= G3_RATIO[1],
        )
    )
    tests.append(
        _chk("G3 oreb team-game corr", g3["oreb_corr"], ">= 0.95", g3["oreb_corr"] >= G3_CORR)
    )
    tests.append(
        _chk("G3 dreb team-game corr", g3["dreb_corr"], ">= 0.95", g3["dreb_corr"] >= G3_CORR)
    )
    # --- G1 (played, n_prior >= 5 rows)
    fr = feats.filter(pl.col("n_prior") >= 5).join(
        a1.select(*KEYS, *KEEP_COLS), on=KEYS, how="left"
    )
    g1: dict[str, Any] = {}
    for season in (2022, 2023, 2024):
        s = fr.filter(pl.col("season") == season)
        comp = (s["lr_prior_poss"] >= MIN_PRIOR_POSS) & (s["lr_has_h"] == 1)
        g1[str(season)] = {
            "n": s.height,
            "computable": float(comp.mean()),
            "nan_lr_nbig": float(s["lr_nbig"].is_nan().mean()),
        }
    for season in (2023, 2024):
        tests.append(
            _chk(
                f"G1 computable share {season}",
                g1[str(season)]["computable"],
                f">= {G1_MIN}",
                g1[str(season)]["computable"] >= G1_MIN,
            )
        )
    # --- G2
    tg24 = (
        a1.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("season") == 2024)
        .group_by(["game_id", "team_id"])
        .agg(pl.col("big_out_proj_any").first())
    )
    n_tg = int(tg24["big_out_proj_any"].sum())
    s24 = fr.filter(pl.col("season") == 2024)
    n_hb = int(((s24["team_id"] == HOU) | (s24["is_big_p"] == 1)).sum())
    n_bigs = {
        str(s): int((fr.filter(pl.col("season") == s)["is_big_p"] == 1).sum()) for s in (2023, 2024)
    }
    tests.append(
        _chk(
            "G2 team-games with projected big OUT (2024, not tip-gated)",
            n_tg,
            f">= {G2_MIN_TEAM_GAMES}",
            n_tg >= G2_MIN_TEAM_GAMES,
        )
    )
    tests.append(_chk("G2 HOU-or-bigs rows (2024)", n_hb, f">= {G2_MIN_ROWS}", n_hb >= G2_MIN_ROWS))
    for s, n in n_bigs.items():
        tests.append(
            _chk(f"R1 bigs rows {s} (rule 1 power)", n, f">= {BIGS_MIN_N}", n >= BIGS_MIN_N)
        )
    # --- G4
    audit, gap = missingness(a1, pgs2, games)
    tests.append(
        _chk(
            "G4 max NULL-rate gap across minutes buckets / reb terciles (pp)",
            gap,
            f"<= {G4_MAX_GAP_PP}",
            gap <= G4_MAX_GAP_PP,
        )
    )
    # --- planted tests
    reg24 = games.filter(
        (pl.col("season") == REPORT_SEASON) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list())
    upto = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    before_day = set(games.filter(pl.col("game_date") < day)["game_id"].to_list())
    all_g = set(games["game_id"].to_list())
    gl = list(targets)

    def rebuild(
        pg: pl.DataFrame, st: pl.DataFrame, po: pl.DataFrame, fl: dict[str, set[int]]
    ) -> pl.DataFrame:
        r, _ = build_lineup_reb(games, pg, static2, st, po, fl, fl)
        print(f"  rebuild done {time.time() - t0:.0f}s", flush=True)
        return r

    pg3, st3, po3 = plant_same_game(pgs2, stints, poss, targets)
    pg3 = pg3.with_columns(
        pl.when(pl.col("game_id").is_in(gl))
        .then(pl.col("oreb") + 9)
        .otherwise(pl.col("oreb"))
        .alias("oreb"),
        pl.when(pl.col("game_id").is_in(gl))
        .then(pl.col("dreb") + 9)
        .otherwise(pl.col("dreb"))
        .alias("dreb"),
    )
    po3 = po3.with_columns(
        pl.when(pl.col("game_id").is_in(gl))
        .then(pl.col("n_oreb") + 2)
        .otherwise(pl.col("n_oreb"))
        .alias("n_oreb"),
        pl.when(pl.col("game_id").is_in(gl))
        .then(pl.col("n_dreb") + 2)
        .otherwise(pl.col("n_dreb"))
        .alias("n_dreb"),
    )
    p3 = rebuild(pg3, st3, po3, flagged)
    same_ok = lr_equal(a1, p3, upto, LR_COLS)
    lr30_before_ok = lr_equal(a1, p3, before_day, LR30)
    lr30_moves = not lr_equal(a1, p3, targets, LR30)
    later_moves = not lr_equal(a1, p3, all_g - upto, LR_COLS)
    tests += [
        _chk(
            "PLANT same-game box/stints/possessions/starters edited: lr_* identical through the day",
            int(same_ok),
            "== 1",
            same_ok,
        ),
        _chk(
            "PLANT same-game is not inert (later games change)",
            int(later_moves),
            "== 1",
            later_moves,
        ),
        _chk(
            "PLANT lr30_* identical for games before the day",
            int(lr30_before_ok),
            "== 1",
            lr30_before_ok,
        ),
        _chk(
            "CONTROL lr30_* (T-30) reacts to flipped starter flags on the day",
            int(lr30_moves),
            "== 1",
            lr30_moves,
        ),
    ]
    late_out, early_out = _post_tip_out_flagged(inp, targets)
    p3b = rebuild(pgs2, stints, poss, late_out)
    post_ok = lr_equal(a1, p3b, None, (*LR_COLS, *LR30))
    p3c = rebuild(pgs2, stints, poss, early_out)
    ctl = not lr_equal(a1, p3c, targets, LR_COLS)
    tests += [
        _chk(
            "PLANT teammate moved to OUT after the real tip: no change",
            int(post_ok),
            "== 1",
            post_ok,
        ),
        _chk("CONTROL same teammate OUT before the tip does change lr_*", int(ctl), "== 1", ctl),
    ]
    cutoff = dt.date(2024, 1, 15)
    pg4, st4, po4 = plant_future(games, pgs2, stints, poss, cutoff)
    late = games.filter(pl.col("game_date").cast(pl.Date) >= cutoff)["game_id"].to_list()
    pg4 = pg4.with_columns(
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("oreb") + 5)
        .otherwise(pl.col("oreb"))
        .alias("oreb"),
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("dreb") + 5)
        .otherwise(pl.col("dreb"))
        .alias("dreb"),
    )
    po4 = po4.with_columns(
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("n_oreb") + 2)
        .otherwise(pl.col("n_oreb"))
        .alias("n_oreb"),
        pl.when(pl.col("game_id").is_in(late))
        .then(pl.col("n_dreb") + 2)
        .otherwise(pl.col("n_dreb"))
        .alias("n_dreb"),
    )
    p4 = rebuild(pg4, st4, po4, flagged)
    before = set(games.filter(pl.col("game_date").cast(pl.Date) <= cutoff)["game_id"].to_list())
    fut_ok = lr_equal(a1, p4, before, (*LR_COLS, *LR30))
    fut_moves = not lr_equal(a1, p4, all_g - before, LR_COLS)
    tests += [
        _chk(
            "PLANT future (>= cutoff date) stints/box/possessions: earlier lr_* unchanged",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk("PLANT future is not inert (later games change)", int(fut_moves), "== 1", fut_moves),
    ]
    res: dict[str, Any] = {
        "frozen_sha256": sha,
        "frozen_prefix_ok": sha.startswith(FROZEN_PREFIX),
        "git_sha": git_sha(),
        "data_version": data_version(),
        "seeds": SEEDS,
        "g1": g1,
        "g2": {"team_games_big_out_2024": n_tg, "hou_or_bigs_rows_2024": n_hb, "bigs_rows": n_bigs},
        "g3": g3,
        "g4_max_gap_pp": gap,
        "audit": audit,
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests) and sha.startswith(FROZEN_PREFIX),
        "max_season_loaded": int(feats["season"].max()),
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {k: res[k] for k in ("frozen_sha256", "g1", "g2", "g3", "g4_max_gap_pp", "tests", "audit")},
        indent=2,
        default=_jd,
        sort_keys=True,
    )
    (out_dir / "gate.json").write_text(body)
    res["gate_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    _write_json(out_dir / "results.json", res)
    return res


# --------------------------------------------------------------------------- walk-forward

META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    "traded",
    "is_big_p",
    "height_in",
    "big_out_proj",
    "lr_nbig",
    "lr_chg",
    "lr_cover",
)


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    max_blocks: int | None = None,
) -> dict[str, Any]:
    """Walk-forward month blocks (train strictly before the block; 2022 warm-up), integer grid."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    qs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for b, (start, end) in enumerate(month_blocks(test_all)):
        if max_blocks is not None and b >= max_blocks:
            break
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        z = (cal_df["resid"].to_numpy() - mu) / sc
        q = raw_quantiles(c, s, z, CRPS_TAUS)
        qs.append(to_integer_support(q).astype(np.int16))
        ys.append(block["y"].to_numpy())
        metas.append(block.select([c_ for c_ in META_COLS if c_ in block.columns]))
        print(
            f"  {stat} block {start} done ({time.time() - t0:.0f}s, n={block.height})", flush=True
        )
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def arm_cache(stat: str, arm: str) -> Path:
    return CACHE_DIR / f"arm_{stat}_{arm}.npz"


def collect_cached(
    frame: pl.DataFrame, stat: str, arm: str, cfg: ContextResidualConfig, names: list[str]
) -> dict[str, Any]:
    p = arm_cache(stat, arm)
    if p.exists():
        z = np.load(p)
        return {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    t0 = time.time()
    out = collect_arm(frame, stat, cfg, names)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=out["qi"], y=out["y"])
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {arm} done in {time.time() - t0:.0f}s", flush=True)
    return out


def arm_names(stat: str, arm: str) -> list[str]:
    extra: dict[str, tuple[str, ...]] = {
        "A0": (),
        "A1": LR_COLS,
        "A2": LR_A,
        "A3": LR_COLS,
        "A4": LR_COLS,
    }
    if arm in T30_ARMS:
        return stat_feature_names(
            stat, extra=LR_COLS_T30 if arm == "A1_30" else (), lineups_known=True
        )
    return stat_feature_names(stat, extra=extra[arm])


# --------------------------------------------------------------------------- scoring


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def row_scores(
    qi: np.ndarray, y: np.ndarray, stat: str, seed: int = SEEDS["pit"]
) -> dict[str, np.ndarray]:
    qf = qi.astype(float)
    pit = randomized_pit(qf, y, seed)
    tll = np.mean([_ll((qf >= n).mean(axis=1), y >= n) for n in TLL_THRESHOLDS[stat]], axis=0)
    return {
        "crps": crps_from_quantiles(qf, y),
        "resid": y - qf.mean(axis=1),
        "pit": pit,
        "tll": tll,
    }


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    dd = add_slice_columns(d)
    h = d["height_in"].to_numpy().astype(float)
    big = (d["is_big_p"] == 1).fill_null(False).to_numpy()
    guard = np.isfinite(h) & (h < GUARD_HEIGHT) & ~big
    chg = d["lr_chg"].to_numpy().astype(float)
    return {
        "all": np.ones(d.height, dtype=bool),
        "bigs": big,
        "guards": guard,
        "others": ~big & ~guard,
        "HOU": (d["team_id"] == HOU).fill_null(False).to_numpy(),
        "lineup_change": d["big_out_proj"].fill_null(False).to_numpy(),
        "lr_chg!=0": np.isfinite(chg) & (np.abs(np.nan_to_num(chg)) > 1e-9),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "teammate_out": (dd["sl_tm_out"] == "teammate_out").to_numpy(),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
    }


GUARD_SLICES: tuple[str, ...] = (
    "bigs",
    "guards",
    "others",
    "HOU",
    "lineup_change",
    "lr_chg!=0",
    "first15",
    "teammate_out",
    "starter",
    "bench",
)


def tercile_masks(d: pl.DataFrame, col: str) -> dict[str, np.ndarray]:
    v = d[col].to_numpy().astype(float)
    season = d["season"].to_numpy()
    out = {f"T{i}": np.zeros(d.height, dtype=bool) for i in (1, 2, 3)}
    for s in np.unique(season):
        m = (season == s) & ~np.isnan(v)
        if not m.any():
            continue
        lo, hi = np.quantile(v[m], [1 / 3, 2 / 3])
        out["T1"] |= m & (v <= lo)
        out["T2"] |= m & (v > lo) & (v <= hi)
        out["T3"] |= m & (v > hi)
    return out


def _delta_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    ci = _ci(delta, gid, N_BOOT)
    return {
        "dcrps": ci["point"],
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "p": boot_p_value(delta, gid, N_BOOT),
        "mde": (ci["hi"] - ci["lo"]) / 2.0,
    }


def score_stat(stat: str, arms: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Tables for one stat from the collected arms."""
    ref = arms["A0"]["meta"]
    for a, v in arms.items():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
            f"{a}: arms must score identical rows"
        )
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    mech: list[dict[str, Any]] = []
    attack: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: row_scores(v["qi"][sm], v["y"][sm], stat) for a, v in arms.items()}
        masks = slice_masks(d)
        for a in arms:
            snap = "T30" if a in T30_ARMS else "T60"
            base_arm = "A0_30" if snap == "T30" else "A0"
            s = sc[a]
            r: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": a,
                "snapshot": snap,
                "n_rows": int(d.height),
                "n_games": int(len(np.unique(gid))),
                "crps_int": float(s["crps"].mean()),
                "bias": float(s["resid"].mean()),
                "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()),
                "pit_q10": float((s["pit"] <= 0.10).mean()),
                "pit_q20": float((s["pit"] <= 0.20).mean()),
                "tll": float(s["tll"].mean()),
                "dcrps": None,
                "ci_lo": None,
                "ci_hi": None,
                "p": None,
                "p_bh": None,
                "mde": None,
            }
            if a not in ("A0", "A0_30"):
                r.update(_delta_stats(s["crps"] - sc[base_arm]["crps"], gid))
            rows.append(r)
        d1 = sc["A1"]["crps"] - sc["A0"]["crps"]
        for name, m in masks.items():
            n = int(m.sum())
            rec: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "slice": name,
                "snapshot": "T60",
                "n": n,
            }
            if n >= MIN_SLICE_N or name == "all":
                rec.update(_delta_stats(d1[m], gid[m]))
            sl.append(rec)
        d30 = sc["A1_30"]["crps"] - sc["A0_30"]["crps"]
        for name in ("all", "bigs"):
            m = masks[name]
            rec = {
                "stat": stat,
                "season": season,
                "slice": name,
                "snapshot": "T30",
                "n": int(m.sum()),
            }
            if int(m.sum()) >= MIN_SLICE_N:
                rec.update(_delta_stats(d30[m], gid[m]))
            sl.append(rec)
        # mechanism: dCRPS by tercile of lr_nbig (A1 - A0, T-60) and HOU bias A0 vs A1
        for tname, m in tercile_masks(d, "lr_nbig").items():
            rec = {
                "stat": stat,
                "season": season,
                "by": "lr_nbig",
                "tercile": tname,
                "n": int(m.sum()),
            }
            if int(m.sum()):
                rec.update(_delta_stats(d1[m], gid[m]))
            mech.append(rec)
        hm = masks["HOU"]
        mech.append(
            {
                "stat": stat,
                "season": season,
                "by": "HOU_bias",
                "tercile": "HOU",
                "n": int(hm.sum()),
                "bias_A0": float(sc["A0"]["resid"][hm].mean()) if hm.any() else None,
                "bias_A1": float(sc["A1"]["resid"][hm].mean()) if hm.any() else None,
            }
        )
        for scope in ("all", "bigs"):
            m = masks[scope]
            ent: dict[str, Any] = {"stat": stat, "season": season, "scope": scope}
            for other in ("A2", "A3", "A4"):
                ent[f"A1_minus_{other}"] = _ci(
                    sc["A1"]["crps"][m] - sc[other]["crps"][m], gid[m], N_BOOT
                )
            ent["A4_dcrps"] = float((sc["A4"]["crps"][m] - sc["A0"]["crps"][m]).mean())
            ent["A3_dcrps"] = float((sc["A3"]["crps"][m] - sc["A0"]["crps"][m]).mean())
            ent["A2_dcrps"] = float((sc["A2"]["crps"][m] - sc["A0"]["crps"][m]).mean())
            attack.append(ent)
    return {"rows": rows, "slices": sl, "mech": mech, "attack": attack}


def add_bh(rows: list[dict[str, Any]], slices: list[dict[str, Any]]) -> None:
    """BH over {reb, pts}, per season: all-rows A1 p (table) and bigs-slice p (rule 1)."""
    for season in TEST_SEASONS:
        tgt = [r for r in rows if r["season"] == season and r["arm"] == "A1"]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)
        bg = [
            s
            for s in slices
            if s["season"] == season and s["slice"] == "bigs" and s["snapshot"] == "T60"
        ]
        adj2 = bh_adjust(np.array([s["p"] for s in bg]))
        for s, a in zip(bg, adj2, strict=True):
            s["p_bh"] = float(a)


# --------------------------------------------------------------------------- pass rule


def evaluate_rules(res: dict[str, Any], tests_ok: bool) -> dict[str, Any]:
    rows, sl, atk = res["rows"], res["slices"], res["attack"]
    tab = {(r["stat"], r["season"], r["arm"]): r for r in rows}
    sli = {(s["stat"], s["season"], s["slice"], s["snapshot"]): s for s in sl}
    out: dict[str, Any] = {}
    for stat in STATS:
        b24, b23 = sli[(stat, 2024, "bigs", "T60")], sli[(stat, 2023, "bigs", "T60")]
        a24 = tab[(stat, 2024, "A1")]
        r1_primary = bool(
            b24["n"] >= BIGS_MIN_N
            and b23["n"] >= BIGS_MIN_N
            and b24["dcrps"] <= FLOOR
            and b24["ci_hi"] < 0.0
            and b24.get("p_bh") is not None
            and b24["p_bh"] < 0.05
            and b23["dcrps"] < 0.0
        )
        no_harm = {
            str(se): bool(
                tab[(stat, se, "A1")]["dcrps"] <= NO_HARM_POINT
                and tab[(stat, se, "A1")]["ci_hi"] <= NO_HARM_HI
            )
            for se in TEST_SEASONS
        }
        r1 = bool(r1_primary and all(no_harm.values()))
        at = {(a["season"], a["scope"]): a for a in atk if a["stat"] == stat}
        ab = at[(2024, "bigs")]
        r2 = bool(ab["A1_minus_A2"]["point"] <= STRUCT_FLOOR)
        bad = {
            s["slice"]: s["dcrps"]
            for s in sl
            if s["stat"] == stat
            and s["season"] == 2024
            and s["snapshot"] == "T60"
            and s["slice"] in GUARD_SLICES
            and s["n"] >= MIN_SLICE_N
            and s.get("dcrps") is not None
            and s["dcrps"] > SLICE_WORSE
        }
        guards = {
            "abs_bias<=0.5": bool(abs(a24["bias"]) <= MAX_BIAS),
            "cov80_in_[.75,.85]": bool(COV_LO <= a24["cov80"] <= COV_HI),
            "pit_q10_within_.02": bool(abs(a24["pit_q10"] - 0.10) <= PIT_TOL),
            "no_slice_worse_than_+0.01": not bad,
        }
        r3 = all(guards.values())
        gain1 = b24["dcrps"]
        attacks = {
            "A1_minus_A3<=-0.003 (bigs 2024)": bool(ab["A1_minus_A3"]["point"] <= ATTACK_FLOOR),
            "A1_gain_smaller_than_A4_gain (bigs 2024)": bool(ab["A4_dcrps"] < gain1),
            "planted_tests_pass": bool(tests_ok),
        }
        r4a = all(attacks.values())
        out[stat] = {
            "rule1_primary_bigs": r1_primary,
            "rule1_no_harm": no_harm,
            "rule1": r1,
            "rule2_structure_beyond_sum": r2,
            "rule3_guards": guards,
            "rule3": r3,
            "slices_worse": bad,
            "rule4_attacks_ex_knockout": attacks,
            "rule4a": r4a,
            "leak_flag_A1_not_below_A4": bool(ab["A4_dcrps"] >= gain1),
            "all_ex_knockout": bool(r1 and r2 and r3 and r4a),
            "knockout_needed": bool(r1 and r2 and r3 and r4a),
            "final_pass": False,
        }
    return out


# --------------------------------------------------------------------------- stages


def frames_for(inp: dict[str, Any], a1: pl.DataFrame, orc: pl.DataFrame) -> dict[str, pl.DataFrame]:
    base = attach_lr(inp["feats"], a1)
    base30 = attach_lr(inp["feats30"], a1)
    return {
        "A0": base,
        "A1": base,
        "A2": base,
        "A3": placebo_frame(base),
        "A4": oracle_frame(base, orc),
        "A0_30": base30,
        "A1_30": base30,
    }


def check_a0_repro(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for stat in STATS:
        a = arms[stat]
        for season in TEST_SEASONS:
            sm = (a["meta"]["season"] == season).to_numpy()
            got = float(crps_from_quantiles(a["qi"][sm].astype(float), a["y"][sm]).mean())
            exact = float(ref[stat][str(season)]["off"]["crps_int"])
            n_ref = int(ref[stat][str(season)]["off"]["n"])
            good = abs(got - exact) <= 1e-6 and int(sm.sum()) == n_ref
            ok &= good
            rec[f"{stat}_{season}"] = {
                "got": got,
                "stored": exact,
                "delta": got - exact,
                "n": int(sm.sum()),
                "n_ref": n_ref,
                "ok": good,
            }
    return {"ok": bool(ok), "detail": rec}


def run_arms(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    stored = (out_dir / "gate.json").read_bytes()
    if hashlib.sha256(stored).hexdigest() != cur.get("gate_sha256"):
        raise RuntimeError("gate.json does not match its recorded sha256")
    if not cur.get("gate_ok"):
        raise RuntimeError("minimum-data gate did not pass: no arm may be fitted")
    sha = frozen_sha256(DOC)
    assert sha.startswith(FROZEN_PREFIX)
    t0 = time.time()
    inp = load_study(db_path)
    a1, orc = build_or_load_lr(inp)
    frames = frames_for(inp, a1, orc)
    cfg = inp["cfg"]
    arms: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STATS}
    for stat in STATS:
        arms[stat]["A0"] = collect_cached(frames["A0"], stat, "A0", cfg, arm_names(stat, "A0"))
    repro = check_a0_repro({s: arms[s]["A0"] for s in STATS})
    print(f"A0 repro ok={repro['ok']} {json.dumps(repro['detail'])}", flush=True)
    cur["a0_repro"] = repro
    _write_json(path, cur)
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    for stat in STATS:
        for arm in (*T60_ARMS[1:], *T30_ARMS):
            arms[stat][arm] = collect_cached(frames[arm], stat, arm, cfg, arm_names(stat, arm))
            print(f"[{time.time() - t0:.0f}s] {stat} {arm} ready", flush=True)
    tables: dict[str, list[dict[str, Any]]] = {"rows": [], "slices": [], "mech": [], "attack": []}
    for stat in STATS:
        t = score_stat(stat, arms[stat])
        for k in tables:
            tables[k] += t[k]
        print(f"[{time.time() - t0:.0f}s] {stat} scored", flush=True)
    add_bh(tables["rows"], tables["slices"])
    tests_ok = all(t["ok"] for t in cur["tests"])
    rules = evaluate_rules(tables, tests_ok)
    cur["arms"] = {
        **tables,
        "rules": rules,
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "frozen_sha256": sha,
        "n_boot": N_BOOT,
        "arms_seconds": time.time() - t0,
    }
    _write_json(path, cur)
    arms_out: dict[str, Any] = cur["arms"]
    return arms_out


def run_knockout(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Red-team knockout arms (doc 'Red-team plan' 3), run only for stats whose other rules pass."""
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    need = [s for s in STATS if cur["arms"]["rules"][s]["knockout_needed"]]
    if not need:
        print("knockout not needed: no stat passes rules 1-4 ex knockout", flush=True)
        return {}
    inp = load_study(db_path, with_t30=False)
    a1, _ = build_or_load_lr(inp)
    base = attach_lr(inp["feats"], a1)
    drops = {
        "K_height": ("lr_h5", "lr_h2"),
        "K_chg": ("lr_chg",),
        "K_groupA": LR_A,
    }
    out: dict[str, Any] = {}
    for stat in need:
        a0 = collect_cached(base, stat, "A0", inp["cfg"], arm_names(stat, "A0"))
        a1c = collect_cached(base, stat, "A1", inp["cfg"], arm_names(stat, "A1"))
        for kname, dropped in drops.items():
            names = stat_feature_names(stat, extra=tuple(c for c in LR_COLS if c not in dropped))
            ko = collect_cached(base, stat, kname, inp["cfg"], names)
            for season in TEST_SEASONS:
                sm = (a0["meta"]["season"] == season).to_numpy()
                d = a0["meta"].filter(pl.Series(sm))
                big = slice_masks(d)["bigs"]
                gid = d["game_id"].to_numpy()
                s0 = row_scores(a0["qi"][sm], a0["y"][sm], stat)["crps"]
                s1 = row_scores(a1c["qi"][sm], a1c["y"][sm], stat)["crps"]
                sk = row_scores(ko["qi"][sm], ko["y"][sm], stat)["crps"]
                g1 = float((s1 - s0)[big].mean())
                gk = float((sk - s0)[big].mean())
                out[f"{stat}_{kname}_{season}"] = {
                    "A1_gain_bigs": g1,
                    "without_gain_bigs": gk,
                    "kept_fraction": gk / g1 if g1 != 0 else None,
                    "ci_without": _ci((sk - s0)[big], gid[big], N_BOOT),
                }
    cur["knockout"] = out
    for stat in need:
        flags = [
            v["kept_fraction"] is not None and v["kept_fraction"] <= 1.0 - KNOCKOUT_RED_FLAG
            for k, v in out.items()
            if k.startswith(f"{stat}_") and k.endswith("_2024")
        ]
        r = cur["arms"]["rules"][stat]
        r["knockout_red_flag"] = bool(any(flags))
        r["final_pass"] = bool(r["all_ex_knockout"] and not any(flags))
    _write_json(path, cur)
    return out


def run_smoke(db_path: str = "nba.duckdb") -> None:
    """One month block, A0 vs A1 (reb), nothing written except the feature cache."""
    t0 = time.time()
    inp = load_study(db_path, with_t30=False)
    print(f"loaded {time.time() - t0:.0f}s", flush=True)
    a1, orc = build_or_load_lr(inp)
    print(f"lr ready {time.time() - t0:.0f}s; cols nan share:", flush=True)
    for c in (*LR_COLS, *LR30):
        v = a1[c].to_numpy()
        print(
            f"  {c}: nan {float(np.isnan(v).mean()):.4f}  mean {float(np.nanmean(v)):.4f}",
            flush=True,
        )
    base = attach_lr(inp["feats"], a1)
    for arm in ("A0", "A1"):
        o = collect_arm(base, "reb", inp["cfg"], arm_names("reb", arm), (2023,), max_blocks=1)
        crps = float(crps_from_quantiles(o["qi"].astype(float), o["y"]).mean())
        print(
            f"{arm} one-block crps_int {crps:.4f} n={len(o['y'])} [{time.time() - t0:.0f}s]",
            flush=True,
        )


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return ""
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    ln = [
        "# F8 lineup rebounding: results (docs/prereg/F8_LINEUP_REBOUNDING.md)",
        "",
        f"Frozen sha256 `{r['frozen_sha256']}` (first 8 `{FROZEN_PREFIX}`, prefix ok: {r['frozen_prefix_ok']}). "
        f"git SHA at run `{r['git_sha']}`. data_version {r['data_version']}. Seeds {r['seeds']}. CPU. "
        f"Seasons <= 2024 only (max loaded {r['max_season_loaded']}); nba.duckdb read-only.",
        "",
        "## Gate and tests `[test, ok]`",
        "",
        "| test | value | threshold | ok |",
        "|---|---|---|---|",
    ]
    for t in r["tests"]:
        v = t["value"]
        ln.append(
            f"| {t['test']} | {v:.6g} | {t['threshold']} | {'ok' if t['ok'] else 'FAIL'} |"
            if isinstance(v, float)
            else f"| {t['test']} | {v} | {t['threshold']} | {'ok' if t['ok'] else 'FAIL'} |"
        )
    ln += [
        "",
        "G1 detail:",
        "",
        "```",
        json.dumps(r["g1"], indent=1),
        "```",
        "",
        "G2:",
        "",
        "```",
        json.dumps(r["g2"], indent=1),
        "```",
        "",
        "G3:",
        "",
        "```",
        json.dumps(r["g3"], indent=1),
        "```",
        "",
    ]
    ln += [
        "Missingness audit `[col, bucket, null_rate]`:",
        "",
        "| col | bucket | null_rate | n |",
        "|---|---|---|---|",
    ]
    ln += [f"| {a['col']} | {a['bucket']} | {a['null_rate']:.5f} | {a['n']} |" for a in r["audit"]]
    arms = r.get("arms")
    if not arms:
        return "\n".join([*ln, "", "Arms not run."])
    ln += ["", "## A0 reproduction", "", "```", json.dumps(r["a0_repro"], indent=1), "```", ""]
    ln += [
        "## Results `[stat, season, arm, snapshot, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, pit_q20, tll]`",
        "",
        "dcrps = arm - A0 (T-30 arms: A1_30 - A0_30), integer support, game-clustered 95% CI, 2000 resamples, seed 0.",
        "",
        "| stat | season | arm | snap | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in arms["rows"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {t['arm']} | {t['snapshot']} | {t['n_rows']} | {t['n_games']} | {_f(t['crps_int'])} | "
            f"{_f(t['dcrps'], 4, True)} | {_f(t['ci_lo'], 4, True)} | {_f(t['ci_hi'], 4, True)} | {_f(t['p'], 3)} | {_f(t['p_bh'], 3)} | "
            f"{_f(t['mde'])} | {_f(t['bias'], 3, True)} | {_f(t['cov80'], 3)} | {_f(t['pit_q10'], 3)} | {_f(t['pit_q20'], 3)} | {_f(t['tll'])} |"
        )
    ln += [
        "",
        "## Slices `[stat, season, slice, n, dcrps, ci_lo, ci_hi]` (A1 - A0; n >= 300 enforced)",
        "",
        "| stat | season | snap | slice | n | dcrps | ci_lo | ci_hi | p | p_bh |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in arms["slices"]:
        ln.append(
            f"| {s['stat']} | {s['season']} | {s['snapshot']} | {s['slice']} | {s['n']} | {_f(s.get('dcrps'), 4, True)} | {_f(s.get('ci_lo'), 4, True)} | {_f(s.get('ci_hi'), 4, True)} | {_f(s.get('p'), 3)} | {_f(s.get('p_bh'), 3)} |"
        )
    ln += [
        "",
        "## Mechanism (descriptive)",
        "",
        "| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi | bias_A0 | bias_A1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in arms["mech"]:
        ln.append(
            f"| {m['stat']} | {m['season']} | {m['by']} | {m['tercile']} | {m['n']} | {_f(m.get('dcrps'), 4, True)} | {_f(m.get('ci_lo'), 4, True)} | {_f(m.get('ci_hi'), 4, True)} | {_f(m.get('bias_A0'), 3, True)} | {_f(m.get('bias_A1'), 3, True)} |"
        )

    def cell(d: dict[str, float]) -> str:
        return f"{d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]"

    ln += [
        "",
        "## Attack inputs (A1 minus other; arm gains vs A0)",
        "",
        "| stat | season | scope | A1-A2 | A1-A3 | A1-A4 | A2 gain | A3 gain | A4 gain |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in arms["attack"]:
        ln.append(
            f"| {a['stat']} | {a['season']} | {a['scope']} | {cell(a['A1_minus_A2'])} | {cell(a['A1_minus_A3'])} | {cell(a['A1_minus_A4'])} | {a['A2_dcrps']:+.4f} | {a['A3_dcrps']:+.4f} | {a['A4_dcrps']:+.4f} |"
        )
    ln += ["", "## Rules", "", "```", json.dumps(arms["rules"], indent=1), "```", ""]
    if "knockout" in r:
        ln += ["## Knockout", "", "```", json.dumps(r["knockout"], indent=1), "```", ""]
    ln += ["## Unstated details, chosen before scoring", ""] + [f"* {u}" for u in UNSTATED]
    return "\n".join(ln)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="F8 lineup rebounding (frozen rule)")
    ap.add_argument("stage", choices=("gate", "arms", "knockout", "report", "smoke"))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "smoke":
        run_smoke(a.db_path)
    elif a.stage == "gate":
        wait_for_replay()
        with heavy_lock():
            res = run_gate(a.db_path)
        print(
            json.dumps(
                {k: res[k] for k in ("frozen_sha256", "tests", "gate_ok")}, indent=1, default=_jd
            )
        )
    elif a.stage == "arms":
        wait_for_replay()
        with heavy_lock():
            res = run_arms(a.db_path)
        print(json.dumps(res["rules"], indent=1))
    elif a.stage == "knockout":
        wait_for_replay()
        with heavy_lock():
            run_knockout(a.db_path)
    else:
        REPORT_MD.write_text(render_report())
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
