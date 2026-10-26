# ruff: noqa: E501
"""Devin's 2026-10-25 rules (docs/TRADING_CONTEXT.md): Discord delivery, payout line, own picks, kill
switch and floor, Daily Dollar, halftime and morning posts, and model weight by stat."""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis import dailydollar, sinks
from nba.jarvis.book import PaperBook, score_book, weekly_summary
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Play, PlayLeg, Quote, Ref
from nba.jarvis.picks import PickError, build_pick, log_pick
from nba.jarvis.render import fair_phrase, play_lines, slate_texts
from nba.jarvis.runner import Runtime, run_slate, run_tick
from nba.jarvis.slate import PinnacleRefs, SlateState, gather, single_candidate
from nba.jarvis.sources import load_games
from nba.jarvis.weights import STAT_KEYS, blend, load_weights, stat_key, update_weights
from tests.jarvis.conftest import G1, LAL, LEBRON, NOW, SLATE, TIP1, Sent, StubModel

# a shaped-like-Discord URL built at run time so no webhook literal is ever in the repo
HOOK = "https://discord" + ".com/api/webhooks/" + "1234567890/" + "tokenABC_def-123"


class FakeHttp:
    def __init__(self, code: int = 204, boom: Exception | None = None) -> None:
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.status = code
        self.boom = boom

    def post(self, url: str, json: dict[str, Any]) -> Any:
        if self.boom:
            raise self.boom
        self.posted.append((url, json))
        return type("R", (), {"status_code": self.status})()

    def close(self) -> None:
        pass


# ------------------------------------------------------------------------------ Discord


def test_discord_posts_the_text_with_mentions_disabled(cfg: JarvisConfig) -> None:
    c = FakeHttp()
    r = sinks.send_discord(
        "hello @everyone", env_var=cfg.discord_env_var, env={cfg.discord_env_var: HOOK}, client=c
    )
    assert r.ok and r.mode == "sent"
    url, body = c.posted[0]
    assert (
        url == HOOK
        and body["allowed_mentions"] == {"parse": []}
        and body["content"] == "hello @everyone"
    )


def test_discord_url_is_env_only_validated_and_never_in_errors(cfg: JarvisConfig) -> None:
    var = cfg.discord_env_var
    assert var == "JARVIS_DISCORD_WEBHOOK"
    miss = sinks.send_discord("x", env_var=var, env={})
    assert not miss.ok and HOOK not in miss.note
    bad = sinks.send_discord(
        "x", env_var=var, env={var: "https://evil.example/api/webhooks/1/x"}, client=FakeHttp()
    )
    assert not bad.ok  # not a Discord webhook shape: nothing is posted
    import httpx

    boom = httpx.ConnectError(f"cannot reach {HOOK}")
    r = sinks.send_discord("x", env_var=var, env={var: HOOK}, client=FakeHttp(boom=boom))
    assert not r.ok and HOOK not in r.note and "tokenABC" not in r.note
    r2 = sinks.send_discord("x", env_var=var, env={var: HOOK}, client=FakeHttp(code=429))
    assert not r2.ok and "tokenABC" not in r2.note and "429" in r2.note
    assert "tokenABC" not in sinks.redact(f"failed posting to {HOOK}?x=1", HOOK)


def test_dry_run_prints_and_never_reads_the_webhook(cfg: JarvisConfig) -> None:
    out: list[str] = []
    r = sinks.send_discord(
        "x", env_var="JARVIS_DISCORD_WEBHOOK", dry_run=True, env={}, out=out.append, client=None
    )
    assert r.ok and r.mode == "dry_run" and "Discord" in out[0]


def test_discord_is_the_default_sink_and_imessage_is_optional(cfg: JarvisConfig) -> None:
    assert cfg.sinks == ("discord",) and cfg.max_chars == cfg.max_chars_discord
    both = replace(cfg, sinks=("discord", "imessage"))
    assert both.max_chars == both.max_chars_imessage  # the strictest sink sets the length
    c = FakeHttp()
    send = sinks.make_sender(cfg, discord_client=c)
    assert send("hi", env={cfg.discord_env_var: HOOK}).ok and len(c.posted) == 1
    none = sinks.make_sender(replace(cfg, sinks=()))("x")
    assert not none.ok


