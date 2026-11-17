// "Where this number comes from": the stored record behind a shown forecast (record_v1). Model, version, weights, how much
// data it was fit on and through when, when it was made against tip-off, whether that met the T-60 rule, the data and
// code versions, and the other stored snapshots (time, lead, model only: the API never serves their numbers).
import { useState } from "preact/hooks";
import { View } from "./components";
import { count, decimal, MISSING } from "./format";
import { honestyApi, type Maybe, type ProvRecord, type Snapshot } from "./honesty";
import { gameHref, legHref, sourceHref } from "./router";
import { stamp } from "./time";
import { termHref } from "./terms";
import { Badge, Dialog, PageHead, Table, Tile, type Column } from "./ui";
import { useMode } from "./prefs";
import { useLoad } from "./useLoad";
import type { LegStat } from "./legTypes";
import { LEG_SHORT } from "./legTypes";

const when = (s: string | null | undefined): string => (s ? stamp(s) : MISSING);
const lead = (m: number | null | undefined): string => (typeof m === "number" ? `${decimal(m, 0)} min` : MISSING);

/** The T-60 rule in plain words, from the stored `meets_t60`. */
export function t60Words(meets: boolean | null | undefined): string {
  if (meets === true) return "Yes: it was made at least 60 minutes before tip-off, the forward scoring rule's cutoff.";
  if (meets === false) return "No: it was made less than 60 minutes before tip-off, so the forward scoring rule would not count it.";
  return "Not known: no lead time is stored for it.";
}

/** The stored `meets_t60` as icon + word (never colour alone). */
function T60Badge({ meets }: { meets: boolean | null | undefined }) {
  if (meets === true) return <Badge tone="better" icon="check" word="Meets T-60" />;
  if (meets === false) return <Badge tone="worse" icon="warn" word="Misses T-60" />;
  return <Badge tone="none" icon="info" word="T-60 not known" />;
}

/** Phones: the snapshots as a list of short cards (the six-column table is for wider screens; same rows, same order). */
function SnapCards({ rows }: { rows: readonly Snapshot[] }) {
  return (
    <ul class="prov-snapcards" aria-label="Every stored forecast for this one">
      {rows.map((s) => (
        <li key={`${s.made_at ?? ""}-${s.model_name ?? ""}-${s.version ?? ""}`}>
          <div class="psc-top num">
            <strong>{when(s.made_at)}</strong>
            <T60Badge meets={s.meets_t60} />
          </div>
          <div class="small num">{lead(s.lead_minutes)} before tip-off</div>
          <div class="small">
            <span class="mono">{s.model_name ?? MISSING}</span> · version <span class="mono">{s.version ?? MISSING}</span>
          </div>
          <div class="small muted">
            {[s.is_primary ? "the one shown" : null, s.is_latest ? "latest of its model" : null, s.comparison_only ? "comparison only" : null].filter(Boolean).join(", ") || MISSING}
          </div>
        </li>
      ))}
    </ul>
  );
}

function Short(props: { short: string | null; full: string | null; label: string }) {
  if (props.full === null && props.short === null) return <span>{MISSING}</span>;
  return (
    <details class="prov-sha">
      <summary>
        <span class="mono">{props.short ?? props.full}</span> <span class="small muted">(show the full {props.label})</span>
      </summary>
      <span class="mono prov-full">{props.full ?? props.short}</span>
    </details>
  );
}

