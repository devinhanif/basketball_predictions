# Red-team review: production models (2026-10-09)

Targets: `rung0_injury_elo v1` and `props_context_residual v1`. Both were promoted after a
walk-forward win plus one pre-registered 2025 touch, and neither had been attacked until now.

Scope and rules followed: seasons <= 2024 only (no 2025 rows loaded by any attack; loaders
filter `season <= 2024`, and the props availability frame was inner-joined to <= 2024 games).
`nba.duckdb` was opened `read_only=True` and closed straight after loading. No source, config,
registry or ledger was modified. CPU only. Heavy fits ran under `data/ops/heavy.lock`.
Attack scripts are in the session scratchpad
(`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/7ff798e7-932b-4807-b103-45d365238231/scratchpad/rt/`),
each run as `uv run python <script>` from the repo root. Real tip-off times come from the cached
schedule (`data/schedule/raw_{2022-23,2023-24,2024-25}.parquet`, `gameDateTimeEst`, 3,953/3,953
games matched, 0 date mismatches) and are written by `tips.py`.

## Verdicts

| model | verdict | one-line reason |
|---|---|---|
| `rung0_injury_elo v1` | **SURVIVES** | Every attack passed. Gating the report on the real tip-off instead of the 19:00 proxy does not shrink the gain (-0.00786 becomes -0.00812). |
| `props_context_residual v1` | **WOUNDED (minor)** | The gain is real and passes every leak attack. But the 19:00 tip proxy lets a **post-tip** 17:00 report into ~7.5% of scored rows, which inflates the pts gain by a significant 0.0031 CRPS (3.5%). That inflation is concentrated in early-tip games (pts: -0.180 with the proxy vs -0.139 with the real tip). The OOF and the 2025-holdout magnitudes are overstated by about that much. |

**Top finding.** The backfill holds 11:00 and 17:00 ET snapshots, and the 19:00 ET proxy with a
60-min lead admits the 17:00 one for every game. 306 of 3,953 games in 2022-24 (7.7%) tip at or
before 17:00 ET, so for them the snapshot used was published after tip-off. Those snapshots
still list the early game, carrying game-time decisions. Examples: rotation players who were
"questionable" at 11:00 and DNP were flipped to OUT at 17:00 at a rate of 0.45 -> 0.76 (13h tips)
and 0.53 -> 0.84 (15h tips), versus 0.55 -> 0.55 for 20h tips. Of 431 early-game players newly OUT
at 17:00, every one sat (0 played), so the snapshot carries no in-game injuries. It is T-0
information, not post-game information. The live/forward paths (`predict_games`,
`predict_slate_context`) already gate on the real tip minus 60 (verified below), so the defect
affects backtests, the 2025 holdout numbers and training data, not live serving.

**Single most likely failure mode if a claim is wrong:** the tip-off proxy. Any as-of rule
anchored to `game_date + 19:00` instead of the real tip mis-dates information for matinee,
weekend, holiday and playoff games. Those games are over-represented exactly where gains look
largest (props: playoffs pts -0.21, early tips -0.18).

---

## 1. `rung0_injury_elo v1`

Reproduction: `elo_attacks.py` arm A, using the production functions unchanged on <= 2024. The
log-loss delta vs MOV-Elo is **-0.007861 [-0.01189, -0.00360]**, n = 3,951 games. That is
bit-identical to the stored `data/injury_elo/results.json`. Final coefs (b_out, g_doubt) =
(0.308, 0.0007).

