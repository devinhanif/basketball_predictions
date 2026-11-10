# ruff: noqa: E501
"""GAME_SCRIPTS_FORWARD harness: guards (holdout, clock, one look, frozen rule), the served-number
equivalences, and the words on synthetic data. No real database is opened."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.parlay import combo
from research.eval import game_scripts_forward_eval as h
from tests.parlay import combo_fixture as cf

# ---------------------------------------------------------------- tiny synthetic database


def _db(tmp_path: Path, n_days: int = 40, per_day: int = 20, season: int = 2026) -> Path:
    path = tmp_path / "nba.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
        "away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, "
        "tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INTEGER, "
        "prediction JSON)"
    )
    rng = np.random.default_rng(3)
    d0 = date(2026, 11, 1)
    k = 0
    for day in range(n_days):
        d = d0 + timedelta(days=day)
        for _ in range(per_day):
            gid = f"0022{k:06d}"
            k += 1
            tip = datetime(d.year, d.month, d.day, 23, 30) + timedelta(hours=1)
            hp, ap = int(rng.integers(95, 130)), int(rng.integers(95, 130))
            con.execute(
                "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)",
                [gid, d, season, 1 + k % 30, 1 + (k + 7) % 30, hp, ap],
            )
            for lead in (300, 70):
                con.execute(
                    "INSERT INTO forward_predictions VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    [
                        f"r{lead}",
                        tip - timedelta(minutes=lead),
                        gid,
                        tip,
                        h.WIN_MODEL,
                        h.WIN_VERSION,
                        "win_prob_home",
                        json.dumps({"p_home": 0.4 + lead / 1000}),
                    ],
                )
    con.close()
    return path


def _rows(path: Path, first: date = date(2026, 10, 20)) -> pl.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return h.window_rows(con, None, first)
    finally:
        con.close()


# ---------------------------------------------------------------- guards


def test_holdout_season_is_refused() -> None:
    with pytest.raises(h.Refused):
        h.refuse_seasons([2026, 2025])
    h.refuse_seasons([2026])


def test_window_rows_refuse_nothing_but_select_only_2026(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=2, per_day=3))
    assert rows.height == 6
    assert set(rows["season"].to_list()) == {2026}
    # latest stamp strictly before tip is the 70-minute one
    assert rows["p_home"].to_list() == pytest.approx([0.47] * 6)
    assert "home_pts" not in rows.columns and "margin" not in rows.columns


def test_window_rows_ignore_other_seasons(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=2, per_day=3, season=2025))
    assert rows.height == 0


def test_look_state_counts_only_after_freeze_and_needs_settled(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=40, per_day=20))
    freeze = date(2026, 11, 2)  # window starts 11-03; 750 reached on the 38th window game day
    now = datetime(2027, 1, 1, tzinfo=UTC)
    st = h.look_state(rows, freeze, date(2026, 11, 30), now)
    assert not st.open and st.look_date is None
    st = h.look_state(rows, freeze, date(2026, 12, 15), now)
    assert st.look_date == date(2026, 12, 10) and st.n_at_look == 760
    assert st.open
    # the look date itself is not enough: the run must be after it
    st = h.look_state(rows, freeze, date(2026, 12, 10), now)
    assert not st.open
    # an unsettled game through D blocks the look until 36 h after its tip
    rows2 = rows.with_columns(
        pl.when(pl.col("game_id") == rows["game_id"][100])
        .then(False)
        .otherwise(pl.col("final"))
        .alias("final")
    )
    # one game less moves D to 12-11; give the unsettled game a tip that late so the 36 h rule binds
    tip = datetime(2026, 12, 11, 0, 0)
    rows2 = rows2.with_columns(
        pl.when(pl.col("game_id") == rows["game_id"][100])
        .then(pl.lit(tip))
        .otherwise(pl.col("tip_sched"))
        .alias("tip_sched")
    )
    early = (tip + timedelta(hours=35)).replace(tzinfo=UTC)
    late = (tip + timedelta(hours=37)).replace(tzinfo=UTC)
    assert not h.look_state(rows2, freeze, date(2026, 12, 15), early).open
    assert h.look_state(rows2, freeze, date(2026, 12, 15), late).open


def _with_later_listing(rows: pl.DataFrame, d: date) -> pl.DataFrame:
    """A scheduled, unplayed game dated ``d`` (the database lists games up to D)."""
    extra = rows.head(1).with_columns(
        pl.lit("0022999999").alias("game_id"),
        pl.lit(d).alias("et_date"),
        pl.lit(False).alias("final"),
        pl.lit(datetime(d.year, d.month, d.day, 23)).alias("tip_sched"),
    )
    return pl.concat([rows, extra])


def test_backstop_opens_the_look_with_fewer_games(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=10, per_day=10))
    after = h.BACKSTOP + timedelta(days=1)
    now = datetime(2027, 4, 1, tzinfo=UTC)
    # a clock moved past the backstop cannot open a look on the games that exist today
    st = h.look_state(rows, date(2026, 10, 30), after, now)
    assert st.look_date == h.BACKSTOP and not st.open and "lists games only" in st.reason
    soon = datetime(2027, 3, 29, 20, tzinfo=UTC)  # the listed backstop game is not settled yet
    st = h.look_state(_with_later_listing(rows, h.BACKSTOP), date(2026, 10, 30), after, soon)
    assert st.n_at_look == 100 and not st.open
    late = datetime(2027, 4, 10, tzinfo=UTC)
    st = h.look_state(_with_later_listing(rows, h.BACKSTOP), date(2026, 10, 30), after, late)
    assert st.open


def test_look_needs_the_real_utc_date_past_d(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=40, per_day=20))
    st = h.look_state(
        rows, date(2026, 11, 2), date(2026, 12, 15), datetime(2026, 12, 10, tzinfo=UTC)
    )
    assert not st.open


def test_scores_and_features_refuse_a_closed_look(tmp_path: Path) -> None:
    path = _db(tmp_path, n_days=2, per_day=2)
    rows = _rows(path)
    st = h.look_state(
        rows, date(2026, 10, 30), date(2026, 11, 5), datetime(2026, 11, 5, tzinfo=UTC)
    )
    assert not st.open
    con = duckdb.connect(str(path), read_only=True)
    try:
        with pytest.raises(h.Refused):
            h.load_scores(con, rows["game_id"].to_list(), st)
        with pytest.raises(h.Refused):
            h.as_of_mu_t(con, rows, None, st)
        with pytest.raises(h.Refused):
            h.leg_outcomes(con, pl.DataFrame({"game_id": ["x"]}), st)
    finally:
        con.close()


def test_run_refuses_until_the_rule_is_frozen(tmp_path: Path) -> None:
    assert h.RULE_SHA256 is None
    assert not hasattr(h, "FREEZE_COMMIT")
    assert h.main(["run", "--data-root", str(tmp_path)]) == 2
    assert h.main(["run-s", "--data-root", str(tmp_path)]) == 2


def test_cli_and_commands_take_no_date_or_clock() -> None:
    import inspect

    with pytest.raises(SystemExit):
        h.main(["run", "--data-root", "x", "--today", "2027-03-30"])
    assert list(inspect.signature(h.cmd_run).parameters) == ["root", "repo"]
    assert list(inspect.signature(h.cmd_run_s).parameters) == ["root", "repo"]


# ---------------------------------------------------------------- a frozen rule in a temporary repo

REAL_REPO = Path(__file__).resolve().parents[3]
RULE_TEXT = "# x\n\nStatus: **FROZEN** (test)\nrule body\n"


def _git(repo: Path, *args: str, date_iso: str | None = None) -> str:
    import os
    import subprocess

    env = dict(os.environ)
    if date_iso:
        env.update(GIT_COMMITTER_DATE=date_iso, GIT_AUTHOR_DATE=date_iso)
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def _frozen_repo(tmp_path: Path) -> tuple[Path, str]:
    import shutil

    repo = tmp_path / "repo"
    (repo / "docs/prereg").mkdir(parents=True)
    (repo / "nba/parlay").mkdir(parents=True)
    (repo / h.RULE_DOC).write_text(RULE_TEXT + h.MARKER + "\n\n(empty)\n")
    for rel in (h.COMBO_PARAMS_REL, h.LINE_CAL_MAP_REL):
        shutil.copy(REAL_REPO / rel, repo / rel)
    sha = h.rule_text_sha(repo / h.RULE_DOC)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        f"prereg(GAME_SCRIPTS_FORWARD): {sha}",
        date_iso="2026-11-02T12:00:00-05:00",
    )
    return repo, sha


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data_root"
    root.mkdir()
    db = _db(root, n_days=45, per_day=20)
    assert db == root / "nba.duckdb"
    (root / "data/facts").mkdir(parents=True)
    (root / "data/parlay").mkdir(parents=True)
    con = duckdb.connect(str(db), read_only=True)
    tips = con.execute("SELECT DISTINCT game_id, tipoff FROM forward_predictions").fetchall()
    con.close()
    f = duckdb.connect(str(root / "data/facts/facts.duckdb"))
    f.execute(
        "CREATE TABLE facts (subject VARCHAR, predicate VARCHAR, value_text VARCHAR, known_at TIMESTAMP)"
    )
    for gid, tip in tips:
        f.execute(
            "INSERT INTO facts VALUES (?, 'tips_at', ?, ?)",
            [f"game:{gid}", tip.isoformat(), tip - timedelta(days=2)],
        )
    f.close()
    params = {
        "fit_seasons": [2022, 2023, 2024], "halflife": 20.0, "league_total": 227.6, "margin_b": 0.58,
        "margin_s": 6.97, "margin_sd": 13.63, "n_margin": 1, "n_total": 1, "prior_k": 8.0,
        "total_a": -176.4, "total_c": 1.774, "total_sd": 18.82,
    }  # fmt: skip
    (root / h.JOINT_MODEL_REL).write_text(json.dumps({"params": params}))
    return root


def _freeze(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    repo, sha = _frozen_repo(tmp_path)
    root = _data_root(tmp_path)
    monkeypatch.setattr(h, "RULE_SHA256", sha)
    monkeypatch.setattr(h, "JOINT_MODEL_SHA8", h.sha8(root / h.JOINT_MODEL_REL))
    monkeypatch.setattr(
        h, "_today_et", lambda: (date(2026, 12, 20), datetime(2026, 12, 20, 15, tzinfo=UTC))
    )
    return repo, root


def test_verify_frozen_finds_the_commit_and_checks_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha = _frozen_repo(tmp_path)
    monkeypatch.setattr(h, "RULE_SHA256", sha)
    c = h.verify_frozen(repo)
    assert h.freeze_et_date(repo, c) == date(2026, 11, 2)
    (repo / h.RULE_DOC).write_text(RULE_TEXT + "changed\n" + h.MARKER + "\n")
    with pytest.raises(h.Refused):
        h.verify_frozen(repo)
    monkeypatch.setattr(h, "RULE_SHA256", "0" * 64)
    with pytest.raises(h.Refused):
        h.verify_frozen(repo)


def test_code_change_after_the_freeze_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha = _frozen_repo(tmp_path)
    monkeypatch.setattr(h, "RULE_SHA256", sha)
    c = h.verify_frozen(repo)
    h.verify_code_unchanged(repo, c)
    (repo / "nba/parlay/new.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    with pytest.raises(h.Refused):
        h.verify_code_unchanged(repo, c)


def test_cmd_run_end_to_end_then_refuses_a_second_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _freeze(monkeypatch, tmp_path)
    res = h.cmd_run(root, repo)
    assert res["look_date"] == "2026-12-10" and res["integrity"]["fail"] is False
    assert set(res["P"]) == set(h.SCRIPTS) and res["S"] == "DEFERRED_LINE_CAL_LIVE"
    assert all("word" not in v for v in res["PRE_FREEZE"].get("P", {}).values())
    assert (repo / h.OUT_REL).exists()
    with pytest.raises(h.Refused):
        h.cmd_run(root, repo)


def test_a_committed_results_file_blocks_the_look_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _freeze(monkeypatch, tmp_path)
    out = repo / h.OUT_REL
    out.parent.mkdir(parents=True)
    out.write_text("{}\n")
    _git(repo, "add", h.OUT_REL)
    _git(repo, "commit", "-q", "-m", "results")
    _git(repo, "rm", "-q", h.OUT_REL)
    _git(repo, "commit", "-q", "-m", "removed")
    with pytest.raises(h.Refused):
        h.cmd_run(root, repo)


def test_run_s_builds_legs_before_reading_any_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _freeze(monkeypatch, tmp_path)
    h.cmd_run(root, repo)
    lcl = repo / h.LINE_CAL_LIVE_RESULTS
    lcl.parent.mkdir(parents=True)
    lcl.write_text("{}\n")
    called: list[str] = []

    def legs_crash(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("export path crashed")

    def spy(*_a: Any, **_k: Any) -> None:
        called.append("load_scores")
        raise AssertionError("outcome read before the legs")

    monkeypatch.setattr(h, "s_legs", legs_crash)
    monkeypatch.setattr(h, "load_scores", spy)
    with pytest.raises(RuntimeError):
        h.cmd_run_s(root, repo)
    assert called == []


def test_planted_future_and_other_seasons_leave_mu_t_unchanged(tmp_path: Path) -> None:
    path = _db(tmp_path, n_days=20, per_day=10)
    rows = _rows(path)
    open_state = h.LookState(
        date(2027, 1, 1), date(2026, 10, 30), date(2026, 11, 20), 200, True, "test"
    )
    params = SimpleParams()
    con = duckdb.connect(str(path), read_only=True)
    before = h.as_of_mu_t(con, rows, params, open_state)
    con.close()
    w = duckdb.connect(str(path))
    w.execute("INSERT INTO games VALUES ('0022999990', DATE '2027-06-01', 2026, 1, 2, 400, 400)")
    w.execute("INSERT INTO games VALUES ('0022599990', DATE '2026-04-01', 2025, 1, 2, 400, 400)")
    w.execute("INSERT INTO games VALUES ('0012699990', DATE '2026-10-25', 2026, 1, 2, 400, 400)")
    w.close()
    con = duckdb.connect(str(path), read_only=True)
    after = h.as_of_mu_t(con, rows, params, open_state)
    con.close()
    assert before == after


class SimpleParams:
    """The game model's surface the harness uses (frozen numbers of joint_model.json)."""

    halflife, prior_k, league_total = 20.0, 8.0, 227.6
    margin_sd, total_sd = 13.63, 18.82

    def total_mu(self, feat: float | None) -> float:
        return -176.4 + 1.774 * (self.league_total if feat is None else feat)

    def margin_mu(self, p_home: float) -> float:
        return 0.58 + 6.97 * h.logit(p_home)


