# ruff: noqa: E501
"""LLM_SHADOW harness: pure metrics, the fixture builder, module-pending gate, leg table on a synthetic season."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from research.eval import llm_shadow_eval as ev

DOC = Path("docs/prereg/LLM_SHADOW_2026_27.md")


def _legs(n_dates: int = 40, per: int = 25, edge: float = 0.0, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_dates):
        for i in range(per):
            p_true = rng.uniform(0.3, 0.7)
            y = float(rng.random() < p_true)
            p_prod = float(np.clip(p_true + rng.normal(0, 0.02), 0.05, 0.95))
            p_llm = float(
                np.clip(p_prod + edge * (p_true - p_prod) * 10 + rng.normal(0, 0.05), 0.05, 0.95)
            )
            o = p_prod + 0.02
            rows.append(
                {
                    "game_id": f"g{d}", "game_date": dt.date(2026, 11, 1) + dt.timedelta(days=d),
                    "player_id": i, "stat": ev.STATS[i % 4], "y": y, "p_llm": p_llm, "p_prod": p_prod,
                    "p_mkt": p_prod, "p_over_raw": o, "p_under_raw": 1 - p_prod + 0.02,
                    "mult_t60": p_prod, "mult_close": p_prod + rng.normal(0, 0.01) if i % 2 else None,
                }
            )  # fmt: skip
    return pl.DataFrame(rows)


def test_ll_and_blend_and_ece() -> None:
    y = np.array([1.0, 0.0])
    assert np.allclose(ev.leg_ll(y, np.array([0.5, 0.5])), np.log(2))
    p = ev.blend_logit(np.array([0.3]), np.array([0.3]))
    assert abs(float(p[0]) - 0.3) < 1e-9
    ece, tab = ev.reliability(np.array([1, 0, 1, 0.0]), np.array([0.95, 0.05, 0.95, 0.05]))
    assert ece < 0.06 and sum(r["n"] for r in tab) == 4


def test_p_ge_full_threshold_is_ceil_of_line() -> None:
    pred = {"p_ge_full": {"n": 100, "c": [90, 70, 50, 30, 10]}}
    assert ev.parse_p_ge_full(pred, 2.5) == 0.5  # P(Y >= 3)
    assert ev.parse_p_ge_full(pred, 9.5) == 0.0  # beyond the stored range
    assert ev.parse_p_ge_full({"mean": 1}, 2.5) is None


def test_clv_side_and_value() -> None:
    side = ev.clv_side(np.array([0.6, 0.4]), np.array([0.5, 0.5]), np.array([0.5, 0.5]))
    assert list(side) == [1, -1]
    assert np.allclose(
        ev.clv_value(side, np.array([0.5, 0.5]), np.array([0.52, 0.52])), [0.02, -0.02]
    )


def test_summarize_null_and_noise_costs_log_loss() -> None:
    s = ev.summarize(_legs())
    assert s["n"] == 1000 and s["n_dates"] == 40
    assert s["D_vs_prod"]["point"] > 0  # pure noise around production costs log loss
    assert s["D_vs_prod"]["lo"] < s["D_vs_prod"]["point"] < s["D_vs_prod"]["hi"]
    assert "clv" in s and s["clv"]["n"] == 480


def test_decide_gates_and_kill() -> None:
    base = {"n": 6000, "n_dates": 61, "ll_llm": 0.70, "ll_prod": 0.69, "ll_mkt": 0.69}
    kill = {**base, "D_vs_prod": {"point": 0.01, "lo": 0.002, "hi": 0.02}}
    assert ev.decide(kill, "L60", {})["outcome"] == "KILL"
    assert ev.decide({**kill, "n": 100}, "L60", {})["outcome"] == "KEEP_RUNNING"  # below the n gate
    assert (
        ev.decide({**kill, "D_vs_prod": {"point": 0.009, "lo": 0.002, "hi": 0.02}}, "L30", {})[
            "outcome"
        ]
        == "MONITOR"
    )
    early = {
        **base,
        "n_dates": 31,
        "n": 2100,
        "D_vs_prod": {"point": 0.012, "lo": 0.001, "hi": 0.02},
    }
    assert ev.decide(early, "L30", {})["outcome"] == "KILL_COST"
    assert ev.decide({**early, "n": 1500}, "L30", {})["outcome"] == "MONITOR"
    mkt_worse = {
        **base,
        "ll_llm": 0.68,
        "ll_mkt": 0.67,
        "D_vs_prod": {"point": -0.008, "lo": -0.02, "hi": -0.001},
    }
    assert (
        ev.decide(mkt_worse, "L60", {"G1": True, "G2": True, "G3": True})["outcome"]
        == "KEEP_RUNNING"
    )
    ok = {**base, "ll_llm": 0.68, "D_vs_prod": {"point": -0.008, "lo": -0.02, "hi": -0.001}}
    g = {"G1": True, "G2": True, "G3": True}
    assert ev.decide(ok, "L60", g)["outcome"] == "PASS_INFORMATION_ONLY"
    assert ev.decide(ok, "L60", {**g, "G2": False})["outcome"] == "KEEP_RUNNING"
    wide = {**ok, "D_vs_prod": {"point": -0.008, "lo": -0.02, "hi": 0.004}}
    assert ev.decide(wide, "L60", g)["outcome"] == "KEEP_RUNNING"


def test_doc_marker_and_open_fields_pending() -> None:
    checks = {c.name: c for c in ev.doc_checks(DOC, allow_draft=True)}
    assert checks["doc has whole-line marker"].status == "PASS"
    assert checks["open fields filled (check 6)"].status == "PENDING"
    strict = {c.name: c for c in ev.doc_checks(DOC, allow_draft=False)}
    assert strict["open fields filled (check 6)"].status == "FAIL"


def test_gate_reports_module_pending_exit_2_when_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ev, "_import_module", lambda: None)
    monkeypatch.setattr(
        ev, "legcount_check", lambda: ev.Check("leg-count projection (check 7)", "PASS")
    )
    assert ev.run_gate(DOC, allow_draft=True) == 2


def test_fixture_builds_and_facts_planted_after_tip_are_refused(tmp_path: Path) -> None:
    from nba.facts.query import PostTipError, known_before_tip

    paths = ev.build_fixture(tmp_path)
    con = duckdb.connect(str(paths["facts"]), read_only=True)
    facts = known_before_tip(con, ev.GAME, 60)
    assert "player:103" not in set(facts["subject"].to_list())  # post-tip 'out' fact
    assert "announced_starter" not in set(facts["predicate"].to_list())  # T-30 snapshot
    assert "player:101" in set(facts["subject"].to_list())
    try:
        from nba.facts.query import as_of

        as_of(con, ev.GAME, ev.TIP + dt.timedelta(minutes=1))
        raised = False
    except PostTipError:
        raised = True
    assert raised
    m = duckdb.connect(str(paths["market"]), read_only=True)
    assert m.execute("SELECT count(*) FROM live_book_odds").fetchone()[0] == 8  # type: ignore[index]


def test_stub_provider_modes() -> None:
    payload = {"legs": [{"player_id": 1, "stat": "pts", "line": 1.5, "p_over_prod": 0.5}]}
    ok = json.loads(ev.StubProvider("ok").forecast(payload)["raw_text"])
    assert ok["forecasts"][0]["stat"] == "pts"
    for m in ("prose", "fenced"):
        try:
            json.loads(ev.StubProvider(m).forecast(payload)["raw_text"])
            parsed = True
        except json.JSONDecodeError:
            parsed = False
        assert not parsed


def test_build_legs_scores_at_t60_line_and_drops_pushes_and_dnp(tmp_path: Path) -> None:
    paths = ev.build_fixture(tmp_path)
    n = duckdb.connect(str(paths["nba"]))
    tip = ev.TIP
    for pid, pts in ((101, 30), (102, 17), (103, 18), (104, 0)):
        n.execute(
            "INSERT INTO player_game_stats VALUES (?,?,?,?,?,?,?,?)",
            [ev.GAME, pid, 1, 30.0 if pid != 104 else 0.0, pts, 5, 5, 1],
        )
    n.execute("UPDATE games SET home_pts = 100, away_pts = 90 WHERE game_id = ?", [ev.GAME])
    for pid in ev.PLAYERS:
        pj = {"p_over": 0.6, "line": ev.LINE[pid], "reason": "x"}
        n.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                "llm",
                tip - dt.timedelta(minutes=57),
                ev.GAME,
                tip,
                "llm_shadow",
                "v1",
                "pts",
                pid,
                json.dumps(pj),
            ],
        )
    n.close()
    legs = ev.build_legs(
        duckdb.connect(str(paths["nba"]), read_only=True),
        duckdb.connect(str(paths["market"]), read_only=True),
    )
    assert sorted(legs["player_id"].to_list()) == [101, 102, 103]  # 104 did not play
    row = legs.filter(pl.col("player_id") == 101).row(0, named=True)
    assert row["y"] == 1.0 and abs(row["p_llm"] - 0.6) < 1e-12 and row["mult_close"] is None
    assert legs.filter(pl.col("player_id") == 103)["y"][0] == 0.0


# ----------------------------------------------------------------------------- reference fake of the assumed interface
def _fake_modules() -> tuple[object, object, object]:
    """A minimal reference implementation of the assumed nba.shadow_llm contract (an executable spec, not the steward's code)."""
    import datetime as _dt
    import types

    from nba.daily.store import ForwardPrediction, append_predictions
    from nba.facts.query import known_before_tip

    def build_payload(game_id, *, now, paths):  # type: ignore[no-untyped-def]
        f = duckdb.connect(str(paths["facts"]), read_only=True)
        try:
            facts = known_before_tip(f, game_id, 60)
        finally:
            f.close()
        n = duckdb.connect(str(paths["nba"]), read_only=True)
        try:
            prod = n.execute(
                "SELECT player_id, prediction FROM forward_predictions WHERE model_name = 'props_context_residual'"
            ).fetchall()
        finally:
            n.close()
        legs = []
        for pid, pj in prod:
            line = ev.LINE[int(pid)]
            legs.append(
                {
                    "player_id": int(pid),
                    "stat": "pts",
                    "line": line,
                    "p_over_prod": ev.parse_p_ge_full(pj, line),
                }
            )
        return {
            "game_id": game_id,
            "tipoff": ev.TIP.isoformat(),
            "facts": [
                {
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "known_at": str(r["known_at"]),
                }
                for r in facts.iter_rows(named=True)
            ],
            "legs": legs,
        }

    def run_game(game_id, *, provider, now, paths):  # type: ignore[no-untyped-def]
        if now >= ev.TIP:
            return {"status": "post_tip_refused", "rows_written": 0}
        payload = build_payload(game_id, now=now, paths=paths)  # type: ignore[no-untyped-call]
        raw = provider.forecast(payload)
        status, rows = "ok", []
        try:
            body = json.loads(raw["raw_text"])
            keys = [(x["player_id"], x["stat"]) for x in body["forecasts"]]
            want = [(x["player_id"], x["stat"]) for x in payload["legs"]]
            if set(body) != {"forecasts"} or keys != want:
                raise ValueError("legs")
            for x in body["forecasts"]:
                if not (0.01 <= float(x["p_over"]) <= 0.99):
                    raise ValueError("p")
                rows.append(
                    ForwardPrediction(
                        game_id,
                        ev.TIP,
                        "llm_shadow",
                        "v1",
                        x["stat"],
                        {"p_over": x["p_over"], "reason": x["reason"]},
                        int(x["player_id"]),
                    )
                )
        except (ValueError, KeyError, TypeError):
            status, rows = "schema_violation", []
        if rows:
            n = duckdb.connect(str(paths["nba"]))
            append_predictions(n, "r1", now, rows)
            n.close()
        s = duckdb.connect(str(paths["shadow"]))
        s.execute("CREATE TABLE IF NOT EXISTS llm_shadow_calls (game_id VARCHAR, status VARCHAR)")
        s.execute("INSERT INTO llm_shadow_calls VALUES (?, ?)", [game_id, status])
        s.close()
        return {"status": status, "rows_written": len(rows)}

    _ = _dt
    return (
        types.SimpleNamespace(),
        types.SimpleNamespace(build_payload=build_payload),
        types.SimpleNamespace(run_game=run_game),
    )


def test_fixture_checks_pass_against_the_reference_fake() -> None:
    checks = ev.fixture_checks(_fake_modules())
    assert [c.status for c in checks] == ["PASS"] * 4, [c.line() for c in checks]


def test_fixture_checks_fail_when_the_runner_leaks_a_post_tip_fact() -> None:
    prov, pay, run = _fake_modules()
    good = pay.build_payload  # type: ignore[attr-defined]

    def leaky(game_id, *, now, paths):  # type: ignore[no-untyped-def]
        p = good(game_id, now=now, paths=paths)
        p["facts"].append(
            {
                "subject": "player:103",
                "predicate": "listed_status",
                "known_at": str(ev.TIP + dt.timedelta(minutes=5)),
            }
        )
        return p

    pay.build_payload = leaky  # type: ignore[attr-defined]
    checks = {c.name[:3]: c.status for c in ev.fixture_checks((prov, pay, run))}
    assert checks["(b)"] == "FAIL"
