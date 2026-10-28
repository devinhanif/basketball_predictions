# mypy: disable-error-code="no-untyped-call,attr-defined,unused-ignore"
# ruff: noqa: E501
"""LINEUPS_KNOWN_REBASE harness: freeze refusal, pess2 and P2S construction on a toy frame, scored row sets equal A0 / R by key hash (L5),
the shuffle keeps five per team-game, plants non-inert, the served walks are the plain walks when nothing is served, the tip cap, the
reading rule, and the synthetic dryrun end to end."""

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
from research.eval import lineups_known_rebase as E
from research.eval import recent_role_eval as RR

CFG = ContextResidualConfig(n_estimators=8, min_cal_rows=300)


def _git_with(subject: str, blob: str) -> Any:
    def git(*a: str) -> str:
        if a[0] == "log" and a[1].startswith("--format=%H%x09"):
            return f"abc\t{subject}\n"
        return blob if a[0] == "show" else ""

    return git


def test_require_frozen_sha8_subject_and_committed_copy(tmp_path: Path) -> None:
    body = "rule\n"
    p = tmp_path / "d.md"
    p.write_text(body + "=== RESULTS BELOW ===\n")
    sha = hashlib.sha256(body.encode()).hexdigest()
    assert (
        E.require_frozen(
            sha, p, _git_with(f"prereg(LINEUPS_KNOWN_REBASE): {sha[:8]} FROZEN", p.read_text())
        )
        == sha
    )
    with pytest.raises(RuntimeError, match="no commit"):
        E.require_frozen(sha, p, _git_with(f"prereg(WORKLOAD_PTS_V2): {sha[:8]}", p.read_text()))
    with pytest.raises(RuntimeError, match="no commit"):
        E.require_frozen(
            sha,
            p,
            _git_with(f"prereg(LINEUPS_KNOWN_REBASE): {sha[:8]}", "x\n=== RESULTS BELOW ===\n"),
        )
    assert E.frozen_sha(E.DOC).startswith("876e5804")  # a48ef7b3 + Amendment 1


def test_amendment1_pins_and_canonical_hash_ignore_float_jitter_only() -> None:
    assert (
        E.PROPS_PIN.startswith("231e8648")
        and E.PPLAY_PIN.startswith("372dacf3")
        and E.VAC_DECIMALS == 9
    )
    df = pl.DataFrame(
        {
            "game_id": ["a", "b"],
            "player_id": [1, 2],
            E.VAC: [7.123456789012, 3.5],
            "t30_starter": [1.0, 0.0],
        }
    )
    cols = [E.VAC, "t30_starter"]
    h = E.frame_sha(E.canon(df), cols)
    jit = df.with_columns(pl.col(E.VAC) + 2.8e-14)
    assert E.frame_sha(jit, cols) != E.frame_sha(df, cols)  # the raw recipe moves on the jitter
    assert E.frame_sha(E.canon(jit), cols) == h  # the amended recipe does not
    assert (
        E.frame_sha(E.canon(df.with_columns(pl.col(E.VAC) + 1e-6)), cols) != h
    )  # a real change still moves it
    assert (
        E.frame_sha(E.canon(df.with_columns(pl.col("t30_starter") + 1e-14)), cols) != h
    )  # no other column is rounded
    assert E.canon(df.drop(E.VAC)).equals(df.drop(E.VAC))


def test_plant_check_tolerates_the_vac_jitter_only_through_canon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g, b = _toy()
    real = E.t30_cols
    calls = {"n": 0}

    def jittered(games: pl.DataFrame, pgs: pl.DataFrame, prefix: str = "") -> pl.DataFrame:
        calls["n"] += 1
        return real(games, pgs, prefix).with_columns(pl.col(prefix + E.VAC) + calls["n"] * 1e-14)

    monkeypatch.setattr(E, "t30_cols", jittered)
    g2 = pl.concat(
        [
            g,
            g.with_columns(
                pl.lit(2024, dtype=pl.Int64).alias("season"),
                (pl.col("game_date") + dt.timedelta(days=400)),
                (pl.col("game_id") + "x").alias("game_id"),
            ),
        ]
    )
    b2 = pl.concat([b, b.with_columns((pl.col("game_id") + "x").alias("game_id"))])
    t = E.plants(g2, b2)
    assert t[0]["ok"], t[0]
    assert (
        t[0]["value"]["by_column"][E.VAC]["edited_max_abs_diff"] > 0
    )  # the raw comparison would have failed


