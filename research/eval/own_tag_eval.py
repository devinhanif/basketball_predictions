# ruff: noqa: E501
"""OWN_TAG harness (rule: docs/prereg/OWN_TAG.md; DRAFT until frozen).

    M="-m research.eval.own_tag_eval"
    .venv/bin/python -W ignore $M gate  --allow-draft --doc docs/prereg/OWN_TAG.md   # checks 1-4, no arm fitted
    .venv/bin/python -W ignore $M smoke --doc docs/prereg/OWN_TAG.md --sha <64 hex>  # one month block, A0 vs A1 (pts)
    .venv/bin/python -W ignore $M run   --doc docs/prereg/OWN_TAG.md --sha <64 hex>  # gate -> A0-A3 x 4 stats -> results.json
    .venv/bin/python -W ignore $M report --doc docs/prereg/OWN_TAG.md --sha <64 hex> # reports/prereg_own_tag.md

``--sha`` must equal the sha256 of everything above the ``=== RESULTS BELOW ===`` line; ``--allow-draft``
skips that check and is for the gate stage only. Research module (``nba/`` untouched): the two columns
go through ``stat_feature_names(stat, extra=...)``. ``nba.duckdb`` is opened read-only, every SQL
carries ``season <= 2024`` and the frames are asserted; season 2025 is never loaded. Heavy stages take
``data/ops/heavy.lock``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import attach_realised
from nba.features.game_tipoff import build_game_tipoff, tip_coverage
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
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
from research.eval.f11_lineup_context import _bucket_minutes, _jd, git_sha
from research.eval.four_factors_eval import (
    COV_HI,
    COV_LO,
    FLOOR,
    MAX_BIAS,
    MIN_SLICE_N,
    N_BOOT,
    SLICE_WORSE,
    _chk,
    _delta_stats,
    row_scores,
)
from research.eval.four_factors_eval import (
    collect_arm as _unused,  # noqa: F401  (pattern reference)
)
from research.eval.minutes_hazard_eval import HeavyLock, frozen_sha
from research.features.own_tag import OWN_TAG_COLS, TAG_CODES, build_own_tag, placebo_own_tag

DOC = Path("docs/prereg/OWN_TAG.md")
OUT_DIR = Path("reports/prereg_own_tag")
REPORT_MD = Path("reports/prereg_own_tag.md")
CACHE_DIR = Path("data/ops/own_tag_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
STATS: tuple[str, ...] = PROP_STATS
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3")
KEYS = ["game_id", "player_id"]
LEVELS = {0: "none", 1: "available", 2: "probable", 3: "questionable", 4: "doubtful", 5: "out"}

COVERAGE_MIN = 0.95
NONE_SHARE_MIN = 0.03
NULL_GAP_PP = 2.0
ATTACK_FLOOR = -0.003
AUDIT_SEASONS = (2023, 2024)

UNSTATED: list[str] = [
    "snapshot = the game's latest usable report (usable_report_rows, tip_source='real', proxy only where the real tip is missing, as production); only rows of THAT snapshot count, so a player in an earlier snapshot but absent from the latest has own_tag 0",
    "a player listed twice in the snapshot takes the highest code",
    "an OUT line is coded 5 (the draft lists 0-4; OUT rows are 'already excluded' only if the player did not play); the gate counts played rows with code 5 and reports it as an open item",
    "a game with no usable snapshot gets own_tag 0 and own_tag_hours 48 (capped value), never NULL; `has_report` already carries the fact; the missingness audit measures exactly this imputed share by realised-minutes bucket",
    "roster = every player_game_stats row of season <= 2024 (DNP included); the tag frame is built on all of it, then joined to the played feature frame (a join miss would show as NULL and is counted)",
    "check 1 denominator = regular-season ('002') games with a result per season; also reported: all games, real-tip coverage and proxy-fallback count",
    "check 2 = share of non-none own_tag among stat-frame rows (n_prior >= 5, the scored universe) per season, min over 2023/2024 must be >= 3%; counts per level for played rows and scored rows are reported",
    "check 3 gap = max-min over the five realised-minutes buckets (DNP, <5, 5-10, 10-20, >20; DNP included, stricter) of the imputed/NULL rate, over both columns, seasons 2023-2024 roster rows; the played-only gap is reported too; the non-none share by bucket is reported as description (that variation is the information, not missingness)",
    "planted same-game: an extra all-roster 'questionable' snapshot stamped tip - 30 min is injected on one day's games and their stored minutes are altered; tags must be byte-identical. Non-inert control: the same snapshot at tip - 90 min must change them",
    "planted future: every report row of games dated on/after 2024-01-15 gets its status rotated and its stamp shifted; tags of earlier games must be identical, later games must change",
    "check 4 (frozen wording: the stored reference's key set) compares A0 with reports/lower_tail/candidates.json 'off' AS IT STANDS: that reference was re-baselined on the current DB on 2026-10-11 15:11 CT after the data clean-up, so its key set is the full current one (2024 n 27,701); all 4 stats x 2 seasons, CRPS to 1e-6, n identical",
    "placebo A2 permutes own_tag within (team_id, game_date) among the scored feature rows with numpy default_rng(0); own_tag_hours is kept with its row",
    "'stat passes' = dCRPS <= -0.005, CI upper < 0, BH p < 0.05 (4 stats per season, A1) in BOTH 2023 and 2024; guards and the A1-vs-A2 gate are read on 2024 all rows; no stat passing 2023 closes the question",
    "guard slices (n >= 300, 2024, A1 - A0 > +0.01 fails): starter, bench, first15, tag none / probable / questionable",
]


# --------------------------------------------------------------------------- freeze


def require_frozen(doc: Path, expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(doc)
    if allow_draft:
        return sha
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


# --------------------------------------------------------------------------- inputs


def load_study(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    tips = build_game_tipoff(max_season=MAX_SEASON)  # holdout: never read 2025-26 schedule files
    tag = build_own_tag(pgs, games, avail, cfg.report, tips)
    return {
        "games": games,
        "pgs": pgs,
        "avail": avail,
        "feats": feats,
        "cfg": cfg,
        "flagged": flagged,
        "tips": tips,
        "tag": tag,
    }


def attach_tag(feats: pl.DataFrame, tag: pl.DataFrame) -> pl.DataFrame:
    out = feats.join(tag.select([*KEYS, *OWN_TAG_COLS]), on=KEYS, how="left")
    assert out.height == feats.height, "own_tag join changed the row count"
    return out


def arm_names(stat: str, arm: str) -> list[str]:
    extra: dict[str, tuple[str, ...]] = {
        "A0": (),
        "A1": OWN_TAG_COLS,
        "A2": OWN_TAG_COLS,
        "A3": ("own_tag",),
    }
    return stat_feature_names(stat, extra=extra[arm])


def arm_frames(base: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Identical keys and row order in every arm; only the own_tag column differs in A2."""
    return {
        "A0": base,
        "A1": base,
        "A2": placebo_own_tag(base, SEEDS["placebo"]),
        "A3": base,
    }


