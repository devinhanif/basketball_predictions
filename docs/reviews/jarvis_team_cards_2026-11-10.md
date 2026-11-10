# Adversary review: JARVIS per-team Discord cards (agent/market-jarvis-team-cards, 6e9ee57), 2026-11-10

Verdict: BLOCKED

Grade: **WOUNDED**. The feature is real and mostly delivery-only: no secret leaked under any attack, and the alert, slate and
halftime decisions are unchanged. Two problems block the merge: (1) a Discord outage can stretch the 2-minute tick to about
8 minutes, and (2) the pre-tip card brings back the 11-08 stale-price incident by a new route. Five more findings are major: two
claims in the brief are false as stated, and three card or log behaviours mislead.

Scope: `nba/jarvis/cards/*` (new), edits to `nba/jarvis/{cli,config,live,runner}.py`, `configs/jarvis.yaml`,
`docs/JARVIS_DISCORD_SETUP.md`, `tests/jarvis/cards/*`, `tests/jarvis/conftest.py`, one DECISIONS row. Worktree
`/Users/devin/Downloads/nba-prediction/.claude/worktrees/agent-ad54c5a5c4dd6e942`. I loaded no season 2025 data, sent nothing to
Discord, and used scripted HTTP stand-ins for every call. I did not run the replay oracle (the main session owns it).

## Most likely failure if "delivery only" is wrong

The card's pre-tip "Top overs by EV after fees" is a **new selection surface**. It ranks every Yes-side prop with edge > 0
straight from `single_candidate` and leaves out JARVIS's own caps: a stale Pinnacle reference, a missing P(plays), Pinnacle newer
than the price. It also skips the 11-08 preview hold. So the card can headline exactly the plays the slate preview now holds back,
and it logs them at that price as "card picks".

## Findings (severity, evidence, exact fix)

### BLOCKER B1: the card pass can take over the tick (failure isolation)
- **Evidence.** `RateGate` limits only rate-limit *sleeps* (`discord.py:56-64`, `rate_budget_s: 20`). Time spent waiting on
  transport is not counted. Each request can take up to the httpx timeout (`discord.py:94,99`, `timeout_s=15.0`, applied to each
  phase). A transport error marks the slot `deferred` (`discord.py:131-132`), but the loop goes on to the next slot (`run.py:355-396`).
  Nothing stops the pass after the first failure, and the next tick retries every slot that has no record.
- **Attack.** Script `t2/test_attack_budget.py::test_rate_budget_does_not_bound_transport_time`, with `rate_budget_s=0.1` and every
  request sleeping 0.5 s: wall time 2.28 s for 4 requests, {'deferred': 4}. Script `t2/test_attack_delivery.py::test_timeout_storm_no_breaker`
  (every request raises `httpx.ReadTimeout`): 4 requests on tick 1 and 4 again on tick 2, with no breaker. Both pass, exit 0.
- **Worst case on a 15-game night.** Before the first posts succeed, every game inside `post_lead_min` (120) or already live has
  no record, so up to 30 slots × 1 request × 15 s = **about 450 s**. Add up to 20 s of rate-limit budget and about 10-20 s of
  state-file scans late in the season (M7): **about 470-490 s per tick, against a 120 s cadence**. launchd does not overlap
  `watch --once`, so the next tick slips by about 6 minutes. The in-game pass runs after `_cards` (`runner.py:430-436`) and waits
  the full time. Alerts already sent in that tick are safe, but the next tick's alerts and halftime check-ins are late. Halftime is
  about 15 minutes, and Devin enters at halftime.
- **Fix.**
  1. Give `run_cards` one wall-clock deadline that covers transport time, for example `team_cards.tick_budget_s: 20`. Check it
     before every request in `CardClient._send` and defer when it has passed.
  2. Open a circuit for the rest of the tick after the first transport error or 5xx: return `deferred` for every remaining slot.
  3. `httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0))`.
  4. Move `rep["cards"] = _cards(...)` after `run_ingame` in `runner.py:_tick`.
  5. Add a test: a transport that sleeps, with the pass held under the deadline.

