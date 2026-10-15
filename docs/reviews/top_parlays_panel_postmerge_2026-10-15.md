# Post-merge review: top-3 parlay panel (merge 7d824c9), 2026-10-15

Scope: `git diff 7d824c9^1 7d824c9`, app/ only, 13 files, +1,186 lines. Merged to main on Devin's go before review.
`git diff 7d824c9 HEAD -- app/` is empty, so the line numbers below hold on main at 049ed9e.
Rules: `docs/contracts/top_parlays_v1.md`, `docs/APP_CONTENT_SPEC.md` (CS), `docs/APP_SECURITY_BASELINE.md` (SB).
Season 2025 not loaded. All DuckDB access `read_only=True`. Attack scripts live in the scratchpad (`tp/attack.py`, `tp/stale.mjs`),
not in the repo.

Verdict: BLOCKED

In one line: missing and unknown files fail closed, and the default verdict text is right. But on the app's default landing
page (Today, 2024-11-12), the synthetic illustration sits on top of the real historical replay slate for that date. It reuses
that slate's game ids and matchups, and the only label is one small notice line. The panel also states "before tip" and
"calibrated" as facts that neither the API nor the data supports. Nothing is deployed outward, so a revert is not needed if
fixes 1 to 4 land before any outside exposure. Whether to keep it on Today until then is Devin's call.

This is not a statistical claim, so the six attack checks do not apply as written (no gain, no OOF arrays, no CI to
re-derive). They were replaced by boundary attacks on the loader and the render logic.

## Per-check table

