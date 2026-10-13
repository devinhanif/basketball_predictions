# ruff: noqa: E501
"""Sequence exports for SEQ_LEARNING_CURVE (docs/prereg/SEQ_LEARNING_CURVE.md, frozen).

Three segments, built SEPARATELY (a token chain never crosses a segment boundary; Elo restarts per segment, as the RT history export did):

* **S1** labels 2015-2018, ``data/history/nba_history.duckdb`` (play-by-play-derived box): PLAYED ROWS ONLY (the 129-167 non-playing rows a season are dropped), no report
  snapshot (``has_report = 0``), position channels ZERO for every row (the position cache holds survivors only: a future-derived NULL pattern), no DNP rows.
* **S2** labels 2019-2021, the same DB: the RT history export unchanged (real-tip report tokens 2019-20 / 2020-21, none for 2021-22, position from DB + cache).
* **S3** labels 2022-2023, ``nba.duckdb``, **capped at season 2023 in code and in SQL**: the RT current export unchanged (real tips). 2024 and 2025 are never loaded.

Two variants of each segment, same rows:

* ``norm`` (NORM arms): ctx = RT's 31 + 3 covid era channels + 7 cumulative step / S1 / report-dropped channels (width 41), and the past-only league-rate normalizer applied to the
  player-game table (every token's pts/reb/ast/fg3m, every ``base_*`` recency mean, every target) with ``c_k(d) = L_ref,k / L_s,k(d)``; ``norm_c`` (n, 4) per row is
  stored so the scorer divides the predictions back to raw units.
* ``raw`` (RAW arms): exactly the RT construction (3 covid era channels, fixed-constant scaling, no normalizer; S1 enters with has_report = 0 and position zeroed). ``norm_c`` = 1.

    uv run python -m research.features.seq_learning_curve_export build [--out data/colab/seq_learning_curve] [--only S3]   # heavy: take data/ops/heavy.lock
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from research.eval import seq_learning_curve_rule as RULE
from research.features import player_sequences as P
from research.features import seq_ensemble_export as X
from research.features import seq_full_mix_export as X25
from research.features import seq_full_mix_rt_export as RT

CAP_SEASON = 2023  # the select season; 2024 and 2025 are never exported
SEGMENTS: dict[str, tuple[int, int]] = {"S1": (2015, 2018), "S2": (2019, 2021), "S3": (2022, 2023)}
SEG_DB = {"S1": RT.HIST_DB, "S2": RT.HIST_DB, "S3": Path(RT.CUR_DB)}
OUT_DIR = RULE.ROOT / "data/colab/seq_learning_curve"
STORED_DIR = RULE.ROOT / "data/colab/seq_full_mix_rt"  # run #1's exports
VARIANTS = ("norm", "raw")
S1_LABELS = tuple(range(2015, 2019))
REPORT_DROPPED_LABELS = (2021,)
TEST_LABEL = 2023
COLD_END = (
    "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice (every window)
)

# --- the frozen normalizer constants (rule table; 3 decimals as printed) -----------------------------------------------------------------------------------------
REF = {
    "pts": 114.330,
    "reb": 43.422,
    "ast": 25.211,
    "fg3m": 12.315,
}  # 2022-23 full-season mean per team-game
SEASON_MEAN = {  # games.season label -> full-season mean per team-game (pts, reb, ast, fg3m)
    2015: (102.489, 43.707, 22.132, 8.558),
    2016: (105.621, 43.409, 22.598, 9.705),
    2017: (106.215, 43.474, 23.147, 10.492),
    2018: (110.989, 45.155, 24.488, 11.363),
    2019: (111.646, 44.710, 24.276, 12.275),
    2020: (111.971, 44.257, 24.598, 12.678),
    2021: (110.322, 44.287, 24.524, 12.422),
    2022: (114.330, 43.422, 25.211, 12.315),
}
PRIOR_N = 200.0
#: Lprev for season s = the completed season s-1's mean; 2015-16 has no prior season in the data and uses L_ref. 2023-24 (test) uses 2022-23 (= L_ref).
LPREV: dict[int, dict[str, float]] = {
    s: dict(zip(P.STATS, (SEASON_MEAN[s - 1] if s - 1 in SEASON_MEAN else tuple(REF.values())), strict=True))
    for s in range(2015, CAP_SEASON + 1)
}  # fmt: skip

# --- era / rule-change channels (cumulative step indicators) -------------------------------------------------------------------------------------------------------
RULE_FLAG_FROM = {
    "rule_away_foul_2016": 2016,
    "rule_timeouts_2017": 2017,
    "rule_reset14_2018": 2018,
    "rule_nonbball_2021": 2021,
    "rule_takefoul_2022": 2022,
}
EXTRA_CTX = (*RULE_FLAG_FROM, "era_s1_derived_noreport", "era_report_dropped")
NORM_CTX_FEATURES = (*P.CTX_FEATURES, *RT.ERA_CTX, *EXTRA_CTX)
RAW_CTX_FEATURES = (*P.CTX_FEATURES, *RT.ERA_CTX)
IDENTITY_KEYS = X25.IDENTITY_KEYS
STRUCT_KEYS = (
    "mask",
    "opp_step",
    "team_idx",
    "opp_idx",
    "n_prior",
    "date_days",
    "player_id",
    "season",
)


def assert_cap(season_max: int) -> None:
    if int(season_max) > CAP_SEASON:
        raise RuntimeError(
            f"season {int(season_max)} > {CAP_SEASON}: the SEQ_LEARNING_CURVE exports are capped at the select season"
        )


# --------------------------------------------------------------------------- the past-only normalizer
def norm_factors(
    games: pd.DataFrame,
    pgs: pd.DataFrame,
    lprev: dict[int, dict[str, float]] | None = None,
    ref: dict[str, float] | None = None,
    prior_n: float = PRIOR_N,
) -> pd.DataFrame:
    """Per game: ``c_k = L_ref,k / L_s,k(d)``, ``L_s,k(d) = (sum of team-game totals of k in season s dated STRICTLY BEFORE d + prior_n * Lprev_s,k) / (n such team-games + prior_n)``.

    Same-date games are excluded (strictly before), so a game's factor never sees its own date. Columns: game_id, c_pts, c_reb, c_ast, c_fg3m. Games of a date with no earlier games
    get ``ref / lprev`` exactly.
    """
    lprev = lprev or LPREV
    ref = ref or REF
    g = games[["game_id", "game_date", "season"]].copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    tg = (
        pgs.groupby(["game_id", "team_id"])[list(P.STATS)]
        .sum(min_count=1)
        .fillna(0.0)
        .reset_index()
    )
    tg["n"] = 1.0
    tg = tg.merge(g, on="game_id", how="inner")
    out = []
    for season, gs in g.groupby("season"):
        s = int(season)
        if s not in lprev:
            raise KeyError(f"no Lprev for season {s}")
        dates = pd.DatetimeIndex(sorted(gs["game_date"].unique()))
        daily = (
            tg[tg["season"] == season]
            .groupby("game_date")[[*P.STATS, "n"]]
            .sum()
            .reindex(dates, fill_value=0.0)
        )
        before = daily.cumsum() - daily  # team-games strictly before each date
        c = pd.DataFrame(index=dates)
        for k in P.STATS:
            lk = (before[k] + prior_n * lprev[s][k]) / (before["n"] + prior_n)
            c[f"c_{k}"] = ref[k] / lk
        out.append(
            gs[["game_id", "game_date"]]
            .merge(c, left_on="game_date", right_index=True, how="left")
            .drop(columns="game_date")
        )
    return pd.concat(out, ignore_index=True)


def apply_norm(raw: P.RawInputs) -> pd.DataFrame:
    """In place on ``raw.pgs``: multiply pts/reb/ast/fg3m by the game's factor. Returns the factor table (game_id, c_*)."""
    c = norm_factors(raw.games, raw.pgs)
    m = raw.pgs[["game_id"]].merge(c, on="game_id", how="left")
    if m[[f"c_{k}" for k in P.STATS]].isna().any().any():
        raise RuntimeError("a box row has a game with no normalizer factor")
    pgs = raw.pgs.copy()
    for k in P.STATS:
        pgs[k] = pgs[k].astype(float) * m[f"c_{k}"].to_numpy()
    raw.pgs = pgs
    return c


