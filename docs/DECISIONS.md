# Decision record

Direction-setting choices, three lines each: what we chose, why, and what would reverse it. The
ledger records what happened to each test; this records why we pointed where we pointed. Append-only;
dated. Not for tests (ledger) or incidents (best practices).

| Date | Decision | Why | What would reverse it |
|---|---|---|---|
| 2026-10-05 | Local DuckDB + Python, no cloud, read-only markets | cheapest possible; no employer infra; no execution risk | never on execution; cloud only if a VM passes the reachability test and saves real effort |
| 2026-10-07 | Frozen holdout = season 2025; one logged touch per model | three test seasons cannot separate close models; need one clean confirmation | rollover to 2026 once ≥20 games are played |
| 2026-10-08 | Injury report gated at tip − 60 min | the report is published on a schedule; 60 min is the latest we can rely on before every tip | evidence that a later cutoff is reliably available (T-30 lineups are a separate shadow arm) |
| 2026-10-08 | Production = injury-Elo + context-residual; the sim is not used for props | the sim lost the fair rematch on every prop; information beat architecture | a sim variant that beats production under a frozen rule |
| 2026-10-09 | Switch production training to real tip times | removes a train/serve mismatch found by the red team | never; proxy kept only as an option for comparisons |
| 2026-10-09 | History starts 2019-20, not 2013 | official injury PDFs exist only from 2018-12; earlier eras would recreate the missingness leak | a leak-safe way to use earlier seasons without injury features |
| 2026-10-09 | Score counts on integer support for every arm | continuous vs integer grids compared on integer outcomes rewards the grid for free | never |
| 2026-10-09 | Shadow arms (int, lt, t30) logged live; 2026-27 forward log decides, under frozen rules | 2024 was seen by all of them; the live season is the only clean test | the frozen checkpoints at 30/60/120 dates |
| 2026-10-09 | Agents in sequence (build → adversary → review → decide), one owner per directory | parallel fan-out produced swept commits and a broken pipeline | never; parallel only across non-overlapping areas |
| 2026-10-09 | CLAUDE.md rewritten as a partnership agreement; original spec archived | the spec drove effort toward architectures the data rejected; goals are built together now | edited together whenever either of us learns something |
| 2026-10-09 | The tool shows true odds identically after wins and losses | Devin: growth in skill feels like justification but clouds judgment | never |

| 2026-10-09 | F9/F11/F12 pre-registered from fan knowledge, frozen before any fit | the hypotheses came from Devin watching the game and all four descriptive checks agreed; the rule is committed first so creativity cannot reopen it | a new question gets a new rule; these are never edited after results |

| 2026-10-09 | F11 (lineup context) HELD until stint durations reconcile with box minutes (currently 48% within 1 min) | four fan hypotheses rest on stints; running F11 on untrusted lineups would waste its one shot | stint reconciliation ≥ 95%, or proof that possession-level lineups are right even if durations are off |
| 2026-10-09 | Coaches pulled right after officials, ahead of shots/matchups | ~120 requests; unblocks F14 ("coach's guy") | never |

| 2026-10-09 | Fix the lineup tracker (period-start look-ahead, no eviction heuristic, initial-prefixed name resolution) before any lineup-based test | possessions have the correct five on only 78% of rows; lineups are the unit the project now cares about | never; the reconciliation rate (target ≥ 95%, reference tracker reaches 91.8%) is the acceptance test |
| 2026-10-09 | Archive = `research/` in this repo, importable and tested in CI (DESIGN_RESTRUCTURE Q2) | every ledger result stays reproducible; CI cost is small | if CI time becomes a burden, mark research tests slow and run them nightly |
| 2026-10-09 | Pre-registration and holdout rules enforced IN CODE, with a logged override flag (Q4) | two rules were frozen wrong by a human on 2026-10-09; code catches what process misses | never; the override is the escape hatch, and every use is logged |
| 2026-10-09 | Game explainer: local pages for us first, under reports/explain/ (Q7) | publishing is outward-facing and Devin's call; design for us, decide later | Devin chooses to publish |
| 2026-10-09 | First lineup hypothesis after the tracker fix: F8 lineup rebounding (Q12) | cleanest mechanism; a measured live miss on HOU (+0.16 reb) | never; order only |
| 2026-10-09 | Smaller design-doc questions, decided by Claude (Devin may override): Q1 timing as the doc recommends (archive + edges before 10-18; renames and the daily rewrite in November, dual-run); Q5 archive the Colab tooling and drop colab-runner (no GPU jobs planned; restore if one is); Q6 `Known(T)` for new code first, retrofit production later; Q8 names as drafted (data/models/truth/markets/research/explain); Q9 model_gate moves to research with the rung ladder; Q10 the assistant stays, as the conversational face of the explainer; Q11 chunk replay per step, full replay per wave; Q13 retire team.py with the old agent names; Q14 daily briefing stays a few lines, weekly session for the deep look | these are implementation choices, not direction | Devin says otherwise |
| 2026-10-09 | Restructure guards are code: the replay oracle is `python -m nba.daily.canonical_dump` (sha256 of forward_predictions + forward_scores minus run-time columns), and the live/research boundary is `tests/test_layering.py` (two-way ratchet over the measured 18 edges) | a human list of edges was already wrong by two entries (coldstart.shrinkage, carryover); the test cannot drift from the code | never; the oracle may become a tolerance if Day 0 shows the replay is not deterministic |
| 2026-10-09 | `shrink_rate` and `carryover_blend` are live utilities and move to `nba/features/shrinkage.py`; the rest of `coldstart` (archetypes, SB classification) stays research and leaves the live path with the forward.py sim branch and the opponent config | five live feature modules call them on every run; archetypes and SB buckets reached their verdict (routing not kept) | never |
| 2026-10-09 | Amendment to the shrinkage row: only `shrink_rate` moved to `nba/features/shrinkage.py`; `carryover_blend` stays in `coldstart` | after edge 5 removed the sim branch, `features/time_decay` (the only live user of carryover) left the live path, so carryover is research again | a live module imports it |
| 2026-10-09 | Public face, three honest tiers in order: (1) the learning site: structure of the game, every claim with n and CI, nulls included; (2) the forecast record: probabilities stamped before tip, scored publicly next morning; (3) nothing sold as picks until the live log shows an edge after fees. Site = static pages generated by the morning job (the local explainer), publishing a gated step that is Devin's | Devin wants to market the project ("website to market", "microbusiness", "run the socials"); the market beats us on games and props are unproven, so the truthful products are understanding and the record, not picks | the live props log shows a positive-EV track record at the conservative bound, then a paid tier is on the table |
| 2026-10-09 | Social media: Claude drafts (pre-tip cards, morning recaps, a weekly "what the data taught us" piece, charts) into a daily queue; Devin approves and posts or schedules. Claude never holds account credentials or posts. Recommend disclosing the collaboration ("built by Devin, with Claude as the analyst") | posting is outward-facing (non-negotiable 6) and credentials are the same risk class as trading keys (non-negotiable 5); disclosure is the differentiator among anonymous tipsters and keeps the account inside platform rules on automation | Devin decides disclosure; the drafts step is added to the morning job only after opening night (live-path freeze 10-17 to 10-20) |
| 2026-10-10 | theoddsapi.com (Business, $99/mo) kept as the LIVE props benchmark (Pinnacle + US books, props from opening night), captured read-only pre-tip next to Kalshi; it is never a dependency of the daily job. The two-season historical comparison still needs the-odds-api.com (5M, $119 once) or the live log | its archive starts 2026-05-13, so it cannot answer the history question, but it is the only sharp props price we can put next to every prediction from game one | Devin cancels it, or the live log answers the props-vs-market question without it |
| 2026-10-10 | Wave A archive: 98 modules (40,780 lines) moved to `research/`, importable and tested in CI (research/INDEX.md). Judgment calls kept from Appendix A: the rung-ladder chain (model_gate, eval/run, rung1-3, sim/engine) stays in nba/ until `evaluate_gate` is cut out of registry/ops; `props/pts_tail.py` stays until `raw_quantiles` moves to lower_tail; `stack/oof.py` stays behind `--write-oof`; `parlay/independence_check` and `joint_eval` are live (the parlay CLI exposes them), so Appendix A's RESEARCH mark for them is withdrawn; Colab tooling lives at `research/colab/` not `research/colab_tools/` | each exception avoids a live→research import or an edit to a KEEP module tonight; the three cuts are small and listed for Wave B2 | the listed cuts land, then the remaining ~2k lines move |
| 2026-10-10 | A 12-line pickle-compatibility shim stays at `nba/sim/usage_redistribution.py` re-exporting `ReportTriggerConfig` from the live module | context-residual model caches written before today pickled the class under that path; without the shim a warm cache raises and the daily job silently falls back to recency (caught by tests/daily/test_review_fixes.py) | every cache on disk post-dates 2026-10-10, or the fingerprint changes |
| 2026-10-10 | Eighteen agent definitions replaced by six (archivist, modeler, adversary, steward, market, scribe) per DESIGN_RESTRUCTURE s5; the main session is the only committer and replaces `lead`; the adversary has no write access to `nba/` and reviews every `nba/truth/` diff; `team.py` and `team/` deleted (Q13); frozen pre-registrations keep their old agent names | the old files overlapped (three reviewers, three modelers, four operators) and carried the same ported rules in different words; one owner per directory needs one name per directory; the harness queued agents by the retired names and was referenced nowhere live; a frozen rule is not edited for a rename | an agent that needs two owners' directories to do its job, or a review the adversary cannot run without write access to `nba/`; a GPU job would restore a Colab runner from `research/colab/` |
| 2026-10-10 | Game explainer (`nba/explain`, local HTML pages under reports/explain/) reads the 2025-26 replay and the odds history read-only for DISPLAY: one game per page, every miss drawn like every hit, integer-support CRPS with n and a within-game CI, the market's de-vigged price beside ours, never the words lock/pick/bet, never an EV claim; descriptive only, no selection, nothing published | the project's purpose is to show the structure of a game honestly; a page that can only ever describe cannot leak into a decision | any page number feeds a decision, or Devin decides to publish (his call) |
| 2026-10-10 | Possession parser orders play-by-play with the lineup tracker's `nba.parse.ordering.in_game_order` (period, clock descending, substitutions first at a tie, then `action_number`); `possessions` re-derived once for all 5,269 games and `poss_idx` renumbered in 4,374 | `action_number` is not game order (the feed files corrections after the period end): 5,789 possessions ended after they started, in 4,258 games; the fix takes both defect counts to 0 and improves the two-team balance (mean 1.347 -> 1.145); the ADR 0001 formula error rises 0.06 and was kept as a ceiling gate (<= 2.5), not a no-regression gate, after a sample run showed the move (disclosed in docs/reviews/possession_order_2026-10-10.md) | the corrected order loses to the old one on a ground truth we do not yet have (an official possession count), or an upstream feed fix makes `action_number` reliable; restore from `data/backups/possessions_pre_order_fix_20261010T145505Z_possessions.parquet` |
| 2026-10-10 | Wave A follow-ups landed: the rung-ladder chain (model_gate, eval/{__main__,run,report,slices}, rung1-3, sim/{engine,possession_model}, features/{possession_features,player_features}, coldstart/config) moved to `research/` with `GateFailure/evaluate_gate/load_tolerances` extracted to `nba/registry/gate.py`; `raw_quantiles` moved to `nba/props/lower_tail.py` and `props/pts_tail.py` archived; `--write-oof` dropped from the production backtest and `stack/oof.py` archived; `nba/stack` no longer exists. Three 20-date replays identical (oracle_l/m/n = 732fba31). Note: cut 1 and half of cut 2 landed inside f11d95b by a shared-index mistake (a `git commit` without paths while agents shared the index); f11d95b and a2e7609 therefore fail CI on research imports; this commit repairs HEAD | the live path imported none of it (layering test), so the moves are tidiness that make `nba/` only what runs at 7 pm | restore from tag `pre-restructure-2026-10-10`; process rule: with more than one agent in the tree, commit by explicit paths only |
| 2026-10-10 | theoddsapi.com subscription cancelled by Devin (refund); the pre-tip `odds_capture` step is removed, the adapter stays (nba/odds, tested); the live props benchmark for 2026-27 is Kalshi only until another feed is chosen | the archive began 2026-05-13 and the two-season history question is answered by the-odds-api.com pull instead; $99/month for a live benchmark was not worth it to Devin | a live sharp props line is wanted again: re-add the step with a valid key in .env |
| 2026-10-10 | Four duplicate official ids merged in `game_officials` (kept id = more game slots, tie → lower id; mapping in `configs/officials_id_merges.yaml`, applied by the loader so new rows cannot reintroduce them; 6 rows changed, backup kept; data_version 85d8a41277ce → d7dea7140c8f) | REFEREES gate G0 item 2 needs name ↔ id to be 1:1; the duplicates are feed defects (a nine-digit id, a leading-1 typo, two legacy ids); no result had been seen, so this is a data correction, not a rule change | restore the backup parquet and remove the config rows |
| 2026-10-10 | `possessions.oreb` means "the trip was extended by a player-credited offensive rebound" (team rebounds with `team_id` 0 are not flagged); `parse_possessions` changed, segmentation did not (0 key or non-oreb cell differences over 1,047,292 rows). The stored column is NOT rewritten: the `--flags-only` gate failed (flag/box 0.876-0.884, corr 0.946-0.952 vs the gate 0.90-1.10 and 0.95), because a boolean cannot count two rebounds in one trip. Proposed: add `n_oreb INT` (player-credited offensive rebounds in the trip; reconciles to box OREB at 0.9997, corr 0.9999) and derive `oreb = n_oreb > 0`, backfilled with the same machinery | the old rule flagged 1.33x box OREB (team rebounds, 1,021 of 2,498 between free throws) and made F8 gate G3 fail at corr 0.79; the feed has the event (player `(Off:n Def:m)` counters sum to box exactly), so the defect was the rule, and the remaining shortfall is the boolean's representation; re-stating the gate for trips after seeing the numbers would be a rule change | Devin prefers a trip-level gate (a new rule, not an edit) or declines the column; restore is the parser line plus (if written) `data/backups/possessions_pre_oreb_fix_*_possessions.parquet`. NOTE: until the table is rewritten, games parsed after this change (`parse_games`) carry the new flag while older games carry the old one |

