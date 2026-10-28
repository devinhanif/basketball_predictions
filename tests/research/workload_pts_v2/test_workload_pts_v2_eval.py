# mypy: disable-error-code="no-untyped-call,attr-defined,unused-ignore"
# ruff: noqa: E501
"""WORKLOAD_PTS_V2 harness: freeze refusal (sha8 subject + the commit's own copy of the doc), the frame-hash recipe and its refusal, the
past-only placebo NaN assertion, the recording walk is bit-identical, the shared-permutation shuffle and the K-seed guard, the decision rule
failing each way, the drift rule, the tip cap, and the synthetic dryrun end to end."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import workload_pts_v2_eval as E
from research.recent_role import placebo as PB

CFG = ContextResidualConfig(n_estimators=8, min_cal_rows=300)


# --------------------------------------------------------------------------- freeze
def _doc(tmp_path: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp_path / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nresults\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def _git_with(subject: str, blob: str) -> Any:
    def git(*a: str) -> str:
        if a[0] == "log" and a[1].startswith("--format=%H%x09"):
            return f"abc123\t{subject}\n"
        if a[0] == "show":
            return blob
        if a[0] == "log":
            return "1700000000\n"
        return ""

    return git


def test_require_frozen_accepts_the_sha8_subject_only_when_the_committed_doc_hashes_to_the_sha(
    tmp_path: Path,
) -> None:
    p, sha = _doc(tmp_path)
    blob = p.read_text()
    assert (
        E.require_frozen(
            sha, p, _git_with(f"prereg(WORKLOAD_PTS_V2): {sha[:8]} FROZEN after review", blob)
        )
        == sha
    )
    assert E.require_frozen(sha, p, _git_with(f"prereg(WORKLOAD_PTS_V2): {sha}", blob)) == sha
    with pytest.raises(RuntimeError, match="no commit"):  # another id
        E.require_frozen(sha, p, _git_with(f"prereg(LINEUPS_KNOWN_REBASE): {sha[:8]}", blob))
    with pytest.raises(RuntimeError, match="no commit"):  # the committed copy is a different rule
        E.require_frozen(
            sha,
            p,
            _git_with(f"prereg(WORKLOAD_PTS_V2): {sha[:8]}", "other rule\n=== RESULTS BELOW ===\n"),
        )
    with pytest.raises(RuntimeError, match="no commit"):  # a different sha prefix
        E.require_frozen(sha, p, _git_with("prereg(WORKLOAD_PTS_V2): deadbeef", blob))
    with pytest.raises(RuntimeError, match="required"):
        E.require_frozen(None, p, _git_with("x", blob))
    p.write_text(p.read_text().replace("rule", "rule edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen(sha, p, _git_with(f"prereg(WORKLOAD_PTS_V2): {sha[:8]}", blob))


def test_the_real_rule_hashes_to_the_frozen_20d480f7() -> None:
    assert E.frozen_sha(E.DOC).startswith("20d480f7")


# --------------------------------------------------------------------------- frame identity
def _reference_sha(df: pl.DataFrame, cols: list[str]) -> str:
    """Verbatim recipe of scratchpad gates/framehash.py (the gate that produced 47b569fb)."""
    d = df.sort(["game_id", "player_id"])
    h = hashlib.sha256()
    for k in ("game_id", "player_id"):
        h.update(k.encode() + b"\0" + "\n".join(map(str, d[k].to_list())).encode())
    for c in sorted(set(cols) - {"game_id", "player_id"}):
        a = d[c].cast(pl.Float64, strict=False).to_numpy().astype("<f8")
        a = np.where(np.isnan(a), np.float64("nan"), a)
        h.update(c.encode() + b"\0" + a.tobytes())
    return h.hexdigest()


def test_frame_sha_equals_the_gate_recipe_is_order_free_and_moves_on_one_value() -> None:
    df = pl.DataFrame(
        {"game_id": ["b", "a", "a"], "player_id": [1, 2, 1], "x": [1.0, None, 3.0], "k": [1, 2, 3]}
    )
    ref = _reference_sha(df, ["x", "k"])
    assert E.frame_sha(df, ["x", "k"]) == ref
    assert E.frame_sha(df.reverse(), ["k", "x"]) == ref
    assert (
        E.frame_sha(
            df.with_columns(
                pl.when(pl.col("k") == 3).then(3.0000001).otherwise(pl.col("x")).alias("x")
            ),
            ["x", "k"],
        )
        != ref
    )
    assert E.FRAME_PIN.startswith("47b569fb") and len(E.hash_cols()) == 71


def test_arms_refuse_a_frame_that_differs_from_the_gate_hash(tmp_path: Path) -> None:
    fr = E.synthetic_frame(days=20, per_day=20)
    comps = {"row_gid": fr["game_id"].to_numpy().astype(str), "row_pid": fr["player_id"].to_numpy()}
    sha, _ = E.a0_frame_sha(fr, comps["row_gid"], comps["row_pid"])
    gate = {
        "frame_sha256": sha,
        "frame_all_sha256": E.whole_frame_sha(fr),
        "harness_git_hash": E.harness_hash(),
    }
    ctx = E.Ctx(
        out_dir=tmp_path, cache=tmp_path / "c", frame_fn=lambda: (fr, CFG, {}), comps=comps, pin=sha
    )
    assert E.check_frame(ctx, gate).height == fr.height
    with pytest.raises(RuntimeError, match="refuse"):
        E.check_frame(ctx, {**gate, "frame_sha256": "0" * 64})
    with pytest.raises(RuntimeError, match="harness file changed"):
        E.check_frame(ctx, {**gate, "harness_git_hash": "0" * 40})
    ctx2 = E.Ctx(
        out_dir=tmp_path,
        cache=tmp_path / "c",
        frame_fn=lambda: (fr.with_columns(pl.col("pts") + 1), CFG, {}),
        comps=comps,
        pin=sha,
    )
    with pytest.raises(RuntimeError, match="refuse"):
        E.check_frame(ctx2, gate)
    # a change confined to rows outside the A0 keys passes the pin but not the whole-frame hash (M1)
    keep = fr.head(100)
    sub = {
        "row_gid": keep["game_id"].to_numpy().astype(str),
        "row_pid": keep["player_id"].to_numpy(),
    }
    sha_sub, _ = E.a0_frame_sha(fr, sub["row_gid"], sub["row_pid"])
    g3 = {
        "frame_sha256": sha_sub,
        "frame_all_sha256": E.whole_frame_sha(fr),
        "harness_git_hash": E.harness_hash(),
    }
    other = (
        fr.with_row_index("_i")
        .with_columns(
            pl.when(pl.col("_i") >= 100)
            .then(pl.col("wl_g_7d") + 1)
            .otherwise(pl.col("wl_g_7d"))
            .alias("wl_g_7d")
        )
        .drop("_i")
    )
    ctx3 = E.Ctx(
        out_dir=tmp_path,
        cache=tmp_path / "c",
        frame_fn=lambda: (other, CFG, {}),
        comps=sub,
        pin=sha_sub,
    )
    with pytest.raises(RuntimeError, match="whole-frame"):
        E.check_frame(ctx3, g3)


def test_harness_hash_is_git_hash_object() -> None:
    import subprocess

    got = subprocess.run(
        ["git", "hash-object", str(Path(E.__file__))], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert E.harness_hash() == got


# --------------------------------------------------------------------------- placebo, walks, shuffle
def test_placebo_nan_iff_no_earlier_row_and_the_check_catches_a_planted_violation() -> None:
    fr = PB.permute_past_only(E.synthetic_frame(days=20, per_day=20), E.MODEL27, 0, E.PL)
    gid, pid = fr["game_id"].to_numpy().astype(str), fr["player_id"].to_numpy()
    ok = E.placebo_nan_check(fr, gid, pid)
    assert ok["nan_iff_first_day_whole_frame"]
    bad = fr.with_columns(
        pl.lit(1.0).alias(E.PL + E.X22[0])
    )  # a placebo value on a first row = a non-past source
    assert not E.placebo_nan_check(bad, gid, pid)["nan_iff_first_day_whole_frame"]


def test_columns_and_families_partition_the_22() -> None:
    assert len(E.X22) == 22 and not set(E.X22) & set(E.R5)
    assert sorted((*E.LOADX, *E.SCHEDULE, *E.TRAVEL)) == sorted(E.X22) and len(E.LOADX) == 10
    assert E.HEADS["WF"] == (*E.R5, *E.X22) and E.HEADS["B"] == E.MODEL27
    assert all(c.startswith(E.PL) for c in E.HEADS["PL27"]) and E.HEADS["PL"][:5] == E.R5
    assert set(E.CLAIM_ARMS).isdisjoint(E.GATE_ARMS)


def test_recording_walk_is_bit_identical_and_sees_the_columns() -> None:
    fr = PB.permute_past_only(E.synthetic_frame(days=40, per_day=50), E.MODEL27, 0, E.PL)
    a = E.walk(fr, CFG, "WF", log=False)
    b = E.walk(fr, CFG, "WF", log=False, record=True)
    for k in ("row_c", "row_s", "cal_z", "cal_c", "cal_s"):
        assert np.array_equal(a[k], b[k]), k
    assert len(b["imp_names"]) == 42 + 27 and 0.0 < E.importance_share(b) < 1.0


def test_shuffle_shares_one_permutation_across_arms_and_the_guard_math() -> None:
    fr = PB.permute_past_only(E.synthetic_frame(days=40, per_day=50), E.MODEL27, 0, E.PL)
    fc = E.walk(fr, CFG, "FULL", shuffle=True, seed=3, log=False)
    wf = E.walk(fr, CFG, "WF", shuffle=True, seed=3, log=False)
    real = E.walk(fr, CFG, "FULL", log=False)
    assert list(fc["label_hashes"]) == list(wf["label_hashes"]) != list(real["label_hashes"])
    assert list(E.walk(fr, CFG, "FULL", shuffle=True, seed=4, log=False)["label_hashes"]) != list(
        fc["label_hashes"]
    )
    rng = np.random.default_rng(0)
    assert not E.guard_fires(rng.normal(-0.0003, 0.002, 50))  # honest null (the diagnostic's mean)
    assert E.guard_fires(rng.normal(-0.003, 0.002, 50))  # a label-free gain of 0.003
    d = np.full(50, -0.0005)
    d[0] = -0.0006
    assert not E.guard_fires(d)


def test_ratio_ci_is_paired_and_recovers_a_known_share() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.arange(400).astype(str), 20)
    den = rng.normal(-0.01, 0.05, len(gid))
    num = 0.25 * den + rng.normal(0, 0.001, len(gid))
    r = E.ratio_ci(num, den, gid)
    assert abs(r["share"] - 0.75) < 0.02 and r["lo"] < 0.75 < r["hi"]


# --------------------------------------------------------------------------- decision rule
def _res(
    d23: float = -0.008,
    d24: float = -0.008,
    hi: float = -0.002,
    p: float = 0.01,
    pl: float = -0.008,
    cov_wf: float = 0.10,
    slice_d: float = 0.0,
    ko: float = -0.006,
) -> dict[str, Any]:
    rows = []
    for s, d in ((2023, d23), (2024, d24)):
        rows.append(
            {
                "season": s,
                "arm": "WF_minus_FULL",
                "delta": d,
                "ci_lo": d - 0.003,
                "ci_hi": hi,
                "p": p,
                "mde": 0.002,
                "n_rows": 27000,
                "n_games": 1300,
            }
        )
        rows.append(
            {
                "season": s,
                "arm": "WF_minus_PL",
                "delta": pl,
                "ci_lo": pl - 0.003,
                "ci_hi": pl + 0.003,
                "p": 0.01,
                "mde": 0.002,
            }
        )
        for fam in E.FAMILIES:
            rows.append(
                {
                    "season": s,
                    "arm": f"KO_{fam}_minus_FULL",
                    "delta": ko,
                    "ci_lo": 0,
                    "ci_hi": 0,
                    "p": 1,
                    "mde": 0,
                }
            )
    cov = {
        a: {str(s): {f"{t:.2f}": t for t in E.COV_TAUS} for s in E.SEASONS} for a in ("FULL", "WF")
    }
    cov["WF"]["2024"]["0.10"] = cov_wf
    slices = [
        {"season": 2024, "slice": "bench", "n": 15000, "delta": slice_d},
        {"season": 2024, "slice": "playoffs", "n": 200, "delta": 0.5},
    ]
    return {"rows": rows, "slices": slices, "coverage": cov}


def test_decide_pass_needs_every_rule_and_the_shuffle() -> None:
    shuf_ok = {"complete": True, "fires": False}
    assert E.decide(_res(), True, shuf_ok)["pass"]
    assert not E.decide(_res(), True, {"complete": False})[
        "pass"
    ]  # primary passed: the shuffle must have run
    assert not E.decide(_res(), True, {"complete": True, "fires": True})["pass"]
    assert not E.decide(_res(d23=-0.004), True, shuf_ok)["pass"]
    assert not E.decide(_res(d24=-0.004), True, shuf_ok)["pass"]
    assert not E.decide(_res(hi=0.0001), True, shuf_ok)["pass"]
    assert not E.decide(_res(p=0.06), True, shuf_ok)["pass"]
    assert not E.decide(_res(pl=-0.002), True, shuf_ok)["pass"]
    assert not E.decide(_res(cov_wf=0.115), True, shuf_ok)["pass"]
    assert not E.decide(_res(slice_d=0.011), True, shuf_ok)["pass"]
    assert not E.decide(_res(), False, shuf_ok)["pass"]
    v = E.decide(_res(d23=0.001), True, {"complete": False})
    assert (
        v["rule4_shuffle"] is None
        and v["rule4_note"] == "not run, primary failed"
        and "no effect larger than" in v["null_statement"]
    )
    named = E.decide(_res(ko=-0.001), True, shuf_ok)  # dropping any family leaves 12.5% of the gain
    assert named["pass"] and named["candidate_needs_adversary_clearance"] and named["named_family"]


# --------------------------------------------------------------------------- gate stops and the drift rule
def _dry(tmp_path: Path, k: int = 2) -> E.Ctx:
    return E.dry_ctx(tmp_path, n_shuffle=k)


def test_gate_stops_on_a_pin_mismatch_before_any_fit(tmp_path: Path) -> None:
    ctx = _dry(tmp_path)
    ctx.pin = "0" * 64
    g = E.run_gate(ctx)
    assert not g["gate_ok"] and "pin" in g["stop"] and not (ctx.cache / "heads_P0C.npz").exists()
    with pytest.raises(RuntimeError, match="gate has not passed"):
        E.run_arms(ctx)


def test_reproduction_failure_with_identical_inputs_is_a_kill_and_with_changed_inputs_is_drift(
    tmp_path: Path,
) -> None:
    ctx = _dry(tmp_path / "a")
    assert ctx.full_heads is not None
    ctx.full_heads = {
        **ctx.full_heads,
        "row_c": ctx.full_heads["row_c"] + 1e-3,
    }  # same inputs, different outputs
    g = E.run_gate(ctx)
    assert not g["gate_ok"] and "code defect" in g["stop"]
    ctx2 = _dry(tmp_path / "b")
    assert ctx2.comps is not None
    ctx2.comps = {
        **ctx2.comps,
        "row_c": ctx2.comps["row_c"] + 1e-3,
        "row_m": ctx2.comps["row_m"] + 1.0,
    }  # stored inputs differ: drift
    g2 = E.run_gate(ctx2)
    assert g2["drift"]["drift"] and (ctx2.cache / "a0_pic_refit.npz").exists()
    pc = next(t for t in g2["tests"] if t["test"].startswith("positive control"))
    assert "drift" in pc["threshold"]


def test_tip_cap_caps_none_refuses_2025_and_never_opens_a_later_schedule_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.features import game_tipoff as GT

    for name in ("raw_2024-25", "raw_2025-26", "live_tips_2026-27"):
        pl.DataFrame(
            {
                "gameId": [name],
                "gameDateTimeUTC": ["2025-01-01T00:00:00Z"],
                "gameDateTimeEst": ["2024-12-31T19:00:00Z"],
            }
        ).write_parquet(tmp_path / f"{name}.parquet")
    opened: list[str] = []
    real_read = pl.read_parquet

    def rec(f: Any, *a: Any, **k: Any) -> pl.DataFrame:
        opened.append(Path(f).stem)
        return real_read(f, *a, **k)

    monkeypatch.setattr(GT.pl, "read_parquet", rec)
    with E.tip_cap() as calls:
        out = GT.build_game_tipoff(tmp_path)
        with pytest.raises(RuntimeError, match="holdout"):
            GT.build_game_tipoff(tmp_path, max_season=2025)
    assert (
        calls == [None, 2025]
        and opened == ["raw_2024-25"]
        and out["game_id"].to_list() == ["raw_2024-25"]
    )


# --------------------------------------------------------------------------- end to end
def test_dryrun_runs_every_stage_end_to_end_on_synthetic_labels(tmp_path: Path) -> None:
    ctx = _dry(tmp_path, k=2)
    assert E.run_all(ctx) == 0
    res = json.loads((tmp_path / "results.json").read_text())
    v = res["verdict"]
    assert v["rule7_gate_ok"] and res["shuffle"]["complete"] and res["shuffle"]["K"] == 2
    assert res["scores"]["descriptive"]["arm_saw_it"]["x22_gain_share_WF"] > 0
    assert res["scores"]["descriptive"]["B_refit_vs_cached_pts__B"]["match_1e-6"]
    arms = {r["arm"] for r in res["scores"]["rows"]}
    assert {
        "WF_minus_FULL",
        "WF_minus_PL",
        "FULL_minus_A0",
        "B_minus_PL27",
        "WF_minus_FULL_date_clustered",
    } <= arms
    pos = {r["season"]: r["delta"] for r in res["scores"]["rows"] if r["arm"] == "FULL_minus_A0"}
    assert all(
        pos[s] < -0.005 for s in E.SEASONS
    )  # synthetic positive control: FULL carries the planted recent-role signal
    assert (tmp_path / "report.md").read_text().startswith("# WORKLOAD_PTS_V2 results")
    assert not E.OUT_DIR.joinpath("dryrun").exists()


def test_no_2025_literal_in_the_loaders() -> None:
    for fn in (E.build_frame, E.real_ctx):
        assert 2025 not in fn.__code__.co_consts
    assert dt.date(2024, 1, 1).year <= E.MAX_SEASON == 2024


# --------------------------------------------------------------------------- adversary review 2026-10-27 (M1, m1, m2, m3, m4, m6)
def test_M1_stale_checkpoint_after_a_training_only_change_is_refused(tmp_path: Path) -> None:
    """The adversary's scenario: gate + arms, then only training-only (2022) values of one of the 22 change; the pin and the gate still
    pass, but the old WF checkpoint must be refused, not reused."""
    ctx = _dry(tmp_path)
    assert E.run_gate(ctx)["gate_ok"]
    E.run_arms(ctx)
    fr, cfg, aux = ctx.frame()
    changed = fr.with_columns(
        pl.when(pl.col("season") == 2022)
        .then(pl.col("wl_g_7d") + 3.0)
        .otherwise(pl.col("wl_g_7d"))
        .alias("wl_g_7d")
    )
    ctx2 = E.Ctx(
        **{**ctx.__dict__, "_frame": None, "frame_fn": lambda: (changed, cfg, aux), "stamp": {}}
    )
    g2 = E.run_gate(ctx2)
    assert g2["gate_ok"] and g2["frame_sha256"] == ctx.pin  # the A0 pin cannot see the change
    assert g2["frame_all_sha256"] != json.loads(json.dumps(ctx.stamp))["frame_all_sha256"]
    with pytest.raises(RuntimeError, match="stale checkpoint"):
        E.run_arms(ctx2)


def test_m1_descriptive_rows_are_a_separate_labelled_table(tmp_path: Path) -> None:
    ctx = _dry(tmp_path, k=2)
    assert E.run_all(ctx) == 0
    md = (tmp_path / "report.md").read_text()
    res = json.loads((tmp_path / "results.json").read_text())
    i = md.index(E.DESC_LABEL)
    assert "per-number 95% CI, not multiplicity-adjusted" in E.DESC_LABEL
    for arm in (
        "B_minus_A0",
        "B_minus_PL27",
        "PL27_minus_A0",
        "PL_minus_FULL",
        "KO_LOADX_minus_FULL",
        "WF_minus_FULL_date_clustered",
    ):
        assert md.index(f"| {arm} |") > i, arm
    for arm in E.RULE_ROWS:
        assert md.index(f"| {arm} |") < i, arm
    assert "not multiplicity-adjusted" in res["scores"]["descriptive"]["T452_rejudged"]["note"]


def test_m2_a_failing_early_gate_test_stops_before_any_fit(tmp_path: Path) -> None:
    ctx = _dry(tmp_path)
    fr, cfg, aux = ctx.frame()
    bad = fr.with_columns(
        pl.lit(1.0).alias(E.PL + E.X22[0])
    )  # breaks W6 only (pin columns untouched)
    ctx.frame_fn, ctx._frame = (lambda: (bad, cfg, aux)), None
    g = E.run_gate(ctx)
    assert not g["gate_ok"] and g["stop"].startswith("W6")
    assert not list((tmp_path / "cache").glob("*.npz")) if (tmp_path / "cache").exists() else True
    assert [t["test"] for t in g["tests"]][-1].startswith("W6")


def test_m3_zero_importance_share_is_a_pipeline_fault_not_a_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _dry(tmp_path)
    assert E.run_gate(ctx)["gate_ok"]
    E.run_arms(ctx)
    monkeypatch.setattr(E, "importance_share", lambda comp, cols=E.X22: 0.0)
    v = E.run_score(ctx)["verdict"]
    assert (
        v["pipeline_fault"] and not v["pass"] and v["null_statement"].startswith("PIPELINE FAULT")
    )


def test_m4_shuffle_refuses_unless_rules_1_2_passed(tmp_path: Path) -> None:
    ctx = _dry(tmp_path)
    ctx.force_shuffle = False
    assert E.run_gate(ctx)["gate_ok"]
    with pytest.raises(RuntimeError, match="shuffle refused"):
        E.run_shuffle(ctx)


def test_m6_tip_cap_also_patches_the_workload_gates_binding() -> None:
    from research.eval import workload_gates as WG

    orig = getattr(WG, "build_game_tipoff", None)
    with E.tip_cap():
        if orig is not None:
            assert WG.build_game_tipoff is not orig
    assert getattr(WG, "build_game_tipoff", None) is orig
