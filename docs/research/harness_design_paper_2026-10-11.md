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

**Addendum 2 (Devin, 2026-10-11: "jev engineering, it's about a better way of working with you by routing tasks").** Jev is
TypeSafe AI's decision model. The practice: a router (an open-source proxy between Claude Code and the API, or a Claude Code
skill) asks Jev which tier should handle each message or sub-task (Haiku / Sonnet / Opus), only ever RAISES a session's tier (to
keep the prompt cache), returns "undecided" below a confidence threshold so Claude decides itself, and sends sensitive classes
(payments, auth, migrations) to a human approval step; related: context compaction before the window fills. Claimed savings
69-80% per session in vendor-adjacent write-ups; unverified here.

**What we adopt (practice, no purchase):** (1) task tiers written down: search/format/docs/boilerplate → the cheapest tier
(read-only Explore agents, scribe drafts); builds and data fixes with typed briefs → Sonnet (this weekend's evidence: the six
Sonnet agents ran every frozen rule exactly, with the gates catching the rest); frozen-rule interpretation, adversary reviews,
production and outward changes → the strongest tier or the main session; (2) raise, never lower, inside a task; (3) "undecided"
is a valid answer: when a brief's class is unclear the main session decides, not the agent; (4) sensitive classes (money,
production, holdout, outward posts) always stop at Devin, which is already non-negotiable 6. **Not adopted yet:** the Jev proxy
or skill itself (needs an OpenRouter/TypeSafe key and a measured token comparison; Devin's purchase if wanted).

**Addendum 3 (2026-10-11 ~17:40, Devin: "google what others did to get their number lower"):** three extended searches. No
rigorous public ablation of NBA prop features exists; practitioner themes (minutes first, with/without conditional usage,
recency weighting, pace, blowout risk, "injury uncertainty widens the range") are all things this repo has tested under rules
(MINUTES_V2, usage redistribution, FOUR_FACTORS, lower tail). Two transferable results: (1) a Bundesliga study (Wilkens 2026)
found the market better calibrated than its model and recommends odds as an INPUT, which is MARKET_LINE_CLASSIFIER; (2)
calibration layers (temperature, then isotonic on a rolling window; Platt favoured over isotonic for log loss stability) fix
over-confidence without changing rankings, which is CALIBRATED_GRID. Sources: techbuzzireland feature-engineering post; the
calibrated football forecasting paper (cse.aua.am); Wilkens, Journal of Sports Analytics 2026; the arXiv conformal and NBA
scoring papers (none on player props).
