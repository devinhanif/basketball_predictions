# Reading log: the live beat-reporter feed (minutes caps and status before the official report), 2026-10-27

DRAFT research note (modeler, written Tue 2026-10-27 18:57 CDT by `date`). Reading-log intake plus design; no fit, no outcome of any reporter test exists. Draft rule: `docs/prereg/REPORTER_FEED_LIVE.md` (DRAFT, not frozen).
**Advice or momentum:** *follows advice*. Claude recommended a live-only reporter log on 2026-10-21 (docs/research/free_sources_salary_reporters_2026-10-21.md, "Recommendation, Question 2"); Devin's go is DECISIONS 2026-10-27 18:41, item (e). Model class: **information source feeding a fixed, parameter-light adjustment of production** (no new model is fitted); playbook check in section 10.

Verdict: **PURSUE as live logging now; the pass-carrying test is smaller than the question asked.** At plausible event rates, one live season can decide whether reporter status claims beat the official report at T-60, and probably whether cap claims carry a minutes shortfall. One season cannot decide whether caps improve minutes or points CRPS (sections 7-8). One cheap paid check, under $1 and Devin's purchase, would replace guessed rates with measured ones before anything freezes (section 3.4).

## 1. The claim, restated
Target: a player's minutes tonight and his points (and, for status, whether he plays). Information: posts by allow-listed beat reporters before the official report carries the same fact: minutes limits ("on a 20-minute restriction"), "will play" / "out tonight", shootaround participation, game-time decisions, G League recalls. Model: none. A claim seen at or before T-60 adjusts production's own forecast by a rule frozen in advance. Metric asked: minutes CRPS and pts CRPS against production on the live log. Number: none yet; no archive is free, so the first observation is the first night of logging.

Credibility of the premise:
* The information is real and pre-tip. Coaches give availability at pre-game media sessions, usually 75-100 minutes before tip, and at shootaround in the morning. The 2023-24 data (section 7) shows the official report keeps changing after its T-60 list.
* No split, leakage or accuracy issue yet, because nothing has been scored. The known leak shape is text that describes the past and reads like a pre-tip claim (section 5.1).
* Prior evidence here: every win so far came from pre-tip information (official report; T-30 lineups). Every architecture-only idea lost or tied (TEST_LEDGER: sim props, RAPM, set transformer, pbp GPT, tracking rates, stacks). This idea adds information, not capacity. INJURY_TIMING (docs/research/injury_timing_measurement_2026-10-15.md, rev 3) is the nearest ancestor. It found a fresher official list matters at most 0.001-0.014 pts CRPS on all rows, with a prior of about 0.07 of reaching the -0.005 floor on all rows. A reporter feed is the same lever arriving earlier and covering fewer players, so it is scored on the rows it touches, not on all rows.

## 2. What already exists (read from code and data on 2026-10-27)
| piece | state | consequence |
|---|---|---|
| `nba/ingest/reporters.py` (X API v2 client, allow-list, budget, write-once raw parquet), `reporter_parse.py` (regex, no LLM), `reporter_facts.py`, docs/REPORTER_FEED.md | built and tested; not on the daily path | the feed needs fixing and scheduling, not building |
| `configs/reporters.yaml` | 28 handles (27 teams plus Shams Charania), approved by Devin 2026-10-15, last change e6f6b79 (file sha256 `e11918f8…`). No handle for CHA, MIL, POR | the frozen allow-list candidate (section 4) |
| `X_BEARER_TOKEN` | present in `.env` (value not read) | no sign-up needed |
| Captures | 45 post reads (32 unique) on 2026-10-15/16, preseason; nothing since opening night | logging has never run in season |
| Spend so far | 45 posts x $0.005 + 59 user reads x $0.010, about $0.82 (`data/reporters/budget.json`) | |
| Facts `known_at` | the post's `created_at` (X server time), not our fetch time | must change to our `first_seen_at` (section 6) |
| Facts store | written into `data/facts/facts.duckdb`, which `nba.facts build` drops and recreates | reporter facts would be erased by a rebuild; they need their own append-only DB |
| Poll window | `start = max(now - 6 h, last_created_at)`, one page per handle | two capture holes. (a) A shootaround post more than 6 h before the first poll is never read. On 10-16 an ORL shootaround post created 15:41Z was first seen 21:30Z, 5 h 49 min later, inside the lookback only barely. (b) A page cut short by the remaining budget still advances `last_created_at`, so older posts in the gap are skipped for good |
| Planned cadence | T-90 and T-40 per team | posts between T-90 and T-60 are first seen after T-60 |
| Production minutes path | `nba.props.minutes.predict_minutes`: hurdle model, minutes given play ~ Normal(mu, sigma) truncated to [0, max]; prior box scores only; does not read the injury report. Live JSON logs `proj_minutes` (= mu) and `p_play`, not sigma; no minutes target in `forward_predictions` | a minutes CRPS needs sigma logged (side table) or a verified reconstruction |

