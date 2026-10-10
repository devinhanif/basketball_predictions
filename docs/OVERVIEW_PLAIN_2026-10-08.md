# The NBA Prediction Project, Explained Over Coffee (2026-10-08)

This is the plain-English version of `docs/RESULTS_2026-10-08.md` and `NEXT_SESSION.md`. Every
claim below is paired with a real player, a real game, a predicted number, and what actually
happened — so you can go look it up yourself.

**Methodology note on the examples below:** the headline numbers (CRPS, bias, CI) in this doc are
quoted straight from the full 4-season run in `RESULTS_2026-10-08.md` (~5,300 games). For the
*named* hit/miss examples, I ran the same evaluation code live, read-only, on the first 80 games
of the 2022-23 season (`max_games=80`, `n_sims=300` — small on purpose, to stay cheap on this
machine) rather than re-running the full multi-hour job. That means these specific example games
skew toward season-opener cold-start noise, which is noted where relevant. Every number quoted is
real output from the DB and the sim — nothing here is invented. Reproduce with the commands under
each section.

---

## 1. Who wins the game? Elo, and nothing beats it.

The model that predicts **which team wins** is a souped-up version of chess rating (Elo), tuned
with a margin-of-victory tweak. Its holdout "log loss" is **0.601**.

What does 0.601 mean in plain terms? Log loss punishes confident wrong answers hard and rewards
honest uncertainty. Two reference points:
- A coin flip (always saying 50/50) scores **0.693** — every edge the model has over a coin flip
  shows up as a lower number than that.
- A sharp Vegas closing line (which gets games right ~68-70% of the time) scores somewhere around
  **0.58-0.60** on this kind of log-loss scale.

So 0.601 sits close to "about as good as a competent sportsbook line," using a model built
entirely from free, public box scores — no inside injury news, no line-shopping. We haven't
actually tested it against real market odds yet (that's Phase 3, not built), so treat "close to
the market" as a plausible estimate from the literature, not a verified result.

The catch: nothing fancier beat it. Logistic regression, gradient-boosted trees, travel/rest/TV
features, even the full possession-by-possession simulation — none of them beat plain team-strength
Elo on win probability. That's a real, slightly disappointing finding, not a bug: almost all the
predictable signal in "who wins" is just "which team is better, adjusted for home court," and Elo
already captures that about as well as anything we tried.

Reproduce: `docs/RESULTS_2026-10-08.md` section 4; champion search in `research/eval/ga_tune.py`.

---

## 2. Player stat lines: the sim earned a real win on rebounds, and ties on points/assists

For each player-game we build a full probability distribution (not just a single number) for
points, rebounds, and assists, and score it against reality with CRPS — a metric that's lower when
the predicted distribution is both close to the truth and appropriately confident. We compare the
simulation against the simplest honest baseline: **that player's own season average**.

| Stat | Sim CRPS | Season-avg CRPS | Who wins | How sure are we |
|---|---|---|---|---|
| Rebounds | 1.356 | 1.370 | **Sim** | Real — the 95% confidence interval excludes a tie |
| Points | 3.239 | 3.229 | Tie | Difference is noise (CI crosses 0) |
| Assists | 0.869 | 0.875 | Tie, leaning sim | Difference is noise (CI crosses 0) |

**Why rebounds and not points?** Rebounding is the most *position-driven* stat in basketball —
centers and power forwards grab boards because of where they stand and how tall they are, which
is exactly the information the sim now has (real 5-man lineups + real height/position data for all
891 players). Scoring *volume*, by contrast, is driven mostly by **minutes and role** — how long a
guy plays and whether the offense runs through him that night — which swings game to game for
reasons a position label can't see (foul trouble, blowouts, a hot streak, a coach's whim). More
noise in the input, less separation from a simple average.

### Rebounds — a hit and a miss (real numbers, 2022-23 season openers)
- **Hit: Ivica Zubac**, game `0022200036`. Sim predicted **8.1** rebounds; he grabbed **8**. The
  season-average baseline was well off that same night. This is the classic case the position data
  fixes — a true starting center in a normal role.
- **Miss: Precious Achiuwa**, game `0022200045`. Sim predicted **4.7** rebounds; he actually
  grabbed **22**. That's a huge, rare outlier game (a backup big unexpectedly dominating the
  glass) — the kind of single-game spike no averaging method, sim or season-average, is built to
  see coming. Honest read: this is variance, not a fixable bug.

Reproduce:
```
uv run python -c "
from nba.db.connect import connect
from research.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
reb, ast = run_reb_ast_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(reb.raw.sort('crps_sim'))"
```

### Assists — a hit and a miss
- **Hit: Devin Booker**, game `0022200027`. Sim predicted **4.0** assists; he had **3** — close,
  and well ahead of the season-average baseline that game.
- **Miss: Nikola Jokić**, game `0022200035`. Sim predicted **3.4** assists; he had **13**. Jokić is
  a center who passes like a point guard — a role that defies the position-based priors the sim
  leans on. A big man having an elite playmaking night is exactly the kind of "archetype says one
  thing, the player does another" case the model doesn't catch.

