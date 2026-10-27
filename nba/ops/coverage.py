"""Daily data-coverage check: did every input of tonight's forecasts actually arrive?

Read-only and offline: opens ``nba.duckdb``, ``market_asof.duckdb`` and the schedule/roster caches
and the ops logs, never writes, never fetches. Born of three quiet failures on 2026-10-20 (roster
fetch failed so players were forecast on last season's teams; schedule fetch failed so tonight's
games were missing from ``games``; sportsbook lines never paired with forecasts because the
forecast ``made_at`` preceded the line's ``received_at``) that the app showed no sign of.

    python -m nba.ops.coverage --date 2026-10-20 [--json] [--root PATH]

One line per check, ``OK|WARN|FAIL <check>: <numbers>``; exit 0 when nothing FAILs, 3 otherwise.

Clocks (verified against the stored data): ``forward_predictions.made_at``/``tipoff``,
``forward_scores.scored_at`` and ``live_book_odds`` times are naive UTC;
``player_availability.as_of``
is the naive US-Eastern clock of the report slot; the ops logs carry the Mac's local wall clock.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.features.game_tipoff import build_game_tipoff

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]
PROPS_MODEL = "props_context_residual"
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
MIN_ROSTER = 13
INJURY_OK_MIN = 60
INJURY_WARN_MIN = 30
PAIRING_WARN = 0.80
PRETIP_GAP_MIN = 40
SCORE_LAG = timedelta(hours=4)
EXIT_COVERAGE_FAIL = 3
LOCK_RETRY_S = 30
LOCK_MAX_S = 600
OK, WARN, FAIL = "OK", "WARN", "FAIL"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass
class Result:
    status: str
    check: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"{self.status} {self.check}: {self.text}"


@dataclass
class Game:
    game_id: str
    tip_utc: datetime  # naive UTC
    home: int
    away: int
    real_tip: bool

    @property
    def tip_et(self) -> datetime:
        return self.tip_utc.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)


@dataclass
class Paths:
    root: Path
    slate_date: date
    nba_db: Path
    market_db: Path
    rosters: Path
    schedule_cache: Path
    schedule_dir: Path
    ops: Path

    @classmethod
    def under(cls, root: Path, d: date) -> Paths:
        return cls(
            root=root,
            slate_date=d,
            nba_db=root / "nba.duckdb",
            market_db=root / "data" / "markets" / "market_asof.duckdb",
            rosters=root / "data" / "rosters" / d.isoformat(),
            schedule_cache=root / "data" / "schedule_cache",
            schedule_dir=root / "data" / "schedule",
            ops=root / "data" / "ops",
        )

    def log(self, kind: str) -> Path:
        return self.ops / f"{kind}_{self.slate_date:%Y%m%d}.log"


def season_label(d: date) -> str:
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def connect_ro(
    path: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
    retry_s: float = LOCK_RETRY_S,
    max_s: float = LOCK_MAX_S,
) -> duckdb.DuckDBPyConnection:
    """Open read-only; on a writer's lock sleep and retry up to ``max_s`` (the live job writes for
    2-8 minutes at :20 and :50). Never opens with write access."""
    waited = 0.0
    while True:
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or waited >= max_s:
                raise
            sleep(retry_s)
            waited += retry_s


def _ts(x: datetime) -> str:
    return x.strftime("%H:%M")


def _mins(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


# ------------------------------------------------------------------ slate


def load_slate(p: Paths) -> tuple[list[Game], str | None]:
    """Games of the ET date from the schedule cache, with real tips where the source has them."""
    f = p.schedule_cache / f"schedule_{season_label(p.slate_date)}.json"
    try:
        rows = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        return [], f"schedule cache unreadable ({f}): {e}"
    try:
        tips = build_game_tipoff(p.schedule_dir)
    except Exception:  # unreadable parquet: everything falls to the cache time, reported as proxy
        tips = pl.DataFrame(schema={"game_id": pl.Utf8, "tipoff_utc": pl.Datetime("us")})
    real = {r["game_id"]: r["tipoff_utc"] for r in tips.iter_rows(named=True)}
    out = []
    for gid, t, h, a in rows:
        cache_tip = datetime.fromisoformat(t)
        if cache_tip.replace(tzinfo=UTC).astimezone(ET).date() != p.slate_date:
            continue
        gid = str(gid)
        out.append(
            Game(gid, real.get(gid, cache_tip), int(h), int(a), gid in real),
        )
    return sorted(out, key=lambda g: (g.tip_utc, g.game_id)), None


# ------------------------------------------------------------------ a. schedule


def check_schedule(nba: duckdb.DuckDBPyConnection, games: Sequence[Game]) -> list[Result]:
    ids = [g.game_id for g in games]
    ph = ",".join("?" for _ in ids)
    have = {
        r[0]: (r[1], r[2])
        for r in nba.execute(
            f"SELECT game_id, home_team, away_team FROM games WHERE game_id IN ({ph})",  # noqa: S608
            ids,
        ).fetchall()
    }
    missing = [g.game_id for g in games if g.game_id not in have]
    unset = [g.game_id for g in games if g.game_id in have and None in have[g.game_id]]
    wrong = [
        g.game_id
        for g in games
        if g.game_id in have and None not in have[g.game_id] and have[g.game_id] != (g.home, g.away)
    ]
    bad = missing + unset + wrong
    out = [
        Result(
            FAIL if bad else OK,
            "schedule",
            f"{len(games) - len(bad)}/{len(games)} slate games in games with home/away"
            + (f"; missing={missing} unset={unset} mismatch={wrong}" if bad else ""),
            {"n": len(games), "missing": missing, "unset": unset, "mismatch": wrong},
        )
    ]
    proxy = [g.game_id for g in games if not g.real_tip]
    out.append(
        Result(
            WARN if proxy else OK,
            "tips",
            f"{len(games) - len(proxy)}/{len(games)} real tip times"
            + (f"; proxy/cache-only={proxy}" if proxy else ""),
            {"proxy": proxy},
        )
    )
    return out


# ------------------------------------------------------------------ b. roster


def read_rosters(d: Path) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for f in sorted(d.glob("*.parquet")) if d.is_dir() else []:
        try:
            ids = pl.read_parquet(f, columns=["player_id"])["player_id"].to_list()
        except Exception:
            continue
        out[int(f.stem)] = {int(i) for i in ids}
    return out


def latest_prod_forecasts(
    nba: duckdb.DuckDBPyConnection, gids: Sequence[str]
) -> list[tuple[str, int, str, datetime]]:
    ph = ",".join("?" for _ in gids)
    return [
        (r[0], int(r[1]), r[2], r[3])
        for r in nba.execute(
            f"""SELECT game_id, player_id, target, made_at FROM forward_predictions
                WHERE model_name = ? AND game_id IN ({ph})
                QUALIFY row_number() OVER (PARTITION BY game_id, player_id, target
                                           ORDER BY made_at DESC, run_id DESC) = 1""",  # noqa: S608
            [PROPS_MODEL, *gids],
        ).fetchall()
    ]


def check_roster(
    nba: duckdb.DuckDBPyConnection, games: Sequence[Game], rosters: dict[int, set[int]], rdir: Path
) -> list[Result]:
    teams = sorted({t for g in games for t in (g.home, g.away)})
    bad_teams = [t for t in teams if len(rosters.get(t, ())) < MIN_ROSTER]
    out = [
        Result(
            FAIL if bad_teams else OK,
            "roster_files",
            f"{len(teams) - len(bad_teams)}/{len(teams)} slate teams with >={MIN_ROSTER} players "
            f"in {rdir}"
            + (
                f"; short/missing={[(t, len(rosters.get(t, ()))) for t in bad_teams]}"
                if bad_teams
                else ""
            ),
            {"short": bad_teams, "sizes": {str(t): len(rosters.get(t, ())) for t in teams}},
        )
    ]
    team_of: dict[int, set[int]] = defaultdict(set)
    for t, ids in rosters.items():
        for i in ids:
            team_of[i].add(t)
    fc = latest_prod_forecasts(nba, [g.game_id for g in games])
    by_game = {g.game_id: {g.home, g.away} for g in games}
    placed = misplaced = unknown = 0
    bad_by_game: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, int]] = set()
    for gid, pid, _t, _m in fc:
        if (gid, pid) in seen:
            continue
        seen.add((gid, pid))
        ts = team_of.get(pid)
        if not ts:
            unknown += 1
        elif ts & by_game[gid]:
            placed += 1
        else:
            misplaced += 1
            bad_by_game[gid] += 1
    out.append(
        Result(
            FAIL if misplaced else (WARN if unknown else OK),
            "roster_placement",
            f"latest {PROPS_MODEL} players: placed={placed} misplaced={misplaced} unknown={unknown}"
            + (f"; misplaced by game={dict(bad_by_game)}" if misplaced else ""),
            {
                "placed": placed,
                "misplaced": misplaced,
                "unknown": unknown,
                "by_game": dict(bad_by_game),
            },
        )
    )
    return out


# ------------------------------------------------------------------ c/e. logs


_RUN = re.compile(r"^run (?P<id>\w+) date=(?P<date>\S+) (?P<rest>.*)$")


def parse_pretip_runs(text: str) -> list[dict[str, Any]]:
    """Every ``run …`` summary line, with the ``roster_source`` of the ``models:`` line after it."""
    runs: list[dict[str, Any]] = []
    for ln in text.splitlines():
        m = _RUN.match(ln)
        if m:
            kv = dict(re.findall(r"(\w+)=(\S+)", m["rest"]))
            inj = re.search(r"injury=(\S+)(?: \(([^)]*)\))?", m["rest"])
            runs.append(
                {
                    "run_id": m["id"],
                    "slate": kv.get("slate"),
                    "predicted": kv.get("predicted"),
                    "refused_after_tipoff": kv.get("refused_after_tipoff"),
                    "rows": kv.get("rows"),
                    "deduped": int(kv["deduped"]) if "deduped" in kv else None,
                    "injury": inj[1] if inj else None,
                    "injury_as_of": inj[2] if inj else None,
                    "roster_source": None,
                }
            )
        elif runs and runs[-1]["roster_source"] is None and ln.startswith("models:"):
            rs = re.search(r"'roster_source': '([^']*)'", ln)
            runs[-1]["roster_source"] = rs[1] if rs else "n/a"
    return runs


# What the readers below need from a job log: its own timestamped lines, predict's ``run ...``
# summary and the ``models:`` line after it. Everything else (indented echoes of this check's own
# output, library warnings, tracebacks) is dropped while streaming. 2026-10-21..23: the ALERT
# section below printed every ``ALERT:`` line it found, the step output was appended to the same
# log, and the next run found the echoes and printed them again: the pretip log doubled per run
# (9.9 GB, 9.4 GB, 0.5 GB) and reading a whole file killed coverage_yesterday (rc=137).
_KEEP = re.compile(r"^(?:\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] |run \w+ date=|models:)")
_ALERT_LINE = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] ALERT: ")
MAX_ALERTS_SHOWN = 50


def _read(path: Path) -> str | None:
    try:
        with path.open(errors="replace") as f:
            return "".join(ln for ln in f if _KEEP.match(ln))
    except OSError:
        return None


def _alert_lines(path: Path) -> list[str]:
    """Genuine ``[stamp] ALERT: ...`` lines of one log, streamed (echoes are indented)."""
    try:
        with path.open(errors="replace") as f:
            return [ln.rstrip("\n") for ln in f if _ALERT_LINE.match(ln)]
    except OSError:
        return []


def check_injury(
    nba: duckdb.DuckDBPyConnection, games: Sequence[Game], pretip_text: str | None
) -> list[Result]:
    out: list[Result] = []
    for g in games:
        rows = nba.execute(
            "SELECT as_of, status FROM player_availability WHERE game_id = ?", [g.game_id]
        ).fetchall()
        usable_cut = g.tip_et - timedelta(minutes=INJURY_OK_MIN)
        warn_cut = g.tip_et - timedelta(minutes=INJURY_WARN_MIN)
        slots = sorted({r[0] for r in rows})
        ok_slots = [s for s in slots if s <= usable_cut]
        warn_slots = [s for s in slots if s <= warn_cut]
        if ok_slots:
            st, best = OK, ok_slots[-1]
        elif warn_slots:
            st, best = WARN, warn_slots[-1]
        else:
            st, best = FAIL, None
        if best is None:
            out.append(
                Result(
                    FAIL,
                    f"injury[{g.game_id}]",
                    f"no report slot >= {INJURY_WARN_MIN} min before tip {_ts(g.tip_et)} ET "
                    f"({len(slots)} slots stored)",
                    {"game_id": g.game_id, "slots": len(slots)},
                )
            )
            continue
        n_out = sum(1 for r in rows if r[0] == best and str(r[1]).lower() == "out")
        out.append(
            Result(
                st,
                f"injury[{g.game_id}]",
                f"latest usable as_of {best:%H:%M} ET = {_mins(best, g.tip_et):.0f} min before tip "
                f"{_ts(g.tip_et)} ET; OUT players={n_out}; slots={len(slots)}",
                {"game_id": g.game_id, "minutes_before_tip": _mins(best, g.tip_et), "out": n_out},
            )
        )
    runs = parse_pretip_runs(pretip_text) if pretip_text else []
    if not runs:
        out.append(Result(WARN, "pretip_run", "no `run …` line in the pretip log", {}))
    else:
        r = runs[-1]
        rs = r["roster_source"] or "n/a"
        failed = "FAILED" in rs or r["injury"] not in ("ok",)
        out.append(
            Result(
                WARN if failed else OK,
                "pretip_run",
                f"latest run {r['run_id']}: injury={r['injury']} ({r['injury_as_of']}); "
                f"roster_source={rs!r}",
                r,
            )
        )
    return out


def check_forecasts(
    nba: duckdb.DuckDBPyConnection, games: Sequence[Game], pretip_text: str | None
) -> list[Result]:
    out: list[Result] = []
    for g in games:
        n, last, first = nba.execute(
            """SELECT count(*), max(made_at), min(made_at) FROM forward_predictions
               WHERE game_id = ?""",
            [g.game_id],
        ).fetchone() or (0, None, None)
        if not n:
            out.append(
                Result(FAIL, f"forecasts[{g.game_id}]", "no forward_predictions rows", {"rows": 0})
            )
            continue
        post = nba.execute(
            "SELECT count(*) FROM forward_predictions WHERE game_id = ? AND made_at > ?",
            [g.game_id, g.tip_utc],
        ).fetchone()
        n_post = int(post[0]) if post else 0
        out.append(
            Result(
                FAIL if n_post else OK,
                f"forecasts[{g.game_id}]",
                f"rows={n}; latest made_at {last:%H:%M}Z = {_mins(last, g.tip_utc):.0f} min before "
                f"tip {_ts(g.tip_utc)}Z; first {first:%H:%M}Z; rows made after tip={n_post}",
                {"rows": n, "minutes_before_tip": _mins(last, g.tip_utc), "after_tip": n_post},
            )
        )
    runs = parse_pretip_runs(pretip_text) if pretip_text else []
    d = runs[-1]["deduped"] if runs else None
    out.append(
        Result(
            OK if d is not None else WARN,
            "deduped",
            f"latest pretip run deduped={d}" if d is not None else "no deduped= in pretip log",
            {"deduped": d},
        )
    )
    return out


# ------------------------------------------------------------------ d. lines


def check_lines(
    nba: duckdb.DuckDBPyConnection, odds: duckdb.DuckDBPyConnection, games: Sequence[Game]
) -> list[Result]:
    out: list[Result] = []
    gids = [g.game_id for g in games]
    ph = ",".join("?" for _ in gids)
    snaps: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    for gid, kind, ra in odds.execute(
        f"""SELECT game_id, snapshot_kind, min(received_at) FROM live_book_odds
            WHERE game_id IN ({ph}) GROUP BY game_id, snapshot_kind ORDER BY 3""",  # noqa: S608
        gids,
    ).fetchall():
        snaps[gid].append((kind, ra))
    mm = ",".join("?" for _ in MARKET_STAT)
    first_quote: dict[tuple[str, int, str], datetime] = {}
    for gid, pid, market, ra in odds.execute(
        f"""SELECT game_id, player_id, market, min(received_at) FROM live_book_odds
            WHERE game_id IN ({ph}) AND player_id IS NOT NULL AND market IN ({mm})
              AND received_at < tipoff AND received_at < commence_time
              AND point IS NOT NULL AND price_american IS NOT NULL
              AND side IN ('over', 'under')
            GROUP BY game_id, player_id, market""",  # noqa: S608
        [*gids, *MARKET_STAT],
    ).fetchall():
        first_quote[(gid, int(pid), MARKET_STAT[market])] = ra
    fc = latest_prod_forecasts_with_time(nba, gids)
    confirmed = last_confirmed_runs(nba, games)
    for g in games:
        s = snaps.get(g.game_id, [])
        if s:
            last_kind, last_ra = s[-1]
            out.append(
                Result(
                    OK,
                    f"lines[{g.game_id}]",
                    f"{len(s)} snapshots {[k for k, _ in s]}; latest {last_kind} at "
                    f"{last_ra:%H:%M}Z = {_mins(last_ra, g.tip_utc):.0f} min before tip",
                    {
                        "snapshots": [k for k, _ in s],
                        "latest_minutes_before_tip": _mins(last_ra, g.tip_utc),
                    },
                )
            )
        else:
            out.append(Result(WARN, f"lines[{g.game_id}]", "no sportsbook snapshots stored", {}))
        rows = [(p, t, m) for gid, p, t, m in fc if gid == g.game_id]
        quoted = [(p, t, m) for p, t, m in rows if (g.game_id, p, t) in first_quote]
        # CAVEAT: an OK pairing here is NOT the T-60 scored pairing (FORWARD_PREREG rule 3) and must
        # not be cited for any market claim; it only shows a run followed the first quote.
        # A forecast is "as of" the last pretip run that confirmed it, not its stored first write:
        # the store writes on change only, so an unchanged forecast keeps its first made_at (12:00Z
        # on 2026-10-26) while lines exist only from about T-90 (morning_failures_2026-10-27).
        conf = confirmed.get(g.game_id)
        asof = {(p, t): (m if conf is None else max(m, conf)) for p, t, m in quoted}
        paired = [(p, t, m) for p, t, m in quoted if asof[(p, t)] >= first_quote[(g.game_id, p, t)]]
        paired_stored = sum(1 for p, t, m in quoted if m >= first_quote[(g.game_id, p, t)])
        any_quotes = any(k[0] == g.game_id for k in first_quote)
        if not quoted:
            out.append(
                Result(
                    FAIL if (any_quotes and rows) else WARN,
                    f"pairing[{g.game_id}]",
                    f"0 of {len(rows)} forecasts have a main-line quote"
                    + (
                        "; main-line quotes exist but none for forecast players"
                        if any_quotes
                        else ""
                    ),
                    {"forecasts": len(rows), "quoted": 0, "paired": 0},
                )
            )
            continue
        rate = len(paired) / len(quoted)
        out.append(
            Result(
                FAIL if not paired else (WARN if rate < PAIRING_WARN else OK),
                f"pairing[{g.game_id}]",
                f"{len(paired)}/{len(quoted)} = {rate:.0%} of quoted forecasts pair "
                f"(latest props run before tip >= earliest main-line received_at; "
                f"stored made_at alone: {paired_stored}); forecasts={len(rows)}"
                + (f"; latest props run {conf:%H:%M}Z" if conf is not None else "; no run stamps"),
                {
                    "forecasts": len(rows),
                    "quoted": len(quoted),
                    "paired": len(paired),
                    "paired_stored_made_at": paired_stored,
                    "rate": rate,
                },
            )
        )
    return out


def _et_day_start_utc(g: Game) -> datetime:
    """Naive UTC instant of 00:00 Eastern on the game's Eastern date."""
    d = g.tip_et.date()
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC).replace(tzinfo=None)