### BLOCKER B2: the pre-tip card skips JARVIS's caps and the 11-08 stale-reference hold
- **Evidence.** `facts.py:74-90` keeps every play with `edge_c > 0` and never reads `p.label`, `p.notes` or `p.ref_age_min`
  against the pulls gate.
  - `single_candidate` caps a play at thin when the Pinnacle reference is more than 120 min old, when Pinnacle is newer than the
    price, or when P(plays) is missing (`slate.py:173-213`).
  - When P(plays) is missing it prices `fair = p_over × 1.0`, which treats the player as certain to play.
  - The card shows none of these notes (`text.py:106-112` prints only an age).
  - `_hold_for_preview` (`runner.py:545-575`) leaves stale-reference plays out of the preview (DECISIONS 2026-11-08 21:42). The
    card has no such hold.
  - Cards post at T-120, before the T-90 preview prefetch, so a stale reference is the likely case there.
- **Fix.** In `pregame_overs`:
  - drop plays whose reference is older than the pulls gate limit, reusing `_gate(rt, now, "hold_stale_slate")` (pass the result
    in through `Deps`);
  - drop or label plays capped for missing P(plays) or for Pinnacle being newer;
  - print the label and the first cap note on the over's "why" line.
  - Add a test: a stale-reference over never appears on a card and is never logged to `card_picks.jsonl`.

### MAJOR M1: the claim "halftime Enter/Skip equals `halftime_overs.top_overs`" is false for multi-row games
- **Evidence.** `_verdict` (`facts.py:173-196`) applies the per-row edge and book filter. It does not apply `top_overs`'
  best-strike-per-(player, stat) rule or its `top_n` cutoff. `half_legs` returns up to 4 legs (`facts.py:205,251`).
- **Attack.** Script `test_attack_enter.py`, 2 passed, exit 0:
  - Four qualifying rows with the pre-tip over 4th best: `top_overs` lists A/B/C, while the card says **Enter** on all four,
    LeBron 25+ included.
  - Rows for LeBron 20+ (edge 0.15) and 25+ (edge 0.06): `top_overs` lists 20+ only, while the card says **Enter** on both.
- **Why the agent's test missed it.** `test_the_verdict_is_exactly_the_scorers_display_rule_over_a_grid` (test_facts_recap_safety.py:88-95)
  passes exactly one row per call, so neither the cutoff nor the strike rule can show up.
- **Fix.** In `half_legs`, compute `listed = {(norm_name(r["player_name"]), r["stat"], int(r["k"])) for r in HO.top_overs(...)}`
  once. A leg is `enter` only if its key is in `listed`; otherwise it is `skip` with "not among the scorer's top N" or "a better
  strike for this player clears the bar". Add multi-row tests for both cases.

### MAJOR M2: the pre-tip card flips to "keep your money" when quotes go stale, while the pick stays logged
- **Evidence.** `pregame_overs` reads `st.tradable(cfg)`. That drops quotes older than `max_quote_age_min` (30) and every quote
  within `min_minutes_to_tip` (3). Empty overs render as `NO_OVER` (`text.py:132-133`). The next refresh edits the card
  (`need_edit`, `run.py:187-194`).
- **Attack.** Script `t2/test_attack_fliptip.py`, 1 passed, exit 0. Posted at NOW: "LeBron James 25+ pts — ask 44c ... EV +0.12".
  At NOW+35 min the card is edited to "No over is worth it tonight — keep your money." The pick stays in the log and comes back at
  halftime "as shown pre-tip". "Keep your money" there is a statement about price freshness posing as a statement about EV.
- **Fix.**
  - In `build_card`, when `phase == "pretip"` and `_priced == 0`, do not replace the overs. Keep the logged ones, frozen
    (`overs_from_picks`), with the note "no fresh price right now".
  - Show `NO_OVER` only when `priced > 0` and no over has edge > 0.
  - Add a test for the 30-min quote age-out and for the T-3 cutoff.

### MAJOR M3: idempotence breaks on a torn line or a malformed record
- **Torn line.** `_append` (`state.py:28-34`) appends straight after a torn last line, so the new record is glued to it and lost
  on read.
  - Attack `test_torn_last_line_swallows_next_record`: after tick 1 the record is `None`, and 2 POSTs go out over 2 ticks, so the
    channel gets a **duplicate card**.
  - The same glue on `card_picks.jsonl` drops a pick, and `add_pick` then logs it again at a **later price**. That breaks the
    "price first shown" known-at promise.
  - The agent's test `test_a_pick_is_kept_at_the_price_first_shown_and_a_torn_line_is_skipped` checks only that the torn line is
    skipped, not the append that follows it.
  - **Fix.** In `_append`, if the file is non-empty and its last byte is not `\n`, write `\n` first.
