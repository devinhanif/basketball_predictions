# Lane J2a: the Jarvis look (tokens, components, shell, first-run chooser)

Branch `agent/app-jarvis-look`, worktree `nba-wt-1117-jv-look`. Edits only under `app/web/` and this file. Started 23:27 CST
2026-11-16 (from `date`); report finished 00:47 CST 2026-11-17. Nothing pushed. No new dependency, no font added, no external URL.

## How to check it yourself

```
cd app/web && npm ci
mkdir -p artifact-data && cp <a baked.json> artifact-data/
npm run check && npm run build:artifact            # must end "checks passed"
cd ../api && uv sync && APP_LIVE_DIR=$PWD/../fixtures/live uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port 8070
cd ../web && API_ORIGIN=http://127.0.0.1:8070 npx vite --host 127.0.0.1 --port 5211
node scripts/shots.mjs regular http://127.0.0.1:5211 <outdir>          # LENS=market ONLY=today,... W=390 optional
WALK_API_PORT=8072 WALK_WEB_PORT=5212 ../scripts/walk.sh --widths      # plain, injected, 360/768/1180
node scripts/learn-dom.mjs http://127.0.0.1:5211 9661
```
To see the first-run chooser: open the app with `localStorage` cleared (or remove `ph.welcomed`).

## Reference build
The donor kit was rebuilt from the flat files in scratch (`.../scratchpad/jarvis-ref/ui`, copies only), `npm install
--ignore-scripts`, served on 5220, and photographed at 375/768/1280, dark and light, Simple and Analyst: sign-in, chooser,
overview, compare, ideas, signals, hedge, history, alerts, preferences, search, a modal, the mobile menu and the slip drawer
(86 PNGs, `.../scratchpad/jv-donor-shots/`). It built first try. Nothing from it entered the repo; its server is stopped.

## Visual gap list (donor vs app before this lane) and what was closed
| Area | Donor | App before | Now |
|---|---|---|---|
| Palette | `#10131d` bg, purple accent | same | unchanged (it was already the same) |
| Radii / elevation | cards 16, hero 17-18, dialog 18; soft shadow scale | one 14 radius, no elevation | radius scale + elev-0..3 tokens; cards 16, dialogs and hero 18 |
| Gradients | purple radial hero, quiet header card, nav-active gradient, tilted brand tile, page glow | none | five gradient tokens; `Card variant="feature" / "header"`; brand tile; chooser glow |
| Sidebar | 231 px, brand tile + tagline, 400-weight items, active = gradient + 3 px left rail, quiet section labels, paper note with glow, profile strip at the foot | 248 px, flat wordmark, bold muted items, active = solid soft fill | 240 px, tilted gradient brand tile + one-line tagline, medium items, active gradient + rail, paper note glow, profile strip |
| Top bar | 76 px, borderless ghost icons, quiet search with kbd, chevron breadcrumb | boxed 44 px icon tiles everywhere | ghost icons (44 px targets kept), boxed search only from 480 px, chevron crumbs, menu button leads on phones |
| Mobile menu | slide-in drawer with scrim | dropdown sheet, no Escape, no trap | left drawer with scrim, focus trap, Escape, focus returns, scroll lock |
| Data line + health strip | (none) | two bands; the strip was 3-4 lines of jargon on phones | one band aligned to the page column; strip is one line + Details |
| Page heading | eyebrow, 28 px title, one-line description, actions right | kicker + very large h1 | `PageHead` component; Discover uses the block with Detail switch on the right |
| Badges | 6 px radius, tinted border | pill | 6 px radius, tone-tinted border; provenance + freshness badges added |
| Tabs / segmented | 9 px tabs, active tint + accent border; dark tray switch with raised active | pill tabs; solid purple active | as donor |
| Tables | small caps headers, rule lines, hover tint | plain | as donor, plus optional sticky header |
| Dialog | blur scrim, 18 radius, quiet close | flat scrim, 14 radius, boxed close | as donor |
| Chooser | full-page two-card chooser after sign-in | none (removed as a blocking modal earlier tonight) | two-card chooser as step one of the in-page intro (never blocks) |
| Right panel | persistent slip rail >= 1280, bottom sheet below | none | generic `SidePanel` (rail >= 1280 that narrows the page; bottom sheet below); empty by default |
| Type | donor goes down to 6-9 px | 12 px floor | 12 px floor kept everywhere (deliberate) |

