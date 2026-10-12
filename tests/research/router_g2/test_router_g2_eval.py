# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,arg-type,index,assignment,operator"
# ruff: noqa: E501
"""ROUTER_G2 harness: freeze and holdout refusals, the hard route, an as-of g' walk (planted future, non-inert control), the
constant gate, arms that collapse to A0 when the gate is closed, SHUF-C / SHUF-G that can fail, and the decision logic.

Everything runs on a synthetic world (no DB, no stored artefact)."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import near_miss_bundle_eval as N
from research.eval import recent_role_eval as RR
from research.eval import router_g2_eval as R
from tests.research.near_miss_bundle.test_near_miss_bundle import make_world

CFG = ContextResidualConfig()


@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    return make_world()


def _gates(n: int, geff: np.ndarray) -> dict[str, np.ndarray]:
    z = np.zeros(n)
    return {
        "g": geff, "g_pl": z, "g_shuf": z, "g_const": z, "has_row": np.ones(n, dtype=bool),
        "geff": geff, "geff_pl": z, "geff_shuf": z, "geff_const": np.full(n, 0.92),
    }  # fmt: skip


# --------------------------------------------------------------------------- declared structure
def test_declared_cells_and_constants() -> None:
    assert R.CELLS == (
        ("E1p", "pts"),
        ("E2p", "pts"),
        ("E2p", "reb"),
        ("E2p", "ast"),
        ("E2p", "fg3m"),
        ("E4p", "pts"),
    )
    assert R.TAU == 0.90 and R.FLOOR == -0.005 and R.REGULAR_PREFIX == "002"
    assert set(R.PLACEBO_INPUTS) == {"E1p", "E2p", "E4p"}


# --------------------------------------------------------------------------- freeze and holdout refusal
def _doc(tmp: Path, status: str) -> Path:
    p = tmp / "ROUTER_G2.md"
    p.write_text(f"# x\n\nStatus: {status}\n\nrule text\n\n{R.MARKER}\n\nresults\n")
    return p


def test_require_frozen_refusals(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "FROZEN")
    sha = R.frozen_sha(doc)
    assert sha == hashlib.sha256(R.head_text(doc).encode()).hexdigest()

    def ok_git(*a: str) -> str:
        return f"abc\tprereg({R.ID}): {sha}\n"

    def no_git(*a: str) -> str:
        return "abc\tsomething else\n"

    assert R.require_frozen(sha, doc, ok_git) == sha
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        R.require_frozen("0" * 64, doc, ok_git)
    with pytest.raises(RuntimeError, match="--sha"):
        R.require_frozen(None, doc, ok_git)
    with pytest.raises(RuntimeError, match="not frozen"):
        R.require_frozen(sha, doc, no_git)
    draft = _doc(tmp_path, "DRAFT")
    with pytest.raises(RuntimeError, match="not marked FROZEN"):
        R.require_frozen(
            R.frozen_sha(draft), draft, lambda *a: f"abc\tprereg({R.ID}): {R.frozen_sha(draft)}\n"
        )


def test_holdout_refuses_without_a_committed_confirmed_row(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "FROZEN")
    sha = R.frozen_sha(doc)
    log = tmp_path / "log.md"

    def mk(dirty: bool = False, in_commit: bool = True) -> Any:
        def git(*a: str) -> str:
            if a[0] == "log" and "--format=%H%x09%s" in a:
                return f"abc\tprereg({R.ID}): {sha}\n"
            if a[0] == "log":
                return "abc\n" if in_commit else ""
            if a[0] == "status":
                return " M log" if dirty else ""
            return ""

        return git

    log.write_text("nothing\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        R.require_holdout_row(sha, log, doc, mk())
    log.write_text(f"| 2026-10-12 | {R.ID} {sha[:8]} PRE-REGISTERED only |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        R.require_holdout_row(sha, log, doc, mk())
    log.write_text(f"| 2026-10-12 | {R.ID} {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n")
    assert R.ID in R.require_holdout_row(sha, log, doc, mk())
    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_holdout_row(sha, log, doc, mk(dirty=True))
    with pytest.raises(RuntimeError, match="not in any commit"):
        R.require_holdout_row(sha, log, doc, mk(in_commit=False))


def test_run_refuses_before_taking_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from research.eval import workload_eval as WE

    def boom() -> None:
        raise AssertionError("heavy.lock must not be taken before the freeze is verified")

    monkeypatch.setattr(WE, "heavy", boom)
    with pytest.raises(RuntimeError):
        R.stage_run("0" * 64)
    with pytest.raises(RuntimeError, match="--sha"):
        R.stage_run(None)


def test_holdout_stage_has_no_2025_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(R, "require_holdout_row", lambda sha: "row")
    with pytest.raises(SystemExit, match="no 2025 loader"):
        R.stage_holdout("a" * 64)
    monkeypatch.undo()
    with pytest.raises(RuntimeError):
        R.stage_holdout(None)


# --------------------------------------------------------------------------- the hard route and the routing
def test_effective_gate_hard_routes_postseason_nan_and_missing_rows() -> None:
    gid = np.array(["0022300001", "0042300001", "0052300001", "0022300002", "0022300003"])
    g = np.array([0.95, 0.99, 0.99, np.nan, 0.8])
    has = np.array([True, True, True, False, True])
    e = R.effective_gate(g, gid, has)
    assert e.tolist() == [0.95, 0.0, 0.0, 0.0, 0.8]
    assert np.isfinite(R.effective_gate(g, gid)).all()
    assert R.effective_gate(np.array([1.5]), np.array(["0022300001"]))[0] == 1.0


def test_route_uses_other_only_where_the_gate_clears_tau() -> None:
    first = np.zeros((4, 199), dtype=np.int16)
    other = np.ones((4, 199), dtype=np.int16)
    out = R.route(first, other, np.array([0.0, 0.8999, 0.9, 1.0]))
    assert out[:, 0].tolist() == [0, 0, 1, 1]


def test_constant_gate_is_asof_and_ignores_later_labels() -> None:
    dates = np.array(
        [dt.date(2023, 1, 1) + dt.timedelta(days=int(d)) for d in range(0, 120, 5)],
        dtype="datetime64[D]",
    )
    label = np.tile([1.0, 0.0, 1.0, 1.0], len(dates) // 4 + 1)[: len(dates)]
    starts = {0: np.datetime64("2023-03-01"), 1: np.datetime64("2023-04-01")}
    # direct construction: rows labelled with blocks; training rows are the first len(dates) with date < start
    rows_blk = np.array([0.0, 1.0, np.nan])
    out = R.constant_gate(rows_blk, starts, dates, label)
    m0, m1 = dates < starts[0], dates < starts[1]
    assert out[0] == pytest.approx(label[m0].mean()) and out[1] == pytest.approx(label[m1].mean())
    assert np.isnan(out[2])
    planted = label.copy()
    planted[dates >= starts[0]] = 7.0
    out2 = R.constant_gate(rows_blk, starts, dates, planted)
    assert (
        out2[0] == out[0] and out2[1] != out[1]
    )  # block 0 cannot see later labels; block 1 may see block-0-era rows


# --------------------------------------------------------------------------- g' is a played-rows, minutes >= 5 walk and is as-of
def _frame(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    gid = 0
    for season, start in (
        (2022, dt.date(2022, 11, 1)),
        (2023, dt.date(2023, 11, 1)),
        (2024, dt.date(2024, 11, 1)),
    ):
        for day in range(0, 150, 2):
            d = start + dt.timedelta(days=day)
            gid += 1
            for p in range(12):
                x = rng.normal()
                z = rng.normal()
                short = rng.random() < 1.0 / (1.0 + np.exp(-(1.5 * z - 2.2)))
                played = rng.random() < 0.9
                rows.append(
                    {
                        "game_id": f"002{season % 100:02d}{gid:05d}", "player_id": p, "season": season, "game_date": d,
                        "played": 1 if played else 0, "min_real": (3.0 if short else 22.0) if played else 0.0, "x": x, "z": z,
                    }
                )  # fmt: skip
    return pl.DataFrame(rows)


def test_gate_label_frame_keeps_played_rows_and_labels_minutes_ge_5() -> None:
    fr = _frame()
    pf = R.gate_label_frame(fr)
    assert pf.height == int((fr["played"] == 1).sum())
    assert ((pf["min_real"] >= 5).cast(pl.Int64) == pf["played"]).all()


def _walk(fr: pl.DataFrame, cols: list[str], **kw: Any) -> dict[str, Any]:
    res, _ = RR.walk(R.gate_label_frame(fr), {"G": cols}, CFG, min_train=300, n_estimators=15, **kw)
    return res


def test_gate_walk_is_as_of_planted_future_and_non_inert_control() -> None:
    fr = _frame()
    cut = dt.date(2025, 1, 10)
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("min_real")).alias("min_real"),
        pl.when(late).then(9.0).otherwise(pl.col("z")).alias("z"),
    )
    a, b = _walk(fr, ["x", "z"]), _walk(edited, ["x", "z"])
    ja = pl.DataFrame({"game_id": a["gid"], "player_id": a["pid"], "pa": a["p_G"]})
    jb = pl.DataFrame({"game_id": b["gid"], "player_id": b["pid"], "pb": b["p_G"]})
    j = ja.join(jb, on=["game_id", "player_id"]).join(
        fr.select("game_id", "player_id", "game_date"), on=["game_id", "player_id"]
    )
    pre = j.filter(pl.col("game_date") < cut)
    nxt = j.filter(pl.col("game_date") >= dt.date(2025, 2, 15))
    assert pre.height > 100 and nxt.height > 100
    assert float(np.abs(pre["pa"].to_numpy() - pre["pb"].to_numpy()).max()) == 0.0
    assert float(np.abs(nxt["pa"].to_numpy() - nxt["pb"].to_numpy()).max()) > 0.0


def test_gate_learns_the_planted_signal_and_shuffled_labels_do_not() -> None:
    fr = _frame()
    pf = R.gate_label_frame(fr)
    real = _walk(fr, ["x", "z"])
    shuf = _walk(fr, ["x", "z"], shuffle=True)
    y = real["y"].astype(float)
    from research.eval import workload_eval as WE

    assert WE._ll(real["p_G"], y).mean() < WE._ll(shuf["p_G"], y).mean() - 0.02
    assert pf.height > 0 and set(np.unique(y)) <= {0.0, 1.0}


def test_align_to_rows_puts_values_on_the_a0_keys_and_nan_elsewhere() -> None:
    res = {"gid": np.array(["a", "b"]), "pid": np.array([1, 2]), "p_G": np.array([0.2, 0.7])}
    out = R.align_to_rows(res, "p_G", np.array(["b", "a", "c"]), np.array([2, 1, 3]))
    assert out[:2].tolist() == [0.7, 0.2] and np.isnan(out[2])


# --------------------------------------------------------------------------- arms on the synthetic world
def test_closed_gate_collapses_every_arm_to_a0(world: dict[str, Any]) -> None:
    w = world["w"]
    n = len(w["base"]["pts"]["y"])
    gates = _gates(n, np.zeros(n))
    gates["geff_const"] = np.zeros(n)
    arms = R.build_arms("pts", w, gates)
    for k in (
        "E1p",
        "E2p",
        "E4p",
        "E1p_PL_G",
        "E4p_PL_G",
        "E1p_SHUFG",
        "E2p_SHUFG",
        "E1p_CONST",
        "E2p_CONST",
        "E4p_CONST",
    ):
        assert np.array_equal(arms[k], arms["A0"]), k


def test_open_gate_gives_the_unrouted_members(world: dict[str, Any]) -> None:
    w = world["w"]
    n = len(w["base"]["pts"]["y"])
    gates = _gates(n, np.ones(n))
    gates["geff_const"] = np.ones(n)
    arms = R.build_arms("pts", w, gates)
    assert np.array_equal(arms["E2p"], arms["E2_all"])
    assert np.array_equal(
        arms["E1p"], N.router_grid(arms["A0"], w["a1_int"], np.ones(n), w["a1_present"])
    )
    assert np.array_equal(
        arms["E4p_CONST"], arms["E4p"]
    )  # constant gate 1 clears TAU like g' = 1: identical
    arms_r = R.build_arms("reb", w, gates)
    assert not any(k.startswith(("E1", "E4")) for k in arms_r)


def test_score_cell_end_to_end_with_planted_signal(world: dict[str, Any]) -> None:
    w = world["w"]
    n = len(w["base"]["pts"]["y"])
    gates = _gates(n, np.ones(n))
    arms = R.build_arms("reb", w, gates)
    rs = R.RowScores(w["base"]["reb"])
    nulls = R.null_world("reb", w, gates)
    res = R.score_cell("E2p", "reb", w, arms, rs, nulls)
    for s in ("2023", "2024"):
        d = res["seasons"][s]["delta"]
        assert d["point"] < -0.01 and d["hi"] < 0
    assert res["shuffle_ok"] is True and set(res["placebo"]) == {"PL_G", "PL_SEQ", "CONST"}
    assert res["flags"] == {}  # no carrier flag for a one-member cell
    dec = R.decide_cell(res, True, {"2023": 0.001, "2024": 0.001})
    assert dec["verdict"] in {"PASS", "FAIL_GUARD"}
    # E4' carries the flag
    wp = R.build_arms("pts", w, gates)
    rsp = R.RowScores(w["base"]["pts"])
    r4 = R.score_cell("E4p", "pts", w, wp, rsp, R.null_world("pts", w, gates))
    assert set(r4["flags"]) == {"2023", "2024"} and set(r4["placebo"]) == {
        "PL_G",
        "PL_A1",
        "PL_SEQ",
        "CONST",
    }


def test_no_signal_world_is_not_a_pass() -> None:
    wd = make_world(4, signal=False)
    w = wd["w"]
    n = len(w["base"]["reb"]["y"])
    gates = _gates(n, np.ones(n))
    arms = R.build_arms("reb", w, gates)
    res = R.score_cell(
        "E2p", "reb", w, arms, R.RowScores(w["base"]["reb"]), R.null_world("reb", w, gates)
    )
    dec = R.decide_cell(res, True, {"2023": 0.9, "2024": 0.9})
    assert dec["verdict"] in {"FAIL", "FAIL_GUARD"}


# --------------------------------------------------------------------------- the shuffle checks can fail
def leaky_blend(
    first199: np.ndarray,
    seq199: np.ndarray,
    y: np.ndarray,
    date_days: np.ndarray,
    season: np.ndarray,
    ctx: Any,
    stat: str = "pts",
) -> tuple[np.ndarray, np.ndarray]:
    pooled = 0.5 * first199 + 0.5 * seq199
    pooled = pooled + 0.6 * (y - pooled.mean(axis=1))[:, None]
    return pooled, np.full((len(y), 2), 0.5)


def test_shuf_c_detects_a_planted_leak_and_passes_the_honest_pipeline(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world["w"]
    n = len(w["base"]["reb"]["y"])
    gates = _gates(n, np.ones(n))
    honest = R.null_world("reb", w, gates)["E2p|reb"]
    assert not N.shuffle_fails(honest), honest
    monkeypatch.setattr(N, "learned_blend", leaky_blend)
    leaked = R.null_world("reb", w, gates)["E2p|reb"]
    assert N.shuffle_fails(leaked), leaked


def test_shuf_g_semantics_and_placebo_margin() -> None:
    ok = {"2023": {"point": 0.0004, "hi": 0.002}, "2024": {"point": -0.0004, "hi": 0.001}}
    bad = {"2023": {"point": -0.0015, "hi": -0.0003}, "2024": {"point": 0.0, "hi": 0.001}}
    assert not R.shuf_g_fails(ok) and R.shuf_g_fails(bad)
    good = {"2023": {"point": -0.002, "hi": -0.001}, "2024": {"point": -0.0012, "hi": -0.0002}}
    assert R.placebo_pass(good)
    assert not R.placebo_pass(
        {**good, "2024": {"point": -0.0008, "hi": -0.0001}}
    )  # misses the margin
    assert not R.placebo_pass({**good, "2024": {"point": -0.002, "hi": 0.0001}})  # CI reaches 0


def test_check_cell_needs_floor_ci_and_bh_in_both_seasons() -> None:
    d = {"2023": {"point": -0.006, "hi": -0.002}, "2024": {"point": -0.0049, "hi": -0.001}}
    assert R.check_cell(d, {"2023": 0.01, "2024": 0.01}) == {"2023": True, "2024": False}
    d["2024"]["point"] = -0.006
    assert R.check_cell(d, {"2023": 0.01, "2024": 0.06}) == {"2023": True, "2024": False}
    assert all(R.check_cell(d, {"2023": 0.01, "2024": 0.01}).values())


def test_verdict_order() -> None:
    assert R.cell_verdict(True, True, True, True, True, False) == "HARM"
    assert R.cell_verdict(False, False, True, True, True, False) == "FAIL"
    assert R.cell_verdict(False, True, False, True, True, False) == "FAIL_GUARD"
    assert R.cell_verdict(False, True, True, False, True, False) == "FAIL_GUARD"
    assert R.cell_verdict(False, True, True, True, False, False) == "FAIL_GUARD"
    assert R.cell_verdict(False, True, True, True, True, True) == "FLAG_ONE_MEMBER"
    assert R.cell_verdict(False, True, True, True, True, False) == "PASS"
    assert R.cell_verdict(False, True, True, True, True, False, repro_ok=False) == "FAIL"
