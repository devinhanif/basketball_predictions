# App analysis studio (design, 2026-10-21; first slice built)

Source: Devin 2026-10-21 18:30 CDT ("make the app cool for analyzing basketball"; panes for every tested idea that moved;
intuitive UI, pictures, interactive courts, video links, a highlight reel) and 18:40 CDT (player "build" card).
Companions: `docs/APP_ARCHITECTURE.md` (sections 2, 3, 9), `docs/APP_CONTENT_SPEC.md`, `docs/APP_SECURITY_BASELINE.md`.
Branch `agent/app-analysis-studio`. Nothing is deployed or published. Owner: the app agent; the export (`nba/export`) is
owned by another agent, so section 6 is a request, not a change.

## 1. Rules this inherits (unchanged)
- Every number comes from a stored object and carries its source table and as-of date; no invented prices, odds, results or
  attributes. A rate prints its n; a claim prints n and a clustered interval or it is not shown.
- Seasons: examples and displays use seasons <= 2024 (and the live 2026 log when it exists). Season 2025 is the frozen holdout.
  The app shows 2025 rows only where the export declares them display history (existing rule). **No 2025 shots or
  play-by-play are ever added** to fixtures or the export. The "What we learned" pane quotes ledger results that were
  produced by logged holdout touches (T473, T478); it shows the ledger's numbers, not any 2025 game data.
- Responsible-gambling footer on every page (the shared `Footer`); the "keep your money" state stays first-class.
- Read-only: no orders, credentials, accounts, payments, LLM calls, posting. Studio pages call no API in this slice.

## 2. Navigation and panes
New bottom-nav and top-nav item **Studio** (`#/studio`) opening a hub of panes. Each pane is one hash route, phone first
(375 px), tap targets 44 px, readable without hover, light and dark.

| Pane | Route | Status | Question it answers |
|---|---|---|---|
| Studio hub | `#/studio` | built | where to start |
| Shot chart and replay | `#/court/:gameId?` | built on 4-game fixture | where was each shot taken, and in what order |
| Player builds | `#/builds/:playerId?` | built on 5-player fixture | what kind of player is this, how sure are we |
| What we learned | `#/learned` | built (10 items) | which tested ideas moved anything, honestly |
| Card "Why" panel | inside `#/leg/...` | built, hidden until reasons exist | why does the forecast look like this tonight |
| Matchup lens | `#/matchup/:gameId` | design only | who guards whom, and what that did |
| Lineup lens | `#/lineups/:gameId` | design only | which five-man units played, with possession counts |
| Data reel | `#/reel/:gameId` | first part built (replay inside the court pane) | the key plays of a game as a captioned replay |
| Video links | inside court and reel | link-out only, section 5 | the official NBA.com page |

Every "tested idea that showed a change" becomes a row in What we learned (section 3), and gets its own deeper pane only
when the export carries the data to draw it (Matchup lens needs `player_game_matchups` aggregates, Lineup lens needs
`possessions`; both listed in section 6). Nulls are not panes.

## 3. What we learned (built)
`app/web/src/learned.json` (typed in `learned.ts`) holds the items; `pages/Learned.tsx` renders them. Statuses:
**Passed the holdout**, **Held in two seasons, holdout untouched**, **Real but small**, **Passed one season only**,
**Failed, but informative**. An item with no n, or no interval and no stated alternative, is not drawn (`isShowable`).
Numbers are strings copied from the source text; `app/api/tests/test_learned_ledger.py` fails if any is not in its ledger row,
or in the named sidecar report when the ledger row does not carry n.

