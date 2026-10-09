# Kalshi + parlay opening-night readiness (2026-10-09)

Read-only path only: public GETs, no orders, no credentials. `nba.duckdb` and `kalshi.duckdb` were
opened read-only (kalshi.duckdb via a file copy so the launchd writer was never contended).
Nothing was installed and no job was started or stopped. Opening night is 2026-10-20.

## 1. Snapshot health (launchd, every 15 min)

Window: first run 2026-10-08 15:44Z to latest 2026-10-09 15:29Z (about 24 h). n = 90 runs logged
(96 expected at 900 s; the Mac sleeps, and launchd only fires while awake).

| Item | Value |
|---|---|
| Runs with all series ok | 88 of 90 (97.8%; 95% Wilson CI 92% to 99.4%) |
| Run with every series failed | 1 (2026-10-08 21:51Z, `markets=0 raw_files=0`): DNS `ConnectError` on all 14 series, then a 35 min gap |
| Run with one series failed | 1 (about 07:03Z, from the `snapshot.err` mtime): `KXNBABLK` `RemoteProtocolError` ("Server disconnected"); the other 13 series were fine |
| Other gap | 66 min, 14:24Z to 15:29Z (no run logged: machine asleep) |
| UNMATCHED warnings | 8 runs on 2026-10-08, none since the 18 aliases were added |

Failures by series: the DNS outage hit all 14 configured series once each, so `KXNBASTL` and `KXNBABLK`
appear in `snapshot.err` only because they are last in the list; the only series-specific blip was
`KXNBABLK` once. `snapshot.err` had no timestamps (times above are inferred from the run log and file mtime).

**Do transients get handled? They did not, before this change.** `KalshiClient._get` retried only HTTP
429/5xx. A `ConnectError` or `RemoteProtocolError` is raised by httpx before any response exists, so it went
straight to the per-series failure, and that series' data for the 15-minute slot was lost (a real
top-of-book observation, not recoverable later). Fixed: transport errors (`httpx.TransportError`: connect,
protocol, timeout) are retried up to 4 attempts with 1 s, 2 s, 3 s backoff, then re-raised so the series is
still reported as FAILED. Tested in `tests/kalshi/test_snapshot.py::test_client_retries_transient_transport_errors`
(2 transient errors then success; persistent failure re-raises after exactly 4 calls). Worst case for a fully
offline run is 14 series x 6 s of backoff, about 85 s, well inside the 900 s interval. Failed attempts are not
cached (the raw page is written only after a successful response), and the market upsert plus the
`(ticker, ts, source)` price key keep re-runs idempotent.

Also changed: stderr lines (`FAILED`, `UNMATCHED`, new `UNPARSED`) now carry an ISO timestamp, and the UNMATCHED
line names the players. The exit code semantics are unchanged except that an unparsed prop-series title now
also exits 1 (see section 2).

### Raw JSON growth (bounded?)

Measured on real bytes (not `du` blocks), `data/kalshi/raw/<day>/`:

| Day | Files | Bytes | Runs |
|---|---|---|---|
| 2026-10-08 (from 15:44Z) | 980 | 3.06 MB | 35 |
| 2026-10-09 (to 15:29Z) | 1,623 | 4.55 MB | 58 |

Per run: 28 gz files (14 series x open + settled page), about 78 KB. At 96 runs/day that is about 7.5 MB/day
today (preseason, three active series). `kalshi.duckdb` is 4.5 MB for 23,937 price rows (about 34k rows/day).
`data/kalshi` total is 21 MB.

Where the bytes go: about 85% is the `*_settled_*` pages (the 3-day settled sweep re-downloads the same
800+ settled markets every 15 minutes; the content is identical run to run). Open pages are the only
non-reproducible information.

Not bounded as built. Projection for the regular season (assumption, not measured, since no prop market is open yet):
a busy slate posts roughly 10 games x 10 prop series x ~8 players x ~5 ladder rungs, thousands of markets,
at roughly 100 to 250 B of gz per market. That is plausibly 1 to 3 MB per run, so 100 to 300 MB/day on heavy days, and tens of GB over a season
if settled pages keep being re-stored.
Treat the range as order-of-magnitude; re-measure after the first game week:
`find data/kalshi/raw -type f -name '*.gz' -newermt 2026-10-20 | xargs stat -f %z | awk '{s+=$1} END {print s/1e6 " MB"}'`.

