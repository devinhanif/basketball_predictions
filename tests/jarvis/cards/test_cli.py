"""``python -m nba.jarvis cards``: the dry-run print, the HTML/JSON preview, and the webhook setup
helpers. None of it sends; none of it prints a URL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nba.jarvis.cards.colors import ABBRS
from nba.jarvis.cli import main

SECRET = "SECRETSECRET"


def hook(team: str) -> str:
    return f"https://discord.com/api/webhooks/{1000 + sum(map(ord, team))}/{SECRET}{team}"


def test_template_prints_one_empty_slot_per_team(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["cards", "--template"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert sorted(got) == list(ABBRS) and set(got.values()) == {""}


def test_check_webhooks_reports_counts_and_team_names_never_a_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mixed = {"LAL": hook("LAL"), "LAC": hook("LAL"), "BOS": "https://example.com/x", "DEN": ""}
    monkeypatch.setenv("JARVIS_DISCORD_TEAM_WEBHOOKS", json.dumps(mixed))
    code = main(["cards", "--check-webhooks"])
    out = capsys.readouterr().out
    assert code == 1  # one invalid entry
    assert "2/30 team webhooks configured" in out and "invalid: BOS" in out
    assert "same URL for: LAC, LAL" in out and "no webhook yet:" in out
    assert SECRET not in out and "discord.com" not in out


def test_check_webhooks_with_nothing_set_is_a_clean_no(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["cards", "--check-webhooks"]) == 1
    assert "0/30" in capsys.readouterr().out


def test_dry_run_prints_the_embeds_and_the_preview_writes_html_and_json(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "JARVIS_DISCORD_TEAM_WEBHOOKS", json.dumps({"LAL": hook("LAL"), "OKC": hook("OKC")})
    )
    out_dir = tmp_path / "prev"
    args = ["cards", "--root", str(root), "--date", "2026-10-28", "--no-model", "--no-polymarket"]
    code = main([*args, "--phase", "pretip", "--preview", "--out", str(out_dir)])
    cap = capsys.readouterr()
    assert code == 0
    data = json.loads(cap.out)
    games = {c["game"]: c for c in data["cards"]}
    assert set(games) == {"LAL@LAC", "OKC@DAL"}
    e = games["LAL@LAC"]["embeds"]["LAL"]
    assert e["title"].startswith("LAL @ LAC —") and e["footer"]["text"].endswith("Not advice.")
    assert set(games["LAL@LAC"]["embeds"]) == {"LAL", "LAC"}  # one card, both channels
    assert "2 with a webhook" in cap.err and "preview written" in cap.err
    assert SECRET not in cap.out + cap.err and "api/webhooks" not in cap.out
    html = (out_dir / "cards.html").read_text()
    assert "LAL @ LAC" in html and "Paper only. Not advice." in html and SECRET not in html
    assert json.loads((out_dir / "cards.json").read_text())["cards"]
    assert not (root / "data" / "jarvis" / "card_picks.jsonl").exists()  # a preview logs nothing


def test_the_preview_can_show_a_later_phase_from_real_data(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["cards", "--root", str(root), "--date", "2026-10-28", "--no-model", "--no-polymarket"]
    assert main([*args, "--phase", "halftime", "--game", "LAL@LAC"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["game"] for c in data["cards"]] == ["LAL@LAC"]
    e = data["cards"][0]["embeds"]["LAL"]
    assert any(f["name"] == "Halftime entry check" for f in e["fields"])
