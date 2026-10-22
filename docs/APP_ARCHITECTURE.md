# App architecture (design only, 2026-10-11; nothing is built, no accounts exist)

Source decisions: DECISIONS 2026-10-11 ~15:10 (parlayhitters.ai), `docs/BUSINESS_DRAFT_2026-10-11.md`, CLAUDE.md.
Companion: `docs/APP_SECURITY_BASELINE.md`. Status: DRAFT for Devin; every account or purchase is listed in the
security baseline checklist, section 12.

## 1. What the app is
A props assistance tool with paper trading, not a picks service. Users see the local props model's forecasts, track
paper (and, by photo, real) slips, and get an honest ROI and calibration page. The app shows the engine's honest answer,
including "no parlay today" and "keep your money". Read-only with respect to markets: no orders, no credentials, ever.

## 2. The wall between the app and the research repo
```
nba/ (pipeline, models, truth)  --never imports-->  app/
app/ (API, jobs, adapters)      --imports nba only via-->  nba/export/   (read-only)
nba/export/                     --reads-->  nba.duckdb opened read_only=True
```
- `app/` is a separate top-level package with its own `pyproject` extras, lockfile section and tests (`tests/app/`).
- `nba/export/` is small and pure: functions returning plain dicts/dataclasses for (a) forward predictions,
  (b) settled scores, (c) explainer data, (d) the props model's forecast grids (integer-support quantile and
  probability grids per player-stat, as stored by the daily run; no model is fit or loaded at serve time).
  It has no write path, no network, and imports no `app`.
- `tests/test_layering.py` gains two rules: `nba/` never imports `app.*`; `app/` imports `nba.*` only as `nba.export.*`.
  Same both-ways rule as KNOWN_EDGES: a new edge is a regression, a vanished edge means a stale list.
- The live path (`ops/*.sh` closure) must not import `app/`. The export job is a new entrypoint added to the closure
  with its edge listed. Hands-off window 10-18 to 10-20: nothing under `nba/`, `ops/`, `configs/`, `uv.lock` changes.
- The API never opens the live `nba.duckdb`. It reads a nightly-copied file (section 5).

## 3. Components
1. **PWA** (Cloudflare Pages). Static TypeScript build (Vite, no UI framework unless a page proves it needs one; Preact
   is the ceiling). Service worker caches the shell and the last-fetched forecast pages so it works offline and is
   installable. Talks only to the API origin. Pages: today's slate and forecasts, explainer per game, paper ledger,
   slip upload, my calibration, "parlay of the day", about/method/responsible-gambling, privacy, terms.
2. **API** (Oracle VM, Python, FastAPI; Starlette-only if the footprint matters). Two read sources: the nightly
   `export.duckdb` (opened `read_only=True`) and the app DB (SQLite in WAL mode, user data only, separate file from
   anything nba). Rate-limited (per IP and per account), CORS pinned to `https://parlayhitters.ai`, no wildcard.
3. **Slip ingestion.** Upload -> object store bucket with a 24-hour lifecycle TTL -> nightly bulk read by a vision LLM
   (API key from the VM secrets store, hard monthly cap, section 8) -> extracted fields written to the app DB ->
   image deleted -> deletion verified (object HEAD returns 404) and a line logged (slip id hash, time, result; no PII).
   If the cap is reached the batch stops and slips wait; any image reaching 24 h is deleted unread and the user is
   asked to re-upload. OCR is the fallback if it parses more reliably (DECISIONS wording); extraction output schema is
   identical either way. First 20 slips are reviewed by Devin (phase 1 gate).
4. **Paper-trading ledger** per user. Email magic-link accounts, no passwords. Rows: leg, line, price, book, stated
   time, source (typed / slip), stake (paper), settlement, and the model and market numbers at that minute (from the
   forward log, never the close).
5. **Settlement** from our box scores: after the morning job ingests final games, the nightly app job resolves legs
   against `export` settled scores. Unresolvable legs (name mismatch, voided, unknown market) are marked `needs_review`,
   never guessed.