# --------------------------------------------------------------------------- channels
def extra_channels(season: np.ndarray) -> np.ndarray:
    """(n, 7) float32 NORM channels per row from the season label: five cumulative rule steps, S1 flag, report-dropped flag."""
    s = np.asarray(season).astype(int)
    cols = [(s >= frm).astype(np.float32) for frm in RULE_FLAG_FROM.values()]
    cols.append(np.isin(s, S1_LABELS).astype(np.float32))
    cols.append(np.isin(s, REPORT_DROPPED_LABELS).astype(np.float32))
    return np.stack(cols, axis=1)


def add_channels(exp: P.SeqExport, flags: pl.DataFrame, norm: bool) -> dict[str, int]:
    era, miss = RT.era_matrix(exp.meta["game_id"].to_numpy(), flags)
    parts = [exp.arrays["ctx"], era]
    names = (*P.CTX_FEATURES, *RT.ERA_CTX)
    if norm:
        parts.append(extra_channels(exp.arrays["season"]))
        names = NORM_CTX_FEATURES
    exp.arrays["ctx"] = np.hstack(parts).astype(np.float32)
    exp.ctx_features = names
    return {"rows": int(len(era)), "rows_without_flags": miss}


def era_runtime_gate(ctx: np.ndarray, season: np.ndarray, names: tuple[str, ...]) -> dict[str, Any]:
    """Run-time gate: on 100% of 2023-24 test rows the era vector equals the 2022-23 vector (all rule_* = 1; covid, S1 and report-dropped channels = 0)."""
    idx = [names.index(c) for c in (*RT.ERA_CTX, *EXTRA_CTX)]
    expect = np.array([0, 0, 0, 1, 1, 1, 1, 1, 0, 0], dtype=np.float32)
    test = ctx[season == TEST_LABEL][:, idx]
    prev = ctx[season == TEST_LABEL - 1][:, idx]
    prev_rows = {tuple(r) for r in prev}
    eq = (test == expect[None, :]).all(axis=1)
    return {
        "test_rows": int(len(test)),
        "share_equal_expected": float(eq.mean()) if len(test) else float("nan"),
        "prev_season_distinct_vectors": sorted(list(map(float, v)) for v in prev_rows),
        "test_vectors_in_prev_season": bool(all(tuple(r) in prev_rows for r in test)),
        "ok": bool(len(test) > 0 and eq.all() and all(tuple(r) in prev_rows for r in test)),
    }


