# Storyboard — The NBA Prediction Project (2026-10-08)

Presentation form of `docs/OVERVIEW_PLAIN_2026-10-08.md`. Twelve panels, scene by
scene. Every number below is copied verbatim from the docs cited at the top of
each panel — nothing here is re-derived or newly invented. Where a panel makes a
claim, it is paired with a named hit and (where relevant) a named miss so you can
look the player up yourself.

---

## Panel 1 — The mission: don't just guess right, know *how sure* you are

**Visual:** Two weather forecasters side by side. Forecaster A says "70% rain"
every day it rains 70% of the time. Forecaster B randomly says "99% rain" or
"1% rain" and is right exactly as often overall — but useless to plan around.

**Narration:** This project doesn't just try to guess who wins or how many
points someone scores — it tries to be *honestly uncertain*. A model that says
"60% chance" needs to be right about 60% of the time it says that, not just
"usually right." That's calibration, and it's the metric this whole project is
built around, not raw accuracy. Source: `CLAUDE.md` mission statement.

---

## Panel 2 — The ladder: three rungs, climbed one at a time

**Visual:** A ladder diagram, three rungs labeled bottom-to-top: "Elo (team
strength)" → "LightGBM (more features)" → "Possession sim (simulates every
trip up the floor)". A dotted arrow from rung 1 straight to the top labeled
"actually still the champion."

**Narration:** The project built three levels of sophistication to predict who
wins: a souped-up chess-rating system (Elo) with a margin-of-victory tweak,
then gradient-boosted trees with more features (rest, travel, pace), then a
full possession-by-possession Monte Carlo simulation. The honest twist: **none
of the fancier rungs beat plain Elo** on win probability. That's not a bug —
almost all the predictable signal in "who wins" turns out to be "which team is
better, adjusted for home court," and Elo already captures that. Source:
`docs/OVERVIEW_PLAIN_2026-10-08.md` §1, `NEXT_SESSION.md`.

---

## Panel 3 — How one game gets simulated, possession by possession

**Visual:** A basketball court diagram with a single possession animated in
steps: tip → duration → shot type/zone chosen → make/miss → rebound → repeat,
times 2,000 (one per simulated game).

**Narration:** For player props, the system doesn't predict "Steph Curry
scores 23" directly — it runs the game forward thousands of times, possession
by possession, picking who shoots, from where, and whether it goes in, based on
that player's real rates (shrunk toward position/archetype priors when the
sample is thin). Out of 2,000 simulated versions of the game, you get a full
distribution of possible stat lines, not just one number. **Hit:** for
Stephen Curry, game `0022200055`, the sim's predicted mean was **22.9** points;
he scored **21** — close, and it beat the season-average baseline that night.
Source: `docs/OVERVIEW_PLAIN_2026-10-08.md` §2 (Points section).

---

## Panel 4 — Win probability: 0.601, and what that number actually means

**Visual:** A horizontal number line from 0.40 (very good) to 0.693 (coin
flip), with three marks: "0.693 coin flip," "0.601 this model," "~0.58-0.60
sharp Vegas line (unverified estimate)."

**Narration:** The win-probability model scores **0.601 log loss** on held-out
games — a metric that punishes confident wrong answers hard. A coin flip
(always "50/50") scores 0.693; every bit below that is real edge. Literature
suggests a sharp Vegas closing line scores around 0.58-0.60 on this scale, which
would put this model — built entirely from free box-score data, no inside
injury news — in the same neighborhood as a competent sportsbook. **The
catch, stated plainly: this has never actually been checked against real market
odds.** That comparison is Phase 3 work, not done yet. Treat "close to the
market" as a plausible estimate, not a verified result. Source:
`docs/OVERVIEW_PLAIN_2026-10-08.md` §1.

---

## Panel 5a — Player props: rebounds is a real win, and here's why

**Visual:** Bar chart, three stat rows (Rebounds, Points, Assists), each with
two bars (Sim CRPS vs. Season-Average CRPS) — rebounds' sim bar visibly
shorter; points/assists bars nearly identical with an overlap band marked
"statistical tie."

**Narration:** For each player-game the sim builds a full probability
distribution and scores it with CRPS (lower = better, both accurate and
appropriately confident) against the simplest honest baseline: that player's
own season average. Rebounds: sim **1.356** vs. season-avg **1.370** — a real
win, confirmed even after a strict multiple-testing correction (p≈0.012,
survives Benjamini-Hochberg at q=0.05). **Hit:** Ivica Zubac, game
`0022200036` — sim predicted **8.1** rebounds, he grabbed **8**. Rebounding is
the most position-driven stat in basketball (height + where you stand), which
is exactly what the sim has real data for. **Miss:** Precious Achiuwa, game
`0022200045` — sim predicted **4.7**, he grabbed **22**. A backup big
unexpectedly dominating the glass is rare-outlier variance, not a fixable bug.
Source: `docs/OVERVIEW_PLAIN_2026-10-08.md` §2; `docs/FDR_AUDIT_2026-10-08.md`
Family A.