def test_integrity_fail_on_a_stamp_at_tip(tmp_path: Path) -> None:
    rows = _rows(_db(tmp_path, n_days=3, per_day=4))
    gid = rows["game_id"][0]
    rows = rows.with_columns(
        pl.when(pl.col("game_id") == gid)
        .then(pl.col("tipoff"))
        .otherwise(pl.col("made_at"))
        .alias("made_at")
    )
    scores = pl.DataFrame(
        {"game_id": rows["game_id"], "margin": [3] * rows.height, "total": [220] * rows.height}
    )
    mu_t = dict.fromkeys(rows["game_id"].to_list(), 225.0)
    _, integ = h.scored_frame(rows, scores, mu_t, SimpleParams())
    assert integ["fail"] and integ["bad_tip_or_stamp_ids"] == [gid]


def test_game_parameters_are_rounded_as_the_app_payload() -> None:
    from nba.parlay import combo_build as cb

    shared = {
        "scripts": {
            "definitions": {"blowout_abs_margin": 15, "close_abs_margin": 5, "total_delta": 12.0}
        }
    }
    params = SimpleParams()
    for p_home, mu_t in ((0.731, 227.4449), (0.3, 219.995), (0.5, 231.12501)):
        payload = cb.build_game_payload(
            game_id="g", date="2026-12-01", home_team=1, away_team=2, tip_utc="t", made_at="m",
            mu_m=params.margin_mu(p_home), sd_m=params.margin_sd, mu_t=mu_t, sd_t=params.total_sd, players=[],
        )  # fmt: skip
        mm, mt, sdm, sdt = h.game_params(p_home, mu_t, params)
        assert (mm, mt, sdm, sdt) == tuple(
            payload["game"][k] for k in ("mu_m", "mu_t", "sd_m", "sd_t")
        )
        ours = h.priors(mm, mt, sdm, sdt)
        for s in h.SCRIPTS:
            assert ours[s] == combo.script_prior(s, payload, shared)


