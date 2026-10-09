"""Props analysis, budget and target-payout tests: synthetic DBs, no network, no real files."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_joint import HOME, _build_dbs, _run

import nba.parlay.analysis as analysis_mod
from nba.parlay.__main__ import main
from nba.parlay.analysis import Candidate, build_analysis
from nba.parlay.assistant.backend import LLMReply, OllamaUnavailable
from nba.parlay.assistant.names import NameResolver
from nba.parlay.assistant.numguard import REMOVED, collect_numbers
from nba.parlay.budget import (
    KEEP_YOUR_MONEY,
    best_for_budget,
    best_for_budget_from,
    best_for_target,
    best_for_target_from,
    frontier_from,
    max_affordable,
    order_cost,
    plan,
    target_plan,
)
from nba.parlay.config import load_config
from nba.parlay.ev import NO_POSITIVE_EV, POSITIVE_EV, evaluate, order_fee
from nba.parlay.legs import Leg
from nba.parlay.narrate import narrate, narration_payload
from nba.parlay.report import build_report
from nba.parlay.shadow import ensure_schema, log_shadow

CFG = load_config()
D = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 20, 0)
NAMES = NameResolver({"test player": [1]}, {1: "Test Player"})


def _an(tmp: Path, **kw: Any) -> Any:
    if not (tmp / "nba.duckdb").exists():
        _build_dbs(tmp)
    return build_analysis(
        D,
        NOW,
        nba_db=tmp / "nba.duckdb",
        kalshi_db=tmp / "kalshi.duckdb",
        paper_db=tmp / "paper.duckdb",
        model_path=tmp / "joint_model.json",
        names=NAMES,
        **kw,
    )


def _open_gate(tmp: Path) -> None:
    """Settled shadow history where the model clearly beats the market, over 20 dates."""
    _build_dbs(tmp) if not (tmp / "nba.duckdb").exists() else None
    paper = duckdb.connect(str(tmp / "paper.duckdb"))
    ensure_schema(paper)
    leg = Leg("old", 0, "win", 0, "yes", HOME)
    for i in range(400):
        log_shadow(
            paper, shadow_id=f"s{i}", ticker="t", side="yes", legs=[leg],
            raw_model_prob=0.9 if i % 2 else 0.1, market_mid=0.5, price=0.5, engine="x",
        )  # fmt: skip
        paper.execute(
            "UPDATE shadow_predictions SET settled=TRUE, outcome=? WHERE shadow_id=?",
            [bool(i % 2), f"s{i}"],
        )
    paper.execute(
        "UPDATE shadow_predictions SET created_at = created_at - "
        "to_days(CAST(CAST(substr(shadow_id, 2) AS INTEGER) % 20 AS INTEGER))"
    )
    paper.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _cand(
    ask: float, p: float, *, n_settled: int = 0, kind: str = "single", key: str = "k"
) -> Candidate:
    leg = Leg("g", 1, "pts", 20.0, "yes", HOME)
    rec = evaluate([leg], p, 0, 0, ask, CFG, bid=ask - 0.02, n_settled=n_settled,
                   skill=0.2 if n_settled else 0.0)  # fmt: skip
    return Candidate(
        kind, key, "yes", (leg,), key, ("g",), ask, ask - 0.02, ask - 0.01, "KXNBAPTS",
        rec, 0, p * 0.9, min(p * 1.1, 0.99), kind == "single", "kalshi_ask",
    )  # fmt: skip


# ----------------------------------------------------------------- report contract
def test_contract_fields_and_defers_to_market(tmp_path: Path) -> None:
    an = _an(tmp_path)
    rep = build_report(an)
    assert rep["n_contracts"] == 6 and rep["headline_verdict"] == NO_POSITIVE_EV
    assert rep["gate"]["n_settled"] == 0 and rep["gate"]["gate_open"] is False
    need = {"legs", "model_prob", "prob_interval", "price", "fees", "ev", "ev_low", "verdict"}
    for r in rep["contracts"]:
        assert need <= r.keys() and r["model_weight"] == 0.0
        assert {"mid", "ask", "bid", "raw_prob_interval", "p_play", "drivers", "shadow"} <= r.keys()
        assert len(r["prob_interval"]) == 2
    prop = next(r for r in rep["contracts"] if r["stat"] == "pts" and r["side"] == "yes")
    assert prop["p_play"] == 0.95 and prop["dnp_risk"] == pytest.approx(0.05)
    assert prop["player"] == "Test Player"
    assert "disclaimer" in rep and "not financial advice" in rep["disclaimer"]
    same = rep["same_game_parlays"]
    assert same and all(c["independence_prob"] is not None and not c["tradable"] for c in same)
    evs = [c["ev_low"] for c in same]
    assert evs == sorted(evs, reverse=True)


def test_verdicts_match_evaluate_cli(tmp_path: Path) -> None:
    """The analysis reuses the engine: per-contract EV/verdict equal the existing ``evaluate``."""
    ev = {(d["ticker"], d["side"]): d for d in _run(tmp_path, ["--no-log"])}  # type: ignore[index]
    rep = build_report(_an(tmp_path))
    assert len(rep["contracts"]) == len(ev)
    for r in rep["contracts"]:
        e = ev[(r["ticker"], r["side"])]
        assert r["verdict"] == e["verdict"]
        assert r["ev"] == pytest.approx(e["ev"]) and r["ev_low"] == pytest.approx(e["ev_low"])
        assert r["raw_model_prob"] == pytest.approx(e["raw_model_prob"])


def test_gate_open_flags_positive(tmp_path: Path) -> None:
    _open_gate(tmp_path)
    an = _an(tmp_path)
    assert an.gate_open
    rep = build_report(an)
    assert rep["n_flagged_positive"] >= 1 and all(
        0 < r["model_weight"] < 1 for r in rep["contracts"]
    )


def test_no_writes_to_source_dbs(tmp_path: Path) -> None:
    _build_dbs(tmp_path)
    files = [tmp_path / n for n in ("nba.duckdb", "kalshi.duckdb", "joint_model.json")]
    before = [_sha(f) for f in files]
    # paper DB deliberately absent: analysis must not create it
    assert not (tmp_path / "paper.duckdb").exists()
    rep = build_report(_an(tmp_path), budget=20, target=10)
    assert rep["budget"]["mode"] == "target"
    assert [_sha(f) for f in files] == before
    assert not (tmp_path / "paper.duckdb").exists()


def test_cli_writes_md_and_json_only(tmp_path: Path) -> None:
    nba, k, model, paper = _build_dbs(tmp_path)
    out = tmp_path / "rep"
    argv = ["analyze", "--date", "2026-10-08", "--now", "2026-10-08T20:00", "--db", str(nba),
            "--kalshi-db", str(k), "--model", str(model), "--paper-db", str(paper),
            "--out", str(out), "--budget", "20"]  # fmt: skip
    assert main(argv) == 0
    md = (out / "2026-10-08.md").read_text()
    js = json.loads((out / "2026-10-08.json").read_text())
    assert "not financial advice" in md and "no_positive_ev_found" in md
    assert KEEP_YOUR_MONEY in md and js["budget"]["verdict"] == NO_POSITIVE_EV
    assert sorted(p.name for p in out.iterdir()) == ["2026-10-08.json", "2026-10-08.md"]


# ----------------------------------------------------------------- shadow arms
def _add_shadow(tmp: Path) -> None:
    c = duckdb.connect(str(tmp / "nba.duckdb"))
    grid = [v + 22 for v in range(19)]
    for name in ("props_context_residual_int", "props_context_residual_lt"):
        c.execute(
            "INSERT INTO forward_predictions SELECT run_id, made_at, game_id, tipoff, ?, version,"
            " target, player_id, ? FROM forward_predictions WHERE model_name = "
            "'props_context_residual'",
            [name, json.dumps({"q_grid": grid, "p_play": 0.95})],
        )
    c.close()


def test_shadow_columns_labelled_and_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_dbs(tmp_path)
    _add_shadow(tmp_path)
    rep = build_report(_an(tmp_path))
    prop = next(r for r in rep["contracts"] if r["stat"] == "pts" and r["side"] == "yes")
    sh = prop["shadow"]
    assert sh["label"] == "shadow, unconfirmed" and sh["p"]["int"] is not None
    assert sh["p"]["t30"] is None and "shadow, unconfirmed" in build_report_md(rep)
    # primary prediction is unchanged by the shadow rows
    assert prop["raw_model_prob"] == pytest.approx(
        next(r for r in build_report(_an(tmp_path, shadow=False))["contracts"]
             if r["stat"] == "pts" and r["side"] == "yes")["raw_model_prob"]
    )  # fmt: skip
    monkeypatch.setattr(analysis_mod, "SEALED_BEFORE", date(2027, 1, 1))
    sealed = build_report(_an(tmp_path))
    assert not sealed["shadow_enabled"] and all(r["shadow"] is None for r in sealed["contracts"])
    assert any("SEALED" in n for n in sealed["notes"])
    assert "shadow" not in build_report_md(sealed).split("## Key drivers")[1].split("##")[0].lower()


def build_report_md(rep: dict[str, Any]) -> str:
    from nba.parlay.report import render_markdown

    return render_markdown(rep)


# ----------------------------------------------------------------- order math
def test_fee_round_up_at_small_stakes() -> None:
    c = _cand(0.45, 0.5)
    assert order_fee(0.45, CFG.fee, 1) == pytest.approx(0.02)  # 0.0173 rounds UP to a cent
    cost1, fee1 = order_cost(c, 1, CFG)
    assert fee1 == pytest.approx(0.02) and cost1 == pytest.approx(0.47)
    cost10, fee10 = order_cost(c, 10, CFG)
    assert fee10 == pytest.approx(0.18) and fee10 < 10 * fee1  # one round-up per ORDER
    p = plan(c, 10, CFG)
    assert p.cost == pytest.approx(4.68) and p.profit_if_hit == pytest.approx(10 - 4.68)
    assert p.ev == pytest.approx(10 * c.rec.model_prob - 4.68)


@pytest.mark.parametrize("ask", [0.03, 0.07, 0.45, 0.5, 0.91])
@pytest.mark.parametrize("budget", [1.0, 4.4, 20.0, 99.99])
def test_budget_never_exceeded_and_integral(ask: float, budget: float) -> None:
    c = _cand(ask, 0.5)
    n = max_affordable(c, budget, CFG)
    assert isinstance(n, int) and order_cost(c, n, CFG)[0] <= budget + 1e-9
    assert order_cost(c, n + 1, CFG)[0] > budget  # maximal: one more contract would not fit


def test_unaffordable_contract_excluded() -> None:
    c = _cand(0.91, 0.99)
    assert max_affordable(c, 0.5, CFG) == 0


# ----------------------------------------------------------------- budget answers
def test_budget_keep_your_money_when_gate_closed(tmp_path: Path) -> None:
    out = best_for_budget_from(_an(tmp_path), 20)
    assert out["verdict"] == NO_POSITIVE_EV and out["answer"] == KEEP_YOUR_MONEY
    assert out["recommendation"] is None and out["gate"]["gate_open"] is False
    pdl = [o["order"]["ev_low_per_dollar"] for o in out["ranked"]]
    assert pdl == sorted(pdl, reverse=True) and "$ staked" in out["ranked_by"]
    assert out["raw_hypothesis"]["label"] == "hypothesis, not a recommendation"
    assert out["orders_placed"] == 0 and "not financial advice" in out["disclaimer"]
    for o in out["ranked"]:
        od = o["order"]
        assert od["cost"] <= 20 and isinstance(od["contracts"], int) and od["contracts"] >= 1
        assert od["ev_low"] <= 0  # no positive order while the model weight is 0
    assert out["gate"]["n_settled"] == 0 and out["gate"]["min_settled_dates"] == 14


def test_budget_positive_when_gate_open_respects_budget(tmp_path: Path) -> None:
    _open_gate(tmp_path)
    out = best_for_budget_from(_an(tmp_path), 20)
    assert out["verdict"] == POSITIVE_EV and out["recommendation"] is not None
    od = out["recommendation"]["order"]
    assert od["ev_low"] > 0 and od["cost"] <= 20 and od["contracts"] >= 1
    assert out["recommendation"]["tradable"] is True  # a parlay at a product price is never advised
    assert (
        od["fee"]
        == pytest.approx(
            order_fee(out["recommendation"]["ask"], CFG.fee, od["contracts"], series="KXNBA")
        )
        or od["fee"] > 0
    )


def test_public_entry_points(tmp_path: Path) -> None:
    _build_dbs(tmp_path)
    kw = dict(nba_db=tmp_path / "nba.duckdb", kalshi_db=tmp_path / "kalshi.duckdb",
              paper_db=tmp_path / "paper.duckdb", model_path=tmp_path / "joint_model.json",
              names=NAMES)  # fmt: skip
    assert best_for_budget(20, D, NOW, **kw)["answer"] == KEEP_YOUR_MONEY
    assert best_for_target(20, 10, D, NOW, **kw)["mode"] == "target"
    with pytest.raises(ValueError):
        best_for_budget_from(_an(tmp_path), 0)


# ----------------------------------------------------------------- target mode
def test_target_reached_after_fees_within_budget(tmp_path: Path) -> None:
    out = best_for_target_from(_an(tmp_path), 20, 10)
    assert out["ranked"] and out["max_parlay_legs"] <= 4
    for o in out["ranked"]:
        od = o["order"]
        assert od["profit_if_hit"] >= 10 - 1e-9 and od["cost"] <= 20 + 1e-9
        assert od["profit_if_hit"] == pytest.approx(od["payout"] - od["cost"])
    ps = [o["model_prob"] for o in out["ranked"]]
    assert ps == sorted(ps, reverse=True)  # primary = most likely way to make the target
    pd = [o["order"]["ev_low_per_dollar"] for o in out["best_ev_per_dollar"]]
    assert pd == sorted(pd, reverse=True) and "best_ev_per_dollar" in out["frontier"][0]
    fr = [f for f in out["frontier"] if f["reachable"]]
    assert len(fr) >= 3
    assert [f["p_hit"] for f in fr] == sorted((f["p_hit"] for f in fr), reverse=True)
    assert all(f["p_hit_not_above_previous"] for f in fr[1:])  # fixture frontier decreasing
    assert all("ev_low_per_dollar" in f for f in fr)
    assert out["verdict"] == NO_POSITIVE_EV and "keep your money" in out["answer"]
    with pytest.raises(ValueError, match="capped at 4"):
        best_for_target_from(_an(tmp_path), 20, 10, max_legs=5)


def test_target_unreachable_and_all_frontier_levels(tmp_path: Path) -> None:
    out = best_for_target_from(_an(tmp_path), 1, 1000)
    assert out["n_reaching_target"] == 0 and out["recommendation"] is None
    assert "no option on this slate reaches" in out["answer"]
    fr = {f["target_profit"]: f for f in out["frontier"]}
    assert {5.0, 10.0, 25.0, 50.0, 100.0, 1000.0} <= fr.keys()
    assert fr[1000.0]["reachable"] is False


def test_kalshi_title_names_beat_ids() -> None:
    from nba.parlay.analysis import OverlayNames, _title_names
    from nba.parlay.kalshi_map import MarketRow

    m = MarketRow(
        "t", "KXNBAPTS", "e", "Victor Wembanyama: 30+ points", 5, "pts", 30.0, 0.4, 0.5, None
    )
    ov = OverlayNames(NAMES, _title_names([m]))
    assert ov.display(5) == "Victor Wembanyama" and ov.display(1) == "Test Player"
    assert ov.display(9) == "player 9"


def test_target_plan_is_minimal_and_frontier_monotone() -> None:
    cands = [_cand(a, a * 0.95, key=f"k{a}") for a in (0.5, 0.2, 0.05)]
    for c in cands:
        p = target_plan(c, 20.0, 5.0, CFG)
        assert p is not None and p.profit_if_hit >= 5.0
        assert p.n == 1 or plan(c, p.n - 1, CFG).profit_if_hit < 5.0  # smallest reaching order
    fr = [f for f in frontier_from(cands, 20.0, (5.0, 10.0, 25.0), CFG) if f["reachable"]]
    assert len(fr) == 3
    ps = [f["raw_p_hit"] for f in fr]
    assert ps == sorted(ps, reverse=True)  # bigger target -> no higher P(hit)
    assert all(b.get("p_hit_not_above_previous") for b in fr[1:])
    evs = [f["ev_low"] for f in fr]
    assert evs == sorted(evs, reverse=True)  # and (here) no better EV


def test_target_positive_only_for_tradable(tmp_path: Path) -> None:
    _open_gate(tmp_path)
    out = best_for_target_from(_an(tmp_path), 20, 10)
    if out["verdict"] == POSITIVE_EV:
        assert out["recommendation"]["tradable"] is True


# ----------------------------------------------------------------- narration + numguard
class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text, self.seen = text, []  # type: ignore[var-annotated]

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        self.seen.append(messages)
        return LLMReply(self.text)


def test_narration_numguard_and_payload_only(tmp_path: Path) -> None:
    rep = build_report(_an(tmp_path), budget=20)
    pay = narration_payload(rep)
    ask = pay["least_bad_contracts"][0]["ask"]
    llm = FakeLLM(
        f"The best ask is {ask:.3f}.\nThe model puts it at 99.7%.\n"
        "I recommend you bet $50 now.\nNo positive EV was found; analysis only."
    )
    prose, note = narrate(rep, llm)
    assert prose is not None and f"{ask:.3f}" in prose
    assert "99.7%" not in prose and REMOVED in prose
    assert "bet $50" not in prose and "removed" in note
    sent = json.dumps(llm.seen[0])  # the model saw only the computed numbers, no raw DB rows
    assert "q_grid" not in sent and rep["date"] in sent
    allowed = collect_numbers(pay)
    assert ask in allowed


def test_narration_skipped_without_ollama(tmp_path: Path) -> None:
    rep = build_report(_an(tmp_path))
    assert narrate(rep, None)[0] is None

    class Down:
        def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
            raise OllamaUnavailable("Ollama is not reachable\nmore")

    prose, note = narrate(rep, Down())
    assert prose is None and "skipped" in note


# ----------------------------------------------------------------- assistant tools
def test_assistant_budget_tools(tmp_path: Path) -> None:
    from nba.parlay.assistant.render import render_result
    from nba.parlay.assistant.toolbox import EnginePaths, Toolbox

    nba, k, model, paper = _build_dbs(tmp_path)
    tb = Toolbox(EnginePaths(nba, k, paper, model), CFG, NAMES, default_date=D)
    r = tb.call("best_for_budget", {"budget_usd": 20})
    assert r["answer"] == KEEP_YOUR_MONEY
    t = tb.call("best_for_target", {"budget_usd": 20, "target_profit_usd": 10})
    assert t["frontier"] and "keep your money" in t["answer"]
    assert "budget_usd" in tb.call("best_for_budget", {})["error"]
    txt = render_result("best_for_budget", r)
    assert "keep your money" in txt and "not financial advice" in txt.lower()
    shutil.rmtree(tmp_path, ignore_errors=True)
