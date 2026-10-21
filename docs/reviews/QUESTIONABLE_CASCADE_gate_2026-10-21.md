# QUESTIONABLE_CASCADE: adversary gate review (2026-10-21)

Subject: `docs/prereg/QUESTIONABLE_CASCADE.md` (DRAFT; rule-text hash `awk '/^=== RESULTS BELOW ===$/{exit} {print}' | shasum -a 256` = **d4479c50**0fab..., matches the brief; whole-file sha 87faf1f8), harness `research/eval/questionable_cascade_{features,gate,rule,eval}.py`, `reports/prereg_questionable_cascade/gate.json` (sha256 3dff9f02, matches the draft) and `rehearsal/results.json` (04d139ca, matches). Base: main f9fc32d. This is a gate review, not a pre-freeze review: the rule cannot be frozen as written.

Holdout: nothing here reads season 2025. Every query is `season <= 2024`; my two scratch scripts asserted it. DuckDB was opened read-only through `prereg_gates_common.connect()` (`read_only=True`). No C arm was fitted or scored. The only real-label quantities I touched are the per-row CRPS deltas of RECENT_ROLE_FULL's frozen arms (T467: R, PL, SHUF_R, SHUF_CTRL vs A0). I used them for their sd only, the same use the gate makes of FULL minus A0.

**Gate verdict: GATE FAIL DOUBTFUL.** The 0.00620 half-width depends on the proxy. The test's inability to resolve the plausible effect does not, and that conclusion stands.

Verdict: BLOCKED

(BLOCKED means: do not freeze the split design as written, and do not take option C. The gate number is WOUNDED: real, but not for the reason given.)

## Top finding

The gate's per-row sd (0.331) comes from FULL minus A0. Its size is set by **how far the arm moves the predictions**. It is not a property of the rows. On 2024 TM rows (n = 11,181), measured on the stored arrays:

