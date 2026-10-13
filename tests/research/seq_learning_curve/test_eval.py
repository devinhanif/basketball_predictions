# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return,call-overload"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE scorer on SYNTHETIC data only (no real 2023-24 window is scored by any test): the shared bootstrap equals the repo's clustered CI, denormalization happens before the
integer grid, the verdict logic fails each way, the world is restricted to 2023-24 at load, the review refusal comes before any data is touched, and the whole curve wires end to end."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from nba.props.context_residual import to_integer_support
from nba.props.metrics import paired_score_delta_ci
from research.eval import ensemble_eval as EE
from research.eval import seq_full_mix_eval as S
from research.eval import seq_learning_curve_eval as E

TAU199 = np.arange(1, 200) / 200.0
TAU19 = np.arange(1, 20) / 20.0


# ------------------------------------------------------------------ bootstrap
def test_shared_boot_equals_the_repo_clustered_ci_and_reuses_one_set_of_draws() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.array([f"g{i:03d}" for i in range(120)]), 9)
    v = rng.normal(-0.01, 0.2, len(gid)) + np.repeat(rng.normal(0, 0.1, 120), 9)
    boot = E.SharedBoot(gid)
    c = boot.ci(v)
    ref = paired_score_delta_ci(v, np.zeros_like(v), n_boot=2000, seed=0, cluster_ids=gid)
    assert (
        c["point"] == pytest.approx(ref.point)
        and c["lo"] == pytest.approx(ref.lo, abs=1e-12)
        and c["hi"] == pytest.approx(ref.hi, abs=1e-12)
    )
    assert boot.idx.shape == (2000, 120)
    w = rng.normal(size=len(gid))
    d = (
        boot.means(v) - boot.means(w)
    )  # a contrast of means is the mean of the contrast on the SAME draws (linearity needs shared draws)
    assert np.allclose(d, boot.means(v - w))
    assert boot.ci(v, np.ones(len(v), dtype=bool))["lo"] == pytest.approx(c["lo"])
    sl = np.arange(len(v)) % 2 == 0
    cs = boot.ci(v, sl)
    assert cs["n"] == int(sl.sum()) and cs["n_games"] == 120 and cs["lo"] < cs["point"] < cs["hi"]


# ------------------------------------------------------------------ denormalization
def test_denormalize_divides_before_the_grid_and_roundtrips() -> None:
    rng = np.random.default_rng(1)
    raw_q = np.sort(rng.gamma(3.0, 2.0, (50, 4, 19)), axis=2)
    c = rng.uniform(0.9, 1.15, (50, 4))
    m, q = E.denormalize(np.ones((50, 4)) * c, raw_q * c[:, :, None], c)
    assert np.allclose(q, raw_q, atol=1e-12) and np.allclose(m, 1.0)
    z = {
        "game_id": np.array([f"g{i}" for i in range(50)]),
        "player_id": np.arange(50),
        "season": np.full(50, 2023),
        "q": (raw_q * c[:, :, None]).astype(np.float32),
    }
    base = {
        "game_id": z["game_id"][::-1],
        "player_id": z["player_id"][::-1],
    }  # A0 rows in another order
    g = E.seq_grid_from_oof(z, base, 1, c[::-1, 1])
    assert np.allclose(g, EE.ext19(raw_q[::-1, 1, :]), atol=1e-4)  # float32 storage
    with pytest.raises(RuntimeError, match="season other than"):
        E.seq_grid_from_oof({**z, "season": np.full(50, 2024)}, base, 0, None)
    with pytest.raises(RuntimeError, match="missing"):
        E.seq_grid_from_oof(z, {"game_id": np.array(["nope"]), "player_id": np.array([1])}, 0, None)


# ------------------------------------------------------------------ the verdict
def ci(p: float, w: float = 0.002) -> dict[str, float]:
    return {"point": p, "lo": p - w, "hi": p + w}


