# PACE_TOTALS: a possessions x efficiency game-total model, scored against the market total (pre-registration)

Status: **FROZEN** (2026-10-12; Devin approved, DECISIONS e24b56c). The main session commits it as `prereg(PACE_TOTALS): <sha256>` before any fit.
Seasons: 2023 = 2023-24 (select), 2024 = 2024-25 (report). Season 2025 is never loaded and never touched under this rule.
Draft by the modeler. Gates below were measured on 2023-24 inputs only, no model was fit (reports/pace_periods_2026-10-12.md, section C).

## Why (Devin: "no way fitting the games to the pace model doesn't work")
A total is possessions x points per possession. The props pace diagnostic (same report, section A) found no pace signal in the
production props residuals after r20 points-for/against. That does not settle the game total, where pace is the first-order driver. But the
market line already carries it: corr(line, as-of expected pace) = 0.78 in 2023, and corr(actual total minus line, expected pace) = 0.003
(partial 0.025, Fisher 95% [-0.029, +0.079], n = 1,318). Prior (mine) that any pass clears the full rule: about 5%. A clean "the line already
has the pace" is a result we publish.

## Fixed design
- **Unit**: one regular-season game with a full-game market total at T-60 and as-of inputs for both teams. Periods q1..q4, h1, h2 secondary (same construction, own share).
- **P (pace x efficiency)**: `pi` = mean of the two teams' rolling-20 (min 5, shift 1, as of the game date) possessions per 48 from the `possessions` table (OT-adjusted).
  Efficiency per team: rolling-20 points per 100 possessions for (off) and against (def). `ppp_h = lg + (off_h - lg) + (def_a - lg) + hca/2`, `ppp_a` likewise with `- hca/2`, `lg` the
  league mean of the fit window. `x = pi * (ppp_h + ppp_a) / 100`. `mu_P = c0 + c1 * x` by OLS on the fit window; `sigma_P` = fit-window residual sd.
  Total-points distribution: Normal(mu, sigma) on the integer grid (continuity-corrected), the same support for every arm.
- **M (market alone)**: Normal(`line`, `sigma_M`) on the integer grid, `line` = Pinnacle where quoted, else median across books; `sigma_M` = fit-window sd of (total - line). The market's own
  de-vigged over price at its line is carried as `p_over_mkt` for the log-loss and decision comparisons.
- **B (blend)**: mean = `line + w (mu_P - line)`, `w` in {0, 0.05, ..., 1.0}, sigma refit; fit by integer CRPS. Walk-forward by calendar month (months strictly before X; minimum 600 prior games, else skipped
  and counted; 2023 burn-in expected). **B is the only arm that can pass; P alone is descriptive.**
- **Scores**: (a) integer-grid CRPS of total points, B minus M, paired, game-clustered 95% CI (B = 2000, seed 20261012); (b) log loss at the market line: P(over) from each arm's distribution vs the realised
  over event, pushes dropped, B minus M; (c) decision score per `docs/DECISION_SCORING.md` on totals: bet the side with the larger EV at the actual DraftKings T-60 price, EV >= 5%, one unit, ROI per unit with a
  game-clustered CI, n; Pinnacle price descriptive. Below 300 bets the decision cell is `insufficient sample` and cannot pass.
- **Slices (report season, no pass role)**: season half; expected-pace tercile; line tercile; back-to-back either team; Pinnacle vs consensus-fallback line; period totals q1..q4, h1, h2.

## Pass condition (family = full-game total: CRPS, log loss, decision score; BH over the 3 primary tests at q <= 0.05)
All on season 2024, walk-forward:
1. CRPS: B minus M <= **-0.05 points** (about 0.5% of a ~9.9-point total CRPS) with 95% CI upper < 0. Practical floor chosen as the smallest gain that moves a total's median by a tenth of a point.
2. Log loss at the line: B minus M <= **-0.002** with CI upper < 0.
3. Decision guard: B ROI at EV >= 5% >= **+1.0%**, CI lower > 0, n >= 300, and B minus market-lean ROI (same count, games resampled jointly) CI lower > 0.
4. Select-season sign: on 2023 (same walk-forward) points 1 and 2 have negative point estimates and 3 a non-negative ROI.
5. Guards (below) all clean.
Verdict: `passes` | `fails_crps` | `fails_logloss` | `fails_decision_guard` | `fails_guard`. If none passes the verdict is **`the_line_already_has_the_pace` / defer to the market**.
Period totals (6 targets, BH within that family) are secondary and can only add a `descriptive_support` note, never a pass.

