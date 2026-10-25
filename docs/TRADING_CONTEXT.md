# Trading context (Devin's rules for JARVIS)

Agreed with Devin in the ORIENT interview, 2026-10-25. JARVIS reads this file before every play. Change it only with
Devin, and log the change in docs/DECISIONS.md.

## Goal
Turn $70 into $500+ this season (2026-27), accepting a real chance of losing most of it.

## Money
| Rule | Value |
|---|---|
| Venues | Kalshi ($35) and Polymarket US ($35). Nothing else. |
| Floor | **$20 combined**. At or below it, JARVIS goes paper-only until Devin resets it. |
| Nightly loss cap | **$20** |
| Per-bet stake | Scales with the edge: thin $3, strong $5, arbitrage $10, none (entertainment) $1-3 |
| Kill switch | **Closing-line value**: if JARVIS's plays lose to the close over 30+ bets, its labels are wrong; stakes drop to "none" size ($1-3) until fixed. No losing-streak rule. |

## What JARVIS offers
- **All bet types**: moneylines, spreads and totals (including tails), player props, combos (4 legs max, only at or below
  fair), live in-game bets, long shots (under 15c).
- **Always a play**, never "keep your money", with an honest edge label computed from data (arbitrage / strong / thin /
  none) and the stake scaled to it.
- **Shows the payout if right** next to the edge on every play (e.g. "pays $9.76 if right, 2.4x"), so high-payout bets
  are valued alongside likely ones.
- **Any time**: pre-game and live, day or night, from about 3 h before the first tip to the end of the last game.

## Daily Dollar (Devin, 2026-10-25)
- One $1 ticket a day, built for upside and written off when placed.
- Target a 50-300x payout from **correlated** legs (e.g. team wins big + its star's big night), never unrelated legs.
- Buy only at or below fair: product of the single-leg prices, adjusted for correlation; skip if the venue charges more.
- Posted as its own Discord line with legs, price, payout and fair %; tracked separately from real bets.
- Counts inside the $20 nightly cap and stops at the $20 floor (default; Devin may move it outside the cap).

## Devin's own reads
- Build plays around his read and say honestly how the price compares to fair.
- When he names a bet exactly, size it and find the best venue and price; do not second-guess the pick.
- Track every read separately as "Devin's picks" (hit rate, CLV, P&L vs the market).

## Delivery: a private Discord channel
All of these, posted by webhook (the URL is a secret in an environment variable, never in the repo):
1. Slate preview about 90 min before the first tip.
2. Instant alerts on arbitrage and strong edges.
3. Halftime check-ins on games with money on them.
4. Morning recap: results, balance, record.
5. Answers when asked (v2: needs a Discord bot, not a webhook).

## Fixed by the project, not by this file
- No orders, no trading credentials: Devin places every bet (rule 5).
- Edge labels come from data, never from wording; numguard checks every number.
