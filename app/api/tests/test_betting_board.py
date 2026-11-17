"""The betting quote board (docs/contracts/betting_board_v1.md): stored quote snapshots side by
side, switch on only. Exact key sets, the newest-snapshot-before-tip rule, empty and refused
states, the holdout, and no model number, price ranking or money word anywhere."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import edit_db, seal
from fastapi.testclient import TestClient

from parlayhitters_api.betting import board, catalog
from parlayhitters_api.deps import get_store
from parlayhitters_api.errors import REGISTRY
from parlayhitters_api.main import create_app, reset_store

FIX = Path(__file__).resolve().parents[2] / "fixtures"
BETTING_SRC = Path(__file__).resolve().parents[1] / "src" / "parlayhitters_api" / "betting"
ERR_KEYS = {"ok", "code", "text", "field", "request_id", "detail"}

GID, PID, STAT = "0022400015", 990001, "ast"  # export_small: run r_a_2024-11-05, tip 2024-11-06 00Z
BOARD = f"/api/betting/board/{GID}/{PID}/{STAT}"
WHERE = f"game_id = '{GID}' AND player_id = {PID} AND stat = '{STAT}'"
FIVE = ["draftkings", "fanduel", "kalshi", "novig", "polymarket"]
UNKNOWN = ["fee", "settlement_rule_id", "liquidity", "live_status"]

PROVIDERS_KEYS = {"as_of", "data_version", "providers", "unknown", "note"}
PROVIDER_KEYS = {
    "id", "label", "kind", "price_unit", "newest_snapshot_utc", "n_quotes", "fee",
    "settlement_rule_id",
}  # fmt: skip
BOARD_KEYS = {
    "game_id", "date", "tip_utc", "final", "player_id", "player", "team", "opponent", "stat",
    "as_of", "data_version", "provenance", "lines", "providers_absent", "model_ref", "unknown",
    "note",
}  # fmt: skip
LINE_KEYS = {"line", "quotes"}
QUOTE_KEYS = {
    "quote_id", "provider", "side", "price_american", "implied_prob_raw", "snapshot_utc",
    "minutes_before_tip", "is_alternate", "usable", "unusable_reason",
}  # fmt: skip
SLATE_KEYS = {"date", "as_of", "games", "props"}
SLATE_GAME_KEYS = {"game_id", "tip_utc", "home", "away", "final", "n_props_with_quotes"}
SLATE_PROP_KEYS = {"game_id", "player_id", "player", "team", "stat", "n_providers", "n_lines"}
#: Every key any board payload may carry; a model number or price ranking would add one.
ALL_KEYS = (
    PROVIDERS_KEYS | PROVIDER_KEYS | BOARD_KEYS | LINE_KEYS | QUOTE_KEYS | SLATE_KEYS
    | SLATE_GAME_KEYS | SLATE_PROP_KEYS | {"props", "source"}
)  # fmt: skip
#: The only keys whose value may be a non-integer number: stored line, stored implied, stored lead.
FLOAT_KEYS = {"line", "implied_prob_raw", "minutes_before_tip"}

#: The money-safety grep of app/web/src/bettingSafety.test.ts, run here over the API half.
ALLOWED = ("no orders are placed",)
FORBIDDEN = (
    re.compile(r"authorization", re.I),
    re.compile(r"api[_-]?key", re.I),
    re.compile(r"secret", re.I),
    re.compile(r"place_order", re.I),
    re.compile(r"execute", re.I),
    re.compile(r"\btokens?\b", re.I),
    re.compile(r"\borders?\b", re.I),
    re.compile(r"https?://|wss?://", re.I),
    re.compile(r"\b(?:XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b"),
    re.compile(
        r"^\s*(?:import|from)\s+(?:requests|httpx|aiohttp|urllib3?|socket|http\.client)\b", re.M
    ),
    re.compile(r"@\w+\.(?:post|put|patch|delete|api_route|websocket)\s*\(|add_api_route|methods\s*="),
)  # fmt: skip


def _words_bad(text: str) -> list[str]:
    for p in ALLOWED:
        text = text.replace(p, " ")
    return [f.pattern for f in FORBIDDEN if f.search(text)]


def _env(monkeypatch: pytest.MonkeyPatch, export: Path | None) -> None:
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_LIVE_DIR", str(FIX / "live"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    monkeypatch.setenv("APP_CHECKPOINT_SNAPSHOT", str(FIX / "no_checkpoint_snapshot.json"))
    if export is None:
        monkeypatch.delenv("APP_EXPORT_DIR", raising=False)
    else:
        monkeypatch.setenv("APP_EXPORT_DIR", str(export))


def _api(
    monkeypatch: pytest.MonkeyPatch, export: Path | None, betting: bool
) -> Iterator[TestClient]:
    _env(monkeypatch, export)
    reset_store()
    with TestClient(create_app(betting=betting), raise_server_exceptions=False) as c:
        yield c
    reset_store()


@pytest.fixture
def on_export(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _api(monkeypatch, export_dir, True)


@pytest.fixture
def on_replay(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _api(monkeypatch, None, True)


def _typed(r: Any, status: int, code: str, field: str | None = None) -> None:
    body = r.json()
    assert r.status_code == status, (r.status_code, body)
    assert set(body) == ERR_KEYS and body["code"] == code, body
    assert REGISTRY[code]["status"] == status
    if field is not None:
        assert body["field"] == field, body


def _walk(x: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    if isinstance(x, dict):
        for k, v in x.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v, path + "[]")


def _no_model_number(payload: Any) -> None:
    for where, k, v in _walk(payload):
        assert k in ALL_KEYS, f"unexpected key {where}.{k}"
        if isinstance(v, float):
            assert k in FLOAT_KEYS, f"a non-stored number at {where}.{k}"


def _no_clock(x: Any) -> Any:
    """A payload without its wall-clock keys (``clock_utc`` is the time of the request)."""
    if isinstance(x, dict):
        return {k: _no_clock(v) for k, v in x.items() if not k.endswith("clock_utc")}
    if isinstance(x, list):
        return [_no_clock(v) for v in x]
    return x


def _plant(d: Path, *changes: str) -> None:
    """Copies of one stored draftkings quote of the test prop with ``changes`` (REPLACE items)."""
    edit_db(
        d,
        *(
            f"INSERT INTO prop_lines SELECT * REPLACE ({c}) FROM prop_lines WHERE {WHERE} "  # noqa: S608
            "AND book = 'draftkings' AND side = 'over' AND line = 6.5"
            for c in changes
        ),
    )


# ---------------------------------------------------------------- switch OFF


@pytest.mark.parametrize("export", [False, True])
def test_off_the_new_paths_are_the_ordinary_404(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch, export: bool
) -> None:
    for c in _api(monkeypatch, export_dir if export else None, False):
        for path in ("/api/betting/providers", BOARD, "/api/betting/slate/2024-11-12"):
            _typed(c.get(path), 404, "not_found")


def test_on_existing_responses_are_unchanged(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store hook adds nothing to any existing payload: the same bodies with the switch on."""
    paths = [
        f"/api/props/{GID}/{PID}/{STAT}?line=6.5",
        f"/api/card/{GID}/{PID}/{STAT}",
        f"/api/breakdown/{GID}/{PID}/{STAT}",
        f"/api/games/{GID}",
        "/api/slates/2024-11-05",
        "/api/meta",
    ]
    got: dict[bool, list[Any]] = {}
    for on in (False, True):
        for c in _api(monkeypatch, export_dir, on):
            got[on] = [_no_clock(c.get(p).json()) for p in paths]
    assert got[False] == got[True]


