# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return,call-overload,var-annotated"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE_2 scorer (docs/prereg/SEQ_LEARNING_CURVE_2.md): decision words at every threshold, reproduction / fallback / budget statuses, the seed-pool tables and the two-way
draws against a brute-force bootstrap, the nothing-scored-before-the-gates order, and a tiny end-to-end run (exporter fixture -> synthetic seed quantiles and A0 / FULL world -> verdicts)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from research.eval import ensemble_eval as E
from research.eval import seq_full_mix_eval as S
from research.eval import seq_learning_curve_2_eval as V
from research.eval import seq_learning_curve_eval as P1
from research.features import seq_learning_curve_2_export as X2

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def small_blend_gate(monkeypatch):
    """The walk-forward blend needs 3000 training rows in production; the fixture world has a few hundred, so the minimum is lowered for these tests only."""
    monkeypatch.setattr(S.N, "GATE_MIN_ROWS", 100)


def ci(point: float, lo: float, hi: float) -> dict[str, float]:
    return {"point": point, "lo": lo, "hi": hi}


# ------------------------------------------------------------------ the decision words, every branch and boundary
@pytest.mark.parametrize(
    "am,a,word",
    [
        (ci(-0.0030, -0.006, -0.0001), ci(-0.006, 0, 0), "DATA"),  # exactly at the floor, upper < 0
        (ci(-0.0040, -0.008, 0.0000), ci(-0.006, 0, 0), "MIXED"),  # upper = 0 is not < 0
        (ci(-0.0020, -0.0029, 0.0010), ci(-0.006, 0, 0), "MIXED"),  # between -0.0015 and -0.003
        (
            ci(-0.0010, -0.0029, 0.0010),
            ci(-0.006, 0, 0),
            "STEPS",
        ),  # lower > -0.003, A <= -0.003, A_m >= -0.0015
        (ci(-0.0015, -0.0029, 0.0010), ci(-0.003, 0, 0), "STEPS"),  # boundaries inclusive
        (
            ci(-0.0010, -0.0029, 0.0010),
            ci(-0.0029, 0, 0),
            "MIXED",
        ),  # the unmatched gain did not reproduce
        (ci(-0.0010, -0.0031, 0.0010), ci(-0.006, 0, 0), "MIXED"),  # CI straddles the floor
        (ci(0.0010, -0.001, 0.003), ci(-0.006, 0, 0), "STEPS"),  # A_m >= -0.0015 includes positive
    ],
)
def test_decide_A(am, a, word) -> None:
    assert V.decide_A(am, a) == word


@pytest.mark.parametrize(
    "e,a_word,word",
    [
        (ci(-0.0030, -0.006, -0.0001), "DATA", "RISING_10"),
        (ci(-0.0030, -0.006, -0.0001), "MIXED", "RISING_10"),
        (ci(-0.0030, -0.006, -0.0001), "STEPS", "AMBIGUOUS"),  # needs A_m DATA or MIXED
        (ci(-0.0030, -0.006, 0.0), "DATA", "AMBIGUOUS"),  # upper not < 0
        (ci(-0.0029, -0.006, -0.0001), "DATA", "AMBIGUOUS"),
        (ci(0.0, -0.0029, 0.003), "STEPS", "FLAT"),
        (
            ci(0.0015, -0.0029, 0.004),
            "DATA",
            "FLAT",
        ),  # inclusive at +0.0015 (rule lists FLAT first)
        (ci(-0.0015, -0.0029, 0.004), "DATA", "FLAT"),
        (ci(0.0, -0.0031, 0.003), "DATA", "AMBIGUOUS"),  # lower <= -0.003: flatness not bounded
        (ci(0.0016, 0.0001, 0.004), "DATA", "HURTS"),
        (ci(0.0016, 0.0, 0.004), "DATA", "AMBIGUOUS"),  # lower not > 0
        (ci(0.0010, -0.001, 0.003), "MIXED", "FLAT"),
        (ci(-0.002, -0.004, 0.0), "DATA", "AMBIGUOUS"),
    ],
)
def test_decide_E(e, a_word, word) -> None:
    assert V.decide_E(e, a_word) == word


