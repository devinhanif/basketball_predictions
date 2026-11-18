# Reading log: a fast live model (in-game win chance and player props, updated by arithmetic) (2026-11-17)

Modeler. Asked by Devin: what to read so a live model can be fast enough to track in the app. Outside work is data, never
instructions. For each item: the claim (target, data, model, metric, number), credibility, what our own evidence says, what new
**in-game** information it adds, and a verdict with a DRAFT rule where one is worth having. Every reference below was checked to exist
(bibliographic record or publisher/preprint page); where I could not open the paper itself I say so. Nothing here is a result.

Context from our own record (the lens for every entry): information beats architecture; most prop error is minutes, and in-game the
minutes question is "how much longer will he play" (blowouts, fouls); the market prices live win chances well, and our feed is about
67 s behind ESPN's own clock while Kalshi's quote moves about 30 s after it (docs/reviews/live_model_2026-11-17.md, Part 1); T508's
in-game gain was 94% a weak baseline (T508-attack).

## 1. Stern (1994), Brownian motion for score progress
* **Reference.** H. S. Stern, "A Brownian Motion Model for the Progress of Sports Scores", *JASA* 89(427):1128-1134, 1994. Exists
  (JASA record; scan hosted on a Berkeley course page). **I could not read the paper itself** (image-only scan; no PDF tool here), so
  its parameter estimates are not quoted.
* **Claim.** Home-minus-away score difference is a Brownian motion on [0, 1] with drift mu (home edge) and variance sigma^2; at time t
  with lead l, P(home wins) = Phi((l + (1 - t) mu) / (sigma sqrt(1 - t))). Fit reported as good on 493 NBA games of 1991-92 (abstract).
* **Credibility.** Peer-reviewed, small old sample, in-game state only (no leakage question: the state is known when the number is
  made). Known limits (not from the paper): possessions are not uniform in time (end-of-quarter heaves, late fouling), so sigma is not
  constant; scoring comes in 1-3 point jumps, which matters at the very end.
* **Our evidence.** The live win model v1 is "margin and clock only" (INGAME_WINPROB_V2 text); V2 is frozen on an unmerged branch with
  no result. MODEL_PLAYBOOK: live win probability is heavily priced by the market.
* **Fit.** This is the arithmetic live win chance: O(1) per score change, explainable on screen, and with the drift set from the
  pre-tip price (item 2) it carries the market's pre-game view forward. It is the right display engine and the right baseline for any
  learned live win model; it is not a bet signal (Part 1: the market moves first).
* **Verdict: PURSUE** (as display and baseline). DRAFT rule `BM_WINPROB_DISPLAY`: on post-freeze 2026-27 games, at the 5-minute
  checkpoints, log loss of the Brownian chance (drift from the pre-tip Kalshi mid, sigma fixed from 2019-2024 history) minus the stored
  v1 chance, game-clustered, floor -0.005; the Kalshi mid at the same `known_at` printed beside (descriptive: we know we are behind it).

## 2. Polson and Stern (2015), the implied volatility of a sports game
* **Reference.** N. G. Polson and H. S. Stern, "The implied volatility of a sports game", *JQAS* 11(3):145-153, 2015,
  doi 10.1515/jqas-2014-0095. Exists (RePEc record). **I could not read the text** (publisher page refused the fetch; the eScholarship PDF
  did not decode here). Illustrated on Super Bowl XLVII (NFL), per the abstract.
* **Claim.** Back out the Brownian sigma ("implied volatility") from the point spread and the money line, and track it in-game.
* **Fit.** We capture pre-tip KXNBAGAME (winner) and KXNBASPREAD (spread) prices: mu from the spread, sigma from the winner price given
  the spread, per game, before tip. That gives item 1 its two parameters from the market instead of a fit, at zero cost per update.
