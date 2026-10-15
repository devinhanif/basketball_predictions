/**
 * Scroll behaviour for a hash router (F11). Following a link starts at the top; browser Back (or Forward)
 * returns to where that page was left. The browser cannot do this itself because every page here is built
 * after a fetch, so we remember the position per hash and restore it once the page is tall enough.
 */
export type NavAction = { kind: "top" } | { kind: "restore"; y: number };

export class ScrollNav {
  private positions = new Map<string, number>();
  private current: string;
  private forward = false;
  /** While a restore is pending the page is shorter than before; do not record the clamped position. */
  private restoring = false;

  constructor(initialHash: string) {
    this.current = initialHash;
  }

  /** A click on an in-app link: the next hash change is a forward navigation. */
  linkClicked(): void {
    this.forward = true;
  }

  onScroll(y: number): void {
    if (!this.restoring) this.positions.set(this.current, y);
  }

  /** Called on every hash change with the new hash. */
  onHashChange(next: string): NavAction {
    const wasForward = this.forward;
    this.forward = false;
    this.current = next;
    const saved = this.positions.get(next);
    if (wasForward || saved === undefined || saved <= 0) {
      this.restoring = false;
      return { kind: "top" };
    }
    this.restoring = true;
    return { kind: "restore", y: saved };
  }

  /** The restore finished (reached the position, or gave up / the user scrolled). */
  restoreDone(): void {
    this.restoring = false;
  }
}

/** Try to scroll to `y` until the page is tall enough or `maxMs` pass; returns a cancel function. */
export function restoreScroll(y: number, done: () => void, maxMs = 3000): () => void {
  let stopped = false;
  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    window.clearInterval(timer);
    window.removeEventListener("wheel", stop);
    window.removeEventListener("touchstart", stop);
    window.removeEventListener("keydown", stop);
    done();
  };
  const t0 = performance.now();
  const timer = window.setInterval(() => {
    const room = document.documentElement.scrollHeight - window.innerHeight;
    if (room >= y - 1) {
      window.scrollTo(0, y);
      stop();
    } else if (performance.now() - t0 > maxMs) {
      window.scrollTo(0, Math.max(0, room));
      stop();
    }
  }, 50);
  window.addEventListener("wheel", stop, { passive: true });
  window.addEventListener("touchstart", stop, { passive: true });
  window.addEventListener("keydown", stop);
  return stop;
}
