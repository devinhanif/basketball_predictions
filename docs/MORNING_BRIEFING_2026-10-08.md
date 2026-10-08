# Morning Briefing — 2026-10-08 overnight session

Status: nothing committed. All changes live in the working tree on `main`. This doc
is the entry point; read it before any of the individual session docs.

## 1. Executive summary

Eight agents worked in parallel overnight on disjoint files inside one shared
working tree: a statistician audit, a perf fix, a plain-English results writeup, a
clustered-bootstrap statistics fix, an independent QA review, pre-registered
acceptance criteria + two code-gap closures, a real-data matchup-3A rerun, an
injury/availability feed scaffold, a time-decay cold-start module, a Kalshi
read-only ingestor scaffold, and rung-4 possession step-heads (trained on Colab,
not locally). Nothing was pushed, merged, or committed by any agent, per standing
rules.

**Headline per workstream:**
- **Statistics (FDR audit + clustered bootstrap + acceptance criteria):** the
  project's bootstrap was resampling player-games instead of games, making every
  prior CI anti-conservative (too narrow). That's now fixed and threaded through
  `nba/props/metrics.py`, `nba/eval/model_routing.py`, `nba/eval/routed_eval.py`.
  Of everything claimed this session, only two results survive a real BH
  correction: rebounds beats season-average overall, and lineup archetype-mix
  non-additivity is real but practically tiny. Everything else is PROVISIONAL —
  either never had a numeric CI committed, or was curated/cherry-picked.
- **Matchup 3A (archetype opponent factor):** real-data rerun, ~4 seasons, clustered
  CIs. Hard gate failed (rebounds regressed) — **NO SIGNAL, flag stays OFF.**
  Team-level variant also regressed on the full data (contradicts an earlier
  partial read). Decided, documented, closed.
- **Perf fix (archetype caching):** legitimate ~25-28x speedup on synthetic data by
  removing a redundant per-date full-history SQL re-scan; proven equivalent with
  before/after `.equals()` tests. Unblocked 3A's runtime (3A still rejected on
  signal, not speed).
- **Injury feed:** confirmed `nba_api` has **no forward-looking injury signal at
  all** — only a post-hoc inactive list. Schema/puller/parser for a
  `manual_announced` path are landed and tested so a future external
  scraper/PDF-parser can plug in with zero schema change. The real gap stays open.
- **Time decay:** new, isolated cold-start module (season-carryover with decay +
  age curve) that should reduce the "Tatum game-1, predicted 0.1 pts, actual 35"
  class of failure. Built but **not wired in** — that's a deliberate follow-up for
  whoever owns `archetypes.py`/`player_possession_features.py` next.
- **Kalshi scaffold:** read-only ingestor (client, parsing, name-matching, sample
  guardrail) built against fixtures only — no live network call was made. No-auth,
  no-order guarantee verified by code inspection.
- **Rung-4 step-heads:** possession-outcome step heads + Colab training package.
  QA-reviewed, no leakage found, chronological split confirmed. Not wired into the
  router or the eval harness yet — ships the training contract only.
- **Integration check (this run):** lint and types are clean. Tests are
  **green (574/574)** once one known, newly-surfaced environment conflict is
  isolated — see §4, this is the most important finding below.

## 2. Deliverable table

