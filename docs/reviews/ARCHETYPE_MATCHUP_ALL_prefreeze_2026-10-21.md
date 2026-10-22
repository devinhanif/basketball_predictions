# ARCHETYPE_MATCHUP_ALL (T507): pre-freeze adversary review, 2026-10-21

Subject: `docs/prereg/ARCHETYPE_MATCHUP_ALL.md` (DRAFT, merged on `main` at b018ae2), `research/eval/archetype_matchup_all_{rule,gate,eval}.py`,
the reused `research/eval/archetype_matchup_{rule,features,gate}.py` and `matchup_availability_features.py`, the gate artifact
`reports/prereg_archetype_matchup_all/gate.json`, and `tests/research/eval/test_archetype_matchup_all.py`.
Context: this is Devin's override (DECISIONS 2026-10-21 20:10) of the T506 close. Claude recommended against it. Nothing in this review
reads an outcome. Every read is `season <= 2024`, DuckDB is opened `read_only=True`, and season 2025 is never queried, not even as a count.

Pre-freeze verdict word: **NOT_READY**

Verdict: BLOCKED

**Top finding.** A pass can come from one stat alone. CM is the mean of four per-stat deltas, each divided by that stat's floor. No pass
criterion limits any one stat's share of the composite (`archetype_matchup_all_rule.py:97-120`). The shares are reported but cannot fail
anything (doc line 28). I ran a Gaussian simulation with the measured per-stat noise. A true effect in fg3m alone of -0.005 CRPS (5 fg3m floors,
zero in the other three stats) passes criteria (b)+(c)+(d) **88%** of the time. It would be reported as a four-stat composite win.
fg3m is the stat where small systematic changes to the grid are worth many floor units: integer support alone moved fg3m by -0.0230, which
is 23 floors (T118). Adding a "max stat share <= 0.5" criterion stops this (0% pass) and costs under 0.1 percentage point of power when the
effect is spread evenly.

**Single most likely route to a false pass.** The arm M refit makes a small systematic change to the fg3m grid. The cause is a recalibration
interacting with the integer grid (`ceil(q - 0.5)`) through `lambda` and the `off_arch` / `e_usual` role axis, not matchup information. That
change is worth about 4 fg3m floors and nothing in the other stats. The shuffled control is one seed, compared on point estimates only, and
its permuted `delta_rate` is pure noise that can take tree splits away from the role features. So the shuffle can land just under 50% of the
gain, and the composite passes with fg3m at a 100% share. M1 plus S1 close this route.

## Findings (severity, file:line, fix)

### MUST (fix before the freeze commit)

**M1. One stat can carry the composite claim.** `research/eval/archetype_matchup_all_rule.py:97-120` (`primary_verdict`) and doc lines 27-28.
The +1.0 no-hurt guard limits how much a stat can lose. It does nothing about one stat providing all of the gain: fg3m -4 floors with the other
three stats at 0 gives CM = -1.0. The share table cannot fail anything.
Simulated pass rates, using per-stat sd = binding half-width / 1.96 (pts 1.088, reb 0.665, ast 0.652, fg3m 0.566 floor units, mean of
2023 and 2024), stats independent, 200k draws, seed 0:

| true per-stat effect (floor units) | current rule | + max share <= 0.5 | HURT_MAX 0.5 instead | at least 3 of 4 stat points < 0 |
|---|---|---|---|---|
| uniform -1.25 each | 0.898 | 0.897 | 0.898 | 0.898 |
| uniform -1.5 each | 0.995 | 0.994 | 0.994 | 0.995 |
| fg3m only -5 | **0.884** | **0.000** | 0.695 | 0.488 |
| fg3m -5, pts +1 (pts hurt by a full floor) | 0.376 | 0.000 | 0.151 | 0.141 |
| fg3m -4.5, ast -1 | 0.947 | 0.000 | 0.756 | 0.731 |
| three stats -1.7, pts 0 | 0.904 | 0.892 | 0.792 | 0.904 |
| ast and fg3m -3 each | 0.961 | 0.311 | 0.760 | 0.732 |

Fix: add criterion (g) `max_s stat_shares(parts_2024)[s] <= MAX_STAT_SHARE = 0.5` (fails when CM >= 0). Put it in the rule text item 5,
add a constant in `archetype_matchup_all_rule.py`, add a line in `primary_verdict`, and add a test case (fg3m-only parts fail). Keep
HURT_MAX at 1.0. With (g) in place, lowering it adds little and costs power in the three-stat case (0.90 to 0.79).

**M2. The off_arch category labels change meaning between 2023 and 2024, and the 2024 training pool holds 2023 rows.** The cause is
`matchup_availability_features.py` `assign_archetypes` (labels ordered by the cluster mean of `pts36`) and `build_archetypes`, which
re-clusters every season. The doc says (ARCHETYPE_MATCHUP.md line 22, inherited here) that arm M trains "on rows of strictly earlier months
only (2023 rows are in the pool for 2024)", with `off_arch` as a categorical feature. I measured the labels label-free for the 336 players
clustered in both seasons:
- offense labels agree for only 0.622 of players. Labels 4 and 5 are swapped: 36 of 42 players labelled 4 in 2023 are labelled 5 in 2024,
  and 33 of 36 labelled 5 become 4;
- defense labels agree for 0.595, with label 1 to 2 and label 2 to 1 drifts.
Early in 2024 the training pool is almost all 2023 rows. A tree split learned on 2023 label 4 is then applied to 2024 label 4, which is a
different archetype. This is not a false-pass route; it pushes toward a null. But it makes the test invalid, and an invalid test is not a null
(playbook). The doc also declares this the last matchup variant, so a null caused by a labelling artifact would close the idea for good.
Defender labels enter only through season-specific tables (`e_usual`), and the unknown index (6) is stable across seasons, so only the raw
`off_arch` category is affected.
Fix (safe default): align each season's labels to the previous season's before use. Use Hungarian matching on the crosstab of players
present in both seasons (or on the centroids). Add a test that the aligned 2023/2024 offense labels agree for at least 0.8 of players.
Alternative: drop raw `off_arch` from the layer, since `e_usual` already carries 96% of it (see S2). That changes the declared T506 feature
set, so it is Devin's call.

**M3. Arm M, the shuffled refit and the knockouts are not built, and the freeze pins only the doc text.** `archetype_matchup_all_eval.py:29-60`
refuses and says "build it behind the frozen rule". `require_frozen` hashes the doc only, not `rule.py`, `features.py` or the gate and eval
code (the features module is shared with T505 and T506). No LightGBM constant exists in code (grep for num_leaves, learning_rate and lightgbm
in the five modules: none). Choices still open, all of which would be made after the freeze by a hand that can read outcomes:
- treatment of the first month: no earlier month exists for October 2023. Is lambda set to 1, the row scored as A0, or the row excluded?
- what "A0's mean" is: the mean of the 199-point integer grid, the stored `c`, or the continuous mean;
- whether `off_arch` is passed as LightGBM `categorical_feature` or as an integer;
- `boost_from_average`, deterministic and threading flags;
- whether the 2023 pool is 2023 earlier months only.
Fix: before freezing, implement arm M, the shuffled refit (all four stats) and the knockouts. Add a synthetic test: zero features give lambda
== 1 and a CRPS delta of exactly 0, and a planted feature-driven shift is recovered. Then commit a harness pin
(`harness(ARCHETYPE_MATCHUP_ALL): <pin>`, the `preseason_role_prior.require_harness_pinned` pattern at
`research/archive/preseason_role_prior/preseason_role_prior.py:162-197`) and make `run` require both commits.

