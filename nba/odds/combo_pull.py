"""COMBO_LINE_CAL repair R1: the-odds-api.com historical MAIN-LINE pull of six combo / defensive
player markets (pra, pr, pa, ra, blocks, blocks+steals), raw files only.

Two stages, both GET-only on the vendor's public historical routes, key from ``THE_ODDS_API_KEY``
only (never printed, stored or logged; the stored params never contain it):

* ``probe``: 12 sampled games (one per sampled date, 6 dates per season, seeds and dates committed
  in ``configs/odds_combo_probe_sample.yaml`` BEFORE the pull; the code refuses a sample that does
  not recompute to the committed one). Hard cap 720 credits on its own ledger. The success bar
  (Devin, fixed before the probe): two-sided non-alternate pra, pr, pa and ra at DraftKings or
  FanDuel on >= 10 of the 12 dates. A date counts only if all four markets are quoted.
* ``full``: every game with a real tip in seasons 2023 and 2024, region ``us``, t60, six markets,
  own ledger, hard cap 160,000, under ``data/ops/heavy.lock``, resumable (a raw file on disk is the
  done marker; the probe's raw files are reused), manifest with counts, credits and sha. Refused
  unless the probe raw files, re-evaluated here, meet the bar. Season 2025 is refused in code.

Nothing here writes ``nba.duckdb`` (opened read-only for the schedule) or ``odds_history.duckdb``;
ingest is a separate, proposed step.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

from nba.odds.history_pull import load_games
from nba.odds.the_odds_api import (
    HOLDOUT_SEASON,
    CreditBudgetExceeded,
    CreditLedger,
    EventMatchError,
    GameRef,
    HistoricalClient,
    HistoryConfig,
    HistoryError,
    HoldoutRefused,
    RequestRejected,
    UnreachableError,
    default_resolver,
    load_config,
    load_raw,
    match_event,
    odds_path,
    parse_events,
    read_key,
    redact,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "odds_combo_pull.yaml"
SAMPLE_PATH = Path(__file__).resolve().parents[2] / "configs" / "odds_combo_probe_sample.yaml"
ET = ZoneInfo("America/New_York")
SHORT = {
    "player_points_rebounds_assists": "pra",
    "player_points_rebounds": "pr",
    "player_points_assists": "pa",
    "player_rebounds_assists": "ra",
    "player_blocks": "blk",
    "player_blocks_steals": "bs",
}


class BarNotMet(HistoryError):
    """The full pull was asked for but the probe's success bar is not met."""


class LockHeld(HistoryError):
    """``heavy.lock`` is held by another job; it is never broken."""


# ----------------------------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComboConfig:
    data_root: Path
    markets: tuple[str, ...]
    bar_markets: tuple[str, ...]
    bar_books: tuple[str, ...]
    regions: tuple[str, ...]
    snapshot: str
    seasons: tuple[int, ...]
    probe_seed: int
    probe_dates_per_season: int
    probe_season_start: Mapping[int, date]
    probe_max_credits: int
    bar_min_dates: int
    full_max_credits: int
    min_server_remaining: int
    cost_per_market_region: int

    def __post_init__(self) -> None:
        if HOLDOUT_SEASON in self.seasons:
            raise HoldoutRefused(f"seasons names the holdout season {HOLDOUT_SEASON}")

    @property
    def cost_per_event(self) -> int:
        return self.cost_per_market_region * len(self.markets) * len(self.regions)

    @property
    def old_raw_dir(self) -> Path:
        """The existing historical pull's raw cache: read only, used for cached events lists."""
        return self.data_root / "data" / "odds" / "raw" / "the_odds_api"

    @property
    def lock_dir(self) -> Path:
        return self.data_root / "data" / "ops" / "heavy.lock"

    def probe_dir(self, day: str) -> Path:
        return self.data_root / "data" / "raw" / f"odds_combo_probe_{day}"

    def pull_dir(self, day: str) -> Path:
        return self.data_root / "data" / "raw" / f"odds_combo_pull_{day}"


