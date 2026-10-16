# INJURY_TIMING step 1: how stale is the stored "T-60" report, and does it matter? (descriptive; no rule frozen, no fit)

> **REV 3 NOTICE (pre-freeze review B1, 2026-10-15): every stamp and lead in sections 1-12 is on the FILENAME clock. Hour-only report PDFs are headed HH:30, so every stored and v1-intraday stamp is 30 minutes early, every lead is 30 minutes too long, and the "T-180 to T-60" gap is really about T-150 to T-30 for most games. The direction of each conclusion was kept in rev 3 but the numbers are superseded by section 13 (corrected header clock).**


Written Thu 2026-10-15 (shell clock `Thu Oct 15 13:05:38 CDT 2026` at the gate run), modeler. Follows Devin's 2026-10-15 request (DECISIONS 12:49 CDT, item 1) and `docs/research/questionable_status_scope_2026-10-15.md`.
Model class: none fitted. This is a data and error-structure measurement of the report snapshot feeding the context-residual props (gradient-boosted residual on a recency mean; playbook class "tree on tabular features") and the Q/D-tag consumers (P(play) shadow R, AVG shadow).
Season label = start year. Reads: `/Users/devin/Downloads/nba-prediction/nba.duckdb`, `data/history/nba_history.duckdb` (both `read_only=True`, `season <= 2024` in SQL), `data/schedule` with `max_season=2024`, and the untracked `reports/minutes_v2/*.npz` (production per-row minutes and pts forecasts, 2023-24, 55,202 rows / 2,633 games). Season 2025 was never queried. Scripts: `research/scratch/injury_timing_descriptive.py`, `injury_timing_rows.py`, `injury_timing_tagshift.py`; tables: `reports/injury_timing/{descriptive,rows,tagshift}.json` (seed 2026, B = 2000, game-clustered percentile bootstrap).

## 1. What is stored versus what exists

| item | finding | source |
|---|---|---|
| Stamps stored per game, 2023 / 2024 | 2.55 / 2.51 per game (all), 2.48 / 2.43 usable at lead >= 60 | descriptive.json `stamps_2023/2024` |
| Clock times of stored stamps, 2023-24 | exactly two: **11:00 and 17:00 ET** (206 and 207 dates in 2023, 211 and 212 in 2024). Nothing else, on any date. | same |
| Why | `nba/ingest/availability.py` `DEFAULT_ANCHORS = ("11:45", "17:45")`: the history backfill took, per date, the latest report at or before two anchors. It is a **backfill artifact**, not what the league published. Live code (`nba/daily/injury.py`) probes 15-minute slots, newest first, so the live pull can land on any slot. | code |
| Only stored full intraday sequence | 2021-22: 24.9 stamps per game, hourly 06:00-23:00 (1,323 games). 2019, 2020: 4.7, 4.6 per game. | descriptive.json `stamps_2021` |
| What the CDN holds for 2023-24 between 17:00 and tip | **not verified.** The probe code and the 2021 hourly files imply reports exist at least hourly some seasons; a WebFetch of one 2024 slot timed out (60 s) and the agent shell cannot reach the CDN. A 250-request pilot is drafted (`research/eval/injury_timing_pilot_probe.py`, `--dry-run` prints "5 dates, 250 requests"). | this run |

## 2. Lead of the snapshot production actually uses

Training and the live code use the same function (`latest_pretip_flagged` / `serve_pretip_flagged`: latest stamp with `as_of <= min(now, real tip - 60 min)`, live also `<= 36 h` old). Lead = tip - used stamp, minutes.

| season | games with a usable snapshot (of games) | lead p10 / p50 / p90 | share lead <= 90 | share >= 150 | share >= 180 |
|---|---|---|---|---|---|
| 2023 | 1,317 (1,318) | 120 / 180 / 300 | 3.9% | 74.8% | 56.2% |
| 2024 | 1,317 (1,320) | 120 / 180 / 300 | 4.9% | 74.1% | 56.9% |
| 2021 (reference, hourly) | 1,323 (1,323) | 60 / 60 / 90 | 99.8% | 0.1% | 0.0% |

