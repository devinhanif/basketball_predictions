"""Rolling forward metrics with game-date-clustered bootstrap CIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.daily.season import FORWARD_SEASON, ROLLOVER_MIN_GAMES
from nba.daily.store import ensure_tables
from nba.lineups.store import DEFAULT_LINEUPS_DB, connect_lineups

#: Reference for win prob: an uninformative p=0.5 forecast.
NAIVE_LOG_LOSS = float(np.log(2.0))


@dataclass(frozen=True)
class ClusterCI:
    point: float
    lo: float
    hi: float
    n: int
    n_clusters: int


def cluster_bootstrap_mean(
    values: np.ndarray, clusters: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> ClusterCI:
    """Percentile CI for the mean, resampling whole clusters (e.g. game dates)
    so same-night correlation is not mistaken for independent samples."""
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        nan = float("nan")
        return ClusterCI(nan, nan, nan, 0, 0)
    _, inv = np.unique(clusters, return_inverse=True)
    k = int(inv.max()) + 1
    sums = np.bincount(inv, weights=v, minlength=k)
    cnts = np.bincount(inv, minlength=k).astype(float)
    point = float(v.mean())
    if k < 2:
        return ClusterCI(point, float("nan"), float("nan"), v.size, k)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return ClusterCI(point, float(lo), float(hi), v.size, k)


def rollover_status(con: duckdb.DuckDBPyConnection) -> tuple[int, bool]:
    """(# completed 2026-27 games ingested, rollover trigger fired?)."""
    row = con.execute(
        "SELECT count(*) FROM games WHERE season = ? AND home_pts > 0 AND away_pts > 0",
        [FORWARD_SEASON],
    ).fetchone()
    n = int(row[0]) if row else 0
    return n, n >= ROLLOVER_MIN_GAMES


def _fmt(ci: ClusterCI, d: int = 4) -> str:
    return f"{ci.point:.{d}f} [{ci.lo:.{d}f}, {ci.hi:.{d}f}] (n={ci.n}, dates={ci.n_clusters})"


INJURY_MODEL = "rung0_injury_elo"
BASE_MODEL = "rung0_mov_elo"


def _paired_win_section(win: list[tuple[Any, ...]]) -> list[str]:
    """Paired injury-Elo minus MOV-Elo log loss / Brier on games scored by BOTH
    (games that fell back to MOV-Elo have no injury row and are excluded;
    the exclusion count is reported). Negative = injury-Elo better."""
    inj = {r[0]: r for r in win if r[4] == INJURY_MODEL}
    base = {r[0]: r for r in win if r[4] == BASE_MODEL}
    both = sorted(set(inj) & set(base), key=str)
    lines = ["", f"### Paired: {INJURY_MODEL} minus {BASE_MODEL} (negative = injury better)"]
    lines.append(
        f"- games scored by both: {len(both)}; mov_elo-only (no usable report -> fallback): "
        f"{len(set(base) - set(inj))}"
    )
    if not both:
        lines.append("- no paired games yet.")
        return lines
    d = np.array([str(base[g][1]) for g in both])
    dll = np.array([inj[g][2] - base[g][2] for g in both], dtype=float)
    dbr = np.array([inj[g][3] - base[g][3] for g in both], dtype=float)
    lines += [
        f"- delta log loss: {_fmt(cluster_bootstrap_mean(dll, d), 5)}",
        f"- delta Brier: {_fmt(cluster_bootstrap_mean(dbr, d), 5)}",
    ]
    if len(both) < 100:
        lines.append(f"- CAUTION: only {len(both)} paired games; no claim either way yet.")
    return lines


PROPS_PRIMARY = "props_context_residual"
PROPS_COMPARISON = "props_recency_v1"


def _paired_props_section(props: list[tuple[Any, ...]]) -> list[str]:
    """Paired context-residual minus recency CRPS per stat on player-games scored
    by BOTH (the recency model is the comparison; rows where the primary fell
    back to recency are identical by construction and counted separately).
    Negative = context model better. Bootstrap resamples game dates."""
    lines = [
        "",
        f"### Paired: {PROPS_PRIMARY} minus {PROPS_COMPARISON} (negative = primary better)",
    ]
    prim = {(r[0], r[6], r[7]): r for r in props if r[5] == PROPS_PRIMARY}
    comp = {(r[0], r[6], r[7]): r for r in props if r[5] == PROPS_COMPARISON}
    if not prim or not comp:
        lines.append("- no paired player-games yet.")
        return lines
    for stat in sorted({k[0] for k in prim}):
        keys = sorted((k for k in prim if k[0] == stat and k in comp), key=str)
        if not keys:
            continue
        d = np.array([str(prim[k][1]) for k in keys])
        dc = np.array([prim[k][2] - comp[k][2] for k in keys], dtype=float)
        db = np.array(
            [abs(prim[k][4] - prim[k][3]) - abs(comp[k][4] - comp[k][3]) for k in keys],
            dtype=float,
        )
        lines.append(
            f"- {stat}: delta CRPS {_fmt(cluster_bootstrap_mean(dc, d))}; "
            f"delta |error of mean| {_fmt(cluster_bootstrap_mean(db, d), 3)}"
        )
        if len(keys) < 300:
            lines.append(f"  - CAUTION: only {len(keys)} paired player-games for {stat}.")
    return lines


T30_MODEL = "props_context_residual_t30"


def _paired_t30_section(
    con: duckdb.DuckDBPyConnection, props: list[tuple[Any, ...]], lineups_db: Path | None
) -> list[str]:
    """SHADOW comparison: props_context_residual_t30 (confirmed lineup known at T-30) minus
    the production T-60 props_context_residual, paired per (stat, game, player) on
    player-games scored by BOTH. Negative = T-30 better. The T-30 model is comparison-only;
    pre-registered upward-biased expectation (docs/LINEUPS_KNOWN.md): about -1.0 to -1.4% CRPS."""
    lines = ["", f"### Paired SHADOW: {T30_MODEL} minus {PROPS_PRIMARY} (negative = T-30 better)"]
    try:
        dec = con.execute(
            "SELECT outcome, split_part(split_part(reason, ':', 1), ';', 1), count(*) "
            "FROM forward_t30_decisions GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
    except duckdb.CatalogException:
        dec = []
    if dec:
        lines.append(
            "- games decided: "
            + "; ".join(f"{o}/{(r or 'ok').split(';')[0]}={n}" for o, r, n in dec)
        )
    else:
        lines.append("- no T-30 decisions recorded yet.")
    prim = {(r[0], r[6], r[7]): r for r in props if r[5] == PROPS_PRIMARY}
    t30 = {(r[0], r[6], r[7]): r for r in props if r[5] == T30_MODEL}
    for stat in sorted({k[0] for k in t30}):
        keys = sorted((k for k in t30 if k[0] == stat and k in prim), key=str)
        if not keys:
            continue
        d = np.array([str(t30[k][1]) for k in keys])
        dc = np.array([t30[k][2] - prim[k][2] for k in keys], dtype=float)
        n_games = len({k[1] for k in keys})
        lines.append(
            f"- {stat}: delta CRPS {_fmt(cluster_bootstrap_mean(dc, d))}; "
            f"{len(keys)} paired player-games over {n_games} games"
        )
        if len(keys) < 300:
            lines.append(f"  - CAUTION: only {len(keys)} paired player-games for {stat}.")
    if not t30:
        lines.append("- no settled T-30 prop rows yet.")
    path = lineups_db or DEFAULT_LINEUPS_DB
    if path.exists():
        try:
            from nba.lineups.analysis import render_collector_section

            lcon = connect_lineups(path, read_only=True, retries=2)
            try:
                lines += render_collector_section(con, lcon)
            finally:
                lcon.close()
        except Exception as exc:  # the report must never fail on an auxiliary monitor
            lines.append(f"- lineup collector measurements unavailable: {exc!r}")
    else:
        lines.append("- no lineup collector database yet.")
    return lines


T15_MODEL = "props_context_residual_t15"


def _paired_t15_section(con: duckdb.DuckDBPyConnection, props: list[tuple[Any, ...]]) -> list[str]:
    """DESCRIPTIVE sibling arm (docs/prereg/FORWARD_T15_2026_27.md): props_context_residual_t15
    (confirmed lineup known at T-15) minus the production T-60 row, and minus the T-30 arm on the
    player-games scored by BOTH arms. Negative = T-15 better. No decision weight; the immutable
    checkpoint snapshots are the only decision input."""
    lines = ["", f"### Paired SHADOW (descriptive): {T15_MODEL} minus {PROPS_PRIMARY}"]
    try:
        dec = con.execute(
            "SELECT outcome, split_part(split_part(reason, ':', 1), ';', 1), count(*) "
            "FROM forward_t15_decisions GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
    except duckdb.CatalogException:
        dec = []
    if dec:
        lines.append(
            "- games decided: "
            + "; ".join(f"{o}/{(r or 'ok').split(';')[0]}={n}" for o, r, n in dec)
        )
    else:
        lines.append("- no T-15 decisions recorded yet.")
    prim = {(r[0], r[6], r[7]): r for r in props if r[5] == PROPS_PRIMARY}
    t30 = {(r[0], r[6], r[7]): r for r in props if r[5] == T30_MODEL}
    t15 = {(r[0], r[6], r[7]): r for r in props if r[5] == T15_MODEL}
    for stat in sorted({k[0] for k in t15}):
        for label, base in (("T-60", prim), ("T-30", t30)):
            keys = sorted((k for k in t15 if k[0] == stat and k in base), key=str)
            if not keys:
                continue
            d = np.array([str(t15[k][1]) for k in keys])
            dc = np.array([t15[k][2] - base[k][2] for k in keys], dtype=float)
            n_games = len({k[1] for k in keys})
            lines.append(
                f"- {stat} vs {label}: delta CRPS {_fmt(cluster_bootstrap_mean(dc, d))}; "
                f"{len(keys)} paired player-games over {n_games} games"
            )
    if not t15:
        lines.append("- no settled T-15 prop rows yet.")
    return lines


def _late_scratch_section(con: duckdb.DuckDBPyConnection, lineups_db: Path | None) -> list[str]:
    try:
        from nba.daily.late_scratch import late_scratch_section

        return late_scratch_section(con, lineups_db)
    except Exception as exc:  # the report must never fail on an auxiliary section
        return ["", f"## Late scratches: unavailable ({exc!r})"]


def _checkpoint_section() -> list[str]:
    """Pre-registered immutable checkpoint snapshots (the only decision inputs, prereg rule 10)."""
    try:
        from nba.daily.checkpoint import latest_snapshots_section

        return latest_snapshots_section()
    except Exception as exc:  # the report must never fail on an auxiliary section
        return ["", "## Pre-registered checkpoints", f"- unavailable: {exc!r}"]


def _independence_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Cross-game independence monitor for parlay legs (nba.parlay.independence_check)."""
    try:
        from nba.parlay.independence_check import forward_section

        return forward_section(con)
    except Exception as exc:  # report must never fail because of an auxiliary monitor
        return ["", "## Cross-game independence check (forward)", f"- unavailable: {exc!r}"]


def _pplay_shadow_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    """SHADOW: recent-role P(play) (model R, ledger T458) vs production's hurdle P(play), scored on
    the same player-games (log loss, lower is better). Descriptive; promotion is Devin's."""
    head = ["", "## P(play) shadow: recent-role model R vs production (SHADOW, never primary)"]
    try:
        n_rows = con.execute("SELECT count(*) FROM forward_pplay_shadow").fetchone()
        rows = con.execute(
            "SELECT game_date, ll_prod, ll_r, in_prod_log FROM forward_pplay_scores "
            "WHERE status = 'scored' AND ll_r IS NOT NULL"
        ).fetchall()
    except duckdb.CatalogException:
        return []  # no shadow table yet: no briefing line
    if not n_rows or not n_rows[0]:
        return []
    if not rows:
        return head + [f"- {int(n_rows[0])} shadow rows logged, none settled yet."]
    lines = list(head)
    for label, sub in (
        ("all rostered not-OUT", rows),
        ("logged by production", [r for r in rows if r[3]]),
        ("dropped by production", [r for r in rows if not r[3]]),
    ):
        if not sub:
            continue
        d = np.array([str(r[0]) for r in sub])
        lp = np.array([r[1] for r in sub], dtype=float)
        lr = np.array([r[2] for r in sub], dtype=float)
        lines.append(
            f"- {label}: production {_fmt(cluster_bootstrap_mean(lp, d))}; "
            f"R {_fmt(cluster_bootstrap_mean(lr, d))}; "
            f"R minus production {_fmt(cluster_bootstrap_mean(lr - lp, d), 5)}"
        )
    try:  # the live confirmation of R against the comparators C and C' (same rows, paired)
        comp = con.execute(
            "SELECT game_date, ll_r, ll_c, ll_cp FROM forward_pplay_scores "
            "WHERE status = 'scored' AND ll_r IS NOT NULL AND ll_c IS NOT NULL "
            "AND ll_cp IS NOT NULL"
        ).fetchall()
    except duckdb.CatalogException:
        comp = []
    if comp:
        d = np.array([str(r[0]) for r in comp])
        lr = np.array([r[1] for r in comp], dtype=float)
        for label, idx in (("C", 2), ("C'", 3)):
            lc = np.array([r[idx] for r in comp], dtype=float)
            lines.append(
                f"- R minus {label} ({len(comp)} player-games): "
                f"{_fmt(cluster_bootstrap_mean(lr - lc, d), 5)}"
            )
    from nba.daily.pplay_shadow.settle import parity_check

    _status, parity_line = parity_check(con)
    if parity_line:
        lines.append(parity_line)
    lines.append(
        f"- {len(rows)} settled player-games over {len({r[0] for r in rows})} dates "
        "(R minus production < 0 favours R; the 2025 holdout measured R minus the C comparator "
        "at -0.0205, not production's own hurdle)."
    )
    return lines


def _avg_shadow_section(con: duckdb.DuckDBPyConnection, season: int = 2026) -> list[str]:
    """SHADOW (AVG_SHADOW_LIVE_2): counts and gates only (no delta on a non-look day)."""
    from nba.daily.avg_shadow.display import section

    return section(con, season)


def _primary_source_section(con: duckdb.DuckDBPyConnection, season: int) -> list[str]:
    """Source counts for the latest slate day, so a silent fallback is visible (read-only SQL).

    Per stat (regular-season 002 games): production prop triples (game, player) with a pre-tip
    ``props_context_residual`` row, how many also have a pre-tip AVG shadow row, which model the
    AVG row's A0 grid came from (``a0_source``), and how many pre-tip rows that source logged on
    the day. Pts reads ``props_context_residual_lt``: if that variant stops logging, the app's
    pts routing falls back to production (``avg_inputs_changed``) and this line shows
    ``a0 source ... 0 rows``. P(play): latest pre-tip shadow rows with R vs production only.
    The app's exact routing is ``nba.export.best_model`` (layering keeps it off this path), so
    these are coverage counts, not the app's final per-row verdict."""
    if season != FORWARD_SEASON:
        return []
    day = con.execute(
        "SELECT max(g.game_date) FROM forward_predictions p JOIN games g USING (game_id) "
        "WHERE p.game_id LIKE '0022%' AND g.season = ?",
        [season],
    ).fetchone()
    if not day or day[0] is None:
        return []
    d = day[0]
    lines = ["", f"## Best-model source counts (latest slate day {d}, app primary routing inputs)"]
    prod = dict(
        con.execute(
            "SELECT p.target, count(DISTINCT (p.game_id, p.player_id)) FROM forward_predictions p "
            "JOIN games g USING (game_id) WHERE g.game_date = ? AND p.game_id LIKE '002%' "
            "AND p.model_name = 'props_context_residual' AND p.made_at < p.tipoff "
            "AND p.target <> 'win_prob_home' GROUP BY 1",
            [d],
        ).fetchall()
    )
    avg = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT a.stat, count(DISTINCT (a.game_id, a.player_id)), "
            "string_agg(DISTINCT coalesce(a.a0_source, '?'), '/') FROM forward_avg_shadow a "
            "JOIN games g USING (game_id) WHERE g.game_date = ? AND a.game_id LIKE '002%' "
            "AND a.made_at < a.tipoff_utc GROUP BY 1",
            [d],
        ).fetchall()
    }
    logged = dict(
        con.execute(
            "SELECT p.model_name, count(*) FROM forward_predictions p JOIN games g USING (game_id) "
            "WHERE g.game_date = ? AND p.game_id LIKE '002%' AND p.made_at < p.tipoff "
            "AND p.model_name LIKE 'props_context_residual%' GROUP BY 1",
            [d],
        ).fetchall()
    )
    if not prod:
        return lines + ["- no pre-tip production prop rows."]
    any_avg = any(v[0] for v in avg.values())
    for stat in sorted(prod):
        n_p, (n_a, src) = prod[stat], avg.get(stat, (0, "-"))
        flag = ""
        if any_avg and n_a == 0:
            flag = "  **FALLBACK: no AVG rows for this stat while others have them**"
        elif src and any(logged.get(x, 0) == 0 for x in src.split("/") if x not in ("?", "-")):
            flag = "  **FALLBACK: AVG a0 source logged 0 rows**"
        lines.append(
            f"- {stat}: production triples {n_p}, with AVG {n_a}, no AVG {n_p - n_a}; "
            f"a0 source {src}{flag}"
        )
    pp = con.execute(
        "SELECT count(*), count(p_play_r) FROM (SELECT DISTINCT ON (game_id, player_id) "
        "p_play_r FROM forward_pplay_shadow s JOIN games g USING (game_id) "
        "WHERE g.game_date = ? AND s.game_id LIKE '002%' AND s.made_at < s.tipoff "
        "ORDER BY game_id, player_id, s.made_at DESC, s.run_id DESC)",
        [d],
    ).fetchone()
    n_pp, n_r = (int(pp[0]), int(pp[1])) if pp else (0, 0)
    lines.append(f"- P(play): R {n_r}, production only {n_pp - n_r} (latest shadow row per player)")
    return lines


def _combo_shadow_section(con: duckdb.DuckDBPyConnection, season: int = 2026) -> list[str]:
    """SHADOW (COMBO_STOCKS_SHADOW): counts and gates only (no delta on a non-look day)."""
    from nba.daily.combo_shadow.display import section

    return section(con, season)


def _guarded_section(
    name: str, fn: Callable[..., list[str]], con: duckdb.DuckDBPyConnection, *args: Any
) -> list[str]:
    """A shadow section never breaks the briefing: any exception becomes one reported line."""
    try:
        return fn(con, *args)
    except Exception as exc:
        return ["", f"## {name}", f"- unavailable: {type(exc).__name__}: {exc}"[:300]]


def _full_shadow_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    """SHADOW: RECENT_ROLE_FULL (props heads + lower-tail pi with the five recent-role columns) vs
    production, integer-support CRPS, paired and date-clustered. Descriptive; the binding rule is
    docs/prereg/RECENT_ROLE_FULL.md (Stage 1 and 2 are the claim) and the live log for the joint
    forecast; promotion is Devin's."""
    head = ["", "## FULL shadow: recent-role heads + pi vs production (SHADOW, never primary)"]
    try:
        n_rows = con.execute("SELECT count(*) FROM forward_full_shadow").fetchone()
        rows = con.execute(
            "SELECT stat, game_date, played, crps_prod, crps_full, p_play_r, crps_joint_prod, "
            "crps_joint_full, crps_joint_rprod FROM forward_full_scores WHERE status = 'scored'"
        ).fetchall()
    except duckdb.CatalogException:
        return []  # no shadow table yet: no briefing line
    if not n_rows or not n_rows[0]:
        return []
    if not rows:
        return head + [f"- {int(n_rows[0])} shadow rows logged, none settled yet."]
    lines = list(head)
    for stat in ("pts", "reb", "ast", "fg3m"):
        sub = [r for r in rows if r[0] == stat]
        cond = [r for r in sub if r[2]]
        if cond:
            d = np.array([str(r[1]) for r in cond])
            delta = np.array([r[4] - r[3] for r in cond], dtype=float)
            lines.append(
                f"- {stat} conditional on playing: FULL minus production CRPS "
                f"{_fmt(cluster_bootstrap_mean(delta, d), 5)}"
            )
        both = [r for r in sub if r[7] is not None]
        if both:
            d = np.array([str(r[1]) for r in both])
            jp = np.array([r[6] for r in both], dtype=float)
            jf = np.array([r[7] for r in both], dtype=float)
            jr = np.array([r[8] for r in both], dtype=float)
            lines.append(
                f"- {stat} joint (incl. listed DNP, {len(both)} player-games): "
                f"R x FULL minus production {_fmt(cluster_bootstrap_mean(jf - jp, d), 5)}; "
                f"R x production minus production {_fmt(cluster_bootstrap_mean(jr - jp, d), 5)}; "
                f"R x FULL minus R x production {_fmt(cluster_bootstrap_mean(jf - jr, d), 5)}"
            )
    lines.append(
        f"- {len(rows)} settled player-stats over {len({r[1] for r in rows})} dates "
        "(< 0 favours FULL; joint = mixture CRPS of p x 19 stored quantiles + (1 - p) at 0 "
        "vs the stat, DNP as 0)."
    )
    return lines


def build_report(
    con: duckdb.DuckDBPyConnection,
    season: int = FORWARD_SEASON,
    lineups_db: Path | None = None,
) -> str:
    ensure_tables(con)
    n_games, trigger = rollover_status(con)
    lines = [f"# Forward (out-of-sample) report, season={season}", ""]
    lines.append(
        f"Completed {FORWARD_SEASON}-{(FORWARD_SEASON + 1) % 100:02d} games ingested: "
        f"{n_games} (rollover threshold {ROLLOVER_MIN_GAMES})."
    )
    if trigger:
        lines.append(
            "**HOLDOUT ROLLOVER TRIGGER FIRED** (docs/HOLDOUT_ACCESS_LOG.md rule 3): "
            "re-designate the canonical props holdout as season=2026, fold season=2025 "
            "into the tuning pool, and do not tune on 2026 before its first confirmatory use."
        )
    lines.append("")
    win = con.execute(
        "SELECT game_id, game_date, log_loss, brier, model_name, version FROM forward_scores "
        "WHERE season = ? AND target = 'win_prob_home' AND status = 'scored'",
        [season],
    ).fetchall()
    lines.append("## Win probability (home)")
    if not win:
        lines.append("No settled forward win predictions yet.")
    else:
        for name in sorted({r[4] for r in win}):
            sub = [r for r in win if r[4] == name]
            d = np.array([str(r[1]) for r in sub])
            ll = np.array([r[2] for r in sub], dtype=float)
            br = np.array([r[3] for r in sub], dtype=float)
            lines += [
                f"### {name} {sorted({r[5] for r in sub})}",
                f"- log loss: {_fmt(cluster_bootstrap_mean(ll, d))}",
                f"- Brier: {_fmt(cluster_bootstrap_mean(br, d))}",
                f"- log loss minus naive p=0.5 ({NAIVE_LOG_LOSS:.4f}): "
                f"{_fmt(cluster_bootstrap_mean(ll - NAIVE_LOG_LOSS, d))}",
            ]
        lines += _paired_win_section(win)
        if len({r[0] for r in win}) < 100:
            lines.append(
                f"- CAUTION: only {len({r[0] for r in win})} settled games; CIs are very wide."
            )
    lines += ["", "## Props (CRPS from the stored quantile grid, lower is better)"]
    props = con.execute(
        "SELECT target, game_date, crps, y, pred, model_name, game_id, player_id "
        "FROM forward_scores WHERE season = ? AND target <> 'win_prob_home' "
        "AND status = 'scored'",
        [season],
    ).fetchall()
    dnp = con.execute(
        "SELECT count(*) FROM forward_scores WHERE season = ? AND status = 'dnp'", [season]
    ).fetchone()
    if not props:
        lines.append("No settled forward prop predictions yet.")
    for name in sorted({r[5] for r in props}):
        lines.append(f"### {name}")
        for stat in sorted({r[0] for r in props if r[5] == name}):
            sub = [r for r in props if r[0] == stat and r[5] == name]
            d = np.array([str(r[1]) for r in sub])
            crps = np.array([r[2] for r in sub], dtype=float)
            bias = np.array([r[4] - r[3] for r in sub], dtype=float)
            lines.append(
                f"- {stat}: CRPS {_fmt(cluster_bootstrap_mean(crps, d))}; "
                f"mean bias (pred-actual) {_fmt(cluster_bootstrap_mean(bias, d), 3)}"
            )
    lines += _paired_props_section(props)
    lines += _paired_t30_section(con, props, lineups_db)
    lines += _paired_t15_section(con, props)
    lines.append(f"- DNP / not-in-box (excluded from metrics): {int(dnp[0]) if dnp else 0}")
    lines += _guarded_section("P(play) shadow", _pplay_shadow_section, con)
    lines += _guarded_section("FULL shadow", _full_shadow_section, con)
    lines += _guarded_section("AVG shadow", _avg_shadow_section, con, season)
    lines += _guarded_section("COMBO_STOCKS shadow", _combo_shadow_section, con, season)
    lines += _guarded_section("Best-model sources", _primary_source_section, con, season)
    lines += _late_scratch_section(con, lineups_db)
    lines += _checkpoint_section()
    lines += _independence_section(con)
    lines += [
        "",
        "CIs: percentile bootstrap resampling game dates (2000 draws, seed 0); running CIs are "
        "descriptive, not decision-bearing (only checkpoint snapshots count). "
        "Props are conditional on the player playing.",
    ]
    return "\n".join(lines) + "\n"