# --------------------------------------------------------------------------- loading / preparing a segment
def load_segment_raw(seg: str, db: str | Path | None = None) -> P.RawInputs:
    """Read-only load of one segment: SQL ``season <= hi`` (<= 2023), then only the segment's own seasons are kept (fresh Elo and tokens per segment)."""
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
    """In place, S1 only: played rows only, no report snapshot, position ZERO for every row, no tips."""
    played = raw.pgs["minutes"].fillna(0) > 0
    n_drop = int((~played).sum())
    raw.pgs = raw.pgs[played].reset_index(drop=True)
    raw.avail = raw.avail.iloc[
        0:0
    ]  # no report snapshot is loaded for 2015-2018 (has_report = 0 on every row)
    raw.static = pd.DataFrame(
        {"player_id": pd.Series(dtype="int64"), "position": pd.Series(dtype="object")}
    )  # position ZERO for every S1 row
    raw.tips = None
    return {"s1_dropped_non_playing_rows": n_drop}


def prepare_segment(
    raw: P.RawInputs, seg: str, elo_config: str = "configs/mov_elo_tuned.yaml"
) -> dict[str, Any]:
    """In place: Elo, static, tips and the S1 rules. Returns the coverage info."""
    info: dict[str, Any] = {}
    X._elo(raw, elo_config)
    if seg == "S1":
        info.update(s1_rules(raw))
    elif seg == "S2":
        raw.static = X.merge_static(raw.static, X.cache_static())
        raw.tips, info["tip_coverage"] = RT.tip_table(RT.HIST_SCHEDULE, raw.games)
        raw.no_report_seasons = RT.NO_REPORT_HIST
    else:
        raw.tips, info["tip_coverage"] = RT.tip_table(RT.CUR_SCHEDULE, raw.games)
    return info


