# ruff: noqa: E501
"""The offline combo rebuild (``combo_night``: an export copy through ``combo_stage.build``, the one
producer) and the payload validator, on a small synthetic export (no live data, no network)."""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.export import combo_stage as cs
from nba.parlay import combo
from nba.parlay import combo_night as cn
from nba.parlay import line_cal as lc

NIGHT = "2026-11-17"
TIP = datetime(2026, 11, 18, 1, 0)
T_RUN1 = datetime(2026, 11, 17, 14, 0)
T_RUN2 = datetime(2026, 11, 17, 18, 50)
ASOF = datetime(2026, 11, 17, 20, 0)
GAME_PARAMS = {
    "fit_seasons": [2022, 2023, 2024],
    "halflife": 20.0,
    "league_total": 227.64692482915717,
    "margin_b": 0.5830295877961291,
    "margin_s": 6.9690807906428045,
    "margin_sd": 13.633615128693934,
    "n_margin": 3951,
    "n_total": 3951,
    "prior_k": 8.0,
    "total_a": -176.4130005633288,
    "total_c": 1.774093488559142,
    "total_sd": 18.81687010740591,
}
MU_T_FALLBACK = round(
    GAME_PARAMS["total_a"] + GAME_PARAMS["total_c"] * GAME_PARAMS["league_total"], 2
)
CAL = lc.load_map()
JM: Path = Path("unset")


@pytest.fixture(autouse=True)
def _joint_model(tmp_path_factory: pytest.TempPathFactory) -> None:
    global JM
    JM = tmp_path_factory.mktemp("jm") / "joint_model.json"
    JM.write_text(json.dumps({"params": GAME_PARAMS}))


def _sf_counts(mean: float, sd: float, k_len: int = 40, n: int = 199) -> list[int]:
    return [
        round(n * 0.5 * math.erfc(((k - 0.5 - mean) / sd) / math.sqrt(2)))
        for k in range(1, k_len + 1)
    ]


def _q_grid(mean: float, sd: float) -> list[float]:
    nd = __import__("statistics").NormalDist(mean, sd)
    return [nd.inv_cdf((i + 1) / 20) for i in range(19)]


def _line_cal(
    counts: list[int], q: list[float], line: float, at: datetime, stat: str = "pts", n: int = 199
) -> str:
    po = counts[math.ceil(line) - 1] / n
    pc = CAL.apply("draftkings", stat, po, line, q[9], (q[14] - q[4]) / lc.IQR_TO_SD, 0.9)
    return json.dumps(
        {
            "book": "draftkings",
            "main_line": line,
            "price_at": cn.iso_z(at),
            "p_over_raw": po,
            "p_over_cal": pc,
        }
    )