### Retention proposal (nothing deleted; maintainer decision)

| Age | Keep | Drop |
|---|---|---|
| 0 to 7 days | everything | nothing |
| 7 to 30 days | `*_open_*` pages for runs at :00 and :30 (hourly-ish is enough to replay prices); all pages with a non-empty `markets` array on game days | `*_settled_*` pages (results already live in `kalshi_markets.result`; settled data re-pullable from `/historical/markets`); pages under 300 B (empty series) |
| over 30 days | one tarball per day of the retained open pages (`tar czf raw-YYYY-MM-DD.tgz`), moved off the working tree | the loose files |

Cap: warn when `data/kalshi/raw` exceeds 5 GB. The cheapest real fix is in the writer (not done here, it
changes snapshot behaviour): skip writing a settled page whose sha256 equals the previous run's page for that
series, or sweep settled markets once an hour rather than every run. The DuckDB prices table is small
(order 20 MB/day worst case) and needs no retention in season. Rotate `snapshot.log`/`snapshot.err` monthly
(currently 14 KB and 2 KB). A dry-run listing command for the 7-day rule:
`find data/kalshi/raw -type f -name '*_settled_*' -mtime +7 | wc -l` (add `-delete` only after review).

## 2. Series coverage and the parser/mapper

Evidence: the 1,514 markets in `kalshi.duckdb` (copied read-only) plus 5 days of raw pages. I did not call the live
API in this review, so "exist now" is what the ingest has seen; `python -m nba.kalshi discover` lists all.

| Series | Seen in DB (n) | Title / threshold format | Parser (`nba/kalshi`) | Mapper (`kalshi_map.py`) |
|---|---|---|---|---|
| KXNBAGAME | 208, open now 66 | `... Winner?`; ticker suffix = team abbr | `game_win`, no threshold | yes: `win` leg |
| KXNBASPREAD | 626, open 50 | `X wins by over 10.5 points`; `floor_strike` 10.5; ticker `-LAL11` | `spread`, threshold = floor_strike | yes: `spread` leg, team from suffix |
| KXNBATOTAL | 558, open 42 | `Full Game: Over 211.5 points scored`; ticker `-212` | `total`, threshold = floor_strike | yes: `total` leg |
| KXNBAPTS | 124 | `Name: 40+ points`; floor_strike 39.5 | `pts` (verified) | yes |
| KXNBAREB | 100 | `Name: 4+ rebounds` | `reb` (verified) | yes |
| KXNBAAST, KXNBA3PT | 0 seen | assumed `Name: N+ assists` / `threes` | `ast` / `fg3m` by series (format unverified) | yes |
| KXNBAPRA/PR/PA/RA | 0 seen | assumed `Name: N+ ...` | `pra/pr/pa/ra` | NO: `unsupported_series_or_stat:<series>` (no joint marginal for sums) |
| KXNBASTL, KXNBABLK | 0 seen | assumed `Name: N+ steals/blocks` | `stl` / `blk` | NO: same reason (settler supports them; the props model has no stl/blk marginal) |
| KXNBATEAMTOTAL | 0 seen | unknown | `team_total` row | NO: unsupported |

Checks run: every one of the 1,514 stored event tickers parses (`parse_event`), both team abbreviations are among the 30 in
`configs/team_markets.yaml`, every spread suffix resolves to a team, every game suffix is a team. 0 failures.
All 224 stored prop titles parse (`parse_prop_title`). Nothing was seen for AST/3PT/STL/BLK/combos, so those formats are
unverified until a market exists; the parser takes the stat from the series ticker, so only the `Name: N+` shape matters.

Gaps and what changed:
- Unmapped series are never silent in `evaluate`: they land in the printed skip-reason dict
  (`unsupported_series_or_stat:KXNBAPRA` etc.). That is the "flag" for the mapper. Not mappable today: PRA/PR/PA/RA,
  STL, BLK, TEAMTOTAL (and the quarter/half/H2H families, which are not ingested at all).
- **Fixed: silent drop of drifted prop titles.** A market in a prop series whose title did not match
  `Name: N+` was discarded as "not a prop" with no trace. `parse_markets_frame(..., unparsed=[...])` now collects those
  tickers; the snapshot report lists them (`unparsed_props`), prints `UNPARSED prop-series markets` to stderr and exits 1.
  Test: `test_unparseable_prop_series_title_is_surfaced_not_dropped`.
