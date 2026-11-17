// One failure screen for the whole app: the registry's plain words for a code (title, what happened, what to do), the
// server's reason when it gave one, the code and a reference id to quote, and always a way on. Built from existing
// classes only (panel error, btn, mono, explain); the design lane owns how it looks.
import { codeCopy } from "./errorCodes";
import { helpHref, topHref } from "./router";

export interface ProblemProps {
  code: string;
  /** The server's own reason (a 503 refusal says why); shown verbatim. */
  reason?: string | null;
  /** The id to quote when reporting it: the server's request id, or one made here. */
  requestId?: string | null;
  /** Developer detail (status line and path), behind a closed "Technical detail". */
  detail?: string | null;
  retry?: (() => void) | null;
  /** Extra sentence for this screen (for example the address that was asked for). */
  note?: string | null;
  /** Heading level: a page-level failure is the page's h1; a failure inside a section is an h2. */
  level?: 1 | 2;
}

export function ProblemPanel(props: ProblemProps) {
  const c = codeCopy(props.code);
  const Heading = props.level === 2 ? "h2" : "h1";
  return (
    <div class="panel error" role="alert" data-code={props.code}>
      <Heading>{c.title}</Heading>
      <p>{c.what}</p>
      {props.reason ? (
        <p>
          Reason: <span class="mono">{props.reason}</span>
        </p>
      ) : null}
      {props.note ? <p class="small muted">{props.note}</p> : null}
      <p>{c.todo}</p>
      <p class="small muted">
        Code{" "}
        <a class="mono" href={helpHref(props.code)} title="What this code means">
          {props.code}
        </a>
        {props.requestId ? (
          <>
            {" "}
            · reference <span class="mono">{props.requestId}</span>
          </>
        ) : null}
      </p>
      <details class="explain">
        <summary>Why this happens</summary>
        <div class="explain-body">
          <p class="small">{c.why}</p>
          {props.detail ? <p class="small mono">{props.detail}</p> : null}
        </div>
      </details>
      <p class="row wrap">
        {props.retry ? (
          <button type="button" class="btn" onClick={props.retry}>
            Try again
          </button>
        ) : null}
        <a class="btn" href={topHref("today")}>
          Back to Discover
        </a>
      </p>
    </div>
  );
}

/** The designed screen for an address the app could not have linked to. */
export function NotFound(props: { hash: string }) {
  const asked = props.hash.length > 120 ? `${props.hash.slice(0, 120)}...` : props.hash;
  return <ProblemPanel code="bad_route" note={asked ? `The address asked for: ${asked}` : null} />;
}
