# COMBO_LINE_CAL: does a LINE_CALIBRATION-style map calibrate P(over) at book main lines for combos and stocks? (pre-registration)

Status: **DRAFT, revision 2, written by the modeler Thu Oct 15 2026 (clock read by `date` in the same command as the final gate run: `Thu Oct 15 10:33:10 CDT 2026`). Nothing is frozen, fitted or scored.** Revision 1 (hash 20f085c4, merged at f8fc330) found no main-line combo price in the store and was not freezable. The main-line markets were pulled and ingested on 2026-10-15 (DATA_CHANGELOG 2026-10-15; 1,731,549 t60 rows); this revision re-measures the gate on them, and the family F is set by that gate.
- **How it becomes the rule:** the main session commits `prereg(COMBO_LINE_CAL): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/COMBO_LINE_CAL.md | shasum -a 256`. After that commit, and after the adversary has read the harness and the synthetic rehearsal, a hand that can commit adds `harness(COMBO_LINE_CAL): <pin>` (`python -m research.eval.combo_line_cal_rule pin`). `select` refuses without both.
- **Advice or momentum:** *Devin's override* (DECISIONS 2026-10-15 00:28, follow-up 4 of five), continued by the coordinator's instruction of 2026-10-15 to rebuild on the main-line prices.
- **Season labels** (`games.season`): 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (the frozen holdout, **never loaded by this rule or its harness**), 2026 = 2026-27 (the live forward log: **the verdict venue**).
- **Relation to other rules:** COMBO_STOCKS_SHADOW (its S forecasts, G1 and looks) is unchanged. LINE_CALIBRATION (frozen `a5441242`, freeze commit `06d76e3`) supplies the map. This rule reads S, never rewrites it, and writes to no `forward_combo_*` table.
- **Model classes (playbook):** quantile and distribution layer (logistic recalibration at the line) on a count head and a Gaussian-copula joint.

