# COACH_PHASE (archived 2026-10-20)

- Rule: COACH_PHASE, `docs/prereg/COACH_PHASE.md`. Frozen sha256 `8111c026`, freeze commit `572f08e` (`prereg(COACH_PHASE): 8111c026...`).
- Ledger: T450 (stopped at the gate, no arm fitted: 2024 production-minutes drift after the 2026-10-11 clean-up, and
  the coach playoff-habit fallback gap of 5.8 pp against 2 pp). Decisions: `docs/DECISIONS.md` 2026-10-11 23:25 (frozen),
  2026-10-12 00:07 (stopped at its gate), 2026-10-20 (coach rotation is not re-run as a feature).
- Harness pin: none recorded (no `harness(COACH_PHASE)` commit exists; the run stopped at the gate).
- Verdict: closed. Reversal route recorded in DECISIONS: a new rule COACH_PHASE_2, never an edit of this one.

| old path | new path |
|---|---|
| `research/eval/coach_phase_eval.py` | `research/archive/coach_phase/coach_phase_eval.py` |
| `research/eval/coach_phase_gates.py` | `research/archive/coach_phase/coach_phase_gates.py` |
| `tests/research/eval/test_coach_phase_eval.py` | `tests/research/archive/coach_phase/test_coach_phase_eval.py` |
| `tests/research/eval/test_phase_gates.py` | `tests/research/archive/coach_phase/test_phase_gates.py` |

Left in place because other harnesses import them: `research/features/coach_habits.py`, `research/features/phase.py`
(used by `research/budget/asof.py` and the TEAM_BUDGET modules, which stay; see `research/INDEX.md`).
The frozen prereg text still names `research/eval/coach_phase_*.py`; this README is the forwarding note.
Run: `python -m research.archive.coach_phase.coach_phase_eval` (was `research.eval.coach_phase_eval`).
