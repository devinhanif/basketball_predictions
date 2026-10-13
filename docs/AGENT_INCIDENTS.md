# Agent incidents

One row per error an agent or the main session made, whatever caught it. Reviewed monthly (first review 2026-11-13): a class
that repeats changes that agent's checklist (`.claude/agents/*.md`, "Before you return") or becomes a hook (`.claude/hooks/`,
`.git/hooks/`); a checklist item or hook that caught nothing in a month is proposed for deletion. Rules live where they are
enforced; BEST_PRACTICES keeps the incident history.

| Date | Agent | Class | What happened | Caught by | Cost | Fix |
|---|---|---|---|---|---|---|
| 2026-10-13 | modeler | scope | SEQ_LEARNING_CURVE_2 download list left out the shotdetail archives the loader needs (only source of game_date) | loader stopped before writing | one extra Devin download | checklist: compare a download list to what the loader reads and the last download of that source |
| 2026-10-13 | modeler, relayed by main | scope | `history_static_fix fetch` covered 2019-21 only; the 2015-18 gap stayed at 25.2% after Devin ran it | main session re-measured | one wasted Devin run | fetch widened (b2c20d1); checklist: read the code path a command runs; give the dry-run count |
| 2026-10-13 | main, archivist | attribution | Co-Authored-By trailer pushed in e333157 and 6d0d690 | after push | two commits carry it | `attribution` set empty in .claude/settings.json (cause); .git/hooks/commit-msg strips trailers (backstop) |
| 2026-10-13 | main | reading | D4 reported PASS on the pooled 0.33%; per season 2013-14 was 1.061% | modeler re-gate | a wrong status to Devin | checklist: per season or pooled, as the rule says, before reporting |
| 2026-10-13 | modeler | contradiction | AVG_SHADOW_LIVE revision kept a late-row drop and a "never touches 2025" line that contradicted the fixes | adversary re-review | one review round | checklist: after revising a rule, grep it for every statement the revision changed |
| 2026-10-13 | main | bookkeeping | guessed timestamps (12:40 for 12:16; "finishing now"); R19 named as the live arm; FT mismatch direction backwards; facts-store item listed open when done | self, on checking | corrections in DECISIONS | checklist: `date` before any time; quote DECISIONS/ledger, not memory |