---

## Panel 5b — Points and assists: an honest tie, and why minutes beat position here

**Visual:** Same bar-chart idea, zoomed on Points: 3.239 (sim) vs. 3.229
(season-avg) — bars essentially equal, CI arrow crossing zero.

**Narration:** Points (3.239 vs. 3.229) and assists (0.869 vs. 0.875) are
statistical ties — confirmed as genuine near-nulls, not just "not enough data,"
after the BH correction. Scoring volume is driven mostly by minutes and role
(foul trouble, blowouts, a coach's whim), which swings game-to-game in ways a
position label can't see. **Hit:** Devin Booker, game `0022200027` — predicted
**4.0** assists, actual **3**, beating the baseline that night. **Miss:**
Nikola Jokić, game `0022200035` — predicted **3.4** assists, actual **13**. A
center who passes like a point guard defies the position-based prior the sim
leans on. **Miss:** Jayson Tatum, game `0022200001`, the season's very first
game — predicted **0.1** points, actual **35**. No current-season data yet, and
last season's production wasn't blended in at all (see Panel 6). Source:
`docs/OVERVIEW_PLAIN_2026-10-08.md` §2; `docs/FDR_AUDIT_2026-10-08.md` §4.

---

## Panel 6 — Cold start: the Tatum problem, and the fix built tonight

**Visual:** Before/after diagram. "Before": a scale with Tatum's predicted
points pinned near zero, labeled "no in-season data yet, prior = league-average
wing." "After": the same scale, now pulled most of the way toward "last
season's rate," labeled "time-decay carryover."

**Narration:** The sharpest kind of cold-start failure: at a season opener,
there's no fresh in-season data for the minutes/rate model to lean on, and —
until tonight — last season's production wasn't blended in with any
time-decay at all. A new, isolated module (`nba/features/time_decay.py`) now
blends a player's current-season rate with a decayed version of last season's
rate plus an age curve, so a returning star's season-opener prediction should
sit between the generic position prior and "what they actually did last
year," instead of collapsing to the league-average fallback. **Important
honesty note:** this fix is built and tested (12/12 tests, including
leakage and limiting-behavior checks) but **not yet wired into the live
pipeline** — it's a standalone module waiting for the next session to plug
in. Source: `docs/TIME_DECAY_2026-10-08.md`.

---

## Panel 7 — The minutes model: a clean win, concentrated exactly where it's needed

**Visual:** Two bars labeled "Established players" (flat, no change) and
"Cold-start players" (long bar, "−1.47 minutes error") — with a caption "same
fix, 15x bigger effect where it matters."

**Narration:** Most prop-betting error traces back to one thing: how many
minutes will this guy play? This session's minutes upgrade (learned game
context + a depth-chart/rest-of-roster signal) improved overall minutes error
by about 0.1 minutes per player-game (CI excludes 0) — small but real — and by
**1.47 minutes** for cold-start players specifically (rookies, short track
records), with **zero regression** on established players. This is the single
largest-sample result of the night (n=69,205 player-games) and the one the
audit trusts most, because the team cross-checked it against established-player
regression before shipping rather than just taking the headline win. Points
scale almost linearly with minutes, so this fix matters most there. Source:
`docs/OVERVIEW_PLAIN_2026-10-08.md` §3; `docs/FDR_AUDIT_2026-10-08.md` Family H.

---

## Panel 8 — Statistical honesty: most of tonight's "wins" didn't survive a real check

**Visual:** A courtroom-style scoreboard. "Tested: 5 claims with real numbers."
"Survived cross-examination: 2." Checkmarks next to "Rebounds beats
season-avg" and "Lineup-mix effect is real (but tiny)"; red X's next to
"Assists," "Points," and a note "13 more claims never even had numbers saved to
check."

**Narration:** An inference-validity pass this session found the project's bootstrap
(the method used to compute "is this difference real or noise") was resampling
individual player-game rows instead of whole games — understating uncertainty
and making every prior confidence interval too narrow, i.e. making more "wins"
look significant than they really were. That's now fixed. Of every A/B test
that actually had numbers saved to re-check (5 total), only **two survive** the
correction: rebounds beating season-average, and a real-but-tiny lineup
archetype-mix effect (R²≈0.016 — not worth building a neural lineup encoder
over). Everything else — a 15-cell routing grid, archetype-cluster "examples,"
walk-forward-vs-holdout splits, the matchup-adjustment deltas — had no numeric
CI ever saved to the repo, so none of it can be formally checked; it's
downgraded to "suggestive, not confirmed." Source: `docs/FDR_AUDIT_2026-10-08.md`
§§1-2.

---

## Panel 9 — What got honestly killed, and why that's a feature not a failure

**Visual:** A "graveyard" of three tombstones: "On-court usage (regressed
+0.398 CRPS)," "Lineup archetype mix (real, but 0.02 pts/100 poss — too small
to ship)," "Usage redistribution 2A/2B (inert, n=0 — no injury feed to trigger
it)."

