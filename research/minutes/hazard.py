"""MINUTES_HAZARD: minutes as an on-court hazard over game time (docs/prereg/MINUTES_HAZARD.md).

Frozen design (constants final): 48 one-minute regulation slots, overtime appended 5 slots per
period (at most two overtimes carried). ``P(on_pt)`` is a logistic of the player's as-of rotation
habit ``h_p(t)`` plus one fixed shift per (fouls so far, period) cell, one per (|margin| bucket,
period, role) cell and a ``vac_min`` coefficient; minutes are the path sums of a 400-path
simulation. Every function below that feeds the pre-tip simulation takes AS-OF inputs only;
tonight's stints, fouls and margin enter solely through the training-label builders and the
oracle arm (H2), never through :func:`asof_inputs` or the H1/H0 simulators.

Unstated details are listed in the results section of the pre-registration, each chosen before
any score existed (see :data:`UNSTATED_DETAILS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
from scipy import optimize
from scipy.stats import norm

NREG = 48
N_OT_SLOTS = 5
MAX_OT = 2
NSLOT = NREG + N_OT_SLOTS * MAX_OT  # 58
N_PATHS = 400
SEED = 0
HABIT_GAMES = 10
HABIT_PC = 5.0
FOUL_GAMES = 40
FOUL_PC_MIN = 300.0
FOUL_CAP = 6
N_FOUL = FOUL_CAP + 1  # cells 0..6
N_PER = 5  # periods 1-4 and "overtime"
N_BUCKET = 3  # |margin| < 10, 10-19, 20+
N_ROLE = 3  # starter, first bench, deep bench
ROTATION_MIN10 = 12.0
P_EPS = 1e-4
MAXITER = 200
MARGIN_EDGES = (10.0, 20.0)
OT_TIE_BAND = 0.5
VAC_SCALE = 10.0

UNSTATED_DETAILS: tuple[str, ...] = (
    "Slot label Y_pt = fraction of the minute the player is on court (from stints); the "
    "simulation draws Bernoulli(P(on)) per slot. Training uses the fractional label.",
    "Roles: starter = as-of starter10 >= 0.5; first bench = min10 >= 12 (production "
    "ROTATION_MIN40 value); deep bench = the rest or NaN.",
    "Coach-season = (team_id, season) (team_coaches has no dates). The coach-season role profile "
    "is the plain mean of Y over prior player-games of that role in that team-season, falling "
    "back to the league-wide role profile over strictly earlier dates when the team-season has "
    "no prior row of the role, and to 0.5 if the league has none.",
    "Habit window = last 10 played games with the same team; the habit and the profile are "
    "regulation-slot quantities. Overtime slot habit = mean of the habit over regulation slots "
    "43-47. P(on) clipped to [1e-4, 1-1e-4] before the logit.",
    "Foul rate: per-minute, last 40 played games (any team), shrunk toward the training-window "
    "league rate (pf / minutes over all played rows before the block) with pseudo-count 300 "
    "minutes. Fouls so far are capped at 6 (cell 6 = fouled out or about to).",
    "Foul times: personal fouls (Foul events excluding technicals) from play-by-play, placed in "
    "the one-minute slot containing them; a player-game whose parsed count differs from pf (or "
    "with no pbp file) uses the declared fallback: pf spread over his on-court slots in "
    "proportion to Y; the fallback share is reported. Cell index = floor(fouls so far).",
    "Score margin at a slot start (training labels and H2) = score_diff of the last possession "
    "starting at or before the slot start, oriented to the player's team; 0 before the first.",
    "Pre-tip margin: Normal(mu, sd) with sd = sd of the final home margin over all games before "
    "the block, mu = sd * Phi^-1(p_home) from the OOF rung0 injury-Elo p (game_rows parquet); "
    "2022 games (no OOF p, used only to build props-training features) use the as-of MOV-Elo "
    "expected margin of the home team instead.",
    "Simulated margin = Brownian motion with drift mu/48 per slot and total variance sd^2 over "
    "the game (equivalent to a Brownian bridge to the final margin); margin used at a slot is "
    "its value at the slot start. Overtime occurs in a path iff |final margin| < 0.5; its "
    "margin is held at 0 (bucket < 10); a single overtime at most.",
    "H0 (habit only) has no margin and no foul state, hence no overtime slots.",
    "H2 (oracle) uses the actual margin path and the actual number of overtimes (<= 2).",
    "vac_min NaN (no pre-tip report) is treated as 0 in the logistic; coefficient fit on "
    "vac_min / 10.",
    "Fit = scipy L-BFGS-B, mean log loss over all valid training slots, zero start, "
    "maxiter 200, default tolerances, no regularisation (period intercepts of the foul and "
    "margin cells are not separately identified; only their sum is used).",
    "mv2_pi analogue (props features) = share of the 400 paths with minutes < 0.5 * min10 "
    "(the MINUTES_V2 short-event definition).",
    "Month blocks, MIN_TRAIN and the row set are exactly MINUTES_V2's (including 2022 "
    "warm-up blocks, simulated so the props model sees hazard features on its training rows).",
    "Slice 'returning from 3+ games' = the player's previous played game was >= 4 team games "
    "ago in the same season (missed >= 3 team games).",
    "PIT q10 / q90 = P(randomized PIT <= 0.10) and P(randomized PIT <= 0.90) (nominal 0.10 / "
    "0.90); 80% coverage = P(0.10 < PIT <= 0.90), as in MINUTES_V2.",
    "Missingness audit buckets of realised minutes: [0,10), [10,20), [20,30), [30,inf).",
)

# slot -> period (1..6) and period cell (0..4)
SLOT_PERIOD = np.array(
    [1 + t // 12 for t in range(NREG)] + [5 + (t - NREG) // N_OT_SLOTS for t in range(NREG, NSLOT)],
    dtype=np.int64,
)
SLOT_CELL = np.minimum(SLOT_PERIOD, 5) - 1


# --------------------------------------------------------------------------- label builders


def period_slots(period: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(base slot, n slots, period length in seconds) per period number."""
    p = np.asarray(period, dtype=np.int64)
    reg = p <= 4
    base = np.where(reg, 12 * (p - 1), NREG + N_OT_SLOTS * (p - 5))
    n = np.where(reg, 12, N_OT_SLOTS)
    plen = np.where(reg, 720.0, 300.0)
    return base, n, plen


