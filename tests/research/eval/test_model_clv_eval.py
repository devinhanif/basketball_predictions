# ruff: noqa: E501
"""MODEL_CLV harness on a synthetic pairs frame (no real data, no result is read)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

import research.eval.model_clv_eval as mc
from research.eval.minutes_hazard_eval import MARKER, frozen_sha


def synth(season: int, seed: int, n_games: int = 160) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    base = dt.datetime(season, 11, 1, 23, 0)
    for g in range(n_games):
        gid = f"002{season % 100}{g:05d}"
        tip = base + dt.timedelta(hours=g)
        for pid in range(1, 9):
            for stat in mc.STATS:
                line = float(rng.integers(2, 25)) + 0.5
                m0 = float(rng.uniform(0.35, 0.65))
                pm = float(np.clip(m0 + rng.normal(0, 0.08), 0.02, 0.98))
                over = float(rng.random() < m0)
                extra = (float(rng.random()), float(rng.random()), float(rng.integers(0, 3)))
                for book in (*mc.BOOKS, "consensus"):
                    mult = float(np.clip(m0 + rng.normal(0, 0.01), 0.05, 0.95))
                    for kind in ("t60", "t5"):
                        if kind == "t5" and rng.random() < 0.03:
                            continue
                        mu = (
                            mult
                            if kind == "t60"
                            else float(np.clip(mult + rng.normal(0, 0.01), 0.05, 0.95))
                        )
                        ln = line if kind == "t60" or rng.random() < 0.85 else line + 1.0
                        ts = tip - dt.timedelta(minutes=64 if kind == "t60" else 9)
                        rows.append({
                            "game_id": gid, "player_id": pid, "stat": stat, "book": book, "snapshot_kind": kind,
                            "snapshot_ts": ts, "line": ln, "price_over": -115, "price_under": -105,
                            "p_over_raw": mu + 0.025, "p_under_raw": 1 - mu + 0.025, "p_over_mult": mu,
                            "p_model": pm, "over": over, "tipoff_utc": tip, "starter10": extra[0],
                            "has_report": int(extra[1] > 0.5), "n_out_rot": extra[2], "season": season,
                            "game_date": tip.date(),
                        })  # fmt: skip
    return pl.DataFrame(rows).with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("price_over").cast(pl.Int64),
        pl.col("price_under").cast(pl.Int64),
        pl.col("season").cast(pl.Int32),
    )


@pytest.fixture(scope="module")
def pairs_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("pairs")
    for i, s in enumerate(mc.SEASONS):
        synth(s, i).write_parquet(d / f"pairs_{s}.parquet")
    return d


def test_season_2025_refused(pairs_dir: Path) -> None:
    with pytest.raises(ValueError):
        mc.load_season(pairs_dir, 2025)


def test_dec_and_bootstrap() -> None:
    assert mc.dec(np.array([100.0, -200.0, 150.0])).tolist() == [2.0, 1.5, 2.5]
    gi = np.repeat(np.arange(50), 4)
    v = np.random.default_rng(1).normal(0.2, 1.0, len(gi))
    r = mc.Boot(gi, 50).mean(v, np.ones(len(gi), bool))
    assert r["lo"] < r["mean"] < r["hi"] and r["n"] == 200 and 0 <= r["p"] <= 1


def test_side_and_clv_sign(pairs_dir: Path) -> None:
    L = mc.load_season(pairs_dir, 2023)
    c, edge, sel = mc.arm_choice(L, "M1")
    assert np.all(edge[sel] >= 0.03)
    over = mc.side_arrays(L, np.ones(L["n"], bool))["clv"]
    under = mc.side_arrays(L, np.zeros(L["n"], bool))["clv"]
    ok = np.isfinite(over)
    assert np.allclose(over[ok], -under[ok]) and ok.sum() == L["unch"].sum()


def test_shuffle_keeps_group_multiset(pairs_dir: Path) -> None:
    L = mc.load_season(pairs_dir, 2023)
    pm = mc.shuffle_model(L, 3)
    assert not np.array_equal(pm, L["pm"]) and np.isclose(pm.sum(), L["pm"].sum())
    pm2 = mc.shuffle_model(L, 3)
    assert np.array_equal(pm, pm2)


def test_planted_future_known_at(pairs_dir: Path) -> None:
    """A t5 capture moved after tip must fail the known-at check."""
    L = mc.load_season(pairs_dir, 2023)
    L["ts5"] = L["tip"] + pl.Series([dt.timedelta(minutes=1)] * L["n"])
    tests, _ = mc.check_2({2023: L})
    assert not tests[0]["ok"]


def test_gate_run_report(pairs_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "LOCK", tmp_path / "no.lock")
    doc = tmp_path / "MODEL_CLV.md"
    doc.write_text("# x\n\nStatus: DRAFT\n\n=== RESULTS BELOW ===\n")
    out = tmp_path / "out"
    g = mc.run_gate(pairs_dir, doc, frozen_sha(doc), True, out)
    assert g["max_season_loaded"] <= 2024 and g["tests"]
    fam = g["family_cells"]
    assert fam and all(t["ok"] for t in g["tests"] if t["fatal"]), [
        t for t in g["tests"] if not t["ok"] and t["fatal"]
    ]
    shas = g["pairs_sha256"]
    body = f"# x\n\nStatus: FROZEN test\nFAMILY_CELLS: {', '.join(fam)}\nPAIRS_SHA256_2023: {shas['2023']}\nPAIRS_SHA256_2024: {shas['2024']}\n\n{MARKER}\n"
    doc.write_text(body)
    sha = frozen_sha(doc)
    with pytest.raises(SystemExit):
        mc.require_frozen(doc, "0" * 64, False)
    assert mc.require_frozen(doc, sha, False) == sha
    # run refuses on a draft gate record
    with pytest.raises(RuntimeError):
        mc.run_arms(pairs_dir, doc, sha, out)
    mc.run_gate(pairs_dir, doc, sha, False, out)
    res = mc.run_arms(pairs_dir, doc, sha, out)
    assert "passing_cells" in res["rules"] and res["l1"]["n_train_2023"] > 100
    assert res["regression"]["2023"]["n"] > 50
    # synthetic noise: no cell may be a pass
    assert res["rules"]["passing_cells"] == []
    (tmp_path / "x").mkdir()
    monkeypatch.setattr(mc, "REPORT_MD", tmp_path / "r.md")
    assert "MODEL_CLV results" in mc.render_report(out)
    assert mc.write_ledger(out).read_text().count("T###") > 5
    assert json.loads((out / "results.json").read_text())["n_boot"] == 2000
