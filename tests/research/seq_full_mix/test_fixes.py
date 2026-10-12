# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""Adversary review fixes (docs/reviews/seq_full_mix_loader_review_2026-10-12.md): B1 resume identity, B2 K, N1 preflight, N2/N6 season bound, N3 per-program
Colab evidence, N4/N5 export provenance."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.eval import seq_full_mix_descriptive as D
from research.eval import seq_full_mix_eval as S

SHA = "a" * 64


def _marker(**over):
    m = {
        "sha256": SHA, "head": "h1", "code_diff_sha256": "d1", "seq_run": "/x/run", "export": {"npz_sha256": "n", "meta_sha256": "m"},
        "seq_files": {"oof_E2.npz": "1", "oof_E2P.npz": "2", "oof_E2S.npz": "3", "config.json": "4"},
    }  # fmt: skip
    return m | over


# --------------------------------------------------------------------------- B1
def test_resume_refuses_a_failed_gate_even_if_the_touch_was_not_consumed(tmp_path: Path) -> None:
    (tmp_path / "gate_failed.json").write_text(json.dumps({"touch_consumed": False}))
    for resume in (False, True):
        with pytest.raises(RuntimeError, match="new id"):
            S.require_unspent(tmp_path, resume, _marker())


@pytest.mark.parametrize(
    "key,value",
    [
        ("sha256", "b" * 64),
        ("head", "h2"),
        ("code_diff_sha256", "d2"),
        ("seq_run", "/y/run"),
        ("export", {"npz_sha256": "z", "meta_sha256": "m"}),
        (
            "seq_files",
            {"oof_E2.npz": "CHANGED", "oof_E2P.npz": "2", "oof_E2S.npz": "3", "config.json": "4"},
        ),
    ],
)
def test_resume_must_repeat_the_started_touch_exactly(tmp_path: Path, key: str, value) -> None:
    (tmp_path / "touch_started.json").write_text(json.dumps(_marker()))
    S.require_unspent(tmp_path, True, _marker())  # identical: allowed
    with pytest.raises(RuntimeError, match="repeat the started touch exactly"):
        S.require_unspent(tmp_path, True, _marker(**{key: value}))
    with pytest.raises(RuntimeError, match="consumed"):
        S.require_unspent(tmp_path, False, _marker())
    with pytest.raises(RuntimeError, match="needs the identity"):
        S.require_unspent(tmp_path, True, None)


def test_marker_is_never_overwritten_and_resumes_are_appended(tmp_path: Path) -> None:
    first = S.write_marker(tmp_path, _marker())
    body = first.read_text()
    second = S.write_marker(tmp_path, _marker(head="h2"))
    third = S.write_marker(tmp_path, _marker(head="h3"))
    assert first.name == "touch_started.json" and first.read_text() == body
    assert second.name == "touch_resumed_1.json" and third.name == "touch_resumed_2.json"
    assert json.loads(second.read_text())["head"] == "h2"


def test_marker_info_hashes_the_oof_files_and_the_config(tmp_path: Path) -> None:
    for n in ("oof_E2.npz", "oof_E2P.npz", "oof_E2S.npz", "config.json"):
        (tmp_path / n).write_bytes(n.encode())
    a = S.marker_info(SHA, tmp_path, {"npz_sha256": "n", "meta_sha256": "m"})
    (tmp_path / "oof_E2P.npz").write_bytes(b"changed")
    b = S.marker_info(SHA, tmp_path, {"npz_sha256": "n", "meta_sha256": "m"})
    assert (
        a["seq_files"]["oof_E2P.npz"] != b["seq_files"]["oof_E2P.npz"]
        and a["seq_files"]["oof_E2.npz"] == b["seq_files"]["oof_E2.npz"]
    )
    assert a["seq_run"] == str(tmp_path.resolve()) and set(S.MARKER_KEYS) <= set(a)


# --------------------------------------------------------------------------- B2
def test_held_out_run_refuses_any_k_but_50(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="K = 50"):
        S.run_held_out(SHA, tmp_path, False, null_k=3)


# --------------------------------------------------------------------------- N1, N3, N4, N5
def _run_dir(tmp: Path, **over) -> tuple[Path, dict]:
    run = tmp / "run"
    run.mkdir()
    for p in ("E2", "E2P", "E2S"):
        np.savez(run / f"oof_{p}.npz", season=np.array([2023, 2024, 2025]))
        info = {
            "seeds": [0, 1, 2],
            "hidden": 64,
            "smoke": False,
            "rule_sha256": SHA,
            "blocks_detail": [{"max_pool_date": "2025-01-31", "min_block_date": "2025-02-01"}],
        }
        (run / f"info_{p}.json").write_text(json.dumps(info | over.get(p, {})))
    cfg = {
        "rule_sha256": SHA,
        "seeds": [0, 1, 2],
        "hidden": 64,
        "test_seasons": [2023, 2024, 2025],
        "season_2025_used": True,
        "smoke": False,
        "export_npz_sha256": "N",
    } | over.get("cfg", {})
    (run / "config.json").write_text(json.dumps(cfg))
    return run, {"npz_sha256": "N", "meta_sha256": "M"}


