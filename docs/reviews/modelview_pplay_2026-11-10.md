# Adversary review: ModelView.p_play (agent/market-modelview-pplay, 583293a4 vs main 91871588)

Date: 2026-11-10. Reviewer: adversary. Scope: nba/jarvis/forecasts.py, nba/jarvis/cards/players.py,
tests/jarvis/cards/test_players.py, tests/jarvis/test_model_view_pplay.py (+139 / -20). LIVE path
(JARVIS every 2 min from main). Devin's go to fix: DECISIONS 2026-11-10 12:47.

**Grade: WOUNDED.** The change does what it says, the builder's numbers reproduce under an honest
as-of replay, no label reaches strong and nothing becomes alert-eligible on six slates. Two small
must-fix items stand between it and merge: a new parse on the live path that can raise out of the
tick, and a safety claim in the docstring that the data path does not keep.

Verdict: BLOCKED

(Becomes APPROVED once M1 and M2 below are in; both are a few lines; no re-measure needed.)

## What the change is (model class)

Not a model. It is a pricing adapter: the stored forward P(plays) from `props_context_residual`
(a gradient-boosted P(play) head, MODEL_PLAYBOOK "Gradient-boosted trees") is handed to
`single_candidate`, which since 10-27 prices a Kalshi prop Yes as P(plays) x Pinnacle P(over | plays)
while Kalshi's DNP rule is unverified, and caps the play at thin when no P(plays) exists.

## Checks

| # | Check | Command (scratch: /private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/a234aace-5d9c-4658-9e4a-3b1d144ee734/scratchpad/adv_pp/) | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Tests | `.venv-1/bin/python -m pytest tests/jarvis -q` | 475 passed, 1 skipped | exit 1 is only the repo-wide 70% coverage gate on a subset run (6.79%), same as on main | pass |
| 2 | Lint/format/types | `ruff check` (4 files), `ruff format --check`, `mypy` forecasts.py players.py | 4 files | exit 0 / 0 / 0 | pass |
| 3 | Builder's numbers, honest replay | `measure_asof.py` (ModelView rebuilt per tick with `load_slate(as_of=tick)`; OUT gate at the tick) on 11-04..11-09, ticks T-180/-120/-60/-30 of first tip | 6 slates, 33,946 prop quote-ticks | strong 0 before / 0 after; alert-eligible 0 / 0; thin 315 -> 68 (11-04 93->17, 11-05 17->6, 11-06 47->7, 11-07 75->18, 11-08 46->12, 11-09 37->8); overs shown on cards 0 -> 8 (priced 0 -> 1,194) | pass (reproduces) |
| 4 | Known-at: tip guard can silently skip? | read toolbox.py:61-68, 146-181; parlay/slate.py:31-42, 185-186 | n/a | `_Slate.made_at` is a dataclass field (always present) and `SlateInfo.tipoff[gid]` is set for every ctx game; the getattr defaults only matter for test doubles. Live rows are all <= now; 0 of 99,348 forward rows have made_at > tipoff | pass |
| 5 | Known-at: OUT gate timezone | out_gate.py:40-44, 77-78; parlay/slate.py:188-190 | n/a | serve time is naive UTC converted to naive ET before comparing with `player_availability.as_of` (the M2 fix of 11-08); `p_play` reads the same `out_on_report` as `p_yes` | pass |
| 6 | Consistency p_yes vs p_play | read forecasts.py:88-129, slate.py:170-196; live weights `data/jarvis/model_weights.jsonl` | 6 stats | Both read `ctx.p_play`. They diverge only when p_play returns None (post-tip row, out-of-range value), and None caps the play at thin: the safe direction. `blend` is inert today (w = 0 for every stat, last update 11-09) | pass |
| 7 | Stale P(plays) exposure | `stale.py 60`, `stale.py 30`, `doubtful.py 60/30` | 1,541 players with a row at T-60; 1,542 at T-30 | T-60: 11 players (0.7%) priced from a row (p_play 0.66-0.98) made before a later questionable/doubtful report; 2 doubtful (p_play 0.90, 0.98). Only 3 had a Pinnacle-referenced Yes; all priced negative (-3.1c, -5.0c, -3.9c, label none). Both doubtful players had no Pinnacle prop (pulled). T-30: 3 such players; 0 positive | pass, with a standing risk (below) |
| 8 | Player-checkpoint pass unchanged | diff of `_WithPPlay` -> `PPlayView`; stored p_play range in DB | 28,920 rows | Only differences: PPlayView also requires kind == "prop" and a value in (0, 1] and finite. Stored values span 0.5006..0.99 (0 outside), and checkpoint quotes are props, so behaviour on real data is identical to 2668f482. The checkpoint tests pass unchanged except the rename | pass |
| 9 | Tick safety | `cost.py` (live 11-10 view) | 950 prop quotes | p_play costs 0.045 ms per call (0.04 s per tick). Planting `made_at = "None"` raises `ValueError: Invalid isoformat string` out of `p_play`; `plan_slate` in `_tick` (runner.py:372) and `run_slate` (runner.py:236) is not inside a try | **fail (M1)** |
| 10 | "never a default of 1.0" | parlay/slate.py:168 | 28,920 rows since 10-20 lack p_play: 0 | `load_slate` fills `ctx.p_play[pid] = float(d.get("p_play", 1.0))`, so a row written without p_play reaches `stored_p_play` as 1.0 and prices the prop as a sure starter, the exact case the docstring says cannot happen. Latent today | **fail (M2)** |
| 11 | New test vacuous? | `test_vacuous.py` (pytest with the jarvis conftest) | 1 | fixture gives pin_prob 0.298, so the assertion runs; but it is guarded by `if ... is not None` and would pass silently if the fixture lost its Pinnacle line | minor |

