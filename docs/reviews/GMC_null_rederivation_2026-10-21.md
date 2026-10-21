# GAME_MARKET_CONTEXT null: adversary re-derivation (2026-10-21)

Subject: GAME_MARKET_CONTEXT (prereg `docs/prereg/GAME_MARKET_CONTEXT.md`, frozen sha256 fd37a3f3, freeze commit 0a4f230,
result commit 496fcd6; ledger T389-T414; closed as a null on 2026-10-11). Asked by Devin: "how do we know those tests were
done accurately?" This null never had an adversary review, so it is attacked here the way a win would be.

Model class: gradient-boosted trees (LightGBM context residual, `nba/props/context_residual.py`), a feature-substitution and
feature-addition test (A1 replace, A2 add) against a within-date placebo.

**Verdict: STANDS as a null at the rule's floor (-0.005 CRPS).** The interpretation written into DECISIONS and FAN_KNOWLEDGE
overreaches and should be corrected (finding F1).

Verdict: APPROVED

Top finding: the arithmetic, rule integrity, join, sign convention, missingness and placebo all check out, and the A1/A2 trees
do read the market columns (3-6% of gain, 17-73 splits per column, predictions move by 0.16 pts on average when the market is
permuted). But the test has no positive control: removing ALL game context (Elo margin and r20 scoring replaced by game-free
placebo numbers, P1a vs A0) costs nothing measurable in 2024 either (pts -0.0008 [-0.0036, +0.0018], n 27,701, 1,320 games).
So the result is "game-level context of any kind, proxies or market, is worth less than about 0.003-0.005 CRPS to this model",
not "the trees already extract what the market knows from our own numbers". A real pts gain of -0.003 is not excluded.

