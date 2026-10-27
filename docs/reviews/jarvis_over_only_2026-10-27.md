# Adversary review: JARVIS player props Over/Yes only + confirmed arbitrage $15 (2026-10-27)

Branch `agent/market-jarvis-over-only`, commit 72e986f, worktree `/Users/devin/Downloads/nba-wt-over-only`
(data/ symlinked to main, read-only). Rule implemented: docs/TRADING_CONTEXT.md and DECISIONS row 2026-10-27 16:22
(b88f719). Player props: Over/Yes only on every venue. Game markets: both sides. Confirmed arbitrage: $10 -> $15,
nightly cap $20 and strong reserve $10 unchanged.

Verdict: APPROVED

Top finding: no path delivers a player-prop No to Devin. On tonight's real slate (2026-10-27, 21:30 UTC clock,
687 tradable prop quotes) the flag removes all 604 prop-No candidates, and with both sides enabled the best
play in one game *is* a prop No ("Jrue Holiday: fewer than 2 threes", thin), so the filter does real work and
is not vacuous. Game-market Nos are still offered (8 of 8). Five minor defects follow; none blocks the merge.
m1 and m3 are two-line fixes and worth making first.

## Scope of the diff

8 files, +93/-7: `configs/jarvis.yaml` (stake 15, `sides.props: ["yes"]`), `nba/jarvis/config.py` (`props_sides`,
`_side`, validation), `nba/jarvis/edge.py` (`side_allowed`; applied in `best_arbitrage`), `nba/jarvis/picks.py`
and `nba/jarvis/slate.py` (guard), and 3 test files. The diff does what the task says and nothing more. It
does not touch `nba/truth/`, ops/ or launchd.

## Path-by-path attack: can a prop No reach Devin?

| Path | Where the side comes from | Result |
|---|---|---|
| Slate preview, alerts (`runner.run_slate`, `_tick`) | `slate.best_per_game` -> `single_candidate` (slate.py:131-133 guard) | blocked; dry run 0 prop-No legs, 0 "fewer than" texts |
| Ladder/cover arbitrage (`edge.best_arbitrage`) | filters `kind == "game_win"` at edge.py:180, so the new guard at :187 is redundant (harmless) | no prop leg can enter |
| Daily Dollar (`dailydollar.py:149`) | side hard-coded `"yes"` | yes-only |
| In-game live plays (`ingame_run._bet`, :207) | side hard-coded `"yes"` | yes-only |
| In-game "benched" post | shows YES bid/ask ladder only, no BUY line | informational. The thesis is an under, but it never tells Devin to buy one |
| Halftime check-in (`posts.py:136`) | re-renders stored `leg_desc` of logged plays, unfiltered | the paper book has 0 prop-No rows ever (17 rows, 10-25..10-26), so nothing can resurface |
| Morning recap | book rows only | same as above |
| Picks (`picks.py:81`) | guard raises PickError | verified on the real slate: prop No rejected, prop Yes and game No loggable |
| `ask` REPL / agent | `best_play` from `plan_slate` (filtered). The `prices` tool returns winners only | the LLM's own prose has no side guard (info, below) |
| Fast-arb `confirmed_quotes` (`nba/markets/fastarb/jarvis_source.py`) | builds `game_win` only (Kalshi KXNBAGAME + Polymarket US winner) | no props. Also not wired: `QUOTE_SOURCES` is empty (slate.py:35) |
| Fast-arb live poller | game markets only (venues.py:60, runner.py:221). Player-stat ladder rungs excluded by `include_player_props: false` (configs/fastarb.yaml:58) | safe today, but held by a separate flag (info m-x) |
| Polymarket quotes (separate token per outcome) | jarvis loader reads the full-game winner only (`polymarket.py`, kind `game_win`). The `no` side of a Polymarket quote is skipped | no Polymarket props exist in JARVIS |
| Kind mislabelling (team totals / spreads / PRA as game or prop) | `sources.load_kalshi_quotes` reads only KXNBAGAME + KXNBAPTS/REB/AST/3PT. KXNBATEAMTOTAL, KXNBATOTAL, KXNBASPREAD, STL/BLK/PRA are never loaded | no mislabel path |
| A "Yes" that is economically an under | 10,939 Kalshi prop markets in the 4 loaded series: 0 titles match under/fewer/less/below or lack "+" | every loaded prop Yes is an "N+" over |
| `exit-check` (cli.py:360) | arithmetic on CLI args (bid, fair, contracts). Prints a sell/don't-sell line, never a BUY | safe to leave unrestricted: it advises on a position Devin already holds and cannot open a new No |

## YAML boolean attack (fail-closed?)

Script: `scratchpad/yaml_attack.py` substitutes the `sides:` block of configs/jarvis.yaml and calls `load_config`.