def build_y(stints: pl.DataFrame, keys: pl.DataFrame) -> np.ndarray:
    """Fraction of every slot each (game_id, player_id) key is on court. ``stints`` carries
    game_id, period, start_clock, end_clock (seconds remaining) and players (list of ids)."""
    n = keys.height
    y = np.zeros((n, NSLOT), dtype=np.float64)
    ex = (
        stints.select("game_id", "period", "start_clock", "end_clock", "players")
        .explode("players", empty_as_null=True)
        .rename({"players": "player_id"})
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .filter(pl.col("period") <= 4 + MAX_OT)
        .join(
            keys.select("game_id", "player_id").with_row_index("rid"),
            on=["game_id", "player_id"],
            how="inner",
        )
    )
    if ex.height == 0:
        return y.astype(np.float32)
    rid = ex["rid"].to_numpy().astype(np.int64)
    base, nsl, plen = period_slots(ex["period"].to_numpy())
    e0 = plen - ex["start_clock"].cast(pl.Float64).to_numpy()
    e1 = plen - ex["end_clock"].cast(pl.Float64).to_numpy()
    for s in range(12):
        ov = np.clip(np.minimum(e1, 60.0 * (s + 1)) - np.maximum(e0, 60.0 * s), 0.0, None) / 60.0
        m = (ov > 0) & (s < nsl)
        if m.any():
            np.add.at(y, (rid[m], base[m] + s), ov[m])
    return np.asarray(np.clip(y, 0.0, 1.0), dtype=np.float32)


