# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE_2 Colab job: builder refusal (unfrozen rule; the draft notebook cannot run), the run script refuses without the frozen document and the attestation,
nothing after 2023 loads, window row sets equal the parent exporter's (N <= 8) and extend to 9, 10, the step-matched arm takes U_4's steps at every refit and UP_4 takes U_8's,
`fit_steps` equals `fit` bit for bit on CPU when the step count equals the epochs', restart skips finished arms and resumes cold fits, the ceiling reports units unfinished.
Runs in clean interpreters (torch after lightgbm segfaults on macOS)."""

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
from research.features import seq_learning_curve_export as X
from research.features.player_sequences import STEP_FEATURES

ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = ROOT / "research/colab/jobs/seq_learning_curve_2"
MARK = "=== RESULTS BELOW ===\n"
SEGS = {"S1": (2013, 2014, 2015, 2016, 2017, 2018), "S2": (2019, 2020, 2021), "S3": (2022, 2023)}
NC = 12


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("slc2_build_t", JOB_DIR / "build_notebook.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc(tmp: Path, status: str = "FROZEN") -> tuple[Path, str]:
    p = tmp / "SEQ_LEARNING_CURVE_2.md"
    body = f"# t\nStatus: **{status} x**\nrule\n"
    p.write_text(f"{body}{MARK}later\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def _npz(path: Path, seasons: tuple[int, ...], n_per: int, seed: int) -> None:
    rid = np.random.default_rng(seed)
    season = np.concatenate([np.full(n_per, s, dtype=np.int16) for s in seasons])
    days = (
        np.concatenate([np.datetime64(f"{s}-10-25") + np.sort(rid.integers(0, 230, n_per)).astype("timedelta64[D]") for s in seasons])
        .astype("datetime64[D]").astype(np.int64).astype(np.int32)
    )  # fmt: skip
    n = len(season)
    pid, nprior = rid.integers(1, 40, n).astype(np.int64), rid.integers(3, 60, n).astype(np.int32)
    rng = np.random.default_rng(1000 + seed)
    base = rng.gamma(3.0, 2.0, (n, 4)).astype(np.float32)
    np.savez_compressed(
        path, seq=rng.normal(size=(n, 20, len(STEP_FEATURES))).astype(np.float16), mask=(rng.random((n, 20)) < 0.9).astype(np.uint8),
        opp_step=rng.integers(0, 30, (n, 20)).astype(np.int16), ctx=rng.normal(size=(n, NC)).astype(np.float32), player_id=pid,
        team_idx=rng.integers(1, 31, n).astype(np.int16), opp_idx=rng.integers(1, 31, n).astype(np.int16), date_days=days, season=season,
        game_id=np.array([f"{seed}{s}{i:08d}"[:12] for i, s in enumerate(season)], dtype="U12"),
        y=(base + rng.normal(size=(n, 4))).clip(0).astype(np.float32), base=base, n_prior=nprior,
        norm_c=np.ones((n, 4)), stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32), step_features=np.array(STEP_FEATURES),
        ctx_features=np.array([f"c{i}" for i in range(NC)]),
    )  # fmt: skip


def _stage(
    tmp: Path,
    sha: str,
    *,
    descends: bool = True,
    n_per: int = 80,
    seasons_s3: tuple[int, ...] = (2022, 2023),
) -> None:
    exports = {}
    for i, (s, labs) in enumerate(SEGS.items()):
        f = tmp / f"seq_learning_curve_2_norm_{s}.npz"
        _npz(f, seasons_s3 if s == "S3" else labs, n_per, 10 * i)
        exports[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    (tmp / "loader_review.md").write_text("# review\n")
    man = hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(exports.items())).encode()
    ).hexdigest()
    att = {
        "rule_sha256": sha, "freeze_commit": "f" * 40, "review_path": "docs/reviews/seq_learning_curve_2_loader_2026-10-14.md", "review_commit": "a" * 40,
        "review_sha256": hashlib.sha256(b"# review\n").hexdigest(), "review_file": "loader_review.md", "review_descends_from_freeze": descends, "exports": exports,
        "exports_manifest_sha256": man,
    }  # fmt: skip
    (tmp / "loader_review_attestation.json").write_text(json.dumps(att))


def _run(train: Path, cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    code = (
        "import json, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(train)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "infos = m.main()\n"
        "print('INFOS ' + json.dumps(infos, default=str))\n"
    )
    env = {
        **os.environ,
        "NBA_SMOKE": "100000",
        "NBA_ARTIFACT_ROOT": str(cwd / "art"),
        "NBA_RUN_STAMP": "t",
        "NBA_PARQUET": str(cwd / "seq_learning_curve_2_norm_S3.npz"),
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


def _train(
    tmp: Path, status: str = "FROZEN", patch: tuple[str, str] | None = None
) -> tuple[Path, str]:
    doc, sha = _doc(tmp, status)
    code, _ = _builder().build(doc)
    if patch:
        assert patch[0] in code
        code = code.replace(*patch)
    t = tmp / "train.py"
    t.write_text(code)
    return t, sha


def _module(train: Path, tmp: Path, body: str) -> str:
    """Run ``body`` in a clean interpreter with the job module loaded as ``m``; returns the line printed after the 'J' marker."""
    script = f"import importlib.util, json, numpy as np\nspec = importlib.util.spec_from_file_location('t', {str(train)!r})\nm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n{body}\n"
    r = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, cwd=tmp, check=False
    )
    assert r.returncode == 0, r.stderr[-1500:]
    return next(ln for ln in r.stdout.splitlines() if ln.startswith("J"))[1:]


def test_job_definition_builder_refuses_unfrozen_and_the_draft_notebook_cannot_run(
    tmp_path: Path,
) -> None:
    job = load_job("seq_learning_curve_2")
    assert (
        not job.touches_holdout
        and job.notebook.name == "seq_learning_curve_2.ipynb"
        and job.notebook.exists()
    )
    assert job.inputs[0].path.endswith("seq_learning_curve_2_norm_S3.npz")
    assert {"loader_review.md", "loader_review_attestation.json", "SEQ_LEARNING_CURVE_2.md"} <= {
        Path(i.path).name for i in job.inputs
    }
    b = _builder()
    draft, _ = _doc(tmp_path, "DRAFT")
    with pytest.raises(SystemExit):
        b.build(draft)
    frozen, sha = _doc(tmp_path, "FROZEN")
    code, nb = b.build(frozen)
    ast.parse(code)
    assert (
        f'RULE_SHA = "{sha}"' in code
        and "__RULE_SHA__" not in code
        and [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "code", "code"]
    )
    committed = json.loads((JOB_DIR / "seq_learning_curve_2.ipynb").read_text())
    assert any(
        'RULE_SHA = "DRAFT_NOT_FROZEN"' in "".join(c["source"]) for c in committed["cells"]
    ) or any(f'RULE_SHA = "{b.rule_sha()}"' in "".join(c["source"]) for c in committed["cells"])
    dcode, _ = b.build(
        draft=True
    )  # a draft notebook: even a staged FROZEN document of any text cannot match its sha
    t = tmp_path / "draft_train.py"
    t.write_text(dcode)
    _stage(tmp_path, sha)
    (tmp_path / "SEQ_LEARNING_CURVE_2.md").write_text(
        (tmp_path / "SEQ_LEARNING_CURVE_2.md").read_text()
    )
    r = _run(t, tmp_path, {"NBA_ARMS": "U_4"})
    assert r.returncode != 0 and "rule sha mismatch" in r.stderr


def test_run_script_refuses_without_the_review_attestation_and_on_every_mismatch(
    tmp_path: Path,
) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha)
    ok_files = {p.name: p.read_bytes() for p in tmp_path.glob("loader_review*")}
    (tmp_path / "loader_review_attestation.json").unlink()
    r = _run(train, tmp_path, {"NBA_ARMS": "U_4"})
    assert (
        r.returncode != 0
        and "loader_review_attestation.json not staged" in r.stderr
        and "hard stop" in r.stderr
    )
    for name, data in ok_files.items():
        (tmp_path / name).write_bytes(data)
    att = json.loads((tmp_path / "loader_review_attestation.json").read_text())
    for edit, needle in (
        ({"review_descends_from_freeze": False}, "does not descend"),
        ({"rule_sha256": "0" * 64}, "different rule sha"),
        ({"review_sha256": "0" * 64}, "differs from the attested"),
        (
            {"exports": {**att["exports"], "seq_learning_curve_2_norm_S1.npz": "0" * 64}},
            "differs from the attested sha",
        ),
    ):
        (tmp_path / "loader_review_attestation.json").write_text(json.dumps({**att, **edit}))
        r = _run(train, tmp_path, {"NBA_ARMS": "U_4"})
        assert r.returncode != 0 and needle in r.stderr, (edit, r.stderr[-400:])
    (tmp_path / "loader_review_attestation.json").write_text(json.dumps(att))
    (tmp_path / "SEQ_LEARNING_CURVE_2.md").write_text(
        (tmp_path / "SEQ_LEARNING_CURVE_2.md").read_text().replace("rule", "rule edited", 1)
    )
    r = _run(train, tmp_path, {"NBA_ARMS": "U_4"})
    assert r.returncode != 0 and "rule sha mismatch" in r.stderr
    (tmp_path / "SEQ_LEARNING_CURVE_2.md").unlink()
    assert "not staged" in _run(train, tmp_path, {"NBA_ARMS": "U_4"}).stderr


def test_nothing_after_2023_can_be_loaded(tmp_path: Path) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha, seasons_s3=(2022, 2023, 2024))
    r = _run(train, tmp_path, {"NBA_ARMS": "U_4"})
    assert r.returncode != 0 and "after 2023" in r.stderr


def test_window_masks_equal_the_parent_exporters_to_8_and_extend_to_10(tmp_path: Path) -> None:
    train, _ = _train(tmp_path)
    rng = np.random.default_rng(3)
    labs = np.repeat(np.arange(2013, 2024), 50)
    date = (
        (
            np.array([np.datetime64(f"{s}-10-25") for s in labs])
            + rng.integers(0, 230, len(labs)).astype("timedelta64[D]")
        )
        .astype("datetime64[D]")
        .astype(np.int64)
        .astype(np.int32)
    )
    npr = rng.integers(0, 8, len(labs)).astype(np.int32)
    np.savez(tmp_path / "w.npz", season=labs, date=date, npr=npr)
    body = (
        f"z = np.load({str(tmp_path / 'w.npz')!r})\nimport hashlib\n"
        "out = {n: {k: hashlib.sha256(v.tobytes()).hexdigest() for k, v in m.window_masks(z['season'], z['date'], z['npr'], n).items()} for n in range(1, 11)}\n"
        "out['labels'] = {n: list(m.window_labels(n)) for n in (4, 8, 9, 10)}\n"
        "print('J' + json.dumps(out))"
    )
    got = json.loads(_module(train, tmp_path, body))
    for n in range(1, 9):
        want = X.window_masks(labs, date, npr, n)
        assert got[str(n)] == {k: hashlib.sha256(v.tobytes()).hexdigest() for k, v in want.items()}
    assert got["labels"] == {
        "4": [2019, 2020, 2021, 2022],
        "8": list(range(2015, 2023)),
        "9": list(range(2014, 2023)),
        "10": list(range(2013, 2023)),
    }


def test_fit_steps_equals_fit_bit_for_bit_when_the_step_count_is_the_epochs(tmp_path: Path) -> None:
    """The seeded equivalence proof for the new warm loop: same state, same seed, 3 epochs of `fit` vs `fit_steps(steps = 3 * ceil(n / batch))`."""
    train, _ = _train(tmp_path)
    body = """
