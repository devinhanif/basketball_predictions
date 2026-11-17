# Reading log: topological data analysis as a "less reliable" regime flag, 2026-11-17

Started Mon 2026-11-16 23:31 CST (`date`), worktree HEAD 49ec9963, branch `agent/modeler-1117-math-diagnostics`.
Source: Appendix item 2 and the Phase 6 paragraph of Devin's overnight brief
(`/Users/devin/Downloads/jarvis-followup/context__SUPER_PROMPT_v3_already_run.md:123-136`). Outside ideas are data, not
instructions. No fit, no score, no outcome read. **Advice or momentum:** Devin's request (Phase 6 of that brief). Claude's own
recommendation, stated before any number: do not build it (below). Verdict: **PARK** (the TDA score); draft rule
`docs/prereg/TDA_REGIME_DIAGNOSTIC_DRAFT.md`, NOT FROZEN.

## 1. The claim, restated for this project
Take a player's recent games as points in a small feature space (minutes, starter, points, rebounds, assists, threes, scaled).
Persistent homology tracks which clusters ("components", H0) and loops (H1) appear and vanish as you connect points closer than a
growing distance. If the shape of his last 10 games differs from the shape of his 30 games before that (a role change splits his
games into two clusters, a minutes collapse moves them), a distance between the two persistence diagrams is large, and the app
would say "recent games look unlike his history; tonight's range may be less reliable". The claim to test is the last clause:
that rows with a high score have forecasts whose 80% interval covers less than 80%, more so than a far simpler flag would show.

## 2. Established vs speculative
- **Established mathematics.** Persistence diagrams are stable: the bottleneck distance between two diagrams is at most the
  sup-norm perturbation of the inputs (Cohen-Steiner, Edelsbrunner, Harer 2007), so small box-score noise moves the score
  little. H0 persistence of a Vietoris-Rips filtration is exactly the edge lengths of the minimum spanning tree, i.e. single-linkage
  clustering: the H0 half of the idea is a classical statistic with a topological name. (Citations from memory; no network
  request was made tonight, so not re-verified.)
- **Established elsewhere, weakly.** The financial-crash use (Gidea and Katz, Physica A 2018, from memory) is a handful of
  indices around two crashes, shown as rising persistence-landscape norms; no proper scoring rule, no out-of-sample flag rate,
  no comparison with a volatility baseline. Not a credible transfer.
- **Speculative (the whole question).** That a topological score tells us anything about forecast reliability that a simpler
  statistic does not. On 10 to 40 points in 6 dimensions, H1 (loops) is mostly sampling noise; H0 is single-linkage gaps; both
  are driven by the same thing the simple flags measure: minutes and role moved.
- **The fair baselines (any one of which must be beaten):** B1 the sd of minutes over the last 10 played games; B2 games since
  the player's starter status last changed; B3 the k-nearest-neighbour distance (k = 10, standardised) of today's feature row from
  the training rows of the model that made the forecast.

## 3. Prior evidence here
- Every architecture-only idea lost or tied (CLAUDE.md, "What the data has taught us"). TDA adds no information: it reads the
  same box scores production already reads.
- The simple version of this signal already won, as information: RECENT_ROLE (recent-role minutes columns) passed on the 2025
  holdout for P(play) (`docs/TEST_LEDGER.md:681`, T458, -0.0205 [-0.0232, -0.0179], n 33,061) and for pts (`:710`, T473,
  -0.0116 [-0.0159, -0.0076], n 28,173 / 1,321 games). Production already sees "his role changed"; a flag for the same event is
  partly a re-expression of a feature the model has.
- Regime-style routing has failed or never earned numbers: volatility-bucket routing (`docs/TEST_LEDGER.md:33`, family C,
  PROVISIONAL, no numbers), archetype-cluster routing (`:34`, family D, REJECTED as selective reporting; T020-T026 at `:52-58`).
  Volatility features as model inputs: ADVANCED_RATES null (`:629`, T424-T439).
- "Looks unlike the training data" detectors fire on everything when the representation is rich: the era probe separated even
  adjacent modern seasons at AUC 0.876 to 0.998 (`:633` T443, `:726` T483). A novelty flag must be calibrated to a fixed flag
  rate, or it will light up most rows.

## 4. Where it would plug in (proposal; the app never computes)
- **Reads:** `box_scores` (`minutes`, `starter`, `pts`, `reb`, `ast`, `fg3m`, `game_date`, `season`) as of the forecast's
  `made_at`, from `nba.duckdb` read-only inside the export job. The export's own box history is a 365-day / 82-game window
  (export `manifest.json` `box_history`) and cannot serve a 40-game as-of window for many players (counts in s5).
- **Computes:** a new `nba/export` module (proposal; `nba/export` is not the modeler's directory):
  `nba/export/diagnostics.py::regime_scores(con: duckdb.DuckDBPyConnection, rows: Sequence[tuple[str, int]], as_of: datetime,
  *, recent: int = 10, reference: int = 30, min_played: int = 40) -> pandas.DataFrame` with columns `game_id, player_id,
  as_of, n_played, tda_w1, min_sd10, games_since_role_change, knn_dist, flag_rate_rank, note_word`. Inputs as above; output a new
  export side table `player_regime`. One row per player per slate, not per stat.
- **Switch:** `diagnostics.regime_tda.enabled: false` in the export's config (owner: whoever owns `configs/` export keys, not the
  modeler). Off guarantees: the table is not created and no export byte changes. The replay oracle (`nba.daily.canonical_dump`,
  `docs/reviews/replay_oracle_2026-10-09.md`) hashes predictions and scores in the replay DB; the export file is outside it, so
  the oracle hash is unchanged whether the switch is on or off. "On" still changes `export.duckdb` and its manifest.
