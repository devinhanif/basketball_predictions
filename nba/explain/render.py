"""HTML for one game. Inline CSS, inline SVG, no JavaScript, no external assets.

Every dynamic string goes through :func:`esc`. Display rules (DECISIONS 2026-10-09): a miss is
drawn exactly like a hit (same size, same type, shape plus a word, never color alone); the
market's price sits beside ours with no stakes and no sizing; the page says what the market
priced and what we said, and nothing more.
"""

from __future__ import annotations

import html
from datetime import datetime

from nba.explain import data as D
from nba.explain.known import section_known
from nba.explain.names import team_abbr, team_name
from nba.explain.page import Call, Page, PlayerRow, ScoreRow, StatCell, WinInfo

STAT_LABEL = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "fg3m": "Threes made"}
GLYPH = {"inside": "●", "above": "▲", "below": "▼"}
GLYPH_WORD = {"inside": "inside", "above": "above", "below": "below"}

CSS = """
:root{--ink:#1d2330;--mute:#5d667a;--line:#dfe3ec;--bg:#f6f7fa;--card:#fff;--ours:#2458c8;
--mkt:#c25b00;--in:#1a7f6d;--above:#b3541e;--below:#6b3fa0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,system-ui,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:12px 14px 40px}
h1{font-size:1.45rem;line-height:1.2;margin:8px 0 2px}
h2{font-size:1.1rem;margin:0 0 8px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;
margin:12px 0}
.sub{color:var(--mute);margin:0 0 6px}
.score{font-size:1.25rem;font-weight:650;margin:2px 0 8px}
.note{color:var(--mute);font-size:.88rem;margin:6px 0}
.catch{border-left:3px solid var(--line);padding-left:10px;color:var(--mute);font-size:.92rem}
.bars{margin:8px 0}
.bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:.95rem}
.bar .lab{flex:0 0 7.4em;color:var(--mute)}
.bar svg{flex:1 1 auto;min-width:0;height:16px}
.bar .val{flex:0 0 3.2em;text-align:right;font-weight:650}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.pl{display:grid;grid-template-columns:1fr auto;gap:2px 10px;padding:9px 0;
border-bottom:1px solid var(--line)}
.pl:last-child{border-bottom:0}
.pl .who{font-weight:650}
.tag{font-weight:500;color:var(--mute);font-size:.85rem;margin-left:4px}
.pl .meta,.pl .mk{color:var(--mute);font-size:.88rem}
.pl .res{grid-column:2;grid-row:1 / span 3;text-align:right;min-width:4.6em}
.res .big{font-size:1.5rem;font-weight:650;line-height:1.1}
.g{display:inline-block;font-size:.85rem;font-weight:600;white-space:nowrap}
.g.inside{color:var(--in)}.g.above{color:var(--above)}.g.below{color:var(--below)}
.fan{display:block;margin:3px 0;max-width:100%}
details{grid-column:1 / -1;margin-top:4px}
summary{cursor:pointer;color:var(--ours);font-size:.9rem}
.sub-stat{display:grid;grid-template-columns:1fr auto;gap:0 10px;padding:6px 0 6px 8px;
border-top:1px dashed var(--line)}
.legend{font-size:.85rem;color:var(--mute);margin:6px 0}
.taught{font-size:1.02rem}
.src{color:var(--mute);font-size:.82rem}
code{font-size:.85em;background:#eef0f5;padding:1px 4px;border-radius:4px}
@media (prefers-color-scheme:dark){
:root{--ink:#e8ebf2;--mute:#9aa3b8;--line:#2d3446;--bg:#12151d;--card:#1a1f2b;--ours:#7aa2ff;
--mkt:#ffa14a;--in:#4ecbb3;--above:#ff9a6b;--below:#c19bf2}code{background:#262d3d}}
"""


def esc(x: object) -> str:
    return html.escape(str(x), quote=True)


def pct(p: float | None) -> str:
    """Whole-percent string; never 0% or 100% for a probability that is not exactly 0 or 1."""
    if p is None:
        return "n/a"
    v = 100.0 * p
    if 0 < v < 1:
        return "<1%"
    if 99 < v < 100:
        return ">99%"
    return f"{round(v)}%"


def fmt1(x: float) -> str:
    return f"{x:.1f}"


def signed(x: float, nd: int = 2) -> str:
    return f"{x:+.{nd}f}"


