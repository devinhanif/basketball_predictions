# ruff: noqa: E501
"""Game scripts, sliders and best picks per case (``docs/contracts/game_scripts_v1.md``).

Standard library only (plus ``nba.parlay.combo``, itself stdlib), so the browser port can be tested
against it. No price, no fee, no stake enters this module: the market block of a pick is attached by
the CALLER through ``market_fn`` (``nba.export.combo_stage`` supplies one built on ``nba.parlay.ev``,
the cards' fee code), never computed here.

Four things, each EXPLORATORY (the review override of 2026-11-10: no script is calibrated):

1. ``script_priors``: P(script) per game from the game model's margin / total normals.
2. ``point_condition`` + ``cond_surv``: re-condition one player-stat on an arbitrary point of the two
   sliders (final total, final margin) by Gaussian-copula conditioning on the margin and total latents.
   It is LINEAR in the margin: it cannot see that blowouts rest starters (the discrete scripts do).
3. ``shift_surv``: the "who sits" slider, a location shift of the survival curve by the teammate-sits
   export's ``delta_mean`` (pts only; the shape is not changed).
4. ``best_picks``: per script, legs ranked by P(leg | script) with the unconditional numbers beside.
5. ``live_script_probs``: P(script) from the in-game state (Brownian conditioning, section "live").
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from nba.parlay import combo as C

Payload = Mapping[str, Any]
STATUS = "exploratory"
SCRIPTS = C.SCRIPTS
MIN_P_PLAY = 0.5  # a pick needs a likely player; below this the leg is not ranked
TOP_LEGS = 6
TOP_PAIRS = 3
REGULATION_MIN = 48.0
REM_MIN = (
    0.005  # floor on the remaining fraction of the game (about 15 s), keeps the variance positive
)


# ---------------------------------------------------------------- 1. priors


def script_priors(game: Payload, shared: Payload) -> dict[str, float]:
    """P(script) per script, the same numbers as ``combo.script_prior`` (integer-corrected normal tails)."""
    return {s: C.script_prior(s, game, shared) for s in SCRIPTS}


def script_rules(game: Payload, shared: Payload) -> dict[str, dict[str, Any]]:
    return C.script_definitions(game, shared)


# ---------------------------------------------------------------- 2. the (total, margin) slider


def point_condition(
    game: Payload, shared: Payload, player: Payload, stat: str, margin: float, total: float
) -> tuple[float, float]:
    """(mean, sd) of the player-stat latent given the point (final margin = ``margin``, final total =
    ``total``), margin = home minus away.

    Gaussian conditioning on the two game latents. ``zm = (margin - mu_m) / sd_m``, ``zt = (total -
    mu_t) / sd_t``. The player latent has correlation ``a = sign * rho_own_margin`` with the margin
    (``sign`` +1 for a home player) and ``b = rho_game(stat, total)`` with the total; the game block
    has correlation ``r = rho_game(margin, total)``. With ``S = [[1, r], [r, 1]]`` and ``c = (a, b)``:
    ``mean = c S^-1 (zm, zt)``, ``sd = sqrt(1 - c S^-1 c)``. Nothing is precomputed: the browser does this
    from ``shared.corr`` and the game's four numbers."""
    g, table = game["game"], shared["corr"]
    zm = (margin - float(g["mu_m"])) / float(g["sd_m"])
    zt = (total - float(g["mu_t"])) / float(g["sd_t"])
    sign = 1.0 if int(player["team"]) == int(game["home_team"]) else -1.0
    a = sign * C.corr_lookup(table, "own_margin", stat, "margin")
    b = C.corr_lookup(table, "game", stat, "total")
    r = C.corr_lookup(table, "game", "margin", "total")
    det = 1.0 - r * r
    # S^-1 = 1/det * [[1, -r], [-r, 1]]
    w_m = (a - r * b) / det
    w_t = (b - r * a) / det
    mean = w_m * zm + w_t * zt
    var = 1.0 - (w_m * a + w_t * b)
    return mean, math.sqrt(max(var, 1e-6))


def cond_surv(grid: Payload, k: int, mean: float, sd: float) -> float:
    """S'(k) = P(Y >= k | point): the latent threshold ``a_k = Phi^-1(1 - S(k))`` against ``N(mean, sd)``."""
    s = C.surv_at(grid, k)
    if s >= 1.0:
        return 1.0
    if s <= 0.0:
        return 0.0
    a = C.norm_ppf(1.0 - s)
    return 1.0 - C.norm_cdf((a - mean) / sd)


def point_regions(game: Payload, shared: Payload, margin: float, total: float) -> list[str]:
    """Which of the four scripts the slider point lies in (not exclusive; may be empty)."""
    d = shared["scripts"]["definitions"]
    defs = script_rules(game, shared)
    out = []
    if abs(margin) >= d["blowout_abs_margin"]:
        out.append("blowout")
    if abs(margin) <= d["close_abs_margin"]:
        out.append("close")
    if total >= defs["shootout"]["min_total"]:
        out.append("shootout")
    if total <= defs["grind"]["max_total"]:
        out.append("grind")
    return out


