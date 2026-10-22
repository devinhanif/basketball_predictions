# Pre-freeze review, round 2: INGAME_WINPROB_V2 (T513), 2026-10-22

Reviewed: head 3399a5f on `agent/market-ingame-v2`. Round 1 is in `docs/reviews/prefreeze_ingame_v2_2026-10-22.md`.
Every check is label-free, read-only, and covers 2022-24 regular season only; 2025 was never named. Scripts are in the scratchpad
(`five2.py`, `five3.py`, `five_roster.py`), each run with `.venv/bin/python <script> <n>`, and every run exited 0.

**NOT_READY.** Four MUSTs remain, and all are small text changes. The most important is R1: the causal five leaves an outgoing
player on the floor whenever the incoming substitute cannot be named. That happens in 11-12% of team-states and 17-19% of
Q4 blowout states, and it overstates lineup strength in exactly the region the guard is judged on.

Verdict: BLOCKED

## Round-1 MUSTs: applied as written?
| MUST | Status |
|---|---|
| M1 grid | applied. `GRID = range(60, 2821, 60)`, 47 states. Gate rerun: gate.json sha256 **130b423f** (confirmed). `db_sha256` 5dcc497a9de2 equals `shasum` of `data/backups/nba_20261022.duckdb` (confirmed). Counts 801 / 862 / 850 games >= 98% (confirmed), and every cell matches my recount exactly. Verdict PASS |
| M2 causal prefix | applied (`prefix_len`, gate line 236, unit test) |
| M3 causal five | applied in code. The text overstates it: "players subbed in at or before T are added" is false when the substitute cannot be named (see R1) |
| M4 lag guard | applied |
| M5 fitter | applied |
| M6 C0 shuffle | applied |
| M7 inference | applied, except the bootstrap routine is "X **or** Y", which is a fork. R4 picks one |
| M8 rating | applied |
| M9 cadence | applied |
| M10 prior rows | applied. The gate's prior check is now against real tip minus 60 min (1,228 / 1,230 / 1,225) |
| M11 ambiguities | applied. OT moved to A1 (S1) |

## Probe 1: substitutes who cannot be named without a box roster
`five2.py` / `five3.py`, 60 sampled games per season (seed 7), 5,280 team-states per season. For each one I compared the causal five
with the stint five and tracked the substitutions the tracker could not resolve in the current period.

| Season | causal ≠ stint | states with an unresolved sub | share of mismatches that are unresolved subs | Q4, \|margin\| >= 15: mismatch / unresolved |
|---|---|---|---|---|
| 2022 | 24.2% | 11.4% | 47.3% | 32.2% / 17.8% (n 376) |
| 2023 | 25.0% | 12.2% | 48.9% | 37.1% / 19.4% (n 660) |
| 2024 | 25.8% | 11.5% | 44.3% | 34.9% / 16.8% (n 392) |

