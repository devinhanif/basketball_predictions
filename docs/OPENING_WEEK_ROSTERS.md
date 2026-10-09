# Opening-week rosters for the forward props path

Status: implemented behind a flag; production default unchanged (`--roster-source recent`).
Measured on read-only replays of two opening weeks (2024-10-22..11-04 and 2023-10-24..11-06) on a
database COPY. Season 2025 was not read. No live nba_api call was made while building this.

## Problem

The forward roster is "players in a team's last 10 games, minus injury-report OUTs". At the
opener that is last spring's roster. In the dress rehearsal (docs/DRESS_REHEARSAL_2026-10-09.md,
issue B) 41% of the players who played on 2024-10-22..24 had no prediction, and P(play) was 0.70
vs 0.57 observed.

## What changed

| Piece | Where | Default |
|---|---|---|
| Official roster fetch: `CommonTeamRoster`, one call per team (30), cached per as-of date in `data/rosters/<date>/<team>.parquet`; rate-limited; 3 attempts per team; a circuit breaker stops after 2 consecutive failed teams; never raises (falls back to the recent roster and says so in the run summary) | `nba/props/rosters.py` | used only with `--roster-source official` |
| Merge: official roster (slate teams) union recent-games players, minus OUTs. A player listed on any official roster belongs to that team (a mover is never projected for two teams); a recent-games player no roster lists is kept (union) | `merge_rosters` | same |
| No-history players: minutes from the existing draft-slot ridge prior (`rookie_minutes.RookieMinutesPrior`, `apply_prior`); stats from the league distribution of that stat among played games at the same minutes (4-minute bins, last two seasons); distribution labelled `recency_fallback` by the existing <5-prior-games convention | `nba/props/roster_cold.py` | on when an official roster is passed |
| Roster cap 18 instead of 13 when an official roster is passed (official rosters list up to 15 + 3 two-way; with the sim off the cap only bounds output) | `ForwardConfig.official_max_roster` | 18 (official source only) |
| Players new to a team | `ForwardConfig.new_team_shrinkage` | OFF (see "Movers") |
| Strict variant (also drop recent players no official roster lists) | `ForwardConfig.drop_unlisted_recent` | off; no CLI flag |
| CLI/pipeline | `python -m nba.daily run ... --roster-source {recent,official}`; `run_daily(roster_source=, roster_dir=, roster_fetch=)` | `recent` (a test pins CLI and function defaults) |
| Replay harness | `nba/props/roster_replay.py` | refuses to open a file named `nba.duckdb` |

With `recent` nothing is read or computed differently (test: `official_roster=None` equals an
empty frame, byte for byte).

### Movers: what is done about "borrowing the old team's minutes"

The minutes model's history is per player, so a traded player keeps his old team's play rate and
minutes. I implemented the existing role-change convention (pseudo-counts times
`role_change.k_multiplier`: x4 right after the change, decaying to x1 over 8 team games, shrinking
toward the 24-minute / 0.82 league default) and measured it against not doing it. It was worse in
all three replays, so it is OFF by default and the mover keeps his own history:

| Replay | movers n | minutes MAE, shrink minus no-shrink (min, 95% CI) | P(play) Brier delta |
|---|---|---|---|
| 2024 first 3 days | 56 | +0.13 [-0.10, +0.36] | +0.008 [+0.005, +0.012] |
| 2024 first 14 days | 77 | +0.45 [+0.16, +0.85] | +0.005 [-0.000, +0.009] |
| 2023 first 14 days | 90 | +0.79 [+0.46, +1.18] | +0.004 [-0.002, +0.010] |

(delta > 0 = shrinking is worse; CIs are game-clustered bootstraps; the three rows overlap in
players, they are not independent samples.) The cleaner statement is: the player's own history
predicts his minutes on a new team better than the league default does; a role-change flag after
the first games for the new team is still handled by the existing CUSUM path. The code stays
available for the maintainer to re-test once real 2026-27 trades exist.

## Replay design and why the numbers are optimistic

