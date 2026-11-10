# Adversary review: unders on cards + Kalshi prop DNP rule verified (agent/market-cards-unders, 9c20207a vs main)

Date: 2026-11-10. Reviewer: adversary. Scope: configs/jarvis.yaml (`settlement.kalshi_prop_dnp_rule_verified: true`),
nba/jarvis/cards/{facts,model,players,preview,recap,run,text}.py, tests/jarvis/conftest.py,
tests/jarvis/cards/{test_unders,test_embed_text}.py (+361 / -30). Touches the LIVE path (JARVIS ticks every 2 min from main).
Devin's go is conditional on this review; his call: unders alert under the same 3c strong rule as overs.

**Grade: WOUNDED.** The code does what it says. The No is priced on the no-vig Pinnacle under. The Kalshi `N+` to
Pinnacle `over N-0.5` mapping is right, and so are settlement, pick ids and paper log. The problem is the alert
claim. All 3 unders that became alert-eligible were the sharp line running behind news that Kalshi had already
priced. The same holds for 14 of the 17 unders shown on the cards. One small fix stands between the branch and
merge: unders should be shown, but should not alert yet.

Verdict: BLOCKED

(Becomes APPROVED once M1 is in. M1 is about 4 lines and needs no re-measure; the cards still show the unders.
Devin can override M1, because the 3c rule for unders was his call. The evidence below says don't.)

## What the change is (model class)

This is not a model. It changes a pricing rule plus a display. With the flag true, `single_candidate`
(nba/jarvis/slate.py:173-189) prices every Kalshi prop as follows:

- Yes = Pinnacle no-vig P(over | plays).
- No = 1 - that, which is the no-vig P(under | plays).

The old pricing multiplied by P(plays) and capped every prop No at thin. Cards and player checkpoints now list
the No next to the overs. Lineage: the 10-27 DNP cap (Devin) and the 11-10 ModelView.p_play adapter (reviewed
WOUNDED -> APPROVED today).

## Checks

Scratch dir: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a234aace-5d9c-4658-9e4a-3b1d144ee734/scratchpad/adv_unders/`.
All runs are read-only, use seasons 2026 live stores as of the tick, and are run from this worktree at 9c20207a.

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Tests | `.venv-1/bin/python -m pytest tests/jarvis -q -p no:cacheprovider --no-cov` | 483 passed, 1 skipped | exit 0. With coverage on, exit 1 comes only from the repo-wide 70% gate on a subset run (6.87%), same as on main | pass |
| 2 | Lint / format / types | `ruff check nba/jarvis/cards tests/jarvis/cards tests/jarvis/conftest.py`; `ruff format --check` (same); `mypy` on the 7 touched modules | 27 files | exit 0 / 0 / 0 | pass |
| 3 | Q1: is the No fair the no-vig under? | read slate.py:171-189, sources.py:346-389 | n/a | `fair = 1 - p_over`, where `p_over` is `devig_pair(over, under)[0]` at point `threshold - 0.5`. So the No fair is the proportional no-vig Pinnacle under. It is not 1 - a DNP-discounted over. No P(plays) term. Weights are 0 for every stat, so `blend` is inert | pass |
| 4 | Q2: threshold mapping / off-by-one | `attack.py 2026-11-09`: raw Pinnacle rows for each alert vs Kalshi ticker; `settle_check.py` | 3 alerts, 2 settlement cases | Poeltl: Kalshi `REB-...-8` (8+) maps to Pinnacle o/u 7.5. No ask = 1 - yes_bid = 0.48. Fair = 0.574/(0.495+0.574) = 0.537. Edge = 0.537-0.48-0.02 = +3.71c, reproduced exactly. Card text "under 8 reb (Kalshi No)" means 7 or fewer, matching Kalshi's "fewer than 8". Settlement: stat 7 -> No won (+1.00 u at 48c+2c), stat 8 -> No lost | pass |
| 5 | Q2: are the 3 alert-eligible unders real gaps? | `attack.py`, `trail.py` (Pinnacle trail to tip), `inj2.py` (report status changes) | 3 | Builder's 3 reproduce (Fears ast 4+ +7.9c T-180, Queen pts 10+ +5.9c T-120, Poeltl reb 8+ +3.7c T-60). **All 3 were the sharp line lagging.** Fears: the ref was 58 min old (o3.5 0.60/0.469), and Pinnacle then moved the line to 4.5. Queen: the ref was 2 min old at o9.5, then the line moved to 12.5 by 22:58. Poeltl: Pinnacle re-quoted the identical o7.5 0.495/0.574 from 18:31 to 22:58 even though 3 TOR players went questionable -> out at 18:15 UTC (ids 1630567, 1630193, 1642867). At 23:43 it flipped to 0.595/0.474, so the close under is 0.443 vs the 0.50 paid (-5.7c CLV). NOP had 6 players ruled out (15:45-20:45 UTC) before the Fears/Queen ticks. All 3 Kalshi markets settled `yes` (all 3 No lost; n=3, an anecdote only) | **fail** |
| 6 | Q2: sharp drift after display (outcome-free) | `persist_ci.py 2026-11-04 .. 2026-11-09` (each unique ticker/side shown on a card, verified arm; Pinnacle last pre-tip line vs the ref used; game-clustered bootstrap, 4000 reps, seed 20261110, scratch code, not truth/bootstrap.py) | unders 17 (11 games); overs 42 (22 games) | Pinnacle later moved toward Kalshi, usually past it (102-255% of the gap): **unders 14/17 = 0.82 [0.68, 1.00]**, 0 moved away. Overs 12/42 = 0.29 [0.12, 0.47], 3 moved away, 27 flat (±1pp) | **fail (unders)** |
| 7 | Q3: other consumers of the flag | `grep -rn kalshi_prop_dnp_rule_verified nba` | 6 sites | slate.py:173 is the pricing for **every** consumer: main-channel slate preview (`best_per_game`), alerts, paper-book sizing, cards, checkpoints. picks.py:100 (manual pick verdict no longer capped). render.py:199 (thin note dropped). facts.py:109, players.py:346 (display). Parlay (`nba/parlay/analysis.py`) and the halftime scorer do not read it | see 8 |
| 8 | Q3: effect beyond what was measured | `bpg.py` (best_per_game before/after, T-120/T-60, 11-04..11-09) | 94 game-ticks | The main-channel "best play per game" changes on **68/94** game-ticks. Game-winner tops fall from 35 to 12, prop Yes tops rise from 41 to 57, and prop No tops rise from 18 to 25 (strong 0 -> 2, both lag cases above). Every one of these is paper-staked by `size_plays`. The builder's out.txt measured labels and cards, not the preview or paper stakes | disclosure (major) |
| 9 | Q4: settlement / recap / pick ids / paper log | read recap.py:55-131, book.py:451-520, facts.py:37-41, 284-322, state.py:140-149; `settle_check.py` | 2 | `side` flows pick -> settle row -> `OutcomeSource` (`not yes` for a No, both on the Kalshi `result` path and the box path). Units use the No ask and No fee. An over's pick id is unchanged and an under's id adds `side`. Old picks without `side` read as "yes". A DNP falls to `void` (not counted), which is safe under a last-fair-price settlement | pass |
| 10 | Q4: LINE_CAL_LIVE hygiene | read players.py:1-17, 443-455; run.py:161-176 | n/a | Unders are added to pre-tip displays only. Checkpoints are never edited after posting and never given outcomes. `tradable()` drops games inside `min_minutes_to_tip`, so no in-game No is priced. The halftime legs filter to `side == "yes"` | pass |
| 11 | Q5: tick safety | read run.py:161-187, preview.py:102-109, players.py:360-391 | n/a | New calls sit inside the existing per-card / per-checkpoint `try` blocks. `pregame_unders` / `priced_unders` repeat `single_candidate` over the game's tradable props, about 2x the card pricing work. Per-tick cost was not timed (the builder's 24-tick, 3-arm replay ran in 100 s) | pass (cost not timed) |
| 12 | Q5: do tests still exercise production config? | read conftest.py:191-205; `grep -rln cfg_verified tests` | 1 file | The shared `cfg` fixture now pins `kalshi_prop_dnp_rule_verified=False`. So the runner / alert / preview / picks / render tests run the path production no longer takes. Only test_unders.py (5 tests) uses `cfg_verified`. No test runs a tick or the slate preview with the verified config, and none settles a `side="no"` pick | minor (m3) |

The six result checks (shuffled labels, planted future, missingness, knockout, replication, integer support) do not
apply: the branch makes no model claim. Checks 5-6 are the "too good to be true" check for the alert claim. The
+3.7..+7.9c under edges are 2-4x every positive over in the same replay, so they were presumed artifacts until
explained. The explanation is sharp-line lag.

## Findings

**M1 (blocker for the alert part).** A prop No reaches `strong` and alerts through `single_candidate` at
nba/jarvis/slate.py:188-189 (the verified branch), and all of those alerts so far were sharp-line lag.

Why the lag hits unders: when teammates are ruled out, the remaining players' lines go up. Kalshi reprices
first. Pinnacle's prop line often keeps re-quoting the old number for hours. Its `requested_at` is fresh, so
`ref_age_min`, the pull-hold (30 min) and `max_ref_lead_min` all pass. Kalshi's Yes then looks rich and the No
looks cheap. The OUT filter (slate.py:108-127) removes only the injured player, not his teammates' props.

Overs are much less exposed, because the same news pushes their gap the other way.

Smallest fix, tonight: hold a prop No at thin even when the rule is verified. The No stays shown on cards and
checkpoints and stays priced on P(under | plays). In slate.py, replace line 189:

```python
        else:
            fair = p_over if side == "yes" else 1.0 - p_over
            if q.kind == "prop" and side == "no":
                capped = True  # display yes, alert no: the sharp prop line lags teammate news (cards_unders_2026-11-10)
                notes.append("prop No held at thin: the sharp prop line often lags news Kalshi has priced")
```

This note does not start with any of `facts.CAP_NOTES`, so `under_display_ok` still shows it. It needs a test
in test_unders.py: under `cfg_verified` the No is thin, `supports_alert` is False, and it is still listed by
`pregame_unders`.

The test that would lift M1 is a new rule, pre-registered on the live log: unders' Pinnacle CLV, together with a
"same-team availability changed after Pinnacle's last price change" hold. Do not tune it on these 17.

**M2 (major, disclosure; Devin should know before merge).** The flag is global: check 8 above. Showing unders on
cards is the smaller effect. The larger one is that the main-channel slate preview's best play per game changes
on 72% of game-ticks: prop plays replace game-winner plays, and the paper book stakes them. That follows
correctly from the verified rule, but it was neither measured nor stated in the merge request. No code fix; add
one line to the DECISIONS row.

**m1 (minor; latent, becomes major if a weight turns on).** `ModelView.p_yes` (forecasts.py:105) is
P(plays) x P(over | plays), with "DNP counts as a miss". With the rule verified, `_model_side` for a prop No is
`1 - p_yes`, which counts a DNP as a No **win**. The blend at slate.py:194-198 would then pull the No fair above
Pinnacle. Today w = 0 for every stat, but `load_weights` updates from CLV automatically. Fix: in `_model_side`,
when `q.kind == "prop" and cfg.kalshi_prop_dnp_rule_verified`, divide by `st.model.p_play(q)` (None if p_play
is None or 0), so the model side is conditional too.

**m2 (minor).** The edge ignores how a DNP actually settles. "Last fair price as determined by the Exchange" is
Kalshi's own price K, not Pinnacle's. So the true EV is roughly pp x shown_edge + (1-pp) x (K - ask - fee).

- For Poeltl (pp 0.915) that is about +3.1c, not the +3.71c shown.
- Card text "if he sits, it settles at the fair price" (text.py `why_line_under`, `UNDER_VERIFIED_SHORT`,
  players.py:608) should say "at Kalshi's last fair price".
- Leave the pricing for now and say it in the text.

**m3 (minor).** Tests: the `cfg` fixture now pins the non-production path (check 12). Add three things:

- one runner tick / slate-preview test under `cfg_verified` with a prop No;
- one `settle_pending` test with `side="no"` (stat at the threshold -> No loses);
- a test that a pre-existing pick without `side` still settles as an over.

**m4 (minor).** Stale comments:

- slate.py:109-110 ("Refused, not repriced: Kalshi's DNP settlement is unread");
- `facts.UNDER_NOTE` is unused;
- the docstring of `under_display_ok` says "never alerted", which is false under the verified rule;
- `players.snapshot` does not record unders, so checkpoint diffs never mention an under that moved.

**Unverified (stated, not chased).** A player who is active but never enters (DNP-CD) is neither "withdraws
prior to commencement" nor "played at least one second" in the quoted terms. It could settle on stats (an over
loses, an under wins) or at the last fair price. That risk was covered by the old P(plays) discount on overs and
is not covered now. It is small for the rotation players Kalshi lists.

## Playbook check

Class: pricing rule + display (market comparison against a sharp reference). It is not a fitted model.

1. **Information or capacity?** Information: the contract terms. Reading the rule is a real input. The under
   "edges" it unlocks are not information. They are the reference being late.
2. **Enough data?** For the alert claim, no: 3 alerts on one slate. For the drift test, 17 unders and 42 overs
   over 6 slates are enough to see the asymmetry (CIs above). They are not enough to estimate a CLV floor.
3. **Valid test?** As-of replay at real ticks, with the OUT gate and ModelView at the tick. The Kalshi `result
   IS NULL` filter is lifted for replay only. Drift is measured against the last pre-tip Pinnacle line, so it
   does not use outcomes. The builder's measurement counted labels but had no check that the gaps were real.
   That is the missing guard.
4. **Lineage.** This is the 10-27 prop No cap, lifted. The cap existed for DNP settlement. It was also, by
   accident, the only thing keeping lagged unders from alerting. The guard is now carried forward on purpose
   (M1) for a stated reason.
5. **What it scores against.** Pinnacle no-vig at a stale-but-fresh-looking timestamp. The right yardstick is
   the closing Pinnacle line (CLV), which these alerts lose (Poeltl -5.7c; Queen and Fears had lines moved by
   +3 and +1).

## Corrected family and power note

This review asks two questions:

- Are alert-eligible unders real? 3 of 3 failed.
- Is the shown-under drift asymmetric vs overs? 0.82 vs 0.29, with non-overlapping game-clustered CIs.

No BH is needed for a 2-test family with this separation. The scratch bootstrap is not `truth/bootstrap.py`.
Flag it if these numbers are ever cited as a result.

Power: the n=3 outcome record (0/3) has no power and is not used as evidence. The drift asymmetry is the evidence.

## Single biggest risk

Tonight, under the branch as written, a teammate ruled out ~1-2 h before tip produces a "strong" under alert on a
line Pinnacle has not moved. The model weight is 0, so nothing else checks it. That is the main-channel alert
Devin acts on.

## Recommended changes (not applied)

1. M1 (4 lines + 1 test) before merge.
2. Add M2's disclosure to the DECISIONS row.
3. m1 before any weight turns on.
4. m2 wording, m3 tests and m4 comments can follow.

## Drafted DECISIONS row (not appended)

`2026-11-10 | Kalshi prop DNP rule verified (pre-tip withdrawal -> last fair price; played >= 1 s -> stats); unders shown on cards and checkpoints, priced on P(under | plays); prop No held at thin (no alert) because the sharp prop line lagged teammate news on 3/3 alert-eligible unders and 14/17 shown unders (adversary cards_unders_2026-11-10). Flag also re-ranks the main-channel preview (68/94 game-ticks) and paper stakes. | Reverse: a pre-registered live-log rule shows unders' Pinnacle CLV >= 0 with a teammate-news hold.`
