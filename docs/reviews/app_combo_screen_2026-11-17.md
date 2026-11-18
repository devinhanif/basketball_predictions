# Combo mode screen (app lane), 2026-11-17

Branch `agent/app-combo-screen` (worktree `/Users/devin/Downloads/nba-wt-combo-app`). Everything is under `app/` plus one reader-notes file
`docs/contracts/combo_app_reader_v1.md` and this report. Nothing outside `app/` and those two docs changed; the root `pyproject.toml` and
`uv.lock` are untouched; no new dependency (API or web); `app/` imports nothing from `nba/`.

## What was built

1. **API** (`app/api/src/parlayhitters_api/combo.py`, `routes/combo.py`): `GET /api/combo` (nights that have a file) and
   `GET /api/combo/{date}` -> `{"mode", "clock_utc", "combo": <the stored file unchanged>}`. Export mode reads `APP_EXPORT_DIR/combo_<date>.json`
   beside the manifest; fixture mode reads `app/fixtures/combo/`. Absent file: typed 404 `no_combo`, never a sample, never a fixture in export
   mode. Validation: schema name, night, shapes, `made_at` before `tip_utc`, `surv` nonincreasing in [0, 1], and a refusal (422 `combo_refused`)
   of any key anywhere whose words include price, odds, fee, stake, ev, edge, kelly, vig, ask, bid, payout, wager, juice, cost, bankroll. Unknown
   keys pass through. Three registry codes added (`no_combo`, `combo_refused`, `combo_unavailable`). Artifact bake takes the combo file per baked night.
2. **Browser port** (`app/web/src/combo/engine.ts`): the whole of `nba/parlay/combo.py` (evaluate, scripts, priors, lift ranking, target search,
   Genz QMC with the reference lattice and shifts, Wichura-equivalent inverse by Halley on an accurate cdf). Golden vectors copied to
   `app/web/src/combo/golden/combo_golden_v1.json`; `engine.test.ts` pins its sha256 (`d94d948b...acb`, equal to `tests/parlay/golden/combo_golden_v1.json`)
   and tests every case at the contract tolerances (exact paths 2e-6; QMC within 4 se + 1e-4, and in fact within 1e-7 because the lattice is copied).
   Stat grids are iterated in the fixed order pts, reb, ast, fg3m (the golden file's JSON keys are sorted, the reference's in-memory order is not).
   Two app-side additions, off by default so the golden tests are unchanged: `candidateLegs(..., calOnly, mainOnly)`.
3. **Screen** (`#/combo`, Explore and Market lenses, hidden from Learn nav and search; Learn shows "Combo mode is an Explore screen"):
   "If the game goes like this" (leads; Model and History tabs), lines that move together, aim for a chance, build your own.

## Choices I made (no questions were possible)