**M4. The doc's kill list and the coded verdict disagree.** Doc line 65 (Kill criteria) says the claim is voided by (i) planted-future identity
failing and (ii) "a flagged missingness column that predicts realised minutes AND carries the gain in its knockout". Neither is in
`primary_verdict` (`rule.py:97-120`). Doc line 27 says the code is `primary_verdict`, and doc line 29(e) calls the knockouts "descriptive".
"Predicts realised minutes" and "carries the gain" have no numbers. Also, `primary_verdict` does not say which season its
`stat_points_floor_units`, `top5_share` and `shuffled_cm_point` come from.
Fix: either encode (ii) with numbers in `primary_verdict` (for example: missingness gap >= 0.30 against realised minutes < 5 vs >= 28, AND
the knockout of that column's block keeps < 50% of the 2024 gain), or delete it from line 65. Make 7(e) and line 65 agree. Add "all on 2024"
to item 5 and to the docstring.

### SHOULD

**S1. The shuffled control is one seed, compared on point estimates, and it is a weak perturbation.** Doc line 27(f), `rule.py:114`.
I measured how much the defender-label permutation (seed 0) changes each feature (2023, n 25,815, label-free). Correlation between real and
shuffled values: `retained` 1.000, `e_usual` 0.909, `def_unknown_share` 0.892, `shift_l1` 0.793, `delta_rate` -0.012; `off_arch` is not
shuffled at all. So (f) amounts to "the gain is carried by `delta_rate` and the opponent-specific part of the mix". That is a good attribution
test, but say it in the text. With CM <= -1 and shuffle < 0.5 x gain, the matchup-specific part (M minus shuffle) can be as small as 0.5
floor units, and it never gets a CI.
Fix: use the mean over 5 shuffle seeds (0-4) in (f), and report the clustered paired CI of (M minus shuffle-mean) as a descriptive number.
The playbook asks for shuffle guards sized from a null; a 50% point threshold is not sized.

**S2. `off_arch` and `e_usual` encode the scorer's identity, so the declared "off_arch dropped" knockout does not remove it.** The
between-category share of variance of `e_usual` on `off_arch` is **0.964** (on player-season 0.964). For `def_unknown_share` it is 0.177.
`corr(e_usual, min10)` = 0.517.
Leak status: **not a leak.** "Unknown" depends on the prior season's minutes (unknown share by `n_prior` quartile 0.674 / 0.046 / 0.003 /
0.000) and on static data. It is knowable before tip, and A0 already holds `n_prior`. This is not the `opp_adjusted_ridge` pattern, whose
NULL was a function of tonight's minutes. An explicit category is fine as a leak matter.
It is still a role axis, and the residual layer can learn role from it ("does the arm only re-express the base?", playbook).
Fix (concrete pre-freeze guard): replace knockout 7(e) "off_arch dropped" with a mandatory, reported knockout of the **identity block
{off_arch, e_usual}**. Also add a descriptive identity-only arm: A0 plus `off_arch` plus `e_lg[a] = sum_b A[a,b] R[a,b] - lg`, refit
identically. If the identity-only arm keeps >= 50% of M's 2024 gain, the claim is voided (or at least labelled "role, not matchup"). Write the
threshold before the freeze.

**S3. The planted-future check perturbs values only.** `archetype_matchup_gate.py:174-211` scales `mm`, `poss` and `pts` after the cut. It does
not insert future rows: a new defender, a team change, or an OUT row whose `as_of` is after tip. My reading of `roster_candidates`, `team_at`
and `decayed_asof` (all strictly before the day) finds no leak, but the test does not prove it.
Fix: add planted-row insertion and a planted post-tip OUT row (the features must be identical before the cut). `archetype_matchup_all_gate.py:314`,
`chk("season 2025 loaded", False, "never", True)`, always passes. Replace it with an assertion on `max(mu.season)`, `max(games.season)` and the
model_rows seasons read.

**S4. The select step is harder than the report step.** `rule.py:109`. The 2023 fit has fewer earlier months than 2024, and October has none,
so (a) is a stricter hurdle than (b). The playbook notes a 2023 select season trains on about one season. This is conservative (false-null
risk), not a false-pass risk. State it in the doc so a 2023-only stop is read as weak evidence.

### NIT

- N1. Times: doc line 3 says "20:30 CDT" and DECISIONS line 1363 says "20:10", but the commits are 07f43de 19:41, 96159e5 19:47 and
  f8f2d53 19:52 (-0500 = CDT). The text claims times after the commits that contain it. The commit order is right (decisions, then draft,
  then gate); correct the times.
- N2. `archetype_matchup_all_gate.py:292`: `k if k != "player_seasons" else "player_seasons"` is a no-op.
- N3. CIs come from `nba.props.metrics._clustered_boot_means` via `role_prior_salary_eval.boot_ci`, not `nba/truth/bootstrap.py` (which does
  not exist yet). Project-wide, not specific to this rule.
- N4. `players_static.position` is a current snapshot and is used to cluster prior-season stats, so it is not as-of. Low impact.

## The six checks and the inference checks

| check | command / evidence | n | result | pass/fail |
|---|---|---|---|---|
| 1 shuffled labels | not applicable before a fit; design reviewed (S1) | n/a | the shuffle keeps most feature content; one seed, point estimates only | design: SHOULD fix |
| 2 planted future | gate.json (T506 code): identical True | 1,597 / 1,589 query rows; 114,686 / 116,273 events | values only, no row insertion (S3) | pass, incomplete |
| 2b lag +1 day | code reading: every as-of uses `searchsorted(..., side="left")` (strictly before); OUT set at T-60, `tip_source="real"` | n/a | nothing sits at tip except the pre-tip OUT set | pass (by reading) |
| 3 missingness | gate.json missingness + attack_identity.py | 25,815 (2023) | only `off_arch` flagged (gap 0.641 / 0.609); `e_usual` is 96.4% explained by `off_arch`; unknown tracks prior-season experience, not tonight | not a leak; role proxy (S2) |
| 4 knockout | design reviewed | n/a | the "off_arch dropped" knockout is not a knockout (`e_usual` re-encodes it) | SHOULD fix (S2) |
| 5 replication | not applicable before a fit | n/a | the select/report swap is built in | n/a |
| 6 integer support | A0 from the stored grid (`quantile_grid`), arm re-served with `ceil(q - 0.5)`; A0 reproduction ok in `reports/prereg_preseason_role_prior/check.json` and `reports/prereg_role_prior_salary/gate.json` for all 8 season-stat files (same sha256 pins as this gate) | 27,619 / 27,583 stored (all game types) | same support for every arm | pass |
| row set | gate.json `row_sets_four_stats` | 25,815 / 25,649; 551 player-seasons; 0 dropped | 002 only; four-stat intersection is identical | pass |
| forking paths | `git log` over the three docs and the harness; `git diff 96159e5 b018ae2 -- research/` (empty); `git diff 96159e5 f8f2d53` (doc: status, Amendment 1, results only) | n/a | no T507 choice is outcome-informed: T505 and T506 stopped at label-free gates, and the stats, floors, bars and guards were fixed before T507's gate (stated as "knew per-stat power"). The family actually run on outcomes is 0 tests. | pass |
| holdout | pytest below; module source scan (`test_no_2025_and_no_label_columns_in_modules`) | n/a | no 2025 query; `read_flagged` is in the holdout-filter list | pass |
| rule integrity | eval.py:29-47 | n/a | the freeze pins the doc but not the code (M3) | MUST fix |
| power | gate.json | 25.8k rows, 551 player-seasons | CM detectable half-width 0.400 / 0.390 floor units, MDE80 0.572 / 0.557; pass line 2.5 half-widths from 0 | powered |

