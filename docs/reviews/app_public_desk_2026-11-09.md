# Adversary review: public /api/desk serves the shared cut (agent/app-friendly-empty-states, 58d5b99)

**Outcome (main session, 2026-11-09):** M1, M2, m1, m2, m3 and m5 fixed in d6ab397 (346 API tests incl. 17 plant tests); merged to main 37701f7. m4/m6 open (producer side); m7 addressed operationally (Vite now allows only the one tunnel hostname). Private money values in this file were redacted before commit.

Date: 2026-11-09. Reviewer: adversary. Scope: `git diff main...58d5b99` (app/ only, 25 files). Context: the app dev
server is shared through a Cloudflare quick tunnel (`cloudflared tunnel --url http://localhost:5173`); Vite on 5173
proxies `/api` to the API on 127.0.0.1:8000, which runs PUBLIC (no `PH_PRIVATE`).

**Adversary verdict: WOUNDED.** Nothing leaks today. The real `desk_shared.json` carries no money value, and
`desk.json` cannot be reached from a public request. The claim "guarded by `money_fields()`" is narrower than it
sounds, though. The guard is a denylist of six known keys plus a `$<digit>` regex, so 16 of the 18 planted money
or identity fields below were served with status 200. Everything stays private only while the producer never adds a
new key, which is a promise and not something the API checks.

Verdict: APPROVED

(The merge is approved because no current data leaks. M1 and M2 should land before the desk producer's schema next
changes, and before `PH_PRIVATE=1` is ever set on a machine that runs the tunnel.)

Top finding: `money_fields()` (desk.py, the `def money_fields` block) is a denylist. A `plays[].stake_units`,
`status.bankroll_usd`, `daily.pnl_cents` or a "12.34 USD" string passes and is served to anyone with the link.

## Playbook check

Model class: none. This is app serving code, not a model, so the MODEL_PLAYBOOK class checks do not apply.
Cross-cutting questions: information or capacity: n/a. Enough data: n/a. Test validity: the tests are valid for the
paths they cover, and the gaps are listed under m3. Lineage and inherited guards: this inherits the producer's
`shared_cut()` (nba/export/desk.py) and the contract docs/contracts/desk_export_v1.md, which fixes the schema, so a
key allowlist can be derived from it. Scored against: n/a (no metric claim, so the six statistical checks are n/a;
the adversarial analogues run here are planted fields, misconfiguration and the error paths).

## Attack table

All runs were CPU-local and read-only against the real files. The branch was extracted with `git archive 58d5b99
app/api app/fixtures docs/contracts` into scratch and run in-process with FastAPI's TestClient. No running server
(5173/5174/8000/8010) or cloudflared was touched. A `curl` probe of 5173 was denied by the permission system, so the
Vite checks are static.

