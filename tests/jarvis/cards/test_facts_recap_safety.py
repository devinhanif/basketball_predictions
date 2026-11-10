"""Halftime verdicts follow the scorer's own rule; settlement and the season record; the append-only
files; and the structural guarantees (read-only, no execution code, no secret, delivery only)."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from datetime import date, datetime
from itertools import product
from pathlib import Path
from typing import Any

import pytest
import yaml

from nba.jarvis import halftime_overs as HO
from nba.jarvis.book import Outcome
from nba.jarvis.cards import facts as F
from nba.jarvis.cards import recap as RC
from nba.jarvis.cards.model import OverRow
from nba.jarvis.cards.state import CardState
from nba.jarvis.config import JarvisConfig

HO_SET = {
    "enabled": True, "min_edge": 0.05, "top_n": 3, "display_min_bid_c": 1,
    "display_max_spread_c": 10, "artifact_digest": "x",
}  # fmt: skip


def over(**kw: Any) -> OverRow:
    base: dict[str, Any] = dict(
        pick_id="p1", player="LeBron James", stat="pts", line=25.0, ask=0.44, fee_c=2.0,
        fair=0.60, pin=0.62, model=0.58, edge_c=14.0, ref_age_min=12.0, label="strong",
        ticker="T1", venue="kalshi", player_id=2544,
    )  # fmt: skip
    base.update(kw)
    return OverRow(**base)


def row(name: str = "LeBron James", k: int = 25, ask: int = 52, bid: int | None = 48,
        edge: float = 0.07, cur: int | None = 14, stat: str = "pts") -> dict[str, Any]:  # fmt: skip
    return {
        "game": "LAL@LAC",
        "player_name": name,
        "stat": stat,
        "k": k,
        "ask_c": ask,
        "bid_c": bid,
        "fee_c": 2,
        "p_model": 0.61,
        "edge_model": edge,
        "cur": cur,
    }


def cfg_ho(cfg: JarvisConfig) -> JarvisConfig:
    return replace(cfg, halftime_overs=dict(HO_SET))


def test_enter_and_skip_in_plain_words_from_the_scorers_own_rule(cfg: JarvisConfig) -> None:
    c = cfg_ho(cfg)
    rows = [
        row(),
        row("Austin Reaves", 20, edge=0.03),
        row("Rui Hachimura", 15, ask=40, bid=20, edge=0.08),
        row("Jaxson Hayes", 8, bid=None, edge=0.08),
    ]
    picks = [
        over(),
        over(pick_id="p2", player="Austin Reaves", line=20.0),
        over(pick_id="p3", player="Rui Hachimura", line=15.0),
        over(pick_id="p4", player="Jaxson Hayes", line=8.0),
        over(pick_id="p5", player="Nobody Here", line=10.0),
    ]
    legs = F.half_legs(c, picks, rows, "LAL@LAC", (), limit=10)
    by = {leg.player: leg for leg in legs}
    assert by["LeBron James"].verdict == "enter" and "+7c after the fee" in by["LeBron James"].why
    assert by["Austin Reaves"].verdict == "skip" and "under the 5c bar" in by["Austin Reaves"].why
    assert "20c wide (bid 20c, ask 40c)" in by["Rui Hachimura"].why
    assert "nobody is bidding" in by["Jaxson Hayes"].why
    assert by["Nobody Here"].verdict == "skip" and by["Nobody Here"].ask_c is None
    assert "no live price was scored" in by["Nobody Here"].why
    assert by["LeBron James"].cur == 14 and by["LeBron James"].edge_c == pytest.approx(7.0)


def test_the_verdict_is_exactly_the_scorers_display_rule_over_a_grid(cfg: JarvisConfig) -> None:
    """enter  <=>  the row would be listed by halftime_overs.top_overs. The card adds no rule."""
    c = cfg_ho(cfg)
    for edge, ask, bid in product((0.0, 0.049, 0.05, 0.06, 0.2), (30, 52), (None, 0, 1, 25, 48)):
        r = row(edge=edge, ask=ask, bid=bid)
        listed = bool(HO.top_overs([r], "LAL@LAC", 0.05, 3, 1, 10))
        leg = F.half_legs(c, [over()], [r], "LAL@LAC", ())[0]
        assert (leg.verdict == "enter") is listed, (edge, ask, bid)


def test_scorer_picks_that_are_not_pre_tip_overs_are_added_and_marked(cfg: JarvisConfig) -> None:
    c = cfg_ho(cfg)
    legs = F.half_legs(c, [over()], [row(), row("Other Guy", 9, edge=0.09)], "LAL@LAC", ())
    assert [leg.player for leg in legs] == ["LeBron James", "Other Guy"]
    assert [leg.from_pick for leg in legs] == [True, False]


def test_with_the_scorer_off_a_leg_says_so_and_never_invents_a_price(cfg: JarvisConfig) -> None:
    legs = F.half_legs(cfg, [over()], [row()], "LAL@LAC", ())
    assert legs[0].verdict == "skip" and legs[0].ask_c is None
    assert "scorer is off" in legs[0].why


def test_box_score_name_match_is_exact_and_never_guessed() -> None:
    from nba.jarvis.ingame import PlayerLine

    def pl(name: str, team: str, pts: int) -> PlayerLine:
        return PlayerLine("1", name, team, True, True, "F", 20, pts, 0, 0, 0, 0)

    box = (
        pl("LeBron James", "LAL", 14),
        pl("Marcus Smith", "LAL", 3),
        pl("Marcus Smith", "LAC", 9),
    )
    assert F.box_cur(box, "LeBron James", "pts", None) == 14
    assert F.box_cur(box, "Lebron  James", "pts", None) == 14  # case / spacing normalised
    assert F.box_cur(box, "Marcus Smith", "pts", None) is None  # two Smiths: never guessed
    assert F.box_cur(box, "Marcus Smith", "pts", "LAC") == 9
    assert F.box_cur(box, "L. James", "pts", None) is None


def test_over_name_comes_from_the_venue_title_before_the_colon() -> None:
    assert F.player_name("LeBron James: 25+ points", "x") == "LeBron James"
    assert F.player_name("", "player 7") == "player 7"


# ------------------------------------------------------------------------------ state + recap


def state(tmp: Path) -> CardState:
    return CardState(tmp / "s.jsonl", tmp / "p.jsonl")


def pick(pid: str = "p1", game: str = "G1", d: str = "2026-10-28", ask: float = 0.44,
         edge: float = 14.0, tip: str = "2026-10-29 00:30:00") -> dict[str, Any]:  # fmt: skip
    return {
        "pick_id": pid, "slate": d, "game_id": game, "matchup": "LAL@LAC", "tip": tip,
        "ticker": "T" + pid, "venue": "kalshi", "player": "A B", "player_id": 5, "stat": "pts",
        "line": 20.0, "ask": ask, "fee_c": 2.0, "fair": 0.6, "pin": 0.6, "model": 0.58,
        "edge_c": edge, "ref_age_min": 3.0, "label": "strong", "shown_at": "x",
        "includes_p_plays": False,
    }  # fmt: skip


class FakeSource:
    def __init__(self, outcomes: dict[str, Outcome]) -> None:
        self.outcomes = outcomes

    def of(self, r: dict[str, Any]) -> Outcome:
        return self.outcomes.get(r["ticker"], Outcome(None, "unresolved"))


def test_a_pick_is_kept_at_the_price_first_shown_and_a_torn_line_is_skipped(tmp_path: Path) -> None:
    s = state(tmp_path)
    assert s.add_pick(pick(ask=0.44)) is True
    assert s.add_pick(pick(ask=0.60)) is False  # a later price never replaces the first
    with s.picks_path.open("a") as fh:
        fh.write('{"rec": "pick", "pick_id": "torn')  # a process killed mid-write
    got = s.picks()
    assert len(got) == 1 and got[0]["ask"] == 0.44


def test_units_are_one_unit_at_risk(tmp_path: Path) -> None:
    assert RC.units_for(0.44, 2.0, False) == -1.0
    assert RC.units_for(0.44, 2.0, True) == pytest.approx((1 - 0.46) / 0.46)


def test_a_void_waits_until_the_box_score_is_loaded(
    cfg: JarvisConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final score without its player rows is not proof he did not play (seen on real data)."""
    s = state(tmp_path)
    s.add_pick(pick("c"))
    src = FakeSource({"Tc": Outcome(None, "void")})
    monkeypatch.setattr(RC, "box_loaded", lambda cfg, gid: False)
    now = datetime(2026, 10, 29, 6, 0)
    assert RC.settle_pending(cfg, s, now, source=src) == 0  # type: ignore[arg-type]
    monkeypatch.setattr(RC, "box_loaded", lambda cfg, gid: True)
    assert RC.settle_pending(cfg, s, now, source=src) == 1  # type: ignore[arg-type]


