# Knowledge graph: what it would be here, and what it would not (design note, 2026-10-10)

Devin asked: "what about knowledge graphs?" Short answer: yes as the project's information backbone, no as a model.

## What the data has already taught us
Every win came from knowing something true before tip-off (injury report, confirmed lineups, integer counts); every
architecture-only idea lost. A knowledge graph is two different things and only one of them fits that lesson:
1. **A representation of facts with time**: entities (players, teams, coaches, officials, reporters), relations (plays-for,
   coached-by, listed-OUT-on, returned-after, started-with, replaced, restricted-to-minutes), each fact stamped with when it
   became knowable. This is CLAUDE.md's "known-at is first-class" made explicit. Fits.
2. **A model over the graph** (node embeddings, GNNs, link prediction). This is architecture. The repo's record (RAPM, set
   transformer, PBP-GPT, rung-4 heads, F8/F11 projected-five features) says it loses without new information. Does not fit.

## What the graph gives us if built as (1)
- One as-of query for the pretip pipeline and the explainer: "everything knowable about this game at T-60 / T-30", instead of
  five joins across injury rows, lineups snapshots, players_static, coaches and (soon) reporter flags.
- Entity resolution in one place: the alias problem (vendor nicknames, duplicate official ids) becomes a maintained edge set.
- Provenance per fact: source, known-at, who verified it. This is the prompt-injection defence for the reporter feed: a tweet
  becomes a *claimed* edge with a source and never a feature until a rule says how it is used.
- Replayability: the graph at time t is a filter on known-at, so backtests ask the same question the live job asks.

## What it would look like (cheap version)
Not a graph database. Two DuckDB tables: `facts(subject_id, predicate, object_id_or_value, known_at, valid_from, valid_to,
source, confidence)` and `entities(id, kind, canonical_name)`, plus views per predicate. The existing tables stay the source of
truth; the facts table is derived nightly and rebuildable (same rule as model-ready frames). Everything the daily pipeline
reads about availability, lineups, coaches and restrictions is read through it; nothing is written into it by a model.

## What it would not do
- It will not beat the market by itself and gets no pre-registration; it is scaffolding.
- Graph features (e.g. "who historically replaces whom") were already measured: the most frequent absorber takes the top
  spot 29% of the time (reports/model_miss_examples_2026-10-10.md); role clusters add nothing over position. The graph records
  these relations; it does not make them predictive.

## Order of work (not started; after the three frozen runs and the coach analysis)
1. `facts` and `entities` tables + nightly builder from existing tables (availability, lineups, coaches, players_static, aliases).
2. The explainer reads from it (one as-of query per game).
3. The reporter feed writes *claimed* edges into it with provenance; the briefing shows them; a rule decides their use.