def _case(**kw):
    base = {
        "A": ci(-0.005), "B": ci(-0.003), "A_rep": -0.004, "B_rep": -0.002, "NR4": ci(0.0),
        "gates": {"repro_ok": True, "runtime_ok": True, "budget_ok": True}, "unfinished": [],
    }  # fmt: skip
    base.update(kw)
    return E.decide(base)


def test_decision_rule_fails_and_passes_each_way() -> None:
    assert _case()["verdict"] == "RISING"
    assert _case(A=ci(-0.0029, 0.0005))["verdict"] == "NOT RISING (closed)"  # misses the size floor
    assert _case(A=ci(-0.004, 0.0045))["verdict"] == "NOT RISING (closed)"  # CI upper >= 0
    assert (
        _case(B=ci(-0.001, 0.0005))["verdict"] == "NOT RISING (closed)"
    )  # still falling at the end fails
    assert _case(B=ci(-0.003, 0.0035))["verdict"] == "NOT RISING (closed)"
    assert (
        _case(A_rep=-0.001)["verdict"] == "INCONCLUSIVE (replicate guard)"
    )  # (i)+(ii) pass, (iii) fails: the rule names no verdict
    assert _case(B_rep=0.0001)["verdict"] == "INCONCLUSIVE (replicate guard)"
    assert (
        _case(A=ci(0.002, 0.001))["verdict"] == "HURTS"
        and _case(A=ci(0.002, 0.003))["verdict"] == "NOT RISING (closed)"
    )
    assert (
        _case(NR4=ci(0.0035, 0.001))["verdict"] == "INCONCLUSIVE (construction)"
        and _case(NR4=ci(0.0035, 0.005))["verdict"] == "RISING"
    )
    assert (
        _case(gates={"repro_ok": False, "runtime_ok": True, "budget_ok": True})["verdict"]
        == "VOID (gate)"
    )
    assert (
        _case(gates={"repro_ok": True, "runtime_ok": False, "budget_ok": True})["verdict"]
        == "VOID (gate)"
    )
    assert (
        _case(gates={"repro_ok": True, "runtime_ok": True, "budget_ok": False})["verdict"]
        == "INVALID (budget)"
    )
    assert _case(unfinished=["NORM_8"])["verdict"] == "INCONCLUSIVE (unfinished)"
    r = _case()
    assert r["criteria"] == {"i": True, "ii": True, "iii": True, "iv_gates": True, "v_budget": True}


def test_reproduction_gate_and_budget_diagnostic() -> None:
    ok = E.repro_gate(-0.0083 + 0.0029, -0.0203 - 0.0029, -0.0083501, -0.0083501)
    assert ok["ok"]
    assert not E.repro_gate(-0.0083 + 0.0031, -0.0203, -0.0083501, -0.0083501)["ok"]
    assert not E.repro_gate(-0.0083, -0.0203 + 0.0031, -0.0083501, -0.0083501)["ok"]
    assert not E.repro_gate(-0.0083, -0.0203, -0.0083501 + 2e-6, -0.0083501)[
        "ok"
    ]  # the stored A0 / FULL grids must reproduce to 1e-6

    def cold(reached: bool, n: int = 3) -> list[dict]:
        return [
            {
                "epochs_run": 25 if reached else 12,
                "best_epoch": 22 if reached else 8,
                "best_val": 1.0,
                "reached_cap": False,  # the job's own flag is ignored: the scorer recomputes it
                "seed": i,
            }
            for i in range(n)
        ]

    infos = [{"arm": f"NORM_{n}", "cold": cold(False)} for n in range(1, 8)] + [
        {"arm": "NORM_8", "cold": cold(True)}
    ]
    g = E.budget_gate(infos)
    assert (
        g["cold_fits"] == 24
        and g["reached_cap"] == 3
        and g["ok"]
        and g["share"] == pytest.approx(3 / 24)
    )
    infos2 = [
        {"arm": f"NORM_{n}", "cold": cold(n >= 5)} for n in range(1, 9)
    ]  # 12 of 24 reach the cap
    assert not E.budget_gate(infos2)["ok"]
    exactly_third = [{"arm": "NORM_1", "cold": cold(True, 1) + cold(False, 2)}]
    assert E.budget_gate(exactly_third)[
        "ok"
    ]  # "more than one third" fails; exactly one third passes


