# ROLE_SHIFT program: minutes, roles and the coach's decisions (2026-11-02)

Status: PROGRAM NOTE (reading log), drafted by the modeler on branch `agent/modeler-role-shift`. No fit, no outcome-scored candidate number,
season 2025 never loaded. The first pre-registration is `docs/prereg/MINUTES_DECISION.md` (DRAFT). Every other part below gets its own id,
its own minimum-data gate (non-negotiable 8) and its own adversary read before any fit.

**Advice or momentum.** Devin's request (DECISIONS 2026-11-02 20:09, "okay that works"). It follows the ledger's one consistent finding
(minutes are most of the prop error; information beats architecture), and it sits against another (coach habits added nothing in TEAM_BUDGET,
T447; the coach blowout habit was not reliable, T450). Claude's priors are written below and are low for parts 1-3. The program is worth running
because part 1 is cheap (about 45 CPU minutes), it answers a question nobody has asked here (the coach's DECISION, not the player's OUTPUT), and
its pre-declared descriptives decide whether parts 2-4 are worth a rule at all.

## 1. Devin's ideas, in the words we have

Devin's five ideas reached me through the DECISIONS row of 2026-11-02 20:09 (his ideas, refined with Claude; I do not have his verbatim
messages, so the row's wording is quoted):
1. "a coach rotation-DECISION model inside the minutes model (predict minutes/starter status from game state, coach history, both teams' outs,
   opponent lineup size/shooting; props on top; select 2023 / report 2024) - first, biggest lever (minutes error)";
2. "between-game role changepoint vs the recent-role columns (ROLE_CHANGEPOINT, parked 10-23, reopened)";
3. "coach familiarity for movers (games coached WITH + AGAINST; faster role convergence; descriptive first, power stated; T504 cohort caveat)";
4. "situational-mandate substitutions (attempt rate after a cold-team sub; descriptive on 2022-24 play-by-play first)";
5. "in-game role deviation at halftime for the T508 model (scored on the halftime paper log)".

Three additions from Devin (relayed by the coordinator, 2026-11-02 evening), binding on every part:
* **A. The simplest model is the first comparator everywhere**: production A0 (recency average plus context residual) and FULL (A0 plus the
  5 recent-role columns, T467). Every part reports its gain as a paired difference against that simplest baseline with a game-clustered CI,
  plus a knockout that shows what the addition carries beyond it. The question "does the simplest model already have it?" is asked
  explicitly, part by part, in section 3.
