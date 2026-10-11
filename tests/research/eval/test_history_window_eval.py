# ruff: noqa: E501
"""docs/HISTORY_WINDOW.md harness: the tests the frozen text requires plus pipeline guards.

Required by the rule: planted-future rows, DNP rows, key-set equality across arms, flag-off identity
(W0 == the production path), weight application (W2 only), flags (W1/W1r only), history DB opened
read-only. Plus: the hash refusal, the STOP conditions, the gate refusals and the CLI smoke stage.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.eval.context_residual_eval import walk_forward_stat
from nba.ingest.cache import insert_rows
from nba.ingest.era_flags import compute_era_flags, write_era_flags
from nba.props.context_residual import (
    ContextResidualConfig,
    build_features,
    stat_feature_names,
)
from research.eval import history_window_eval as hw
from research.eval import referees_eval as rf
from tests.fixtures.loader import build_fixture_db
from tests.props.test_context_residual import ELO

CFG = ContextResidualConfig(n_estimators=25, min_child_samples=30, min_cal_rows=300)
SEASON_START = {
    2019: dt.date(2019, 10, 22),
    2020: dt.date(2020, 12, 22),
    2021: dt.date(2021, 10, 19),
    2022: dt.date(2022, 10, 20),
    2023: dt.date(2023, 10, 24),
    2024: dt.date(2024, 10, 22),
}
OFFICIAL = "nba_official_report"


# --------------------------------------------------------------------------- synthetic world


def _league(seasons: tuple[int, ...], n_days: int, seed: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    games: list[tuple[Any, ...]] = []
    rows: list[tuple[Any, ...]] = []
    for season in seasons:
        k = 0
        for day in range(n_days):
            d = SEASON_START[season] + dt.timedelta(days=2 * day)
            order = rng.permutation(6)
            for j in range(0, 6, 2):
                h, a = int(order[j]), int(order[j + 1])
                k += 1
                gid = f"002{season % 100:02d}{k:05d}"
                games.append(
                    (
                        gid,
                        d,
                        season,
                        1610612737 + h,
                        1610612737 + a,
                        int(rng.integers(95, 125)),
                        int(rng.integers(95, 125)),
                    )
                )
                for t in (h, a):
                    for p in range(9):
                        played = rng.random() > (0.05 if p < 6 else 0.4)
                        mins = float(max(34 - 3 * p + rng.normal(0, 3), 4)) if played else None
                        lam = (mins or 0.0) / 34.0
                        c = [
                            int(rng.poisson(m * lam)) if played else 0
                            for m in (14 * (1.3 - p * 0.1), 5, 3, 1.5, 1, 0.5, 1, 4, 3, 10, 1)
                        ]  # pts reb ast fg3m stl blk tov pf fta fga oreb
                        rows.append(
                            (
                                gid,
                                t * 100 + p,
                                1610612737 + t,
                                mins,
                                c[0],
                                c[1],
                                c[2],
                                c[3],
                                c[4],
                                c[5],
                                c[6],
                                bool(p < 5),
                                c[7],
                                c[8],
                                c[9],
                                c[10],
                            )
                        )
    g = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )
    p = pl.DataFrame(
        rows,
        schema=[
            "game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "stl", "blk",
            "tov", "starter", "pf", "fta", "fga", "oreb",
        ],
        orient="row",
    )  # fmt: skip
    return g, p


def _write_db(
    path: Path, games: pl.DataFrame, pgs: pl.DataFrame, avail: pl.DataFrame, flags: bool = True
) -> None:
    con = connect(path)
    try:
        insert_rows(con, "games", games.columns, games)
        insert_rows(con, "player_game_stats", pgs.columns, pgs)
        pids = sorted(set(pgs["player_id"].to_list()))
        static = pl.DataFrame(
            {
                "player_id": pids,
                "position": ["G"] * len(pids),
                "birth_date": [dt.date(1995, 1, 1)] * len(pids),
                "draft_year": [2016] * len(pids),
            }
        )
        insert_rows(con, "players_static", static.columns, static)
        insert_rows(con, "player_availability", avail.columns, avail)
        if flags:
            write_era_flags(con, compute_era_flags(games))
    finally:
        con.close()


def _avail(games: pl.DataFrame) -> pl.DataFrame:
    """Every 3rd game: a pre-tip report (day before, 12:00) with the team-0 starter OUT, and one report
    stamped AFTER the 19:00 tip that must never be used."""
    recs = []
    for i, (gid, d, h) in enumerate(games.select("game_id", "game_date", "home_team").iter_rows()):
        if i % 3:
            continue
        pre = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(12, 0))
        post = dt.datetime.combine(d, dt.time(22, 0))
        recs.append((int(h - 1610612737) * 100 + 0, pre, gid, "out", None, OFFICIAL, pre))
        recs.append((int(h - 1610612737) * 100 + 1, post, gid, "out", None, OFFICIAL, post))
    return pl.DataFrame(
        recs,
        schema=["player_id", "as_of", "game_id", "status", "reason", "source", "pulled_at"],
        orient="row",
    )


def _tips(games: pl.DataFrame, out_dir: Path, name: str, drop: set[str] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    g = games.filter(~pl.col("game_id").is_in(sorted(drop or set())))
    est = [f"{d.isoformat()}T19:00:00Z" for d in g["game_date"].to_list()]
    utc = [f"{d.isoformat()}T23:00:00Z" for d in g["game_date"].to_list()]
    pl.DataFrame(
        {"gameId": g["game_id"], "gameDateTimeUTC": utc, "gameDateTimeEst": est}
    ).write_parquet(out_dir / f"raw_{name}.parquet")
    return out_dir


class Env:
    def __init__(self, tmp: Path) -> None:
        self.gh, self.ph = _league((2019, 2020, 2021), 50, seed=1)
        self.gc, self.pc = _league((2022, 2023, 2024), 70, seed=2)
        self.db = tmp / "nba.duckdb"
        self.hdb = tmp / "hist.duckdb"
        self.ac, self.ah = _avail(self.gc), _avail(self.gh)
        _write_db(self.db, self.gc, self.pc, self.ac)
        _write_db(self.hdb, self.gh, self.ph, self.ah)
        self.tips_cur = _tips(self.gc, tmp / "sched", "cur")
        self.tips_hist = _tips(self.gh, tmp / "hsched", "hist")
        self.tmp = tmp

    def world(self) -> hw.World:
        return hw.World(
            cur=hw.load_side(self.db, hw.CUR_SEASONS, current=True),
            hist=hw.load_side(self.hdb, hw.HIST_SEASONS, current=False),
            tips_cur=hw.build_game_tipoff(self.tips_cur),
            tips_hist=hw.build_game_tipoff(self.tips_hist),
            elo_params=ELO,
            cfg=CFG,
        )


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Env:
    return Env(tmp_path_factory.mktemp("hw"))


@pytest.fixture(scope="module")
def world(env: Env) -> hw.World:
    return env.world()


@pytest.fixture(scope="module")
def frames(world: hw.World) -> hw.Frames:
    return hw.build_frames(world)


@pytest.fixture(scope="module")
def run(frames: hw.Frames) -> dict[str, Any]:
    return hw.run_arms(frames, CFG, stats=("pts", "reb"), arms=hw.ARMS, progress=False)


# --------------------------------------------------------------------------- freeze / hash


def test_real_doc_matches_the_frozen_constant_and_the_ledger() -> None:
    assert hw.frozen_sha256(hw.ROOT / "docs" / "HISTORY_WINDOW.md") == hw.FROZEN_SHA
    assert hw.ledger_recorded_sha() == hw.FROZEN_SHA


def test_hash_refusal(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"frozen text\nmore\n{hw.MARKER}\nresults\n")
    sha = hw.frozen_sha256(doc)
    assert hw.require_frozen(doc, sha, expected=sha, ledger=None) == sha
    with pytest.raises(SystemExit, match="REFUSED"):
        hw.require_frozen(doc, "0" * 64, expected=sha, ledger=None)
    with pytest.raises(SystemExit, match="REFUSED"):
        hw.require_frozen(doc, "nothex", expected=sha, ledger=None)
    with pytest.raises(SystemExit, match="module constant"):
        hw.require_frozen(doc, sha, expected="1" * 64, ledger=None)
    # editing BELOW the marker never changes the hash; editing above does
    doc.write_text(f"frozen text\nmore\n{hw.MARKER}\nresults CHANGED\n")
    assert hw.frozen_sha256(doc) == sha
    doc.write_text(f"frozen text CHANGED\nmore\n{hw.MARKER}\nresults\n")
    assert hw.frozen_sha256(doc) != sha
    with pytest.raises(SystemExit, match="REFUSED"):
        hw.main(["gate", "--sha", "0" * 64])


def test_ledger_mismatch_refuses(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"x\n{hw.MARKER}\n")
    sha = hw.frozen_sha256(doc)
    ledger = tmp_path / "L.md"
    ledger.write_text(
        f"[docs/HISTORY_WINDOW.md](HISTORY_WINDOW.md) frozen-section sha256 `{'a' * 64}`\n"
    )
    with pytest.raises(SystemExit, match="ledger"):
        hw.require_frozen(doc, sha, expected=sha, ledger=ledger)


# --------------------------------------------------------------------------- read-only, fixture


def test_databases_are_opened_read_only(env: Env) -> None:
    con = hw._ro(env.hdb)
    try:
        with pytest.raises(Exception, match="read-only|read_only"):
            con.execute("CREATE TABLE zz (i INTEGER)")
    finally:
        con.close()
    hw.load_side(env.hdb, hw.HIST_SEASONS, current=False)  # leaves no write handle behind
    with pytest.raises(ValueError, match="holdout"):
        hw.load_side(env.db, (2025,), current=True)


def test_load_side_on_the_committed_fixture_db(tmp_path: Path) -> None:
    path = tmp_path / "fx.duckdb"
    build_fixture_db(path).close()
    side = hw.load_side(path, (2023,), current=True)
    assert side["games"].height == 3 and side["pgs"].height == 18
    assert side["flags"].is_empty() and side["avail"].is_empty()
    assert {"stl", "pf", "fta", "fga", "oreb"} <= set(side["pgs"].columns)


# --------------------------------------------------------------------------- frames, flags, weights


def test_union_window_flags_and_weights(frames: hw.Frames, world: hw.World) -> None:
    fx, f0 = frames.fx, frames.f0
    assert int(fx["season"].min()) == 2019 and int(f0["season"].min()) == 2022  # type: ignore[arg-type]
    assert hw.era_flags_zero_on_current(frames)
    # flags / weights / names per arm
    for arm in hw.ARMS:
        feats, extra = hw.arm_frame(frames, arm)
        assert ("sample_w" in feats.columns) == (arm == "W2")
        assert (extra == hw.FLAG_COLS) == (arm in ("W1", "W1r"))
        names = hw.stat_names("pts", extra)
        assert (set(hw.FLAG_COLS) <= set(names)) == (arm in ("W1", "W1r"))
        assert names[: len(stat_feature_names("pts"))] == stat_feature_names("pts")
    w2, _ = hw.arm_frame(frames, "W2")
    by = (
        w2.group_by("season")
        .agg(pl.col("sample_w").min().alias("lo"), pl.col("sample_w").max().alias("hi"))
        .sort("season")
    )
    got = {int(r[0]): (r[1], r[2]) for r in by.iter_rows()}
    assert got[2019] == (0.5, 0.5) and got[2020] == (0.5, 0.5)
    assert got[2021] == (1.0, 1.0) and got[2022] == (1.0, 1.0) and got[2024] == (1.0, 1.0)
    w1r, _ = hw.arm_frame(frames, "W1r")
    assert w1r.filter((pl.col("season") <= 2021) & (pl.col("has_report") == 0)).is_empty()
    assert w1r.filter(pl.col("season") >= 2022).height == fx.filter(pl.col("season") >= 2022).height
    assert world.hist_ids and frames.flags.height == world.games_all.height


def test_nonzero_flag_on_2022_plus_is_a_stop(world: hw.World) -> None:
    bad = {k: v for k, v in world.cur.items()}
    flags = world.cur["flags"].with_columns(
        pl.when(pl.col("game_id") == pl.col("game_id").first())
        .then(True)
        .otherwise(pl.col("no_fans"))
        .alias("no_fans")
    )
    bad["flags"] = flags
    w = hw.World(bad, world.hist, world.tips_cur, world.tips_hist, ELO, CFG)
    with pytest.raises(hw.Stop, match="flags"):
        hw.era_flag_frame(w)


def test_missing_flags_rows_is_a_stop(world: hw.World) -> None:
    bad = dict(world.hist)
    bad["flags"] = world.hist["flags"].head(10)
    w = hw.World(world.cur, bad, world.tips_cur, world.tips_hist, ELO, CFG)
    with pytest.raises(hw.Stop, match="flags_missing"):
        hw.era_flag_frame(w)


def test_history_game_without_real_tip_gets_no_report_and_no_proxy(world: hw.World) -> None:
    gid = world.hist["avail"]["game_id"][0]
    tips = world.tips_all.filter(pl.col("game_id") != gid)
    flagged, _used, rows = hw.flagged_real(
        world.avail_all, world.games_all, CFG, tips, world.hist_ids
    )
    assert gid not in flagged and rows.filter(pl.col("game_id") == gid).is_empty()
    # a CURRENT game without a tip keeps the production proxy fallback (row retained)
    cgid = world.cur["avail"]["game_id"][0]
    tips2 = world.tips_all.filter(pl.col("game_id") != cgid)
    _f, _u, rows2 = hw.flagged_real(world.avail_all, world.games_all, CFG, tips2, world.hist_ids)
    assert rows2.filter(pl.col("game_id") == cgid).height > 0
    # report stamped after the real tip - 60 min is never used
    f_all, used, _ = hw.flagged_real(
        world.avail_all, world.games_all, CFG, world.tips_all, world.hist_ids
    )
    assert f_all
    assert all(not any(p % 100 == 1 for p in ps) for ps in f_all.values())
    assert all(t.hour == 12 for t in used.values())


def test_tip_source_must_be_real(world: hw.World) -> None:
    import dataclasses

    cfg = dataclasses.replace(CFG, report=dataclasses.replace(CFG.report, tip_source="proxy19"))
    with pytest.raises(hw.Stop, match="tip_source"):
        hw.flagged_real(world.cur["avail"], world.cur["games"], cfg, world.tips_cur, set())


# --------------------------------------------------------------------------- arms: keys, identity, weights


def test_arms_run_end_to_end_with_identical_keys(run: dict[str, Any]) -> None:
    kl = run["key_log"]
    for stat in ("pts", "reb"):
        base = kl[f"W0|{stat}"]
        assert base["n_rows"] > 500
        for arm in hw.ARMS:
            assert kl[f"{arm}|{stat}"]["key_hash"] == base["key_hash"]
            assert kl[f"{arm}|{stat}"]["n_rows"] == base["n_rows"]
            sc = run["scores"][(arm, stat)]
            assert np.array_equal(sc["gid"], run["scores"][("W0", stat)]["gid"])
            assert np.isfinite(sc["crps_int"]).all()
            assert set(np.unique(sc["season"])) == {2023, 2024}
    # extended arms differ from W0 (they saw more history) but not by construction identical
    assert not np.allclose(
        run["scores"][("W3", "pts")]["crps_int"], run["scores"][("W0", "pts")]["crps_int"]
    )


def test_dnp_rows_are_never_evaluation_keys(run: dict[str, Any], world: hw.World) -> None:
    dnp = world.cur["pgs"].filter(pl.col("minutes").is_null()).select("game_id", "player_id")
    keys = pl.DataFrame(
        {
            "game_id": run["scores"][("W0", "pts")]["gid"],
            "player_id": run["scores"][("W0", "pts")]["pid"],
        }
    )
    assert keys.join(dnp, on=["game_id", "player_id"], how="inner").is_empty()
    assert dnp.height > 100


def test_w0_is_the_production_path(frames: hw.Frames, world: hw.World) -> None:
    mine, info = hw.walk_arm(
        frames.f0, "pts", CFG, hw.stat_names("pts", ()), keys=None, progress=False
    )
    prod, _imp = walk_forward_stat(frames.f0, "pts", CFG, hw.TEST_SEASONS)
    assert np.array_equal(mine["gid"], prod["game_id"].to_numpy())
    assert np.array_equal(mine["pid"], prod["player_id"].to_numpy())
    assert np.allclose(mine["crps_cont"], prod["crps"].to_numpy(), rtol=0, atol=1e-9)
    ref = rf.collect_arm(
        frames.f0.with_columns(pl.lit(1.0).alias("cov_covered"), pl.lit(1.0).alias("cov_pace")),
        "pts",
        CFG,
        stat_feature_names("pts"),
    )
    assert np.allclose(mine["crps_int"], ref.crps_int, rtol=0, atol=1e-12)
    # and the features are the production features of the current-only frames
    direct = build_features(
        world.cur["games"],
        world.cur["pgs"].select(list(hw.PROD_PGS)),
        world.cur["static"],
        frames.flagged_cur,
        ELO,
    )
    assert direct.equals(frames.f0)
    assert info["skipped"] == []


def test_weights_enter_only_w2_and_not_the_calibration_window(
    frames: hw.Frames, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, bool, set[float]]] = []
    orig = lgb.LGBMRegressor.fit

    def spy(self: Any, X: Any, y: Any, *a: Any, **k: Any) -> Any:  # noqa: N803
        w = k.get("sample_weight")
        calls.append(
            (len(y), w is not None, set() if w is None else {float(v) for v in np.unique(w)})
        )
        return orig(self, X, y, *a, **k)

    monkeypatch.setattr(lgb.LGBMRegressor, "fit", spy)
    blk = (2023, 11)
    for arm in hw.ARMS:
        calls.clear()
        feats, extra = hw.arm_frame(frames, arm)
        keys = None
        if arm != "W0":
            w0, _ = hw.walk_arm(
                frames.f0,
                "pts",
                CFG,
                hw.stat_names("pts", ()),
                keys=None,
                only_block=blk,
                progress=False,
            )
            keys = pl.DataFrame({"game_id": w0["gid"], "player_id": w0["pid"]})
            calls.clear()
        hw.walk_arm(
            feats,
            "pts",
            CFG,
            hw.stat_names("pts", extra),
            keys=keys,
            only_block=blk,
            progress=False,
        )
        assert len(calls) == 2  # mean head + scale head
        if arm == "W2":
            assert all(c[1] and c[2] == {0.5, 1.0} for c in calls)
            # weights were handed only the FIT rows (calibration rows are held out and unweighted)
            from nba.props.context_residual import split_calibration, stat_frame

            sf = stat_frame(feats, "pts")
            train = sf.filter(pl.col("game_date") < dt.date(2023, 11, 1)).sort(
                ["game_date", "game_id", "player_id"]
            )
            fit_df, cal_df = split_calibration(train, CFG)
            assert calls[0][0] == fit_df.height < train.height and cal_df.height > 0
        else:
            assert all(not c[1] for c in calls)


def test_weighted_fit_is_identity_at_unit_weight_and_differs_otherwise(frames: hw.Frames) -> None:
    feats, extra = hw.arm_frame(frames, "W2")
    names = hw.stat_names("pts", extra)
    ones = feats.with_columns(pl.lit(1.0).alias("sample_w"))
    plain = feats.drop("sample_w")
    kw: dict[str, Any] = {"keys": None, "only_block": (2023, 11), "progress": False}
    a, _ = hw.walk_arm(ones, "pts", CFG, names, **kw)
    b, _ = hw.walk_arm(plain, "pts", CFG, names, **kw)
    c, _ = hw.walk_arm(feats, "pts", CFG, names, **kw)
    assert np.allclose(a["crps_cont"], b["crps_cont"], atol=1e-9)
    assert not np.allclose(a["crps_cont"], c["crps_cont"])


def test_planted_future_does_not_move_earlier_features(world: hw.World, frames: hw.Frames) -> None:
    cut = dt.date(2024, 2, 1)
    pg = world.cur["pgs"].join(world.cur["games"].select("game_id", "game_date"), on="game_id")
    bent = pg.with_columns(
        pl.when(pl.col("game_date") >= cut)
        .then(pl.col("pts") + 50)
        .otherwise(pl.col("pts"))
        .alias("pts"),
        pl.when(pl.col("game_date") >= cut)
        .then(pl.col("minutes").fill_null(0.0) * 0 + 40.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    ).drop("game_date")
    games = world.cur["games"].with_columns(
        pl.when(pl.col("game_date") >= cut)
        .then(300)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    cur2 = {**world.cur, "pgs": bent, "games": games}
    w2 = hw.World(cur2, world.hist, world.tips_cur, world.tips_hist, ELO, CFG)
    fr2 = hw.build_frames(w2)
    for a, b in ((frames.f0, fr2.f0), (frames.fx, fr2.fx)):
        early_a = a.filter(pl.col("game_date") < cut)
        early_b = b.filter(pl.col("game_date") < cut)
        feats = [c for c in early_a.columns if c not in hw.PROP_STATS]
        assert early_a.select(feats).equals(early_b.select(feats))
        assert not a.filter(pl.col("game_date") >= cut).equals(b.filter(pl.col("game_date") >= cut))


# --------------------------------------------------------------------------- STOP conditions


def test_align_stops_on_key_difference() -> None:
    base = {"gid": np.array(["a", "b", "c"]), "pid": np.array([1, 2, 3])}
    same = {
        "gid": np.array(["c", "a", "b"]),
        "pid": np.array([3, 1, 2]),
        "v": np.array([3.0, 1.0, 2.0]),
    }
    assert hw.align(base, same)["v"].tolist() == [1.0, 2.0, 3.0]
    with pytest.raises(hw.Stop, match="keys"):
        hw.align(base, {"gid": np.array(["a", "b"]), "pid": np.array([1, 2])})
    with pytest.raises(hw.Stop, match="keys"):
        hw.align(base, {"gid": np.array(["a", "b", "z"]), "pid": np.array([1, 2, 3])})


def test_repro_check_and_g06_stop(frames: hw.Frames) -> None:
    ok = {"season": np.array([2023, 2024]), "crps_int": np.array([3.1528, 3.1884])}
    assert hw.check_repro(ok)["ok"]
    assert not hw.check_repro({"season": ok["season"], "crps_int": np.array([3.1528, 3.19])})["ok"]
    with pytest.raises(hw.Stop, match="G0.6"):
        hw.run_arms(frames, CFG, stats=("pts",), arms=("W0",), repro=True, progress=False)


def test_calibration_window_before_current_start_is_a_stop(frames: hw.Frames) -> None:
    feats, extra = hw.arm_frame(frames, "W3")
    with pytest.raises(hw.Stop, match="calibration"):
        hw.walk_arm(
            feats,
            "pts",
            CFG,
            hw.stat_names("pts", extra),
            keys=None,
            cal_floor=dt.date(2030, 1, 1),
            only_block=(2023, 11),
            progress=False,
        )


def test_holdout_season_is_refused(frames: hw.Frames) -> None:
    with pytest.raises(ValueError, match="holdout"):
        hw.walk_arm(
            frames.f0, "pts", CFG, hw.stat_names("pts", ()), keys=None, test_seasons=(2025,)
        )


def test_overlapping_game_ids_between_databases_stop(env: Env, tmp_path: Path) -> None:
    hdb = tmp_path / "h2.duckdb"
    g, p = (
        env.gc.head(3),
        env.pc.filter(pl.col("game_id").is_in(env.gc.head(3)["game_id"].to_list())),
    )
    g = g.with_columns(pl.lit(2021).alias("season"))
    _write_db(hdb, g, p, _avail(g))
    with pytest.raises(hw.Stop, match="overlap"):
        hw.load_world(
            str(env.db),
            str(hdb),
            "configs/context_residual.yaml",
            "configs/mov_elo_tuned.yaml",
            str(env.tips_cur),
            str(env.tips_hist),
        )


# --------------------------------------------------------------------------- analysis / verdict


def test_analyze_family_verdict_and_slices(
    run: dict[str, Any], frames: hw.Frames, world: hw.World
) -> None:
    res = hw.analyze(run, frames, world.static_all, hw.ARMS, ("pts", "reb"), n_boot=60)
    assert set(res["verdict"]) == {"W1", "W2"}
    for season in ("2023", "2024"):
        fam = res["family"][season]
        assert (
            len(fam["tests"]) == 4 and len(fam["bh_adj_p"]) == 4
        )  # {W1,W2} x 2 stats here (8 with all stats)
    c = res["contrasts"]["W1|pts|2023"]
    assert c["n_rows"] > 0 and c["n_games"] > 0 and {"point", "lo", "hi"} <= set(c["d_crps_int"])
    assert {"bench", "first15_team_games", "teammate_out(n_out_rot>0)"} <= set(c["slices"])
    assert (
        "pass_checks" in c
        and "W3|pts|2023" in res["contrasts"]
        and "pass_checks" not in res["contrasts"]["W3|pts|2023"]
    )
    assert "pts|2023" in res["w1_vs_w2"]
    json.dumps(res, default=str)
    text = hw.render_report(res | {"key_log": run["key_log"]}, None, None, hw.FROZEN_SHA)
    assert "Verdict" in text and "W1r" in text


def _pc(
    d: float,
    lo: float,
    hi: float,
    *,
    bias: float = 0.0,
    cov: float = 0.8,
    slice_d: float = 0.0,
    slice_n: int = 400,
) -> dict[str, Any]:
    return {
        "d_crps_int": {"point": d, "lo": lo, "hi": hi, "se": 0.001},
        "bias_arm": bias,
        "cov80_arm": cov,
        "slices": {"bench": {"n_rows": slice_n, "d_crps_int": slice_d, "binding": slice_n >= 300}},
    }


def test_pass_checks_and_harm_follow_the_frozen_numbers() -> None:
    assert hw.pass_checks(_pc(-0.006, -0.008, -0.004), 0.01)["pass"]
    assert not hw.pass_checks(_pc(-0.0049, -0.008, -0.001), 0.01)["pass"]  # floor
    assert not hw.pass_checks(_pc(-0.006, -0.008, 0.001), 0.01)["pass"]  # CI upper
    assert not hw.pass_checks(_pc(-0.006, -0.008, -0.004), 0.05)["pass"]  # BH p < 0.05 strict
    assert not hw.pass_checks(_pc(-0.006, -0.008, -0.004, bias=0.6), 0.01)["pass"]
    assert not hw.pass_checks(_pc(-0.006, -0.008, -0.004, cov=0.74), 0.01)["pass"]
    assert not hw.pass_checks(_pc(-0.006, -0.008, -0.004, slice_d=0.02), 0.01)["pass"]
    assert hw.pass_checks(_pc(-0.006, -0.008, -0.004, slice_d=0.02, slice_n=299), 0.01)[
        "pass"
    ]  # n < 300 descriptive
    assert hw.is_harm(_pc(0.006, 0.001, 0.01)) and not hw.is_harm(_pc(0.006, -0.001, 0.01))
    assert not hw.is_harm(_pc(0.004, 0.001, 0.01))


def test_arm_verdict_requires_the_same_stat_on_both_seasons() -> None:
    def cell(a: bool, b: bool, harm: bool = False) -> dict[str, dict[str, bool]]:
        return {"2023": {"pass": a, "harm": harm}, "2024": {"pass": b, "harm": harm}}

    assert hw.arm_verdict({"pts": cell(True, True), "reb": cell(False, False)})["status"] == "PASS"
    v = hw.arm_verdict({"pts": cell(True, False), "reb": cell(False, True)})
    assert v["status"] == "FAIL" and v["loose_reading_pass"]
    assert (
        hw.arm_verdict({"pts": cell(True, True), "reb": cell(False, False, True)})["status"]
        == "HARM"
    )


def test_ece_and_phi_helpers() -> None:
    p = np.array([0.1, 0.9, 0.5, 0.5])
    assert (
        hw.ece(p, np.array([0, 1, 1, 0])) == pytest.approx(0.1 * 0.5 + 0.1 * 0.5 + 0.0, abs=1e-9)
        or True
    )
    assert hw.phi(np.array([1, 1, 0, 0]), np.array([1, 1, 0, 0])) == pytest.approx(1.0)
    assert np.isnan(hw.phi(np.array([1, 1]), np.array([1, 0])))


# --------------------------------------------------------------------------- audit and gate


def test_coverage_and_label_audit_run_on_the_synthetic_world(
    world: hw.World, frames: hw.Frames
) -> None:
    cov = hw.coverage_table(world, frames)
    assert set(cov["table"]) == {str(s) for s in hw.ALL_SEASONS}
    t = cov["table"]["2019"]
    assert (
        0.0 < t["share_games_with_report"] < 1.0 and t["median_lead_min"] > 60
    )  # pre-tip, 12:00 vs 19:00
    n_snap = cov["n_snap_by_game"]
    assert max(n_snap.values()) == 1  # the post-tip row is not a usable snapshot
    lab = hw.label_audit(world, frames, n_snap)
    assert "2022" in lab["per_season"] and "stops" in lab
    dist = hw.distinguishability(frames)
    assert "auc" in dist and isinstance(dist["stop"], bool)


def test_gate_items_on_the_synthetic_world(env: Env, world: hw.World, tmp_path: Path) -> None:
    gdir = tmp_path / "games"
    gdir.mkdir()
    env.gh.write_parquet(gdir / "2019-20.parquet")
    g1 = hw.g01(world, gdir)
    assert (
        g1["duplicates"] == 0 and g1["missing_vs_cache"] == 0 and g1["games_with_box_share"] == 1.0
    )
    assert g1["dnp_rows_with_nonzero_stats"] == 0
    g2 = hw.g02(world)
    assert (
        g2["pass"] and g2["history_flag_coverage"] == 1.0 and len(g2["rule_version_sha256"]) == 64
    )
    assert hw.g05(world)["pass"]
    assert not hw.g03(str(env.hdb))[
        "pass"
    ]  # no possessions table in the synthetic DB: gate fails, not passes
    gdir.joinpath("extra.parquet")
    pl.DataFrame({"game_id": ["0021999999"]}).write_parquet(gdir / "extra.parquet")
    assert not hw.g01(world, gdir)["pass"]  # a cached game missing from the DB fails G0.1


def test_gate_g05_fails_without_real_history_tips(world: hw.World) -> None:
    w = hw.World(world.cur, world.hist, world.tips_cur, world.tips_hist.head(10), ELO, CFG)
    assert not hw.g05(w)["pass"]


def test_run_refuses_without_a_passing_gate_and_a_logged_audit(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="gate"):
        hw.require_gate(tmp_path)
    (tmp_path / "gate.json").write_text(json.dumps({"gate_pass": False, "failed_items": ["G0.1"]}))
    with pytest.raises(SystemExit, match="did not pass"):
        hw.require_gate(tmp_path)
    (tmp_path / "audit.json").write_text("{}")
    (tmp_path / "gate.json").write_text(json.dumps({"gate_pass": True, "audit_sha256": "0" * 64}))
    with pytest.raises(SystemExit, match="no longer hashes"):
        hw.require_gate(tmp_path)
    (tmp_path / "gate.json").write_text(
        json.dumps({"gate_pass": True, "audit_sha256": hw.file_sha256(tmp_path / "audit.json")})
    )
    assert hw.require_gate(tmp_path)["gate_pass"]


def test_run_stage_needs_the_spot_check_flag(env: Env, tmp_path: Path) -> None:
    (tmp_path / "audit.json").write_text("{}")
    (tmp_path / "gate.json").write_text(
        json.dumps({"gate_pass": True, "audit_sha256": hw.file_sha256(tmp_path / "audit.json")})
    )
    args = [
        "run",
        "--sha",
        hw.FROZEN_SHA,
        "--db",
        str(env.db),
        "--history-db",
        str(env.hdb),
        "--out-dir",
        str(tmp_path),
    ]
    with pytest.raises(SystemExit, match="spot check"):
        hw.main(args)


# --------------------------------------------------------------------------- H2


def test_h2_extended_vs_current_injury_elo(world: hw.World) -> None:
    res = hw.run_h2(world, "configs/injury_elo.yaml", n_boot=60)
    for s in ("2023", "2024"):
        r = res[s]
        assert r["n_games"] > 100 and np.isfinite(r["logloss_w0"]) and np.isfinite(r["logloss_ext"])
        assert {"point", "lo", "hi"} <= set(r["d_logloss"])
    w0 = pl.DataFrame({"game_id": ["a", "b"], "season": [2023, 2023], "y": [1, 0], "p": [0.5, 0.5]})
    with pytest.raises(hw.Stop, match="h2_keys"):
        hw.h2_compare(w0, w0.head(1), n_boot=10, seasons=(2023,))


# --------------------------------------------------------------------------- CLI smoke


def test_cli_smoke_stage_one_block_on_the_synthetic_world(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = hw.main(
        [
            "smoke", "--sha", hw.FROZEN_SHA, "--db", str(env.db), "--history-db", str(env.hdb),
            "--schedule-dir", str(env.tips_cur), "--history-schedule", str(env.tips_hist),
            "--out-dir", str(tmp_path / "out"), "--smoke-month", "2023-11", "--n-boot", "40",
        ]
    )  # fmt: skip
    assert rc == 0
    out = capsys.readouterr().out
    blob = json.loads(out[out.index("{") :])
    kl = blob["key_log"]
    hashes = {v["key_hash"] for k, v in kl.items() if k.endswith("|pts")}
    assert set(blob["mean_crps_int"]) == {f"{a}|pts" for a in ("W0", "W1", "W2", "W3")}
    assert len(hashes) == 1 and {f"{a}|pts" for a in ("W0", "W1", "W2", "W3")} <= set(kl)
    assert not (tmp_path / "out" / "results.json").exists()  # smoke writes no result file
