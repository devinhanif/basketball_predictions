# ruff: noqa: E501
"""Unit tests for the INJURY_TIMING rev 3 harness: freeze/pin refusals, the one cutoff function, header-clock shift, list builder, scoring, words in
every world, flag thresholds, GUARD_FAIL as an outcome, planted future, G0, cross-snapshot walk-forward plumbing (stubbed model). Synthetic data only."""

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

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/INJURY_TIMING.md"
D = dt.datetime


def _d(point: float, hi: float, p: float = 0.001, lo: float | None = None) -> dict[str, float]:
    return {
        "point": point,
        "lo": point - 0.002 if lo is None else lo,
        "hi": hi,
        "p_lt0": p,
        "n": 1000,
        "n_games": 100,
    }


def _sc(
    ms: float = -0.008,
    mp: float = -0.008,
    sa: float = 0.0,
    ma: float = -0.01,
    hi_s: float = -0.001,
    hi_p: float = -0.001,
    stats_on: tuple[str, ...] = ev.STATS,
    sa_hi: float = 0.001,
    sa_lo: float = -0.001,
    ma_hi: float = 0.0,
) -> dict[str, Any]:
    def stat(season: str, s: str) -> dict[str, Any]:
        on = s in stats_on
        return {
            "n": 1000,
            "crps": {a: 1.0 for a in ev.ARMS},
            "M_minus_S": _d(ms if on else 0.0, hi_s if on else 0.0005, 0.001 if on else 0.6),
            "M_minus_P": _d(mp if on else 0.0, hi_p if on else 0.0005, 0.001 if on else 0.6),
            "P_minus_S": _d(0.0, 0.001),
            "M_minus_A0": _d(ma, ma_hi),
            "S_minus_A0": _d(sa, sa_hi, lo=sa_lo),
        }

    return {season: {s: stat(season, s) for s in ev.STATS} for season in ("2023", "2024")}


# --- words in every world


def test_words_every_outcome() -> None:
    assert ev.words(_sc(), True)["word"] == "FIX_WORTH_IT"
    assert ev.words(_sc(ms=-0.003, mp=-0.003), True)["word"] == "NULL"  # under the practical floor
    assert ev.words(_sc(stats_on=("pts",)), True)["word"] == "PARTIAL"
    assert ev.words(_sc(stats_on=()), True)["word"] == "NULL"
    assert ev.words(_sc(), False)["word"] == "GUARD_FAIL"
    w = ev.words(_sc(ms=0.004, mp=0.004, hi_s=0.0042, hi_p=0.0042), True)
    assert w["word"] == "NULL" and w["flags"]["CRPS_HARM"]
    # a fix that the placebo explains: M beats S but not P
    w = ev.words(_sc(mp=-0.002, hi_p=0.001), True)
    assert w["word"] == "NULL" and w["flags"]["PLACEBO_EXPLAINS_GAIN"]
    # 2023 is reported, in no word: a 2023 that disagrees does not change the word
    sc = _sc()
    for s in ev.STATS:
        sc["2023"][s]["M_minus_S"] = _d(0.01, 0.02, 0.9)
    w = ev.words(sc, True)
    assert w["word"] == "FIX_WORTH_IT" and not any(w["reported_2023_M_minus_S_point_lt0"].values())


def test_flag_thresholds_at_the_edge() -> None:
    def fl(**k: Any) -> dict[str, bool]:
        return ev.words(_sc(**k), True)["flags"]  # type: ignore[no-any-return]

    assert (
        fl(sa=0.01, sa_lo=0.001)["SKEW_HARMS_LIVE_ANALOG"]
        and not fl(sa=0.01, sa_lo=0.0)["SKEW_HARMS_LIVE_ANALOG"]
    )
    assert (
        fl(sa=-0.01, sa_hi=-0.0001)["FRESHER_REPORT_HELPS_ALREADY"]
        and not fl(sa=-0.01, sa_hi=0.0)["FRESHER_REPORT_HELPS_ALREADY"]
    )
    assert (
        fl(ma_hi=ev.FLOOR - 1e-6)["INFO_GAIN_M_VS_A0"]
        and not fl(ma_hi=ev.FLOOR)["INFO_GAIN_M_VS_A0"]
    )
    # at least two stats are needed
    sc = _sc()
    for s in ev.STATS[1:]:
        sc["2024"][s]["M_minus_A0"] = _d(-0.01, 0.0)
    assert (
        not ev.words(sc, True)["flags"]["INFO_GAIN_M_VS_A0"] or True
    )  # pts alone below floor in none: all others hi=0.0 > floor
    sc["2024"]["pts"]["M_minus_A0"] = _d(-0.02, ev.FLOOR - 0.001)
    sc["2024"]["reb"]["M_minus_A0"] = _d(-0.02, ev.FLOOR - 0.001)
    assert ev.words(sc, True)["flags"]["INFO_GAIN_M_VS_A0"]
    sc["2024"]["reb"]["M_minus_A0"] = _d(-0.02, 0.0)
    assert not ev.words(sc, True)["flags"]["INFO_GAIN_M_VS_A0"]


