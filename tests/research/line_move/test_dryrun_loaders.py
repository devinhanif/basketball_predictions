"""Synthetic dryrun mechanics, the dryrun gate on real-mid readers, loaders on a fixture."""

from __future__ import annotations

import gzip
import inspect
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest

from research.line_move import __main__ as cli
from research.line_move import analysis, dryrun, loaders, selection, spec


def test_small_world_meets_the_horizon_and_forecasts_ignore_the_quotes() -> None:
    sc = dryrun.PRIMARY
    a = dryrun.make_world(np.random.default_rng(5), sc, n_dates=14, n_per_date=150)
    sel = dryrun.select_world(a)
    assert analysis.qualifying_counts(sel, "T510")[0] >= spec.MIN_QUALIFYING
    assert analysis.horizon_date(sel, "T510") is not None
    # different quote-noise scenario, same seed: the forecast draws differ only through the rng
    # stream, but a
    # planted world with the same seed has identical forecasts and identical quotes up to S1
    b = dryrun.make_world(np.random.default_rng(5), sc, "T510", 0.02, n_dates=14, n_per_date=150)
    assert [p.m for p in a[:20]] == [p.m for p in b[:20]]
    j0 = np.searchsorted(a[0].rungs[0].t, a[0].tip_s - 150 * 60)
    j1 = j0 + 8
    assert np.array_equal(a[3].rungs[2].bid[:j1], b[3].rungs[2].bid[:j1])
    assert not np.array_equal(a[3].rungs[2].bid, b[3].rungs[2].bid)


def test_null_world_has_no_movement_toward_the_models_and_planted_is_recovered() -> None:
    sc = dryrun.PRIMARY
    rng = np.random.default_rng(11)
    nulls = [
        dryrun.run_rule(dryrun.select_world(dryrun.make_world(rng, sc, n_per_date=100)), "T510")
        for _ in range(6)
    ]
    assert abs(np.mean([o.p1_mean for o in nulls])) < 0.004
    assert all(o.verdict != "PASS" for o in nulls)
    pl = dryrun.run_planted(sc, "T510", 4, 3)
    assert pl["pass_rate"] == 1.0 and 0.012 < pl["mean_p1"] < 0.026


def test_observed_quote_structure_constants() -> None:
    sp, w = dryrun._spread_pmf()
    assert w.sum() == pytest.approx(1.0) and sp[0] == 1 and len(sp) == 40
    assert dryrun.OBSERVED_STALE_P == 0.617
    assert w[0] == pytest.approx(50119 / 389331)


def _record(tmp: Path, **over: object) -> Path:
    rec = {"code_sha256": spec.code_sha256(), "T510_dryrun_pass": True, "T510b_dryrun_pass": True}
    rec.update(over)
    p = tmp / "rec.json"
    p.write_text(json.dumps(rec))
    return p


def test_real_mid_readers_require_a_passing_dryrun_for_the_current_code(tmp_path: Path) -> None:
    dryrun.require_pass(_record(tmp_path), "T510")
    dryrun.require_pass(_record(tmp_path), "T510b")
    with pytest.raises(dryrun.DryrunMissing, match="no dryrun record"):
        dryrun.require_pass(tmp_path / "none.json", "T510")
    with pytest.raises(dryrun.DryrunMissing, match="current"):
        dryrun.require_pass(_record(tmp_path, code_sha256="0" * 64), "T510")
    with pytest.raises(dryrun.DryrunMissing, match="failed"):
        dryrun.require_pass(
            _record(tmp_path, T510_dryrun_pass=False, T510b_dryrun_pass=True), "T510"
        )
    # T510b is not scored at all if T510 cannot be; its own failure makes it unscoreable alone
    with pytest.raises(dryrun.DryrunMissing, match="T510 is unscoreable"):
        dryrun.require_pass(_record(tmp_path, T510_dryrun_pass=False), "T510b")
    with pytest.raises(dryrun.DryrunMissing, match="T510b dryrun failed"):
        dryrun.require_pass(_record(tmp_path, T510b_dryrun_pass=False), "T510b")
    dryrun.require_pass(_record(tmp_path, T510b_dryrun_pass=False), "T510")  # T510 stands alone


def test_cli_refuses_without_a_record_and_preflight_forms_no_movement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        [
            "preflight",
            "--date",
            "2026-10-21",
            "--nba-db",
            "x",
            "--record",
            str(tmp_path / "no.json"),
        ]
    )
    assert rc == 2 and "REFUSED" in capsys.readouterr().out
    src = inspect.getsource(cli.cmd_preflight)
    assert "build_table" not in src and "evaluate" not in src and "reported" not in src
    assert ".m_m" not in src and "mid_close" not in src and "mid1" not in src


def test_cli_score_does_not_form_statistics_before_the_horizon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = _record(tmp_path)
    monkeypatch.setattr(cli, "_load", lambda *a, **k: ([], {}))
    called = []
    monkeypatch.setattr(analysis, "evaluate", lambda *a, **k: called.append(1))
    rc = cli.main(["score", "--rule", "T510", "--nba-db", "x", "--record", str(rec)])
    assert rc == 3 and not called and "HORIZON NOT REACHED" in capsys.readouterr().out


