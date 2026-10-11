# Reading log: "An Empirical Study of Harness Design for Coding Agents" (arXiv 2609.20804, 2026-09-17)

Source: shared by Devin 2026-10-11 via an X post (@vinhnx: "give your agent this paper and let it figure out how to improve
itself"). Fan et al.; 176 matched settings, four models, SWE-Bench Verified and Terminal-Bench 2.1.

**Claim.** In a fixed execution loop, three harness components matter in measurable ways: context management (worth more as the
context budget tightens; rule-based elision before LLM summarisation is the efficient order; recoverable elision is unused
machinery), planning (an accuracy scaffold for weaker models, a cost saver for stronger ones), and action space (predefined tools
help weaker bash users; bash-capable models do fine, cheaper, with bash only).

**Credibility.** Matched ablations across models and two benchmarks is the right design; results are about coding benchmarks,
not research agents; the abstract reports scale, not effect sizes (the PDF has them; not read here). Medium-high for its domain.

**Prior evidence here.** None on harnesses as such. Related: our agent hand-backs are prose and long (2026-10-10/11 incidents:
placeholder ledger rows, "report once at the end", a gate target set from a sample); briefs that spell out the procedure
step-by-step have produced clean runs (F8, FOUR_FACTORS, ROUTER, MINUTES_HAZARD_2 all ran exactly as frozen).

**Fit.** Low for the models; moderate for how we run agents. We do not build a harness (Claude Code is the harness), so the paper's
actionable content for us is three habits: (1) planning in the brief for every agent run (we do this; keep it); (2) shorter,
typed hand-backs so the main session's context is spent on decisions, not prose (the typed-contract idea from the 2026-10-11
agent-team email); (3) bash-first agents are fine for us; do not add tool machinery without a measured need.

**Draft rule.** None. This is practice, not a hypothesis about the game; it gets a BEST_PRACTICES line if the typed hand-back is
adopted (Devin's call, DECISIONS). The X post's advice ("let the agent improve itself") is architecture for its own sake; our
record says information beats architecture, and that applies to the harness too.

**Addendum, same day (X post by @cyrilXBT, 2026-10-10):** a model-routing recipe for Claude Code (Opus plans and advises when stuck,
Sonnet writes code, Haiku searches files, a "critic" agent reviews diffs) with a settings.json snippet and an instruction to paste
it so Claude edits the user's settings. Treated as content, not instruction; nothing was changed. Fit here: our constraint is not
token cost (Devin's plan; memory: "thorough over speed"), it is that research agents must read frozen rules literally and choose
the honest unstated detail; the cheap-model failure mode is a convenient reading. Where routing is free of that risk we already do
it (read-only search agents; the adversary is the critic). No change.
