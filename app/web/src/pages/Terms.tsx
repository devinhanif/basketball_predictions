import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { IS_ARTIFACT } from "../env";
import { count } from "../format";
import { LensSwitch } from "../Lens";
import { useLens } from "../prefs";
import { termsHashFor } from "../router";
import { allTerms, searchTerms, termHref, termsFor } from "../terms";

/** Key terms: plain definitions, searchable, each with its own link. Market terms appear only in the Market lens. */
export function Terms(props: { term: string | null; q: string | null }) {
  const market = useLens() === "market";
  const [q, setQ] = useState(props.q ?? "");
  const list = useMemo(() => termsFor(market), [market]);
  const shown = searchTerms(list, q);
  const hidden = allTerms().length - list.length;
  const wanted = props.term;
  const wantedHidden = wanted !== null && !list.some((t) => t.id === wanted) && allTerms().some((t) => t.id === wanted);
  const unknown = wanted !== null && !allTerms().some((t) => t.id === wanted);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (wanted === null) return;
    const el = document.getElementById(`term-${wanted}`);
    if (!el) return;
    el.scrollIntoView({ block: "center" });
    el.focus({ preventScroll: true });
  }, [wanted, market]);

  const onSearch = (v: string): void => {
    setQ(v);
    // Keep a shareable link without a navigation (no scroll jump, no page reset).
    if (!IS_ARTIFACT) window.history.replaceState(null, "", termsHashFor(wanted, v.trim() === "" ? null : v.trim()));
  };

  return (
    <div class="stack terms-page">
      <header class="page-head">
        <div class="kicker">Key terms</div>
        <h1>What the words on this site mean</h1>
        <p class="lead">
          {count(list.length)} terms, one or two plain sentences each. Every term has its own link, so a definition can be shared or opened from
          anywhere it is used.
        </p>
      </header>
      {hidden > 0 && !market ? (
        <p class="small muted">
          {count(hidden)} more terms about the market are listed in the Market lens. <LensSwitch compact />
        </p>
      ) : null}
      {wantedHidden ? (
        <p class="kbanner tone-notice" role="status">
          The term you followed is about the market, so it is listed in the Market lens only.
        </p>
      ) : null}
      {unknown ? (
        <p class="kbanner tone-notice" role="status">
          There is no term with that link. Search below instead.
        </p>
      ) : null}
      <label class="field terms-search" for="terms-q">
        <span class="label">Search terms</span>
        <input
          id="terms-q"
          type="search"
          value={q}
          placeholder="e.g. interval, holdout, calibrated"
          autoComplete="off"
          onInput={(e) => {
            onSearch(e.currentTarget.value);
          }}
        />
      </label>
      <p class="small muted num" aria-live="polite">
        {q.trim() === "" ? `${count(list.length)} terms` : `${count(shown.length)} of ${count(list.length)} terms match "${q.trim()}"`}
      </p>
      <div class="terms-list" ref={box}>
        {shown.length === 0 ? (
          <div class="panel empty">
            <h2>No term matches</h2>
            <p class="muted">Nothing in the list uses those words. Try one word, such as "interval" or "forecast".</p>
          </div>
        ) : (
          <dl class="terms-dl">
            {shown.map((t) => (
              <div key={t.id} id={`term-${t.id}`} class={`term${t.id === wanted ? " on" : ""}`} tabIndex={-1}>
                <dt>
                  <a href={termHref(t.id)} class="term-name">
                    {t.term}
                  </a>
                </dt>
                <dd>{t.says}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </div>
  );
}
