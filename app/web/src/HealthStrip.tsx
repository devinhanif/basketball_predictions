// The system health strip under the data line (health_strip_v1): how old the export is, its schema, and, in the Market
// lens only, the newest stored quote per book (Pinnacle called out), the stale-model marks and what the export does not
// report yet. A dead quote feed must look dead ("Newest Pinnacle quote: 3 days ago"), never like a quiet night.
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

export function isOld(at: string | null | undefined, hours: number, now: Date): boolean {
  if (!at) return false;
  const h = ageHours(at, now);
  return h !== null && h > hours;
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

/** The strip's content for a health answer (pure apart from `now`). */
export function StripBody(props: { h: Health; market: boolean; now?: Date }) {
  const { h, market } = props;
  const now = props.now ?? new Date();
  const q = h.newest_quote ?? null;
  const marks = h.stale_marks ?? null;
  const missing = h.strip_missing ?? [];
  const schema = h.export?.schema_version;
  return (
    <div class="hstrip" role="status" aria-label="System health">
      <span class="hs-item">
        Export made {h.data_made_at ? <Age at={h.data_made_at} old={isOld(h.data_made_at, OLD_AFTER_HOURS.export, now)} /> : "at a time not reported"}
        {schema !== undefined ? `, schema ${schema.toString()}` : ""}
      </span>
      {market ? (
        <>
          <span class="hs-item">
            {q === null ? (
              "Newest quotes: not reported by this data"
            ) : q.pinnacle === null ? (
              <span class="hs-old">No Pinnacle quote in this export</span>
            ) : (
              <>
                Newest Pinnacle quote: <Age at={q.pinnacle} old={isOld(q.pinnacle, OLD_AFTER_HOURS.quote, now)} />
              </>
            )}
          </span>
          {marks !== null ? (
            <span class="hs-item">
              Stale-model marks:{" "}
              {Object.keys(marks.by_model).length === 0
                ? "none"
                : Object.entries(marks.by_model)
                    .map(([m, by]) => `${m} ${Object.entries(by).map(([why, n]) => `${count(n)} (${why})`).join(", ")}`)
                    .join("; ")}
              {marks.primary_fallback_stale !== null ? `; shown rows on a stale fallback ${count(marks.primary_fallback_stale)}` : ""}
            </span>
          ) : null}
          <details>
            <summary>Every book's newest quote, and what the export does not report yet</summary>
            {q !== null ? (
              <>
                <p class="small">{q.label}: {q.source}.</p>
                <ul class="small">
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
                <p class="small">Not reported by the export yet:</p>
                <ul class="small">
                  {missing.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </details>
        </>
      ) : null}
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
  if (st.kind === "none") return <p class="hstrip">System health: {st.why}</p>;
  return <StripBody h={st.h} market={market} />;
}