def test_reached_the_cap_means_25_epochs_and_the_best_epoch_in_the_last_four() -> None:
    f = E.fit_reached_cap
    assert f({"epochs_run": 25, "best_epoch": 21}) and f({"epochs_run": 25, "best_epoch": 24})
    assert not f({"epochs_run": 25, "best_epoch": 20})  # patience fired on the 25th epoch
    assert not f({"epochs_run": 24, "best_epoch": 23}) and not f(
        {"epochs_run": 12, "best_epoch": 11}
    )
    infos = [
        {"arm": "NORM_4", "cold": [{"epochs_run": 25, "best_epoch": 22}] * 3},
        {"arm": "NORM_6", "cold": [{"epochs_run": 25, "best_epoch": 20}] * 3},
    ]
    g = E.budget_gate(infos)
    assert g["reached_cap"] == 3 and g["by_arm"]["NORM_6"]["reached_cap"] == 0
    assert g["arms_with_every_fit_at_cap"] == ["NORM_4"] and g["share_norm_only"] == pytest.approx(
        0.5
    )


def test_restrict_world_drops_every_other_season_at_load() -> None:
    n = 10
    season = np.array([2023] * 6 + [2024] * 4)
    b = {
        "y": np.arange(n, dtype=float),
        "season": season,
        "game_id": np.array([f"002{i}" for i in range(n)]),
        "starter10": np.ones(n),
        "n_prior": np.ones(n),
        "n_rep_out": np.zeros(n),
        "has_report": np.ones(n),
        "team_game_no": np.arange(n),
        "const": 7,
    }
    w = {
        "base": {st: dict(b) for st in E.STATS},
        "date_days": np.arange(n),
        "seq": "stored",
        "g": np.zeros(n),
    }
    r = E.restrict_world(w)
    assert (
        set(r) == {"base", "date_days", "ctx"}
        and len(r["date_days"]) == 6
        and (r["base"]["pts"]["season"] == 2023).all()
        and r["base"]["pts"]["y"].max() == 5.0
        and r["base"]["pts"]["const"] == 7
    )
    bad = {
        "base": {st: {**b, "season": np.full(n, 2024)} for st in E.STATS},
        "date_days": np.arange(n),
    }
    with pytest.raises((RuntimeError, ValueError)):
        E.restrict_world(bad)


def test_run_refuses_before_touching_any_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked(*a, **k):
        raise RuntimeError("no usable loader review")

    def touched(*a, **k):
        raise AssertionError("data was touched before the review check")

    monkeypatch.setattr(E.RULE, "require_review", blocked)
    for name in ("load_run", "load_world_2023", "check_run"):
        monkeypatch.setattr(E, name, touched)
    with pytest.raises(RuntimeError, match="loader review"):
        E.run(tmp_path)
    with pytest.raises(RuntimeError, match="loader review"):
        E.main(["check"])


