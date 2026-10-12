# ruff: noqa: E501
"""LINE_CALIBRATION harness on synthetic pairs frames (no real data, no result is read)."""

from __future__ import annotations

import datetime as dt
import inspect
from pathlib import Path

import numpy as np
import polars as pl
import pytest

import research.eval.line_calibration_eval as lc
from research.eval import line_calibration_rule as rule


def synth(season: int, seed: int, games_per_month: int = 25, months: int = 4) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    g = 0
    for mo in range(months):
        y, m = (season, 11 + mo) if 11 + mo <= 12 else (season + 1, 11 + mo - 12)
        for d in range(games_per_month):
            day = dt.date(y, m, 1 + d)
            tip = dt.datetime(y, m, 1 + d, 23, 30)
            gid = f"002{season % 100}{g:05d}"
            g += 1
            for pid in range(1, 7):
                for stat in lc.STATS:
                    mean = float(rng.uniform(1.0, 20.0))
                    sd = float(np.sqrt(mean) + 0.3)
                    line = float(np.floor(mean + rng.normal(0, 1))) + 0.5
                    pm = float(np.clip(1 - 0.5 * (1 + np.tanh((line - mean) / sd)), 0.01, 0.99))
                    # truth: production is biased low (strongly, so 600 fit rows find it)
                    pt = 1 / (1 + np.exp(-(0.6 + 0.85 * np.log(pm / (1 - pm)))))
                    over = float(rng.random() < pt)
                    for book in ("pinnacle", "draftkings", "fanduel"):
                        rows.append(
                            {
                                "game_id": gid, "player_id": pid, "stat": stat, "book": book, "snapshot_kind": "t60",
                                "snapshot_ts": tip - dt.timedelta(minutes=64), "line": line, "p_over_mult": 0.5,
                                "p_model": pm, "over": over, "tipoff_utc": tip, "starter10": float(pid <= 4),
                                "mean_int": mean, "std_int": sd, "season": season, "game_date": day,
                            }
                        )  # fmt: skip
    return pl.DataFrame(rows).with_columns(pl.col("season").cast(pl.Int32))


@pytest.fixture
def pairs_dir(tmp_path: Path) -> Path:
    for i, s in enumerate((2023, 2024)):
        synth(s, i).write_parquet(tmp_path / f"pairs_{s}.parquet")
    return tmp_path


@pytest.fixture
def small_min_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "MIN_FIT", 200)


def test_read_pairs_refuses_2025(pairs_dir: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        lc.read_pairs(pairs_dir / "pairs_2024.parquet", 2025)


def test_read_pairs_t60_two_books(pairs_dir: Path) -> None:
    d = lc.read_pairs(pairs_dir / "pairs_2023.parquet", 2023)
    assert set(d["book"].unique()) == {"pinnacle", "draftkings"}


def test_design_is_market_free() -> None:
    params = list(inspect.signature(lc.design).parameters)
    assert params == ["pm", "line", "mi", "si", "st10"]
    X = lc.design(
        np.array([0.0, 0.5, 1.0]),
        np.array([5.5] * 3),
        np.array([5.0] * 3),
        np.array([0.1] * 3),
        np.array([1.0, 0.0, 0.0]),
    )
    assert X.shape == (3, 5) and np.all(np.isfinite(X))
    assert X[0, 2] == pytest.approx(1.0)  # (5.5 - 5) / max(0.1, 0.5)
    assert list(X[:, 3]) == [0.0, 1.0, 1.0]


def test_fit_logistic_recovers_coefficients() -> None:
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(40000), rng.normal(size=40000)])
    y = (rng.random(40000) < 1 / (1 + np.exp(-(0.3 + 0.8 * X[:, 1])))).astype(float)
    b = lc.fit_logistic(X, y, ridge=0.0)
    assert b[0] == pytest.approx(0.3, abs=0.05) and b[1] == pytest.approx(0.8, abs=0.05)


def test_walk_forward_identity_in_first_block_and_fits_later(
    pairs_dir: Path, small_min_fit: None
) -> None:
    D, _ = lc.load_real(pairs_dir)
    p, info = lc.walk_forward(D, D["over"], 2023, "pinnacle", "cal")
    first = [r for r in info if r["block"] == "2023-11"]
    assert first and all(r["identity"] for r in first)
    later = [r for r in info if r["block"] == "2024-01"]
    assert later and not any(r["identity"] for r in later)
    for r in info:
        if not r["identity"]:
            assert r["last_fit_date"] < r["block"] + "-01"


def test_planted_future_identical_and_positive_control(
    pairs_dir: Path, small_min_fit: None
) -> None:
    D, _ = lc.load_real(pairs_dir)
    pf = lc.planted_future(D, lc.synthetic_labels(D, 1), 2024)
    assert pf["probes"] == 2 * 4 * 3
    assert pf["identical"] == pf["probes"] and pf["positive_control_moved"] == pf["probes"], pf[
        "bad"
    ]


def test_label_on_block_start_stays_out(pairs_dir: Path, small_min_fit: None) -> None:
    D, _ = lc.load_real(pairs_dir)
    y = lc.synthetic_labels(D, 3)
    b0 = np.datetime64("2024-01-01")
    p0, _ = lc.walk_forward(D, y, 2024, "pinnacle", "cal", only_block=b0)
    y2 = y.copy()
    y2[D["date"] == b0] = 1 - y2[D["date"] == b0]
    p1, _ = lc.walk_forward(D, y2, 2024, "pinnacle", "cal", only_block=b0)
    sc = (D["season"] == 2024) & (D["book"] == "pinnacle") & (D["mstart"] == b0)
    assert np.array_equal(p0[sc], p1[sc])