def test_shown_mask_matches_the_app_drop_rule() -> None:
    legs = pl.DataFrame(
        {
            "u": [0.5, 0.8, 0.5, 0.7],
            "raw": [0.5, 0.5, 0.85, 0.6],
            **{f"c_{s}": [0.5, 0.5, 0.5, 0.76] for s in h.SCRIPTS},
        }
    )
    assert h.shown_mask(legs, "grind").to_list() == [True, False, False, False]


def test_rule_sha_matches_the_awk_recipe() -> None:
    doc = Path(__file__).resolve().parents[3] / h.RULE_DOC
    lines = doc.read_text().splitlines()
    head = "".join(ln + "\n" for ln in lines[: lines.index(h.MARKER)])
    import hashlib

    assert h.rule_text_sha(doc) == hashlib.sha256(head.encode()).hexdigest()


# ---------------------------------------------------------------- served-number equivalences


def test_priors_are_the_served_function_and_the_closed_form() -> None:
    for mm, mt in ((-9.0, 211.3), (0.58, 228.0), (7.7, 240.49)):
        a, b = h.priors(mm, mt, 13.63, 18.82), h.closed_form(mm, mt, 13.63, 18.82)
        for s in h.SCRIPTS:
            assert a[s] == pytest.approx(b[s], abs=1e-12)