| # | Check | Command | Result | Pass |
|---|---|---|---|---|
| 1 | Every number on screen comes from a payload field | read `TopParlaysPanel.tsx` 1-238, `topParlays.ts` 1-71 | Every rendered number is a payload field (`pct(p_*)`, `american(price_american)`, `lineText(line)`, `evText(ev_*)`, `stamp(made_at/price_at)`, `rank`). **But** the text "before tip" at `TopParlaysPanel.tsx:35` is a factual claim that no field backs: `price_at < tipoff` is never compared, and the API accepts a leg priced after tip (attack A4e). | FAIL (claim, not number) |
| 2 | No hype words; "keep your money" shown whenever true | grep the copy and caveats | No "lock", "best bet" or "guaranteed" in app copy. The headline is a state. Missing and refused files still render "Keep your money." (`:198`). **But** whether the verdict is true is left to the producer: the API accepts `positive_ev_found` with every `ev_low80 < 0` and `illustration: true` (A4b), and the panel then drops "Keep your money". Free text (`why`, `reason`, `caveats`, `calibration.status`) is not screened, so a file with "LOCK. Hammer this, guaranteed." is served (A4m). | FAIL (unguarded) |
| 3 | An unknown verdict string cannot render as advice | `uv run --frozen python tp/attack.py` (A4a) | `verdict: "LOCK_OF_THE_NIGHT"` gets 422 and renders as "Keep your money" plus a refusal. The front end only says "keep" on an exact match, and anything else renders the raw label in monospace with "still a paper ticket" (`:108-115`). | PASS |
| 4 | The illustration cannot be taken for a real slate | API `GET /api/slates`, `/api/slates/2024-11-12`, `/api/breakdown/0022400001/9000/reb` | The default store's slates are exactly 2024-11-12 and 2024-11-20, the two illustration dates. `Today.tsx:70` takes `slates[0]`, so the **default landing page** shows the illustration. The fixture's game ids 0022400001-04 and matchups (ATL@BOS, MIA@DET, CHA@ORL, NYK@PHI) are the **real replay games listed directly below the panel**. Only the player names ("Sample Center 1") and one `.notice` line (`:144-148`, under the large verdict) mark it as synthetic. Ticket cards carry no mark. Rank 1 shows "EV per $1 +$0.05" with DraftKings prices and "Price stamped Nov 12, 6:05 PM ET, before tip." Every "Open this leg's breakdown" link 404s ("no such player in that game"). | FAIL |
| 5 | The API cannot serve a path outside its directory; a missing file fails closed | A1-A3 in `tp/attack.py`; `test_bad_date_never_reads_a_file` | `..%2F..`, `%2e%2e`, `%0A` and `%00` all get 404. No traversal is possible: the pattern admits only digits and `-`, and a FastAPI path segment cannot contain `/`. A missing file gets 404 and "Keep your money / normal gap". Hardening gaps: `_DATE.match` with `$` accepts a trailing `\n`, and `\d` accepts Unicode digits ('٢٠٢٤-11-12' matches). Neither can escape the directory. A misconfigured `APP_TOP_PARLAYS_DIR` (missing dir) gets a silent 404 that looks like a normal gap. An empty `APP_TOP_PARLAYS_DIR=""` falls back to the illustration fixtures (A3b, 200). | PASS (minor hardening) |
| 6 | The calibration line is honest: Stage 1 pass only, live pending | ledger T493; fixture `calibration.status` | The fixture text "Stage 1 pass (T493); live confirmation pending" matches T493 (CALIBRATED at Stage 1, select 2024, SURVIVES attack; the live log is the recommended confirmation). **But** the line is free text from the file, and any string is served (A4k "Fully calibrated and proven" is accepted). Separately, T493 calibrated **single legs** (log loss at the line). The panel labels the **joint** as calibrated (`:64`, "Chance every leg hits (calibrated)") and says the 80% range holds "four times in five" (`:177-178`). Neither has been tested. T490 found joint chances overconfident at ge70 (2-leg gap -0.098 [-0.129, -0.068], n 3,337 parlays; 3-leg -0.087 [-0.119, -0.055], n 3,360; date-clustered B 5,000; raw legs, not CAL). | FAIL (overclaim) |
| 7 | Stale logic across time zones | `node tp/stale.mjs` | `isStale` (`topParlays.ts:57`) compares the UTC calendar date of `made_at` with the ET `slate_date`. A file made the evening before in ET (20:00-23:59 EDT, 19:00-23:59 EST) is **not** flagged: `2026-10-20T01:00Z` for slate 2026-10-20 gives false (the ET date is 10-19). It never flags a file made **after tip** (`2026-10-21T01:30Z`, false) or days later (`2026-10-25`, false). A non-timestamp `made_at` is accepted by the API (A4h), and `stamp()` then throws `RangeError: Invalid time value`, which takes down the render. There is no error boundary in `app/web/src`. The panel never prints `slate_date`, and the Parlay page shows `slates[0]` (`TopParlaysPanel.tsx:233`) under "Our most confident tickets for the night", whatever night that is. | FAIL |
| 8 | Shape invariants at the boundary | A4c-A4l | Accepted: a 3-leg ticket under size "2"; 9 tickets with duplicate ranks in a "top 3" list; inverted `p_joint_cal_80` [0.9, 0.1]; `ev_low80 > ev_per_dollar`; `p_joint_cal` 0.99 from legs 0.60 x 0.61; a fixture without `illustration` (it becomes "real" by default). NaN is refused. | FAIL (minor-major) |
| 9 | Content spec state B | CS 1.2 B, rule 2 | The spec requires the gate counters ("now n = X over Y dates") and the grey label **HYPOTHESIS, UNPROVEN** when the gate is closed. The panel has neither, and contract v1 has no gate field, so the app cannot check the verdict. The T490 caveat is shown with no n and no CI (CS rule 2) and misstates the finding. It says "Parlays the model called 70%+ ... in 2023-24", but T490's ge70 pool is *legs* the raw model put at >= 0.70, pooled over 2023-24 **and** 2024-25. | FAIL |
| 10 | Checks | below, unpiped | app/api: ruff 0, format 0 (32 files), mypy 0 (20 files), pytest 212 passed 1 skipped, exit 0. app/web: vitest `topParlays.test.ts` 4 passed, exit 0; `tsc --noEmit` exit 0. | PASS |

Commands (exit codes shown in the run):
```
cd app/api && uv run --frozen pytest -q -p no:randomly                          # 212 passed, 1 skipped, exit 0
cd app/api && uv run --frozen ruff check src tests                              # exit 0
cd app/api && uv run --frozen ruff format --check src tests                     # exit 0
cd app/api && uv run --frozen mypy src                                          # exit 0
cd app/web && npx vitest run src/topParlays.test.ts                             # 4 passed, exit 0
cd app/web && npx tsc --noEmit -p .                                             # exit 0
cd app/api && uv run --frozen python <scratch>/tp/attack.py                     # exit 0 (A1-A4n as reported above)
node <scratch>/tp/stale.mjs                                                     # exit 0
cd app/api && PYTHONPATH=src uv run --frozen python -c "...TestClient: /api/slates, /api/slates/2024-11-12, /api/breakdown/0022400001/9000/reb"   # exit 0
```