## Tokens (theme.css; dark and light; nothing removed or renamed)
| Token | Role | Donor source |
|---|---|---|
| `--subtle` | decorative rules only (fails AA as text) | `--subtle` |
| `--line-strong`, `--accent-line` | hover border; active/selected hairline | `#586078`, `#9f8cff16`-family |
| `--cyan`, `--cyan-soft`, `--amber`, `--amber-soft` | status colours (info, caution) | `--cyan`, `--amber` |
| `--radius-2xs` 4, `--radius-lg` 16, `--radius-xl` 18 | radius scale (with existing xs 6, sm 9, 14) | cards/hero/modal |
| `--elev-0..3` | elevation scale | `--shadow` and toasts |
| `--grad-feature`, `--feature-line`, `--on-feature`, `--on-feature-muted` | hero card | `.feature-card` |
| `--grad-card`, `--grad-tile`, `--grad-nav-active`, `--grad-brand`, `--on-brand`, `--grad-page` | header card, stat tiles, active nav, brand tile, page glow | `.match-card`, `.metric-strip`, `.nav-item.active`, `.brand-icon`, `.onboarding-page` |
| `--focus-ring` | input focus halo (outline focus ring unchanged) | focus styles |
| `--fs-3xl`, `--fw-regular/medium/bold/heavy`, `--track-title/head/kicker` | page-title size, weights (only bundled weights), tracking | h1/h2/eyebrow |
| `--sidebar-w` 240, `--topbar-h` 64, `--sidepanel-w` 360 | shell geometry | 231 / 76 / 360 |
Name mapping kept: donor `--panel/--panel2/--panel3/--border/--text/--sidebar/--purple` = app `--surface/--surface-2/--surface-3/--line/--ink/--head/--accent-ink`. Semantic roles from tonight (model, mine, base, better/worse/same, edge labels) untouched.

## Components for the next lanes (all exported from `src/ui`)
- `PageHead({ kicker?, title, description?, actions?, level?: 1|2, class? })`: the page heading block (title is the h1 unless level 2).
- `SectionHead({ title, sub?, action?, id?, level?: 2|3 })`: section title row with an action on the right.
- `Card({ title?, kicker?, tone?, variant?: "feature"|"header", actions?, flat?, id?, class?, children })`: `.panel`; `feature` = the one purple hero per page; `actions` puts a header row in.
- `Badge({ tone, icon, word, title?, class? })` (now takes `class`); `ProvenanceBadge({ source })`: live / delayed / manual / sample from the stored source string, anything else verbatim in grey; sample is dashed. `FreshnessBadge({ at, oldAfterHours, label?, now? })`: "Pinnacle quote: fresh · 3 h ago", "Old · 3 days ago", "Time not reported" (never "fresh" by default).
- `FilterChips({ label, options: {value,label,count?}[], value, onChange, class? })`: toggle pills (`aria-pressed`, tick + colour).
- `StateBlock({ kind: "loading"|"empty"|"stale"|"partial"|"error"|"reconnecting", title?, children?, action?, problem?, inline?, lines? })`: one family, icon + word + sentence each; `error` renders the existing `ProblemPanel` from `problem` (registry code).
- `setSidePanel({ owner, title, openLabel?, content })` / `clearSidePanel(owner)`: fill or clear the shell's side panel; `SidePanelHost` is mounted in the shell. Rail from 1280 px (page narrows, nothing covered); below, a button opens a bottom sheet (modal: trap, Escape, focus back, scroll lock, safe areas). Empty slot renders nothing.
- `Table` gains `sticky` (header stays in view inside a scrolling frame).
- Pure logic, tested in `jarvisKit.test.ts`: `provenanceOf`, `PROV_LOOK`, `freshness`, `FRESH_LOOK`, `STATE_LOOK`, `panelLayout`, `panelIsModal`, the slot's ownership rules.
- `MenuDrawer` (shell only), `Welcome` now has the chooser step.