By tip hour (ET), 2024 median lead: 19:xx tips 120 (n 482), 20:xx 180 (n 330), 21:xx 240 (n 140), 22:xx 300 (n 200). The "T-180" label is an average over a 120-300 spread set by the tip hour.
**Live lead is an upper bound on freshness, not a measured T-60.** Live picks the newest slot the CDN has; if the league publishes only a few reports an evening, the live lead can also be 90-150. No live log exists yet; the first 2026-27 pulls will measure it (stamp, tip, lead per game is already in `player_availability`).

## 3. How often the status changes (n games, game-clustered 95% CI, events per game, both teams)

Two sources, because the stored 2023-24 data have no stamp between 17:00 and tip. **2023-24: 11:00 -> 17:00 same-day (a 6 h window).** **2021-22: latest <= T-180 -> latest <= T-60 (a 2 h window; the only season that has both).** 2021-22 is outside the "2023-24 only" frame; it is used only because it is the only stored intraday sequence. It is a COVID-era season (health-and-safety protocol outs, 1,591 "available" listings at T-60 versus 810-826 in 2023-24), so treat its levels as a regime-specific proxy.
"Rotation" = prior-10-game minutes average >= 20 over >= 5 prior games (the `rotation_flagged_by_team` rule, as-of, `season <= 2024` state).

| event, per game | 2023 11:00->17:00 (n 1,158) | 2024 11:00->17:00 (n 1,143) | 2021 T-180->T-60 (n 1,323) |
|---|---|---|---|
| OUT added, all players | 1.44 [1.33, 1.56] | 1.53 [1.41, 1.66] | 0.84 [0.79, 0.90] |
| OUT added, rotation | 0.62 [0.56, 0.68] | 0.75 [0.68, 0.83] | 0.53 [0.49, 0.58] |
| OUT removed, rotation | 0.003 [0.001, 0.007] | 0.008 [0.003, 0.014] | 0.015 [0.008, 0.025] |
| Q/D -> OUT, rotation | 0.17 [0.15, 0.20] | 0.17 [0.15, 0.20] | 0.43 [0.39, 0.47] |
| Q/D cleared (to available / probable / off the report), rotation | 0.16 [0.13, 0.19] | 0.14 [0.12, 0.17] | 0.42 [0.38, 0.46] |
| any status change, rotation | 1.23 [1.14, 1.32] | 1.35 [1.24, 1.45] | 1.18 [1.11, 1.24] |

Reading: the report moves a lot between the stamps. Of the 2021-22 Q/D players at T-180, 37.7% (785 of 2,082) were OUT by T-60, and 805 of 1,888 Q (43%) were Available. In 2023-24 the 11:00->17:00 window already shows 13.8% of Q players (261 of 1,891) turning OUT. A rotation OUT is **added** to a game in the last two hours about 0.5 times per game (about 0.27 per team-game, so at most roughly a quarter of team-games carry a late rotation-OUT change in the 2021 regime); **removals are rare** (0.015 per game). The skew is one-sided: stale lists miss late OUTs.

## 4. Does the tag mean the same thing at T-180 and T-60? (n player-games, P(box minutes > 0), game-clustered CI)

| group | P(played) | n |
|---|---|---|
| 2021 Q at T-180 (all) | 0.538 [0.513, 0.562] | 1,888 |
| 2021 Q at T-60 | 0.656 [0.602, 0.704] | 450 |
| 2021 Q at T-180, still Q at T-60 | 0.660 [0.605, 0.710] | 432 |
| 2021 Q at T-180, OUT at T-60 | 0.000 | 641 |
| 2021 Q at T-180, Available at T-60 | 0.901 [0.879, 0.921] | 805 |
| 2023, stored "T-60" Q | 0.494 [0.469, 0.519] | 2,045 |
| 2024, stored "T-60" Q | 0.475 [0.451, 0.499] | 1,972 |

