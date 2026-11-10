# Adversary review: app calibrated-only (agent/app-calibrated-only at 96d1633)

Commits a415c33 and 96d1633 on top of main f257257. Worktree `.claude/worktrees/agent-aed16da6a05fc9863`, reviewed
2026-11-10.
- Data: `data/export/latest` (season 2026 only; DuckDB was not opened directly), and the committed 2024 fixtures for
  the top-parlays file.
- Servers: I ran my own only (API :8765, vite :5293, Chrome :9672) and stopped all of them. I did not touch
  5173, 8000 or cloudflared.

**Overall: WOUNDED.** The published T493 record is copied exactly. Labels never call a model-only number calibrated
(39,985 differential checks, 0 mismatches). Overflow at 375 px is fixed. Three problems stand:
- The API still serves the raw chance at the very line where the calibrated one exists.
- Mixing sources makes the line ladder and slider incoherent: P(over 8.5) is shown below P(over 9.5).
- Replay and the guess reveal still state the model's lean beside the outcome, which is a per-prop verdict in all but
  name during the LINE_CAL_LIVE window.

Verdict: BLOCKED

The block clears when M1 and M2 are fixed, and M3 is either fixed or accepted by Devin with a DECISIONS row.

## Model class and playbook check
This is a display layer over the frozen LINE_CALIBRATION map (T493) and the production props model. The relevant
MODEL_PLAYBOOK class is evaluation / scoring surface.
- **Information or capacity:** none added. The branch only chooses which stored number to show.
- **Data:** the T493 record is n=37,528 (Pinnacle) and 53,752 (DraftKings) legs on 2024. No 2026 prop calibration is
  computed.
- **Test validity:** the live test is LINE_CAL_LIVE (frozen 8e492f33), with one look at about 200 games. This review
  asks whether the app opens an interim CAL look.
- **Lineage and inherited guards:** `shown_probs` must match the web's `read()` rules. The differential check below
  shows it does.
- **Scored against:** nothing new is scored.

## Checks

