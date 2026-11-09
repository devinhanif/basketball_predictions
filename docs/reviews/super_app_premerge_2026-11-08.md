# Super app shell (`agent/app-super-shell` @ 3d16a80), pre-merge adversary review (2026-11-08)

Scope: `git diff origin/main...3d16a80` (93 files; outside `app/` only `docs/DECISIONS.md` and
`docs/contracts/desk_export_v1.md`, both identical to main). Built page:
`.claude/worktrees/agent-a34ecf4111ce8dc07/app/web/dist-artifact/parlayhitters.html` (6,211,964 bytes). Inline JSON
(`script#ph-baked`, 5.83 MB, 26 gzip shards = 81.1 MB decompressed) and JS (236 KB) extracted and scanned in scratch.
Model class: none (a static UI over stored exports). No DuckDB opened, no season 2025, no code modified.

**Overall: WOUNDED.** No money is present or derivable in the baked page, and the edge arithmetic reproduces 83/83.
Three things need fixing before the link goes out: the page carries Devin's absolute file path; the Discover hero
shows a stale-line +11.4c "edge" with copy that is false for desk rows; and the tests would not catch a returning
money field.

Verdict: BLOCKED

## Top finding (major): the Discover hero for 11-08 is a stale-line number with wrong copy

The default night (2026-11-08) puts the desk's best play in "The closest look": Austin Reaves, fewer than 6 assists,
fair 51.4%, ask 38c, **+11.4c edge after fees**, shown in green. The stored `reason` says the fair price is
**238 min old** and the ask 6 min old ("Pinnacle no-vig 51.4c (o5.5, 238 min old)"; the explanation also mentions Dončić
OUT). The page never renders `reason` or `explanation` (grep of `pages/`, `DiscoverParts.tsx`), so the age is lost.
Then `DiscoverParts.tsx:102` prints "Unproven: the model has no track record yet." for every non-negative row.
For a desk row that is false on two counts: the model is not an input (w_model 0), and JARVIS does have a record
(36-92, CLV -0.88c, kill switch on since 11-03). The hero also has no "keep your money" line.
**Fix:** in `Hero`, for `row.source === "desk"`, render the stored `reason` verbatim (or the fair-price age parsed from
it) under `edgeSentence`. Replace line 102's desk branch with "JARVIS's kill switch is {on/off}: its own sample
n = {ks.n}, mean CLV {ks.mean}. Paper only, keep your money." Add a test that a desk row never gets the "model has
no track record" text.

## Check table