| Item | Ledger rows (checked) | Status shown | Headline number as the ledger prints it |
|---|---|---|---|
| Averaging two models, points | T478, T479 | Passed the holdout | AVG minus FULL -0.0133 [-0.0158, -0.0109], n 28,173 / 1,321 games |
| Lower-tail mixture | T136, T148, T151 (docs/LOWER_TAIL.md) | Two seasons | 2023 -0.0126 [-0.0172, -0.0080] n 27,619; 2024 -0.0109 [-0.0155, -0.0061] n 27,583 |
| Injury report and recent minutes, P(play) | T482, T484 | Two seasons (T484 WOUNDED, said on the card) | -0.0854 [-0.0910, -0.0801] / -0.0922 [-0.0976, -0.0866] |
| T-30 confirmed starters | T085, T166 | Two seasons, upper bound | -0.0412 [-0.0475, -0.0346] (n 27,583 from docs/LINEUPS_KNOWN.md); red-team range -0.023 to -0.041 |
| Line calibration | T493 | Real but small | CAL-A0 log loss -0.0117 [-0.0139, -0.0096], n 37,528 legs / 1,260 games; CAL 0.6882 vs market 0.6865, still behind |
| RECENT_ROLE_FULL | T473 | Failed, informative | -0.0116 [-0.0159, -0.0076], shuffled-label guard fired -0.0021 [-0.0036, -0.0006] |
| F1_WARM_WINDOW | T498 | One season only | 2023 -0.0188 [-0.0247, -0.0126]; 2024 -0.0021 [-0.0069, +0.0026] (n from reports/prereg_f1_warm_window/results.json) |
| WOWY_STRUCT | T499 | One season only | pts leader out 2023 -0.0096 [-0.0170, -0.0021]; 2024 -0.0037 [-0.0097, +0.0023] (n from reports/prereg_wowy_struct/results.json) |
| Lineup archetype mix | T042 | Real but small | R2 0.016, n 1,154 lineups; **the ledger row has no interval** (permutation p 0.004, null R2 0.005), so the card says so instead of inventing one |
| Market vs model, game winners | docs/REPLAY_2025_26.md (no ledger row) | Failed, informative | Brier model minus market +0.0064 [+0.0023, +0.0104], 1,311 games, 207 date clusters |

Judgement calls to confirm: (a) T042 is shown although it lacks a clustered CI, because Devin named it; the card states the
gap. (b) The market comparison has no ledger id; it is cited to the replay report and labelled exploratory. (c) The T-30
n comes from LINEUPS_KNOWN.md because the ledger row omits it. Candidates considered and left out: null results (they are
not "a change"), T497 MEDIAN_BIAS (partial, no follow-up allowed), T500 (not read in this slice).

## 4. Interactive court (built)
`court/geometry.ts` (pure, tested), `court/Court.tsx` (SVG, Preact only, **no new dependency**), `pages/CourtPage.tsx`.
- Half court in NBA shot coordinates (tenths of a foot, basket at 0,0), basket drawn at the bottom. Five disjoint zone regions
  (restricted area, paint non-RA, mid-range, corner 3, above-the-break 3) shaded by FG% over the currently filtered shots
  (result filter ignored for the percentages so "made only" does not read 100%). **A zone with fewer than 5 attempts is not
  shaded and prints "n=k".** The zone table under the chart prints made/tried for every zone.
- Markers: made = filled disc, missed = cross (shape plus colour). Tap anywhere: the nearest shot within 40 court units is
  selected (an effective large target; markers themselves are small). Hover is never required.
- Filters (44 px chips): quarter, 2 or 3 pointers, made or missed, player. Game switcher chips.
- Play-by-play scrubber: slider, Back, Next (44 px), **Replay the game** (steps every 0.75 s, 1.4 s under
  prefers-reduced-motion; the pop animation is disabled there), **Hide later shots** (replay mode: only shots so far).
  The caption is the stored play text (`pbp_description`), then one sentence built from the row. A full ordered shot list
  (each row a 44 px button) mirrors the scrubber for keyboard and screen-reader use.
- Fixture: `app/web/src/fixtures/studio_shots.json`, 681 real shots from four 2024-11-12 games (game ids 0022400001,
  -04, -07, -08 as stored in nba.duckdb), built read-only by `app/fixtures/build_studio_fixtures.py` with `season <= 2024`.
  The page loads it with a dynamic import so the main bundle stays small. Row shape = the proposed `export.shots` (section 6).

## 5. Video and the highlight reel, said plainly
- **We link out, nothing more.** The app never downloads, re-hosts, caches, clips or embeds NBA video or images. The one link
  built today is the official game page `https://www.nba.com/game/<game_id>` opened in a new tab (`rel="noopener noreferrer"`),
  labelled "opens their site". Verification owed: the stored `game_id` must be the NBA `gameId` for that link to resolve
  (the 2024-11-12 fixture ids look like NBA ids; confirm one by hand before any public launch).
- **Per-play video.** stats.nba.com has served per-event video pages keyed by game id and event number (our `game_event_id`
  / `action_number`), with a pattern of the form
  `https://www.nba.com/stats/events/?flag=1&GameID=<game_id>&GameEventID=<action_number>`. I could not fetch it here (no
  network use by this agent), so the pattern is **unverified** and not linked in the UI. If Devin confirms it works, it
  would be an outbound link only, one per shot row ("Find this play on NBA.com").
- **A public app needs Devin's licence decision.** Linking is low risk; anything beyond a link (embedding, thumbnails, clips,
  a reel made of their footage, scraping the video endpoint at scale) is not done and not proposed without a licence
  decision and the NBA's terms read. Until then the studio is private/preview only.
