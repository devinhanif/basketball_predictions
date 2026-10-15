"""The nightly export as a read-only store.

Reads the directory `python -m nba.export build` writes: ``export.duckdb``, ``parlay_of_day.json``
and ``manifest.json``. Nothing here imports ``nba``; the files are the whole interface.

Before a byte of the database is opened the manifest is checked:
  * it must be valid JSON with ``schema_version`` 1 or 2, a non-empty ``data_version`` and a
    ``made_at`` that parses (an export that cannot say what data it came from, or when, is refused);
  * the season 2025 holdout may not appear in its ``seasons`` (or in any table);
  * the sha256 of ``export.duckdb`` and ``parlay_of_day.json`` must equal the manifest's.
Any failure raises ``ExportRefused``; the API answers 503 with the reason rather than showing
something it cannot vouch for.

The tables are read once, with ``read_only=True``, then closed. The result is reshaped into the
same dictionaries the fixture slates use, so the routes and the leg breakdown do not care which
source they run on. What the export does not carry is left absent, never invented:
  * schema 1 has no sportsbook lines, so a prop's line is the middle of the model's own forecast,
    labelled "reference", with no prices, swipe deck or market probability. Schema 2 carries
    ``prop_lines``: the posted main line (the line most books agree on) and the books' prices at
    it, including exchanges, plus alternate ladders. A quote flagged ``unusable`` is never shown
    as a price. Lines exist only for points, rebounds, assists and threes;
  * steals and blocks exist from schema 2 (a game whose box score has them NULL is left out of
    those stats, never counted as zero); player positions are never exported (no "opponent allows
    to his position"); the home/away side of games older than the lookback window is unknown.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from .forecast import reference_line
from .history import STAT_ORDER, History

Json = dict[str, Any]

SCHEMA_VERSIONS = (1, 2)
DB_NAME = "export.duckdb"
PARLAY_NAME = "parlay_of_day.json"
MANIFEST_NAME = "manifest.json"
HOLDOUT_SEASON = 2025
MODEL_STATS = ("pts", "reb", "ast", "fg3m")
EXPORT_STATS = ("pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra")
#: Only used for an export that predates best_model_schema (no is_primary column): the old rule.
PREFERRED_PROP_MODEL = "props_context_residual"
BEST_MODEL_SCHEMA = "1"
ET = ZoneInfo("America/New_York")
NO_POSITIVE_EV = "no_positive_ev_found"
POSITIVE_EV = "positive_ev_at_conservative_bound"
TWO_WORD_NICKNAMES = ("Trail Blazers",)
#: Sportsbooks first, in this order, for the one price shown beside a prop; any other book
#: (exchanges such as kalshi, novig, polymarket) follows alphabetically. Every book's quote is
#: still shown on the leg screen.
BOOK_ORDER = ("draftkings", "fanduel", "betmgm", "caesars", "espnbet", "fanatics")


class ExportRefused(ValueError):
    """The export on disk cannot be trusted or is not one this app may show."""


def _utc_iso(ts: datetime) -> str:
    """A naive-UTC timestamp (as the export stores them) as an ISO string with Z."""
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _et_to_utc_iso(ts: datetime) -> str:
    return ts.replace(tzinfo=ET).astimezone(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_NAME


def has_export(directory: Path | None) -> bool:
    return directory is not None and manifest_path(directory).is_file()


def verify(directory: Path) -> Json:
    """Check the manifest and the two files it names; return the manifest or raise."""
    try:
        manifest = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportRefused(f"manifest.json is not readable JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ExportRefused("manifest.json is not a JSON object")
    if manifest.get("schema_version") not in SCHEMA_VERSIONS:
        raise ExportRefused(f"unsupported schema_version {manifest.get('schema_version')!r}")
    dv = manifest.get("data_version")
    if not isinstance(dv, str) or not dv.strip():
        raise ExportRefused("manifest has no data_version: cannot say what data this came from")
    made = manifest.get("made_at")
    if not isinstance(made, str):
        raise ExportRefused("manifest has no made_at: cannot say when this was built")
    try:
        datetime.fromisoformat(made.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportRefused(f"manifest made_at is not a timestamp: {made!r}") from exc
    seasons = manifest.get("seasons")
    if not isinstance(seasons, list) or not seasons or not all(isinstance(s, int) for s in seasons):
        raise ExportRefused("manifest has no list of seasons")
    if HOLDOUT_SEASON in seasons:
        raise ExportRefused(f"manifest lists season {HOLDOUT_SEASON}, the frozen holdout")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ExportRefused("manifest has no file checksums")
    for name in (DB_NAME, PARLAY_NAME):
        want = (files.get(name) or {}).get("sha256")
        if not isinstance(want, str) or len(want) != 64:
            raise ExportRefused(f"manifest has no sha256 for {name}")
        path = directory / name
        if not path.is_file():
            raise ExportRefused(f"{name} is missing")
        if sha256_file(path) != want:
            raise ExportRefused(f"{name} does not match the manifest sha256")
    return manifest


def _table(con: duckdb.DuckDBPyConnection, name: str) -> list[Json]:
    cur = con.execute(f'SELECT * FROM "{name}"')  # noqa: S608 - fixed table names
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _lead_mean(player: Json) -> float:
    """The forecast mean used to order a team's players: points if he has them, else any stat."""
    props = player["props"]
    lead = props.get("pts") or next(iter(props.values()))
    return float(lead["forecast"]["mean"] or 0.0)