Corrected family: one test (CM, 2024). No outcome has been seen by T505, T506 or T507, so there is nothing to BH-correct. If M1's (g) and
S2's identity guard are added, they are pass conditions, not extra tests.
Power note: the composite can see -1.0 (MDE80 about 0.57 floor units). pts alone cannot (1.08-1.10 floor units half-width). A null is
"tested at adequate power" only after M2 is fixed.

## Playbook check (class: GBM context-residual layer with a categorical table input, plus a quantile rescale; four stats)

1. Information or capacity: information in principle (who is OUT on the opposing side, before tip). Measured: two of the six features
   (`off_arch`, `e_usual`) are scorer identity, not opponent. Only `delta_rate`, `shift_l1`, `retained` and the residual of `e_usual` carry
   the opponent. Capacity is small.
2. Enough data for the class: rows are ample (about 25.7k per stat-season). The binding limit is 551 player-seasons, which the composite
   handles. The 2023 select fit trains on less than one season (S4).
3. Test validity: integer support, real tips, row set and power: pass. Invalid as drafted: off_arch label drift across the pooled seasons
   (M2), and the arm is unbuilt and unpinned (M3).
4. Lineage and inherited guards: T505/T506 code (`opp_adjusted_ridge` NULL-pattern lesson; audited, not that pattern), T500 three-way
   clustering, top-5, positive control. Not inherited: the playbook's "shuffle guards sized from a null" (S1).
5. Scored against: A0 (stored production OOF, reproduced exactly) on whole-distribution integer CRPS; no line value claimed.

## Commands run (all exit codes shown; none piped through tail or head)

- `.venv-1/bin/python -m pytest -q tests/research/eval/test_archetype_matchup_all.py tests/eval/test_holdout_source_filter.py`: **47 passed, exit 0**
  (`.venv` has no pytest; `.venv-1` does).
- `NBA_DATA_ROOT=... PYTHONPATH=... .venv/bin/python -W ignore <scratch>/attack_identity.py 2023`: exit 0, wall 2.2 s (read-only; 2023;
  label-free). Output: the R2, shuffle-correlation and unknown-by-n_prior numbers above.