def test_decide_combines_and_uses_the_unmatched_a_point() -> None:
    assert V.decide(ci(-0.004, -0.007, -0.001), ci(-0.006, 0, 0), ci(-0.004, -0.006, -0.001)) == {
        "A_m": "DATA",
        "E": "RISING_10",
    }
    assert V.decide(ci(-0.001, -0.002, 0.0), ci(-0.006, 0, 0), ci(0.0, -0.001, 0.001)) == {
        "A_m": "STEPS",
        "E": "FLAT",
    }


def test_decision_level_is_bonferroni_975_and_the_interval_uses_it() -> None:
    assert V.DECISION_LEVEL == 0.975
    d = np.linspace(-1.0, 1.0, 2001)
    s = V.summarize(d, 0.0, 10, 3)
    assert s["lo"] == pytest.approx(np.quantile(d, 0.0125)) and s["hi"] == pytest.approx(
        np.quantile(d, 0.9875)
    )
    assert s["lo95"] == pytest.approx(np.quantile(d, 0.025)) and s["hi95"] == pytest.approx(
        np.quantile(d, 0.975)
    )
    assert s["lo"] < s["lo95"] and s["hi"] > s["hi95"]


# ------------------------------------------------------------------ gates
def test_repro_and_gate_status_mapping() -> None:
    ok = V.repro_gate(-0.0201, -0.0263, -0.00835, -0.00835)
    assert ok["ok"] and V.gate_verdict(ok) is None
    edge = V.repro_gate(-0.0201 + 0.003, -0.0263 - 0.004, -0.00835, -0.00835)
    assert edge["ok"]  # bands inclusive
    u4 = V.repro_gate(-0.0201 + 0.0031, -0.0263, -0.00835, -0.00835)
    assert not u4["ok"] and V.gate_verdict(u4) == "INCONCLUSIVE (construction)"
    u8 = V.repro_gate(-0.0201, -0.0263 + 0.0041, -0.00835, -0.00835)
    assert V.gate_verdict(u8) == "VOID (gate)"
    grids = V.repro_gate(-0.0201, -0.0263, -0.00835, -0.00835 + 2e-6)
    assert V.gate_verdict(grids) == "VOID (gate)"
    both = V.repro_gate(-0.0201 + 0.01, -0.0263, -0.00835, -0.00835 + 1e-3)
    assert V.gate_verdict(both) == "VOID (gate)"  # a grid mismatch outranks the fallback


def _cold(n: int, seed: int, epochs: int, best: int) -> dict[str, Any]:
    return {"seed": seed, "epochs_run": epochs, "best_epoch": best, "window_n": n}


def test_budget_gate_counts_each_window_seed_once() -> None:
    cold4 = [_cold(4, s, 25, 24) for s in range(6)]  # all at the cap
    cold8 = [_cold(8, s, 12, 8) for s in range(6)]
    infos = [
        {"arm": "U_4", "window_n": 4, "cold": cold4},
        {"arm": "UP_4", "window_n": 4, "cold": cold4},  # shares U_4's cold fits: not double counted
        {"arm": "U_8", "window_n": 8, "cold": cold8},
        {"arm": "M_8", "window_n": 8, "cold": cold8},
    ]
    g = V.budget_gate(infos)
    assert g["cold_fits"] == 12 and g["reached_cap"] == 6 and g["share"] == 0.5 and not g["ok"]
    infos[0]["cold"] = infos[1]["cold"] = [
        _cold(4, s, 25, 20) for s in range(6)
    ]  # ran 25 epochs but patience, not the cap, ended the best epoch
    assert V.budget_gate(infos)["ok"]
    cold4b = [_cold(4, s, 25, 24) if s < 4 else _cold(4, s, 10, 5) for s in range(6)]
    infos[0]["cold"] = infos[1]["cold"] = cold4b
    g2 = V.budget_gate(infos)
    assert (
        g2["reached_cap"] == 4 and g2["share"] == pytest.approx(4 / 12) and g2["ok"]
    )  # exactly one third is allowed