def _book_rank(book: str) -> tuple[int, str]:
    return (BOOK_ORDER.index(book) if book in BOOK_ORDER else len(BOOK_ORDER), book)


def _quote(r: Json) -> Json:
    """A prop_lines row as the app keeps it. An unusable quote keeps no price at all."""
    price = int(r["price_american"])
    # Beyond the exporter's own flag: a number that is not an American price cannot be shown.
    bad = r["unusable"] or price == 0 or -100 < price < 100
    return {
        "book": r["book"],
        "line": float(r["line"]),
        "side": r["side"],
        "price": None if bad else price,
        "unusable": bool(bad),
        "reason": r["unusable_reason"] or ("not_american_odds" if bad else None),
        "alt": bool(r["is_alternate"]),
        "minutes_before_tip": r["minutes_before_tip"],
        "snapshot_utc": None if r["snapshot_ts"] is None else _utc_iso(r["snapshot_ts"]),
    }


def main_line(quotes: list[Json]) -> float | None:
    """The posted main line: among usable non-alternate quotes, the line the most books carry;
    a tie goes to the line nearest the median of the tied lines, then the lower one. None when no
    book posted a usable main line."""
    books: dict[float, set[str]] = defaultdict(set)
    for q in quotes:
        if not q["alt"] and not q["unusable"]:
            books[q["line"]].add(q["book"])
    if not books:
        return None
    top = max(len(b) for b in books.values())
    tied = sorted(ln for ln, b in books.items() if len(b) == top)
    mid = statistics.median(tied)
    return min(tied, key=lambda ln: (abs(ln - mid), ln))


def line_entries(quotes: list[Json], main: float | None) -> list[Json]:
    """One price entry per line for the breakdown's ladder: for each side, the best-ranked book
    with a usable price there, preferring one book that has both sides."""
    by_line: dict[float, dict[str, dict[str, Json]]] = defaultdict(lambda: defaultdict(dict))
    for q in quotes:
        if not q["unusable"]:
            by_line[q["line"]][q["book"]][q["side"]] = q
    out: list[Json] = []
    for ln in sorted(by_line):
        books = by_line[ln]
        both = sorted((b for b, s in books.items() if "over" in s and "under" in s), key=_book_rank)
        pick: dict[str, Json] = {}
        for side in ("over", "under"):
            if both:
                pick[side] = books[both[0]][side]
            else:
                cands = sorted((b for b, s in books.items() if side in s), key=_book_rank)
                if cands:
                    pick[side] = books[cands[0]][side]
        ov, un = pick.get("over"), pick.get("under")
        out.append(
            {
                "main": ln == main,
                "point": ln,
                "over_price": None if ov is None else ov["price"],
                "under_price": None if un is None else un["price"],
                "over_book": None if ov is None else ov["book"],
                "under_book": None if un is None else un["book"],
                "snapshot_utc": (ov or un or {}).get("snapshot_utc"),
            }
        )
    return out