The stored 2023-24 Q rate (0.47-0.49) looks like the 2021 T-180 Q rate (0.54), not the T-60 rate (0.66). A live true-T-60 Q is a **different, smaller, more informative group** (fewer Q, higher P(play), the OUT-bound third already moved to OUT). Consumers that read the Q/D tag (P(play) shadow R, AVG shadow, the lineups T-30 analysis) are trained on one meaning and served another. Production props read only OUT (`statuses=("out",)`), so they do not have this problem; their problem is section 5.

## 5. Production's errors on the affected rows (2023-24, n 55,202 rows, 2,633 games; box rows only)

Production arms: minutes `P` (minutes_scores.npz) and points `A0` (props_pts_A0.npz). Selection caveat: only players who played are scored, so a player who was Q at the used snapshot and turned OUT is never in this table (T484 wound); the table is about the rows that remain.

| group | n rows | minutes CRPS in vs out, diff [95% CI] | points CRPS in vs out, diff [95% CI] |
|---|---|---|---|
| listed Q/D at the used snapshot | 1,929 | 4.00 vs 4.30, -0.30 [-0.45, -0.15] | 3.53 vs 3.16, **+0.375 [+0.245, +0.508]** |
| teammates of a rotation Q/D (his team has >= 1 at the used snapshot) | 20,464 (37.1%) | 4.41 vs 4.22, +0.193 [+0.119, +0.266] | 3.25 vs 3.12, +0.129 [+0.082, +0.177]; bias +0.44 pts [+0.35, +0.54] higher |

Sensitivity of production's own mean to the rotation-OUT count (what a late OUT would change), points mean at 0 / 1 / 2+ rotation OUTs, by as-of minutes stratum: min10 >= 25: 16.10 / 16.20 / 16.52 (n 4,409 / 6,978 / 13,410); 15-25: 6.80 / 7.16 / 8.44; < 15: 2.99 / 3.30 / 4.72. Production's minutes mean for min10 >= 25 *falls* with the OUT count (30.7 / 30.4 / 29.5) while realized minutes rise (30.8 / 30.9 / 31.1): the known low-median bias, not a staleness effect.

**Bound, not measurement.** A stale list that misses one late rotation OUT shifts a teammate's points mean by delta; with a Gaussian forecast sd of about 5.6 (implied by the 3.15 CRPS) the CRPS cost per affected row is 0.0045 at delta 0.3, 0.013 at 0.5, 0.050 at 1.0, 0.20 at 2.0. Times an exposure of at most ~0.27 of team-games: **0.001 (delta 0.3) to 0.014 (delta 1.0) all-rows CRPS**, where the practical floor is 0.005. The aggregate sensitivity above says delta is mostly 0.1-0.5 pts (bench OUTs dominate the count), but a star's late OUT moves teammates by far more, and the aggregate cannot separate them. So **the effect cannot be bounded below the floor from data we hold**, and it also cannot be shown to exceed it.

## 6. Which way does the skew run, and who pays?

* **Production as served (OUT-only):** live uses the fresher list; the model was trained on staler lists. A model trained on a noisy OUT feature attenuates its coefficient, so live under-reacts to a fresh OUT rather than over-reacting. The harm is a second-order calibration cost (and a missed gain), not a bias that makes live worse than the backtest. Direction: benign. Size: unknown, between ~0.001 and ~0.014 all-rows by the bound.
* **Tag consumers (shadow models):** a real calibration shift (0.54 -> 0.66 for Q, different group sizes). The live log of PPLAY_INTO_PROPS_LIVE will expose it for free.

## 7. Is it worth a rule? Verdict

Not negligible: the status churn is large (about half a rotation OUT per game in the final two hours of the one season we can see), the stored lead is 2-5 hours and the bound cannot exclude the floor. Worth **one** rule, but the rule cannot run on the data we hold: **the minimum-data gate fails on the stored table** (fresh-lead share 3.9% / 4.9% against a >= 80% threshold, `Thu Oct 15 13:05:38 CDT 2026` to `13:05:39`, `reports/prereg_injury_timing/gate.json`). The information-correct fix is to train on the same selection live uses (latest stamp <= tip - 60 from the real intraday sequence), which needs the intraday history to be repaired first; the alternative of serving a deliberately old report to match training throws away the 37.7% of Q/D that resolved to OUT and is rejected. Draft: `docs/prereg/INJURY_TIMING.md`. Recommendation in section 8.

