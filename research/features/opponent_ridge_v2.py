"""As-of opponent-adjusted ridge feature variants (experiment 4, ``ridge_v2``).

Group (i) of ``research.props.context_features_v2`` (no-intercept ridge on player and opponent-team
effects for per-36 rates, refit monthly) carried almost all of the ctxres_v2 gain. This module
rebuilds that estimator as a general, strictly as-of, block-structured weighted ridge and adds the
variants listed below. Every variant is refit once per calendar month on played rows dated
STRICTLY BEFORE the month's first day (``LOOKBACK_DAYS`` window) and the month's rows read the
coefficients; nothing from the target game (minutes, box line, lineup) is read.

Objective (per stat, per month)::

    min_beta  sum_i w_i (y_i - mu - x_i beta)^2 + sum_b alpha_b ||beta_b||^2

``y`` = per-36 rate, ``w`` = minutes x time decay, ``mu`` = weighted mean of ``y``. The reference
configuration (:class:`RidgeCfg` defaults) reproduces the experiment-2 estimator.

Variants (components of :class:`RidgeCfg`)::

    r1  recency weights: w *= 0.5 ** (age_days / halflife)          (``halflife``)
    r2  opponent x position-group (or archetype) deviations         (``opp_grp``)
    r3a venue split: home term, opponent x venue, player x venue    (``venue``)
    r3b pace-adjusted target: y / (game pace / window mean pace)    (``pace``)
    r4  player x "top-usage teammate OUT" interaction               (``star``)
    r5  hierarchical player prior: group (position/archetype) mean + penalised deviation (``hier``)
    r6  per-stat penalties (``alpha_p``, ``alpha_o``) tuned on the selection season only

Output components per (stat, cfg): ``p`` player effect (+ group effect for r5), ``o`` opponent
effect (centred on the league), ``ox`` opponent interaction total (r2/r3a), ``px`` player-venue
total (r3a), ``tm`` teammate-context total (r4). ``rate`` (mu + all terms) is returned for the
ridge-level tuning proxy but is not exported as a model feature.

KNOWN LEAK IN EXPERIMENT 2 (fixed here): ``opp_adjusted_ridge`` in ``context_features_v2``
computed features only for rows with ``minutes >= 5`` TONIGHT, so the feature was NULL exactly
for players who barely played (mean pts 0.86 vs 11.76 on non-null rows). This module predicts for
every row regardless of tonight's minutes; ``missing_by_tonight_minutes`` audits it.

Temporal rules: training rows are strictly earlier than the refit month start; the star-OUT
regressor is fit on past box-score absences but READ at prediction time from the pre-tip OUT report
set (``flagged``), never from tonight's box score; archetypes use only window rates and static
demographics; pace-adjustment uses past games' pace. Season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import scipy.linalg as sla
import scipy.sparse as sp
import yaml

from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
)
from research.coldstart.archetypes import encode_position_fractions
from research.props.context_features_v2 import (
    EXPORT_SEASONS,
    MAX_SEASON,
    RIDGE_ALPHA,
    RIDGE_LOOKBACK_DAYS,
    RIDGE_MIN_ROWS,
    load_inputs_v2,
)

SEED = 20261008
SELECT_SEASON = 2023
MIN_TRAIN_MINUTES = 5.0
LOOKBACK_DAYS = RIDGE_LOOKBACK_DAYS  # 540, same as experiment 2 (held fixed across variants)
MIN_ROWS = RIDGE_MIN_ROWS  # 3000
K_ARCH = 6  # archetype clusters (fixed, pre-registered)
USAGE_MIN_GAMES = 10
HALFLIFE_GRID: tuple[float | None, ...] = (None, 30.0, 60.0, 120.0, 240.0, 480.0)
ALPHA_P_GRID: tuple[float, ...] = (2.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, 400.0)
ALPHA_O_GRID: tuple[float, ...] = (10.0, 50.0, 250.0, 1000.0, 4000.0, 16000.0)
PENALTY_GRID: tuple[float, ...] = (50.0, 200.0, 800.0)  # r2/r3a/r4 interaction penalties
ALPHA_G_GRID: tuple[float, ...] = (1.0, 5.0, 25.0)  # r5 group-mean penalty
PROXY_MIN_GAIN = 0.002  # proxy-selected combo keeps a component if rel. MSE gain >= 0.2%
COMPONENT_ORDER = ("p", "o", "ox", "px", "tm")
PLAYER_STRIDE = 4_000_000  # (game_int * stride + player_id) played-set keys


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class RidgeCfg:
    """One ridge variant for ONE stat. Defaults = the experiment-2 estimator."""

    halflife: float | None = None  # r1: days; None = flat weights
    alpha_p: float = RIDGE_ALPHA  # player penalty (r6 tunes it)
    alpha_o: float = RIDGE_ALPHA  # opponent penalty (r6 tunes it)
    hier: str | None = None  # r5: None | "pos" | "arch"
    alpha_g: float = 5.0  # r5 group-mean penalty
    opp_grp: str | None = None  # r2: None | "pos" | "arch"
    alpha_og: float = 200.0  # r2 opponent x group deviation penalty
    venue: bool = False  # r3a
    alpha_v: float = 200.0  # r3a deviation penalty
    pace: bool = False  # r3b
    star: bool = False  # r4
    alpha_pz: float = 200.0  # r4 player x star-out penalty
    alpha_z: float = 1.0  # r4 shared star-out penalty

    def canonical(self) -> RidgeCfg:
        """Zero the penalties of switched-off blocks so equal estimators compare/hash equal."""
        return dataclasses.replace(
            self,
            alpha_g=self.alpha_g if self.hier else 0.0,
            alpha_og=self.alpha_og if self.opp_grp else 0.0,
            alpha_v=self.alpha_v if self.venue else 0.0,
            alpha_pz=self.alpha_pz if self.star else 0.0,
            alpha_z=self.alpha_z if self.star else 0.0,
        )

    def design_key(self) -> tuple[str | None, str | None, bool, bool]:
        return (self.hier, self.opp_grp, self.venue, self.star)

    def components(self) -> tuple[str, ...]:
        comps = ["p", "o"]
        if self.opp_grp or self.venue:
            comps.append("ox")
        if self.venue:
            comps.append("px")
        if self.star:
            comps.append("tm")
        return tuple(comps)

    def tag(self) -> str:
        blob = json.dumps(dataclasses.asdict(self.canonical()), sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]


REF_CFG = RidgeCfg()


def column_name(stat: str, cfg: RidgeCfg, comp: str) -> str:
    return f"rg_{cfg.tag()}_{comp}_{stat}"


# --------------------------------------------------------------------------- inputs


@dataclass
class RidgeInputs:
    """Row-aligned arrays for every candidate row (one per PLAYED player-game, sorted by date).

    ``minutes``/``y``/``usage`` of row r are used only when r is a TRAINING row (strictly before
    the refit month); they are never read for prediction rows."""

    game_id: np.ndarray
    player_id: np.ndarray
    team_id: np.ndarray
    opp_id: np.ndarray
    date: np.ndarray  # datetime64[D]
    season: np.ndarray
    minutes: np.ndarray
    y: dict[str, np.ndarray]
    pos: np.ndarray  # pos_code, -1 unknown
    is_home: np.ndarray
    pace: np.ndarray  # game possessions per team (box estimate), NaN unknown
    usage: np.ndarray  # fga + 0.44 fta + tov
    flagged: dict[str, set[int]]  # game_id -> players OUT on the usable pre-tip report
    demo: dict[int, tuple[float, float, str]]  # player -> (height_in, weight_lb, position)

    @property
    def n(self) -> int:
        return len(self.game_id)

    def month_index(self) -> np.ndarray:
        return self.date.astype("datetime64[M]")


def build_inputs(
    v1: pl.DataFrame,
    pgs: pl.DataFrame,
    flagged: dict[str, set[int]],
    demo: dict[int, tuple[float, float, str]] | None = None,
) -> RidgeInputs:
    """Assemble :class:`RidgeInputs` from the v1 feature frame and the box-score table.

    ``pgs`` needs ``game_id, player_id, team_id, minutes, fga, fta, tov, oreb`` (DNP rows may be
    present: they never enter ``v1`` because v1 is played-only)."""
    pg = pgs.with_columns(pl.col("player_id").cast(pl.Int64))
    team_poss = (
        pg.with_columns(
            (
                pl.col("fga").fill_null(0)
                + 0.44 * pl.col("fta").fill_null(0)
                + pl.col("tov").fill_null(0)
                - pl.col("oreb").fill_null(0)
            ).alias("_p")
        )
        .group_by(["game_id", "team_id"])
        .agg(pl.col("_p").sum())
        .group_by("game_id")
        .agg(pl.col("_p").mean().alias("pace"))
    )
    use = pg.select(
        "game_id",
        "player_id",
        "minutes",
        (
            pl.col("fga").fill_null(0)
            + 0.44 * pl.col("fta").fill_null(0)
            + pl.col("tov").fill_null(0)
        ).alias("usage"),
    )
    d = (
        v1.with_columns(pl.col("player_id").cast(pl.Int64))
        .select(
            "game_id",
            "player_id",
            "team_id",
            "opp_id",
            "game_date",
            "season",
            "pos_code",
            "is_home_f",
            *PROP_STATS,
        )
        .join(use, on=["game_id", "player_id"], how="left")
        .join(team_poss, on="game_id", how="left")
        .sort(["game_date", "game_id", "player_id"])
    )
    return RidgeInputs(
        game_id=d["game_id"].to_numpy(),
        player_id=d["player_id"].to_numpy().astype(np.int64),
        team_id=d["team_id"].to_numpy().astype(np.int64),
        opp_id=d["opp_id"].to_numpy().astype(np.int64),
        date=d["game_date"].to_numpy().astype("datetime64[D]"),
        season=d["season"].to_numpy().astype(np.int64),
        minutes=d["minutes"].cast(pl.Float64).to_numpy(),
        y={s: d[s].cast(pl.Float64).to_numpy() for s in PROP_STATS},
        pos=d["pos_code"].fill_null(-1).to_numpy().astype(np.int64),
        is_home=d["is_home_f"].fill_null(0).to_numpy().astype(np.int64),
        pace=d["pace"].cast(pl.Float64).to_numpy(),
        usage=d["usage"].cast(pl.Float64).to_numpy(),
        flagged=flagged,
        demo=demo or {},
    )


# --------------------------------------------------------------------------- solver


def solve_spd(
    gram: np.ndarray, rhs: np.ndarray, penalty: np.ndarray, backend: str = "numpy"
) -> np.ndarray:
    """Solve ``(gram + diag(penalty)) beta = rhs`` (symmetric positive definite).

    ``backend`` ``numpy`` (scipy Cholesky, the default) or ``torch``: float64 Cholesky on CUDA when
    a GPU is present; without CUDA the torch request falls back to numpy (the CPU torch LAPACK
    segfaults on some macOS builds, and numpy is as fast at these sizes: K <= ~3500). Both paths
    solve the same system to ~1e-10 (tested)."""
    if backend == "torch":
        import torch

        if torch.cuda.is_available():
            dev = "cuda"
            a = torch.as_tensor(gram, dtype=torch.float64, device=dev)
            a = a + torch.diag(torch.as_tensor(penalty, dtype=torch.float64, device=dev))
            b = torch.as_tensor(rhs, dtype=torch.float64, device=dev).reshape(len(rhs), -1)
            sol = torch.cholesky_solve(b, torch.linalg.cholesky(a))
            return np.asarray(sol.cpu().numpy()).reshape(rhs.shape)
    a_np = gram + np.diag(penalty)
    cf = sla.cho_factor(a_np, lower=True, check_finite=False)
    return np.asarray(sla.cho_solve(cf, rhs, check_finite=False))


# --------------------------------------------------------------------------- blocks


@dataclass
class _Block:
    name: str
    pen: str  # which alpha applies
    comp: str  # output component
    keys: np.ndarray  # per row, -1 = not applicable
    vals_fit: np.ndarray
    vals_pred: np.ndarray
    center: bool = False
    vocab: np.ndarray | None = None

    def fit_vocab(self, tr: np.ndarray) -> None:
        use = tr & (self.keys >= 0) & (self.vals_fit != 0.0)
        self.vocab = np.unique(self.keys[use])

    def lookup(self, rows: np.ndarray) -> np.ndarray:
        assert self.vocab is not None
        k = self.keys[rows]
        if len(self.vocab) == 0:
            return np.full(len(k), -1, dtype=np.int64)
        pos = np.searchsorted(self.vocab, k)
        pos_c = np.minimum(pos, len(self.vocab) - 1)
        ok = (k >= 0) & (self.vocab[pos_c] == k)
        return np.where(ok, pos_c, -1).astype(np.int64)


def _pen_value(cfg: RidgeCfg, key: str) -> float:
    return {
        "p": cfg.alpha_p,
        "o": cfg.alpha_o,
        "g": cfg.alpha_g,
        "og": cfg.alpha_og,
        "h": 1.0,
        "v": cfg.alpha_v,
        "z": cfg.alpha_z,
        "pz": cfg.alpha_pz,
    }[key]


# --------------------------------------------------------------------------- engine


class MonthlyRidge:
    """Run any set of (stat, :class:`RidgeCfg`) fits month by month, strictly as-of."""

    def __init__(self, inp: RidgeInputs, backend: str = "numpy") -> None:
        self.inp = inp
        self.backend = backend
        self.months = inp.month_index()
        self.days = inp.date.astype("datetime64[D]").astype(np.int64)
        gi = np.unique(inp.game_id, return_inverse=True)[1].astype(np.int64)
        self._played = np.sort(gi * PLAYER_STRIDE + inp.player_id)
        self._gint = gi
        self.pos_grp = np.where(inp.pos >= 0, inp.pos, 3).astype(np.int64)
        self._arch_cache: dict[Any, np.ndarray] = {}

    # ----- window-level helpers

    def _archetypes(self, mth: np.datetime64, tr: np.ndarray) -> np.ndarray:
        """Per-row archetype label (as-of window rates + static demographics); unknown = K_ARCH."""
        if mth in self._arch_cache:
            return self._arch_cache[mth]
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        inp = self.inp
        out = np.full(inp.n, K_ARCH, dtype=np.int64)
        pids = np.unique(inp.player_id)
        known = [int(p) for p in pids if int(p) in inp.demo]
        if len(known) < 2:
            self._arch_cache[mth] = out
            return out
        m = inp.minutes[tr]
        ok = np.isfinite(m) & (m > 0)
        pid_tr = inp.player_id[tr][ok]
        w = m[ok]
        rates = np.column_stack([inp.y[s][tr][ok] / w * 36.0 for s in PROP_STATS])
        uniq, inv = np.unique(pid_tr, return_inverse=True)
        wsum = np.bincount(inv, weights=w, minlength=len(uniq))
        rsum = np.column_stack(
            [np.bincount(inv, weights=w * rates[:, j], minlength=len(uniq)) for j in range(4)]
        )
        league = np.average(rates, axis=0, weights=w) if len(w) else np.zeros(4)
        k_shrink = 200.0  # pseudo-minutes toward the league mean (unseen players -> league mean)
        prate = {
            int(p): (rsum[i] + k_shrink * league) / (wsum[i] + k_shrink) for i, p in enumerate(uniq)
        }
        hs = np.array([inp.demo[p][0] for p in known], dtype=float)
        ws = np.array([inp.demo[p][1] for p in known], dtype=float)
        hs = np.where(np.isfinite(hs), hs, np.nanmedian(hs))
        ws = np.where(np.isfinite(ws), ws, np.nanmedian(ws))
        feats = np.array(
            [
                [hs[i], ws[i], *encode_position_fractions(inp.demo[p][2]), *prate.get(p, league)]
                for i, p in enumerate(known)
            ]
        )
        fit_mask = np.array([p in prate for p in known])
        k = min(K_ARCH, int(fit_mask.sum()))
        if k < 2:
            self._arch_cache[mth] = out
            return out
        sc = StandardScaler().fit(feats[fit_mask])
        km = KMeans(n_clusters=k, n_init=5, random_state=SEED).fit(sc.transform(feats[fit_mask]))
        lab = km.predict(sc.transform(feats))
        pmap = dict(zip(known, lab.tolist(), strict=True))
        uniq_all, inv_all = np.unique(inp.player_id, return_inverse=True)
        per_player = np.array([pmap.get(int(p), K_ARCH) for p in uniq_all], dtype=np.int64)
        out = per_player[inv_all]
        self._arch_cache[mth] = out
        return out

    def _star_context(self, tr: np.ndarray, cur: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(z_fit, z_pred) per row for the top-usage-teammate-OUT regressor (r4).

        Partner(i) = the team's top as-of usage player (the second if i is the top), where the team
        is i's latest team in the training window and usage = mean (fga + 0.44 fta + tov) per game
        over >= ``USAGE_MIN_GAMES`` window games with that team. z_fit (training rows) = the
        partner has no played row in that game while on the roster window (past box score);
        z_pred (this month's rows) = the partner is on the pre-tip OUT report. Both are 0 when the
        partner relation does not apply (new player, traded, team without two eligible players)."""
        inp = self.inp
        z_fit = np.zeros(inp.n)
        z_pred = np.zeros(inp.n)
        idx = np.where(tr)[0]
        if len(idx) == 0:
            return z_fit, z_pred
        df = pl.DataFrame(
            {
                "pid": inp.player_id[idx],
                "team": inp.team_id[idx],
                "day": self.days[idx],
                "u": inp.usage[idx],
            }
        ).filter(pl.col("u").is_finite())
        last = df.sort(["pid", "day"]).group_by("pid").agg(pl.col("team").last().alias("lteam"))
        lteam = {int(p): int(t) for p, t in zip(last["pid"], last["lteam"], strict=True)}
        agg = (
            df.join(last, on="pid")
            .filter(pl.col("team") == pl.col("lteam"))
            .group_by(["pid", "lteam"])
            .agg(
                pl.col("u").mean().alias("mu"),
                pl.len().alias("n_on"),
                pl.col("day").min().alias("since"),
            )
            .filter(pl.col("n_on") >= USAGE_MIN_GAMES)
            .sort(["lteam", "mu", "pid"], descending=[False, True, False])
        )
        top_by_team: dict[int, list[int]] = {}
        since: dict[int, int] = {}
        for pid, team, sday in zip(agg["pid"], agg["lteam"], agg["since"], strict=True):
            lst = top_by_team.setdefault(int(team), [])
            if len(lst) < 2:
                lst.append(int(pid))
                since[int(pid)] = int(sday)
        by_team: dict[int, list[int]] = {}
        for p, t in lteam.items():
            by_team.setdefault(t, []).append(p)
        partner: dict[int, int] = {}
        for team, tops in top_by_team.items():
            if len(tops) < 2:
                continue
            for p in by_team.get(team, []):
                partner[p] = tops[1] if p == tops[0] else tops[0]
        if not partner:
            return z_fit, z_pred
        uniq, inv = np.unique(inp.player_id, return_inverse=True)
        part_u = np.array([partner.get(int(p), -1) for p in uniq], dtype=np.int64)
        team_u = np.array([lteam.get(int(p), -1) for p in uniq], dtype=np.int64)
        since_u = np.array([since.get(int(q), 1 << 60) for q in part_u], dtype=np.int64)
        part_row, same_team = part_u[inv], team_u[inv] == inp.team_id
        valid = tr & (part_row >= 0) & same_team & (self.days >= since_u[inv])
        keys = self._gint * PLAYER_STRIDE + np.maximum(part_row, 0)
        pos = np.minimum(np.searchsorted(self._played, keys), len(self._played) - 1)
        present = self._played[pos] == keys
        z_fit = np.where(valid & ~present, 1.0, 0.0)
        for r in np.where(cur & (part_row >= 0) & same_team)[0]:
            if int(part_row[r]) in inp.flagged.get(str(inp.game_id[r]), ()):
                z_pred[r] = 1.0
        return z_fit, z_pred

    def _blocks(
        self,
        cfg: RidgeCfg,
        tr: np.ndarray,
        mth: np.datetime64,
        star_ctx: tuple[np.ndarray, np.ndarray] | None,
    ) -> list[_Block]:
        inp = self.inp
        n = inp.n
        ones = np.ones(n)
        pk, ok_ = inp.player_id, inp.opp_id
        bl = [
            _Block("p", "p", "p", pk, ones, ones),
            _Block("o", "o", "o", ok_, ones, ones, center=True),
        ]
        grp: dict[str | None, np.ndarray] = {None: np.full(n, -1)}
        if cfg.hier == "arch" or cfg.opp_grp == "arch":
            grp["arch"] = self._archetypes(mth, tr)
        grp["pos"] = self.pos_grp
        if cfg.hier:
            bl.append(_Block("g", "g", "p", grp[cfg.hier], ones, ones))
        if cfg.opp_grp:
            g = grp[cfg.opp_grp]
            bl.append(_Block("og", "og", "ox", np.where(g >= 0, ok_ * 16 + g, -1), ones, ones))
        if cfg.venue:
            home = inp.is_home.astype(float)
            bl.append(_Block("h", "h", "px", np.zeros(n, dtype=np.int64), home, home))
            bl.append(_Block("ov", "v", "ox", ok_ * 2 + inp.is_home, ones, ones))
            bl.append(_Block("pv", "v", "px", pk * 2 + inp.is_home, ones, ones))
        if cfg.star:
            assert star_ctx is not None
            zf, zp = star_ctx
            bl.append(_Block("s", "z", "tm", np.zeros(n, dtype=np.int64), zf, zp))
            bl.append(_Block("ps", "pz", "tm", pk, zf, zp))
        for b in bl:
            b.fit_vocab(tr)
        return bl

    # ----- main loop

    def run(
        self,
        jobs: Iterable[tuple[str, RidgeCfg]],
        months: Iterable[np.datetime64] | None = None,
    ) -> dict[tuple[str, RidgeCfg], dict[str, np.ndarray]]:
        """Compute components (+ ``rate``) for every unique (stat, canonical cfg) in ``jobs``.

        ``months`` restricts the refit months (default: all months present)."""
        inp = self.inp
        job_list = list(jobs)
        uniq: dict[tuple[str, RidgeCfg], None] = {(s, c.canonical()): None for s, c in job_list}
        out: dict[tuple[str, RidgeCfg], dict[str, np.ndarray]] = {
            k: {c: np.full(inp.n, np.nan) for c in (*k[1].components(), "rate")} for k in uniq
        }
        groups: dict[tuple[Any, ...], list[tuple[str, RidgeCfg]]] = {}
        for s, c in uniq:
            groups.setdefault((c.design_key(), c.halflife), []).append((s, c))
        month_list = (
            sorted(set(self.months.tolist()))
            if months is None
            else sorted({np.datetime64(m, "M") for m in months})
        )
        minutes_ok = np.isfinite(inp.minutes) & (inp.minutes >= MIN_TRAIN_MINUTES)
        for mth in month_list:
            mth = np.datetime64(mth, "M")
            start = mth.astype("datetime64[D]")
            start_day = int(start.astype(np.int64))
            cur_idx = np.where(self.months == mth)[0]
            if len(cur_idx) == 0:
                continue
            cur = np.zeros(inp.n, dtype=bool)
            cur[cur_idx] = True
            tr = (self.days < start_day) & (self.days >= start_day - LOOKBACK_DAYS) & minutes_ok
            if int(tr.sum()) < MIN_ROWS:
                continue
            star_ctx = None
            if any(c.star for _, c in uniq):
                star_ctx = self._star_context(tr, cur)
            tr_idx = np.where(tr)[0]
            for (_dk, hl), items in groups.items():
                age = (start_day - self.days[tr_idx]).astype(float)
                decay = np.ones(len(tr_idx)) if hl is None else 0.5 ** (age / hl)
                w = inp.minutes[tr_idx] * decay
                cfg0 = items[0][1]
                blocks = self._blocks(cfg0, tr, mth, star_ctx)
                x, offsets, lens = self._design(blocks, tr_idx)
                xw = x.T.multiply(w[None, :]).tocsr()
                gram = (xw @ x).toarray()
                pbar = None
                if any(c.pace for _, c in items):
                    pc = inp.pace[tr_idx]
                    okp = np.isfinite(pc)
                    pbar = float(np.average(pc[okp], weights=w[okp])) if okp.any() else np.nan
                for stat, cfg in items:
                    y = inp.y[stat][tr_idx] / inp.minutes[tr_idx] * 36.0
                    if cfg.pace and pbar and np.isfinite(pbar):
                        ratio = np.where(
                            np.isfinite(inp.pace[tr_idx]), inp.pace[tr_idx] / pbar, 1.0
                        )
                        y = y / ratio
                    mu = float(np.average(y, weights=w))
                    rhs = xw @ (y - mu)
                    pen = np.concatenate(
                        [
                            np.full(b_len, _pen_value(cfg, b.pen))
                            for b, b_len in zip(blocks, lens, strict=True)
                        ]
                    )
                    coef = solve_spd(gram, rhs, pen, self.backend)
                    res = out[(stat, cfg)]
                    total = np.zeros(len(cur_idx))
                    comp_sum: dict[str, np.ndarray] = {}
                    for b, off, ln in zip(blocks, offsets, lens, strict=True):
                        cb = coef[off : off + ln].copy()
                        if b.center and ln:
                            cb -= cb.mean()
                        li = b.lookup(cur_idx)
                        contrib = np.where(li >= 0, cb[np.maximum(li, 0)] if ln else 0.0, 0.0)
                        contrib = contrib * b.vals_pred[cur_idx]
                        comp_sum[b.comp] = comp_sum.get(b.comp, np.zeros(len(cur_idx))) + contrib
                        total += contrib
                    for comp in cfg.components():
                        res[comp][cur_idx] = comp_sum.get(comp, np.zeros(len(cur_idx)))
                    res["rate"][cur_idx] = mu + total
        return {(s, c): out[(s, c.canonical())] for s, c in job_list}

    def _design(
        self, blocks: list[_Block], tr_idx: np.ndarray
    ) -> tuple[sp.csr_matrix, list[int], list[int]]:
        rows_l, cols_l, data_l = [], [], []
        offsets: list[int] = []
        lens: list[int] = []
        off = 0
        n = len(tr_idx)
        for b in blocks:
            assert b.vocab is not None
            li = b.lookup(tr_idx)
            keep = (li >= 0) & (b.vals_fit[tr_idx] != 0.0)
            rows_l.append(np.arange(n)[keep])
            cols_l.append(off + li[keep])
            data_l.append(b.vals_fit[tr_idx][keep])
            offsets.append(off)
            lens.append(len(b.vocab))
            off += len(b.vocab)
        x = sp.csr_matrix(
            (np.concatenate(data_l), (np.concatenate(rows_l), np.concatenate(cols_l))),
            shape=(n, off),
        )
        return x, offsets, lens


