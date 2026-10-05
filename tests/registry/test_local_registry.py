"""Tests for the local registry adapter (nba.registry.local.LocalRegistry).

Every test uses an in-memory DuckDB connection and a pytest ``tmp_path``
artifact store -- never real data, never the project .duckdb file.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from nba.db.connect import connect
from nba.registry.cli_guard import validate_cli_flags
from nba.registry.factory import get_registry
from nba.registry.local import LocalRegistry


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def registry(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> LocalRegistry:
    return LocalRegistry(con, tmp_path / "registry_store")


def _make_artifact(tmp_path: Path, name: str = "weights.pt", size_bytes: int = 10) -> Path:
    model_dir = tmp_path / name
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"0" * size_bytes)
    (model_dir / "config.yaml").write_text("lr: 0.1\n")
    (model_dir / "report.md").write_text("# report\n")
    return model_dir


def test_next_version_starts_at_v1_for_unknown_model(registry: LocalRegistry) -> None:
    assert registry.next_version("rung0_home_court") == "v1"


def test_next_version_increments_from_existing_rows(
    registry: LocalRegistry, tmp_path: Path
) -> None:
    artifact = _make_artifact(tmp_path)
    registry.log_model("rung0_home_court", "v1", str(artifact), {}, {}, {})
    assert registry.next_version("rung0_home_court") == "v2"
    registry.log_model("rung0_home_court", "v2", str(artifact), {}, {}, {})
    assert registry.next_version("rung0_home_court") == "v3"
    # A different model name starts its own sequence.
    assert registry.next_version("rung1_logistic") == "v1"


def test_log_model_writes_db_row_and_store_files(
    registry: LocalRegistry, con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    artifact = _make_artifact(tmp_path)
    registry.log_model(
        "rung1_logistic",
        "v1",
        str(artifact),
        metrics={"log_loss": 0.65},
        metadata={"seed": 7},
        tags={"rung": 1, "model_method": "logistic", "source_config": "configs/rung1.yaml"},
    )

    row = con.execute(
        "SELECT stage, alias, version, metrics, tags, metadata, artifact_path "
        "FROM experiments WHERE model_name = ?",
        ["rung1_logistic"],
    ).fetchone()
    assert row is not None
    stage, alias, version, metrics_json, tags_json, metadata_json, artifact_path = row
    assert stage == "candidate"
    assert alias == "candidate"
    assert version == "v1"
    assert metrics_json is not None and "log_loss" in metrics_json
    assert tags_json is not None and "logistic" in tags_json
    assert metadata_json is not None and "7" in metadata_json

    stored = Path(artifact_path)
    assert (stored / "weights.bin").exists()
    assert (stored / "config.yaml").exists()
    assert (stored / "report.md").exists()


def test_log_model_refuses_oversized_artifact(registry: LocalRegistry, tmp_path: Path) -> None:
    artifact = _make_artifact(tmp_path, name="big", size_bytes=1)
    small_registry = LocalRegistry(registry._con, tmp_path / "small_store", max_artifact_bytes=0)
    with pytest.raises(ValueError, match="cheapness cap"):
        small_registry.log_model("rung1_logistic", "v1", str(artifact), {}, {}, {})


def test_log_model_refuses_raw_data_artifacts(registry: LocalRegistry, tmp_path: Path) -> None:
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "snapshot.parquet").write_bytes(b"x")
    with pytest.raises(ValueError, match="raw data"):
        registry.log_model("rung1_logistic", "v1", str(bad_dir), {}, {}, {})


def test_set_alias_updates_only_targeted_version(
    registry: LocalRegistry, con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    artifact = _make_artifact(tmp_path)
    registry.log_model("rung1_logistic", "v1", str(artifact), {}, {}, {})
    registry.set_alias("rung1_logistic", "v1", "shadow")
    alias = con.execute(
        "SELECT alias FROM experiments WHERE model_name = ? AND version = ?",
        ["rung1_logistic", "v1"],
    ).fetchone()[0]
    assert alias == "shadow"


def test_set_alias_unknown_version_raises(registry: LocalRegistry) -> None:
    with pytest.raises(LookupError):
        registry.set_alias("rung1_logistic", "v99", "candidate")


def test_promote_deprecates_previous_production(
    registry: LocalRegistry, con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    artifact = _make_artifact(tmp_path)
    registry.log_model("rung1_logistic", "v1", str(artifact), {}, {}, {})
    registry.log_model("rung1_logistic", "v2", str(artifact), {}, {}, {})
    registry.promote("rung1_logistic", "v1")
    registry.promote("rung1_logistic", "v2")

    rows = con.execute(
        "SELECT version, stage FROM experiments WHERE model_name = ? ORDER BY version",
        ["rung1_logistic"],
    ).fetchall()
    stages = dict(rows)
    assert stages["v1"] == "deprecated"
    assert stages["v2"] == "production"

    production_rows = con.execute(
        "SELECT COUNT(*) FROM experiments WHERE model_name = ? AND stage = 'production'",
        ["rung1_logistic"],
    ).fetchone()[0]
    assert production_rows == 1


def test_promote_unknown_version_raises(registry: LocalRegistry) -> None:
    with pytest.raises(LookupError):
        registry.promote("rung1_logistic", "v1")


def test_load_model_defaults_to_production(registry: LocalRegistry, tmp_path: Path) -> None:
    artifact = _make_artifact(tmp_path)
    registry.log_model("rung1_logistic", "v1", str(artifact), {}, {}, {})
    registry.promote("rung1_logistic", "v1")
    path = registry.load_model("rung1_logistic")
    assert Path(path).exists()
    assert path.endswith("v1")


def test_load_model_missing_alias_raises(registry: LocalRegistry) -> None:
    with pytest.raises(LookupError):
        registry.load_model("rung1_logistic")


def test_validate_cli_flags_rejects_scenario_and_promote() -> None:
    with pytest.raises(ValueError, match="scenario"):
        validate_cli_flags(promote=True, scenario=True)
    # All other combinations are fine.
    validate_cli_flags(promote=True, scenario=False)
    validate_cli_flags(promote=False, scenario=True)
    validate_cli_flags(promote=False, scenario=False)


def test_get_registry_unknown_backend_raises(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REGISTRY_BACKEND", "snowflake")
    with pytest.raises(NotImplementedError):
        get_registry(con)


def test_get_registry_defaults_to_local(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REGISTRY_BACKEND", raising=False)
    adapter = get_registry(con, store_dir=tmp_path / "store")
    assert isinstance(adapter, LocalRegistry)
