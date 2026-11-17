// The system health strip under the data line (health_strip_v1): how old the export is, its schema, and, in the Market
// lens only, the newest stored quote per book (Pinnacle called out), the stale-model marks and what the export does not
// report yet. A dead quote feed must look dead ("Newest Pinnacle quote: 3 days ago"), never like a quiet night.
// One compact line (a disclosure); the detail opens under it. The Pinnacle age stays on the line in the Market lens.
import { useEffect, useState } from "preact/hooks";
import { codeCopy } from "./errorCodes";
import { bookName, count } from "./format";
import { honestyApi, type Health } from "./honesty";
import { useLens } from "./prefs";
import { CT_ZONE, STALE_AFTER_HOURS, ageHours, agoText, stamp } from "./time";

/**
 * When a stored time reads as old. The export: the data line's own threshold (time.ts, 30 h: a nightly job that missed
 * one run). A quote: the odds capture runs several times a day before tip-offs, so a newest quote more than 12 hours
 * old on a slate day means the capture stopped, not that the market went quiet. Display only; nothing else uses these.
 */
export const OLD_AFTER_HOURS = { export: STALE_AFTER_HOURS, quote: 12 } as const;

type St = { kind: "loading" } | { kind: "ok"; h: Health } | { kind: "none"; why: string };
type Marks = NonNullable<Health["stale_marks"]>;

export function isOld(at: string | null | undefined, hours: number, now: Date): boolean {
  if (!at) return false;
  const h = ageHours(at, now);
  return h !== null && h > hours;
}

/** Why a comparison model's rows were marked stale, in plain words (nba/export/best_model.py `avg_freshness`). */
const STALE_WHY: Record<string, string> = {
  writer_flag: "its own run flagged its weights as out of date",
  month_block_mismatch: "its weights are from a different month than the game",
  installed_after_made: "its weights were installed after the forecast was made",
  fit_through_not_before_month: "its weights were fit on data from the game's own month",
  avg_run_behind_production: "its run is older than the production forecast it competes with",
};

/** A model id to a reader's name: the AVG shadow models are "the AVG comparison model"; anything else as stored. */
export function modelName(id: string): string {
  return id.startsWith("props_avg") ? `the AVG comparison model (${id})` : `model ${id}`;
}

/**
 * The stale-model marks as sentences, from the stored counts only. One sentence per model and reason, then what the
 * shown forecasts use instead. Empty marks say so in words.
 */
export function staleSentences(marks: Marks): string[] {
  const out: string[] = [];
  for (const [m, by] of Object.entries(marks.by_model)) {
    for (const [why, n] of Object.entries(by)) {
      const reason = STALE_WHY[why] ?? `it is marked "${why.replace(/_/g, " ")}"`;
      const name = modelName(m);
      out.push(`${name.charAt(0).toUpperCase()}${name.slice(1)} is set aside on ${count(n)} ${n === 1 ? "row" : "rows"} because ${reason}.`);
    }
  }
  if (out.length === 0) out.push("No model is marked out of date in this export.");
  const fb = marks.primary_fallback_stale;
  if (fb !== null) out.push(fb === 0 ? "No shown forecast needed the fallback." : `${count(fb)} shown ${fb === 1 ? "forecast uses" : "forecasts use"} the production model instead.`);
  return out;
}

/** The short tail for the compact line: how many shown forecasts fell back, or null when there is nothing to say. */
export function staleShort(marks: Marks | null | undefined): string | null {
  if (!marks) return null;
  const n = marks.primary_fallback_stale;
  if (n !== null && n > 0) return `${count(n)} on the production model`;
  return Object.keys(marks.by_model).length === 0 ? null : "a comparison model set aside";
}

function Age(props: { at: string; old: boolean }) {
  const ago = agoText(props.at) ?? stamp(props.at, CT_ZONE);
  return props.old ? (
    <span class="hs-old" title={codeCopy("stale_data").what}>
      {ago} ({codeCopy("stale_data").title.toLowerCase()})
    </span>
  ) : (
    <span>{ago}</span>
  );
}

const Sep = () => (
  <span class="hs-sep" aria-hidden="true">
    ·
  </span>
);

