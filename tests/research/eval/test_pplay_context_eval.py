"""PPLAY_CONTEXT harness (docs/prereg/PPLAY_CONTEXT.md, DRAFT): terms, gate checks, plants, fits.

Synthetic league only (no real database, no season 2025). The gate checks on the real data are
run by the maintainer's command; here each check is proven on data where the answer is known,
including one injected leak that the planted test must catch.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.cache import insert_rows
from nba.props.context_residual import ContextResidualConfig
from research.eval import pplay_context_eval as m

TEAMS, PER_TEAM = 6, 8


def make_league(
    seed: int = 0, seasons: tuple[int, ...] = (2022, 2023, 2024), days: int = 26
) -> dict:
    rng = np.random.default_rng(seed)
    grows: list[dict[str, Any]] = []
    n = 0
    for season in seasons:
        start = dt.date(season, 10, 20)
        for d in range(days):
            day = start + dt.timedelta(days=d)
            if rng.random() < 0.2:
                continue
            order = rng.permutation(np.arange(1, TEAMS + 1))
            for k in range(int(rng.integers(2, 4))):
                n += 1
                grows.append(
                    {
                        "game_id": f"002{season % 100:02d}{n:05d}",
                        "game_date": day,
                        "season": season,
                        "home_team": int(order[2 * k]),
                        "away_team": int(order[2 * k + 1]),
                        "home_pts": int(rng.integers(90, 130)),
                        "away_pts": int(rng.integers(90, 130)),
                    }
                )
    games = pl.DataFrame(grows).sort(["game_date", "game_id"])
    last: dict[int, dt.date] = {}
    gis: dict[tuple[int, int], int] = defaultdict(int)
    prow: list[dict[str, Any]] = []
    srow: list[dict[str, Any]] = []
    for g in games.to_dicts():
        day = g["game_date"]
        tip = dt.datetime.combine(day, dt.time(19, 30))
        teams = (g["home_team"], g["away_team"])
        ids: list[int] = []
        for team in teams:
            b2b = last.get(team) == day - dt.timedelta(days=1)
            gis[(team, g["season"])] += 1
            for k in range(PER_TEAM):
                pid = team * 100 + k
                ids.append(pid)
                bm, bp = (34.0, 25.0) if k < 2 else (26.0, 12.0) if k < 5 else (12.0, 4.0)
                plays = rng.random() < (0.55 if (k < 2 and b2b) else 0.9)
                prow.append(
                    {
                        "game_id": g["game_id"],
                        "player_id": pid,
                        "team_id": team,
                        "game_date": day,
                        "season": g["season"],
                        "minutes": max(1.0, float(rng.normal(bm, 3))) if plays else 0.0,
                        "pts": float(rng.poisson(bp)) if plays else 0.0,
                        "b2b": b2b,
                        "games_into_season": gis[(team, g["season"])],
                    }
                )
        for team in teams:
            last[team] = day
        stat = ["questionable", "probable", "available", "doubtful"]
        for at, cnt in ((dt.time(11, 0), 4), (dt.time(17, 45), 5)):
            for pid in rng.choice(ids, size=cnt, replace=False):
                srow.append(
                    {
                        "game_id": g["game_id"],
                        "player_id": int(pid),
                        "status": str(rng.choice(stat)),
                        "as_of": dt.datetime.combine(day, at),
                        "game_date": day,
                        "tip_et": tip,
                    }
                )
        if rng.random() < 0.6:
            for pid in rng.choice(ids, size=3, replace=False):
                srow.append(
                    {
                        "game_id": g["game_id"],
                        "player_id": int(pid),
                        "status": str(rng.choice(stat)),
                        "as_of": dt.datetime.combine(day - dt.timedelta(days=1), dt.time(17, 45)),
                        "game_date": day,
                        "tip_et": tip,
                    }
                )
    pgs_full = pl.DataFrame(prow)
    pgs = pgs_full.select("game_id", "player_id", "team_id", "minutes", "pts").with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("team_id").cast(pl.Int64)
    )
    snaps = pl.DataFrame(srow).with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("tip_et").cast(pl.Datetime("us")),
        pl.col("game_date").cast(pl.Date),
    )
    base = pgs_full.with_columns(
        (pl.col("minutes") > 0).cast(pl.Float64).alias("played"),
        pl.col("minutes").alias("min_real"),
        pl.Series("p_platt", rng.uniform(0.2, 0.95, pgs_full.height)),
        pl.Series("p_raw", rng.uniform(0.2, 0.95, pgs_full.height)),
        *(pl.Series(c, rng.normal(0, 1, pgs_full.height)) for c in _plain_features()),
        pl.col("season").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
        pl.col("player_id").cast(pl.Int64),
    )
    prior_wp = m.prior_win_pct(games, None)
    terms = m.build_terms(
        base.select("game_id", "player_id", "team_id", "season", "game_date"),
        games,
        pgs,
        snaps,
        prior_wp,
    )
    frame = m.permute_team_game_values(base.join(terms, on=m.KEYS, how="left"))
    return {
        "games": games,
        "pgs": pgs,
        "snaps": snaps,
        "prior_wp": prior_wp,
        "base": base,
        "frame": frame,
        "rows": base.select("game_id", "player_id", "team_id", "season", "game_date"),
    }


def _plain_features() -> list[str]:
    skip = {"b2b", "games_into_season"}
    return [c for c in m.BASE_FEATURES if c not in skip]


@pytest.fixture(scope="module")
def league() -> dict:
    return make_league()


# --------------------------------------------------------------------------- freeze + CLI


def test_hash_cli_refusals_and_redirects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PREREG", "OUT_DIR", "REPORT_PATH", "CACHE"):
        monkeypatch.setattr(m, name, getattr(m, name))
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "PPLAY_CONTEXT.md"
    doc.write_text("rule line\n=== RESULTS BELOW ===\nnumbers\n")
    m.configure(doc)
    assert Path("reports/prereg_pplay_context") == m.OUT_DIR
    assert Path("data/pplay_context") == m.CACHE
    sha = hashlib.sha256(b"rule line\n").hexdigest()
    assert m.require_frozen(sha, False) == sha
    assert m.require_frozen(None, True) == sha
    with pytest.raises(SystemExit):
        m.require_frozen("0" * 64, False)
    with pytest.raises(SystemExit):
        m.require_frozen("abc", False)
    with pytest.raises(SystemExit):
        m.require_frozen(None, False)
    with pytest.raises(SystemExit):
        m.main(["run", "--doc", str(doc), "--allow-draft"])
    with pytest.raises(SystemExit):
        m.main(["smoke", "--doc", str(doc), "--sha", "1" * 64])
    with pytest.raises(SystemExit):
        m.main(["gate", "--doc", str(doc)])


def test_default_doc_and_real_draft_hash_is_stable() -> None:
    assert Path("docs/prereg/PPLAY_CONTEXT.md") == m.DEFAULT_DOC
    assert len(m.frozen_sha(m.DEFAULT_DOC)) == 64


# --------------------------------------------------------------------------- terms


def _naive(lg: dict, window: int, min_b2b: int, k: float = 20.0):
    games = lg["games"].sort(["game_date", "game_id"]).to_dicts()
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in lg["pgs"].to_dicts():
        by_game[r["game_id"]].append(r)
    hist: dict[int, list[tuple[float, float]]] = defaultdict(list)
    state: dict[tuple[str, int], tuple[float, float]] = {}
    star_at: dict[tuple[str, int], bool] = {}
    played_by: dict[tuple[str, int], set[int]] = {}
    for g in games:
        gid = g["game_id"]
        played = [r for r in by_game[gid] if r["minutes"] > 0]
        for r in played:
            h = hist[r["player_id"]]
            h.append((r["minutes"], r["pts"]))
            if len(h) >= 5:
                last = h[-10:]
                state[(gid, r["player_id"])] = (
                    sum(x[0] for x in last) / len(last),
                    sum(x[1] for x in last) / len(last),
                )
        for team in (g["home_team"], g["away_team"]):
            mine = [r for r in played if r["team_id"] == team]
            played_by[(gid, team)] = {r["player_id"] for r in mine}
            cands = sorted(
                (
                    (state[(gid, r["player_id"])][0], r["player_id"])
                    for r in mine
                    if (gid, r["player_id"]) in state
                ),
                reverse=True,
            )
            rank = {pid: i + 1 for i, (_, pid) in enumerate(cands)}
            for r in mine:
                st = state.get((gid, r["player_id"]))
                star_at[(gid, r["player_id"])] = st is not None and (
                    st[1] >= 20.0 or rank.get(r["player_id"], 99) <= 3
                )
    seq: dict[int, list[dict]] = defaultdict(list)
    for g in games:
        seq[g["home_team"]].append(g)
        seq[g["away_team"]].append(g)
    gx: dict[tuple[str, int], float] = {}
    ind: dict[tuple[str, int], float] = {}
    date_of: dict[tuple[str, int], dt.date] = {}
    for team, gl in seq.items():
        for j, g in enumerate(gl):
            key = (g["game_id"], team)
            date_of[key] = g["game_date"]
            gx[key], ind[key] = 0.0, 0.0
            if j == 0 or (g["game_date"] - gl[j - 1]["game_date"]).days != 1:
                continue
            pv = gl[j - 1]["game_id"]
            opp = [p for p in played_by[(pv, team)] if star_at[(pv, p)]]
            if not opp:
                continue
            sat = sum(1 for p in opp if p not in played_by[(g["game_id"], team)])
            gx[key], ind[key] = sat / len(opp), 1.0
    out: dict[tuple[str, int], tuple[float, bool, float | None]] = {}
    for team, gl in seq.items():
        for j, g in enumerate(gl):
            key = (g["game_id"], team)
            d = g["game_date"]
            cx = sum(gx[q] for q in gx if date_of[q] < d)
            cn = sum(ind[q] for q in ind if date_of[q] < d)
            lg_rate = cx / cn if cn > 0 else None
            win = [(x["game_id"], team) for x in gl[max(0, j - window) : j]]
            sx, nb = sum(gx[q] for q in win), sum(ind[q] for q in win)
            raw = (sx + k * lg_rate) / (nb + k) if lg_rate is not None else None
            out[key] = (nb, nb >= min_b2b, raw if nb >= min_b2b else lg_rate)
    return out, star_at, played_by, seq


def test_team_rate_and_star_match_a_naive_recomputation(
    league: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "MIN_B2B", 3)
    window = 20
    tg = m.team_games(league["games"])
    pgp = m._played_states(league["pgs"], tg)
    star_tbl, rates = m.team_rates(tg, pgp, window=window)
    naive, star_at, played_by, seq = _naive(league, window, 3)
    got = {(r["game_id"], r["team_id"]): r for r in rates.to_dicts()}
    assert len(got) == len(naive)
    defined_seen = {True: 0, False: 0}
    for key, (nb, defined, val) in naive.items():
        r = got[key]
        assert r["n_b2b_prior"] == nb
        assert r["b2b_defined"] == defined
        defined_seen[defined] += 1
        if val is None:
            assert r["team_b2b_sit"] is None
        else:
            assert r["team_b2b_sit"] == pytest.approx(val, abs=1e-12)
    assert defined_seen[True] > 20 and defined_seen[False] > 20  # both branches exercised
    # star flag per (player, next team game) = star at the team's previous game
    expect = {}
    for team, gl in seq.items():
        for j in range(1, len(gl)):
            for p in played_by[(gl[j - 1]["game_id"], team)]:
                expect[(p, gl[j]["game_id"], team)] = star_at[(gl[j - 1]["game_id"], p)]
    seen = {(r["player_id"], r["game_id"], r["team_id"]): r["star"] for r in star_tbl.to_dicts()}
    assert seen == expect
    assert any(seen.values()) and not all(seen.values())


def test_league_rate_uses_strictly_earlier_dates(league: dict) -> None:
    tg = m.team_games(league["games"])
    pgp = m._played_states(league["pgs"], tg)
    _, rates = m.team_rates(tg, pgp)
    first_day = league["games"]["game_date"].min()
    day0 = rates.join(tg.select("game_id", "team_id", "game_date"), on=["game_id", "team_id"])
    assert day0.filter(pl.col("game_date") == first_day)["team_b2b_sit"].is_null().all()


def test_b2b_flag_matches_the_p1_column(league: dict) -> None:
    f = league["frame"]
    assert (f["is_b2b_second"] == f["b2b"]).all()


def test_strength_terciles_split_thirty_teams_ten_ten_ten() -> None:
    wp = pl.DataFrame(
        {
            "team_id": list(range(1, 31)),
            "season": [2024] * 30,
            "prior_wp": [i / 30 for i in range(30)],
        }
    )
    t = m.strength_terciles(wp)
    assert sorted(t["terc"].value_counts()["count"].to_list()) == [10, 10, 10]
    assert t.filter(pl.col("team_id") == 30)["terc"].item() == 2.0
    assert t.filter(pl.col("team_id") == 1)["terc"].item() == 0.0


def test_prior_win_pct_is_regular_season_and_shifted() -> None:
    g = pl.DataFrame(
        {
            "game_id": ["0022200001", "0022200002", "0042200003"],
            "season": [2022, 2022, 2022],
            "home_team": [1, 2, 1],
            "away_team": [2, 1, 2],
            "home_pts": [100, 90, 100],
            "away_pts": [90, 100, 0],
        }
    )
    out = m.prior_win_pct(g, None).sort("team_id")
    assert out["season"].to_list() == [2023, 2023]
    assert out["prior_wp"].to_list() == [1.0, 0.0]  # playoff game ignored


def _snap_rows(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
            "tip_et": pl.Datetime("us"),
        },
        orient="row",
    )


def test_snapshot_views_gate_morning_and_evening() -> None:
    d = dt.date(2024, 1, 10)
    tip = dt.datetime(2024, 1, 10, 19, 30)
    at = lambda h, mi, day=d: dt.datetime(day.year, day.month, day.day, h, mi)  # noqa: E731
    prev = dt.date(2024, 1, 9)
    mat = dt.datetime(2024, 1, 11, 12, 30)
    d2 = dt.date(2024, 1, 11)
    rows = [
        ("g1", 1, "out", at(17, 45, prev), d, tip),
        ("g1", 1, "questionable", at(11, 0), d, tip),
        ("g1", 2, "probable", at(11, 0), d, tip),
        ("g1", 1, "available", at(17, 45), d, tip),
        ("g1", 3, "doubtful", at(18, 45), d, tip),  # after tip - 60 (18:30): unusable
        ("g2", 5, "questionable", at(17, 45), d, tip),  # only a same-day 17:45 snapshot
        ("g3", 7, "questionable", at(11, 0, d2), d2, mat),  # matinee, 11:00 <= 11:30
        (
            "g4",
            8,
            "questionable",
            dt.datetime(2024, 1, 11, 11, 5),
            d2,
            dt.datetime(2024, 1, 11, 12, 0),
        ),
    ]
    v = m.snapshot_views(_snap_rows(rows))
    t60 = v["t60"].sort("game_id", "player_id")
    assert t60.filter(pl.col("game_id") == "g1")["player_id"].to_list() == [1]
    assert t60.filter(pl.col("game_id") == "g1")["status"].to_list() == ["available"]
    assert sorted(v["morning"].filter(pl.col("game_id") == "g1")["player_id"].to_list()) == [1, 2]
    assert "g2" not in v["morning"]["game_id"].to_list()  # earliest same-day is 17:45
    assert "g3" in v["morning"]["game_id"].to_list()
    assert "g4" not in v["t60"]["game_id"].to_list()  # 11:05 > tip - 60 = 11:00
    assert v["evening"]["game_id"].to_list() == ["g1"]
    assert v["evening"]["status"].to_list() == ["out"]


def test_tag_and_listed_encodings_keep_no_snapshot_null() -> None:
    rows = pl.DataFrame({"game_id": ["g1", "g1", "g2"], "player_id": [1, 2, 9]})
    snap = pl.DataFrame(
        {"game_id": ["g1"], "player_id": [1], "status": ["questionable"]},
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "status": pl.Utf8},
    )
    rows = rows.with_columns(pl.col("player_id").cast(pl.Int64))
    tag = m._attach_snapshot(rows, snap, "c", "q", "d", "h").sort(m.KEYS)
    assert tag["c"].to_list() == [3.0, 0.0, None]  # listed questionable, unlisted, no snapshot
    assert tag["q"].to_list() == [1.0, 0.0, None]
    assert tag["h"].to_list() == [1, 1, 0]
    on = m._listed(rows, snap, "on").sort(m.KEYS)
    assert on["on"].to_list() == [1.0, 0.0, None]


def test_terms_have_the_documented_columns_and_row_count(league: dict) -> None:
    f = league["frame"]
    assert f.height == league["base"].height
    for c in m.TERM_COLUMNS:
        assert c in f.columns
    assert set(f["star"].unique().to_list()) <= {0.0, 1.0}
    assert (f["b2b_star"] <= f["star"]).all()
    # no prior season for 2022: tercile null there, present afterwards
    assert f.filter(pl.col("season") == 2022)["terc"].is_null().all()
    assert f.filter(pl.col("season") == 2024)["terc"].is_not_null().all()
    q = f.filter((pl.col("q60") == 1.0) & pl.col("terc").is_not_null())
    assert q.height > 0 and (q["q60_x_terc"] == q["terc"]).all()


def test_permuted_placebo_is_within_season_and_shared_by_a_team_game(league: dict) -> None:
    f = league["frame"]
    per = f.group_by(["game_id", "team_id"]).agg(
        pl.col("team_b2b_sit_perm").n_unique().alias("k"), pl.col("season").first()
    )
    assert per["k"].max() == 1
    for s in (2023, 2024):
        a = f.filter(pl.col("season") == s).unique(["game_id", "team_id"])
        x = np.sort(a["team_b2b_sit"].fill_null(-1.0).to_numpy())
        y = np.sort(a["team_b2b_sit_perm"].fill_null(-1.0).to_numpy())
        assert np.array_equal(x, y)
    again = m.permute_team_game_values(f.drop("team_b2b_sit_perm"))
    assert again["team_b2b_sit_perm"].equals(f["team_b2b_sit_perm"])


# --------------------------------------------------------------------------- gate checks


def test_planted_tests_pass_and_the_controls_can_fail(league: dict) -> None:
    res = m.planted_tests(
        league["rows"],
        league["games"],
        league["pgs"],
        league["snaps"],
        league["prior_wp"],
        n_games=3,
    )
    assert all(res.values()), res


def test_planted_post_tip_test_catches_an_ungated_report(
    league: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "usable_report_rows", lambda rows, cfg: rows)  # leak: no tip gate
    res = m.planted_tests(
        league["rows"],
        league["games"],
        league["pgs"],
        league["snaps"],
        league["prior_wp"],
        n_games=2,
    )
    assert res["planted_post_tip_snapshots"] is False


def test_planted_same_game_catches_a_leaky_team_rate(
    league: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = m.team_rates

    def leaky(tg: pl.DataFrame, pgp: pl.DataFrame, window: int = m.WINDOW):
        star_tbl, rates = real(tg, pgp, window)
        # leak: each team-game's own star sit share (read from tonight's box score)
        _, cand = m.star_opportunities(tg, pgp)
        own = cand.group_by(["team_id", "game_id"]).agg(pl.col("sat").mean().alias("own"))
        rates = rates.join(own, on=["team_id", "game_id"], how="left").with_columns(
            (pl.col("team_b2b_sit").fill_null(0.0) + pl.col("own").fill_null(0.0)).alias(
                "team_b2b_sit"
            )
        )
        return star_tbl, rates.drop("own")

    monkeypatch.setattr(m, "team_rates", leaky)
    res = m.planted_tests(
        league["rows"],
        league["games"],
        league["pgs"],
        league["snaps"],
        league["prior_wp"],
        n_games=4,
    )
    assert res["planted_same_game_box_score"] is False


def test_missingness_audit_and_gap(league: dict) -> None:
    audit = m.missingness(league["frame"])
    cols = {r["col"] for r in audit}
    assert set(m.AUDIT_NULLS) == cols
    assert all(0.0 <= r["null_rate"] <= 1.0 for r in audit if r["n"] > 0)
    gaps = m.max_gap_pp(audit)
    assert gaps["star"] == 0.0 and gaps["b2b_star"] == 0.0
    # a planted NULL pattern concentrated on short-minutes rows is caught
    f = league["frame"].with_columns(
        pl.when(pl.col("min_real") < 10)
        .then(None)
        .otherwise(pl.col("tag60_code"))
        .alias("tag60_code")
    )
    assert m.max_gap_pp(m.missingness(f))["tag60_code(no T-60 snapshot)"] > m.GAP_PP * 10


def test_defined_check_reports_distribution_and_fails_when_window_is_short(league: dict) -> None:
    res = m.check_defined(league["frame"])
    d = res["detail"]
    assert res["ok"] is (d["defined_share"] >= 0.95) and res["ok"] is False
    assert d["fallback_rows"] == d["n_rows"] - round(d["defined_share"] * d["n_rows"])
    assert d["n_b2b_prior_quantiles"][1.0] >= 0
    assert set(d["what_if_share_at_threshold"]) == {"5", "8", "10", "12", "15", "20"}


def _stored_from(frame: pl.DataFrame) -> dict[str, np.ndarray]:
    t = frame.filter(pl.col("season").is_in([2023, 2024]))
    return {
        "gid": t["game_id"].to_numpy(),
        "season": t["season"].to_numpy(),
        "y": t["played"].to_numpy(),
        "p_raw": t["p_raw"].to_numpy(),
        "p_platt": t["p_platt"].to_numpy(),
    }


def test_p0_reproduction_ok_perturbed_and_row_set_changes(league: dict) -> None:
    f = league["frame"].sort(["game_date", "game_id", "player_id"])
    st = _stored_from(f)
    assert m.check_p0(f, st)["ok"] is True
    bad = dict(st)
    bad["p_platt"] = np.clip(st["p_platt"] + 1e-3 * (st["season"] == 2024), 0, 1)
    res = m.check_p0(f, bad)
    assert res["ok"] is False
    assert res["detail"]["2023"]["season_ok"] is True
    assert res["detail"]["2024"]["p_platt"]["within_1e-6"] is False
    assert res["detail"]["2024"]["p_platt"]["within_0.005_hazard2_precedent_info"] is True
    # one game missing from the stored arrays (a data clean-up): diagnosed by game, fails literally
    gone = st["gid"][st["season"] == 2024][0]
    keep = st["gid"] != gone
    cut = {k: v[keep] for k, v in st.items()}
    res2 = m.check_p0(f, cut)
    assert res2["ok"] is False
    d24 = res2["detail"]["2024"]
    assert d24["games_with_different_row_counts"] == 1 and d24["differing_games_first20"] == [gone]
    assert d24["rows_aligned_matching_games"] is True


def test_snapshot_coverage_counts_games_and_days(league: dict) -> None:
    res = m.check_snapshots(league["games"], league["snaps"])
    det = res["detail"]
    assert det["all"]["games"] == league["games"].filter(pl.col("season") > 2022).height
    assert det["all"]["t60_share"] == 1.0 and det["all"]["morning_day_share"] == 1.0
    assert 0.0 < det["all"]["evening_game_share"] < 1.0
    assert res["ok"] is True
    drop = league["snaps"].filter(pl.col("game_id").str.slice(-1).cast(pl.Int64) < 5)
    res2 = m.check_snapshots(league["games"], drop)
    assert res2["ok"] is False and res2["detail"]["all"]["t60_share"] < 0.95


def test_load_snapshots_is_read_only_and_never_loads_2025(tmp_path: Path) -> None:
    path = tmp_path / "x.duckdb"
    con = connect(path)
    insert_rows(
        con,
        "games",
        ["game_id", "game_date", "season", "home_team", "away_team"],
        pl.DataFrame(
            {
                "game_id": ["0022400001", "0022500001"],
                "game_date": [dt.date(2024, 11, 1), dt.date(2025, 11, 1)],
                "season": [2024, 2025],
                "home_team": [1, 1],
                "away_team": [2, 2],
            }
        ),
    )
    av = pl.DataFrame(
        {
            "player_id": [1, 1, 2, 3],
            "as_of": [dt.datetime(2024, 11, 1, 11, 0)] * 2 + [dt.datetime(2025, 11, 1, 11, 0)] * 2,
            "game_id": ["0022400001", "0022400001", "0022500001", "0022500001"],
            "status": ["Questionable", "Out", "Out", "Out"],
            "source": [
                "nba_official_report",
                "other",
                "nba_official_report",
                "nba_official_report",
            ],
        }
    )
    insert_rows(
        con, "player_availability", ["player_id", "as_of", "game_id", "status", "source"], av
    )
    con.close()
    tips = pl.DataFrame(
        {"game_id": ["0022400001"], "tip_et": [dt.datetime(2024, 11, 1, 19, 30)]}
    ).cast({"tip_et": pl.Datetime("us")})
    df = m.load_snapshots(str(path), tips)
    assert df.height == 1
    assert df["status"].to_list() == ["questionable"]
    assert df["tip_et"].to_list() == [dt.datetime(2024, 11, 1, 19, 30)]
    assert df["game_date"].max() == dt.date(2024, 11, 1)


# --------------------------------------------------------------------------- fits + report


def test_arm_features_p1_has_no_terms_p3_swaps_p4_drops_the_tag_term() -> None:
    arms = m.all_arms()
    assert set(arms) == {"P1", "P2_T60", "P3_T60", "P4_T60", "P2_M", "P3_M", "P4_M"}
    assert not set(arms["P1"]) & set(m.TERM_COLUMNS)
    assert "team_b2b_sit_perm" in arms["P3_T60"] and "team_b2b_sit" not in arms["P3_T60"]
    assert set(arms["P2_T60"]) - set(arms["P4_T60"]) == set(m.TERM_TAG["T-60"])
    assert "tag60_code" not in arms["P2_M"] and "tagm_code" in arms["P2_M"]
    for names in arms.values():
        assert not [c for c in names if c in ("played", "min_real", "minutes")]


@pytest.fixture(scope="module")
def scores(league: dict, tmp_path_factory: pytest.TempPathFactory) -> dict[str, np.ndarray]:
    mp = pytest.MonkeyPatch()
    mp.setattr(m, "MIN_TRAIN_ROWS", 300)
    try:
        cfg = ContextResidualConfig(n_estimators=8, min_child_samples=20, num_leaves=4, n_jobs=1)
        ck = tmp_path_factory.mktemp("ck")
        z = m.walk_forward(league["frame"], m.all_arms(), cfg, ckpt=ck)
        z2 = m.walk_forward(league["frame"], m.all_arms(), cfg, ckpt=ck)  # from checkpoints
        for k in z:
            assert z[k].dtype == z2[k].dtype and np.array_equal(
                z[k].astype(str) if z[k].dtype.kind in "OUS" else z[k],
                z2[k].astype(str) if z2[k].dtype.kind in "OUS" else z2[k],
                equal_nan=z[k].dtype.kind == "f",
            )
        return z
    finally:
        mp.undo()


def test_walk_forward_scores_test_seasons_only_and_trains_strictly_before(
    league: dict, scores: dict[str, np.ndarray]
) -> None:
    assert set(np.unique(scores["season"]).tolist()) == {2023, 2024}
    for arm in m.all_arms():
        p = scores[arm]
        assert p.shape == scores["y"].shape and ((p > 0) & (p < 1)).all()
    n_test = league["frame"].filter(pl.col("season").is_in([2023, 2024])).height
    assert 0 < len(scores["y"]) <= n_test
    assert scores["m_terc"].shape == scores["y"].shape


def test_smoke_block_only_fits_the_requested_month(
    league: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "MIN_TRAIN_ROWS", 300)
    cfg = ContextResidualConfig(n_estimators=5, min_child_samples=20, num_leaves=4, n_jobs=1)
    arms = m.all_arms()
    part = m.walk_forward(
        league["frame"],
        {k: arms[k] for k in ("P1", "P2_T60")},
        cfg,
        only_start=dt.date(2023, 11, 1),
    )
    dates = league["frame"].select("game_id", "game_date").unique("game_id")
    got = dates.filter(pl.col("game_id").is_in(np.unique(part["gid"]).tolist()))["game_date"]
    assert got.min() >= dt.date(2023, 11, 1) and got.max() < dt.date(2023, 12, 1)
    assert set(part) >= {"P1", "P2_T60", "y", "gid"}


def test_walk_forward_refuses_season_2025(league: dict) -> None:
    f = league["frame"].with_columns(pl.lit(2025).alias("season"))
    with pytest.raises(ValueError, match="holdout"):
        m.walk_forward(f, m.all_arms(), ContextResidualConfig())


def test_analyze_structure_bh_and_pass_rule(scores: dict[str, np.ndarray]) -> None:
    res = m.analyze(scores, n_boot=40)
    arms = {(r["family"], r["season"], r["arm"]) for r in res["main"]}
    assert len(arms) == len(res["main"]) == 2 * 2 * 5
    p2 = [r for r in res["main"] if r["arm"] == "P2"]
    assert all("p_bh" in r and r["p_bh"] >= r["p"] - 1e-12 for r in p2)
    for r in res["main"]:
        assert r["n_games"] <= r["n_rows"] and 0.0 < r["logloss"] < 5.0
        if r["arm"] == "P1":
            assert "dlogloss" not in r
        elif r["arm"] in ("P2", "P3", "P4"):
            assert r["ci_lo"] <= r["dlogloss"] <= r["ci_hi"]
    assert {s["slice"] for s in res["slices"]} >= {"b2b_second_nights", "star_rows"}
    assert set(res["verdict"]) == {"T-60", "Morning"}
    assert set(res["pairs"]["T-60|2023"]) == {"P2-P0", "P2-P3", "P2-P4"}


def _verdict_inputs(d: float, ece_d: float, slice_d: float, vs_p3: float, p_bh: float, hi: float):
    main, slices, pairs = [], [], {}
    for fam in m.FAMILIES:
        for s in m.SEASONS:
            main.append({"family": fam, "season": s, "arm": "P1", "ece": 0.01})
            main.append(
                {
                    "family": fam,
                    "season": s,
                    "arm": "P2",
                    "ece": 0.01 + ece_d,
                    "dlogloss": d,
                    "ci_hi": hi,
                    "p_bh": p_bh,
                }
            )
            slices.append({"family": fam, "season": s, "slice": "x", "n": 400, "dlogloss": slice_d})
            pairs[f"{fam}|{s}"] = {"P2-P3": {"dlogloss": vs_p3}}
    return main, slices, pairs


def test_pass_rule_requires_every_condition_in_both_seasons() -> None:
    ok = m.pass_rule(*_verdict_inputs(-0.006, 0.0, 0.0, -0.004, 0.01, -0.001))
    assert all(v["pass"] for v in ok.values())
    for kw, check in (
        ({"d": -0.004}, "floor"),
        ({"hi": 0.001}, "ci_below_0"),
        ({"p_bh": 0.2}, "bh_p"),
        ({"ece_d": 0.01}, "ece"),
        ({"slice_d": 0.02}, "slices"),
        ({"vs_p3": -0.001}, "beats_placebo"),
    ):
        args = {
            "d": -0.006,
            "ece_d": 0.0,
            "slice_d": 0.0,
            "vs_p3": -0.004,
            "p_bh": 0.01,
            "hi": -0.001,
        }
        args.update(kw)
        res = m.pass_rule(*_verdict_inputs(**args))
        assert not res["T-60"]["pass"]
        assert not res["T-60"]["seasons"]["2023"]["checks"][check]


def test_report_stage_end_to_end_in_a_temp_tree(
    league: dict,
    scores: dict[str, np.ndarray],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PREREG", "OUT_DIR", "REPORT_PATH", "CACHE"):
        monkeypatch.setattr(m, name, getattr(m, name))
    monkeypatch.setattr(m, "N_BOOT", 30)
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "PPLAY_CONTEXT.md"
    doc.write_text("frozen rule\n=== RESULTS BELOW ===\n(not run)\n")
    sha = hashlib.sha256(b"frozen rule\n").hexdigest()
    m.configure(doc)
    gate = {
        "1_p0_reproduces": {"ok": True},
        "4a_missingness_gap": {"ok": True, "audit": m.missingness(league["frame"])},
        "all_ok": True,
    }
    m.write_gate(gate, sha, False)
    m.CACHE.mkdir(parents=True)
    m._savez(m.CACHE / "scores.npz", scores)
    assert m.main(["report", "--doc", str(doc), "--sha", sha]) == 0
    res = json.loads((m.OUT_DIR / "results.json").read_text())
    assert res["sha256"] == sha and res["seeds"]["n_boot"] == 30
    assert {"main", "slices", "pairs", "verdict", "tests", "audit"} <= set(res)
    md = m.REPORT_PATH.read_text()
    assert "Paired log loss" in md and "Slices" in md and "T-60" in md
    with pytest.raises(SystemExit):
        m.main(["report", "--doc", str(doc), "--sha", "0" * 64])