## 2026-10-10 2026-10-10 16:36 CT — Devin: "confirm everyone!!!"
What: in one message Devin confirmed the four items pending on him: (1) F8 lineup-rebounding pre-registration → FROZEN now
(sha256 2a8e581e) and run; (2) pull player_steals history from the-odds-api for 2023-24 and 2024-25 (≈0.25M credits; cap raised
accordingly); (3) a beat-reporter / social feed for minutes restrictions is wanted, WITH prompt-injection defences
("just worry about prompt injection"): allow-listed verified accounts only, text treated as data never instructions, regex-first
extraction into a fixed schema, no tool access for any LLM parser, known-at stamped, human review in the briefing, shadow-only until
its own pre-registration; vendor and plan to be named exactly before any purchase (Devin buys); (4) FOUR_FACTORS pre-registration
drafted for confirmation.
Why: each was argued in chat on 2026-10-10 with the data behind it (FAN_KNOWLEDGE, same date).
What would reverse it: F8 is frozen and cannot be edited (new question = new rule); (2) a credit cost above 0.4M stops the pull;
(3) any injection path that reaches a model input without the review step blocks the feed.

## 2026-10-10 ~19:10 CT — F8 lineup rebounding: CLOSED as a null
What: F8 ran under its frozen rule (sha256 2a8e581e, commit e30b750; research/eval/f8_lineup_rebounding.py; reports/prereg_f8.md;
ledger T252–T263). Rule 1 fails for reb (bigs −0.0009 [−0.0047, +0.0025], 2024) and pts (+0.0038 [−0.0005, +0.0080]); pooled pts
is worse in 2023 (+0.0035 [+0.0008, +0.0063]); the additive control ties A0 and the placebo is at or above A0. The oracle on actual
stints gains −0.0128 reb / −0.0292 pts in 2024 and the T-30 columns alone −0.0177 / −0.0434: the information is who actually shares
the floor, as F11 found. Closed; no promotion, no holdout row.
Why: the expected on-court rebounding environment built from the projected five adds nothing beyond production's teammate-OUT
features; the projected five does not recover the real one.
What would reverse it: a positive result in a lineup-aware T-30 model (the only lever seen). The one allowed follow-up is a
pre-registered learned player-trait embedding rule; not queued unless Devin asks.

## 2026-10-10 2026-10-10 16:58 CT — Devin: "do everything" → PTS_COMPONENTS, FOUR_FACTORS, ROUTER_TIME_WEIGHTED FROZEN
What: the three drafts of 2026-10-10 are frozen on this message and run in sequence by the modeler (ledger rows drafted by the
agents, appended by the main session). Hashes: PTS_COMPONENTS 7e6fd8f0;FOUR_FACTORS 0d87c2ce;ROUTER_TIME_WEIGHTED bb633f7a.
Knowledge graph (asked the same message): adopted as the information backbone only (an as-of fact store for players, teams,
coaches, injuries, lineups, reporter flags with known-at time), not as a model; design note to follow; no pre-registration.
Why: Devin confirmed; each rule's gate was measured against real data before the freeze (non-negotiable 8).
What would reverse it: nothing above a line changes; a new question gets a new rule.

