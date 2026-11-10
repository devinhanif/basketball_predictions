> **Superseded by `docs/prereg/GAME_SCRIPTS_FORWARD.md`** (draft, 2026-11-10); this file is kept as history and is not a rule.

# DRAFT forward rule: script calibration on 2026 games (NOT frozen, NOT run)

Status: draft written 2026-11-10 after adversary review `docs/reviews/combo_math_2026-11-10.md` (finding B1). A script may be labelled
`calibrated` only after a rule like this is frozen by a hand that can commit, before any look, and then passes. Nothing here has been run.

- Question: do script-conditional leg probabilities (`combo_params_v1.json`, fitted on 2023-24, no refit) match 2026 outcomes?
- Data: season 2026 games only, once at least 20 games exist and, per script, at least about 150 games that realised the script (about
  +-0.04 on the same-player joint ratio). Legs: overs at the posted main x.5 line of the production forecast, stamped before tip,
  conditional on the player playing. Marginals: the shipped LINE_CALIBRATION map. Realised scripts from the final score with the as-of
  `mu_t` stamped before tip (the game model that produced the payload, not refitted).
- Per script, all three must hold, game-clustered 95% CI, 2,000 resamples:
  (i) paired Brier difference (script-conditional minus unconditional) has CI upper bound < 0;
  (ii) observed minus mean conditional prediction has its CI inside +-0.02;
  (iii) EQUIVALENCE for same-player pairs: the CI of observed / predicted conditional joint lies inside [0.95, 1.05] (replaces the old
  "CI contains 1", which a small sample passes more easily).
- Multiplicity: four scripts, conjunctive per script; report all four, BH not needed for a conjunction but the family size is stated.
- Below the minimum number of games the verdict is "insufficient sample" and the label stays `exploratory`.
- No parameter, band, definition or threshold changes after the first look; a new question gets a new rule.
- Prior (for PREDICTIONS_LEDGER, adversary): P(grind passes) 0.45.
