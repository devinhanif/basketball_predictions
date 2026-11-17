// #/bet: the betting tools hub. What the tools are and are not, the money-safety note, how they fit together (three steps),
// the ways in (each a real link with its next step), the state of the workspace as tiles, and export/import and the
// labelled demo (through the slip's own tools). The demo loads only when the button is pressed.
import { PageHead, Skeleton, Tile } from "../ui";
import { count } from "../format";
import { TITLE_HOME } from "./copy";
import { activeLegs, evaluateDraft } from "./slipModel";
import { MarketGate, MoneyNote, money } from "./parts";
import { SlipTools } from "./Slip";
import { ensureBetStyles, useBet, useNow } from "./useBet";

const STEPS: readonly { href: string; title: string; text: string; next: string }[] = [
  { href: "#/bet/compare", title: "Pick a prop and compare", text: "Each venue's stored or typed price side by side. Press \"+ Slip\" beside any line on a game or player page (Market lens) to start from there.", next: "Compare venues" },
  { href: "#/bet/slip", title: "Build your slip", text: "Add the price you mean to use; it is frozen. See cost and every listed outcome, then mark what you placed elsewhere.", next: "Open the slip" },
  { href: "#/bet/hedge", title: "Size a hedge", text: "From a position you recorded as placed, size the other side at each venue.", next: "Open the Hedge Finder" },
  { href: "#/bet/ideas", title: "Save it as an idea", text: "Keep the slip with a thesis, then record what happened after the game.", next: "See saved ideas" },
];

export function BetHome() {
  ensureBetStyles();
  const bet = useBet();
  const now = useNow();
  const ws = bet.ready ? bet.view.workspace : null;
  const planned = ws?.draft.legs.filter((l) => l.role === "planned") ?? [];
  const recorded = ws?.draft.legs.filter((l) => l.role === "existing") ?? [];
  const ev = ws ? evaluateDraft(ws.draft, { now }) : null;
  return (
    <MarketGate name="Betting tools" what="The betting tools work on prices.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-home-h">
        <PageHead
          id="ph-bet-home-h"
          kicker="Market tools"
          title={TITLE_HOME}
          description="Tools for arithmetic on prices you see elsewhere: compare venues, keep a slip, record what you placed, size a hedge. They never estimate a chance or say what to bet."
        />
        <MoneyNote arithmetic />
        {ws ? (
          <p class="ph-bet-glance" role="status">
            <strong>Your slip:</strong> {activeLegs(ws.draft).length || planned.length || recorded.length || ws.picks.length ? `${count(planned.length)} planned, ${count(recorded.length)} recorded, ${count(ws.picks.length)} waiting for a price` : "empty"}
            {ev && activeLegs(ws.draft).length && ev.totalCost !== null ? <> · cost <span class="num">{money(ev.totalCost)}</span></> : null}
            {" "}
            <a class="tap" href="#/bet/slip">Open the slip</a>
          </p>
        ) : null}
        <nav class="ph-bet-hub" aria-label="Four steps">
          {STEPS.map((d, i) => (
            <a key={d.href} href={d.href}>
              <span class="ph-bet-hub-icon num" aria-hidden="true">
                {i + 1}
              </span>
              <strong>{d.title}</strong>
              <span class="small">{d.text}</span>
              <span class="ph-bet-hub-next small">{d.next} →</span>
            </a>
          ))}
        </nav>
        <section aria-labelledby="ph-bet-ws-h">
          <h2 class="ph-bet-h" id="ph-bet-ws-h">
            Your slip in numbers
          </h2>
          {!bet.ready || !ws ? (
            bet.ready && bet.view.error ? (
              <p class="ph-bet-err" role="alert">
                {bet.view.error.text}
              </p>
            ) : (
              <Skeleton lines={2} label="Loading your workspace" />
            )
          ) : (
            <>
              <div class="ph-bet-tiles">
                <Tile label="Planned legs" value={count(planned.length)} note={`${count(planned.filter((l) => l.enabled).length)} included`} />
                <Tile label="Recorded positions" value={count(recorded.length)} note="Placed elsewhere, noted here" tone="mine" />
                <Tile label="Waiting for a price" value={count(ws.picks.length)} note="Picks with no venue yet" />
                <Tile label="Saved ideas" value={count(ws.ideas.length)} note={`${count(ws.ideas.filter((i) => i.status === "settled").length)} settled`} />
                <Tile label="Slip cost" value={ev && activeLegs(ws.draft).length ? (ev.totalCost === null ? "—" : money(ev.totalCost)) : "—"} note={ev && activeLegs(ws.draft).length && ev.totalCost === null ? "Not complete: a fee or amount is unknown" : "At the prices in the slip"} />
              </div>
              {ws.draft.origin === "sample" ? <p class="small">The working slip is the labelled demo: invented sample prices.</p> : null}
              <SlipTools bet={bet} ws={ws} />
              <p class="small muted">Until the live record proves skill, keeping your money is a fine answer.</p>
            </>
          )}
        </section>
      </section>
    </MarketGate>
  );
}
