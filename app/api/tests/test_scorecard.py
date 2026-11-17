"""Scorecard and forward-test tracker: means trace to planted settled_scores rows; the holdout,
preseason and late rows are never averaged; locked dates and frozen arms give counts only."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import edit_db
from fastapi.testclient import TestClient
from honesty_helpers import BASE, DATES, INT, PROD, export_api, keys_of

from parlayhitters_api import scorecard, trackrecord


def _pts(c: TestClient, **q: str) -> dict[str, Any]:
    body = c.get("/api/scorecard", params={"target": "props", "stat": "pts", **q}).json()
    (card,) = body["targets"]
    return {"body": body, "card": card, **{m["model_name"]: m for m in card["models"]}}


def test_scorecard_means_trace_to_the_planted_rows(planted: TestClient) -> None:
    got = _pts(planted)
    prod, base = got[PROD], got[BASE]
    n = sum(i % 3 + 1 for i in range(12))
    want_prod = sum(2.0 + 0.1 * j for i in range(12) for j in range(i % 3 + 1)) / n
    assert prod["metrics"]["crps"]["value"] == pytest.approx(want_prod)
    assert prod["n_scored"] == n == prod["metrics"]["crps"]["n"]
    assert prod["n_dates"] == 12 and prod["n_dnp"] == 12
    assert prod["n_late_excluded"] == 1  # the 30-minute row, counted, never averaged
    assert prod["n_t60_unknown"] == n  # planted games have no stored tip-off
    want_base = sum(2.5 + 0.05 * i for i in range(12) for _ in range(i % 3 + 1)) / n
    assert base["metrics"]["crps"]["value"] == pytest.approx(want_base)
    (cmp_,) = [x for x in got["card"]["comparisons"] if x["shown"]]
    m = cmp_["metrics"]["crps"]
    assert (cmp_["arm"], cmp_["comparator"]) == (PROD, BASE)
    assert m["value"] == pytest.approx(want_prod - want_base) and m["n"] == n
    assert m["lo"] <= m["value"] <= m["hi"]
    assert m["call"] == ("better" if m["hi"] < 0 else "worse" if m["lo"] > 0 else "same")
    assert got["body"]["score_rule"]["rule"] == "latest_row"
    assert {x["metric"].split(" ")[0] for x in got["body"]["not_available"]} >= {
        "pinball19", "PIT", "CLV"}  # fmt: skip


def test_scorecard_never_reads_the_holdout_or_preseason(planted: TestClient) -> None:
    body = _pts(planted)["body"]
    assert body["seasons"] == [2024]
    for m in _pts(planted)["card"]["models"]:
        v = m["metrics"].get("crps")
        assert v is None or v["value"] < 999.0 / 50  # a 999 row would show at once
    assert body["score_rule"]["n_rows_read"] == 2 * 24 + 24 + 12 + 1  # 2025 and 001 not read


def test_scorecard_is_deterministic(planted: TestClient) -> None:
    a = planted.get("/api/scorecard").json()
    from parlayhitters_api.deps import _cache

    _cache.pop("scorecard", None)
    assert planted.get("/api/scorecard").json() == a


def test_frozen_arms_give_counts_only(planted: TestClient) -> None:
    got = _pts(planted)
    arm = got[INT]
    assert arm["shown"] is False and arm["metrics"] == {} and arm["n_scored"] == 24
    assert "frozen" in arm["withheld_reason"]
    for c in got["card"]["comparisons"]:
        if c["arm"] == INT:
            assert c["metrics"] == {} and c["n_pairs"] == 24 and not c["shown"]


def test_locked_dates_give_counts_only(
    planted: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parlayhitters_api.deps import _cache

    monkeypatch.setattr(scorecard, "MODEL_LOCK_FROM", DATES[6].isoformat())
    _cache.pop("scorecard", None)
    got = _pts(planted)
    prod = got[PROD]
    open_n = sum(i % 3 + 1 for i in range(6))
    assert prod["n_scored"] == open_n == prod["metrics"]["crps"]["n"]
    assert prod["n_scored_locked"] == sum(i % 3 + 1 for i in range(6, 12))
    assert prod["metrics"]["crps"]["n_dates"] == 6
    (cmp_,) = [x for x in got["card"]["comparisons"] if x["shown"]]
    assert cmp_["metrics"]["crps"]["call"] == "too_few_dates"  # six dates never decide
    monkeypatch.setattr(scorecard, "MODEL_LOCK_FROM", "2000-01-01")
    _cache.pop("scorecard", None)
    got = _pts(planted)
    assert got[PROD]["metrics"] == {} and got[PROD]["n_scored_locked"] > 0
    assert all(c["metrics"] == {} for c in got["card"]["comparisons"])


def test_win_scores_are_the_frozen_tripwire(export_client: TestClient) -> None:
    body = export_client.get("/api/scorecard", params={"target": "win"}).json()
    (card,) = body["targets"]
    assert card["target"] == "win_prob_home"
    assert card["models"] and all(m["metrics"] == {} and not m["shown"] for m in card["models"])
    assert card["models"][0]["n_scored"] == 22  # the fixture's 22 settled winner rows
    r = export_client.get("/api/scorecard", params={"target": "win", "stat": "pts"})
    assert r.status_code == 422 and r.json()["field"] == "stat"


def test_scorecard_without_scores_says_so(
    client: TestClient, export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = client.get("/api/scorecard").json()
    assert body["available"] is False and body["targets"] == []
    edit_db(export_dir, "DROP TABLE settled_scores")
    for c in export_api(export_dir, monkeypatch):
        body = c.get("/api/scorecard").json()
        assert body["available"] is False and "no settled_scores" in body["reason"]
        assert c.get("/api/forward-tests").status_code == 200


def test_bootstrap_edge_cases() -> None:
    assert scorecard.date_cluster_ci([], []) == (None, None, None)
    assert scorecard.date_cluster_ci([3.0], [2]) == (1.5, None, None)
    m, lo, hi = scorecard.date_cluster_ci([1.0, 2.0, 3.0], [1, 1, 1])
    assert m == pytest.approx(2.0) and lo is not None and hi is not None and lo <= 2.0 <= hi
    assert scorecard.date_cluster_ci([1.0, 2.0, 3.0], [1, 1, 1]) == (m, lo, hi)


# --------------------------------------------------------------------------- forward tests


def test_forward_tests_cite_their_sources_and_carry_no_outcome(planted: TestClient) -> None:
    body = planted.get("/api/forward-tests").json()
    assert body["tests"] and body["as_of"]
    for t in body["tests"]:
        assert re.search(r"\.md:\d", t["source"]), t["id"]
        assert t["scheduled_read"] and t["n_needed"] and t["as_of"]
        assert (t["n_so_far"] is None) == bool(t["n_so_far_reason"]) or t["n_so_far"] is not None
    banned = {"score", "crps", "log_loss", "brier", "delta", "diff", "better", "worse", "verdict"}
    assert not (keys_of(body["tests"]) & banned)
    by_id = {t["id"]: t for t in body["tests"]}
    assert by_id["FORWARD_PREREG_INT"]["n_so_far"][INT] == {"scored": 24, "dnp": 0, "dates": 12}
    assert by_id["LINE_CAL_LIVE"]["n_so_far"] is None


def test_forward_tests_in_fixture_mode(client: TestClient) -> None:
    body = client.get("/api/forward-tests").json()
    t = next(x for x in body["tests"] if x["arms"])
    assert t["n_so_far"] is None and "no settled_scores" in t["n_so_far_reason"]


def test_the_scorecard_lock_is_the_one_constant() -> None:
    assert vars(scorecard)["MODEL_LOCK_FROM"] == trackrecord.MODEL_LOCK_FROM
