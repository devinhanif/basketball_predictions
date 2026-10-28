# Adversary review: JARVIS prop sides per venue (Kalshi No open, capped at thin) (2026-10-27)

Branch `agent/market-jarvis-props-sides` at 498da78 (on top of `agent/market-jarvis-halftime-scorer` 432c2df, already
reviewed). Worktree `/Users/devin/Downloads/nba-wt-props-sides`, data/ symlinked to main and only read. No Discord, no
launchd, no 2025 data, no write connection. Rule implemented: DECISIONS 064aaa5 (Devin named Kalshi for prop No).

Verdict: BLOCKED

(BLOCKED here means NOT_READY. One major defect, a small fix: the `pick` path calls a Kalshi prop No "a strong edge" while
the slate caps the same bet at thin and says why. Everything else the brief asked about passes.)

Top finding: the cap and the venue gate hold on every path that sizes or sends a play. On tonight's real POR@LAL state
the change adds 158 Kalshi prop-No candidates and no Polymarket ones. All 158 are `none` tonight, and the Yes play and
every Yes or game candidate are byte-identical to 432c2df. A planted cheap No is labelled thin, staked $2.69 and never
alerts. Two things fail: `pick` has no cap (M1), and production never multiplies the No's fair by P(plays) (m1). For a
No that second one does not change the sign of the edge, but the docs describe it wrongly.

## Scope of the diff

`git diff --stat 432c2df 498da78`: 8 files, +332/-30. They are configs/jarvis.yaml (only the `sides` block),
nba/jarvis/{config,edge,picks,slate}.py, docs/DAILY_PIPELINE.md, tests/jarvis/{test_slate,test_halftime_overs}.py. It
does not touch nba/truth, ops/, the nightly cap, the reserve, the ladder, the halftime scorer or its rule. The diff
stays within its task.

## Per-check table

