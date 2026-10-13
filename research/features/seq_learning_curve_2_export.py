# ruff: noqa: E501
"""Sequence exports for SEQ_LEARNING_CURVE_2 (docs/prereg/SEQ_LEARNING_CURVE_2.md; DRAFT until the main session commits the freeze).

Reuses the parent's exporter (``research.features.seq_learning_curve_export``: normalizer formula, era channels, segment loader, S2 / S3 preparation, writer, run-time era gate,
manifest hash) and changes only what the rule changes:

* **S1 is labels 2013-2018** (the parent: 2015-2018): played rows only, no report snapshot (``has_report = 0``), no DNP rows, and **position is REAL for every row** (rule, "Position
  channels", gate D4 passed: DB ``players_static`` + the fetch cache + the hand-sourced sidecar ``players_static_position_source``), not zero.
* **Normalizer constants** (D7, written in the rule): 2013-14 and 2014-15 season means added; the re-measured 2015-16 / 2016-17 points means; Lprev for 2013-14 = L_ref, 2014-15 = the
  2013-14 mean, **2015-16 = the 2014-15 mean** (the parent used L_ref).
* NORM variant only (``seq_learning_curve_2_norm_{S1,S2,S3}.npz``); the job reads these three.
* A run-time D4 guard: every exported season label <= 2021 must have <= 1% of target rows without a position channel (the rule's per-season threshold); else the build refuses.

S2 and S3 go through the same code with the new constants; for labels >= 2018 the constants equal the parent's (a test pins it), so those two exports are the parent's NORM exports with
the filled positions. Exports are capped at season 2023 in code and in SQL; 2024 and 2025 are never loaded.

    uv run python -m research.features.seq_learning_curve_2_export build [--out data/colab/seq_learning_curve_2] [--only S3]   # heavy: take data/ops/heavy.lock
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import polars as pl

from research.eval import seq_learning_curve_2_rule as RULE
from research.eval.pplay_minutes_valid_rule import file_sha256
from research.features import player_sequences as P
from research.features import seq_ensemble_export as X
from research.features import seq_full_mix_rt_export as RT
from research.features import seq_learning_curve_export as X1

CAP_SEASON = X1.CAP_SEASON  # the select season; 2024 and 2025 are never exported
SEGMENTS: dict[str, tuple[int, int]] = {"S1": (2013, 2018), "S2": (2019, 2021), "S3": (2022, 2023)}
SEG_DB = X1.SEG_DB
OUT_DIR = RULE.ROOT / "data/colab/seq_learning_curve_2"
S1_LABELS = tuple(range(2013, 2019))
REPORT_DROPPED_LABELS = X1.REPORT_DROPPED_LABELS
TEST_LABEL = X1.TEST_LABEL
POSITION_MAX_UNKNOWN_SHARE = 0.01  # gate D4, per season label <= 2021
POSITION_GUARD_LAST_LABEL = 2021
SIDECAR = "players_static_position_source"

# --- the frozen normalizer constants (rule table "Windows, segments, arms"; 3 decimals as printed) --------------------------------------------------------------------
REF = X1.REF
SEASON_MEAN: dict[int, tuple[float, float, float, float]] = {  # games.season label -> full-season mean per team-game (pts, reb, ast, fg3m)
    2013: (100.910, 42.639, 21.856, 7.763),
    2014: (100.047, 43.401, 22.016, 7.904),
    2015: (102.486, 43.707, 22.132, 8.558),  # pts re-measured after the free-throw fix (parent 102.489)
    2016: (105.618, 43.409, 22.598, 9.705),  # pts re-measured (parent 105.621)
    **{s: v for s, v in X1.SEASON_MEAN.items() if s >= 2017},
}  # fmt: skip
PRIOR_N = X1.PRIOR_N
#: Lprev for season s = the completed season s-1's mean; 2013-14 has no prior season in the data and uses L_ref (rule: "as 2015-16 was in the parent").
LPREV: dict[int, dict[str, float]] = {
    s: dict(zip(P.STATS, (SEASON_MEAN[s - 1] if s - 1 in SEASON_MEAN else tuple(REF.values())), strict=True))
    for s in range(2013, CAP_SEASON + 1)
}  # fmt: skip
NORM_CTX_FEATURES = X1.NORM_CTX_FEATURES
STRUCT_KEYS = X1.STRUCT_KEYS


def assert_cap(season_max: int) -> None:
    if int(season_max) > CAP_SEASON:
        raise RuntimeError(
            f"season {int(season_max)} > {CAP_SEASON}: the SEQ_LEARNING_CURVE_2 exports are capped at the select season"
        )


def stem(seg: str) -> str:
    return f"seq_learning_curve_2_norm_{seg}"


# --------------------------------------------------------------------------- normalizer and channels (the parent's formulas, this rule's constants and S1 labels)
def apply_norm(raw: P.RawInputs) -> pd.DataFrame:
    """In place on ``raw.pgs``: multiply pts/reb/ast/fg3m by the game's factor (parent ``norm_factors`` with this rule's Lprev). Returns the factor table."""
    c = X1.norm_factors(raw.games, raw.pgs, lprev=LPREV, ref=REF)
    m = raw.pgs[["game_id"]].merge(c, on="game_id", how="left")
    if m[[f"c_{k}" for k in P.STATS]].isna().any().any():
        raise RuntimeError("a box row has a game with no normalizer factor")
    pgs = raw.pgs.copy()
    for k in P.STATS:
        pgs[k] = pgs[k].astype(float) * m[f"c_{k}"].to_numpy()
    raw.pgs = pgs
    return c


def extra_channels(season: np.ndarray) -> np.ndarray:
    """(n, 7) float32 NORM channels per row from the season label: five cumulative rule steps (none changes before 2016-17, so 2013-15 rows carry zeros), S1 flag (2013-2018), report-dropped flag."""
    s = np.asarray(season).astype(int)
    cols = [(s >= frm).astype(np.float32) for frm in X1.RULE_FLAG_FROM.values()]
    cols.append(np.isin(s, S1_LABELS).astype(np.float32))
    cols.append(np.isin(s, REPORT_DROPPED_LABELS).astype(np.float32))
    return np.stack(cols, axis=1)


def add_channels(exp: P.SeqExport, flags: pl.DataFrame) -> dict[str, int]:
    era, miss = RT.era_matrix(exp.meta["game_id"].to_numpy(), flags)
    exp.arrays["ctx"] = np.hstack(
        [exp.arrays["ctx"], era, extra_channels(exp.arrays["season"])]
    ).astype(np.float32)
    exp.ctx_features = NORM_CTX_FEATURES
    return {"rows": int(len(era)), "rows_without_flags": miss}


era_runtime_gate = (
    X1.era_runtime_gate
)  # test rows' era vector equals 2022-23's (run-time gate, as the parent)


# --------------------------------------------------------------------------- position (gate D4, enforced at export time)
def sidecar_positions(db: str | Path) -> pd.DataFrame:
    """Hand-sourced positions with provenance (``players_static_position_source``); empty when the table is absent."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        have = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        if SIDECAR not in have:
            return pd.DataFrame(
                {"player_id": pd.Series(dtype="int64"), "position": pd.Series(dtype="object")}
            )
        return con.execute(f"SELECT player_id, position FROM {SIDECAR}").df()
    finally:
        con.close()


