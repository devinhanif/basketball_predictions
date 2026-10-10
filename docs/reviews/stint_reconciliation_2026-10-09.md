# Stint-minutes reconciliation: diagnosis (2026-10-09)

Trigger: F12 check 1b found stint-derived minutes within 1.0 min of box minutes on only 48.1% of
82,403 player-games (2022-24). Task: diagnose, do not rewrite. No file under `nba/parse/` was changed.
Read-only DuckDB, CPU only. Scratch scripts were not committed (method is described below so it can be rerun).

## Verdict (short)
- **Durations are correct. Player attribution is wrong.** Each team's stints tile every period exactly
  (sum = 720.0 s in Q1-Q4, 300.0 s in OT, for all 42,758 team-periods; min/max within 0.0005 s).
  Mean signed error is +0.03 min and the median is 0; the errors cancel inside a team. So stints are
  not systematically short or long, and it is not an OT, clock-keying, zero-duration-possession or
  period-boundary-timing problem.
- **Mechanism: the period-opening five is guessed, and the guess is wrong in about 80% of Q2/Q3/Q4 team-periods.**
  `track_lineups` carries the *closing* five of the previous period into the next period and only
  corrects it when an unseen player acts, evicting "the first unconfirmed player in insertion order".
  That eviction is a coin flip over the stale guess, so wrong players are credited with time they did
  not play and the real players are under-credited until (and unless) they act.
- **Secondary mechanism: unresolvable substitution names.** Same-surname teammates (A./J. Holiday,
  Green, Williams, Wagner, Harris) resolve to `None` in `_build_name_lookup` (ambiguous key), and the
  sub text carries an initial ("A. Holiday") that never matches `player_name` (last name only). The
  sub is then skipped as a no-op, so the out-player stays on court. Players who never act in a game
  are also absent from the name lookup.
- **Lineups are not fully trustworthy.** "5v5 on every possession" is true by construction (the tracker
  always holds five), not evidence of correctness. Against a corrected reference tracker, 78% of
  possessions (Q2-Q4: ~72-75%) have the exact five on both sides; mean overlap is 4.72 of 5 (details below).

## 1. Reproduction
Same SQL as `nba/eval/f12_closing_risk.py` (`load_player_games`, season <= 2024):
n = 82,407 player-games (F12 reports 82,403; 4 rows of difference, presumably DB growth), **within 1.0 min = 48.08%**.
Replaying `track_lineups` on a sample of 1,318 games (every 3rd game) from the cached PBP reproduces the
stored `stints` row count in 100% of games, so the DB is exactly what the current code emits.

## 2. Error characterisation (old stints vs box minutes)
All rows, 82,407 player-games (full DB) unless a sample is stated.

| slice | n | within 1.0 | mean signed err (min) |
|---|---|---|---|
| all | 82,407 | 48.1% | +0.03 (median 0.0) |
| starters | 39,530 | 43.8% | **-0.54** |
| bench | 42,877 | 52.0% | **+0.56** |
| season 2022 | 27,265 | 49.3% | +0.03 |
| season 2023 | 27,572 | 47.8% | +0.04 |
| season 2024 | 27,570 | 47.2% | +0.03 |

Signed quantiles (5/25/50/75/95): -4.6 / -1.1 / 0.0 / +1.1 / +5.0 min. Two-sided and symmetric, not a
bias in one direction. Box-minute buckets (full DB, sample of 28,110 for MAE):

| box minutes | n (sample) | within 1.0 | mean err | MAE |
|---|---|---|---|---|
| <10 | 4,640 | 70.3% | +0.08 | 0.94 |
| 10-20 | 6,468 | 46.0% | +0.50 | 1.94 |
| 20-30 | 8,374 | 43.1% | +0.09 | 2.09 |
| 30+ | 8,628 | 43.1% | -0.50 | 2.00 |

Stars lose time, rotation bench players gain it. Season trend is flat-to-slightly-worse (49.3 -> 47.2%).
Per game (sample): median game has 47% of its players within 1 min; only 0.08% of games are fully clean.
So it is a per-game, every-game problem, not a handful of bad games.

Per period (stint share of team-period opening lineups that differ from the corrected reference, sample
1,318 games, 10,698 team-periods):

| period | n team-periods | opening five wrong | mean players wrong | mean len of first (stale) stint |
|---|---|---|---|---|
| Q1 | 2,636 | 0.2% | 0.002 | 349 s |
| Q2 | 2,636 | **78.5%** | 1.36 | 86 s |
| Q3 | 2,636 | **81.8%** | 1.29 | 126 s |
| Q4 | 2,636 | **82.9%** | 1.51 | 80 s |
| OT1 | 138 | 51.4% | 0.65 | 169 s |
| OT2 | 16 | 50.0% | 0.56 | 195 s |

Q1 is correct because box starters seed it; the failure begins exactly at the first period boundary.
OT is handled structurally (periods 5/6, 300 s; stints tile exactly); it is wrong only through the same
carryover guess. The stale first stint lasts 80-130 s on average, i.e. the wrong player is credited for
roughly the first 1.5-2 minutes of each period unless a correction happens.

Other candidates checked and ruled out: substitution not closing a stint until next possession (stints
are keyed on the substitution event clock, not possessions); possession-clock vs wall-clock (stints use
the PBP game clock and sum to 720 s); zero-duration stints (do not break the totals); OT excluded or
mis-numbered (periods 5-6 present, 300 s each); technical/flagrant FT substitutions (marginal; see
fix item 3).