# ------------------------------------------------------------------ end to end on a synthetic world
def _world(n_games: int = 430, per: int = 10, seed: int = 0):
    rng = np.random.default_rng(seed)
    n = n_games * per
    gid = np.repeat(np.array([f"002{23:02d}{i:05d}" for i in range(n_games)]), per)
    pid = np.tile(np.arange(1, per + 1), n_games) + 1000
    day0 = np.datetime64("2023-10-25").astype("datetime64[D]").astype(np.int64)
    days = (day0 + np.repeat(np.sort(rng.integers(0, 170, n_games)), per)).astype(np.int64)
    mu = rng.gamma(4.0, 1.5, (n, 4))
    y = rng.poisson(mu).astype(float)

    def grid(m, jitter):
        return (
            poisson.ppf(TAU199[None, :], m[:, None])
            + jitter * rng.uniform(0, 1, (len(m), 199)).cumsum(axis=1) / 199
        )

    base = {}
    for si, st in enumerate(E.STATS):
        mix = grid(mu[:, si], 0.5)
        base[st] = {
            "y": y[:, si], "game_id": gid, "player_id": pid, "season": np.full(n, 2023), "mix": mix, "a0_int": to_integer_support(mix).astype(np.int16),
            "full_int": poisson.ppf(TAU199[None, :], (mu[:, si] * 1.03)[:, None]).astype(np.int16), "minutes": rng.uniform(0, 36, n), "starter10": rng.random(n),
            "n_prior": rng.integers(0, 80, n), "has_report": (rng.random(n) < 0.9).astype(float), "team_game_no": rng.integers(0, 82, n), "n_rep_out": rng.integers(0, 3, n),
        }  # fmt: skip
    w = {"base": base, "date_days": days, "ctx": S.blend_ctx(base["pts"])}
    return w, mu


def _oof(w, mu, c, noise, rng):
    n = len(w["date_days"])
    q = np.stack(
        [
            poisson.ppf(
                TAU19[None, :],
                (mu[:, si] * (1 + noise * rng.normal(size=n)).clip(0.5, 1.5))[:, None],
            )
            for si in range(4)
        ],
        axis=1,
    )
    q = q + 0.3 * TAU19[None, None, :]
    perm = rng.permutation(n)  # the OOF file is in another row order than the A0 world
    return {
        "game_id": w["base"]["pts"]["game_id"][perm], "player_id": w["base"]["pts"]["player_id"][perm], "season": np.full(n, 2023),
        "q": (q * c[:, :, None])[perm].astype(np.float32),
    }, q  # fmt: skip


def test_curve_wires_end_to_end_on_a_synthetic_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(E, "CACHE", tmp_path / "cache")
    w, mu = _world()
    rng = np.random.default_rng(5)
    n = len(w["date_days"])
    c = rng.uniform(0.92, 1.12, (n, 4))
    meta = pd.DataFrame(
        {
            "game_id": w["base"]["pts"]["game_id"],
            "player_id": w["base"]["pts"]["player_id"],
            **{f"norm_c_{st}": c[:, i] for i, st in enumerate(E.STATS)},
        }
    )
    assert E.check_world_rows(w, meta)["ok"]
    base_crps = {
        st: {
            "A0": E.E_crps(w["base"][st]["a0_int"], w["base"][st]["y"]),
            "FULL": E.E_crps(w["base"][st]["full_int"], w["base"][st]["y"]),
        }
        for st in E.STATS
    }
    arm_crps = {}
    zs = {}
    for k, arm in enumerate(E.ALL_ARMS):
        z, q_raw = _oof(
            w,
            mu,
            c if not arm.startswith("RAW") else np.ones_like(c),
            0.15 - 0.01 * k,
            np.random.default_rng(100 + k),
        )
        zs[arm] = (z, q_raw)
        for st in E.STATS if arm.startswith("NORM") else ("pts",):
            arm_crps[(arm, st)] = E.score_arm_stat(arm, st, w, z, meta, "t")
    assert (E.CACHE / "NORM_4__pts__t.npz").exists()
    # resumable: the cached result is returned without recomputation
    again = E.score_arm_stat("NORM_4", "pts", w, None, meta, "t")
    assert np.array_equal(again["crps_avg"], arm_crps[("NORM_4", "pts")]["crps_avg"])
    # normalization is undone before scoring: a NORM arm whose OOF is raw_q * c scores exactly like a RAW arm fed raw_q
    z_norm, q_raw = zs["NORM_4"]
    order = pd.MultiIndex.from_arrays([z_norm["game_id"], z_norm["player_id"]])
    pos = pd.MultiIndex.from_arrays(
        [w["base"]["pts"]["game_id"], w["base"]["pts"]["player_id"]]
    ).get_indexer(order)
    z_raw_equiv = {**z_norm, "q": q_raw[pos].astype(np.float32)}
    chk = E.score_arm_stat("RAW_4", "pts", w, z_raw_equiv, meta, "equiv")
    assert np.allclose(chk["crps_avg"], arm_crps[("NORM_4", "pts")]["crps_avg"], atol=1e-4)
    gates = {"repro_ok": True, "runtime_ok": True, "budget_ok": True}
    res = E.assemble(base_crps, arm_crps, w, gates, [])
    assert res["n_rows"] == n and res["n_games"] == 430
    assert set(res["norm"]["steps"]) == {f"{k}-{k - 1}" for k in range(2, 9)} and {
        "point",
        "lo",
        "hi",
    } <= set(res["norm"]["A"])
    assert res["norm"]["A"]["point"] == pytest.approx(res["norm"]["C"]["8"] - res["norm"]["C"]["4"])
    assert res["norm"]["B"]["point"] == pytest.approx(res["norm"]["C"]["8"] - res["norm"]["C"]["6"])
    assert len(res["secondary_family_S"]["rows"]) == 6 and all(
        "p_bh" in r for r in res["secondary_family_S"]["rows"]
    )
    assert set(res["slices"]) == {
        "Oct-Nov",
        "Dec-Jan",
        "Feb-Apr",
        "postseason",
        "starters",
        "bench",
        "cold_start",
        "minutes<5",
        "no_report",
    }
    assert all(len(v) == 19 for v in res["pts_coverage_seqc"].values()) and set(
        res["pts_coverage_seqc"]
    ) == set(E.ALL_ARMS)
    assert set(res["seqc_minus_a0"]) == set(E.STATS) and set(
        res["replicates"]["main_minus_rep"]
    ) == {"4", "6", "8"}
    assert res["decision"]["verdict"] in {
        "RISING",
        "NOT RISING (closed)",
        "HURTS",
        "INCONCLUSIVE (construction)",
        "INCONCLUSIVE (replicate guard)",
    }
    json.dumps(res, default=float)
    txt = E.render(
        {
            "rule_sha256": "a" * 64,
            "verdict": res["decision"]["verdict"],
            "gates": gates,
            "unfinished": [],
            "curve": res,
        }
    )
    assert "Verdict" in txt and "A = C(8) - C(4)" in txt