# --------------------------------------------------------------------------- toy box score
def _toy() -> tuple[pl.DataFrame, pl.DataFrame]:
    """One team (1) vs (2), five games; players 10-14 start games 0-3. Game 4: 10 is a dressed DNP (late), 11 has no box row (late via a pre-tip
    row), 12 was OUT at T-60, 15 and 16 replace them; 17 starts too (replacing 12)."""
    games, rows = [], []
    for gi in range(5):
        gid = f"0022300{gi:03d}"
        games.append((gid, dt.date(2023, 11, 1) + dt.timedelta(days=2 * gi), 2023, 1, 2))
        for p in range(10, 20):
            if gi == 4 and p in (11, 12):
                continue
            st = p in (10, 11, 12, 13, 14) if gi < 4 else p in (13, 14, 15, 16, 17)
            mins = 0.0 if (gi == 4 and p == 10) else (30.0 if st else 12.0)
            rows.append((gid, p, 1, mins, 10, 5, 3, 1, st))
        for p in range(20, 25):
            rows.append((gid, p, 2, 30.0, 10, 5, 3, 1, True))
    g = pl.DataFrame(
        games,
        schema={
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "season": pl.Int64,
            "home_team": pl.Int64,
            "away_team": pl.Int64,
        },
        orient="row",
    )
    b = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "minutes": pl.Float64,
            "pts": pl.Int64,
            "reb": pl.Int64,
            "ast": pl.Int64,
            "fg3m": pl.Int64,
            "starter": pl.Boolean,
        },
        orient="row",
    )
    return g, b


def test_late_resolved_categories_follow_the_rule() -> None:
    g, b = _toy()
    late = E.late_resolved(
        g, b, {"0022300004": {12}}, pl.DataFrame({"game_id": ["0022300004"], "player_id": [11]})
    )
    cats = dict(zip(late["player_id"].to_list(), late["cat"].to_list(), strict=True))
    assert cats == {
        10: "late_resolved_dressed_dnp",
        11: "late_resolved_nobox_pretip_row",
        12: "out_t60",
    }
    late2 = E.late_resolved(
        g, b, {"0022300004": {12}}, pl.DataFrame(schema={"game_id": pl.Utf8, "player_id": pl.Int64})
    )
    assert (
        dict(zip(late2["player_id"].to_list(), late2["cat"].to_list(), strict=True))[11]
        == "excluded_nobox_no_pretip_row"
    )


def test_pess2_promotes_late_starters_demotes_fewest_prior_starts_and_adds_no_scored_row() -> None:
    g, b = _toy()
    late = E.late_resolved(
        g, b, {"0022300004": {12}}, pl.DataFrame({"game_id": ["0022300004"], "player_id": [11]})
    )
    p2 = E.pess2_pgs(g, b, late)
    tonight = p2.filter((pl.col("game_id") == "0022300004") & (pl.col("team_id") == 1))
    st = set(tonight.filter(pl.col("starter"))["player_id"].to_list())
    # 10 (dressed DNP) and 11 (added DNP row) promoted; 15, 16, 17 have 0 prior starts -> the two lowest ids (15, 16) demoted; 13, 14 stay
    assert st == {10, 11, 13, 14, 17}
    added = tonight.filter(pl.col("player_id") == 11)
    assert added.height == 1 and added["minutes"].item() == 0.0
    assert (
        p2.filter(pl.col("team_id") == 1)
        .group_by("game_id")
        .agg(pl.col("starter").sum())["starter"]
        .to_list()
        == [5] * 5
    )
    t_h, t_p2 = E.t30_cols(g, b), E.t30_cols(g, p2, E.P2)
    assert t_p2.height == t_h.height + 1  # the added DNP row
    scored = b.filter(pl.col("minutes") > 0).select(E.KEYS)  # played rows = the A0-style scored set
    j = scored.join(t_h, on=E.KEYS, how="left").join(t_p2, on=E.KEYS, how="left")
    assert E.key_hash(j["game_id"].to_numpy(), j["player_id"].to_numpy()) == E.key_hash(
        scored["game_id"].to_numpy(), scored["player_id"].to_numpy()
    )
    row = j.filter((pl.col("game_id") == "0022300004") & (pl.col("player_id") == 15))
    assert row["t30_starter"].item() == 1.0 and row["p2_t30_starter"].item() == 0.0


