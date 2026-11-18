# Reading log: schedule context (information program item 7), 2026-11-17

DRAFT intake note (modeler). Declared member of F-INFO as **SEASON_STAKES** (the one part of item 7 not already closed).
**Gate result: FAILS for a live 2026 report** (section "Gate"). Advice or momentum: *Devin's override* (all eight). The stakes idea is
Devin's F16 (FAN_KNOWLEDGE, queued in `docs/research/README.md`).

## Claim (restated)
Travel distance, altitude, rest and back-to-backs, and late-season incentives (eliminated, seed locked, tanking, play-in race) change
who plays and how much.

## Sub-items
| Sub-item | Knowable | Status here |
|---|---|---|
| Rest, back-to-backs, schedule density | schedule, weeks before | **CLOSED**: WORKLOAD T451-T454 (fatigue and density add nothing), WORKLOAD_PTS_V2 T521 (SCHEDULE family -0.0004 when dropped), PPLAY_CONTEXT T316 (b2b habits nothing), B2B_ELO draft gate fail, CONTEXT_SCREEN_GAMES T180 (rest null for winners) |
| Travel distance, time zones | schedule | **CLOSED**: T521 TRAVEL family (-0.0016 when dropped, within noise), T180 (west-to-east CI spans 0) |
| Altitude | schedule | **CLOSED for props** (T521 has no altitude effect worth a rule); T180 game winner p 0.081 (n 271), a candidate only with a fresh-season rule, not this program |
| Late-season stakes (as-of standings) | the morning of the game | **SEASON_STAKES (this member)** |

Nothing in the closed sub-items is different now (same information, same timing). They are not re-proposed.

## Outside evidence (for the closed sub-items, so the closure is weighed fairly)
Peer-reviewed observational studies find second-night back-to-backs and travel distance worsen team performance (2014-2018 data,
Chronobiology International 2022; Charest et al., J Clin Sleep Med 2021: about 4% lower win odds per extra 500 km on some back-to-back
sequences; Frontiers in Psychology 2021, one season: no location effect for back-to-backs). These are team win effects, in-sample,
season-wide; none tests a player-props forecast that already uses recent minutes. They do not contradict T521's null on our props
(different target, and our recency features absorb much of it). Sources: [Arizona experts page](https://experts.arizona.edu/en/publications/impacts-of-travel-distance-and-travel-direction-on-back-to-back-g/),
[PubMed 34170248](https://www.pubmed.gov/34170248/), [Frontiers 2021](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2021.630769/full).
Effect sizes are from abstracts and snippets; full texts not read.

## SEASON_STAKES: information, known-at, mechanism
As-of standings before the game date (wins, losses, games left, magic numbers for elimination and seed lock, bottom-5), computed from
`games` strictly before the date. Forecast time: morning onward. Mechanism: **P(play)** (rest of core players on eliminated or locked
teams) and **minutes** (bench run).

## Prior evidence here
T476 (descriptive, 2023-24, many slices, uncorrected): last 20 games, eliminated teams' core DNP 17.8% vs 3.5%, **76% of it listed OUT**
(so production already sees most of it); tight-race teams behave normally; conditional on playing, pts errors within +-0.25 late. Early
bottom-5 teams over-forecast in Oct-Nov (-0.92 pts, n 920), fading by Dec-Jan.

## Gate (umbrella section 6)
* Rows (live 2026): last 20 games x 30 teams = 600 team-games; stake state (eliminated, locked, bottom-5) assumed about 40%: about 240
  team-games x about 10.5 not-OUT players x 0.86 = **about 2,170 rows**, all in March-April 2027.
* P(play) log loss: per-row sd of the paired difference about 0.2 (assumed), design effect 1.2: **h about 0.0092 nats > floor 0.005**.
* e_plan: unlisted core DNP 17.8% x 24% = about 4.3%. If production predicts 2.0-3.5% for these rows, the oracle KL gain is
  **0.001-0.010** nats; at 60% capture **0.0006-0.006**. Minutes (played rows): pts errors within +-0.25 imply a minutes bias under
  about 0.5 min, an oracle CRPS gain under about 0.02 min vs the 0.10 floor.
* **Result: NOT FREEZABLE for a live 2026 report** (h above the floor and e_plan at or below it). History 2019-2024 without 2025 (the report era: about 15,000 stake
  rows, h about 0.0035) resolves the floor, but e_plan stays at or below it: power under 0.5 unless production is badly calibrated on these
  rows, which nobody has measured.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Games for as-of standings | 2013-2024, 2026 | about 1,310 a season | none | n/a | n/a |
| Schedule, tips, travel, altitude | 2022-23 to 2026-27 raw schedule parquet; `game_context_features.parquet` | complete | history seasons' tips (real-tip gating needs `game_tipoff` for 2013-2021) | partly | archivist 0.5 day |
| Production P(play) on history | 2022-2024 stored | 33,000 rows a season | 2013-2021 not run; 2019-21 reports structurally different (T315) | partly | modeler 1-1.5 days |

## What would change the verdict
A descriptive on **2019-2021** (report-era history seasons T476 did not look at; 2013-2018 have no report, so a production-recipe
P(play) cannot be built there; no claim): production-recipe P(play) calibration on stake-state rows in the last 20 games. If production under-predicts DNP there by at least 2 percentage points (oracle gain at least about 0.01 nats), draft a
history rule (select 2019-2021, report 2022-2024) and a live addendum for 2027-28. Cost: modeler 1.5 days, maintainer command.

## Playbook check
Class: gradient-boosted trees (P(play) and minutes) with one as-of standings group. (1) Information: standings are pre-tip, but most of
the behaviour reaches the report (76%). (2) Data: enough in history; live too small. (3) Validity: as-of standings strictly before the
date; magic numbers computed from the schedule, not from final standings (a planted-future risk). (4) Lineage: T476, F16, TANKING slices;
WORKLOAD (closed schedule effects). (5) Scores against production P(play) at T-60 (with the report), not against Elo.

## Verdict
**PARK** (gate fails for this season); optional descriptive on 2019-2021. Expected null. Prior: P(freezable) 0.15; P(pass if run) 0.04.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: as-of standings are exact in history, so a 2023 -> 2024 backtest is possible on data, but one season holds about
2,500 stake rows (h about 0.0086 nats > 0.005): not freezable per season; a 2025 touch has the same size. 2023-24 were seen
descriptively (T476). Not proposed.
