# Pre-freeze review: STAR_FLOOR_PAPER (2026-10-22)

Reviewed: `docs/prereg/STAR_FLOOR_PAPER.md` at commit 12c6563 (2026-10-22 19:16:57 -0500), gate script
`research/scratch/star_floor_gate_2026_10_21.py` (same commit), HOLDOUT_ACCESS_LOG row 135 (commit 7d6b60d).
Model class: none fitted; an empirical historical-frequency lower-tail estimate (closest playbook class: quantile and
distribution layers) scored against the Kalshi ask as a paper trading rule.

**Verdict: NOT_READY as written; READY_TO_FREEZE once M1-M6 below are applied verbatim.** The six MUSTs are
mechanical. None changes a threshold, and none needs another outcome-free read of the data. M1 and M3 change what the
harness reads. M2, M4, M5 and M6 close ways the harness could resolve the rule more than one way. No outcome or
return was computed in this review.

Verdict: BLOCKED

## Top finding
The Kalshi snapshots also carry markets for games on other dates. The 10-22 capture lists `KXNBAPTS-26OCT23HOUSAS`
and `26OCT23NYKBOS` the day before, and every pre-tip snapshot on 10-21 still lists in-play markets of games that
tipped earlier. The rule and the gate script match a market to a game only by the player's roster team
(`memb & set(gteams[gid])`, gate script line 37). On a back-to-back, the harness would book tomorrow's market at
tomorrow's price as tonight's trade, and could count two trades for the same player at the same k. On 10-21 this did
not bite: no team that played 10-21 had a later-dated market in its pre-tip window. On ordinary slates it will. Fix: M3.

## Checks (all label-free; no season 2025 loaded by this review; DuckDB read_only=True)

| # | Check | Command / evidence | Result |
|---|---|---|---|
| 1 | Event-date matching | Event tickers in the last pre-tip snapshot per 10-21 game, from the `.venv/bin/python` count over `data/raw/live_2026-10-21/kalshi/<series>_p0`. Every window contains `26OCT22CLEPHI`, `26OCT22DENOKC` and, from 23:30Z, `26OCT23NYKBOS`; the 02:00Z windows still list `26OCT21INDNOP` and others, already in play. The 10-22 06:59Z file lists `26OCT23HOUSAS` (HOU also played 10-21). exit 0 | FAIL. The rule never ties a market to the game. M3 |
| 2 | Regular-season definition | `select season, substr(game_id,1,3), count(*) from games where season between 2022 and 2024`. Each season has 1230 rows with prefix 002, 82-84 with 004 (playoffs) and 6 with 005 (play-in). The `games` table has no season_type column. The gate script's history query (line 45) and its 2025 last-team query (line 25) filter on season only, so they include playoffs and play-in, while the rule says "regular-season". exit 0 | FAIL. Two harnesses could disagree. M1, M2 |
| 3 | DNP settlement | `rules_secondary` of a captured market reads: "If a player is inactive, or active but never takes the court, the market settles to a fair price." A DNP is therefore neither Yes nor No. The rule's "$1 per contract on Yes … settles as Kalshi settles it" lets a harness drop the trade, score it as No, or use the fair price. | FAIL (ambiguous). M5 |
| 4 | Cannot fail / horizon | The horizon is "first date with >= 100 trades AND >= 14 dates", with no end date. If the volume (about 6 a night, from one opening-night slate of 11 games) falls short, the test never scores. That is a way to never fail. | FAIL. M6 |
| 5 | Threshold arithmetic | `ask <= hit_rate − 0.05` in floats: for h/n = 0.95 and ask 0.90, `0.95-0.05` is 0.8999999…, so the line is wrongly excluded. The boundary depends on the implementation. | FAIL (ambiguous). M4 |
| 6 | Thresholds chosen without outcomes | The gate script reads only prices, roster membership, the 2025 team_id and 2022-24 stats. It reads no 2026-27 stat or settlement. The test sample (games tipping after the freeze) is disjoint from the 10-21 capture used to choose the thresholds, so even knowledge of 10-21 results could not bias the forward test. The 12 threshold cells printed are counts only. | PASS |
| 7 | Season-2025 use | team_id is not an outcome of any 2026-27 game; it is known before tip; and no model or metric is scored on 2025. Acceptable as eligibility. The log row covers "a read". The harness will re-read the same columns every night unless the map is frozen. | PASS with condition. M2 |
| 8 | Correlated lines / clustering | Several lines per player (10+ and 15+ of the same player are nearly the same bet) all fall on one date, and resampling dates keeps them together, so the CI is valid. "100 trades" overstates the information: the effective n is nearer to distinct player-stat-games. The percentile bootstrap with 14-17 clusters undercovers; the required t-interval on per-date means offsets this. | PASS. Inference is valid; n is presentational (S1) |
| 9 | Survivorship | Eligibility needs >= 60 games with minutes > 0 in 2022-24 and a game for the same team in 2025-26. This drops rookies, players who missed all of 2025-26, and anyone traded or signed. All of it is known before tip, so there is no leak. The population is healthy, stable veterans, which is the hypothesis. The history stops a season early (2025-26 is the holdout), and aging lowers the realised hit rate. That bias works toward failing, not passing. Excluding DNPs from the hit-rate denominator matches Kalshi's rule that a DNP settles at a fair price. | PASS |
| 10 | Gaming by the harness author | The harness is written after the freeze. Snapshot choice ("last"), name resolution, duplicate tickers and the voided-trade threshold were all open choices. | FAIL until M3, M5 and M6 are applied |
| 11 | Universe from `forward_predictions` | 10-21 has 11 games in `forward_predictions` and 12 Kalshi event codes. Games missing from `forward_predictions` drop out silently. | Minor. Count and report (S2) |