| # | Check | Command / method | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | Raw P in API payloads | `probe_cal.py`: 16 endpoints plus slate, results and swipe for all 7 slates; walked keys for `raw`, `p_ge`, `q`, `taus` | 37 payloads | `line_cal.p_over_raw` is served on `/api/games/{id}` (14 props in game 0022600175) and `/api/card/...`. It is the raw chance at exactly the line where the calibrated one is shown. `/api/top-parlays/{date}` still serves `legs[].p_raw` and `p_joint_raw`. The full distribution (`q`, `dist.p_ge`) is served too, so raw is derivable at any line | **FAIL (M1)** |
| 1b | Raw P rendered | `crawl3.mjs` in Learn, Explore and Market on the props with the largest raw-to-calibrated gaps (0022600160/1642854 pts at 9.5: raw 17%, calibrated 42%; 0022600175/1628401 pts at 16.5: raw 28%, calibrated 46%). Pages: game, leg over and under, replay, calibration, guess, today | 3 lenses x 8 routes | The raw value is never printed at the calibrated line. "Model, raw", "before adjusting" and "unadjusted" appear only on the T493 tab ("better than the unadjusted model"), which is correct | PASS |
| 2 | "Model only" never labelled calibrated | `diff_source.py`: Python port of `slider.ts read()` against API `p_source` on `/api/games` (main line) and `/api/props?line=` at main ±0.5, 1, 1.5, 2, 3, over 28 games | 3,448 main + 26,537 off-main | 0 mismatches in source or value. Every calibrated label sits exactly on line_cal's main line or a listed rung_cal line (half-points only). Off-list lines and whole lines are model only. Earlier timeline snapshots carry `p_over=null` when the prop is calibrated | PASS |
| 2b | Coherence of mixed sources | crawl text, leg ladder | 2 props, 3 lenses | Ladder over: 7.5 28% model, 8.5 22% model, **9.5 42% calibrated**, 10.5 13% model. Under side: 8.5 78%, 9.5 58%, 10.5 87%. Derrick White: 15.5 33%, **16.5 46%**, 17.5 23%. A screen now shows P(over 9.5) > P(over 8.5). The neighbours also let a reader interpolate the hidden raw value (about 17%) | **FAIL (M2)** |
| 3 | 2026 CAL-vs-outcome in aggregate | code read and payloads | 7 slates | No endpoint pairs a chance with an outcome except the guess reveal (one prop per call). `/results` no longer carries `model_p`. But `/api/slates/{date}` (calibrated `model_p_over`, `model_p_source`) joins `/api/slates/{date}/results` (`actual`) by `prop_id`, so the whole window's CAL-vs-outcome is one join away for anyone with the link. On screen, Replay (`Record.tsx:36`) shows "we gave the over 42% calibrated, so we leaned over" beside "That is under the line" for every posted prop of a game. The guess reveal (`Record.tsx:521`) shows "so it said over" beside "under. You were wrong." No tally is computed, but the lean plus the outcome is a verdict by juxtaposition, and a Replay night is a hand-countable readout | **WOUNDED (M3)** |
| 4 | T493 copied faithfully | compared `published/line_cal_t493.json` to `reports/prereg_line_calibration/select.json` (results commit 8938350) | 2 books | n 37,528/53,752 and games 1,260/1,300 equal. Log loss CAL-A0 (`d_cal_a0`) mean, lo and hi equal. `mean_cal_minus_realised` equals `calib.all.CAL` mean, lo and hi. Bins equal `reliability.CAL` (n>0) exactly, 7 and 8 bins summing to n, not the A0 bins. Rule sha8 a5441242 equals select.json sha256[:8]. Verdict CALIBRATED matches TEST_LEDGER T493. "2,000 resamples" matches the harness ("2,000 multinomial game resamples"). The UI orders the log-loss range correctly (0.0096 to 0.0139 better) | PASS |
| 5 | 375 px dark/light | `shots_b.mjs` on game, replay (score revealed) and guess (round revealed) | 6 shots | `scrollWidth` 375 and no element past the viewport in all 6. Nit: the reveal wraps ", so it said over" onto its own line after the pill (`guess_light_375.png`) | PASS |
| 6 | Gates (unpiped) | `uv run --frozen pytest -q`; `ruff check`; `ruff format --check`; `mypy src`; `npm run lint`; `npm run typecheck`; `npm test` | 388+1 skipped; 253 | all exit 0 | PASS |
| 6b | Tests meaningful | read `test_calibrated_only.py`, `calibratedOnly.test.ts` | 7 API tests | They pin line_cal at main across game, breakdown, props and ladder (asserting `!= p_over_raw`); model-only labels for null reasons; rung only at listed lines; timeline nulls; no correctness field anywhere. Missing: a test that no payload carries `p_over_raw`/`p_raw` (M1), and a ladder monotonicity test (M2) | partial |

## Findings

**M1 (major): raw is still served where the calibrated value exists.**
- `PropView.line_cal` (on `/api/games/{id}` and in the card) carries `p_over_raw`, the raw chance at line_cal's own main
  line. The web uses it only for `Reading.p_raw` (`slider.ts:178`), which nothing renders.
- `/api/top-parlays` serves `p_raw` and `p_joint_raw`. The panel no longer renders them (`TopParlaysPanel.tsx`), but
  they are in the payload.
- This contradicts "removes raw everywhere", and for a public link the payload is the product.
- Fix: in `_prop_view`/`card`, emit `line_cal` without `p_over_raw` and compute `common()`'s `p_raw` from `p_ge`. In
  `top_parlays.py`, drop `p_raw` and `p_joint_raw` from the served model, or mark them `exclude=True`.
- Add a test that walks every payload for `raw` keys.
- The distribution (`q`, `p_ge`) is the forecast itself and must stay. Say on the About page that the chance at any line
  can be read from it.