**Narration:** Three ideas were tested on real multi-season data and did not
survive, and the project documents them instead of quietly dropping them.
On-court usage (precise shot-share instead of a season-wide proxy) made points
predictions worse by over-concentrating shots. Lineup archetype mix is real but
too small to be worth building on. Usage redistribution (who absorbs shots when
a teammate sits) is mechanically sound but has literally never fired — it needs
an injury/availability feed the project doesn't have yet. A team willing to
report "we built this and it didn't work" is the whole point of calibration
over hype. Source: `docs/OVERVIEW_PLAIN_2026-10-08.md` §4;
`docs/MATCHUP_3A_RESULT_2026-10-08.md`.

---

## Panel 10 — Matchup 3A: a specific case study in killing an idea the right way

**Visual:** A small table, 4 rows (pts/reb/ast/fg3m), two columns (baseline
CRPS, archetype CRPS) — all four archetype numbers slightly worse than
baseline, with a big red stamp "GATE FAILED — FLAG STAYS OFF."

**Narration:** The idea: does an opponent's defensive archetype mix change how
a player's props should shift? Tested on the real, ~4-season dataset with a
pre-registered hard rule (rebounds must not regress, checked first, no
exceptions): rebounds went from **1.4708** baseline to **1.4750** with the
archetype adjustment — a regression, so the test failed its own gate before
even looking at the other three stats. Decision: **flag stays off, documented,
closed** — not shelved quietly, not re-tested until the rule changes. Source:
`docs/MATCHUP_3A_RESULT_2026-10-08.md`.

---

## Panel 11 — The injury feed: the single biggest data gap, now being closed

**Visual:** A previously-locked door labeled "Usage redistribution (2A/2B)"
with its "Blocked — no forward-looking injury signal" sign being peeled off.
Feeding into it: a pipe from a PDF icon labeled "NBA official injury report,"
with tags "forward-looking," "Out/Doubtful/Questionable/Probable/Available."

**Narration:** The project first confirmed, carefully, that its only free data
source (`nba_api`) has **zero forward-looking injury information** — it only
tells you who was inactive *after* a game started, with no reason and no
questionable/probable tiers. That was the exact gap blocking the "who absorbs
extra shots when a starter sits" feature from ever firing. Tonight that gap got
its real fix: a read-only puller + parser for the **NBA's official pregame
injury report** (published the evening before and refreshed up to ~30 min
before tip), feeding the `player_availability` table with status tiers and
reasons, as-of the report's publish time (so no leakage). It was chosen over
Twitter/scraping specifically because an official structured report carries far
less prompt-injection and reliability risk. Names resolve against the full
active-player list and games link best-effort via a verified team map. This is
the single highest-leverage remaining input for points accuracy — the next step
is wiring it into the usage/minutes models. Source:
`docs/INJURY_FEED_2026-10-08.md`.

---

## Panel 12 — What's next, and the honest bottom line

**Visual:** A short roadmap arrow: "Wire time-decay in" → "Rung-4 neural
step-heads (trained on Colab)" → "Kalshi markets + honest EV engine." Below it,
a single caption line in larger type: "Edge is probably small. The tool is
built to say so."

**Narration:** Next up: wire the time-decay module into the live pipeline to
fix Tatum-style cold starts, train rung-4 neural possession step-heads (built
tonight, not yet run) as a third option for the points router specifically,
and — once Kalshi market data is pulled — finally test "close to the market"
against real odds instead of literature estimates. The honest summary of
tonight: win-probability is solid but un-beatable by anything fancier so far;
player props genuinely beat a season average only on rebounds; the single
biggest fixable gap is minutes/availability data; and most of what looked like
a "win" in the raw numbers did not survive a real statistical check — which is
exactly the kind of result this project is designed to report truthfully
rather than bury. Source: `NEXT_SESSION.md`; `docs/FDR_AUDIT_2026-10-08.md`
bottom line.

---

### Reproduce every example above
```
uv run python -c "
from nba.db.connect import connect
from nba.eval.player_points_sim_eval import run_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
r = run_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(r.raw.sort('crps_sim'))"

uv run python -c "
from nba.db.connect import connect
from nba.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
con = connect('nba.duckdb', read_only=True)
reb, ast = run_reb_ast_sim_vs_baseline_eval(con, n_sims=300, seed=0, max_games=80, return_raw=True)
print(reb.raw.sort('crps_sim'))"
```
Player names resolved via `nba_api.stats.static.players.find_player_by_id`. All
headline CRPS/bias/CI numbers are quoted verbatim from `docs/RESULTS_2026-10-08.md`,
`docs/FDR_AUDIT_2026-10-08.md`, `docs/MATCHUP_3A_RESULT_2026-10-08.md`,
`docs/TIME_DECAY_2026-10-08.md`, and `docs/INJURY_FEED_2026-10-08.md` — none
were re-derived for this storyboard.