def _rv(tmp: Path) -> tuple[dict, dict, dict]:
    """A run directory's worth of consistent inputs: exports on disk, a review result naming them, a config and one info per arm."""
    for n in E.RULE.EXPORT_NAMES:
        (tmp / n).write_bytes(n.encode())
    exp = {n: E.RULE.file_sha256(tmp / n) for n in E.RULE.EXPORT_NAMES}
    rv = {"review_commit": "c" * 40, "exports_manifest": E.RULE.manifest_sha(exp)}
    sha = "a" * 64
    cfg = {
        "rule_sha256": sha, "cap_season": 2023, "test_label": 2023, "review_commit": rv["review_commit"], "exports": exp, "hidden": 64, "smoke": None,
        "cold": E.COLD_CFG, "warm": E.WARM_CFG, "seeds_main": [0, 1, 2], "seeds_rep": [3, 4, 5],
    }  # fmt: skip
    infos = {}
    for arm in E.ALL_ARMS:
        variant, seeds = E.expected_arm(arm)
        infos[arm] = {
            "arm": arm, "variant": variant, "hidden": 64, "cold_cfg": E.COLD_CFG, "warm_cfg": E.WARM_CFG, "seeds": seeds, "rule_sha256": sha, "n_test": 28197, "smoke": False,
            "exports": exp, "review_commit": rv["review_commit"], "test_keys_sha256": "k", "test_inputs_sha256": variant,
            "cold": [{"seed": s, "epochs_run": 12, "best_epoch": 8} for s in seeds],
        }  # fmt: skip
    return cfg, infos, rv