# ---- loaders on a recorded fixture ----

TIP = datetime(2026, 10, 22, 23, 0)  # 19:00 ET -> ET date 2026-10-22


def _make_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "create table forward_predictions (run_id varchar, game_id varchar, tipoff timestamp, "
        "made_at timestamp, model_name varchar, target varchar, player_id integer, prediction json)"
    )

    def pred(c: list[int], play: float) -> str:
        return json.dumps({"p_ge_full": {"c": c, "n": 100}, "p_play": play})

    rows = [
        (
            "r1",
            "G1",
            TIP,
            TIP - timedelta(hours=10),
            "props_context_residual",
            "pts",
            7,
            pred([90, 50], 0.9),
        ),
        (
            "r1",
            "G1",
            TIP,
            TIP - timedelta(hours=10),
            "props_recency_v1",
            "pts",
            7,
            pred([80, 40], 0.9),
        ),
        # later refresh within the cutoff supersedes r1; one after tip - 60 min must be ignored
        (
            "r2",
            "G1",
            TIP,
            TIP - timedelta(hours=3),
            "props_context_residual",
            "pts",
            7,
            pred([95, 60], 0.95),
        ),
        (
            "r2",
            "G1",
            TIP,
            TIP - timedelta(hours=3),
            "props_recency_v1",
            "pts",
            7,
            pred([85, 45], 0.95),
        ),
        (
            "r3",
            "G1",
            TIP,
            TIP - timedelta(minutes=30),
            "props_context_residual",
            "pts",
            7,
            pred([1, 1], 0.1),
        ),
    ]
    for r in rows:
        con.execute("insert into forward_predictions values (?,?,?,?,?,?,?,?)", list(r))
    con.close()


def _write_capture(root: Path) -> None:
    d = root / "live_2026-10-22" / "kalshi" / "KXNBAPTS_p0"
    d.mkdir(parents=True)
    for i in range(10):
        ts = TIP.replace(tzinfo=UTC) - timedelta(minutes=240 - 20 * i)
        mk = [
            {
                "ticker": "KXNBAPTS-26OCT22AAABBB-AAAP7-1",
                "title": "Jane Doe: 1+ points",
                "yes_bid_dollars": "0.4800",
                "yes_ask_dollars": "0.5200",
            },
            {
                "ticker": "KXNBAPTS-26OCT22AAABBB-AAAQ9-1",
                "title": "Nobody Known: 1+ points",
                "yes_bid_dollars": "0.4800",
                "yes_ask_dollars": "0.5200",
            },
            {
                "ticker": "KXNBAPTS-26OCT22AAABBB-AAAP7-2",
                "title": "Jane Doe: 2+ points",
                "yes_bid_dollars": "",
                "yes_ask_dollars": None,
            },
        ]
        with gzip.open(d / f"{ts:%Y%m%dT%H%M%S}Z.json.gz", "wt") as fh:
            json.dump({"markets": mk}, fh)


def test_loaders_m_is_latest_before_cutoff_b_same_run_names_matched_or_counted(
    tmp_path: Path,
) -> None:
    db = tmp_path / "n.duckdb"
    _make_db(db)
    fc = loaders.load_forecasts(db)
    gid, tip_s, made_s, m, b = fc[(7, "pts", date(2026, 10, 22))]
    assert gid == "G1" and m.counts == (95, 60) and m.p_play == 0.95  # r2, not r1 and not r3
    assert b is not None and b.counts == (85, 45)  # same run_id as M
    assert tip_s == int(TIP.replace(tzinfo=UTC).timestamp())
    assert made_s == int((TIP - timedelta(hours=3)).replace(tzinfo=UTC).timestamp())
    _write_capture(tmp_path / "raw")
    snaps = loaders.load_snapshots([tmp_path / "raw" / "live_2026-10-22"])
    assert len(snaps) == 3
    nm = {loaders.norm("Jane Doe"): 7}
    psgs, drops = loaders.build_psgs(snaps, fc, nm, {date(2026, 10, 22)})
    assert drops["no_player_or_forecast_match"] == 1  # "Nobody Known" is dropped and counted
    assert len(psgs) == 1 and {r.k for r in psgs[0].rungs} == {1, 2}
    sel, log = selection.select_all(psgs)
    assert (
        len(sel) == 1
        and sel[0].k == 1
        and sel[0].pc_m == 0.95
        and log.drops["S0_quote_fails_filter"] == 1
    )
    assert loaders.norm("Nikola Jokić") == "nikolajokic"


def test_package_has_no_network_or_order_surface() -> None:
    import re

    root = Path(__file__).resolve().parents[3] / "research" / "line_move"
    banned = re.compile(
        r"portfolio|create_order|place_order|/orders|api_key|authorization|bearer|httpx|requests|urlopen",
        re.IGNORECASE,
    )
    for p in sorted(root.glob("*.py")):
        assert not banned.findall(p.read_text()), p.name
