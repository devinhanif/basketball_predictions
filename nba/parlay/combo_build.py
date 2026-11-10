"""Server-side builders for the combo payload (``docs/contracts/combo_export_v1.md``).

Numpy lives here, never in ``combo.py``: this module turns stored production forecasts into
CALIBRATED integer survival grids and packs them. Nothing here reads a price or a credential, and no
model is refit: the calibration map is the frozen LINE_CALIBRATION map (``line_cal.py``), applied
at every rung of the grid.

Calibration across the grid. For a player-stat with raw survival ``S(k) = P(Y >= k)`` (k = 1..K,
the production integer grid) the map is applied at line ``k - 0.5`` with the row's own median /
spread (from the q_grid) and starter flag, but only inside ``|z| <= Z_BAND`` (where the map was
fitted); outside, the logit offset is held at the band edge (see ``calibrate_surv_banded``). Values
at or beyond the 199-sample resolution pass through, then the grid is made monotone.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from nba.parlay.combo import SCHEMA
from nba.parlay.line_cal import EPS, SPREAD_MIN, CalMap, logit, sigmoid

F64 = NDArray[np.float64]
RES = 1.0 / 199.0
BOOK = "draftkings"
SURV_DIGITS = 4
Z_BAND = 1.0  # |z| of a rung against the median where the map is applied directly


def calibrate_surv_banded(
    cal: CalMap,
    stat: str,
    raw: F64,
    med: F64,
    spr: F64,
    st10: F64,
    book: str = BOOK,
) -> tuple[F64, NDArray[np.int64], NDArray[np.int64]]:
    """Calibrated, monotone survival grid and the 1-based rung range ``[k_lo, k_hi]`` where the map
    was applied directly. ``raw`` is (N, K), column j = P(Y >= j + 1).

    The map is applied at every rung, but its logit slope is below 1 (it shrinks overconfident
    probabilities toward 0.5), so applied far from the lines it was fitted on it fattens both tails:
    on 2024 the PIT normal score of the fully mapped grids had sd 0.63-0.90 against 1.07-1.13 for the
    raw grids. Posted main lines sit at ``|z| <= 1`` (99th percentile 0.84-0.99), z = (line - median) /
    spread. So the map is applied only for ``|z| <= Z_BAND``; beyond the band the logit offset
    (calibrated minus raw) is HELD at its value at the band edge, which keeps the grid monotone and
    the tails tails. Beyond the 199-sample resolution (``S <= 1/199`` or ``S >= 1 - 1/199``) raw
    passes through.
    """
    n, k_max = raw.shape
    ks = np.arange(1, k_max + 1, dtype=float)
    lines = ks[None, :] - 0.5
    z = (lines - med[:, None]) / np.maximum(spr[:, None], SPREAD_MIN)
    inband = np.abs(z) <= Z_BAND
    has = inband.any(axis=1)
    near = np.argmin(np.abs(z), axis=1)
    k_lo = np.where(has, np.argmax(inband, axis=1), near)
    k_hi = np.where(has, k_max - 1 - np.argmax(inband[:, ::-1], axis=1), near)
    pm = np.clip(raw, EPS, 1 - EPS)
    cal_p = cal.apply_many(
        book,
        stat,
        pm.ravel(),
        np.broadcast_to(lines, (n, k_max)).ravel(),
        np.repeat(med, k_max),
        np.repeat(spr, k_max),
        np.repeat(st10, k_max),
    ).reshape(n, k_max)
    delta = logit(cal_p) - logit(pm)
    idx = np.clip(np.arange(k_max)[None, :], k_lo[:, None], k_hi[:, None])
    held = np.take_along_axis(delta, idx, axis=1)
    out = np.where((raw <= RES) | (raw >= 1.0 - RES), raw, sigmoid(logit(pm) + held))
    surv = np.asarray(np.minimum.accumulate(np.clip(out, 0.0, 1.0), axis=1))
    return surv, (k_lo + 1).astype(np.int64), (k_hi + 1).astype(np.int64)


def calibrate_surv(
    cal: CalMap, stat: str, raw: F64, med: F64, spr: F64, st10: F64, book: str = BOOK
) -> F64:
    return calibrate_surv_banded(cal, stat, raw, med, spr, st10, book)[0]


def pit_z(surv: F64, y: F64, u: F64) -> F64:
    """Normal score of the randomized PIT of integer outcomes ``y`` on survival grids ``surv``
    (N, K), uniform draws ``u``. ``P(Y < y) = 1 - S(y)``, ``P(Y <= y) = 1 - S(y + 1)``."""
    from scipy import stats

    n, k_max = surv.shape
    s_pad = np.concatenate([np.ones((n, 1)), surv, np.zeros((n, 1))], axis=1)  # index k = 0..K+1
    yi = np.clip(y.astype(int), 0, k_max)
    rows = np.arange(n)
    lo = 1.0 - s_pad[rows, yi]
    hi = 1.0 - s_pad[rows, yi + 1]
    v = lo + u * (hi - lo)
    return np.asarray(stats.norm.ppf(np.clip(v, 1e-9, 1 - 1e-9)))


def trim_surv(row: F64, digits: int = SURV_DIGITS) -> tuple[int, list[float]]:
    """(k0, values) with ``S(k) = values[k - k0]``; 1 below k0 and 0 past the end. Rounded and
    made monotone so the stored grid is a valid survival function."""
    r = np.round(np.minimum.accumulate(np.clip(row, 0.0, 1.0)), digits)
    below = np.nonzero(r < 1.0)[0]
    if below.size == 0:
        return len(r) + 1, []
    first = int(below[0])
    nz = np.nonzero(r > 0.0)[0]
    if nz.size == 0:
        return first + 1, []
    last = int(nz[-1])
    return first + 1, [float(v) for v in r[first : last + 1]]


def build_stat_entry(
    cal_surv_row: F64, raw_surv_row: F64, main_line: float | None
) -> dict[str, Any]:
    k0, vals = trim_surv(cal_surv_row)
    entry: dict[str, Any] = {"k0": k0, "surv": vals, "main_line": main_line}
    if main_line is not None:
        k = int(math.floor(main_line)) + 1
        entry["raw_at_main"] = round(float(raw_surv_row[k - 1]), SURV_DIGITS)
    return entry


def build_shared(
    corr_rows: Sequence[Sequence[Any]],
    script_params: Sequence[Sequence[Any]],
    script_status: Mapping[str, Any],
    provenance: Mapping[str, Any],
    definitions: Mapping[str, Any],
) -> dict[str, Any]:
    """The night-level block shared by every game (correlations, script parameters, stamps)."""
    return {
        "corr": [[r[0], r[1], r[2], round(float(r[3]), 4), int(r[4])] for r in corr_rows],
        "scripts": {
            "definitions": dict(definitions),
            "params": [
                [r[0], r[1], r[2], round(float(r[3]), 4), round(float(r[4]), 4), int(r[5])]
                for r in script_params
            ],
            "status": dict(script_status),
        },
        "provenance": dict(provenance),
    }


def build_game_payload(
    *,
    game_id: str,
    date: str,
    home_team: int,
    away_team: int,
    tip_utc: str,
    made_at: str,
    mu_m: float,
    sd_m: float,
    mu_t: float,
    sd_t: float,
    players: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One game's block. ``players``: ``{player_id, name, team, p_play, starter, stats: {stat:
    {"cal": F64 row, "raw": F64 row, "main_line": float | None}}}``. Rounding is the contract's
    (4 decimals for probabilities, 2 for game parameters)."""
    out_players = []
    for p in players:
        stats = {
            s: build_stat_entry(v["cal"], v["raw"], v.get("main_line")) for s, v in p["stats"].items()
        }
        out_players.append(
            {
                "player_id": int(p["player_id"]),
                "name": str(p["name"]),
                "team": int(p["team"]),
                "p_play": round(float(p["p_play"]), SURV_DIGITS),
                "starter": int(p["starter"]),
                "stats": stats,
            }
        )
    return {
        "game_id": game_id,
        "date": date,
        "home_team": home_team,
        "away_team": away_team,
        "tip_utc": tip_utc,
        "made_at": made_at,
        "game": {
            "mu_m": round(mu_m, 2),
            "sd_m": round(sd_m, 2),
            "mu_t": round(mu_t, 2),
            "sd_t": round(sd_t, 2),
        },
        "players": out_players,
    }


def nightly(shared: Mapping[str, Any], games: Sequence[Mapping[str, Any]], night: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "night": night, "shared": dict(shared), "games": list(games)}
