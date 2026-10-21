import { useState } from "preact/hooks";
import { LEARNED, STATUS_LABEL, STATUS_ORDER, evidenceText, isShowable, type LearnedStatus } from "../learned";

export function LearnedPage() {
  const [only, setOnly] = useState<LearnedStatus | "all">("all");
  const items = LEARNED.filter(isShowable).filter((l) => only === "all" || l.status === only);
  return (
    <div class="stack">
      <div class="page-head">
        <h1>What we learned</h1>
        <p class="muted">
          Every idea we tested that moved something, with how sure we are. Each shows its sample size and an interval, and
          the ledger row it came from. Most ideas did nothing and are not here.
        </p>
      </div>
      <div class="st-seg-row" role="group" aria-label="Filter by result">
        <button type="button" class="st-chip" aria-pressed={only === "all"} onClick={() => { setOnly("all"); }}>
          All ({LEARNED.length.toString()})
        </button>
        {STATUS_ORDER.map((s) => (
          <button key={s} type="button" class="st-chip" aria-pressed={only === s} onClick={() => { setOnly(s); }}>
            {STATUS_LABEL[s]} ({LEARNED.filter((l) => l.status === s).length.toString()})
          </button>
        ))}
      </div>
      <p class="small muted">
        How to read the numbers: each is a difference in forecast error (lower is better, so a minus sign means better).
        The brackets are the confidence interval the ledger reports, clustered by game or date; if it excludes zero the difference is probably not luck. Small differences can
        still be worth little.
      </p>
      {items.map((l) => (
        <article key={l.id} class={`panel learned st-${l.status}`} id={l.id}>
          <div class="row wrap between">
            <span class={`st-status s-${l.status}`}>{STATUS_LABEL[l.status]}</span>
            <span class="small muted num">{l.ledger_ids.length > 0 ? `Ledger ${l.ledger_ids.join(", ")}` : "Replay report"}</span>
          </div>
          <h2>{l.title}</h2>
          <p>{l.claim}</p>
          <table class="st-table num">
            <tbody>
              {l.evidence.map((e) => (
                <tr key={e.label}>
                  <th scope="row">{e.label}</th>
                  <td>
                    <b>{evidenceText(e)}</b>
                    <div class="small muted">
                      n = {e.n}; {e.interval}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p class="small muted st-caveat">{l.caveat}</p>
        </article>
      ))}
      {items.length === 0 ? <p class="muted">Nothing in this group.</p> : null}
      <p class="small muted">
        Where the market is better, we say so. The honest answer to "what should I bet" stays "keep your money" until the
        live log shows skill.
      </p>
    </div>
  );
}
