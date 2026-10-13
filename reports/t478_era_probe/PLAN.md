# T478 era-embedding probe: descriptive plan

Status: COMMITTED 2026-10-12 20:07 CDT (clock read by `date`; main session; Devin: "do the era probe first"). No run before this commit.
Kind: descriptive. No hypothesis test, no verdict, no family, no floor. It has no bearing on the T478 verdict (DECISIONS 2026-10-12 17:19, commitment 2). Not a holdout touch.
Follows: Claude's recommendation 6 in `docs/reviews/T478_post_result_2026-10-12.md` ("run the committed era-embedding probe and cite T443").
Lineage: ENSEMBLE E3 (T443) was disqualified by P-emb (AUC 0.998 for 2019 v 2022 and 2020 v 2022, above the limit). SEQ_FULL_MIX_RT is the same W_ALL construction with explicit era channels, and no P-emb guard was carried over (the guard was dropped at its freeze without being stated). Prior placebo evidence: the P-emb probe is weak (value AUCs of 0.96-0.998 even between adjacent modern seasons, HW3), so only placebo-relative numbers are read.

## What it measures
Whether the state that the final W_ALL sequence model builds for a target row (the input to its quantile head) lets a linear classifier tell the season it came from, and whether history seasons are told apart more than two modern seasons the same distance apart are.