## 2026-10-10 ~20:00 CT — Reporter feed for minutes restrictions: "whichever has a free plan" (Devin)
What: X API (free tier closed to new developers Feb 2026; reads billed per post) and xAI Grok X Search (per-post billing;
free-credit reports conflict, one log says ended April 2026) both cost money. The free candidate is Bluesky's public AT Protocol
API (public.api.bsky.app, unauthenticated reads of public author feeds). Decision: build the feed against Bluesky first, with an
allow-list of reporter handles Devin approves; buy nothing. Open check: which NBA beat reporters post restrictions there (a
read-only probe of the public API was not run yet; Devin to approve the call or run it). If coverage is thin, the fallback is
X pay-per-use at $0.005 per post read, a few dollars a month, which is Devin's purchase.
Why: Devin's answer; the injection defences (DECISIONS 2026-10-10 "confirm everyone") apply to any source.
What would reverse it: reporters' restriction notes not appearing on Bluesky within the first two weeks of the season.

## 2026-10-10 ~20:20 CT — FOUR_FACTORS: CLOSED as a null
What: ran under frozen sha256 0d87c2ce (commit 918e03c); null on pts, reb, ast, fg3m for pace alone and for pace plus the eight
factors (T264–T279; reports/prereg_four_factors.md). reb is slightly worse with CIs clear of zero. Closed; no promotion.
Why: production's rolling team points for/against and opponent-allowed columns already carry what pace and efficiency add;
the mechanism check shows no gain even in the fast-pace or high-allowed-ORB terciles.
What would reverse it: a new pre-registered idea, not a variant of this one.

## 2026-10-10 ~20:45 CT — PTS_COMPONENTS: CLOSED, candidate worse than production
What: ran under frozen sha256 7e6fd8f0 (commit 918e03c). The components model loses to production by +0.030 to +0.033 CRPS in
both seasons (T280–T283); the attempt-feature arm ties production; a threes-only head ties production's threes. Closed.
Why: production's single-number points model already carries the three-point information through the player's recent threes
and attempt rates; decomposing the output adds variance faster than it adds structure.
What would reverse it: nothing in this family; the one allowed follow-up is not queued.

## 2026-10-10 ~21:05 CT — ROUTER_TIME_WEIGHTED: CLOSED, no pass
What: ran under frozen sha256 bb633f7a (commit 918e03c; T284–T299). The router beats production on pts/reb/ast only because it
routes almost every row to the T-30 lineups-known arm; the best single candidate ties or beats it (rule 3 fails). Rolling-10,
rolling-5 and recency alone are all worse than production. Closed; the finding is the known one: T-30 lineup information.
Why: no candidate other than the T-30 arm beats production in any cell, so there is nothing to route between.
What would reverse it: a router beating the best single candidate by >= 0.003 on a stat with REAL T-30 snapshots (live season).

## 2026-10-10 ~22:05 CT — player_steals historical prices pulled (2023-24, 2024-25; never 2025-26)
What: Devin approved ("confirm everyone"); `history-pull --markets player_steals` (new option; state keyed by market set so the
finished markets are untouched) pulled 520,220 rows at 122,920 credits (dry-run cap 400k; config max_credits now 1.75M, spent
1.56M). Pinnacle quotes no steals; consensus and DraftKings/FanDuel exist; Novig/ProphetX late 2024 only.
Why: Devin's hypothesis that books under-price steals; a descriptive calibration check is the honest first look (no steals model).
What would reverse it: nothing to reverse; rows are deletable by market.

## 2026-10-10 ~22:20 CT — As-of fact store adopted as the information backbone (nba/facts)
What: `nba/facts/` (schema, builder, as-of query with post-tip refusal; docs/FACTS_STORE.md) built from existing tables with an
honest known_at per predicate; first real build: 87,530 listed_status, 103,785 played_minutes, 2,884 announced_starter, 3,961 tips,
87 head_coach (confidence 0.5; table known wrong). Not a model, no pre-registration; nothing in the live path reads it yet.
Why: Devin's "knowledge graph" question (2026-10-10); the data's lesson that information wins; one as-of query for the explainer,
the pretip job and the reporter feed (claimed edges with provenance = the prompt-injection defence).
What would reverse it: no reader adopts it within the season.

## 2026-10-10 2026-10-10 18:05 CT — Devin: "lets go" → MINUTES_HAZARD FROZEN (sha256 65fced23)
What: the minutes-as-hazard rule is frozen and run by the modeler (research-only code; comparator M2 from MINUTES_V2; props
downstream unchanged). Ledger rows drafted by the agent, appended by the main session.
Why: Devin confirmed; gate measured on real data before the freeze; the honest prior (props gain expected small) is in the rule.
What would reverse it: nothing above the line changes; a new question gets a new rule.

## 2026-10-10 ~23:55 CT — MINUTES_HAZARD stopped at its gate; `stints` drops short appearances (needs Devin)
What: the frozen rule (65fced23) failed its own missingness check: the on-court label from `stints` is missing for 5.8% of rows
under 10 minutes and for none above 20 (limit 2 pp). Cause: the stints table has no row for 40% of players who played under a
minute and 21% at 1-2 minutes. No fit was made (T300). The rule is not edited.
Options for Devin: (a) rebuild `stints` to include short appearances (data fix with backup, like the possession-order and rebound
flag rebuilds), then register MINUTES_HAZARD_2 with the same design; (b) register a new rule that imputes the label from box
minutes; (c) close the direction. Claude recommends (a): a known gap in a core table should be fixed regardless of this
experiment (F8 and F11 read stints; the T-30 arm does not).
What would reverse it: n/a until Devin chooses.

## 2026-10-11 ~00:05 CT — Devin: "do a comprehensive data clean up"
What: approval for a data-layer pass on nba.duckdb and the odds DB: (1) audit every core table for null patterns, duplicates,
coverage gaps and reconciliation failures (read-only, report first); (2) fix with a pre-fix backup under data/backups/, one table
per commit, each with its own gate before/after, a DATA_CHANGELOG entry and a data_version bump: stints (short appearances
missing), team_coaches (wrong head coaches, no mid-season changes), and whatever the audit finds; (3) apply the 61 AUTO steals
aliases (roster-verified) and reparse; the 11 REVIEW names stay unresolved until Devin names them. The replay-oracle golden hash
is re-baselined after the data changes (data changed, code did not; recorded as such). Holdout season 2025 rows are rebuilt
by the same deterministic code without being evaluated (a rebuild is not a touch).
Why: two experiments in one day stopped or were qualified by table gaps (rebound flag, stints); the coaches table is wrong.
What would reverse it: a rebuild that fails its gate is rolled back from the backup and recorded.

## 2026-10-11 ~00:55 CT — Devin's batch of confirmations
What: (1) "ship t30 fix": the T-30 reader dedupe is approved; committed by path when the steward's draft (test + replay oracle)
lands. (2) "fix minutes hazard 2": MINUTES_HAZARD_2 is pre-approved: same design as MINUTES_HAZARD (65fced23), re-registered
and frozen after the stint rebuild (F4/F8) passes its gate; its gate must pass before the freeze (non-negotiable 8). (3) Alias
rule for same-name players: the join key is the TEAM roster; 9 REVIEW names applied, J. Green and bare "Thompson" left
unresolved. (4) "I want a twitter/x block": the reporter feed is built against the X API (pay-per-use, read-only, allow-listed
handles, injection defences as decided 2026-10-10) instead of Bluesky; Devin signs up at developer.x.com (pay-per-use credits,
no monthly plan) and stores the bearer token as X_BEARER_TOKEN in .env; nothing is bought by Claude. (5) Coach clusters: Devin's
answer is that coaches redraw or spread by the philosophy they hold; recorded in FAN_KNOWLEDGE next to the data's finding that
the choice is not predictable from the coach across seasons. (6) Drive: gdrive:nba_colab/ridge_v2_sweep (4.397 GiB, 130 objects)
deleted on Devin's "please". (7) the-odds-api.com plan: Devin cancels after the month.
Why: Devin's message of 2026-10-11 ~00:50.
What would reverse it: (1) the oracle showing a change outside the previously-skipped games; (4) X pricing or terms changing.

## 2026-10-11 ~01:20 CT — Data clean-up pass: four standing decisions (from the audit, accepted)
1. **Possession reconciliation gate restated.** The hard gate is points: possession points == box points per team-game. The
   formula `FGA + 0.44 FTA + TOV − OREB` is a diagnostic only; it has run 1.6–2.0 above the parser in every season (MAE 2.0–2.25)
   while points reconcile in 98.1–100% of team-games. Reverse if points fail to reconcile on a fixed parser or the formula bias
   moves between seasons.
2. **Head coach by game.** `team_coach_games` (nba/data/coaches.py from a reviewed reference CSV, verified 2022-24) is the source
   of truth; `team_coaches` stays as the raw fetch and is no longer used for coach-by-game work. 2025-26 rows are written as
   unverified (NULL known_at) until Devin supplies or approves a source. Reverse on a dated source or Devin's correction.
3. **Lineup snapshots are read through `lineup_snapshots_latest`** (starter = OR over duplicate rows; status from the latest
   source_ts). Raw rows untouched. Reverse if the feed stops duplicating or the confirmed-lineup definition changes.