def et_clock(ts_utc: datetime) -> str:
    t = D.utc_to_et(ts_utc)
    return t.strftime("%-I:%M %p ET")


def et_long(ts_utc: datetime) -> str:
    t = D.utc_to_et(ts_utc)
    return t.strftime("%A, %B %-d, %Y, %-I:%M %p ET")


def glyph(status: str | None) -> str:
    """The marker for an outcome against the 80% interval: shape plus a word, never color alone."""
    if status is None:
        return '<span class="g">did not play</span>'
    return f'<span class="g {status}">{GLYPH[status]} {GLYPH_WORD[status]}</span>'


# ------------------------------------------------------------------------------------ svg


def prob_bar(p: float, colour: str, label: str) -> str:
    w = max(0.0, min(1.0, p)) * 100.0
    return (
        '<svg viewBox="0 0 100 16" preserveAspectRatio="none" role="img" '
        f'aria-label="{esc(label)}">'
        '<rect x="0" y="4" width="100" height="8" rx="2" fill="currentColor" opacity=".13"/>'
        f'<rect x="0" y="4" width="{w:.2f}" height="8" rx="2" fill="{colour}"/>'
        '<line x1="50" y1="1" x2="50" y2="15" stroke="currentColor" opacity=".35" '
        'stroke-width=".6"/></svg>'
    )


