# ruff: noqa: E501
"""PRESEASON_ROLE_PRIOR harness: synthetic data only, except one gate-reproduction test that skips without the data root."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.eval import preseason_role_prior as m

REPO = Path(__file__).resolve().parents[3]
KAPPA = {"starter": 1.25, "bench": 0.94}


def make_ctx(n: int = 40, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    ctx = pd.DataFrame(
        {
            "game_id": [f"g{i // 4}" for i in range(n)],
            "player_id": np.arange(n),
            "slice": np.where(np.arange(n) % 5 == 0, "stayer", "mover"),
            "gwt": rng.integers(0, 14, n).astype(float),
            "min10": rng.uniform(8, 34, n),
            "pre_n": rng.integers(0, 5, n).astype(float),
            "pre_start": rng.random(n) < 0.4,
            "prev_min": rng.uniform(100, 2000, n),
            "l10_n": np.full(n, 10.0),
            "l10_min": rng.uniform(100, 340, n),
        }
    )
    ctx["pre_m"] = ctx.pre_n * rng.uniform(8, 25, n)
    for s in m.STATS:
        ctx[f"pre_{s}"] = ctx.pre_m * rng.uniform(0.05, 0.5, n)
        ctx[f"prev_{s}"] = ctx.prev_min * rng.uniform(0.05, 0.5, n)
        ctx[f"l10_{s}"] = ctx.l10_min * rng.uniform(0.05, 0.5, n)
    mrec = ctx.min10.to_numpy() * rng.uniform(0.1, 0.5, n)
    return ctx, mrec


def test_weights_zero_gives_lambda_exactly_one_and_scores_equal_a0_bit_for_bit() -> None:
    ctx, mrec = make_ctx()
    z = ctx.assign(pre_n=0.0, pre_m=0.0)
    rng = np.random.default_rng(1)
    q = np.maximum.accumulate(np.sort(rng.uniform(0, 30, (len(ctx), m.NQ)), axis=1), axis=1)
    y = rng.integers(0, 35, len(ctx)).astype(float)
    for arm in ("A1", "A2", "A3"):
        lam = m.lambdas(z, mrec, "pts", arm, KAPPA)
        assert np.array_equal(lam, np.ones(len(ctx)))
        assert np.array_equal(m.crps_int_q(q * lam[:, None], y), m.crps_int_q(q, y))


def test_uncovered_stayer_and_late_rows_are_served_as_a0() -> None:
    ctx, mrec = make_ctx()
    for arm in ("A1", "A2", "A3"):
        lam = m.lambdas(ctx, mrec, "pts", arm, KAPPA)
        bad = (ctx.pre_n < m.COVER_MIN_GAMES) | (ctx["slice"] != "mover") | (ctx.gwt >= m.K_MAIN)
        assert np.all(lam[bad.to_numpy()] == 1.0)
        assert np.any(lam[~bad.to_numpy()] != 1.0)
        assert lam.min() >= m.LAM_CLIP[0] and lam.max() <= m.LAM_CLIP[1]


def test_fade_vanishes_at_k_and_a4_permutes_values_not_weights() -> None:
    ctx, mrec = make_ctx()
    at_k = ctx.assign(gwt=float(m.K_MAIN))
    assert np.all(m.lambdas(at_k, mrec, "pts", "A2", KAPPA) == 1.0)
    perm = m.a4_perm(ctx, m.K_MAIN, "mover", 0)
    n_cov = int(((ctx["slice"] == "mover") & (ctx.gwt < m.K_MAIN) & (ctx.pre_n >= 2)).sum())
    assert sorted(perm.tolist()) == list(range(n_cov))
    lam4 = m.lambdas(ctx, mrec, "pts", "A4", KAPPA, perm=perm)
    assert lam4.shape == (len(ctx),)
    with pytest.raises(ValueError):
        m.lambdas(ctx, mrec, "pts", "A4", KAPPA)


def test_2025_is_never_loaded() -> None:
    with pytest.raises(ValueError, match="holdout"):
        m.check_season(2025)
    with pytest.raises(ValueError, match="holdout"):
        m.read_rows("pts", 2025)
    with pytest.raises(ValueError, match="holdout"):
        m.load_pre_rows(None, 2025)
    with pytest.raises(ValueError, match="holdout"):
        m.build_ctx(pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float), pd.DataFrame(), 2025)
    assert m.MAX_SEASON == 2024 and "<=?" in "g.season<=?".replace(" ", "")


def tiny_reg() -> pd.DataFrame:
    rows = []
    gid = 0
    for season, team in ((2022, 1), (2023, 2)):
        for d in range(15):
            gid += 1
            rows.append(
                {
                    "player_id": 7,
                    "team_id": team,
                    "season": season,
                    "game_date": pd.Timestamp(f"{season}-11-{d + 1:02d}"),
                    "game_id": f"002{gid:07d}",
                    "minutes": 20.0,
                    "pts": 10,
                    "reb": 3,
                    "ast": 2,
                    "fg3m": 1,
                }
            )
    return pd.DataFrame(rows)


def tiny_rows(reg: pd.DataFrame) -> pd.DataFrame:
    r = reg[reg.season == 2023][["game_id", "player_id", "team_id", "game_date"]].copy()
    r["min10"] = 20.0
    r["starter10"] = 1.0
    r["m_recency"] = 9.0
    return r.reset_index(drop=True)


def tiny_pre(day: str = "2023-10-05") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "player_id": [7, 7],
            "team_id": [2, 2],
            "game_date": [pd.Timestamp(day)] * 2,
            "minutes": [30.0, 28.0],
            "pts": [18, 12],
            "reb": [4, 5],
            "ast": [3, 2],
            "fg3m": [2, 1],
            "starter": [True, True],
        }
    )


def test_slice_is_mover_and_planted_future_inputs_leave_lambda_unchanged() -> None:
    reg = tiny_reg()
    fe = pd.Series({7: 2022})
    rows = tiny_rows(reg)
    ctx = m.build_ctx(rows, reg, fe, tiny_pre(), 2023)
    assert set(ctx["slice"]) == {"mover"}
    lam = m.lambdas(ctx, rows["m_recency"].to_numpy(), "pts", "A2", KAPPA)
    assert lam[0] != 1.0 and lam[m.K_MAIN :].tolist() == [1.0] * (len(lam) - m.K_MAIN)
    # planted future regular-season rows (dated after row 3) must not move row 0..3
    future = reg.assign(pts=999, minutes=48.0)
    cutoff = reg.game_date[17]
    reg_full = pd.concat(
        [
            reg[reg.season == 2022],
            reg[(reg.season == 2023) & (reg.game_date <= cutoff)],
            future[(future.season == 2023) & (future.game_date > cutoff)],
        ]
    ).reset_index(drop=True)
    ctx2 = m.build_ctx(rows, reg_full, fe, tiny_pre(), 2023)
    lam2 = m.lambdas(ctx2, rows["m_recency"].to_numpy(), "pts", "A2", KAPPA)
    assert np.array_equal(lam, lam2)
    # a preseason game dated on or after the first scored row violates known-at
    with pytest.raises(ValueError, match="known-at"):
        m.build_ctx(rows, reg, fe, tiny_pre("2023-11-02"), 2023)


def test_block_zq_recovers_the_served_integer_grid() -> None:
    from statistics import NormalDist

    rng = np.random.default_rng(3)
    taus = (np.arange(1, m.NQ + 1) - 0.5) / m.NQ
    zq = np.array([NormalDist().inv_cdf(t) for t in taus])
    n = 400
    c = rng.uniform(2, 25, n)
    s = rng.uniform(0.8, 6, n)
    q = m.grid_q(c, s, zq)
    pge = [np.array([(row > k - 0.5).sum() / m.NQ for k in range(1, 60)]) for row in q]
    z = m.block_zq(c, s, pge)
    assert z["inconsistent"] == 0
    q2 = m.grid_q(c, s, z["mid"])
    for k in range(1, 60):
        assert np.array_equal((q > k - 0.5).sum(1), (q2 > k - 0.5).sum(1))
    y = rng.integers(0, 30, n).astype(float)
    assert np.allclose(m.crps_int_q(q, y), m.crps_int_q(q2, y), atol=1e-12)
    assert np.allclose(m.crps_int_pge(pge, y), m.crps_int_q(q, y), atol=1e-12)


def cell(
    point: float, hi: float, p: float, a4: float = 0.0, cov: float = 0.0, lo: float | None = None
) -> dict[str, float]:
    return {
        "point": point,
        "lo": point - (hi - point) if lo is None else lo,
        "hi": hi,
        "p": p,
        "a4_point": a4,
        "cov_dev": cov,
    }


def res_of(
    good: bool, seasons: tuple[str, ...] = ("2023", "2024"), stat: str = "pts", **kw: float
) -> dict[str, dict[str, dict[str, float]]]:
    neutral = cell(-0.0001, 0.002, 0.4)
    out = {s: {st: dict(neutral) for st in m.STATS} for s in ("2023", "2024")}
    for s in seasons:
        out[s][stat] = cell(
            -0.02 if good else -0.001, -0.008 if good else 0.003, 0.001 if good else 0.3, **kw
        )
    return out


def test_each_word_is_reachable() -> None:
    assert m.evaluate(res_of(True))["word"] == "PASS"
    assert m.evaluate(res_of(True, stat="ast"))["word"] == "PASS_OTHER_STAT"
    assert m.evaluate(res_of(True, a4=-0.02))["word"] == "PLACEBO_TOO"
    assert m.evaluate(res_of(True, cov=0.05))["word"] == "FAIL_GUARD"
    assert m.evaluate(res_of(True, seasons=("2023",)))["word"] == "FAIL_ONE_SEASON"
    assert m.evaluate(res_of(False))["word"] == "FAIL_NO_GAIN"


def test_harm_elsewhere_is_a_guard_failure() -> None:
    r = res_of(True)
    r["2024"]["reb"] = cell(0.02, 0.03, 0.9, lo=0.01)
    assert m.evaluate(r)["word"] == "FAIL_GUARD"


def test_bh_adjust_matches_hand_value() -> None:
    adj = m.bh_adjust(np.array([0.01, 0.04, 0.03, 0.5]))
    assert np.allclose(adj, [0.04, 0.0533333333, 0.0533333333, 0.5])


def test_rehearsal_every_world_returns_its_word() -> None:
    out = m.rehearse(seeds=3)
    assert out["total_ok"] == out["total"] == 18


def test_run_refuses_without_the_frozen_rule() -> None:
    with pytest.raises(RuntimeError, match="sha256"):
        m.run_real(None)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        m.run_real("0" * 64)


TEXT = "RULE ABOVE\n" + m.MARKER + "\nbelow\n"
SHA = m.text_sha(TEXT)
FC, PC = "f" * 40, "a" * 40


def fake_git(log: str, shows: dict[str, str]) -> Callable[..., str]:
    def run(*args: str) -> str:
        return log if args[0] == "log" else shows.get(args[1], "") if args[0] == "show" else ""

    return run


def test_freeze_and_pin_refusals(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(TEXT)
    with pytest.raises(RuntimeError, match="not frozen"):
        m.require_frozen(SHA, doc, fake_git("", {}))
    ok_log = f"{FC}\tprereg({m.ID}): {SHA}\n"
    ok = fake_git(ok_log, {f"{FC}:{m.DOC_REL}": TEXT})
    assert m.require_frozen(SHA, doc, ok) == SHA
    with pytest.raises(RuntimeError, match="not pinned"):
        m.require_harness_pinned(SHA, ok, REPO, lambda *a: True)
    blobs = {}
    shows = {f"{FC}:{m.DOC_REL}": TEXT}
    for rel in m.HARNESS_FILES:
        txt = (REPO / rel).read_text()
        shows[f"{PC}:{rel}"] = txt
        shows[f"{FC}:{rel}"] = txt
        blobs[rel] = hashlib.sha256(txt.encode()).hexdigest()
    pin = m.pin_of(blobs)
    log = f"{PC}\tharness({m.ID}): {pin}\n{ok_log}"
    assert m.require_harness_pinned(SHA, fake_git(log, shows), REPO, lambda *a: True) == blobs
    with pytest.raises(RuntimeError, match="descend"):
        m.require_harness_pinned(SHA, fake_git(log, shows), REPO, lambda *a: False)
    shows[f"{PC}:{m.HARNESS_FILES[0]}"] = "edited\n"
    with pytest.raises(RuntimeError, match="differs"):
        m.require_harness_pinned(SHA, fake_git(log, shows), REPO, lambda *a: True)


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
            out |= {n.module + "." + a.name for a in n.names}
    return out


def test_pin_covers_import_closure() -> None:
    seen: set[str] = set()
    todo = ["research/eval/preseason_role_prior.py"]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for mod in _imports(REPO / rel):
            if mod.split(".")[0] in ("nba", "research"):
                p = REPO / (mod.replace(".", "/") + ".py")
                if p.exists():
                    todo.append(str(p.relative_to(REPO)))
    assert seen <= set(m.HARNESS_FILES), sorted(seen - set(m.HARNESS_FILES))
    for rel in m.HARNESS_FILES:
        assert (REPO / rel).exists()


@pytest.mark.skipif(not m.DB.exists(), reason="no data root")
def test_gate_reproduces_the_committed_gate_json() -> None:
    committed = json.loads((REPO / "reports/prereg_preseason_role_prior/gate.json").read_text())[
        "gate"
    ]
    got = m.run_gate()
    assert json.dumps(got, sort_keys=True, default=str) == json.dumps(committed, sort_keys=True)
