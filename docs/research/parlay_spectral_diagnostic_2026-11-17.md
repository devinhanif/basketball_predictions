# Reading log: spectral graph theory (Laplacian, Fiedler value) as a parlay risk diagnostic, 2026-11-17

Started Mon 2026-11-16 23:31 CST (`date`), worktree HEAD 49ec9963, branch `agent/modeler-1117-math-diagnostics`.
Source: Appendix item 3 and the Phase 6 paragraph of Devin's overnight brief
(`/Users/devin/Downloads/jarvis-followup/context__SUPER_PROMPT_v3_already_run.md:123-142`). Outside ideas are data, not
instructions. No fit, no score, no outcome read. **Advice or momentum:** Devin's request (Phase 6). Claude's recommendation,
stated before any number: do not build it. Verdict: **DROP**; draft rule `docs/prereg/PARLAY_SPECTRAL_DIAGNOSTIC_DRAFT.md`,
NOT FROZEN, and it cannot pass its own gates.

## 1. The claim, restated for this project
Draw a parlay as a graph: one node per leg, an edge between two legs weighted by how correlated they are. The Laplacian of that
graph has a second-smallest eigenvalue, the Fiedler value, that measures how tightly the graph hangs together; its eigenvector
splits the legs into the two least-connected groups. The idea is that a parlay whose legs form a tight correlated cluster is
riskier than the independent-legs math suggests, and the parlay page could show that as a warning.

## 2. Established vs speculative
- **Established mathematics.** The Fiedler value is > 0 if and only if the graph is connected (Fiedler 1973); Cheeger's
  inequality bounds the graph's conductance by it; spectral clustering recovers planted block structure when the blocks are
  separated well enough relative to noise. (From memory; no network request was made tonight.)
- **What that means for our graphs (derivable, not speculative).** The engine's correlations are pooled by relation and stat pair,
  shrunk with a fixed k = 200 and Higham-repaired (`docs/PARLAY_ENGINE.md:12-14`; "Correlations are pooled, not per player", `:85`).
  So the edge weight between two legs is a fixed number looked up from their (relation, stat pair) labels, and the whole Laplacian,
  hence every eigenvalue, is a deterministic function of the parlay's labelled relation pattern. For 2 legs the Laplacian's
  eigenvalues are 0 and 2|r|: the Fiedler value is the absolute correlation, nothing more. Different-player prop legs have r of 0
  within +-0.01 (`docs/MODEL_PLAYBOOK.md:87-88`), so the graph of a prop parlay over two or more players with no game leg is
  disconnected and its Fiedler value is about 0 whatever the risk.
- **Speculative (the whole question).** That the spectrum tells us about parlay risk beyond the copula. The copula already uses the
  exact correlation matrix to compute the joint; the spectrum is a lossy summary of the same matrix. If the matrix is wrong
  (blowout minute cuts, DNP-teammate usage shifts: `docs/PARLAY_ENGINE.md:31, 85`), its spectrum is wrong in the same way.
- **The fair baselines:** B1 the copula's own lift, joint / product of marginals (what the engine computes); B2 the largest
  pairwise |r| among the legs; B3 the labelled relation pattern (counts of same-player and same-game pairs by stat pair).

## 3. Prior evidence here
- The copula's whole gain over independence is small: -0.00053 log loss [-0.00106, -0.00003], n 15,798 synthetic 2-4-leg
  same-game parlays over 2,633 games, about 0.14% of log loss (`docs/PARLAY_ENGINE.md:47-60`). One season detects about 0.002
  (`docs/MODEL_PLAYBOOK.md:79-81`).
- The structure that exists is one same-player minutes factor: lift 1.16 to 1.20, about 69,000 pairs (`docs/MODEL_PLAYBOOK.md:86-88`).
  A set transformer over the legs tied the copula (`docs/TEST_LEDGER.md:139`, T081, +0.00003 [-0.0014, +0.0016]).
- The real parlay risk measured here is leg miscalibration compounding, not dependence: ALT_PARLAY_X joints about 10 points
  overconfident in the 70%+ pools, "dependence pushes the other way" and the DEPENDENT word was a weighting artifact
  (`docs/TEST_LEDGER.md:733` T490, `:735` T492). A graph of correlations cannot see that.