def load_combo_config(path: Path | None = None) -> ComboConfig:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text())
    start = {
        int(k): (v if isinstance(v, date) else date.fromisoformat(str(v)))
        for k, v in raw["probe_season_start"].items()
    }
    return ComboConfig(
        data_root=Path(raw["data_root"]),
        markets=tuple(raw["markets"]),
        bar_markets=tuple(raw["bar_markets"]),
        bar_books=tuple(raw["bar_books"]),
        regions=tuple(raw["regions"]),
        snapshot=str(raw["snapshot"]),
        seasons=tuple(int(s) for s in raw["seasons"]),
        probe_seed=int(raw["probe_seed"]),
        probe_dates_per_season=int(raw["probe_dates_per_season"]),
        probe_season_start=start,
        probe_max_credits=int(raw["probe_max_credits"]),
        bar_min_dates=int(raw["bar_min_dates"]),
        full_max_credits=int(raw["full_max_credits"]),
        min_server_remaining=int(raw["min_server_remaining"]),
        cost_per_market_region=int(raw["cost_per_market_region"]),
    )


def history_config(cc: ComboConfig, raw_dir: Path, ledger: Path, cap: int) -> HistoryConfig:
    """The existing client's config, pointed at this stage's own raw dir, ledger and cap."""
    base = load_config()
    return replace(
        base,
        regions=cc.regions,
        us_ex_from=None,
        raw_dir=raw_dir,
        ledger_path=ledger,
        max_credits=cap,
        min_server_remaining=cc.min_server_remaining,
        cost_per_market_region=cc.cost_per_market_region,
        games_db=cc.data_root / "nba.duckdb",
        schedule_dir=cc.data_root / "data" / "schedule",
        seasons=cc.seasons,
        alt_seasons=(2023, 2024),
    )


# ----------------------------------------------------------------------------------------------
# Sampling (deterministic; committed before the pull)
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeItem:
    day: date  # ET calendar date of the tip
    game: GameRef


def et_date(game: GameRef) -> date:
    return game.tipoff_utc.replace(tzinfo=UTC).astimezone(ET).date()


def events_cached(cc: ComboConfig, game: GameRef) -> bool:
    return (
        cc.old_raw_dir / str(game.season) / game.game_id / f"events_{cc.snapshot}_none_none.json.gz"
    ).exists()


