# Scorer review: T510 / T510b line-move scorer (research/line_move), 2026-10-24

Reviewer: adversary. Code reviewed: `agent/market-t510-scorer` at 0add724 (11 files, +1,877; only `research/line_move/`
and `tests/research/line_move/`; nothing under `nba/`, `configs/` or the registry). Frozen texts:
T510 `docs/prereg/LINE_MOVE_TOWARD_MODEL.md` sha256 881aca57… (freeze 94c56f0), T510b
`docs/prereg/LINE_MOVE_TOWARD_MODEL_DNP_FAIR.md` sha256 68f0ef84… (freeze 870959d). Both hash as pinned, and
`git diff 94c56f0 0add724` / `git diff 870959d 0add724` on the two texts are empty.

**No movement statistic was formed on any real night.** Every real-data command below reads counts, signs of
`p_X − mid0`, snapshot times and alias matches only. All stress and attack runs use synthetic quotes. No 2025 data
was read.

Verdict: BLOCKED

In the task's terms this is **CHANGES_REQUIRED**. The selection, the valuations, the resampling and the 95% and 97.5%
bounds implement the frozen text correctly, and I found no look-ahead. Five fixes (F1–F5) are needed before any real
mid is scored. They pin the alias file, stop a partial night from entering the window, make the scoring a single shot,
encode the outcome table, and add the diagnostics that separate information from drift. F6–F10 are needed before the
scoring date (about 2026-11-06), not before merge.

**Top finding (rule-level; the code is faithful, the exposure is in the frozen design).** On the gate night the model
leans hard to one side. Among T510 part-1 qualifiers, mean sign(p_M − mid0) is **−0.82** (10-21, n=324) and −0.65
(10-22, n=57). In part 2, M and B lean in opposite directions: mean s_M −0.67 against s_B +0.18 (10-21, n=399). A
market-wide pre-tip drift in the "k+" Yes mids therefore passes both parts with no market-specific information. In the
builder's own synthetic world with that lean added, a uniform **−1.5c** drift from S1 to close passes T510 **150/150**
and T510b **149/150**, while the same drift without the lean passes 0/150. Part 2 is meant to be the guard, but it is
the most exposed part: at −1.0c, part 2 passes 299/300. The builder's dryrun forecasts are sign-balanced (n+ ≈ n−), so
it cannot see this. The frozen text cannot change. The remedy is a pre-look reading note (D6) and the diagnostics that
carry it (F5).

## Playbook check

Model class: no model is fitted. The scorer compares stored outputs of `props_context_residual` (gradient-boosted
residual read on an integer survival grid, `p_ge_full`) and `props_recency_v1` (quantile/distribution layer) with the
Kalshi mid's later movement. Playbook sections used: gradient-boosted trees, and quantile and distribution layers.

1. **Information or capacity?** The question asks about information: does the context residual know something the
   market prices later? The finding above shows the test cannot tell that information apart from a common drift times
   the model's under-lean. A PASS is therefore not evidence of information unless the per-sign split (F5, D6) agrees.
2. **Enough data for this class?** Nothing is fitted. Dates bind (14). Markets do not: 324 to 345 part-1 qualifiers on
   one 11-game night. The discriminating subset (minority-sign markets) is about 9% of qualifiers under T510 (on 10-21,
   about 29 of 324), so the per-sign reading will be low-powered (power note below).
3. **Test validity.** The selection is valid against quote noise. S0, S1 and close are decoupled, the rung is chosen on
   S0 only, and there is no fallback. It is invalid against drift times lean (top finding). An invalid test is not a
   null, and a PASS from it is not a win.
4. **Lineage and inherited guards.** T510's L1–L5 and T510b's B1–B5 are carried into code: decoupled S0/S1/close
   (selection.py:132–178), date resampling with the pinned RNG (analysis.py:157–165), the 97.5% companion level, the
   unscoreable states and the dryrun. The sign-shuffled placebo is implemented across dates rather than within a date,
   and only for part 1 (F5).
5. **Scored against?** Kalshi mid movement S1 to close, in probability units. This is a price-direction (CLV-style)
   test, not EV and not calibration.

Class checks. Trees: the M-versus-B contrast isolates the residual only if the residual does not also shift the lean.
Here it does (s_M −0.67 against s_B +0.18), so the contrast mixes residual information with drift. Quantile layer:
integer support is respected (`c[k−1]/n`, 0 past the grid; selection.py:35–39), and `p_play` is one scalar shared by
M and B.

## Checks

