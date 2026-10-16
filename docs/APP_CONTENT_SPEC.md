# parlayhitters.ai content spec (2026-10-11; draft for Devin, nothing is published)

Scope: what the app shows each day, and the social formats built from the same stored rows. Source decisions:
DECISIONS 2026-10-11 ~15:10 (positioning, platforms, ceiling, defaults), docs/BUSINESS_DRAFT_2026-10-11.md (phases, gates),
docs/FAN_KNOWLEDGE.md (craziest parlay, F19, over-pricing), reports/props_analysis/2026-10-11.md, nba/parlay.
No code here. Nothing outward-facing is authorised by this file (non-negotiable 6).

## 0. Rules that apply to every card and post

1. **Source rule.** Every number is read from a stored object: `forward_predictions`, `forward_scores`, `shadow_predictions`,
   `paper_trades`, the ledger, or a committed report. Prose never supplies a number. The assistant's `numguard`
   (`nba/parlay/assistant/numguard.py`) is run over every draft; a number it cannot match to the tool output blocks the draft.
2. **n and CI on every rate.** A hit rate, return or Brier difference prints n, the interval, and what it was clustered on
   (night for parlays and returns, game for game-level scores). No interval, no number: the card prints "not enough to say".
3. **Evaluation seasons.** Examples and model-evaluation claims come from the live forward log, the 2025-26 replay, or
   seasons <= 2024 (2024-25 out-of-fold rows). Never the 2025 holdout for any model-evaluation claim; that season is
   touched only by a logged, pre-registered test.
4. **Wins and misses look the same.** True odds are shown identically after a hit and a miss. A hit is annotated
   "this one hit; it was still a -X% bet" when its EV was negative. No stakes, no sizing, no "units".
5. **Verdict line.** Unless the live log has proven skill, the verdict shown is "keep your money". It is shown as often as it is true.
6. **Footer on every page and post (text, fixed):** "Analysis only, not advice. We do not take bets or sell picks. 21+ (US).
   If gambling is a problem, call 1-800-GAMBLER."
7. **Disclosure line:** app pages: "Generated automatically each night; not individually reviewed." (Devin, 2026-10-15); social posts, which go through the approval queue: "Written by an automated system and reviewed by a person before posting." No AI-vendor name anywhere.
8. **Paper only.** Every ticket on the app is a paper trade. The app never places or routes an order and holds no credential.

## 1. "Parlay of the day"

### 1.1 Definition (exact)
For each slate date, after the pre-tip run, the engine (`gaussian_copula`; same-game legs use the copula, cross-game legs are
treated as independent per the independence check) prices every candidate ticket from legs mapped to a market, using the **ask,
never the mid**, and the verified fee schedule (`kalshi_fee_schedule`, taker, rounded up per order). The card shows:

