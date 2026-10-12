# ADVANCED_RATES results

| family | stat | season | n | dCRPS [CI] | p_bh | B-P | cov80 | bias |
|---|---|---|---|---|---|---|---|---|
| USAGE_PACE | pts | 2023 | 27619 | -0.0026 [-0.0063, +0.0011] | 0.322 | +0.0192 | 0.773 | -0.151 |
| USAGE_PACE | pts | 2024 | 27701 | +0.0022 [-0.0009, +0.0052] | 0.708 | +0.0173 | 0.763 | -0.106 |
| USAGE_PACE | reb | 2023 | 27619 | +0.0024 [+0.0010, +0.0039] | 0.015 | +0.0072 | 0.792 | -0.040 |
| USAGE_PACE | reb | 2024 | 27701 | +0.0004 [-0.0008, +0.0016] | 0.735 | +0.0062 | 0.787 | -0.045 |
| USAGE_PACE | ast | 2023 | 27619 | -0.0010 [-0.0023, +0.0002] | 0.200 | +0.0038 | 0.789 | -0.046 |
| USAGE_PACE | ast | 2024 | 27701 | -0.0000 [-0.0010, +0.0010] | 0.990 | +0.0032 | 0.783 | -0.052 |
| USAGE_PACE | fg3m | 2023 | 27619 | +0.0003 [-0.0005, +0.0011] | 0.608 | +0.0044 | 0.789 | -0.013 |
| USAGE_PACE | fg3m | 2024 | 27701 | +0.0004 [-0.0003, +0.0011] | 0.708 | +0.0044 | 0.785 | -0.002 |
| EFFICIENCY | pts | 2023 | 27619 | +0.0006 [-0.0028, +0.0044] | 0.872 | +0.0158 | 0.775 | -0.184 |
| EFFICIENCY | pts | 2024 | 27701 | -0.0024 [-0.0055, +0.0006] | 0.708 | +0.0075 | 0.765 | -0.106 |
| EFFICIENCY | fg3m | 2023 | 27619 | -0.0008 [-0.0016, +0.0000] | 0.177 | +0.0018 | 0.788 | -0.022 |
| EFFICIENCY | fg3m | 2024 | 27701 | +0.0003 [-0.0004, +0.0011] | 0.708 | +0.0025 | 0.784 | -0.006 |
| PLAYMAKING | ast | 2023 | 27619 | +0.0001 [-0.0011, +0.0012] | 0.884 | +0.0044 | 0.788 | -0.058 |
| PLAYMAKING | ast | 2024 | 27701 | +0.0004 [-0.0006, +0.0015] | 0.708 | +0.0036 | 0.784 | -0.059 |
| REBOUNDING | reb | 2023 | 27619 | +0.0020 [+0.0007, +0.0033] | 0.037 | +0.0051 | 0.792 | -0.049 |
| REBOUNDING | reb | 2024 | 27701 | -0.0002 [-0.0013, +0.0008] | 0.796 | +0.0034 | 0.788 | -0.053 |
| SHOT_PROFILE | pts | 2023 | 27619 | +0.0021 [-0.0016, +0.0062] | 0.423 | +0.0019 | 0.774 | -0.161 |
| SHOT_PROFILE | pts | 2024 | 27701 | +0.0008 [-0.0022, +0.0038] | 0.777 | +0.0023 | 0.764 | -0.110 |
| SHOT_PROFILE | fg3m | 2023 | 27619 | -0.0008 [-0.0017, +0.0002] | 0.291 | +0.0003 | 0.787 | -0.017 |
| SHOT_PROFILE | fg3m | 2024 | 27701 | +0.0001 [-0.0007, +0.0009] | 0.870 | +0.0010 | 0.783 | -0.002 |
| DEFENSE_CONTEXT | pts | 2023 | 27619 | -0.0030 [-0.0060, +0.0001] | 0.177 | -0.0030 | 0.774 | -0.163 |
| DEFENSE_CONTEXT | pts | 2024 | 27701 | +0.0011 [-0.0015, +0.0036] | 0.708 | +0.0008 | 0.765 | -0.157 |
| DEFENSE_CONTEXT | fg3m | 2023 | 27619 | -0.0003 [-0.0011, +0.0005] | 0.608 | -0.0004 | 0.789 | -0.012 |
| DEFENSE_CONTEXT | fg3m | 2024 | 27701 | -0.0002 [-0.0009, +0.0004] | 0.735 | -0.0002 | 0.784 | -0.007 |
| PLAY_TYPE | pts | 2023 | 27619 | +0.0006 [-0.0033, +0.0045] | 0.872 | +0.0040 | 0.774 | -0.183 |
| PLAY_TYPE | pts | 2024 | 27701 | +0.0015 [-0.0015, +0.0046] | 0.708 | +0.0033 | 0.765 | -0.101 |
| PLAY_TYPE | reb | 2023 | 27619 | +0.0016 [+0.0001, +0.0031] | 0.177 | +0.0009 | 0.794 | -0.054 |
| PLAY_TYPE | reb | 2024 | 27701 | +0.0008 [-0.0003, +0.0019] | 0.708 | +0.0011 | 0.786 | -0.053 |
| PLAY_TYPE | fg3m | 2023 | 27619 | -0.0001 [-0.0009, +0.0007] | 0.884 | +0.0007 | 0.787 | -0.024 |
| PLAY_TYPE | fg3m | 2024 | 27701 | +0.0004 [-0.0003, +0.0012] | 0.708 | +0.0003 | 0.782 | -0.006 |

## Verdict

```json
{
 "cells": {
  "USAGE_PACE/pts": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "USAGE_PACE/reb": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "USAGE_PACE/ast": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "USAGE_PACE/fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "EFFICIENCY/pts": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "EFFICIENCY/fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "PLAYMAKING/ast": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "REBOUNDING/reb": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "SHOT_PROFILE/pts": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "SHOT_PROFILE/fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "DEFENSE_CONTEXT/pts": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "DEFENSE_CONTEXT/fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "PLAY_TYPE/pts": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "PLAY_TYPE/reb": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  },
  "PLAY_TYPE/fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "cov80": true,
    "bias": true,
    "no_slice_worse": true,
    "cold_start": true
   },
   "slices_worse": {},
   "beats_placebo_by_0.003": false,
   "tests_ok": true,
   "pass_before_knockout": false
  }
 },
 "families": {
  "USAGE_PACE": false,
  "EFFICIENCY": false,
  "PLAYMAKING": false,
  "REBOUNDING": false,
  "SHOT_PROFILE": false,
  "DEFENSE_CONTEXT": false,
  "PLAY_TYPE": false
 },
 "close": true
}
```