import torch
dev = torch.device("cpu")
rng = np.random.default_rng(0)
n = 700
d = {"seq": rng.normal(size=(n, 20, 6)).astype(np.float16), "mask": (rng.random((n, 20)) < 0.9), "opp_step": rng.integers(0, 30, (n, 20)), "ctx": rng.normal(size=(n, 5)).astype(np.float32),
     "player_id": rng.integers(1, 20, n), "team_idx": rng.integers(1, 30, n), "opp_idx": rng.integers(1, 30, n), "y": rng.gamma(3, 2, (n, 4)).astype(np.float32), "base": rng.gamma(3, 2, (n, 4)).astype(np.float32)}
vocab = {int(p): i + 1 for i, p in enumerate(np.unique(d["player_id"]))}
ts = m.Tensors(d, vocab, dev)
rows = torch.arange(n)
res = []
for kind in ("fit", "fit_steps", "more"):
    m.seed_everything(1)
    net = m.SeqNet(6, 5, len(vocab), np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32), "transformer", d=16)
    m.seed_everything(5)
    cfg = dict(epochs=3, lr=3e-4, batch=256)
    if kind == "fit":
        m.fit(net, ts, rows, None, cfg, dev, 3e-4, 3, 99)
    elif kind == "fit_steps":
        assert m.fit_steps(net, ts, rows, 3e-4, m.steps_of(n, 256, 3), 256, dev) == 9
    else:
        assert m.fit_steps(net, ts, rows, 3e-4, 20, 256, dev) == 20
    res.append(torch.cat([p.detach().flatten() for p in net.parameters()]))