def test_shuffle_keeps_five_per_team_game_and_moves_identities() -> None:
    g, b = _toy()
    sh = E.shuffle_pgs(b, seed=0)
    assert (
        sh.group_by(["game_id", "team_id"]).agg(pl.col("starter").sum())["starter"].sort().to_list()
        == b.group_by(["game_id", "team_id"])
        .agg(pl.col("starter").sum())["starter"]
        .sort()
        .to_list()
    )
    j = b.join(sh.select(*E.KEYS, pl.col("starter").alias("s2")), on=E.KEYS)
    assert (j["starter"] != j["s2"]).sum() > 0 and sh.height == b.height


def test_key_hash_is_order_free() -> None:
    a = E.key_hash(np.array(["a", "b"]), np.array([1, 2]))
    assert (
        a
        == E.key_hash(np.array(["b", "a"]), np.array([2, 1]))
        != E.key_hash(np.array(["a", "b"]), np.array([1, 3]))
    )


# --------------------------------------------------------------------------- walks
@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    s = E.synthetic_inputs(days=40)
    return E.assemble(
        s["games"], s["pgs"], s["props"], s["pplay"], s["flagged"], s["pretip"], CFG, CFG, {}
    )


def test_served_heads_walk_equals_plain_walk_when_served_columns_are_identical_and_differs_otherwise(
    world: dict[str, Any],
) -> None:
    fr = world["props"]
    same = fr.with_columns([pl.col(c).alias(E.P2 + c) for c in E.T30])
    a = E.heads_walk(same, "pts", CFG, "T30", log=False)
    b = E.heads_walk(same, "pts", CFG, "T30_P2S", log=False)
    for k in ("row_c", "row_s", "cal_z", "cal_c", "cal_s"):
        assert np.array_equal(a[k], b[k]), k
    c = E.heads_walk(fr, "pts", CFG, "T30_P2S", log=False)
    assert np.array_equal(a["cal_z"], c["cal_z"])  # training rows / calibration read headline flags
    assert not np.array_equal(a["row_c"], c["row_c"])  # test rows served pess2 flags


def test_served_pplay_walk_is_bit_identical_to_recent_role_walk(world: dict[str, Any]) -> None:
    fr = world["pplay"]
    arms = {k: v for k, v in E.pplay_arms().items() if k in ("R", "RT")}
    mine = E.pplay_walk_served(fr, arms, {}, CFG)
    ref, _ = RR.walk(fr, arms, CFG)
    for k in ("p_R", "p_RT", "y", "gid"):
        assert np.array_equal(mine[k], ref[k]), k
    served = E.pplay_walk_served(
        fr, {"RT_P2S": E.pplay_arms()["RT_P2S"]}, {"RT_P2S": E.SERVE_P2}, CFG
    )
    assert not np.array_equal(served["p_RT_P2S"], mine["p_RT"])


def test_plants_are_as_of_and_non_inert(world: dict[str, Any]) -> None:
    t = E.plants(world["games"], world["pgs"])
    assert (
        all(x["ok"] for x in t) and t[0]["value"]["n_rows"] > 0 and t[1]["value"]["n_changed"] > 0
    )


def test_tip_cap_never_opens_a_later_schedule_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.features import game_tipoff as GT

    for name in ("raw_2023-24", "raw_2025-26", "live_tips_2026-27"):
        pl.DataFrame(
            {
                "gameId": [name],
                "gameDateTimeUTC": ["2025-01-01T00:00:00Z"],
                "gameDateTimeEst": ["2024-12-31T19:00:00Z"],
            }
        ).write_parquet(tmp_path / f"{name}.parquet")
    opened: list[str] = []
    real = pl.read_parquet

    def rec(f: Any, *a: Any, **k: Any) -> pl.DataFrame:
        opened.append(Path(f).stem)
        return real(f, *a, **k)

    monkeypatch.setattr(GT.pl, "read_parquet", rec)
    monkeypatch.setattr(GT, "SCHEDULE_DIR", tmp_path)
    with E.tip_cap() as calls:
        rows = pl.DataFrame({"game_id": ["raw_2023-24"]})
        out = GT.attach_real_tips(rows)
        with pytest.raises(RuntimeError, match="holdout"):
            GT.attach_real_tips(rows, max_season=2025)
    assert opened == ["raw_2023-24"] and calls == [None, 2025] and out["tip_et"].null_count() == 0


# --------------------------------------------------------------------------- reading rule
def _cell(d: float, hi: float | None = None) -> dict[str, Any]:
    return {
        "delta": d,
        "ci_lo": d - 0.002,
        "ci_hi": d + 0.002 if hi is None else hi,
        "date_ci": [d - 0.002, d + 0.002],
        "n_dates": 200,
        "n": 27000,
    }


