# Adversary review: WORKLOAD (cells P and pts), 2026-10-12

Rule `docs/prereg/WORKLOAD.md`, sha256 261fabce (commit 282e2cc, 23:25:28 CDT). Harness `research/eval/workload_eval.py`
(commit f76278e, 23:37:36). Outputs `reports/prereg_workload/*`, per-row arrays `data/ops/workload_cache/*`. Seasons 2023
and 2024 only. Season 2025 was not loaded. `nba.duckdb` opened `read_only=True`. `heavy.lock` was absent and not taken. No arm of
the harness was refitted. Attack scripts are in the session scratchpad (`a1.py` .. `a5.py`), not under `nba/`.

## Verdicts

| Cell | Verdict | One line |
|---|---|---|
| **P** (P(play) log loss) | **SURVIVES** (no leak), renamed | Real pre-tip information. The carrier is **the last game's minutes** (`wl_min_last`, `wl_mean_min_l3`, `wl_min_3d`), not workload, fatigue or tracking. Bench rows that fell out of the rotation carry it. C lacked a last-game-minutes term. |
| **pts** (integer CRPS vs A0) | **WOUNDED** | No leak found. But in the 2023 select season the within-player-season placebo matches the candidate (B−PL = +0.0006 [−0.0038, +0.0050]). The pass rests on judging the placebo gate on 2024 only (B−PL −0.0044 [−0.0085, −0.0004]), and that reading is a harness choice. |

Top finding: none of the LOAD gain is a leak. The P gain is "how many minutes he played last time out". That fact is known the night before.
Lagging every LOAD column by one extra day keeps 93-95% of the stacked gain. The pts gain is real-looking but not separable from the placebo in its own select season.

## Rule integrity

* `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/WORKLOAD.md | shasum -a 256` gives 261fabce...; `git diff 282e2cc HEAD -- docs/prereg/WORKLOAD.md` is empty. PASS.
* Freeze 23:25:28 < harness commit 23:37:36 < first artifact (`pts__base.npz` 23:38:20). The AMBIGUITIES readings (including "placebo judged on 2024") were committed before any result. PASS.
* Row counts drifted from the gate: Cell P 2024 has 33,127 rows (prereg says 32,986, +141). M2 reproduction holds on the 27,583-row intersection (`m2_repro.json`). Minor.

## Checks