| # | Attack | Command / method | Result | Pass |
|---|---|---|---|---|
| A1 | Real shared file through the branch route | TestClient GET /api/desk, `APP_DESK_SHARED_FILE=data/export_private/desk_shared.json`, PH_PRIVATE unset | 200, privacy=shared, balance=None, 137 plays, `Cache-Control: no-cache` | pass |
| A2 | Point the shared env var at desk.json | `APP_DESK_SHARED_FILE=.../desk.json` | 422 "the public desk file must be the shared cut" (no values echoed) | pass |
| A3 | Producer bug: desk.json relabelled `privacy: shared` | planted file | 422, lists field names only (balance, daily.pnl_usd, plays.contracts, plays.fee_cents, plays.result.pnl_usd, plays.stake_usd) | pass |
| A4 | Money under keys the denylist does not know | 8 plants: `status.bankroll_usd`, `status.balance{current_usd,floor_usd}`, `plays.stake_units`, `plays.kelly_fraction`, `plays.result.pnl_cents`, `plays.contracts_filled`, `daily.pnl_cents`, `record.net_usd` | **8/8 served 200** | **FAIL (M1)** |
| A5 | Money in free text without `$<digit>` | "12.34 USD", "58 contracts", "USD 2.75", "$.12" | **4/4 served 200**. Caught: "$ 2.75" and "per $1 ... $2"; control "$0.12" also caught | **FAIL (M1)** |
| A6 | Identity and paths in text | email, `~/Downloads/...`, `data/export_private/desk.json`, `file:///Users/...` | **4/4 served 200** (the `_PATH` regex needs `/Users/` after whitespace, `(`, a quote or `=`) | FAIL (m5) |
| A7 | Error body echo | file chmod 000 | 422 `unreadable: [Errno 13] Permission denied: '<absolute path>'`, so the absolute path is in a public body and the web DeskGate prints `h.detail` | FAIL (m1) |
| A8 | Error body echo, bad JSON | truncated JSON holding a token-like string | 422 `Expecting ',' delimiter: line 1 column 38`, no content echoed | pass |
| A9 | Env precedence | read main.py `desk()` | `PH_PRIVATE == "1"` exactly selects desk.json. Nothing else (Host, forwarding headers) is checked | risk (M2) |
| A10 | Live topology | `ps eww` (env names only), `lsof` | tunnel to 5173 (main checkout Vite, `--host 0.0.0.0`, `API_ORIGIN` unset, so 8000); 8000 has no PH_PRIVATE. 5174 goes to 8010 (rehearsal, not tunneled) | pass today |
| A11 | Vite file serving over the tunnel | static: `searchForWorkspaceRoot` in node_modules/vite 8.3.4; looked for package.json/pnpm-workspace/lerna/deno.json from app/ up to `/` | none found, so `fs.allow` = app/web and `/@fs/.../data/export_private/*` is outside it. No private desk under app/web (grep for start_usd/current_usd/privacy private: exit 1) | pass (not exercised live) |
| A12 | Service worker | read public/sw.js | `/api/*` network-first, cached per browser origin. A stranger's cache only holds what the public API gave them. A 422 is <500, so no stale fallback | pass |
| A13 | Admin and other routes | `git grep useDesk/deskOf` | Admin is gated on `privacy === "private"` (shellData.ts:34), so the shared cut does not unlock it. No other route reads the desk. bake_artifact defaults to the shared cut and `--money` is explicit | pass |
| A14 | Fee in `plays[].reason` re-derives nulled fields | compared to desk.json; contract-count inference using the Kalshi fee formula | reason fee == private fee_cents for 92/92. But it is the 1-contract quote: unique contract recovery 2/70, true count outside the candidate set 22/70. Stake and PnL cannot be recovered without contracts | pass (m6 cosmetic) |
| A15 | daily_dollar edge re-derived | shared `fair_prob` + bundle leg `price_cents` | fair and ask are visible, fee is not, so edge (fair-ask-fee) and contracts are not recovered | pass |
| A16 | Branch API tests | `cd <scratch>/br/app/api && PYTHONPATH=src .venv/bin/python -m pytest -q` (unpiped, redirected) | 322 passed, 1 skipped, exit 0 | pass |

### Real shared file vs desk.json, key by key

Leaf paths are generalised over list indices. Only in private: `balance.start_usd`, `balance.current_usd`,
`balance.floor_usd` (values redacted); shared has `balance: null`. Values that differ: `privacy`; `plays[].fee_cents`
(137, value to null); `plays[].contracts` (137, value to null); `plays[].stake_usd` (137, value to null);
`plays[].result.pnl_usd` (130); `plays[].edge_cents` (32, daily_dollar only); `plays[].reason` (32, daily_dollar
only, e.g. "product of asks 0.70c, assumed fair 0.82c" to null); `daily[].pnl_usd` (12); `section_notes.shared_cut`.
Everything else is identical, including `status.model_review` (true in both, kill switch on).

What a stranger sees that `money_fields()` does not look at (no secrets, emails, tokens or absolute paths found; a
keyword grep of every string for stake/contract/bankroll/balance/unit/kelly/usd/pnl/account/token/key/http/discord/
`@`/Devin matched only player names, team matchups and section notes):
- `record`: `{n_settled: 128, n_won: 36, n_lost: 92, n_void: 2}`, `clv_summary.mean_cents` -0.88 (CI -1.43 to -0.52,
  n=83, 12 dates), and `track_record.model_vs_market.diff` +0.0095 Brier (CI 0.0064 to 0.0125, n=6812, 133 games).