def parse_clock(clock: pl.Expr) -> pl.Expr:
    """'PT09M16.00S' -> seconds remaining."""
    return clock.str.extract(r"PT(\d+)M", 1).cast(pl.Float64) * 60.0 + clock.str.extract(
        r"M([\d.]+)S", 1
    ).cast(pl.Float64)


def load_foul_events(pbp_dir: Path, game_ids: list[str]) -> pl.DataFrame:
    """Personal-foul events (game_id, player_id, period, remaining seconds) from the raw pbp
    parquet files; games without a file contribute nothing (the declared fallback applies)."""
    frames: list[pl.DataFrame] = []
    for gid in game_ids:
        f = pbp_dir / f"{gid}.parquet"
        if not f.exists():
            continue
        d = pl.read_parquet(
            f, columns=["game_id", "period", "clock", "player_id", "action_type", "sub_type"]
        )
        d = d.filter(
            (pl.col("action_type") == "Foul")
            & ~pl.col("sub_type").fill_null("").str.contains("Technical")
            & pl.col("player_id").is_not_null()
            & (pl.col("player_id") > 0)
        )
        if d.height:
            frames.append(
                d.with_columns(parse_clock(pl.col("clock")).alias("remaining")).select(
                    "game_id", "player_id", "period", "remaining"
                )
            )
    if not frames:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "period": pl.Int64,
                "remaining": pl.Float64,
            }
        )
    return pl.concat(frames).with_columns(pl.col("player_id").cast(pl.Int64))


