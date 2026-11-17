// The honest scorecard (#/scorecard; scorecard_v1): the production model against its declared baseline on stored scores,
// each mean with its n rows, n dates and date-clustered interval; frozen-test rows and locked dates as counts only; the
// rule the scores follow, verbatim; and what cannot be shown yet. Every number is a field of the answer.
import type { ComponentChildren } from "preact";
import { View } from "../components";
import { count, decimal, MISSING } from "../format";
import { hasPriceWords, honestyApi, type Maybe, type Metric, type ScoreTarget, type Scorecard } from "../honesty";
import { PageControls } from "../Lens";
import { useLens, useMode } from "../prefs";
import { topHref } from "../router";
import { termHref } from "../terms";
import { stamp } from "../time";
import { Card, CompareBadge, OnThisPage, PageHead, SectionHead, StateBlock, Table, Tile, type Column } from "../ui";
import { useLoad } from "../useLoad";

const TARGET_LABEL: Record<string, string> = { pts: "Points", reb: "Rebounds", ast: "Assists", fg3m: "Threes", win_prob_home: "Game winner" };
const targetLabel = (t: string): string => TARGET_LABEL[t] ?? t;

export interface ScoreRow {
  key: string;
  target: string;
  label: string;
  role: string;
  shown: boolean;
  metric: string | null;
  m: Metric | null;
  n: number;
  nDates: number;
  nLocked: number;
  datesLocked: number;
  withheld: string | null;
  comparison: boolean;
}

/** One table row per stored model row and per stored comparison, metric by metric; counts-only rows keep their counts. */
export function scoreRows(targets: readonly ScoreTarget[]): ScoreRow[] {
  const out: ScoreRow[] = [];
  for (const t of targets) {
    for (const m of t.models) {
      const metrics = Object.entries(m.metrics);
      const base = { target: t.target, role: m.role, shown: m.shown, n: m.n_scored, nDates: m.n_dates, nLocked: m.n_scored_locked, datesLocked: m.n_dates_locked, withheld: m.withheld_reason, comparison: false };
      if (metrics.length === 0) out.push({ ...base, key: `${t.target}:${m.model_name}`, label: m.model_name, metric: null, m: null });
      for (const [name, v] of metrics) out.push({ ...base, key: `${t.target}:${m.model_name}:${name}`, label: m.model_name, metric: name, m: v });
    }
    for (const c of t.comparisons) {
      const metrics = Object.entries(c.metrics);
      const base = { target: t.target, role: "difference", shown: c.shown, n: c.n_pairs, nDates: c.n_dates, nLocked: c.n_pairs_locked, datesLocked: c.n_dates_locked, withheld: c.withheld_reason, comparison: true };
      const label = `${c.arm} minus ${c.comparator}`;
      if (metrics.length === 0) out.push({ ...base, key: `${t.target}:${label}`, label, metric: null, m: null });
      for (const [name, v] of metrics) out.push({ ...base, key: `${t.target}:${label}:${name}`, label, metric: name, m: v });
    }
  }
  return out;
}

const CALL_WORDS: Record<string, string> = {
  too_few_dates: "no call: ten or fewer dates never decide",
  no_interval: "no call: no interval",
};

function Call({ r }: { r: ScoreRow }) {
  const c = r.m?.call ?? null;
  if (!r.comparison || !r.shown) return <span class="muted st-none">{MISSING}</span>;
  if (c === "better" || c === "worse" || c === "same") return <CompareBadge which={c} what="than the baseline" />;
  return <span>{c === null ? MISSING : (CALL_WORDS[c] ?? c)}</span>;
}

function Mean({ r }: { r: ScoreRow }) {
  if (!r.shown || r.m === null) {
    return (
      <span class="hidden-row">
        Hidden until its look.
      </span>
    );
  }
  const v = r.m;
  return (
    <span class="num">
      {decimal(v.value, 3)}{" "}
      <span class="small muted sc-int">
        [{decimal(v.lo, 3)}, {decimal(v.hi, 3)}]
      </span>
    </span>
  );
}

const MEASURE: Column<ScoreRow> = { key: "target", head: "Measure", lead: true, cell: (r) => `${targetLabel(r.target)}${r.metric ? `, ${r.metric.toUpperCase()}` : ""}`, sort: (r) => r.target };
const ROW: Column<ScoreRow> = {
  key: "row",
  head: "Row",
  lead: true,
  cell: (r) => (
    <span class="wrap-any">
      <span class="mono">{r.label}</span> <span class="small muted">({r.role.replace(/_/g, " ")})</span>
    </span>
  ),
  sort: (r) => r.label,
};
const LOCKED: Column<ScoreRow> = {
  key: "locked",
  head: "Locked dates (hidden until the look)",
  wide: true,
  cell: (r) => (r.nLocked > 0 ? `${count(r.nLocked)} rows, ${count(r.datesLocked)} dates` : MISSING),
  sort: (r) => r.nLocked,
  num: true,
};

