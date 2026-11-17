"""The scorecard and the forward-test tracker (docs/contracts/scorecard_v1.md,
forward_tests_v1.md). Means of stored scores with n and date-clustered intervals; locked and
frozen rows give counts only."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query

from ..deps import StoreDep, _cache
from ..errors import ApiError
from ..forward_tests import forward_tests
from ..honesty_data import Scores
from ..honesty_models import ForwardTests, Scorecard
from ..scorecard import build as build_scorecard


def register(app: FastAPI) -> None:
    @app.get("/api/scorecard", response_model=Scorecard)
    def scorecard(
        store: StoreDep,
        target: Annotated[Literal["props", "win"] | None, Query()] = None,
        stat: Annotated[Literal["pts", "reb", "ast", "fg3m"] | None, Query()] = None,
    ) -> Scorecard:
        """Means of the stored scores with n and date-clustered intervals; locked and frozen
        rows give counts only."""
        if target == "win" and stat is not None:
            raise ApiError(
                "bad_request",
                "a stat applies to player props only",
                field="stat",
                text="The value given for stat is not one this address accepts.",
            )
        key = (target, stat)
        hit = _cache.get("scorecard")
        if hit is not None and hit[0] is store and key in hit[1]:
            cached: Scorecard = hit[1][key]
            return cached
        scores = getattr(store, "scores", None)
        if scores is None:
            scores = Scores(False, "the fixture replay has no settled_scores", [])
        m: dict[str, Any] = getattr(store, "manifest", {}) or {}
        result = build_scorecard(
            scores,
            target=target,
            stat=stat,
            as_of=m.get("made_at") or m.get("built_at"),
            data_version=m.get("data_version") or m.get("source_data_version"),
        )
        if hit is None or hit[0] is not store:
            per: dict[tuple[str | None, str | None], Scorecard] = {}
            hit = (store, per)
            _cache["scorecard"] = hit
        hit[1][key] = result
        return result

    @app.get("/api/forward-tests", response_model=ForwardTests)
    def forward_tests_route(store: StoreDep) -> ForwardTests:
        """The frozen forward tests, their scheduled reads and n needed; counts only."""
        return forward_tests(getattr(store, "scores", None))
