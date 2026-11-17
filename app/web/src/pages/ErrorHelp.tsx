// Error-code help (#/help/errors, #/help/errors/<code>): every code in the shared registry (app/error_codes.json, the
// same file the API serves at /api/errors) with what happened, why, and what to do. Bundled, so it works with the data
// server down. Searchable; each code has its own link, which the failure screen uses.
import { useEffect, useState } from "preact/hooks";
import { allCopies, knownCode, searchCodes } from "../errorCodes";
import { IS_ARTIFACT } from "../env";
import { count } from "../format";
import { helpHref } from "../router";
import { PageHead, StateBlock } from "../ui";

export function ErrorHelp(props: { code: string | null; q: string | null }) {
  const [q, setQ] = useState(props.q ?? "");
  const list = allCopies();
  const shown = searchCodes(list, q);
  const wanted = props.code;
  const unknown = wanted !== null && !knownCode(wanted);
  useEffect(() => {
    if (wanted === null) return;
    const el = document.getElementById(`code-${wanted}`);
    el?.scrollIntoView({ block: "center" });
    el?.focus({ preventScroll: true });
  }, [wanted]);
  return (
    <div class="stack pb-page pb-codes">
      <PageHead kicker="Help" title="What each error code means" description={`Every failure screen names a code. Here is what happened, why, and what to do, for each of the ${count(list.length)} codes.`} />
      {unknown ? (
        <p class="kbanner tone-notice" role="status">
          There is no code called "{wanted}". Search below instead.
        </p>
      ) : null}
      <div class="pb-toolbar">
      <label class="field terms-search" for="codes-q">
        <span class="label">Search codes</span>
        <input
          id="codes-q"
          type="search"
          value={q}
          placeholder="e.g. offline, forecast, old"
          autoComplete="off"
          onInput={(e) => {
            const v = e.currentTarget.value;
            setQ(v);
            if (!IS_ARTIFACT) window.history.replaceState(null, "", helpHref(wanted, v.trim() === "" ? null : v.trim()));
          }}
        />
      </label>
      <p class="small muted num" aria-live="polite">
        {q.trim() === "" ? `${count(list.length)} codes` : `${count(shown.length)} of ${count(list.length)} codes match "${q.trim()}"`}
      </p>
      </div>
      {shown.length === 0 ? (
        <StateBlock kind="empty" title="No code matches">
          Try one word from the failure screen, such as "offline" or "forecast".
        </StateBlock>
      ) : (
        <div class="code-list">
          {shown.map((c) => (
            <section key={c.code} id={`code-${c.code}`} class={`panel code-card${c.code === wanted ? " on" : ""}`} tabIndex={-1} aria-labelledby={`code-h-${c.code}`}>
              <h2 id={`code-h-${c.code}`}>{c.title}</h2>
              <p class="small muted">
                <a class="mono" href={helpHref(c.code)}>
                  {c.code}
                </a>
                {c.status !== null ? ` · answer status ${c.status.toString()}` : " · raised by this page"}
              </p>
              <dl class="pb-code-rows">
                <div>
                  <dt>What happened</dt>
                  <dd>{c.what}</dd>
                </div>
                <div>
                  <dt>Why</dt>
                  <dd>{c.why}</dd>
                </div>
                <div>
                  <dt>What to do</dt>
                  <dd>{c.todo}</dd>
                </div>
              </dl>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
