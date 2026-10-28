"""Stage 0 of REPORTER_FEED_LIVE: label-free full-archive COUNTS of allow-list posts (sizing).

``python -m nba.ingest.reporters counts --dry-run`` prints the plan and the worst-case cost and
sends nothing (no token needed). A real run needs the token in the environment, ``--confirm`` and a
cost under ``--max-usd``; every request is checked against ``configs/reporters.yaml``'s month cap
and charged to ``data/reporters/budget.json`` ($0.010 per ``counts/all`` request, docs.x.com
pricing read 2026-10-27).

What it asks: how many posts from the 28 allow-listed handles match restriction phrases, and how
many match status phrases, per day, in the 2023-24 and 2024-25 REGULAR seasons. Counts only: the
endpoint returns no post text. **Season 2025-26 is never queried** (:data:`NOT_AFTER`; the holdout
rule extended to this source). Caveats are the rule's: deleted posts are missing and keyword counts
include recaps and "no restriction", so the numbers are an upper bound on the cap rate, a sizing
input, never a result.

Output: ``data/raw/x_counts_<UTC date>/<query>_<season>_p<k>.json``, one file per page, written
once (a rerun resumes from the pages already on disk).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.ingest.reporters import Allowlist, BudgetExceeded, PollRefused, XClient

#: regular seasons (first game day, last game day), inclusive
SEASONS: dict[str, tuple[date, date]] = {
    "2023-24": (date(2023, 10, 24), date(2024, 4, 14)),
    "2024-25": (date(2024, 10, 22), date(2025, 4, 13)),
}
#: no query may reach into 2025-26 (opens 2025-10-21)
NOT_AFTER = date(2025, 8, 1)
QUERY_MAX_CHARS = 1024  # full-archive counts limit (docs.x.com, 2026-10-27)
DAYS_PER_PAGE = 31  # day granularity, per docs.x.com
DEFAULT_MAX_USD = 0.50

RESTRICTION_PHRASES = (
    '"minutes restriction"',
    '"minute restriction"',
    '"minutes limit"',
    '"minute limit"',
    '"minutes cap"',
    '"limited to"',
    '"restricted to"',
    '"capped at"',
)
STATUS_PHRASES = (
    '"game-time decision"',
    '"game time decision"',
    '"ruled out"',
    '"will not play"',
    '"will play"',
    '"is out"',
    '"is available"',
    '"cleared to play"',
)


@dataclass(frozen=True)
class CountQuery:
    qid: str  # restriction | status
    text: str


def build_queries(allow: Allowlist) -> list[CountQuery]:
    handles = " OR ".join(f"from:{h}" for h in allow.handles())
    if not handles:
        raise PollRefused("configs/reporters.yaml lists no handles; nothing to count")
    out = []
    for qid, phrases in (("restriction", RESTRICTION_PHRASES), ("status", STATUS_PHRASES)):
        text = f"({handles}) ({' OR '.join(phrases)}) -is:retweet"
        if len(text) > QUERY_MAX_CHARS:
            raise PollRefused(
                f"query {qid!r} is {len(text)} chars > {QUERY_MAX_CHARS}; split the handles"
            )
        out.append(CountQuery(qid, text))
    return out


def window(season: str) -> tuple[datetime, datetime]:
    """``[start, end)`` UTC covering the regular season's game days (end = day after the last)."""
    lo, hi = SEASONS[season]
    start = datetime(lo.year, lo.month, lo.day, tzinfo=UTC)
    end = datetime(hi.year, hi.month, hi.day, tzinfo=UTC) + timedelta(days=1)
    if end.date() > NOT_AFTER:
        raise PollRefused(f"{season}: window ends {end:%Y-%m-%d}; season 2025-26 is never queried")
    return start, end


def pages_for(season: str) -> int:
    start, end = window(season)
    return math.ceil((end - start).days / DAYS_PER_PAGE)


def request_cap(season: str) -> int:
    """Most requests one (query, season) may send: the pages it needs plus ONE spare for a split
    page. It is a hard bound, not an estimate: the loop stops there however the server pages."""
    return pages_for(season) + 1