**M2 (major, new defect): mixing calibrated and model-only numbers across lines produces impossible orderings.**
- Where: `Leg.tsx` ladder (`:433-460`), the line slider (`CardDetail.tsx` LineSlider, `Explore.tsx` Lab) and the alt
  stepper (`AltLine.tsx`).
- Example: P(over 8.5) = 22% sits above a row with P(over 9.5) = 42%. Raw is monotone and calibrated is one point, so
  stepping one line moves the chance from 22% to 42% to 13%.
- A newcomer, who is exactly the Learn audience, will read this as a bug. It also leaks the raw at the calibrated line
  by interpolation (M1 by another route).
- Options (Devin's choice; I recommend (a)):
  - (a) For a prop that has a calibrated number, show model-only neighbours as "no calibrated number at this line"
    without a percentage. Keep percentages on the slider only in the Explore lens, with a one-line note that "model
    only" and "calibrated" come from different scales.
  - (b) Shift model-only neighbours by the calibration's delta at main, labelled "approximate". This is a new method
    and needs its own check.
- Add a test that no screen shows non-monotone over-chances across adjacent lines without such a note.

**M3 (major, needs Devin): an implied per-prop verdict during the LINE_CAL_LIVE window.**
- `Record.tsx:36` prints "so we leaned **over**" and then "That is under the line."
- `Record.tsx:521` prints "so it said **over**" beside "under. You were wrong."
- `record.ts:10,47` still stores `model_call` beside `went_over` in each device's history. That is a tally waiting to
  be summed.
- No number is computed, but a Replay night shows the calibrated chance, the lean and the outcome for every posted prop
  of every game. On 2026-11-09 a raw bin read from this same tab became a DECISIONS disclosure. Devin reading a Replay
  night is the CAL version of that.
- Fix:
  - drop the "so we leaned/said" clause and `model_call` (API `GuessReveal.model_call`, `record.ts`) until the look;
  - for slates after 2026-10-29, show either the chance or the outcome on Replay, not both (for example, the outcome
    behind its existing "Tap to reveal", with the chance hidden once revealed). Or have Devin accept the per-page
    exposure in a DECISIONS row before merge.
- Also say in DECISIONS that the public API joins chance and outcome by `prop_id`. Nobody running that join before the
  look is a discipline rule, not a technical one.

**m1 (minor): two reason cleaners disagree.** The API counts a null reason only if it is in `NULL_REASONS`
(`forecast.py:231`), while the web treats any non-null code as known (`slider.ts:43`). An unknown code would make the
game page (API) say rung_cal at main while the leg page (web) says model only. This happens 0 times in today's export.
Fix: make the web call `lineCalReason` only for codes in the same set, or have the API pass through unknown codes as
known.

**m2 (nit):** in the guess reveal the pill wraps the sentence (", so it said over" starts a new line). This goes away
if M3 removes the clause.

## Corrected family and power note
Nothing new is inferred. The T493 numbers are copied, not recomputed, and the winners tab is unchanged from Phase B.
The only inferential stake is LINE_CAL_LIVE's single look (Brier floor -0.0015, MDE -0.0031). This branch must not
spend it early, which is the point of M3.

## Single biggest risk
Someone (Devin included) reads Replay nights in November. The chance, lean and outcome side by side are an unlogged
interim look at the calibrated arm of a frozen test. That is a non-negotiable-4 problem, not a UI one.

## Not verified
- The artifact build: no `artifact-data`.
- Replay with every player panel expanded: the code is clear, but my capture had only the score revealed.
- The Market lens at 375 px: I shot only Learn.
- Whether `top_parlays` live files (not fixtures) exist on the tunnel path.

## Drafted rows (not appended)
- DECISIONS (draft, if Devin accepts M3 instead of fixing it): "2026-11-10: the app's Replay and Guess show the
  calibrated chance beside the outcome for 2026 props during LINE_CAL_LIVE's window; no tally is computed or shown,
  and Claude and agents will not aggregate them before the look; the public API joins them by prop_id. Reverse: any
  aggregate read before the look is logged as an exposure and the look is reported as exposed."
- Ledger: none.
