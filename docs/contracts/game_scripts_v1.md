# game scripts v1: "how a game can play out" (export -> app), data side

Status 2026-11-10 (DECISIONS 2026-11-10 12:52, Devin: pieces 1 to 3; piece 4, the forward test, is separate). Built on branch
`agent/market-game-scripts`; not merged; nothing launchd-side changes. **Every per-script number in these files is
EXPLORATORY** (the review override of 2026-11-10 recorded in `combo_export_v1.md` section 2: no script is calibrated, grind's
second-run pass is not confirmatory). The app MUST print the word "exploratory" beside every script chance, every
script-conditional leg probability and every script lift, and MUST NOT show a stake, a size or a "should I bet" anywhere.
The market is a prior and a benchmark; until the live log proves skill the answer to "what should I bet?" is "keep your
money".

This document is written so an app agent can build the whole UI from it alone. Where it says "browser computes", the
reference implementation is `nba/parlay/game_scripts.py` and `nba/parlay/combo.py` (standard library only; port from them,
test against them).

## 0. Files

Written by the nightly export (`python -m nba.export build`, step guarded like `teammate_sits`: it can never fail the
export; `--no-scripts` skips it) beside `export.duckdb`, each to a temp name and renamed into place:

| file | schema | holds | price? |
|---|---|---|---|
| `combo_<YYYY-MM-DD>.json` | `combo_export_v1` plus the additive keys of section 1 | per game: the calibrated survival grid of every rotation player and stat, the game model, P(script), the who-sits support | **none** (a test greps its keys) |
| `game_scripts_<YYYY-MM-DD>.json` | `game_scripts_v1` (section 3) | per game x script: ranked legs and same-player pairs; the ONLY place a `market` block exists | only inside `picks[..].legs[].market`, only where a current Kalshi ask exists |
| `manifest.json` key `game_scripts` | | `{status: "ok" \| "absent", reason, n_games, n_players, n_priced_picks, verdicts, skipped, n_asks, sits_games, markets: {status, n_asks, left_out, n_settled_track}, files: {name: {bytes, gzip_bytes, sha256}}, seconds}` | |

`absent` reasons: `disabled`, `no_games_built` (with `skipped` counts), `error:<ExceptionType>`. When absent the app shows
no script panel (not a placeholder number). The step reads the export's own in-memory tables, the Kalshi snapshot store
read-only (a copy if locked), the paper-trade store read-only (track record), and `data/parlay/joint_model.json`; it
fits nothing and makes no network call. Season 2025 is never read: the as-of total feature uses games of the live
season only (so early in a season `mu_t` leans on the league mean; `mu_t_source` says which).

Known-at: every game block is built only from production rows stamped before that game's tip (the export's own filter);
`made_at` is the newest of those rows. Nothing in either file is an outcome. **LINE_CAL_LIVE (DECISIONS 2026-11-10
02:51):** for slates from 2026-10-30 the app must not show a prop prediction next to its outcome; `game_scripts_*.json` carries
`lock: {line_cal_live: true|false, note}` and the app applies the same finished-game rule it already applies to
`/scenarios`: for a finished game it sends no pick, no leg probability and no script chance. No raw (uncalibrated)
probability is exported except the combo contract's `raw_at_main` (Analyst mode only, as that contract says).

## 1. `combo_<night>.json`: what is new on top of `combo_export_v1`

Everything in `docs/contracts/combo_export_v1.md` holds (grids, `shared.corr`, `shared.scripts`, `game`, `p_play`, `cal_k`,
`main_line`). Additive keys, per game:

| key | meaning |
|---|---|
| `home_abbr`, `away_abbr` | team tri-codes (the JARVIS snapshot matches games by `AWAY@HOME`) |
| `scripts` | `{blowout, close, shootout, grind: {p, rule, status: "exploratory"}}`: P(script) before the game from the game model's margin and total normals (`combo.script_prior`: integer-corrected tails). `rule` is the checkable definition, e.g. `final total >= 241` (the shootout and grind thresholds move with each game's `mu_t`; blowout is `|final margin| >= 15`, close `|final margin| <= 5`). Not exclusive; they do not sum to 1. |
| `win_model` | which win row fed `mu_m` (`rung0_injury_elo`, else `rung0_mov_elo`) |
| `mu_t_source` | `"live-season games only"` or `"league fallback"` |
| `sits` | the who-sits support (section 2.3); absent when the sits export had no rows for the game |

Top level adds `generated_at`. The 2023-24 evidence for the scripts (n, game-clustered CIs, every status reason) is
`combo_export_v1.md` section 7; the app shows "exploratory" and offers that section's n on request. Note the contract's
own statement that the REALISED share of shootout / grind has ranged 22% to 31% by season while the prior is the same
number (about 0.27) for every game by definition: show the prior with that range.