All work on seasons 2023 and 2024 (or the run's own cached OOF). Every DuckDB open read_only=True. Attack scripts live in the
session scratchpad (`.../scratchpad/gmc/`), not in the repo.

## Per-check table

| # | Check | Command (run from the worktree) | Numbers (n, CI) | Result |
|---|---|---|---|---|
| 1a | Frozen sha at freeze, result and HEAD | `for c in 0a4f230 496fcd6 HEAD; do git show $c:docs/prereg/GAME_MARKET_CONTEXT.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256; done` | fd37a3f3... at all three; `diff` of the frozen text 0a4f230 vs 496fcd6 exit 0 (empty) | PASS |
| 1b | Run started after the freeze | `stat -f '%SB %Sm %N'` on `data/ops/game_market_context_*.log`, cache `arm_*` files, report files; `git log --format='%h author=%ad committer=%cd'` | freeze commit 17:19:03 (author = committer); run log born 17:19:13 ("run start 17:19:13"); first non-A0 artifact `arm_pts_A1.npz` 17:19:38; A0 cache 17:04 (gate, allowed); gate logs a-e (17:04-17:17) contain no A1/A2/A3/P1 lines; results/report 17:27:18; result commit 17:28:36. Ten minutes is the real run time (log: 440 s for 20 arm fits) | PASS |
| 1c | Results provenance | `results.json` keys `git_sha`, `sha256` | `sha256` = fd37a3f3; `git_sha` = **4a957b1** (an unrelated LLM_SHADOW draft committed at 17:25:35, mid-run), not 0a4f230; the harness stamps HEAD at write time. 4a957b1 touched no GMC file, so the code that ran is 0a4f230's | PASS with minor finding F4 |
| 2 | Re-derive dCRPS from the stored per-row arrays | `uv run --no-sync python scratchpad/gmc/rederive.py` (own CRPS = 2 x mean pinball over the 199 taus; own game-clustered bootstrap, B 2000, seed 20261021) over `data/ops/game_market_context_cache/arm_{stat}_{arm}.npz/.parquet` | Every point estimate (A1-A0, A2-A0, A1-P1a, A2-P1b, P1-A0; 4 stats x 2 seasons; 2023 mature rows) matches results.json and the ledger to 4 dp (48/48 + 8/8 mature). A0 CRPS equals the ledger to 1e-6. n 27,619 rows / 1,318 games (2023), 27,701 / 1,320 (2024), 2023 mature 12,757. Key cells: pts 2024 A1-A0 +0.0005 [-0.0023, +0.0031], A2-A0 -0.0023 [-0.0046, +0.0000]; reb 2024 A1-A0 -0.0008 [-0.0019, +0.0003], A2-A0 -0.0001 [-0.0010, +0.0008]; pts 2023 mature A1 +0.0016 [-0.0026, +0.0058], A2 +0.0049 [+0.0008, +0.0089]; reb 2023 mature A1 +0.0026 [+0.0005, +0.0047], A2 +0.0025 [+0.0010, +0.0041]. CIs differ from the ledger only in the 4th dp (different bootstrap seed) | PASS |
| 3a | Did the arm see the market? (importance) | `scratchpad/gmc/knock.py ... pts,reb 2024-03,2025-01,2025-03` (refits A0/A1/A2 on 3 mature blocks; first verifies the refit reproduces the cache bit-for-bit) | All 18 refits reproduce the cached integer grids exactly. Market gain share A1 3.2-5.9%, A2 2.5-4.5% (A0 proxies 9-15%); splits per market column 17-73; mean abs change of the mean head when the market is permuted across games within date: pts 0.13-0.18, reb 0.04-0.08 | PASS (not a non-test) |
| 3b | Knockout at predict time (same fitted model) | same script | real minus within-date-shuffled market, per block (n about 4,850 rows, about 230 games): pts A1 +0.0014 [-0.0039, +0.0064], -0.0029 [-0.0070, +0.0009], -0.0018 [-0.0055, +0.0017]; reb A1 -0.0014 [-0.0036, +0.0009], +0.0006 [-0.0019, +0.0028], -0.0015 [-0.0036, +0.0005]. The model uses the columns; the game-specific information in them is worth at most a few thousandths, signs inconsistent across blocks | PASS (consistent with null) |
| 3c | Join: 50 sampled 2023/2024 games against odds_history t60 | `scratchpad/gmc/join.py` (independent SQL `season<=2024 and snapshot_kind='t60'`, latest snapshot <= tip-55 per book, main line closest to even money, Pinnacle-if-complete else median of >=2 consensus books) | 0 mismatches of 50 games (47 Pinnacle, 3 consensus) on home margin, total and source; 50/50 sampled player rows carry the correctly signed `mkt_margin` | PASS |
| 3d | Sign convention against final margins | same script and `scratchpad/gmc/sign.py` | game level corr(market home margin, actual home margin) 0.481 (2023, 1,318 games) / 0.511 (2024, 1,315); corr(total, actual total) 0.510 / 0.454; mean market home margin 2.15 vs actual 2.31 (2023). Player level corr(`mkt_margin`, signed actual team margin) 0.513 / 0.537 (n 28,197 / 28,118), Elo `exp_margin` 0.449 / 0.482: the market is sharper, correctly signed and correctly joined | PASS |
| 4 | Missingness of market columns by outcome-correlated groups (2023-24, n 56,433 played rows) | `sign.py`, `join.py` | overall NULL 0.209% (2023 0.000%, 2024 0.418%, five games: 2024-11-02, two 2024-12-14 Cup games, 2025-01-23 and 01-25 Paris games). played vs DNP: the frame has no DNP rows (attach_realised keeps played rows; the gate's roster-level audit gave DNP 0.17% vs >20 min 0.17%). minutes<5 0.424% vs >=5 0.187%; starter 0.190% vs bench 0.226%; win 0.208% vs loss 0.210%; pts above median 0.182% vs below 0.233% (reb 0.192/0.225, ast 0.181/0.226, fg3m 0.200/0.214). The <5-minutes gap (0.24 pp) comes from two blowout Paris games; the cause (no T-60 snapshot for those games) is knowable before tip. 111 of 27,701 2024 scored rows; cannot move a season mean by more than about 0.0001 | PASS |
| 5a | Placebo breaks the game-to-market link | `sign.py` (P1a frame from `arm_frames`) | rows keeping their own (margin, total) tuple 0.137% (77 of 56,315); same margin value 1.96% (ties of common spreads); within home/away corr of placebo margin with actual margin 0.000-0.028 (real 0.49-0.53); marginal kept (total mean 226.66 vs 226.55, sd 9.89 vs 9.88) | PASS |
| 5b | Placebo is information-free | same | corr(placebo total, real total) **0.255 in 2023**, 0.03 in 2024: the within-date shuffle keeps the date-level league total (2023-24 scoring rose then fell after the All-Star break). That date-level pace is legitimately pre-game information, so R2 tests game-specific information only; this makes R2 slightly conservative in 2023, not a leak | PASS with minor finding F5 |
| 5c | Same seeds and rows | harness `score_stat` asserts identical keys and `y`; `SEEDS` all 0; `knock.py` refits reproduce the cache | identical | PASS |
| 5d | Placebo noise (permutation null, BEST_PRACTICES 2026-10-12) | `scratchpad/gmc/permnull.py ... pts,reb 2025-01,2025-03 12` (A1 refit on 12 placebo seeds, 2 mature 2024 blocks) | real minus placebo, per block: pts null mean +0.0015 sd 0.0015 (Jan), -0.0015 sd 0.0018 (Mar); reb +0.0006 sd 0.0005, -0.0006 sd 0.0009; the seed-0 placebo the run used sits inside each distribution. Real is not separated from its placebos | PASS (null) |
| 6 | Power | analytic from the re-derived clustered se | se: pts 2024 0.00135 (A1) / 0.00118 (A2), 2023 mature 0.0022 / 0.0021; reb 0.00057 / 0.00046, mature 0.0011 / 0.0008. P(R1 passes both seasons) at a true effect of -0.003: pts 1.3% (A1) / 0.8% (A2), others about 0; at exactly the floor -0.005: 25% for every cell (the floor is a point-estimate bar, so at most 50% per season); at -0.008: pts 90-92%, others >= 99.7% | see power note |
| 7 | Scope of the result commit | `git show --stat 496fcd6` | DECISIONS (+12), FAN_KNOWLEDGE (+5), MORNING_BRIEFING (+6), TEST_LEDGER (+38), prereg results section (+12, below the line). No code, config or frozen text. results.json, ledger_rows.md and the report md were committed later in 3a875a2 (18:26, bundled with unrelated work); their content is what the re-derivation matched | PASS with minor finding F4 |

Not run: planted future and lag-one-day (a leak would fake a win, not hide one; the gate's 11/11 plants and the 62.5-minute
minimum lead are recorded in gate.json and were not repeated); integer support (every arm is on the same integer grid by
construction, `to_integer_support` in `collect_arm`); shuffled labels (not applicable to a null).

## Findings (severity-ranked)

**F1 (major, interpretation).** `docs/DECISIONS.md` 2026-10-11 17:28 row ("the trees already extract what the market knows
about blowout risk and pace from our own numbers") and `docs/FAN_KNOWLEDGE.md` ("so the trees already had it") are not supported.
P1a (the A1 names with game-free market numbers, i.e. no game context at all) vs A0 in 2024: pts -0.0008 [-0.0036, +0.0018],
reb -0.0006 [-0.0018, +0.0006], ast +0.0003 [-0.0006, +0.0011], fg3m +0.0003 [-0.0003, +0.0009] (n 27,701, 1,320 games). The
proxies themselves are worth nothing measurable, so the run cannot say the proxies carry the market's information; it says
game context of any kind is below the harness's resolution. Fix: replace that sentence in both docs with "neither the market
lines nor our Elo/r20 proxies add measurable CRPS to this model (removing all game context costs -0.0008 [-0.0036, +0.0018] on
pts 2024); effects smaller than about 0.003 (pts) are not excluded."

**F2 (minor, wording).** The prereg results section, ledger narrative and FAN_KNOWLEDGE say every real arm is "within ±0.001 of
its placebo". Re-derived: pts A1-P1a 2024 +0.0013, 2023 -0.0010; fg3m A2-P1b 2023 +0.0007 [+0.0002, +0.0013] (real worse than
placebo). Fix: "within ±0.0013 of its placebo, none better with CI upper < 0".

**F3 (minor, design, for the follow-up rule).** R1's floor is a point-estimate bar, so a true effect exactly at the floor passes
one season with 50% and both with 25% probability; the harness's `mde` field (`(hi - lo) / 2`, `four_factors_eval._delta_stats`)
is a CI half-width, not an MDE. Fix for the live follow-up: state the 80%-power effect (2.8 x se) and the power at the floor
before freezing.

**F4 (minor, provenance).** `results.json` `git_sha` is 4a957b1 (HEAD at write time, a commit made mid-run) while the run's code
is 0a4f230; the result commit did not include results.json (landed in 3a875a2 an hour later with unrelated files). Fix: capture
`git_sha()` at run start in `run_arms` (`research/eval/game_market_context_eval.py`, the `"git_sha": git_sha()` at line 1104)
and commit result artifacts with the result commit.

**F5 (minor, placebo definition).** The prereg says the placebo has "no game information"; it keeps the date-level league total
(corr 0.255 with the real total in 2023). Correct as a game-specific test; say so in any follow-up rule.

**F6 (major, holdout hygiene, found during this review).** `research/eval/game_market_context_eval.py:173` calls
`build_game_tipoff()` without `max_season=MAX_SEASON`; the 2026-10-15 hygiene fix (3c42394) patched the availability call on line
170 but missed this one, so `load_study` materialises 2025-26 and 2026-27 schedule rows (game_id and tip times only; no scores,
stats or odds). Both the original 10-11 run (which also lacked the line-170 fix) and this review's `load.py` read them; this
review wrote them to a scratch file, which was deleted after the check (6,790 rows, 2,600 of them 2025/2026 ids; only
`game_id, tipoff_utc, tip_et`). No 2025 outcome, stat or odds row was read (every SQL carries `season <= 2024`; `base`,
`games` assert max season 2024). Fix: `tips = build_game_tipoff(max_season=MAX_SEASON)` on line 173, plus a test in
`tests/eval/test_holdout_source_filter.py`. Whether this incidental metadata read gets a HOLDOUT_ACCESS_LOG line is the main
session's call (recommended: yes, as a metadata-only read, no 2025 model touch).

## Power and family note

Family actually run for R1: {A1, A2} x 4 stats = 8 per season, as declared; A3 and P1 are descriptive and placebo arms, not tests.
BH can only make a null harder to overturn; no cell has a negative point estimate beyond -0.0023, so no BH correction changes the
verdict. What the null supports, from the 2024 CIs (n 27,701, 1,320 games): versus A0, no arm improves by more than 0.0046 (pts),
0.0019 (reb), 0.0010 (ast), 0.0005 (fg3m); versus its placebo (the information contrast), by more than 0.0026 (pts), 0.0010
(reb), 0.0011 (ast), 0.0006 (fg3m). Every 2024 CI excludes the -0.005 floor, so "no effect at the floor" is a positive
conclusion, not just a failure to reject. A real pts gain of -0.003 can coexist with the result (pts A2-A0 2024 CI reaches
-0.0046; 2023 A1-P1a reaches -0.0035): for pts, "underpowered below 0.004" is the honest qualifier. For reb, ast and fg3m a
-0.003 effect is excluded.

## The single most likely way a real gain was hidden

Encoding, not a bug. A pace effect on a counting stat is multiplicative (a 3% faster game is worth about 3% of the player's own
volume), but the model's target is the additive residual `y - m10` and `mkt_total`/`mkt_pace` enter as plain game-level
columns, so the trees must learn an `m10 x pace` interaction with 200 trees of 15 leaves and min_child 100. Combined with
per-block fit noise of about 0.0015 (pts), a real pts effect of 0.002-0.004 would read exactly like this run. The rule forbids
re-encoding the market columns, so this is not a defect of the run; it is the open question a new rule would have to ask (for
example a per-row expected-volume offset `m10 x mkt_total / league mean`), and the null cannot be cited against it.

## Playbook check (MODEL_PLAYBOOK, gradient-boosted trees)

1. Information or capacity: information (the market's T-60 game lines), the class that has won here before.
2. Enough data: 2022 has no market (NULL), so A1/A2 learn market effects from 2023+ only; 2023 blocks before February had <= 480
   market games, which AM1 handled by judging 2023 on mature blocks; every 2024 block has >= 1,318. Adequate for a GBM.
3. Test validity: real tips, played rows, integer support for every arm, game-clustered CI, placebo of the same shape. Missing:
   a positive control showing the harness can detect game context at all (F1), and a permutation-null-sized placebo (the
   2026-10-12 rule postdates this run; measured here in 5d, consistent with the null). The NULL-market-in-2022 pattern doubles
   as a season indicator, identical in real and placebo arms, so R2 is unaffected. Valid test, so this is a null, not INVALID.
4. Lineage: OWN_TAG (same harness, information already held, tied) and the proxies' own CONTEXT_RESIDUAL lineage; no
   disqualifying guard was dropped.
5. Scored against: integer CRPS (decision) and DraftKings T-60 over/under log loss (non-inferiority only). The over/under numbers
   were not re-derived here.

## What was not verified

- The R4 over/under log-loss numbers and the A3 descriptive arm (not re-derived; neither can turn a null into a win).
- That no pre-freeze A1/A2 fit was run and its cache deleted: the cache is reused when present, all non-A0 cache files and the run
  log post-date the freeze, and the gate logs contain no non-A0 arm, but deletion cannot be ruled out from file metadata.
- The gate's planted tests were not re-run.
- Knockout and permutation null cover 3 and 2 mature blocks for pts and reb only (about 4,850 rows each), not all blocks or stats.

## Recommended changes (main session acts; none applied here)

1. Correct the DECISIONS 2026-10-11 17:28 reading and the FAN_KNOWLEDGE GMC bullet per F1 and F2 (new dated DECISIONS line, old
   one left intact).
2. Fix `research/eval/game_market_context_eval.py:173` per F6 and add the holdout-source test; consider a HOLDOUT_ACCESS_LOG
   metadata-only line.
3. Stamp `git_sha` at run start (F4).
4. Adopt the null-review rule below in BEST_PRACTICES.

## Drafted rows (not appended)

TEST_LEDGER (next free id):
`| T### | GAME_MARKET_CONTEXT null re-derivation (adversary, 2026-10-21) | STANDS at the -0.005 floor: 56/56 point estimates re-derived to 4 dp from the cached OOF (n 27,619/27,701 rows, 1,318/1,320 games); join 50/50, sign corr 0.51/0.54, market NULL 0.21% with no outcome asymmetry, placebo fixed points 0.14%; arms read the market (gain 3-6%); no positive control: P1a-A0 pts 2024 -0.0008 [-0.0036, +0.0018]; pts gains < 0.004 not excluded; docs/reviews/GMC_null_rederivation_2026-10-21.md |`

DECISIONS:
`## 2026-10-21 — GAME_MARKET_CONTEXT null re-derived: stands; reading corrected`
`What: the null stands at the -0.005 floor; the 10-11 reading "the trees already extract what the market knows from our proxies" is withdrawn: removing all game context also costs nothing measurable, so game context of any kind is below this model's resolution (pts < ~0.004 not excluded). Why: adversary re-derivation (docs/reviews/GMC_null_rederivation_2026-10-21.md). What would reverse it: a rule with a positive control and a volume-proportional pace encoding showing a gain.`

BEST_PRACTICES (proposed, see lesson below):
`## Nulls get a positive control and an "arm saw it" check (2026-10-21)`
`Incident: GAME_MARKET_CONTEXT closed as "market = placebo, the proxies already carry it" without showing the harness could detect game context at all; removing all game context also tied (P1a vs A0). Rule: a null is recorded only with (1) evidence the arm read the new input (gain share or a predict-time knockout), (2) a positive or negative control showing the comparison can move (e.g. the information removed entirely), and (3) the smallest effect the CIs exclude, stated next to the floor.`

## General lesson for how we review nulls

A win is attacked for leaks because a leak makes a fake win; a null hides a different failure, a test that could not have moved.
For every null we should check three things before writing it down: that the new input actually reached the model (coverage,
correct join and sign, importance or a predict-time knockout, since a silently NULL or mis-joined column reads as "no
information"); that the harness can detect an effect of the claimed kind at all (a positive control, or at minimum the
"remove the whole information class" contrast, here P1a vs A0, which was in the results but never read); and the effect size the
CIs exclude, so the record says "no effect larger than X" rather than "no effect". The interpretation sentence is where nulls go
wrong: this run's arithmetic was exact, but the story attached to it claimed more than the data showed.

```
status: done
rule: GAME_MARKET_CONTEXT fd37a3f3
gate: [{check: frozen sha 0a4f230 vs 496fcd6, before: fd37a3f3, after: fd37a3f3, pass: true}, {check: run after freeze, before: freeze 17:19:03, after: run start 17:19:13, pass: true}, {check: re-derive 56 point estimates to 4 dp, before: ledger, after: 56/56 match, pass: true}, {check: join 50 games, before: odds_history t60, after: 0 mismatches, pass: true}, {check: arm reads market, before: gain 0?, after: gain 3-6%, pass: true}, {check: positive control, before: none, after: P1a-A0 pts 2024 -0.0008 [-0.0036, +0.0018], pass: false}]
changed_files: [docs/reviews/GMC_null_rederivation_2026-10-21.md]
backups: none
drafted_rows: {ledger: docs/reviews/GMC_null_rederivation_2026-10-21.md, decisions: docs/reviews/GMC_null_rederivation_2026-10-21.md, changelog: none, holdout_log: none}
numbers: [{name: pts 2024 A2-A0 dCRPS, value: -0.0023, n: "27701 rows / 1320 games", ci: "[-0.0046, +0.0000]", family: "GMC R1 8/season"}, {name: pts 2024 A1-P1a, value: +0.0013, n: "27701 / 1320", ci: "[-0.0009, +0.0035]", family: "GMC R2"}, {name: pts 2024 P1a-A0 (no game context), value: -0.0008, n: "27701 / 1320", ci: "[-0.0036, +0.0018]", family: descriptive}, {name: reb 2024 A1-A0, value: -0.0008, n: "27701 / 1320", ci: "[-0.0019, +0.0003]", family: "GMC R1"}]
verification: {ruff: n/a (docs only), format: n/a, mypy: n/a, pytest: "n/a (no code changed)"}
unstated_details: [own bootstrap seed 20261021 B 2000; knockout blocks 2024-03, 2025-01, 2025-03 chosen as mature blocks before looking; permutation null K 12 on 2 blocks]
cost: {requests: 0, credits: 0, wall_minutes: 35, ceiling: 600 s per command}
confidence: 0.85 because the over/under arm, all-block knockouts and a true positive control were not run
unknowns: [whether a pre-freeze arm fit existed and was deleted, R4 numbers not re-derived]
needs_devin: [whether the incidental 2025-26 schedule tip-time read (F6) gets a HOLDOUT_ACCESS_LOG line]
```