def _split_name(full: str) -> tuple[str, str]:
    for nick in TWO_WORD_NICKNAMES:
        if full.endswith(" " + nick):
            return full[: -len(nick) - 1], nick
    head, _, tail = full.rpartition(" ")
    return (head, tail) if head else ("", full)


def _stamp_text(row: Json) -> str:
    bits = [f"{row['model_name']} {row['version']}"]
    if row.get("stamp_fit_through"):
        bits.append(f"fit through {row['stamp_fit_through']}")
    if row.get("stamp_n_train"):
        bits.append(f"n_train {row['stamp_n_train']}")
    if row.get("stamp_weights_sha256"):
        bits.append(f"weights {str(row['stamp_weights_sha256'])[:12]}")
    if row.get("bundle_month"):
        bits.append(f"bundle {row['bundle_month']}")
    bits.append(f"made {_utc_iso(row['made_at'])}")
    return "; ".join(bits)


def _pick_latest(
    rows: list[Json], key: Callable[[Json], Any], prefer: Callable[[Json], bool]
) -> dict[Any, Json]:
    """Per key, the usable row: the preferred kind first, then the newest made_at."""
    best: dict[Any, tuple[tuple[bool, datetime, str], Json]] = {}
    for r in rows:
        k = key(r)
        rank = (prefer(r), r["made_at"], r["run_id"])
        cur = best.get(k)
        if cur is None or rank > cur[0]:
            best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


