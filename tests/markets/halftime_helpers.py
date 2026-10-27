"""Synthetic capture nights and a tiny artifact for the halftime scorer tests (no network)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from nba.markets import halftime_model as hm
from nba.markets.halftime_model import Artifact, load_artifact, save_artifact

T0 = datetime(2026, 10, 27, 1, 0, 0, tzinfo=UTC)  # first scoreboard tick showing halftime
EID = "401900001"


def _write(night: Path, rel: str, body: Any) -> None:
    p = night / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wb") as f:
        f.write(json.dumps(body).encode())


class Night:
    """Builds ``data/raw/live_<date>/`` with a manifest, scoreboard, summary and Kalshi files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.manifest: list[dict[str, Any]] = []
        self.n = 0

    def add(self, source: str, key: str, at: datetime, body: Any) -> None:
        self.n += 1
        ts = at.strftime("%Y%m%dT%H%M%S") + f"_{self.n}"
        rel = f"{source}/{key}/{ts}.json.gz"
        _write(self.root, rel, body)
        self.manifest.append(
            {
                "http_status": 200,
                "key": key,
                "path": rel,
                "received_at": at.isoformat(),
                "source": source,
            }
        )

    def scoreboard(
        self, at: datetime, status: str, period: int, away: int = 50, home: int = 60
    ) -> None:
        ev = {
            "id": EID,
            "status": {"type": {"name": status}, "period": period},
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "away", "score": str(away), "team": {"abbreviation": "DET"}},
                        {"homeAway": "home", "score": str(home), "team": {"abbreviation": "PHI"}},
                    ]
                }
            ],
        }
        self.add("espn_scoreboard", "2026-10-26", at, {"events": [ev]})

    def summary(
        self, at: datetime, players: list[dict[str, Any]], status: str = "STATUS_HALFTIME"
    ) -> None:
        teams: dict[str, list[dict[str, Any]]] = {}
        for p in players:
            teams.setdefault(p["team"], []).append(
                {
                    "athlete": {"displayName": p["name"]},
                    "starter": p.get("starter", False),
                    "didNotPlay": p.get("dnp", False),
                    "ejected": p.get("ejected", False),
                    "stats": p.get("stats", []),
                }
            )
        body = {
            "header": {"competitions": [{"status": {"type": {"name": status}}}]},
            "boxscore": {
                "players": [
                    {
                        "team": {"abbreviation": t},
                        "statistics": [
                            {
                                "labels": [
                                    "MIN",
                                    "PTS",
                                    "FG",
                                    "3PT",
                                    "FT",
                                    "REB",
                                    "AST",
                                    "TO",
                                    "STL",
                                    "BLK",
                                    "OREB",
                                    "DREB",
                                    "PF",
                                    "+/-",
                                ],
                                "athletes": a,
                            }
                        ],
                    }
                    for t, a in teams.items()
                ]
            },
        }
        self.add("espn_summary", EID, at, body)

    def kalshi(
        self, series: str, at: datetime, markets: list[dict[str, Any]], page: int = 0
    ) -> None:
        self.add("kalshi", f"{series}_p{page}", at, {"markets": markets})

    def close(self) -> Path:
        with (self.root / "manifest.jsonl").open("w") as f:
            for r in sorted(self.manifest, key=lambda r: r["received_at"]):
                f.write(json.dumps(r) + "\n")
        return self.root


def box(
    name: str,
    team: str,
    mins: int,
    pts: int = 0,
    reb: int = 0,
    ast: int = 0,
    tpm: int = 0,
    pf: int = 0,
    **kw: Any,
) -> dict[str, Any]:
    stats = [
        str(mins),
        str(pts),
        "3-8",
        f"{tpm}-3",
        "0-0",
        str(reb),
        str(ast),
        "1",
        "0",
        "0",
        "0",
        str(reb),
        str(pf),
        "+2",
    ]
    return {
        "name": name,
        "team": team,
        "stats": [] if kw.get("no_stats") else stats,
        **{k: v for k, v in kw.items() if k != "no_stats"},
    }


def market(
    series: str,
    name: str,
    k: int,
    ask: float | None = 0.40,
    size: float = 100.0,
    bid: float | None = 0.38,
    status: str = "active",
    strike_type: str = "greater",
    unit: str = "points",
) -> dict[str, Any]:
    return {
        "event_ticker": f"{series}-26OCT26DETPHI",
        "ticker": f"{series}-26OCT26DETPHI-{name.replace(' ', '')}-{k}",
        "title": f"{name}: {k}+ {unit}",
        "strike_type": strike_type,
        "floor_strike": k - 0.5,
        "status": status,
        "yes_ask_dollars": None if ask is None else f"{ask:.4f}",
        "yes_ask_size_fp": f"{size:.2f}",
        "yes_bid_dollars": None if bid is None else f"{bid:.4f}",
        "no_ask_dollars": "0.9999",  # present in the real feed; the scorer must never read it
    }


def tiny_artifact(tmp: Path, p_shift: float = 0.0) -> Artifact:
    """A real artifact (boosters, Platt, glm_e) trained on random rows; for wiring tests."""
    path = tmp / "artifact.json"
    if path.exists():  # artifacts are write-once; a test calling twice gets the same one
        return load_artifact(path)
    rng = np.random.default_rng(0)
    n = 600
    xa = rng.normal(size=(n, len(hm.FEATURES_A)))
    stage_a = lgb.train(
        {"objective": "regression", "num_leaves": 4, "verbose": -1},
        lgb.Dataset(xa, label=rng.uniform(5, 20, n)),
        num_boost_round=5,
    )
    stage_b = {}
    for s in hm.STATS:
        xb = rng.normal(size=(n, len(hm.FEATURES_B)))
        stage_b[s] = lgb.train(
            {"objective": "binary", "num_leaves": 4, "verbose": -1},
            lgb.Dataset(xb, label=(rng.uniform(size=n) < 0.5).astype(float)),
            num_boost_round=5,
        )
    platt = {s: (p_shift, 1.0) for s in hm.STATS}
    glm = {s: {"coef": [0.5, 0.3, 0.1, -0.2], "intercept": 0.0} for s in hm.STATS}
    save_artifact(path, {"test": True}, stage_a, stage_b, platt, glm)
    return load_artifact(path)
