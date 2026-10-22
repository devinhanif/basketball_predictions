# Pre-freeze review, round 3: INGAME_WINPROB_V2 (T513), 2026-10-22

Reviewed: head 6e7a5f4 on `agent/market-ingame-v2`. Rounds 1 and 2 are in `docs/reviews/prefreeze_ingame_v2_2026-10-22.md` and
`docs/reviews/prefreeze_ingame_v2_round2_2026-10-22.md`. Everything here is label-free and read-only; 2025 was never named.

**NOT_READY. One MUST remains (R5), and it is text only.** Rules 1 and 2 still say the scored-row builder, the incremental builder
and the design-matrix builder "are committed with the freeze, with their sha256 in the freeze line", but none of the three exists.
A frozen rule that requires artifacts which do not exist at freeze would be broken the moment it is frozen. With R5's text in,
this is **READY_TO_FREEZE**. No further review round is needed for R5 itself, but the builder diff comes back to the adversary
before the dryrun (part of R5).

Verdict: BLOCKED

## Round-2 items: applied?
| Item | Status |
|---|---|
| R1 as-of roster and names | applied. `asof_roster` uses players with minutes > 0 for the team in games dated strictly before this one, same season, plus box starters. `asof_names` uses the team's 15 previous pbp files. Read line by line: causal, and this game's box roster is never passed |
| R2 run-time identity | applied as written. It references `rows_mismatch` (ints and sets exact, floats within 1e-12) |
| R3 equality test | applied as written. Its status note discloses that the builders do not exist |
| R4 bootstrap | applied (`decision_scoring.Boot`, seed 0, 2,000 draws) |
| S8, S9 | applied |
| Gate | gate.json sha256 72a134b0 (confirmed by `shasum`). DB sha 5dcc497a9de2 unchanged. Cell counts unchanged (801 / 862 / 850) |

## Question 1: builder written after the freeze, is that acceptable?
**Yes, with a binding step.** Every feature, interaction family, the grid, the prefix, the causal five and its inputs, the fitter,
the controls, the floors and the inference are defined in the rule text. A builder written afterwards has nothing left to choose,
except where the rule says "listed in code before the run" (the interaction list in rule 2). That clause is the one place a builder
written after the freeze could tune. It is safe only if the builder is bound and hashed before **any** label is loaded, the
dryrun's synthetic labels included, and if the builder never loads the outcome column. R2's identity and R3's equality test are
label-free, so they can and should run before the dryrun. The draft text implies a single freeze moment and has to say two stages.

## Question 2: early 2022 thin roster: does it feed training?
**Yes. 2022 is the only training season for the 2023 select fit and half the training for the 2024 report fit** (rule 4).
This is acceptable at freeze:
- It is causal, so it is not a leak.
- It touches only L, S and F (A2, A2s, A3, C0), never V1r or A1.
- The thin period is the first few games per team. Per round 2, the as-of roster fills after a handful of team games.

Its effect is noise in the lineup terms of the training fit, which biases toward a lineup null and not toward a false win. That is
a power risk to P2 and P3 in 2023 (and the 2023 floor gates whether 2024 is scored at all), not a validity risk. It joins the 2022
rating thinness that is already disclosed (89.9% of player-games with 5 or more prior games). The repair, an as-of roster and names
drawn from the previous season (2021 sits in `data/history`), would cost one gate rerun. SHOULD S10 lists it, as non-negotiable 8
asks.

## MUST (exact replacement text)

**R5. Two-stage binding.** In rule 1, replace "The reference, the incremental builder and the test are committed with the freeze,
with their sha256 in the freeze line." and the parenthetical "(Status today: ...)" with:
"**Freeze** hashes this file, `nba/ride/causal_five.py` and `tests/ride/test_causal_five.py`. The scored-row builder, the
design-matrix builder (every column and interaction of every arm) and any incremental builder are written **after** the freeze,
under these constraints:
- No code in this run loads the outcome column (final score or winner), real or synthetic, before the **binding commit**.
- The builders read events only through `causal_prefix` and the R1 inputs.
- The binding commit contains the builders, a manifest with their sha256, and the saved outputs of the R3 equality test and the
  rule 7(b) identity. Both are label-free and both must pass.
- The adversary reviews the builder diff against this rule text before the binding commit.
- The dryrun and the real run refuse to start unless the files' sha256 match the manifest.
- A change to any bound file after the binding commit is a new id."

In rule 2, replace "the design-matrix builder (every column and interaction) is committed with the freeze and its sha256 is in the
freeze line, a change to it is a new id" with "the design-matrix builder is bound in the binding commit (rule 1); a change to it
after that commit is a new id". In rule 7(b), append: "It runs before the dryrun (it is label-free) and again inside the real run."

## SHOULD
- S10. In rule 4, append: "2022, the training season for both fits, starts with no earlier games in the window: unresolved
  substitutions in its opening games reach 10.9% (2023 / 2024: 2.7 / 4.2%), and 10.1% of its player-games have the league-rate
  rating. The run reports the unresolved share and the league-rate share for each full training season. Repair, not done: extend the
  as-of roster, names and ratings into 2021 from `data/history` (cost: one gate rerun)."

## Single most likely failure mode
The interaction list, written after the freeze but before binding, drifts toward what a builder author "knows works". R5 binds
it before any label, including synthetic ones, and requires adversary review of the builder diff.

## Not verified
- pytest, ruff and mypy: the tools are missing from the worktree env.
- The builders themselves: they do not exist yet.
- The full-season unresolved share for 2022: only the opening 12 games were measured.

## Drafted row (not appended)
TEST_LEDGER T513 note: "pre-freeze round 3 2026-10-22: R1-R4, S8-S9 applied (gate sha 72a134b0); one MUST R5 (two-stage freeze:
builders written after freeze, bound by hash with R2/R3 outputs and adversary review before any label incl. synthetic); with R5
the rule is READY_TO_FREEZE; early-2022 roster thinness feeds training (power risk, not validity), disclosed via S10; review
docs/reviews/prefreeze_ingame_v2_round3_2026-10-22.md."