| # | Check | Command (scratch) | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | Date and timezone consistency | `a1.py` | 3,958 games; meta rows P 96,058 / props 84,170 / minutes 80,430 | `game_date` matches `games.game_date` for every meta row (0 mismatches). ET date of real tip matches `game_date` for 3,958 / 3,958 (the UTC date differs for 2,991, but nothing uses it). | PASS |
| 1b | Doubleheaders and duplicates | `a1.py` | 84,170 played rows | 0 player same-date rows, 0 team same-date games, 0 duplicate (game, player) rows | PASS |
| 1c | Own planted future on the REAL played table | `a2.py` | 2,440 target rows on 12 dates across 2023-24 | Every row dated >= D set to 47.9 min / 9.9 mi, plus a fake 44-min game on D and on D+1 for every target player: **0 cells changed**. Control (edit D−1): **2,258 cells changed** | PASS |
| 2 | Missingness / DNP encoding (P rows) | `a3.py` | 66,220 | No LOAD column has a NaN rate that differs by outcome (max 0.0012 vs 0.0007). DNP and "no game" are both encoded as absence from the played-only table. The game counts that would expose prior DNPs (`g_3d`, `g_7d`) add **nothing** over C (stack +0.0028 / +0.0008, i.e. worse than the recalibrated base). `days_since_played` and `dnp_last5` are already in C. | PASS |
| 3 | Which LOAD columns (stack on harness OOF `p_base`, month walk-forward) | `a5.py` | 2023: 21,245 (first 3 months as warm-up); 2024: 33,127 | 2024 deltas: `min_last` −0.0094 [−0.0113, −0.0071]; `mean_min_l3` −0.0084; `min_3d` −0.0070; `min_7d` −0.0064; `mean_min_l5` −0.0057; `min_l5_vs_base` −0.0027; `g_3d`/`g_7d` +0.0008/+0.0009; all LOAD_MIN −0.0122 [−0.0145, −0.0098]. 2023 has the same ranking (`min_last` −0.0069 [−0.0103, −0.0034]). | carrier identified |
| 2b | Lag every LOAD feature one extra day | `a5.py` | as above | All LOAD_MIN: −0.0122 → −0.0114 (2024), −0.0085 → −0.0081 (2023). Nothing sits close to tip. | PASS |
| 4 | Knockout (recorded, re-derived from per-row arrays) | `a3.py`, `a4.py` | P 33,093 / 33,127; pts 27,619 / 27,701 | P: KO_LOAD +0.0002 / +0.0001; NT (no tracking) −0.0165 / −0.0160; AL_LOAD −0.0161 / −0.0158. pts: KO_LOAD +0.0017 / −0.0003; AL_LOAD −0.0034 [−0.0071, +0.0001] / −0.0065; NT −0.0056 / −0.0101 (tracking slightly hurts in 2024). | LOAD_MIN carries both cells |
| 5a | Placebo (within player-season) | `results.json`, re-derived | as above | P: B−PL −0.0154 [−0.0179, −0.0129] / −0.0129 [−0.0153, −0.0105]. Passes both seasons. **pts: B−PL +0.0006 [−0.0038, +0.0050] (2023) / −0.0044 [−0.0085, −0.0004] (2024).** Fails in the select season. | P PASS; pts FAIL 2023 |
| 5b | Shuffled labels | `P__shuffle.npz` vs `P__scores.npz` | 66,220 | **Inert by construction.** Only B is trained on permuted labels and is compared with an *unshuffled, trained* base (`p_base` byte-identical in both files). A label-free model always loses: +0.166 (P), +0.078 / +0.099 (pts SHUF). `p_AL_*` in the shuffle file are byte-identical to the unshuffled ones, so those "_shuf" rows were never shuffled. The test could not fail. | NOT A TEST |
| 6 | Replication: months, regular vs post-season, slices | `a3.py`, `a4.py` | per slice below | P: every block negative in both seasons (Oct-Dec −0.0182 / −0.0201; Jan-Feb −0.0163 / −0.0141; Mar-Jun −0.0154 / −0.0124); regular season −0.0170 / −0.0163; playoffs −0.0128 / −0.0079 [−0.0186, +0.0019]. Bench carries 86% / 91% (−0.0223 both); starters −0.0066 / −0.0038. `min_last < 5` rows (DNP rate 51-52%): −0.0418 / −0.0355. Idle > 7 days: −0.0020 / −0.0034 (ns). pts: regular −0.0060 / −0.0090; playoffs −0.0225 (21% of the 2023 gain) / +0.0019 (ns); Jan-Feb 2023 −0.0035 [−0.0101, +0.0030]; realised minutes < 10 carry 60% / 63%; starters ns. Dropping the best month: pts −0.0067 / −0.0074. Top 10 players carry 38% / 31% of the pts gain. | P holds; pts uneven |
| 6b | Integer support | rule, harness | — | All S arms go through the same mixture and `to_integer_support`, and A0 is on the same grid. | PASS |
| — | D3 oracle minutes (recorded) | `results.json` | 27,619 / 27,701 | pts A3W−A3 = −0.0013 / −0.0009. The effect runs through minutes, which fits the bench / low-minutes concentration. | consistent |

Clustered CIs are game-clustered percentile bootstraps (my re-derivations: 1,000 draws for slices and 500 for stacks, seed 0; the harness used 2,000 draws, seed 0).
My re-derived headline numbers match `results.json` to 4 decimals (P −0.0167 / −0.0157; pts −0.0071 / −0.0082).

## Why P survives, and what it actually is

C (`BASE_FEATURES` + T-60 tag + report flags) holds `days_since_played`, `dnp_last5` and a trailing-window `avg_minutes_given_played_prior`,
but no last-game minutes. A deep-bench player who got 2 garbage-time minutes last time is often a DNP-CD next time, and C cannot see that.
This is a box-score fact from a game dated at least one day earlier, available hours after that game. It is not workload: the gain sits on
low recent minutes (falling out of the rotation), not on heavy load, and tracking adds nothing (B−NT +0.0003 in 2024). The honest
name for the claim is "recent-role minutes for P(play)". It is cheap: one column, `min_last`, delivers about 75% of the stacked LOAD_MIN gain.

## Why pts is wounded, not broken