The six attack checks (shuffled labels, planted future, missingness, knockout, replication, integer support) do not
apply before the freeze: nothing has been fitted or scored yet. Planted future is covered structurally by the tip
gate (`t < tipoff`, scheduled tip) and by M3.

## MUSTs: exact replacement text for `docs/prereg/STAR_FLOOR_PAPER.md`

**M1. Replace the `History` bullet with:**
> - **History**: games with `game_id LIKE '002%'` (regular season; excludes play-in `005`, playoffs `004` and any other
>   prefix) and `games.season` in (2022, 2023, 2024), joined to `player_game_stats` with `minutes > 0` (NULL minutes
>   excluded); at least 60 such games. With n such games and h of them with stat >= k, hit_rate = h / n.

**M2. Replace the `Same team` bullet with:**
> - **Same team**: the player is on the official roster cache for the slate date for one of the game's two teams, AND
>   his `team_id` in his latest (`max(game_date)`) season-2025 game with `game_id LIKE '002%'` and
>   `game_date < 2026-10-01` is that same team. Players with no such game are ineligible. From season 2025 the harness
>   reads only `player_id`, `team_id`, `game_id` and `game_date`; it reads no stat, minutes or score column. These
>   nightly reads are covered by the 2026-10-22 HOLDOUT_ACCESS_LOG row for this rule, extended to say so before the
>   harness first runs.

(The playoff team equals the last regular-season team, so the 2025 map the gate already read does not change.)

**M3. Replace the `Entry snapshot` bullet with:**
> - **Entry snapshot**: for each series separately, the last file in
>   `data/raw/live_<slate date>/kalshi/<series>_p0/` whose filename UTC timestamp t satisfies
>   tipoff − 10 min <= t < tipoff (`forward_predictions.tipoff`, UTC). A market belongs to the game only if the date
>   token of its `event_ticker` (YYMONDD, e.g. `26OCT22`) equals the game's America/New_York date AND the player's team
>   is one of the game's two teams. Markets with any other date are ignored. The market must have `status` = `active`
>   and a title matching `^(.+?): (\d+)\+ (points|rebounds|assists|threes)$`. Bid and ask are parsed as exact decimals
>   from `yes_bid_dollars` / `yes_ask_dollars`, with 0 < bid < ask < 1 and ask − bid <= 0.05. Player identity: the
>   title name is normalised (NFKD, ASCII, lowercase letters only) and matched to the game's `forward_predictions`
>   players. Zero matches or more than one match drops the line, and the drop is counted. Each ticker is traded at most
>   once.

**M4. Replace the first sentence of the `Eligible` bullet with:**
> - **Eligible**: with n, h from History and ask a as an exact decimal: 10·h >= 9·n AND 100·a·n <= 100·h − 5·n
>   (i.e. hit_rate >= 0.90 and a <= hit_rate − 0.05, evaluated exactly, without floating point).