def build_fouls(
    events: pl.DataFrame, keys: pl.DataFrame, pf: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(fouls before each slot [n, NSLOT] float32, parsed_ok [n] bool). Rows whose parsed count
    equals ``pf`` use real timing; the rest use the per-minute fallback (pf spread over his
    on-court slots in proportion to ``y``)."""
    n = keys.height
    inc = np.zeros((n, NSLOT), dtype=np.float64)
    if events.height:
        ev = events.join(
            keys.select("game_id", "player_id").with_row_index("rid"),
            on=["game_id", "player_id"],
            how="inner",
        ).filter(pl.col("period") <= 4 + MAX_OT)
        if ev.height:
            base, nsl, plen = period_slots(ev["period"].to_numpy())
            el = plen - ev["remaining"].to_numpy()
            s = np.clip(np.floor(el / 60.0), 0, nsl - 1).astype(np.int64)
            np.add.at(inc, (ev["rid"].to_numpy().astype(np.int64), base + s), 1.0)
    parsed = inc.sum(axis=1)
    pf_f = np.nan_to_num(pf.astype(np.float64), nan=-1.0)
    ok = np.isclose(parsed, pf_f) & (pf_f >= 0)
    ysum = y.astype(np.float64).sum(axis=1)
    safe = np.where(ysum > 0, ysum, 1.0)
    fallback = np.where(
        (ysum > 0)[:, None], np.maximum(pf_f, 0.0)[:, None] * y / safe[:, None], 0.0
    )
    use = np.where(ok[:, None], inc, fallback)
    before = np.cumsum(use, axis=1) - use
    return before.astype(np.float32), ok


def build_margins(
    poss: pl.DataFrame, games: pl.DataFrame
) -> tuple[dict[str, int], np.ndarray, np.ndarray]:
    """(game_id -> row, margin [G, NSLOT] float32 home-perspective at every slot start,
    n_ot [G]). ``poss``: game_id, poss_idx, period, clock_start, off_team, score_diff."""
    gm = games.select("game_id", "home_team").unique("game_id").sort("game_id")
    gid_list = gm["game_id"].to_list()
    index = {g: i for i, g in enumerate(gid_list)}
    out = np.zeros((len(gid_list), NSLOT), dtype=np.float32)
    n_ot = np.zeros(len(gid_list), dtype=np.int64)
    p = (
        poss.join(gm, on="game_id", how="inner")
        .filter(pl.col("period") <= 4 + MAX_OT)
        .sort(["game_id", "poss_idx"])
    )
    if p.height == 0:
        return index, out, n_ot
    base, _, plen = period_slots(p["period"].to_numpy())
    elapsed = 60.0 * base + (plen - p["clock_start"].cast(pl.Float64).to_numpy())
    sign = np.where(p["off_team"].to_numpy() == p["home_team"].to_numpy(), 1.0, -1.0)
    sd = np.nan_to_num(p["score_diff"].cast(pl.Float64).to_numpy(), nan=0.0) * sign
    per = p["period"].to_numpy()
    gids = p["game_id"].to_numpy()
    starts = np.r_[0, np.nonzero(gids[1:] != gids[:-1])[0] + 1, len(gids)]
    bounds = 60.0 * np.arange(NSLOT)
    for a, b in zip(starts[:-1], starts[1:], strict=True):
        gi = index[str(gids[a])]
        el = np.maximum.accumulate(elapsed[a:b])
        idx = np.searchsorted(el, bounds, side="right") - 1
        out[gi] = np.where(idx >= 0, sd[a:b][np.clip(idx, 0, None)], 0.0)
        n_ot[gi] = int(min(max(int(per[a:b].max()) - 4, 0), MAX_OT))
    return index, out, n_ot


# --------------------------------------------------------------------------- as-of inputs


def role_of(min10: np.ndarray, starter10: np.ndarray) -> np.ndarray:
    """0 starter, 1 first bench, 2 deep bench (NaN -> deep bench). As-of inputs only."""
    st = np.nan_to_num(starter10, nan=0.0) >= 0.5
    mid = np.nan_to_num(min10, nan=0.0) >= ROTATION_MIN10
    return np.where(st, 0, np.where(mid, 1, 2)).astype(np.int64)


def _group_start(keys: list[np.ndarray]) -> np.ndarray:
    """Index of the first row of each row's group; rows must be sorted by ``keys``."""
    n = len(keys[0])
    new = np.zeros(n, dtype=bool)
    new[0] = True
    for k in keys:
        new[1:] |= k[1:] != k[:-1]
    first = np.where(new, np.arange(n), 0)
    return np.asarray(np.maximum.accumulate(first))


def _window_sum(a: np.ndarray, first: np.ndarray, window: int) -> np.ndarray:
    """Sum of the previous ``window`` rows of the same group (rows sorted so groups are
    contiguous; ``first`` = group start index per row). Fixed addition order and no global
    prefix sums, so a row's value depends only on its own group's earlier rows (byte-exact
    under edits to other groups or to later rows)."""
    n = a.shape[0]
    pos = np.arange(n)
    out = np.zeros(a.shape, dtype=np.float64)
    for j in range(1, window + 1):
        ok = (pos - j) >= first
        out[ok] += a[pos[ok] - j]
    return out


def _group_prefix(a: np.ndarray, first: np.ndarray) -> np.ndarray:
    """Exclusive prefix sum within each contiguous group (own group's earlier rows only)."""
    out = np.zeros(a.shape, dtype=np.float64)
    starts = np.unique(first)
    ends = np.r_[starts[1:], a.shape[0]]
    for s0, e0 in zip(starts, ends, strict=True):
        c = np.cumsum(a[s0:e0], axis=0)
        out[s0 + 1 : e0] = c[:-1]
    return out


@dataclass
class AsOf:
    """As-of inputs, aligned to the input row order."""

    habit_logit: np.ndarray  # [n, NSLOT] float32
    fnum40: np.ndarray  # fouls over the last 40 played games
    fden40: np.ndarray  # minutes over the same games
    role: np.ndarray
    k_habit: np.ndarray  # same-team games in the habit window


def asof_inputs(
    pid: np.ndarray,
    tid: np.ndarray,
    season: np.ndarray,
    gid: np.ndarray,
    date: np.ndarray,
    minutes: np.ndarray,
    pf: np.ndarray,
    role: np.ndarray,
    y: np.ndarray,
) -> AsOf:
    """Habit logits and foul-rate sums for every row from STRICTLY PRIOR games only.

    ``date`` is an integer day number, ``gid`` an integer game key (ties on a date broken by
    it). All players' rows (not only eligible ones) must be passed: they are the history."""
    n = len(pid)
    yreg = y[:, :NREG].astype(np.float64)
    # ---- habit: last 10 played games with the same team, prior only
    o = np.lexsort((gid, date, tid, pid))
    first = _group_start([pid[o], tid[o]])
    pos = np.arange(n)
    lo = np.maximum(first, pos - HABIT_GAMES)
    s_win = _window_sum(yreg[o], first, HABIT_GAMES)
    k_o = (pos - lo).astype(np.float64)
    # ---- role profiles (coach-season = team-season), prior games only
    key = np.stack([gid, tid, role], axis=1).astype(np.int64)
    uk, inv = np.unique(key, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    order = np.argsort(inv, kind="stable")
    sb = np.r_[0, np.nonzero(np.diff(inv[order]) != 0)[0] + 1]
    ysum = np.add.reduceat(yreg[order], sb, axis=0)
    cnt = np.diff(np.r_[sb, n]).astype(np.float64)
    first_row = order[sb]
    k_tid, k_role = uk[:, 1], uk[:, 2]
    k_season = season[first_row]
    k_date = date[first_row]
    k_gid = uk[:, 0]
    ko = np.lexsort((k_gid, k_date, k_role, k_season, k_tid))
    kfirst = _group_start([k_tid[ko], k_season[ko], k_role[ko]])
    ts_sum = np.zeros_like(ysum)
    ts_cnt = np.zeros(len(ko))
    ts_sum[ko] = _group_prefix(ysum[ko], kfirst)
    ts_cnt[ko] = _group_prefix(cnt[ko], kfirst)
    # league role profile over strictly earlier dates
    dk = np.stack([role, date], axis=1).astype(np.int64)
    udk, dinv = np.unique(dk, axis=0, return_inverse=True)
    dinv = np.asarray(dinv).reshape(-1)
    do = np.argsort(dinv, kind="stable")
    db = np.r_[0, np.nonzero(np.diff(dinv[do]) != 0)[0] + 1]
    dsum = np.add.reduceat(yreg[do], db, axis=0)
    dcnt = np.diff(np.r_[db, n]).astype(np.float64)
    dord = np.lexsort((udk[:, 1], udk[:, 0]))
    dfirst = _group_start([udk[dord, 0]])
    lg_sum = np.zeros_like(dsum)
    lg_cnt = np.zeros(len(dord))
    lg_sum[dord] = _group_prefix(dsum[dord], dfirst)
    lg_cnt[dord] = _group_prefix(dcnt[dord], dfirst)
    r_ts_sum, r_ts_cnt = ts_sum[inv], ts_cnt[inv]
    r_lg_sum, r_lg_cnt = lg_sum[dinv], lg_cnt[dinv]
    prof = np.where(
        (r_ts_cnt > 0)[:, None],
        r_ts_sum / np.maximum(r_ts_cnt, 1.0)[:, None],
        np.where((r_lg_cnt > 0)[:, None], r_lg_sum / np.maximum(r_lg_cnt, 1.0)[:, None], 0.5),
    )
    s_row = np.zeros((n, NREG))
    k_row = np.zeros(n)
    s_row[o] = s_win
    k_row[o] = k_o
    h = (s_row + HABIT_PC * prof) / (k_row[:, None] + HABIT_PC)
    h_ot = h[:, 43:NREG].mean(axis=1, keepdims=True)
    h_full = np.concatenate([h, np.repeat(h_ot, NSLOT - NREG, axis=1)], axis=1)
    h_full = np.clip(h_full, P_EPS, 1.0 - P_EPS)
    logit = np.log(h_full / (1.0 - h_full)).astype(np.float32)
    # ---- foul rate inputs: last 40 played games (any team)
    o2 = np.lexsort((gid, date, pid))
    first2 = _group_start([pid[o2]])
    pf0 = np.nan_to_num(pf.astype(np.float64), nan=0.0)
    mn0 = np.where(np.isnan(pf), 0.0, np.nan_to_num(minutes.astype(np.float64), nan=0.0))
    num = np.zeros(n)
    den = np.zeros(n)
    num[o2] = _window_sum(pf0[o2], first2, FOUL_GAMES)
    den[o2] = _window_sum(mn0[o2], first2, FOUL_GAMES)
    return AsOf(logit, num, den, role.astype(np.int64), k_row)


def foul_rates(num40: np.ndarray, den40: np.ndarray, league_rate: float) -> np.ndarray:
    """Per-minute foul rate, prior 40 games shrunk toward the league rate (pseudo-count 300 min)."""
    return np.asarray((num40 + FOUL_PC_MIN * league_rate) / (den40 + FOUL_PC_MIN))


# --------------------------------------------------------------------------- the model


@dataclass
class HazardParams:
    foul: np.ndarray  # [N_FOUL, N_PER]
    margin: np.ndarray  # [N_BUCKET, N_PER, N_ROLE]
    beta: float
    n_slots: int
    n_iter: int
    converged: bool


def margin_bucket(m: np.ndarray) -> np.ndarray:
    a = np.abs(m)
    return (a >= MARGIN_EDGES[0]).astype(np.int8) + (a >= MARGIN_EDGES[1]).astype(np.int8)


def fit_hazard(
    logit_h: np.ndarray,
    y: np.ndarray,
    valid: np.ndarray,
    fouls: np.ndarray,
    margin: np.ndarray,
    role: np.ndarray,
    vac: np.ndarray,
) -> HazardParams:
    """One L-BFGS logistic fit on slot-level rows. Arrays are [rows, NSLOT] except ``role``
    and ``vac`` [rows]; ``margin`` is from the player's team's perspective (only |.| is used)."""
    fcell = np.minimum(np.floor(fouls), FOUL_CAP).astype(np.int64)
    idx_f_full = fcell * N_PER + SLOT_CELL[None, :]
    bucket = margin_bucket(margin).astype(np.int64)
    idx_m_full = (bucket * N_PER + SLOT_CELL[None, :]) * N_ROLE + role[:, None]
    vac_full = np.broadcast_to((np.nan_to_num(vac, nan=0.0) / VAC_SCALE)[:, None], valid.shape)
    sel = valid
    off = logit_h[sel].astype(np.float64)
    yy = y[sel].astype(np.float64)
    if_ = idx_f_full[sel]
    im = idx_m_full[sel]
    vv = vac_full[sel].astype(np.float64)
    n = float(len(yy))
    nf, nm = N_FOUL * N_PER, N_BUCKET * N_PER * N_ROLE

    def fg(w: np.ndarray) -> tuple[float, np.ndarray]:
        z = off + w[:nf][if_] + w[nf : nf + nm][im] + w[nf + nm] * vv
        loss = float(np.mean(np.maximum(z, 0.0) + np.log1p(np.exp(-np.abs(z))) - yy * z))
        r = (1.0 / (1.0 + np.exp(-z)) - yy) / n
        g = np.concatenate(
            [
                np.bincount(if_, weights=r, minlength=nf),
                np.bincount(im, weights=r, minlength=nm),
                [float(r @ vv)],
            ]
        )
        return loss, g

    res = optimize.minimize(
        fg, np.zeros(nf + nm + 1), jac=True, method="L-BFGS-B", options={"maxiter": MAXITER}
    )
    w = res.x
    return HazardParams(
        w[:nf].reshape(N_FOUL, N_PER),
        w[nf : nf + nm].reshape(N_BUCKET, N_PER, N_ROLE),
        float(w[nf + nm]),
        int(n),
        int(res.nit),
        bool(res.success),
    )


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-z)))


