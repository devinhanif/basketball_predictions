# NBA prediction

A personal research project. It forecasts NBA games and player stat lines, scores itself on what actually
happened, and compares itself to betting markets. It is a way to learn how the game and forecasting both work.
If it cannot beat the market, that is a result we are proud of.

## What it is not

- It is **read-only with respect to markets.** There is no order code and no credentials in the repo. Nothing
  here places a bet. A person places every bet by hand.
- It is not a product, a tipster or financial advice.

## The honest current answer

As of 2026-11-16 (detail and sources in `docs/PROJECT_STATUS.md`, `CLAUDE.md` and `docs/TEST_LEDGER.md`):

- The market beats the model on game winners. In the 2025-26 replay the model's Brier score was 0.0064 worse
  than Kalshi's price. It also beat the model on every props cell measured so far.
- The model's edge over a plain Elo baseline is small: log loss -0.0081 [-0.0121, -0.0038], n = 3,951 games,
  out-of-fold 2022-24. Win accuracy is near 64%.
- The parlay engine's model weight is **0**. The "fair price" it shows is the market's, not ours.
- So the answer to "what should I bet?" is **keep your money** until the live forward log proves skill.

## Map

| Directory | What is in it |
|---|---|
| `nba/` | The live system: ingest, parse, features, models, the daily pipeline, markets, parlay engine, JARVIS (Discord), export |
| `research/` | Finished and running experiments. `nba/` never imports it |
| `tests/` | The test suite, including the layering test |
| `app/` | A separate read-only web app (API and PWA). It reads only the files `nba.export` writes. Not deployed |
| `configs/` | YAML settings: fees, odds schedules, model settings, JARVIS rules |
| `ops/` | The shell wrappers and launchd files that run the jobs on one Mac |
| `docs/` | The memory: ledger, decisions, pre-registrations, reviews, handoffs |

`data/` (databases, raw pulls) and `reports/` (daily outputs) hold local state and are mostly not tracked.

## Set up and check

```
make setup     # uv sync --extra dev
make lint      # ruff check, ruff format --check, mypy on nba/ research/ tests/
make test      # the full suite
```

- `make test` runs **two pytest processes**. torch and LightGBM each bundle their own OpenMP runtime and
  deadlock when both train in one process on macOS.
- The live `.venv` has no dev tools (no pytest, no ruff), per the 2026-11-16 survey. Run checks from a dev environment.
- A fresh git worktree needs `uv sync --frozen --all-extras` before checks will run.
- Never pipe a check through `tail`; read the exit code.

## Run the app

See `app/README.md`. One line to start the API: `cd app/api && uv sync && uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8000`.

## Two safety checks for any refactor

1. **Layering test**, `tests/test_layering.py`: `nba/` never imports `research/` or `app/`, and `app/` never
   imports `nba/`.
2. **Replay oracle**: replay the first 20 dates of season 2025 through the real daily process on a copy of the
   database, then compare a hash of `forward_predictions` and `forward_scores`. The golden hash is `89889f6d`
   (46,658 predictions). **Log a row in `docs/HOLDOUT_ACCESS_LOG.md` first**, because the replay reads the
   frozen holdout season. The command is in `docs/reviews/replay_oracle_2026-10-09.md`. It is run by hand,
   not in CI.

## Where the memory lives

| Need | File |
|---|---|
| How we work, the rules | `CLAUDE.md` |
| What we tried and what happened (T001 onward) | `docs/TEST_LEDGER.md` |
| Every direction-setting choice: what, why, what would reverse it | `docs/DECISIONS.md` |
| Every touch of the frozen holdout | `docs/HOLDOUT_ACCESS_LOG.md` |
| Rules learned from incidents | `docs/BEST_PRACTICES.md` |
| What is running now | `docs/PROJECT_STATUS.md`, `docs/DAILY_PIPELINE.md` |
| Resume here | `docs/NEXT_SESSION.md` |
| Live-season scoring rules (frozen) | `docs/FORWARD_PREREG_2026_27.md` |
| Pre-registered tests and adversary reviews | `docs/prereg/`, `docs/reviews/` |

## Governance in five lines

1. A decision rule is committed before any result exists, and never changed after.
2. The holdout (season 2025) is never touched without a logged row first.
3. A claim carries n and a clustered confidence interval, or it is not a claim.
4. No orders, no credentials, no execution code, ever.
5. Money, production changes and anything outward-facing are the maintainer's decisions; every direction-setting
   choice gets a line in `docs/DECISIONS.md`.

The full rules are in `CLAUDE.md`. No AI attribution appears in commits or repo content.