**M5. Replace the `Entry` / `Exit` bullet's settlement sentence and the `Return` bullet with:**
> - **Entry**: $10 at the ask; contracts = floor(10 / a); fee = ceil_to_cent(0.07 × contracts × a × (1 − a)), once per
>   order (fee multiplier 1.0; configs/parlay.yaml at freeze). **Exit**: hold to settlement. payout = contracts × the
>   per-contract settlement value from Kalshi's settled market record: `result` yes = $1.00, no = $0.00, and a
>   fair-price settlement (player inactive or never takes the court, or game cancelled, per the market's
>   `rules_secondary`) = the dollar value Kalshi pays. Fair-price trades stay in the primary test and are also reported
>   separately (count and mean payout). A trade whose settlement record cannot be retrieved within 7 days of the game is
>   dropped and counted. If dropped trades exceed 5% of all trades, the result is INVALID and not scored.
> - **Return** per trade = payout − contracts × a − fee.

**M6. Replace the `Horizon` section with:**
> Scored once, on the first slate date D at whose end there are >= 100 settled trades over >= 14 distinct dates (all
> of D's trades included). If that is not reached by the last regular-season date of 2026-27, score once on all
> settled trades then. Fewer than 30 trades or fewer than 10 dates → INCONCLUSIVE (underpowered, not a null);
> otherwise apply the primary test. Outcomes: PASS (both bounds > 0), HARM (percentile CI wholly < 0), NULL
> (otherwise).

## SHOULDs (not blocking)
- **S1**: in the report, give the count of distinct (player, stat, game) beside the trade count. Nested lines make
  "100 trades" closer to about 60-70 independent bets.
- **S2**: report, per date, games present in Kalshi but missing from `forward_predictions`, and lines dropped for name
  ambiguity.
- **S3**: the gate counts come from one 11-game opening-night slate. On a 7-game slate expect about 4 a night, so
  roughly 25-30 nights to reach 100 trades. That is still well inside the season, so the gate passes.

## Power / MDE (assumption arithmetic, no outcomes)
Assume a typical eligible line has a ≈ 0.85 and p ≈ 0.90, with a $10 stake: a win nets about +$1.76 and a loss about
−$10.00. The per-trade SD is then about $3.5, and the SE at n = 100 is about $0.35 before clustering. That puts the
80%-power MDE near $1.0 per $10 trade (about +10% ROI), larger once clustering is counted. A NULL means "no edge of
about 10% or more"; it does not mean "no edge". Family: one primary test, no multiplicity; every slice is reported
only.

## Playbook check
1. **Information or capacity?** Neither model nor capacity. The rule asks whether a stale 3-season frequency carries
   information the market price lacks. The prior is weak: the market sees the same history plus 2025-26 and the injury
   report.
2. **Enough data for the class?** At least 60 games per player-stat gives a binomial SE of about 0.04 at a rate of
   0.9. That is about as wide as the 5¢ margin, so many eligible lines are selected noise, a winner's-curse risk.
   This is what the test measures, but expect shrinkage toward the market.
3. **Test validity?** Tip gating is real (scheduled tip, strictly before). DNP handling is fixed by M5. The trade is
   binary at the line, so integer support is not an issue. Power is given above. The event-date leak is fixed by M3.
4. **Lineage**: lower-tail mixture work (`reports/lower_tail/`) and T477, where distribution gains did not reach the
   line. The guard carried forward is the test at the line itself, after fees.
5. **Scored against**: Kalshi ask after taker fee, as realised paper return. This is the right target for the
   question.

## Single most likely failure mode if a PASS is wrong
Mis-attributed markets (wrong game, or a duplicate ticker per player-k) or a DNP fair-price convention chosen after
the fact. M3 and M5 close both.

## Not verified
- I did not run the gate script; it would read season 2025. The 6 / 811 / 2,057 counts are taken as reported. I showed
  that M3 does not change the 10-21 counts, but I did not check how far M1 moves the 6 (playoff rows are about 6% of
  history).
- Whether the existing capture and ingest code retrieves the fair-price settlement value (`nba/kalshi/parse.py` keeps
  `result` and `settled_ts`; I did not confirm a settlement-value field).
- Whether `forward_predictions.tipoff` equals the scheduled tip for every 2026-27 game.

## Drafted rows (not appended)
- DECISIONS: none. HOLDOUT_ACCESS_LOG: the main session should extend row 135 to cover the harness's nightly reads of
  (player_id, team_id, game_id, game_date) for season 2025 (M2) before the harness first runs.