def _props(h: float, p2: float, ps: float, n2: tuple[float, float, float]) -> dict[str, Any]:
    n1 = {str(s): {"headline": _cell(h), "pess2": _cell(p2), "P2S": _cell(ps)} for s in E.SEASONS}
    n2d = {
        str(s): {"headline": _cell(n2[0]), "pess2": _cell(n2[1]), "P2S": _cell(n2[2])}
        for s in E.SEASONS
    }
    return {
        "N1": n1,
        "N2": n2d,
        "shuffle_T30SH_minus_P0": {str(s): _cell(-0.0003) for s in E.SEASONS},
    }


def _pplay(b: float) -> dict[str, Any]:
    return {
        "N4": {
            str(s): {w: {"bench_tonight": _cell(b)} for w in ("headline", "pess2", "P2S")}
            for s in E.SEASONS
        },
        "shuffle_RT_SH_minus_R": {str(s): _cell(0.0) for s in E.SEASONS},
    }


def test_reading_rule_each_branch() -> None:
    r = E.reading(
        {
            "pts": _props(-0.045, -0.023, -0.018, (-0.03, -0.012, -0.010)),
            "reb": _props(-0.015, -0.008, -0.007, (-0.004, -0.001, -0.001)),
            "fg3m": _props(-0.004, -0.002, -0.003, (-0.006, -0.003, -0.006)),
        },
        _pplay(-0.010),
    )
    assert (
        r["N1"]["pts"]["range_2024"] == [-0.018, -0.045]
        and r["N1"]["pts"]["low_end_way"] == "P2S"
        and r["N1"]["pts"]["literal_numeric_min_pess2_P2S"] == -0.023
    )
    assert (
        r["N1"]["pts"]["tripwire_may_fire_on_honest_data"]
        and not r["N1"]["reb"]["tripwire_may_fire_on_honest_data"]
    )
    assert r["N1"]["fg3m"]["expected_to_miss_floor"] and "l60_power_at_low_end" in r["N1"]["reb"]
    assert (
        r["N2"]["pts"].startswith("the starting five adds")
        and r["N2"]["reb"].startswith("the offline T-30 gain over FULL is below")
        and r["N2"]["fg3m"].startswith("conditional")
    )
    assert (
        r["N4"].startswith("recommend drafting")
        and E.reading({"pts": _props(-0.04, -0.02, -0.02, (-0.03, -0.01, -0.01))}, _pplay(-0.004))[
            "N4"
        ]
        == "no T-30 P(play) work"
    )
    assert not r["flags"]["starter_shuffle"]["raised"]
    sh = _props(-0.04, -0.02, -0.02, (-0.03, -0.01, -0.01))
    sh["shuffle_T30SH_minus_P0"]["2024"] = _cell(-0.003, hi=-0.001)
    assert E.reading({"pts": sh}, _pplay(0.0))["flags"]["starter_shuffle"]["raised"]


def test_l60_power_matches_forward_section_6_style() -> None:
    p = E.l60_power(
        -0.023, 0.0064 * np.sqrt(0.85) / np.sqrt(212 / 60), 212
    )  # FORWARD: SE @60 dates 0.0064 for T30 pts
    assert abs(p["se_L60"] - 0.0064) < 1e-9 and abs(p["forward_style_power"] - 0.89) < 0.01


# --------------------------------------------------------------------------- stops and end to end
def test_gate_stops_on_a_pin_mismatch_before_any_fit(tmp_path: Path) -> None:
    ctx = E.dry_ctx(tmp_path)
    ctx.props_pin = "0" * 64
    g = E.run_gate(ctx)
    assert (
        not g["gate_ok"] and "pin" in g["stop"] and not list((tmp_path / "cache").glob("heads_*"))
    )
    with pytest.raises(RuntimeError, match="gate has not passed"):
        E.run_arms(ctx)


def test_dryrun_end_to_end(tmp_path: Path) -> None:
    ctx = E.dry_ctx(tmp_path)
    assert E.run_all(ctx) == 0
    res = json.loads((tmp_path / "results.json").read_text())
    assert set(res["props"]) == set(E.STATS) and res["descriptive"]
    for st in E.STATS:
        for s in ("2023", "2024"):
            assert set(res["props"][st]["N1"][s]) == {"headline", "pess2", "P2S", "excl-late"}
            assert set(res["props"][st]["N3"][s]) == {"headline", "pess2", "P2S", "excl-late"}
    assert "bridge_continuous_T30_minus_P0_2024" in res["props"]["pts"]
    assert (
        set(res["pplay"]["N4"]["2024"]) == {"headline", "pess2", "P2S", "excl-late"}
        and res["pplay"]["l5_walk_rows_equal_R_rows"]
    )
    hl = res["props"]["pts"]["N1"]["2024"]["headline"]
    assert (
        hl["delta"] < 0 and hl["n"] > 0
    )  # synthetic minutes follow tonight's flag: the T-30 arm must see it
    assert (tmp_path / "report.md").exists()