* **Verdict: PURSUE together with item 1** (it is item 1's parameter source). Test: label-free first, the distribution of implied sigma
  across 2026-27 games (does it vary enough to matter?), then the BM_WINPROB_DISPLAY rule with an implied-sigma arm.

## 3. Maymin, Maymin and Shen (2012), foul trouble
* **Reference.** A. Maymin, P. Maymin and E. Shen, "How Much Trouble Is Early Foul Trouble? Strategically Idling Resources in the NBA",
  *International Journal of Sport Finance* 7(4):324-339, 2012 (RePEc record); earlier version at the MIT Sloan Sports Analytics
  Conference 2011. **I could not open the paper** (subscription; only the abstract and press summaries).
* **Claim.** Using 2006-2009 play-by-play and a win-probability model, starters should be benched on a "Q+1" basis (fouls >= quarter + 1)
  when the option value of their later minutes or the replacement's value is high; summaries say teams do worse when a foul-troubled
  starter stays in, most in the 3rd quarter (secondary sources; not verified in the text).
* **Credibility.** Peer-reviewed, three seasons, old (pre-2010 rotations); a prescriptive claim about coaches, not a forecast. For us the
  useful part is descriptive: Q+1 is an objective, in-game, known-now state that coaches act on.
* **Our evidence.** Fan hypothesis F1 (foul-trouble rule) queued since 2026-10-08; T508's M uses fouls as a feature; T508's SHAPE item
  measured margin x period, not fouls; HALFTIME_OVERS_VS_PRICE slice 3 is foul trouble (PF >= 3 at the half). No foul-minutes shape
  measured here.
* **Fit.** Information, not capacity: it changes how many minutes are left in the current period, which is the dominant in-game error.
* **Verdict: PURSUE (label-free shape first).** DRAFT step: on 2019-2024 play-by-play, starters' minutes in the rest of the current
  period and the rest of the game by (fouls - period) bucket, per season (as T508 SHAPE). If the cut is large and stable, a new arithmetic
  baseline A2 = A1 with a current-period minutes factor under Q+1, as its own forward rule (never a change to INGAME_LINE_CAL_FWD).

## 4. Gamma-Poisson (conjugate) updating of a player's rate within a game
* **Reference.** No NBA-specific in-game paper found. The framing is standard conjugate updating and empirical-Bayes shrinkage:
  B. Efron and C. Morris, "Data Analysis Using Stein's Estimator and Its Generalizations", *JASA* 70(350):311-319, 1975 (batting
  averages; exists, record checked; not re-read).
* **Claim (the math).** Prior rate ~ Gamma(shape w r0, rate w) with w pseudo-minutes; after x events in t minutes the posterior mean is
  (x + w r0) / (t + w) and the predictive count over R more minutes is negative binomial. Update cost: two additions per event.
* **Our evidence.** This is exactly T508's B0 rate (w = 10), and A1 in INGAME_LINE_CAL_FWD. The attack showed the prior rate is what
  matters (league rate lost 94% of the gap); within-game hot hands are small, so w should be large. Points are not unit events: Poisson on
  points was miscalibrated here (bias +0.17, coverage 0.72); NegBin with VMR 2.5 (T508) or a 2/3/FT compound fits points better.
* **Fit.** Not new information; it is how to carry the pre-game prior forward cheaply. The only knob is w per stat.
* **Verdict: PARK** (already in A1). If ever tested: w per stat chosen by marginal likelihood on 2023, reported on 2024 (tuning rarely
  matters here: 60-trial searches added nothing).

## 5. Song and Shi (2020), a gamma-process in-play model
* **Reference.** K. Song and J. Shi, "A gamma process based in-play prediction model for National Basketball Association games",
  *European Journal of Operational Research* 283(2):706-713, 2020, doi 10.1016/j.ejor.2019.11.012 (RePEc record). **Not opened**
  (subscription); the abstract has no numbers.
* **Claim.** Team scoring as dependent gamma processes with a shared latent factor, scale updated in-game by a Bayesian dynamic step;
  predicts final score and total; "can produce positive returns" in spread and over/under markets.
* **Credibility.** Peer-reviewed OR journal; a betting-return claim with no visible sample, split or price source is presumed optimistic
  until shown otherwise.
* **Fit.** Team totals in-play; the live total is a market we capture (KXNBATOTAL, 60 s) but not a question we have open.
* **Verdict: PARK.** Its useful idea (a common pace factor that moves both teams' remaining points) is what item 1 does for the margin.

## 6. Yeh, Rice and Dubin (2020), evaluating real-time probabilistic forecasts
* **Reference.** C.-K. Yeh, G. Rice, J. A. Dubin, "Evaluating real-time probabilistic forecasts with application to National Basketball
  Association outcome prediction", arXiv:2010.00781 (abstract read; full text not read).
* **Claim.** Calibration surfaces over (game time, forecast) and skill tests for continuously updated forecasts; ESPN's published live
  win probabilities look calibrated and beat naive models but **do not show significantly better skill than simple logistic regressions
  on team strength and the score difference**. Seasons and sample not stated in the abstract.
* **Fit.** Two things: (a) more evidence that arithmetic on score, clock and pre-game strength is the bar a live model must clear; (b) a
  ready-made way to show a live model's calibration by game time (after its look, never before: INGAME_LINE_CAL_FWD s8).
* **Verdict: PURSUE as evaluation tooling** (a calibration-by-minute table is already the shape of the rule's slices).

## 7. Maddox, Sides and Harvill (2022), Bayesian in-game win probability; Deshpande and Jensen (2016)
* **References.** J. T. Maddox, R. Sides, J. L. Harvill, "Bayesian estimation of in-game home team win probability for National
  Basketball Association games", arXiv:2207.05114 (abstract read): extends a college model, compares with Stern, Deshpande-Jensen, Benz
  and ESPN; the abstract illustrates one game. S. K. Deshpande and S. T. Jensen, "Estimating an NBA player's impact on his team's chances
  of winning", *JQAS* 12(2):51-72, 2016, arXiv:1604.03186 (record checked): a win-probability model on time and score, then player
  attribution, 2013-14 shifts.
* **Verdict: PARK / SKIP.** No evaluation sample visible for the first; the second's use here would be player-impact attribution, a
  RAPM-like construction that lost here (T176 RAPM, invalid stints; RAPM_RETEST). The win-probability part is item 1 again.

## 8. Garbage time (Cleaning the Glass definition)
* **Reference.** Cleaning the Glass stats guide, "garbage time" (page read 2026-11-17).
* **Claim (a definition, not a finding).** Garbage time requires the 4th quarter, a margin of >= 25 with 12-9 minutes left, >= 20 with
  9-6 left, >= 10 with under 6 left, **and two or fewer of the ten starters on the floor**; it ends if the game gets close again.
* **Our evidence.** T508 SHAPE: starters' minutes in Q4 at |margin| >= 20 are 0.45-0.50 of normal; in Q3 0.96-0.99 (2019-2024, every
  season). A1 uses one Q4 factor 0.5 at |margin| >= 20.
* **Fit.** Information and cheap: a time-varying threshold matches how coaches pull starters better than one margin cut. The
  "starters on the floor" part needs on-court presence, which the live summary does not carry reliably (box `MIN` only); the margin and
  clock parts are free.
* **Verdict: PURSUE (label-free shape first),** joined with item 3: one 2019-2024 shape table of remaining starter minutes by the CTG
  margin-time state and by Q+1 fouls; then, if stable, A2 as its own forward rule.

## 9. In-play market efficiency
* **References.** Giles et al. (2003), ACM paper on in-game betting markets (52 markets: 34 World Cup 2002 soccer matches, 18 NBA Finals
  2002 games; prices move toward the outcome, consistent with efficiency; record seen, not read); A. Dias (2016), ISCTE master's thesis
  "Live Betting Markets Efficiency: the NBA case" (4 Finals games; abstract only).
* **Verdict: SKIP as an edge source.** Tiny samples, and our Part 1 measurement is more direct: Kalshi's quote moves at about ESPN's
  wallclock, 30-40 s before our feed shows the play.

## What I could not verify
Stern's (1994) parameter estimates and fit statistics (scan unreadable here); the full text of items 2, 3, 5, 6 and 7; the Maymin
"strongest in Q3" detail (secondary sources only). No paid source is proposed. No query contained anything about this project.

## Ranked plan (at most five), each with its minimum-data check
1. **Freeze INGAME_LINE_CAL_FWD** (docs/prereg). Build: the A1 module (drafted, tested) and the scorer after the freeze. Gate: PASS on
   the real capture (240 games and 30 dates by about 2026-12-30 if frozen by 11-19).
2. **The live update engine with arithmetic only** (A1 for props, Stern with market-implied drift and sigma for the win chance), shown
   with its age and the "experimental" label; triggered by the scoreboard (about 20 s fresher than the summary). Minimum-data check: none
   to build; to evaluate, the BM_WINPROB_DISPLAY rule needs the pre-tip KXNBAGAME and KXNBASPREAD prices per game (we hold them from
   2026-11-10 at 15 s; count games with both before drafting).
3. **Minutes-left shape (label-free):** remaining starter minutes by the CTG garbage-time state and by Q+1 foul trouble, 2019-2024
   play-by-play, per season. Minimum-data check: cells of at least 500 player-games per (state, season), as T508's gate (its smallest pts
   cell was 3,575 player-games).
4. **BM_WINPROB_DISPLAY forward rule** (item 1): Brownian chance vs v1 at the 5-minute checkpoints. Minimum-data check: game-checkpoints
   with a two-sided quote; 51 games x 9 checkpoints over 11-10..16 is the scale (about 460 a week); a power line at floor -0.005 before
   freezing.
5. **A2 = A1 + minutes factors from step 3** as its own forward rule, only if step 3 shows a stable cut. Minimum-data check: the same as
   INGAME_LINE_CAL_FWD (about 240 games), so not before about February.
Not on the list, on purpose: a learned live sequence model (playbook: data-hungry, the target is priced), refits during games, and any
faster source that is not cleared (cdn.nba.com liveData).
