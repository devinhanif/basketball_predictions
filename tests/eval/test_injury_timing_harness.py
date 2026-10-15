# ruff: noqa: E501
"""Unit tests for the INJURY_TIMING harness: freeze/pin refusals, scoring, words, synthetic rehearsal, planted future,
the cross-snapshot walk-forward plumbing (stubbed model) and the gate arithmetic. Synthetic data only (no real data, no fit)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import injury_timing_eval as ev
from research.eval import injury_timing_gate as gate
from research.eval import injury_timing_rule as rule

DOC = Path(__file__).resolve().parents[2] / "docs/prereg/INJURY_TIMING.md"


def _score(
    mS: float, mA0: float, sA0: float, hi: float = -0.001, p: float = 0.001, hi23: float = -0.001
) -> dict[str, Any]:
    def d(point: float, h: float, pp: float = 0.5) -> dict[str, float]:
        return {
            "point": point,
            "lo": point - 0.002,
            "hi": h,
            "p_lt0": pp,
            "n": 1000,
            "n_games": 100,
        }

    def stat(season: str) -> dict[str, Any]:
        h = hi if season == "2024" else hi23
        return {
            "n": 1000,
            "crps": {"A0": 1.0, "S": 1.0, "M": 1.0},
            "M_minus_S": d(mS, h, p),
            "M_minus_A0": d(mA0, 0.0),
            "S_minus_A0": d(sA0, 0.001),
        }

    return {s: {st: stat(s) for st in ev.STATS} for s in ("2023", "2024")}


def test_words_follow_the_rule_floor_and_replication() -> None:
    assert ev.words(_score(-0.008, -0.01, 0.0), True)["word"] == "FIX_WORTH_IT"
    assert (
        ev.words(_score(-0.003, -0.01, 0.0), True)["word"] == "NULL"
    )  # significant but under the practical floor
    assert (
        ev.words(_score(-0.008, -0.01, 0.0, hi=0.001, p=0.2), True)["word"] == "NULL"
    )  # CI straddles zero
    assert (
        ev.words(_score(-0.008, -0.01, 0.0, hi23=0.001), True)["word"] == "NULL"
    )  # 2023 does not replicate
    assert ev.words(_score(-0.008, -0.01, 0.0), False)["word"] == "GUARD_FAIL"
    w = ev.words(_score(0.004, 0.004, 0.0, hi=0.0042, p=0.99), True)
    assert w["word"] == "NULL" and w["flags"]["CRPS_HARM"]


def test_rehearsal_recovers_each_synthetic_truth() -> None:
    for truth, word in (("null", "NULL"), ("fix", "FIX_WORTH_IT"), ("harm", "NULL")):
        d = ev.words(ev.score(ev.synthetic_arms(truth)), True)
        assert d["word"] == word, truth
    assert ev.words(ev.score(ev.synthetic_arms("harm")), True)["flags"]["CRPS_HARM"]
    assert ev.words(ev.score(ev.synthetic_arms("fix")), True)["flags"]["SKEW_HARMS_LIVE_ANALOG"]


def test_clustered_delta_is_game_clustered_and_signed() -> None:
    gid = np.repeat(np.arange(200), 5).astype(str)
    d = ev.clustered_delta(np.full(1000, -0.1), gid, seed=1, n_boot=200)
    assert (
        d["point"] == pytest.approx(-0.1)
        and d["hi"] < 0
        and d["n_games"] == 200
        and d["p_lt0"] < 0.05
    )
    z = ev.clustered_delta(np.zeros(1000), gid, seed=1, n_boot=200)
    assert z["p_lt0"] > 0.9


def test_bh_matches_hand_value() -> None:
    assert ev.bh_adjust(np.array([0.01, 0.02, 0.03, 0.5])) == pytest.approx([0.04, 0.04, 0.04, 0.5])


def test_crps_int_zero_on_a_point_mass_and_uses_the_integer_map() -> None:
    q = np.full((1, 199), 4.2)
    assert ev.crps_int(q, np.array([4.0]))[0] == pytest.approx(0.0)  # 4.2 maps to 4
    assert ev.crps_int(np.full((1, 199), 4.6), np.array([4.0]))[0] > 0.0  # 4.6 maps to 5


def test_g4_requires_the_pinned_values() -> None:
    ok = {st: {s: ev.A0_PINS[st][s] for s in ("2023", "2024")} for st in ev.STATS}
    assert ev.g4_a0_repro(ok)["pass"]
    bad = {st: dict(v) for st, v in ok.items()}
    bad["pts"]["2024"] += 1e-4
    assert not ev.g4_a0_repro(bad)["pass"]


def _rows(n_games: int = 80) -> pl.DataFrame:
    """Each game: a 11:00 stamp with player 1 OUT and a 17:00 stamp (tip 19:30 -> lead 150) with players 1 and 2 OUT."""
    recs = []
    for g in range(n_games):
        day = dt.datetime(2024, 1, 1) + dt.timedelta(days=g)
        tip = day.replace(hour=19, minute=30)
        for hh, pids in ((11, (1,)), (17, (1, 2))):
            for p in pids:
                recs.append((f"g{g}", p, "out", day.replace(hour=hh), day.date(), tip, 2023))
    return pl.DataFrame(
        recs,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
            "tip_et": pl.Datetime("us"),
            "season": pl.Int64,
        },
        orient="row",
    )


def test_planted_future_row_after_the_cutoff_never_enters() -> None:
    r = ev.planted_future_check(_rows(), n_games=40)
    assert (
        r["leaked_after_T60"] == 0
        and r["detected_at_T60_edge"] == r["games_planted"] == 40
        and r["pass"]
    )


def test_planted_future_check_fails_if_the_cutoff_is_loosened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nba.features import injury_report as ir

    real = ir.usable_report_rows

    def loose(rows: pl.DataFrame, config: Any) -> pl.DataFrame:
        return real(
            rows,
            ir.ReportTriggerConfig(statuses=config.statuses, lead_minutes=0, tip_source="real"),
        )

    monkeypatch.setattr(ir, "usable_report_rows", loose)
    assert not ev.planted_future_check(_rows(), n_games=40)["pass"]


def test_gate_arithmetic_on_stored_style_and_fresh_style_rows() -> None:
    stored = _rows()
    blk = gate.season_block(stored, 80)
    assert (
        blk["games_with_usable_snapshot"] == 80
        and blk["fresh_share_lead_le_90"] == 0.0
        and blk["lead_min_p10_p50_p90"][1] == 150.0
    )
    fresh = stored.with_columns(
        pl.when(pl.col("as_of").dt.hour() == 17)
        .then(pl.col("as_of") + pl.duration(minutes=75))
        .otherwise(pl.col("as_of"))
        .alias("as_of")
    )
    b2 = gate.season_block(fresh, 80, stored)
    assert (
        b2["fresh_share_lead_le_90"] == 1.0 and b2["vs_stored"]["games_out_set_differs"] == 0
    )  # same OUT set, only the stamp moved
    v = gate.gate_verdict({"2023": {**b2, "games_with_usable_snapshot": 1300}}, True)
    assert not v["gate_pass"]  # no OUT-set difference to learn from
    assert not gate.gate_verdict({"2023": blk}, False)["gate_pass"]


def test_collect_cross_fits_on_train_frame_and_predicts_from_test_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nba.props.context_residual as cr

    n_tr, n_te = 2000, 60
    dates = [dt.date(2022, 11, 1) + dt.timedelta(days=i // 20) for i in range(n_tr)] + [
        dt.date(2023, 3, 2)
    ] * n_te
    n = n_tr + n_te

    def frame(feat: float) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "game_id": [f"g{i // 10}" for i in range(n)],
                "player_id": list(range(n)),
                "season": [2022] * n_tr + [2023] * n_te,
                "game_date": dates,
                "y": [1.0] * n,
                "resid": [0.0] * n,
                "feat": [feat] * n,
            }
        )

    class Stub:
        def __init__(self, stat: str, cfg: Any, names: list[str]) -> None:
            self.mean_head = type("H", (), {"predict": staticmethod(lambda x: np.zeros(len(x)))})()

        def fit(self, train: pl.DataFrame) -> Stub:
            self.tr_feat = float(train["feat"].mean())  # type: ignore[arg-type]
            return self

        def predict_components(self, block: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
            return block["feat"].to_numpy() + 100.0 * self.tr_feat, np.ones(block.height)

        def _scale(self, x: np.ndarray) -> np.ndarray:
            return np.ones(len(x))

    monkeypatch.setattr(cr, "ContextResidualModel", Stub)
    monkeypatch.setattr(cr, "stat_frame", lambda f, stat: f)
    monkeypatch.setattr(cr, "stat_feature_names", lambda stat: ["feat"])
    monkeypatch.setattr(cr, "split_calibration", lambda tr, cfg: (tr, tr.tail(500)))
    out = ev.collect_cross(frame(1.0), frame(2.0), "pts", object(), (2023,))
    assert np.allclose(
        out["row_c"], 2.0 + 100.0 * 1.0
    )  # features from the test frame, fit from the train frame
    same = ev.collect_cross(frame(1.0), frame(1.0), "pts", object(), (2023,))
    assert np.allclose(same["row_c"], 101.0)
    with pytest.raises(RuntimeError, match="same rows"):
        ev.collect_cross(
            frame(1.0),
            frame(1.0).with_columns(player_id=pl.col("player_id") + 1),
            "pts",
            object(),
            (2023,),
        )
    with pytest.raises(ValueError, match="holdout"):
        ev.collect_cross(frame(1.0), frame(1.0), "pts", object(), (2025,))


# --- freeze / pin refusals (same contract as the MEDIAN_BIAS module)


def test_require_frozen_refuses_without_a_matching_commit(tmp_path: Path) -> None:
    doc = tmp_path / "INJURY_TIMING.md"
    doc.write_text("rule\n" + rule.MARKER + "\n")
    sha = rule.text_sha(doc.read_text())
    with pytest.raises(RuntimeError):
        rule.require_frozen(None, doc)
    with pytest.raises(RuntimeError):
        rule.require_frozen("0" * 64, doc, lambda *a: "")
    with pytest.raises(RuntimeError):
        rule.require_frozen(sha, doc, lambda *a: "")


def test_run_refuses_before_any_data_without_the_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_preflight(*a: Any, **k: Any) -> dict[str, Any]:
        called.append("preflight")
        return {}

    monkeypatch.setattr(ev, "preflight", fake_preflight)
    with pytest.raises(RuntimeError):
        ev.main(["run", "--sha", "0" * 64])
    assert (
        called == []
    )  # the freeze refusal comes before the preflight, and the preflight before any outcome


def test_pin_tracks_every_harness_file() -> None:
    assert set(rule.HARNESS_FILES) == {
        "research/eval/injury_timing_eval.py",
        "research/eval/injury_timing_rule.py",
        "research/eval/injury_timing_gate.py",
    }
    root = Path(__file__).resolve().parents[2]
    assert all((root / f).exists() for f in rule.HARNESS_FILES)
    b = {k: "0" * 64 for k in rule.HARNESS_FILES}
    assert rule.pin_of(b) == rule.pin_of(dict(reversed(list(b.items()))))


def test_rule_text_carries_the_pinned_numbers_and_the_marker() -> None:
    text = DOC.read_text()
    assert rule.MARKER in text
    for st in ev.STATS:
        for s in ("2023", "2024"):
            assert f"{ev.A0_PINS[st][s]:.6f}" in text
    for k, v in (("FLOOR", ev.FLOOR), ("HARM", ev.HARM)):
        assert str(v) in text.replace("−", "-"), k
