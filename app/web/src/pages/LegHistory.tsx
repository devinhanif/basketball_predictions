// Leg page, the record: last games, the model panel, the ladder, the numbers in words and every book (split from Leg.tsx).
import { CalTag, Explain } from "../components";
import { IS_ARTIFACT } from "../env";
import { plainModelSentence } from "../plain";
import { american, bookName, lineText, longDate, num } from "../format";
import { ladderAt, modelPct, nText, ofText, chanceSource, rowHits, rowModel, rowPrice } from "../leg";
import { gameHref } from "../router";
import { type Chip, type LadderRow, type LegBreakdown, type Side } from "../legTypes";

const S = (n: number): string => n.toString();

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

export function Games({ data, side, line, row }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null }) {
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
export function ModelPanel({ data, side, line, row }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null }) {
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
            {data.player} {side === "over" ? "goes over" : "stays under"} {lineText(line)}: <b>{modelPct(p)}</b>{" "}
            <CalTag source={chanceSource(row, m)} />
          </>
        ) : (
          "No calibrated number at this line."
        )}
      </p>
      <p class="small">{m.reason}</p>
      <p class="small" data-model-source={m.source}>
        {plainModelSentence(m.source)}
        {m.source === "fixture" ? "" : " It was built without ever seeing this game."}
      </p>
      <Explain label="Show the exact details">
        <ul class="plain small">
          <li>{m.source_label}</li>
          <li>
            Forecast stamp: <span class="mono">{m.stamp ?? "none"}</span>
          </li>
        </ul>
      </Explain>
    </section>
  );
}

/* ------------------------------------------------------------------ alt lines side by side */
export function Ladder(props: { data: LegBreakdown; line: number; side: Side; onLine: (l: number) => void; market: boolean }) {
  const { data, line, side } = props;
  const rows = [-2, -1, 0, 1, 2]
    .map((k) => ladderAt(data.ladder, line + k))
    .filter((r): r is LadderRow => r !== null);
  const model = data.model.available;
  return (
    <section class="panel" aria-label="Other lines side by side">
      <h2>Other lines, side by side</h2>
      <p class="small muted" style={{ marginTop: 0 }}>
        {IS_ARTIFACT ? "Recent form on the left, our forecast on the right." : "Tap a line to move to it. Recent form on the left, our forecast on the right."}
      </p>
      <div class="table-wrap">
        <table class="leg-table num">
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col">Last 10</th>
              <th scope="col">Last 5</th>
              <th scope="col">Model</th>
              {props.market ? <th scope="col">Price</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const p = rowModel(r, side);
              const px = rowPrice(r, side);
              return (
                <tr key={r.line} aria-current={r.line === line ? "true" : undefined}>
                  <th scope="row">
                    {IS_ARTIFACT ? (
                      <span class="num">{lineText(r.line)}</span>
                    ) : (
                      <button
                        type="button"
                        class="linklike leg-pick"
                        onClick={() => {
                          props.onLine(r.line);
                        }}
                      >
                        {lineText(r.line)}
                      </button>
                    )}
                  </th>
                  <td>{ofText(rowHits(r, side, 10), data.ladder_n10)}</td>
                  <td>{ofText(rowHits(r, side, 5), data.ladder_n5)}</td>
                  <td>
                    {model && p !== null ? (
                      <>
                        {modelPct(p)} <CalTag source={r.model_source} />
                      </>
                    ) : model ? (
                      <span class="muted small" style={{ whiteSpace: "normal" }}>no calibrated number here</span>
                    ) : (
                      <span class="muted">none yet</span>
                    )}
                  </td>
                  {props.market ? <td>{px !== null ? american(px) : <span class="muted">none</span>}</td> : null}
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
export function InWords({ data }: { data: LegBreakdown }) {
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
export function Books({ data, line }: { data: LegBreakdown; line: number }) {
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