const SHOWN_COLS: readonly Column<ScoreRow>[] = [
  MEASURE,
  ROW,
  { key: "mean", head: "Mean [95% interval]", wide: true, cell: (r) => <Mean r={r} />, sort: (r) => r.m?.value ?? null },
  { key: "n", head: "n rows", cell: (r) => count(r.m?.n ?? r.n), sort: (r) => r.n, num: true, first: "descending" },
  { key: "d", head: "n dates", cell: (r) => count(r.m?.n_dates ?? r.nDates), sort: (r) => r.nDates, num: true, first: "descending" },
  { key: "call", head: "Better, worse or same", wide: true, cell: (r) => <Call r={r} /> },
  LOCKED,
];

/** The producer's reason names its pre-registration file; a reader of the page gets the sentence, Analyst keeps the path. */
function withheldText(t: string | null | undefined, analyst: boolean): string {
  if (!t) return MISSING;
  if (analyst) return t;
  return stripPaths(t)
    .replace(/\s*\(docs\/[^)]*\)/g, "")
    .replace(/,\s*see docs\/[^)]*\)/g, ")")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** A source reference in brackets, "(nba/daily/settle.py:264-418)", means nothing to a reader; Analyst keeps it. */
function stripPaths(t: string): string {
  return t.replace(/\s*\((?:nba|app|docs)\/[^)]*\)/g, "");
}

function hiddenCols(analyst: boolean): readonly Column<ScoreRow>[] {
  return [
    MEASURE,
    ROW,
    { key: "n", head: "n rows", cell: (r) => count(r.n), sort: (r) => r.n, num: true, first: "descending" },
    { key: "d", head: "n dates", cell: (r) => count(r.nDates), sort: (r) => r.nDates, num: true, first: "descending" },
    LOCKED,
    { key: "why", head: "Hidden until its look, because", wide: true, cell: (r) => <span class="small">{withheldText(r.withheld, analyst)}</span> },
  ];
}


/** Simple: the method behind one "how this is measured" disclosure. Analyst: laid out as its own card. */
function Method(props: { analyst: boolean; children: ComponentChildren }) {
  if (props.analyst) {
    return (
      <Card kicker="Method" title="Which rows these scores follow" class="pb-method" id="sc-rule">
        {props.children}
      </Card>
    );
  }
  return (
    <details class="explain pb-how" id="sc-rule">
      <summary>How this is measured: which rows these scores follow</summary>
      <div class="explain-body">{props.children}</div>
    </details>
  );
}