def export_from_raw(
    raw: P.RawInputs, seg: str, norm: bool, flags: pl.DataFrame, **cfg_kw: Any
) -> tuple[P.SeqExport, dict[str, Any]]:
    """One segment, one variant, from an already prepared ``RawInputs`` (DB-free; the unit tests call this). ``raw`` is not mutated."""
    lo, hi = SEGMENTS[seg]
    assert_cap(int(raw.games["season"].max()))
    raw = dataclasses.replace(raw, pgs=raw.pgs.copy())
    c: pd.DataFrame | None = apply_norm(raw) if norm else None
    exp = P.build_export(raw, RT.real_cfg(min_target_season=lo, max_season=hi, **cfg_kw))
    assert_cap(int(exp.meta["season"].max()))
    info: dict[str, Any] = {"era": add_channels(exp, flags, norm)}
    n = len(exp.meta)
    if c is not None:
        cc = exp.meta[["game_id"]].merge(c, on="game_id", how="left")
        norm_c = cc[[f"c_{k}" for k in P.STATS]].to_numpy(dtype=np.float64)
    else:
        norm_c = np.ones((n, len(P.STATS)), dtype=np.float64)
    if np.isnan(norm_c).any():
        raise RuntimeError("a target row has no normalizer factor")
    exp.arrays["norm_c"] = norm_c
    for i, k in enumerate(P.STATS):
        exp.meta[f"norm_c_{k}"] = norm_c[:, i]
    info["position"] = X.residual_position_share(exp)
    return exp, info


