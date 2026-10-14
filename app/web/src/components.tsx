import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { LoadState } from "./useLoad";
import { num, pct } from "./format";
import type { PropView, Team, WinProbability } from "./types";
import { STAT_LABEL } from "./types";
import type { Route } from "./router";

/* ------------------------------------------------------------------ icons */
const I = {
  today: "M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z",
  swipe: "M4 7h16v10H4zM8 3h8M8 21h8",
  picks: "M5 4h14v16H5zM8.5 9h7M8.5 13h7M8.5 17h4",
  method: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 8v6m0-9.5v.5",
};

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#1f2a44" />
      <rect x="14" y="36" width="9" height="14" rx="2" fill="#9fb0ff" />
      <rect x="27.5" y="20" width="9" height="30" rx="2" fill="#f6f5f1" />
      <rect x="41" y="29" width="9" height="21" rx="2" fill="#9fb0ff" />
    </svg>
  );
}

/* ------------------------------------------------------------------ shell */
const NAV: { name: Route["name"]; href: string; label: string; icon: string }[] = [
  { name: "today", href: "#/", label: "Today", icon: I.today },
  { name: "swipe", href: "#/swipe", label: "Swipe", icon: I.swipe },
  { name: "picks", href: "#/picks", label: "My picks", icon: I.picks },
  { name: "method", href: "#/method", label: "Method", icon: I.method },
];

function isCurrent(route: Route, name: Route["name"]): boolean {
  if (name === "today") return route.name === "today" || route.name === "game" || route.name === "parlay";
  return route.name === name;
}

