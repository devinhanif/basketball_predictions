"""Prediction record and distribution routes: every expected value is read from the committed small
export (or a private copy a test edits); the lock is tested by moving its date."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import edit_db
from fastapi.testclient import TestClient
from honesty_helpers import a_prop, db_rows, export_api, keys_of

from parlayhitters_api import record as record_mod
from parlayhitters_api import trackrecord

# --------------------------------------------------------------------------- record


def test_prop_record_copies_the_primary_row(export_client: TestClient, export_dir: Path) -> None:
    gid, pid, st = a_prop(export_client, final=False)
    r = export_client.get(f"/api/record/{gid}/{pid}/{st}")
    assert r.status_code == 200
    body = r.json()
    (row,) = db_rows(
        export_dir,
        "SELECT model_name, version, stamp_weights_sha256, lead_minutes, primary_source, "
        "stamp_n_train, stamp_git_sha FROM prop_grids WHERE game_id = ? AND player_id = ? "
        "AND stat = ? AND is_primary ORDER BY made_at DESC, run_id DESC LIMIT 1",
        [gid, pid, st],
    )
    assert (body["model_name"], body["version"]) == (row[0], row[1])
    assert body["weights_sha256"] == row[2]
    assert body["weights_sha256_short"] == (None if row[2] is None else row[2][:12])
    assert body["lead_minutes"] == pytest.approx(row[3])
    assert body["meets_t60"] is (row[3] >= 60)
    assert body["primary_source"] == row[4]
    assert body["n_train"] == row[5] and body["git_sha"] == row[6]
    n_snaps = db_rows(
        export_dir,
        "SELECT count(*) FROM prop_grids WHERE game_id = ? AND player_id = ? AND stat = ?",
        [gid, pid, st],
    )[0][0]
    assert len(body["snapshots"]) == n_snaps
    # the snapshot list never carries a forecast number
    numeric = {"mean", "q", "q_grid", "std", "p_play", "p_over", "surv", "p_ge_full", "p_home"}
    assert not (keys_of(body["snapshots"]) & numeric)


def test_game_record_and_its_refusals(export_client: TestClient, export_dir: Path) -> None:
    gid = "0022400023"
    body = export_client.get(f"/api/record/{gid}").json()
    (row,) = db_rows(
        export_dir,
        "SELECT model_name, lead_minutes FROM win_probs WHERE game_id = ? AND is_latest "
        "AND NOT comparison_only",
        [gid],
    )
    assert body["kind"] == "game_winner" and body["model_name"] == row[0]
    assert body["lead_minutes"] == pytest.approx(row[1])
    assert len(body["snapshots"]) == 2  # two stored rows for that game
    r = export_client.get("/api/record/0029999999")
    assert r.status_code == 404 and r.json()["code"] == "unknown_game"
    r = export_client.get(f"/api/record/{gid}/1/pts")
    assert r.status_code == 404 and r.json()["code"] == "no_forecast"
    assert r.json()["field"] == "player_id"
    r = export_client.get(f"/api/record/{gid}/1/stl")
    assert r.status_code == 404 and r.json()["code"] == "unknown_stat"


def test_game_with_no_winner_row_is_a_typed_404(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit_db(export_dir, "DELETE FROM win_probs WHERE game_id = '0022400023'")
    for c in export_api(export_dir, monkeypatch):
        r = c.get("/api/record/0022400023")
        assert r.status_code == 404
        assert r.json()["code"] == "no_game_forecast" and r.json()["field"] == "game_id"


def test_fixture_record_says_what_it_lacks(client: TestClient) -> None:
    body = client.get("/api/record/0022400001/1628369/pts").json()
    assert body["available"] is False and "fixture replay" in body["reason"]
    assert body["model_name"] is None and body["snapshots"] == []
    assert client.get("/api/record/0022400001").json()["available"] is False


# --------------------------------------------------------------------------- distribution


def test_distribution_is_the_stored_row(export_client: TestClient, export_dir: Path) -> None:
    gid, pid, st = a_prop(export_client, final=False)
    body = export_client.get(f"/api/distribution/{gid}/{pid}/{st}").json()
    (row,) = db_rows(
        export_dir,
        "SELECT q_grid, mean, std, p_ge_full_counts, p_ge_full_n, proj_minutes FROM prop_grids "
        "WHERE game_id = ? AND player_id = ? AND stat = ? AND is_primary "
        "ORDER BY made_at DESC, run_id DESC LIMIT 1",
        [gid, pid, st],
    )
    assert body["locked"] is False and len(body["taus"]) == 19
    assert body["q_grid"] == pytest.approx(list(row[0]))
    assert body["mean"] == pytest.approx(row[1]) and body["std"] == pytest.approx(row[2])
    assert body["p_ge_full"]["counts"] == list(row[3]) and body["p_ge_full"]["n"] == row[4]
    assert body["proj_minutes"] == pytest.approx(row[5])
    ints = all(float(v).is_integer() for v in row[0])
    assert body["support"]["q_grid"] == ("integer" if ints else "continuous")
    assert body["support"]["survival"] == "integer"
    assert "last fair price" in body["kalshi_dnp_note"]
    # the conditional pieces are only ever stored ones: present together or null and named
    if body["conditional"] is None:
        assert any(m.startswith("conditional.") for m in body["missing"])


def test_a_missing_piece_is_null_and_named(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit_db(export_dir, "UPDATE prop_grids SET std = NULL")
    for c in export_api(export_dir, monkeypatch):
        gid, pid, st = a_prop(c, final=False)
        body = c.get(f"/api/distribution/{gid}/{pid}/{st}").json()
        assert body["std"] is None and "std" in body["missing"]


def test_distribution_locks_a_finished_game_in_the_window(
    export_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gid, pid, st = a_prop(export_client, final=True)
    assert export_client.get(f"/api/distribution/{gid}/{pid}/{st}").json()["locked"] is False
    monkeypatch.setattr(record_mod, "MODEL_LOCK_FROM", "2024-01-01")
    body = export_client.get(f"/api/distribution/{gid}/{pid}/{st}").json()
    assert body["locked"] is True and body["lock_text"]
    assert body["q_grid"] is None and body["mean"] is None and body["p_ge_full"] is None
    assert body["conditional"] is None and body["p_play"] is None
    up = a_prop(export_client, final=False)  # an unplayed game is never locked
    assert export_client.get("/api/distribution/{}/{}/{}".format(*up)).json()["locked"] is False


def test_the_lock_is_the_one_constant() -> None:
    assert vars(record_mod)["MODEL_LOCK_FROM"] == trackrecord.MODEL_LOCK_FROM