const SNAP_COLS: readonly Column<Snapshot>[] = [
  { key: "made", head: "Made", cell: (s) => when(s.made_at), sort: (s) => s.made_at, first: "ascending" },
  { key: "lead", head: "Before tip-off", cell: (s) => lead(s.lead_minutes), sort: (s) => s.lead_minutes, num: true },
  { key: "t60", head: "Meets T-60", cell: (s) => (s.meets_t60 === true ? "yes" : s.meets_t60 === false ? "no" : MISSING) },
  { key: "model", head: "Model", cell: (s) => <span class="mono">{s.model_name ?? MISSING}</span>, sort: (s) => s.model_name },
  { key: "ver", head: "Version", cell: (s) => <span class="mono">{s.version ?? MISSING}</span> },
  {
    key: "role",
    head: "Role",
    cell: (s) =>
      [s.is_primary ? "the one shown" : null, s.is_latest ? "latest of its model" : null, s.comparison_only ? "comparison only" : null].filter(Boolean).join(", ") ||
      MISSING,
  },
];

/** The stored record, laid out as a definition list. Every value is a field of the answer. */
export function RecordBody({ rec }: { rec: ProvRecord }) {
  if (!rec.available) {
    return (
      <div class="prov">
        <p class="lead">No stored record for this forecast.</p>
        <p>{rec.reason ?? "The data does not carry a record for it."}</p>
      </div>
    );
  }
  const analyst = useMode() === "analyst";
  const snaps =
    rec.snapshots.length > 0 ? (
      <>
        <div class="prov-snaptable">
          <Table caption="Every stored forecast for this one" rows={rec.snapshots} columns={SNAP_COLS} rowKey={(s) => `${s.made_at ?? ""}-${s.model_name ?? ""}-${s.version ?? ""}`} noun={rec.snapshots.length === 1 ? "snapshot" : "snapshots"} />
        </div>
        <SnapCards rows={rec.snapshots} />
        <p class="small muted">{rec.snapshots_note ?? "Their numbers are not served here."}</p>
      </>
    ) : (
      <p class="small muted">No other stored snapshots.</p>
    );
  return (
    <div class="prov">
      <div class="prov-summary">
        <div class="prov-summary-model">
          <span class="label">Model</span>
          <span class="mono">{rec.model_name ?? MISSING}</span>
          <T60Badge meets={rec.meets_t60} />
        </div>
        <div class="prov-tiles">
          <Tile label="before tip-off" value={lead(rec.lead_minutes)} note={`made ${when(rec.made_at)}`} />
          <Tile label="rows trained on" value={rec.n_train === null ? MISSING : count(rec.n_train)} note={`games through ${rec.fit_through ?? MISSING}`} />
        </div>
      </div>
      <dl class="prov-dl">
        <dt>Model</dt>
        <dd>
          <span class="mono">{rec.model_name ?? MISSING}</span>, version <span class="mono">{rec.version ?? MISSING}</span>
        </dd>
        <dt>Weights</dt>
        <dd>
          <Short short={rec.weights_sha256_short} full={rec.weights_sha256} label="fingerprint (sha256)" />
          {rec.weights_scope ? <span class="small muted"> {rec.weights_scope}</span> : null}
        </dd>
        <dt>Trained on</dt>
        <dd class="num">
          {rec.n_train === null ? MISSING : `${count(rec.n_train)} rows`}, games through {rec.fit_through ?? MISSING}
        </dd>
        <dt>Made</dt>
        <dd class="num">
          {when(rec.made_at)}; tip-off {when(rec.tipoff)}; lead {lead(rec.lead_minutes)}
        </dd>
        <dt>
          <a href={termHref("t-minus")}>T-60 rule</a>
        </dt>
        <dd>
          {t60Words(rec.meets_t60)}
          {rec.t60_rule ? <span class="small muted"> {rec.t60_rule}</span> : null}
        </dd>
        <dt>Data version</dt>
        <dd>
          <span class="mono">{rec.data_version ?? MISSING}</span>
        </dd>
        <dt>Code version</dt>
        <dd>
          <Short short={rec.git_sha === null ? null : rec.git_sha.slice(0, 12)} full={rec.git_sha} label="commit" />{" "}
          {rec.git_dirty === true
            ? "The code had uncommitted changes when it ran (a dirty tree)."
            : rec.git_dirty === false
              ? "The code was clean (no uncommitted changes)."
              : "Whether the code had uncommitted changes is not stored."}
        </dd>
        <dt>Stamped</dt>
        <dd class="num">{when(rec.stamped_at)}</dd>
        {rec.kind === "prop" ? (
          <>
            <dt>Why this row is the one shown</dt>
            <dd>
              <span class="mono">{rec.primary_source ?? MISSING}</span>
              {rec.primary_detail ? `: ${rec.primary_detail}` : ""}
            </dd>
          </>
        ) : rec.fallback_reason ? (
          <>
            <dt>Fallback</dt>
            <dd>{rec.fallback_reason}</dd>
          </>
        ) : null}
      </dl>
      {rec.missing.length > 0 ? (
        <p class="small">
          <strong>Not stored:</strong> {rec.missing.join(", ")}.
        </p>
      ) : null}
      {analyst ? (
        snaps
      ) : (
        <details class="prov-more">
          <summary>Every stored forecast for this one</summary>
          {snaps}
        </details>
      )}
    </div>
  );
}