## 3. Source plan
### 3.1 X (primary; already approved and funded)
* **Plan needed: X API pay-per-use**, the default for new developers. Third-party pages say the old $200-a-month Basic plan is closed to new sign-ups and was migrated to pay-per-use, and Pro is being retired; X's own page does not say. Credits are bought and managed at **https://console.x.com** (the Developer Console named on X's pricing page; developer.x.com/#pricing shows the same table).
* Rates, read on **https://docs.x.com/x-api/getting-started/pricing** on 2026-10-27 (the page has no date): **Posts: Read $0.005 per post returned; User: Read $0.010; Counts: All $0.010 per request; Counts: Recent $0.005 per request.** Pay-per-use is capped at 3 million post reads per billing cycle. Repeat reads of the same post within one UTC day are "deduplicated" (a "soft guarantee"). A spending limit can be set per cycle, after which requests are blocked. A $20 credit is offered for saving a card and making a first purchase.
* Endpoints on pay-per-use (docs.x.com, 2026-10-27): user timelines (in use); **full-archive search `/2/tweets/search/all` ("Pay-per-use, Enterprise", up to 500 posts per request)**; **full-archive counts `/2/tweets/counts/all`** (minute, hour or day granularity, 31 days per page, 1,024-character query). The 10-21 note could not find the full-archive price; it is the ordinary per-post rate.
* **Terms that bind** (docs.x.com/developer-terms/policy, read 2026-10-27):
  - "If you store X Content offline, you must keep it up to date with the current state of that content on X."
  - You must "delete or modify any content you have if it is deleted or modified on X … as soon as reasonably possible, or within 24 hours after receiving a request to do so by X."
  - Only post, DM and user IDs may be distributed.

  This conflicts with write-once raw storage and with pushing post text to GitHub. Proposed handling (Devin's call, C5): raw text stays in `data/` and is never committed or pasted into repo docs (this note paraphrases). Fact-bearing posts are re-read at T+24 h. A deleted or edited post has its stored text purged or replaced, leaving a tombstone (post_id, detected_at, sha256 of the first-seen text). The parsed fact keeps its `first_seen_at`, because it records what was knowable at the time. No training clause was found on that page.
* Cost, estimated (not measured in season): in preseason the 6 h lookback returned about 3-7 posts per handle per poll (10-16). With the lookback anchored at 06:00 ET I assume 10-20 posts per covered handle per game day. On an 8-game night: 16 teams x 0.9 coverage x 15 + Shams 15 = **about 230 posts, $1.15 a night, about $30 a month and about $150-180 for the rest of the regular season.** That fits the existing guards ($40 and 8,000 posts a month) except **`posts_per_day` 400, which binds on a 13-15 game night** (27 handles x 15 = 405). Raise it to 600, or big nights lose coverage on whichever teams poll last (a selection effect).

### 3.2 Bluesky (free supplement; coverage unverified)
* Public AppView `public.api.bsky.app`, unauthenticated, `app.bsky.feed.getAuthorFeed`. Rate limits (bsky-docs source on GitHub, `advanced-guides/rate-limits.md`; the docs site renders client-side and returned nothing): "generous rate-limits. Please contact us if you encounter rate-limiting". No AppView number is given; the PDS limit is 3,000 requests per 5 min per IP. Developer guidelines (`support/developer-guidelines.md`): "All services must have a method for deleting content a user has requested to be deleted"; nothing on archiving, commercial use or automated reading. Bluesky's Terms of Service were not read.
* Coverage: unknown. The 10-21 note found no evidence our beat writers post there. The DECISIONS 2026-10-10 condition (restriction notes appearing within two weeks) was never tested, because Devin chose X on 10-11.
* Plan (free, one-off, Devin runs it; agents cannot curl): run `app.bsky.actor.searchActors` for each of the 28 names and record any handle whose bio names the beat. If at least 5 of 28 are active, add them as second sources for the same person (`source = bsky:<handle>`; `first_seen_at` = the earliest over that person's platforms); otherwise drop Bluesky.

### 3.3 Team official accounts (default: off)
Team accounts post heavily on game days. Their injury notes mostly repeat the report, and starters come from the lineup feed. Adding 30 would roughly double posts (about +$1.50 a night) and break the $40 cap. If wanted later, add them as a slice under a new id.

### 3.4 The paid check worth doing before freezing (Stage 0, label-free, Devin's purchase)
A **full-archive count** (counts only, no post text, no outcome) of allow-list posts matching restriction phrases in the 2023-24 and 2024-25 regular seasons. Season 2025-26 is never queried. It gives a measured historical rate of restriction posts per team-game, an upper bound on the cap rate λ that sections 7-8 can only guess. A second query does the same for status phrases. One query ORs all 28 `from:` handles (about 560 characters plus keywords, under 1,024). Day granularity, 31 days per page: about 6 pages x 2 seasons x 2 queries = **24 requests, about $0.24**; per-handle counts cost 336 requests, about $3.36. Caveats: 2023-25 beat assignments differ from today's; deleted posts are missing (survivorship); keyword counts include recaps and "no restriction" (an upper bound). Sizing input only.

The same endpoint family (full-archive search, $0.005 per post) would also make a retrospective cap test on 2023-24/2024-25 affordable (a rough guess: 3,000-20,000 posts, $15-100). That would be a different rule, with known-at = `created_at` and the survivorship bias. I flag it because the premise "no retrospective archive" holds for free sources only.

Who builds it: a `counts` subcommand on `nba.ingest.reporters` (the nba/ingest owner), with the same budget guard, `--dry-run` printing the request count and cost, and output to `data/raw/x_counts_<date>/`. Devin runs it. No command is given here because the code does not exist yet, and a raw curl would skip the budget guard.

## 4. The allow-list, frozen before logging starts
* Candidate: `configs/reporters.yaml` at the Stage A commit (today 28 entries; sha256 `e11918f8…` at e6f6b79). The rule records the file's sha256. A handle added later is outside this rule's population, though it may be logged and shown in the briefing.
* Fixed per entry: name, handle, team (tricode or null), platform. No handle is removed for performance (intent-to-treat). A reporter who changes beat or goes quiet stays on the list. A team-game counts as covered only on nights with an ok poll of its handles in [T-90, T-60] (measured, not assumed).
* Open choice C3 (size): 28 as is (default); or add CHA, MIL and POR writers if Devin finds current ones before Stage A (about +$0.10 a night; the 10-15 check found no current CHA or POR beat writer and an unconfirmed MIL one); or add Bluesky mirrors (3.2).

## 5. Parser spec: future-tense and pre-tip filters, and the labelled test set
### 5.1 What the current parser does (measured 2026-10-27 on 14 sentences I wrote; no X content)
* **3 of 5 post-game recaps parse as `minutes_restriction` with N = minutes already played**: "was limited to 18 minutes in his return Tuesday" gives cap 18; "held to 15 minutes in Sunday's loss" gives 15; "limited to 12 minutes in the win, finishing with 6 points" gives 12. The 10-21 note saw the same shape in RotoWire headlines.
* A same-game recap posted inside the existing 3 h in-progress window attaches to tonight's game, but cannot pass `first_seen_at <= tip - 60`. **The live danger is the next-morning recap**: posted more than 3 h after the previous tip, it attaches to the *next* game as a pre-tip cap whose N is last game's minutes. That is a mislabel, and one correlated with tonight's minutes.
* Pre-tip sentences: 4 of 5 parse with the right type. A trailing number is lost ("will be on a minutes restriction tonight, around 20 minutes" gives N = None).
* "no longer on a minutes restriction" gives `unknown`, though a lift is real information. "could be on a restriction" gives no claim. "for the next few games" attaches to the next game only.

### 5.2 Spec
Implemented by the nba/ingest owner and frozen by sha256 with the rule; any change is a new parser version and a new rule id.

New predicates sit beside the existing `claimed_*` ones: `reporter_minutes_cap` (value = N or NULL) and `reporter_status` (value_text status in {out, will_play, available, game_time_decision, cap_lifted}). The briefing keeps using `claimed_*`; the rule reads only the new ones. A claim enters them only if it passes every filter:
1. **F1 tense.** Reject the sentence if it has a past marker: `was|were|played|logged|finished|went|scored|had|came off|in his return <weekday>|last night|yesterday|in the (win|loss)|in <weekday>'s|in Game \d|through \d quarters|\d+-\d+ (win|loss)`. Accept a cap only with a prospective marker in the same sentence: `will|is expected to|is set to|is slated to|remains on|is on|will be on|is going to|tonight|today|vs\.?|against|this evening`. The lexicon is part of the frozen sha. Precision over recall.
2. **F2 stat line.** Reject if the sentence has a box-score count `\d+\s*(points|pts|rebounds|boards|reb|assists|ast|steals|blocks|3s|threes)` or a shooting line `\d+-(of-)?\d+`.
3. **F3 day reference.** A named weekday or date must be tonight's ET game date; else reject.
4. **F4 time.** `created_at` > previous team game's tip + 3 h (else it belongs to that game). `created_at <= first_seen_at`; a violation is a clock fault, logged, and the row is dropped. The T-60 arm needs `first_seen_at <= real tip - 60 min`.
5. **F5 hedge.** could, might, may, possibly, hoping, if, unless, "we'll see": keep the claim with `hedged = true`; it is excluded from pass-carrying tests.
6. **F6 number.** Look for N anywhere in the sentence after the cap phrase. A range gives the upper end; a value outside [6, 40] is dropped.
7. **F7 lift.** "no longer on a (minutes) restriction", "restriction lifted", "full go", "no limitations" become `reporter_status = cap_lifted`, which ends any earlier cap for that player.
8. **F8 scope.** "for the next N games / this week / back-to-back" attaches to the next game only, with `multi_game = true`; there is no carry-forward in this rule.
9. Unchanged: allow-list first, retweets ignored, negation and questions give unknown, names resolve through reviewed tables only, no LLM.

### 5.3 Labelled test set (hand-written, text-only, outcome-blind)
* Schema per (post, player claim): `label_id, source (synthetic|x_post_id), text_ref (synthetic text, or post_id only), gold_player, gold_type (cap|status|lift|none), gold_N, gold_tense (pre_tip_tonight|future_other_game|past_recap|general), gold_qualifies, labeller, labelled_at`. Real posts are referenced by post_id only; their text stays in `data/` (X terms).
* Seed set (synthetic; the nba/ingest owner turns it into `tests/fixtures/reporters/pretip_labels_v1.jsonl`): the 14 sentences of 5.1 plus the patterns of 5.2, at least 80 rows. That is at least 25 past recaps written to beat F1-F3 (hard negatives), 20 pre-tip caps (half with N, a quarter with a trailing N), 15 pre-tip statuses, 10 lifts and hedges, and 10 general chatter.
* Real set: the first 5 logged game nights. Claude and Devin label from text only, before any box score of those games is joined to any fact. Every post the parser marks as a claim is labelled, plus 50 random ignored posts.
* **Acceptance before Stage B:** precision of `gold_qualifies` among parser-qualifying facts >= 0.95, and false-positive rate on past recaps <= 0.02 (at most 1 of the >= 25 hard negatives). Recall is reported with no floor; low recall costs power, not validity. During the look, every 4 weeks a blind audit of 50 random qualifying facts by text; precision below 0.90 is a kill.

## 6. Known-at discipline and storage
* `first_seen_at` = the `fetched_at` (our clock, naive UTC) of the **earliest raw parquet file containing the post_id**. It is computed from the immutable raw store at fact-write time and recomputed the same way on replay, so a replay can never give an earlier stamp than the original capture. `created_at` is kept but is never known-at.
* `known_at` of every `reporter_*` fact = `first_seen_at`, never backfilled. A post pulled by any search, archive or backfill call is stored with `capture_mode = backfill` and is never eligible for this rule.
* Storage: **a separate append-only DuckDB, `data/reporters/reporter_facts.duckdb`**, with the facts schema (`nba/facts/schema.sql`). The facts rebuild never touches it; the poller is its only writer.
  - Row: `subject player:<id>`; `predicate reporter_minutes_cap|reporter_status`; `object game:<id>`; `value` N or NULL.
  - `value_text` is JSON: `{post_id, platform, handle, created_at, first_seen_at, parser_version, evidence_sha256, status, hedged, multi_game, n_lo, n_hi}`.
  - `known_at` = first_seen_at; `source` = `x:<handle>` (or `bsky:<handle>`); `confidence` < 1.
  - Unique on (subject, predicate, object, source, post_id). `nba.facts.query.as_of` reads it as a second store.
* A `poll_log` row is written per scheduled poll (ok | refused:<reason> | error), so coverage is computed and not guessed.
* Planted tests the builder ships:
  1. A post created before T-60 but first fetched after T-60 is not T-60-eligible.
  2. A replay of the same raw files gives identical facts (hash).
  3. Shifting every `first_seen_at` by +24 h empties the T-60 set.
  4. A `backfill` raw file creates no eligible fact.
  5. The parser imports nothing new.

## 7. Event rates on 2023-24 (descriptive; sizes the rule; not a result)
Source and versions:
- Script `research/scratch/reporter_feed_rates.py` (sha256 `3f20b4fd…`): read-only, `season <= 2024` in SQL, season 2025 never loaded; seed 2026, B 2,000, game-clustered percentile bootstrap, fixed row order (two runs identical).
- Output `reports/reporter_feed/rates_2023_24.json` (sha256 `fc71f927…`), run 2026-10-27 19:04 CDT.
- Inputs: `nba.duckdb` (data_version `49a2fc3d3dff`); `data/scratch/injury_intraday_v2.duckdb` (sha256 `0e64c257…`, header clock); `reports/minutes_v2/minutes_scores.npz` (`9ad6a6ed…`); `reports/minutes_v2/props_pts_A0.npz` (`f47d5b3f…`).

**(A) Official-report changes after the T-60 list.** Compares the latest report <= real tip - 60 with the last report before tip; events per game, both teams; rotation = prior-10 average >= 20 over >= 5 games. These are the changes a reporter post at or before T-60 could get ahead of. They are a **lower bound**: the hourly table has no report between the T-60 list and tip in 33% of games (e.g. :30 tips), and those games count zero.
| per game | 2023 (n 1,316 games) | 2024 (n 1,306) |
|---|---|---|
| rotation Q/D -> OUT | 0.248 [0.217, 0.281] | 0.315 [0.277, 0.354] |
| rotation Q/D cleared | 0.312 [0.277, 0.349] | 0.279 [0.247, 0.312] |
| rotation OUT added (any prior status) | 0.264 [0.231, 0.302] | 0.336 [0.299, 0.375] |
| rotation, any change | 0.774 [0.712, 0.837] | 0.837 [0.772, 0.907] |
| all players: Q/D -> OUT; Q/D cleared; other -> OUT | 0.474 [0.422, 0.531]; 0.464 [0.416, 0.510]; about 0.08 | 0.498 [0.450, 0.547]; 0.442 [0.394, 0.489]; about 0.08 |

**(B) Rotation returners.** First game back after the team played >= K straight games without him; prior-10 average >= 15 min; 1,230 regular-season games a season.
| | 2023 | 2024 |
|---|---|---|
| K >= 3 per game | 0.252 [0.224, 0.283] (310 rows, 207 players) | 0.315 [0.285, 0.346] (387 rows, 243 players) |
| K >= 5 / K >= 10 per game | 0.124 [0.105, 0.145] / 0.046 [0.035, 0.059] | 0.176 [0.153, 0.199] / 0.058 [0.045, 0.072] |
| K >= 3: mean(y - production P mean) | **-3.80 [-4.66, -2.93] min** | **-2.84 [-3.57, -2.14] min** |
| K >= 10: same | -6.45 [-7.99, -4.83] (57 rows) | -4.86 [-6.32, -3.43] (71) |
| K >= 3: P minutes CRPS (all rows) | 4.70 [4.34, 5.11] (4.30, n 27,619) | 4.37 [4.04, 4.70] (4.28, n 27,583) |
| sd(y - P mean), K >= 3 (all rows) | 7.27 (7.71) | 7.25 (7.68) |

From box scores alone, production over-forecasts a returner by about 3 minutes, more after long absences. A free "returner" adjustment needs no reporters, so the fair comparator for a cap claim is production plus that adjustment (the prereg's B_ret, constant -3.3 min, pooled over 696 rows).

**(C) Power simulation for a cap treatment.** NOT a result: N is simulated from the realised minutes because no archive exists. Rows: 696 K >= 3 rows.
- P is approximated as Normal(mu, sd) on [0, 48], with mu = (q10 + q90)/2 and sd = (q90 - q10)/2.563.
- The cap holds with probability h, N = y + U(0, 4); otherwise it is broken, N = y - U(1, 6).
- Pts: Normal(A0 mean, 5.6 sqrt(mean/11)), scaled by the minutes ratio.

| treatment | h | min ΔCRPS/row | sd/row | rows for half-width 0.5 min | pts ΔCRPS | pts sd | rows for pts half-width 0.20 / 0.10 |
|---|---|---|---|---|---|---|---|
| hard truncation at N + 2 | 0.6 / 0.8 / 0.9 | -1.68 / -2.07 / -2.28 | 4.02 / 3.82 / 3.71 | 249 / 225 / 212 | -0.11 / -0.19 / -0.24 | 1.86 / 1.67 / 1.56 | 332 / 268 / 234 ; 1,328 / 1,069 / 936 |
| **mixture 0.8 truncated + 0.2 P** | 0.6 / 0.8 / 0.9 | -1.88 / -2.13 / -2.25 | 3.64 / 3.50 / 3.41 | 204 / 188 / 179 | -0.19 / -0.25 / -0.27 | 1.55 / 1.39 / 1.29 | 230 / 185 / 161 ; 918 / 739 / 643 |

The mixture beats hard truncation in every scenario with smaller per-row variance (a broken cap is not fatal under a proper score). The draft therefore fixes w = 0.8 and slack +2 now, before any live data. The simulated N sits close to the realised minutes, so these effects are optimistic; the prior halves them (section 11).

## 8. What this means for the question as posed
Projection inputs (stated, not measured live):
- about 7.5 games a night (1,230 in about 165 nights; 56 logged in the first 8 nights) and logged share 0.85, so **6.4 logged games a night and about 990 logged games** from a 2026-10-29 start to 2027-04-11;
- cap rows per logged game λ: low 0.05, central 0.10, high 0.15. Central = K >= 3 returners 0.28 a game x P(capped) 0.35 x a 2.5-game ramp-up x P(our reporter posts it with a T-60 first sight) 0.5, plus 0.02 a game of non-returner caps;
- numbered caps assumed to be half of all caps;
- status front-run share f of the about 1.0 later status resolutions a game: 0.2 / 0.35 / 0.5, x coverage 0.9;
- design effect 1.5 for one player's ramp-up (player-season clustering, BEST_PRACTICES 2026-10-20), 1.2 for status.

| test | rows needed | nights at central inputs | verdict for 2026-27 |
|---|---|---|---|
| C1 minutes CRPS, numbered-cap rows, half-width 0.5 min | 188 x 1.5 = 282 | about 880 (about 6 seasons; high λ about 590) | cannot pass in one season |
| C2 pts CRPS, numbered-cap rows, half-width 0.15 | 330 x 1.5 = 495 | about 1,550 (about 10 seasons) | cannot pass in one season |
| M1 cap mechanism, mean(y - m_B), half-width 2.0 min (sd 7.25) | 51 x 1.5 = 76 | 119, about early March (low λ: 238, no; high: 79) | carries at central or high λ |
| S1 status, P(play) log loss vs report-aware baseline, half-width 0.15 (sd 0.8 assumed) | 109 x 1.2 = 131 | 64 at f 0.35, about early January (f 0.2: 114) | carries at plausible rates |

So the honest answer to "does a reporter fact improve minutes and pts CRPS over production" is that one season of live logging cannot say. The CRPS contrasts are reported at every look with their CIs and pooled with 2027-28 under a new id. One season can decide (1) whether cap claims carry a minutes shortfall beyond a free returner rule, and (2) whether status claims beat the official report at T-60. If (1) fails, the CRPS question is not worth a second season. A label-free count after 14 logged nights decides which tests carry a pass, by a rule fixed in the draft; Stage 0 is reported beside it.

## 9. Ops: cadence, the launchd job (steward builds it), and the Mac asleep
* **Cadence per slate team** (ET schedule cache, real tips): polls at **T-120, T-70 and T-35**, plus a **13:30 ET sweep** of all slate teams. T-70 is the last poll serving the T-60 arm, leaving a 10-minute margin for launchd jitter and a slow API. T-35 serves slice D5 (facts first seen in (T-60, T-15]). League-wide handles poll at the same four times around the first and last tips. Cost depends on posts, not polls: an empty poll returns nothing and is not billed, and repeat reads in one UTC day are deduplicated.
* **Capture fixes before day 1** (nba/ingest owner):
  - (a) anchor the lookback at 06:00 ET of the game day (or `last_created_at`, whichever is later);
  - (b) paginate until `start_time`, and never advance `last_created_at` past a short page (refuse and log instead);
  - (c) raise `posts_per_day` to 600;
  - (d) write facts to `data/reporters/reporter_facts.duckdb` with `known_at = first_seen_at`;
  - (e) write a `poll_log` row per scheduled poll.
* **Job sketch:** `ops/launchd/local.nba.reporters.plist`.
  - Trigger: `StartInterval` 300 s. `ProgramArguments`: `uv run --env-file .env python -m nba.ingest.reporters tick`, via `uv`, never `/bin/sh` (TCC, BEST_PRACTICES 5).
  - `tick` reads the schedule cache, polls the teams due in the current 5-minute bucket, and writes raw first, then facts, then a heartbeat `data/ops/heartbeats/reporters.json` ({last_tick, last_ok_poll per team, refusals}).
  - It holds `mkdir data/reporters/poll.lock` (with a pid file; rmdir in `finally`) so ticks never overlap. It does not read nba.duckdb and needs no heavy.lock.
  - Watchdog check: on a slate day, alert if any slate team has no ok poll in [T-90, T-60] by T-55.
* **Mac asleep.** launchd skips `StartInterval` runs while the machine sleeps and runs once on wake. A missed T-70 poll loses no posts (`last_created_at` catches up) but makes them late: first seen after T-60, so they do not count for the T-60 arm. That is correct (it is what we knew) but costs coverage, and the sleeping nights are not random (selection). A team-game counts as covered at T-60 only if `poll_log` has an ok poll in [T-90, T-60]. Uncovered games are excluded from both arms and counted; coverage below 0.70 is a kill. Mitigation (C6): `pmset` scheduled wake plus `caffeinate -s` from 16:30 to 23:59 ET on game days, or later the VM (`ops/vm`), which would put the X token on a second machine.
* Steward effort: about 1 day for the capture fixes and the job, plus the replay oracle (the job is off the daily path, so the oracle should be unchanged) and an adversary read of the filters.

## 10. Playbook check
Model class: information source feeding a fixed adjustment of production. Nothing is fitted; the treatment is a frozen transform of production's own minutes and pts distributions. Nearest classes: quantile/distribution layers (the mixture reshapes production's grid) and gradient-boosted trees (production props, untouched).
1. **Information or capacity?** Information: pre-tip facts the report does not carry (caps never; statuses only later). No capacity is added.
2. **Enough data for this class?** Nothing is trained. The binding constraint is evaluation n (section 8): the CRPS contrasts need 6-10 seasons; the mechanism and status tests fit in one.
3. **Test validity.**
   - Clocks and rows: real tips; our own `first_seen_at`; production rows from the run at or before T-60.
   - DNP handling: the P(play) test includes DNP rows; the CRPS tests use played rows only, in both arms.
   - Support: integer support for pts in both arms (both from production's q199, mapped to integers after scaling).
   - Fair baselines: the minutes comparator knows returner status, and the status baseline knows the T-60 report tag (Q 0.514, D 0.024, P 0.928, docs/research/questionable_status_scope_2026-10-15.md), per BEST_PRACTICES 2026-10-22.
   - Parsing noise pulls effects toward zero, so a blind audit of parser precision runs during the look. A null from a poorly covered or badly parsed feed is INCONCLUSIVE, not a null.
4. **Lineage.**
   - INJURY_TIMING: header-clock known-at, real-tip cutoff and the rotation definition carried forward.
   - RETURNING_TEAMMATE / _LIVE_T30: player-season clustering; the returner's own rows treated separately.
   - COACH_PHASE: pts grid scaled by a clipped minutes ratio, gamma 1; band [0.5, 1.0] here, because a cap only lowers minutes.
   - PPLAY_INTO_PROPS_LIVE: the Q-tag shift of +0.02 to +0.04 (INJURY_TIMING rev 3) is why the baseline uses the T-60 rates.
   - No ancestor guard is dropped.
5. **Scored against:** CRPS of the whole minutes distribution and integer CRPS for pts, as asked; log loss for P(play). Not the market line; a pass earns only a line-level follow-up, never a bet.

## 11. Priors (for PREDICTIONS_LEDGER before any result; drafted, not appended)
* P(Stage B makes M1 pass-carrying) 0.45; P(M1 passes | carrying) 0.70; so P(M1 PASS in 2026-27) about 0.30. Expected D -3.5 min (80% [-7.0, -0.5]).
* P(S1 carrying) 0.75; P(S1 passes | carrying) 0.80; P(S1 PASS) about 0.60. Expected ΔLL -0.30 nats per qualifying row (80% [-0.60, -0.05]). Not higher because the front-run share is unmeasured and three teams are uncovered.
* P(C1 pass-carrying in 2026-27) 0.08; it needs numbered-cap rows at about 0.29 a game, about six times my central guess. Expected C1 -1.0 min CRPS (80% [-2.2, +0.2]); expected C2 -0.10 (80% [-0.30, +0.06]).
* P(any kill: coverage < 0.70, integrity, or parser precision) 0.20.

## 12. What I could not verify
- X: the Developer Agreement beyond the policy page; whether `exclude=replies` drops reporters' self-threads ("1/2, 2/2"); edit-history behaviour on timelines; the Basic/Pro migration dates (third-party pages only).
- Bluesky: its ToS and whether any of our beat writers are on it; the getAuthorFeed lexicon (the fetch returned 404).
- In-season posts per handle per day: the cost estimate is a guess from 2 preseason days.
- The rates λ and f: Stage 0 bounds λ from above; the 14-night count measures both.
- Whether 2023-24 reports exist at 15-minute resolution after the T-60 list (the hourly table makes 7A a lower bound).
