"""Routing table: build/validate on synthetic OOF, 2025 guard, registry round trip, daily route.

Temp DBs / stores only; never nba.duckdb.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from nba.db.connect import connect
from nba.registry.local import LocalRegistry
from nba.stack.oof import write_oof
from research.registry.routing import (
    CANDIDATES,
    RouteSpec,
    fetch_routes,
    format_table,
    promote_route,
    register_spec,
    route_hash,
    validate_spec,
)
from research.stack.adapters import quantile_grid
from research.stack.frozen import freeze_softmax
from research.stack.routebuild import BuildInputs, build_specs

LEDGER_TEXT = "rung0_injury_elo rung0_mov_elo context_residual recency"


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.md"
    p.write_text(LEDGER_TEXT)
    return p


def _synthetic(tmp_path: Path, with_2025: bool = False) -> tuple[BuildInputs, str, int]:
    """Two props candidates; A is right when start_rate10 == 1, B otherwise."""
    rng = np.random.default_rng(0)
    per_day = 20
    days = [pd.Timestamp("2023-10-24") + pd.Timedelta(days=i) for i in range(120)]
    days += [pd.Timestamp("2024-10-22") + pd.Timedelta(days=i) for i in range(120)]
    if with_2025:
        days += [pd.Timestamp("2025-10-22") + pd.Timedelta(days=i) for i in range(30)]
    rows = []
    for di, d in enumerate(days):
        for i in range(per_day):
            rows.append((di, d, i))
    df = pd.DataFrame(rows, columns=["di", "game_date", "i"])
    n = len(df)
    df["game_id"] = [f"g{a}_{b // 4}" for a, b in zip(df["di"], df["i"], strict=True)]
    df["player_id"] = df["i"]
    df["season"] = np.where(
        df["game_date"] < "2024-08-01", 2023, np.where(df["game_date"] < "2025-08-01", 2024, 2025)
    )
    x = rng.integers(0, 2, n)
    mu = rng.normal(15, 3, n)
    y = mu + rng.normal(0, 3, n)
    qa = quantile_grid(mu + np.where(x == 1, 0.0, 8.0), np.full(n, 3.0), "normal")
    qb = quantile_grid(mu + np.where(x == 1, 8.0, 0.0), np.full(n, 3.0), "normal")
    base = pd.DataFrame(
        {
            "target": "pts",
            "game_id": df["game_id"],
            "player_id": df["player_id"],
            "game_date": df["game_date"],
            "season": df["season"],
            "made_with_data_through": df["game_date"] - pd.Timedelta(days=1),
            "mean": mu,
        }
    )
    oof = str(tmp_path / "oof.duckdb")
    write_oof(base.assign(q_grid=list(qa)), "props_context_residual", "v1", oof)
    write_oof(base.assign(q_grid=list(qb)), "season_avg_pts", "v1-hl10", oof)
    hist = df[["game_id", "player_id", "game_date", "season"]].assign(
        played=True, pts=y, minutes=30.0, starter=x.astype(float)
    )
    cold = rng.random(n) < 0.15
    gate = pd.DataFrame(
        {
            "game_id": df["game_id"],
            "player_id": df["player_id"],
            "log_career": rng.normal(4, 1, n),
            "games_played_season": rng.integers(0, 80, n).astype(float),
            "min_avg10": rng.normal(25, 5, n),
            "min_trend": rng.normal(0, 3, n),
            "start_rate10": x.astype(float),
            "cold_start_bucket": np.where(cold, "cold", "established"),
        }
    )
    sl = gate[["game_id", "player_id", "cold_start_bucket", "games_played_season"]].assign(
        starter=x.astype(float)
    )
    n_ok = int((df["season"] <= 2024).sum())
    return BuildInputs(hist, gate, sl, with_arch=False), oof, n_ok


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[RouteSpec], int]:
    tmp = tmp_path_factory.mktemp("route")
    inp, oof, n_ok = _synthetic(tmp, with_2025=True)
    specs = build_specs(
        inp, oof_path=oof, stats=("pts",), sets=("full",), win=False, n_boot=100, thresholds=True
    )
    return specs, n_ok


def test_build_produces_valid_specs(built: tuple[list[RouteSpec], int], ledger: Path) -> None:
    specs, _ = built
    assert {s.target for s in specs} == {"pts", "thr_pts"}
    for s in specs:
        errs = validate_spec(s.to_dict(), ledger=ledger)
        assert errs == [], errs
    pts = next(s for s in specs if s.target == "pts")
    # planted context-dependent signal: the router beats the best single by a wide margin ...
    ev = pts.evidence
    assert ev["router_mean_score"] < ev["best_single_mean_score"] - 0.5
    assert ev["router_delta_vs_best"][2] < 0 and ev["router_q"] < 0.05
    # ... but the pre-registered slice rule (no slice > +0.01 worse than the best single, which
    # is ideal on half the rows here) is allowed to veto it: then the spec is a champion
    assert pts.kind == ("router" if ev["router_kept"] else "champion")
    assert (pts.gate is not None) == (pts.kind == "router")
    assert ev["decisions"]["softmax"]["reasons"]
    table = format_table([("draft", pts.to_dict())])
    assert "context_residual" in table and "clean-confirmed" in table


def test_build_never_touches_2025(built: tuple[list[RouteSpec], int]) -> None:
    specs, n_ok = built
    for s in specs:
        assert max(s.fit_window["seasons"]) <= 2024
        assert s.holdout_season == 2025
        assert s.fit_window["date_max"] < "2025-08-01"
    pts = next(s for s in specs if s.target == "pts")
    assert pts.fit_window["n_rows"] == n_ok  # the 2025 rows in the store were dropped
    assert pts.evidence["candidates"]["context_residual"]["n"] == n_ok


def test_cell_guardrails_in_spec(built: tuple[list[RouteSpec], int]) -> None:
    specs, _ = built
    for s in specs:
        for c in s.cells:
            if c["claim"] is not None:
                assert c["n"] >= 500 and c["games"] >= 150 and c["q"] < 0.05
            if not c["eligible"]:
                assert c["claim"] is None
    thr = next(s for s in specs if s.target == "thr_pts")
    assert any(c["dimension"] == "line_region" for c in thr.cells)


def test_validate_rejects_tampering_and_leaks(ledger: Path) -> None:
    pts = _router_spec_for_fixture()
    bad = json.loads(json.dumps(pts))
    bad["champion"] = "recency" if pts["champion"] != "recency" else "context_residual"
    assert any("route_hash" in e for e in validate_spec(bad, ledger=ledger))
    leak = json.loads(json.dumps(pts))
    leak["fit_window"]["seasons"] = [2023, 2024, 2025]
    leak["route_hash"] = route_hash(leak)
    assert any("holdout season" in e for e in validate_spec(leak, ledger=ledger))
    nog = json.loads(json.dumps(pts))
    nog["gate"] = None
    nog["route_hash"] = route_hash(nog)
    assert any("frozen gate" in e for e in validate_spec(nog, ledger=ledger))
    wrong = json.loads(json.dumps(pts))
    wrong["candidates"][0]["holdout_status"] = "untouched"
    wrong["route_hash"] = route_hash(wrong)
    assert any("marked untouched" in e for e in validate_spec(wrong, ledger=ledger))
    # rollover to a later holdout is allowed structurally (fit through 2025)
    roll = json.loads(json.dumps(pts))
    roll["holdout_season"] = 2026
    roll["fit_window"]["seasons"] = [2023, 2024, 2025]
    roll["route_hash"] = route_hash(roll)
    assert validate_spec(roll, ledger=ledger) == []


def test_validate_oof_store_checks(
    built: tuple[list[RouteSpec], int], tmp_path: Path, ledger: Path
) -> None:
    pts = next(s for s in built[0] if s.target == "pts").to_dict()
    errs = validate_spec(pts, ledger=ledger, oof_path=str(tmp_path / "nope.duckdb"))
    assert any("not found" in e for e in errs)


def test_registry_round_trip_and_promotion(
    built: tuple[list[RouteSpec], int], tmp_path: Path, ledger: Path
) -> None:
    pts = RouteSpec.from_dict(_router_spec_for_fixture())
    con = connect(tmp_path / "r.duckdb")
    try:
        reg = LocalRegistry(con, tmp_path / "store")
        v = register_spec(reg, pts, work_dir=tmp_path / "work")
        assert v == "v1"
        rows = fetch_routes(con, "pts")
        assert len(rows) == 1 and rows[0].tags["kind"] == "route" and rows[0].stage == "candidate"
        assert rows[0].spec()["route_hash"] == pts.to_dict()["route_hash"]
        with pytest.raises(PermissionError):  # router needs a verdict
            promote_route(reg, con, "pts", v, ledger=ledger)
        verdict = tmp_path / "v.json"
        verdict.write_text(
            json.dumps({"route_hash": pts.to_dict()["route_hash"], "kept": False, "target": "pts"})
        )
        with pytest.raises(PermissionError):
            promote_route(reg, con, "pts", v, verdict_path=verdict, ledger=ledger)
        verdict.write_text(
            json.dumps({"route_hash": pts.to_dict()["route_hash"], "kept": True, "target": "pts"})
        )
        promote_route(reg, con, "pts", v, verdict_path=verdict, ledger=ledger)
        assert fetch_routes(con, "pts")[0].stage == "production"
    finally:
        con.close()


# ------------------------------------------------------------------ daily application


def _router_spec_for_fixture() -> dict[str, Any]:
    """A frozen router whose gate features are the standard props features."""
    rng = np.random.default_rng(1)
    n = 600
    ctx = pd.DataFrame(
        {
            "log_career": rng.normal(3, 1, n),
            "games_played_season": rng.integers(0, 60, n).astype(float),
            "min_avg10": rng.normal(25, 5, n),
            "min_trend": rng.normal(0, 3, n),
            "start_rate10": rng.integers(0, 2, n).astype(float),
            "cold_start_bucket": rng.choice(["cold", "established"], n),
        }
    )
    mu = rng.normal(15, 3, n)
    y = mu + rng.normal(0, 3, n)
    sh = np.where(ctx["start_rate10"] > 0.5, 0.0, 3.0)
    cand = np.stack(
        [
            quantile_grid(mu + sh, np.full(n, 3.0), "normal"),
            quantile_grid(mu, np.full(n, 3.0), "normal"),
        ],
        axis=1,
    )
    from research.stack.data import StackData

    keys = pd.DataFrame(
        {
            "game_id": [f"x{i}" for i in range(n)],
            "player_id": np.arange(n),
            "game_date": pd.Timestamp("2023-11-01") + pd.to_timedelta(np.arange(n) // 10, "D"),
            "season": 2023,
        }
    )
    data = StackData("pts", ["context_residual", "recency"], keys, y, cand, ctx)
    gate = freeze_softmax(data, max_season=2024)
    cands = [
        {
            "id": cid,
            "registry_model": None,
            "registry_version": None,
            "oof_model": CANDIDATES[cid].oof_name("pts"),
            "oof_version": CANDIDATES[cid].oof_version,
            "holdout_status": CANDIDATES[cid].holdout_status,
        }
        for cid in ("context_residual", "recency")
    ]
    spec = RouteSpec(
        "pts",
        "full",
        "router",
        "context_residual",
        cands,
        2025,
        {"seasons": [2023], "date_min": "2023-11-01", "date_max": "2023-12-30", "n_rows": n},
        gate.to_dict(),
        gate.features,
    )
    return spec.to_dict()


def test_cli_validate_show_and_scenario_guard(
    tmp_path: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from research.registry.__main__ import main
    from research.registry.routing import write_spec

    spec = RouteSpec.from_dict(_router_spec_for_fixture())
    write_spec(spec, tmp_path / "drafts" / "pts_full")
    base = ["--db-path", str(tmp_path / "none.duckdb")]
    rc = main([*base, "route", "validate", "--drafts-dir", str(tmp_path / "drafts"),
               "--ledger", str(ledger)])  # fmt: skip
    assert rc == 0 and "OK" in capsys.readouterr().out
    assert main([*base, "route", "show", "--drafts-dir", str(tmp_path / "drafts")]) == 0
    assert "context_residual" in capsys.readouterr().out
    bad = json.loads((tmp_path / "drafts" / "pts_full" / "route.json").read_text())
    bad["champion"] = "recency"
    (tmp_path / "drafts" / "pts_full" / "route.json").write_text(json.dumps(bad))
    assert main([*base, "route", "validate", "--drafts-dir", str(tmp_path / "drafts"),
                 "--ledger", str(ledger)]) == 1  # fmt: skip
    # --scenario and promote cannot be combined (main reports the guard as exit code 1)
    assert main([*base, "route", "promote", "pts", "v1", "--scenario"]) == 1


def test_extra_candidates_file_rejects_frozen_season(tmp_path: Path) -> None:
    from research.registry.routing import load_extra_candidates

    bad = {
        "candidates": [
            {"id": "zz", "label": "x", "registry_model": None, "registry_version": None,
             "oof_model": "zz", "oof_version": "v1", "seasons": [2024, 2025],
             "holdout_status": "untouched", "holdout_basis": "-", "ledger_key": "zz"},
        ]
    }  # fmt: skip
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="< 2025"):
        load_extra_candidates(p)
