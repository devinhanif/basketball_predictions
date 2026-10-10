# Pre-registered acceptance criteria + props holdout fix — 2026-10-08

Written **before** any of the three upcoming tests are run (or re-run), per the
"re-derive, don't trust" / no-goalpost-moving discipline. Maintainer and
the implementing agent (the modeler) follow this spec; do not
reinterpret the decision rule after seeing a result. Any deviation goes in
`docs/ESCALATIONS.md` before the run, not after.

All CIs below **must** use the clustered (per-`game_id`) bootstrap —
`nba.props.metrics.paired_score_delta_ci(..., cluster_ids=game_id)` — not the
row-level default. Per `docs/FDR_AUDIT_2026-10-08.md` §0 and
`docs/BOOTSTRAP_CLUSTERED_2026-10-08.md`, the clustering support exists in
`nba/props/metrics.py` and is already wired in `research/props/run.py`, but
**`research/eval/model_routing.py` (line ~103) and `research/eval/routed_eval.py` (line
~117) still call `paired_score_delta_ci` without `cluster_ids`** — row-level,
anti-conservative. Any of the three tests below that routes through those two
modules must have `cluster_ids=game_id` threaded in first, or its CI is
capped at PROVISIONAL regardless of the result. This is a prerequisite code
change for whoever implements the test, not optional polish.

---

## 1. Matchup-3A archetype-level opponent factor

**Metric:** CRPS delta (archetype-adjusted − baseline), paired per player-game,
clustered by `game_id`, one delta per stat in {pts, reb, ast, fg3m}.

**Family:** these 4 deltas are ONE pre-registered family, m=4, run together in
the same job. Do not split them into separate "cycles" later to dodge
correction.

**Decision rule (fixed now):**
1. **Hard safety gate, checked first, not subject to BH:** rebounds' 95%
   clustered-CI **upper bound must be ≤ 0** (no regression on the one stat
   with an existing real win). If this fails → **REJECT**, flag off,
   document. Do not evaluate the other 3 stats' significance as a tiebreaker.
2. If gate 1 passes: apply BH at q=0.05 across the 4 raw p-values (normal
   approx from the clustered CI: `se=(hi-lo)/(2*1.95996)`, `z=|point|/se`).
   **Ship only if at least one stat survives BH in the improving
   (negative-delta) direction.**
