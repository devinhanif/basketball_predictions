# Design: the super app (parlayhitters + Jarvis in one)

_Draft 2026-11-08 for Devin to mark up. Nothing in this document has been built. Items marked ❓ are questions for Devin._

**Label:** Devin's request ("combine parlayhitters ui with the html file", "super app", 2026-11-08). It partly follows Claude's
earlier review of `~/Downloads/Jarvis-Interactive.html` (lead with honest status, make the numbers add up, fix phone layout,
contrast and the badge over the photo).

## 0. What we are combining

| | parlayhitters (`app/web`) | Jarvis prototype (`Jarvis-Interactive.html`) |
|---|---|---|
| What it is | Real Preact PWA, read-only FastAPI, reads `nba.export` files, tests and lint | One 2.4 MB minified HTML file, no source in the repo, sample data only |
| Strengths | Real data; honest rules built in (verdict line, n and CI, footer, paper only); plain language; Game and Leg pages are deep (line table, splits, calibration) | Layout system (sidebar on desktop, top bar on phone); Simple/Analyst modes; strong visual hierarchy; Signals, Alerts and Model History screens built around what JARVIS actually does |
| Weaknesses | Long walls of text; every card has the same weight; no home "answer"; no Jarvis data (plays, CLV, alerts) | Marketing slogans as headlines; the exciting card leads and the bad news is hidden (CLV −1.28¢, kill switch on); numbers that do not add up on screen; text at 5–10px with contrast 2.4–3.5:1; sign-in form clipped on phone; badge over the photo; NBA photos and logos; a client-side "admin password" |

**The combination:** Jarvis's shell, layout and visual language, filled with parlayhitters' real data and honesty rules, plus
JARVIS's real paper book (plays, closing-line value, kill switch, alerts) as new screens.

## 1. Decisions for Devin

The first one is settled; the rest change the build.

1. **One app** ✅ (Devin, "super app"). One codebase in `app/web`, one design system, one API.
2. ❓ **Public and private inside one app.** parlayhitters is meant to go public. JARVIS shows your bankroll, stakes, venues and
   kill switch, and the content spec bans stakes and sizing on public pages.
   **Recommendation:** add a "Desk" section (Signals, Paper book, Alerts) that exists only in a **private build**, served on
   127.0.0.1 or your LAN. The public build leaves those routes out at compile time, not just hidden behind a toggle, and the
   public API has no private endpoints. Same app, same look, one flag.
3. ❓ **Player photos and team logos.** The prototype embeds NBA headshots and logos, which we do not have the rights to use in
   public. **Recommendation:** use team-colour bars and initials everywhere (parlayhitters already does this). If you want
   photos in the private build only, you supply them locally.
4. ❓ **Brand.** **Recommendation:** "parlayhitters" is the app; "Jarvis" is the name of the private Desk (the assistant that
   watches games and alerts you). One accent colour, Jarvis purple. Orange is retired, except as the "tonight" highlight.
5. ❓ **Simple / Analyst.** **Recommendation:** keep both. They map cleanly onto what exists:
   - Simple is today's plain-language cards.
   - Analyst opens the Leg-page depth inline: line ladder, splits, calibration, the exact edge arithmetic.

   The mode is a per-device preference, not an account.
6. ❓ **Dropped from the prototype.** **Recommendation:** drop all five.
   - **Sign-in:** no accounts until phase 4.
   - **Admin studio:** a password checked in the browser is not security, and nothing needs administering.
   - **Hedge Finder:** this is sizing advice. Arbitrage candidates move into the private Signals screen instead.
   - **The NFL/MLB/NHL tabs:** there is no data behind them.
   - **The slogans.**

## 2. Screen map

Each row is one screen: where its data comes from, which existing code it reuses, and what changes.

| Screen (nav) | Public / private | Data | Reuses | What changes |
|---|---|---|---|---|
| **Today** (home) | public | `export.duckdb`, `parlay_of_day.json` | `Today.tsx`, `VerdictStrip`, `TopParlaysPanel` | Opens with the night's answer in the headline (see below). Games become a grid of compact cards. The top scorers collapse to one line each in Simple mode. |
| **Game** | public | export | `Game.tsx` | Jarvis "Around the court" header: score, tip time, win-probability bar. Player lines as a table. Analyst mode shows the full breakdown. |
| **Leg** | public | export | `Leg.tsx`, `CardDetail`, `AltLine` | Hero shows model %, the 80% range and the book price, with the edge arithmetic on one line. The line ladder and splits move under Analyst. |
| **Parlay** | public | `parlay_of_day.json` | `Parlay.tsx` | The A/B/C states from the content spec, unchanged in meaning, restyled as the hero card. |
| **Swipe / My picks** | public, local only | browser storage (paper) | `Swipe.tsx`, `Picks.tsx` | Restyle only. |
| **Track record** (was Model History) | public | `reports/forward_scorecard/*.json` via the export | new page | Model vs market, Brier gap with a clustered CI and n; a calibration chart from real forward scores; a running-total line. Closes with "keep your money" while that is true. |
| **Signals** | private (Desk) | `data/jarvis/paper_book.duckdb` (`plays`, `ingame_flags`), `halftime_overs.duckdb` | new | Tonight's JARVIS plays and halftime overs: edge after fees, label, the time JARVIS knew it, and whether the player is OUT. Overs-only view by default (your venue). |
| **Paper book** | private (Desk) | `plays` + `scores`, `killswitch_log`, `configs/jarvis.yaml` balance | new | Balance against the $70 start and the $20 floor. Ledger with **CLV per row** next to P&L. Mean CLV with n and CI. Kill-switch state at the top whenever it is on. |
| **Alerts** | private (Desk) | `sends` | new | A mirror of what JARVIS sent to Discord and iMessage, newest first. |
| **Settings** | both | browser storage | `Shell` theme toggle | Theme, Simple/Analyst, default screen. |

