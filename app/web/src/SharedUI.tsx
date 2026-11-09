import { useEffect, useMemo, useState } from "preact/hooks";
import { american, lineText, longDate } from "./format";
import { loadLedger, onLedgerChange, settle, type Pick } from "./ledger";
import { getShared, onIt, subscribeShared, toPick, type SharedState } from "./sharedPicks";
import { useResults } from "./useSettled";
import { didText } from "./words";

export function useShared(): SharedState {
  const [s, setS] = useState(getShared);
  useEffect(() => subscribeShared(() => { setS(getShared()); }), []);
  return s;
}

/** This device's ledger, kept current as picks are made or merged in. */
export function useLedger(): Pick[] {
  const [rows, setRows] = useState<Pick[]>(loadLedger);
  useEffect(() => onLedgerChange(() => { setRows(loadLedger()); }), []);
  return rows;
}

/** "on it: you, Sam" on a leg card, from this device's pick and the shared ones. Nothing when nobody is on it. */
export function OnItChip({ propId }: { propId: string }) {
  const shared = useShared();
  const own = useLedger();
  const who = onIt(propId, own, shared);
  if (who.length === 0) return null;
  return (
    <p class="onit small" aria-label="Paper picks on this prop">
      <span class="onit-label">on it:</span>{" "}
      {who.map((w, i) => (
        <span key={`${w.who}-${i.toString()}`} class="onit-who">
          {i > 0 ? ", " : ""}
          {w.who} <span class="muted">({w.side} {lineText(w.line)})</span>
        </span>
      ))}
    </p>
  );
}

/** One line on what sharing is doing right now, in words. */
export function ShareNote() {
  const s = useShared();
  let text: string;
  switch (s.status) {
    case "off":
      return null;
    case "loading":
      text = "Connecting to your friends' picks...";
      break;
    case "timeout":
      text = "We cannot reach the shared picks yet. Your picks are saved on this device and will be shared once the connection answers.";
      break;
    case "error":
      text = "Shared picks are not available in this view. Your picks stay on this device.";
      break;
    default:
      text = s.canWrite
        ? "Your paper picks are shared with the people who open this link, and theirs with you. Picks only: no stakes."
        : "You can see shared picks, but this view has no identity for you, so your picks stay on this device.";
  }
  return (
    <p class="small muted share-note" role="status">
      {text}
      {s.writeError ? ` ${s.writeError}` : ""}
    </p>
  );
}

/** Friends' picks with their own record where graded. Names come from the platform at render and are never stored. */
export function FriendsPicks() {
  const s = useShared();
  const ids = Object.keys(s.others);
  const all = useMemo(() => ids.flatMap((id) => (s.others[id] ?? []).map((p) => ({ id, p }))), [s.others]);
  const dates = useMemo(() => [...new Set(all.map((x) => x.p.slate_date))].sort(), [all]);
  const results = useResults(dates);
  if (s.status === "off") return null;
  return (
    <section class="panel" aria-labelledby="friends-h">
      <h2 id="friends-h">Friends' picks</h2>
      {s.status === "loading" ? <p class="muted">Looking for your friends' picks...</p> : null}
      {s.status === "timeout" && ids.length === 0 ? (
        <p class="muted">Nothing has loaded from the shared store yet. We will keep trying; your own picks are safe on this device.</p>
      ) : null}
      {s.status === "live" && ids.length === 0 ? (
        <p class="muted">Nobody else has made a pick yet. When they do, they show up here and as "on it" on the player's card.</p>
      ) : null}
      {ids.map((id) => {
        const picks = (s.others[id] ?? []).map(toPick);
        const graded = results.status === "ready" ? settle(picks, results.rows) : null;
        const wins = graded ? graded.settled.filter((x) => x.outcome === "win").length : 0;
        const losses = graded ? graded.settled.filter((x) => x.outcome === "loss").length : 0;
        const name = s.names[id] || "Someone";
        return (
          <div class="friend" key={id}>
            <h3 class="friend-name">{name}</h3>
            <p class="small muted num">
              {picks.length} {picks.length === 1 ? "pick" : "picks"}
              {graded && wins + losses > 0 ? `, ${wins.toString()}-${losses.toString()} where graded` : ""}
            </p>
            <ul class="picks">
              {[...picks].reverse().slice(0, 12).map((p) => {
                const g = graded?.settled.find((x) => x.pick.id === p.id);
                return (
                  <li key={p.id} class={`pick ${g ? g.outcome : "open"}`}>
                    <span class="badge">{g ? (g.outcome === "win" ? "Hit" : g.outcome === "loss" ? "Miss" : "Push") : "Open"}</span>
                    <div class="pick-body">
                      <div class="pick-title">{g ? `${p.player} ${didText(p.stat, g.row.actual)}` : p.player}</div>
                      <div class="pick-sub num">
                        Took {p.side} {lineText(p.line)} ({american(p.side === "over" ? p.over_price : p.under_price)}). {p.team} vs {p.opponent}, {longDate(p.slate_date)}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