def test_settlement_waits_for_a_result_voids_a_dnp_and_is_idempotent(
    cfg: JarvisConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RC, "box_loaded", lambda cfg, gid: True)
    s = state(tmp_path)
    for pid in ("a", "b", "c", "d"):
        s.add_pick(pick(pid))
    src = FakeSource({"Ta": Outcome(True, "settled"), "Tb": Outcome(False, "settled"),
                      "Tc": Outcome(None, "void")})  # fmt: skip
    now = datetime(2026, 10, 29, 6, 0)
    assert RC.settle_pending(cfg, s, now, source=src) == 3  # type: ignore[arg-type]
    assert RC.settle_pending(cfg, s, now, source=src) == 0  # idempotent
    rec = RC.recap_for_game(cfg, s, "G1")
    assert (rec.n_hit, rec.n_miss, rec.n_void, rec.waiting) == (1, 1, 1, 1)
    assert rec.units == pytest.approx(RC.units_for(0.44, 2.0, True) - 1.0)
    assert rec.season_n == 2  # a void is not counted in n
    assert not hasattr(rec, "ci")  # no interval exists on this population


def test_a_game_that_has_not_tipped_is_never_settled(cfg: JarvisConfig, tmp_path: Path) -> None:
    s = state(tmp_path)
    s.add_pick(pick("a"))
    src = FakeSource({"Ta": Outcome(True, "settled")})
    early = datetime(2026, 10, 28, 20, 0)
    assert RC.settle_pending(cfg, s, early, source=src) == 0  # type: ignore[arg-type]


