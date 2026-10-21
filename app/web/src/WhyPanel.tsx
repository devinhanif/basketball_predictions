import { KIND_LABEL, type Reason } from "./why";

/** "Why" panel for a card. Renders nothing when there are no reasons: no placeholder, no empty box. */
export function WhyPanel({ reasons }: { reasons: Reason[] }) {
  if (reasons.length === 0) return null;
  return (
    <section class="panel why" aria-label="Why this forecast">
      <h2>Why</h2>
      <ul class="why-list">
        {reasons.map((r, i) => (
          <li key={`${r.kind}-${i.toString()}`}>
            <span class="tag">{KIND_LABEL[r.kind]}</span>
            <span class="why-text">{r.text}</span>
            {r.source ? (
              <span class="small muted num why-src">
                {r.source}
                {r.as_of ? `, as of ${r.as_of}` : ""}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
