# SEQ_LEARNING_CURVE_2 results (rule 86f31649)

**Verdict: A_m=DATA; E=FLAT**

Gates: {"repro_ok": true, "runtime_ok": true, "budget_ok": true}; unfinished arms: none.

Epoch-cap diagnostic: 1/30 (window, seed) cold fits at the cap (share 0.033).

n = 27619 player-games, 1318 games (2023-24, pts), 2000 two-way draws (game x seed).

A_m = C_M(8) - C_M(4): -0.0058 [-0.0087, -0.0029] (97.5%); 95% [-0.0083, -0.0034]; n=27619, clusters=1318 -> **DATA**
E = C_M(10) - C_M(8): +0.0007 [-0.0022, +0.0032] (97.5%); 95% [-0.0018, +0.0029]; n=27619, clusters=1318 -> **FLAT**
A = C_U(8) - C_U(4): -0.0058 [-0.0084, -0.0031] (97.5%); 95% [-0.0081, -0.0035]; n=27619, clusters=1318
step share A - A_m = C_U(8) - C_M(8): +0.0000 [-0.0025, +0.0023] (97.5%); 95% [-0.0021, +0.0021]; n=27619, clusters=1318

| arm | C (AVG) | AVG - FULL |
|---|---|---|
| U_4 | 3.1106 | -0.0212 |
| UP_4 | 3.1101 | -0.0218 |
| U_8 | 3.1049 | -0.0270 |
| M_8 | 3.1049 | -0.0270 |
| U_10 | 3.1048 | -0.0270 |
| M_10 | 3.1056 | -0.0263 |
| U_6 | 3.1078 | -0.0240 |
| M_6 | 3.1077 | -0.0242 |
| U_9 | 3.1042 | -0.0277 |
| M_9 | 3.1056 | -0.0263 |

- Blend weights are those fit on the full 6-seed ensemble and held fixed in every resampled pool (rule, disclosed approximation); the leave-one-seed-out spread of each contrast is printed beside its interval.
- Cold fits are shared by the U / M / UP arms of a window, so the independent per-arm seed resampling ignores that pairing: intervals are conservative (rule, disclosed).
- Descriptive contrasts other than A_m, E and A - A_m are game-clustered on the full 6-seed ensemble only (seed noise not included); slices use the two-way draws; team drop-one is point estimates.
- A failed U(4) reproduction is INCONCLUSIVE (construction) (the rule's Fallback, condition met); a failed U(8) band or a failed FULL / A0 grid reproduction is VOID (gate), as the rule states.
- The 'wall-clock of an interrupted arm' is not counted in the ceiling (the job sums runtime_s of completed arms only), as the parent disclosed.
