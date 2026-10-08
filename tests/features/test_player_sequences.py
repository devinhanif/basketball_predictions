"""Sequence export: as-of windows, absent tokens, pre-tip report rule, holdout guard."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nba.features.player_sequences import (
    _SI,
    CTX_FEATURES,
    STEP_FEATURES,
    RawInputs,
    SeqConfig,
    build_export,
    latest_pretip_status,
)

T1, T2 = 1, 2
N_GAMES = 12
CFG = SeqConfig(k=5, min_target_season=2023, rotation_min=5.0, min_rotation_games=2)


def _date(i: int) -> dt.date:
    return dt.date(2024, 1, 1) + dt.timedelta(days=2 * i)


def _raw(season: int = 2023, tweak: dict | None = None) -> RawInputs:
    games = pd.DataFrame(
        {
            "game_id": [f"G{i:02d}" for i in range(N_GAMES)],
            "game_date": [_date(i) for i in range(N_GAMES)],
            "season": season,
            "home_team": [T1 if i % 2 == 0 else T2 for i in range(N_GAMES)],
            "away_team": [T2 if i % 2 == 0 else T1 for i in range(N_GAMES)],
            "home_pts": 100 + np.arange(N_GAMES),
            "away_pts": 98,
        }
    )
    rows = []
    for i in range(N_GAMES):
        for team, base in ((T1, 10), (T2, 20)):
            for k in range(1, 5):
                pid = base + k
                if pid == 12 and i == 3:  # DNP row (minutes NULL, stats 0)
                    rows.append((f"G{i:02d}", pid, team, np.nan, 0, 0, 0, 0, 0, 0, 0, False, 0, 0))
                    continue
                if pid == 12 and i == 4:  # no row at all
                    continue
                if pid == 14 and i < 6:  # arrives (new to the roster) at game 6
                    continue
                rows.append(
                    (f"G{i:02d}", pid, team, 30.0, 10 + i + k, 4, 3, 1, 1, 0, 2, k <= 2, 9, 4)
                )
    pgs = pd.DataFrame(
        rows,
        columns=[
            "game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m",
            "stl", "blk", "tov", "starter", "fga", "fta",
        ],
    )  # fmt: skip
    if tweak:
        for (gid, pid), pts in tweak.items():
            pgs.loc[(pgs.game_id == gid) & (pgs.player_id == pid), "pts"] = pts
    poss_team = pd.DataFrame(
        [(f"G{i:02d}", t, 100) for i in range(N_GAMES) for t in (T1, T2)],
        columns=["game_id", "team_id", "n_poss"],
    )
    poss_player = pd.DataFrame(
        columns=["game_id", "player_id", "att", "ft_trips", "z_rim", "z_mid", "z_ab3"]
    )
    avail = pd.DataFrame(
        {
            "game_id": ["G08", "G08", "G08", "G09"],
            "player_id": [11, 11, 13, 11],
            "status": ["available", "out", "out", "out"],
            "as_of": pd.to_datetime(
                [
                    f"{_date(8)} 10:00",
                    f"{_date(8)} 18:30",  # after tip proxy (19:00) - 60 min: ignored
                    f"{_date(8)} 10:00",
                    f"{_date(9)} 19:30",  # also after cutoff: no usable report for G09
                ]
            ),
        }
    )
    static = pd.DataFrame({"player_id": [11, 12, 13], "position": ["G", "F", "C"]})
    return RawInputs(games, pgs, poss_player, poss_team, avail, static)


def _export(raw: RawInputs | None = None):
    return build_export(raw or _raw(), CFG, keep_window_ids=True)


def _row(exp, gid: str, pid: int) -> int:
    m = exp.meta
    return int(m.index[(m.game_id == gid) & (m.player_id == pid)][0])


def test_windows_never_include_target_or_later() -> None:
    exp = _export()
    date_of = {f"G{i:02d}": _date(i) for i in range(N_GAMES)}
    ids = exp.window_game_ids
    assert ids is not None
    for r, (gid, mk) in enumerate(zip(exp.meta.game_id, exp.arrays["mask"], strict=True)):
        used = ids[r][mk.astype(bool)]
        assert all(date_of[g] < date_of[gid] for g in used)
        assert gid not in set(used)
        assert len(set(used)) == len(used)


def test_target_box_score_does_not_change_its_own_inputs() -> None:
    a = _export(_raw())
    b = _export(_raw(tweak={("G07", 11): 99}))
    r = _row(a, "G07", 11)
    assert np.array_equal(a.arrays["seq"][r], b.arrays["seq"][r])
    assert np.array_equal(a.arrays["ctx"][r], b.arrays["ctx"][r])
    later = _row(a, "G09", 11)  # the next game sees G07 as history
    assert not np.array_equal(a.arrays["seq"][later], b.arrays["seq"][later])
    assert b.arrays["y"][_row(b, "G07", 11), 0] == 99


def test_absent_tokens_dnp_and_missing_rows() -> None:
    exp = _export()
    r = _row(exp, "G07", 12)
    ids = exp.window_game_ids[r]
    mk = exp.arrays["mask"][r].astype(bool)
    seq = exp.arrays["seq"][r].astype(np.float32)
    by_game = {g: seq[i] for i, g in enumerate(ids) if mk[i]}
    assert list(ids[mk]) == ["G02", "G03", "G04", "G05", "G06"]
    for g in ("G03", "G04"):  # DNP row, and a game with no row at all
        assert by_game[g][_SI["absent"]] == 1 and by_game[g][_SI["present"]] == 0
        assert by_game[g][_SI["pts"]] == 0 and by_game[g][_SI["min"]] == 0
    for g in ("G02", "G05", "G06"):
        assert by_game[g][_SI["present"]] == 1 and by_game[g][_SI["absent"]] == 0
        assert by_game[g][_SI["min"]] > 0
    # the team's game context is still known for the missed game
    assert by_game["G03"][_SI["home"]] in (0.0, 1.0)


def test_new_arrival_has_padding_not_absent_before_first_row() -> None:
    exp = _export()
    r = _row(exp, "G08", 14)  # first row at G06
    mk = exp.arrays["mask"][r].astype(bool)
    assert mk.sum() == 2  # G06, G07 only; earlier team games are padding
    assert not mk[:3].any()
    assert exp.arrays["seq"][r][~mk].astype(np.float32).sum() == 0.0


def test_pretip_report_rule_uses_latest_usable_snapshot() -> None:
    raw = _raw()
    snap = latest_pretip_status(raw.avail, raw.games, CFG)
    g8 = snap[snap.game_id == "G08"].set_index("player_id")["status"].to_dict()
    assert g8 == {11: "available", 13: "out"}  # 18:30 'out' for 11 is after the cutoff
    assert "G09" not in set(snap.game_id)  # only a post-cutoff snapshot -> no report
    exp = _export(raw)
    ci = {n: i for i, n in enumerate(CTX_FEATURES)}
    c = exp.arrays["ctx"]
    r11 = _row(exp, "G08", 11)
    assert c[r11, ci["has_report"]] == 1 and c[r11, ci["st_available"]] == 1
    assert c[r11, ci["st_out"]] == 0
    assert c[r11, ci["n_rep_out"]] > 0  # teammate 13 is a reported-OUT rotation player
    r9 = _row(exp, "G09", 12)
    assert c[r9, ci["has_report"]] == 0 and c[r9, ci["n_rep_out"]] == 0


def test_teammates_out_counted_for_past_game_tokens() -> None:
    exp = _export()
    r = _row(exp, "G07", 11)
    ids = exp.window_game_ids[r]
    mk = exp.arrays["mask"][r].astype(bool)
    seq = exp.arrays["seq"][r].astype(np.float32)
    g3 = seq[list(ids).index("G03")]
    assert g3[_SI["n_tm_out"]] > 0 and g3[_SI["vac_tm"]] > 0  # 12 sat G03 (DNP row)
    g5 = seq[list(ids).index("G05")]
    assert g5[_SI["n_tm_out"]] == 0 and mk.any()


def test_frozen_season_is_rejected() -> None:
    with pytest.raises(ValueError, match="frozen"):
        build_export(_raw(season=2025), CFG)


def test_shapes_dtypes_and_finiteness() -> None:
    exp = _export()
    a = exp.arrays
    n = len(exp.meta)
    assert a["seq"].shape == (n, CFG.k, len(STEP_FEATURES)) and a["seq"].dtype == np.float16
    assert a["ctx"].shape == (n, len(CTX_FEATURES))
    assert np.isfinite(a["seq"].astype(np.float32)).all() and np.isfinite(a["ctx"]).all()
    assert (exp.meta.season == 2023).all()
    assert (a["y"] >= 0).all()  # DNP rows are never targets
    assert not ((exp.meta.game_id == "G03") & (exp.meta.player_id == 12)).any()


# --------------------------------------------------------------------------- job / model / eval


def _load_train_module():
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "nba/colab/jobs/seq_props/seq_props_train.py"
    spec = importlib.util.spec_from_file_location("seq_props_train", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_job_parses_and_notebook_in_sync() -> None:
    import json
    from pathlib import Path

    from nba.colab.jobs import load_job

    job = load_job("seq_props")
    assert job.name == "seq_props" and not job.touches_holdout
    assert "oof_2024.parquet" in job.artifacts
    root = Path(__file__).resolve().parents[2] / "nba/colab/jobs/seq_props"
    nb = json.loads((root / "seq_props.ipynb").read_text())
    code = "".join(next(c for c in nb["cells"] if "def main" in "".join(c["source"]))["source"])
    src = (root / "seq_props_train.py").read_text()
    assert code.strip() == src.replace('if __name__ == "__main__":\n    main()\n', "").strip()


def test_net_quantiles_monotone_and_handles_empty_history() -> None:
    """Runs in a subprocess: torch and lightgbm (imported by other tests) can clash on
    the macOS OpenMP runtime and segfault when they share one process."""
    import subprocess
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "nba/colab/jobs/seq_props/seq_props_train.py"
    code = f"""
