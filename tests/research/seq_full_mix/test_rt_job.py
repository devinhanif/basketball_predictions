# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX_RT Colab jobs: builder refusals, the selection run cannot see season 2025, WALL pools hold 2019-2021 and W22 pools do not, no pool row is later than
its block, the held-out run refuses without the frozen document and the holdout row. Runs in clean interpreters (torch after lightgbm segfaults on macOS)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from research.colab.jobs import load_job
from research.features.player_sequences import CTX_FEATURES, STEP_FEATURES

ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = ROOT / "research/colab/jobs/seq_full_mix_rt"
MARK = "=== RESULTS BELOW ===\n"
START = {
    2019: "2019-10-25",
    2020: "2020-12-25",
    2021: "2021-10-20",
    2022: "2022-10-20",
    2023: "2023-10-20",
    2024: "2024-10-20",
    2025: "2025-10-20",
}


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "seq_full_mix_rt_build_t", JOB_DIR / "build_notebook.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc(tmp: Path, status: str = "FROZEN") -> tuple[Path, str]:
    p = tmp / "SEQ_FULL_MIX_RT.md"
    body = f"# t\nStatus: {status} x\nrule\n"
    p.write_text(f"{body}{MARK}later\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def _npz(path: Path, seasons: tuple[int, ...], n_per: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    season = np.concatenate([np.full(n_per, s, dtype=np.int16) for s in seasons])
    days = (
        np.concatenate(
            [
                np.datetime64(START[s])
                + np.sort(rng.integers(0, 230, n_per)).astype("timedelta64[D]")
                for s in seasons
            ]
        )
        .astype("datetime64[D]")
        .astype(np.int64)
        .astype(np.int32)
    )
    n = len(season)
    k, ns, nc = 20, len(STEP_FEATURES), len(CTX_FEATURES) + 3
    base = rng.gamma(3.0, 2.0, (n, 4)).astype(np.float32)
    np.savez_compressed(
        path, seq=rng.normal(size=(n, k, ns)).astype(np.float16), mask=(rng.random((n, k)) < 0.9).astype(np.uint8), opp_step=rng.integers(0, 30, (n, k)).astype(np.int16),
        ctx=rng.normal(size=(n, nc)).astype(np.float32), player_id=rng.integers(1, 40, n).astype(np.int64), team_idx=rng.integers(1, 31, n).astype(np.int16),
        opp_idx=rng.integers(1, 31, n).astype(np.int16), date_days=days, season=season, game_id=np.array([f"00{s}{seed}{i:06d}"[:12] for i, s in enumerate(season)], dtype="U12"),
        y=(base + rng.normal(size=(n, 4))).clip(0).astype(np.float32), base=base, n_prior=rng.integers(3, 60, n).astype(np.int32),
        stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32), step_features=np.array(STEP_FEATURES), ctx_features=np.array((*CTX_FEATURES, "era_bubble", "era_nofans", "era_limited")),
    )  # fmt: skip


