"""Tests for the ALT_LINE_70 run harness on a synthetic data root: the refusals (rule sha, freeze
commit, FROZEN status, ALT_LADDER_HIST not frozen, pinned inputs), the dry run that never reads
``y``, the words, and the invariants of a full scoring pass (small B and R)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import alt_harness_common as hc
from research.eval import alt_line_70_eval as ev
from tests.research.eval.alt_synth import build_root

RULE_TEXT = "# {n}\n\nStatus: **{s}**\n- body\n\n=== RESULTS BELOW ===\n(empty)\n"


def _write_rules(repo: Path, adh_status: str = "FROZEN 14:30") -> dict[str, str]:
    (repo / "docs/prereg").mkdir(parents=True, exist_ok=True)
    out = {}
    for rid, status in (("ALT_LINE_70", "FROZEN 13:47"), ("ALT_LADDER_HIST", adh_status)):
        p = repo / "docs/prereg" / f"{rid}.md"
        p.write_text(RULE_TEXT.format(n=rid, s=status))
        out[rid] = hc.rule_sha256(p)
    return out


def _lookup(shas: dict[str, str], with_adh: bool = True) -> Any:
    def f(rule_id: str, _repo: Path) -> list[str]:
        if rule_id == "ALT_LADDER_HIST" and not with_adh:
            return []
        return [shas[rule_id]]

    return f


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    r = tmp_path_factory.mktemp("synth")
    build_root(r, n_games=30)
    return r


@pytest.fixture(autouse=True)
def pin_odds(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real pin is the frozen store's fingerprint; the synthetic store gets its own."""
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", ev.odds_fingerprint(root / ev.ODDS_DB))


def test_preflight_passes_only_when_everything_is_frozen_and_pinned(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas = _write_rules(tmp_path)
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])
    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=_lookup(shas))
    assert pf.ok, [c for c in pf.checks if not c.ok]
    pf.require()


def test_harness_refuses_when_alt_ladder_hist_is_not_frozen(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas = _write_rules(tmp_path, adh_status="DRAFT 14:04. Not frozen.")
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])
    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=_lookup(shas))
    assert not pf.ok
    assert any("ALT_LADDER_HIST" in c.name and "FROZEN" in c.name for c in pf.failures())
    with pytest.raises(hc.PreregError, match="ALT_LADDER_HIST"):
        pf.require()


def test_harness_refuses_when_the_adh_freeze_commit_is_missing(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas = _write_rules(tmp_path)  # status says FROZEN but no commit carries its sha
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])
    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=_lookup(shas, with_adh=False))
    assert [c.name for c in pf.failures()] == [
        "ALT_LADDER_HIST: a commit 'prereg(ALT_LADDER_HIST): <sha>' carries the rule text sha",
        "ALT_LADDER_HIST: exactly one distinct prereg(ALT_LADDER_HIST) sha exists in history",
    ]


def test_harness_refuses_a_re_frozen_alt_ladder_hist(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H1: a second prereg(ALT_LADDER_HIST) sha in history (a re-freeze after results) fails, and
    so does any sha other than the exact one handed in with --adh-sha."""
    shas = _write_rules(tmp_path)
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])

    def two(rid: str, _repo: Path) -> list[str]:
        return [shas[rid], "f" * 64] if rid == "ALT_LADDER_HIST" else [shas[rid]]

    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=two)
    assert [c.name for c in pf.failures()] == [
        "ALT_LADDER_HIST: exactly one distinct prereg(ALT_LADDER_HIST) sha exists in history"
    ]
    pf2 = hc.Preflight()
    ev.run_preflight(pf2, root, tmp_path, lookup=_lookup(shas), adh_sha="0" * 64)
    assert any(
        "pinned frozen sha" in c.name and "ALT_LADDER_HIST" in c.name for c in pf2.failures()
    )
    pf3 = hc.Preflight()
    ev.run_preflight(pf3, root, tmp_path, lookup=_lookup(shas), adh_sha=shas["ALT_LADDER_HIST"])
    assert pf3.ok and pf3.info["ALT_LADDER_HIST_sha256"] == shas["ALT_LADDER_HIST"]


def test_odds_store_check_fails_closed_without_a_backup(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H2: no --pre-alt-backup is not a pass; the live store must equal the pinned fingerprint."""
    shas = _write_rules(tmp_path)
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", {"rows": 1, "hash_sum": "0"})
    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=_lookup(shas))
    assert [c.name for c in pf.failures()] == [
        "odds rows the rule reads equal the pinned fingerprint"
    ]
    assert not any("NOT VERIFIED" in c.detail for c in pf.checks)