4. **Defaults recorded:** the 1,200-game live 2026-27 tips file is re-checked weekly (F3); the F2 fix added 10 neutral-site games
   and linked 148 report rows; 188 report rows with no game stay NULL as a loose end (not guessed).

## 2026-10-11 2026-10-10 20:14 CT — MINUTES_HAZARD_2 FROZEN (sha256 a11ae618) after one pre-freeze amendment
What: same design as MINUTES_HAZARD; gate 5a now passes on the rebuilt stints (on-court label gap 5.79 → 0.77 pp; max over all
inputs 1.66 pp, limit 2). Check 5c ("M2 reproduces its stored results to 1e-6") was amended BEFORE the freeze to: 2023 to 1e-6
(unchanged rows) and 2024 within 0.005 min of the stored mean, because the data clean-up moved 2024 rows from 2024-11-04 on by
0.0015 min; M2 is refit by the same harness so the H1-vs-M2 comparison stays like-for-like. Nothing was scored before the freeze.
Why: non-negotiable 8 (a rule that cannot pass its gate is not frozen) and Devin's pre-approval.
What would reverse it: nothing above the line changes; a new question gets a new rule.

## 2026-10-11 ~03:40 CT — Facts store: schedule-only seasons; lineup facts bounded by max-season
What: `nba.facts build --schedule-seasons 2026` adds only tips_at/home_team/away_team (source = schedule) for 2026-27 so the reporter
feed can attach claims to games; lineup facts are now also dropped for seasons above `--max-season` (the old filter leaned on
`games`, which has no future rows, so 2026 preseason lineup facts had leaked into the default build). Default build loses those
8,187 preseason announced_starter facts; nothing reads them.
Why: the holdout stays unread (no 2025 outcome fact) while the live season gets its schedule.
What would reverse it: a reader that needs 2026 lineup facts → replace `--max-season` with an explicit `--seasons 2022,2023,2024,2026`
list (queued in NEXT_SESSION) rather than widening the bound past 2025.

## 2026-10-11 ~04:00 CT — MINUTES_HAZARD_2: CLOSED, candidate worse than M2; "better pre-game minutes for props" closed
What: ran under frozen sha256 a11ae618 (T301–T314). The hazard model is 0.55–0.58 min worse than M2 on minutes CRPS with ~50% coverage
at nominal 80%, worse in every slice and Elo bucket, and worse than M2 when fed to props. Per the rule's kill criteria the direction
is closed: the pre-game minutes forecast cannot be improved enough to move props; the lever is information (T-30 lineups, reporter
restrictions), not minutes mechanics.
Why: between-game variance that M2's features capture is not reproduced by habit × foul × margin multipliers; the L-BFGS cap (200
iterations, conv=False in every block) is a possible contributor but tuning is forbidden by the rule and would not close a 0.5-min gap.
What would reverse it: a new pre-registered idea that adds pre-tip information; not a mechanism rewrite. The allowed follow-up is
not spent.

## 2026-10-11 ~09:30 CT — Typed hand-backs and two checks for agent runs (Devin: "yes on 2")
What: a fixed hand-back block appended to all six agent definitions; cost-ceiling and ledger-complete checks before every commit of
agent work (BEST_PRACTICES). From the agent-team email and the harness paper (docs/research/harness_design_paper_2026-10-11.md).
Why: four incidents in two days that a schema would have caught. No production impact.
What would reverse it: the block costing more context than it saves (measure: hand-back length vs incidents over two weeks).

## 2026-10-11 ~03:05 CT — Devin: "run everything overnight that you can, no need to check in w me for everything"
What: Claude runs the queued work unattended tonight, within the standing limits (no production code ships to the live path
without Devin's word; no money; no outward action; no holdout evaluation; data fixes only under the clean-up decision with
backups and gates). Scope taken: (1) HISTORY_WINDOW: after `hist:parse:2019` completes, PAUSE the ingest queue (SIGTERM, it is
resumable and skips done steps), run the history-DB prep (era flags, availability load), the G0 gate, and the frozen experiment,
then RESUME the queue; (2) same-surname stint rule with gate ≤ 20 and the 188 NULL-game_id report rows (archivist, data fixes);
(3) facts `--seasons` list and a DRAFT nightly rebuild step for ops/nba_daily.sh, replay-checked against oracle_golden2, NOT shipped
until Devin says so; (4) everything committed by path with ledger rows numbered as they land; the morning briefing carries it all.
Why: Devin's instruction; the queue pause is the only operational call inside it and is reversible (done steps are skipped).
What would reverse it: any gate failing stops that item; a conflict for nba.duckdb serialises on data/ops/heavy.lock.

