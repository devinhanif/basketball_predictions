# ruff: noqa: E501
"""COMBO_LINE_CAL repair R-F: persist the S out-of-fold integer pmf199 grids for 2023-24 (DESCRIPTIVE input, no score).

Composes the COMBO_STOCKS_SHADOW arm S exactly as its config says, from four walk-forward stage-A outputs made by
``reports/prereg_combo_stocks_shadow/stage_a_oof.py`` (month blocks over 2023 and 2024, each block fitted on rows dated strictly
before it): pts = production lower-tail mixture (``--lt``), reb and ast = production recipe, fg3m = production recipe, blk and stl =
the NB count head. stl and blk are the stage grids themselves; pra, pr, pa, ra, bs are the one six-stat Gaussian copula with the
config's R (4 dp) and the frozen base normals (sha checked against the config), one call so the combos are mutually coherent.

Reads no outcome: the stage files also hold ``y_*``; this script never opens those arrays (it lists the arrays it reads). Season 2025
is never loaded (stage A filters ``season <= 2024`` in SQL). Output goes under data/ (not committed):

    <out>/keys.parquet        game_id, player_id, game_date, season, n_prior, starter10, team_id, has_report, minutes_played_flag
    <out>/grids.npz           int16 (n, 199): stl, blk, pra, pr, pa, ra, bs, and the marginals pts, reb, ast, fg3m
    <out>/manifest.json       sha256 of each file, row counts, the population check (25,815 / 25,767), provenance

    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore reports/prereg_combo_line_cal/build_oof.py \
        --stages <dir containing cr lt nb fg3m> --out /Users/devin/Downloads/nba-prediction/data/ops/combo_oof_2023_24
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nba.props import combo_copula as C  # noqa: E402

MAIN = Path("/Users/devin/Downloads/nba-prediction")
LOCK = MAIN / "data/ops/heavy.lock"
STAT_SRC = {"pts": "lt", "reb": "cr", "ast": "cr", "fg3m": "fg3m", "blk": "nb", "stl": "nb"}
EXPECT = {2023: 25815, 2024: 25767}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def load_keys(d: Path) -> pl.DataFrame:
    tags = sorted(p.stem.removeprefix("keys_") for p in d.glob("keys_*.parquet"))
    return pl.concat([pl.read_parquet(d / f"keys_{t}.parquet") for t in tags])


def load_q(d: Path, stat: str) -> np.ndarray:
    """Only ``q_<stat>`` is read from each block file (never ``y_*``)."""
    tags = sorted(p.stem.removeprefix("keys_") for p in d.glob("keys_*.parquet"))
    parts = []
    for t in tags:
        z = np.load(d / f"oof_{t}.npz")
        parts.append(z[f"q_{stat}"])
    return np.concatenate(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-lock", action="store_true", help="tests only")
    a = ap.parse_args()
    now = subprocess.run(["date"], capture_output=True, text=True).stdout.strip()
    print(f"date: {now}", flush=True)
    t0 = time.time()
    st = Path(a.stages)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty; refuse to overwrite (write-once)")

    from research.eval import workload_eval as WE

    class _Null:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: object) -> None:
            return None

    with _Null() if a.no_lock else WE.WorkloadLock(LOCK):
        keys = {k: load_keys(st / k) for k in ("cr", "lt", "nb", "fg3m")}
        base_keys = keys["cr"].select("game_id", "player_id")
        for k, v in keys.items():
            assert v.select("game_id", "player_id").equals(base_keys), f"stage {k} rows differ"
        kf = keys["cr"]
        assert int(kf["season"].max()) <= 2024  # type: ignore[arg-type]
        q = {s: load_q(st / src, s) for s, src in STAT_SRC.items()}
        for s, arr in q.items():
            assert arr.shape == (kf.height, 199), (s, arr.shape)
        print(f"loaded {kf.height} rows ({time.time() - t0:.0f}s)", flush=True)

        cfg = yaml.safe_load((ROOT / "configs/combo_stocks_shadow.yaml").read_text())
        r = np.array(cfg["copula"]["R"], dtype=float)
        base = C.base_normals()
        bsha = hashlib.sha256(base.tobytes()).hexdigest()
        assert bsha == cfg["copula"]["base_sha256"], "base normals differ from the frozen config"
        assert tuple(cfg["stat_order"]) == C.STAT_ORDER
        cdfs = {s: C.int_cdf(q[s], C.KMAX[s]) for s in C.STAT_ORDER}
        n = kf.height
        res: dict[str, list[np.ndarray]] = {c: [] for c in C.COMBOS}
        step = 4000
        for lo in range(0, n, step):
            hi = min(lo + step, n)
            gj = C.joint_grids({s: cdfs[s][lo:hi] for s in C.STAT_ORDER}, r, base)
            for c in C.COMBOS:
                res[c].append(gj[c])
            print(f"copula rows {hi}/{n} ({time.time() - t0:.0f}s)", flush=True)
        grids = {c: np.concatenate(v) for c, v in res.items()}
        grids.update({"stl": q["stl"], "blk": q["blk"]})
        marg = {s: q[s] for s in ("pts", "reb", "ast", "fg3m")}

        out.mkdir(parents=True, exist_ok=True)
        kf = kf.with_columns((pl.col("minutes") > 0).alias("played")).drop("minutes")
        kf.write_parquet(out / "keys.parquet")
        arrs = {k: v.astype(np.int16) for k, v in grids.items()}
        arrs.update({f"marg_{k}": v.astype(np.int16) for k, v in marg.items()})
        np.savez_compressed(out / "grids.npz", **arrs)  # type: ignore[arg-type]
        pop = kf.filter(
            pl.col("game_id").str.starts_with("002") & (pl.col("n_prior") >= 5) & pl.col("played")
        )
        by_season = {int(s): int(c) for s, c in pop.group_by("season").len().iter_rows()}
        # coherence: PRA >= PR, PA, RA draw-level implies quantile-wise order
        coh = bool(
            (grids["pra"] >= grids["pr"]).all()
            and (grids["pra"] >= grids["pa"]).all()
            and (grids["pra"] >= grids["ra"]).all()
        )
        man = {
            "date_cmd_output": now,
            "rule": "COMBO_LINE_CAL repair R-F (DRAFT rule; no fit, no score)",
            "rows": n,
            "games": int(kf["game_id"].n_unique()),
            "seasons": sorted(int(s) for s in kf["season"].unique().to_list()),
            "population_regular_played_nprior5_by_season": by_season,
            "population_expected": {str(k): v for k, v in EXPECT.items()},
            "population_reproduces": by_season.get(2023) == EXPECT[2023]
            and by_season.get(2024) == EXPECT[2024],
            "config_sha256": sha256_file(ROOT / "configs/combo_stocks_shadow.yaml"),
            "base_normals_sha256": bsha,
            "R_source": "configs/combo_stocks_shadow.yaml copula.R (4 dp)",
            "stage_sources": STAT_SRC,
            "pra_ge_pr_pa_ra_quantilewise": coh,
            "grid_targets": sorted(grids),
            "marginals": sorted(f"marg_{k}" for k in marg),
            "files": {
                p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                for p in (out / "keys.parquet", out / "grids.npz")
            },
            "arrays_never_read": "y_*, qb_*, q19raw_*, mean_*, m_*",
            "elapsed_s": round(time.time() - t0, 1),
        }
        (out / "manifest.json").write_text(json.dumps(man, indent=1))
        print(
            json.dumps(
                {
                    k: man[k]
                    for k in (
                        "rows",
                        "games",
                        "population_regular_played_nprior5_by_season",
                        "population_reproduces",
                        "pra_ge_pr_pa_ra_quantilewise",
                    )
                }
            ),
            flush=True,
        )
        print(f"done in {man['elapsed_s']} s", flush=True)


if __name__ == "__main__":
    main()