# ---------------------------------------------------------------- 3. who sits


def shift_surv(grid: Payload, k: int, delta: float) -> float:
    """S'(k) = S(k - delta) with linear interpolation between integer rungs: the whole curve moves by
    ``delta`` stat units (``delta_mean`` of the teammate-sits export), its shape is unchanged. Clamped
    to the stored range, so it stays a valid survival function."""
    x = k - delta
    lo = math.floor(x)
    f = x - lo
    s_lo, s_hi = C.surv_at(grid, int(lo)), C.surv_at(grid, int(lo) + 1)
    return (1.0 - f) * s_lo + f * s_hi


# ---------------------------------------------------------------- 4. best picks per case

MarketFn = Callable[[int, str, int], "dict[str, Any] | None"]


def _leg(game: Payload, pid: int, stat: str, line: float, side: str) -> dict[str, Any]:
    return {"kind": "player", "game_id": str(game["game_id"]), "player_id": pid, "stat": stat,
            "line": line, "side": side}  # fmt: skip


def candidate_legs(
    game: Payload, ask_ks: Mapping[tuple[int, str], Sequence[int]] | None = None
) -> list[dict[str, Any]]:
    """Legs a pick may name: the posted main line, both sides; plus OVER legs at every threshold k where a
    Kalshi YES ask exists and the unconditional P lies in the target band (so a price can be compared).
    Players with ``p_play`` below ``MIN_P_PLAY`` or unknown are not ranked."""
    out: list[dict[str, Any]] = []
    for p in game["players"]:
        pp = p.get("p_play")
        if pp is None or float(pp) < MIN_P_PLAY:
            continue
        pid = int(p["player_id"])
        for stat, grid in p["stats"].items():
            seen: set[tuple[float, str]] = set()
            ml = grid.get("main_line")
            if ml is not None:
                for side in ("over", "under"):
                    out.append(_leg(game, pid, stat, float(ml), side))
                    seen.add((float(ml), side))
            for k in (ask_ks or {}).get((pid, stat), ()):
                line = k - 0.5
                s = C.surv_at(grid, int(k))
                if (line, "over") in seen or not C.TARGET_P_MIN <= s <= C.TARGET_P_MAX:
                    continue
                ck = grid.get("cal_k")
                if ck is not None and not (int(ck[0]) <= k <= int(ck[1])):
                    continue
                out.append(_leg(game, pid, stat, line, "over"))
    return out


def _player_of(game: Payload, pid: int) -> Payload:
    return C._player(game, pid)


def best_picks(
    game: Payload,
    shared: Payload,
    *,
    top_legs: int = TOP_LEGS,
    top_pairs: int = TOP_PAIRS,
    market_fn: MarketFn | None = None,
    ask_ks: Mapping[tuple[int, str], Sequence[int]] | None = None,
) -> dict[str, Any]:
    """Per script: legs ranked by P(leg | script) and same-player pairs ranked by P(pair | script).

    Every leg carries ``p_script`` (the game's P(script)), ``p_leg`` (unconditional, calibrated, given
    the player plays), ``p_leg_script``, ``shift`` (their difference) and, for an over leg whose threshold
    has a current Kalshi ask, ``market`` (from ``market_fn``; computed by the caller with the cards' fee
    code from the UNCONDITIONAL probability, never the script one). A leg above the tested range
    (unconditional, or conditional P above 0.75) is dropped and counted, never capped. Pairs: the same
    player's two different stats, both over or both under the posted main lines; ``lift`` is joint over
    product under the script, ``script_vs_base`` is P(pair | script) / P(pair)."""
    priors = script_priors(game, shared)
    cands = candidate_legs(game, ask_ks)
    base: dict[tuple[int, str, float, str], float] = {}
    for c in cands:
        base[(c["player_id"], c["stat"], c["line"], c["side"])] = C.evaluate_game_legs(
            [c], game, shared
        ).p_joint
    out: dict[str, Any] = {}
    for script in SCRIPTS:
        rows: list[dict[str, Any]] = []
        dropped = 0
        for c in cands:
            key = (c["player_id"], c["stat"], c["line"], c["side"])
            pb = base[key]
            if C.leg_above_tested_range(c, game):
                dropped += 1
                continue
            ps = C.evaluate_game_legs([c], game, shared, script=script).p_joint
            if ps > C.TESTED_P_MAX:
                dropped += 1
                continue
            pl = _player_of(game, c["player_id"])
            k = C.line_to_bounds(c["line"], c["side"])[0]
            rec: dict[str, Any] = {
                "player_id": c["player_id"], "name": str(pl["name"]), "team": int(pl["team"]),
                "stat": c["stat"], "line": c["line"], "side": c["side"],
                "p_script": round(priors[script], 4), "p_leg": round(pb, 4),
                "p_leg_script": round(ps, 4), "shift": round(ps - pb, 4),
                "p_play": pl.get("p_play"), "status": STATUS,
                "market": None,
            }  # fmt: skip
            if c["side"] == "over" and market_fn is not None:
                rec["market"] = market_fn(c["player_id"], c["stat"], int(k))
            rows.append(rec)
        rows.sort(key=lambda r: (-r["p_leg_script"], r["player_id"], r["stat"], r["side"]))
        out[script] = {
            "p_script": round(priors[script], 4), "status": STATUS,
            "legs": rows[:top_legs], "n_legs_ranked": len(rows), "n_dropped_above_range": dropped,
            "pairs": _pairs(game, shared, script, top_pairs),
        }  # fmt: skip
    return out