- **The reel we can build ourselves is a data reel**, from our own tables: an animated court replay of key plays with
  captions from the play text, plus the list of official links. Key-play pick rules, all computed not judged:
  (1) a player's made shots in order; (2) the biggest swings (largest change in modelled win probability across a stored
  event, needs `win_prob_events`, section 6); (3) every shot in a quarter; (4) "plays behind a miss" for a card: the
  minutes, foul and blowout facts already in the explainer. Built today: rule 1 and 3 through the filter plus replay on one
  game. Not built: swing detection, auto-generated captions beyond the stored text, links per event. **"Could the AI
  generate a highlight reel" answer:** the reel is deterministic selection plus stored captions; no LLM is needed and none is
  called. An LLM caption layer would be a later, shadow-first decision (architecture section 9 "Why library").

## 6. What the export must carry (request to the export owner)
All as read-only tables in `export.duckdb`, seasons <= 2024 plus the live 2026 log when it exists; never 2025 shots or
play-by-play. `known_at` stamped where the source has it. Column names match the fixtures so the swap is a file change.

1. **`shots`** (court pane) for the slate players' recent games and for any game the explainer links:
   `game_id VARCHAR, action_number INTEGER, period INTEGER, clock_seconds INTEGER, player_id INTEGER, team_id INTEGER,
   loc_x INTEGER, loc_y INTEGER, shot_distance INTEGER, shot_zone_basic VARCHAR, shot_zone_area VARCHAR,
   shot_zone_range VARCHAR, action_type VARCHAR, shot_type VARCHAR, made BOOLEAN, pbp_description VARCHAR, game_date DATE,
   season INTEGER`. Source `shot_events` (233k rows per season 2022-2024; 2026 not yet ingested). The fixture also carries
   `player` (full name) and `team` (abbr) joined in; the export already has `players` and `teams`, so those stay joins.
   Fixture column names: `zone` (= `shot_zone_basic` with both corners merged to "Corner 3"; Backcourt excluded from zone
   percentages), `description` (= `pbp_description`). Size note: 681 rows is 200 KB uncompressed, so request per-game
   slices, not whole seasons.
2. **`pbp_events`** (reel captions, non-shot plays: turnovers, fouls, rebounds, free throws): `game_id, action_number,
   period, clock_seconds, team_id, player_id, action_type, description, score_home, score_away`. Needed for score swings.
3. **`player_matchups_game`** (Matchup lens): `game_id, off_player_id, def_player_id, matchup_minutes, partial_possessions,
   percentage_defender_total_time, percentage_offensive_total_time, player_points, matchup_field_goals_made,
   matchup_field_goals_attempted, matchup_three_pointers_made, matchup_three_pointers_attempted, help_field_goals_attempted,
   switches_on` from `player_game_matchups`. Plus an as-of aggregate `matchup_shares(as_of, off_player_id, def_player_id,
   share_of_possessions, n_games)` computed from games before `as_of`.
4. **`lineup_possessions`** (Lineup lens): `game_id, team_id, possession_no, period, lineup_player_ids (5 ids), off_pts` from
   `possessions`; or a pre-aggregated `lineup_stints(game_id, team_id, lineup_key, possessions, minutes, pts_for, pts_against)`.
5. **`player_archetypes`** (Class on the build card and archetype reasons), once MATCHUP_AVAILABILITY produces it:
   `player_id, as_of DATE, offense_class VARCHAR, defense_class VARCHAR, cluster_version VARCHAR, n_games INTEGER,
   neighbours INTEGER[]` (the ids used as peers for thin samples) with the clustering's fit-through date.
6. **`card_reasons`** (Why panel): `game_id, player_id, stat, kind ('matchup'|'archetype'|'availability'|'other'), text,
   source_table, as_of`. The API passes them as `why: [{kind, text, source, as_of}]` on the leg breakdown; the web side
   already ignores malformed entries and hides the panel when the list is empty.
7. **`player_build`** (build card; pure derived facts so the card is not recomputed in the app):
   `player_id, as_of DATE, position_listed, birth_date, draft_year, draft_pick, draft_status, first_season, n_games_window,
   minutes_share, starter_rate, recent_mpg, recent_n, prior_mpg, prior_n`, per-36 `pts, reb, ast, fg3m, stl, blk, tov` with
   `n_minutes`, and a `player_zone_fg(player_id, as_of, zone, att, made)` plus `league_zone_fg(as_of, zone, att, made)`;
   a `level_cuts(as_of, cut1..cut4, rule, n)` row; and `player_per36_percentile(as_of, stat, player_id, percentile, pool_n)`.
   The fixture builder shows the exact computation; the export may reuse it or replace it.