| # | check | command / evidence | n | result | pass |
|---|---|---|---|---|---|
| 1a | named money fields in baked | `scan.py` key walk over the baked JSON | 137 plays, 130 results, 12 daily | `contracts`, `stake_usd`, `fee_cents`, `result.pnl_usd`, `daily.pnl_usd`, `balance`: all null | pass |
| 1b | baked desk = shared cut; `--money` not used | `cmp` of sorted JSON, baked `/api/desk` vs `data/export_private/desk_shared.json` | 1 | identical (exit 0); `privacy="shared"`, `fixture=false`, source file `desk_shared.json` | pass |
| 1c | fee/edge -> n attack on the baked copy | implied fee = fair*100 - price - edge | 83 plays with edge | implied fee 2.0c x81, 1.0c x2, residual < 4e-15: the one-contract fee, carries no n. daily_dollar: edge/fee null (32 legs) | pass |
| 1d | shards | gunzip all 26 shards, walk keys and strings | 1,681 engine contracts | only `fees` = 1c/2c per contract (public engine field); 0 hits for `/Users`, email, `$\d`, env/token, "staked"/"bankroll" | pass |
| 1e | file paths | regex `/Users\|/private/\|/home/` over the baked JSON | 1 hit | **`desk.source = "/Users/devin/Downloads/nba-prediction/data/export_private/desk_shared.json"`**, written by `bake_artifact.py:248`. Not rendered, but it is in the page source the friend receives | **FAIL (major)** |
| 1f | JS | grep for `/Users`, emails, `process.env`, `import.meta.env`, `sk-`, API key | - | 0 | pass |
| 2a | desk edge = fair - ask - fee | `edge.py`; `reason` text parsed and compared | 83 | 0 mismatches (fair to 0.05c, edge to 0.05c, fee to 0.06c); `discover.ts:84-91` desk rows use fair, never model | pass |
| 2b | engine edge = model - ask - fee | same script over `/api/contracts/top` and all `/api/edges` | 12 + 1,681 | 0 mismatches. For the engine the model *is* the input, which `discover.ts:93` / `edges.ts:51` say | pass |
| 2c | model % not shown as desk edge input | `DiscoverParts.tsx:94-97` "Separately, our model says ... model weight 0" | - | ok | pass |
| 2d | CLV tile n + CI | `History.tsx:30-36` reads `clv_summary` | n=83, 12 dates | -0.88c [-1.43, -0.52], clustered on slate date (stored) | pass |
| 2e | two CLV populations | `History.tsx:37`, `:78-87`; `alerts.ts:89` | 83 vs 30 | labelled "a different population" in both places | pass |
| 2f | review-derived line | `History.tsx:38-41` vs `desk_export_premerge_2026-11-08.md` | 83 | 0.69c (review 0.687), -0.19c [-0.74, +0.15], n=83, date clusters: exact. But it is a literal string: it goes stale on the next rebuild while the tile's n moves | pass (minor) |
| 2g | record win rate CI | `desk.ts:268-275` Wilson (row-level, computed client-side) | 128 decided, 12 dates | Wilson [21.1%, 36.5%]; my date-clustered bootstrap (seed 20261108, 5000 reps) [20.7%, 35.3%]. Spec 0.2 wants a clustered interval. The 128 mix 32 daily_dollar legs (0 won, 11 bundles, perfectly correlated within a bundle) with singles | minor |
| 2h | model vs market | `History.tsx:64-76` | 6,812 rows / 133 games | +0.0095 [0.0064, 0.0125], clustered on game, sign explained | pass |
| 2i | headline vs radar sources | `headline.ts:57-62` "Source: the parlay engine"; `DiscoverParts.tsx:203-205` "JARVIS's price check ... a different check from the headline above" | 2,956 / 7 plays | both carry sources. The hero (finding above) does not distinguish enough | pass |
| 2j | Signals "Props to keep in view" | `Signals.tsx:63-68`, `:138` | 1,764 halftime rows | sorted by stored `edge_cents` (= model - ask - fee, residual 1c/2c), but it shows model % next to ask with neither fee nor edge | minor |
| 3a | payoff direction | `edge_cases.ts` (node, scratch) on `hedge.ts` | 14 cases | NO 6 @38+2: +60 on 0-5, -40 on 6+; YES 6 @62+2: -64 / +36; team crossing correct | pass |
| 3b | complements and gaps | same | 3 candidates | exact complement, "4 or more" covers every count, "fewer than 8" leaves a gap: all labelled correctly | pass |
| 3c | cost = contracts x (ask + fee) | `legCost` 3 x (38+2) = 120 | - | ok | pass |
| 3d | edge cases | 0 contracts / ask 0 / ask -5 -> leg excluded; ask 100 -> 99; 2.6 contracts -> 3; 1e9 -> 1000 | - | sane | pass |
| 3e | empty or non-numeric fee | `Number("")`, `Number("abc")` -> `clampLeg` 0 (`hedge.ts:41`); shared cut has no fee, so `Hedge.tsx:54` falls back to the export row, else 0 | - | cost drops 40c -> 38c **silently**; the field shows a blank "-" | minor |
| 3f | sizing advice | grep for size, stake, kelly, units | - | none; `NOTE` says "no size is suggested" (`Hedge.tsx:17-18`) | pass |
| 4a | db paths vs rules | README.md:265 rules; writes `picks/<self>` (`sharedPicks.ts:269`), `watch/<self>` (`watch.ts:137`), `data/users/<id>/state` (`userState.ts:95`), `photos/<pid>` (`photos.ts:121`); owner delete `picks/<uid>` (`Admin.tsx:167`) | - | all even-segment doc paths; prefix rules set both read and write; owner delete allowed by `picks` write owner | pass |
| 4b | other viewers' data | `cleanShared` / `cleanDoc` (caps 80/60/30/10/6 chars, 400 picks, version), `cleanWatchDoc`, ids `ID_RE`, photo ids `ASSET_RE` | - | validated and capped | pass |
| 4c | subscriptions | `main.artifact.tsx:17-31`: picks, watch, photos, own state, each guarded by `started` | 4 | none in render | pass |
| 4d | null capability | `platform.ts:54-62`, `startShared` -> "off" (test `artifact.test.ts:158`) | - | page works locally | pass |
| 4e | admin gating | `identity.ts:61-63` isOwner/canEdit from `user.me()`; clear button only on `isOwner` (`Admin.tsx:196`); no client secret | - | ok (UX gate; data is in the page anyway, so gating is not secrecy) | pass |
| 4f | assets / downloads | upload only inside Admin; urls built `/_blob/<id>` and used only as `<img src>` (`Avatar.tsx:10`); `downloads.save` only from the "Export ledger" click (`History.tsx:298`) | - | ok | pass |
| 4g | owner clear is durable | `sharedPicks.ts:259-280`: after `mergedOnce` the viewer's next ledger change rewrites the whole local ledger | - | the clear is undone at the friend's next pick; `watch/<uid>` is never cleared | minor |
| 4h | root rule | README rules leave the root at default write `interact` | - | any Contributor can create documents at arbitrary paths (quota) | minor |
| 5 | frame rules | `extract.py` + grep on the built page | 6.21 MB | no doctype/html/head/body; no fetch/XHR/WebSocket/serviceWorker/alert/confirm/prompt/print/`a.download` (tree-shaken); the only `http` strings are escaped SVG/MathML namespaces; `[data-theme=dark]` and `[data-theme=light]` both present; < 16 MB | pass |
| 5r | route names | `router.ts:24`, `:40`; `artifact.test.ts:74` | - | top-level plain names exist (`#prefs`, `#swipe`), but `topHref` and deep links use `#/...` with query strings. The reserved-name list is not in the 0.2.96 `.d.ts`; **not verified** | unknown |
| 6 | content spec | grep in JS | - | footer and 1-800-GAMBLER x2, disclosure, "keep your money" x8; no lock/guarantee/free money/can't lose/hammer/units. "Easy to explore" (Welcome tagline) is about the UI, not a bet. **"claude.ai" is shown to users** (`Shell.tsx:207`, `:371`, Admin copy) against spec 0.7 "No AI-vendor name anywhere" | minor |
| 7a | test: reintroduced money field | `mut.py` against `check_desk` (`desk.py:35-49`) with the shared fixture | 4 mutants | `contracts=58`, `fee_cents=4/47`, `reason="staked $12.40"`, absolute `sources`: **4/4 accepted**. No test scans `baked.json` | **FAIL (major)** |
| 7b | test: wrong payoff direction | `hedge.test.ts:85`, `:95` | - | would fail | pass |
| 7c | test: db write outside own doc | only `picks` is asserted (`artifact.test.ts:169-213`, id `u_me`). No test covers `watch/`, `data/users/`, `photos/`, or Admin delete paths | - | not caught for 4 of 5 write paths | **FAIL (major)** |
| V | verification (worktree, unpiped) | `uv run --frozen pytest` (app/api); `npx vitest run`; `npx tsc --noEmit`; `npx eslint .` | - | 311 passed 1 skipped exit 0; 196 passed exit 0; exit 0; exit 0 | pass |