# ------------------------------------------------------------------ seed pools and draws
def test_multiset_tables_and_draw_streams() -> None:
    assert len(V.MULTISETS) == 462 and V.COUNTS.shape == (468, 6)
    assert (V.COUNTS.sum(axis=1)[:462] == 6).all() and (V.COUNTS.sum(axis=1)[462:] == 5).all()
    assert (V.COUNTS[V.FULL_ROW] == 1).all()
    assert [V.COUNTS[r].argmin() for r in V.LOSO_ROWS] == list(range(6))
    a, b = V.seed_draw_rows("U_4"), V.seed_draw_rows("M_8")
    assert (
        a.shape == (2000,)
        and a.max() < 462
        and (a == V.seed_draw_rows("U_4")).all()
        and not (a == b).all()
    )  # own stream per arm, deterministic
    # a pool resampled i.i.d. from 6 seeds hits the full pool with probability 6!/6^6
    assert (a == V.FULL_ROW).mean() == pytest.approx(720 / 46656, abs=0.01)
    # the arm list is the job's
    tail = (
        ROOT / "research/colab/jobs/seq_learning_curve_2/seq_learning_curve_2_tail.py"
    ).read_text()
    units = next(
        ast.literal_eval(n.value)
        for n in ast.parse(tail).body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "UNITS"
    )
    assert tuple(a for _, arms in units for a in arms) == V.ARM_NAMES


def test_pooled_grid_is_the_job_ensemble_then_c_then_extension() -> None:
    rng = np.random.default_rng(0)
    sq = np.sort(rng.gamma(3, 2, (6, 40, 19)), axis=2) * rng.uniform(0.9, 1.1, (6, 40, 1))
    sq[:, 0, :3] = -1.0  # negative raw values are clipped after pooling
    c = rng.uniform(0.9, 1.1, 40)
    full = V.pooled_grid(sq, np.ones(6), c)
    manual = E.ext19(np.maximum.accumulate(np.clip(sq.mean(axis=0), 0, None), axis=1) / c[:, None])
    assert np.allclose(full, manual, atol=1e-12)
    cnt = np.array([2.0, 0, 1, 0, 3, 0])  # a multiset with multiplicities
    pool = (2 * sq[0] + sq[2] + 3 * sq[4]) / 6
    assert np.allclose(
        V.pooled_grid(sq, cnt, None),
        E.ext19(np.maximum.accumulate(np.clip(pool, 0, None), axis=1)),
        atol=1e-12,
    )
    # exchangeable: the order of the seeds in a pool is irrelevant
    perm = rng.permutation(6)
    assert np.allclose(V.pooled_grid(sq[perm], np.ones(6), c), full, atol=1e-12)


