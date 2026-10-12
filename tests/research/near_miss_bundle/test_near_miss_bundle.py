# ruff: noqa: E501
"""NEAR_MISS_BUNDLE harness: planted future, past-only placebo, can-fail shuffle, fixed membership, freeze and holdout refusal.

Everything runs on a synthetic world (no DB, no stored artefact)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from nba.props.context_residual import CRPS_TAUS
from research.eval import near_miss_bundle_eval as M

# --------------------------------------------------------------------------- synthetic world


def poisson_grid(mu: np.ndarray) -> np.ndarray:
    return np.asarray(stats.poisson.ppf(CRPS_TAUS[None, :], mu[:, None]), dtype=np.int16)


def make_world(
    seed: int = 0, games_per_season: int = 450, per_game: int = 12, signal: bool = True
) -> dict[str, Any]:
    """Two seasons (dates 0..170 and 365..535), player means shared by A0 / LOAD / A1 / SEQ with different noise.
    With ``signal`` SEQ and A1 know the true mean (A0 and the LOAD arm see it through noise); without, nothing is informative."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    gid = 0
    n_players = 150
    pmu = rng.gamma(4.0, 1.0, size=n_players) + 0.5
    for season, d0 in ((2023, 0), (2024, 365)):
        for g in range(games_per_season):
            day = d0 + int(g * 170 / games_per_season)
            ps = rng.choice(n_players, size=per_game, replace=False)
            for p in ps:
                rows.append(
                    {
                        "game_id": f"002{season % 100:02d}{gid:05d}",
                        "player_id": int(p),
                        "season": season,
                        "day": day,
                        "mu": pmu[p] * np.exp(rng.normal(0, 0.25)),
                        "team": int(p % 30) + 1,
                    }
                )
            gid += 1
    df = pd.DataFrame(rows).sort_values(["day", "game_id", "player_id"]).reset_index(drop=True)
    n = len(df)
    mu = df["mu"].to_numpy()
    y = rng.poisson(mu).astype(float)

    def nz(sd: float) -> np.ndarray:
        return np.exp(rng.normal(0, sd, size=n))

    a0 = poisson_grid(mu * nz(0.30))
    load = poisson_grid(mu * nz(0.28)) if signal else a0.copy()
    a1 = poisson_grid(mu * nz(0.10)) if signal else a0.copy()
    seq = poisson_grid(mu * nz(0.12)).astype(float) if signal else a0.astype(float)
    base = {
        "game_id": df["game_id"].to_numpy().astype(str), "player_id": df["player_id"].to_numpy().astype(np.int64),
        "season": df["season"].to_numpy().astype(np.int64), "y": y, "team_id": df["team"].to_numpy(),
        "a0_int": a0, "minutes": rng.uniform(0, 36, size=n), "starter10": rng.integers(0, 2, size=n).astype(float),
        "n_prior": rng.integers(0, 80, size=n).astype(float), "team_game_no": rng.integers(0, 82, size=n).astype(float),
        "has_report": np.ones(n), "n_rep_out": rng.integers(0, 3, size=n).astype(float), "tip_hour": np.full(n, 19.5),
        "mix": a0.astype(float),
    }  # fmt: skip
    ctx = pd.DataFrame(
        {
            "log_n_prior": np.log1p(base["n_prior"]),
            "starter10": base["starter10"],
            "n_rep_out": base["n_rep_out"],
            "has_report": base["has_report"],
            "team_game_no": base["team_game_no"],
        }
    )
    present = np.ones(n, dtype=bool)
    present[::997] = False
    w = {
        "base": {"reb": base, "pts": base}, "date_days": df["day"].to_numpy().astype(np.int64), "ctx": ctx,
        "g": rng.uniform(0.6, 1.0, size=n), "a1_int": a1, "a1_present": present,
        "seq": {"reb": seq, "pts": seq},
    }  # fmt: skip
    arms_in = {"A0": a0, "L": load, "PL_L": poisson_grid(mu * nz(0.30))}
    return {"w": w, "arms_in": arms_in, "load": load}


