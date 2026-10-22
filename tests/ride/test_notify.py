from __future__ import annotations

from pathlib import Path

from nba.ride.notify import ENV_VAR, Notifier


def test_imessage_used_when_env_set_and_number_never_logged(tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []

    def fake_send(t: str, to: str) -> bool:
        sent.append((t, to))
        return True

    n = Notifier(
        tmp_path / "l.log",
        imessage=fake_send,
        notify=lambda t: False,
        env={ENV_VAR: "+15550001111"},
    )
    assert n.alert("RIDE", "hello\nworld") == "imessage"
    assert sent == [("hello\nworld", "+15550001111")]
    log = (tmp_path / "l.log").read_text()
    assert "5550001111" not in log and "push=imessage" in log and "\n" not in log.strip()


def test_falls_back_to_notification_then_log_only(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_notify(t: str) -> bool:
        calls.append(t)
        return True

    n = Notifier(
        tmp_path / "l.log",
        imessage=lambda t, to: False,
        notify=fake_notify,
        env={ENV_VAR: "x"},
    )
    assert n.alert("PLAN", "a") == "notification"
    unset = Notifier(
        tmp_path / "m.log", imessage=lambda t, to: True, notify=lambda t: False, env={}
    )
    assert unset.alert("PLAN", "b") == "none"  # no env var, no notification: still logged
    assert "push=none" in (tmp_path / "m.log").read_text()


def test_push_off_only_logs(tmp_path: Path) -> None:
    n = Notifier(
        tmp_path / "l.log",
        push=False,
        imessage=lambda t, to: True,
        notify=lambda t: True,
        env={ENV_VAR: "x"},
    )
    assert n.alert("RIDE", "t") == "none"
