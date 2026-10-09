from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.parlay.__main__ import main
from nba.parlay.calibration import clustered_paired_bootstrap
from nba.parlay.config import FeeModel, load_config
from nba.parlay.copula import PairCorr
from nba.parlay.ev import NO_POSITIVE_EV, evaluate, fee_per_contract, order_fee
from nba.parlay.game_model import (
    GameModelParams,
    asof_total_features,
    fit_game_model,
)
from nba.parlay.joint import (
    GameCtx,
    GameResiduals,
    JointModel,
    corr_from_json,
    corr_to_json,
    effective_prob,
    estimate_game_corr,
    leg_marginal,
)
from nba.parlay.kalshi_map import MarketRow, map_market, parse_event
from nba.parlay.legs import Leg
from nba.parlay.papertrade import resolve_legs, settle_trades
from nba.parlay.qdist import QuantileGridDist
from nba.parlay.shadow import ensure_schema, log_shadow, settle_shadow, track_record

CFG = load_config()
GRID = [float(v) for v in np.linspace(5, 35, 19)]
HOME, AWAY = 1610612747, 1610612758  # LAL, SAC


# ---------------------------------------------------------------- fees
def test_fee_examples_from_published_schedule() -> None:
    assert CFG.fee.verified
    assert order_fee(0.40, CFG.fee, 100) == pytest.approx(1.68)
    assert order_fee(0.55, CFG.fee, 100) == pytest.approx(1.74)
    assert fee_per_contract(0.55, CFG.fee, 100) == pytest.approx(0.0174)


def test_fee_maker_multiplier_and_order_rounding() -> None:
    fee = FeeModel("t", "p_one_minus_p", 0.07, True, 0.0175, 1.0, (("KXA", 0.5), ("KXAB", 2.0)))
    assert fee.multiplier("KXABC") == 2.0 and fee.multiplier("KXAZ") == 0.5
    assert fee.multiplier("OTHER") == 1.0
    assert order_fee(0.5, fee, 100, series="KXAZ") == pytest.approx(0.88)  # 0.5*1.75=0.875 up
    # rounded up once per order: 1 contract at 0.5 -> 0.0175 -> 2 cents
    assert order_fee(0.5, fee, 1) == pytest.approx(0.02)
    with pytest.raises(ValueError):
        order_fee(0.5, fee, 1, order_type="bogus")


@settings(max_examples=60, deadline=None)
@given(st.floats(0.01, 0.99), st.integers(1, 5000))
def test_maker_is_quarter_of_taker(p: float, c: int) -> None:
    t = order_fee(p, CFG.fee, c, order_type="taker")
    m = order_fee(p, CFG.fee, c, order_type="maker")
    raw_t = 0.07 * c * p * (1 - p)
    assert t >= raw_t - 1e-9 and t - raw_t < 0.01 + 1e-9  # rounded up to the cent
    assert abs(m - t / 4) <= 0.0100001 + 1e-9  # same ratio up to cent rounding


# ---------------------------------------------------------------- qdist
@settings(max_examples=50, deadline=None)
@given(st.lists(st.floats(0, 60), min_size=19, max_size=19))
def test_qdist_monotone(q: list[float]) -> None:
    d = QuantileGridDist.from_grid(q)
    ps = [d.p_ge(float(t)) for t in range(0, 70)]
    assert all(0 <= p <= 1 for p in ps)
    assert all(a >= b - 1e-12 for a, b in zip(ps, ps[1:], strict=False))


def test_qdist_median() -> None:
    d = QuantileGridDist.from_grid(GRID)
    assert d.p_ge(20.0) == pytest.approx(0.5, abs=0.03)


