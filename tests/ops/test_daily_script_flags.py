# ruff: noqa: E501
"""ops/nba_daily.sh pretip: shadow flag on, and one roster source shared by predict and parlay."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "ops" / "nba_daily.sh"
SCRIPT = SCRIPT_PATH.read_text()


def _step(name: str) -> str:
    """The (continued) shell command of ``step <name> ...`` in the script."""
    m = re.search(rf"^\s*step {name} (.*?)(?<!\\)\n", SCRIPT, re.M | re.S)
    assert m, name
    return m.group(1).replace("\\\n", " ")


def test_pplay_shadow_flag_in_predict_step() -> None:
    assert "--log-pplay-shadow" in _step("predict")


def test_avg_and_combo_shadow_flags_are_on_in_the_predict_step() -> None:
    # Devin 2026-10-14: AVG_SHADOW_LIVE_2 (logs "skipped" until the bundle is installed) and COMBO_STOCKS_SHADOW
    step = _step("predict")
    assert "--log-avg-shadow" in step and "--log-combo-shadow" in step
    # the shadows are flags of the one predict call: no separate step that could block settle, report or pretip
    assert "avg_shadow" not in SCRIPT.replace("--log-avg-shadow", "").replace(
        "heartbeat avg_shadow.json", ""
    )
    assert not re.search(r"^\s*step (avg|combo)", SCRIPT, re.M)


def test_parlay_steps_follow_the_predict_roster_switch() -> None:
    # one ROSTER variable feeds all three steps; the date switch lives only in ops/roster_source.sh
    assert len(re.findall(r"\bROSTER=", SCRIPT)) == 0
    assert "/roster_source.sh" in SCRIPT
    for name in ("predict", "parlay_shadow", "props_analysis"):
        assert '--roster-source "$ROSTER"' in _step(name), name


_INCLUDE = Path(__file__).resolve().parents[2] / "ops" / "roster_source.sh"


def _roster_on(day: str) -> str:
    import subprocess

    out = subprocess.run(
        ["sh", "-c", f'TODAY_ET={day}; . "{_INCLUDE}"; echo $ROSTER'],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_official_roster_window_runs_through_2026_11_30_inclusive() -> None:
    assert _roster_on("2026-10-12") == "official"
    assert _roster_on("2026-11-04") == "official"
    assert _roster_on("2026-11-30") == "official"
    assert _roster_on("2026-12-01") == "recent"


def test_both_entrypoints_use_the_single_switch() -> None:
    lineups = (_INCLUDE.parent / "nba_lineups.sh").read_text()
    assert "/roster_source.sh" in lineups
    for text in (SCRIPT, lineups):
        assert "2026-11-04" not in text and "2026-11-03" not in text


# --- nightly facts rebuild: opt-in, and its failure never changes the morning exit path ----------


def _run_morning(tmp: Path, *, nightly: str, facts_rc: int) -> tuple[int, str, str]:
    import os
    import subprocess

    root = tmp / "root"
    (root / "data").mkdir(parents=True)
    uv = tmp / "uv"
    # stub uv: the facts build exits facts_rc, every other step succeeds and is recorded
    uv.write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp}/calls"\n'
        f'case "$*" in *nba.facts*) exit {facts_rc};; esac\nexit 0\n'
    )
    rc = tmp / "rclone"
    rc.write_text("#!/bin/sh\nexit 0\n")
    for f in (uv, rc):
        f.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "NBA_ROOT": str(root),
        "NBA_UV": str(uv),
        "NBA_RCLONE": str(rc),
    }
    if nightly:
        env["NBA_FACTS_NIGHTLY"] = nightly
    script = Path(__file__).resolve().parents[2] / "ops" / "nba_daily.sh"
    p = subprocess.run(["sh", str(script), "morning"], env=env, capture_output=True, text=True)
    calls = (tmp / "calls").read_text() if (tmp / "calls").exists() else ""
    hb = (root / "data/ops/heartbeat/daily-morning.json").read_text()
    return p.returncode, calls, hb


def test_nightly_facts_off_by_default(tmp_path: Path) -> None:
    rc, calls, _ = _run_morning(tmp_path, nightly="", facts_rc=0)
    assert rc == 0 and "nba.facts" not in calls


def test_nightly_facts_failure_does_not_change_exit_path(tmp_path: Path) -> None:
    rc, calls, hb = _run_morning(tmp_path, nightly="1", facts_rc=1)
    lines = calls.splitlines()
    facts_i = next(i for i, c in enumerate(lines) if "nba.facts" in c)
    assert "--seasons 2022,2023,2024,2026" in lines[facts_i] and "2025" not in lines[facts_i]
    assert any("daily settle" in c for c in lines[:facts_i])
    assert any("daily report" in c for c in lines[:facts_i])
    # steps after the failed rebuild still ran
    assert any("parlay evaluate" in c and "--settle" in c for c in lines[facts_i + 1 :])
    assert (
        rc == 0
    )  # script exit unchanged; the failure is visible via ALERTS.md and the heartbeat rc
    assert '"rc": 1' in hb


def test_nightly_facts_informational_rc4_is_not_a_failure(tmp_path: Path) -> None:
    rc, _, hb = _run_morning(tmp_path, nightly="1", facts_rc=4)
    assert rc == 0 and '"rc": 0' in hb


# --- morning backup: prune and Drive upload see only the morning job's own nba_YYYYMMDD.duckdb ----


def _select_backups(tmp_path: Path, script: str = SCRIPT) -> list[str]:
    """Newest-first names the script's own ``ls`` line (the one behind prune and ``latest=``)
    selects in a scratch data/backups where a history backup is the newest file."""
    import os
    import subprocess

    glob = re.search(r"^BACKUP_GLOB=.+$", script, re.M)
    assert glob, "BACKUP_GLOB missing"
    d = tmp_path / "data" / "backups"
    d.mkdir(parents=True)
    names = [
        "nba_20261010.duckdb",
        "nba_20261011.duckdb",
        "nba_20261012.duckdb",
        "nba_history_pre_static_fix_20261013T080424.duckdb",  # newest by mtime
        "facts_pre_seasons_20261011.duckdb",
    ]
    for i, n in enumerate(names):
        (d / n).write_text("x")
        os.utime(d / n, (1_000_000 + i, 1_000_000 + i))
    lines = re.findall(r"^\s*(?:latest=\$\()?(ls -1t \$BACKUP_GLOB[^|)]*)", script, re.M)
    assert len(lines) == 2, lines  # the prune line and the `latest=` line
    out = subprocess.run(
        ["sh", "-c", f"{glob.group(0)}; {lines[1]}"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [Path(p).name for p in out.stdout.split()]


def test_backup_prune_and_drive_upload_ignore_history_backups(tmp_path: Path) -> None:
    assert "backups/nba_*" not in SCRIPT  # nothing may fall back to the loose glob
    assert _select_backups(tmp_path) == [
        "nba_20261012.duckdb",
        "nba_20261011.duckdb",
        "nba_20261010.duckdb",
    ]


def test_the_backup_test_would_catch_the_old_loose_glob(tmp_path: Path) -> None:
    loose = SCRIPT.replace("nba_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].duckdb", "nba_*.duckdb")
    assert "nba_history_pre_static_fix_20261013T080424.duckdb" in _select_backups(tmp_path, loose)


# --- per-step status record read by the app export gate (nba.ops.app_export) ---------------------


def test_morning_writes_a_step_record_tied_to_the_heartbeat(tmp_path: Path) -> None:
    import json

    rc, _, hb = _run_morning(tmp_path, nightly="1", facts_rc=1)
    lines = (tmp_path / "root/data/ops/heartbeat/daily-morning.steps").read_text().splitlines()
    assert lines[0] == f"start_ts {json.loads(hb)['start_ts']}"
    steps = {x.split()[0]: x.split()[1:] for x in lines[1:]}
    assert steps["settle"] == ["0", "ok"] and steps["facts"] == ["1", "failed"]
    assert steps["data_check"] == ["0", "ok"]
    assert rc == 0


def test_script_step_names_satisfy_the_gate_contract(tmp_path: Path) -> None:
    import json

    from nba.ops.app_export import data_step_problem

    hb_dir = tmp_path / "off/root/data/ops/heartbeat"
    _, _, hb = _run_morning(tmp_path / "off", nightly="", facts_rc=0)
    assert data_step_problem(hb_dir, json.loads(hb)["start_ts"]) == (None, [])
    _, _, hb = _run_morning(tmp_path / "on", nightly="1", facts_rc=1)
    on_dir = tmp_path / "on/root/data/ops/heartbeat"
    # issue #13: facts is not forecast-bearing, so its failure is reported as degraded, not a block
    assert data_step_problem(on_dir, json.loads(hb)["start_ts"]) == (None, ["facts"])


def test_coverage_and_scorecard_are_wired() -> None:
    # Devin, 2026-10-20 ("wire em"): coverage every pretip run (informational), and last night's
    # coverage + the season-to-date scorecard each morning after the settle.
    assert '--date "$TODAY_ET"' in _step("coverage")
    assert '--date "$YESTERDAY_ET"' in _step("coverage_yesterday")
    assert "--since 2026-10-20" in _step("scorecard")
    assert "date -v-1d" in SCRIPT  # YESTERDAY_ET is defined
    assert re.search(r'\[ "\$name" = coverage \] && \[ "\$rc" -eq 3 \]', SCRIPT)
    assert SCRIPT.index("step settle") < SCRIPT.index("step scorecard")


def test_settle_rc4_is_informational_and_rc5_alerts(tmp_path: Path) -> None:
    """settle exits 4 for a fresh refusal (box not final yet) and 5 for one older than 24 h."""
    import os
    import subprocess

    alerts = {}
    for rc in (4, 5):
        d = tmp_path / f"rc{rc}"
        (d / "root" / "data").mkdir(parents=True)
        uv, rclone = d / "uv", d / "rclone"
        uv.write_text(f'#!/bin/sh\ncase "$*" in *"nba.daily settle"*) exit {rc};; esac\nexit 0\n')
        rclone.write_text("#!/bin/sh\nexit 0\n")
        for f in (uv, rclone):
            f.chmod(0o755)
        env = {"PATH": os.environ["PATH"], "NBA_ROOT": str(d / "root"), "NBA_UV": str(uv),
               "NBA_RCLONE": str(rclone)}  # fmt: skip
        subprocess.run(
            ["sh", str(SCRIPT_PATH), "morning"], env=env, capture_output=True, check=True
        )
        alerts[rc] = (d / "root" / "data" / "ops" / "ALERTS.md").exists()
    assert alerts == {4: False, 5: True}


# --- early briefing + report edition + post-game timeouts (Devin 2026-10-21) ---------------------


def _run_mode(
    tmp: Path, mode: str, uv_body: str = "", extra_env: dict[str, str] | None = None
) -> tuple[int, list[str], Path]:
    """Run the real script with a stub uv that records every call; ``uv_body`` adds case arms."""
    import os
    import subprocess

    root = tmp / "root"
    (root / "data").mkdir(parents=True)
    uv, rclone = tmp / "uv", tmp / "rclone"
    uv.write_text(f'#!/bin/sh\necho "$@" >> "{tmp}/calls"\ncase "$*" in\n{uv_body}\nesac\nexit 0\n')
    rclone.write_text("#!/bin/sh\nexit 0\n")
    for f in (uv, rclone):
        f.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "NBA_ROOT": str(root), "NBA_UV": str(uv),
           "NBA_RCLONE": str(rclone), **(extra_env or {})}  # fmt: skip
    p = subprocess.run(["sh", str(SCRIPT_PATH), mode], env=env, capture_output=True, text=True)
    calls = (tmp / "calls").read_text().splitlines() if (tmp / "calls").exists() else []
    return p.returncode, calls, root


def _idx(calls: list[str], needle: str) -> int:
    return next(i for i, c in enumerate(calls) if needle in c)


def test_morning_runs_early_forecast_and_briefing_after_report_before_fetches(
    tmp_path: Path,
) -> None:
    _, calls, _ = _run_mode(tmp_path, "morning")
    rep, fc = _idx(calls, "daily report"), _idx(calls, "daily run --date")
    br = _idx(calls, "nba.explain slate")
    assert rep < fc < br < _idx(calls, "coverage --date")
    assert br < _idx(calls, "postgame-fetch --source tracking")
    assert "--edition early" in calls[br] and "_early.md" in calls[br]
    # the early forecast carries exactly the pretip predict flags
    assert (
        _step("early_forecast").split("--roster-source", 1)[1]
        == _step("predict").split("--roster-source", 1)[1]
    )


def test_pretip_runs_briefing_report_after_predict_before_refs_and_market(tmp_path: Path) -> None:
    _, calls, _ = _run_mode(tmp_path, "pretip")
    pr, br = _idx(calls, "daily run --date"), _idx(calls, "nba.explain slate")
    assert pr < br < _idx(calls, "ingest.referees") < _idx(calls, "markets capture")
    assert "--edition report" in calls[br] and "_report.md" in calls[br]


def test_briefing_and_early_forecast_rc4_informational_other_alerts(tmp_path: Path) -> None:
    arms = '*"nba.explain slate"*) exit {rc};;\n*"nba.daily run"*) exit {rc};;'
    out = {}
    for rc in (4, 5):
        d = tmp_path / f"rc{rc}"
        _run_mode(d, "morning", arms.format(rc=rc))
        a = d / "root/data/ops/ALERTS.md"
        out[rc] = a.read_text() if a.exists() else ""
    assert out[4] == ""
    assert "early_forecast failed" in out[5] and "briefing_early failed" in out[5]


def test_postgame_steps_are_wrapped_with_the_alarm() -> None:
    assert re.search(r'step "fetch_\$s" alarm_run "\$FETCH_TIMEOUT_S"', SCRIPT)
    assert _step("postgame_load").startswith('alarm_run "$LOAD_TIMEOUT_S"')
    assert "FETCH_TIMEOUT_S=${NBA_FETCH_TIMEOUT_S:-1800}" in SCRIPT
    assert "LOAD_TIMEOUT_S=${NBA_LOAD_TIMEOUT_S:-1200}" in SCRIPT
    assert "alarm shift; exec @ARGV" in SCRIPT
    assert "exit 127" in SCRIPT
    assert _step("predict").startswith('alarm_run "$PREDICT_TIMEOUT_S"')
    assert _step("early_forecast").startswith('alarm_run "$EARLY_TIMEOUT_S"')


def test_a_hung_fetch_alerts_as_timeout_and_the_following_steps_still_run(tmp_path: Path) -> None:
    # stub uv hangs in the tracking fetch; a 1 s alarm ends it with rc 142
    _, calls, root = _run_mode(
        tmp_path,
        "morning",
        '*"postgame-fetch --source tracking"*) exec sleep 30;;',
        {"NBA_FETCH_TIMEOUT_S": "1"},
    )
    steps = (root / "data/ops/heartbeat/daily-morning.steps").read_text().splitlines()
    assert "fetch_tracking 142 failed" in steps
    assert "fetch_tracking timed out" in (root / "data/ops/ALERTS.md").read_text()
    after = [x.split()[0] for x in steps[steps.index("fetch_tracking 142 failed") + 1 :]]
    assert {
        "fetch_hustle",
        "fetch_officials",
        "fetch_matchups",
        "postgame_load",
        "data_check",
    } <= set(after)
    assert any("source hustle" in c for c in calls)


def test_exit_142_from_a_fetch_step_alerts(tmp_path: Path) -> None:
    _, _, root = _run_mode(tmp_path, "morning", '*"source officials"*) exit 142;;')
    assert "fetch_officials timed out" in (root / "data/ops/ALERTS.md").read_text()


def test_a_hung_early_forecast_alerts_and_the_morning_continues(tmp_path: Path) -> None:
    _, calls, root = _run_mode(
        tmp_path, "morning", '*"daily run --date"*) exec sleep 30;;', {"NBA_EARLY_TIMEOUT_S": "1"}
    )
    steps = (root / "data/ops/heartbeat/daily-morning.steps").read_text().splitlines()
    assert "early_forecast 142 failed" in steps
    assert "early_forecast timed out" in (root / "data/ops/ALERTS.md").read_text()
    assert any("nba.explain slate" in c for c in calls) and any(
        "postgame-fetch" in c for c in calls
    )


def test_a_hung_pretip_predict_alerts_and_the_following_steps_run(tmp_path: Path) -> None:
    _, calls, root = _run_mode(
        tmp_path, "pretip", '*"daily run --date"*) exec sleep 30;;', {"NBA_PREDICT_TIMEOUT_S": "1"}
    )
    steps = (root / "data/ops/heartbeat/daily-pretip.steps").read_text().splitlines()
    assert "predict 142 failed" in steps
    assert "predict timed out" in (root / "data/ops/ALERTS.md").read_text()
    assert any("ingest.referees" in c for c in calls) and any("markets capture" in c for c in calls)


def test_alarm_run_of_a_missing_command_fails_the_step(tmp_path: Path) -> None:
    _, _, root = _run_mode(tmp_path, "morning", "", {"NBA_UV": str(tmp_path / "no_such_uv")})
    steps = (root / "data/ops/heartbeat/daily-morning.steps").read_text().splitlines()
    # the unwrapped steps (127 from the shell) and the wrapped ones (127 from alarm_run) both fail
    assert "fetch_tracking 127 failed" in steps and "early_forecast 127 failed" in steps