def sample_probe(
    cc: ComboConfig, games_by_season: Mapping[int, Sequence[GameRef]]
) -> list[ProbeItem]:
    """One game on each of ``probe_dates_per_season`` dates per season.

    Eligible: regular season (``game_id`` starts ``002``), on/after the season's start date, with
    a cached events list (so the probe costs exactly 60 credits per game). The eligible dates are
    cut into equal-width bins by rank and one date is drawn per bin, then one game on that date.
    """
    out: list[ProbeItem] = []
    for season in cc.seasons:
        start = cc.probe_season_start[season]
        by_day: dict[date, list[GameRef]] = {}
        for g in games_by_season[season]:
            d = et_date(g)
            if g.game_id.startswith("002") and d >= start and events_cached(cc, g):
                by_day.setdefault(d, []).append(g)
        days = sorted(by_day)
        k = cc.probe_dates_per_season
        if len(days) < k:
            raise HistoryError(f"season {season}: only {len(days)} eligible dates for {k} draws")
        rng = random.Random(f"{cc.probe_seed}-{season}")
        for i in range(k):
            bin_days = days[i * len(days) // k : (i + 1) * len(days) // k]
            day = rng.choice(bin_days)
            out.append(ProbeItem(day, rng.choice(sorted(by_day[day], key=lambda g: g.game_id))))
    return out


def sample_record(items: Sequence[ProbeItem]) -> list[dict[str, Any]]:
    return [
        {"date": i.day.isoformat(), "season": i.game.season, "game_id": i.game.game_id}
        for i in items
    ]


# ----------------------------------------------------------------------------------------------
# Success bar
# ----------------------------------------------------------------------------------------------


def two_sided_lines(body: Any, market: str, book: str) -> tuple[int, int]:
    """``(n two-sided player lines, n one-sided)`` for the NON-alternate ``market`` at ``book``.

    A line is a (player, point) with both an Over and an Under price. The market key must equal
    ``market`` exactly, so an ``*_alternate`` key never counts as a main line.
    """
    data = body.get("data") if isinstance(body, dict) else None
    books = data.get("bookmakers") if isinstance(data, dict) else None
    sides: dict[tuple[str, float], set[str]] = {}
    for bk in books or []:
        if not isinstance(bk, dict) or bk.get("key") != book:
            continue
        for mk in bk.get("markets") or []:
            if not isinstance(mk, dict) or mk.get("key") != market:
                continue
            for oc in mk.get("outcomes") or []:
                if not isinstance(oc, dict) or oc.get("point") is None:
                    continue
                if oc.get("price") is None:
                    continue
                key = (str(oc.get("description") or ""), float(oc["point"]))
                sides.setdefault(key, set()).add(str(oc.get("name")))
    two = sum(1 for s in sides.values() if {"Over", "Under"} <= s)
    return two, len(sides) - two


@dataclass
class BarResult:
    table: list[dict[str, Any]]
    dates: list[dict[str, Any]]
    n_dates: int
    n_pass: int
    met: bool
    per_market: dict[str, int]
    lines: list[str] = field(default_factory=list)


def evaluate_bar(cc: ComboConfig, raw_dir: Path, items: Sequence[ProbeItem]) -> BarResult:
    """Re-evaluate the bar from the raw probe files alone (nothing is trusted from a flag)."""
    hcfg = history_config(cc, raw_dir, raw_dir / "unused_ledger.json", cc.probe_max_credits)
    table: list[dict[str, Any]] = []
    dates: list[dict[str, Any]] = []
    per_market = {m: 0 for m in cc.markets}
    for it in items:
        at = it.game.tipoff_utc  # path only needs the region/market hash; at is unused for us
        path = odds_path(hcfg, it.game.season, it.game.game_id, cc.snapshot, cc.markets, at)
        body = load_raw(path).get("body") if path.exists() else None
        have = {m: False for m in cc.markets}
        for m in cc.markets:
            for b in cc.bar_books:
                two, one = two_sided_lines(body, m, b) if body is not None else (0, 0)
                table.append(
                    {
                        "date": it.day.isoformat(),
                        "season": it.game.season,
                        "game_id": it.game.game_id,
                        "market": m,
                        "book": b,
                        "two_sided_lines": two,
                        "one_sided_lines": one,
                    }
                )
                have[m] = have[m] or two > 0
        ok = all(have[m] for m in cc.bar_markets)
        for m in cc.markets:
            per_market[m] += int(have[m])
        dates.append(
            {
                "date": it.day.isoformat(),
                "game_id": it.game.game_id,
                "raw_present": body is not None,
                "markets_quoted": sorted(SHORT[m] for m in cc.markets if have[m]),
                "bar_pass": ok,
            }
        )
    n_pass = sum(1 for d in dates if d["bar_pass"])
    res = BarResult(table, dates, len(dates), n_pass, n_pass >= cc.bar_min_dates, per_market)
    res.lines = bar_lines(cc, res)
    return res


def bar_lines(cc: ComboConfig, res: BarResult) -> list[str]:
    out = [
        "| date | game_id | market | book | two-sided lines | one-sided |",
        "|---|---|---|---|---|---|",
    ]
    out += [
        f"| {r['date']} | {r['game_id']} | {SHORT[r['market']]} | {r['book']} | "
        f"{r['two_sided_lines']} | {r['one_sided_lines']} |"
        for r in res.table
    ]
    out.append("")
    out += [
        f"date {d['date']} {d['game_id']}: raw_present={d['raw_present']} "
        f"quoted={','.join(d['markets_quoted']) or 'none'} bar_pass={d['bar_pass']}"
        for d in res.dates
    ]
    out.append(
        "dates with each market at DK or FD: "
        + ", ".join(f"{SHORT[m]}={n}/{res.n_dates}" for m, n in res.per_market.items())
    )
    out.append(
        f"BAR (all of pra,pr,pa,ra at DK or FD, two-sided, non-alternate): {res.n_pass}/"
        f"{res.n_dates} dates, need {cc.bar_min_dates} -> {'MET' if res.met else 'NOT MET'}"
    )
    return out


# ----------------------------------------------------------------------------------------------
# Server counters, lock, manifest
# ----------------------------------------------------------------------------------------------


def server_counters(
    cc: ComboConfig,
    environ: Mapping[str, str] | None = None,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, int | None]:
    """``x-requests-remaining`` / ``-used`` from the free ``/sports`` route (no quota charged)."""
    key = read_key(environ)
    get = http_get or httpx.get
    try:
        resp = get(
            load_config().base_url.rstrip("/") + "/sports", params={"apiKey": key.reveal()},
            timeout=30.0,
        )  # fmt: skip
    except httpx.TransportError as exc:
        raise UnreachableError(f"counter check failed: {type(exc).__name__}") from exc
    if int(resp.status_code) != 200:
        raise RequestRejected(int(resp.status_code), redact(str(resp.text)[:120], key.reveal()))
    h = {k.lower(): str(v) for k, v in resp.headers.items()}

    def num(name: str) -> int | None:
        v = h.get(name)
        return int(v) if v is not None and v.lstrip("-").isdigit() else None

    return {"remaining": num("x-requests-remaining"), "used": num("x-requests-used")}


class HeavyLock:
    """``mkdir`` lock with our pid inside; removed only if the pid file is ours; never broken."""

    def __init__(self, path: Path, purpose: str, wait_s: float = 14_400.0, retry_s: float = 120.0):
        self.path, self.purpose = path, purpose
        self.wait_s, self.retry_s = wait_s, retry_s
        self.mine = False

    def __enter__(self) -> HeavyLock:
        t0 = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.path.mkdir()
                break
            except FileExistsError:
                if time.monotonic() - t0 > self.wait_s:
                    raise LockHeld(
                        f"{self.path} held after {self.wait_s:.0f}s; not broken"
                    ) from None
                time.sleep(self.retry_s)
        (self.path / "pid").write_text(f"{os.getpid()}\n{self.purpose}\n")
        self.mine = True
        return self

    def __exit__(self, *exc: object) -> None:
        pid = self.path / "pid"
        if self.mine and pid.exists() and pid.read_text().split("\n")[0] == str(os.getpid()):
            pid.unlink()
            self.path.rmdir()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, check=False,
        )  # fmt: skip
        return r.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def build_manifest(
    raw_dir: Path, *, stage: str, extra: Mapping[str, Any], make_read_only: bool = True
) -> dict[str, Any]:
    """Counts, per-file sha256 and a tree sha over ``raw_dir``'s odds / events files."""
    files = sorted(p for p in raw_dir.rglob("*.json.gz") if p.is_file())
    entries: list[dict[str, Any]] = []
    per_season: dict[str, dict[str, int]] = {}
    for p in files:
        rel = str(p.relative_to(raw_dir))
        entries.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
        season = rel.split("/")[0]
        kind = p.name.split("_")[0]
        per_season.setdefault(season, {}).setdefault(kind, 0)
        per_season[season][kind] += 1
        if make_read_only:
            p.chmod(0o444)
    tree = hashlib.sha256(
        "".join(f"{e['sha256']}  {e['path']}\n" for e in entries).encode()
    ).hexdigest()
    return {
        "stage": stage,
        "built_at": datetime.now(UTC).isoformat(),
        "code_git_sha": git_sha(),
        "n_files": len(entries),
        "files_by_season_and_kind": per_season,
        "tree_sha256": tree,
        **extra,
        "files": entries,
    }