# ----------------------------------------------------------- proxy tuning (selection season only)


def selection_months(inp: RidgeInputs, season: int = SELECT_SEASON) -> list[np.datetime64]:
    m = inp.month_index()[inp.season == season]
    return sorted({np.datetime64(x, "M") for x in m.tolist()})


def proxy_mse(inp: RidgeInputs, res: dict[str, np.ndarray], stat: str, rows: np.ndarray) -> float:
    """Minutes-weighted MSE of the ridge's own per-36 prediction on rows with >= 5 minutes."""
    ok = (
        rows
        & np.isfinite(res["rate"])
        & np.isfinite(inp.minutes)
        & (inp.minutes >= MIN_TRAIN_MINUTES)
    )
    w = inp.minutes[ok]
    err = inp.y[stat][ok] / w * 36.0 - res["rate"][ok]
    return float(np.average(err**2, weights=w))


def _mse_table(
    eng: MonthlyRidge,
    cfgs: Sequence[RidgeCfg],
    stats: Sequence[str],
    months: list[np.datetime64],
    rows: np.ndarray,
) -> dict[tuple[str, RidgeCfg], float]:
    jobs = [(s, c) for s in stats for c in cfgs]
    res = eng.run(jobs, months)
    return {k: proxy_mse(eng.inp, v, k[0], rows) for k, v in res.items()}