def test_seq_run_is_checked_per_program(tmp_path: Path) -> None:
    run, exp = _run_dir(tmp_path)
    S.check_seq_run(run, SHA, exp)
    for i, (over, what) in enumerate(
        [
            ({"E2P": {"seeds": [0]}}, "E2P.seeds"), ({"E2S": {"hidden": 16}}, "E2S.hidden"), ({"E2": {"smoke": True}}, "E2.smoke"),
            ({"E2": {"blocks_detail": [{"max_pool_date": "2025-02-01", "min_block_date": "2025-02-01"}]}}, "pool_before_block"),
            ({"E2": {"blocks_detail": []}}, "pool_before_block"), ({"E2P": {"rule_sha256": "b" * 64}}, "E2P.rule_sha256"),
            ({"cfg": {"export_npz_sha256": "OTHER"}}, "export_npz_sha256"), ({"cfg": {"smoke": "100"}}, "smoke"), ({"cfg": {"seeds": [0]}}, "seeds"),
        ]
    ):  # fmt: skip
        d = tmp_path / f"c{i}"
        d.mkdir()
        run2, _ = _run_dir(d, **over)
        with pytest.raises(RuntimeError, match="full-budget"):
            S.check_seq_run(run2, SHA, exp)
        try:
            S.check_seq_run(run2, SHA, exp)
        except RuntimeError as e:
            assert what in str(e)
    run3, _ = _run_dir(tmp_path / "x") if (tmp_path / "x").mkdir() is None else (None, None)
    np.savez(run3 / "oof_E2.npz", season=np.array([2023, 2024]))  # missing 2025 forecasts
    with pytest.raises(RuntimeError, match="oof_seasons"):
        S.check_seq_run(run3, SHA, exp)
    (run3 / "info_E2S.json").unlink()
    with pytest.raises(RuntimeError, match="info_E2S"):
        S.check_seq_run(run3, SHA, exp)


def test_export_must_match_what_the_exporter_described_and_what_colab_used(tmp_path: Path) -> None:
    from research.eval.seq_full_mix_rule import file_sha256

    npz, meta, info = tmp_path / "e.npz", tmp_path / "m.parquet", tmp_path / "export_info.json"
    npz.write_bytes(b"npz"), meta.write_bytes(b"meta")
    good = {
        "npz_sha256": file_sha256(npz),
        "meta_sha256": file_sha256(meta),
        "identity_vs_stored_export": {"share_rows_equal": {"seq": 1.0}},
    }
    info.write_text(json.dumps(good))
    got = S.check_export({"export_npz_sha256": good["npz_sha256"]}, info, npz, meta)
    assert got["identity_vs_stored_export"] == {
        "share_rows_equal": {"seq": 1.0}
    }  # carried into results.json
    with pytest.raises(RuntimeError, match="different export"):
        S.check_export({"export_npz_sha256": "other"}, info, npz, meta)
    npz.write_bytes(b"re-exported")
    with pytest.raises(RuntimeError, match="not the one export_info"):
        S.check_export(None, info, npz, meta)
    meta.write_bytes(b"x")
    npz.write_bytes(b"npz")
    with pytest.raises(RuntimeError, match="not the one export_info"):
        S.check_export(None, info, npz, meta)


def test_preflight_refuses_before_the_touch_when_an_input_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run_dir(tmp_path)
    monkeypatch.setattr(S, "EXPORT25_META", tmp_path / "nope.parquet")
    with pytest.raises(RuntimeError, match="preflight refuses"):
        S.preflight(run, SHA)
    for name in ("EXPORT25_NPZ", "EXPORT25_INFO", "T473"):
        monkeypatch.setattr(S, name, tmp_path / "nope2")
    with pytest.raises(RuntimeError, match="preflight refuses"):
        S.preflight(run, SHA)


# --------------------------------------------------------------------------- N2 / N6
def test_descriptive_refuses_a_frame_beyond_the_target_season() -> None:
    D.assert_bounded(pd.DataFrame({"season": [2022, 2024]}), 2024, "games")
    with pytest.raises(RuntimeError, match="season 2025 > target 2024"):
        D.assert_bounded(pd.DataFrame({"season": [2024, 2025]}), 2024, "games")
    D.assert_bounded(pd.DataFrame({"season": []}), 2024, "empty")


def test_descriptive_queries_never_load_later_seasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    import duckdb

    con = duckdb.connect(str(tmp_path / "nba.duckdb"))
    con.execute(
        "CREATE TABLE games(game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, away_team INTEGER)"
    )
    con.execute(
        "CREATE TABLE player_game_stats(game_id VARCHAR, player_id INTEGER, team_id INTEGER, minutes DOUBLE)"
    )
    for i, s in enumerate((2023, 2024, 2025, 2026)):
        con.execute(
            "INSERT INTO games VALUES (?,?,?,?,?)", [f"G{i}", dt.date(2023 + i, 11, 1), s, 1, 2]
        )
        con.execute("INSERT INTO player_game_stats VALUES (?,1,1,30.0)", [f"G{i}"])
    con.close()
    seen = {}

    def spy(pgs, games):
        seen["pgs"], seen["games"] = pgs, games
        raise StopIteration  # stop right after the queries

    monkeypatch.setattr(D, "context_flags", spy)
    verdict = tmp_path / "stage2" / "results.json"
    verdict.parent.mkdir()
    verdict.write_text(json.dumps({"verdict": {}}))
    with pytest.raises(StopIteration):
        D.run_descriptive(
            {},
            2024,
            verdict.parent,
            verdict,
            str(tmp_path / "nba.duckdb"),
            tmp_path / "o.duckdb",
            None,
        )
    assert int(seen["games"]["season"].max()) == 2024 and int(seen["pgs"]["season"].max()) == 2024