- `plays[]` (137): `ts` (send time, e.g. 2026-10-25T23:43:11Z), `venue` (kalshi 134, polymarket 3), `side`,
  `price_cents`, `edge_label` (including `daily_dollar`, a strategy name that hints at a fixed small daily budget),
  `result.won`, `clv_cents`, `delivered: sent`.
- `status.kill_switch` (on since 2026-11-03, mean CLV -1.28c, n=30).
- `sources`: relative internal paths and row counts, e.g. "data/jarvis/paper_book.duckdb: plays 137 rows, newest
  2026-11-08T18:59:38Z" and "configs/jarvis.yaml (balance block only)".
- `alerts[]` (476) and `observations[]` (523): send timestamps and titles such as "Halftime check-in sent for
  MEM@SAC".

This content was already published on the shared claude.ai page at Devin's "ok update" (DECISIONS 2026-11-08 19:40),
so serving it publicly is consistent with a decision already taken. It is flagged for Devin only because the tunnel's
audience ("anyone with the link") may differ from that page's audience.

## Findings (severity-ranked)

**M1 (major): the public guard is a denylist.** Location: app/api/src/parlayhitters_api/desk.py, `money_fields()`
(the explicit keys `balance`, `plays.{stake_usd,contracts,fee_cents}`, `plays.result.pnl_usd`, `daily.pnl_usd`, plus
`_DOLLAR = r"\$\s?\d"`). Evidence: A4 and A5, 12 of the 14 planted money fields under other keys or wordings were served
with status 200. The producer's `shared_cut()` (nba/export/desk.py) is also a denylist, so a new money key added
upstream would pass both. Fix: in `load_shared_desk`, validate against a key allowlist derived from
docs/contracts/desk_export_v1.md. Refuse any key not in the contract, at every level (top level, `status`, `plays[]`,
`plays[].result`, `daily[]`, `record`, `clv_summary`, `track_record`, `alerts[]`, `observations[]`, `halftime[]`).
Also refuse any non-null value whose key matches `(?i)usd|stake|contract|bankroll|balance|pnl|units|kelly|fee`, except
the documented `fee`-free keys. Add a test for each A4 plant that expects 422.

**M2 (major, pre-existing but now on a live public route): one env var flips the tunneled API to private.**
Location: main.py `desk()`, `return load_desk() if os.environ.get("PH_PRIVATE") == "1" else load_shared_desk()`.
Evidence: A9 and A10. Vite proxies with `changeOrigin: false`, so the API sees the tunnel's `Host:
*.trycloudflare.com` and Cloudflare's `cf-connecting-ip`. If PH_PRIVATE=1 is ever set on the 8000 instance, or a
private API is pointed to through `API_ORIGIN` on the tunneled Vite, then balance, stakes and PnL go to anyone with
the link. Fix: in private mode, serve desk.json only when the request's Host is `localhost`, `127.0.0.1` or `[::1]`
(any port) and no `cf-connecting-ip`, `cf-ray` or `x-forwarded-for` header is present. Otherwise fall back to
`load_shared_desk()`. Test: PH_PRIVATE=1 with `Host: x.trycloudflare.com` must return privacy=shared.

**m1 (minor): the 422 body echoes an absolute path.** Location: desk.py `load_desk`, `raise
DeskRefused(f"unreadable: {e}") from None`, surfaced by main.py `raise HTTPException(422, f"desk export refused:
{e}")` and shown to strangers by DeskUI.tsx DeskGate (`Reason: ${h.detail}`). Evidence: A7 (absolute path to the file,
including the username, on an OSError). Fix: in the public branch, map OSError to the fixed text "unreadable" and keep
`e` for the server log. Key names in money refusals are fine.