## Numbered fixes (file:line, exact fix), severity-ranked

**Blockers (before any outside exposure, and before the panel stays on the default Today page)**

1. `app/fixtures/build_top_parlays_fixture.py:18-30, 98-101`: the illustration copies the real replay slate's game ids and
   matchups on the same dates. Fix: give the illustration dates that are not in any store (for example `2024-01-01`) and game
   ids outside the NBA range (for example `"ILLUS-1"`). Then the Today page for a real night shows "No tickets file", and the
   illustration is reachable only by asking for it. Rebuild the two JSON files. In `test_top_parlays.py`, assert that no
   fixture date or game id appears in `/api/slates`.
2. `app/web/src/TopParlaysPanel.tsx:141-148, 51-56`: when `d.illustration` is true, the kicker and `h2` must say
   "ILLUSTRATION: synthetic, not a real night" (not "Our most confident tickets for the night"), and every `Ticket` must carry a
   visible "ILLUSTRATION" tag in `.tp-head`. A screenshot of a single ticket must not pass as real. Suppress the breakdown link
   (`:37-41`) for illustration files.
3. `app/web/src/TopParlaysPanel.tsx:34-35` together with `app/api/src/parlayhitters_api/top_parlays.py:47-48, 107-111`: the screen
   asserts "before tip". Fix it in the API: parse `price_at`, `tipoff` and `made_at` as timezone-aware `datetime` (pydantic
   `AwareDatetime`), and refuse (422) any leg with `price_at >= tipoff`, as the contract requires ("only legs with a pre-tip price
   stamped before tip", CS 1.1 "refused after"). Also refuse a file whose `made_at` is at or after the earliest leg tip (a
   post-tip file). Only then may the UI print "before tip". Add negative tests for both.
4. `app/api/src/parlayhitters_api/top_parlays.py:82` and `app/web/src/TopParlaysPanel.tsx:100-119`: "Keep your money" depends
   on producer honesty alone. Fix: refuse `positive_ev_found` unless `illustration` is false **and** at least one ticket has
   `ev_low80 > 0`. Until contract v2 carries the gate counters (fix 7), render "Keep your money." on every file. A
   `positive_ev_found` from a file the app cannot check against the gate is shown as "the file claims positive EV; the app cannot
   confirm the gate is open; keep your money". Add a negative test.

**Major**

5. `app/web/src/TopParlaysPanel.tsx:64, 69, 177-178`: "Chance every leg hits (calibrated)" and "four times in five". T493
   calibrated single legs; the joint and its 80% range are untested, and T490 measured joint overconfidence of about 9-10 points
   at ge70. Fix the copy to: "Chance every leg hits (from calibrated legs; the combined chance is not itself calibrated)" and "The
   80% range is the model's own spread; how often it holds has not been checked yet."
6. `app/web/src/topParlays.ts:56-58`: replace with an ET comparison:
   `etDate(madeAt) < slateDate` where `etDate` uses `Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" })`. Add
   `madeAfterTip(d)` (made_at >= min leg tipoff), with its own alert "made after tip; not a forecast". Add the six cases from
   `tp/stale.mjs` to `topParlays.test.ts`. Print `slate_date` in the `h2` (`TopParlaysPanel.tsx:142`), for example "for Tue Nov 12".
   On the Parlay page (`:228-237`), label the night shown and flag it when it is not today's ET date.
7. `docs/contracts/top_parlays_v1.md` (a contract v2 change, so `schema: top_parlays/2`, not an edit to v1) and the panel: add
   `gate: {settled, needed: 30, dates, needed_dates: 14, skill_vs_market, open}`. Render CS 1.2 B: "The model defers to the
   market until it has a track record (needs >= 30 settled, >= 14 slate dates, positive skill; now n = X over Y dates)" and the
   grey label **HYPOTHESIS, UNPROVEN** over the tickets while `open` is false. Also list `illustration` in the contract (the app
   reads a field the contract does not define).