# --------------------------------------------------------------------------- gate


def tag_equal(a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str]) -> bool:
    ids = list(game_ids)
    x = a.filter(pl.col("game_id").is_in(ids)).sort(KEYS)
    y = b.filter(pl.col("game_id").is_in(ids)).sort(KEYS)
    if x.height != y.height or not x.select(KEYS).equals(y.select(KEYS)):
        return False
    return all(np.array_equal(x[c].to_numpy(), y[c].to_numpy()) for c in OWN_TAG_COLS)


def check_1_coverage(
    games: pl.DataFrame, flagged: dict[str, set[int]], tag: pl.DataFrame, tips: pl.DataFrame
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reg = games.filter(pl.col("game_id").str.starts_with("002") & pl.col("home_pts").is_not_null())
    snap_games = set(tag.filter(pl.col("_has_snap") == 1)["game_id"].unique().to_list())
    parity = snap_games == set(flagged)
    out: dict[str, Any] = {"parity_with_production_flagged": parity}
    tests: list[dict[str, Any]] = []
    for s in (2022, 2023, 2024):
        r = reg.filter(pl.col("season") == s)
        a = games.filter(pl.col("season") == s)
        v = float(r["game_id"].is_in(list(snap_games)).mean())  # type: ignore[arg-type]
        va = float(a["game_id"].is_in(list(snap_games)).mean())  # type: ignore[arg-type]
        tc = tip_coverage(a, tips)
        out[str(s)] = {
            "regular_season_games": r.height,
            "coverage_regular": v,
            "coverage_all_games": va,
            "n_games": a.height,
            "real_tip": tc["with_real_tip"],
            "proxy_fallback": tc["proxy_fallback"],
        }
        if s in AUDIT_SEASONS:
            tests.append(
                _chk(
                    f"1 snapshot coverage {s} (regular season)",
                    v,
                    f">= {COVERAGE_MIN}",
                    v >= COVERAGE_MIN,
                )
            )
    tests.append(_chk("1 snapshot games == production flagged games", int(parity), "== 1", parity))
    return tests, out


def check_2_share(feats_t: pl.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: dict[str, Any] = {}
    shares: list[float] = []
    for s in AUDIT_SEASONS:
        for name, f in (
            ("played", feats_t.filter(pl.col("season") == s)),
            ("scored", feats_t.filter((pl.col("season") == s) & (pl.col("n_prior") >= 5))),
        ):
            counts = {LEVELS[int(k)]: int(v) for k, v in f.group_by("own_tag").len().iter_rows()}
            n = f.height
            share = 1.0 - counts.get("none", 0) / n
            out[f"{s}_{name}"] = {"n": n, "counts": counts, "non_none_share": share}
            if name == "scored":
                shares.append(share)
    n_out = int(feats_t.filter(pl.col("own_tag") == 5).height)
    out["played_rows_tagged_out"] = n_out
    ok = min(shares) >= NONE_SHARE_MIN
    return [
        _chk(
            "2 non-none tag share, scored rows, min over seasons",
            min(shares),
            f">= {NONE_SHARE_MIN}",
            ok,
        )
    ], out


def check_3_missingness(
    tag: pl.DataFrame, pgs: pl.DataFrame, games: pl.DataFrame, feats_t: pl.DataFrame
) -> tuple[float, float, list[dict[str, Any]], dict[str, Any]]:
    d = (
        tag.join(pgs.select([*KEYS, "minutes"]), on=KEYS, how="left")
        .join(games.select(["game_id", "season"]), on="game_id", how="left")
        .filter(pl.col("season").is_in(list(AUDIT_SEASONS)))
        .with_columns(_bucket_minutes(pl.col("minutes")).alias("_b"))
    )
    audit: list[dict[str, Any]] = []
    gaps: dict[str, float] = {}
    for c in OWN_TAG_COLS:
        rates: dict[str, float] = {}
        for b in ("DNP", "<5", "5-10", "10-20", ">20"):
            sub = d.filter(pl.col("_b") == b)
            imputed = float(((sub["_has_snap"] == 0) | sub[c].is_null() | sub[c].is_nan()).mean())  # type: ignore[arg-type]
            nonnone = float((sub["own_tag"] > 0).mean())  # type: ignore[arg-type]
            rates[b] = imputed
            audit.append(
                {
                    "col": c,
                    "bucket": b,
                    "n": sub.height,
                    "imputed_or_null_rate": imputed,
                    "non_none_share": nonnone,
                }
            )
        gaps[c] = (max(rates.values()) - min(rates.values())) * 100.0
        gaps[f"{c}_played"] = (
            max(v for k, v in rates.items() if k != "DNP")
            - min(v for k, v in rates.items() if k != "DNP")
        ) * 100.0
    join_nulls = int(sum(feats_t[c].is_null().sum() for c in OWN_TAG_COLS))
    gap_all = max(gaps[c] for c in OWN_TAG_COLS)
    gap_played = max(gaps[f"{c}_played"] for c in OWN_TAG_COLS)
    extra = {"gaps_pp": gaps, "join_nulls_on_feature_frame": join_nulls}
    return gap_all, gap_played, audit, extra


def _with_extra_snapshot(
    avail: pl.DataFrame, pgs: pl.DataFrame, tips: pl.DataFrame, gids: set[str], minutes_before: int
) -> pl.DataFrame:
    """Add one all-roster 'questionable' snapshot per game, stamped ``minutes_before`` before tip."""
    ids = list(gids)
    r = (
        pgs.filter(pl.col("game_id").is_in(ids))
        .select(KEYS)
        .join(tips.select(["game_id", "tip_et"]), on="game_id")
        .with_columns(
            pl.lit("questionable").alias("status"),
            (pl.col("tip_et") - pl.duration(minutes=minutes_before)).alias("as_of"),
            pl.lit("nba_official_report").alias("source"),
        )
        .select(["game_id", "player_id", "status", "as_of", "source"])
    )
    return pl.concat(
        [
            avail.select(["game_id", "player_id", "status", "as_of", "source"]),
            r.cast(avail.schema, strict=False),
        ]
    )


def planted_tests(inp: dict[str, Any]) -> list[dict[str, Any]]:
    games, pgs, avail, tips, cfg, tag = (
        inp["games"],
        inp["pgs"],
        inp["avail"],
        inp["tips"],
        inp["cfg"],
        inp["tag"],
    )
    reg24 = games.filter(
        (pl.col("season") == 2024) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list()) & set(
        tips["game_id"].to_list()
    )
    # same-game: post-cutoff snapshot + altered stored minutes
    pgs2 = pgs.with_columns(
        pl.when(pl.col("game_id").is_in(list(targets)))
        .then(pl.col("minutes").fill_null(0.0) + 7.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    late = _with_extra_snapshot(avail, pgs, tips, targets, 30)
    t_late = build_own_tag(pgs2, games, late, cfg.report, tips)
    same_ok = tag_equal(tag, t_late, targets)
    early = _with_extra_snapshot(avail, pgs, tips, targets, 90)
    t_early = build_own_tag(pgs2, games, early, cfg.report, tips)
    moves = not tag_equal(tag, t_early, targets)
    # future: rotate statuses and shift stamps of every report row on/after the cutoff date
    cutoff = dt.date(2024, 1, 15)
    fut_ids = list(games.filter(pl.col("game_date") >= cutoff)["game_id"].to_list())
    order = list(TAG_CODES)
    rot = {a: order[(i + 1) % len(order)] for i, a in enumerate(order)}
    av2 = avail.with_columns(
        pl.when(pl.col("game_id").is_in(fut_ids))
        .then(pl.col("status").str.to_lowercase().replace_strict(rot, default="out"))
        .otherwise(pl.col("status"))
        .alias("status"),
        pl.when(pl.col("game_id").is_in(fut_ids))
        .then(pl.col("as_of") - pl.duration(minutes=45))
        .otherwise(pl.col("as_of"))
        .alias("as_of"),
    )
    t_fut = build_own_tag(pgs, games, av2, cfg.report, tips)
    before = set(games.filter(pl.col("game_date") < cutoff)["game_id"].to_list())
    fut_ok = tag_equal(tag, t_fut, before)
    fut_moves = not tag_equal(tag, t_fut, set(fut_ids))
    # the gate rule itself: a snapshot after tip - 60 never enters (direct assertion on usable rows)
    probe = _with_extra_snapshot(avail, pgs, tips, targets, 59)
    t_probe = build_own_tag(pgs2, games, probe, cfg.report, tips)
    probe_ok = tag_equal(tag, t_probe, targets)
    return [
        _chk(
            "3 PLANT same-game: post-cutoff snapshot (tip-30) + altered minutes leave tags identical",
            int(same_ok),
            "== 1",
            same_ok,
        ),
        _chk("3 PLANT same-game: tip-59 snapshot is refused", int(probe_ok), "== 1", probe_ok),
        _chk(
            "3 PLANT same-game is not inert (tip-90 snapshot changes tags)",
            int(moves),
            "== 1",
            moves,
        ),
        _chk(
            "3 PLANT future (>= 2024-01-15) report edits: earlier tags unchanged",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk("3 PLANT future is not inert (later tags change)", int(fut_moves), "== 1", fut_moves),
    ]


# --------------------------------------------------------------------------- walk-forward

META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "game_date",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    *OWN_TAG_COLS,
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


def collect_cached(
    frame: pl.DataFrame, stat: str, arm: str, cfg: ContextResidualConfig
) -> dict[str, Any]:
    p = CACHE_DIR / f"arm_{stat}_{arm}.npz"
    if p.exists():
        z = np.load(p)
        return {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    t0 = time.time()
    out = collect_arm(frame, stat, cfg, arm_names(stat, arm))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=out["qi"], y=out["y"])
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {arm} done in {time.time() - t0:.0f}s", flush=True)
    return out


def check_a0_repro(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A0 vs ``reports/lower_tail/candidates.json`` 'off' (re-baselined on the current DB on
    2026-10-11), all 4 stats x 2 seasons, CRPS to 1e-6 and n identical."""
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for stat, a in arms.items():
        for season in TEST_SEASONS:
            sm = (a["meta"]["season"] == season).to_numpy()
            got = float(crps_from_quantiles(a["qi"][sm].astype(float), a["y"][sm]).mean())
            stored = float(ref[stat][str(season)]["off"]["crps_int"])
            n_ref = int(ref[stat][str(season)]["off"]["n"])
            good = abs(got - stored) <= 1e-6 and int(sm.sum()) == n_ref
            ok &= good
            rec[f"{stat}_{season}"] = {
                "got": got,
                "stored": stored,
                "delta": got - stored,
                "n": int(sm.sum()),
                "n_ref": n_ref,
                "ok": good,
            }
    return {"ok": bool(ok), "detail": rec}


def run_gate(
    db_path: str, doc: Path, sha: str, allow_draft: bool, out_dir: Path = OUT_DIR
) -> dict[str, Any]:
    t0 = time.time()
    inp = load_study(db_path)
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    games, pgs, feats, tag, tips = inp["games"], inp["pgs"], inp["feats"], inp["tag"], inp["tips"]
    feats_t = attach_tag(feats, tag)
    tests: list[dict[str, Any]] = []
    t1, cov = check_1_coverage(games, inp["flagged"], tag, tips)
    tests += t1
    t2, share = check_2_share(feats_t)
    tests += t2
    gap_all, gap_played, audit, miss_extra = check_3_missingness(tag, pgs, games, feats_t)
    tests.append(
        _chk(
            "3 max imputed/NULL-rate gap across realised-minutes buckets incl. DNP (pp), both columns",
            gap_all,
            f"<= {NULL_GAP_PP}",
            gap_all <= NULL_GAP_PP,
        )
    )
    tests.append(
        _chk(
            "3 join NULLs on the played feature frame",
            miss_extra["join_nulls_on_feature_frame"],
            "== 0",
            miss_extra["join_nulls_on_feature_frame"] == 0,
        )
    )
    tests += planted_tests(inp)
    print(f"checks 1-3 done {time.time() - t0:.0f}s", flush=True)
    base = feats_t
    arms: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        arms[stat] = collect_cached(base, stat, "A0", inp["cfg"])
    repro = check_a0_repro(arms)
    tests.append(
        _chk(
            "4 A0 reproduces production integer CRPS and n to 1e-6 (4 stats x 2 seasons)",
            int(repro["ok"]),
            "== 1",
            repro["ok"],
        )
    )
    names_ok = all(arm_names(s, "A0") == stat_feature_names(s) for s in STATS)
    tests.append(
        _chk("4 A0 names == production stat_feature_names", int(names_ok), "== 1", names_ok)
    )
    res: dict[str, Any] = {
        "status": "GATE_PASSED" if all(t["ok"] for t in tests) else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "doc": str(doc),
        "sha256": sha,
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "coverage": cov,
        "share": share,
        "missingness_audit": audit,
        "missingness_extra": miss_extra,
        "a0_repro": repro,
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "max_season_loaded": int(feats["season"].max()),
        "unstated_details": UNSTATED,
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1, default=_jd, sort_keys=True))
    return res


# --------------------------------------------------------------------------- scoring


def tag_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    t = d["own_tag"].to_numpy()
    return {f"tag:{LEVELS[k]}": t == k for k in (0, 1, 2, 3, 4)}


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    dd = add_slice_columns(d)
    return {
        "all": np.ones(d.height, dtype=bool),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        **tag_masks(d),
    }


GUARD_SLICES = ("starter", "bench", "first15", "tag:none", "tag:probable", "tag:questionable")


def score_stat(stat: str, arms: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ref = arms["A0"]["meta"]
    for a, v in arms.items():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
            f"{a}: arms must score identical rows"
        )
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    attack: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: row_scores(v["qi"][sm], v["y"][sm], stat) for a, v in arms.items()}
        masks = slice_masks(d)
        for a in arms:
            s = sc[a]
            r: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": a,
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
            if a != "A0":
                r.update(_delta_stats(s["crps"] - sc["A0"]["crps"], gid))
            rows.append(r)
        for a in ("A1", "A2", "A3"):
            da = sc[a]["crps"] - sc["A0"]["crps"]
            for name, m in masks.items():
                n = int(m.sum())
                rec: dict[str, Any] = {
                    "stat": stat,
                    "season": season,
                    "arm": a,
                    "slice": name,
                    "n": n,
                }
                if n >= MIN_SLICE_N or name == "all":
                    ds = _delta_stats(da[m], gid[m])
                    rec.update({k: ds[k] for k in ("dcrps", "ci_lo", "ci_hi")})
                sl.append(rec)
        ent: dict[str, Any] = {"stat": stat, "season": season}
        d12 = sc["A1"]["crps"] - sc["A2"]["crps"]
        ent["A1_minus_A2"] = {
            k: v for k, v in _delta_stats(d12, gid).items() if k in ("dcrps", "ci_lo", "ci_hi")
        }
        ent["A1_minus_A3"] = {
            k: v
            for k, v in _delta_stats(sc["A1"]["crps"] - sc["A3"]["crps"], gid).items()
            if k in ("dcrps", "ci_lo", "ci_hi")
        }
        attack.append(ent)
    return {"rows": rows, "slices": sl, "attack": attack}


def add_bh(rows: list[dict[str, Any]]) -> None:
    for season in TEST_SEASONS:
        for arm in ("A1", "A2", "A3"):
            tgt = [r for r in rows if r["season"] == season and r["arm"] == arm]
            adj = bh_adjust(np.array([r["p"] for r in tgt]))
            for r, a in zip(tgt, adj, strict=True):
                r["p_bh"] = float(a)


def evaluate_rules(res: dict[str, list[dict[str, Any]]], tests_ok: bool) -> dict[str, Any]:
    rows, sl, atk = res["rows"], res["slices"], res["attack"]
    tab = {(r["stat"], r["season"], r["arm"]): r for r in rows}
    out: dict[str, Any] = {}
    for stat in STATS:
        at = {a["season"]: a for a in atk if a["stat"] == stat}
        r1 = {
            str(se): bool(
                tab[(stat, se, "A1")]["dcrps"] <= FLOOR
                and tab[(stat, se, "A1")]["ci_hi"] < 0.0
                and tab[(stat, se, "A1")]["p_bh"] < 0.05
            )
            for se in TEST_SEASONS
        }
        t24 = tab[(stat, 2024, "A1")]
        bad = {
            s["slice"]: s["dcrps"]
            for s in sl
            if s["stat"] == stat
            and s["season"] == 2024
            and s["arm"] == "A1"
            and s["slice"] in GUARD_SLICES
            and s["n"] >= MIN_SLICE_N
            and s.get("dcrps") is not None
            and s["dcrps"] > SLICE_WORSE
        }
        guards = {
            "abs_bias<=0.5": bool(abs(t24["bias"]) <= MAX_BIAS),
            "cov80_in_[.75,.85]": bool(COV_LO <= t24["cov80"] <= COV_HI),
            "no_slice_worse_than_+0.01": not bad,
        }
        beats = bool(at[2024]["A1_minus_A2"]["dcrps"] <= ATTACK_FLOOR)
        out[stat] = {
            "rule1_by_season": r1,
            "rule1": all(r1.values()),
            "rule2_guards": guards,
            "rule2": all(guards.values()),
            "slices_worse": bad,
            "A1_beats_A2_by_0.003_2024": beats,
            "A1_minus_A2_2024": at[2024]["A1_minus_A2"]["dcrps"],
            "pass": bool(all(r1.values()) and all(guards.values()) and beats and tests_ok),
            "pass_2023": bool(r1["2023"]),
        }
    out["close_no_stat_passes_2023"] = not any(out[s]["pass_2023"] for s in STATS)
    return out


def run_arms(db_path: str, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    path = out_dir / "results.json"
    gate = json.loads((out_dir / "gate.json").read_text())
    if gate.get("draft") or not gate.get("gate_ok") or gate.get("sha256") != sha:
        raise RuntimeError("no passing gate recorded for this frozen sha: no arm may be fitted")
    t0 = time.time()
    inp = load_study(db_path)
    base = attach_tag(inp["feats"], inp["tag"])
    frames = arm_frames(base)
    arms: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STATS}
    for stat in STATS:
        for arm in ARMS:
            arms[stat][arm] = collect_cached(frames[arm], stat, arm, inp["cfg"])
            print(f"[{time.time() - t0:.0f}s] {stat} {arm} ready", flush=True)
    repro = check_a0_repro({s: arms[s]["A0"] for s in STATS})
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    tables: dict[str, list[dict[str, Any]]] = {"rows": [], "slices": [], "attack": []}
    for stat in STATS:
        t = score_stat(stat, arms[stat])
        for k in tables:
            tables[k] += t[k]
        print(f"[{time.time() - t0:.0f}s] {stat} scored", flush=True)
    add_bh(tables["rows"])
    rules = evaluate_rules(tables, True)
    res = {
        **tables,
        "rules": rules,
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "sha256": sha,
        "n_boot": N_BOOT,
        "a0_repro": repro,
        "arms_seconds": time.time() - t0,
    }
    path.write_text(json.dumps(res, indent=1, default=_jd))
    return res


def run_smoke(db_path: str) -> None:
    t0 = time.time()
    inp = load_study(db_path)
    base = attach_tag(inp["feats"], inp["tag"])
    frames = arm_frames(base)
    for arm in ("A0", "A1"):
        o = collect_arm(
            frames[arm], "pts", inp["cfg"], arm_names("pts", arm), (2023,), max_blocks=1
        )
        crps = float(crps_from_quantiles(o["qi"].astype(float), o["y"]).mean())
        print(
            f"{arm} one-block crps_int {crps:.4f} n={len(o['y'])} [{time.time() - t0:.0f}s]",
            flush=True,
        )


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    L = [
        f"# OWN_TAG results (sha256 `{r['sha256']}`, git `{r['git_sha']}`)",
        "",
        "| stat | season | arm | n | games | CRPS | dCRPS | CI | p_bh | bias | cov80 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in r["rows"]:
        L.append(
            f"| {x['stat']} | {x['season']} | {x['arm']} | {x['n_rows']} | {x['n_games']} | {_f(x['crps_int'])} | {_f(x['dcrps'], 4, True)} | [{_f(x['ci_lo'], 4, True)}, {_f(x['ci_hi'], 4, True)}] | {_f(x['p_bh'], 3)} | {_f(x['bias'], 3, True)} | {_f(x['cov80'], 3)} |"
        )
    L += [
        "",
        "## Slices (A1 - A0)",
        "",
        "| stat | season | slice | n | dCRPS | CI |",
        "|---|---|---|---|---|---|",
    ]
    for x in r["slices"]:
        if x["arm"] == "A1":
            L.append(
                f"| {x['stat']} | {x['season']} | {x['slice']} | {x['n']} | {_f(x.get('dcrps'), 4, True)} | [{_f(x.get('ci_lo'), 4, True)}, {_f(x.get('ci_hi'), 4, True)}] |"
            )
    L += ["", "## Rules", "", "```", json.dumps(r["rules"], indent=1), "```", ""]
    return "\n".join(L)


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    tab = {(x["stat"], x["season"], x["arm"]): x for x in r["rows"]}
    body = ["| id | test | result |", "|---|---|---|"]
    for stat in STATS:
        for se in TEST_SEASONS:
            x, p = tab[(stat, se, "A1")], tab[(stat, se, "A2")]
            body.append(
                f"| T### | OWN_TAG {stat} {se} A1-A0 (n {x['n_rows']}, {x['n_games']} games) | "
                f"dCRPS {_f(x['dcrps'], 4, True)} CI [{_f(x['ci_lo'], 4, True)}, {_f(x['ci_hi'], 4, True)}] "
                f"p_bh {_f(x['p_bh'], 3)}; placebo A2 {_f(p['dcrps'], 4, True)}; pass 2023 "
                f"{r['rules'][stat]['pass_2023']} |"
            )
    for stat in STATS:
        v = r["rules"][stat]
        body.append(
            f"| T### | OWN_TAG {stat} verdict | pass {v['pass']}; rule1 {v['rule1']}; guards {v['rule2']}; A1 beats A2 by 0.003: {v['A1_beats_A2_by_0.003_2024']} |"
        )
    body.append(
        f"| T### | OWN_TAG close (no stat passes 2023) | {r['rules']['close_no_stat_passes_2023']} |"
    )
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--db", default="nba.duckdb")
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.doc, a.sha, a.allow_draft)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "gate":
        res = run_gate(a.db, a.doc, sha, a.allow_draft)
        print(
            json.dumps({k: res[k] for k in ("sha256", "tests", "gate_ok")}, indent=1, default=_jd)
        )
        return 0 if res["gate_ok"] else 1
    with HeavyLock():
        if a.stage == "smoke":
            run_smoke(a.db)
        elif a.stage == "run":
            g = run_gate(a.db, a.doc, sha, False)
            if not g["gate_ok"]:
                print("GATE FAILED: stop, no model result", flush=True)
                return 2
            res = run_arms(a.db, sha)
            REPORT_MD.write_text(render_report())
            write_ledger()
            print(json.dumps(res["rules"], indent=1))
        else:
            REPORT_MD.write_text(render_report())
            write_ledger()
            print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