def last_confirmed_runs(
    nba: duckdb.DuckDBPyConnection, games: Sequence[Game]
) -> dict[str, datetime]:
    """Per game, the made_at of the latest pretip run of the production props model before the tip.

    Source: ``forward_model_stamps`` (one row per run and model, written at fit time whether or not
    the write-on-change store then wrote any row), field ``made_at`` = the run's made_at, the same
    clock the store uses. A run that found a forecast unchanged wrote nothing to
    ``forward_predictions`` but is its confirmation. The stamp is per run, not per game or player:
    only stamps from the game's own Eastern date (made_at >= UTC start of that ET day) and before
    tip count, so an earlier slate's run cannot confirm it. Games with no such stamp, or a DB
    without the table, are absent (the caller falls back to the stored made_at).
    """
    out: dict[str, datetime] = {}
    try:
        for g in games:
            row = nba.execute(
                "SELECT max(made_at) FROM forward_model_stamps"
                " WHERE model_name = ? AND made_at >= ? AND made_at < ?",
                [PROPS_MODEL, _et_day_start_utc(g), g.tip_utc],
            ).fetchone()
            if row and row[0] is not None:
                out[g.game_id] = row[0]
    except duckdb.Error:
        return {}
    return out


def latest_prod_forecasts_with_time(
    nba: duckdb.DuckDBPyConnection, gids: Sequence[str]
) -> list[tuple[str, int, str, datetime]]:
    return latest_prod_forecasts(nba, gids)


