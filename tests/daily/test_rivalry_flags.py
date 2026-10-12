"""Rivalry flags in the P(play) shadow log: the list matches the doc; as-of rules."""

from __future__ import annotations

import datetime as dt
import itertools
import re
from pathlib import Path

import duckdb
import pytest

from nba.daily.pplay_shadow import rivalry, store
from nba.daily.pplay_shadow import run as run_mod
from tests.daily.conftest import RUN_DATE
from tests.daily.test_pplay_shadow import _run

DOC = Path(__file__).resolve().parents[2] / "docs" / "research" / "rivalries_2026-10-09.md"


def _doc_sections() -> tuple[str, str]:
    t = DOC.read_text()
    a, b, c = t.index("## Tier 1"), t.index("## Tier 2"), t.index("## Tier 3")
    return t[a:b], t[b:c]


def test_csv_matches_the_doc_80_rows() -> None:
    t1, t2 = _doc_sections()
    ids: dict[str, int] = {}
    for m in re.finditer(r"^### \w+: (.+)$", t1, re.M):
        ids.update({ab: int(i) for ab, i in re.findall(r"([A-Z]{3}) (\d{10})", m.group(1))})
    assert len(ids) == 30
    doc1 = [
        frozenset((ids[x], ids[y]))
        for x, y, mark in re.findall(r"^\| ([A-Z]{3})–([A-Z]{3}) \|.*\| (\w+) \|$", t1, re.M)
        if mark == "Y"
    ]
    doc2 = {
        tag: frozenset((int(a), int(b)))
        for tag, _x, a, _y, b, mark in re.findall(
            r"^\| (H\d+) \| ([A-Z]{3}) (\d+) – ([A-Z]{3}) (\d+) \|.*\| (\w+) \|$", t2, re.M
        )
        if mark == "Y"
    }
    assert len(doc1) == 60 and len(doc2) == 20
    pairs = rivalry.load_pairs()
    assert len(pairs) == 80
    csv1 = [frozenset((p.a, p.b)) for p in pairs if p.tier == 1]
    csv2 = {p.tag: frozenset((p.a, p.b)) for p in pairs if p.tier == 2}
    assert sorted(map(sorted, csv1)) == sorted(map(sorted, doc1))
    assert len(set(csv1)) == 60 and csv2 == doc2


def test_matches_the_research_flags_on_a_grid() -> None:
    from research.features import rivalry as R

    teams = sorted({t for p in rivalry.load_pairs() for t in (p.a, p.b)})
    assert len(teams) == 30
    # Tier 2 meetings and divisions identical to the research encoding
    assert {
        (p.tag, p.a, p.b, p.meetings, p.older) for p in rivalry.load_pairs() if p.tier == 2
    } == {(p.tag, p.a, p.b, p.meetings, p.older) for p in R.TIER2}
    for d in (
        dt.date(2022, 10, 20),
        dt.date(2024, 1, 15),
        dt.date(2026, 10, 28),
        dt.date(2028, 3, 1),
    ):
        for a, b in itertools.permutations(teams, 2):
            f = R.flags(a, b, d)
            got = rivalry.flags(a, b, d)
            assert (got["div_rival"], got["hist_rival"]) == (f["div_rival"], f["hist_rival"])


def test_asof_by_calendar_year_never_sees_the_current_years_series() -> None:
    okc_sas = next(p for p in rivalry.load_pairs() if p.tag == "H18")  # 2012, 2014, 2016, 2026
    assert rivalry.asof_strength(okc_sas, dt.date(2026, 12, 1)) == 1  # the 2026 series is invisible
    assert rivalry.asof_strength(okc_sas, dt.date(2027, 1, 5)) == 3  # visible from 2027
    h4 = next(p for p in rivalry.load_pairs() if p.tag == "H4")  # NYK-SAS Finals 1999, 2026
    assert rivalry.asof_strength(h4, dt.date(2026, 10, 28)) == 2  # only the 1999 Finals counts
    assert rivalry.hist_rival(h4.a, h4.b, dt.date(2026, 10, 28)) is True
    h7 = next(p for p in rivalry.load_pairs() if p.tag == "H7")  # DEN-MIA, Finals 2023
    assert rivalry.hist_rival(h7.a, h7.b, dt.date(2023, 12, 1)) is False  # same calendar year
    assert rivalry.hist_rival(h7.a, h7.b, dt.date(2024, 1, 15)) is True  # a Finals counts after
    assert rivalry.div_rival(h7.a, h7.a) is False


def test_flags_are_stored_on_every_shadow_row(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int, dt.date]] = []

    def fake(h: int, a: int, d: dt.date) -> dict[str, bool]:
        seen.append((h, a, d))
        return {"div_rival": True, "hist_rival": False}

    monkeypatch.setattr(run_mod.rivalry, "flags", fake)
    _run(con)
    assert seen and all(d == RUN_DATE for _h, _a, d in seen)
    rows = con.execute("SELECT DISTINCT div_rival, hist_rival FROM forward_pplay_shadow").fetchall()
    assert rows == [(True, False)]


def test_ensure_tables_migrates_a_table_without_the_flag_columns() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE forward_pplay_shadow (run_id VARCHAR, game_id VARCHAR)")
    store.ensure_tables(c)
    cols = {r[0] for r in c.execute("DESCRIBE forward_pplay_shadow").fetchall()}
    assert {"div_rival", "hist_rival"} <= cols
    store.ensure_tables(c)  # idempotent
