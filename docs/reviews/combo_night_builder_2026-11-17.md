# Combo night builder (combo_export_v1 wiring), 2026-11-17

Plumbing only. No model, map, correlation, script parameter or contract math changed; no accuracy claim is made. No prices, fees,
stakes or EV anywhere. Advice or momentum: follows Devin's request of 2026-11-17 (the loose end "combo export wiring" of T525).

## What it does
`python -m nba.parlay.combo_night --export <copy of export.duckdb> --date YYYY-MM-DD --out <dir> --joint-model <copy of joint_model.json> [--asof ISO]`
writes `combo_<date>.json` (`combo_export_v1`) and prints the validator result (exit 1 if it finds an error). Modules:
`nba/parlay/combo_night.py` (builder, validator, CLI), `nba/parlay/combo_hist.py` (season-2026 history and pre-game `mu_t`).
Reuses `combo_build` (`calibrate_surv_banded`, `build_game_payload`, `build_shared`, `nightly`), `combo_params_v1.json`, the frozen
LINE_CALIBRATION map, `top_source.quartiles`, `game_model`. The export copy is opened `read_only=True`; no price table (`prop_lines`) and no
price column is read.

## Inputs (export table -> payload field)
| payload | export source |
|---|---|
| grids `k0, surv, cal_k, raw_at_main` | `prop_grids` rows of `props_context_residual` (not comparison): `p_ge_full_counts / p_ge_full_n` (199-sample integer survival), median/spread from the 19-point `q_grid` (`top_source.quartiles`, exactly as `line_cal`), `starter_rate` in `prediction_json` |
| `starter` | `starter_rate >= 0.5` |
| `p_play` | `p_play_primary`, else `p_play`, else `null` |
| `main_line` | the row's stored `line_cal.main_line`, only if `line_cal.price_at <= block made_at`; else `null` with a reason |
| `made_at`, `tip_utc` | newest production row stamp used (at or before `--asof` and before tip); `games.tipoff` |
| `mu_m` | primary `win_probs` row at or before `made_at`, `margin_b + margin_s * logit(p_home)` (params from `joint_model.json`) |
| `mu_t` | NOT stored in the export. Rebuilt with `game_model` from season-2026 completed games (team totals from `box_scores`); `mu_t_source` says live-season or league fallback |
| `sd_m`, `sd_t` | `joint_model.json` params |
| names, team | `players`, `prop_grids.team_id`; an id with no name is skipped and logged, never guessed |

Slate-date filter in every query (`games.game_date = ?`, `season >= 2026`; a date before 2026-10-01 is refused before any query). The only
box rows read are `season = 2026 AND substr(game_id,4,2)='26' AND game_id LIKE '002%'`; season 2025 rows are never read, and a test shows a 2025 row
and a 2025-style id in the input change nothing.

## What the export did not hold (honest list)
1. **`mu_t` is not stored.** The contract's "as-of scoring environment" needs completed-game totals across seasons (the model fit and league mean
   include 2025, holdout). Chosen: the live-season-only feature the unmerged `agent/market-game-scripts` branch also uses, rebuilt from `box_scores`
   (the export's `games` table has only the last 7 days; `box_scores` has 205 season-2026 games, and the 51 games present in both agree on both team
   totals). Tonight's `mu_t` is 223.4 to 226.9, three games used the league fallback. It equals the live app's number only if `box_scores` is
   complete; unverified against `nba.duckdb`.
2. **Main lines are mostly absent tonight.** DraftKings lines first appear at 15:04 UTC; games 0022600253/256/257 have only a 14:00 production run, so
   their props have `main_line: null` by the known-at rule (line stamped after `made_at`); 65 of 436 stat entries carry one. Not approximated.
3. **Some players have a 10,000-sample grid** (not the 199 the banded map assumes): 36 prop rows in games 255/256 are skipped, logged as
   `no_survival_grid_or_n_not_199`. Fixing it needs the combo math to know the resolution; not done.
4. No slate for 2026-11-18 exists in the export (`games` ends 11-17). Game 0022600253 tipped at 00:30Z, before the export's `made_at` (00:35Z), so it is left out and logged.
   The brief said the export was made 22:35 UTC; its meta says 2026-11-18T00:35:03Z.

## Validator (`validate_payload`, `load_expected`)
Checks schema name, nonincreasing `surv`, values in [0,1], trimming, `k0 >= 1`, `cal_k` bounds (1 <= lo <= hi <= 199), size budget (300 KB raw, 60 KB gzipped),
price-like keys (token match: price, odds, fee, stake, kelly, ev, vig, ...), `made_at < tip_utc`, `tip > asof`, date equals night, `hist` shape, and that the stored calibrated
value at the main line equals the export's `line_cal.p_over_cal` for that prop (tolerance 1e-4: four-decimal rounding plus float noise).
Run on the real copy, slate 2026-11-17: **0 errors; 4 games, 109 players, 436 stat entries; 65 main-line values compared; largest absolute difference 4.96e-05** (0022600255 / 1641709 / pts: 0.4940 vs 0.49395); none over tolerance, none needed an exemption.