def test_check_run_flags_each_problem(tmp_path: Path) -> None:
    cfg, infos, rv = _rv(tmp_path)
    sha = "a" * 64

    def chk(cfg_=cfg, **changes):
        arms = {a: {**i, **changes.get(a, {})} for a, i in infos.items()}
        return E.check_run(cfg_, list(arms.values()), sha, tmp_path, rv)

    assert chk()["ok"]
    assert not E.check_run(
        {**cfg, "rule_sha256": "b" * 64}, list(infos.values()), sha, tmp_path, rv
    )["ok"]
    assert not chk(
        {**cfg, "exports": {**cfg["exports"], "seq_learning_curve_raw_S3.npz": "0" * 64}}
    )["ok"]
    assert not chk({**cfg, "cap_season": 2024})["ok"]
    assert not chk({**cfg, "review_commit": "d" * 40})["ok"]
    assert not chk({**cfg, "hidden": 16})["ok"] and not chk({**cfg, "smoke": "4000"})["ok"]
    assert not chk({**cfg, "cold": {**E.COLD_CFG, "epochs": 30}})["ok"]
    assert not chk({**cfg, "seeds_rep": [0, 1, 2]})["ok"]
    assert not chk({**cfg, "exports": dict(list(cfg["exports"].items())[:5])})[
        "ok"
    ]  # fewer than the six reviewed exports
    # per-arm configuration drift (a restart under a different environment)
    drifts: list[dict] = [
        {"hidden": 16}, {"cold_cfg": {**E.COLD_CFG, "patience": 9}}, {"warm_cfg": {**E.WARM_CFG, "epochs": 1}}, {"seeds": [0, 1, 5]}, {"rule_sha256": "b" * 64},
        {"n_test": 28000}, {"smoke": True}, {"variant": "raw"}, {"exports": {}}, {"review_commit": "d" * 40}, {"test_keys_sha256": "other"},
    ]  # fmt: skip
    for bad in drifts:
        r = chk(**{"NORM_3": bad})
        assert not r["ok"], bad
    assert not chk(**{"NREP_4": {"seeds": [0, 1, 2], "cold": [{"seed": s} for s in (0, 1, 2)]}})[
        "ok"
    ]  # the replicate set must use seeds 3-5
    assert not chk(**{"RAW_4": {"test_inputs_sha256": "different"}})["ok"]


# ------------------------------------------------------------------ gate first, curve last (F2 / F5)
def _score_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drop=(),
    repro_ok=True,
    runtime_ok=True,
    budget_ok=True,
):
    monkeypatch.setattr(E, "CACHE", tmp_path / "cache")
    w, mu = _world()
    rng = np.random.default_rng(5)
    n = len(w["date_days"])
    c = rng.uniform(0.92, 1.12, (n, 4))
    meta = pd.DataFrame({"game_id": w["base"]["pts"]["game_id"], "player_id": w["base"]["pts"]["player_id"], **{f"norm_c_{st}": c[:, i] for i, st in enumerate(E.STATS)}})  # fmt: skip
    zs = {
        a: _oof(
            w,
            mu,
            c if not a.startswith("RAW") else np.ones_like(c),
            0.15 - 0.01 * k,
            np.random.default_rng(100 + k),
        )[0]
        for k, a in enumerate(E.ALL_ARMS)
    }
    loaded: list[str] = []

    def load_oof(arm: str):
        loaded.append(arm)
        return zs[arm], "t"

    monkeypatch.setattr(E, "repro_gate", lambda *a, **k: {"checks": {}, "ok": repro_ok})
    done = [a for a in E.ALL_ARMS if a not in drop]
    cold = [
        {"epochs_run": 25 if not budget_ok else 12, "best_epoch": 22 if not budget_ok else 8}
    ] * 3
    infos = [{"arm": a, "cold": cold} for a in done]
    out = {
        "run_check": {"ok": True, "arms_done": done, "problems": []},
        "budget": E.budget_gate(infos),
        "unfinished": [a for a in E.ALL_ARMS if a in drop],
    }
    return out, infos, runtime_ok, w, meta, load_oof, loaded


