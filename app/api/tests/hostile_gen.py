"""A small, seeded generator of hostile path and query values (no new dependency).

Every route template below names its parameters; ``cases`` yields one request per (route,
parameter, bad value) with every other parameter left valid, so a refusal can be checked for
naming the parameter that was bad. The draw is deterministic for a given seed.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote

from parlayhitters_api.features import betting_enabled

#: Valid values for the committed fixtures (fixture_replay store, live fixture capture).
VALID: dict[str, str] = {
    "date": "2024-11-12",
    "live_date": "2026-11-09",
    "game_id": "0022400001",
    "event_id": "401909952",
    "player_id": "1628369",
    "stat": "pts",
}

#: (template, path parameters, query parameters with a valid value). ``event_id`` and
#: ``live_date`` are the live routes' own spellings of game_id and date.
ROUTES: list[tuple[str, tuple[str, ...], dict[str, str]]] = [
    ("/api/health", (), {}),
    ("/api/meta", (), {}),
    ("/api/slates", (), {}),
    ("/api/slates/{date}", ("date",), {}),
    ("/api/slates/{date}/swipe", ("date",), {"seed": "0", "n": "24"}),
    ("/api/slates/{date}/results", ("date",), {}),
    ("/api/games/{game_id}", ("game_id",), {}),
    ("/api/games/{game_id}/scenarios", ("game_id",), {}),
    ("/api/games/{game_id}/result", ("game_id",), {}),
    ("/api/props/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {"line": "20.5"}),
    ("/api/card/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/knows/{game_id}", ("game_id",), {}),
    ("/api/knows/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/track-record/calibration", (), {}),
    ("/api/track-record/guess", (), {"seed": "1", "n": "12"}),
    ("/api/track-record/guess/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    (
        "/api/breakdown/{game_id}/{player_id}/{stat}",
        ("game_id", "player_id", "stat"),
        {"line": "20.5", "side": "over"},
    ),
    ("/api/parlay-of-the-day", (), {}),
    ("/api/edges/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/contracts/top", (), {"n": "12"}),
    ("/api/desk", (), {}),
    ("/api/top-parlays/{date}", ("date",), {}),
    ("/api/combo", (), {}),
    ("/api/combo/{date}", ("date",), {}),
    ("/api/evidence", (), {}),
    ("/api/live/{live_date}", ("live_date",), {}),
    ("/api/live/{event_id}/snapshot", ("event_id",), {}),
    ("/api/live/{event_id}/plays", ("event_id",), {"before": "1000", "limit": "50"}),
    ("/api/live/{event_id}/checkpoint", ("event_id",), {}),
    ("/api/live/{event_id}/card", ("event_id",), {}),
    ("/api/live/{live_date}/cards", ("live_date",), {}),
    # the honesty routes (2026-11-17)
    ("/api/record/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/record/{game_id}", ("game_id",), {}),
    ("/api/distribution/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/scorecard", (), {"target": "props", "stat": "pts"}),
    ("/api/forward-tests", (), {}),
    ("/api/parlay-diagnostics/{date}", ("date",), {"offset": "0", "limit": "100"}),
    ("/api/errors", (), {}),
]

#: The betting module's routes. Served only with the betting switch on, so they join ROUTES (and
#: every test that walks it) only in a run with the switch on; tests/test_betting_flag.py fuzzes
#: them in both states explicitly.
BETTING_ROUTES: list[tuple[str, tuple[str, ...], dict[str, str]]] = [
    ("/api/betting/status", (), {}),
    ("/api/betting/providers", (), {}),
    ("/api/betting/board/{game_id}/{player_id}/{stat}", ("game_id", "player_id", "stat"), {}),
    ("/api/betting/slate/{date}", ("date",), {}),
]
if betting_enabled():
    ROUTES = ROUTES + BETTING_ROUTES

#: The name a refusal must give for each parameter (the live routes' path names).
FIELD: dict[str, str] = {"live_date": "date", "event_id": "game_id"}

FIXED_BAD: tuple[str, ...] = (
    "",
    " ",
    "  pts",
    "pts ",
    "PTS",
    "0",
    "-1",
    "-0",
    "+1",
    "1e9",
    "1e309",
    "-1e309",
    "nan",
    "NaN",
    "inf",
    "-inf",
    "Infinity",
    "0x1F",
    "1_000",
    "١٢٣",
    "12.5.1",
    "9" * 40,
    "-" + "9" * 40,
    "2147483648",
    "-2147483649",
    "18446744073709551616",
    "0.0000001",
    "200.5",
    "1e-320",
    "true",
    "null",
    "None",
    "undefined",
    "[]",
    "{}",
    '{"a":1}',
    "[1,2]",
    "..",
    ".",
    "../",
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "%2e%2e%2f",
    "....//",
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "\\\\server\\share",
    "~/.ssh/id_rsa",
    "file:///etc/hosts",
    "a\x00b",
    "\x00",
    "\r\nSet-Cookie: x=1",
    "\t",
    "\x7f",
    "\u202e",
    "\ufeff",
    "\ud7ff",
    "<script>alert(1)</script>",
    "' OR '1'='1",
    "1; DROP TABLE games",
    '"',
    "'",
    "`",
    "$(id)",
    "${jndi:ldap://x}",
    "%00",
    "%",
    "%%",
    "%zz",
    "?",
    "#",
    "&",
    "=",
    ";",
    ",",
    "*",
    "|",
    "2024-11-12 ",
    " 2024-11-12",
    "2024-11-12T00:00:00",
    "2024-02-30",
    "2024-13-01",
    "2024-00-10",
    "0000-00-00",
    "9999-12-31",
    "2024-1-1",
    "20241112",
    "+2024-11-12",
    "2024/11/12",
    "12-11-2024",
    "2025-01-15",
    "2025-11-12",
    "1970-01-01",
    "2024-11-12\x00",
    "２０２４-１１-１２",
    "0022400001 ",
    "0022400001x",
    "00224000010022400001",
    "1" * 13,
    "40190995",
    "4019099521234",
    "a" * 300,
    "a" * 5000,
    "é" * 200,
    "😀",
    "😀" * 100,
    "ǅ",
    "İ",
    "ß",
    "over ",
    "OVER",
    "both",
    "under\x00",
    "-20.5",
    "20.25",
    "20,5",
    "1/2",
)

_POOL = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "-_.~!$&'()*+,;=:@/\\%?#[]{}<>|^` \"\t\x00\x01\x1f\x7féü€😀ı\u0130\u200b\u202e"
)


def random_values(rng: random.Random, k: int) -> list[str]:
    out: list[str] = []
    for _ in range(k):
        kind = rng.randrange(6)
        if kind == 0:
            out.append("".join(rng.choice(_POOL) for _ in range(rng.randrange(1, 40))))
        elif kind == 1:
            out.append(str(rng.randint(-(10**25), 10**25)))
        elif kind == 2:
            out.append(repr(rng.uniform(-1e6, 1e6)))
        elif kind == 3:
            y, m, d = rng.randint(0, 99999), rng.randint(-1, 99), rng.randint(-1, 99)
            out.append(f"{y:04d}-{m:02d}-{d:02d}")
        elif kind == 4:
            out.append("".join(rng.choice("0123456789") for _ in range(rng.randrange(1, 30))))
        else:
            out.append(rng.choice(FIXED_BAD) + rng.choice(("", "/", "%", "..", "\x00", "x")))
    return out


@dataclass(frozen=True)
class Case:
    url: str
    route: str
    param: str  # the parameter carrying the bad value ("" for an extra query key)
    value: str
    in_path: bool


def _url(template: str, path: dict[str, str], query: dict[str, str]) -> str:
    u = template
    for k, v in path.items():
        u = u.replace("{" + k + "}", quote(v, safe=""))
    if query:
        u += "?" + "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query.items())
    return u


def cases(seed: int = 20261117, per_param: int = 60) -> Iterator[Case]:
    """Every route with each parameter replaced by every fixed bad value and ``per_param`` random
    ones, plus an unknown query key and a repeated key per route."""
    rng = random.Random(seed)  # noqa: S311 - a seeded test draw, not a secret
    for template, path_params, query in ROUTES:
        valid_path = {p: VALID[p] for p in path_params}
        params = [(p, True) for p in path_params] + [(q, False) for q in query]
        for param, in_path in params:
            for v in FIXED_BAD + tuple(random_values(rng, per_param)):
                path = dict(valid_path)
                q = dict(query)
                if in_path:
                    path[param] = v
                else:
                    q[param] = v
                yield Case(_url(template, path, q), template, param, v, in_path)
        extra = _url(template, valid_path, {**query, "zz": "1"})
        yield Case(extra, template, "", "zz=1", False)
        if query:
            k = next(iter(query))
            dup = _url(template, valid_path, query) + f"&{quote(k)}=x"
            yield Case(dup, template, k, "x", False)


def field_name(param: str) -> str:
    return FIELD.get(param, param)