All commands were run unpiped. `<SP>` = `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/214e8eaf-cfca-4a90-ae80-a0349b39bd8b/scratchpad`.
The DB is the builder's read-only copy `<SP>/n3.duckdb` (copied 2026-10-24 00:09 CDT), opened with `read_only=True`.

| # | Check | Command | n / numbers | Pass |
|---|---|---|---|---|
| 1 | Frozen texts intact; scope | `shasum -a 256 docs/prereg/LINE_MOVE_TOWARD_MODEL*.md`; `git diff --stat 94c56f0 0add724 -- …`; `git diff --stat 870959d 0add724 -- …` | 881aca57…, 68f0ef84…; both diffs empty; 11 files, research/ + tests/ only | PASS |
| 2 | Lint, types, tests | `uv run --frozen --extra dev ruff check …` exit 0; `ruff format --check` exit 0; `mypy research/line_move` exit 0; `pytest -q --no-cov tests/research/line_move` **27 passed, exit 0** | With the repo's default `--cov-fail-under=70`, a subset run exits 1 on total coverage alone (0.71%); that is not a scorer failure | PASS |
| 3 | S0/S1/close use nothing later than allowed (planted future) | `.venv/bin/python <SP>/t510rev/planted.py; echo exit=$?` → exit=0 | (A) quotes at tip and after tip are never the close; (B) a rung whose post-S0 quotes sit at 0.50 loses to the rung nearest 0.50 at S0; (C) S0 = the first snapshot at or after made_at when made_at > tip − 150; (D) a capture that starts after tip drops the market and never scores it | PASS |
| 4 | Rung chosen on S0 only | selection.py:133–150 read line by line; test (B) above; builder test `test_selection_ignores_quotes_after_s0_when_choosing_the_rung` | The key is (\|mid0 − 0.50\|, k) over rungs that pass the S0 filter; S1 and close are read only after the choice | PASS |
| 5 | T510b reuses T510's selection exactly | `__main__.cmd_score` and `cmd_preflight` build one `sel` with `selection.select_all`; `valuation(rule, …)` is applied only in `analysis.build_table`/`qualifying_counts`; `select_psg` reads the valuation only to label drop signs | One code path; no re-implementation to diff | PASS |
| 6 | 97.5% bounds | analysis.py:179–189: `a = (1 − lv)/2·100` → 1.25 / 98.75 percentiles; `t.ppf(1 − (1 − lv)/2, g − 1)` → 0.9875; HARM uses the rule's own level (analysis.py:216) | T510 at 95%, T510b at 97.5% with the 95% bounds reported (`passes_at_95`) | PASS |
| 7 | Never-before-T510 ordering | analysis.py:124–128 `window_end` = max(own, T510's), None if either is None; dryrun.py:340 refuses T510b when T510's dryrun failed | Correct for the window. Computation order is not enforced: T510b can be run before T510 is (F3) | PASS (window) / FIX (order) |
| 8 | Dryrun gating; no movement before the horizon | `require_pass` binds to sha256 of spec/selection/analysis/dryrun (record `<SP>/lm_record.json` code_sha 3bfed72b… = the committed code); `cmd_score` returns 3 before `build_table`; preflight forms no movement (`test_cli_*`) | Gated on the CLI path. Gaps: the record is a hand-editable JSON in scratch, not committed; `loaders.py` and `__main__.py` are not hashed; a horizon date's partial night can enter the window (F2) | FIX |
| 9 | Snapshot grid (per-ticker vs capture file) | `<SP>/t510rev/structure.py` exit 0; `<SP>/t510rev/counts.py` exit 0 | No ticker has a gap inside its span (10-21/22/23, all four series). No sub-cent prices (0 of 1.47M quote fields). Selected markets whose S0 or close differs from the capture file grid: **0** (10-21, n=431) and **0** (10-22, n=98) | PASS (guard F6) |
| 10 | Alias delta (builder #1) | `counts.py` | 57164b8 adds **30** player-stat-games on 10-21 (24 selected) and 0 on 10-22. All 30 belong to 8 players with no `nba_api` static name (pids 1642865, 1643408–1643414; rookies). It re-maps **0** existing exact-name matches and changes the rung set of **0** pre-existing PSGs. 92 of 532 forecast players have no static name and can only match through an alias | see ruling #1 |
| 11 | Sign lean (selection-level fields only) | `counts.py` | T510 part 1: mean s_M −0.821 (10-21, n=324), −0.649 (10-22, n=57). Part 2: s_M −0.674, s_B +0.178 (n=399); 10-22 −0.605 / +0.235 (n=81). T510b part 1: −0.651 (n=298), −0.472 (n=53). T510b part 2: −0.489 / +0.400 (n=403), −0.310 / +0.500 (n=84) | finding |
| 12 | Noise dryrun, extended (synthetic only) | `.venv/bin/python <SP>/t510rev/stress.py <scenario> 300` (seed 7; builder's selection and analysis unchanged) | Lean only: PASS 0/300 both rules. Lean + zero-mean date shock 2c: T510 PASS 1.7%, T510b 0.3%. Lean + date shock 4c: T510 2.0%, T510b 1.0%. Noise 5c at φ 0.97 with lean: T510 0.0%, T510b 0.0% (part 1 alone 1.3%). **Lean + drift −1.0c: T510 PASS 5.3%, part 2 99.7%.** **Lean + drift −1.5c (150 draws): T510 100%, T510b 99.3%.** No lean + drift −1.5c: 0% | noise null PASS; drift×lean FAIL |
| 13 | Does the shipped placebo catch it? | `<SP>/t510rev/placebo_check.py` exit 0 | Drift −1.5c with no information: observed m_M +0.0132, placebo +0.0133, difference −0.0000 (it catches drift). But under a strong lean, a real +0.02 signal is also mostly absorbed: the expected placebo is about 0.02·mean(s)² ≈ 0.017. Placebo subtraction cannot tell the two apart | partial |
| 14 | Per-sign split discriminates | `<SP>/t510rev/bysign.py` exit 0 | Drift −1.5c, no information: s=+1 group mean −0.0105 / −0.0207 / −0.0231 (n 37 / 30 / 26); s=−1 group +0.0149 / +0.0143 / +0.0133. Planted +0.02 information: both groups about +0.019 to +0.021 | PASS (adopt as D6) |
| 15 | Integer support | selection.py:35–39; dryrun `_counts` | `c[k−1]/n` on the integer grid, 0 past the grid | PASS |
| 16 | Missing-data handling | loaders.py:41–47 | A null `p_play` defaults to 1.0 instead of dropping. Measured: 0 null `p_play` and 0 null `p_ge_full` among 8,270 M/B rows | minor (F7) |

Six standard checks. Shuffled labels: the sign-shuffled placebo exists (check 13), and its within-date form is F5.
Planted future: PASS (check 3). Missingness: the S0/S1/close drop table by sign and by p_play < 0.9 is implemented
(selection.py:154–164); the loader drops are counted, not split by sign (minor). Knockout: n/a (no feature groups).
Replication: n/a until a result exists; the per-sign split (F5) is the replication that matters. Integer support:
PASS.

## Rulings on the builder's ambiguities (against the frozen text)

I had the five items named in the task (#1, #2, #10, #14, the outcome table). The other 13 were not in anything I
could read (the branch, the commit, the shared scratch). I rule below on the named five and on six more that I found
myself. The draft DECISIONS rows are at the end.

**#1 Alias-file pinning. Ruling: use the post-merge file, pinned by hash now, before any look:
`configs/kalshi_aliases.yaml` sha256 `4d176c5381249effa54887cf9eecab88fa733417ae9f32dbbd57e0dbda4cd5d9` (57164b8).** The
text names a path, not a version ("matched by `configs/kalshi_aliases.yaml` first, then exact normalized full name;
unmatched markets are dropped and counted"). Like `forward_predictions`, the file is data read when the scorer runs. The
"dropped and counted" clause describes a leftover, not a sampling design. The freeze-time file (6544535a…, identical at
94c56f0 and 870959d; 57164b8 landed at 22:55 CDT, after the T510b freeze at 22:30) systematically excludes rookies,
because exact-name matching runs through `nba_api` static names and 92 of 532 forecast players have none. The 57164b8
additions are label-free (one official lineup-feed personId per name, reviewed for the snapshot, not for T510). They
re-map 0 existing matches, touch 0 existing PSGs, and were merged before any movement on any window date was formed.
The gate (non-negotiable 8) is a floor, and 455/345/318/293 clears it as 431/324/298/276 did. What decides it: an
unpinned file is a forking path, since the scorer could be re-run with a different file after the result. So the pin
matters more than which file is pinned. Any later alias change applies to neither rule. The gate replication stays a
regression test of the selection code, run with the freeze-time file.

**#2 Missing B row. Ruling: drop the market from both parts and count it (as built, selection.py:182–188).** "Dropped
and counted if absent" can only drop a market; an absent row cannot be dropped. Keeping part 1 on the part-2 base also
keeps T510b's shared selection identical. Measured: B is present for every M row on every run since 10-20 (4,135 /
4,135 rows each model, 9 runs). If the count is ever > 0, report part 1 with those markets added back, labelled
sensitivity.

**#10 What counts as a "date". Ruling: an ET date (from tipoff) on or after 2026-10-22, other than 10-21, that holds at
least one part-1 qualifying market of that rule, and whose capture is complete (every tip on that ET date has passed,
and the run happens on a later ET date).** The first half is the builder's definition (analysis.py:107–121). It is
correct, and in practice it is equivalent to T510b's "every date with a valid pre-tip capture". It also guarantees the
bootstrap has 14 clusters for part 1. The completeness clause is new and needed: today a run on the evening of the
horizon date can count a half-captured night, and a later rerun would give a different window (F2).

**#14 Dryrun pass criteria and noise. Ruling: the builder's criteria are an acceptable reading of the text, and the
1c / φ 0.8 primary is a fair noise null. It is not a fair null for the forecasts' sign structure.**
- Criteria: (i) T510 part-1 false-pass ≤ 5% over 1,000 draws, and |mean part 2| ≤ 0.002 for "~0". The 0.002 is 40% of
  the part-2 floor; the observed value is 3.7e-5. T510b: PASS rate and part-1-alone rate both ≤ 2.5% (B4 asks only for
  the first, so this is stricter). (ii) Planted +0.02 passes in ≥ 95 of 100 draws (observed 100/100 for both rules).
  Also required: every window holds ≥ 300 part-1 qualifiers (min 424). All are consistent with the text. The text gives
  no numbers for (ii) or for "~0", so these are the builder's numbers and are recorded as D4.
- Noise: the independent price-runs scratch (79bbbca, read 2026-10-23 after both freezes; no forecast; not movement
  toward M or B) found pre-tip prop-mid change autocorrelation of −0.29, flat at 1, 5 and 15 min (VR(15) 0.31). That is
  short-memory quote flicker, the benign case for the S0→S1 decoupling, and matches φ 0.8 per 2 min (about 0.19 left
  after 15 min). The stress runs (builder: 3c at φ 0.95, 2c at 90% stale; mine: 5c at φ 0.97) do not false-pass above
  1.3%, because the 0.010 floor dominates.
- Shared night shock: when it has zero mean it does not hide a false pass. The date resampling sees it (2c: 1.7% / 0.3%;
  4c: 2.0% / 1.0%; all inside tolerance). The hidden risk is a non-zero common drift combined with the lean (top
  finding). The text's null is "pure reverting quote noise", so the dryrun as specified passes honestly. The lean+drift
  scenario goes into STRESS as reported-only (F10).

**Outcome table not in code. Ruling: not acceptable as a reading step; encode it (F4).** The table was decided before
any movement so that nobody chooses a reading after seeing T510 and T510b side by side. Leaving its 7 rows to a person
at result time brings back exactly that choice. It is about 30 lines and a 7-case test.

**Additional rulings.**
- **A1. Snapshot = capture file.** S0, S1 and close are capture files (per series). A rung absent from the S0 or close
  file has no quote there and fails the filter. As built, the code takes the ticker's own nearest appearance. This made
  0 difference on 10-21/22 (check 9), but a partial file in future would make S0 later or close staler than the text
  allows (F6).
- **A2. Placebo.** "A sign-shuffled placebo" means: permute sign(p_M − mid0) **within date** across part-1 markets,
  and permute the pair (sign_M, sign_B) jointly within date for part 2, 1,000 permutations, seed 20261022 + 1. As built
  (analysis.py:241–254), the permutation runs across dates and part 2 has none (F5).
- **A3. T510b window when its own horizon is earlier than T510's.** The window is through T510's date (max), as built.
  This is the only reading under which "scored once, on the first date by which…" and "never before T510 itself is
  scored" can both hold. It differs from the alternative only if markets, not dates, bind, which the gate says will not
  happen.
- **A4. T510 scored but T510b never reaches its own horizon.** Read as the table's last row: T510's verdict and "What
  it decides" stand unmodified, and T510b is reported unscoreable.
- **A5. Percentile method.** `numpy.percentile` with default linear interpolation. The t-interval uses unweighted
  per-date means with ddof 1. The text fixes neither; record both before the look (D9).
- **A6. Null `p_play`.** Drop and count (`drop_null_p_play`); do not default to 1.0 (F7). There are 0 today.

## Required before any real mid is scored (blocking)

- **F1 (blocker) Pin the alias file.** spec.py, after line 20: add `ALIASES_SHA256 =
  "4d176c5381249effa54887cf9eecab88fa733417ae9f32dbbd57e0dbda4cd5d9"` and `ALIASES_SHA256_FREEZE =
  "6544535ab785f6cbae73d239be74e5c6ac5cf75f5e554804cb4d70353f2bfd11"` (`git show 94c56f0:configs/kalshi_aliases.yaml | shasum -a 256`). In
  `__main__.cmd_score` (line 119), before `_load`, refuse (exit 2, "REFUSED") unless
  `sha256(aliases) == ALIASES_SHA256`. In `cmd_preflight` (line 97–101), compare against `GATE_21` only when the
  aliases hash is the freeze hash, and against a new `GATE_21_PINNED = {markets_scored 455, p1_T510 345, p1_T510b 318,
  p1_both 293, only_T510 52, only_T510b 25, sign_flips 1, p2_T510 420, p2_T510b 423, pplay_lt_0.9_scored 133}` when
  it is the pinned hash. Print the `nba_api` package version in both commands; it is the name source for exact-name
  matching.
- **F2 (major) No partial night in the horizon or window.** `__main__.cmd_score` line 121: also drop
  `s.day >= datetime.now(ZoneInfo("America/New_York")).date()`, and refuse if any window date has a tip later than
  now − 30 min. Test: a selection with a market dated "today" does not count toward the 14 dates.
- **F3 (major) Scored once; T510 computed first.** `cmd_score` writes `reports/line_move/<rule>_result.json` (verdict,
  window first/last date, n per part, every bound, `passes_at_95`, the code sha256 of all six modules including
  loaders.py and __main__.py, aliases sha256, `forward_predictions` row count and max made_at of the DB copy, capture
  file count) and refuses to overwrite it. For `--rule T510b`, refuse unless `T510_result.json` exists. Tests for both
  refusals.
- **F4 (major) Encode the outcome table.** New `analysis.family_reading(t510_state, t510b_state, t510b_at95) -> str`,
  where state ∈ {PASS, NULL, HARM, UNSCOREABLE}, returning the frozen row text verbatim (T510b lines 73–85); a
  `family` subcommand that reads both result records (or an unscoreable marker written by F3 when `require_pass` or the
  horizon refuses at season end). One test per row (7).
- **F5 (major) Diagnostics that separate information from drift (reported; they decide nothing).** analysis.py:241–254:
  replace the across-market permutation with the within-date permutation of A2 and add the part-2 joint permutation.
  Add `by_sign`: for s in {+1, −1}, n, mean m_M with the date-resampled 95% percentile CI (`resample_dist`), and the
  same for m_M − m_B over part-2 markets grouped by sign(p_M − mid0). Print these directly under the verdict, not in the
  REPORTED block. Test: in `stress.py`'s lean+drift world the s=+1 group mean is < 0; in the planted world both groups
  are > 0.

## Before the scoring date (about 2026-11-06), not before merge

- **F6 (minor)** selection.py:134–168: pass each series' capture-file times into `select_psg`. Take S0 = the first
  file at or after the S0 time and close = the last file before tip. If the rung is absent from that file, count
  `rung_absent_at_snapshot` and treat the rung as failing the filter. Today this changes 0 markets.
- **F7 (minor)** loaders.py:47: `p_play` missing or null → `Forecast` with `p_play=None`; select_psg drops it as
  `drop_null_p_play` (counted).
- **F8 (minor)** Commit the dryrun record (`reports/line_move/dryrun_record.json`) and make it the default
  `--record`. After F1–F5 the analysis.py hash changes, so the dryrun must be re-run (about 885 s). The re-run record is
  the one that gates.
- **F9 (minor; reported items the texts require)** T510: "the harness states Kalshi's actual rule from the market's
  rules text": print the distinct `rules_secondary` (stat noun masked) over the window's tickers from
  `data/raw/live_*/kalshi_rules/`. T510b: wire `inactive_mid_close` into `cmd_score` (inactive = no box minutes, read
  only after the window's games are final), print the slice by M age (already there), and print drops by
  sign(p_M′ − mid0) (already there).
- **F10 (minor)** dryrun.py STRESS: add `lean_drift_-1.5c` (M mean bias −0.35 in log space, a uniform −0.015 drift
  S1→close) as reported-only, so the record documents the exposure. This changes no gate criterion.

## Power / MDE

The dryrun's planted +0.02 passes 100/100 at 14 dates with ≥ 424 part-1 markets. The rule detects a uniform 2c move
toward M. The per-sign reading (D6) runs on the minority-sign group: on 10-21 that is about 9% of T510 part-1
qualifiers (about 29 of 324 per 11-game night; 18% on 10-22), so about 30–60 markets by the horizon. With per-market
movement SD unknown (not computed), a 2c effect in that group is detectable only if the SD is under about 4c. The
honest reading of a PASS whose minority group CI spans 0 is "drift and information not separated", not "information".

## Single most likely failure mode if a T510 PASS is wrong

Prop Yes mids drift down in the last ~90 minutes before tip (a common, non-informative move). The model's under-lean
(mean sign −0.82; M and B on opposite sides in part 2) turns that drift into "movement toward M" in both parts. The
threshold is about −1.2c of common drift for T510 part 1 and about −0.6c for part 2.

## What was not verified

- The real pre-tip drift of prop mids (computing it would form movement on window dates; not done, by instruction).
  Whether the top finding bites is therefore unknown, not shown.
- The builder's other 13 ambiguities (items 3–9, 11–13, 15–18): not provided to me.
- The full 1,000-draw dryrun (885 s) was not re-run. I relied on the record `<SP>/lm_record.json`, whose code_sha256
  3bfed72b… matches the committed code (recomputed), and ran my own 150–300-draw synthetic scenarios.
- The loader's ET date comes from the ticker string (loaders.py:142), not from tipoff. It agrees on 10-21/22 (every
  selected market matched a forecast keyed by tipoff ET date). It is not proved for late tips after ET midnight.
- Kalshi settlement for DNP markets (never stored).

## Side effects

`uv run --frozen` created an untracked `.venv/` in the builder worktree
(`.claude/worktrees/agent-ad928b7041370f353/.venv`). Removing it was denied by the permission system. It is ignored and
uncommitted; the main session may delete it.

## Drafted DECISIONS rows (pre-look; not appended; the frozen texts are unchanged, these are reading notes)

All rows are dated 2026-10-24, written before any movement statistic on any window date (10-22 onward) was formed by
anyone on the T510 path.

| When | What | Why | Reverses if |
|---|---|---|---|
| 2026-10-24 (pre-look) | T510/T510b alias file pinned: `configs/kalshi_aliases.yaml` sha256 4d176c53… (57164b8). Later alias changes apply to neither rule; the freeze-time file (6544535a…) is kept only for the 10-21 gate-replication test (431/324/298/276); the pinned file gives 455/345/318/293 on 10-21. | The text names a path, not a version; the 57164b8 additions are label-free, add only 8 rookies without static names (30 PSGs on 10-21, 0 on 10-22), re-map nothing, and landed before any look; an unpinned file is a forking path. | A pinned-file alias is shown to map a name to the wrong player. Then: fix it in a new file, report both counts, and keep this pin for the score. |
| 2026-10-24 (pre-look) | Missing B row: the market is dropped from both parts and counted; if > 0, part 1 with them added back is reported as sensitivity. | "Dropped and counted" can only apply to a market; it keeps T510b's selection shared. 0 absent in 4,135 rows. | — |
| 2026-10-24 (pre-look) | A "date" = an ET date (from tipoff), ≥ 10-22, ≠ 10-21, with ≥ 1 part-1 qualifier of that rule and a complete capture (all its tips passed; the run is on a later ET date). | Matches T510b's "valid pre-tip capture", gives 14 bootstrap clusters, and prevents a half-captured horizon night. | — |
| 2026-10-24 (pre-look) | Dryrun criteria as built: T510 part-1 false-pass ≤ 5% and \|mean part 2\| ≤ 0.002 over 1,000 draws; T510b PASS and part-1-alone ≤ 2.5% at 97.5%; planted +0.02 passes ≥ 95/100; windows ≥ 300 part-1. Primary noise σ 1c, φ 0.8 per 2 min, observed spreads and 61.7% staleness. A lean+drift scenario is reported, decides nothing. | The text sets no numbers for (ii) or "~0"; noise matches the 79bbbca finding of short-memory flicker; zero-mean night shocks stay inside tolerance. | A complete-night measurement on an excluded night shows 15-min noise persistence well above 0.2. Then: re-run the dryrun with it before the score. |
| 2026-10-24 (pre-look) | The T510b outcome table is applied by code (`family_reading`), not by a reader. | Removes the side-by-side reading choice that the table was frozen to prevent. | — |
| 2026-10-24 (pre-look) | Reading note for any T510 or T510b PASS (the verdict itself is unchanged): the within-date sign-shuffled placebo and the per-sign split (mean m_M and m_M − m_B by sign(p_M − mid0), date-resampled 95% CI) are printed under the verdict. If the minority-sign group's mean is ≤ 0, or its CI spans 0 while the majority's does not, the note says "PASS not separable from a common pre-tip drift times the model's under-lean (10-21 mean sign −0.82; part 2 s_M − s_B −0.85)", and Claude recommends against raising the blend weight. | Synthetic: a −1.5c drift with no information passes T510 150/150 and T510b 149/150 under the observed lean; the per-sign split separates drift (minority group < 0) from information (both groups > 0). | The real lean at the horizon is near balanced (\|mean sign\| < 0.3). Then the note is moot and is reported as such. |
| 2026-10-24 (pre-look) | Placebo = permutation within date (part 1: sign_M; part 2: (sign_M, sign_B) jointly), 1,000 permutations, seed 20261022 + 1. Percentiles by numpy linear interpolation; t-interval on unweighted per-date means, ddof 1. | The text fixes none of these; fixing them before the look removes harness choices. | — |
| 2026-10-24 (pre-look) | T510b window = through max(own horizon, T510's); if T510 is scored and T510b never reaches its own horizon, T510's verdict and "What it decides" stand and T510b is reported unscoreable. S0 and close are capture files; a rung absent from that file fails the filter. | The only reading consistent with both "scored once on the first date…" and "never before T510". The table has no row for T510b missing its horizon; the last row's reading is the natural one. | — |

## Scratch files (not committed)

`<SP>/t510rev/structure.py`, `counts.py`, `planted.py`, `stress.py`, `placebo_check.py`, `bysign.py`, and
`stress_*.txt` outputs.

## Re-review (diff only): `git diff 6f66ba6 dd20ebc`, 2026-10-24

Scope: 10 files (+899/−76), all under `research/line_move/`, `tests/research/line_move/` and
`reports/line_move/dryrun_record.json`. No new attacks were run. Every check below was run unpiped in the builder
worktree at dd20ebc.

Verdict: BLOCKED

In the task's terms this is **CHANGES_REQUIRED**. F1–F10 are all in and correct. The revision adds two new escape
hatches, R1 and R2. Each is a few lines to fix.

| Item | Evidence | Result |
|---|---|---|
| Lint / types / tests | `uv run --frozen --extra dev ruff check …` exit 0; `ruff format --check` exit 0; `mypy research/line_move` exit 0; `pytest -q --no-cov tests/research/line_move` **47 passed, exit 0** | PASS |
| F1 alias pin, two constants | spec.py `ALIASES_SHA256` 4d176c53… (score only; `_check_pinned_aliases` refuses anything else), `ALIASES_SHA256_FREEZE` 6544535a… (preflight regression only). `python -m research.line_move preflight --date 2026-10-21 --nba-db <SP>/n3.duckdb --aliases configs/kalshi_aliases.yaml` → 455/345/318/293/52/25/1/420/423/133, match True, exit 0; the same with `--aliases <SP>/aliases_old.yaml` → 431/324/298/276/48/22/1/399/403/109, match True, exit 0. nba_api 1.11.4 printed | PASS |
| F2 complete nights | `analysis.complete_days`: ET date < today's ET date AND last tip ≤ now − 30 min; `assert_window_complete` re-checks the window; test `test_a_market_dated_today_does_not_count_toward_the_14_dates` | PASS |
| F3 once-only, T510 first | `_write_once` uses `open("x")`; score refuses if its record exists; T510b refuses without `T510_result.json` or when it says UNSCOREABLE. **But see R1 and R2** | PASS with R1/R2 |
| F4 family table | All 7 `ROW_*` constants appear verbatim as `| … |` cells of the frozen T510b text (checked programmatically, 7/7 True). The `family_reading` branch order matches the table: T510b HARM is read as "not PASS", and T510 HARM shares the NULL rows | PASS |
| F5 diagnostics | `placebo`: within-date permutation; part 2 permutes the index jointly for (sign_M, sign_B); seed 20261022 + 1; 1,000 permutations. `by_sign`: part 1 m_M and part 2 m_M − m_B by sign(p_M − mid0), date-resampled 95% CI. Both are printed directly under the verdict line | PASS |
| F6 capture-file grid | `_file_index` / `_quote_at` take S0, S1 and close from per-series file times; an absent rung fails. Preflight counts unchanged (as predicted: 0 affected markets). `rung_absent_at_snapshot` 137 on 10-21 (late-listed rungs) | PASS (R3 minor) |
| F7 null p_play | loaders.py keeps None; selection drops `drop_null_p_play`; the drop-sign logging handles None | PASS |
| F8 dryrun record | Committed at `reports/line_move/dryrun_record.json`; code_sha256 c05efe8e… equals `spec.code_sha256()` recomputed at dd20ebc. The hash now covers every `*.py` in the package except `__init__.py`, with file names hashed too | PASS |
| F9 rules text, inactive mid_close | `rules_secondary_texts` (stat noun masked) and `inactive_pids` → `inactive_mid_close` are wired into `cmd_score`; T510b only for the latter | PASS (R4 minor) |
| F10 lean+drift stress | Reported in STRESS; changes no criterion. Record: T510 part-1 false-pass 1.0 at −1.5c, which documents the exposure | PASS |
| Does the B-generator change weaken the primary dryrun? | `mu_b` now derives from `mu_m0` (M before the lean) rather than `mu_m`. With `m_bias = 0` (PRIMARY and the planted runs), `mu_m == mu_m0`, and the RNG draw order is unchanged (`mu_m0` normal, then `mu_b` normal). The committed record's primary and planted numbers are **bit-identical** to the pre-change record (`<SP>/lm_record.json`): t510_mean_part1 0.0001261082540092213, planted pass 1.0/1.0, min_q1 424/486/445 | Not weakened |

### New findings in the revision

- **R1 (major; escape hatch). `mark-unscoreable` accepts any reason at any time** (`__main__.cmd_mark_unscoreable`).
  After seeing a T510 PASS, someone can mark T510b unscoreable and land on the last table row ("T510's verdict … stand
  unmodified"). That dodges the row-3 withdrawal. They can also mark T510 unscoreable before its horizon. The frozen
  text allows "unscoreable" only for a failed dryrun or a horizon never reached this season (plus ruling A4 for T510b
  missing its own horizon). Fix: make `--reason` a choice of {`dryrun_failed`, `horizon_not_reached`}.
  `dryrun_failed` must verify that the committed record's `<rule>_dryrun_pass` is false (or that `require_pass`
  raises). `horizon_not_reached` must take `--nba-db` and verify both that today's ET date ≥ a pinned
  `spec.SEASON_END` and that `horizon_date(rule)` on complete dates is None. For T510b it must also refuse when
  `T510b_result.json` exists. Add one refusal test per branch. `SEASON_END` must be pinned before any look; my default
  is the last regular-season ET date in `data/schedule/live_tips_2026-27.parquet`, and the main session confirms it.
- **R2 (major; escape hatch). `--out-dir` on `score`, `family` and `mark-unscoreable`** lets anyone re-score into a
  fresh directory. That defeats both the once-only record and the T510-first order. Fix: remove the `--out-dir` CLI
  arguments (`main()`: the `p.add_argument("--out-dir", …)` under score, `f.add_argument`, `u.add_argument`) and read
  `spec.REPORT_DIR` directly. Tests monkeypatch `spec.REPORT_DIR` instead. Also commit each result record right after
  it is written (process note for the main session).
- **R3 (minor).** selection.py: an absent rung at S0 increments both `rung_absent_at_snapshot` and
  `S0_quote_fails_filter`, so the drop table double counts it. Remove the `S0_quote_fails_filter` increment on the
  absent branch, or label it "of which absent".
- **R4 (minor).** `loaders.inactive_pids` treats a missing `player_game_stats` row as inactive. Count "no box row"
  separately from "minutes 0/null", so an ingest gap does not read as a scratch.

### Drafted DECISIONS row (pre-look; not appended)

| When | What | Why | Reverses if |
|---|---|---|---|
| 2026-10-24 (pre-look) | T510/T510b "unscoreable" is declared only by code-verified condition: the committed dryrun record fails for that rule, or on/after a pinned SEASON_END the rule's horizon on complete dates is still unmet. Result records are written to `reports/line_move/` only, once, and committed when written. | A free-text unscoreable marker and a configurable output directory each let a reader choose the table row after seeing T510. | — |

R1 and R2 must be fixed before any real mid is scored. R3 and R4 can wait until the scoring date. Nothing in F1–F10
needs rework.