def _pairs(game: Payload, shared: Payload, script: str, top_n: int) -> list[dict[str, Any]]:
    """Same-player two-stat pairs at the posted main lines under ``script`` (exact bivariate; screened
    coarsely, the leaders recomputed at full resolution)."""
    scored: list[tuple[float, float, list[dict[str, Any]], str]] = []
    for p in game["players"]:
        pp = p.get("p_play")
        if pp is None or float(pp) < MIN_P_PLAY:
            continue
        pid = int(p["player_id"])
        stats = sorted(s for s, g in p["stats"].items() if g.get("main_line") is not None)
        for i, s1 in enumerate(stats):
            for s2 in stats[i + 1 :]:
                for side in ("over", "under"):
                    legs = [
                        _leg(game, pid, s, float(p["stats"][s]["main_line"]), side)
                        for s in (s1, s2)
                    ]
                    if any(C.leg_above_tested_range(x, game) for x in legs):
                        continue
                    r = C.evaluate_game_legs(
                        legs, game, shared, script=script, biv_n=C.SCREEN_BIV_N
                    )
                    if r.p_joint < 0.02:
                        continue
                    scored.append((r.p_joint, r.lift or 0.0, legs, side))
    scored.sort(key=lambda t: (-t[0], str(t[2])))
    out = []
    for _pj, _lf, legs, side in scored[: max(top_n * 2, top_n)]:
        rs = C.evaluate_game_legs(legs, game, shared, script=script)
        rb = C.evaluate_game_legs(legs, game, shared)
        pl = _player_of(game, legs[0]["player_id"])
        pp = pl.get("p_play")
        out.append({
            "player_id": legs[0]["player_id"], "name": str(pl["name"]), "side": side,
            "legs": [{"stat": x["stat"], "line": x["line"]} for x in legs],
            "p_joint_script": round(rs.p_joint, 4), "p_independent_script": round(rs.p_independent, 4),
            "lift": None if rs.lift is None else round(rs.lift, 4),
            "p_joint": round(rb.p_joint, 4), "p_independent": round(rb.p_independent, 4),
            "script_vs_base": None if rb.p_joint <= 0 else round(rs.p_joint / rb.p_joint, 4),
            "p_play": pp, "status": STATUS,
        })  # fmt: skip
    out.sort(key=lambda r: (-r["p_joint_script"], r["player_id"], str(r["legs"])))
    return out[:top_n]


# ---------------------------------------------------------------- live


def live_script_probs(
    game: Payload, shared: Payload, elapsed_min: float, margin: float, total: float
) -> dict[str, float]:
    """P(script) given the in-game state: ``elapsed_min`` regulation minutes played, ``margin`` = home
    minus away NOW, ``total`` = points scored NOW.

    METHOD (independent increments, Stern 1994 style, built on the pre-game normals only). With ``tau =
    elapsed / 48`` and ``rem = max(1 - tau, REM_MIN)``: final margin ~ N(margin + mu_m rem, sd_m^2 rem),
    final total ~ N(total + mu_t rem, sd_t^2 rem). At tau = 0 this is exactly the pre-game prior; as tau
    -> 1 it collapses onto the score. Thresholds are the game's own (integer-corrected, as the prior).
    NOT validated against play-by-play; the observed PACE is not extrapolated beyond the points already
    scored (hot or cold shooting is treated as noise, which understates persistence); overtime is out of
    scope. Exploratory."""
    g, d = game["game"], shared["scripts"]["definitions"]
    mu_m, sd_m, mu_t, sd_t = (float(g[k]) for k in ("mu_m", "sd_m", "mu_t", "sd_t"))
    tau = min(max(elapsed_min / REGULATION_MIN, 0.0), 1.0)
    rem = max(1.0 - tau, REM_MIN)
    m_mu, m_sd = margin + mu_m * rem, sd_m * math.sqrt(rem)
    t_mu, t_sd = total + mu_t * rem, sd_t * math.sqrt(rem)
    tb = d["blowout_abs_margin"] - 0.5
    tc = d["close_abs_margin"] + 0.5
    defs = script_rules(game, shared)
    return {
        "blowout": C.norm_cdf((-tb - m_mu) / m_sd) + (1.0 - C.norm_cdf((tb - m_mu) / m_sd)),
        "close": C.norm_cdf((tc - m_mu) / m_sd) - C.norm_cdf((-tc - m_mu) / m_sd),
        "shootout": 1.0 - C.norm_cdf((defs["shootout"]["min_total"] - 0.5 - t_mu) / t_sd),
        "grind": C.norm_cdf((defs["grind"]["max_total"] + 0.5 - t_mu) / t_sd),
    }
