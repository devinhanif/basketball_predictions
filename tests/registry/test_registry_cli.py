"""Operator CLI / ops / bootstrap tests. Temp DB + tmp store only; never nba.duckdb."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.db.connect import connect
from nba.registry import bootstrap as bs
from nba.registry.__main__ import main
from nba.registry.local import LocalRegistry
from nba.registry.ops import (
    check_promotable,
    fetch_versions,
    gate_local,
    key_metrics,
    parse_tags,
)
from tests.fixtures.loader import build_fixture_db

FULL_METRICS: dict[str, Any] = {
    "log_loss": 0.62,
    "brier": 0.21,
    "ece": 0.02,
    "n_games": 3700,
    "n_seasons": 3,
    "backtest_scope": "multi_season",
}
FULL_META: dict[str, object] = {"game_date_min": "2022-10-18", "game_date_max": "2025-06-01"}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.duckdb"
    connect(path).close()
    return path


def _log(
    db: Path,
    tmp_path: Path,
    metrics: dict[str, Any] | None = None,
    meta: dict[str, object] | None = None,
    tags: dict[str, object] | None = None,
    name: str = "m",
) -> str:
    art = tmp_path / "art"
    art.mkdir(exist_ok=True)
    (art / "config.yaml").write_text("a: 1\n")
    con = duckdb.connect(str(db))
    try:
        reg = LocalRegistry(con, tmp_path / "store")
        v = reg.next_version(name)
        reg.log_model(
            name, v, str(art), metrics or FULL_METRICS, meta or FULL_META, tags or {"rung": 0}
        )
        return v
    finally:
        con.close()


def _args(db: Path, tmp_path: Path, *rest: str) -> list[str]:
    return ["--db-path", str(db), "--store-dir", str(tmp_path / "store"), *rest]


def test_list_and_show(db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(db, tmp_path)
    assert main(_args(db, tmp_path, "list")) == 0
    out = capsys.readouterr().out
    assert "m" in out and "v1" in out and "candidate" in out and "0.6200" in out
    assert main(_args(db, tmp_path, "show", "m", "v1")) == 0
    assert "promotable: True" in capsys.readouterr().out
    assert main(_args(db, tmp_path, "show", "m", "--alias", "production")) == 1  # none yet


def test_promote_flow_deprecates_previous(db: Path, tmp_path: Path) -> None:
    _log(db, tmp_path)
    _log(db, tmp_path)
    assert main(_args(db, tmp_path, "promote", "m", "v1")) == 0
    assert main(_args(db, tmp_path, "promote", "m", "v2")) == 0
    ro = duckdb.connect(str(db), read_only=True)
    stages = {r.version: r.stage for r in fetch_versions(ro)}
    ro.close()
    assert stages == {"v1": "deprecated", "v2": "production"}


def test_promote_refuses_partial_backtest(db: Path, tmp_path: Path) -> None:
    _log(db, tmp_path, metrics={"log_loss": 0.6, "n_games": 12}, meta={})
    assert main(_args(db, tmp_path, "promote", "m", "v1")) == 1
    ro = duckdb.connect(str(db), read_only=True)
    assert fetch_versions(ro)[0].stage == "candidate"
    ro.close()


def test_promote_rule_variants(db: Path, tmp_path: Path) -> None:
    _log(db, tmp_path, metrics={"log_loss": 0.6, "n_games": 3000}, meta=FULL_META)  # derived
    _log(
        db,
        tmp_path,
        metrics={"log_loss": 0.6, "n_games": 3000},
        meta={"game_date_min": "2024-10-01", "game_date_max": "2025-06-01"},
    )
    _log(db, tmp_path, metrics={**FULL_METRICS, "n_seasons": 2}, meta={})
    _log(db, tmp_path, metrics={**FULL_METRICS, "log_loss": float("nan")})
    _log(db, tmp_path, tags={"rung": 0, "scenario": True})
    ro = duckdb.connect(str(db), read_only=True)
    ok = [check_promotable(r)[0] for r in fetch_versions(ro)]
    ro.close()
    assert ok == [True, False, False, False, False]


def test_scenario_promote_guard(db: Path, tmp_path: Path) -> None:
    _log(db, tmp_path)
    assert main(_args(db, tmp_path, "promote", "m", "v1", "--scenario")) == 1
    ro = duckdb.connect(str(db), read_only=True)
    assert fetch_versions(ro)[0].stage == "candidate"
    ro.close()
    art = tmp_path / "ext"
    art.mkdir()
    (art / "metrics.json").write_text(json.dumps(FULL_METRICS))
    assert (
        main(_args(db, tmp_path, "import-artifacts", "m2", str(art), "--scenario", "--promote"))
        == 1
    )


def test_compare(db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(db, tmp_path)
    _log(db, tmp_path, metrics={**FULL_METRICS, "log_loss": 0.61})
    assert main(_args(db, tmp_path, "compare", "m", "v1", "v2")) == 0
    out = capsys.readouterr().out
    assert "-0.0100" in out and "better" in out


def test_import_artifacts_roundtrip_and_idempotent(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ext = tmp_path / "colab"
    ext.mkdir()
    (ext / "metrics.json").write_text(json.dumps(FULL_METRICS))
    (ext / "config.json").write_text('{"lr": 0.001}')
    (ext / "weights.pt").write_bytes(b"x" * 100)
    cmd = _args(
        db,
        tmp_path,
        "import-artifacts",
        "rung4_deepsets",
        str(ext),
        "--tags",
        "rung=4",
        "model_method=deepsets",
    )
    assert main(cmd) == 0
    assert main(cmd) == 0  # same content -> no new version
    out = capsys.readouterr().out
    assert "imported: rung4_deepsets v1" in out and "already imported" in out
    ro = duckdb.connect(str(db), read_only=True)
    rows = fetch_versions(ro, "rung4_deepsets")
    ro.close()
    assert len(rows) == 1 and rows[0].stage == "candidate"
    assert rows[0].tags["rung"] == 4 and rows[0].tags["model_method"] == "deepsets"
    assert (tmp_path / "store" / "rung4_deepsets" / "v1" / "weights.pt").exists()


def test_import_rejects_missing_metrics_oversize_and_parquet(db: Path, tmp_path: Path) -> None:
    ext = tmp_path / "bad"
    ext.mkdir()
    assert main(_args(db, tmp_path, "import-artifacts", "x", str(ext))) == 1  # no metrics.json
    (ext / "metrics.json").write_text(json.dumps(FULL_METRICS))
    (ext / "data.parquet").write_bytes(b"0")
    assert main(_args(db, tmp_path, "import-artifacts", "x", str(ext))) == 1
    (ext / "data.parquet").unlink()
    con = duckdb.connect(str(db))
    from nba.registry.ops import import_artifacts

    reg = LocalRegistry(con, tmp_path / "store", max_artifact_bytes=10)
    (ext / "w.pt").write_bytes(b"0" * 50)
    with pytest.raises(ValueError, match="cheapness cap"):
        import_artifacts(reg, con, "x", ext, {})
    con.close()


def test_parse_tags() -> None:
    assert parse_tags(["rung=4", "m=deepsets", 'f=["a"]']) == {
        "rung": 4,
        "m": "deepsets",
        "f": ["a"],
    }
    with pytest.raises(ValueError):
        parse_tags(["bad"])


def test_gate_local(db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(db, tmp_path)
    _log(db, tmp_path, metrics={**FULL_METRICS, "log_loss": 0.63})
    assert main(_args(db, tmp_path, "promote", "m", "v1")) == 0
    assert main(_args(db, tmp_path, "gate")) == 1  # v2 worse by 0.01 > 0.002
    assert "log_loss" in capsys.readouterr().out
    _log(db, tmp_path, metrics={**FULL_METRICS, "log_loss": 0.619})
    assert main(_args(db, tmp_path, "gate", "--candidate", "v3")) == 0
    ro = duckdb.connect(str(db), read_only=True)
    f, notes = gate_local(ro, {"log_loss_tolerance": 0.0}, "nonexistent")
    ro.close()
    assert f == [] and notes == []


def test_key_metrics_props_style() -> None:
    km = key_metrics(
        {"stats": [{"stat": "pts", "n": 5, "crps": 2.0}, {"stat": "r", "n": 7, "crps": 4.0}]}
    )
    assert km == {"crps_mean": 3.0, "n": 5.0}


def test_compute_mov_elo_metrics_on_fixture() -> None:
    con = build_fixture_db(":memory:")
    from nba.eval.walkforward import split_frozen_holdout
    from nba.features.team_features import build_matchup_features

    df = split_frozen_holdout(build_matchup_features(con), None).tunable_df
    con.close()
    params = bs.load_params(bs.REPO_ROOT / bs.MOV_ELO_CONFIG)
    m = bs.compute_mov_elo_oof_metrics(df, params)
    assert 0 < m["log_loss"] < 2 and m["backtest_scope"] == "partial"


def test_bootstrap_idempotent_and_promotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "fx.duckdb"
    build_fixture_db(path).close()
    store = tmp_path / "store"
    # Fixture is tiny -> real metrics are "partial": must NOT promote.
    log1 = bs.bootstrap_production(path, store, holdout_season=9999)
    assert any("NOT PROMOTED" in line for line in log1)
    # Force a multi-season result: re-run uses same fingerprint -> reuse + promote.
    monkeypatch.setattr(
        bs,
        "compute_mov_elo_oof_metrics",
        lambda df, p, s=0: {**FULL_METRICS},
    )
    path2 = tmp_path / "fx2.duckdb"
    build_fixture_db(path2).close()
    log2 = bs.bootstrap_production(path2, tmp_path / "s2", holdout_season=9999)
    assert any("promoted rung0_mov_elo v1 -> production" in line for line in log2)
    log3 = bs.bootstrap_production(path2, tmp_path / "s2", holdout_season=9999)
    assert any("already registered" in line for line in log3)
    assert any("already production" in line for line in log3)
    ro = duckdb.connect(str(path2), read_only=True)
    rows = fetch_versions(ro, "rung0_mov_elo")
    ro.close()
    assert len(rows) == 1 and rows[0].stage == "production"
    assert rows[0].metrics["holdout_log_loss"] == 0.6010


def test_props_gate() -> None:
    good = {"stats": [{"stat": "pts", "mean_bias": 0.1, "coverage": 0.79}]}
    assert bs._props_gate(good)[0]
    bad = {"stats": [{"stat": "pts", "mean_bias": 0.1, "coverage": 0.54}]}
    ok, why = bs._props_gate(bad)
    assert not ok and "coverage" in why
