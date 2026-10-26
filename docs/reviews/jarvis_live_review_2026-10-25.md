# Adversary review: JARVIS in-game signals, pre-merge (2026-10-25)

Branch `agent/market-jarvis-live` at `1d87102` (from main `ae579a5`). Scope: `nba/jarvis/ingame.py`,
`nba/jarvis/ingame_run.py`, the `ingame` block of `configs/jarvis.yaml`, small edits to `book.py`, `config.py`,
`runner.py` and `cli.py` (the `ingame-replay` command), plus tests and fixtures. Signals: BENCHED, BENCH WINDOW,
FOUL TROUBLE and STARTERS BACK, read from the live capture's ESPN summaries and Kalshi prop ladders.

The review ran in a scratch detached worktree at `1d87102`, with `data/` symlinked to the main checkout (`ls -la`:
`data -> /Users/devin/Downloads/nba-prediction/data`). No network, no season 2025, no DuckDB write connection, and no
betting outcome computed. The only "outcome" read is whether a flagged starter's ESPN minutes rose in a later capture
file of the same game, which is the replay's own data. Attack scripts live in the scratchpad (`jlive_attack/bench.py`,
`bench_strict.py`, `variants.py`). The 10-25 capture was still being written during the review, so its counts can
move by one between runs (74 vs 75).

Verdict: BLOCKED

(In the caller's terms this is **CHANGES_REQUIRED**. The plumbing holds: no look-ahead, read-only, per-player dedupe,
failure isolation, `live_none` at $1 inside the cap, no CLV for live entries, and ESPN text as inert data. The BENCHED
signal does not hold. On every dense game captured so far, **35 of 75 "likely done" starters (47%) played again**.
The post pairs each one with "His overs ... are dead" and "Kalshi YES ... (should be near 0) ... (still priced)",
which is an edge claim: "sell this, it is mispriced".)

## Top finding

At 23:54Z, the replay of tonight's IND@BKN posts:

```
Pascal Siakam likely done (17 min, frozen; margin 26). His overs above 18 pts / 5 ast / 5 reb are dead.
  Kalshi YES above his number, bid/ask (should be near 0): 20+ pts 50c/66c (still priced); 6+ reb 49c/60c (still priced) ...
```