def fill_position(raw: P.RawInputs, cache: pd.DataFrame, sidecar: pd.DataFrame) -> dict[str, int]:
    """Real position for every player any source knows: DB ``players_static`` wins, then the fetch cache, then the sidecar. In place on ``raw.static``."""
    n0 = int((raw.static["position"].fillna("").astype(str).str.len() > 0).sum())
    s = X.merge_static(raw.static, cache)
    raw.static = X.merge_static(s, sidecar)
    return {"db_known": n0, "known_after": int(len(raw.static))}


def position_guard(exp: P.SeqExport) -> dict[str, Any]:
    """Gate D4 at run time: per season label <= 2021, the share of target rows with NO position channel must be <= 1%. Raises otherwise (nothing is written)."""
    ctx = exp.arrays["ctx"]
    idx = [P.CTX_FEATURES.index(c) for c in X.POS_CTX]
    none = ctx[:, idx].sum(axis=1) == 0
    season = exp.arrays["season"].astype(int)
    out: dict[str, Any] = {}
    bad = []
    for s in sorted(set(season.tolist())):
        m = season == s
        share = float(none[m].mean()) if m.any() else 0.0
        out[str(s)] = {"rows": int(m.sum()), "no_position_rows": int(none[m].sum()), "share": share}
        if s <= POSITION_GUARD_LAST_LABEL and share > POSITION_MAX_UNKNOWN_SHARE:
            bad.append(f"{s}: {share:.4%}")
    out["ok"] = not bad
    if bad:
        raise RuntimeError("gate D4 at export time: position unknown above 1% in " + ", ".join(bad))
    return out