8. `docs/contracts/top_parlays_v1.md:19` and `app/fixtures/build_top_parlays_fixture.py:14`: the T490 caveat is wrong and has no
   n or CI. Replace with: "Two-leg parlays built from legs the raw model put at 70% or more hit 9.8 points less often than the
   model said (95% CI 6.8 to 12.9 points, n 3,337 parlays, date-clustered, 2023-24 and 2024-25; T490). Three-leg: 8.7 points
   (5.5 to 11.9, n 3,360)." Source: `reports/alt_parlay_x/results.json` cells `ge70_size2/T1`, `ge70_size3/T1`. Producer
   wording is the parlay owner's; the app only displays it.
9. `app/api/src/parlayhitters_api/top_parlays.py:55-65, 84`: enforce shape invariants with model validators. `len(legs)` must
   equal the size key; at most 3 tickets per size; ranks must be 1..k, unique and in order; `lo <= p_joint_cal <= hi`;
   `ev_low80 <= ev_per_dollar`; `abs(p_joint_cal - prod(p_cal)) <= 0.05` when `same_game` is false (cross-game legs are
   independent by contract); `price_american` must not fall in (-100, 100). Each gets a 422 and a negative test.
10. `app/api/src/parlayhitters_api/top_parlays.py:77`: `illustration: bool = False` makes a fixture that loses its flag look real.
    Either require the field (no default), or have the fixture test assert the flag through the API rather than only on disk
    (`test_top_parlays.py:29` reads the file, not the response).

**Minor**

11. `top_parlays.py:21, 94`: use `re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date)` plus `datetime.date.fromisoformat(date)`.
    The current `$` accepts a trailing newline and `\d` accepts Unicode digits.
12. `top_parlays.py:89, 96-98`: `os.environ.get(ENV) or DEFAULT_DIR` sends an empty string to the illustration fixtures, and a
    missing configured directory gives a silent 404 that reads as "normal gap". Fix: if `ENV` is set but not an existing
    directory, return 503 "top parlays directory not configured". Also add `path.resolve().is_relative_to(dir.resolve())`
    (defence in depth against a symlink in the directory).
13. `TopParlaysPanel.tsx:207`: the panel's `View` ignores `fromCache`, so a service-worker copy shows no "saved copy" mark. Pass
    an `onStale` handler or render a "saved copy" notice when `state.fromCache` is true.
14. `topParlays.ts:65-67`: `evText(-0.004)` renders "-$0.00". Round first, then sign: `const r = Math.round(x * 100) / 100`.
15. `TopParlaysPanel.tsx:94`: `key={t.rank}` collides on duplicate ranks (moot once fix 9 lands).
16. `TopParlaysPanel.tsx:181-182`: "We only call a ticket worth it if the low bound is above zero" leaves out the gate condition.
    Append ", and only after the model's live record passes its gate".
17. App boundary has no screen on free text (`why`, `reason`, `caveats`). CS rule 1 puts numguard on the producer. Recommend a
    banned-phrase refusal in the loader (CS 3 list) as a second line, at least for "lock", "guaranteed", "free money" and
    "can't lose".

## What held up
- Unknown schema, unknown verdict, malformed JSON, a missing required field and a file whose date does not match its name all
  get 422, and the UI renders "Keep your money" with a refusal line (tests at `test_top_parlays.py:58-82`). A missing file gets
  404 and "normal gap, keep your money". `extra="ignore"` plus `model_dump` means unknown fields are dropped, not passed through.
- No orders, credentials or network calls. The diff stays within app/ and matches its task (the panel plus a read-only API plus
  fixtures). The DECISIONS row is a separate commit.

## Corrected family and power note
No inferential claim is made in this diff, so there is no BH family to correct and no power to state. The only numbers carrying
inference on screen are the T490 caveat (CI added in fix 8) and the calibration status (T493, its own family, already
attacked: SURVIVES).

## Playbook check
Model class: **set and joint models (copulas for parlays)** over **quantile/distribution layers (recalibration at the line)**.
This review is of the display, not of a fit.
1. Information or capacity: the panel adds no information. It ranks existing calibrated leg probabilities, so any edge has to
   come from T493's leg calibration, not from the parlay construction.
2. Enough data for this class: the copula's gain over independence is about 0.0011 log loss and below one-season power
   (playbook). The joint layer cannot be validated by a single season, so "calibrated" must not be claimed for the joint (fix 5).
