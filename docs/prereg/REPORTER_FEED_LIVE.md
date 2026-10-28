# REPORTER_FEED_LIVE: do beat-reporter facts seen by T-60, and not carried by the official report at T-60, improve on production? (pre-registration)

Status: **DRAFT (modeler, Tue 2026-10-27 about 19:05 CDT by `date`). Not frozen.** A live 2026-27 rule only, with no history verdict. Season 2025 is never loaded, and no 2025 touch is ever earned.

Two-stage freeze (precedents: INGAME_WINPROB_V2 stage 1; SHARP_WALLETS_PERSIST two-step gate):
* **Stage A**, before the first logged night: everything above `=== RESULTS BELOW ===` is committed as `prereg(REPORTER_FEED_LIVE): <sha256>` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/REPORTER_FEED_LIVE.md | shasum -a 256`), with the allow-list, parser and label-fixture shas filled in below. Every parameter, arm, floor and threshold is fixed here.
* **Stage B**, label-free, after 14 logged nights and parser acceptance: the count script decides mechanically which tests carry a pass ("Gate"). Its JSON is committed as `prereg(REPORTER_FEED_LIVE stage B): <sha256>` before any reporter fact is joined to any outcome.

Origin: DECISIONS 2026-10-27 18:41 (e). Note: docs/research/reporter_feed_2026-10-27.md (rates, power, sources, parser spec). Model class: information source feeding a fixed adjustment of production; nothing is fitted.

## The rule in eight lines
1. **Population:** regular-season (002) and play-in (005) games of season 2026 from the first logged night. Per (game, player): a production `props_context_residual` pts row made at or before real tip - 60, on a team-game covered at T-60 (an ok poll of its eligible handles in [tip - 90, tip - 60]).
2. **Treatment information:** `reporter_minutes_cap` / `reporter_status` facts from the frozen allow-list and frozen parser, with `first_seen_at <= tip - 60`, not hedged, and not already carried by the official report used at T-60.
3. **M1, cap mechanism:** D = mean(y_min - m_B) on played cap rows, where m_B = production's minutes mean minus 3.3 for a box-score returner, else production's mean. Pass: D <= -2.0 min, 95% CI upper < 0, BH.
4. **S1, status:** ΔLL = mean[LL(p_T) - LL(p_B)] on qualifying status rows, DNP included. p_B = the T-60 report tag's 2022-24 rate (else production p_play); p_T = the reporter claim's fixed rate. Pass: ΔLL <= -0.15 nats, CI upper < 0, BH.
5. **C1 and C2, the question as asked:** minutes CRPS and integer pts CRPS on numbered-cap rows, with treatment = production reshaped by the cap. Reported at every look. Pass-carrying only if Stage B projects 282 / 495 rows; otherwise descriptive and pooled with 2027-28 under a new id.
6. **CIs:** percentile bootstrap, B 2,000, seed 20262027, clustered by ET date and by player-season; the wider governs. BH at q 0.05 over the pass-carrying tests.
7. **Looks:** weekly label-free status; Stage B after 14 logged nights; final for each pass-carrying test at the first Sunday its n is met, else Sunday 2027-04-18 (UNDERPOWERED, not a null).
8. **What a pass means:** a recommendation to red-team and to Devin (wire the fact into the props shadow); never a bet, never a production change by itself. A null closes the arm; one follow-up at most.

## Hypothesis
Beat reporters publish two kinds of fact before the official report carries them: (a) minutes caps, which the report never lists; (b) availability decisions, which reach the report only later. Production's minutes path reads no report, and production props read only OUT, so both are information production lacks at T-60. Size evidence: production over-forecasts returners by 2.8-3.8 min (2023-24), and the report changes after its T-60 list about 0.8 times per game for rotation players (note section 7).

## Definitions
* **Tip:** real tip (`nba.features.game_tipoff`, `tip_source = "real"`). T-60 = tip - 60 min, inclusive.
* **Production row:** the `forward_predictions` row of `props_context_residual`, target pts, with the latest `made_at <= tip - 60`; that run's `forward_prop_grid199` q199; JSON `p_play` and `proj_minutes`. A player with no such row is outside the population (counted).
* **Production minutes P:** TN(mu, sigma) on [0, M] from the same run (Dependency 2). E_P is its mean.
* **Report at T-60:** the latest official report (header clock) with known-at <= tip - 60, which is production's own rule.
* **Returner (for m_B):** the team played >= 3 straight regular-season games before g without him (no minutes > 0), and his prior-10 played average (>= 5 games) is >= 15 min, from box scores strictly before g (`returners` in research/scratch/reporter_feed_rates.py). The constant 3.3 is the pooled 2023-24 mean of (P mean - y) on 696 such rows: -3.80 (n 310) and -2.84 (n 386).
* **Qualifying cap fact:** a `reporter_minutes_cap` fact with first_seen <= T-60, hedged false, for a player not OUT or Doubtful on the T-60 report. The latest such fact per player-game wins. A later `cap_lifted` or `reporter_status = out` (first_seen <= T-60) cancels it. N = value (upper end of a range); the fact is "numbered" if N is not NULL.
* **Qualifying status fact:** a `reporter_status` fact in {out, will_play, available, game_time_decision}, first_seen <= T-60, hedged false. The latest per player wins; on ties, out > game_time_decision > will_play/available.
  - p_T: out 0.03; will_play or available 0.95; game_time_decision 0.514.
  - p_B from the report tag at T-60: Doubtful 0.024, Questionable 0.514, Probable 0.928 (docs/research/questionable_status_scope_2026-10-15.md, 2022-24). Available or not listed: production JSON p_play. OUT on the report: not a row.
  - The row qualifies only if |p_T - p_B| >= 0.10. Probabilities are clipped to [0.01, 0.99].
* **Outcomes:** y_min = box minutes (decimal); played = minutes > 0. A rostered player with no box row, or listed inactive, has not played.

## Tests
### Family A (decision; members set by Stage B; BH over the pass-carrying members, q 0.05)
* **M1 cap mechanism.** Rows: production rows with a qualifying cap fact (numbered or not) whose player played. D = mean(y_min - m_B), with m_B = E_P - 3.3 for returners, E_P otherwise. Pass: D <= -2.0, CI upper < 0, and BH-adjusted p < 0.05 (two-sided bootstrap p = 2 x min(share of draws >= 0, share <= 0)).
* **S1 status.** Rows: production rows with a qualifying status fact, played or not. ΔLL = mean[-log p_T(y) + log p_B(y)]. Pass: ΔLL <= -0.15, CI upper < 0, BH.
* **C1** joins Family A if Stage B projects >= 282 numbered-cap rows; **C2** if >= 495.

### Family B (reported; descriptive in 2026-27 unless moved to A)
* **C1 minutes CRPS.** Numbered-cap played rows. T_min = 0.8 x TN(mu, sigma; [0, min(N + 2, M)]) + 0.2 x P. CRPS by numerical integration on a 0.05-min grid over [0, max(M, y)]. Δ = CRPS(T_min) - CRPS(P). Floor -0.50 min.
* **C1r, returner-explained check.** As C1, but with P replaced in both arms by B_ret = TN(mu - 3.3 for returners, sigma). If C1 passes and C1r's CI covers 0, the word is PASS_RETURNER_EXPLAINED (build the free returner rule instead).
* **C2 pts CRPS.** Numbered-cap played rows. r = clip(E_Tmin / E_P, 0.5, 1.0); treatment grid = `ceil(r x q199 - 0.5)`; production grid = `ceil(q199 - 0.5)`; integer CRPS (RPS) of both against y_pts. Floor -0.15.
* **S2 teammates (mechanism only).** Rotation teammates (prior-10 >= 15) of a player with a qualifying `out` fact who did not play: mean(y_pts - m_int), minus the same over all other played rows of covered games, where m_int is production's integer median. Date-clustered CI. No counterfactual arm in 2026-27.
* **Descriptive D1-D7:**
  - D1 lead times (tip - first_seen; first_seen - created_at);
  - D2 claim accuracy (cap honoured means y <= N + 2; out means not played; will_play means played);
  - D3 share of facts later confirmed by the report;
  - D4 hedged facts;
  - D5 facts first seen in (T-60, T-15], i.e. the feed's T-15 value;
  - D6 unnumbered caps;
  - D7 top-5 handle share of qualifying facts, and cost per qualifying fact.

### Slices (no pass role)
- Cap: returner vs not; N <= 20 vs > 20; multi_game; starter10 >= 0.5.
- Status: out vs will_play/available vs game_time_decision; report tag at T-60 (Q / D / P / not listed).
- All: home vs away; tip hour ET; first vs second half of the look.

## Gate (non-negotiable 8)
* **Data audit (BEST_PRACTICES 2026-10-12).** No free archive exists (10-21 note: RotoWire litigation, ESPN terms, Wayback unreachable, CBS minute stamps only). Paid: X full-archive search and counts are on pay-per-use (docs.x.com, 2026-10-27). A 2023-25 count (Stage 0, about $0.24) is the cheapest repair of the rate inputs; a 2023-25 retrospective test ($15-100) is possible as a separate id. Live capture since opening night: none (45 preseason posts only).
* **Stage A projection** (note sections 7-8; 2023-24 rates; λ and f unmeasured): about 990 logged games to 2027-04-11 (7.5 games a night, logged share 0.85). Rows needed:
  - M1: 76 (sd 7.25 per row, half-width 2.0, design effect 1.5);
  - S1: 131 (sd 0.8 assumed, half-width 0.15, deff 1.2);
  - C1: 282 (simulated sd 3.50, half-width 0.5, deff 1.5);
  - C2: 495 (simulated sd 1.39, half-width 0.15, deff 1.5).

  Projected rows: M1 50-150 (central about 100); S1 180-440; numbered caps 25-75. **At central inputs M1 and S1 can carry a pass in one season; C1 and C2 cannot** (they need numbered-cap rows at about 0.29 and 0.50 a game).
* **Stage B (label-free; the deciding gate).** Runs after the 14th logged night and after parser acceptance (note 5.3: precision >= 0.95, recap false-positive rate <= 0.02).
  - The count script reads only `reporter_facts.duckdb`, `poll_log` and `forward_predictions`, and asserts that no box-score, `forward_scores` or outcome table is opened.
  - Per logged covered game it counts qualifying cap rows, numbered-cap rows, qualifying status rows and coverage.
  - Projection: n_proj = rows so far + measured rate x measured logged games a night x remaining game nights to 2027-04-11.
  - A test carries a pass iff n_proj >= 76 (M1), 131 (S1), 282 (C1), 495 (C2).
  - Stage 0 counts, if run, are reported beside the live rate as an upper-bound check and are not used in the projection.
  - If no test carries, the rule is a descriptive log for 2026-27, and the CRPS question moves to a pooled id.
* **Coverage gate at every look:** covered team-games / scheduled team-games >= 0.70 (Wilson lower bound), else INCONCLUSIVE.

## Words
PASS; NULL (point above the floor or CI covers 0; the record states the smallest effect the CI excludes); HARMS (CI lower > 0); UNDERPOWERED (final with n below its requirement); INCONCLUSIVE (a kill); PASS_RETURNER_EXPLAINED (C1 only).

## What a null means
* **M1 NULL:** cap claims from this list, as parsed, carry no minutes shortfall beyond a free returner rule at T-60. Cap wiring closes, caps stay a briefing item, and C1/C2 get no second season.
* **S1 NULL:** our reporters do not get ahead of the T-60 report usefully. One follow-up at most: the T-15 version, and only if D5 shows most status facts arrive in (T-60, T-15].
* Neither null is evidence about other reporters, about T-30/T-15 use, or about how caps affect minutes in general; power and coverage bound what this rule can say.

## Kill criteria
- (a) Coverage below 0.70.
- (b) Integrity: any qualifying fact with first_seen > tip - 60, any `capture_mode = backfill` fact, or a parser or allow-list sha differing from Stage A. Repaired only by a result-blind rule, with affected nights excluded.
- (c) Blind parser audit (every 4 weeks, 50 random qualifying facts, text only) with precision below 0.90.
- (d) Budget refusals on more than 3 nights in a 4-week window: flagged as a coverage problem.
- (e) Production rows missing for more than 20% of qualifying facts.

A production model change during the look is not a kill: the comparator is whatever production logged each night, and results are reported by production version.

## Multiplicity, holdout and population
Family A is set by Stage B, BH at q 0.05. Family B gets labels only (BH over m = 3: C1, C2, S2). Season 2025 is never loaded; Stage 0 counts cover the 2023-24 and 2024-25 regular seasons only. Cup group games (002) and the play-in (005) count; the Cup final (006) and playoffs (004) do not.

## Why these choices (what each protects against)
* **first_seen_at from our fetch:** the only clock we control. A post we had not read was not known to us (late polls, deletions, reposts).
* **T-60, with production's run at or before it:** both arms share one information set except the reporter fact.
* **Report-aware baseline for S1, returner-aware baseline for M1:** a reporter must beat what is free, not a model that ignores both (BEST_PRACTICES 2026-10-22).
* **Mixture w 0.8, slack +2, N at the upper end, all fixed now:** a broken cap is not given zero support; caps are approximate; no tuning after data.
* **Ratio band [0.5, 1.0] (lineage COACH_PHASE):** a cap only lowers minutes, and the band stops a bad N from collapsing the grid.
* **Player-season clustering:** a ramp-up repeats one claim over consecutive games.
* **Label-free Stage B:** the rates that power depends on are unknowable before logging; counting facts reads no outcome.
* **Hedged facts kept out of Family A:** precision over recall.
* **Floors:**
  - M1 -2.0 min: about two thirds of the returner shortfall; smaller would not change a prop view.
  - S1 -0.15 nats per row: less than about a 14% likelihood ratio per flagged player is not worth wiring.
  - C1 -0.5 min: about 11% of production's returner CRPS of 4.4-4.7.
  - C2 -0.15 pts: about 5% of the row CRPS of 3.16.

## Dependencies (this rule changes no nba/daily file; owners in brackets)
1. **Capture fixes** [nba/ingest owner]: lookback anchored at 06:00 ET; pagination with no state advance on a short page; posts_per_day 600; `poll_log`; facts into `data/reporters/reporter_facts.duckdb` with known_at = first_seen_at; parser filters F1-F8 and the predicates `reporter_minutes_cap` / `reporter_status`; the labelled fixture.
2. **`forward_minutes_dist` side table** [steward]: run_id, made_at, game_id, tipoff, player_id, mu, sigma, max_minutes, p_play, version. Written by the pretip run and not read by `nba.daily.canonical_dump`; the replay oracle must be IDENTICAL. For nights before it exists, an offline reconstruction with `predict_minutes` must match the logged `proj_minutes` within 1e-6 on >= 98% of rows, or those nights are excluded from M1/C1/C2 (counted).
3. **launchd job `local.nba.reporters`** [steward]: T-120, T-70 and T-35 per team, a 13:30 ET sweep, a heartbeat, and a watchdog alert if a slate team has no ok poll in [T-90, T-60] by T-55.
4. **Scorer and Stage B count script** [modeler]: `nba/eval/reporter_feed_live.py`, fixture-tested with planted-future facts, read-only.
5. **At Stage A:** `reporter_facts.duckdb` confirmed empty; allow-list, parser and fixture shas recorded below.

## Unstated details chosen before scoring
- The T-60 comparison is <=, not <.
- y_min > M is scored as is; the CRPS grid extends to y. The grid step is 0.05 min.
- A player resolved to a roster but absent from both the game's box and its inactive list counts as not played.
- Caps outside [6, 40] are dropped by the parser.
- League-wide handles resolve names through the reviewed alias table only.
- Ties in first_seen are broken by post_id.
- A team-game is covered only if every eligible handle's poll was ok; a team with only league-wide coverage counts as covered when the league-wide poll was ok.
- LL in nats; B 2,000; seed 20262027; percentile CIs.

## Frozen identifiers (filled at Stage A)
allow-list sha256: <configs/reporters.yaml at the Stage A commit> | parser sha256: <nba/ingest/reporter_parse.py with filters F1-F8> | label fixture sha256: <tests/fixtures/reporters/pretip_labels_v1.jsonl>

## Priors (PREDICTIONS_LEDGER row drafted in the modeler hand-back; appended before Stage A)
P(M1 carrying) 0.45, P(M1 PASS) 0.30, D about -3.5 min (80% -7.0 to -0.5); P(S1 carrying) 0.75, P(S1 PASS) 0.60, ΔLL about -0.30 (80% -0.60 to -0.05); P(C1 carrying) 0.08, C1 about -1.0 min (80% -2.2 to +0.2), C2 about -0.10 (80% -0.30 to +0.06); P(any kill) 0.20.

=== RESULTS BELOW ===

(not run)

---

```
status: partial
rule: REPORTER_FEED_LIVE (DRAFT, not on disk; sha to be computed by the main session after writing DOC 2) | n/a
gate: [{check: "Stage A projection, M1 rows needed 76 vs projected 50-150 (central ~100), 2023-24 rates", before: "unmeasured", after: "carries at central/high lambda", pass: "projected yes (central)"}, {check: "S1 rows needed 131 vs projected 180-440", before: "unmeasured", after: "carries", pass: "projected yes"}, {check: "C1 numbered-cap rows needed 282 vs projected 25-75", before: "unmeasured", after: "cannot carry in 2026-27", pass: false}, {check: "C2 rows needed 495 vs projected 25-75", before: "unmeasured", after: "cannot carry", pass: false}, {check: "Stage B label-free count on live data", before: "no live facts", after: "pending (~2026-11-13)", pass: "unknown"}]
changed_files: [research/scratch/reporter_feed_rates.py, reports/reporter_feed/rates_2023_24.json]   # to be CREATED by the main session from DOC 1/DOC 2: docs/research/reporter_feed_2026-10-27.md, docs/prereg/REPORTER_FEED_LIVE.md
backups: none
drafted_rows: {ledger: "T5xx REPORTER_FEED_LIVE DRAFTED row (optional, at Stage A), text in report", decisions: "2026-10-27 19:1x REPORTER_FEED_LIVE drafted row, text in report", changelog: none, holdout_log: none}
numbers: [{name: "rotation report changes after the T-60 list, per game", value: "0.774 (2023) / 0.837 (2024)", n: "1,316 / 1,306 games", ci: "[0.712, 0.837] / [0.772, 0.907] game-clustered", family: "descriptive, no family"}, {name: "rotation Q/D->OUT after T-60 list, per game", value: "0.248 / 0.315", n: "1,316 / 1,306 games", ci: "[0.217, 0.281] / [0.277, 0.354]", family: "descriptive"}, {name: "rotation returners K>=3 per game", value: "0.252 / 0.315", n: "310 / 387 rows over 1,230 games each", ci: "[0.224, 0.283] / [0.285, 0.346]", family: "descriptive"}, {name: "production minutes error on K>=3 returners, mean(y - P mean)", value: "-3.80 / -2.84 min", n: "310 / 386 rows", ci: "[-4.66, -2.93] / [-3.57, -2.14] game-clustered", family: "descriptive"}, {name: "simulated cap effect (NOT a result; N simulated from y), mixture w0.8 h0.8", value: "-2.13 min CRPS (sd 3.50/row); pts -0.25 (sd 1.39)", n: "696 rows x 5 reps", ci: "n/a (simulation)", family: "power only"}]
verification: {ruff: 0, format: 0, mypy: 0, pytest: "94 passed, exit 0 (--no-cov; with coverage on: 94 passed, exit 1 from the 70% coverage threshold on a 4-file subset)"}
unstated_details: [mixture weight w=0.8 and slack +2 fixed before any live data, returner constant -3.3 min (pooled 2023-24, 696 rows), status mapping out 0.03 / will_play 0.95 / gtd 0.514 and baseline Q 0.514 / D 0.024 / P 0.928, qualify threshold |p_T - p_B| >= 0.10, floors M1 -2.0 min / S1 -0.15 nats / C1 -0.5 min / C2 -0.15 pts, design effects 1.5 (caps) and 1.2 (status), S1 per-row sd 0.8 assumed, numbered caps = half of caps assumed, lambda 0.05/0.10/0.15 and f 0.2/0.35/0.5 scenarios, 7.5 games a night and logged share 0.85, ratio band [0.5, 1.0], seed 20262027, B 2000, wider of date and player-season clustering governs]
cost: {requests: 11 (10 WebFetch of docs/terms, 1 WebSearch; 0 X or Bluesky API calls), credits: 0, wall_minutes: 30, ceiling: "no paid calls made; recommended X ceiling $40/month"}
confidence: 0.6 because the two rates that set every power number (cap rows per game, status front-run share) are guesses until Stage 0 or the 14-night count measures them
unknowns: [in-season posts per handle per day (cost), whether exclude=replies drops self-threads, Bluesky coverage of the 28 reporters, X edit/delete behaviour on timelines, whether 2023-24 reports exist at 15-min resolution after the T-60 list, whether derived facts from a later-deleted post are "X Content" under the policy]
needs_devin: [C1 platforms (default X + free Bluesky probe), C2 cost ceiling $40/month and posts_per_day 600 (default yes), C3 allow-list 28 as is (default), C4 Stage 0 counts ~$0.24 (default yes), C5 X deletion/edit compliance handling (terms call), C6 Mac-asleep mitigation pmset+caffeinate (default), C7 forward_minutes_dist side table (default), C8 park the paid retrospective test (default), C9 pre-Stage-B nights count (default yes), read and confirm DOC 2 before the Stage A commit]
```