**The headline rule (every page):** the biggest text on a page states that page's answer, read from stored rows. It is never a
slogan. For Today, that means one of:

- *"11 games · 0 props clear an edge after fees · keep your money"*
- *"Model's best ticket: … (paper; record n = X)"*

The kill-switch and "model in review" states sit above everything else when they are on.

## 3. Design system

These are taken from the prototype and corrected.

- **Tokens:** the prototype's dark navy surfaces and purple accent, as CSS variables on `:root`, with a matching light theme.
  Green and red always carry a sign (+ / −) or a word, so colour is never the only signal.
- **Type:**
  - Manrope for display text and DM Sans for body text, both OFL and self-hosted (the app already self-hosts Barlow and Plex).
  - Tabular numerals for every number.
  - **Floor of 12px.** Secondary grey must reach at least 4.5:1 contrast on the background.
- **Layout:**
  - Phone first: top bar plus bottom tabs, with no horizontal scroll at 390px.
  - Desktop: the left sidebar from the prototype.
  - No text is ever placed over an image.
- **Components:**
  - Stat tile: value, label, n and CI, with a "not enough to say" state.
  - Verdict banner (A/B/C/kill switch).
  - Win-probability bar.
  - Edge line: model % [range] − (ask + fee) = edge, so the numbers on screen visibly add up.
  - Ledger table.
  - Calibration chart and running-total line, using the dataviz rules.
- **Honesty rules stay on every page** (APP_CONTENT_SPEC section 0): every number from a stored row, n and CI on every rate,
  the fixed footer and disclosure, paper only, and no stakes on public pages.

## 4. Data plumbing

- **Public:** unchanged. `nba.export` writes `export.duckdb`, `parlay_of_day.json` and `manifest.json`, and the app reads only
  those. The one addition is a table holding the forward scorecard, so Track record does not read `reports/` directly. It is
  additive and keeps schema version 2.
- **Private:** a new read-only writer, `nba/export/desk.py`. It opens `paper_book.duckdb` and `halftime_overs.duckdb` with
  `read_only=True` and retries, reads the balance from `configs/jarvis.yaml`, and writes `data/export_private/desk.json`
  atomically.
  - The API serves `/api/desk/*` only when started with `PH_PRIVATE=1`, and it refuses to start private on anything but
    127.0.0.1 or the LAN address.
  - Nothing on the live path changes: no edits to `nba/daily`, `ops/`, the JARVIS runner, or the configs.
  - At first it runs by hand or from the app's API on request. Scheduling it is your decision, made later.
- **The wall stays:** `app/` never imports `nba/`.

## 5. Phases

Each phase runs build → adversary → review → your go to merge, on its own branch.

| Phase | Owner | Branch | Contents | Done when |
|---|---|---|---|---|
| P1 Shell and design system | app | `agent/app-super-shell` | Tokens, fonts, sidebar and bottom tabs, Simple/Analyst toggle, Settings; Today, Game, Leg, Parlay, Swipe and Picks restyled; headline rule on Today | `npm run check` green; screenshots at 390 and 1440 for every screen, light and dark; a contrast audit script finds no text under 12px and none under 4.5:1; no horizontal overflow |
| P2 Track record | archivist (export) + app | `agent/archivist-export-scorecard`, then `agent/app-track-record` | Scorecard table in the export; Track record page | Every number traced to a scorecard row; the CI and n printed; replay oracle not needed (no live-path files) |
| P3 Jarvis Desk | market (`desk.py`) + app | `agent/market-desk-export`, then `agent/app-desk` | Signals, Paper book and Alerts in the private build | The public build contains no Desk code (checked with grep on `dist/`); private API bound locally; CLV and balance match `paper_book.duckdb` on a spot check of 10 rows |
| P4 Retire the prototype | main session | — | Move `Jarvis-Interactive.html` to `docs/design/` as a reference; the README describes the private build | — |
| P5 (optional) JARVIS messages | market | later | Discord and iMessage text uses the same card language (your 10-27 note: "helpful but not pretty") | Separate plan |

**Order and size:**

- P1 is the biggest piece and touches only `app/`, so it can start the moment you approve.
- P2 and P3 can run in parallel with each other once P1 lands.
- Rough effort: P1 a long session; P2 and P3 half a session each.

## 6. What would change this plan

- **Going public sooner:** if you want the public site up before P3, nothing changes, because the Desk is compile-time optional.
- **Desk not wanted in this app:** if you would rather keep JARVIS entirely in Discord, skip P3. The super app is then the public
  app in Jarvis's look.
- **Photos:** if you get rights to player photos, they slot into the Leg and Signals heroes without a layout change.