| `sides` block | Result | Fails closed? |
|---|---|---|
| absent | ('yes',) | yes |
| `props: ["yes"]`, `[yes]`, `[Yes]`, `[on]`, `[true]` | ('yes',) | yes |
| `prop: ["yes","no"]` (typo key) | ('yes',) | yes |
| `props: yes` (bare scalar) | TypeError, load fails | yes (loud, opaque message) |
| `props: "yes"` / `"no"` / `"yes,no"` (string scalar) | ValueError (iterates characters) | yes |
| `props: []`, `["Yes"]`, `["over"]`, `[1]`, `[y]` | ValueError | yes |
| `props:` null / `sides:` null | TypeError / AttributeError, load fails | yes (opaque) |
| **`props: [no]`, `[off]`, `[false]`** | **('no',)**: prop Yes suppressed, prop No offered | **no** (m1) |
| **duplicate `props:` key, last `["yes","no"]`** | **('yes','no')**: PyYAML keeps the last key silently | **no** (m1) |
| `props: [yes, no]` | ('yes','no') | explicit choice; outside the rule |

The common misparse (bare `yes` read as a boolean) is handled correctly. The validator still accepts a No-only
config and anything containing "no", so one wrong word in the YAML opens prop Nos without any error.

## Arbitrage $15 against the caps and reserve

Script: `scratchpad/sizing_attack.py` (real configs/jarvis.yaml, `size_plays`, scratch books). Each row
gives the legs at 46c + 44c, the stake per play, and the night total.

| Scenario | Stakes | Night total | <= $20 cap | <= $15 per bet |
|---|---|---|---|---|
| arb on an empty night | arb 14.95 | 14.95 | pass | pass |
| arb + strong, same pass | 14.95, 4.66 | 19.61 | pass | pass |
| strong $5 earlier, then arb | 14.95 | 19.95 | pass | pass |
| $10 of entertainment earlier, then arb | 9.35 | 19.35 | pass | pass |
| two arbs | 14.95, 4.68 | 19.63 | pass | pass |
| arb, then thin + none | 14.95, 2.59, 0.52 | 18.06 | pass | pass |
| arb recorded, then strong + thin | 4.66, 0.00 | 19.61 | pass | pass |

No stake goes above per_bet_usd (15), per_label (15) or the nightly cap. The reserve logic is unchanged.
Entertainment can never spend the $10 reserve, and an arbitrage may consume it, which is what the reserve is
for. Two practical effects:
- **The $15 is latent.** `QUOTE_SOURCES` is empty (slate.py:35), so the `arbitrage` label cannot be produced
  today. Every cover is `arb_candidate`, sized as thin at $3.
