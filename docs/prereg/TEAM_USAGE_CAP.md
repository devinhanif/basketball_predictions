# TEAM_USAGE_CAP: cap the players' projected minutes, shots and assists at what one team can produce (pre-registration)

Status: **DRAFT 2026-10-22, modeler, branch `agent/modeler-team-usage-cap` off `main` 6d29113. NOT FROZEN; hash not taken; no arm
fitted; no arm scored on any outcome. The minimum-data gate was measured (below) and the points cells FAIL on power in every
configuration tried (binding half-width 0.0064 to 0.0130 against a 0.005 floor); reb, ast and fg3m are within about 10% of
their bars and fail or pass depending on the shock model. Under non-negotiable 8 this rule is not freezable as drafted:
STOPPED AT THE GATE for the main session, the adversary and Devin.** Model class: **a fixed deterministic constraint layer (no
fitted parameter) on top of the stored production context-residual forecasts; family: gradient-boosted-tree residual output +
set/joint consistency (MODEL_PLAYBOOK).**
Everything above the line `=== RESULTS BELOW ===` would be the rule if it were frozen. Freeze evidence (when and if): commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TEAM_USAGE_CAP.md | shasum -a 256` (first 8 hex into the results section
and every ledger row). Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25). Season 2025 is the spent holdout: never loaded.

**Advice or momentum.** Devin's override (idea 2026-10-22, tonight's PHI example), against Claude's standing recommendation: TEAM_BUDGET
(T446-T449) was closed with the reversal condition "a T-30 version fed confirmed lineups", not a T-60 version. This draft is a T-60 version.
It is a new question (changed-roster slice, one-sided cap, usage and assist share), so it gets a new id, and by the repo's rule it is the
ONE allowed follow-up of the TEAM_BUDGET family, after which the family is closed. Claude's recommendation: close at this draft (see Options).

## What Devin said
> Players share 48 minutes and the team's possessions, but production forecasts each player independently, so on teams that changed
> rosters the projected player means can sum to more shots, points, assists and minutes than a team produces. Tonight: PHI added LeBron
> James and Jaylen Brown; the model keeps LeBron at about 21 pts / 34 min (the market prices about 15) and VJ Edgecombe well above the
> market. Rescale players' projected minutes (to 240 team minutes including the OT expectation) and/or projected usage and assist share so
> team totals match the team's projected possessions, points and assists, before the per-stat distributions are drawn. (2026-10-22)

## Plain answer: architecture-only, or does it add information?
* **A1 (minutes cap) is architecture-only.** It is a deterministic function of A0's own minutes forecasts and the roster. It is the one-sided
  cousin of TEAM_BUDGET's T1 (T446: pts +0.1017 [+0.0910, +0.1120] / +0.1179 [+0.1062, +0.1302], HARM in every cell, even in the
  smallest-correction tercile: pts 2023 +0.0053 [-0.0017, +0.0120], 2024 +0.0095 [+0.0042, +0.0146]). Nothing in T-60 data tells us WHICH rostered
  players will not play; the overcount (mean forecast sum 264.2 / 258.6 minutes against 241.5, sd 46) is mostly players who sit.
* **A2 (usage and assist share) adds information only through its budget.** With a budget built from A0's own sums it is architecture-only
  (a tautology: the cap equals what it caps). With a budget from the team's as-of scoring history it is stale on exactly the slice of interest
  (a team that changed its roster has a stale history). The one genuinely new pre-tip fact is the **market's implied team total** ((total +/- spread)/2,
  stored at t60 for 1,318 / 1,315 games of 2023 / 2024). That is market information, whose game-context lineage is a tie (GAME_MARKET_CONTEXT, T389-T414;
  T501 re-derivation). It is NOT in this family; it is the one follow-up arm (A2m) if and only if this rule is ever frozen and run.
* The ledger lesson applies: architecture-only ideas have lost or tied every time (T062-T064 sim, T081 set transformer, T446-T449 budget).

## Prior evidence (lineage; cite before believing)
* **TEAM_BUDGET T1/T2 (T446-T449, frozen e43ca3c3)**: rake of production's roster minutes to 240 + 25 x OT, two-sided, clipped [0.8, 1.25]: harm in every stat and season,
  every slice (starter +0.169, bench +0.048, cold5_9 +0.056 [+0.009, +0.100], first15 +0.106, pts 2023); the harm halves at half strength (T448), i.e. the factor is mostly noise.
  Oracle shares (realised minutes) would give pts -0.180: the value is in who plays, which T-60 does not know. Its own follow-up TEAM_BUDGET_2 ("couple stats to team totals") was conditional on a T1 PASS and is therefore not licensed; A2 below is that follow-up, adopted on Devin's override.
* **Possession sim for props (T061-T064)**: pts sim minus recency +0.190 [+0.175, +0.206] (n 138,409): replacing the forecast by a rate-generated stat compounds errors. Here A0's distribution stays and one factor multiplies it.
* **T081 set transformer** (joint structure): R1 tie; team-total CRPS CI upper +0.18 > 0.05: joint/consistency structure bought nothing for marginals.
* **F11_LINEUP_CONTEXT (T244-T251)**: who shares the floor is knowable at T-30, not T-60; adequately powered null (pts 2024 -0.0020 [-0.0040, +0.0002]; MDE 0.0021-0.0029).
* **MINUTES_HAZARD (T300-T313)**: minutes CRPS +0.55 min worse than M2; props null (pts 2023 A1-A0 +0.0012 [-0.0017, +0.0040]); **MINUTES_V2 (T162-T173)**: minutes -0.83 min yet pts -0.0053 / -0.0018.
* **PRESEASON_ROLE_PRIOR (T500) and ROLE_PRIOR_SALARY (T504)**: the movers cohort (first 10 games with a new team) cleared pts on 2024 only (-0.0321 [-0.0624, -0.0032]; 2023 -0.0098 [-0.0362, +0.0179]) and ROLE_PRIOR_SALARY STOPPED AT THE GATE on power (pts noise-arm half-width 0.0167 / 0.0170 vs 0.005). This rule's slice is a superset of that cohort with the same power problem.
* **Live descriptive (2026-27, not a test)**: scorecard 2026-10-21, Part 1, all stats pooled, Team vs last pre-season game: new team n=268 (13 games) bias +0.308 [-0.445, +1.085], CRPS 1.698 [1.424, 1.978]; same team n=888 bias -0.123 [-0.375, +0.148], CRPS 1.543. The new-team bias CI includes 0 and the CRPS CIs overlap; 13 games is far too few to read. Exposure: Devin and I have seen this table; it is not an outcome of any arm here.
* **Label-free premise check (this gate, below)**: in 2023 / 2024 changed-roster team-games the stored A0 means summed over the rows that played are LOWER (113.0 / 110.5 pts) than in other team-games (114.4 / 113.7), and so are the summed recency minutes (236.3 / 234.3 vs 238.5 / 237.6). The premise "forecasts overshoot more on changed rosters" is not visible in the backtest averages (it can still hold for star-arrival cases like tonight's; the slice averages many small moves).

## Exposure (honest)
Seen: the idea and tonight's PHI example (live, 2026-27, not 2023/24); the scorecard slice above; the ledger rows above; the label-free gate numbers below.
Not seen: any stat outcome of any cap arm on 2023 or 2024. The gate reads no `y` and no realised `minutes` of the scored game: realised minutes are read only for PRIOR
games (strictly earlier dates) to build as-of rolling minutes. Season 2025 never queried; `nba.duckdb` and `data/odds/odds_history.duckdb` opened `read_only=True`, `season <= 2024` in SQL.
Data version: `reports/odds_history/model_rows_{2023,2024}_{pts,reb,ast,fg3m}.parquet` sha256 first 8: pts d0897569 / 81a66fd6, reb 337be51e / bbb89d17, ast 241cd38a / 62bb6c7c, fg3m 96e46fbb / 6fa84bd8
(stored 2026-10-10; the T493-T496 note says current production A0 for 2024 differs by one data commit, d872dc7: 10 neutral-site games, 118 rows; 2023 identical; not re-derived here); `nba.duckdb` mtime 2026-10-22 12:50 CDT, main HEAD b85fd86.

## Hypotheses
H1 (A1, minutes cap): applying a one-sided team minutes cap (factor <= 1 on the players of a team whose arrivals-inclusive projected minutes exceed the budget) to production's forecasts lowers integer-support CRPS on the changed-roster slice by at least the floor.
H2 (A2, minutes + usage + assist cap): additionally capping the team's projected points-producing (FGA + 0.44 FTA + TOV) and assist shares does so beyond A1.
Null: no change. Both are expected to fail; the rule exists so a null is clean and closed. Protects against: crediting a bias correction as "structure" (A3 below is the bias-only comparator), and crediting a slice for what any recalibration would do.

## The slice (frozen constants; defined from as-of information only)
* **Rows**: regular-season (`game_id` prefix `002`) A0 rows of `model_rows` (played, `n_prior >= 5`; 25,815 / 25,649 rows per stat after dropping playoff and play-in rows: T505-erratum). Scored rows = the A0 rows, as in every prior rule.
* **Arrival (as-of)**: player p, team T, game g with date d is an arrival if p has an earlier regular-season appearance for another team, p's current same-team run began with that change, p has appeared for T in fewer than **K = 10** regular-season games before d, OR p has not yet appeared for T but appears for T in the preseason log of that season (preseason games are before the regular season; known-at is the preseason game date). Preseason logs cover 30 of 30 teams in 2022-24 (1,841 / 1,834 / 1,989 player rows).
* **Rotation**: the 10 players of T's as-of roster (appeared for T in its previous 10 games, plus preseason-identified arrivals) with the highest mean minutes over their previous 10 appearances (any team, dates strictly before d).
* **NS(g, T)** = arrivals' share of the rotation's as-of minutes. **Primary slice: NS >= X = 0.20** (about two to three rotation arrivals; tonight's PHI would be about 0.3). X was chosen from label-free counts (below); X = 0.15 and 0.10 are reported, never selected between.
* Arrivals NOT identifiable from the preseason log (mid-season trades and late signings: 11% / 16% identifiable in 2023 / 2024) are not in NS, so they enter the slice as non-arrivals: the slice is the preseason-known movers' first 10 games. Stated, not repaired (see data audit).
* Secondary, no verdict word: all rows; X = 0.15; X = 0.10; arrival rows only; incumbents on slice teams.

## Fixed design (constants final if ever frozen)
Budgets. `B_min = 240 + 25 x ot_rate_asof` (TEAM_BUDGET constant, `research/budget/constraint.py::team_budget`). `e_i = p_i x mu_i` from the production minutes path (`predict_minutes`) at the player's latest row strictly before the game (arrivals therefore use their old-team row; fallback p = 0.5, mu = as-of mean minutes), roster = as-of rotation roster (above) including preseason-identified arrivals, minus the real-tip-gated OUT set (`tip_source="real"`). Not measured for arrivals yet (see gate G7).
* **A0** production + lower-tail mixture, reproduced to 1e-9 on the stored grids.
* **A1** minutes cap: `f_g = min(1, B_min x cover_asof / sum_i e_i)`; every roster player's A0 quantile grid is multiplied by `r_i = max(f_g, 0.8)` (one-sided: never raises a forecast; floor 0.8 as in TEAM_BUDGET); players outside the roster keep r = 1. Re-served on the integer grid `ceil(q - 0.5)`.
* **A2** = A1 plus a stat budget. pts and fg3m: `U_i = e_i x (as-of usage per minute)` with usage FGA + 0.44 FTA + TOV per minute (the proxy STRUCT used, decayed half-life 10 games); budget `B_use` = the team's as-of usage total per team-game, shrunk to the league value with pseudo-count 10 games; `f_use = min(1, B_use / sum U_i)`. ast: `B_ast` likewise from assists. reb is not capped (a rebound is not a possession share in this proxy); reb carries A1 only. Factors multiply into A1's `r_i` for that stat, floored at 0.8.
  The budget is **non-market** (the team's own as-of history plus the league mean). Where the team has an arrival-heavy rotation that history is stale by construction; that is the honest weakness of A2 and the reason A2m exists as the follow-up.
* **A3 (information-matched baseline; BEST_PRACTICES 2026-10-22)**: a walk-forward scalar per stat on slice rows only: the mean log ratio actual/forecast over EARLIER slice rows (strictly earlier dates, at least 100 rows, else 1), applied uniformly to every slice row's A0 grid. It has the same pre-game information as A1/A2 (the slice flag) and no team structure. A1/A2 must beat A3 by point <= -0.003 pts / -0.002 reb / -0.001 ast / -0.0005 fg3m (half the floors).
* **A4 (placebo)**: A1/A2 computed with the cap factor drawn from a different team-game of the same date and season (marginal of factors kept, link to this roster broken). A past-only construction (no future information).
* **Oracle O (descriptive, a leak by construction, never in the family)**: A1 with realised minutes known. Bounds the value; an arm that beats O is a presumed leak.
Information each arm has: A0 = recency minutes/rates, `vac_*`, `n_out_rot`, the report; no arrival status, no roster budget. A1 = A0 + arrival-inclusive roster minutes budget. A2 = A1 + team usage/assist history. A3 = A0 + slice membership. A4 = A1/A2 with a wrong team's factor. A2m (follow-up only) = A2 with the market implied team total as the budget.

## Metric, support, inference, family
* **Metric**: paired integer-support CRPS (199-point grid, `ceil(q - 0.5)` for EVERY arm), per stat, arm minus A0, on slice rows. **Floors** (CRPS units, benefit <= -floor): pts 0.005, reb 0.003, ast 0.002, fg3m 0.001 (the PRESEASON_ROLE_PRIOR / ROLE_PRIOR_SALARY scale; Devin's "-0.005" is the pts floor; applying -0.005 to every stat would make ast and fg3m unpassable, see open choices).
* **CI**: 95% percentile bootstrap (2000 draws, seed 0, `nba/props/metrics.py::_clustered_boot_means`) clustered by **game, by team-season and by ISO week; the widest binds** (a team's cap factor is shared across its first ten games, so game clustering alone is anti-conservative: BEST_PRACTICES 2026-10-20).
* **Family and multiplicity**: arms {A1, A2} x stats {pts, reb, ast, fg3m} = 7 cells (reb has A1 only), BH q = 0.05 within each season. **Select 2023, report 2024.** A cell clears a season when point <= -floor, binding CI upper < 0, BH p < 0.05. A cell PASSES only if it clears BOTH seasons AND beats A3 by half the floor AND A4 by the same margin.
* **Guards** (each stat, slice): |bias| <= 0.5; 80% PIT coverage in [0.75, 0.85] (randomised PIT with a fixed seed and the canonical row order `sort by (game_id, team_id, player_id)` before any draw); lower-tail PIT q0.10 within +/- 0.02 of 0.10; no sub-slice with n >= 300 (starters, bench, arrivals, incumbents) worse than A0 by more than +0.01. Reliability at the line is not a verdict here (no price is read).
* **Cap-48 and sum identity**: after A1, the capped roster sum is <= B_min x cover to 1e-9 on every team-game (unit test on synthetic and real team-games).

## Minimum-data gate (non-negotiable 8), measured Thu Oct 22 2026, about 12:46-12:51 CDT
Command (read-only, 36 s, no heavy lock needed, light polars/pandas): `cd <checkout with data/ and nba.duckdb> && .venv/bin/python -W ignore -m research.eval.team_usage_cap_gate` ->
`reports/prereg_team_usage_cap/gate.json` and `gate.scores.parquet` (NS per team-game; gitignored scratch, regenerated by the command). Dry-run count: 4,920 regular-season team-games (2 x 2,460), 3,690 games loaded, 78,587 prior box rows, 5,664 preseason rows, 8 model_rows files.

| check | 2023 | 2024 | bar (written before the run) | pass |
|---|---|---|---|---|
| regular-season team-games / A0 rows per stat | 2,460 / 25,815 | 2,460 / 25,649 | reproduces T505-erratum minus playoffs | yes |
| NS missing | 0 | 0 | 0 | yes |
| G1 slice team-games, NS >= 0.20 (teams) | 392 (23) | 375 (21) | >= 300 | yes |
| G1b slice team-games at 0.15 / 0.10 | 687 / 906 | 584 / 850 | reported | n/a |
| G2 slice rows per stat (games / player-seasons / weeks) at 0.20 | 4,068 (341 / 347 / 23) | 3,854 (325 / 321 / 18) | rows >= 3,000, weeks >= 15 | yes |
| rows at 0.15 / 0.10 | 7,207 / 9,545 | 6,033 / 8,780 | reported | n/a |
| G3 season-start arrivals (first 5 team-games) known from the preseason log | 91 of 98 (92.9%) | 74 of 76 (97.4%) | >= 0.90 | yes |
| G3b later-season arrivals known from the preseason log (mid-season trades, signings) | 14 of 124 (11.3%) | 21 of 135 (15.6%) | reported | no (repairable? see audit) |
| G4 team-game roster as-of (previous 10 games + preseason arrivals): NS built from no game on or after the date | by construction; planted-future unit test required before any arm | same | exact identity | **not yet built as a test** |
| G5 market implied team total at t60 (stored), games | 1,318 of 1,318 | 1,315 of 1,315 | >= 0.95 | yes (A2m only) |
| G6 team possession / points forecast stored as-of for these games | **no**: `team_context` and `team_game_advanced` hold 0 rows; no stored team-total forecast | same | exists | **NO** (A2's budget must be rebuilt from box scores) |
| G7 production minutes `e_i` exist as-of for arrivals | **not measured** (TEAM_BUDGET G5 covered the previous-10-game roster: 2,636 / 2,640 team-games; arrivals are outside that roster) | same | >= 0.97 of slice roster rows | **unmeasured** |
| **G8 pts power, binding half-width of an informationless shock (sd of log factor 0.05), NS >= 0.20** | 0.0077 (team-season shock) / 0.0103 (player-season) / 0.0130 (team-game) | 0.0067 / 0.0121 / 0.0101 | <= 0.005 | **NO, every shock, both seasons** |
| G8 pts at X = 0.15 (n 7,207 / 6,033) | 0.0072 / 0.0079 / 0.0082 | 0.0064 / 0.0079 / 0.0083 | <= 0.005 | **NO** |
| G8 pts at X = 0.10 (n 9,545 / 8,780) | 0.0092 / 0.0087 / 0.0082 | 0.0096 / 0.0078 / 0.0073 | <= 0.005 | **NO** |
| G8 reb, NS >= 0.20 (floor 0.003) | 0.0025 / 0.0040 / 0.0038 | 0.0027 / 0.0045 / 0.0037 | <= 0.003 | passes only under the team-season shock; fails otherwise |
| G8 ast, NS >= 0.20 (floor 0.002) | 0.0022 / 0.0027 / 0.0030 | 0.0017 / 0.0029 / 0.0026 | <= 0.002 | fails under the widest-binds rule |
| G8 fg3m, NS >= 0.20 (floor 0.001) | 0.0009 / 0.0013 / 0.0017 | 0.0008 / 0.0013 / 0.0012 | <= 0.001 | fails under the widest-binds rule |
| season 2025 loaded | no | no | never | yes |
The G8 triple reads: team-season shock / player-season shock / team-game shock (the order of the keys in gate.json); the binding clustering is in gate.json. The widest-binds rule is the repo's convention (T500, T504-T506), so reb, ast and fg3m also fail it, narrowly (reb at X = 0.10: 0.0027-0.0033; ast 0.0016-0.0021; fg3m 0.0008-0.0011).
Not run (declared): the planted-future test (G4) and the A0 reproduction (needs the full stored grids; unit tests only); the positive control (an oracle shift must clear pts on the real slice rows) and the synthetic-outcome dry run of the whole harness (BEST_PRACTICES / DECISIONS 2026-10-21 T507 incident: outcomes are not loaded until a complete dry run on synthetic outcomes passes) are required before any real-outcome run; they are part of the harness spec, not yet built, because the gate stops the rule first.

**Data-capture audit (non-negotiable 8; before any gate is declared failed on data).** Held: nba.duckdb 2022-2024 (3,690 regular-season games in scope), `data/history` 2013-2021 box scores (not read: A0 `model_rows` exist only for 2023-24, so earlier seasons would buy warm-up, not select or report seasons), preseason logs 2022-2026 for all 30 teams, `odds_history.duckdb` totals/spreads/h2h at t60 and t5 for 100% of 2023-24 games, official reports (T-60 real-tip, the same fixtures as F11). Not held and could be added: (i) mid-season trade and signing dates for 2023-24 (the 85-89% of later arrivals the preseason log cannot place): NBA.com transactions via nba_api (free, about 30 team calls per season, ToS to check) or the official daily transaction pages (one request per day, about 200 per season); Pro Sports Transactions is behind Cloudflare and we do not circumvent access controls (DECISIONS 2026-10-12). It would raise identifiable arrivals from 45-47% to near 100% but also shifts the slice toward mid-season trades with different dynamics; (ii) a stored team-total forecast: rebuild from box scores (free, no new source) or use the market totals already stored (A2m); (iii) more A0 rows: the A0 context-residual walk-forward for 2022 (a 2021 warm-up from `data/history`) would add a THIRD season of slice rows (about 4,000 rows) and cut the half-widths by about 20%, cost one heavy refit on Devin's Mac (about 95 minutes for the previous full run, not done here). None of (i)-(iii) closes the pts gap (0.0064-0.0130 vs 0.005): pts would need about n x (hw / 0.005)^2 = 6,900 (best case) to 27,000 (worst case) slice rows per season against 3,854-4,068 held. So the pts failure is a power failure on the data we hold, and no listed repair is cheap enough to fix it; a pooled 2023+2024 single test (no select/report split) would reach 0.0045-0.0092 at best, borderline, and gives up replication.

## Options (Devin's call) and Claude's recommendation
1. **Close at the draft, record the null-by-gate (recommended).** Reasons: T446-T449 harm including the least-correction tercile; the premise is not visible in the backtest averages; pts fails power by 30-160%; A1 is architecture-only; F11 and TEAM_BUDGET both say the information is T-30. Costs nothing more. Cost of being wrong: a real star-arrival effect hidden in a slice that is too noisy to see; the live log can show it later.
2. **Forward-only shadow in the 2026-27 log**: compute A1/A2 in the shadow table beside production for every slice team-game, no fit, no production change; freeze a final-look rule (rows needed: pts 7k-27k at the X = 0.20 slice; at the live pooled new-team rate of about 20 rows per slate game, 7k rows is most of a season and 27k is more than one; ast and fg3m earlier). Needs a separate rule and Devin's yes; touches `nba/daily` (not this branch).
3. **A T-30 version fed confirmed lineups (the ledger's stated reversal)**: inactives and starters known, so the budget is applied to the true set of players. New rule id; needs T-30 snapshots stored for 2023-24 (not held: confirmed lineups exist from 2026-27 only) so it is forward-only too. Information, not architecture: the only version with a reason to win.
4. **A2m market-total budget**: new rule, new information (market implied team total), lineage GAME_MARKET_CONTEXT tie; the market is a prior, not a target.
Choosing 1 closes the family; choosing 2 or 3 is the one allowed follow-up.

## Kill criteria (if ever frozen and run)
* Check G4, G7, the A0 reproduction, the sum identity or the positive control fails: stop, no model result; the fix is a new id, not an edit.
* No cell clears 2023: close. No weights, floors, X, K, rotation size or budget variants afterwards. At most ONE pre-registered follow-up (Option 3 or A2m), then closed.
* A1 or A2 beats the oracle O: presumed leak, audited by qa before any number is reported.
* A cell that clears 2023 and fails 2024 is FAIL_ONE_SEASON (T498/T499 precedent), not a promotion.
* No 2025 touch is requested by this rule. A pass earns only a drafted (not appended) holdout row and a live-shadow recommendation; promotion is Devin's.

## What each choice protects against
One-sided cap with floor 0.8: T446's harm came from two-sided raking that also inflated players. Slice from as-of data only: a slice defined from the box score (who played, who started) is membership leakage. Arrival from the preseason log: the live path uses official rosters, and a replay roster that peeks at later games is optimistic (nba/props/rosters.py). Three clusterings, widest binds: team-level factors persist across games. A3: BEST_PRACTICES 2026-10-22 (T508), a baseline with the same information. A4: a cap that "wins" with another team's factor is a recalibration, not structure. Integer grid for every arm: T114-T118 artifact. Floors per stat: the CRPS scale differs by stat. No fitted parameter: nothing to tune on a select season.

## Playbook check (MODEL_PLAYBOOK)
Class: constraint layer on GBT context-residual output (tree class checks) with set/joint consistency checks.
1. Information or capacity? A1 is capacity/structure only; A2 adds only a stale non-market budget; the new information (T-30 lineups, market team total) is outside this family.
2. Enough data for this class? A fixed layer needs no training data; the TEST lacks data: 3,854-4,068 slice rows per season vs 6,900-27,000 needed for pts at the floor.
3. Test valid? Not yet: no as-of roster test (G4), `e_i` for arrivals unmeasured (G7), the synthetic-outcome dry run and positive control are specified, not built; a noisy slice and 11-16% identifiability of later arrivals can create a null, so a failed run would not be a clean null; hence the gate stops first. Guards: shuffles are not used (a within-team placebo A4 is). The A0 comparator has all the information the cap uses except arrival status.
4. Lineage: T446-T449 (T1 is A1's two-sided parent; its harm is carried forward as the prior), T062-T064, T081, F11, MINUTES_HAZARD, T500/T504. Inherited guards: integer grid, game/team/week clustering, per-slice no-harm, oracle bound. Dropped: coach habits (nothing at T-60), role-share model T2.
5. Scored against? Integer CRPS vs A0 and vs A3. No claim about lines or ROI: CRPS gains have not reached the line (T477).
Class checks: missingness: NS has 0 NULLs; any later column must show a NULL audit by realised-minutes bucket (<5, 5-10, 10-20, >20) with a limit of 2 pp; planted future: the as-of roster and NS must be byte-identical when any row dated on or after the game is edited; does the arm only re-express the base? A1 rescales A0 by a factor of one number per team-game: yes, unless it beats A3.

## Priors (for docs/PREDICTIONS_LEDGER.md, written before any result; none of these has an outcome)
* P(rule is freezable as drafted: pts power gate passes) 0.03; P(any of reb/ast/fg3m gates pass under the widest-binds rule) 0.25.
* If it were frozen and run: P(any cell PASSES both seasons) 0.03; P(A1 pts clears either season) 0.02 (expected A1 pts dCRPS +0.015, 80% -0.004 to +0.06: harm; smaller than T1's +0.10 because one-sided and slice-limited); P(A2 any stat clears 2023) 0.08; expected A2 pts dCRPS +0.005 (80% -0.010 to +0.030).
* P(Devin chooses close at the draft) not stated (his call).

## Open choices for Devin (each with a default)
1. Close at the draft (Option 1) vs forward shadow (2) vs T-30 version (3). Default: 1; if any, 3 over 2.
2. If run anyway: floors per stat (default: pts 0.005, reb 0.003, ast 0.002, fg3m 0.001) vs uniform -0.005.
3. Slice threshold X (default 0.20; 0.15 reported) and K = 10, rotation = top 10.
4. Shock model for the power bar (default: widest of player-season, team-season, team-game binds, as T500).
5. Whether to pay for a repair of arrival identifiability (default: no; cost above).
6. Whether A2's budget may use the market total (default: no, A2m is the follow-up).
7. Whether usage proxy FGA + 0.44 FTA + TOV and a 0.8 floor are accepted (default: yes).

=== RESULTS BELOW ===

(not run; stopped at the gate)