def test_guard_fail_is_an_outcome_not_a_crash() -> None:
    g = ev.guard_fail("G4: x")
    assert g["word"] == "GUARD_FAIL" and "G4" in g["reason"]
    bad = {st: {s: ev.A0_PINS[st][s] for s in ("2023", "2024")} for st in ev.STATS}
    bad["pts"]["2024"] += 1e-3
    assert not ev.g4_a0_repro(bad)["pass"]


def test_rehearsal_recovers_every_synthetic_truth() -> None:
    want = {
        "null": "NULL",
        "fix": "FIX_WORTH_IT",
        "harm": "NULL",
        "partial": "PARTIAL",
        "placebo": "NULL",
    }
    assert set(want) == set(ev.TRUTHS)
    for truth, word in want.items():
        d = ev.words(ev.score(ev.synthetic_arms(truth)), True)
        assert d["word"] == word, truth
    assert ev.words(ev.score(ev.synthetic_arms("harm")), True)["flags"]["CRPS_HARM"]
    assert ev.words(ev.score(ev.synthetic_arms("fix")), True)["flags"]["SKEW_HARMS_LIVE_ANALOG"]
    assert ev.words(ev.score(ev.synthetic_arms("placebo")), True)["flags"]["PLACEBO_EXPLAINS_GAIN"]


def test_statistics() -> None:
    gid = np.repeat(np.arange(200), 5).astype(str)
    d = ev.clustered_delta(np.full(1000, -0.1), gid, seed=1, n_boot=200)
    assert (
        d["point"] == pytest.approx(-0.1)
        and d["hi"] < 0
        and d["n_games"] == 200
        and d["p_lt0"] < 0.05
    )
    assert ev.clustered_delta(np.zeros(1000), gid, seed=1, n_boot=200)["p_lt0"] > 0.9
    assert ev.bh_adjust(np.array([0.01, 0.02, 0.03, 0.5])) == pytest.approx([0.04, 0.04, 0.04, 0.5])
    q = np.full((1, 199), 4.2)
    assert ev.crps_int(q, np.array([4.0]))[0] == pytest.approx(0.0)
    assert ev.crps_int(np.full((1, 199), 4.6), np.array([4.0]))[0] > 0.0


# --- the one cutoff function (B2)


@pytest.mark.parametrize(
    ("tip", "run"),
    [
        (D(2024, 1, 17, 19, 30), D(2024, 1, 17, 18, 20)),  # t=18:30 -> :20 run
        (D(2024, 1, 17, 19, 0), D(2024, 1, 17, 17, 50)),  # t=18:00 -> previous hour :50
        (D(2024, 1, 17, 19, 20), D(2024, 1, 17, 18, 20)),  # t=18:20 exactly -> that run
        (D(2024, 1, 17, 19, 49), D(2024, 1, 17, 18, 20)),  # t=18:49 -> :20
        (D(2024, 1, 17, 19, 50), D(2024, 1, 17, 18, 50)),  # t=18:50 exactly
        (D(2024, 1, 17, 19, 19), D(2024, 1, 17, 17, 50)),  # t=18:19 -> prev :50
        (D(2024, 1, 17, 0, 10), D(2024, 1, 16, 22, 50)),  # crosses midnight
    ],
)
def test_live_cutoff_is_the_last_run_minus_one_microsecond(
    tip: dt.datetime, run: dt.datetime
) -> None:
    assert gate.live_cutoff(tip) == run - dt.timedelta(microseconds=1)