# ---------------------------------------------------------------- game model
def _games(n: int = 400, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        d = date(2022, 10, 20).toordinal() + i // 5
        h, a = int(rng.integers(1, 9)), int(rng.integers(9, 17))
        mu = rng.normal(0, 5)
        hp = int(110 + mu / 2 + rng.normal(0, 8))
        ap = int(110 - mu / 2 + rng.normal(0, 8))
        rows.append((f"g{i}", date.fromordinal(d), 2022, h, a, hp, ap))
    return pl.DataFrame(
        rows,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )


def test_total_features_are_asof() -> None:
    g = _games()
    f1 = asof_total_features(g, 20, 8).sort("game_id")
    g2 = g.with_columns(
        pl.when(pl.col("game_id") == "g300")
        .then(300)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    f2 = asof_total_features(g2, 20, 8).sort("game_id")
    d1, d2 = g["game_date"], None
    cut = g.filter(pl.col("game_id") == "g300")["game_date"][0]
    early = g.filter(pl.col("game_date") <= cut)["game_id"]
    a = f1.filter(pl.col("game_id").is_in(early.to_list())).sort("game_id")
    b = f2.filter(pl.col("game_id").is_in(early.to_list())).sort("game_id")
    assert a.equals(b)  # changing g300's result cannot move any feature on/before its date
    _ = (d1, d2)


def test_fit_game_model_never_uses_2025() -> None:
    g = _games().with_columns(
        pl.when(pl.col("game_id").str.slice(1).cast(pl.Int64) % 2 == 0)
        .then(2022)
        .otherwise(2025)
        .alias("season")
    )
    p = pl.DataFrame({"game_id": g["game_id"], "p": 0.55})
    params = fit_game_model(g, p)
    assert (
        params.fit_seasons == (2022,)
        and params.n_margin == g.filter(pl.col("season") == 2022).height
    )
    assert 0 < params.margin_sd < 30 and params.total_sd > 0


# ---------------------------------------------------------------- joint
def _ctx(mu_m: float = 3.0, p_play: float = 0.9) -> GameCtx:
    d = QuantileGridDist.from_grid(GRID)
    return GameCtx(
        "g",
        HOME,
        AWAY,
        mu_m,
        13.0,
        228.0,
        19.0,
        {(1, "pts"): d, (2, "pts"): d, (3, "pts"): d, (1, "reb"): d},
        {1: HOME, 2: HOME, 3: AWAY},
        {1: p_play, 2: p_play, 3: p_play},
    )


def _pc(**kw: float) -> PairCorr:
    rho = {
        ("teammate", "pts", "pts"): kw.get("tm", 0.0),
        ("same_player", "pts", "reb"): kw.get("sp", 0.0),
        ("own_margin", "margin", "pts"): kw.get("om", 0.0),
        ("game", "pts", "total"): kw.get("tot", 0.0),
        ("game", "margin", "total"): 0.0,
    }
    return PairCorr(rho, {k: 1000 for k in rho})


def test_win_and_spread_are_coherent() -> None:
    ctx = _ctx()
    ctxs = {"g": ctx}
    win = Leg("g", 0, "win", 0.0, "yes", HOME)
    sp = Leg("g", 0, "spread", 7.5, "yes", HOME)
    away_win = Leg("g", 0, "win", 0.0, "yes", AWAY)
    m = JointModel("gaussian", _pc(), 20000, 1)
    j = m.joint_many([[win, sp], [win, away_win], [sp, away_win]], ctxs)
    assert j[0].p_cond == pytest.approx(leg_marginal(sp, ctx), abs=0.01)  # sp implies win
    assert j[1].p_cond < 1e-3 and j[2].p_cond < 1e-3  # mutually exclusive
    assert leg_marginal(win, ctx) + leg_marginal(away_win, ctx) == pytest.approx(1.0)


def test_independence_and_zero_corr_agree_and_positive_corr_helps() -> None:
    ctx = _ctx()
    legs = [Leg("g", 1, "pts", 20, "yes", HOME), Leg("g", 2, "pts", 20, "yes", HOME)]
    ind = JointModel("independence", _pc()).joint(legs, {"g": ctx})
    g0 = JointModel("gaussian", _pc(), 60000, 3).joint(legs, {"g": ctx})
    g1 = JointModel("gaussian", _pc(tm=0.5), 60000, 3).joint(legs, {"g": ctx})
    t1 = JointModel("t", _pc(tm=0.5), 60000, 3).joint(legs, {"g": ctx})
    assert ind.p_cond == pytest.approx(g0.p_cond, abs=0.01)
    assert g1.p_cond > ind.p_cond + 0.03 and t1.p_cond > ind.p_cond + 0.03


def test_player_margin_link() -> None:
    ctx = _ctx()
    leg_a = Leg("g", 1, "pts", 20, "yes", HOME)
    leg_w = Leg("g", 0, "win", 0.0, "yes", HOME)
    leg_aw = Leg("g", 0, "win", 0.0, "yes", AWAY)
    m = JointModel("gaussian", _pc(om=0.4), 60000, 5)
    pw = m.joint([leg_a, leg_w], {"g": ctx}).p_cond
    paw = m.joint([leg_a, leg_aw], {"g": ctx}).p_cond
    base = leg_marginal(leg_a, ctx)
    assert pw > base * leg_marginal(leg_w, ctx) and paw < base * leg_marginal(leg_aw, ctx)


def test_dnp_policies() -> None:
    assert effective_prob(0.4, 0.8, "loss", 0.3) == pytest.approx(0.32)
    assert effective_prob(0.4, 0.8, "void", 0.3) == pytest.approx(0.32 + 0.2 * 0.3)
    # void EV equals p_nov * (p_cond - cost)
    assert effective_prob(0.4, 0.8, "void", 0.3) - 0.3 == pytest.approx(0.8 * (0.4 - 0.3))
    with pytest.raises(ValueError):
        effective_prob(0.4, 0.8, "bogus", 0.3)


def test_estimate_game_corr_recovers_known_rho() -> None:
    rng = np.random.default_rng(0)
    out = []
    for _ in range(3000):
        zm, zt = rng.normal(), rng.normal()
        a = 0.6 * zm + 0.8 * rng.normal()  # home player pts: rho 0.6 with margin
        b = 0.5 * a + np.sqrt(0.75) * rng.normal()
        out.append(
            GameResiduals(
                1, zm, zt, np.array([1, 2]), np.array([1, 1]), np.array([0, 0]), np.array([a, b])
            )
        )
    pc = estimate_game_corr(out, shrink_k=10.0)
    assert pc.get("own_margin", "pts", "margin") == pytest.approx(
        0.45, abs=0.05
    )  # (0.6 + 0.3) / 2 over the two players
    assert pc.get("teammate", "pts", "pts") == pytest.approx(0.5, abs=0.05)
    assert abs(pc.get("game", "pts", "total")) < 0.06
    back = corr_from_json(json.loads(json.dumps(corr_to_json(pc))))
    assert back.rho == pc.rho


def test_clustered_bootstrap_detects_shift_and_clusters() -> None:
    rng = np.random.default_rng(1)
    g = np.repeat(np.arange(300).astype(str), 4)
    a = rng.normal(0.0, 1, 1200) - 0.3
    c = clustered_paired_bootstrap(a, np.zeros(1200), g, metric="m", names=("a", "b"), n_boot=500)
    assert c.a_better_significant and c.n == 1200 and c.ci_hi < 0


# ---------------------------------------------------------------- kalshi map
def test_parse_event_and_map() -> None:
    assert parse_event("KXNBAGAME-26OCT20OKCSAS") == (date(2026, 10, 20), "OKC", "SAS")
    assert parse_event("garbage") is None
    abbr = {"SAC": AWAY, "LAL": HOME}
    gk = {(date(2026, 10, 8), AWAY, HOME): "g"}

    def row(**kw: object) -> MarketRow:
        base: dict[str, object] = dict(
            ticker="KXNBASPREAD-26OCT08SACLAL-SAC8",
            series_ticker="KXNBASPREAD",
            event_ticker="KXNBASPREAD-26OCT08SACLAL",
            title="t",
            player_id=None,
            stat="spread",
            threshold=7.5,
            yes_bid=0.3,
            yes_ask=0.35,
            ts="x",
        )
        base.update(kw)
        return MarketRow.from_row(base)

    leg, why = map_market(row(), abbr, gk, {})
    assert why is None and leg == Leg("g", 0, "spread", 7.5, "yes", AWAY)
    leg, why = map_market(
        row(
            ticker="KXNBAGAME-26OCT08SACLAL-LAL",
            series_ticker="KXNBAGAME",
            event_ticker="KXNBAGAME-26OCT08SACLAL",
            stat="game_win",
        ),
        abbr,
        gk,
        {},
    )
    assert leg is not None and leg.stat == "win" and leg.team_id == HOME
    leg, why = map_market(
        row(
            series_ticker="KXNBAPTS",
            stat="pts",
            player_id=None,
            ticker="KXNBAPTS-26OCT08SACLAL-X-20",
            event_ticker="KXNBAPTS-26OCT08SACLAL",
        ),
        abbr,
        gk,
        {},
    )
    assert leg is None and why == "unmatched_player"
    leg, why = map_market(row(event_ticker="KXNBATOTAL-26OCT09SACLAL"), abbr, gk, {})
    assert why == "game_not_in_slate"


# ---------------------------------------------------------------- settlement
def _results_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE games (game_id VARCHAR, home_team INT, home_pts INT, away_pts INT)")
    con.execute("INSERT INTO games VALUES ('g', ?, 110, 100)", [HOME])
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT,"  # noqa: E501
        " pts INT, reb INT, ast INT, fg3m INT, stl INT, blk INT, tov INT, starter BOOLEAN)"
    )
    con.execute("INSERT INTO player_game_stats VALUES ('g',1,?,30,25,5,6,2,0,0,1,TRUE)", [HOME])
    con.execute("INSERT INTO player_game_stats VALUES ('g',2,?,NULL,0,0,0,0,0,0,0,FALSE)", [HOME])
    return con


def _book(legs: list[Leg]) -> duckdb.DuckDBPyConnection:
    book = duckdb.connect(":memory:")
    ensure_schema(book)
    rec = evaluate(legs, 0.3, 0, 0, 0.3, CFG)
    from nba.parlay.papertrade import log_trade

    log_trade(book, rec, CFG.fee, trade_id="t")
    return book


@pytest.mark.parametrize(
    ("policy", "expect"),
    [("void", (None, 0.0)), ("loss", (False, None))],
)
def test_settle_game_and_dnp_policies(
    policy: str, expect: tuple[bool | None, float | None]
) -> None:
    res = _results_db()
    legs = [Leg("g", 0, "win", 0, "yes", HOME), Leg("g", 2, "pts", 5, "yes", HOME)]
    book = _book(legs)
    assert settle_trades(book, res, policy) == 1
    out = book.execute("SELECT outcome, realized_pnl FROM paper_trades").fetchone()
    assert out is not None and out[0] == expect[0]
    if expect[1] is not None:
        assert out[1] == pytest.approx(expect[1])
    else:
        assert out[1] < 0
    # game legs settle: home won by 10 -> spread 9.5 hits, 10.5 misses; total 210
    b2 = _book([Leg("g", 0, "spread", 9.5, "yes", HOME), Leg("g", 0, "total", 209.5, "yes", None)])
    settle_trades(b2, res, policy)
    assert b2.execute("SELECT outcome FROM paper_trades").fetchone() == (True,)
    b3 = _book([Leg("g", 0, "spread", 10.5, "yes", HOME)])
    settle_trades(b3, res, policy)
    assert b3.execute("SELECT outcome FROM paper_trades").fetchone() == (False,)


def test_player_absent_from_final_box_score_is_dnp_not_open() -> None:
    """Inactive players have no box row at all: with the game final and its box ingested they
    must settle as DNP (void/loss), never stay open forever. Box not ingested -> still open."""
    res = _results_db()  # player 9 has no row; the box has other rows; the game is final
    leg = [Leg("g", 9, "pts", 5, "yes", HOME)]
    assert resolve_legs(res, leg, "void") == ("void", False)
    assert resolve_legs(res, leg, "loss") == ("done", False)
    res.execute("DELETE FROM player_game_stats")  # box score not ingested yet
    assert resolve_legs(res, leg, "void")[0] == "open"
    res.execute("INSERT INTO player_game_stats VALUES ('g',1,?,30,25,5,6,2,0,0,1,TRUE)", [HOME])
    res.execute("UPDATE games SET home_pts = NULL, away_pts = NULL")  # game not final
    assert resolve_legs(res, leg, "void")[0] == "open"


# ---------------------------------------------------------------- CLI end to end
def _build_dbs(tmp: Path) -> tuple[Path, Path, Path, Path]:
    nba = tmp / "nba.duckdb"
    c = duckdb.connect(str(nba))
    c.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT,"
        " away_team INT, home_pts INT, away_pts INT)"
    )
    c.execute("INSERT INTO games VALUES ('g0', '2026-10-05', 2026, ?, ?, 112, 104)", [HOME, AWAY])
    c.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT,"
        " minutes FLOAT, pts INT, reb INT, ast INT, fg3m INT, stl INT, blk INT, tov INT,"
        " starter BOOLEAN)"
    )
    c.execute("INSERT INTO player_game_stats VALUES ('g0',1,?,30,25,5,6,2,0,0,1,TRUE)", [HOME])
    c.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR,"
        " player_id INT, prediction JSON)"
    )
    tip = datetime(2026, 10, 9, 0, 30)  # 20:30 ET on 10-08
    win = {"p_home": 0.80, "home_team": HOME, "away_team": AWAY}
    c.execute(
        "INSERT INTO forward_predictions VALUES ('r', '2026-10-08 12:00', 'g', ?, "
        "'rung0_injury_elo', 'v', 'win_prob_home', -1, ?)",
        [tip, json.dumps(win)],
    )
    props = {"q_grid": [v + 15 for v in GRID], "p_play": 0.95}
    c.execute(
        "INSERT INTO forward_predictions VALUES ('r', '2026-10-08 12:00', 'g', ?, "
        "'props_context_residual', 'v', 'pts', 1, ?)",
        [tip, json.dumps(props)],
    )
    c.close()
    k = tmp / "kalshi.duckdb"
    kc = duckdb.connect(str(k))
    kc.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR,"
        " title VARCHAR, player_id INT, stat VARCHAR, threshold FLOAT, open_time TIMESTAMP,"
        " close_time TIMESTAMP, settled_ts TIMESTAMP, result VARCHAR)"
    )
    kc.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT,"
        " last FLOAT, volume INT, open_interest INT, source VARCHAR)"
    )
    ev = "KXNBAGAME-26OCT08SACLAL"
    rows = [
        (f"{ev}-LAL", "KXNBAGAME", ev, "LAL wins", None, "game_win", None),
        (
            "KXNBASPREAD-26OCT08SACLAL-LAL8",
            "KXNBASPREAD",
            "KXNBASPREAD-26OCT08SACLAL",
            "x",
            None,
            "spread",
            7.5,
        ),
        (
            "KXNBAPTS-26OCT08SACLAL-LALP1-20",
            "KXNBAPTS",
            "KXNBAPTS-26OCT08SACLAL",
            "p: 30+",
            1,
            "pts",
            30.0,
        ),
    ]
    for r in rows:
        kc.execute("INSERT INTO kalshi_markets VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)", list(r))
        kc.execute(
            "INSERT INTO kalshi_prices VALUES (?, '2026-10-08 15:00', 0.40, 0.45, 0.42, 1, 1, 'live')",  # noqa: E501
            [r[0]],
        )
    kc.close()
    # fitted model json
    model = tmp / "joint_model.json"
    params = GameModelParams(
        0.5, 7.0, 13.5, -176.0, 1.77, 18.8, 227.0, 20.0, 8.0, 100, 100, (2022,)
    )
    model.write_text(
        json.dumps(
            {"params": json.loads(params.to_json()), "corr": [["game", "margin", "total", 0.0, 5]]}
        )
    )
    return nba, k, model, tmp / "paper.duckdb"


