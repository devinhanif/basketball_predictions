"""History injury-report PDF fetcher: seed/hourly probing, resumability, loader guard."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import httpx
import pytest

from nba.ingest import history_injury as hi


def _client(published: set[str], calls: list[str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        calls.append(name)
        if name in published:
            return httpx.Response(200, content=b"%PDF-1.4 fake")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _names(d: str, hours: list[str]) -> set[str]:
    return {f"Injury-Report_{d}_{h}.pdf" for h in hours}


def test_filename_roundtrip_and_regime() -> None:
    assert hi.report_dt_from_filename("Injury-Report_2021-11-15_09AM.pdf") == datetime(
        2021, 11, 15, 9
    )
    assert hi.report_dt_from_filename("Injury-Report_2019-11-15_08PM.pdf").hour == 20
    assert hi.is_hourly_regime([11, 13, 14, 17])
    assert not hi.is_hourly_regime([11, 14, 17])  # bubble cadence
    assert not hi.is_hourly_regime([13, 17, 20])  # 2019-21 cadence


def test_sparse_era_probes_only_seeds(tmp_path: Path) -> None:
    calls: list[str] = []
    pub = _names("2019-11-15", ["01PM", "05PM", "08PM"])
    res = hi.fetch_seasons(
        [2019],
        data_dir=tmp_path,
        rate_limit_s=0,
        dates=[date(2019, 11, 15)],
        client=_client(pub, calls),
        progress_every=0,
    )
    assert res.dates_done == 1 and res.pdfs_saved == 3
    assert len(calls) == len(hi.SEED_HOURS)
    assert {p.name for p in (tmp_path / hi.RAW_SUBDIR).glob("*.pdf")} == pub


def test_hourly_era_probes_all_hours_and_is_resumable(tmp_path: Path) -> None:
    calls: list[str] = []
    hours = [f"{h:02d}{'AM' if h < 12 else 'PM'}" for h in (6, 7, 8, 9, 10, 11)]
    hours += ["12PM", "01PM", "02PM", "03PM", "05PM", "08PM"]
    pub = _names("2021-11-15", hours)
    client = _client(pub, calls)
    kw = {"data_dir": tmp_path, "rate_limit_s": 0, "dates": [date(2021, 11, 15)], "client": client}
    res = hi.fetch_seasons([2021], progress_every=0, **kw)  # type: ignore[arg-type]
    assert res.pdfs_saved == len(pub)
    assert len(calls) == len(hi.ALL_HOURS)  # each hour exactly once (seeds not re-probed)
    n = len(calls)
    res2 = hi.fetch_seasons([2021], progress_every=0, **kw)  # type: ignore[arg-type]
    assert res2.dates_skipped == 1 and len(calls) == n  # manifest => no network


def test_off_day_recorded_no_report(tmp_path: Path) -> None:
    res = hi.fetch_seasons(
        [2019],
        data_dir=tmp_path,
        rate_limit_s=0,
        dates=[date(2019, 12, 24)],
        client=_client(set(), []),
        progress_every=0,
    )
    assert res.dates_no_report == 1 and res.dates_failed == 0


def test_html_200_placeholder_is_absent(tmp_path: Path) -> None:
    c = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>"))
    )
    res = hi.fetch_seasons(
        [2019],
        data_dir=tmp_path,
        rate_limit_s=0,
        dates=[date(2019, 12, 24)],
        client=c,
        progress_every=0,
    )
    assert res.pdfs_saved == 0 and res.dates_no_report == 1


def test_server_error_not_recorded_as_done(tmp_path: Path) -> None:
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    res = hi.fetch_seasons(
        [2019],
        data_dir=tmp_path,
        rate_limit_s=0,
        dates=[date(2019, 12, 24)],
        client=c,
        progress_every=0,
        max_consecutive_errors=1,
        sleep=lambda s: None,
    )
    assert res.dates_failed == 1
    assert not list((tmp_path / hi.MANIFEST_SUBDIR).glob("*.json"))


def test_windows_skip_covid_stoppage() -> None:
    d = set(hi.season_dates(2019))
    assert date(2020, 3, 12) in d and date(2020, 4, 15) not in d and date(2020, 7, 30) in d
    assert min(hi.season_dates(2021)) == date(2021, 10, 10)


def test_loader_refuses_without_history_box_scores() -> None:
    from nba.db.connect import connect

    con: duckdb.DuckDBPyConnection = connect(":memory:")
    with pytest.raises(RuntimeError, match="player_game_stats"):
        hi.build_history_resolver(con)
