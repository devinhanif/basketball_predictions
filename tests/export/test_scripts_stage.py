# ruff: noqa: E501, F811
"""The game-scripts export step: payload shape, calibration equality at the main line, no price in the
combo file, the market block from the engine's own EV code, who-sits support, and the guard (the step
can never fail the export). Recorded / synthetic inputs only; no network, no live DB."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import nba.export.combo_stage as cs
from nba.export.sits_schema import COLUMNS  # noqa: F401  (the column list the sits rows follow)
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts
from nba.parlay.config import load_config
from nba.parlay.ev import fee_per_contract
from nba.parlay.kalshi_map import load_team_abbr
from nba.parlay.shadow import TrackRecord
from nba.props import full_support
from tests.export.test_export import _build, src  # noqa: F401  (fixture)

DAY = date(2026, 11, 10)
TIP = datetime(2026, 11, 10, 23, 30)
MADE = TIP - timedelta(minutes=90)
HOME, AWAY = load_team_abbr()["PHI"], load_team_abbr()["BOS"]
CAL = lc.load_map()
GM = {
    "params": {
        "fit_seasons": [2022, 2023, 2024], "halflife": 20.0, "league_total": 227.6,
        "margin_b": 0.58, "margin_s": 6.97, "margin_sd": 13.63, "n_margin": 3951,
        "n_total": 3951, "prior_k": 8.0, "total_a": -176.4, "total_c": 1.774, "total_sd": 18.82,
    }
}  # fmt: skip
PLAYERS = {  # pid -> (team, {stat: (mean, sd, main)}, p_play, starter_rate)
    101: (HOME, {"pts": (24.0, 7.0, 23.5), "reb": (6.0, 2.8, 5.5), "ast": (5.0, 2.4, 4.5)}, 0.97, 0.99),
    102: (HOME, {"pts": (9.0, 4.5, 8.5), "reb": (3.0, 1.9, 2.5)}, 0.9, 0.1),
    201: (AWAY, {"pts": (21.0, 6.5, 20.5), "reb": (8.0, 3.0, 7.5)}, 0.95, 0.95),
    202: (AWAY, {"pts": (5.0, 3.5, None), "reb": (2.0, 1.5, None)}, 0.4, 0.05),
}  # fmt: skip


def _prop(
    pid: int, stat: str, mean: float, sd: float, main: float | None, pp: float, sr: float
) -> dict[str, Any]:
    rng = np.random.default_rng(pid * 7 + len(stat))
    q = np.sort(np.clip(rng.normal(mean, sd, 199), 0, None))  # equally weighted samples
    fs = full_support.from_samples(q, stat)
    qg = [float(np.quantile(q, t)) for t in np.linspace(0.05, 0.95, 19)]
    line_cal = None
    if main is not None:  # what line_cal_fields stores: the pair stamp and the calibrated P(over)
        qs = ts.quartiles({"pred": {"q_grid": qg}})
        po = ts.p_over({"p_ge_full": {"c": fs["c"], "n": fs["n"]}}, main)
        assert qs is not None and po is not None
        pc = CAL.apply("draftkings", stat, po, main, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, sr)
        line_cal = {"main_line": main, "price_at": "2026-11-10T21:55:00Z", "p_over_raw": po,
                    "p_over_cal": pc}  # fmt: skip
    return {
        "run_id": "r1", "made_at": MADE, "game_id": "G1", "tipoff": TIP,
        "model_name": "props_context_residual", "comparison_only": False, "player_id": pid,
        "stat": stat, "p_play": pp, "team_id": PLAYERS[pid][0],
        "prediction_json": json.dumps({"q_grid": qg, "starter_rate": sr}),
        "p_ge_full_counts": fs["c"], "p_ge_full_n": fs["n"],
        "line_cal": line_cal,
    }  # fmt: skip


def _inputs() -> dict[str, Any]:
    props = [
        _prop(pid, stat, *v, pp, sr)
        for pid, (_t, stats, pp, sr) in PLAYERS.items()
        for stat, v in stats.items()
    ]
    return {
        "games": [
            {"game_id": "G1", "game_date": DAY, "home_team": HOME, "away_team": AWAY, "tipoff": TIP}
        ],
        "win_probs": [
            {
                "game_id": "G1",
                "model_name": "rung0_injury_elo",
                "made_at": MADE,
                "run_id": "r1",
                "p_home": 0.62,
                "comparison_only": False,
            }
        ],  # fmt: skip
        "props": props,
        "pplay": [
            {"game_id": "G1", "player_id": pid, "made_at": MADE, "p_play_primary": v[2]}
            for pid, v in PLAYERS.items()
        ],
        "hist": [
            ("H1", DAY - timedelta(days=2), HOME, AWAY, 118, 110),
            ("H2", DAY - timedelta(days=1), AWAY, HOME, 105, 99),
        ],  # fmt: skip
        "names": {pid: f"Player {pid}" for pid in PLAYERS},
        "abbr": {HOME: "PHI", AWAY: "BOS"},
    }


@pytest.fixture
def gm_path(tmp_path: Path) -> Path:
    p = tmp_path / "joint_model.json"
    p.write_text(json.dumps(GM))
    return p


def _build_stage(gm_path: Path, **kw: Any) -> cs.StageResult:
    i = _inputs()
    return cs.build(
        DAY, i["games"], i["win_probs"], i["props"], i["pplay"], kw.pop("sits", []), i["hist"],
        i["names"], i["abbr"], made_at=MADE + timedelta(minutes=5), code_version="abc123",
        game_model_path=gm_path, **kw,
    )  # fmt: skip


def _keys(o: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(str(k))
            out |= _keys(v)
    elif isinstance(o, list):
        for v in o:
            out |= _keys(v)
    return out


def test_combo_payload_shape_and_no_price(gm_path: Path) -> None:
    r = _build_stage(gm_path)
    assert r.info["status"] == "ok" and r.combo is not None
    c = r.combo
    assert c["schema"] == "combo_export_v1" and c["night"] == "2026-11-10"
    g = c["games"][0]
    assert (g["home_abbr"], g["away_abbr"]) == ("PHI", "BOS")
    assert {p["player_id"] for p in g["players"]} == {101, 102, 201, 202}
    assert set(g["scripts"]) == {"blowout", "close", "shootout", "grind"}
    assert all(v["status"] == "exploratory" for v in g["scripts"].values())
    assert set(c["shared"]["scripts"]["status"]) == set(g["scripts"])
    banned = {"ask", "bid", "ev", "fee", "price", "stake", "kelly", "market", "ev_raw"}
    assert not (_keys(c) & banned)  # the combo file is price-free
    for p in g["players"]:
        for e in p["stats"].values():
            s = e["surv"]
            assert all(a >= b for a, b in zip(s, s[1:], strict=False))
            assert e["cal_k"][0] <= e["cal_k"][1]
    # sd and mu come from the game model
    assert g["game"]["sd_m"] == 13.63 and g["game"]["sd_t"] == 18.82
    assert abs(g["game"]["mu_m"] - (0.58 + 6.97 * np.log(0.62 / 0.38))) < 0.01


def test_calibrated_grid_equals_line_cal_at_the_main_line(gm_path: Path) -> None:
    r = _build_stage(gm_path)
    assert r.combo is not None
    cal = lc.load_map()
    from nba.parlay import combo as C

    checked = 0
    for p in r.combo["games"][0]["players"]:
        for stat, e in p["stats"].items():
            if e["main_line"] is None:
                continue
            row = next(
                x
                for x in _inputs()["props"]
                if x["player_id"] == p["player_id"] and x["stat"] == stat
            )
            pred = json.loads(row["prediction_json"])
            qs = ts.quartiles({"pred": pred})
            assert qs is not None
            po = ts.p_over(
                {"p_ge_full": {"c": row["p_ge_full_counts"], "n": row["p_ge_full_n"]}},
                e["main_line"],
            )
            assert po is not None
            want = cal.apply(
                "draftkings",
                stat,
                po,
                e["main_line"],
                qs[1],
                (qs[2] - qs[0]) / lc.IQR_TO_SD,
                pred["starter_rate"],
            )
            got = C.surv_at(e, int(e["main_line"]) + 1)
            assert abs(got - want) < 1.5e-4, (p["player_id"], stat, got, want)
            checked += 1
    assert checked >= 6


def test_slider_and_sits_support_in_the_payload(gm_path: Path) -> None:
    sits = [
        {"game_id": "G1", "team_id": HOME, "sitter_id": 101, "sitter_status": "questionable",
         "player_id": 102, "stat": "pts", "delta_mean": 1.234},
        {"game_id": "G1", "team_id": HOME, "sitter_id": 101, "sitter_status": "questionable",
         "player_id": 102, "stat": "reb", "delta_mean": -0.4},
        {"game_id": "G1", "team_id": HOME, "sitter_id": 101, "sitter_status": "questionable",
         "player_id": 102, "stat": "ast", "delta_mean": 0.9},
        {"game_id": "G1", "team_id": HOME, "sitter_id": 101, "sitter_status": "questionable",
         "player_id": 102, "stat": "pts", "delta_mean": 0.01},
    ]  # fmt: skip
    r = _build_stage(gm_path, sits=sits)
    assert r.combo is not None
    s = r.combo["games"][0]["sits"]
    assert s["sitters"] == [
        {
            "sitter": 101,
            "team": HOME,
            "status": "questionable",
            "pts": [[102, 1.23]],
            "reb_dir": [[102, -1]],
        }
    ]  # ast never; |delta| < 0.05 dropped; reb is a direction only


def test_picks_without_asks_carry_no_market_and_the_lock_flag(gm_path: Path) -> None:
    r = _build_stage(gm_path)
    assert r.scripts is not None
    assert r.scripts["lock"]["line_cal_live"] is True and r.scripts["status"] == "exploratory"
    g = r.scripts["games"][0]
    for blk in g["picks"].values():
        assert blk["status"] == "exploratory"
        for leg in blk["legs"]:
            assert leg["market"] is None and leg["p_play"] >= 0.5
    assert r.info["n_priced_picks"] == 0
    # the picks file never names a stake
    assert not (_keys(r.scripts) & {"stake", "kelly", "contracts"})


def test_market_block_uses_the_engine_ev_code(gm_path: Path) -> None:
    cfg = load_config()
    # a Kalshi YES ask at a threshold the leg ranks on: take player 101's main-line over
    probe = _build_stage(gm_path)
    assert probe.combo is not None
    from nba.parlay import combo as C

    pl = next(p for p in probe.combo["games"][0]["players"] if p["player_id"] == 101)
    k = int(pl["stats"]["pts"]["main_line"]) + 1
    asks = {("G1", 101, "pts", k): {"ticker": "KXNBAPTS-X-101-24", "series": "KXNBAPTS",
                                    "ask": 0.55, "bid": 0.52, "price_ts": "2026-11-10T21:55:00Z"}}  # fmt: skip
    r = _build_stage(gm_path, asks=asks, cfg=cfg, track=TrackRecord(0, None, None, 0.0))
    assert r.scripts is not None
    priced = [
        leg
        for blk in r.scripts["games"][0]["picks"].values()
        for leg in blk["legs"]
        if leg["market"] is not None
    ]
    assert priced, "the priced leg must be ranked in at least one script"
    for leg in priced:
        m = leg["market"]
        assert leg["player_id"] == 101 and m["threshold"] == k
        p_over = C.surv_at(pl["stats"]["pts"], k)
        fee = fee_per_contract(0.55, cfg.fee, series="KXNBAPTS")
        assert not ({"ev_raw", "ev_raw_low", "p_low"} & set(m))  # m6: only the conservative bound
        assert m["ev_low"] <= m["ev"] + 1e-9 or m["model_weight"] == 0.0
        assert (
            abs(m["ev"] - (m["p_over"] * 0 + (0.5 * (0.55 + 0.52)) - 0.55 - fee)) < 2e-4
        )  # market mid - ask - fee
        assert abs(m["fee"] - fee) < 1e-4 and abs(m["spread"] - 0.03) < 1e-9
        assert abs(m["p_over"] - p_over) < 1e-4 and m["p_model_incl_dnp"] <= m["p_over"]
        assert m["verdict"] == "no_positive_ev_found" and m["n_settled_track"] == 0
        assert m["model_weight"] == 0.0  # no track record: the market wins entirely
        assert m["ev"] < 0  # shrunk to the market: ask + fee above it
        assert "p_script" in leg and leg["p_leg"] == round(
            p_over, 4
        )  # unconditional, not the script P
    assert r.info["verdicts"] == {"no_positive_ev_found": len(priced)}


def test_ask_index_maps_through_the_engine_and_drops_stale_and_post_tip(gm_path: Path) -> None:
    i = _inputs()
    base = {"series_ticker": "KXNBAPTS", "event_ticker": "KXNBAPTS-26NOV10BOSPHI", "title": "x",
            "player_id": 101, "stat": "pts", "yes_bid": 0.5, "yes_ask": 0.55}  # fmt: skip
    now = datetime(2026, 11, 10, 22, 0)

    def mk(t: int, ts_: datetime, **kw: Any) -> dict[str, Any]:
        return {
            **base,
            "ticker": f"KXNBAPTS-26NOV10BOSPHI-PHIPLAYER-{t}",
            "threshold": float(t),
            "ts": ts_,
            **kw,
        }

    fresh = now - timedelta(minutes=10)
    markets = [
        mk(20, fresh),
        mk(25, now - timedelta(hours=3)),  # stale
        mk(30, TIP + timedelta(minutes=1)),  # after tip
        mk(15, fresh, yes_ask=None),
        {**mk(10, fresh), "player_id": 999, "title": "nobody"},  # no model row: unmapped
        {**mk(5, fresh), "series_ticker": "KXNBAGAME", "stat": None, "player_id": None},
    ]
    asks, left = cs.ask_index(markets, load_team_abbr(), DAY, i["games"], i["props"], now)
    assert set(asks) == {("G1", 101, "pts", 20)}
    assert left["quote_stale"] == 1 and left["quote_after_tip"] == 1 and left["no_ask"] == 1
    assert left["no_model_for_player"] == 1


def test_game_without_a_win_row_or_props_is_skipped_loudly(gm_path: Path) -> None:
    i = _inputs()
    r = cs.build(DAY, i["games"], [], i["props"], i["pplay"], [], i["hist"], i["names"], i["abbr"],
                 made_at=MADE, game_model_path=gm_path)  # fmt: skip
    assert r.combo is None and r.info["reason"] == "no_games_built"
    assert r.info["skipped"] == {"no_win_row_or_teams_or_props": 1}


def test_size_is_small_on_a_four_player_game(gm_path: Path) -> None:
    r = _build_stage(gm_path)
    assert r.combo is not None and r.scripts is not None
    assert len(cs.dumps(r.combo)) < 30_000 and len(cs.dumps(r.scripts)) < 30_000


# ------------------------------------------------------------------ the guard (nba/export/build.py)


def test_export_without_scripts_is_unchanged_and_says_why(src: Path, tmp_path: Path) -> None:  # noqa: F811
    m = _build(src, tmp_path / "out")
    assert m["game_scripts"] == {"status": "absent", "reason": "disabled"}
    assert not list((tmp_path / "out").glob("combo_*.json"))


def test_a_crashing_scripts_step_never_fails_the_export(
    src: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise MemoryError("scripts step died")

    monkeypatch.setattr(cs, "build", boom)
    m = _build(src, tmp_path / "out", scripts=True)
    assert (
        m["game_scripts"]["status"] == "absent"
        and m["game_scripts"]["reason"] == "error:MemoryError"
    )
    assert m["row_counts"]["prop_grids"] > 0 and (tmp_path / "out" / "export.duckdb").exists()


def test_a_broken_import_in_the_scripts_step_never_fails_the_export(
    src: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import nba.export as pkg

    monkeypatch.delattr(pkg, "combo_stage", raising=False)
    monkeypatch.setitem(sys.modules, "nba.export.combo_stage", None)  # forces ImportError
    m = _build(src, tmp_path / "out", scripts=True)
    assert m["game_scripts"]["status"] == "absent"
    assert m["game_scripts"]["reason"] in ("error:ImportError", "error:ModuleNotFoundError")
    assert m["row_counts"]["prop_grids"] > 0


def test_a_missing_game_model_fails_soft_and_a_good_run_writes_atomically(
    src: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gm_path: Path,
) -> None:
    monkeypatch.setattr(cs, "GAME_MODEL", tmp_path / "absent.json")
    m = _build(src, tmp_path / "out", scripts=True)
    assert m["game_scripts"]["status"] == "absent"  # the fixture day has no game model: fail soft
    assert m["row_counts"]["prop_grids"] > 0
    monkeypatch.setattr(cs, "GAME_MODEL", gm_path)
    m2 = _build(src, tmp_path / "out2", scripts=True)
    gs = m2["game_scripts"]
    assert gs["status"] in (
        "ok",
        "absent",
    )  # fixture rows may lack the production grid: either is fine
    leftovers = [p.name for p in (tmp_path / "out2").iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_the_manifest_files_block_and_hashes_are_unchanged_by_the_step(
    src: Path, tmp_path: Path
) -> None:  # noqa: F811
    a = _build(src, tmp_path / "a")
    b = _build(src, tmp_path / "b", scripts=True)
    assert a["files"].keys() == b["files"].keys() == {"export.duckdb", "parlay_of_day.json"}
    assert a["row_counts"] == b["row_counts"]


def test_now_is_naive_utc_helper_is_consistent() -> None:
    assert cs._iso_z(datetime(2026, 11, 10, 1, 2, 3, tzinfo=UTC)) == "2026-11-10T01:02:03Z"


# ------------------------------------------------------------------ adversary fixes (M2, m1, m2, m3, m7)


def test_read_markets_removes_its_private_copy_of_a_locked_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    import duckdb

    import nba.markets.asof as asof

    db = tmp_path / "kalshi.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR, "
        "title VARCHAR, player_id BIGINT, stat VARCHAR, threshold DOUBLE, result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid DOUBLE, yes_ask DOUBLE)"
    )
    con.execute("INSERT INTO kalshi_markets VALUES ('T1','S','E','t',1,'pts',20,NULL)")
    con.execute(
        "INSERT INTO kalshi_prices VALUES ('T1', TIMESTAMP '2026-11-10 12:00:00', 0.4, 0.5)"
    )
    con.close()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    real = duckdb.connect

    def locked(path: str, *a: Any, **k: Any) -> Any:
        if Path(path) == db:
            raise duckdb.IOException("Could not set lock on file")
        return real(path, *a, **k)

    monkeypatch.setattr(asof.duckdb, "connect", locked)
    monkeypatch.setattr("time.sleep", lambda s: None)
    rows = cs.read_markets(db)
    assert [r["ticker"] for r in rows] == ["T1"] and rows[0]["yes_ask"] == 0.5
    assert list(scratch.iterdir()) == []  # the copy was used, then deleted


def test_read_markets_on_an_unlocked_store_deletes_nothing(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "market_ro_keep" / "kalshi.duckdb"
    db.parent.mkdir()
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR, "
        "title VARCHAR, player_id BIGINT, stat VARCHAR, threshold DOUBLE, result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid DOUBLE, yes_ask DOUBLE)"
    )
    con.close()
    assert cs.read_markets(db) == []
    assert db.exists()  # the original is never removed, whatever its folder is called


def test_budget_is_enforced_and_reported(gm_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = _build_stage(gm_path)
    assert r.combo is not None and r.scripts is not None
    assert (
        r.info["budget"]["over_budget"] is False and r.info["budget"]["pairs_dropped_for_size"] == 0
    )
    assert r.info["mu_t_source"] == {"live-season games only": 1}
    monkeypatch.setattr(cs, "COMBO_BUDGET", 5_000)
    monkeypatch.setattr(cs, "PICKS_BUDGET", 1)
    r2 = _build_stage(gm_path)
    b = r2.info["budget"]
    assert b["dropped_raw_at_main"] is True and b["trimmed_surv_below"] == 0.001
    assert b["over_budget"] is True  # said, not hidden
    assert all(
        "raw_at_main" not in e
        for g in r2.combo["games"]
        for p in g["players"]
        for e in p["stats"].values()
    )
    assert all(blk["pairs"] == [] for g in r2.scripts["games"] for blk in g["picks"].values())


def test_pairs_are_dropped_lowest_first() -> None:
    def pair(p: float) -> dict[str, Any]:
        return {"p_joint_script": p, "pad": "x" * 100}

    scripts: dict[str, Any] = {
        "games": [
            {
                "picks": {
                    "a": {"pairs": [pair(0.5), pair(0.1)]},
                    "b": {"pairs": [pair(0.4), pair(0.2)]},
                }
            }
        ]
    }
    full = len(cs.dumps(scripts)) + 1
    cs.PICKS_BUDGET, saved = full - 150, cs.PICKS_BUDGET
    try:
        out = cs.enforce_budget({"games": []}, scripts)
    finally:
        cs.PICKS_BUDGET = saved
    left = sorted(
        p["p_joint_script"] for blk in scripts["games"][0]["picks"].values() for p in blk["pairs"]
    )
    assert out["pairs_dropped_for_size"] == 2 and left == [0.4, 0.5]


def test_second_file_failure_leaves_neither_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from types import SimpleNamespace

    import nba.export.build as bm

    res = cs.StageResult({"games": []}, {"games": []}, {"status": "ok"})
    monkeypatch.setattr(cs, "build", lambda *a, **k: res)
    fwd = SimpleNamespace(games=[], prop_grids=[], win_probs=[], p_play=[])
    real = os.replace

    def flaky(a: Any, b: Any) -> None:
        if str(b).endswith("game_scripts_2026-11-10.json"):
            raise OSError("disk")
        real(a, b)

    out = tmp_path / "o"
    out.mkdir()
    monkeypatch.setattr(bm.os, "replace", flaky)
    m = bm._scripts_stage(out, DAY, fwd, [], [], [], [], MADE, None, None, None)  # type: ignore[arg-type]
    assert m["status"] == "absent" and m["reason"] == "error:OSError"
    assert list(out.iterdir()) == []  # the combo file swapped in first was removed again
    # the combo file was swapped in before the failure only if its own replace ran first: both temps exist
    # before either swap, so a failure while WRITING leaves nothing; check that case too
    monkeypatch.setattr(bm.os, "replace", real)
    out2 = tmp_path / "o2"
    out2.mkdir()
    calls = {"n": 0}
    orig = Path.write_text

    def bad_write(self: Path, *a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", bad_write)
    m2 = bm._scripts_stage(out2, DAY, fwd, [], [], [], [], MADE, None, None, None)  # type: ignore[arg-type]
    assert m2["reason"] == "error:OSError" and list(out2.iterdir()) == []  # neither file, no temp


def test_note_says_the_track_record_is_large_but_shows_no_skill() -> None:
    assert "shows no skill yet" in cs.MARKET_NOTE and "defers to the market" in cs.MARKET_NOTE


# ------------------------------------------------------------------ one producer (2026-11-17 reconciliation)


def test_history_rows_sql_is_the_text_the_forward_rule_runs() -> None:
    """GAME_SCRIPTS_FORWARD rule line 2 recomputes mu_t with this exact SQL; it must not drift."""
    seen: dict[str, Any] = {}

    class Src:
        def execute(self, sql: str, args: list[Any]) -> Any:
            seen["sql"], seen["args"] = sql, args
            return self

        def fetchall(self) -> list[Any]:
            return []

    assert cs.history_rows(Src(), DAY, 2026) == []
    assert seen["sql"] == (
        "SELECT game_id, game_date, home_team, away_team, home_pts, away_pts FROM games "
        "WHERE season = ? AND game_date < ? AND home_pts IS NOT NULL AND home_pts > 0 "
        "AND game_id NOT LIKE '001%' AND game_id NOT LIKE '003%' ORDER BY game_date, game_id"
    )
    assert seen["args"] == [2026, DAY]


def _box(
    pid: int, gid: str, day: date, pts: int, *, season: int = 2026, minutes: float | None = 30.0
) -> dict[str, Any]:
    return {"game_id": gid, "player_id": pid, "team_id": PLAYERS[pid][0], "minutes": minutes,
            "pts": pts, "reb": 5, "ast": 3, "fg3m": 1, "game_date": day, "season": season}  # fmt: skip


def test_hist_counts_ride_in_the_payload_where_a_main_line_exists(gm_path: Path) -> None:
    box = [
        _box(101, "0022600001", DAY - timedelta(days=2), 30),  # H1 in the history rows: 118-110
        _box(101, "0022600002", DAY - timedelta(days=1), 20),  # H2: 105-99
        _box(101, "0022600003", DAY - timedelta(days=1), 0, minutes=None),  # a DNP is not a game
        _box(101, "0022500009", DAY - timedelta(days=3), 50, season=2025),  # holdout season: never
        _box(101, "0022500010", DAY - timedelta(days=3), 50),  # labelled 2026 with a 2025 id: never
        _box(101, "0022600004", DAY, 50),  # the slate date itself: not before it
    ]
    i = _inputs()
    i["hist"] = [("0022600001", DAY - timedelta(days=2), HOME, AWAY, 118, 110),
                 ("0022600002", DAY - timedelta(days=1), AWAY, HOME, 105, 99)]  # fmt: skip
    r = cs.build(
        DAY, i["games"], i["win_probs"], i["props"], i["pplay"], [], i["hist"], i["names"], i["abbr"],
        made_at=MADE + timedelta(minutes=5), game_model_path=gm_path, box_rows=box,
    )  # fmt: skip
    assert r.combo is not None and r.info["validation"]["errors"] == 0
    g = r.combo["games"][0]
    for p in g["players"]:
        for e in p["stats"].values():
            assert ("hist" in e) == (e["main_line"] is not None)
    h = next(p for p in g["players"] if p["player_id"] == 101)["stats"]["pts"]["hist"]
    assert h["n_all"] == 2 and h["k_all"] == 1  # 30 > 23.5, 20 < 23.5
    assert h["close"] == [0, 0] and h["blowout"] == [0, 0]  # margins 8 and 6
    assert r.combo["build_log"]["hist"]["past_games"]["n_games"] == 2
    assert r.combo["build_log"]["history_input"] == cs.HISTORY_GAMES


def test_without_box_rows_hist_is_left_out_not_zero(gm_path: Path) -> None:
    r = _build_stage(gm_path)
    assert r.combo is not None
    assert all("hist" not in e for p in r.combo["games"][0]["players"] for e in p["stats"].values())
    assert r.combo["build_log"]["hist"] == {"status": "no_box_rows"}


def test_block_made_at_is_the_newest_input_used(gm_path: Path) -> None:
    i = _inputs()
    late = MADE + timedelta(minutes=30)
    i["win_probs"][0]["made_at"] = late  # a newer win row (still before tip) feeds mu_m
    r = cs.build(DAY, i["games"], i["win_probs"], i["props"], i["pplay"], [], i["hist"], i["names"],
                 i["abbr"], made_at=late, game_model_path=gm_path)  # fmt: skip
    assert r.combo is not None and r.combo["games"][0]["made_at"] == cs._iso_z(late)
    i["win_probs"][0]["made_at"] = TIP  # a row stamped at tip is never used
    r2 = cs.build(DAY, i["games"], i["win_probs"], i["props"], i["pplay"], [], i["hist"], i["names"],
                  i["abbr"], made_at=late, game_model_path=gm_path)  # fmt: skip
    assert r2.combo is None and r2.info["skipped"] == {"no_win_row_or_teams_or_props": 1}


def test_a_line_stamped_after_made_at_is_dropped_and_counted(gm_path: Path) -> None:
    i = _inputs()
    for p in i["props"]:
        if p["player_id"] == 101 and p["stat"] == "pts":
            p["line_cal"] = {**p["line_cal"], "price_at": cs._iso_z(TIP - timedelta(minutes=1))}
    r = cs.build(DAY, i["games"], i["win_probs"], i["props"], i["pplay"], [], i["hist"], i["names"],
                 i["abbr"], made_at=MADE, game_model_path=gm_path)  # fmt: skip
    assert r.combo is not None
    e = next(p for p in r.combo["games"][0]["players"] if p["player_id"] == 101)["stats"]["pts"]
    assert e["main_line"] is None and "raw_at_main" not in e
    assert r.combo["build_log"]["games"][0]["reasons"]["main_line_null:line_after_made_at"] == 1
    assert ("G1", 101, "pts") not in r.expected


def test_a_payload_that_fails_validation_is_not_returned(
    gm_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "_p_over_cal", lambda row: 0.999)  # the export's value disagrees
    r = _build_stage(gm_path)
    assert r.combo is None and r.scripts is None
    assert r.info["reason"] == "validation_failed" and r.info["validation"]["errors"] > 0


def test_each_row_is_calibrated_on_its_own_grid(gm_path: Path) -> None:
    """Grouping by grid length: a row's calibrated values do not depend on which other rows share
    the night (the old stat-wide zero padding could move ``cal_k`` when a padded rung fell in band)."""
    from collections import defaultdict

    import nba.export.rung_cal_fields as rcf

    def reasons() -> dict[str, dict[str, int]]:
        return defaultdict(lambda: defaultdict(int))

    prod = rcf._production_rows(_inputs()["props"])
    together = cs.calibrated(prod, CAL, reasons())
    for key, row in prod.items():
        alone = cs.calibrated({key: row}, CAL, reasons())
        a, b = alone[key], together[key]
        assert np.array_equal(a[0], b[0]) and a[2] == b[2]


def test_a_10000_sample_row_is_used_with_its_own_n(gm_path: Path) -> None:
    i = _inputs()
    row = next(p for p in i["props"] if p["player_id"] == 201 and p["stat"] == "pts")
    row["p_ge_full_counts"] = [round(c * 10000 / 199) for c in row["p_ge_full_counts"]]
    row["p_ge_full_n"] = 10000
    po = ts.p_over({"p_ge_full": {"c": row["p_ge_full_counts"], "n": 10000}}, 20.5)
    qs = ts.quartiles({"pred": json.loads(row["prediction_json"])})
    assert po is not None and qs is not None
    row["line_cal"]["p_over_cal"] = CAL.apply(
        "draftkings", "pts", po, 20.5, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, 0.95
    )
    r = cs.build(DAY, i["games"], i["win_probs"], i["props"], i["pplay"], [], i["hist"], i["names"],
                 i["abbr"], made_at=MADE, game_model_path=gm_path)  # fmt: skip
    assert r.combo is not None and r.info["validation"]["errors"] == 0
    assert r.combo["build_log"]["games"][0]["reasons"]["grid_n_10000_used"] == 1
    e = next(p for p in r.combo["games"][0]["players"] if p["player_id"] == 201)["stats"]["pts"]
    assert e["main_line"] == 20.5