def _run(train: Path, cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    code = (
        "import json, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(train)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "infos = m.main()\n"
        "print('INFOS ' + json.dumps({k: {a: b for a, b in v.items()} for k, v in infos.items()}))\n"
    )
    env = {
        **os.environ,
        "NBA_SMOKE": "100000",
        "NBA_ARTIFACT_ROOT": str(cwd / "art"),
        "NBA_RUN_STAMP": "t",
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _infos(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, r.stderr[-2500:]
    return json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("INFOS "))[6:])


def test_jobs_are_defined_and_the_selection_job_is_not_a_holdout_touch() -> None:
    sel, held = load_job("seq_full_mix_rt_sel"), load_job("seq_full_mix_rt")
    assert (
        not sel.touches_holdout
        and sel.preregistration_id is None
        and held.touches_holdout
        and held.preregistration_id == "SEQ_FULL_MIX_RT"
    )
    assert (
        sel.inputs[0].path.endswith("seq_ensemble_rt_cur.npz") and "rt_hist" in sel.inputs[1].path
    )
    assert held.inputs[0].path.endswith(
        "seq_ensemble_rt_cur25.npz"
    ) and "docs/HOLDOUT_ACCESS_LOG.md" in [i.path for i in held.inputs]
    assert {"oof_WALL_E2.npz", "oof_WALL_E2S.npz", "oof_W22_E2.npz"} <= set(sel.artifacts)


def test_builder_refuses_unfrozen_held_out_and_bakes_the_all_data_window(tmp_path: Path) -> None:
    b = _builder()
    draft, _ = _doc(tmp_path, "DRAFT")
    with pytest.raises(SystemExit):
        b.build("held", draft)
    code, nb = b.build("sel", draft)  # the gate-measurement notebook builds from a draft
    ast.parse(code)
    assert (
        'MODE = "sel"' in code
        and "__MODE__" not in code
        and "__WINDOW__" not in code
        and [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "code", "code"]
    )
    frozen, sha = _doc(tmp_path, "FROZEN")
    code, _ = b.build("held", frozen)
    assert f'RULE_SHA = "{sha}"' in code and 'WINDOW = "WALL"' in code and 'MODE = "held"' in code
    assert not hasattr(b, "chosen_window")  # no window selection exists any more


def test_gate_run_covers_wall_and_the_w22_diagnostic_2023_2024_only_and_pools_differ(
    tmp_path: Path,
) -> None:
    b = _builder()
    draft, _ = _doc(tmp_path, "DRAFT")
    code, _ = b.build("sel", draft)
    train = tmp_path / "train.py"
    train.write_text(code)
    _npz(tmp_path / "seq_ensemble_rt_cur.npz", (2022, 2023, 2024), 150, 1)
    _npz(tmp_path / "seq_ensemble_rt_hist.npz", (2019, 2020, 2021), 150, 2)
    infos = _infos(
        _run(train, tmp_path, {"NBA_PARQUET": str(tmp_path / "seq_ensemble_rt_cur.npz")})
    )
    assert set(infos) == {"WALL_E2", "WALL_E2S", "W22_E2"}  # W22 is a one-program diagnostic
    out = tmp_path / "art" / "t"
    hist_last = int(np.load(tmp_path / "seq_ensemble_rt_hist.npz")["date_days"].max())
    for name, info in infos.items():
        z = np.load(out / f"oof_{name}.npz")
        assert (
            set(np.unique(z["season"]).tolist()) == {2023, 2024} and int(z["season"].max()) <= 2024
        )  # nothing scored after 2024
        assert (
            info["window"] == name.split("_")[0]
            and info["seeds"] == [0]
            and info["smoke"] is True
            and info["mode"] == "sel"
        )
        pools = {s for b_ in info["blocks_detail"] for s in b_["pool_by_season"]}
        if name.startswith("WALL"):
            assert {"2019", "2020", "2021", "2022"} <= pools  # the WALL pool holds 2019-2021 rows
            assert info["n_fit"] > infos["W22_E2"]["n_fit"]
        else:
            assert not ({"2019", "2020", "2021"} & pools)  # the W22 pool does not
        assert all(
            b_["max_pool_date"] < b_["min_block_date"] for b_ in info["blocks_detail"]
        )  # no pool row (history included) is later than its block
        assert str(np.datetime64(hist_last, "D")) < min(
            b_["min_block_date"] for b_ in info["blocks_detail"]
        )
    cfg = json.loads((out / "config.json").read_text())
    assert (
        cfg["mode"] == "sel"
        and cfg["season_2025_used"] is False
        and sorted(cfg["programs"]) == sorted(infos)
    )


def test_selection_run_cannot_read_season_2025(tmp_path: Path) -> None:
    b = _builder()
    draft, _ = _doc(tmp_path, "DRAFT")
    code, _ = b.build("sel", draft)
    train = tmp_path / "train.py"
    train.write_text(code)
    _npz(
        tmp_path / "seq_ensemble_rt_cur.npz", (2023, 2024, 2025), 60, 1
    )  # a 2025 row in the export
    _npz(tmp_path / "seq_ensemble_rt_hist.npz", (2019, 2020, 2021), 60, 2)
    r = _run(train, tmp_path, {"NBA_PARQUET": str(tmp_path / "seq_ensemble_rt_cur.npz")})
    assert r.returncode != 0 and "after 2025" in r.stderr
    _npz(tmp_path / "seq_ensemble_rt_cur.npz", (2022, 2023, 2024), 60, 1)
    _npz(
        tmp_path / "seq_ensemble_rt_hist.npz", (2019, 2020, 2021, 2025), 40, 2
    )  # or in the history export
    r = _run(train, tmp_path, {"NBA_PARQUET": str(tmp_path / "seq_ensemble_rt_cur.npz")})
    assert r.returncode != 0


def test_held_out_run_refuses_without_the_holdout_row_and_runs_the_all_data_window_to_2025(
    tmp_path: Path,
) -> None:
    b = _builder()
    doc, sha = _doc(tmp_path, "FROZEN")
    code, _ = b.build("held", doc)
    train = tmp_path / "train.py"
    train.write_text(code)
    _npz(tmp_path / "seq_ensemble_rt_cur25.npz", (2022, 2023, 2024, 2025), 120, 1)
    _npz(tmp_path / "seq_ensemble_rt_hist.npz", (2019, 2020, 2021), 120, 2)
    env = {"NBA_PARQUET": str(tmp_path / "seq_ensemble_rt_cur25.npz")}
    r = _run(train, tmp_path, env)  # no HOLDOUT_ACCESS_LOG.md staged
    assert r.returncode != 0 and "HOLDOUT_ACCESS_LOG.md not staged" in r.stderr
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").write_text(
        f"| SEQ_FULL_MIX_RT {sha[:8]} PRE-REGISTERED |\n"
    )
    assert _run(train, tmp_path, env).returncode != 0  # not DEVIN CONFIRMED
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").write_text(
        f"| SEQ_FULL_MIX {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n"
    )
    assert _run(train, tmp_path, env).returncode != 0  # the withdrawn rule's id does not count
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").write_text(
        f"| SEQ_FULL_MIX_RT {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n"
    )
    infos = _infos(_run(train, tmp_path, env))
    assert set(infos) == {"E2", "E2P", "E2S"}
    out = tmp_path / "art" / "t"
    for name, info in infos.items():
        assert info["window"] == "WALL" and info["mode"] == "held"
        assert set(np.unique(np.load(out / f"oof_{name}.npz")["season"]).tolist()) == {
            2023,
            2024,
            2025,
        }
        assert {"2019", "2020", "2021"} <= {
            s for b_ in info["blocks_detail"] for s in b_["pool_by_season"]
        }
        assert any("2025" in b_["pool_by_season"] for b_ in info["blocks_detail"][1:])
    doc.write_text(doc.read_text().replace("rule", "rule edited", 1))
    assert (
        _run(
            train,
            tmp_path,
            {"NBA_PARQUET": str(tmp_path / "seq_ensemble_rt_cur25.npz"), "NBA_RUN_STAMP": "t2"},
        ).returncode
        != 0
    )  # sha mismatch


