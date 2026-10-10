"""Shared fixtures: a fake ``httpx.get`` (no network ever) and a tmp-dir client config."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from nba.odds.theoddsapi import KEY_ENV, OddsConfig, TheOddsApiClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "odds"
TEST_KEY = "test-key-0123456789-SECRET"


def probe_body(name: str) -> Any:
    with gzip.open(FIX / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)["body"]


def synthetic(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text())


@dataclass
class FakeResponse:
    status_code: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=lambda: {"X-RateLimit-Remaining": "6000"})

    @property
    def text(self) -> str:
        return json.dumps(self.body) if not isinstance(self.body, str) else self.body

    def json(self) -> Any:
        if isinstance(self.body, str):
            raise ValueError("not json")
        return self.body


class FakeHttp:
    """Scripted replacement for ``httpx.get``; records every call (url, params, headers)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: list[Any] = []  # FakeResponse | Exception | Callable[[str], FakeResponse]
        self.routes: dict[str, Callable[[], FakeResponse]] = {}

    def __call__(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append({"url": url, **kw})
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, FakeResponse)
            return item
        for suffix, fn in self.routes.items():
            if url.endswith(suffix):
                return fn()
        raise AssertionError(f"unscripted request to {url}")


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(httpx, "get", fake)
    return fake


@pytest.fixture
def cfg(tmp_path: Path) -> OddsConfig:
    return OddsConfig(
        min_interval_s=0.0,
        retry_backoff_s=0.0,
        raw_dir=tmp_path / "raw",
        budget_dir=tmp_path / "budget",
        out_db=tmp_path / "market.duckdb",
    )


@pytest.fixture
def clock() -> Callable[[], datetime]:
    t = datetime(2026, 10, 20, 20, 0, 5, tzinfo=UTC)
    return lambda: t


@pytest.fixture
def client(
    cfg: OddsConfig, clock: Callable[[], datetime], monkeypatch: pytest.MonkeyPatch
) -> TheOddsApiClient:
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    return TheOddsApiClient(cfg, clock=clock, sleep=lambda _s: None)