## Recommended changes (not applied)

1. **Major, `app/api/src/parlayhitters_api/bake_artifact.py:248`:** `desk["source"] = desk_file.name if desk_file else None`.
   In `app/web/scripts/build-artifact.mjs` forbidden list, add `[/\/Users\/|\/home\/|\/private\/var|\/private\/tmp/, "an absolute path"]`.
2. **Major, `app/web/src/pages/DiscoverParts.tsx:94-103`:** the desk-row hero fix above (stored `reason`, kill-switch
   line, "keep your money"). Optionally keep a desk play whose fair-price age is over 60 min out of the hero.
3. **Major, `app/api/src/parlayhitters_api/desk.py:35-49` `money_fields`:** add `plays[].contracts` and `plays[].fee_cents`
   (either non-null in shared), every string matching `\$\s?\d+(\.\d+)?` except "per $1", and any string starting with
   `/`. Add a bake test (`test_edges_and_bake.py`) that walks every string and key of the baked dict, shards included.
   Then rerun `mut.py` and expect 4/4 refused.
4. **Major, tests:** a fake-db test per write path (`watch.ts:137`, `userState.ts:95`, `photos.ts:121`,
   `Admin.tsx:167`) asserting the exact path, which must contain the viewer's own id (or `photos/<digits>`).