6. **Parlay of the day.** Artifact produced by the existing shadow engine (`nba/parlay`) during the pretip job and
   exported as JSON by `nba/export`. Its deferral gate and the `no_positive_ev_found` verdict are first-class: the
   page renders the verdict string verbatim, with "no parlay today" as a normal state, not an error. App code does not
   recompute, reorder or soften it.
7. **Social adapters** (`app/social/`): X, Discord, TikTok, Instagram, each a thin adapter with one method,
   `publish(approved_item)`. Content agents write drafts into the approval queue (a table plus files under
   `app/queue/`). Devin clears items; the poster reads only approved items. A **kill switch file**
   (`data/ops/SOCIAL_KILL`, presence = stop) is checked before every publish and by the watchdog.
   - X: official API v2 supports posting with a paid tier; needs a developer app with write scope on Devin's account.
   - Discord: a bot or webhook in Devin's server; posting needs only a channel webhook (cheapest, least privilege).
   - TikTok: Content Posting API requires app registration and an audit before posts are public; unaudited clients
     post private-only. Needs: developer app, Content Posting product approval, user OAuth by Devin. FLAGGED.
   - Instagram: Graph API content publishing needs a Professional (Business/Creator) account linked to a Facebook
     Page, a Meta app, and App Review for `instagram_content_publish`; image/video must be at a public URL. FLAGGED.
   - Each platform's terms on automated posting are checked before the first post (DECISIONS reversal condition).
   Until TikTok/Instagram approvals exist, those two stay in "manual: agent drafts, Devin posts by hand".
8. **Customer service.** Discord support channel plus a support inbox. The scribe agent answers from a canned
   knowledge base (`app/support/kb/*.md`, reviewed by Devin). Escalation rule: anything about money, refunds, legal,
   privacy requests, account deletion disputes, harassment, self-harm or gambling-harm language, press, a security
   report, or any question the KB does not answer goes to Devin with the thread, unanswered. The agent never promises
   outcomes, never gives picks, and always includes responsible-gambling resources on harm language.