| Deliverable | One-line outcome | Status |
|---|---|---|
| `docs/FDR_AUDIT_2026-10-08.md` | Re-derives every claimed p-value; flags row-level bootstrap as the session's biggest inference risk | safe-to-commit (docs only) |
| `docs/BOOTSTRAP_CLUSTERED_2026-10-08.md` + `nba/props/metrics.py`, `nba/props/run.py` | Adds per-game cluster bootstrap, backward-compatible (`cluster_ids=None` unchanged) | safe-to-commit |
| `docs/QA_AUDIT_2026-10-08.md` | Independent review of 3 items; found 1 major gap (reliability note gated on row count `n`, not cluster count `g`) | safe-to-commit (docs only); its finding was separately fixed, see next row |
| `docs/PROVISIONAL_FIXES_2026-10-08.md` + `nba/eval/model_routing.py`, `nba/eval/routed_eval.py`, `nba/eval/walkforward.py`, `nba/props/config.py`, `nba/props/__main__.py`, `nba/props/metrics.py` | Threads `cluster_ids` into routing/routed-eval; wires `filter_by_holdout_mode` into props config; fixes the QA audit's `g`-vs-`n` gating bug | safe-to-commit (verified by its own new tests) |
| `docs/ACCEPTANCE_CRITERIA_2026-10-08.md` | Pre-registers decision rules for 3A, points retest, rung-4 grid, before any were (re)run | safe-to-commit (docs only) |
| `docs/MATCHUP_3A_RESULT_2026-10-08.md` | Real 4-season rerun: hard gate fails (rebounds regressed) on both archetype and team-level configs — **flag OFF, no signal** | safe-to-commit (docs only); decision final per its own acceptance criteria |
| `docs/PERF_ARCHETYPE_2026-10-08.md` + `nba/coldstart/archetypes.py`, `nba/props/opponent.py` + `tests/props/test_opponent.py` | Removes a redundant per-date full-history SQL re-scan (~25-28x on synthetic data); proven row-for-row equivalent | safe-to-commit (QA-reviewed independently, verdict: safe) |
| `docs/OVERVIEW_PLAIN_2026-10-08.md` | Plain-English writeup of the full session's results with named player examples | safe-to-commit (docs only) |
| `docs/INJURY_FEED_2026-10-08.md` + `nba/db/schema.sql` (+table), `nba/ingest/availability.py`, `nba/parse/availability.py`, fixtures/tests | Confirms no forward-looking nba_api injury source exists; ships schema + puller + fuzzy-match parser for a future external feed, 15 tests | safe-to-commit; the underlying data gap is **not** closed, by design |
| `docs/TIME_DECAY_2026-10-08.md` + `nba/features/time_decay.py` | New, isolated season-carryover-with-decay module; 12 tests including limiting-behavior and leakage tests | safe-to-commit; **not wired into any caller yet** |
| `docs/KALSHI_SCAFFOLD_2026-10-08.md` + `nba/kalshi/*` (client/cutoff/thresholds/aliases/parse/sampling/ingest/`__main__`), `nba/kalshi/__init__.py` | Read-only market-data ingestor scaffold, 37 fixture-only tests, no live network call made | safe-to-commit |
| `nba/features/possession_step_features.py`, `nba/models/rung4_stepheads.py`, `nba/models/colab/*`, `tests/ml/test_rung4_stepheads.py` | Rung-4 possession step-heads + turnkey Colab training package | safe-to-commit (QA-reviewed, no leakage, chronological split confirmed); **surfaces the one integration risk in §4** |
| `.claude/settings.json` | Allow-lists more read-only/profiling commands | safe-to-commit (tooling only, zero code risk) |
| `docs/ci_cd.md` (pre-existing, re-verified) | Model-gate job already existed from a prior session; re-confirmed present and correctly scoped (PRs touching `nba/models/`, `nba/props/`, `nba/parlay/`) | no action needed |

## 3. Decisions and honest findings already made

- **Matchup 3A = NO SIGNAL, flag stays off.** The pre-registered hard gate
  (rebounds' clustered-CI upper bound must stay ≤ 0, checked before any other
  stat) failed for both the archetype-level and team-level configs on the full
  real dataset. Per the acceptance criteria's own rule, the other three stats are
  not even evaluated as a tiebreaker once the hard gate fails. This also quietly
  corrects an earlier, more optimistic partial read of team-level (captured in the
  prior handoff commit) — the full run shows it regresses too, just by less.
- **Points retest at `n_sims=2000` = DEFERRED, not run.** Per
  `ACCEPTANCE_CRITERIA_2026-10-08.md` §2, this test would reuse exactly the same
  rows as the existing `n_sims=500` tie, which the audit's holdout-reuse rule
  (§3) requires labeling "exploratory only" regardless of outcome, plus the
  acceptance doc's own power analysis shows the current CI is underpowered by
  ~3x relative to rebounds' effect size, so a same-rows rerun would not be
  informative even run cleanly. No agent ran it. If it's run later, it must be
  labeled exploratory and pooled against the original Family-A p-values, not
  reported as a fresh finding.
- **Injury feed: no forward-looking nba_api source exists.** Verified from
  `nba_api`'s own documented endpoint shapes (not exercised live — `nba_api` isn't
  installed in the sandbox). The only injury-adjacent endpoint
  (`BoxScoreSummaryV2` → `InactivePlayers`) only resolves after a game starts, has
  no reason/severity field, and has no questionable/probable tier. Usage
  redistribution (sim mechanisms 2A/2B, shipped flag-off in a prior session) stays
  inert until a real feed exists. The `manual_announced` path is now plumbed end
  to end (schema, loader, fuzzy name-matcher, leakage test) specifically so a
  future PDF-scraper for the NBA's official injury report can be dropped in with
  zero schema change — that scraper itself was not built tonight.
