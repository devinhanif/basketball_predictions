# ruff: noqa: E501
"""WOWY_STRUCT freeze / pin refusals and the pin's coverage of the harness import closure. No data, no fit, no real git."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from research.archive.wowy_struct import wowy_struct_rule as rule

REPO = Path(__file__).resolve().parents[4]
TEXT = "RULE ABOVE\nline two\n" + rule.MARKER + "\nresults below do not count\n"
SHA = rule.text_sha(TEXT)
FC = "f" * 40
PC = "a" * 40


def fake_git(log: str, shows: dict[str, str]) -> Callable[..., str]:
    def run(*args: str) -> str:
        if args[0] == "log":
            return log
        if args[0] == "show":
            return shows[args[1]]
        return ""

    return run


def test_text_sha_ignores_everything_below_the_marker() -> None:
    assert rule.text_sha(TEXT) == rule.text_sha(TEXT + "more results\n")
    assert rule.text_sha(TEXT) != rule.text_sha(TEXT.replace("line two", "line 2"))


def test_require_frozen_refuses_without_sha_wrong_text_or_commit(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(TEXT)
    with pytest.raises(RuntimeError, match="sha256"):
        rule.require_frozen(None, doc)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        rule.require_frozen("0" * 64, doc, fake_git("", {}))
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_frozen(SHA, doc, fake_git("", {}))
    wrong = fake_git(f"{FC}\tprereg({rule.ID}): {SHA}\n", {f"{FC}:{rule.DOC_REL}": "other text\n"})
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_frozen(SHA, doc, wrong)
    ok = fake_git(f"{FC}\tprereg({rule.ID}): {SHA}\n", {f"{FC}:{rule.DOC_REL}": TEXT})
    assert rule.require_frozen(SHA, doc, ok) == SHA


def pinned_setup(
    tmp_path: Path, edit_disk: bool = False, bad_pin: bool = False, descends: bool = True
) -> tuple[Callable[..., str], Callable[..., bool]]:
    blobs = {}
    shows = {f"{FC}:{rule.DOC_REL}": TEXT}
    for rel in rule.HARNESS_FILES:
        txt = f"content of {rel}\n"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(
            txt + ("EDIT\n" if edit_disk and rel.endswith("run.py") else "")
        )
        shows[f"{PC}:{rel}"] = txt
        shows[f"{FC}:{rel}"] = txt
        blobs[rel] = hashlib.sha256(txt.encode()).hexdigest()
    pin = rule.pin_of(blobs)
    log = f"{PC}\tharness({rule.ID}): {'0' * 64 if bad_pin else pin}\n{FC}\tprereg({rule.ID}): {SHA}\n"
    return fake_git(log, shows), (lambda *a: descends)


def test_pin_accepts_the_committed_harness(tmp_path: Path) -> None:
    run, ok = pinned_setup(tmp_path)
    assert set(rule.require_harness_pinned(SHA, run, tmp_path, ok)) == set(rule.HARNESS_FILES)


@pytest.mark.parametrize("case", ["edited", "bad_pin", "not_descendant", "no_pin", "not_frozen"])
def test_pin_refuses(tmp_path: Path, case: str) -> None:
    run, ok = pinned_setup(
        tmp_path,
        edit_disk=case == "edited",
        bad_pin=case == "bad_pin",
        descends=case != "not_descendant",
    )
    if case == "no_pin":
        run = fake_git(f"{FC}\tprereg({rule.ID}): {SHA}\n", {f"{FC}:{rule.DOC_REL}": TEXT})
    if case == "not_frozen":
        run = fake_git("", {})
    with pytest.raises(RuntimeError):
        rule.require_harness_pinned(SHA, run, tmp_path, ok)


def test_pin_refuses_a_harness_edit_between_the_freeze_and_the_pin_commits(tmp_path: Path) -> None:
    blobs = {}
    shows = {f"{FC}:{rule.DOC_REL}": TEXT}
    for rel in rule.HARNESS_FILES:
        txt = f"content of {rel}\n"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(txt)
        shows[f"{PC}:{rel}"] = txt
        shows[f"{FC}:{rel}"] = txt + ("EDIT\n" if rel == rule.HARNESS_FILES[0] else "")
        blobs[rel] = hashlib.sha256(txt.encode()).hexdigest()
    log = f"{PC}\tharness({rule.ID}): {rule.pin_of(blobs)}\n{FC}\tprereg({rule.ID}): {SHA}\n"
    with pytest.raises(RuntimeError, match="freeze commit and the pin commit"):
        rule.require_harness_pinned(SHA, fake_git(log, shows), tmp_path, lambda *a: True)


def test_pin_changes_when_any_one_file_changes() -> None:
    blobs = {rel: "0" * 64 for rel in rule.HARNESS_FILES}
    base = rule.pin_of(blobs)
    for rel in rule.HARNESS_FILES:
        assert rule.pin_of({**blobs, rel: "1" * 64}) != base


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def closure(start: str) -> set[str]:
    seen: set[str] = set()
    todo = [start]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for mod in _imports(REPO / rel):
            if mod.split(".")[0] not in ("nba", "research"):
                continue
            p = REPO / (mod.replace(".", "/") + ".py")
            if p.exists():
                todo.append(str(p.relative_to(REPO)))
    return seen


def test_pin_covers_import_closure() -> None:
    need = (
        closure("research/archive/wowy_struct/wowy_struct_eval.py")
        | closure("research/archive/wowy_struct/wowy_struct_rule.py")
        | closure("research/archive/wowy_struct/wowy_struct_repl_gate.py")
    )
    assert need <= set(rule.HARNESS_FILES), sorted(need - set(rule.HARNESS_FILES))


def test_pin_files_exist_and_configs_read_by_the_harness_are_pinned() -> None:
    for rel in rule.HARNESS_FILES:
        assert (REPO / rel).exists(), rel
    src = "".join(
        (REPO / d / f"{m}.py").read_text()
        for d, m in (
            ("research/archive/wowy_struct", "wowy_struct_eval"),
            ("research/archive/wowy_struct", "wowy_struct_repl_gate"),
            ("research/eval", "f1_warm_window_gate"),
            ("research/eval", "f1_warm_window_run"),
        )
    )
    for cfg in ("configs/context_residual.yaml", "configs/mov_elo_tuned.yaml"):
        assert cfg.split("/")[1] in src and cfg in rule.HARNESS_FILES


def test_f1_files_are_pinned_because_arm_a0_is_f1s_arm_c() -> None:
    for rel in ("research/eval/f1_warm_window_gate.py", "research/eval/f1_warm_window_run.py"):
        assert rel in rule.HARNESS_FILES


def test_declared_reads_only_hex_lines_above_the_marker(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"X_SHA: {'a' * 64}\nnot a pin: {'b' * 64}\n{rule.MARKER}\nY_SHA: {'c' * 64}\n")
    assert rule.declared(doc) == {"X_SHA": "a" * 64}


def test_doc_pins_equal_the_committed_artifacts() -> None:
    d = rule.declared()
    for name, rel in (
        ("GATE_SHA256", "reports/prereg_wowy_repl/gate.json"),
        ("STRUCT_GATE_SHA256", "reports/prereg_wowy_redistribution/gate.json"),
        ("F1_GATE_SHA256", "reports/prereg_f1_warm_window/gate.json"),
    ):
        assert d[name] == rule.file_sha256(REPO / rel), name
    for name in (
        "C_SHA256_PTS",
        "C_SHA256_AST",
        "C_SHA256_PIC",
        "F1_RULE_SHA256",
        "F1_PIN_SHA256",
        "PRIORS_SHA256",
        "HISTORY_DB_SHA256",
        "FP_NBA_LE2024",
        "FP_HISTORY_LE2024",
        "FP_STATIC_MERGED",
        "FP_SCHEDULE_LE2024",
    ):
        assert len(d[name]) == 64, name


def test_doc_marker_is_one_whole_line_and_the_rule_hash_is_computable() -> None:
    lines = rule.DOC.read_text().splitlines()
    assert lines.count(rule.MARKER) == 1
    assert len(rule.frozen_sha()) == 64
