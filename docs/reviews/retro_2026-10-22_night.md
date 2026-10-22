# Retro: 2026-10-21 evening to 2026-10-22 ~02:00 CDT

**Recommended / done / held up**
- Hardening workflow (9 items) launched, then stopped by Devin ("stop"); partial work left in worktrees wt_h1..wt_h9 (lock integrity, timeouts, export gate commit on agent/steward-export-gate-semantics unpushed). Not merged. Pick up or discard next session.
- Live betting support (Devin's money, his decisions): five combos, about +$8.3. What held up: selling when the offer beat an informed estimate of the remaining chance; holding cheap positions with upside. What did not: Claude's "nearly dead" call on the Suns legs (blowout benching is a Q4 effect, confirmed later by data); Claude's options watcher was too trigger-happy (memory live-betting-calm-calls).
- Matchup line closed: T505, T506 stopped at gates; T507 (Devin's override) ran NULL (+3.84 floor units, every stat worse).
- T508 in-game line model: gate, two pre-freeze reviews, frozen, PASS 8x the floor, post-result attack WOUNDED (league-rate baseline; fair player-aware baseline leaves ~0.0025).
- Capture + paper trader + playbook arm built; shakedown 10-22 by hand.

**Rules to add / change**
- Added: information-matched baselines (BEST_PRACTICES, T508).
- Add: every eval gets a synthetic-outcome dryrun on real frames before the real run (T507 crashed on a Series/Index bug after loading outcomes; T508's dryrun caught dtype and join bugs first).
- Add: canonical row order for every DB frame feeding a frozen rule (T507 A5-3, mu order changed results in the 5th decimal).
- Process note: agents twice queried or hashed outcome-adjacent data pre-freeze (T505 stray 2025 count, disclosed; T508 finals hash, kept as a pin). Keep the disclosure habit.

**Calibration of priors tonight**: gate priors good; effect-size priors poor (T507 expected -0.15, got +3.84; T508 expected modest, got 8x then 94% baseline artifact). Price the fitted layer's null cost (dryrun) before stating an expected effect.

**Session length**: this session passed one compaction and ~28 h of work. Start a fresh session next; handoff is docs/NEXT_SESSION.md.
