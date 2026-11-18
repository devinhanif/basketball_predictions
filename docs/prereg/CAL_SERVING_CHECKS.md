# CAL_SERVING_CHECKS: is every calibrated chance the app shows computed the way the map was fitted, paired with a line known at the time, and is nothing shown where no calibrated chance exists? (acceptance checks, pre-registration form)

Status: **DRAFT, revision 0, written by the modeler Tue 2026-11-17, committed 19:58 CST (clock read by `date` at commit), branch
`agent/modeler-calibration-program`. Not frozen. Nothing fitted, nothing scored, no outcome read.**
- **How it becomes the rule:** the main session commits `prereg(CAL_SERVING_CHECKS): <sha256>` of everything above the line
  `=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/CAL_SERVING_CHECKS.md | shasum -a 256`), after an
  adversary pre-freeze read and Devin's confirmation of section 6.
- **Origin:** Devin, 2026-11-17: "do everything in forecasting best practices to calibrate the model", "make sure the model is calibrated
  always", and his choice (4) of the evening list (DECISIONS 2026-11-17 19:26: "change how the export pairs lines with forecasts to raise
  calibrated coverage, checked against the frozen calibration test first"). **Follows advice** (retrain survey 2026-11-17 part 3, row 1;
  decisions review 2026-11-17 row 7; DECISIONS 2026-10-29 12:29 loose end).
- **What kind of rule this is.** These are bugs and gaps, not hypotheses. Each item is a check with a pass condition on label-free
  quantities (counts, equalities, payload scans). No outcome is read by any check, so this rule cannot be a look at any frozen test.
- **Season labels:** `games.season`. Only season 2026 serving rows are read. Season 2025 is never loaded.

## 1. What it protects against
| check | protects against |
|---|---|
| C1 pairing coverage | a calibrated map that exists but reaches 7% of props because of a pairing rule (today 500 of 6,752 distinct props, 500 of 1,877 that have a DraftKings main line by the run's own pairing cutoff) |
| C2 line time stamped, time scope labelled | calling a number "calibrated" at a line time the map never saw (today 68.6% of served values use a line read 2 or more hours before tip; 5.6% sit in the fitted T-60 window) |
| C3 fit-serve input identity (bench) | a map applied to an input it was not fitted on (bench term fitted on `starter10`, served on `starter_rate`: 12.80% of the map's own 72,674 fit legs flip) |
| C4 show nothing when no calibrated value | the raw, overconfident number reaching a user (Devin 2026-11-17: nothing, never the raw number) |
| C5 scope | the main-line map stretched to rungs (T494: worse than raw by +0.044 log loss), whole-number lines, other books |
| C6 one number per (prop, book, line, line time) | the card, the parlay panel, JARVIS and the combo screen disagreeing on the same leg |
| C7 known-at pairing | a line received after tip, or after the forecast's validity, leaking into a stated chance |
| C8 lock hygiene and frozen surfaces | an interim look at LINE_CAL_LIVE through a display; a serving change silently moving a frozen test's inputs |

## 2. The checks (each: definition, today's measured value, pass condition)
Measured on a scratch copy of the app export (`data/export/latest/export.duckdb`, sha256 `750c736e...`, data_version `6edbcef0c89d`,
export date 2026-11-17, slates 2026-11-10..17, 56 games, 6,752 distinct production props), with
`reports/prereg_calibration_program/gate.py` (output `gate_draft.json`, sha256 `3629b223...`). Label-free.

**C1 Pairing coverage.** Eligible prop = a production (`props_context_residual`, primary) prop with a two-sided DraftKings main x.5 line
received at or before the row's `lines_through` (docs/DESIGN_EXPORT_LINE_PAIRING.md: the newest pretip run that covered the game, while
the row was still the latest) and strictly before tip. Coverage = props with a non-NULL `line_cal` / eligible props, per slate.
- Today: 500 / 1,877 = **0.266** over the slates with lines (11-10 105/296, 11-11 180/490, 11-12 84/211, 11-13 62/484, 11-16 **0/155**,
  11-17 69/241; 11-14 and 11-15 had no sportsbook lines, odds key outage). Cause: `line_cal_fields` pairs only quotes received at or before
  the production row's `made_at` (design note B2), and a write-on-change row keeps the morning `made_at` (median forecast lead 630 min).
- **Pass:** coverage >= 0.98 on each of 7 consecutive slates with lines, measured through the shipped `build_export` entry point
  (BEST_PRACTICES 2026-11-10: never a helper). Every non-covered eligible prop carries a reason code that is not `no_book_line`.

**C2 Line time stamped and time scope labelled.** Every served calibrated value carries `price_at`, its lead before tip, and a
`time_scope` field: `fit_window` when the line's lead is in [45, 90] min (the map's T-60 fit condition), `untested_time` otherwise, until
CAL_BY_TIME (draft) reads for that time.
- Today: `price_at` is present; lead of the served line, quantiles 5/25/50/75/95 = 88.5 / 92.3 / 358.0 / 362.3 / 479.8 min; share with lead
  >= 120 min **0.686**; in [45, 90) **0.056**. No `time_scope` field exists.