def test_conditional_leg_equals_combo_one_leg_under_each_script() -> None:
    game, shared = cf.game(), cf.shared()
    for pid, stat, line in ((1, "pts", 24.5), (2, "reb", 2.5), (3, "fg3m", 1.5)):
        pl_ = next(p for p in game["players"] if p["player_id"] == pid)
        u = combo.surv_at(pl_["stats"][stat], int(line + 0.5))
        role = "starter" if pl_["starter"] else "bench"
        leg = {
            "kind": "player",
            "game_id": "G1",
            "player_id": pid,
            "stat": stat,
            "line": line,
            "side": "over",
        }
        for s in h.SCRIPTS:
            mu, sg = combo.script_params(shared, s, stat, role)
            got = combo.evaluate_game_legs([leg], game, shared, script=s).p_joint
            assert h.cond_p(u, mu, sg) == pytest.approx(got, abs=1e-9)


def test_same_player_pair_equals_combo_two_leg_joint() -> None:
    game, shared = cf.game(), cf.shared()
    p1 = game["players"][0]
    legs = [
        {
            "kind": "player",
            "game_id": "G1",
            "player_id": 1,
            "stat": "pts",
            "line": 24.5,
            "side": "over",
        },
        {
            "kind": "player",
            "game_id": "G1",
            "player_id": 1,
            "stat": "reb",
            "line": 6.5,
            "side": "over",
        },
    ]
    for s in h.SCRIPTS:
        a = []
        for st, ln in (("pts", 24.5), ("reb", 6.5)):
            mu, sg = combo.script_params(shared, s, st, "starter")
            a.append((h._ppf(1 - combo.surv_at(p1["stats"][st], int(ln + 0.5))) - mu) / sg)
        rho = combo.corr_lookup(shared["corr"], "same_player", "pts", "reb")
        mine = combo.bivariate_rect(a[0], math.inf, a[1], math.inf, rho)
        assert mine == pytest.approx(
            combo.evaluate_game_legs(legs, game, shared, script=s).p_joint, abs=1e-6
        )


