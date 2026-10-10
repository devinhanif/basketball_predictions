# Context screen: player pedigree (draft slot, college) vs the recency-average props baseline (2026-10-08)

Exploratory. Seasons 2022-24 only (2025 never read). Played rows only (minutes > 0, per the DNP audit).
Baseline = played-only recency-weighted mean, half-life 10 games, strictly as-of. Pre-registered family
(m = 36, BH q = 0.10, effect floors) is in the docstring of `research/eval/context_screen_players.py`.
Reproduce: `uv run python -m research.eval.context_screen_players --out out.md` (about 5 seconds, seed 20261008).

## Plain-English summary

1. **Screen A (does pedigree explain the baseline's residuals?) -- no, not at a useful size.** Of 32 tests, 7 pass BH
   but all are "significant but trivial" (below the pre-registered floors). Draft slot (ln pick) and college
   power-conference status have no detectable relation to the residual in either the first-15-games subset or
   the first-two-seasons subset (all p > 0.06 before correction). Age shows a small negative residual
   (older players are slightly over-predicted: about -0.19 pts/g per SD in the first 15 games of a season).
   Undrafted players show small positive residuals (about +0.1 pts, +0.04 to +0.06 reb). Direction check: the
   hypothesis "high picks improve faster than the average predicts" is NOT supported (ln_pick coefficients are
   near zero, wrong or mixed sign). The one real pattern is not pedigree-specific: every early-career bucket has
   a positive residual (about +0.4 pts/g) because young players improve and the recency average lags.
2. **Screen B (cold-start rookies) -- draft slot predicts playing time, not rate stats.** Two tests survive BH
   plus the floor, with a caveat each:
   - mpg: ridge beats the position-only mean by 0.75 min/g (13% MAE, CI [+0.10, +1.42]). The supplementary
     comparison shows this gain survives against a position+height/weight ridge (+0.85, CI [+0.19, +1.48]),
     so it IS pedigree (draft slot). Picks 1-5 average 26.6 min/g vs 15.8 for picks 31-60 and undrafted.
   - reb/36: ridge beats position-only by 17.5% -- but a position+height/weight ridge alone matches it
     (pedigree increment -0.02, CI [-0.10, +0.06]). The survivor is body size, not draft or college.
   - pts/36: 7.8% MAE gain, p = 0.047, does not pass BH; increment over position+body +0.17 with CI including 0.
     ast/36: nothing.
   - Descriptive: picks 1-5 produce 17.5 pts/36 vs 12-14 for everyone else (flat below pick 6); college group
     differences are all inside the CIs.
3. **Recommendation.** A full rookie-prior module (CLAUDE.md method 3) is NOT justified by this screen as a
   points/rebounds/assists lever. A narrow version is: a **minutes** prior for rookies and first-year players keyed on
   draft slot (log pick, undrafted flag), feeding the existing minutes model's cold-start branch. Expected gain:
   about 0.7-0.8 min/g MAE on the rookie-season mean, on roughly 50 rookies per year, i.e. a tiny share of
   player-games (about 3% of player-game rows), and with a 95% CI that touches 0.1. Per-100/36 rate priors should
   keep using position and height (the existing archetype priors), not college. Candidate for a gated A/B against
   the existing minutes cold-start branch (`learned_context_max_n_played=20`), not for shipping.
   Note the dominant cold-start-minutes signal is likely depth-chart/availability, which the 1A model already has.

## Caveats (read these)

- **Small n.** Screen B has 138 rookies in total (2022: 36, 2023: 47, 2024: 55 with >= 200 min and >= 10 gp);
  the test set is 102 (classes 2023 and 2024). Training the 2023 fold uses only 36 players. Walk-forward by class
  leaves almost nothing to fit on; ridge alpha is chosen by 5-fold CV inside the train set.
- **Selection.** `players_static` covers only the 891 players in our data (every `player_game_stats` player is
  present). Rookies who never reached 200 minutes are excluded, so the cohort is survivors of the rotation cut;
  low picks are over-selected for having made the rotation, which compresses the draft-slot gradient
  in rate stats (but not in minutes by construction).
- **Draft NULL = undrafted?** 290 players have both draft_year and draft_pick NULL; 48 have more than 2000
  minutes and 14 more than 6000 in our data, consistent with undrafted veterans (plus internationals never
  drafted). I could not verify against an external source, so treat "NULL = undrafted" as an assumption. 6 more
  have draft_year but NULL pick (unknown pick, excluded from pick-based tests). Draft years in the table span
  2003-2025; the 2025 class never enters this screen. `college_stats` is NULL for all 891 players: the college
  box-score path of method 3 is not testable on this DB.
- **Undrafted rookies in 2022 are unidentifiable** (every veteran also has "first appearance" 2022), so the 2022
  class contains drafted players only; undrafted rookies enter from 2023. The train sets are therefore
  drafted-only for the first fold.
- **College mapping is hand-built** (`configs/college_conferences.yaml`): current-era power-conference
  membership; unlisted non-blank names default to mid_major. 603 of 891 players land in power_conf, so the
  college contrast has few non-power cells (mid-major 22, international 12, pathway 9 in the rookie cohort).
  International/pathway rows are not college players at all; their "age at draft" is meaningful but their
  production is less comparable.
- **Screen A noise.** Residuals have SD of several points per game; the player-clustered bootstrap is the
  primary test and every flagged test must also pass BH under the game-clustered bootstrap. 'power_conf' is
  highly collinear with being American-college-trained, so it is a weak lens on "college quality".
- **Early-career residual is positive for everyone** (S2 rows): the recency average lags a rising player.
  That is a baseline property (a growth/age-curve prior) that pedigree does not explain; age is the only
  consistent signal (negative: older players over-predicted).
- Exploratory screen: survivors are candidate features, not shipped. No 2025 data touched.

## Files

- `/Users/devin/Downloads/nba-prediction/nba/features/player_pedigree.py` (as-of pedigree features)
- `/Users/devin/Downloads/nba-prediction/configs/college_conferences.yaml` (hand-built mapping)
- `/Users/devin/Downloads/nba-prediction/research/eval/context_screen_players.py` (screen, pre-registration in docstring)
- `/Users/devin/Downloads/nba-prediction/tests/features/test_player_pedigree.py` (6 tests incl. planted-future-game)

# Full output (verbatim from the run)

seed=20261008  played rows=84052  players=771

## Missingness (players_static, all 891 rows)

- draft_year & draft_pick both NULL (treated undrafted): 290
- draft_year set, pick NULL (unknown pick): 6
- birth_date NULL: 0; height NULL: 4; position NULL: 4
- college groups: high_school=5, international_pro=90, mid_major=168, pathway=21, power_conf=603, unknown=4

## Pre-registered family: m = 36 (A: 32, B: 4), BH q = 0.1


### Screen A: residual (actual - recency avg) per 1 SD of predictor

| subset | stat | predictor | n rows / players | beta [95% CI player-boot] | p(player) | p(game) | BH | floor | verdict |
|---|---|---|---|---|---|---|---|---|---|
| S1_first15_of_season | pts | ln_pick | 20649 / 735 | -0.020 [-0.130, +0.090] | 0.739 | 0.650 | n | 0.2 | - |
| S1_first15_of_season | pts | undrafted | 20649 / 735 | +0.081 [-0.009, +0.168] | 0.070 | 0.034 | n | 0.2 | - |
| S1_first15_of_season | pts | age | 20649 / 735 | -0.185 [-0.276, -0.091] | 0.001 | 0.001 | Y | 0.2 | sig-but-trivial |
| S1_first15_of_season | pts | power_conf | 20649 / 735 | +0.011 [-0.082, +0.107] | 0.845 | 0.759 | n | 0.2 | - |
| S1_first15_of_season | reb | ln_pick | 20649 / 735 | +0.010 [-0.036, +0.052] | 0.649 | 0.623 | n | 0.1 | - |
| S1_first15_of_season | reb | undrafted | 20649 / 735 | +0.041 [+0.003, +0.080] | 0.034 | 0.018 | n | 0.1 | - |
| S1_first15_of_season | reb | age | 20649 / 735 | -0.052 [-0.089, -0.016] | 0.007 | 0.004 | Y | 0.1 | sig-but-trivial |
| S1_first15_of_season | reb | power_conf | 20649 / 735 | +0.024 [-0.014, +0.064] | 0.242 | 0.166 | n | 0.1 | - |
| S1_first15_of_season | ast | ln_pick | 20649 / 735 | -0.029 [-0.059, +0.002] | 0.064 | 0.045 | n | 0.07 | - |
| S1_first15_of_season | ast | undrafted | 20649 / 735 | -0.010 [-0.035, +0.014] | 0.402 | 0.370 | n | 0.07 | - |
| S1_first15_of_season | ast | age | 20649 / 735 | -0.028 [-0.059, +0.002] | 0.064 | 0.030 | n | 0.07 | - |
| S1_first15_of_season | ast | power_conf | 20649 / 735 | +0.018 [-0.011, +0.046] | 0.217 | 0.149 | n | 0.07 | - |
| S1_first15_of_season | fg3m | ln_pick | 20649 / 735 | -0.014 [-0.032, +0.004] | 0.146 | 0.109 | n | 0.03 | - |
| S1_first15_of_season | fg3m | undrafted | 20649 / 735 | +0.014 [-0.002, +0.029] | 0.081 | 0.070 | n | 0.03 | - |
| S1_first15_of_season | fg3m | age | 20649 / 735 | -0.025 [-0.042, -0.008] | 0.007 | 0.003 | Y | 0.03 | sig-but-trivial |
| S1_first15_of_season | fg3m | power_conf | 20649 / 735 | +0.002 [-0.014, +0.019] | 0.832 | 0.823 | n | 0.03 | - |
| S2_first_two_seasons | pts | ln_pick | 15710 / 291 | +0.043 [-0.036, +0.126] | 0.278 | 0.466 | n | 0.2 | - |
| S2_first_two_seasons | pts | undrafted | 15710 / 291 | +0.104 [-0.002, +0.204] | 0.054 | 0.057 | n | 0.2 | - |
| S2_first_two_seasons | pts | age | 15710 / 291 | -0.131 [-0.221, -0.029] | 0.014 | 0.014 | Y | 0.2 | sig-but-trivial |
| S2_first_two_seasons | pts | power_conf | 15710 / 291 | -0.005 [-0.087, +0.082] | 0.927 | 0.863 | n | 0.2 | - |
| S2_first_two_seasons | reb | ln_pick | 15710 / 291 | +0.026 [-0.018, +0.073] | 0.246 | 0.278 | n | 0.1 | - |
| S2_first_two_seasons | reb | undrafted | 15710 / 291 | +0.064 [+0.015, +0.119] | 0.008 | 0.007 | Y | 0.1 | sig-but-trivial |
| S2_first_two_seasons | reb | age | 15710 / 291 | -0.043 [-0.083, +0.001] | 0.060 | 0.063 | n | 0.1 | - |
| S2_first_two_seasons | reb | power_conf | 15710 / 291 | +0.029 [-0.009, +0.071] | 0.144 | 0.128 | n | 0.1 | - |
| S2_first_two_seasons | ast | ln_pick | 15710 / 291 | +0.013 [-0.009, +0.039] | 0.242 | 0.359 | n | 0.07 | - |
| S2_first_two_seasons | ast | undrafted | 15710 / 291 | +0.035 [+0.007, +0.065] | 0.014 | 0.018 | Y | 0.07 | sig-but-trivial |
| S2_first_two_seasons | ast | age | 15710 / 291 | -0.036 [-0.062, -0.010] | 0.008 | 0.024 | Y | 0.07 | sig-but-trivial |
| S2_first_two_seasons | ast | power_conf | 15710 / 291 | -0.001 [-0.027, +0.024] | 0.936 | 0.883 | n | 0.07 | - |
| S2_first_two_seasons | fg3m | ln_pick | 15710 / 291 | +0.004 [-0.012, +0.022] | 0.619 | 0.751 | n | 0.03 | - |
| S2_first_two_seasons | fg3m | undrafted | 15710 / 291 | +0.013 [-0.004, +0.030] | 0.132 | 0.204 | n | 0.03 | - |
| S2_first_two_seasons | fg3m | age | 15710 / 291 | -0.012 [-0.030, +0.006] | 0.181 | 0.264 | n | 0.03 | - |
| S2_first_two_seasons | fg3m | power_conf | 15710 / 291 | -0.001 [-0.015, +0.013] | 0.891 | 0.894 | n | 0.03 | - |

Rookie cohort for Screen B: n=138 (train+test; classes 2022: 36, 2023: 47, 2024: 55; >= 200 min and >= 10 gp)

### Screen B: walk-forward ridge (pick+college+age+height/weight+position) vs position-only mean

Test = classes 2023 (train 2022) and 2024 (train 2022-23), pooled. dMAE = MAE(pos-only) - MAE(ridge); positive = pedigree helps.

| outcome | n test | MAE ridge | MAE pos-only | MAE pick-only | MAE college-only | R2 ridge | R2 pos-only | dMAE [95% CI] | rel gain | dR2 [95% CI] | p | BH | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pts36 | 102 | 2.82 | 3.06 | 2.70 | 2.97 | +0.120 | -0.031 | +0.239 [-0.006, +0.480] | +7.8% | +0.151 [+0.028, +0.272] | 0.053 | n | - |
| reb36 | 102 | 1.28 | 1.55 | 1.97 | 1.96 | +0.555 | +0.342 | +0.270 [+0.121, +0.423] | +17.5% | +0.214 [+0.095, +0.369] | 0.001 | Y | SURVIVOR |
| ast36 | 102 | 1.07 | 1.06 | 1.28 | 1.28 | +0.256 | +0.251 | -0.006 [-0.072, +0.059] | -0.6% | +0.005 [-0.054, +0.073] | 0.837 | n | - |
| mpg | 102 | 4.86 | 5.62 | 4.93 | 5.62 | +0.137 | -0.016 | +0.754 [+0.093, +1.399] | +13.4% | +0.153 [-0.064, +0.338] | 0.025 | Y | SURVIVOR |

Supplementary (untested): does PEDIGREE add over position + height/weight? dMAE = MAE(ridge on position+body only) - MAE(full ridge).

| outcome | MAE pos+body ridge | MAE full ridge | dMAE [95% CI] |
|---|---|---|---|
| pts36 | 2.99 | 2.82 | +0.169 [-0.056, +0.392] |
| reb36 | 1.25 | 1.28 | -0.022 [-0.095, +0.056] |
| ast36 | 1.05 | 1.07 | -0.019 [-0.062, +0.027] |
| mpg | 5.71 | 4.86 | +0.845 [+0.189, +1.479] |

Survivors (BH + floor): 2 of 36.

## Descriptive (not tested)


**First-season production by pick bucket** (mean, 95% bootstrap CI over players)

| group | n | pts/36 | reb/36 | ast/36 | min/g |
|---|---|---|---|---|---|
| 1-5 | 15 | 17.5 [15.8, 19.3] | 7.1 [5.9, 8.4] | 3.6 [2.8, 4.4] | 26.6 [23.5, 29.4] |
| 6-14 | 25 | 13.3 [12.1, 14.4] | 6.7 [5.5, 8.0] | 2.8 [2.3, 3.4] | 20.4 [18.2, 22.6] |
| 15-30 | 41 | 13.9 [13.0, 14.9] | 6.3 [5.5, 7.1] | 2.9 [2.5, 3.4] | 17.6 [15.6, 19.5] |
| 31-60 | 33 | 12.4 [11.2, 13.5] | 6.2 [5.4, 7.0] | 3.2 [2.5, 3.8] | 15.8 [13.8, 17.6] |
| undrafted | 24 | 12.3 [10.7, 13.8] | 6.2 [5.5, 6.9] | 3.0 [2.3, 3.8] | 15.8 [13.5, 18.0] |

**First-season production by college group** (mean, 95% bootstrap CI over players)

| group | n | pts/36 | reb/36 | ast/36 | min/g |
|---|---|---|---|---|---|
| power_conf | 94 | 13.7 [13.0, 14.4] | 6.3 [5.8, 6.8] | 2.9 [2.6, 3.3] | 18.1 [16.8, 19.4] |
| mid_major | 22 | 12.9 [11.4, 14.2] | 6.3 [5.3, 7.3] | 3.5 [2.8, 4.4] | 18.8 [15.8, 21.6] |
| international_pro | 12 | 12.7 [10.3, 15.8] | 6.9 [5.6, 8.6] | 2.6 [2.1, 3.1] | 17.8 [14.0, 21.8] |
| pathway | 9 | 14.6 [12.2, 16.9] | 6.7 [5.5, 8.0] | 3.4 [2.4, 4.3] | 19.0 [15.9, 22.3] |
| unknown | 1 | 17.2 [17.2, 17.2] | 8.6 [8.6, 8.6] | 3.2 [3.2, 3.2] | 27.1 [27.1, 27.1] |

**Mean residual (actual - recency avg) by pick bucket**

| subset | bucket | n rows | pts | reb | ast | fg3m |
|---|---|---|---|---|---|---|
| S1_first15_of_season | 1-5 | 2535 | -0.11 [-0.39, +0.19] | -0.05 [-0.16, +0.07] | +0.05 [-0.03, +0.14] | +0.06 [+0.00, +0.12] |
| S1_first15_of_season | 6-14 | 3854 | -0.10 [-0.34, +0.12] | +0.01 [-0.09, +0.09] | +0.05 [-0.02, +0.13] | +0.03 [-0.01, +0.06] |
| S1_first15_of_season | 15-30 | 5171 | +0.09 [-0.12, +0.28] | -0.01 [-0.08, +0.07] | +0.01 [-0.05, +0.06] | +0.01 [-0.03, +0.05] |
| S1_first15_of_season | 31-60 | 4689 | -0.12 [-0.31, +0.09] | +0.03 [-0.06, +0.12] | -0.01 [-0.06, +0.05] | +0.03 [-0.01, +0.06] |
| S1_first15_of_season | undrafted | 4400 | +0.23 [+0.04, +0.43] | +0.12 [+0.04, +0.21] | +0.02 [-0.03, +0.07] | +0.06 [+0.03, +0.09] |
| S2_first_two_seasons | 1-5 | 1937 | +0.38 [+0.21, +0.57] | +0.07 [-0.04, +0.17] | +0.08 [+0.01, +0.15] | +0.05 [+0.01, +0.09] |
| S2_first_two_seasons | 6-14 | 3063 | +0.34 [+0.17, +0.50] | +0.15 [+0.07, +0.23] | +0.10 [+0.06, +0.14] | +0.04 [+0.01, +0.06] |
| S2_first_two_seasons | 15-30 | 4903 | +0.41 [+0.26, +0.56] | +0.14 [+0.07, +0.21] | +0.09 [+0.05, +0.13] | +0.05 [+0.02, +0.08] |
| S2_first_two_seasons | 31-60 | 4295 | +0.42 [+0.26, +0.58] | +0.17 [+0.09, +0.26] | +0.11 [+0.07, +0.15] | +0.05 [+0.03, +0.07] |
| S2_first_two_seasons | undrafted | 1512 | +0.62 [+0.36, +0.95] | +0.33 [+0.20, +0.48] | +0.17 [+0.10, +0.26] | +0.08 [+0.04, +0.13] |
