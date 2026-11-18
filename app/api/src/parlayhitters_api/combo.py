"""Combo mode: serves the stored night file `combo_<YYYY-MM-DD>.json` (contract
docs/contracts/combo_export_v1.md, schema `combo_export_v1`). Pure probability: the file carries no
price, fee, stake or expected value, and a file that does is refused, never passed through.

Read-only and unchanged: the file is validated, then served as stored (unknown keys kept; the
contract evolves additively). Where it is read from: `APP_EXPORT_DIR` in export mode (the file sits
beside the manifest; it is not one of the manifest's checksummed files), the committed fixtures
(`app/fixtures/combo`) in fixture mode. An absent file is a typed 404, never a sample in its place.
Nothing is computed here: the browser does the math (web/src/combo/engine.ts).
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .params import contained, is_date

Json = dict[str, Any]
SCHEMA = "combo_export_v1"
MAX_BYTES = 2_000_000  # the contract budgets 300 KB raw per night; this is a sanity cap
STATS = ("pts", "reb", "ast", "fg3m")
SCRIPTS = ("blowout", "close", "shootout", "grind")

# A key is refused when any of its words (split on non-letters) is one of these: the payload is pure
# probability, so nothing priced may ride along.
_BANNED_WORDS = frozenset(
    "price prices priced odds fee fees stake stakes ev edge kelly vig vigorish ask bid payout "
    "payouts wager wagers juice cost bankroll".split()
)
_WORD = re.compile(r"[A-Za-z]+")


class ComboMissing(Exception):
    """No file for that night."""


class ComboRefused(Exception):
    """A file exists but is not a combo_export_v1 payload this app can vouch for."""


class ComboUnavailable(Exception):
    """The configured directory cannot be read, so absence of a file would be misleading."""


def _banned_key(tree: Any, path: str = "") -> str | None:
    """The first key anywhere in the payload that names a price, fee, stake or EV; else None."""
    if isinstance(tree, dict):
        for k, v in tree.items():
            if any(w.lower() in _BANNED_WORDS for w in _WORD.findall(str(k))):
                return f"{path}/{k}"
            hit = _banned_key(v, f"{path}/{k}")
            if hit:
                return hit
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            hit = _banned_key(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def _num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _utc(x: Any) -> datetime | None:
    if not isinstance(x, str):
        return None
    try:
        d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _check_stat(e: Any, where: str) -> None:
    if not isinstance(e, dict):
        raise ComboRefused(f"{where}: not an object")
    k0, surv, cal = e.get("k0"), e.get("surv"), e.get("cal_k")
    if not isinstance(k0, int) or isinstance(k0, bool) or k0 < 0:
        raise ComboRefused(f"{where}: k0 is not a whole number")
    if not isinstance(surv, list) or not all(_num(v) and 0 <= v <= 1 for v in surv):
        raise ComboRefused(f"{where}: surv is not a list of chances")
    if any(b > a + 1e-9 for a, b in zip(surv, surv[1:], strict=False)):
        raise ComboRefused(f"{where}: surv is not nonincreasing")
    if not (
        isinstance(cal, list) and len(cal) == 2 and all(_int(v) for v in cal) and cal[0] <= cal[1]
    ):
        raise ComboRefused(f"{where}: cal_k is not [lo, hi]")
    ml = e.get("main_line")
    if ml is not None and not (_num(ml) and abs(ml - math.floor(ml) - 0.5) < 1e-9):
        raise ComboRefused(f"{where}: main_line is not an x.5 number")
    mo = e.get("main_line_open")
    if mo is not None and not _num(mo):
        raise ComboRefused(f"{where}: main_line_open is not a number")


def _check_game(g: Any, i: int) -> None:
    where = f"games[{i}]"
    if not isinstance(g, dict):
        raise ComboRefused(f"{where}: not an object")
    for k in ("game_id", "date", "tip_utc", "made_at"):
        if not isinstance(g.get(k), str):
            raise ComboRefused(f"{where}: {k} missing")
    for k in ("home_team", "away_team"):
        if not _int(g.get(k)):
            raise ComboRefused(f"{where}: {k} is not a team id")
    tip, made = _utc(g["tip_utc"]), _utc(g["made_at"])
    if tip is None or made is None:
        raise ComboRefused(f"{where}: tip_utc or made_at is not a UTC time")
    if made >= tip:
        raise ComboRefused(f"{where}: made at or after tip")
    gm = g.get("game")
    if not isinstance(gm, dict) or not all(
        _num(gm.get(k)) for k in ("mu_m", "sd_m", "mu_t", "sd_t")
    ):
        raise ComboRefused(f"{where}: game block is incomplete")
    if gm["sd_m"] <= 0 or gm["sd_t"] <= 0:
        raise ComboRefused(f"{where}: a spread is not positive")
    players = g.get("players")
    if not isinstance(players, list):
        raise ComboRefused(f"{where}: players missing")
    for j, p in enumerate(players):
        pw = f"{where}.players[{j}]"
        if (
            not isinstance(p, dict)
            or not _int(p.get("player_id"))
            or not isinstance(p.get("name"), str)
        ):
            raise ComboRefused(f"{pw}: bad player")
        if p.get("team") not in (g["home_team"], g["away_team"]):
            raise ComboRefused(f"{pw}: team is not in the game")
        pp = p.get("p_play")
        if pp is not None and not (_num(pp) and 0 <= pp <= 1):
            raise ComboRefused(f"{pw}: p_play is not a chance or null")
        if p.get("starter") not in (0, 1):
            raise ComboRefused(f"{pw}: starter is not 0 or 1")
        stats = p.get("stats")
        if not isinstance(stats, dict):
            raise ComboRefused(f"{pw}: stats missing")
        for s, e in stats.items():
            if s not in STATS:
                raise ComboRefused(f"{pw}: unknown stat {s!r}")
            _check_stat(e, f"{pw}.{s}")


def _check_shared(sh: Any) -> None:
    if not isinstance(sh, dict):
        raise ComboRefused("shared missing")
    corr = sh.get("corr")
    if not isinstance(corr, list) or not all(
        isinstance(r, list) and len(r) == 5 and _num(r[3]) and abs(r[3]) < 1 for r in corr
    ):
        raise ComboRefused("shared.corr is not [relation, a, b, rho, n] rows")
    sc = sh.get("scripts")
    if not isinstance(sc, dict):
        raise ComboRefused("shared.scripts missing")
    d = sc.get("definitions")
    if not isinstance(d, dict) or not all(
        _num(d.get(k)) for k in ("blowout_abs_margin", "close_abs_margin", "total_delta")
    ):
        raise ComboRefused("shared.scripts.definitions is incomplete")
    params = sc.get("params")
    if not isinstance(params, list) or not all(
        isinstance(r, list)
        and len(r) == 6
        and r[0] in SCRIPTS
        and _num(r[3])
        and _num(r[4])
        and r[4] > 0
        for r in params
    ):
        raise ComboRefused("shared.scripts.params is not [script, stat, role, mu, sigma, n] rows")
    if not isinstance(sc.get("status"), dict):
        raise ComboRefused("shared.scripts.status missing")


def validate(raw: Any, date: str) -> Json:
    """The payload itself when it is a combo_export_v1 for `date`; else ComboRefused."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        got = raw.get("schema") if isinstance(raw, dict) else None
        raise ComboRefused(f"unknown schema {got!r}; this app reads {SCHEMA!r} only")
    hit = _banned_key(raw)
    if hit:
        raise ComboRefused(
            f"key {hit} names a price, fee, stake or expected value; this payload carries none"
        )
    if raw.get("night") != date:
        raise ComboRefused(f"file is for night {raw.get('night')!r}, asked for {date}")
    _check_shared(raw.get("shared"))
    games = raw.get("games")
    if not isinstance(games, list):
        raise ComboRefused("games missing")
    seen: set[str] = set()
    for i, g in enumerate(games):
        _check_game(g, i)
        if g["game_id"] in seen:
            raise ComboRefused(f"games[{i}]: duplicate game_id")
        seen.add(g["game_id"])
        if g["date"] != date:
            raise ComboRefused(f"games[{i}]: date {g['date']!r} is not {date}")
    return raw