## 2026-10-11 ~03:40 CT — HISTORY_WINDOW harness: unstated details confirmed before any data
What: research/eval/history_window_eval.py built against the frozen rule (sha256 9a1090be, matches the 2026-10-09 notice). Choices the
rule left open, fixed now and printed in the report: outputs under reports/history_window/ and reports/history_window.md; PASS = the
same stat passes on 2023 AND 2024 (the looser reading is reported, not counted); a 2/sqrt(n) noise floor on the sign-flip STOP so a
near-zero correlation cannot trip it by chance; NULL-pattern phi definitions; G0.1 game list = data/history/games/*.parquet; G0.3 from
the history possessions table; G0.4 "done" = ≥ 1 report row on the manifest date, plus the manual 20-date PDF spot check before
`run`; true-rookie = first played season in the union equals the evaluated season; history games without a real tip get no report
rows (no proxy). Confirmed by the main session; no number exists.
Why: non-negotiable 4 (nothing changes after a result) requires these to be written before the gate runs on real data.
What would reverse it: nothing after the run; Devin may ask for the looser PASS reading as a reported secondary only.

## 2026-10-11 ~04:30 CT — Facts build takes an explicit seasons list; nightly rebuild drafted, OFF
What: `nba.facts build --seasons …` (holdout absent unless listed; tested); a `facts` step in ops/nba_daily.sh after the morning
report, guarded by `NBA_FACTS_NIGHTLY=1` (unset: the script's behaviour is byte-identical; `sh -n` passes). Replay oracle on
data_version 1bee33d02ff2: hash 030804df… identical to golden2. Real build done (backup data/backups/facts_pre_seasons_20261011.duckdb).
Why: the reporter feed and the explainer need 2026-27 facts; the live path must not change without Devin's word.
What would reverse it: Devin sets the env var in the launchd job (ops/install_launchd.sh) to turn it on; removing the step turns it off.

## 2026-10-11 ~03:30 CT - DRAFT: alias evidence in the stint name book; availability rows resolved only by the report's own text (archivist)
What: (1) `_NameBook` uses the corpus of substitution out-side texts (exact ids) to separate same-surname teammates and to name
sub-ins the feed lists under another name; no minutes-based guessing, no jersey (not in the feed); absence of evidence never
selects a player. (2) 188 NULL-game_id availability rows were resolved only where the report's own (date, matchup) names a game
in `games` or a unique same-pair game within 7 days; 167 stay NULL with reasons, including 156 postponed beyond 7 days.
Why: brief 2026-10-11 ~03:05 (gates <= 20 missing, within-1-min not lower in any season, rows unchanged); both gates passed
(91 -> 1; 188 -> 167 with 0 changed). What would reverse it: restore data/backups/lineups_pre_fix_20261011T082617Z_*.parquet and
data/backups/player_availability_pre_f2b_20261011T082721Z.parquet; or disable alias use by passing no aliases.
Open for Devin: GSW@MIN 2026-01-24 (7 rows) would resolve to 0022500644 by game-id slot; not applied.

## 2026-10-11 ~09:50 CT — HISTORY_WINDOW: STOPPED at the coverage audit (T315); queue resumed
What: the frozen rule's own STOP rules fired on the 2019-21 injury reports (coverage −17.6 points in 2019-20; played-rate gap and
NULL-pattern asymmetry by era; report-column classifier AUC 0.80 with sign flips). No arm fitted. The ingest queue is resumed.
Why: the rule anticipated exactly this ("the single largest leak trap for the report features") and says the extension cannot be
evaluated cleanly. Devin's actual question (does MOV-Elo gain from more seasons) does not need report features, so the one allowed
follow-up is drafted as ELO_HISTORY_WINDOW for his confirmation.
What would reverse it: a report source for 2019-10-21 to 2019-11-09 and an era-balanced report design; not expected.

## 2026-10-11 ~11:05 CT — Devin: "Run everything, confirmed confirmed" (PPLAY_CONTEXT and ELO_HISTORY_WINDOW)
What: both drafts are confirmed. Each freezes the moment its minimum-data gate is measured on real data (non-negotiable 8) and
runs under its rule; results, ledger rows and the briefing follow without further check-ins. Also run today: the supervised live
pretip run from the opening-night checklist.
Why: Devin's instruction; the gates are the only thing between confirmation and freeze.
What would reverse it: a failed gate stops that rule before the freeze and is reported, not patched.

## 2026-10-11 2026-10-11 10:44 CT — PPLAY_CONTEXT FROZEN (sha256 3310de87) after two pre-freeze gate amendments
What: the gate measure (66,220 rows) found check 3 impossible as drafted (teams play 13-16 b2b second nights per 82 games; the
draft asked for 20) → threshold 10 (96.2% defined); check 1's 2024 reproduction is off by 0.0002 after the data clean-up → 0.005
tolerance for 2024 (HAZARD_2 precedent); P0 stays the deployed Platt P(play) with the raw p reported beside it. Checks 2 and 4 pass
(T-60 snapshots 99.85% of games; morning 99.3% of game days; max null gap 0.91 pp; planted tests pass). No arm fitted before the freeze.
Why: non-negotiable 8 and Devin's confirmation.
What would reverse it: nothing above the line changes; a new question gets a new rule.

## 2026-10-11 ~12:20 CT — Devin: "Do tracking and hustle, let's ingest that"
What: the ingest queue order moves history tracking/hustle (2019-21, + loads) and the 2025-26 tracking/hustle raw fetches ahead
of shots and matchups; the queue is restarted (resumable; done steps skipped). 2025-26 rows are raw post-game data, never evaluated.
Why: Devin's priority; the tracking/hustle screens are closed (T119-T123, T174-T175) but the history tables complete the data layer.
What would reverse it: nothing; order only.

## 2026-10-11 ~12:40 CT — PPLAY_CONTEXT: CLOSED, no pass (team habit is noise; the report tag is the information)
What: ran under frozen sha256 3310de87 (T316–T317). Large log-loss gains over the comparator, but the placebo matches the candidate
everywhere, so the team back-to-back term carries nothing; the gain is the T-60 report tag. Closed; the allowed follow-up (tag-only
P(play) vs the comparator) is drafted only if Devin asks. PRODUCTION NOTE for Devin: the deployed Platt map on P(play)
(`forward.P_PLAY_PLATT`, fit on 2023-24 slates) is worse than the raw hurdle p on 2023 and 2024 rows by ~0.07 log loss with ECE
~0.14; a production fix (drop or refit the map) is Devin's call and would go through the replay oracle.
Why: the descriptive team trait did not survive a placebo once the report tag was in the model.
What would reverse it: a multi-season team-habit term (history box scores) under a new rule, if Devin wants the follow-up spent there.

## 2026-10-11 2026-10-11 10:53 CT — ELO_HISTORY_WINDOW FROZEN (sha256 d5432e50) after two pre-freeze amendments
What: gate measured (3,637/3,637 history games, 0 ties; planted-future byte-identical; era flags 100%); check 2 could not pass on
today's games because the 2026-10-11 clean-up restored five 2024 neutral-site games after the registry run → check 2 now reproduces
on the registry's 3,951 keys (exact: |diff| 0.0) and the five games stay in the evaluation; "regular-season" → all games with a
result, playoffs included, as the stored baseline. No arm fitted before the freeze. E3 down-weights bubble/no-fans games only.
Why: non-negotiable 8 and Devin's confirmation.
What would reverse it: nothing above the line changes.

## 2026-10-11 ~13:10 CT — ELO_HISTORY_WINDOW: NULL; the history-window direction is closed for 2026-27
What: ran under frozen sha256 d5432e50. More seasons of scores do nothing for MOV-Elo: the long replay is identical by the evaluation
window (2024 delta exactly 0.0000) and re-tuning on four seasons is within noise. Injury-Elo remains production. This spent
HISTORY_WINDOW's one allowed follow-up; no further history-window rule this season.
Why: Elo forgets with a half-life of about a season; the 2022-23 warm-up already carries everything the ratings can carry.
What would reverse it: nothing in this family. (The early-2019 report-layout parser fix proceeds as a data repair on its own merits.)

## 2026-10-11 ~14:10 CT — P(play) Platt map OFF by default (production; Devin: "fix everything!!")
What: `ForwardConfig.p_play_platt` defaults to None, so the forward path's p_play is the minutes model's raw hurdle probability;
the old constants are one setting away. On 2023/2024 rows the mapped probability was 0.07 log loss worse than raw with ECE ~0.14
(T316–T317). Readers: the parlay engine's unconditional legs, mean_uncond/p_ge_uncond and the morning report now see higher
p_play (mean 0.85 vs 0.70); the 0.5 eligibility threshold already used the raw value, so no player's eligibility changes;
settlement and scores untouched. Replay oracle: only p_play, mean_uncond and p_ge_uncond moved on prop rows (46,320 of 46,658);
game rows, scores, T-30 decisions and row sets identical. New golden: 89889f6d (data_version 418510d401d3).
Why: a calibration map fit in-sample on one season made the live probability worse out of sample.
What would reverse it: p_play_platt=P_PLAY_PLATT in config; or a walk-forward-fit map that beats raw under a rule.

## 2026-10-11 ~14:20 CT — Nightly facts rebuild ON (Devin: "fix everything!!")
What: `NBA_FACTS_NIGHTLY=1` added to the daily-morning launchd job's environment (ops/install_launchd.sh generator and the live
plist; only that job was unloaded and reloaded; the other four untouched). The step runs after the morning report and rebuilds
data/facts/facts.duckdb (seasons 2022,2023,2024,2026 + 2026 schedule); exit 4 informational, never blocks later steps. Nothing in
the forecast path reads the store.
Why: Devin's instruction; the switch was the last thing he had left to himself.
What would reverse it: remove the variable from the plist and reload that job.

## 2026-10-11 ~15:10 CT — Devin: the business (parlayhitters.ai)
What: name/domain parlayhitters.ai; platforms X, Discord, TikTok, Instagram; Claude runs the socials (drafts and posts) on Devin's
accounts with API tokens managed the enterprise-safe way (secrets manager, least privilege, rotation; never in repo or chat);
positioning: a props assistance tool with paper trading, not picks; users see and can use the local props model's forecasts;
slip images read by a nightly bulk LLM pass (or OCR, whichever parses reliably), fields extracted, images deleted; hosting on the
VM + Cloudflare Pages; security best practices throughout; Claude handles customer service and escalates to Devin when needed;
monthly spend: "very little" (ceiling $30 until Devin raises it); Devin's time: his call. Defaults taken where unanswered: no
affiliate links until the honest tier pays its hosting; sole proprietor under Devin's name for now; US, 21+, no state-specific
content; email-only accounts; payments only in phase 4; uploaded slips usable for the bettor-flow model under plain terms.
Why: docs/BUSINESS_DRAFT_2026-10-11.md and Devin's answers of 2026-10-11 ~15:00.
What would reverse it: Devin; any platform's terms barring automated posting on his accounts (checked per platform before the
first post); any legal advice on the betting-adjacent positioning.

## 2026-10-11 ~16:50 CT — Devin: "i confirm everything, try everything and you have my approval"
What: MARKET_LINE_CLASSIFIER is confirmed: gate measured on real data, then frozen and run under its rule; the result goes to the
ledger either way. Standing limits unchanged (no money, no outward action, no holdout evaluation, production changes through the
replay oracle and Devin's word). Items that still need Devin's own hand are not unlocked by this: the X/TikTok/Instagram
developer apps and the reporter-feed token, the Jev router key, the accounts checklist (APP_SECURITY_BASELINE section 12).
Why: Devin's instruction.
What would reverse it: a failed gate stops the rule before the freeze.

## 2026-10-11 ~17:40 CT — Devin: "fix the props injury features and the structural asymmetry; think outside the box"
What: three new rules are drafted under Devin's "try everything, you have my approval" (16:50): OWN_TAG (the player's own T-60
report status as a production props feature; the model carries flag timing/direction but not the tag itself), CALIBRATED_GRID
(a trailing-window temperature/isotonic layer on P(over L) to fix the measured over-confidence: claims 75% where right 61%), and an
era-aware report definition (status- and reason-category-consistent `has_report` across 2019-24) with a re-audit; if the audit
reaches parity, HISTORY_WINDOW_2 is drafted as a new question. Each follows gate → freeze → run; results go to the ledger either way.
Why: Devin's instruction; the searches found no rigorous public result, only the practitioner themes we have already tested
(minutes first, conditional injury adjustments, recency weighting, pace) and one that we have not: the market as an input and
calibration layers (docs/research/harness_design_paper_2026-10-11.md will carry the reading-log addendum).
What would reverse it: Devin; a gate failing.

## 2026-10-11 ~18:20 CT — Era asymmetry diagnosed: do not redefine the report features; repair the STOP statistics under a new id
What: reports/era_asymmetry_2026-10-11.md. The frozen HISTORY_WINDOW audit's played-rate STOP is game-level (has_report = "the
game has a usable snapshot"): its no-report group is 9 games in six seasons (175 rows), its CIs are degenerate, and 2022 and 2023
sit 7 points from their own pooled value, so the statistic fails its own placebo; the gap_days phi STOP is left-censoring of the
history window (15.8% of rows before mid-November 2019 have no earlier played game vs 0.6% pooled; cold-start-matched, 2019 is
within 0.017-0.028 of 2022-24). Per-status play rates are stable across all six seasons (out 0-6%, questionable 68-78%,
probable 92-96%); what moves is the status mix. The report-column classifier AUC (0.80) comes from rotation-absence columns
(COVID absences, the 2021 hourly cadence) with NO sign flip against the labels. None of five report re-definitions reaches parity
under the frozen statistic because the statistic cannot move. Decision: production's report definition is unchanged; HISTORY_WINDOW
stays STOPPED as recorded (T315); a NEW rule, HISTORY_WINDOW_2, repairs the two STOP statistics (status-standardised played rates;
phi against a cold-start-matched baseline; every current season must pass against its own pool before the rule may stop
anything) and keeps every arm, floor and kill criterion of HISTORY_WINDOW. This is a change to a STOP rule after it fired and is
labelled as such; prior that the arms then pass stays ~20%.
Why: Devin's "fix the structural asymmetry" and the diagnosis above; the data was never the problem, the statistic was.
What would reverse it: Devin keeping the direction closed; the repaired audit still stopping.

## 2026-10-11 2026-10-11 15:05 CT — OWN_TAG FROZEN (sha256 2198c1f0); stored production reference to be re-baselined
What: gate passed on coverage (99.92% / 99.84%), tag share (8.0% / 8.3%), missingness (0.25 pp) and planted tests; check 4 amended
pre-freeze to the reference key set (the stored OOF reference predates the restored 2024 games). Standing fix queued: regenerate
the stored production OOF reference on the current data_version so every future gate reproduces on today's rows (steward/modeler,
after the four runs).
Why: non-negotiable 8; the third rule to hit the same stale-reference stop.
What would reverse it: nothing above the line changes.

## 2026-10-11 2026-10-11 15:06 CT — MARKET_LINE_CLASSIFIER FROZEN (sha256 b85ae0d7)
What: gate passed on real data (pairs 9,650-15,882 per stat-season; 100% game-line coverage; 0 leak-guard violations; M0 reproduces
ODDS_HISTORY to 1e-16; missingness gap 0.07 pp; planted tests pass incl. a bug the plant caught in the tier join). Frozen with its
gate-section choices (tier as-of join, spread signed to the player's team, consensus p at the consensus line, joint permutation
placebo, one pooled learner with a stat code, 2023 selects without Holm, 2024 confirms with Holm); Oct-Nov 2023 unscored warm-up.
Why: Devin's confirmation; non-negotiable 8.
What would reverse it: nothing above the line changes.

## 2026-10-11 ~19:20 CT — MARKET_LINE_CLASSIFIER: NULL; "the market as an input" closed
What: ran under frozen sha256 b85ae0d7 (T320–T324). The classifier is worse than the book in the select season on all four stats
and within noise in 2024; the one-weight blend from ODDS_HISTORY beats it everywhere. Closed; no follow-up.
Why: the book's price already contains what our forecast knows; a learner given both overfits where one shrinkage weight does not.
What would reverse it: nothing in this family; the budget tool keeps the ODDS_HISTORY stance (market as prior, weight by live track record).

## 2026-10-11 2026-10-11 15:12 CT — CALIBRATED_GRID FROZEN (sha256 43f9182c)
What: gate passed (7/7 post-warm-up blocks ≥ 3,000 rows; A0 reproduces production CRPS to 0.0 and ODDS_HISTORY line log loss to
2e-16 on the stored OOF rows; 16/16 planted probes inert; monotonicity exact). Pre-freeze amendment: C3's minimum is 2,000 DK pairs
(assists sit just under 3,000 per block). Universe fixed to the stored 2026-10-10 OOF rows.
Why: non-negotiable 8; Devin's pre-approval.
What would reverse it: nothing above the line changes.
Correction (same hour): the first freeze commit (5ad045f) carried the DRAFT status and an unamended C3 line because the status edit
failed its match; nothing ran under it. The doc was corrected and re-hashed before any run; the hash above is the real one.

## 2026-10-11 15:14 CDT (label corrected; was "~20:10") — HISTORY_WINDOW_2 not frozen; history-extension direction CLOSED for 2026-27
What: the repaired audit (status-standardised played rates, cold-start-matched phi, placebo requirement) still STOPs, because the
placebo threshold my brief specified is below the statistics' sampling noise; the repaired numbers themselves put 2019-20 and
2020-21 inside the current seasons' range, and 2021-22 (hourly report era) is the one real deviation. A noise-aware threshold would
be a third change to a STOP rule after seeing results; not made. The audit-v2 code and tests are committed for reproducibility.
Why: non-negotiable 4 and the no-variant-chasing practice apply to gates as much as to arms. Devin may reopen with a NEW question
(e.g. 2019-20 and 2020-21 only, excluding the hourly 2021-22 era) at his discretion; Claude's recommendation is to leave it closed
and spend the effort on T-30 and live information.
What would reverse it: Devin's explicit request for a HISTORY_WINDOW_3 with a CI-aware threshold or a two-season extension.

## 2026-10-11 15:18 CDT (label corrected; was "~20:30") — OWN_TAG: NULL (T326–T338); closed
What: the player's own T-60 report tag as a props feature adds nothing beyond the flag-history and team-OUT features production
already has; no stat near the floor; placebo-equivalent. Closed under its kill rule.
Why: the model reads the minutes shift from existing columns; ~1,000 questionable rows per season are too few to add more.
What would reverse it: nothing in this family; the tag's information lives in P(play) (PPLAY_CONTEXT), not in minutes given played.

## 2026-10-11 15:20 CDT (label corrected; was "~21:00") — CALIBRATED_GRID: CLOSED, no pass (T339–T339); the four "try everything" rules are done
What: calibration maps on the grid do not fix the over-confidence at the book's lines; a shuffled-label control beats every real
map on line log loss, so the only effect available is generic shrinkage toward 0.5, and every arm stays worse than the book.
Reading: the grid is calibrated overall (80% coverage 0.77-0.79) and over-confident exactly where the book places its line,
which is the line carrying information the model lacks, not a map-fixable defect. Closed. Tally for the day's rules:
MARKET_LINE_CLASSIFIER null, OWN_TAG null, HISTORY_WINDOW_2 not frozen (direction closed), CALIBRATED_GRID closed; the T-60
props model is at the ceiling of its information; the levers are T-30, reporters and slips, all live-season.
Why: the frozen rules; every one honoured.
What would reverse it: a calibration source that uses the line's information with a track record (the ODDS_HISTORY blend, live).

## 2026-10-11 16:21 CDT (label corrected; was "~22:40") — Product tiers: guidance free, honesty paid (Devin)
What: the free layer is guidance (the explainer, what the forecast and the market say about a leg, paper tracking of the user's
own slips). The paid layer is honesty: realised EV with intervals, "this price is fair, sit out" as a first-class answer, the
user's own calibration, closing-line value of their bets. Matches BUSINESS_DRAFT revenue option 1; the price stays open.
Why: Devin's answer to "would you accept a product whose most common output is sit out": yes, and that is the thing worth paying for.
What would reverse it: a month of users who pay for guidance and not for honesty (the slip ledger will show which features are opened).

## 2026-10-11 16:21 CDT (label corrected; was "~22:40") — The live-season benchmark becomes the CLOSING line (Devin)
What: in addition to the frozen FORWARD_PREREG_2026_27 scoring (unchanged; nothing edited there), a companion rule scores every
model-preferred leg by closing-line value (CLV): the closing de-vigged probability of the chosen side minus the T-60 one, plus
realised return at both prices. Historical version first (docs/prereg/MODEL_CLV.md, drafting; t5 as the closing proxy), then the
live companion rule, which needs a closing capture that does not exist today: the live sportsbook capture was removed on
2026-10-10 with the cancelled vendor. A per-game `close` capture via the-odds-api.com is being drafted behind NBA_CLOSE_CAPTURE
(default off, worktree); installing it is a production change and Devin's; keeping the the-odds-api subscription through the
season is a money decision and Devin's (he had planned to cancel after a month).
Why: every forecasting field that faced "the outcome is decided after the forecast" settled on the closing price as the measure
of skill; CLV is readable within weeks, realised return needs a season.
What would reverse it: the vendor dropping live NBA props, or CLV and realised return disagreeing in sign over a season.

## 2026-10-11 16:21 CDT (label corrected; was "~22:40") — "Train the model to beat lines now" (Devin): approved as a rule family, not a target swap
What: Devin approved training against lines. Reading: the production target stays the outcome (CLAUDE.md: markets are a prior,
not a target); the new work is a family of pre-registered rules whose METRIC is the line: MODEL_CLV (does the model's T-60 pick beat
the close), then a line-move learner (descriptive arm inside MODEL_CLV), then, if either passes, a shadow arm for the live season.
Also running under the day's approvals: XGB_PTS_VS_LT (the leak-free XGBoost pts arm vs production + lower-tail + integer support).
Why: MARKET_LINE_CLASSIFIER already showed a learner on model + line does not beat the one-weight blend on outcome log loss; the
untested question is CLV, which is the market's own currency and the first thing a sharp book would ask us.
What would reverse it: MODEL_CLV null on Pinnacle and DK cells (then the honest product is a price scanner plus the explainer).

## 2026-10-11 16:23 CDT (label corrected; was "~22:55") — Devin: keep the-odds-api through the season; MODEL_CLV and XGB_PTS_VS_LT run as soon as their gates pass; closing capture approved
What: "im keeping the api ... they run as soon as you're done, i approve." The subscription stays (money decision taken); the two
drafted rules freeze and run without a further check-in once each gate passes on real data (non-negotiable 8), pre-freeze
amendments labelled as usual; the closing-line capture merges to main behind NBA_CLOSE_CAPTURE and is switched on for the lineups
job once its tests and the replay oracle are green (production change approved here; the first night is verified by the morning
briefing).
Why: Devin's standing approval for the day's rule family plus explicit word on the two items I had marked as his.
What would reverse it: a gate failure (rule not frozen, no fit), or the capture's first night writing anything after a tip.

## 2026-10-11 16:32 CDT (label corrected; was "~23:10") — MODEL_CLV frozen (sha256 dd16a802) and run
What: does the production props model's T-60 pick beat the market's closing price (t5 as the closing proxy)? Family 20 cells (M1/M2 ×
Pinnacle/DK × 4 stats, M3 × DK × 4); floor +0.005 probability points of CLV, CI lower > 0, BH; DK cells must also beat the
fade-retail-toward-Pinnacle comparator; every cell must beat its 20-shuffle null (A1, kept). Pre-freeze decisions after the gate
measure: A1 kept, A2 not applied (M3 as drafted), A3 done, FanDuel descriptive. Gate 5/5 pass; 4 M3 × Pinnacle cells dropped (n < 300).
Why: Devin's "closing line" and "train to beat lines" (22:40, 22:55 rows); CLV is readable on history today and live within weeks.
What would reverse it: nothing in this rule; a null closes it, with the live companion rule as the one follow-up.

## 2026-10-11 16:36 CDT (label corrected; was "~23:45") — MODEL_CLV: CLOSED, no pass (T340-T381)
What: the model's T-60 picks beat the closing price by about a fifth of a point of probability, consistently, and a no-model side
rule earns most of that; nothing near the half-point floor. The line-move direction is predictable from the Pinnacle-vs-DK gap alone;
the model adds nothing to it. Closed on the select season.
Reading: the market finishes moving toward us a little; the model is not wrong, it is early by 55 minutes and small. For the product
this is the number to show honestly: "the line moved your way 0.2 points", not an edge.
Why: the frozen rule.
What would reverse it: the live companion rule with the real last-before-tip capture (the one follow-up), if the real close moves more
than the t5 proxy does.


## 2026-10-11 16:37 CDT (label corrected; was "~23:55") — XGB_PTS_VS_LT frozen (sha256 c74f1d1a) and run
What: does the leak-free XGBoost quantile pts model (hybrid_lf pts arm, frozen configs, refit identically) beat production as it now
stands for pts (context_residual v2 + lower-tail mixture, integer support)? Floor −0.005 integer CRPS, 2023 selects / 2024 confirms, BH
m=2, threshold log loss at DK lines not worse than +0.002, calibration, slices, minutes ≥ 5 no-harm, team concentration ≤ 25%,
proxy-safe slice. Gate 6/6 as amended (check 3: computed NULL-status exemption; check 5: key intersection ≤ 0.5%). Pass = shadow arm only.
Why: Devin: "rerun them now that they are uncontaminated, we should see if that was accurately thrown away." The leak-free rerun and the
red team already answered three of four stats; this is the fair test of the fourth. Prior pass probability ~5-25%.
What would reverse it: nothing in this rule; a null closes the XGBoost direction for 2026-27 with the lower-tail mixture as the answer.

## 2026-10-11 17:05 CDT — XGB_PTS_VS_LT: CLOSED, no pass (T382-T388); XGBoost direction closed for 2026-27
What: tie overall; XGBoost quantile better on the body (≥ 5 minutes, −0.015 to −0.021), the production lower-tail mixture better on the
near-DNP rows (+0.16 to +0.18), postseason and upper tail worse for XGBoost. Closed under the rule (P1 failed, no follow-up).
Reading for Devin: it was not thrown away wrongly. The leak-free gain was real where players play and it was a tail artefact where they do
not; production kept the tail fix and the body gain is worth about 0.5% of CRPS, below the floor once the tail is handled.
Why: the frozen rule.
What would reverse it: a NEW rule for a combined arm (mixture tail + quantile body), Devin's call; or the live log showing the body gain
matters at the lines (P3 was mixed: better in 2023, worse in 2024).

## 2026-10-11 17:10 CDT — Correction: this afternoon's time labels were wrong by about 6.5 hours
What: the headings above from HISTORY_WINDOW_2 to the XGB_PTS_VS_LT freeze were labelled "~20:10 CT" to "~23:55 CT" from a running
estimate carried across a context reset; the commits (7135a5b 15:14, d019cdb 15:18, ee9b572 15:20, 35f7aad 16:21, 62eabfc 16:23, 6e75614
16:32, a832d61 16:36, dafe0de 16:37 CDT) show the real times, now written into each heading with the old label kept in brackets. The same
wrong labels sit INSIDE the frozen status lines of docs/prereg/MODEL_CLV.md ("~23:10 CT") and docs/prereg/XGB_PTS_VS_LT.md ("~23:55 CT");
those blocks are frozen and are not edited; their true freeze times are the commit times 16:32 and 16:37 CDT. The TEST_LEDGER heading for
MODEL_CLV and the "Run ... 23:35 CT" line in MODEL_CLV's results section are corrected in place (below-the-line text).
Why: the record must carry real times; the launchd pretip windows and the morning briefing depend on them. Rule going forward: take the
time from `date` or the commit, never from memory (added to BEST_PRACTICES next edit).
What would reverse it: nothing; it is a correction.


## 2026-10-11 17:14 CDT — LLM shadow forecaster for 2026-27 approved (Devin: "lets go for it")
What: a nightly shadow arm, `llm_shadow`, in which the strongest Claude model receives ONLY the as-of facts the pipeline holds for a game
(facts store + injury report + T-60 lines + production's own numbers, nothing from the open web) and writes over/under probabilities for a
fixed set of legs before tip, stamped and refused after tip like every other forward prediction. Scored beside production under a companion
rule to FORWARD_PREREG_2026_27 (log loss at the lines, closing-line value, calibration), frozen before opening night, with a token budget per
night and a kill criterion. Never a production input; never a pick shown to users until it passes. Backtests on past seasons are
forbidden for this arm: the model has read those box scores (contamination in the weights), so the live log is the only test.
Why: Devin's question "could an LLM do better as a thinker over time"; the honest answer is a measured shadow, not an argument. Routing per
Jev: cheap model for fact extraction (later), strongest model for the nightly forecast.
What would reverse it: the frozen companion rule's kill criterion; or a nightly cost above the budget Devin sets.

## 2026-10-11 17:18 CDT — GAME_MARKET_CONTEXT frozen (sha256 fd37a3f3) and run
What: the market's T-60 spread and total replace (A1) or join (A2) the props model's Elo-margin and 20-game scoring proxies; placebo
arms with the market columns shuffled within date; A3 descriptive (no own-stat history). Floor −0.005 integer CRPS, BH over 8 cells per
season, must beat the placebo both seasons, calibration guards, DK-line log loss not worse than +0.002 (point estimate). 2023 selects on
its mature blocks (≥ 600 market-bearing training games, 12,757 rows: AM1, accepted because the 2022 training rows carry no market), 2024
confirms on all rows. Gate 20/20 (A0 reproduces the re-baselined reference exactly; market coverage 100% / 99.6%; known-at min lead 62.5
min; NULL gap by minutes 0.25 pp; 11/11 planted and placebo tests). AM2 (CI-upper non-inferiority) rejected for power.
Why: Devin (16:21 row): move the props model off proxies for game context onto the market's own numbers. Prior pass probability ~10-20%:
the market margin correlates 0.90 with our Elo margin; the total is the less redundant column.
What would reverse it: nothing in this rule; a null closes it with one live-season follow-up (the closing capture gives the live context).

## 2026-10-11 17:28 CDT — GAME_MARKET_CONTEXT: CLOSED, null (T389-T414); "move off proxies" answered
What: the market's spread and total neither replace nor improve on our Elo margin and 20-game scoring rates as props game context (all
cells within ±0.005 CRPS, placebo-equivalent); dropping the player's own stat history costs 10-13% of CRPS. Closed; one live-season follow-up
allowed under a new id once the closing capture has a season of real T-60 context.
Reading for Devin: the proxies were not the problem; the trees already extract what the market knows about blowout risk and pace from our
own numbers, and "predict points without viewing points" is a 13% worse model. The market's information lives in the player LINE (MODEL_CLV,
MARKET_LINE_CLASSIFIER), not in the game lines.
Why: the frozen rule.
What would reverse it: the live follow-up, or a market-context feature with information the proxies lack (e.g. live total moves after
lineup news), which is a T-30 question.


## 2026-10-11 17:56 CDT — LLM shadow job MERGED (560232f) behind NBA_LLM_SHADOW, off; backstop 2.5M tokens (A5)
What: nba/shadow_llm merged after the replay oracle on the branch reproduced the golden exactly (89889f6d, 46,658 predictions, 44,562
scores; first attempt from the bare worktree was not comparable: no data directory, 19:00 proxy tips, one run per date). Flag off, so the
working tree's live behaviour is unchanged; the only shared-path edit excludes model_name llm_shadow from settle_pending. Monthly token
backstop set to 2,500,000 (config, defaults, doc, test; the rule draft 0724ce2 says the same). The real provider runs only when
ANTHROPIC_API_KEY and MONTHLY_CAP_USD are set and NBA_LLM_SHADOW=1 is in .env; `uv sync --extra llm` installs the SDK first.
Why: Devin's approval (17:14); the oracle gate every structural change passes.
What would reverse it: a first live night writing anything after tip, or a schema-violation rate above 10% (the rule's G2).

## 2026-10-11 ~20:10 CDT — Phase 2 data clean-up closed: F1 dedup view, F12 gate restated, cards (Devin: "fix everything")
What: (a) readers of `lineup_snapshots` use `lineup_snapshots_dedup` / `nba.lineups.store.dedup_lineup_snapshots(con)` (Confirmed over Expected, then
latest `source_ts`; raw rows stay append-only). (b) Possession reconciliation hard gate is points == box points per team-game; the
`FGA + 0.44 FTA + TOV - OREB` formula is a reported diagnostic (ADR 0002; audit question 3, safe default). (c) Side tables with minutes-dependent
row existence are joined through `join_side_table` (absent = NULL + flag); data cards in `docs/DATA_CARDS.md`. Results: audit section 10.
Why: 240 duplicate snapshot groups made the T-30 reader skip 4 of 6 decisions; the formula gate was unmet in every season while points reconciled
100%; the hustle row-existence shape is the leak pattern that contaminated three experiments.
What would reverse it: official team box totals making the formula an identity (re-tighten it); a feed change that makes Expected rows later
than Confirmed ones meaningful (re-rank the dedup winner).

**2026-10-11 ~21:30 CT — Lineup dedup rule frozen (Devin: "yes"); possession fixture bound restored (Devin: "yes").**
What: (1) Per (snapshot, game, team): the team's status is Confirmed if any of its rows is Confirmed, else Expected. A player is a starter
only if a row carrying that winning status has `announced_starter` TRUE (OR over winning-status rows only; never across statuses, never
"latest row wins"). One shared implementation in `nba/lineups/store.py`, used by both `nba/daily/t30.py` and the view `lineup_snapshots_dedup`.
The T-30 gate is unchanged: both teams Confirmed with exactly 5 starters each, else skip. (2) The possession-formula fixture bound returns from
2.5 to 1.0 (clean fixture MAE 0.34); real data keeps points reconciliation as the hard gate and gains a band on the signed formula bias.
Why: adversary review `docs/reviews/phase2_cleanup_review_2026-10-11.md`: the feed marks starter blocks Expected or Confirmed per team (MIA
Confirmed and MIN Expected in one snapshot of 0012600002), so an Expected five is unconfirmed. The view ORed starters across statuses (87 of
87 Confirmed-not-5 team-snapshots promoted, 6 genuine); t30's latest-row rank dropped 6 of 527 Confirmed fives (false skip of 0012600041).
At 2.5 a planted +2 or +3 possessions per team-game passed the fixture gate. Expected effect on the 9 skipped preseason decisions: 0041 logged,
8 still skipped. Fixed before any live T-30 result under the new rule exists.
What would reverse it: evidence that the feed's Expected starter rows after a Confirmed roster are re-publications of a confirmed five
(e.g. lineups later matching at a rate indistinguishable from Confirmed fives over >= 50 team-games); for (2), official team possession
counts that show the 0.44 formula is biased on clean data by more than 1.

## 2026-10-11 DRAFT - F5 coach source: Basketball-Reference team pages, no new dependency; facts switch to team_coach_games
What: head coach by game comes from Basketball-Reference team pages (`/teams/<ABBR>/<YEAR>.html`, "Coach: A (W-L), B (W-L)" in tenure order), approved
by Devin 2026-10-11 after he reviewed the 14 `team_coaches` disagreements, for all seasons 2022-2025 (2025 = 2025-26). Fetched with `httpx` (already
locked) and parsed with the standard library (`nba/data/coaches_br.py`); no package added: `basketball_reference_web_scraper` and
`basketball-reference-scraper` expose players, schedules and standings, not the per-team-season coach line with W-L. Reference CSV committed at
`nba/data/head_coach_games_reference.csv` (data/ is gitignored); table `team_coach_games(game_id, team_id, coach_name, coach_id, source, known_at)`
replaces the earlier 2022-24 draft (hand-copied, 2025 unverified; its CSV is kept as `head_coach_reference_handcopied_2026-10-10.csv` for the agreement
gate). `nba/facts/build.py` `coach_facts` now reads `team_coach_games` (per stint, known_at = tip of the first game he ran, confidence 0.9) and keeps
the old 1-Oct `team_coaches` fact only for seasons the new table does not cover (2026 live season).
Why: `team_coaches` is the last occupant of the job (14 of 87 wrong, 11 next-season hires, no mid-season changes); a stamp of 1 October made future
facts look known. The first game of a stint is therefore not known before its tip in the facts store (conservative; the real hire date is not sourced).
What would reverse it: a dated hiring source (press release dates) that makes known_at earlier and verifiable; or BR page layout/terms changes that
stop the fetch (the cached raw HTML stays the record).

**2026-10-11 ~23:30 CT — Decision scoring is the standard decision metric (Devin: "yes to all").**
What: every pre-registration that makes a betting-relevant claim declares, next to its accuracy metric, the decision score in
`docs/DECISION_SCORING.md` (`nba/markets/decision_scoring.py`): legs selected by model EV after the actual price at thresholds 0/2/5/10%,
one unit per bet, ROI with a game-clustered CI, against the market-lean and random comparators of the same count.
Why: CRPS scores every forecast, but a bettor acts only on the confident legs; descriptive run 2023-24 (3f5363a): model ROI -1.2% to +0.4%
across thresholds, above market-lean (-3.9% to -5.1%) and random (about -6%), not above zero; model over-confident above 0.60 (said 66.7%,
hit 57.0%). Two follow-ups approved the same hour: a rebounds rule scored on the live 2026-27 log only, and a market-shrinkage calibration rule.
What would reverse it: a stake or fee convention a rule needs that this metric cannot express (then the rule states its own, in advance).

**2026-10-11 ~23:45 CT — Preseason (001) and All-Star (003) games are skipped by the daily path (Devin: "can you not do stuff on the preseason games").**
What: `nba/daily/schedule.py::slate_for_date` (the single slate builder for the daily run, T-30, the lineups tick and replay) drops 001/003 game ids;
the parlay shadow (`nba/parlay/slate.py`) and LLM shadow (`nba/shadow_llm/run.py`) skip stored 001/003 rows. Jobs still start and heartbeat; a
preseason-only date prints "no regular-season games: N preseason skipped" and exits 0. Existing 136 forward_predictions and 10 forward_t30_decisions
preseason rows are kept, marked unscorable rehearsal rows in DAILY_PIPELINE.md. Replay oracle 89889f6d IDENTICAL (20 dates, 46,658 predictions).
Why: `games` never holds 001/003, so forward rows on them can never settle; they cluttered logs and the T-30 review.
What would reverse it: a need to rehearse T-30 or lineup feeds on preseason slates (drop the prefix test in `slate_for_date`).

**2026-10-11 ~22:45 CT — HISTORY_WINDOW_3 FROZEN (sha256 32e05aec, commit c7d16ae).**
What: props training adds 2019-20 and 2020-21 as rows with the injury report missing, 2021-22 excluded; arms A1, A2 (history weight 0.5),
knockouts K0/K1; select 2023, report 2024; pass means shadow logging only. Gate passed after the free `fetch-static` pull (243 of 244 history
profiles) and a pre-freeze amendment of G1b to played players only (the one profile-less id never played).
Why: Devin reopened the direction ("okay, go for it") per the 15:14 entry; confirmations "yes yes" (knockout -0.0025, A2 weight 0.5, W0 3.187553).
What would reverse it: nothing above the results marker changes; a null closes the history direction for props this season.
