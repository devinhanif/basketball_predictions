"""Real-data readers: forecasts from a COPY of nba.duckdb, snapshots from the write-once live
capture.

Read-only. No order, credential or network route. Names are matched by
``configs/kalshi_aliases.yaml``
first, then by exact normalized full name; unmatched markets are dropped and counted.
"""

from __future__ import annotations

import glob
import gzip
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import yaml

from research.line_move import spec
from research.line_move.selection import PSG, Forecast, Rung

NY = ZoneInfo("America/New_York")


def norm(s: str) -> str:
    return re.sub(
        r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    )


def _epoch(d: datetime) -> int:
    return int(d.replace(tzinfo=UTC).timestamp())


def _forecast(pred: object) -> Forecast:
    d = json.loads(pred) if isinstance(pred, str) else pred
    assert isinstance(d, dict)
    f = d.get("p_ge_full")
    counts = tuple(int(x) for x in f["c"]) if f else None
    n = int(f["n"]) if f else 0
    pp = d.get("p_play")
    return Forecast(counts, n, None if pp is None else float(pp))


def load_forecasts(
    db_copy: Path,
) -> dict[tuple[int, str, date], tuple[str, int, int, Forecast, Forecast | None]]:
    """(pid, stat, ET date) -> (game_id, tip_s, made_s, M, B).

    M = latest ``props_context_residual`` per (game_id, player_id, target) with
    ``made_at <= tipoff - 60 min``; B = the ``props_recency_v1`` row with the same run_id, game,
    player
    and target. ``tipoff`` and ``made_at`` are stored as naive UTC.
    """
    con = duckdb.connect(str(db_copy), read_only=True)
    try:
        rows = con.execute(
            "select run_id, game_id, tipoff, target, player_id, prediction, made_at, model_name "
            "from forward_predictions where model_name in (?, ?) and player_id is not null "
            "and target in ('pts','reb','ast','fg3m')",
            [spec.M_MODEL, spec.B_MODEL],
        ).fetchall()
    finally:
        con.close()
    bmap: dict[tuple[str, str, int, str], Forecast] = {}
    cand: dict[tuple[str, int, str], tuple[datetime, str, datetime, Forecast]] = {}
    for run_id, gid, tip, tgt, pid, pred, made, mn in rows:
        if mn == spec.B_MODEL:
            bmap[(run_id, gid, int(pid), tgt)] = _forecast(pred)
        elif made <= tip - timedelta(minutes=spec.FORECAST_LEAD_MIN):
            k = (gid, int(pid), tgt)
            if k not in cand or made > cand[k][0]:
                cand[k] = (made, run_id, tip, _forecast(pred))
    out = {}
    for (gid, pid, tgt), (made, run_id, tip, fm) in cand.items():
        et = tip.replace(tzinfo=UTC).astimezone(NY).date()
        out[(pid, tgt, et)] = (
            gid,
            _epoch(tip),
            _epoch(made),
            fm,
            bmap.get((run_id, gid, pid, tgt)),
        )
    return out


def load_name_map(pids: set[int], aliases_path: Path) -> dict[str, int]:
    from nba.explain.names import player_name

    aliases = {
        norm(k): int(v) for k, v in yaml.safe_load(aliases_path.read_text())["aliases"].items()
    }
    by_name = {norm(player_name(p, {})): p for p in pids}
    return {**by_name, **aliases}


def load_snapshots(
    capture_dirs: list[Path],
) -> dict[str, tuple[str, str, list[tuple[int, float, float]]]]:
    """ticker -> (series, title, [(t_s, bid, ask)]) from live_<date>/kalshi/<SERIES>_p0 files."""
    acc: dict[str, tuple[str, str, list[tuple[int, float, float]]]] = {}
    for d in capture_dirs:
        for series in spec.SERIES_STAT:
            for f in sorted(glob.glob(str(d / "kalshi" / f"{series}_p0" / "*.json.gz"))):
                t = _epoch(datetime.strptime(Path(f).name[:15], "%Y%m%dT%H%M%S"))
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    markets = json.load(fh).get("markets", [])
                for m in markets:
                    b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
                    row = (
                        t,
                        float(b) if b not in (None, "") else float("nan"),
                        float(a) if a not in (None, "") else float("nan"),
                    )
                    tk = m["ticker"]
                    if tk not in acc:
                        acc[tk] = (series, m.get("title", ""), [])
                    acc[tk][2].append(row)
    return acc