@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    return make_world()


# --------------------------------------------------------------------------- membership
def test_membership_is_fixed() -> None:
    assert M.MEMBERS_PTS == ("LOAD", "ROUTER", "SEQ")
    assert M.MEMBERS_OTHER == ("LOAD", "SEQ")
    assert M.STATS == ("pts", "reb", "ast", "fg3m")


def test_arms_cover_exactly_the_members(world: dict[str, Any]) -> None:
    arms = M.build_arms("pts", world["w"], world["arms_in"])
    for m in M.MEMBERS_PTS:
        assert {f"AL_{m}", f"K_{m}", f"PL_{m}"} <= set(arms)
    arms_r = M.build_arms("reb", world["w"], world["arms_in"])
    assert not any("ROUTER" in k for k in arms_r)
    extra = {
        k
        for k in arms
        if k.split("_", 1)[-1] not in M.MEMBERS_PTS and k not in {"A0", "R", "B", "B_weight_seq"}
    }
    assert not extra


# --------------------------------------------------------------------------- past-only placebo
def test_past_only_index_is_strictly_earlier_and_same_player() -> None:
    w = make_world(1, games_per_season=300)["w"]
    b = w["base"]["reb"]
    idx, cnt = M.past_only_index(b["player_id"], w["date_days"], 3)
    d = w["date_days"]
    own = idx != np.arange(len(idx))
    assert (d[idx[own]] < d[own]).all()
    same = b["player_id"][idx] == b["player_id"]
    assert cnt["own_player"] + cnt["any_player"] + cnt["self"] == len(idx)
    assert same.sum() >= cnt["own_player"]
    assert cnt["self"] == int((d == d.min()).sum()) - 0 or cnt["self"] <= int((d == d.min()).sum())
    again, _ = M.past_only_index(b["player_id"], w["date_days"], 3)
    assert np.array_equal(idx, again)


def test_past_only_planted_future_never_enters_earlier_rows() -> None:
    w = make_world(2, games_per_season=300)["w"]
    b = w["base"]["reb"]
    d = w["date_days"]
    idx, _ = M.past_only_index(b["player_id"], d, 5)
    col = np.arange(len(d), dtype=float)
    cut = int(np.median(d))
    planted = np.where(d >= cut, 1e9, col)
    early = d < cut
    assert (
        planted[idx][early] == col[idx][early]
    ).all()  # values dated >= cut never reach a row dated < cut
    control = np.where(
        d == d[early].max(), 1e9, col
    )  # a previous-day value does move later placebo values
    assert not (control[idx][d > d[early].max()] == col[idx][d > d[early].max()]).all()


def test_first_row_of_a_player_draws_from_any_earlier_row() -> None:
    player = np.array([1, 2, 1, 3, 2])
    date = np.array([0, 1, 2, 3, 4])
    idx, cnt = M.past_only_index(player, date, 0)
    assert idx[0] == 0 and cnt["self"] == 1  # earliest date keeps its own row
    assert date[idx[1]] < date[1] and idx[3] in (0, 1, 2)
    assert idx[2] == 0 and idx[4] == 1  # only one earlier row of that player


# --------------------------------------------------------------------------- labels and the blend
def test_a0_null_labels_follow_the_a0_distribution() -> None:
    w = make_world(3, games_per_season=200)
    a0 = w["arms_in"]["A0"]
    y = M.a0_null_labels(a0, 7)
    assert np.array_equal(y, M.a0_null_labels(a0, 7)) and not np.array_equal(
        y, M.a0_null_labels(a0, 8)
    )
    assert (y == np.round(y)).all() and (y >= a0[:, 0]).all() and (y <= a0[:, -1]).all()
    assert abs((y - a0.mean(axis=1)).mean()) < 0.1  # unbiased under A0
    pit = ((a0 < y[:, None]).mean(axis=1) + (a0 <= y[:, None]).mean(axis=1)) / 2
    assert abs(pit.mean() - 0.5) < 0.02