## How
* **Model**: SEQ_FULL_MIX_RT run-#1 program WALL_E2, unchanged code (`run_walk_forward` of `research/colab/jobs/seq_full_mix_rt/seq_full_mix_rt_tail.py`, sel mode): hidden 64, cold fit on 2019-20, 2020-21, 2021-22 and 2022-23 rows before 2023-04-01 (early stopping on the rest of 2022-23), then warm refit (3 epochs, lr 3e-4) before every calendar month of **2023-24 and 2024-25** (18 blocks), as ENSEMBLE's E3 walk. **Seed 0 only** (net 0 of the 3-seed run: same cold and warm seeds; GPU nondeterminism aside, the same net). ENSEMBLE also read net 0.
* **Seasons, labels**: 2019 = 2019-20, 2020 = 2020-21, 2021 = 2021-22, 2022 = 2022-23, 2023 = 2023-24 (select), 2024 = 2024-25 (report season of ENSEMBLE; not the holdout). Season 2025 (2025-26) is the frozen holdout: the export has none, the base loader refuses it, and the job and reader assert no season above 2024. Devin's decision (main session, 2026-10-12): use the committed form including 2024-25.
* **States**: the input of `net.head[0]` (eval mode; 128-d = target token (64) concatenated with the mean-pooled history (64). ENSEMBLE's text said "64-d target-token state" but its code dumped this 128-d vector, and this probe does the same as primary, reporting the target-token half alone as a second reading) after the last warm refit, as ENSEMBLE did for E3, for up to 6000 random rows (rng seed 0) per season 2019-2023 with `n_prior >= 5`. Saved with game id, season, `era_zero` (all of era_bubble, era_nofans, era_limited equal 0) and `has_report`.
* **Classifier** (Mac, `research/eval/t478_era_probe.py`): standardised logistic regression (C = 1), trained on games with `sha256(game_id) % 3 != 0`, AUC on games with `% 3 == 0`, game-clustered 95% percentile interval, B = 200, seed 0.
* **Pairs** (class a v class b): history v 2022: 2019v2022, 2020v2022, 2021v2022 (ENSEMBLE had 2019 and 2020; 2021-22 added because W_ALL contains it), and **2019z v 2022** (2019-20 rows whose era channels are all zero, i.e. before the bubble restart: the pair in which no explicit era channel distinguishes the seasons). Placebo, as ENSEMBLE committed: 2022v2023, 2022v2024, 2023v2024. Null: a random half of 2022 games v the other half (expected about 0.5, a check on the code).
* **Reading rule, written now** (ENSEMBLE's committed P-emb text, followed exactly): limit = AUC(2022v2024) + 0.02 (history is 2-3 seasons from 2022, so the placebo at the same two-season gap is the fair one; the 2019 gap of three seasons makes the limit conservative against history). A history pair is "flagged" when its interval LOWER end exceeds the limit. Flags are reported as such; they trigger nothing. The other placebos (2022v2023, 2023v2024) are reported for context and set no limit.

## How it will be read (written before any number exists)
1. The 2020v2022 and 2021v2022 pairs are expected to be flagged by construction: 2020-21 rows carry `era_limited = 1`, and 2021-22 rows carry `has_report = 0`; the model is given those channels as inputs. Their flags are not evidence about hidden era reading.
2. **2019z v 2022 is the informative pair.** Flagged: the model separates seasons beyond the explicit era tokens, as E3 did (T443); the T478 history gain then cannot be separated from era reading by this probe, and the follow-up the adversary recommended (a CPU model given the same 2019-21 history, 2023/24 only) becomes the next step. Not flagged: the explicit era tokens and the dropped 2021-22 report tokens do not leave a larger-than-placebo trace in the embedding; this is one-sided comfort, because the probe is weak (HW3) and a linear read of the final state is not the whole model.
3. The null pair must read within 0.45-0.55; otherwise the code is wrong and nothing is read.
4. Whatever the outcome: no change to T478 (PASS stands as a production-change proposal), no new guard retro-fitted, at most the one follow-up already named above (a new id).

## What it cannot say
* **The reversal condition in the T478 review ("2025 rows read as a history era") needs 2025 states, which need the 2025 export and a fresh read of season 2025 inputs.** This probe has none. Era channels are 0 on all 2025 rows (as for 2022-24), so the 2022-24 placebo pairs are the nearest proxy. A 2025 state read would be a new holdout-log row before anything is run; it is not requested here.
* The T478 held-out model (2025 run) is not this net: this net is the run-#1 selection net (cold fit on pre-2023-04 rows, warm through 2024). Same construction and code, different pools.
* States of 2023-24 and 2024-25 rows come from a model that was warm-fit on most of those rows (pool: everything before the last block's month); the same holds for ENSEMBLE's E3 probe, so placebo and history pairs are treated alike.

## Gates (checked before the run, on the real data)
* Data exists and holds nothing after 2024: `data/colab/seq_full_mix_rt/seq_ensemble_rt_cur.npz` seasons 2022-2024 (27,737 / 28,197 / 28,236 rows) and `seq_ensemble_rt_hist.npz` seasons 2019-2021 (24,097 / 25,053 / 28,045). Rows per season with `n_prior >= 5` exceed 6000 (ENSEMBLE's gate counted 126,474 such rows). The notebook asserts no season > 2024 and refuses a 2025 row (base loader).
* Cost: run #1 WALL_E2 took 418.8 s for 3 seeds (about 140 s per seed, 18 warm blocks, the same blocks as this run): **about 7 T4 minutes (5-12) including load, the state dump and Colab startup**; ceiling 15.
* Equivalence: the job calls the rt tail's `run_walk_forward` unmodified; a CPU smoke test proves the `fit` wrapper changes no OOF value against a direct call.

## Ambiguity for Devin
Resolved by the main session: the committed form (2024-25 included) is used. Remaining note: ENSEMBLE's text said "64-d target-token state" but its code dumped the 128-d head input; both are reported.

## Playbook check
Neural sequence model, W_ALL. (1) Information or capacity: neither; this reads the embedding, no forecast. (2) Data: W_ALL has six seasons; one seed is enough for a state read. (3) Test validity: three placebo pairs, a null pair, game-level hold-out, clustered CI; the probe is weak (HW3), so a non-flag is not proof. (4) Lineage: ENSEMBLE E3 / T443, SEQ_FULL_MIX_RT; the explicit era channels change the reading of 2020 and 2021, hence 2019z. (5) Scored against: placebo seasons at the same gap; no CRPS.

=== RESULTS BELOW ===