5. **Minor:** `desk.ts:274`: replace Wilson with a date-clustered interval from the producer (add `record.ci95` to
   desk_export, clustered on slate date). Or print "not clustered" and leave daily_dollar legs out of the rate.
   `History.tsx:38-41`: render the review line only when `c.n === 83 && Math.abs(c.mean_cents + 0.8795) < 1e-3`; otherwise
   print "decomposition from the 11-08 review applies to the n = 83 snapshot only". `Signals.tsx:138`: show the stored
   `edge_cents` with "after fee, unproven model".
   `hedge.ts:41` / `Hedge.tsx:186`: when the fee is 0 and `source !== "stored"`, show "fee not entered: counted as 0c".
   Also state that Kalshi rounds the fee on the whole order, so n x the one-contract fee is an upper bound.
   `Admin.tsx:167`: also delete `watch/<uid>`, and say that the viewer's device will republish their picks.
   README rules: add `{"path":"","read":"view","write":"owner"}` and `{"path":"data/users/{self}","write":"interact"}`.
   `Shell.tsx:207`, `:371`: "Signed in through this page's host". `photos.ts:10`: relax to `^[A-Za-z0-9_-]{16,64}$`
   (the contract only says "opaque 32-char"). `hedge.ts:75`: skip the `[0, 0)` bucket when the first threshold is 0.

## Playbook check

Model class: none (UI over stored exports; JARVIS's price check and the parlay engine are scored elsewhere).
(1) Information or capacity: n/a. (2) Enough data: the page's rates are n=83/12 dates (CLV), n=128 (record), n=30 (kill
switch). All are underpowered for a sign claim, and the page says "cannot be told apart from zero" where it matters.
(3) Test validity: CLV vs ask includes the half-spread; the page now says so. (4) Lineage and inherited guards: it
inherits desk_export v1's shared cut, which is intact in the bake. The consumer re-check (`money_fields`) is weaker than
the producer's guard, so it is not a second line of defence. (5) Scored against: the de-vigged close and the
market's Brier, copied faithfully.

## Not verified

The rendered DOM (no screenshot run; `shots.mjs` needs Chrome and was not run under the watchdog). Reserved
hash names (rule text not in the 0.2.96 `.d.ts`). The Leg page's edge section beyond `edgeLine`. Behaviour when
`claude.use()` never resolves (`platform.ts` has no timeout of its own). The asset id alphabet.

## Most likely failure mode if published as is

The friend opens Discover and sees "+11.4c edge after fees" on Reaves. The stale Pinnacle line behind it and JARVIS's
tripped kill switch are not on that card, so it reads as a bet. Separately, page source shows `/Users/devin/...`.

## Drafted rows (not appended)

none. This is a pre-merge code review, not a ledger result.