def test_router_grid_edges() -> None:
    rng = np.random.default_rng(0)
    first = np.sort(rng.integers(0, 30, size=(20, 199)), axis=1).astype(np.int16)
    a1 = np.sort(rng.integers(0, 30, size=(20, 199)), axis=1).astype(np.int16)
    g = np.full(20, 1.0)
    pr = np.ones(20, dtype=bool)
    assert np.array_equal(M.router_grid(first, a1, g, pr), a1)
    assert np.array_equal(M.router_grid(first, a1, np.zeros(20), pr), first)
    assert np.array_equal(M.router_grid(first, a1, g, np.zeros(20, dtype=bool)), first)
    assert np.array_equal(M.router_grid(first, None, None, None), first)


def test_blend_weights_use_only_earlier_labels_and_keep_row_order(world: dict[str, Any]) -> None:
    w = world["w"]
    b = w["base"]["reb"]
    first = b["a0_int"].astype(float)
    seq = w["seq"]["reb"]
    pooled, ww = M.learned_blend(first, seq, b["y"], w["date_days"], b["season"], w["ctx"])
    cut = 400  # inside the 2024 season
    y2 = b["y"].copy()
    y2[w["date_days"] >= cut] = 99.0  # planted future labels
    pooled2, ww2 = M.learned_blend(first, seq, y2, w["date_days"], b["season"], w["ctx"])
    early = w["date_days"] < cut
    assert np.array_equal(ww[early], ww2[early]) and np.array_equal(pooled[early], pooled2[early])
    late = w["date_days"] >= cut + 31
    assert not np.array_equal(ww[late], ww2[late])  # control: the gate does read earlier labels
    # permuting the input row order permutes the weights the same way (no hidden sort)
    perm = np.random.default_rng(0).permutation(len(first))
    pooled_p, ww_p = M.learned_blend(
        first[perm],
        seq[perm],
        b["y"][perm],
        w["date_days"][perm],
        b["season"][perm],
        w["ctx"].iloc[perm],
    )
    assert np.allclose(ww_p, ww[perm], atol=1e-9)
    assert (ww[:, 0] == 0.5).sum() > 0 and (ww[:, 0] == 0.5).sum() < len(
        ww
    )  # warm-up rows weigh 0.5, then the gate takes over


# --------------------------------------------------------------------------- the shuffled-label arm can fail
def leaky_blend(
    first199: np.ndarray,
    seq199: np.ndarray,
    y: np.ndarray,
    date_days: np.ndarray,
    season: np.ndarray,
    ctx: Any,
    stat: str = "pts",
) -> tuple[np.ndarray, np.ndarray]:
    """A planted leak: the pooled grid is pulled toward the label the row is scored on (an in-sample / peeking gate)."""
    pooled = 0.5 * first199 + 0.5 * seq199
    pooled = pooled + 0.6 * (y - pooled.mean(axis=1))[:, None]
    return pooled, np.full((len(y), 2), 0.5)