| # | attack | what was run | result | pass/fail |
|---|---|---|---|---|
| 1 | Shuffled features | permute (`d_out`,`d_doubt`) across games within date, 5 seeds, full walk-forward refit | delta +0.00076, +0.00105, +0.00038, +0.00041, +0.00132 (each CI spans 0) | **PASS** (gain vanishes) |
| 1b | Shuffled labels | permute y within date, refit, score on permuted y, 3 seeds | delta -0.0033 [-0.0057,-0.0009], -0.0038 [-0.0065,-0.0013], -0.0019 [-0.0040,+0.0001] | Inconclusive by design. With random labels the frozen Elo offset is badly overconfident, so any coefficient correlated with the Elo logit shrinks it. Attack 6 rules out recalibration as the source of the real gain. |
| 2 | Planted future, report | add an `out` row at 18:01 for every game on 2024-01-15 (proxy path), with a 17:59 control | 18:01: 0/11 games changed; 17:59: 11/11 changed | **PASS** (cutoff enforced). The control also shows the proxy accepts a 17:59 stamp for a 13:00 tip. |
| 2b | Planted future, box | same-day monster box rows (comp 500, 48 min) for every player on D | games on D changed: 0/11; games after D changed: 16/17 (sanity) | **PASS** |
| 2c | Planted future, live path | `predict_games` on 2025-03-02 (season 2024; tips 13:00-21:30), star OUT stamped at real tip -59 vs -61 min | -59: 0/9 games use it; -61: 9/9 | **PASS**. The live path uses the real tip. |
| 3 | Missingness | `has_report` rate by home win/loss; nonzero `d_out` rate by outcome | has_report 0.9991 vs 0.9989; nonzero d_out 0.9746 vs 0.9777 | **PASS** (no outcome asymmetry) |
| 3b | OUT list vs post-hoc DNP | 17:00 OUT-list precision/recall vs box-score DNPs (rotation players, as-of avg >= 20 min) | precision 0.99-1.00 (only 11 of 1,292 OUT-listed rotation player-games with tip >= 19:00 played). Recall of DNPs: 0.45 (tip >= 19), 0.71 (tip <= 17). corr(`d_out`, ORACLE box-DNP value) = 0.27. ORACLE log loss gain is only -0.0020 vs the report's -0.0079. | **PASS.** `d_out` is not the inactive list: the post-hoc oracle is 4x weaker. The 0.45 vs 0.71 recall gap is the proxy issue below. |
| 4 | Knockout | OUT-only variant (stored) | -0.00793 vs -0.00786 with doubtful | Doubtful carries nothing. The whole gain is in `d_out`, so its as-of logic was audited line by line (`build_value_state`, `asof_values`, `latest_pretip_flagged`, `rotation_flagged_by_team`). Values come from games with `game_date <= D-1`; the snapshot is the latest one <= cutoff; the team is the one from the player's last prior game. No defect beyond the proxy. |
| 5 | Time shift (report earlier) | 11:00 snapshot only | -0.00250 [-0.00452, -0.00042] | Expected drop (~7 h staler). Gain survives. Most of the value is in the late-afternoon report, which is pre-tip for tips >= 18:00. |
| 5b | Time shift (values) | player values lagged one extra day | -0.00789 [-0.01193, -0.00359] | **PASS** (unchanged, so nothing sits too close to tip) |
| 5c | **Real tip-off gate** | report rows kept iff `as_of <= real tip - 60 min` (3,090 rows dropped; `d_out` changed in 186 games) | overall **-0.00812 [-0.01212, -0.00379]**; early-tip (<= 17:00) slice -0.0070 [-0.0245, +0.0101] vs -0.0031 with proxy, n = 306 | **PASS.** The proxy did not inflate the Elo gain; the real-tip version is marginally better. |
| 6 | Baseline fairness | monthly walk-forward Platt (intercept + slope on Elo logit) on BOTH sides | Platt base vs raw MOV-Elo +0.0017 [-0.0003, +0.0038] (recalibration alone does not help); Platt+injury vs Platt base **-0.00832 [-0.01243, -0.00392]** | **PASS.** The gain is not recalibration. The MOV-Elo baseline was itself GA-tuned on 2022-24 OOF, which advantages the baseline. |
| 7 | Replication | per season; drop best month (2025-01); drop top team (NOP, 1610612740) | 2022 -0.0063 [-0.0138, +0.0005]; 2023 -0.0055 [-0.0131, +0.0018]; 2024 -0.0118 [-0.0185, -0.0048]; drop best month -0.0072 [-0.0119, -0.0027]; drop NOP -0.0063 [-0.0107, -0.0017]; playoffs -0.0052 [-0.0164, +0.0065] (n = 250); regular -0.0080 [-0.0129, -0.0033] | **PASS** (sign holds everywhere). Single seasons 2022/2023 are individually underpowered (CIs touch 0). |
| 8 | Too good to be true | accuracy, size of gain | accuracy 65.6%; log-loss gain 0.008 (1.3%); no playoff concentration | **PASS** |
| 9 | Holdout hygiene | git order of `HOLDOUT_ACCESS_LOG.md` vs result file | prereg row committed 11:18:10 (12ea1ec); `data/injury_elo/holdout_2025.json` written 11:18:58; result row 0c80b51 at 11:19:26; procedure commit 84848f9 at 11:17:57. Code drift since then is additive only (forward path, optional args) and reproduces the stored OOF exactly. | **PASS**, with a caveat: the holdout run used the 19:00 proxy. Per attack 5c this is harmless for Elo. |