| Field | Source | Note |
|---|---|---|
| Legs, book, line, price, time stamped | market snapshot used by the engine | stamped before tip; refused after |
| Model probability + 80% interval | `joint` / copula output | raw model; shown even when deferred |
| Book implied probability | product of leg asks (same-game: the book's own combined quote if one exists) | the props report states it is "NOT a tradable price" because no combo contracts are ingested; the card repeats that |
| Fee-aware EV per $1 + interval | `ev.py`, low bound of the 80% interval shown beside the point value | verdict is by the low bound, as in the props report |
| Paper-trade record to date | `paper_trades` (and `shadow_predictions` for the deferred state) | n settled, hit rate, return per $1, night-clustered bootstrap CI, plus the gate counters |
| Gate state | `gate_dict()`: settled rows vs 30, slate dates vs 14, skill vs market > 0 | |

"Highest-EV ticket" is chosen by EV at the **low bound** among tickets of 2 to 5 legs (the range the engine prices today), ties
broken by fewer legs. The card says so. It is Devin's "best parlay the model predicts in the day" with the honest qualifier: best
by the model's own numbers, at the book's prices, after fees.

### 1.2 Three states of the card (the headline is a state, not a mood)
- **A. Gate open and a ticket has positive EV at the low bound.** Title "Model's best ticket". Still carries the record, the
  interval, and "paper trade; the live record is n = X".
- **B. Gate closed (today's real state).** The engine's weight on its own model is 0.00 and it defers to the market, so no
  positive-EV verdict is possible. The card says: "The model defers to the market until it has a track record (needs >= 30 settled,
  >= 14 slate dates, positive skill; now n = X over Y dates)." The highest-EV ticket is still shown, under a grey label
  **HYPOTHESIS, UNPROVEN**, with raw model columns. Verdict: keep your money.
- **C. No parlay today.** First-class. No priced ticket (no mapped pre-tip legs, no games, or `no_positive_ev_found`). Shows
  the reason in one line, the record, and the verdict.

### 1.3 Filled example (history, state B styling; a hindsight illustration, not a past recommendation)
Source: FAN_KNOWLEDGE "Craziest parlay" entry; OOF model rows x DraftKings T-60 main lines, 2023-24 and 2024-25.
Command behind it: the descriptive query recorded with that entry (rerun by the maintainer; not rerun here).

```
PARLAY OF THE DAY -- 2024-10-23 (historical example)                      [HYPOTHESIS, UNPROVEN]
 1. Grayson Allen  over 1.5 assists    +180
 2. Tari Eason     over 0.5 assists    +145
 3. Aldama         under 5.5 rebounds
 4. Jackson-Davis  under 5.5 rebounds
 5. Duren          under 13.5 points
 Pays 62x if all five hit.
 Model probability: 10%     Book implied: 1.2%
 EV per $1 after fees: not stored for this ticket (historical, pre-engine)
 Result: all five hit.   This one hit; the model's edge was never proven.
 Record (this model-leaned-legs strategy, 419 nights): five of five hit on 5.5% of nights (book 3.5%),
   typical payout 18.7x, return per $1 = 1.15, night-bootstrap CI [0.71, 1.65] -- indistinguishable from break-even.
 Verdict: keep your money.
 Footnote: the model's own legs hit 55.9% (n = 72,321 legs where it leaned >= 55%; book implied 53.3%; the model
   claimed 62.7%), so it was over-confident; the 62x night is the best of 419, chosen after the fact.
```
The footnote is not optional: a card showing the winner without it fails review. The per-leg model probabilities are not in the
FAN_KNOWLEDGE entry; the card prints them only if the maintainer's query supplies them (open item).

### 1.4 Empty-day card, 2026-10-11 (real)
Source: reports/props_analysis/2026-10-11.md (as of 2026-10-11T19:20 UTC).
```
NO PARLAY TODAY -- Sunday 2026-10-11
 Why: no win-probability forecasts stored for today (0 games with forward predictions); 210 open Kalshi markets read,
 0 mapped to a game on the slate, 0 contracts priced on both sides.
 Verdict: no_positive_ev_found (0 of 0 contracts positive at the low bound).
 Model weight: 0.00 -- the engine defers to the market until the gate opens.
 Record: no settled shadow history (n = 0). Gate: >= 30 settled over >= 14 slate dates; open = False.
 Keep your money. The live season's log starts building on opening night.
```

### 1.5 Companion card: "Craziest ticket that would have hit last night" (hindsight)
Definition: among the previous night's model-leaned legs (model >= 55% to a side, the FAN_KNOWLEDGE definition) that all hit,
the combination with the longest posted odds at the pre-tip snapshot. Always titled "In hindsight", grey, with:
the legs and prices, the payout, that this was selected after the results, the strategy's honest record from 1.3 (n nights,
return, CI), and the same footnote shape. It never says "the model predicted it" unless the legs were stamped before tip in
`forward_predictions`; otherwise "the model leaned this way". If no such ticket exists, the card is omitted, not padded.

### 1.6 Companion card: "Your slips vs the model" (per user, after upload)
For each uploader: n slips and n legs parsed; their hit rate against the book's implied probability for the same legs and
against our model's probability where we have a stored forecast; return per $1 with a leg-clustered CI (game-clustered when a
slip has same-game legs); and one named hit and one named miss from their own slips. Rules: shows "too few slips to say" below
n = 20 legs per cut; no cross-user comparison, no leaderboard; the extracted fields are kept, images deleted (DECISIONS
2026-10-11 ~15:10); no advice, no "you should". Caveat printed on the card: the model is unproven, so "vs the model" is
a comparison, not a verdict on your bets. The expected finding, from our own descriptive work (overs priced 1-3 points high on
respected names; FAN_KNOWLEDGE) is a hypothesis for BETTOR_FLOW, not something the card asserts.

## 2. Content formats (X, Discord; TikTok and Instagram as text-first scripts)

