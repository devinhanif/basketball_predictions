# MARKET_PRIOR_RESIDUAL (STUB: priors only, written before any gate number; the full DRAFT replaces this file)

Written Tue Oct 20 23:12 CDT 2026 (`date`), branch agent/modeler-market-prior-residual, base main 85a928f. Not blind to: the ledger rows
T204-T214, T389-T414, T415-T416, T493 and the row counts in the task brief. Blind to: every gate number of this rule (coverage by stat,
lead distribution, cluster counts, power proxy, feature-frame misses) and to every R, PL, or M-vs-A0 outcome contrast.

Priors (binary events, scored when the run lands):
- P(gate G1-G7 all pass on the real data) = 0.75. Risks: G7 (the feature frame and the stored model rows come from two data versions; 2024 stored OOF differs from today's by the 118-row backfill) and G4 (power at a -0.003 floor).
- P(PASS: checks a, b and c all hold on 2024) = 0.02.
- P(a: R minus M per-leg log loss <= -0.003 with CI upper < 0, widest clustering, pooled) = 0.04.
- P(b: disagreement coefficient positive, widest-clustering CI excludes 0) = 0.12.
- P(c: R beats PL by the floor) = 0.03.
- Expected R minus M pooled on 2024: +0.0004 (80% interval -0.0020 to +0.0035). R is a regularised tree on a few thousand legs per stat with the market as offset; the likely error is noise fitted on top of a hard-to-beat price.
- P(the design collapses to ODDS_HISTORY H2, meaning R minus the walk-forward w-only blend is inside +-0.001 pooled and R carries no information that blend lacks) = 0.55.
- P(Devin hears "keep your money" from this rule) = 0.97.
