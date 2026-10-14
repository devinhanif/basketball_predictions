"""The nightly export as the API's data source: verification, fallback, and the same screens.

The export under test (app/fixtures/export_small) was built by the real `python -m nba.export
build` from SYNTHETIC rows dated Oct-Nov 2024 (season 2024), with invented player ids. See
fixtures/build_export_fixture.py. Nothing here touches season 2025.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import EXPORT_DAY, EXPORT_SMALL, seal
from fastapi.testclient import TestClient

from parlayhitters_api import export_store as es
from parlayhitters_api.export_store import ExportRefused, ExportStore
from parlayhitters_api.forecast import surv_probs
from parlayhitters_api.main import reset_store
from parlayhitters_api.store import EXPORT_ENV, Store, load_store

BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "fixtures" / "build_export_fixture.py"


def _manifest(d: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads((d / "manifest.json").read_text())
    return out


# --------------------------------------------------------------------------- verification


def test_committed_export_loads_and_is_pre_2025() -> None:
    s = ExportStore(EXPORT_SMALL)
    assert s.manifest["seasons"] == [2024]
    assert list(s.slates)[0] == EXPORT_DAY  # newest night first
    for slate in s.slates.values():
        assert slate["season"] <= 2024 and slate["date"] < "2025-07-01"
    assert s.data_version == s.manifest["data_version"]
    assert s.made_at == s.manifest["made_at"]


def test_a_changed_database_is_refused_before_it_is_opened(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (export_dir / "export.duckdb").open("ab") as f:
        f.write(b"x")

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("the database was opened before the checksum was verified")

    monkeypatch.setattr(duckdb, "connect", boom)
    with pytest.raises(ExportRefused, match="sha256"):
        ExportStore(export_dir)


def test_a_changed_parlay_file_is_refused(export_dir: Path) -> None:
    (export_dir / "parlay_of_day.json").write_text("{}")
    with pytest.raises(ExportRefused, match="parlay_of_day.json"):
        ExportStore(export_dir)


@pytest.mark.parametrize("missing", ["data_version", "made_at"])
def test_a_manifest_without_data_version_or_made_at_is_refused(
    export_dir: Path, missing: str
) -> None:
    seal(export_dir, **{missing: None})
    with pytest.raises(ExportRefused, match=missing):
        ExportStore(export_dir)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_data_version_is_refused(export_dir: Path, blank: str) -> None:
    seal(export_dir, data_version=blank)
    with pytest.raises(ExportRefused, match="data_version"):
        ExportStore(export_dir)


def test_a_null_data_version_is_refused(export_dir: Path) -> None:
    seal(export_dir, data_version=None)  # the exporter writes null when no data manifest exists
    with pytest.raises(ExportRefused, match="data_version"):
        ExportStore(export_dir)


def test_an_unparseable_made_at_is_refused(export_dir: Path) -> None:
    seal(export_dir, made_at="yesterday")
    with pytest.raises(ExportRefused, match="made_at"):
        ExportStore(export_dir)


def test_a_manifest_that_lists_the_holdout_season_is_refused(export_dir: Path) -> None:
    seal(export_dir, seasons=[2024, 2025])
    with pytest.raises(ExportRefused, match="holdout"):
        ExportStore(export_dir)


def test_holdout_rows_inside_the_database_are_refused(export_dir: Path) -> None:
    con = duckdb.connect(str(export_dir / "export.duckdb"))
    con.execute("UPDATE box_scores SET season = 2025 WHERE rowid = 0")
    con.execute("CHECKPOINT")
    con.close()
    seal(export_dir)  # a sealed, checksum-clean export with a 2025 row is still refused
    with pytest.raises(ExportRefused, match="holdout"):
        ExportStore(export_dir)


def test_meta_must_agree_with_the_manifest(export_dir: Path) -> None:
    seal(export_dir, made_at="2030-01-01T00:00:00Z")
    with pytest.raises(ExportRefused, match="meta"):
        ExportStore(export_dir)


def test_garbled_or_wrong_schema_manifests_are_refused(export_dir: Path) -> None:
    seal(export_dir, schema_version=2)
    with pytest.raises(ExportRefused, match="schema_version"):
        ExportStore(export_dir)
    (export_dir / "manifest.json").write_text("{not json")
    with pytest.raises(ExportRefused, match="readable"):
        ExportStore(export_dir)


def test_missing_checksums_or_files_are_refused(export_dir: Path) -> None:
    seal(export_dir, files={})
    with pytest.raises(ExportRefused, match="sha256"):
        ExportStore(export_dir)
    shutil.rmtree(export_dir)
    export_dir.mkdir()
    (export_dir / "manifest.json").write_text(
        json.dumps({**_manifest(EXPORT_SMALL), "files": _manifest(EXPORT_SMALL)["files"]})
    )
    with pytest.raises(ExportRefused, match="missing"):
        ExportStore(export_dir)


# ------------------------------------------------------------------------------- fallback


def test_fixtures_are_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXPORT_ENV, raising=False)
    assert isinstance(load_store(), Store)
    monkeypatch.setenv(EXPORT_ENV, str(tmp_path / "nothing-here"))  # no manifest.json
    assert isinstance(load_store(), Store)
    monkeypatch.setenv(EXPORT_ENV, str(EXPORT_SMALL))
    assert isinstance(load_store(), ExportStore)


def test_a_refused_export_answers_503_with_the_reason(
    export_dir: Path, export_client: TestClient
) -> None:
    assert export_client.get("/api/health").json() == {"status": "ok", "mode": "export"}
    seal(export_dir, data_version=None)  # replaced on disk while the API is running
    r = export_client.get("/api/meta")
    assert r.status_code == 503 and "data_version" in r.json()["detail"]


def test_a_new_export_is_picked_up_without_a_restart(
    export_dir: Path, export_client: TestClient
) -> None:
    before = export_client.get("/api/meta").json()["built_at"]
    seal(export_dir, made_at="2024-11-13T12:00:00Z")
    con = duckdb.connect(str(export_dir / "export.duckdb"))
    con.execute("UPDATE meta SET value = '2024-11-13T12:00:00Z' WHERE key = 'made_at'")
    con.execute("CHECKPOINT")
    con.close()
    seal(export_dir)
    after = export_client.get("/api/meta").json()
    assert before != after["built_at"] == "2024-11-13T12:00:00Z"


# ------------------------------------------------------------------------- the same screens


def test_meta_says_when_the_data_is_from(export_client: TestClient) -> None:
    m = export_client.get("/api/meta").json()
    assert m["mode"] == "export"
    assert m["data_as_of"] == _manifest(EXPORT_SMALL)["made_at"]
    assert m["data_version"] == _manifest(EXPORT_SMALL)["data_version"]
    assert any("no sportsbook lines" in s for s in m["stubs"])


def test_slate_and_game_come_from_the_export(export_client: TestClient) -> None:
    slates = export_client.get("/api/slates").json()
    assert [s["date"] for s in slates][0] == EXPORT_DAY
    s = export_client.get(f"/api/slates/{EXPORT_DAY}").json()
    assert s["mode"] == "export" and len(s["games"]) == 2
    for g in s["games"]:
        wp = g["win_probability"]
        assert 0 < wp["model_home"] < 1 and wp["market_home"] is None
        assert g["key_props"] and g["key_props"][0]["market_p_over"] is None
    gid = s["games"][0]["game_id"]
    body = export_client.get(f"/api/games/{gid}").json()
    assert body["final"] is False
    prop = body["players"][0]["props"]["pts"]
    assert prop["line_source"] == "reference"
    assert prop["over_price"] is None and prop["market_p_over"] is None
    assert prop["model_p_over"] + prop["model_p_under"] + prop["model_p_push"] == pytest.approx(1)
    assert export_client.get(f"/api/games/{gid}/result").status_code == 404  # not played yet


def test_a_played_game_has_a_result_and_actuals(export_client: TestClient) -> None:
    s = export_client.get("/api/slates/2024-11-11").json()
    gid = s["games"][0]["game_id"]
    g = export_client.get(f"/api/games/{gid}").json()
    assert g["final"] is True
    r = export_client.get(f"/api/games/{gid}/result").json()
    assert r["label"] == "final" and r["home_pts"] > 0
    assert r["players"] and all(p["minutes"] is not None for p in r["players"])


def test_win_probability_is_the_latest_pre_tip_run_not_the_late_one(
    export_client: TestClient,
) -> None:
    con = duckdb.connect(str(EXPORT_SMALL / "export.duckdb"), read_only=True)
    try:
        rows = con.execute(
            "SELECT game_id, p_home FROM win_probs WHERE is_latest AND game_id IN "
            "(SELECT game_id FROM games WHERE game_date = DATE '2024-11-12')"
        ).fetchall()
    finally:
        con.close()
    s = export_client.get(f"/api/slates/{EXPORT_DAY}").json()
    got = {g["game_id"]: g["win_probability"]["model_home"] for g in s["games"]}
    assert got == {gid: p for gid, p in rows}


def test_breakdown_works_from_the_export(export_client: TestClient) -> None:
    s = export_client.get(f"/api/slates/{EXPORT_DAY}").json()
    gid = s["games"][0]["game_id"]
    g = export_client.get(f"/api/games/{gid}").json()
    star = next(p for p in g["players"] if p["name"] == "Player 990001")
    b = export_client.get(f"/api/breakdown/{gid}/{star['player_id']}/pts").json()
    assert b["line_source"] == "model_middle" and b["line"] % 1 == 0.5
    assert b["last10"]["n"] == 10 and len(b["chips"]) == 10
    assert b["model"]["available"] and not b["model"]["extrapolated"]
    assert b["prices"]["posted_line"] is None and "no sportsbook lines" in b["prices"]["say"]
    # minutes are stored with the forecast, so the role carries them and the old caveat is gone
    assert b["role"]["minutes_forecast"] is not None and b["role"]["p_play"] is not None
    assert "do not forecast minutes" not in b["model"]["reason"]
    # the sidekick is out on tonight's report: he is listed with his earlier games without him
    assert [t["name"] for t in b["teammates"]["items"]] == ["Player 990002"]
    assert b["teammates"]["items"][0]["without"]["n"] == 3
    # no steals or blocks in the export: those tabs and routes do not exist
    assert [t["stat"] for t in b["tabs"]] == list(es.EXPORT_STATS)
    assert export_client.get(f"/api/breakdown/{gid}/{star['player_id']}/stocks").status_code == 404
    assert export_client.get(f"/api/breakdown/{gid}/{star['player_id']}/pra").status_code == 200
    # home/away is known only for games the export schedules; older games leave it out
    homes = {c["home"] for c in b["chips"]}
    assert None in homes and (True in homes or False in homes)
    split = {x["key"]: x for x in b["splits"]}
    sided = split["home"]["since_last_season"]["n"] + split["away"]["since_last_season"]["n"]
    assert 0 < sided < b["prior_games"]  # games with an unknown side enter neither split
    assert b["opponent_allowed"] is None  # no positions in the export


def test_alt_lines_use_the_exact_stored_table(export_client: TestClient) -> None:
    s = export_client.get(f"/api/slates/{EXPORT_DAY}").json()
    gid = s["games"][0]["game_id"]
    star = next(
        p for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["name"] == "Player 990001"
    )  # fmt: skip
    con = duckdb.connect(str(EXPORT_SMALL / "export.duckdb"), read_only=True)
    try:
        c, n = con.execute(
            "SELECT p_ge_full_counts, p_ge_full_n FROM prop_grids WHERE game_id = ? AND "
            "player_id = ? AND stat = 'pts' AND is_latest AND NOT comparison_only",
            [gid, star["player_id"]],
        ).fetchone() or ([], 0)
    finally:
        con.close()
    for line in (10.5, 20.5, 25.5, 30.5, 99.5):
        r = export_client.get(
            f"/api/props/{gid}/{star['player_id']}/pts", params={"line": line}
        ).json()
        want = surv_probs(c, n, line)
        assert r["p_over"] == pytest.approx(want.p_over)
        assert r["p_under"] == pytest.approx(want.p_under)
        assert r["extrapolated"] is False
        assert r["over_price"] is None and "full-support" in r["method"]
        # P(over L) is the stored count of outcomes >= floor(L) + 1, over the stored denominator
        k = int(line) + 1
        assert r["p_over"] == pytest.approx((c[k - 1] / n) if k - 1 < len(c) else 0.0)
    ladder = export_client.get(f"/api/breakdown/{gid}/{star['player_id']}/pts").json()["ladder"]
    row = next(x for x in ladder if x["line"] == 25.5)
    assert row["model_over"] == pytest.approx(surv_probs(c, n, 25.5).p_over)
    assert row["over_price"] is None


def test_no_prices_means_no_swipe_cards_and_no_settled_rows(export_client: TestClient) -> None:
    deck = export_client.get(f"/api/slates/{EXPORT_DAY}/swipe").json()
    assert deck["cards"] == [] and "no sportsbook lines" in deck["note"]
    res = export_client.get("/api/slates/2024-11-11/results").json()
    assert res["rows"] == []


def test_breakdown_text_is_attached_and_checked(export_client: TestClient) -> None:
    s = export_client.get(f"/api/slates/{EXPORT_DAY}").json()
    gid = s["games"][0]["game_id"]
    pid = export_client.get(f"/api/games/{gid}").json()["players"][0]["player_id"]
    d = export_client.get(f"/api/breakdown/{gid}/{pid}/reb").json()["dissection"]
    assert d["provider"] == "template" and d["guard_ok"] is True and d["text"]
    assert d["numbers_checked"] > 0 and d["tier"] == "leg" and not d["fell_back"]


# ------------------------------------------------------------------------- parlay of the day


def _parlay_export(d: Path, body: dict[str, Any]) -> None:
    (d / "parlay_of_day.json").write_text(json.dumps(body))
    seal(d)


def test_parlay_no_positive_ev_is_a_normal_state(export_client: TestClient) -> None:
    p = export_client.get("/api/parlay-of-the-day").json()
    assert p["state"] == "C" and p["headline_verdict"] == "no_positive_ev_found"
    assert p["verdict_line"] == "Keep your money." and p["gate"] is None
    assert p["as_of"] == _manifest(EXPORT_SMALL)["made_at"]


def test_parlay_not_run_is_not_a_verdict(export_dir: Path, export_client: TestClient) -> None:
    _parlay_export(
        export_dir,
        {"date": EXPORT_DAY, "source": "evaluate_x.json", "status": "not_run", "verdict": None,
         "n_evaluated": 0, "n_positive": 0, "records": []},
    )  # fmt: skip
    p = export_client.get("/api/parlay-of-the-day").json()
    assert p["headline_verdict"] == "not_run" and "not a verdict" in p["verdict_line"]


def test_parlay_positive_verdict_is_passed_through(
    export_dir: Path, export_client: TestClient
) -> None:
    _parlay_export(
        export_dir,
        {"date": EXPORT_DAY, "source": "evaluate_x.json", "status": "evaluated",
         "verdict": "positive_ev_at_conservative_bound", "n_evaluated": 4, "n_positive": 1,
         "records": [{"verdict": "positive_ev_at_conservative_bound"}]},
    )  # fmt: skip
    p = export_client.get("/api/parlay-of-the-day").json()
    assert p["state"] == "A" and p["headline_verdict"] == "positive_ev_at_conservative_bound"
    assert "1 of 4" in p["verdict_line"]


# ------------------------------------------------------- built by the real exporter (optional)


def _nba_python() -> str | None:
    cand = os.environ.get("NBA_PYTHON") or str(
        Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"
    )
    return cand if Path(cand).exists() else None


@pytest.mark.skipif(_nba_python() is None, reason="set NBA_PYTHON to the repo interpreter")
def test_the_committed_export_is_what_the_real_exporter_builds(tmp_path: Path) -> None:
    out = tmp_path / "rebuilt"
    subprocess.run(  # noqa: S603
        [str(_nba_python()), str(BUILD_SCRIPT), "--out", str(out), "--python", str(_nba_python())],
        check=True,
        capture_output=True,
    )
    fresh, kept = _manifest(out), _manifest(EXPORT_SMALL)
    assert fresh["row_counts"] == kept["row_counts"]
    assert fresh["schema_version"] == kept["schema_version"]
    a, b = ExportStore(out), ExportStore(EXPORT_SMALL)
    assert list(a.slates) == list(b.slates)
    assert {g: v["win_probability"]["model_home"] for g, v in a.games.items()} == {
        g: v["win_probability"]["model_home"] for g, v in b.games.items()
    }
    reset_store()
