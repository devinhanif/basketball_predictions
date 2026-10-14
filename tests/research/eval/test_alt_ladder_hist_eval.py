"""Tests for the ALT_LADDER_HIST run harness on a synthetic data root: the refusals (exact rule sha,
one distinct freeze sha, the ALT_LINE_70 result's recorded sha, pinned gate files and gate json, the
odds fingerprint, the timing and gate-count guards), the dry run that never reads ``y``, the words,
and the invariants of a full scoring pass (small B and R)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from research.eval import alt_harness_common as hc
from research.eval import alt_ladder_hist_eval as ev
from research.eval import alt_ladder_hist_gate as ag
from research.eval import alt_line_70_eval as e70
from tests.research.eval.alt_synth import build_alt_root

RULE_TEXT = "# {n}\n\nStatus: **{s}**\n- body\n\n=== RESULTS BELOW ===\n(empty)\n"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    r = tmp_path_factory.mktemp("synth_adh")
    build_alt_root(r, n_games=40)
    (r / "reports/prereg_alt_ladder_hist").mkdir(parents=True)
    gate = ag.run_gate(r, r / "nba.duckdb", hash_inputs=True)
    (r / ev.GATE_JSON).write_text(json.dumps(gate, sort_keys=True, default=str))
    return r


@pytest.fixture()
def repo(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A repo root with a FROZEN rule file, copies of the pinned code, and every pin patched to the
    synthetic values; the ALT_LINE_70 result records the rule sha."""
    (tmp_path / "docs/prereg").mkdir(parents=True)
    p = tmp_path / ev.RULE_PATH
    p.write_text(RULE_TEXT.format(n="ADH", s="FROZEN 15:30"))
    sha = hc.rule_sha256(p)
    monkeypatch.setattr(hc, "ADH_SHA", sha)
    for f in ev.CODE_SHA:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / f, tmp_path / f)
    monkeypatch.setattr(ev, "CODE_SHA", {f: hc.file_sha256(tmp_path / f) for f in ev.CODE_SHA})
    monkeypatch.setattr(ev, "GATE_JSON_SHA", hc.file_sha256(root / ev.GATE_JSON))
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", e70.odds_fingerprint(root / ev.ODDS_DB))
    res70 = root / ev.LINE70_RESULT
    res70.parent.mkdir(parents=True, exist_ok=True)
    res70.write_text(json.dumps({"adh_sha256": sha}))
    return tmp_path, sha


def _pf(root: Path, repo: Path, sha: str, commits: list[str] | None = None) -> hc.Preflight:
    pf = hc.Preflight()
    ev.run_preflight(pf, root, repo, lambda _i, _r: commits if commits is not None else [sha])
    return pf


def test_preflight_passes_only_with_everything_pinned(root: Path, repo: tuple[Path, str]) -> None:
    r, sha = repo
    pf = _pf(root, r, sha)
    assert pf.ok, pf.failures()
    assert pf.info["ALT_LADDER_HIST_sha256"] == sha


def test_refuses_a_rule_that_is_not_frozen_or_not_the_pinned_sha(
    root: Path, repo: tuple[Path, str]
) -> None:
    r, sha = repo
    p = r / ev.RULE_PATH
    p.write_text(RULE_TEXT.format(n="ADH", s="DRAFT"))
    pf = _pf(root, r, sha)
    names = [c.name for c in pf.failures()]
    assert any("pinned frozen sha" in n for n in names)  # the text changed: not the pinned sha
    assert any("FROZEN" in n for n in names)
    with pytest.raises(hc.PreregError):
        pf.require()


def test_refuses_a_second_freeze_sha_in_history(root: Path, repo: tuple[Path, str]) -> None:
    r, sha = repo
    pf = _pf(root, r, sha, commits=[sha, "f" * 64])
    assert [c.name for c in pf.failures()] == [
        "ALT_LADDER_HIST: exactly one distinct prereg(ALT_LADDER_HIST) sha exists in history"
    ]


def test_refuses_when_the_alt_line_70_result_recorded_another_sha_or_none(
    root: Path, repo: tuple[Path, str]
) -> None:
    r, sha = repo
    res70 = root / ev.LINE70_RESULT
    res70.write_text(json.dumps({"adh_sha256": "0" * 64}))
    pf = _pf(root, r, sha)
    assert [c.name for c in pf.failures()] == [
        "the ALT_LINE_70 result recorded this ALT_LADDER_HIST sha"
    ]
    res70.unlink()
    pf2 = _pf(root, r, sha)
    assert [c.name for c in pf2.failures()] == [
        "the ALT_LINE_70 result recorded this ALT_LADDER_HIST sha"
    ]
    res70.write_text(json.dumps({"adh_sha256": sha}))


