import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { gameForLeg, toLeg, type ImportLeg, type ImportSpec } from "../importLink";
import type { LiveGameRow } from "../liveTypes";
import { findPlayer, money, type Parlay } from "../tracker";

export interface PreviewSource {
  spec: ImportSpec;
  /** Where the numbers came from, shown above the form. */
  source: string;
  screen?: { max_payout?: number; current_value?: number };
  resolved?: boolean[];
}

interface Draft {
  leg: ImportLeg;
  eventId: string;
}

const uid = (): string => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

function numOrNull(t: string): number | null {
  const n = t.trim() === "" ? null : Number(t);
  return n !== null && Number.isFinite(n) ? n : null;
}

/** An editable preview of an imported parlay. Nothing is added until the viewer taps Add. */
export function ImportPreview(props: { src: PreviewSource; board: LiveGameRow[]; onAdd: (p: Parlay) => void; onCancel: () => void }) {
  const { spec } = props.src;
  const [name, setName] = useState(spec.name);
  const [contracts, setContracts] = useState(spec.contracts.toString());
  const [paid, setPaid] = useState(spec.paid.toString());
  const [combo, setCombo] = useState(spec.combo_price === undefined ? "" : spec.combo_price.toString());
  const [drafts, setDrafts] = useState<Draft[]>(spec.legs.map((leg) => ({ leg, eventId: "" })));
  const [searching, setSearching] = useState(false);

  // Fill each leg's game from its ticker or team, then look for unplaced players in the live box scores.
  useEffect(() => {
    if (props.board.length === 0) return;
    const alive = { v: true };
    const dead = (): boolean => !alive.v;
    setDrafts((ds) => ds.map((d) => (d.eventId !== "" ? d : { ...d, eventId: gameForLeg(d.leg, props.board)?.event_id ?? "" })));
    const missing = drafts.map((d, i) => ({ d, i })).filter(({ d }) => d.eventId === "" && d.leg.kind === "player" && gameForLeg(d.leg, props.board) === null);
    if (missing.length === 0) return;
    setSearching(true);
    void (async () => {
      for (const row of props.board) {
        if (dead() || row.status.state === "pre") continue;
        const g = await api.liveGame(row.event_id).then((r) => r.data, () => null);
        if (!g || dead()) continue;
        setDrafts((ds) =>
          ds.map((d) => (d.eventId === "" && d.leg.kind === "player" && findPlayer(g, d.leg.player ?? "") ? { ...d, eventId: row.event_id } : d)),
        );
      }
      if (!dead()) setSearching(false);
    })();
    return () => { alive.v = false; };
  }, [props.board.length]);

  const patch = (i: number, p: Partial<ImportLeg>): void => {
    setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, leg: { ...d.leg, ...p } } : d)));
  };
  const shares = numOrNull(contracts);
  const cost = numOrNull(paid);
  const bad = shares === null || shares <= 0 || cost === null || cost < 0;
  const add = (): void => {
    if (bad) return;
    const cp = numOrNull(combo);
    props.onAdd({
      id: uid(),
      name: name.trim() === "" ? "Imported parlay" : name.trim(),
      shares,
      totalCost: cost,
      comboPrice: cp !== null && cp > 0 && cp < 1 ? cp : null,
      legs: drafts.map((d) =>
        toLeg(d.leg, props.board.find((r) => r.event_id === d.eventId) ?? null, uid()),
      ),
    });
  };
  return (
    <section class="panel verdict tk-preview" aria-label="Check before adding">
      <h2>Check before adding</h2>
      <p class="small muted">{props.src.source}. Nothing is added until you tap Add. Fix anything that looks wrong first.</p>
      {props.src.screen && (props.src.screen.current_value !== undefined || props.src.screen.max_payout !== undefined) ? (
        <p class="small num">
          The screenshot also showed{props.src.screen.current_value !== undefined ? ` a current value of ${money(props.src.screen.current_value)}` : ""}
          {props.src.screen.max_payout !== undefined ? ` and a maximum payout of ${money(props.src.screen.max_payout)}` : ""}. We
          do not use these; the tracker computes its own value from live prices.
        </p>
      ) : null}
      <div class="tk-grid">
        <label class="tk-field">
          <span class="label">Name</span>
          <input type="text" value={name} onInput={(e) => { setName(e.currentTarget.value); }} />
        </label>
        <label class="tk-field">
          <span class="label">Contracts</span>
          <input type="number" inputMode="decimal" step="any" value={contracts} onInput={(e) => { setContracts(e.currentTarget.value); }} />
        </label>
        <label class="tk-field">
          <span class="label">Total paid, $</span>
          <input type="number" inputMode="decimal" step="any" value={paid} onInput={(e) => { setPaid(e.currentTarget.value); }} />
        </label>
        <label class="tk-field">
          <span class="label">Combo price per contract, $ (optional)</span>
          <input type="number" inputMode="decimal" step="any" value={combo} onInput={(e) => { setCombo(e.currentTarget.value); }} />
        </label>
      </div>
      {searching ? <p class="small muted" role="status">Looking for each player in today's box scores...</p> : null}
      <ul class="tk-leglist">
        {drafts.map((d, i) => (
          <li key={i} class="tk-leg tk-leg-col">
            <b>{d.leg.label}</b>
            <div class="small muted">
              {d.leg.ticker ? "Kalshi ticker found. " : props.src.resolved?.[i] === false || d.leg.kind === "spread" || d.leg.kind === "total" ? "No Kalshi ticker found: paste one to get live prices. " : ""}
              {d.eventId === "" ? "No game found: pick one so live status works." : ""}
            </div>
            <label class="tk-field">
              <span class="label">Game</span>
              <select value={d.eventId} onChange={(e) => { const v = e.currentTarget.value; setDrafts((ds) => ds.map((x, j) => (j === i ? { ...x, eventId: v } : x))); }}>
                <option value="">Not set</option>
                {props.board.map((g) => (
                  <option key={g.event_id} value={g.event_id}>{g.away.abbr} @ {g.home.abbr}</option>
                ))}
              </select>
            </label>
            {d.leg.kind === "player" ? (
              <label class="tk-field">
                <span class="label">Player (as ESPN spells it)</span>
                <input type="text" value={d.leg.player ?? ""} onInput={(e) => { patch(i, { player: e.currentTarget.value }); }} />
              </label>
            ) : null}
            {d.leg.threshold !== undefined ? (
              <label class="tk-field">
                <span class="label">{d.leg.kind === "player" ? "N or more" : d.leg.kind === "total" ? "Line" : "Handicap"}</span>
                <input
                  type="number"
                  inputMode="decimal"
                  step="any"
                  value={d.leg.threshold}
                  onInput={(e) => { const n = numOrNull(e.currentTarget.value); if (n !== null) patch(i, { threshold: n }); }}
                />
              </label>
            ) : null}
            <label class="tk-field">
              <span class="label">Kalshi ticker (optional)</span>
              <input
                type="text"
                value={d.leg.ticker ?? ""}
                autocapitalize="characters"
                onInput={(e) => { const t = e.currentTarget.value.trim(); patch(i, { ticker: t === "" ? undefined : t }); }}
              />
            </label>
          </li>
        ))}
      </ul>
      <div class="row wrap">
        <button type="button" class="btn primary" disabled={bad} onClick={add}>Add this parlay</button>
        <button type="button" class="btn" onClick={props.onCancel}>Cancel</button>
        {bad ? <span class="small muted">Contracts must be above 0 and total paid 0 or more.</span> : null}
      </div>
    </section>
  );
}
