# INJURY_ELO_T30 - win probability at T-30 (inactive list known)

Status: PRE-REGISTERED 2026-10-09, written before any result was computed. Results are filled in
below the line "RESULTS" only after the run. The rule is not changed after seeing results.
Season 2025 (frozen holdout) is NOT touched: the loader filters `season <= 2024` in SQL and the
eval raises on anything later. No HOLDOUT_ACCESS_LOG row is needed.

## Question

Production `rung0_injury_elo` v2 (T-60) = frozen MOV-Elo logit + 2-coefficient ridge on the as-of
value of players the official report lists OUT (and DOUBTFUL at weight 0.5), report gated on the
real scheduled tip minus 60 minutes. At T-30 the teams' inactive list is submitted. Does adding
it improve the win-probability log loss?

## Leak statement (read first)

T-30 features use the per-game inactive list. They are a LEAK if used at T-60 and exist only behind
an explicit opt-in (new module `nba/models/injury_elo_t30.py`; `nba/models/injury_elo.py` is not
modified, so production is byte-identical). The T-30 model is a different prediction TIME, not a
replacement for production.

## Inactive-list data audit (done before the run)

- `player_availability` contains ZERO rows with `source='nba_inactive_list'` in `nba.duckdb`
  (sources present: `nba_official_report` only, 2022-10-18 .. 2026-06-13). The BoxScoreSummaryV2
  inactive pull exists in code (`nba/ingest/availability.py`) but was never run on the history, and
  that endpoint carries NO timestamp (it exists only once the game is in the Stats API).
- Therefore the historical inactive list is PROXIED from the box score: a player is "inactive
  tonight" iff he has NO `player_game_stats` row for the game while (a) he has a row for the same
  team in at least one of the team's previous `W = 10` games of the same season and (b) his most
  recent prior box row is for this team (trade guard). Players with a row but NULL/0 minutes
  (dressed, coach's decision) are NOT inactive: that decision is made after the list is submitted
  (this matches the NBA rule: inactives are submitted 30 min before tip; later scratches show as
  DNP rows). The proxy is derived from the game's own box score, so it is POST-HOC by construction:
  its timestamp is "final box score", not "T-30". It is exact only if the list is public by T-30
  and no one is removed from it after submission.
- Long-term absentees (outside the 10-game window) are covered by the T-60 report OUT rows.
- Consequences up front: the headline is an UPPER-biased estimate (same direction as the
  LINEUPS_KNOWN proxy caveat, docs/reviews/redteam_lineups_known_2026-10-09.md); the worst case
  below is the bound.

## Candidate and comparator (single pre-specified config; no tuning)

- Comparator ("T-60", production v2): `build_injury_features` with `configs/injury_elo.yaml`
  (tip_source real, lead 60, doubtful_weight 0.5), month-block walk-forward ridge
  (`walk_forward_probs`, lambda 5, min_signal_games 150) on the frozen MOV-Elo offset.
- Candidate ("T-30"): identical recipe and ridge, features
  `d_out30 = (V(out30_away) - V(out30_home))/10`, `d_doubt30 = (0.5 V(dbt30_away) - 0.5 V(dbt30_home))/10`
  (same sign convention and value function as production), where
  `out30 = report_OUT(lead 30 min) UNION inactive_proxy`, and
  `dbt30 = report_DOUBTFUL(lead 30 min) MINUS out30`. Report rows are admitted up to real tip - 30.
  `V` is the as-of value (games strictly before the date) from `nba.models.injury_elo`.
- Worst case ("T-30 WC", red-team method): treat players whose latest pre-tip (lead 60) report
  status is questionable, doubtful or probable and who are later inactive as UNKNOWN at T-30. Their
  inactivity is removed from `out30`; they keep their T-60 treatment (doubtful at weight 0.5,
  questionable/probable no weight). Features otherwise as the candidate, report lead 60. This is
  production plus only the inactive players the T-60 report did not hint at. Reported as a bound;
  not part of the pass rule.
- Not run (declared): "starters changed vs previous game" augmentation. Its proxy is the box-score
  starter flag, which the red team showed resolves game-time decisions (optimistic); the inactive
  list is the cleaner T-30 fact. It is not substituted after the fact.
- Sensitivity rows (descriptive, cannot replace the candidate): T-30 OUT-only (`d_out30` alone).

## Walk-forward and scoring

Month-block walk-forward on seasons 2022-2024 (coefficients for a block use only games strictly
before it; Elo logit replay identical to the production harness). The first date is dropped as in
the production harness. Nothing is selected: there is one config, so "select on 2023" is vacuous;
2023 is a descriptive replication, 2024 is the reported season. Season 2025 never loaded.

## Metrics and decision rule (season 2024 scored games)

Primary: paired per-game log-loss delta (T-30 minus T-60), 95% bootstrap CI (2,000 resamples,
seed 0; one row per game, so game-clustered). Brier and ECE deltas with the same pairing.

T-30 "adds value" iff ALL:
1. log-loss delta <= -0.002 AND CI upper bound < 0;
2. guards: Brier delta point <= 0; ECE (10-bin) delta <= +0.005;
3. no slice with n >= 100 has a log-loss delta > +0.005.

Slices (fixed now): games with >= 1 game-time decision (a rotation player, mean minutes >= 20 over
>= 5 prior games, whose latest lead-60 report status is questionable or doubtful, either team);
rotation players out at T-30 = 0 / 1 / 2+ (rotation as of the game); >= 2 rotation players out;
early (<= 15 games) / rest; games where the inactive list adds a rotation player beyond the
T-60 report OUT set ("late adds").

Report both the headline and the worst case. The verdict does NOT require the worst case to meet
the floor; it is classified as: "robust" (WC delta <= -0.002, CI < 0), "conditional" (WC delta < 0
but not meeting the floor/CI), or "headline only" (WC delta >= 0). A negative result is reported
as such.

## Forward shadow

Only if the pass rule is met: extend `nba.daily run-t30` to also log `rung0_injury_elo_t30`
win-probability rows from the lineup collector's confirmed inactives (comparison only, not a
production prediction). If it fails, no shadow is added.

