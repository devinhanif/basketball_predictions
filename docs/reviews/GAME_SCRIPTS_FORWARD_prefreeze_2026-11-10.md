# GAME_SCRIPTS_FORWARD: adversary pre-freeze review (2026-11-10)

Reviewed: branch `agent/modeler-game-scripts-forward` at `c40573bb`; rule `docs/prereg/GAME_SCRIPTS_FORWARD.md` rev 1
(text sha256 `840ff16b`, reproduced with the rule's awk recipe); harness `research/eval/game_scripts_forward_eval.py`
(sha256 `283556b9`, reproduced); gate `research/eval/game_scripts_forward_gate.py`; tests; priors in `docs/PREDICTIONS_LEDGER.md`.
Branch scope against its merge base `56adf8f7`: exactly the 8 files the task names (no scope creep).

**Pre-freeze verdict: NOT_READY** (one blocker, four majors; each fix is small).

Verdict: BLOCKED

**Top finding.** The rule says it tests "the pre-tip probability the combo payload shows" and (4.4) that its `mu_t` is built
"exactly as the number the app shows does". It is not. The app's game-scripts payload (branch `agent/market-game-scripts`
`529a39ef`, `nba/export/combo_stage.py` lines 415-470 via `build._history_stage` -> `combo_stage.history_rows(src, day,
guard.LIVE_SEASON)`; contract `docs/contracts/game_scripts_v1.md` lines 29-30 and 214-215) builds `mu_t` from **live-season
games only**. The harness builds it as the parlay engine does (`nba/parlay/slate.py`): from every final game, 2025-26
included. On the 2024 analogue of the forward window (season <= 2024 only, n = 776 games, 2024-11-13 to 2025-03-07) the two
definitions differ by mean -1.04 points of `mu_t` (mean |d| 3.09 through mid-December, 1.47 after), change the shootout or
grind label on 49 of 776 games, and move the realised shootout share from 0.316 to 0.298: a 0.018 shift, 36% of the 0.05
tolerance. Two "live engines" disagree, and O3 ("as the live engine builds it") was decided without knowing that.

## Model class and playbook check
Classes: game-level distribution layer (Normal margin and total over the injury-Elo rating) for P/D; set/joint model
(Gaussian copula plus per-script latent shift) for S.
1. *Information or capacity:* neither added; a calibration audit. Agreed with the rule.
2. *Enough data for this class:* five frozen numbers, nothing fitted. Forward n is schedule-bound; 750 is defensible (power
   re-derived below).
3. *Test valid:* mostly yes (real tips, stamps strictly before tip verified on 154/154, planted future ignored, date
   clustering for a cross-game question). Invalid in one place: the scored number is not the served number (B1). The run
   does not execute the frozen code (M2), so validity at the look is not guaranteed by the freeze.
