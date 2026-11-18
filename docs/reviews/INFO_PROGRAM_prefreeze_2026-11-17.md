# Pre-freeze review: INFO_PROGRAM_2026_27 (umbrella draft), 2026-11-17

Verdict: NOT_READY

(In the checklist vocabulary this is BLOCKED: do not freeze, and do not run stage 1, until the five blockers below are fixed.)

Reviewer: adversary, branch `agent/adversary-info-program` (worktree from `agent/modeler-info-program` tip ebd93255). Draft
reviewed: `docs/prereg/INFO_PROGRAM_2026_27.md`. The pre-freeze hash is confirmed:
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INFO_PROGRAM_2026_27.md | shasum -a 256` gives `b832510a...`, exit 0.
I also read the eight intake notes, the program report `docs/reviews/info_program_2026-11-17.md`, CLAUDE.md, BEST_PRACTICES,
MODEL_PLAYBOOK, HOLDOUT_ACCESS_LOG (its binding definition, the 2026-10-08 "Burned" row and the rows since), DECISIONS 2026-11-17 19:26
and 19:43, and ledger rows T238/T239/T243, T389, T414, T451, T454, T476, T501, T502, T504, T510, T512, T517 and T521. I also checked the
LINE_CAL_LIVE lock and REPORTER_FEED_LIVE's `B_ret` (on `agent/modeler-reporter-feed`).

What I did not do: no fit, no outcome read, no DuckDB opened, no season 2025, no network. All my numbers are arithmetic on the draft's
own inputs (script: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/gates.py`,
run with `python3 -I`, exit 0). I computed the CRPS gain from removing a bias b exactly for Normal(sd 7.25) outcomes. It reproduces the
draft's own −0.42 min at b = 3.3, so my numbers sit on the same model the draft uses.

Standing context from Devin (relayed by the coordinator tonight): season 2025 stays the frozen holdout, with no rollover and no release
into backtests, and Devin is told about each stage-2 touch before it runs. The stage-2 recommendation at the end takes this into account.

Model classes: PROP_LINE_INPUT is a gradient-boosted residual with a market input. RETURN_RAMP is a fixed distribution layer on
production's minutes. KALSHI_NEWS_MOVES is a fixed P(play) adjustment. REF_FOUL_MINUTES and SEASON_STAKES are boosted residuals that are
not freezable.

---

## Blockers

### B1. RETURN_RAMP's gate is computed against production, but its primary is scored against production plus a constant shift. Against its own comparator, the member fails its gate.

* **Where:** umbrella §5, lines 105-107 (the "free information-equivalent baseline"), and §9b, line 174 (n 875, h 0.110, power 0.71,
  e_plan −0.14). Also `info_04` "Draft rule summary" (primary = "arm minus production-plus-constant-shift") and "Gate".
* **The problem:** The oracle −0.24 min (−0.42 on the first game, about −0.12 on games 2-3) and the per-row sd 1.35 both measure the gain
  from correcting **uncorrected** production. Neither applies to the declared primary contrast. The draft's own sd is visible in this:
  0.577 × 3.3 = 1.90 matches its 1.88. The constant shift already captures most of that gain. What is left for the ramp is (i) the gap
  between game-1 and games-2-3 bias and (ii) any spread of first-game bias across absence bands.
* **My recomputation:**
  * Comparator = a pooled constant fitted on returner games 1-3, which is what §5 describes. Across ramp factor 1.67/2.5/3.33,
    games-2-3 bias 1.17/1.75/2.33 min, and spread across absence bands of 0 or 1.5 min: oracle 0.008-0.095 min, e_plan (60%)
    0.005-0.057 min, power 0.00-0.17.
  * Comparator = REPORTER's `B_ret` (−3.3 min applied to every returner row): oracle 0.015-0.150, e_plan 0.009-0.090, power 0.00-0.40.
  * **No scenario reaches the −0.10 min floor.** The ramp is not freezable as declared.