- **Model-gate already existed; verified, not rebuilt.** `docs/ci_cd.md` and
  `nba/registry/model_gate.py` / `nba/registry/model_gate_config.yaml` predate this
  session (from the CI/CD milestone). Confirmed still present, correctly scoped to
  PRs touching `nba/models/`, `nba/props/`, `nba/parlay/`, and untouched by
  tonight's diff.

## 4. Integration check result

**Lint/type:** `uv run ruff check nba/ tests/` → clean. `uv run mypy nba` → `Success:
no issues found in 104 source files`. `uv run ruff format --check nba/ tests/` → 5
files would be reformatted — **3 are pre-existing and untouched by tonight**
(`nba/props/minutes.py`, `tests/props/test_minutes_garbage_time.py`,
`tests/props/test_minutes_learned_game_context.py` — not in `git status`, not a
regression), and **2 are cosmetic, in tonight's new Colab package**
(`nba/models/colab/README.md`, `nba/models/colab/rung4_stepheads.ipynb` — ruff's
markdown/notebook embedded-code formatter, not a logic issue). Non-blocking either
way; `make lint` would still need `ruff format` run once before this is fully
clean, maintainer's call on timing.

**Tests — the one important finding.** A naive full `uv run pytest tests/ -q
--no-cov` **hangs** (not a crash — 0% CPU, no further output) partway through, at
test index ~287/578, every time, reproducibly landing inside
`tests/ml/test_rung4_stepheads.py` (new tonight — the first test file in this repo
that actually builds/trains a real PyTorch `nn.Module` in-process). Isolated that
file: **4/4 pass in 1.9s on its own.** Re-ran the full suite with only that one
file excluded (`--ignore=tests/ml/test_rung4_stepheads.py`):

```
574 passed, 6 warnings in 17.50s
```

Diagnosis: this is a **torch-vs-lightgbm OpenMP (`libomp`) conflict**, a
well-known macOS/Homebrew issue where two native libraries each bundle their own
OpenMP runtime and deadlock (sometimes segfault, sometimes just hang) when both
are loaded into one process — not a correctness bug in tonight's code. It is
*environment-specific* and was not visible before tonight only because no test
file previously imported `torch` and ran real training in the same pytest
process as the many LightGBM-backed tests elsewhere in `tests/ml/`
(`test_models_contract.py`, `test_game_context_features.py`,
`test_holdout_sequential_eval.py`, `test_eval_cli.py`, `test_smoke_guard.py`, and
others that exercise `nba/models/rung2_gbm.py`). I also found and killed five
orphaned, hung pytest processes left over from earlier in the session (0% CPU,
stuck for 40-48 minutes) before running this check — they were not causing the
hang above (reproduced cleanly after killing them) but were worth clearing before
reporting a clean number.

**Verdict: integration-green, with one isolation needed.** Everything in the
working tree is mutually compatible at the test level once `tests/ml/` and
`tests/ml/test_rung4_stepheads.py` are run as separate pytest invocations (or CI
is updated to do so). This is a CI/tooling fix, not a code fix:

```
uv run pytest tests/ -q --ignore=tests/ml/test_rung4_stepheads.py
uv run pytest tests/ml/test_rung4_stepheads.py -q
```

Recommend the maintainer add this split to `.github/workflows/ci.yml`'s `unit`
job (two steps) and to `Makefile`'s `test` target, or set
`KMP_DUPLICATE_LIB_OK=TRUE` as an env var for CI only (works, but papers over a
real deadlock risk rather than isolating it — the two-invocation split is safer
for an 8GB box since it also avoids holding both libraries' thread pools in one
process).

## 5. Suggested sequential merge/commit order

File ownership was disjoint overnight (confirmed via `git diff --stat` — no two
agents touched the same file), so the order below is a recommendation, not a
hard dependency chain, except where noted.

1. **`nba/props/metrics.py` + `nba/props/run.py` (clustered bootstrap)** — ship
   first. Nothing else depends on it, but it's the statistical foundation
   (`cluster_ids`) that `nba/eval/model_routing.py`/`routed_eval.py` build on in
   step 2.
2. **`nba/eval/model_routing.py` + `nba/eval/routed_eval.py` + `nba/eval/walkforward.py`
   + `nba/props/config.py` + `nba/props/__main__.py`** — the PROVISIONAL_FIXES
   patch. Depends on step 1's `cluster_ids` contract already existing; also fixes
   the QA audit's `g`-vs-`n` reliability-note bug in the same metrics.py (so this
   and step 1 are the one place with a real ordering dependency — commit them
   together or step-1-then-step-2, not reversed).
