# Matchup 3A — archetype opponent factor (REAL DATA, exploratory)

_Run 2026-10-08 · seed=0 · n_boot=500 · clustered-by-game CIs · dates 2022-10-18..2026-06-13._

> Exploratory: no canonical props frozen-holdout yet (adversary designing one); combos/coherence/conformal/volatility disabled to isolate the opponent factor and protect the 8GB box; archetype-vs-baseline *paired* CI deferred (needs return_raw). crps_vs_season CIs ARE the new clustered (per-game) ones.

## Pooled CRPS by config (lower is better)

| stat | baseline | team_level | archetype | arch−base | team−base |
|---|---|---|---|---|---|
| pts | 3.6502 | 3.6501 | 3.6826 | +0.0324 | -0.0001 |
| reb | 1.4708 | 1.4704 | 1.4750 | +0.0041 | -0.0004 |
| ast | 1.2761 | 1.2757 | 1.2766 | +0.0005 | -0.0004 |
| fg3m | 0.7250 | 0.7249 | 0.7254 | +0.0004 | -0.0001 |

## crps_vs_season_avg (clustered CI; negative = model beats season-avg)

| stat | config | lo | point | hi | CI excl 0 |
|---|---|---|---|---|---|
| pts | baseline | +0.2618 | +0.2730 | +0.2845 | yes |
| pts | archetype | +0.2943 | +0.3054 | +0.3165 | yes |
| reb | baseline | +0.0770 | +0.0811 | +0.0856 | yes |
| reb | archetype | +0.0812 | +0.0852 | +0.0898 | yes |
| ast | baseline | +0.3422 | +0.3467 | +0.3517 | yes |
| ast | archetype | +0.3427 | +0.3472 | +0.3523 | yes |
| fg3m | baseline | +0.1257 | +0.1291 | +0.1321 | yes |
| fg3m | archetype | +0.1262 | +0.1295 | +0.1324 | yes |

## Gate

- Rebounds no-regression: baseline 1.4708 → archetype 1.4750 (+0.0041) → **REGRESSED (FAIL)**
- Stats where archetype improves pooled CRPS vs baseline: NONE
- **Verdict (exploratory): NO SIGNAL — keep flag OFF, document**

_Flag remains OFF regardless; this run does not change config. Confirmation requires the paired archetype-vs-baseline clustered CI on the adversary's frozen props holdout._