- **Malformed record.** A valid JSON line without `message_id` raises `KeyError` in `CardState.latest` (`state.py:79`). The call
  comes from `_quiet` (`run.py:368`), which sits outside the per-game `try` (`run.py:372`), so every card dies for the rest of the
  night. The tick survives through `runner._cards`.
  - Attack `test_malformed_record_kills_all_cards`: raised `KeyError`.
  - **Fix.** `latest()` skips records that lack any required key, and the `_quiet` call moves inside the `try`.

### MAJOR M4: the season recap publishes a performance claim no frozen rule covers
- `season_line` (`text.py:283-298`) prints "Mean ±x units per over, 95% CI ..." once 30 overs have settled over 14 dates
  (`configs/parlay.yaml:42-43`). On busy nights that threshold falls within about two weeks.
  - That is an ROI claim with a CI, on a population no frozen rule covers (top-3 Yes overs per game with edge > 0, Kalshi price
    first shown). It is posted to a channel others can read.
  - CLAUDE.md: "a result becomes a claim only through a frozen test".
  - It is also a second "JARVIS record" next to the paper book's morning recap, built on a different population.
  - Its CI comes from `nba.jarvis.book.cluster_ci`, not `truth/bootstrap.py`. That predates this branch but is now used here too.
- No overlap with frozen populations:
  - BEST_PRICE_FORWARD (5ac0fa2f) scores production-model picks at the best U1 price.
  - The HALFTIME_OVERS draft scores scorer rows, which the cards only read.
  - Grep: only `nba/jarvis/cards/state.py` reads `card_picks.jsonl`, so it is not a scoring backdoor today.