# ---------------------------------------------------------------- providers


def test_providers_on_the_small_export(on_export: TestClient) -> None:
    r = on_export.get("/api/betting/providers")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == PROVIDERS_KEYS
    assert body["unknown"] == UNKNOWN
    assert body["note"] == (
        "Stored snapshots from the nightly export. Delayed, not live. Analysis only; this service "
        "is read-only."
    )
    meta = on_export.get("/api/meta").json()
    assert (body["as_of"], body["data_version"]) == (meta["data_as_of"], meta["data_version"])
    assert [p["id"] for p in body["providers"]] == FIVE  # sorted by id, nothing else
    for p in body["providers"]:
        assert set(p) == PROVIDER_KEYS
        assert p["fee"] is None and p["settlement_rule_id"] is None
        assert p["price_unit"] == "american"
        assert p["kind"] == (
            "exchange" if p["id"] in {"kalshi", "novig", "polymarket"} else "sportsbook"
        )
        assert p["n_quotes"] > 0 and p["newest_snapshot_utc"] == "2024-11-12T23:00:00Z"
    assert {p["id"]: p["label"] for p in body["providers"]}["draftkings"] == "DraftKings"
    _no_model_number(body)


def test_providers_count_exactly_what_the_boards_serve(on_export: TestClient) -> None:
    prov = {
        p["id"]: p["n_quotes"] for p in on_export.get("/api/betting/providers").json()["providers"]
    }
    served: dict[str, int] = {}
    for d in on_export.get("/api/meta").json()["slates"]:
        for p in on_export.get(f"/api/betting/slate/{d}").json()["props"]:
            b = on_export.get(
                f"/api/betting/board/{p['game_id']}/{p['player_id']}/{p['stat']}"
            ).json()
            for ln in b["lines"]:
                for q in ln["quotes"]:
                    served[q["provider"]] = served.get(q["provider"], 0) + 1
    assert served == prov


