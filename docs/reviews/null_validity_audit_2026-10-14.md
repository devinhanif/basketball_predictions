# Null validity audit: did a known data defect change the answer of any closed test? (2026-10-14)

Adversary, read-only. Asked by Devin through the main session: "earlier nulls may rest on flawed data". Scope: every CLOSED ledger
test (null, lost, tied, FAIL, or stopped at a gate), T001-T486. Lens: **data defects only**. Design flaws (training window, harness
placebos, mis-sized guards, weak comparators) were classified in `docs/reviews/validity_audit_2026-10-12.md` and are not repeated
here, except where this audit disagrees with it. Rule applied: "the data had a bug somewhere" is not a reason. A defect counts only if
the defective field was an input to the mechanism of the arm that won or lost, at a share and in a direction that could change the
answer.

Nothing was fitted. Season 2025 was not loaded (every query filters `season <= 2024`, or reads stored 2023/24 arrays). Every DuckDB
connection was `read_only=True`. Scripts are in the scratchpad, not in `nba/`.

Verdict: APPROVED

Overall: the closed null record **SURVIVES** the data-defect lens. Of 59 closed test groups, **56 STAND**, **2 warrant a re-test**
(F12 closing risk T185, RAPM T176; both on pre-v2 stints), **1 is UNKNOWN** (PBP_GPT P1/P3, T082/T084; play-by-play order). No
data defect plausibly turned a real win into a recorded null anywhere else, mainly because (a) the defect was shared by both arms, (b) it
flattered the arm that lost anyway, or (c) its share of rows is 10-1000x too small for the margin.

**Single most likely failure mode if this audit is wrong:** a research harness built its features from a cached frame that predates a fix
by more than its run time suggests (the ledger records no data_version per run; I inferred versions from commit and changelog times).
XGB_PTS_VS_LT (A1 from the frozen 2026-10-09 exp-3 frame) is the known instance; there may be others I did not trace.

## 0. Coordinator add (high priority): the 2024 production OOF mismatch

**Finding: expected consequence of the F2 fix (10 neutral-site games restored, 2026-10-10 18:56 CDT), not a defect.** Re-derived:

| check | command (scratchpad, all exit 0) | n | result |
|---|---|---|---|
| stored 10-09 OOF = stored 10-09 A0 arrays | `q4.py`: `reports/context_residual_realtip/oof_context_residual.parquet` (pts `mean`) vs `reports/minutes_v2/props_pts_A0.npz` (`mean`), both written 2026-10-09 | 55,202 rows | 100% equal |
| stored 10-09 A0 vs post-F2 A0 | `q5.py`: 10-09 A0 vs `data/minutes_hazard_2/props/props_pts_A0.npz` (2026-10-10 20:25) and `data/history_window_3/arm_A0_pts.npz` (2026-10-11 21:33), keyed (game, player, season) | 27,619 (2023), 27,583 (2024) common rows | 2023: **100.00%** equal; 2024: **20.09%** equal |
| by month, 2024 | same | Oct 1,407; Nov 4,624; Dec 3,970; Jan-Jun 17,582 | Oct 100% equal; Nov 89.3%; Dec 0.03%; Jan-Jun 0.0-0.1%. First mismatching date 2024-11-04 |
| post-F2 references agree with each other | MH2 A0 (10-10 20:25) vs HW3 A0 (10-11 21:33) | 55,202 | identical match pattern and values (both 20.09% vs 10-09) |
| size of the change | mean integer CRPS on the 27,583 common 2024 rows | 27,583 | 3.188411 (10-09) -> 3.188477 (post-F2), **+0.000066** |

Mechanism: the first restored 2024 game is 0022400147 (MIA@WAS, Mexico City, 2024-11-02). As-of features of the players of those teams
change from their next game (11-04: the 89% November match). Production refits per month, so from the December fold on, every model
is trained on rows that include the restored games and every 2024 prediction moves (the 0% match from December). 2023 is untouched
because no 2023-24 game was missing. The match share the combo modeler saw (~20%) is exactly this pattern (20.09%). Fixes that do
**not** contribute: the possession-order rebuild (10-10), the stint rebuilds F4 / F4b (10-10, 10-11) and the report game_id resolution F2b
(10-11) all touched 2023 too, and 2023 matches to the bit, so production props read none of them (F2b's 6 rows are on the restored
game itself). MH2's A0 (10-10 20:25, after F2 and F4, before F4b) equals HW3's A0 (10-11 21:33, after F4b, F2b and the coach reload).