- Default night = newest night that has a combo file (`GET /api/combo`), else the newest slate (the page then says the file is absent).
- The API envelope adds the server clock; the payload inside is byte-for-byte the stored file. The tip rule uses that clock, never the browser's.
- Posted MAIN line only, over or under, in every picker and every framing (Devin's tightening): constant `MAIN_LINE_ONLY` in `combo/script.ts`
  (`offerableLines`), plus the `cal_k` check on the main line. One sentence on screen: "Other lines are left out until their calibration is tested."
  Dropped counts are printed ("N of M posted lines left out"). Pinned by `script.test.ts` ("the pickers offer the posted main line only").
- Game legs (win, total) are not offered: the payload carries no posted game line, and the win/total numbers are game-model normals, not calibrated
  grids. The engine supports them.
- Target search under main-line-only searches over and under legs (each in 35% to 75%); the search key now includes the side. "Lines at or above
  the posted number" checkbox is hidden (it is meaningless with main lines only).
- Script legs: each (player, stat) shows in "more likely" on the side that rises and in "less likely" on the side that falls (mirror rows), top 6
  (Analyst) or 4 (Simple), "show all" expands.
- Sentence per script is built from the signed group averages (starters' overs vs bench overs), so the words follow the numbers.
- Realised script shares (2023, 2024) are constants in `script.ts` quoted from contract section 7; the payload does not carry them. The only
  per-night count is the optional `shared.scripts.history`, shown when present.
- Optional payload keys read tolerantly: stat `hist` (shape from the engine lane: `[n, k]` per script, `n_all`, `k_all`), stat `main_line_open`
  (shown as "line opened 23.5, now 24.5", number only, no reading), top-level `fixture` (banner). Documented in `docs/contracts/combo_app_reader_v1.md`.
- No line-movement or price-movement source exists in the app today (no served line history), so movement shows only when the file carries
  `main_line_open`. Item 6 of the design steer (edge-label explainer, Market lens) was not done (lower priority, out of time).
- Photos: the app's `Avatar` (remote headshot with initials fallback; the artifact build keeps its existing behaviour), resized by combo CSS only
  (36 by 44; 56 by 68 in Simple). No image copied.
- "Dark terminal base": the combo screen follows the app theme (dark or light) instead of forcing a dark island in the light theme.
- "few games": the compact count shows `few` when n < 5, so a pair with n = 4 reads `blow 2/4 few  all 6/12` rather than the mock-up's `blow 2/4  all 6/12`.
- Walk: `app/web/scripts/route-walk.mjs` got combo routes (fixture and artifact sets).

## How each hard rule is enforced, and the test that pins it

| rule | enforcement | test |
|---|---|---|
| calibrated only; never `raw_at_main` on screen | UI reads `surv` through the engine; `raw_at_main` is read only inside the engine's tested-range rule | `script.test.ts` "never reads or shows the uncalibrated number" (source grep over every combo file) |
| only in-range lines | `lineInRange`, `offerableLines`, `calibratedOnly`, `candidateLegs(calOnly)` | `script.test.ts` range and pickers tests |
| main line only | `MAIN_LINE_ONLY` constant | `script.test.ts` "the pickers offer the posted main line only" |
| "exploratory" beside every script | `statusWord` (never "calibrated" while the payload says otherwise), badge in both tabs | `script.test.ts` source checks |
| 3+ entities labelled "model estimate, not checked against results"; +-2 se only for qmc | engine `label`/`range_2se`; `ResultBlock` | `engine.test.ts` golden cases (`label`, `range_2se`) |
| p_all_play and p_joint_incl_dnp beside the joint; null p_play shown as unknown | `ResultBlock`, `PlayLine` | `engine.test.ts` golden (`p_all_play` null case) |
| no price, EV, fee, stake anywhere | no such words in any combo screen file; API refuses such keys | `script.test.ts` source checks; `test_combo.py` refusals |
| display lock: no model chance beside an outcome; games past tip closed | no result/outcome field is read by any combo file; `hasTipped(game, serverClock)` closes the game on every tab and the builder offers open games only; an unreadable time counts as closed | `script.test.ts` "what the combo screen may not contain"; `page.test.ts` tip-rule tests; `script.test.ts` `hasTipped` |
| strict lock on counts (Devin, today): History tab shows no chance, Model tab no count, History sorted by name | `ComboScriptHistory.tsx` / `ComboScriptModel.tsx` split; `SEPARATE_HISTORY` constant | `lock.test.ts` (texts and source of both tabs) |
| not behind the betting switch; flag parity | no `BETTING` reference | `page.test.ts`; `scripts/flag-parity.mjs` in `check.sh` |
| calibrated-only API tests (`forecast.shown_probs`) | untouched; combo never calls `/api/props` or `/api/card` | `test_calibrated_only.py` passes |

How the display lock is read: LINE_CAL_LIVE (frozen 2026-10-29; look about 2026-12-01) forbids interim looks at the model's chance beside outcomes of
locked nights (>= 2026-10-30). Combo mode only ever shows a chance for a game that has not tipped on the server's clock and never reads a result.
The season counts are a player's past outcomes against tonight's line, not the model's past chances; Devin chose the strict reading anyway, so they
live on their own tab. **After the look, set `SEPARATE_HISTORY` to false in `app/web/src/combo/script.ts`** and the counts return beside the chance on
one view (the History tab and its sub-switch disappear); `lock.test.ts` then needs its first test updated.

## Checks run (all unpiped; `nice` on a busy 8 GB Mac)

```
cd /Users/devin/Downloads/nba-wt-combo-app
nice app/scripts/check.sh --bake       # switch off
nice app/scripts/check.sh --betting    # switch on
/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python -m pytest tests/test_layering.py --no-cov -q -p no:cacheprovider
WALK_API_PORT=8221 WALK_WEB_PORT=5221 nice app/scripts/walk.sh --widths
```

Results at commit `d6a98c34` plus a one-line registry wording fix and the walk routes (error-contract, combo and full web vitest re-run after it, all green): switch off: ruff, format, mypy, fixtures ruff, eslint, tsc pass; API pytest
628 passed, 1 skipped; web vitest 61 files, 644 tests; build, artifact bake and build, flag parity pass. Switch on: the same, 628 and 644, flag parity
passes. Layering: 15 passed. Combo-specific: 24 API tests (`test_combo.py`), 28 engine/golden tests, plus `script`, `page`, `bar` and `lock` tests.

## Not verified / limits

- Photos: headless Chrome here did not reliably load the NBA CDN images, so the screenshots show the initials fallback; the sizes were checked,
  the live image was not seen.
- The real night file (`combo_2026-11-17.json`, read-only copy in a scratch fixture dir) loads, validates and renders (65 of 436 stat entries with a
  main line, two of four games thin); it was not served from an export directory with a manifest.
- No export-mode run on a real manifest; export mode is covered by a test on the small committed export.
- The engine port was not timed on a 15-game night in a slow browser (QMC for 4-leg searches takes about a second here).
- Item 6 of the design steer (Market-lens edge explainer) not done.

## Next lane (look)

- A wide display font or a second monospace was not added (no dependency, no external font); the screen uses the app's `--mono` and Manrope/DM Sans.
- Forcing the dark terminal base in the light theme (if Devin wants it) is a theme decision.
- An Analyst-mode distribution curve needs a validated distribution first: nothing here draws a curve from `surv`, because the banded grid is a set of
  calibrated leg probabilities at plausible lines, not a validated whole distribution (contract section 3).

## Contract changes I suggest

1. Name the optional keys (`hist`, `main_line_open`, `fixture`, `shared.scripts.history`) in `combo_export_v1.md` (the engine lane's branch documents `hist`).
2. Say that the file is not manifest-checked and the app validates it (reader notes).
3. Carry the per-season script shares with n in `shared.scripts` so the constants in `script.ts` can go.
4. Section 6.2's target search is over-only; the app searches posted-line overs and unders. If unders are wanted in the reference, add them there and regenerate the golden vectors.

## Walk

`WALK_API_PORT=8221 WALK_WEB_PORT=5221 app/scripts/walk.sh --widths`: seed 1 at 390 px, seed 2 with injected failures at 390 px, then 360, 768 and 1180 px:
0 violations in each (exit 0). `walk.sh` has no 1280 px width; 1180 is its widest desktop. Two earlier runs found real things that are fixed: the
Error codes help page (Learn lens) listed my `combo_refused` text with price words (reworded), and one injected-failure run showed a single "blank page"
on `record-guess` (market lens reload), which did not reproduce on the rerun and is not a combo route. Screenshots: see the hand-off message.
