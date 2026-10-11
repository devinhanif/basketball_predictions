"""docs/prereg/ELO_HISTORY_WINDOW.md harness: hash refusal, E0 == production replay, identical
keys across arms, planted-future, weights only in E3, read-only/season guards, smoke wiring."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.ingest.era_flags import compute_era_flags
from nba.models.injury_elo import sequential_elo_logits, sigmoid
from research.eval import elo_history_eval as eh

START = {
    2019: dt.date(2019, 10, 22),
    2020: dt.date(2020, 12, 22),
    2021: dt.date(2021, 10, 19),
    2022: dt.date(2022, 10, 18),
    2023: dt.date(2023, 10, 24),
    2024: dt.date(2024, 10, 22),
    2025: dt.date(2025, 10, 21),
}


def _league(seasons: tuple[int, ...], seed: int, prefix: int, days: int = 40) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    n = 0
    for s in seasons:
        offsets = [START[s] + dt.timedelta(days=day) for day in range(days)]
        if s == 2019:  # bubble restart block
            offsets += [dt.date(2020, 8, 1) + dt.timedelta(days=k) for k in range(6)]
        for d in offsets:
            order = rng.permutation(8)
            for j in range(0, 8, 2):
                n += 1
                rows.append(
                    {
                        "game_id": f"{prefix}{n:07d}",
                        "game_date": d,
                        "season": s,
                        "home_team": int(order[j]) + 1,
                        "away_team": int(order[j + 1]) + 1,
                        "home_pts": int(rng.integers(85, 130)),
                        "away_pts": int(rng.integers(85, 130)),
                    }
                )
    df = pl.DataFrame(rows).with_columns(pl.col("game_date").cast(pl.Date))
    return df.filter(pl.col("home_pts") != pl.col("away_pts"))


def _world() -> dict[str, pl.DataFrame]:
    hist = _league((2019, 2020, 2021), 1, 1)
    cur = _league((2022, 2023, 2024), 2, 2)
    return {
        "hist": hist,
        "cur": cur,
        "hist_flags": compute_era_flags(hist.select("game_id", "game_date", "season", "home_team")),
        "cur_flags": compute_era_flags(cur.select("game_id", "game_date", "season", "home_team")),
    }


PARAMS = {
    "k_factor": 9.4,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 6.7e-4,
}


# --------------------------------------------------------------------------- freeze


def _doc(tmp_path: Path, body: str = "frozen text\n", tail: str = "(not run)\n") -> Path:
    p = tmp_path / "RULE.md"
    p.write_text(body + eh.MARKER + "\n" + tail)
    return p


def test_hash_refusal_and_results_section_is_outside_the_hash(tmp_path: Path) -> None:
    p = _doc(tmp_path)
    sha = eh.frozen_sha(p)
    eh.configure(p)
    assert eh.require_frozen(sha) == sha
    with pytest.raises(SystemExit):
        eh.require_frozen("0" * 64)
    with pytest.raises(SystemExit):
        eh.require_frozen(None)
    with pytest.raises(SystemExit):
        eh.require_frozen(sha[:8])
    p.write_text("frozen text\n" + eh.MARKER + "\nresults changed\n")
    assert eh.frozen_sha(p) == sha
    p.write_text("frozen text EDITED\n" + eh.MARKER + "\n")
    with pytest.raises(SystemExit):
        eh.require_frozen(sha)
    assert eh.require_frozen(None, allow_draft=True) == eh.frozen_sha(p)
    eh.configure(eh.DEFAULT_DOC)


def test_allow_draft_is_gate_only(tmp_path: Path) -> None:
    p = _doc(tmp_path)
    for stage in ("run", "smoke", "report"):
        with pytest.raises(SystemExit):
            eh.main([stage, "--doc", str(p), "--allow-draft"])
    with pytest.raises(SystemExit):
        eh.main(["run", "--doc", str(p)])  # no --sha
    eh.configure(eh.DEFAULT_DOC)


# --------------------------------------------------------------------------- replay


def test_fast_replay_equals_production_replay() -> None:
    w = _world()
    for start in (eh.E0_START, eh.E1_START):
        g = eh.combined_games(w, start)
        prod = eh.production_logits(g, PARAMS)
        assert np.array_equal(prod, np.asarray(sequential_elo_logits(g, PARAMS)))
        assert np.max(np.abs(eh.fast_logits(eh.prepare_arrays(g), PARAMS) - prod)) <= 1e-12


def test_e0_is_the_production_replay_from_2022() -> None:
    w = _world()
    e0 = eh.arm_probs("E0", w, PARAMS)
    cur = eh.with_result(w["cur"])
    prod = sequential_elo_logits(cur, PARAMS)
    ref = cur.with_columns(pl.Series("l", prod)).select("game_id", "l")
    j = e0.join(ref, on="game_id")
    assert np.array_equal(j["p"].to_numpy(), np.clip(sigmoid(j["l"].to_numpy()), 0, 1))
    assert eh.combined_games(w, eh.E0_START)["season"].min() == 2022


def test_long_replay_differs_from_e0_and_carries_ratings() -> None:
    w = _world()
    e0 = eh.arm_probs("E0", w, PARAMS)["p"].to_numpy()
    e1 = eh.arm_probs("E1", w, PARAMS)["p"].to_numpy()
    assert not np.array_equal(e0, e1)


def test_keys_identical_across_arms_and_mismatch_raises() -> None:
    w = _world()
    frames = {a: eh.arm_probs(a, w, PARAMS) for a in eh.ARMS}
    n = eh.assert_same_keys(frames)
    keys = eh.eval_keys(w["cur"])
    assert n == keys.height
    assert set(keys["season"].to_list()) == {2023, 2024}
    bad = dict(frames)
    bad["E1"] = frames["E1"].head(n - 1)
    with pytest.raises(ValueError):
        eh.assert_same_keys(bad)


# --------------------------------------------------------------------------- planted future


def test_planted_future_changes_no_prior_rating() -> None:
    w = _world()
    for start in (eh.E0_START, eh.E1_START):
        out = eh.planted_future(eh.combined_games(w, start), PARAMS)
        assert out["ok"], out


def test_a_score_edit_after_the_game_never_moves_its_logit() -> None:
    g = eh.combined_games(_world(), eh.E1_START)
    base = eh.production_logits(g, PARAMS)
    i = g.height // 2
    d = g["game_date"][i]
    hp = g["home_pts"].to_numpy().copy()
    hp[(g["game_date"] > d).to_numpy()] += 40
    hp[i] += 40  # its own score too
    new = eh.production_logits(g.with_columns(pl.Series("home_pts", hp)), PARAMS)
    assert new[i] == base[i]
    assert np.array_equal(new[: i + 1][(g["game_date"][: i + 1] <= d).to_numpy()], base[: i + 1])


# --------------------------------------------------------------------------- weights


def test_weights_only_in_e3() -> None:
    w = _world()
    tune = eh.tune_frame(w)
    ids = tune.sort(["game_date", "game_id"])["game_id"].to_list()
    flags = pl.concat([w["hist_flags"], w["cur_flags"]], how="vertical")
    assert {n: s.weighted for n, s in eh.ARM_SPECS.items()} == {
        "E0": False,
        "E1": False,
        "E2": False,
        "E3": True,
    }
    for a in ("E0", "E1", "E2"):
        assert np.all(eh.tuning_weights(ids, flags, eh.ARM_SPECS[a].weighted) == 1.0)
    w3 = eh.tuning_weights(ids, flags, True)
    hit = flags.filter(pl.col("covid_bubble") | pl.col("no_fans"))["game_id"].to_list()
    assert set(w3) == {0.5, 1.0}
    assert int((w3 == 0.5).sum()) == len(set(hit) & set(ids)) > 0
    # limited-fans-only games are NOT down-weighted by the rule as drafted
    lim = flags.filter(pl.col("limited_fans") & ~pl.col("covid_bubble") & ~pl.col("no_fans"))
    assert lim.height > 0
    pos = {g: i for i, g in enumerate(ids)}
    assert all(w3[pos[g]] == 1.0 for g in lim["game_id"].to_list() if g in pos)
    # weights change the objective; scoring (analyze) takes no weight argument
    arr = eh.prepare_arrays(tune)
    y = np.array([1.0 if h > a else 0.0 for h, a in zip(arr["hp"], arr["ap"], strict=True)])
    first = np.array([d == arr["date"][0] for d in arr["date"]])
    x = np.array([PARAMS[k] for k in eh.PARAM_NAMES])
    o1 = eh.tuning_objective(arr, y, np.ones(len(y)), first)(x)
    o3 = eh.tuning_objective(arr, y, w3, first)(x)
    assert o1 != o3
    import inspect

    assert "weight" not in inspect.signature(eh.analyze).parameters


def test_ga_is_deterministic_and_checkpointed(tmp_path: Path) -> None:
    w = _world()
    tune = eh.tune_frame(w)
    ones = np.ones(tune.height)
    a = eh.run_ga(tune, ones, maxiter=2, popsize=4)
    b = eh.run_ga(tune, ones, maxiter=2, popsize=4)
    assert a == b
    ck = eh.tuned_params("E2", w, cache=tmp_path, maxiter=1, popsize=4)
    again = eh.tuned_params("E2", w, cache=tmp_path, maxiter=99, popsize=99)  # checkpoint wins
    assert ck == again


# --------------------------------------------------------------------------- data guards + gate


def _make_db(path: Path, games: pl.DataFrame, flags: pl.DataFrame) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
        "away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    con.execute(
        "CREATE TABLE game_era_flags (game_id VARCHAR, season INTEGER, covid_bubble BOOLEAN, "
        "no_fans BOOLEAN, limited_fans BOOLEAN, shortened_season BOOLEAN, notes VARCHAR)"
    )
    con.register("g", games)
    con.execute("INSERT INTO games SELECT * FROM g")
    con.register("f", flags)
    con.execute("INSERT INTO game_era_flags SELECT * FROM f")
    con.close()


def test_load_world_reads_only_allowed_seasons_and_read_only(tmp_path: Path) -> None:
    w = _world()
    cur = pl.concat([w["cur"], _league((2025,), 3, 3)], how="vertical")
    cur_flags = compute_era_flags(cur.select("game_id", "game_date", "season", "home_team"))
    _make_db(tmp_path / "cur.duckdb", cur, cur_flags)
    _make_db(tmp_path / "hist.duckdb", w["hist"], w["hist_flags"])
    out = eh.load_world(str(tmp_path / "cur.duckdb"), str(tmp_path / "hist.duckdb"), wait_s=1.0)
    assert int(out["cur"]["season"].max()) == 2024  # the 2025 rows were never read
    assert int(out["hist"]["season"].max()) == 2021
    with pytest.raises(ValueError):
        eh.assert_seasons(cur, eh.MAX_SEASON)
    # a write connection from this module would be a bug: the opener is read-only
    con = eh._open_ro(tmp_path / "cur.duckdb", 1.0)
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM games")
    con.close()


def test_gate_checks_on_a_complete_and_an_incomplete_history(tmp_path: Path) -> None:
    w = _world()
    pq = tmp_path / "games"
    pq.mkdir()
    w["hist"].write_parquet(pq / "all.parquet")
    ok = eh.check_history_complete(w, pq)
    assert ok["detail"]["scores_non_null"] == w["hist"].height
    assert ok["ok"] is (w["hist"].height == eh.HIST_N)  # synthetic league is not 3,637 games
    short = dict(w)
    short["hist"] = w["hist"].with_columns(
        pl.when(pl.col("game_id") == w["hist"]["game_id"][0])
        .then(None)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    assert eh.check_history_complete(short, pq)["detail"]["scores_non_null"] == w["hist"].height - 1
    fl = eh.check_era_flags(w)
    assert fl["detail"]["rule_disagreements"] == 0 and fl["detail"]["missing"] == 0
    broken = dict(w)
    broken["hist_flags"] = w["hist_flags"].head(10)
    assert eh.check_era_flags(broken)["detail"]["missing"] == w["hist"].height - 10
    assert eh.check_era_flags(broken)["ok"] is False


def test_registry_excluded_ids(tmp_path: Path) -> None:
    w = _world()
    keys = eh.eval_keys(w["cur"])
    for s in eh.SEASONS:
        sub = keys.filter(pl.col("season") == s)
        if s == 2024:
            sub = sub.head(sub.height - 2)
        sub.select("game_id").write_parquet(tmp_path / f"game_rows_{s}.parquet")
    ex = eh.registry_excluded_ids(w["cur"], tmp_path)
    assert ex == sorted(keys.filter(pl.col("season") == 2024)["game_id"].to_list()[-2:])


def test_analyze_bh_and_verdict_shapes() -> None:
    w = _world()
    keys = eh.eval_keys(w["cur"])
    probs = {a: eh.arm_probs(a, w, PARAMS)["p"].to_numpy() for a in eh.ARMS}
    first = np.arange(keys.height) % 40
    res = eh.analyze(keys, probs, first)
    rows = res["table"]
    assert len(rows) == 2 * len(eh.ARMS)
    e0 = [r for r in rows if r["arm"] == "E0"]
    assert all(r["dlogloss"] == 0.0 for r in e0)
    for r in rows:
        if r["arm"] in eh.FAMILY:
            assert r["p_bh"] >= r["p"] - 1e-12
    v = eh.verdict(res)
    assert set(v) >= {"E1", "E2", "closed_on_select_season", "any_arm_passes"}
    assert eh.bh_adjust(np.array([0.01, 0.04])).tolist() == pytest.approx([0.02, 0.04])


def test_smoke_wiring(capsys: pytest.CaptureFixture[str]) -> None:
    eh.do_smoke()
    out = capsys.readouterr().out
    assert "smoke GA best" in out and "fast-production" in out
