import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { LoadState } from "./useLoad";
import { num, pct } from "./format";
import { clearLedger, lastWeek, recordOf, streak, type Settled } from "./ledger";
import { MIN_LEGS } from "./stats";
import { teamColor } from "./teams";
import type { PropView, Team, WinProbability } from "./types";
import { STAT_LABEL } from "./types";
import { errorCopy } from "./errors";
import type { Route } from "./router";
import { FOOTER_DISCLOSURE } from "./config";
import { lineMarkLabel, winSentence } from "./words";
import { plainModel, plainModelSentence } from "./plain";

/* ------------------------------------------------------------------ icons */
const I = {
  today: "M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z",
  swipe: "M4 7h16v10H4zM8 3h8M8 21h8",
  picks: "M5 4h14v16H5zM8.5 9h7M8.5 13h7M8.5 17h4",
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
      <rect width="64" height="64" rx="10" fill="#0f1115" />
      <rect x="14" y="36" width="9" height="14" rx="1" fill="#f3f3f0" />
      <rect x="27.5" y="20" width="9" height="30" rx="1" fill="#ff7a1a" />
      <rect x="41" y="29" width="9" height="21" rx="1" fill="#f3f3f0" />
    </svg>
  );
}

/* ------------------------------------------------------------------ shell */
const NAV: { name: Route["name"]; href: string; label: string; icon: string }[] = [
  { name: "today", href: "#/", label: "Today", icon: I.today },
  { name: "swipe", href: "#/swipe", label: "Swipe", icon: I.swipe },
  { name: "picks", href: "#/picks", label: "My picks", icon: I.picks },
];

function isCurrent(route: Route, name: Route["name"]): boolean {
  if (name === "today") return route.name === "today" || route.name === "game" || route.name === "leg" || route.name === "parlay";
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
          problem, call <strong class="nowrap">1-800-GAMBLER</strong>.
        </p>
        <p>Our forecasts can be wrong. A forecast is a chance, not a promise. Past results do not predict what comes next.</p>
        <p>{FOOTER_DISCLOSURE}</p>
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
      <a
        class="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
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
            You are offline, or the data server is not answering. Showing the last copy we saved.
          </div>
        ) : null}
      </header>
      <main id="main" tabIndex={-1}>{props.children}</main>
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
  if (/^404 /.test(props.message)) {
    return (
      <div class="panel error" role="alert">
        <h1>Not in the loaded data</h1>
        <p>
          That page points to something this data does not hold (a game, player or stat that is not in the current export).
          The server is fine.
        </p>
        <p class="muted small">
          Technical detail: <span class="mono">{props.message}</span>
        </p>
        <a class="btn" href="#/">
          Back to today
        </a>
      </div>
    );
  }
  const copy = errorCopy(props.message, import.meta.env.DEV);
  return (
    <div class="panel error" role="alert">
      <h1>{copy.title}</h1>
      <p>{copy.body}</p>
      {copy.reason ? (
        <p>
          Reason: <span class="mono">{copy.reason}</span>
        </p>
      ) : null}
      {copy.devHint ? (
        <p class="muted small">
          Development build: start the API with <code>uv run uvicorn parlayhitters_api.main:app --app-dir src</code> in{" "}
          <code>app/api</code>.
        </p>
      ) : null}
      <details class="explain">
        <summary>Technical detail</summary>
        <div class="explain-body">
          <span class="mono">{props.message}</span>
        </div>
      </details>
      <button type="button" class="btn" onClick={props.retry}>
        Try again
      </button>
    </div>
  );
}

/** A small "what does this mean?" line. Closed by default; the main text never needs it. */
export function Explain(props: { children: ComponentChildren; label?: string }) {
  return (
    <details class="explain">
      <summary>{props.label ?? "What does this mean?"}</summary>
      <div class="explain-body">{props.children}</div>
    </details>
  );
}

/**
 * Where the forecasts on a page came from, said once in plain words, with the exact stored wording one tap away.
 * `items` is every (source, verbatim label) on the page; repeats are folded.
 */