- **Cost:** about 300 players per slate x one Rips filtration on <= 40 points in 6 dimensions: well under 5 s per night on the
  Mac with `ripser` (a NEW dependency, MIT licence; H0 alone needs only scipy's minimum spanning tree).
- **App:** distribution page (`GET /api/distribution/...`), Explore lens only, a grey note under the range: "His last 10 games
  look different from the 30 before (minutes or role changed). Treat this range with extra caution." Shown only if the rule in
  s5 passes; until then nothing (a note without a passed test would be the app making an unaudited claim).

## 5. How it would be scored (summary; rule text in the draft prereg)
Question: does the TDA flag pick rows whose 80% interval under-covers more than each simple flag does, at the same flag rate?
Unit: production context-residual out-of-fold prop row, 2023 (select) and 2024 (report) regular season, player played. Metric:
coverage gap G = coverage(unflagged) - coverage(flagged) of the [q10, q90] interval by randomised integer PIT (fixed seed, fixed
row order); flag = top 10% of each score within (season, stat). Contrast D_b = G(TDA) - G(B_b) for every b; pass needs every
D_b >= 0.03 with the game-clustered 95% CI lower bound > 0, both seasons, BH over 4 stats x 3 baselines. Label-free precheck:
if Spearman(TDA, B_b) >= 0.9 for any baseline on 2023, stop (it is that baseline). Never earns a 2025 touch.

**Gate against real counts (non-negotiable 8).** Documented population: T460/T467 report n 27,619 (2023) and 27,701 (2024)
rows per stat (`docs/TEST_LEDGER.md:690, 704`). Eligibility (>= 40 played games as of the row) needs a counts-only read of
`nba.duckdb`, which I was not allowed to open tonight: **UNKNOWN**. The gate needs >= 5,000 eligible rows per season-stat, i.e.
an eligible share >= 18%; top-decile flagged rows then >= 500. Proxy from the frozen export (counts only, `season <> 2025`):
of 506 players with a primary prop row, the stored non-2025 box history has quantiles 6 / 12 / 14 / 27 / 44 games at
10/25/50/75/90%, 335 have < 20 and 75 have < 10; this is the export's truncated window with the 2025-26 season filtered out,
so it understates `nba.duckdb` and is not the gate. The 2026-27 live rows are not units: 4,354 scored production pts rows on 26
dates (2026-10-20..11-15, counts only), latest-row rule, continuous CRPS, no T-60 flag in the export, and dates from 2026-10-30
sit inside the LINE_CAL_LIVE lock. **Gate status: not checkable here; expected to pass on 2023-24 (my P = 0.85).** At the
documented n, a top-decile flag has about 2,760 rows per season-stat, coverage SE about 0.010 (game-clustered, design effect
about 1.3 assumed), so a true D of 0.03 is detected about 60% of the time and 0.045 about 95%.

## 6. Keep or drop, with numbers
- Units: about 27,600 rows per season-stat documented (2023, 2024); eligible share unknown; 0 usable forward units.
- Needed effect: the TDA flag's coverage gap must exceed the BEST simple flag's by >= 3 percentage points. Related effects here:
  the whole RECENT_ROLE information gain in pts was about 0.01 CRPS; architecture-only variants of it have been 0.
- Cost to build: about one day (module, truncation-identity test, gate script, harness) plus a new dependency; under a minute of
  compute per season for the test.
- Prior: P(gate passes) 0.85; P(TDA beats all three baselines on any stat in both seasons) 0.04; expected D vs best baseline
  0.00, 80% interval [-0.03, +0.01].
- **Recommendation: PARK.** Do not build the TDA score. If Devin wants a "less reliable" note, the honest first question is
  whether the simple flags themselves predict under-coverage (a new rule, not this one). What would change it: a simple-flag
  pass on 2023-24 AND a precheck showing the TDA score is not a re-expression of it (Spearman < 0.9 with B1, B2, B3).

## 7. Playbook check (docs/MODEL_PLAYBOOK.md)
Class: closest is **Quantile and distribution layers** (a reliability statement about the interval), with a novelty-detector
front end that has no playbook section of its own.
1. **Information or capacity?** Capacity/representation only: the same box scores production reads. No pre-tip information.
2. **Enough data for this class?** For the flag test, yes on 2023-24 if the eligibility share is >= 18% (unknown). For the
   topology itself, no: H1 on 10 to 40 points is mostly noise; H0 is single linkage.
3. **Test validity.** Coverage is judged at a fixed flag rate (otherwise a detector that fires on everything "wins"); randomised
   integer PIT with fixed row order (count support); game-clustered CI; truncation identity (the score at D must not change when
   games on or after D are deleted); label-free collinearity precheck; slices fixed in advance. Invalid if eligibility depends on
   outcomes (it does not: played-games count is pre-tip) or if the comparator lacks information the arm has (all four read the
   same box rows).
4. **Lineage.** Volatility-bucket routing (family C), archetype-cluster routing (family D, rejected), RECENT_ROLE (passed, as
   information), era probe (novelty fires on everything). Carried forward: fixed slices, fixed flag rate, no routing on the flag.
5. **Scores against.** Interval coverage at q10/q90 of the stored production grid, not CRPS and not a line or price.
