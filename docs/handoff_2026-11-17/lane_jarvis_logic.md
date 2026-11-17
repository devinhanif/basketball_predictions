# Lane J3: betting-tools pure logic (2026-11-16, 23:25 to 23:45 CST)

Branch `agent/app-jarvis-betting-logic`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-logic`. Code commit `1bcb0f37`.
Everything is under `app/web/src/betting/`. Nothing in the app imports it yet, and it has no screens, CSS, flag reads,
network calls or framework imports. It does not include `index.ts` or `features.ts`; another lane owns those.

## Public API (for the screens lane)

**types.ts** (types only): `ProviderId`, `ProviderKind` (`exchange|sportsbook|entry`), `ProviderInfo`, `Side`,
`Provenance` (`live|delayed|manual|sample`), `RecordOrigin`, `MarketIdentity` (eventId, eventDate, playerId, stat, line,
period, settlementRuleId or null for unverified), `Market`, `Fee` (`known` with basis, or `unknown`), `QuotePrice`
(`binary|sportsbook|entry-pick`), `Quote`, `QuoteSlot` (`available|unavailable`), `Freshness`, `Catalog`, `SlipLeg`
(frozen `quote` and `market` snapshots, `amount` null for an entry pick, `placedAt`), `EntryTerms`, `SlipDraft`,
`SavedIdea`, `SlipWorkspace` (draft, ideas, manualQuotes), `Failure`, `Result`.

**catalog.ts**
- `KNOWN_PROVIDERS`: kalshi (exchange), draftkings (sportsbook), prizepicks (entry).
- `providersFor(bookIds)`: the known providers plus every export book id, each added as a sportsbook.
- `buildCatalog({providers?, markets, quotes})` returns `{catalog, rejected[]}`. It checks each record on its own: a bad record is listed with kind, index, id and reason, and the valid records are kept. Quotes are unique per event, provider, market and side. Nothing requires a fixed count.
- `refreshQuotes(catalog, quotes)`: a new frozen board. Captured legs and ideas do not change.
- `quoteSlot(catalog, marketId, provider, side)` and `boardFor(catalog, marketId, side)`: each slot is available or `unavailable`.
- `findMarket`, `oppositeSide`, `providerKind`.
- `freshness(quote, now, maxAgeMs=15 min)`: `current|stale|unknown|sample`.
- `comparability(a, b, now)` returns `{comparable, reasons[]}`. It checks the conditions for an equal-payout cost ranking. It never makes an arbitrage claim.
- Per-record checks: `checkMarket`, `checkQuote`, `checkPrice`, `checkProvider`.
- Keys: `marketIdentityKey`, `quoteSlotKey`.
- Holdout: `HOLDOUT_FIRST_DATE` and `HOLDOUT_LAST_DATE`, used by `inHoldoutSeason`.

**slipModel.ts**
- `normalisedCost(quote)` returns `{ok, cents}`, or a failure coded `entry_only`, `fee_unknown` or `invalid_price`.
- `legCost(leg)` and `legPayout(leg)` return a number or null. They return null for an entry pick, an unknown fee or an invalid amount.
- `legProblem(leg)` explains why a leg cannot be priced.
- Building a draft: `makeLeg(quote, market, role, {id, amount, placedAt})` and `defaultAmount`.
- Empty states: `emptyDraft()` and `createSlipWorkspace()` (empty; it never seeds anything).
- `activeLegs` returns the switched-on planned legs plus every recorded position.
- Edits: `addQuote` (re-enables a planned leg that already holds the same quote id), `removeLeg`, `toggleLeg` (refuses a recorded position), `setLegAmount` (whole contracts, or stake to the cent) and `setEntry`.
- `markPlaced(draft, placedAt)` records the switched-on planned legs as existing. Each keeps its id, quote, side and amount.
- `makeHedgeLeg(draft, quote, market)` makes a planned leg sized to the uncovered gross payout in that market.
- `describeOverlaps(legs)` describes same-market, same-player and same-event exposure in words only.
- `evaluateDraft(draft, {now, maxQuoteAgeMs?, maxMarkets?})` returns `{complete, totalCost, providerTotals, groups, rows, existingWorst, coverage, warnings, overlaps}`.
  - A floor, ceiling or "every listed outcome is positive" claim may come only from `coverage`.
  - `coverage` is either `{shown: true, worst, best, allListedPositive, improvesOnExisting, basis}` or `{shown: false, reason, text}`.
  - Reasons: `no_legs`, `incomplete`, `too_many_markets`, `stale_quote`, `unverified_settlement`, `related_markets`.
- Ideas: `saveIdea`, `loadIdea`, `removeIdea`, `recordOutcomes` (settling needs every active market), `ideaMarketIds` and `putManualQuote`.
- `validateSlipWorkspace(value)` returns `{ok, workspace}` with a deep-frozen copy, or `invalid_data` with the path that failed. `isSlipWorkspace` is the type-guard form.
- Limits: `MAX_LEGS`, `MAX_IDEAS`, `MAX_TAGS`, `MAX_MANUAL_QUOTES`, `ENTRY_MULTIPLIER_MAX`, `DEFAULT_ENTRY`.

**hedgeMath.ts**
- `enumerateOutcomes(ids, cap=10)` returns the outcomes, or `{ok:false, code:"too_many_markets"}`.
- `MAX_ENUMERATED_MARKETS`.
- `grossPerUnit(price)`, `grossPayoutOf(leg)`, `validNativeAmount(price, amount)`.
- `uncoveredPayout(legs, marketId, hedgeSide)` counts recorded legs only.
- `sizeToPayout(target, price)` rounds Kalshi contracts up to whole contracts and sportsbook stake up to the cent. It refuses an entry pick.
- `sizeHedge(legs, marketId, side, price)`.
- The donor's fixed scenario: `calculateFixedScenarioHedge(legs)` and `FIXED_SCENARIO_CANDIDATES`.
- `round6`.

**repository.ts**
- `scopeForViewer({source, id, loading?})`:
  - a platform viewer with an id gets `{kind:"user"}`;
  - anyone else gets `{kind:"device"}`;
  - while the platform viewer is still loading it returns null, which means wait.
- `storageKey(scope)` gives `ph.betting.workspace:device` or `ph.betting.workspace:user:<id>`. `scopeId` is also exported.
- `WorkspacePort<T>` has load, save (`expectedRevision`, `mutationKey`), exportJson and importJson. Every method resolves to a typed `PortResult`.
- `LocalWorkspacePort<T>({storage, scope, codec, now, newRevision?})` works over an injected `StorageLike`.
- `SLIP_CODEC`, `Codec`, `migrate(codec, fromVersion, doc)`, `MAX_IMPORT_CHARS`.
- `createWorkspaceSession({port, validate, clone, newMutationKey?})` provides getSnapshot, subscribe, load, stage, save, discard and cancel. `createSlipSession(port)` is the version for slip workspaces.
- `RepoCode`: `invalid_data`, `unsupported_version`, `scope_mismatch`, `conflict`, `save_failed`, `load_failed`, `storage_unavailable`, `mutation_key_reused`, `import_too_large`, `unsaved_changes`, `busy`.
- A server port is a marked SEAM comment and is not built.

**ids.ts**: `localId(prefix?)`, `jsonCopy`, `deepFreeze`, `frozenCopy`, and `digest` (FNV-1a, which binds an idempotency key to its payload; it is not a security function).

**sample.ts**: the labelled demo. These functions run only when called:
- `sampleCatalog()`, `sampleQuote(short, provider, side)` and `sampleMarketId(short)`;
- `buildNBASampleDraft()` (Jalen DraftKings YES, $10 at +145, $24.50 gross);
- `buildDemoDraft()` (Collin, $93 / $100);
- `sampleWorkspace()`;
- `SAMPLE_STAMP`.

## Donor to destination map
| Donor file | Destination |
|---|---|
| `ui/src/slip-model.ts` | `slipModel.ts`. The catalog part went to `catalog.ts`, the sample fixtures to `sample.ts` and the enumeration to `hedgeMath.ts`. |
| `ui/src/hedge.mjs` + `hedge.d.mts` | `hedgeMath.ts` (`calculateFixedScenarioHedge`) |
| `ui/src/local-id.ts` | `ids.ts` |
| `ui/src/types.ts` | `types.ts` (slip types only; Player, Account and Admin types not ported) |
| `ui/src/storage.ts` | `repository.ts` (`LocalWorkspacePort`). The unscoped key, sample fallback, photo and account validation were not ported. |
| `ui/src/integration/contracts.ts` | `types.ts`: Fee, provenance, entry-pick and freshness ideas. Not ported: `ModelSignal`, `FeedResult`, `PositionView`. |
| `ui/src/integration/validate-feed.ts` | `catalog.ts` (`buildCatalog`, per record). Model signals are not ported. |
| `ui/src/integration/model-feed.ts` | `catalog.ts` (`freshness`, `comparability`). `createModelFeedPort` is not ported because this lane does no network or model reading. |
| `ui/src/integration/workspace-controller.ts` | `repository.ts` (`createWorkspaceSession`) |

## Tests
`npx vitest run src/betting` gives 77 tests, all passing.

Of the 43 donor test cases considered:

| Donor tests | Ported | Adapted | Dropped |
|---|---|---|---|
| slip-model | 9 | 11 | 0 |
| hedge | 10 | 0 | 0 |
| controller | 6 | 1 (a conflict is now a typed result, not a thrown error) | 0 |
| feed | 0 | 5 | 1 |
| Total | 25 | 17 | 1 |

There are 35 new tests.

The dropped test is feed "reader reuses injected model services". This lane has no model reader and no network: the model is the nba/ export's.

The adaptations, all because of the hard rules or the catalog:
- A pick's cost and payout are null, not 0.
- Coverage on the demo is suppressed because its settlement is unverified. The rows still show $100 and +$7.
- `providerTotals` lists only the providers present.
- Snapshots are frozen, so the tests edit through the functions.
- Missing or invalid data is refused instead of falling back to sample.
- The "exactly 24 quotes" case was replaced, because no count is required.
- A current manual quote is comparable. The donor blocked it, but here a price the viewer typed is the input.
- The "percent probability" part was dropped because there are no probabilities here.

The donor's slip-ui, ui, connected-workspace and standalone tests cover screens. They are left for the screens lane.

Full gate: `cd app/web && npm run check && npm run build:artifact` exits 0 and prints "checks passed". Web tests went from 408 to 485, in 40 files before and 45 after.

## Bundles unchanged (same SHA-256 before the first commit and after the last)
| File | Bytes | SHA-256 prefix |
|---|---|---|
| `dist/assets/index-D9viIkEz.js` | 390,523 | b9b4078c |
| `dist/assets/index-DDMGYJkl.css` | 76,308 | a521f847 |
| `dist-artifact/parlayhitters.html` | 4,520,376 | 916ef1ee |
| `dist-artifact/preview.html` | 4,520,553 | 6bd4300d |

## Renamed donor concepts (rule 2)
- The idempotency "mutationId" or "token" is now `mutationKey`.
- The "revision token" is now a revision tag (`revision`).
- The controller's local `token` variable is now `op`.
- `PortError` is gone; failures are typed result codes.
- `Platform` is now `ProviderId`, an open set.
- `normalizedCostCents` is now `normalisedCost`, which returns a result.
- `source: illustrative` is now `provenance: sample`.
- `CANDIDATES` is now `FIXED_SCENARIO_CANDIDATES`.
- `calculateHedge` is now `calculateFixedScenarioHedge`.
- `canonicalSlipWorkspace` is folded into `validateSlipWorkspace`.

## Decisions (what / why / what would reverse it)
1. **Planned prices go stale after 15 minutes, or at `expiresAt` if that comes first.** A manual price ages too; a recorded position's entry price never goes stale. Why: a coverage claim on an old price is the failure the brief names, and a typed timestamp does not make a price live. Reverse: Devin sets a different age; it is the `maxQuoteAgeMs` parameter.
2. **The coverage summary is separate from the outcome rows.** The rows are arithmetic on the entered prices. Floor, ceiling and "all positive" exist only in `coverage`, which is suppressed when a price is stale, the input is incomplete, settlement across providers is unverified, two lines on one player and stat are listed as independent, or the market count is over the cap. Why: the conservative reading of rule 3. Reverse: drop the `related_markets` gate if Devin accepts nested lines as listed.
3. **Cross-provider coverage needs a non-null `settlementRuleId` on the market.** Every market in the sample has null. Why: no provider rule equivalence is verified anywhere. Reverse: an export field that carries a verified rule id.
4. **A typed display name is not an identity.** In the regular build every viewer uses the device scope. While the platform viewer is loading, the scope is null (wait). Why: no accounts exist; guessing a scope could reuse another person's data. Reverse: real accounts.
5. **The catalog refuses event dates from 2025-07-01 to 2026-06-30 (season 2025, the frozen holdout).** Why: the holdout rule. Reverse: none expected.
6. **The sample uses donor names with invented prices.** Event ids say "sample", dates are 2024-11-04/05, every record is marked sample, and the sample runs only when called. Why: the brief requires the donor fixtures and the "pre-2025 fixtures" rule. Reverse: replace the names with synthetic ones if the screens lane finds real names next to invented prices misleading.
7. **The enumeration cap is 10 markets (1,024 rows).** Reverse: change `MAX_ENUMERATED_MARKETS`.
8. **Time parsing goes through `src/time.ts` (`instantMs`, `isIsoDate`).** The app's `time.test.ts` forbids `Date.parse` elsewhere. Zone-less instants are refused, never guessed.

## Not verified
- No screen uses this code, so mobile and desktop and light and dark are not applicable or checked.
- No real export rows were mapped. `prop_lines` to catalog is left to the screens lane; `providersFor` handles the book ids.
- Provider fee and settlement rules are not verified; they stay `unknown` or null.
- Nothing was checked in a real browser `localStorage`. The tests use an in-memory `StorageLike`.
- Nothing was pushed or merged.
