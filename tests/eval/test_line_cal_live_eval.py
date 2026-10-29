"""LINE_CAL_LIVE harness: rule guards, look logic, statistics and verdicts (synthetic only)."""

from __future__ import annotations

import inspect
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.eval import line_cal_live_eval as ev
from research.eval import line_cal_live_gate as gate

DOC = Path(__file__).resolve().parents[2] / "docs/prereg/LINE_CAL_LIVE.md"


def _frame(games: int = 250, per: int = 30, seed: int = 0) -> ev.Frame:
    rng = np.random.default_rng(seed)
    n = games * per
    p_cal = rng.uniform(0.40, 0.60, n)
    # RAW: too low and too steep around the line (the T477 shape)
    x = np.log(p_cal / (1 - p_cal))
    p_raw = 1 / (1 + np.exp(-(3.0 * x - 0.35)))
    st = rng.choice(np.array(gate.STATS), n)
    line = rng.integers(1, 25, n) + 0.5
    return {
        "game": np.repeat(np.array([f"g{i:04d}" for i in range(games)]), per),
        "date": np.repeat(np.datetime64("2026-11-01") + np.arange(games) // 8, per),
        "stat": st,
        "pid": rng.integers(1, 400, n),
        "p_raw": p_raw,
        "p_cal": p_cal,
        "p_mkt": np.clip(p_cal + rng.normal(0, 0.02, n), 0.05, 0.95),
        "c0": np.full(n, 0.48),
        "starter": rng.random(n) < 0.78,
        "p_play": rng.uniform(0.6, 1.0, n),
        "min_sd": np.where(rng.random(n) < 0.3, np.nan, rng.uniform(1, 9, n)),
        "proj_min": rng.uniform(10, 36, n),
        "mean": line + rng.normal(-0.5, 2, n),
        "line": line,
        "x_out": rng.random(n) < 0.05,
    }


@pytest.mark.parametrize("world", list(ev.EXPECT))
def test_every_verdict_is_reachable(world: str) -> None:
    r = ev.evaluate(ev.synth_outcomes(_frame(), world, seed=7))
    assert ev.verdict(r["verdict_core"], r["games"], "look") == ev.EXPECT[world]


def test_partial_names_the_under_side() -> None:
    r = ev.evaluate(ev.synth_outcomes(_frame(), "under_gap", seed=7))
    assert "P2" in r["failing"]
    assert r["checks"]["P1"] and r["checks"]["P1b"]


def test_verdict_needs_150_games_and_a_look() -> None:
    assert ev.verdict("TRANSPORTS", 149, "backstop") == "INSUFFICIENT_DATA"
    assert ev.verdict("TRANSPORTS", 210, "look") == "TRANSPORTS"
    with pytest.raises(ValueError):
        ev.verdict("TRANSPORTS", 210, "not_reached")


def test_rule_hash_matches_awk_shasum() -> None:
    if shutil.which("awk") is None or shutil.which("shasum") is None:
        pytest.skip("awk/shasum missing")
    out = subprocess.run(
        f"awk '/^{ev.MARKER}$/{{exit}} {{print}}' '{DOC}' | shasum -a 256",
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    assert ev.rule_text_sha(DOC.read_text()) == out


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def test_freeze_commit_from_git(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    doc = tmp_path / ev.DOC_REL
    doc.parent.mkdir(parents=True)
    body = "# x\n\nStatus: **FROZEN by the main session.**\nrule text\n" + ev.MARKER + "\nresults\n"
    doc.write_text(body)
    sha = ev.rule_text_sha(body)
    _git(tmp_path, "add", ".")
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", f"prereg({ev.RULE}): {sha}"],
        check=True,
        capture_output=True,
        env={"GIT_COMMITTER_DATE": "2026-10-30T23:30:00-05:00", "PATH": "/usr/bin:/bin"},
    )
    commit, fz = ev.freeze_commit(tmp_path, sha)
    assert len(commit) == 40
    assert fz == date(2026, 10, 31)  # 23:30 CDT is 00:30 ET the next day
    ev.check_doc(tmp_path, sha)
    with pytest.raises(SystemExit):
        ev.freeze_commit(tmp_path, "0" * 64)


def test_check_doc_refuses_a_draft(tmp_path: Path) -> None:
    doc = tmp_path / ev.DOC_REL
    doc.parent.mkdir(parents=True)
    body = "# x\n\nStatus: **DRAFT**\n" + ev.MARKER + "\n"
    doc.write_text(body)
    with pytest.raises(SystemExit):
        ev.check_doc(tmp_path, ev.rule_text_sha(body))


def test_look_is_first_date_with_200_games_and_5000_legs() -> None:
    counts = [(date(2026, 11, 1) + timedelta(days=i), 8, 250) for i in range(40)]
    start = date(2026, 11, 3)
    lk = ev.find_look(counts, start, date(2026, 12, 31))
    assert lk.kind == "look"
    assert lk.games >= 200 and lk.legs >= 5000
    # 25 dates of 8 games starting 11-03 -> 11-27
    assert lk.date == date(2026, 11, 27)
    assert ev.find_look(counts, start, date(2026, 11, 20)).kind == "not_reached"
    thin = [(d, 1, 10) for d, _, _ in counts]
    lb = ev.find_look(thin, start, date(2027, 2, 1))
    assert lb.kind == "backstop" and lb.date == ev.BACKSTOP


def test_look_never_counts_today_or_dates_before_the_window() -> None:
    counts = [(date(2026, 11, 1), 300, 9000), (date(2026, 11, 2), 300, 9000)]
    assert ev.find_look(counts, date(2026, 11, 2), date(2026, 11, 2)).kind == "not_reached"
    assert ev.find_look(counts, date(2026, 11, 2), date(2026, 11, 3)).date == date(2026, 11, 2)


def test_boot_is_seeded_and_brackets_the_point() -> None:
    rng = np.random.default_rng(4)
    v = rng.normal(-0.01, 0.05, 3000)
    g = np.repeat(np.arange(100), 30)
    a, b = ev.Boot(g, 0, 500).mean(v), ev.Boot(g, 0, 500).mean(v)
    assert a == b
    assert a["lo"] < a["point"] < a["hi"]
    assert a["clusters"] == 100
    assert ev.Boot(g, 0, 500).mean(v - 1.0)["p_lt0"] < 0.01


def test_bh_q_values() -> None:
    q = ev.bh([0.01, 0.04, 0.03, 0.5])
    assert q == pytest.approx([0.04, 0.0533333, 0.0533333, 0.5], abs=1e-6)


def test_d2_components_sum_to_the_error() -> None:
    F = ev.synth_outcomes(_frame(60, 20), "transport", seed=3)
    r = ev.d2(F, gate.side_under(F["p_raw"]))
    assert r["mean_miss_minutes"] + r["mean_miss_rate"] == pytest.approx(r["mean_error"], abs=1e-9)


def test_status_and_gate_loader_never_read_an_outcome() -> None:
    src = inspect.getsource(gate.load_legs) + inspect.getsource(ev.cmd_status)
    for bad in ("s.y", " y,", "crps", "brier", "log_loss", "attach_outcomes"):
        assert bad not in src


def test_side_partition_is_fixed_by_raw() -> None:
    F = ev.synth_outcomes(_frame(60, 20), "transport", seed=5)
    r = ev.evaluate(F)
    n_under = int(gate.side_under(F["p_raw"]).sum())
    assert r["side"]["under"]["brier_cal_minus_raw"]["n"] == n_under
    assert r["side"]["over"]["brier_cal_minus_raw"]["n"] == r["n"] - n_under