### Points — a hit and a miss (tie overall, but individual games still swing hard)
- **Hit: Stephen Curry**, game `0022200055`. Sim predicted **22.9** points; he scored **21**, and
  beat the season-average baseline by a wide margin that night.
- **Miss: Jayson Tatum**, game `0022200001` — the very first game of the season. Sim predicted
  **0.1** points; he scored **35**. This is the sharpest kind of cold-start failure: at the start of
  a new season there's no fresh in-season data yet for the minutes/rate model to lean on, and the
  carryover from last season isn't currently blended in with any time-decay (a known, documented
  gap — see section 4). The model effectively didn't know Tatum would play his normal starter
  minutes that night.

Reproduce:
```
uv run python -c "
from nba.db.connect import connect
from research.eval.player_points_sim_eval import run_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
r = run_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(r.raw.sort('crps_sim'))"
```

**Routing finding, stated honestly:** across all three stats, the sim's edge isn't spread evenly —
it's concentrated in players whose own history is thin or erratic (rookies, bench players, guys
who miss games). For those players, a season average has little to work with, and the sim wins
clearly (CI excludes 0) in that slice. For "smooth," high-minute regular stars, the season average
is hard to beat and actually wins. The practical rule: **route cold-start/erratic players to the
sim, smooth regulars to the season average.** Full breakdown by bucket is in
`RESULTS_2026-10-08.md` section 2.

---

## 3. The minutes model: a clean win for the players who need it most

Most prop-betting error traces back to one thing: **how many minutes will this guy play?** A
30-point scorer who gets hurt in the 1st quarter and a bench guy who suddenly starts both blow up
a points prediction for reasons that have nothing to do with shooting skill.

This session's "minutes 1A" upgrade (a learned fit using game context + a depth-chart/rest-of-roster
signal) is a genuine, clean win specifically for the hardest-to-predict players:

- **Overall minutes error:** improved by about 0.1 minutes per player-game (small but real, CI
  excludes 0).
- **Cold-start players** (rookies, guys with a short track record): improved by **1.47 minutes**
  per game (CI excludes 0) — a much bigger gain, right where it's needed most.
- **Established players:** no regression — the fix didn't cost accuracy anywhere else.

Why this matters more for points than rebounds/assists: points scale almost linearly with minutes
(more time on court, more shots, more points), so a minutes miss directly becomes a points miss.
Rebounds and assists are less purely minutes-driven (a big man still rebounds a lot in limited
minutes relative to a guard), so the leverage is smaller there.

---

## 4. What we tried and honestly killed

Three ideas were tested on the real 4-season data and did **not** survive, and the project keeps
them flagged off rather than quietly dropping the negative result:

- **On-court usage (true on-court shot-share instead of a season-wide proxy):** regressed points
  CRPS badly (+0.398, CI excludes 0). The simpler season-wide proxy already captured usage well;
  the "more precise" version over-concentrated shots once renormalized across the on-court five.
- **Lineup archetype mix** (does a particular blend of player types on the floor together move
  team offense beyond the sum of the five players?): real, statistically detectable (R²≈0.016,
  permutation p=0.004) but **practically tiny** — about 0.02 points per 100 possessions. Not worth
  building a whole neural lineup-encoder on yet.
- **Usage redistribution when a teammate sits** (who absorbs the extra shots when a starter is
  out): the mechanism is sound in principle but currently **inert** — it never fires, because the
  project has no real injury/availability feed. It only has "who actually played," which you only
  know *after* the game. This is explicitly the single data gap most likely to unlock points
  accuracy; it's just not available yet from free public sources.

---

## 5. Bottom line, the way you'd explain it to a friend

- NBA winners: about as hard to call better than a good Elo rating — nothing fancy beats it, but
  the number itself is solid, close to what a sharp betting line would say.
- Player stat lines are honest (unbiased) and now genuinely beat a simple season average for
  rebounds, roughly tie for points and assists, and the sim's real edge shows up specifically for
  rookies/bench/volatile players — exactly where you'd want a smarter model to help.
- The single biggest fixable gap is minutes/availability data (no real injury feed yet) — that's
  what's standing between "tie on points" and "beat on points."
- Nothing here has been checked against real betting odds yet, so "is there money on the table" is
  still an open question, not a yes.

---

### All commands used for this doc
```
uv run python -c "
from nba.db.connect import connect
from research.eval.player_points_sim_eval import run_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
r = run_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(r.summary())"

uv run python -c "
from nba.db.connect import connect
from research.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
reb, ast = run_reb_ast_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(reb.summary(), ast.summary())"
```
Player names resolved via `nba_api.stats.static.players.find_player_by_id`. Headline 600-game /
4-season CRPS, bias, and CI numbers are copied verbatim from `docs/RESULTS_2026-10-08.md` and were
not re-run for this doc.
