# CI/CD pipeline

Implements part of CLAUDE.md's "CI/CD" section. Workflow file:
`.github/workflows/ci.yml`. All jobs run on `ubuntu-latest`, free-tier
GitHub-hosted runners, with a shared `actions/checkout` -> `astral-sh/setup-uv`
-> `actions/setup-python` -> `uv sync --extra dev` step sequence and the
repo's `uv.lock` for reproducible installs.

## Jobs implemented so far

| Job | Trigger | What it does |
|---|---|---|
| `lint-type` | every push to `main` / every PR | `ruff check`, `ruff format --check`, `mypy nba/` |
| `unit` | every push / PR | `pytest` with the coverage ratchet gate (`--cov-fail-under=70`, see `pyproject.toml`) |
| `data-contract` | every push / PR | `make data-contract`: schema tests (`tests/data/test_schema.py`), fixture-coverage tests (`tests/data/test_fixtures.py`), no-leakage tests (`tests/ml/test_no_leakage.py`), and the possession-count-reconciliation test (`tests/data/test_possession_reconciliation.py`, currently `@pytest.mark.skip` pending the PBP parser -- the job still *runs* it, so it activates automatically the moment that skip is lifted). Coverage gate disabled (`--no-cov`) since this is a test-selection subset, not the whole suite. |
| `smoke-backtest` | every push / PR, after `lint-type`/`unit`/`data-contract` pass | `make smoke-check`: runs `make smoke` (`python -m nba.eval --config configs/rung_ladder_fixture.yaml --register`, the full train -> walk-forward backtest -> report -> registry-log path on the committed fixture), then `python -m nba.registry.smoke_guard`, which fails the job if `report.md` was not produced, is empty, or contains a literal `"NaN"` in any rendered metric. The run step itself (not dependency install) has a 3-minute `timeout-minutes` budget. |
| `model-gate` | PRs only, and only when the diff touches `nba/models/`, `nba/props/`, or `nba/parlay/` (via `dorny/paths-filter`) | `make gate`: re-runs the fixture ladder in-process and fails if log loss, Brier, or ECE regresses beyond a configured tolerance vs. a committed baseline. See "Model-gate production reference" below. |

### Deprecation-warning cleanup

Pinned actions were bumped to their current majors (`actions/checkout@v7`,
`actions/setup-python@v7`, `astral-sh/setup-uv@v10`, `dorny/paths-filter@v4`)
to clear the Node 20 -> Node 24 runner deprecation warning. All pins stay on
major-version tags (no `@latest`/`@main`), matching the repo's existing
convention.

## Model-gate production reference

CLAUDE.md's local registry backend (`nba/registry/local.py`) stores its
state in the `experiments` DuckDB table plus `./registry_store/` on disk.
Both are gitignored by design (CLAUDE.md "Hard constraints": never commit
the DuckDB file; "Cheapness rules": the registry store holds only small
artifacts, and nothing about it says "commit it to git"). That means a
fresh CI checkout has **no persisted registry state at all** to query for
"the current production version's metrics" -- there is nothing to diff
against unless something durable is committed.

`tests/registry/baselines/fixture_metrics.json` is that durable stand-in:
a snapshot of the fixture ladder's `log_loss` / `brier` / `ece` (and a
`crps: null` placeholder, since CRPS doesn't exist until Phase 2 props
ships) for every rung, captured at the point the ladder was last
intentionally reviewed and accepted. `nba/registry/model_gate.py` loads it,
re-runs the same fixture ladder in-process to get "candidate" metrics
(never registering anything -- the gate is a pure comparison), and fails if
any gated metric for a given `model_name` regresses beyond its tolerance.

Tolerances live in `nba/registry/model_gate_config.yaml` (never hardcoded
in the gate's logic):

```yaml
log_loss_tolerance: 0.05
brier_tolerance: 0.03
ece_tolerance: 0.08
crps_tolerance: 0.05
```

### Refreshing the baseline

Refresh `tests/registry/baselines/fixture_metrics.json` only when a change
to `nba/models/`, `nba/props/`, or `nba/parlay/` is being *intentionally*
promoted (i.e. the ladder rules in CLAUDE.md's "Architecture ladder" section
say the new rung should be kept). Do not refresh it just to make a failing
gate pass -- that defeats its purpose.

```bash
uv run python -c "
import json
from nba.registry.model_gate import candidate_metrics_from_fixture
m = candidate_metrics_from_fixture()
for metrics in m.values():
    metrics['crps'] = None
with open('tests/registry/baselines/fixture_metrics.json', 'w') as f:
    json.dump(m, f, indent=2, sort_keys=True)
    f.write('\n')
"
```

Then commit the updated JSON alongside the model change, in the same PR,
with a note in the PR description explaining which ladder comparison
justified the promotion (per CLAUDE.md: "a rung is kept only if it beats
the previous on walk-forward log loss").

Once a real (non-fixture) walk-forward backtest and a persisted,
long-running registry exist (i.e. `nba.duckdb` + `registry_store/` kept
across runs on a real machine, not CI), the same `model_gate.py` module can
be pointed at `LocalRegistry.load_model(model_name, alias="production")`
instead of the committed JSON -- `candidate_metrics_from_fixture` and
`load_baseline` are already split into separate functions specifically so
that swap only touches `main()`, not `evaluate_gate`.

## Deliberately not built in this milestone

Per CLAUDE.md's "CI/CD" section, the remaining pipeline stages are:

- **`build`**: CPU Docker image (pinned base, lockfile via `uv`), tagged
  with the git SHA, running the smoke test inside the container.
- **`release`** (on tag): publish the image, attach `report.md` and
  registry metadata as release artifacts.
- **`nightly`** (scheduled): incremental ingest, settle paper trades,
  regenerate the calibration report, open an issue on ingest/calibration
  drift.

These are out of scope for this milestone to keep it reviewable and keep CI
fast; a `Dockerfile` already exists in the repo root (owned by this same
role) but is not yet wired into a CI `build` job.