def write_export(exp: P.SeqExport, out: Path, stem: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    arrays = dict(exp.arrays)
    arrays["step_features"] = np.array(exp.step_features)
    arrays["ctx_features"] = np.array(exp.ctx_features)
    arrays["date_str"] = np.array(
        pd.to_datetime(exp.meta["game_date"]).dt.strftime("%Y-%m-%d").to_numpy(), dtype="U10"
    )
    path = out / f"{stem}.npz"
    np.savez_compressed(path, **arrays)  # type: ignore[arg-type]
    exp.meta.to_parquet(out / f"{stem}_meta.parquet", index=False)
    return path


def stem(variant: str, seg: str) -> str:
    return f"seq_learning_curve_{variant}_{seg}"


# --------------------------------------------------------------------------- windows (the same definition the Colab job uses)
def window_labels(n: int) -> tuple[int, ...]:
    """The N training seasons ending 2022-23 (labels). N = 1..8."""
    if not 1 <= n <= 8:
        raise ValueError("windows 1..8 only (9-10 have no rows in either database)")
    return tuple(range(2022 - n + 1, 2023))


def window_masks(
    season: np.ndarray, date_days: np.ndarray, n_prior: np.ndarray, n: int
) -> dict[str, np.ndarray]:
    """Row sets of window N over rows of all segments: cold-fit rows, early-stopping validation rows, test rows, and the pool-eligible rows (window rows + 2023-24, before the block start)."""
    season = np.asarray(season)
    date = np.asarray(date_days).astype("datetime64[D]")
    labs = window_labels(n)
    in_win = np.isin(season, labs)
    cold_end = np.datetime64(COLD_END)
    fit = (n_prior >= 3) & in_win & (((season == 2022) & (date < cold_end)) | (season < 2022))
    val = (n_prior >= 3) & (season == 2022) & (date >= cold_end)
    test = season == TEST_LABEL
    pool_elig = (n_prior >= 3) & (in_win | test)
    return {"fit": fit, "val": val, "test": test, "pool_eligible": pool_elig}


def window_counts(meta: pd.DataFrame, n: int) -> dict[str, int]:
    m = window_masks(
        meta["season"].to_numpy(),
        pd.to_datetime(meta["game_date"]).to_numpy().astype("datetime64[D]").astype(np.int64),
        meta["n_prior"].to_numpy(),
        n,
    )
    return {k: int(v.sum()) for k, v in m.items()}


# --------------------------------------------------------------------------- identity against run #1's stored exports
def identity_vs_run1(new: dict[str, P.SeqExport], stored_dir: Path = STORED_DIR) -> dict[str, Any]:
    """The RAW S2 / S3 rows against run #1's stored RT exports for the shared rows (labels 2019-2021 from the history export; 2022 and 2023 from the current export), per array
    and in total. The 2024 rows of the stored current export are dropped by season before anything is compared. Reported, not a rule (the RT exporter omitted it)."""
    out: dict[str, Any] = {}
    for key, seg, stored_stem, label_sets in (
        ("hist_2019_2021", "S2", "seq_ensemble_rt_hist", {"2019-2021": (2019, 2020, 2021)}),
        ("cur", "S3", "seq_ensemble_rt_cur", {"2022": (2022,), "2023": (2023,)}),
    ):
        z = np.load(stored_dir / f"{stored_stem}.npz", allow_pickle=False)
        meta = pd.read_parquet(stored_dir / f"{stored_stem}_meta.parquet").reset_index(drop=True)
        season = z["season"]
        res: dict[str, Any] = {
            "ctx_features_equal": [str(a) for a in z["ctx_features"]]
            == [str(a) for a in new[seg].ctx_features],
            "step_features_equal": [str(a) for a in z["step_features"]]
            == [str(a) for a in new[seg].step_features],
            "stat_scale_equal": bool(
                np.array_equal(z["stat_scale"], new[seg].arrays["stat_scale"])
            ),
        }
        for name, labs in label_sets.items():
            m = np.isin(
                season, labs
            )  # drop every other season (incl. 2024) before the arrays are touched
            stored = {k: z[k][m] for k in IDENTITY_KEYS}
            res[name] = X25.identity_report(new[seg], stored, meta[m].reset_index(drop=True))
            res[name]["new_rows_in_labels"] = int(np.isin(new[seg].arrays["season"], labs).sum())
        out[key] = res
    return out


def norm_vs_raw_structure(norm: P.SeqExport, raw: P.SeqExport) -> dict[str, Any]:
    """Same rows in the same order, same structural arrays; only the stat channels, base, y and the ctx tail differ."""
    same_rows = bool(
        np.array_equal(norm.meta["game_id"].to_numpy(), raw.meta["game_id"].to_numpy())
        and np.array_equal(norm.meta["player_id"].to_numpy(), raw.meta["player_id"].to_numpy())
    )
    return {
        "same_rows": same_rows,
        **{k: bool(np.array_equal(norm.arrays[k], raw.arrays[k])) for k in STRUCT_KEYS},
    }


# --------------------------------------------------------------------------- build
def manifest_sha(files: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()


def build(
    out: Path | None = None,
    only: tuple[str, ...] = ("S3", "S2", "S1"),
    elo_config: str = "configs/mov_elo_tuned.yaml",
) -> dict[str, Any]:
    out = out or OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {"cap_season": CAP_SEASON, "segments": {}}
    keep_raw: dict[str, P.SeqExport] = {}  # RAW S2 / S3 only, for the identity check against run #1
    for seg in only:
        raw = load_segment_raw(seg)
        pinfo = prepare_segment(raw, seg, elo_config)
        flags = RT.cur_flags(str(SEG_DB[seg]), SEGMENTS[seg][1])
        exps: dict[str, P.SeqExport] = {}
        for variant in VARIANTS:
            print(f"[{seg}/{variant}] building", flush=True)
            exp, einfo = export_from_raw(raw, seg, variant == "norm", flags)
            npz = write_export(exp, out, stem(variant, seg))
            rec: dict[str, Any] = {
                "npz": str(npz),
                "npz_sha256": RULE.file_sha256(npz),
                "meta_sha256": RULE.file_sha256(out / f"{stem(variant, seg)}_meta.parquet"),
                "targets": int(len(exp.meta)),
                "rows_by_season": {
                    str(s): int((exp.meta["season"] == s).sum())
                    for s in sorted(exp.meta["season"].unique())
                },
                "rows_n_prior_ge3_by_season": {
                    str(s): int(((exp.meta["season"] == s) & (exp.meta["n_prior"] >= 3)).sum())
                    for s in sorted(exp.meta["season"].unique())
                },
                "ctx_width": int(exp.arrays["ctx"].shape[1]),
                **pinfo,
                **einfo,
            }
            if variant == "norm":
                rec["norm_c_range"] = {
                    k: [
                        float(exp.arrays["norm_c"][:, i].min()),
                        float(exp.arrays["norm_c"][:, i].max()),
                    ]
                    for i, k in enumerate(P.STATS)
                }
            info["segments"][f"{variant}_{seg}"] = rec
            exps[variant] = exp
        info["segments"][f"norm_{seg}"]["structure_vs_raw"] = norm_vs_raw_structure(
            exps["norm"], exps["raw"]
        )
        if seg in ("S2", "S3"):
            keep_raw[seg] = exps["raw"]
        del exps, raw
    if "S2" in keep_raw and "S3" in keep_raw:
        info["identity_vs_run1"] = identity_vs_run1(keep_raw)
    else:
        info["identity_vs_run1"] = "not computed: needs S2 and S3 built in the same call"
    if "S3" in only:
        z = np.load(out / f"{stem('norm', 'S3')}.npz")
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