8. **`win_prob_events`** (swing detection, later): `game_id, action_number, p_home_after` from a stored in-game model, only
   if one is validated. No such model is in production; do not fabricate one for the reel.
9. **Declared display history**: if any 2025 row is to appear, the export must declare it (existing rule); this request
   asks for none.

## 7. Player build card (built on fixture; Devin 18:40)
Route `#/builds`, `pages/BuildPage.tsx`, helpers `build.ts`, fixture `studio_builds.json` (five real players as of
2024-11-12: two stars, two rotation players, one rookie). Every section prints its source table and as-of date.
1. **Class** = archetype (offensive and defensive type) from the as-of clustering that MATCHUP_AVAILABILITY will produce.
   Today the field is `null` and the card says "Not assigned yet", showing only the listed position (a stored fact, labelled
   as such). No class is invented.
2. **Level 1 to 5** = role tier from as-of minutes share (minutes per game / 48 over the 365 days before the as-of date),
   cut at the **quintiles of minutes per game / 48 over all 2023 and 2024 player-seasons with at least 20 games (n = 894)**:
   0.271, 0.388, 0.514, 0.640 (computed by the fixture builder, printed on the card; they are re-derived, never hand-set).
   The gate of 20 games is the minimum n to enter the distribution. Starter rate is printed beside the level but does not
   move it (a quintile of one variable is the only cut Devin's "never hand-picked" rule lets us justify today; a two-variable
   tier needs a pre-stated rule and is left for later). The rotation filter is therefore "at least 20 games", not a
   minutes threshold.
3. **Potential** = stored facts plus a **range, not a promise**: draft pick and year, age at the as-of date, seasons in the
   league, minutes trend (last 20 games against the games before, n printed), and the 10th to 90th percentile of next-season
   minutes per game among 2022 and 2023 player-seasons of the same age (within one year) and the same level, with the number of
   analogs. Shown only with at least 30 analogs, else "Not enough comparable players". Only two season-pairs exist in our data
   (2022 to 2023, 2023 to 2024), so analog counts are small (31 to 34 on the fixture); the card says so by printing n.
4. **Stat bars** = as-of per-36 rates for points, rebounds, assists, threes, steals, blocks, turnovers, with the rank among the
   league window pool (players with at least 20 games; n = 439 on the fixture, "99th" is the cap so a sample never claims
   100th), and `n = games, minutes` under every bar. Shooting zones show FG% against the league's in the same zone (tick) with
   `n = made of tried` and "(small sample)" under 30 shots.
5. **Rookies and new arrivals.** If the player has fewer than 20 own games in the window, the card adds **Borrowed build**:
   the mean per-36 of players of the same listed position drafted within 5 picks whose first season was 2022 or 2023
   (their first 20 games), with the count of peers (5 for the fixture rookie) and the player's own games seen (11). This is a
   stand-in for archetype neighbours and is named as such; when `player_archetypes.neighbours` exists it replaces the rule.
   "New to a team" (games with the current team) is not built: it needs `team_id` history in `player_build`.
No attribute is invented: height, weight, "traits", ratings and badges are deliberately absent. A rating nobody can trace to a
table would break the source rule.

## 8. Card "Why" panel (built, hidden)
`why.ts` (`parseReasons`) + `WhyPanel.tsx`, mounted on the leg breakdown. It reads an optional `why` array and renders kind tag,
text, and `source, as of date` when present. Absent, empty or malformed input renders nothing (no empty box, no placeholder
copy). Text is escaped (Preact text nodes), capped at 400 characters and 8 reasons.

## 9. Files
`app/web/src`: `learned.json`, `learned.ts`, `build.ts`, `why.ts`, `WhyPanel.tsx`, `court/{geometry.ts,Court.tsx}`,
`pages/{Studio,CourtPage,BuildPage,Learned}.tsx`, `fixtures/{studio_shots,studio_builds}.json`, tests beside each, router,
`App.tsx`, `components.tsx` (nav), `styles.css`, `legTypes.ts` (optional `why`), `pages/Leg.tsx` (one line).
`app/fixtures/build_studio_fixtures.py` (read-only fixture builder). `app/api/tests/test_learned_ledger.py` (ledger trace).

## 10. Left for later
Matchup and Lineup lenses; swing detection and win-probability reel; per-event video links (needs a verified URL pattern and
Devin's licence call); caption styling beyond stored text; two-variable level tier; "new to team" borrowed builds; class
labels; a studio route in the API instead of fixtures; the export tables above; screenshots in CI. Anything that publishes or
embeds NBA media waits for Devin.
