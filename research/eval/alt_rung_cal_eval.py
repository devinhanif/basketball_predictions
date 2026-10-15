# ruff: noqa: E501
"""ALT_RUNG_CAL harness (rule: docs/prereg/ALT_RUNG_CAL.md; see its "Harness contract (pinned)" section).

    .venv/bin/python -W ignore -m research.eval.alt_rung_cal_eval check      --data-root <main checkout>
    .venv/bin/python -W ignore -m research.eval.alt_rung_cal_eval rehearse   --data-root <main checkout> --truth oracle|a0
    .venv/bin/python -W ignore -m research.eval.alt_rung_cal_eval run        --data-root <main checkout> --sha <64 hex>

* ``check``: label-free preflight, every pinned count equality (no label of any kind is read).
* ``rehearse``: the whole pipeline on SYNTHETIC labels (one uniform per (game, player, stat) unit, hit = U < T, T = the
  published-gap oracle or A0 itself); no real rung or leg label is read; refuses nothing because nothing real is read.
* ``run``: the real labels. Refuses unless the rule is frozen (``prereg(ALT_RUNG_CAL): <sha>``) AND the harness files equal
  the ``harness(ALT_RUNG_CAL): <pin>`` commit (``alt_rung_cal_rule.require_ready``), and unless the preflight equalities hold.

Seasons 2023 and 2024 only (asserted before any read). CAL-main uses ``line_calibration_eval`` functions unchanged
(``design``, ``age_weights``, ``fit_logistic``); the bootstrap is ``model_clv_eval.Boot`` (ratio of resampled per-game sums).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import alt_rung_cal_gate as gate
from research.eval import alt_rung_cal_rule as rule
from research.eval import line_calibration_eval as lc
from research.eval.alt_ladder_hist_gate import (
    alt_over_rungs,
    load_alt_quotes,
    load_main_lines,
    load_report_stamps,
    window_games,
)
from research.eval.alt_line_70_gate import SEASONS, STATS, sha256_file
from research.eval.model_clv_eval import Boot

MAX_SEASON = 2024
BOOKS = ("draftkings", "fanduel")
PRIMARY = "draftkings"
EPS = lc.EPS  # 0.5 / 199
FLOOR = -0.002
ALPHA_FAMILY = 0.05 / 3  # Bonferroni m = 3 (CAL-main, CAL-rung, C0), one-sided
P3_ALPHA = 0.025
GAP_MAX, GAP_INTERVAL, GAP_SEASON = 0.02, 0.03, 0.03
BIN_HARM = 0.001
N_BOOT, SEED_GAME, SEED_DATE = 5000, 2026, 2027
K_NULL = 50
C0_BIN_W, C0_MIN_RAW = 0.05, 100
R_CLIP, ZM_CLIP = 8.0, 5.0
DEFECT_FIRST, DEFECT_LAST = np.datetime64("2023-12-07"), np.datetime64("2023-12-19")
OUT_REAL = Path("reports/alt_rung_cal")
OUT_REHEARSAL = Path("reports/prereg_alt_rung_cal/rehearsal")
GATE_JSON = Path("reports/prereg_alt_rung_cal/gate.json")
SELECT_JSON = Path("reports/prereg_line_calibration/select.json")
BIN_NAMES = [b for b, _, _ in gate.BIN_EDGES]
ARMS3 = ("CAL_main", "CAL_rung", "C0")
#: counts asserted before any label is read (gate.json, reproduced by the review)
PINNED = {
    "dk_S": 162_745,
    "dk_scored": 134_462,
    "dk_bins": {"55": 38_122, "65": 31_992, "75": 27_930, "85": 24_325, "95": 12_093},
    "dk_identity_rungs": 21_507,
    "dk_no_main_rungs": 6_776,
    "dk_units": 64_984,
    "fd_S": 244_270,
    "fd_scored": 221_776,
    "fd_identity_blocks": 5,
    "blocks": 64,
    "window_games": {"2023": 39, "2024": 62},
}
#: pinned design of CAL-rung: [1, x, zm, r, r*x, bench, bench*x]
COLS_NO_R = [0, 1, 2, 5, 6]
COLS_NO_ZM = [0, 1, 3, 4, 5, 6]
COLS_X_ONLY = [0, 1]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def bh_adjust(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (monotone step-up); byte-identical logic to
    ``nba.eval.context_residual_eval.bh_adjust`` (inlined so the harness pin covers it; a unit test checks equality)."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def sigmoid(v: np.ndarray) -> np.ndarray:
    return lc.sigmoid(v)


# ------------------------------------------------------------------------------------ frames (label-free)


def rung_arrays(g: pl.DataFrame, season: int) -> dict[str, Any]:
    g = g.sort(["game_id", "player_id", "stat", "line"])
    x, z = gate.design_xz(g)
    med = g["med"].to_numpy().astype(float)
    spr = np.maximum(g["spr"].to_numpy().astype(float), lc.SPREAD_MIN)
    point = g["line"].to_numpy().astype(float)
    main = g["main_point"].to_numpy().astype(float)
    has_main = ~np.isnan(main)
    mn = np.where(has_main, main, 0.0)
    zm = np.clip((mn - med) / spr, -ZM_CLIP, ZM_CLIP)
    r = np.clip((point - mn) / spr, -R_CLIP, R_CLIP)
    game = g["game_id"].to_numpy().astype(str)
    player = g["player_id"].to_numpy().astype(np.int64)
    stat = g["stat"].to_numpy().astype(str)
    unit = np.char.add(np.char.add(game, "|"), np.char.add(player.astype(str), stat))
    st10 = g["starter10"].fill_null(1.0).to_numpy().astype(float)
    return {
        "season": np.full(g.height, season, dtype=np.int64),
        "pm": g["p_model"].to_numpy().astype(float),
        "stat": stat,
        "game": game,
        "player": player,
        "point": point,
        "date": g["game_date"].to_numpy().astype("datetime64[D]"),
        "med": med,
        "spr": spr,
        "st10": st10,
        "x": x,
        "z": z,
        "zm": np.where(has_main, zm, 0.0),
        "r": np.where(has_main, r, 0.0),
        "has_main": has_main,
        "bench": (st10 < 0.5).astype(np.int64),
        "unit": unit,
    }


def concat(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def build_frames(data_root: Path) -> dict[str, Any]:
    """Label-free rung and leg frames for DraftKings and FanDuel (seasons 2023-24 only) plus the pinned meta."""
    assert max(SEASONS) <= MAX_SEASON
    odds_db = data_root / "data/odds/odds_history.duckdb"
    pairs = {
        s: gate.read_pairs_free(data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet", s)
        for s in SEASONS
    }
    tips = pl.concat([p.select("game_id", "tipoff_utc") for p in pairs.values()]).unique("game_id")
    stamps = load_report_stamps(data_root / "nba.duckdb")
    rungs: dict[str, list[dict[str, Any]]] = {b: [] for b in BOOKS}
    legs: list[dict[str, Any]] = []
    window_ids: dict[str, list[str]] = {}
    for season in SEASONS:
        _log(f"frames {season}")
        model = gate.read_model(
            data_root / f"reports/odds_history/model_rows_{season}.parquet", season
        )
        raw = load_alt_quotes(odds_db, season).filter(pl.col("book").is_in(list(BOOKS)))
        main = load_main_lines(odds_db, season).filter(pl.col("book").is_in(list(BOOKS)))
        model_alt = model.drop("starter10", "c", "s", "mo", "season").with_columns(
            pl.col("player_id").cast(raw.schema["player_id"])
        )
        t, _ = alt_over_rungs(raw, tips, model_alt, main)
        snap = (
            raw.group_by("game_id")
            .agg(pl.col("snapshot_ts").max().alias("snapshot_ts"))
            .join(tips, on="game_id", how="inner")
        )
        wg = window_games(stamps, snap.select("game_id", "tipoff_utc", "snapshot_ts"))
        w_set = sorted(wg["game_id"].to_list())
        window_ids[str(season)] = w_set
        t = t.with_columns(pl.col("game_id").is_in(w_set).alias("in_window")).join(
            main.select("game_id", "player_id", "stat", "book", "main_point"),
            on=["game_id", "player_id", "stat", "book"],
            how="left",
        )
        for book in BOOKS:
            g, _ = gate.book_rungs(t, book, model, model)
            rungs[book].append(rung_arrays(g, season))
        leg = (
            pairs[season]
            .filter(pl.col("book") == PRIMARY)
            .with_columns(pl.col("player_id").cast(pl.Int64))
        )
        lg, _ = gate.with_grid(
            leg.select(
                "game_id", "player_id", "stat", "book", "line", "p_model", "game_date", "starter10"
            ),
            model,
        )
        lg = lg.sort(["game_id", "player_id", "stat"])
        legs.append(
            {
                "season": np.full(lg.height, season, dtype=np.int64),
                "stat": lg["stat"].to_numpy().astype(str),
                "game": lg["game_id"].to_numpy().astype(str),
                "player": lg["player_id"].to_numpy().astype(np.int64),
                "date": lg["game_date"].to_numpy().astype("datetime64[D]"),
                "pm": lg["p_model"].to_numpy().astype(float),
                "line": lg["line"].to_numpy().astype(float),
                "med": lg["med"].to_numpy().astype(float),
                "spr": np.maximum(lg["spr"].to_numpy().astype(float), lc.SPREAD_MIN),
                "st10": lg["starter10"].to_numpy().astype(float),
            }
        )
    L = concat(legs)
    L["unit"] = np.char.add(
        np.char.add(L["game"], "|"), np.char.add(L["player"].astype(str), L["stat"])
    )
    meta = {
        "window_games": {k: len(v) for k, v in window_ids.items()},
        "window_game_ids_sha256": {
            k: hashlib.sha256("\n".join(v).encode()).hexdigest() for k, v in window_ids.items()
        },
        "input_sha256": {f: sha256_file(data_root / f) for f in gate.INPUT_FILES},
    }
    return {"rungs": {b: concat(rungs[b]) for b in BOOKS}, "legs": L, "meta": meta}


INPUT_NAMES = {
    "ODDS_HISTORY_SHA256": "data/odds/odds_history.duckdb",
    "PAIRS_SHA256_2023": "data/ops/model_clv_pairs/pairs_2023.parquet",
    "PAIRS_SHA256_2024": "data/ops/model_clv_pairs/pairs_2024.parquet",
    "MODEL_ROWS_SHA256_2023": "reports/odds_history/model_rows_2023.parquet",
    "MODEL_ROWS_SHA256_2024": "reports/odds_history/model_rows_2024.parquet",
    "ALT_LADDER_HIST_GATE_SHA256": "reports/prereg_alt_ladder_hist/gate.json",
}


def verify_inputs(meta: dict[str, Any], declared: dict[str, str]) -> dict[str, bool]:
    """The six data inputs and the two derived window-game lists must equal the sha256 lines pinned in the rule."""
    got = {k: meta["input_sha256"][v] for k, v in INPUT_NAMES.items()}
    got |= {f"WINDOW_IDS_SHA256_{s}": meta["window_game_ids_sha256"][str(s)] for s in SEASONS}
    bad = {
        k: (v[:12], declared.get(k, "missing")[:12]) for k, v in got.items() if declared.get(k) != v
    }
    if bad:
        raise RuntimeError(f"pinned input sha256 mismatch: {bad}")
    return {k: True for k in got}


# ------------------------------------------------------------------------------------ labels


def hit_of(q: np.ndarray, thr: np.ndarray, real: bool) -> np.ndarray:
    """Real: Y >= point + 0.5. Synthetic: U < T. ``q`` is the unit's latent (Y or U)."""
    return (q >= thr).astype(float) if real else (q < thr).astype(float)