## Output
`/Users/devin/Downloads/nba-scratch-combo-night/out/combo_2026-11-17.json`: 89,959 bytes raw, 20,026 gzipped (budget 300 KB / 60 KB). Without `hist`: 84,041 / 19,369 (hist adds 5,918 raw, 657 gzipped over 65 entries).
Per-night counts of past season-2026 games (`build_log.hist.past_games`, descriptive, 205 games dated 10-20 to 11-16): blowout 72, close 37, shootout 62, grind 48.

## Smoke test of `combo.evaluate_combo` on the real file (descriptive numbers, not accuracy; game 0022600255)
| combo | p_joint | p_independent | lift | method |
|---|---|---|---|---|
| same player: Cunningham pts over 25.5 + ast over 7.5 | 0.2630 | 0.2332 | 1.128 | exact_bivariate |
| teammates: Cunningham pts over 25.5 + Thompson pts over 9.5 | 0.2337 | 0.2356 | 0.992 | exact_bivariate |
| cross-game: Cunningham pts 25.5 + a game-254 player's pts over 18.5 | 0.2356 | 0.2356 | 1.000 | product |
| three-leg: Cunningham pts, ast, reb overs (25.5 / 7.5 / 5.5) | 0.1564 (se 0.00002) | 0.1032 | 1.516 | qmc, "model estimate, not checked against results" |

## Season-2026 script history (`hist`, Devin's change of scope)
Shape on a stat entry with a `main_line`: `"hist": {"n_all", "k_all", "blowout": [n, k], "close": [n, k], "shootout": [n, k], "grind": [n, k]}`, integer counts, documented in
the contract (section 3). Rules: season 2026 only (SQL filter on `season` and on the game id, tested), strictly before the slate date, played = minutes > 0 (NULL or 0 is a DNP, tested),
over = stat > main_line. Script membership uses `shared.scripts.definitions` on the final score. **Shootout and grind were possible** because a pre-game `mu_t` can be rebuilt for each past game by the
same `game_model` code from season-2026 box totals dated before it (the export does not store one; it is rebuilt, never the current-day value). First-day games use the league fallback (3 of 205). Example (real file): a player with line 25.5 has `n_all` 12, `k_all` 6, blowout [4, 2], close [2, 1], shootout [5, 2], grind [4, 2]: counts with their n, no claim.
Games "known before made_at": all counted games are dated before the slate date, so they ended hours before the first production run of the slate day.

**Could this touch GAME_SCRIPTS_FORWARD (rule 563a1c67, `agent/modeler-game-scripts-forward`, not frozen)?** It scores the app's P(script) and the script-conditional leg
probability against outcomes of games AFTER the freeze. `hist` uses neither quantity, feeds no forecast and no number that rule scores, and reads only games before the slate. Definitions
are read from the same `definitions` block the rule uses (15 / 5 / 12.0), with `mu_t` rounded to 2 dp as there. Two cautions: (a) my `mu_t` comes from `box_scores`, not the `games`
table `combo_stage.history_rows` reads, so it may differ from the number that rule scores if `box_scores` is incomplete; this builder is not the producer of that rule's number
and must not be mistaken for it; (b) as the season goes on, the displayed counts include games that rule has already scored, which is display only. I did not change that rule or branch.
Per-player counts are small this season (a player has played 10 to 20 games, a script a fraction of them), so most `[n, k]` pairs have n of 0 to 5: show "k of n" only.

## Playbook check (MODEL_PLAYBOOK cross-cutting questions)
1. Information or capacity: neither; no model. Inputs are stored forecasts and posted lines known at `made_at`.
2. Training data: n/a, nothing is fit. The frozen map and corr/script parameters are 2023-24 fits.
3. Valid test: this is plumbing, not a test. Checks are structural (contract rules, parity with the export's stored value). No outcome is scored; `hist` is a descriptive count of already-finished games. Known-at and tip gating enforced and tested; 2025 refusal tested.
4. Lineage: reuses T525 builders and `line_cal`; nothing new in the math. 5. Scores against: nothing; no claim.
Calibration caveat from the contract stands: banded grids are calibrated at plausible lines, not a validated whole distribution; script statuses are all `exploratory`.

## Proposal (not implemented): nightly wiring
Needs Devin's go and the replay oracle. Reuse the in-memory export tables inside `nba/export/build.py` after `line_cal_fields.attach` (same place the unmerged `combo_stage` step sits in `agent/market-game-scripts`; they should be one step, not two):
call `combo_night.build_night(con, night, cal=..., params=..., game_params=...)` on the in-memory export connection, then `validate_payload`, write `combo_<date>.json` beside `export.duckdb` to a temp name and rename, guarded so it
can never fail the export, with a manifest key. In production `mu_t` should come from `nba.duckdb`'s `games` (as `combo_stage.history_rows`), not `box_scores`, to equal the app's number. The oracle run must show `export.duckdb` and manifest content for existing tables unchanged (the step adds a file and one manifest key); the daily forecast outputs are untouched. Not run here.

## Not verified
Equality of tonight's `mu_t` with the live app's; box_scores completeness for 2026; behaviour on a 2026-11-18 slate (none in the export); the 10,000-sample rows; a `--asof` earlier than the export's; anything about model accuracy; the full test suite (not run; only touched files plus combo and line_cal tests).