All commands were run unpiped. `S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/56016700-50e4-4284-bfa6-be67cdcaa5af/scratchpad`.
Old and new code were extracted with `git archive 432c2df|498da78 nba configs | tar -x -C $S/old|new`. The dry runs use
a fixed clock of 2026-10-28 01:30 UTC (T-90 for POR@LAL, tip 03:00Z), `use_polymarket=False`, no LLM, a sender that
only records, and a scratch paper book (empty, or a copy of the live book). The book bytes were unchanged after each
run.

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Config fails closed | `python $S/attack_config.py <worktree> $S` | 35 variants | All of these are REFUSED: duplicate `sides`, duplicate `props`, duplicate venue (flow and block), typo keys (`prop`, `props_by_venues`, `Props`), `[no]` (default and per venue), strings `'yes'` and `'yes,no'`, null props / venue value / props_by_venue, `sides` as a list, `[]`, `[yes,yes]`, `YES`, `under`, venues `pinnacle` / `Kalshi` / `polymarket_us` / `True` / `1`, nested list, merge keys `<<:`. LOADED: shipped (kalshi `yes,no`, polymarket `yes`). Bare `sides:` loads as Yes only, which is the safe default. `[on, off]` and `[true, false]` load as yes/no (nit m3). | PASS |
| 2 | No Polymarket prop No reaches any output | `python $S/attack_paths.py $S/new $S` (a Polymarket prop quote planted at the cheap No's price) | 1 planted quote x 2 configs | Shipped config: single_candidate None, best_per_game / plan legs `kalshi no` only, best_arbitrage None, pick refused. Worst-case config (`polymarket: [yes,no]`): the slate still never offers it (hard skip slate.py:341, edge.py:186), but **pick logs it** (m4). Daily Dollar (dailydollar.py:149) and in-game (ingame_run.py:208) hard-code `"yes"`, and Polymarket quotes are `game_win` only (polymarket.py:165). | PASS (shipped) |
| 3a | Thin cap on slate / alerts | same script; a Kalshi prop with a Pinnacle ref planted so its No costs 43c vs fair 52.8c | 1 | label thin, +7.82c, `supports_alert` False. With `kalshi_prop_dnp_rule_verified=True` the same bet is strong / alert True, so the cap is what holds it, not a missing reference. The cap is set before the model blend (slate.py:163-167), so w>0 cannot lift it. | PASS |
| 3b | Thin cap on picks | same script, `build_pick(... ticker, side="no")` | 1 | Pick text: "+7.8c per $1 after the fee: **a strong edge**". Fair is unconditional (picks.py:98) and nothing is said about the DNP rule. | **FAIL (M1)** |
| 3c | Thin cap on in-game | code read | n/a | in-game has no No code path (ingame_run.py:189-218 builds Yes legs only) | PASS (vacuous) |
| 3d | Yes bit-identical | `for arm in old kalshi_no_closed shipped; for bm in empty copy: python $S/dryrun.py ...`, then a JSON compare | 180 tradable quotes, 179 Yes/game candidates | old vs Kalshi-No-closed: every field identical (texts, plays, candidates, Daily Dollar, notes) on both books. old vs shipped: the 179 Yes/game candidates are identical. The single difference in the Yes play is the note "best of 19 priced bets" becoming "best of 34", because the 15 measured No candidates now count in the selection. That is correct and by design. | PASS |
| 4 | Sizing | same script; `size_plays` on the planted No, 12 copies, and kill-switch level | 1 / 12 | thin No: $2.69 (6 contracts) and never more than the $3 thin ladder. 12 thin Nos: 2.69, 2.69, 2.69, 1.79, then $0 x8, sum $9.86, within the non-reserved $10 (nightly $20, reserve $10). Kill switch: $0.90 / 2 contracts. Config diff outside `sides`: none. | PASS |
| 5 | Halftime untouched | `git diff --exit-code --stat 432c2df 498da78 -- 'nba/markets/halftime_*' nba/jarvis/halftime_overs.py nba/jarvis/{dailydollar,ingame,ingame_run,runner,render,book}.py nba/truth` | n/a | exit 0, empty. The rule's home (docs/MARKET_FEATURE.md) is unchanged and docs/prereg is unchanged. The new test in test_halftime_overs.py only adds assertions. | PASS |
| 6 | DNP: P(plays) multiplied and stated | dry run plus code read | 15 measured No candidates tonight | **Not multiplied in production.** `ModelView` (forecasts.py:19-79) has no `p_play`, so `_p_play` (slate.py:240-242) always returns None and the No's fair is `1 - p_over`. The play text says so honestly: "prop DNP rule unverified and no P(plays): capped at thin" plus "prop No ... capped at thin". The multiplied path runs only with the test fake `StubModel.p_play = 0.97` (tests/jarvis/conftest.py:208). The doc claim at docs/DAILY_PIPELINE.md:1057 ("the fair price is Pinnacle's P(over \| plays) x our P(plays)") is false in production for both sides. This predates the branch. See m1 for why it does not flip the sign of a No's edge. | PARTIAL (m1) |
| - | Tests / lint | `.venv/bin/python -m pytest tests/jarvis tests/markets tests/test_layering.py --no-cov -q`, `ruff check nba/jarvis tests/jarvis`, `ruff format --check ...`, `mypy nba/jarvis` | 454 tests | 454 passed, exit 0; ruff exit 0; format exit 0; mypy exit 0 | PASS |

The six statistical checks (shuffled labels, planted future, missingness, knockout, replication, integer support) do not
apply. This diff is a side gate and a label cap. It makes no performance claim and has no score array to recompute. The
planted-quote attack (3a, 2) is the analogue of the planted-future check.

## Defects

**M1 (major): `pick` on a Kalshi prop No says "a strong edge" and does not mention the DNP rule.**
`nba/jarvis/picks.py:97-99` prices the No at `1 - ref.p_yes`, and `:118-122` picks the verdict without the cap the slate
applies at slate.py:163-167. The pick label is `pick`, so no cap, kill-switch or label record is affected. But this is
the line Devin reads before placing his own real bet, and it contradicts the slate on the same contract (slate: thin,
"capped"; pick: "strong"). Smallest fix, which leaves Yes picks byte-identical:
```python
capped = q.kind == "prop" and side == "no" and not cfg.kalshi_prop_dnp_rule_verified
...
else "a thin edge" if (edge or 0) < cfg.strong_cents or capped
...
if capped:
    lines.append("Kalshi's DNP rule is unverified: a prop No is held at thin (a DNP may settle it either way).")
```
Also add a test: a planted cheap Kalshi prop No pick shows "a thin edge" and the DNP line. Yes picks have the same
missing DNP adjustment, and that predates this branch. It is a separate follow-up because fixing it changes Yes output.

**m1 (minor, predates the branch, doc): P(plays) is never multiplied in production.** See check 6. For a No the
unmultiplied fair is safe on sign. A DNP either wins the No or voids it, and no plausible rule settles a DNP as Yes. Under a
void the true edge is P(plays) x the shown edge: smaller, same sign. So neither the thin/none label nor the max price
(edge >= 0) can be wrong for the No. For the Yes the unmultiplied fair can be optimistic if Kalshi settles a DNP as No, but
the Yes is also capped at thin. Fix, smallest first:
(a) correct docs/DAILY_PIPELINE.md:1057 to say "no P(plays) is available in production (ModelView has no p_play), so
the fair is Pinnacle's P(over | plays) and every prop label is capped at thin";
(b) add a test of a prop No under a model with no `p_play`, the production path. The current No-cap test uses StubModel
with 0.97.
Adding `ModelView.p_play` would change Yes fairs and labels. That needs its own decision, not this branch.

**m2 (minor): the slate's "Best play per game" list does not carry the DNP statement.** `render.py:119-129`
(`compact_line`) shows `[thin +x.xc]` only. A prop No that is the best play in a game other than the top one would be
listed without the cap note. Only the top play (`play_lines`, notes are its first two of four) and alerts carry it. Fix
without changing Yes text: in `slate_texts`, add one footer line when any listed play is a prop No: "prop No: Kalshi's
DNP rule is unverified, label held at thin".

**m3 (nit): `config.py:165` `_side` maps any YAML boolean**, so `[on, off]` and `[true, false]` load as `[yes, no]`.
The intent is still readable and it fails toward a list Devin typed. Leave it, or refuse bools whose source scalar is
not `yes`/`no`, which needs a resolver tweak.

**m4 (nit): the Polymarket rule disagrees between paths if Devin ever sets `polymarket: [yes, no]`.** The slate and
arbitrage hard-skip every Polymarket No (slate.py:341, edge.py:186), whose rationale is "the other team's token is the
same bet", true for winners only. `pick` (picks.py:81) relies on the config alone and would log a Polymarket prop No.
Shipped config refuses it. Fix: in picks, mirror the hard skip for `q.venue == "polymarket" and side == "no"`, or say
in the config comment that the Polymarket `no` entry affects nothing until the slate skip is lifted.

Info: tonight no prop No is offered at any label above none. 15 have a measured edge, all negative; the other 143 have
no Pinnacle line. The filter matters only on nights when a Kalshi No is cheap against Pinnacle. Play keys carry the
side (`...:no/thin`), so a No cannot collide with the Yes on dedupe or prior-stake. Paper-book settlement and CLV
already flip correctly for a No (book.py:527-528, 600-603), so the kill switch reads No bets correctly.

## Playbook check

Model class: none. This is a side gate and label cap on the live alert path (Pinnacle no-vig reference + venue fee),
not a fitted model. Of the playbook's cross-cutting questions: (1) information or capacity: neither; it opens a market
side and adds no information. (2) Data for the class: n/a, nothing is fitted. (3) Test validity: the No cap is
unit-tested only through a fake model that has `p_play`. Production has none, so the production path was tested here
by the planted-quote attack (thin, $2.69) and needs its own test (m1b). (4) Lineage and inherited guards: it inherits
the over-only review's guards (side gate in single_candidate, best_arbitrage, picks; Yes-only hard-codes in Daily
Dollar and in-game; Polymarket No skip). All still hold, except that picks lacks the new cap. (5) Scored against: the
kill switch's CLV on the paper book, which flips sides correctly for a No.

