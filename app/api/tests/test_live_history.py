from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import live, live_history

T = "KXNBAPTS-24NOV12ATLORL-ATLJJOHNSON1-15"


def put(root: Path, stamp: str, bid: str, ask: str, page: str = "p0") -> None:
    d = root / "kalshi" / f"KXNBAPTS_{page}"
    d.mkdir(parents=True, exist_ok=True)
    body = {
        "markets": [
            {"ticker": T, "yes_bid_dollars": bid, "yes_ask_dollars": ask},
            {"ticker": "KXNBAPTS-OTHER-1", "yes_bid_dollars": "0.5", "yes_ask_dollars": "0.6"},
        ]
    }
    with gzip.open(d / f"{stamp}Z.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(body, fh)


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> None:
    live_history.reset()
    live.clear_cache()
    monkeypatch.delenv(live_history.ENV, raising=False)


def test_capture_read_incrementally_and_only_for_asked_tickers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(live_history.ENV, str(tmp_path))
    put(tmp_path, "20241112T180000", "0.60", "0.70")
    put(tmp_path, "20241112T180200", "0.62", "0.72")
    h = live_history.history([T])
    pts = h["series"][T]
    assert h["capture"] is True
    assert [p["yes_bid"] for p in pts] == [0.60, 0.62]
    assert pts[0]["t"] == "2024-11-12T18:00:00+00:00" and pts[0]["src"] == "capture"
    assert "KXNBAPTS-OTHER-1" not in h["series"]
    assert live_history.load_capture([T]) == 0  # nothing new: nothing re-read
    put(tmp_path, "20241112T180400", "0.65", "0.75")
    assert live_history.load_capture([T]) == 1
    assert len(live_history.history([T])["series"][T]) == 3


def test_damaged_file_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live_history.ENV, str(tmp_path))
    put(tmp_path, "20241112T180000", "0.60", "0.70")
    (tmp_path / "kalshi" / "KXNBAPTS_p0" / "20241112T180200Z.json.gz").write_bytes(b"not gzip")
    assert len(live_history.history([T])["series"][T]) == 1


def test_samples_recorded_from_fresh_fetches_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(url: str) -> dict[str, Any]:
        calls.append(url)
        return {"markets": [{"ticker": T, "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.50"}]}

    monkeypatch.setattr(live, "http_get", fake)
    client.get("/api/live/kalshi", params={"tickers": T})
    client.get("/api/live/kalshi", params={"tickers": T})  # cached: not sampled again
    h = client.get("/api/live/history", params={"tickers": T}).json()
    assert h["capture"] is False
    assert len(calls) == 1 and len(h["series"][T]) == 1
    assert h["series"][T][0]["yes_bid"] == 0.40 and h["series"][T][0]["src"] == "sample"


def test_history_validation(client: TestClient) -> None:
    assert client.get("/api/live/history", params={"tickers": "a b"}).status_code == 422
    assert client.get("/api/live/history", params={"tickers": ""}).status_code == 422


def test_downsampled_to_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(live_history.MAX_POINTS + 50):
        live_history._samples[T].append(
            {"t": f"2024-11-12T00:{i:05d}", "yes_bid": 0.5, "yes_ask": 0.6, "src": "sample"}
        )
    assert len(live_history.history([T])["series"][T]) == live_history.MAX_POINTS