def tune_selection_season(
    inp: RidgeInputs,
    stats: Sequence[str] = PROP_STATS,
    season: int = SELECT_SEASON,
    backend: str = "numpy",
    log: Any = print,
) -> dict[str, Any]:
    """Ridge-level tuning on SELECTION-SEASON months only (never later data).

    Greedy, pre-registered order: (1) r1 half-life with reference penalties, (2) r6 penalties with
    flat weights, (3) joint (half-life, alpha_p, alpha_o) -> ``L1``; then each component's own
    penalty grid in two contexts (``ref`` base and ``L1`` base). Pace (r3b) has no proxy (its
    target differs). Returns JSON-able dict ``{stat: {...}}`` of config dicts + relative MSE."""
    if int(inp.season.max()) > MAX_SEASON:
        raise ValueError("season > 2024 present: 2025 must not be loaded")
    eng = MonthlyRidge(inp, backend)
    months = selection_months(inp, season)
    rows = (inp.season == season) & np.isin(
        inp.month_index(), np.array(months, dtype="datetime64[M]")
    )
    t0 = time.monotonic()

    def best_of(
        cfgs: list[RidgeCfg], stat: str, tab: dict[tuple[str, RidgeCfg], float]
    ) -> tuple[RidgeCfg, float]:
        c = min(cfgs, key=lambda cf: tab[(stat, cf)])
        return c, tab[(stat, c)]

    grid = [
        RidgeCfg(halflife=h, alpha_p=ap, alpha_o=ao)
        for h in HALFLIFE_GRID
        for ap in ALPHA_P_GRID
        for ao in ALPHA_O_GRID
    ]
    tab = _mse_table(eng, grid, stats, months, rows)
    log(f"tune: joint grid {len(grid)} cfgs x {len(stats)} stats in {time.monotonic() - t0:.0f}s")
    out: dict[str, Any] = {}
    for s in stats:
        base_mse = tab[(s, REF_CFG)]
        r1c, r1m = best_of([c for c in grid if c.alpha_p == 50.0 and c.alpha_o == 50.0], s, tab)
        r6c, r6m = best_of([c for c in grid if c.halflife is None], s, tab)
        l1c, l1m = best_of(grid, s, tab)
        out[s] = {
            "ref_mse": base_mse,
            "r1": {"cfg": dataclasses.asdict(r1c), "mse": r1m},
            "r6": {"cfg": dataclasses.asdict(r6c), "mse": r6m},
            "L1": {"cfg": dataclasses.asdict(l1c), "mse": l1m},
            "at_grid_edge": [
                n
                for n, v, g in (
                    ("halflife", l1c.halflife, [h for h in HALFLIFE_GRID if h is not None]),
                    ("alpha_p", l1c.alpha_p, list(ALPHA_P_GRID)),
                    ("alpha_o", l1c.alpha_o, list(ALPHA_O_GRID)),
                )
                if v is not None and v in (min(g), max(g))
            ],
            "grid_mse": {
                f"hl={c.halflife}|ap={c.alpha_p}|ao={c.alpha_o}": tab[(s, c)] for c in grid
            },
            "components": {},
        }
    # component penalty grids in two contexts
    comp_defs: dict[str, tuple[str, list[dict[str, Any]]]] = {
        "r5pos": ("alpha_g", [{"hier": "pos", "alpha_g": a} for a in ALPHA_G_GRID]),
        "r5arch": ("alpha_g", [{"hier": "arch", "alpha_g": a} for a in ALPHA_G_GRID]),
        "r2pos": ("alpha_og", [{"opp_grp": "pos", "alpha_og": a} for a in PENALTY_GRID]),
        "r2arch": ("alpha_og", [{"opp_grp": "arch", "alpha_og": a} for a in PENALTY_GRID]),
        "r3a": ("alpha_v", [{"venue": True, "alpha_v": a} for a in PENALTY_GRID]),
        "r4": ("alpha_pz", [{"star": True, "alpha_pz": a} for a in PENALTY_GRID]),
    }
    for ctx in ("ref", "L1"):
        for cname, (_pk, variants) in comp_defs.items():
            bases = {
                s: (REF_CFG if ctx == "ref" else RidgeCfg(**out[s]["L1"]["cfg"])) for s in stats
            }
            jobs = [(s, dataclasses.replace(bases[s], **v)) for s in stats for v in variants]
            jobs += [(s, bases[s]) for s in stats]
            res = eng.run(jobs, months)
            for s in stats:
                cands = [dataclasses.replace(bases[s], **v) for v in variants]
                mse = {c: proxy_mse(inp, res[(s, c)], s, rows) for c in cands}
                bc = min(cands, key=lambda c: mse[c])
                b0 = proxy_mse(inp, res[(s, bases[s])], s, rows)
                out[s]["components"].setdefault(cname, {})[ctx] = {
                    "cfg": dataclasses.asdict(bc),
                    "mse": mse[bc],
                    "base_mse": b0,
                    "rel_gain": (b0 - mse[bc]) / b0,
                }
        log(f"tune: components in context {ctx} done at {time.monotonic() - t0:.0f}s")
    return out


