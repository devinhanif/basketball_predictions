# desk_export v1 producer, pre-merge adversary review (2026-11-08)

Branch `agent/market-desk-export` @ 16e788f: `nba/export/desk.py` (770 lines), `tests/export/test_desk.py` (269 lines).
Contract: `docs/contracts/desk_export_v1.md` (main, ae70afe). Real outputs read: `data/export_private/desk.json`,
`desk_shared.json` (built 2026-11-08 18:32, data_as_of 2026-11-09T00:31:15Z). Every DuckDB opened `read_only=True`;
the lock-hold timing ran on file copies in scratch. No season 2025 data was touched; this review involves no model.

**Overall: BROKEN.** The shared cut leaks exact stake and P&L for most daily_dollar bundles through `fee_cents`,
and it carries `/Users/devin/...` paths. Both fixes are small. The numbers are honest and reproduce exactly.
Read-only safety is acceptable, with one major live-path finding.

Verdict: BLOCKED

## Top finding (blocker): money can be rebuilt from `fee_cents` in the shared cut

The daily_dollar producer stores `fee_cents = (per - price) * 100` (`nba/jarvis/dailydollar.py:173`). That is the
order fee divided by the contract count, so it equals `k/n` cents. `desk.py:257` copies it unchanged, and
`shared_cut` (`desk.py:708-720`) does not touch it. On the real `desk_shared.json`, taking the smallest n for which
`fee_cents*n` is a whole number and setting `stake = n*prod(leg prices) + n*fee` gives this:

| check | n | result |
|---|---|---|
| daily_dollar bundles with fractional fee_cents | 11 bundles (32 legs) | **7/11 give exact contracts and exact stake_usd** (e.g. 58 contracts, $0.4337; 47, $0.5664; 41, $0.6179). The other 4 come out as n/5 or n/2 and are fixed by the known ~$0.50-0.70 target. P&L follows: all legs `won=false`, so pnl = -stake. |

Command: scratch script over `desk_shared.json` and `desk.json` (inline in the session; exit 0).
**Fix:** in `shared_cut`, null `fee_cents` when `edge_label == "daily_dollar"`, or in every case. A cleaner option is
to export the one-contract fee (`E.fee_per_contract_raw`) in both cuts, so that no field depends on n.

## Check table

| # | check | command / evidence | n | result | pass |
|---|---|---|---|---|---|
| P1 | named money fields null in shared | structural diff private vs shared | 137 plays, 130 results, 12 daily | only `contracts`, `stake_usd`, `result.pnl_usd`, `daily.pnl_usd`, `balance`, `privacy` differ | pass |
| P2 | `$` in free text (reason, explanation, leg_desc, titles, notes) | regex `\$\s?\d` on shared file | 83 hits | all 83 are the literal `per $1` in `reason` (a unit, not an amount); no `\d+ contracts`, no "staked", "profit", "bonus", "cap" | pass |
| P3 | derivable money | fee_cents attack above | 11 bundles | exact stake recovered for 7 | **FAIL (blocker)** |
| P4 | account-like strings | regex `/Users`, recipient/phone/iMessage, env/API key | 6 / 0 / 0 | `sources.*` = `/Users/devin/Downloads/nba-prediction/data/jarvis/...` (`desk.py:617`, `:630`; `book_p = root / PAPER_BOOK` at `:596` is absolute) | **FAIL (major)** |
| P5 | balance inferable | `status.model_review = ks_on or current_usd <= floor_usd` (`desk.py:653`, `:691`) is kept in shared | 1 bit | silent today because the kill switch is on; once it clears, `model_review=true` reveals "balance at or below floor" | minor |
| R1 | DBs read-only, bounded retry | `desk.py:92` `read_only=True`; 10 tries x 3 s = 30 s max (`:38-39`) | - | ok | pass |
| R2 | no nba.jarvis / nba.daily / heavy.lock / nba.duckdb | grep of desk.py | - | none imported or opened | pass |
| R3 | writes only in out dir, atomic, old kept on failure | `write_atomic` `:723-739`; build runs before any write; test `:205-224` | - | ok. If the second `os.replace` fails, the pair is left mismatched (new private, old shared). Minor | pass |
| R4 | effect on the live writer | timed on copies: book read lock held **0.31 s**, halftime 0.005 s | 1 run | JARVIS write handles (`book.py:102`) have no retry. A tick that collides raises IOException and `run_tick` skips the whole pass (`runner.py:285`). ESPN gz parsing runs inside the open book connection (`desk.py:601-619` calls `EspnNames` at `:605`), which makes the hold longer than it needs to be | **major** |
| N1 | CLV summary | independent numpy re-run, read_only on `paper_book.duckdb` | 83 plays, 12 dates | mean **-0.8795c** (exact match); my date-cluster CI (rng differs, 10k reps) **[-1.412, -0.530]** vs reported [-1.431, -0.522]; re-running the branch code gives the reported CI exactly | pass |
| N2 | clustering really by date | `clv_summary` groups on the `slate_date` key (`desk.py:187-193`) and resamples whole groups | 12 clusters | true; 12 clusters is few for a percentile bootstrap (somewhat anti-conservative) | pass |
| N3 | model vs market | `reports/forward_scorecard/2026-11-07.json` `part3.all.brier_diff` | 6812 rows, 133 games | diff +0.009492, CI [0.006449, 0.012522]; sign = model minus market (`scorecard.py:601`); cluster = game_id (`scorecard.py:5`) | pass |
| N4 | record counts | `scores` grouped by status | 130 | settled 128, won 36, lost 92, void 2 | pass |
| M1 | correction links | every `kind=correction` observation resolved | 30 | 30/30 linked, same game, target ts <= correction ts | pass |
| M2 | ESPN names, spot-check 10 | own parse of every snapshot and every stat group for the game | 10 | 10/10 names and matchups match; 0 unresolved ids in 541 observations | pass |
| M3 | `ingame_back` mapping | `desk.py:423-431` vs `ingame.py:455-462`, `ingame_run.py:178-186` | 1 row | JARVIS posts "STARTERS BACK?": the lead shrank, so flagged starters *may* return. The export calls it `kind=correction`, `certainty=correction`, title "flagged players back in the game". That is invented certainty | **FAIL (major)** |
| M4 | `ingame_window` title | `desk.py:438` vs `ingame.py:432-433` (fires on >= 1 frozen starter) | 39 windows | 5/39 have exactly one flagged player, so "Several players' minutes frozen" overstates | minor |
| M5 | foul / benched / halftime certainty | `ingame_run.py:89` says "Inference from minutes, not a certainty", mapped to inference; foul = stored PF count, mapped to reported | 230 / 136 / 86 | consistent | pass |
| T1 | tests fail on a money leak | in-process mutants of `shared_cut` against `test_shared_cut_nulls_money` | 7 mutants | dropping each of the 5 nulling lines: killed 5/5. Planting `"staked $12.40"` in `reason`: **survives**. Planting a `fee_cents = 4/47`: **survives** | partial |
| V | ruff / format / mypy / pytest | `uv run --frozen ...` in the worktree, unpiped | - | 0 / 0 / 0 / 9 passed, exit 0 (`--no-cov`; the default run exits 1 only on the repo-wide coverage gate for a single file) | pass |