def load_file_times(capture_dirs: list[Path]) -> dict[str, np.ndarray]:
    """series -> sorted capture-file times (a snapshot is a capture file; from the file names)."""
    out: dict[str, np.ndarray] = {}
    for series in spec.SERIES_STAT:
        ts = {
            _epoch(datetime.strptime(Path(f).name[:15], "%Y%m%dT%H%M%S"))
            for d in capture_dirs
            for f in glob.glob(str(d / "kalshi" / f"{series}_p0" / "*.json.gz"))
        }
        out[series] = np.array(sorted(ts), dtype=np.int64)
    return out


def capture_file_count(capture_dirs: list[Path]) -> int:
    return sum(len(v) for v in load_file_times(capture_dirs).values())


def db_stamp(db_copy: Path) -> dict[str, object]:
    """Row count and latest made_at of ``forward_predictions`` in the DB copy (result record)."""
    con = duckdb.connect(str(db_copy), read_only=True)
    try:
        n, mx = con.execute(
            "select count(*), max(made_at) from forward_predictions"
        ).fetchone() or (0, None)
    finally:
        con.close()
    return {"forward_predictions_rows": int(n), "max_made_at": None if mx is None else str(mx)}


def rules_secondary_texts(capture_dirs: list[Path], tickers: set[str]) -> Counter[str]:
    """Distinct ``rules_secondary`` over the given tickers, the stat noun masked (T510 Data)."""
    out: Counter[str] = Counter()
    seen: set[str] = set()
    for d in capture_dirs:
        for f in sorted(glob.glob(str(d / "kalshi_rules" / "*" / "*.json.gz"))):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            for tk, rec in data.items():
                if tk in tickers and tk not in seen and isinstance(rec, dict):
                    seen.add(tk)
                    txt = str(rec.get("rules_secondary", ""))
                    out[re.sub(r"player's \S+ recorded", "player's <stat> recorded", txt)] += 1
    return out


def inactive_pids(
    db_copy: Path, pairs: set[tuple[str, int]]
) -> tuple[set[int], set[tuple[str, int]]]:
    """(players with a box row of minutes 0 or null, (game, player) pairs with NO box row).

    T510b report only; the games are final. A missing box row is an ingest gap, counted
    separately, and is not read as a scratch.
    """
    con = duckdb.connect(str(db_copy), read_only=True)
    try:
        zero: set[int] = set()
        no_row: set[tuple[str, int]] = set()
        for gid, pid in pairs:
            r = con.execute(
                "select minutes from player_game_stats where game_id = ? and player_id = ?",
                [gid, pid],
            ).fetchall()
            if not r:
                no_row.add((gid, pid))
            elif r[0][0] is None or r[0][0] == 0:
                zero.add(pid)
    finally:
        con.close()
    return zero, no_row


def schedule_last_et_date(path: Path) -> date:
    """Last ET calendar date of a scheduled tip in the schedule parquet."""
    import polars as pl

    df = pl.read_parquet(path)
    return max(
        datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(NY).date()
        for v in df["gameDateTimeUTC"].to_list()
    )


def build_psgs(
    snaps: dict[str, tuple[str, str, list[tuple[int, float, float]]]],
    forecasts: dict[tuple[int, str, date], tuple[str, int, int, Forecast, Forecast | None]],
    name_map: dict[str, int],
    dates: set[date],
    file_times: dict[str, np.ndarray] | None = None,
) -> tuple[list[PSG], Counter[str]]:
    drops: Counter[str] = Counter()
    stat_series = {v: k for k, v in spec.SERIES_STAT.items()}
    groups: dict[tuple[int, str, date], list[Rung]] = defaultdict(list)
    for tk, (series, title, rows) in snaps.items():
        m = re.match(r"(.+?): (\d+)\+", title)
        if not m:
            drops["title_unparsed"] += 1
            continue
        pid = name_map.get(norm(m.group(1)))
        stat = spec.SERIES_STAT[series]
        et = datetime.strptime(tk.split("-")[1][:7], "%y%b%d").date()
        if pid is None or (pid, stat, et) not in forecasts:
            drops["no_player_or_forecast_match"] += 1
            continue
        if et not in dates:
            continue
        rows.sort()
        arr = np.array(rows, dtype=float)
        groups[(pid, stat, et)].append(
            Rung(tk, int(m.group(2)), arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2])
        )
    psgs = []
    for (pid, stat, et), rungs in groups.items():
        gid, tip_s, made_s, fm, fb = forecasts[(pid, stat, et)]
        ft = None if file_times is None else file_times[stat_series[stat]]
        psgs.append(PSG(pid, stat, et, gid, tip_s, made_s, fm, fb, rungs, ft))
    psgs.sort(key=lambda p: (p.day, p.game_id, p.pid, p.stat))
    return psgs, drops