def _run(tmp: Path, extra: list[str]) -> list[dict[str, object]]:
    nba, k, model, paper = (
        _build_dbs(tmp)
        if not (tmp / "nba.duckdb").exists()
        else (
            tmp / "nba.duckdb",
            tmp / "kalshi.duckdb",
            tmp / "joint_model.json",
            tmp / "paper.duckdb",
        )
    )
    argv = [
        "evaluate",
        "--date",
        "2026-10-08",
        "--nba-db",
        str(nba),
        "--kalshi-db",
        str(k),
        "--model",
        str(model),
        "--paper-db",
        str(paper),
        "--out-dir",
        str(tmp / "out"),
        *([] if "--now" in extra else ["--now", "2026-10-08T20:00"]),  # before the 00:30Z tip
        *extra,
    ]
    assert main(argv) == 0
    return json.loads((tmp / "out" / "evaluate_2026-10-08.json").read_text())  # type: ignore[no-any-return]


def test_cli_defers_to_market_with_zero_track_record(tmp_path: Path) -> None:
    out = _run(tmp_path, [])
    assert len(out) == 6  # 3 markets x both sides
    assert all(d["verdict"] == NO_POSITIVE_EV for d in out)
    assert all(d["model_weight"] == 0.0 and d["n_settled_track"] == 0 for d in out)
    # the raw model clearly disagrees with the 0.45 ask on the home-win leg (p~0.80)
    win_yes = next(d for d in out if d["ticker"].endswith("-LAL") and d["side"] == "yes")  # type: ignore[attr-defined]
    assert win_yes["raw_model_prob"] > 0.7 and win_yes["raw_ev_low"] > 0 and win_yes["ev_low"] < 0
    paper = duckdb.connect(str(tmp_path / "paper.duckdb"))
    assert paper.execute("SELECT count(*) FROM paper_trades").fetchone() == (0,)
    assert paper.execute("SELECT count(*) FROM shadow_predictions").fetchone() == (6,)


