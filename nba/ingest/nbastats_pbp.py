"""Adapter: stats.nba.com-format play-by-play (shufinskiy/nba_data ``nbastats``) -> our V3-style
schema.

The downloaded files (Apache-2.0, see data/raw/nba_data_shufinskiy_2026-10-12/LICENSE) carry the
PlayByPlayV2 layout (``EVENTMSGTYPE``, ``HOMEDESCRIPTION`` ...). Our parser (``nba/parse``) consumes
the PlayByPlayV3 layout normalised in ``nba/ingest/pbp.py``. This module is the column/value
mapping between the two, nothing more: the possession and lineup parsers are reused unchanged.

Validated on the 2019-20 and 2020-21 overlap against play-by-play we fetched ourselves
(docs/reviews/history_2015_2019_ingest_2026-10-12.md): the same events carry the same ids, periods,
teams and players; the mapping is exercised by ``tests/ingest/test_nbastats_pbp.py``.

Mapping facts (all measured on the overlap, not assumed):

* ``action_number`` is ``EVENTNUM``; the V3 feed carries extra STEAL/BLOCK rows under the same
  number
  (blank ``action_type``). They are skipped by the parser and are not generated here.
* ``sub_type`` is a pure function of ``(EVENTMSGTYPE, EVENTMSGACTIONTYPE)`` on all 1.08M overlap
  rows;
  the table is ``nbastats_subtypes.csv``. Unseen pairs get an empty sub_type and are counted.
* ``team_id``/``player_id`` come from ``PLAYER1_*``; a team event (team rebound, timeout) has no
  team
  id and parks the team id in ``player_id``, exactly like V3.
* Substitution text is ``SUB: <in> FOR <out>``; the *out* player id is ``PLAYER1_ID`` (exact).
  Where two
  teammates in a game share a surname the text gets the feed's own disambiguation (``"A. Holiday"``,
  ``"Co. Martin"``) so the lineup tracker's name ladder works as it does on V3 data.
* Clocks are whole seconds (V3 carries tenths late in periods); that is the only precision
  difference.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl

from nba.ingest.pbp import _PBP_SCHEMA

SUBTYPE_CSV = Path(__file__).with_name("nbastats_subtypes.csv")

ACTION_TYPE = {
    1: "Made Shot",
    2: "Missed Shot",
    3: "Free Throw",
    4: "Rebound",
    5: "Turnover",
    6: "Foul",
    7: "Violation",
    8: "Substitution",
    9: "Timeout",
    10: "Jump Ball",
    11: "Ejection",
    12: "period",
    13: "period",
    18: "Instant Replay",
}

RAW_COLUMNS = [
    "GAME_ID",
    "EVENTNUM",
    "EVENTMSGTYPE",
    "EVENTMSGACTIONTYPE",
    "PERIOD",
    "PCTIMESTRING",
    "HOMEDESCRIPTION",
    "NEUTRALDESCRIPTION",
    "VISITORDESCRIPTION",
    "SCORE",
    "PERSON1TYPE",
    "PLAYER1_ID",
    "PLAYER1_NAME",
    "PLAYER1_TEAM_ID",
    "PLAYER2_ID",
    "PLAYER2_NAME",
    "PLAYER2_TEAM_ID",
    "PLAYER3_ID",
    "PLAYER3_TEAM_ID",
]

_SUB_RE = re.compile(r"^SUB:\s*(.+?)\s+FOR\s+(.+)$")
_DIST_RE = re.compile(r"(\d+)'")
_PTS_RE = re.compile(r"\((\d+) PTS\)")
_SUFFIXES = {"jr.", "sr.", "ii", "iii", "iv", "v"}
_SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_CLOCK_RE = re.compile(r"^(\d+):(\d+)")


def load_subtype_map(path: Path = SUBTYPE_CSV) -> dict[tuple[int, int], str]:
    with path.open(newline="") as f:
        return {
            (int(r["event_msg_type"]), int(r["event_msg_action_type"])): r["sub_type"]
            for r in csv.DictReader(f)
        }


def read_nbastats(paths: Iterable[Path]) -> pl.DataFrame:
    """Read nbastats CSVs as strings, keep the mapped columns, drop exact duplicate rows.

    The source contains exact duplicate rows (132 in 2019-21); a duplicated key with differing
    content would be a real conflict and is checked by the caller (``duplicate_keys``).
    """
    frames = [
        pl.read_csv(p, infer_schema_length=0, columns=RAW_COLUMNS, null_values=["", "NaN"])
        for p in paths
    ]
    return pl.concat(frames).unique(maintain_order=True)


def duplicate_keys(df: pl.DataFrame) -> int:
    """Number of (GAME_ID, EVENTNUM) keys carried by more than one distinct row."""
    return int(df.group_by(["GAME_ID", "EVENTNUM"]).len().filter(pl.col("len") > 1).height)


def game_id10(raw_id: str) -> str:
    return f"{int(raw_id):010d}"


def _int(v: Any) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def _season_start(game_id: str) -> int:
    return 2000 + int(game_id[3:5]) if int(game_id[3:5]) < 90 else 1900 + int(game_id[3:5])


def _surnames(rows: list[dict[str, Any]]) -> dict[int, tuple[str, str]]:
    """player_id -> (surname text the feed uses, first name), learned from this game's text.

    The longest proper suffix of the full name that occurs in an event description is the surname
    ("Porter Jr.", "Alexander-Walker"); substitution text gives the exact in/out pairing.
    """
    out: dict[int, tuple[str, str]] = {}
    for r in rows:
        for pid_key, name_key, side in (
            ("PLAYER1_ID", "PLAYER1_NAME", "p1"),
            ("PLAYER2_ID", "PLAYER2_NAME", "p2"),
        ):
            pid = _int(r[pid_key])
            name = r[name_key]
            if not pid or not name or pid in out:
                continue
            toks = name.split()
            desc = " ".join(
                x
                for x in (r["HOMEDESCRIPTION"], r["VISITORDESCRIPTION"], r["NEUTRALDESCRIPTION"])
                if x
            )
            if _int(r["EVENTMSGTYPE"]) == 8:
                m = _SUB_RE.match((r["HOMEDESCRIPTION"] or r["VISITORDESCRIPTION"] or "").strip())
                if m:
                    text = m.group(1 if side == "p2" else 2).strip()
                    out[pid] = (text, toks[0] if len(toks) > 1 else "")
                    continue
            if side != "p1" or _int(r["PERSON1TYPE"]) not in (4, 5):
                continue
            for k in range(1, len(toks)):
                cand = " ".join(toks[k:])
                if cand and re.search(r"(?<![\w.])" + re.escape(cand) + r"(?![\w])", desc):
                    out[pid] = (cand, toks[0])
                    break
            else:
                if toks and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", desc):
                    out[pid] = (name, "")
    return out


def _initials(players: list[tuple[int, str]]) -> dict[int, str]:
    """Shortest first-name prefix (1-3 letters) separating same-surname teammates."""
    res: dict[int, str] = {}
    for n in (1, 2, 3):
        pref = {pid: (first[:n].capitalize() if first else "") for pid, first in players}
        if len(set(pref.values())) == len(pref) or n == 3:
            res = {pid: (p + "." if p else "") for pid, p in pref.items()}
            break
    return res


def infer_home_team(rows: list[dict[str, Any]]) -> int:
    """Home team id: the team whose actor rows carry the home description column most often."""
    votes: dict[int, int] = {}
    for r in rows:
        tid = _int(r["PLAYER1_TEAM_ID"])
        if not tid:
            continue
        h, v = bool(r["HOMEDESCRIPTION"]), bool(r["VISITORDESCRIPTION"])
        if h != v:
            votes[tid] = votes.get(tid, 0) + (1 if h else -1)
    return max(votes, key=lambda t: votes[t]) if votes else 0


def _fallback_surname(r: dict[str, Any]) -> str:
    """Surname for a player never named in this game's text: last token (two if a suffix)."""
    toks = (r["PLAYER1_NAME"] or "").split()
    if len(toks) >= 3 and toks[-1].lower() in _SUFFIXES:
        return " ".join(toks[-2:])
    return toks[-1] if toks else ""