def _norm_cache(tmp_path: Path) -> list[str]:
    d = tmp_path / "cache"
    return sorted(p.name for p in d.glob("N*")) if d.exists() else []


def test_a_void_run_scores_only_the_two_reproduction_arms_and_writes_no_norm_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, infos, rt, w, meta, load, loaded = _score_inputs(tmp_path, monkeypatch, repro_ok=False)
    E.score_run(out, infos, rt, w, meta, load)
    assert (
        out["curve"]["decision"]["verdict"] == "VOID (gate)" and out["gates"]["repro_ok"] is False
    )
    assert sorted(loaded) == ["RAW_1", "RAW_4"] and _norm_cache(tmp_path) == []
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == [
        "RAW_1__pts__t.npz",
        "RAW_4__pts__t.npz",
    ]
    assert set(out["curve"]) == {"decision"}  # no curve, no per-arm figure


def test_a_runtime_gate_failure_scores_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, infos, _, w, meta, load, loaded = _score_inputs(tmp_path, monkeypatch)
    E.score_run(out, infos, False, w, meta, load)
    assert (
        out["curve"]["decision"]["verdict"] == "VOID (gate)"
        and loaded == []
        and not (tmp_path / "cache").exists()
    )


def test_unfinished_and_budget_runs_stop_before_any_norm_arm_and_a_missing_raw_arm_is_not_void(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, infos, rt, w, meta, load, loaded = _score_inputs(
        tmp_path, monkeypatch, drop=("NORM_8", "NREP_8")
    )
    E.score_run(out, infos, rt, w, meta, load)
    assert (
        out["curve"]["decision"]["verdict"] == "INCONCLUSIVE (unfinished)"
        and sorted(loaded) == ["RAW_1", "RAW_4"]
        and _norm_cache(tmp_path) == []
    )
    # F5: RAW_1 missing -> INCONCLUSIVE (unfinished), not VOID, and nothing is scored
    sub = tmp_path / "b"
    sub.mkdir()
    out, infos, rt, w, meta, load, loaded = _score_inputs(sub, monkeypatch, drop=("RAW_1",))
    E.score_run(out, infos, rt, w, meta, load)
    assert (
        out["curve"]["decision"]["verdict"] == "INCONCLUSIVE (unfinished)"
        and loaded == []
        and _norm_cache(sub) == []
    )
    sub = tmp_path / "c"
    sub.mkdir()
    out, infos, rt, w, meta, load, loaded = _score_inputs(sub, monkeypatch, budget_ok=False)
    E.score_run(out, infos, rt, w, meta, load)
    assert (
        out["curve"]["decision"]["verdict"] == "INVALID (budget)"
        and sorted(loaded) == ["RAW_1", "RAW_4"]
        and _norm_cache(sub) == []
    )


def test_when_every_gate_passes_the_whole_curve_is_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, infos, rt, w, meta, load, loaded = _score_inputs(tmp_path, monkeypatch)
    E.score_run(out, infos, rt, w, meta, load)
    assert out["gates"] == {"repro_ok": True, "runtime_ok": True, "budget_ok": True}
    assert "norm" in out["curve"] and out["curve"]["decision"]["verdict"] in {
        "RISING",
        "NOT RISING (closed)",
        "HURTS",
        "INCONCLUSIVE (construction)",
        "INCONCLUSIVE (replicate guard)",
    }
    assert (
        set(loaded) == set(E.ALL_ARMS) and len(_norm_cache(tmp_path)) == 8 * 4 + 3
    )  # NORM_1..8 x 4 stats, NREP x pts
    r = out["curve"]["replicates"]
    assert (
        "main_minus_rep_sd_across_N" in r
        and "seed_sd_single_ensemble_estimate" in r
        and "seed_noise_sd_of_differences" not in r
    )


def test_oof_files_are_read_without_pickles() -> None:
    import inspect

    src = inspect.getsource(E.run)
    assert "allow_pickle=True" not in src and src.count("allow_pickle=False") >= 2