def test_cli_positive_needs_track_record_and_logs(tmp_path: Path) -> None:
    _build_dbs(tmp_path)
    paper = duckdb.connect(str(tmp_path / "paper.duckdb"))
    ensure_schema(paper)
    leg = Leg("old", 0, "win", 0, "yes", HOME)
    for i in range(400):  # model clearly better than market on settled shadow rows
        log_shadow(
            paper,
            shadow_id=f"s{i}",
            ticker="t",
            side="yes",
            legs=[leg],
            raw_model_prob=0.9 if i % 2 else 0.1,
            market_mid=0.5,
            price=0.5,
            engine="x",
        )
        paper.execute(
            "UPDATE shadow_predictions SET settled=TRUE, outcome=? WHERE shadow_id=?",
            [bool(i % 2), f"s{i}"],
        )
    tr = track_record(paper)
    assert tr.n_settled == 400 and tr.skill > 0.5
    assert tr.n_dates == 1 and tr.gated_skill(14) == 0.0  # one night's rows never open the gate
    paper.execute(  # spread the same history over 20 settled dates
        "UPDATE shadow_predictions SET created_at = created_at - "
        "to_days(CAST(CAST(substr(shadow_id, 2) AS INTEGER) % 20 AS INTEGER))"
    )
    assert track_record(paper).n_dates == 20
    paper.close()
    out = _run(tmp_path, [])
    assert any(d["verdict"] != NO_POSITIVE_EV for d in out)
    assert all(0 < d["model_weight"] < 1 for d in out)  # type: ignore[operator]
    paper = duckdb.connect(str(tmp_path / "paper.duckdb"))
    n_tr = paper.execute("SELECT count(*) FROM paper_trades").fetchone()
    assert n_tr is not None and n_tr[0] >= 1