def test_stage_end_to_end_recovers_planted_bias(pairs_dir: Path, small_min_fit: None) -> None:
    D, _ = lc.load_real(pairs_dir)
    res = lc.run_stage(D, D["over"], 2024, k_null=3)
    pin = res["pinnacle"]
    assert pin["n"] == 4 * 25 * 6 * 4 and pin["identity_rows"] == 0
    assert pin["d_cal_a0"]["mean"] < 0  # the synthetic truth is biased; the map must help
    assert pin["calib"]["all"]["A0"]["mean"] < 0  # A0 too low, as planted
    assert abs(pin["calib"]["all"]["CAL"]["mean"]) < abs(pin["calib"]["all"]["A0"]["mean"])
    assert set(pin["per_stat"]) == set(lc.STATS) and all(
        "q_bh" in r for r in pin["per_stat"].values()
    )
    assert pin["null"]["k"] == 3 and np.isfinite(pin["null"]["sd"])
    assert set(res["rule"]["checks"]) and isinstance(res["rule"]["pass"], bool)


def test_shuffle_arm_is_seed_deterministic(pairs_dir: Path, small_min_fit: None) -> None:
    D, _ = lc.load_real(pairs_dir)
    a, _ = lc.walk_forward(D, D["over"], 2024, "pinnacle", "shuffle", 4)
    b, _ = lc.walk_forward(D, D["over"], 2024, "pinnacle", "shuffle", 4)
    c, _ = lc.walk_forward(D, D["over"], 2024, "pinnacle", "shuffle", 5)
    m = np.isfinite(a)
    assert np.array_equal(a[m], b[m]) and not np.array_equal(a[m], c[m])


# --------------------------------------------------------------------------- freeze and holdout refusals


SHA = "a" * 64


def _doc(tmp_path: Path) -> tuple[Path, str]:
    doc = tmp_path / "LINE_CALIBRATION.md"
    doc.write_text("# rule\nStatus: DRAFT\n" + f"\n{rule.MARKER}\nresults\n")
    return doc, rule.frozen_sha(doc)


def test_require_frozen_refuses_without_commit(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_frozen(sha, doc, run=lambda *a: "")
    with pytest.raises(RuntimeError, match="64 hex"):
        rule.require_frozen(None, doc, run=lambda *a: "")
    with pytest.raises(RuntimeError, match="hashes to"):
        rule.require_frozen(SHA, doc, run=lambda *a: "")


def _fake_git(doc: Path, sha: str, log_dirty: bool = False, row_committed: bool = True):  # type: ignore[no-untyped-def]
    def run(*args: str) -> str:
        if args[0] == "log" and args[1] == "--format=%H%x09%s":
            return f"abc123\tprereg(LINE_CALIBRATION): {sha}\n"
        if args[0] == "show":
            return doc.read_text()
        if args[0] == "status":
            return " M docs/HOLDOUT_ACCESS_LOG.md" if log_dirty else ""
        if args[0] == "log":
            return "def456\n" if row_committed else ""
        return ""

    return run


def test_require_frozen_accepts_matching_commit(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    assert rule.require_frozen(sha, doc, run=_fake_git(doc, sha)) == sha


def test_holdout_row_needs_devin_words_and_commit(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    log = tmp_path / "HOLDOUT_ACCESS_LOG.md"
    log.write_text(
        f"| 2026-10-12 | LINE_CALIBRATION report {sha[:8]} | PRE-REGISTERED | DEVIN CONFIRMED |\n"
    )
    with pytest.raises(RuntimeError, match="DEVIN CONFIRMED"):
        rule.require_holdout_row(sha, log, _fake_git(doc, sha), doc)
    log.write_text(
        f'| 2026-10-12 | LINE_CALIBRATION report {sha[:8]} | PRE-REGISTERED | DEVIN CONFIRMED "go ahead" |\n'
    )
    assert "go ahead" in rule.require_holdout_row(sha, log, _fake_git(doc, sha), doc)
    with pytest.raises(RuntimeError, match="uncommitted"):
        rule.require_holdout_row(sha, log, _fake_git(doc, sha, log_dirty=True), doc)
    with pytest.raises(RuntimeError, match="not in any commit"):
        rule.require_holdout_row(sha, log, _fake_git(doc, sha, row_committed=False), doc)


def test_report_and_select_refuse_unfrozen(pairs_dir: Path, tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    with pytest.raises(RuntimeError):
        lc.select(pairs_dir, doc, sha, tmp_path)
    with pytest.raises(RuntimeError):
        lc.report(pairs_dir, pairs_dir / "pairs_2024.parquet", doc, sha, tmp_path)
    with pytest.raises(SystemExit, match="allow-draft"):
        lc.main(["select", "--allow-draft", "--doc", str(doc)])


def test_declared_parses_frozen_lines(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(
        f"PAIRS_SHA256_2023: {'1' * 64}\nPAIRS_SHA256_2024: {'2' * 64}\nNULL_SD_PINNACLE_2024: 1.5e-05\n{rule.MARKER}\nPAIRS_SHA256_2023: {'9' * 64}\n"
    )
    d = lc.declared(doc)
    assert (
        d["pairs_sha_2023"] == "1" * 64
        and d["pairs_sha_2024"] == "2" * 64
        and d["null_sd"] == 1.5e-05
    )


def test_stage2_data_check_flags_small_season(pairs_dir: Path, small_min_fit: None) -> None:
    frames = [lc.read_pairs(pairs_dir / f"pairs_{s}.parquet", s) for s in (2023, 2024)]
    f25 = synth(2025, 9).filter(pl.col("book") != "fanduel").sort(lc.K4)
    D = lc.to_arrays([*frames, f25])
    bad = lc.stage2_data_check(D)
    assert any("< 20000" in b for b in bad)
    assert not any("identity" in b for b in bad)
