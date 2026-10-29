# ruff: noqa: E501
"""LINE_CAL_LIVE harness: rule guards, look logic, statistics and verdicts (synthetic only)."""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
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
        "fallback": rng.random(n) < 0.04,
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


# ------------------------------------------------------------------ review B1-B3, M2, M4, m6


def test_run_refuses_any_clock_but_the_real_one(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="real clock"):
        ev.cmd_run(tmp_path, tmp_path, "0" * 64, date(2027, 2, 1))


def test_main_run_refuses_a_today_override() -> None:
    assert ev.main(["run", "--sha", "0" * 64, "--today", "2027-02-01"]) == 2


def _gs(gid: str, final: bool, predicted: bool, scored: bool, tip: datetime) -> ev.GameState:
    return ev.GameState(gid, tip.date(), tip, final, predicted, scored)


def test_settlement_lists_postponed_and_unforecast_without_blocking() -> None:
    now = datetime(2026, 12, 3, 15, 0, tzinfo=UTC)
    old = datetime(2026, 11, 28, 0, 30, tzinfo=UTC)
    out = ev.classify_settlement(
        [
            _gs("ok", True, True, True, old),
            _gs("postponed", False, True, False, old),  # never final, tip 5 days ago
            _gs("no_forecast", True, False, False, old),
        ],
        now,
    )
    assert out == {
        "settled": ["ok"],
        "pending": [],
        "unplayed_or_postponed": ["postponed"],
        "unforecast": ["no_forecast"],
    }


def test_settlement_blocks_final_but_unscored_and_recent_unfinished() -> None:
    now = datetime(2026, 12, 3, 15, 0, tzinfo=UTC)
    out = ev.classify_settlement(
        [
            _gs("late_settle", True, True, False, datetime(2026, 11, 20, 0, 30, tzinfo=UTC)),
            _gs("tonight", False, True, False, now - timedelta(hours=3)),
        ],
        now,
    )
    assert out["pending"] == ["late_settle", "tonight"]


def test_pinned_diff_flags_a_changed_served_function(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for f in ev.PINNED:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("v1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "freeze")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ev.pinned_diff(tmp_path, head) == []
    (tmp_path / "nba/parlay/top_source.py").write_text("v2\n")
    assert ev.pinned_diff(tmp_path, head) == ["nba/parlay/top_source.py"]


def test_secondaries_carry_no_pass_word() -> None:
    r = ev.evaluate(ev.synth_outcomes(_frame(60, 20), "transport", seed=1))
    s = ev.strip_pass_words(r)
    assert s is not None
    assert not set(ev.PASS_WORDS) & set(s)
    assert "primary_brier_cal_minus_raw" in s
    assert ev.strip_pass_words(None) is None


def test_fallback_slice_is_reported() -> None:
    r = ev.evaluate(ev.synth_outcomes(_frame(60, 20), "transport", seed=1))
    n_fb = r["S6_slices"]["fallback_rows"]["n"]
    assert n_fb > 0
    assert n_fb + r["S6_slices"]["context_model_rows"]["n"] == r["n"]


def _fixture_root(root: Path, y: float | None) -> None:
    """A tiny season-2026 nba.duckdb + market_asof with one game, two players, pts at 10.5."""
    tip = datetime(2026, 11, 5, 0, 0)
    made = tip - timedelta(hours=8)
    con = duckdb.connect(str(root / "nba.duckdb"))
    con.execute(
        "CREATE TABLE forward_scores (game_id VARCHAR, game_date DATE, season INT, model_name VARCHAR, "
        "version VARCHAR, target VARCHAR, player_id INT, made_at TIMESTAMP, y DOUBLE, brier DOUBLE, "
        "log_loss DOUBLE, crps DOUBLE, status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE forward_predictions (game_id VARCHAR, model_name VARCHAR, target VARCHAR, "
        "player_id INT, made_at TIMESTAMP, prediction VARCHAR)"
    )
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes DOUBLE)")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE forward_prop_grid199 (game_id VARCHAR, player_id INT, stat VARCHAR, "
        "made_at TIMESTAMP, model VARCHAR, q199 DOUBLE[])"
    )
    gid, gd = "0022600100", date(2026, 11, 4)
    con.execute("INSERT INTO games VALUES (?, ?, 2026, 110, 100)", [gid, gd])
    for pid, sr in ((1, 1.0), (2, 0.0)):
        c = [max(0, 199 - 9 * k) for k in range(30)]
        pred = {
            "mean": 9.0, "p_play": 0.95, "proj_minutes": 28.0, "starter_rate": sr,
            "p_ge_full": {"c": c, "n": 199}, "q_grid": [float(i) for i in range(2, 21)],
        }  # fmt: skip
        con.execute(
            "INSERT INTO forward_predictions VALUES (?, 'props_context_residual', 'pts', ?, ?, ?)",
            [gid, pid, made, json.dumps(pred)],
        )
        sentinel = None if y is None else 999.0
        con.execute(
            "INSERT INTO forward_scores VALUES (?, ?, 2026, 'props_context_residual', 'ctxres-v1', "
            "'pts', ?, ?, ?, ?, ?, ?, 'scored')",
            [gid, gd, pid, made, y, sentinel, sentinel, sentinel],
        )
    con.close()
    (root / "data/markets").mkdir(parents=True)
    m = duckdb.connect(str(root / "data/markets/market_asof.duckdb"))
    m.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, player_id INT, market VARCHAR, book VARCHAR, "
        "side VARCHAR, point DOUBLE, price_american INT, received_at TIMESTAMP, snapshot_kind VARCHAR, "
        "tipoff TIMESTAMP, slate_date DATE)"
    )
    for pid in (1, 2):
        for side in ("over", "under"):
            m.execute(
                "INSERT INTO live_book_odds VALUES (?, ?, 'player_points', 'pinnacle', ?, 10.5, -110, ?, "
                "'t60', ?, ?)",
                [gid, pid, side, tip - timedelta(minutes=62), tip, gd],
            )
    m.close()


