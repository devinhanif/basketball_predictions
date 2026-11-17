// The error boundary: a render error anywhere below shows the failure screen with a reference id and a way home, never a
// blank page. App mounts one around everything (`outer`, which brings its own <main> because the shell may be what
// failed) and one around the page, which clears itself when the route changes (`resetKey`) without remounting the page.
import { Component, type ComponentChildren } from "preact";
import { ProblemPanel } from "./Problem";

/** A short id to quote: made on this device, written to the console with the error. */
export function localRef(now: number = Date.now(), r: number = Math.random()): string {
  return `w-${now.toString(36)}-${Math.floor(r * 36 ** 4).toString(36).padStart(4, "0")}`;
}

interface State {
  ref: string | null;
}

export class Boundary extends Component<{ children: ComponentChildren; outer?: boolean; resetKey?: string }, State> {
  override state: State = { ref: null };

  override componentDidUpdate(prev: { resetKey?: string }): void {
    if (prev.resetKey !== this.props.resetKey && this.state.ref !== null) this.setState({ ref: null });
  }

  override componentDidCatch(error: unknown): void {
    const ref = localRef();
    console.error(`parlayhitters: page error ${ref}`, error);
    this.setState({ ref });
  }

  render() {
    if (this.state.ref === null) return <>{this.props.children}</>;
    const panel = (
      <ProblemPanel
        code="unexpected_error"
        requestId={this.state.ref}
        retry={() => {
          this.setState({ ref: null });
        }}
      />
    );
    return this.props.outer ? (
      <main id="main" tabIndex={-1} class="stack">
        {panel}
      </main>
    ) : (
      panel
    );
  }
}
