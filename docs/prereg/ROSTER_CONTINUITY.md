# ROSTER_CONTINUITY: (a) team-specific Elo season carryover from returning minutes; (b) early-season props spread widening for low-continuity teams (pre-registration)

Status: **DRAFT (written by Mon Oct 12 13:58 CDT 2026, clock read by `date`), modeler. Not frozen. Both parts FAIL the minimum-data gate; (a) also fails on data: no as-of opening roster exists for 2023-24. The modeler recommends NOT freezing either part.** Queue item 3 of DECISIONS 2026-10-12 13:27 CDT. If Devin still wants a rule, the main session commits `prereg(ROSTER_CONTINUITY): <sha256>` BEFORE any arm is fitted. Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ROSTER_CONTINUITY.md | shasum -a 256`. Seasons: 2023 select, 2024 report; 2025 never loaded.

## The rule in five lines (as it would be frozen; not recommended)
1. Continuity `c_T` = share of team T's previous-season minutes (for T) played by players on T's **opening roster**, known before T's first game (official `CommonTeamRoster` pulled pre-opener, as `nba/props/rosters.py` does live; never box-score membership).
2. (a) Candidate = production injury-Elo with the season carryover `carry_T = clip(carry + beta (c_T - mean c), 0.2, 0.95)`, beta fitted once on 2022->2023 transitions, comparator production injury-Elo; game log loss, floor -0.004 (ELO_HISTORY_WINDOW), game-clustered CI (team-season clustering reported), primary population = games with either team in its first 25 games.
3. (b) Candidate = A0 with the integer grid widened about its median by a factor fitted on the select season for bottom-tercile-continuity teams in their first 25 games; comparator A0; integer CRPS pts on that slice, floor -0.005, game-clustered CI, coverage guard (tau 0.10/0.90 not worse by > 0.01).
4. Select 2023 / report 2024, both must pass; families: (a) and (b) are separate questions, one test each.
5. A pass is a candidate; Stage 2 = the 2026-27 forward log (30 team-seasons). Null closed; no follow-up.

## Hypothesis
Teams that kept their minutes carry their strength into the new season; rebuilt teams regress more (Elo) and are harder to forecast early (props). Descriptive already seen (main session, scratch, 2023-24): corr(continuity, team mean absolute props error, Oct to mid-Nov) -0.24, bias about 0.1 (no bias effect). No descriptive look exists for Elo.

## Forking paths and data exposure
The props corr -0.24 was seen on 2023-24 (the select/report seasons). For this gate: the continuity proxy below, outcome-free KL/CRPS-divergence ceilings, per-row sd/deff of the stored FULL-minus-A0 deltas, and one descriptive ratio (mean |y - A0 median| low-continuity vs other teams, early rows: 1.030 in 2023, 1.060 in 2024; the same look as the corr). No arm fitted or scored.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 13:54 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.roster_continuity_gate` (read-only, `season <= 2024`; 6.6 s; JSON `reports/prereg_roster_continuity/gate.json`).
* **As-of opening roster for 2023-24: NONE.** `data/rosters/` holds only the 2026-10-10 pull; a season-end `CommonTeamRoster` includes later trades (docs/OPENING_WEEK_ROSTERS.md); preseason games are not in `games`. The house replay proxy (players who played in the first 14 days) is box-score membership, which the brief and BEST_PRACTICES ("use rosters you'd actually know") forbid. **(a) and (b) both FAIL on data for a history test.**
* For sizing only, a box-score proxy (players with a box row, played or DNP, in the team's first game; never a feature): 60 team-seasons, continuity mean 0.637, sd 0.160, range 0.22-0.97.
* **(a) Elo, outcome-free:** an exact replay of production MOV-Elo with team-specific carryover (`elo_replay`, unit-tested equal to `sequential_elo_logits` at constant carry). Early games (either team in its first 25): 384 / 387 per season. beta 1.0 (carry spans about +-0.16 for +-1 sd of continuity): mean |prob change| 0.014 / 0.019 early, KL ceiling **-0.0008 / -0.0014** (floor -0.004), half-width 0.0041 / 0.0053 with the arm-implied sd, power 0.06 / 0.08 per season. beta 0.5: ceiling -0.0002 / -0.0004. The effective sample for a team-level treatment is 30 team-seasons per season, so a game-clustered CI would also be anti-conservative. **FAIL.** (Consistent with T318/T319: the first-15-games slice showed no carryover sensitivity.)
* **(b) Props, outcome-free:** low-continuity early rows 2,542 / 2,537 (tercile cut 0.565 / 0.591), sd_row 0.356 / 0.338, half-width 0.0138 / 0.0134. If the truth were 10% wider (the descriptive MAE ratio is 1.03-1.06, so 10% is generous): expected -0.0070 on the slice -> power 0.17 per season; at 5% wider: -0.0019. All rows: -0.0006. **FAIL** (as Devin expected).

## Gate verdict and recommendation
**(a) FAIL (no as-of data; ceiling a fifth to a third of the floor). (b) FAIL (power 0.17 at a generous widening).** Recommend not freezing either. What would reverse (a): opening-roster pulls archived each October from 2026 on, and a multi-season forward question; it cannot be powered within one season (30 team-seasons). (b) is closed as descriptive.

=== RESULTS BELOW ===

(not run)