def test_harness_refuses_a_rule_text_that_is_not_the_frozen_sha(tmp_path: Path, root: Path) -> None:
    shas = _write_rules(tmp_path)  # RULE_SHA stays the real frozen sha: the tmp text differs
    pf = hc.Preflight()
    ev.run_preflight(pf, root, tmp_path, lookup=_lookup(shas))
    assert any("pinned frozen sha" in c.name for c in pf.failures())


def test_harness_refuses_when_an_input_file_changed(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas = _write_rules(tmp_path)
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_LINE_70"])
    # a data root whose gate.json carries a different sha for one input
    alt = tmp_path / "data_root"
    (alt / "reports/prereg_alt_line_70").mkdir(parents=True)
    g = json.loads((root / ev.GATE_JSON).read_text())
    g["input_sha256"]["reports/odds_history/model_rows_2023.parquet"] = "0" * 64
    (alt / ev.GATE_JSON).write_text(json.dumps(g))
    for f in ev.MODEL_INPUTS:
        (alt / f).parent.mkdir(parents=True, exist_ok=True)
        (alt / f).write_bytes((root / f).read_bytes())
    (alt / "data/odds").mkdir(parents=True, exist_ok=True)
    (alt / ev.ODDS_DB).write_bytes((root / ev.ODDS_DB).read_bytes())
    pf = hc.Preflight()
    ev.run_preflight(pf, alt, tmp_path, lookup=_lookup(shas))
    bad = [c.name for c in pf.failures()]
    assert bad == ["input reports/odds_history/model_rows_2023.parquet matches the frozen sha256"]


def test_odds_fingerprint_is_stable_and_detects_a_changed_price(root: Path, tmp_path: Path) -> None:
    import shutil

    import duckdb

    a = ev.odds_fingerprint(root / ev.ODDS_DB)
    copy = tmp_path / "copy.duckdb"
    shutil.copy(root / ev.ODDS_DB, copy)
    assert ev.odds_fingerprint(copy) == a
    con = duckdb.connect(str(copy))
    con.execute("update odds_history set price_american = price_american - 5 where rowid = 3")
    con.close()
    assert ev.odds_fingerprint(copy) != a
    # the BetRivers duplicate-key guard is 0 on the synthetic store
    assert ev.betrivers_duplicate_keys(root / ev.ODDS_DB, 2023, "t5") == 0


def test_check_only_never_requests_the_outcome_column(tmp_path: Path) -> None:
    build_root(tmp_path, n_games=12, with_y=False)  # model rows without any y column at all
    pf = hc.Preflight()
    res = ev.run_scoring(tmp_path, pf, with_y=False, expected=None)
    assert res["counts_only"] is True
    assert pf.ok, pf.failures()
    # the loader asked for no y and the parquet has none
    assert (
        "y"
        not in pl.read_parquet(tmp_path / "reports/odds_history/model_rows_2023.parquet").columns
    )


def test_expected_count_mismatch_is_a_failed_check(tmp_path: Path) -> None:
    build_root(tmp_path, n_games=12, with_y=False)
    pf = hc.Preflight()
    ev.run_scoring(tmp_path, pf, with_y=False, expected=ev.EXPECTED)
    names = [c.name for c in pf.failures()]
    assert any("arm A rungs 2023 equals the rule's gate table" in n for n in names)


def test_seasons_beyond_2024_are_refused_before_any_read(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        ev.load_model(tmp_path, 2025, with_y=False)  # no file is touched: the guard fires first


def test_calibration_word_logic() -> None:
    gate = ev.P1_HEAD
    base = {"games": 500, "line_rows": 9000, "halfwidth_cons": 0.02}
    cal = {**base, "gap": 0.005, "lo": -0.02, "hi": 0.02}
    assert ev.calibration_word(cal, 0.5, gate) == "CALIBRATED"
    wide = {**base, "gap": 0.005, "lo": -0.04, "hi": 0.02}
    assert ev.calibration_word(wide, 0.5, gate) == "INCONCLUSIVE"
    over = {**base, "gap": -0.05, "lo": -0.08, "hi": -0.01}
    assert ev.calibration_word(over, 0.01, gate) == "OVERCONFIDENT"
    assert ev.calibration_word(over, 0.2, gate) == "INCONCLUSIVE"  # BH q too large
    straddle = {**base, "gap": -0.05, "lo": -0.08, "hi": 0.01}
    assert ev.calibration_word(straddle, 0.01, gate) == "INCONCLUSIVE"
    under = {**base, "gap": 0.04, "lo": 0.01, "hi": 0.07}
    assert ev.calibration_word(under, 0.01, gate) == "UNDERCONFIDENT"
    assert ev.calibration_word({**cal, "games": 100}, 0.5, gate) == "NO WORD (gate)"
    assert ev.calibration_word({**cal, "line_rows": 100}, 0.5, gate) == "NO WORD (gate)"
    assert ev.calibration_word({**cal, "halfwidth_cons": 0.05}, 0.5, gate) == "NO WORD (gate)"


def test_stat_and_model_words() -> None:
    assert ev.stat_word("CALIBRATED", "CALIBRATED") == "CALIBRATED"
    assert ev.stat_word("CALIBRATED", "INCONCLUSIVE") == "MIXED"
    ok = dict.fromkeys(ev.STATS, "CALIBRATED")
    assert ev.model_word(ok) == "calibrated at 70%+"
    assert ev.model_word({**ok, "pts": "OVERCONFIDENT"}) == "OVERCONFIDENT at 70%+ for pts"
    assert ev.model_word({**ok, "reb": "MIXED"}).startswith("NOT SHOWN calibrated")


def test_money_word_logic() -> None:
    def res(point: float, lo: float, hi: float, seasons: tuple[float, float]) -> dict[str, Any]:
        return {
            "rungs": 1000,
            "point": point,
            "lo": lo,
            "hi": hi,
            "by_season": {"2023": {"point": seasons[0]}, "2024": {"point": seasons[1]}},
        }

    assert ev.money_word(res(0.05, 0.01, 0.09, (0.04, 0.06)), True) == "PROFITABLE"
    assert (
        ev.money_word(res(0.05, 0.01, 0.09, (0.04, -0.01)), True) == "INCONCLUSIVE"
    )  # a season < 0
    assert ev.money_word(res(0.05, -0.01, 0.11, (0.04, 0.06)), True) == "INCONCLUSIVE"  # lo <= 0
    assert ev.money_word(res(0.015, 0.001, 0.03, (0.02, 0.01)), True) == "INCONCLUSIVE"  # < floor
    assert ev.money_word(res(-0.06, -0.10, 0.01, (-0.05, -0.07)), True) == "NOT PROFITABLE"
    assert ev.money_word(res(0.05, 0.01, 0.09, (0.04, 0.06)), False) == "NO WORD (gate)"
    # BH failure keeps PROFITABLE from being issued (falls to INCONCLUSIVE, hi >= floor)
    assert ev.money_word(res(0.05, 0.01, 0.09, (0.04, 0.06)), True, bh_ok=False) == "INCONCLUSIVE"
    assert ev.money_word({"rungs": 0}, True) == "NO WORD (gate)"


@pytest.fixture(scope="module")
def scored(root: Path) -> dict[str, Any]:
    pf = hc.Preflight()
    return ev.run_scoring(root, pf, b=60, expected=None)


def test_scoring_produces_the_rules_cells_and_words(scored: dict[str, Any]) -> None:
    p1 = scored["part1"]
    assert set(p1["headline"]) == {"2023", "2024"}
    assert set(p1["headline"]["2023"]) == set(ev.STATS)
    assert set(p1["stat_words"]) == set(ev.STATS)
    assert p1["model_word"]
    # six reliability bins plus the P = 1.0 sub-row, five distance bins, 4 x 4 x 2 secondary cells
    assert len(p1["bins"]["2023"]["pts"]) == 7
    assert len(p1["distance"]["2023"]["pts"]) == 5
    assert sum(len(v["2023"]) + len(v["2024"]) for v in p1["secondary"].values()) == 32
    # the synthetic model is calibrated by construction (cells hold a few hundred rows)
    for s in ("2023", "2024"):
        for st in ev.STATS:
            c = p1["headline"][s][st]
            assert c["line_rows"] > 0 and abs(c["gap"]) < 0.25
            assert c["lo"] <= c["hi"]
    # tiny synthetic data cannot pass the real gate: every word is a gate refusal, not a guess
    assert {c["word"] for s in p1["headline"].values() for c in s.values()} == {"NO WORD (gate)"}


def test_betrivers_void_or_unmatched_is_counted(root: Path, scored: dict[str, Any]) -> None:
    for s in (2023, 2024):
        v = scored["meta"][f"betrivers_t5_{s}"]
        assert v["void_or_unmatched"] == 30  # one unmatched player per game, 30 games
        assert v["two_sided_half_point_rungs"] == v["joined_to_model"] + 30
        assert (
            ev.betrivers_two_sided_half_point(root / ev.ODDS_DB, s, "t5")
            == (v["two_sided_half_point_rungs"])
        )


def test_part1_gap_equals_hit_rate_minus_mean_p(root: Path) -> None:
    m = pl.read_parquet(root / "reports/odds_history/model_rows_2023.parquet").filter(
        pl.col("stat") == "reb"
    )
    surv = np.array(m["p_ge"].to_list())
    y = m["y"].to_numpy()
    ks = np.arange(1, surv.shape[1] + 1)[None, :]
    mask = (surv >= 0.70 - 1e-12) & (surv < 0.90)
    hits = (y[:, None] >= ks)[mask].mean()
    pf = hc.Preflight()
    res = ev.run_scoring(root, pf, b=30, expected=None)
    c = res["part1"]["headline"]["2023"]["reb"]
    assert c["line_rows"] == int(mask.sum())
    assert c["hit_rate"] == pytest.approx(hits)
    assert c["mean_p"] == pytest.approx(surv[mask].mean())
    assert c["gap"] == pytest.approx(hits - surv[mask].mean())


def test_part2_arm_a_equals_a_hand_computation(root: Path, scored: dict[str, Any]) -> None:
    from research.eval.alt_line_70_gate import priced_rungs

    tips = ev.load_tips(root)
    total = 0
    profit = 0.0
    for s in ev.SEASONS:
        m = ev.load_model(root, s, True)
        t, _ = priced_rungs(root / ev.ODDS_DB, s, tips, m, "t5")
        t = ev.with_outcome(t, m)
        a = t.filter((pl.col("book") == "betrivers") & (pl.col("pm") >= 0.70 - 1e-12))
        total += a.height
        profit += float(a["profit"].sum())
        hand = np.where(
            a["y"].to_numpy() > a["point"].to_numpy(), a["dec"].to_numpy() - 1, -1
        ).sum()
        assert a["profit"].sum() == pytest.approx(hand)
    e1 = scored["part2"]["arm_A"]["E1"]
    assert e1["rungs"] == total and total > 100
    assert e1["point"] == pytest.approx(profit / total)
    assert e1["lo"] <= e1["hi"]
    assert set(e1["by_season"]) == {"2023", "2024"}


def test_part2_structure_comparators_and_plan(scored: dict[str, Any]) -> None:
    p2 = scored["part2"]
    assert p2["arm_A"]["word"] == "NO WORD (gate)"  # synthetic n is far below 1,500 rungs
    assert set(p2["by_stat"]) == set(ev.STATS)
    assert set(p2["secondary"]) == {"55", "65", "75", "85"}
    assert {k for k in p2["secondary"]["55"]} >= {"E1", "E1u", "gate", "E2_plan", "word", "bh_q"}
    # C2 (all BetRivers rungs) loses about the overround on a calibrated model
    assert p2["C2_all_betrivers_rungs"]["point"] < 0.0
    # Unibet is a 2023-only copy
    assert set(p2["unibet_replicate"]["by_season"]) == {"2023"}
    # the plan never stakes more than $20 a week: staked median <= weeks x $20
    for s, e2 in p2["E2_plan"].items():
        assert e2["total_staked_median"] <= e2["weeks"] * 20.0 + 1e-9, s
    be = p2["break_even_by_p_bin"]
    assert set(be) == {"0.70-0.75", "0.75-0.80", "0.80-0.85", "0.85-0.90", "0.90-0.95", "0.95-1.00"}
    for row in be.values():
        if row["rungs"]:
            assert row["decimal_at_claim"] == pytest.approx(1 / row["mean_claimed_p"])
    assert scored["attacks"].startswith("not run")


def test_a_leaked_price_signal_is_what_the_planted_future_attack_measures(root: Path) -> None:
    # the attack list is only run on a PROFITABLE word; call it directly to prove it executes
    pf = hc.Preflight()
    tips = ev.load_tips(root)
    models = {s: ev.load_model(root, s, True) for s in ev.SEASONS}
    from research.eval.alt_line_70_gate import priced_rungs

    gids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in ev.SEASONS}
    dts = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in ev.SEASONS
    }
    t5 = {}
    for s in ev.SEASONS:
        t, _ = priced_rungs(root / ev.ODDS_DB, s, tips, models[s], "t5")
        t5[s] = ev.with_outcome(ev.add_indices(t, gids[s], dts[s]), models[s])
    bx = ev.Boots(gids, dts, 40)
    arm_a = {
        s: df.filter((pl.col("book") == "betrivers") & (pl.col("pm") >= 0.70 - 1e-12))
        for s, df in t5.items()
    }
    out = ev.attacks(t5, bx, models, arm_a, c2=-0.05, seeds=3)
    assert {"i_planted_future", "ii_shuffle_p", "iii_leave_out", "vi_overround_sensitivity"} <= set(
        out
    )
    assert out["iii_leave_out"]["roi_without_best_5pct"] < float(
        pl.concat(list(arm_a.values()))["profit"].mean()  # type: ignore[arg-type]
    )
    assert pf.ok


def test_main_check_only_exits_nonzero_when_a_check_fails(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # no rules in tmp_path: the dry run still runs, reports the failed checks, and exits 2
    with pytest.raises(SystemExit) as e:
        ev.main(["--data-root", str(root), "--repo-root", str(tmp_path), "--check-only"])
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "ALT_LADDER_HIST: rule file exists" in out


def test_main_full_run_refuses_without_the_freeze(root: Path, tmp_path: Path) -> None:
    with pytest.raises(hc.PreregError):
        ev.main(
            [
                "--data-root",
                str(root),
                "--repo-root",
                str(tmp_path),
                "--out",
                str(tmp_path / "o.json"),
            ]
        )
    assert not (tmp_path / "o.json").exists()


def test_the_real_frozen_rule_hash_matches_the_pin() -> None:
    repo = Path(__file__).resolve().parents[3]
    rule = repo / ev.RULE_PATH
    if rule.exists():
        assert hc.rule_sha256(rule) == ev.RULE_SHA
        assert hashlib.sha256(hc.rule_text_above_mark(rule).encode()).hexdigest() == ev.RULE_SHA
