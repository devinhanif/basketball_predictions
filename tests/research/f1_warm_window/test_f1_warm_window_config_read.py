"""Functional: gate and run read the PINNED configs from a foreign cwd and parse `model`.

Catches a misplaced parenthesis in the config read (F1 re-check, 2026-10-15)."""

from pathlib import Path

import pytest
import yaml

from research.eval import f1_warm_window_gate as G
from research.eval import f1_warm_window_run as R

REPO = Path(G.__file__).resolve().parents[2]
WANT = yaml.safe_load((REPO / "configs/context_residual.yaml").read_text())["model"]


class Stop(Exception):
    pass


def _recorder(seen):
    real = G.ContextResidualConfig

    def rec(**kw):
        seen.append(kw)
        return real(**kw)

    return rec


def test_gate_build_and_stamp_exposure_read_pinned_configs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no configs/ here
    seen = []
    monkeypatch.setattr(G, "ContextResidualConfig", _recorder(seen))

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(G, "load_inputs", boom)
    with pytest.raises(Stop):
        G.build(tmp_path, ["x.duckdb"])
    with pytest.raises(Stop):
        G.stamp_exposure(tmp_path)
    assert seen == [WANT, WANT]


def test_run_load_frames_reads_pinned_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(R, "ContextResidualConfig", _recorder(seen))

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(R, "_frame", boom)
    with pytest.raises(Stop):
        R.load_frames(tmp_path)
    assert seen == [WANT]