# --------------------------------------------------------------------------- second review 2026-10-27 (M1, m3, m4, m6, m7)
def test_M1_pi_mismatch_with_identical_heads_inputs_is_a_code_defect_stop(tmp_path: Path) -> None:
    ctx = E.dry_ctx(tmp_path)
    assert ctx.full_pi is not None
    ctx.full_pi = ctx.full_pi + 1e-3
    g = E.run_gate(ctx)
    assert not g["gate_ok"] and "FULL pi" in g["stop"] and "code defect" in g["stop"]
    t = [x for x in g["tests"] if "FULL pi refit" in x["test"]]
    assert t and t[0]["value"]["pi_mismatch_with_identical_heads_inputs"]
    assert not (tmp_path / "cache" / "pi_FULL.npz").exists()


def test_m3_training_only_change_refuses_stale_checkpoints_without_touching_the_pins(
    tmp_path: Path,
) -> None:
    ctx = E.dry_ctx(tmp_path)
    assert E.run_gate(ctx)["gate_ok"]
    g0 = json.loads((tmp_path / "gate.json").read_text())
    assert set(E.STAMP_KEYS) <= set(g0) and g0["harness_git_hash"] == E.harness_hash()
    E.run_arms(ctx)
    z = np.load(tmp_path / "cache" / "heads_pts_T30.npz")
    assert str(z["_stamp_props_all_sha256"]) == g0["props_all_sha256"]
    w = ctx.world()
    props = w["props"]
    changed = props.with_columns(
        pl.when(pl.col("season") == 2022)
        .then(pl.col("t30_n_changed") + 1)
        .otherwise(pl.col("t30_n_changed"))
        .alias("t30_n_changed")
    )
    w2 = {**w, "props": changed}
    ctx2 = E.Ctx(**{**ctx.__dict__, "_w": None, "world_fn": lambda: w2, "stamp": {}})
    with pytest.raises(RuntimeError, match="stamp differs"):
        E.require_gate(ctx2)  # the old gate.json no longer matches the rebuilt world
    g2 = E.run_gate(ctx2)
    assert (
        g2["gate_ok"] and g2["pins"]["props_sha256"] == g0["pins"]["props_sha256"]
    )  # scored-row pin unchanged
    assert g2["props_all_sha256"] != g0["props_all_sha256"]
    with pytest.raises(RuntimeError, match="stale checkpoint"):
        E.run_arms(ctx2)


def test_m7_overlap_draws_are_workload_eval_ci_draws() -> None:
    rng = np.random.default_rng(1)
    gid = np.repeat(np.array([f"g{i:03d}" for i in range(300)]), 7)
    x = rng.normal(0.01, 0.3, len(gid))
    inv, w = E.boot_weights(gid)
    g = w.shape[1]
    s, c = np.bincount(inv, x, g), np.bincount(inv, minlength=g).astype(float)
    means = (w @ s) / np.maximum(w @ c, 1.0)
    ref = E.WE._ci(x, gid)
    assert (
        float(np.percentile(means, 2.5)) == ref["lo"]
        and float(np.percentile(means, 97.5)) == ref["hi"]
    )


def test_m4_m6_report_carries_the_supersede_sentence_and_pplay_l5_is_asserted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = E.dry_ctx(tmp_path)
    assert E.run_all(ctx) == 0
    md = (tmp_path / "report.md").read_text()
    assert E.SUPERSEDE_NOTE in md and "is not edited" in md and "Range low end" in md
    arms = dict(np.load(tmp_path / "cache" / "pplay_arms.npz", allow_pickle=True))
    cut: dict[str, Any] = {
        k: (v[1:] if isinstance(v, np.ndarray) and v.shape[:1] == arms["gid"].shape else v)
        for k, v in arms.items()
    }
    np.savez_compressed(tmp_path / "cache" / "pplay_arms.npz", **cut)
    with pytest.raises(RuntimeError, match="L5"):
        E.score_pplay(
            ctx,
            json.loads((tmp_path / "gate.json").read_text()),
            ctx.world()["pplay"].select(
                *E.KEYS, "game_date", "late_tg", "t30_starter", "t30_start_delta", "p2_t30_starter"
            ),
        )
