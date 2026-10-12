# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX: freeze and holdout refusals, the guarded 2025 export, the Colab job (refusals, placebo, shuffle, a tiny end-to-end run), the blend's
past-only property, the permutation helper and the decision rule failing each way."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from research.colab.jobs import load_job
from research.eval import seq_full_mix_rule as R
from research.features import player_sequences as P
from research.features import seq_full_mix_export as X
from research.features.player_sequences import CTX_FEATURES, STEP_FEATURES, SeqConfig, build_export
from tests.research.features.test_player_sequences import _raw

ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = ROOT / "research/colab/jobs/seq_full_mix"
MARK = "=== RESULTS BELOW ===\n"


def _doc(tmp: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp / "SEQ_FULL_MIX.md"
    p.write_text(f"# t\nStatus: FROZEN x\n{body}{MARK}later\n")
    return p, hashlib.sha256(f"# t\nStatus: FROZEN x\n{body}".encode()).hexdigest()


def _fake_git(doc: Path, sha: str, rid: str = "SEQ_FULL_MIX"):
    """`git log` shows one prereg commit; `git show <hash>:<doc>` returns the document's current text."""

    def run(*a: str) -> str:
        return doc.read_text() if a[0] == "show" else f"abc\tprereg({rid}): {sha}\n"

    return run


# --------------------------------------------------------------------------- freeze and holdout refusals
def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    good = _fake_git(p, sha)
    other = _fake_git(p, sha, "RECENT_ROLE_FULL")
    assert R.require_frozen(sha, p, good) == sha
    with pytest.raises(RuntimeError, match="no commit"):
        R.require_frozen(sha, p, other)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        R.require_frozen("b" * 64, p, good)
    with pytest.raises(RuntimeError, match="required"):
        R.require_frozen(None, p, good)
    p.write_text(p.read_text().replace("rule", "rule edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        R.require_frozen(sha, p, good)


def test_holdout_refuses_without_a_committed_confirmed_row(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    log = tmp_path / "log.md"
    frozen = _fake_git(p, sha)
    log.write_text("| nothing |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        R.require_holdout_row(sha, log, frozen, p)
    log.write_text(
        f"| 2026-10-12 | SEQ_FULL_MIX {sha[:8]} PRE-REGISTERED |\n"
    )  # no DEVIN CONFIRMED
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        R.require_holdout_row(sha, log, frozen, p)
    log.write_text(f"| 2026-10-12 | SEQ_FULL_MIX {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n")

    def dirty(*a: str) -> str:
        return " M log.md\n" if a[0] == "status" else frozen(*a)

    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_holdout_row(sha, log, dirty, p)

    def uncommitted_row(*a: str) -> str:
        return (
            ""
            if a[0] == "status" or (len(a) > 2 and a[0] == "log" and a[2].startswith("-S"))
            else frozen(*a)
        )

    with pytest.raises(RuntimeError, match="not in any commit"):
        R.require_holdout_row(sha, log, uncommitted_row, p)

    def clean(*a: str) -> str:
        return (
            ""
            if a[0] == "status"
            else ("deadbeef\n" if len(a) > 2 and a[2].startswith("-S") else frozen(*a))
        )

    assert "DEVIN CONFIRMED" in R.require_holdout_row(sha, log, clean, p)


# --------------------------------------------------------------------------- the guarded 2025 export
def test_export_refuses_before_the_freeze_and_never_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p, sha = _doc(tmp_path)
    monkeypatch.setattr(R, "DOC", p)
    monkeypatch.setattr(
        X,
        "build_current25",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("built before the freeze")),
    )
    with pytest.raises(RuntimeError, match="no commit"):
        X.export(sha, run=lambda *a: "", out=tmp_path)
    frozen = _fake_git(p, sha)
    monkeypatch.setattr(R, "HOLDOUT_LOG", tmp_path / "none.md")
    with pytest.raises(
        RuntimeError, match="no committed holdout row"
    ):  # frozen but no confirmed row
        X.export(sha, run=frozen, out=tmp_path)


def test_season_bound_lifts_and_restores_the_loader_wall() -> None:
    cfg = SeqConfig(
        k=5, min_target_season=2025, max_season=2025, rotation_min=5.0, min_rotation_games=2
    )
    raw = _raw(season=2025)
    with pytest.raises(ValueError, match="frozen holdout"):
        build_export(raw, cfg)
    with pytest.raises(ValueError, match="frozen holdout"):
        P.load_raw(":memory:", max_season=2025)
    with X.season_bound(2025):
        exp = build_export(raw, cfg, keep_window_ids=True)
    assert P.FROZEN_SEASON == 2025  # restored
    assert set(exp.meta["season"]) == {2025}
    with pytest.raises(ValueError, match="frozen holdout"):
        build_export(raw, cfg)
    ids = exp.window_game_ids
    assert ids is not None
    date_of = {g: d for g, d in zip(raw.games["game_id"], raw.games["game_date"], strict=True)}
    for r, (gid, mk) in enumerate(
        zip(exp.meta.game_id, exp.arrays["mask"], strict=True)
    ):  # windows never hold the target or later games
        assert all(date_of[g] < date_of[gid] for g in ids[r][mk.astype(bool)])


def test_planted_future_in_the_held_out_season_changes_no_earlier_input() -> None:
    cfg = SeqConfig(
        k=5, min_target_season=2025, max_season=2025, rotation_min=5.0, min_rotation_games=2
    )
    with X.season_bound(2025):
        a = build_export(_raw(season=2025), cfg)
        raw = _raw(season=2025)
        late = raw.pgs["game_id"] >= "G08"
        raw.pgs.loc[late, "pts"] = raw.pgs.loc[late, "pts"] + 23
        raw.pgs.loc[late & raw.pgs["minutes"].notna(), "minutes"] = 9.0
        b = build_export(raw, cfg)
    kb = {(g, p): i for i, (g, p) in enumerate(zip(b.meta.game_id, b.meta.player_id, strict=True))}
    early = [i for i, g in enumerate(a.meta.game_id) if g < "G08"]
    assert len(early) > 10
    for i in early:
        j = kb[(a.meta.game_id[i], a.meta.player_id[i])]
        for k in ("seq", "ctx", "mask"):
            assert np.array_equal(a.arrays[k][i], b.arrays[k][j])
    assert any(
        not np.array_equal(
            a.arrays["seq"][i], b.arrays["seq"][kb[(a.meta.game_id[i], a.meta.player_id[i])]]
        )
        for i, g in enumerate(a.meta.game_id)
        if g >= "G09"
    )  # not inert


def test_identity_report_flags_a_changed_row() -> None:
    cfg = SeqConfig(k=5, min_target_season=2023, rotation_min=5.0, min_rotation_games=2)
    a = build_export(_raw(), cfg)
    raw = _raw(tweak={("G02", 11): 77})
    b = build_export(raw, cfg)
    same = X.identity_report(a, dict(a.arrays), a.meta)
    assert same["stored_rows_found"] == same["stored_rows"] and all(
        v == 1.0 for v in same["share_rows_equal"].values()
    )
    diff = X.identity_report(b, dict(a.arrays), a.meta)
    assert diff["share_rows_equal"]["seq"] < 1.0 and diff["share_rows_equal"]["y"] < 1.0


# --------------------------------------------------------------------------- the Colab job
def _builder() -> ModuleType:
    """Loaded by path under a unique name: `import build_notebook` would return another job's cached module of the same name."""
    spec = importlib.util.spec_from_file_location(
        "seq_full_mix_build_notebook", JOB_DIR / "build_notebook.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _module(tmp: Path, sha: str) -> ModuleType:
    path = tmp / "train.py"
    path.write_text(_builder().script(sha))
    spec = importlib.util.spec_from_file_location("seq_full_mix_train_t", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stage(tmp: Path, confirmed: bool = True) -> str:
    doc, sha = _doc(tmp)
    row = f"| 2026-10-12 | SEQ_FULL_MIX {sha[:8]} PRE-REGISTERED {'DEVIN CONFIRMED' if confirmed else ''} |\n"
    (tmp / "HOLDOUT_ACCESS_LOG.md").write_text("# log\n" + row)
    return sha


def _npz(
    path: Path, n_per: int, seed: int, seasons: tuple[int, ...] = (2022, 2023, 2024, 2025)
) -> None:
    rng = np.random.default_rng(seed)
    start = {2022: "2022-10-20", 2023: "2023-10-20", 2024: "2024-10-20", 2025: "2025-10-20"}
    season = np.concatenate([np.full(n_per, s, dtype=np.int16) for s in seasons])
    days = (
        np.concatenate(
            [
                (
                    np.datetime64(start[s])
                    + np.sort(rng.integers(0, 230, n_per)).astype("timedelta64[D]")
                )
                for s in seasons
            ]
        )
        .astype("datetime64[D]")
        .astype(np.int64)
        .astype(np.int32)
    )
    n = len(season)
    k, ns, nc = 20, len(STEP_FEATURES), len(CTX_FEATURES)
    base = rng.gamma(3.0, 2.0, (n, 4)).astype(np.float32)
    np.savez_compressed(
        path,
        seq=rng.normal(size=(n, k, ns)).astype(np.float16),
        mask=(rng.random((n, k)) < 0.9).astype(np.uint8),
        opp_step=rng.integers(0, 30, (n, k)).astype(np.int16),
        ctx=rng.normal(size=(n, nc)).astype(np.float32),
        player_id=rng.integers(1, 40, n).astype(np.int64),
        team_idx=rng.integers(1, 31, n).astype(np.int16),
        opp_idx=rng.integers(1, 31, n).astype(np.int16),
        date_days=days,
        season=season,
        game_id=np.array([f"00{s}{i:06d}"[:12] for i, s in enumerate(season)], dtype="U12"),
        y=(base + rng.normal(size=(n, 4))).clip(0).astype(np.float32),
        base=base,
        n_prior=rng.integers(3, 60, n).astype(np.int32),
        stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32),
        step_features=np.array(STEP_FEATURES),
        ctx_features=np.array(CTX_FEATURES),
    )


def test_job_definition_is_valid_and_inputs_travel_with_the_rule() -> None:
    job = load_job("seq_full_mix")
    assert job.touches_holdout and job.preregistration_id == "SEQ_FULL_MIX"
    paths = [i.path for i in job.inputs]
    assert (
        paths[0].endswith("seq_ensemble_cur25.npz")
        and "docs/prereg/SEQ_FULL_MIX.md" in paths
        and "docs/HOLDOUT_ACCESS_LOG.md" in paths
    )
    assert job.inputs[0].produce == "uv run python -m research.features.seq_full_mix_export export"
    assert {"metrics.json", "oof_E2.npz", "oof_E2P.npz", "oof_E2S.npz"} <= set(job.artifacts)


def test_builder_refuses_unfrozen_document(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = _builder()
    p = tmp_path / "SEQ_FULL_MIX.md"
    p.write_text(f"# t\nStatus: DRAFT x\nrule\n{MARK}later\n")
    monkeypatch.setattr(b, "DOC", p)
    with pytest.raises(SystemExit):
        b.rule_sha()
    monkeypatch.setattr(b, "DOC", _doc(tmp_path)[0])
    assert len(b.rule_sha()) == 64


def test_check_rule_refuses_wrong_sha_draft_and_a_missing_or_unconfirmed_holdout_row(
    tmp_path: Path,
) -> None:
    sha = _stage(tmp_path)
    mod = _module(tmp_path, sha)
    assert mod.check_rule(tmp_path) == sha
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").write_text("# log\n")
    with pytest.raises(RuntimeError, match="no holdout row"):
        mod.check_rule(tmp_path)
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").write_text(
        f"| SEQ_FULL_MIX {sha[:8]} PRE-REGISTERED |\n"
    )  # not confirmed
    with pytest.raises(RuntimeError, match="no holdout row"):
        mod.check_rule(tmp_path)
    (tmp_path / "HOLDOUT_ACCESS_LOG.md").unlink()
    with pytest.raises(FileNotFoundError):
        mod.check_rule(tmp_path)
    _stage(tmp_path)
    doc = tmp_path / "SEQ_FULL_MIX.md"
    doc.write_text(doc.read_text().replace("rule\n", "rule edited\n", 1))
    with pytest.raises(RuntimeError, match="sha mismatch"):
        mod.check_rule(tmp_path)
    doc.write_text(f"# t\nStatus: DRAFT x\nrule\n{MARK}")
    with pytest.raises(RuntimeError, match="not FROZEN"):
        mod.check_rule(tmp_path)


def test_placebo_index_is_strictly_earlier_same_player_season_and_deterministic(
    tmp_path: Path,
) -> None:
    mod = _module(tmp_path, _stage(tmp_path))
    rng = np.random.default_rng(3)
    n = 600
    player = rng.integers(1, 12, n)
    season = rng.choice(np.array([2023, 2024, 2025], dtype=np.int16), n)
    date = rng.integers(19000, 19120, n).astype(np.int32)
    idx = mod.placebo_index(player, season, date, seed=0)
    assert np.array_equal(
        idx, mod.placebo_index(player, season, date, seed=0)
    ) and not np.array_equal(idx, mod.placebo_index(player, season, date, seed=1))
    for i in range(n):
        j = int(idx[i])
        assert player[j] == player[i] and season[j] == season[i]
        if j != i:
            assert date[j] < date[i]  # strictly earlier, never a same-day row
        else:  # no strictly earlier row of that player-season exists
            assert not ((player == player[i]) & (season == season[i]) & (date < date[i])).any()
    assert (idx != np.arange(n)).mean() > 0.8


def test_shuffle_keeps_the_target_player_key_and_the_permutation_stays_in_the_date(
    tmp_path: Path,
) -> None:
    mod = _module(tmp_path, _stage(tmp_path))
    _npz(tmp_path / "c.npz", 120, 0, (2022, 2023))
    d = mod.load_npz_25(tmp_path / "c.npz")
    s = mod.shuffle_within_date({k: (v.copy() if hasattr(v, "copy") else v) for k, v in d.items()})
    assert np.array_equal(s["pid_true"], d["player_id"]) and not np.array_equal(
        s["player_id"], d["player_id"]
    )
    rows = np.arange(10)
    assert np.array_equal(
        mod.oof_dict(s, rows, np.zeros((10, 4)), np.zeros((10, 4, 19)))["player_id"],
        d["player_id"][rows],
    )
    assert np.array_equal(s["y"], d["y"]) and np.array_equal(s["base"], d["base"])


def test_load_npz_25_admits_2025_and_nothing_later(tmp_path: Path) -> None:
    mod = _module(tmp_path, _stage(tmp_path))
    _npz(tmp_path / "ok.npz", 50, 0, (2024, 2025))
    assert int(mod.load_npz_25(tmp_path / "ok.npz")["season"].max()) == 2025
    d = dict(np.load(tmp_path / "ok.npz"))
    d["season"][:] = 2026
    np.savez(tmp_path / "bad.npz", **d)
    with pytest.raises(ValueError, match="after 2025"):
        mod.load_npz_25(tmp_path / "bad.npz")


def test_end_to_end_tiny_run_covers_2025_with_no_future_pool(tmp_path: Path) -> None:
    """Runs in a clean interpreter: torch after lightgbm/xgboost in one process segfaults on macOS (OpenMP), and the real notebook is its own process."""
    import subprocess

    sha = _stage(tmp_path)
    mod = _module(tmp_path, sha)  # builds train.py (and imports torch here only to prove it builds)
    _npz(tmp_path / "seq_ensemble_cur25.npz", 150, 1)
    code = (
        "import json, importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(tmp_path / 'train.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "infos = m.main(); infos2 = m.main()\n"
        "print('INFOS ' + json.dumps({k: {a: b for a, b in v.items()} for k, v in infos.items()}))\n"
        "print('RESUMED ' + json.dumps(sorted(infos2)))\n"
    )
    env = {
        **os.environ, "NBA_SMOKE": "100000", "NBA_PARQUET": str(tmp_path / "seq_ensemble_cur25.npz"),
        "NBA_ARTIFACT_ROOT": str(tmp_path / "art"), "NBA_RUN_STAMP": "t",
    }  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    infos = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("INFOS "))[6:])
    assert (
        json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("RESUMED "))[8:]) == []
    )  # a resumed run skips finished programs
    out = tmp_path / "art" / "t"
    assert set(infos) == {"E2", "E2P", "E2S"}
    for p in ("E2", "E2P", "E2S"):
        z = np.load(out / f"oof_{p}.npz")
        assert (
            z["q"].shape[1:] == (4, 19)
            and np.isfinite(z["q"]).all()
            and (np.diff(z["q"], axis=2) >= -1e-6).all()
        )
        assert set(np.unique(z["season"]).tolist()) == {2023, 2024, 2025}

    def keys(p: str) -> set[tuple[str, int]]:
        z = np.load(out / f"oof_{p}.npz")
        return set(zip(z["game_id"], z["player_id"].tolist(), strict=True))

    assert (
        keys("E2") == keys("E2P") == keys("E2S")
    )  # the shuffled arm is keyed by the TARGET player, so the three align
    det = infos["E2"]["blocks_detail"]
    assert any(
        "2025" in b["pool_by_season"] for b in det[1:]
    )  # later 2025 months train on earlier 2025 rows
    assert all(b["max_pool_date"] < b["min_block_date"] for b in det)
    assert (
        infos["E2P"]["placebo_self_share"] is not None and infos["E2"]["placebo_self_share"] is None
    )
    cfg = json.loads((out / "config.json").read_text())
    assert (
        cfg["rule_sha256"] == sha
        and cfg["test_seasons"] == [2023, 2024, 2025]
        and cfg["season_2025_used"] is True
    )
    assert sha == mod.RULE_SHA
    # N3/N4: per-program evidence survives the second session (which ran nothing), and the config carries the export's hash
    assert sorted(cfg["programs"]) == ["E2", "E2P", "E2S"] and cfg[
        "export_npz_sha256"
    ] == R.file_sha256(tmp_path / "seq_ensemble_cur25.npz")
    assert sorted(json.loads((out / "metrics.json").read_text())["programs"]) == [
        "E2",
        "E2P",
        "E2S",
    ]
    for prog in ("E2", "E2P", "E2S"):
        info = json.loads((out / f"info_{prog}.json").read_text())
        assert (
            info["seeds"] == [0]
            and info["smoke"] is True
            and info["hidden"] == 16
            and info["blocks_detail"]
        )  # smoke is recorded as smoke
        assert info["export_npz_sha256"] == cfg["export_npz_sha256"] and info["rule_sha256"] == sha


def test_notebook_builds_from_a_frozen_document_and_compiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ast

    b = _builder()
    doc, sha = _doc(tmp_path)
    monkeypatch.setattr(b, "DOC", doc)
    code, nb = b.build()
    assert f'RULE_SHA = "{sha}"' in code and "__RULE_SHA__" not in code
    ast.parse(code)  # the notebook body is valid Python
    assert [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "code", "code"]
    src = "".join(nb["cells"][1]["source"])
    assert "def run_walk_forward" in src and "E2P" in src and "check_rule" in src
    assert "seq_full_mix" in "".join(nb["cells"][0]["source"])


def test_freeze_commit_checks_the_content_of_the_commit_not_only_its_subject(
    tmp_path: Path,
) -> None:
    p, sha = _doc(tmp_path)
    other = tmp_path / "other.md"
    other.write_text(p.read_text().replace("rule", "a different rule"))
    assert R.freeze_commit(sha, _fake_git(p, sha)) == "abc"
    assert (
        R.freeze_commit(sha, _fake_git(other, sha)) is None
    )  # right subject, wrong content at that commit
    with pytest.raises(RuntimeError, match="no commit"):
        R.require_frozen(sha, p, _fake_git(other, sha))


def test_export_records_the_sha256_of_the_npz_and_the_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = SeqConfig(k=5, min_target_season=2023, rotation_min=5.0, min_rotation_games=2)
    exp = build_export(_raw(), cfg)
    monkeypatch.setattr(X, "build_current25", lambda *a, **k: exp)
    monkeypatch.setattr(R, "require_ready", lambda *a, **k: "x")
    monkeypatch.setattr(X, "STORED_CUR", tmp_path / "none.npz")
    info = X.export("a" * 64, out=tmp_path)
    assert info["npz_sha256"] == R.file_sha256(tmp_path / "seq_ensemble_cur25.npz")
    assert info["meta_sha256"] == R.file_sha256(tmp_path / "seq_ensemble_cur25_meta.parquet")
    assert (
        json.loads((tmp_path / "export_info.json").read_text())["npz_sha256"] == info["npz_sha256"]
    )
