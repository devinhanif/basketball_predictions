// Leg page, top: the line card, the headline and the no-history state (split from Leg.tsx; behaviour unchanged).
import { CalTag } from "../components";
import { IS_ARTIFACT } from "../env";
import { priceText, lineText, longDate, pct } from "../format";
import { bookChance, clampLine, modelPct, nText, ofText, rangeText, chanceSource, rowHits, rowModel, snapHalf } from "../leg";
import { type LadderRow, type LegBreakdown, type Side } from "../legTypes";
import { SourceButton } from "../Provenance";

const S = (n: number): string => n.toString();

/* ------------------------------------------------------------------ the line, front and center */
export function LineCard(props: {
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
  if (IS_ARTIFACT) {
    // The snapshot holds the posted line only: other lines would need the live server, and a number we cannot compute is not shown.
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
          <span class="small muted">{posted !== null && line === posted ? "Posted line" : data.line_source === "model_middle" ? "Middle of our forecast" : "Line"}</span>
        </div>
        <div class="leg-fixed">
          <span class="label">{data.label} line</span>
          <span class="line-out leg-line-out num">{lineText(line)}</span>
        </div>
        <p class="small muted">This snapshot shows the line as it was posted. The line slider needs the live app.</p>
      </section>
    );
  }
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
export function Headline(props: { data: LegBreakdown; row: LadderRow | null; side: Side; line: number; fresh: boolean; market: boolean }) {
  const { data, row, side, line, fresh, market } = props;
  const n10 = data.ladder_n10;
  const n5 = data.ladder_n5;
  const h10 = row ? rowHits(row, side, 10) : data.last10.hits;
  const h5 = row ? rowHits(row, side, 5) : data.last5.hits;
  const p = row ? rowModel(row, side) : data.model.p_hit;
  const price = row ? (side === "over" ? row.over_price : row.under_price) : null;
  const range = fresh ? rangeText(data.last10) : null;
  const book = market ? bookChance(data.prices, data.line === line, row, side) : null;
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
              <div class="leg-sub">
                to go {side} <CalTag source={chanceSource(row, data.model)} />
              </div>
              <div class="leg-sub">
                <SourceButton gameId={data.game_id} playerId={data.player_id} stat={data.stat} what="this forecast" />
              </div>
            </>
          ) : (
            <div class="leg-sub strong">
              {data.model.available ? "No calibrated number at this line" : "No model forecast for this stat yet"}
            </div>
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
          {nText(data.last10)}. Ten games say less than they seem to: a streak is not a signal.
        </p>
      )}
      {market ? (
      <p class="small num leg-price">
        {price !== null ? (
          <>
            {side === "over" ? "Over" : "Under"} <b>{priceText(price)}</b> at {lineText(line)}
            {row && row.over_price !== null && row.under_price !== null
              ? ` (over ${priceText(row.over_price)}, under ${priceText(row.under_price)})`
              : ""}
            . {data.line === line ? data.prices.say : ""}
          </>
        ) : (
          <span class="muted">No price is stored for this line.</span>
        )}
      </p>
      ) : null}
    </section>
  );
}

/** A player with no earlier games in the loaded data (a preseason export): say so, show only what exists. */
export function NoHistory({ data, side, line, row, market }: { data: LegBreakdown; side: Side; line: number; row: LadderRow | null; market: boolean }) {
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
                to go {side} {lineText(line)} <CalTag source={chanceSource(row, data.model)} />
              </div>
            </>
          ) : (
            <div class="leg-sub strong">
              {data.model.available ? "No calibrated number at this line" : "No model forecast for this stat yet"}
            </div>
          )}
        </div>
      </div>
      {market ? (
      <p class="small num leg-price">
        {price !== null ? (
          <>
            {side === "over" ? "Over" : "Under"} <b>{priceText(price)}</b> at {lineText(line)}.
          </>
        ) : (
          <span class="muted">No price is stored for this line.</span>
        )}
      </p>
      ) : null}
    </section>
  );
}