def test_refuses_a_changed_gate_file_a_changed_gate_json_and_a_changed_odds_store(
    root: Path, repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r, sha = repo
    f = r / "research/eval/alt_ladder_hist_gate.py"
    f.write_text(f.read_text() + "\n# edited after the freeze\n")
    pf = _pf(root, r, sha)
    assert [c.name for c in pf.failures()] == [
        "harness-pinned file research/eval/alt_ladder_hist_gate.py equals the sha256 "
        "recorded at the freeze"
    ]
    monkeypatch.undo()
    pf2 = hc.Preflight()
    ev.run_preflight(pf2, root, r, lambda _i, _r: [sha], adh_sha=sha)
    assert not pf2.ok  # nothing is patched any more: the real pins do not match the synthetic root


def test_odds_fingerprint_fails_closed(
    root: Path, repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r, sha = repo
    monkeypatch.setattr(ev, "ODDS_FINGERPRINT", {"rows": 1, "hash_sum": "0"})
    pf = _pf(root, r, sha)
    assert [c.name for c in pf.failures()] == [
        "odds rows the rule's siblings read equal the pinned fingerprint"
    ]


def test_the_pinned_sha_is_the_frozen_rule_and_the_pinned_files_match() -> None:
    rule = REPO / ev.RULE_PATH
    if rule.exists():
        assert hc.rule_sha256(rule) == hc.ADH_SHA
    for f, want in ev.CODE_SHA.items():
        assert hc.file_sha256(REPO / f) == want


def test_check_only_reads_no_outcome_and_the_guards_pass(tmp_path: Path) -> None:
    build_alt_root(tmp_path, n_games=30, with_y=False)  # model rows without any y column
    gate = ag.run_gate(tmp_path, tmp_path / "nba.duckdb", hash_inputs=False)
    pf = hc.Preflight()
    res = ev.run_scoring(tmp_path, pf, gate, with_y=False)
    assert res["counts_only"] is True
    assert pf.ok, pf.failures()
    assert any("equal the gate table" in c.name for c in pf.checks)


def test_gate_count_mismatch_and_late_quotes_are_failed_checks(tmp_path: Path) -> None:
    build_alt_root(tmp_path, n_games=30, with_y=False)
    gate = ag.run_gate(tmp_path, tmp_path / "nba.duckdb", hash_inputs=False)
    gate["per_season"]["2023"]["meta"]["games_with_report_stamp_in_window"] += 1
    pf = hc.Preflight()
    ev.run_scoring(tmp_path, pf, gate, with_y=False)
    assert [c.name for c in pf.failures()] == [
        "2023: games_with_report_stamp_in_window equals the gate table"
    ]
    # a quote refreshed after the cutoff stops the run
    con = duckdb.connect(str(tmp_path / ev.ODDS_DB))
    con.execute(
        "update odds_history set last_update = snapshot_ts + interval 30 minute where rowid in "
        "(select rowid from odds_history where last_update is not null limit 5)"
    )
    con.close()
    pf2 = hc.Preflight()
    ev.run_scoring(tmp_path, pf2, None, with_y=False)
    assert any("last_update <= tip - 60 min" in c.name for c in pf2.failures())


def test_secondary_word_logic() -> None:
    base = {
        "games": 500,
        "line_rows": 9000,
        "halfwidth_cons": 0.02,
        "by_season_gap": {"2023": -0.05, "2024": -0.04},
    }
    over = {**base, "gap": -0.045, "lo": -0.08, "hi": -0.01}
    assert ev.secondary_word(over, 0.01) == "OVERCONFIDENT"
    assert ev.secondary_word(over, 0.2) == "INCONCLUSIVE"
    split = {**over, "by_season_gap": {"2023": -0.05, "2024": 0.01}}
    assert ev.secondary_word(split, 0.01) == "INCONCLUSIVE"  # seasons disagree in sign
    cal = {
        **base,
        "gap": 0.0,
        "lo": -0.02,
        "hi": 0.02,
        "by_season_gap": {"2023": -0.03, "2024": 0.03},
    }
    assert ev.secondary_word(cal, 0.5) == "CALIBRATED"  # the disclosed asymmetry (rule N5)
    assert ev.secondary_word({**cal, "games": 10}, 0.5) == "NO WORD (gate)"
    under = {
        **base,
        "gap": 0.05,
        "lo": 0.02,
        "hi": 0.08,
        "by_season_gap": {"2023": 0.04, "2024": 0.06},
    }
    assert ev.secondary_word(under, 0.01) == "UNDERCONFIDENT"


@pytest.fixture(scope="module")
def scored(root: Path) -> dict[str, Any]:
    gate = json.loads((root / ev.GATE_JSON).read_text())
    pf = hc.Preflight()
    res = ev.run_scoring(root, pf, gate, b=50, run_attacks=False)
    assert pf.ok
    return res


def test_scoring_structure(scored: dict[str, Any]) -> None:
    dk = scored["draftkings"]
    p1 = dk["part1"]
    assert set(p1["headline"]) == {"2023", "2024"}
    assert set(p1["stat_words"]) == set(ev.STATS)
    assert p1["model_word"].startswith("NOT SHOWN calibrated")  # every cell is a gate refusal
    assert len(p1["bins"]["2023"]["pts"]) == 7
    assert len(p1["distance"]["2023"]["pts"]) == 5
    assert len(p1["tail"]["2023"]["pts"]) == 5
    assert sum(len(v) for v in p1["secondary"].values()) == 16
    assert set(dk["part2"]["secondary"]) == {"55", "65", "75", "85"}
    assert set(dk["sensitivity"]) == {
        "S1_window_games_included",
        "S2_no_lead_cap",
        "S3_excluded_added_back",
        "window_games_only",
    }
    assert {"fanduel", "fanatics"} == set(scored["replicates"])
    assert scored["attacks"].startswith("not run")
    # tiny synthetic data cannot pass the real gate: every word is a gate refusal
    assert {c["word"] for s in p1["headline"].values() for c in s.values()} == {"NO WORD (gate)"}
    assert dk["part2"]["arm_A"]["word"] == "NO WORD (gate)"


def test_the_window_games_are_out_of_the_primary_arm_and_s1_adds_them_back(root: Path) -> None:
    tips = pl.concat(
        [pl.read_parquet(root / f"data/ops/model_clv_pairs/pairs_{s}.parquet") for s in ev.SEASONS]
    ).unique("game_id")
    stamps = ag.load_report_stamps(root / "nba.duckdb")
    t, _model, meta = ev.build_season(root, 2023, tips, stamps, True)
    arms = ev.arms_of(t)
    assert meta["games_with_report_stamp_in_window"] == 4  # every tenth of 40 games
    prim_games = set(arms["primary"]["game_id"].to_list())
    win_games = set(t.filter(pl.col("in_window"))["game_id"].to_list())
    assert len(win_games) == 4 and not (prim_games & win_games)
    assert win_games <= set(arms["S1_window_games_included"]["game_id"].to_list())
    assert (
        arms["window_games_only"].height + arms["primary"].height
        == arms["S1_window_games_included"].height
    )


def test_arm_a_roi_equals_a_hand_computation(root: Path, scored: dict[str, Any]) -> None:
    tips = pl.concat(
        [pl.read_parquet(root / f"data/ops/model_clv_pairs/pairs_{s}.parquet") for s in ev.SEASONS]
    ).unique("game_id")
    stamps = ag.load_report_stamps(root / "nba.duckdb")
    tot, n = 0.0, 0
    for s in ev.SEASONS:
        t, _m, _meta = ev.build_season(root, s, tips, stamps, True)
        a = ev.arms_of(t.filter(pl.col("book") == "draftkings"))["primary"]
        a = a.filter(pl.col("pm") >= 0.70 - 1e-12)
        hand = np.where(
            a["y"].to_numpy() >= a["point"].to_numpy() + 0.5, a["dec"].to_numpy() - 1.0, -1.0
        )
        tot += float(hand.sum())
        n += a.height
    e1 = scored["draftkings"]["part2"]["arm_A"]["E1"]
    assert e1["rungs"] == n and n > 200
    assert e1["point"] == pytest.approx(tot / n)


def test_part1_gap_equals_hit_rate_minus_mean_p(root: Path, scored: dict[str, Any]) -> None:
    c = scored["draftkings"]["part1"]["headline"]["2024"]["reb"]
    assert c["gap"] == pytest.approx(c["hit_rate"] - c["mean_p"])
    assert c["lo"] <= c["hi"] and c["line_rows"] > 50


def test_plan_stakes_never_exceed_the_weekly_budget(scored: dict[str, Any]) -> None:
    for e2 in scored["draftkings"]["part2"]["E2_plan"].values():
        assert e2["total_staked_median"] <= e2["weeks"] * 20.0 + 1e-9


def test_attacks_run_and_report(root: Path) -> None:
    tips = pl.concat(
        [pl.read_parquet(root / f"data/ops/model_clv_pairs/pairs_{s}.parquet") for s in ev.SEASONS]
    ).unique("game_id")
    stamps = ag.load_report_stamps(root / "nba.duckdb")
    t_by, models = {}, {}
    for s in ev.SEASONS:
        t_by[s], models[s], _ = ev.build_season(root, s, tips, stamps, True)
    gids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in ev.SEASONS}
    dts = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in ev.SEASONS
    }
    bx = e70.Boots(gids, dts, 30)
    dk = ev.book_arms(t_by, "draftkings")
    fd = ev.book_arms(t_by, "fanduel")
    out = ev.attacks(
        dk["primary"],
        bx,
        models,
        -0.05,
        dk["S1_window_games_included"],
        dk["window_games_only"],
        dk["S3_excluded_added_back"],
        fd["primary"],
        {"point": -0.01},
        seeds=3,
    )
    assert {
        "i_planted_future",
        "ii_shuffle_p",
        "iii_leave_out",
        "iv_replicates",
        "vii_window_games",
        "x_S3",
    } <= set(out)


def test_main_check_only_refuses_without_the_freeze(root: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        ev.main(["--data-root", str(root), "--repo-root", str(tmp_path), "--check-only"])
    assert e.value.code == 2
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