## The one biggest risk

The settlement rule. Until `kalshi_prop_dnp_rule_verified` is read, the only thing between a cheap-looking prop No and
a $5 strong stake is the `capped = True` at slate.py:163-167. Flipping the flag to true without reading the rule
would let the same planted bet go to strong / $4.92 / alert (check 3a, arm C). The flag should change only with a
DECISIONS row citing the Kalshi rulebook text.

## Not verified

- The real Kalshi DNP settlement rule (no network). The sign argument in m1 assumes a DNP wins or voids a No.
- The Polymarket US gateway (`use_polymarket=False` in the dry runs). It quotes winners only, by code read.
- The `ask`/agent LLM prose: there is no side guard on free text. It was not attacked; it was out of the brief and
  unchanged.
- The live tick (`run_tick`) end to end. It was covered through `plan_slate` and `supports_alert`, which it calls.

## Recommended changes (not applied)

1. M1 fix in picks.py with its test. This is the only change needed to merge.
2. m1(a) doc line and m1(b) production-path test.
3. m2 footer line. m3 and m4 at the steward's discretion.

## Drafted rows (not appended)

- DECISIONS (only if the main session merges after M1): "2026-10-27 JARVIS prop No opened on Kalshi only
  (props_by_venue), label capped at thin while the DNP rule is unread; review jarvis_props_sides_2026-10-27.md. Reverses
  if: Kalshi's DNP rule is read (lift the cap by a new row) or the CLV of prop-No bets is negative over the kill-switch
  window."

