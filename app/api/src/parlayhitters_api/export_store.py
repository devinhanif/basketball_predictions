"""The nightly export as a read-only store.

Reads the directory `python -m nba.export build` writes: ``export.duckdb``, ``parlay_of_day.json``
and ``manifest.json``. Nothing here imports ``nba``; the files are the whole interface.

Before a byte of the database is opened the manifest is checked:
  * it must be valid JSON with ``schema_version`` 1 or 2, a non-empty ``data_version`` and a
    ``made_at`` that parses (an export that cannot say what data it came from, or when, is refused);
  * the season 2025 holdout may not appear in its ``seasons`` (or in any table, except box-score
    rows the manifest declares as display history in ``box_history_seasons``);
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

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .export_rows import (
    PROP_LINE_COLS,
    _book_rank,
    _display_name,
    _et_to_utc_iso,
    _lead_mean,
    _pick_latest,
    _prop_lines_for,
    _quote,
    _schema_at_least_1,
    _split_name,
    _stamp_text,
    _table,
    _utc_iso,
    line_entries,
    main_line,
)
from .export_verify import (
    DB_NAME,
    HOLDOUT_SEASON,
    MANIFEST_NAME,
    PARLAY_NAME,
    ExportRefused,
    has_export,
    manifest_path,
    sha256_file,
    verify,
)
from .forecast import reference_line
from .history import STAT_ORDER, History

Json = dict[str, Any]
log = logging.getLogger("parlayhitters")

MODEL_STATS = ("pts", "reb", "ast", "fg3m")
EXPORT_STATS = ("pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra")
#: Only used for an export that predates best_model_schema (no is_primary column): the old rule.
PREFERRED_PROP_MODEL = "props_context_residual"
NO_POSITIVE_EV = "no_positive_ev_found"
POSITIVE_EV = "positive_ev_at_conservative_bound"


class ExportStore:
    """Same surface as the fixture Store, backed by an export directory."""

    mode = "export"

    def __init__(self, directory: Path, now: Callable[[], datetime] | None = None) -> None:
        self.root = Path(directory)
        self._now = now or (lambda: datetime.now(UTC))
        self.manifest: Json = verify(self.root)
        try:
            con = duckdb.connect(str(self.root / DB_NAME), read_only=True)
        except duckdb.Error as exc:
            # The checksum matched, so the file is what the exporter wrote, but DuckDB cannot open
            # it. Refused (503), not a crash; the reason names the error kind, never a path.
            log.warning("export.duckdb cannot be opened: %s", exc)
            raise ExportRefused(f"export.duckdb cannot be opened ({type(exc).__name__})") from exc
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
            # optional: an export built before the 'if a teammate sits' step has no such table
            tables["teammate_sits"] = (
                _table(con, "teammate_sits") if "teammate_sits" in have else []
            )
            meta = {r["key"]: r["value"] for r in tables["meta"]}
            # best_model_schema 1: prop_grids marks one primary row per prop and p_play carries
            # the chance to play. An export without it keeps the old production-model choice.
            grids = tables["prop_grids"]
            self.best_model: bool = _schema_at_least_1(meta.get("best_model_schema")) and (
                not grids or "is_primary" in grids[0]
            )
            props = self._latest_props(grids)
            if "prop_lines" in have:
                tables["prop_lines"] = _prop_lines_for(con, props)
            elif self.manifest["schema_version"] >= 2:
                raise ExportRefused("a schema 2 export has no prop_lines table")
            else:
                tables["prop_lines"] = []
        except duckdb.Error as exc:  # a table or column the app needs is missing or unreadable
            log.warning("export.duckdb could not be read: %s", exc)
            raise ExportRefused(f"export.duckdb could not be read ({type(exc).__name__})") from exc
        finally:
            con.close()
        if meta.get("made_at") != self.manifest["made_at"] or (
            meta.get("data_version", "") != (self.manifest.get("data_version") or "")
        ):
            raise ExportRefused("export.duckdb meta does not match the manifest")
        if any(int(r["season"]) == HOLDOUT_SEASON for r in tables["games"]):
            raise ExportRefused(f"games holds season {HOLDOUT_SEASON}, the frozen holdout")
        # Box scores are raw game logs shown for display (last-10, swipe stats). Season 2025 rows
        # are let in only when the export declares them as display history (nba.export guard
        # DISPLAY_TABLES; Devin's display approval in HOLDOUT_ACCESS_LOG); nothing here scores them.
        display = self.manifest.get("box_history_seasons")
        if HOLDOUT_SEASON not in (display if isinstance(display, list) else []) and any(
            int(r["season"]) == HOLDOUT_SEASON for r in tables["box_scores"]
        ):
            raise ExportRefused(f"box_scores holds season {HOLDOUT_SEASON}, the frozen holdout")
        self.sits_info: Json | None = (
            self.manifest["teammate_sits"]
            if isinstance(self.manifest.get("teammate_sits"), dict)
            else None
        )
        self.sits_rows: dict[str, list[Json]] = defaultdict(list)
        for r in tables["teammate_sits"]:
            self.sits_rows[str(r["game_id"])].append(r)
        self.made_at: str = self.manifest["made_at"]
        self.data_version: str = self.manifest["data_version"]
        self._build(tables, props)
        self.parlay: Json = self._parlay()
        self.evidence: Json = {
            "available": False,
            "note": "The model-versus-market evidence tables are not part of the nightly export.",
        }

    # ------------------------------------------------------------------ building

    def _build(
        self, tables: dict[str, list[Json]], props: dict[tuple[str, int, str], Json]
    ) -> None:
        # The export sometimes stores a placeholder ("Player 1643411") where it has no name. Show it
        # as what it is, not as something that looks like a name. No name is looked up elsewhere.
        self.names: dict[int, str] = {
            int(r["player_id"]): _display_name(int(r["player_id"]), r["full_name"])
            for r in tables["players"]
        }
        self.teams: dict[int, Json] = {}
        for r in tables["teams"]:
            city, name = _split_name(str(r["full_name"]))
            self.teams[int(r["team_id"])] = {
                "id": int(r["team_id"]), "abbr": r["abbr"], "city": city, "name": name,
            }  # fmt: skip
        # A game with no home or away team cannot be shown or placed: skip it and count it, so
        # one damaged row never takes the whole store down.
        games = {
            r["game_id"]: r
            for r in tables["games"]
            if r["home_team"] is not None and r["away_team"] is not None
        }
        self.skipped_games: int = len(tables["games"]) - len(games)
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
        self.snapshots: dict[tuple[str, int, str], list[Json]] = self._snapshots(
            tables["prop_grids"], props
        )
        self.injury: dict[str, list[Json]] = self._injury(tables["injury_report"])

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

    @staticmethod
    def _snapshots(
        rows: list[Json], props: dict[tuple[str, int, str], Json]
    ) -> dict[tuple[str, int, str], list[Json]]:
        """Every stored forecast of the shown model for each shown prop, oldest first: the same
        model and version as the row on screen, so a change between two of them is news, not a
        different model. Read-only; used by the tip-off timeline."""
        shown = {k: (r["model_name"], r["version"]) for k, r in props.items()}
        out: dict[tuple[str, int, str], dict[Any, Json]] = defaultdict(dict)
        for r in rows:
            key = (r["game_id"], int(r["player_id"]), r["stat"])
            if shown.get(key) != (r["model_name"], r["version"]):
                continue
            q = r["q_grid"]
            if not (isinstance(q, list) and len(q) == 19 and all(v is not None for v in q)):
                continue
            if r["lead_minutes"] is None:
                continue
            pp = r.get("p_play_primary")
            if pp is None:
                pp = r.get("p_play")
            made = _utc_iso(r["made_at"])
            out[key][made] = {
                "made_at": made,
                "lead_minutes": float(r["lead_minutes"]),
                "mean": None if r["mean"] is None else float(r["mean"]),
                "q": [float(v) for v in q],
                "proj_minutes": None if r["proj_minutes"] is None else float(r["proj_minutes"]),
                "p_play": None if pp is None else float(pp),
                "surv": None
                if not r["p_ge_full_counts"] or not r["p_ge_full_n"]
                else {"n": int(r["p_ge_full_n"]), "c": [int(c) for c in r["p_ge_full_counts"]]},
                "run_id": r["run_id"],
                "shown": r["run_id"] == props[key]["run_id"],
            }
        return {k: sorted(v.values(), key=lambda x: str(x["made_at"])) for k, v in out.items()}

    @staticmethod
    def _injury(rows: list[Json]) -> dict[str, list[Json]]:
        """The official injury report lines by game, oldest report first, times in UTC."""
        out: dict[str, list[Json]] = defaultdict(list)
        for r in rows:
            if r["report_as_of_et"] is None or r["player_id"] is None:
                continue
            out[r["game_id"]].append(
                {
                    "at_utc": _et_to_utc_iso(r["report_as_of_et"]),
                    "player_id": int(r["player_id"]),
                    "team_id": None if r["team_id"] is None else int(r["team_id"]),
                    "status": str(r["status"]).lower(),
                    "reason": None if r["reason"] is None else str(r["reason"]),
                }
            )
        for v in out.values():
            v.sort(key=lambda x: (x["at_utc"], x["player_id"]))
        return dict(out)

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
                    "book_main_line": r.get("book_main_line"),
                    "rung_cal": r.get("rung_cal"),
                    "line_cal": r.get("line_cal"),
                    "line_cal_null_reason": r.get("line_cal_null_reason"),
                    "forecast": forecast,
                    "actual": row[st] if did_play and row is not None else None,
                }
            if not pprops:
                continue
            out.append(
                {
                    "player_id": pid,
                    "name": self.names.get(pid, f"Unnamed player (id {pid})"),
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
        try:
            p = json.loads((self.root / PARLAY_NAME).read_text(encoding="utf-8"))
            if not isinstance(p, dict):
                raise ValueError("not a JSON object")
            _ = (int(p.get("n_evaluated", 0)), int(p.get("n_positive", 0)))  # counts are integers
        except (OSError, ValueError, TypeError) as exc:
            raise ExportRefused(f"{PARLAY_NAME} is not a readable parlay file") from exc
        recs = p.get("records")
        self.parlay_records: list[Json] = recs if isinstance(recs, list) else []
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
                *(
                    [
                        f"{self.skipped_games} game(s) in the export have no home or away team "
                        "and are not shown."
                    ]
                    if self.skipped_games
                    else []
                ),
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


#: Imported from here by store.py and the tests; their homes are export_verify.py and
#: export_rows.py since the split.
__all__ = [
    "DB_NAME",
    "ExportRefused",
    "HOLDOUT_SEASON",
    "MANIFEST_NAME",
    "PARLAY_NAME",
    "PROP_LINE_COLS",
    "_display_name",
    "_table",
    "has_export",
    "line_entries",
    "main_line",
    "manifest_path",
    "sha256_file",
    "verify",
    "ExportStore",
]
