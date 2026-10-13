# SEQ_LEARNING_CURVE results (rule ad0408a7)

**Verdict: RISING**

Gates: {"repro_ok": true, "runtime_ok": true, "budget_ok": true}; unfinished arms: none.

Epoch-cap diagnostic: 1/42 cold fits reached the cap (share 0.024, NORM-only 0.000); arms with every fit at the cap: none.

n = 27619 player-games, 1318 games (2023-24, pts), 2000 paired game-clustered draws.

| N | C(N) AVG | AVG - FULL |
|---|---|---|
| 1 | 3.1226 | -0.0093 [-0.0118, -0.0068] |
| 2 | 3.1157 | -0.0162 [-0.0189, -0.0136] |
| 3 | 3.1112 | -0.0207 [-0.0236, -0.0177] |
| 4 | 3.1118 | -0.0201 [-0.0231, -0.0172] |
| 5 | 3.1091 | -0.0227 [-0.0259, -0.0197] |
| 6 | 3.1085 | -0.0234 [-0.0264, -0.0204] |
| 7 | 3.1064 | -0.0254 [-0.0286, -0.0223] |
| 8 | 3.1056 | -0.0263 [-0.0295, -0.0231] |

A = C(8) - C(4): -0.0062 [-0.0079, -0.0044]; B = C(8) - C(6): -0.0029 [-0.0044, -0.0014]

Decision detail: {"verdict": "RISING", "criteria": {"i": true, "ii": true, "iii": true, "iv_gates": true, "v_budget": true}, "fallback_construction": false, "hurts": false}

- Cost ceiling (review F9): the job sums runtime_s of COMPLETED arms only, so wall-clock spent in an arm that was interrupted before it wrote its info file is not counted; the 240 T4-minute ceiling can be overrun by one partial arm.
- Seed noise (review ruling 11): the figures under 'replicates' rest on three main-minus-replicate differences (N = 4, 6, 8); the 95% range of a standard deviation from 3 values is about 0.6x to 3.7x.
