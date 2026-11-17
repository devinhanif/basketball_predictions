// The forward-test tracker (#/tests; forward_tests_v1): each frozen test, what it asks, when it reads, the n it needs
// (the rule's own words) and the stored rows counted so far. Never an outcome: the route carries none, and the page says
// so for every test.
import { View } from "../components";
import { count, MISSING } from "../format";
import { hasPriceWords, honestyApi, type ForwardTest, type ForwardTests, type Maybe } from "../honesty";
import { LensSwitch } from "../Lens";
import { useLens } from "../prefs";
import { topHref } from "../router";
import { termHref } from "../terms";
import { SoWhat } from "../ui";
import { useLoad } from "../useLoad";

/** Is everything a test says free of price words (so it may show in the Learn lens)? */
export function learnSafeTest(t: ForwardTest): boolean {
  return ![t.title, t.scheduled_read, t.n_needed, t.source, t.n_so_far_reason].some((x) => hasPriceWords(x));
}

function SoFar({ t }: { t: ForwardTest }) {
  if (t.n_so_far === null) {
    return (
      <span>
        Count not exported. <span class="small muted">{t.n_so_far_reason ?? "No reason given."}</span>
      </span>
    );
  }
  return (
    <ul class="plain">
      {Object.entries(t.n_so_far).map(([arm, c]) => (
        <li key={arm} class="num">
          <span class="mono">{arm}</span>: {count(c.scored)} scored rows over {count(c.dates)} dates
          {c.dnp !== null ? `, ${count(c.dnp)} did not play` : ""}
        </li>
      ))}
      {t.n_so_far_reason ? <li class="small muted">{t.n_so_far_reason}</li> : null}
    </ul>
  );
}

function TestCard({ t }: { t: ForwardTest }) {
  return (
    <article class="panel test-card" aria-labelledby={`t-${t.id}`}>
      <h2 id={`t-${t.id}`}>{t.title}</h2>
      <p class="small muted mono wrap-any">{t.id}</p>
      <dl>
        <dt>Reads</dt>
        <dd>{t.scheduled_read ?? MISSING}</dd>
        <dt>n needed</dt>
        <dd>{t.n_needed ?? MISSING}</dd>
        <dt>n so far</dt>
        <dd>
          <SoFar t={t} />
        </dd>
        <dt>Outcome</dt>
        <dd>
          <strong>Hidden until the look.</strong> The result is published at the scheduled read, by the project, not here.
        </dd>
        <dt>Rule</dt>
        <dd class="small wrap-any">{t.source}</dd>
      </dl>
    </article>
  );
}

function Body({ f, market }: { f: ForwardTests; market: boolean }) {
  const shown = market ? f.tests : f.tests.filter(learnSafeTest);
  const hidden = shown.length < f.tests.length;
  return (
    <>
      <section class="panel flat">
        <p>{f.note}</p>
        <p class="small">
          <strong>How "n so far" is counted:</strong> {f.count_rule}
        </p>
        <p class="small muted">
          List as of {f.as_of ?? MISSING}. {f.source}
        </p>
      </section>
      {hidden ? <p class="small muted">Further tests are about the market side; they are listed in the Market lens.</p> : null}
      {shown.length === 0 ? (
        <section class="panel empty">
          <h2>No tests to list here</h2>
          <p>The data carries no frozen tests for this lens.</p>
        </section>
      ) : (
        <div class="test-list">
          {shown.map((t) => (
            <TestCard key={t.id} t={t} />
          ))}
        </div>
      )}
    </>
  );
}

export function TestsPage() {
  const market = useLens() === "market";
  const [state, reload] = useLoad(() => honestyApi.forwardTests(), []);
  return (
    <div class="stack">
      <header class="page-head">
        <div class="kicker">Track record</div>
        <h1>Forward tests: what is being tested, and when it reads</h1>
      </header>
      <SoWhat>
        Each test's rule was frozen before any result existed. No outcome is shown here until its scheduled look. <a href={termHref("prereg")}>Frozen rule</a> ·{" "}
        <a href={topHref("scorecard")}>Scorecard</a>
      </SoWhat>
      <LensSwitch compact />
      <View state={state} reload={reload} what="the forward tests">
        {(m: Maybe<ForwardTests>) =>
          m.kind === "absent" ? (
            <section class="panel empty">
              <h2>Not in this copy of the data</h2>
              <p>{m.why}</p>
            </section>
          ) : (
            <Body f={m.data} market={market} />
          )
        }
      </View>
    </div>
  );
}