def test_fixture_replay_has_no_snapshots_and_says_so(on_replay: TestClient) -> None:
    p = on_replay.get("/api/betting/providers").json()
    assert p["providers"] == [] and p["unknown"] == UNKNOWN
    b = on_replay.get("/api/betting/board/0022400001/1628369/pts")
    assert b.status_code == 200
    body = b.json()
    assert body["lines"] == [] and body["providers_absent"] == []
    assert body["note"].endswith("This data source (the fixture replay) stores no quote snapshots.")
    s = on_replay.get("/api/betting/slate/2024-11-12").json()
    assert s["props"] == [] and all(g["n_props_with_quotes"] == 0 for g in s["games"])


# ---------------------------------------------------------------- board


def test_board_shape_and_stored_values(on_export: TestClient, export_dir: Path) -> None:
    r = on_export.get(BOARD)
    assert r.status_code == 200
    b = r.json()
    assert set(b) == BOARD_KEYS
    assert (b["game_id"], b["player_id"], b["stat"]) == (GID, PID, STAT)
    assert (
        b["date"] == "2024-11-05" and b["tip_utc"] == "2024-11-06T00:00:00Z" and b["final"] is True
    )
    assert b["provenance"] == "delayed" and b["unknown"] == UNKNOWN
    assert b["model_ref"] == {
        "props": f"/api/props/{GID}/{PID}/{STAT}",
        "source": f"/api/record/{GID}/{PID}/{STAT}",
    }
    assert (
        "implied_prob_raw is the raw implied probability the export stored, vig included"
        in b["note"]
    )
    assert "newest snapshot stored before tip-off" in b["note"]
    assert b["team"] and b["opponent"] and b["team"] != b["opponent"]
    assert [ln["line"] for ln in b["lines"]] == sorted(ln["line"] for ln in b["lines"])
    ids: list[str] = []
    seen: set[tuple[float, str, str]] = set()
    for ln in b["lines"]:
        assert set(ln) == LINE_KEYS
        keys = [(q["provider"], q["side"] != "over") for q in ln["quotes"]]
        assert keys == sorted(keys)  # by provider id, then over before under: never by price
        for q in ln["quotes"]:
            assert set(q) == QUOTE_KEYS
            k = (ln["line"], q["provider"], q["side"])
            assert k not in seen  # one quote per line, provider and side
            seen.add(k)
            ids.append(q["quote_id"])
            assert q["quote_id"] == "|".join(
                [
                    GID,
                    str(PID),
                    STAT,
                    f"{ln['line']:g}",
                    q["provider"],
                    q["side"],
                    q["snapshot_utc"],
                ]
            )
    assert len(ids) == len(set(ids))
    assert b["providers_absent"] == []  # all five providers quote this prop
    _no_model_number(b)
    # The prices and implied values are the stored ones, row for row.
    import duckdb

    con = duckdb.connect(str(export_dir / "export.duckdb"), read_only=True)
    try:
        stored = {
            (float(ln), bk, sd): (pa, ip)
            for ln, bk, sd, pa, ip in con.execute(
                "SELECT line, book, side, price_american, implied_prob_raw FROM prop_lines"  # noqa: S608
                f" WHERE {WHERE}"
            ).fetchall()
        }
    finally:
        con.close()
    for ln in b["lines"]:
        for q in ln["quotes"]:
            pa, ip = stored[(ln["line"], q["provider"], q["side"])]
            if q["usable"]:
                assert (q["price_american"], q["implied_prob_raw"]) == (pa, ip)
            else:
                assert q["price_american"] is None and q["implied_prob_raw"] is None


