# mypy: disable-error-code="no-untyped-def,arg-type,index,no-any-return,type-arg"
# ruff: noqa: E501
"""ERROR_FEEDBACK: as-of columns (planted future, same-day, past control, placebo shift), the report-free base
(walk-forward, report columns refused, season cap on storage), the scorer's checks, and the touch refusals."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig, stat_feature_names
from research.eval import error_feedback_eval as E
from research.eval import error_feedback_rule as RULE
from research.eval.error_feedback_gate import build_error_feedback
from research.features import error_feedback as EF

MARK = "=== RESULTS BELOW ===\n"


# --------------------------------------------------------------------------- columns
def _rand(n: int = 2500, seed: int = 1):
    rng = np.random.default_rng(seed)
    pid = rng.integers(0, 40, n)
    day = rng.integers(19000, 19200, n)
    return pid, day, rng.normal(size=n), rng.random(n)


def test_vectorised_columns_equal_the_reference_loop() -> None:
    pid, day, res, pit = _rand()
    ref = build_error_feedback(pid, day.astype("datetime64[D]"), res, pit)
    got = EF.error_feedback_columns(pid, day, pid, day, res, pit)
    for k in ref:
        assert np.allclose(ref[k], got[k], equal_nan=True, atol=1e-9), k


def test_planted_future_is_inert_and_past_control_moves() -> None:
    pid, day, res, pit = _rand()
    cut = 19100
    a = EF.error_feedback_columns(pid, day, pid, day, res, pit)
    fut = day >= cut
    b = EF.error_feedback_columns(
        pid, day, pid, day, np.where(fut, res + 50, res), np.where(fut, 1 - pit, pit)
    )
    early = day <= cut  # rows ON the cut day must not see same-day rows either
    for k in a:
        assert np.array_equal(a[k][early], b[k][early], equal_nan=True), k
    c = EF.error_feedback_columns(pid, day, pid, day, np.where(~fut, res + 50, res), pit)
    late = (day > cut) & (day <= cut + 5)  # their 10-row windows reach back before the cut
    assert (np.abs(c["ef_res_l10"][late] - a["ef_res_l10"][late]) > 1).mean() > 0.5


def test_same_day_rows_never_feed_each_other_and_cap_at_ten() -> None:
    pid = np.array([7] * 14)
    day = np.array([1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12])
    res = np.arange(14, dtype=float)
    out = EF.error_feedback_columns(pid, day, pid, day, res, np.zeros(14))
    assert np.isnan(out["ef_res_l3"][0]) and np.isnan(out["ef_res_l3"][1]) and out["ef_n"][1] == 0
    assert out["ef_n"][2] == 2 and out["ef_res_l3"][2] == 0.5
    assert (
        out["ef_n"][12] == 10 and out["ef_n"][13] == 10
    )  # the two day-12 rows see the same 10 earlier rows
    assert out["ef_res_l3"][12] == out["ef_res_l3"][13] == np.mean([9, 10, 11])
    assert out["ef_res_l10"][12] == np.mean(np.arange(2, 12))


def test_placebo_shift_reads_rows_eleven_to_twenty_back() -> None:
    pid = np.zeros(25, dtype=int)
    day = np.arange(25)
    res = np.arange(25, dtype=float)
    out = EF.error_feedback_columns(pid, day, pid, day, res, np.zeros(25), shift=10, prefix="pl_")
    assert set(out) == {"pl_res_l3", "pl_res_l5", "pl_res_l10", "pl_pit_l5", "pl_n"}
    # row 24 has 24 earlier rows; skipping the latest 10 leaves rows 0..13, the window is the latest 10 of those
    assert out["pl_n"][24] == 10 and out["pl_res_l3"][24] == np.mean([11, 12, 13])
    assert out["pl_res_l10"][24] == np.mean(np.arange(4, 14))
    assert np.isnan(out["pl_res_l3"][10]) and out["pl_n"][10] == 0
    with pytest.raises(ValueError):
        EF.error_feedback_columns(pid, day, pid, day, res, res, shift=-1)


def test_shuffle_keeps_timing_and_marginals_and_breaks_the_link() -> None:
    base = {
        "row_y": np.arange(1000, dtype=float),
        "row_med": np.zeros(1000),
        "row_pit": np.linspace(0, 1, 1000),
        "row_season": np.repeat([2021, 2022], 500),
    }
    r, p = EF.shuffled(base, 3)
    assert sorted(r[:500]) == list(range(500)) and sorted(r[500:]) == list(range(500, 1000))
    assert np.allclose(p, r / 999)  # residual and PIT move together
    r2, _ = EF.shuffled(base, 3)
    assert np.array_equal(r, r2) and not np.array_equal(r, EF.shuffled(base, 4)[0])


def test_mid_pit_on_integer_grid() -> None:
    qi = np.tile(np.arange(199) // 20, (2, 1)).astype(float)  # 0..9 in equal-ish mass
    y = np.array([0.0, 50.0])
    p = EF.mid_pit(qi, y)
    assert p[0] == pytest.approx(0.5 * (0 + 20 / 199)) and p[1] == 1.0


# --------------------------------------------------------------------------- base
def _synth_stat_frame(
    n_players: int = 60, start: dt.date = dt.date(2021, 10, 20), days: int = 200, seed: int = 0
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    names = stat_feature_names("pts")
    rows = []
    for d in range(0, days, 2):
        date = start + dt.timedelta(days=d)
        season = 2021 if date < dt.date(2022, 7, 1) else 2022
        for p in range(n_players):
            if rng.random() < 0.2:
                continue
            rows.append((f"00{season}{d:05d}", p, date, season))
    n = len(rows)
    df = pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "player_id": [r[1] for r in rows],
            "game_date": [r[2] for r in rows],
            "season": [r[3] for r in rows],
        }
    )
    feats = {c: rng.normal(size=n) for c in names}
    m = rng.uniform(5, 25, n)
    y = np.maximum(np.round(m + rng.normal(scale=5, size=n)), 0)
    df = df.with_columns([pl.Series(c, v) for c, v in feats.items()]).with_columns(
        pl.Series("m", m),
        pl.Series("s", np.full(n, 5.0)),
        pl.Series("y", y),
        pl.Series("resid", y - m),
        pl.Series("minutes", rng.uniform(0, 40, n)),
        pl.Series("starter10", rng.random(n)),
        pl.Series("n_prior", rng.integers(5, 80, n).astype(float)),
    )
    return df.sort(["game_date", "game_id", "player_id"])


CFG = ContextResidualConfig(n_estimators=15, min_child_samples=20, min_cal_rows=200, n_jobs=1)


def test_base_feature_names_drop_every_report_column_and_walk_refuses_them() -> None:
    for s in ("pts", "reb", "ast", "fg3m"):
        names = EF.base_feature_names(s)
        assert not set(names) & set(EF.REPORT_FEATURES)
        assert set(stat_feature_names(s)) - set(names) == {
            "has_report",
            "n_out_rot",
            "vac_min",
            f"vac_{s}",
        }
    with pytest.raises(ValueError, match="report columns"):
        EF.walk_base(
            _synth_stat_frame(days=20), "pts", CFG, (2021,), names=stat_feature_names("pts")
        )


def test_walk_base_is_walk_forward_planted_future_label() -> None:
    sf = _synth_stat_frame()
    a = EF.walk_base(sf, "pts", CFG, (2021, 2022), min_train=500)
    blocks = np.unique(a["row_blk"])
    assert len(blocks) >= 3
    last_start = int(a["blk_start"][-1])
    cut = dt.date(1970, 1, 1) + dt.timedelta(days=last_start)
    planted = sf.with_columns(
        pl.when(pl.col("game_date") >= cut)
        .then(pl.col("y") + 40)
        .otherwise(pl.col("y"))
        .alias("y"),
        pl.when(pl.col("game_date") >= cut)
        .then(pl.col("resid") + 40)
        .otherwise(pl.col("resid"))
        .alias("resid"),
    )
    b = EF.walk_base(planted, "pts", CFG, (2021, 2022), min_train=500)
    early = a["row_day"] < last_start
    for k in ("row_c", "row_s", "row_med"):
        assert np.array_equal(a[k][early], b[k][early]), k
    # non-inert control: planting BEFORE the first block's start moves every forecast
    first = dt.date(1970, 1, 1) + dt.timedelta(days=int(a["blk_start"][0]))
    ctl = sf.with_columns(
        pl.when(pl.col("game_date") < first)
        .then(pl.col("resid") + 40)
        .otherwise(pl.col("resid"))
        .alias("resid")
    )
    c = EF.walk_base(ctl, "pts", CFG, (2021, 2022), min_train=500)
    assert np.abs(c["row_c"] - a["row_c"]).mean() > 1.0
    # integer median and mid-PIT are on the integer grid
    assert (
        np.array_equal(a["row_med"], np.round(a["row_med"]))
        and ((a["row_pit"] >= 0) & (a["row_pit"] <= 1)).all()
    )


def test_save_and_load_base_refuse_seasons_beyond_the_cap(tmp_path: Path) -> None:
    base = {"row_season": np.array([2023, 2025]), "row_y": np.zeros(2)}
    with pytest.raises(ValueError, match="season > 2024"):
        EF.save_base(tmp_path / "b.npz", base, 2024)
    np.savez_compressed(tmp_path / "c.npz", **base)
    with pytest.raises(ValueError):
        EF.load_base(tmp_path / "c.npz", 2024)
    ok = {"row_season": np.array([2023, 2024]), "row_y": np.zeros(2)}
    assert (
        EF.load_base(EF.save_base(tmp_path / "d.npz", ok, 2024), 2024)["row_season"].max() == 2024
    )


def test_concat_base_refuses_duplicates() -> None:
    a = {"row_gid": np.array(["g1"]), "row_pid": np.array([1]), "row_season": np.array([2023])}
    with pytest.raises(ValueError, match="duplicate"):
        EF.concat_base(a, a)


# --------------------------------------------------------------------------- harness pieces
def test_world_refuses_2025_without_the_touch_token_and_the_live_season_always() -> None:
    with pytest.raises(RuntimeError, match="only inside the logged touch"):
        E.load_world(2025)
    with pytest.raises(RuntimeError, match="never loaded"):
        E.load_world(2026, touch=E._Touch())


def test_assert_cap_and_touch_body_refuse_frames_beyond_the_target() -> None:
    with pytest.raises(E.GateStop):
        E.assert_cap(pl.DataFrame({"season": [2023, 2025]}), 2024, "x")
    w = E.World(*(pl.DataFrame() for _ in range(6)), set(), set(), CFG, {}, 2025)  # type: ignore[arg-type]
    with pytest.raises(E.GateStop, match="cap 2025 != target 2024"):
        E.touch_body(2024, w, Path("/nonexistent"), Path("/nonexistent"), True, 1)


def test_blind_perm_permutes_only_within_games() -> None:
    gid = np.repeat(np.array(["a", "b", "c", "d"]), 9)
    p = E.blind_perm(gid, 0)
    assert (
        sorted(p) == list(range(36))
        and np.array_equal(gid[p], gid)
        and (p != np.arange(36)).mean() > 0.5
    )


def test_walk_arm_blind_changes_scores_not_forecasts() -> None:
    sf = EF.attach(
        _synth_stat_frame(days=120),
        {
            "row_pid": np.array([0]),
            "row_day": np.array([0]),
            "row_y": np.zeros(1),
            "row_med": np.zeros(1),
            "row_pit": np.zeros(1),
        },
    )
    names = stat_feature_names("pts")
    a = E.walk_arm(sf, "pts", names, (2021,), CFG)
    b = E.walk_arm(sf, "pts", names, (2021,), CFG, blind_seed=5)
    assert np.array_equal(a["c"], b["c"]) and not np.allclose(a["crps"], b["crps"])
    assert np.allclose(np.sort(a["y"]), np.sort(b["y"]))


def _arms(n: int = 4000, eff: float = -0.02, seed: int = 0):
    rng = np.random.default_rng(seed)
    gid = np.repeat(np.arange(n // 10).astype(str), 10)
    base = {
        "gid": gid,
        "pid": np.arange(n),
        "y": np.zeros(n),
        "season": np.full(n, 2025),
        "day": np.repeat(np.arange(n // 100), 100),
        "starter10": rng.random(n),
        "n_prior": np.full(n, 40.0),
        "minutes": rng.uniform(0, 40, n),
        "ef_n": np.full(n, 10.0),
        "pit": rng.random(n),
    }
    a0 = {**base, "crps": 3.0 + rng.normal(scale=0.3, size=n)}
    ef = {**base, "crps": a0["crps"] + eff + rng.normal(scale=0.01, size=n)}
    pl_ = {**base, "crps": a0["crps"] + 0.0}
    return a0, ef, pl_


GOOD_NULL = E.null_summary([0.0005, -0.0005, 0.0003, -0.0002, 0.0001])
GOOD_MISS = {"pass": True}


def test_primary_verdict_passes_a_clear_gain_and_fails_each_way() -> None:
    a0, ef, pl_ = _arms()
    m = np.ones(len(a0["y"]), dtype=bool)
    v = E.primary_verdict(a0, ef, pl_, m, GOOD_NULL, GOOD_MISS)
    assert v["pass"], v["checks"]
    small = E.primary_verdict(*_arms(eff=-0.002), m, GOOD_NULL, GOOD_MISS)
    assert not small["checks"]["primary: EF-A0 <= -0.005 and game-clustered CI upper < 0"]
    a0b, efb, _ = _arms()
    placebo_as_good = {**a0b, "crps": efb["crps"].copy()}
    assert not E.primary_verdict(a0b, efb, placebo_as_good, m, GOOD_NULL, GOOD_MISS)["checks"][
        "placebo: EF-PL <= -0.003"
    ]
    loud_null = E.null_summary([-0.03, 0.0, -0.01, 0.01, -0.02])
    assert not E.primary_verdict(a0, ef, pl_, m, loud_null, GOOD_MISS)["checks"][
        "null guard: EF-A0 <= null mean - 2 null sd"
    ]
    assert not E.primary_verdict(a0, ef, pl_, m, GOOD_NULL, {"pass": False})["pass"]
    harm = {**ef, "crps": ef["crps"].copy()}
    bench = a0["starter10"] < 0.5
    harm["crps"][bench] = a0["crps"][bench] + 0.05
    assert not E.primary_verdict(a0, harm, pl_, m, GOOD_NULL, GOOD_MISS)["checks"][
        "slices: none worse than +0.01 (n >= 300)"
    ]


def test_missingness_flags_a_pattern_beyond_its_as_of_cause() -> None:
    rng = np.random.default_rng(0)
    n = 8000
    n_prior = rng.integers(5, 80, n).astype(float)
    cold = n_prior < 8
    mins = np.where(
        cold, rng.uniform(0, 12, n), rng.uniform(0, 40, n)
    )  # cold-start players play less
    as_of = np.where(
        cold, 0.0, 10.0
    )  # ef_n < 3 exactly when n_prior < 8: explained by history length
    r = E.missingness(as_of, mins, n_prior)
    assert r["unconditional"]["max_gap"] > 0.05 and r["pass"]
    leak = np.where(mins < 5, 0.0, 10.0)  # missing whenever tonight's minutes are short
    assert not E.missingness(leak, mins, n_prior)["pass"]


def test_require_unspent_semantics(tmp_path: Path) -> None:
    E.require_unspent(tmp_path, False)
    mk = {k: "x" for k in E.MARKER_KEYS}
    E.write_marker(tmp_path, mk, 2025)
    with pytest.raises(RuntimeError, match="consumed"):
        E.require_unspent(tmp_path, False, mk)
    with pytest.raises(RuntimeError, match="differs"):
        E.require_unspent(tmp_path, True, {**mk, "head": "y"})
    E.require_unspent(tmp_path, True, mk)
    E.write_marker(tmp_path, mk, 2025)
    assert (tmp_path / "touch_resumed_1.json").exists() and json.loads(
        (tmp_path / "touch_started.json").read_text()
    )["head"] == "x"
    (tmp_path / "results.json").write_text("{}")
    with pytest.raises(RuntimeError, match="spent"):
        E.require_unspent(tmp_path, True, mk)


# --------------------------------------------------------------------------- freeze and holdout-row refusals
def test_text_sha_matches_awk(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("rule line\nmore\n" + MARK + "results\n")
    awk = subprocess.run(
        ["awk", "/^=== RESULTS BELOW ===$/{exit} {print}", str(doc)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert RULE.frozen_sha(doc) == hashlib.sha256(awk.encode()).hexdigest()


def _fake_git(doc_text: str, sha: str, log_clean: bool = True, in_log: bool = True):
    def run(*args: str) -> str:
        if args[0] == "log" and args[1] == "--format=%H%x09%s":
            return f"abc123\tprereg({RULE.ID}): {sha}\n"
        if args[0] == "show":
            return doc_text
        if args[0] == "status":
            return "" if log_clean else " M docs/HOLDOUT_ACCESS_LOG.md"
        if args[0] == "log":
            return "abc123\n" if in_log else ""
        return ""

    return run


def test_require_frozen_and_holdout_row(tmp_path: Path) -> None:
    text = "rule\n" + MARK
    doc = tmp_path / "d.md"
    doc.write_text(text)
    sha = RULE.frozen_sha(doc)
    with pytest.raises(RuntimeError, match="required"):
        RULE.require_frozen(None, doc, _fake_git(text, sha))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        RULE.require_frozen("0" * 64, doc, _fake_git(text, sha))
    with pytest.raises(RuntimeError, match="not frozen"):
        RULE.require_frozen(sha, doc, _fake_git("other\n" + MARK, sha))
    assert RULE.require_frozen(sha, doc, _fake_git(text, sha)) == sha
    log = tmp_path / "log.md"
    log.write_text(f"| {RULE.ID} | {sha[:8]} | PRE-REGISTERED. DEVIN CONFIRMED (2026-10-13) |\n")
    with pytest.raises(RuntimeError, match="his words"):
        RULE.require_holdout_row(sha, log, _fake_git(text, sha), doc)
    log.write_text(
        f'| {RULE.ID} | {sha[:8]} | PRE-REGISTERED. DEVIN CONFIRMED (2026-10-13: "run it once") |\n'
    )
    assert RULE.require_ready(sha, _fake_git(text, sha), doc, log)
    with pytest.raises(RuntimeError, match="uncommitted"):
        RULE.require_holdout_row(sha, log, _fake_git(text, sha, log_clean=False), doc)
    with pytest.raises(RuntimeError, match="not in any commit"):
        RULE.require_holdout_row(sha, log, _fake_git(text, sha, in_log=False), doc)


def test_preflight_refuses_without_a_select_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "OUT_DIR", tmp_path)
    monkeypatch.setattr(E, "CACHE", tmp_path / "cache")
    for s in E.STATS:
        EF.save_base(
            E.base_path(s, tmp_path / "cache"),
            {"row_season": np.array([2024]), "row_y": np.zeros(1)},
            2024,
        )
    meta = {
        "stats": {
            s: {"sha256": RULE.file_sha256(E.base_path(s, tmp_path / "cache"))} for s in E.STATS
        }
    }
    (tmp_path / "base_meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(E, "base_path", lambda s, d=tmp_path / "cache": d / f"base_{s}.npz")
    with pytest.raises(RuntimeError, match="no select-stage results"):
        E.preflight("a" * 64)
    (tmp_path / "select").mkdir()
    (tmp_path / "select/results.json").write_text(
        json.dumps({"sha256": "a" * 64, "primary": {"pass": False}})
    )
    with pytest.raises(RuntimeError, match="did not pass"):
        E.preflight("a" * 64)
    (tmp_path / "select/results.json").write_text(
        json.dumps({"sha256": "b" * 64, "primary": {"pass": True}})
    )
    with pytest.raises(RuntimeError, match="different sha"):
        E.preflight("a" * 64)


# --------------------------------------------------------------------------- RETURNING_TEAMMATE_LIVE_T30 gate proxy
def test_t30_proxy_counts_only_returners_who_play_and_excludes_self() -> None:
    from research.eval.returning_teammate_t30_gate import proxy_t30_columns

    r = pl.DataFrame(
        {
            "game_id": ["g", "g", "g"],
            "team_id": [1, 1, 1],
            "returner_id": [10, 11, 12],
            "streak": [3, 4, 5],
            "pre_mpg": [30.0, 12.0, 20.0],
        }
    )
    box = pl.DataFrame(
        {
            "game_id": ["g"] * 5,
            "team_id": [1, 1, 1, 1, 2],
            "player_id": [10, 11, 12, 13, 20],
            "minutes": [31.0, None, 18.0, 25.0, 30.0],
        }
    )
    out = {
        int(p): v
        for p, v in proxy_t30_columns(r, box).select("player_id", "ret_min_t30").iter_rows()
    }
    assert out[13] == 50.0  # 10 and 12 played; 11 (no minutes) is not active
    assert out[10] == 20.0 and out[12] == 30.0  # a returner's own minutes never count for himself
    assert out[11] == 50.0 and out[20] == 0.0  # other team: nobody returns
