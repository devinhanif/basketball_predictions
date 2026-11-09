# Design: pull orchestrator

_2026-11-08. Devin: "i need a pull like orchestrator, do orchestration." Label: Devin's request. It follows the 11-08
finding that JARVIS alerted against a 238-min-old Pinnacle price because nothing pulls between the 10:00 morning snapshot
and T-180._

## Problem
Every source is pulled on its own clock:

| Job | Clock | Source |
|---|---|---|
| lineups | 5 min | odds-API schedule |
| kalshi-snapshot | 15 min | Kalshi pre-game prices |
| live-capture | in game | ESPN, Kalshi ladders |

JARVIS reads all of them every 2 min, but cannot ask for fresh data. So an alert's freshness depends on when it happens to
fire, not on what it needs.

## Shape
One component, `nba/pulls/`, owns **when** to pull. The existing fetchers keep owning **how** to pull and stay unchanged.

1. **Freshness rules** (`configs/pulls.yaml`): for each source and use, the oldest data allowed.
   - Pinnacle reference for an alert: ≤ 30 min.
   - Kalshi quote for an alert: ≤ 5 min (JARVIS's existing rule).
   - Odds closing line: the existing close slot.
   - Each source carries a cost (credits per call) and a priority: close > alert-driven > scheduled.
2. **Requests.** A consumer never fetches. It writes a request row to `data/ops/pulls.duckdb`, table `pull_requests`:
   `(source, scope e.g. game_id, need_by, max_age_min, requester, reason, created_at)`.
   - JARVIS is the first consumer. When a candidate's Pinnacle reference is older than the rule allows, JARVIS writes a
     request and **holds the alert** instead of sending it on a stale price.
   - The next JARVIS tick (2 min) sees the fresh row and decides again.
3. **The orchestrator tick** runs every 2 min from its own launchd job (`local.nba.pulls`), with its own lock and
   heartbeat. Each tick:
   1. reads open requests and the fixed schedule;
   2. dedupes by (source, scope);
   3. checks the credit ledger: daily cap, monthly plan rule, and the server floor;
   4. calls the existing fetcher (`nba.odds.live_close` per event, the Kalshi snapshot);
   5. marks each request `done | refused_budget | refused_post_tip | failed`, with the reason.

   It never calls a source after tip for a pre-game use (the known-at rule stays). It never holds `heavy.lock` and
   never writes `nba.duckdb`.
4. **Freshness status.** A `freshness` view gives the newest row per (source, game) and its age. Three readers:
   - the watchdog, which alerts if a game within 3 h of tip has a Pinnacle reference older than 60 min;
   - the morning briefing;
   - the app's "Ready to connect" panel.

## Phases
- **P1 (now):**
  - Requests table and orchestrator tick, Pinnacle (odds-API per-event) only.
  - JARVIS writes a request and holds alerts whose Pinnacle reference is older than 30 min.
  - The freshness view and the watchdog check.
  - Built on top of `agent/steward-odds-hourly`, whose hourly slots remain the floor.
- **P2:** move the Kalshi snapshot and the fixed odds schedule under the same tick: one ledger, one heartbeat, retire the
  separate clocks. Replay oracle plus a 3-night shadow comparison of coverage first.
- **P3:** in-game sharp references for the halftime overs, if the vendor's in-play Pinnacle feed covers player props. To
  check before building, because halftime is when Devin enters.

## Rules kept
- Read-only toward markets: GET only, no orders, no credentials in the repo, and the key only from `THE_ODDS_API_KEY`.
- Known-at stamping on every row (`requested_at` / `received_at`); post-tip pulls are refused for pre-game use.
- The credit ledger is the existing `the_odds_api_live_<date>` files. The orchestrator adds a request-id field so every
  credit spent is traceable to its requester.
- **Merging is Devin's go,** after review, the replay oracle and a dry run on a copy. JARVIS holding stale alerts is a
  behaviour change; this request is the go for it (DECISIONS row).

## What would reverse it
- Pulls on demand fail or lag past the 2-min tick often enough that alerts are missed. Measured over 3 nights:
  held → sent latency, and refused requests.
- The vendor's per-call cost makes on-demand pulls exceed the plan rule.