## Guards (all run through the identical pipeline)
- **Planted-future**: inflate every score and possession count from a mid-season date by a large constant; every feature, `mu_P` and B forecast of earlier games must be unchanged (max difference 0), later ones must move.
- **Past-only placebo**: replace each team's pace and efficiency histories with a permutation of that team's PRIOR games only (no future information, same marginal); B must not beat M by the floor, else the gain is not the pace/efficiency signal.
- **Non-inert shuffle**: permute `pi` and `ppp` across games within a month; P alone must get WORSE by >= 0.5 CRPS points than unshuffled P (the inputs are used), and B's gain must vanish. A shuffle that changes nothing means the pipeline ignores its inputs and the rule is void.
- **Missingness audit**: games with null pace/efficiency or no market line are listed by reason and season; no imputation; a gain concentrated in one reason class is a flag.
- **Fair baseline**: every arm scored on the same rows and the same integer support; M is not allowed a different window or a different sigma fit.

## Gates (measured 2026-10-12 against the real data; nothing fit)
| gate | 2023 | 2024 | need | pass |
|---|---|---|---|---|
| games | 1,318 | 1,320 | >= 1,200 | yes |
| games with possessions (median 197 / 198 per game) | 1,318 | 1,320 | >= 99% | yes (100%) |
| both teams' as-of rolling-20 pace and points for/against (min 5 games, 2022 is warm-up) | 1,318 | 1,320 | >= 99% | yes (100%) |
| full-game total, any book, T-60 | 1,318 | 1,315 | >= 1,200 | yes |
| full-game total, Pinnacle | 1,267 | 1,279 | descriptive | 96% / 97% |
| pbp period scores reconcile with the box | 1,318 | 1,320 | >= 99% | yes (100%) |
| period totals q1..q4, h1, h2 (any book) | 1,318 each | 1,320 each | >= 1,200 | yes; Pinnacle quotes only q1, q2, h1 (2,531 games); q3, q4, h2 are consensus-only |
| EV >= 5% bets expected >= 300 in 2024 | not measurable before the fit | | | open: if B's w is ~0 it places few bets and the decision cell is `insufficient sample` |
Honest caveat on the gates: pace and the line are 0.78 correlated, so the gate on information content, not coverage, is the binding one and is only answerable by the fit.
Open assumption not verifiable here: the T-60 prices precede tip for every book/game (documented capture time tip - 64 min; per-quote `snapshot_ts < tipoff` not re-checked in this draft; the decision scorer asserts it).

## Hardware and cost
Closed-form OLS and NumPy only; a few seconds on the local CPU. No GPU. Entry point: `python -m research.eval.pace_totals_eval run --sha <frozen>` (refuses unless this text above the marker hashes to the sha and a `prereg(PACE_TOTALS): <sha>` commit exists).

## What each choice protects against
- Blend only passes: protects against celebrating a pace model that merely tracks the line (corr 0.78) while adding nothing the price lacks.
- Walk-forward monthly `w`: protects against a weight tuned on the months it is scored on.
- Floors in points and log loss, not p-values alone: protects against a CI-clean 0.01-point gain.
- Decision guard at the actual price with n >= 300: protects against a score gain that earns nothing after the vig.
- Past-only placebo and non-inert shuffle: protect against the opp_adjusted_ridge fingerprint (one feature group carrying everything, shuffle that does not bite).
- Period totals secondary and cannot pass: protects against a family of six hunting for one lucky period.
- One report season, no 2025: protects the holdout; a new idea gets a new id.

=== RESULTS BELOW ===
