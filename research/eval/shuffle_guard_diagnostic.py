# ruff: noqa: E501
"""Descriptive diagnostic (Devin 2026-10-12): the permutation null of RECENT_ROLE_FULL's shuffled-label guard (SHUF_FULL minus SHUF_CTRL, pts).

Seasons 2023 and 2024 only (2025 is never loaded). Not a rule, no claim; it re-runs the frozen guard construction under many permutation seeds.
    run:      python -m research.eval.shuffle_guard_diagnostic run [--seeds N]     (reads nba.duckdb read-only; caches per seed)
    analyze:  python -m research.eval.shuffle_guard_diagnostic analyze             (cache only, writes reports/shuffle_guard_diagnostic_2026-10-12.*)
Per (season, seed) four fits: heads C/R (permuted residuals, seed s), pi C/R (permuted short flags, seed s = same permutation index as the heads,
exactly the frozen construction), plus pi C/R with an independent seed s+1000 (variant). The 2x2 of (heads in {C,R}) x (pi in {C,R}) gives the
decomposition: heads-only = (hR,piC)-(hC,piC); pi-only = (hC,piR)-(hC,piC); both = (hR,piR)-(hC,piC).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import collect_pi
from nba.props.lower_tail import short_flag
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as RF
from research.eval import workload_eval as WE

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/ops/shuffle_guard_cache"
SEASONS = (2023, 2024)
STAT = "pts"
INDEP = 1000


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(n_seeds: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    fr, cfg, _ = RF.build_frame()
    fr = fr.filter(pl.col("season") <= 2024)  # belt and braces: 2025 never enters
    assert int(fr["season"].max()) <= 2024  # type: ignore[arg-type]
    for season in SEASONS:
        ts = (season,)
        pic = collect_pi(fr, cfg, stats=(STAT,), test_seasons=ts)
        pic["row_gid"] = pic["row_gid"].astype(str)
        pic["row_pid"] = pic["row_pid"].astype(np.int64)
        extra = fr.select(["game_id", "player_id", *RF.R_COLS]).with_columns(
            pl.col("game_id").cast(pl.Utf8)
        )
        for seed in range(n_seeds):
            f = CACHE / f"s{season}_seed{seed}.npz"
            if f.exists():
                continue
            t0 = time.time()
            heads = {}
            for arm in ("SHUF_CTRL", "SHUF_R"):
                heads[arm] = RF.collect_heads(fr, STAT, cfg, RF.heads_names(STAT, arm), shuffle=True, seed=seed, test_seasons=ts, log=False)  # fmt: skip
            pis = {}
            for arm in ("SHUF_CTRL", "SHUF_R"):
                cols, _ = RF.ARMS_PI[arm]
                for tag, sd in (("", seed), ("i", seed + INDEP)):
                    r = PR.walk_pi(fr, cols, cfg, test_seasons=ts, shuffle=True, seed=sd, log=False)
                    pis[arm + tag] = PR.align_to_stored(r, pic["row_gid"], pic["row_pid"])
            h0 = heads["SHUF_CTRL"]
            assert RF.same_rows(h0, heads["SHUF_R"])
            y = h0["row_y"]
            out: dict[str, np.ndarray] = {}
            for hk in ("SHUF_CTRL", "SHUF_R"):
                for pk in ("SHUF_CTRL", "SHUF_R", "SHUF_CTRLi", "SHUF_Ri"):
                    qi = WE.mixture_qi(heads[hk], PR.candidate_pic(pic, pis[pk]), STAT)
                    out[f"crps__{hk}__{pk}"] = PR.crps_int(qi, y).astype(np.float32)
            for pk, v in pis.items():
                out[f"pi__{pk}"] = v.astype(np.float32)
            for hk in heads:
                out[f"c__{hk}"] = heads[hk]["row_c"].astype(np.float32)
                out[f"s__{hk}"] = heads[hk]["row_s"].astype(np.float32)
            if seed == 0:  # row identity and covariates once per season
                j = pl.DataFrame({"game_id": h0["row_gid"].astype(str), "player_id": h0["row_pid"].astype(np.int64)}).join(extra, on=["game_id", "player_id"], how="left")  # fmt: skip
                base = {"gid": h0["row_gid"].astype(str), "pid": h0["row_pid"], "y": y, "minutes": h0["row_minutes"], "min10": h0["row_min10"], "starter10": h0["row_starter10"], "short": short_flag(h0["row_minutes"], h0["row_min10"]), "pic_pi": pic["row_pi"], "m": h0["row_c"]}  # fmt: skip
                for c in RF.R_COLS:
                    base[c] = j[c].to_numpy()
                np.savez_compressed(CACHE / f"s{season}_base.npz", **base)
            kw: dict[str, Any] = dict(out)
            np.savez_compressed(f, **kw)
            _log(f"season {season} seed {seed} done {time.time() - t0:.0f}s")


def run_noise(n_seeds: int) -> None:
    """Control: the five columns replaced by a fixed row-permutation of themselves (same marginals, no link to the row). Labels shuffled with the
    same seed as the cached C arm; saves crps of (heads Rn, pi Rn) so the analysis can contrast it with the cached (C, C)."""
    fr, cfg, _ = RF.build_frame()
    fr = fr.filter(pl.col("season") <= 2024)
    perm = np.random.default_rng(7).permutation(fr.height)
    fr = fr.with_columns([pl.Series(c, fr[c].to_numpy()[perm]) for c in RF.R_COLS])
    for season in SEASONS:
        ts = (season,)
        pic = collect_pi(fr, cfg, stats=(STAT,), test_seasons=ts)
        pic["row_gid"] = pic["row_gid"].astype(str)
        pic["row_pid"] = pic["row_pid"].astype(np.int64)
        for seed in range(n_seeds):
            f = CACHE / f"s{season}_noise_seed{seed}.npz"
            if f.exists():
                continue
            t0 = time.time()
            h = RF.collect_heads(
                fr,
                STAT,
                cfg,
                RF.heads_names(STAT, "SHUF_R"),
                shuffle=True,
                seed=seed,
                test_seasons=ts,
                log=False,
            )
            r = PR.walk_pi(
                fr,
                RF.ARMS_PI["SHUF_R"][0],
                cfg,
                test_seasons=ts,
                shuffle=True,
                seed=seed,
                log=False,
            )
            pi = PR.align_to_stored(r, pic["row_gid"], pic["row_pid"])
            qi = WE.mixture_qi(h, PR.candidate_pic(pic, pi), STAT)
            np.savez_compressed(
                f, crps=PR.crps_int(qi, h["row_y"]).astype(np.float32), gid=h["row_gid"].astype(str)
            )
            _log(f"noise season {season} seed {seed} done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    if sys.argv[1] == "noise":
        run_noise(int(sys.argv[sys.argv.index("--seeds") + 1]))
    elif sys.argv[1] == "run":
        n = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 24
        run(n)
    else:
        from research.eval.shuffle_guard_analyze import analyze

        analyze()