- `... .venv/bin/python -W ignore <scratch>/arch_align.py`: exit 0 (archetype label crosstab, 2023 vs 2024, 336 players).
- `.venv/bin/python -W ignore <scratch>/guard_sim.py`: exit 0 (M1 table; Gaussian, seed 0, 200k draws).
- `git log` / `git diff` as in the table.
The gate was **not re-run**: the harness is unchanged since 96159e5, and I re-derived what mattered label-free.
Scratch: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a8c0c6c7-1aa4-4298-8df3-e647137be187/scratchpad/`.

## Not verified

- Arm M's behaviour: it does not exist yet.
- `flagged_from_availability`'s T-60 cut: production code, tested elsewhere; not re-attacked here.
- Whether the stored A0 OOF equals the live production model: the project convention is accepted.
- Correlation between stats in M1's simulation (assumed independent). pts and fg3m are positively correlated, which widens the composite a
  little; it does not change the one-stat-carry conclusion.

## Recommended changes (for the main session; none applied by me)

1. M1: add the (g) share criterion. 2. M2: align archetype labels across seasons (or Devin chooses to drop raw `off_arch`).
3. M3: build arm M, the shuffle and the knockouts; synthetic tests; harness pin; `run` requires both commits. 4. M4: reconcile the kill list
with `primary_verdict`; seasons explicit. 5. S1-S4 as written. After these, re-run the label-free gate only if a gated quantity changed (M2
changes features, so re-run planted-future and missingness), then freeze.

## Drafted rows (not appended)

- TEST_LEDGER (under T507): `| T507-review | 2026-10-21 | ARCHETYPE_MATCHUP_ALL pre-freeze adversary review (docs/reviews/ARCHETYPE_MATCHUP_ALL_prefreeze_2026-10-21.md) | label-free | NOT_READY: one-stat carry (fg3m-only -5 floors passes 88% simulated), off_arch labels swap across seasons (offense agreement 0.622), arm unbuilt and unpinned, kill list vs code mismatch | n/a | n/a | n/a | n/a | BLOCKED pre-freeze | not holdout |`
- DECISIONS: none drafted. Choosing between aligning and dropping `off_arch` (M2) is Devin's call; safe default: align.

---

## Second review (2026-10-21, after Amendment 2 and 2b; main at a4f3a3f / f1c1bc7)

Subject: `docs/prereg/ARCHETYPE_MATCHUP_ALL.md` Amendment 2 (lines 131-211), `research/eval/archetype_matchup_all_{rule,features,arm,gate,eval}.py`,
`tests/research/eval/test_archetype_matchup_all{,_amendment2}.py`, `reports/prereg_archetype_matchup_all/gate_run2.json`, `gate_run3.json`.
Everything below is label-free: seasons 2022-2023 player stats (the clustering inputs of 2023 and 2024), DuckDB `read_only=True`, no outcome
column read, season 2025 never queried.

Pre-freeze verdict word: **NOT_READY** (one new MUST, cheap; every first-review MUST and SHOULD is closed)

Verdict: BLOCKED

**Top finding (new M5).** The harness pin does not cover every input that can change a number, so `run()` would not refuse a changed
input. Missing: (1) `research/coldstart/archetypes.py`, whose `fit_archetypes` (KMeans, line 194) produces every `off_arch` label and
therefore the alignment and all six features; (2) the eight stored A0 parquet files, whose sha256 the gate records
(`archetype_matchup_all_gate.py:366-368`) but `archetype_matchup_all_eval.run` (lines 250-260) never checks; (3) the production OUT-set path
(`nba/props/context_residual.flagged_from_availability`, `nba/features/injury_report.ReportTriggerConfig`). It is left out on purpose, but it
has already changed in a way that moves numbers: commit 4ea569a on 2026-10-12 changed the tip default. (4) The sklearn and lightgbm versions.
The test `test_archetype_matchup_all_amendment2.py:530` checks only the five T507 modules, not what they import.

### Closure of the first review's findings

| id | closed? | code (file:line) | doc (line) | evidence |
|---|---|---|---|---|
| M1 one-stat carry | **closed** | `archetype_matchup_all_rule.py:53` (`MAX_STAT_SHARE` 0.5), `:202-203` (criterion `max_stat_share`, fails when gain <= 0) | 134 (g), 137-140 | tests `test_m1_*` (amendment2 tests lines 64-92), including the fg3m-only case failing only (g), and the boundary. A hurt stat gets a negative share, which pushes the others above 1 (fg3m -5 with pts +1 fails). |
| M2 label drift | **closed** (default: align) | `archetype_matchup_all_features.py:72-129` (Hungarian on scaled centroids; both sides built from `canonical_frame`) | 142-147, 148 (M2b) | Run 3: offense 0.4345 before, 0.8274 after, n 336, bar 0.80. The defender relabelling cannot change a feature (amendment2 test line 263). |
| M2b (theirs) non-reproducible labels | **closed** | `archetype_matchup_all_features.py:32-37`, `:106`, `:111-113` | 148 | test line 178. My check: the canonical order (player_id ascending) and descending order both give 0.8274. |
| M3 arm unbuilt, text-only pin | **closed for the arm; the pin is incomplete (M5)** | `archetype_matchup_all_arm.py:37-62` (every LightGBM constant), `:65-67` (A0 mean from the 199-point grid), `:74-80` (lambda clip, MEAN_FLOOR), `:107-132` (walk-forward), `rule.py:235-250` (HARNESS_FILES), `:306-335` (`require_harness_pinned`), `eval.py:69-75`, `:251` (`run` calls `require_ready` first) | 155-159 | tests at lines 358-529 and 570-624. |
| M4 kill list vs code | **closed** | `rule.py:153-170` (`minutes_leak` with GAP 0.30, EXCESS 0.10, KEEP_MIN 0.5), `:173-215` (ten criteria; `report_*` names; the docstring states the seasons), `eval.py:129-149` | 134, 161-164; line 65 carries an [Amended] pointer | test line 115 |
| S1 one-seed shuffle | **closed** | `rule.py:50,54-55`, `:190-192` (asserts five points), `:205`; `eval.py:92-93,120-122` (descriptive CI of M minus the shuffled mean) | 166-169 | test line 93 |
| S2 identity axis | **closed** (default: void at >= 0.5) | `rule.py:81-90` (knockout `identity` = {off_arch, e_usual}), `:206-207` (h); `features.py:132-136` (`e_lg`) | 171-174 | test line 102. The "needs the role axis" qualifier is not emitted (m3). |
| S3 planted values only; always-true 2025 line | **closed** | `gate.py:208-224` (inserted future rows), `:227-…` (post-tip OUT through production), `:279` (`planted_future_full`), `:338-352` (`season_assertions`) | 176-179 | tests at lines 666, 731 and 776. Run 3: 3,000 inserted rows; 308 / 3 games planted; positive controls fire. The post-tip plant is +27 h, a blunt probe (S5). |
| S4 select harder than report | **closed in the doc** | the run's stop note (`eval.py:271-276`) does not carry the caveat (m2) | 181 | n/a |
| N1-N4 | closed / stated | N2 removed (grep exit 1) | 3, 183 | n/a |

### New criteria (g)-(j): coherent with each other and with `primary_verdict`

- The doc's ten criteria (line 134) map one to one onto the ten keys of `rule.py:197-210`, and they are all measured on the 2024 report season
  except (a). There is no partial pass (`:213`).
- A pass driven by global recalibration is blocked twice. `boost_from_average` puts the training-mean shift inside M. The shuffled refits
  keep it, because they use the same target and lose only the defender labels, so (f) fails. The identity-only arm keeps it too, so (h)
  fails. The `all_features` knockout reports it.
- Role content in `retained` and `shift_l1` (real-to-shuffled correlation 1.00 and 0.79) survives the shuffle, so a gain carried by role
  through those features fails (f). What can pass is signal carried by `delta_rate` and the opponent-specific part of the mix. That is the
  hypothesis.
- (j) does not void the known `off_arch` role pattern: the gap on realised minutes is close to the pre-tip `min10` gap, so the excess is
  below 0.10. It would void a NULL pattern that follows tonight's minutes. That matches the S2 qualifier text (line 174).
- Minor gap: the S2 qualifier ("the gain needs the role axis") is decided in prose but not computed (m3).

### Walk-forward: no future rows (by reading, plus tests)

- `arm.py:122`: `tr = month < m`, over the whole pool, using lexical `YYYY-MM`. `:123`: `te = is_score & month == m`. Only `resid[tr]`
  enters the fit (`:126`). The test rows contribute their A0 mean (pre-game) and their features only.
- October 2023: `tr` is empty, which is below MIN_TRAIN_ROWS, so lambda is 1 and the row is scored as A0 and not dropped (`:124-125`).
- The 2024 pool is 2023 plus the earlier months of 2024 (`eval.py:240` with `make_pool`). 2023 rows carry 2023-season features built on
  2022 tables.
- The A0 mean comes from the continuous grid (`arm.py:65-67`). The arm and A0 are both scored with `crps_int_q`, which applies
  `ceil(q - 0.5)` internally (`preseason_role_prior.py:645-651`), so they are on the same support.
- Tests at lines 400 and 419 scramble the outcomes of the current and later months and see no change.

### Run 2 to Run 3: not a forking path

- The bar was not changed: `ALIGN_MIN_AGREE` = 0.80 at ebc94f1, b9b1b17 and ef563ad.
- `git diff ebc94f1 b9b1b17 -- research/` contains only `canonical_frame` and its two call sites.
- The fix and the Run 3 priors were committed at 20:31:35 (b9b1b17), before the Run 3 artifact at 20:38:34 (ef563ad). `b9b1b17..ef563ad`
  touches no code.
- The membership-crosstab matching, which would have passed Run 2, was declined.
- The decisive check is whether the order was picked because it passes. I re-clustered both seasons under 300 random row orders (label-free,
  `order_robust.py`, exit 0, about 5 s per 30 orders). Offense agreement after alignment had mean 0.8077, sd 0.062, range 0.536-0.863.
  **92.3% of orders pass 0.80.** player_id ascending and player_id descending both give 0.8274.
- Run 2's 0.5565 was a tail draw (about 7.7%), not the typical result. The canonical order is the obvious choice and was not selected from
  alternatives.
- Defense agreement varies more: mean 0.723, range 0.473-0.854. It is descriptive only, and the features do not depend on the defender
  label numbers.
- Residual: the k-means partition itself, not only its numbering, depends on row order. The features are therefore one deterministic draw
  among several local optima. It was fixed before any outcome, so it is legitimate, but say so (m5).

### Holdout

- `pytest --no-cov`: 83 passed, exit 0. This includes `tests/eval/test_holdout_source_filter.py`, `test_archetype_matchup_all.py` and
  `test_archetype_matchup_all_amendment2.py`.
- grep of the T507 modules: the only SQL is `gate.py:233-236`. It reads `player_availability` restricted to `_allowed_games` from
  `read_games` (season <= 2024; asserted at `eval.py:259`).
- `read_rows` and `read_player_season_stats` go through `check_season` / `season <= MAX_SEASON`.
- Run 3's `season_assertions`: 2024 / 2024 / 2025-06-22. That date is the 2024-25 season's own rows.
- **Pass.**

### New findings

**M5 (MUST, before the freeze commit, because the freeze commit must hold the final code). The pin does not cover every input that changes
a number.** `archetype_matchup_all_rule.py:235-250`, `archetype_matchup_all_eval.py:250-260`.
Fix:
(a) Add `"research/coldstart/archetypes.py"` to `HARNESS_FILES`.
(b) Above the results marker, write: the eight A0 parquet sha256 values from `gate_run3.json` `pins`; a sha256 of each season's aligned
`(player_id, off_arch, def_arch)` vectors; a sha256 of the sorted OUT set from `read_flagged` for seasons 2023-2024; and the sklearn,
lightgbm, numpy and pandas versions. `run()` asserts each one before `read_outcomes` is first called and refuses on any mismatch. The label
digest covers both sklearn drift and `archetypes.py`. The OUT-set digest covers the production files without pinning live code.
(c) Replace the coverage test at `test_archetype_matchup_all_amendment2.py:530` with a transitive one. Collect every repo-local module
imported by `research.eval.archetype_matchup_all_eval` (for example with `modulefinder` or by walking `sys.modules` after import). Assert
each module is either in `HARNESS_FILES` or in an explicit digest-covered allow-list (`nba.props.context_residual`,
`nba.features.injury_report`, plus the unused-at-runtime imports of `archetypes.py`, `research.features.*`, which should be named).
The digests need about 2 minutes of label-free compute and no new gate run.

**S5 (SHOULD). The planted post-tip OUT row is only a +27 h probe.** `gate.py:227-…`. It proves that a far-future row is ignored, but not
that the cut sits at T-60 against the real tip. The 19:00-proxy incident was about rows stamped minutes after tip. Fix: also plant rows at
real tip + 1 min (must not count) and at real tip - 61 min (must count), on the same sampled games, in `planted_post_tip_out` and in its
test.

**m1 (minor).** `gate.py:365`: `status` is hard-coded "DRAFT gate Run 2", so `gate_run3.json` says Run 2. Pass the run number in.
**m2 (minor).** `eval.py:271-276`: the 2023 stop note should carry A2-S4's "weak evidence of no composite gain, not strong evidence".
**m3 (minor).** `eval.py:129-149`: emit `qualifier: "gain needs the role axis"` when `knockouts.identity.keep_share < 0.5` and the verdict
passes, as line 174 promises.
**m4 (minor, outside T507).** `research/coldstart/archetypes.py:184`, "Deterministic given seed", is false: KMeans depends on row order. The
owner of that file should correct the docstring.
**m5 (minor, doc).** Record the order-robustness numbers above (92.3% of 300 orders pass; Run 3 margin 0.027) as descriptive under Run 3.

### Single most likely route to a false pass now

Not a leak in the six features as built. The route is an **input that changes between the gate and the run without `run()` refusing
(M5)**, for example a rebuilt A0 parquet, a changed OUT-set function, or a different sklearn KMeans. The scored arm would then not be the
gated arm, and the evidence would still read "pinned". Among outcome routes, the residual one is a **two-stat carry on the small floors**:
(g) at 0.5 still lets ast and fg3m carry CM together (31% pass at -3 floors each, first-review simulation), with an opponent-specific signal
that is real but tiny in absolute CRPS. That would be a true result read too broadly (WOUNDED), not a fake one.

### Commands (exit codes shown, nothing piped through tail or head)

- `.venv-1/bin/python -m pytest -q --no-cov -p no:cacheprovider tests/research/eval/test_archetype_matchup_all.py tests/research/eval/test_archetype_matchup_all_amendment2.py tests/eval/test_holdout_source_filter.py`:
  **83 passed, exit 0**.
- `NBA_DATA_ROOT=... PYTHONPATH=... .venv/bin/python -W ignore <scratch>/order_robust.py 30`, then `... 300`: exit 0 both times (read-only;
  2022/2023 stats; seeds 100-399).
- `git diff ebc94f1 b9b1b17 -- research/`, `git diff --stat b9b1b17 ef563ad`, `git log --date=iso` over the doc, code and reports.
- grep for SQL and 2025 in the T507 modules, and for the transitive imports of the harness files.

### Not verified

- The full eval run, including whether LightGBM's categorical handling is bit-stable across versions. No fit on outcomes exists, by design.
- That Run 3 was not computed before b9b1b17. The commit order shows it was not, and the order-robustness result makes the question
  nearly moot.
- `flagged_from_availability`'s T-60 against real tips. It is production code, and the +27 h probe does not test it (S5).

### Playbook check (GBM context-residual layer plus quantile rescale, four stats)

1. Information or capacity: information (the opposing OUT set through `delta_rate` and the mix). The role axis is now priced by (h) and the
   identity knockout.
2. Enough data for the class: about 25.7k rows per season and 551 player-seasons. 2023 trains on its own earlier months only (S4,
   conservative).
3. Test validity: the labels are now reproducible and aligned (0.8274), with the same support and same rows, and the arm is fully specified.
   Invalid until M5: the pin does not bind every input.
4. Lineage and inherited guards: the T505/T506 code, the `opp_adjusted_ridge` NULL-pattern guard as (j) with numbers, shuffle guards over
   five seeds (S1), and the identity arm.
5. Scored against: A0, the stored production OOF, on integer CRPS, as a four-stat composite in floor units, with one test and no BH family.

### Recommended changes (for the main session; none applied by me)

1. M5 (a)-(c), then re-take the harness pin values; no gate re-run is needed.
2. S5.
3. m1-m3 and m5 in the same commit.
4. Then freeze. On this evidence I expect the third review to be READY_TO_FREEZE.

### Drafted rows (not appended)

- TEST_LEDGER (under T507): `| T507-review-2 | 2026-10-21 | ARCHETYPE_MATCHUP_ALL second pre-freeze review (docs/reviews/ARCHETYPE_MATCHUP_ALL_prefreeze_2026-10-21.md, Second review) | label-free | NOT_READY: M1-M4 and S1-S4 closed; new M5 (pin misses research/coldstart/archetypes.py, the A0 parquet hashes are not asserted by run, OUT-set and library drift unpinned); alignment robust (92.3% of 300 row orders pass 0.80) | n/a | n/a | n/a | n/a | BLOCKED pre-freeze | not holdout |`
- DECISIONS: none. Decisions (1)-(5) of Amendment 2 remain Devin's, and the safe defaults are in force.

## Third review (2026-10-21, closure, after Amendment 3; main at 7b73182)

Scope: narrow closure of M5, S5 and m1-m5. Subject: `docs/prereg/ARCHETYPE_MATCHUP_ALL.md` lines 211-251 (Amendment 3 and the "Declared input
pins" block), `research/eval/archetype_matchup_all_{rule,features,gate,eval}.py`, and the Amendment 3 tests in
`tests/research/eval/test_archetype_matchup_all_amendment2.py` (lines 796-976). Everything was label-free. DuckDB was opened `read_only=True`
(through `preseason_role_prior.connect`), only `season <= 2024` was queried, and no outcome column was read.

Pre-freeze verdict word: **NOT_READY** (one new MUST, M6, about ten lines of code; M5 as specified, S5 and every minor are closed)

Verdict: BLOCKED

**Top finding (new M6).** `compute_input_digests` (`archetype_matchup_all_eval.py:156-169`) says it covers "everything that can change a
number without changing a pinned file". It leaves out the two DuckDB tables that the six features are built from:
- `player_game_matchups`, read by `read_matchups` in `matchup_availability_features.py:36-44`. That is 692,906 rows for 2022-2024. It feeds
  `prior_tables` and `season_frame` (`archetype_matchup_all_features.py:156-158`), and through them `delta_rate`, the mix, `retained`,
  `shift_l1` and `e_usual`.
- `games`, read by `read_games` in `matchup_availability_features.py:47-51`. That is 3,690 rows. It supplies the walk-forward months, the
  decay days, the week clusters and the opponent mapping.

`player_game_matchups` can change. `nba/ingest/postgame.py:633-643` deletes and reinserts by game key on any reload of the cached parquet,
and then rewrites `def_team_id`. If the table is rebuilt between the gate and the run, the scored arm is not the gated arm and `run()` does
not refuse. That is exactly the M5 failure route. This gap is my omission: the second review's M5(b) list named A0, labels, OUT sets and
libraries but not these two tables. The modeler implemented that list faithfully.

### Closure table

| id | closed? | evidence (file:line, command) |
|---|---|---|
| M5(a) harness files | **closed** | `archetype_matchup_all_rule.py:233-251`: 17 entries, including `research/coldstart/archetypes.py`, `nba/props/distributions.py` and `nba/truth/metrics.py`. I recomputed all 17 file sha256 values and the combined pin with `r.pin_of(r.disk_blobs())`: **efbcb276e59ecdd737f0efc5be99f25b88a325f43699050c947cf093867b141e**, equal to the claim, exit 0. |
| M5(b) declared input pins | **closed for the items named; incomplete (M6)** | I ran `python -m research.eval.archetype_matchup_all_eval digests` read-only (exit 0, 2.2 s) and diffed it against the declared block (doc lines 231-249): `diff` exit 0, so all 17 lines are identical. The A0 lines equal `gate_run3.json` `pins`. `input_mismatches` (`rule.py:378-386`) refuses on a changed value, on a missing declaration and on a declaration that was not recomputed (test line 869). `verify_inputs` runs before any outcome read: in `run` (`eval.py:291-303`) it comes after `require_ready` and the three label-free loaders and before `load_season`. `load_season` is the only caller of `read_outcomes` (grep). This is tested by a stubbed run in which `load_season` and `read_outcomes` raise (test line 904), and by a source-order assertion (test line 934). The declared block sits above the marker, so it is covered by the frozen-text sha that `require_frozen` checks before `verify_inputs` reads the doc. |
| M5(c) transitive coverage test | **closed** | Test lines 798-849 walk the imports with `ast`, lazy imports included. Without the stop list I re-walked the closure: 35 modules. The only modules reachable solely through `DIGEST_COVERED` are `nba.models.{base,common,rung0_baselines}`, `nba.props.{baselines,lineup_features,lower_tail}` and `nba.features.{injury_report,game_tipoff}`. They are imported by `nba.props.context_residual`, and T507 uses only `flagged_from_availability` from it, whose output is the OUT digest. None of the 35 modules uses a relative or dynamic import (scan: none found). The package `__init__` files that are skipped are docstrings only (0, 0, 0, 0, 422 and 112 bytes). |
| EXEMPT_UNUSED reasons | **hold** | `fit_archetypes` and `encode_position_fractions` (`archetypes.py:114-210`) call only `StandardScaler`, `KMeans`, numpy and local code. `matchup_availability_features.py:214` passes `k` explicitly, so `config.DEFAULT_N_CLUSTERS` is unused. `research.features.*`, `carryover` and `shrinkage` are loaded at import and never called. They are also covered twice: any change of theirs that moved a label would change `PIN_LABELS_*`. |
| S5 real-tip edges | **closed** | `archetype_matchup_all_gate.py:282-323`. The plants are timed from production's own `build_game_tipoff` `tip_et`, with +1 min over all sampled games that have a tip and -61 min on games whose latest pre-cut snapshot is earlier than tip - 61. Both feed `planted_future_full.identical`. Doc line 249: 308/307 games, all with a real tip; +1 min unchanged; -61 min counted 293/293 and 291/291. The 15/16 games not expected to count have a snapshot inside (T-61, T-60], which is consistent with reports that land exactly at T-60. |
| m1 | closed | `gate.py:411,421,569`: `--label` writes the run name. `gate_run3.json` stays as committed, and the doc states it is Run 3. |
| m2 | closed | `eval.py:313-316`; test line 934 |
| m3 | closed | `eval.py:145-147`; test line 942 |
| m4 | noted, outside T507 | doc line 221 |
| m5 | closed | doc line 221 records 92.3% of 300 orders and the 0.027 margin as descriptive |
| Rule integrity | pass | The Amendment 3 priors were in 06d06cc (20:50:29), and that commit contains neither the digest values nor the S5 counts (grep count 0). The values came in a8ac840 (20:55:53), which touches only the doc. Nothing is uncommitted under `research/`, `tests/` or the doc relative to 7b73182 (`git diff | wc -l` = 0). No bar, floor or constant changed (`git diff 9ccd509 7b73182 --stat`: six files, all inside T507). |
| Holdout | pass | `pytest` includes `tests/eval/test_holdout_source_filter.py`. Every query I ran asserted `season.max() <= 2024`. |

### New findings

**M6 (MUST, before the freeze commit).** The feature-source tables are not digested. Files: `archetype_matchup_all_eval.py:156-169` (and
its call at `:300-302`) and the declared block in the doc (lines 230-249).
Fix:
(a) Pass `mu` into `compute_input_digests(c, games, flagged, mu)` and add two digests, using the existing `arch.sha_table`:
- `PIN_MATCHUPS: arch.sha_table(mu.sort_values(list(mu.columns)).reset_index(drop=True))`
- `PIN_GAMES: arch.sha_table(games.sort_values("game_id").reset_index(drop=True))`

Sort before hashing: `read_matchups` has no ORDER BY.
(b) Update `main`'s `digests` branch and the stub at test line 924 (`lambda c, g, f, m: ...`).
(c) Add a test that a one-cell change to `mu` or `games` changes its digest.
(d) Re-run `digests` twice and paste the two new lines above the marker. Then re-take the harness pin, because `eval.py` changes.

Cost: my scratch digest of both tables took 0.3 s, and two consecutive runs were identical (`5f2444c3f5d6c40a…` for 692,906 rows and
`efdeb2c24070e319…` for 3,690 rows). No gate re-run is needed.

**m6 (minor).** `run` checks the labels from one `build_aligned_archetypes` call (inside `compute_input_digests`) but builds the features
from a second call (`load_season`, `eval.py:189`). The two agree today: the digests matched twice and the procedure is deterministic. The
cleaner fix is to assert `af.label_digest(off_a, def_a) == declared PIN_LABELS_{S}` inside `load_season`, so the labels checked are the
labels used.

### Single most likely route to a false pass now

An **unpinned DuckDB table that changes between the gate and the run (M6)**: for example, a postgame reload rewrites
`player_game_matchups` rows for 2022-2024, the features move, and `run` still reads "inputs verified". After M6 I see no remaining
number-changing input that is not byte-pinned or digested. The outcome-side residual (a two-stat carry on the small floors) is unchanged
from the second review.

### Commands (exit codes shown, nothing piped through tail or head)

- `.venv/bin/python -c "from research.eval import archetype_matchup_all_rule as r; b=r.disk_blobs(); ... print(r.pin_of(b))"`: 17 files,
  pin efbcb276…141e, exit 0.
- `NBA_DATA_ROOT=… .venv/bin/python -W ignore -m research.eval.archetype_matchup_all_eval digests > <scratch>/digests.txt`: exit 0. Then
  `diff <scratch>/digests.txt <scratch>/declared.txt`: exit 0.
- `.venv-1/bin/python -m pytest -q --no-cov -p no:cacheprovider tests/research/eval/test_archetype_matchup_all.py tests/research/eval/test_archetype_matchup_all_amendment2.py tests/eval/test_holdout_source_filter.py`:
  **91 passed, exit 0**.
- `<scratch>/mu_digest.py` (read-only, season <= 2024, run twice in one process): exit 0, identical digests, 0.3 s.
- Unstopped closure walk plus a relative/dynamic-import scan: 35 modules, none flagged, exit 0.
- `git diff 9ccd509 7b73182 --stat`, `git show 06d06cc:docs/prereg/ARCHETYPE_MATCHUP_ALL.md | grep -c …` (0),
  `git diff 06d06cc a8ac840 --stat`, `git diff 7b73182 -- research/ tests/ <doc> | wc -l` (0).

### Not verified

- That the stored `as_of` and `tip_et` share a timezone convention. The S5 probe times its plants with production's own `tip_et`. If both
  were consistently mis-zoned, the probe would still pass. This belongs to production (`nba/features/injury_report.py:121-138`), not to T507.
- `.venv-1` (tests) and `.venv` (the documented run command) may have different library versions. If they do, `run` refuses, which is the
  safe direction.
- The full eval run. By design, no fit on outcomes exists.

### Playbook check (GBM context-residual layer plus quantile rescale, four stats)

1. Information or capacity: information (the opposing OUT set through `delta_rate` and the mix). Unchanged.
2. Enough data for the class: about 25.7k rows per season. Unchanged.
3. Test validity: the labels are aligned and reproducible, rows and support are the same, the arm is fully specified and the code is
   byte-pinned. The test is still invalid until M6, because the features' source tables do not bind.
4. Lineage and inherited guards: the T505/T506 code, the `opp_adjusted_ridge` NULL-pattern guard (j), five shuffle seeds, the identity
   arm, and now the M5 input digests and the S5 real-tip plants.
5. Scored against: A0, the stored production OOF, on integer CRPS, as a four-stat composite in floor units. One test, no BH family.

### Recommended changes (for the main session; none applied by me)

1. M6 (a)-(d), plus m6 if convenient. Re-run `digests`, re-take the harness pin, and do not re-run the gate.
2. Then freeze. A fourth review should be a diff check of M6 only (`git diff 7b73182 <new>`); I expect READY_TO_FREEZE.

### Drafted rows (not appended)

- TEST_LEDGER (under T507): `| T507-review-3 | 2026-10-21 | ARCHETYPE_MATCHUP_ALL third pre-freeze review (docs/reviews/ARCHETYPE_MATCHUP_ALL_prefreeze_2026-10-21.md, Third review) | label-free | NOT_READY: M5 as specified, S5 and m1-m5 closed (harness pin efbcb276 recomputed; 17 declared digests reproduced exactly; S5 293/293, 291/291); new M6: player_game_matchups and games, the feature-source tables, are not digested | n/a | n/a | n/a | n/a | BLOCKED pre-freeze | not holdout |`
- DECISIONS: none.

## Fourth review (2026-10-21, closure, after Amendment 4; main at 19807b1)

Scope: M6 and m6 from the third review only. I checked them by diff (`git diff 04597b2 19807b1`, and `git diff 7b73182 19807b1 --stat` for
scope), by recomputing the digests, the file hashes and the pin, and by running the tests. Everything was label-free. DuckDB was opened
`read_only=True` (`preseason_role_prior.connect`). Only `season <= 2024` was queried: max season 2024 in both `mu` and `games`. No outcome
column was read.

Pre-freeze verdict word: **READY_TO_FREEZE**

Verdict: APPROVED

**Top finding.** M6 is closed. `PIN_MATCHUPS` and `PIN_GAMES` are computed in `compute_input_digests` (`archetype_matchup_all_eval.py:159-167`)
from the same `mu` and `games` objects that `run` passes on to the features (`:300-311`). They are declared above the marker, and two fresh
`digests` runs reproduce all 19 declared lines byte for byte. A reload of either table between now and the run now makes `run` refuse
before any outcome is read.

### Closure table

| id | closed? | evidence (file:line, command) |
|---|---|---|
| M6(a) two table digests | **closed** | `eval.py:159-167`. `mu` is sorted by all columns and `games` by `game_id`, each with its index reset, then hashed with `arch.sha_table`, exactly as I specified. Determinism of the sort: `games` has 3,690 rows and `game_id` is unique (True). `mu` has 692,906 rows and 0 fully duplicated rows, so the unstable default sort cannot reorder distinct rows. `compute_input_digests` gets the same `mu` and `games` that feed `load_season` (`eval.py:300-311`). `sort_values` returns a copy, so neither table is mutated. |
| M6(b) `digests` branch and test stub | **closed** | `eval.py:352-354` passes `feat.read_matchups(c)`. The stub at test line 925 takes four arguments. `read_matchups` and `read_games` are the T505 functions re-exported (`archetype_matchup_features.py:29-30`), so `digests` and `run` hash the same query. |
| M6(c) one-cell test | **closed (see m7)** | `test_a_one_cell_change_in_either_feature_source_table_changes_its_digest`, test lines 979-1007: changing one cell in either table changes that table's digest and only that one, and row order does not matter. |
| M6(d) declared lines and harness pin | **closed** | I ran `digests` twice: both exit 0, `diff` between them exit 0, and `diff` against the declared block (19 lines) exit 0. `PIN_MATCHUPS 5f2444c3…7d5c` and `PIN_GAMES efdeb2c2…e724` equal my third-review scratch prefixes. `rule.declared(doc)` parses 19 entries, and `input_mismatches(declared, recomputed) == []`. I recomputed all 17 harness file sha256 values. Only `archetype_matchup_all_eval.py` changed (`c183d5b87654…`); the other 16 files are untouched since 7b73182 (diff stat). The combined pin is **63b3d9b0277c47130557aae5008c5707540efafbb1cc8a1c4bc0d4a02a94bc75**, equal to the claim. |
| m6 labels used = labels declared | **closed** | `load_season` (`eval.py:206-214`) computes `af.label_digest(off_a, def_a)` on the labels it then builds `sf` from, and raises SystemExit on a mismatch. The check runs before `read_outcomes`, which is called at `:231`. `run` passes `rule.declared(DOC.read_text())` (`:309`), and `run` is the only caller of `load_season` (grep). It is tested by `test_load_season_refuses_labels_that_differ_from_the_declared_pin` (test line 1010). |
| Rule integrity | pass | The priors were committed in bab8eea (21:02:22): doc only, 6 lines, with grep count 0 for both new digest values and 0 for "Amendment 4 results". The values and the code came in eece82b (21:07:16). Since 7b73182 only three files changed, all inside T507: the doc, `_eval.py` and the amendment-2 test file. No bar, floor, constant or feature changed. Nothing is uncommitted under `research/`, `tests/` or the doc (`git diff 19807b1 ... | wc -l` = 0). |
| Holdout | pass | `tests/eval/test_holdout_source_filter.py` was in the passing run. Both loaders filter on `season <= MAX_SEASON` (2024), and the observed max season is 2024. |
| Lint | pass | `ruff check` exit 0, `ruff format --check` exit 0 and `mypy` exit 0 on `_eval.py` (and ruff on the test file). |

### New findings (none blocking)

**m7 (minor, optional).** The one-cell test (test lines 990-994) re-implements the hashing expression in a local `dig()`. It does not call
`compute_input_digests`; it checks the source only with the substring test `"PIN_MATCHUPS" in src`. So a later edit that, for example, dropped the sort
inside `compute_input_digests` would not fail this test. It would still be caught in practice: any such edit changes `_eval.py`, which breaks
the harness pin, and a wrong expression would stop reproducing the declared value, so `run` refuses. Either way the failure is safe. Fix if
convenient (not needed for the freeze): build the two entries through a small helper `table_digests(mu, games)` used by both
`compute_input_digests` and the test.

**m8 (note).** One Amendment 4 prior ("reviewer prefixes match", P 0.85) predicted a value I had already published in the third review, so it is
not an independent forecast. Score it as informative only in PREDICTIONS_LEDGER.

### Single most likely route to a false pass now

I see no remaining number-changing input that is unpinned or undigested. The residual risk is still the one on the outcome side (a carry by
two stats on the small floors), and that is what the frozen bars are for. Of the "Not verified" items in the third review, the as_of/tip_et
timezone convention still stands. It belongs to production, not to T507.

### Commands (exit codes shown, nothing piped through tail or head)

- `.venv/bin/python -W ignore -m research.eval.archetype_matchup_all_eval digests > <scratch>/d1.txt` and the same to `d2.txt`: exit 0 and
  exit 0. `diff d1 d2`: exit 0. `diff d1 declared` (19 lines extracted from the declared block): exit 0.
- `<scratch>/check4.py`: declared n 19, mismatches `[]`, 17 file hashes, pin 63b3d9b0…bc75, text sha, games 3,690 rows with unique
  game_id, mu 692,906 rows with 0 full duplicates, max season 2024 for both; exit 0.
- `.venv-1/bin/python -m pytest -q --no-cov -p no:cacheprovider tests/research/eval/test_archetype_matchup_all.py tests/research/eval/test_archetype_matchup_all_amendment2.py tests/eval/test_holdout_source_filter.py`:
  **93 passed, exit 0** (91 before, plus the 2 new tests).
- `ruff check` exit 0, `ruff format --check` exit 0, `mypy research/eval/archetype_matchup_all_eval.py` exit 0.
- `git log 04597b2..19807b1`, `git show bab8eea:<doc> | grep -c <values>` (0), `git diff 7b73182 19807b1 --stat`.

### Not verified

- The full eval run. By design, no fit on outcomes exists.
- The sha256 of the text above the marker, as computed after this commit. This review file is not the rule doc, so appending to it does not
  change that sha. At 19807b1 it is `f10ed5b1dd0f855a15bab6135f0ded608062d8debda8a36d8df8f70732b89f9b`, and the freeze commit must carry exactly that
  value unless the doc changes again.

### Playbook check (GBM context-residual layer plus quantile rescale, four stats)

1. Information or capacity: information (the opposing OUT set through `delta_rate` and the mix). Unchanged.
2. Enough data for the class: about 25.7k rows per season. Unchanged.
3. Test validity: valid as specified now. The labels are aligned and pinned, the labels used are asserted equal to the labels declared, the
   feature-source tables are digested, the A0 files, the OUT sets and the libraries are pinned, and the harness code is byte-pinned.
4. Lineage and inherited guards: the T505/T506 code, the `opp_adjusted_ridge` NULL-pattern guard (j), five shuffle seeds, the identity arm,
   the M5 input digests, the S5 real-tip plants, and now the M6 table digests.
5. Scored against: A0, the stored production OOF, on integer CRPS, as a four-stat composite in floor units. One test, no BH family.

### Recommended changes (for the main session; none applied by me)

1. Freeze: commit `prereg(ARCHETYPE_MATCHUP_ALL): f10ed5b1dd0f855a15bab6135f0ded608062d8debda8a36d8df8f70732b89f9b`, then
   `harness(ARCHETYPE_MATCHUP_ALL): 63b3d9b0277c47130557aae5008c5707540efafbb1cc8a1c4bc0d4a02a94bc75` with every harness file byte-identical.
   If m7 is fixed first, the pin changes and must be re-taken.
2. The 2025 touch, if any, is Devin's decision and needs its HOLDOUT_ACCESS_LOG row before it runs.

### Drafted rows (not appended)

- TEST_LEDGER (under T507): `| T507-review-4 | 2026-10-21 | ARCHETYPE_MATCHUP_ALL fourth pre-freeze review (docs/reviews/ARCHETYPE_MATCHUP_ALL_prefreeze_2026-10-21.md, Fourth review) | label-free | READY_TO_FREEZE: M6 and m6 closed (19 declared digests reproduced twice, byte-identical; harness pin 63b3d9b0 recomputed over 17 files; text sha f10ed5b1; 93 tests passed); minor m7 (test re-implements the digest) optional | n/a | n/a | n/a | n/a | APPROVED pre-freeze | not holdout |`
- DECISIONS: none.