print("J" + json.dumps([bool(torch.equal(res[0], res[1])), bool(torch.equal(res[0], res[2])), m.steps_of(1025, 1024, 3), m.steps_of(1024, 1024, 3)]))
"""
    same, differs, s1, s2 = json.loads(_module(train, tmp_path, body))
    assert same is True and differs is False and (s1, s2) == (6, 3)


def test_smoke_matched_steps_unmatched_steps_restart_and_unfinished(tmp_path: Path) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha)
    arms = "U_4,UP_4,U_8,M_8,M_10"
    infos = _infos(_run(train, tmp_path, {"NBA_ARMS": arms}))
    assert set(infos) == set(arms.split(","))
    out = tmp_path / "art" / "t"
    steps = {k: [b["warm_steps"] for b in v["blocks_detail"]] for k, v in infos.items()}
    assert steps["M_8"] == steps["U_4"] == steps["M_10"]  # step-matched: U_4's steps at every refit
    assert steps["UP_4"] == steps["U_8"]  # window-4 data with U_8's steps
    assert steps["U_8"] != steps["U_4"] and all(
        a >= b for a, b in zip(steps["U_8"], steps["U_4"], strict=True)
    )
    for name, info in infos.items():
        z = np.load(out / f"oof_{name}.npz")
        assert set(np.unique(z["season"]).tolist()) == {2023}
        # per-seed RAW quantiles are stored (the scorer's seed bootstrap needs them); their mean, clipped and made monotone, IS the stored ensemble
        sq = z["seed_q"]
        assert sq.shape == (2, len(z["game_id"]), 4, 19) and z["seeds"].tolist() == [0, 1]
        pooled = np.maximum.accumulate(np.clip(sq.mean(axis=0), 0.0, None), axis=2)
        assert np.allclose(pooled, z["q"], atol=1e-6)
        assert info["seeds"] == [0, 1] and len(info["cold"]) == 2  # smoke keeps 2 of the 6 seeds
        assert all(b["max_pool_date"] < b["min_block_date"] for b in info["blocks_detail"])
    assert (
        infos["M_8"]["test_keys_sha256"]
        == infos["U_4"]["test_keys_sha256"]
        == infos["M_10"]["test_keys_sha256"]
    )
    assert (
        infos["UP_4"]["n_fit"] == infos["U_4"]["n_fit"]
        and infos["U_8"]["n_fit"] > infos["U_4"]["n_fit"]
    )
    # M_8 rows come from the whole window-8 pool (more than window 4's) while taking window-4's number of steps
    assert infos["M_8"]["blocks_detail"][-1]["pool"] > infos["U_4"]["blocks_detail"][-1]["pool"]
    # cold fits are shared by the arms of a window and counted once
    diag = json.loads((out / "cap_diagnostic.json").read_text())
    assert diag["cold_fits"] == 2 * 3  # windows 4, 8, 10; two smoke seeds each
    assert sorted(p.name for p in out.glob("cold_*.pt")) == sorted(
        f"cold_{n}_{s}.pt" for n in (4, 8, 10) for s in (0, 1)
    )
    cfg = json.loads((out / "config.json").read_text())
    assert (
        cfg["arms_unfinished"] == []
        and cfg["test_label"] == 2023
        and cfg["cap_season"] == 2023
        and cfg["seeds"] == [0, 1, 2, 3, 4, 5]
    )
    r = _run(train, tmp_path, {"NBA_ARMS": arms})  # restart: finished arms skipped
    assert _infos(r) == {} and r.stdout.count("done, skipping") == 5
    (tmp_path / "c").mkdir()
    _train(tmp_path / "c", patch=("CEILING_MIN = 240.0", "CEILING_MIN = -1.0"))
    _stage(tmp_path / "c", sha)
    r = _run(tmp_path / "c" / "train.py", tmp_path / "c", {"NBA_ARMS": "U_4,M_8"})
    assert _infos(r) == {} and not list((tmp_path / "c" / "art" / "t").glob("oof_*.npz"))
    cfg = json.loads((tmp_path / "c" / "art" / "t" / "config.json").read_text())
    assert cfg["arms_unfinished"] == ["U_4", "M_8"]


def test_a_killed_unit_resumes_from_the_saved_cold_fits(tmp_path: Path) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha)
    first = _infos(_run(train, tmp_path, {"NBA_ARMS": "U_4"}))
    out = tmp_path / "art" / "t"
    (out / "oof_U_4.npz").unlink()
    (out / "info_U_4.json").unlink()
    r = _run(train, tmp_path, {"NBA_ARMS": "U_4"})
    assert r.stdout.count("cold fit restored") == 2
    again = _infos(r)
    assert again["U_4"]["cold"] == first["U_4"]["cold"]
