// Framing 4, build your own: any calibrated lines, any games that have not tipped; live chance, unrelated chance, lift.
import { useMemo, useState } from "preact/hooks";
import { Card } from "../ui";
import { CLOSED_SENTENCE, OUT_OF_RANGE_NOTE, ResultBlock, legKey, legWords, matchup, pc, type Ctx } from "./Bits";
import { evaluateCombo, intervalProb, lineToBounds, statEntries } from "./engine";
import { OTHER_LINES_SENTENCE, SCRIPT_TITLE, STAT_NAME, hasTipped, offerableLines, postedLineCount, scriptGeneric } from "./script";
import { SCRIPTS, type ComboGame, type PlayerLeg, type ScriptName } from "./types";

const MAX_LEGS = 8;

export function BuildTab(props: { ctx: Ctx; game: ComboGame }) {
  const { ctx } = props;
  const open = useMemo(() => ctx.payload.games.filter((g) => !hasTipped(g, ctx.clock)), [ctx]);
  const closed = ctx.payload.games.length - open.length;
  const [gid, setGid] = useState(
    (open.find((g) => g.game_id === props.game.game_id && postedLineCount(g) > 0) ?? open.find((g) => postedLineCount(g) > 0) ?? open[0])?.game_id ?? "",
  );
  const game = open.find((g) => g.game_id === gid);
  const [pid, setPid] = useState<number | null>(null);
  const withLines = (game?.players ?? []).filter((p) => statEntries(p).some(([, g]) => offerableLines(g).length > 0));
  const player = withLines.find((p) => p.player_id === pid) ?? withLines[0];
  const stats = player === undefined ? [] : statEntries(player).filter(([, g]) => offerableLines(g).length > 0);
  const [stat, setStat] = useState("pts");
  const entry = stats.find((s) => s[0] === stat) ?? stats[0];
  const lines = useMemo(() => (entry === undefined ? [] : offerableLines(entry[1])), [entry]);
  const [lineSel, setLineSel] = useState<number | null>(null);
  const line = lineSel !== null && lines.includes(lineSel) ? lineSel : (entry?.[1].main_line != null && lines.includes(entry[1].main_line) ? entry[1].main_line : lines[Math.floor(lines.length / 2)]);
  const [side, setSide] = useState<"over" | "under">("over");
  const [legs, setLegs] = useState<PlayerLeg[]>([]);
  const [script, setScript] = useState<ScriptName | "">("");

  const gamesById = useMemo(() => Object.fromEntries(open.map((g) => [g.game_id, g])), [open]);
  const preview = useMemo(() => {
    if (game === undefined || player === undefined || entry === undefined || line === undefined) return null;
    const [lo, hi] = lineToBounds(line, side);
    return intervalProb(entry[1], lo, hi);
  }, [game, player, entry, line, side]);
  const result = useMemo(() => {
    if (legs.length === 0) return null;
    try {
      return evaluateCombo(legs, gamesById, ctx.payload.shared, script === "" ? null : script);
    } catch {
      return null;
    }
  }, [legs, gamesById, ctx.payload.shared, script]);
  const sameGame = new Set(legs.map((l) => l.game_id)).size <= 1;

  if (open.length === 0) {
    return (
      <section class="panel" role="note">
        <p>{CLOSED_SENTENCE}</p>
      </section>
    );
  }
  const add = (): void => {
    if (game === undefined || player === undefined || entry === undefined || line === undefined || legs.length >= MAX_LEGS) return;
    const leg: PlayerLeg = { kind: "player", game_id: game.game_id, player_id: player.player_id, stat: entry[0], line, side };
    if (legs.some((l) => legKey(l) === legKey(leg))) return;
    setLegs([...legs, leg]);
  };
  return (
    <div class="stack">
      <section class="panel" aria-labelledby="cb-b-h">
        <h2 id="cb-b-h">Build your own</h2>
        <p class="small muted">
          Pick calibrated lines from any game that has not tipped. Legs in one game are tied together by the model; legs in different games are treated as
          independent. Only each player's posted line is offered, over or under. {OTHER_LINES_SENTENCE} {OUT_OF_RANGE_NOTE}
          {closed > 0 ? ` ${closed.toString()} game${closed === 1 ? " has" : "s have"} tipped and ${closed === 1 ? "is" : "are"} not offered.` : ""}
        </p>
        <div class="cb-controls">
          <label class="field">
            <span>Game</span>
            <select value={gid} onChange={(e) => { setGid(e.currentTarget.value); setPid(null); setLineSel(null); }}>
              {open.map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {matchup(ctx, g)}
                </option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>Player</span>
            <select value={player?.player_id.toString() ?? ""} onChange={(e) => { setPid(Number(e.currentTarget.value)); setLineSel(null); }}>
              {withLines.map((p) => (
                <option key={p.player_id} value={p.player_id.toString()}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>Stat</span>
            <select value={entry?.[0] ?? ""} onChange={(e) => { setStat(e.currentTarget.value); setLineSel(null); }}>
              {stats.map(([s]) => (
                <option key={s} value={s}>
                  {STAT_NAME[s] ?? s}
                </option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>Side</span>
            <select value={side} onChange={(e) => { setSide(e.currentTarget.value === "under" ? "under" : "over"); }}>
              <option value="over">over</option>
              <option value="under">under</option>
            </select>
          </label>
          <label class="field">
            <span>Line</span>
            <select value={line?.toString() ?? ""} onChange={(e) => { setLineSel(Number(e.currentTarget.value)); }} disabled={lines.length === 0}>
              {lines.map((l) => (
                <option key={l} value={l.toString()}>
                  {l.toString()}
                  {entry?.[1].main_line === l ? " (posted)" : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
        {withLines.length === 0 ? (
          <p class="small" role="note">
            <strong>No posted lines for this game.</strong> The file holds no posted line for any player in it, so there is nothing to build from here. Try another game.
          </p>
        ) : null}
        <p class="small">
          {preview !== null && line !== undefined ? (
            <>
              This leg alone: <b class="num">{pc(preview)}</b> (calibrated).{" "}
            </>
          ) : null}
          <button type="button" class="btn small" onClick={add} disabled={legs.length >= MAX_LEGS || line === undefined}>
            Add leg
          </button>
          {legs.length >= MAX_LEGS ? <span class="muted"> Eight legs is the most.</span> : null}
        </p>
      </section>

      <section class="panel" aria-labelledby="cb-legs-h">
        <h3 id="cb-legs-h">Your legs</h3>
        {legs.length === 0 ? (
          <p class="small muted">No legs yet. Add one above.</p>
        ) : (
          <ul class="plain cb-legs">
            {legs.map((l) => {
              const g = gamesById[l.game_id];
              return (
                <li key={legKey(l)} class="cb-leg">
                  <span>
                    {g === undefined ? l.game_id : legWords(l, g)} <span class="muted small">({g === undefined ? "" : matchup(ctx, g)})</span>
                  </span>
                  <button type="button" class="btn small" onClick={() => { setLegs(legs.filter((x) => legKey(x) !== legKey(l))); }} aria-label={`Remove ${g === undefined ? "leg" : legWords(l, g)}`}>
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <label class="field">
          <span>If the game goes like this (optional)</span>
          <select value={script} onChange={(e) => { setScript(e.currentTarget.value as ScriptName | ""); }}>
            <option value="">No script (chances as they stand)</option>
            {SCRIPTS.map((s) => (
              <option key={s} value={s}>
                {SCRIPT_TITLE[s]}: {scriptGeneric(s, ctx.payload.shared)} (exploratory)
              </option>
            ))}
          </select>
        </label>
        {script !== "" ? <p class="small muted">Exploratory: the script's effect is an estimate. It is applied to every game in the combo, each using its own expected total where the script is about total points.</p> : null}
      </section>

      {result !== null ? (
        <Card title="The combined chance" tone="verdict">
          <ResultBlock r={result} sameGame={sameGame} />
        </Card>
      ) : null}
    </div>
  );
}