# --------------------------------------------------------------------------- arms


COMPONENTS = ("r1", "r6", "r5", "r2", "r3a", "r3b", "r4")
REFERENCE_ARMS = ("exp2_ref", "ref_fixed", "no_ridge")


def _apply(cfg: RidgeCfg, comp: str, tuned: dict[str, Any], ctx: str) -> RidgeCfg:
    """Switch component ``comp`` ON in ``cfg`` using the tuned penalties of context ``ctx``."""
    if comp == "r1":
        return dataclasses.replace(cfg, halflife=tuned["r1"]["cfg"]["halflife"])
    if comp == "r6":
        t = tuned["r6"]["cfg"]
        return dataclasses.replace(cfg, alpha_p=t["alpha_p"], alpha_o=t["alpha_o"])
    if comp == "r3b":
        return dataclasses.replace(cfg, pace=True)
    name = {"r5": "r5pos", "r2": "r2pos", "r3a": "r3a", "r4": "r4"}[comp]
    t = tuned["components"][name][ctx]["cfg"]
    keys = {
        "r5pos": ("hier", "alpha_g"),
        "r2pos": ("opp_grp", "alpha_og"),
        "r3a": ("venue", "alpha_v"),
        "r4": ("star", "alpha_pz"),
    }[name]
    return dataclasses.replace(cfg, **{k: t[k] for k in keys})