# ----------------------------------------------------------------------------------------------
# Pull engine
# ----------------------------------------------------------------------------------------------


@dataclass
class PullStats:
    attempted: int = 0
    fetched: int = 0
    from_cache: int = 0
    reused_probe: int = 0
    empty: int = 0
    match_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    credits: int = 0
    requests: int = 0
    stopped: str | None = None


def seed_events(cc: ComboConfig, raw_dir: Path, game: GameRef) -> bool:
    """Copy the cached t60 events list for ``game`` into this stage's raw dir (cost 0)."""
    src = (
        cc.old_raw_dir / str(game.season) / game.game_id / f"events_{cc.snapshot}_none_none.json.gz"
    )
    dst = raw_dir / str(game.season) / game.game_id / src.name
    if dst.exists():
        return True
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def pull_games(
    cc: ComboConfig,
    games: Sequence[GameRef],
    *,
    raw_dir: Path,
    ledger_path: Path,
    cap: int,
    reuse_from: Path | None = None,
    client: HistoricalClient | None = None,
    environ: Mapping[str, str] | None = None,
    log: Callable[[str], None] = print,
) -> PullStats:
    """Fetch the six markets for each game; a raw file on disk is the done marker (idempotent)."""
    for g in games:
        if g.season == HOLDOUT_SEASON or g.season not in cc.seasons:
            raise HoldoutRefused(f"season {g.season} is not requestable (seasons {cc.seasons})")
    hcfg = history_config(cc, raw_dir, ledger_path, cap)
    cli = client or HistoricalClient(hcfg, environ=environ)
    res = default_resolver()
    st = PullStats()
    expected = cc.cost_per_event
    for n, g in enumerate(games, 1):
        at = g.tipoff_utc.replace(tzinfo=None) - _t60()
        opath = odds_path(hcfg, g.season, g.game_id, cc.snapshot, cc.markets, at)
        st.attempted += 1
        if opath.exists():
            st.from_cache += 1
            continue
        if reuse_from is not None:
            old = odds_path(
                replace(hcfg, raw_dir=reuse_from), g.season, g.game_id, cc.snapshot, cc.markets, at
            )
            if old.exists():
                opath.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, opath)
                st.reused_probe += 1
                continue
        seed_events(cc, raw_dir, g)
        try:
            ev = cli.events(season=g.season, game_id=g.game_id, snapshot_kind=cc.snapshot, at=at)
            event = match_event(parse_events(ev.body), g, res, hcfg.event_match_window_h)
            od = cli.event_odds(
                season=g.season, game_id=g.game_id, snapshot_kind=cc.snapshot, at=at,
                event_id=event.event_id, markets=cc.markets,
            )  # fmt: skip
        except EventMatchError as exc:
            st.match_failures.append(f"{g.game_id}: {exc}")
            continue
        except (RequestRejected, UnreachableError) as exc:
            st.errors.append(f"{g.game_id}: {exc}")
            continue
        except CreditBudgetExceeded as exc:
            st.stopped = f"credit guard: {exc}"
            break
        st.fetched += 1
        if od.cost > expected:
            st.stopped = f"call cost {od.cost} exceeded the expected {expected}; stopped"
            break
        data = od.body.get("data") if isinstance(od.body, dict) else None
        if not (isinstance(data, dict) and data.get("bookmakers")):
            st.empty += 1
        if n % 100 == 0:
            log(f"progress {n}/{len(games)} credits this run {cli.credits_spent}")
    st.credits = cli.credits_spent
    st.requests = cli.requests_made
    return st