## CLV: is it "almost constant at -0.5c"?

No. Only 27/83 (33%) are exactly -0.5. Values run from -4.0 to +3.5 in half-cent steps. The producer's intuition is
still right in substance. Using `clv_cents = (close_mid - entry_ask)*100` (`book.py:607`), the identity
`clv_mid = clv_vs_ask - half_spread_at_close` holds on all 83 rows:

| component | n | mean | 95% CI (date-clustered, seed 20261108, 5000 reps) |
|---|---|---|---|
| clv_mid (reported) | 83 | -0.880c | [-1.43, -0.52] |
| clv_vs_ask (price movement) | 83 | -0.193c | [-0.74, +0.15] |
| half-spread at close | 83 | 0.687c | 65/83 at exactly 0.5c |

What this means for reading CLV (report only; JARVIS not changed): about 80% of the -0.88c is the half-spread
paid by buying at the ask. The part that measures whether the line moved toward the pick cannot be told apart from
zero, and n=83 over 12 dates is underpowered. Even with zero adverse movement, a tight 1c-tick market scores -0.5c.
So the kill switch's "mean CLV < 0" rule really tests "edge > half-spread", not "picks beat the close". That may be
the right rule, but the desk page should say so. Also, CLV is missing on 45/128 settled plays (live_ labels, no
two-sided close), so the 83 are a selected subset.

## Recommended changes (not applied)

1. **Blocker:** `desk.py:708-720` `shared_cut`: null `plays[].fee_cents` for `edge_label == "daily_dollar"`, or for
   every play. Better: emit the one-contract fee in both cuts.
2. **Major:** `desk.py:596`, `:617`, `:630`: write `sources` paths relative to `root`
   (`book_p.relative_to(root)`), or blank `sources` in `shared_cut`.
3. **Major:** `desk.py:423-431`: map `ingame_back` to `kind="rotation"`, `certainty="inference"`, title
   "Lead down to N: flagged starters may return". Keep the `corrects` link out (it is not a correction).
4. **Major (live path):** `desk.py:601-619`: fetch all book rows, close the connection, then resolve ESPN names.
   Better still, follow `nba/jarvis/sources.py:92-115` (snapshot copy plus WAL, then read the copy), so the exporter
   can never collide with a JARVIS write. Schedule it away from tick boundaries.
5. **Minor:** null `status.model_review` in shared, or derive it from the kill switch only (`desk.py:691`).
   `desk.py:438`: "Starter(s) frozen (game-level flag)". `--root` default (`:751`) resolves to the worktree when run
   from one, which silently reads the wrong data.
6. **Tests:** add to `test_desk.py` a shared-cut invariant that walks every string in the shared doc and fails on
   `\$\s?\d+(\.\d+)?` other than `per $1`, on `/Users/`, and on any `fee_cents*k` that is a whole number for some
   k > 1 with fractional fee_cents. Seed the fixture with a daily_dollar row (`fee_cents=4/47`) and a `reason`
   containing a dollar amount, so the test proves it catches both.

