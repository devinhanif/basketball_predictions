# Adversary: why did the SEQ_FULL_MIX_RT run-#1 gain double? (2026-10-12)

Subject: SEQ_FULL_MIX_RT (DRAFT, `docs/prereg/SEQ_FULL_MIX_RT.md`, commit e1560ec, draft text sha256 d9e5e8d1 = the run's `rule_sha256`), Colab run #1
`data/colab/runs/seq_full_mix_rt_sel/20261012_144207` (W22_E2, WALL_E2, WALL_E2S; 3 seeds, full budgets, not smoke; export sha 152189e4, hist sha 6268ae63,
both equal to the files on disk). Seasons 2023/24 only. No season-2025 row was loaded. All DuckDB access read-only (through the harness loaders). Heavy runs
held `data/ops/heavy.lock` (harness `WE.heavy()` for scoring; mkdir + pid + trap for the CPU smoke runs; waited while pid 8944 held it).

## Verdict: **SURVIVES** (no leak found; the jump is fully explained)

The doubling is the **training window**, not the real-tip export. The real-tip change slightly *shrinks* the gain. The history-window gain survives every leak
check I could run. It is real on 2023/24, but it shrinks over each season and from 2023 to 2024 (more on this under the biggest risk). So the draft's stated
2025 expectation (-0.006 to -0.0087, "no reason to expect more than a small change" from W_ALL) is now stale and must be rewritten before the freeze. The rule is
still a DRAFT, so that is allowed. It is a disclosure, not a rule change.

## Decomposition (pts, AVG - FULL, integer CRPS, paired, game-clustered 95% CI, `E.clustered`)

| step | what changes | 2023 (n = 27,619, 1,318 games) | 2024 (n = 27,701, 1,320 games) |
|---|---|---|---|
| stored ENSEMBLE E2 (proxy export), proxy blend ctx | the withdrawn rule's figure | -0.0100 [-0.0126, -0.0073] | -0.0098 [-0.0124, -0.0072] |
| same member, **real-tip blend ctx** | gate context only | -0.0101 [-0.0126, -0.0074] (step -0.0001 [-0.0001, -0.0000]) | -0.0096 [-0.0122, -0.0071] (step +0.0001 [+0.0001, +0.0003]) |
| **W22_E2** (real-tip export, era channels, Colab rerun) | real-tip tokens + rerun noise | -0.0083 [-0.0108, -0.0058] (step **+0.0017** [+0.0007, +0.0028]) | -0.0083 [-0.0107, -0.0060] (step **+0.0013** [+0.0001, +0.0026]) |
| **WALL_E2** (adds 2019-2021 history) | training window | **-0.0203** [-0.0234, -0.0173] (step **-0.0120** [-0.0145, -0.0097]) | **-0.0155** [-0.0184, -0.0128] (step **-0.0072** [-0.0091, -0.0053]) |

Shares of the WALL figure, 2023 / 2024: proxy-era SEQ member 50% / 62%; real-tip + rerun **-8% / -8%** (a loss); history **59% / 46%**; unexplained **0** (the
steps add up to the gate's figures to within 1e-4). The real-tip and rerun effects are confounded in one step (one W22 run; another W22 seed run would separate
them). Either way that step makes the gain smaller, so neither can be what inflated it.
The W22 figure lands at -0.0083 on both seasons, which is the adversary's earlier clean-game proxy figure (-0.0087). The brief's test ("if W22 is about -0.009 to
-0.010, history explains the jump") comes out on the history side.

## Checks

Scripts are in the scratchpad (`.../scratchpad/atk/`): `score.py` (harness pieces, unedited), `analyze.py`, `data_checks.py`, `seq_check.py`, `pool_align.py`,
`code/plant.py` and `code/train.py` (an unmodified copy of the Colab script). Every run below exited 0 and was shown unpiped.

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| 1 | W22 diagnostic scored like the gate (SEQc blend via `S.learned_blend` + `seqc_grid`, AVG = `mix_half(SEQc, FULL)`; E2S guard skipped, no W22 E2S file) | `.venv/bin/python -W ignore score.py rows.npz` (12 s world + 40 s arms) | 27,619 / 27,701 | W22 AVG-FULL -0.0083 / -0.0083; SEQc_W22-A0 -0.0088 [-0.0123,-0.0053] / -0.0121 [-0.0153,-0.0088]; WALL reproduces the gate exactly (-0.0203 / -0.0155; SEQc-A0 -0.0281 / -0.0216; AVG-AVG_S -0.0151 / -0.0124); FULL-A0 -0.0084 / -0.0083 (T467) | done |
| 2a | every warm pool's max date < block start (WALL, W22, WALL_E2S) | read `info_*.json` blocks_detail | 18 blocks x 3 | 0 violations (also `< block-01`) | PASS |
| 2b | the pools recomputed from the exports equal blocks_detail, and no test row is in a pool on/after its block | `pool_align.py` | 18 x 2 windows | 0 mismatches, 0 later rows in any pool | PASS |
| 2c | **planted future**: +40 to every label (all four stats) dated >= 2024-01-01 in the current export; the unmodified Colab script on CPU, smoke 4000 rows, WALL_E2, run orig / orig2 / planted | `NBA_SMOKE=4000 NBA_WINDOWS=WALL NBA_PROGRAMS=E2 ... train.py` x3 | 2,682 OOF rows, 46,120 planted rows | orig vs orig2 max\|dq\| = 0.0 in every block (deterministic). orig vs planted: **0.0 for blocks 2023-10 .. 2024-01**, then 4.3, 10.4, ... 87.7 from 2024-02 on (positive control: the plant does reach later blocks) | PASS |
| 2d | history can only precede tests | `data_checks.py` | 77,195 hist / 84,170 cur | hist dates 2019-10-22..2022-06-16; first current row 2022-10-18; first 2023 test row 2023-10-24 | PASS |
| 3 | row alignment: OOF keys -> export meta, and the export's label vs the A0 world's label | `pool_align.py rows.npz` | 56,433 OOF / 55,320 A0 rows | 0 duplicate keys, 0 missing, date and season match, **label match 1.000000** for WALL_E2, WALL_E2S, W22_E2 | PASS |
| 3b | key-permutation control: WALL E2 forecasts permuted among the players of each date (`within_date_perm`, seeds 0, 1), blend re-learned, AVG-FULL | `score.py` | as above | seed 0: +0.0366 [+0.0282,+0.0449] / -0.0006 [-0.0027,+0.0013]; seed 1: +0.0371 / -0.0006 [-0.0026,+0.0014]. The gain collapses (2023 is positive because the gate holds 0.5/0.5 before its first fit and a permuted member at weight 0.5 is ruinous: Oct +0.22, Nov +0.18) | PASS |
| 4 | duplicates / overlap: games in both exports; (game, player) keys | `data_checks.py` | 3,958 cur / 3,637 hist games | game overlap 0; key duplicates 0 within either export and 0 across; game-id prefixes 00219-00221 vs 00222-00224 | PASS |
| 4b | id consistency: player ids, team_idx mapping, stat_scale, feature names | `data_checks.py` | 30 teams; 771 / 776 players (478 shared) | team_id -> team_idx identical in both exports (0 differences); stat_scale equal [7, 3, 2.2, 1.2]; step/ctx feature names equal (the training concat takes these non-row keys from hist; harmless because they are equal) | PASS |
| 5 | replication by month (pts AVG-FULL, WALL) | `analyze.py` | months with n >= 300 | every regular-season month is negative in both seasons (2023: -0.0350 Oct ... -0.0084 Apr; 2024: -0.0245 Oct ... -0.0145 Apr). Only 2024-05 (2023 season, playoffs, where SEQc = A0 by rule) is +0.0108 [-0.0003, +0.0211], with WALL-W22 exactly 0 there | PASS |
| 5b | drop the best month | `analyze.py` | | 2023 without Nov: -0.0182 [-0.0214,-0.0151]; 2024 without Nov: -0.0149 [-0.0179,-0.0118]; the history step without the best month: -0.0083 / -0.0063 | PASS |
| 6a | the sequence holds the target game's box stats? | `seq_check.py` | 82,999 cur / 76,032 hist rows with a previous row | last present step's pts = the player's **previous** row's pts in 100% of rows (corr 1.000) in both exports; vs the own target: corr 0.61 / 0.60, exact match 9% (chance); days_ago > 0 on every unmasked step | PASS |
| 6b | era tokens as a target channel | `data_checks.py` era/has_report by season | | test seasons: all three era channels are 0 in every row, so they cannot carry 2023/24 target information. Hist: 2019-20 bubble/no-fans 0.149, 2020-21 `era_limited` = 1.0 and `era_nofans` = 0.0 on every row (see minor 2), 2021-22 zeros with has_report 0.0 by rule | PASS (no leak) |
| 6c | real-tip games vs the rest (the rows whose report meta differs from proxy19) | `analyze.py` | 2,230 rows (4.0%) | WALL-FULL on the differing games -0.0142 / -0.0156; on the same games -0.0206 / -0.0155: the gain does **not** sit in the games the real-tip change touched | PASS |
| 6d | mechanism plausibility: raw SEQ member, no blend | `score.py` | | RAW WALL_E2 - RAW W22_E2: -0.0470 [-0.0560,-0.0386] / -0.0254 [-0.0328,-0.0181]. The cold-fit validation pinball (2022 rows after 2023-04-01, before every test row and after every history row) falls 1.352 -> 1.326 (mean of 3 seeds), the same direction measured on data the history cannot reach | consistent |
| 6e | integer support | code read | | every arm is scored by `crps_from_quantiles` on integer 199-grids (SEQc through `to_integer_support`, the mixtures through `mix_integer_quantiles`); raw members converted the same way | PASS |
| - | rule integrity | `awk ... \| shasum -a 256`, `git log` | | draft head sha d9e5e8d1 = run config `rule_sha256`; commit e1560ec 14:41:59 CDT precedes the run artifacts (15:07); doc unchanged since | PASS (draft, not frozen) |

## Where the history gain sits (why the size is not a 2025 number)
WALL - W22 by month: 2023 Oct -0.0161, Nov -0.0307, Dec -0.0185, Jan -0.0065, Feb -0.0087, Mar -0.0032 [-0.0077, +0.0010], Apr -0.0046; 2024 Oct -0.0299, Nov
-0.0116, Dec -0.0080, Jan -0.0028, Feb -0.0107, Mar -0.0019, Apr -0.0029. By season -0.0120 (W22 pool = 1 prior season) then -0.0072 (2 prior seasons). By
n_prior: [0,10) -0.0045 (CI spans 0) / -0.0133; [10,40) -0.0131 / -0.0139; >= 40 -0.0120 / -0.0064. The pattern fits "more training data helps most when
the current-era pool is small": it is largest at the start of each season and decays as the warm pool fills. That is a legitimate mechanism, not a leak, but
it predicts a smaller marginal gain on 2025, where W_ALL's pool is six seasons and the 2023/24 conditions (a 1-2 season pool) do not recur.

## The single biggest risk if the claim is wrong
Not a leak: the **size**. 2023/24 is selection evidence, and the history effect falls with pool size (-0.0120 -> -0.0072, and toward about -0.002 late in each season).
Someone reading the gate figures (-0.0203 / -0.0155) as the 2025 expectation will overrate the result. Also, both windows are single Colab runs. The one replicate
I have of training randomness plus the real-tip change (W22 vs the stored ENSEMBLE E2) moves AVG-FULL by +0.0013 to +0.0017. That is far smaller than the
history step, so the step is not training noise, but its exact size is uncertain by about that much.

## Family, power
Scope of this review: 21 paired pts contrasts x 2 seasons, all descriptive (no decision rests on them; no BH claim is made). Every leak check above is
pass/fail, not a test in the family. The rule's own family (primary m = 1, secondary m = 7) is untouched. MDE at these n (half-width of the game-clustered CI):
about 0.003 for AVG-FULL, about 0.002 for the WALL-W22 step. The history step is 3-6x its MDE on both seasons.

## Not verified
* Real-tip and Colab-rerun noise, separately (needs a second W22 run, or W22 on the proxy export).
* Missingness by outcome group (played/DNP, minutes < 5, starter, win/loss) for the report and era channels. Only per-season rates were read here. The
  earlier loader review covered the report channel on the current export, but not the history export.
* "Lag every feature one extra day": needs a retrain. The planted-future smoke used 4,000 rows and hidden 16, not full budgets. Walk-forward isolation is a
  property of the code path, so the smoke run exercises the same code.
* `game_era_flags` correctness for 2020-21 (see minor 2) against an external source.
* The E2S guard for W22 (no file; diagnostic only).

## Findings and recommended changes (none applied by me)
* **Major (doc, before the freeze):** rewrite "Honest prior" and item 6 "Expectation". The W_ALL change was not small: AVG-FULL is -0.0203 / -0.0155 on 2023/24.
  The jump is the history window (-0.0120 / -0.0072). The real-tip export cost +0.0013 to +0.0017. The effect decays with pool size. State a 2025 expectation
  that accounts for that decay (my read: about -0.008 to -0.012, but that is the modeler's call). This changes no metric, floor or guard.
* **Minor 1:** `AVG_S - FULL` (the shuffled-input member in the 50/50 mixture) is -0.0052 [-0.0078, -0.0026] on 2023 and -0.0032 on 2024. A noise member reaches
  the primary floor against FULL on 2023 through the mixture effect alone. The AVG - AVG_S <= -0.003 guard and the permutation null cover this, so they must stay.
* **Minor 2:** in the history export `game_era_flags` makes `era_limited` = 1 and `era_nofans` = 0 on every 2020-21 row. The draft's text describes no-fans and
  limited-fans periods within 2020-21. The channel is therefore a 2020-21 season indicator. Harmless for test rows (all zeros), but the doc should say so, or the flags should be checked.
* **Minor 3:** `has_report = 0` is both the no-report token and, in training, the 2021-22 identity (every 2021-22 row). The 0.1-0.2% of test rows with no report
  inherit that. Negligible at that share. Disclose it.
* **Minor 4:** `concat_rows` takes the non-row keys (`stat_scale`, `step_features`, `ctx_features`, `date_str`) from the history dict. They are equal today
  (checked), so nothing is wrong now. An assert in the job that they are equal would keep it that way.

## Drafted rows (not appended)
DECISIONS (draft): "2026-10-12 SEQ_FULL_MIX_RT run #1: the AVG-FULL jump (-0.0100/-0.0098 -> -0.0203/-0.0155) is the W_ALL history window (-0.0120/-0.0072,
game-clustered CIs exclude 0); real-tip tokens + rerun +0.0013/+0.0017; adversary found no leak (planted future, pools, alignment, overlap, sequence
contents, key permutation). The expectation paragraph is updated before the freeze. Reverse if: a second W22 run or the missingness audit of the history
export shows an outcome-correlated pattern."
