// "Add to slip" beside a leg on the player page and beside each prop on the game page (Market lens only). It adds the
// leg exactly as the page shows it (game, player, stat, line, side) with NO price: the viewer picks a venue price in
// Compare or types one. Adding a pick that is already there switches it back on instead of duplicating it.
import { useState } from "preact/hooks";
import { api } from "../api";
import { failureMessage, failureOf } from "../errors";
import { lineText } from "../format";
import { useLens } from "../prefs";
import { pushToast } from "../toast";
import { STAT_SHORT } from "../types";
import type { AddToSlipProps } from "./seam";
import { slipCount } from "./Slip";
import { isStat, marketFromFacts, sideOf } from "./snapshots";
import { ensureBetStyles, useBet } from "./useBet";

export function AddToSlip(props: AddToSlipProps) {
  ensureBetStyles();
  const lens = useLens();
  const bet = useBet();
  const [busy, setBusy] = useState(false);
  const { playerId, stat, line } = props;
  if (lens !== "market" || !bet.ready || playerId === null || stat === null || line === null || !isStat(stat)) return null;
  const add = async (side: "over" | "under"): Promise<void> => {
    setBusy(true);
    try {
      const g = (await api.game(props.gameId)).data;
      const p = g.players.find((x) => x.player_id === playerId);
      if (!p) throw new Error("this player is not in the game's data");
      const team = p.side === "home" ? g.home.abbr : g.away.abbr;
      const built = marketFromFacts({ gameId: g.game_id, date: g.date, playerId, playerName: p.name, stat, line, eventLabel: `${g.away.abbr} @ ${g.home.abbr}` });
      if (!built.ok) {
        pushToast("Not added", built.text, 8000);
        return;
      }
      const d = await bet.actions.addPick(built.market, sideOf(side), props.from);
      const ws = bet.store.get().workspace;
      const n = ws ? slipCount(ws) : 0;
      if (d.ok) pushToast(`${p.name} (${team}) ${side} ${lineText(line)} ${STAT_SHORT[stat]}`, `${d.text} Slip: ${String(n)} ${n === 1 ? "item" : "items"}.`);
      else pushToast("Not added", d.text, 8000);
    } catch (e) {
      pushToast("Not added", `The game could not be read: ${failureMessage(failureOf(e))}`, 8000);
    } finally {
      setBusy(false);
    }
  };
  const sides: ("over" | "under")[] = props.side === null ? ["over", "under"] : [props.side];
  return (
    <span class="ph-bet ph-bet-add">
      {sides.map((s) => (
        <button
          key={s}
          type="button"
          class="btn"
          disabled={busy}
          aria-label={`Add ${s} ${lineText(line)} ${STAT_SHORT[stat]} to your slip (no price yet)`}
          onClick={() => {
            void add(s);
          }}
        >
          + Slip: {s} {lineText(line)}
        </button>
      ))}
    </span>
  );
}