Shuffled labels / planted future / missingness / knockout / integer support (the six result checks)
are not applicable: no result or claim is made; this is a live-path behaviour change. The planted-
future analogue is check 4 (post-tip row refused: covered by the new test, and no such row exists)
and check 9 (planted bad timestamp: raises).

## Builder's measurement: right numbers, flawed replay

`measure.py` built one `ModelView` per day with no `as_of`, so a T-180 tick saw forward rows made
later that evening and an OUT gate evaluated at today's clock (11-10). The as-of rerun moves the
counts only slightly (11-04 thin after 20 -> 17; 11-06 9 -> 7; overs shown identical on 11-04 and
11-06) and changes no conclusion. The builder also stopped at 11-08 although the claim says
11-04..11-09; 11-09 is added above (thin 37 -> 8, 2 overs shown, 0 alert-eligible).
"At most 1 over on 5 game-ticks" was in fact 6 game-ticks on 11-04..11-08, plus 2 on 11-09.

## Must-fix (smallest fix each)

- **M1 (major; tick safety). nba/jarvis/forecasts.py:126-128.** `_naive(made)` parses a stored
  string (`str(made_at)`); a NULL `made_at` (the column is nullable, nba/daily/store.py:25) becomes
  `"None"` and raises out of `p_play` -> `single_candidate` -> `plan_slate` -> `_tick` (runner.py:372) and `run_slate` (236), which
  is unguarded: every tick fails while that row exists (the 10-28 every-tick crash shape). JARVIS
  never parsed `made_at` before this branch. Fix, fail closed:
  ```python
  try:
      late = _naive(made) > tip
  except (TypeError, ValueError):
      return None  # an unreadable stamp caps the prop
  if late:
      return None
  ```
  plus one test that plants `made = {(G1, LEBRON, "pts"): "None"}` and asserts `None`.

