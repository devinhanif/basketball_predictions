from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from nba.ride import features as feat
from nba.ride.capture_view import CaptureView, Play

from .helpers import T0, put


def play(period: int, clock: float, h: int, a: int, scoring: bool = True) -> Play:
    return Play(period, clock, h, a, None, scoring, "1")


def test_run_features_points_in_last_minutes() -> None:
    plays = (
        play(1, 700.0, 2, 0),
        play(2, 600.0, 30, 30),
        play(2, 400.0, 36, 30),
        play(2, 300.0, 36, 37),
    )
    f = feat.run_features(plays)
    assert f["home_pts_last5m"] == 6.0 and f["away_pts_last5m"] == 7.0
    assert f["margin_chg_last5m"] == -1.0


def test_candles_ohlc_volume_imbalance(tmp_path: Path) -> None:
    base = int(datetime(2026, 10, 23, 1, 30, tzinfo=UTC).timestamp())
    trs = [
        {
            "trade_id": "a",
            "yes_price_dollars": "0.50",
            "count_fp": "10",
            "taker_side": "yes",
            "created_time": "2026-10-23T01:30:05Z",
        },
        {
            "trade_id": "b",
            "yes_price_dollars": "0.55",
            "count_fp": "5",
            "taker_side": "no",
            "created_time": "2026-10-23T01:30:20Z",
        },
        {
            "trade_id": "c",
            "yes_price_dollars": "0.52",
            "count_fp": "5",
            "taker_side": "yes",
            "created_time": "2026-10-23T01:30:50Z",
        },
    ]
    df = feat.candles_from_trades({"T": {base: trs}})
    r = df.iloc[0]
    assert (r.open, r.high, r.low, r.close, r.volume) == (0.50, 0.55, 0.50, 0.52, 20.0)
    assert r.imbalance == (15 - 5) / 20


def test_write_once_never_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "a" / "x.parquet"
    assert feat.write_once(pd.DataFrame({"a": [1]}), p)
    assert not feat.write_once(pd.DataFrame({"a": [2]}), p)
    assert pd.read_parquet(p)["a"].tolist() == [1]


def test_feature_log_flushes_old_minutes_only_once(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    put(
        cap,
        "kalshi_trades",
        "2026-10-22",
        T0,
        {
            "T": [
                {
                    "trade_id": "a",
                    "yes_price_dollars": "0.50",
                    "count_fp": "10",
                    "taker_side": "yes",
                    "created_time": "2026-10-23T01:29:05Z",
                },
                {
                    "trade_id": "b",
                    "yes_price_dollars": "0.60",
                    "count_fp": "1",
                    "taker_side": "no",
                    "created_time": "2026-10-23T01:31:30Z",
                },
            ]
        },
    )
    out = tmp_path / "out"
    log = feat.FeatureLog(CaptureView(cap), out, "2026-10-22")
    now = T0 + timedelta(seconds=75)
    assert log.ingest_trades(now) == 2
    assert log.flush_candles(now)  # minute 01:29 is older than now-1min; 01:31 is not
    assert not log.flush_candles(now)
    files = sorted(out.glob("candles_*.parquet"))
    assert len(files) == 1 and len(pd.read_parquet(files[0])) == 1
    again = feat.FeatureLog(
        CaptureView(cap), out, "2026-10-22"
    )  # restart: the written minute is not rewritten
    again.ingest_trades(now)
    assert not again.flush_candles(now)
