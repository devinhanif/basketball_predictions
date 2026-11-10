# Adversary review: Today's picks + NBA headshots (app only)

Branch `agent/app-todays-picks-headshots` at `0cc5183c` vs `main` (`c81b948b`). 21 files, +567/-14.
Scope: app display and one API field. No statistical claim, no model and no rule, so the six statistical checks
do not apply. Reviewed against Devin's ask, the LINE_CAL_LIVE display lock (DECISIONS 2026-11-10 02:51) and the
public-desk privacy rules.

Outcome: **WOUNDED**. The plumbing is safe, but two honesty defects need fixing before merge. The site is served
live from main, so a merge publishes them at once.

Verdict: BLOCKED

## Top finding (M1): the "why" text states something false on every row of the current export

`app/web/src/picksLogic.ts:71`, in the `model_weight === 0` branch:
"so its ${pct(model_prob)} sits at the ${price} price, and the ${fee} fee leaves it negative."

What I checked: `data/export/latest/parlay_of_day.json` on main (date 2026-11-09, verdict no_positive_ev_found,
read-only):

```
python3 -I - <<'EOF'   # see the scratchpad command in the hand-back; exit 0
r = single-leg player records
max|model_prob - (price - spread/2)| = 0.0      n = 2451   (model_prob is exactly the market mid)
max|model_prob - price|             = 0.49     n = 2451   (never equal: share equal = 0.0)
model_weight == 0 on 2451/2451; drivers present on 0/2451; ev > 0 on 0/2451
EOF
```

The top row shown would read "Kevin Durant 1+ threes ... its 82.5% sits at the 83c price, and the 1c fee leaves
it negative." That is false. 82.5% is the market's middle, not the 83c ask. The ev of -0.015 is half the spread
(0.5c) plus the fee (1c), not the fee alone. The "why" does not invent a number, but it states a relation the
numbers contradict.
The `Model chance` label (`TodaysPicks.tsx:37`) has the same problem: at weight 0 it shows the market middle under
the model's name.

Smallest fix:
- `picksLogic.ts:71` becomes `The model defers to the market until its record clears the gate, so its chance is the
  market's middle, ${pct(c.model_prob)}; buying at the ${price} ask plus the ${fee} fee leaves it ${c.ev < 0 ?
  "negative" : "positive"}.` Add a vitest case that pins the words and rejects "sits at".
- At `TodaysPicks.tsx:37`, label the cell `Market middle (model weight 0)` when `c.context?.model_weight === 0`,
  and `Model chance` otherwise.

## M2: negative-EV rows sit under a "picks" heading with rank numbers

At `TodaysPicks.tsx:64` the heading is always `The model's morning picks`, and every row gets a rank badge (line 15).
With 0 positive of 3,462 tonight, and on most nights, the public home page shows five ranked negative-EV contracts
under "The model's morning picks". The status line under the heading does say "No bet is positive after fees this
morning; these are the closest", and each EV cell is red, so this is wording, not a leak. But the brief requires
that a negative-EV row is never presented as a pick. In the positive state the same happens to the rows below the
positive ones: a row with ev <= 0 is ranked under "N contracts cleared the engine's bound".

Smallest fix:
- Choose the h2 after `head` is computed (move it inside the render callback): `The model's morning picks` only when
  `head.kind === "positive"`, else `Closest to break-even this morning (none is a bet)`.
- In `Pick`, when `c.ev <= 0`, drop the rank badge or add a visible `not a bet` tag.
- Keep the panel label "Today's picks", since that is Devin's name for it.

## Minor
- m1 `picksLogic.ts:89`: the positive head's count comes from `n_flagged_positive` (the bound), but `anyPositive`
  tests `ev > 0`. If a row has ev > 0 and ev_low <= 0 while the verdict is something other than
  no_positive_ev_found, the head could read "0 contracts cleared the engine's bound" styled as positive. Fix: use
  `kind: "positive"` only when `(nPositive ?? 0) > 0`, else the negative wording.
- m2 Privacy, third party: `Avatar` now fetches headshots wherever a `playerId` is passed. That includes the desk
  pages `History.tsx:359`, `Hedge.tsx:96/120/210`, `Signals.tsx:130` and `Admin.tsx:78`. cdn.nba.com therefore
  sees the viewer's IP and the player ids on a private slip or hedge. `no-referrer` strips the page path, so the
  site's routes do not leak, but the set of players does. Devin accepted a public link; I am not sure he accepted it
  for private desk pages. Fix if wanted: add a `remote?: boolean` prop (default false) and set it only on the
  public pages (Today, Game, Leg, Explore, Live, TodaysPicks).
