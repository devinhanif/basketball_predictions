from __future__ import annotations

from nba.jarvis.agent import Jarvis, JarvisToolbox, games_in_question
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.runner import Runtime
from nba.parlay.assistant.backend import LLMReply, ToolCall
from nba.parlay.assistant.names import NameResolver
from tests.jarvis.conftest import G1, G2, LEBRON, NOW, SLATE


class Script:
    """Scripted LLM: a list of replies, one per chat() call."""

    def __init__(self, *replies: LLMReply) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []  # type: ignore[type-arg]

    def chat(self, messages, tools):  # type: ignore[no-untyped-def]
        self.seen.append(list(messages))
        return self.replies.pop(0) if self.replies else LLMReply("")


def resolver() -> NameResolver:
    return NameResolver({"lebron james": [LEBRON]}, {LEBRON: "LeBron James"})


def bot(rt: Runtime, backend) -> Jarvis:  # type: ignore[no-untyped-def]
    j = Jarvis(rt, backend, JarvisToolbox(rt, resolver()))
    j.tb.reset(SLATE)
    return j


# ----------------------------------------------------------------------------- knowledge


def test_injuries_are_grouped_sanitised_and_dated(kb: Knowledge) -> None:
    inj = kb.injuries(G1)
    rows = [r for t in inj["teams"].values() for r in t]
    assert {r["status"] for r in rows} == {"out", "questionable"}
    out = next(r for r in rows if r["status"] == "out")
    assert "http" not in out["reason"] and len(out["reason"]) <= 48
    assert inj["report_as_of_utc"] and inj["report_age_min"] is not None
    assert "LAL" in inj["teams"]  # team resolved from 2026 box scores, not the 2025 row
    assert out["mpg"] == 30.0  # 2026 minutes only (the 2025 row is ignored)


def test_lineups_show_announced_starters_and_age(kb: Knowledge) -> None:
    lu = kb.lineups(G1)
    assert lu["starters"]["LAL"][0]["player"] == "LeBron James"
    assert lu["snapshot_age_min"] is not None
    assert "no lineup snapshot" in kb.lineups(G2)["note"]


def test_fan_knowledge_retrieves_by_player_team_or_topic(kb: Knowledge) -> None:
    r = kb.fan_knowledge("Kevin Durant Houston")
    assert r["excerpts"][0]["section"].startswith("Houston")
    r2 = kb.fan_knowledge("back to back minutes rest")
    assert r2["excerpts"][0]["section"] == "Minutes and rest"
    assert kb.fan_knowledge("zzzz qqqq")["excerpts"] == []
    assert "hypotheses" in r["note"]


def test_usage_pace_units_use_2026_only(kb: Knowledge) -> None:
    out = kb.usage_pace(team="LAL", player_id=LEBRON)
    assert out["team"] == "LAL" and out["team_games"] == 1  # the 2025 game is excluded
    assert out["team_possessions_per_game"] == 10.0
    assert out["top_units"][0]["minutes"] == 9.0  # (240 + 300) s = 9 min; 2025 stint ignored
    assert out["player_usage_share"] == 0.3 and out["player_possessions_used"] == 3
    assert out["player_minutes_per_game"] == 9.0 and out["player_games"] == 1


def test_team_tables_and_unknown_team(kb: Knowledge) -> None:
    assert kb.usage_pace(team="ZZZ").get("team") is None


# ------------------------------------------------------------------------------- agent


def test_ask_always_returns_a_play_block(rt: Runtime) -> None:
    j = bot(rt, Script(LLMReply("Lakers look fine at 50¢ tonight.")))
    ans = j.ask("who should I play tonight?", log=False)
    assert "PLAY:" in ans.text and ans.plays and "keep your money" not in ans.text.lower()


def test_give_up_prose_is_replaced_but_the_play_stays(rt: Runtime) -> None:
    j = bot(rt, Script(LLMReply("Honestly keep your money, there is no bet here.")))
    ans = j.ask("anything good on LAL@LAC?", log=False)
    assert "keep your money" not in ans.text.lower() and "PLAY:" in ans.text
    assert ans.prose_source.startswith("template")


def test_tools_feed_the_answer_and_numbers_are_checked(rt: Runtime) -> None:
    script = Script(
        LLMReply("", [ToolCall("injuries", {"game": "LAL@LAC"})]),
        LLMReply("Lakers shoot 97% from three tonight."),
    )
    j = bot(rt, script)
    ans = j.ask("what about the Lakers?", log=False)
    assert "97" not in ans.text and ans.prose_source.startswith("template")
    assert "injuries" in ans.tool_calls
    tool_msg = next(m for m in script.seen[1] if m.get("role") == "tool")
    assert (
        "evil.example" not in tool_msg["content"]
    )  # fetched text sanitised before the model sees it


def test_named_game_selects_that_games_play(rt: Runtime) -> None:
    j = bot(rt, None)
    ans = j.ask("what about the Thunder game, OKC?", log=False)
    assert ans.plays[0].game_id == G2
    ans2 = j.ask("how about LAL?", log=False)
    assert ans2.plays[0].game_id == G1


def test_unknown_game_tool_is_an_error_not_a_guess(rt: Runtime) -> None:
    tb = JarvisToolbox(rt, resolver())
    tb.reset(SLATE)
    assert "error" in tb.call("best_play", {"game": "ZZZ@YYY"})
    assert "error" in tb.call("nope", {})
    assert "error" in tb.call("fan_knowledge", {})  # missing required arg


def test_no_prices_still_does_not_say_keep_your_money(rt: Runtime) -> None:
    rt.clock = lambda: NOW.replace(hour=23, minute=55)  # quotes are hours old
    j = bot(rt, None)
    ans = j.ask("what should I bet?", log=False)
    assert "keep your money" not in ans.text.lower() and "no tradable price" in ans.text.lower()


def test_ask_logs_the_play_unless_told_not_to(rt: Runtime) -> None:
    j = bot(rt, None)
    j.ask("what should I bet?", log=False)
    assert not rt.book.path.exists()
    j.ask("what should I bet?")
    assert rt.book.spent_tonight(SLATE) > 0


def test_games_in_question_matches_abbr_and_nickname(rt: Runtime) -> None:
    from nba.jarvis.sources import load_games

    games = load_games(rt.cfg, SLATE)
    names = dict(rt.kb.names)
    assert [g.game_id for g in games_in_question("thunder tonight", games, names)] == [G2]
    assert [g.game_id for g in games_in_question("is DAL any good", games, names)] == [G2]
    assert games_in_question("who wins", games, names) == []