## Simple and Analyst today (audit of what reads `mode`; no page restyled)
| Page | Simple | Analyst |
|---|---|---|
| Discover | one line per key player ("typical 24.3 pts" / "over 23.5 pts 41%") | each key player as a block: the plain range sentence (typical night with its middle-half range) or the line sentence, Market-lens comparison line |
| Game | player rows closed (`<details>`), opened on tap | every player row open |
| Leg | headline, panels, then "More depth: other lines, splits, teammates, role, opponent" folded | the same depth shown inline |
| Everywhere else | no difference | no difference |
The switch lives in the profile menu, Preferences ("Detail level"), Discover's heading, Game and Leg. Not done (next lane): Analyst showing model names, quantile tables, n and intervals, and the stored-record link more prominently; today those appear in both modes where the page already shows them.

## First-run flow (decision)
One flow, in the page: step 1 the chooser (two cards, radio group, arrows move, "Start in Simple/Analyst"; "Skip, just let me look" and the close button keep Simple and end the flow), step 2 the existing five-point intro (focus moves to its heading after a pick). Never `aria-modal`, never takes focus on load, shown until dismissed (the existing `ph.welcomed`), choice stored in the existing `ph.mode`. Visitors who already dismissed the intro never see the chooser.

## Health strip (same stored fields, plain words)
Line (Market lens): "Newest Pinnacle quote 5 h ago · Export 5 h ago · 6,604 on the production model   Details". On a phone only the Pinnacle age stays on the line (an old export also stays). Details: export time and schema; "The AVG comparison model (props_avg_live2) is set aside on 15,276 rows because its own run flagged its weights as out of date." / "6,604 shown forecasts use the production model instead."; every book's newest quote; what the export does not report. Learn lens: "Export made 5 h ago, schema 2". Reasons map from `nba/export/best_model.py avg_freshness`; an unknown reason or model id is shown verbatim. Tests: `healthStrip.test.ts`.

## Commits (oldest first, each passed `npm run check && npm run build:artifact`)
| Commit | Theme |
|---|---|
| `963355c5` | tokens |
| `cc3d1380` | components, state family, badges, SidePanel (+ `jarvis.css`, loaded last in both entries) |
| `c3a4d5db` | health strip |
| `92efcbdd` | first-run chooser (in `Welcome.tsx`); fan chart labels at 12 px on phones |
| `c3e003ba` | shell finish (CSS) and Discover top (`pages/Today.tsx` heading) |
| `ad6c5fcf` | audit fixes; `MenuDrawer` component |
| `5d2b4ee8` | `Shell.tsx`: drawer, brand line, chevron crumbs, side panel slot |
| (this file) | report |