def test_the_season_line_counts_every_settled_over_across_dates(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    c = replace(cfg, min_settled=6, min_settled_dates=3)
    s = state(tmp_path)
    outcomes: dict[str, Outcome] = {}
    n = 0
    for day in range(3):
        for k in range(3):
            pid = f"{day}{k}"
            s.add_pick(pick(pid, game="G1" if day == 0 else f"G{day}", d=f"2026-10-{20 + day}",
                            tip=f"2026-10-{20 + day} 23:00:00"))  # fmt: skip
            outcomes["T" + pid] = Outcome(k != 0, "settled")
            n += 1
    RC.settle_pending(c, s, datetime(2026, 11, 1), source=FakeSource(outcomes))  # type: ignore[arg-type]
    rec = RC.recap_for_game(c, s, "G1")
    assert rec.season_n == n == 9 and rec.season_dates == 3
    assert rec.season_hit == 6 and rec.season_units != 0.0


def test_state_records_the_latest_per_game_and_team_and_survives_a_missing_file(
    tmp_path: Path,
) -> None:
    from nba.jarvis.cards.state import CardRecord

    s = state(tmp_path)
    assert s.latest(date(2026, 10, 28), "G1", "LAL") is None and s.picks() == []
    s.record(CardRecord("2026-10-28", "G1", "LAL", "m1", "pretip", "h1", "fp", "post", "t1"))
    s.record(CardRecord("2026-10-28", "G1", "LAL", "m1", "halftime", "h2", "fp", "edit", "t2"))
    s.record(CardRecord("2026-10-28", "G1", "LAC", "m9", "pretip", "h1", "fp", "post", "t1"))
    last = s.latest(date(2026, 10, 28), "G1", "LAL")
    assert last is not None and (last.phase, last.message_id) == ("halftime", "m1")
    lines = s.state_path.read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(ln) for ln in lines)  # append-only


# ------------------------------------------------------------------------------ structure

PKG = Path("nba/jarvis/cards")
FILES = sorted(PKG.glob("*.py"))


def src() -> str:
    return "\n".join(p.read_text() for p in FILES)


def test_no_order_portfolio_or_signing_word_anywhere_in_the_cards() -> None:
    text = src().lower()
    for word in (
        "/orders", "create_order", "place_order", "portfolio", "private_key", "api_key", "apikey",
        "authorization", "bearer ", "signature", "wallet", "submit_order", "cancel_order",
        "clob.polymarket", "kalshi-access",
    ):  # fmt: skip
        assert word not in text, word


def test_only_the_card_client_touches_the_network_and_only_post_and_patch_to_a_webhook() -> None:
    users = {
        p.name
        for p in FILES
        if re.search(r"^\s*(import|from) (httpx|requests|urllib|socket)", p.read_text(), re.M)
    }
    assert users == {"discord.py"}
    methods = set(re.findall(r'_send\("([A-Z]+)"', (PKG / "discord.py").read_text()))
    assert methods == {"POST", "PATCH"}
    for p in FILES:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in ("get", "put", "delete"):
                base = ast.unparse(node.value)
                assert not any(h in base for h in ("http", "client", "session")), (p.name, base)


def test_cards_are_delivery_only_they_import_no_pricing_model_truth_or_daily_code() -> None:
    forbidden = ("nba.jarvis.edge", "nba.models", "nba.truth", "nba.daily", "nba.parlay", "duckdb")
    for p in FILES:
        for node in ast.walk(ast.parse(p.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.startswith(forbidden):
                    assert (p.name, n) == ("preview.py", "duckdb"), (p.name, n)


def test_the_only_duckdb_use_is_a_read_only_read_of_the_scorers_table() -> None:
    for p in FILES:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "duckdb.connect":
                kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
                assert p.name == "preview.py" and kw.get("read_only") == "True"


def test_no_season_2025_and_no_ai_attribution_in_the_cards() -> None:
    text = src()
    assert not re.search(r"season\s*(=|==|IN|in)\s*\(?\s*2025", text)
    for w in ("co-authored", "generated by", "claude", "anthropic"):
        assert w not in text.lower(), w


def test_config_block_names_the_variable_but_holds_no_secret() -> None:
    cfg = yaml.safe_load(Path("configs/jarvis.yaml").read_text())["team_cards"]
    assert cfg["env_var"] == "JARVIS_DISCORD_TEAM_WEBHOOKS" and cfg["keychain_service"] == ""
    blob = json.dumps(cfg).lower()
    for w in ("password", "token", "secret", "api_key", "key:", "discord.com"):
        assert w not in blob, w
    assert 1 <= cfg["top_overs"] <= 3


def test_the_card_files_are_written_one_line_at_a_time_to_the_configured_jsonl_only() -> None:
    text = (PKG / "state.py").read_text()
    assert '.open("ab"' in text and ".write_text(" not in text and "unlink" not in text
    assert not any("open(" in line and '"w"' in line for line in text.splitlines())