def test_cli_dnp_policy_changes_player_ev_and_combo(tmp_path: Path) -> None:
    v = {d["ticker"] + d["side"]: d for d in _run(tmp_path, ["--dnp-policy", "void"])}
    lo = {d["ticker"] + d["side"]: d for d in _run(tmp_path, ["--dnp-policy", "loss"])}
    k = "KXNBAPTS-26OCT08SACLAL-LALP1-20yes"
    assert v[k]["raw_model_prob"] > lo[k]["raw_model_prob"]  # void refunds, loss does not
    combos = _run(
        tmp_path, ["--combo", "KXNBAGAME-26OCT08SACLAL-LAL,KXNBAPTS-26OCT08SACLAL-LALP1-20"]
    )
    c = [d for d in combos if "price_basis" in d]
    assert len(c) == 1 and c[0]["verdict"] == NO_POSITIVE_EV and len(c[0]["legs"]) == 2  # type: ignore[arg-type]


def test_shadow_settles_and_track_record(tmp_path: Path) -> None:
    res = _results_db()
    book = duckdb.connect(":memory:")
    ensure_schema(book)
    log_shadow(
        book,
        shadow_id="a",
        ticker="t",
        side="yes",
        legs=[Leg("g", 0, "win", 0, "yes", HOME)],
        raw_model_prob=0.8,
        market_mid=0.6,
        price=0.62,
        engine="m",
    )
    assert (
        log_shadow(
            book,
            shadow_id="a",
            ticker="t",
            side="yes",
            legs=[],
            raw_model_prob=0.1,
            market_mid=0.1,
            price=0.1,
            engine="m",
        )
        is False
    )  # idempotent
    assert settle_shadow(book, res) == 1
    tr = track_record(book)
    assert tr.n_settled == 1 and tr.skill > 0


