# Adversary review: JARVIS paper assistant, pre-merge (2026-10-25)

Branch `agent/market-jarvis-paper` at `fa04597` (`bfacdde` build + `fa04597` correction round). Scope: `nba/jarvis/`,
`configs/jarvis.yaml`, `ops/launchd/local.nba.jarvis.plist` (template, not installed), `tests/jarvis/`, one line in
`tests/test_layering.py`, plus 66 doc lines in `docs/DAILY_PIPELINE.md`. Rules under test: DECISIONS 2026-10-25 14:41,
14:43, 15:18 and 15:23, and memory note `user-end-goal-parlay-ai` (always a play; an honest computed edge label; stake
scales with the edge; paper book scored by CLV = close mid minus entry ask; iMessage only to `JARVIS_IMESSAGE_TO`;
numguard on every LLM number; no orders, no credentials).

The review ran in a scratch worktree at `fa04597` (branch `agent/adversary-jarvis-review`), with `data/` symlinked to the
main checkout. `ls -la` showed `data -> /Users/devin/Downloads/nba-prediction/data`. No network calls. No season-2025
rows. No outcome was computed: the label replay blanked `kalshi_markets.result` in a scratch copy and never read box
scores or closes. Every project DuckDB was opened `read_only=True` (brief opens of `market_asof.duckdb` and
`nba.duckdb` to extract 2026-27 rows into scratch files). The attack scripts live in the scratchpad
(`jarvis_attack/attack.py`, `stakes.py`, `replay.py`, `replay2.py`, `replay3.py`, `lock.py`), not in the repo.

Verdict: BLOCKED