# ------------------------------------------------------------------ f. jobs


_STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)$")


def _stamped(text: str | None) -> list[tuple[datetime, str]]:
    out = []
    for ln in (text or "").splitlines():
        m = _STAMP.match(ln)
        if m:
            out.append((datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S"), m[2]))
    return out


def check_jobs(p: Paths, games: Sequence[Game], log_tz: ZoneInfo | None = None) -> list[Result]:
    """Pretip/lineups/app-export starts and pretip gaps. Log stamps are the Mac's local clock; the
    slate window is converted from UTC with ``log_tz`` (default: the system zone)."""
    out: list[Result] = []
    pre = _stamped(_read(p.log("pretip")))
    starts = [t for t, m in pre if m.startswith("start predict")]
    lineups = _stamped(_read(p.log("lineups")))
    runs = [t for t, m in lineups if m.startswith("run-t30")]
    exp = _stamped(_read(p.log("app_export")))
    exp_starts = [t for t, m in exp if m.startswith("start export")]
    exp_skips = [t for t, m in exp if m.startswith("skipped")]

    def local(u: datetime) -> datetime:
        z = u.replace(tzinfo=UTC)
        return (z.astimezone(log_tz) if log_tz else z.astimezone()).replace(tzinfo=None)

    gaps: list[tuple[datetime, datetime, float]] = []
    if games:
        w0 = local(min(g.tip_utc for g in games)) - timedelta(hours=4)
        w1 = local(max(g.tip_utc for g in games))
        inwin = [t for t in starts if w0 <= t <= w1]
        for a, b in zip(inwin, inwin[1:], strict=False):
            if _mins(a, b) > PRETIP_GAP_MIN:
                gaps.append((a, b, _mins(a, b)))
    skips = [t for t, m in pre if m.startswith("skipped")]
    out.append(
        Result(
            FAIL if gaps else (OK if starts else WARN),
            "jobs_pretip",
            f"{len(starts)} pretip starts"
            + (
                f" ({_ts(starts[0])} .. {_ts(starts[-1])} local)"
                if starts
                else " (no log or no start)"
            )
            + f"; lock-skips={len(skips)}"
            + (
                "; job did not run: "
                + ", ".join(f"{_ts(a)}->{_ts(b)} ({m:.0f} min)" for a, b, m in gaps)
                if gaps
                else ""
            ),
            {
                "starts": [t.isoformat() for t in starts],
                "gaps": [(a.isoformat(), b.isoformat(), m) for a, b, m in gaps],
            },
        )
    )
    out.append(
        Result(
            OK if runs else WARN,
            "jobs_lineups",
            f"{len(runs)} lineups ticks"
            + (f" ({_ts(runs[0])} .. {_ts(runs[-1])} local)" if runs else " (none logged)"),
            {"ticks": len(runs)},
        )
    )
    out.append(
        Result(
            OK if exp_starts else WARN,
            "jobs_app_export",
            f"{len(exp_starts)} export starts, {len(exp_skips)} skipped"
            + (
                f" (first {_ts(exp_starts[0])}, last {_ts(exp_starts[-1])} local)"
                if exp_starts
                else ""
            ),
            {"starts": len(exp_starts), "skipped": len(exp_skips)},
        )
    )
    alerts: list[str] = []
    if p.ops.is_dir():
        for f in sorted(p.ops.glob(f"*_{p.slate_date:%Y%m%d}.log")):
            alerts.extend(f"{f.name}: {ln}" for ln in _alert_lines(f))
    out.append(
        Result(
            WARN if alerts else OK,
            "alerts",
            f"{len(alerts)} ALERT lines"
            + "".join(f"\n    {a}" for a in alerts[:MAX_ALERTS_SHOWN])
            + (
                f"\n    ... {len(alerts) - MAX_ALERTS_SHOWN} more"
                if len(alerts) > MAX_ALERTS_SHOWN
                else ""
            ),
            {"alerts": alerts},
        )
    )
    return out


# ------------------------------------------------------------------ g. scoring


def check_scoring(nba: duckdb.DuckDBPyConnection, games: Sequence[Game]) -> list[Result]:
    early: list[tuple[str, str, datetime, int, float]] = []
    total = 0
    for g in games:
        row = nba.execute(
            "SELECT count(*) FROM forward_scores WHERE game_id = ?", [g.game_id]
        ).fetchone()
        total += int(row[0]) if row else 0
        for model, sa, n in nba.execute(
            """SELECT model_name, scored_at, count(*) FROM forward_scores
               WHERE game_id = ? AND scored_at < ? GROUP BY model_name, scored_at
               ORDER BY scored_at, model_name""",
            [g.game_id, g.tip_utc + SCORE_LAG],
        ).fetchall():
            early.append((g.game_id, model, sa, int(n), _mins(g.tip_utc, sa)))
    lines = "".join(
        f"\n    {gid} {m}: {n} rows scored_at {sa:%H:%M}Z = {mn:.0f} min after tip"
        for gid, m, sa, n, mn in early
    )
    return [
        Result(
            FAIL if early else OK,
            "scoring",
            f"{sum(e[3] for e in early)} of {total} forward_scores rows scored before tip+4h"
            + (" (scored before final)" if early else "")
            + lines,
            {"early": [(a, b, c.isoformat(), d, e) for a, b, c, d, e in early], "total": total},
        )
    ]


# ------------------------------------------------------------------ driver


def run(
    p: Paths,
    *,
    log_tz: ZoneInfo | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Result]:
    res: list[Result] = []
    games, err = load_slate(p)
    if err:
        return [Result(FAIL, "schedule", err)]
    if not games:
        return [Result(OK, "slate", f"no games on {p.slate_date} in the schedule cache")]
    pretip_text = _read(p.log("pretip"))

    def guarded(name: str, fn: Callable[[], list[Result]]) -> None:
        try:
            res.extend(fn())
        except Exception as e:
            res.append(Result(FAIL, name, f"could not compute: {type(e).__name__}: {e}"))

    nba = odds = None
    try:
        nba = connect_ro(p.nba_db, sleep=sleep)
    except Exception as e:
        res.append(Result(FAIL, "nba.duckdb", f"cannot open read-only: {type(e).__name__}: {e}"))
    try:
        odds = connect_ro(p.market_db, sleep=sleep)
    except Exception as e:
        res.append(Result(FAIL, "market_asof.duckdb", f"cannot open read-only: {e}"))
    rosters = read_rosters(p.rosters)
    if nba is not None:
        guarded("schedule", lambda: check_schedule(nba, games))
        guarded("roster", lambda: check_roster(nba, games, rosters, p.rosters))
        guarded("injury", lambda: check_injury(nba, games, pretip_text))
        if odds is not None:
            guarded("lines", lambda: check_lines(nba, odds, games))
        guarded("forecasts", lambda: check_forecasts(nba, games, pretip_text))
    guarded("jobs", lambda: check_jobs(p, games, log_tz))
    if nba is not None:
        guarded("scoring", lambda: check_scoring(nba, games))
    for c in (nba, odds):
        if c is not None:
            c.close()
    return res


def render(res: Sequence[Result], d: date, n_games: int | None = None) -> str:
    worst = max((r.status for r in res), key=lambda s: _RANK[s], default=OK)
    head = f"== coverage {d} ({worst}) =="
    return "\n".join([head, *(r.line() for r in res)])


def exit_code(res: Sequence[Result]) -> int:
    return EXIT_COVERAGE_FAIL if any(r.status == FAIL for r in res) else 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--date", required=True, help="ET slate date YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--root", default=str(ROOT), help="repo root holding nba.duckdb and data/")
    ap.add_argument("--log-tz", default=None, help="zone of the ops log stamps (default: system)")
    a = ap.parse_args(argv)
    d = date.fromisoformat(a.date)
    res = run(Paths.under(Path(a.root), d), log_tz=ZoneInfo(a.log_tz) if a.log_tz else None)
    if a.json:
        print(
            json.dumps(
                {
                    "date": a.date,
                    "exit": exit_code(res),
                    "checks": [
                        {"status": r.status, "check": r.check, "text": r.text, "data": r.data}
                        for r in res
                    ],
                },
                default=str,
                indent=1,
            )
        )
    else:
        print(render(res, d))
    return exit_code(res)


if __name__ == "__main__":
    sys.exit(main())