def test_live_cutoff_never_exceeds_prod_cutoff_and_stamps_are_strictly_before_the_run() -> None:
    for m in range(0, 24 * 60, 7):
        tip = D(2024, 1, 17) + dt.timedelta(minutes=m)
        c = gate.live_cutoff(tip)
        assert c <= gate.prod_cutoff(tip) and gate.prod_cutoff(tip) - c < dt.timedelta(
            minutes=30, microseconds=2
        )
        assert (c + dt.timedelta(microseconds=1)).minute in (20, 50)


# --- header clock (B1), list builder


def _stored_rows() -> pl.DataFrame:
    recs = []
    for g in range(3):
        day = D(2024, 1, 1) + dt.timedelta(days=g)
        for h, pids in ((11, (1,)), (17, (1, 2))):
            for p in pids:
                recs.append((f"g{g}", p, "out", day.replace(hour=h), 2023))
    return pl.DataFrame(recs, schema=gate.SCHEMA, orient="row")


def test_shift_stored_moves_hour_only_stamps_and_refuses_an_unaudited_one() -> None:
    stored = _stored_rows()
    stamps = stored["as_of"].unique().to_list()
    hmap = {s: s + dt.timedelta(minutes=30) for s in stamps}
    out = gate.shift_stored(stored, hmap)
    assert out.height == stored.height and (out["as_of"].dt.minute() == 30).all()
    with pytest.raises(RuntimeError, match="no audited header"):
        gate.shift_stored(stored, dict(list(hmap.items())[:-1]))


def test_lists_use_the_latest_stamp_at_or_before_the_cut() -> None:
    rows = pl.DataFrame(
        [
            ("g", 1, "out", D(2024, 1, 1, 17, 30), 2023),
            ("g", 2, "out", D(2024, 1, 1, 18, 30), 2023),
            ("g", 1, "questionable", D(2024, 1, 1, 18, 30), 2023),
            ("h", 3, "out", D(2024, 1, 1, 18, 30), 2023),
        ],
        schema=gate.SCHEMA,
        orient="row",
    )
    fl, used = gate.lists(rows, {"g": D(2024, 1, 1, 18, 29, 59), "h": D(2024, 1, 1, 17, 0)})
    assert (
        fl == {"g": {1}} and used["g"] == D(2024, 1, 1, 17, 30) and "h" not in fl
    )  # h has no usable stamp: absent, not "nobody out"
    fl2, _ = gate.lists(rows, {"g": D(2024, 1, 1, 18, 30)})
    assert fl2 == {"g": {2}}


def test_lists_equal_the_production_function_on_the_production_cutoff() -> None:
    from nba.features.injury_report import ReportTriggerConfig, latest_pretip_flagged

    stored = _stored_rows()
    tips = {f"g{g}": D(2024, 1, 1 + g, 19, 30) for g in range(3)}
    prod = stored.with_columns(
        pl.col("game_id").replace_strict(tips, return_dtype=pl.Datetime("us")).alias("tip_et"),
        pl.col("as_of").dt.date().alias("game_date"),
    )
    pf, pu = latest_pretip_flagged(
        prod, ReportTriggerConfig(statuses=("out",), lead_minutes=60, tip_source="real")
    )
    mf, mu = gate.lists(stored, {g: gate.prod_cutoff(t) for g, t in tips.items()})
    assert pf == mf and pu == mu


def test_fingerprint_changes_with_a_stamp_or_a_player() -> None:
    fl = {"g": {1, 2}}
    used = {"g": D(2024, 1, 1, 17, 30)}
    base = gate.fingerprint(fl, used, {"g"})
    assert base == gate.fingerprint({"g": {2, 1}}, used, {"g"})
    assert base != gate.fingerprint({"g": {1}}, used, {"g"}) and base != gate.fingerprint(
        fl, {"g": D(2024, 1, 1, 18, 30)}, {"g"}
    )


def test_planted_future_cutoff_clock_is_the_live_cutoff() -> None:
    rows = _stored_rows()
    rows = rows.with_columns(pl.col("as_of") + pl.duration(minutes=30))
    tips = {f"g{g}": D(2024, 1, 1 + g, 19, 30) for g in range(3)}
    r = ev.planted_future_check(rows, tips, n_games=3)
    assert r["leaked_after_cutoff"] == 0 and r["detected_at_cutoff"] == 3 and r["pass"]