Which tests compared against which 2024 reference:
- **Before F2 (runs up to 2026-10-10 18:22 CDT): internally consistent.** Both arms were built on the pre-F2 data of the day; 2024 n =
  27,583. LOWER_TAIL, PTS_TAIL, MINUTES_V2, F9b, ODDS_HISTORY, REFEREES, F11, F8, FOUR_FACTORS, PTS_COMPONENTS, ROUTER_TIME_WEIGHTED,
  MINUTES_HAZARD gate. Their "A0 reproduces LOWER_TAIL to 0.0" checks are valid on that day's data.
- **After F2, recomputed A0 on current data (2024 n = 27,701): consistent.** MINUTES_HAZARD_2, PPLAY_CONTEXT, OWN_TAG (check 4 amended
  pre-freeze to the reference key set, DECISIONS 2026-10-11 15:05), MARKET_LINE_CLASSIFIER, GAME_MARKET_CONTEXT ("A0 reproduces the
  re-baselined reference exactly"), HW3, ADVANCED_RATES, ENSEMBLE, TEAM_BUDGET, WORKLOAD, RECENT_ROLE*, ROUTER_G2, ROAD_STARS,
  ERROR_FEEDBACK. The re-baselined reference is `reports/lower_tail/components_*.npz` / `candidates.json` (DATA_CHANGELOG 2026-10-11
  15:11 CT, 2024 n 27,701, pts 3.187553).
- **Used the stale 10-09 reference:** COACH_PHASE (T450: its frozen text required 1e-6 reproduction of the stored 10-09 minutes table;
  the "drift from game 0022400155" is this same F2 effect, so gate item (a) was a stale-reference stop). XGB_PTS_VS_LT (T382-T388) pairs
  an A1 built from the frozen 10-09 exp-3 frame (pre-F2, 2024 n 27,583, "check 5 key intersection, 118 restored rows") with A0;
  mixed data versions on 0.43% of rows plus 5 games of training data, against a +0.000066 reference shift. Neither changes an answer
  (section 2).
- **Stale stored arrays still on disk:** `reports/context_residual_realtip/oof_context_residual.parquet` and `reports/minutes_v2/*.npz`
  are 10-09 (pre-F2). Anyone reproducing 2024 production must compare against a post-F2 A0 (e.g. `data/history_window_3/arm_A0_*.npz`),
  not these. The combo modeler should expect 100% / 100% there; anything less would be a real defect.

## 1. Defect timeline (what was wrong, when it was fixed, what it fed)

The ledger records no data_version per run. Versions below are inferred from DATA_CHANGELOG times (UTC converted to CDT) and the result
commit times in `git log -- docs/TEST_LEDGER.md`.

| # | defect | fixed (CDT) / data_version | consumers that matter | status now |
|---|---|---|---|---|
| D1 | report gating on a 19:00 ET proxy tip (admits post-tip reports, ~7.7% of games) | production 10-09 08:08 (76cc177); library default 10-12 02:37 (4ea569a) | every report-reading arm before; SEQ / ctx-v2 / game_sets research exports until 10-12 | fixed; shared or flatters the candidate (never creates a null) |
| D2 | stints from the v1 tracker: 47-49% of player-games within 1 min of box; offence five equal to the v2 tracker on 80.2% of possessions | 10-09 20:10 (e9b76c58), 957 negative stints fixed 20:48 (ff1db81f) | RAPM, F12, T042, the sim, rung-4, `on_poss` in ctx-v2 features | fixed |
| D3 | possessions in `action_number` order: 5,789 trips end after they start, 4,258 of 5,269 games affected | 10-10 09:55 (85d8a412) | PBP_GPT tokens, rung-4 durations, sim transitions, ctx-v2, RAPM, WINPROB_FAMILY GBM features | fixed |
| D4 | `possessions.oreb` = 1.33x box OREB (team / dead-ball rebounds) | 10-10 14:17 (1c5b0901) | sim, rung-4 rebound head; F8 used the new `n_oreb` (did not exist before) | fixed |
| D5 | 10 neutral-site games missing (5 in 2024) | 10-10 18:56 (F2) | every 2024 row from 2024-11-04 (section 0) | fixed |
| D6 | "Missi Free Throw" read as a miss (79 games, all Yves Missi, 2024) and 2 team-0 trips | 10-10 19:11 (F6/F7) | possession points/pace in those games (FOUR_FACTORS ran 17:15, before) | fixed |
| D7 | stints missing short appearances (40.2% of < 1 min, 21.3% at 1-2 min) | 10-10 19:14 (F4, 671 -> 91 at >= 1 min) and 10-11 03:26 (F4b same-surname, 91 -> 1) | F11, F8 (ran before F4), MINUTES_HAZARD gate (found it), MH2 (after F4, before F4b) | **fixed for 2022-24**: 0 of 858 / 1,685 / 4,873 / 6,211 / 70,543 played player-games (< 1, 1-2, 2-5, 5-10, 10+ min) lack a stint row today (`q1.py`). The brief's "still open" is out of date; the one residual is a 2025 game |
| D8 | players_static first_season / draft_status NULL for 204 legacy rows | 10-10 19:56 (F10) | F9b (ran 10-10 01:03) | fixed; F9b not exposed (section 2) |
| D9 | `player_availability` NULL game_id 336 -> 188 (F2) -> 167 (F2b, 10-11 03:27) | 167 still NULL: 156 postponed games, 7 ambiguous, 4 Cup final | every report reader, both arms | open, 0.14% of 118,238 rows, mostly stale reports for postponed games |
| D10 | `team_coaches` wrong in 14 of 87 team-seasons (11 = next season's coach) | `team_coach_games` from Basketball-Reference 10-11 21:31 | MH2 (keys team-season only), TEAM_BUDGET and COACH_PHASE (read the fixed table) | superseded table |
| D11 | history injury reports: early-2019 layouts unparsed; layout-C reason text polluted | 10-11 11:04 and 13:35 | HISTORY_WINDOW gate T315 (09:46) | fixed |
| D12 | history players_static empty before 10-12 20:34 (survival pattern) | 10-12 20:34 / 21:33 | W_ALL SEQ exports only (322 training targets, 0 eval rows) | closed by `history_position_audit_2026-10-13.md` |
| D13 | history bare "Free Throw k of n" counted as a make (41 rows, 2013-16) | 10-13 12:13 | T485 only (not closed) | fixed |
| D14 | `game_officials` 4 duplicate ids | 10-10 13:34 | REFEREES (gate caught it, run after) | fixed |
| D15 | **not in the brief, still open:** `possessions.assister_id` NULL for accented passers and same-surname teammates | open | ctx-v2 `ast_makes`, ADVANCED_RATES `ast_mk` (assisted share of a shooter's makes) | **open**, see below |
| D16 | **not in the brief, documented limitation:** no corner-3 zone (V3 PBP has no x/y; every three is `above3`) | open in `possessions`; x/y now in `shots` (loaded 10-12) | ctx-v2 `z_c3` (constant 0), ADVANCED_RATES SHOT_PROFILE | open |
| D17 | undocumented change between snapshots ff1db81f and d4963264 (10-10 14:46Z), per DATA_CHANGELOG | unknown | F9b (06:03Z) and REFEREES G0 (11:30Z) fall in the window | unknown; no mechanism found |

D15 numbers (`q1.py`, seasons 2022-24, exit 0): possessions carry 187,653 assisted makes against 205,704 box assists (91.2%). Ten
players with >= 300 box assists have **zero** attributed: 203999 (2,508 box ast), 1629029 (1,805), 203471 (1,260), 202696 (788),
203992 (579), 203994 (576), 202685 (489), 204001 (392), 203995 (390), 1629026 (305); the ids match Jokić, Dončić, Schröder, Vučević,
Bogdanović, Nurkić, Valančiūnas, Porziņģis (the parser matches the "(Name N AST)" suffix against a last-name lookup and loses
diacritics). Partial losses follow same-surname teammates (1630532 Franz Wagner 28.5%, 203507 Antetokounmpo 36.5%, 1630224 Jalen
Green 33.1%, 2544 James 74.5%). No closed verdict rests on it (section 2), but any future assist-network, playmaking or "assisted share"
feature from possessions inherits it. It needs a DATA_CHANGELOG entry and a parser fix (normalise diacritics, reuse the F4b alias
evidence).

## 2. Per-test verdicts (data-defect lens)

STANDS = no known defect touches the mechanism at a share and direction that could change the answer. RE-TEST WARRANTED = it does; one
pre-registered rule after 10-20. UNKNOWN = it touches the mechanism and I cannot bound it from stored numbers; the settling step is given.
"validity 10-12" quotes the earlier audit's label for design flaws.

| T rows | test (class) | ran (CDT) / data | defects present that touch inputs | does it touch the answer? | verdict |
|---|---|---|---|---|---|
| T001-T003, T183 | sim vs season-avg (possession sim) | <= 10-08 / pre-D2, D3, D4, D1 | D2-D4 in the sim's transition and lineup inputs | retracted for DNP handling (T062-T064), not for data; superseded | STANDS (superseded) |
| T004 | on-court usage denominator (linear) | <= 10-08 / pre-D2 | D2: the on-court denominator is the defective field | yes in principle, but the question was re-asked on fixed stints by ADVANCED_RATES USAGE_PACE (T424-T427, null) | STANDS (superseded) |
| T005-T033 | routing grids | <= 10-08 | DNP-label artefact (design), not data | no | STANDS (superseded) |
| T034-T041, T048-T060 | matchup adjust, M3A | 10-08 | none material; point-only | re-asked validly (RIDGE_V2, ADVANCED_RATES DEFENSE_CONTEXT) | STANDS (superseded) |
| T061-T063 | sim pts @2000; fair rematch pts, reb | 10-08 / pre-D2-D4 | D2-D4 feed the sim | sim aggregate bias was small (pts -0.01, reb -0.12; DNP_AUDIT, 600 games), so the extra-possession route of D4 did not create a bias; losses +0.19 / +0.268 / +0.123 are 25-50x any floor | STANDS |
| T064 | fair rematch ast (sim) | 10-08 | D2, D3; not D15 (sim assist rates come from box: `player_rebound_assist_features` reads `player_game_stats`) | +0.0143 [+0.0052, +0.0229]; the sim's ast bias (+0.29) is attribution, and pts bias ~0 rules out a possession-count cause | STANDS (validity 10-12: UNCERTAIN on design) |
| T065-T072 | SEQ alone / blends (neural) | 10-08 / D1 | D1 flatters SEQ; D3 pre-fix possessions in the export | D1 cannot create a loss; D3 share ~0.5% of trips | STANDS (data); validity 10-12 INVALID for training window, a design flaw, not re-judged here |
| T073-T080, T102-T113 | CTXRES V2 / V3 | 10-08/09 | leak, support mismatch, D1-D3 | already superseded by the leak and support restatements | STANDS (superseded) |
| T081 | JOINT set transformer (set) | 10-08 / D1 | D1 via game_sets (shared) | tie, no defect reaches it | STANDS |
| T082, T084 | PBP_GPT P1 live win, P3 live player lines (PBP generative) | 10-08 / pre-D3, pre-D4 | **D3 is the token grammar itself**: 81% of games carry at least one displaced trip; D4 if rebounds are tokenised | P1 loses by +0.020..+0.038 LL at every checkpoint. Whether the score/clock baseline read the same mis-ordered stream (shared, harmless) or the box/feed score (GPT-only damage) is not recorded | **UNKNOWN**. Settle: read `research/features/pbp_tokens.py` and the P1 baseline's state source (minutes, no fit); if the baseline reads the same stream, STANDS; if not, count checkpoint states whose score differs between action-number and clock order in 2024 (read-only, minutes) |
| T083 | PBP_GPT P2 pregame | 10-08 | D3 only in training history; target game not tokenised | +0.0875 [+0.0403, +0.1339], n 150 | STANDS |
| T098-T101 | RIDGE_V2 leak-free (linear) | 10-09 01:00 / pre-D2, pre-D3 | none: the ridge is player + opponent-team effects on per-36 **box** rates (`opponent_ridge_v2.py` docstring and line 195), no lineup or possession input | no | STANDS |
| T114-T117 | V3 fair restatement | 10-09 | D1, D2 in v2 features (flatter / blur the loser) | the losing arm was flattered | STANDS |
| T119-T123, T174-T175 | TRACKING / hustle screen (GBM) | 10-09 06:27 / 18:20 | hustle row existence depends on minutes, but only for **past** games (knowable pre-tip) | nulls 4x inside the floor | STANDS |
| T129-T135, T136-T159 (non-pass cells) | PTS_TAIL, LOWER_TAIL (quantile layer) | 10-09 08:33-08:45 | none beyond D5 (shared) | no | STANDS |
| T160-T161 | INJURY_ELO_T30 (ratings) | 10-09 11:00 | D9 (336 NULL game_id rows then, both arms) | 0.3% of report rows, shared | STANDS (underpowered per validity 10-12) |
| T162-T173 | MINUTES_V2 props cells | 10-09 11:16 | D5 (shared) | 2024 pts -0.0018 vs floor -0.005; D5 is 118 rows and moves A0 by 0.00007 | STANDS |
| T176 | RAPM in injury-Elo (linear ratings) | 10-08 / **pre-D2, pre-D3** | **D2 is the design matrix**: `rapm.py` reads `possessions.off_players/def_players`; 1 in 5 possessions carried a different offence five than the v2 tracker; D3 displaced trips | +0.0032 [-0.0003, +0.0066], MDE ~0.0035. A one-in-five lineup error biases every coefficient toward the team mean, the exact way RAPM would lose to a box value. Direction: against the candidate | **RE-TEST WARRANTED** |
| T177 | rung-4 step heads (neural) | <= 10-08 | D3 (negative-duration trips) and D4 (inflated oreb) are the **targets** of the duration and rebound heads | not kept for no comparator / not routable; consumer (sim) dead. The answer does not depend on the data | STANDS |
| T178 | WINPROB_FAMILY (Elo / GBM / MLP) | <= 10-09 | D3, D4 in GBM team features (near-uniform scaling for D4) | stack failed only its playoffs slice; Elo unaffected | STANDS |
| T184 | F9 gate | 10-09 19:52 | missing draft_status column (schema gap) | superseded by F9b, which ran | STANDS (superseded) |
| T185 | F12 closing risk gate (descriptive) | 10-09 19:54 / **pre-D2 (v2 at 20:10)** | **D2 is the gate**: check 1b failed on stint-vs-box reconciliation 48.1% (needs 95%; today 99.97-99.98%); the premise check (young players Q4 vs Q1-3 floor ratio) read the same stints | the gate answer certainly changes at 1b; the premise (-31.3%, fail) is unknown | **RE-TEST WARRANTED** |
| T186-T201 | F9b young-pick prior (GBM) | 10-10 01:03 / ff1db81f, pre-D8 | D8: first_season NULL for 27 / 81 players (174 / 3,658 played rows, 2023 / 2024, many 2024 rookies) | no: `f9b_young_pick.py` falls back to draft_year then first game; all of those rows had draft_year and draft_pick in the pre-F10 backup (`q6.py`, exit 0) | STANDS |
| T202, T238-T243 | REFEREES gate and run | 10-10 06:30 / 13:45 | D14 found by the gate, fixed before the run | no | STANDS |
| T203-T237 | ODDS_HISTORY | 10-10 13:28 / 85d8a412 | D5 (pre-F2), D9 | market better by +0.007..+0.024, CIs above 0 | STANDS |
| T244-T251, T252-T263 | F11 lineup context, F8 lineup rebounding (GBM) | 10-10 14:16 / 16:56 / v2 stints, **pre-D7** | D7: 1,106 played player-games without a stint row (0.98%), nearly all < 2-min garbage-time subs | past-only lineup features of rotation players; powered nulls (MDE 0.0006-0.0054) with valid oracles; F8 used `n_oreb` (post-D4) | STANDS |
| T264-T279 | FOUR_FACTORS (GBM) | 10-10 17:15 / pre-D5, pre-D6 | D6 in 79 NOP games' possession boundaries; factors use box (`b_oreb`) | best cell -0.0004 vs -0.005 | STANDS |
| T280-T283, T284-T299 | PTS_COMPONENTS, ROUTER_TIME_WEIGHTED | 10-10 17:22 / 17:26 | D5 (shared) | +0.03 harm; router = best single | STANDS |
| T300 | MINUTES_HAZARD gate | 10-10 18:22 | D7 is what the gate found | fixed, re-asked as MH2 | STANDS (superseded) |
| T301-T314 | MINUTES_HAZARD_2 (hazard GBM) | 10-10 20:31 / post-F4, pre-F4b | D7 residual 91 player-games (>= 1 min); D10 irrelevant (coach-season keyed by team-season) | +0.55 / +0.58 min worse; 91 rows cannot move a 27,619-row mean by 0.5 min | STANDS |
| T315, T325 | HISTORY_WINDOW gate, HW2 | 10-11 09:46 / 15:14 | T315: **D11 caused gate G0.4** (82% coverage); D12 survival pattern in static columns | T315's gate is invalid as a measurement, but HW2 re-ran on repaired reports and still stopped, and HW3 answered the box-score question; the stops were on report columns, not static ones | STANDS (superseded) |
| T316-T317 | PPLAY_CONTEXT | 10-11 10:52 / 418510d4 | D9 | failed on the team-habit placebo, not on reports | STANDS |
| T318-T319 | ELO_HISTORY_WINDOW | 10-11 10:56 | none (game results) | no | STANDS |
| T320-T324, T339, T340-T381, T415-T419 | MARKET_LINE, CALIBRATED_GRID, MODEL_CLV, MARKET_SHRINK | 10-11 15:11-21:35 | D5 reference staleness at most (+0.00007 CRPS) | external market benchmark; no | STANDS |
| T326-T338 | OWN_TAG | 10-11 15:18 | D9 (0.14%), stale reference handled pre-freeze | no | STANDS |
| T382-T388 | XGB_PTS_VS_LT (GBM) | 10-11 17:05 / A1 frozen 10-09 frame | D1, D2 (`on_poss`), D3, D15 (`ast_makes`), D16 in A1's v2 features; D5 mixed versions | flatters or blurs the arm that tied; the tie is the < 5-min half (+0.16), unrelated to these features | STANDS |
| T389-T414 | GAME_MARKET_CONTEXT | 10-11 17:28 / 418510d4 | none | no | STANDS |
| T420-T423 | HISTORY_WINDOW_3 | 10-11 21:47 | D12 handled (`static_with_cache`, 0 played rows without static) | no | STANDS |
| T424-T439 | ADVANCED_RATES (GBM) | 10-11 22:12 / f67c63f3 | D15 (`ast_mk`, one column of the shot families), D16 (no corner zone) | null on 15 cells, best -0.0024 / -0.0030; one degraded column cannot carry a family to -0.005. Caveat: "shot profile with corner threes" was never tested; the `shots` table now has x/y | STANDS |
| T440-T445 | ENSEMBLE (neural) | 10-11 22:37 | D1 (flatters SEQ), D12 handled | no data defect reaches it | STANDS (data); validity 10-12 INVALID on design |
| T446-T449 | TEAM_BUDGET | 10-11 22:51 | D10 fixed 21:31, before the run | harm +0.10 | STANDS |
| T450 | COACH_PHASE gate | 10-12 00:07 | gate (a) stopped on the D5 stale reference (section 0) | gate (b), the habit-fallback missingness gap 5.8 pp vs 2 pp, stops it independently | STANDS |
| T452-T453 | WORKLOAD pts, M | 10-12 00:18 | none | (validity 10-12: pts INVALID for the placebo, a harness flaw) | STANDS (data) |
| T455-T456, T460-T461 | RECENT_ROLE, PI_RECENT_ROLE (near-misses by 0.0004) | 10-12 00:40 / 01:36 | D9 only (reports, shared; the mechanism is box minutes) | a 0.0004 miss is the one place a small defect could flip a verdict; no defect touches box minutes | STANDS |
| T464-T466 | ROUTER_G2 | 10-12 02:23 | D1 in SEQ member (flatters) | no | STANDS |
| T472, T475, T481 | PACE_TOTALS, ROAD_STARS, ERROR_FEEDBACK | 10-12 10:16 / 12:46 / 23:38 | none current (D12 fill done before T481) | no | STANDS |
| T473 | RECENT_ROLE_FULL stage 2 | 10-12 10:22 | none | (validity 10-12: INVALID as a negative, guard sizing) | STANDS (data) |

Out of scope: T042, T085-T097, T451/T454, T458/T462, T478/T479, T482/T484 (positive results); T485/T486 (open follow-up; exposed to
D12 for 2015-18 and D13, already listed in its own review); T047, T179 (not tests); descriptives.

**Counts (59 closed groups; a table row with several rule ids counts once per rule):** STANDS 56 (of which 11 "superseded": the question was re-asked on fixed data), RE-TEST WARRANTED 2,
UNKNOWN 1.

**Where this audit disagrees with validity_audit_2026-10-12:** (1) RIDGE_V2 needs no lineup caveat (box-based). (2) T064 is not
UNCERTAIN on data grounds: sim assist rates come from box, and the possession route cannot produce a pts bias of ~0 together with an ast
loss. (3) Its F7 row lists "short appearances missing from stints" as open on 1bee33d0 / 418510d4; for 2022-24 it is fully closed today.
(4) It does not list D15 or D16.

## 3. Re-test candidates, ranked by likely impact

| rank | test | defect | why it may change | first step and cost | venue |
|---|---|---|---|---|---|
| 1 | T185 F12 closing risk | pre-v2 stints (48.1% within 1 min) | gate 1b now passes on 99.97-99.98% reconciled stints; the young-player premise (-31.3%) was read from the same stints and garbage-time minutes the v1 tracker mis-assigned | re-measure checks 1a/1b and the premise on current stints, seasons 2022-24, with garbage time defined before running; descriptive, Mac CPU, minutes; F12b frozen only if the premise holds (non-negotiable 8) | 2022-24 descriptive |
| 2 | T176 RAPM in injury-Elo | pre-v2 lineups (offence five wrong on ~20% of possessions) plus pre-order trips | lineup error shrinks RAPM toward the team mean, the direction in which it lost; prior from later null lineup tests (F11, F8) is low, and MDE ~0.0035 at n 3,951 means only a large effect can show | power check first (the floor must be reachable at n 3,951 games, OOF 2022-24; add 2019-21 history possessions as a prior only if a data-capture audit admits them); rebuild RAPM on today's stints; Mac CPU, about an hour | 2022-24 OOF; RAPM is virgin on 2025 |
| - | T082/T084 PBP_GPT (UNKNOWN) | play-by-play order | settle by the code read in section 2; re-test only if the baseline read clean state and the GPT did not | minutes, read-only | - |

## 4. Checks run (commands, n, result, exit code)

| check | command | n | result | pass |
|---|---|---|---|---|
| stints coverage today | `.venv/bin/python scratchpad/q1.py` (first run exit 1: `stints` has no `player_id`, fixed to `unnest(players)`; rerun exit 0) | 84,170 played player-games 2022-24 | 0 missing in every minutes bucket | brief corrected |
| assister coverage | `q1.py` | 205,704 box ast; 330,474 makes | 187,653 attributed (91.2%); 10 high-volume passers at 0% | new open defect D15 |
| shot zones | `q1.py` | 618,634 shots | above3 / mid / rim only; no corner3 | D16 confirmed |
| PA NULL game_id | `q1.py` | 118,238 rows | 167 | as documented |
| 2024 OOF mismatch | `q4.py`, `q5.py` (exit 0); `q3.py` (exit 0, but compared `row_c`, a mixture component, not the mean: discarded) | 55,202 common rows | section 0 | expected, not a defect |
| F9b exposure to D8 | `q6.py` plus inline query (exit 0) on `data/backups/players_static_pre_f10_20261011T005643Z.parquet` | 891 static rows; 3,832 played rows with NULL first_season | every one had draft_year and draft_pick (fallback correct) | not exposed |

The six attack checks (shuffled labels, planted future, missingness, knockout, replication, integer support) are for claimed wins; no
win is under review here. Bootstrap: none run; every CI quoted is the ledger's.

## 5. Playbook check (docs/MODEL_PLAYBOOK.md)

Classes touched: possession sim, linear ratings (RAPM, ridge), gradient-boosted trees (most rules), neural sequence and PBP generative
(SEQ, PBP_GPT, rung-4), set (JOINT), descriptive gates (F12, HISTORY_WINDOW).
1. **Information or capacity?** The two re-tests are information-quality questions: RAPM and F12 read lineups, and the lineups were wrong.
   No capacity re-test is proposed on data grounds.
2. **Enough data for the class?** RAPM is data-hungry (multi-season possessions with correct lineups); its first run had neither. Trees:
   not at issue for any data defect here.
3. **Test valid?** Invalid only where the defective field was the mechanism (RAPM design matrix, F12 reconciliation gate, T315 coverage
   gate). An invalid test is not a null: T176 and T185 are not cited against their ideas; T315 was superseded by valid re-asks.
4. **Lineage and inherited guards:** the stint and possession fixes each carry before/after gates in DATA_CHANGELOG; the assister field
   (D15) has none and no missingness guard would catch it (it is a value loss on named players, not a NULL by outcome).
5. **Scored against:** production A0 on integer CRPS for props; for references, the post-F2 A0, never the 10-09 arrays (section 0).

## 6. Not verified

- Per-run data_version (not recorded; inferred from times). The undocumented change D17 between ff1db81f and d4963264.
- PBP_GPT's baseline state source (the UNKNOWN).
- Colab notebook code for SEQ / JOINT exports (only `research/` and `nba/` were read).
- Whether any closed harness reused a cached feature frame older than its run commit, other than XGB_PTS_VS_LT.
- D15 names were matched by id from memory of NBA ids (players_static carries no name column); the counts are from the data.

## 7. Recommended changes (not applied)

1. DATA_CHANGELOG entry for D15 (assister NULL for accented passers; parser fix: normalise diacritics and reuse the F4b alias evidence;
   gate: possession assisted makes vs box ast per player >= 0.98 for every player with >= 100 assists) and D16 (corner zone from
   `shots` x/y). Owner: archivist / steward.
2. Record data_version in every future ledger row and in each harness's results JSON (this audit had to infer it).
3. Mark `reports/context_residual_realtip/oof_context_residual.parquet` and `reports/minutes_v2/*.npz` as pre-F2 (10-09) in a README or
   move them under a `superseded_pre_f2/` folder; point reproduction checks at a post-F2 A0.
4. Correct the open-defects list: stints short appearances are closed for 2022-24 (D7).
5. Two pre-registered re-tests after 10-20, in the order of section 3; settle the PBP_GPT UNKNOWN by a code read first.

## 8. Drafted rows (not appended)

Ledger notice (no T number consumed):
```
**2026-10-14 - NULL VALIDITY AUDIT, data defects (docs/reviews/null_validity_audit_2026-10-14.md; no row edited, nothing fitted, season 2025 not loaded).** 59 closed test groups: 56 STAND, 2 RE-TEST WARRANTED (T185 F12 closing risk and T176 RAPM: both read pre-v2 stints, offence five wrong on ~20% of possessions; not citable against their ideas), 1 UNKNOWN (T082/T084 PBP_GPT, play-by-play order; settle by code read). The 2024 production OOF differs from the stored 2026-10-09 arrays on 79.9% of rows (2023: 0%) because the F2 neutral-site restoration enters the monthly refits from December 2024; common-row CRPS +0.000066; expected, not a defect. New open defect: possessions.assister_id is NULL for accented passers (10 players with >= 300 box assists at 0%; 91.2% coverage overall); no closed verdict depends on it.
```

DECISIONS (draft):
```
2026-10-14 — Null validity audit (data defects) closed: the null record stands except T185 (F12) and T176 (RAPM), queued as one pre-registered re-test each after 10-20 (F12 first: its gate failed on the defect itself); PBP_GPT P1/P3 UNKNOWN pending a code read. Why: Devin, "earlier nulls may rest on flawed data"; only these three had the defective field inside the mechanism. What would reverse it: a cached pre-fix feature frame found under another closed harness, or the PBP_GPT baseline found to read clean state.
```

DATA_CHANGELOG (draft, for the archivist): D15 and D16 as in section 7.1.
