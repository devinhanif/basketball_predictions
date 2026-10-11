# F9 young top-10 pick prior: stopped at data checks

Pre-registration: docs/prereg/F9_YOUNG_PICK_PRIOR.md (frozen sha256 7dfe62de, verified). Ledger T184.

No arm (A0 to A3) was fitted. Minimum data check 1 failed twice, so the study closes as
"data/power insufficient" under its own kill criteria.

| check | value | threshold | ok |
|---|---|---|---|
| 1a draft_pick coverage (n=630 players) | 77.9% | >= 90% | NO |
| 1b NaN gap, played<10 vs rest (n=55,202 rows) | 13.4 pp | <= 2 pp | NO |
| 2 slice S rows 2023 / 2024 | 6,022 / 912 | >= 300 | yes / yes |
| 3 planted future and same game | pass (tests) | exact | yes |
| 4 flag-off identity | pass (tests); A0 reproduction not run | | partial |

Caveats: undrafted players are counted as missing by 1a; the DB starts in 2022 so the frozen
season index is truncated (2023 slice S: 1,043 of 6,022 rows have draft_year >= 2022).
Run: `.venv/bin/python -m nba.eval.f9_young_pick checks`.
