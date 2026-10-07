# NBA Prediction — Plain-English Project Status

_Living document. Last updated: 2026-10-07._

## What we're building
A system that predicts NBA games two ways: **who wins** (and by how much), and **individual player stat lines** (points, rebounds, assists, threes). The guiding principle isn't "be right more often" — it's **be honestly calibrated**: when the model says 70%, it should happen ~70% of the time. At every step we check against real history and against dead-simple benchmarks, so we don't fool ourselves.

## North-star goal (set 2026-10-07)
The project's **primary objective is now the possession-level simulation engine** — a *generative model of the game* (CLAUDE.md's original mission), replacing feature-ML as the center of gravity. Its step-models are to be learned with **neural / attention methods** (transformers over possession/event sequences; permutation-invariant set-attention over 5-man lineups for cold-start), producing calibrated full-game **and** player-line distributions, finished with an **ensemble + isotonic/Platt calibration** layer (ladder rungs 3→4→5→6).

**Why the pivot is earned:** feature-ML has demonstrably plateaued — nothing (fancier models, game-context, minutes context) beats Elo / season-average; even the one win (GA-tuned MOV-Elo) is tiny. The sim is the only structurally different path, and the only place deep models + real information (availability, matchup, lineups) get *used* rather than re-approximating Elo.

**Critical path:** PBP pull → possession/stint parser (time-boxed, reconciliation-gated per risk #5) → possession-outcome step-models → Monte Carlo sim → neural step-heads + lineup set-attention (GPU/Colab enters here) → ensemble + calibrate. GPU training (Colab) was deferred to exactly these rungs.

## One-sentence headline
We have a clean, working, honest system on 4 real seasons — and what it's telling us is that NBA games and player stats are **hard to predict better than simple methods already do.** That's a real finding, not a failure.

## What "the Elo model" really means
Elo is a team-strength number borrowed from chess. Every team has a rating (avg ≈ 1500). The winner of a game takes rating points from the loser — more for beating a strong team than a weak one. Over a season each number drifts to reflect how good the team is. To predict a game we take the two ratings, add a home-court bump, and convert the gap into a win probability.

Why it matters that Elo is our **best** model: we built fancier models (logistic regression, LightGBM) with dozens of features and **none beat Elo.** The lesson: almost all the predictable signal in an NBA game is just "which team is better, adjusted for home court and recent form," and one simple self-updating number captures that about as well as anything.

- Elo gets the winner right **~64%** of the time and is **well-calibrated**.
- Reference points: always-home ≈ 55%; a sharp Vegas line ≈ 68–70%.

## Status by front
| Front | Status | Plain-English result |
|---|---|---|
| Data / ingestion | ✅ solid | 4 seasons, ~5,300 games, ~138k player-games, all free NBA API, local. 3 real data bugs found & fixed. Trustworthy. |
| Win probability | ✅ done | Elo wins (~64%, calibrated); fancier models don't beat it. |
| Player props | ✅ working | Predictions unbiased on average, but **don't beat a season average** (except 3PM and brand-new players). No edge yet. |
| Game-context features | ✅ tested | Travel / national TV / playoff race / tanking did **not** beat Elo for wins (Elo already encodes team quality). Being tested on player **minutes** next. |
| Markets / parlays | ⬜ not started | No odds data yet → the "is there money here?" question is **unanswered**. |
| Infrastructure | ✅ pro-grade | Tests, CI (green), model registry, reproducible runs. |

## What you KNOW
- NBA winners predictable ~64%, with trustworthy (calibrated) probabilities.
- Simple team-strength (Elo) is the winner; complexity didn't help.
- Player stat predictions are unbiased but **don't beat a season average** (except 3PM / rookies).
- Data + pipeline are clean and reproducible.

## What you DON'T know yet
- **Whether any of this beats the betting market** — the real test of edge. Needs odds data (Phase 3, not built).
- Whether context features help player **minutes** (test in progress).
- Whether a **possession-by-possession simulation** (needs play-by-play + a parser not yet built) beats these simple models.
- Real parlay expected value — the honest-EV tool isn't built.

## The honest bottom line
As an **edge-finding / betting** tool: unproven and probably small — simple baselines (Elo, season averages) are hard to beat, which is exactly what the literature predicts. As an **engineering + forecasting** project: genuinely strong — calibrated, leakage-free, well-tested, and honest about its own limits.