3. Test validity: T493 tested leg log loss at the line, a valid test of the legs only. No test of joint calibration of CAL legs
   exists. T490 tested raw legs. An untested joint is not a calibrated joint.
4. Lineage: this is ALT_PARLAY_X (T490, no cell profitable, ge70 overconfident) and the parlay-of-the-day engine with CAL legs
   swapped in. The inherited guards are the pre-tip price stamp (not enforced at the app boundary, fix 3) and the gate (absent
   from the contract, fix 7).
5. Scored against: the book's implied joint (a product of vig-inclusive asks; CS 1.1 says the card must say it is "NOT a tradable
   price", which the panel does not), and in the end the live forward log.

## Single biggest risk
Someone reads the default Today page, or a screenshot of one ticket, as a real night's recommendation: a +$0.05 EV DraftKings
two-leg on that night's real ATL@BOS game. The only label is one small notice line that the ticket card does not carry.

## Not verified
- I did not run the panel in a browser. Layout, the visual weight of `.notice` against `.tp-verdict`, and the dark-mode contrast
  were judged from CSS, not seen.
- The producer (`nba/parlay` writing `reports/parlay/top_parlays_<date>.json`) does not exist yet, so the real file's
  numguard, copula pricing, fee math and stamps are unreviewed.
- Full vitest suite and eslint were not run (only `topParlays.test.ts` and `tsc`).
- Whether CS 1.1's "NOT a tradable price" note is meant to apply to this panel as well as to Parlay of the day.

## Drafted rows (not appended)
- TEST_LEDGER: none (no test was run on outcomes).
- DECISIONS draft: "2026-10-15. Top-parlays panel post-merge review BLOCKED (docs/reviews/top_parlays_panel_postmerge_2026-10-15.md):
  illustration overlays the real replay slate, the pre-tip stamp is unenforced, and the verdict is unchecked against the gate.
  Options: (a) hide the panel on Today until fixes 1-4 land (safe default), (b) keep it on Today as is, internal only.
  Reverse: fixes 1-4 merged with negative tests."
- BEST_PRACTICES draft: "An illustration fixture never shares dates or ids with a real store, and it is labelled on every card,
  not once per panel (top-parlays 2026-10-15)."

---

## Re-check of the fixes: origin/agent/app-top-parlays-fixes 3c86c08 (off main e7476b9), 2026-10-15

Scope: `git diff e7476b9 3c86c08`, 14 files: app/ plus `docs/contracts/top_parlays_v2.md`. Read in a detached worktree at
3c86c08. Attacks are in the scratchpad (`tp/attack2.py`). Season 2025 was not loaded.

Verdict: BLOCKED

In one line: 15 of the 17 fixes hold, and the T490 numbers re-derive exactly. But the gate's own *thresholds* come from the
file. A file that sets `settled_min: 1, dates_min: 1` opens the gate and serves `positive_ev_found`, and the panel then says
"the model has a live record that passed its gate". That is the one guarantee blocker 4 exists for. The fix is one line. There
is also a new render crash when a size key is missing.

### Checks (unpiped, exit codes)