## 2. What the browser computes vs what is precomputed

| thing | precomputed (server) | computed in the browser |
|---|---|---|
| calibrated survival grid `S(k)` per player and stat | yes (`players[].stats[]`) | looked up |
| game model `mu_m, sd_m, mu_t, sd_t`, latent correlations | yes (`game`, `shared.corr`) | read |
| P(script) pre-game | yes (`scripts`) | read |
| script rules and thresholds | yes (`scripts[s].rule`; `shared.scripts.definitions`) | `min_total` / `max_total` are the integers in the rule |
| **slider point -> player distribution** | nothing | section 2.1: two numbers per player-stat from `shared.corr`, then `S'(k)` from the grid |
| **slider point -> which scripts it lies in** | nothing | section 2.2: four comparisons |
| **a script chosen -> leg probability** | for the ranked picks only (`game_scripts_*.json`) | any other leg: `combo.evaluate_game_legs(..., script=s)` (the combo contract section 5 and 6.3) |
| **who sits -> player distribution** | the deltas (`sits`) | section 2.3: shift the grid |
| joint of several legs | no | `combo.evaluate_combo` (combo contract section 5) |

### 2.1 The (final total, final margin) sliders

Two sliders per game: final total `t` and final margin `m` (home minus away). Defaults and ranges: `m` in `[-30, 30]`, `t`
in `[mu_t - 40, mu_t + 40]`, both starting at the expected values `mu_m`, `mu_t` (where nothing moves). For each
player-stat shown:

```
zm = (m - mu_m) / sd_m        zt = (t - mu_t) / sd_t
a  = sign * rho(own_margin, stat, margin)   sign = +1 if player.team == home_team else -1   (0 if the row is missing)
b  = rho(game, stat, total)                 (0 if missing)
r  = rho(game, margin, total)
det = 1 - r^2
w_m = (a - r*b) / det         w_t = (b - r*a) / det
mean = w_m*zm + w_t*zt        sd = sqrt(1 - (w_m*a + w_t*b))
```

This is the Gaussian copula conditioned on the margin and total latents (`game_scripts.point_condition`, checked in the
tests against `Sigma_pg Sigma_gg^-1` with numpy). The player's new survival function is
`S'(k) = 1 - Phi((a_k - mean) / sd)` with `a_k = Phi^-1(1 - S(k))` (`game_scripts.cond_surv`; `S' = 1` where `S = 1`,
`S' = 0` where `S = 0`). A leg's probability is then read off `S'` exactly as off `S` (over `x.5` is `S'(floor(x)+1)`).

What to tell the user, honestly: **the effect is small** because the measured latent correlations are small (own margin
about +-0.07, game total +0.11 for points, +0.09 threes, +0.08 assists, -0.04 rebounds): sliding the total from the
expectation to +20 moves a points leg by about one to two percentage points. The model is LINEAR in the margin, so it
cannot see that **blowouts rest starters** (a large margin of either sign lowers their minutes). That effect is real in
the data (over legs hit 3.5 to 4.6 points less in blowouts and grinds; combo contract section 7) and lives in the discrete
scripts, not here. So the slider panel must show, next to the sliders, the discrete-script reading whenever the slider
point lies in a script's region (2.2): the player's `S` under that script is `combo.evaluate_game_legs([leg], game,
shared, script=s)`. Do not present the slider as the whole story, and do not interpolate between scripts: there is no
evidence for an interior mixture.

### 2.2 Which script the point is in

`blowout` if `|m| >= shared.scripts.definitions.blowout_abs_margin` (15); `close` if `|m| <= close_abs_margin` (5);
`shootout` if `t >= scripts.shootout.rule's number` (`ceil(mu_t + 12)`); `grind` if `t <= floor(mu_t - 12)`. Not
exclusive; may be empty. (`game_scripts.point_regions`.)

### 2.3 Who sits

`sits.sitters`: one entry per candidate sitter the TEAMMATE_SITS export covers (a regular starter who is not already OUT):

```
{"sitter": <player_id>, "team": <team_id>, "status": "questionable" | ...,
 "pts": [[<teammate_id>, <delta_pts>], ...],       # delta_mean of the scenario minus the same-run baseline, 0.01
 "reb_dir": [[<teammate_id>, +1 | -1], ...]}       # direction only
```
and `sits.rule` = `{pts: "HELPS", reb: "HELPS (direction only; size uncertain)"}`. Rows with `|delta| < 0.05` are not exported
(treat as 0). **Display rule (T526-a, as `/api/games/{id}/scenarios`):** points get numbers; rebounds a direction and the words
"uncertain size"; assists and threes are not modelled and never shown; a combination of two or more sitters is not
modelled; team totals are not forced to add up; the opponent and the game result do not move.