- **Pass:** 100% of served values carry `price_at`, lead and `time_scope`; the app shows the line time beside the chance.

**C3 Fit-serve input identity.** The bench input passed to `CalMap.apply` equals the fitted definition (`starter10`: exponentially weighted
starter share over played games, half-life 10, as of the row's date), stored with each prediction.
- Today: not stored (`prediction_json` keys include `starter_rate`, not `starter10`); live disagreement cannot be measured; on the fit legs
  12.80% (mean absolute CAL change 0.0018, max 0.196; LINE_CAL_LIVE section 0). 147 of the 500 served values tonight used served bench = 1.
- **Pass:** `starter10` stored on 100% of production rows; served CAL recomputed with it equals the shown value to 1e-12 on 100% of a
  7-slate scan; a test pins `starter10` to the fit harness's definition on the fixture.

**C4 Show nothing when no calibrated value.** No user-facing payload (export fields, API routes, JARVIS text and embeds, briefings,
combo payload, explainer pages) carries a raw model P(over), P(under) or P(N+) at a line. Where no calibrated value exists the field is
NULL with a reason, and the screen shows nothing for the chance. Exempt only: the T493 history tab labelled "raw model, before
calibration" (DECISIONS 2026-11-09 23:02), frozen comparators that are not user-facing (forward scorer, shadow logs, LLM payload),
and Analyst mode if Devin keeps it (section 6, choice 2).
- Today (from the reviews, not re-measured here): JARVIS "model X%" text is raw `leg_marginal` x P(play) (retrain survey row 11);
  `line_cal.p_over_raw` and top-parlays `p_raw` were served on API routes on 11-10 (app review M1; fix status unverified); the full
  distribution (`q`, `dist.p_ge`) is served, so raw is derivable at any line.
- **Pass:** an automated payload walk over every route and file on 7 slates finds 0 raw probabilities outside the exempt list; an AST
  test keeps it that way (CALIBRATION_EVERYWHERE plan section 5.1 allow-list design).

**C5 Scope.** A calibrated value is emitted only inside a validated scope: LINE_CALIBRATION at a DraftKings or Pinnacle x.5 MAIN line, raw x
inside the map's fit band (1st-99th percentile per stat); the rung map only at listed DraftKings rungs with raw P >= 0.5 and r inside its
band; nothing at whole-number lines, other books, combo stats, or rungs off the main line (DECISIONS 2026-11-17 19:08: combo v1 main line
only; the 56% / 44% flat ladder).
- **Pass:** 0 out-of-scope values on a 7-slate scan; 0 ladder inversions among the chances shown for one prop (P(over a) >= P(over b) for
  a < b; the 11-10 app review M2 case).

**C6 One number.** For the same (game, player, stat, book, line, line time) the card, the parlay panel, JARVIS and the combo screen show
the same calibrated value to 1e-9. **Pass:** 0 mismatches on a 7-slate scan.

**C7 Known-at pairing.** **Pass (each must be 0 on the scan):** paired lines received at or after tip; paired lines received after
`lines_through`; served values whose forecast row was superseded before the line's `received_at`.

**C8 Lock hygiene and frozen surfaces.** **Pass:** until the LINE_CAL_LIVE look, no surface places a prop chance for a slate on or after
2026-10-30 beside its outcome (History tab without chances, DECISIONS 2026-11-17 19:26 choice 2); the replay oracle over
`forward_predictions` and `forward_scores` is IDENTICAL before and after each serving change; no file pinned by LINE_CAL_LIVE section 13
(`nba/parlay/line_cal.py`, `line_cal_map_v1.json`, `nba/parlay/top_source.py`, `nba/forward/scorecard.py`) is edited before its look.

## 3. Verdict
- **SERVING_OK:** C1-C8 all pass on the same 7-slate scan. **SERVING_GAPS:** any fails; the failing checks are listed with counts.
- No CI is attached: these are exact counts on the full population of served values, not samples. The scan's slates are the first 7
  consecutive slates with lines after the last of the fixes lands, read through the shipped entry points.
- Re-run on every change to `nba/export`, `nba/parlay`, `nba/jarvis` or the app's payloads (a CI test plus a weekly scan).

## 4. Minimum-data gate (non-negotiable 8)
The checks need 7 slates with sportsbook lines and the export. Measured tonight: 6 of 8 slates had lines (11-14 and 11-15 none; 11-16
partial, 3 of 8 games); 1,877 eligible props; 500 served values. **Gate: PASS** (every check is measurable now; C3's live part becomes
measurable once `starter10` is stored, which is the fix itself; C4 needs the payload walker, not data).

## 5. Interactions with frozen rules
- **LINE_CAL_LIVE (frozen 8e492f33, look about 2026-12-03 to 12-12, see the report):** no check reads an outcome. C1, C2 and C3 change what
  the card serves, not what the look computes: the look recomputes CAL from stored fields with its own pinned harness, at Pinnacle's t60
  line, with `starter_rate`. If C3 lands before the look, the look tests a bench definition no longer served (its section 8.3 already
  discloses this); hence choice 1 below. C8 forbids edits to the pinned files before the look, so the parlay panel's own cut at
  `prod.made_at` (pinned `top_source.py`) stays until then and C6 is measured after the look.
- **FORWARD_PREREG_2026_27, PPLAY_INTO_PROPS_LIVE, UNDERS_FORWARD, BEST_PRICE_FORWARD, LLM_SHADOW:** they read raw stored fields
  (`p_ge_full`, `p_ge`, `q_grid`, Pinnacle prices); none of the fixes writes those fields. Replay oracle must be IDENTICAL (C8).
- **JARVIS weight rule (frozen 2026-10-25):** keeps reading RAW (LINE_CAL_LIVE choice 7). C4 changes only the text shown to the user.

## 6. Devin's choices (my recommended default first)
1. **When to store and serve `starter10` (C3):** *default: after the LINE_CAL_LIVE look* (about two weeks; the mean CAL change is 0.0018, so
   the cost of waiting is small, and the look keeps testing what was served). Alternative: now, disclosed in the look's results.
2. **Analyst mode raw beside calibrated (combo contract, 2026-11-09):** *default: remove it* (Devin 2026-11-17 "nothing, never the raw
   number"); keep raw only on the T493 history tab. Alternative: keep it in Analyst mode, labelled "raw model, uncalibrated".
3. **Which line a calibrated value is computed at (C1/C2):** *default: the newest DraftKings main line known when the forecast was still
   valid, with `time_scope` shown*; the label says "calibrated (map fitted at T-60; this line read at <time>)". Alternative (strict):
   compute CAL only from a line read 45-90 min before tip; morning cards then show nothing. CAL_BY_TIME (draft) decides between them with data.
4. **JARVIS "model X%" text (C4):** *default: show the calibrated value where C5 allows and nothing elsewhere* (JARVIS's labels come from
   Pinnacle no-vig while w = 0, so no alert changes). Alternative: keep the raw number labelled "raw model".

## 7. Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile and distribution layers, recalibration at the line, serving side)
1. **Information or capacity?** Neither: no new pre-tip information, no new model. It makes an existing, tested map reach the props it was
   built for and stops an untested number reaching a user.
2. **Enough data for the class?** Nothing is fitted. The checks are exact counts.
3. **Valid test?** Each check is measured through the shipped entry point on full populations; known-at pairing is checked (C7); the
   lock (C8) prevents the checks from becoming a look.
4. **Lineage.** CALIBRATION_EVERYWHERE plan (2813fac, never merged: inventory of 15 raw-probability consumers, accessor design, AST
   allow-list); DESIGN_EXPORT_LINE_PAIRING (B2 kept the old cut for calibration fields); app review 2026-11-10 (M1 raw served, M2 ladder
   inversion); LINE_CAL_LIVE pre-freeze M1 (bench input). Carried: the scope table, the allow-list, the oracle requirement.
5. **What it scores against.** Nothing is scored. Pass conditions are equalities and counts.

## 8. Commands (maintainer; label-free, about 10 s)
```
cp /Users/devin/Downloads/nba-prediction/data/export/latest/export.duckdb <scratch>/export.duckdb
cd /Users/devin/Downloads/nba-wt-calibration
/Users/devin/Downloads/nba-prediction/.venv-1/bin/python -I reports/prereg_calibration_program/gate.py --export <scratch>/export.duckdb \
  --schedule /Users/devin/Downloads/nba-prediction/data/schedule/live_tips_2026-27.parquet --out <scratch>/gate.json
```
C1 and C2 are the `coverage_by_slate` and `served_line_leads` blocks of its output. C3-C7 need a payload walker that does not exist yet
(market/app lane, specified by CALIBRATION_EVERYWHERE section 7).

=== RESULTS BELOW ===

(none)