* **B. Role stability** as an explicit feature and slice: dispersion of recent minutes and usage (sd over the last 10 played games; the
  changepoint's confidence). Stable-role and unstable-role players are scored separately; hypothesis: the decision model helps mostly for
  unstable roles.
* **C. The pts/reb/ast relationship under role change.** The production copula (`nba/parlay/copula.py`, `estimate_pair_corr`: one shrunk
  correlation per relation and stat pair; T081, T495) is an average. Pre-declared descriptive on 2022-24: does the same-player correlation
  of pts, reb and ast residuals differ by role state (scorer vs playmaker share, minutes up vs down, stable vs unstable)? If it does, a future
  rule tests whether a role-conditioned correlation lowers joint log loss on same-game parlay legs against the static copula. Descriptive only.

## 2. What each idea predicts, and what we already tested

| part | prediction (falsifiable) | new pre-tip information? | already tested here | prior |
|---|---|---|---|---|
| 1 decision model | minutes CRPS of the decision model C beats FULL-minutes F by >= 0.10 min (both seasons); the coach part (C minus everything-but-coach) by >= 0.05 min; the gain is larger for unstable roles | partly: opponent outs, opponent size and 3-point profile, coach template and minutes-vs-margin curve, player-under-coach history. Recency, own outs, rest, rotation tightness and blowout risk are already in M2 | MINUTES_V2 V2 group over M1: -0.021 / -0.003 min (T162-T165 context); TEAM_BUDGET coach habits +0.0002 pts (T447); COACH_PHASE stopped at its gate, blowout habit split-half r -0.07 (T450); coach-to-absorption cluster does not carry across seasons (reports/coach_allocation_2026-10-10.md); MINUTES_HAZARD worse than M2 (T301-T306) | P(H1) 0.07; P(coach part) 0.03; P(props) 0.03 |
| 2 changepoint | after a between-game role change, FULL lags the new role; a detector closes the lag beyond FULL's own recent-role columns | no (same minutes stream) | ROLE_CHANGEPOINT draft 78d0250: 60% of the detector's level gap is explained by A0's columns, 54% of flagged rows already flagged by production's CUSUM, 9% better than a half-life-4 EWMA, P(PASS_MINUTES) 0.03 (DECISIONS 2026-10-23 21:20) | reopen only on D1 (below) |
| 3 familiarity | movers with prior games under / against the new coach converge to their new role faster | yes in principle (the coach has seen him) | T500 movers (FAIL_ONE_SEASON), T504 / T512 stopped at power (movers pts half-width 0.013-0.017 vs 0.005). **Measured today: only 3 of 133 (2023) and 2 of 114 (2024) offseason movers had ANY earlier game under their new coach in the data we hold (2022 onward)**; against-counts are schedule (median 1-2 games) | WITH untestable on held data; D3 on AGAINST only |
| 4 situational subs | a bench shooter who checks in while his team shoots below its season 3P rate takes more threes per minute than his baseline (a mandate), beyond the same player's normal check-ins | in-game state only (not pre-tip) | nothing here; T508 in-game model uses minutes and score, not the reason for a sub | descriptive; counts pass (2,105 / 2,359 cold events) |
| 5 halftime deviation | first-half minutes below / above the decision model's expectation predict the second half beyond T508's state | yes (in-game) | T508: gain over a player-aware baseline is 0.0021-0.0025 log loss (T508-attack), residual edge at minute >= 30; the halftime scorer has logged paper rows since 2026-10-27 | needs part 1's expected-minutes output and the halftime log |
| C copula by role | same-player pts/ast (and pts/reb) residual correlation differs by role state by >= 0.05 | no new data; a structure question | T081 (set transformer ties the copula), T495 (CAL helps the joint), T490 (dependence is a weighting artifact) | P(any pre-tip state with abs(dRho) >= 0.05 both seasons) 0.30 |

## 3. "Does the simplest model already have it?" (addition A, part by part)

* Part 1: the comparator is F = the minutes mixture with production's base features plus the 5 recent-role columns (the minutes analogue of
  FULL), and R (A0's own recency centre). H1 is C minus F. Knockouts C minus each group (coach, familiarity, opponent, stability, V2) say
  what each carries beyond F. Props: PC (FULL + C's minutes columns) minus FULL, and the knockout PC minus PF (FULL + F's minutes columns).
* Part 2: D1 asks first whether F still lags the new role on changepoint-flagged rows. If F's bias on those rows is under 1 minute, the
  simplest model already has it and part 2 stays parked.
* Part 3: the comparator is FULL on the movers' first 10 games (T500/T504 cohort); the familiarity columns are a knockout of C.
* Part 4: the comparator is the same player's own check-ins when his team is at or above its rate (a within-player control), and FULL's
  per-minute 3PA rate.
* Part 5: the comparator is T508's binding baseline glm_e (player-aware); the knockout is T508's M without the deviation term.
* Addition C: the comparator is the static copula (`estimate_pair_corr`) on the same rows.

## 4. Order, and why

1. **Part 1, MINUTES_DECISION (now).** The biggest lever by error share, cheapest to run, and its four pre-declared descriptives (D1 changepoint,
   D2 situational subs, D3 familiarity, D4 copula by role) are the gate evidence for the later parts. Role stability (addition B) is a feature
   group and a pre-declared slice contrast inside it.
2. **Part 4 descriptive (D2, inside part 1's run).** Cheap, the counts already pass; it feeds the in-game model, not the pre-game one.
3. **Part 5, halftime role deviation.** Information (first-half minutes against expectation) is the kind of thing that has worked. It needs
   part 1's expected-minutes output and about 200 halftime paper rows (the scorer started 2026-10-27; roughly early December).
4. **Part 2, ROLE_CHANGEPOINT.** Reopened only if D1 shows FULL lags the new role (trigger in the MINUTES_DECISION rule); otherwise stays
   parked: it is architecture on an input the model already has.
5. **Part 3, familiarity.** Last. The WITH half cannot be tested on the data we hold (5 movers in two seasons); see the data audit. D3 runs on
   the AGAINST half only and is descriptive.
6. **Addition C (D4)** runs inside part 1; a JOINT_ROLE_COPULA rule only if D4's trigger fires.

## 5. Data audit (non-negotiable 8, Devin 2026-10-12): what we hold, what we could add

* **Coach by game:** `team_coach_games` in `nba.duckdb` covers every team-game 2022-24 (7,906 rows in the gate's window; coach name present
  on all; `coach_id` NULL on 89 team-games in 2022 and 28 in 2023, so the key is the diacritic-free name). Built by `nba/data/coaches.py`
  from Basketball-Reference team pages (DECISIONS 2026-10-12; reference CSV `nba/data/head_coach_games_reference.csv`, 133 coach stints).
* **History 2013-21: no coach data at all.** `data/history/nba_history.duckdb` has 0 rows in `team_coach_games` and 0 in `team_coaches`
  (gate.json `history_audit`), though it holds the boxes (27,526-33,848 rows a season) and stints (1,143-1,323 games a season). The brief's
  "team_coaches back to 2013" does not match the store.
* **Repairable gap 1 (largest):** extend `nba/data/coaches_br.py` to 2013-21, 270 Basketball-Reference team pages (9 seasons x 30 teams) at
  >= 3 s spacing (about 15 minutes, one burst). Cost: free, but the Sports Reference terms check for the earlier pulls is still open
  (docs/reviews/bref_terms_check_2026-10-27.md: Devin must read the two sentences on bots and reuse). Downloads go through Devin
  (agents cannot curl). Effect: coach histories from 2013 (the 2023 select season would see 10 seasons of each veteran coach instead of one),
  and familiarity WITH counts for movers become non-trivial (coach-player reunions). It would NOT fix the movers' power (T504's half-width is
  about the cohort size, not the window).
* **Repairable gap 2:** 2019-21 stints exist; the stint-derived coach habits (first substitution, closing five) can be built for 2013-21
  once gap 1 is closed. Cost: CPU only.
* **Not available free:** coach rotation intent (practice reports, minutes restrictions said in pressers) is in the reporter feed only when
  Devin funds X access (REPORTER_FEED_LIVE); T-30 lineups for 2022-24 do not exist (T512, T522-a).
* **Decision for part 1:** run on what we hold (coach data 2022 onward). The rule says so; gap 1 is an open choice for Devin, and taking it
  means a re-gate before the freeze, not a change after.

## 6. Pre-declared descriptives (feed parts 2-5; no floors, no verdicts)

Written in full in MINUTES_DECISION section "Descriptives D1-D4" so they are frozen with that rule. Triggers (each opens a NEW id only):
D1 reopens part 2 if FULL-minutes F has abs(mean bias) >= 1.0 min with CI excluding 0 on changepoint-flagged rows in both seasons and C's
gain there is at least twice its gain elsewhere. D2 opens a part-4 in-game feature rule if the cold-minus-control ratio of 3PA per minute is
>= 0.10 with CI excluding 0 in both seasons. D3 opens part 3 (after the history pull) if movers with >= 2 games against the coach converge
>= 2 games faster, CI excluding 0 in both seasons. D4 opens JOINT_ROLE_COPULA if a pre-tip-knowable role state shifts a same-player
correlation by abs(dRho) >= 0.05 with CI excluding 0 in both seasons.

## 7. Playbook check (program level)

* Model classes: part 1 gradient-boosted trees (LightGBM mixture, MINUTES_V2 M2 recipe) with feature groups; part 2 a Bayesian filter feeding
  the same trees; parts 3-4 descriptives; part 5 the T508 logistic/GBM in-game model; addition C a Gaussian copula.
* Information or capacity: part 1 mixes both; the honest new information is opponent availability and size, coach habits and the
  player-coach history. Stability columns (addition B) are a re-expression of the minutes stream (capacity) and are judged as a slice
  contrast, not credited as information. Part 2 is capacity. Parts 4-5 are in-game information.
* Enough data for the class: trees on 2-3 seasons are fine (playbook); coach habits are per-coach and thin (34 coaches a season; a 2023
  row sees a median 121 earlier games of its coach), which is why the history pull is listed.
* Test validity: real-tip report gating, played-games rows (DNP is P(play)), integer support for every props arm, a binding CI (widest of
  game, player-season, week), shuffled coach ids and donor placebos with real labels, planted future and same-game plants, a conditional
  missingness audit with a positive control (the T450 failure was an unconditional audit of a legitimately as-of NULL).
* Lineage: COACH_PHASE (ridge, playoffs; stopped), TEAM_BUDGET (coach habits null), MINUTES_V2 (minutes-only), RECENT_ROLE_FULL (the FULL
  comparator), ROLE_CHANGEPOINT (parked), T508 (in-game). Carried guards: 5-game back-fill exclusion, previous-game coach rule, frame-hash
  rounding (LINEUPS_KNOWN_REBASE jitter), near-DNP slice (T471/T479).
* Scored against: minutes CRPS first (continuous, the minutes model's support), props integer CRPS second; nothing here reaches the line,
  and a CRPS gain has not reached the line before (T477, T480).

## 8. What I could not verify

* Devin's verbatim wording of the five ideas (only the DECISIONS paraphrase).
* The "Q4 cliff" numbers in the brief (starters' Q4 minutes 7.1 -> 4.8 at a 15+ halftime margin, DECISIONS 2026-10-27): I could not find that
  line in DECISIONS, NEXT_SESSION or the ledger on `main`. The ledgered version is T508's SHAPE: starters' Q4 minutes ratio 0.45-0.50 for
  abs(margin) >= 20 vs < 10, Q3 ratio 0.96-0.99, every season 2019-24. MINUTES_DECISION uses the T508 form (end-of-Q3 margin) for its
  coach Q4 habit.
* Whether the Sports Reference terms allow the history coach pull (open, Devin's read).