def build_arms(tuned: dict[str, dict[str, Any]]) -> dict[str, dict[str, RidgeCfg]]:
    """Pre-registered arm list (see docs/RIDGE_V2.md): arm -> stat -> RidgeCfg.

    ``exp2_ref`` / ``no_ridge`` carry no ridge cfg (handled by the job); ``ref_fixed`` is the
    experiment-2 estimator with the null-by-tonight-minutes leak removed."""
    arms: dict[str, dict[str, RidgeCfg]] = {"ref_fixed": {s: REF_CFG for s in tuned}}
    for s in tuned:
        t = tuned[s]
        ref = REF_CFG
        singles = {
            "r1": _apply(ref, "r1", t, "ref"),
            "r6": _apply(ref, "r6", t, "ref"),
            "r5": _apply(ref, "r5", t, "ref"),
            "r2": _apply(ref, "r2", t, "ref"),
            "r3a": _apply(ref, "r3a", t, "ref"),
            "r3b": _apply(ref, "r3b", t, "ref"),
            "r4": _apply(ref, "r4", t, "ref"),
        }
        for nm, cf in singles.items():
            arms.setdefault(nm, {})[s] = cf
        arms.setdefault("r5arch", {})[s] = dataclasses.replace(
            ref,
            hier="arch",
            alpha_g=t["components"]["r5arch"]["ref"]["cfg"]["alpha_g"],
        )
        arms.setdefault("r2arch", {})[s] = dataclasses.replace(
            ref,
            opp_grp="arch",
            alpha_og=t["components"]["r2arch"]["ref"]["cfg"]["alpha_og"],
        )
        l1 = RidgeCfg(**t["L1"]["cfg"])
        l2 = _apply(l1, "r5", t, "L1")
        l3 = _apply(l2, "r2", t, "L1")
        l4 = _apply(_apply(l3, "r3a", t, "L1"), "r3b", t, "L1")
        l5 = _apply(l4, "r4", t, "L1")
        for nm, cf in (("L1", l1), ("L2", l2), ("L3", l3), ("L4", l4), ("ALL", l5)):
            arms.setdefault(nm, {})[s] = cf
        # drop-one from ALL (component reverted to its reference setting)
        drops = {
            "drop_r1": dataclasses.replace(l5, halflife=None),
            "drop_r6": dataclasses.replace(l5, alpha_p=RIDGE_ALPHA, alpha_o=RIDGE_ALPHA),
            "drop_r5": dataclasses.replace(l5, hier=None),
            "drop_r2": dataclasses.replace(l5, opp_grp=None),
            "drop_r3a": dataclasses.replace(l5, venue=False),
            "drop_r3b": dataclasses.replace(l5, pace=False),
            "drop_r4": dataclasses.replace(l5, star=False),
        }
        for nm, cf in drops.items():
            arms.setdefault(nm, {})[s] = cf
        # proxy-selected: greedy forward on the ridge-level proxy gain (r3b has no proxy)
        sel = l1
        for comp in ("r5", "r2", "r3a", "r4"):
            key = {"r5": "r5pos", "r2": "r2pos", "r3a": "r3a", "r4": "r4"}[comp]
            if t["components"][key]["L1"]["rel_gain"] >= PROXY_MIN_GAIN:
                sel = _apply(sel, comp, t, "L1")
        arms.setdefault("proxy_sel", {})[s] = sel
    return arms


