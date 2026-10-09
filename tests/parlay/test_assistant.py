"""Assistant tests: scripted fake LLM, synthetic DBs, no network."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from test_joint import HOME, _build_dbs

from nba.parlay.__main__ import ROOT
from nba.parlay.assistant.agent import Assistant
from nba.parlay.assistant.backend import (
    LLMReply,
    OllamaBackend,
    OllamaUnavailable,
    ToolCall,
)
from nba.parlay.assistant.cli import add_arguments, run
from nba.parlay.assistant.names import NameResolver, PlayerResolutionError
from nba.parlay.assistant.numguard import REMOVED, check_prose, collect_numbers
from nba.parlay.assistant.toolbox import EnginePaths, Toolbox
from nba.parlay.config import load_config
from nba.parlay.ev import NO_POSITIVE_EV
from nba.parlay.legs import Leg
from nba.parlay.shadow import ensure_schema, log_shadow

CFG = load_config()
D = date(2026, 10, 8)
WIN = {"stat": "win", "team": "LAL"}
PTS = {"stat": "pts", "player": "Test Player", "threshold": 30}


class FakeLLM:
    """Replays scripted replies; records every message list it was shown."""

    def __init__(self, replies: list[LLMReply]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _resolver() -> NameResolver:
    return NameResolver({"test player": [1], "other guy": [2]}, {1: "Test Player", 2: "Other Guy"})


def _paths(tmp: Path) -> EnginePaths:
    if (tmp / "nba.duckdb").exists():
        nba, k, model, paper = (
            tmp / n for n in ("nba.duckdb", "kalshi.duckdb", "joint_model.json", "paper.duckdb")
        )
    else:
        nba, k, model, paper = _build_dbs(tmp)
    return EnginePaths(nba, k, paper, model)


def _box(tmp: Path, **kw: Any) -> Toolbox:
    return Toolbox(_paths(tmp), CFG, _resolver(), default_date=D, **kw)


def _call(name: str, **args: Any) -> LLMReply:
    return LLMReply("", [ToolCall(name, args)])


# ---------------------------------------------------------------- tools
def test_dispatch_and_arg_validation(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    assert "unknown tool" in tb.call("place_order", {})["error"]
    assert "unknown argument" in tb.call("list_slate", {"bogus": 1})["error"]
    assert "missing required" in tb.call("price_parlay", {})["error"]
    assert "one of" in tb.call("price_parlay", {"legs": [{"stat": "dunks"}]})["error"]
    assert "bad date" in tb.call("list_slate", {"date": "10/08"})["error"]
    assert (
        "finite number"
        in tb.call("explain_leg", {"player": "x", "stat": "pts", "threshold": "30"})["error"]
    )
    assert (
        "not guessing"
        in tb.call("explain_leg", {"player": "Test Playr X", "stat": "pts", "threshold": 30})[
            "error"
        ]
    )
    assert "needs player" in tb.call("price_parlay", {"legs": [{"stat": "pts"}]})["error"]
    assert (
        "no forward prediction"
        in tb.call("price_parlay", {"legs": [{**PTS, "player": "Other Guy"}]})["error"]
    )
    assert "no forward predictions" in tb.call("list_slate", {"date": "2026-10-20"}).get(
        "error", "no forward predictions"
    )
    ls = tb.call("list_slate", {})
    assert ls["n_games"] == 1 and ls["games"][0]["top_players"][0]["name"] == "Test Player"


def test_name_resolver_never_guesses() -> None:
    r = NameResolver({"a b": [1, 2], "c d": [3]}, {})
    with pytest.raises(PlayerResolutionError, match="ambiguous"):
        r.resolve("A. B")
    assert r.resolve("c d") == 3 and r.resolve("7") == 7
    with pytest.raises(PlayerResolutionError, match="unknown"):
        r.resolve("zzz")


def test_price_single_leg_defers_to_market(tmp_path: Path) -> None:
    res = _box(tmp_path).call("price_parlay", {"legs": [WIN]})
    assert res["priced"] and res["verdict"] == NO_POSITIVE_EV
    lo, hi = res["prob_interval"]
    assert lo <= res["model_prob"] <= hi  # weight 0 -> interval collapses onto the market mid
    assert res["fees"] > 0 and res["price"] == pytest.approx(0.45)
    assert res["ev_low"] < 0 and res["raw_model_prob"] > 0.7 and res["model_weight"] == 0.0
    assert res["fee_model"]["verified"] is True


def test_price_combo_and_user_ask_and_unpriced(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    combo = tb.call("price_parlay", {"legs": [WIN, PTS]})
    assert combo["price_basis"].endswith("NOT_A_TRADABLE_PRICE")
    assert combo["price"] == pytest.approx(0.45 * 0.45) and combo["n_sims"] > 0
    assert "correlation_effect" in combo and combo["verdict"] == NO_POSITIVE_EV
    # a leg with no Kalshi market and no ask -> not evaluable, never an invented price
    odd = tb.call("price_parlay", {"legs": [{"stat": "total", "team": "LAL", "threshold": 215}]})
    assert odd["verdict"] == "not_evaluable_no_price" and odd["ev"] is None
    ok = tb.call(
        "price_parlay", {"legs": [{"stat": "total", "team": "LAL", "threshold": 215}], "ask": 0.5}
    )
    assert ok["price_basis"] == "user_supplied_ask" and ok["ev_low"] is not None
    assert "between 0 and 1" in tb.call("price_parlay", {"legs": [WIN], "ask": 1.5}).get(
        "error", "between 0 and 1"
    )


def test_what_if_reports_deltas(tmp_path: Path) -> None:
    res = _box(tmp_path).call("what_if", {"legs": [WIN], "add": PTS})
    assert res["before"]["n_legs"] == 1 and res["after"]["n_legs"] == 2
    d = res["delta"]
    assert d["joint_prob_if_all_play"] < 0 and "correlation_effect" in d
    assert "give" in _box(tmp_path).call("what_if", {"legs": [WIN]})["error"]
    assert "out of range" in _box(tmp_path).call("what_if", {"legs": [WIN], "remove": [3]})["error"]


def test_explain_leg_includes_prediction_and_kalshi(tmp_path: Path) -> None:
    res = _box(tmp_path).call(
        "explain_leg", {"player": "test player", "stat": "pts", "threshold": 30}
    )
    assert res["stored_prediction"]["p_play"] == 0.95 and res["p_play"] == 0.95
    assert 0 < res["p_ge_if_plays"] < 1
    assert res["kalshi"]["ticker"] == "KXNBAPTS-26OCT08SACLAL-LALP1-20"
    assert res["kalshi"]["yes_ask"] == pytest.approx(0.45)
    other = _box(tmp_path).call(
        "explain_leg", {"player": "test player", "stat": "pts", "threshold": 12}
    )
    assert other["kalshi"] is None


def test_evaluate_slate_summary(tmp_path: Path) -> None:
    res = _box(tmp_path).call("evaluate_slate", {"top_n": 2})
    assert res["n_contracts_evaluated"] == 6 and res["n_flagged_positive_ev"] == 0
    assert res["no_positive_ev_found"] is True and len(res["top"]) == 2
    assert res["model_weight_zero"] is True


def test_track_record_insufficient_then_sufficient(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    r0 = tb.call("track_record", {"stat": "win"})
    assert (
        r0["insufficient_sample"]
        and "insufficient sample" in r0["message"]
        and r0["n_settled"] == 0
    )
    paper = duckdb.connect(str(tb.paths.paper_db))
    ensure_schema(paper)
    for i in range(40):
        log_shadow(
            paper,
            shadow_id=f"s{i}",
            ticker="t",
            side="yes",
            legs=[Leg("g", 0, "win", 0, "yes", HOME)],
            raw_model_prob=0.6,
            market_mid=0.5,
            price=0.5,
            engine="x",
        )
        paper.execute(
            "UPDATE shadow_predictions SET settled=TRUE, outcome=? WHERE shadow_id=?",
            [bool(i % 2), f"s{i}"],
        )
    paper.close()
    r1 = tb.call("track_record", {"stat": "win"})
    assert r1["n_settled"] == 40 and not r1["insufficient_sample"] and r1["brier_model"] > 0
    assert tb.call("track_record", {"stat": "pts"})["n_settled"] == 0


# ---------------------------------------------------------------- read-only guarantees
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_no_writes_to_nba_or_kalshi_dbs_and_paper_log_gated(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    before = {p: _sha(p) for p in (tb.paths.nba_db, tb.paths.kalshi_db)}
    for name, args in [
        ("list_slate", {}),
        ("evaluate_slate", {}),
        ("price_parlay", {"legs": [WIN, PTS]}),
        ("explain_leg", {"player": "Test Player", "stat": "pts", "threshold": 30}),
        ("what_if", {"legs": [WIN], "add": PTS}),
        ("track_record", {}),
    ]:
        assert "error" not in tb.call(name, args), name
    assert {p: _sha(p) for p in before} == before
    assert not tb.paths.paper_db.exists()  # nothing created without an explicit request
    assert "unknown tool" in tb.call("log_paper_trade", {"legs": [WIN], "confirm": True})["error"]
    # the attached catalogs reject writes
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{tb.paths.nba_db}' AS nba (READ_ONLY)")
    with pytest.raises(duckdb.Error):
        con.execute("INSERT INTO nba.games VALUES ('x','2026-01-01',2026,1,2,1,1)")
    con.close()


def test_paper_log_requires_flag_and_confirm(tmp_path: Path) -> None:
    tb = _box(tmp_path, allow_paper_log=True)
    assert "confirm" in tb.call("log_paper_trade", {"legs": [WIN], "confirm": False})["error"]
    assert (
        "single priced"
        in tb.call("log_paper_trade", {"legs": [WIN, PTS], "confirm": True})["error"]
    )
    res = tb.call("log_paper_trade", {"legs": [WIN], "confirm": True})
    assert res["logged"] and res["paper_only"]
    con = duckdb.connect(str(tb.paths.paper_db), read_only=True)
    assert con.execute("SELECT count(*) FROM paper_trades").fetchone() == (1,)
    con.close()
    assert tb.call("log_paper_trade", {"legs": [WIN], "confirm": True})["logged"]  # idempotent id
    con = duckdb.connect(str(tb.paths.paper_db), read_only=True)
    assert con.execute("SELECT count(*) FROM paper_trades").fetchone() == (1,)
    con.close()


# ---------------------------------------------------------------- numbers-only-from-engine
def test_numguard_strips_hallucinated_numbers() -> None:
    engine = collect_numbers({"model_prob": 0.4171, "ev_low": -0.0482, "n": 400, "thr": 30.0})
    out, n = check_prose("I think it is 62% likely.", engine)
    assert "62%" not in out and REMOVED in out and n == 1
    ok, n2 = check_prose("Model says 41.7%, ev_low -0.048, n=400, 30+ points, 3 legs.", engine)
    assert n2 == 0 and ok.startswith("Model says 41.7%")
    bad, n3 = check_prose(
        "EV is +0.37 and the price is $0.55 on KXNBAGAME-26OCT08SACLAL-LAL 2026-10-08", engine
    )
    assert n3 == 2 and "KXNBAGAME-26OCT08SACLAL-LAL" in bad and "2026-10-08" in bad
    reco, n4 = check_prose("Analysis follows. You should bet $50 on this.", engine)
    assert n4 >= 1 and "bet $50" not in reco


def test_end_to_end_fake_llm_strips_and_surfaces_verdict(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    llm = FakeLLM(
        [
            _call("price_parlay", legs=[WIN]),
            LLMReply("Uncertain: roughly a 62% chance, verdict no_positive_ev_found."),
        ]
    )
    tr = tmp_path / "t.jsonl"
    bot = Assistant(llm, tb, transcript=tr)
    ans = bot.ask("Price LAL to win")
    print("\n" + ans.text)
    assert "no_positive_ev_found" in ans.text
    assert "62%" not in ans.text and REMOVED in ans.text and ans.n_removed == 1
    assert "not financial advice" in ans.text
    for key in ("prob_interval", "ev_low"):  # rendered by Python from the tool result
        assert ans.runs[0].result[key] is not None
    assert "ev_low" in ans.text and "fees:" in ans.text and "interval" in ans.text
    kinds = [json.loads(line)["type"] for line in tr.read_text().splitlines()]
    assert kinds == ["user", "assistant", "tool", "assistant", "final"]
    # the model saw the tool result as a tool message
    assert llm.seen[1][-1]["role"] == "tool"


def test_prose_numbers_from_tool_results_survive(tmp_path: Path) -> None:
    tb = _box(tmp_path)
    res = tb.call("price_parlay", {"legs": [WIN]})
    lo = 100 * res["prob_interval"][0]
    p = f"Model probability {100 * res['model_prob']:.1f}% (interval low {lo:.1f}%)."
    llm = FakeLLM([_call("price_parlay", legs=[WIN]), LLMReply(p)])
    ans = Assistant(llm, tb).ask("price it")
    assert ans.n_removed == 0 and p in ans.text


def test_no_tool_call_means_no_numbers_shown(tmp_path: Path) -> None:
    ans = Assistant(FakeLLM([LLMReply("The edge is 12.5%.")]), _box(tmp_path)).ask("hi")
    assert "12.5%" not in ans.text and "no engine tool was called" in ans.text


def test_tool_error_is_rendered_not_raised(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _call("explain_leg", player="Nobody", stat="pts", threshold=20),
            LLMReply("Could not resolve."),
        ]
    )
    ans = Assistant(llm, _box(tmp_path)).ask("explain Nobody")
    assert "ERROR" in ans.text and "not guessing" in ans.text


# ---------------------------------------------------------------- Ollama backend
def _mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_down_gives_install_instructions() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    be = OllamaBackend("qwen2.5:3b", "http://localhost:11434", client=_mock(boom))
    for call in (be.ping, lambda: be.chat([], [])):
        with pytest.raises(OllamaUnavailable) as e:
            call()
        msg = str(e.value)
        assert "brew install ollama" in msg and "ollama serve" in msg
        assert "ollama pull qwen2.5:3b" in msg


def test_ollama_chat_request_and_tool_call_parsing() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "list_slate", "arguments": {"date": "2026-10-08"}}}
            ],
        }
        return httpx.Response(200, json={"message": msg})

    be = OllamaBackend("qwen2.5:3b", "localhost:11434", seed=7, client=_mock(handler))
    reply = be.chat([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert reply.tool_calls == [ToolCall("list_slate", {"date": "2026-10-08"})]
    b = seen["body"]
    assert b["options"] == {"temperature": 0, "seed": 7} and b["stream"] is False


def test_ollama_missing_model_message() -> None:
    be = OllamaBackend(
        "qwen2.5:3b", "http://h", client=_mock(lambda r: httpx.Response(200, json={"models": []}))
    )
    with pytest.raises(OllamaUnavailable, match="ollama pull qwen2.5:3b"):
        be.ping()


def test_cli_reports_ollama_down_with_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import nba.parlay.assistant.cli as cli

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    monkeypatch.setattr(
        cli, "OllamaBackend", lambda m, h, **kw: OllamaBackend(m, h, client=_mock(boom))
    )
    nba, k, model, paper = _build_dbs(tmp_path)
    ap = argparse.ArgumentParser()
    add_arguments(ap, ROOT)
    ns = ap.parse_args(
        ["--nba-db", str(nba), "--kalshi-db", str(k), "--paper-db", str(paper),
         "--joint-model", str(model), "--ask", "hello", "--date", "2026-10-08"]
    )  # fmt: skip
    monkeypatch.setattr(cli.NameResolver, "default", classmethod(lambda c: _resolver()))
    assert run(ns) == 2
    assert "brew install ollama" in capsys.readouterr().err


def test_cli_one_shot_with_fake_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import nba.parlay.assistant.cli as cli

    nba, k, model, paper = _build_dbs(tmp_path)
    ap = argparse.ArgumentParser()
    add_arguments(ap, ROOT)
    tr = tmp_path / "tr.jsonl"
    ns = ap.parse_args(
        ["--nba-db", str(nba), "--kalshi-db", str(k), "--paper-db", str(paper),
         "--joint-model", str(model), "--ask", "slate?", "--date", "2026-10-08",
         "--transcript", str(tr)]
    )  # fmt: skip
    monkeypatch.setattr(cli.NameResolver, "default", classmethod(lambda c: _resolver()))
    fake = FakeLLM([_call("list_slate"), LLMReply("One game on the slate.")])
    assert run(ns, fake) == 0
    out = capsys.readouterr().out
    assert "SAC@LAL" in out and tr.exists()