## 3. Possession-level lineups (what F11/F13/F15/F17 use)
Reference = corrected tracker (below). Possessions in the 1,318-game sample, n = 262,178; each side
compared via the same `_covering_stint` rule used by `attach_lineups_to_possessions`.

| period | possessions | offense five exact | defense five exact | mean overlap (of 5) |
|---|---|---|---|---|
| Q1 | 66,477 | 93.4% | 93.4% | 4.91 |
| Q2 | 65,790 | 75.3% | 74.6% | 4.68 |
| Q3 | 65,411 | 72.5% | 71.7% | 4.65 |
| Q4 | 63,011 | 72.4% | 71.5% | 4.64 |
| OT | 1,489 | ~69% | ~67% | 4.63 |
| all | 262,178 | 78.5% | 77.9% | 4.72 |

Distribution of correct offensive players per possession: 5 right 78.5%, 4 right 16.9%, 3 right 3.8%,
<=2 right 0.8%. Errors are almost always one wrong slot, concentrated in the first ~1-2 minutes of
Q2-Q4. This is an upper bound on lineup error only to the extent the reference is right; the reference's
validity is supported independently by the box-minute match below (91.8%, MAE 0.47 min), not by
agreeing with the stored lineups.

**Implication.** The possession lineups are *not* "right, only durations off". Roughly one possession in
five carries at least one wrong player, mostly in the early minutes of Q2-Q4. Effects on the queued
hypotheses:
- F15 (weakest defender), F17 (rest lineup), F11 (lineup context): individual-player attribution matters
  directly; a wrong slot on ~22% of possessions is attenuating noise for any per-player or
  per-combination feature. Q1-only possessions are 93% clean and could be used as a degraded interim.
- F13 (momentum) depends less on exact identity, but any lineup-quality covariate is affected.
So these are **blocked on the fix** (not merely tainted), but the fix is small (below). Do not run them on
current `stints`/`possessions.off_players` and report as if lineups were verified.

## 4. Proposed minimal fix (not applied)
Replace the period-opening guess in `nba/parse/lineups.py` with a look-ahead scan. Prototyped
outside `nba/parse/` on the 1,318-game sample (not committed):

1. **Period starters by first appearance (look-ahead).** For each team-period, scan its events in order.
   A player is a starter if his first appearance is an action (shot, rebound, turnover, foul, FT,
   violation, jump ball) or as the *out* side of a substitution, before he has been subbed *in*.
   Stop at five. Exclude technical/delay/ejection fouls and technical FTs as evidence (bench players
   can commit them). If fewer than five are found, fill from the previous closing five minus players
   already subbed in.
2. **No eviction heuristic at all** for mid-period play; apply substitutions only (remove out, add in).
3. **Name resolution for subs.** Strip a leading initial ("A. Holiday" -> "Holiday"); when a surname maps
   to several teammates choose the one not currently on court; for names absent from the PBP name map
   use roster elimination (box-score players with minutes > 0 who never appear in the PBP name map).
   Residual ambiguity (both same-surname teammates off court at that moment) remains and is the main
   cause of the leftover misses; the clean cure is to ingest first-name initials (or box-score V3 player
   names) and key the lookup on initial + surname.
4. Keep the ADR-0001 style gate and add one for stints: share of player-games within 1.0 min of box.

Measured effect (sample 1,318 games, 28,110 player-games with box minutes > 0):

| tracker | within 1.0 min | within 2.0 min | MAE (min) | starters mean err | bench mean err |
|---|---|---|---|---|---|
| current (stored `stints`) | 48.3% | 65.7% | 1.84 | -0.54 | +0.48 |
| look-ahead starters only (no name fix) | 88.5% (330-game subsample) | 91.1% | 0.60 | ~0 | ~0 |
| + initial/elimination name fix | **91.8%** | 92.8% | **0.47** | +0.04 | -0.03 |

Expected rate after the fix: ~92% (+/- ~0.3 pp binomial on 28k rows; sample covers every 3rd game of
2022-24 so it is representative). The F12 bar was 95%; the fix reaches ~92%, short of 95%. The remaining
miss is concentrated in games with same-surname teammates (bimodal: 66% of games are fully clean, residual
errors are large when they occur), and needs the first-initial source to close. If the bar stays at 95%,
plan for the name-source step; if F11/F13/F15/F17 can accept ~92% (with a per-game flag to drop games
with unresolved subs, `unresolved_in`/`fwd_unres` counters), they can be unblocked by the fix alone.

Caveat on the reference: box minutes are the only ground truth available (no official lineup feed in
the repo), and they are rounded to the second but include dead-ball nuances (the 4-row team sums
exceed 240 by ~1.4 min on average), so a ceiling of 100% within 1.0 min is not expected.
After the fix, re-run: `.venv/bin/python -m nba.eval.f12_closing_risk checks` (read-only), plus the
possession-lineup reload via `nba/parse/loader.py` (`load_stints`, `load_possession_lineups`) -- a
write step for the maintainer, not performed here.

## 5. Honest notes
- Negative result for the original assumption: "lineups validated as 5v5 on every possession" does not
  validate lineup identity. It checked set size only.
- The reference tracker is a prototype built to size the fix; it has its own residuals (unresolved subs,
  `scan_gt5` in ~10% of team-periods where an unresolved sub makes a later action look like a starter).
  The 91.8% figure is a measured reconciliation vs box minutes, not a claim of exact lineup truth.
- Possession-level agreement numbers are against that prototype, so the true error rate of the stored
  lineups is likely in the 20-25% range, not an exact 21.5%.