## Re-review: 2b0f0a2 vs 498da78 (2026-10-27)

Verdict: APPROVED

This supersedes the BLOCKED above. M1, m1, m2 and m4 are fixed and tested, and each new test except m1(b) fails on the
old code. Yes picks and game picks are byte-identical to 432c2df. In the dry run, Yes output is identical except for the
"best of N priced bets" count. Two minors predate the branch, and one wording nit is new. None of them blocks the merge.

Scope: `git diff --stat 498da78 2b0f0a2` touches 5 files (+242/-3): nba/jarvis/{picks,render}.py, docs/DAILY_PIPELINE.md
(one line), tests/jarvis/test_slate.py (+89) and this review. No configs, nba/truth, ops/, halftime or sizing. The diff
stays within its task.

| # | Check | Command (S = scratchpad) | n | Result | Pass |
|---|---|---|---|---|---|
| M1 | Kalshi prop No pick: thin + DNP line while unverified | `python -I $S/attack_paths.py $S/rr_new $S` (2b0f0a2 extracted with `git archive`), planted No at 48c vs fair 58.1c | 1 planted + 3 real | "+8.1c per $1 after the fee: **a thin edge**", then "Kalshi's DNP rule is unverified: a prop No is held at thin (a DNP may settle it either way)." With `kalshi_prop_dnp_rule_verified=True` the unit test gets "a strong edge" and no DNP line. picks.py:100, :124, :131-135 | PASS |
| M1 | Yes and game picks byte-identical | `python -I $S/rr_picks2.py $S/old|$S/rr_new ...; python -I $S/rr_picks_cmp.py` (quotes picked by sorted ticker) | 32 cases (2 configs x 8 markets x 2 sides) | 24/24 non-prop-No cases IDENTICAL (prop Yes cheap/real, 2 Kalshi game x 2 sides, 2 planted Polymarket game x 2 sides, Polymarket prop Yes). Only the 8 prop-No cases differ: they were refused before and are logged (Kalshi) or refused (Polymarket) now. exit 0 | PASS |
| m2 | Footer once, only when a listed play is a prop No | `test_slate_footer_names_the_cap_only_when_a_listed_play_is_a_prop_no` (test_slate.py:435); render.py:197-200 | 3 assertions | count == 1 with [yes, no]; absent with [yes]; absent when verified. Tonight's real slate has no footer (no prop No listed) | PASS |
| m4 | Polymarket prop No refused whatever the config; game picks unchanged | attack_paths (worst config `polymarket: [yes,no]`), test_slate.py:416, picks.py:81-83 | 2 configs | Refused under both configs ("player props are Over / Yes only on polymarket"). The slate still offers only `kalshi no`. Polymarket game picks (both sides) are byte-identical to 432c2df | PASS |
| m1a | Doc no longer claims P(plays) is multiplied | `grep -n "ModelView. has no" docs/DAILY_PIPELINE.md` and a read of slate.py:151-167 | n/a | DAILY_PIPELINE.md:1059 now matches the code: no p_play in production, so Yes and No are both capped at thin with "no P(plays)"; with a p_play model the value is multiplied and only the No stays capped. Attack arm B (p_play 0.90) confirms the No stays thin. | PASS |
| m1b | Production-path test (no p_play) | test_slate.py:376 | 1 | fair == 1 - p_over (unmultiplied), label thin, both notes; verified lifts to strong; the Yes is also thin | PASS |
| - | Mutation: new tests bite | 498da78 nba/configs + 2b0f0a2 tests in `$S/rr_mut`; `pytest tests/jarvis/test_slate.py -k "..." --no-cov` | 4 tests | 3 failed (M1, m4, m2), 1 passed (m1b covers behaviour that was already correct), exit 1 on the old code | PASS |
| 3d | Dry run, tonight, shipped config, vs 432c2df | `python -I $S/dryrun.py $S/old old {empty,copy}` and `$S/rr_new shipped {empty,copy}`, fixed clock 2026-10-28 01:30Z, then `python -I $S/rr_compare.py $S` | 180 tradable, 179 Yes/game cands, 158 prop-No cands | 179 Yes/game candidates identical on both books. texts / plays identical modulo "best of 19" -> "best of 34". daily_dollar, notes, send, sent_calls identical raw. The 158 prop Nos are all Kalshi and all `none`: 15 measured, 0 positive. No footer. Books unchanged. 2b0f0a2 output == the first pass's 498da78 output on every field. exit 0 | PASS |
| - | Tests / lint | `.venv/bin/python -m pytest tests/jarvis tests/markets tests/test_layering.py --no-cov -q`; `ruff check nba/jarvis tests/jarvis`; `ruff format --check nba/jarvis tests/jarvis`; `mypy nba/jarvis` | 458 tests | 458 passed, exit 0 (454 + 4 new); ruff 0; format 0; mypy 0 | PASS |