```
cd app/api && uv run --frozen ruff check src tests          # All checks passed, exit 0
cd app/api && uv run --frozen ruff format --check src tests # 32 files already formatted, exit 0
cd app/api && uv run --frozen mypy src                      # no issues in 20 files, exit 0
cd app/api && uv run --frozen pytest -q -p no:randomly      # 239 passed, 1 skipped, exit 0
cd app/web && npx tsc --noEmit -p .                         # exit 0
cd app/web && npx vitest run                                # 7 files, 55 tests passed, exit 0
cd app/web && npx eslint .                                  # exit 0
cd app/api && uv run --frozen python <scratch>/tp/attack2.py   # exit 0; results below
```
(node_modules was a temporary symlink to the main checkout's, removed afterwards.)

### Fix by fix

| Fix | What I checked | Result |
|---|---|---|
| 1 illustration isolated | Fixtures moved to 2024-01-01/02 with ids `ILLUS-1..4`. I checked overlap against all three committed stores (default: 2024-11-12, 11-20; export_small and export_best_real: 11-05..11-12): none. `test_illustration_never_overlaps_a_real_store` asserts this for the default store, and that real nights return 404. | HOLDS |
| 2 labelled everywhere | Kicker "ILLUSTRATION", h2 "ILLUSTRATION: synthetic, not a real night" (`TopParlaysPanel.tsx:163-168`), an "Illustration" tag on every ticket (`:64`), breakdown links hidden (`:47`). | HOLDS |
| 3 pre-tip enforced | `AwareDatetime` on all three times. `price_at >= tipoff` gets 422 (`top_parlays.py:81`). `made_at >=` the earliest tip gets 422 (`:172`). Naive and non-timestamp times get 422 (tests). New gap: `price_at` later than `made_at` is accepted, and the fixture itself does it (price 23:05Z, made 22:10Z; see R3). | HOLDS (minor gap) |
| 4 verdict guarded | `positive_ev_found` needs `illustration` false, `gate.open` true and some `ev_low80 > 0` (`:174-177`). **But `gate.open` can be manufactured** (R1). | **FAILS through R1** |
| 5 copy | "legs calibrated; joint not yet tested", the 80% range "has not been checked yet", "not a tradable price". | HOLDS |
| 6 ET stale, post-tip, date shown | `etDate` uses America/New_York (`topParlays.ts:65-80`). The tests cover 01:00Z = previous ET evening and the 03:59:59Z/04:00Z boundary (EDT). `madeAfterTip` is added. The h2 shows `longDate(slate_date)`. The Parlay page reads `?date=` and flags a night that is not today in ET. | HOLDS |
| 7 gate object | `Gate` refuses `open` when it disagrees with `settled >= settled_min and slate_dates >= dates_min and skill > 0` (`:126-144`). The counters render as CS 1.2 B, and HYPOTHESIS, UNPROVEN shows while the gate is closed (`:196`). **The thresholds `settled_min` and `dates_min` are free integers from the file**, so the gate only agrees with itself, not with the frozen gate of 30 settled and 14 dates (R1). | **FAILS** |
| 8 T490 caveat | Re-derived from `reports/alt_parlay_x/results.json` (B 5,000, date seed 2026; 95% per prereg line 33). 2-leg T1 point -0.09847, [-0.12870, -0.06769], so 9.8 points (6.8 to 12.9); n 3,337 = 2,407 (2023) + 930 (2024). 3-leg -0.08713, [-0.11948, -0.05506], so 8.7 (5.5 to 11.9); n 3,360 = 2,481 + 879. Both OVERCONFIDENT, BH q 0.001, same sign in both seasons. **The numbers are correct.** Wording nit (R5): the population was BetRivers alternate-line *overs* priced with the *raw* model. | HOLDS (nit) |
| 9 shape invariants | Size and leg count, at most 3 tickets, ranks 1..k, range, ev order, joint vs leg product (cross-game), price dead zone: all get 422 with tests. **But a missing size key is accepted** (R2). | PARTIAL |
| 10 illustration required | No default; the test asserts the flag on the API response. | HOLDS |
| 11 date pattern | `fullmatch([0-9]...)` + `fromisoformat`; tested with trailing newline and Unicode digits. | HOLDS |
| 12 directory | Empty or missing `APP_TOP_PARLAYS_DIR` gets 503; a symlink out of the directory is treated as missing (tested). Unset still falls back to the fixtures, which is now harmless (fix 1). | HOLDS |
| 13 saved copy | Notice when `fromCache` (`TopParlaysPanel.tsx:251-258`). | HOLDS |
| 14 evText | Rounds before choosing the sign; `-0.004` gives "+$0.00" (tested). | HOLDS |
| 15 key | Index plus rank. | HOLDS |
| 16 "worth it" | The gate clause is added (`:226-227`). | HOLDS |
| 17 banned phrases | `why`, `reason`, `caveats` and `calibration.status` are screened (`top_parlays.py:31-33`). Refused: "Lock it in.", "a lock.", "Hammer". **Accepted:** "LOCKED IN", "can’t lose" (curly apostrophe), "Guarantee", "easy money", "fade the public", "sure thing". `ranking` and `empty_reason` are rendered but not screened ("LOCK of the night, guaranteed" in `ranking` served; "free money tonight" in `empty_reason` served). The minimum my fix asked for is met except the curly-apostrophe "can't lose". | PARTIAL (R4) |

### New findings (numbered, file:line on 3c86c08)

R1. **Blocker.** `app/api/src/parlayhitters_api/top_parlays.py:128,130`: `settled_min: int = Field(gt=0)` and
`dates_min: int = Field(gt=0)` let the file set its own bar. Attack: `gate = {settled 1, settled_min 1, slate_dates 1,
dates_min 1, skill_vs_market 0.0001, open true}`, `illustration false`, `verdict positive_ev_found`, one `ev_low80 0.01`. The
API serves it (200), and `TopParlaysPanel.tsx:117-125` drops "Keep your money" for "The gate is open … the model has a live
record that passed its gate". Fix: `settled_min: Literal[30]` and `dates_min: Literal[14]` (the frozen gate in contract v1
"Rules" and FORWARD_PREREG), or app-side constants `SETTLED_MIN = 30`, `DATES_MIN = 14` with the check computed from those and
the file's values ignored. Add a negative test `gate_thresholds_lowered` to `NEGATIVE`. State in `top_parlays_v2.md` that the
thresholds are fixed values, not parameters. The counters themselves (`settled`, `slate_dates`, `skill_vs_market`) remain
producer-asserted; the app cannot check them against `paper_trades`. Record that as a residual.