No leak signature: the plant is inert, the dates are clean, there is no NaN asymmetry, and the gain runs through minutes (D3). But:
1. In 2023, the season the rule selects on, the placebo (LOAD permuted within player-season) equals B. Null-ish arms span −0.0077 (PL) to +0.0017
   (KO_LOAD), a spread larger than the claimed gain.
2. AL_LOAD alone in 2023 is −0.0034 with a CI crossing 0, so LOAD alone does not reach the floor in the select season.
3. The 2024 pass of the placebo gate rests on the AMBIGUITIES reading "judged on the REPORT season 2024". That reading was committed before
   results, so it is legitimate process. But the rule says "all must hold", and on a both-seasons reading pts fails rule 3.
4. The placebo is itself not a clean null. Permuting within player-season puts later games' LOAD values on earlier rows (look-ahead on the
   player's season-level minutes). That makes it harder to beat, which helps P's case, but a gain equal to it (pts 2023) is uninterpretable.

## Corrected family and power

The family actually run is 6 primary cells × 2 seasons. BH within season as declared: P (p_bh 0.00075 / 0.0006) and pts (0.00075 / 0.0006)
both survive BH, so multiplicity is not the problem. MDE (1.96 × clustered SE): P 0.0024-0.0025, pts 0.0035-0.0037. The pts 2023 CI upper
(−0.0035) does not clear the −0.005 floor. The point clears it by 0.002, about half an MDE, so a floor-sized pts effect is only marginally resolved.

## Single most likely failure mode if the claims are wrong

P: comparator weakness, not leakage. A C that carried production's own recency minutes (min5 / last game) could absorb most of the gain,
leaving a "win" that just says C was missing a column production can compute. pts: noise between refits of the same pipeline. The
placebo and knockout spread in 2023 is about the size of the gain.

## Harness findings (severity-ranked)

* **Major**: the shuffled-label check cannot fail. It compares a permuted-label B with a trained, unshuffled base, and the `AL_*_shuf` arms are
  copies of the unshuffled arms. Fix for any future rule: shuffle labels for BOTH arms (or permute the label residual against the base)
  and compare B_shuf with base_shuf.
* **Major**: the placebo is a full-season within-player permutation, so it injects future season information. Use a past-only permutation
  (or a circular time shift) in future rules.
* **Minor**: 2024 P rows are 33,127, not the gate's 32,986 (data drift between gate and run; it should be logged).
* **Minor**: `load_played` has no game-type filter. It is harmless here: only types 002, 004 and 005 are present, and all of them are earlier games.

## Not verified

* That a comparator with production's recency minutes (min5 / last-game) absorbs the P gain. That needs a refit of C + `min_last` with the
  exact command `python -m research.eval.workload_eval` arms on a new rule id. I did not refit.
* A past-only placebo for pts (needs a mixture refit; over the watchdog budget).
* Seed replication of the pts arms (LightGBM refit with another seed).

## Recommended (not applied)

1. P: record as a PASS **named "recent-role minutes (LOAD_MIN; `min_last` dominant)"**, not workload or tracking. Before any holdout touch,
   draft a new rule that tests C + `min_last` / `mean_min_l3` only against C + production's min5. If that absorbs it, the result is "add one
   column to P(play)", not a family.
2. pts: do not draft a holdout row on this result. Record it as WOUNDED: the select-season placebo matches the candidate.
3. Fix the shuffled-label and placebo constructions in the harness template before the next rule freezes.

## Drafted rows (not appended)

Ledger (`docs/TEST_LEDGER.md`):
`| T4xx | WORKLOAD adversary | P: SURVIVES (no leak); carrier last-game minutes (min_last, mean_min_l3, min_3d), not workload/tracking; LOAD_MIN stack 2024 −0.0122 [−0.0145,−0.0098] n=33,127; plant 0/2,440 changed, control 2,258; +1-day lag keeps 93-95%. pts: WOUNDED; 2023 B−PL +0.0006 [−0.0038,+0.0050] n=27,619; passes only under the 2024-only placebo reading. Shuffled-label check inert (major). | docs/reviews/workload_review_2026-10-12.md |`

Decisions (`docs/DECISIONS.md`):
`WORKLOAD adversary: P renamed "recent-role minutes"; any follow-up is a new rule testing C+min_last vs C+production recency; pts not taken to holdout. Reverses if a past-only placebo and seed replication show pts B−PL <= −0.003 in 2023.`