## Numbers (before = base `b6e16d67`, after = `5d2b4ee8`)
| What | Before | After |
|---|---|---|
| vitest | 408 / 408 (40 files) | 421 / 421 (42 files: `jarvisKit.test.ts` 10, `healthStrip.test.ts` 3); no test changed or removed |
| Regular build js | 390.52 kB (gz 122.73) | 400.48 kB (gz 125.44) |
| Regular build css | 76.30 kB (gz 15.69) | 100.89 kB (gz 20.65) |
| Artifact html | 4,520,376 B | 4,554,917 B ("checks passed") |
| Walker (fixtures): plain 390 seed 1 / injected 390 seed 2 / 360 / 768 / 1180 seed 3 | 0 (lanes B and C1) | 0 / 0 / 0 / 0 / 0 (run 00:31-00:38). An earlier run at 23:59 under heavy load (3 dev servers, 2 Chromes) logged 4 hits on seed 1 (3 "expected a not-found screen", 1 "blank page"); the same routes loaded correctly by hand and seed 1 rerun alone gave 0. |
| learn-dom | pass | pass ("Learn lens: no price words on any route", 00:45) |
| Contrast/size audit, Learn, 112 screens (390+1440, dark+light, Simple+Analyst), real export | not run on this data before | 0 findings |
| Same, Market lens, 64 screens | Discover analyst light: 3 contrast (Today's Picks labels, 3.68:1) | 2 findings, both pre-existing: a scrolling table on Leg (Market, 390, Simple) clips its "Price" column; it clips identically with `jarvis.css` removed. Not fixed. |

Screenshot directories (all under `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/`): donor `jv-donor-shots/`; app before `jv-app-before/`, `jv-app-before-mkt/`, `jv-app-before2/`; components gallery (scratch-only render, placeholder copy) `jv-gallery/`; chooser `jv-first/`; shell and Discover `jv-b1/`, `jv-b3/`; dialogs `jv-b2/`; drawer `jv-drawer/`; health strip `jv-a1/`, `jv-a2/`; final audits `jv-shots-final/`, `jv-shots-final-mkt/`. Looked at: donor overview/chooser/menu/modal/slip; app Discover 375/768/1280/1440 dark and light, both lenses; chooser 375 and 1280; health strip open and closed; drawer 375 dark and 768 light; about and search dialogs; every state component, badge and the side panel rail and sheet.

## Decisions (what / why / what would reverse it)
1. One new stylesheet, `jarvis.css`, loaded last, restyles existing classes through tokens; no donor CSS pasted. Why: one import to remove reverts the look. Reverse: drop the import from both entries.
2. The 12 px text floor beats the donor's 6-9 px labels. Why: accessibility and the existing audit. Reverse: none advised.
3. Chooser is step one of the in-page intro, not a full-page screen. Why: the brief says it must never block, and there is no sign-in to follow. Reverse: render `ModeChooser` as its own route-independent page.
4. Flag lane had not landed after 40 min (checked 23:48-00:28); PHASE B went ahead with a five-hunk `Shell.tsx` patch (imports, brand line, crumb chevron, drawer, `<SidePanelHost />`). `router.ts`, `App.tsx`, `shellData.ts`, vite configs untouched. Reverse: revert `5d2b4ee8`.
5. Discover's Detail switch moved from the "View" disclosure to the heading's actions; the disclosure now says "View: <lens> lens (change)". Why: the donor puts the mode switch in the page heading. Reverse: restore the two lines in `Today.tsx`.
6. Health strip on phones shows only the Pinnacle age (and an old export); the rest is under Details. Why: one line without cutting a number. Reverse: drop `.hs-opt`.
7. Sidebar brand line reads "NBA forecasts" (longer wording overflowed 240 px).
8. `--subtle` exists but is not used for text (fails AA).
9. Fixed two pre-existing findings on the way (fan labels 10.8 px on phones, Today's Picks labels 3.68:1).

## Not done
- Page-by-page restyle (next lane), including Analyst exposing model names, quantiles, n/intervals and record links more prominently.
- Merge of `agent/app-jarvis-followup` (not landed while I ran). Expect small conflicts in `Shell.tsx` only.
- The donor's metric strip, insight cards and radar table finish on Discover below the heading (content belongs to the pages lane).
- Search palette lists Market-only screens in the Learn lens (pre-existing, seen in `jv-b2`); not mine to change.
- Visual-regression checks at 375/768/1280 as tests (the screenshot tooling is manual).

## Not verified
- No real phone, Safari or Firefox; headless Chrome only. No screen reader listened to.
- The side panel has no real content anywhere; rail and sheet were seen only in a scratch-only gallery render.
- Artifact build not screenshotted (built and checked only); the chooser does show there too.
- Reduced motion: covered by the existing global rule; not toggled in a browser.