def test_an_unusable_quote_carries_no_price(on_export: TestClient) -> None:
    b = on_export.get(BOARD).json()
    poly = [
        q
        for ln in b["lines"]
        for q in ln["quotes"]
        if q["provider"] == "polymarket" and q["side"] == "under"
    ]
    assert poly and all(not q["usable"] for q in poly)
    for q in poly:
        assert q["price_american"] is None and q["implied_prob_raw"] is None
        assert q["unusable_reason"] == "polymarket_under_placeholder"


def test_newest_snapshot_before_tip_wins(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tip-off is 2024-11-06 00:00Z and the stored draftkings 6.5 over is from 20:40Z (-119)."""
    _plant(
        export_dir,
        # newer and before tip: served, with ITS stored implied value (not one read off the price)
        "TIMESTAMP '2024-11-05 22:00:00' AS snapshot_ts, 120.0 AS minutes_before_tip,"
        " -150 AS price_american, 0.6123 AS implied_prob_raw",
        # same snapshot, alternate ladder: the main market's quote comes first on a tie
        "TIMESTAMP '2024-11-05 22:00:00' AS snapshot_ts, 120.0 AS minutes_before_tip,"
        " -170 AS price_american, 0.63 AS implied_prob_raw, TRUE AS is_alternate",
        # newer, but the stored lead says at or after tip: never served
        "TIMESTAMP '2024-11-05 23:59:00' AS snapshot_ts, 0.0 AS minutes_before_tip,"
        " -200 AS price_american",
        # claims a lead but its snapshot is after the known tip-off: never served
        "TIMESTAMP '2024-11-06 00:30:00' AS snapshot_ts, 30.0 AS minutes_before_tip,"
        " -250 AS price_american",
        # newer and before tip, but in another quote run than the shown prop's: not loaded
        "TIMESTAMP '2024-11-05 23:30:00' AS snapshot_ts, 30.0 AS minutes_before_tip,"
        " -300 AS price_american, 'r_other' AS run_id",
    )
    for c in _api(monkeypatch, export_dir, True):
        b = c.get(BOARD).json()
        got = [
            q for ln in b["lines"] if ln["line"] == 6.5 for q in ln["quotes"]
            if q["provider"] == "draftkings" and q["side"] == "over"
        ]  # fmt: skip
        assert len(got) == 1
        q = got[0]
        assert q["snapshot_utc"] == "2024-11-05T22:00:00Z"
        assert (q["price_american"], q["implied_prob_raw"], q["is_alternate"]) == (
            -150,
            0.6123,
            False,
        )
        assert q["minutes_before_tip"] == 120.0


def test_a_missing_lead_is_judged_by_the_tip(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _plant(
        export_dir,
        "TIMESTAMP '2024-11-05 21:00:00' AS snapshot_ts, NULL AS minutes_before_tip,"
        " -140 AS price_american",
    )
    for c in _api(monkeypatch, export_dir, True):
        b = c.get(BOARD).json()
        q = next(
            q for ln in b["lines"] if ln["line"] == 6.5 for q in ln["quotes"]
            if q["provider"] == "draftkings" and q["side"] == "over"
        )  # fmt: skip
        assert (q["snapshot_utc"], q["price_american"], q["minutes_before_tip"]) == (
            "2024-11-05T21:00:00Z", -140, None,
        )  # fmt: skip


def test_the_rule_unit() -> None:
    def q(
        snap: str | None, mbt: float | None, alt: bool = False, price: int = -110
    ) -> dict[str, Any]:
        return {
            "book": "x", "line": 1.5, "side": "over", "price": price, "unusable": False,
            "reason": None, "alt": alt, "minutes_before_tip": mbt, "snapshot_utc": snap,
        }  # fmt: skip

    tip = "2024-11-06T00:00:00Z"
    assert board.served([q(None, 10.0)], None, tip) == []  # no snapshot time
    assert (
        board.served([q("2024-11-05T20:00:00Z", None)], None, None) == []
    )  # nothing proves pre-tip
    assert board.served([q("2024-11-05T20:00:00Z", -1.0)], None, None) == []
    assert board.served([q("2024-11-05T20:00:00Z", float("nan"))], None, None) == []
    assert len(board.served([q("2024-11-05T20:00:00Z", 5.0)], None, None)) == 1  # lead alone
    assert len(board.served([q("2024-11-05T20:00:00Z", None)], None, tip)) == 1  # tip alone
    assert board.served([q("2024-11-06T00:00:00Z", None)], None, tip) == []  # at tip is not before
    a, b = q("2024-11-05T20:00:00Z", 5.0, price=-120), q("2024-11-05T20:00:00Z", 5.0, price=-130)
    assert board.served([a, b], None, tip)[0].price_american == -120  # full tie: first stored row


def test_an_empty_prop_is_an_empty_board(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edit_db(export_dir, f"DELETE FROM prop_lines WHERE {WHERE}")  # noqa: S608
    for c in _api(monkeypatch, export_dir, True):
        r = c.get(BOARD)
        assert r.status_code == 200
        b = r.json()
        assert b["lines"] == [] and b["providers_absent"] == FIVE
        assert set(b) == BOARD_KEYS
        slate = c.get("/api/betting/slate/2024-11-05").json()
        assert not [
            p
            for p in slate["props"]
            if (p["game_id"], p["player_id"], p["stat"]) == (GID, PID, STAT)
        ]


def test_unknown_ids_are_typed_refusals(on_export: TestClient) -> None:
    _typed(
        on_export.get(f"/api/betting/board/0029999999/{PID}/{STAT}"), 404, "unknown_game", "game_id"
    )
    _typed(on_export.get(f"/api/betting/board/{GID}/1/{STAT}"), 404, "unknown_player", "player_id")
    _typed(on_export.get(f"/api/betting/board/{GID}/{PID}/stl"), 404, "unknown_stat", "stat")
    _typed(on_export.get(f"/api/betting/board/{GID}/{PID}/pra"), 404, "unknown_stat", "stat")
    _typed(on_export.get(f"/api/betting/board/{GID}/x/{STAT}"), 422, "bad_request", "player_id")
    _typed(on_export.get("/api/betting/slate/2024-11-06"), 404, "unknown_slate", "date")
    _typed(on_export.get("/api/betting/slate/2024-02-30"), 404, "not_a_date", "date")


def test_no_forecast_for_that_stat(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edit_db(export_dir, f"DELETE FROM prop_grids WHERE {WHERE}")  # noqa: S608
    for c in _api(monkeypatch, export_dir, True):
        _typed(c.get(BOARD), 404, "no_forecast", "stat")


def test_a_refused_export_is_503_on_every_board_route(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seal(export_dir)
    m = json.loads((export_dir / "manifest.json").read_text())
    m["files"]["export.duckdb"]["sha256"] = "0" * 64
    (export_dir / "manifest.json").write_text(json.dumps(m))
    for c in _api(monkeypatch, export_dir, True):
        for path in ("/api/betting/providers", BOARD, "/api/betting/slate/2024-11-05"):
            _typed(c.get(path), 503, "export_refused")


# ---------------------------------------------------------------- slate


def test_slate_lists_the_props_with_quotes(on_export: TestClient) -> None:
    r = on_export.get("/api/betting/slate/2024-11-05")
    assert r.status_code == 200
    s = r.json()
    assert set(s) == SLATE_KEYS and s["date"] == "2024-11-05"
    assert [g["game_id"] for g in s["games"]] == ["0022400015", "0022400016"]
    for g in s["games"]:
        assert set(g) == SLATE_GAME_KEYS
        assert g["n_props_with_quotes"] == sum(
            1 for p in s["props"] if p["game_id"] == g["game_id"]
        )
    assert s["props"]
    for p in s["props"]:
        assert set(p) == SLATE_PROP_KEYS
        b = on_export.get(f"/api/betting/board/{p['game_id']}/{p['player_id']}/{p['stat']}").json()
        quotes = [q for ln in b["lines"] for q in ln["quotes"]]
        assert p["n_lines"] == len(b["lines"])
        assert p["n_providers"] == len({q["provider"] for q in quotes})
        assert p["team"] == b["team"] and p["player"] == b["player"]
    _no_model_number(s)


# ---------------------------------------------------------------- holdout; providers never shown


def test_a_holdout_game_refuses_the_whole_export(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit_db(
        export_dir,
        f"INSERT INTO games SELECT * REPLACE ('0022500999' AS game_id, 2025 AS season) FROM games WHERE game_id = '{GID}'",  # noqa: S608, E501
    )
    for c in _api(monkeypatch, export_dir, True):
        for path in ("/api/betting/providers", BOARD, "/api/betting/board/0022500999/990001/ast"):
            _typed(c.get(path), 503, "export_refused")


def test_holdout_quote_rows_never_reach_the_board(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forecast and quote rows planted for a 2025-26 game id the games table does not hold, under
    a provider id seen nowhere else: no route serves or counts them."""
    hold = "0022500999"
    edit_db(
        export_dir,
        f"INSERT INTO prop_grids SELECT * REPLACE ('{hold}' AS game_id) FROM prop_grids"  # noqa: S608
        f" WHERE {WHERE}",
        f"INSERT INTO prop_lines SELECT * REPLACE ('{hold}' AS game_id, 'planted2025' AS book) FROM prop_lines WHERE {WHERE}",  # noqa: S608, E501
    )
    for c in _api(monkeypatch, export_dir, True):
        prov = c.get("/api/betting/providers").json()
        assert [p["id"] for p in prov["providers"]] == FIVE
        _typed(c.get(f"/api/betting/board/{hold}/{PID}/{STAT}"), 404, "unknown_game", "game_id")
        for d in c.get("/api/meta").json()["slates"]:
            assert hold not in c.get(f"/api/betting/slate/{d}").text


def test_a_holdout_slate_in_the_store_is_never_served(on_export: TestClient) -> None:
    """Belt and braces behind the store's own refusal: a slate marked 2025 is unknown here."""
    assert on_export.get(BOARD).status_code == 200
    store = get_store()
    store.slates["2024-11-05"]["season"] = 2025
    _typed(on_export.get("/api/betting/slate/2024-11-05"), 404, "unknown_slate", "date")
    _typed(on_export.get(BOARD), 404, "unknown_game", "game_id")


def test_prizepicks_is_never_a_provider(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edit_db(
        export_dir,
        "INSERT INTO prop_lines SELECT * REPLACE ('prizepicks' AS book) FROM prop_lines"  # noqa: S608
        f" WHERE {WHERE}",
    )
    for c in _api(monkeypatch, export_dir, True):
        assert "prizepicks" not in c.get("/api/betting/providers").text
        assert "prizepicks" not in c.get(BOARD).text


# ---------------------------------------------------------------- words and catalog


def test_money_safety_words_absent_from_the_betting_package() -> None:
    files = sorted(BETTING_SRC.glob("*.py"))
    assert {f.name for f in files} >= {
        "__init__.py",
        "routes.py",
        "board.py",
        "catalog.py",
        "models.py",
    }
    bad = {f.name: _words_bad(f.read_text()) for f in files}
    assert not {k: v for k, v in bad.items() if v}, bad
    assert (
        _words_bad("an order") and _words_bad("api_key") and not _words_bad("no orders are placed")
    )


def test_money_safety_words_absent_from_the_payloads(on_export: TestClient) -> None:
    for path in ("/api/betting/providers", BOARD, "/api/betting/slate/2024-11-05"):
        assert not _words_bad(on_export.get(path).text), path


def test_exchange_list_is_the_documented_one() -> None:
    assert catalog.EXCHANGES == {"kalshi", "polymarket", "novig", "prophetx", "betopenly"}
    assert catalog.kind("pinnacle") == "sportsbook" and catalog.kind("kalshi") == "exchange"
    assert catalog.label("someday_book") == "someday_book"
    assert catalog.UNKNOWN == tuple(UNKNOWN)