# --------------------------------------------------------------------------- loading / preparing a segment
def load_segment_raw(seg: str, db: str | Path | None = None) -> P.RawInputs:
    """Read-only load of one segment (SQL ``season <= hi`` <= 2023), keeping only the segment's own seasons (fresh Elo and tokens per segment)."""
    lo, hi = SEGMENTS[seg]
    assert_cap(hi)
    raw = P.load_raw(str(db or SEG_DB[seg]), max_season=hi)
    keep = set(raw.games.loc[(raw.games["season"] >= lo) & (raw.games["season"] <= hi), "game_id"])
    got = sorted(int(s) for s in raw.games.loc[raw.games["game_id"].isin(keep), "season"].unique())
    if got != list(range(lo, hi + 1)):
        raise ValueError(f"segment {seg}: database seasons {got} != {list(range(lo, hi + 1))}")
    return dataclasses.replace(
        raw,
        games=raw.games[raw.games["game_id"].isin(keep)].reset_index(drop=True),
        pgs=raw.pgs[raw.pgs["game_id"].isin(keep)].reset_index(drop=True),
        poss_player=raw.poss_player[raw.poss_player["game_id"].isin(keep)].reset_index(drop=True),
        poss_team=raw.poss_team[raw.poss_team["game_id"].isin(keep)].reset_index(drop=True),
        avail=raw.avail[raw.avail["game_id"].isin(keep)].reset_index(drop=True),
    )


def s1_rules(raw: P.RawInputs) -> dict[str, Any]:
    """In place, S1 only: played rows only, no report snapshot, no tips. Position is NOT touched here (real position, see ``fill_position``)."""
    played = raw.pgs["minutes"].fillna(0) > 0
    n_drop = int((~played).sum())
    raw.pgs = raw.pgs[played].reset_index(drop=True)
    raw.avail = raw.avail.iloc[
        0:0
    ]  # no report snapshot for 2013-2018 (has_report = 0 on every row)
    raw.tips = None
    return {"s1_dropped_non_playing_rows": n_drop}