| arm vs stored A0 (pts, 2024 TM rows) | mean abs median move (pts) | per-row sd of CRPS delta | binding deff | 95% half-width |
|---|---|---|---|---|
| FULL R (the gate's proxy, real y) | 0.423 | 0.331 | 1.02 (game) | **0.00620** (reproduces gate.json exactly) |
| FULL PL, within-date placebo refit (real y) | 0.216 | 0.176 | 1.36 (week) | 0.00380 |
| ORACLE shift, f = 1.0 (synthetic y, 3 seeds) | 0.431 | 0.304-0.305 | 1.14-1.20 | 0.0060-0.0062 |
| ORACLE shift, f = 0.5 (synthetic y, 3 seeds) | 0.216 | 0.154-0.156 | 0.93-1.08 | 0.0028-0.0030 |
| FULL SHUF_R / SHUF_CTRL (shuffled-label arms, real y) | 1.35 / 1.36 | 1.06 / 1.06 | 1.64 / 1.69 | 0.025 / 0.026 |

So the gate's sizing describes an arm that moves TM predictions about as far as the f = 1 (T476-sized) world. An arm that delivers a floor-sized effect (f of about 0.5) moves them half as far. Put that signal-sized sd (0.155) together with refit noise the size of the FULL placebo (0.176). If the two are independent, the sd is about 0.234 and the half-width is **0.0044-0.0048** (deff 1.02-1.2). That passes 0.005, and the rows needed fall to 8,600-10,100 against 11,181 available. The gate's 24% miss sits inside the proxy's own uncertainty: the plausible half-width range is 0.0038-0.0062.

This does not rescue the test. The rehearsal shows why. At f = 0.5 the oracle's CI is narrow (about 0.003), yet it passes only 7 of 30 seeds, because the **point** sits at the floor (mean -0.0041; per-seed -0.0016 to -0.0052). The CRPS gain grows roughly with the square of f: about -0.001 at f = 0.25, -0.005 at 0.5 and -0.021 at 1.0. The binding constraint is therefore the floor against a plausible effect size. Rows are secondary. That holds under any sd proxy, and it holds for B too.

The single most likely failure mode if the "FAIL" is wrong: the gate blocks a test with a valid sd proxy borrowed from a bigger-moving arm ("an invalid test is not a null"). If the claim "the test cannot resolve this" is wrong, the cause would be a real effect at least 0.6 times T476's post-hoc magnitude. That is unlikely: T476's magnitude is post-hoc, conditional on the star playing, and exposed to winner's curse.

## Per-check table

| # | check | command / source | n | number (CI or range) | pass/fail |
|---|---|---|---|---|---|
| 1a | Re-derive the pts 2024 half-width from gate.json inputs | `1.96*0.330728*sqrt(1.021508/11181)`; `(1.96*sd*sqrt(deff)/0.005)^2` | 11,181 TM rows, 882 games | hw 0.006196; rows needed 17,169 | PASS (arithmetic exact) |
| 1b | Is FULL minus A0 a fair sd proxy for C2 minus A0? | `scratchpad/attack_sd.py` (run from `scratchpad/qc_run`, `PYTHONPATH=<wt> uv run --no-sync --project <wt> python -W ignore attack_sd.py`, exit 0, 8 s) | 11,181 | sd scales with prediction movement: 0.176 (PL) to 0.331 (R); plausible C2 hw 0.0038-0.0062 | **FAIL as a basis for a hard FAIL**: the proxy is conservative for a floor-sized arm |
| 2 | Can the affected population be enlarged legitimately? | analytic, with gate.json counts (Probable 721 rotation player-games in 2024, r_P 0.946) | n/a | adding rows with expected vacancy of about 0 lowers t roughly as S/sqrt(n) | no legitimate enlargement helps (below) |
| 3 | Staleness: are the T-180 and T-60 tags the same thing? | `scratchpad/cadence2.py`, read-only, `season 2022-24`, exit 0 | 1,317-1,318 games a season | games with any report snapshot in [T-60, T-5): **0.23% / 0.15% / 0.08%** (2022/23/24); last pre-T-5 snapshot = last pre-T-60 snapshot in 99.77-99.92%; 2.5-2.6 snapshots a game | the "0.2% changed" is **absence of data, not stability**. Modeler right on direction, overclaims on certainty (below) |
| 4 | Positive control | `rehearsal/results.json` (30 seeds, B = 1,000) plus my 3-seed sd re-run | 11,181 | f=1 PASS 30/30; f=0.5 PASS 7/30, mean -0.0041; null CLOSED 30/30; wrong rows never PASS | PASS. The harness detects what it should. The "0.5-0.6 of T476" headline is honest, and it is about the floor, not the CI |
| 5 | Options A-D | below | | | |
| 6 | Lineage | `reports/prereg_returning_teammate_2/gate.json` `variants.OUT_N3.*.by_status_tonight` | RT2 returner-games 564 (2024) | 145 of 564 RT2 returners (26%) are Questionable tonight; that is about 9% of the 1,672 rotation-Q player-games | Confirmed and now **measured** (approximate; definitions differ) |
| 7a | Tests | `uv run --no-sync pytest tests/research/questionable_cascade tests/test_layering.py --no-cov -q` | | `30 passed, 5 warnings in 4.42s`, exit 0 | PASS |
| 7b | Lint / types | `ruff check` (4 files): `All checks passed!` exit 0; `ruff format --check`: `4 files already formatted` exit 0; `mypy` (4 files): `Success: no issues found in 4 source files` exit 0 | | | PASS |
| 7c | Path resolution | `grep -n 'Path(".")' research/eval/questionable_cascade_eval.py` (lines 218, 307, 452, 509, 592); `LT = Path("reports/lower_tail")` line 41; `CACHE = Path("data/ops/...")` line 42 | | | minor finding (below) |
| - | Rule integrity | `git show -s 3940016`: priors 11:25:59 CDT; gate measured 11:47 CDT; draft commit 1293537 11:54 | | priors precede the gate number | PASS |

The six adversary checks (shuffled labels, planted future, missingness, knockout, replication, integer support) are **n/a**: no outcome contrast exists. The planted-future unit test and the G7 missingness audit are in the draft and the tests pass. I did not re-derive G7.

## 1. Is the gate's sizing honest?

The arithmetic is exact, and the convention (RETURNING_TEAMMATE_2's) was declared before the number. The finding is that the convention mis-sizes this arm. The FULL-minus-A0 sd mixes two things. One is refit noise from re-growing the trees with extra columns: the FULL placebo, with no information, gives sd 0.176 and moves medians 0.22 pts. The other is FULL's real signal on recent role: it moves medians 0.42 pts and gains -0.0063 on these rows. C2 has nine columns that are constant on 60% of rows. Its refit noise is plausibly no larger than FULL-PL's. Its signal move, in a world where it could pass, is about 0.22 pts (oracle f = 0.5, sd 0.155). So a real C2 refit most plausibly has a **smaller** per-row sd than 0.331, about 0.18-0.25. It would be larger only if C2's refit noise exceeded FULL's, which nothing suggests.

The draft also has an internal inconsistency. The scenario power at f = 0.5 (0.39, `gate.json sizing.pts.2024.teammate_affected.scenarios.0.5`) uses the f = 1-sized se. That is wrong in both directions: the CI is too wide, and the line is `min(floor, -hw) = -0.0062` instead of -0.005. Under the f = 0.5 world's own sd the power is about 0.5-0.6 by the normal approximation. The rehearsal's empirical 7/30 is lower because the realised mean sits above the floor.

**Fix (recommend, not applied):** in `research/eval/questionable_cascade_gate.py` `sizing()` (line 311, `d = PR.crps_int(q1, y) - PR.crps_int(q0, y)`), also report the sd from FULL's PL arm (`RF.load_heads(stat, "PL")`, `pi_PL.npz`) and the oracle's synthetic sd. State the half-width as a range, `[PL-noise-only, FULL-R]`. Declare now, before any C2 number, which proxy the gate uses. Changing the gate's proxy after a FAIL is itself a rule change and needs Devin's line in DECISIONS. The honest framing is "the gate is indeterminate between 0.0038 and 0.0062", not "PASS".

## 2. Data capture: can the population grow without changing the question?

No. Each route either changes the question or dilutes it:
- **Wider rotation cut (min40 < 12) or Probable included.** These add tagged players with small expected vacancy: Probable is 1 - 0.946 = 0.054 of min10, and sub-12-minute players vacate few minutes. The added TM rows carry about zero signal. The mean effect per row falls about as n_signal/n while the half-width falls only as 1/sqrt(n), so t falls as S/sqrt(n). More rows, worse detection, and the floor is per-row. Both are rule changes: section 1 line 2 and section 4 define the population.
- **K days of Q status.** A tag from an earlier game is not tonight's coin flip. It is older information than the stored tag, and partly RETURNING_TEAMMATE_2's question. It is a different question.
- **The direction that does help is narrower.** For example, restricting TM to the existing slice "vacated >= 20" raises the per-row effect. It is label-free and pre-tip. Chosen after the gate, though, it is a population picked from the gate, which is the same smell as C. Not recommended.
- **A capture gap the audit understates.** In 2022-24 the store holds 2.5 snapshots a game, and **99.8-99.9% of games have no snapshot between T-60 and T-5** (check 3). 2021-22 had hourly reports in the history DB. If the league's per-release report PDFs for 2022-24 are retrievable, the true-T-60 history is repairable. The audit's "cannot be bought, only logged live" (section 9) is then unproven. This would not add rows, but it would make B test the live feature instead of the stale one. Cost unknown. Downloads go through Devin. Non-negotiable 8 requires listing it with a cost before the gate is called a data failure.

## 3. Staleness: weak evidence, or overstated?

The modeler is right on the substance and imprecise on the mechanism. The "status changed before tip-5 in only 0.2%" figure does **not** show that Questionable tags are stable. In 99.8%+ of games there is no later snapshot to compare against: the T-5 lookup returns the same T-180 snapshot. The brief's counter-reading ("the status rarely changes, so T-180 and T-60 tags coincide") is therefore not supported. The data cannot distinguish "the tag does not change" from "we did not record a later tag".

What the data does say is that the historical feature is a T-120 to T-300 tag. It is a weaker version of a live T-60 tag only if the live source publishes later updates, and that is an assumption about the 2026-27 report cadence I could not verify (2026 is not read here).

The brief's second point is correct and should be in the draft. Even a perfect T-60 tag does not show the play decision. What resolves late is mostly sit or play, which only lineups at T-30 reveal. The cascade's realistic ceiling is therefore the expected-vacancy oracle, not the realised one. The rehearsal's ORACLE is exactly that, so the harness already uses the right ceiling.

Net: "a historical null is weak evidence about the live tag" holds if the live tag is fresher. "A historical PASS is not inflated" holds. The draft should replace "the status rarely changes" style evidence with the cadence numbers above.

## 4. Positive control

The harness is sound. The null is CLOSED 30/30. The right-sized shift on wrong rows never PASSes (CLOSED 24 / INCONCLUSIVE 6 at f = 1; CLOSED 30 at f = 0.5). The oracle passes 30/30 at f = 1.

The headline "the floor is reachable only at about 0.5-0.6 of T476's size" is honest and is the real result of this draft. Three things make it optimistic, not pessimistic:
- the ORACLE knows the true functional form;
- it is scored with the 2023 rate 0.580, while 2024's real rate was 0.507;
- it has no refit noise.

The rehearsal's f = 0.5 failures come from the point estimate, not the CI (half-width about 0.003 at f = 0.5). So **the effect a real arm could plausibly deliver cannot clear the -0.005 floor on pts in any historical design, pooled or split.** That holds unless the true effect is at least about 0.6 of a post-hoc magnitude. reb, ast and fg3m are not rehearsed. Their gates pass on rows, but their expected effects under the T476-shaped world are unmeasured. Open item: rehearse at least reb.

## 5. Options

| option | what it buys | what it risks (CLAUDE.md rules) |
|---|---|---|
| **A. Close at the draft** | A clean recorded null of the "cannot be tested at the floor" kind, at zero compute. It honours "nulls are findings" and the modeler's own 2026-10-15 advice. | Nothing is lost if D is kept as the live question. Closing without a live rule would leave Devin's coin-flip question unanswered. |
| **B. Pooled 2023+2024, one test** | A number this month. The gate passes on any sd proxy (0.00449 on FULL's). | No select/report split; r_status is in-sample for 2023; the stale T-180 tag (check 3); point power at a floor-sized effect is about 0.5 at best (rehearsal-like). It spends the 2023-24 history for this question, so the one pre-registered follow-up then has to be the live one. **Legitimacy:** the gate is label-free with respect to C2 (FULL deltas are a frozen, already-scored arm), the floor does not move, and RETURNING_TEAMMATE_2's B is the precedent. So B is a legitimate single pre-registered test **if declared now**, logged as Devin's override, with no split variant run afterwards. It is sample-shopping, not floor-shopping. |
| **C. Split with pts floor -0.0065** | Keeps select/report. The bar is actually **stricter** (point <= -0.0065 vs the -0.0062 the CI already required), so there is no false-positive risk. | Choosing a bar from the gate's own number to make the gate pass breaks non-negotiable 8 in spirit. Power at a floor-sized world is about 0.2, so the likely output is an INCONCLUSIVE that reads like "no effect". Reject. |
| **D. Log live, test at a pre-declared n** | The only option that sees the T-60 tag the system will actually serve. reb, ast and fg3m are resolvable on one season (TM half-widths 0.0012-0.0028). | (i) The draft's D pools live pts with 2024, which **mixes the stale T-180 feature with the live T-60 feature**, the very skew the draft cites in favour of D. Declare pts live-only, or accept pts as descriptive. (ii) "Log the nine columns live" may need no live-path change if the forward pull already stores the raw official-report snapshot with `as_of`. Then D is a rebuild-at-season-end rule and needs no `nba/daily` change. If it is not stored, D is a live-path change and needs Devin's go plus the replay oracle. Verify before freezing. |

**Recommendation (one line):** close the historical test at the draft (A) and freeze D as a live-only rule now: reb/ast/fg3m decided on the 2026-27 log, pts live-only or descriptive, columns rebuilt from stored live snapshots; run B only if Devin wants a historical number and declares it now as the single test.

## 6. Lineage

- **PPLAY_INTO_PROPS_LIVE: confirmed not re-asked.** That rule changes only the point mass p and holds the conditional grid fixed. This one changes the conditional grid. One caveat for the draft: both rules will score the same live rows, so their families should be declared jointly, or the second to report should say so.
- **RETURNING_TEAMMATE_2: overlap confirmed and now approximately measured.** In RT2's OUT_N3 variant, 26% of returner-games are Questionable tonight in 2024 (145 / 564; 2023: 119 / 531). That is about 9% of this rule's 1,672 rotation-Q player-games. A modest overlap, not negligible. The draft's "unmeasured" (section 3) should cite this.
- **WOWY_STRUCT (T499)** is the right structural ancestor. Its one-season failure on the OUT vacancy effect supports a low prior here.

## 7. Code review

- **Minor: path resolution.** `questionable_cascade_eval.py` resolves the DB, components, configs and cache relative to the cwd (`Path(".")` at lines 218/307/452/509/592; `LT` line 41; `CACHE` line 42). The gate takes `--data-root` but still reads configs relative to the cwd (`questionable_cascade_gate.py` lines 79, 83). That is why the modeler needed untracked symlinks, and why my run needed a scratch dir of links. **Fix:** resolve read-only inputs (`nba.duckdb`, `reports/lower_tail/components_*.npz`, `data/ops/recent_role_full_cache`) from the main checkout, via `git rev-parse --path-format=absolute --git-common-dir` and its parent, as `research/eval/market_prior_residual_eval.py:52` does. Keep outputs and cache writes inside the worktree. The pinned component hashes already protect identity. Not a blocker, since the pins catch a wrong file.
- **Minor:** `prereg_gates_common.DB = ROOT / "nba.duckdb"` (line 41) silently points at the worktree. In a worktree without the link it errors (seen: `IO Error: ... database does not exist`). Safe failure, but the same fix applies.
- **Minor:** `gate.json` scenario power (section 1 above) uses the f = 1 se for every f.
- The knockout (C2 with its nine columns set to their means at predict time) is not implemented (draft section 8). Before any freeze it should exist: the ridge-ablation precedent says a gain carried by one block gets its as-of logic read line by line.

## Corrected family and power note

There is no result yet, so there is no BH family to correct. The declared family is four stats (pts, reb, ast, fg3m), BH q = 0.05. The OWN contrast is outside it, which is correct. If D runs alongside PPLAY_INTO_PROPS_LIVE on the same live rows, declare whether they share a family.

Power on pts, 2024 TM rows, n = 11,181:
- MDE80 under the gate's proxy: 0.0089.
- Under the PL-plus-signal proxy: about 0.006-0.007.
- Either way the floor needs an effect of at least about 0.5 of T476's size, so the pts test is **underpowered for the plausible effect. Cannot conclude is the expected output, and it is not "no effect".**

## Playbook check (class: gradient-boosted trees with a distribution layer)

1. **Information or capacity?** Information: the teammates' Q/D tag. In history it is a T-180 tag (check 3), so the historical test sees the stalest version.
2. **Enough data for this class?** Training rows are fine for trees. The binding constraint is report-season rows against the floor, and more fundamentally effect size against the floor (check 4).
3. **Test validity.** The integer grid, real tips, played-rows conditioning (stated), the within-date placebo and the planted-future test are all present. The sd proxy is the weak link (check 1). The knockout is missing. An invalid gate is not a null: the FAIL should be read as "indeterminate", not "proved too small".
4. **Lineage and inherited guards.** Confirmed (section 6). RT2 overlap is about 9% / 26%.
5. **What it scores against.** CRPS against A0, not the line. A PASS would not be a bet (T477).
- **Class checks.** Duplicated signal (`qc_evac_*` vs `vac_*`) is bounded by C2 minus C1 and PL. "Arm saw it" has no knockout yet.

## What was not verified

- Live 2026-27 report cadence: 2026 not read. Whether live snapshots are stored raw with `as_of` (needed for D without a live-path change).
- Whether the league's per-release report PDFs for 2022-24 are retrievable (no network).
- The real C2 refit sd. My range is an argument from FULL R/PL and the oracle, not a C2 measurement. `null --k 50` would measure the noise part for this exact construction.
- G7 missingness numbers (taken from the draft; tests pass).
- reb/ast/fg3m expected effects (not rehearsed).
- The RT2 overlap is approximate: RT2's returner definition is not the cascade's rotation cut.

## Recommended changes (for the main session; none applied)

1. Draft section 9: replace the hard "FAIL by 24%" with the proxy range 0.0038-0.0062 and the reason, and state that the binding constraint is effect against floor (rehearsal), not rows.
2. Draft section 5: replace the 0.2% stability reading with the cadence numbers (no snapshot in [T-60, T-5) for 99.8-99.9% of games; 2.5 snapshots a game).
3. Draft section 9 capture audit: list "per-release official report PDFs 2022-24, retrievability and cost unknown" as a repairable gap.
4. Option D text: pts live-only (no pooling with the stale 2024 tag); verify that raw live snapshots are stored.
5. Section 3: cite the measured RT2 overlap.
6. Harness: resolve read-only inputs via `--git-common-dir`; add the predict-time knockout before any freeze; fix the per-f se in scenario power.

## Drafted rows (not appended)

DECISIONS (draft, for Devin's choice): `2026-10-21 | QUESTIONABLE_CASCADE: historical test closed at draft (A); live-only rule D drafted (reb/ast/fg3m on 2026-27, pts live-only/descriptive) | gate half-width proxy-dependent (0.0038-0.0062), and a floor-sized pts effect needs >= ~0.5x T476's post-hoc magnitude (rehearsal 7/30 at f=0.5); historical tag is T-180 with no later snapshot in 99.8% of games | reverse if per-release 2022-24 report PDFs are recoverable (true T-60 history) or Devin declares B now as the single test`.

PREDICTIONS_LEDGER: none new from the adversary.
