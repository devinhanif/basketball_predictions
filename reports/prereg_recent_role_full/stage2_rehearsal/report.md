# RECENT_ROLE_FULL Stage 2 results (season 2024, pts only, one run)

Rule sha256 None. Verdict: **PASS** (a pass is a proposal; Devin decides).
Expected before scoring: delta -0.007, pass probability [0.75, 0.82].

| arm | n rows | n games | integer CRPS / delta | CI | p | p BH | MDE |
|---|---|---|---|---|---|---|---|
| A0 | 27701 | 1320 | 3.176840 | | | | |
| FULL | 27701 | 1320 | 3.168525 | | | | |
| PL_FULL | 27701 | 1320 | 3.177104 | | | | |
| SHUF_CTRL | 27701 | 1320 | 3.310436 | | | | |
| SHUF_FULL | 27701 | 1320 | 3.314161 | | | | |
| FULL_minus_A0 | 27701 | 1320 | -0.00831 | [-0.01275, -0.00408] | 0.0005 | 0.0005 | 0.00434 |
| FULL_minus_PL_FULL | 27701 | 1320 | -0.00858 | [-0.01276, -0.00440] | 0.0005 |  | 0.00418 |
| SHUF_FULL_minus_SHUF_CTRL | 27701 | 1320 | 0.00372 | [0.00186, 0.00555] | 0.0005 |  | 0.00185 |

| slice | n | FULL - A0 | CI |
|---|---|---|---|
| all | 27701 | -0.00831 | [-0.01275, -0.00408] |
| min<5 | 2326 | -0.05404 | [-0.07059, -0.03758] |
| bench | 15589 | -0.01082 | [-0.01723, -0.00491] |
| playoffs | 1804 | -0.00436 | [-0.01975, 0.01111] |
| cold_start | 464 | -0.01027 | [-0.03089, 0.01126] |
| starter | 12112 | -0.00509 | [-0.01027, -0.00011] |
| short(<0.5*proj) | 2820 | -0.06215 | [-0.07878, -0.04519] |
| not short | 24881 | -0.00221 | [-0.00665, 0.00185] |
| min<10 | 4299 | -0.03617 | [-0.04775, -0.02427] |

```json
{
 "coverage": {
  "A0": {
   "2024": {
    "0.10": 0.11717988520270026,
    "0.20": 0.21645427962889427,
    "0.80": 0.8026786036605177,
    "0.90": 0.9006534060142233
   }
  },
  "FULL": {
   "2024": {
    "0.10": 0.11634959026749937,
    "0.20": 0.21558788491390202,
    "0.80": 0.802714703440309,
    "0.90": 0.9007978051333887
   }
  }
 },
 "pi": {
  "A0": {
   "ece": 0.009525686174817,
   "logloss": 0.2442829888650198,
   "auc": 0.8552068199238303,
   "mean_pi": 0.09227569283677103
  },
  "FULL": {
   "ece": 0.009304253473699725,
   "logloss": 0.23834476278144556,
   "auc": 0.8637805600046291,
   "mean_pi": 0.0924971255378883
  },
  "ece_delta": -0.00022143270111727419
 },
 "verdict": {
  "pass": true,
  "primary": true,
  "delta": -0.008314476376969474,
  "ci": [
   -0.012753930621961788,
   -0.004075507257000795
  ],
  "p_bh": 0.0004997501249375312,
  "mde": 0.0043392116824804965,
  "floor": -0.005,
  "ece_ok": true,
  "coverage_ok": true,
  "coverage_bad": [],
  "slices_ok": true,
  "slices_worse": [],
  "placebo_ok": true,
  "shuffle_ok": true,
  "gate_ok": true,
  "note": "A pass is a production-change PROPOSAL only (Devin's decision); a miss is read against the stated expectation, not as a broken pipeline."
 }
}
```