def prepare_segment(
    raw: P.RawInputs,
    seg: str,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    cache: pd.DataFrame | None = None,
    sidecar: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """In place: Elo, tips (S2 / S3 as the parent), the S1 rules, and real position for every segment. Returns the coverage info."""
    info: dict[str, Any] = {}
    X._elo(raw, elo_config)
    if seg == "S1":
        info.update(s1_rules(raw))
    elif seg == "S2":
        raw.tips, info["tip_coverage"] = RT.tip_table(RT.HIST_SCHEDULE, raw.games)
        raw.no_report_seasons = RT.NO_REPORT_HIST
    else:
        raw.tips, info["tip_coverage"] = RT.tip_table(RT.CUR_SCHEDULE, raw.games)
    info["position_sources"] = fill_position(
        raw,
        X.cache_static() if cache is None else cache,
        sidecar if sidecar is not None else sidecar_positions(SEG_DB[seg]),
    )
    return info


def export_from_raw(
    raw: P.RawInputs, seg: str, flags: pl.DataFrame, **cfg_kw: Any
) -> tuple[P.SeqExport, dict[str, Any]]:
    """One segment, NORM variant, from an already prepared ``RawInputs`` (DB-free; the unit tests call this). ``raw`` is not mutated."""
    lo, hi = SEGMENTS[seg]
    assert_cap(int(raw.games["season"].max()))
    raw = dataclasses.replace(raw, pgs=raw.pgs.copy())
    c = apply_norm(raw)
    exp = P.build_export(raw, RT.real_cfg(min_target_season=lo, max_season=hi, **cfg_kw))
    assert_cap(int(exp.meta["season"].max()))
    info: dict[str, Any] = {"era": add_channels(exp, flags)}
    cc = exp.meta[["game_id"]].merge(c, on="game_id", how="left")
    norm_c = cc[[f"c_{k}" for k in P.STATS]].to_numpy(dtype=np.float64)
    if np.isnan(norm_c).any():
        raise RuntimeError("a target row has no normalizer factor")
    exp.arrays["norm_c"] = norm_c
    for i, k in enumerate(P.STATS):
        exp.meta[f"norm_c_{k}"] = norm_c[:, i]
    info["position"] = X.residual_position_share(exp)
    info["position_by_season"] = position_guard(exp)
    return exp, info


write_export = X1.write_export
manifest_sha = X1.manifest_sha


# --------------------------------------------------------------------------- windows (N = 1..10; the job pins these equal to the parent's for N <= 8)
def window_labels(n: int) -> tuple[int, ...]:
    if not 1 <= n <= 10:
        raise ValueError("windows 1..10 only")
    return tuple(range(2022 - n + 1, 2023))


def window_counts(meta: pd.DataFrame, n: int) -> dict[str, int]:
    labs = window_labels(n)
    season = meta["season"].to_numpy()
    date = pd.to_datetime(meta["game_date"]).to_numpy().astype("datetime64[D]")
    nprior = meta["n_prior"].to_numpy()
    in_win = np.isin(season, labs)
    cold_end = np.datetime64(X1.COLD_END)
    fit = (nprior >= 3) & in_win & (((season == 2022) & (date < cold_end)) | (season < 2022))
    val = (nprior >= 3) & (season == 2022) & (date >= cold_end)
    test = season == TEST_LABEL
    pool = (nprior >= 3) & (in_win | test)
    return {
        "fit": int(fit.sum()),
        "val": int(val.sum()),
        "test": int(test.sum()),
        "pool_eligible": int(pool.sum()),
    }


# --------------------------------------------------------------------------- build
def build(
    out: Path | None = None,
    only: tuple[str, ...] = ("S3", "S2", "S1"),
    elo_config: str = "configs/mov_elo_tuned.yaml",
) -> dict[str, Any]:
    out = out or OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {"cap_season": CAP_SEASON, "rule": RULE.ID, "segments": {}}
    for seg in only:
        raw = load_segment_raw(seg)
        pinfo = prepare_segment(raw, seg, elo_config)
        flags = RT.cur_flags(str(SEG_DB[seg]), SEGMENTS[seg][1])
        print(f"[{seg}] building", flush=True)
        exp, einfo = export_from_raw(raw, seg, flags)
        npz = write_export(exp, out, stem(seg))
        info["segments"][seg] = {
            "npz": str(npz),
            "npz_sha256": file_sha256(npz),
            "meta_sha256": file_sha256(out / f"{stem(seg)}_meta.parquet"),
            "targets": int(len(exp.meta)),
            "rows_by_season": {str(s): int((exp.meta["season"] == s).sum()) for s in sorted(exp.meta["season"].unique())},
            "rows_n_prior_ge3_by_season": {str(s): int(((exp.meta["season"] == s) & (exp.meta["n_prior"] >= 3)).sum()) for s in sorted(exp.meta["season"].unique())},
            "ctx_width": int(exp.arrays["ctx"].shape[1]),
            "norm_c_range": {k: [float(exp.arrays["norm_c"][:, i].min()), float(exp.arrays["norm_c"][:, i].max())] for i, k in enumerate(P.STATS)},
            **pinfo,
            **einfo,
        }  # fmt: skip
        del exp, raw
    if "S3" in only:
        z = np.load(out / f"{stem('S3')}.npz")
        info["era_runtime_gate"] = era_runtime_gate(
            z["ctx"], z["season"], tuple(str(a) for a in z["ctx_features"])
        )
    files = {Path(r["npz"]).name: r["npz_sha256"] for r in info["segments"].values()}
    info["manifest_sha256"] = manifest_sha(files)
    (out / "export_info.json").write_text(json.dumps(info, indent=2, default=str))
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("cmd", choices=("build",))
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", nargs="*", default=["S3", "S2", "S1"], choices=["S1", "S2", "S3"])
    a = ap.parse_args(argv)
    print(json.dumps(build(Path(a.out) if a.out else None, tuple(a.only)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
