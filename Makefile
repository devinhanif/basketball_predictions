UV := $(shell command -v uv 2>/dev/null)
PY := python3

.PHONY: setup test lint backtest smoke smoke-check report data-contract gate

## Install all dependencies (prefers uv, falls back to pip + venv).
setup:
ifdef UV
	uv sync --extra dev
else
	$(PY) -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"
endif

## Run the test suite with the coverage ratchet gate.
test:
ifdef UV
	uv run pytest
else
	. .venv/bin/activate && pytest
endif

## Lint + format-check + type-check nba/.
lint:
ifdef UV
	uv run ruff check nba/ tests/
	uv run ruff format --check nba/ tests/
	uv run mypy nba/
else
	. .venv/bin/activate && ruff check nba/ tests/ && ruff format --check nba/ tests/ && mypy nba/
endif

## Walk-forward backtest + report + registry logging. Override the config to
## run against real multi-season data: `make backtest CONFIG=configs/rung_ladder_full.yaml`.
CONFIG ?= configs/rung_ladder_fixture.yaml
backtest:
ifdef UV
	uv run python -m nba.eval --config $(CONFIG) --register
else
	python -m nba.eval --config $(CONFIG) --register
endif

## Fast end-to-end smoke run on the tiny committed fixture (<3 minutes):
## full train -> backtest -> report -> register path. Used by CI smoke-backtest.
smoke:
ifdef UV
	uv run python -m nba.eval --config configs/rung_ladder_fixture.yaml --register
else
	python -m nba.eval --config configs/rung_ladder_fixture.yaml --register
endif

## Regenerate the per-experiment report.md (re-runs the fixture ladder).
report:
ifdef UV
	uv run python -m nba.eval --config configs/rung_ladder_fixture.yaml
else
	python -m nba.eval --config configs/rung_ladder_fixture.yaml
endif

## Data-contract tests only (schema, fixture coverage, no-leakage,
## possession-count reconciliation) -- no coverage gate, no network. Matches
## CI's `data-contract` job.
data-contract:
ifdef UV
	uv run pytest tests/data tests/ml/test_no_leakage.py --no-cov -q
else
	$(PY) -m pytest tests/data tests/ml/test_no_leakage.py --no-cov -q
endif

## Run the smoke backtest, then fail if report.md was not produced or any
## headline metric is NaN. Used by CI's `smoke-backtest` job (<3 min budget).
smoke-check: smoke
ifdef UV
	uv run python -m nba.registry.smoke_guard
else
	$(PY) -m nba.registry.smoke_guard
endif

## Model-gate: re-run the fixture ladder and fail if any gated metric
## (log loss, Brier, ECE, CRPS) regresses beyond its configured tolerance
## vs. the committed baseline (tests/registry/baselines/fixture_metrics.json
## -- the CI stand-in for the persisted production registry version; see
## docs/ci_cd.md). Used by CI's `model-gate` job.
gate:
ifdef UV
	uv run python -m nba.registry.model_gate
else
	$(PY) -m nba.registry.model_gate
endif