/** The strip's content for a health answer (pure apart from `now`). */
export function StripBody(props: { h: Health; market: boolean; now?: Date }) {
  const { h, market } = props;
  const now = props.now ?? new Date();
  const q = h.newest_quote ?? null;
  const marks = h.stale_marks ?? null;
  const missing = h.strip_missing ?? [];
  const schema = h.export?.schema_version;
  const exportOld = isOld(h.data_made_at, OLD_AFTER_HOURS.export, now);
  if (!market)
    return (
      <div class="hstrip" role="status" aria-label="System health">
        <div class="hs-line">
          <span class={`hs-dot${exportOld ? " old" : ""}`} aria-hidden="true" />
          <span class="hs-items">
            Export made {h.data_made_at ? <Age at={h.data_made_at} old={exportOld} /> : "at a time not reported"}
            {schema !== undefined ? `, schema ${schema.toString()}` : ""}
          </span>
        </div>
      </div>
    );
  const quoteOld = q?.pinnacle ? isOld(q.pinnacle, OLD_AFTER_HOURS.quote, now) : false;
  const short = staleShort(marks);
  const alarm = exportOld || quoteOld || (q !== null && q.pinnacle === null);
  return (
    <div class="hstrip" role="status" aria-label="System health">
      <details class="hs-details">
        <summary class="hs-line">
          <span class={`hs-dot${alarm ? " old" : ""}`} aria-hidden="true" />
          <span class="hs-items">
            {q === null ? (
              <span class="hs-item">Newest quotes not reported</span>
            ) : q.pinnacle === null ? (
              <span class="hs-item hs-old">No Pinnacle quote in this export</span>
            ) : (
              <span class="hs-item">
                Newest Pinnacle quote <Age at={q.pinnacle} old={quoteOld} />
              </span>
            )}
            <Sep />
            <span class="hs-item">Export {h.data_made_at ? <Age at={h.data_made_at} old={exportOld} /> : "time not reported"}</span>
            {short !== null ? (
              <>
                <Sep />
                <span class="hs-item">{short}</span>
              </>
            ) : null}
          </span>
          <span class="hs-more">Details</span>
        </summary>
        <div class="hs-body">
          <p class="small">
            Export made{" "}
            {h.data_made_at ? (
              <>
                {stamp(h.data_made_at, CT_ZONE)} (<Age at={h.data_made_at} old={exportOld} />)
              </>
            ) : (
              "at a time not reported"
            )}
            {schema !== undefined ? `, schema ${schema.toString()}` : ""}.
          </p>
          {marks !== null ? (
            <>
              <p class="small hs-sub">Models set aside as out of date</p>
              <ul class="small">
                {staleSentences(marks).map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </>
          ) : null}
          {q !== null ? (
            <>
              <p class="small hs-sub">
                Every book's {q.label} ({q.source})
              </p>
              <ul class="small hs-books">
                {Object.entries(q.by_book).map(([book, at]) => (
                  <li key={book}>
                    {bookName(book)}: {stamp(at, CT_ZONE)} (<Age at={at} old={isOld(at, OLD_AFTER_HOURS.quote, now)} />)
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {missing.length > 0 ? (
            <>
              <p class="small hs-sub">Not reported by the export yet</p>
              <ul class="small">
                {missing.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </details>
    </div>
  );
}

export function HealthStrip() {
  const market = useLens() === "market";
  const [st, setSt] = useState<St>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    honestyApi.health().then(
      (r) => {
        if (live) setSt(r.data.kind === "ok" ? { kind: "ok", h: r.data.data } : { kind: "none", why: r.data.why });
      },
      () => {
        if (live) setSt({ kind: "none", why: "The health check did not answer." });
      },
    );
    return () => {
      live = false;
    };
  }, []);
  if (st.kind === "loading") return null;
  if (st.kind === "none")
    return (
      <p class="hstrip">
        <span class="hs-line">
          <span class="hs-dot old" aria-hidden="true" />
          <span class="hs-items">System health: {st.why}</span>
        </span>
      </p>
    );
  return <StripBody h={st.h} market={market} />;
}
