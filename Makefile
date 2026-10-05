UV := $(shell command -v uv 2>/dev/null)
PY := python3

.PHONY: setup test lint backtest smoke report

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

## Full walk-forward backtest entrypoint. Placeholder until nba/eval ships
## the backtest harness (Phase 1, Milestone 4 in CLAUDE.md).
backtest:
ifdef UV
	uv run python -m nba.eval.backtest 2>/dev/null || echo "backtest: not implemented yet (see nba/eval/)"
else
	echo "backtest: not implemented yet (see nba/eval/)"
endif

## Fast end-to-end smoke run on the tiny fixture dataset (<3 minutes).
## Placeholder until the fixture dataset and eval harness land.
smoke:
ifdef UV
	uv run python -m nba.eval.smoke 2>/dev/null || echo "smoke: not implemented yet (see nba/eval/ and the fixture dataset)"
else
	echo "smoke: not implemented yet (see nba/eval/ and the fixture dataset)"
endif

## Regenerate the per-experiment report.md.
report:
ifdef UV
	uv run python -m nba.eval.report 2>/dev/null || echo "report: not implemented yet (see nba/eval/)"
else
	echo "report: not implemented yet (see nba/eval/)"
endif