### New findings in the revision (none blocking)

**m5 (minor, predates the branch, now also reaches No picks): the pick's "Best ask now" compares across stats.**
picks.py:136-141 (`same = [...]`) filters game, kind, team, player and threshold but not `stat`. It is the same on
432c2df (line 129). On tonight's state a Ja Morant REB 4+ No pick at 48c printed "Best ask now: kalshi 18c (30c above
the best ask)". That 18c is the AST 4+ No. The Yes pick printed "kalshi 9c (44c above)", which is the 3PT 4+ Yes.
`$S/rr_same.py` lists the three matched quotes (ast, fg3m, reb). Devin reads this line before placing a real bet, and it
tells him he overpaid when he did not. Fix: add `and x.stat == q.stat` to the filter at picks.py:140, with a test of
two stats at one threshold. This changes Yes pick output, so it is a separate follow-up, not a condition of this merge.

**n1 (nit, new): a capped No priced below fair-or-worse prints "a fair price or worse" and then "held at thin".**
picks.py:131 appends the DNP line whenever `capped`, including when edge < 0. It reads as a contradiction. Fix: guard it
with `if capped and (edge or 0) >= 0:`, or reword it to "a prop No is never labelled above thin while Kalshi's DNP rule
is unverified".

**Info (predates the branch): the order of tradable quotes is not deterministic.** Two `gather` calls in one process
on 432c2df gave different orders of the same 180 quotes: the order sha differs and the sorted sha is the same
(`$S/rr_order.py`). It does not change tonight's output: every dump compared equal. Any tie-break that relies on input
order (two candidates at equal edge) could flip between runs. Worth a sort key in `gather` some day. Not this branch.

Still open from the first pass, unchanged by design: a Yes pick has no "no P(plays)" cap, so a cheap Yes pick can say
"strong" while the slate says thin. That is a separate follow-up because it changes Yes output. m3 (YAML bools) is
left as is.

### Playbook check (re-review)

Model class: none (a side gate and a label cap; no fit). (1) Information or capacity: neither. (2) Data for the
class: n/a. (3) Test validity: the production path (no p_play) is now tested directly, and the mutation run shows the
new tests fail on the old code. (4) Lineage and inherited guards: every guard from the over-only review still holds,
and picks now matches the slate on the cap and on the Polymarket No skip. (5) Scored against: the paper book's CLV via
the kill switch, unchanged.

### Biggest risk (unchanged)

The `kalshi_prop_dnp_rule_verified` flag. Setting it true lifts every prop-No cap at once: slate, alert and pick. It
should change only with a DECISIONS row that quotes Kalshi's rulebook text.

### Not verified

The real Kalshi DNP rule (no network). The Polymarket US gateway (`use_polymarket=False`). LLM free text. `run_tick`
end to end. The m2 footer on a real slate night where a prop No is listed (none tonight; covered by the unit test only).

### Drafted rows (not appended)

- DECISIONS: the row drafted above stands, now without the M1 condition.
- Follow-up queue (main session's choice): m5 (`x.stat == q.stat` in picks.py:140, changes Yes text); a Yes-pick
  "no P(plays)" cap; n1 wording; a deterministic quote order in `gather`.