_FILE = re.compile(r"combo_([0-9]{4}-[0-9]{2}-[0-9]{2})\.json")


def list_nights(base: Path) -> list[str]:
    """The nights that have a combo file in `base`, oldest first (names only; nothing is opened).
    A directory that cannot be read is ComboUnavailable, not an empty list."""
    if not base.is_dir():
        raise ComboUnavailable("the directory holding the combo files is not readable")
    try:
        names = [p.name for p in base.iterdir() if p.is_file()]
    except OSError as e:
        raise ComboUnavailable("the directory holding the combo files is not readable") from e
    dates = [m.group(1) for n in names if (m := _FILE.fullmatch(n)) and is_date(m.group(1))]
    return sorted(dates)


def load_combo(date: str, base: Path) -> Json:
    """The validated payload for `date` from `base`. Raises ComboMissing / Refused / Unavailable."""
    if not is_date(date):
        raise ComboMissing(date)
    if not base.is_dir():
        raise ComboUnavailable("the directory holding the combo files is not readable")
    path = base / f"combo_{date}.json"
    if not path.is_file() or not contained(base, path):
        raise ComboMissing(date)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ComboRefused(f"file is over {MAX_BYTES} bytes")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ComboRefused(f"unreadable JSON: {e}") from e
    return validate(raw, date)


def clock_for(payload: Json, clock: str | None) -> str:
    """The clock the builder's tip rule uses. The store's clock when it has one (export mode: the
    real time; fixture replay on a stored slate: the slate's frozen clock, 30 minutes before its
    first tip), else 30 minutes before this file's own first tip."""
    if clock:
        return clock
    tips = [t for g in payload["games"] if (t := _utc(g["tip_utc"])) is not None]
    first = min(tips) if tips else datetime.now(UTC)
    return (first - timedelta(minutes=30)).astimezone(UTC).isoformat(timespec="seconds")