def test_shuffle_c_detects_a_planted_leak_and_passes_the_honest_pipeline(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    w, arms_in = world["w"], world["arms_in"]
    b = w["base"]["reb"]
    yp = M.a0_null_labels(arms_in["A0"], seed=1)

    def effect() -> dict[str, float]:
        arms = M.build_arms("reb", w, arms_in, y_fit=yp)
        d = M.delta_by_season("reb", b, arms["B"], arms["A0"], y=yp)
        return {s: v["point"] for s, v in d.items()}

    honest = effect()
    assert not M.shuffle_fails(honest), honest
    monkeypatch.setattr(M, "learned_blend", leaky_blend)
    leaked = effect()
    assert M.shuffle_fails(leaked), leaked


def test_shuffle_threshold_semantics() -> None:
    assert M.shuffle_fails({"2023": -0.003, "2024": 0.0})
    assert not M.shuffle_fails({"2023": -0.001, "2024": 0.002})


# --------------------------------------------------------------------------- full scoring path on the synthetic world
def test_score_stat_end_to_end_with_planted_signal(world: dict[str, Any]) -> None:
    res = M.score_stat("reb", world["w"], world["arms_in"], {"2023": 0.0, "2024": 0.0})
    assert res["members"] == ["LOAD", "SEQ"]
    for s in ("2023", "2024"):
        d = res["seasons"][s]["delta"]
        assert d["point"] < -0.01 and d["hi"] < 0  # SEQ and A1 know the truth in this world
        assert res["seasons"][s]["n"] == 450 * 12
    assert res["shuffle_ok"] is True
    assert set(res["placebo_ok"]) == {"LOAD", "SEQ"}
    assert res["flags"]["2024"]["carriers"] == [
        "SEQ"
    ]  # SEQ alone carries the gain here: the flag fires
    assert res["seq_weight_mean"]["2024"] > 0.6
    dec = M.decide_stat(res, True, {"2023": 0.001, "2024": 0.001})
    assert dec["verdict"] in {
        "FLAG_ONE_MEMBER",
        "FAIL_GUARD",
        "FAIL",
    }  # never PASS while one member carries it


def test_no_signal_world_is_not_a_pass() -> None:
    wd = make_world(4, signal=False)
    res = M.score_stat("reb", wd["w"], wd["arms_in"], {"2023": 0.0, "2024": 0.0})
    dec = M.decide_stat(res, True, {"2023": 0.9, "2024": 0.9})
    assert dec["verdict"] in {"FAIL", "FAIL_GUARD"}
    assert abs(res["seasons"]["2024"]["delta"]["point"]) < 0.01


# --------------------------------------------------------------------------- verdict logic
def test_one_member_flag() -> None:
    f = M.one_member_flag(0.01, {"LOAD": 0.002, "SEQ": 0.0085}, {"LOAD": 0.0098, "SEQ": 0.0015})
    assert f["flag"] and f["carriers"] == ["SEQ"]
    f2 = M.one_member_flag(0.01, {"LOAD": 0.004, "SEQ": 0.006}, {"LOAD": 0.006, "SEQ": 0.004})
    assert not f2["flag"]
    assert M.one_member_flag(-0.001, {"LOAD": 0.0}, {"LOAD": 0.0})["na"]


def test_verdict_order() -> None:
    assert M.stat_verdict(True, True, True, True, True, False) == "HARM"
    assert M.stat_verdict(False, False, True, True, True, False) == "FAIL"
    assert M.stat_verdict(False, True, False, True, True, False) == "FAIL_GUARD"
    assert M.stat_verdict(False, True, True, False, True, False) == "FAIL_GUARD"
    assert M.stat_verdict(False, True, True, True, False, False) == "FAIL_GUARD"
    assert M.stat_verdict(False, True, True, True, True, True) == "FLAG_ONE_MEMBER"
    assert M.stat_verdict(False, True, True, True, True, False) == "PASS"
    assert M.stat_verdict(False, True, True, True, True, False, repro_ok=False) == "FAIL"


# --------------------------------------------------------------------------- freeze and holdout refusal
def _doc(tmp: Path, status: str) -> Path:
    p = tmp / "NEAR_MISS_BUNDLE.md"
    p.write_text(f"# x\n\nStatus: {status}\n\nrule text\n\n{M.MARKER}\n\nresults\n")
    return p


def test_require_frozen_refusals(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "FROZEN")
    sha = M.frozen_sha(doc)

    def ok_git(*a: str) -> str:
        return f"abc\tprereg({M.ID}): {sha}\n"

    def no_git(*a: str) -> str:
        return "abc\tsomething else\n"

    assert M.require_frozen(sha, doc, ok_git) == sha
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        M.require_frozen("0" * 64, doc, ok_git)
    with pytest.raises(RuntimeError, match="--sha"):
        M.require_frozen(None, doc, ok_git)
    with pytest.raises(RuntimeError, match="not frozen"):
        M.require_frozen(sha, doc, no_git)
    sub = tmp_path / "d"
    sub.mkdir()
    draft = _doc(sub, "DRAFT")
    with pytest.raises(RuntimeError, match="FROZEN"):
        M.require_frozen(M.frozen_sha(draft), draft, ok_git)
    doc.write_text(doc.read_text().replace("rule text", "edited rule"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        M.require_frozen(sha, doc, ok_git)


def test_results_below_the_marker_do_not_change_the_hash(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "FROZEN")
    sha = M.frozen_sha(doc)
    doc.write_text(doc.read_text() + "more results\n")
    assert M.frozen_sha(doc) == sha


def test_holdout_stage_refuses_without_a_committed_confirmed_row(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "FROZEN")
    sha = M.frozen_sha(doc)
    log = tmp_path / "HOLDOUT_ACCESS_LOG.md"
    log.write_text("# log\n")

    def clean(*a: str) -> str:
        return (
            f"abc\tprereg({M.ID}): {sha}\n"
            if a and a[0] == "log" and "--format=%H%x09%s" in a
            else ""
        )

    def dirty(*a: str) -> str:
        return " M log" if a and a[0] == "status" else clean(*a)

    def committed(*a: str) -> str:
        return "deadbeef" if a and a[0] == "log" and a[1] == "--format=%H" else clean(*a)

    with pytest.raises(RuntimeError, match="no committed holdout row"):
        M.require_holdout_row(sha, log, doc, clean)
    log.write_text(f"| {M.ID} {sha[:8]} PRE-REGISTERED, DEVIN CONFIRMED |\n")
    with pytest.raises(RuntimeError, match="not in any commit"):
        M.require_holdout_row(sha, log, doc, clean)
    with pytest.raises(RuntimeError, match="uncommitted"):
        M.require_holdout_row(sha, log, doc, dirty)
    assert M.ID in M.require_holdout_row(sha, log, doc, committed)


def test_no_2025_loader_in_the_module() -> None:
    src = Path(M.__file__).read_text()
    assert "season = 2025" not in src and "season=2025" not in src
    assert "PRAGMA" not in src and "read_only=False" not in src


# --------------------------------------------------------------------------- stages on the synthetic world
def _write_arm(cache: Path, stat: str, tag: str, qi: np.ndarray, b: dict[str, Any]) -> None:
    np.savez(
        cache / f"{stat}__{tag}.npz",
        qi=qi,
        y=b["y"],
        gid=b["game_id"],
        pid=b["player_id"],
        season=b["season"],
    )


def test_stage_score_and_render_on_the_synthetic_world(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world["w"]
    b = w["base"]["reb"]
    cache = tmp_path / "cache"
    cache.mkdir()
    for st in M.STATS:
        w["base"][st] = b
        _write_arm(cache, st, "L", world["arms_in"]["L"], b)
        _write_arm(cache, st, "PL_L", world["arms_in"]["PL_L"], b)
        _write_arm(cache, st, "SH_base", world["arms_in"]["A0"], b)
        _write_arm(cache, st, "SH_L", world["arms_in"]["L"], b)
        w["seq"][st] = w["seq"]["reb"]
    out = tmp_path / "out"
    out.mkdir()
    (out / "repro.json").write_text(
        '{"repro": {"pts": {"pass": true}, "reb": {"pass": true}, "ast": {"pass": true}, "fg3m": {"pass": true}}, "load_nulls": {"pass": true}}'
    )
    monkeypatch.setattr(M, "CACHE", cache)
    monkeypatch.setattr(M, "OUT_DIR", out)
    monkeypatch.setattr(M, "REPORT_MD", tmp_path / "r.md")
    monkeypatch.setattr(M, "PAIRS_DIR", tmp_path / "no_pairs")
    monkeypatch.setattr(M, "load_world", lambda: w)
    monkeypatch.setattr(M, "require_frozen", lambda sha, *a, **k: "f" * 64)
    res = M.stage_score("f" * 64)
    assert set(res["stats"]) == set(M.STATS) and (out / "results.json").exists()
    assert "error" in res["decision_score_dk_t60"]  # no pairs directory: secondary, never fatal
    for st in M.STATS:
        d = res["stats"][st]["decision"]
        assert d["verdict"] in {"FLAG_ONE_MEMBER", "FAIL_GUARD", "FAIL", "PASS", "HARM"}
        assert set(d["bh_p"]) == {"2023", "2024"}
    text = (tmp_path / "r.md").read_text()
    assert "NEAR_MISS_BUNDLE" in text and "| pts | 2023 |" in text
    assert res["eligible_for_holdout_row"] == [
        s for s in M.STATS if res["stats"][s]["decision"]["verdict"] == "PASS"
    ]


def test_repro_check_and_cached_arm_alignment(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = world["w"]["base"]["reb"]
    monkeypatch.setattr(M, "CACHE", tmp_path)
    _write_arm(tmp_path, "reb", "base", b["a0_int"], b)
    assert M.repro_check("reb", b)["pass"] is True
    _write_arm(tmp_path, "reb", "base", b["a0_int"] + 1, b)
    assert M.repro_check("reb", b)["pass"] is False
    bad = dict(b)
    bad["player_id"] = b["player_id"][::-1].copy()
    with pytest.raises(RuntimeError, match="not the A0 rows"):
        M.load_cached_arm("reb", "base", bad)


def test_load_audit_flags_a_minutes_dependent_null_pattern() -> None:
    import polars as pl

    n = 3000
    rng = np.random.default_rng(0)
    minutes = rng.uniform(0, 36, size=n)
    b = {
        "game_id": np.array([f"g{i}" for i in range(n)]),
        "player_id": np.arange(n),
        "minutes": minutes,
    }
    col_ok = rng.normal(size=n)
    col_bad = np.where(
        minutes < 5, np.nan, rng.normal(size=n)
    )  # the ADVANCED_RATES fingerprint: NaN follows minutes
    fr = pl.DataFrame(
        {"game_id": b["game_id"], "player_id": b["player_id"], "ok": col_ok, "bad": col_bad}
    )
    a = M.load_audit(fr, b, ["ok", "bad"])
    assert a["ok"]["gap_pp"] == 0.0 and a["bad"]["gap_pp"] > 90.0 and a["pass"] is False


def test_main_refuses_unfrozen_stages_and_the_holdout() -> None:
    with pytest.raises(RuntimeError, match="not the frozen rule|--sha"):
        M.main(["run", "--sha", "0" * 64])
    with pytest.raises(RuntimeError, match="not the frozen rule|--sha"):
        M.main(["score"])
    with pytest.raises(RuntimeError):
        M.main(["holdout", "--sha", "0" * 64])


def test_baseline_is_a_parameter_with_separate_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("BASELINE_DIR", "CACHE", "OUT_DIR", "REPORT_MD"):
        monkeypatch.setattr(M, name, getattr(M, name))
    monkeypatch.setattr(M.E, "LT_DIR", M.E.LT_DIR)
    from research.eval import workload_eval as WE

    monkeypatch.setattr(WE, "LT_DIR", WE.LT_DIR)
    assert M.set_baseline(None) == M.DEFAULT_BASELINE_DIR and M.baseline_is_current()
    default_cache = M.CACHE
    alt = tmp_path / "pi_recent_role"
    M.set_baseline(alt)
    assert (
        not M.baseline_is_current() and alt.resolve() == M.E.LT_DIR and alt.resolve() == WE.LT_DIR
    )
    assert default_cache != M.CACHE and M.CACHE.name.endswith("_pi_recent_role")
    assert M.OUT_DIR.name.endswith("_pi_recent_role")
    M.set_baseline(None)
    assert default_cache == M.CACHE