def to_v3(
    game: pl.DataFrame,
    subtypes: dict[tuple[int, int], str],
    stats: dict[str, int] | None = None,
    shot_distance_by_event: dict[int, int] | None = None,
) -> pl.DataFrame:
    """Map one game's nbastats rows to ``_PBP_SCHEMA``. ``stats`` accumulates unmapped counts.

    ``shot_distance_by_event`` (EVENTNUM -> feet, from the shotdetail file) fills ``shot_distance``
    when the text carries none: 2015-17 descriptions often omit it ("Schroder  Driving Layup").
    """
    st = stats if stats is not None else {}
    rows = game.to_dicts()
    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in _PBP_SCHEMA})
    gid = game_id10(rows[0]["GAME_ID"])
    names = _surnames(rows)

    home_team = infer_home_team(rows)

    # same-surname teammates -> feed-style initials in substitution text
    by_team: dict[int, dict[str, set[int]]] = {}
    for r in rows:
        for pk, tk in (("PLAYER1_ID", "PLAYER1_TEAM_ID"), ("PLAYER2_ID", "PLAYER2_TEAM_ID")):
            pid, tid = _int(r[pk]), _int(r[tk])
            if pid and tid and pid in names:
                by_team.setdefault(tid, {}).setdefault(names[pid][0], set()).add(pid)
    sub_text: dict[int, str] = {pid: sur for pid, (sur, _) in names.items()}
    for groups in by_team.values():
        for sur, pids in groups.items():
            if len(pids) > 1:
                ini = _initials([(p, names[p][1]) for p in sorted(pids)])
                for p in pids:
                    sub_text[p] = f"{ini[p]} {sur}".strip()

    out: list[dict[str, Any]] = []
    last_score = ("", "")
    for r in rows:
        et, ea = _int(r["EVENTMSGTYPE"]), _int(r["EVENTMSGACTIONTYPE"])
        action = ACTION_TYPE.get(et)
        if action is None:
            st["unknown_event_type"] = st.get("unknown_event_type", 0) + 1
            action = ""
        if et == 12:
            sub_type = "start"
        elif et == 13:
            sub_type = "end"
        elif action:
            known = subtypes.get((et, ea))
            if known is None:
                st["unmapped_subtype"] = st.get("unmapped_subtype", 0) + 1
            sub_type = known or ""
        else:
            sub_type = ""
        pid, tid = _int(r["PLAYER1_ID"]), _int(r["PLAYER1_TEAM_ID"])
        hd, vd, nd = r["HOMEDESCRIPTION"], r["VISITORDESCRIPTION"], r["NEUTRALDESCRIPTION"]
        side = "h" if tid == home_team else "v"
        location = side if tid else ("h" if hd else ("v" if vd else ""))
        if tid:
            desc = (hd if tid == home_team else vd) or hd or vd or nd or ""
        else:
            desc = hd or vd or nd or ""
        if et == 8:
            m = _SUB_RE.match(desc.strip())
            p_in, p_out = _int(r["PLAYER2_ID"]), pid
            if m and p_in and p_out:
                desc = (
                    f"SUB: {sub_text.get(p_in, m.group(1))} FOR {sub_text.get(p_out, m.group(2))}"
                )
        sm = _SCORE_RE.match(r["SCORE"] or "")
        if sm:
            last_score = (sm.group(2), sm.group(1))  # SCORE is "away - home"
            score_home, score_away = last_score
        elif et in (12, 13, 18):
            score_home, score_away = last_score if last_score != ("", "") else ("0", "0")
        else:
            score_home, score_away = "", ""
        pm = _PTS_RE.search(desc)
        if et in (12, 13, 18):
            points_total = int(score_home or 0) + int(score_away or 0)
        else:
            points_total = int(pm.group(1)) if pm else 0
        is_fg = 1 if et in (1, 2) else 0
        shot_value = 0
        shot_distance = 0
        if is_fg:
            shot_value = 3 if "3PT" in desc else 2
            dm = _DIST_RE.search(desc)
            if dm:
                shot_distance = int(dm.group(1))
            elif shot_distance_by_event and _int(r["EVENTNUM"]) in shot_distance_by_event:
                shot_distance = shot_distance_by_event[_int(r["EVENTNUM"])]
                st["distance_from_shotdetail"] = st.get("distance_from_shotdetail", 0) + 1
            else:
                st["distance_missing"] = st.get("distance_missing", 0) + 1
        cm = _CLOCK_RE.match(r["PCTIMESTRING"] or "")
        clock = f"PT{int(cm.group(1)):02d}M{int(cm.group(2)):02d}.00S" if cm else ""
        out.append(
            {
                "game_id": gid,
                "action_number": _int(r["EVENTNUM"]),
                "period": _int(r["PERIOD"]),
                "clock": clock,
                "team_id": tid,
                "player_id": pid,
                "player_name": (names[pid][0] if pid in names else _fallback_surname(r))
                if tid
                else "",
                "action_type": action,
                "sub_type": sub_type,
                "description": desc,
                "score_home": score_home,
                "score_away": score_away,
                "points_total": points_total,
                "shot_result": "Made" if et == 1 else ("Missed" if et == 2 else ""),
                "shot_value": shot_value,
                "shot_distance": shot_distance,
                "is_field_goal": is_fg,
                "location": location,
            }
        )
    return pl.DataFrame(
        out, schema_overrides={"score_home": pl.Utf8, "score_away": pl.Utf8}
    ).select(_PBP_SCHEMA)