@dataclass(frozen=True)
class Plan:
    queries: list[CountQuery]
    seasons: list[str]
    max_requests: int  # the hard bound the run enforces
    usd_per_request: float
    expected_requests: int = 0  # what the pages need if none splits

    @property
    def max_usd(self) -> float:
        return round(self.max_requests * self.usd_per_request, 4)


def make_plan(allow: Allowlist, seasons: list[str] | None = None) -> Plan:
    names = seasons or list(SEASONS)
    for s in names:
        if s not in SEASONS:
            raise PollRefused(f"season {s!r} is not one of {sorted(SEASONS)}")
    queries = build_queries(allow)
    n = sum(request_cap(s) for s in names) * len(queries)
    expected = sum(pages_for(s) for s in names) * len(queries)
    return Plan(queries, names, n, allow.usd_per_count_all, expected)


def format_plan(plan: Plan, out_dir: Path) -> str:
    lines = [
        f"counts plan: {len(plan.queries)} queries x seasons {plan.seasons} "
        f"(day granularity, {DAYS_PER_PAGE} days a page)"
    ]
    for q in plan.queries:
        lines.append(f"  {q.qid}: {len(q.text)} chars")
    lines.append(
        f"expected {plan.expected_requests} requests "
        f"(${plan.expected_requests * plan.usd_per_request:.2f}); "
        f"worst case (hard bound) {plan.max_requests} requests x ${plan.usd_per_request:.3f} = "
        f"${plan.max_usd:.2f} -> {out_dir}/"
    )
    lines.append("season 2025-26 is never queried; counts only, no post text is returned")
    return "\n".join(lines)


def run_counts(
    client: XClient, plan: Plan, out_dir: Path, *, max_usd: float = DEFAULT_MAX_USD
) -> dict[str, Any]:
    """Fetch every page of every (query, season), write-once, resuming from files on disk.
    Raises :class:`BudgetExceeded` before the first request if the plan is over ``max_usd``."""
    if plan.max_usd > max_usd + 1e-9:
        raise BudgetExceeded(
            f"counts plan worst case ${plan.max_usd:.2f} > --max-usd ${max_usd:.2f}; refusing"
        )
    client.budget.check_counts(client.now(), plan.max_requests)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"requests": 0, "totals": {}, "truncated": []}
    for q in plan.queries:
        for season in plan.seasons:
            start, end = window(season)
            token: str | None = None
            total = 0
            for k in range(1, request_cap(season) + 1):
                f = out_dir / f"{q.qid}_{season}_p{k}.json"
                if f.exists():
                    body = json.loads(f.read_text())
                else:
                    body = client.counts_all_page(q.text, start, end, token)
                    summary["requests"] += 1
                    with f.open("x") as fh:
                        json.dump(
                            {"query": q.qid, "season": season, "page": k, "response": body}, fh
                        )
                    body = {"response": body}
                resp = body.get("response", body)
                total += sum(int(d.get("tweet_count", 0)) for d in resp.get("data", []))
                token = (resp.get("meta") or {}).get("next_token")
                if not token:
                    break
            else:
                summary["truncated"].append(f"{q.qid}/{season}")  # cap reached, pages remain
            summary["totals"][f"{q.qid}/{season}"] = total
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def default_out_dir(now: datetime | None = None) -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "data" / "raw" / f"x_counts_{(now or datetime.now(UTC)):%Y-%m-%d}"


def command(
    allow: Allowlist,
    *,
    data_dir: Path,
    out_dir: Path,
    seasons: list[str] | None,
    dry_run: bool,
    confirm: bool,
    max_usd: float,
    client_factory: Any = None,
) -> int:
    """The ``counts`` subcommand: 0 ok, 2 refused. ``--dry-run`` sends nothing, needs no token."""
    plan = make_plan(allow, seasons)
    print(format_plan(plan, out_dir))
    if dry_run:
        return 0
    if not confirm:
        print("REFUSED: this spends money; pass --confirm after reading the plan", file=sys.stderr)
        return 2
    client = XClient(allow, data_dir=data_dir) if client_factory is None else client_factory()
    summary = run_counts(client, plan, out_dir, max_usd=max_usd)
    print(f"requests sent: {summary['requests']}")
    for k, v in sorted(summary["totals"].items()):
        print(f"  {k}: {v} matching posts")
    return 0
