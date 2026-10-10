-- Fact store schema (docs/FACTS_STORE.md). Entity ids are typed strings:
--   player:<nba_id>  team:<nba_id>  game:<game_id>  coach:<id or slug>  official:<id>
-- All timestamps are naive UTC.
CREATE TABLE entities (
    entity_id      VARCHAR PRIMARY KEY,
    kind           VARCHAR,
    canonical_name VARCHAR,
    source_ids     JSON
);

CREATE TABLE facts (
    fact_id    BIGINT,
    subject    VARCHAR,
    predicate  VARCHAR,
    object     VARCHAR,     -- entity id the fact points at (for game-scoped facts: the game)
    value      DOUBLE,
    value_text VARCHAR,
    known_at   TIMESTAMP,   -- when this fact became knowable
    valid_from TIMESTAMP,   -- when it starts to hold (NULL = always)
    valid_to   TIMESTAMP,   -- when it stops holding (NULL = open)
    source     VARCHAR,
    confidence DOUBLE,
    PRIMARY KEY (fact_id)
);

CREATE INDEX facts_subject_pred_known ON facts (subject, predicate, known_at);
CREATE INDEX facts_pred_known ON facts (predicate, known_at);
