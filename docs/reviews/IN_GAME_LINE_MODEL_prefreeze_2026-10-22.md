# IN_GAME_LINE_MODEL (T508): adversary pre-freeze review, 2026-10-22

Reviewer: adversary, read-only. Written 2026-10-22 00:28 CDT (from `date`). Reviewed: `main` at dd2ba1b (merge of
`agent/modeler-in-game-line-2`): `docs/prereg/IN_GAME_LINE_MODEL.md` (Amendments 1-2), `research/eval/in_game_line_{rule,features,gate,eval}.py`,
`tests/research/eval/test_in_game_line.py`, `reports/prereg_in_game_line/` (gate Runs 1, 2, 4; `dryrun.json`).
Claimed text sha `72c21fd4...` and harness pin `add7798b...`: both recomputed and equal (below). No season >= 2025 queried. No outcome
(pts/reb/ast/fg3m box value) read by me; two attacks read box KEYS of played rows and one read box MINUTES (not a target stat), seasons <= 2024.

**NOT_READY** (pre-freeze vocabulary).

Verdict: BLOCKED

**Top finding.** The pts state is clean: my truncation test, which is stronger than the planted-future check, found zero differing rows for
minutes, starter, fouls, margin, pts, reb and fg3m over 600 games (77,614 player-checkpoint rows). The likeliest false pass is not a leak.
It is a handicapped baseline that the inert shuffle cannot catch. B0 and B1 get one 2-parameter Platt fit pooled over 7 checkpoints x 5 lines.
The dryrun shows what that costs. On e = 42 rows the synthetic outcome is drawn from raw B0 itself, so raw B0 is the truth there, yet M beats
recalibrated B0 by 0.0042 (pts, 2024 Q4 normal, n = 75,156 rows). M cannot beat the truth, so pooled Platt alone costs B0 at least about 0.004
on those rows, which is close to the 0.005 floor. The shuffled-label control (g) cannot fire: in the dryrun it is 0.195 worse than B0. Separately,
`ast` has a real whole-game leak (0.24% of rows), and the dryrun was not outcome-blind.