* **The draft's own figures, against production (the brief's "wrong by a third" question):**
  * Ramp 2.5 with games-2-3 bias 1.5-2.0 reproduces power 0.68-0.71.
  * Ramp 1.67 (a third low): n 583, power 0.56-0.65.
  * Ramp 3.33 with games-2-3 bias a third low: power 0.48, below 0.5.
  * So even against production the gate holds only if the assumptions are roughly right.
* **The comparator is also undefined.** §5 says "a constant returner shift fitted on the same fit seasons". `info_04` says `B_ret`
  "refitted on 2013-2022". REPORTER defines `B_ret` as −3.3 min from 696 first-game rows. Which one is chosen changes e_plan by about 3x.
* **A deeper issue.** Absence length and games since return come from box scores production already reads (`gap_days`, `since_flag`,
  `flag_dir`, `since_team`). This member mostly repairs how production uses information it already has, a capacity or calibration
  question (playbook question 1). Only the report-reason column is new information.
* **Fix.** Pick one option before the freeze:
  * (a) **Recommended.** In §9b line 174, replace the Result cell with:
    "**NOT FREEZABLE as declared**: against production plus a pooled returner constant (the §5 comparator) the ramp's oracle is
    0.01-0.10 min and e_plan 0.005-0.06 min, below the −0.10 floor in every scenario of the adversary's grid (ramp 1.67-3.33 games,
    games-2-3 bias 1.2-2.3 min, absence-band spread 0-1.5 min). What would change it: a label-free count plus a 2022 descriptive showing
    a first-game bias spread across absence bands of at least 2.5 min (oracle about 0.10 vs the constant)."
  * (b) Make the primary "arm minus production as served". Then add a required word rule to §5:
    "RETURN_RAMP's word is PASS_RETURNER only; the constant-shift comparison is reported and the arm is never described as a ramp
    improvement unless arm minus (production + c) has CI upper < 0. c = one scalar, the mean of (y − production mu) over played returner
    games 1-3 of the fit window, fitted once." Option (b) admits the claim is "a returner correction beats production", which is
    calibration. If chosen, move the member to the calibration program's family, or state in §2 why it stays here.

### B2. The stage-2 venue the draft relies on (a 2025 touch) is defined by the binding holdout log as non-confirmatory. The draft freezes it as "the claim" while marking the question open.