def attach_real_labels(fr: dict[str, Any], data_root: Path) -> None:
    """Y from model_rows (integer-valued, non-null on every unit), legs' ``over`` from the pairs. Real labels."""
    ys = []
    for s in SEASONS:
        m = pl.read_parquet(
            data_root / f"reports/odds_history/model_rows_{s}.parquet",
            columns=["game_id", "player_id", "stat", "y"],
        ).with_columns(pl.col("player_id").cast(pl.Int64))
        ys.append(m)
    y = pl.concat(ys)
    assert y.group_by(["game_id", "player_id", "stat"]).len()["len"].max() == 1
    for b in BOOKS:
        R = fr["rungs"][b]
        j = pl.DataFrame({"game_id": R["game"], "player_id": R["player"], "stat": R["stat"]}).join(
            y, on=["game_id", "player_id", "stat"], how="left"
        )
        yy = j["y"].to_numpy().astype(float)
        assert not np.isnan(yy).any(), "a rung unit without a realised stat"
        assert np.all(yy == np.round(yy)), "a non-integer realised stat"
        R["q"], R["thr"], R["real"] = yy, R["point"] + 0.5, True
        R["hit"] = hit_of(R["q"], R["thr"], True)
    attach_leg_labels(fr, data_root)


def attach_leg_labels(fr: dict[str, Any], data_root: Path) -> None:
    """DraftKings main-line leg labels only (``pairs.over``, already scored in T493); no rung label."""
    L = fr["legs"]
    ov = pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "player_id", "stat", "book", "snapshot_kind", "over"],
            )
            .filter((pl.col("book") == PRIMARY) & (pl.col("snapshot_kind") == "t60"))
            .with_columns(pl.col("player_id").cast(pl.Int64))
            for s in SEASONS
        ]
    )
    j2 = pl.DataFrame({"game_id": L["game"], "player_id": L["player"], "stat": L["stat"]}).join(
        ov, on=["game_id", "player_id", "stat"], how="left"
    )
    L["over"] = j2["over"].to_numpy().astype(float)
    assert not np.isnan(L["over"]).any()