Pinned inputs (checked by the harness preflight before any outcome is read; each is the sha256 of the file named):
ODDS_SHA256: d6526d6c91b05688db86f31ce4de39f35d5dd0dd7d957fa11569e0ffa877f2e3
OOF_KEYS_SHA256: 569adc26b824527c6fcc0e521416db2a61c71f5850275e04bebc4306081eb9bd
OOF_GRIDS_SHA256: 734e300d15a029ffeb4356efc61a92156b7a8308f28951a947dcfc3dd3efe7b9
DB_SHA256: 759450f634b0e40d659e71fc0b9bfa83ecca16dc42b7dadfacc124b5935f71a6
PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c
UV_LOCK_SHA256: 97e3413ef0d45adeb613c23359b2ea834d00c908d57fc5f46418db3de4b4287d
(`ODDS_SHA256` = `data/odds/odds_history.duckdb` after the combo ingest, the DATA_CHANGELOG's `d6526d6c`; `OOF_*` = `data/ops/combo_oof_2023_24/` built by `reports/prereg_combo_line_cal/build_oof.py`, bit-identical to the combo-shadow rehearsal grids; `DB_SHA256` = `data/backups/nba_20261014.duckdb`, read only for the played flag, `minutes > 0`, and for labels at `select`.)

## The question
At a book's posted main line, is the probability the combo shadow serves for "over" honest? COMBO_STOCKS_SHADOW's G1 checks the lower tail of the whole pmf (pra and pa miss q10 narrowly: 0.1211 and 0.1204 against 0.12). Whole-pmf calibration and calibration at the line are different questions (playbook cross-cutting question 5): for pts the lower tail was close and P(over) at the line was still 4.3 pp low (T477). This rule asks the line question for stl, blk, bs, pra, pr, pa and ra with the map that fixed it for pts, reb, ast and fg3m (LINE_CALIBRATION Stage 1, T493: CAL minus A0 -0.0117 on Pinnacle).

## The rule in eight lines
1. **Family F (set by the gate, mechanically):** a target is in F iff its primary cell passes the gate table on the data_version at freeze. On today's data **F = {stl, blk, bs, pra, pr, pa, ra}, m = 7**. Primary book per target = the first of DraftKings, FanDuel, BetMGM, BetRivers that passes the bars; consistency book = the next that passes (None if none). Today: stl, blk, bs BetMGM (consistency: stl BetRivers; blk and bs none); pra, pr, pa, ra DraftKings (consistency FanDuel).
2. **Object.** A0 = P_S(Y >= ceil(line)) read from the persisted `s_pmf199` grid of the target (stored at `data/ops/combo_oof_2023_24/` for history; the `forward_combo_shadow` row live). Exact at x.5 lines; whole-number lines are refused by the preflight (none exist). **CAL** is LINE_CALIBRATION's map: per (book, target, calendar month) one weighted logistic regression on every earlier t60 main-line leg of that book and target (both seasons, strictly dated before the block), terms `[1, x, z, bench, bench*x]`, `x = logit A0` clipped at 0.5/199, `bench = starter10 < 0.5`, weights `0.5 ** (age_days / 365)`, ridge 1.0 on the four non-intercept terms, IRLS tolerance 1e-10, identity if fewer than 2,000 fit rows. **One changed term, z:** S is stored as an integer grid, so z = (line - q50) / max((q75 - q25) / 1.349, 0.5), clipped to [-5, 5], from the stored integer grid (q25, q50, q75 interpolated at positions 49.25, 99, 148.75). Measured, label-free: the 0.5 floor binds on 0.00% of the legs of every combo cell, 0.18% of stl BetMGM, and **25.6% of blk BetMGM** (the fallback trigger is 50%; not reached; the preflight refuses above it). A fitted map never sees a price.
3. **Leg definition (price-only, declared before any outcome).** Per (game, player, book) at the t60 snapshot, the two-sided point with the most balanced raw implied prices (ties to the lower point), kept iff: the key carries at most 3 distinct points (**a main key with more points is a ladder under the main key and is excluded and counted**: betrivers and unibet_us 2023 pra post a mean of 7.45 points, betrivers 2024 pra 33% of keys have 4 or more, betonlineag and mybookie blk are over-only ladders), the raw implied P(over) + P(under) lies in [1.00, 1.25], and, for the four sums, the point is within 4.0 of the sum of the same book's component main points whenever the book posts all components (a leg whose book posts none is kept and counted). The player-game must be a played regular-season row with `n_prior >= 5` (the combo shadow's population, 25,815 / 25,767 rows reproduced) and the snapshot at least 60 minutes before the real tip. DNP legs are voided as the books do.
4. **Primary metric.** Mean per-leg log loss of P(over) at the line, CAL minus A0, pooled over F's primary cells, every leg weighing the same. CI: paired 95% percentile bootstrap, 2,000 resamples, seed 0, clustered by **game**, and again by **date**; both upper bounds must be below 0. Practical floor **-0.003** (LINE_CALIBRATION's).
5. **Checks and verdicts (LINE_CALIBRATION P1-P7).** P1: primary <= -0.003, both uppers < 0. P2: CAL - C0 upper < 0 (C0 = weighted base rate of the fit rows of the cell and block). P3: CAL - mean(SH_0..49) <= -2 null sd and upper < 0 (SH = CAL refit on label-permuted fit rows, `default_rng([1000+k, cell, block])`; null sd recomputed inside every look). P4: |mean CAL - realised| <= 0.015. P5: no target significantly worse (CI lower > 0); per-target contrasts are reported with BH over m = 7 and carry no pass. P6: the starter and bench slices each have a point estimate <= 0. P7: the consistency books (the targets that have one) agree in direction (CAL - A0 point < 0). **CALIBRATED:** P1-P7. **SHRINKAGE_ONLY:** P1 and P4-P7 pass, P2 or P3 fails; the served object is C0 and the wording is "at the line the model carries no information beyond the base rate"; C0 must itself pass P1 and P4-P7. **FAIL:** anything else. **INCONCLUSIVE (live only):** a cell with fewer than 3,000 on-time legs is excluded from the pooled primary, counted and reported INCONCLUSIVE, and if fewer than 4 of the 7 cells reach it the pooled verdict is INCONCLUSIVE; so is P1's point <= -0.003 with an upper bound >= 0.
6. **Stages.** *Stage 1 (select, 2024, main lines, descriptive for the rule):* each 2024 month is fit on 2023 plus the earlier 2024 months; run once, after the freeze and the harness pin. It cannot pass the rule. If pooled CAL - A0 is **positive** with a game-clustered one-sided lower bound > 0, the rule closes before the live look. *Stage 2 (live, the verdict):* interim **Sun 2027-01-10**, kill-only (same kill criterion on live legs); final **Mon 2027-04-12** on games through the regular-season finale Sun 2027-04-11, once; the harness for Stage 2 does not exist yet (it needs the live shadow and live-lines loader) and is a separate pinned artifact. The live map for a block is fit strictly before the block from history plus live legs already settled, written to a versioned file (sha, `fit_through`) before the block starts.
7. **Slices (fixed now):** starter / bench (P6), target, month, book. Labels only except P6. Reliability in ten bins. Coin flip and the de-vigged market are reference rows only; no price enters any fit (`design()` takes no price argument).
8. **One pass, no variants.** No arm, term, floor, family, book order, tolerance, point cap or look changes after any outcome is read; a new idea is a new id. **No 2025 touch is requested now or ever.** Production, serving and promotion are Devin's.

