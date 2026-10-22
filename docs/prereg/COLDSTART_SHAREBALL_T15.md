# COLDSTART_SHAREBALL_T15: new-team cold-start prior plus "teammates share one ball", re-shared at T-15 on confirmed fives (pre-registration)

Status: **DRAFT 2026-10-22, modeler, branch `agent/modeler-coldstart-shareball` off `main` 2e3d73e. NOT FROZEN; hash not taken; no arm fitted; no
arm scored; no stat outcome read.** The minimum-data gate was measured label-free (below). **Part A (movers' cold-start prior) FAILS its power gate for
every stat and both seasons; Part B (share-ball re-share) passes power on the large-change and everyone slices but has no historical comparator
(G9) and no announced-five history (G6). Under non-negotiable 8 the full rule is not freezable as drafted: STOPPED AT THE GATE** for the main
session, the adversary and Devin. Model class: **a fixed-form input blend on the GBM context-residual output (Part A) plus a deterministic
set/joint share-normalisation layer (Part B); no fitted parameter beyond the constants below.** Everything above `=== RESULTS BELOW ===` would be
the rule if frozen (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/COLDSTART_SHAREBALL_T15.md | shasum -a 256`). Season labels are
`games.season` (2023 = 2023-24). Season 2025 (holdout) is never loaded; this rule requests no 2025 touch.

**Name and timing.** File is `_T15`, not `_T30`: the steward's diagnosis (regular season, n = 14 games) found both fives confirmed in the daily_lineups
feed at T-30 in 0 of 14, at T-20 in 6 of 14, at T-10 in 13 of 14, at tip 14 of 14, median lead 16.8 min (5.5-24.9). The frozen
`props_context_residual_t15` arm (FORWARD_T15_2026_27) logged 9 of 14. The information cutoff is therefore **tip minus 15 minutes, trigger = both
fives Confirmed under the frozen dedup rule (`nba/lineups/store.py::dedup_snapshot_rows`)**, snapshot `fetched_at` < tip - 15 min, real tip only.
T-10 (13 of 14) is a fallback that would be a SEPARATE rule with its own id; nothing here reads it.

**Advice or momentum.** Devin's override (2026-10-22) of the T509 gate stop, built as the ledger's stated reversal of TEAM_BUDGET (T446-T449,
HARM): a confirmed-lineup version, not a T-60 one. Claude's standing recommendation was to close the family at T509; this draft is the ONE allowed
follow-up of it (kill criteria below). It is a new question, so a new id.

## What Devin said
Cold start: a player on a new team gets his own per-minute rates adjusted toward the role he steps into (usage and minutes vacated by departures on his
new team, measured as-of from transactions and preseason), shrunk by games played with the new team. Shared ball: teammates share one ball, so at
T-15, with the five starters confirmed and the expected rotation known, re-share minutes and usage (shot share, assist share) among the players actually
available.

## Plain answer: what is new information, what is architecture
Compared with the **production T-60 model** (A0 context residual): new are (i) the confirmed five and listed-inactive set at T-15, (ii) the new team's
as-of vacated role. Compared with the **frozen T-15 shadow arm** (starter flags `t30_*` already inside it): only (ii) and the cross-player
normalisation of (iii) are new, and (iii) is **architecture** (a deterministic function of A0's own forecasts and the available set).
* Information: departures' prior-season minutes and usage on the mover's team (preseason log, S-1 box scores; mid-season trades only if a transaction
  source is added). Honest limit: this is a team-level scalar; at the player level it says how much room exists, not who gets it. T500 (PRESEASON_ROLE_PRIOR,
  movers' first 10 games) cleared pts on 2024 only (-0.0321 [-0.0624, -0.0032]; 2023 -0.0098 [-0.0362, +0.0179]), WOUNDED at player clustering (5 players carry
  98.6%). The vacated-role number adds a team-level term to that.
* Architecture: share-normalisation. Lineage says it loses or ties: TEAM_BUDGET T1/T2 HARM in every cell (pts 2024 +0.1179 [+0.1062, +0.1302]; even the smallest-correction tercile +0.0095),
  oracle shares -0.180 (value is in WHO plays), F11_LINEUP_CONTEXT adequately powered null (pts 2024 -0.0020 [-0.0040, +0.0002], MDE 0.0021).
  What T-15 changes: the available set is partly known (inactives, confirmed fives), which removes the T-60 failure (the overcount was players who sit).
  If Part B fails again the family is closed.

## Hypotheses
HA (cold start): on movers' first K = 10 team games (K = 5 reported) the role-prior blend lowers integer-support CRPS vs the T-15 arm by at least the floor.
HB (share ball): at T-15, normalising the available players' minutes and usage/assist shares to the team's budget lowers it on teams with a large roster change (and does not harm everyone).
HC (primary, combined): CS = A + B beats the T-15 arm on both slices. Null: no change. Protects against crediting a recalibration as structure (A3) and a slice for what any scalar would do.

## Fixed design (constants final if ever frozen)
Rows: regular-season (`game_id` prefix `002`) rows of the stored A0 `model_rows` (played, `n_prior >= 5`): 25,815 / 25,649 per stat (2023 / 2024).
* **A0** = comparator. In the forward log: the logged `props_context_residual_t15` row. In history: a T-15 stand-in (production features + `t30_*` from the box `starter` flag, refit as in `research/eval/lineups_known_eval.py`); **not yet built or reproduced (G9)**.
* **Mover / cohort**: slice label from `preseason_role_prior.slice_frame` (mover = first team of S differs from last team of S-1; `gwt` < K games with the team). Reproduced: 1,202 / 1,025 rows (K = 10), 640 / 540 (K = 5).
* **Vacated role V(T)** as-of: sum of S-1 minutes per game of T's players with >= 20 games and >= 15 mpg who are not on T's season-S preseason log, split into "confirmed elsewhere" (another team's preseason log; V_c) and "unknown". Mid-season departures enter only with a transaction source (not held).
* **C (cold start)**: per-minute rates r_i from the player's as-of recency (any team); role minutes m_role = clip(as-of mean minutes x (1 + V_c-share of the rotation x his rank share), 0.5, 2.0) (rank share = his as-of minutes over the arrivals' minutes); blend w = g/(g + 10), g = games with the team (pseudo-count 10, T500 constant): lambda = w x 1 + (1-w) x m_role/m_own. The forecast grid is A0's multiplied by lambda, re-served on the integer grid `ceil(q - 0.5)`.
* **S (share ball)**: available set = as-of roster (previous 10 games + preseason arrivals) minus the report's OUT set minus T-15 listed-inactive (forward) , the five confirmed starters forced in. Team budget B_min = 240 + 25 x ot_rate_asof; factor on projected minutes `min(1, B_min x cover / sum e_i)`, floor 0.8, one-sided (TEAM_BUDGET T1 raked two-sided and raised players, which harmed); usage and assist budgets from the team's as-of totals shrunk to league (pseudo-count 10 games); reb is not capped.
* **CS** = C then S (primary). C and S alone are secondary, no verdict word.
* **Information-matched controls** (same inputs available to CS, no structure): **A3** walk-forward scalar per stat and slice (mean log actual/forecast over earlier slice rows, >= 100 rows else 1); **A4** placebo: C and S factors taken from a different team-game of the same date (marginal kept, link broken, past-only). **O** oracle with realised minutes (descriptive; an arm that beats O is a presumed leak).
Information per arm: A0 = recency, `vac_*`, report, starter flags; C = A0 + vacated-role scalar; S = A0 + available set budget; CS = both; A3 = A0 + slice flag; A4 = CS with a wrong team's factors.

## Metric, support, inference, family
* Paired integer-support CRPS (199-point grid, `ceil(q - 0.5)` for EVERY arm), arm minus A0, per stat. **Floors** (benefit <= -floor): pts 0.005, reb 0.003, ast 0.002, fg3m 0.001 (CRPS scale differs by stat; the PRESEASON_ROLE_PRIOR / ROLE_PRIOR_SALARY / TEAM_USAGE_CAP scale, so results are comparable; 0.005 is Devin's pts floor; one floor for all stats would make ast and fg3m unpassable).
* CI: 95% percentile bootstrap (2000 draws, seed 0, `_clustered_boot_means`) clustered by game, by player-season (movers) or team-season (large-change) and by ISO week; **the widest binds** (BEST_PRACTICES 2026-10-20).
* Slices: **movers K = 10** (primary for HA), **large-change teams** (T509 NS >= 0.20: arrivals' share of the as-of rotation minutes; 392 / 375 team-games; primary for HB), **everyone** (guard: CS must not be worse than A0 by more than +0.001 pts / +0.0005 others, upper CI). Also reported: movers vs non-movers on large-change teams, K = 5, starters vs bench, arrival vs incumbent.
* Family: arm CS x slices {movers, large-change} x stats {pts, reb, ast, fg3m} = 8 cells, BH q = 0.05 within each season. **Select 2023, report 2024.** A cell clears a season when point <= -floor, binding CI upper < 0, BH p < 0.05. A cell PASSES only if it clears BOTH seasons AND beats A3 and A4 by half the floor.
* Guards per stat and slice: |bias| <= 0.5; 80% randomised-PIT coverage in [0.75, 0.85] (row order `(game_id, team_id, player_id)`, fixed seed); lower-tail PIT q0.10 within +/-0.02 of 0.10; no sub-slice with n >= 300 worse by more than +0.01; top-5-player share of the movers' gain <= 0.5 (T500 lesson); NULL audit of every added column by realised-minutes bucket (<5, 5-10, 10-20, >20) within 2 pp; planted-future test on V, the roster and the available set.
* Historical stage cap: with a box-starter proxy the history stage can only say "PROCEED TO FORWARD SHADOW" or "CLOSE"; any CRPS claim about T-15 waits for the forward log (>= 300 logged mover rows is far short of power; see gate).

## T-15 as-of reconstruction: announced fives are not in history
History holds the box `starter` flag (exactly 5 starters in 2,460 of 2,460 team-games in both seasons; 0 NULL) and stints (realised fives, 98-99% reconciled). Announced fives
and the inactive list are captured only from 2026-10-09 (`data/lineups/lineups.duckdb`). How realised information is kept out:
1. **Stints are never read.** A stint five is what happened, including late substitutions and the order of the first possessions.
2. The announced-five **proxy is the box `starter` flag** (LINEUPS_KNOWN convention). It is optimistic: it already resolves game-time scratches (16.7% of 2024 rows sit in team-games with a usual starter questionable at T-60 and ruled out; docs/reviews/redteam_lineups_known_2026-10-09.md). Every historical number is labelled PROXY and carries the red team's range logic (worst case: all such calls after the cutoff).
3. **The available set never uses who played or minutes**: it is the as-of roster minus T-60 OUT (real tip), plus the proxy five; A0 rows are "played" rows, so scoring rows are conditional on playing for every arm alike, but the budget is built from the available set, not from the scored rows (membership leak otherwise; sim backtest lesson).
4. No historical bench inactive list exists ("listed active" unmeasurable), so S in history re-shares over the as-of roster, which is the T-60 information plus the proxy five. The T-15 gain beyond that is forward-only.
5. 5% synthetic scratch sensitivity (LINEUPS_KNOWN design) applied to the proxy; reported, not a verdict.

## Minimum-data gate (non-negotiable 8), measured Thu Oct 22 2026 about 15:50 CDT, label-free
Command (read-only, about 15 s, no heavy lock): `cd <checkout with data/> && PYTHONPATH=. .venv/bin/python -W ignore -m research.eval.coldstart_shareball_gate` -> `reports/prereg_coldstart_shareball/gate.json` (sha256 first 8 of the committed copy: see commit; regenerated by the command). Dry-run counts: 4,920 regular-season team-games, 8 `model_rows` files, 2026 live counts from 2 decision tables. Reads no `y`; realised minutes only for prior games / the prior season. Season 2025 not loaded. Data version: `model_rows_{2023,2024}_{pts,reb,ast,fg3m}.parquet` as in TEAM_USAGE_CAP (pts d0897569 / 81a66fd6); `nba.duckdb` mtime 2026-10-22 15:20 CDT; main HEAD 83409fd.

| check | 2023 | 2024 | bar (written before the run) | pass |
|---|---|---|---|---|
| G1 mover rows per stat, K = 10 (players / games / weeks) | 1,202 (136 / 487 / 25) | 1,025 (115 / 468 / 25) | reproduces T500 / T504 (1,202 / 1,025); >= 400 | yes |
| G1b mover rows K = 5 | 640 | 540 | reported | n/a |
| G2 large-change slice (NS >= 0.20): team-games / rows | 392 / 4,068 | 375 / 3,854 | reproduces T509 | yes |
| G2b movers inside the large-change slice | 434 | 410 | reported | n/a |
| G3 mover rows whose team has confirmed-departure vacated minutes >= 24 mpg (V_c) | 96.7% (1,162) | 92.3% (946) | >= 50% (otherwise the role signal is absent) | yes, but **uninformative**: median V_c 71 / 68 mpg, 28 / 24 of 30 teams, so V_c barely discriminates movers' teams |
| G4 vacated minutes identifiable at the player level | no: V is a team scalar | same | n/a | note |
| G5 mid-season arrivals placeable (preseason log only) | 14 of 124 (11%) | 21 of 135 (16%) | reported (T509) | repairable only with a transaction source, not held |
| G6 announced five in history | no: box-starter proxy only (2,460 of 2,460 team-games have exactly 5) | same | announced five | **NO (proxy)** |
| G6b movers' rows where the proxy starter flag differs from the as-of usual-starter flag (where a T-15 five would act) | 204 of 1,202 (17.0%) | 175 of 1,025 (17.1%) | reported | n/a |
| G6c all rows where it differs | 12.0% | 13.0% | reported | n/a |
| G7 T-60 report coverage of A0 rows / rows with an OUT rotation player (movers) | 99.9% / 79.2% | 100% / 84.7% | >= 0.95 | yes |
| G8 live T-15 capture 2026-27 (decisions) | logged 9 of 14 (64%); T-30: 1 of 24 | | >= 0.70 Wilson lower bound for a live claim | **NO** (Wilson lower about 0.39) |
| G9 T-15 stand-in comparator (box-starter refit) exists and reproduces | **not built** | | needed before any arm | **NO / unmeasured** |
| G10 pts power, movers K = 10, binding half-width of an informationless shock (sigma 0.05; seeds 1-6 range) | 0.0258 (0.013-0.026) | 0.0137 (0.0125-0.0241) | <= 0.005 | **NO, every seed** |
| G10 pts, movers sigma 0.10 | 0.0570 | 0.0276 | <= 0.005 | **NO** |
| G10 reb / ast / fg3m, movers sigma 0.05 | 0.0066 / 0.0063 / 0.0032 | 0.0053 / 0.0031 / 0.0021 | <= 0.003 / 0.002 / 0.001 | **NO, NO, NO** |
| G10 movers pooled 2023+2024 pts / reb / ast / fg3m (sigma 0.05) | 0.0128 / 0.0042 / 0.0035 / 0.0013 (n 2,227) | | as above | **NO** |
| G11 large-change pts (sigma 0.03 / 0.05) | 0.0076 / 0.0130 | 0.0058 / 0.0101 | <= 0.005 | **NO** |
| G11 large-change reb / ast / fg3m (sigma 0.03) | 0.0023 / 0.0018 / 0.0010 | 0.0022 / 0.0015 / 0.0007 | <= 0.003 / 0.002 / 0.001 | yes at sigma 0.03; reb and ast fail at sigma 0.05 (0.0039 / 0.0030; 0.0037 / 0.0026) |
| G12 everyone pts / reb / ast / fg3m (team-game shock sigma 0.03) | 0.0028 / 0.0010 / 0.0007 / 0.0004 | 0.0027 / 0.0010 / 0.0007 / 0.0003 | <= floor | yes (guard slice is powered) |
| season 2025 loaded | no | no | never | yes |

Reading the power rows. Shocks are shared within player-season (movers) or team-game (the other slices); the half-width of the informationless-shock CI is the smallest benefit the test could separate from zero. Rows needed for the floor (= n x (hw / floor)^2, from gate.json, sigma 0.05, single season): movers pts 7,700-32,000 (held 1,025-1,202), reb 3,200-5,800, ast 2,500-11,800, fg3m 4,400-11,900; large-change pts (sigma 0.03) 5,200-9,400 (held 3,854-4,068). The mover half-width itself is noisy with 115-136 players (0.013-0.026 across seeds); the median, about 0.016, matches T504 (0.0167 / 0.0170 on 841 / 730 salary-line rows).

**Data-capture audit (non-negotiable 8; before declaring the gate failed on data).** Held: `nba.duckdb` 2022-2024 regular season; `data/history` 2013-2021 box scores (not read: no stored A0 `model_rows`, so earlier seasons add warm-up, not select/report seasons, unless a third and fourth season of A0 is refit); preseason logs 2022-2026, 30 of 30 teams; T-60 official reports with real tips; `odds_history.duckdb` totals/spreads at t60 and t5; the live lineup feed 2026-10-09 onward (45,314 snapshots, 57 tips). Not held and addable: (i) announced fives / inactive lists for 2023-24: not recoverable (no archive of the feed; check the Wayback Machine for nba.com daily_lineups: unverified, ToS to check, cost unknown); (ii) transactions (trade/signing dates): NBA.com via nba_api about 150-300 requests per three seasons, free, ToS to check (Pro Sports Transactions is behind Cloudflare, not circumvented, DECISIONS 2026-10-12); lifts placeable later arrivals from 11-16% toward ~100% but changes the slice; (iii) 2019-21 A0 refit from `data/history`: three more seasons of mover rows, about 3,300 (estimate: 1,100 per season; history has the box `starter` flag, not verified for these seasons; no preseason logs before 2022, so V_c unavailable there, transactions needed); cost one heavy walk-forward refit on Devin's Mac (about 95 minutes for the previous full run) plus the transaction pull; (iv) a season of live T-15 capture (2026-27): free, already running; movers K = 10 about 1,100 rows over the whole season at the held rate (scorecard 2026-10-21: new team n = 268 over 13 game-days), logged share 0.64, so about 700 rows by season end. **Combined best case for movers** (2023-24 + 2019-21 + 2026-27 live) is about 5,500-6,000 rows; scaling the pooled half-widths by sqrt(2,227 / 5,750) = 0.62 gives pts about 0.008 (sigma 0.05), reb 0.0026, ast 0.0022, fg3m 0.0008: **reb and fg3m would pass, ast borderline, pts still fail.** So Part A's pts claim cannot be tested on any data we can plausibly hold; the sample is limited by how many players change teams, not by effort. If Part A is kept at all it should be on reb / fg3m (mover role mostly shows in minutes and threes) and be declared a secondary claim.

## Options for Devin (each with Claude's default)
1. **Do not freeze the historical rule; run a forward shadow (default).** Log CS beside the T-15 arm for every logged game (no fit, no production change, touches `nba/daily`, so not this branch), report movers and large-change slices descriptively with their CIs and no verdict until a separate final-look rule; Part B's historical test is not run because lineage predicts harm.
2. **Freeze Part B only on history** (large-change and everyone slices, proxy-capped, "PROCEED TO SHADOW" or "CLOSE"): powered, but needs G9 first; expected null or harm (see priors). Costs a refit of the T-15 stand-in (heavy, Devin's Mac).
3. **Freeze Part A only on reb / fg3m movers after adding 2019-21 and a transaction pull** (cost above): the only route to a powered mover test.
4. Close (T509's recommendation stands).

## Kill criteria (if frozen and run)
G6/G9/planted-future/sum-identity/positive-control failure: stop, no result, new id. No CS cell clears 2023: close; no variants. A cell that clears 2023 and fails 2024 is FAIL_ONE_SEASON. Beats O: presumed leak, audited. A mover gain carried by <= 5 players: WOUNDED, not a pass. **At most ONE follow-up**: the T-10 fallback timing as a new id, or the reb/fg3m Part A after data repair; then the family (TEAM_BUDGET -> TEAM_USAGE_CAP -> this) is closed. No 2025 touch. A pass earns a drafted holdout row and a live-shadow recommendation only; promotion is Devin's.

## What each choice protects against
T-15 and confirmed fives: the T-60 version's overcount was players who sit. Box starter proxy labelled and stint ban: realised lineups used as announced ones would leak. Available set not built from the scored rows: membership leakage. One-sided cap with floor 0.8: TEAM_BUDGET raked two-sided and harmed. A3: same pre-game information, no structure. A4: a factor that wins with the wrong team's number is a recalibration. Three clusterings, widest binds: mover factors persist across a player's ten games and a team's. Per-stat floors: CRPS scales differ. Integer grid for all arms: T114-T118 artifact. Top-5 guard: T500's gain was five players.

## Playbook check (MODEL_PLAYBOOK)
Class: GBT context-residual output with a fixed blend and a set/joint consistency layer.
1. Information or capacity? Part A adds a team-level vacated-role scalar (weak information); Part B is capacity/structure; the real new information (confirmed fives) is already in the T-15 arm.
2. Enough data for this class? A fixed layer needs none; the TEST lacks data: movers 1,025-1,202 rows per season against 2,500-32,000 needed.
3. Test valid? Not yet: no announced-five history (proxy), T-15 stand-in comparator unbuilt (G9), vacated-role signal nearly constant across teams (G3), positive control and synthetic-outcome dry run specified but unbuilt (stopped first). A noisy slice can create a null, so a run now would not be a clean null.
4. Lineage: TEAM_BUDGET T446-T449 (HARM), TEAM_USAGE_CAP T509 (stopped at gate), F11 (null), LINEUPS_KNOWN T085-T090/T096-T097 (proxy-optimistic), PRESEASON_ROLE_PRIOR T500 and ROLE_PRIOR_SALARY T504 (movers, power), RETURNING_TEAMMATE_LIVE_T30. Guards inherited: integer grid, widest-binds clustering, oracle bound, top-5, past-only placebo, BH.
5. Scored against: integer CRPS vs the T-15 arm and A3; no claim about lines or ROI (CRPS gains have not reached the line, T477).
Class checks: missingness audit by realised-minutes bucket; planted-future on V and the available set; does the arm only re-express the base? B rescales A0 by one team-game factor: yes unless it beats A3.

## Priors (for docs/PREDICTIONS_LEDGER.md, written before any result)
* P(rule freezable as drafted) 0.05. P(Part B power-only rule freezable after G9) 0.6.
* If run: P(CS clears any cell in BOTH seasons) 0.05; P(S alone clears pts on large-change in 2023) 0.10; P(C clears a mover cell in 2023) 0.12. Expected CS dCRPS on movers, pts -0.004 (80% -0.030 to +0.020); S on large-change, pts +0.004 (80% -0.004 to +0.025: harm more likely than gain); everyone guard breached 0.2.

## Open choices for Devin (defaults)
1. Option 1 vs 2 vs 3 vs 4. Default: 1 (forward shadow, no verdict), then 3 only if a transaction pull is approved.
2. Transaction source (NBA.com via nba_api, free, ToS check, about 150-300 requests). Default: not now.
3. 2019-21 A0 refit (about 95 min heavy, Devin's Mac). Default: not now.
4. Floors per stat as above vs a looser mover-slice floor. Default: as above (a floor chosen to pass the gate is gaming).
5. T-10 fallback as its own rule. Default: yes, after the T-15 shadow shows its logged share.

=== RESULTS BELOW ===
(none: no arm fitted, no outcome read)