def test_track_record_counts_each_market_once_not_both_sides() -> None:
    """evaluate logs YES and NO of every market; the NO row is the same event, so only the YES
    row counts toward n_settled (otherwise the min_settled gate opens on half the evidence)."""
    book = duckdb.connect(":memory:")
    ensure_schema(book)
    leg = Leg("g", 0, "win", 0, "yes", HOME)
    for i in range(10):
        for side, p in (("yes", 0.7), ("no", 0.3)):
            log_shadow(
                book,
                shadow_id=f"s{i}|{side}",
                ticker=f"t{i}",
                side=side,
                legs=[leg],
                raw_model_prob=p,
                market_mid=0.5,
                price=0.5,
                engine="x",
            )
    book.execute("UPDATE shadow_predictions SET settled=TRUE, outcome=TRUE")
    assert track_record(book).n_settled == 10


def test_track_record_skill_gated_on_distinct_dates() -> None:
    from nba.parlay.shadow import TrackRecord

    one_night = TrackRecord(200, 0.20, 0.25, 0.2, n_dates=1)
    assert one_night.gated_skill(14) == 0.0  # a single night's props never open the gate
    season = TrackRecord(200, 0.20, 0.25, 0.2, n_dates=20)
    assert season.gated_skill(14) == 0.2