Common to all: drafted by the content agent from a named stored source; numerically checked by `numguard`; footer (0.6) and
disclosure (0.7) on every one; written to the approval queue (section 4). Length limits: X 280 characters per post (a thread of
up to 5 allowed, footer on the last post and the age statement within the first screen of any long form); Discord one message
<= 1,800 characters (embed allowed, same limits); TikTok/Instagram script <= 110 spoken words (about 45 seconds) plus an on-screen
caption <= 150 characters, the footer in the caption. Images are generated from the same rows (inline SVG from the explainer).

| # | Format | Cadence | Data source (never prose) | Must carry | Skipped when |
|---|---|---|---|---|---|
| F1 | Nightly miss explained | daily in season | explainer page for a game (`forward_predictions` + `forward_scores`) | one named hit and one named miss (player, predicted, actual, one-line honest why), n of player-games scored that night, how the model scored (CRPS or log loss, game-clustered CI), the command to check | fewer than 5 scored games |
| F2 | Weekly calibration card | weekly | `forward_scores` + ledger | reliability by bin, n per bin, Brier with date-clustered CI, versus the market's Brier when priced | fewer than 14 slate dates of live data (then say "building") |
| F3 | No bet tonight | when state C | props analysis report headline | the reason, `no_positive_ev_found`, record n and gate counters, "keep your money" | never skipped; this is the default post |
| F4 | Market-vs-model scoreboard | weekly (live page updates nightly) | `forward_scores` model vs market price | n games, Brier difference model minus market with CI (2025-26 replay: the market was ahead, by 0.0064 Brier), who is ahead, said plainly | n < 30 games |
| F5 | Craziest parlay with its footnote | when a ticket qualifies (hindsight) | 1.5 definition | legs, payout, "selected after results", strategy record n nights, return, CI, model's legs hit rate vs claimed | no qualifying ticket |
| F6 | What the slips say | monthly, aggregate | uploaded-slip table | **only with n >= 50 distinct uploaders** (and total legs shown); hit rate vs implied with uploader- or game-clustered CI; who uploads is not who bets | fewer than 50 uploaders; the post says nothing instead |

Responsible-gambling line: the footer in 0.6, plus for F2/F4/F5/F6 the sentence "Past results do not predict what comes next."
Age statement: "21+ (US)". Both appear in the post body or caption, not only a profile bio.

### 2.1 Worked examples from real stored data (descriptive, all pre-2025 holdout; checkable)

**F5, craziest parlay (X thread, 3 posts).**
1/ "In hindsight: on 2024-10-23 five legs our model leaned toward all hit and paid 62x (Allen over 1.5 ast, Eason over 0.5 ast,
Aldama u5.5 reb, Jackson-Davis u5.5 reb, Duren u13.5 pts). The model gave it 10%; the book implied 1.2%."
2/ "The catch: it is the best of 419 nights, picked after. Betting the model's five most confident legs every night returned
1.15 per $1, CI [0.71, 1.65]. That is break-even, not a finding. When it leaned >= 55% (n = 72,321 legs) it hit 55.9%; it
claimed 62.7%."
3/ "Keep your money. Analysis only, not advice. 21+. 1-800-GAMBLER. Written by an automated system, reviewed by a person."
Source: FAN_KNOWLEDGE 2026-10-11 craziest-parlay entry.

**F4 / F3 variant, "the market is better at winners" (Discord).** From F19 (2025-26 replay, Kalshi game-winner prices, 207 nights,
1,312 games): the four highest-priced sides all won on 40.5% of nights (62 of 153); the prices multiplied to 37.4%; gap +3.1
points, night-clustered CI [-4.1, +10.5]. After taker fees a rolled four-leg ticket returned about -5% (CI -25% to +15%).
Named example: 2026-03-21, PHX at 0.83 lost to MIL 108-105; sweep: 2026-04-12 (TOR, POR, PHI, DET). Read: the market's
"givens" are priced right; safe legs are a fair lottery ticket minus fees. Source: reports/f19_safest_legs.md.

**F1, nightly miss (shape, to be filled from the first forward game).** Cannot be shown with live data yet (0 forward games).
Template: "Hit: [player], predicted [x] [stat], scored [y] (command). Miss: [player], predicted [x], scored [y]; why: [injury /
blowout / role change / variance]. Tonight n = [N] player-games, CRPS [value] [CI], clustered on game." The first real one is
generated from the explainer on the first scored game and goes through the dry run.

**F3, no bet tonight.** The 1.4 card, condensed to 280 characters: "No parlay today. 0 games with stored forecasts, 0 priced
contracts. Verdict: no_positive_ev_found. The model defers to the market until it has >= 30 settled over >= 14 dates (now n = 0).
Keep your money. Analysis only, not advice. 21+."

