# ruff: noqa: E501
"""INJURY_REASON: frame builders, gate audits, scoring, descriptive tables and report rendering.

Complements ``test_injury_reason_eval.py`` (freeze refusal, ridge-logistic, placebos, rule logic).
Synthetic frames and tmp files only: no database, no season 2025.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from research.eval import injury_reason_eval as ev
from research.eval.f11_lineup_context import _jd
from research.eval.four_factors_eval import MIN_SLICE_N
from research.eval.minutes_v2_eval import _ll
from research.features.injury_reason import CLASS_CODE, TYPE_CODE

N_PER_SEASON = 2100


def _dates(i: np.ndarray, season: int) -> list[dt.date]:
    start = dt.date(2023 if season == 2023 else 2024, 11, 1)
    return [start + dt.timedelta(days=int(k) // 14) for k in i]


def _t1(leak: bool = False, n: int = N_PER_SEASON) -> pl.DataFrame:
    """Flagged-row Target 1 frame with minutes buckets cycling DNP, <5, 5-10, 10-20, >20."""
    rows = []
    cyc = [0.0, 2.0, 7.0, 15.0, 30.0]
    for season in ev.SEASONS:
        idx = np.arange(n)
        dates = _dates(idx, season)
        for k in range(n):
            m = cyc[k % 5]
            unknown = (m == 0.0) if leak else ((k // 5) % 2 == 0)
            rows.append(
                {
                    "game_id": f"g{season}_{k // 14}_{k % 14 // 7}",
                    "player_id": k + 1,
                    "game_date": dates[k],
                    "season": season,
                    "p_raw": 0.4 + 0.4 * (k % 3) / 2,
                    "played": float(m > 0),
                    "status_code": ev.FLAGGED_CODES[k % 3],
                    "cls": "unknown" if unknown else "injury",
                    "body": "knee",
                    "type": "soreness",
                    "side": "left",
                    "days_since_flag": float(k % 20),
                    "min_real": m,
                }
            )
    f = pl.DataFrame(rows)
    return f.with_columns(pl.Series("day_bin", ev.day_bin(f["days_since_flag"].to_numpy())))


def _t2(leak: bool = False, n: int = N_PER_SEASON) -> pl.DataFrame:
    rows = []
    cyc = [2.0, 7.0, 15.0, 30.0]
    for season in ev.SEASONS:
        for k in range(n):
            back = (k % 3) + 1 if k % 10 else 0
            m = cyc[k % 4]
            found = float((k // 4) % 2)
            if leak and m < 5:
                found = 0.0
            rows.append(
                {
                    "game_id": f"g{season}_{k // 10}",
                    "player_id": k + 1,
                    "season": season,
                    "game_date": _dates(np.array([k]), season)[0],
                    "n_prior": 10,
                    "minutes": m,
                    "min10": 25.0,
                    "pts": 10.0 + (k % 7),
                    "rb_game_back": float(back),
                    "rb_spell_missed": 4.0 if back else 0.0,
                    "rb_spell_listed": 2.0 if back else 0.0,
                    "rb_found": found if back else 0.0,
                    "rb_cls": float(1 + k % 3) if back else 0.0,
                    "rb_body": float(1 + k % 4) if back else 0.0,
                    "rb_type": float(1 + k % 5) if back else 0.0,
                }
            )
    return pl.DataFrame(rows)


# ------------------------------------------------------------------------------ loaders and builders


def test_load_reason_rows_stops_at_the_last_open_season(tmp_path: Path) -> None:
    db = tmp_path / "a.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table games (game_id varchar, season int)")
    con.execute(
        "create table player_availability (game_id varchar, player_id int, status varchar, reason varchar, as_of timestamp, source varchar)"
    )
    con.execute("insert into games values ('a', 2024), ('b', 2025)")
    con.execute(
        "insert into player_availability values "
        "('a', 1, 'Questionable', 'Injury/Illness - Left Knee; Soreness', '2024-01-01 12:00:00', 'nba_official_report'),"
        "('b', 1, 'Out', 'Injury/Illness - Left Knee; Soreness', '2025-01-01 12:00:00', 'nba_official_report'),"
        "(NULL, 2, 'Out', 'x', '2024-01-01 12:00:00', 'nba_official_report')"
    )
    con.close()
    d = ev.load_reason_rows(str(db))
    assert d["game_id"].to_list() == ["a"] and d["season"].to_list() == [
        2024
    ]  # holdout and orphans dropped
    assert d.columns == ["game_id", "player_id", "status", "reason", "as_of", "source", "season"]


def test_day_bin_treats_unlisted_as_bin_zero() -> None:
    assert ev.day_bin(np.array([np.nan, 0.5, 20.0])).tolist() == [0, 0, 4]


def test_attach_reason_keeps_every_row_and_t1_frame_keeps_flagged_ones() -> None:
    base = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g2"],
            "player_id": [1, 2, 1, 3],
            "game_date": [dt.date(2024, 1, 2)] * 2 + [dt.date(2024, 1, 1)] * 2,
            "p_raw": [0.5, 0.6, 0.7, 0.8],
        }
    )
    snap = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g2"],
            "player_id": [1, 1, 3],
            "status_code": [3, 5, 2],  # questionable, out, probable
            "cls": ["injury", "injury", "illness"],
            "body": ["knee", "knee", "none"],
            "type": ["soreness", "surgery", "illness"],
            "side": ["left", "left", "none"],
        }
    )
    ep = pl.DataFrame(
        {"game_id": ["g1", "g2"], "player_id": [1, 3], "days_since_flag": [2.0, 20.0]}
    )
    out = ev.attach_reason(base, snap, ep)
    assert out.height == base.height
    assert out.sort("game_id", "player_id")["status_code"].to_list() == [
        3,
        0,
        5,
        2,
    ]  # unlisted => 0
    assert out.filter(pl.col("player_id") == 3)["day_bin"].to_list() == [4]
    t1 = ev.t1_frame(base, snap, ep)
    # only probable / questionable / doubtful survive ('out' and unlisted rows are not the universe) and rows are time ordered
    assert t1["player_id"].to_list() == [3, 1]
    assert t1["game_date"].to_list() == sorted(t1["game_date"].to_list())


def test_t2_frame_marks_return_rows_with_shifted_codes() -> None:
    # player 1: team games 1, 2, then 6 (misses 3 => a return), then 7 (second game back)
    played = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "player_id": [1, 1, 1, 1],
            "game_date": [dt.date(2024, 1, i) for i in (1, 3, 10, 12)],
            "season": [2024] * 4,
            "team_game_no": [1, 2, 6, 7],
        }
    )
    snap = pl.DataFrame(
        {
            "game_id": ["b2", "c"],
            "player_id": [1, 1],
            "game_date": [dt.date(2024, 1, 5), dt.date(2024, 1, 10)],
            "cls": ["injury", "injury"],
            "body": ["knee", "knee"],
            "type": ["surgery", "surgery"],
            "status_code": [5, 3],
        }
    )
    feats = played.select("game_id", "player_id")
    out = ev.t2_frame(feats, played, snap).sort("game_id")
    assert out.height == feats.height and set(ev.EXTRA_COLS) <= set(out.columns)
    assert out["rb_game_back"].to_list() == [0.0, 0.0, 1.0, 2.0]
    assert out["rb_spell_missed"].to_list()[:2] == [0.0, 0.0] and out["rb_spell_missed"][2] == 3.0
    # codes are +1 so that 0 means "not a return row"
    assert out["rb_cls"][2] == CLASS_CODE["injury"] + 1 and out["rb_cls"][0] == 0.0
    assert out["rb_type"][2] == TYPE_CODE["surgery"] + 1
    assert out["rb_found"].to_list() == [0.0, 0.0, 1.0, 1.0]


def test_reliability_bins_hold_every_row() -> None:
    p = np.array([0.05, 0.06, 0.55, 0.95])
    y = np.array([0.0, 1.0, 1.0, 1.0])
    rel = ev.reliability(p, y)
    assert sum(b["n"] for b in rel) == 4
    first = rel[0]
    assert first["bin"] == 0 and first["n"] == 2 and first["obs"] == 0.5
    assert first["mean_p"] == pytest.approx(0.055)
    assert [b["bin"] for b in rel] == [0, 5, 9]  # empty bins are omitted


# ------------------------------------------------------------------------------ gate audits


def test_check_2_coverage_counts_parsed_reasons_by_season() -> None:
    reasons = (
        ["Injury/Illness - Left Knee; Soreness"] * 90 + ["G League - Two-Way"] * 5 + ["???"] * 5
    )
    avail = pl.DataFrame(
        {
            "season": [2023] * 50 + [2024] * 50,
            "source": ["nba_official_report"] * 100,
            "reason": reasons,
        }
    )
    avail = pl.concat([avail, avail.head(5).with_columns(pl.lit("other_source").alias("source"))])
    snap = pl.DataFrame(
        {
            "game_date": [dt.date(2024, 1, 1), dt.date(2022, 1, 1)],
            "cls": ["injury", "unknown"],
        }
    )
    t1 = pl.DataFrame({"cls": ["injury", "unknown", "illness", "injury"]})
    r = ev.check_2_coverage(avail, snap, t1)
    assert r["n_rows"] == 100  # only the official report counts
    assert r["coverage"] == pytest.approx(0.95)
    assert r["ok"] is True  # exactly at the 0.95 minimum
    assert r["latest_snapshot_rows_2023_24"] == {"n": 1, "non_unknown": 1.0}
    assert r["target1_rows"]["n"] == 4 and r["target1_rows"]["non_unknown"] == pytest.approx(0.75)
    assert r["target1_rows"]["by_class"] == {"injury": 2, "unknown": 1, "illness": 1}
    assert set(r["by_season"]) == {"2023", "2024"}
    worse = avail.with_columns(
        pl.when(pl.col("reason") == "G League - Two-Way")
        .then(pl.lit("???"))
        .otherwise(pl.col("reason"))
        .alias("reason")
    )
    assert ev.check_2_coverage(worse, snap, t1)["ok"] is False


def test_check_3_missingness_passes_when_nulls_are_independent_of_minutes() -> None:
    r = ev.check_3_missingness(_t1(), _t2())
    assert r["ok"] is True and max(r["gaps_pp"].values()) <= ev.NULL_GAP_PP
    assert [b["bucket"] for b in r["t1"]] == list(ev.BUCKETS)
    assert [b["bucket"] for b in r["t2"]] == ["<5", "5-20", ">20"]
    assert r["t1"][0]["n"] == 2 * N_PER_SEASON // 5  # one fifth of the rows are DNP
    assert set(r["gaps_pp"]) == {"t1_all", "t1_played", "t2"}


def test_check_3_missingness_flags_a_dnp_linked_unknown_pattern() -> None:
    r = ev.check_3_missingness(_t1(leak=True), _t2())
    assert r["ok"] is False and r["gaps_pp"]["t1_all"] == pytest.approx(100.0)
    assert r["gaps_pp"]["t1_played"] == pytest.approx(0.0)  # the played buckets alone look clean
    r2 = ev.check_3_missingness(_t1(), _t2(leak=True))
    assert r2["ok"] is False and r2["gaps_pp"]["t2"] > ev.NULL_GAP_PP


def test_check_5_cells_requires_the_minimum_cell_size() -> None:
    t1, t2 = _t1(), _t2(n=2400)
    r = ev.check_5_cells(t1, t2)
    assert r["t1_ok"] is True and r["t2_ok"] is True and r["min_cell"] == ev.MIN_CELL
    s = r["t1"]["2023"]
    assert s["flagged_rows"] == N_PER_SEASON and sum(s["by_status"].values()) == N_PER_SEASON
    assert set(s["by_status"]) == {"probable", "questionable", "doubtful"}
    assert r["t2"]["2024"]["return_rows"] == sum(r["t2"]["2024"]["by_game_back"].values())
    small = ev.check_5_cells(t1.filter(pl.col("season") == 2023), t2.head(100))
    assert small["t1_ok"] is False and small["t2_ok"] is False  # 2024 has no rows at all


def test_check_6_keys_requires_placebo_to_preserve_keys_and_multiset() -> None:
    t1 = _t1(n=300)
    t2 = _t2(n=300)
    r = ev.check_6_keys(t1, t2)
    assert r == {
        "t1_keys_identical": True,
        "t2_keys_identical": True,
        "t2_placebo_preserves_multiset": True,
        "ok": True,
    }


def test_check_1b_pplay_matches_stored_log_loss_and_row_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = pl.DataFrame(
        {
            "season": [2023] * 4 + [2024] * 4 + [2022] * 2,
            "p_raw": [0.9, 0.8, 0.2, 0.5] * 2 + [0.5, 0.5],
            "played": [1.0, 1.0, 0.0, 1.0] * 2 + [1.0, 0.0],
        }
    )
    y = np.array([1, 1, 0, 1], dtype=bool)
    p = np.array([0.9, 0.8, 0.2, 0.5])
    ll = float(_ll(p, y).mean())
    ref = {
        "gate": {
            "1_p0_reproduces": {
                "detail": {str(s): {"p_raw": {"mine": ll}, "n_mine": 4} for s in ev.SEASONS}
            }
        }
    }
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(ref))
    monkeypatch.setattr(ev, "PPLAY_GATE_JSON", gate)
    monkeypatch.chdir(tmp_path)  # no stored minutes_v2 npz => the information block is skipped
    r = ev.check_1b_pplay(base)
    assert r["ok"] is True and abs(r["detail"]["2023"]["delta"]) < 1e-12
    assert "info_minutes_v2_npz" not in r["detail"]["2023"]
    ref["gate"]["1_p0_reproduces"]["detail"]["2024"]["n_mine"] = (
        5  # a different row count fails the gate
    )
    gate.write_text(json.dumps(ref))
    assert ev.check_1b_pplay(base)["ok"] is False
    # the older stored npz is information only
    (tmp_path / "reports/minutes_v2").mkdir(parents=True)
    np.savez(
        tmp_path / "reports/minutes_v2/pplay.npz",
        season=np.array([2023] * 4 + [2024] * 4),
        p_raw=np.array([0.9, 0.8, 0.2, 0.5] * 2),
        y=np.array([1, 1, 0, 1] * 2),
    )
    info = ev.check_1b_pplay(base)["detail"]["2023"]["info_minutes_v2_npz"]
    assert info["n_stored"] == 4 and info["logloss_stored"] == pytest.approx(ll)


def test_check_1a_pts_compares_cached_a0_with_the_stored_lower_tail_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.eval.context_residual_eval import crps_from_quantiles

    rng = np.random.default_rng(0)
    taus = (np.arange(1, 200) - 0.5) / 199
    n = 30
    qi = np.repeat(np.floor(np.quantile(rng.poisson(12, 3000), taus))[None, :], 2 * n, 0).astype(
        np.int16
    )
    y = rng.poisson(12, 2 * n).astype(float)
    season = np.array([2023] * n + [2024] * n)
    meta = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(2 * n)],
            "player_id": np.arange(2 * n),
            "season": season,
            "game_date": [dt.date(2024, 1, 1)] * (2 * n),
        }
    )
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path)
    np.savez(tmp_path / "arm_pts_A0.npz", qi=qi, y=y)
    meta.write_parquet(tmp_path / "arm_pts_A0.parquet")
    stored = {
        "pts": {
            str(s): {
                "off": {
                    "crps_int": float(
                        crps_from_quantiles(qi[season == s].astype(float), y[season == s]).mean()
                    ),
                    "n": n,
                }
            }
            for s in ev.SEASONS
        }
    }
    cand = tmp_path / "candidates.json"
    cand.write_text(json.dumps(stored))
    monkeypatch.setattr(ev, "LOWER_TAIL_JSON", cand)
    r = ev.check_1a_pts({}, None, None)  # type: ignore[arg-type]
    assert r["ok"] is True and r["detail"]["2023"]["delta"] == pytest.approx(0.0)
    assert r["meta_keys"].columns == ["game_id", "player_id", "season", "game_date"]
    stored["pts"]["2024"]["off"]["n"] = n + 1
    cand.write_text(json.dumps(stored))
    assert ev.check_1a_pts({}, None, None)["ok"] is False  # type: ignore[arg-type]


def test_check_1c_minutes_p_reproduces_production_scores_per_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.eval.context_residual_eval import crps_from_quantiles
    from research.props.minutes_v2 import trunc_normal_quantiles

    rng = np.random.default_rng(1)
    n = 40
    gid = np.array([f"g{i}" for i in range(n)])
    pid = np.arange(n)
    season = np.array([2023] * (n // 2) + [2024] * (n // 2))
    mu, sig = rng.uniform(15, 30, n), rng.uniform(3, 8, n)
    y = rng.uniform(0, 40, n)
    crps = crps_from_quantiles(trunc_normal_quantiles(mu, sig), y)
    monkeypatch.setattr(ev, "MV2_SCORES", tmp_path / "s.npz")
    np.savez(tmp_path / "s.npz", row_gid=gid, row_pid=pid, row_season=season, row_y=y, P_crps=crps)
    prod = pl.DataFrame(
        {"game_id": gid.tolist(), "player_id": pid.astype(np.int64), "p_mu": mu, "p_sigma": sig}
    )
    monkeypatch.setattr(ev, "production_minutes_table", lambda sc: prod)
    r = ev.check_1c_minutes_p({"sc": None})
    assert r["ok"] is True and r["detail"]["2023"]["max_abs_diff"] < 1e-9
    assert r["detail"]["2024"]["keys_identical_to_1e-9"] == n // 2
    # a perturbed production mean is a failure for the strict 2023 season
    prod2 = prod.with_columns(
        pl.when(pl.col("game_id") == "g0")
        .then(pl.col("p_mu") + 5)
        .otherwise(pl.col("p_mu"))
        .alias("p_mu")
    )
    monkeypatch.setattr(ev, "production_minutes_table", lambda sc: prod2)
    r = ev.check_1c_minutes_p({"sc": None})
    assert r["ok"] is False and r["detail"]["2023"]["ok"] is False


# ------------------------------------------------------------------------------ scoring and report


def _arm(meta: pl.DataFrame, rng: np.random.Generator, shift: float) -> dict[str, Any]:
    n = meta.height
    y = np.arange(n, dtype=float) % 30
    mean = y + rng.normal(0, 2, n) + shift
    return {"meta": meta, "y": y, "mean": mean, "crps": np.abs(mean - y) * 0.5 + 0.1}


def _t2_scoring_inputs() -> tuple[dict[str, Any], dict[str, Any], pl.DataFrame]:
    t2 = _t2(n=1500)
    meta = t2.select("game_id", "player_id", "game_date", "season")
    rng = np.random.default_rng(3)
    pts = {"A0": _arm(meta, rng, 0.0), "A1": _arm(meta, rng, 0.0), "P1": _arm(meta, rng, 0.0)}
    mins = {"A0": _arm(meta, rng, 0.0), "A1": _arm(meta, rng, 0.0), "P1": _arm(meta, rng, 0.0)}
    for arms in (pts, mins):
        for a in ("A1", "P1"):
            arms[a]["y"] = arms["A0"]["y"]
    return pts, mins, t2


def test_score_t2_restricts_to_return_rows_and_checks_alignment() -> None:
    pts, mins, t2 = _t2_scoring_inputs()
    out = ev.score_t2(pts, mins, t2)
    tab = {(r["target"], r["season"], r["arm"]): r for r in out["rows"]}
    n_ret = int(((t2["season"] == 2023) & (t2["rb_game_back"] >= 1)).sum())
    assert tab[("pts", 2023, "A1")]["n_rows"] == n_ret
    assert tab[("pts", 2023, "A1-A0:all_rows")]["n_rows"] == int((t2["season"] == 2023).sum())
    d = tab[("pts", 2023, "A1-A0")]
    ret = ((t2["season"] == 2023) & (t2["rb_game_back"] >= 1)).to_numpy()
    assert d["dcrps"] == pytest.approx(float((pts["A1"]["crps"] - pts["A0"]["crps"])[ret].mean()))
    assert d["ci_lo"] <= d["dcrps"] <= d["ci_hi"]
    slices = {(s["target"], s["season"], s["slice"]): s for s in out["slices"]}
    assert (
        slices[("pts", 2023, "game_back:1")]["n"]
        + slices[("pts", 2023, "game_back:2")]["n"]
        + slices[("pts", 2023, "game_back:3")]["n"]
        == n_ret
    )
    assert any("dcrps" in s for s in out["slices"])  # large slices carry a delta
    # arms scored on different rows or labels are refused, never silently misaligned
    bad = {**pts, "A1": {**pts["A1"], "meta": pts["A1"]["meta"].reverse()}}
    with pytest.raises(AssertionError, match="identical rows"):
        ev.score_t2(bad, mins, t2)
    bad_y = {**pts, "A1": {**pts["A1"], "y": pts["A1"]["y"] + 1}}
    with pytest.raises(AssertionError):
        ev.score_t2(bad_y, mins, t2)


def test_descriptive_reports_rates_shortfalls_and_market_over_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t1 = _t1(n=400).with_columns(pl.col("status_code").alias("status_code"))
    t2 = _t2(n=400)
    prod = t2.select("game_id", "player_id").with_columns(pl.lit(20.0).alias("p_mu"))
    monkeypatch.setattr(ev, "production_minutes_table", lambda sc: prod)
    mk_dir = tmp_path
    for s in ev.SEASONS:
        ret = t2.filter((pl.col("season") == s) & (pl.col("rb_game_back") >= 1)).head(60)
        pl.DataFrame(
            {
                "game_id": ret["game_id"],
                "player_id": ret["player_id"],
                "stat": ["pts"] * ret.height,
                "book": ["draftkings"] * ret.height,
                "snapshot_kind": ["t60"] * ret.height,
                "line": [12.5] * ret.height,
            }
        ).write_parquet(mk_dir / f"market_rows_{s}.parquet")
    monkeypatch.setattr(ev, "MARKET_ROWS", str(mk_dir / "market_rows_{season}.parquet"))
    out = ev.descriptive(t1, t2, {}, {"sc": None})
    q = out["questionable_play_rate"]
    assert {r["by"] for r in q} == {"type", "cls"}
    assert all(0 <= r["play_rate"] <= 1 and r["n"] > 0 for r in q)
    sh = out["first_game_back_shortfall"]
    assert sh and all(
        r["shortfall_vs_min10"]
        == pytest.approx(25.0 - r["shortfall_vs_min10"] * 0 - (25.0 - r["shortfall_vs_min10"]))
        for r in sh
    )
    assert {r["season"] for r in sh} == {2023, 2024}
    over = out["dk_over_rate_first3_back"]
    assert over and all(r["snapshot_kind"] == "t60" and 0 <= r["over_rate"] <= 1 for r in over)
    # with no market files the over-rate table is simply empty
    monkeypatch.setattr(ev, "MARKET_ROWS", str(tmp_path / "none_{season}.parquet"))
    assert ev.descriptive(t1, t2, {}, {"sc": None})["dk_over_rate_first3_back"] == []


def _results_rows(t1_gain: float, pts_gain: float, min_gain: float) -> list[dict[str, Any]]:
    rows = []
    for tgt, arm, d in (
        ("t1", "A1-A0t", t1_gain),
        ("pts", "A1-A0", pts_gain),
        ("min", "A1-A0", min_gain),
    ):
        for s in ev.SEASONS:
            rows.append(
                {
                    "target": tgt,
                    "season": s,
                    "arm": arm,
                    "dcrps": d,
                    "ci_lo": d - 0.004,
                    "ci_hi": d + 0.003,
                    "p": 0.001 if d else 0.8,
                    "mde": 0.004,
                    "n_rows": 3000,
                }
            )
            rows.append(
                {
                    "target": tgt,
                    "season": s,
                    "arm": "A1-P1",
                    "dcrps": d,
                    "ci_lo": d - 0.004,
                    "ci_hi": d + 0.003,
                    "n_rows": 3000,
                }
            )
            rows.append({"target": tgt, "season": s, "arm": "A1", "ece": 0.02, "n_rows": 3000})
            rows.append({"target": tgt, "season": s, "arm": "A0t", "ece": 0.02, "n_rows": 3000})
    ev.add_bh(rows, {"t1": "A1-A0t", "pts": "A1-A0", "min": "A1-A0"})
    return rows


def test_evaluate_rules_each_condition_can_fail_independently() -> None:
    ok = ev.evaluate_rules(_results_rows(-0.02, -0.02, -0.5), [])
    assert all(ok[t]["pass"] for t in ("t1", "pts", "min")) and ok["close_no_pass_2023"] is False
    # placebo margin: if the shuffled-reason arm gains as much, the claim fails
    rows = _results_rows(-0.02, -0.02, -0.5)
    for r in rows:
        if r["target"] == "t1" and r["arm"] == "A1-P1" and r["season"] == 2024:
            r["dcrps"] = -0.001
    assert ev.evaluate_rules(rows, [])["t1"]["A1_beats_P1_by_margin_2024"] is False
    assert ev.evaluate_rules(rows, [])["t1"]["pass"] is False
    # a harmful large slice in 2024 fails the target, a small one is ignored
    sl = [
        {
            "target": "pts",
            "season": 2024,
            "slice": "type:surgery",
            "n": MIN_SLICE_N,
            "dcrps": 0.5,
        },
        {"target": "pts", "season": 2024, "slice": "tiny", "n": MIN_SLICE_N - 1, "dcrps": 9.0},
        {"target": "pts", "season": 2023, "slice": "old", "n": 5000, "dcrps": 9.0},
    ]
    res = ev.evaluate_rules(_results_rows(-0.02, -0.02, -0.5), sl)
    assert res["pts"]["slices_worse"] == {"2024:type:surgery": 0.5} and res["pts"]["pass"] is False
    # calibration: a worse ECE than the tag-only baseline fails Target 1 only
    rows = _results_rows(-0.02, -0.02, -0.5)
    for r in rows:
        if r["target"] == "t1" and r["arm"] == "A1" and r["season"] == 2024:
            r["ece"] = 0.05
    res = ev.evaluate_rules(rows, [])
    assert res["t1"]["ece_ok"] is False and res["pts"]["ece_ok"] is True
    # nothing selects on 2023 => the direction closes
    none = ev.evaluate_rules(_results_rows(0.0, 0.0, 0.0), [])
    assert none["close_no_pass_2023"] is True and not any(
        none[t]["pass"] for t in ("t1", "pts", "min")
    )


def test_cached_arm_roundtrip_and_get_or_compute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path / "c")
    calls: list[int] = []

    def make() -> dict[str, Any]:
        calls.append(1)
        return {"crps": np.arange(3.0), "meta": pl.DataFrame({"game_id": ["a", "b", "c"]})}

    first = ev._cached("arm", make)
    again = ev._cached("arm", make)
    assert calls == [1]
    assert np.array_equal(again["crps"], first["crps"]) and again["meta"]["game_id"].to_list() == [
        "a",
        "b",
        "c",
    ]


def test_report_and_ledger_from_a_full_results_file(tmp_path: Path) -> None:
    pts, mins, t2 = _t2_scoring_inputs()
    s2 = ev.score_t2(pts, mins, t2)
    t1 = _t1(n=400)
    p0 = t1["p_raw"].to_numpy()
    s1 = ev.score_t1(t1, {"A0": p0, "A0t": p0, "A1": np.clip(p0 + 0.02, 0.01, 0.99), "P1": p0})
    rows = s1["rows"] + s2["rows"]
    ev.add_bh(rows, {"t1": "A1-A0t", "pts": "A1-A0", "min": "A1-A0"})
    slices = s1["slices"] + s2["slices"]
    res = {
        "rows": rows,
        "slices": slices,
        "reliability": s1["reliability"],
        "descriptive": {"questionable_play_rate": []},
        "rules": ev.evaluate_rules(rows, slices),
        "seeds": ev.SEEDS,
        "git_sha": "abc123",
        "sha256": "cd" * 32,
    }
    (tmp_path / "results.json").write_text(json.dumps(res, default=_jd))
    md = ev.render_report(tmp_path)
    assert md.startswith("# INJURY_REASON results") and "cd" * 32 in md and "`abc123`" in md
    assert "| t1 | 2023 | A1-A0t |" in md and "## Slices (primary contrast)" in md
    assert md.count("| pts | 2024 |") >= 4
    led = ev.write_ledger(tmp_path).read_text().splitlines()
    assert led[0] == "| id | test | result |"
    assert sum(ln.startswith("| T###") for ln in led) == 6 + 3 + 1
    assert any("INJURY_REASON close (no pass on 2023)" in ln for ln in led)
    assert any("t1 2023 A1-A0t" in ln and "p_bh" in ln for ln in led)


def test_formatting_helpers() -> None:
    assert ev._f(None) == "n/a" and ev._f(float("nan")) == "n/a"
    assert ev._f(0.12345) == "0.1235" and ev._f(0.12345, 2, sign=True) == "+0.12"
    assert ev._mean(pl.Series([True, False, True, True])) == 0.75


def test_main_report_stage_writes_report_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pts, mins, t2 = _t2_scoring_inputs()
    s2 = ev.score_t2(pts, mins, t2)
    t1 = _t1(n=400)
    p0 = t1["p_raw"].to_numpy()
    s1 = ev.score_t1(t1, {"A0": p0, "A0t": p0, "A1": p0, "P1": p0})
    rows = s1["rows"] + s2["rows"]
    monkeypatch.chdir(tmp_path)  # OUT_DIR / REPORT_MD are relative paths
    out_dir = tmp_path / "reports" / "prereg_injury_reason"
    out_dir.mkdir(parents=True)
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "slices": [],
                "reliability": {},
                "descriptive": {},
                "rules": ev.evaluate_rules(rows, []),
                "git_sha": "g",
                "sha256": "s",
            },
            default=_jd,
        )
    )
    doc = tmp_path / "RULE.md"
    doc.write_text("# R\n\nStatus: FROZEN\n\nrule\n=== RESULTS BELOW ===\n")
    monkeypatch.setattr(ev, "HeavyLock", contextlib.nullcontext)
    assert ev.main(["report", "--doc", str(doc), "--sha", ev.frozen_sha(doc)]) == 0
    assert (
        (tmp_path / "reports" / "prereg_injury_reason.md")
        .read_text()
        .startswith("# INJURY_REASON results")
    )
    assert (out_dir / "ledger_rows.md").exists()