def fan_svg(c: StatCell) -> str:
    """One row: the stored 80% interval as a band, the median as a tick, the market line as a
    dashed rule, the actual as a marker (shape differs by where it fell)."""
    pts = [c.lo, c.hi, c.median]
    if c.actual is not None:
        pts.append(float(c.actual))
    if c.line is not None:
        pts.append(c.line.line)
    top = max(pts) * 1.06 + 1.0
    W, H = 220.0, 22.0

    def x(v: float) -> float:
        return 4.0 + (W - 8.0) * v / top

    parts = [
        f'<svg class="fan" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
        'role="img" aria-label="forecast interval and outcome">',
        f'<line x1="4" y1="11" x2="{W - 4:.0f}" y2="11" stroke="currentColor" opacity=".15"/>',
        f'<rect x="{x(c.lo):.1f}" y="6" width="{max(x(c.hi) - x(c.lo), 1.0):.1f}" height="10" '
        'rx="2" fill="#2458c8" opacity=".28"/>',
        f'<line x1="{x(c.median):.1f}" y1="3" x2="{x(c.median):.1f}" y2="19" stroke="#2458c8" '
        'stroke-width="2"/>',
    ]
    if c.line is not None:
        parts.append(
            f'<line x1="{x(c.line.line):.1f}" y1="2" x2="{x(c.line.line):.1f}" y2="20" '
            'stroke="#c25b00" stroke-width="1.4" stroke-dasharray="2 2"/>'
        )
    if c.actual is not None and c.status:
        cx, cy = x(float(c.actual)), 11.0
        col = {"inside": "#1a7f6d", "above": "#b3541e", "below": "#6b3fa0"}[c.status]
        if c.status == "inside":
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy}" r="4.5" fill="{col}"/>')
        elif c.status == "above":
            parts.append(
                f'<polygon points="{cx:.1f},{cy - 5.5} {cx - 5:.1f},{cy + 4} '
                f'{cx + 5:.1f},{cy + 4}" '
                f'fill="{col}"/>'
            )
        else:
            parts.append(
                f'<polygon points="{cx:.1f},{cy + 5.5} {cx - 5:.1f},{cy - 4} '
                f'{cx + 5:.1f},{cy - 4}" '
                f'fill="{col}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


# -------------------------------------------------------------------------------- sections


def _stat_line(c: StatCell) -> str:
    return (
        f"{STAT_LABEL[c.stat]}: median {fmt1(c.median)}, 80% interval {fmt1(c.lo)} to {fmt1(c.hi)}"
    )


BOOK_LABEL = {"pinnacle": "Pinnacle", "draftkings": "DraftKings"}


def _market_line(c: StatCell, *, quiet_none: bool = False) -> str:
    if c.line is None:
        return "" if quiet_none else "No market line stored for this stat."
    book = BOOK_LABEL.get(c.line.book, c.line.book.capitalize())
    return (
        f"{esc(book)} line {c.line.line:g}: we said {pct(c.p_over_ours)} over, the market "
        f"priced the over at {pct(c.line.p_over)}."
    )


def _result(c: StatCell | None, played: bool) -> str:
    if c is None:
        return ""
    if not played or c.actual is None:
        return '<div class="big">DNP</div><div class="g">did not play</div>'
    return f'<div class="big">{c.actual}</div>{glyph(c.status)}'


def tidy_reason(reason: str | None) -> str:
    """``Injury/Illness-LeftKnee;Sprain`` is shown as ``LeftKnee: Sprain``."""
    if not reason:
        return ""
    r = reason.replace("Injury/Illness-", "").replace(";", ": ")
    return r.strip()


def _lead(p: Page, w: WinInfo, m: D.MarketWin) -> str:
    """One plain sentence: who we favoured, who the market favoured, who won."""
    g = p.game
    h, a = team_abbr(g.home_team), team_abbr(g.away_team)

    def fav(prob: float) -> str | None:
        return None if abs(prob - 0.5) < 0.005 else h if prob > 0.5 else a

    ours, theirs = fav(w.p_home), fav(m.p_home)
    if ours is None or theirs is None:
        who = "At least one of us saw a coin flip"
    elif ours == theirs:
        who = f"We and the market both favoured {ours}"
    else:
        who = f"We favoured {ours}; the market favoured {theirs}"
    if g.final and g.home_won is not None:
        return f"{who}. {h if g.home_won else a} won."
    return f"{who}."


def section_game(p: Page) -> str:
    g = p.game
    h_abbr, a_abbr = team_abbr(g.home_team), team_abbr(g.away_team)
    h_name, a_name = team_name(g.home_team), team_name(g.away_team)
    out = ['<section id="game"><h2>1. The game</h2>']
    if g.final:
        winner = h_abbr if g.home_won else a_abbr
        out.append(
            f'<p class="score">{esc(a_abbr)} {g.away_pts}, {esc(h_abbr)} {g.home_pts} '
            f"(final; {esc(winner)} won by {g.margin})</p>"
        )
    else:
        out.append('<p class="score">No final score is stored for this game.</p>')
    tip = et_long(g.tip_utc) if g.tip_utc else "not stored"
    out.append(
        f'<p class="sub">{esc(a_name)} at {esc(h_name)}. Tip-off: {esc(tip)}. '
        f"Game id <code>{esc(g.game_id)}</code>.</p>"
    )
    if not p.has_forecasts or p.win is None:
        reason = (
            "No forecast for this game was stored before tip-off (T-60), so nothing is shown "
            "and nothing is recomputed."
            if p.late_only_rows == 0
            else f"Only {p.late_only_rows} stored rows exist, all made after the T-60 cutoff; "
            "they are not shown, and nothing is recomputed."
        )
        out.append(f"<p>{esc(reason)}</p>")
        if p.win is None and p.has_forecasts:
            out.append("<p>Player forecasts exist, but no win-probability row was stored.</p>")
        if g.final and p.win is None:
            pass
        out.append("</section>")
        return "".join(out)
    w = p.win
    m = w.market
    lab_ours = f"We said (made {et_clock(w.made_at_utc)})"
    out.append('<div class="bars">')
    out.append(
        f'<div class="bar"><span class="lab">{esc(lab_ours)}</span>'
        f"{prob_bar(w.p_home, 'var(--ours)', 'our probability')}"
        f'<span class="val">{pct(w.p_home)}</span></div>'
    )
    if m is not None:
        out.append(
            f'<div class="bar"><span class="lab">Pinnacle at T-60</span>'
            f"{prob_bar(m.p_home, 'var(--mkt)', 'Pinnacle probability')}"
            f'<span class="val">{pct(m.p_home)}</span></div>'
        )
    out.append("</div>")
    out.append(
        f'<p class="note">Bars are the probability that {esc(h_name)} (home) win. '
        "Pinnacle's moneyline is de-vigged proportionally (the two sides sum to 100%).</p>"
    )
    if m is not None:
        out.append(
            f"<p>The market priced {esc(h_abbr)} at {pct(m.p_home)}; we said {pct(w.p_home)}.</p>"
        )
        out.append(f"<p><b>{esc(_lead(p, w, m))}</b></p>")
    else:
        out.append("<p>No Pinnacle moneyline is stored for this game.</p>")
    if g.final and g.home_won is not None:
        pw = w.p_home if g.home_won else 1.0 - w.p_home
        winner_name = h_name if g.home_won else a_name
        line = f"We gave the winner, {esc(winner_name)}, {pct(pw)}"
        if m is not None:
            mw = m.p_home if g.home_won else 1.0 - m.p_home
            line += f"; Pinnacle gave {pct(mw)}. "
            if abs(pw - mw) < 0.005:
                line += "Neither was closer."
            elif pw > mw:
                line += "We were closer on this one."
            else:
                line += "Pinnacle was closer on this one."
        else:
            line += "."
        out.append(f"<p>{line}</p>")
    out.append(
        '<p class="catch">The catch: this is one game, so who was closer is a story, not '
        "evidence. In the 2025-26 replay the market's price scored better than ours on game "
        "winners (CLAUDE.md), so the honest summary is that we do not out-forecast the market "
        "on who wins.</p>"
    )
    if w.fallback_reason:
        out.append(
            f'<p class="note">Win model fell back to rating-only Elo: {esc(w.fallback_reason)}.</p>'
        )
    out.append("</section>")
    return "".join(out)


def section_out(p: Page) -> str:
    out = ['<section id="out"><h2>2. Who was out</h2>']
    rep = p.report
    if p.game.tip_utc is None:
        out.append(
            "<p>No tip-off time is stored for this game, so the T-60 report rule cannot be "
            "applied; no report is shown.</p></section>"
        )
        return "".join(out)
    if rep.as_of_et is None:
        out.append(
            "<p>No usable official injury report was on file for this game at T-60 "
            "(none stamped between 36 hours and 60 minutes before tip). The model treats that "
            "as <em>unknown</em>, never as nobody being out.</p></section>"
        )
        return "".join(out)
    stamp = rep.as_of_et.strftime("%A %B %-d, %-I:%M %p ET")
    out.append(
        f'<p class="sub">Official report stamped {esc(stamp)}, the latest at least 60 minutes '
        "before tip.</p>"
    )
    played = {r.player_id: r for r in p.rows}
    key_status = ("out", "doubtful", "questionable")
    shown = [ln for ln in rep.lines if ln.status in key_status]
    rank = {s: i for i, s in enumerate(key_status)}
    shown.sort(key=lambda ln: (rank[ln.status], ln.team_id or 0, p.report_names[ln.player_id]))
    if shown:
        out.append(
            '<div class="scroll"><table><tr><th>Player</th><th>Report</th>'
            "<th>What happened</th></tr>"
        )
        for ln in shown:
            pr = played.get(ln.player_id)
            what = (
                f"played {pr.minutes:.0f} min" if pr is not None and pr.minutes else "did not play"
            )
            team = team_abbr(ln.team_id) if ln.team_id else "n/a"
            why = f'<br><span class="tag">{esc(tidy_reason(ln.reason))}</span>' if ln.reason else ""
            out.append(
                f"<tr><td>{esc(p.report_names[ln.player_id])}"
                f'<span class="tag">{esc(team)}</span></td>'
                f"<td>{esc(ln.status)}{why}</td><td>{esc(what)}</td></tr>"
            )
        out.append("</table></div>")
    else:
        out.append("<p>Nobody was listed out, doubtful or questionable on the report we used.</p>")
    n_other = len(rep.lines) - len(shown)
    out.append(
        f'<p class="note">{len(shown)} listed out, doubtful or questionable; {n_other} more listed '
        'probable or available (not shown). "What happened" is read from the box score after '
        "the game.</p>"
    )
    w = p.win
    if w is not None and w.model == "rung0_injury_elo" and w.p_mov_elo is not None:
        out.append(
            f"<p>What it did to our win number: ratings alone said {pct(w.p_mov_elo)} for "
            f"{esc(team_abbr(p.game.home_team))}; after the report, {pct(w.p_home)}.</p>"
        )
    out.append(
        "<p>The mechanism, in one sentence: when a regular is listed out, the minutes and shots "
        'he normally takes (his "vacated stats") are handed to the teammates around him in '
        "the points model, and the win model docks his team's rating for the absence.</p>"
    )
    if p.movers:
        bits = "; ".join(f"{esc(n)} {fmt1(a)} vs {fmt1(b)}" for n, a, b in p.movers)
        out.append(
            '<p class="note">Largest gaps between our points forecast and the plain '
            f"recent-games average (ours vs average): {bits}. That gap is everything the model "
            "knew (report, minutes, opponent, rest), not the report alone.</p>"
        )
    out.append("</section>")
    return "".join(out)


def _call_line(label: str, c: Call | None) -> str:
    if c is None:
        return ""
    return (
        f"<li><b>{label}:</b> {esc(c.name)}, points. We said median {fmt1(c.median)} "
        f"(80% interval {fmt1(c.lo)} to {fmt1(c.hi)}); he scored {c.actual} "
        f"({GLYPH[c.status]} {GLYPH_WORD[c.status]}).</li>"
    )


def _team_tag(r: PlayerRow, p: Page) -> str:
    if r.team_id in (p.game.home_team, p.game.away_team):
        return esc(team_abbr(r.team_id))
    if r.team_id:
        return f"last seen on {esc(team_abbr(r.team_id))}"
    return "team unknown"


def _player_card(r: PlayerRow, p: Page, report_status: dict[int, str], dnp: bool) -> str:
    pts = r.cells.get("pts")
    bits = []
    if r.proj_minutes is not None:
        bits.append(f"projected {r.proj_minutes:.0f} min")
    if r.p_play is not None:
        bits.append(f"P(play) {pct(r.p_play)}")
    if not dnp and r.minutes:
        bits.append(f"played {r.minutes:.0f} min")
    if r.player_id in report_status:
        bits.append(f"report: {report_status[r.player_id]}")
    tags = f'<span class="tag">{_team_tag(r, p)}{" · starter" if r.starter else ""}</span>'
    out = [f'<div class="pl"><div class="who">{esc(r.name)}{tags}</div>']
    out.append(f'<div class="meta">{esc(" · ".join(bits))}</div>')
    if pts is not None:
        out.append(f'<div class="fc">{esc(_stat_line(pts))}{fan_svg(pts)}</div>')
        out.append(f'<div class="mk">{_market_line(pts)}</div>')
    out.append(f'<div class="res">{_result(pts, not dnp)}</div>')
    others = [st for st in ("reb", "ast", "fg3m") if st in r.cells]
    if others and not dnp:
        out.append("<details><summary>rebounds, assists, threes</summary>")
        for st in others:
            c = r.cells[st]
            out.append(
                f'<div class="sub-stat"><div>{esc(_stat_line(c))}{fan_svg(c)}'
                f'<div class="mk">{_market_line(c, quiet_none=True)}</div></div>'
                f'<div class="res">{_result(c, True)}</div></div>'
            )
        out.append("</details>")
    out.append("</div>")
    return "".join(out)


def section_players(p: Page) -> str:
    out = ['<section id="players"><h2>3. Players</h2>']
    if not p.has_forecasts or (not p.rows and not p.dnp_rows):
        out.append("<p>No player forecasts were stored for this game before T-60.</p></section>")
        return "".join(out)
    n, ins, ab, be = p.pts_cover
    if n:
        out.append(
            f"<p>Points, {n} players who played: {ins} finished inside our 80% interval "
            f"({round(100 * ins / n)}%; the interval is built to hold about 80%), {ab} above it, "
            f"{be} below it. One game of {n} players says little either way.</p>"
        )
    out.append("<ul>")
    out.append(_call_line("Best call", p.best))
    out.append(_call_line("Worst call", p.worst))
    out.append("</ul>")
    out.append(
        '<p class="note">Best and worst are among players projected for 15+ minutes who played, '
        "judged by distance from our median in units of half our 80% interval. Computed, not "
        "chosen by hand.</p>"
    )
    out.append(
        '<p class="legend">Reading a row: the blue band is our stored 80% interval, the blue tick '
        "our median, the dashed orange rule the market line, and the marker the actual "
        f"({GLYPH['inside']} inside, {GLYPH['above']} above, {GLYPH['below']} below; inside means "
        "between the 10th and 90th percentiles inclusive). Forecasts are for points <em>given "
        "that he plays</em>, and so is our P(over); books usually void a prop if the player "
        "does not play. Hits and misses are drawn the same way.</p>"
    )
    status = {ln.player_id: ln.status for ln in p.report.lines if ln.status != "available"}
    for r in p.rows:
        out.append(_player_card(r, p, status, dnp=False))
    if p.dnp_rows:
        out.append(
            f'<h3 style="font-size:1rem;margin:14px 0 2px">Forecast, but did not play '
            f"({len(p.dnp_rows)})</h3>"
        )
        out.append(
            '<p class="note">We forecast these players and the box score shows no minutes. '
            "A high P(play) next to DNP is a miss on whether he would play, shown as it is.</p>"
        )
        for r in p.dnp_rows:
            out.append(_player_card(r, p, status, dnp=True))
    if p.unforecast_played:
        names = ", ".join(
            f"{esc(n)} ({esc(team_abbr(t))}, {m:.0f} min)" for n, t, m in p.unforecast_played
        )
        out.append(
            f'<p class="note">Played but had no stored forecast ({len(p.unforecast_played)}): '
            f"{names}. The stored rows do not say why; the roster is built from each team's "
            "recent box scores.</p>"
        )
    out.append("</section>")
    return "".join(out)


def _score_sentence(s: ScoreRow) -> str:
    if s.hi < 0:
        return "ours scored lower (better); the interval excludes zero"
    if s.lo > 0:
        return "the recent-games average scored lower (better); the interval excludes zero"
    return "the interval spans zero, so this game cannot tell the two apart"


def section_scoring(p: Page) -> str:
    out = ['<section id="scoring"><h2>4. Scoring</h2>']
    if not p.scoring:
        out.append(
            "<p>No forecast could be scored against a box score for this game.</p></section>"
        )
        return "".join(out)
    out.append(
        "<p>The score is CRPS on whole numbers: for each player, how far our whole forecast "
        "(not just its middle) sat from what happened, in the stat's own units. Zero would be "
        "perfect; a vague shrug scores worse than a sharp forecast that is right. Lower is "
        "better. The comparison is with the plain recent-games average (half-life 10 games), "
        "scored the same way.</p>"
    )
    out.append(
        '<div class="scroll"><table><tr><th>Stat</th><th class="n">n</th><th class="n">Ours</th>'
        '<th class="n">Recent avg</th><th class="n">Ours minus avg [95% CI]</th></tr>'
    )
    for s in p.scoring:
        out.append(
            f'<tr><td>{STAT_LABEL[s.stat]}</td><td class="n">{s.n}</td>'
            f'<td class="n">{s.ours:.2f}</td><td class="n">{s.base:.2f}</td>'
            f'<td class="n">{signed(s.diff)} [{signed(s.lo)}, {signed(s.hi)}]</td></tr>'
        )
    out.append("</table></div>")
    out.append("<ul>")
    for s in p.scoring:
        extra = f" ({s.n_unscorable} player-stats unscorable, excluded)" if s.n_unscorable else ""
        out.append(f"<li>{STAT_LABEL[s.stat]}: {_score_sentence(s)}{esc(extra)}.</li>")
    out.append("</ul>")
    out.append(
        '<p class="catch">The catch: n is the number of players who played in this one game; '
        "the interval resamples those players, so it understates how much a different night "
        "would vary. Players who did not play are excluded, as in the daily scoring. "
        "Season-long figures belong in the forward report, not on a single game's page.</p>"
    )
    out.append("</section>")
    return "".join(out)


def section_taught(p: Page) -> str:
    t = p.taught
    return (
        '<section id="taught"><h2>5. What the data taught us</h2>'
        f'<p class="taught">{esc(t.sentence)}</p>'
        f'<p class="src">Chosen by rule "{esc(t.rule)}" (blowout if the margin reached 25, else a '
        "watch-list star in the game, else the default). Source: "
        f"{esc(t.source)}.</p></section>"
    )


def render_page(p: Page) -> str:
    g = p.game
    h_abbr, a_abbr = team_abbr(g.home_team), team_abbr(g.away_team)
    title = f"{a_abbr} at {h_abbr}, {g.game_date.strftime('%B %-d, %Y')}"
    cutoff_note = (
        "Everything below was read from stored rows; nothing was refit. Forecasts shown are the "
        "latest stored at least 60 minutes before tip-off. All times are Eastern."
    )
    replay = (
        f" Source database: <code>{esc(p.source_name)}</code>. If this is a replay database, the "
        "forecasts were produced by running the daily pipeline over a past season, and the "
        "results are descriptive, not a live record."
        if p.source_name
        else ""
    )
    body = [
        f"<h1>{esc(title)}</h1>",
        f'<p class="note">{esc(cutoff_note)}{replay}</p>',
        section_game(p),
        section_out(p),
        section_players(p),
        section_scoring(p),
        section_known(p.known) if p.known is not None else "",
        section_taught(p),
        '<p class="src">Local page for our own use. Not published; publishing is Devin\'s '
        "decision.</p>",
    ]
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main>"
        + "".join(body)
        + "</main></body></html>\n"
    )