ARM_DESCRIPTIONS: dict[str, str] = {
    "exp2_ref": "experiment-2 ridge exactly as exported (NULL when tonight's minutes < 5: leaky)",
    "ref_fixed": "experiment-2 estimator for every row (leak removed): PRIMARY reference",
    "no_ridge": "v2 without group (i)",
    "r1": "recency-weighted (half-life tuned on 2023)",
    "r6": "per-stat penalties tuned on 2023",
    "r5": "hierarchical: position-group mean + penalised player deviation",
    "r5arch": "hierarchical with archetype groups",
    "r2": "opponent x position-group deviations",
    "r2arch": "opponent x archetype deviations",
    "r3a": "venue split (home term, opponent x venue, player x venue)",
    "r3b": "pace-adjusted per-36 target",
    "r4": "player x top-usage-teammate-OUT",
    "L1": "r1 + r6 jointly tuned",
    "L2": "L1 + r5",
    "L3": "L2 + r2",
    "L4": "L3 + r3a + r3b",
    "ALL": "L4 + r4 (every component)",
    "drop_r1": "ALL without r1",
    "drop_r6": "ALL without r6",
    "drop_r5": "ALL without r5",
    "drop_r2": "ALL without r2",
    "drop_r3a": "ALL without r3a",
    "drop_r3b": "ALL without r3b",
    "drop_r4": "ALL without r4",
    "proxy_sel": "L1 + components whose 2023 ridge-level proxy gain >= 0.2% (greedy)",
}