def test_evaluate_surfaces_unmatched_player_instead_of_dropping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prop market whose player has no reviewed alias (player_id NULL) is not priced, but it
    must be named on stderr and counted in the skip summary, never silently dropped."""
    _, k, _, paper = _build_dbs(tmp_path)
    kc = duckdb.connect(str(k))
    kc.execute("UPDATE kalshi_markets SET player_id = NULL WHERE series_ticker = 'KXNBAPTS'")
    kc.close()
    out = _run(tmp_path, [])
    cap = capsys.readouterr()
    assert len(out) == 4  # game + spread, both sides; the prop is NOT priced
    assert not any("KXNBAPTS" in str(d["ticker"]) for d in out)
    assert "'unmatched_player': 1" in cap.out
    assert "WARNING: 1 prop market(s)" in cap.err and "KXNBAPTS-26OCT08SACLAL-LALP1-20" in cap.err
    p = duckdb.connect(str(paper))
    assert p.execute("SELECT count(*) FROM shadow_predictions").fetchone() == (4,)


def test_track_record_dates_are_slate_dates_not_utc_log_time() -> None:
    """One game night's rows logged across 00:00Z (props post late) are ONE date, so the
    14-date gate cannot open early; rows from two slates are two dates."""
    book = duckdb.connect(":memory:")
    ensure_schema(book)
    leg = Leg("g", 0, "win", 0, "yes", HOME)
    for i, (slate, ts) in enumerate(
        [
            ("2026-10-20", datetime(2026, 10, 20, 22, 0)),
            ("2026-10-20", datetime(2026, 10, 21, 0, 30)),  # same slate, next UTC day
            ("2026-10-21", datetime(2026, 10, 21, 22, 0)),
        ]
    ):
        log_shadow(
            book,
            shadow_id=f"{slate}|t{i}|yes",
            ticker=f"t{i}",
            side="yes",
            legs=[leg],
            raw_model_prob=0.6,
            market_mid=0.5,
            price=0.5,
            engine="x",
            now=ts,
        )
    book.execute("UPDATE shadow_predictions SET settled=TRUE, outcome=TRUE")
    tr = track_record(book)
    assert tr.n_settled == 3 and tr.n_dates == 2


def test_cli_prices_nothing_once_the_game_has_tipped(tmp_path: Path) -> None:
    out = _run(tmp_path, ["--now", "2026-10-09T01:00"])  # 30 min after tip-off (M7)
    assert out == []