Caveat. Bootstrap CIs here are game-level (one row per game). Bootstrap seed 0; the ridge
fit is deterministic, so a "different seed" changes only the bootstrap.

## 2. `props_context_residual v1`

Reproduction: `props_build.py` builds the feature frame with the production
`build_features`; `props_wf.py prod` runs the production month-block walk-forward over
2023-24. Against `recency_conformal` the pts delta is **-0.0893 [-0.0977, -0.0812]**; reb
-0.0312, ast -0.0234, fg3m -0.0106; n = 55,202 played rows per stat. That is identical to the
stored `reports/context_residual/report.txt`. All props CIs are game-clustered bootstraps
(1,000 resamples).

Comparator note. The pre-registered OOF rule compared against the recency **Normal** (pts
-0.1153). That headline includes ~0.026 of pure conformal-shape gain. The fair comparator is
`recency_conformal`, which gets the same conformal machinery, the same rows, the same played-only
DNP handling and the same training window; it was the 2025 holdout's comparator. All numbers below
use it.

| # | attack | what was run | result (pts / reb / ast / fg3m, delta vs recency_conformal) | pass/fail |
|---|---|---|---|---|
| 1 | Shuffled labels | permute training residuals within date, then refit per month | +0.0060 [+0.0003,+0.0116] / +0.0129 / +0.0124 / +0.0127 | **PASS** (gain vanishes; candidate becomes worse) |
| 1b | Shuffled vacated block | permute (`n_out_rot`,`vac_min`,`vac_*`) across (game, team) within date | -0.0588 / -0.0233 / -0.0179 / -0.0079 | **PASS.** Equal to the vacated knockout (-0.0612 / ...), so the vacated gain needs the true team mapping. |
| 2 | Planted future, box | on 2024-12-27: tonight's pts/reb/ast/fg3m x10, minutes 48, starter flipped | 0 changed feature cells over 173 rows and all 66 production features (union over 4 stats) | **PASS** |
| 2b | **DNP flip** (the suspicion) | mark a random 40% (69) of tonight's played players as DNP; compare the other 104 rows | 0 changed feature cells | **PASS.** No feature depends on who actually played tonight. |
| 2c | Planted future, score | tonight's home score +60 | `exp_margin`, r20 and opp features unchanged (2 NaN-vs-NaN debut rows flagged only by polars NaN ordering; an identical rebuild shows the same 2) | **PASS** |
| 3 | Missingness | null rate of every production feature by tonight's minutes < 5, tonight's starter, team win/loss, pts > median (n_prior >= 5, 2023-24) | only `vac_*`/`n_out_rot`/`vac_min` are ever null (0.065%, games with no report); max absolute gap across all features and groups = **0.00025** | **PASS** (the `opp_adjusted_ridge` pattern is absent) |
| 3b | Vacated = pre-tip OUT only? | code audit plus data check | `vacated_features` uses only `flagged` (the latest usable official-report snapshot, `source='nba_official_report'`, statuses `out`); values come from `post` state at the last played game <= D-1. 45 of 28,309 OUT-listed player-games actually played (so the list is not a DNP list in disguise), and ~36% of rotation DNPs (1,067/2,925) never appear on any snapshot. | **PASS** (computed only from the pre-tip report, never from actual DNPs, apart from the proxy issue) |
| 4 | Group knockout | drop one group, full walk-forward refit | vac -0.0612 / -0.0238 / -0.0183 / -0.0081; minutes+role -0.0737 / -0.0221 / -0.0183 / -0.0078; team/opp ctx -0.0791 / -0.0287 / -0.0199 / -0.0098; changepoint/gap -0.0793 / -0.0272 / -0.0216 / -0.0099; own multi-horizon history -0.0839 / -0.0305 / -0.0228 / -0.0097 | **PASS.** No single group carries the gain; the largest (vacated) accounts for ~31% of pts and ~24% of reb. |
| 5 | Time shift: report earlier | 11:00 snapshot only | -0.0740 [-0.0814,-0.0674] / -0.0263 / -0.0204 / -0.0087 | Expected drop; gain survives |
| 5b | **Real tip-off gate** | report rows kept iff `as_of <= real tip - 60` | **-0.0862 [-0.0940,-0.0784]** / -0.0313 / -0.0229 / -0.0102 | **FAIL (minor)**: see the paired table below |
| 6 | Baseline fairness | recency_conformal = same conformal shape, same rows, same played-only handling, same window | already the comparator; the candidate still clears the -0.005 floor by 2x-18x | **PASS** |
| 7 | Replication | seed 1; season 2023 vs 2024; drop best month (2025-04); drop top team | seed 1 -0.0887 / -0.0311 / -0.0236 / -0.0107. 2023: -0.0759 [-0.0861,-0.0654] / -0.0264 / -0.0207 / -0.0086. 2024: -0.1027 [-0.1145,-0.0902] / -0.0360 / -0.0261 / -0.0127. Drop 2025-04: -0.0781 / -0.0280 / -0.0212 / -0.0098. Top team holds <= 6.2% of the gain; dropping it gives -0.0873 / -0.0305 / -0.0230 / -0.0104. | **PASS** (sign and floor hold everywhere) |
| 8 | Too good to be true | concentration | playoffs (n = 3,488): **-0.210** [-0.238,-0.179] / -0.063 / -0.076 / -0.034, i.e. 2.4x the overall pts gain; bench -0.119 vs starters -0.052 | **Explained, not a leak.** Playoff bench players score 1.7-2.0 pts below their recency mean (2022: -1.95; 2024: -1.73), and `team_game_no` >= 80-82 identifies playoff games, which is known before tip. Removing April 2025 leaves the gain intact. The CRPS gain is 2.7% of baseline CRPS, below the T-30 lineup result in scale. |
| 9 | Holdout hygiene | git order | prereg row ec71ca0 at 11:30:24 (same second as model commit 03b33ea); `reports/context_residual/holdout_2025.json` written 11:32:00; result row af45a5c at 11:32:26 | **PASS**, with caveats: (a) the holdout used the 19:00 proxy, so its magnitudes inherit the inflation; (b) `reports/context_residual/report.txt` and the OOF parquet were rewritten at 11:59, after the holdout touch. That code path is hard-capped at season <= 2024 and reproduces exactly, so this is cosmetic, but artifacts should not be regenerated after a confirmatory touch without a note. |