# --------------------------------------------------------------------------- audits


def missing_by_tonight_minutes(
    values: np.ndarray, minutes: np.ndarray, thresh: float = MIN_TRAIN_MINUTES
) -> dict[str, float]:
    """Leak audit: is a feature's missingness tied to tonight's minutes?

    Returns the NaN share among rows with minutes < ``thresh`` vs >= ``thresh``; a feature that is
    null only for the first group leaks the outcome (rows with almost no minutes have ~0 stats)."""
    low = minutes < thresh
    nan = ~np.isfinite(values)
    return {
        "null_share_low_minutes": float(nan[low].mean()) if low.any() else float("nan"),
        "null_share_normal_minutes": float(nan[~low].mean()) if (~low).any() else float("nan"),
        "n_low": int(low.sum()),
    }


# --------------------------------------------------------------------------- export


def load_demo(db_path: str) -> dict[int, tuple[float, float, str]]:
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT player_id, height_in, weight_lb, position FROM players_static"
        ).pl()
    finally:
        con.close()
    out: dict[int, tuple[float, float, str]] = {}
    for pid, h, w, pos in df.iter_rows():
        out[int(pid)] = (
            float(h) if h is not None else float("nan"),
            float(w) if w is not None else float("nan"),
            str(pos) if pos is not None else "",
        )
    return out


def build_inputs_from_db(
    db_path: str, elo_config: str = "configs/mov_elo_tuned.yaml"
) -> tuple[RidgeInputs, pl.DataFrame]:
    """(RidgeInputs for ALL played rows <= 2024, v1 frame). Read-only DB; season 2025 not loaded."""
    games, pgs, static, avail, _poss = load_inputs_v2(db_path)
    if games["season"].max() > MAX_SEASON:  # type: ignore[operator]
        raise ValueError("season > 2024 present: 2025 must not be loaded")
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig()
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    v1 = build_features(games, pgs, static, flagged, elo)
    inp = build_inputs(v1, pgs, flagged, load_demo(db_path))
    return inp, v1