## Model class and Playbook check
Class: two-stage LightGBM (stage A remaining minutes, stage B monotone binary model with B0's logit as a feature) over a period-aware
linear-pace NegBin baseline. Playbook sections: "Gradient-boosted trees" and "Quantile and distribution layers".
1. **Information or capacity.** Mostly information: the in-game state is true and is unknown at tip. Part of the capacity is spent re-expressing
   B0 per checkpoint and line, and that part is not information (M4).
2. **Enough data for this class.** Yes. 2019-22 gives about 2.7M pts units for training. Smallest required cell: 3,575 player-games / 173 games
   against bars of 250 / 100.
3. **Test validity.** Four defects:
   - an inert shuffle (playbook: "an inert shuffle (only false passes)");
   - a comparator with weaker recalibration than the arm (M4);
   - a planted-future test that cannot see whole-game aggregates (M2);
   - ast state built from whole-game name membership (M1).

   Power is adequate. The binding half-width is 0.00185 / 0.00183, so the MDE at 80% power is about 0.0026, half the floor.
4. **Lineage and guards.** The ARCHETYPE_MATCHUP_ALL guards (pin, input digests, top-5 share, better baseline) are carried forward. Not carried:
   the T507 digest of every DB table a number depends on (`player_game_stats` is unpinned, S1). Not carried either: BEST_PRACTICES 2026-10-12,
   "Shuffle guards must be sized from a permutation null".
5. **Scored against.** Log loss at fixed lines against B0 and the better B1, not a market. The playbook asks for a reliability table at the
   line that matters (S4).

Class checks (GBM):
- Missingness audit beyond the as-of cause: done below, no asymmetry for pts.
- Planted future: present but weak (M2).
- Does the arm only re-express the base? Not tested by the rule (M3, M4).

## Per-check table
Scratch scripts: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a8c0c6c7-1aa4-4298-8df3-e647137be187/scratchpad/`
(`trunc_leak.py`, `missing.py`, `a0cov.py`, `stintbox.py`). Every run below is read-only with `season <= 2024`.

| # | check | command | n | result | pass/fail |
|---|---|---|---|---|---|
| 1 | Truncation leak test: state at e from pbp truncated at T plus stints clipped at T, against the full-game state at e | `NBA_DATA_ROOT=... .venv/bin/python -W ignore <scratch>/trunc_leak.py <season> 150` for 2019, 2022, 2023, 2024; exit 0 x4 | 150 games per season; 19,676 / 19,621 / 19,051 / 19,266 rows | 0 differing rows in every column except `ast`. `ast` differs in 37 / 43 / 58 / 47 rows (185 of 77,614, 0.24%), always full > truncated. 0 rows missing or extra. | **pts PASS; ast FAIL (M1)** |
| 2 | Planted-future coverage | read `features.py:518-564` | n/a | Doubles post-cut events under the same names and rewrites post-cut stint ids. It cannot change any whole-game aggregate, so it missed check 1. | **FAIL as a guard (M2)** |
| 3a | Missingness: completeness `full` by \|margin\| at e = 36 and at end of regulation | `<scratch>/missing.py 2022/2023/2024`; exit 0 x3 | 1,230 games per season | 2023 `full` by margin at e = 36 (0-10 / 10-20 / 20+): 0.961 / 0.953 / 0.941 (n 584 / 425 / 221). By final margin: 0.949 / 0.958 / 0.962, the opposite sign. 2024: 0.980 / 0.977 / 0.992. 2022: 0.986 / 0.992 / 1.000. The largest gap (0.02, n = 221) is about 1.2 SE and its sign flips with the margin definition. The run does NOT filter on `full`, so no game drops; incompleteness only undercounts `fouls` in about 4.5% of 2023 games. | PASS |
| 3b | Missingness: on-court player-games with no played box row (inclusion would be box membership) | same script, keys only | 24,405 / 24,351 / 24,434 | 0 / 0 / 1 without a box row | PASS |
| 3c | Missingness: A0 inner join (scored rows need A0) by stint-derived regulation minutes, among eval-eligible rows (n_prior >= 5 from played-row keys) | `<scratch>/a0cov.py 2023/2024`; exit 0 x2 | 24,070 / 24,143 | A0 coverage 0.9981 / 0.9950. By minutes (<5 / 5-12 / 12-24 / 24+): 2023 0.9964 / 0.9930 / 0.9979 / 0.9993; 2024 0.9912 / 0.9951 / 0.9941 / 0.9958. At most 0.7 points of asymmetry. | PASS (note) |
| 3d | Stint identity: stint regulation minutes against box minutes, non-OT games | `<scratch>/stintbox.py`; exit 0 x4 | 20,999 / 24,342 / 25,248 / 25,150 | within 1 min: 0.9878 (2019), 1.0000 (2022), 0.9998 (2023), 0.9999 (2024) | PASS (S5, S6) |
| 4 | Knockout / re-expression | not runnable without outcomes; dryrun e = 42 rows used as a natural experiment | 2024 Q4 rows: pts 75,156 / 22,365; fg3m 52,041 / 15,665 | M − B0 on the rows where raw B0 is the truth: pts −0.0042 / −0.0011, fg3m −0.0043 / −0.0046, reb −0.0009 / −0.0012, ast +0.0017 / +0.0010 (normal / blowout) | **FAIL of baseline fairness (M4)** |
| 5 | Shuffled control (g) | `dryrun.json` `shuffled_gain_vs_b0` | 701,599 rows | −0.195. It can never exceed +0.001. | **inert (M3)** |
| 6 | Integer support | read `rule.py:174-196`, `eval.py:137-144` | n/a | B0, B1 and A0 are all integer-grid survival functions at integer lines; M is a direct binary probability; every arm is scored as P(final >= integer line) by log loss | PASS |
| 7 | Digests | `.venv-1/bin/python -W ignore -m research.eval.in_game_line_eval digests` (exit 0, 9 s), then `rule.input_mismatches(declared, recomputed)` | 31 lines | `[]` | PASS |
| 8 | Text sha and harness pin | `rule.text_sha(doc)`, `rule.pin_of(rule.disk_blobs())` | 4 files | `72c21fd432a3fcf7b84f1d70d112cd5e15d4f309ed38e1312b6efb6873f5b414`, `add7798ba8c5d091f0df2b3df3fa6e59e70c35fa88a68c7cc79cbf7c3c7bb274`, both equal the claim; the text-read convention gives the same pin | PASS |
| 9 | run() order | read `eval.py:652-664`, `rule.py:455-481` | n/a | `require_harness_pinned`, then the text sha, then `verify_inputs(compute_input_digests)`, then `run_pipeline`. In `run_pipeline`, `load_states` comes before `finals_fn`, the only outcome reader. Order is right, but no stubbed negative test (S2). | PASS (S2) |
| 10 | Join-order determinism | 8 repeated inner joins of the 2023 state on a shuffled key frame | 154,541 rows | 1 distinct output order; stable in practice, not guaranteed by polars (S3) | PASS (S3) |
| 11 | Tests | `.venv-1/bin/python -m pytest tests/research/eval/test_in_game_line.py tests/eval/test_holdout_source_filter.py -q --no-cov -p no:cacheprovider` | n/a | **57 passed, 6 warnings, exit 0** (`.venv` has no pytest; `.venv-1` has identical python/polars/lightgbm/numpy/scipy/duckdb versions) | PASS |
| 12 | Lint and types | `.venv-1/bin/ruff check ...` exit 0; `ruff format --check` exit 0; `mypy` (4 modules) exit 0 | n/a | clean | PASS |
| 13 | Holdout scan | grep of every SQL and path: `read_games`, `read_stints`, `FINALS_SQL` carry `season <= 2024`; pbp paths go through `check_game_id`; A0 goes through `check_season`; holdout filter test passes | n/a | no 2025 path | PASS |

## Findings

### MUST (blocking the freeze)

**M1. `ast` state depends on post-checkpoint play-by-play (whole-game name membership).**
- Where: `research/eval/in_game_line_features.py:147-151`. `names` is built from every pbp row of the game with `player_id > 0`. Both the unique-surname path
  (`:168`) and the `st is None` path (`:157-163`) read it.
- What it does: an assist at time t resolves only if the assister has his OWN pbp event somewhere in the game, possibly after the checkpoint.
  Appearing in the pbp is box-score membership by another name (BEST_PRACTICES "Use rosters you'd actually know").
- Check 1: 185 of 77,614 rows (0.24%) differ, always with more `ast` in the full-game state. pts, reb and fg3m are unaffected, and so is the
  pts primary.
- Fix: resolve candidates only from information available at t:
  - the name dictionary comes from pbp rows with `t <= event t`, or from the season's prior games, or from a pre-game roster;
  - candidates are restricted to teammates on court at t (`a < t <= b`), which the code already computes at `:172-175`.

  Then re-run check 1 until it shows 0 rows in every column, recompute `PIN_STATE_*`, and run Run 5 of the gate. Alternative: drop ast from
  the secondary family, say so, and leave the code as is.

**M2. The planted-future identity cannot detect whole-game aggregates. Add the truncation identity as a gate item and as criterion (h).**
- Where: `features.py:518-564`. The plant duplicates later events with unchanged names and rewrites only post-cut stints, so it changes nothing
  that a whole-game aggregate reads. It passed with M1 present.
- Fix: add `truncation_identity(season, n_games)` to the features module. For each checkpoint e, build the state from `pbp.filter(t <= T)` with
  stints clipped at T (`end_clock` set to T's clock for stints spanning T; stints starting at or after T dropped). It must equal the full-game
  state at e on every column, with no missing or extra rows. My scratch `trunc_leak.py` is a working reference.
- Run it in the gate for all six seasons (>= 150 games each) and in `run_pipeline` alongside `planted_future`. Fold it into criterion (h)
  (`rule.py:361,379`; doc line 25, item (h)).
- Add a unit test in which a same-surname teammate appears only after T.
- Keep the planted check as a second guard.

**M3. Criterion (g), the shuffled-label refit, is inert.**
- Where: `eval.py:216-217,501-508`, `rule.py:84,378`. Training labels are permuted over all rows, so shuffled M predicts roughly the base rate.
  The dryrun gives a gain of −0.195 against a bar of +0.001: the guard cannot fire.
- Why it matters: the playbook (item 3) lists "an inert shuffle (only false passes)" as an invalid-test pattern. BEST_PRACTICES 2026-10-12
  requires guards sized from a permutation null.
- Fix (pick one, before freeze):
  - (a) Delete (g) and stop citing it as a guard.
  - (b) Replace it with the re-expression control M0: the same stage-B LightGBM and Platt on {`b0_logit`, `e`, `line`, `need`} only. Criterion:
    M − M0 point <= −0.0025 (half the floor) on 2024 pts, with the binding CI upper bound < 0. This is the "does the arm only re-express the
    base?" check, and it can fire.

  (b) is recommended because it also covers M4's residue.

**M4. Baseline handicap: one pooled 2-parameter Platt for B0 and B1 across all checkpoints and lines.**
- Where: `eval.py:234-236`.
- The dryrun is a natural experiment. At e = 42 the synthetic final is `cur + NegBin(B0 mean)`, so raw B0 is the exact truth on those rows.
  Recalibrated B0 still loses to M by 0.0042 (pts, n 75,156) and 0.0043 / 0.0046 (fg3m). By propriety that is a lower bound on what pooled
  Platt costs B0 there.
- On real data B0's miscalibration varies by checkpoint (pace shrink, OT, rate prior). Part of any real M − B0 gain can therefore be cheap
  per-cell recalibration, not state information. That is the single likeliest way the claim "a state model beats linear pace" passes falsely.
- Fix:
  - add B0e and B1e: Platt per checkpoint e (7 x 2 parameters), fitted on training rows only;
  - the binding B0 is the better of {B0, B0e} on the scored rows (the same rule as "better B1", doc line 84), and likewise for B1;
  - criteria (a)-(c), (d) and (f) use the binding baselines;
  - report raw B0 as well.
- In the dryrun, also report M − raw B0 on e = 42 rows as an outcome-free pipeline leak test. That contrast must be >= −(2 x its bootstrap
  half-width), because nothing M sees depends on the e = 42 draw.

**M5. The dryrun was not outcome-blind. The exposure statement is wrong and must be corrected before freeze.**
- Where: doc line 17 ("Blind to: ... all arm performance") and line 193 ("These are artifacts of the construction, not evidence").
- The synthetic final is the REAL pbp production through minute 42 plus a B0 draw. For a unit at e < 42, most of the remaining production in
  its "outcome" is real. The dryrun cells show the gap tracking the real share:
  - 2024 pts M − B0 is −0.076 at Q1 (n 178,385), −0.027 at Q2, −0.011 at Q3 and −0.004 at Q4;
  - reb, ast and fg3m show the same pattern.

  So the modeler and main session have seen strong evidence that M beats B0 on real in-game production before the freeze.
- Fix:
  - record this in "Exposure, said first" as item (d);
  - the frozen-test priors (0.55 / 0.30 / 0.20) stay scored as written; they predate the dryrun (commit 16424f8, 23:57:49);
  - add the rule that any amendment after the dryrun may only harden the test (stronger baselines, more guards) and never relax a criterion.

  M1-M4 all qualify: each makes a pass harder or touches only ast.

### SHOULD

- **S1. Pin the outcome table and duckdb.**
  - `compute_input_digests` (`eval.py:245-269`) covers games, stints, state and A0, but not `player_game_stats`. That table is the source of
    `y`, of `pregame_mean`, of `rec_minutes` and of the 2018 warm-up.
  - `nba.duckdb` is a production store refit daily. The T507 precedent pinned every DB table a number depends on.
  - Fix: add `PIN_FINALS_{2018..2024}` = `sha_frame(read_finals(root, (s,)))`, computed by code that prints only the hash. Disclose it as a
    mechanical read. Add `"duckdb"` to `LIB_NAMES` (`rule.py:399`).
- **S2. Missing negative test for run order.** Add a test that stubs `require_harness_pinned` and `text_sha` to pass, makes
  `compute_input_digests` return a mismatch, and makes `read_finals` and `finals_fn` raise. `run` must exit through `verify_inputs`
  (SystemExit), never through the reader. Add a source-order assertion as T507 did. The current test (`test_in_game_line.py:300-310`) only
  shows refusal when the rule is unfrozen.
- **S3. Guarantee row order.** Use `maintain_order="left"` (or an explicit `.sort`) on the joins in `attach_a0` (`eval.py:342`) and
  `minutes_rows` (`eval.py:328-334`, which feeds the stage A fit). Order is stable today (check 10), but polars does not promise it, and T507's
  F-A5-1 was exactly this class of bug.
- **S4. Report where the money is.**
  - Pts has about 29 rows per player-game. Low scorers carry all 35, and a large share of rows are near-certain tails (bench player, line 30).
  - Add descriptive slices "contested" (raw B0 in [0.1, 0.9]) and "starters", plus a reliability table at the line (playbook, Quantile
    section).
  - Not a criterion, because the dilution makes a pass harder, not easier.
- **S5. Write down that stints are a look-ahead reconstruction.**
  - Source: `nba/parse/lineups.py:28-35,44-47`. Period openers come from a within-period look-ahead, Q1 is seeded from box starters, and the
    name-resolution ladder (d) uses the box roster.
  - On-court identity is observable live, and stint minutes match box minutes (check 3d), so this is not a leak of unknowable information.
  - Neither the planted test nor my truncation test can exercise it, because both pass stints in directly. Add this to "Known-at", and state
    that a forward shadow must take minutes and on-court status from a live source.
- **S6. The "stints tile" completeness component is true by construction.** `lineups.py:17-19` says exactly that. Report stint-against-box
  minutes within 1 min per season (check 3d gives 0.988-1.000) as the identity check.
- **S7. The dryrun never exercised the 2018 warm-up path.** `synthetic_finals` produces no 2018 rows, so the dryrun's recency starts in 2019,
  while the real run reads 2018 from `nba_history.duckdb` (26,010 played rows). Add 2018 to `check_finals_schema` (`eval.py:610`) and give the
  synthetic provider 2018 finals.
- **S8. Say in the doc that completeness is a gate measure, not a row filter** (`run_pipeline` scores every game). Record check 3a's table.

### NIT

- **N1. Typed times later than the commits that carry them** (BEST_PRACTICES "Timestamps come from the clock"):
  - status line "23:50 CDT" in 929fc41 (committed 23:37:16);
  - Amendment 1 "dated 23:50" in e539bc8 (23:48:19);
  - Amendment 2 "23:58 CDT" in 16424f8 (23:57:49).

  The commit order still supports every before/after claim.
- **N2. The CI code is local** (`rule.py:219-281`), not `truth/bootstrap.py`, which does not exist yet (`nba/truth/` holds `metrics.py` and
  `walkforward.py`). Flagged per the role; the code is a correct paired cluster bootstrap with fixed seeds.
- **N3. Two in-sample fits** (`eval.py:221`, `:239`): stage A predictions on training rows and M's Platt on in-sample stage-B scores. Both
  handicap M; no false-pass risk.
- **N4. Changing the gate constants after the smoke was harmless for a false pass.** NOISE_SIGMA and POS_SIGMA moved from 0.10/0.30 to 0.50.
  NOISE_SIGMA up makes the detectable-effect bar harder. POS_SIGMA only sizes an oracle positive control. No test constant, floor or
  criterion moved. Disclosed adequately.

## The questions in the brief
1. **Leakage.**
   - pts, reb, fg3m, minutes, fouls, margin and starter: none found (check 1).
   - Unit selection is on-court-by-stints at t <= T; the box-row requirement is non-binding (check 3b).
   - Recency is strictly earlier (`shift(1)`, tested).
   - The A0 join is near-symmetric (check 3c).
   - `m48` is used only as the stage A target.
   - ast: a whole-game aggregate (M1). The planted-future test is too weak (M2).
2. **Baselines.** The constants are declared and not fitted, and every arm is recalibrated on training rows only (`eval.py:234-240`). The
   pooled recalibration handicaps B0 (M4). Leaving A0 uncalibrated is by design and only weakens the B1 the rule does not bind on.
3. **Unit weighting.** The game, player-season and week clusterings plus the top-5 share handle within-player-game dependence (player-season
   bound 40/40 in the gate). Row weighting dilutes rather than inflates the effect (S4).
4. **2023 completeness 0.9545.** No game is dropped. Incompleteness is not tied to blowouts beyond noise (check 3a). The thin margin only says
   the 2023 fouls feed misses fouls more often (Dec-Jan 0.937-0.939).
5. **Pre-priors smoke.** Harmless (N4).
6. **A leak test that needs no outcomes.** Three proposals:
   - (i) truncation identity (M2), already run, and it found M1;
   - (ii) the e = 42 truth invariant inside the dryrun (M4);
   - (iii) a recency plant: set the current game's final to 999 in a test frame, then assert `pregame_mean` and `rec_minutes` are unchanged for
     that player-game and changed for the next one.
7. **Run order and pins.** Order is correct. 31 of 31 digests reproduce, and the text sha and pin equal the claims. Missing: the finals pin,
   duckdb, a stubbed order test and a join-order guarantee (S1-S3).
8. **Holdout.** No season >= 2025 path. Tests: 57 passed with `--no-cov`. Holdout filter test passes.

## Corrected family and power
- Primary: one test (pts), with B0, the better B1 and (after M4) B0e/B1e as binding baselines. No multiplicity inside it; every criterion must
  hold.
- Secondary: reb, ast and fg3m, with BH at 0.05 over the three. That is the family actually run.
- Power: the binding half-width is 0.00185 (2023) and 0.00183 (2024) on about 700k rows, 1,225 games and 521 player-seasons. The MDE is about
  0.0026 at 80% power, so "underpowered" is not a possible reading of a null at the 0.005 floor.

## Single biggest risk
M passes against B0 because B0's recalibration is pooled across checkpoints and lines, not because the state carries information. Criterion
(g) as written cannot detect that.

## Not verified
- Real-outcome performance of any arm. Deliberately not computed: that would contaminate the frozen test.
- How much per-checkpoint miscalibration B0 has on real training seasons. Measuring it needs outcomes; I declined pre-freeze.
- The lineup tracker's look-ahead error rate (`open_filled`, `q1_mismatch`). Not computed; check 3d bounds its effect on minutes.
- Whether stage B is bit-reproducible across processes. Not re-run; the modeler reports two identical dryrun JSONs.
- Truncation test on 2020 and 2021. Run on 2019, 2022, 2023 and 2024 only, 150 games each.

## Recommended changes (never applied by the reviewer)
M1-M5 in full, then S1-S3. Then Run 5 of the gate, the dryrun repeated with the e = 42 invariant, new pins, and a narrow re-review of the diff
before the `prereg(IN_GAME_LINE_MODEL)` commit.

## Drafted rows (for the main session; not appended)
- TEST_LEDGER (under T508): `| T508-review-1 | 2026-10-22 | IN_GAME_LINE_MODEL adversary pre-freeze review (docs/reviews/IN_GAME_LINE_MODEL_prefreeze_2026-10-22.md) | label-free (box keys/minutes only, seasons <= 2024) | NOT_READY: truncation test clean for pts/reb/fg3m/minutes/fouls/margin/starter (0 of 77,614 rows over 600 games) but ast leaks whole-game name membership (185 rows, 0.24%); planted-future cannot see whole-game aggregates; shuffled control inert (dryrun -0.195); pooled Platt handicaps B0 (dryrun e=42 rows, where raw B0 is the truth: M-B0 -0.0042 pts n=75,156); dryrun not outcome-blind (Q1 -0.076 vs Q4 -0.004); digests 31/31, text sha 72c21fd4 and pin add7798b reproduce; 57 tests pass | n/a | n/a | n/a | n/a | BLOCKED pre-freeze | not holdout |`
- DECISIONS: `| 2026-10-22 00:28 | T508 pre-freeze review NOT_READY (M1-M5). Amendments after the dryrun may only harden the test. | The dryrun's synthetic outcomes contain real production through minute 42, so the authors are no longer blind to M vs B0. | A re-review that finds M1-M5 closed. |`
