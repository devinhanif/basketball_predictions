# App: colour as navigation, light and dark (2026-11-24)

Branch `agent/app-colour-nav` (worktree `/Users/devin/Downloads/nba-wt-colour-nav`), cut from main 7039b4ed, then merged in order
`origin/agent/app-combo-screen` (0268710f) and `origin/agent/app-live-card` (c831e269); both merged without conflict. Everything is under
`app/` plus this file. Decision row: last row of `docs/DECISIONS.md` (2026-11-24 18:05). Nothing here is merged to main.

## What changed

- One colour per job. `app/web/src/jobs.ts` is the single map from a route to a job and holds each job's tab word, title, sentence and icon.
  `app/web/src/colour.css` holds every colour as a token (dark, light, and the job scopes) and loads after the older sheets.
- Each screen wears one job colour: header title (`.job-title`), the 3px rule under the header (`.topbar::after`; on neutral screens the
  four-segment bar), section labels (`.kicker`, `.eyebrow`, `.label`, door-group labels), links, primary buttons, focus rings and the active tab.
  The old purple accent names (`--accent`, `--accent-ink`, `--on-accent`, `--focus`, `--model*`, the gradients) are now derived from the screen's
  job tokens, so every old use became the job colour or a neutral. The purple hero card is a plain raised surface with the job rule.
- Five tabs (phone, `nav-bottom jobtabs`) and five links at the top of the sidebar (desktop): icon (thin stroke SVG), word, and a 3px bar of the
  tab's colour even when inactive; the active one is filled.
- Doors (`Doors.tsx`): coloured outlined box in the DESTINATION's colour with the destination's name written on it. Added where the destination
  already exists: Team picks (per game: PROPS, EXPLORE), Player props (per game: TEAMS; bottom: SLIP), Game page (PROPS, EXPLORE), Leg page
  (TEAMS, EXPLORE, SLIP), Combo (PROPS, SLIP), Parlay of the day (PROPS, EXPLORE, TEAMS).
- Home (`#/`): "What are you here for?", four large doors (sentence plus the stored game count and prop count; Explore's fact is the four script
  names; Slip has no fact because the app holds none to show), then the three-line legend. All earlier home content (answer headline, night
  tabs, live strip, picks, verdict, desk panels, games, tiles) is unchanged and sits directly below the legend on the same page.
- Two new screens that only re-use the slate the home page already loads (no new API, no new data): `#/teams` (Team picks: the games list with
  win chances, live strip, night tabs) and `#/props` (Player props: each game's key lines with the stored chance and the calibrated-or-model tag,
  a 30-70% bar, a row opens the leg page; `?game=` shows one game). Router, search and artifact names updated.
- Theme: Auto / Light / Dark segment added to the profile menu (Preferences already had the three choices); stored in `ph.theme` (try/catch via
  `prefs.ts`); `public/theme-init.js` sets `data-theme`, `data-mode` and the `theme-color` meta before first paint; `color-scheme` is set in both
  blocks; `HEAD_COLOR` and `index.html` agree (test exists). `data-mode` (simple or analyst) is now on the root: Simple makes doors larger and tinted.
- Rule 3 (green and red only for up or down): error panels, the live dot, the health dot, foul trouble, warnings, "out" status, on-pace chips,
  the betting module's error, negative and failed-toast colours, desk alarm borders and the `linked` rule moved to neutral ink or the job colour
  (each already had its own word or icon). Still green or red: up/down chances, hit/miss, "cleared the line", the better/worse comparison badges,
  and (Market lens only) the `edge-strong` badge and the compare badges built on `--better/--worse` (see "what I would decide" below).
- Rule 4: win bars no longer use team colours (home = job colour, away = grey, both teams named with their percentages under the bar). Team
  colours remain only in `Avatar` and the `TeamTag` chip (test pins this).