R2. **Major.** `top_parlays.py:157`: `sizes: dict[Literal["2","3"], list[Ticket]]` accepts `{"2": [...]}` and `{}`. The UI
then reads `d.sizes["3"].length` (`topParlays.ts:89`) and spreads `d.sizes["3"]` (`topParlays.ts:84`); both throw on
`undefined`. `Body` calls `madeAfterTip` first (`TopParlaysPanel.tsx:159`), and there is no error boundary, so the Today page
goes blank. Found by reading the code; the API accepting the file was confirmed by attack. Fix: a validator requiring
`set(self.sizes) == {"2", "3"}` (or a `Sizes` model with both keys required by alias), plus a negative test `missing_size_key`.

R3. **Minor.** `top_parlays.py:81`: add `price_at <= made_at` for every leg (a file cannot hold a price stamped after it was
made). Fix the fixture so the price is stamped before the file is made: `app/fixtures/build_top_parlays_fixture.py:51`
(`price_at` 23:05Z) against `:85` (`made_at` 22:10Z); for example `price_at = f"{date}T21:40:00Z"`.

R4. **Minor.** `top_parlays.py:31-33`: extend the pattern to
`\b(locks?|locked|guarantee[ds]?|free[\s-]money|can[’']?t lose|cannot lose|hammer|best bets?|easy money|sure thing|fade the public)\b`.
Normalise `’` to `'` before matching. Screen `ranking` and `empty_reason` as well (`:154,156`). Add the curly-apostrophe and
"LOCKED" cases to `NEGATIVE`.

R5. **Nit.** The T490 caveat in `build_top_parlays_fixture.py:15-19` and `docs/contracts/top_parlays_v2.md` (example
`caveats`): say what population it covers. Suggested text: "Two-leg parlays of alternate-line overs the raw model put at 70%
or more (BetRivers) hit 9.8 points less often than the raw model said (95% CI 6.8 to 12.9, n 3,337 parlays, date-clustered,
2023-24 and 2024-25; T490). Three-leg: 8.7 points (5.5 to 11.9, n 3,360). These were raw chances; the chances shown here are
from calibrated legs." Stale text also remains: `build_top_parlays_fixture.py:1` says "top_parlays/1 contract" and
`topParlays.ts:1` cites `top_parlays_v1.md`. Point both at v2, and add a "superseded by v2" line at the top of
`top_parlays_v1.md`.

### To reach APPROVED
R1 and R2 with their negative tests. R3-R5 can follow in the same branch or later. Nothing else blocks.

### Not verified (re-check)
- The panel was not rendered in a browser. The R2 crash is derived from the code, not observed.
- The producer still does not exist, so the gate counters' truth against `paper_trades` is unverifiable from app/.

### Drafted rows (not appended)
- DECISIONS draft: "2026-10-15. Top-parlays fixes 3c86c08 re-check BLOCKED on R1 (gate thresholds file-supplied) and R2
  (missing size key crashes the panel); fixes 1-3, 5, 6, 8, 10-16 hold, and the T490 numbers were re-derived exactly. Reverse:
  R1 and R2 merged with negative tests."