- **Fix (Devin's call).** Either print n, hit/miss and units with "descriptive; no rule tests this", with no CI, or write a
  prereg first. Add a DECISIONS line that `card_picks.jsonl` is never an input to a frozen rule, the kill switch or model weights.

### MAJOR M5: the "How sure" wording can be misread
- `confidence_words` and `_sure_line` (`text.py:80-95,223-245`) print "58%, 4 pts from the sharp line: moderate range". On a
  points prop ("25+ pts"), "4 pts" reads as four basketball points. "Narrow/wide range" under the heading **How sure** reads as an
  uncertainty interval. It is actually agreement between our model and Pinnacle, and none is stored for props.
- **Fix.**
  - Replace "pts" with "percentage points".
  - Rename the heading "**Model vs sharp line**".
  - Use the words "close / some gap / far apart" and add "(agreement, not a confidence interval)".
  - Update `test_the_model_range_words_follow_the_configured_gap`.

### MAJOR M6 (flag for Devin): the "keep your money" override reaches beyond the cards
- `docs/TRADING_CONTEXT.md:22` says "Always a play, never 'keep your money'". The DECISIONS row scopes the override to "card overs
  only".
- But `_overview` (`run.py:419`) posts `T.NO_OVER` to the **main channel** when no over has EV. So the override also changes
  main-channel text.
- **Needs Devin.**
  - Option (a) keep the override and also edit TRADING_CONTEXT.
  - Option (b) on the overview, say "no over with positive EV after fees" and leave out "keep your money".
  - Option (c) drop the override.
  - Safe default: (b).

### MAJOR M7: scanning the state file slows every tick as the season grows
- `CardState.latest` and `picks` parse the whole file on every call (`state.py:70-101`).
- Synthetic season-size file (14,760 state lines, 7,380 pick lines): `latest()` takes 0.210 s and `picks()` 0.060 s per call.
- About 4-6 `latest()` calls per game plus several `picks()` calls per pass gives roughly 10-20 s per tick by spring. No budget
  counts this time.
- **Fix.** Read each file once per pass into an index held by `CardState`, or split the files by slate (`cards_state/<date>.jsonl`).

### Minor
- m1. **Duplicate card when Discord stores a POST but the response is lost.**
  - Attack `test_duplicate_post_when_response_lost`: 2 POSTs to #lal.
  - A 200 response with no `id` re-posts on every tick: `test_ok_without_message_id_reposts_every_tick`, 5 POSTs in 5 ticks.
  - **Fix.** On `failed_no_message_id`, record a sentinel that stops retries for that slot that night. Count lost-response
    duplicates in `tick_line`; Discord webhooks have no idempotency key.
- m2. `ev_phrase` (`text.py:99`) divides by `ask`, not `ask + fee`. "+27%" should read +26% at 44c plus 2c, and it disagrees with
  the units math (`recap.py:27`). **Fix:** `row.edge_c / (row.ask*100 + row.fee_c)`.
- m3. `settled_line` (`text.py:257`) prints "this one hit; it was still a +27% bet". "Still" only makes sense for a negative-EV
  bet, and every shown over has positive EV. **Fix:** "a +27% EV bet when shown".
- m4. `why_line` (`text.py:111`) says "the sharp line (Pinnacle, no vig) puts fair at {row.fair}", but `fair` is the *blended*
  fair (`slate.py:193-196`). It is correct only while every model weight is 0, which holds in `data/jarvis/model_weights.jsonl` on
  2026-11-09. **Fix:** print `row.pin`, and print `row.fair` separately as "JARVIS fair" when they differ.
- m5. "Kalshi asks" is hard-coded (`text.py:107`), but `props_by_venue` allows Polymarket Yes props. **Fix:** use `row.venue`.
- m6. A non-httpx exception on one team's slot skips the other team's card for the same game. In
  `test_secret_in_foreign_exception`, 2 errors cost 4 slots. **Fix:** wrap each `deliver_one` in its own `try`.
- m7. A deleted webhook gives a 404 on POST, which is retried every tick all night: 12 requests in 3 ticks across 4 slots. **Fix:**
  stop after 3 `gone` results per slot per night, and flag it in `tick_line`.
- m8. `discord.py:151` treats `10008` as an HTTP status. It is a JSON error code. It does no harm; remove it.
- m9. With `keep_halftime_posts: false`, the main-channel halftime lines are dropped even when the card's halftime edit was
  deferred or failed, so neither channel carries them. **Fix:** skip only after `cards_state` shows a halftime record for both
  teams.
- m10. Setup doc: step 4A runs `rm ~/jarvis_team_webhooks.json` before step 5 says to load the file "BEFORE you delete it".
  **Fix:** reorder 4 and 5. Re-installing the plist from the template silently drops the team variable, and
  `{"skipped": "no team webhooks configured"}` puts no flag in `tick_line`. **Fix:** add `cards=off(no webhooks)` while
  `enabled: true`.
- m11. Tests that need adding: the grid test with several rows (M1); the "main channel unchanged" check
  (`test_the_tick_never_adds_an_alert_condition`), which covers only one pre-tip tick and should also run at halftime and at
  final; transport time (B1); torn append and malformed record (M3); the quote age-out flip (M2).

## The seven requested checks

| # | Check | Command / method | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | No change to what JARVIS decides or alerts | read `git diff main...6e9ee57 -- nba/jarvis/{runner,live,config,cli}.py`: `_cards` runs after alerts and `_halftime`; `_main_halftime_wanted` returns True unless enabled AND `keep_halftime_posts: false` AND both webhooks are set; `latest_scoreboard(until=None)` behaves as before; the code default is `{"enabled": False}`; `rt.ht_scored` is only stored | n/a | The only new main-channel post is the declared one-time overview (see M6) | PASS |
| 1b | Halftime Enter/Skip equals `top_overs` | `pytest -s scratch/cards_attack/test_attack_enter.py` | 2 constructed games | the card says Enter on 4 legs where `top_overs` lists 3; it says Enter on 2 strikes where `top_overs` lists 1 | **FAIL (M1)** |
| 1c | Main channel unchanged with cards on or off | agent test + code reading | 1 pre-tip tick | the overview is the only added post; halftime and final ticks are not tested | PASS (weak test) |
| 2 | Secrets | `pytest -s scratch/cards_attack/t2/test_attack_delivery.py` (URL planted in an httpx exception, a `RuntimeError`, and bad env JSON; logs captured at DEBUG; report, `tick_line`, and every `.jsonl/.json/.html` under tmp checked) | 3 attacks | no secret in any artifact; the fingerprint is the first 12 hex of sha256(url) | PASS |
| 3 | Failure isolation | `t2/test_attack_budget.py`, `test_timeout_storm_no_breaker`, 404 and 5xx scripts | 4 slots per tick | 429s stay within 20 s; transport time is not limited; worst case is about 470-490 s per tick against 120 s | **FAIL (B1)** |
| 4 | Idempotence | torn line, malformed record, lost response, 200 with no id, rotation and delete (agent tests) | 1-5 ticks | duplicate posts; a pick re-logged at a later price; `KeyError` stops every card for the night | **FAIL (M3, m1)** |
| 5 | Honest text | read `text.py` and `facts.py`; `t2/test_attack_fliptip.py` | 1 card | "pts" ambiguous; "range" reads as an interval; the "keep your money" flip; caps hidden; % and "still" wording | **FAIL (B2, M2, M5, m2-m5)** |
| 6 | `card_picks.jsonl` known-at time and backdoor | read `state.py`, `run.py:389-391`; grep for readers | n/a | stamped before tip; first record wins; never re-written except by the torn-line path (M3); no other reader; no frozen population overlaps; but the season CI is a claim with no rule (M4) | WOUNDED |
| 7 | Embed limits and the 6000-char trim | read `embed.py:51-82` and the agent's limit tests | n/a | sound: the shrink loop's floor is about 850 characters, under 6000; not verified whether Discord counts code points or UTF-16 units for emoji | PASS |

Verification on the branch (worktree `agent-ad54c5a5c4dd6e942`):
- `ruff check nba/jarvis tests/jarvis`: exit 0
- `ruff format --check`: exit 0
- `mypy nba/jarvis`: exit 0
- `pytest --no-cov tests/jarvis/cards`: 92 passed, exit 0
- `pytest tests/jarvis`: 393 passed, 1 skipped, but I mistakenly ran this one through `grep`, so its exit code is masked. Rerun it
  unpiped before the merge.

Attack scripts live under
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/cards_attack/`.
Every attack ran with `cd <worktree> && .venv/bin/python -m pytest -q -s -p no:cacheprovider <script>`, exit 0, except the first
draft of the flip script. That draft failed because the fixture quotes were already stale at T-20, which confirmed the same
mechanism; the second draft is the one reported.

## Statistical note (family and power)
This change makes no statistical claim, so there is no family to correct and no power question. The only statistic is the card's
season CI (M4). It has no pre-registered family, and its threshold of 30 settled over 14 dates is a gate for printing, not a power
analysis.

## Playbook check
- **Model class:** none. This is delivery and formatting over existing outputs: `single_candidate` (Pinnacle no-vig fair with
  model weight w = 0) and the halftime scorer (T508 arm M). There is no new model, fit or feature.
- **Information or capacity?** Neither. The card adds no information and no capacity. It does *remove* information JARVIS has
  (cap notes, labels, the stale-reference hold); see B2.
- **Enough data for this class?** Not applicable. No training.
- **Test validity:** the agent's equality test is single-row and so cannot catch the multi-row divergence (M1). The "unchanged
  main channel" test covers one pre-tip tick. An invalid test is not a pass.
- **Lineage and inherited guards:** these come from the slate preview and halftime paths.
  - The 11-08 stale-reference hold: **dropped** (B2).
  - Thin caps: **dropped from display** (B2).
  - The `top_overs` display rule: **partly carried** (M1).
  - URL redaction and httpx logger pinning: carried.
  - Silent flag, no mentions: carried.
- **Scored against:** the card's units record is scored against the Kalshi ask plus fee when first shown, with no market
  comparator and no rule (M4).

## Recommended changes (not applied by me)
Before the merge: B1 and B2, then M1-M3 and M5. Devin decides M4 and M6. Next, re-review the revision for new contradictions,
then the main session runs the replay oracle (live path).

## Drafted rows (not appended)
- DECISIONS draft: "2026-11-10 | Team cards review (docs/reviews/jarvis_team_cards_2026-11-10.md) WOUNDED/BLOCKED: transport time
  can stretch the tick to about 8 min on a 15-game night (B1); pre-tip card overs skip the 11-08 stale-reference hold and the thin
  caps (B2); card Enter ≠ top_overs on multi-row games (M1). Fix before merge. card_picks.jsonl is descriptive only: never an input
  to a frozen rule, the kill switch or model weights. Reverse: none; a re-review closes it."
- PREDICTIONS_LEDGER: none. No prior was stated before the result.