- Tests changed: `routes.test.ts` (known route list gains teams, props, combo; the game page back link is now `teamsHref(g.date)`, "Back to team
  picks"). New: `colour.test.ts` (111 tests).

## Route to job

| route | job |
|---|---|
| today (home), record, scorecard, tests, terms, help, alerts, settings, admin, history, notfound, betting module screens | TONIGHT (neutral, four-colour bar) |
| teams, game, live (list and one game), source (game's win chance) | TEAMS |
| props, leg, swipe, picks, signals, source (one prop) | PROPS |
| parlay (Parlay of the day), hedge | SLIP |
| combo | EXPLORE |

Live: no hue of its own. The live player card (`ScoreBoard`, `SlateBoard`) sits in a `data-job="props"` scope inside the TEAMS-coloured live page.
Judgement calls to confirm: the SLIP tab opens Parlay of the day (the only screen about whether a parlay holds); the combo screen, whose "Together"
and "Build" tabs are slip-like, is wholly EXPLORE; swipe and My picks are PROPS (over/under on player lines); Signals is PROPS, Hedge Finder SLIP.

## Tokens (both themes; contrast from `colour.test.ts`, computed WCAG ratios)

Ground, dark / light: bg #08070c / #f3f1f6, head #0d0b12 / #fbfafd, surface #14121c / #fbfafd, surface-2 #1c1927 / #ebe8f0, surface-3 #262236 / #e0dce9,
ink #e9e6f2 / #1b1826, muted #a7a1b8 / #55506a, line #2a2636 / #d6d2e0.

| token | dark ink=fill (on) | light ink=fill (on) | dark ink on bg / surface | light ink on bg / surface | on-fill dark / light |
|---|---|---|---|---|---|
| tonight | #e9e6f2 (#08070c) | #1b1826 (#f3f1f6) | 16.31 / 15.06 | 15.54 / 16.76 | 16.31 / 15.54 |
| teams | #4cc2ff (#08070c) | #0a6aa6 (#ffffff) | 10.01 / 9.24 | 5.16 / 5.57 | 10.01 / 5.79 |
| props | #ffb224 (#08070c) | #8a5200 (#ffffff) | 11.13 / 10.28 | 5.70 / 6.14 | 11.13 / 6.39 |
| slip | #ff6fd0 (#08070c) | #b0167a (#ffffff) | 8.07 / 7.45 | 5.78 / 6.23 | 8.07 / 6.48 |
| explore | #9d7bff (#08070c) | #5b3fd6 (#ffffff) | 6.42 / 5.92 | 5.99 / 6.46 | 6.42 / 6.72 |
| up | #6fe3a1 | #12703f | 12.60 / 11.63 | 5.48 / 5.91 | |
| down | #ff8f8f | #b3261e | 9.16 / 8.45 | 5.83 / 6.29 | |
| muted | #a7a1b8 | #55506a | 8.06 / 7.44 | 6.83 / 7.37 | |

The test also checks every job, up, down, ink and muted on surface-2, every fill against all three grounds at 3:1 (rules and bars), and that the
media-query light block equals the explicit light block. My light starting points all passed; none was adjusted.

## Where the two "Explore" labels are visible together

The purple tab "Explore" is on screen on every page (bottom tabs on a phone, sidebar links on desktop). The lens "Explore" segment is visible
with it on: home (Today), Team picks, Player props, a Game page, a Leg page, Scorecard, Forward tests, Key terms (all through `PageControls lens`).
In the Learn lens the Explore screen itself says "Combo mode is an Explore screen" with a "Switch to Explore" button (lens), under the purple
Explore title. The profile menu's lens text and Preferences do not show the lens segment, but "Explore" lens appears in the lens one-liners. Not renamed.

## What I would restyle next (not done)

Body type is still DM Sans and Manrope, not the mock-ups' monospace body (I used mono only for the tabs, the home question and the home caret);
corners are still rounded on most cards and buttons; panels, tiles, the hero and the Today answer card keep their old proportions; the data
banner and "notice" tones are an amber-brown that sits close to the PROPS hue; `--tonight` orange, `--mine` gold, `--amber` and `--cyan` are still
in use (some near the PROPS and TEAMS hues); the leg page's "Back to the game" link is in the PROPS colour though it leads to TEAMS (the door below
it is correct); per-row "+ SLIP" on props rows was not built because no slip exists outside the betting module (behind its switch); the command
caret and command line of the mock-ups were not built (the app has no command input).

## What I would decide with Devin

- `edge-strong` and the compare badges are green in the Market lens although "clears an edge after fees" is not a chance moving; strictly they
  break rule 3. I left them (a change touches the Market lens vocabulary).
- Whether the SLIP tab should wait for a real slip screen (today it is the Parlay of the day page, which is market-side and says "keep your money"
  in Learn and Explore).

## Checks (all unpiped, `nice`, exit code 0 each; logs in the session scratch area)

| check | before (main + two merges) | after |
|---|---|---|
| api ruff / ruff format / mypy | pass | pass |
| api pytest | 656 passed, 1 skipped | 656 passed, 1 skipped |
| web eslint / tsc | pass | pass |
| web vitest | 679 passed (63 files) | 790 passed (64 files) |
| web build | pass | pass |
| artifact build, flag parity | skipped (no baked data) / pass | pass / pass |
| browser route walk (`check.sh --browser`) | not run | pass, 0 violations |
| `check.sh --fast --betting` (switch ON) | not run | pass |

Screenshots (headless Chrome, `app/web/scripts/shots.mjs`, now with teams, props, combo and live-game screens and `PHONE_W`, `DESK_W`,
`VIEWPORT`, `NO_ANALYST` env switches): the DOM audit (size, contrast, overflow) reported nothing on the job screens at 375 and 1280 except an
existing clipped data-line banner on the combo screen at 375.

## What I could not verify

- A real phone; everything is headless Chrome on fixtures (the mock-ups' look, fonts and touch feel are judged from screenshots only).
- The betting module's own screens in light and dark: the ON build compiles, flag parity and tests pass, and I moved its red uses to neutral, but
  I did not photograph those screens. The Tour dialog, the Welcome first-run screen and the single-file artifact were not photographed either.
- The live pages against live data (only the fixture night 2026-11-09), and the home and Teams screens against the real export
  (`APP_EXPORT_DIR`): not run; fixtures only.
- `tests/test_layering.py` (repo root venv) was not run; no import in `app/` changed.
- The combo-screen data banner is clipped at 375px (audit finding); I did not trace whether it predates this branch.
- Contrast was computed for the token pairs, plus the rendered-DOM audit on the screens above; text on translucent `color-mix` tints (door
  hover, tinted Simple doors) is not computed.
