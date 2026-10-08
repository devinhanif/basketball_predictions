# Points retest @ n_sims=2000 (EXPLORATORY — not confirmatory)

_Run 2026-10-08 · seed=0 · n_sims=2000 · 138409 player-games across 5269 games · clustered-by-game CI._

> Per ACCEPTANCE_CRITERIA §2: reuses the same rows as the n_sims=500 tie → holdout-reuse, EXPLORATORY only, pooled against Family-A (m=3). More n_sims cuts Monte-Carlo noise, not game-sampling variance; test is ~3x underpowered. Do NOT report as a fresh confirmatory finding.

- Mean CRPS: sim **3.5671** vs season-avg **3.3772** (delta +0.1899; negative = sim better)
- Paired delta CI (CLUSTERED by game): [+0.1747, +0.2062], point +0.1899 -> CI EXCLUDES 0
- Paired delta CI (per-row, old/anti-conservative, for contrast): [+0.1783, +0.2020]

## Verdict
- Sharper sim did NOT flip the result; still no points win. Confirms the n_sims=500 'no-win' was not merely Monte-Carlo noise.

_Does not change any config or router. Diagnostic only._