def attach_synthetic_labels(fr: dict[str, Any], truth: str, seed: int = 0) -> None:
    """One uniform per unit (shared by a unit's rungs and its leg, so ladders are nested); hit = U < T.
    truth = 'oracle': T = clip(A0 + published gap); truth = 'a0': T = A0. No real label is read."""
    assert truth in ("oracle", "a0")
    units = np.unique(
        np.concatenate([fr["legs"]["unit"]] + [fr["rungs"][b]["unit"] for b in BOOKS])
    )
    u = np.random.default_rng([seed, 7]).random(units.size)

    def tr(pm: np.ndarray, stat: np.ndarray) -> np.ndarray:
        a = np.clip(pm, EPS, 1 - EPS)
        if truth == "a0":
            return np.asarray(a)
        return np.asarray(np.clip(a + gate.oracle_gap(pm, stat), 1e-3, 1 - 1e-3))

    for b in BOOKS:
        R = fr["rungs"][b]
        R["q"] = u[np.searchsorted(units, R["unit"])]
        R["thr"], R["real"] = tr(R["pm"], R["stat"]), False
        R["hit"] = hit_of(R["q"], R["thr"], False)
    L = fr["legs"]
    qu = u[np.searchsorted(units, L["unit"])]
    L["over"] = (qu < tr(L["pm"], L["stat"])).astype(float)


# ------------------------------------------------------------------------------------ arms


def rung_design(R: dict[str, Any]) -> np.ndarray:
    """CAL-rung terms [1, x, zm, r, r*x, bench, bench*x]."""
    x = gate.logit(R["pm"])
    b = R["bench"].astype(float)
    return np.column_stack([np.ones_like(x), x, R["zm"], R["r"], R["r"] * x, b, b * x])


def c0_bin(p: np.ndarray) -> np.ndarray:
    return np.asarray(
        np.minimum(((p - 0.5 + 1e-12) / C0_BIN_W).astype(int), int(0.5 / C0_BIN_W) - 1)
    )


def shift_table(
    p_fit: np.ndarray, hit_fit: np.ndarray, w: np.ndarray, p_rows: np.ndarray
) -> np.ndarray:
    """C0 (pinned, review B1): clip(A0 + g, EPS, 1 - EPS), g = the bin's time-weighted (hit - A0) over the fit rungs of the
    0.05-wide A0 bin; g = 0 when the bin has fewer than 100 RAW fit rungs."""
    a_fit = np.clip(p_fit, EPS, 1 - EPS)
    a_rows = np.clip(p_rows, EPS, 1 - EPS)
    nb = int(0.5 / C0_BIN_W)
    bf = np.clip(c0_bin(a_fit), 0, nb - 1)
    g = np.zeros(nb)
    for b in range(nb):
        m = bf == b
        if int(m.sum()) >= C0_MIN_RAW:
            g[b] = float(np.sum(w[m] * (hit_fit[m] - a_fit[m])) / np.sum(w[m]))
    br = np.clip(c0_bin(a_rows), 0, nb - 1)
    return np.asarray(np.clip(a_rows + g[br], EPS, 1 - EPS))


