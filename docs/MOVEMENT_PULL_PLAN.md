# MOVEMENT pull: plan, snapshot times, cost (2026-10-23)

Phase `movement` of `nba.odds history-pull` (Devin, 2026-10-23: "pay for the pull"). Read-only, GET-only,
key from `THE_ODDS_API_KEY` only. Seasons 2023 and 2024 only; 2025 (frozen holdout) is refused in code
(`HoldoutRefused`, also if the config names it). Own ledger
(`data/odds/budget/the_odds_api_movement_ledger.json`), own cap (1,000,000), existing rows untouched
(new snapshot kinds; the write is keyed by game, kind and market). Not on the live path: nothing in
`nba/daily` or `ops/` imports or schedules it.

Questions it makes testable on history: (1) information freshness (is our model's edge largest when news
arrived after the line last moved?) and (2) a Pinnacle shadow model with its update rule (how far and how
fast the line moves after news). Caveat carried from the decision row: reproducing the market earns
nothing; the value is in knowing when to defer fully and in a measured news-to-line lag.

## What is requested

* Markets in one per-event call: player_points, player_rebounds, player_assists, player_threes, h2h,
  spreads, totals (7). Not repeated: t60/t5 already exist for these (all regions).
* Books by `bookmakers=` (not `regions=`). The vendor bills each group of up to 10 books as ONE region
  (docs: "Every group of 10 bookmakers is the equivalent of 1 region"; `bookmakers` takes priority over
  `regions`). Nine books therefore cost the same as one region: pinnacle (the price to shadow),
  betonlineag, lowvig, bovada (sharp-leaning / reduced-juice), betfair_ex_eu (exchange, game lines
  only), draftkings, fanduel, betmgm, betrivers (soft US retail; the stale-line side). All have ~95%+
  coverage in our t60 pulls except betfair_ex_eu / lowvig on props (not offered; not charged).
  Adding a 10th book is free; an 11th doubles the bill.
* Cost per game-snapshot: 10 x 7 x 1 = 70 credits upper bound, plus 1 for the events lookup = 71.
  A market the vendor does not return at that time is not charged, so early snapshots (props are
  often not posted 24 h out) will come in under the estimate.

## Snapshot times (minutes before the real tip) and why

What moves a line: the official injury report (as we store it: 11:00 ET and 17:00 ET stamps for
2023-24; the league also expects an update by 17:00 ET the day before), the inactive list due 90 min
before tip, and beat-reporter / shootaround news in between. Stored report stamps are fixed clock
times while snapshots are tip-relative, so each (game, snapshot) must be joined to the report stamps
by clock time in analysis; for 7-8:30 pm ET tips the 17:00 report falls between T-120 and T-90.

| kind | T- | what it brackets |
|---|---|---|
| m1440 | 24 h | baseline after the previous day's 17:00 report; the line "before today's news". Props are often not yet posted (cheap, possibly empty) |
| m360 | 6 h | after the 11:00 ET report and morning shootaround news for 7-10 pm tips; the 11:00 effect = m360 - m1440 |
| m180 | 3 h | the last price before the 17:00 report for tips up to about 8 pm ET (for later tips 17:00 is T-5 h or earlier, so m180 is already after it) |
| m120 | 2 h | straddles 17:00 for 7 pm tips; mostly redundant with m180 and m90 |
| m90 | 90 min | the inactive-list deadline; the price once the 17:00 report and early scratches are in |
| t60, t5 (existing) | 60, 5 min | reaction to the inactive list; the pre-tip anchor already used everywhere |

The vendor stores 5-minute snapshots, so m90 -> t60 is 6 snapshots apart: reaction speed after the
inactive deadline is measurable at 30-minute resolution, not finer (a finer grid is a separate decision).

## Variants and dry-run credit estimates (both seasons, upper bound, events calls included)

Run by `history-pull --dry-run` (2023: 1,318 games, 2024: 1,320 games, all with real tips). Cost is
linear in the number of kinds.

| variant | kinds | markets | 2023 | 2024 | both |
|---|---|---|---|---|---|
| full | m1440 m360 m180 m120 m90 | props + games | 467,890 | 468,600 | 936,490 |
| **recommended (R4)** | m1440 m360 m180 m90 | props + games | 374,312 | 374,880 | 749,192 |
| R3 (cheaper) | m360 m180 m90 | props + games | 280,734 | 281,160 | 561,894 |
| two kinds | m360 m120 (or m180 m90) | props + games | 187,156 | 187,440 | 374,596 |
| one kind | m1440 | props + games | 93,578 | 93,720 | 187,298 |
| games only, full | all five | h2h spreads totals | 204,290 | 204,600 | 408,890 |
| games only, two kinds | m360 m120 | h2h spreads totals | 81,716 | 81,840 | 163,556 |
| props only, full | all five | 4 props | 270,190 | 270,600 | 540,790 |
| props only, two kinds | m360 m120 | 4 props | 108,076 | 108,240 | 216,316 |

(R4 = 4 x the per-kind cost 93,578 / 93,720.)

Budget context: ledger for the main pull 1,558,192 spent; server remaining 3,441,402, floor 100,000;
the queued 2022 games pull is 161,040. Full movement (936k) plus 2022 leaves about 2.34M above the floor.

## Recommendation

1. Probe first: `--max-games 20` on each of m1440 and m360 (about 20 x 2 x 71 = 2.8k credits) to see
   real prop coverage 24 h and 6 h out and the Pinnacle row counts. If props are mostly absent at m1440,
   rerun m1440 with `--scope games` and keep props at m360 / m180 / m90.
2. Then pull R4 (about 749k upper bound). Drop m120 (adjacent to m180 and m90, straddles the report time).
3. If 749k is too much: R3 (562k) loses the pre-11:00 baseline; games-only R4 is about 327k.
4. This pull does not by itself make freshness testable for a model: it needs the report stamps
   (11:00 / 17:00 only for 2023-24) joined by clock time, and the freshness covariate is "last line move
   before the report" read off these snapshots. That pre-registration is the modeler's.

## Commands Devin runs (key in `.env`, loaded into the shell as THE_ODDS_API_KEY)

Dry runs (no network, no credits):

    python -m nba.odds history-pull --phase movement --season 2023 --dry-run
    python -m nba.odds history-pull --phase movement --season 2024 --dry-run
    python -m nba.odds history-pull --phase movement --season 2024 --dry-run --kinds m1440,m360,m180,m90

Probe (about 2.8k credits):

    python -m nba.odds history-pull --phase movement --season 2024 --kinds m1440,m360 --max-games 20

Recommended pull (resumable; rerun the same command after any stop, finished items cost 0):

    python -m nba.odds history-pull --phase movement --season 2024 --kinds m1440,m360,m180,m90
    python -m nba.odds history-pull --phase movement --season 2023 --kinds m1440,m360,m180,m90

Status: `python -m nba.odds history-status` (movement ledger line first).

Notes: running a `--scope games` or `--scope props` variant uses different state keys and raw paths, so it
never marks the full variant done (and the reverse). `--kinds` keys are per kind, so a later run with more
kinds only buys the missing ones.