def test_two_way_draws_equal_a_brute_force_resample() -> None:
    rng = np.random.default_rng(3)
    n, g = 300, 25
    gid = np.repeat(np.arange(g), n // g)
    row_x = rng.gamma(2, 1, (len(V.COUNTS), n))
    row_y = rng.gamma(2, 1, (len(V.COUNTS), n))
    inv = np.unique(gid, return_inverse=True)[1]
    sums = lambda r: np.stack([np.bincount(inv, weights=v, minlength=g) for v in r])  # noqa: E731
    cnt = np.bincount(inv, minlength=g).astype(float)
    tx, ty = V.SeedTable(sums(row_x), cnt), V.SeedTable(sums(row_y), cnt)
    boot = P1.SharedBoot(gid, n_boot=40)
    rx, ry = V.seed_draw_rows("U_8", 40), V.seed_draw_rows("M_8", 40)
    got = V.two_way_draws(boot, tx, rx, ty, ry, chunk=7)
    for d in (0, 5, 39):
        games = boot.idx[d]
        rows = np.concatenate([np.flatnonzero(gid == k) for k in games])
        want = (row_x[rx[d]][rows] - row_y[ry[d]][rows]).mean()
        assert got[d] == pytest.approx(want, abs=1e-12)
    # a mask with no rows in a draw gives NaN, not zero
    empty = V.SeedTable(np.zeros_like(tx.sums), np.zeros(g))
    assert np.isnan(V.two_way_draws(boot, empty, rx, None, None)).all()


def test_contrast_point_loso_and_step_share_identity() -> None:
    rng = np.random.default_rng(4)
    g, n = 20, 200
    gid = np.repeat(np.arange(g), n // g)
    inv = np.unique(gid, return_inverse=True)[1]

    def mk() -> V.SeedTable:
        return V.SeedTable(
            np.stack(
                [np.bincount(inv, weights=rng.gamma(2, 1, n), minlength=g) for _ in range(468)]
            ),
            np.bincount(inv, minlength=g).astype(float),
        )

    u4, u8, m8 = mk(), mk(), mk()
    boot = P1.SharedBoot(gid, n_boot=300)
    a, am = V.contrast(boot, u8, "U_8", u4, "U_4", 300), V.contrast(boot, m8, "M_8", u4, "U_4", 300)
    share = V.contrast(boot, u8, "U_8", m8, "M_8", 300)
    assert a["point"] - am["point"] == pytest.approx(share["point"])
    assert len(a["loso"]["values"]) == 6 and a["loso"]["min"] <= a["point"] + 1.0
    # the same draws in both: A - A_m == U_8 - M_8 draw by draw (U_4's pool cancels)
    da = V.two_way_draws(boot, u8, V.seed_draw_rows("U_8", 300), u4, V.seed_draw_rows("U_4", 300))
    dm = V.two_way_draws(boot, m8, V.seed_draw_rows("M_8", 300), u4, V.seed_draw_rows("U_4", 300))
    ds = V.two_way_draws(boot, u8, V.seed_draw_rows("U_8", 300), m8, V.seed_draw_rows("M_8", 300))
    assert np.allclose(da - dm, ds)


# ------------------------------------------------------------------ a synthetic world on the exporter's fixture rows
STATS = V.STATS
TAUS = E.CRPS_TAUS


def _int_grid(mu: np.ndarray) -> np.ndarray:
    return sps.poisson.ppf(TAUS[None, :], mu[:, None]).astype(np.int16)


def make_world(
    game_id: np.ndarray, player_id: np.ndarray, seed: int = 0
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    n = len(game_id)
    gids = np.unique(game_id)
    day = {g: 19700 + 2 * i for i, g in enumerate(gids)}
    date_days = np.array([day[g] for g in game_id], dtype=np.int64)
    base: dict[str, Any] = {}
    mus: dict[str, np.ndarray] = {}
    for si, st in enumerate(STATS):
        mu = rng.uniform(1.5, 12.0, n) / (1 + si)
        mus[st] = mu
        y = rng.poisson(mu).astype(float)
        a0 = _int_grid(mu)
        full = _int_grid(mu * 1.03)
        base[st] = {
            "game_id": game_id.astype("U12"), "player_id": player_id.astype(np.int64), "season": np.full(n, 2023), "y": y, "n_prior": rng.integers(0, 50, n),
            "starter10": (rng.random(n) < 0.4).astype(float), "n_rep_out": np.zeros(n), "has_report": np.ones(n), "team_game_no": rng.integers(1, 82, n).astype(float),
            "minutes": rng.uniform(0, 40, n), "mix": a0.astype(float), "a0_int": a0, "full_int": full,
        }  # fmt: skip
    world = {"base": base, "date_days": date_days, "ctx": S.blend_ctx(base["pts"])}
    meta = pd.DataFrame(
        {
            "game_id": game_id,
            "player_id": player_id,
            "season": 2023,
            **{f"norm_c_{st}": 1.0 for st in STATS},
        }
    )
    return world, meta, mus


def make_oof(
    world: dict[str, Any], mus: dict[str, np.ndarray], seed: int, spread: float = 0.05
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(world["date_days"])
    sq = np.zeros((6, n, 4, 19))
    for si, st in enumerate(STATS):
        for s in range(6):
            sq[s, :, si, :] = sps.poisson.ppf(
                np.arange(1, 20) / 20.0,
                (mus[st] * (1 + spread * rng.normal(size=n)).clip(0.5))[:, None],
            )
    sq = sq.astype(np.float32) + rng.normal(0, 0.05, sq.shape).astype(np.float32)
    q = np.maximum.accumulate(np.clip(sq.mean(axis=0), 0, None), axis=2).astype(np.float32)
    b = world["base"]["pts"]
    return {
        "game_id": b["game_id"],
        "player_id": b["player_id"],
        "season": np.full(n, 2023, dtype=np.int16),
        "q": q,
        "seed_q": sq,
        "seeds": np.arange(6, dtype=np.int16),
    }


def good_infos(arms=V.ARM_NAMES) -> list[dict[str, Any]]:
    out = []
    for a in arms:
        n = int(a.split("_")[1])
        out.append(
            {
                "arm": a,
                "window_n": n,
                "cold": [
                    {"seed": s, "epochs_run": 10, "best_epoch": 6, "window_n": n} for s in range(6)
                ],
            }
        )
    return out


def run_out(arms_done, unfinished=()) -> dict[str, Any]:
    infos = good_infos(arms_done)
    return {
        "run_check": {"arms_done": sorted(arms_done), "ok": True, "problems": []},
        "budget": V.budget_gate(infos),
        "unfinished": list(unfinished),
    }


@pytest.fixture(scope="module")
def fixture_world():
    from tests.research.seq_learning_curve.test_export import BASE
    from tests.research.seq_learning_curve_2.test_export import _export, _raw, _tips  # noqa: F401

    raw = _tips(_raw(season=2023))
    exp, _ = X2.export_from_raw(
        raw,
        "S3",
        __import__("tests.research.seq_learning_curve_2.test_export", fromlist=["_flags"])._flags(
            raw
        ),
        **BASE,
    )
    sel = exp.meta["season"].to_numpy() == 2023
    # replicate the 90-odd fixture rows so the synthetic world has enough games / rows for a bootstrap; keys stay unique through a copy index
    k = 4
    gid = np.concatenate(
        [
            np.char.add(f"002{c:02d}", exp.meta["game_id"].to_numpy()[sel].astype("U6"))
            for c in range(k)
        ]
    )
    pid = np.tile(exp.meta["player_id"].to_numpy()[sel], k)
    world, meta, mus = make_world(gid, pid)
    oofs = {
        a: make_oof(world, mus, 10 + i, spread=0.05 + 0.01 * i) for i, a in enumerate(V.ARM_NAMES)
    }
    return world, meta, oofs, exp


def patched_repro(monkeypatch, world, oofs, u4_shift=0.0, u8_shift=0.0, t467_shift=0.0):
    fa = P1.E_crps(world["base"]["pts"]["full_int"], world["base"]["pts"]["y"]) - P1.E_crps(
        world["base"]["pts"]["a0_int"], world["base"]["pts"]["y"]
    )
    monkeypatch.setattr(S, "T467", {2023: float(fa.mean()) + t467_shift})
    return fa


def test_end_to_end_tiny_fixture_all_words_and_tables(monkeypatch, tmp_path, fixture_world) -> None:
    world, meta, oofs, exp = fixture_world
    monkeypatch.setattr(V, "CACHE", tmp_path / "cache")
    # the reproduction targets are the synthetic world's own numbers (the real ones are T485's)
    infos_done = list(V.ARM_NAMES)
    loaded: list[str] = []

    def load_oof(arm):
        loaded.append(arm)
        return oofs[arm], "t0000000"

    out = {**run_out(infos_done), "disclosures": V.DISCLOSURES, "rule_sha256": "0" * 64}
    # first pass only to read U_4 / U_8 AVG - FULL on the synthetic world, then pin the targets to them
    probe = V.full_arm(
        world["base"]["pts"],
        world,
        "pts",
        np.asarray(oofs["U_4"]["seed_q"][:, :, 0, :], dtype=float),
        None,
        verify=True,
    )
    assert probe["fixed_blend_identity"]  # the fixed-weight AVG equals build_arms bit for bit
    fa = P1.E_crps(world["base"]["pts"]["full_int"], world["base"]["pts"]["y"])
    u4 = float((probe["crps_avg"] - fa).mean())
    probe8 = V.full_arm(
        world["base"]["pts"],
        world,
        "pts",
        np.asarray(oofs["U_8"]["seed_q"][:, :, 0, :], dtype=float),
        None,
    )
    u8 = float((probe8["crps_avg"] - fa).mean())
    monkeypatch.setattr(V, "REPRO_U4", (u4, 0.003))
    monkeypatch.setattr(V, "REPRO_U8", (u8, 0.004))
    monkeypatch.setattr(
        S,
        "T467",
        {
            2023: float(
                (fa - P1.E_crps(world["base"]["pts"]["a0_int"], world["base"]["pts"]["y"])).mean()
            )
        },
    )
    res = V.score_run(
        out,
        [],
        True,
        world,
        meta,
        load_oof,
        team=np.arange(len(meta)) % 5,
        earlier_players={11, 12, 13, 14},
        date_clusters=True,
    )
    c = res["curve"]
    assert res["gates"] == {"repro_ok": True, "runtime_ok": True, "budget_ok": True}
    assert c["decision"]["A_m"] in ("DATA", "STEPS", "MIXED") and c["decision"]["E"] in (
        "RISING_10",
        "FLAT",
        "HURTS",
        "AMBIGUOUS",
    )
    assert c["fixed_blend_identity"] == {"U_4/pts": True}
    for k in ("A_m", "E", "A", "step_share_A_minus_Am"):
        r = c[k]
        assert (
            r["lo"] <= r["point"] <= r["hi"] or abs(r["point"]) < 1e-3
        )  # the point is the full-ensemble value, inside its (conservative) interval up to noise
        assert (
            r["lo95"] >= r["lo"] - 1e-12
            and r["hi95"] <= r["hi"] + 1e-12
            and r["n"] == len(meta)
            and len(r["loso"]["values"]) == 6
        )
    assert c["A"]["point"] - c["A_m"]["point"] == pytest.approx(
        c["step_share_A_minus_Am"]["point"], abs=1e-12
    )
    # the point of every contrast equals the plain full-ensemble CRPS difference
    avg = {
        a: probe_
        for a, probe_ in zip(("U_4", "U_8"), (probe["crps_avg"], probe8["crps_avg"]), strict=True)
    }
    assert c["A"]["point"] == pytest.approx(float((avg["U_8"] - avg["U_4"]).mean()), abs=1e-12)
    assert (
        c["secondary_family_S"]["m"] == 6
        and len(c["secondary_family_S"]["rows"]) == 6
        and all("p_bh" in r for r in c["secondary_family_S"]["rows"])
    )
    assert set(c["slices"]) >= {
        "Oct-Nov",
        "starters",
        "bench",
        "cold_start",
        "minutes<5",
        "no_report",
        "debut_2022_plus",
    }
    assert all(v["A_m"]["n"] == int(v["A_m"]["n"]) for v in c["slices"].values())
    assert c["team_drop_one"]["n_teams"] == 5 and c["date_clustered"]["n_dates"] == len(
        np.unique(world["date_days"])
    )
    assert set(c["descriptive_game_only"]) >= {"UP_4-U_4", "M_8-M_6", "M_10-M_9"}
    assert len(c["pts_coverage_seqc"]) == 10 and len(c["pts_coverage_seqc"]["U_4"]) == 19
    # a second run is a pure cache read of the seed pools (identical numbers)
    out2 = {**run_out(infos_done), "disclosures": V.DISCLOSURES, "rule_sha256": "0" * 64}
    res2 = V.score_run(
        out2, [], True, world, meta, load_oof, team=None, earlier_players=None, date_clusters=False
    )
    assert res2["curve"]["A_m"] == c["A_m"] and res2["curve"]["E"] == c["E"]
    assert V.render({**res, "verdict": res["curve"]["decision"]["verdict"]}).startswith(
        "# SEQ_LEARNING_CURVE_2"
    )


def test_nothing_beyond_u4_and_u8_is_read_when_a_gate_fails(
    monkeypatch, tmp_path, fixture_world
) -> None:
    world, meta, oofs, _ = fixture_world
    monkeypatch.setattr(V, "CACHE", tmp_path / "cache")
    fa = P1.E_crps(world["base"]["pts"]["full_int"], world["base"]["pts"]["y"])
    monkeypatch.setattr(
        S,
        "T467",
        {
            2023: float(
                (fa - P1.E_crps(world["base"]["pts"]["a0_int"], world["base"]["pts"]["y"])).mean()
            )
        },
    )
    seen: list[str] = []

    def load_oof(arm):
        seen.append(arm)
        return oofs[arm], "t0000000"

    # U(4) far from T485 -> the declared fallback; the curve arms were never opened
    monkeypatch.setattr(V, "REPRO_U4", (-0.5, 0.003))
    monkeypatch.setattr(
        V,
        "REPRO_U8",
        (
            float(
                (
                    V.full_arm(
                        world["base"]["pts"],
                        world,
                        "pts",
                        np.asarray(oofs["U_8"]["seed_q"][:, :, 0, :], dtype=float),
                        None,
                    )["crps_avg"]
                    - fa
                ).mean()
            ),
            0.004,
        ),
    )
    out = {**run_out(V.ARM_NAMES), "disclosures": [], "rule_sha256": "0" * 64}
    res = V.score_run(out, [], True, world, meta, load_oof)
    assert res["curve"]["decision"]["verdict"] == "INCONCLUSIVE (construction)" and set(seen) == {
        "U_4",
        "U_8",
    }
    # a run-time gate failure opens nothing at all
    seen.clear()
    out = {**run_out(V.ARM_NAMES), "disclosures": [], "rule_sha256": "0" * 64}
    res = V.score_run(out, [], False, world, meta, load_oof)
    assert res["curve"]["decision"]["verdict"] == "VOID (gate)" and seen == []
    # unfinished: INCONCLUSIVE, curve arms unopened
    seen.clear()
    monkeypatch.setattr(
        V,
        "REPRO_U4",
        (
            float(
                (
                    V.full_arm(
                        world["base"]["pts"],
                        world,
                        "pts",
                        np.asarray(oofs["U_4"]["seed_q"][:, :, 0, :], dtype=float),
                        None,
                    )["crps_avg"]
                    - fa
                ).mean()
            ),
            0.003,
        ),
    )
    done = [a for a in V.ARM_NAMES if a != "M_10"]
    out = {**run_out(done, ["M_10"]), "disclosures": [], "rule_sha256": "0" * 64}
    res = V.score_run(out, [], True, world, meta, load_oof)
    assert res["curve"]["decision"]["verdict"] == "INCONCLUSIVE (unfinished)" and set(seen) == {
        "U_4",
        "U_8",
    }
    # budget
    seen.clear()
    out = {**run_out(V.ARM_NAMES), "disclosures": [], "rule_sha256": "0" * 64}
    out["budget"] = {"ok": False}
    res = V.score_run(out, [], True, world, meta, load_oof)
    assert res["curve"]["decision"]["verdict"] == "INVALID (budget)" and set(seen) == {"U_4", "U_8"}


def test_oof_that_does_not_reproduce_its_own_ensemble_is_refused(
    monkeypatch, tmp_path, fixture_world
) -> None:
    world, meta, oofs, _ = fixture_world
    monkeypatch.setattr(V, "CACHE", tmp_path / "cache")
    bad = {k: v for k, v in oofs["U_4"].items()}
    bad["q"] = bad["q"] + 0.5  # the stored ensemble no longer equals the pooled per-seed quantiles
    fa = P1.E_crps(world["base"]["pts"]["full_int"], world["base"]["pts"]["y"])
    monkeypatch.setattr(
        S,
        "T467",
        {
            2023: float(
                (fa - P1.E_crps(world["base"]["pts"]["a0_int"], world["base"]["pts"]["y"])).mean()
            )
        },
    )
    out = {**run_out(V.ARM_NAMES), "disclosures": [], "rule_sha256": "0" * 64}
    with pytest.raises(RuntimeError, match="do not reproduce"):
        V.score_run(
            out, [], True, world, meta, lambda arm: (bad if arm == "U_4" else oofs[arm], "t")
        )


def test_exporter_rows_feed_the_scorer_meta(fixture_world) -> None:
    """The exporter's 2023-24 rows are exactly what the scorer's meta / row identity reads (norm_c from the S3 npz, same keys)."""
    world, meta, _, exp = fixture_world
    tmp = {k: exp.arrays[k] for k in ("norm_c", "game_id", "player_id", "season")}
    m = P1.meta_from_exports(tmp)
    assert (
        set(m.columns) >= {"game_id", "player_id", "season", "norm_c_pts"}
        and (m["season"] == 2023).all()
        and len(m) == int((exp.meta["season"] == 2023).sum())
    )
    assert P1.check_world_rows(world, meta)["ok"]
