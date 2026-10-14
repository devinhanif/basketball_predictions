"""Tests for the ALT_PARLAY_X run harness on a synthetic data root: the refusals (rule,
ALT_LADDER_HIST freeze, pinned construction code, numpy pin, gate.json, rebuilt counts and
fingerprints), the identity of the bootstrap with the frozen estimators, the words, and the
scoring invariants."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import alt_harness_common as hc
from research.eval import alt_line_70_eval as e70
from research.eval import alt_parlay_x_eval as ev
from research.eval import alt_parlay_x_gate as x
from tests.research.eval.alt_synth import build_root

RULE_TEXT = "# {n}\n\nStatus: **{s}**\n- body\n\n=== RESULTS BELOW ===\n(empty)\n"


def _rules(repo: Path, adh: str = "FROZEN 14:30") -> dict[str, str]:
    (repo / "docs/prereg").mkdir(parents=True, exist_ok=True)
    out = {}
    for rid, status in (("ALT_PARLAY_X", "FROZEN 13:47"), ("ALT_LADDER_HIST", adh)):
        p = repo / "docs/prereg" / f"{rid}.md"
        p.write_text(RULE_TEXT.format(n=rid, s=status))
        out[rid] = hc.rule_sha256(p)
    return out


def _lookup(shas: dict[str, str]) -> Any:
    return lambda rid, _repo: [shas[rid]]


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    r = tmp_path_factory.mktemp("synthx")
    build_root(r, n_games=60)
    (r / "reports/prereg_alt_parlay_x").mkdir(parents=True)
    res = x.run_gate(r, sims=3)
    (r / ev.GATE_JSON).write_text(json.dumps(res))
    return r


@pytest.fixture(autouse=True)
def pin_odds(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", e70.odds_fingerprint(root / ev.ODDS_DB))


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    """A repo root holding the rule files and copies of the construction code."""
    shas = _rules(tmp_path)
    for f in ev.CODE_SHA:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(__file__).resolve().parents[3] / f, tmp_path / f)
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_PARLAY_X"])
    return tmp_path, shas


def _preflight(root: Path, repo: Path, shas: dict[str, str]) -> hc.Preflight:
    pf = hc.Preflight()
    ev.run_preflight(pf, root, repo, lookup=_lookup(shas))
    return pf


def test_preflight_passes_with_every_pin_in_place(
    root: Path, repo: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    pf = _preflight(root, *repo)
    assert pf.ok, pf.failures()


def test_harness_refuses_a_gate_json_that_is_not_the_one_the_rule_quotes(
    root: Path, repo: tuple[Path, dict[str, str]]
) -> None:
    pf = _preflight(root, *repo)  # GATE_JSON_SHA is the real pin; the synthetic gate differs
    assert [c.name for c in pf.failures()] == ["gate.json equals the sha256 quoted in the rule"]


def test_odds_store_check_fails_closed_and_a_re_freeze_is_refused(
    root: Path, repo: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    r, shas = repo
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", {"rows": 1, "hash_sum": "0"})
    pf = _preflight(root, r, shas)
    assert [c.name for c in pf.failures()] == [
        "odds rows the rule reads equal the pinned fingerprint"
    ]
    monkeypatch.undo()

    def two(rid: str, _repo: Path) -> list[str]:
        return [shas[rid], "f" * 64] if rid == "ALT_LADDER_HIST" else [shas[rid]]

    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_PARLAY_X"])
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", e70.odds_fingerprint(root / ev.ODDS_DB))
    pf2 = hc.Preflight()
    ev.run_preflight(pf2, root, r, lookup=two)
    assert [c.name for c in pf2.failures()] == [
        "ALT_LADDER_HIST: exactly one distinct prereg(ALT_LADDER_HIST) sha exists in history"
    ]
    pf3 = hc.Preflight()
    ev.run_preflight(pf3, root, r, lookup=_lookup(shas), adh_sha="0" * 64)
    assert any("ALT_LADDER_HIST" in c.name and "pinned" in c.name for c in pf3.failures())


def test_harness_refuses_when_alt_ladder_hist_is_not_frozen(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas = _rules(tmp_path, adh="DRAFT 14:04")
    monkeypatch.setattr(ev, "RULE_SHA", shas["ALT_PARLAY_X"])
    pf = _preflight(root, tmp_path, shas)
    assert any("ALT_LADDER_HIST" in c.name and "FROZEN" in c.name for c in pf.failures())
    with pytest.raises(hc.PreregError):
        pf.require()


def test_harness_refuses_a_changed_construction_file(
    root: Path, repo: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    r, shas = repo
    f = r / "research/eval/alt_parlay_x_gate.py"
    f.write_text(f.read_text() + "\n# edited after the freeze\n")
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    pf = _preflight(root, r, shas)
    assert [c.name for c in pf.failures()] == [
        "construction file research/eval/alt_parlay_x_gate.py equals the rule's pinned sha256"
    ]


def test_harness_refuses_a_different_numpy(
    root: Path, repo: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    monkeypatch.setattr(ev, "NUMPY_PIN", "0.0.0")
    pf = _preflight(root, *repo)
    assert [c.name for c in pf.failures()] == ["numpy version equals the rule's pin"]


def test_the_pinned_code_hashes_match_the_files_in_this_checkout() -> None:
    repo = Path(__file__).resolve().parents[3]
    for f, want in ev.CODE_SHA.items():
        assert hc.file_sha256(repo / f) == want


def _fake_cell(rng: np.random.Generator, n_legs: int, n_par: int, m: int, g: int) -> ev.SeasonCell:
    p = rng.uniform(0.5, 0.85, n_legs)
    hit = (rng.random(n_legs) < p).astype(float)
    idx = np.stack([rng.choice(n_legs, size=m, replace=False) for _ in range(n_par)])
    legs = pl.DataFrame({"x": np.arange(n_legs)})
    par = pl.DataFrame({"x": np.arange(n_par)})
    dl = rng.integers(0, g, n_legs)
    dp = rng.integers(0, g, n_par)
    return ev.SeasonCell(legs, par, idx, p, hit, "fp", dp, dl, dp // 3, dl // 3)


def test_bootstrap_with_an_all_ones_resample_equals_the_frozen_estimators() -> None:
    rng = np.random.default_rng(5)
    for m in (2, 3):
        c = _fake_cell(rng, 60, 40, m, 12)
        est = x.estimators([(c.p, c.hit, c.idx)])
        ones = np.ones((2, 12))
        jb = ev.joint_bootstrap([(c, ones, "date")])
        assert jb["gap"][0] == pytest.approx(est["gap"])
        assert jb["excess"][0] == pytest.approx(est["excess"])
        assert jb["rho"][0] == pytest.approx(est["rho"])
    # two seasons pool with parlay weights exactly as estimators() does
    c1, c2 = _fake_cell(rng, 50, 30, 2, 10), _fake_cell(rng, 70, 45, 2, 14)
    est = x.estimators([(c1.p, c1.hit, c1.idx), (c2.p, c2.hit, c2.idx)])
    jb = ev.joint_bootstrap([(c1, np.ones((1, 10)), "date"), (c2, np.ones((1, 14)), "date")])
    assert jb["gap"][0] == pytest.approx(est["gap"])
    assert jb["excess"][0] == pytest.approx(est["excess"])
    assert jb["rho"][0] == pytest.approx(est["rho"])


def test_bootstrap_resamples_vary_and_are_seeded() -> None:
    rng = np.random.default_rng(6)
    c = _fake_cell(rng, 80, 60, 2, 15)
    boots = hc.make_boot({2023: 15}, 120, 2026)
    a = ev.joint_bootstrap([(c, boots[2023], "date")], chunk=50)
    b = ev.joint_bootstrap([(c, boots[2023], "date")], chunk=17)
    assert np.allclose(a["gap"], b["gap"], equal_nan=True)  # chunking never changes a number
    assert np.nanstd(a["gap"]) > 0


def test_words() -> None:
    cell = {"point": -0.08, "lo": -0.15, "hi": -0.02}
    assert ev.t1_word(cell, 0.01, False, True, True) == "OVERCONFIDENT"
    assert ev.t1_word(cell, 0.2, False, True, True) == "INCONCLUSIVE"
    assert ev.t1_word(cell, 0.01, False, True, False) == "INCONCLUSIVE"  # seasons disagree
    assert ev.t1_word(cell, 0.01, False, False, True) == "NO WORD (gate)"
    ok = {"point": 0.005, "lo": -0.02, "hi": 0.02}
    assert ev.t1_word(ok, 0.5, True, True, True) == "CALIBRATED"
    assert ev.t1_word(ok, 0.5, False, True, True) == "INCONCLUSIVE"  # cell cannot issue CALIBRATED
    assert ev.t2_word(ok, 0.5, True, True, True) == "INDEPENDENCE SUPPORTED"
    assert ev.t2_word(ok, 0.5, False, True, True) == "INCONCLUSIVE"
    dep = {"point": 0.03, "lo": 0.01, "hi": 0.05}
    assert ev.t2_word(dep, 0.01, False, True, True) == "DEPENDENT"
    res = {
        "point": 0.05,
        "lo": 0.01,
        "hi": 0.09,
        "by_season": {"2023": {"point": 0.02}, "2024": {"point": 0.1}},
    }
    assert ev.money_word(res, True, 0.01) == "PROFITABLE"
    assert ev.money_word(res, True, 0.2) == "INCONCLUSIVE"
    assert ev.money_word(res, False, 0.01) == "NO WORD (gate)"
    neg = {"point": -0.2, "lo": -0.3, "hi": -0.1, "by_season": {"2023": {"point": -0.2}}}
    assert ev.money_word(neg, True, 0.9) == "NOT PROFITABLE"
    assert {("55", 2), ("55", 3), ("65", 2), ("65", 3)} == ev.CAN_CALIBRATED
    assert len(ev.CAN_INDEP) == 6 and ("ge70", 3) not in ev.CAN_INDEP
    assert sum(ev.EXPECTED_GATE_PASS.values()) == 8 and not ev.EXPECTED_GATE_PASS[("85", 2)]


def test_check_only_rebuilds_the_construction_without_y(tmp_path: Path) -> None:
    build_root(tmp_path, n_games=60, with_y=False)
    (tmp_path / "reports/prereg_alt_parlay_x").mkdir(parents=True)
    (tmp_path / ev.GATE_JSON).write_text(json.dumps(x.run_gate(tmp_path, sims=2)))
    pf = hc.Preflight()
    res = ev.run_scoring(tmp_path, pf, with_y=False)
    assert res["counts_only"] is True
    assert pf.ok, pf.failures()
    assert any(v for v in res["fingerprints"].values())


def test_a_changed_construction_fails_the_count_and_fingerprint_checks(
    root: Path, tmp_path: Path
) -> None:
    gate = json.loads((root / ev.GATE_JSON).read_text())
    gate["seasons"]["2023"]["ge70"]["size2"]["fingerprint_sha256"] = "0" * 64
    gate["pooled"]["55_size3"]["parlays"] += 1
    bad = tmp_path / "gate.json"
    bad.write_text(json.dumps(gate))
    pf = hc.Preflight()
    ev.run_scoring(root, pf, with_y=False, gate_json=bad)
    names = [c.name for c in pf.failures()]
    assert "fingerprint 2023 ge70 size 2 equals gate.json" in names
    assert "pooled parlay count 55 size 3 equals the rule's table" in names


@pytest.fixture(scope="module")
def scored(root: Path) -> dict[str, Any]:
    pf = hc.Preflight()
    return ev.run_scoring(root, pf, b=40, expected_gate=None, run_attacks=False)


def test_scoring_covers_the_ten_cells_and_their_families(scored: dict[str, Any]) -> None:
    cells = scored["cells"]
    assert len(cells) == 10
    live = {k: v for k, v in cells.items() if v["parlays"] > 0}
    assert live
    for name, v in live.items():
        assert {"T1", "T2", "money", "break_even", "gate", "E2_plan"} <= set(v), name
        for fam in (v["T1"], v["T2"], v["money"]):
            assert 0.0 <= fam["bh_q"] <= 1.0 and fam["lo"] <= fam["hi"]
        # synthetic data are tiny: every money word is a gate refusal, never a guess
        assert v["money"]["word"] == "NO WORD (gate)"


def test_scoring_roi_equals_a_hand_computation(root: Path, scored: dict[str, Any]) -> None:
    tips = e70.load_tips(root)
    total = 0.0
    n = 0
    dates, wk = {}, {}
    for s in ev.SEASONS:
        m = e70.load_model(root, s, True)
        dates[s] = np.sort(m["game_date"].unique().to_numpy().astype("datetime64[D]"))
        wk[s] = np.unique(dates[s] - ((dates[s].astype(int) + 3) % 7).astype("timedelta64[D]"))
        gids = np.sort(m["game_id"].unique().to_numpy())
        t, _ = ev.priced_rungs(root / ev.ODDS_DB, s, tips, m, "t5")
        t = ev.with_outcome(ev.add_indices(t, gids, dates[s]), m)
        pi = 0
        c = ev.build_season_cell(t, s, pi, 0.70, 1.0 + 1e-9, 2, dates[s], wk[s], True)
        if c:
            # hand profit: product of leg decimals - 1 if all legs hit, else -1
            legs_dec = c.legs["dec"].to_numpy()
            y = c.legs["y"].to_numpy()
            pt = c.legs["point"].to_numpy()
            for row in c.idx:
                won = bool(np.all(y[row] > pt[row]))
                total += (np.prod(legs_dec[row]) - 1.0) if won else -1.0
                n += 1
    cell = scored["cells"]["ge70_size2"]
    assert cell["parlays"] == n and n > 20
    assert cell["money"]["point"] == pytest.approx(total / n)
    assert cell["break_even"]["realised_roi"] == pytest.approx(total / n)


def test_parlays_are_cross_game_and_the_plan_respects_the_weekly_budget(
    scored: dict[str, Any],
) -> None:
    for v in scored["cells"].values():
        if v["parlays"] == 0:
            continue
        for e2 in v["E2_plan"].values():
            assert e2["total_staked_median"] <= e2["weeks"] * 20.0 + 1e-9
        be = v["break_even"]
        assert be["decimal_at_claim"] == pytest.approx(1.0 / be["mean_joint_p"])
        assert be["compounded_overround"] > 1.0


def test_attacks_run_on_a_named_cell(root: Path) -> None:
    tips = e70.load_tips(root)
    models = {s: e70.load_model(root, s, True) for s in ev.SEASONS}
    dates = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in ev.SEASONS
    }
    weeks = {
        s: np.unique(dates[s] - ((dates[s].astype(int) + 3) % 7).astype("timedelta64[D]"))
        for s in ev.SEASONS
    }
    gids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in ev.SEASONS}
    t5, t60 = {}, {}
    for s in ev.SEASONS:
        for kind, store in (("t5", t5), ("t60", t60)):
            t, _ = ev.priced_rungs(root / ev.ODDS_DB, s, tips, models[s], kind)
            store[s] = ev.with_outcome(ev.add_indices(t, gids[s], dates[s]), models[s])
    out = ev.attacks(["ge70_size2"], t5, t60, models, dates, weeks, True, seeds=3)
    entry = out["ge70_size2"]
    assert {
        "base",
        "i_planted_future",
        "ii_shuffle_p_roi_p05_p50_p95",
        "iii_leave_out",
        "viii_pulled_lines",
    } <= set(entry)
    assert entry["iii_leave_out"]["roi_without_best_5pct"] <= entry["base"]["roi"] + 1e-9


def test_main_check_only_and_full_run_refuse_without_the_freeze(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as e:
        ev.main(["--data-root", str(root), "--repo-root", str(tmp_path), "--check-only"])
    assert e.value.code == 2
    assert "ALT_PARLAY_X: rule file exists" in capsys.readouterr().out
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
    rule = Path(__file__).resolve().parents[3] / ev.RULE_PATH
    if rule.exists():
        assert hc.rule_sha256(rule) == ev.RULE_SHA