function effectiveDark(): boolean {
  const t = document.documentElement.dataset["theme"];
  if (t === "dark") return true;
  if (t === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function ThemeToggle() {
  const [dark, setDark] = useState(effectiveDark);
  return (
    <button
      type="button"
      class="icon-btn"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => {
        const next = dark ? "light" : "dark";
        document.documentElement.dataset["theme"] = next;
        try {
          localStorage.setItem("ph.theme", next);
        } catch {
          /* storage blocked: the choice lasts for this visit only */
        }
        setDark(!dark);
      }}
    >
      {dark ? "Light" : "Dark"}
    </button>
  );
}

export function Footer() {
  return (
    <footer class="footer">
      <div class="footer-in">
        <p>
          <strong>Analysis only, not advice.</strong> We do not take bets or sell picks. 21+ (US). If gambling is a
          problem, call <strong>1-800-GAMBLER</strong>.
        </p>
        <p>
          Forecasts are uncertain, and the market is usually better than we are on game winners.{" "}
          <a href="#/method">Method and responsible gambling</a>.
        </p>
        <p>Written by an automated system and reviewed by a person. Everything here is a paper trade.</p>
      </div>
    </footer>
  );
}

export function Shell(props: {
  route: Route;
  replayNote: string | null;
  offline: boolean;
  children: ComponentChildren;
}) {
  return (
    <div class="shell">
      <header class="topbar">
        <div class="topbar-in">
          <a class="brand" href="#/" aria-label="parlayhitters, home">
            <Logo />
            parlayhitters
          </a>
          <nav class="nav-desktop" aria-label="Main">
            {NAV.map((n) => (
              <a key={n.name} href={n.href} aria-current={isCurrent(props.route, n.name) ? "page" : undefined}>
                {n.label}
              </a>
            ))}
          </nav>
          <span class="spacer" />
          <ThemeToggle />
        </div>
        {props.replayNote ? <div class="banner">{props.replayNote}</div> : null}
        {props.offline ? (
          <div class="banner" role="status">
            Offline or API down: showing the last saved copy.
          </div>
        ) : null}
      </header>
      <main id="main">{props.children}</main>
      <Footer />
      <nav class="nav-bottom" aria-label="Main">
        {NAV.map((n) => (
          <a key={n.name} href={n.href} aria-current={isCurrent(props.route, n.name) ? "page" : undefined}>
            <Icon d={n.icon} />
            {n.label}
          </a>
        ))}
      </nav>
    </div>
  );
}

/* ------------------------------------------------------------------ load states */
export function Loading() {
  return (
    <div class="stack" aria-busy="true" aria-live="polite">
      <div class="skeleton" />
      <div class="skeleton" />
      <span class="sr">Loading</span>
    </div>
  );
}

export function ErrorPanel(props: { message: string; retry: () => void }) {
  return (
    <div class="card error" role="alert">
      <h2>Could not load this</h2>
      <p class="muted">
        The local API may not be running (<code>uv run uvicorn parlayhitters_api.main:app</code> in app/api). Detail:{" "}
        <span class="mono">{props.message}</span>
      </p>
      <button type="button" class="btn" onClick={props.retry}>
        Try again
      </button>
    </div>
  );
}

export function View<T>(props: {
  state: LoadState<T>;
  reload: () => void;
  onStale?: (stale: boolean) => void;
  children: (data: T) => ComponentChildren;
}) {
  const { state, onStale } = props;
  const stale = state.status === "ready" && state.fromCache;
  useEffect(() => {
    onStale?.(stale);
  }, [stale, onStale]);
  if (state.status === "loading") return <Loading />;
  if (state.status === "error") return <ErrorPanel message={state.message} retry={props.reload} />;
  return <>{props.children(state.data)}</>;
}

/* ------------------------------------------------------------------ win probability */
function WpRow(props: { kind: "model" | "market"; label: string; homeP: number; home: string; away: string }) {
  const homePct = props.homeP * 100;
  const homeFav = props.homeP >= 0.5;
  const fav = homeFav ? props.home : props.away;
  const favP = homeFav ? props.homeP : 1 - props.homeP;
  return (
    <div class="wp-row">
      <span class="lab">{props.label}</span>
      <div
        class={`wp-bar ${props.kind}`}
        role="img"
        aria-label={`${props.label}: ${props.home} ${pct(props.homeP)}, ${props.away} ${pct(1 - props.homeP)}`}
      >
        <i style={{ width: `${homePct.toFixed(1)}%` }} />
      </div>
      <span class="num">
        {fav} {pct(favP)}
      </span>
    </div>
  );
}

export function WinBars(props: { wp: WinProbability; home: Team; away: Team }) {
  const { wp, home, away } = props;
  if (wp.model_home === null) return <p class="muted small">No stored forecast for this game.</p>;
  return (
    <div class="wp">
      <WpRow kind="model" label="Model" homeP={wp.model_home} home={home.abbr} away={away.abbr} />
      {wp.market_home !== null ? (
        <WpRow kind="market" label="Market" homeP={wp.market_home} home={home.abbr} away={away.abbr} />
      ) : null}
    </div>
  );
}

export function WinLegend() {
  return (
    <p class="small muted" style={{ margin: 0 }}>
      Bars fill from the left for the home team (the second team named); the notch is 50%.{" "}
      <span class="tag forecast">model</span> out-of-fold forecast. <span class="tag market">market</span> DraftKings
      moneyline, de-vigged, about an hour before tip.
    </p>
  );
}

/* ------------------------------------------------------------------ distribution bar */
export function DistBar(props: { player: string; prop: PropView; actual?: number | undefined }) {
  const { prop, actual } = props;
  const q = prop.q;
  const q05 = q[0] ?? 0;
  const q25 = q[4] ?? q05;
  const q50 = q[9] ?? q25;
  const q75 = q[14] ?? q50;
  const q95 = q[18] ?? q75;
  const vals = [q05, q95, prop.line, ...(actual === undefined ? [] : [actual])];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const span = Math.max(hi - lo, 1);
  lo -= span * 0.06;
  hi += span * 0.06;
  const W = 320;
  const x = (v: number): number => 10 + ((v - lo) / (hi - lo)) * (W - 20);
  const label = `Forecast range for ${props.player}, ${STAT_LABEL[prop.stat]}: 5th percentile ${num(q05)}, median ${num(q50)}, 95th percentile ${num(q95)}; line ${prop.line}${actual === undefined ? "" : `; actual ${actual}`}`;
  return (
    <svg class="dist" viewBox={`0 0 ${W} ${actual === undefined ? 58 : 70}`} role="img" aria-label={label}>
      <line class="axis" x1="10" x2={W - 10} y1="40" y2="40" />
      <rect class="band" x={x(q05)} y="30" width={Math.max(x(q95) - x(q05), 1)} height="20" rx="4" />
      <rect class="iqr" x={x(q25)} y="26" width={Math.max(x(q75) - x(q25), 1)} height="28" rx="4" />
      <line class="med" x1={x(q50)} x2={x(q50)} y1="26" y2="54" />
      <line class="lineMark" x1={x(prop.line)} x2={x(prop.line)} y1="14" y2="58" />
      <text class="lineText" x={x(prop.line)} y="10" text-anchor="middle">
        line {prop.line}
      </text>
      {actual !== undefined ? (
        <g>
          <polygon class="actual" points={`${x(actual)},20 ${x(actual) + 6},27 ${x(actual)},34 ${x(actual) - 6},27`} />
          <text x={x(actual)} y="68" text-anchor="middle" style={{ fill: "var(--ink)", fontWeight: 700 }}>
            actual {actual}
          </text>
        </g>
      ) : null}
    </svg>
  );
}

export function DistLegend() {
  return (
    <p class="small muted" style={{ margin: 0 }}>
      Light band: the model's 5th to 95th percentile. Dark bar: 25th to 75th. White tick: median. Orange line: the book's
      line.
    </p>
  );
}
