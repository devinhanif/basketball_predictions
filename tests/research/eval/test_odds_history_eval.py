"""ODDS_HISTORY scorer (docs/prereg/ODDS_HISTORY.md): synthetic-data tests only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from scipy.stats import poisson

from research.eval import odds_history_eval as oh

DOC_TEXT = """# X

Status: DRAFT
Verify: awk ...
sha256 of the frozen section: {rec}

<!-- FROZEN-BEGIN -->
## 0. Question
rule text {extra}
<!-- FROZEN-END -->

## Amendments (append only, dated)
(none)
"""


def _doc(tmp_path: Path, rec: str = "(recorded at freeze)", extra: str = "a") -> Path:
    p = tmp_path / "ODDS_HISTORY.md"
    p.write_text(DOC_TEXT.format(rec=rec, extra=extra))
    return p


# ----------------------------------------------------------------------------- freeze guard


def test_frozen_block_hash_matches_awk_on_real_doc() -> None:
    if shutil.which("awk") is None or shutil.which("shasum") is None:
        pytest.skip("awk/shasum not available")
    cmd = (
        "awk '/<!-- FROZEN-END -->/{f=0} f{print} /<!-- FROZEN-BEGIN -->/{f=1}' "
        f"{oh.DOC} | shasum -a 256"
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True).stdout
    assert out.split()[0] == oh.frozen_sha256()


def test_freeze_guard_refuses_unfrozen_draft(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    sha = oh.frozen_sha256(doc)
    with pytest.raises(oh.RuleError, match="not frozen"):
        oh.require_frozen(sha[:8], doc)


def test_freeze_guard_accepts_matching_and_refuses_other_cases(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    sha = oh.frozen_sha256(doc)
    doc.write_text(DOC_TEXT.format(rec=sha, extra="a"))
    assert oh.require_frozen(sha[:8], doc) == sha
    with pytest.raises(oh.RuleError, match="does not match"):
        oh.require_frozen("deadbeef", doc)
    with pytest.raises(oh.RuleError, match="8 lowercase hex"):
        oh.require_frozen(sha[:7], doc)
    # editing the rule after the freeze: recorded sha no longer matches the block
    doc.write_text(DOC_TEXT.format(rec=sha, extra="changed after the freeze"))
    with pytest.raises(oh.RuleError, match="changed after the freeze"):
        oh.require_frozen(sha[:8], doc)
    # text outside the frozen block (amendments, header line) does not move the hash
    doc.write_text(DOC_TEXT.format(rec=sha, extra="a") + "\n- amendment 1\n")
    assert oh.frozen_sha256(doc) == sha


def test_score_cli_refuses_without_a_sha(tmp_path: Path) -> None:
    assert oh.main(["score", "--season", "2024", "--out-dir", str(tmp_path)]) == 2


def test_score_cli_refuses_when_rule_not_frozen(tmp_path: Path, monkeypatch: Any) -> None:
    doc = _doc(tmp_path)
    sha = oh.frozen_sha256(doc)[:8]
    real = oh.require_frozen
    monkeypatch.setattr(oh, "require_frozen", lambda s: real(s, doc))
    ran: list[int] = []
    monkeypatch.setattr(oh, "score_season", lambda *a, **k: ran.append(1))
    assert (
        oh.main(["score", "--season", "2024", "--frozen-sha", sha, "--out-dir", str(tmp_path)]) == 2
    )
    assert ran == []  # the scorer never started


# ----------------------------------------------------------------------------- holdout guard


def test_holdout_guard(tmp_path: Path) -> None:
    for s in (2023, 2024):
        oh.guard_season(s)
    for bad in (2019, 2022, 2026):
        with pytest.raises(oh.RuleError, match="outside the study"):
            oh.guard_season(bad)
    with pytest.raises(oh.RuleError, match="holdout-row"):
        oh.guard_season(2025)
    with pytest.raises(oh.RuleError, match="does not exist"):
        oh.guard_season(2025, str(tmp_path / "nope.md"))
    log = tmp_path / "log.md"
    log.write_text("| 2026-10-10 | other run | 2025 |\n")
    with pytest.raises(oh.RuleError, match="ODDS_HISTORY"):
        oh.guard_season(2025, str(log))
    log.write_text("| 2026-10-10 | ODDS_HISTORY frozen arm | 2025 | planned |\n")
    with pytest.raises(oh.RuleError, match="not committed"):
        oh.guard_season(2025, str(log), committed_check=lambda p: False)
    # even a committed row does not start a touch in this build
    with pytest.raises(NotImplementedError):
        oh.guard_season(2025, str(log), committed_check=lambda p: True)


def test_stage_clis_refuse_the_holdout(tmp_path: Path) -> None:
    for stage in ("model-rows", "market-rows", "game-rows"):
        assert oh.main([stage, "--season", "2025", "--out-dir", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------------- model side


def test_pge_matrix_and_moments() -> None:
    qi = np.tile(np.array([0, 1, 1, 2, 3], dtype=np.int16), (2, 40))[:, :199]
    p = oh.pge_matrix(qi, 4)
    assert p.shape == (2, 4)
    assert p[0, 0] == pytest.approx((qi[0] >= 1).mean())
    assert p[0, 3] == 0.0  # nothing reaches 4
    mean, std = oh.pge_moments(p)
    assert mean[0] == pytest.approx(qi[0].mean())
    assert std[0] == pytest.approx(qi[0].std())


def test_model_p_over_half_lines_and_pushes() -> None:
    # Y uniform on {8, 9, 10, 11}: P(Y >= k) for k = 1..12
    pmf = {8: 0.25, 9: 0.25, 10: 0.25, 11: 0.25}
    pge = np.array([[sum(v for y, v in pmf.items() if y >= k) for k in range(1, 13)]])
    pge = np.repeat(pge, 5, axis=0)
    line = np.array([9.5, 9.0, 9.0, 7.25, 9.5])
    y = np.array([10, 9, 11, 8, 9])
    p, over, status = oh.model_p_over(pge, line, y)
    assert p[0] == pytest.approx(0.5)  # P(Y >= 10)
    assert over.tolist() == [1.0, 0.0, 1.0, 1.0, 0.0]
    # whole line 9: P(Y>=10) / (P(Y>=10) + P(Y<=8)) = 0.5 / 0.75 ; the y == 9 row is a push
    assert p[1] == pytest.approx(2.0 / 3.0)
    assert status.tolist() == [0, 1, 0, 2, 0]


def test_model_all_mass_on_the_push_is_undefined() -> None:
    pge = np.array([[1.0] * 9 + [0.0] * 3])  # Y == 9 for sure
    p, _, status = oh.model_p_over(pge, np.array([9.0]), np.array([8]))
    assert np.isnan(p[0]) and status[0] == 3


def test_model_line_beyond_support_and_below_zero() -> None:
    pge = np.array([[0.5, 0.25, 0.0]])
    p, _, _ = oh.model_p_over(pge, np.array([40.5]), np.array([1]))
    assert p[0] == 0.0
    p, _, _ = oh.model_p_over(pge, np.array([0.5]), np.array([1]))
    assert p[0] == pytest.approx(0.5)


def test_a0_check(tmp_path: Path) -> None:
    ref = tmp_path / "cand.json"
    ref.write_text(json.dumps({"pts": {"2023": {"off": {"crps_int": 3.1528123, "n": 3}}}}))
    fr = pl.DataFrame({"game_id": ["a"], "player_id": [1], "c": [1.0], "s": [1.0]})
    ok = oh.a0_check("pts", 2023, 3.1528123 + 5e-7, 3, fr, ref)
    assert ok["ok"] and ok["delta"] == pytest.approx(5e-7)
    assert not oh.a0_check("pts", 2023, 3.1528, 3, fr, ref)["ok"]  # 4-dp rounding is not 1e-6
    assert not oh.a0_check("pts", 2023, 3.1528123, 4, fr, ref)["ok"]  # n must match


# ----------------------------------------------------------------------------- market side


def test_devig_multiplicative_and_power() -> None:
    ro, ru = np.array([0.55, 0.5238]), np.array([0.50, 0.5238])
    m = oh.devig_mult(ro, ru)
    assert m[0] == pytest.approx(0.55 / 1.05)
    assert m[1] == pytest.approx(0.5)
    p = oh.devig_power(ro, ru)
    k = np.log(p) / np.log(ro)
    assert (ro**k + ru**k).tolist() == pytest.approx([1.0, 1.0], abs=1e-9)
    assert p[1] == pytest.approx(0.5)
    assert 0 < p[0] < 1
    # the two methods differ for an unbalanced pair
    assert abs(p[0] - m[0]) > 1e-4


def test_american_prob() -> None:
    assert oh.american_prob(np.array([-110, 110, -100, 100, -200])).tolist() == pytest.approx(
        [110 / 210, 100 / 210, 0.5, 0.5, 200 / 300]
    )


def _q(rows: list[tuple[Any, ...]]) -> pl.DataFrame:
    """(game, kind, book, market, name, pid, side, point, price)."""
    ts = dt.datetime(2024, 1, 1, 12, 0)
    return pl.DataFrame(
        [
            {
                "game_id": g,
                "snapshot_kind": k,
                "snapshot_ts": ts,
                "requested_at": ts,
                "book": b,
                "market": m,
                "player_name": n,
                "player_id": pid,
                "side": s,
                "point": pt,
                "price_american": pr,
            }
            for g, k, b, m, n, pid, s, pt, pr in rows
        ],
        schema={
            "game_id": pl.String,
            "snapshot_kind": pl.String,
            "snapshot_ts": pl.Datetime("us"),
            "requested_at": pl.Datetime("us"),
            "book": pl.String,
            "market": pl.String,
            "player_name": pl.String,
            "player_id": pl.Int64,
            "side": pl.String,
            "point": pl.Float64,
            "price_american": pl.Int32,
        },
    )


def _two(g: str, b: str, pid: int, pt: float, po: int, pu: int, k: str = "t60") -> list[Any]:
    base = (g, k, b, "player_points", f"P{pid}", pid)
    return [(*base, "over", pt, po), (*base, "under", pt, pu)]


def test_main_line_is_most_balanced_and_ties_go_to_the_lower_line() -> None:
    rows = (
        _two("g1", "pinnacle", 1, 20.5, -125, 105)  # lopsided
        + _two("g1", "pinnacle", 1, 21.5, -110, -110)  # balanced -> main
        + _two("g1", "pinnacle", 1, 22.5, 120, -140)
        + _two("g1", "pinnacle", 2, 10.5, -110, -110)  # tie on balance with 9.5
        + _two("g1", "pinnacle", 2, 9.5, -110, -110)
    )
    built = oh.build_lines(_q(rows))
    mains = oh.main_lines(built["good"]).sort("player_id")
    assert mains["point"].to_list() == [21.5, 9.5]
    assert mains["n_lines_two_sided"].to_list() == [3, 2]
    t = oh.market_table(built["good"]).filter(pl.col("book") == "pinnacle").sort("player_id")
    assert t["p_over_mult"].to_list() == pytest.approx([0.5, 0.5])
    assert t["p_over_power"].to_list() == pytest.approx([0.5, 0.5])
    assert t["overround"].to_list() == pytest.approx([2 * 110 / 210 - 1] * 2)


def test_reason_counts_one_sided_unresolved_duplicates_and_odd_lines() -> None:
    rows = (
        _two("g1", "pinnacle", 1, 20.5, -110, -110)
        + [("g1", "t60", "pinnacle", "player_points", "P2", 2, "over", 5.5, -110)]  # one-sided
        + [  # unresolved name (player_id NULL), two-sided
            ("g1", "t60", "fanduel", "player_points", "Nick Name", None, "over", 8.5, -110),
            ("g1", "t60", "fanduel", "player_points", "Nick Name", None, "under", 8.5, -110),
        ]
        + _two("g1", "draftkings", 3, 7.5, -110, -110)
        + _two("g1", "draftkings", 3, 7.5, -105, -115)  # same player/line twice -> ambiguous
        + _two("g1", "draftkings", 4, 7.25, -110, -110)  # odd line
    )
    built = oh.build_lines(_q(rows))
    r = oh.reason_counts(built)["t60"]
    assert r["pinnacle"]["one_sided"] == 1 and r["pinnacle"]["quoted_two_sided"] == 1
    assert r["fanduel"]["unresolved_name"] == 1 and r["fanduel"]["quoted_two_sided"] == 0
    assert r["draftkings"]["ambiguous_duplicate"] == 1
    assert r["draftkings"]["odd_line_groups"] == 1 and r["draftkings"]["quoted_two_sided"] == 0
    names = oh.unresolved_names(built)
    assert names["player_name"].to_list() == ["Nick Name"]
    assert names["rows"].to_list() == [2]


def test_consensus_modal_line_and_median_over_all_books_at_that_line() -> None:
    rows = (
        _two("g1", "a", 1, 20.5, -120, 100)
        + _two("g1", "b", 1, 20.5, -110, -110)
        + _two("g1", "c", 1, 20.5, 100, -120)
        + _two("g1", "d", 1, 21.5, -110, -110)  # main line elsewhere
        + _two("g1", "d", 1, 20.5, -130, 110)  # but also quotes 20.5
    )
    built = oh.build_lines(_q(rows))
    t = oh.market_table(built["good"]).filter(pl.col("book") == "consensus")
    assert t["line"].to_list() == [20.5]
    assert t["n_books"].to_list() == [4]
    ps = [
        oh.devig_mult(oh.american_prob(np.array([o])), oh.american_prob(np.array([u])))[0]
        for o, u in ((-120, 100), (-110, -110), (100, -120), (-130, 110))
    ]
    assert t["p_over_mult"].to_list() == pytest.approx([float(np.median(ps))])


def test_h2h_table_pinnacle_and_consensus() -> None:
    ts = dt.datetime(2024, 1, 1)

    def row(b: str, side: str, pr: int) -> dict[str, Any]:
        return {"game_id": "g", "snapshot_kind": "t60", "snapshot_ts": ts, "book": b,
                "side": side, "price_american": pr}  # fmt: skip

    h = pl.DataFrame(
        [
            row("pinnacle", "home", -200),
            row("pinnacle", "away", 170),
            row("dk", "home", -180),
            row("dk", "away", 150),
            row("fd", "home", -150),
        ]  # fmt: skip
    )
    t = oh.h2h_table(h)
    assert sorted(t["source"].to_list()) == ["consensus", "pinnacle"]
    assert t.filter(pl.col("source") == "consensus")["n_books"].to_list() == [2]  # fd one-sided


# ----------------------------------------------------------------------------- inference


def test_log_loss_clip_and_brier() -> None:
    ll = oh.ll_vec(np.array([0.0, 1.0, 0.5]), np.array([1, 0, 1]), oh.MODEL_EPS)
    assert ll[0] == pytest.approx(-np.log(oh.MODEL_EPS))
    assert ll[1] == pytest.approx(-np.log(oh.MODEL_EPS))
    assert ll[2] == pytest.approx(np.log(2))
    assert oh.brier_vec(np.array([0.7]), np.array([1]))[0] == pytest.approx(0.09)


def test_holm_adjust() -> None:
    adj = oh.holm_adjust([0.01, 0.04, 0.03, 0.005])
    assert adj.tolist() == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert oh.holm_adjust([0.3, 0.2]).tolist() == pytest.approx([0.4, 0.4])
    assert oh.holm_adjust([0.4] * 3).tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_verdict_labels() -> None:
    assert oh.verdict(-0.0001, -0.00005, 0.001)["label"] == "indistinguishable"
    assert oh.verdict(0.0019, 0.01, 0.5)["label"] == "indistinguishable"
    assert oh.verdict(-0.004, -0.001, 0.01)["label"] == "beats_market"
    v = oh.verdict(-0.004, 0.001, 0.4)
    assert v["label"] == "market_better" and v["qualifier"] == "negative_delta_ci_not_below_0"
    v = oh.verdict(-0.004, -0.001, 0.2)
    assert v["label"] == "market_better" and "holm" in v["qualifier"]
    assert oh.verdict(0.01, 0.02, 0.0)["label"] == "market_better"
    assert oh.verdict(-0.004, -0.001, None)["label"] == "beats_market"  # untested column


def test_boot_ci_is_clustered_seeded_and_floors_p() -> None:
    rng = np.random.default_rng(1)
    gid = np.repeat(np.arange(200), 10)
    d = np.repeat(rng.normal(0, 1, 200), 10)  # perfectly clustered noise
    a = oh.boot_ci(d, gid, 1000)
    assert a == oh.boot_ci(d, gid, 1000)  # same seed, same answer
    row = oh.boot_ci(d, np.arange(d.size), 1000)  # row-level (anti-conservative)
    assert (a["hi"] - a["lo"]) > 2 * (row["hi"] - row["lo"])
    assert a["n"] == 2000 and a["n_games"] == 200
    shifted = oh.boot_ci(d - 10, gid, 1000)
    assert shifted["p"] == pytest.approx(1 / 1001)
    z = oh.boot_ci(np.zeros(50), np.arange(50), 100)
    assert z["p"] == 1.0 and z["lo"] == z["hi"] == 0.0


def test_blend_weight_fit() -> None:
    rng = np.random.default_rng(3)
    n = 20000
    z = rng.normal(0, 1, n)
    p_true = 1 / (1 + np.exp(-z))
    over = (rng.random(n) < p_true).astype(float)
    shrunk = 1 / (1 + np.exp(-0.6 * z))  # a market that under-uses the signal
    w, curve = oh.fit_blend_weight(shrunk, p_true, over)
    assert 0.5 <= w <= 1.0 and curve[w] <= curve[0.0]
    # the blend at w=0 is exactly the market, at w=1 exactly the model
    assert oh.blend_p(shrunk, p_true, 0.0) == pytest.approx(shrunk)
    assert oh.blend_p(shrunk, p_true, 1.0) == pytest.approx(p_true)
    noise = rng.random(n) * 0.9 + 0.05  # an uninformative model
    w0, _ = oh.fit_blend_weight(p_true, noise, over)
    assert w0 == 0.0
    # tie -> the smaller w
    w_t, _ = oh.fit_blend_weight(p_true, p_true, over)
    assert w_t == 0.0
    assert len(oh.W_GRID) == 21 and oh.W_GRID[0] == 0.0 and oh.W_GRID[-1] == 1.0


def test_ev_payouts() -> None:
    assert oh.payout(np.array([100, -200, 150]), np.array([1, 1, 1])).tolist() == pytest.approx(
        [1.0, 0.5, 1.5]
    )
    assert oh.payout(np.array([100, -200]), np.array([0, 0])).tolist() == [-1.0, -1.0]


# ----------------------------------------------------------------------------- leak guard


def test_leak_guard() -> None:
    tip = dt.datetime(2024, 1, 1, 20, 0)
    base = {"game_id": ["g"], "tipoff_utc": [tip]}
    ok = pl.DataFrame(
        {**base, "snapshot_kind": ["t60"], "snapshot_ts": [tip - dt.timedelta(minutes=62)]}
    )
    assert oh.leak_guard(ok)["violations"] == 0
    late = pl.DataFrame(
        {**base, "snapshot_kind": ["t60"], "snapshot_ts": [tip - dt.timedelta(minutes=58)]}
    )
    with pytest.raises(oh.RuleError, match="leak guard"):
        oh.leak_guard(late)
    t5 = pl.DataFrame(
        {**base, "snapshot_kind": ["t5"], "snapshot_ts": [tip - dt.timedelta(minutes=9)]}
    )
    assert oh.leak_guard(t5)["t60_pairs"] == 0  # t5 is later by design


# ----------------------------------------------------------------------------- pairing + score


def _world(
    tmp: Path, n_games: int = 60, seed: int = 0, informative: bool = True
) -> tuple[dict[int, pl.DataFrame], dict[int, pl.DataFrame]]:
    """Synthetic model rows + market rows for seasons 2023/2024 written under ``tmp``, plus the
    played / tips frames for the monkeypatched loaders."""
    rng = np.random.default_rng(seed)
    played: dict[int, pl.DataFrame] = {}
    tips: dict[int, pl.DataFrame] = {}
    for season in (2023, 2024):
        mrows: list[dict[str, Any]] = []
        krows: list[dict[str, Any]] = []
        prow: list[dict[str, Any]] = []
        trow: list[dict[str, Any]] = []
        for g in range(n_games):
            gid = f"{season}{g:05d}"
            tip = dt.datetime(season, 11, 1) + dt.timedelta(days=g)
            trow.append(
                {
                    "game_id": gid,
                    "home_team": 1,
                    "away_team": 2,
                    "home_pts": 100 + g % 7,
                    "away_pts": 98 + g % 5,
                    "tipoff_utc": tip,
                }  # fmt: skip
            )
            for pid in range(1, 9):
                dnp = pid == 8 and g % 5 == 0
                prow.append({"game_id": gid, "player_id": pid, "minutes": None if dnp else 25.0})
                for stat, scale in zip(oh.STATS, (4.0, 1.0, 0.7, 0.3), strict=True):
                    mu = scale * (pid + 3) * float(np.exp(rng.normal(0, 0.15)))
                    kmax = oh.PGE_KMAX[stat]
                    # the model sees a noisy mean; the world draws from the true mean
                    mu_model = mu if informative else mu * float(np.exp(rng.normal(0, 0.4)))
                    pge = poisson.sf(np.arange(0, kmax), mu_model)
                    if not dnp:
                        y = int(rng.poisson(mu))
                        mrows.append(
                            {
                                "game_id": gid,
                                "player_id": pid,
                                "stat": stat,
                                "season": season,
                                "y": float(y),
                                "p_ge": pge.tolist(),
                                "mean_int": mu_model,
                                "std_int": float(np.sqrt(mu_model)),
                                "m_recency": mu,
                                "sd_recency": float(np.sqrt(mu)),
                                "starter10": float(pid <= 5),
                                "team_game_no": g + 1,
                                "n_out_rot": float(pid % 2),
                                "has_report": 1,
                                "is_home_f": pid % 2,
                            }  # fmt: skip
                        )
                    line = float(np.floor(mu)) + (0.0 if (g + pid) % 17 == 0 else 0.5)
                    p_over = float(poisson.sf(int(np.floor(line)), mu))
                    po = float(np.clip(p_over + rng.normal(0, 0.02), 0.05, 0.95))
                    for kind in oh.KINDS:
                        off = 62 if kind == "t60" else 9
                        for book in oh.BOOKS:
                            has_price = book != "consensus"
                            krows.append(
                                {
                                    "game_id": gid,
                                    "player_id": pid,
                                    "stat": stat,
                                    "book": book,
                                    "snapshot_kind": kind,
                                    "snapshot_ts": tip - dt.timedelta(minutes=off),
                                    "requested_at": tip - dt.timedelta(minutes=off),
                                    "line": line,
                                    "price_over": -110 if has_price else None,
                                    "price_under": -110 if has_price else None,
                                    "p_over_raw": None,
                                    "p_under_raw": None,
                                    "overround": None,
                                    "p_over_mult": po,
                                    "p_over_power": po,
                                    "n_books": 3,
                                    "n_lines_two_sided": 1,
                                }  # fmt: skip
                            )
        mdf = pl.DataFrame(mrows)
        mdf.write_parquet(tmp / f"model_rows_{season}.parquet")
        (tmp / f"model_rows_{season}.meta.json").write_text(json.dumps({"a0_ok": True}))
        schema = {"price_over": pl.Int64, "price_under": pl.Int64, "p_over_raw": pl.Float64,
                  "p_under_raw": pl.Float64, "overround": pl.Float64, "n_books": pl.UInt32,
                  "n_lines_two_sided": pl.UInt32}  # fmt: skip
        pl.DataFrame(krows, schema_overrides=schema).write_parquet(
            tmp / f"market_rows_{season}.parquet"
        )
        qg = {
            "quote_groups": 1000,
            "quoted_two_sided": 990,
            "one_sided": 5,
            "ambiguous_duplicate": 0,
            "unresolved_name": 5,
            "odd_line_groups": 0,
        }
        reasons = {"t60": {"all": dict(qg)}, "t5": {"all": dict(qg)}}
        (tmp / f"market_rows_{season}.meta.json").write_text(
            json.dumps(
                {
                    "unmatched_event": {
                        "games_with_real_tip": n_games,
                        "by_kind": {"t60": 0, "t5": 0},
                    },
                    "reasons": reasons,
                    "unresolved_names": {"distinct": 3, "rows": 5, "top": []},
                    "coverage_vs_box_score": {},
                }
            )
        )
        played[season] = pl.DataFrame(prow)
        tips[season] = pl.DataFrame(trow)
        gm_rows = []
        gr_rows = []
        for t in trow:
            hw = t["home_pts"] > t["away_pts"]
            for kind in oh.KINDS:
                gm_rows.append(
                    {
                        "game_id": t["game_id"],
                        "snapshot_kind": kind,
                        "snapshot_ts": t["tipoff_utc"],
                        "source": "pinnacle",
                        "p_home_mult": 0.55,
                        "p_home_power": 0.55,
                        "n_books": 1,
                        "price_home": -120,
                        "price_away": 100,
                    }  # fmt: skip
                )
            gr_rows.append(
                {
                    "game_id": t["game_id"],
                    "game_date": t["tipoff_utc"].date(),
                    "season": season,
                    "p": 0.6,
                    "made_with_data_through": t["tipoff_utc"],
                    "tipoff_utc": t["tipoff_utc"],
                    "home_win": hw,
                }  # fmt: skip
            )
        pl.DataFrame(gm_rows).write_parquet(tmp / f"game_market_{season}.parquet")
        pl.DataFrame(gr_rows).write_parquet(tmp / f"game_rows_{season}.parquet")
    return played, tips


def test_pairing_counts_and_end_to_end_score(tmp_path: Path, monkeypatch: Any) -> None:
    played, tips = _world(tmp_path)
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    res = oh.score_season(2024, tmp_path, "x", n_boot=200)
    # every market row is accounted for: paired + dnp + no_model_row + push + odd + certain-push
    for key, c in res["pairing_counts"].items():
        assert c["market_rows"] == (
            c["paired"] + c["dnp"] + c["no_model_row"] + c["push"] + c["odd_line"]
            + c["model_push_certain"]
        ), key  # fmt: skip
        assert c["no_model_row"] == 0
    c = res["pairing_counts"]["t60/pts/pinnacle"]
    assert c["dnp"] == 12  # player 8 in games 0,5,..,55 of 60
    assert c["push"] > 0  # whole-number lines exist and their pushes are dropped
    assert c["paired"] == c["model_rows"] - c["push"]
    assert res["leak_guard"]["violations"] == 0
    # tests: H1 + H2 for the two tested books over 4 stats = 16 per snapshot kind (Holm family)
    for kind in oh.KINDS:
        fam = [
            res[h][f"{kind}/{s}/{b}"]
            for h in ("h1", "h2")
            for s in oh.STATS
            for b in oh.TESTED_BOOKS
        ]
        assert len(fam) == 16 and all(x["holm_family_size"] == 16 for x in fam)
        assert res["gates"]["all_ok"]
        assert all(x["holm_p"] >= x["delta"]["p"] - 1e-12 for x in fam)
    # an informative model against a noisy market: blend weight > 0 somewhere, w fit on 2023
    ws = [v["w"] for k, v in res["h2"].items() if v.get("n")]
    assert max(ws) > 0.0
    # untested books are present but carry no Holm p
    assert "holm_p" not in res["h1"]["t60/pts/draftkings"]
    # the select season has no H2
    res23 = oh.score_season(2023, tmp_path, "x", n_boot=100)
    assert res23["h2"] == {}
    assert res23["h1"]["t60/pts/pinnacle"]["holm_family_size"] == 8


def test_h2_blend_uses_the_frozen_select_season_weight(tmp_path: Path, monkeypatch: Any) -> None:
    played, tips = _world(tmp_path, n_games=40)
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    res = oh.score_season(2024, tmp_path, "x", n_boot=100)
    m23 = oh.load_model_rows(2023, tmp_path)
    k23 = pl.read_parquet(tmp_path / "market_rows_2023.parquet")
    p23, _ = oh.pairs_for_season(m23, k23, played[2023], tips[2023])
    c = oh.cell_arrays(p23, "pts", "pinnacle", "t60")
    w, _ = oh.fit_blend_weight(
        c["p_over_mult"].to_numpy(), c["p_model"].to_numpy(), c["over"].to_numpy()
    )
    assert res["h2"]["t60/pts/pinnacle"]["w"] == w


def test_score_refuses_model_rows_failing_the_a0_check(tmp_path: Path, monkeypatch: Any) -> None:
    played, tips = _world(tmp_path, n_games=10)
    (tmp_path / "model_rows_2024.meta.json").write_text(json.dumps({"a0_ok": False}))
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    with pytest.raises(oh.RuleError, match="A0"):
        oh.score_season(2024, tmp_path, "x", n_boot=50)


def test_leak_guard_voids_a_late_t60_snapshot_in_the_scorer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    played, tips = _world(tmp_path, n_games=10)
    k = pl.read_parquet(tmp_path / "market_rows_2024.parquet")
    k = k.with_columns(
        pl.when(pl.col("snapshot_kind") == "t60")
        .then(pl.col("snapshot_ts") + pl.duration(minutes=3))
        .otherwise(pl.col("snapshot_ts"))
        .alias("snapshot_ts")
    )
    k.write_parquet(tmp_path / "market_rows_2024.parquet")
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    with pytest.raises(oh.RuleError, match="leak guard"):
        oh.score_season(2024, tmp_path, "x", n_boot=50)


def test_h3_prefers_pinnacle_and_falls_back_to_consensus() -> None:
    ts = dt.datetime(2024, 1, 1)
    gr = pl.DataFrame(
        {"game_id": ["a", "b", "c"], "p": [0.7, 0.4, 0.6], "home_win": [True, False, True]}
    )
    gm = pl.DataFrame(
        {
            "game_id": ["a", "b", "b", "c"],
            "snapshot_kind": ["t60"] * 4,
            "snapshot_ts": [ts] * 4,
            "source": ["pinnacle", "pinnacle", "consensus", "consensus"],
            "p_home_mult": [0.6, 0.5, 0.45, 0.55],
            "p_home_power": [0.6, 0.5, 0.45, 0.55],
            "n_books": [1, 1, 4, 3],
        }
    )
    r = oh.h3_games(gr, gm, n_boot=100)
    assert (r["n"], r["n_pinnacle"], r["n_consensus_fallback"]) == (3, 2, 1)
    exp_m = oh.ll_vec(np.array([0.7, 0.4, 0.6]), np.array([1, 0, 1]), 1e-9).mean()
    assert r["ll_model"] == pytest.approx(float(exp_m))
    assert r["ll_market"] == pytest.approx(
        float(oh.ll_vec(np.array([0.6, 0.5, 0.55]), np.array([1, 0, 1]), 1e-9).mean())
    )


def test_clv_agreement_direction() -> None:
    def cell(lines: list[float], p_mkt: list[float], p_model: list[float]) -> pl.DataFrame:
        n = len(lines)
        return pl.DataFrame(
            {
                "game_id": [f"g{i}" for i in range(n)],
                "player_id": [1] * n,
                "line": lines,
                "p_over_mult": p_mkt,
                "p_model": p_model,
            }  # fmt: skip
        )

    c60 = cell([10.5, 10.5, 10.5, 10.5], [0.5, 0.5, 0.5, 0.5], [0.6, 0.4, 0.6, 0.6])
    c5 = cell([11.5, 11.5, 10.5, 10.5], [0.5, 0.5, 0.55, 0.5], [0.0] * 4)
    r = oh.clv_cell(c60, c5, n_boot=50)
    # row0: model over, line up -> agree; row1: model under, line up -> disagree;
    # row2: model over, same line, price up -> agree; row3: no move -> excluded
    assert r["n"] == 3 and r["n_line_moved"] == 2
    assert r["agree_rate"] == pytest.approx(2 / 3)


def test_ev_cell_edge_filter_and_return() -> None:
    c = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "p_over_mult": [0.5, 0.5, 0.5, 0.5],
            "over": [1.0, 0.0, 1.0, 0.0],
            "price_over": [100, 100, -110, -110],
            "price_under": [-120, -120, -110, -110],
        }
    )
    p_use = np.array([0.56, 0.56, 0.52, 0.40])  # bet over, over, none, under
    r = oh.ev_cell(c, p_use, 0.03, n_boot=50)
    assert (r["n"], r["n_over"], r["n_under"]) == (3, 2, 1)
    # a: +1.0 ; b: -1.0 ; d (under at -110, under won): +100/110
    assert r["mean_return"]["point"] == pytest.approx((1.0 - 1.0 + 100 / 110) / 3)
    assert oh.ev_cell(c, np.full(4, 0.5), 0.03, n_boot=50) == {"n": 0}


def test_render_report_smoke(tmp_path: Path, monkeypatch: Any) -> None:
    played, tips = _world(tmp_path, n_games=30)
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    results = {}
    for s in (2023, 2024):
        r = oh.score_season(s, tmp_path, "x", n_boot=50)
        r["h3"] = oh.score_games(s, tmp_path, 50)
        results[s] = json.loads(json.dumps(r, default=oh._jd))
    md = oh.render_report(results, hashlib.sha256(b"x").hexdigest())
    for needle in (
        "### H1 props skill, t60",
        "### H3 games",
        "Proposed ledger rows",
        "Season 2024",
    ):
        assert needle in md


def test_a_failed_minimum_data_check_stops_with_no_model_result(
    tmp_path: Path, monkeypatch: Any
) -> None:
    played, tips = _world(tmp_path, n_games=10)
    meta = json.loads((tmp_path / "market_rows_2024.meta.json").read_text())
    meta["reasons"]["t60"]["all"]["unresolved_name"] = 100  # 90% resolved < 97%
    (tmp_path / "market_rows_2024.meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    res = oh.score_season(2024, tmp_path, "x", n_boot=50)
    assert res["stopped"] and "h1" not in res
    bad = [c for c in res["gates"]["checks"] if not c["ok"]]
    assert [c["check"][0] for c in bad] == ["3"]
    md = oh.render_report({2024: json.loads(json.dumps(res, default=oh._jd))}, "ab" * 32)
    assert "STOPPED" in md and "H1 props skill" not in md


def test_holm_family_is_fixed_by_the_rule_even_when_a_cell_is_missing() -> None:
    cell = {"n": 10, "delta": {"p": 0.001, "point": -0.01, "lo": -0.02, "hi": -0.005}}
    res: dict[str, Any] = {"h1": {"t60/pts/pinnacle": dict(cell)}, "h2": {}}
    oh.apply_multiplicity(res, 2024)
    assert res["h1"]["t60/pts/pinnacle"]["holm_p"] == pytest.approx(0.016)  # 16 x 0.001
    assert res["h1"]["t60/pts/pinnacle"]["holm_family_size"] == 16


def test_score_cli_happy_path_on_a_frozen_synthetic_rule(tmp_path: Path, monkeypatch: Any) -> None:
    played, tips = _world(tmp_path, n_games=30)
    (tmp_path / "rule").mkdir()
    doc = _doc(tmp_path / "rule", extra="frozen")
    sha = oh.frozen_sha256(doc)
    doc.write_text(DOC_TEXT.format(rec=sha, extra="frozen"))
    real = oh.require_frozen
    monkeypatch.setattr(oh, "require_frozen", lambda s: real(s, doc))
    monkeypatch.setattr(oh, "load_played", lambda s, d="x": played[s])
    monkeypatch.setattr(oh, "load_tips", lambda s, d="x": tips[s])
    monkeypatch.setattr(oh, "REPORT_MD", tmp_path / "report.md")
    rc = oh.main(
        [
            "score",
            "--season",
            "2023",
            "--frozen-sha",
            sha[:8],
            "--out-dir",
            str(tmp_path),
            "--n-boot",
            "50",
        ]  # fmt: skip
    )
    assert rc == 0
    rc = oh.main(
        [
            "score",
            "--season",
            "2024",
            "--frozen-sha",
            sha[:8],
            "--out-dir",
            str(tmp_path),
            "--n-boot",
            "50",
        ]  # fmt: skip
    )
    assert rc == 0
    res = json.loads((tmp_path / "results_2024.json").read_text())
    assert res["frozen_sha256"] == sha and res["h3"]["n"] == 30
    md = (tmp_path / "report.md").read_text()
    assert sha in md and "Season 2023" in md and "Season 2024" in md
