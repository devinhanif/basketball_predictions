# ruff: noqa: E501
"""Install-deadline check of the AVG shadow (AVG_SHADOW_LIVE_2, "Refit staleness"): the monthly weights are installed by 12:00 CT on the 1st.

PROPOSED (modeler, 2026-10-14), not wired: nothing imports this module yet. Pure and read-only: it reads ``manifest.json`` files under the bundle root,
writes nothing, and never raises on a missing or unverifiable bundle (that is the finding). Exit codes of ``python -m nba.daily.avg_shadow.install_check``:
0 = ok (or deadline not yet near), 3 = WARN (deadline within ``WARN_DAYS`` and the month's bundle is not installed), 1 = FAIL (deadline passed and the
month's bundle is missing, installed late, or not valid for the month). Dry run: ``--now-ct 2026-11-01T13:00:00`` pretends it is that CT wall time.

Valid (the rule): ``month_block`` = the month, ``fit_through`` strictly before the 1st, ``installed_at`` set. Late: ``installed_at`` after the deadline (rows made between
the deadline and the install are stale; reported in the line, exit 0 once installed: the deadline miss is history, the watchdog and ``stale_model`` carry it).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.daily.avg_shadow.bundle import DEFAULT_ROOT, list_bundles, verify

CT = ZoneInfo("America/Chicago")
DEADLINE_HOUR_CT = 12
WARN_DAYS = 3
OK, FAIL, WARN = 0, 1, 3


@dataclass(frozen=True)
class Verdict:
    month: str
    code: int
    line: str


def deadline_utc(month: str) -> datetime:
    """12:00 CT on the 1st of ``month`` (YYYY-MM) as naive UTC (the shadow's timestamp convention)."""
    y, m = (int(x) for x in month.split("-"))
    d = datetime(y, m, 1, DEADLINE_HOUR_CT, tzinfo=CT)
    return d.astimezone(UTC).replace(tzinfo=None)


def target_month(now_utc: datetime) -> str:
    """The calendar month containing ``now`` in CT (``months_to_judge`` adds the next month inside the last ``WARN_DAYS`` days)."""
    ct = now_utc.replace(tzinfo=UTC).astimezone(CT)
    return f"{ct.year:04d}-{ct.month:02d}"


def months_to_judge(now_utc: datetime) -> list[str]:
    ct = now_utc.replace(tzinfo=UTC).astimezone(CT)
    cur = target_month(now_utc)
    nxt = (date(ct.year, ct.month, 1) + timedelta(days=32)).replace(day=1)
    out = [cur]
    if nxt - ct.date() <= timedelta(days=WARN_DAYS):
        out.append(f"{nxt.year:04d}-{nxt.month:02d}")
    return out


def check_month(root: Path, month: str, now_utc: datetime) -> Verdict:
    deadline = deadline_utc(month)
    first = date.fromisoformat(f"{month}-01")
    found = None
    for p in list_bundles(root):
        try:
            b = verify(p)
        except (
            ValueError,
            OSError,
            KeyError,
        ) as e:  # an unreadable or tampered bundle is a finding, not a crash
            return Verdict(
                month, FAIL, f"AVG INSTALL FAIL {month}: bundle {p.name} does not verify ({e})"
            )
        if b.month_block == month and b.installed_at is not None:
            found = b
    if found is None:
        if now_utc >= deadline:
            return Verdict(
                month,
                FAIL,
                f"AVG INSTALL FAIL {month}: no installed bundle; deadline 12:00 CT on {month}-01 passed; rows are stale_model until installed",
            )
        left = deadline - now_utc
        if left <= timedelta(days=WARN_DAYS):
            return Verdict(
                month,
                WARN,
                f"AVG INSTALL WARN {month}: no installed bundle; {left.total_seconds() / 3600:.0f} h to the 12:00 CT {month}-01 deadline",
            )
        return Verdict(month, OK, f"avg install {month}: not due ({left.days} d to the deadline)")
    if found.fit_through >= first:
        return Verdict(
            month,
            FAIL,
            f"AVG INSTALL FAIL {month}: bundle {found.model_id[:8]} has fit_through {found.fit_through} not before {first} (invalid for the month)",
        )
    assert found.installed_at is not None
    late = max((found.installed_at - deadline).total_seconds() / 3600, 0.0)
    note = f"; LATE by {late:.1f} h (rows in between were stale_model)" if late else ""
    return Verdict(
        month,
        OK,
        f"avg install {month}: ok (bundle {found.model_id[:8]}, fit_through {found.fit_through}, installed {found.installed_at:%Y-%m-%d %H:%M} UTC{note})",
    )


def check(root: Path, now_utc: datetime) -> list[Verdict]:
    return [check_month(root, m, now_utc) for m in months_to_judge(now_utc)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AVG shadow monthly-install deadline check (read-only)"
    )
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument(
        "--now-ct", default=None, help="dry run: pretend it is this CT wall time (ISO, naive)"
    )
    a = ap.parse_args(argv)
    if a.now_ct:
        now = (
            datetime.fromisoformat(a.now_ct).replace(tzinfo=CT).astimezone(UTC).replace(tzinfo=None)
        )
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
    vs = check(a.root, now)
    for v in vs:
        print(v.line)
    print(json.dumps({v.month: v.code for v in vs}))
    codes = {v.code for v in vs}
    return FAIL if FAIL in codes else WARN if WARN in codes else OK


if __name__ == "__main__":
    raise SystemExit(main())