def test_status_output_does_not_depend_on_outcomes(tmp_path: Path) -> None:
    outs = []
    for i, y in enumerate((None, 12.0)):
        root = tmp_path / f"r{i}"
        root.mkdir()
        _fixture_root(root, y)
        outs.append(
            ev.cmd_status(root, root, None, date(2026, 11, 10), assume_freeze=date(2026, 11, 1))
        )
    assert outs[0] == outs[1]
    assert outs[0]["window"] == {"games": 1, "legs": 2, "dates": 1}


# ------------------------------------------------------------------ re-review N2, N4, N5

WT = Path(__file__).resolve().parents[2]


def _freeze_repo(tmp: Path, change_top_source: bool) -> str:
    """A clean repo whose freeze commit carries a FROZEN doc and the pinned files (optionally a different
    committed top_source.p_over: the adversary's rr_b3 scenario). Returns the rule sha."""
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    for f in ev.PINNED:
        (tmp / f).parent.mkdir(parents=True, exist_ok=True)
        b = (WT / f).read_bytes()
        if change_top_source and f == "nba/parlay/top_source.py":
            b = b.replace(b"return 1.0 if k <= 0", b"return 0.9 if k <= 0")
            assert b != (WT / f).read_bytes()
        (tmp / f).write_bytes(b)
    doc = tmp / ev.DOC_REL
    doc.parent.mkdir(parents=True, exist_ok=True)
    body = "# x\n\nStatus: **FROZEN by the main session.**\nrule\n" + ev.MARKER + "\n"
    doc.write_text(body)
    sha = ev.rule_text_sha(body)
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-q", "-m", f"prereg({ev.RULE}): {sha}")
    return sha