def export_columns(
    inp: RidgeInputs,
    arms: dict[str, dict[str, RidgeCfg]],
    backend: str = "numpy",
    log: Any = print,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Compute all unique (stat, cfg) fits over every month; return the column frame + arm spec."""
    jobs = [(s, c) for a in arms.values() for s, c in a.items()]
    eng = MonthlyRidge(inp, backend)
    t0 = time.monotonic()
    res = eng.run(jobs)
    log(f"export: {len(res)} unique (stat, cfg) fits in {time.monotonic() - t0:.0f}s")
    cols: dict[str, np.ndarray] = {}
    for (stat, cfg), comps in res.items():
        for comp in cfg.components():
            cols[column_name(stat, cfg, comp)] = comps[comp].astype(np.float32)
    frame = pl.DataFrame(
        {
            "game_id": inp.game_id,
            "player_id": inp.player_id,
            **{k: pl.Series(k, v).fill_nan(None) for k, v in cols.items()},
        }
    )
    spec_arms: dict[str, Any] = {}
    seen: dict[tuple[tuple[str, RidgeCfg], ...], str] = {}
    for name, per_stat in arms.items():
        sig = tuple((s, c.canonical()) for s, c in sorted(per_stat.items()))
        spec_arms[name] = {
            "alias_of": seen.get(sig),  # identical estimator already in the list: not re-run
            "description": ARM_DESCRIPTIONS.get(name, ""),
            "columns": {
                s: [column_name(s, c, comp) for comp in c.components()] for s, c in per_stat.items()
            },
            "cfg": {s: dataclasses.asdict(c.canonical()) for s, c in per_stat.items()},
        }
        seen.setdefault(sig, name)
    return frame, spec_arms


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


def validate_against_exp2(
    frame: pl.DataFrame, base: pl.DataFrame, ref_arm: dict[str, Any]
) -> dict[str, Any]:
    """The reference cfg must reproduce the experiment-2 columns where those are non-null."""
    j = base.select(
        "game_id",
        "player_id",
        *[f"ridge_{k}_{s}" for s in PROP_STATS for k in ("player", "opp")],
    ).join(frame, on=["game_id", "player_id"], how="inner")
    out: dict[str, Any] = {"n_joined": j.height}
    for s in PROP_STATS:
        cp, co = ref_arm["columns"][s]
        for old, new in ((f"ridge_player_{s}", cp), (f"ridge_opp_{s}", co)):
            both = j.filter(pl.col(old).is_not_null() & pl.col(new).is_not_null())
            d = (both[old].cast(pl.Float64) - both[new].cast(pl.Float64)).abs()
            out[old] = {
                "n_both": both.height,
                "max_abs_diff": _f(d.max()) if both.height else float("nan"),
                "old_null_new_nonnull": j.filter(
                    pl.col(old).is_null() & pl.col(new).is_not_null()
                ).height,
            }
    return out


def export_dataset(
    db_path: str = "nba.duckdb",
    out_dir: str = "data/colab/ridge_v2",
    base_dir: str = "data/colab/ctxres_v2",
    base_run: str = "data/colab/runs/ctxres_v2_sweep/20261008_171446",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    backend: str = "numpy",
    tuning_path: str | None = None,
    log: Any = print,
) -> dict[str, Any]:
    """Tune on 2023 months, build every pre-registered arm, write parquet + spec + tuning JSON."""
    t0 = time.monotonic()
    inp, v1 = build_inputs_from_db(db_path, elo_config)
    log(f"inputs: {inp.n} played rows, seasons {sorted(set(inp.season.tolist()))}")
    if tuning_path and Path(tuning_path).exists():
        tuned = json.loads(Path(tuning_path).read_text())["tuned"]
        log(f"tuning loaded from {tuning_path}")
    else:
        tuned = tune_selection_season(inp, backend=backend, log=log)
    arms = build_arms(tuned)
    frame, spec_arms = export_columns(inp, arms, backend, log)
    base_path = Path(base_dir) / "ctxres_v2.parquet"
    base = pl.read_parquet(base_path, columns=["game_id", "player_id", "season", "n_prior"])
    keep = (
        v1.filter(
            (pl.col("n_prior") >= MIN_PRIOR_PLAYED) & pl.col("season").is_in(list(EXPORT_SEASONS))
        )
        .select("game_id", pl.col("player_id").cast(pl.Int64))
        .sort(["game_id", "player_id"])
    )
    want = base.select("game_id", pl.col("player_id").cast(pl.Int64)).sort(["game_id", "player_id"])
    if keep.height != want.height or not keep.equals(want):
        raise ValueError("ridge export rows differ from the ctxres_v2 parquet rows")
    out = want.join(frame, on=["game_id", "player_id"], how="left")
    full_base = pl.read_parquet(base_path)
    check = validate_against_exp2(out, full_base, spec_arms["ref_fixed"])
    for k, v in check.items():
        if k == "n_joined":
            continue
        tol = 0.25 if k.startswith("ridge_player") else 0.02  # exp-2 used sparse_cg (tol 1e-4)
        if not v["max_abs_diff"] <= tol:
            raise ValueError(f"reference ridge does not reproduce experiment 2 for {k}: {v}")
    leak = {}
    minutes_by_key = pl.DataFrame(
        {"game_id": inp.game_id, "player_id": inp.player_id, "minutes": inp.minutes}
    )
    joined = full_base.select("game_id", "player_id", "ridge_player_pts").join(
        minutes_by_key, on=["game_id", "player_id"], how="left"
    )
    leak["exp2_ridge_player_pts"] = missing_by_tonight_minutes(
        joined["ridge_player_pts"].cast(pl.Float64).to_numpy(), joined["minutes"].to_numpy()
    )
    rf_col = spec_arms["ref_fixed"]["columns"]["pts"][0]
    j2 = out.select("game_id", "player_id", rf_col).join(
        minutes_by_key, on=["game_id", "player_id"], how="left"
    )
    leak["ref_fixed_player_pts"] = missing_by_tonight_minutes(
        j2[rf_col].cast(pl.Float64).to_numpy(), j2["minutes"].to_numpy()
    )
    o = Path(out_dir)
    o.mkdir(parents=True, exist_ok=True)
    out.write_parquet(o / "ridge_v2.parquet", compression="zstd")
    cfg_json = json.loads(Path(base_run, "best_config.json").read_text())
    base_cfg = {
        "source_run": base_run,
        "params": cfg_json["search_v12"]["params"],
        "families": {
            "pts": "quantile",
            "reb": "poisson_nb",
            "ast": "poisson_nb",
            "fg3m": "poisson_nb",
        },
        "recorded_candidate_exp2": cfg_json.get("recorded_candidate"),
    }
    (o / "base_config.json").write_text(json.dumps(base_cfg, indent=1))
    spec = {
        "arms": spec_arms,
        "reference_arms": list(REFERENCE_ARMS),
        "primary_reference": "ref_fixed",
        "exp2_columns": {s: [f"ridge_player_{s}", f"ridge_opp_{s}"] for s in PROP_STATS},
        "n_rows": out.height,
        "n_columns": out.width - 2,
        "seed": SEED,
        "select_season": SELECT_SEASON,
        "lookback_days": LOOKBACK_DAYS,
        "min_rows": MIN_ROWS,
        "k_arch": K_ARCH,
        "reproduction_check": check,
        "leak_audit": leak,
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "column_stats": {
            c: {
                "mean": _f(out[c].mean()),
                "std": _f(out[c].std()),
                "null_share": _f(out[c].is_null().mean()),
                "nonzero_share": _f((out[c].fill_null(0.0) != 0.0).mean()),
            }
            for c in out.columns
            if c.startswith("rg_")
        },
        "null_share": {c: _f(out[c].is_null().mean()) for c in out.columns if c.startswith("rg_")},
    }
    (o / "ridge_v2_spec.json").write_text(json.dumps(spec, indent=1))
    (o / "ridge_v2_tuning.json").write_text(json.dumps({"tuned": tuned, "seed": SEED}, indent=1))
    log(f"export done in {time.monotonic() - t0:.0f}s -> {o}")
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export ridge_v2 feature variants for the Colab sweep")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--out-dir", default="data/colab/ridge_v2")
    ap.add_argument("--base-dir", default="data/colab/ctxres_v2")
    ap.add_argument("--base-run", default="data/colab/runs/ctxres_v2_sweep/20261008_171446")
    ap.add_argument("--elo-config", default="configs/mov_elo_tuned.yaml")
    ap.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    ap.add_argument("--tuning", default=None, help="reuse an existing ridge_v2_tuning.json")
    a = ap.parse_args(argv)
    backend = "numpy"
    if a.device in ("cuda", "auto"):
        import torch

        if torch.cuda.is_available():
            backend = "torch"
        elif a.device == "cuda":
            raise SystemExit("--device cuda requested but CUDA is not available")
    spec = export_dataset(a.db, a.out_dir, a.base_dir, a.base_run, a.elo_config, backend, a.tuning)
    print(json.dumps({k: spec[k] for k in ("n_rows", "n_columns")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