Paired candidate(proxy) - candidate(real tip), game-clustered, from `props_wf_prod` vs
`props_wf_realtip`:

| stat | all rows (n = 55,202) | early tip <= 17:00 ET (n = 4,161) | tip > 17:00 (n = 51,041) | gain vs recency_conformal, early: proxy -> real tip |
|---|---|---|---|---|
| pts | **-0.0031 [-0.0056, -0.0009]** | **-0.0405 [-0.0669, -0.0186]** | -0.0000 [-0.0015, +0.0016] | -0.180 -> -0.139 |
| reb | +0.0001 [-0.0007, +0.0008] | -0.0056 [-0.0121, -0.0004] | +0.0006 | -0.062 -> -0.056 |
| ast | -0.0006 [-0.0013, +0.0000] | -0.0064 [-0.0138, -0.0008] | -0.0001 | -0.039 -> -0.033 |
| fg3m | -0.0005 [-0.0008, -0.0001] | -0.0022 [-0.0049, +0.0002] | -0.0003 | -0.014 -> -0.012 |

The defect is real but bounded. Late games are untouched, and the corrected overall gains are
pts -0.0862, reb -0.0313, ast -0.0229, fg3m -0.0102, all still far past the -0.005 floor. By
analogy, the 2025 holdout pts figure (-0.1206) is probably overstated by about 0.003-0.004. That
cannot be re-measured without another 2025 touch, which the rules forbid.

