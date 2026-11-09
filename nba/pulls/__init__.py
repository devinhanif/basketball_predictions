# ruff: noqa: E501
"""Pull orchestrator: WHEN to pull (docs/DESIGN_PULL_ORCHESTRATOR.md). HOW stays in nba.odds.

Public API for a consumer: :func:`nba.pulls.store.request_pull`. Nothing here places an order, holds a
credential or writes ``nba.duckdb``.
"""

from __future__ import annotations

from nba.pulls.store import PullRequest, PullStore, RequestResult, request_pull

__all__ = ["PullRequest", "PullStore", "RequestResult", "request_pull"]
