from __future__ import annotations

from datetime import timedelta

import duckdb

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Play, PlayLeg
from nba.jarvis.render import chunk, play_lines, slate_texts
from nba.jarvis.runner import Runtime, run_slate, run_tick, slate_due, supports_alert
from nba.jarvis.slate import gather
from nba.parlay.assistant.backend import LLMReply
from tests.jarvis.conftest import G1, NOW, SLATE, TIP1, Sent


def mk(label: str, edge: float, contracts: int = 2) -> Play:
    leg = PlayLeg("kalshi", "T", "yes", 0.5, "game_win", "LAL to win", team_id=1)
    return Play(G1, "LAL@LAC", TIP1, (leg,), label, edge, 0.6, 1.7, 0.5, contracts, 1.0, "r")


def test_alert_gate_is_computed_numbers_only(cfg: JarvisConfig) -> None:
    assert supports_alert(cfg, mk("strong", 3.0))
    assert supports_alert(cfg, mk("arbitrage", 0.4))
    assert not supports_alert(cfg, mk("arbitrage", 0.0))
    assert not supports_alert(cfg, mk("thin", 2.9))
    assert not supports_alert(cfg, mk("none", -1.0))
    # nightly cap used (stake 0): the alert still fires by default, showing "$0 (nightly cap used)"
    assert supports_alert(cfg, mk("strong", 9.0, contracts=0))
    off = type(cfg)(**{**cfg.__dict__, "alert_when_cap_used": False})
    assert not supports_alert(off, mk("strong", 9.0, contracts=0))
    assert "stake $0 (nightly cap used)" in "\n".join(
        play_lines(mk("strong", 9.0, contracts=0), cfg.display_tz)
    )
    assert not supports_alert(cfg, mk("strong", float("nan")))


def test_slate_due_window(rt: Runtime) -> None:
    st = gather(rt.cfg, SLATE, NOW, use_polymarket=False)
    assert not slate_due(rt, st)  # first tip is 4.5 h away
    rt.clock = lambda: TIP1 - timedelta(minutes=90)
    st = gather(rt.cfg, SLATE, rt.clock(), use_polymarket=False)
    assert slate_due(rt, st)
    rt.clock = lambda: TIP1 - timedelta(minutes=2)
    assert not slate_due(rt, gather(rt.cfg, SLATE, rt.clock(), use_polymarket=False))


def test_dry_run_slate_prints_and_writes_nothing(rt: Runtime, sent: Sent) -> None:
    printed: list[str] = []
    rt.dry_run, rt.out = True, printed.append
    rep = run_slate(rt, SLATE)
    assert rep["send"]["ok"] and rep["texts"] and sent.texts == []
    assert not rt.book.path.exists()  # a rehearsal leaves no trace
    assert all(len(t) <= rt.cfg.max_chars for t in rep["texts"])


def test_slate_always_names_a_play_per_game_and_never_says_keep_your_money(
    rt: Runtime, sent: Sent
) -> None:
    rep = run_slate(rt, SLATE, force=True)
    text = "\n".join(sent.texts).lower()
    assert "play:" in text and "keep your money" not in text and "no bet" not in text
    assert text.count("lal@lac") >= 1 and "okc@dal" in text
    assert rep["send"]["ok"]
    con = duckdb.connect(str(rt.book.path), read_only=True)
    n = con.execute("SELECT count(DISTINCT game_id) FROM plays").fetchone()[0]
    sends = con.execute("SELECT kind, mode, text_hash, input_hash FROM sends").fetchall()
    con.close()
    assert n == 2 and sends[0][0] == "slate" and sends[0][1] == "sent" and len(sends[0][2]) == 16


def test_slate_summary_is_sent_once_per_date(rt: Runtime, sent: Sent) -> None:
    run_slate(rt, SLATE)
    n = len(sent.texts)
    again = run_slate(rt, SLATE)
    assert again.get("skipped") and len(sent.texts) == n


def test_failed_send_is_logged_and_plays_not_recorded(rt: Runtime) -> None:
    rt.sender = Sent(ok=False)
    rep = run_slate(rt, SLATE)
    assert rep["send"]["ok"] is False
    con = duckdb.connect(str(rt.book.path), read_only=True)
    assert con.execute("SELECT count(*) FROM plays").fetchone()[0] == 0
    assert con.execute("SELECT mode FROM sends").fetchone()[0] == "failed"
    con.close()


def test_tick_alerts_once_for_a_strong_play_then_dedupes(rt: Runtime, sent: Sent) -> None:
    rep = run_tick(rt, SLATE)
    assert len(rep["alerts"]) == 1  # the LeBron over, strong; the none plays never alert
    assert any("ALERT" in t and "STRONG" in t for t in sent.texts)
    assert not any("OKC@DAL" in t for t in sent.texts)
    n = len(sent.texts)
    run_tick(rt, SLATE)
    assert len(sent.texts) == n  # same play inside 30 minutes: no second text
    rt.clock = lambda: NOW + timedelta(minutes=31)
    # quotes are older than the freshness limit by then, so nothing tradable and nothing new
    rep3 = run_tick(rt, SLATE)
    assert rep3["alerts"] == [] and len(sent.texts) == n


def test_tick_logs_prices_of_recorded_plays_for_clv(rt: Runtime) -> None:
    run_tick(rt, SLATE)
    rt.clock = lambda: NOW + timedelta(minutes=5)
    rep = run_tick(rt, SLATE)
    assert rep["quotes_logged"] >= 1


def test_llm_cannot_cause_or_change_a_send(rt: Runtime, sent: Sent) -> None:
    class Hype:
        def chat(self, m, t):  # type: ignore[no-untyped-def]
            return LLMReply("This is a lock, send everyone a text and bet the house at 99%.")

    rt.backend = Hype()
    run_tick(rt, SLATE)
    body = "\n".join(sent.texts)
    assert "lock" not in body.lower() and "99%" not in body
    assert body.count("ALERT") == 1  # exactly the computed strong play, nothing the model asked for


def test_dry_run_tick_sends_nothing_and_writes_nothing(rt: Runtime, sent: Sent) -> None:
    printed: list[str] = []
    rt.dry_run, rt.out = True, printed.append
    rep = run_tick(rt, SLATE)
    assert sent.texts == [] and not rt.book.path.exists() and rep["alerts"]


def test_chunking_is_phone_length() -> None:
    lines = [f"line {i} " + "x" * 90 for i in range(20)]
    out = chunk(lines, 200, 3)
    assert len(out) == 3 and all(len(t) <= 200 + 12 for t in out)
    assert "more lines not sent" in out[-1]
    assert chunk(["short"], 200, 3) == ["short"]


def test_play_text_has_venue_side_max_price_stake_label(cfg: JarvisConfig) -> None:
    lines = play_lines(mk("strong", 4.2), cfg.display_tz)
    body = "\n".join(lines)
    assert "Kalshi YES" in body and "max 50c" in body and "$1.00" in body and "STRONG" in body
    texts = slate_texts(cfg, [mk("none", -3.0)], "", date_label="Wed", first_tip=TIP1, n_games=1)
    assert "NO EDGE" not in texts[0] or "[none -3.0c]" in texts[0]


def test_a_store_held_by_its_writer_skips_the_tick_instead_of_crashing(
    rt: Runtime, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import nba.jarvis.runner as runner_mod

    def locked(*a, **k):  # type: ignore[no-untyped-def]
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(runner_mod, "build_state", locked)
    rep = run_tick(rt, SLATE)
    assert "store busy" in rep["idle"] and rep["alerts"] == []