## What we hold (data-capture audit, non-negotiable 8; re-measured on the ingest)
Script `reports/prereg_combo_line_cal/gate.py`, output `gate_main.json` (sha256 `0954471b08761fa91558eaaf8d045aa2adcf56dfa045cabfdb1bde18d05b1d56`, 10 s, exit 0), `date: Thu Oct 15 10:33:10 CDT 2026`. Read only, `season <= 2024` in every SQL, the only box-score field read is `minutes > 0`.
- **Main-line rows, t60:** pra 362,180 (2023) / 205,972 (2024); pr 163,262 / 150,464; pa 140,073 / 116,035; ra 132,109 / 109,474; blk 193,419 / 66,356; bs 58,804 / 33,401; stl (earlier pull) 376,325 / 143,895 (also t5). 7-11 books per market; **no Pinnacle** (it quotes none of these stats; LINE_CALIBRATION's primary-book design cannot carry over, hence the book order above).
- **Contamination found and handled by rule line 3:** betrivers and unibet_us 2023 pra (7.45 points per key), betrivers 2024 pra, betonlineag and mybookie blk (over-only ladders), betrivers 2024 pa/pr/ra (about 40% of keys have no under). None of the primary or consistency cells is affected (0 keys over the point cap in them).
- **Not held:** t5 for the new markets (t60 only), season 2025 (never loaded), any live line (the live store has no player rows yet), Kalshi combo/stocks markets (KXNBAPRA etc. parsed, never seen open).
- **Forecasts:** S out-of-fold pmf199 for all 55,320 test rows of 2023-24 persisted (R-F, committed 7b6c095): the kept legs join it at 100% in every cell.

## Minimum-data gate (measured, 2026-10-15 10:33:10 CDT)
Bars (declared; LINE_CALIBRATION's where one exists): primary-cell 2024 legs >= 5,000; 2024 games >= 300; fit rows >= 2,000 in every 2024 block; snapshot >= 60 min before the real tip; forecast join 100%; z floor binds on < 50% of legs.

| target | primary cell | 2023 legs | 2024 legs | 2024 games | min fit rows in a 2024 block | legs / game (2024) | consistency cell (2023 / 2024 legs) | gate |
|---|---|---|---|---|---|---|---|---|
| stl | BetMGM | 11,001 | 8,543 | 717 | 11,001 | 11.91 | BetRivers (5,165 / 7,316) | **PASS** |
| blk | BetMGM | 10,851 | 14,100 | 1,205 | 10,851 | 11.70 | none (DraftKings 3,202 fails legs; William Hill 3,113 and 279 games fail) | **PASS** (no P7) |
| bs | BetMGM | 8,510 | 8,639 | 727 | 8,510 | 11.88 | none (DraftKings 1,700, BetRivers 2023 0) | **PASS** (no P7) |
| pra | DraftKings | 10,698 | 10,308 | 1,201 | 10,698 | 8.58 | FanDuel (7,645 / 6,917) | **PASS** |
| pr | DraftKings | 12,927 | 12,588 | 1,201 | 12,927 | 10.48 | FanDuel (11,623 / 11,959) | **PASS** |
| pa | DraftKings | 11,746 | 9,248 | 1,201 | 11,746 | 7.70 | FanDuel (7,831 / 7,271) | **PASS** |
| ra | DraftKings | 8,903 | 8,384 | 1,199 | 8,903 | 6.99 | FanDuel (7,638 / 6,726) | **PASS** |

DraftKings fails the leg bar for stl (4,758), blk (3,202) and bs (1,700) in 2024 (it posts them for 3-4 players a game); the book order therefore moves those three to BetMGM, a fixed rule stated before any outcome (disclosed forking path: the order was written before the counts were seen, but the DraftKings-first preference is Devin-facing, not statistical).

| other item | measured | verdict |
|---|---|---|
| Population reproduces the combo shadow's | 25,815 and 25,767 | PASS |
| Forecast join (kept legs to persisted grid rows) | 100.00% in every cell | PASS |
| z floor binding | combos 0.00%; stl 0.18%; blk BetMGM 25.6% (DraftKings 19.9%); bs 0.00% | PASS |
| Snapshot >= 60 min before tip (held) | 100% of kept legs, median lead 64.4 min | PASS |
| Live T-60 lead (window [55, 65] in `configs/odds_live.yaml`) | about half of live snapshots would have lead < 60 min; **unmeasured until 2026-10-20** | RISK (repair R5) |
| Live capture switched on (`NBA_CLOSE_CAPTURE=1`) | not verifiable here | RISK (Devin) |
| Live power, pooled over the 7 cells (borrowed LC per-leg SD and design effect, density from the held main lines, unmeasured) | nominal 40,851 legs at the interim (hw 0.00102) and 85,165 at the final (0.00070); if half the snapshots are lost and the effective n is half: 0.00203 and 0.00141, against the 0.0015 bar | PASS at nominal and at the final look in the worst case; **FAIL at the interim in the worst case** (interim is kill-only, so it does not decide a pass) |
| Live power per cell at the final look | hw 0.0017 (stl, bs, blk), 0.0018 (pr), 0.0020 (pra), 0.0021 (pa), 0.0022 (ra); 0.0024-0.0031 if half the snapshots are lost | per-target contrasts are labels; only the pooled primary claims |

**Gate verdict: PASS on the held data for all seven targets**, with the two live risks above. Repairs that remain: R5 (move the t60 window to [60, 70]: zero credits, touches the live path, Devin's go) and confirming `NBA_CLOSE_CAPTURE=1`. Closed since revision 1: R1 (main-line pull; 152,770 pull + 710 probe credits per the ingest note), R-F (grids persisted). Not recommended: other sources (SportsDataIO quote-only; scraping excluded; 2022-23 props not purchasable from this vendor).

## Hypothesis and what each choice protects against
- **H1.** The CAL map lowers line log loss against A0 by at least 0.003 per leg, pooled over F's primary cells at main lines, and beats the base rate. **Null:** the map only reproduces the base rate, or does not reach the floor.
- Priors are in their own section below.

| choice | protects against |
|---|---|
| F and the primary book set mechanically by the gate | choosing the family or the book after seeing which calibrates |
| main-line legs only, point cap 3, overround and component checks, price-only | a vendor ladder under the main key, a broken quote or a mislabelled market becoming a "calibration failure" |
| fit on 2023 plus earlier 2024, scored on the next month | a later line or outcome shaping an earlier fit (planted-future probe) |
| no price in any fit | a "calibration" that is secretly the market (MARKET_SHRINK is another question) |
| z from the integer grid, floor binding counted and capped | a distance term that is constant on count stats |
| C0 and SH required; SHRINKAGE_ONLY serves C0 | shrinkage passing as skill (CALIBRATED_GRID) and a served probability contradicting its wording |
| game and date clustering, both uppers < 0 | slate-level correlation posing as precision |
| n_min 3,000 per live cell and INCONCLUSIVE named | a thin cell issuing a word; optional stopping |
| Stage 1 cannot pass; the live log decides; no 2025 touch | spending the holdout; a partly exposed select season being read as confirmation |
| harness preflight before labels, pinned files | an input or code change between the freeze and the first outcome |

## Exposure (nothing here is blind)
- **Seen:** LINE_CALIBRATION Stage 1 (T493) and the pts/reb/ast/fg3m line results (T477, T480); COMBO_STOCKS_SHADOW's rehearsal (CRPS and G1 tails for these targets on 2023-24); FAN_KNOWLEDGE's descriptive steals line (consensus over price 0.505 vs realised 0.474 in 2023-24, 0.506 vs 0.494 in 2024-25: the stl base rate below 0.5 is known, which a map can learn without skill).
- **Seen label-free by this draft:** the mean A0 at the kept lines (0.35 blk BetMGM, 0.45 bs, 0.47 stl, 0.45-0.48 for the sums on DraftKings) and prices; no outcome at a combo, blk or bs line, no CAL or C0 fit on these legs, no S forecast scored at a line (the gate reads `minutes > 0` only).
- **Consequence:** Stage 1 on 2024 is a genuine select on main lines but its season is shared with the rehearsal's marginals; a Stage 1 pass earns the live test and nothing more.

## Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile and distribution layer on a count head and a Gaussian copula)
1. **Information or capacity?** Neither new: the map adds no pre-tip information; it re-expresses S at the line. The class's typical failure ("P(over) 4 pp low at the line while overall CRPS looks good") is the target. C0 and SH are required.
2. **Enough data for the class?** A 5-parameter map per (book, target, month): 8,510-12,927 fit rows per primary cell from 2023 alone, 15.9k-22.7k effective with 2024 at the 365-day half-life; every 2024 block has at least 8,510. blk and bs now have enough (BetMGM). The live test side is the weak part (above).
3. **Valid test?** Real tips; played only; x.5 lines; integer support (P(over) from the 199-atom grid is exact); leak-free inputs (S is out-of-fold month blocks, lines at t60); label reader isolated and called only after the freeze and the pin; planted-future probe on synthetic labels. Open risks, stated: the OOF (monthly blocks) versus the live nightly-refit S can differ (a map fit on history may meet a shifted S: LINE_CALIBRATION's replay-oracle caveat applies); three or four sums per player-game share outcomes, so nominal n overstates information (game clustering handles it; the pooled power numbers are borrowed); BetMGM is the primary book for three targets (it covers fewer games: 717 in 2024 for stl and bs).
4. **Lineage.** LINE_CALIBRATION (`a5441242`): same map; carried P1-P7, C0/SH, B2 wording, no price in a fit, 365-day half-life; changed: books (no Pinnacle), z grid, family, live venue. CALIBRATED_GRID (T339): shrink-style maps beat real maps in a shuffled control; carried as C0/SH. COMBO_STOCKS_SHADOW G1 and ALT_LADDER_HIST integrity tests: carried as the price-only leg tests. Revision 1 of this rule (ladder-derived pseudo-main legs) is superseded: the transfer hazard no longer exists.
5. **What does it score against?** Log loss of P(over) at the line against the realised side, CAL against raw S, the base rate and permuted-label maps; the de-vigged market is a reference row. No CRPS claim, no money claim.

## Priors (binary; restated in PREDICTIONS_LEDGER before any result)
1. P(main session freezes with F = the seven targets unchanged by 2026-10-31) = **0.45**.
2. P(Stage 1 pooled CAL - A0 <= -0.003 with both CI uppers < 0) = **0.70**; expected value **-0.007** (80% interval -0.015 to -0.002). Why: the mean A0 sits at 0.45-0.48 at near-even lines and pts-based sums inherit pts's overconfidence; why not higher: the tails are close (G1) and the stl/blk heads are calibrated at P(Y = 0).
3. P(Stage 1 verdict) CALIBRATED **0.40**, SHRINKAGE_ONLY **0.25**, FAIL **0.35**; P(the kill criterion closes the rule) **0.04**.
4. P(stl cell alone has CAL - A0 <= -0.003 at Stage 1) = **0.25**.
5. P(final-look pooled primary meets P1, given a freeze, at least 3,000 on-time legs in at least 4 cells and live capture working) = **0.45**.
6. P(live final verdict) CALIBRATED **0.30**, SHRINKAGE_ONLY **0.20**, FAIL **0.20**, INCONCLUSIVE **0.30**, given the freeze.
7. P(share of live t60 snapshots with lead >= 60 min below 0.6 in the first week, window left at [55, 65]) = **0.80**.

## Harness (built before any label; synthetic rehearsal only)
`research/eval/combo_line_cal_eval.py` (preflight, rehearse, select, print-pins) and `research/eval/combo_line_cal_rule.py` (freeze and pin refusals); pinned files: those two and `reports/prereg_combo_line_cal/gate.py`. `select` calls `rule.require_ready(sha)` first; `load_labels` is the only reader of a box-score stat and nothing but `select` calls it (a test asserts it). Preflight refuses on: any pinned sha256 mismatch, library versions (numpy 2.5.3, polars 1.44.2, duckdb 1.5.6), cell choice or counts or A0 means differing from the values pinned in the harness (measured by `print-pins`, equal to the gate table above), z floor share > 50%, a lead < 60, a whole-number line, a 2025 row. Rehearsal: three synthetic truths on the real leg structure of 2024 (T477-like bias -> CALIBRATED; no information beyond a 0.475 base rate -> SHRINKAGE_ONLY; production calibrated -> FAIL) and a planted-future probe (labels on or after a block start complemented: block output identical; labels inside the window complemented: output moves). Commands: `.venv/bin/python -W ignore -m research.eval.combo_line_cal_eval preflight` (label-free), `... rehearse`, and after the freeze and pin `... select --sha <64 hex>`.

=== RESULTS BELOW ===

(none: no fit and no score on real outcomes. Gate: `reports/prereg_combo_line_cal/gate_main.json`. Rehearsal: `reports/prereg_combo_line_cal/rehearsal.json` (synthetic labels only).)
