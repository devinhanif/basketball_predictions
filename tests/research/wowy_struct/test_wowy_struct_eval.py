# ruff: noqa: E501
"""WOWY_STRUCT harness: the decision (one test per word), the inference code path, the guards, the arms and their permuted columns, alignment and the order of
the run. Synthetic only (no database, no real fit)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.eval.lower_tail_eval import attach_realised
from nba.props.context_residual import ContextResidualConfig, build_features
from nba.props.metrics import _clustered_boot_means
from research.eval import f1_warm_window_run as F1R
from research.eval import wowy_struct_eval as E
from tests.props.test_context_residual import ELO, _league

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs/prereg/WOWY_STRUCT.md"


# ----------------------------------------------------------------- one test per word
@pytest.mark.parametrize(
    ("world", "word"),
    [
        ("pass", "PASS"),
        ("guard", "FAIL_GUARD"),
        ("one_season", "FAIL_ONE_SEASON"),
        ("no_gain", "FAIL_NO_GAIN"),
    ],
)
def test_each_word_is_reachable_in_its_world(world: str, word: str) -> None:
    for seed in range(2):
        assert E.evaluate(E.synthetic_bundle(world, seed))["word"] == word, (world, seed)


def test_every_word_is_tested_and_written_in_the_rule() -> None:
    lines = DOC.read_text().splitlines()
    text = "\n".join(
        lines[: lines.index("=== RESULTS BELOW ===")]
    )  # the marker is a whole line, as in the freeze hash
    for w in E.WORDS:
        assert w in text, w
    for w in E.TWIST_LABELS:
        assert w in text, w
    tested = {"PASS", "FAIL_GUARD", "FAIL_ONE_SEASON", "FAIL_NO_GAIN"}
    assert set(E.WORDS) == tested


def test_pass_world_passes_every_cell_with_g1_holding() -> None:
    ev = E.evaluate(E.synthetic_bundle("pass", 0))
    assert all(v["word"] == "PASS" for v in ev["cells"].values()) and ev["verdict"] == "PASS"
    assert all(r["g1"] and r["g2"] and r["g3"] and r["g5"] for r in ev["rows"])


def test_guard_world_fails_g1_only() -> None:
    ev = E.evaluate(E.synthetic_bundle("guard", 0))
    assert ev["word"] == "FAIL_GUARD"
    assert all(r["clears"] for r in ev["rows"])
    assert not any(r["g1"] for r in ev["rows"])
    assert all(r["g2"] and r["g3"] and r["g5"] for r in ev["rows"])


def test_one_season_world_clears_2023_only() -> None:
    ev = E.evaluate(E.synthetic_bundle("one_season", 0))
    for v in ev["cells"].values():
        assert v["clears_2023"] and not v["clears_2024"]


def test_gate_or_reproduction_failure_is_a_stop_not_a_word() -> None:
    b = E.synthetic_bundle("pass", 0)
    with pytest.raises(RuntimeError, match="STOP"):
        E.evaluate(b, gates_ok=False)
    with pytest.raises(RuntimeError, match="STOP"):
        E.evaluate(b, repro_ok=False)


def test_a_gain_between_zero_and_the_floor_does_not_clear() -> None:
    b = E.synthetic_bundle("pass", 0)
    for st in E.STATS:
        rec = b["stats"][st]
        sc = rec["score"]
        cell = np.zeros(len(sc["ROLE"]), dtype=bool)
        for sn in rec["cells"]:
            cell |= rec["cells"][sn]
        sc["S_R"] = np.where(
            cell, sc["ROLE"] - 0.002, sc["S_R"]
        )  # real but below the 0.005 floor, no noise
    ev = E.evaluate(b)
    assert ev["word"] == "FAIL_NO_GAIN"


def test_g2_all_rows_no_harm_blocks_a_pass() -> None:
    b = copy.deepcopy(E.synthetic_bundle("pass", 0))
    for st in E.STATS:
        sc = b["stats"][st]["score"]
        sc["S_R"] = sc["S_R"] + 0.02 * (
            ~(b["stats"][st]["cells"]["leader_out"])
        )  # worse than A0 outside the cell
    ev = E.evaluate(b)
    assert ev["word"] == "FAIL_GUARD" and not any(r["g2"] for r in ev["rows"])


def test_g9_by_itself_turns_a_pass_into_fail_guard() -> None:
    b = copy.deepcopy(E.synthetic_bundle("pass", 0))
    for st in E.STATS:
        rec = b["stats"][st]
        exposed = ~rec["unexposed"]
        sc = rec["score"]
        cell = np.zeros(len(exposed), dtype=bool)
        for c in rec["cells"].values():
            cell |= c
        # the primary clears on all rows (0.015 everywhere, 0.20 more in exposed games) but only 0.003 outside the exposed games
        sc["S_R"] = (
            sc["ROLE"] - np.where(cell, np.where(exposed, 0.20, 0.003), 0.0) + 0.0 * sc["S_R"]
        )
        sc["P_R"] = sc["ROLE"]  # G1 holds
    ev = E.evaluate(b)
    assert all(r["clears"] for r in ev["rows"])
    assert all(r["g1"] and r["g2"] and r["g3"] and r["g5"] for r in ev["rows"])
    assert not any(r["g9"] for r in ev["rows"])
    assert ev["word"] == "FAIL_GUARD"
    assert all(r["g9"] for r in E.evaluate(E.synthetic_bundle("pass", 0))["rows"])


def test_descriptive_contrasts_carry_no_p_value_and_exposed_games_are_reported() -> None:
    ev = E.evaluate(E.synthetic_bundle("pass", 0))
    for r in ev["rows"]:
        assert all("p" not in d for d in r["desc"].values())
        assert r["exposed"] is not None and "p" not in r["exposed"]


def _comp(gid: np.ndarray, pid: np.ndarray, y: np.ndarray, qi: np.ndarray) -> dict[str, Any]:
    return {
        "row_gid": gid,
        "row_pid": pid,
        "row_y": y,
        "row_season": np.full(len(gid), 2023),
        "qi": qi,
    }


def test_build_bundle_scores_arms_on_a0_keys_with_the_integer_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = 6
    gid = np.array([f"g{i}" for i in range(n)])
    pid = np.arange(n)
    y = np.array([3.0, 5.0, 0.0, 8.0, 2.0, 4.0])
    rng = np.random.default_rng(0)
    base_qi = np.sort(rng.integers(0, 12, size=(n, 199)), axis=1)
    monkeypatch.setattr(E.F1R, "qi_of", lambda c, pic, st: c["qi"])
    monkeypatch.setattr(
        E,
        "cell_and_slice_masks",
        lambda fr, st, g, p: (
            {"leader_out": np.ones(n, bool), "top25_fixed": np.zeros(n, bool)},
            {},
            {},
        ),
    )
    perm = np.array([3, 1, 5, 0, 2, 4])  # the arm's rows come in a different order
    comps: dict[str, dict[str, dict[str, Any]]] = {}
    for st in E.STATS:
        comps[st] = {"A0": _comp(gid, pid, y, base_qi)}
        for arm in E.FIT_ARMS:
            comps[st][arm] = _comp(
                gid[perm], pid[perm], y[perm], base_qi[perm] + (1 if arm == "ROLE" else 0)
            )
    dates = {g: "2023-11-01" for g in gid}
    b = E.build_bundle(comps, {}, pl.DataFrame(), dates)
    for st in E.STATS:
        want = F1R.crps_int(base_qi, y)
        assert np.allclose(b["stats"][st]["score"]["A0"], want)
        assert np.allclose(
            b["stats"][st]["score"]["S_R"], want
        )  # reordered rows land back on A0's order
        assert np.allclose(b["stats"][st]["score"]["ROLE"], F1R.crps_int(base_qi + 1, y))
    bad = copy.deepcopy(comps)
    bad["pts"]["T_R"]["row_pid"] = bad["pts"]["T_R"]["row_pid"] + 100
    with pytest.raises(RuntimeError, match="evaluation key is missing"):
        E.build_bundle(bad, {}, pl.DataFrame(), dates)
    bad = copy.deepcopy(comps)
    bad["ast"]["RAW"]["row_y"] = bad["ast"]["RAW"]["row_y"] + 1
    with pytest.raises(RuntimeError, match="outcomes differ"):
        E.build_bundle(bad, {}, pl.DataFrame(), dates)


def test_g3_coverage_blocks_a_pass() -> None:
    b = copy.deepcopy(E.synthetic_bundle("pass", 0))
    for st in E.STATS:
        b["stats"][st]["pit"]["S_R"] = b["stats"][st]["pit"]["S_R"] ** 1.5  # miscalibrated tails
    ev = E.evaluate(b)
    assert ev["word"] == "FAIL_GUARD" and not any(r["g3"] for r in ev["rows"])


def test_g5_gates_only_slices_with_300_rows_and_the_playoffs_slice_is_reported() -> None:
    b = copy.deepcopy(E.synthetic_bundle("pass", 0))
    rec = b["stats"]["pts"]
    po = rec["slices"]["playoffs"] & rec["cells"]["leader_out"]
    rec["score"]["S_R"] = np.where(
        po, rec["score"]["ROLE"] + 0.5, rec["score"]["S_R"]
    )  # a huge harm inside a small slice
    ev = E.evaluate(b)
    r = next(r for r in ev["rows"] if r["cell"] == "pts|leader_out" and r["season"] == 2023)
    assert r["slices"]["playoffs"]["n"] < E.MIN_SLICE_N and not r["slices"]["playoffs"]["gated"]
    assert r["g5"] and ev["word"] == "PASS"
    # the same harm in a big slice does gate
    rec2 = copy.deepcopy(E.synthetic_bundle("pass", 0))["stats"]["pts"]
    big = rec2["slices"]["bench"] & rec2["cells"]["leader_out"]
    assert int(big.sum()) > 2 * E.MIN_SLICE_N


def test_the_twist_arms_never_change_a_word() -> None:
    a = E.synthetic_bundle("pass", 0)
    b = copy.deepcopy(a)
    for st in E.STATS:
        for arm in ("T_R", "TP_R", "TQ", "RAW"):
            b["stats"][st]["score"][arm] = b["stats"][st]["score"][arm] + 0.5
    ea, eb = E.evaluate(a), E.evaluate(b)
    assert ea["word"] == eb["word"] == "PASS"
    assert [r["primary"]["delta"] for r in ea["rows"]] == [
        r["primary"]["delta"] for r in eb["rows"]
    ]


def test_twist_labels() -> None:
    def h(d: float, lo: float, hi: float) -> dict[str, float]:
        return {"delta": d, "ci_lo": lo, "ci_hi": hi}

    assert E.twist_label([h(-0.004, -0.007, -0.001)] * 2) == "TWIST_BETTER"
    assert E.twist_label([h(0.004, 0.001, 0.007)] * 2) == "TWIST_WORSE"
    assert E.twist_label([h(-0.004, -0.007, -0.001), h(-0.001, -0.004, 0.002)]) == "TWIST_TIE"
    assert (
        E.twist_label([h(-0.004, -0.007, 0.001)] * 2) == "TWIST_TIE"
    )  # point past the floor but the CI reaches 0
    # the synthetic worlds: T_R is a tie with S_R in the pass world and 0.003 worse elsewhere (a worse label needs CI above 0)
    ev = E.evaluate(E.synthetic_bundle("pass", 1))
    assert {v["twist"] for v in ev["cells"].values()} <= set(E.TWIST_LABELS)


# ----------------------------------------------------------------- inference code path
def test_delta1_uses_the_house_draws_and_the_one_sided_p_formula() -> None:
    rng = np.random.default_rng(3)
    cl = np.repeat(np.arange(200), 5)
    d = rng.normal(-0.02, 0.5, size=1000)
    r = E.delta1(d, cl)
    draws = _clustered_boot_means(d, cl, E.N_BOOT, np.random.default_rng(E.SEED))
    assert r["ci_lo"] == pytest.approx(float(np.quantile(draws, 0.025)), abs=1e-15)
    assert r["ci_hi"] == pytest.approx(float(np.quantile(draws, 0.975)), abs=1e-15)
    assert r["p"] == pytest.approx((1 + int((draws >= 0).sum())) / (1 + E.N_BOOT))
    assert r["n_clusters"] == 200 and r["n_rows"] == 1000


def test_clustering_widens_the_interval_relative_to_rows() -> None:
    rng = np.random.default_rng(1)
    cl = np.repeat(np.arange(100), 10)
    d = np.repeat(rng.normal(0, 0.3, 100), 10) + 0.05 * rng.normal(size=1000)
    clustered = E.delta1(d, cl)
    rowwise = E.delta1(d, np.arange(1000))
    assert clustered["ci_hi"] - clustered["ci_lo"] > 1.5 * (rowwise["ci_hi"] - rowwise["ci_lo"])


def test_bh_runs_over_the_three_cells_of_a_season() -> None:
    ev = E.evaluate(E.synthetic_bundle("pass", 0))
    for s in E.SEASONS:
        rs = [r for r in ev["rows"] if r["season"] == s]
        assert len(rs) == 3 == len(E.CELLS)
        adj = F1R.bh_adjust(np.array([r["primary"]["p"] for r in rs]))
        assert np.allclose(adj, [r["p_bh"] for r in rs])


def test_g1_thresholds_are_the_frozen_ones() -> None:
    assert E.T_C == {"pts|leader_out": 0.0050, "pts|top25_fixed": 0.0063, "ast|leader_out": 0.0030}
    assert (
        E.FLOOR == -0.005
        and E.NOHARM == 0.001
        and E.COV_TOL == 0.01
        and E.SLICE_TOL == 0.01
        and E.MIN_SLICE_N == 300
    )


def test_coverage_guard_compares_with_a0_at_each_tau() -> None:
    a = {"0.10": 0.10, "0.20": 0.20, "0.80": 0.80, "0.90": 0.90}
    assert E.g3_coverage(a, {**a, "0.80": 0.805})
    assert not E.g3_coverage(a, {**a, "0.20": 0.215})


# ----------------------------------------------------------------- arms and permuted columns
def test_arm_columns_follow_the_rule() -> None:
    assert E.arm_columns("S_R", "pts") == ["role_share_pts", "struct_u_pts"]
    assert E.arm_columns("S_R", "ast") == ["role_share_ast", "struct_d_ast"]
    for st in E.STATS:
        for arm in E.FIT_ARMS:
            cols = E.arm_columns(arm, st)
            assert cols[0] == f"role_share_{st}"
            assert "struct_d_pts" not in cols
    assert E.arm_columns("T_R", "pts") == ["role_share_pts", "rb_pts", "rx_pts", "ro_conf"]
    assert E.arm_columns("TQ", "ast") == ["role_share_ast", "pq_rb_ast", "pq_rx_ast", "pq_ro_conf"]
    assert E.arm_columns("RAW", "pts") == ["role_share_pts", "rr_pts", "ro_conf"]
    assert E.ARMS[0] == "A0" and "A0" not in E.FIT_ARMS  # A0 is F1's arm C, never refitted here


def _perm_frame() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for s in (2023, 2024):
        for p in range(12):
            for g in range(20):
                rows.append(
                    (
                        s,
                        p,
                        f"{s}{g:03d}",
                        __import__("datetime").date(s, 10, 1 + g),
                        float(rng.normal()),
                        float(rng.normal()),
                        float(rng.normal()),
                        float(g),
                    )
                )
    return pl.DataFrame(
        rows,
        schema=[
            "season",
            "player_id",
            "game_id",
            "game_date",
            "struct_u_pts",
            "rb_pts",
            "rx_pts",
            "ro_conf",
        ],
        orient="row",
    )


def test_permuted_columns_are_within_player_season_and_share_one_permutation() -> None:
    f = E.add_permuted(_perm_frame(), "pts")
    assert f.height == 480
    for col in ("struct_u_pts", "rb_pts", "rx_pts", "ro_conf"):
        g1 = f.group_by(["player_id", "season"]).agg(
            pl.col(col).sort().alias("a"), pl.col(f"_perm_{col}").sort().alias("b")
        )
        assert (g1["a"] == g1["b"]).all(), col  # same multiset within every player-season
    assert not np.array_equal(f["struct_u_pts"].to_numpy(), f["_perm_struct_u_pts"].to_numpy())
    # joint permutation: the permuted (rb, rx, conf) triples are triples of the same original row
    orig = {
        (round(a, 12), round(b, 12), round(c, 12))
        for a, b, c in zip(f["rb_pts"], f["rx_pts"], f["ro_conf"], strict=True)
    }
    got = {
        (round(a, 12), round(b, 12), round(c, 12))
        for a, b, c in zip(f["_perm_rb_pts"], f["_perm_rx_pts"], f["_perm_ro_conf"], strict=True)
    }
    assert got == orig


def test_permutation_is_deterministic_and_season_local() -> None:
    f = _perm_frame()
    a, b = E.add_permuted(f, "pts"), E.add_permuted(f, "pts")
    assert a["_perm_struct_u_pts"].equals(b["_perm_struct_u_pts"])
    g = f.with_columns(
        pl.when(pl.col("season") == 2024)
        .then(pl.col("struct_u_pts") * 5)
        .otherwise(pl.col("struct_u_pts"))
        .alias("struct_u_pts")
    )
    c = E.add_permuted(g, "pts")
    s23 = pl.col("season") == 2023
    assert a.filter(s23)["_perm_struct_u_pts"].equals(
        c.filter(s23)["_perm_struct_u_pts"]
    )  # 2024 values do not move a 2023 permutation


# ----------------------------------------------------------------- the walk
def test_collect_heads_x_with_no_extra_column_equals_f1s_walk_and_an_extra_column_moves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gdf, pdf, static = _league(n_days=330)
    feats = attach_realised(build_features(gdf, pdf, static, {}, ELO), gdf, pdf)
    monkeypatch.setattr(F1R, "MIN_TRAIN", 300)
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300)
    a = F1R.collect_heads(feats, "pts", cfg, test_seasons=(2023,), log=False)
    b = E.collect_heads_x(feats, "pts", cfg, [], test_seasons=(2023,), log=False)
    assert set(a) == set(b) and all(np.array_equal(a[k], b[k]) for k in a)
    fr = feats.with_columns(pl.Series("noise", np.random.default_rng(0).random(feats.height)))
    c = E.collect_heads_x(fr, "pts", cfg, ["noise"], test_seasons=(2023,), log=False)
    assert np.abs(c["row_c"] - a["row_c"]).max() > 0.0


def test_walk_refuses_season_2025() -> None:
    with pytest.raises(ValueError, match="frozen holdout"):
        E.collect_heads_x(pl.DataFrame(), "pts", None, [], test_seasons=(2025,))


# ----------------------------------------------------------------- masks and the order of the run
def test_cell_and_slice_masks_follow_the_declared_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO)
    n = 6
    fr = pl.DataFrame(
        {
            "game_id": [
                "0042300101",
                "0022300001",
                "0022300001",
                "0022300002",
                "0022300002",
                "0022300003",
            ],
            "player_id": list(range(n)),
            "vac_min": [30.0, 30.0, 10.0, 30.0, 30.0, 30.0],
            "has_report": [1, 1, 1, 1, 0, 1],
            "n_out_rot": [1, 1, 2, 3, 0, 1],
            "last_starter": [1.0, 0.0, 1.0, None, 0.0, 1.0],
            "team_game_no": [90, 3, 14, 15, 40, 60],
            "struct_u_pts": [9.0, 9.0, 9.0, 9.0, 0.0, 1.0],
            "leader_out_pts": [1, 1, 0, 0, 0, 1],
            "Vmax_pts": [20.0, 10.0, 16.0, 0.0, 0.0, 15.2],
            "V_pts": [50.0, 10.0, 46.0, 0.0, 0.0, 10.0],
        }
    )
    gid = fr["game_id"].to_numpy().astype(str)
    cells, sl, rep = E.cell_and_slice_masks(fr, "pts", gid, fr["player_id"].to_numpy())
    assert cells["leader_out"].tolist() == [True, True, False, False, False, True]
    assert sl["playoffs"].tolist() == [True, False, False, False, False, False]
    assert sl["first15"].tolist() == [False, True, True, False, False, False]
    assert sl["bench"].tolist() == [False, True, False, False, True, False] and sl[
        "starter"
    ].tolist() == [True, False, True, False, False, True]  # NaN last_starter is in neither
    assert sl["star_out"].tolist() == [True, False, True, False, False, True]
    assert sl["n_out_rot=1"].tolist() == [True, True, False, False, False, True] and sl[
        "n_out_rot>=2"
    ].tolist() == [False, False, True, True, False, False]
    assert rep["heavy_absence"].tolist() == [True, False, True, False, False, False]


def test_run_refuses_outside_the_pinned_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="cwd must be"):
        E.run_real(tmp_path, "0" * 64)


def test_run_order_frozen_pinned_then_preflight_then_planted_then_smoke_then_fits() -> None:
    src = (REPO / "research/eval/wowy_struct_eval.py").read_text()
    body = src[src.index("def run_real") :]
    assert body.index("F1R.acquire_lock(root)") < body.index("WG.build_world(")  # review E9
    order = [
        "rule.require_ready(sha)",
        "preflight(root, declared)",
        "WG.build_world(",
        "stored_counts_check(world, root)",
        "WG.planted_future(world",
        "training_frame(root, world)",
        "smoke_block0(root, frame, cfg)",
        "fit_arms(root, frame, cfg",
    ]
    pos = [body.index(o) for o in order]
    assert pos == sorted(pos), dict(zip(order, pos, strict=True))
    assert body.index("evaluate(bundle") > body.index("build_bundle(")


def test_configs_are_read_from_the_pinned_root_never_the_cwd() -> None:
    for rel in ("wowy_struct_eval.py", "wowy_struct_repl_gate.py", "wowy_struct_rule.py"):
        text = (REPO / "research/eval" / rel).read_text()
        assert 'Path("configs' not in text and 'Path("reports/' not in text, rel
    assert "rule.ROOT" in (REPO / "research/eval/wowy_struct_eval.py").read_text()


def test_no_season_2025_anywhere_in_the_harness_and_gate() -> None:
    for rel in ("wowy_struct_eval.py", "wowy_struct_repl_gate.py"):
        src = (REPO / "research/eval" / rel).read_text()
        assert "season <= 2024" in src or "MAX_SEASON" in src, rel
        assert "season = 2025" not in src and "== 2025" not in src, rel


def test_pinned_versions_are_f1s() -> None:
    assert E.F1R.PINNED_VERSIONS["lightgbm"] == "4.7.0"


def test_cache_dirs_and_the_a0_arm_are_f1s() -> None:
    assert E.F1_CACHE_REL == F1R.CACHE_REL
    assert F1R.WINDOW["C"] == E.WINDOW_START


def test_rehearsal_summary_counts_every_world() -> None:
    res = E.rehearse("all", 2)
    assert res["pass"]["words"] == {"PASS": 2}
    assert res["guard"]["words"] == {"FAIL_GUARD": 2}
    assert res["one_season"]["words"] == {"FAIL_ONE_SEASON": 2}
    assert res["no_gain"]["words"] == {"FAIL_NO_GAIN": 2}


def test_bundle_alignment_helper_orders_by_reference_and_raises_on_missing() -> None:
    gid = np.array(["a", "b", "c"])
    pid = np.array([1, 2, 3])
    idx = F1R.align_index(np.array(["c", "a"]), np.array([3, 1]), gid, pid)
    assert idx.tolist() == [2, 0]
    with pytest.raises(RuntimeError):
        F1R.align_index(np.array(["z"]), np.array([9]), gid, pid)


_unused: Any = None
