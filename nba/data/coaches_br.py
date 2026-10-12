"""Basketball-Reference head coaches by team-season: polite fetch, cache, parse, reference CSV.

Why not a package: ``basketball_reference_web_scraper`` and ``basketball-reference-scraper``
expose players, schedules and standings but not the per-team-season "Coach: A (W-L), B (W-L)"
line, so the team pages (``/teams/<ABBR>/<YEAR>.html``, YEAR = season end year) are fetched with
``httpx`` (already a dependency) and parsed with the standard library. No dependency is added.

Politeness: at most one request per 3.5 s (BR blocks above 20/min), a descriptive User-Agent, a
page is cached write-once under ``data/coaches/raw/`` and never refetched, a non-200 or a page
without a coach line is never cached, and a 429 stops the run (do not hammer). Resumable: rerun
and cached pages are skipped.

Run (120 pages, about 7 minutes; prints one line per page, so progress is visible):

    uv run python -m nba.data.coaches_br fetch       # network, writes data/coaches/raw/*.html
    uv run python -m nba.data.coaches_br build-csv   # offline, writes the committed reference CSV

``build-csv`` fails loudly when a page is missing or does not parse; the sum-to-games gate runs in
``python -m nba.data.coaches`` against ``games``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "coaches" / "raw"
REFERENCE_CSV = Path(__file__).with_name("head_coach_games_reference.csv")
SEASONS = (2022, 2023, 2024, 2025)  # DB season s = BR page year s + 1
MIN_INTERVAL_S = 3.5
USER_AGENT = "nba-prediction-research/0.1 (personal non-commercial research; 1 request per 3.5 s)"
URL = "https://www.basketball-reference.com/teams/{br}/{year}.html"

# NBA team id -> (project abbreviation, Basketball-Reference abbreviation)
TEAMS: dict[int, tuple[str, str]] = {
    1610612737: ("ATL", "ATL"), 1610612738: ("BOS", "BOS"), 1610612739: ("CLE", "CLE"),
    1610612740: ("NOP", "NOP"), 1610612741: ("CHI", "CHI"), 1610612742: ("DAL", "DAL"),
    1610612743: ("DEN", "DEN"), 1610612744: ("GSW", "GSW"), 1610612745: ("HOU", "HOU"),
    1610612746: ("LAC", "LAC"), 1610612747: ("LAL", "LAL"), 1610612748: ("MIA", "MIA"),
    1610612749: ("MIL", "MIL"), 1610612750: ("MIN", "MIN"), 1610612751: ("BKN", "BRK"),
    1610612752: ("NYK", "NYK"), 1610612753: ("ORL", "ORL"), 1610612754: ("IND", "IND"),
    1610612755: ("PHI", "PHI"), 1610612756: ("PHX", "PHO"), 1610612757: ("POR", "POR"),
    1610612758: ("SAC", "SAC"), 1610612759: ("SAS", "SAS"), 1610612760: ("OKC", "OKC"),
    1610612761: ("TOR", "TOR"), 1610612762: ("UTA", "UTA"), 1610612763: ("MEM", "MEM"),
    1610612764: ("WAS", "WAS"), 1610612765: ("DET", "DET"), 1610612766: ("CHA", "CHO"),
}  # fmt: skip

CSV_COLUMNS = [
    "season", "team_id", "team_abbr", "seq", "coach_name", "first_game", "last_game",
    "games", "wins", "losses", "source_url", "fetched_date",
]  # fmt: skip

_PARA = re.compile(r"<strong>\s*Coach(?:es)?:?\s*</strong>(.*?)</p>", re.S | re.I)
_ENTRY = re.compile(r"<a[^>]*>([^<]+)</a>\s*\(\s*(\d+)\s*-\s*(\d+)\s*\)", re.S)


def page_path(raw_dir: Path, br_abbr: str, year: int) -> Path:
    return raw_dir / f"{br_abbr}_{year}.html"


def parse_coaches(page: str) -> list[tuple[str, int, int]]:
    """Return ``[(coach name, wins, losses)]`` in the order the page lists them (tenure order).

    Raises ``ValueError`` when the coach line is absent or has no parsable entry; a silent empty
    result would become an empty team-season downstream.
    """
    m = _PARA.search(page)
    if m is None:
        raise ValueError("no 'Coach:' line in page")
    out = [
        (" ".join(html.unescape(n).split()), int(w), int(lo))
        for n, w, lo in _ENTRY.findall(m.group(1))
    ]
    if not out:
        raise ValueError("'Coach:' line has no 'Name (W-L)' entry")
    return out


def _sleep_until(last: float, interval: float) -> None:
    wait = interval - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)


def fetch_all(
    raw_dir: Path = RAW_DIR,
    seasons: tuple[int, ...] = SEASONS,
    *,
    interval_s: float = MIN_INTERVAL_S,
    client: object | None = None,
) -> dict[str, int]:
    """Fetch every missing team-season page. Returns counts {cached, fetched, failed}."""
    import httpx

    raw_dir.mkdir(parents=True, exist_ok=True)
    own = client is None
    http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) if own else client
    counts = {"cached": 0, "fetched": 0, "failed": 0}
    last = 0.0
    log_path = raw_dir / "fetch_log.csv"
    new_log = not log_path.exists()
    try:
        for season in seasons:
            year = season + 1
            for _, br in TEAMS.values():
                path = page_path(raw_dir, br, year)
                if path.exists():
                    counts["cached"] += 1
                    continue
                _sleep_until(last, interval_s)
                url = URL.format(br=br, year=year)
                last = time.monotonic()
                resp = http.get(url)  # type: ignore[attr-defined]
                if resp.status_code == 429:
                    print(
                        f"429 from {url}: stopping, rerun later (cached pages are kept)", flush=True
                    )
                    counts["failed"] += 1
                    return counts
                try:
                    if resp.status_code != 200:
                        raise ValueError(f"HTTP {resp.status_code}")
                    parse_coaches(resp.text)  # never cache a page without a coach line
                except ValueError as exc:
                    counts["failed"] += 1
                    print(f"FAIL {url}: {exc} (not cached)", flush=True)
                    continue
                path.write_bytes(resp.content)  # write-once: existence checked above
                with log_path.open("a", newline="") as fh:
                    w = csv.writer(fh)
                    if new_log:
                        w.writerow(["file", "url", "fetched_utc", "bytes", "sha256"])
                        new_log = False
                    w.writerow(
                        [path.name, url, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         len(resp.content), hashlib.sha256(resp.content).hexdigest()]
                    )  # fmt: skip
                counts["fetched"] += 1
                print(f"ok {path.name}", flush=True)
    finally:
        if own:
            http.close()  # type: ignore[attr-defined]
    return counts


def _fetched_dates(raw_dir: Path) -> dict[str, str]:
    p = raw_dir / "fetch_log.csv"
    if not p.exists():
        return {}
    with p.open(newline="") as fh:
        return {r["file"]: r["fetched_utc"][:10] for r in csv.DictReader(fh)}


def build_reference_rows(
    raw_dir: Path = RAW_DIR, seasons: tuple[int, ...] = SEASONS
) -> list[dict[str, object]]:
    """Parse every cached page into reference rows with cumulative game numbers."""
    dates = _fetched_dates(raw_dir)
    rows: list[dict[str, object]] = []
    for season in seasons:
        year = season + 1
        for team_id, (abbr, br) in TEAMS.items():
            path = page_path(raw_dir, br, year)
            if not path.exists():
                raise FileNotFoundError(f"{path} missing: run the fetch first")
            coaches = parse_coaches(path.read_text(encoding="utf-8", errors="replace"))
            cum = 0
            for seq, (name, w, lo) in enumerate(coaches):
                rows.append(
                    {"season": season, "team_id": team_id, "team_abbr": abbr, "seq": seq,
                     "coach_name": name, "first_game": cum + 1, "last_game": cum + w + lo,
                     "games": w + lo, "wins": w, "losses": lo,
                     "source_url": URL.format(br=br, year=year),
                     "fetched_date": dates.get(path.name, "")}
                )  # fmt: skip
                cum += w + lo
    return rows


def write_reference_csv(rows: list[dict[str, object]], out: Path = REFERENCE_CSV) -> None:
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.data.coaches_br")
    ap.add_argument("cmd", choices=["fetch", "build-csv"])
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        counts = fetch_all()
        print(counts, flush=True)
        return 1 if counts["failed"] else 0
    rows = build_reference_rows()
    write_reference_csv(rows)
    print(f"wrote {REFERENCE_CSV} ({len(rows)} coach rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
