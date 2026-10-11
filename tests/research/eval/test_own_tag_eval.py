"""OWN_TAG (docs/prereg/OWN_TAG.md): snapshot, cutoff, placebo, arm keys, hash refusal."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from nba.features.injury_report import ReportTriggerConfig
from research.eval import own_tag_eval as ev
from research.features.own_tag import build_own_tag, placebo_own_tag

CFG = ReportTriggerConfig(tip_source="real")
TIP = dt.datetime(2024, 1, 10, 19, 30)


def _tips() -> pl.DataFrame:
    return pl.DataFrame({"game_id": ["g1", "g2"], "tipoff_utc": [TIP, TIP], "tip_et": [TIP, TIP]})


def _games() -> pl.DataFrame:
    d = dt.date(2024, 1, 10)
    return pl.DataFrame({"game_id": ["g1", "g2"], "game_date": [d, d]})


def _roster() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1"] * 3 + ["g2"] * 2,
            "player_id": [1, 2, 3, 4, 5],
            "team_id": [10, 10, 11, 12, 12],
        }
    )


def _row(g: str, p: int, status: str, minutes_before: int) -> dict[str, object]:
    return {
        "game_id": g,
        "player_id": p,
        "status": status,
        "as_of": TIP - dt.timedelta(minutes=minutes_before),
        "source": "nba_official_report",
    }


def _build(rows: list[dict[str, object]]) -> pl.DataFrame:
    avail = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "source": pl.Utf8,
        },
    )
    return build_own_tag(_roster(), _games(), avail, CFG, _tips()).sort(["game_id", "player_id"])


def test_latest_snapshot_only_and_hours() -> None:
    out = _build(
        [
            _row("g1", 1, "questionable", 300),
            _row("g1", 2, "probable", 300),
            _row("g1", 1, "probable", 120),  # latest usable snapshot: player 1 probable, 2 absent
            _row("g1", 3, "Doubtful", 120),
        ]
    )
    g1 = out.filter(pl.col("game_id") == "g1")
    assert g1["own_tag"].to_list() == [
        2.0,
        0.0,
        4.0,
    ]  # earlier-snapshot-only player 2 is NOT carried
    assert g1["own_tag_hours"].to_list() == [2.0, 2.0, 2.0]


def test_post_cutoff_snapshot_ignored_and_boundary() -> None:
    base = [_row("g1", 1, "questionable", 120)]
    late = [*base, _row("g1", 1, "available", 59), _row("g1", 2, "out", 30)]
    assert _build(late)["own_tag"].to_list() == _build(base)["own_tag"].to_list()
    assert _build(late)["own_tag_hours"].to_list() == _build(base)["own_tag_hours"].to_list()
    at_cut = _build([*base, _row("g1", 1, "available", 60)])  # tip - 60 exactly is usable
    assert at_cut.filter(pl.col("game_id") == "g1")["own_tag"].to_list()[0] == 1.0


def test_no_snapshot_game_and_cap_and_out_code() -> None:
    out = _build([_row("g1", 1, "out", 3000)])
    g2 = out.filter(pl.col("game_id") == "g2")
    assert g2["own_tag"].to_list() == [0.0, 0.0]
    assert g2["own_tag_hours"].to_list() == [48.0, 48.0]
    assert g2["_has_snap"].to_list() == [0, 0]
    g1 = out.filter(pl.col("game_id") == "g1")
    assert g1["own_tag"].to_list() == [5.0, 0.0, 0.0]
    assert g1["own_tag_hours"].to_list()[0] == 48.0  # capped


def test_placebo_permutes_within_team_date() -> None:
    n = 60
    f = pl.DataFrame(
        {
            "game_id": [f"g{i // 6}" for i in range(n)],
            "player_id": list(range(n)),
            "team_id": [(i // 6) % 3 for i in range(n)],
            "game_date": [dt.date(2024, 1, 1 + (i // 18)) for i in range(n)],
            "own_tag": [float(i % 5) for i in range(n)],
            "own_tag_hours": [float(i) for i in range(n)],
        }
    )
    p = placebo_own_tag(f, 0)
    assert p.select(ev.KEYS).equals(f.select(ev.KEYS))
    assert p["own_tag_hours"].to_list() == f["own_tag_hours"].to_list()
    assert p["own_tag"].to_list() != f["own_tag"].to_list()
    key = ["team_id", "game_date"]
    a = f.group_by(key).agg(pl.col("own_tag").sort()).sort(key)
    b = p.group_by(key).agg(pl.col("own_tag").sort()).sort(key)
    assert a.equals(b)
    assert placebo_own_tag(f, 0)["own_tag"].to_list() == p["own_tag"].to_list()


def test_arms_share_keys_and_names() -> None:
    f = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2"],
            "player_id": [1, 2, 3],
            "team_id": [10, 10, 11],
            "game_date": [dt.date(2024, 1, 10)] * 3,
            "own_tag": [0.0, 3.0, 2.0],
            "own_tag_hours": [4.0, 4.0, 5.0],
        }
    )
    frames = ev.arm_frames(f)
    assert set(frames) == set(ev.ARMS)
    for a in ev.ARMS:
        assert frames[a].select(ev.KEYS).equals(f.select(ev.KEYS))
    for stat in ev.STATS:
        n0, n1, n3 = (ev.arm_names(stat, a) for a in ("A0", "A1", "A3"))
        assert n1 == [*n0, "own_tag", "own_tag_hours"]
        assert n3 == [*n0, "own_tag"]
        assert ev.arm_names(stat, "A2") == n1


def test_hash_refusal(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("rule\n=== RESULTS BELOW ===\nnot run\n")
    sha = ev.require_frozen(doc, None, True)
    assert ev.require_frozen(doc, sha, False) == sha
    with pytest.raises(SystemExit):
        ev.require_frozen(doc, "0" * 64, False)
    with pytest.raises(SystemExit):
        ev.require_frozen(doc, None, False)
    with pytest.raises(SystemExit):
        ev.require_frozen(doc, "abc", False)
    doc.write_text("rule changed\n=== RESULTS BELOW ===\nnot run\n")
    with pytest.raises(SystemExit):
        ev.require_frozen(doc, sha, False)
    with pytest.raises(SystemExit):
        ev.main(["run", "--doc", str(doc), "--allow-draft"])