A season-end `CommonTeamRoster` includes later trades, so it is not a valid pre-tip roster. The
replay proxy is: players with minutes > 0 for a team in the first 14 days of the season
(`proxy_rosters_from_first_games`; a player who changed teams in the window is assigned his first
team). This looks past the replay date, so:

* every proxy player played at least once, so **coverage of players who played is an upper
  bound** (the real roster also lists never-playing players, but those cannot be missed);
* the observed play rate among proxy rosters is inflated, so **P(play) calibration is bracketed,
  not verified** (see below);
* `players_static` in the copy has pedigree for every rookie (it is pulled for players seen in
  a box score). Live, a debutant has no row until pulled, and then the rookie prior silently
  falls back to the league default (see "Before opening night").

The `recent` arm is not proxied: it is the production code on the same dates.

## Results

Arms (all context-residual primary rows, exactly what `nba.daily` stores): `recent` = production;
`official` = official-proxy union recent, cap 18, rookie prior, no mover shrink (the flag's
behavior); `official_strict` = also drop recent players no roster lists; `official_cap13` = cap
unchanged; `official_noadj` = no rookie prior, no mover shrink (isolates the cold-start pieces).
Raw JSON: `data/rehearsal/rr_3d.json`, `rr_14d.json`, `rr_14d_2023.json` (gitignored).

### Coverage of players who played (share of player-games with a prediction; minutes-weighted in brackets)

| Replay (played player-games) | recent | official | official_strict | official_cap13 |
|---|---|---|---|---|
| 2024-10-22..24 (376) | 0.609 [0.686] | 0.955 [0.980] | 0.968 [0.990] | 0.859 [0.949] |
| 2024-10-22..11-04 (2,368) | 0.842 [0.909] | 0.967 [0.986] | 0.975 [0.990] | 0.885 [0.950] |
| 2023-10-24..11-06 (2,254) | 0.837 [0.910] | 0.963 [0.987] | 0.965 [0.987] | 0.892 [0.953] |

By group, 2024 14 days (official): debutants 0.935 [0.927] (n=62), movers 0.917 (n=84); `recent`:
debutants 0.000, movers 0.036. The gain is concentrated in the first three team-games: `recent`
covered 0.67 / 0.58 / 0.65 of players on days 1-3 and 0.86-0.92 on days 4-14 (the last-10 window
fills with new-season games, but never reaches the official source); `official` is 0.93-0.99 on every day. The cap matters: at 13, 40% of
debutants are dropped (the rookie prior ranks them last), at 18 they are not.

### P(play)

Mean predicted vs observed rate among predicted players (n = predicted player-games):

| Replay | recent | official (cap 18, union) | official_strict (cap 18) |
|---|---|---|---|
| 2024 first 3 days | 0.705 vs 0.609 (n=376) | 0.692 vs 0.705 (n=509) | 0.713 vs 0.837 (n=435) |
| 2024 first 14 days | 0.719 vs 0.740 (n=2,696) | 0.691 vs 0.702 (n=3,262) | 0.713 vs 0.831 (n=2,780) |
| 2023 first 14 days | 0.723 vs 0.735 (n=2,567) | 0.699 vs 0.708 (n=3,064) | 0.713 vs 0.795 (n=2,735) |

`recent` shows the dress-rehearsal miscalibration only on the first three days (0.705 vs 0.609);
by 14 days it is roughly calibrated in mean. P(play) is a per-player function of history, so on
players covered by both arms it is identical (paired Brier delta 0.000). What changes is WHO is
in the set. The union arm (stale recent players stay, cap 18) is the pessimistic bracket and is
calibrated in mean (0.69-0.70 vs 0.70-0.71); the strict arm is the optimistic bracket (the
proxy contains only players who played) and under-predicts (0.71 vs 0.80-0.84). A real official
roster sits between them. Do not read the official-arm Brier (0.147-0.167) as better or worse
than `recent`'s (0.157-0.207): the sets differ. Verify on the first live nights.

### Prop CRPS and minutes on covered players (paired, game-clustered 95% CI; delta = first minus second, lower is better)

