"""ops/nba_daily.sh pretip: shadow flag on, and one roster source shared by predict and parlay."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "ops" / "nba_daily.sh").read_text()


def _step(name: str) -> str:
    """The (continued) shell command of ``step <name> ...`` in the script."""
    m = re.search(rf"^\s*step {name} (.*?)(?<!\\)\n", SCRIPT, re.M | re.S)
    assert m, name
    return m.group(1).replace("\\\n", " ")


def test_pplay_shadow_flag_in_predict_step() -> None:
    assert "--log-pplay-shadow" in _step("predict")


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
