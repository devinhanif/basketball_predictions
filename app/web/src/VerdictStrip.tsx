import { api } from "./api";
import { Explain, Loading, View } from "./components";
import { longDate } from "./format";
import { engineSpeaksFor } from "./nights";
import { Body as EngineBody, gateSentence, verdictText, whatWeChecked } from "./pages/Parlay";
import { plainText } from "./plain";
import { TopParlaysPanel } from "./TopParlaysPanel";
import { hasTickets, type TpResult } from "./topParlays";
import { useLoad } from "./useLoad";
import type { ParlayState } from "./types";

/**
 * One strip for "what should I bet tonight?" (F21). The honest default is a single line, "No parlay tonight. Keep
 * your money.", with the two reasons (the engine's check and the tickets file) one tap away. When there are tickets,
 * the full ticket panel shows instead; when the engine itself has a pick for this very night, its card shows first.
 */
export function VerdictStrip({ date }: { date: string }) {
  const [eng, reloadEng] = useLoad(() => api.parlay(), []);
  const [tp, reloadTp] = useLoad(() => api.topParlays(date), [date]);
  if (eng.status === "loading" || tp.status === "loading") return <Loading />;
  if (eng.status === "error") return <View state={eng} reload={reloadEng}>{() => null}</View>;
  if (tp.status === "error") return <View state={tp} reload={reloadTp}>{() => null}</View>;
  const p = eng.data;
  const t = tp.data;
  const speaks = engineSpeaksFor(date, p.report_date);
  const engineHasPick = speaks && p.headline_verdict !== "no_positive_ev_found";
  const tickets = t.kind === "ok" && hasTickets(t.data);
  if (engineHasPick || tickets) {
    return (
      <>
        {engineHasPick ? <EngineBody p={p} compact /> : null}
        {tickets ? <TopParlaysPanel date={date} checkToday /> : null}
      </>
    );
  }
  return <NoParlay date={date} p={p} speaks={speaks} tickets={t} />;
}

function ticketsLine(t: TpResult): string {
  if (t.kind === "refused") return `The tickets file for this night did not pass our checks, so we are not showing it. ${plainText(t.detail)}`;
  if (t.kind === "ok") return `The tickets file for this night holds no tickets. ${plainText(t.data.empty_reason ?? "No reason was given")}.`;
  return "No tickets are stored for this night yet. That is normal before the pre-tip run, not an error.";
}
function NoParlay(props: { date: string; p: ParlayState; speaks: boolean; tickets: TpResult }) {
  const { p, speaks, date } = props;
  const refused = props.tickets.kind === "refused";
  return (
    <section class="panel verdict strip" aria-labelledby="strip-h">
      <div class="kicker">Parlays for {longDate(date)}</div>
      <h2 id="strip-h">{refused ? "Tickets failed their checks. Keep your money." : "No parlay tonight. Keep your money."}</h2>
      <Explain label="Why?">
        <ul class="plain small">
          <li>
            {speaks
              ? `${verdictText(p)} ${whatWeChecked(p, false)}`
              : `No parlay check is stored for ${longDate(date)}. The only one we hold is for ${longDate(p.report_date)}: ${verdictText(p)}`}
          </li>
          <li>{ticketsLine(props.tickets)}</li>
          <li>{gateSentence(p.gate)}</li>
        </ul>
        <p class="small">
          <a href={`#/parlay?date=${date}`}>The full story</a>
        </p>
      </Explain>
    </section>
  );
}