## 8. Recommendation (opinion; Devin decides)

1. No production change before opening night, as Devin said. The skew in production-as-served points the benign way.
2. Spend 250 requests (about 3 minutes from Devin's shell) on the pilot probe before anything else; if no 2023-24 slot exists between 17:15 and tip - 60, the rule is dead on data and the live 2026-27 log is the only source of a true T-60 tag. If slots exist, the repair is a re-pull, not a model change.
3. Do not buy the repair for production props alone: the prior that the props test reaches -0.005 is about 0.07 (see the rule's priors). The repair is worth it only if Devin also wants the tag consumers (P(play), AVG) trained on matched-lag tags; for those the live log is cheaper and clean.
4. First live fact to collect: lead per game of the 2026-27 pulls (distribution), because the live "T-60" may itself be T-90 to T-150.

## What I could not verify

* The CDN contents for 2023-24 intraday slots (network not reachable from the agent shell; WebFetch timed out).
* The live lead distribution (no 2026-27 pulls exist).
* That the 2021-22 transition rates carry to 2023-24 (regime differences listed above).
* Whether late-OUT misses in the stale list change production's forecasts for stars' teammates by more than the aggregate sensitivity shows (needs a refit with the repaired table; that is the rule).

## 12. Revision 2 (2026-10-15 16:2x CDT): the intraday repair, gate and the true T-180 to T-60 gap

Inputs: scratch duckdb `data/scratch/injury_intraday.duckdb` (sha256 `24f0e1a5...bc100`, 353,809 rows, hourly 12:00-21:00 ET, 4,194 PDFs, 0 failures; `nba/ingest/injury_intraday.py`), unioned with the stored table; read-only; `season <= 2024`; season 2025 not loaded. Script `research/scratch/injury_timing_gap.py`, table `reports/injury_timing/gap_2023_24.json` (seed 2026, B = 2000, game-clustered).

**Gate (same thresholds as before, run `Thu Oct 15 16:29:44 CDT 2026` to `16:29:45`): PASS.** Used-lead share <= 90 min 1.000 / 0.986 (2023 / 2024), lead p10/p50/p90 60/60/90, 15.4 / 15.1 stamps per game, OUT set differs from the stored snapshot in 613 / 791 of 1,317 games.

**True latest <= T-180 vs latest <= T-60 (stamp gap 2 h in the median game), events per game, both teams:**

| event | 2023 (n 1,308 games) | 2024 (n 1,300 games) | 2021-22 proxy (superseded) |
|---|---|---|---|
| OUT added, all | 1.04 [0.97, 1.12] | 1.12 [1.05, 1.18] | 0.84 |
| OUT added, rotation | 0.56 [0.51, 0.60] | 0.64 [0.59, 0.69] | 0.53 |
| OUT removed, rotation | 0.003 [0.000, 0.007] | 0.008 [0.002, 0.015] | 0.015 |
| Q/D to OUT, rotation | 0.51 [0.47, 0.56] | 0.59 [0.54, 0.64] | 0.43 |
| Q/D cleared, rotation | 0.54 [0.50, 0.58] | 0.50 [0.46, 0.55] | 0.42 |
| any change, rotation | 1.48 [1.41, 1.56] | 1.60 [1.52, 1.68] | 1.18 |

Players listed Q or D at T-180 and OUT at T-60: 44.9% (1,145 of 2,550) in 2023, 47.5% (1,210 of 2,549) in 2024. Tag meaning, P(played | Q) with game-clustered CI: Q at T-180 0.484 [0.462, 0.507] (n 2,287) / 0.467 [0.444, 0.489] (2,240); Q at T-60 0.604 [0.542, 0.670] (336) / 0.547 [0.483, 0.612] (254); Q at T-180 that became OUT 0.000 (911 / 941); became Available 0.862 / 0.858 (1,057 each). The 2021-22 proxy understated the OUT flow (and Q/D to OUT is larger than the proxy by 0.08-0.16 per game); direction of every conclusion in sections 3-6 is unchanged, and the stale-list exposure is about twice what section 5's bound assumed, so the all-rows bound there (0.001 to 0.014) should be read with the upper end more plausible.

## 13. Revision 3 (2026-10-16 CDT): the corrected header clock, the live cutoff and the true T-180 to T-60 gap

Pre-freeze review B1: hour-only report PDFs are headed HH:30 but were stamped HH:00 (190 of 190 sampled; `HH_MM` files exact). Sections 1-12 above are on the filename clock and are superseded here. Inputs: `data/scratch/injury_intraday_v2.duckdb` (sha256 `0e64c257...919b1`; all 4,194 raw PDFs re-parsed with known-at = header time, 0 errors, 353,809 rows, 4,811 unmatched name entries; `research/eval/injury_timing_reparse.py`), the stored table with every stamp moved to its audited header time (1,261 stored PDFs audited, 0 mismatches), restricted to players with a box row in a season <= 2024. Season labels are start years: 2023 = 2023-24, 2024 = 2024-25; 2025 (2025-26) not used. Scripts `research/scratch/injury_timing_gap_corrected.py`, gate `research/eval/injury_timing_gate.py`; tables `reports/injury_timing/gap_corrected_2023_24.json`, `reports/prereg_injury_timing/gate.json` (seed 2026, B = 2000, game-clustered).

**Lead of the stored snapshot on the header clock (production's training rule, `tip - 60`):** p10 / p50 / p90 = 90 / 150 / 270 min in both seasons (was 120 / 180 / 300); 3.8% (2023) and 4.8% (2024) of games use a report published after tip - 60 (production leak, outside this rule).
**Lead of the live-cutoff list (R_live):** 90 / 90 / 120; 99.8% / 99.4% of games within 120 min, 66.9% / 65.4% within 90.
**True latest <= tip-180 vs latest <= tip-60 on R (stamp gap exactly 2 h; n = 1,306 / 1,296 games), events per game, both teams:**

| event | 2023 | 2024 | superseded rev 2 (mis-stamped) |
|---|---|---|---|
| OUT added, rotation | 0.32 [0.29, 0.36] | 0.33 [0.30, 0.37] | 0.56 / 0.64 |
| OUT added, all | 0.53 [0.48, 0.59] | 0.58 [0.54, 0.63] | 1.04 / 1.12 |
| OUT removed, rotation | 0.002 [0.000, 0.004] | 0.003 [0.001, 0.006] | 0.003 / 0.008 |
| Q/D to OUT, rotation | 0.30 [0.26, 0.33] | 0.31 [0.27, 0.34] | 0.51 / 0.59 |
| Q/D cleared, rotation | 0.24 [0.21, 0.27] | 0.24 [0.21, 0.27] | 0.54 / 0.50 |
| any change, rotation | 0.78 [0.72, 0.84] | 0.83 [0.76, 0.89] | 1.48 / 1.60 |

Players listed Q or D at T-180 and OUT at T-60: 22.7% (588 of 2,595) in 2023 and 24.0% (617 of 2,569) in 2024 (rev 2: 44.9% / 47.5%, which mixed in the 30 minutes of leaked future). P(played | Q): at T-180 0.481 [0.458, 0.502] (n 2,325) / 0.467 [0.444, 0.489] (2,257); at T-60 0.519 [0.490, 0.547] (1,416) / 0.490 [0.461, 0.519] (1,304); Q at T-180 that became OUT 0.000 (466 / 485); became Available 0.818 / 0.821. The tag-meaning shift is about +0.02 to +0.04, not +0.12 to +0.14. **The skew is real but about half the size rev 2 said, and the leak was most of the "large" part.**

**Exposure of the fix on the live cutoff (gate):** games whose OUT set differs from the stored lists: 174 of 1,316 (2023) and 197 of 1,303 (2024, after restricting to players with a history; 322 unrestricted). That is 13-15% of games, below the pre-declared 300 for 2024. See the rule's section 7 and section 4 ("What a failed gate means").