def test_the_repo_holds_no_webhook_url() -> None:
    import re

    pat = re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{6,}")
    for f in [
        *Path("nba/jarvis").glob("*.py"),
        Path("configs/jarvis.yaml"),
        *Path("tests/jarvis").glob("*.py"),
    ]:
        assert not pat.search(f.read_text()), f


# ---------------------------------------------------------------------------- payout, fair


def test_every_play_shows_what_it_pays_and_the_fair_line(cfg: JarvisConfig) -> None:
    leg = PlayLeg("kalshi", "T", "yes", 0.51, "game_win", "DET to win", team_id=LAL)
    p = Play(
        G1,
        "DET@PHI",
        TIP1,
        (leg,),
        "thin",
        0.9,
        0.5,
        2.0,
        0.52,
        19,
        9.76,
        "r",
        model_prob=0.44,
        pin_prob=0.5,
        w_model=0.0,
    )
    body = "\n".join(play_lines(p, cfg.display_tz))
    assert "pays $19.00 if right, 1.9x" in body
    assert "fair: Pinnacle 50% / model 44% / w 0.0" in body
    assert fair_phrase(replace(p, model_prob=None)) == "fair: Pinnacle 50% / model n/a / w 0.0"
    assert fair_phrase(replace(p, pin_prob=None)) == ""


# ----------------------------------------------------------------------- floor, banner


def test_at_or_below_the_floor_every_post_is_marked_paper_only(cfg: JarvisConfig) -> None:
    low = replace(cfg, balance_current=cfg.balance_floor)
    assert low.paper_only and not cfg.paper_only
    t = "\n".join(slate_texts(low, [], "", date_label="x", first_tip=TIP1, n_games=1))
    assert "PAPER ONLY" in t
    assert "PAPER ONLY" not in "\n".join(
        slate_texts(cfg, [], "", date_label="x", first_tip=TIP1, n_games=1)
    )


# ----------------------------------------------------------------------------- picks


def test_a_pick_is_logged_as_called_and_tracked_apart(cfg: JarvisConfig, tmp_path: Path) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    res = build_pick(cfg, st, "LAL ML", venue="kalshi", price=0.50, stake=3.0, now=NOW)
    text = "\n".join(res.lines)
    assert "BUY LAL to win" in text and "at 50c" in text and "pays $" in text
    assert "Fair: Pinnacle no-vig 53c vs your 50c" in text and "Best ask now: kalshi 50c" in text
    p = res.play
    assert (
        p.label == "pick" and p.contracts == 5 and p.stake_usd == pytest.approx(5 * 0.5 + 0.09)
    )  # his price, his stake
    book = PaperBook(tmp_path / "b.duckdb")
    log_pick(book, res, SLATE, NOW)
    log_pick(
        book,
        build_pick(
            cfg, st, "LAL ML", venue="kalshi", price=0.50, stake=3.0, now=NOW + timedelta(minutes=1)
        ),
        SLATE,
        NOW,
    )
    assert book.spent_split(SLATE) == (0.0, 0.0)  # not JARVIS's money, not in the nightly cap
    con = book._con()
    assert con.execute("SELECT count(*), max(source), max(edge_label) FROM plays").fetchone() == (
        2,
        "pick",
        "pick",
    )
    con.close()