F2 and F6 have no worked example: F2 needs live data, F6 needs n >= 50 uploaders; neither exists. A fake one would break the
source rule, so none is given.

## 3. Tone

- Plain words, analogies over equations, the human point first, then the number, then the examples. One "here's the catch" per post.
- Banned: "lock", "guaranteed", "free money", "easy", "can't lose", "hammer", "fade the public" as advice, emoji-as-hype,
  countdown/urgency, any second-person instruction to bet. No "units", stakes, or sizing. Win accuracy near 64% is the honest
  pre-game range; any claim above 75% is a bug and blocks the post.
- Report "about as good as the market" or "worse than the market" exactly as the number reads. Nulls are posted as nulls.
- "Keep your money" is the default verdict until the live log proves skill, and appears as often as it is true (state B/C cards and F3).
- Disclosure: "Written by an automated system and reviewed by a person." Allowed. Not allowed: the name of any AI vendor or model,
  in repo content, post text, alt text, or profile. Claude drafts; the accounts are Devin's.
- Corrections: a factual error is corrected in public with a reply stating the old and new number; the miss is shown as plainly as a hit.

## 4. Approval queue, dry run, kill switch, escalation

**Queue.** Each draft is one file: `data/social/queue/<YYYY-MM-DD>_<format>_<platform>_<seq>.md` (directory does not exist yet;
created by the content agent, git-ignored like other data/). Front matter: `format`, `platform`, `source` (table and row keys or
report path), `numguard: pass|fail`, `created_at`, `status: pending`. Line 1 below the front matter is the **one-line summary**
("F3 | X | no parlay today, n = 0, keep your money"). Then the exact post text, then the check command. Devin answers with one word,
by editing `status:` or replying to the daily briefing: `post` or `skip` (optionally `skip: reason`). Posted and skipped files move to
`data/social/posted/` and `data/social/skipped/` with the timestamp; `skip` reasons feed the tone rules. A pending draft older than
its event window (a night's miss after 24 h) expires to `skipped/` unseen; it is never auto-posted.

**Daily briefing.** The morning briefing's ACTION NEEDED lists the count of pending drafts and their one-line summaries; a few
minutes a day is the budget.

**20-post dry run (BUSINESS_DRAFT phase 2 gate).** For the first 20 real drafts nothing posts without `post`. Gate to leave the
dry run, per format: zero factual errors in those 20 posts (every number traced to a row), `numguard` pass on all, calibration card
agreeing with the ledger, no banned phrase. A format that fails stays on approval. Opening the gate for a format, and scheduling,
are Devin's decisions; each is a DECISIONS row. Per-platform terms are checked before the first post (DECISIONS 2026-10-11 ~15:10).

**Kill switch.** The file `data/social/KILL` (presence only; content ignored). The posting step checks for it immediately before
every API call and again before every queue read; if present it posts nothing, leaves the queue untouched, and writes a line to
`data/ops/ALERTS.md`. Devin or the watchdog creates it; removing it resumes. The watchdog also creates it on: a numguard failure on
anything already posted, a platform error spike, or any posted item not traceable to a queue file. A bypass of the switch is reported
as a skipped non-negotiable.

**Customer service escalation.** Claude answers routine questions (how to upload, what a card means, deleting data) from a fixed FAQ
that restates these rules. Escalate to Devin, never decide, for: refunds or payments (phase 4), legal or regulatory questions,
anything about a person's gambling harm (reply with the responsible-gambling resources immediately, then notify Devin), privacy or
deletion disputes, press, a claimed factual error in a post, or a request for a pick. The escalation is a file
`data/social/escalations/<date>_<n>.md`: who (email hash, not slip content), what they asked verbatim, what was already told to them,
Claude's proposed reply, the options (reply as drafted / edit / ignore), and the safe default (send the holding reply "we will respond
within two days; nothing was posted or changed"). It is listed under ACTION NEEDED in the next briefing; harm-related ones are
flagged at the top.

## Open items and proposed rows (drafted, not appended)
- DECISIONS draft: "Social content formats F1-F6, queue, kill switch and the 20-post dry run as specified in docs/APP_CONTENT_SPEC.md;
  reverse: Devin or any platform term."
- Build owners (not me): `data/social/` layout and posting step (ops/content agent); the card renderer (explainer); the
  minimum-n and "too few to say" thresholds (20 legs per cut, 50 uploaders) are my proposals, to be fixed before any slip data exists.