Browser: the sitter's own legs become "sits: not applicable". For a teammate with a `pts` delta `d`, shift the calibrated
grid by `d` points: `S'(k) = (1 - f) * S(floor(k - d)) + f * S(floor(k - d) + 1)`, `f = (k - d) - floor(k - d)`
(`game_scripts.shift_surv`; stays a valid nonincreasing survival function). **This is an approximation**: it moves the
whole curve by the scenario's change in the mean and keeps the shape; the scenario export's own quantiles are not applied.
Say "the model's forecast if X sits", never "will score". The sits slider and the (total, margin) sliders compose only
loosely (apply the shift, then the copula conditioning on the shifted grid); state that the composition is untested.

## 3. `game_scripts_<night>.json`: best picks per case

```
{ "schema": "game_scripts_v1", "night", "generated_at", "status": "exploratory",
  "lock": {"line_cal_live": bool, "note": str}, "market_note": str,
  "script_status": {script: {"status", "why_not": [...]}}, "definitions": {...},
  "games": [ { "game_id", "home_team", "away_team", "home_abbr", "away_abbr", "tip_utc", "made_at",
               "pregame": {"mu_m", "sd_m", "mu_t", "sd_t"},
               "picks": { "<script>": { "p_script", "status", "n_legs_ranked", "n_dropped_above_range",
                                         "legs": [ <leg>, ... up to 6 ], "pairs": [ <pair>, ... up to 3 ] } } } ] }
```

`<leg>`: `{player_id, name, team, stat, line, side: "over"|"under", p_script, p_leg, p_leg_script, shift, p_play, status, market}`.
- `p_script`: the game's P(script) (copied so a leg never travels without it). `p_leg`: UNCONDITIONAL calibrated P(leg) given
  the player plays (the combo grid). `p_leg_script`: P(leg | script) given he plays (`combo.evaluate_game_legs(script=...)`:
  the player latent is shifted and rescaled by the fitted `(mu, sigma)` per script, stat and starter / bench).
  `shift = p_leg_script - p_leg`. Rank = `p_leg_script` descending.