3. **Practical-significance floor, pre-registered:** an effect smaller than
   **0.005 CRPS units** (half of rebounds' existing −0.014 win) is "too small
   to justify the added per-date archetype-assignment runtime cost" even if
   statistically significant — report it but do not ship on that basis alone.
4. **Minimum sample size:** ≥500 player-games per stat (clustered estimate).
   Below that, exclude the stat from the BH family (reduce m, don't null it)
   and report "insufficient power to conclude," per the audit's Family C
   caution about per-bucket underpowering.
5. Report **all 4 cells**, win or lose — no curated subset (this is the exact
   failure mode flagged in Family D / archetype-cluster "examples").

**If gate 1 passes but no stat survives BH:** REJECT (no signal), document as
provisional null, keep flag off.

---

## 2. Points routing retest at `n_sims=2000`

**Metric:** points CRPS delta (sim − season-avg), clustered by `game_id`,
same definition as Family A (`docs/FDR_AUDIT_2026-10-08.md`).

**Holdout rule (binding, from the audit's §3 flag):** check first whether the
retest's input rows are identical to the `n_sims=500` run's rows (near-certain,
since no new season exists yet).
- If **same rows**: the result is **EXPLORATORY**, full stop. It may not be
  reported as "points now wins" or used to flip the router config. Label it
  explicitly "exploratory, holdout-reuse risk" in any output.
- If genuinely **new rows** (a pre-committed, previously-untouched slice
  carved out *before* running n_sims=2000, or new 2026-27 games once ingested):
  it may be reported as confirmatory.

**Multiplicity rule:** do not let this escape correction by being "the only
one rerun." Pool the new points p-value with the **original** Family A
rebounds/assists p-values (m=3 total, re-ranked), not a fresh m=1 or m=3
family that resets history. This is the standing rule in §3 below, applied
here specifically because it's the first case where it matters.

**Pre-registered power threshold — is this test even capable of flipping the
verdict:**
- Current half-width ≈0.031 CRPS units at `n_sims=500` (from CI
  [−0.019, +0.043]) ⇒ SE ≈ 0.0158 ⇒ MDE at 80% power ≈ 2.8×SE ≈ **0.044 CRPS
  units** — roughly **3× larger than rebounds' actual effect (0.014)**. The
  `n_sims=500` points test was not powered to reliably detect a rebounds-sized
  effect even though the half-width looked comparable at face value.
- `n_sims=2000` (4× more simulation draws) only shrinks the **Monte Carlo
  noise component** of the per-row CRPS estimate, not the **game-to-game
  sampling variance** across the ~600-game pool, which is the other (likely
  larger) contributor to that 0.0158 SE. We do not have a variance
  decomposition to say which dominates.
- **Pre-registered interpretation rule:** if the retest's half-width shrinks
  to ≤0.02 (MDE ≤0.028), conclude "n_sims was indeed a material noise source,"
  and only then treat a CI-excludes-0 result as informative. If the half-width
  does not meaningfully shrink, conclude "n_sims was not the bottleneck; the
  original underpowered-tie verdict stands" — do not reinterpret a
  similar-width CI around a different point estimate as a new finding.
- **Router gate:** points moves from "route to season-avg" to "route to sim"
  only if (a) BH-corrected p survives at q=0.05 in the m=3 pooled family AND
  (b) the holdout-reuse rule above is satisfied (fresh rows). Otherwise stays
  "no change, exploratory only."

---

## 3. Rung-4 step-heads as a third router candidate

**Metric:** CRPS vs. each incumbent (sim, season-avg) in the **same**
stat×bucket cell, clustered bootstrap.

**Family, declared now, before training:** 3 stats × 5 volatility buckets ×
2 incumbent comparisons = **m=30 cells, pre-registered in full**. This
explicitly closes the Family-D hole (curated "examples" instead of the full
grid) — report all 30, win or lose, no subset.

**Decision rule:** rung-4 enters the router for a given cell only if it beats
**both** incumbents in that cell with BH-corrected (q=0.05, m=30) CI excluding
0 in the improving direction. Beating only one incumbent is not sufficient
(the router already has that incumbent as an option).

**Minimum sample size:** ≥150 player-games per cell (smallest reliable bucket
per CLAUDE.md cold-start guidance). Cells below that: excluded from m, reported
as directional-only, never counted as a router decision.

**Holdout discipline:** rung-4 training/hyperparameter selection uses
walk-forward CV restricted to `season < 2025` only (never the frozen holdout).
Exactly **one** confirmatory evaluation on `season=2025` is permitted, and it
must be logged to `docs/HOLDOUT_ACCESS_LOG.md` (see §2 below) **before** it is
run, not after. GPU/Colab is an infra choice and does not relax any of the
above; seed must still be logged.

---

## Canonical frozen holdout for props/routing (mirrors the Elo discipline)

**Mechanism:** reuse `nba.eval.walkforward.split_frozen_holdout` verbatim,
keyed on the existing `season` column, applied to the props/routing/matchup
evaluation dataframes exactly as it's already applied to Elo via
`research/eval/ga_tune.py --holdout-season` (default `2025`).

**Definition:** `holdout_season = 2025` (the 2025-26 season) — **one
project-wide holdout season**, not a separate one per domain. `season < 2025`
(2022-23, 2023-24, 2024-25) is the tunable/training pool for props work,
forever. Exact row/game count for `season=2025` is not estimated here —
maintainer should run a read-only `COUNT(*)` and log it to the ledger below
(`SELECT season, count(distinct game_id), count(*) FROM player_game_stats
JOIN games USING(game_id) WHERE season=2025 GROUP BY season;` against
`nba.duckdb` opened `read_only=True`).

**Already burned — season=2025 rows have been looked at, directly or via
pooled aggregates spanning all 4 seasons, by every one of these existing
results, and none of them may be re-cited as a "clean first touch" of the
holdout going forward:**
- Family A: overall sim-vs-season-avg CRPS (the rebounds win, points/assists
  ties) — pooled across all 4 seasons.
- Family C: 15-cell volatility-bucket routing grid.
- Family D: archetype-cluster routing (6 clusters × stats).
- Family E: the ad hoc "walk-forward vs. holdout" routing split referenced in
  NEXT_SESSION.md — never used `split_frozen_holdout`, assembled unrecorded,
  near-certainly overlaps `season=2025`.
- Family F: matchup/opponent adjustment, both runs (330f036 and 3A rerun
  f150583).
- Family G: lineup archetype-mix non-additivity test.
- Family H: minutes 1A (`learned_context_cal_frac=0.5` chronological split —
  audit confirms this almost certainly included `season=2025` rows).

**Consequence:** there is currently no genuinely virgin confirmatory holdout
left for props. Rule going forward:
1. Do **not** describe any new `season=2025`-touching result as a clean
   "frozen holdout confirmation" unless it's a model that has **never before**
   been evaluated on any data (e.g. rung-4, first touch only, per §3 above).
   Everything else that re-touches `season=2025` is labeled "repeated-use
   holdout, informal cross-check," not a confirmatory result.
2. **Real fix:** once the 2026-27 season has ≥20 games ingested, re-designate
   the canonical props holdout as `season=2026` and fold `season=2025` back
   into the training/tunable pool. This is the only way to get a truly clean
   holdout again — flag this as a standing TODO, re-check at the start of
   every session once 2026-27 games exist.
3. **Code gap to close (modeler, not done here):**
   `research/props/run.py`'s props path, `research/eval/model_routing.py`, and
   `research/eval/routed_eval.py` do not call `split_frozen_holdout` at all
   (confirmed by grep, audit §3) — any "holdout" split they produce is ad hoc
   and unrecorded. Thread `holdout_season` through all three via the shared
   `nba.eval.walkforward` function before the next routing/matchup eval runs,
   so a holdout split is reproducible from committed code, not reassembled by
   whoever happens to run it.
4. **Ledger:** create `docs/HOLDOUT_ACCESS_LOG.md`, append-only, one row per
   confirmatory holdout touch: `date | run_id/command | model family | stat(s)
   | result summary`. Write the planned entry **before** running (pre-register
   the touch), then fill in the result after. This is how "at most once" gets
   enforced instead of trusted.

---

## Standing multiplicity / FDR plan (for all future A/Bs, not just these 3)

1. **Pre-register the family before running.** For every evaluation cycle,
   write `docs/TEST_PLAN_<date>.md` enumerating every comparison to be run,
   its family tag, and `m`, before looking at any result.
2. **Maintain `docs/TEST_LEDGER.md`**, append-only: one row per test ever run
   — date, family tag, stat/cell, point estimate, CI lo/hi, p-approx, BH
   verdict. This is what lets a *later* rerun of the same comparison (like
   the points retest above) be corrected against its **full history**, not a
   fresh small-m family each time (re-rank the union of old+new p-values for
   that exact comparison).
3. **Apply BH within each pre-declared family** at that family's own q
   (default 0.05). Never pool unrelated families after the fact to borrow
   significance; never split one family into smaller ones after seeing
   results to dodge correction. Both directions are banned.
4. **No numeric CI committed to the repo (JSON/CSV, not just a markdown
   checkmark) → capped at PROVISIONAL, permanently**, regardless of how
   confident the writeup sounds. This closes the single biggest hole from
   tonight's audit (Families C, D, E, F had no retained numbers to correct).
5. **Row-level-only bootstrap (no `cluster_ids`) → also capped at
   PROVISIONAL**, same reasoning — it's anti-conservative by construction, so
   its CIs cannot be trusted at face value for a ship/no-ship decision.
6. Report every "win" with: raw p-approx, family size `m`, BH-adjusted
   threshold, and CONFIRMED/PROVISIONAL/REJECTED — the format already used in
   `docs/FDR_AUDIT_2026-10-08.md`. Keep using that format; don't regress to
   checkmark tables.

---

## Summary

- **3A archetype gate:** ships only if rebounds CI upper bound ≤0 (hard gate)
  AND ≥1 of {pts,reb,ast,fg3m} survives BH (m=4, q=0.05) in the improving
  direction AND effect ≥0.005 CRPS — report all 4 cells, no cherry-picking.
- **Points n_sims=2000 gate:** result is exploratory-only if it reuses the
  same rows as the n_sims=500 run; confirmatory only on fresh rows. Must be
  BH-corrected against the ORIGINAL Family-A rebounds/assists p-values
  (m=3 pooled), not treated as a new isolated test. Current MDE (~0.044 CRPS)
  is ~3x rebounds' effect size — this test was underpowered at n_sims=500,
  and more sims may not fix it if game-sampling variance (not MC noise)
  dominates.
- **Holdout, one line:** `season=2025` is the single project-wide frozen
  holdout (mirroring Elo's `--holdout-season 2025`), already burned by every
  props/routing/matchup/minutes result run to date — treat further touches as
  "repeated-use, informal" until 2026-27 data lets us roll the holdout forward
  to a genuinely virgin season.
- Rung-4 must declare its full 30-cell grid up front and beat both incumbents
  per cell to enter the router; one confirmatory holdout touch only, logged
  before it happens.

Relevant files read: `docs/FDR_AUDIT_2026-10-08.md`, `NEXT_SESSION.md`,
`nba/eval/walkforward.py`, `research/eval/ga_tune.py`, `nba/props/metrics.py`,
`research/props/run.py`, `research/eval/model_routing.py`, `research/eval/routed_eval.py`,
`docs/BOOTSTRAP_CLUSTERED_2026-10-08.md`, `docs/RESULTS_2026-10-08.md`.
