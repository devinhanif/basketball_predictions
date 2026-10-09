# Reading log

Where papers, repos and outside ideas enter the project. One note per item, written by the
research-scout agent or by hand. Ideas are welcome and plentiful; claims are rare and earned.

## How an idea travels

1. **Note** — `docs/research/<slug>_<date>.md`: the claim as stated; credibility (split method,
   leakage risk, same-game inputs, sample size, venue); what this repo has already tested
   (`docs/TEST_LEDGER.md`); the *new information* it would add (architecture-only ideas have a
   poor record here); data and compute needs; verdict PURSUE / PARK / SKIP.
2. **Explore** — scratch scripts, fast and loose, in `data/scratch/` or a notebook. Nothing from
   here is a result. This is where the fun is.
3. **Freeze** — if exploration looks promising, a pre-registration doc (rule, floor, CI, seasons,
   slices) is committed *before* the real run. Claude drafts it; Devin confirms it.
4. **Run, record, attack** — one run, a ledger row (win or null), a red team if it won.

The wall between 2 and 3 is the whole method. Anything that crosses it without a frozen rule is
exploration, however good it looks.

## Log

| Date | Item | Verdict | Note |
|---|---|---|---|
| 2026-10-09 | Historical odds providers (The Odds API, Kalshi, others) | PURSUE | [historical_odds_2026-10-09.md](historical_odds_2026-10-09.md) |
| 2026-10-08 | Stacked-ensemble, GCN+RF, NCAA LSTM/Transformer papers (pasted) | tested → not kept | see T-rows for seq_props, joint_game_set, pbp_gpt, winprob family |

## Questions we would like papers for

- Market microstructure of player props: where and when prop lines are stale (late scratches,
  thin books, back-to-backs).
- Minutes: how coaches allocate minutes under blowout, foul trouble and rest; any public models.
- Usage redistribution when a starter is out (causal, not correlational).
- Conformal prediction with per-player (per-group) coverage guarantees.
- Betting-policy evaluation: stake sizing, correlated losses, risk of ruin at small stakes.
- Honest accuracy ranges for pre-game NBA win models (to calibrate what "good" means).
