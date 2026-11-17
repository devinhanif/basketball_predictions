// The forward-test tracker (#/tests; forward_tests_v1): each frozen test, what it asks, when it reads, the n it needs
// (the rule's own words) and the stored rows counted so far. Never an outcome: the route carries none, and the page says
// so for every test.
import { View } from "../components";
import { count, MISSING } from "../format";
import { hasPriceWords, honestyApi, type ForwardTest, type ForwardTests, type Maybe } from "../honesty";
import { PageControls } from "../Lens";
import { useLens, useMode } from "../prefs";
import { topHref } from "../router";
import { termHref } from "../terms";
import { Badge, OnThisPage, PageHead, StateBlock } from "../ui";
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

function TestCard({ t, analyst }: { t: ForwardTest; analyst: boolean }) {
  return (
    <article class="panel pb-test test-card" aria-labelledby={`t-${t.id}`}>
      <div class="card-head">
        <div class="card-head-text">
          <h2 id={`t-${t.id}`}>{t.title}</h2>
          <p class="small muted mono pb-test-id">{t.id}</p>
        </div>
        <div class="card-head-actions">
          <Badge tone="none" icon="lock" word="Outcome hidden until the look" />
        </div>
      </div>
      <dl>
        <div>
          <dt>Reads</dt>
          <dd>{t.scheduled_read ?? MISSING}</dd>
        </div>
        <div>
          <dt>n needed</dt>
          <dd>{t.n_needed ?? MISSING}</dd>
        </div>
        <div>
          <dt>n so far</dt>
          <dd>
            <SoFar t={t} />
          </dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>
            <strong>Hidden until the look.</strong> The result is published at the scheduled read, by the project, not here.
          </dd>
        </div>
      </dl>
      {analyst ? (
        <p class="small wrap-any pb-rule">
          <span class="muted">Rule: </span>
          {t.source}
        </p>
      ) : (
        <details class="explain pb-rule">
          <summary>Where the frozen rule is written</summary>
          <div class="explain-body small wrap-any">{t.source}</div>
        </details>
      )}
    </article>
  );
}

function Body({ f, market, analyst }: { f: ForwardTests; market: boolean; analyst: boolean }) {
  const shown = market ? f.tests : f.tests.filter(learnSafeTest);
  const hidden = shown.length < f.tests.length;
  const how = (
    <>
      <p class="small">
        <strong>How "n so far" is counted:</strong> {f.count_rule}
      </p>
      <p class="small muted">
        List as of {f.as_of ?? MISSING}. {f.source}
      </p>
    </>
  );
  return (
    <>
      <section class="panel verdict" aria-labelledby="ft-what">
        <div class="kicker">In plain words</div>
        <h2 id="ft-what">What this list is</h2>
        <p>{f.note}</p>
        {analyst ? (
          <div class="pb-foot">{how}</div>
        ) : (
          <details class="explain">
            <summary>How this is measured</summary>
            <div class="explain-body">{how}</div>
          </details>
        )}
      </section>
      {hidden ? <p class="small muted">Further tests are about the market side; they are listed in the Market lens.</p> : null}
      {shown.length === 0 ? (
        <StateBlock kind="empty" title="No tests to list here">
          The data carries no frozen tests for this lens.
        </StateBlock>
      ) : (
        <div class="test-list pb-tests">
          {shown.map((t) => (
            <TestCard key={t.id} t={t} analyst={analyst} />
          ))}
        </div>
      )}
    </>
  );
}

export function TestsPage() {
  const market = useLens() === "market";
  const analyst = useMode() === "analyst";
  const [state, reload] = useLoad(() => honestyApi.forwardTests(), []);
  return (
    <div class="stack pb-page">
      <PageHead
        kicker="Track record"
        title="Forward tests: what is being tested, and when it reads"
        description={
          <>
            Each test's rule was frozen before any result existed. No outcome is shown here until its scheduled look. <a href={termHref("prereg")}>Frozen rule</a> ·{" "}
            <a href={topHref("scorecard")}>Scorecard</a>
          </>
        }
        actions={<PageControls lens />}
      />
      <OnThisPage />
      <View state={state} reload={reload} what="the forward tests">
        {(m: Maybe<ForwardTests>) =>
          m.kind === "absent" ? (
            <StateBlock kind="empty" title="Not in this copy of the data">
              <p>{m.why}</p>
            </StateBlock>
          ) : (
            <Body f={m.data} market={market} analyst={analyst} />
          )
        }
      </View>
    </div>
  );
}