* **Where:** umbrella §2, lines 30-33; §4, lines 79-88 (lines 85-88 are the "open question"); line 84 sets the default venue.
* **The conflict:** HOLDOUT_ACCESS_LOG, "Status as of 2026-10-08: NO VIRGIN HOLDOUT", says: "Treat every `season=2025`-touching result
  below as repeated-use until rollover to 2026". DECISIONS 2026-11-17 19:26 cancelled the rollover, so under the binding text every 2025
  result is "repeated-use holdout, informal cross-check" for good. Rule 1 of the same log ("clean only if the model has never been
  evaluated on 2025") allows a clean result for a new model. The two texts disagree, and the umbrella does not resolve them.
* **What could go wrong:** a member passes on 2025 and is called "the claim". Someone later reads the log, and the result is downgraded
  after the fact, or the other way round.
* **"Exactly once" is not enforced.** The 2025 box rows and `odds_history.duckdb` 2025 props sit on disk. Nothing in the rule stops a
  second run.
* **What the model is frozen with is unstated.** Line 79 and the report's row template say "no refit after". They do not say what is
  fit before the touch, or what the comparator is trained on. Live production has been trained on 2025 labels (HOLDOUT_ACCESS_LOG rows
  72, 80, 188).
* **Fix.** Replace §4 lines 79-88, the "Stage 2 (a)" bullet and the "Open question" bullet, with:
  > (a) one logged touch of season 2025 per member. Before the umbrella freezes, Devin records in DECISIONS (and the archivist appends to
  > HOLDOUT_ACCESS_LOG as a definition note): "For a model never evaluated on season 2025, whose arm and comparator are both fit on
  > season <= 2024 rows only, one pre-logged touch is a confirmation clean for that model; the project-wide status of 2025 stays
  > repeated-use." Without that row, a 2025 result is an informal cross-check and the member's F-INFO primary is the forward look.
  > Each touch: the member rule's sha, the harness commit sha, the exact command, the output path and the primary are written in the
  > log row, and Devin is told, before it runs. Arm and production are both fit once on seasons <= 2024 (no refit, no monthly block,
  > no recalibration inside 2025; no 2025 label is ever a training row of either). The harness refuses to run if the output path exists
  > and writes a sha of its output; the result is read once by the maintainer; a rerun for any reason is a new id and is not a claim.
  > A pass is worded "clean for this never-evaluated model on a project-wide repeated-use season".

### B3. BH's m is not fixed before a stage-2 p-value can exist; the early word and the late-freeze clause can be gamed.

* **Where:** umbrella §2, lines 35-45.
* **Timing:** m is "the number frozen by 2027-01-15", written "the day after". The order of work (report step 4) allows a 2025 touch
  about a week after stage 1, well before 2027-01-15. A p-value can then exist while m is still unknown, so "p <= 0.05/m" (line 43)
  cannot be evaluated.
* **Membership can be gamed:** whoever sees that p can decide whether to freeze more members, which changes the BH thresholds. Line 38
  ("never by a stage-2 outcome") has no mechanism behind it.
* **Escape hatch:** line 39, "A member frozen after 2027-01-15 is a new family", lets a candidate wait and be tested alone at 0.05.
* **Fix.** Replace lines 35-39 with:
  > Family F-INFO has m = 3 fixed now: PROP_LINE_INPUT, RETURN_RAMP (if it is re-gated and frozen; else p = 1), KALSHI_NEWS_MOVES.
  > REF_FOUL_MINUTES and SEASON_STAKES are declared not freezable and are not members. A member that has not frozen its stage 2 by
  > 2027-01-15, fails stage 1, fails its gate, or never reaches a valid look enters BH with p = 1. A candidate of this list frozen after
  > 2027-01-15 is not a new family: it stays in F-INFO with p = 1 and its result is descriptive.
* **Fix.** Replace line 43 with:
  > A member whose stage-2 p <= 0.05/3 = 0.0167 and which clears its floor gets its word at its efficacy look (never at a kill-only
  > interim).
* **Fix.** Add to line 42:
  > p is computed by `nba/truth/bootstrap.py` with B = 2,000, seed recorded, resampling the binding (widest) cluster of section 8.5;
  > a pass needs BH rejection AND point <= floor.

### B4. "Production as served" is undefined for every stage-1 and 2025 row, and is not pinned to a commit or model id.

* **Where:** umbrella §5, lines 96-99.
* **The problem:** The only definition is live tables (`props_context_residual` with `made_at`). No served rows exist for 2023, 2024 or
  2025. The two harnesses being built now will each invent their own comparator.
* **The 2025 touch:** live production has been trained on 2025 labels, so it cannot serve as the 2025 comparator.
* **RETURN_RAMP:** production's history minutes are stored only for 2022-2024 (`info_04` data audit). A ramp "fitted on 2013-2022
  rows" relative to production's residual cannot be built without the unrun one-day recipe run.
* **Fix.** Insert after line 99:
  > - Stage 1 and the 2025 touch: "production" is the production recipe at commit `<sha>` (props: model id `<props_context_residual
  >   id>`, frame pin `<hash>`; minutes and P(play): `<id>`), monthly-block walk-forward OOF on 2023 and 2024 (stored arrays named by path
  >   and sha in the member rule; RETURN_RAMP uses `reports/minutes_v2/minutes_scores.npz` only if the member rule shows it is that recipe),
  >   and for 2025 the same recipe fit once on seasons <= 2024. Fit windows of the arm are limited to seasons where this comparator
  >   exists unless the recipe run on history is done and pinned first.
  > - Forward: each row carries `model_name` and code sha; if production changes inside the window, the look is computed on the
  >   pre-change and post-change rows separately and the binding half is the worse one.

### B5. The stage-1 pass condition is not tight enough to build a harness against. The PROP_LINE_INPUT note defers its own rule until after the select stage.

* **Where:** umbrella §2, line 29 ("fit or select"); §4, lines 69-72; line 83 (venue named before stage 1); `info_03` line 60 ("member rule
  to be written after the select stage") and line 66 ("Pinnacle, then consensus").
* **RETURN_RAMP:** its note gives three fit windows (2013-2022; 2013-2023; "minimal" 2023 → 2024).
* **PROP_LINE_INPUT:** it has two line sources and an unstated LightGBM configuration.
* **2023 sign:** "same sign on 2023" is undefined when 2023 is the fit season.
* **Venue lock:** line 83 says the venue is named before stage 1, but PROP_LINE_INPUT's venue is "CONDITIONAL on stage 1". Nothing
  stops switching venue after stage 1 shows which gate passes.
* **Effect on stage 2:** a search inside stage 1 does not by itself inflate a stage-2 error rate on independent 2025 rows. It does turn
  2024 from a report into a selection set, and it inflates e_plan (see M3).
* **Fix.** Replace §4 line 69 to line 72 ("Fit / select (stage 1)") with:
  > Stage 1: the member rule, frozen before stage 1 runs, fixes the exact feature list, model form and hyperparameters, fit window, line
  > source (one), forecast time, population and clustering. No search. 2023 is scored by monthly-block walk-forward OOF inside 2023 (the
  > "same sign" season); 2024 is scored once with the model fit on everything before each 2024 block. Stage-1 pass = 2024 point <= floor,
  > 2024 CI upper < 0, 2023 OOF point < 0, every guard of sections 7-8. The F-INFO venue named in the member rule cannot change after
  > stage 1; if its gate fails at stage 1 the member leaves the family (p = 1).
* **Fix.** In `info_03` line 60, delete "(member rule to be written after the select stage)". Pick Pinnacle or consensus now:
  consensus has more rows (17,993 vs 13,021), and Pinnacle is the sharper input.

---

## Majors

### M1. PROP_LINE_INPUT's backtest and 2025 comparator lack information production has live. "Flattering risk low" is wrong.

* **Where:** umbrella §9a, line 163.
* **The problem:** T502 states that the history T-60 is paired with an injury report "2-5 h older" (two stamps a day in 2022-24). The
  T-60 line carries news up to T-60, and live production has 15-minute reports. The line would be credited for news production sees
  live. The note itself says the mechanism is "mostly minutes (the line knows the injury news ... we lack)".
* **Fix.** Line 163 Flattering risk → "moderate: history production's report is 2-5 h older than the line (T502)". Add to §7:
  > PROP_LINE_INPUT guard: the primary is also computed on line rows of players on no report stamp of that day with stamp <= tip − 60
  > (healthy rows); a pass needs that slice's point < 0 with CI upper < 0, else the word is PASS_REPORT_LAG (no claim).

### M2. The known-at rule cannot be met in history as written.

* **Where:** umbrella §7, lines 139-140.
* **The problem:** The rule says known-at is "our receive clock, never the source's created_at". There is no receive clock for the
  bought the-odds-api snapshots or for the 2022-24 report files. The rule would void every history row or be waived without notice.
* **Fix.** Append:
  > In history: line known_at = the-odds-api snapshot timestamp (never the nominal `t60` label), admissible iff <= real tip (from
  > `games`/`game_tipoff`) − 60 min per row; report known_at = filename stamp + 30 min (INJURY_TIMING rev 3). The harness prints the
  > lead distribution (T502: min 62.6 min) and the violation count; > 0.1% voids the look.

### M3. PROP_LINE_INPUT's conditional gate uses a stage-1 effect that is inflated by selection, so the gate's power is overstated.

* **Where:** umbrella §9b, line 173.
* **Why the stage-2 estimate itself is unaffected:** 2025 is an independent season and is touched once, so conditioning eligibility on
  the 2024 effect does not bias the stage-2 estimate and needs no BH correction.
* **Why the gate is affected:** using the stage-1 point itself as e_plan is inflated by selection. At 0.6 × the stage-1 point, with the
  draft's k = 0.521:
  * e = −0.009 gives power 0.22 (n 13,021) to 0.28 (n 17,993).
  * e = −0.012 gives power 0.35 to 0.46.
* **Fix.** Line 173 condition → "freezable for the 2025 touch iff 0.6 × the 2024 point <= −max(0.005, h_2025), i.e. a 2024 point <=
  −0.0149 (Pinnacle, n 13,021) or −0.0127 (consensus, n 17,993)". Section 6 item 3 adds: "a stage-1 effect enters e_plan at 0.6 ×".
  Also note that k comes from a date-clustered T-15 SE. Under "the widest binds" (§8.5), h may be larger. The member rule recomputes h
  from a label-free noise arm (the T504 method).

### M4. REF_FOUL_MINUTES: the ceiling arithmetic is linear where CRPS is quadratic. The stated reason for failing and the reopen threshold are both wrong.

* **Where:** umbrella §9b, line 176; `info_06` "Gate" and "What would change the verdict".
* **The ceiling:** translated the way the draft translates RETURN_RAMP (gain ≈ 0.282 b²/σ), the REFEREES bound of 0.027 pts on
  foul-prone rows implies a minutes ceiling of 0.11-0.18 min (σ_pts 6-8, σ_min 6-7.25). That is **above** the 0.10 floor, not 0.06 below
  it. At 60%, e_plan is 0.065-0.105, so not freezable is probably still right. But "effect ceiling below the floor; not a data failure"
  is not shown.
* **The reopen threshold:** a top-minus-bottom tercile gap of 0.6 min is worth about 0.003 min of CRPS. Reaching 0.10 needs a gap of
  about 3.6-3.9 min. The threshold as written would reopen a rule that cannot pass.
* **Fix.** Line 176 Result → "**NO**: e_plan 0.06-0.10 min (60% of a 0.11-0.18 ceiling, quadratic translation of T238-T239) sits at
  or below the floor; power < 0.5". In `info_06`, change "at least 0.6 min" to "at least 3.5 min".

### M5. KALSHI_NEWS_MOVES: the baseline fallback lets the arm take credit for report news, and Stage 0 is neither specified nor label-free.

* **Where:** umbrella §5, lines 98-99; §9b, line 175; `info_08` "Missing and cost" and "Gate".
* **The fallback:** on games where the T-15 arm did not log (14%), the baseline falls back to T-60. A move between T-60 and T-15 that
  the T-15 report also carries (for example Probable → Questionable) halves p_play, and the arm is credited for the report.
* **Fix.** Add to §5:
  > KALSHI_NEWS_MOVES is scored only on rows whose baseline is the T-15 arm or a LATE_REPORT row made >= tip − 20 min; T-60-fallback
  > rows are excluded and counted.
* **Stage 0's pass rule is unwritten.**
* **Fix.** Add to line 175:
  > Stage 0 projects a = moves preceding an OUT first posted after T-15 per game, f = all other flagged players per game (counted as
  > false alarms), e = (1.7a − 0.7f)/26, h from the per-row sd sqrt((1.7² a + 0.7² f)/26) x sqrt(1.2/n); freezable iff
  > Phi((|e| − max(0.005, h))/(h/1.96)) >= 0.5.
  The implied sd is 0.09-0.12, not 0.25, so the floor binds, not h. About 61-94 flagged scratches would carry the whole season.
* **"Label-free" is overstated:** an OUT first posted after T-15 is close to the P(play) label. This is acceptable only because Stage 0
  nights are excluded.
* **Fix.** State: "Stage 0 nights are never stage-2 rows; thresholds 6c / 3 markets / 0.5 are frozen in the umbrella text now."
  They currently sit only in the note and in Devin's choice 6.
* **Mechanism doubt (one sentence for the note):** a withdrawal settles at the last fair price, so traders have little reason to trade
  on scratch news. Minutes-restriction news, not scratches, may be what moves the price, and the P(play) primary would miss it.

### M6. The single-group flag and the missingness audit are not pass/fail as written, and the line column fails the 2 pp rule by construction.

* **Where:** umbrella §7, lines 132-138.
* **Single-group flag:** "held ... until a knockout and a planted-future test clear it" has no clearing criterion.
* **Line missingness:** books pull lines on scratches and price higher-minute players, so line presence (and "books agreeing") differs
  by played and by minutes bucket by far more than 2 pp. It is legitimate pre-tip information, but only if it is timestamped.
* **Fix.** Replace lines 133-135 with:
  > ... held as a suspected leak; it is cleared only if (i) the +24 h known-at shift returns production bit for bit on every affected
  > row, (ii) a planted future value is refused on 100% of planted rows, (iii) a positive control of floor size is detected; otherwise the
  > member's word is LEAK_SUSPECT and nothing is claimed.
* **Fix.** Append to line 138:
  > For PROP_LINE_INPUT the line-present and book-count columns are expected to fail the 2 pp test; they pass only if every one is
  > computed from snapshots with known_at <= tip − 60 and the planted-dependence control is detected.

### M7. RETURN_RAMP's stage 1 is not blind, and its "label-free" count is not label-free.

* **Where:** umbrella §9a, line 164; §9b, line 174 ("counted label-free on 2022-24").
* **Stage 1 is a re-reading:** the 2023-24 bias, including the K >= 10 split (−6.45 / −4.86), was measured before this design existed
  (REPORTER note §7B). PPLAY_MINUTES_VALID also slices returners on these seasons. The absence-band feature was chosen after seeing
  that split, so a 2024 stage-1 pass is a re-reading, not a selection result.
* **The count needs labels:** the games-2-3 bias is y − mu and needs outcomes. "Played returner rows" conditions on playing.
* **Fix.** Line 174 → "the ramp factor is counted label-free on 2022-24 as rows eligible pre-tip (player-team games following a
  >= 3-game team absence, prior-10 >= 15 min), without the played filter; the games-2-3 bias is an outcome read on 2022 only (a
  planning descriptive under §6.3, not label-free)". Line 164 → "2023-24 already seen; stage 1 is not informative for selection;
  the 2025 or forward stage 2 is the only evidence."

---

## Minors

* **m1. Too-good bound.** §7 line 130: RETURN_RAMP's too-good bound is stated for first-game rows, while the primary is games 1-3. State
  it on the primary population and against the primary comparator (vs production: −0.24 oracle; vs the constant: ≈ −0.10).
* **m2. Forward start for RETURN_RAMP.** §9b line 174: the forward look cannot start until production's minutes sigma is logged live
  (`info_04`). Forward n counts from the steward's logging start (the replay oracle and Devin's go), not from the freeze.