- BEST_PRACTICES draft: "A gate's thresholds live in the frozen rule and in the reader's code, never in the file being judged
  (top-parlays R1, 2026-10-15)."

---

## Diff-only re-check of R1-R5: origin/agent/app-top-parlays-fixes 54b28fe (on 3c86c08), 2026-10-15

Scope: `git diff 3c86c08 54b28fe`, 9 files: `top_parlays.py`, its tests, the fixture builder and both fixtures,
`TopParlaysPanel.tsx`, `topParlays.ts`, and contracts v1/v2. I re-ran the same attack file (`tp/attack3.py` = `attack2.py`
pointed at 54b28fe) in a detached worktree. Season 2025 was not loaded.

Verdict: APPROVED

### Checks (unpiped, exit codes)
```
python3 app/fixtures/build_top_parlays_fixture.py && git diff --exit-code app/fixtures   # build 0, drift 0 (committed JSON = builder output)
cd app/api && uv run --frozen ruff check src tests          # exit 0
cd app/api && uv run --frozen ruff format --check src tests # exit 0
cd app/api && uv run --frozen mypy src                      # exit 0
cd app/api && uv run --frozen pytest -q -p no:randomly      # 252 passed, 1 skipped, exit 0
cd app/web && npx tsc --noEmit -p .                         # exit 0
cd app/web && npx vitest run                                # 55 passed, exit 0
cd app/web && npx eslint .                                  # exit 0
cd app/api && uv run --frozen python <scratch>/tp/attack3.py   # exit 0
```

| Item | Change | Attack result | Pass |
|---|---|---|---|
| R1 gate thresholds | `settled_min: Literal[30]`, `dates_min: Literal[14]` (`top_parlays.py:130,132`). The contract says the thresholds are fixed. The residual (counters are producer-asserted) is stated in the contract. | The 1/1 lowered-gate `positive_ev_found` file now gets 422. Tests `gate_thresholds_lowered` and `gate_settled_min_only_lowered` (29) are added. | PASS |
| R2 size keys | `set(self.sizes) == {"2","3"}` required. A `useErrorBoundary` wrapper (`TopParlaysPanel.tsx:250-263`) renders "could not be shown" with "Keep your money" instead of a blank page. | `{"2": [...]}` and `{}` get 422. Tests `missing_size_key` and `no_size_keys`. | PASS |
| R3 price after made | `price_at > made_at` gets 422. Fixture `price_at` is 21:40Z, before `made_at` 22:10Z. | Refused, test `price_after_made`. The fixture rebuilds with no drift. | PASS |
| R4 phrase screen | Wider pattern; curly apostrophes normalised; `ranking` and `empty_reason` screened. | "LOCKED IN", "can’t lose", "Guarantee", "easy money", "fade the public", "sure thing", and banned text in `ranking` and `empty_reason` all get 422. Seven new tests. | PASS |
| R5 wording | The T490 caveat names alternate-line overs, BetRivers, raw vs calibrated; the numbers are unchanged from the re-derivation (9.8 [6.8, 12.9] n 3,337; 8.7 [5.5, 11.9] n 3,360). Builder docstring and `topParlays.ts:1` now cite v2; v1 carries "Superseded". | n/a | PASS |

Regression: the illustration still overlaps none of the three committed stores. v1 schema is still refused.

### Nits (non-blocking, no re-review needed)
N1. `docs/contracts/top_parlays_v2.md:21`: the example's `"caveats": [ "…legs."` lost its closing `]`, so the example is no
longer valid JSON. Restore `."]`.
N2. The error boundary has no test. A vitest that renders `TopParlaysPanel` with a payload that throws in `Body` and asserts
the "could not be shown" text would pin it.

### Not verified
- No browser render. The error boundary is judged from code (preact `useErrorBoundary` catches errors in child `PanelInner`).
- The gate counters' truth against `paper_trades` is producer-side; it stays a stated residual.

### Drafted rows (not appended)
- DECISIONS draft: "2026-10-15. Top-parlays fixes 54b28fe APPROVED by the adversary (R1-R5 hold; nits N1-N2 open). Merge to
  main is the main session's call after its own checks; the residual (producer-asserted gate counters) is carried to the
  producer's review."