- Props are posted game day close to tip-off, and none were open when checked. The first useful prop snapshots will
  arrive on 2026-10-20 itself.

## 3. Alias readiness

State (regenerated this run, read-only; `python -m nba.kalshi.alias_review --out <file>`): 20 prop names seen, 20 matched,
0 unmatched, 0 ambiguous; 497 active players staged, 488 high confidence, 9 low.

- **Bug found and fixed in the staging file.** For the nine Jr./II collisions `preseed_roster` took the first index hit,
  which was the retired namesake (father) with 0 games in the DB: e.g. `Gary Payton II` was staged as id 56. Promoting those
  `low` rows would have mapped the son's prop markets to a player with no history. It now leads with the active player's own id.
  `configs/kalshi_aliases_candidates_2026-10-09.yaml` was regenerated (53 changed lines, the nine rows). Test:
  `test_preseed_collision_proposes_the_active_player_not_the_namesake`.

  | Kalshi name | Staged id (active) | Team | Career games in DB |
  |---|---|---|---|
  | Brandon Williams | 1630314 | DAL | 154 |
  | Gary Payton II | 1627780 | GSW | 255 |
  | Gary Trent Jr. | 1629018 | MIL | 303 |
  | Jabari Smith Jr. | 1631095 | HOU | 327 |
  | Jaren Jackson Jr. | 1628991 | UTA | 263 |
  | Kevin Porter Jr. | 1629645 | MIL | 181 |
  | Larry Nance Jr. | 1626204 | CLE | 256 |
  | Ron Harper Jr. | 1631199 | BOS | 98 |
  | Tim Hardaway Jr. | 203501 | DEN | 340 |

  Teams are from the DB's last game (June 2026); offseason trades are invisible, so confirm the team on opening day.
  Alias keys are normalized (`Jr.`/`II` stripped), so each pair shares one key; the key must map to the active id.
- **Fail-loud, tested end to end.** An unmatched name is stored with `player_id` NULL (history is kept), logged to
  `unmatched_names.json`, printed by the snapshot (now with the names) and exits 1. In `evaluate`, `map_market` returns
  `unmatched_player`; `evaluate` now also prints `WARNING: N prop market(s) on this slate have no reviewed player alias and
  were NOT priced` with the tickers on stderr, and the count is in the skip summary.
  Test: `test_evaluate_surfaces_unmatched_player_instead_of_dropping` (the prop is absent from output and shadow log, the
  warning names its ticker, the other contracts still price).
- Old rows: 100 of 124 `KXNBAPTS` and all 100 `KXNBAREB` rows still have `player_id` NULL (ingested before aliases).
  They matter for settled-prop calibration, not for opening night. On a writer-free `kalshi.duckdb`, a re-resolve is:
  load `load_reviewed_aliases()`, `normalize_name(title.split(':')[0])`, `UPDATE kalshi_markets SET player_id = ?`.
  I ran exactly this on a COPY (rehearsal below): 58 of 58 names in one event resolved.

### Opening-day alias review (about 10 minutes)

1. After the first snapshot with props (typically 1 to 3 h before tip), check `data/kalshi/snapshot.err` for
   `UNMATCHED` / `UNPARSED` lines. If none, skip to step 6.
2. `uv run python -m nba.kalshi.alias_review --out configs/kalshi_aliases_candidates_2026-10-20.yaml`
3. Read `unmatched:` first. For each name check `matched_name`, `team`, and `why`. A `high` proposal with the right team is a
   copy-paste. Anything `medium`/`low` needs a look at the player's team page.
4. For the nine Jr./II names, take the id in the table above only after confirming the team; copy rows into
   `configs/kalshi_aliases.yaml` as `"Display Name": player_id`.
