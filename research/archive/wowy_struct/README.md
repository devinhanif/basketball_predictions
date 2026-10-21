# WOWY_STRUCT (archived 2026-10-20)

- Rule: WOWY_STRUCT, `docs/prereg/WOWY_STRUCT.md`. Frozen sha256 `3f78a0f1` (prereg commit `a74eebd`), harness pin `981ebefe` (commit `d71b5d9`).
  The prereg file's own header still reads DRAFT; the ledger row T499 and the commits above are the record of the freeze.
- Ledger: T499, verdict FAIL_ONE_SEASON (pts leader out 2023 clears, 2024 does not; Devin's replacement twist TWIST_TIE in all cells). Closed;
  not promoted; the live log is the only further test. Results: `reports/prereg_wowy_struct/`.
- `wowy_prop_gate.py` is the gate script of WOWY_REDISTRIBUTION follow-up 2 (variant PROP, draft rule, gate only; DECISIONS 2026-10-14);
  it imports `wowy_struct_gate`, so it moves with it. `research/eval/wowy_redistribution_gate.py` and `wowy_redistribution_slices.py`
  (WOWY_REDISTRIBUTION, imported by these modules and in the WOWY_STRUCT pin list) stay in `research/eval/`.

| old path | new path |
|---|---|
| `research/eval/wowy_struct_eval.py` | `research/archive/wowy_struct/wowy_struct_eval.py` |
| `research/eval/wowy_struct_gate.py` | `research/archive/wowy_struct/wowy_struct_gate.py` |
| `research/eval/wowy_struct_null.py` | `research/archive/wowy_struct/wowy_struct_null.py` |
| `research/eval/wowy_struct_crps_null.py` | `research/archive/wowy_struct/wowy_struct_crps_null.py` |
| `research/eval/wowy_struct_power_e7.py` | `research/archive/wowy_struct/wowy_struct_power_e7.py` |
| `research/eval/wowy_struct_repl_gate.py` | `research/archive/wowy_struct/wowy_struct_repl_gate.py` |
| `research/eval/wowy_struct_rule.py` | `research/archive/wowy_struct/wowy_struct_rule.py` |
| `research/eval/wowy_struct_star_cuts.py` | `research/archive/wowy_struct/wowy_struct_star_cuts.py` |
| `research/eval/wowy_prop_gate.py` | `research/archive/wowy_struct/wowy_prop_gate.py` |
| `tests/props/test_wowy_struct_crps_null.py` | `tests/research/archive/wowy_struct/test_wowy_struct_crps_null.py` |
| `tests/props/test_wowy_struct_gate_rule.py` | `tests/research/archive/wowy_struct/test_wowy_struct_gate_rule.py` |
| `tests/props/test_wowy_struct_star_cuts.py` | `tests/research/archive/wowy_struct/test_wowy_struct_star_cuts.py` |
| `tests/research/wowy_struct/test_wowy_struct_eval.py` | `tests/research/archive/wowy_struct/test_wowy_struct_eval.py` |
| `tests/research/wowy_struct/test_wowy_struct_rule.py` | `tests/research/archive/wowy_struct/test_wowy_struct_rule.py` |

Mechanical edits only: imports and `-m` commands repointed, `HARNESS_FILES` in `wowy_struct_rule.py` repointed for the moved files
(files that stayed keep their paths), `__file__` depth `parents[2]` -> `parents[3]` (tests `parents[4]`). The pin `981ebefe` was taken over
the old paths, so `require_harness_pinned` only passes at commit `d71b5d9`; reproduce a frozen run from that commit. Tests that stayed:
`tests/props/test_wowy.py`, `tests/props/test_wowy_repl.py` (they test live `nba/props/wowy*.py`). The frozen prereg text still names
`research/eval/wowy_struct_*.py`; this README is the forwarding note.
Run: `python -m research.archive.wowy_struct.wowy_struct_eval` (was `research.eval.wowy_struct_eval`).