4. *Lineage:* `combo_eval` (exploratory, grind's pass a second look), combo_math B1, T490. Guards carried. Fine.
5. *Scored against:* calibration-in-the-large with a tolerance, Brier against own mean, Brier/calibration at the main line.
   Appropriate for the class ("reliability at the point that matters": the bins and slope are there).
Class check (distribution layer): slices fixed in advance, yes. Class check (joint): "is the effect detectable?" D and the
joint ratio are honestly labelled underpowered/descriptive. Yes.

## Checks run (all on season <= 2024, synthetic data, or label-free 2026 counts; no 2025 row was evaluated; no 2026 score read)

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| 1 | rule text hash | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/GAME_SCRIPTS_FORWARD.md \| shasum -a 256` on the c40573bb tree | n/a | `840ff16b...` | pass |
| 2 | harness hash | `shasum -a 256 research/eval/game_scripts_forward_eval.py` | n/a | `283556b9...` | pass |
| 3 | minimum-data gate, label-free re-run | `.venv-1/bin/python -W ignore -m research.eval.game_scripts_forward_eval status --data-root /Users/devin/Downloads/nba-prediction --assume-freeze 2026-10-19` (exit 0) | 154 finals | 160 listed, 154 final, 154 stamped, look not open (154 < 750) | pass |
| 4 | stamps and tips on the real DB through the harness's own `window_rows` (label-free; scratch `a1_window.py`, exit 0) | as named | 154 | bad tip or stamp 0 (harness expression); `game_date` == ET date of tip 154/154; lead 54-114 min; post-tip win rows 0; one version (`v2`, 2,912 rows); no game with two pre-tip versions | pass |
| 5 | outcomes unreadable before the look | read `window_rows` (boolean `final` only), `_need_open` on `load_scores`/`as_of_mu_t`/`leg_outcomes`; tests | n/a | guarded; tested | pass |
| 6 | season 2025 never scored | `refuse_seasons` on every scored frame; tests | n/a | guarded. 2025 scores enter `as_of_mu_t` as inputs (O3), not evaluated; see B1 | pass (as designed) |
| 7 | planted future on `mu_t` (synthetic DB, scratch `a2_e2e.py`, exit 0) | a game dated 2027-06-01 with total 800; a same-day game on 2026-11-20 with total 800 | 760 synthetic games | max abs change in `mu_t`: 0.0 (future), 0.0 (same slate), 20.8 on later slates (expected) | pass |
| 8 | run path end to end (synthetic, same script) | `_score_window` on a 45-day temp DB with an open look | 760 | integrity pass, closed-form deviation 1.1e-16, words produced, JSON-serialisable | pass (but untested in the suite, M4) |
| 9 | served-number reproduction: `script_prior` | test `test_priors_are_the_served_function_and_the_closed_form` | n/a | identical function | pass |
| 10 | served-number reproduction: `mu_t` source | read `combo_stage.py` on `agent/market-game-scripts`; 2024 analogue (scratch `a3_mu_t_source.py`, exit 0) | 776 games | mean d `mu_t` -1.04; labels differ on 49; shootout share 0.316 vs 0.298 | **FAIL (B1)** |
| 11 | served-number reproduction: payload rounding | `combo_build.build_game_payload` rounds `mu_m, sd_m, mu_t, sd_t` to 2 dp; 20,000 random games | 20,000 | blowout/close max abs dP 1.8e-4 / 1.0e-4; shootout/grind threshold flips on 0.9% of games (dP 0.017 there) | minor (m3) |
| 12 | code pinned at the look | read imports at run time | n/a | `nba.parlay.game_model`, `combo`, `nba.export.*` (private `_read_lines`, `_production_rows`, `_starter_rate`) and `line_cal_map_v1.json` come from the working tree at run time; only `script_prior` is cross-checked | **FAIL (M2)** |
| 13 | clock cannot be gamed | read `cmd_run`, `look_state` | n/a | CLI has no date flag (tested), but `cmd_run(clock=...)` is injectable and the backstop branch opens on whatever exists | **FAIL (M3)** |
| 14 | prior 2026 script-share aggregate | `grep -rIl -i -e blowout -e shootout -e game_script -e "realised script"` and `-e "script share" -e "realised share" -e "blowout rate"` over `reports/ research/ docs/ nba/ app/fixtures` of the main checkout and the `agent/market-game-scripts` tree; `find ... -newermt 2026-10-20` | n/a | none on 2026. Hits are 2023-24 (`combo_eval`, contracts), the IN_GAME_LINE results (2019-2024, in-game `blowout` state), and a research feature. `reports/prereg_game_scripts_forward/` does not exist in the main checkout | pass (main session's belief holds) |
| 15 | lint / types / tests on the branch tree | `ruff check` (exit 0), `ruff format --check` (exit 0), `mypy` on the two modules (exit 0), `pytest -q` on the two test files: 45 passed, exit 0 | n/a | harness coverage 62%: lines 560-757 and 840-965 (`load_scores` body, `as_of_mu_t` body, `s_legs`, `leg_outcomes`, `scored_frame`, `_score_window`, `cmd_run`, `cmd_run_s`, `score_legs`) unexercised | pass, with M4 |

Shuffled-labels, knockout and replication checks do not apply (no result exists; nothing is fitted). Integer support: the
outcome thresholds and P(script) are on the same integer support (`ceil`/`floor`, +-0.5), verified in code and by the
closed-form test.

## Power (re-derived)
Half-width at 750 = 1.96 x sd x sqrt(deff) / sqrt(750): blowout 1.96 x 0.471 x 1.030 / 27.39 = 0.0347 (rule: 0.035); P(CALIBRATED
| gap 0) = 2 Phi((0.05 - 0.0347) / 0.0177) - 1 = 0.61 (rule: 0.62). The gate's table reproduces. Family sizes as declared (P m=4
on MISCALIBRATED, D m=2, S m=4 on S-i); CALIBRATED as intersection-union per script without BH is correct. D is underpowered
by design (MDE about -0.008 / -0.0037 against 2023-24 effects of -0.003 / -0.002), and the rule says NOT_SHOWN is not "no
information". Fine.

## Must-fix (smallest fix each)

**B1 (blocker): the scored `mu_t` is not the app's `mu_t`.** Rule lines 18 (H-P "the combo payload shows"), 30-32 (rule line 2),
116-118 (4.4 "exactly as the number the app shows does"); harness `as_of_mu_t` lines 575-590. Evidence above (check 10). This needs
Devin (O3 was chosen on the premise that there is one live engine). Two smallest fixes, pick one before the freeze:
- (a, recommended) *Score the app's number.* Rule line 2: "`f` from final games of season 2026 dated before the slate (the
  game-scripts export, `combo_stage.history_rows(..., LIVE_SEASON)`), game parameters rounded to 2 dp as
  `build_game_payload` does". Harness: add `AND season = 2026` to the `hist` query (line 584) and round `mm, mt, sd_m, sd_t`
  to 2 dp before `priors`/`closed_form`/`realised` in `scored_frame` (lines 735-740). Freeze only after `agent/market-game-scripts`
  is merged, and pin its `combo_stage.py` sha. Bonus: no 2025 score enters at all; section 4.4 becomes "Season 2025 is never read".
  Restate the shootout/grind priors (still pre-result).
- (b) *Keep the parlay engine's number.* Rewrite H-P and 4.4: "the parlay engine's P(script) (`nba/parlay/slate.py`); the app's
  game-scripts payload builds `mu_t` from live-season games only and is NOT what this rule tests; a CALIBRATED word does not
  license dropping `exploratory` in the app" and strike that consequence from section 9. This makes the rule mostly unable to
  do what Devin asked for (piece 4), which is why (a) is recommended.

**M2 (major): the look does not run the frozen code.** Rule line 2 says "recomputed by code pinned at the freeze commit"; the
harness imports `nba.parlay.game_model` (580), `nba.parlay.combo` (150, 659, 683, 951), `nba.export.build/line_cal_fields/reader/
rung_cal_fields` (595-596, private functions) and `line_cal_map_v1.json` (via `lc.load_map`) from whatever tree runs it four months
later. `nba/export` is a live, actively edited path (the 02:47 pairing fix, and `agent/market-game-scripts` edits `build.py`).
Fix: in `cmd_run` and `cmd_run_s`, before any database read, refuse unless
`git -C repo diff --quiet <freeze commit> HEAD -- nba/parlay nba/export research/eval/game_scripts_forward_eval.py` (or document
"run from `git worktree add <dir> <freeze commit>` with `--data-root` pointing at the live data"); add
`nba/parlay/line_cal_map_v1.json` to `_check_pins`. Rule line 2: append "the run executes that code (`run` refuses on any diff)".

**M3 (major): the freeze commit is set after the freeze and never verified; the clock is injectable.**
(i) Section 10 sets `FREEZE_COMMIT` "right after" the freeze, so the harness that runs is not the harness pinned, and
`verify_rule` (104-113) never checks that `FREEZE_COMMIT` carries `RULE_SHA256` or is an ancestor of HEAD (a rebase changes the
sha and committer date, which defines the window start). Fix: delete the constant; derive the commit as the unique
`git log --format=%H --grep "^prereg(GAME_SCRIPTS_FORWARD): $RULE_SHA256"`, require `git merge-base --is-ancestor <c> HEAD`, and
require `git show <c>:docs/prereg/GAME_SCRIPTS_FORWARD.md` to hash to `RULE_SHA256`. Rule section 10: replace the FREEZE_COMMIT
sentence accordingly. Then M2's diff is empty by construction.
(ii) `cmd_run(..., clock=)` / `cmd_run_s(..., clock=)` (840-841, 897-898) accept any clock, and `look_state`'s backstop branch
(518-521) opens with whatever finals exist when `today > BACKSTOP`; the settle loop (535-541) only covers games already in
`games`, which holds dates up to today (verified: max 2026-11-10). A fake clock in February opens the look at D = 2027-03-28 on
about 600 games. Fix: remove the `clock` parameter (tests monkeypatch `_today_et`), and in `look_state` return not-open unless
`rows["et_date"].max() >= d_look` and `now_utc.date() > d_look`; write `datetime.now(UTC)` into the results.

**M4 (major): the one-shot run path is untested, and run-s reads outcomes before its label-free step.** Coverage 62% (check 15).
`cmd_run_s` calls `load_scores` (927) before `s_legs` (933), which goes through private export functions: a crash there leaves
outcomes read, and rule 6 says "a run that read an outcome is final", i.e. S is lost. Fix: move the `s_legs(...)` call above
`load_scores` in `cmd_run_s`; add tests: (1) `cmd_run` end to end on a temp DB plus a temp git repo with a frozen rule (the
scratch `a2_e2e.py` shows the shape), asserting the results file and that a second call refuses; (2) planted future: a game dated
after every slate with total 800 leaves every `mu_t` unchanged (0.0 measured); (3) the INTEGRITY_FAIL path (one stamp with
`made_at >= tipoff`); (4) `cmd_run_s` ordering (`s_legs` raising leaves `load_scores` uncalled).

**M5 (major for S, small): S scores legs the app never shows conditionally.** The served `best_picks`
(`nba/parlay/game_scripts.py` on `agent/market-game-scripts`, the loop at about lines 196-206) drops legs above the tested range and
legs with P(leg | s) > 0.75; rule 4.3 counts them inside S. The served `p_leg` comes from `calibrate_surv_banded`, the rule's U
from `line_cal` `p_over_cal`; I did not verify they are equal at the main line. Fix (rule line 8): "legs with
`C.leg_above_tested_range` or C_s > 0.75 are excluded from the S words and printed as the slice", and either add a test that
`p_over_cal` equals the served `p_leg` at the main line or say S tests the `line_cal` conditional, not the app's ranked list.

## Minor
- m1 `_score_window` line 886: the INTEGRITY_FAIL `printed` block carries `word` keys; rule 4.1 says "no word". Strip them.
- m2 `WIN_VERSION = "v2"` (line 79) is "the version at the draft"; rule 4.1 says "in force at the freeze", and "the other segment
  is printed" is not implemented. Assert at the freeze; print the other segment.
- m3 Payload rounding (check 11): resolved by B1(a); under B1(b) say the app's number may differ on about 1% of games.
- m4 The one-look guard is a file in one checkout (`results.json exists`); a second worktree has none. Also refuse if
  `git log --all -- reports/prereg_game_scripts_forward/results.json` is non-empty.
- m5 Rule line 6's `unplayed_or_postponed` listing is not written to the results; add the pending/unplayed ids.
- m6 Gate text says lead "55.0 to 115.0 min"; through the harness rows it is 54 to 114 (truncated minutes). Cosmetic.

## Not verified
- That the served `p_leg` equals `line_cal` `p_over_cal` at the main line (M5).
- That `facts.tips_at` is the real tip and not a proxy (taken from the project's prior audits).
- `made_at` is producer-written; no independent write-time audit exists, so a back-dated stamp cannot be ruled out here.
- `s_legs` on the real data (85 s; the modeler's 1,543 legs on 69 games was not re-run).
- `agent/market-game-scripts` is unmerged and may change; B1 is measured against `529a39ef`.

## Biggest risk if frozen as is
A CALIBRATED (or MISCALIBRATED) word for shootout and grind about a number the app does not show, used to drop or keep the app's
`exploratory` label: the definition gap alone moves the realised shootout share by about 0.018 on the 2024 analogue.

## Drafted rows (not appended)
- DECISIONS (needs Devin): "2026-11-10 GAME_SCRIPTS_FORWARD O3 re-opened before freeze: the app's game-scripts payload builds
  `mu_t` from live-season games only, the parlay engine from all history; the rule scores <(a) the app's number / (b) the engine's
  number>. Why: adversary pre-freeze B1 (2024 analogue: labels differ on 49 of 776 games). Reverses if the two engines are unified."
- No ledger or holdout row: nothing was scored and no 2025 row was evaluated.

Scratch (not tracked): `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a234aace-5d9c-4658-9e4a-3b1d144ee734/scratchpad/attacks/a1_window.py`,
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a234aace-5d9c-4658-9e4a-3b1d144ee734/scratchpad/attacks/a2_e2e.py`, `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a234aace-5d9c-4658-9e4a-3b1d144ee734/scratchpad/attacks/a3_mu_t_source.py`.
