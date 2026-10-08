UV := $(shell command -v uv 2>/dev/null)
PY := python3

.PHONY: setup test lint backtest smoke smoke-check report data-contract gate gate-local registry

## Install all dependencies (prefers uv, falls back to pip + venv).
setup:
ifdef UV
	uv sync --extra dev
else
	$(PY) -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"
endif

## Run the test suite with the coverage ratchet gate.
## Split into two pytest invocations: torch (rung-4 step-heads) and lightgbm
## (rung-2) each bundle their own OpenMP runtime and DEADLOCK when both run real
## training in a single process on macOS/Homebrew (a plain `pytest` hangs ~halfway).
## Isolating tests/ml/test_rung4_stepheads.py in its own process avoids it. Coverage
## accumulates across both runs (--cov-append) and the 70% ratchet is enforced once
## on the combined result.
RUNG4_TEST := tests/ml/test_rung4_stepheads.py
test:
ifdef UV
	uv run pytest --ignore=$(RUNG4_TEST) --cov-fail-under=0
	uv run pytest $(RUNG4_TEST) --cov-append --cov-fail-under=0
	uv run coverage report --fail-under=70
else
	. .venv/bin/activate && pytest --ignore=$(RUNG4_TEST) --cov-fail-under=0
	. .venv/bin/activate && pytest $(RUNG4_TEST) --cov-append --cov-fail-under=0
	. .venv/bin/activate && coverage report --fail-under=70
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

## Local model-gate: compare the newest candidate of each model against the REAL
## production version in the local registry (nba.duckdb, opened read-only).
## CI keeps using the committed fixture baseline via `make gate`.
## Narrow with: make gate-local GATE_ARGS="--model rung0_mov_elo --candidate v2"
GATE_ARGS ?=
gate-local:
ifdef UV
	uv run python -m nba.registry gate $(GATE_ARGS)
else
	$(PY) -m nba.registry gate $(GATE_ARGS)
endif

## List registered models x versions x stage x key metrics (read-only).
registry:
ifdef UV
	uv run python -m nba.registry list
else
	$(PY) -m nba.registry list
endif
