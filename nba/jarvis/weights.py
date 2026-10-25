"""Model weight by stat, earned from the closing-line record (paper-only adaptation).

fair = Pinnacle_no_vig + w_stat x (our model - Pinnacle_no_vig). Every w starts at 0, so nothing
changes until the paper book earns a weight, and it stays in [0, w_max]. The update rules are frozen
in configs/jarvis.yaml (``model_weights``) before any data exists. This adapts JARVIS's own paper
fair price only; it is not a frozen test and never touches the scoring of one. Every evaluation is
appended to ``data/jarvis/model_weights.jsonl`` with n, the CI and the date.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.jarvis.book import PaperBook, cluster_ci, now_naive
from nba.jarvis.config import JarvisConfig

STAT_KEYS = ("pts", "reb", "ast", "fg3m", "combos", "win")


def stat_key(kind: str, stat: str | None) -> str:
    if kind == "game_win":
        return "win"
    if stat in ("pts", "reb", "ast", "fg3m"):
        return str(stat)
    return "combos"


def log_path(cfg: JarvisConfig) -> Path:
    p = Path(str(cfg.model_weights["log"]))
    return p if p.is_absolute() else cfg.root / p


def _records(cfg: JarvisConfig) -> list[dict[str, Any]]:
    p = log_path(cfg)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def load_weights(cfg: JarvisConfig) -> dict[str, float]:
    """Current w per stat: the last logged ``w_after``, else 0."""
    w = dict.fromkeys(STAT_KEYS, 0.0)
    for r in _records(cfg):
        if r.get("stat") in w:
            w[str(r["stat"])] = float(r["w_after"])
    wmax = float(cfg.model_weights["w_max"])
    return {k: min(max(v, 0.0), wmax) for k, v in w.items()}


def blend(pin: float, model: float | None, w: float) -> float:
    """Pinnacle + w x (model - Pinnacle); w = 0 or no model gives Pinnacle unchanged."""
    return pin if model is None or w <= 0.0 else pin + w * (model - pin)


def _leaning_rows(book: PaperBook, stat: str) -> list[tuple[date, float]]:
    """(slate_date, CLV cents) of settled single-leg plays on this stat whose model fair was ABOVE
    Pinnacle's for the bought side (the model leaned toward the bet) and that have a close."""
    rows = book._rows(
        "SELECT any_value(p.slate_date), any_value(p.kind), any_value(p.stat), avg(s.clv_cents) "
        "FROM plays p JOIN scores s USING (play_id) "
        "WHERE p.edge_label IN ('strong','thin','none') AND p.pin_prob IS NOT NULL "
        "AND p.model_prob IS NOT NULL AND p.model_prob > p.pin_prob AND s.status = 'settled' "
        "AND s.clv_cents IS NOT NULL GROUP BY p.bundle_id HAVING count(*) = 1"
    )
    return [
        (r[0], float(r[3]))
        for r in rows
        if stat_key(str(r[1]), None if r[2] is None else str(r[2])) == stat
    ]


def update_weights(
    cfg: JarvisConfig,
    book: PaperBook,
    *,
    today: date,
    now: datetime | None = None,
    display_only: bool = False,
) -> list[dict[str, Any]]:
    """Run the frozen weekly rule once (at most every ``min_days_between_updates`` days). Returns
    the records appended (one per stat, change or hold). ``display_only`` evaluates and returns
    without logging or stepping anything, so no flag can add a second step inside the week."""
    mw = cfg.model_weights
    recs = _records(cfg)
    if recs and not display_only:
        last = max(date.fromisoformat(str(r["date"])) for r in recs)
        if (today - last).days < int(mw["min_days_between_updates"]):
            return []
    cur = load_weights(cfg)
    out: list[dict[str, Any]] = []
    for stat in STAT_KEYS:
        vals = _leaning_rows(book, stat)
        dates = {d for d, _ in vals}
        n = len(vals)
        mean = sum(v for _, v in vals) / n if n else None
        ci = cluster_ci(vals, level=float(mw["ci_level"])) if n else None
        lo = None if ci is None else ci[1]
        w0 = cur[stat]
        w1, action = w0, "hold"
        if n >= int(mw["min_n"]) and len(dates) >= int(mw["min_dates"]) and mean is not None:
            if mean > 0 and lo is not None and lo > 0:
                w1, action = min(w0 + float(mw["step"]), float(mw["w_max"])), "raise"
            elif mean < 0:
                w1, action = max(w0 - float(mw["step"]), 0.0), "lower"
        out.append(
            {
                "date": today.isoformat(),
                "ts": (now or now_naive()).isoformat(timespec="seconds"),
                "stat": stat,
                "action": action,
                "w_before": round(w0, 4),
                "w_after": round(w1, 4),
                "n": n,
                "n_dates": len(dates),
                "mean_clv_cents": None if mean is None else round(mean, 3),
                "ci_level": float(mw["ci_level"]),
                "ci_lo_cents": None if lo is None else round(lo, 3),
            }
        )
    if display_only:  # --force: show what the rule would say; never steps a weight
        return out
    p = log_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    return out


def render_changes(recs: list[dict[str, Any]]) -> list[str]:
    """Morning-recap lines: every change, or one line saying all held."""
    ch = [r for r in recs if r["action"] != "hold"]
    if not recs:
        return []
    if not ch:
        return ["Model weights: all held (frozen weekly rule)"]
    return [
        f"Model weight {r['stat']}: {r['w_before']:.1f} -> {r['w_after']:.1f} "
        f"(n {r['n']}, mean CLV {r['mean_clv_cents']:+.2f}c, 80% lower {r['ci_lo_cents']})"
        for r in ch
    ]