class ExportStore:
    """Same surface as the fixture Store, backed by an export directory."""

    mode = "export"

    def __init__(self, directory: Path, now: Callable[[], datetime] | None = None) -> None:
        self.root = Path(directory)
        self._now = now or (lambda: datetime.now(UTC))
        self.manifest: Json = verify(self.root)
        con = duckdb.connect(str(self.root / DB_NAME), read_only=True)
        try:
            tables = {
                t: _table(con, t)
                for t in (
                    "meta",
                    "games",
                    "win_probs",
                    "prop_grids",
                    "box_scores",
                    "injury_report",
                    "players",
                    "teams",
                )  # fmt: skip
            }
            have = {
                r[0]
                for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
            }
            tables["p_play"] = _table(con, "p_play") if "p_play" in have else []
            if "prop_lines" in have:
                tables["prop_lines"] = _table(con, "prop_lines")
            elif self.manifest["schema_version"] >= 2:
                raise ExportRefused("a schema 2 export has no prop_lines table")
            else:
                tables["prop_lines"] = []
        finally:
            con.close()
        meta = {r["key"]: r["value"] for r in tables["meta"]}
        if meta.get("made_at") != self.manifest["made_at"] or (
            meta.get("data_version", "") != (self.manifest.get("data_version") or "")
        ):
            raise ExportRefused("export.duckdb meta does not match the manifest")
        for t in ("games", "box_scores"):
            if any(int(r["season"]) == HOLDOUT_SEASON for r in tables[t]):
                raise ExportRefused(f"{t} holds season {HOLDOUT_SEASON}, the frozen holdout")
        # best_model_schema 1: prop_grids marks one primary row per prop and p_play carries the
        # chance to play. An export without it keeps the old production-model choice.
        grids = tables["prop_grids"]
        self.best_model: bool = meta.get("best_model_schema") == BEST_MODEL_SCHEMA and (
            not grids or "is_primary" in grids[0]
        )
        self.made_at: str = self.manifest["made_at"]
        self.data_version: str = self.manifest["data_version"]
        self._build(tables)
        self.parlay: Json = self._parlay()
        self.evidence: Json = {
            "available": False,
            "note": "The model-versus-market evidence tables are not part of the nightly export.",
        }

    # ------------------------------------------------------------------ building

    def _build(self, tables: dict[str, list[Json]]) -> None:
        self.names: dict[int, str] = {
            int(r["player_id"]): r["full_name"] for r in tables["players"]
        }
        self.teams: dict[int, Json] = {}
        for r in tables["teams"]:
            city, name = _split_name(str(r["full_name"]))
            self.teams[int(r["team_id"])] = {
                "id": int(r["team_id"]), "abbr": r["abbr"], "city": city, "name": name,
            }  # fmt: skip
        games = {r["game_id"]: r for r in tables["games"]}
        box = tables["box_scores"]
        played = [b for b in box if b["minutes"] is not None and b["minutes"] > 0]
        by_game_box: dict[str, list[Json]] = defaultdict(list)
        for b in box:
            by_game_box[b["game_id"]].append(b)
        # Where a player is: his latest box-score team, else the team on the injury report.
        team_of: dict[int, tuple[Any, int]] = {}
        for b in sorted(played, key=lambda r: (r["game_date"], r["game_id"])):
            team_of[int(b["player_id"])] = (b["game_date"], int(b["team_id"]))
        report_team = {
            int(r["player_id"]): int(r["team_id"])
            for r in tables["injury_report"]
            if r["team_id"] is not None
        }
        win = self._latest_win(tables["win_probs"])
        props = self._latest_props(tables["prop_grids"])
        self.pplay = self._pplay(tables["p_play"], props)
        stamps = {k: _stamp_text(v) for k, v in props.items()}
        # The team the exporter recorded for each forecast (box row or prior game), by game+player.
        self.team_pk: dict[tuple[str, int], int] = {}
        for (gid_, pid_, _st), pr in props.items():
            if pr.get("team_id") is not None:
                self.team_pk[(gid_, pid_)] = int(pr["team_id"])
        self.quotes = self._quotes_by_prop(tables["prop_lines"], props)
        self.has_lines = any(main_line(q) is not None for q in self.quotes.values())
        self.has_stl_blk = any(b.get("stl") is not None or b.get("blk") is not None for b in played)
        self.history = self._history(games, played, tables["injury_report"])

        self.slates: dict[str, Json] = {}
        self.games: dict[str, Json] = {}
        self.game_slate: dict[str, str] = {}
        unplaced_total = 0
        for gid, g in sorted(games.items(), key=lambda kv: (kv[1]["game_date"], kv[0])):
            home = self.teams.get(int(g["home_team"]), self._unknown_team(g["home_team"]))
            away = self.teams.get(int(g["away_team"]), self._unknown_team(g["away_team"]))
            players, unplaced = self._players(
                gid, g, home, away, props, stamps, by_game_box.get(gid, []), team_of, report_team
            )
            unplaced_total += unplaced
            final = bool(g["final"])
            game: Json = {
                "game_id": gid,
                "date": g["game_date"].isoformat(),
                "season": int(g["season"]),
                "tip_utc": _utc_iso(g["tipoff"]),
                "arena": None,
                "home": home,
                "away": away,
                "win_probability": win.get(gid) or self._no_win(),
                "final": final,
                "result": {
                    "home_pts": int(g["home_pts"]),
                    "away_pts": int(g["away_pts"]),
                    "home_won": int(g["home_pts"]) > int(g["away_pts"]),
                }
                if final
                else None,
                "players": players,
                "unplaced": unplaced,
            }
            d = game["date"]
            slate = self.slates.setdefault(
                d, {"date": d, "season": game["season"], "label": f"Games on {d}", "games": []}
            )
            slate["games"].append(game)
            self.games[gid] = game
            self.game_slate[gid] = d
        self.slates = dict(sorted(self.slates.items(), reverse=True))  # newest night first
        for s in self.slates.values():
            s["counts"] = {
                "games": len(s["games"]),
                "players": sum(len(g["players"]) for g in s["games"]),
                "props": sum(len(p["props"]) for g in s["games"] for p in g["players"]),
                "unplaced_players": sum(g["unplaced"] for g in s["games"]),
            }
            s["demo_clock_utc"] = None
        self.unplaced_total = unplaced_total

    @staticmethod
    def _unknown_team(tid: Any) -> Json:
        return {"id": int(tid), "abbr": str(tid), "city": "", "name": f"Team {tid}"}

    @staticmethod
    def _no_win() -> Json:
        return {
            "model_home": None, "model_name": "none stored", "model_stamp": None,
            "market_home": None, "market_name": "no market price in this export",
            "market_snapshot_utc": None,
        }  # fmt: skip

    @staticmethod
    def _latest_win(rows: list[Json]) -> dict[str, Json]:
        usable = [r for r in rows if r["is_latest"] and not r["comparison_only"]]
        best = _pick_latest(usable, lambda r: r["game_id"], lambda r: bool(r["is_primary"]))
        out: dict[str, Json] = {}
        for gid, r in best.items():
            out[gid] = {
                "model_home": r["p_home"],
                "model_name": r["model_name"],
                "model_stamp": _stamp_text(r),
                "market_home": None,
                "market_name": "no market price in this export",
                "market_snapshot_utc": None,
            }
        return out

    @staticmethod
    def _quotes_by_prop(
        lines: list[Json], props: dict[tuple[str, int, str], Json]
    ) -> dict[tuple[str, int, str], list[Json]]:
        """Each exported forecast's quotes: the rows stamped with the run named by the row's
        ``lines_run_id`` (an AVG row names its production counterpart's run), else its own run_id
        (an export that predates the column)."""
        by_run: dict[tuple[str, str, int, str], list[Json]] = defaultdict(list)
        for r in lines:
            by_run[(r["run_id"], r["game_id"], int(r["player_id"]), r["stat"])].append(r)
        out: dict[tuple[str, int, str], list[Json]] = {}
        for key, pr in props.items():
            rows = by_run.get((pr.get("lines_run_id") or pr["run_id"], *key))
            if rows:
                out[key] = [_quote(r) for r in rows]
        return out

    def _latest_props(self, rows: list[Json]) -> dict[tuple[str, int, str], Json]:
        """The one row shown per (game, player, stat).

        Best-model export: the row the exporter marked ``is_primary`` (AVG, or the production
        model standing in for it), nothing else (the exporter flags AVG rows ``comparison_only``
        for its own reasons; ``is_primary`` alone decides here); a prop with no primary row is
        not shown. Older export: the latest pre-tip production row
        (``props_context_residual``) first."""
        usable = [
            r
            for r in rows
            if (self.best_model or not r["comparison_only"])
            and r["stat"] in MODEL_STATS
            and isinstance(r["q_grid"], list)
            and len(r["q_grid"]) == 19
            and all(v is not None for v in r["q_grid"])
            and (bool(r["is_primary"]) if self.best_model else bool(r["is_latest"]))
        ]
        return _pick_latest(
            usable,
            lambda r: (r["game_id"], int(r["player_id"]), r["stat"]),
            lambda r: self.best_model or r["model_name"] == PREFERRED_PROP_MODEL,
        )

    def _pplay(
        self, rows: list[Json], props: dict[tuple[str, int, str], Json]
    ) -> dict[tuple[str, int], Json]:
        """Chance to play per (game, player) with where it came from.

        The p_play table (latest pre-tip row, R else production) when the export has one; else the
        ``p_play_primary`` stored on the shown prop row; else, for an older export, the value
        stored with that row (source ``legacy``)."""
        out: dict[tuple[str, int], Json] = {}
        for (gid, pid, _st), r in props.items():
            if (gid, pid) in out:
                continue
            if self.best_model and r.get("p_play_primary") is not None:
                out[(gid, pid)] = {"value": float(r["p_play_primary"]),
                                   "source": r.get("p_play_source"), "detail": None}  # fmt: skip
            elif not self.best_model and r.get("p_play") is not None:
                out[(gid, pid)] = {"value": float(r["p_play"]), "source": "legacy", "detail": None}
        for r in rows:  # the table is the authority and carries the detail
            if r["p_play_primary"] is not None:
                out[(r["game_id"], int(r["player_id"]))] = {
                    "value": float(r["p_play_primary"]),
                    "source": r["p_play_source"],
                    "detail": r["p_play_detail"],
                    "r": r["p_play_r"],
                    "production": r["p_play_prod"],
                }
        return out

    def _model_info(self, r: Json) -> Json:
        """The model-source facts of a shown row (see model_source.model_label)."""
        if not self.best_model:
            src: Any = "legacy"
            detail: Any = None
        else:
            src, detail = r.get("primary_source"), r.get("primary_detail")
        return {
            "source": src,
            "detail": detail,
            "name": r["model_name"],
            "version": r["version"],
            "bundle_id": r.get("bundle_id"),
            "bundle_month": r.get("bundle_month"),
            "bundle_made_at": None if r.get("bundle_made_at") is None
            else _utc_iso(r["bundle_made_at"]),
            "freshness": r.get("freshness"),
            "freshness_reason": r.get("freshness_reason"),
            "source_table": r.get("source_table"),
        }  # fmt: skip

    def _players(
        self,
        gid: str,
        g: Json,
        home: Json,
        away: Json,
        props: dict[tuple[str, int, str], Json],
        stamps: dict[tuple[str, int, str], str],
        box_rows: list[Json],
        team_of: dict[int, tuple[Any, int]],
        report_team: dict[int, int],
    ) -> tuple[list[Json], int]:
        actual: dict[int, Json] = {int(b["player_id"]): b for b in box_rows}
        pids = sorted({k[1] for k in props if k[0] == gid})
        out: list[Json] = []
        unplaced = 0
        for pid in pids:
            earlier = team_of.get(pid)
            team = self.team_pk.get((gid, pid)) or (earlier[1] if earlier else report_team.get(pid))
            if team == home["id"]:
                side = "home"
            elif team == away["id"]:
                side = "away"
            else:
                unplaced += 1
                continue
            pprops: Json = {}
            row = actual.get(pid)
            did_play = row is not None and row["minutes"] is not None and row["minutes"] > 0
            for st in MODEL_STATS:
                r = props.get((gid, pid, st))
                if r is None:
                    continue
                pp = self.pplay.get((gid, pid))
                forecast: Json = {
                    "mean": r["mean"],
                    "q": [float(v) for v in r["q_grid"]],
                    "stamp": stamps[(gid, pid, st)],
                    "model": self._model_info(r),
                    "proj_minutes": r["proj_minutes"],
                    "p_play": None if pp is None else pp["value"],
                    "p_play_info": pp,
                    "surv": None
                    if not r["p_ge_full_counts"] or not r["p_ge_full_n"]
                    else {"n": int(r["p_ge_full_n"]), "c": [int(c) for c in r["p_ge_full_counts"]]},
                }
                quotes = self.quotes.get((gid, pid, st), [])
                posted = main_line(quotes)
                shown = self._pair(quotes, posted) if posted is not None else None
                pprops[st] = {
                    "prop_id": f"{gid}:{pid}:{st}",
                    "line": reference_line(forecast) if posted is None else posted,
                    "line_source": "reference" if posted is None else "posted",
                    "over_price": None if shown is None else shown["over"]["price"],
                    "under_price": None if shown is None else shown["under"]["price"],
                    "price_book": None if shown is None else shown["over"]["book"],
                    "line_snapshot_utc": None if shown is None else shown["over"]["snapshot_utc"],
                    "forecast": forecast,
                    "actual": row[st] if did_play and row is not None else None,
                }
            if not pprops:
                continue
            out.append(
                {
                    "player_id": pid,
                    "name": self.names.get(pid, f"Player {pid}"),
                    "side": side,
                    "result": {"minutes": float(row["minutes"]), "starter": bool(row["starter"])}
                    if did_play and row is not None
                    else None,
                    "props": pprops,
                }
            )
        out.sort(key=lambda p: (-_lead_mean(p), p["name"]))
        return out, unplaced

    @staticmethod
    def _pair(quotes: list[Json], line: float) -> dict[str, Json] | None:
        """The one book with a usable over and under at this line, best-ranked first: its pair
        is the price shown beside the prop (and what a market probability is read from)."""
        by_book: dict[str, dict[str, Json]] = defaultdict(dict)
        for q in quotes:
            if q["line"] == line and not q["unusable"]:
                by_book[q["book"]][q["side"]] = q
        both = sorted((b for b, s in by_book.items() if len(s) == 2), key=_book_rank)
        return by_book[both[0]] if both else None

    def _history(self, games: dict[str, Json], played: list[Json], report: list[Json]) -> History:
        by_game: dict[str, list[Json]] = defaultdict(list)
        for b in played:
            by_game[b["game_id"]].append(b)
        hist_games: dict[str, list[Any]] = {}
        unknown_home: list[str] = []
        box: dict[str, list[list[Any]]] = {}
        for gid, rows in by_game.items():
            first = rows[0]
            g = games.get(gid)
            if g is not None:
                home, away = int(g["home_team"]), int(g["away_team"])
            else:
                teams = sorted({int(r["team_id"]) for r in rows})
                if len(teams) != 2:
                    continue  # one side missing: the opponent cannot be named, so skip the game
                home, away = teams  # the side is a guess; flagged below so no split uses it
                unknown_home.append(gid)
            hist_games[gid] = [first["game_date"].isoformat(), int(first["season"]), home, away]
            box[gid] = [
                [int(r["player_id"]), int(r["team_id"]), float(r["minutes"]), int(r["pts"]),
                 int(r["reb"]), int(r["ast"]), int(r["fg3m"]), r.get("stl"), r.get("blk"),
                 1 if r["starter"] else 0]
                for r in rows
            ]  # fmt: skip
        for gid, g in games.items():  # upcoming and in-window games too, for the schedule
            hist_games.setdefault(
                gid,
                [
                    g["game_date"].isoformat(),
                    int(g["season"]),
                    int(g["home_team"]),
                    int(g["away_team"]),
                ],
            )
        availability: dict[str, list[list[Any]]] = defaultdict(list)
        for r in report:
            availability[r["game_id"]].append(
                [
                    int(r["player_id"]),
                    r["status"],
                    r["reason"],
                    _et_to_utc_iso(r["report_as_of_et"]),
                ]
            )
        seasons = [int(v[1]) for v in hist_games.values()] or [0]
        prices: dict[str, dict[str, dict[str, list[Json]]]] = {}
        quotes: dict[str, dict[str, dict[str, list[Json]]]] = {}
        for (gid, pid, st), qs in self.quotes.items():
            prices.setdefault(gid, {}).setdefault(str(pid), {})[st] = line_entries(
                qs, main_line(qs)
            )
            quotes.setdefault(gid, {}).setdefault(str(pid), {})[st] = qs
        return History(
            {
                "window": {
                    "from_season": min(seasons),
                    "to_date": max((v[0] for v in hist_games.values()), default=""),
                    "max_season": max(seasons),
                },
                "home_unknown": unknown_home,
                "stats": list(STAT_ORDER if self.has_stl_blk else EXPORT_STATS),
                "market": self.has_lines,
                "box": box,
                "games": hist_games,
                "teams": {str(t["id"]): t["abbr"] for t in self.teams.values()},
                "players": {str(p): {"name": n, "pos": None} for p, n in self.names.items()},
                "availability": dict(availability),
                "prices": prices,
                "quotes": quotes,
            }
        )

    # ------------------------------------------------------------------ parlay

    def _parlay(self) -> Json:
        p = json.loads((self.root / PARLAY_NAME).read_text(encoding="utf-8"))
        status, verdict = p.get("status"), p.get("verdict")
        n, npos = int(p.get("n_evaluated", 0)), int(p.get("n_positive", 0))
        base: Json = {
            "as_of": self.made_at,
            "report_date": p.get("date"),
            "source": f"{p.get('source')} (passed through the export untouched, not recomputed)",
            "engine": "nba.parlay shadow engine",
            "counts": {"n_contracts": n, "n_flagged_positive": npos},
            "gate": None,
            "model_weight": None,
            "model_weight_note": "",
            "fee_model": None,
            "price_basis": "",
            "historical_note": "",
            "disclaimer": (
                "Analysis only, not financial advice. Nothing here places orders, handles "
                "credentials, or recommends real-money bets."
            ),
        }
        if status == "evaluated" and verdict == POSITIVE_EV:
            return base | {
                "state": "A",
                "title": "The engine flagged a ticket",
                "headline_verdict": POSITIVE_EV,
                "verdict_line": (
                    f"{npos} of {n} priced contracts cleared the engine's conservative bound. "
                    "Paper only; the track record behind it is not part of this export."
                ),
                "reason": f"{npos} of {n} contracts the engine priced were flagged.",
            }
        if status == "evaluated":
            return base | {
                "state": "C",
                "title": "No parlay today",
                "headline_verdict": NO_POSITIVE_EV,
                "verdict_line": "Keep your money.",
                "reason": (
                    f"The engine priced {n} contracts for this date and none cleared its "
                    "conservative bound."
                ),
            }
        label = "unreadable" if status == "unreadable" else "not_run"
        return base | {
            "state": "C",
            "title": "No parlay check yet",
            "headline_verdict": label,
            "verdict_line": "The engine has no answer for this date, which is not a verdict. "
            "Keep your money.",
            "reason": (
                "The parlay evaluation file for this date is missing."
                if label == "not_run"
                else "The parlay evaluation file for this date could not be read."
            ),
        }

    # ------------------------------------------------------------------ surface shared with Store

    def slate(self, date: str) -> Json | None:
        return self.slates.get(date)

    def game(self, game_id: str) -> Json | None:
        return self.games.get(game_id)

    def clock(self, game_id: str) -> str:  # noqa: ARG002 - same signature as the fixture Store
        return self._now().astimezone(UTC).isoformat(timespec="seconds")

    def slate_clock(self, date: str) -> str:  # noqa: ARG002
        return self._now().astimezone(UTC).isoformat(timespec="seconds")

    def _line_stubs(self) -> list[str]:
        if not self.has_lines:
            status = (self.manifest.get("lines") or {}).get("status", "not in this schema")
            return [
                "This export carries no sportsbook lines or prices "
                f"(lines: {status}). The line shown on a prop is the middle of the model's own "
                "forecast, a place to start, not a market line."
            ]
        return [
            "Lines and prices are the quotes our capture stored before tip, from sportsbooks and "
            "exchanges (Kalshi, Polymarket, Novig). The line beside a prop is the one most books "
            "carry. Exchange prices are not sportsbook odds, and a quote flagged not usable is "
            "never shown as a price. Quotes exist only for points, rebounds, assists and threes.",
        ]

    def _model_stubs(self) -> list[str]:
        if not self.best_model:
            return [
                "This export does not record which model each number came from; every prop is "
                "the production model's forecast and every chance to play is the one stored "
                "with it."
            ]
        counts = (self.manifest.get("primary_sources") or {}) or {}
        said = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        return [
            "Each prop shows the best-evidenced model: AVG when a fresh pre-tip AVG forecast "
            "exists, the production model when AVG is missing or stale and in the postseason. "
            "Every number names its model. Chance to play comes from the availability model R, "
            "else production." + (f" Props by source in this export: {said}." if said else "")
        ]

    def _stat_stubs(self) -> list[str]:
        stubs = ["Player positions are not exported, so the opponent-by-position table is absent."]
        if not self.has_stl_blk:
            stubs.append("No steals or blocks are exported, so those legs are absent.")
        return stubs

    def meta(self) -> Json:
        m = self.manifest
        return {
            "mode": self.mode,
            "mode_note": (
                "Forecasts as the daily run stored them before tip. Nothing here is advice."
            ),
            "data_version": self.data_version,
            "built_at": self.made_at,
            "data_as_of": self.made_at,
            "export_date": m.get("export_date"),
            "max_season": max(int(s) for s in m["seasons"]),
            "slates": list(self.slates),
            "stubs": [
                *self._model_stubs(),
                *self._line_stubs(),
                *self._stat_stubs(),
                "Home or away is known only for games inside the export's lookback window "
                f"({m.get('lookback_days')} days); older games are left out of home/away splits.",
                f"{self.unplaced_total} forecast players could not be placed on a team "
                "(no earlier box score or injury-report line) and are not listed.",
            ],
            "sources": {
                "export.duckdb": f"nightly export, sha256 verified; schema {m['schema_version']}",
                "manifest.json": f"built {self.made_at}, git {str(m.get('git_sha'))[:12]}",
            },
            "queries": {},
            "export": {
                "row_counts": m.get("row_counts"),
                "excluded": m.get("excluded"),
                "seasons": m["seasons"],
            },
        }