- **On a normal night the preview fills most of the non-reserved pool** (tonight's dry run: 4 plays, $9.61).
  A confirmed arb later that night then sizes at about $10.39, not $15. The raise mostly matters on light
  nights. This is consistent with "inside the $20 nightly cap" and is not a defect. Devin should know that
  $15 is a ceiling, not the typical stake.

## Checks run (all unpiped, exit codes shown)

| Check | Command (worktree, `.venv` synced with `uv sync --extra dev --frozen`) | Result |
|---|---|---|
| tests | `.venv/bin/python -m pytest tests/jarvis tests/markets -q -p no:cacheprovider --no-cov` | 323 passed, exit 0 (with coverage on, exit 1 only from the repo-wide 70% fail-under on a subset run; still 323 passed) |
| ruff | `.venv/bin/ruff check nba/jarvis tests/jarvis` | exit 0 |
| format | `.venv/bin/ruff format --check nba/jarvis tests/jarvis` | exit 0 |
| mypy | `.venv/bin/mypy nba/jarvis` | exit 0, 26 files |
| dry run, slate | `scratchpad/dryrun.py`: scratch config (paper_book -> scratchpad copy; nba_db -> main nba.duckdb opened read-only), recording sender, env={}, no Polymarket, no LLM | exit 0; 10-27 yes-only: 0/687 prop-No candidates, 8 game-No candidates, 4 plays, 0 prop-No legs, 0 "fewer than" in texts. Both sides on: 604 prop-No candidates, 1 prop-No leg in plan |
| dry run, watch ticks | `scratchpad/tick.py` at 21:30, 23:05, 00:30 UTC (alerts, halftime, in-game, recap) | exit 0; 7 texts, 0 "fewer than", 0 "NO contract" |
| picks | `scratchpad/pick_attack.py` | prop No rejected; prop Yes and game No loggable |
| paper book history | read-only copy, `GROUP BY kind, side` | 0 prop-No rows |

10-25 and 10-26 produced no tradable quotes at the 21:30 clock (no games resolved for those dates in this
setup). Only 10-27 exercised the filter on real data.

## Defects (severity, file:line, smallest fix)

- **m1 (minor) `nba/jarvis/config.py:182`**: the validator accepts `('no',)` and any set containing "no". So
  `props: [no]`, `[off]` or `[false]` silently flips the slate to prop-No only, and a duplicate `props:` key
  silently enables both sides. Fix: make the rule enforceable, not just expressible:
  `if "yes" not in cfg.props_sides: raise ValueError("sides.props must include yes")`. Also log a WARNING
  line (or refuse) when `"no" in cfg.props_sides`, since TRADING_CONTEXT forbids it.
- **m2 (minor) `nba/jarvis/config.py:180`**: a null `sides:` or `props:` fails closed, but with
  `AttributeError`/`TypeError` instead of a readable message, and a string scalar is iterated character by
  character. Fix: `sp = (raw.get("sides") or {}).get("props", ["yes"])`, then
  `if not isinstance(sp, list): raise ValueError("sides.props must be a list, e.g. [\"yes\"]")`.
- **m3 (minor) `nba/jarvis/edge.py:130`**: deny-by-exception on kind. Any future prop kind string
  (`"player_prop"`, `"combo"`, a Polymarket prop loader) gets both sides. Fix: allow-list game markets:
  `return kind == "game_win" or side in cfg.props_sides`. The only kinds produced today are `game_win` and
  `prop`, so behaviour is unchanged.
- **m4 (minor, docs) `docs/DAILY_PIPELINE.md:1048`**: still says arbitrage stake `$10`, and the JARVIS section
  never says props are Over/Yes only. Fix: `$15`, plus one line "player props: Over/Yes only (sides.props);
  game markets both sides".
- **m5 (minor, test) `nba/jarvis/picks.py:81`**: the new PickError has no test. Fix: in tests/jarvis, add
  `build_pick(..., ticker=<prop ticker>, side="no")` raising PickError, and the same with `side="yes"`
  succeeding.

Info (no change needed in this branch):
- The fast-arb ladder keeps prop rungs out of alerts only through `include_player_props: false`
  (configs/fastarb.yaml:58), which is independent of `sides.props`. If that flag is ever flipped, a ladder
  arb with a prop-No leg could alert. Recommend asserting prop rungs are never alertable while
  TRADING_CONTEXT says Over-only, in a separate fastarb change.
- The `ask` REPL's LLM prose has no side guard. It could say "the under" from the forecasts tool, although the
  logged play is the filtered Python play. It is a local REPL, not a Discord path.
- `python -m nba.markets.fastarb underdog` can suggest "under" picks from a hand-typed CSV. Underdog is not a
  venue in TRADING_CONTEXT.
- `_no_desc` for props (edge.py:222) is now unreachable on the default config. Keep it for the flag.

## Playbook check

Model class: none. This is a rule-based decision and sizing layer (a side filter plus a stake ladder) on top
of existing forecasts. No model is trained or rescored.
1. Information or capacity: neither. It is a constraint that encodes a venue fact (Devin cannot buy prop Nos).
2. Data enough: n/a. No fit.
3. Test validity: the dry run used real 10-27 quotes and showed the filter is load-bearing (604 -> 0 prop-No
   candidates, and a prop No was a per-game best play with both sides on). That is a valid negative test, not
   an inert one. The sizing scenarios use the production config.
4. Lineage: extends the existing "Polymarket No skipped" pattern (edge.py:185, slate.py:336). It inherits the
   reserve logic of the 2026-10-11 reserve review, unchanged.
5. Scored against: nothing new. Removing prop Nos changes which plays enter the CLV kill switch and label
   record, so the "best of N priced bets" counts shrink. That is more honest, not a scoring change.

## Biggest risk

A hand edit or merge conflict in configs/jarvis.yaml (`[no]`, `[off]`, or a duplicated `props:` key) opens prop
Nos with no error (m1). It is cheap to close.

## Not verified

- Live Discord rendering. No send was made, by design.
- A confirmed arbitrage end to end: unreachable until a source is wired into `QUOTE_SOURCES`.
- In-game paths on a live feed. `run_tick` ran at three clocks, but I did not confirm whether any in-game
  window signal fired. The side is hard-coded yes in code (ingame_run.py:207).
- The full test suite. Only tests/jarvis and tests/markets were run.

## Recommended changes (not applied)

Apply m1 and m3 (two lines) and m5 (one test) before merge; m2 and m4 can ride along. Devin's go is still
required before install (the live path: configs/ and nba/jarvis run under launchd).

Drafted DECISIONS row (not appended):
`| 2026-10-27 | Adversary APPROVED JARVIS props Over/Yes-only + arbitrage $15 (72e986f; docs/reviews/jarvis_over_only_2026-10-27.md): no prop-No path found; flag removes 604/604 prop-No candidates on the 10-27 slate; caps hold in 7 sizing scenarios. Minors m1-m5 (config accepts No-only/duplicate key; kind deny-list; stale $10 doc; missing pick test). $15 is latent until QUOTE_SOURCES is wired and is ~$10.4 on a night whose preview fills the pool. | Reverse if any prop No reaches Discord or a stake exceeds the caps. |`