def test_run2_concat_requires_equal_non_row_keys(tmp_path: Path) -> None:
    """History rows are concatenated before the current rows and the non-row keys are taken from the history export: they must equal the current export's."""
    b = _builder()
    doc, _ = _doc(tmp_path, "FROZEN")
    code, _ = b.build("held", doc)
    train = tmp_path / "train.py"
    train.write_text(code)
    _npz(tmp_path / "h.npz", (2019,), 30, 1)
    _npz(tmp_path / "c.npz", (2022,), 30, 2)
    script = (
        "import importlib.util, numpy as np\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(train)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"h = dict(np.load({str(tmp_path / 'h.npz')!r})); c = dict(np.load({str(tmp_path / 'c.npz')!r}))\n"
        "h['date_str'] = np.array(['2019-10-22'] * len(h['y'])); c['date_str'] = np.array(['2022-10-18'] * len(c['y']))\n"
        "out = m.concat_rows(h, c); assert len(out['y']) == len(h['y']) + len(c['y'])\n"
        "assert len(out['date_str']) == len(out['y']) and out['date_str'][0] == '2019-10-22' and out['date_str'][-1] == '2022-10-18'\n"
        "for key, val in (('stat_scale', c['stat_scale'] * 2), ('ctx_features', np.array(['a'] * len(c['ctx_features'])))):\n"
        "    bad = dict(h); bad[key] = val\n"
        "    try:\n"
        "        m.concat_rows(bad, c)\n"
        "    except AssertionError as e:\n"
        "        assert key in str(e)\n"
        "    else:\n"
        "        raise SystemExit('no refusal for ' + key)\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-1500:]