(In the caller's terms this is **CHANGES_REQUIRED**: five blockers, eight majors, ten minors. The safety shell holds: no
orders, the recipient handling, the LLM cannot cause a send or change a number, read-only stores, the season filter and
the lock skip. What fails is the honesty of what it says and stakes. It can label a stale, depth-unknown pair as
"ARBITRAGE" and stake $9.85 on it. It stakes a fair-price "none" play 5x a "strong" one. It re-texts the same message
every 2 minutes after a partial send failure. It lets the LLM call a +0.4c play "great value ... easy money, a slam
dunk".)

## Top finding

**The `arbitrage` label is computed from legs that cannot be simultaneous, have unknown depth and carry no
settlement-rule warning. It must be downgraded until depth and simultaneity exist.** Kalshi prices come from the
snapshot store, written every ~15 minutes on this data (median gap 918 s between game-winner snapshot times,
10-20..10-25). `SlateState.tradable` (`slate.py:57-69`) accepts any quote up to `max_quote_age_min: 30`
(`configs/jarvis.yaml:34`). Polymarket US is read from the event `quote` with no `/book` depth (`polymarket.py:14-15`) and
stamped with the fetch time. `best_arbitrage` (`edge.py:169-196`) labels the pair `arbitrage` whenever both fees are
verified. Reproduced (A2): a 29-minute-old Kalshi LAL 50c plus a fresh Polymarket US LAC 45c gave

```
JARVIS ALERT (new arbitrage) | LAL@LAC 7:30p CDT | PLAY: Kalshi YES LAL to win at 50c, max 51c + Polymarket NO LAL to
lose at 45c, max 46c; stake $9.85 (10 pairs) | ARBITRAGE +1.5c per pair after fees (risk-free only if both legs fill) |
note: two legs are not simultaneous: place the thinner-looking leg first
```

These are the fast-arb blockers B1 (stale, depth-unknown leg), B2 (legs fetched at different times, here up to 30 min
apart rather than 40 s) and B5 (no settlement-rule warning), all at once. JARVIS cannot confirm any of them, because it
has no live Kalshi read and no Polymarket book.

## What holds

| Claim | Status | Evidence |
|---|---|---|
| Kalshi "N+" = Pinnacle "over N-0.5" | **Holds** | Real store: `threshold` 25.0 for title "Victor Wembanyama: 25+ points" (k1.py), so `sources.py:260-262` maps 25+ to o24.5. Settlement `>= threshold` (`book.py:378`). Test `test_kalshi_n_plus_threshold_matches_pinnacle_half_line`. |
| Polymarket US side mapping | **Holds** (data); **misleading wording** (M1) | Fixture DET@PHI: DET side `long` quote 0.51 = ask; PHI side 0.50 = 1 - bestBid. Each Quote has yes_ask = own-side quote and yes_bid = 1 - other-side quote (`polymarket.py:154-162`). Teams map by `teamId`, not by position. |
| Pinnacle h2h side mapping | **Holds** | `side` values on real rows are only `home`/`away` (m1.py). Every latest Pinnacle request since 10-20 has an h2h row (32 of 32 games). |
| Fees in the edge | **Holds as configured; understated for small orders** (M4) | Kalshi via `configs/parlay.yaml`. Polymarket US 0.0695 x C x p(1-p), banker's cent; a market whose `feeCoefficient` differs is skipped (`polymarket.py:129-132`). |
| LLM cannot cause a send or change a number | **Holds** | The gate (`runner.supports_alert`) runs on computed numbers before any LLM call. Every number in the text comes from `render.py` reading the `Play`. Test `test_llm_cannot_cause_or_change_a_send`. A4: an injected venue title does not change `supports_alert`. |
| Prompt injection into the LLM | **Holds for numbers and sends; text injection is possible (m2)** | Injury reasons never enter the alert DATA block (`explain.play_facts` uses player + status only). In `ask` they arrive sanitised to 48 chars. Tool arguments are all strings, so the model cannot launder a number through them. |
| Recipient only from env, passed via argv | **Holds** | `imessage.py:22-55`: the script reads `item 1 of argv`; no string splicing. stderr is redacted (`:86`). The plist placeholder fails `_PHONE`/`_EMAIL`. 6 tests. |
| Text length | **Holds** | `render.chunk` enforces <= 480 chars x <= 3 texts with an overflow note (test `test_chunking_is_phone_length`). |
| No orders, no credentials | **Holds** | The grep below only hits docstrings. Test `test_no_order_portfolio_or_signing_endpoint_anywhere`. |
| nba.duckdb read-only, season 2026 only | **Holds** | Every open goes through `open_ro(read_only=True)`; the Toolbox ATTACHes `READ_ONLY`. SQL filters `season = 2026`, and games are selected by slate date. Tests `test_nba_duckdb_is_only_opened_read_only`, `test_season_2025_is_never_loaded`. ModelView's internal reads were not audited (unverified). |
| CLV has no look-ahead | **Holds** | Close = last quote with `ts < tipoff` (`book.py:389-405`); entry = the ask at emission; the Polymarket quote ts is the fetch time. The definition has gaps (M3). |
| Lock: retry and skip when the pretip job holds nba.duckdb | **Holds** | A11: 10 connect attempts, 30 s of sleeps, then `idle: store busy, retry next tick`, 0 texts. nba.duckdb is never copied. Writers retry 180 s (`nba/db/connect.py:53`), so JARVIS's short reads cannot fail the daily job. |
| Layering line | **Holds** | `nba.jarvis.__main__` is added to `ENTRYPOINTS`. `tests/test_layering.py` passes, with no research import. |
| launchd template | **Holds as a template** (m6, m9) | `uv run --no-sync`, `StartInterval` 120, `RunAtLoad` false, placeholder recipient, logs under data/ops. Test `test_launchd_template_is_valid_uses_uv_and_holds_no_recipient`. |

## Checks run (exit codes shown; nothing piped through tail or head)

Python: `/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, with `PYTHONPATH=<scratch worktree>` (checked:
`nba.__file__` resolves into the worktree). Scratch root: `$S = /private/tmp/claude-501/.../scratchpad`.

| # | Check | Command | n | Result | Pass/fail |
|---|---|---|---|---|---|
| V1 | Lint | `ruff check nba/jarvis tests/jarvis tests/test_layering.py` | 29 files | All checks passed, exit 0 | pass |
| V2 | Format | `ruff format --check nba/jarvis tests/jarvis tests/test_layering.py` | 29 files | exit 0 | pass |
| V3 | Types | `mypy nba/jarvis` | 17 files | no issues, exit 0 | pass |
| V4 | Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/test_layering.py` | 102 | 102 passed, exit 0 | pass |
| G1 | No trading endpoint or credential | `grep -rn -i -E "\.post\(\|\.put\(\|\.delete\(\|/orders\|portfolio\|api[_-]?key\|authorization\|KALSHI_API\|private_key\|secret" nba/jarvis configs/jarvis.yaml ops/launchd/local.nba.jarvis.plist` | package | 2 docstring hits only | pass |
| A1 | Stale Kalshi quote vs a fresh Pinnacle move | `python attack.py $S/wt_jarvis_adv a1` | 1 tick | Kalshi snapshot 29 min old, Pinnacle 1 min old: **STRONG +7.30c**, 1 alert texted | **FAIL (blocker B2)** |
| A2 | Arbitrage on a stale, depth-unknown, non-simultaneous pair | `attack.py ... a2` | 1 tick | `label=arbitrage`, +1.52c, 10 pairs, $9.85, alert sent. Legs 29 min and 0 min old; no settlement wording | **FAIL (blocker B1)** |
| A3 | LLM oversell and numguard | `attack.py ... a3` | 7 prose cases | 5 of 7 pass as `llm`, including "Great value ... mispriced ... strong edge. Easy money, a slam dunk." (thin +0.4c), "a sure bet and a no-brainer" (none), "a 9-cent edge" (thin +0.4c), "twenty cents better ... Bank on it" (none) | **FAIL (blocker B5)** |
| A4 | Where fetched text reaches the LLM and the text | `attack.py ... a4` | 3 probes | Injury reason not in the explain DATA (False); venue title rendered verbatim (True); the alert gate is unchanged (False for thin) | pass for sends; minor m2 |
| A5 | Multi-part send with part 2 failing | `attack.py ... a5` (max_chars 120) | 4 ticks, 8 min | Part (1/3) delivered **4 times in 8 minutes**; 0 plays recorded; no attempt cap | **FAIL (blocker B4)** |
| A6 | Cap crowd-out | `python stakes.py $S/wt_jarvis_adv` (A6b) | 8 none + 1 strong | 8 near-fair `none` plays = $19.69 of $20; then **strong +10.2c -> 0 contracts, "stake $0 (nightly cap used)"** | **FAIL (blocker B3)** |
| A10 | Stake vs measured edge | `stakes.py` (A10) | 9 edges | none -0.25c: **$2.59**; thin +0.75c: $0.52; strong +3.25c: **$0.52**; strong +10.25c: $1.56; strong +18.25c: $3.11 | **FAIL (blocker B3)** |
| A7 | Re-emission of an unchanged play | `attack.py ... a7` | 4 ticks, 31 min apart | 4 bundles for 1 dedupe key, 4 texts. With synthetic scores (not outcomes), `weekly_summary` n = 4 for 1 bet | **FAIL (major M2)** |
| A8 | Close definition | `attack.py ... a8` | 2 cases | bid 0 / ask 0.30 gives mid 0.15 (YES CLV -10.0c from entry 0.25; NO mid 0.85). A quote_log row at T-60 beats a kalshi_prices row at T-5 | **FAIL (major M3)** |
| A9 | Copy fallback on a locked store | `attack.py ... a9` | 3 opens | 3 `jarvis_ro_*` directories left, each a full copy (the real `market_asof.duckdb` is 1,035,218,944 bytes) | **FAIL (major M7)** |
| A11 | nba.duckdb held by its writer | `python lock.py $S/wt_jarvis_adv` | 1 tick | 10 attempts, 30 s, idle, 0 texts | pass |
| R1 | Real-store facts, read-only | `python -I k1.py <copy of kalshi.duckdb>`; `python -I m1.py <market_asof.duckdb>` | kalshi_prices since 10-20: 1,152,021 rows | 0 NULL bids or asks; **36,024 bid = 0; 12,987 ask >= 1**. Snapshot gap median 918 s, p90 930 s. Pinnacle at t180/t120/t90/t60/t45/t30/t20/t15/t10/t5/close | facts |
| R2 | Label replay, slates 2026-10-20..24, every 10 min from first tip - 3 h to last tip; Kalshi + Pinnacle only, outcomes blanked | `python replay.py $S/wt_jarvis_adv $S/jarvis_attack/replay` | 574 best-per-game game-ticks, 36 games | none 278, **thin 293, strong 3**. 292 of 296 thin/strong are props. Pinnacle ref newer than the Kalshi quote by > 5 min in **80/296**, > 10 min in 21/296. Quote age median 8.9 min, max 27.9 | descriptive |
| R3 | Selection behind each pick | `python replay2.py ...` | 302 game-ticks; 9,948 measured candidates | **median 32 candidates per pick (max 50)**; mean candidate edge -2.35c; 8.6% of candidates >= 0c, 0.03% >= 3c. All 3 strong are **YES player props** (Leonard 25+, Simons 10+, Grant 15+) | major M5, M6 |
| R4 | Labels at the actual order size | `python replay3.py ...` | 296 thin/strong | Sized at 1 contract 82x, 2 contracts 207x, 3 contracts 3x, 7 contracts 4x. **14 of 293 thin labels are negative edge** with Kalshi's per-order fee rounding | **FAIL (major M4)** |

R1-R4 are descriptive counts over a fixed set of slates, not samples, so they carry no CI. They are evidence about
mechanics, not claims about edge. The six standard checks: (1) shuffled labels, (3) missingness by outcome and (6)
integer support do not apply, because nothing here is fitted or scored against outcomes; the integer mapping that
matters (N+ to o(N-0.5)) holds. (2) Planted future: A1/A2 are its analogue for prices (a planted fresh reference and a
stale leg), and the CLV close was read for look-ahead (none found). (4) Knockout: the props carry 292 of 296 non-none
labels, so the prop path (M5, M6) gets the line-by-line read. (5) Replication: the replay covers 5 slates. It
reproduces the builder's 10-25 dry-run shape (mostly thin, rare strong).

## Findings, severity-ranked (each with line and fix)

### Blockers (before merge, because the alert text is outward-facing to Devin once installed)

**B1. "ARBITRAGE" is labelled, staked and texted on legs that are stale, depth-unknown, non-simultaneous and unchecked
for settlement rules.** Lines: `slate.py:198` (`label = "arbitrage" if verified else ...`), `edge.py:169-196`,
`slate.py:64-67` (30-minute quote age), `polymarket.py:149-162` (event quote, no depth, `ts = now`),
`render.py:35` and `slate.py:204` (the only caveats). Fast-arb B1/B2/B5 apply in full, and JARVIS cannot fix them
itself: the 15-minute snapshot store can never put two legs within 5 s of each other.
*Fix:*
- (a) In `slate.arbitrage_candidate`, set `label = "arb_candidate"`, a new label that is not in `alert_labels`, unless
  every leg carries a `confirmed` flag. Only a `QUOTE_SOURCES` hook can set that flag, for example the fast-arb confirmed
  candidate, which must hold booked depth on both legs and venue timestamps no more than 5 s apart (fast-arb B2 fix).
- (b) Size an `arb_candidate` under the `thin` cap at most. Render it as
  `ARB CANDIDATE +x.xc (unconfirmed: legs up to N min apart, depth unknown)`.
- (c) Append fast-arb's ` | RESOLUTION RISK: postponement/OT rules differ by venue, not verified` to every two-leg
  play until `configs/jarvis.yaml` has `settlement_rules_verified: true` with sources.
- (d) Test: a 29-min Kalshi leg plus a fresh Polymarket leg gives no `arbitrage` label and no alert.

**B2. A stale venue quote against a fresher Pinnacle reference is labelled strong and texted.** Lines:
`slate.py:131-147` (only the reference's own age is checked, `stale = ref_age > max_ref_age_min`). No check compares the
quote time with the reference time. A1: Kalshi 29 min old, Pinnacle 1 min old after a move, STRONG +7.3c, alert sent.
On real slates the reference is > 5 min newer than the quote in 80 of 296 thin/strong picks (R2). This is the same
failure as the 19:00 proxy tip: the price we "beat" may no longer exist. The paper book then records an entry at that
vanished ask, which inflates CLV by the move.
*Fix:* in `single_candidate`, compute `skew = (ref.ts - q.ts)` in minutes. Cap the label at `thin` (and never alert)
when `skew > freshness.max_ref_lead_min` (new config, 2) or `age_min(q.ts, now) > freshness.max_alert_quote_age_min`
(new config, 5). Add both to `reason`. Test with the A1 setup: label `thin`, no text.

**B3. Stake does not scale with the edge. Entertainment plays out-stake strong plays and can spend the nightly cap before
a strong edge arrives.** Lines: `edge.py:125-148` (`none_budget` gives up to $3 for a fair-or-worse price, while quarter
Kelly on a $35 venue bankroll gives about $0.50-1.60 for +3c to +10c) and `slate.py:285-304` (the cap is shared
first-come, and the slate summary records every game's play at T-90). A10: none -0.25c $2.59 vs strong +3.25c $0.52.
A6b: 8 near-fair `none` plays use $19.69 of $20, and a +10.2c strong play gets "stake $0 (nightly cap used)". This
breaks Devin's condition in DECISIONS 14:41 ("stake scales with the edge").
*Fix:*
- (a) Make the ladder monotone: `none` = 1 contract (or `none_stake_usd.max: 1.0`). Floor `thin` at `min_bet_usd` and
  `strong` at a new `caps.strong_floor_usd` (e.g. 3.0), and require `floor(strong) > max(thin) >= max(none)`.
- (b) Reserve the cap: `caps.reserve_for_strong_usd` (e.g. 10). none/thin may use only `nightly_usd - reserve`.
- (c) Test: stake is non-decreasing in edge across labels, and a strong play after N entertainment plays still gets
  at least its floor.

**B4. A partly failed multi-part send re-sends its delivered parts every tick until tip.** Lines: `runner.py:110-139`
(`ok = all(...)`; any failure records `mode = "failed"`, and plays are not recorded), `book.py:254-268` (`sent_recently`
and `slate_summary_sent` count only `sent`/`dry_run`). A5: part (1/3) was delivered 4 times in 8 minutes, and it repeats
every 2 minutes for hours. A total failure retries every tick with no cap or backoff.
*Fix:*
- (a) Record `mode = "partial"` when any part went out, and let `sent_recently` / `slate_summary_sent` count `partial`.
- (b) Add `delivery.max_attempts_per_key` (e.g. 3 per slate date). After that, log `gave_up` and stop.
- (c) Test: with the second part failing, the first part is delivered exactly once.

**B5. The LLM can oversell a thin or none play, and numguard misses hyphenated and spelled-out cents.** Lines:
- `explain.py:56-59`: the edge-word check runs only for `none`, and `_OVERSELL` misses "easy money", "slam dunk",
  "no-brainer", "sure bet", "bank on", "hammer", "can't-miss" and "locks".
- `explain.py:43` `_CENTS` does not match "9-cent", and `numguard.py:77-78` exempts integers 0-10, so a "9-cent edge"
  on a +0.4c play passes. Number words ("twenty cents") are never checked.

A3: 5 of 7 adversarial sentences pass as `llm`. This breaks DECISIONS 14:41: "it never presents a coin flip as a lock".
*Fix:*
- (a) For labels `none` and `thin`, apply the edge-word check as well. Widen `_OVERSELL` to the list above, plus
  `\blocks?\b`, `\bhammer\b`, `\bno[- ]brainer\b`, `\bsure (?:bet|thing)\b`, `\bbank on\b`, `\beasy money\b`,
  `\bslam dunk\b`, `\bcan'?t[- ]miss\b`, `\bsharp (?:side|play)\b`.
- (b) Change `_CENTS` to `(\d)\s?-?(?:cents?|c|¢)\b`. Reject outright any prose containing a number word next to
  cent/percent/point (`\b(?:one|two|...|twenty|thirty|fifty|hundred)[- ]?(?:cents?|percent|points?)\b`).
- (c) Simplest safe default: use the template for `none`/`thin`, and the LLM only for `strong`/`arbitrage`.
- (d) Make every A3 sentence a test case.

### Major

- **M1. A Polymarket US arbitrage leg is rendered as "Polymarket NO LAL to lose".** The instrument is the LAC side
  token, and Polymarket US has no "NO LAL" button. A hurried reader buys Lakers and doubles the exposure instead of
  hedging. `edge.best_arbitrage` (`edge.py:178-185`) adds `no` legs for Polymarket, while `best_per_game`
  (`slate.py:261-262`) skips them. *Fix:* in `best_arbitrage`, skip `side == "no"` when `q.venue == "polymarket"` (the
  other team's `yes` quote is the same bet). Render Polymarket legs as `Polymarket BUY <team> (<side description>)`.
- **M2. An unchanged play is re-texted and re-recorded every 30 minutes, which pseudo-replicates the paper record.**
  `book.record` (`book.py:150-157`) and `sent_recently` dedupe only within `dedupe_minutes`. A7: 4 bundles and 4 texts for
  one bet; `weekly_summary` n = 4, which also feeds `claim_ok`. *Fix:* record a dedupe key at most once per slate date.
  Re-alert only when the label upgrades or the edge moves >= 1c, and update a `last_seen` column. In
  `weekly_summary`, count one row per (slate_date, dedupe_key) at its first emission, and count an arbitrage bundle once
  (not per leg).
- **M3. The CLV close is under-defined, and the code prefers an earlier quote.** `closing_quote` (`book.py:389-409`)
  returns JARVIS's own `quote_log` row before even looking at a later `kalshi_prices` snapshot (A8: T-60 used, T-5
  ignored). A one-sided book (bid 0 or ask >= 1; 36,024 and 12,987 such rows since 10-20) yields mid = ask/2 or
  (bid+1)/2 (A8: -10.0c from one row). *Fix:* take the latest row across both sources. Set CLV to NULL when
  `bid <= 0 or ask >= 1` (no two-sided close), and report the NULL rate per label in `weekly_summary`. The declared
  convention (close mid minus entry ask) is unchanged; this only defines its undefined case. Commit it before the first
  scored row (none exist yet). After that it would be a new rule.
- **M4. Edges are priced with a 100-lot fee, but the plays are 1-2 contracts.** `edge.fee_per_contract_raw`
  (`edge.py:27-32`) ignores Kalshi's per-order ceiling. R4: 14 of 293 thin labels are negative edge at the sized order.
  *Fix:* after sizing, recompute the edge with `order_cost(cfg, venue, price, n)/n` and relabel, or label using the
  1-contract fee, which is conservative. Test: a +0.1c 100-lot edge at 1 contract is labelled `none`.
- **M5. "thin" is mostly the best of ~32 near-fair prices, not evidence of an edge.** R3: each pick is the max over a
  median of 32 candidates; 8.6% of all candidates are >= 0c, so under independence a best-of-32 is >= 0 about 94% of
  the time with no edge anywhere. The label is arithmetically true and selection-biased. *Fix:* put `best of N priced
  bets` in `reason` and in the text. Keep `thin` stakes at the floor (B3). Read the weekly CLV for `thin` as the test of
  whether it means anything (DECISIONS 15:18 reversal condition).
- **M6. YES player-prop labels may be inflated by the DNP rule.** Pinnacle props void on DNP, so its no-vig number is
  P(over | plays). If Kalshi settles a DNP as NO, the fair YES price is P(plays) x P(over | plays), and every YES edge is
  overstated by about P(DNP) x fair. Kalshi's DNP rule is recorded as unconfirmed
  (`docs/KALSHI_READINESS_2026-10-09.md:180`). All 3 strong labels in R3 were YES props. *Fix:* read Kalshi's KXNBAPTS /
  REB / AST / 3PT contract terms and record the rule with a source in `configs/jarvis.yaml`. Until then, cap YES-prop
  labels at `thin`, or multiply `fair` by the model's `p_play` when available (`forecasts.ModelView` has it).
- **M7. The copy fallback leaks a full copy of the store every time it fires.** `sources.open_ro` (`sources.py:57-60`)
  calls `mkdtemp` and never deletes it. A9: 3 opens left 3 copies. The fallback is used for `market_asof.duckdb`
  (1.0 GB) at `sources.py:127, 227`, and the copy omits any `.wal`. *Fix:* never copy `market_asof.duckdb`; skip the tick
  as for nba.duckdb. For the small stores, copy into one fixed scratch path (overwrite) and delete it on close, through a
  context manager; copy `<file>.wal` too when it exists. Test: no `jarvis_ro_*` directory survives a call.
- **M8. The paper book records a play only when the text was delivered.** `runner.py:120-128`. If Messages fails or the
  placeholder recipient is installed, the book stays empty and nothing says so. The CLV record then depends on delivery,
  not on decisions. *Fix:* record plays at decision time with a `delivered` column (sent / failed / partial / dry-run
  excluded), and keep dedupe on recorded-and-delivered.

### Minor

- m1. `render.stake_phrase` (`render.py:54-55`) says "nightly cap used" whenever contracts = 0, including "play too big".
  *Fix:* carry the reason from `size_plays`.
- m2. Venue titles go into the text verbatim (`edge.leg_from_quote` desc = Kalshi `title`). *Fix:* use
  `knowledge.sanitize(title, 60)` in `sources.load_kalshi_quotes`.
- m3. Polymarket US `live`/`ended` are ignored (fast-arb M3). The slug date (ET slate date) and the abbreviations (GS, NY,
  SA, NO, UTAH) are unverified for late or renamed games. A miss is a silent 404 skip. *Fix:* skip when `live or ended`,
  and count 404s per game in the notes.
- m4. `book.cluster_ci` is a second bootstrap implementation. `nba/truth/bootstrap.py` does not exist yet. *Fix:* move
  it there when it lands, and keep the date clustering.
- m5. `cli.cmd_watch` (`cli.py:135-137`) calls `build_state` outside the lock guard in the non-`--once` loop. *Fix:*
  wrap it like `run_tick`.
- m6. No watchdog heartbeat for `local.nba.jarvis` (same as fast-arb M4). *Fix:* add one at install (Devin's call).
- m7. After the last tip, every tick until ET midnight still fetches Polymarket for every game. *Fix:* return early
  when no game has `tipoff > now`.
- m8. `arg_max(yes_bid, ts)` and `arg_max(yes_ask, ts)` (`sources.py:161`) can come from different rows if one is NULL
  (0 NULLs since 10-20). *Fix:* use `arg_max(struct_pack(yes_bid, yes_ask), ts)`.
- m9. The install command puts the recipient in `plutil`'s argv (briefly visible in `ps`) and leaves it exported in
  the shell. *Fix:* add `; unset JARVIS_IMESSAGE_TO` to the documented command.
- m10. `ask` records its target play by default (`agent.py:349-357`, `--no-log` to opt out), so casual questions spend
  the paper cap. *Fix:* default to no logging in `ask`, or log under `source='ask'` outside the cap.

## Answers to the attack list

- **Label honesty:** fails in four ways. B2 (stale quote vs fresher reference). M4 (fee at 100-lot). M6 (YES props
  under an unconfirmed DNP rule). M5 (best-of-32 selection). The Kalshi "N+" mapping and the Polymarket US side data are
  correct; the Polymarket "NO" wording is not (M1). numguard misses hyphenated and spelled-out cents, and the word
  filters let a thin or none play be called value or a sure thing (B5).
- **Arbitrage:** yes, the `arbitrage` label must be downgraded until depth on both legs and leg simultaneity of 5 s or
  less exist, with a settlement-rule warning until the rules are read (B1).
- **Prompt injection:** fetched text cannot cause a send or change a number. It can put words into the text (m2), and
  the LLM can oversell without any injection (B5).
- **iMessage:** the recipient handling, argv passing and length limits hold. Dedupe does not prevent re-sends after a
  partial failure (B4), and it re-texts unchanged plays every 30 minutes (M2).
- **Stakes and caps:** "stake $0 (cap used)" still texts strong/arbitrage, as designed. But entertainment plays reach
  the cap first, and the stake ladder is inverted (B3).
- **Paper book and CLV:** no look-ahead, season 2026 only, nba.duckdb read-only. The close definition (M3), duplicate
  records (M2) and delivery coupling (M8) need fixing before the first scored night.
- **Lock behaviour:** holds (A11). The copy fallback leaks (M7).
- **launchd template and layering:** hold; m6 and m9 apply at install.

## Playbook check

- **Model class:** none in `docs/MODEL_PLAYBOOK.md`. JARVIS is a decision and labelling layer over market prices (no
  fit). The nearest guidance is "markets are a prior, not a target" and "score on the right support". The class checks
  applied are price freshness, fees at the real order size, settlement equivalence, and selection over candidates.
- **1. Information or capacity?** Neither model information nor capacity. The label is a price comparison (venue ask vs
  Pinnacle no-vig). Its only information edge would be speed, and the 15-minute snapshot store removes that (B1, B2).
- **2. Enough data for this class?** No training. For evaluation, the paper CLV needs `min_settled` over
  `min_settled_dates` (from `configs/parlay.yaml`), counted on distinct bets (M2), or it is descriptive.
- **3. Was the test valid?** The builder's tests are valid unit tests. None covers time skew, partial sends, stake
  monotonicity, order-size fees or oversell vocabulary. Those are the five blockers' tests. The 10-25 dry run ("0 arbitrage,
  0 strong, 2 thin, 5 none") was one evening. The 5-slate replay here matches its shape.
- **4. Lineage:** the parlay assistant (numguard, tools, the Ollama backend) and the fast-arb checker (Polymarket US
  client, fee object). The fast-arb guards B1/B2/B5 were not carried forward (B1). The parlay engine's "keep your money"
  guard was dropped on purpose by Devin's override; the honest label is its replacement, and it is the part that fails.
- **5. Scored against:** CLV = close mid minus entry ask, per leg, with a date-clustered bootstrap. That is the right
  question for "is the label honest", once M2/M3/M8 are fixed. P&L is reported but is not a claim.

## Corrected family and power note

No inferential claim was made by the build, and none is made here. The paper book's future family is CLV per label
(arbitrage, strong, thin, none: 4 tests, BH at the alpha Devin sets). It must be frozen as a rule before the first
scored night if anything is to be claimed from it. Power: unknown until the per-bet CLV SD is seen. On R2's rates
(about 0.6 strong picks per slate, all props), a strong-label CLV test would need months. Expect "underpowered, cannot
conclude" for `strong` for a long time. That is not "no edge".

## The one biggest risk

Devin acts on an "ARBITRAGE" text. Its Kalshi leg is a snapshot up to 30 minutes old, and its Polymarket leg has unknown
depth, settlement rules that may differ, and (M1) a "NO LAL" wording that invites buying the wrong side. The result is
one naked directional position of up to $10, presented as risk-free.

## What was not verified

- The live Polymarket US gateway (no network). Whether the event `quote` is live or cached by the gateway. The slug and
  date convention for late tips and renamed abbreviations.
- Kalshi's DNP and postponement settlement rules, and Polymarket US's rules.
- `forecasts.ModelView` internals (the Toolbox `_need_games` reads), beyond the READ_ONLY ATTACH.
- macOS Messages behaviour with real osascript: rc 0 without delivery, and per-message length limits.
- A real-store CLV or P&L number (out of scope: no outcomes computed).

## Recommended changes (not applied by the adversary)

In order: B1, B2, B3, B4, B5, then M1-M8 before the first real install. Then a diff-only re-check by the adversary,
which also reads the revision for new contradictions. Nothing should be installed until the fixes for B1 and B4 have
tests.

## Proposed DECISIONS row (draft, not appended)

| 2026-10-25 15:40 | JARVIS adversary review CHANGES_REQUIRED (fa04597, docs/reviews/jarvis_paper_review_2026-10-25.md). Blockers: B1 `arbitrage` labelled and staked ($9.85) on a 29-min-old Kalshi leg + depth-unknown Polymarket US quote, no settlement warning; must be `arb_candidate` until a confirmed (depth + <= 5 s) source exists. B2 stale quote vs fresher Pinnacle labelled strong and texted (real slates: ref > 5 min newer in 80/296 thin/strong picks). B3 stake inverted (none -0.25c $2.59 vs strong +3.25c $0.52) and entertainment plays exhaust the $20 cap before a strong edge. B4 partial send failure re-sends every tick. B5 LLM may call a thin/none play "great value ... easy money"; numguard misses "9-cent". Holds: no orders, recipient via env + argv, LLM cannot change numbers or sends, read-only, season 2026, lock skip, N+ mapping, Polymarket US side data. Fixes to the builder; diff-only re-check next. | Adversary sequence. | n/a |

---

## Re-check (diff only): `git diff ba5fd68 c47af06` (2026-10-25, evening)

Scope: the fixes for B1-B5, M1-M8 and the minors (except m4, m6), plus Devin's new rules (Discord sink, payout lines,
picks, CLV kill switch, $20 floor, Daily Dollar, model weight by stat, halftime check-ins, morning recap). Same scratch
worktree, fast-forwarded to `c47af06`; `data -> /Users/devin/Downloads/nba-prediction/data` (checked with `ls -la`).
The first-round attack scripts were rerun unchanged, except A5, where only the renamed config field changed
(`attack_v2.py`). One new probe was added (`giveup.py`). No network, no season 2025, no real outcomes, no write
connection to a project store.

Verdict: BLOCKED

(In the caller's terms: **CHANGES_REQUIRED**, narrowly. All five blockers and all eight majors are closed, verified by
rerunning the original attacks. Two small new items must be fixed before install: N1, the Daily Dollar "fair" line,
and N2, `weights --force` skipping the frozen weekly cadence. Everything else below is a recommendation.)

### Verification (unpiped)

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check nba/jarvis tests/jarvis tests/test_layering.py` | All checks passed, exit 0 |
| Format | `ruff format --check nba/jarvis tests/jarvis tests/test_layering.py` | 37 files already formatted, exit 0 |
| Types | `mypy nba/jarvis` | no issues in 23 files, exit 0 |
| Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/test_layering.py` | **148 passed**, exit 0 |

### The blockers, re-attacked

| Item | Attack rerun | Before | After | Closed? |
|---|---|---|---|---|
| B1 arbitrage on stale/no-depth legs | `attack.py a2` | `arbitrage` +1.52c, $9.85, alert | No alert. A cover is `arbitrage` only when both legs are `confirmed` (set only by a QUOTE_SOURCES hook), read <= `confirmed_max_gap_s` 5 s apart, and fees are verified (`edge.py` Cover, `slate.py` arbitrage_candidate). Otherwise `arb_candidate`, which is not in `alert_labels`, is sized as thin, and carries `RESOLUTION RISK` when cross-venue. Test `test_a_stale_kalshi_leg_plus_a_fresh_polymarket_leg_is_not_an_arbitrage` | yes |
| B2 stale quote vs fresher Pinnacle | `attack.py a1` | STRONG +7.30c, texted | `thin` +7.05c, 0 alerts. Caps: `max_ref_lead_min` 2, `max_alert_quote_age_min` 5 | yes |
| B3 stake ladder and cap reserve | `stakes.py` | none -0.25c $2.59 > strong +3.25c $0.52; strong $0 after 8 none plays | none $0.52 (1 contract) < thin $2.59 < strong $4.66. 8 none plays use $4.16; a later strong gets $4.66. `config.py` refuses a non-increasing ladder | yes |
| B4 partial send re-sent | `attack_v2.py a5`; `giveup.py` | part 1 delivered 4x in 8 min | part 1 delivered **once** in 4 ticks (mode `partial` counts as sent). Total failure: **2 attempts in 8 ticks**, capped by `max_attempts_per_key` 3 | yes |
| B5 LLM oversell / numguard | `attack.py a3` | 5/7 adversarial sentences passed | 0/7 pass. The LLM runs only for `strong`/`arbitrage`; oversell and number-word filters are wider; `_CENTS` handles "9-cent"; the whole reply is judged before clipping | yes (stricter than needed: a legitimate strong sentence with "3.4-cent" is also refused, which is the safe side) |

The majors: **M1** a Polymarket NO leg is skipped in `best_arbitrage`, and the Kalshi NO leg reads "<other team> to
win" (test). **M2** one row per (slate, key); re-alerts need a >= 1c move. A7 now gives 1 bundle, 1 text, n = 1.
**M3** the close is the latest across both sources (A8: T-5 snapshot used); one-sided closes give NULL CLV, and the
null rate is reported. **M4** labels are priced with the 1-contract fee. **M5** "best of N priced bets" is in the notes.
**M6** with the DNP rule unverified, props without a model P(plays) are capped at thin. `forecasts.ModelView` has no
`p_play` method, so today **every prop is capped at thin** and the "includes P(plays)" path is dead code. That is
conservative, but it is worth knowing. **M7** A9 leaves 0 copies; `market_asof.duckdb` is never copied; the `.wal` is
copied. **M8** plays are recorded at decision time with `delivered`. Minors m1, m2 (`sanitize_title`, 60 chars), m3,
m5, m7, m8 (struct `arg_max`), m9 (`unset` in the install line) and m10 (`ask` logs only with `--log`) are done.

### Discord webhook

- **Only POST in the package: holds.** `grep -rn -E "\.post\(|\.put\(|\.patch\(|\.delete\(|httpx\.|urllib|requests|socket|subprocess" nba/jarvis`
  returns `sinks.py:64` (`http.post(url, json=body)`) as the only POST. The other hits are the Polymarket GET client
  and the existing osascript `subprocess.run`.
- **URL redacted: holds in code.** The URL comes from env only and is validated against the Discord webhook shape.
  Transport errors are reported as the exception type only; HTTP errors as the status code run through `redact`. It is
  never written to `sends` (hashes only). `allowed_mentions: {parse: []}` blocks @everyone injection.
- Residual (minor):
  - httpx logs `HTTP Request: POST <full URL>` at INFO. Nothing in the JARVIS import closure configures logging
    (`grep basicConfig`: only `nba/shadow_llm/__main__.py`, `nba/odds/__main__.py`, not imported), so it goes nowhere
    today. Pin `logging.getLogger("httpx").setLevel(logging.WARNING)` in `sinks.py` so that a future
    `basicConfig(INFO)` cannot write the token into `data/ops/launchd_jarvis.err`.
  - The installed plist under ~/Library/LaunchAgents holds the URL in plain text (by design, outside the repo).

### Daily Dollar: honest line? correlated or cheap?

**The label is never better than none:** `daily_dollar` is outside `alert_labels`, the kill switch, the label record
and the weight record, and it is scored as one bundle. **The line is not honest yet (N1, required).**
`dailydollar._ticket` accepts a ticket when `cost/n <= 1.2 x product(leg fair)`. For any leg Pinnacle does not post
(most alt-ladder legs like "2+ assists" or "2+ threes"), the leg's fair is **Kalshi's own mid** (`_leg_fair`, line 36).
A one-sided leg's fair is its ask. So "at or below fair" means "at or below 1.2 x the venue's own mid product". The
"edge" is the assumed 1.2 minus the spreads, and nothing is measured. The text then says "buy only at or below Xc ...
(fair Yc with an ESTIMATED correlation factor)", which reads as a value claim.

**Correlated or cheap? Cheap.** Correlation is enforced only by structure: the team's win plus props of different
players on that team. `corr_factor` is one constant, so the optimiser cannot prefer a more correlated leg. It maximises
`1.2 x product(mid) - product(ask) - fee` inside the 50-300x band, which picks tight-spread, cheap legs. That is how
"LAC win + Hachimura 2+ ast + Lopez 2+ threes" comes out. The config's only evidence (same-player legs hit 1.42x) is
for **same-player** combos, which the code forbids (`dailydollar.py:100-101`). Devin's rule asks for "team wins big +
its star's big night"; nothing selects a star.

*Fix N1 (required):* print the factor-free numbers next to the estimate, for example
`no-correlation fair Zc; at Xc this is W% above it, so it needs correlation >= W to break even (unmeasured)`. When no
leg has a Pinnacle price, say `no outside reference: fair = Kalshi mid x 1.2`. Add `EV unknown (entertainment)` to the
first line.
*Recommended:* restrict prop legs to the team's top-2 usage players (`kb.usage_pace`), or drop the "correlated" claim
and call it a same-team long shot. Log Kalshi combo RFQ quotes to test the product-of-asks pricing assumption.

### Model weight by stat

**It cannot rise without n >= 30, >= 10 dates and an 80% date-clustered lower bound > 0, and it is clamped to
[0, 0.5]** (`weights.py:102-106`; tests `test_weight_rises_by_a_step_only_...`, `..._holds_on_thin_evidence`,
`test_weight_never_exceeds_the_cap`). It lowers on mean < 0 with no CI (asymmetric, safe side). Gaps:

- **N2 (required): `python -m nba.jarvis weights --force` skips the frozen "at most once per 7 days".**
  `cli.py:276` passes `force=a.force` to `update_weights`, and each forced run on the same evidence adds a step. Five
  forced runs take w from 0 to 0.5 in one day, which breaks the frozen cadence (non-negotiable 4). *Fix:* `--force`
  may only display. Or make an update idempotent per (stat, ISO week): a second evaluation in the same week logs
  `hold`. Add a test: two forced runs on the same evidence give one step.
- Recommended, for Devin, **as a new rule id, not an edit**: the rule credits the model for the Pinnacle edge. It
  raises w when model-leaning bets have positive CLV. But those bets were selected on the Pinnacle-based label, so if
  Pinnacle-based edges earn positive CLV, the leaning subset does too even when the model adds nothing. A fair test is
  the **contrast**: CLV of model-leaning bets minus model-against bets (or CLV regressed on model - Pinnacle), with
  the lower bound > 0.
- Minor: the state is the last `w_after` line in `data/jarvis/model_weights.jsonl`. A hand-appended line moves w
  (within the clamp) with no n or CI. Hash-chain the log, or recompute w from the logged decisions.

### Kill switch and floor

- **Kill switch: works and fails safe.** The mean CLV of the last 30 settled bundles with a two-sided close; when it
  is < 0, every stake goes to the `none` size (test `test_kill_switch_drops_every_stake_to_the_none_size`). Two
  recommendations for Devin:
  - It counts **`none` plays**, which claim no edge and whose CLV is expected near or below zero (entry at the ask).
    It can trip on honest labels. Judge on `thin`/`strong`/`arbitrage`/`arb_candidate` only, as TRADING_CONTEXT
    says: "its labels are wrong".
  - It is a rolling window that clears itself when the mean recovers. TRADING_CONTEXT says "until fixed": latch it
    until Devin resets it.
- **Floor:** a banner ("at or below the $20 floor ... Do not place these") on every post when
  `balance.current_usd <= floor_usd` (test `test_at_or_below_the_floor_every_post_is_marked_paper_only`). It depends
  on Devin editing `current_usd` by hand in `configs/jarvis.yaml`. Nothing computes the balance, so the floor is only
  as current as that edit, and a config edit on main is a live-path change. Recommended: show the paper-book P&L since
  the last reset next to the hand balance in the morning recap, so drift is visible.

### New network or send paths

- **Halftime: no network.** `live.py` reads `data/raw/live_<ET date>/espn_scoreboard/*.json.gz` and `heartbeat.json`
  from the live capture's files. A stale feed (> 6 min) is said out loud. The price-now line reads the Kalshi store
  read-only. Each game posts once (`kind_sent("halftime", ...)`), and the give-up cap applies. Minor: a malformed
  ESPN event (`ev["competitions"][0]`, `c["team"]["abbreviation"]`, `int(score)`) raises KeyError/IndexError/ValueError
  outside `run_tick`'s `duckdb.IOException` guard. The process exits with a traceback and retries in 2 minutes. Alerts
  run before halftime in `_tick`, so none is lost. *Fix:* wrap the per-event parse in `try/except (KeyError,
  IndexError, ValueError, TypeError): continue` and count the skip.
- **Recap:** `score_book` runs at the first tick after 08:00 ET. It is the paper book's purpose, read-only on stores.
  It posts once per day (`kind_sent("recap", ...)`).
- **Sends:** every post goes through `Runtime.send` and then `make_sender` (Discord default, optional iMessage). The
  CLI `test-send` and `recap` use the same sender; `pick` and `weights` do not post. With both sinks configured, one
  sink failing marks the text failed, and it is retried up to the 3-attempt cap. The working sink may then get up to
  3 copies (minor; record per-sink delivery).
- **Copy fallback:** `jarvis_scratch/` is one fixed path shared by every JARVIS process. A launchd tick and an `ask`
  session copying the same store at once can unlink each other's copy mid-open (minor; add the PID to the name).

### Playbook check (re-check)

The class is unchanged: a decision and labelling layer, with one new adaptive element (the model weight). Information
or capacity: the weight is the first place the model's information enters JARVIS, and its test is confounded with the
Pinnacle edge (the contrast recommended above). Data: >= 30 settled bets over >= 10 dates, weeks away. Test validity:
the frozen rule is sound in its gates, but `--force` breaks its cadence (N2). Lineage: the parlay engine's
`leg_marginal` and `p_play` via ModelView. Scored against: the CLV convention as fixed in M3.

### Corrected family and power note

Unchanged. Nothing is claimed. The weight rule makes 6 per-stat tests each week (one-sided at 90%), so under the null
roughly 10% of evaluations per stat could raise a weight. The asymmetric lower-on-mean<0 step pulls it back toward 0,
and that is the only multiplicity control. Fine for a paper-only fair price capped at 0.5; not fine as evidence.

### Biggest remaining risk

The Daily Dollar text tells Devin a ticket is "at or below fair" every night, when the only thing above the price is
an assumed 1.2 factor on the venue's own mid. It is $1 a day, but it is the one remaining place where JARVIS states a
value it has not measured.

### Not verified (re-check)

- The live Discord webhook (no network).
- Whether Polymarket US or Kalshi postponement rules differ (still unread; RESOLUTION RISK stays).
- How Kalshi actually prices combos (product of asks vs RFQ).
- `kb.player_teams` for traded players.
- No Daily Dollar replay on real slates. The judgement above is from code and the builder's sample ticket.

### Proposed DECISIONS row (draft, not appended)

| 2026-10-25 21:00 | JARVIS re-check (c47af06, docs/reviews/jarvis_paper_review_2026-10-25.md, re-check section): B1-B5 and M1-M8 closed on rerun attacks (no arbitrage without confirmed depth + <= 5 s legs; stale/skewed prices capped at thin; ladder none $0.52 < thin $2.59 < strong $4.66 with a $10 strong reserve; partial post sent once, failures stop at the cap; 0/7 oversell sentences pass); 148 tests pass. Discord POST is the only POST; URL env-only and redacted. CHANGES_REQUIRED, narrow: N1 Daily Dollar must print the no-correlation fair and "EV unknown" (its fair is 1.2 x Kalshi's own mid where Pinnacle is absent; legs are cheap, not measured as correlated); N2 `weights --force` must not step a weight more than once per frozen week. For Devin: kill switch counts `none` plays and self-clears (TRADING_CONTEXT says labels and "until fixed"); weight rule credits the model for the Pinnacle edge (a contrast test would be a new rule id). | Adversary sequence. | n/a |

---

## Final check (diff only): `git diff d3f5f09 6f14f5d` (2026-10-25)

Same scratch worktree, fast-forwarded to `6f14f5d`, with `data/` symlinked. No new attacks. The first-round attacks
were rerun as a regression check (A1, A3, A5, the stake table).

Verdict: APPROVED

(In the caller's terms: **APPROVED** for merge. N1 and N2 are closed, and the recommendations on the kill switch,
logging and halftime were taken. The merge still needs the replay oracle IDENTICAL, because `tests/test_layering.py`
and `ops/` are touched. Installing the plist and setting `JARVIS_DISCORD_WEBHOOK` remain Devin's decision.)

| Check | Command / evidence | Result |
|---|---|---|
| Lint / format / types | `ruff check ...`, `ruff format --check ...`, `mypy nba/jarvis` | exit 0 / 0 / 0 |
| Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/test_layering.py` | **154 passed**, exit 0 |
| Only POST | `grep -rn -E "\.post\(" nba/jarvis` | `sinks.py:69` only |
| Regression A1 / A3 / A5 / B3 | `attack_v2.py a1`, `a3`, `a5`; `stakes.py` | thin, 0 alerts / 0 of 7 oversell sentences pass / part 1 delivered once / ladder unchanged (thin $2.59, strong $4.66) |

**N1 Daily Dollar: closed.**
- Legs are now the team's win plus its top-usage player's points, and optionally his assists or the second player's
  points. Top usage is ranked on 2026 possessions only (`knowledge.top_usage`, `season = 2026`, >= 50 on-court
  possessions).
- The line now says "EV unknown (entertainment)". It prints the no-correlation fair, the % above it, and the
  correlation needed to break even "(unmeasured)", and it says when no leg has a Pinnacle price ("fair = Kalshi mid x
  1.2 (assumed)").
- It cannot be labelled above none: it stays outside alerts, the kill switch, the label record and the weights.
- What is still unmeasured is now stated rather than hidden: the 1.2 factor, and whether Kalshi prices the combo at
  the product of the asks.
- Minor: early in the season, 50 possessions can rank a bench player as "top usage". Raising `min_possessions` as
  games accumulate is a config choice.

**N2 weights `--force`: closed.** `update_weights(display_only=True)` returns before the log is written, and the
CLI prints "(display only, nothing logged)". No flag can add a second step inside the 7-day window. The frozen gates
(n >= 30, >= 10 dates, 80% lower bound > 0, w in [0, 0.5]) are unchanged.

**Kill switch: matches TRADING_CONTEXT.** It counts only `thin`/`strong`/`arbitrage` (`KILL_LABELS`). A trip is
written to `killswitch_log` by the runner (not in dry-run) and stays latched until `killswitch --clear` logs a dated
row; the count restarts from that clear.

**Small items:**
- httpx/httpcore logging is pinned to WARNING in `sinks.py` (and in the CLI).
- Halftime: each event is parsed under `try` (KeyError/IndexError/TypeError/ValueError), and each game's post is
  guarded on its own.
- Per-sink dedupe: `sink_log(sink, sha(text))`, so a sink that already received a text is skipped and one failing
  sink no longer makes the other repeat. Minor: the key has no date, so the same exact text on a later day would be
  skipped. Texts carry the matchup and tip time, so this is unlikely; add `slate_date` to the key if it ever matters.
- Book migration: `ALTER TABLE plays ADD COLUMN IF NOT EXISTS` runs on the paper book's own write connection only.
  `_rows` reads a missing table as empty. Every write path creates the schema first, so this cannot hide a send
  record in practice.

**Still open (recorded, not blocking):**
- Polymarket US and Kalshi settlement rules are unread, so RESOLUTION RISK stays on cross-venue covers.
- The Kalshi prop DNP rule is unverified, and ModelView has no `p_play`, so every prop stays capped at thin.
- The weight rule's model-vs-Pinnacle confound would need a new rule id if Devin wants it.
- The live Discord webhook is untested from here.

**Biggest remaining risk:** Devin acting on a cross-venue `arb_candidate` before reading both venues' settlement
rules. It never alerts and is sized as thin, and the text says it is unconfirmed.

### Proposed DECISIONS row (draft, not appended)

| 2026-10-25 22:00 | JARVIS final adversary check APPROVED (6f14f5d, docs/reviews/jarvis_paper_review_2026-10-25.md, final section): N1 Daily Dollar now same-team top-usage legs with "EV unknown", no-correlation fair and break-even correlation printed; N2 weights --force display-only; kill switch on thin/strong/arbitrage, latched until a dated clear; httpx log pinned; halftime guarded; per-sink dedupe. 154 tests pass; Discord POST the only POST. Merge needs the replay oracle IDENTICAL; install and the webhook are Devin's. Open: settlement rules unread, prop DNP rule unverified (props capped at thin). | Adversary sequence. | n/a |