# ---------------------------------------------------------------- statistics and words


def test_bh_matches_hand_values() -> None:
    assert h.bh([0.01, 0.04, 0.03, 0.5]) == pytest.approx(
        [0.04, 0.0533333, 0.0533333, 0.5], abs=1e-6
    )


def _y_p(
    n: int, gap: float, seed: int
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    rng = np.random.default_rng(seed)
    p = {s: rng.uniform(0.2, 0.4, n) for s in h.SCRIPTS}
    y = {s: (rng.random(n) < p[s] + gap).astype(float) for s in h.SCRIPTS}
    dates = [f"d{i // 7}" for i in range(n)]
    return y, p, dates, [f"g{i}" for i in range(n)]


def test_family_p_words() -> None:
    y, p, d, g = _y_p(6000, 0.0, 1)
    assert {r["word"] for r in h.family_p(y, p, d, g).values()} == {"CALIBRATED"}
    y, p, d, g = _y_p(1500, 0.09, 2)
    assert {r["word"] for r in h.family_p(y, p, d, g).values()} == {"MISCALIBRATED"}


def test_family_d_needs_resolution() -> None:
    rng = np.random.default_rng(5)
    n = 4000
    p = {s: rng.uniform(0.05, 0.8, n) for s in h.SCRIPTS}
    y = {s: (rng.random(n) < p[s]).astype(float) for s in h.SCRIPTS}
    d = [f"d{i // 7}" for i in range(n)]
    assert {r["word"] for r in h.family_d(y, p, d).values()} == {"INFORMATIVE"}
    flat = {s: np.full(n, 0.3) for s in h.SCRIPTS}
    assert {r["word"] for r in h.family_d(y, flat, d).values()} == {"NOT_SHOWN"}


def _legs(n_games: int, honest: bool, seed: int) -> tuple[pl.DataFrame, dict[str, dict[str, bool]]]:
    """Every game realises every script; outcomes drawn from the grind conditional when honest."""
    rng = np.random.default_rng(seed)
    params = json.loads((Path(h.__file__).resolve().parents[2] / h.COMBO_PARAMS_REL).read_text())
    rows = []
    for gi in range(n_games):
        for j in range(16):
            rows.append(
                {
                    "game_id": f"g{gi}",
                    "player_id": j // 4,
                    "stat": h.STATS[j % 4],
                    "u": float(rng.uniform(0.3, 0.7)),
                    "raw": 0.5,
                    "starter_rate": 0.9 if j < 8 else 0.1,
                }
            )
    legs = h.add_conditionals(pl.DataFrame(rows), params["script_params"])
    src = legs["c_grind"] if honest else legs["u"]
    y = (rng.random(legs.height) < src.to_numpy()).astype(int)
    real = {f"g{i}": dict.fromkeys(h.SCRIPTS, True) for i in range(n_games)}
    return legs.with_columns(pl.Series("y", y)), real


def test_family_s_words() -> None:
    legs, real = _legs(400, honest=True, seed=11)
    assert h.family_s(legs, real)["grind"]["word"] == "CONDITIONAL_CALIBRATED"
    legs, real = _legs(400, honest=False, seed=12)
    assert h.family_s(legs, real)["grind"]["word"] == "NOT_SHOWN"
    legs, real = _legs(100, honest=True, seed=13)
    assert h.family_s(legs, real)["grind"]["word"] == "INSUFFICIENT_DATA"


def test_fewer_than_600_games_is_insufficient() -> None:
    frame, _, _ = h.synthetic(n_games=300, n_dates=40)
    res = h.score_games(frame, 18.82)
    assert {r["word"] for r in res["P"].values()} == {"INSUFFICIENT_DATA"}


def test_dryrun_uses_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("dryrun opened a database")

    monkeypatch.setattr(duckdb, "connect", boom)
    frame, legs, real = h.synthetic(n_games=650, n_dates=90)
    res = h.score_games(frame, 18.82)
    assert set(res["P"]) == set(h.SCRIPTS)
    n_grind = sum(1 for g in legs["game_id"].to_list() if real[g]["grind"])
    assert h.family_s(h.add_conditionals(legs, []), real)["grind"]["legs"] == n_grind