**m2 (minor): the swipe note for 11-09 blames capture when the cause is a stale export.** Location: main.py
`_swipe_empty_note`, the no_lines branch ("no player lines have been captured yet"). Evidence: at 2026-11-10T02:56Z,
`data/export/latest` (made_at 15:20Z, the one prop snapshot at 15:00:30Z) holds lines for 1 of 8 games
(0022600208, tip 01:30Z, started) and 0 rows for 0022600210 (tip 03:00Z, the game still to play). Read with duckdb
`read_only=True` on export.duckdb. The `no_lines` reason is correct for this data, but "captured yet" asserts
something about the capture pipeline that the API cannot know: it only knows the export is about 11.5 h old. Fix: say
"this data (built <data_as_of ET>) holds no player lines for the N games still to play". When `store.has_lines` is
false (schema 1), say "this data carries no player lines" rather than "No player lines were captured for any of
them". The ordering (no_games, then all_started, then no_lines) is right.

**m3 (minor): test gaps.** `test_desk_route_is_private_only` (test_desk.py) no longer tests that, because public
now returns 404 only because the shared file is missing. Rename it. Add (a) a test that the real producer output,
`shared_cut()` of a realistic doc, passes `load_shared_desk` with status 200; (b) the M1 plant tests; (c) the M2 Host
test; (d) a test that an unreadable file's 422 has no `/` in its detail.

**m4 (minor, policy): internal layout in public `sources` and `section_notes`** ("data/jarvis/paper_book.duckdb",
"configs/jarvis.yaml (balance block only)", row counts). No secret here. Optional fix: the producer drops `sources`
from the shared cut or reduces it to a label.

**m5 (minor): the path regex misses `~/`, `file://` and relative `data/...` paths** (A6). Fix: widen `_PATH` to
`(~/|file://|(^|[^\w])/(Users|home|private|var|tmp|etc)/)`. The M1 allowlist makes this less critical.

**m6 (cosmetic): `plays[].reason` restates `fee_cents`** ("ask 55.0c ... + fee 2.0c"; equal for 92/92) while
`section_notes.shared_cut` says fee_cents is null. A14 shows it is the 1-contract quote and does not reveal contract
count, so no privacy fix is needed. Optionally reword the note.

**m7 (minor, out of diff scope): Vite exposure.** `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=.trycloudflare.com`
admits every trycloudflare subdomain, and Vite listens on 0.0.0.0 (LAN as well as the tunnel). `fs.allow` resolves
to app/web, so data/ is outside it, but a dev server is not meant to face the internet. Fix when convenient: tunnel
to `vite preview` of a built dist (no `/@fs`, no HMR), or set `allowedHosts` to the one tunnel hostname.

Scope creep: none material. The wording rewrites across 14 pages match the task. ErrorPanel now hides the technical
detail outside DEV, which is an improvement.

## Multiplicity and power

No statistical claim is made, so there is no family and no MDE. The quoted desk numbers (record, CLV CI, Brier diff)
are the producer's, not this branch's.

## Single biggest risk

A future producer field carrying money under a new name (or PH_PRIVATE=1 on the tunneled API) is served to anyone
with the link, and no test fails.

## Not verified

- Live requests through the tunnel or Vite (`curl` denied). The Vite `/@fs` and host behaviour is from reading the
  code only.
- Whether lines for 0022600210 exist in a capture store newer than the export (the capture table was not found in
  nba.duckdb).
- `npm run check` on the branch was not run, because no finding depended on it.
- Cloudflare edge caching of `/api/desk` (the API sends `Cache-Control: no-cache`, and CF does not cache JSON by
  default).

## Recommended changes (for the main session; not applied)

1. M1 allowlist plus plant tests (app/api desk.py and tests/test_desk.py). Also consider the same allowlist in
   nba/export/desk.py `shared_cut()` (market agent's area).
2. M2 Host/forwarding guard on the private branch of `desk()`.
3. m1 generic unreadable message; m2 note wording; m3 test rename and additions.

## Drafted rows (not appended)

BEST_PRACTICES candidate: "A privacy cut is validated by an allowlist of contract keys, not a denylist of known money
keys. Incident: app_public_desk_2026-11-09, 12 of 14 planted money fields under new names or wordings passed `money_fields()`."
