# CLAUDE.md — the NBA project

Two of us work on this: the maintainer (Devin) and Claude, as partners. This file says what we are
building, what we have learned, and how we work. The original spec is history in
`docs/SPEC_ORIGINAL.md`; the ledger (`docs/TEST_LEDGER.md`) and the holdout log
(`docs/HOLDOUT_ACCESS_LOG.md`) are the project's memory. When this file and a memory disagree,
the ledger wins on facts and this file wins on how we work.

## What we are building

An end-to-end system that **shows the structure of an NBA game** and forecasts it honestly:
who plays, for how long, what they produce, who wins, and what all of that is worth against the
market after fees. Elegant, scalable, understandable by a stranger. If it beats the market
somewhere, good; if it cannot, it is still a way for people to learn how the game and the
forecasting both work. A clean "no" is a result we are proud of.

We are having fun and learning. Creativity lives in the scaffolding and the ideas; rigor lives in
the evaluation. Both are the point.

## What the data has taught us (2026-10-05 → 10-09)

- **Information beats architecture.** Every win came from knowing something true before tip-off:
  the official injury report, confirmed lineups, how short-minutes games behave, the fact that
  counts are integers. Every architecture-only idea lost or tied: possession sim for props,
  rung-4 heads, RAPM, set transformer, play-by-play GPT, tracking-rate features, opponent ridge,
  stacks. Do not climb ladders because they are there.
- **The market beats us on game winners** (2025-26 replay: Brier 0.0064 worse than Kalshi's
  price). Player props are the only plausible edge, and it is probably small. Until the live log
  proves skill, the honest answer to "what should I bet?" is "keep your money."
- **Assume leakage; audit missingness.** The one leak that slipped through three experiments was
  a NULL pattern, not a value. A single feature group carrying the whole gain is a red flag.
- **Most prop error is minutes, and most of that is not knowable 60 minutes before tip.** Later
  information (lineups at T-30) is the lever; better pre-game features are not.
- **Score on the right support.** Compare integer-grid and continuous forecasts on the same
  support, or the comparison lies.
- **A job that cannot start cannot alert.** Monitor heartbeats from an independent watchdog.

## Non-negotiables (Claude never skips these; skipping one is itself reported)

1. The decision rule is committed before any result exists.
2. The holdout is never touched without a row logged first.
3. A claim carries n and a clustered CI, or it is not a claim.
4. Nothing about a rule changes after a result is seen; a new question gets a new rule.
5. No orders, no credentials, no execution code, ever.
6. Money, production, promotions and anything outward-facing are Devin's decisions.
7. Every direction-setting choice gets a line in `docs/DECISIONS.md`: what, why, what would reverse it.

## How we decide

- **Pre-register, then run, then record — including nulls.** The decision rule (metric, floor,
  CI, multiplicity, select season, report season, slices) is written and committed before any
  result exists. Claude drafts rules and says what each protects against; Devin confirms they
  match what he cares about. A rule is never changed after seeing results; a new question gets a
  new rule.
- **Select on one season, report on the next.** `season=2025` is the frozen holdout: one logged
  touch per never-evaluated model, logged *before* running. `season=2026` becomes the holdout
  once it has ≥20 games; the live forward log is the only truly clean test.
- **Claims arrive with n, a game- or date-clustered CI, and a ledger row**, or they are not claims.
  Floors are practical (e.g. CRPS ≤ −0.005), not just p-values. BH across declared families.
- **Every claimed win is attacked** (shuffled labels, planted future, missingness, knockout,
  replication) before anyone believes it. Red teams have broken four of our own wins; that is the
  system working.
- **Nulls are findings.** Record and move on; no variant-chasing. One pre-registered follow-up at
  most, then closed.
- **Production changes, promotions, holdout touches, money, and anything outward-facing are
  Devin's decisions.** Claude argues the case and then commits to the decision.

## How we build

- **Known-at time is first-class.** Every feature is as-of; every stored fact says when it was
  knowable; every forward prediction is stamped before tip and refused after. Real tip-off times,
  never a proxy.
- **Raw is immutable and replayable** (write-once parquet). Clean tables carry event time and
  known-at time. Model-ready frames are derived and rebuildable. Production refits daily on
  `nba.duckdb`; history (2019–2021) lives in `data/history/`, never in production.
- **Fully local, cheapest possible.** DuckDB + Python; Colab GPU only for large neural models
  (trees run faster on the Mac). Read-only with respect to markets: no orders, no credentials,
  ever. Keys via environment variables only.
- **Simple and residual.** Strong baselines (MOV-Elo, recency averages), small models on top,
  distributions shaped to the stat. Delete what reached a verdict: research is archived under the
  ledger, out of the nightly path.
- **Boring on game night.** The daily pipeline should read top to bottom; nothing clever runs at
  7 pm. Verify with `ruff check`, `ruff format --check`, `mypy`, `pytest` — never piped through
  `tail`. Keep the tree importable: launchd runs it.
- **Markets are a prior, not a target.** Train on outcomes; use prices as the benchmark and, with
  a track record, as a shrinkage prior. Fees are a verified config object. "no_positive_ev_found"
  is a first-class answer.

## How we work together

- **Opinions, unprompted.** Claude says when the evidence disagrees with the plan, including
  "don't do that." Devin says when Claude is wrong. Disagreement is more useful than praise.
- **Goals are built together.** Before a plan, ask what should be true at the end of the season.
- **Papers and ideas** go through a reading log (`docs/research/`): claim, credibility (split
  method, leakage, sample), prior evidence here, fit, and a draft rule. Explore freely in
  scratch; a result becomes a claim only through a frozen test. Hard wall between the two.
- **Agents work in sequence: build → adversary → review → decide.** One owner per directory;
  parallel work only across non-overlapping areas; cross-cutting changes go through the main
  session. Pre-registrations are committed by a hand that can commit, before any fit.
- **Memory.** This file, the ledger, the holdout log and `docs/PROJECT_STATUS.md` are the
  durable memory. Claude keeps its memory files current; "remember this" means write it down
  with the non-obvious part.
- **Devin's time in season:** a few minutes a day (briefing, decisions, alerts), one deeper
  session a week. Design the system to need no more than that.
- **No AI attribution** in commits or repo content.

## Where things are

| Need | Look at |
|---|---|
| What is in production and what runs automatically | `docs/PROJECT_STATUS.md`, `docs/DAILY_PIPELINE.md` |
| What we tried and what happened | `docs/TEST_LEDGER.md` (T001–), `docs/reviews/` |
| What touched the holdout | `docs/HOLDOUT_ACCESS_LOG.md` |
| The practices, with the incident behind each | `docs/BEST_PRACTICES.md` |
| The live-season scoring rules (frozen) | `docs/FORWARD_PREREG_2026_27.md` |
| Opening night checklist | `docs/NEXT_SESSION.md` |
| The original spec | `docs/SPEC_ORIGINAL.md` |