function Body({ s, market, analyst }: { s: Scorecard; market: boolean; analyst: boolean }) {
  const say = (t: string | null | undefined): string | null => (t && (market || !hasPriceWords(t)) ? t : null);
  if (!s.available) {
    return (
      <StateBlock kind="empty" title="No scores to show">
        <p>{s.reason ?? "This data carries no settled scores."}</p>
        <p class="small muted">That is a state of the data, not a fault: the scorecard fills in once a nightly export with settled games is served.</p>
      </StateBlock>
    );
  }
  const rows = scoreRows(s.targets);
  const hiddenNotes = s.not_available.filter((x) => !say(`${x.metric} ${x.needs} ${x.why}`)).length;
  const hiddenSentences = s.targets.filter((t) => !say(t.sentence)).length;
  const rule = s.score_rule;
  const hiddenRows = rows.filter((r) => !r.shown);
  return (
    <>
      <section class="panel verdict pb-say" aria-labelledby="sc-say">
        <div class="kicker">In plain words</div>
        <h2 id="sc-say">Where the baseline or the market wins</h2>
        <ul class="plain pb-lines">
          {s.targets.map((t) => {
            const line = say(t.sentence);
            return line ? <li key={t.target}>{line}</li> : null;
          })}
        </ul>
        {hiddenSentences > 0 ? <p class="small muted">Further lines speak of the market side; they show in the Market lens.</p> : null}
        <p class="small pb-foot">{say(s.market.reason) ? `The market column: ${s.market.reason ?? ""}.` : "There is no market column: nothing from the market side is stored beside the scored rows."}</p>
      </section>
      <section class="panel" aria-labelledby="sc-shown">
        <SectionHead
          id="sc-shown"
          title="The scores we can show"
          sub={
            <>
              Lower is better. Each mean carries its n rows, n dates and 95% interval. <a href={termHref("crps")}>CRPS</a> · <a href={termHref("n-interval")}>n and interval</a>
            </>
          }
        />
        <Table caption="Stored scores, lower is better" rows={rows.filter((r) => r.shown)} columns={SHOWN_COLS} rowKey={(r) => r.key} noun="rows" class="sc-table sc-shown" stack />
      </section>
      <section class="panel" aria-labelledby="sc-hidden">
        <SectionHead
          id="sc-hidden"
          title="Counts only, until the look"
          sub={
            <>
              Rows under a frozen test keep their counts and say why they are hidden; no score is filled in. <a href={topHref("tests")}>Forward tests</a>
            </>
          }
        />
        {(() => {
          const table = (
            <Table
              caption={<>Counts only: rows under a frozen test, hidden until its look (<a href={topHref("tests")}>forward tests</a>)</>}
              rows={hiddenRows}
              columns={hiddenCols(analyst)}
              rowKey={(r) => r.key}
              noun="rows"
              class="sc-table sc-hidden"
              sticky
              stack
            />
          );
          return analyst ? (
            table
          ) : (
            <details class="explain sc-fold">
              <summary>Show the {count(hiddenRows.length)} rows kept back for frozen tests</summary>
              {table}
            </details>
          );
        })()}
      </section>
      <section class="panel" aria-labelledby="sc-na">
        <SectionHead id="sc-na" title="What we cannot show yet, and why" sub="Each item names the stored field it would need." />
        {(() => {
          const list = (
            <ul class="plain pb-needs">
              {s.not_available.map((x) => {
                const ok = say(`${x.metric} ${x.needs} ${x.why}`);
                return ok ? (
                  <li key={x.metric}>
                    <strong>{x.metric}</strong>
                    <span>
                      Needs {analyst ? x.needs : stripPaths(x.needs)}. {analyst ? x.why : stripPaths(x.why)}.
                    </span>
                  </li>
                ) : null;
              })}
            </ul>
          );
          return analyst ? (
            list
          ) : (
            <details class="explain sc-fold">
              <summary>Show the {count(s.not_available.length - hiddenNotes)} things we cannot show yet</summary>
              {list}
            </details>
          );
        })()}
        {hiddenNotes > 0 ? <p class="small muted">Further items are about the market side; they are listed in the Market lens.</p> : null}
      </section>
      <Method analyst={analyst}>
        <p>
          Rule: <span class="mono">{rule.rule}</span>. {rule.t60_filter ?? ""}
        </p>
        {rule.limitation ? <p class="small">{rule.limitation}</p> : null}
        {s.lock_text ? <p class="small">{s.lock_text}</p> : null}
        {analyst ? (
          <div class="tiles pb-tiles">
            <Tile label="Stored rows read" value={count(rule.n_rows_read)} note="over all measures" />
            <Tile label="Made too late, left out" value={count(rule.n_late_excluded)} />
            <Tile label="Kept, tip-off not held" value={count(rule.n_t60_unknown)} />
            <Tile label="Seasons" value={s.seasons.join(", ") || MISSING} note={`scores as of ${s.as_of ? stamp(s.as_of) : MISSING}`} />
          </div>
        ) : (
          <p class="small num">
            Over all measures: {count(rule.n_rows_read)} stored rows read, {count(rule.n_late_excluded)} made too late and left out, {count(rule.n_t60_unknown)} kept whose
            tip-off this data does not hold. Seasons {s.seasons.join(", ") || MISSING}; scores as of {s.as_of ? stamp(s.as_of) : MISSING}.
          </p>
        )}
        {analyst ? (
          <>
            <p class="small">{s.method}</p>
            <p class="small">{s.population}</p>
          </>
        ) : (
          <details class="explain">
            <summary>How the means and intervals are made</summary>
            <div class="explain-body">
              <p class="small">{s.method}</p>
              <p class="small">{s.population}</p>
            </div>
          </details>
        )}
        <p class="small">
          <a class="nowrap" href={termHref("crps")}>CRPS</a> · <a class="nowrap" href={termHref("n-interval")}>n and clustered interval</a> · <a class="nowrap" href={termHref("prereg")}>Frozen rule</a>
        </p>
      </Method>
    </>
  );
}

export function ScorecardPage() {
  const market = useLens() === "market";
  const analyst = useMode() === "analyst";
  const [state, reload] = useLoad(() => honestyApi.scorecard(), []);
  return (
    <div class="stack pb-page">
      <PageHead
        kicker="Track record"
        title="Scorecard: the model against its baseline"
        description="Lower scores are better. A row we may not show yet keeps its counts and says when it opens; nothing is filled in."
        actions={<PageControls lens />}
      />
      <OnThisPage />
      <View state={state} reload={reload} what="the scorecard" errorLevel={2}>
        {(m: Maybe<Scorecard>) =>
          m.kind === "absent" ? (
            <StateBlock kind="empty" title="Not in this copy of the data">
              <p>{m.why}</p>
            </StateBlock>
          ) : (
            <Body s={m.data} market={market} analyst={analyst} />
          )
        }
      </View>
    </div>
  );
}
