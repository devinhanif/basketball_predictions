-- NBA possession-prediction schema.
-- Mirrors "Data schema (DuckDB)" in CLAUDE.md. Applied idempotently via
-- nba.db.connect.apply_schema (CREATE TABLE IF NOT EXISTS everywhere).

CREATE TABLE IF NOT EXISTS games (
    game_id VARCHAR PRIMARY KEY,
    game_date DATE,
    season INT,
    home_team INT,
    away_team INT,
    home_pts INT,
    away_pts INT,
    national_tv VARCHAR  -- national broadcaster abbrev (e.g. 'TNT'); NULL = unknown/not national
);

-- Idempotent migration for DBs created before national_tv existed; CREATE
-- TABLE IF NOT EXISTS above is a no-op on an existing table, so the column
-- addition is spelled out explicitly here too (see also
-- nba/ingest/national_tv.py:ensure_national_tv_column, called defensively
-- on every national-tv pull).
ALTER TABLE games ADD COLUMN IF NOT EXISTS national_tv VARCHAR;

CREATE TABLE IF NOT EXISTS possessions (
    game_id VARCHAR,
    poss_idx INT,
    period INT,
    clock_start FLOAT,
    clock_end FLOAT,
    off_team INT,
    def_team INT,
    off_players INT[5],
    def_players INT[5],
    score_diff INT,
    outcome VARCHAR,          -- FGM2, FGM3, FGA_miss, TOV, FT_trip, other
    shooter_id INT,
    shot_zone VARCHAR,        -- rim, mid, corner3, above3
    assister_id INT,
    oreb BOOLEAN,
    fta INT,
    pts INT,
    PRIMARY KEY (game_id, poss_idx)
);

CREATE TABLE IF NOT EXISTS stints (
    game_id VARCHAR,
    team_id INT,
    period INT,
    start_clock FLOAT,
    end_clock FLOAT,
    players INT[5]
);

CREATE TABLE IF NOT EXISTS player_rates (
    player_id INT,
    as_of DATE,
    usage FLOAT,
    tov_rate FLOAT,
    ft_pct FLOAT,
    foul_draw FLOAT,
    zone_mix FLOAT[4],
    zone_fg FLOAT[4],
    orb_rate FLOAT,
    drb_rate FLOAT,
    min_per_g FLOAT,
    n_poss INT,                -- sample size, drives shrinkage weight
    source VARCHAR             -- 'observed' | 'blended' | 'prior'
);

CREATE TABLE IF NOT EXISTS team_context (
    game_id VARCHAR,
    team_id INT,
    rest_days INT,
    b2b BOOLEAN,
    travel_miles FLOAT,
    injured_out INT[]
);

CREATE TABLE IF NOT EXISTS players_static (
    player_id INT PRIMARY KEY,
    position VARCHAR,
    height_in FLOAT,
    weight_lb FLOAT,
    birth_date DATE,
    draft_year INT,
    draft_pick INT,
    college VARCHAR,
    college_stats JSON          -- nullable; per-100 poss where available
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    game_id VARCHAR,
    player_id INT,
    team_id INT,
    minutes FLOAT,
    pts INT,
    reb INT,
    ast INT,
    fg3m INT,
    stl INT,
    blk INT,
    tov INT,
    starter BOOLEAN,
    fgm INT,
    fga INT,
    fg3a INT,
    ftm INT,
    fta INT,
    oreb INT,
    dreb INT,
    pf INT
);

-- Idempotent migration for DBs created before the full traditional box
-- score (FGA/FTA/OREB etc.) was captured; needed for the possession-count
-- reconciliation gate (FGA + 0.44*FTA + TOV - OREB) and a real points model
-- (attempts x make-rate). See nba/ingest/boxscores.py.
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fgm INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fga INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fg3a INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS ftm INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fta INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS oreb INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS dreb INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS pf INT;

-- Real per-team-per-game advanced box score stats (nba_api BoxScoreAdvancedV3
-- + BoxScoreFourFactorsV3), so team efficiency features can use actual
-- offensive/defensive rating and pace instead of the FGA/FTA/OREB box-score
-- proxy (player_game_stats has no FGA/FTA/OREB). See nba/ingest/team_advanced.py.
CREATE TABLE IF NOT EXISTS team_game_advanced (
    game_id VARCHAR,
    team_id INT,
    off_rating FLOAT,          -- offensiveRating (points per 100 poss)
    def_rating FLOAT,          -- defensiveRating
    net_rating FLOAT,          -- netRating
    pace FLOAT,                -- possessions per 48 min
    efg_pct FLOAT,             -- effectiveFieldGoalPercentage, fraction 0-1
    tov_pct FLOAT,             -- teamTurnoverPercentage, fraction 0-1
    oreb_pct FLOAT,            -- offensiveReboundPercentage, fraction 0-1
    ft_rate FLOAT,             -- freeThrowAttemptRate, fraction 0-1
    PRIMARY KEY (game_id, team_id)
);

