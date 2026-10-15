"""The best-evidenced model per stat (DECISIONS 2026-10-14 21:30): which row the app shows and how
it says where each number came from.

The committed export (app/fixtures/export_small) is synthetic. Tonight is 2024-11-12:
  * game 0022400023: a FRESH AVG row for every prop except one planted gap (990006, fg3m);
  * game 0022400024: home-team players have a STALE AVG row, away-team players have none;
  * earlier days: no AVG rows.
A test that needs another shape edits a private copy and reseals it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import EXPORT_SMALL, downgrade_best_model, edit_db
from fastapi.testclient import TestClient

from parlayhitters_api.export_store import ExportStore
from parlayhitters_api.model_source import model_label, month_name, p_play_label

FRESH, MIXED, GAP_PLAYER = "0022400023", "0022400024", 990006
AVG_LABEL = "Model: AVG (sequence + recent-role blend), November 2024 bundle."


def _db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(EXPORT_SMALL / "export.duckdb"), read_only=True)


def _props(c: TestClient, gid: str) -> list[tuple[int, str, dict[str, Any]]]:
    g = c.get(f"/api/games/{gid}").json()
    return [(p["player_id"], st, pr) for p in g["players"] for st, pr in p["props"].items()]


# ------------------------------------------------------------------ primary chosen


def test_fresh_avg_row_is_the_one_shown(export_client: TestClient) -> None:
    rows = _props(export_client, FRESH)
    assert rows
    for pid, st, pr in rows:
        if (pid, st) == (GAP_PLAYER, "fg3m"):
            assert pr["model_source"] == "fallback_missing"
            continue
        assert pr["model_source"] == "avg" and pr["model_label"] == AVG_LABEL, (pid, st)
        assert "props_avg_live2 avg-live2" in pr["forecast_stamp"]
    # the numbers are the AVG row's, not production's
    pid, st, pr = next(r for r in rows if r[1] == "pts" and r[0] != GAP_PLAYER)
    con = _db()
    try:
        avg_mean, prod_mean = (
            con.execute(
                "SELECT mean FROM prop_grids WHERE game_id=? AND player_id=? AND stat=? "
                "AND model_name=? AND is_latest",
                [FRESH, pid, st, name],
            ).fetchone()
            for name in ("props_avg_live2", "props_context_residual")
        )
    finally:
        con.close()
    assert avg_mean and prod_mean and avg_mean[0] != prod_mean[0]
    assert pr["forecast_mean"] == pytest.approx(avg_mean[0])


def test_missing_and_stale_avg_fall_back_to_production_and_say_why(
    export_client: TestClient,
) -> None:
    gap = next(r for r in _props(export_client, FRESH) if (r[0], r[1]) == (GAP_PLAYER, "fg3m"))
    assert gap[2]["model_label"] == (
        "Model: production model (AVG unavailable: no AVG forecast was stored before tip)."
    )
    by_source: dict[str, int] = {}
    for _pid, _st, pr in _props(export_client, MIXED):
        by_source[pr["model_source"]] = by_source.get(pr["model_source"], 0) + 1
        if pr["model_source"] == "fallback_stale":
            assert pr["model_label"].startswith("Model: production model (AVG unavailable: stale ")
            assert "props_context_residual v1" in pr["forecast_stamp"]
    assert set(by_source) == {"fallback_stale", "fallback_missing"}
    assert all(n > 0 for n in by_source.values())


def test_a_prop_with_no_primary_row_is_not_shown(
    export_dir: Path, export_client: TestClient
) -> None:
    # the exporter marks no primary when there is no usable row: the app invents none
    edit_db(
        export_dir,
        f"UPDATE prop_grids SET is_primary = false WHERE game_id = '{FRESH}' "
        "AND player_id = 990001 AND stat = 'pts'",
    )
    c = ExportStore(export_dir)
    star = next(p for p in c.games[FRESH]["players"] if p["player_id"] == 990001)
    assert "pts" not in star["props"] and "reb" in star["props"]


def test_a_non_primary_avg_row_is_never_shown_even_if_newer(export_dir: Path) -> None:
    edit_db(
        export_dir,
        "UPDATE prop_grids SET is_primary = (model_name = 'props_context_residual'), "
        "primary_source = CASE WHEN model_name = 'props_context_residual' THEN 'fallback_stale' "
        f"END WHERE game_id = '{FRESH}' AND is_latest",
    )
    s = ExportStore(export_dir)
    for p in s.games[FRESH]["players"]:
        for pr in p["props"].values():
            assert pr["forecast"]["model"]["name"] == "props_context_residual"
            assert pr["forecast"]["model"]["source"] == "fallback_stale"


# ------------------------------------------------------------------ old export, postseason


def test_an_export_before_best_model_schema_keeps_the_production_choice(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downgrade_best_model(export_dir)
    s = ExportStore(export_dir)
    assert not s.best_model
    seen = 0
    for g in s.games.values():
        for p in g["players"]:
            for pr in p["props"].values():
                f = pr["forecast"]
                assert f["model"]["source"] == "legacy" and f["model"]["name"] == (
                    "props_context_residual"
                )
                assert f["p_play"] == pytest.approx(0.97)  # the value stored with the row
                assert f["p_play_info"]["source"] == "legacy"
                seen += 1
    assert seen
    assert "does not record which model" in " ".join(s.meta()["stubs"])
    label = model_label(
        next(iter(s.games.values()))["players"][0]["props"]["pts"]["forecast"]["model"]
    )
    assert "older export" in label[1]


def test_old_export_api_says_the_source_in_words(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parlayhitters_api.main import create_app, reset_store

    downgrade_best_model(export_dir)
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        _pid, _st, pr = _props(c, FRESH)[0]
    reset_store()
    assert pr["model_source"] == "legacy" and "older export" in pr["model_label"]


def test_a_postseason_row_shows_production_with_the_postseason_reason(
    export_dir: Path,
) -> None:
    edit_db(
        export_dir,
        "UPDATE prop_grids SET is_primary = (model_name = 'props_context_residual'), "
        "primary_source = CASE WHEN model_name = 'props_context_residual' THEN 'postseason' END, "
        "primary_detail = CASE WHEN model_name = 'props_context_residual' "
        f"THEN 'AVG flat in the postseason (T479)' END WHERE game_id = '{FRESH}' AND is_latest",
    )
    s = ExportStore(export_dir)
    pr = s.games[FRESH]["players"][0]["props"]["pts"]
    assert pr["forecast"]["model"]["source"] == "postseason"
    src, label = model_label(pr["forecast"]["model"])
    assert src == "postseason"
    assert label == "Model: production model (postseason: AVG was no better in the playoffs)."


# ------------------------------------------------------------------ chance to play


def test_p_play_is_the_primary_and_its_source_is_shown(export_client: TestClient) -> None:
    con = _db()
    try:
        table = {
            (g, int(p)): (v, src, det)
            for g, p, v, src, det in con.execute(
                "SELECT game_id, player_id, p_play_primary, p_play_source, p_play_detail "
                "FROM p_play"
            ).fetchall()
        }
    finally:
        con.close()
    assert {s for _v, s, _d in table.values()} == {"r", "production"}
    seen: set[str] = set()
    for (gid, pid), (v, src, _det) in sorted(table.items()):
        stat = "pts"
        b = export_client.get(f"/api/breakdown/{gid}/{pid}/{stat}")
        if b.status_code != 200:
            continue
        role = b.json()["role"]
        assert role["p_play"] == pytest.approx(v)
        assert round(v * 100).__str__() + "% chance to play" in role["say"]
        if src == "r":
            assert role["p_play_label"] == "Chance to play: from the availability model R."
        else:
            assert role["p_play_label"] == (
                "Chance to play: from the production model; availability model R had no "
                "number (R stored no number for him)."
            )
        assert role["p_play_label"] in role["say"]
        seen.add(src)
    assert seen == {"r", "production"}


def test_prop_view_carries_p_play_and_its_label(export_client: TestClient) -> None:
    _pid, _st, pr = _props(export_client, FRESH)[0]
    assert pr["p_play"] is not None and pr["p_play_label"].startswith("Chance to play: from the")


def test_older_rows_without_an_r_row_show_the_production_value(export_client: TestClient) -> None:
    # earlier days have no p_play table row: production's own stored value, named as such
    gid = next(g["game_id"] for g in export_client.get("/api/slates/2024-11-11").json()["games"])
    _pid, _st, pr = _props(export_client, gid)[0]
    assert pr["p_play"] == pytest.approx(0.97)
    assert pr["p_play_label"].startswith("Chance to play: from the production model")


def test_meta_says_which_models_are_shown(export_client: TestClient) -> None:
    text = " ".join(export_client.get("/api/meta").json()["stubs"])
    assert "best-evidenced model" in text and "availability model R" in text
    assert "avg 43" in text and "fallback_stale 24" in text


# ------------------------------------------------------------------ the sentences themselves


def test_labels_name_only_what_is_stored() -> None:
    assert month_name("2026-10") == "October 2026" and month_name("junk") is None
    assert model_label(None)[0] == "fixture"
    assert "bundle month not stored" in model_label({"source": "avg"})[1]
    assert p_play_label({"value": None, "source": "r"}) is None
    assert p_play_label({"value": 0.9, "source": "r"}) == (
        "Chance to play: from the availability model R."
    )


# ------------------------------------------------------------------ quotes follow lines_run_id


def test_an_avg_row_with_its_own_run_id_gets_its_production_rows_lines(export_dir: Path) -> None:
    # the AVG run has no prop_lines of its own; lines_run_id names the production run's quotes
    edit_db(
        export_dir, "UPDATE prop_grids SET run_id = 'r_avg_x' WHERE model_name = 'props_avg_live2'"
    )
    s = ExportStore(export_dir)
    for p in s.games[FRESH]["players"]:
        for pr in p["props"].values():
            if pr["forecast"]["model"]["source"] == "avg":
                assert pr["line_source"] == "posted" and pr["over_price"] is not None
    # an export without the column (and a run id with no quotes) shows a reference line instead
    edit_db(export_dir, "UPDATE prop_grids SET lines_run_id = NULL")
    s = ExportStore(export_dir)
    avg = [
        pr
        for p in s.games[FRESH]["players"]
        for pr in p["props"].values()
        if pr["forecast"]["model"]["source"] == "avg"
    ]
    assert avg and all(pr["line_source"] == "reference" and pr["over_price"] is None for pr in avg)


def test_labels_do_not_print_a_version_string(export_client: TestClient) -> None:
    for _pid, _st, pr in _props(export_client, MIXED):
        assert "v1" not in pr["model_label"]
        if pr["model_source"] != "avg":
            assert pr["model_detail"] == "props_context_residual v1"