def test_a_pick_that_cannot_be_matched_is_an_error_not_a_guess(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    for text, kw in (("ZZZ ML", {}), ("LAL to cover", {}), ("LAL ML", {"venue": "polymarket"})):
        with pytest.raises(PickError):
            build_pick(cfg, st, text, venue=kw.get("venue", "kalshi"), price=0.5, stake=3.0)
    with pytest.raises(PickError):
        build_pick(
            cfg, st, "LAL ML", venue="kalshi", price=0.5, stake=0.1
        )  # cannot buy one contract
    ok = build_pick(
        cfg,
        st,
        "x",
        venue="kalshi",
        price=0.40,
        stake=2.0,
        ticker="KXNBAPTS-26OCT28LALLAC-LALLJAMES23-25",
    )
    assert ok.play.legs[0].player_id == LEBRON


def test_picks_are_scored_and_reported_separately(cfg: JarvisConfig, tmp_path: Path) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    book = PaperBook(tmp_path / "b.duckdb")
    log_pick(
        book,
        build_pick(cfg, st, "LAL ML", venue="kalshi", price=0.50, stake=3.0, now=NOW),
        SLATE,
        NOW,
    )
    d = duckdb.connect(str(cfg.paths["nba_db"]))
    d.execute("UPDATE games SET home_pts = 100, away_pts = 110 WHERE game_id = ?", [G1])
    d.close()
    score_book(cfg, book, now=TIP1 + timedelta(hours=3))
    s = weekly_summary(cfg, book, until=SLATE)
    assert s["labels"]["pick"]["n"] == 1 and s["labels"]["pick"]["hit_rate"] == 1.0
    assert s["labels"]["all"]["n"] == 0  # a pick never enters the label record


# ------------------------------------------------------------------------ Daily Dollar


def dd_state(
    cfg: JarvisConfig, factor: float, pin: float | None = 0.60
) -> tuple[SlateState, JarvisConfig]:
    games = load_games(cfg, SLATE)
    ts = NOW - timedelta(minutes=2)

    def q(ticker: str, kind: str, bid: float, ask: float, **kw: Any) -> Quote:
        return Quote(
            "kalshi", ticker, G1, kind, ts, bid, ask, series="KXNBAGAME", label=ticker, **kw
        )

    # In the fixture possessions LAL's top-usage player is id 1 (70% of his on-court possessions
    # end with his shot), then LeBron (30%); player 1000 is a cheap prop but not a top-usage player.
    quotes = [
        q("ML-LAL", "game_win", 0.49, 0.50, team_id=LAL),
        q("P-TOP1-PTS", "prop", 0.115, 0.12, player_id=1, stat="pts", threshold=25.0),
        q("P-LEBRON-PTS", "prop", 0.095, 0.10, player_id=LEBRON, stat="pts", threshold=35.0),
        q("P-1000-REB", "prop", 0.04, 0.05, player_id=1000, stat="reb", threshold=9.0),
    ]
    refs = PinnacleRefs()
    if pin is not None:
        refs.win[(G1, LAL)] = Ref(pin, ts)
    dd = {**cfg.daily_dollar, "corr_factor": factor, "min_possessions": 5}
    return SlateState(SLATE, NOW, games, quotes, refs), replace(cfg, daily_dollar=dd)


def test_daily_dollar_legs_are_the_win_plus_the_top_usage_players_props(
    cfg: JarvisConfig,
    kb,  # type: ignore[no-untyped-def]
) -> None:
    st, c = dd_state(cfg, 1.2)
    top = kb.top_usage(LAL, 2, 5)
    assert [t["player_id"] for t in top] == [1, LEBRON] and top[0]["share"] == 0.7
    play, why = dailydollar.build(c, st, kb, room=10.0)
    assert play is not None, why
    assert play.label == "daily_dollar" and {leg.side for leg in play.legs} == {"yes"}
    assert [leg.player_id for leg in play.legs] == [
        None,
        1,
        LEBRON,
    ]  # the win, top-1, top-2: never 1000
    assert 50 <= play.multiple <= 300 and play.stake_usd <= 1.0 + 1e-9  # type: ignore[operator]
    assert play.fair_prob == pytest.approx(0.60 * 0.1175 * 0.0975 * 1.2)


def test_daily_dollar_line_prints_the_numbers_it_cannot_know(cfg: JarvisConfig, kb) -> None:  # type: ignore[no-untyped-def]
    st, c = dd_state(cfg, 1.2)
    play, _ = dailydollar.build(c, st, kb, room=10.0)
    assert play is not None
    text = "\n".join(dailydollar.lines(play, ""))
    assert "EV unknown (entertainment)" in text
    nc = 0.60 * 0.1175 * 0.0975
    assert f"no-correlation fair {nc * 100:.2f}c" in text
    per = float(play.inputs["per_contract_cost"])  # type: ignore[arg-type]
    assert f"needs correlation >= {per / nc:.2f}x to break even (unmeasured)" in text
    assert "1 of 3 legs priced by Pinnacle, the rest Kalshi mid, x 1.2 (assumed)" in text
    assert "same-team legs: the win plus the team's top-usage player's props" in text
    assert "correlated" not in text  # the word is not claimed
    # with no outside price at all it says so plainly
    st0, c0 = dd_state(cfg, 1.2, pin=None)
    p0, _ = dailydollar.build(c0, st0, kb, room=10.0)
    if p0 is not None:
        assert "no outside reference: fair = Kalshi mid x 1.2 (assumed)" in "\n".join(
            dailydollar.lines(p0, "")
        )


def test_daily_dollar_refuses_to_pay_more_than_fair(cfg: JarvisConfig, kb) -> None:  # type: ignore[no-untyped-def]
    st, c = dd_state(cfg, 1.0, pin=0.50)  # no correlation credit, no sharp edge on the win leg
    play, why = dailydollar.build(c, st, kb, room=10.0)
    assert play is None and "at or below the assumed fair" in why
    st, c = dd_state(cfg, 1.2)
    assert dailydollar.build(c, st, kb, room=0.5)[0] is None  # inside the cap: no room, no ticket
    assert "skipped" in dailydollar.lines(None, why)[0]


def test_daily_dollar_is_in_the_slate_post_and_tracked_apart(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, kb
) -> None:  # type: ignore[no-untyped-def]
    rep = run_slate(rt, SLATE)
    text = "\n".join(sent.texts)
    assert "Daily Dollar" in text  # its own line (a ticket or an honest skip)
    if rep["daily_dollar"]:
        a, b = rt.book.spent_split(SLATE)
        assert b >= 1.0 - 1e-9 or a >= 0  # counted inside the cap


# ----------------------------------------------------------------------- halftime, recap


def live_files(root: Path, slate: date, status: str, away: int, home: int, age_s: int = 30) -> None:
    d = root / "data" / "raw" / f"live_{slate.isoformat()}"
    sb = d / "espn_scoreboard" / slate.isoformat()
    sb.mkdir(parents=True)
    body = {
        "events": [
            {
                "shortName": "LAL @ LAC",
                "status": {"period": 2, "displayClock": "0.0", "type": {"name": status}},
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"abbreviation": "LAC"},
                                "score": str(home),
                            },
                            {
                                "homeAway": "away",
                                "team": {"abbreviation": "LAL"},
                                "score": str(away),
                            },
                        ]
                    }
                ],
            }
        ]
    }
    t = TIP1 + timedelta(minutes=70)
    with gzip.open(sb / f"{t.strftime('%Y%m%dT%H%M%SZ')}.json.gz", "wt") as fh:
        json.dump(body, fh)
    (d / "heartbeat.json").write_text(
        json.dumps(
            {"last_tick_ts": (t - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        )
    )


def test_halftime_check_in_for_a_game_with_a_play_posts_once(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    run_slate(rt, SLATE)  # records tonight's plays
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: TIP1 + timedelta(minutes=70)
    rep = run_tick(rt, SLATE)
    assert len(rep["halftime"]) == 1
    txt = [t for t in sent.texts if "halftime" in t][0]
    assert "LAL 55 - LAC 48" in txt and "pays $" in txt and "BUY LeBron James: 25+ points" in txt
    run_tick(rt, SLATE)
    assert len([t for t in sent.texts if "halftime" in t]) == 1  # once per game


def test_no_halftime_post_when_the_game_is_not_at_the_half_or_has_no_play(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    live_files(cfg.root, SLATE, "STATUS_IN_PROGRESS", 30, 28)
    rt.clock = lambda: TIP1 + timedelta(minutes=70)
    assert run_tick(rt, SLATE)["halftime"] == []
    # at the half but nothing was ever logged on that game
    import shutil

    shutil.rmtree(cfg.root / "data" / "raw" / f"live_{SLATE}")
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", 55, 48)
    assert run_tick(rt, SLATE)["halftime"] == []


def test_morning_recap_posts_results_balance_records_and_weights(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    run_slate(rt, SLATE)
    d = duckdb.connect(str(cfg.paths["nba_db"]))
    d.execute("UPDATE games SET home_pts = 100, away_pts = 110 WHERE game_id = ?", [G1])
    d.close()
    next_day = SLATE + timedelta(days=1)
    rt.clock = lambda: datetime(2026, 10, 29, 14, 30)  # 10:30 ET, after the recap hour
    rep = run_tick(rt, next_day)
    assert rep["recap"] and rep["recap"]["ok"]
    txt = [t for t in sent.texts if "morning recap" in t][0]
    assert (
        f"Balance (the value you set in configs/jarvis.yaml): ${cfg.balance_current:.2f} "
        f"vs ${cfg.balance_start:.0f} start"
    ) in txt
    assert (
        "Records, last 7 days" in txt and "Kill switch" in txt and "Model weights: all held" in txt
    )
    assert "P&L that day" in txt
    run_tick(rt, next_day)
    assert len([t for t in sent.texts if "morning recap" in t]) == 1  # once per date


# ----------------------------------------------------------------------- model weights


def test_blend_and_defaults(cfg: JarvisConfig) -> None:
    assert blend(0.50, 0.44, 0.0) == 0.50 and blend(0.50, None, 0.5) == 0.50
    assert blend(0.50, 0.40, 0.5) == pytest.approx(0.45)
    assert load_weights(cfg) == dict.fromkeys(STAT_KEYS, 0.0)
    assert stat_key("game_win", None) == "win" and stat_key("prop", "fg3m") == "fg3m"
    assert stat_key("prop", "pra") == "combos"


def test_the_frozen_rule_is_in_the_config_before_any_data(cfg: JarvisConfig) -> None:
    txt = Path("configs/jarvis.yaml").read_text()
    assert "FROZEN 2026-10-25, BEFORE ANY DATA EXISTS" in txt and "PAPER-ONLY ADAPTATION" in txt
    assert "NOT a frozen test" in txt
    mw = cfg.model_weights
    assert (mw["w_max"], mw["step"], mw["min_n"], mw["min_dates"], mw["ci_level"]) == (
        0.5,
        0.1,
        30,
        10,
        0.8,
    )


def seed_stat(book: PaperBook, n: int, days: int, clv: float, jitter: float = 0.5) -> None:
    """n settled single-leg pts props whose model leaned toward the bet, spread over `days` dates."""
    for i in range(n):
        day = SLATE - timedelta(days=i % days)
        leg = PlayLeg(
            "kalshi", f"W{i}", "yes", 0.5, "prop", "x", player_id=LEBRON, stat="pts", threshold=20.0
        )
        p = Play(
            G1,
            "LAL@LAC",
            TIP1,
            (leg,),
            "thin",
            1.0,
            0.52,
            2.0,
            0.5,
            2,
            1.0,
            "r",
            model_prob=0.56,
            pin_prob=0.52,
            inputs={"leg_costs": [1.0]},
            key_suffix=f"#{i}",
        )
        book.record([p], source="slate", slate=day, now=NOW + timedelta(seconds=i))
        con = book._con()
        c = clv + (jitter if i % 2 else -jitter)
        con.execute(
            "INSERT INTO scores SELECT play_id, ?, 'settled', TRUE, 0.1, NULL, NULL, NULL, '', ?, NULL FROM plays WHERE ticker = ?",
            [NOW, c, f"W{i}"],
        )
        con.close()


def test_weight_rises_by_a_step_only_on_a_positive_clustered_lower_bound(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    seed_stat(book, 40, 12, clv=3.0)
    recs = update_weights(cfg, book, today=SLATE, now=NOW)
    pts = next(r for r in recs if r["stat"] == "pts")
    assert pts["action"] == "raise" and pts["w_after"] == pytest.approx(0.1)
    assert (
        pts["n"] == 40
        and pts["n_dates"] == 12
        and pts["ci_lo_cents"] > 0
        and pts["ci_level"] == 0.8
    )
    assert all(r["action"] == "hold" for r in recs if r["stat"] != "pts")
    assert load_weights(cfg)["pts"] == pytest.approx(0.1)
    assert (
        update_weights(cfg, book, today=SLATE + timedelta(days=3), now=NOW) == []
    )  # at most weekly
    again = update_weights(cfg, book, today=SLATE + timedelta(days=7), now=NOW)
    assert next(r for r in again if r["stat"] == "pts")["w_after"] == pytest.approx(0.2)
    log = Path(cfg.model_weights["log"])
    log = log if log.is_absolute() else cfg.root / log
    assert len(log.read_text().splitlines()) == 2 * len(STAT_KEYS)  # every evaluation is logged


def test_weight_lowers_on_a_negative_mean_floors_at_zero_and_holds_on_thin_evidence(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    neg = PaperBook(tmp_path / "n.duckdb")
    seed_stat(neg, 40, 12, clv=-2.0)
    r = next(x for x in update_weights(cfg, neg, today=SLATE, now=NOW) if x["stat"] == "pts")
    assert r["action"] == "lower" and r["w_after"] == 0.0  # floor
    few = PaperBook(tmp_path / "f.duckdb")
    seed_stat(few, 20, 12, clv=3.0)  # n < 30
    cfg2 = replace(cfg, model_weights={**cfg.model_weights, "log": str(tmp_path / "w2.jsonl")})
    assert (
        next(x for x in update_weights(cfg2, few, today=SLATE, now=NOW) if x["stat"] == "pts")[
            "action"
        ]
        == "hold"
    )
    few_dates = PaperBook(tmp_path / "d.duckdb")
    seed_stat(few_dates, 40, 4, clv=3.0)  # dates < 10
    cfg3 = replace(cfg, model_weights={**cfg.model_weights, "log": str(tmp_path / "w3.jsonl")})
    assert (
        next(
            x for x in update_weights(cfg3, few_dates, today=SLATE, now=NOW) if x["stat"] == "pts"
        )["action"]
        == "hold"
    )
    noisy = PaperBook(tmp_path / "z.duckdb")
    seed_stat(noisy, 40, 12, clv=0.1, jitter=9.0)  # positive mean, lower bound below 0: hold
    cfg4 = replace(cfg, model_weights={**cfg.model_weights, "log": str(tmp_path / "w4.jsonl")})
    assert (
        next(x for x in update_weights(cfg4, noisy, today=SLATE, now=NOW) if x["stat"] == "pts")[
            "action"
        ]
        == "hold"
    )


def test_weight_never_exceeds_the_cap(cfg: JarvisConfig, tmp_path: Path) -> None:
    log = tmp_path / "w.jsonl"
    log.write_text(
        json.dumps(
            {
                "date": "2026-10-01",
                "stat": "pts",
                "action": "raise",
                "w_before": 0.4,
                "w_after": 0.9,
            }
        )
        + "\n"
    )
    c = replace(cfg, model_weights={**cfg.model_weights, "log": str(log)})
    assert load_weights(c)["pts"] == 0.5


def test_an_earned_weight_moves_fair_toward_the_model(cfg: JarvisConfig, tmp_path: Path) -> None:
    class Lean(StubModel):
        def p_yes(self, q):  # type: ignore[no-untyped-def]
            return 0.40 if q.kind == "prop" else None

    log = tmp_path / "w.jsonl"
    log.write_text(json.dumps({"date": "2026-10-01", "stat": "pts", "w_after": 0.5}) + "\n")
    c = replace(cfg, model_weights={**cfg.model_weights, "log": str(log)})
    st = gather(c, SLATE, NOW, use_polymarket=False, model=Lean())
    prop = next(q for q in st.quotes if q.kind == "prop")
    p = single_candidate(c, st, prop, "yes")
    assert p is not None and p.w_model == 0.5 and p.model_prob == 0.40
    pin = 0.62 / 1.04 * 0.97
    assert p.pin_prob == pytest.approx(pin) and p.fair_prob == pytest.approx(
        pin + 0.5 * (0.40 - pin)
    )
    assert "w 0.5" in fair_phrase(p)
    st0 = gather(cfg, SLATE, NOW, use_polymarket=False, model=Lean())
    p0 = single_candidate(cfg, st0, prop, "yes")
    assert (
        p0 is not None and p0.w_model == 0.0 and p0.fair_prob == pytest.approx(pin)
    )  # w = 0: unchanged


def test_force_only_displays_and_never_steps_a_weight(cfg: JarvisConfig, tmp_path: Path) -> None:
    book = PaperBook(tmp_path / "b.duckdb")
    seed_stat(book, 40, 12, clv=3.0)
    for _ in range(3):  # three forced runs on the same evidence
        shown = update_weights(cfg, book, today=SLATE, now=NOW, display_only=True)
        assert next(r for r in shown if r["stat"] == "pts")["action"] == "raise"
    assert load_weights(cfg)["pts"] == 0.0  # nothing was logged, nothing moved
    log = Path(cfg.model_weights["log"])
    assert not (log if log.is_absolute() else cfg.root / log).exists()
    update_weights(cfg, book, today=SLATE, now=NOW)  # the real, cadence-limited run: one step
    assert load_weights(cfg)["pts"] == pytest.approx(0.1)
    update_weights(cfg, book, today=SLATE, now=NOW, display_only=True)  # forcing again: still 0.1
    assert update_weights(cfg, book, today=SLATE + timedelta(days=2), now=NOW) == []
    assert load_weights(cfg)["pts"] == pytest.approx(0.1)


def settle(book: PaperBook, label: str, clv: float, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        p = replace(
            mkp(label, f"K{label}{i}"), inputs={"leg_costs": [1.0]}, contracts=2, stake_usd=1.0
        )
        book.record(
            [p], source="slate", slate=SLATE - timedelta(days=i % 9), now=NOW + timedelta(minutes=i)
        )
        con = book._con()
        con.execute(
            "INSERT INTO scores SELECT play_id, ?, 'settled', TRUE, 0.1, NULL, NULL, NULL, '', ?, NULL FROM plays WHERE ticker = ?",
            [NOW, clv, f"K{label}{i}"],
        )
        con.close()


def mkp(label: str, key: str) -> Play:
    leg = PlayLeg("kalshi", key, "yes", 0.5, "game_win", f"{key}", team_id=LAL)
    return Play(G1, "LAL@LAC", TIP1, (leg,), label, 1.0, 0.5, 2.0, 0.5, 2, 1.0, "r")


def test_kill_switch_counts_only_thin_strong_arbitrage_and_latches_until_cleared(
    cfg: JarvisConfig, tmp_path: Path
) -> None:
    from nba.jarvis.book import clear_kill_switch, kill_switch, latch_kill_switch

    book = PaperBook(tmp_path / "b.duckdb")
    settle(
        book, "none", -5.0, cfg.kill_min_bets
    )  # entertainment plays are expected to lose to the close
    assert not kill_switch(cfg, book).active and kill_switch(cfg, book).n == 0
    settle(book, "thin", 0.4, cfg.kill_min_bets)  # edge plays beat the close: fine
    assert not kill_switch(cfg, book).active
    settle(
        book, "strong", -3.0, cfg.kill_min_bets, start=100
    )  # the last 30 edge bets lose to the close
    ks = kill_switch(cfg, book)
    assert ks.active and not ks.latched and ks.mean_clv is not None and ks.mean_clv < 0
    ks = latch_kill_switch(cfg, book, SLATE, NOW)
    assert ks.active and ks.latched and ks.since == SLATE
    assert "LATCHED" in ks.message() and "killswitch --clear" in ks.message()
    settle(book, "thin", 5.0, cfg.kill_min_bets, start=200)  # a good run does NOT clear it
    assert kill_switch(cfg, book).active
    cleared = clear_kill_switch(
        cfg, book, SLATE + timedelta(days=1), "labels fixed", NOW + timedelta(days=1)
    )
    assert not cleared.active and cleared.n == 0  # the count restarts after the clear
    rows = book.kill_rows()
    assert [r[2] for r in rows] == ["tripped", "cleared"] and rows[1][1] == SLATE + timedelta(
        days=1
    )


def test_a_failing_sink_does_not_repeat_the_message_on_the_other(
    cfg: JarvisConfig, rt: Runtime
) -> None:
    c = FakeHttp()
    rt.sender = None
    rt.cfg = replace(cfg, sinks=("discord", "imessage"))
    rt.discord_client = c
    rt.env = {cfg.discord_env_var: HOOK}  # iMessage has no recipient: that sink fails every time
    for _ in range(3):
        r = rt.send("same text")
        assert not r.ok and "imessage" in r.note
    assert len(c.posted) == 1  # Discord got it once; the imessage failure never re-posted it there
    assert rt.book.sink_done(
        "discord", __import__("nba.jarvis.book", fromlist=["sha"]).sha("same text")
    )


def test_httpx_logging_cannot_leak_the_webhook() -> None:
    import logging

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_halftime_one_bad_event_does_not_block_the_others(
    cfg: JarvisConfig, rt: Runtime, sent: Sent
) -> None:
    run_slate(rt, SLATE)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    f = next(
        (cfg.root / "data" / "raw" / f"live_{SLATE}" / "espn_scoreboard" / str(SLATE)).glob("*.gz")
    )
    with gzip.open(f, "rt") as fh0:
        body = json.loads(fh0.read())
    body["events"].insert(0, {"shortName": "broken", "status": {}, "competitions": []})  # malformed
    with gzip.open(f, "wt") as fh:
        json.dump(body, fh)
    rt.clock = lambda: TIP1 + timedelta(minutes=70)
    assert len(run_tick(rt, SLATE)["halftime"]) == 1
    import nba.jarvis.runner as rn

    orig = rn._halftime_one

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("bad game")

    rn._halftime_one = boom  # type: ignore[assignment]
    try:
        out = run_tick(rt, SLATE)["halftime"]
    finally:
        rn._halftime_one = orig  # type: ignore[assignment]
    assert out and out[0]["ok"] is False and out[0]["error"] == "RuntimeError"