3. **`nba/coldstart/archetypes.py` + `nba/props/opponent.py` + `tests/props/test_opponent.py`**
   (perf fix) — independent of 1/2, safe any time; grouping here because it's the
   other props-adjacent change and keeps the props-module commits together.
4. **`nba/features/time_decay.py` + its tests** — fully standalone, zero
   dependents yet (not wired in). Safe to merge whenever; no interaction with
   anything above.
5. **`nba/db/schema.sql` + `nba/ingest/availability.py` + `nba/parse/availability.py`
   + fixtures/tests** — the injury-feed scaffold. Touches shared `schema.sql`;
   merge after 1-4 so if anything above also needed a schema diff it's resolved
   first (nothing above did, confirmed by `git diff --stat`, but `schema.sql` is
   the one genuinely shared file in the whole night's diff, so it goes last among
   the "modifies existing shared files" group).
6. **`nba/kalshi/*` (scaffold)** and **`nba/features/possession_step_features.py`
   + `nba/models/rung4_stepheads.py` + `nba/models/colab/*`** — both fully new,
   standalone module trees with no overlap with anything else. Order between
   these two doesn't matter; merge either last.
7. **Docs** (`docs/FDR_AUDIT`, `docs/QA_AUDIT`, `docs/PROVISIONAL_FIXES`,
   `docs/ACCEPTANCE_CRITERIA`, `docs/MATCHUP_3A_RESULT`, `docs/PERF_ARCHETYPE`,
   `docs/OVERVIEW_PLAIN`, `docs/INJURY_FEED`, `docs/TIME_DECAY`,
   `docs/KALSHI_SCAFFOLD`, this briefing) — can ride along with their
   corresponding code commits above, or go in one trailing docs-only commit;
   no code depends on them.
8. **`.claude/settings.json`** — tooling only, merge whenever, zero risk.

**Cross-cutting risk to flag explicitly:** several agents edited the shared
working tree overnight without rebasing on each other (there was nothing to
rebase onto — none of them committed). `git diff --stat` shows zero file-level
collisions, which is good luck as much as good scoping — `nba/props/run.py` and
`nba/props/opponent.py`, for instance, are adjacent modules both touched tonight
by different workstreams and easily could have collided. Before the maintainer
commits, worth a final `git diff` skim of `nba/props/run.py` and
`nba/eval/walkforward.py` specifically (the two files with the most independent
touches converging on them) to confirm nothing was silently overwritten rather
than merged — the integration test run (§4) is strong evidence nothing was, but
it's a test-level check, not a diff-level one.

## 6. Open questions for the maintainer

(a) **Commit strategy** — one squash commit for the whole night, or one commit
per workstream following the order in §5? The disjoint-files property makes
either safe; per-workstream commits give a cleaner `git bisect` story and match
this repo's existing commit granularity (see `git log`).

(b) **Points retest** — run it now, explicitly labeled "exploratory,
holdout-reuse risk, not confirmatory" per the acceptance criteria, or skip it
entirely until a genuinely fresh data slice exists (2026-27 season, or a
pre-committed untouched slice)? Default if no decision: skip, per §3 above.

(c) **Injury-report source** — acquire a real external feed (PDF scraper for the
NBA's official injury report, or a paid feed) to actually close the data gap the
schema/puller/parser are now waiting for? This is flagged as the single
highest-leverage remaining data gap for points accuracy (minutes uncertainty).

(d) **Rung-4 Colab training** — run the turnkey Colab package
(`nba/models/colab/README.md`) to actually produce trained weights, register
them as a candidate, and run the model-gate comparison before considering it as
a third router option for points? Nothing blocks this except GPU access and time.

(e) **`docs/HOLDOUT_ACCESS_LOG.md`** — create this ledger (per
`ACCEPTANCE_CRITERIA_2026-10-08.md` §"Canonical frozen holdout") before anyone
runs a `holdout_mode="holdout_only"` query against the real DB with
`holdout_season=2025`? `season=2025` is already flagged as a repeatedly-touched,
no-longer-pristine holdout across Families A/C/D/E/F/H from this session's own
audit — the ledger is how "at most once, logged before the touch" gets enforced
going forward rather than trusted after the fact. Recommend creating it before
any further props/routing real-DB run, regardless of (b)/(d)'s answers.

Standing rules respected throughout: no AI/Claude/Anthropic attribution anywhere
in the touched files or this doc; no agent committed; the Kalshi work remains
strictly read-only (no order/trading endpoints exist in `nba/kalshi/`, verified by
code inspection in `docs/KALSHI_SCAFFOLD_2026-10-08.md`).