## Tests (written with the code)

Planted-future (later games never change earlier T-30 features), same-game (tonight's minutes and
box stats cannot change the features, only row presence), report cutoff at tip - 30, trade guard,
and production byte-identity (`InjuryEloModel` / `build_injury_features` untouched).

## RESULTS

Run 2026-10-09 (CPU, ~6 s, `uv run python -m nba.eval.injury_elo_t30_eval --db nba.duckdb
--config configs/injury_elo.yaml --out-dir data/injury_elo_t30 --report reports/injury_elo_t30.md`;
nba.duckdb read-only). Pre-registration frozen as git blob `009c5adf0ddcedc08b2db0e94ae6cc6ef745bafd`
(sha256 prefix 202e2ec1b0fae659) at 2026-10-09T15:51Z, hashed before the code ran (the working-tree
file was not committed by the author; the maintainer commits). Full tables: reports/injury_elo_t30.md.
Ledger T160-T161. Delta = T-30 minus T-60 (negative = T-30 better), 95% bootstrap CI.

| season (n games) | T-60 log loss | T-30 d log loss | Brier d | ECE d | verdict |
|---|---|---|---|---|---|
| 2024 (1,315), REPORTED | 0.5979 | -0.0008 [-0.0044, +0.0026] | +0.0000 [-0.0013, +0.0014] | -0.0093 | FAIL (floor -0.002, CI spans 0) |
| 2023 (1,318), descriptive | 0.6055 | +0.0006 [-0.0026, +0.0037] | +0.0003 | -0.0051 | FAIL |
| 2022 (1,318), descriptive | 0.6458 | -0.0021 [-0.0060, +0.0021] | -0.0011 | -0.0046 | FAIL (CI spans 0) |

Worst case (2024): +0.0015 [-0.0000, +0.0030], Brier +0.0007, ECE +0.0075; class "headline only".
It is slightly worse than T-60 (mechanism not investigated). Sensitivity T-30 OUT-only: -0.0008 [-0.0044, +0.0027].

Slices (2024, T-30 d log loss): game-time decision >= 1 (n=774) -0.0021 [-0.0081, +0.0035];
rotation out 0 / 1 / 2+ (n=93 / 140 / 1,082) +0.0020 / +0.0042 [+0.0002, +0.0083] / -0.0017;
late adds >= 1 (n=451) -0.0041 [-0.0140, +0.0059]; early (n=236) -0.0019; rest -0.0006. No slice
veto (none > +0.005). The 2024 Brier point is +0.00004 (flat).

Verdict: NO SIGNAL. The T-30 inactive list does not improve win probability beyond the T-60
official report: the report's OUT rows are near-complete (d_out vs d_out30 correlation 0.90;
non-zero in 97% vs 99% of games) and the extra inactive players are mostly low-value. The effect is
indistinguishable from zero in all three seasons and the sign flips between them. Pass rule not met,
so no `rung0_injury_elo_t30` shadow was wired. Caveats: the inactive list is a box-score proxy
(post-hoc; DB has no `nba_inactive_list` rows), which would bias this toward a gain, yet none appears.
Holdout (2025) untouched. Starters-changed augmentation was not run (declared in advance).