* **m3. Kill-only interim.** §4 line 81: the kill-only interim must state "no efficacy reading, no early word".
* **m4. Wording.** §5 line 104: add "no summary, card or report may describe a PROP_LINE_INPUT pass as beating, matching or
  approaching the market; the arm contains the market's number (T517)".
* **m5. Refit for the touch.** Playbook (trees, data appetite): stage 1 fits on one season of lines. Say that the 2025-touch arm is refit
  on 2023-2024 under the same frozen recipe, and that a 2023-only fit failure is weak evidence (playbook note).
* **m6. Priors disagree.** The report's prior for RETURN_RAMP (freeze 0.70, claim 0.30) differs from `info_04` (0.60, live pass 0.25).
  Pick one before the PREDICTIONS_LEDGER rows are appended.
* **m7. SEASON_STAKES reason.** §9b line 177: the failure is e_plan (production's calibration on stake rows has never been measured),
  not only h. Add: "pooling the 2025 touch and the forward rows (about 4,670) still gives h 0.0063 > 0.005". The data audit is
  otherwise adequate (history 2019-21 listed with its cost).
* **m8. Unfrozen fallback.** §5 line 99: the LATE_REPORT fallback row comes from a rule (LATE_REPORT_FORWARD) with no file and no
  freeze. Name the logging code commit instead.
* **m9. Status line.** The status line should also cite the adversary pre-freeze review path once it is applied.

## Checks

| Check | Command / source | n | Result | Pass/fail |
|---|---|---|---|---|
| Freeze hash | `awk ... | shasum -a 256` (exit 0) | 1 file | b832510a matches the draft | pass |
| RETURN_RAMP gate vs production (draft) | gates.py | 875 (projected) | h 0.110, power 0.707 reproduced; 0.48-0.85 across ±1/3 | conditional |
| RETURN_RAMP gate vs declared comparator | gates.py | 583-1,167 | power 0.00-0.40 in 36 scenarios | **fail** |
| PROP_LINE_INPUT h | gates.py | 13,021 / 17,993 / 4,380 / 10,920 | 0.0089 / 0.0076 / 0.0154 / 0.0098 reproduced | pass (arithmetic) |
| PROP_LINE_INPUT gate with selection-inflated e_plan | gates.py | as above | power at 0.6e 0.22-0.46 | fail as written |
| KALSHI arithmetic | gates.py | 20,700 | 0.08 / 0.12 a game reproduced; implied sd 0.09-0.12 | pass |
| REF ceiling | gates.py | n/a | 0.11-0.18 min, not 0.06 | fail (reason wrong) |
| SEASON_STAKES h | gates.py | 2,170 / 2,500 / 15,000 | 0.0092 / 0.0086 / 0.0035 reproduced | pass |
| Holdout status | HOLDOUT_ACCESS_LOG lines 31-36, row 42; DECISIONS 19:26 | n/a | binding text calls 2025 repeated-use | **fail** (B2) |
| Closed ideas not reopened | T389-T414/T501, T451-T454, T521, T238-T243, T512, T517 | n/a | spread/total, rest/travel, b2b and the mover cold start are not re-proposed; PROP_LINE_INPUT states its difference from T517/H2 (whole-distribution CRPS vs the line), acceptable as a new construction with the same information | pass |
| LINE_CAL_LIVE lock | umbrella lines 89-91 | n/a | consistent; Stage 0 reads report changes only | pass |

No result numbers are claimed, so no family correction applies to this review. Power and minimum detectable effect are covered above.

## Playbook check

1. **Information or capacity?** PROP_LINE_INPUT adds information, but it is the market's, so it is a prior and not skill.
   RETURN_RAMP is mostly capacity or calibration on box features production already reads; only the reason column is new.
   KALSHI_NEWS_MOVES is information, if prices lead the report.
2. **Enough data for the class?** PROP_LINE_INPUT trains trees on one season in stage 1 (the playbook calls a 2023-only fail weak
   evidence). RETURN_RAMP is a few coefficients and has enough rows. KALSHI_NEWS_MOVES has nothing fitted.
3. **Test validity?** Not yet valid: the comparator is not pinned (B4), the history known-at rule cannot be met (M2), the history
   comparator for the line arm lacks information (M1), and RETURN_RAMP's power is computed against the wrong comparator (B1).
4. **Lineage?** Stated correctly. The guards carried are placebo information, the integer grid, the shuffle and the missingness audit.
5. **Scored against?** CRPS and log loss against production, plus the market at the line where the input is a market number. This is
   correctly separated.

## In order: before the freeze

1. The modeler applies B1-B5 and M1-M7 text in the draft. I re-read the revision for new contradictions, not only for these fixes.
2. Devin decides B2: the DECISIONS row that makes a never-evaluated model's single 2025 touch a model-clean confirmation, or that does
   not. The archivist appends the definition note to HOLDOUT_ACCESS_LOG. A main-session hand.
3. The member rules for PROP_LINE_INPUT (and RETURN_RAMP, only if re-gated) are written with everything B5 lists, and frozen before any
   harness reads an outcome. The harness lane should treat §5, §4 stage 1 and §7 as changing, and should not hard-code the comparator,
   the line source, the known-at rule or the fit window until the revised text lands.

## In order: before the first stage-1 run

1. **RETURN_RAMP label-free count.** The modeler writes the script and the maintainer runs it. It must contain
   `duckdb.connect(<path>, read_only=True)` and `season BETWEEN 2022 AND 2024` (never 2025). It outputs the eligible rows for games
   since return 1, 2 and 3 per season, with no played filter and no minutes column read for the scored game. The games-2-3 bias is a
   separate 2022-only outcome read on `reports/minutes_v2/minutes_scores.npz` and is labelled as one. I cannot give the exact command:
   no such script exists in the repo yet.
2. **RETURN_RAMP re-gate.** Re-gate it against the declared comparator with the counts above (B1). If it fails, it is listed NOT
   FREEZABLE with the audit.
3. **PROP_LINE_INPUT.** The archivist pins `odds_history.duckdb` (sha) and the lead distribution of the snapshots for 2023-24 (label-free,
   season <= 2024).
4. **KALSHI Stage 0.** Freeze the thresholds into the umbrella text, then the market agent's `nba/kalshi/prop_history.py` runs on capture
   copies, outside game windows. Stage 0 nights are excluded from stage 2.

## Stage-2 venue recommendation

Devin's standing choice is that 2025 stays frozen with no release into backtests, and he is told before each touch. Given that:

* **PROP_LINE_INPUT.** Use the 2025 touch as the F-INFO primary, but only with the B2 DECISIONS row and the B2 protocol: arm and
  comparator fit on seasons <= 2024 only, one run, a refuse-if-exists harness, and Devin told first. It is the only venue that resolves
  the floor (forward h 0.010-0.015 against 0.008-0.009). It also tests the model question, not serving, and the line arm's design is
  fixed on 2023-24 without any 2025 input.
* **What a 2025 pass would show:** that a model frozen without any 2025 input beats an equally blind production recipe on a season the
  arm never saw.
* **What it would not show:** (i) that the effect survives live, where reports run every 15 minutes (M1); (ii) independence from
  project-wide 2025 knowledge baked into production's design (that knowledge helps the comparator, so it is conservative for the arm);
  (iii) anything about betting (T517).
* **The forward look is a descriptive serving check**, with the kill-only interim.
* **If Devin does not write the B2 row,** invert the design: the forward look is the primary, and PROP_LINE_INPUT is freezable only with
  a 2024 point <= −0.016 to −0.026 (0.6e >= forward h 0.0098-0.0154, by live line density).
* **RETURN_RAMP:** the venue does not matter until B1 is resolved.
* **KALSHI_NEWS_MOVES:** forward only, as drafted.

## What I could not check

* The real 2023-24 returner counts, games-2-3 bias and absence-band spread (no outcome reads allowed). B1's scenarios bracket them.
* Whether `minutes_scores.npz` is production's recipe.
* Whether the 2025 report cadence matches 2024's.
* The snapshot timestamps of the 2023 props.
* LATE_REPORT_FORWARD's text (none exists).
* The calibration program's overlap with RETURN_RAMP (I only grepped it).
* Kalshi capture pages (not opened).
* The market agent's branch review.

## Proposed rows (drafted, not appended)

* **DECISIONS (main session, after Devin):** "2026-11-xx | INFO_PROGRAM_2026_27 pre-freeze review NOT_READY
  (docs/reviews/INFO_PROGRAM_prefreeze_2026-11-17.md): RETURN_RAMP fails its gate against its declared comparator; 2025 touch status
  to be defined; m fixed at 3; comparator pinned. | Adversary review. | A revised draft passes re-review."
* **PREDICTIONS_LEDGER (adversary prior, written before any result):** "2026-11-17 | INFO_PROGRAM RETURN_RAMP | P(re-gated against
  production + constant and found freezable) 0.15."