- **M2 (minor in effect, must-fix as a claim). nba/jarvis/forecasts.py:110-129 (and the
  `stored_p_play` docstring, 21-23).** `ctx.p_play` already carries a 1.0 default from
  nba/parlay/slate.py:168 when a row has no `p_play`, so "never a default of 1.0" is not true end to
  end. Smallest JARVIS-only fix: before `stored_p_play`, require the quote's own row to carry the
  number:
  ```python
  blob = getattr(sl, "props_raw", {}).get((q.game_id, q.player_id, str(q.stat)))
  if blob is None or blob.get("p_play") is None:
      return None
  ```
  (also ties the number to the quote's stat row) and one test with a blob lacking `p_play`.
  If the builder prefers not to, the alternative is to correct both docstrings to say the default
  lives upstream; but then the guard the docstring advertises does not exist.

## Should-fix (not blocking)

- tests/jarvis/test_model_view_pplay.py:77: replace `if with_p.pin_prob is not None and ...` with
  `assert with_p.pin_prob is not None and without.pin_prob is not None` so the test cannot pass
  vacuously.
- No test drives `ModelView.p_play` through `single_candidate` (only `PPlayView`). One test with
  `mv._sl` stubbed and `replace(st, model=mv)` would cover the production wiring.

## The single biggest risk

A stale P(plays) on a player downgraded after the last forward refresh, with Pinnacle still
quoting his prop. Before this branch such a prop was capped at thin; now a stored 0.9-0.98 against
a market that has priced in the downgrade can show a positive EV and, at >= 3c, alert. In six slates
it did not happen (Pinnacle pulled both doubtful players' props; the three questionable ones priced
negative), but n is 11 player-ticks, too small to bound the rate. The OUT gate covers OUT only, by
design (DECISIONS 11-08). The cleanest mitigation, if Devin wants one, is a display note or a thin
cap when the newest report row known now is later than the forward row and says doubtful or
questionable; that is a new decision, not a fix to this branch.

## What else changes (item 4)

- Labels: prop Yes plays are no longer capped by the DNP rule when a P(plays) exists; with the same
  thresholds (`strong_cents` 3.0) they can now be strong. Measured: none were.
- Prop No: still capped at thin; its fair is now (1 - P(over)) x P(plays) instead of 1 - P(over)
  (a DNP treated as a No loss: conservative). Fewer thin Nos as a result.
- Fees, thresholds, alert gate (`supports_alert`), main-channel posting logic: untouched.
- Populations: the JARVIS paper book and the slate preview now carry about 80% fewer thin prop
  plays, and `pin_prob` for a prop Yes now includes P(plays). The weight learner's `_leaning_rows`
  (model_prob > pin_prob) will mix the two definitions across 11-10; w is 0 for every stat, so no
  effect today. No frozen rule scores the JARVIS paper book (same note as the 11-08 OUT-gap row).
- Player checkpoints: identical on real data (check 8).

## Playbook check

1. Information or capacity: information (a stored, pre-tip P(plays) already computed nightly and
   at intraday refreshes; nothing new is fitted).
2. Enough data for this class: n/a (no fit). The P(play) head itself was validated elsewhere.
3. Test validity: the builder's replay was not as-of (see above); the as-of rerun is the valid one
   and agrees. Six slates is a behaviour check, not an edge claim; no claim of skill is made.
4. Lineage and inherited guards: inherits the OUT-on-report gate (ET-correct) and the 10-27 DNP
   pricing rule; adds a post-tip refusal that is dead in live (0 post-tip rows) but harmless. Does
   not inherit any doubtful/questionable guard (none exists).
5. Scored against: nothing is scored; the Kalshi ask after fee vs Pinnacle no-vig x P(plays) is a
   price comparison. Class check (GBDT P(play)): stored values are bounded 0.50-0.99 because the
   forward store writes no row under 0.5; a player below that has no row and stays capped.

## Not verified

- The live JARVIS process end to end (no Discord, no tick run); only gather/single_candidate/
  pregame_overs in-process.
- Behaviour on a slate where Pinnacle keeps a prop for a doubtful player (none in the sample).
- Whether `st.tradable` excludes started games for every path that reaches `single_candidate`
  (the post-tip guard assumes it matters only rarely).
- Kalshi's DNP settlement rule (Devin's read; unchanged).

## Drafted rows (not appended)

DECISIONS (for the main session, after M1/M2 and merge):
`| 2026-11-10 hh:mm | MERGED agent/market-modelview-pplay <sha>: production ModelView gains p_play (stored forward P(plays); None when no row/OUT on a report known now/row made after tip/unreadable stamp/row without p_play); player checkpoints use the same lookup (PPlayView). Measured as-of on 11-04..11-09: strong 0 -> 0, alert-eligible 0 -> 0, thin prop plays 315 -> 68, overs on cards 0 -> 8. Adversary WOUNDED -> APPROVED (docs/reviews/modelview_pplay_2026-11-10.md). Standing risk: stale P(plays) on doubtful/questionable downgrades now priced, not capped (11 of 1,541 player-ticks at T-60; none positive). | Devin's decision (2026-11-10 12:47). | A positive-EV or alerting prop on a player downgraded after his last forward row; or Kalshi's DNP rule read. |`
