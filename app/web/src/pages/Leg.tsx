import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { ErrorPanel, Explain, Loading } from "../components";
import { american, bookName, lineText, longDate, num, pct, stamp, tipTime } from "../format";
import {
  bookChance,
  clampLine,
  ladderAt,
  modelPct,
  nText,
  ofText,
  rangeText,
  rankPhrase,
  ratio,
  rowHits,
  rowModel,
  rowPrice,
  snapHalf,
} from "../leg";
import { gameHref, legHref } from "../router";
import { BeyondBlock, MatePer36, OnOffBlock, SteadyBlock } from "../RoleCards";
import { LEG_SHORT, type Chip, type LadderRow, type LegBreakdown, type LegStat, type Side, type Split, type Tally, type TeammateOut } from "../legTypes";

const S = (n: number): string => n.toString();

/* ------------------------------------------------------------------ the line, front and center */
function LineCard(props: {
  data: LegBreakdown;
  line: number;
  side: Side;
  min: number;
  max: number;
  onLine: (l: number) => void;
  onSide: (s: Side) => void;
}) {
  const { data, line, side, min, max } = props;
  const posted = data.prices.posted_line;
  const id = `leg-line-${data.stat}`;
  const step = (d: number): void => {
    props.onLine(clampLine(line + d, min, max));
  };
  return (
    <section class="panel leg-line" aria-label="The line">
      <div class="row between wrap">
        <div class="leg-sides" role="group" aria-label="Over or under">
          {(["over", "under"] as const).map((s) => (
            <button
              key={s}
              type="button"
              class="leg-side"
              aria-pressed={side === s}
              onClick={() => {
                props.onSide(s);
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <span class="small muted">
          {posted !== null && line === posted
            ? "Posted line"
            : data.line_source === "model_middle"
              ? "Your line (starts at the middle of our forecast)"
              : "Your line"}
        </span>
      </div>
      <div class="stepper leg-stepper">
        <button type="button" class="btn" aria-label="Lower the line by half a point" disabled={line <= min} onClick={() => { step(-0.5); }}>
          -0.5
        </button>
        <div class="stepper-mid">
          <label class="label" for={id}>
            {data.label} line
          </label>
          <output class="line-out leg-line-out num" htmlFor={id} aria-live="polite">
            {lineText(line)}
          </output>
        </div>
        <button type="button" class="btn" aria-label="Raise the line by half a point" disabled={line >= max} onClick={() => { step(0.5); }}>
          +0.5
        </button>
      </div>
      <input
        id={id}
        class="slider"
        type="range"
        min={min}
        max={max}
        step={0.5}
        value={line}
        onInput={(e) => {
          props.onLine(snapHalf(Number(e.currentTarget.value)));
        }}
      />
      <div class="muted small num alt-ends">
        <span>{lineText(min)}</span>
        {posted !== null ? (
          <button type="button" class="linklike" disabled={line === posted} onClick={() => { props.onLine(posted); }}>
            Back to the posted line ({lineText(posted)})
          </button>
        ) : (
          <span>No posted line for this stat</span>
        )}
        <span>{lineText(max)}</span>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ headline numbers */
function Headline(props: { data: LegBreakdown; row: LadderRow | null; side: Side; line: number; fresh: boolean }) {
  const { data, row, side, line, fresh } = props;
  const n10 = data.ladder_n10;
  const n5 = data.ladder_n5;
  const h10 = row ? rowHits(row, side, 10) : data.last10.hits;
  const h5 = row ? rowHits(row, side, 5) : data.last5.hits;
  const p = row ? rowModel(row, side) : data.model.p_hit;
  const price = row ? (side === "over" ? row.over_price : row.under_price) : null;
  const range = fresh ? rangeText(data.last10) : null;
  const book = bookChance(data.prices, data.line === line, row, side);
  const verb = side === "over" ? "went over" : "stayed under";
  return (
    <section class="leg-head" aria-label="Headline numbers">
      <div class={`leg-tiles${book ? " four" : ""}`}>
        <div class="leg-tile main">
          <div class="label">Last {S(n10)} games</div>
          <div class="leg-big num">{ofText(h10, n10)}</div>
          <div class="leg-sub">
            {verb} {lineText(line)}
          </div>
          {range ? (
            <div class="leg-sub num">
              95% range {range}
            </div>
          ) : null}
        </div>
        <div class="leg-tile">
          <div class="label">Last {S(n5)}</div>
          <div class="leg-big num">{ofText(h5, n5)}</div>
          <div class="leg-sub">{verb}</div>
        </div>
        <div class={`leg-tile${data.model.available ? " model" : " nomodel"}`}>
          <div class="label">Our model</div>
          {data.model.available && p !== null ? (
            <>
              <div class="leg-big num">{modelPct(p)}</div>
              <div class="leg-sub">to go {side}</div>
            </>
          ) : (
            <div class="leg-sub strong">No model forecast for this stat yet</div>
          )}
        </div>
        {book ? (
          <div class="leg-tile book">
            <div class="label">{book.book}</div>
            <div class="leg-big num">{pct(book.p)}</div>
            <div class="leg-sub">to go {side}, margin removed</div>
          </div>
        ) : null}
      </div>
      {data.last10.small ? (
        <p class="leg-flag" role="note">
          <strong>Small sample.</strong> Only {S(data.last10.n)} earlier games, fewer than 10. A rate over so few games swings a lot.
        </p>
      ) : (
        <p class="small muted num">
          {nText(data.last10)}. Ten games say less than they seem to: a streak is not an edge.
        </p>
      )}
      <p class="small num leg-price">
        {price !== null ? (
          <>
            {side === "over" ? "Over" : "Under"} <b>{american(price)}</b> at {lineText(line)}
            {row && row.over_price !== null && row.under_price !== null
              ? ` (over ${american(row.over_price)}, under ${american(row.under_price)})`
              : ""}
            . {data.line === line ? data.prices.say : ""}
          </>
        ) : (
          <span class="muted">No price is stored for this line.</span>
        )}
      </p>
    </section>
  );
}

/** A player with no earlier games in the loaded data (a preseason export): say so, show only what exists. */
function NoHistory({ data, side, line, row }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null }) {
  const p = row ? rowModel(row, side) : data.model.p_hit;
  const price = row ? (side === "over" ? row.over_price : row.under_price) : null;
  return (
    <section class="panel flat" aria-label="No game history loaded">
      <h3>No earlier games are loaded for this player</h3>
      <p class="small">
        This data holds none of his games before {longDate(data.as_of_date)}, so there is no last 5, last 10, split or
        teammates-out history to show, and no hit rate. We show only what is stored.
      </p>
      <div class="leg-tiles">
        <div class={`leg-tile${data.model.available ? " model" : " nomodel"}`}>
          <div class="label">Our model</div>
          {data.model.available && p !== null ? (
            <>
              <div class="leg-big num">{modelPct(p)}</div>
              <div class="leg-sub">
                to go {side} {lineText(line)}
              </div>
            </>
          ) : (
            <div class="leg-sub strong">No model forecast for this stat yet</div>
          )}
        </div>
      </div>
      <p class="small num leg-price">
        {price !== null ? (
          <>
            {side === "over" ? "Over" : "Under"} <b>{american(price)}</b> at {lineText(line)}.
          </>
        ) : (
          <span class="muted">No price is stored for this line.</span>
        )}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ the last ten games */
function ChipItem({ c }: { c: Chip }) {
  const word = c.state === "hit" ? "Hit" : c.state === "miss" ? "Miss" : "Push";
  const body = (
    <>
      <span class="sr">
        {word}: {S(c.value)} against {c.opp}, {longDate(c.date)}, {num(c.minutes)} minutes.
        {c.linkable === true ? " Opens that game." : ""}
      </span>
      <span class="leg-chip-val num" aria-hidden="true">
        {S(c.value)}
      </span>
      <span class="leg-chip-mark" aria-hidden="true">
        {c.state === "hit" ? "HIT" : c.state === "miss" ? "MISS" : "PUSH"}
      </span>
      <span class="leg-chip-meta num" aria-hidden="true">
        {c.home === null ? "" : c.home ? "vs " : "at "}
        {c.opp}
      </span>
      <span class="leg-chip-meta num" aria-hidden="true">
        {num(c.minutes, 0)} min
      </span>
    </>
  );
  return (
    <li class={`leg-chip ${c.state}${c.linkable === true ? " linked" : ""}`}>
      {c.linkable === true ? (
        <a class="leg-chip-link" href={gameHref(c.game_id)}>
          {body}
        </a>
      ) : (
        body
      )}
    </li>
  );
}

function Games({ data, side, line, row }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null }) {
  const first = data.chips.slice(0, 5);
  const rest = data.chips.slice(5);
  const h5 = row ? rowHits(row, side, 5) : data.last5.hits;
  const h10 = row ? rowHits(row, side, 10) : data.last10.hits;
  return (
    <section class="panel" aria-label="Last ten games">
      <div class="row between wrap">
        <h2>Last {S(data.chips.length)} games</h2>
        <span class="small muted">newest first</span>
      </div>
      <p class="small muted" style={{ marginTop: 0 }}>
        {data.label} against the {lineText(line)} line, {side}. Minutes are what he played.
      </p>
      <div class="label leg-group">
        Last {S(first.length)}: {ofText(h5, first.length)}
      </div>
      <ul class="leg-chips">
        {first.map((c) => (
          <ChipItem key={c.game_id} c={c} />
        ))}
      </ul>
      {rest.length > 0 ? (
        <>
          <div class="label leg-group">
            Last {S(data.chips.length)}: {ofText(h10, data.chips.length)} (games 6 to {S(data.chips.length)} below)
          </div>
          <ul class="leg-chips">
            {rest.map((c) => (
              <ChipItem key={c.game_id} c={c} />
            ))}
          </ul>
        </>
      ) : null}
      {data.chips.some((c) => c.linkable !== true) ? (
        <p class="small muted">
          {data.chips.some((c) => c.linkable === true)
            ? "A game with an arrow opens its page. The others are older than the data loaded here, so they cannot be opened."
            : "These games are older than the data loaded here, so none of them can be opened."}
        </p>
      ) : null}
      <p class="small muted num">
        {data.role.say} Season to date: {ofText(data.season.hits, data.season.n)} {side === "over" ? "over" : "under"} {lineText(line)}
        {data.season.avg !== null ? `, averaging ${num(data.season.avg)}` : ""} ({nText(data.season)}).
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ the model's view */
function ModelPanel({ data, side, line, row }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null }) {
  const m = data.model;
  const p = row ? rowModel(row, side) : m.p_hit;
  if (!m.available) {
    return (
      <section class="panel" aria-label="The model">
        <h2>The model</h2>
        <p class="lead">No model forecast for this stat yet.</p>
        <p class="small muted">
          Our forecasts cover points, rebounds, assists and threes. Everything else on this page, the recent games, the
          splits, the teammates and the matchup, comes straight from box scores. We never add single-stat forecasts to
          make one for a combo: the stats move together, so that would be an invented number.
        </p>
      </section>
    );
  }
  return (
    <section class="panel" aria-label="The model">
      <h2>The model</h2>
      <p class="lead">
        {p !== null ? (
          <>
            {data.player} {side === "over" ? "goes over" : "stays under"} {lineText(line)}: <b>{modelPct(p)}</b>
          </>
        ) : (
          "No chance computed at this line."
        )}
      </p>
      <p class="small">{m.reason}</p>
      <p class="small" data-model-source={m.source}>
        {m.source_label}
      </p>
      <p class="small muted">
        Forecast stamp: <span class="mono">{m.stamp ?? "none"}</span>. The model was built without ever seeing this game.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ alt lines side by side */
function Ladder(props: { data: LegBreakdown; line: number; side: Side; onLine: (l: number) => void }) {
  const { data, line, side } = props;
  const rows = [-2, -1, 0, 1, 2]
    .map((k) => ladderAt(data.ladder, line + k))
    .filter((r): r is LadderRow => r !== null);
  const model = data.model.available;
  return (
    <section class="panel" aria-label="Other lines side by side">
      <h2>Other lines, side by side</h2>
      <p class="small muted" style={{ marginTop: 0 }}>
        Tap a line to move to it. Recent form on the left, our forecast on the right.
      </p>
      <div class="table-wrap">
        <table class="leg-table num">
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col">Last 10</th>
              <th scope="col">Last 5</th>
              <th scope="col">Model</th>
              <th scope="col">Price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const p = rowModel(r, side);
              const px = rowPrice(r, side);
              return (
                <tr key={r.line} aria-current={r.line === line ? "true" : undefined}>
                  <th scope="row">
                    <button
                      type="button"
                      class="linklike leg-pick"
                      onClick={() => {
                        props.onLine(r.line);
                      }}
                    >
                      {lineText(r.line)}
                    </button>
                  </th>
                  <td>{ofText(rowHits(r, side, 10), data.ladder_n10)}</td>
                  <td>{ofText(rowHits(r, side, 5), data.ladder_n5)}</td>
                  <td>{model && p !== null ? modelPct(p) : <span class="muted">none yet</span>}</td>
                  <td>{px !== null ? american(px) : <span class="muted">none</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ the numbers, in words */
function InWords({ data }: { data: LegBreakdown }) {
  const d = data.dissection;
  if (!d || !d.text) return null;
  return (
    <section class="panel leg-words" aria-label="In words">
      <h2>In plain words</h2>
      <p class="leg-words-text">{d.text}</p>
      <p class="small muted num">
        {d.disclosure} Each of the {S(d.numbers_checked)} numbers in this paragraph was matched to a figure on this page
        before it was shown.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ every book at this line */
function Books({ data, line }: { data: LegBreakdown; line: number }) {
  const books = data.prices.books;
  if (books.length === 0 || data.line !== line) return null;
  const cell = (price: number | null, unusable: boolean) =>
    unusable ? <span class="muted">not usable</span> : price !== null ? american(price) : <span class="muted">none</span>;
  return (
    <section class="panel" aria-label="Prices by book">
      <h2>Prices by book at {lineText(line)}</h2>
      <p class="small muted" style={{ marginTop: 0 }}>
        What each book had before tip. Exchanges (Kalshi, Polymarket, Novig) are not sportsbooks. "Not usable" is a quote
        with no real bid behind it: we do not show it as a price.
      </p>
      <div class="table-wrap">
        <table class="leg-table num">
          <thead>
            <tr>
              <th scope="col">Book</th>
              <th scope="col">Over</th>
              <th scope="col">Under</th>
              <th scope="col">Min to tip</th>
            </tr>
          </thead>
          <tbody>
            {books.map((b) => (
              <tr key={b.book}>
                <td>
                  <strong>{bookName(b.book)}</strong>
                  {b.alternate ? <span class="tag">Alt</span> : null}
                </td>
                <td>{cell(b.over_price, b.over_unusable)}</td>
                <td>{cell(b.under_price, b.under_unusable)}</td>
                <td>{b.minutes_before_tip !== null ? Math.round(b.minutes_before_tip).toString() : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ heads up and notes */
function HeadsUp({ data }: { data: LegBreakdown }) {
  if (!data.heads_up) return null;
  return (
    <p class="leg-notice" role="note">
      {data.heads_up}
    </p>
  );
}

/* ------------------------------------------------------------------ splits */
function Cell({ t, label }: { t: Tally; label: string }) {
  return (
    <div class={`leg-cell${t.small ? " thin" : ""}`}>
      <div class="label">{label}</div>
      {t.n === 0 ? (
        <div class="leg-cell-main muted">no games</div>
      ) : (
        <>
          <div class="leg-cell-main num">{ratio(t)}</div>
          <div class="small muted num">
            {t.avg !== null ? `avg ${num(t.avg)}` : ""}
            {t.avg_minutes !== null ? `, ${num(t.avg_minutes, 0)} min` : ""}
          </div>
        </>
      )}
      <div class="small muted num">{t.n === 0 ? "n = 0" : nText(t)}</div>
    </div>
  );
}

const SPLIT_GROUPS: { title: string; keys: string[] }[] = [
  { title: "Home or away", keys: ["home", "away"] },
  { title: "Rest", keys: ["b2b", "rest1", "rest2"] },
  { title: "This opponent", keys: ["vs_opp"] },
];

function SplitRow({ s }: { s: Split }) {
  return (
    <div class={`leg-split${s.tonight ? " tonight" : ""}`}>
      <div class="leg-split-label">
        {s.label}
        {s.tonight ? <span class="tag">Tonight</span> : null}
      </div>
      <div class="leg-cells">
        <Cell t={s.this_season} label="This season" />
        <Cell t={s.since_last_season} label="Since last season" />
      </div>
    </div>
  );
}

function Splits({ data, side, line }: { data: LegBreakdown; side: Side; line: number }) {
  return (
    <section class="panel" aria-label="Splits">
      <h2>Splits bettors check</h2>
      <p class="small muted" style={{ marginTop: 0 }}>
        Times he went {side} {lineText(line)}, out of games in each situation. The number under each is the sample size:
        thin samples are dimmed and should not be trusted.
      </p>
      {SPLIT_GROUPS.map((g) => {
        const rows = data.splits.filter((s) => g.keys.includes(s.key));
        return (
          <div key={g.title} class="leg-group-block">
            <h3>{g.title}</h3>
            {rows.map((s) => (
              <SplitRow key={s.key} s={s} />
            ))}
          </div>
        );
      })}
    </section>
  );
}

/* ------------------------------------------------------------------ teammates out */
function cleanReason(r: string | null): string {
  if (!r || r === "-") return "";
  return r.replace(/^Injury\/Illness-/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function TeammateCard({ t, side, line }: { t: TeammateOut; side: Side; line: number }) {
  const wo = t.without;
  const wi = t.with_;
  return (
    <div class="leg-mate">
      <div class="row between wrap">
        <strong>{t.name}</strong>
        <span class={`tag leg-status ${t.status}`}>{t.status}</span>
      </div>
      <div class="small muted">
        {cleanReason(t.reason) ? `${cleanReason(t.reason)}. ` : ""}Plays {num(t.avg_minutes, 0)} minutes a game lately.
      </div>
      <BeyondBlock b={t.beyond} />
      <div class="past-games">
      <div class="label">Past games on this line</div>
      <div class="leg-cells">
        <div class={`leg-cell${wo.small ? " thin" : ""}`}>
          <div class="label">With him out</div>
          {wo.n === 0 ? (
            <div class="leg-cell-main muted">no games</div>
          ) : (
            <>
              <div class="leg-cell-main num">{ratio(wo)}</div>
              <div class="small muted num">
                {wo.avg !== null ? `avg ${num(wo.avg)}` : ""}
                {wo.avg_minutes !== null ? `, ${num(wo.avg_minutes, 0)} min` : ""}
              </div>
            </>
          )}
          <div class="small muted num">{nText(wo)}</div>
        </div>
        <div class={`leg-cell${wi.small ? " thin" : ""}`}>
          <div class="label">With him playing</div>
          {wi.n === 0 ? (
            <div class="leg-cell-main muted">no games</div>
          ) : (
            <>
              <div class="leg-cell-main num">{ratio(wi)}</div>
              <div class="small muted num">
                {wi.avg !== null ? `avg ${num(wi.avg)}` : ""}
                {wi.avg_minutes !== null ? `, ${num(wi.avg_minutes, 0)} min` : ""}
              </div>
            </>
          )}
          <div class="small muted num">{nText(wi)}</div>
        </div>
      </div>
      <p class="small muted" style={{ marginBottom: 0 }}>
        Games {side} {lineText(line)}. {t.note}
      </p>
      </div>
      <MatePer36 without={t.per36_without} with_={t.per36_with} name={t.name} />
      <p class="small muted" style={{ marginBottom: 0 }}>
        Per 36 minutes, the same games, whatever stat is on screen. Assists are from the box score.
      </p>
    </div>
  );
}

function Teammates({ data, side, line }: { data: LegBreakdown; side: Side; line: number }) {
  const t = data.teammates;
  const own = t.player_status && t.player_status !== "available" ? t.player_status : null;
  return (
    <section class="panel" aria-label="Teammates out tonight">
      <h2>Teammates out tonight</h2>
      {own ? (
        <p class="leg-flag" role="note">
          <strong>
            {data.player} is listed {own}
          </strong>
          {cleanReason(t.player_status_reason) ? `: ${cleanReason(t.player_status_reason)}` : ""}. Every number here assumes he plays.
        </p>
      ) : null}
      {t.items.length === 0 ? (
        <p>No rotation teammates are listed out, doubtful or questionable on the injury report.</p>
      ) : (
        <div class="stack tight">
          {t.items.map((m) => (
            <TeammateCard key={m.player_id} t={m} side={side} line={line} />
          ))}
        </div>
      )}
      {t.omitted_unnamed > 0 ? (
        <p class="small muted">
          {S(t.omitted_unnamed)} more rotation {t.omitted_unnamed === 1 ? "player is" : "players are"} listed but not named in our data.
        </p>
      ) : null}
      <p class="small muted">
        {t.note}
        {t.report_as_of_utc ? ` Latest report used: ${stamp(t.report_as_of_utc)}.` : ""}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ role and on/off */
function RolePanel({ data }: { data: LegBreakdown }) {
  return (
    <section class="panel" aria-label="Role and on/off">
      <h2>His role and the team with him on</h2>
      <SteadyBlock s={data.steadiness} />
      <OnOffBlock o={data.onoff} player={data.player} />
    </section>
  );
}

/* ------------------------------------------------------------------ opponent */
function Opponent({ data }: { data: LegBreakdown }) {
  const o = data.opponent_allowed;
  return (
    <section class="panel" aria-label="The opponent">
      <h2>{data.opponent} against his position</h2>
      {o ? (
        <>
          <p class="lead num">
            {o.opp} allows <b>{num(o.avg_allowed)}</b> {data.label.toLowerCase()} a game to {o.position_noun}
          </p>
          <p class="num">
            <b>{rankPhrase(o.rank, o.of)}</b>. League average {num(o.league_avg)}. {S(o.n_games)} games this season.
          </p>
          <p class="small muted">{o.note}</p>
        </>
      ) : (
        <p class="muted">We do not have this comparison for this player.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ the page */
function Header({ data, line }: { data: LegBreakdown; line: number }) {
  return (
    <header class="leg-title">
      <p class="back-row">
        <a class="back-link" href={`#/game/${data.game_id}`}>
          Back to the game
        </a>
      </p>
      <h1>{data.player}</h1>
      <p class="muted num" style={{ margin: 0 }}>
        {data.team} {data.home ? "vs" : "at"} {data.opponent} · tip-off {tipTime(data.tip_utc)} · {data.label} {lineText(line)}
      </p>
      <nav class="leg-tabs" aria-label="Stat">
        {data.tabs.map((t) => (
          <a
            key={t.stat}
            class="leg-tab"
            href={legHref(data.game_id, data.player_id, t.stat, data.side)}
            aria-current={t.stat === data.stat ? "true" : undefined}
            aria-label={`${t.label}${t.has_model ? "" : ", no model forecast yet"}`}
          >
            {LEG_SHORT[t.stat]}
            {t.has_model ? null : <i class="leg-dot" aria-hidden="true" />}
          </a>
        ))}
      </nav>
      <p class="small muted" style={{ margin: "4px 0 0" }}>
        A dot means no model forecast yet for that stat.
      </p>
    </header>
  );
}

export function Leg(props: { gameId: string; playerId: number; stat: LegStat; side: Side; onStale: (s: boolean) => void }) {
  const [line, setLine] = useState<number | null>(null);
  const [side, setSide] = useState<Side>(props.side);
  const [data, setData] = useState<LegBreakdown | null>(null);
  const [bounds, setBounds] = useState<{ min: number; max: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(true);
  const [tick, setTick] = useState(0);
  const { onStale } = props;

  useEffect(() => {
    let live = true;
    setPending(true);
    const t = setTimeout(
      () => {
        api.breakdown(props.gameId, props.playerId, props.stat, line, side).then(
          (r) => {
            if (!live) return;
            setData(r.data);
            setError(null);
            setPending(false);
            onStale(r.fromCache);
            const first = r.data.ladder[0];
            const last = r.data.ladder[r.data.ladder.length - 1];
            if (first && last) setBounds((b) => b ?? { min: first.line, max: last.line });
          },
          (e: unknown) => {
            if (!live) return;
            setError(e instanceof Error ? e.message : String(e));
            setPending(false);
          },
        );
      },
      data === null ? 0 : 100,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [props.gameId, props.playerId, props.stat, line, side, tick]);

  const tabsReady = data !== null;
  useEffect(() => {
    // On a phone the stat strip scrolls sideways: keep the current stat in view.
    document.querySelector(".leg-tab[aria-current='true']")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [tabsReady, props.stat]);

  if (error && !data) {
    return (
      <ErrorPanel
        message={error}
        retry={() => {
          setTick((x) => x + 1);
        }}
      />
    );
  }
  if (!data || !bounds) return <Loading />;

  const shown = line ?? data.line;
  const row = ladderAt(data.ladder, shown);
  const fresh = !pending && data.line === shown && data.side === side;
  return (
    <div class={`stack leg${fresh ? "" : " stale"}`} aria-busy={pending}>
      <Header data={data} line={shown} />
      <LineCard
        data={data}
        line={shown}
        side={side}
        min={bounds.min}
        max={bounds.max}
        onLine={setLine}
        onSide={setSide}
      />
      {data.prior_games === 0 ? (
        <>
          <NoHistory data={data} side={side} line={shown} row={row} />
          <Books data={data} line={shown} />
        </>
      ) : (
        <>
          <Headline data={data} row={row} side={side} line={shown} fresh={fresh} />
          <InWords data={data} />
          <HeadsUp data={data} />
          <Games data={data} side={side} line={shown} row={row} />
          <ModelPanel data={data} side={side} line={shown} row={row} />
          <Ladder data={data} line={shown} side={side} onLine={setLine} />
          <Books data={data} line={shown} />
          <Splits data={data} side={side} line={shown} />
          <Teammates data={data} side={side} line={shown} />
          <RolePanel data={data} />
          <Opponent data={data} />
        </>
      )}
      {error ? (
        <p role="alert" class="small">
          Could not refresh: {error}{" "}
          <button
            type="button"
            class="linklike"
            onClick={() => {
              setTick((x) => x + 1);
            }}
          >
            Try again
          </button>
        </p>
      ) : null}
      <Explain label="How these numbers are made">
        <p>
          Everything here is counted from box scores of games before {longDate(data.as_of_date)}: no game on that night or later is
          used, so nothing leaks from the result. Hit means {side === "over" ? "above" : "below"} the line; landing exactly on a
          whole-number line is a push. A combo is the sum of its parts in each game. The range is a 95% interval for the true
          rate given {S(data.last10.n)} games.
        </p>
        <p>
          The model chance exists only for points, rebounds, assists and threes. Teammate effects compare his own games with
          that teammate out against games with him playing; they are small samples and can easily be noise. This is analysis, not
          advice.
        </p>
      </Explain>
    </div>
  );
}
