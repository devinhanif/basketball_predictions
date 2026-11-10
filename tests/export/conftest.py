"""Export tests never read the real repo data/ for the player-name fallback."""

from __future__ import annotations

import pytest

from nba.explain import local_names


@pytest.fixture(autouse=True)
def _no_real_local_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_names, "load_local_names", lambda *a, **k: local_names.LocalNames())