- Candidate legs: the posted main line, both sides; plus OVER legs at every threshold `k` that has a current Kalshi YES ask
  when the unconditional P is in `[0.35, 0.75]` and `k` is inside `cal_k`. A player with `p_play < 0.5` (or unknown) is not
  ranked. A leg whose unconditional P is above the tested range (calibrated P above 0.75, or raw at the main line at least
  0.80) or whose conditional P is above 0.75 is DROPPED and counted in `n_dropped_above_range`, never capped (the combo
  contract's rule). The panel must say "ranked by chance given the script, not by value".
- Show P(script) next to every list ("blowout: about 35% of games like this"), and the word "exploratory".

`<pair>` (same player, two different stats, both over or both under the posted main lines, the only dependence the evidence
supports): `{player_id, name, side, legs: [{stat, line}, {stat, line}], p_joint_script, p_independent_script, lift,
p_joint, p_independent, script_vs_base, p_play, status}`. `lift = p_joint_script / p_independent_script` is the
joint-vs-product "script lift" (the copula's same-player correlation under the script, 2023-24 measured 1.17 to 1.20 for a
pair unconditionally); `script_vs_base = p_joint_script / p_joint`. Labelled "model estimate"; two-leg combos are the only
ones the contract scored. Pairs across players or games have lift about 1 and are not offered.

`<leg>.market` is `null` unless the leg is an OVER at a threshold with a current Kalshi YES ask (quote stamped before tip,
at most 60 minutes before the build; `market.price_ts` says when). Then:

```
{ticker, threshold, ask, bid, spread, price_ts, fee, p_over, p_model_incl_dnp,
 ev, ev_low, verdict, model_weight, n_settled_track}
```
- `p_model_incl_dnp = p_play * p_over`: Kalshi's DNP rule is unverified, so a DNP is priced as a miss (as the cards do);
  `p_over` is the UNCONDITIONAL calibrated chance, **never the script one**: the script chance is not a price comparison.
- EV is `nba.parlay.ev.evaluate` (the single EV code of the engine and the cards; the fee object of `configs/parlay.yaml`,
  `fee = ceil_to_cent(M * rate * P * (1 - P))` for one contract, rounded up, `M` by series): the raw expectation is computed inside the engine but NOT exported;
  `ev` / `ev_low` after the engine shrinks the model to the market with weight `model_weight = n/(n+k)` if the paper track
  record has skill, else 0 (on 2026-11-10 `n_settled_track` was 31,481 but the skill was not positive, so
  `model_weight` was 0 and `ev` is the market's own expectation less the fee: negative by construction); `spread = ask - bid` is shown, the cost is the ASK. `verdict` is
  `no_positive_ev_found` or `positive_ev_at_conservative_bound`; tonight every verdict is the first. **Say it:**
  "no_positive_ev_found" is a result; "keep your money" is the answer until the live log proves skill. A hit is annotated
  "this one hit; it was still a -X% bet" and a miss and a hit show the "true odds" in the identical font and size.
- **Display validity (adversary M1):** the export is not rebuilt after the last tip, so the app shows a `market` block ONLY
  while `now < tip_utc` and `now - price_ts <= 60 min`; otherwise it shows "no current quote" (never a stale or post-tip ask
  beside a live game).
- **What may sit beside a price (m6):** in a pick row, `ask`, `bid`, `fee` and `ev` / `ev_low` are shown only beside `p_leg`
  / `p_model_incl_dnp`, never in the `p_leg_script` column. Only the conservative `ev_low` is exported as a bound; the file no
  longer carries a raw EV or a raw probability bound, so a positive raw EV can never sit beside a `no_positive_ev_found`
  verdict.
- **Tip source (m5):** quotes are filtered against the stamped `tipoff`; a game stamped with the 19:00 ET proxy (the export
  logs "real tip missing") may admit a quote from after its real tip, which is why the app's `now < tip_utc` rule above is
  the binding one.
- No `stake`, `size`, `kelly` or contract count exists in the file; the app adds none.

## 4. Size (measured; budget: combo <= 300 KB raw and <= 60 KB gzipped per night as in combo_export_v1 section 4)

Measured on the real 2026-11-10 slate (6 games, 172 players; the step took 4.3 s):

| file | raw bytes | gzipped |
|---|---|---|
| `combo_2026-11-10.json` | 147,180 | 32,242 |
| `game_scripts_2026-11-10.json` | 89,447 | 8,588 |

The budgets are ENFORCED (m3): above 300 KB the combo file loses `raw_at_main`, then its `surv` tails below 0.001; above
150 KB the picks file loses its lowest-ranked pairs first (lowest `p_joint_script`). The manifest's `game_scripts.budget` says
what was cut (`dropped_raw_at_main`, `trimmed_surv_below`, `pairs_dropped_for_size`, byte counts, `over_budget`). The combo
file is about 24.5 KB raw per game before trimming. The manifest also carries `history` (`ok|empty|error:<type>`) and
`mu_t_source` counts, so a league-mean fallback night is visible (m2). A player with `p_play < 0.02` is not exported.

## 5. What the app may and may not say (additions to combo_export_v1 section 8)

- Every script chance, script-conditional probability, script lift, slider result and who-sits number: "exploratory".
- The slider is a small linear effect; the scripts carry the large one (section 2.1). Show that sentence.
- Pre-game P(script) is the game model's normal tail, not a forecast of the script; "the prior moves with the scoring
  environment: realised shootout share ranged 22% to 31% by season".
- Who sits: points numbers and a rebound direction only; "the model's forecast if X sits".
- The live tracker (`ingame_checkpoint_snapshot_v2.md`): "UNPROVEN: no live record; most nights the market already knows
  this."
- Never: a stake, a size, a suggested bet, "edge" language for a script-conditional number, a script chance beside a
  finished game's prop outcome on a LINE_CAL_LIVE night, or a price in a file that is not `game_scripts_*.json`.

## 6. Risks recorded for the review

- `main_line` comes from `line_cal`, which needs a two-sided DraftKings pair stamped at or before the production run. On
  2026-11-10 only 1 of 6 games had main lines (the 12:00 UTC production run precedes the books' lines), so main-line legs and
  same-player pairs exist for that game only; the other five list only Kalshi-threshold OVER legs. The app must say "no
  posted line yet" rather than show an empty list without a reason.

- The pick ranking by `p_leg_script` favours near-certain legs; the 0.75 drop and the p_play floor bound that, not remove
  it. The ranking is by probability given the script, not by value.
- `mu_t` uses live-season games only (the holdout rule), so the first weeks lean on the league mean; the shootout / grind
  thresholds then differ from the parlay engine's (which reads all history). The priors are near 0.27 by definition either
  way.
- The market block compares an UNCONDITIONAL model chance to a price; no script number is ever put against a price.
- The slider is linear in the margin and cannot show the starter-rest effect (section 2.1); the who-sits shift keeps the
  shape and composes untested with the copula conditioning.
- Calibration compounds: the grids are calibrated at plausible lines only (`cal_k`); picks off the posted main line are
  limited to Kalshi thresholds inside `cal_k` and the 0.35 to 0.75 band.