* official vs recent, players covered by both (n about 1,900-2,500 per replay): CRPS pts
  +0.001 [0.000, +0.002] (2024, 14 d), +0.007 [0.000, +0.017] (2024, 3 d), 0.000 (2023); reb/ast/3pm
  all within +/-0.001. Minutes MAE -0.20 [-0.31, -0.10] (2024, 14 d), -0.13 [-0.22, -0.04]
  (2023). No loss on players the old path already covered; the small minutes gain is the rookie
  prior reaching second-year-in-the-league debutants of the first days.
* Rookie prior (official vs official_noadj), debutants who played: minutes MAE -5.8 [-8.4, -2.5]
  (n=40, 3 d), -7.4 [-9.4, -5.2] (n=58, 2024 14 d), -7.5 [-9.7, -5.4] (n=63, 2023 14 d);
  points CRPS -1.3 [-2.2, -0.4], -2.0 [-2.7, -1.3], -2.1 [-2.8, -1.6]; reb CRPS -0.6 to -0.75.
  Consistent in all three, CIs exclude 0. (Without the prior a debutant is projected at the
  24-minute league default.)
* Newly covered players (only in the official arm, 2024 14 d): 300 player-games, points CRPS
  2.80 (covered-by-both players: about 3.5). There is no baseline for them in the old path (they
  had no prediction); a lower level is mostly lower minutes, so do not read it as skill.

## Honest notes

* The proxy is optimistic (coverage upper bound; P(play) bracketed, not validated). The movers
  and rookie-prior comparisons are within-proxy and so less affected.
* Held for real data: whether `CommonTeamRoster` is complete the week before the opener and how
  quickly it reflects waivers. `official_strict` would be the right setting if it is, but the
  replay cannot prove that (the proxy omits players who do not play, which is exactly who strict
  removes), so the union default is kept. The code switch is `ForwardConfig(drop_unlisted_recent=True)`.
* Debutant stat distributions use league stats at the projected minutes, not player-specific
  skill; they are `recency_fallback` rows and should be treated as wide priors.
* Nothing here lowers the DNP risk for rostered veterans; it only stops dropping players who
  play.

## Before opening night

1. `players_static` is only pulled for players seen in a box score, so a 2026-27 rookie has no
   draft slot and gets the league-default minutes (the `noadj` row above: debutant minutes MAE
   13-14 vs 8-9). The run summary prints "N rostered players lack players_static" when it
   happens. Pull them through the shared ingest queue once the rosters exist (about 60-80
   CommonPlayerInfo calls; I did not make any). I added `rosters.missing_static_ids(con, roster)`
   to list them.
2. The first live run makes 30 `CommonTeamRoster` calls (cached per date afterwards; a second
   run the same day costs none). They go through the `--rate-limit-s` limiter (default 0.6 s);
   raise it if the queue owner needs headroom.

## Commands

Live (maintainer, opening day; supervised, as the dress-rehearsal checklist says):

    uv run python -m nba.daily run --date 2026-10-20 --roster-source official --rate-limit-s 2.5

Replay (on the copy; hold `data/ops/heavy.lock`; about 2 min per 14 dates once models are cached):

    uv run python -m nba.props.roster_replay --db data/rehearsal/nba_full_copy.duckdb \
        --start 2024-10-22 --end 2024-11-04 --out data/rehearsal/rr_14d.json

## Recommendation

Turn `--roster-source official` on for opening night (and keep it on through at least the first
two weeks), with the checks above. It cannot make the covered predictions worse (paired CRPS delta
within +/-0.002 on shared players) and it restores 95%+ of player-games (98-99% of minutes) in the
first week where the recent roster misses 15-40%; the rookie prior is a measured, consistent gain.
Leave the production default at `recent` in code until the first live week confirms the roster
fetch and P(play). After about two weeks the two sources converge (recent coverage reaches
0.86-0.92), so the flag is mainly an opening-week fix; revisit then. Treat early parlay-engine
results on rookies and movers as untested either way.