-- As-of player availability / injury status feed (CLAUDE.md "Known data
-- gaps": the #1 blocker for usage-redistribution and the single
-- highest-leverage new input for points, since minutes is the top
-- prop-error driver -- see docs/INJURY_FEED_2026-10-08.md).
--
-- LEAKAGE CONTRACT (read before using this table in any feature/model):
--   `as_of` is the timestamp this status became KNOWN to the world, NOT
--   the game date. A row is valid evidence for a prediction made at time T
--   only if `as_of <= T` -- same discipline as every other as-of table in
--   this schema (player_rates, team_context), but at per-status-change
--   granularity instead of one snapshot per game.
--   - source = 'nba_inactive_list': populated from nba_api's
--     BoxScoreSummaryV2 InactivePlayers result set. This list is only
--     fetchable once the game exists in the NBA Stats API, i.e. AT OR
--     AFTER that game's own tip-off -- it is NOT a forward-looking feed.
--     `as_of` is set to that game's tip-off (approximated at game_date
--     granularity; see docs/INJURY_FEED_2026-10-08.md). Safe to use as a
--     feature for games strictly AFTER this one in a walk-forward
--     backtest (e.g. cold-start / role-change detection on a teammate who
--     is now getting more usage); NEVER safe to use as a predictor of
--     *this* game, and never usable to predict a game that has not yet
--     been played, since the row cannot exist until after the fact.
--   - source = 'manual_announced': human-curated from externally published
--     injury reports (no genuine forward-looking source exists in
--     nba_api -- see docs/INJURY_FEED_2026-10-08.md). Carries whatever
--     `as_of` the curator supplies (the announcement time); callers must
--     still enforce `as_of <= T` themselves.
--   `game_id` is nullable: a status snapshot (e.g. "questionable for
--   Thursday's game") may be recorded before this pipeline has resolved
--   the specific game_id, or may describe a multi-game absence.
-- No PRIMARY KEY (same convention as stints/kalshi_prices/player_game_stats
-- in this schema): loaders upsert idempotently via DELETE+INSERT keyed on
-- (source, game_id) or (source, player_id, as_of) -- see
-- nba/ingest/availability.py.
CREATE TABLE IF NOT EXISTS player_availability (
    player_id INT,
    as_of TIMESTAMP,              -- when this status became known (NOT the game date)
    game_id VARCHAR,               -- nullable: may predate game_id resolution
    status VARCHAR,                -- 'out' | 'questionable' | 'probable' | 'available' | 'inactive'
    reason VARCHAR,                 -- nullable free text, e.g. 'Left Knee; Soreness'
    source VARCHAR,                 -- 'nba_inactive_list' | 'manual_announced'
    pulled_at TIMESTAMP             -- when this pipeline recorded the row
);

CREATE TABLE IF NOT EXISTS prop_predictions (
    run_id VARCHAR,
    game_id VARCHAR,
    player_id INT,
    stat VARCHAR,                -- pts|reb|ast|fg3m|pra...
    mean FLOAT,
    dist_family VARCHAR,
    dist_params JSON,
    p_ge JSON,                   -- {"10":0.93,"15":0.71,...} P(stat >= N)
    q10 FLOAT,
    q50 FLOAT,
    q90 FLOAT,
    made_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kalshi_markets (
    ticker VARCHAR PRIMARY KEY,
    series_ticker VARCHAR,
    event_ticker VARCHAR,
    title VARCHAR,
    player_id INT,
    stat VARCHAR,
    threshold FLOAT,             -- "N+" contracts
    open_time TIMESTAMP,
    close_time TIMESTAMP,
    settled_ts TIMESTAMP,
    result VARCHAR
);

CREATE TABLE IF NOT EXISTS kalshi_prices (
    ticker VARCHAR,
    ts TIMESTAMP,
    yes_bid FLOAT,
    yes_ask FLOAT,
    last FLOAT,
    volume INT,
    open_interest INT,
    source VARCHAR               -- 'live' | 'historical'
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP,
    legs JSON,
    model_prob FLOAT,
    model_prob_lo FLOAT,
    model_prob_hi FLOAT,
    price FLOAT,
    fee_model VARCHAR,
    expected_value FLOAT,
    settled BOOLEAN,
    outcome BOOLEAN,
    realized_pnl FLOAT
);

CREATE TABLE IF NOT EXISTS experiments (
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP,
    rung INT,
    model_name VARCHAR,
    config JSON,
    metrics JSON,
    artifact_path VARCHAR,
    stage VARCHAR,
    -- registry retrofit columns (see CLAUDE.md "Registry retrofit")
    version VARCHAR,
    alias VARCHAR,
    tags JSON,
    metadata JSON
);

-- Ingest bookkeeping: tracks what has already been pulled from nba_api so
-- resumable pullers never refetch cached data. Not part of the modeling
-- schema in CLAUDE.md; owned entirely by nba/ingest/.
CREATE TABLE IF NOT EXISTS ingest_log (
    source VARCHAR,              -- 'games' | 'boxscore' | 'pbp' | 'team-advanced' | 'national-tv' | 'availability' | 'availability-manual'
    key VARCHAR,                 -- season string, game_id, etc.
    status VARCHAR,              -- 'done' | 'failed'
    cache_path VARCHAR,
    fetched_at TIMESTAMP,
    PRIMARY KEY (source, key)
);