def simulate_game(
    mode: str,
    logit_h: np.ndarray,
    role: np.ndarray,
    vac: np.ndarray,
    rate: np.ndarray,
    mu_home: float,
    sd: float,
    params: HazardParams | None,
    rng_margin: np.random.Generator,
    rng_play: np.random.Generator,
    n_paths: int = N_PATHS,
    margin_actual: np.ndarray | None = None,
    n_ot_actual: int = 0,
) -> np.ndarray:
    """Simulated minutes [P, n_paths] (float32 path sums) for the players of one game.

    ``h1``: pre-tip margin distribution + simulated fouls (as-of inputs only);
    ``h0``: habit only; ``h2``: oracle, tonight's actual margin path (``margin_actual``,
    home perspective) and overtime count (a leak by construction)."""
    p_n = logit_h.shape[0]
    mins = np.zeros((p_n, n_paths), dtype=np.float32)
    if mode == "h0":
        ph = sigmoid(logit_h[:, :NREG].astype(np.float64))
        for t in range(NREG):
            mins += (rng_play.random((p_n, n_paths), dtype=np.float32) < ph[:, t, None]).astype(
                np.float32
            )
        return mins
    if params is None:
        raise ValueError("h1/h2 need fitted params")
    if mode == "h1":
        inc = rng_margin.standard_normal((n_paths, NREG)) * np.sqrt(1.0 / NREG)
        cum = np.concatenate([np.zeros((n_paths, 1)), np.cumsum(inc, axis=1)], axis=1)
        w_all = mu_home * (np.arange(NREG + 1) / NREG)[None, :] + sd * cum  # [paths, 49]
        mh = np.zeros((n_paths, NSLOT))
        mh[:, :NREG] = w_all[:, :NREG]
        ot_mask = np.abs(w_all[:, NREG]) < OT_TIE_BAND
        n_slots_sim = NREG + N_OT_SLOTS
        ot_slots = np.zeros((n_paths, NSLOT), dtype=bool)
        ot_slots[:, NREG:n_slots_sim] = ot_mask[:, None]
    elif mode == "h2":
        if margin_actual is None:
            raise ValueError("h2 needs the actual margin path")
        mh = np.broadcast_to(margin_actual.astype(np.float64)[None, :], (n_paths, NSLOT)).copy()
        n_slots_sim = NREG + N_OT_SLOTS * int(n_ot_actual)
        ot_slots = np.zeros((n_paths, NSLOT), dtype=bool)
        ot_slots[:, NREG:n_slots_sim] = True
    else:
        raise ValueError(f"unknown mode {mode!r}")
    fl = np.zeros((p_n, n_paths), dtype=np.int8)
    vac_term = (params.beta * np.nan_to_num(vac, nan=0.0) / VAC_SCALE)[:, None]
    lam_rate = rate[:, None]
    for t in range(n_slots_sim):
        c = int(SLOT_CELL[t])
        bucket = margin_bucket(mh[:, t])  # [paths], sign-symmetric
        if t >= NREG and mode == "h1":
            bucket = np.zeros(n_paths, dtype=np.int8)
        z = (
            logit_h[:, t, None]
            + params.foul[fl, c]
            + params.margin[bucket[None, :], c, role[:, None]]
            + vac_term
        )
        u = rng_play.random((p_n, n_paths), dtype=np.float32)
        on = u < sigmoid(z)
        if t >= NREG:
            on &= ot_slots[None, :, t]
        mins += on
        nf = rng_play.poisson(lam_rate * on).astype(np.int8)
        fl = np.minimum(fl + nf, FOUL_CAP).astype(np.int8)
    return mins


def quantile_grid(sums: np.ndarray, taus: np.ndarray) -> np.ndarray:
    """199-quantile grid of the path sums per player, clipped to [0, 60]."""
    q = np.quantile(sums.astype(np.float64), taus, axis=1).T
    return np.asarray(np.clip(q, 0.0, 60.0))


def margin_params_from_p(p_home: float, sd: float) -> float:
    """Home mean margin whose Normal(mu, sd) has P(margin > 0) = p_home."""
    return float(sd * norm.ppf(np.clip(p_home, 1e-6, 1.0 - 1e-6)))
