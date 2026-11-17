// The parlay engine's stored joint chances (parlay_diagnostics_v1), Market lens only: per record, the one joint chance its
// engine stored and the engine's name. Where the independent-legs or copula value is not stored, the page says so; it
// never multiplies leg chances (that would be a new model output, and wrong for legs of the same player). Paged.
import { useState } from "preact/hooks";
import { api } from "./api";
import { View } from "./components";
import { count, decimal, MISSING, percent } from "./format";
import { honestyApi, type DiagRecord, type Maybe, type ParlayDiag } from "./honesty";
import { Table, type Column } from "./ui";
import { useLoad } from "./useLoad";

export const PAGE = 50;
const NOT_STORED = "not stored in tonight's export";

const COLS: readonly Column<DiagRecord>[] = [
  { key: "title", head: "Contract", cell: (r) => <span class="wrap-any">{r.title ?? r.ticker ?? MISSING}</span>, sort: (r) => r.title },
  { key: "engine", head: "Engine", cell: (r) => <span class="mono">{r.engine ?? MISSING}</span>, sort: (r) => r.engine },
  { key: "side", head: "Side", cell: (r) => r.legs.map((l) => l.side ?? MISSING).join(", ") || MISSING },
  { key: "legs", head: "Legs", cell: (r) => count(r.n_legs), sort: (r) => r.n_legs, num: true },
  { key: "joint", head: "Stored joint chance", cell: (r) => percent(r.joint_stored, 1), sort: (r) => r.joint_stored, num: true, first: "descending" },
  { key: "ind", head: "Independent legs", cell: (r) => (r.joint_independent === null ? <span class="muted small">{NOT_STORED}</span> : percent(r.joint_independent, 1)) },
  { key: "cop", head: "Copula", cell: (r) => (r.joint_copula === null ? <span class="muted small">{NOT_STORED}</span> : percent(r.joint_copula, 1)) },
  { key: "shrunk", head: "After shrinking to the market", cell: (r) => percent(r.shrunk_prob, 1), sort: (r) => r.shrunk_prob, num: true },
  { key: "w", head: "Model weight", cell: (r) => decimal(r.model_weight, 2), num: true },
];

function Body({ d, offset, onPage }: { d: ParlayDiag; offset: number; onPage: (o: number) => void }) {
  if (!d.available) {
    return <p>{d.reason ?? "No stored parlay check for this night."}</p>;
  }
  return (
    <>
      <p class="num">
        {count(d.n_records)} stored records from <span class="mono">{d.source ?? MISSING}</span>; engines:{" "}
        {Object.entries(d.engines)
          .map(([e, n]) => `${e} ${count(n)}`)
          .join(", ") || MISSING}
        . Records with an independent-legs joint: {count(d.n_with_independent)}; with a copula joint: {count(d.n_with_copula)}.
      </p>
      {d.n_with_independent === 0 || d.n_with_copula === 0 ? (
        <p class="small">
          The independent-legs and copula values are {NOT_STORED}. They need export field 4 of the export-field proposal (an independent-legs joint chance
          beside the copula one on each record; docs/contracts/PROPOSAL_export_fields_2026-11-17.md). Until then nothing is shown in their place.
        </p>
      ) : null}
      <Table caption={`Records from ${count(d.offset)}, ${count(d.n_returned)} on this page`} rows={d.records} columns={COLS} rowKey={(r) => `${r.ticker ?? ""}-${r.legs.map((l) => `${l.side ?? ""}${String(l.threshold)}`).join("")}`} noun="records" class="sc-table" />
      <p class="row wrap">
        <button type="button" class="btn" disabled={offset === 0} onClick={() => { onPage(Math.max(0, offset - PAGE)); }}>
          Previous page
        </button>
        <button type="button" class="btn" disabled={d.offset + d.n_returned >= d.n_records} onClick={() => { onPage(offset + PAGE); }}>
          Next page
        </button>
      </p>
      {d.note ? <p class="small muted">{d.note}</p> : null}
    </>
  );
}

function ForDate({ date }: { date: string }) {
  const [offset, setOffset] = useState(0);
  const [state, reload] = useLoad(() => honestyApi.parlayDiag(date, offset, PAGE), [date, offset]);
  return (
    <View state={state} reload={reload} what="the stored joint chances">
      {(m: Maybe<ParlayDiag>) =>
        m.kind === "absent" ? (
          <p>
            <strong>Not in this snapshot.</strong> {m.why}
          </p>
        ) : (
          <Body d={m.data} offset={offset} onPage={setOffset} />
        )
      }
    </View>
  );
}

/** The panel: the route's night, or the night of the engine's latest card. */
export function ParlayDiagnostics(props: { date: string | null }) {
  const [card, reload] = useLoad(() => (props.date === null ? api.parlay() : Promise.resolve({ data: null, fromCache: false })), [props.date]);
  return (
    <section class="panel" aria-labelledby="pd-h">
      <h2 id="pd-h">The engine's stored joint chances</h2>
      <p class="small muted">Each record's joint chance comes from the engine it names. Nothing is multiplied on this page.</p>
      {props.date !== null ? (
        <ForDate date={props.date} />
      ) : (
        <View state={card} reload={reload} what="the engine's card">
          {(p) => (p?.report_date ? <ForDate date={p.report_date} /> : <p>No engine card is stored, so there is no night to read.</p>)}
        </View>
      )}
    </section>
  );
}
