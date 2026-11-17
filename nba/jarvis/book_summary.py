"""The paper book's weekly summary and CLV kill switch (moved verbatim from ``nba.jarvis.book``,
which re-exports every name here). Reads only through a ``PaperBook`` it is given.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from nba.jarvis.config import LABELS, JarvisConfig

if TYPE_CHECKING:
    from nba.jarvis.book import PaperBook


# ------------------------------------------------------------------------------ summary


def cluster_ci(
    values: Sequence[tuple[date, float]],
    *,
    n_boot: int = 2000,
    seed: int = 20261025,
    level: float = 0.95,
) -> tuple[float, float, float] | None:
    """(mean, lo, hi): bootstrap over slate-date clusters (resample dates, pooled mean). None with
    fewer than 2 date clusters (a CI would be a lie)."""
    by: dict[date, list[float]] = {}
    for d, v in values:
        by.setdefault(d, []).append(v)
    if len(by) < 2:
        return None
    keys = sorted(by)
    rng = random.Random(seed)
    allv = [v for _, v in values]
    means = []
    for _ in range(n_boot):
        tot = 0.0
        n = 0
        for _k in keys:
            vs = by[keys[rng.randrange(len(keys))]]
            tot += sum(vs)
            n += len(vs)
        means.append(tot / n)
    means.sort()
    a = (1.0 - level) / 2.0
    lo = means[int(a * n_boot)]
    hi = means[min(n_boot - 1, int((1.0 - a) * n_boot))]
    return sum(allv) / len(allv), lo, hi


WEEKLY_LABELS = (*LABELS, "daily_dollar", "pick", "live_none")


def _bundles(
    book: PaperBook, since: date, until: date
) -> list[tuple[date, str, str, float, float, float | None, bool]]:
    """One row per fully scored bundle: (date, label, status, pnl, staked, clv, hit). A multi-leg
    play counts once (CLV is the mean of its legs); a re-emission never adds a row (see record)."""
    rows = book._rows(
        "SELECT any_value(p.slate_date), any_value(p.edge_label), "
        "CASE WHEN bool_and(s.status = 'settled') THEN 'settled' ELSE 'void' END, "
        "sum(s.pnl_usd), sum(p.stake_usd), avg(s.clv_cents), sum(s.pnl_usd) > 0 "
        "FROM plays p JOIN scores s USING (play_id) WHERE p.slate_date BETWEEN ? AND ? "
        "GROUP BY p.bundle_id HAVING count(s.play_id) = count(*)",
        [since, until],
    )
    return [
        (
            r[0],
            str(r[1]),
            str(r[2]),
            float(r[3]),
            float(r[4]),
            None if r[5] is None else float(r[5]),
            bool(r[6]),
        )
        for r in rows
    ]


def weekly_summary(
    cfg: JarvisConfig, book: PaperBook, *, until: date, days: int = 7
) -> dict[str, Any]:
    since = until - timedelta(days=days - 1)
    bundles = _bundles(book, since, until)
    emitted = book._rows(
        "SELECT count(DISTINCT bundle_id) FROM plays WHERE slate_date BETWEEN ? AND ?",
        [since, until],
    )
    settled = [b for b in bundles if b[2] == "settled"]
    out: dict[str, Any] = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "n_emitted": int(emitted[0][0]) if emitted else 0,
        "n_scored": len(settled),
        "n_void": sum(1 for b in bundles if b[2] == "void"),
        "labels": {},
        "min_settled": cfg.min_settled,
        "min_dates": cfg.min_settled_dates,
    }
    for label in (*WEEKLY_LABELS, "all"):
        if label == "all":
            sel = [b for b in settled if b[1] in LABELS]
        else:
            sel = [b for b in settled if b[1] == label]
        clv = [(b[0], b[5]) for b in sel if b[5] is not None]
        dates = {b[0] for b in sel}
        ci = cluster_ci([(d, float(v)) for d, v in clv if v is not None])
        out["labels"][label] = {
            "n": len(sel),
            "n_dates": len(dates),
            "n_clv": len(clv),
            "clv_null_rate": None if not sel else round(1 - len(clv) / len(sel), 3),
            "hit_rate": None if not sel else round(sum(1 for b in sel if b[6]) / len(sel), 3),
            "pnl_usd": round(sum(b[3] for b in sel), 2),
            "staked_usd": round(sum(b[4] for b in sel), 2),
            "clv_mean_cents": None
            if not clv
            else round(sum(float(v) for _, v in clv if v is not None) / len(clv), 3),
            "clv_ci95_cents": None if ci is None else [round(ci[1], 3), round(ci[2], 3)],
            "claim_ok": len(sel) >= cfg.min_settled and len(dates) >= cfg.min_settled_dates,
        }
    return out


@dataclass(frozen=True)
class KillState:
    """Closing-line-value kill switch (docs/TRADING_CONTEXT.md): once tripped it stays on ("until
    fixed") until Devin clears it with ``killswitch --clear``, which logs a dated row."""

    active: bool
    n: int
    mean_clv: float | None
    latched: bool = False
    since: date | None = None

    def message(self) -> str:
        mean = "n/a" if self.mean_clv is None else f"{self.mean_clv:+.2f}c"
        if self.latched:
            return (
                f"KILL SWITCH LATCHED since {self.since} (mean CLV {mean} over {self.n} settled "
                "thin/strong/arbitrage bets): every stake is at the entertainment size until Devin "
                "clears it (python -m nba.jarvis killswitch --clear)"
            )
        return (
            f"KILL SWITCH: mean CLV {mean} over the last {self.n} settled thin/strong/arbitrage "
            "bets is negative, so every stake is at the entertainment size until Devin clears it"
        )


KILL_LABELS = ("arbitrage", "strong", "thin")  # none and arb_candidate claim no edge: not counted


def kill_switch(cfg: JarvisConfig, book: PaperBook) -> KillState:
    """Mean CLV of the last ``kill_switch.min_bets`` settled thin/strong/arbitrage bets with a
    two-sided close, counted from the last time Devin cleared it. Below zero it trips (no CI, no
    losing-streak rule) and the trip is LATCHED (see ``latch_kill_switch``): a later good run does
    not clear it. Picks, the Daily Dollar, ``none`` plays and unconfirmed candidates do not
    count."""
    log = book.kill_rows()
    last = log[-1] if log else None
    if last is not None and last[2] == "tripped":
        return KillState(True, int(last[3]), last[4], True, last[1])
    cleared = [r[0] for r in log if r[2] == "cleared"]
    since = max(cleared) if cleared else None
    marks = ",".join("?" for _ in KILL_LABELS)
    rows = book._rows(
        "SELECT avg(s.clv_cents) FROM plays p JOIN scores s USING (play_id) "
        f"WHERE p.edge_label IN ({marks}) AND s.status = 'settled' "
        + ("AND p.ts > ? " if since is not None else "")
        + "GROUP BY p.bundle_id HAVING avg(s.clv_cents) IS NOT NULL "
        "ORDER BY any_value(p.ts) DESC LIMIT ?",
        [*KILL_LABELS, *([since] if since is not None else []), cfg.kill_min_bets],
    )
    if len(rows) < cfg.kill_min_bets:
        return KillState(False, len(rows), None)
    mean = sum(float(r[0]) for r in rows) / len(rows)
    return KillState(mean < 0.0, len(rows), mean)


def latch_kill_switch(
    cfg: JarvisConfig, book: PaperBook, today: date, now: datetime | None = None
) -> KillState:
    """Persist a trip (write path; the runner calls it outside dry-run)."""
    ks = kill_switch(cfg, book)
    if ks.active and not ks.latched:
        book.log_kill("tripped", today, ks.n, ks.mean_clv, "mean CLV below 0", now)
        return kill_switch(cfg, book)
    return ks


def clear_kill_switch(
    cfg: JarvisConfig, book: PaperBook, today: date, note: str = "", now: datetime | None = None
) -> KillState:
    """Devin's reset: logs a dated row and restarts the count from now."""
    book.log_kill("cleared", today, 0, None, note, now)
    return kill_switch(cfg, book)


def render_weekly(s: dict[str, Any]) -> str:
    lines = [
        f"JARVIS paper book {s['since']} .. {s['until']}: emitted {s['n_emitted']}, "
        f"scored {s['n_scored']}, void {s['n_void']}",
        "label          n dates hit%  P&L$   staked$ CLV mean c (null%)  "
        "CI95 (date-clustered)  claim?",
    ]
    for label, d in s["labels"].items():
        ci = (
            "n/a (<2 dates)"
            if d["clv_ci95_cents"] is None
            else f"[{d['clv_ci95_cents'][0]}, {d['clv_ci95_cents'][1]}]"
        )
        clv = "n/a" if d["clv_mean_cents"] is None else f"{d['clv_mean_cents']}"
        claim = "yes" if d["claim_ok"] else "insufficient sample"
        hit = "n/a" if d["hit_rate"] is None else f"{d['hit_rate'] * 100:.0f}"
        nul = "n/a" if d["clv_null_rate"] is None else f"{d['clv_null_rate'] * 100:.0f}"
        lines.append(
            f"{label:<13} {d['n']:>3} {d['n_dates']:>5} {hit:>4} {d['pnl_usd']:>7} "
            f"{d['staked_usd']:>8} {clv:>9} ({nul:>3})  {ci:<21} {claim}"
        )
    lines.append(
        f"A claim needs n >= {s['min_settled']} settled over >= {s['min_dates']} dates; below that "
        "these are descriptive and prove nothing."
    )
    return "\n".join(lines)