## 4. What runs where
| Where | What |
|---|---|
| Mac (launchd, Devin's) | `ops/nba_daily.sh` pretip/morning, watchdog, `nba.export` job, rsync push, approval queue UI, 1Password/Keychain |
| Oracle VM | API, app DB, nightly slip batch, settlement, social poster, support agent, backups |
| Cloudflare | Pages (PWA), DNS, TLS, WAF/Turnstile on signup and upload |
| Object store | Slip bucket (24 h lifecycle). Choice open: Cloudflare R2 or OCI Object Storage; see checklist |
| External APIs | Vision LLM (capped), email sender (magic links), X/Discord/TikTok/Instagram |

## 5. Data flow (text)
```
[pretip :20/:50] nba.daily run -> forward_predictions (nba.duckdb)  -> nba.parlay evaluate (shadow log)
[08:00 morning ] settle, report, ingest, backup                      (existing; unchanged)
[after morning ] nba.export build  : nba.duckdb (read_only) -> export.duckdb + parlay_of_day.json   (Mac)
                 rsync/scp over ssh to VM  (receive-only user, key restricted to one directory)
[VM, on arrival] verify checksum -> atomic rename into place; API re-opens read_only
User phone -> PWA -> API /upload -> bucket (TTL 24h) ; metadata row (no image path in logs)
[VM 03:00 CT   ] slip batch: bucket -> vision LLM (cap) -> schema validate -> app DB -> delete -> verify -> log
[VM 09:30 CT   ] settlement: legs x export settled scores -> ledger; calibration tables refreshed
[VM, continuous] social poster: approved queue -> adapters (kill switch checked every item)
```
Nightly schedule relative to `ops/nba_daily.sh`: the app never runs inside it and never holds `data/ops/lock`. The
export starts only after the morning job's end heartbeat (`daily-morning`, rc 0) shows; if the heartbeat is stale or
non-zero the export is skipped and the site shows "data as of <timestamp>". A second slip sweep at 15:00 CT catches
uploads after 03:00 so no image nears the 24 h TTL. Export is cheap (<1 min); it can never delay a pretip run since it
takes no write lock and reads read-only.

## 6. Failure behaviour
- VM down: PWA serves its cached shell and last forecasts, labelled stale; nothing on the Mac depends on the VM.
- Export fails: yesterday's file stays, banner shows its age; alert to `data/ops/ALERTS.md`; watchdog checks the new
  `app-export` heartbeat (start and end, per the "a job that cannot start cannot alert" rule).
- LLM cap hit or provider down: slips queue, user sees "processing"; no fallback that stores images longer.
- Posting error: item stays approved-unposted; no retries past two; kill switch wins over everything.

## 7. Cost table against the $30/month ceiling
Rates are placeholders to be confirmed against real invoices and then recorded in `configs/cost.yaml` (the only source
of rates; nothing here is an invented price to be relied on).
| Item | Estimate / month | Cap or note |
|---|---|---|
| Cloudflare Pages, DNS, Turnstile | $0 | free tier |
| Oracle VM | $0 | Always Free ARM shape if available; otherwise smallest paid shape, to be confirmed |
| Domain parlayhitters.ai | ~$5-8 (annualised; .ai is pricier than .com) | confirm at registrar |
| Object store (slips, 24 h) | <$1 | lifecycle rule; negligible volume |
| Vision LLM slips | ~$0.01/slip, <= $5 | hard cap in provider console and in code; batch stops at the cap |
| Email (magic links) | $0-1 | free tier of a transactional sender |
| X API posting | ~$0.015/post per the draft, ~30 posts = <$1; a paid access tier may be required and could dominate | confirm tier before first post |
| Discord, support inbox | $0 | |
| Support-agent / content-agent LLM | counted inside the Devin-session budget, not the app | confirm |
| Reserve | ~$10 | |
Target total: about $15-20; the ceiling is $30 and the build halts feature work, not safety work, if forecast spend
passes $25. The cost monitor reports month-to-date; a cleanup or plan change is a recommendation, never auto-run.

## 8. Build order (each step has a gate; Devin raises or cuts)
0. Now to 10-20: nothing under `nba/`, `ops/`. Only these two docs and the Devin checklist.
1. `nba/export/` + layering rules + fixture-DB tests (after 10-20). Replay oracle unaffected (read-only addition).
2. API read-only pages + PWA shell on fixtures. 3. Accounts + paper ledger. 4. Slip ingest (gate: 20 slips by hand).
5. Parlay-of-day page. 6. Social adapters behind the approval queue (gate: zero errors in 20 posts).
7. Support KB. 8. Payments only in phase 4, per the draft.

## 9. Gameplan to the PWA (2026-10-13; adds the features agreed today; weeks are estimates, each step keeps its gate)
Production moves to the Oracle VM first (DECISIONS VM item), so section 4's "Mac" column becomes the VM; the wall in
section 2 is unchanged. New features, all on one ledger stamped before tip:
- **Ledger schema (designed before any code):** one row per leg; `made_at` (refused after tip, like forecasts); line and
  price shown; source (swipe / typed / slip); `saw_model` flag (blind by default); user id; paper stake; settlement.
- **Over/under swipe:** cards drawn at random from the day's props (missingness by design), line and price only, no
  forecast; over / under / skip and response time logged. Feeds leagues and the crowd-signal test.
- **Leagues:** free, bragging rights only, scored on calibration against the price (not raw hits); no entry fees or prizes
  (paid contests are regulated; a change is Devin's call with legal advice).
- **Post-mortems:** per leg, forecast at the time vs outcome, the minutes/fouls/blowout facts behind a miss, bad bet vs bad
  luck from the stamped forecast and price; user tendencies only with n shown.
- **Grounded chat:** RAG over `nba/export`, the facts store and the ledger; numbers only from the engine (numguard);
  routed by difficulty (template, cheap model, strong model; "jev engineering"); "not enough data" is an answer.
- **Why library (later):** line moves paired with what was knowable before them; content first, an LLM-shadow arm only
  under a pre-registered rule.

| Week | Dates | Build | Gate / exit |
|---|---|---|---|
| 0 | now - 10-19 | Ledger + swipe schema (docs); VM created, stats.nba.com test; Devin's accounts (domain, Cloudflare, email sender) | network test result; hands-off 10-18 to 10-20 |
| 1 | 10-20 - 10-26 | Opening night on the Mac; VM runs the pipeline in parallel; `nba/export` + layering tests | replay oracle identical on the VM; export read-only |
| 2 | 10-27 - 11-02 | Cut over to the VM; API + PWA shell on fixtures; slate and forecast pages, installable | a few clean slates on the VM before cutover |
| 3 | 11-03 - 11-09 | Magic-link accounts, paper ledger, swipe cards, settlement | private alpha: Devin + 3-5 friends, consent noted |
| 4 | 11-10 - 11-16 | Post-mortems v1 (templates, no LLM), leagues, "my calibration" | settlement matches box scores on 100% of resolvable legs |
| 5 | 11-17 - 11-23 | Slip screenshot ingest; grounded chat v1 with routing | 20 slips checked by Devin; chat answers carry sources |
| 6+ | 11-24 on | Approval-queue posting (Discord, then X) of honest recaps; parlay-of-day page; support KB | 20-post dry run with zero errors; platform terms checked |
Public beta only after about four weeks of live log and the first PPLAY look (11-15). A business target is committed
before launch (e.g. weekly actives or paying users by 2027-01-15, with a rethink if missed), the same way a model rule is.
Devin supplies: the VM, the domain, a Cloudflare account, an email sender and a capped vision-LLM key (exact vendor URL and
plan each), the friend list, and later the Discord server and X developer app.

## Live pane and parlay tracker (added 2026-10-21)

Routes `#/live`, `#/live/<espn event id>`, `#/tracker`. Read-only, no accounts, no orders, no credentials.

Endpoints (app/api/src/parlayhitters_api/live.py, main.py), all GET, 8 s upstream timeout, 20 s cache, a copy up to
5 minutes old served marked `stale` when the upstream fails, 502 with a reason when there is none:

| Endpoint | Source | Notes |
|---|---|---|
| `/api/live/scoreboard` | ESPN unofficial feed | score, period, clock |
| `/api/live/game/{event_id}` | ESPN unofficial feed | box score; pre-game forecast means joined by normalised name and ET date from the export slate, else null |
| `/api/live/kalshi?tickers=A,B` | Kalshi public prices (no key) | yes/no bid and ask in dollars; at most 25 validated tickers |
| `/api/live/history?tickers=` | raw capture (read-only) + this API's own samples | gzip captures under `APP_LIVE_CAPTURE_DIR/kalshi/<SERIES>_p*/`; without the variable, history starts at the first sample (taken on each fresh `/api/live/kalshi` fetch) |
| `/api/live/model` | export slate | pre-game probability for a player or win leg; none for spreads and totals or when unmatched |

The browser never calls a third party. Hosts are allow-listed in `http_get`; the service worker never caches `/api/live/`.
The tracker keeps its legs in `localStorage` (per viewer, try/catch); nothing is sent to the server. Fees use the
documented approximation 7% x p x (1 - p) per contract, rounded up (the repo's fee config lives outside app/).
There is no in-game model: a live leg is priced at the Kalshi mid unless the viewer types a probability.

Licence: a public deployment of either feed (ESPN's unofficial API, Kalshi's public data) is Devin's decision; this
build is for local and LAN use. Fixtures: `app/fixtures/live/` (trimmed recordings, dates rewritten to 2024-11-12;
`app/fixtures/record_live.py` re-records them by hand).

Tracker views: exit signal (entry edge, model value now, offer, spread cost), value-over-time SVG chart, payoff by leg,
leg deltas, and a CSV import of the viewer's own Kalshi trade history that is parsed in the browser only (FileReader;
never sent to the API). To chart tonight's games from the raw capture run the API with
`APP_LIVE_CAPTURE_DIR=<repo>/data/raw/live_2026-10-21` (opened read-only).

### Import link and screenshot scan (added 2026-10-21)

**Import link.** `#/tracker?import=<base64url of UTF-8 JSON>` opens a "Check before adding" form. Nothing is added until the
viewer taps Add; a bad link shows an error and adds nothing. Max 20,000 characters. Schema (strict: unknown fields,
wrong types and out-of-range numbers are refused; the same rules live in `app/web/src/importLink.ts` and
`app/api/src/parlayhitters_api/scan.py`):

| Field | Rule |
|---|---|
| `name` | string 1-60, optional (default "Imported parlay") |
| `contracts` | number > 0, at most 1,000,000 |
| `paid` | number, total dollars paid, 0 to 1,000,000 |
| `combo_price` | optional, dollars per contract, 0 < x < 1 (22.4c is 0.224) |
| `max_payout`, `current_value` | optional informational numbers (screen values; shown, never used) |
| `legs` | list of 1 to 12 objects |
| leg `label` | string 1-120, the leg as shown |
| leg `kind` | `player`, `win`, `spread` or `total` |
| leg `player`, `stat` | player legs: name as ESPN spells it; stat is `pts`, `reb`, `ast` or `fg3m` |
| leg `threshold` | player: N in "N+" (0 < N <= 200); total: the line (> 0); spread: handicap (-100 to 100, negative = must win by more); not allowed on win |
| leg `side` | `over` (default) or `under`; player and total legs only |
| leg `team` | win and spread legs: 2-5 upper-case letters, e.g. `ATL` (Kalshi or ESPN abbreviation both work) |
| leg `ticker` | optional Kalshi ticker, `[A-Za-z0-9._-]`, up to 80 |
| leg `entry_price` | optional dollars per contract, 0 < x < 1 |

The game for each leg is found from the ticker's game code (e.g. `ATLORL`), else the team, else the live box scores
(player legs); unresolved legs stay editable in the form. Example link (two legs):
`#/tracker?import=eyJuYW1lIjoiVG9uaWdodCBsYWRkZXIiLCJjb250cmFjdHMiOjE0Ljk5LCJwYWlkIjozLjM2LCJjb21ib19wcmljZSI6MC4yMjQsImxlZ3MiOlt7ImxhYmVsIjoiSmFsZW4gSm9obnNvbiAxNSsgcG9pbnRzIiwia2luZCI6InBsYXllciIsInBsYXllciI6IkphbGVuIEpvaG5zb24iLCJzdGF0IjoicHRzIiwidGhyZXNob2xkIjoxNSwidGlja2VyIjoiS1hOQkFQVFMtMjZPQ1QyMUFUTE9STC1BVExKSk9ITlNPTjEtMTUiLCJlbnRyeV9wcmljZSI6MC43M30seyJsYWJlbCI6Ik9ybGFuZG8gd2lucyIsImtpbmQiOiJ3aW4iLCJ0ZWFtIjoiT1JMIn1dfQ`

**Screenshot scan.** `POST /api/scan/kalshi-screenshot`, request body = the raw image (PNG, JPEG, WebP or GIF, max 5 MB;
content type and magic bytes must agree; 413 / 415 otherwise). The image is kept in memory for the request, never
written to disk, never logged. Only when `ANTHROPIC_API_KEY` is set in the environment (read per request, never stored
or echoed) it is sent, with a fixed extraction prompt and nothing else, to Anthropic (model `claude-sonnet-5-5`);
without the key the endpoint answers 503 "screenshot scan needs ANTHROPIC_API_KEY; or paste the screenshot to Claude and
use an import link". The reply must be JSON that validates against the schema above or it is refused (422). Tickers for
player and win legs are then looked up in Kalshi's public open markets (today's and yesterday's Eastern-date events;
player title and threshold must match exactly one market); spreads and totals are left blank. The result is returned as
`{parlay, screen, resolved, source}` and shown as an editable preview. Privacy note in the UI: the image goes to Anthropic
for reading when this is used. Fixtures: `app/fixtures/live/kalshi_series_*.json`.