## Playbook check

Model class: none (a data export, no fitted model). (1) Information or capacity: n/a. (2) Enough data: CLV n=83
over 12 dates is underpowered for its sign question; the MDE is about the CI half-width, ~0.45c. (3) Test validity:
the CLV benchmark is mid-vs-ask, so the half-spread is built in (see above). (4) Lineage and inherited guards:
JARVIS's read-only convention and the `sources.py` snapshot pattern; this producer adopts the first but not the
second. (5) Scored against: the scorecard compares against the de-vigged market close, copied faithfully.

## Not verified

Polymarket fee semantics (none in daily_dollar here). Whether the claude.ai page bakes every field or a subset.
Behaviour when a `.wal` is present during a read-only open under live writes. Repo-wide pytest (single file only).

## Most likely failure mode if this is merged as is

The friend's page shows per-bundle stakes and losses to the cent, rebuilt from `fee_cents`, plus Devin's local
username. Nothing in the tests would catch it.

## Drafted rows (not appended)

none. This is a pre-merge code review, not a ledger result.

---

## Re-review bdc9f9a (2026-11-08, outputs rebuilt 18:41)

**Verdict: SURVIVES.** Every first-round finding is fixed, and the new tests fail against the old code. Two minor
residuals remain, listed below; neither blocks the merge.

Verdict: APPROVED

| # | attack / check | evidence | n | result | pass |
|---|---|---|---|---|---|
| A1 | fee_cents channel | `shared_cut` nulls `fee_cents` on every play (`desk.py` shared_cut); real shared file | 137 plays | 0 non-null fee_cents | pass |
| A2 | edge -> fee -> n | non-daily_dollar plays: implied fee = fair*100 - price - edge | 83 plays with edge | implied fee is always 1.0c or 2.0c (one-contract fee) for every contract count 0-8, so it carries no n | pass |
| A3 | daily_dollar remnants | edge_cents, model_prob, explanation, clv, close all null; `reason` keeps "product of asks Xc, assumed fair Yc" (per contract) | 11 bundles / 32 legs | `n = floor(B/price)` gives 100/62/64/... against true 58/47/47; n is not recoverable without the private `dd.stake_usd` budget and the sizing code | pass (residual, minor) |
| A4 | other fields | price_cents, fair_prob, model_prob, close_cents, clv_cents, daily (n, n_won, clv_mean), record | - | all per-contract or counts; none recovers contracts, stake, P&L or balance | pass |
| A5 | paths | regex `/Users`, `"/[A-Za-z]` on the shared file | 0 / 0 | sources are relative (`data/jarvis/paper_book.duckdb: ...`); the build now fails on any absolute path | pass |
| A6 | `$` text | regex `\$\s?\d` | 83 | all are "per $1"; no `\d+ contracts`; the 43 "devin" hits are players (Devin Booker, Devin Vassell) | pass |
| A7 | model_review | shared = kill switch only | 1 | True (kill switch on); test proves False when the kill switch is off and the floor is closed | pass |
| R1 | book hold | real file `book_hold_seconds` 0.012; my run on copies 0.022 s (total build 0.50 s) | 1 run | about 15-25x shorter than 0.31 s; the connection closes before `EspnNames` (build: `book.close()` then `_observations_alerts(...)`) | pass |
| M1 | ingame_back remap | real file: `rotation / inference / corrects=None`, title "Lead shrank: flagged starters may return" | 1 | matches `ingame_run.py:178-186`; corrections now 29, all linked | pass |
| M2 | window title | "Starter minutes frozen (game-level flag, one or more players)" | 39 | accurate | pass |
| N | numbers unchanged | CLV n=83, -0.8795c [-1.431, -0.522]; record 128/36-92/2; brier diff +0.009492 | - | identical | pass |
| T | new tests fail on old leaks | scratch `mutants2.py`: new tests run against 16e788f's `shared_cut` / `build` | 6 mutants | derivable-money killed, JSON `$`/path scan killed, absolute-path guard killed, old absolute sources killed, old ingame_back killed, old book-open-during-names killed: **6/6** | pass |
| V | ruff / format / mypy / pytest | `uv run --frozen ...`, unpiped | - | 0 / 0 / 0 / 14 passed, exit 0 (`--no-cov`) | pass |

Residuals (minor, recommend; not blocking):
1. The runtime `$` scrub covers only `plays[].reason/explanation/leg_desc`. `status.kill_switch.note` and the risk
   observation `text` carry `killswitch_log.note`. On a "cleared" row that note is free text supplied by the caller
   (`book.py:835`), e.g. a manual note that mentions a deposit, and it would pass through. Fix: run `_has_amount`
   over every string in `shared_cut` and null the hits. The test scan (`test_shared_json_scan`) already walks every
   string, but only on synthetic data.
2. The daily_dollar `reason` plus the label name plus knowledge of the private budget could still bracket n. That
   needs config and code that are not in the shared file. Accept, or null `reason` on daily_dollar plays in shared.

Still not verified: what the claude.ai bake renders. Whether the CLV half-spread reading (above) gets onto the page
is a desk-copy question, not a code one.
