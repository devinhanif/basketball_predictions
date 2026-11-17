// #/bet: the betting tools hub. What the tools are and are not, the money-safety note, the four ways in, the state of the
// workspace (how many planned and recorded legs, where it is kept), export/import and the labelled demo (through the
// slip's own tools). The demo loads only when the button is pressed.
import { Skeleton } from "../ui";
import { count } from "../format";
import { TITLE_HOME } from "./copy";
import { MarketGate, MoneyNote } from "./parts";
import { SlipTools } from "./Slip";
import { ensureBetStyles, useBet } from "./useBet";

const DOORS: readonly { href: string; title: string; text: string }[] = [
  { href: "#/bet/compare", title: "Compare venues", text: "Pick a prop from a night's slate (or type your own) and see each venue's stored or typed price side by side, with the cost per $1 of payout." },
  { href: "#/bet/slip", title: "Your rolling slip", text: "Picks waiting for a price, planned legs and the positions you already placed: cost, every listed outcome, and why a coverage summary is or is not shown." },
  { href: "#/bet/hedge", title: "Slip Hedge Finder", text: "Start from a position you recorded as placed and size the other side at each venue against its gross payout." },
  { href: "#/", title: "Add from a game", text: "In the Market lens, game and player pages carry an \"Add to slip\" button beside each line. It adds the line with no price." },
];

export function BetHome() {
  ensureBetStyles();
  const bet = useBet();
  const ws = bet.ready ? bet.view.workspace : null;
  const planned = ws?.draft.legs.filter((l) => l.role === "planned") ?? [];
  const recorded = ws?.draft.legs.filter((l) => l.role === "existing") ?? [];
  return (
    <MarketGate name="Betting tools" what="The betting tools work on prices.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-home-h">
        <h1 id="ph-bet-home-h">{TITLE_HOME}</h1>
        <p class="ph-bet-lede">
          Tools for arithmetic on prices you see elsewhere: compare venues, keep a slip, record what you already placed, and size a hedge. They never estimate a chance or say what to bet; the model's own forecasts stay on the game and player pages. Until the live record proves skill, keeping your money is a fine answer.
        </p>
        <MoneyNote arithmetic />
        <div class="ph-bet-hub">
          {DOORS.map((d) => (
            <a key={d.href} href={d.href}>
              <strong>{d.title}</strong>
              <span class="small">{d.text}</span>
            </a>
          ))}
        </div>
        <section aria-label="Your workspace">
          <h2 class="ph-bet-h">Your workspace</h2>
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
              <p class="num">
                {count(planned.length)} planned {planned.length === 1 ? "leg" : "legs"} ({count(planned.filter((l) => l.enabled).length)} included), {count(recorded.length)} recorded {recorded.length === 1 ? "position" : "positions"}, {count(ws.picks.length)} waiting for a price.
                {ws.draft.origin === "sample" ? " The working slip is the labelled demo." : ""}
              </p>
              <SlipTools bet={bet} ws={ws} />
            </>
          )}
        </section>
      </section>
    </MarketGate>
  );
}