Siakam played again (ESPN MIN rose after Q4 11:25), and so did Haliburton (flagged at Q4 11:05 with "15+ pts 40c/49c
(still priced)", MIN rose by Q4 9:06). Kalshi's 50c on Siakam 20+ was the market saying he would return. JARVIS told
Devin the opposite and labelled the market wrong. That is a stated edge built on a signal that is wrong about half the
time.

## Checks run (exit codes shown; unpiped)

Python: `/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, with `PYTHONPATH=<scratch worktree>`.

| # | Check | Command | n | Result | Pass/fail |
|---|---|---|---|---|---|
| V1 | Lint | `ruff check nba/jarvis tests/jarvis` | 39 files | exit 0 | pass |
| V2 | Format | `ruff format --check nba/jarvis tests/jarvis` | 39 files | exit 0 | pass |
| V3 | Types | `mypy nba/jarvis` | 25 files | exit 0 | pass |
| V4 | Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/test_layering.py` | 172 | 172 passed, exit 0 | pass |
| G1 | No network or writes in the new modules | `grep -n -E "httpx\|requests\|urllib\|socket\|\.post\(\|subprocess\|duckdb\.connect\|write_text\|unlink" nba/jarvis/ingame.py nba/jarvis/ingame_run.py` | 2 files | no match (exit 1) | pass |
| R1 | What ESPN `active` means | `bench.py` over all captured in-progress snapshots | 2,589 team-snapshots | exactly 5 active in 2,578; 6 in 6; 3 in 2; 0 in 1. It is the on-court flag, with rare stale states | pass (as an on-court proxy) |
| R2 | **False benches, as built** | `python bench_strict.py <wt> 2026-10-21 .. 2026-10-25`. Clock walked every 2 min; only files `<= t` are read for detection; "returned" = ESPN MIN higher in any LATER file | 75 BENCHED signals, 12 games | **35 returned (47%), game-clustered 95% CI [27.7%, 66.2%]** (4,000 resamples, seed 20261025). Q3 window: 24 of 39 (62%, CI [36.4%, 88.6%], 8 games). Q4/OT: 11 of 36 (31%, CI [11.1%, 51.5%], 12 games) | **FAIL (blocker L1)** |
| R3 | Rule variants on the same data | `python variants.py <wt>` | see table | below | informs the fix |
| R4 | The builder's replay | `python -m nba.jarvis ingame-replay --date 2026-10-25 --from 2026-10-25T23:00:00 --to 2026-10-26T02:00:00 --game IND@BKN` | 1 game | 4 posts (BENCHED with 8 starters at Q3 2:53, WINDOW, BENCHED Haliburton Q4 11:05, BENCHED Nembhard Q4 5:44). The builder saw 3 earlier, while the capture was still growing | reproduces L1 and L2 |
| R5 | File-name clock | `date -u` vs the newest `espn_summary` file | 1 | `20261026T002339Z` vs now `20261026T002359`: names are UTC fetch times | pass |

R3, the false-bench rate by rule (same 12 games; **in-sample: the variants were chosen after seeing this data**):

| Rule | Signals | Games | Returned later |
|---|---|---|---|
| As built: Q3 under 3:00 or Q4; frozen 180 s game clock; margin >= 15 | 75 | 12 | 35 (47%) |
| Q4 only; frozen 180 s; margin >= 15 | 67 | 12 | 22 (33%) |
| Q4 only; frozen 360 s; margin >= 15 | 14 | 2 | 0 |
| **Q4 under 6:00; frozen 180 s; margin >= 15** | 46 | 9 | **1 (2%)** |
| Q4 under 6:00; frozen 180 s; margin >= 20 | 33 | 5 | 0 |

The six standard checks: (1) shuffled labels and (6) integer support do not apply, because nothing is fitted or
priced. (2) Planted future: R5 and the code read below; there is no look-ahead. (3) Missingness: `minutes is None`
breaks a frozen run, which is conservative. (4) Knockout: n/a. (5) Replication: R2 covers 5 slate dates and 12 games;
the Q3 failure appears on every date that has a Q3 signal.

## Findings, severity-ranked (each with line and fix)

### Blockers

**L1. "Likely done" is wrong about half the time.** Lines: `ingame.py:366-369` (`late_and_lopsided` allows Q3 under
3:00) and `ingame.py:406-418` (frozen 180 s of game clock plus `active == false`). The `active` requirement does what
it says: ESPN marks the 5 players on the floor. But a starter resting a normal stint is also off the floor with frozen
minutes, which is the end-of-Q3 / start-of-Q4 rotation. A timeout or free-throw stoppage cannot fake the freeze by
itself: MIN must hold over 180 s of game clock. A stale ESPN box could, but the larger problem is ordinary rest.
3:00 of game clock is not enough. Two conditions are needed: time running out, and the player still off the floor.
*Fix:*
- (a) In `late_and_lopsided`, require `snap.period >= 4 and snap.clock_s <= float(ig["benched_max_clock_s"])`, with a
  new config key `benched_max_clock_s: 360`. Drop the Q3 branch. On this data: 46 signals, 1 returned (2%),
  in-sample.
- (b) Freeze the new rule in config with its in-sample number, and count forward returns in the morning recap
  ("BENCHED n, returned k") so the rule is judged on games it has not seen.
- (c) Add a correction post. If a player keyed under `ingame_benched` later shows higher MIN or `active` true, post
  once (`kind ingame_returned`, key `gid:espn_id`): "<name> is back on the floor (posted as likely done at <when>)".
- (d) Word it as "may be done", never "are dead".
- (e) Tests: Siakam at Q3 2:53 is not flagged; Haliburton at Q4 11:05 is not flagged; a player flagged and then
  returning gets one correction post.

**L2. BENCHED and FOUL posts imply an edge.** Lines:
- `ingame_run.py:62` ("His overs above ... are dead").
- `ingame_run.py:68-72`: "(still priced)" when bid >= 5c, and "(should be near 0)". This tells Devin the YES is
  mispriced, i.e. sell YES / buy NO.
- `ingame_run.py:149`: "His backup X's overs are up", a directional claim on a listed price.

The closing line "A fact about the game, not an edge" does not undo a sentence that names the side to take. The
Siakam and Haliburton cases above are the failure.
*Fix:*
- (a) In `_player_benched`, drop the price line or print `Kalshi <stat> ladder now: <threshold>+ bid/ask` with no
  annotation. Delete "(should be near 0)" and "(still priced)".
- (b) Replace "are dead" with "may be done for the night (benched <m> min of game clock)".
- (c) In `foul_lines`, use "<backup> may play more minutes" and list the backup's ladder without "up".
- (d) Test: no in-game post contains `still priced`, `should be near`, `dead`, ` up.`, `edge` (except "not an edge"),
  `value` or `mispriced`.

### Holds (with minor notes)

- **No look-ahead.** `load_snapshots` and `load_snapshots_from` skip files with `ts > until` (`ingame.py:217, 241`).
  `kalshi_live` takes the newest file `<= until` (`:286`). `kalshi_fallback` reads `ts <= until`. File names are
  UTC fetch times (R5). The replay walks `rt.clock` forward and never reads past it. Test
  `test_a_stale_feed_is_silent_and_the_future_is_never_read`.
- **BENCH WINDOW bet stays `live_none` at $1 and inside the cap.**
  - `_bet` (`ingame_run.py:169-195`) sizes from `ladder["none"]` with `none_max_contracts`, against
    `min(nightly - real - other, nightly - reserve - other)`.
  - `live_none` is counted in `other` (`book.py:147`), so it cannot touch the strong reserve.
  - It is excluded from the kill switch (`KILL_LABELS`) and from weights (`weights.py:64`). The text says
    "label none: entertainment, EV unknown". Test `test_bench_window_names_one_entertainment_bet_with_its_payout`.
  - Minor: `_bet` prices every stat with series `KXNBAPTS`; pass the market's own series. With n = 0 a play with
    stake $0 is still recorded; skip it.
- **No pre-tip CLV for live entries.** `score_book` sets `cq = None` for `edge_label LIKE 'live_%'` (`book.py:558-561`).
  Test `test_live_plays_never_get_a_pre_tip_clv`.
- **ESPN text as data.**
  - `clean_name` keeps letters, digits and ` .'-`, capped at 40 characters. There is no LLM in this path.
  - Discord `allowed_mentions` is empty, so "@everyone" cannot ping.
  - Names match exactly after `norm_name` (accents, case, punctuation and Jr/Sr/II removed). An unmatched name is
    skipped, never guessed. Test `test_injected_text_in_a_name_cannot_change_a_decision`.
  - Team comes from the ESPN box group the player sits in, so a player cannot land on the wrong team. Kalshi markets
    are filtered to the game's event ticker.
  - Minor: a name that differs between venues (e.g. "Nic" vs "Nicolas") silently gets no prices. Count unmatched
    names in the post's notes.
- **Dedupe.** BENCHED and FOUL post once per (game, player); WINDOW and BACK once per game. Keys go in `sends` (so
  a restart cannot repeat them) and in the in-process `ingame_seen`. All starters newly flagged in one tick are
  grouped into one post (8 in R4, split as 1/2 and 2/2 by the length limit).
  - Minor: `ingame_run.py:288-289` adds keys to `seen` even when the post failed. In long-running `watch` mode
    (not the launchd `--once` mode), a failed post is never retried. Add keys only when `res["ok"]`.
- **Failure isolation.** `runner._tick` wraps `run_ingame` in `try/except Exception` and runs it last, after the
  pre-tip work.
  - Minor: it re-raises `duckdb.IOException`, so a busy store in the in-game step reports the whole tick as
    "store busy" even though the pre-tip posts went out. Catch it like the rest.
  - Minor: wrap the per-game body of `run_ingame` so one bad game cannot silence the others.
- **Read-only.** G1: no network or writes in the new modules. `kalshi_fallback` uses `open_ro`. The paper book is
  JARVIS's own file.
- **FOUL TROUBLE trigger:** >= 5 fouls, or >= 4 before Q4, once per starter. That is common in Q3, so expect several
  a night; it is noise, not harm, once L2's wording fix lands.

## Playbook check

- **Model class:** none in `docs/MODEL_PLAYBOOK.md`. This is a rule-based state detector on a live feed. The
  nearest guidance is "most prop error is minutes" and "known-at time is first-class".
- **1. Information or capacity?** Information: in-game minutes and on-court state the pre-tip model cannot see. That
  is exactly why the market already prices it. Kalshi's 50c on Siakam 20+ shows the market expected him back.
- **2. Enough data?** 12 dense games, 75 signals. Enough to show the as-built rule fails (47%, CI 28-66%). Not enough
  to certify the fixed rule: 46 signals and 1 return, chosen on the same games.
- **3. Was the test valid?** The builder's replay and tests check that signals fire at the right times. None checks
  whether a flagged player returned, which is the only validity question for "likely done". R2 is that test.
- **4. Lineage:** the halftime check-ins (same capture, same sinks) and the `none` ladder.
- **5. Scored against:** posts make no price claim by design. L2 is where the wording breaks that design.

## Corrected family and power note

No inferential claim is made. The false-bench rate is descriptive with a game-clustered CI. The recommended rule's 2%
is in-sample and needs forward counting. With about 10 BENCHED signals a night, two weeks would give about 100
forward signals, enough to bound the return rate under about 5% if it stays near 2%.

## The one biggest risk

A false "likely done ... overs are dead ... (still priced)" leads Devin to buy NO (or sell YES) on a starter who is
about to return, during the moments when the market is pricing exactly that return.

## What was not verified

- The live Discord rendering of in-game posts.
- How the capture names files when a fetch is slow (seconds of skew; immaterial at 2-minute ticks).
- Kalshi live-ladder depth (the post shows top-of-book only and makes no fill claim).
- Overtime handling beyond `elapsed_s` arithmetic: no OT game in the capture.

## Recommended changes (not applied by the adversary)

L1 and L2 before merge, with their tests. The minors with the same diff. A diff-only re-check follows.

## Proposed DECISIONS row (draft, not appended)

| 2026-10-25 19:40 | JARVIS in-game signals adversary review CHANGES_REQUIRED (1d87102, docs/reviews/jarvis_live_review_2026-10-25.md): L1 "likely done" wrong for 35/75 flagged starters (47%, game-clustered CI 28-66%, 12 games; Q3 window 24/39) -- require Q4 with <= 6:00 left (in-sample 1/46 returned), word it "may be done", post a correction when a flagged player returns; L2 BENCHED/FOUL posts imply an edge ("overs are dead", "(should be near 0)", "(still priced)", "backup's overs are up") -- remove. Holds: no look-ahead (UTC fetch-time file names, ts <= clock), read-only, per-player dedupe, failure isolation, live_none $1 inside the non-reserved cap, no pre-tip CLV for live entries, ESPN text inert. | Adversary sequence. | n/a |

---

## Re-check (diff only): `git diff 6bd1610 f5581be` (2026-10-25)

Same scratch worktree, moved to `f5581be`, with `data/` symlinked (`ls -la`:
`data -> /Users/devin/Downloads/nba-prediction/data`). No new attacks: the builder's replay was rerun, and one raw
ESPN file was read to check team blocks.

Verdict: BLOCKED

(In the caller's terms: **CHANGES_REQUIRED, one line**. L1, L2 and every minor are closed. The remaining item is the
one the coordinator asked about. A BENCHED post mixes both teams' starters with no team on any name, under a header
that names only the leading team.)

### Verification (unpiped)

| Check | Command | Result |
|---|---|---|
| Lint / format / types | `ruff check nba/jarvis tests/jarvis`; `ruff format --check ...`; `mypy nba/jarvis` | exit 0 / 0 / 0 |
| Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/test_layering.py` | **181 passed**, exit 0 |
| Replay | `python -m nba.jarvis ingame-replay --date 2026-10-25 --from 2026-10-25T22:00:00 --to 2026-10-26T01:30:00 --game IND@BKN` | 3 posts: BENCHED x7 at Q4 5:44 (00:22Z), WINDOW (00:22Z), BENCHED Nesmith at Q4 4:08 (00:26Z). **No Q3 post.** The builder's 4th post (00:34Z) did not fire on this run. The capture was still being written, so its later files may differ; not a finding. |
| Team blocks | read `espn_summary/401909862/20261025T222024Z.json.gz` (`boxscore.players[*].team.abbreviation`, starters) | IND: Siakam, Zubac, Nembhard, Nesmith, Haliburton. BKN: Randle, Porter Jr., Sharpe, Ellis, Demin |

### Each item

- **L1: closed.**
  - `late_and_lopsided` now requires `period >= 4` and `clock_s <= benched_max_clock_s` (360), with the Q3 branch
    removed. The config freezes the in-sample figures (47% for the old rule; 1 of 46 for this one) and says the rule
    is judged forward.
  - The wording is "may be done for the night ... Inference from minutes, not a certainty".
  - The correction post (`returned`) fires once per key, only from a snapshot with `ts > flag ts`, and only when MIN
    rose or the player is back on the floor (`ingame.py` detect).
  - Flags persist in the book (`note_flag`), so a restart keeps them.
  - The morning recap counts flagged vs returned (`posts.py`).
  - Tests: `test_siakam_at_q3_2_53_is_not_flagged_any_more` and
    `test_a_returning_starter_gets_one_correction_and_the_night_is_counted`.
  - Residual (minor): ESPN's `active` flag was stale in 8 of 2,589 team-snapshots (R1), so a rare spurious
    correction is possible. It errs toward caution.
- **L2: closed.** "dead", "(should be near 0)", "(still priced)" and "overs are up" are gone. Prices print as
  `Kalshi ladder now (bid/ask): ...` with no commentary. FOUL now reads "his minutes may be cut, and <backup> may play
  more minutes". BACK reads "may return to the floor". `test_no_in_game_post_contains_edge_or_value_language` covers
  every post type.
- **Minors: closed.**
  - `_bet` prices the fee under the market's own series and never records a $0 play.
  - A failed post is not marked seen, so it is retried.
  - A busy store now skips only the in-game step (the `raise` is gone).
  - Each game runs under its own `try`.
  - Kalshi names that match no ESPN player are listed in BENCHED/WINDOW posts.
- **Team assignment: correct.** Every player's team comes from the ESPN `boxscore.players` block they sit in, and the
  raw file confirms it: Zubac and Nembhard are IND starters; Randle, Porter Jr., Sharpe, Ellis and Demin are BKN
  starters. Grouping both teams' starters in one post is right (a 19-point Q4 lead benches both sides).
- **N3 (required, one line): the combined post is not labelled by team.** The 00:22Z post reads
  `**JARVIS live: BENCHED** IND@BKN Q4 5:44, IND +19`, followed by seven names with no team. The obvious reading is
  that all seven are IND players, but five are BKN. The BENCH WINDOW line "Starters out (Ivica Zubac, Andrew Nembhard,
  Julius Randle, ...)" has the same gap; its reserve lines already show `(IND, on the floor)`.
  *Fix:*
  - In `ingame_run._player_benched`, write `f"{p.name} ({p.team}) may be done ..."`.
  - In `window_lines`, write `", ".join(f"{p.name} ({p.team})" for p in sig.players)`.
  - Optionally order the BENCHED group by team, leader first.
  - Test: a BENCHED post with players from both teams shows each player's team abbreviation.

### Playbook check (re-check)

Unchanged class: a rule-based detector on a live feed. Test validity improved: the flagged-vs-returned forward count is
now built in, which is the validity test the first build lacked. Data: the 1-in-46 is in-sample and is labelled so in
the frozen config comment.

### Proposed DECISIONS row (draft, not appended)

| 2026-10-25 20:10 | JARVIS in-game re-check (f5581be): L1 (Q4 <= 6:00 only, "may be done", one correction post from a later snapshot, forward flagged-vs-returned count in the recap), L2 (no edge language, ban test over every post type) and all minors closed; 181 tests pass; replay IND@BKN: 3 posts, none in Q3. Team assignment verified against the raw ESPN blocks. CHANGES_REQUIRED for one line: BENCHED / WINDOW posts mix both teams' starters without a team tag under a header naming only the leader -- tag each name with its team. | Adversary sequence. | n/a |