## 3. Other findings (no verdict impact)

1. **Report coverage gap.** About 36% of rotation-player DNPs (1,067/2,925, 2022-24) appear on no
   report snapshot; for 437 of them the team has no rows in that game's report at all.
   `unmatched.csv` holds only 395 pre-2025 names, so most of the gap is missing team sections or
   parse misses, not name resolution. This is not a leak; it attenuates both models.
2. **Train/serve skew in report freshness.** The backfill has two snapshots a day (11:00, 17:00),
   while the live puller sees roughly every 15 minutes up to tip - 60. Coefficients and trees are
   trained on staler information than they are served. The direction is conservative (live
   should do at least as well), but the live `d_out`/`vac_*` distribution will differ from the
   training one. Watch forward calibration.
3. **Platt P(play)** (`P_PLAY_PLATT = (-0.327, 0.581)` in `nba/props/forward.py`) was fit on 14
   sampled 2023-24 slates and checked on 28 slates of 2024-25. Neither the OOF rule nor the 2025
   holdout tests it (both score played rows only). The unconditional `mean_uncond`/`p_ge_uncond`
   outputs therefore rest on a 2,405-player fit with no confirmatory test.
4. The holdout log labels both touches "CLEAN first touch" while its own status header says "NO
   VIRGIN HOLDOUT". The candidates were never scored on 2025, but some design inputs were chosen
   with 2025 visible elsewhere: MOV-Elo params were confirmed on 2025; played-only and recency
   choices came from work that touched 2025. "Clean for this model, not for the research program"
   is the accurate label.

## 4. Recommended fixes (not applied; maintainer decision)

1. **Replace the 19:00 tip proxy with real tip times everywhere outside the live path.**
   `ReportTriggerConfig` and `usable_report_rows` should take a per-game tip column. The data is
   already cached in `data/schedule/raw_*.parquet` (`gameDateTimeEst`). Alternatively, ingest
   `tipoff` into `games`. Re-run both OOF evals and record the corrected numbers as new
   `TEST_LEDGER.md` rows. Do not re-touch 2025; annotate the two holdout rows as "proxy-gated;
   early-tip inflation estimated at ~3.5% of pts gain for props, nil for Elo".
2. Add a regression test in which an early-tip game (e.g. 13:00) with a 17:00 report row must
   **not** use that row when the real tip is known. The existing planted-future tests pass
   because they test the proxy cutoff, not the true tip.
3. Make `data-check` / the missingness audit also compare feature null rates and value
   distributions by tip bucket (<= 17:00 vs later). That would have surfaced this mechanically.
4. Investigate the 437 missing team sections in the report backfill (page-parse coverage), then
   rerun both models' evals.
5. Report props headlines against `recency_conformal` only. The Normal-baseline delta overstates
   the context gain by ~0.026 pts CRPS.
6. Pre-register a forward (2026-27) check of the Platt P(play) layer, or of unconditional
   threshold log loss, before relying on `p_ge_uncond` for parlays.
