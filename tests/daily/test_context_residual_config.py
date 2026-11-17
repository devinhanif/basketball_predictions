"""``configs/context_residual.yaml`` equals ``ContextResidualConfig``'s defaults, field by field.

Production props fit on the dataclass defaults in code; the P(play) shadow
(``nba.daily.pplay_shadow``) reads the YAML. The two must not drift apart silently (2026-11-17
guard; the YAML is also sha-pinned by research rules, so it is never edited to make this pass).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from nba.daily.pplay_shadow import model as M
from nba.props.context_residual import ContextResidualConfig

ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = ROOT / "configs" / "context_residual.yaml"


def _yaml_model() -> dict[str, object]:
    raw = yaml.safe_load(YAML_PATH.read_text())
    assert set(raw) == {"model"}, f"unexpected top-level keys: {sorted(raw)}"
    model: dict[str, object] = raw["model"]
    return model


def test_yaml_keys_are_dataclass_fields() -> None:
    fields = {f.name for f in dataclasses.fields(ContextResidualConfig)}
    assert set(_yaml_model()) <= fields


@pytest.mark.parametrize("name", sorted(_yaml_model()))
def test_yaml_value_equals_code_default(name: str) -> None:
    yval = _yaml_model()[name]
    default = getattr(ContextResidualConfig(), name)
    assert type(yval) is type(default), f"{name}: yaml {type(yval)} vs code {type(default)}"
    assert yval == default, f"{name}: yaml {yval!r} vs code default {default!r}"


def test_shadow_config_equals_production_defaults() -> None:
    assert M.load_cfg() == ContextResidualConfig()


def test_shadow_reads_the_repo_file_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert M.CONFIG.is_absolute() and YAML_PATH.resolve() == M.CONFIG
    monkeypatch.chdir(tmp_path)
    assert M.load_cfg() == ContextResidualConfig()