def test_run_refuses_when_the_executing_code_differs_from_the_freeze(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    sha = _freeze_repo(tmp_path, change_top_source=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ev.pinned_diff(tmp_path, head) == []  # the old check alone would have let this run
    _, bad = ev.executing_vs_freeze(tmp_path, head)
    assert bad == ["nba/parlay/top_source.py"]
    with pytest.raises(SystemExit, match="executing code differs"):
        ev.cmd_run(tmp_path, tmp_path, sha, ev.real_today())


def test_executing_code_matches_an_identical_freeze(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    _freeze_repo(tmp_path, change_top_source=False)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    shas, bad = ev.executing_vs_freeze(tmp_path, head)
    assert bad == []
    assert set(shas) == set(ev.PINNED)


def test_run_once_guard_checks_both_checkouts(tmp_path: Path) -> None:
    repo, root = tmp_path / "freeze", tmp_path / "main"
    repo.mkdir()
    root.mkdir()
    ev.run_once_guard(repo, root)
    (root / ev.OUT_REL).parent.mkdir(parents=True)
    (root / ev.OUT_REL).write_text("{}")
    with pytest.raises(SystemExit, match="runs once"):
        ev.run_once_guard(repo, root)


def _settlement_fixture(fx: Path) -> tuple[date, date]:
    """The adversary's B2 fixture (rr_b1b2.py): 2026-11-10 settled / postponed / final-unscored and a
    games-only id; 2026-11-12 unforecast and a 0-0 row."""
    (fx / "data/schedule").mkdir(parents=True)
    sched = [
        ("0022600201", "2026-11-11T00:00:00Z", "2026-11-10T19:00:00Z"),
        ("0022600202", "2026-11-11T00:30:00Z", "2026-11-10T19:30:00Z"),
        ("0022600203", "2026-11-11T01:00:00Z", "2026-11-10T20:00:00Z"),
        ("0022600204", "2026-11-13T00:00:00Z", "2026-11-12T19:00:00Z"),
        ("0022600205", "2026-11-13T03:00:00Z", "2026-11-12T22:00:00Z"),
    ]
    pl.DataFrame(
        sched, schema=["gameId", "gameDateTimeUTC", "gameDateTimeEst"], orient="row"
    ).write_parquet(fx / "data/schedule/live_tips_2026-27.parquet")
    con = duckdb.connect(str(fx / "nba.duckdb"))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_pts INT, away_pts INT)"
    )
    con.execute("CREATE TABLE forward_predictions (game_id VARCHAR, model_name VARCHAR)")
    con.execute("CREATE TABLE forward_scores (game_id VARCHAR, model_name VARCHAR, season INT)")
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT)")
    d1, d2 = date(2026, 11, 10), date(2026, 11, 12)
    for gid, d, h, a in (
        ("0022600201", d1, 110, 100),
        ("0022600203", d1, 99, 98),
        ("0022600204", d2, 120, 115),
        ("0022600205", d2, 0, 0),
        ("0022600299", d1, 105, 101),
    ):
        con.execute("INSERT INTO games VALUES (?, ?, 2026, ?, ?)", [gid, d, h, a])
    for gid in ("0022600201", "0022600202", "0022600203", "0022600205", "0022600299"):
        con.execute("INSERT INTO forward_predictions VALUES (?, 'props_context_residual')", [gid])
    for gid in ("0022600201", "0022600299"):
        con.execute("INSERT INTO forward_scores VALUES (?, 'props_context_residual', 2026)", [gid])
    con.close()
    return d1, d2


def test_settlement_fixture_through_the_loader(tmp_path: Path) -> None:
    d1, d2 = _settlement_fixture(tmp_path)
    states = ev.load_game_states(tmp_path, d1, d2)
    assert {s.game_id for s in states} == {
        "0022600201", "0022600202", "0022600203", "0022600204", "0022600205", "0022600299",
    }  # fmt: skip
    early = ev.classify_settlement(states, datetime(2026, 11, 13, 12, 0, tzinfo=UTC))
    assert early == {
        "settled": ["0022600201", "0022600299"],
        "pending": ["0022600203", "0022600205"],  # final but unscored; 0-0 row only 33 h after tip
        "unplayed_or_postponed": ["0022600202"],
        "unforecast": ["0022600204"],
    }
    later = ev.classify_settlement(states, datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    assert later["pending"] == ["0022600203"]
    assert later["unplayed_or_postponed"] == ["0022600202", "0022600205"]
    con = duckdb.connect(str(tmp_path / "nba.duckdb"))
    con.execute("INSERT INTO forward_scores VALUES ('0022600203', 'props_context_residual', 2026)")
    con.close()
    final = ev.classify_settlement(
        ev.load_game_states(tmp_path, d1, d2), datetime(2027, 2, 5, 12, 0, tzinfo=UTC)
    )
    assert final["pending"] == []  # postponed and unforecast games never deadlock the look


def test_box_rows_make_a_game_final_when_the_score_ingest_lags(tmp_path: Path) -> None:
    d1, d2 = _settlement_fixture(tmp_path)
    con = duckdb.connect(str(tmp_path / "nba.duckdb"))
    con.execute(
        "INSERT INTO player_game_stats VALUES ('0022600205', 1)"
    )  # 0-0 in games, but box rows exist
    con.close()
    st = {s.game_id: s for s in ev.load_game_states(tmp_path, d1, d2)}
    assert st["0022600205"].final
    out = ev.classify_settlement(list(st.values()), datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    assert (
        "0022600205" in out["pending"]
    )  # final, predicted, unscored: blocks instead of reading as postponed
