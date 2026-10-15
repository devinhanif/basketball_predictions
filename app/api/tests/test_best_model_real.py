"""The app against an export written by the REAL exporter (not a hand-built table).

app/fixtures/export_best_real was built by `python -m nba.export build` on the export branch
(agent/steward-export-best-model) from a synthetic source with forward_avg_shadow and
forward_pplay_shadow rows (see app/fixtures/build_export_best_real.py). AVG quantiles and pmf are
integers there, as the real writer stores them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from parlayhitters_api.export_store import ExportStore

REAL = Path(__file__).resolve().parents[2] / "fixtures" / "export_best_real"
FRESH, MIXED = "0022400023", "0022400024"


@pytest.fixture(scope="module")
def store() -> ExportStore:
    return ExportStore(REAL)


def _primary_rows() -> dict[tuple[str, int, str], dict[str, Any]]:
    con = duckdb.connect(str(REAL / "export.duckdb"), read_only=True)
    try:
        cur = con.execute("SELECT * FROM prop_grids WHERE is_primary")
        cols = [d[0] for d in cur.description]
        out: dict[tuple[str, int, str], dict[str, Any]] = {}
        for r in cur.fetchall():
            d = dict(zip(cols, r, strict=True))
            key = (str(d["game_id"]), int(d["player_id"]), str(d["stat"]))
            assert key not in out, "the exporter marked two primary rows"
            out[key] = d
        return out
    finally:
        con.close()


def test_the_export_is_a_best_model_export(store: ExportStore) -> None:
    assert store.best_model
    assert store.manifest["primary_sources"]["avg"] > 0


def test_every_shown_prop_is_the_exporters_primary_row(store: ExportStore) -> None:
    prim = _primary_rows()
    shown = 0
    for gid, g in store.games.items():
        for p in g["players"]:
            for st, pr in p["props"].items():
                row = prim[(gid, p["player_id"], st)]
                f = pr["forecast"]
                assert f["model"]["name"] == row["model_name"]
                assert f["model"]["source"] == row["primary_source"]
                assert f["mean"] == pytest.approx(row["mean"])
                assert f["q"] == pytest.approx(list(row["q_grid"]))
                shown += 1
    assert shown > 0
    # and no prop with a primary row went missing
    assert shown == len([k for k in prim if k[2] in ("pts", "reb", "ast", "fg3m")]) - sum(
        1 for k in prim if k[0] not in store.games
    )


def test_avg_rows_have_integer_quantiles_and_the_exact_table_is_used(store: ExportStore) -> None:
    from parlayhitters_api.forecast import forecast_probs

    n = 0
    for p in store.games[FRESH]["players"]:
        for pr in p["props"].values():
            f = pr["forecast"]
            if f["model"]["source"] != "avg":
                continue
            assert all(float(v).is_integer() for v in f["q"])
            assert f["surv"] is not None
            lp = forecast_probs(f, float(pr["line"]))
            assert 0.0 <= lp.p_over <= 1.0 and not lp.extrapolated
            assert lp.p_over + lp.p_under + lp.p_push == pytest.approx(1.0)
            n += 1
    assert n > 0


def test_stale_and_missing_fall_back_with_their_reason(store: ExportStore) -> None:
    labels = {
        pr["forecast"]["model"]["source"]: pr["forecast"]["model"]
        for p in store.games[MIXED]["players"]
        for pr in p["props"].values()
    }
    assert set(labels) == {"fallback_stale", "fallback_missing"}
    assert labels["fallback_stale"]["detail"] == "bundle stale: month_block_mismatch"
    assert labels["fallback_stale"]["name"] == "props_context_residual"


def test_p_play_primary_and_source_come_from_the_p_play_table(store: ExportStore) -> None:
    con = duckdb.connect(str(REAL / "export.duckdb"), read_only=True)
    try:
        table = {
            (g, int(p)): (v, s)
            for g, p, v, s in con.execute(
                "SELECT game_id, player_id, p_play_primary, p_play_source FROM p_play"
            ).fetchall()
        }
    finally:
        con.close()
    seen = set()
    for (gid, pid), (v, s) in table.items():
        player = next((p for p in store.games[gid]["players"] if p["player_id"] == pid), None)
        if player is None:
            continue
        for pr in player["props"].values():
            info = pr["forecast"]["p_play_info"]
            assert pr["forecast"]["p_play"] == pytest.approx(v)
            assert info["source"] == s
        seen.add(s)
    assert seen == {"r", "production", "out_on_report"}


def test_out_on_report_is_said_plainly(store: ExportStore) -> None:
    from parlayhitters_api.model_source import p_play_label

    assert p_play_label({"value": 0.0, "source": "out_on_report"}) == (
        "Chance to play: out on the official injury report."
    )
