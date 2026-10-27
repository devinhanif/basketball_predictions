# ruff: noqa: E501
"""The halftime overs line: selection, wording (UNPROVEN, Yes only, no stake), gating, fail-soft wiring."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis import halftime_overs as HO
from nba.jarvis.config import JarvisConfig, load_config
from nba.jarvis.runner import Runtime, run_slate, run_tick
from tests.jarvis.conftest import SLATE, TIP1, Sent
from tests.jarvis.test_features import live_files

CLOCK = TIP1 + timedelta(minutes=70)


def r(
    player: str, stat: str, k: int, ask: int, fee: int, p: float, game: str = "LAL@LAC"
) -> dict[str, Any]:
    return {
        "game": game, "player_name": player, "stat": stat, "k": k, "ask_c": ask, "fee_c": fee,
        "p_model": p, "edge_model": p - ask / 100 - fee / 100,
    }  # fmt: skip


def test_top_overs_best_strike_per_player_stat_threshold_and_max() -> None:
    rows = [
        r("A", "pts", 15, 44, 2, 0.58),  # edge 0.12
        r("A", "pts", 20, 20, 2, 0.30),  # edge 0.08: same unit, smaller edge -> dropped
        r("B", "reb", 6, 30, 2, 0.45),  # 0.13
        r("C", "ast", 4, 50, 2, 0.60),  # 0.08
        r("D", "fg3m", 2, 35, 2, 0.44),  # 0.07
        r("E", "pts", 10, 40, 2, 0.46),  # 0.04: below 0.05
        r("F", "pts", 10, 40, 2, 0.99, game="BOS@NYK"),  # other game
    ]
    got = HO.top_overs(rows, "LAL@LAC", 0.05, 3)
    assert [(x["player_name"], x["k"]) for x in got] == [("B", 6), ("A", 15), ("C", 4)]
    assert HO.top_overs(rows, "LAL@LAC", 0.05, 1)[0]["player_name"] == "B"
    assert HO.top_overs(rows, "LAL@LAC", 0.50, 3) == []


def test_line_text_is_labelled_yes_only_and_has_no_stake() -> None:
    lines = HO.overs_lines("LAL@LAC", [r("Ann Alpha", "pts", 15, 44, 2, 0.58)])
    txt = "\n".join(lines)
    assert "UNPROVEN" in txt and "paper" in txt
    assert "model 58% vs ask 44c + 2c fee" in txt and "15+ pts (Yes)" in txt
    low = txt.lower()
    for word in ("$", "stake of", "bet ", "buy", "under", " no)", "kelly", "contracts"):
        assert word not in low, word
    assert "no stake suggested" in low
    assert HO.overs_lines("LAL@LAC", []) == []


def test_disabled_or_no_games_is_a_silent_no_op(cfg: JarvisConfig) -> None:
    off = replace(cfg, halftime_overs={"enabled": False})
    assert HO.score_halftimes(off, SLATE, CLOCK, ["LAL@LAC"], dry_run=True)["skipped"]
    assert HO.score_halftimes(cfg, SLATE, CLOCK, [], dry_run=True)["skipped"]


def test_missing_artifact_never_raises(cfg: JarvisConfig) -> None:
    res = HO.score_halftimes(cfg, SLATE, CLOCK, ["LAL@LAC"], dry_run=False)
    assert res["ok"] is False and res["error"] == "artifact missing"


def test_scorer_failure_is_fail_soft(cfg: JarvisConfig, tmp_path: Path) -> None:
    bad = tmp_path / "artifact.json"
    bad.write_text("{not json")
    c2 = replace(cfg, halftime_overs={**cfg.halftime_overs, "artifact": str(bad)})
    res = HO.score_halftimes(c2, SLATE, CLOCK, ["LAL@LAC"], dry_run=True)
    assert res["ok"] is False and "error" in res


def test_config_shipped_state_is_paper_strict_and_valid() -> None:
    c = load_config()
    ho = c.halftime_overs
    assert ho["min_edge"] == 0.05 and ho["top_n"] <= 3 and ho["post_without_plays"] is False
    assert (
        ho["recency_min_game_date"] == "2026-10-01"
    )  # holdout-season rows are not read as features by default
    assert ho["fee_pin"] == {"formula": "p_one_minus_p", "coefficient": 0.07, "multiplier": 1.0}
    assert (
        c.fees["kalshi"].verified and c.fees["kalshi"].coefficient == ho["fee_pin"]["coefficient"]
    )


@pytest.mark.parametrize("bad", [{"top_n": 4}, {"min_edge": 0.0}, {"fee_pin": {}}])
def test_config_validation(tmp_path: Path, bad: dict[str, Any]) -> None:
    import yaml

    raw = yaml.safe_load(Path("configs/jarvis.yaml").read_text())
    raw["halftime_overs"].update(bad)
    p = tmp_path / "j.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_config(p)


def _tick(
    cfg: JarvisConfig,
    rt: Runtime,
    sent: Sent,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    with_plays: bool = True,
) -> list[str]:
    run_slate(rt, SLATE)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: CLOCK
    monkeypatch.setattr(
        HO, "score_halftimes", lambda *a, **k: {"ok": True, "rows": rows, "stored": len(rows)}
    )
    if not with_plays:
        monkeypatch.setattr("nba.jarvis.runner.todays_rows", lambda *a, **k: [])
    run_tick(rt, SLATE)
    return [t for t in sent.texts if "halftime overs" in t]


def test_followup_posts_once_for_a_game_with_a_pick(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [r("Ann Alpha", "pts", 15, 44, 2, 0.58), r("Bo Beta", "reb", 6, 30, 2, 0.45)]
    out = _tick(cfg, rt, sent, monkeypatch, rows)
    assert (
        len(out) == 1
        and "UNPROVEN" in out[0]
        and "Ann Alpha 15+ pts (Yes): model 58% vs ask 44c + 2c fee" in out[0]
    )
    run_tick(rt, SLATE)  # same halftime again: deduped
    assert len([t for t in sent.texts if "halftime overs" in t]) == 1
    assert any("halftime check-in" in t for t in sent.texts)  # the existing check-in is untouched


def test_nothing_posts_below_the_edge_threshold(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _tick(cfg, rt, sent, monkeypatch, [r("A", "pts", 15, 44, 2, 0.50)]) == []


def test_no_follow_up_for_a_game_without_plays_by_default(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _tick(cfg, rt, sent, monkeypatch, [r("A", "pts", 15, 40, 2, 0.70)], with_plays=False) == []
    )


def test_follow_up_for_every_game_when_configured(
    cfg: JarvisConfig, kb: Any, sent: Sent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.jarvis.book import PaperBook
    from tests.jarvis.conftest import StubModel

    c2 = replace(cfg, halftime_overs={**cfg.halftime_overs, "post_without_plays": True})
    rt2 = Runtime(cfg=c2, book=PaperBook(tmp_path / "j2" / "book.duckdb"), kb=kb, backend=None, dry_run=False, sender=sent,
                  clock=lambda: CLOCK, out=lambda s: None, use_polymarket=False, model_factory=lambda d: StubModel())  # fmt: skip
    out = _tick(c2, rt2, sent, monkeypatch, [r("A", "pts", 15, 40, 2, 0.70)], with_plays=False)
    assert len(out) == 1


def test_scorer_exception_in_the_tick_is_contained(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_slate(rt, SLATE)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: CLOCK

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr(HO, "score_halftimes", boom)
    run_tick(rt, SLATE)
    assert any("halftime check-in" in t for t in sent.texts)  # the existing check-in still went out


def test_dry_run_never_writes_the_store(cfg: JarvisConfig, tmp_path: Path) -> None:
    c2 = replace(cfg, halftime_overs={**cfg.halftime_overs, "artifact": "missing.json"})
    HO.score_halftimes(c2, SLATE, CLOCK, ["LAL@LAC"], dry_run=True)
    assert not (cfg.root / cfg.halftime_overs["store"]).exists()