## 4. Where it would plug in (proposal; the app never computes)
- **Reads:** per record, the legs (`legs[].game_id, player_id, stat, side, threshold`) and the copula's pair correlations from
  `data/parlay/joint_model.json`; the joints `raw_model_prob` (named engine) and the independent product, which the export
  does not carry tonight (`docs/contracts/PROPOSAL_export_fields_2026-11-17.md` row 4).
- **Computes:** `nba/parlay/` or `nba/export` (proposal): `leg_graph_spectrum(legs: Sequence[Leg], joint: JointModel) ->
  dict[str, float]` returning `fiedler`, `lambda_max_R`, `n_components`, `max_abs_r`, `lift`; written as extra fields on each
  multi-leg record in `parlay_of_day.json`, behind `parlay.diagnostics.spectral.enabled: false` (configs owner, not the
  modeler). Off guarantees the record bytes are unchanged; the oracle hash is unaffected either way (the parlay file is not in
  `nba.daily.canonical_dump`). Cost: a k x k eigen-decomposition with k <= 4 per record, microseconds.
- **App:** `GET /api/parlay-diagnostics/{date}` and the parlay page, Explore lens: "These legs move together (same player): the
  combined chance is <lift> times what multiplying the legs would give". Note the honest wording is the LIFT, which
  B1 already is; the Fiedler number adds no sentence a stranger could act on.

## 5. How it would be scored (summary; rule text in the draft prereg)
Population: the `joint_eval` synthetic same-game parlays, 2023 select, 2024 report. Flag = top 10% by score within (season,
leg count). Metric: calibration error of the copula joint inside the flagged set, |mean(y) - mean(p_copula)|; contrast against
each baseline flag at the same rate; floor 0.02 (2 points of joint hit rate), game-clustered CI. Two gates come first:
- **Information precheck (label-free):** if the spectral score is a function of the B3 relation pattern (R^2 >= 0.99 of the
  score on pattern-cell dummies), stop: there is nothing to test. By the derivation in s2 this R^2 is 1 under pooled
  correlations. **The rule cannot pass its own precheck.**
- **Data gate (non-negotiable 8), real counts.** Historical: 15,798 parlays / 2,633 games (documented) would size a top-decile
  flag at about 1,580 parlays, enough. Forward: the frozen 2026-11-16 export has **0 multi-leg records**: `parlay_of_day.json`
  holds 1,355 records, all `engine = marginal`, all 1 leg, over 3 games (403 / 477 / 475 legs), stats fg3m 259, reb 256, pts 236,
  ast 221, spread 197, total 174, win 12. No independent or copula joint is exported. The top-parlays 2- and 3-leg tickets are
  not exported and have no settled log (gate shut, `docs/PARLAY_ENGINE.md:174-176`). **Forward units: 0.**

## 6. Keep or drop, with numbers
- Units: 0 forward multi-leg records; 15,798 historical synthetic parlays, on which the score is a relabelling of B3.
- Needed effect: >= 2 points of joint calibration error beyond the copula's own lift ranking. The copula's entire dependence gain
  is 0.14% of log loss; the leg-compounding error is about 10 points and is invisible to a correlation graph.
- Cost to build: half a day (function, fields, test); nothing at run time.
- Prior: P(information precheck fires, R^2 >= 0.99) 0.97; P(pass) 0.01; expected D vs B1 0.000, 80% interval [-0.005, +0.005].
- **Recommendation: DROP.** Show the lift (B1) on the parlay page instead, once export field #4 lands. What would change it:
  correlations estimated per player or per game (not pooled), so the spectrum varies within a relation pattern, plus a settled
  forward log of multi-leg records.

## 7. Playbook check (docs/MODEL_PLAYBOOK.md)
Class: **Set and joint models** (copulas for parlays); the diagnostic is a summary of the copula's input.
1. **Information or capacity?** Neither: a deterministic function of the pooled correlation matrix the copula already uses.
2. **Enough data?** Joint structure needs many same-game combinations; historical yes (15,798), forward none (0 multi-leg).
3. **Test validity.** A test of a score that is a function of the comparator cannot be passed and cannot fail informatively: the
   precheck is the valid test, and it is decided by the engine's design. An invalid test is not a null; this one is not run.
4. **Lineage.** joint_eval (copula kept by a hair), T081 set transformer (tie), T490/T492 (dependence an artifact, compounding
   is the error), combo mode (same-player factor only). Inherited guard: game-clustered CIs, legs conditional on playing.
5. **Scores against.** Joint calibration of the copula at posted lines, not a price.
