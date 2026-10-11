# Tracking screen report (2026-10-09)

See docs/TRACKING_SCREEN.md results section (identical table). Raw: reports/tracking_screen/results.json, report.txt.

| cell | n (2024 played rows) | CRPS A (prod) | CRPS B (+family) | delta B-A [95% clustered CI] | raw p | BH p (m=5) | cov80 A/B | bias A/B | cold (<10 prior) n: delta [CI] | 2023 delta (descr.) | checks failed | PASS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| passing|ast | 27583 | 0.9164 | 0.9162 | -0.0002 [-0.0011, +0.0009] | 0.774 | 0.774 | 0.796/0.794 | +0.037/+0.015 | 463: -0.0139 [-0.0210, -0.0071] | -0.0015 | floor, ci_excludes_0, bh_q | NO |
| passing|pts | 27583 | 3.1890 | 3.1906 | +0.0016 [-0.0004, +0.0035] | 0.123 | 0.205 | 0.795/0.794 | +0.045/+0.044 | 463: -0.0023 [-0.0146, +0.0110] | +0.0007 | floor, ci_excludes_0, bh_q | NO |
| shooting|pts | 27583 | 3.1890 | 3.1909 | +0.0018 [-0.0002, +0.0039] | 0.097 | 0.205 | 0.795/0.790 | +0.045/+0.031 | 463: -0.0018 [-0.0126, +0.0099] | +0.0003 | floor, ci_excludes_0, bh_q | NO |
| shooting|fg3m | 27583 | 0.6150 | 0.6153 | +0.0003 [-0.0003, +0.0009] | 0.335 | 0.419 | 0.791/0.789 | -0.010/-0.011 | 463: -0.0039 [-0.0079, +0.0004] | +0.0002 | floor, ci_excludes_0, bh_q | NO |
| rebounding_T|reb | 27583 | 1.3249 | 1.3237 | -0.0013 [-0.0022, -0.0004] | 0.009 | 0.045 | 0.800/0.799 | +0.032/+0.031 | 463: -0.0018 [-0.0081, +0.0045] | +0.0006 | floor | NO |
