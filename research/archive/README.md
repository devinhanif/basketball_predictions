# research/archive/ - closed rules, one directory per rule

Each directory holds the harness of one pre-registration that reached a verdict in `docs/TEST_LEDGER.md`,
with a `README.md` that says which rule, which T rows, the frozen sha and harness pin as recorded, and the old
and new path of every file. Frozen prereg text (`docs/prereg/<RULE>.md` above its `=== RESULTS BELOW ===`
marker) is never edited, so it still names the old `research/eval/...` paths; the README here is the forwarding
note. The code stays importable and its tests run in CI under `tests/research/archive/<rule>/`. Nothing under
`nba/` imports `research.*` (`tests/test_layering.py`).

The harness pins are sha256 over `path:blob` lines, so the move changes the pin-by-path of every moved file.
To reproduce a frozen run, check out the pin commit named in the rule's README; do not run the pin check from
this tree.

| rule | verdict | directory |
|---|---|---|
| COACH_PHASE | stopped at its gate (T450) | `coach_phase/` |
| PRESEASON_ROLE_PRIOR | FAIL_ONE_SEASON (T500) | `preseason_role_prior/` |
| WOWY_STRUCT | FAIL_ONE_SEASON (T499) | `wowy_struct/` |