def run_book(
    R: dict[str, Any], L: dict[str, Any], k_null: int, label: str
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    """Walk-forward arms for one book's rungs. Fit rows are strictly earlier calendar months. CAL-main is fit on the
    DraftKings MODEL_CLV legs (also for FanDuel: the book swap); CAL-rung, C0 and BASE on this book's rungs with a main
    line. Scored rows: every arm has >= 2,000 fit rows in the block and the unit has a main line."""
    n = R["pm"].size
    names = (
        "A0", "CAL_main", "CAL_rung", "C0", "BASE", "KO_x", "KO_no_r", "KO_no_zm", "CAL_rung_nodefect",
        "SHr_ll", "SHm_ll",
    )  # fmt: skip
    arms = {k: np.full(n, np.nan) for k in names}
    arms["inband"] = np.zeros(n)
    arms["scored"] = np.zeros(n)
    arms["A0"] = np.clip(R["pm"], EPS, 1 - EPS)
    mstart = gate.month_start(R["date"])
    Xm = lc.design(R["pm"], R["point"], R["med"], R["spr"], R["st10"])
    Xl = lc.design(L["pm"], L["line"], L["med"], L["spr"], L["st10"])
    xl, zl = gate.logit(L["pm"]), np.clip((L["line"] - L["med"]) / L["spr"], -lc.Z_CLIP, lc.Z_CLIP)
    Xr = rung_design(R)
    uid = np.unique(R["unit"], return_inverse=True)[1]
    info: list[dict[str, Any]] = []
    sh_k_r: list[list[float]] = [[] for _ in range(k_null)]
    sh_k_m: list[list[float]] = [[] for _ in range(k_null)]
    sh_k_n: list[int] = []
    shr_acc, shm_acc = np.zeros(n), np.zeros(n)
    for si_, stat in enumerate(STATS):
        cell_r, cell_l = R["stat"] == stat, L["stat"] == stat
        for bi, b0 in enumerate(np.unique(mstart[cell_r])):
            tgt = cell_r & (mstart == b0)
            fit_l = cell_l & (L["date"] < b0)
            fit_r = cell_r & R["has_main"] & (R["date"] < b0)
            n_l, n_r = int(fit_l.sum()), int(fit_r.sum())
            ok = n_l >= lc.MIN_FIT and n_r >= lc.MIN_FIT
            rows = tgt & R["has_main"]
            rec: dict[str, Any] = {
                "stat": stat,
                "block": str(b0)[:7],
                "season": int(R["season"][tgt][0]),
                "n_score": int(tgt.sum()),
                "n_score_with_main": int(rows.sum()),
                "n_fit_legs": n_l,
                "n_fit_rungs": n_r,
                "all_arms_fit": ok,
            }
            info.append(rec)
            if not ok or not rows.any():
                continue
            wl = lc.age_weights(L["date"][fit_l], b0)
            wr = lc.age_weights(R["date"][fit_r], b0)
            hit_r = R["hit"][fit_r]
            beta_m = lc.fit_logistic(Xl[fit_l], L["over"][fit_l], w=wl)
            beta_r = lc.fit_logistic(Xr[fit_r], hit_r, w=wr)
            arms["CAL_main"][rows] = np.clip(sigmoid(Xm[rows] @ beta_m), EPS, 1 - EPS)
            arms["CAL_rung"][rows] = np.clip(sigmoid(Xr[rows] @ beta_r), EPS, 1 - EPS)
            arms["C0"][rows] = shift_table(R["pm"][fit_r], hit_r, wr, R["pm"][rows])
            arms["BASE"][rows] = float(np.clip(np.sum(wr * hit_r) / np.sum(wr), EPS, 1 - EPS))
            for nm, cols in (
                ("KO_x", COLS_X_ONLY),
                ("KO_no_r", COLS_NO_R),
                ("KO_no_zm", COLS_NO_ZM),
            ):
                bk = lc.fit_logistic(Xr[fit_r][:, cols], hit_r, w=wr)
                arms[nm][rows] = np.clip(sigmoid(Xr[rows][:, cols] @ bk), EPS, 1 - EPS)
            nd = ~((R["date"][fit_r] >= DEFECT_FIRST) & (R["date"][fit_r] <= DEFECT_LAST))
            if label == PRIMARY and book_has_defect(R) and not nd.all():
                bn = lc.fit_logistic(Xr[fit_r][nd], hit_r[nd], w=wr[nd])
                arms["CAL_rung_nodefect"][rows] = np.clip(sigmoid(Xr[rows] @ bn), EPS, 1 - EPS)
            else:
                arms["CAL_rung_nodefect"][rows] = arms["CAL_rung"][rows]
            xlo, xhi = np.quantile(xl[fit_l], [gate.Q_LO, gate.Q_HI])
            zlo, zhi = np.quantile(zl[fit_l], [gate.Q_LO, gate.Q_HI])
            arms["inband"][rows] = (
                (R["x"][rows] >= xlo)
                & (R["x"][rows] <= xhi)
                & (R["z"][rows] >= zlo)
                & (R["z"][rows] <= zhi)
            )
            arms["scored"][rows] = 1.0
            rec["beta_main"] = [float(v) for v in beta_m]
            rec["beta_rung"] = [float(v) for v in beta_r]
            # shuffled-label nulls (label only): CAL-rung permutes whole units' latent q among the fit units of this
            # stat and block; CAL-main permutes the leg labels (LINE_CALIBRATION's construction)
            first = np.unique(uid[fit_r], return_index=True)
            qfit = R["q"][np.flatnonzero(fit_r)[first[1]]]
            pos = np.searchsorted(first[0], uid[fit_r])
            hit_rows = R["hit"][rows]
            for k in range(k_null):
                rng = np.random.default_rng([1000 + k, si_, bi, BOOKS.index(PRIMARY)])
                qn = rng.permutation(qfit)[pos]
                hk = hit_of(qn, R["thr"][fit_r], bool(R["real"]))
                bs = lc.fit_logistic(Xr[fit_r], hk, w=wr)
                psr = np.clip(sigmoid(Xr[rows] @ bs), EPS, 1 - EPS)
                lr = lc.ll(psr, hit_rows)
                shr_acc[rows] += lr / k_null
                sh_k_r[k].append(float(lr.sum()))
                rngm = np.random.default_rng([1000 + k, si_, bi, 0])
                bm = lc.fit_logistic(Xl[fit_l], rngm.permutation(L["over"][fit_l]), w=wl)
                psm = np.clip(sigmoid(Xm[rows] @ bm), EPS, 1 - EPS)
                lm = lc.ll(psm, hit_rows)
                shm_acc[rows] += lm / k_null
                sh_k_m[k].append(float(lm.sum()))
            sh_k_n.append(int(rows.sum()))
            _log(
                f"{label} {stat} {str(b0)[:7]} scored {int(rows.sum())} fit legs {n_l} rungs {n_r}"
            )
    if k_null:
        arms["SHr_ll"], arms["SHm_ll"] = (
            np.where(arms["scored"] > 0, shr_acc, np.nan),
            np.where(arms["scored"] > 0, shm_acc, np.nan),
        )
    nsc = float(sum(sh_k_n)) if sh_k_n else 1.0
    arms["sh_k_mean_ll_rung"] = np.array([sum(v) / nsc for v in sh_k_r])
    arms["sh_k_mean_ll_main"] = np.array([sum(v) / nsc for v in sh_k_m])
    return arms, info


def book_has_defect(R: dict[str, Any]) -> bool:
    d = R["date"]
    return bool(((d >= DEFECT_FIRST) & (d <= DEFECT_LAST)).any())


# ------------------------------------------------------------------------------------ preflight


def preflight(
    fr: dict[str, Any],
    dk_info: list[dict[str, Any]],
    fd_info: list[dict[str, Any]],
    dk_arms: dict[str, Any],
    fd_arms: dict[str, Any],
) -> dict[str, Any]:
    """Every pinned count equality, asserted before any label is read by ``run``."""
    R = fr["rungs"][PRIMARY]
    S = int(R["pm"].size)
    sc = dk_arms["scored"] > 0
    lab = gate.bin_label(R["pm"])
    bins = {b: int(((lab == b) & sc).sum()) for b in BIN_NAMES}
    ident_rungs = int(sum(r["n_score_with_main"] for r in dk_info if not r["all_arms_fit"]))
    no_main = int((~R["has_main"]).sum())
    units = int(np.unique(R["unit"][sc]).size)
    fd_sc = int((fd_arms["scored"] > 0).sum())
    fd_ident = int(sum(1 for r in fd_info if not r["all_arms_fit"]))
    got = {
        "dk_S": S, "dk_scored": int(sc.sum()), "dk_bins": bins, "dk_identity_rungs": ident_rungs,
        "dk_no_main_rungs": no_main, "dk_units": units, "fd_S": int(fr["rungs"]["fanduel"]["pm"].size),
        "fd_scored": fd_sc, "fd_identity_blocks": fd_ident, "blocks": len(dk_info),
        "window_games": fr["meta"]["window_games"],
    }  # fmt: skip
    # pinned identity-block list must equal gate.json
    gj = json.loads(Path(rule.ROOT / GATE_JSON).read_text())
    want_ident = sorted(
        (b["stat"], b["block"])
        for b in gj["stages"][PRIMARY]["pooled_2023_2024"]["blocks"]
        if not b["all_arms_fit"]
    )
    got_ident = sorted((r["stat"], r["block"]) for r in dk_info if not r["all_arms_fit"])
    bad = {k: (got[k], PINNED[k]) for k in PINNED if got[k] != PINNED[k]}
    if bad:
        raise RuntimeError(f"pinned count mismatch: {bad}")
    if want_ident != got_ident:
        raise RuntimeError("identity-block list differs from gate.json")
    return {"counts": got, "identity_blocks_equal_gate_json": True}


def check_t493(dk_info: list[dict[str, Any]], select_json: Path) -> dict[str, Any]:
    """CAL-main reproduction: every DK block of season 2024 must equal the T493 coefficients to 1e-9."""
    s = json.loads(select_json.read_text())
    ref = {(r["stat"], r["block"]): r for r in s["draftkings"]["blocks_cal"] if "beta" in r}
    n, worst = 0, 0.0
    for r in dk_info:
        if r["season"] != 2024 or "beta_main" not in r:
            continue
        key = (r["stat"], r["block"])
        if key not in ref:
            raise RuntimeError(f"block {key} has no T493 coefficients")
        worst = max(
            worst, float(np.max(np.abs(np.array(r["beta_main"]) - np.array(ref[key]["beta"]))))
        )
        n += 1
    if n == 0 or worst > 1e-9:
        raise RuntimeError(f"CAL-main does not reproduce T493: {n} blocks, max |diff| {worst}")
    return {"blocks": n, "max_abs_diff": worst}


# ------------------------------------------------------------------------------------ scoring


def dense(codes: np.ndarray) -> tuple[np.ndarray, int]:
    u, inv = np.unique(codes, return_inverse=True)
    return inv.astype(np.int64), int(u.size)


def interval_union(g: dict[str, Any], d: dict[str, Any]) -> tuple[float, float]:
    return min(g["lo"], d["lo"]), max(g["hi"], d["hi"])


def cell_word(gap: float, lo: float, hi: float) -> str:
    if abs(gap) <= GAP_MAX and lo >= -GAP_INTERVAL and hi <= GAP_INTERVAL:
        return "CALIBRATED"
    if gap < -GAP_MAX and hi < 0:
        return "OVERCONFIDENT"
    if gap > GAP_MAX and lo > 0:
        return "UNDERCONFIDENT"
    return "INCONCLUSIVE"


def ratio_ci(bs: Boot, num: np.ndarray, den: np.ndarray, m: np.ndarray) -> dict[str, Any]:
    """Weighted mean sum(num)/sum(den) over rows in m, game-clustered (S4 unit-mean weighting)."""
    k = m & np.isfinite(num)
    S = np.bincount(bs.gi[k], weights=num[k], minlength=bs.G)
    C = np.bincount(bs.gi[k], weights=den[k], minlength=bs.G)
    bm = (bs.W @ S) / np.where(bs.W @ C > 0, bs.W @ C, np.nan)
    lo, hi = np.percentile(bm[np.isfinite(bm)], [2.5, 97.5])
    return {"mean": float(S.sum() / C.sum()), "lo": float(lo), "hi": float(hi)}


def score(R: dict[str, Any], arms: dict[str, Any], tag: str) -> dict[str, Any]:
    """Every contrast of one book on its scored rows (pooled 2023-24)."""
    sc = arms["scored"] > 0
    hit = R["hit"][sc]
    gi, G = dense(R["game"][sc])
    di, ND = dense(R["date"][sc].astype(str))
    bs, bd = Boot(gi, G, SEED_GAME, N_BOOT), Boot(di, ND, SEED_DATE, N_BOOT)
    n = int(sc.sum())
    every = np.ones(n, bool)
    p = {a: arms[a][sc] for a in ("A0", "CAL_main", "CAL_rung", "C0", "BASE")}
    L = {a: lc.ll(v, hit) for a, v in p.items()}
    lab = gate.bin_label(R["pm"][sc])
    stat = R["stat"][sc]
    season = R["season"][sc]
    bench = R["bench"][sc] == 1
    month = gate.month_start(R["date"][sc]).astype(str)
    rabs = np.abs(R["r"][sc])
    edges = np.quantile(rabs, [1 / 3, 2 / 3])
    rt = np.digitize(rabs, edges)
    res: dict[str, Any] = {"book_tag": tag, "n": n, "games": G, "dates": ND}
    res["ll"] = {a: float(v.mean()) for a, v in L.items()}
    res["null"] = {
        "k": int(len(arms["sh_k_mean_ll_rung"])),
        "rung_sh_mean_ll": float(arms["sh_k_mean_ll_rung"].mean()),
        "rung_sh_sd": float(arms["sh_k_mean_ll_rung"].std(ddof=1))
        if len(arms["sh_k_mean_ll_rung"]) > 1
        else 0.0,
        "main_sh_mean_ll": float(arms["sh_k_mean_ll_main"].mean()),
        "main_sh_sd": float(arms["sh_k_mean_ll_main"].std(ddof=1))
        if len(arms["sh_k_mean_ll_main"]) > 1
        else 0.0,
    }
    shr, shm = arms["SHr_ll"][sc], arms["SHm_ll"][sc]
    arms_res: dict[str, Any] = {}
    for a in ARMS3:
        r: dict[str, Any] = {}
        d = L[a] - L["A0"]
        mg = bs.mean(d, every)
        r["d_a0"] = {k: mg[k] for k in ("mean", "lo", "hi")} | {"p_better": bs.mean(-d, every)["p"]}
        r["d_a0_date"] = {k: v for k, v in bd.mean(d, every).items() if k in ("mean", "lo", "hi")}
        if a != "C0":
            dc = L[a] - L["C0"]
            r["d_c0"] = {
                k: v for k, v in bs.mean(dc, every).items() if k in ("mean", "lo", "hi")
            } | {"p_better": bs.mean(-dc, every)["p"]}
        sh = shr if a == "CAL_rung" else shm
        ds = L[a] - sh
        r["d_sh_label"] = {k: v for k, v in bs.mean(ds, every).items() if k in ("mean", "lo", "hi")}
        # P2: per-bin gap with the union interval
        bins: dict[str, Any] = {}
        for b in BIN_NAMES:
            m = lab == b
            v = hit - p[a]
            g1, d1 = bs.mean(v, m), bd.mean(v, m)
            lo, hi = interval_union(g1, d1)
            bins[b] = {
                "n": int(m.sum()),
                "gap": g1["mean"],
                "lo": lo,
                "hi": hi,
                "word": cell_word(g1["mean"], lo, hi),
                "d_a0": bs.mean(d, m)["mean"],
                "mean_p": float(p[a][m].mean()),
            }
            bins[b]["pass_p2"] = bool(
                abs(g1["mean"]) <= GAP_MAX and lo >= -GAP_INTERVAL and hi <= GAP_INTERVAL
            )
        r["bins"] = bins
        # per stat (P5): one-sided p for "worse", BH over 4
        ps = [bs.mean(d, stat == s_)["p"] for s_ in STATS]
        q = bh_adjust(np.array(ps))
        r["per_stat"] = {
            s_: {"d_a0": bs.mean(d, stat == s_)["mean"], "p_worse": ps[i], "q_bh": float(q[i])}
            for i, s_ in enumerate(STATS)
        }
        r["slices"] = {
            "starter": bs.mean(d, ~bench)["mean"],
            "bench": bs.mean(d, bench)["mean"],
            **{f"season {s_}": bs.mean(d, season == s_)["mean"] for s_ in SEASONS},
            **{f"r_tertile {t}": bs.mean(d, rt == t)["mean"] for t in range(3)},
            "in_band": bs.mean(d, arms["inband"][sc] > 0)["mean"]
            if (arms["inband"][sc] > 0).any()
            else None,
            "out_of_band": bs.mean(d, arms["inband"][sc] == 0)["mean"]
            if (arms["inband"][sc] == 0).any()
            else None,
        }
        r["by_month"] = {mo: float(d[month == mo].mean()) for mo in np.unique(month)}
        # per-season bin gaps (P8)
        r["season_bin_gap"] = {
            f"{s_}:{b}": float((hit - p[a])[(season == s_) & (lab == b)].mean())
            for s_ in SEASONS
            for b in BIN_NAMES
            if ((season == s_) & (lab == b)).any()
        }
        # drop-the-best-month (label)
        tot = float(d.sum())
        contrib = {mo: float(d[month == mo].sum()) for mo in np.unique(month)}
        best = min(contrib, key=lambda k: contrib[k])
        r["drop_best_month"] = {
            "month": best,
            "d_a0": (tot - contrib[best]) / (n - int((month == best).sum())),
        }
        # unit-mean weighting (S4)
        un, inv, cnt = np.unique(R["unit"][sc], return_inverse=True, return_counts=True)
        w = 1.0 / cnt[inv]
        r["s4_unit_weighted_d_a0"] = ratio_ci(bs, d * w, w, every)
        # calibration in the large by stat and role
        r["calib"] = {
            "all": float((hit - p[a]).mean()),
            **{s_: float((hit - p[a])[stat == s_].mean()) for s_ in STATS},
            "starter": float((hit - p[a])[~bench].mean()),
            "bench": float((hit - p[a])[bench].mean()),
        }
        # reliability, ten equal bins on the arm's own probability
        idx = np.minimum((p[a] * 10).astype(int), 9)
        r["reliability"] = [
            {
                "bin": b_ / 10,
                "n": int((idx == b_).sum()),
                "pred": float(p[a][idx == b_].mean()),
                "obs": float(hit[idx == b_].mean()),
            }
            for b_ in range(10)
            if (idx == b_).any()
        ]
        arms_res[a] = r
    res["arms"] = arms_res
    res["reference"] = {
        "A0_bins": {
            b: {"n": int((lab == b).sum()), "gap": float((hit - p["A0"])[lab == b].mean())}
            for b in BIN_NAMES
        },
        "BASE_minus_A0": float((L["BASE"] - L["A0"]).mean()),
    }
    # knockouts of CAL-rung against C0 (label only)
    ko = {}
    for nm in ("KO_x", "KO_no_r", "KO_no_zm", "CAL_rung_nodefect"):
        v = lc.ll(arms[nm][sc], hit) - L["C0"]
        ko[nm] = {k: val for k, val in bs.mean(v, every).items() if k in ("mean", "lo", "hi")}
    res["knockouts_vs_c0"] = ko
    inb = arms["inband"][sc] > 0
    res["extrapolation_share_outside_band"] = {
        b: float(1.0 - inb[lab == b].mean()) for b in BIN_NAMES if (lab == b).any()
    }
    res["extrapolation_share_outside_band_all"] = float(1.0 - inb.mean())
    # stat x bin cells (labels; words only where the cell passes the width gate)
    cells: dict[str, Any] = {}
    for b in BIN_NAMES:
        for s_ in STATS:
            m = (lab == b) & (stat == s_)
            if not m.any():
                continue
            cinfo = gate.cell(R["pm"][sc][m], R["game"][sc][m])
            eligible = gate.cell_ok(cinfo)
            row: dict[str, Any] = {"n": int(m.sum()), "eligible": eligible}
            for a in ARMS3:
                v = hit - p[a]
                g1, d1 = bs.mean(v, m), bd.mean(v, m)
                lo, hi = interval_union(g1, d1)
                row[a] = {
                    "gap": g1["mean"],
                    "word": cell_word(g1["mean"], lo, hi) if eligible else None,
                }
            cells[f"{b}:{s_}"] = row
    res["stat_bin_cells"] = cells
    return res


def evaluate(dk: dict[str, Any], fd: dict[str, Any]) -> dict[str, Any]:
    """P1-P8 and the words, exactly as the rule states."""
    out: dict[str, Any] = {}
    for a in ARMS3:
        r, rf = dk["arms"][a], fd["arms"][a]
        ch = {
            "P1": bool(r["d_a0"]["mean"] <= FLOOR and r["d_a0"]["p_better"] < ALPHA_FAMILY),
            "P2": all(v["pass_p2"] for v in r["bins"].values()),
            "P3": (
                bool(r["d_c0"]["p_better"] < P3_ALPHA and r["d_c0"]["mean"] < 0)
                if a != "C0"
                else None
            ),
            "P5": all(v["q_bh"] >= 0.05 for v in r["per_stat"].values()),
            "P6": bool(
                r["slices"]["starter"] <= 0
                and r["slices"]["bench"] <= 0
                and all(v["d_a0"] <= BIN_HARM for v in r["bins"].values())
            ),
            "P7": bool(rf["d_a0"]["mean"] < 0),
            "P8": bool(
                all(r["slices"][f"season {s}"] < 0 for s in SEASONS)
                and all(abs(v) <= GAP_SEASON for v in r["season_bin_gap"].values())
            ),
        }
        out[a] = ch
    core = ("P1", "P2", "P5", "P6", "P7", "P8")

    def fixes(a: str) -> bool:
        return all(bool(out[a][k]) for k in core)

    if fixes("CAL_main"):
        word = "MAIN_MAP_FIXES"
    elif fixes("CAL_rung") and out["CAL_rung"]["P3"]:
        word = "RUNG_REFIT_FIXES"
    elif fixes("C0") or fixes("CAL_rung"):
        word = "TABLE_FIXES"
    else:
        word = "NOT_FIXED"
    qual: dict[str, Any] = {}
    if word == "MAIN_MAP_FIXES":
        qual["extrapolation_share_outside_fit_leg_band_by_bin"] = dk[
            "extrapolation_share_outside_band"
        ]
        qual["bins_75_to_95_are_extrapolation"] = True
        if not out["CAL_main"]["P3"]:
            qual["qualifier"] = "a table does as well"
    return {
        "checks": out,
        "word": word,
        "qualifiers": qual,
        "p3_note": "a P3 failure is read as 'not shown' (CAL-rung - C0 has an MDE above the prior effect)",
    }


# ------------------------------------------------------------------------------------ drivers


def save_arrays(out: Path, name: str, R: dict[str, Any], arms: dict[str, Any]) -> str:
    sc = arms["scored"] > 0
    keys = ("game", "player", "stat", "point", "date", "season", "bench", "r", "hit", "pm", "unit")
    d = {k: R[k][sc] for k in keys}
    d["date"] = d["date"].astype(str)
    d["bin"] = gate.bin_label(R["pm"][sc])
    for a in (
        "A0",
        "CAL_main",
        "CAL_rung",
        "C0",
        "BASE",
        "KO_x",
        "KO_no_r",
        "KO_no_zm",
        "CAL_rung_nodefect",
        "SHr_ll",
        "SHm_ll",
        "inband",
    ):
        d[a] = arms[a][sc]
    path = out / f"arrays_{name}.npz"
    np.savez_compressed(path, **d)
    return sha256_file(path)


def dropped_report(R: dict[str, Any], arms: dict[str, Any]) -> dict[str, Any]:
    """S2 and the dropped-row A0 reliability (rule line 6 and the secondary list): counts and A0 reliability for the rows
    dropped by (a) an identity block and (b) no DraftKings/own main line, the no-main share by role and by month."""
    sc = arms["scored"] > 0
    hm = R["has_main"]
    lab = gate.bin_label(R["pm"])
    a0 = arms["A0"]

    def block(m: np.ndarray) -> dict[str, Any]:
        if not m.any():
            return {"n": 0}
        return {
            "n": int(m.sum()),
            "calibration_in_the_large_a0": float((R["hit"][m] - a0[m]).mean()),
            "a0_gap_by_bin": {
                b: {
                    "n": int((m & (lab == b)).sum()),
                    "gap": float((R["hit"] - a0)[m & (lab == b)].mean()),
                }
                for b in BIN_NAMES
                if (m & (lab == b)).any()
            },
        }

    month = gate.month_start(R["date"]).astype(str)
    bench = R["bench"] == 1
    return {
        "identity_block_rows": block(hm & ~sc),
        "no_main_line_rows": block(~hm),
        "no_main_share_by_role": {
            "starter": float((~hm)[~bench].mean()),
            "bench": float((~hm)[bench].mean()),
        },
        "no_main_share_by_month": {mo: float((~hm)[month == mo].mean()) for mo in np.unique(month)},
        "note": "CAL-rung's served object is undefined on rows without a main line",
    }


def pipeline(
    fr: dict[str, Any],
    k_null: int,
    out: Path,
    real: bool,
    data_root: Path,
    repro: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    arms: dict[str, Any] = {}
    info: dict[str, Any] = {}
    for b in BOOKS:
        arms[b], info[b] = run_book(fr["rungs"][b], fr["legs"], k_null, b)
    pre = preflight(fr, info[PRIMARY], info["fanduel"], arms[PRIMARY], arms["fanduel"])
    repro = repro or {"skipped": "synthetic labels"}
    dk = score(fr["rungs"][PRIMARY], arms[PRIMARY], "draftkings")
    fd = score(fr["rungs"]["fanduel"], arms["fanduel"], "fanduel")
    ev = evaluate(dk, fd)
    shas = {b: save_arrays(out, b, fr["rungs"][b], arms[b]) for b in BOOKS}
    res = {
        "rule": "docs/prereg/ALT_RUNG_CAL.md",
        "real_labels": real,
        "k_null": k_null,
        "meta": fr["meta"],
        "preflight": pre,
        "t493_reproduction": repro,
        "draftkings": dk,
        "fanduel": fd,
        "dropped": {b: dropped_report(fr["rungs"][b], arms[b]) for b in BOOKS},
        "evaluation": ev,
        "blocks": {b: info[b] for b in BOOKS},
        "arrays_sha256": shas,
        "wall_seconds": round(time.time() - t0, 1),
    }
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'} word {ev['word']}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--truth", choices=("oracle", "a0"), default="oracle")
    ap.add_argument("--k", type=int, default=K_NULL)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        blobs = rule.require_ready(a.sha)
        _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
    fr = build_frames(a.data_root)
    fr["meta"]["inputs_verified"] = verify_inputs(fr["meta"], rule.declared())
    if a.cmd == "check":
        # label-free: fills synthetic labels only to drive the walk-forward bookkeeping, then discards them
        attach_synthetic_labels(fr, "a0")
        arms, info = {}, {}
        for b in BOOKS:
            arms[b], info[b] = run_book(fr["rungs"][b], fr["legs"], 0, b)
        pre = preflight(fr, info[PRIMARY], info["fanduel"], arms[PRIMARY], arms["fanduel"])
        print(json.dumps(pre, indent=1, default=str))
        return 0
    if a.cmd == "rehearse":
        attach_synthetic_labels(fr, a.truth)
        out = a.out or (rule.ROOT / OUT_REHEARSAL / a.truth)
        pipeline(fr, a.k, out, False, a.data_root)
        return 0
    # real run: counts first (label-free walk-forward bookkeeping on synthetic labels), then the real labels
    attach_synthetic_labels(fr, "a0")
    arms0, info0 = {}, {}
    for b in BOOKS:
        arms0[b], info0[b] = run_book(fr["rungs"][b], fr["legs"], 0, b)
    preflight(fr, info0[PRIMARY], info0["fanduel"], arms0[PRIMARY], arms0["fanduel"])
    _log("preflight equalities hold; reading DraftKings leg labels only for the T493 reproduction")
    attach_leg_labels(fr, a.data_root)
    _, info_leg = run_book(fr["rungs"][PRIMARY], fr["legs"], 0, PRIMARY)
    repro = check_t493(info_leg, rule.ROOT / SELECT_JSON)
    _log(f"CAL-main reproduces T493 ({repro['blocks']} blocks); reading rung labels")
    attach_real_labels(fr, a.data_root)
    out = a.out or (rule.ROOT / OUT_REAL)
    pipeline(fr, a.k, out, True, a.data_root, repro)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