function load(gameId: string, playerId: number | null, stat: LegStat | null) {
  return playerId === null || stat === null ? honestyApi.gameRecord(gameId) : honestyApi.propRecord(gameId, playerId, stat);
}

function Loaded(props: { gameId: string; playerId: number | null; stat: LegStat | null }) {
  const [state, reload] = useLoad(() => load(props.gameId, props.playerId, props.stat), [props.gameId, props.playerId, props.stat]);
  return (
    <View state={state} reload={reload} what="the stored record">
      {(m: Maybe<ProvRecord>) =>
        m.kind === "absent" ? (
          <p class="panel empty">
            <strong>Not in this copy of the data.</strong> {m.why} The record behind a forecast is served by the live app.
          </p>
        ) : (
          <RecordBody rec={m.data} />
        )
      }
    </View>
  );
}

/**
 * The visible affordance beside a forecast number: a real button (keyboard reachable) that opens the record in a dialog,
 * with a link to the same record as a page so it can be shared.
 */
export function SourceButton(props: { gameId: string; playerId?: number | null; stat?: LegStat | null; what: string }) {
  const [open, setOpen] = useState(false);
  const pid = props.playerId ?? null;
  const stat = props.stat ?? null;
  return (
    <>
      <button
        type="button"
        class="linklike prov-open small"
        aria-haspopup="dialog"
        onClick={() => {
          setOpen(true);
        }}
      >
        Where this number comes from
      </button>
      {open ? (
        <Dialog
          title={`Where ${props.what} comes from`}
          id="prov-dlg"
          wide
          onClose={() => {
            setOpen(false);
          }}
        >
          <Loaded gameId={props.gameId} playerId={pid} stat={stat} />
          <p class="small">
            <a href={sourceHref(props.gameId, pid, stat)}>Open this record as a page</a> (a link you can share).
          </p>
        </Dialog>
      ) : null}
    </>
  );
}

/** `#/source/...`: the same record as a page. */
export function SourcePage(props: { gameId: string; playerId: number | null; stat: LegStat | null }) {
  const what = props.playerId === null || props.stat === null ? "the game's win chance" : `the ${LEG_SHORT[props.stat]} forecast`;
  return (
    <div class="stack">
      <PageHead
        kicker="Where this number comes from"
        title={`The stored record behind ${what}`}
        description="Which model made it, on how much data, when, and whether it met the rule that it be made at least an hour before tip-off."
        actions={
          <a class="btn" href={props.playerId === null || props.stat === null ? gameHref(props.gameId) : legHref(props.gameId, props.playerId, props.stat)}>
            {props.playerId === null || props.stat === null ? "Back to the game" : "Back to the forecast"}
          </a>
        }
      />
      <section class="panel">
        <Loaded gameId={props.gameId} playerId={props.playerId} stat={props.stat} />
      </section>
    </div>
  );
}