def make_export(*, tipped: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE meta(key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('made_at', ?)", [cn.iso_z(ASOF)])
    con.execute(
        "CREATE TABLE box_scores(game_id VARCHAR, player_id INTEGER, team_id INTEGER, minutes DOUBLE, pts INTEGER, reb INTEGER, ast INTEGER, fg3m INTEGER, game_date DATE, season INTEGER)"
    )
    con.execute("CREATE TABLE players(player_id INTEGER, full_name VARCHAR)")
    con.execute("INSERT INTO players VALUES (1,'Ann Guard'),(2,'Bob Wing')")  # player 3 has no name
    con.execute(
        "CREATE TABLE games(game_id VARCHAR, season INTEGER, game_date DATE, tipoff TIMESTAMP, home_team INTEGER, away_team INTEGER)"
    )
    tip = ASOF - timedelta(minutes=1) if tipped else TIP
    con.execute("INSERT INTO games VALUES ('G1', 2026, ?, ?, 10, 20)", [NIGHT, tip])
    con.execute(
        "CREATE TABLE win_probs(game_id VARCHAR, made_at TIMESTAMP, run_id VARCHAR, p_home DOUBLE, model_name VARCHAR, is_primary BOOLEAN, comparison_only BOOLEAN)"
    )
    con.execute(
        "INSERT INTO win_probs VALUES ('G1', ?, 'r1', 0.6, 'rung0_injury_elo', TRUE, FALSE)",
        [T_RUN1],
    )
    con.execute(
        """CREATE TABLE prop_grids(game_id VARCHAR, player_id INTEGER, stat VARCHAR, team_id INTEGER, made_at TIMESTAMP,
           tipoff TIMESTAMP, run_id VARCHAR, q_grid DOUBLE[], p_ge_full_counts BIGINT[], p_ge_full_n BIGINT, p_play DOUBLE,
           p_play_primary DOUBLE, prediction_json VARCHAR, line_cal VARCHAR, line_cal_null_reason VARCHAR,
           model_name VARCHAR, comparison_only BOOLEAN)"""
    )
    return con


def add_row(
    con: duckdb.DuckDBPyConnection,
    pid: int,
    made: datetime,
    *,
    mean: float = 20.0,
    n: int = 199,
    starter: float | None = 0.9,
    line: float | None = 20.5,
    line_at: datetime = T_RUN1 - timedelta(minutes=5),
    p_play: float | None = 0.97,
    model: str = "props_context_residual",
    stat: str = "pts",
    tip: datetime = TIP,
) -> None:
    counts = _sf_counts(mean, 6.0, n=n)
    q = _q_grid(mean, 6.0)
    lcj = _line_cal(counts, q, line, line_at, stat, n) if line is not None else None
    pred = json.dumps({"q_grid": q} | ({} if starter is None else {"starter_rate": starter}))
    con.execute(
        "INSERT INTO prop_grids VALUES ('G1', ?, ?, 10, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)",
        [
            pid,
            stat,
            made,
            tip,
            "run-" + made.isoformat(),
            q,
            counts,
            n,
            p_play,
            p_play,
            pred,
            lcj,
            None if lcj else "no_book_line",
            model,
        ],
    )


def run(con: duckdb.DuckDBPyConnection, **kw: object) -> cs.StageResult:
    return cn.build_night(con, NIGHT, game_model_path=JM, cal=CAL, **kw)  # type: ignore[arg-type]


def build(con: duckdb.DuckDBPyConnection, **kw: object) -> dict:
    res = run(con, **kw)
    assert res.combo is not None, res.info
    return res.combo


def base() -> duckdb.DuckDBPyConnection:
    con = make_export()
    add_row(con, 1, T_RUN1)
    add_row(con, 2, T_RUN1, mean=14.0, line=14.5)
    return con


def test_builds_and_validates_and_main_line_matches_export() -> None:
    con = base()
    pl = build(con)
    g = pl["games"][0]
    assert pl["schema"] == "combo_export_v1" and g["made_at"] == "2026-11-17T14:00:00Z"
    assert g["game"]["mu_t"] == MU_T_FALLBACK and g["game"]["sd_t"] == 18.82
    assert g["mu_t_source"] == "league fallback"
    assert g["game"]["mu_m"] == round(
        GAME_PARAMS["margin_b"] + GAME_PARAMS["margin_s"] * math.log(0.6 / 0.4), 2
    )
    ann = g["players"][0]
    assert ann["name"] == "Ann Guard" and ann["starter"] == 1 and ann["p_play"] == 0.97
    stage = run(con)
    assert stage.info["validation"]["errors"] == 0  # the stage validated it before returning it
    res = cn.validate_payload(pl, expected=stage.expected, asof=ASOF)
    assert res["errors"] == [] and res["n_checked"]["main_line_checked"] == 2
    assert res["max_abs_diff"] is not None and res["max_abs_diff"] <= cn.TOL


def test_payload_is_usable_by_the_reference_evaluator() -> None:
    pl = build(base())
    games = {g["game_id"]: g for g in pl["games"]}
    legs = [
        {
            "kind": "player",
            "game_id": "G1",
            "player_id": 1,
            "stat": "pts",
            "line": 20.5,
            "side": "over",
        }
    ]
    r = combo.evaluate_combo(legs, games, pl["shared"])
    assert 0.3 < r.p_joint < 0.7


def test_refuses_holdout_season_date() -> None:
    with pytest.raises(cn.NightRefusal):
        cn.build_night(base(), "2026-03-01", game_model_path=JM)


def test_a_tipped_game_is_built_from_pre_tip_rows_and_does_not_change() -> None:
    """The nightly export runs after the early tips; the in-game snapshot needs those games' pre-game
    block. It is built from pre-tip rows only, so building it after tip gives the same block."""
    con = make_export(tipped=True)
    tip = ASOF - timedelta(minutes=1)
    add_row(con, 1, T_RUN1, tip=tip)
    before = build(con, asof=tip - timedelta(minutes=5))["games"]
    res = run(con, asof=ASOF)
    assert res.combo is not None and len(res.combo["games"]) == 1
    assert before == res.combo["games"]
    assert cn.validate_payload(res.combo, expected=res.expected)["errors"] == []
    errs = cn.validate_payload(res.combo, expected=res.expected, asof=ASOF)["errors"]
    assert errs == ["G1: built for a game that tipped at or before asof"]  # refusable on request


def test_rows_after_asof_or_after_tip_are_ignored() -> None:
    con = base()
    add_row(con, 1, ASOF + timedelta(minutes=30), mean=30.0)  # stamped after the export cut
    pl = build(con)
    assert pl["games"][0]["made_at"] == "2026-11-17T14:00:00Z"
    con2 = make_export()
    add_row(con2, 1, TIP + timedelta(minutes=1))  # after tip
    res = run(con2, asof=TIP + timedelta(hours=1))
    assert res.combo is None and res.info["reason"] == "no_games_built"


def test_older_run_row_used_as_stamped_and_block_made_at_is_newest() -> None:
    con = base()
    add_row(con, 1, T_RUN2, mean=22.0, line=22.5, line_at=T_RUN2 - timedelta(minutes=1))
    g = build(con)["games"][0]
    assert g["made_at"] == "2026-11-17T18:50:00Z"
    assert {p["player_id"] for p in g["players"]} == {1, 2}  # Bob kept from the 14:00 run


def test_main_line_stamped_after_made_at_is_dropped_with_reason() -> None:
    con = make_export()
    add_row(con, 1, T_RUN1, line_at=T_RUN1 + timedelta(minutes=1))
    pl = build(con)
    assert pl["games"][0]["players"][0]["stats"]["pts"]["main_line"] is None
    assert "main_line_null:line_after_made_at" in pl["build_log"]["games"][0]["reasons"]


def test_no_book_line_gives_null_main_line_and_no_raw_at_main() -> None:
    con = make_export()
    add_row(con, 1, T_RUN1, line=None)
    e = build(con)["games"][0]["players"][0]["stats"]["pts"]
    assert e["main_line"] is None and "raw_at_main" not in e and e["cal_k"][0] >= 1


def test_missing_inputs_are_counted_never_guessed() -> None:
    con = base()
    add_row(con, 3, T_RUN1)  # no name in players: kept under a placeholder, counted
    add_row(con, 1, T_RUN1, stat="reb", mean=8.0, line=7.5, n=10000)  # a 10,000-sample grid: used
    add_row(con, 2, T_RUN1, stat="ast", starter=None)  # no starter rate: left out, counted
    add_row(con, 2, T_RUN1, stat="fg3m", model="props_avg_live2")  # not the production model
    res = run(con)
    assert res.combo is not None and res.info["validation"]["errors"] == 0
    reasons = res.combo["build_log"]["games"][0]["reasons"]
    assert reasons["no_player_name"] == 1 and reasons["no_starter_rate"] == 1
    assert reasons["grid_n_10000_used"] == 1
    stats = {p["player_id"]: set(p["stats"]) for p in res.combo["games"][0]["players"]}
    assert stats == {1: {"pts", "reb"}, 2: {"pts"}, 3: {"pts"}}
    assert {p["player_id"]: p["name"] for p in res.combo["games"][0]["players"]}[3] == "Player 3"


def test_null_p_play_stays_null_and_missing_win_prob_leaves_game_out() -> None:
    con = make_export()
    add_row(con, 1, T_RUN1, p_play=None)
    assert build(con)["games"][0]["players"][0]["p_play"] is None
    con.execute("DELETE FROM win_probs")
    res = run(con)
    assert res.combo is None and res.info["skipped"] == {"no_win_row_or_teams_or_props": 1}


# ---------------------------------------------------------------- validator


def good() -> tuple[dict, dict]:
    res = run(base())
    assert res.combo is not None
    return res.combo, res.expected


def _errs(pl: dict, exp: dict | None = None) -> list[str]:
    return cn.validate_payload(pl, expected=exp, asof=ASOF)["errors"]


def test_validator_flags_each_contract_violation() -> None:
    pl, exp = good()
    assert _errs(pl, exp) == []
    e = copy.deepcopy(pl)
    e["schema"] = "combo_export_v0"
    assert "schema" in _errs(e, exp)[0]
    e = copy.deepcopy(pl)
    s = e["games"][0]["players"][0]["stats"]["pts"]
    s["surv"][2] = min(1.0, s["surv"][1] + 0.1)
    assert any("nonincreasing" in x for x in _errs(e, exp))
    e = copy.deepcopy(pl)
    e["games"][0]["players"][0]["stats"]["pts"]["surv"].append(0.0)
    assert any("not trimmed" in x for x in _errs(e, exp))
    e = copy.deepcopy(pl)
    e["games"][0]["players"][0]["stats"]["pts"]["cal_k"] = [5, 2]
    assert any("cal_k" in x for x in _errs(e, exp))
    e = copy.deepcopy(pl)
    e["games"][0]["players"][0]["stats"]["pts"]["price_american"] = -110
    assert any("price-like" in x for x in _errs(e, exp))
    e = copy.deepcopy(pl)
    e["games"][0]["made_at"] = e["games"][0]["tip_utc"]
    assert any("not before tip" in x for x in _errs(e, exp))
    e = copy.deepcopy(pl)
    e["games"][0]["tip_utc"] = "2026-11-17T19:00:00Z"
    e["games"][0]["made_at"] = "2026-11-17T18:00:00Z"
    assert any("tipped at or before asof" in x for x in _errs(e, exp))


def test_validator_flags_main_line_value_mismatch_and_missing_expected() -> None:
    pl, exp = good()
    key = next(iter(exp))
    bad = {**exp, key: (exp[key] or 0.0) + 0.05}
    assert any("export" in x and "diff" in x for x in _errs(pl, bad))
    gone = {**exp, key: None}
    assert any("no line_cal value" in x for x in _errs(pl, gone))


def test_validator_size_budget() -> None:
    pl, exp = good()
    big = copy.deepcopy(pl)
    big["pad"] = "x" * (cn.MAX_RAW_BYTES + 1)
    assert any("raw size" in x for x in _errs(big, exp))
    raw, gz = cn.size_bytes(pl)
    assert raw < cn.MAX_RAW_BYTES and gz < cn.MAX_GZ_BYTES


def test_cli_writes_file_and_exit_zero(tmp_path: Path) -> None:
    db = tmp_path / "export.duckdb"
    src = base()
    src.execute(f"ATTACH '{db}' AS out")
    for t in ("meta", "players", "games", "win_probs", "prop_grids", "box_scores"):
        src.execute(f"CREATE TABLE out.{t} AS SELECT * FROM {t}")
    src.execute("DETACH out")
    src.close()
    jm = JM
    rc = cn.main(
        ["--export", str(db), "--date", NIGHT, "--out", str(tmp_path), "--joint-model", str(jm)]
    )
    assert rc == 0
    pl = json.loads((tmp_path / f"combo_{NIGHT}.json").read_text())
    assert pl["schema"] == "combo_export_v1" and len(pl["games"]) == 1
    with pytest.raises(SystemExit):
        cn.main(
            [
                "--export",
                str(db),
                "--date",
                "2026-03-01",
                "--out",
                str(tmp_path),
                "--joint-model",
                str(jm),
            ]
        )


def test_module_has_no_execution_or_price_reads() -> None:
    src = Path(cn.__file__).read_text().lower()
    for word in (
        "place_order",
        "portfolio",
        "requests.",
        "urllib",
        "api_key",
        "price_american",
        "implied_prob",
    ):
        assert word not in src.replace("price_tokens", ""), word
    assert "prop_lines" not in src


# ---------------------------------------------------------------- season-2026 script history


def add_box(con, gid, day, team, pid, pts, total, *, minutes=30.0, season=2026, reb=0):  # type: ignore[no-untyped-def]
    """A player row with ``pts`` plus a filler row so the team scores ``total``."""
    con.execute(
        "INSERT INTO box_scores VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
        [gid, pid, team, minutes, pts, reb, day, season],
    )
    if total - pts:
        con.execute(
            "INSERT INTO box_scores VALUES (?, ?, ?, 20.0, ?, 0, 0, 0, ?, ?)",
            [gid, 900 + team, team, total - pts, day, season],
        )


def with_history(extra: bool = False) -> duckdb.DuckDBPyConnection:
    """Known answers. G1 (11-10, first day: league-fallback mu_t 227.46): team 10 150 - team 20 100,
    total 250 = blowout and shootout (>= 240). G2 (11-10): 10 vs 40, 103-102."""
    con = base()
    # G1: 10 vs 20, 150-100: margin 50 (blowout), total 250 >= ceil(227.46 + 12) = 240 (shootout)
    add_box(con, "0022600001", "2026-11-10", 10, 1, 30, 150)
    add_box(con, "0022600001", "2026-11-10", 20, 2, 14, 100)
    # G2 (same day, still no history): 10 vs 40, 103-102: close; total 205 <= floor(227.46 - 12) = 215 (grind)
    add_box(con, "0022600002", "2026-11-10", 10, 1, 10, 103)
    add_box(con, "0022600002", "2026-11-10", 40, 5, 3, 102)
    # G3 (11-11): 10 vs 20, 120-110: margin 10 (neither blowout nor close), total 230 (neither total script)
    add_box(con, "0022600003", "2026-11-11", 10, 1, 18, 120)
    add_box(con, "0022600003", "2026-11-11", 20, 2, 16, 110)
    # DNPs are not games: null minutes, zero minutes
    add_box(con, "0022600004", "2026-11-12", 10, 1, 0, 100, minutes=None)  # type: ignore[arg-type]
    add_box(con, "0022600004", "2026-11-12", 40, 2, 0, 90, minutes=0.0)
    # on the slate date itself: not before it
    add_box(con, "0022600005", NIGHT, 10, 1, 50, 100)
    add_box(con, "0022600005", NIGHT, 20, 2, 50, 90)
    if extra:  # season 2025 rows (display-only game logs) and mislabelled ids must change nothing
        add_box(con, "0022500500", "2026-11-09", 10, 1, 40, 200, season=2025)
        add_box(con, "0022500501", "2026-11-09", 20, 2, 40, 100, season=2025)
        add_box(con, "0022500502", "2026-11-09", 10, 1, 40, 200, season=2026)  # a 2025-style id
        add_box(con, "0012600001", "2026-11-09", 10, 1, 40, 200)  # preseason id
    return con


def test_hist_counts_known_answers() -> None:
    pl = build(with_history())
    p1, p2 = pl["games"][0]["players"]
    h1, h2 = p1["stats"]["pts"]["hist"], p2["stats"]["pts"]["hist"]
    # player 1 played G1 (30), G2 (10), G3 (18); G4 is a DNP; G5 is the slate date; main line 20.5
    assert h1 == {
        "n_all": 3,
        "k_all": 1,
        "blowout": [1, 1],
        "close": [1, 0],
        "shootout": [1, 1],
        "grind": [1, 0],
    }
    # player 2 (main line 14.5) played G1 (14), G3 (16); G4 minutes 0 is a DNP
    assert h2 == {
        "n_all": 2,
        "k_all": 1,
        "blowout": [1, 0],
        "close": [0, 0],
        "shootout": [1, 0],
        "grind": [0, 0],
    }
    lg = pl["build_log"]["hist"]
    assert lg["season"] == 2026 and lg["past_games"]["n_games"] == 4
    assert lg["past_games"]["blowout"] == 1 and lg["past_games"]["close"] == 1  # G1, G2
    assert lg["past_games"]["shootout"] == 1 and lg["past_games"]["grind"] == 2  # G2 and G4 (190)


def test_hist_ignores_season_2025_and_foreign_ids() -> None:
    a = build(with_history())
    b = build(with_history(extra=True))
    assert a["games"] == b["games"] and a["build_log"]["hist"] == b["build_log"]["hist"]


def test_hist_only_where_main_line_and_mu_t_uses_live_history() -> None:
    pl = build(with_history())
    g = pl["games"][0]
    assert all(
        ("hist" in e) == (e["main_line"] is not None)
        for p in g["players"]
        for e in p["stats"].values()
    )
    assert g["mu_t_source"] == "live-season games only" and g["game"]["mu_t"] != MU_T_FALLBACK


def test_absent_history_gives_zero_counts_and_league_fallback_mu_t() -> None:
    pl = build(base())
    e = pl["games"][0]["players"][0]["stats"]["pts"]
    assert e["hist"] == {
        "n_all": 0,
        "k_all": 0,
        "blowout": [0, 0],
        "close": [0, 0],
        "shootout": [0, 0],
        "grind": [0, 0],
    }
    assert pl["build_log"]["hist"]["past_games"]["n_games"] == 0
    assert pl["games"][0]["game"]["mu_t"] == MU_T_FALLBACK


def test_hist_is_in_the_validated_payload_and_has_no_price_key() -> None:
    res = run(with_history())
    pl = res.combo
    assert pl is not None
    assert cn.validate_payload(pl, expected=res.expected, asof=ASOF)["errors"] == []
    bad = copy.deepcopy(pl)
    bad["games"][0]["players"][0]["stats"]["pts"]["hist"]["blowout"] = [2, 3]
    assert any("hist" in x for x in cn.validate_payload(bad, asof=ASOF)["errors"])


# ---------------------------------------------------------------- one producer


def test_the_offline_rebuild_is_the_nightly_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*a: object, **k: object) -> str:
        seen.update(k)
        return "the stage"

    monkeypatch.setattr(cs, "build", fake)
    assert cn.build_night(base(), NIGHT, game_model_path=JM) == "the stage"
    assert seen["asof"] == ASOF and seen["history_input"] == cn.HISTORY_BOX


def test_history_db_gives_the_apps_games_table_number() -> None:
    """With a copy of the source database, mu_t comes from its games table through
    ``combo_stage.history_rows`` (the rule's number); where the box scores agree, so do the two."""
    con = with_history()
    h = duckdb.connect(":memory:")
    h.execute(
        "CREATE TABLE games(game_id VARCHAR, season INTEGER, game_date DATE, home_team INTEGER, away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    h.execute(
        "INSERT INTO games VALUES ('0022600001', 2026, '2026-11-10', 10, 20, 150, 100),"
        "('0022600002', 2026, '2026-11-10', 10, 40, 103, 102), ('0022600003', 2026, '2026-11-11', 10, 20, 120, 110),"
        "('0022600004', 2026, '2026-11-12', 10, 40, 100, 90), ('0022500001', 2025, '2026-11-12', 10, 20, 300, 10)"
    )
    via_games = run(con, history=h)
    via_box = run(con)
    assert via_games.combo is not None and via_box.combo is not None
    assert via_games.combo["build_log"]["history_input"] == cs.HISTORY_GAMES
    assert via_games.combo["games"][0]["game"] == via_box.combo["games"][0]["game"]
    assert via_games.combo["build_log"]["hist"] == via_box.combo["build_log"]["hist"]