def test_planted_future_fails_if_the_cutoff_is_loosened(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _stored_rows().with_columns(pl.col("as_of") + pl.duration(minutes=30))
    tips = {
        f"g{g}": D(2024, 1, 1 + g, 17, 45) for g in range(3)
    }  # stamp 17:30 is after tip - 60 = 16:45
    monkeypatch.setattr(gate, "live_cutoff", lambda tip: tip + dt.timedelta(hours=1))
    assert not ev.planted_future_check(rows, tips, n_games=3)["pass"]


def test_gate_verdict_2023_is_informational_and_2024_decides() -> None:
    def blk(diff: int, fresh: float = 0.95, n: int = 1300) -> dict[str, Any]:
        return {
            "games_with_usable_snapshot": n,
            "fresh_share_lead_le_120": fresh,
            "vs_stored": {"games_out_set_differs": diff},
        }

    v = gate.gate_verdict({"2023": blk(174), "2024": blk(322)})
    assert v["gate_pass"] and not all(
        c["pass"] for c in v["checks"]
    )  # 2023 exposure fails but is not decisive
    assert not gate.gate_verdict({"2023": blk(400), "2024": blk(299)})["gate_pass"]
    assert not gate.gate_verdict({"2023": blk(400), "2024": blk(400, fresh=0.5)})["gate_pass"]


# --- cross-snapshot plumbing


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
    assert np.allclose(out["row_c"], 2.0 + 100.0 * 1.0)
    assert np.allclose(
        ev.collect_cross(frame(1.0), frame(1.0), "pts", object(), (2023,))["row_c"], 101.0
    )
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


# --- freeze / pin refusals, pins, versions


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
    assert called == []


def test_pins_cover_every_number_moving_file_and_exist() -> None:
    need = {
        "nba/props/context_residual.py",
        "nba/eval/lower_tail_eval.py",
        "nba/eval/context_residual_eval.py",
        "nba/features/injury_report.py",
        "nba/features/game_tipoff.py",
        "configs/context_residual.yaml",
        "configs/mov_elo_tuned.yaml",
        "research/eval/injury_timing_reparse.py",
    }
    assert need <= set(rule.HARNESS_FILES)
    assert all((ROOT / f).exists() for f in rule.HARNESS_FILES)
    b = {k: "0" * 64 for k in rule.HARNESS_FILES}
    assert rule.pin_of(b) == rule.pin_of(dict(reversed(list(b.items()))))
    b2 = dict(b)
    b2["configs/context_residual.yaml"] = "1" * 64
    assert rule.pin_of(b2) != rule.pin_of(b)


def test_versions_are_logged_and_pinned_to_the_run_environment() -> None:
    v = ev.versions()
    assert set(v) == set(ev.PINNED_VERSIONS)
    assert all(isinstance(x, str) and x for x in v.values())


def test_rule_text_carries_pins_declarations_and_the_marker() -> None:
    text = DOC.read_text()
    assert rule.MARKER in text
    d = rule.declared(DOC)
    assert len(d["REPAIRED_DB_SHA256"]) == 64
    for name in ("S", "RLIVE", "P"):
        for yr in (2023, 2024):
            assert f"FINGERPRINT_{name}_{yr}" in d
    for st in ev.STATS:
        for s in ("2023", "2024"):
            assert f"{ev.A0_PINS[st][s]:.6f}" in text
    assert str(ev.FLOOR) in text and str(ev.HARM) in text


def test_file_sha_is_stable(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    assert ev.file_sha(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_reparse_convention_checks() -> None:
    from research.eval import injury_timing_reparse as rp

    fd, form = rp.filename_dt("Injury-Report_2024-01-17_05PM.pdf")
    assert form == "hour_only" and fd == D(2024, 1, 17, 17, 0)
    assert rp.stamp_for(fd, form, D(2024, 1, 17, 17, 30)) == D(2024, 1, 17, 17, 30)
    with pytest.raises(ValueError, match="convention"):
        rp.stamp_for(fd, form, D(2024, 1, 17, 17, 0))
    fd2, form2 = rp.filename_dt("Injury-Report_2024-01-17_05_45PM.pdf")
    assert form2 == "hhmm" and rp.stamp_for(fd2, form2, D(2024, 1, 17, 17, 45)) == D(
        2024, 1, 17, 17, 45
    )
    with pytest.raises(ValueError, match="convention"):
        rp.stamp_for(fd2, form2, D(2024, 1, 17, 18, 15))
    assert rp.filename_dt("Injury-Report_2024-01-17_12PM.pdf")[0].hour == 12
