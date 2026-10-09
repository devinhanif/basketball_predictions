"""Tests for the audit-metadata / tags helpers."""

from __future__ import annotations

import datetime as dt

from nba.registry.metadata import build_run_metadata, build_tags


def test_build_run_metadata_shape() -> None:
    meta = build_run_metadata(
        game_date_min=dt.date(2023, 10, 1),
        game_date_max=dt.date(2023, 12, 1),
        possession_count=12345,
        seed=42,
        gpu=False,
    )
    assert meta["game_date_min"] == "2023-10-01"
    assert meta["game_date_max"] == "2023-12-01"
    assert meta["possession_count"] == 12345
    assert meta["seed"] == 42
    assert meta["gpu"] is False
    assert isinstance(meta["git_sha"], str) and meta["git_sha"]
    assert isinstance(meta["python_version"], str)
    assert isinstance(meta["torch_version"], str)
    assert "logged_at" in meta


def test_build_run_metadata_handles_missing_dates() -> None:
    meta = build_run_metadata(game_date_min=None, game_date_max=None, possession_count=0, seed=0)
    assert meta["game_date_min"] is None
    assert meta["game_date_max"] is None


def test_build_tags_shape() -> None:
    tags = build_tags(
        rung=2,
        model_method="lightgbm",
        cold_start_flags=["shrinkage"],
        source_config="configs/rung2.yaml",
    )
    assert tags == {
        "rung": 2,
        "model_method": "lightgbm",
        "cold_start_flags": ["shrinkage"],
        "source_config": "configs/rung2.yaml",
    }


def test_build_tags_defaults_cold_start_flags() -> None:
    tags = build_tags(rung=0, model_method="elo")
    assert tags["cold_start_flags"] == []


def test_build_run_metadata_data_version_explicit_and_default() -> None:
    explicit = build_run_metadata(
        game_date_min=None, game_date_max=None, possession_count=0, seed=0, data_version="abc123"
    )
    assert explicit["data_version"] == "abc123"
    auto = build_run_metadata(game_date_min=None, game_date_max=None, possession_count=0, seed=0)
    assert "data_version" in auto