5. Rookies (2026 draft class is not in nba_api's bundled list): the proposal list is empty. Their props cannot be priced
   anyway (no `player_rates`, no `players_static`); leave them out and let `evaluate` report them as unmatched/no model.
6. Run the next snapshot (or wait for it), then `evaluate --date 2026-10-20 --no-log` once to see the skip summary and the
   WARNING line before any shadow row is written.

## 4. Parlay shadow log rehearsal (on a COPY)

`forward_predictions` in `nba.duckdb` is empty right now, so `evaluate` on the real DBs has nothing to price (the
`evaluate_2026-10-08/09.json` reports in `reports/parlay/` contain 0 contracts). I therefore built a rehearsal in the scratch
area: copy of `kalshi.duckdb` + a separate rehearsal `nba.duckdb` (games and box scores copied read-only from the real DB,
plus synthetic forward rows), for the real 2026-06-13 NYK@SAS Finals game (game_id 0042500405). Real parts: Kalshi market
list and the 5 priced `KXNBAPTS` markets (last hourly candle before tip, aliases re-resolved on the copy), team map, box score.
Synthetic parts (plumbing only, no model claim): win prob 0.55, prop quantile grids from each player's last 30 games, and
SYNTHETIC game-winner prices (0.48/0.50, flagged `source='SYNTHETIC'`). Results:

| Check | Result |
|---|---|
| Shadow rows written | 14 (7 priced markets x YES and NO sides); 106 side-quotes skipped as `no_price` (no price for the other 53 mapped markets) |
| Idempotent | second `evaluate` run: still 14 rows (shadow_id = `slate|ticker|side`, first prediction stands) |
| Verdict | all `no_positive_ev_found`; 0 paper trades; model weight 0 (no settled history) |
| Raw (unshrunk) model vs market | shown as hypothesis only, e.g. Wembanyama 30+ NO raw 0.687 vs ask 0.560 (not a signal: synthetic grids) |
| `--settle` | 14 shadow rows settled against the box score; NYK won 94-90 so NYK-YES true, SAS-YES false; props resolved from `player_game_stats` |
| Both sides | YES and NO are logged; the NO row is the same event, so `track_record` counts only `side='yes'`: n = 7 settled, not 14 |
| 14-date gate | `n_dates = 1` after settling; `gated_skill(14)` returns 0, so the engine stays on the market |
| DNP settlement | covered by existing tests (`test_settle_game_and_dnp_policies`, `test_player_absent_from_final_box_score_is_dnp_not_open`): box-score row with 0 minutes or no row in a final game is a DNP; `void` default refunds (outcome NULL, excluded from skill), `loss` loses. Kalshi's real DNP rule is unconfirmed. |

**Bug found and fixed: the 14-date gate counted UTC log days, not game dates.** `track_record.n_dates` was
`count(DISTINCT CAST(created_at AS DATE))`. `created_at` is a UTC wall clock. Props post late, so one night's rows logged
at 22:00Z and 00:30Z counted as two dates, and evaluating several slates in one sitting counted as one. It now counts distinct
slate dates from the `shadow_id` prefix that `evaluate` writes (falls back to the created_at date for free-form ids).
Test: `test_track_record_dates_are_slate_dates_not_utc_log_time`. No pricing or EV math changed.

**Operational risks not changed (flagging):**
- `evaluate` prices the latest stored price for each open market with no tip-off cutoff. Because the first prediction of
  the day stands in the shadow log, a first run made after tip logs an in-play price. Run it before tip.
- Kalshi can settle a market `scalar` (void-like) while the shadow log settles from the box score; those two can disagree.
  Shadow rows are unaffected, but do not cross-check them against Kalshi `result` for `scalar` markets.
- `nba.daily` is not producing forward rows for 2026-10-20 yet (table empty). That is the blocker for a live `evaluate`.

## 5. Local assistant without Ollama

`which ollama` finds nothing. `uv run python -m nba.parlay assistant --ask "price LAL" --date 2026-10-20` exits 2 with
"Ollama is not reachable at http://localhost:11434 (ConnectError)" followed by the install steps
(`brew install ollama`, `ollama serve`, `ollama pull qwen2.5:3b`, `OLLAMA_HOST`). No traceback, no DB access.
A running server without the model gives "Ollama is running but model ... is not pulled. Run: ollama pull ...".
Both are covered by `tests/parlay/test_assistant.py`. Not run against a live LLM (none installed).

## Verification
- `uv run pytest tests/kalshi tests/parlay`: 131 passed (includes the 5 new tests below).
- `uv run ruff check nba tests`, `uv run ruff format --check nba tests`, `uv run mypy nba`: see the final run in the hand-off.
- New tests: transport retry; unparsed prop title surfaced; unmatched player surfaced by `evaluate`; slate-date counting
  in `track_record`; preseed collision.