Mechanism (`nba/parse/lineups.py`, in the substitution branch where `in_id is None`): an unresolved substitution is skipped, so the
**outgoing player stays on the floor**. Bias, measured with a label-free proxy (each player's mean minutes in strictly prior games):
the players found only in the causal five average **+5.9 to +7.7 more minutes per game** than the stint players they replace,
and the gap is positive in 68-72% of mismatches. The causal five keeps rested starters on the floor while deep-bench players play,
and does so most in Q4 blowouts, which is the R98 alert region. This is causal, so it is not a leak. It does do three things:
1. It adds noise to L and S, pulling the lineup gain toward a false null (playbook question 3).
2. It biases L upward in blowouts.
3. Live, L comes from ESPN `active`, which is exact, so training and serving disagree in R98.

**The fix is causal.** `five_roster.py` (25 games per season, skipping the first 100 games, seed 7) passes an **as-of roster**:
players with minutes > 0 for the team in strictly earlier games of the season, plus the box starters, with names from the team's
15 previous pbp files. Unresolved states fall from 11.1 / 11.6 / 10.5% to **2.3 / 2.9 / 3.4%**, and causal ≠ stint falls from
24.7 / 22.1 / 28.7% to **16.4 / 14.0 / 21.5%**. The remainder is mostly the period-opener uncertainty that cannot be known
causally (round 1: about 22% of team-states).

## Probe 2: identity tests
`tests/ride/test_causal_five.py::test_causal_five_identity_and_planted_future` is tautological. `cut = d.iloc[:len(full)]` *is*
`full`, and the planted rows are cut away by `causal_prefix` before `causal_five` ever sees them. It proves only that the function
is deterministic. The leak risk is not in `causal_five` but in whatever builder the run actually uses (R3: an incremental one that
carries state). One fixture's first half cannot be enough. **The run-time identity over 30 games per season, executed on the run's
own builder, is a MUST (R2).** Rule 7(b) also still says "events with elapsed <= T", which contradicts the prefix definition in rule 1.

## Probe 3: incremental builder equality
This is needed because the per-state tracker re-derives each period's openers from all evidence up to T, so an earlier guess can
change at a later T. An incremental builder that freezes its first guess would differ, so exact equality has to be required (R3).

## MUSTs (exact replacement text)

**R1. As-of roster for the causal five.** In rule 2, replace "No box roster is passed (who played is end-of-game information)." with:
"The tracker receives an **as-of roster**: per team, the players with minutes > 0 for that team in its strictly earlier
regular-season games of the same season, plus the box starters (announced before tip). It also receives `names_by_player` taken from the
`player_name` of those players in the team's 15 previous pbp files. Who played in this game is never passed. A substitution whose
incoming player is still unresolved leaves the outgoing player on the floor (the tracker's rule). The share of team-states with an
unresolved substitution in the current period is reported by season and for Q4 with |margin| >= 15 (review sample: 2.3-3.4% with the
roster, against 10.5-12.2% without)." Update the descriptive gate row (line 37) to use the same inputs. The gate checks do not change.

**R2. Run-time identity (rule 7(b)).** Replace 7(b) with: "(b) Truncation and planted-future identity, run at run time on the
**same state builder that produces the scored rows**, for the first 30 games of each season in game_id order, at every grid state
including OT. For each T: the row built from the full game equals (i) the row built from the game truncated to its causal prefix at T,
and (ii) the row built after every event beyond that prefix is replaced by a planted future (actors set to 999, scores +50,
substitutions reversed, and one planted event with elapsed <= T placed after the first event with elapsed > T). Equality means every
column is equal (integers exactly, floats within 1e-12) and the on-floor sets are identical. A single mismatch fails the guard. The
fixture test in `tests/ride/test_causal_five.py` is a unit test only and does not satisfy this guard."

**R3. Incremental builder equality.** Add to rule 1: "If the run uses an incremental (stateful) state builder, it must reproduce the
per-state reference exactly. The reference is `causal_prefix` plus a fresh `track_lineups` on that prefix with the R1 inputs,
recomputing openers from all evidence <= T, and recomputing every other feature from the prefix. Test: on the first 30 games of each
season and on the fixture, every column of every state row from the incremental builder equals the reference (integers exactly, floats
within 1e-12, on-floor sets identical), at all 47 regulation states and every OT state. This test is run, and must pass, before the
dryrun. The reference, the incremental builder and the test are committed with the freeze, with their sha256 in the freeze line."

**R4. One bootstrap routine.** In rule 5, replace "The routine reuses an existing paired game-cluster implementation
(`nba/markets/decision_scoring.py` game index or `nba/ride/backtest.py::cluster_ci`) checked against a plain loop" with: "The routine
is `nba.markets.decision_scoring.Boot(gi, n_games, n_boot=2000, seed=0)` (one shared multinomial resample matrix W for every arm and
contrast). The draw value of a contrast is (W @ d_g) / n_games, where d_g is the per-game difference. The CI and p are computed from
those draws. It is checked once against a plain loop on synthetic data."

## SHOULDs
- S8. Rule 10: state that live L, S and F use ESPN `active` (exact) while training uses the causal five, which still differs from
  the stint five in 14-21% of team-states even with the roster. Add the shadow's 0.95+ reliability split by Q4 |margin| >= 15.
- S9. Rename the fixture test to say that it checks determinism, so nobody later reads it as the 7(b) guard.

## Single most likely failure mode
If the lineup group (L, S) carries the gain, or fails the 98% guard, the likely cause is the stale-starter bias in garbage
time (R1), not real information.

## Not verified
- pytest, ruff and mypy: the tools are missing from the worktree env.
- The roster fix was measured on 25 games per season, and only after each season's first 100 games. Early-season rosters will be thinner.
- How the name ladder behaves with an as-of roster on two-way and 10-day players.

## Drafted row (not appended)
TEST_LEDGER T513 note: "pre-freeze round 2 2026-10-22: NOT_READY; M1-M11 applied (gate rerun sha 130b423f, DB 5dcc497a9de2,
801/862/850 confirmed); 4 MUSTs: as-of roster for the causal five (unresolved subs 11-12% of team-states, 17-19% in Q4 blowouts;
2-3% with roster), run-time identity on the run's builder, incremental-builder equality test, one named bootstrap routine;
review docs/reviews/prefreeze_ingame_v2_round2_2026-10-22.md."
