"""Basketball-Reference coach pages: parse, polite write-once fetch, reference rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from nba.data import coaches_br
from nba.data.coaches_br import build_reference_rows, fetch_all, parse_coaches

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "coaches_br"


def test_parse_keeps_tenure_order_and_records() -> None:
    got = parse_coaches((FIX / "ATL_2023.html").read_text())
    assert got == [("Nate McMillan", 22, 37), ("Joe Prunty", 1, 1), ("Quin Snyder", 18, 3)]


def test_parse_handles_line_breaks_and_entities() -> None:
    got = parse_coaches((FIX / "BRK_2024.html").read_text())
    assert got == [("Jacque Vaughn", 21, 33), ("Kevin Ollie", 11, 17)]  # first Coach line only
    assert parse_coaches(
        '<p><strong>Coach:</strong> <a href="/x">Jordi Fern&aacute;ndez</a> (26-56)</p>'
    ) == [("Jordi Fernández", 26, 56)]


@pytest.mark.parametrize("page", ["<html></html>", "<p><strong>Coach:</strong> nobody</p>"])
def test_parse_fails_loudly(page: str) -> None:
    with pytest.raises(ValueError):
        parse_coaches(page)


class _Resp:
    def __init__(self, code: int, text: str) -> None:
        self.status_code, self.text, self.content = code, text, text.encode()


class _Client:
    def __init__(self, script: dict[str, _Resp]) -> None:
        self.script, self.urls = script, []  # type: ignore[var-annotated]

    def get(self, url: str) -> _Resp:
        self.urls.append(url)
        return self.script.get(url, _Resp(404, ""))


def test_fetch_is_write_once_resumable_and_never_caches_bad_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        coaches_br, "TEAMS", {1610612737: ("ATL", "ATL"), 1610612751: ("BKN", "BRK")}
    )
    good = (FIX / "ATL_2023.html").read_text()
    url_atl = coaches_br.URL.format(br="ATL", year=2023)
    url_brk = coaches_br.URL.format(br="BRK", year=2023)
    client = _Client(
        {url_atl: _Resp(200, good), url_brk: _Resp(200, "<html>challenge page</html>")}
    )
    c1 = fetch_all(tmp_path, (2022,), interval_s=0, client=client)
    assert c1 == {"cached": 0, "fetched": 1, "failed": 1}
    assert (tmp_path / "ATL_2023.html").exists() and not (tmp_path / "BRK_2023.html").exists()
    # rerun: the cached page is not requested again, the failed one is retried
    client.urls.clear()
    c2 = fetch_all(tmp_path, (2022,), interval_s=0, client=client)
    assert client.urls == [url_brk] and c2["cached"] == 1
    assert len((tmp_path / "fetch_log.csv").read_text().splitlines()) == 2  # header + one fetch


def test_429_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        coaches_br, "TEAMS", {1610612737: ("ATL", "ATL"), 1610612751: ("BKN", "BRK")}
    )
    client = _Client({coaches_br.URL.format(br="ATL", year=2023): _Resp(429, "")})
    out = fetch_all(tmp_path, (2022,), interval_s=0, client=client)
    assert out["failed"] == 1 and len(client.urls) == 1  # stopped, did not go on to BRK


def test_reference_rows_have_cumulative_game_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coaches_br, "TEAMS", {1610612737: ("ATL", "ATL")})
    (tmp_path / "ATL_2023.html").write_text((FIX / "ATL_2023.html").read_text())
    (tmp_path / "fetch_log.csv").write_text(
        "file,url,fetched_utc,bytes,sha256\nATL_2023.html,u,2026-10-11T20:00:00Z,1,x\n"
    )
    rows = build_reference_rows(tmp_path, (2022,))
    assert [(r["coach_name"], r["first_game"], r["last_game"], r["games"]) for r in rows] == [
        ("Nate McMillan", 1, 59, 59),
        ("Joe Prunty", 60, 61, 2),
        ("Quin Snyder", 62, 82, 21),
    ]
    assert {r["fetched_date"] for r in rows} == {"2026-10-11"}
    assert rows[0]["source_url"] == "https://www.basketball-reference.com/teams/ATL/2023.html"