export function ModelNote(props: { items: readonly { source: string; label: string }[] }) {
  const bySource = new Map<string, string>();
  for (const i of props.items) if (!bySource.has(i.source)) bySource.set(i.source, i.label);
  const sources = [...bySource.keys()];
  const [first, ...rest] = sources;
  if (first === undefined) return null;
  const sentence =
    rest.length === 0
      ? plainModelSentence(first)
      : `Forecasts come from ${plainModel(first)}; for some players, from ${rest.map(plainModel).join(" and ")}.`;
  return (
    <div class="model-note">
      <p class="small muted">{sentence}</p>
      <Explain label="Show the exact details">
        <ul class="plain small">
          {[...bySource.values()].map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </Explain>
    </div>
  );
}

/** Abbreviation with its team swatch. */
export function TeamTag(props: { abbr: string; big?: boolean }) {
  return (
    <span class={`team${props.big ? " big" : ""}`}>
      <i class="swatch" style={{ background: teamColor(props.abbr) }} aria-hidden="true" />
      {props.abbr}
    </span>
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
function WpRow(props: { label: string; kind: "model" | "book"; homeP: number; home: Team; away: Team }) {
  const { home, away, homeP } = props;
  return (
    <div class={`wp-row ${props.kind}`}>
      <div class="wp-head">
        <span class="wp-lab">{props.label}</span>
        <span class="wp-say">{winSentence(homeP, home.name, away.name)}</span>
      </div>
      <div
        class="wp-bar"
        role="img"
        aria-label={`${props.label}: ${away.abbr} ${pct(1 - homeP)}, ${home.abbr} ${pct(homeP)}`}
      >
        <i style={{ flexGrow: 1 - homeP, background: teamColor(away.abbr) }} />
        <i style={{ flexGrow: homeP, background: teamColor(home.abbr) }} />
      </div>
      <div class="wp-ends num">
        <span>
          {away.abbr} {pct(1 - homeP)}
        </span>
        <span>
          {pct(homeP)} {home.abbr}
        </span>
      </div>
    </div>
  );
}

export function WinBars(props: { wp: WinProbability; home: Team; away: Team }) {
  const { wp, home, away } = props;
  if (wp.model_home === null) return <p class="muted small">We have no forecast for this game.</p>;
  return (
    <div class="wp">
      <WpRow kind="model" label="Our model" homeP={wp.model_home} home={home} away={away} />
      {wp.market_home !== null ? (
        <WpRow kind="book" label="Sportsbook odds" homeP={wp.market_home} home={home} away={away} />
      ) : null}
    </div>
  );
}

export function WinLegend() {
  return (
    <Explain>
      <p>
        Each bar splits 100% between the two teams. <strong>Our model</strong> is our own forecast, made before the game
        using only what was known then. <strong>Sportsbook odds</strong> are DraftKings' moneyline about an hour before
        tip, with the book's built-in margin taken out so the two sides add up to 100%.
      </p>
      <p>If the two bars match, the market and our model see the game the same way. If they differ, we disagree.</p>
    </Explain>
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
  const label = `Likely range for ${props.player}, ${STAT_LABEL[prop.stat]}: about half of nights between ${num(q25)} and ${num(q75)}, nine nights in ten between ${num(q05)} and ${num(q95)}, typical night ${num(q50)}; ${lineMarkLabel(prop.line_source)} ${prop.line.toString()}${actual === undefined ? "" : `; final number ${actual.toString()}`}`;
  return (
    <svg class="dist" viewBox={`0 0 ${W.toString()} ${actual === undefined ? "58" : "70"}`} role="img" aria-label={label}>
      <line class="axis" x1="10" x2={W - 10} y1="40" y2="40" />
      <rect class="band" x={x(q05)} y="30" width={Math.max(x(q95) - x(q05), 1)} height="20" />
      <rect class="iqr" x={x(q25)} y="26" width={Math.max(x(q75) - x(q25), 1)} height="28" />
      <line class="med" x1={x(q50)} x2={x(q50)} y1="26" y2="54" />
      <line class="lineMark" x1={x(prop.line)} x2={x(prop.line)} y1="14" y2="58" />
      <text class="lineText" x={x(prop.line)} y="10" text-anchor={x(prop.line) < 80 ? "start" : x(prop.line) > W - 80 ? "end" : "middle"}>
        {lineMarkLabel(prop.line_source)} {prop.line}
      </text>
      {actual !== undefined ? (
        <g>
          <polygon class="actual" points={`${x(actual).toString()},20 ${(x(actual) + 6).toString()},27 ${x(actual).toString()},34 ${(x(actual) - 6).toString()},27`} />
          <text x={x(actual)} y="68" text-anchor="middle" class="actualText">
            finished {actual}
          </text>
        </g>
      ) : null}
    </svg>
  );
}

export function DistLegend(props: { reference?: boolean }) {
  return (
    <Explain label="How do I read these bars?">
      <p>
        Each bar is our forecast for one player's night. The dark bar is where about half of his nights land. The light
        band stretches to cover nine nights in ten. The short tick inside the dark bar is the typical night. The tall line
        that reaches above and below the bar marks{" "}
        {props.reference
          ? "the line: where no sportsbook line was usable it is a reference line at the middle of our own forecast, not a number any book set."
          : "the line, the number the sportsbook set."}
      </p>
      <p>
        A bar that sits mostly to the right of the line says "over". The wider the bar, the less sure we are. These
        forecasts assume the player suits up.
      </p>
    </Explain>
  );
}

/* ------------------------------------------------------------------ record board */
export function RecordBoard(props: { settled: Settled[]; loading: boolean; picks: number; now?: Date; compact?: boolean }) {
  const all = recordOf(props.settled);
  const week = recordOf(lastWeek(props.settled, props.now));
  const weekN = week.wins + week.losses;
  const allN = all.wins + all.losses;
  const shown = weekN > 0 ? week : all;
  const run = streak(props.settled);
  if (allN === 0) {
    return (
      <div class={`board${props.compact ? " compact" : ""}`}>
        <p class="board-line">{props.loading ? "Grading your picks..." : "No graded picks yet."}</p>
        <p class="muted small">
          {props.picks === 0
            ? "Make a few picks and your record shows up here."
            : "Your picks will be graded as soon as the results load."}
        </p>
      </div>
    );
  }
  return (
    <div class={`board${props.compact ? " compact" : ""}`}>
      <p class="board-line">
        You're{" "}
        <span class="rec num">
          {shown.wins}-{shown.losses}
        </span>{" "}
        {weekN > 0 ? "this week" : "so far"}
      </p>
      <div class="board-facts num">
        {run ? (
          <span class={`streak ${run.kind}`}>
            {run.count} {run.kind === "win" ? (run.count === 1 ? "hit" : "hits") : run.count === 1 ? "miss" : "misses"} in a row
          </span>
        ) : null}
        {weekN > 0 ? (
          <span>
            All time {all.wins}-{all.losses}
          </span>
        ) : null}
        <span>{pct(all.wins / allN)} right</span>
      </div>
      {allN < MIN_LEGS && !props.compact ? (
        <p class="muted small">Only {allN} graded so far, so luck is doing most of the work. It takes {MIN_LEGS} or more to say anything.</p>
      ) : null}
    </div>
  );
}

/** Clears every saved pick on this device. Asks once first. */
export function ResetPicks(props: { onReset: () => void }) {
  const [confirm, setConfirm] = useState(false);
  if (confirm) {
    return (
      <span class="row">
        <button
          type="button"
          class="btn primary"
          onClick={() => {
            clearLedger();
            setConfirm(false);
            props.onReset();
          }}
        >
          Yes, wipe my picks
        </button>
        <button
          type="button"
          class="btn"
          onClick={() => {
            setConfirm(false);
          }}
        >
          Keep them
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      class="btn"
      onClick={() => {
        setConfirm(true);
      }}
    >
      Reset my picks
    </button>
  );
}