import importlib.util, numpy as np, torch
spec = importlib.util.spec_from_file_location("m", {str(path)!r})
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
n_step, n_ctx = {len(STEP_FEATURES)}, {len(CTX_FEATURES)}
for arm in ("transformer", "gru"):
    net = m.SeqNet(n_step, n_ctx, 10, np.array([7, 3, 2.2, 1.2]), arm, d=16, k=5).eval()
    b = dict(
        seq=torch.randn(6, 5, n_step),
        mask=torch.tensor([[1] * 5] * 3 + [[0] * 5] * 3, dtype=torch.bool),
        opp_step=torch.randint(0, 30, (6, 5)), ctx=torch.randn(6, n_ctx),
        pid=torch.randint(0, 11, (6,)), team=torch.randint(1, 30, (6,)),
        opp=torch.randint(1, 30, (6,)), base=torch.rand(6, 4) * 10,
    )
    mean, q = net(b)
    assert torch.isfinite(q).all() and torch.isfinite(mean).all()
    assert (q[:, :, 1:] >= q[:, :, :-1]).all() and q.shape == (6, 4, 19)
print("ok")
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-500:]


def test_eval_rule_and_frozen_guard() -> None:
    from nba.eval import seq_props_eval as ev

    good = {
        "delta": [-0.02, -0.03, -0.01], "p_value": 0.001, "slice_regressions": [],
    }  # fmt: skip
    small = {"delta": [-0.001, -0.002, 0.0005], "p_value": 0.6, "slice_regressions": []}
    reg = {"delta": [-0.02, -0.03, -0.01], "p_value": 0.001, "slice_regressions": ["x"]}
    v = ev.apply_rule({"pts": good, "reb": small, "ast": reg, "fg3m": good})
    assert v["pts"]["keep"] and v["fg3m"]["keep"]
    assert not v["reb"]["keep"] and not v["ast"]["keep"]
    assert v["ast"]["checks"]["no_slice_regression"] is False
    bad = pd.DataFrame({"season": [2025], "target": ["pts"]})
    with pytest.raises(ValueError, match="frozen"):
        ev.align(bad, bad, bad, "pts")


def test_eval_summarize_detects_a_better_model() -> None:
    from nba.eval import seq_props_eval as ev

    rng = np.random.default_rng(0)
    n = 600
    y = rng.poisson(10, n).astype(float)
    taus = np.arange(1, 20) / 20
    from scipy.stats import norm

    good = 10 + 3.2 * norm.ppf(taus)[None, :] + np.zeros((n, 1))
    bad = 14 + 6.0 * norm.ppf(taus)[None, :] + np.zeros((n, 1))
    gid = np.repeat(np.arange(n // 6), 6)
    sl = {"role": np.where(np.arange(n) % 2 == 0, "starter", "bench")}
    r = ev.summarize(good, bad, y, gid, sl, n_boot=200)
    assert r["delta"][2] < 0 and r["n_games"] == n // 6
