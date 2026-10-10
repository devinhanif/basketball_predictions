"""Section "What was known, and when": the first reader of the as-of fact store (nba/facts).

Reads ``data/facts/facts.duckdb`` read-only through ``nba.facts.query``. Never refits, never
writes, and never fails the page: any problem becomes one plain sentence.
"""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.explain.names import player_name, team_abbr
from nba.facts.query import as_of, game_tip, known_before_tip

CENTRAL = ZoneInfo("America/Chicago")
DEFAULT_FACTS_DB = "data/facts/facts.duckdb"
CUTOFFS = (60, 30)


def central(ts_utc: dt.datetime) -> str:
    """Naive UTC -> '6:30 PM CDT, Oct 22 (US Central)' with the zone named."""
    t = ts_utc.replace(tzinfo=dt.UTC).astimezone(CENTRAL)
    return t.strftime("%-I:%M %p %Z, %b %-d") + " (US Central)"


@dataclass
class Listed:
    player_id: int
    name: str
    status: str
    known_at: dt.datetime


@dataclass
class Starter:
    player_id: int
    name: str
    role: str  # Expected | Confirmed


@dataclass
class TeamView:
    team_id: int
    abbr: str
    listed: list[Listed] = field(default_factory=list)
    starters: list[Starter] = field(default_factory=list)


@dataclass
class Cutoff:
    minutes: int
    at_utc: dt.datetime
    teams: list[TeamView]
    snapshot_utc: dt.datetime | None  # latest starters snapshot known by the cutoff


@dataclass
class Known:
    message: str | None = None  # set when the section cannot be shown
    tip_utc: dt.datetime | None = None
    cutoffs: list[Cutoff] = field(default_factory=list)
    post_tip_excluded: int = 0


def _pid(subject: str) -> int:
    return int(subject.split(":", 1)[1])


def _latest_team(con: Any, pid: int, before: dt.datetime) -> int | None:
    r = con.execute(
        "SELECT arg_max(team_id, g.game_date) FROM player_game_stats p "
        "JOIN games g USING (game_id) WHERE p.player_id = ? AND g.game_date <= ?",
        [pid, before.date()],
    ).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def _views(
    facts: pl.DataFrame, home: int, away: int, team_of: dict[int, int | None]
) -> tuple[list[TeamView], dt.datetime | None]:
    views = {home: TeamView(home, team_abbr(home)), away: TeamView(away, team_abbr(away))}
    unplaced = TeamView(0, "team not recorded")
    ls = facts.filter(pl.col("predicate") == "listed_status").sort("known_at", "fact_id")
    latest: dict[int, Listed] = {}
    for r in ls.iter_rows(named=True):
        pid = _pid(r["subject"])
        latest[pid] = Listed(pid, player_name(pid), str(r["value_text"]), r["known_at"])
    for ln in sorted(latest.values(), key=lambda x: x.name):
        (views.get(team_of.get(ln.player_id) or 0) or unplaced).listed.append(ln)
    st = facts.filter(pl.col("predicate") == "announced_starter").sort("known_at", "fact_id")
    snap: dt.datetime | None = None
    if st.height:
        snap = dt.datetime.fromisoformat(str(st["known_at"].max()))
        last = st.filter(pl.col("known_at") == snap)
        for r in last.iter_rows(named=True):
            if r["value"] == 1:
                pid = _pid(r["subject"])
                s = Starter(pid, player_name(pid), str(r["value_text"]))
                (views.get(team_of.get(pid) or 0) or unplaced).starters.append(s)
    out = [views[home], views[away]]
    if unplaced.listed or unplaced.starters:
        out.append(unplaced)
    return out, snap


def load_known(
    facts_db: str | Path | None, main_con: Any, game_id: str, home: int, away: int
) -> Known:
    """Never raises: a missing DB, an unknown game or a read error becomes a sentence."""
    if facts_db is None or not Path(facts_db).exists():
        return Known(message="The fact store was not found, so there is no timeline for this game.")
    try:
        con = duckdb.connect(str(facts_db), read_only=True)
    except Exception:
        return Known(message="The fact store could not be opened, so there is no timeline.")
    try:
        tip = game_tip(con, game_id)
        if tip is None:
            return Known(
                message="This game is not in the fact store (it holds seasons 2024 and earlier), "
                "so there is no timeline for it."
            )
        cutoffs: list[Cutoff] = []
        for m in CUTOFFS:
            facts = known_before_tip(con, game_id, m)
            pids = {
                _pid(s)
                for s in facts.filter(
                    pl.col("predicate").is_in(["listed_status", "announced_starter"])
                )["subject"]
            }
            team_of = {p: _latest_team(main_con, p, tip) for p in pids}
            teams, snap = _views(facts, home, away, team_of)
            cutoffs.append(Cutoff(m, tip - dt.timedelta(minutes=m), teams, snap))
        far = tip + dt.timedelta(days=3650)
        excluded = (
            as_of(con, game_id, far, allow_post_tip=True).height - as_of(con, game_id, tip).height
        )
        return Known(tip_utc=tip, cutoffs=cutoffs, post_tip_excluded=excluded)
    except Exception:
        return Known(
            message="The fact store could not be read for this game, so there is no timeline."
        )
    finally:
        con.close()


def _e(x: object) -> str:
    return html.escape(str(x), quote=True)


def _team_block(t: TeamView, c: Cutoff) -> str:
    out = [f"<h4>{_e(t.abbr)}</h4>"]
    if t.listed:
        out.append("<ul>")
        for ln in t.listed:
            out.append(
                f"<li>{_e(ln.name)}: {_e(ln.status)} "
                f'<span class="src">(report published {_e(central(ln.known_at))})</span></li>'
            )
        out.append("</ul>")
    else:
        out.append("<p>Nobody from this team was on the official report yet.</p>")
    if c.minutes == 30:
        if t.starters and c.snapshot_utc is not None:
            names = ", ".join(f"{s.name} ({s.role.lower()})" for s in t.starters)
            out.append(
                f"<p>Announced starters, snapshot taken {_e(central(c.snapshot_utc))}: "
                f"{_e(names)}.</p>"
            )
        else:
            out.append("<p>No starters had been announced for this team yet.</p>")
    return "".join(out)


def section_known(k: Known) -> str:
    out = ['<section id="known"><h2>What was known, and when</h2>']
    if k.message is not None or k.tip_utc is None:
        out.append(f"<p>{_e(k.message or 'No timeline is available.')}</p></section>")
        return "".join(out)
    out.append(
        "<p>Before a game, the league publishes an injury report and, closer to tip-off, the "
        "teams announce who starts. This is what we could have known at 60 and at 30 minutes "
        "before the game began, and when each piece of it appeared.</p>"
        f"<p><b>Tip-off:</b> {_e(central(k.tip_utc))}.</p>"
    )
    for c in k.cutoffs:
        out.append(
            f"<h3>{c.minutes} minutes before tip ({_e(central(c.at_utc))})</h3>"
            + "".join(_team_block(t, c) for t in c.teams)
        )
    out.append(
        f'<p class="src">Facts known after tip are not shown: {k.post_tip_excluded}.</p></section>'
    )
    return "".join(out)
