// #/bet: the betting tools hub. What the tools are and are not, the money-safety note, how they fit together (three steps),
// the ways in (each a real link with its next step), the state of the workspace as tiles, and export/import and the
// labelled demo (through the slip's own tools). The demo loads only when the button is pressed.
import { PageHead, Skeleton, Tile } from "../ui";
import { Icon, type IconName } from "../icons";
import { count } from "../format";
import { TITLE_HOME } from "./copy";
import { activeLegs, evaluateDraft } from "./slipModel";
import { MarketGate, MoneyNote, money } from "./parts";
import { SlipTools } from "./Slip";
import { ensureBetStyles, useBet, useNow } from "./useBet";

const DOORS: readonly { href: string; icon: IconName; title: string; text: string; next: string }[] = [
  { href: "#/bet/compare", icon: "swap", title: "Compare venues", text: "Tonight's props with stored prices, or a market you type. Each venue's price side by side, by name, never ranked.", next: "Pick a prop" },
  { href: "#/bet/slip", icon: "picks", title: "Your rolling slip", text: "Picks waiting for a price, planned legs and the positions you already placed: cost and every listed outcome.", next: "Open the slip" },
  { href: "#/bet/hedge", icon: "hedge", title: "Slip Hedge Finder", text: "Start from a position you recorded as placed; the other side is sized at each venue to cover its gross payout.", next: "Size a hedge" },
  { href: "#/bet/ideas", icon: "book", title: "Saved ideas", text: "Slips saved with a thesis, prices frozen when saved. Come back after the game and record what happened.", next: "See your ideas" },
];

const STEPS: readonly { title: string; text: string; href: string; link: string }[] = [
  { title: "Find a line", text: "On a game or player page (Market lens), press \"+ Slip\" beside a line. It goes in with no price.", href: "#/", link: "Go to Discover" },
  { title: "Compare and add", text: "Compare shows each venue's stored or typed price. Add the one you mean to use; it is frozen at that price.", href: "#/bet/compare", link: "Open Compare" },
  { title: "Record and hedge", text: "Mark what you placed elsewhere (a local note), then size the other side in the Hedge Finder. Save the slip as an idea to review later.", href: "#/bet/slip", link: "Open the slip" },
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
          description="Tools for arithmetic on prices you see elsewhere: compare venues, keep a slip, record what you already placed, and size a hedge."
        />
        <p class="ph-bet-lede">
          They never estimate a chance or say what to bet; the model's own forecasts stay on the game and player pages. Until the live record proves skill, keeping your money is a fine answer.
        </p>
        <MoneyNote arithmetic />
        <ol class="ph-bet-steps" aria-label="How this works">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span class="ph-bet-step-n num" aria-hidden="true">
                {i + 1}
              </span>
              <div>
                <strong>{s.title}</strong>
                <p class="small">{s.text}</p>
                <a class="small tap" href={s.href}>
                  {s.link}
                </a>
              </div>
            </li>
          ))}
        </ol>
        <nav class="ph-bet-hub" aria-label="Betting tools">
          {DOORS.map((d) => (
            <a key={d.href} href={d.href}>
              <span class="ph-bet-hub-icon" aria-hidden="true">
                <Icon name={d.icon} />
              </span>
              <strong>{d.title}</strong>
              <span class="small">{d.text}</span>
              <span class="ph-bet-hub-next small">{d.next} →</span>
            </a>
          ))}
        </nav>
        <section aria-labelledby="ph-bet-ws-h">
          <h2 class="ph-bet-h" id="ph-bet-ws-h">
            Your workspace
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
            </>
          )}
        </section>
      </section>
    </MarketGate>
  );
}