- m3 `docs/APP_SECURITY_BASELINE.md:31`: "the CSP allows img-src ... cdn.nba.com" reads as already in place, but no
  CSP exists in the code (`grep -rn 'img-src\|Content-Security' app/web/index.html app/api/src` finds nothing).
  Change it to "when the CSP is added, img-src must allow ...".
- m4 Records cannot carry `drivers` yet (0/2451), so the minutes/teammates sentence never shows. The API test uses
  a synthetic `drivers`. That is fine, but the "why" will say only the weight-0 sentence until the export adds
  drivers.

## Checks run

| Check | Command | Result | Pass |
|---|---|---|---|
| API tests | `uv run --frozen pytest -q` (app/api of 0cc5183c, scratch copy) | 434 passed, 1 skipped, exit 0 | pass |
| Web tests | `npx vitest run` (app/web of 0cc5183c) | 29 files, 315 tests passed, exit 0 | pass |
| Types | `npx tsc --noEmit` | exit 0 | pass |
| Raw P never served | `_row`/`_context` in `edges.py` forward neither `raw_model_prob` nor `raw_ev` | absent | pass |
| Calibrated/blended P | `model_prob` is the engine's served (weight-shrunk) value | weight 0, so it is the market mid | pass (label fails, M1) |
| No outcome on panel | TodaysPicks reads no result, final or actual; a vitest case pins this | no outcome | pass (LINE_CAL_LIVE) |
| "No positive" stated | live export: verdict no_positive_ev_found, 0/3462 positive gives the negative head | correct | pass |
| Run time shown | `Morning run, prices as of {stamp(engine.as_of)}` | shown only when the engine loads | pass |
| Wrong-night export | `runIsForNight(date, data.report_date)` hides picks and says so | pass | pass |
| Empty or missing fields | `context?` optional, `?? null` on counts, empty list gives the "none" head | no crash path found | pass |
| Headshot URL | `headshot.ts`: positive integer id only, fixed host and path | pass | pass |
| no-referrer, lazy, size, alt, onError fallback | `Avatar.tsx` | present | pass |
| Other external hosts | `grep -rln 'https://' app/web/src` finds only headshot.ts plus font licences | cdn.nba.com only | pass |
| `context` field privacy | public names of teammates out (truncated to 60 chars, max 3) and engine numbers; no secret, user or desk data | pass | pass |
| Merge conflict | `git merge-tree --write-tree main 0cc5183c` | only `docs/DECISIONS.md`, append-only (one row each side at the end) | note |

There is no n or CI because nothing here is a statistical claim. The counts (n = 2,451 single-leg player records,
3,462 evaluated) describe the one export the defect was checked on.

## Playbook check
Model class: none. This is a display layer over the engine's stored `parlay_of_day.json`.
(1) Information or capacity: n/a, nothing new is predicted.
(2) Data for the class: n/a.
(3) Test validity: the display tests pin wording and selection. They did not catch M1 because the fixture sets
model_prob equal to price (0.48 vs 0.5 in the API test; the web fixture was not checked against a real export).
(4) Lineage and inherited guards: the LINE_CAL_LIVE display lock and the raw-P removal are both kept.
(5) Scored against: nothing. The panel makes no forecast claim beyond the stored EV.

## Single most likely failure mode
A visitor reads "The model's morning picks" and "its 82.5% sits at the 83c price" as the model having a view near
the price. In fact the model has zero weight and every row is negative after fees.

## Not verified
- Rendering in a browser, and whether cdn.nba.com actually serves 260x190 for every id or hotlinking works. I did
  not start servers, per the instructions.
- Whether the engine's `ParlayState.report_date` always matches `/api/contracts/top`'s `report_date`. Both read
  the same file, so they should.
- The Shell/Welcome/Settings avatars (uploaded `src`) are unchanged and were not exercised.

## Recommended changes (not applied)
M1 and M2 as above, then re-run vitest and tsc. m1 to m3 are optional. When merging, resolve DECISIONS.md by
keeping both appended rows in time order.

## Drafted DECISIONS row (not appended)
| 2026-11-10 hh:mm | Adversary on app Today's picks + headshots (docs/reviews/app_picks_headshots_2026-11-10.md): WOUNDED. M1: the weight-0 'why' says the model's chance 'sits at the price'; on the 11-09 export it is the market mid on 2451/2451 rows, never the ask. M2: negative-EV rows ranked under 'The model's morning picks'. Fix both, then merge on Devin's go. | Adversary sequence. | n/a |