def _t60() -> Any:
    from datetime import timedelta

    return timedelta(minutes=60)


def load_season_games(cc: ComboConfig, seasons: Sequence[int]) -> dict[int, list[GameRef]]:
    hcfg = history_config(cc, cc.data_root / "data" / "raw", Path("unused.json"), 0)
    out: dict[int, list[GameRef]] = {}
    for s in seasons:
        if s == HOLDOUT_SEASON or s not in cc.seasons:
            raise HoldoutRefused(f"season {s} is not requestable")
        out[s], _ = load_games(hcfg, s)
    return out


# ----------------------------------------------------------------------------------------------
# Stages
# ----------------------------------------------------------------------------------------------


def committed_sample(path: Path | None = None) -> list[dict[str, Any]] | None:
    p = path or SAMPLE_PATH
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text())
    return list(data["games"]) if isinstance(data, dict) else None


def run_probe(
    cc: ComboConfig,
    day: str,
    *,
    sample_only: bool = False,
    games_by_season: Mapping[int, Sequence[GameRef]] | None = None,
    environ: Mapping[str, str] | None = None,
    client: HistoricalClient | None = None,
    http_get: Callable[..., Any] | None = None,
    sample_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[str], BarResult | None]:
    gbs = games_by_season or load_season_games(cc, cc.seasons)
    items = sample_probe(cc, gbs)
    rec = sample_record(items)
    lines = [f"probe sample (seed {cc.probe_seed}):"]
    lines += [f"  {r['date']} season={r['season']} game_id={r['game_id']}" for r in rec]
    committed = committed_sample(sample_path)
    if committed is None:
        lines.append("no committed sample file; refusing to pull (commit the sample first)")
        return lines, None
    if committed != rec:
        lines.append("RECOMPUTED SAMPLE DIFFERS FROM THE COMMITTED ONE; refusing to pull")
        return lines, None
    lines.append("sample equals the committed sample")
    if sample_only:
        return lines, None
    raw_dir = cc.probe_dir(day)
    ledger = raw_dir / "ledger.json"
    before = server_counters(cc, environ, http_get)
    lines.append(f"server counters BEFORE: {before}")
    est = cc.cost_per_event * len(items)
    if est > cc.probe_max_credits:
        lines.append(f"estimate {est} exceeds the probe cap {cc.probe_max_credits}; refusing")
        return lines, None
    st = pull_games(
        cc, [i.game for i in items], raw_dir=raw_dir, ledger_path=ledger,
        cap=cc.probe_max_credits, client=client, environ=environ, log=log,
    )  # fmt: skip
    after = server_counters(cc, environ, http_get)
    lines.append(f"server counters AFTER: {after}")
    spent = CreditLedger(ledger, cc.probe_max_credits, cc.min_server_remaining).spent()
    delta = (
        None if before["used"] is None or after["used"] is None else after["used"] - before["used"]
    )
    lines.append(
        f"probe credits (ledger) {spent} of cap {cc.probe_max_credits}; server 'used' delta "
        f"{delta}; fetched={st.fetched} cached={st.from_cache} empty={st.empty} "
        f"match_failures={len(st.match_failures)} errors={len(st.errors)} stopped={st.stopped}"
    )
    bar = evaluate_bar(cc, raw_dir, items)
    lines += bar.lines
    manifest = build_manifest(
        raw_dir, stage="probe",
        extra={
            "sample": rec, "credits_ledger": spent, "cap": cc.probe_max_credits,
            "server_before": before, "server_after": after, "bar_met": bar.met,
            "bar_pass_dates": bar.n_pass, "bar_dates": bar.n_dates,
            "markets": list(cc.markets), "regions": list(cc.regions),
        },
    )  # fmt: skip
    (raw_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    (raw_dir / "BAR.json").write_text(
        json.dumps({"table": bar.table, "dates": bar.dates, "met": bar.met}, indent=1)
    )
    lines.append(
        f"probe manifest tree sha256 {manifest['tree_sha256']} ({manifest['n_files']} files)"
    )
    return lines, bar


def run_full(
    cc: ComboConfig,
    day: str,
    probe_day: str,
    *,
    games_by_season: Mapping[int, Sequence[GameRef]] | None = None,
    environ: Mapping[str, str] | None = None,
    client: HistoricalClient | None = None,
    http_get: Callable[..., Any] | None = None,
    sample_path: Path | None = None,
    lock: bool = True,
    log: Callable[[str], None] = print,
) -> list[str]:
    gbs = games_by_season or load_season_games(cc, cc.seasons)
    committed = committed_sample(sample_path)
    if committed is None:
        raise BarNotMet("no committed probe sample")
    by_id = {g.game_id: g for gl in gbs.values() for g in gl}
    items = [ProbeItem(date.fromisoformat(r["date"]), by_id[r["game_id"]]) for r in committed]
    probe_dir = cc.probe_dir(probe_day)
    bar = evaluate_bar(cc, probe_dir, items)
    lines = list(bar.lines[-3:])
    if not bar.met:
        raise BarNotMet("probe bar not met; the full pull is not run")
    games = [g for s in cc.seasons for g in gbs[s]]
    raw_dir = cc.pull_dir(day)
    ledger = raw_dir / "ledger.json"

    def go() -> list[str]:
        out = list(lines)
        before = server_counters(cc, environ, http_get)
        out.append(f"server counters BEFORE: {before}")
        out.append(
            f"games {len(games)} x {cc.cost_per_event} credits = "
            f"{len(games) * cc.cost_per_event:,} upper bound; cap {cc.full_max_credits:,}"
        )
        st = pull_games(
            cc, games, raw_dir=raw_dir, ledger_path=ledger, cap=cc.full_max_credits,
            reuse_from=probe_dir, client=client, environ=environ, log=log,
        )  # fmt: skip
        after = server_counters(cc, environ, http_get)
        spent = CreditLedger(ledger, cc.full_max_credits, cc.min_server_remaining).spent()
        out.append(f"server counters AFTER: {after}")
        out.append(
            f"full pull: credits (ledger) {spent:,} of cap {cc.full_max_credits:,}; "
            f"games={st.attempted} fetched={st.fetched} cached={st.from_cache} "
            f"reused_probe={st.reused_probe} empty={st.empty} "
            f"match_failures={len(st.match_failures)} errors={len(st.errors)} stopped={st.stopped}"
        )
        manifest = build_manifest(
            raw_dir, stage="full",
            extra={
                "credits_ledger": spent, "cap": cc.full_max_credits, "server_before": before,
                "server_after": after, "games_planned": len(games),
                "stats": {
                    "fetched": st.fetched, "from_cache": st.from_cache,
                    "reused_probe": st.reused_probe, "empty": st.empty,
                    "match_failures": st.match_failures, "errors": st.errors,
                    "stopped": st.stopped,
                },
                "markets": list(cc.markets), "regions": list(cc.regions),
                "probe_day": probe_day,
            },
        )  # fmt: skip
        (raw_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
        out.append(
            f"manifest {raw_dir / 'MANIFEST.json'} tree sha256 {manifest['tree_sha256']} "
            f"({manifest['n_files']} files)"
        )
        return out

    if not lock:
        return go()
    with HeavyLock(cc.lock_dir, "combo odds full pull (network only)"):
        return go()
