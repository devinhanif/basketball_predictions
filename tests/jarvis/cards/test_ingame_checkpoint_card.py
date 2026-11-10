# ruff: noqa: E501
"""The in-game checkpoint block on the game card: silent edits only, calm when nothing is new,
no new alert condition, and the existing card phases behave exactly as before."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis import ingame_checkpoints as IC
from nba.jarvis.cards import run as R
from nba.jarvis.cards import text as T
from nba.jarvis.cards.embed import build_embed
from nba.jarvis.cards.model import CardData, CkOver
from nba.jarvis.cards.state import CardRecord
from nba.jarvis.config import JarvisConfig, load_config
from nba.jarvis.runner import Runtime, tick_line
from tests.jarvis.cards.test_run import Ids, at, make_rt, run, scoreboard
from tests.jarvis.conftest import G1, TIP1

ROOT = Path(__file__).resolve().parents[3]
TOP = {
    "player_name": "Ann Alpha",
    "stat": "pts",
    "k": 20,
    "p_model": 0.58,
    "p_glm_e": 0.52,
    "ask_c": 51,
    "bid_c": 49,
    "fee_c": 2,
}


def view(cp: int, known: datetime, **over: Any) -> dict[str, Any]:
    v = {
        "game": "LAL@LAC", "checkpoint": cp, "e_used": cp + 0.3, "period": 3, "clock": "8:12",
        "known_at": known, "support": "trained", "primary": cp >= 30, "n_units": 40, "n_flagged": 2,
        "top": [TOP],
    }  # fmt: skip
    return {**v, **over}


def test_the_shipped_config_is_off_and_valid() -> None:
    cfg = load_config(ROOT / "configs" / "jarvis.yaml")
    assert cfg.ingame_checkpoints["enabled"] is False  # flipping it on is the merge-time decision
    assert not IC.enabled(cfg)
    assert str(cfg.ingame_checkpoints["store"]).endswith("ingame_checkpoints.duckdb")


def test_enabled_needs_the_halftime_overs_artifact_settings(cfg: JarvisConfig) -> None:
    from dataclasses import replace

    bad = replace(
        cfg,
        ingame_checkpoints={"enabled": True, "store": "x.duckdb"},
        halftime_overs={"enabled": False},
    )
    assert not IC.enabled(bad)


def test_score_checkpoints_is_a_no_op_when_off(cfg: JarvisConfig) -> None:
    out = IC.score_checkpoints(
        cfg, datetime(2026, 11, 9).date(), datetime(2026, 11, 9, 20), ["LAL@LAC"], dry_run=True
    )
    assert out["ok"] is True and out["latest"] == {} and "skipped" in out


# ------------------------------------------------------------------------------ the block


def test_build_views_picks_the_newest_checkpoint_and_marks_the_late_family() -> None:
    known = datetime(2026, 11, 9, 1, 0)
    row = {
        "checkpoint": 30,
        "e_used": 30.4,
        "period": 3,
        "clock": "5:20",
        "known_at": known,
        "model_support": "trained",
        "game": "LAL@LAC",
        "player_name": "Ann Alpha",
        "stat": "pts",
        "k": 20,
        "edge_model": 0.08,
        "p_model": 0.6,
        "p_glm_e": 0.5,
        "ask_c": 50,
        "bid_c": 48,
        "fee_c": 2,
    }
    weak = {**row, "player_name": "Bob Beta", "edge_model": 0.01, "k": 15}
    out = IC.build_views({"LAL@LAC": [row, weak]}, 0.05, 3, 1, 10)
    v = out["LAL@LAC"]
    assert (
        v["checkpoint"] == 30 and v["primary"] is True and v["n_units"] == 2 and v["n_flagged"] == 1
    )
    assert [t["player_name"] for t in v["top"]] == ["Ann Alpha"]
    early = IC.build_views({"LAL@LAC": [{**row, "checkpoint": 10}]}, 0.05, 3, 1, 10)["LAL@LAC"]
    assert early["primary"] is False


def test_the_block_says_unproven_no_baseline_and_no_edge_promise() -> None:
    v = R.ck_views({"latest": {"LAL@LAC": view(35, datetime(2026, 11, 9, 1))}})["LAL@LAC"]
    assert v.primary and not v.extrapolated and isinstance(v.top[0], CkOver)
    card = CardData("g", "LAL", "LAC", None, "live", datetime(2026, 11, 9, 1), checkpoint=v)
    name, value = T.checkpoint_field(card, T.Style()) or ("", "")
    assert "35 game-min" in name and "Q3 8:12" in name
    assert "UNPROVEN: no live record; most nights the market already knows this." in value
    assert "model 58% vs ask 51c, bid 49c + 2c fee" in value
    assert "glm" not in value.lower() and "baseline" not in value.lower()
    assert "edge is expected" not in value and "no stake" in value
    early = R.ck_views(
        {"latest": {"LAL@LAC": view(10, datetime(2026, 11, 9, 1), support="extrapolated")}}
    )["LAL@LAC"]
    c2 = CardData("g", "LAL", "LAC", None, "live", datetime(2026, 11, 9, 1), checkpoint=early)
    v2 = (T.checkpoint_field(c2, T.Style()) or ("", ""))[1]
    assert "descriptive only" in v2 and "outside the minutes it was trained on" in v2


@pytest.mark.parametrize("phase", ["halftime", "final"])
def test_halftime_and_final_show_no_old_prices(phase: str) -> None:
    v = R.ck_views({"latest": {"LAL@LAC": view(35, datetime(2026, 11, 9, 1))}})["LAL@LAC"]
    card = CardData("g", "LAL", "LAC", None, phase, datetime(2026, 11, 9, 1), checkpoint=v)
    name, value = T.checkpoint_field(card, T.Style()) or ("", "")
    assert (
        "35 game-min" in name
        and "not current" in value
        and "ask" not in value
        and "model 58%" not in value
    )


def test_no_pretip_block_and_no_block_without_a_checkpoint() -> None:
    v = R.ck_views({"latest": {"LAL@LAC": view(35, datetime(2026, 11, 9, 1))}})["LAL@LAC"]
    pre = CardData("g", "LAL", "LAC", None, "pretip", datetime(2026, 11, 9, 1), checkpoint=v)
    assert T.checkpoint_field(pre, T.Style()) is None
    live = CardData("g", "LAL", "LAC", None, "live", datetime(2026, 11, 9, 1))
    assert T.checkpoint_field(live, T.Style()) is None
    names = [f["name"] for f in build_embed(live, "LAL", T.Style())["fields"]]
    assert not any("In-game model check" in n for n in names)


def test_a_malformed_summary_costs_that_games_block_only() -> None:
    out = R.ck_views(
        {"latest": {"A@B": {"checkpoint": "x"}, "LAL@LAC": view(30, datetime(2026, 11, 9, 1))}}
    )
    assert list(out) == ["LAL@LAC"] and R.ck_views(None) == {} and R.ck_views({"ok": False}) == {}


def test_the_hash_ignores_the_as_of_time_but_not_a_new_checkpoint() -> None:
    from nba.jarvis.cards.embed import content_hash

    k = datetime(2026, 11, 9, 1)
    a = R.ck_views({"latest": {"G": view(30, k)}})["G"]
    b = R.ck_views({"latest": {"G": view(35, k)}})["G"]
    base = dict(game_id="g", away="LAL", home="LAC", tip=None, phase="live")
    e1 = build_embed(CardData(**base, as_of=k, checkpoint=a), "LAL", T.Style())
    e2 = build_embed(
        CardData(**base, as_of=k + timedelta(minutes=3), checkpoint=a), "LAL", T.Style()
    )
    e3 = build_embed(CardData(**base, as_of=k, checkpoint=b), "LAL", T.Style())
    assert content_hash(e1) == content_hash(e2) != content_hash(e3)


# ------------------------------------------------------------------------------ edits are calm and silent


def rec(phase: str, h: str = "h1") -> CardRecord:
    return CardRecord("2026-11-09", "g", "LAL", "m1", phase, h, "fp", "post", "2026-11-09T01:00:00")


@pytest.mark.parametrize("phase", ["pretip", "halftime", "final"])
@pytest.mark.parametrize("same_hash", [True, False])
def test_existing_phases_are_unchanged_by_the_checkpoint_flag(phase: str, same_hash: bool) -> None:
    now = datetime(2026, 11, 9, 2)
    h = "h1" if same_hash else "h2"
    assert R.need_edit(rec(phase), phase, h, now, 15.0) == R.need_edit(
        rec(phase), phase, h, now, 15.0, True
    )


def test_a_live_card_edits_only_with_a_checkpoint_block_and_only_when_content_changed() -> None:
    now = datetime(2026, 11, 9, 2)
    assert R.need_edit(rec("live"), "live", "h2", now, 15.0) is False  # calm, as before
    assert R.need_edit(rec("live"), "live", "h2", now, 15.0, True) is True
    assert R.need_edit(rec("live"), "live", "h1", now, 15.0, True) is False


def test_a_live_game_card_is_edited_once_per_new_checkpoint_silently(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)  # pre-tip posts
    n_post = len(http.of("POST"))
    t1 = TIP1 + timedelta(minutes=50)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 60, 58, t1)
    rt.ck_scored = {"latest": {"LAL@LAC": view(30, t1 - timedelta(seconds=30))}}
    assert run(at(rt, t1))["cards"]["edited"] == 2  # both team channels, same message
    assert (
        run(at(rt, t1 + timedelta(minutes=1)))["cards"].get("edited", 0) == 0
    )  # nothing new: calm
    rt.ck_scored = {"latest": {"LAL@LAC": view(35, t1 + timedelta(minutes=1, seconds=20))}}
    assert run(at(rt, t1 + timedelta(minutes=2)))["cards"]["edited"] == 2
    assert run(at(rt, t1 + timedelta(minutes=3)))["cards"].get("edited", 0) == 0
    assert len(http.of("POST")) == n_post  # never a second card
    live_edits = [
        p for p in http.of("PATCH") if "In-game model check" in str(p[2]["embeds"][0]["fields"])
    ]
    assert len(live_edits) == 4
    for _m, _u, body in live_edits:
        assert "content" not in body and body["allowed_mentions"] == {
            "parse": []
        }  # an edit never pings
    names = [f["name"] for f in live_edits[-1][2]["embeds"][0]["fields"]]
    assert any("35 game-min" in n for n in names)


def test_with_no_checkpoint_summary_a_live_card_stays_calm(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    t1 = TIP1 + timedelta(minutes=50)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 60, 58, t1)
    assert run(at(rt, t1))["cards"].get("edited", 0) == 0


def test_the_halftime_and_final_cards_keep_the_last_checkpoint_block(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    t1 = TIP1 + timedelta(minutes=50)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 60, 58, t1)
    rt.ck_scored = {"latest": {"LAL@LAC": view(45, t1)}}
    run(at(rt, t1))
    t_fin = TIP1 + timedelta(minutes=150)
    scoreboard(rt.cfg.root, "STATUS_FINAL", 110, 104, t_fin)
    assert run(at(rt, t_fin))["cards"]["edited"] == 2
    fin = http.of("PATCH")[-1][2]["embeds"][0]
    assert any("In-game model check, 45 game-min" in f["name"] for f in fin["fields"])
    assert G1  # the fixture game


# ------------------------------------------------------------------------------ no new alert condition


@pytest.mark.parametrize(
    "rel",
    [
        "nba/jarvis/ingame_checkpoints.py",
        "nba/markets/checkpoint_scorer.py",
        "nba/markets/checkpoint_store.py",
    ],
)
def test_the_new_modules_have_no_delivery_alert_or_network_code(rel: str) -> None:
    tree = ast.parse((ROOT / rel).read_text())
    ids = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
        n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    }
    for word in ("_deliver", "note_alert", "post_main", "supports_alert", "make_sinks", "imessage"):
        assert word not in ids and word not in imports, (rel, word)
    for mod in ("httpx", "requests", "urllib", "socket", "http"):
        assert not any(i == mod or i.startswith(mod + ".") for i in imports), (rel, mod)
    assert "order" not in {i.split(".")[-1] for i in imports}


def test_the_tick_hook_only_scores_and_hands_the_summary_to_the_cards() -> None:
    src = (ROOT / "nba/jarvis/runner.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_checkpoints"
    )
    called = {
        c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
    }
    assert not called & {"_deliver", "send", "note_alert", "kind_sent", "log_quotes", "post"}, (
        called
    )
    tick = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_tick")
    # the alert loop is untouched: the checkpoint hook sits after it and before the cards pass
    names = [ast.unparse(s)[:60] for s in tick.body]
    i_alert = next(i for i, s in enumerate(names) if s.startswith("for p in plays"))
    i_ck = next(i for i, s in enumerate(names) if "ingame_checkpoints" in s)
    i_cards = next(i for i, s in enumerate(names) if "rep['cards']" in s)
    assert i_alert < i_ck < i_cards


def test_a_failing_checkpoint_scorer_never_breaks_the_tick_line() -> None:
    line = tick_line(
        {"at": "t", "plays": 0, "alerts": [], "ingame_checkpoints": {"ok": False, "error": "Boom"}}
    )
    assert "ck_error=Boom" in line
    ok = tick_line(
        {"at": "t", "plays": 0, "alerts": [], "ingame_checkpoints": {"ok": True, "stored": 40}}
    )
    assert "ck_scored=40rows" in ok


def test_a_failed_live_checkpoint_edit_never_reposts(rt: Runtime, tmp_path: Path) -> None:
    from tests.jarvis.cards.test_webhooks_and_client import Resp

    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    n_post = len(http.of("POST"))
    t1 = TIP1 + timedelta(minutes=50)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 60, 58, t1)
    rt.ck_scored = {"latest": {"LAL@LAC": view(30, t1 - timedelta(seconds=30))}}
    http.script = [Resp(404, {"code": 10008}), Resp(404, {"code": 10008})]
    rep = run(at(rt, t1))
    assert len(http.of("POST")) == n_post  # no new (possibly notifying) message
    assert rep["cards"].get("reposted", 0) == 0
