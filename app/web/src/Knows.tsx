import { api } from "./api";
import { Explain, View } from "./components";
import { lineText, pct, stamp } from "./format";
import { legHref } from "./router";
import { percentAlong } from "./slider";
import { STAT_LABEL, type GameKnows, type KnowsNews, type KnowsSnap, type PlayerKnows } from "./types";
import { useLoad } from "./useLoad";

/** "5 h 30 min before tip", "42 min before tip". */
export function leadText(min: number): string {
  const m = Math.round(min);
  if (m < 1) return "at tip";
  const h = Math.floor(m / 60);
  const r = m % 60;
  const t = h === 0 ? `${r.toString()} min` : r === 0 ? `${h.toString()} h` : `${h.toString()} h ${r.toString()} min`;
  return `${t} before tip`;
}

const STATUS_WORD: Record<string, string> = { out: "out", doubtful: "doubtful", questionable: "questionable", probable: "probable", available: "available" };
const status = (s: string): string => STATUS_WORD[s] ?? s;

/** Reports grouped by the time they were current: one block per time, first lines folded into a count. */
export function groupNews(news: readonly KnowsNews[]): { at: string; lead: number | null; first: KnowsNews[]; changes: KnowsNews[] }[] {
  const out: { at: string; lead: number | null; first: KnowsNews[]; changes: KnowsNews[] }[] = [];
  for (const n of news) {
    let g = out.find((x) => x.at === n.at_utc);
    if (!g) {
      g = { at: n.at_utc, lead: n.lead_minutes, first: [], changes: [] };
      out.push(g);
    }
    (n.first_report ? g.first : g.changes).push(n);
  }
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

function countsText(items: readonly KnowsNews[]): string {
  const c = new Map<string, number>();
  for (const i of items) c.set(i.status, (c.get(i.status) ?? 0) + 1);
  return [...c.entries()].map(([s, n]) => `${n.toString()} ${status(s)}`).join(", ");
}

function NewsBlock({ g }: { g: ReturnType<typeof groupNews>[number] }) {
  return (
    <li class="kn-item news">
      <div class="kn-when num">
        {stamp(g.at)}
        {g.lead !== null ? <span class="muted small"> · {leadText(g.lead)}</span> : null}
      </div>
      <div class="kn-what">
        <strong>Injury report</strong>
        {g.first.length > 0 ? (
          <details class="kn-first">
            <summary>
              {g.first.length} {g.first.length === 1 ? "player" : "players"} listed: {countsText(g.first)}
            </summary>
            <ul class="plain small">
              {g.first.map((n) => (
                <li key={n.player_id}>
                  {n.himself ? <b>{n.name}</b> : n.name}
                  {n.team ? ` (${n.team})` : ""}: {status(n.status)}
                  {n.reason ? `, ${n.reason}` : ""}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {g.changes.map((n) => (
          <p key={`${n.player_id.toString()}-${n.status}`} class="small kn-change">
            {n.himself ? <b>{n.name}</b> : n.name}
            {n.team ? ` (${n.team})` : ""}: {n.previous ? `${status(n.previous)} to ` : ""}
            <b>{status(n.status)}</b>
            {n.reason ? `, ${n.reason}` : ""}
          </p>
        ))}
      </div>
    </li>
  );
}

function ForecastRow(props: { s: KnowsSnap; prev: KnowsSnap | null; scale: { lo: number; hi: number }; line: number | null; stat: string }) {
  const { s, prev, scale } = props;
  const at = (v: number): string => `${percentAlong(v, scale.lo, scale.hi).toFixed(1)}%`;
  const moved = prev ? s.p50 - prev.p50 : null;
  const dOver = prev ? s.p_over - prev.p_over : null;
  return (
    <li class={`kn-item forecast${s.shown ? " shown" : ""}`}>
      <div class="kn-when num">
        {stamp(s.made_at)}
        <span class="muted small"> · {leadText(s.lead_minutes)}</span>
      </div>
      <div class="kn-what">
        <strong>Forecast{s.shown ? " (the one used in this app)" : ""}</strong>
        <div class="kn-range" role="img" aria-label={`Middle 80 percent ${s.p10.toFixed(1)} to ${s.p90.toFixed(1)}, typical ${s.p50.toFixed(1)}`}>
          <i class="kn-band" style={{ left: at(s.p10), width: `${(percentAlong(s.p90, scale.lo, scale.hi) - percentAlong(s.p10, scale.lo, scale.hi)).toFixed(1)}%` }} />
          <i class="kn-mid" style={{ left: at(s.p50) }} />
          {props.line !== null ? <i class="kn-line" style={{ left: at(props.line) }} /> : null}
        </div>
        <p class="small num">
          Typical night <b>{s.p50.toFixed(1)}</b>; middle 80% {s.p10.toFixed(1)} to {s.p90.toFixed(1)}
          {props.line !== null ? (
            <>
              ; over {lineText(props.line)}: <b>{pct(s.p_over)}</b>
            </>
          ) : null}
          .
        </p>
        {moved !== null && dOver !== null ? (
          <p class="small muted num">
            Since the last forecast: typical {moved >= 0 ? "+" : "-"}
            {Math.abs(moved).toFixed(1)}
            {props.line !== null ? `, over chance ${dOver >= 0 ? "+" : "-"}${Math.abs(dOver * 100).toFixed(0)} points` : ""}.
          </p>
        ) : null}
        {s.proj_minutes !== null ? (
          <p class="small muted num">
            Projected minutes {s.proj_minutes.toFixed(0)}
            {s.p_play !== null ? `; chance to play ${s.p_play >= 0.995 ? "over 99%" : pct(s.p_play)}` : ""}.
          </p>
        ) : null}
      </div>
    </li>
  );
}

/** One prop's forecasts before tip and the news beside them, oldest first, ending at tip. */
function Timeline({ d }: { d: PlayerKnows }) {
  const snaps = d.snapshots;
  if (snaps.length === 0) return <p class="small muted">{d.note}</p>;
  const lo = Math.min(...snaps.map((s) => s.p10), d.line_source === "posted" ? d.line : Infinity);
  const hi = Math.max(...snaps.map((s) => s.p90), d.line_source === "posted" ? d.line : -Infinity);
  const pad = Math.max(1, (hi - lo) * 0.08);
  const scale = { lo: lo - pad, hi: hi + pad };
  const events: { t: number; node: preact.JSX.Element }[] = [];
  snaps.forEach((s, i) => {
    events.push({
      t: Date.parse(s.made_at),
      node: <ForecastRow key={`f-${s.made_at}`} s={s} prev={i > 0 ? (snaps[i - 1] ?? null) : null} scale={scale} line={d.line_source === "posted" ? d.line : null} stat={d.stat} />,
    });
  });
  for (const g of groupNews(d.news)) events.push({ t: Date.parse(g.at), node: <NewsBlock key={`n-${g.at}`} g={g} /> });
  events.sort((a, b) => a.t - b.t);
  return (
    <>
      <p class="small muted">{d.note}</p>
      <ol class="kn-rail" aria-label="Forecasts and injury reports, oldest first">
        {events.map((e) => e.node)}
        <li class="kn-item tip">
          <div class="kn-when num">{stamp(d.tip_utc)}</div>
          <div class="kn-what">
            <strong>Tip-off</strong>
          </div>
        </li>
      </ol>
      <p class="small muted">The bar under each forecast is the middle 80% of his {STAT_LABEL[d.stat].toLowerCase()}; the tick is the typical night{d.line_source === "posted" ? "; the thin line is the posted line" : ""}.</p>
    </>
  );
}

function Why({ d }: { d: PlayerKnows }) {
  const u = d.uncertainty;
  const lo = Math.max(0, u.p10 - (u.p90 - u.p10) * 0.25);
  const hi = u.p90 + (u.p90 - u.p10) * 0.25;
  const share = u.minutes.minutes_share;
  return (
    <div class="kn-why">
      <h3>Why the range is this wide</h3>
      <div class="kn-range big" role="img" aria-label={`Middle 80 percent ${u.p10.toFixed(1)} to ${u.p90.toFixed(1)}`}>
        <i class="kn-band" style={{ left: `${percentAlong(u.p10, lo, hi).toFixed(1)}%`, width: `${(percentAlong(u.p90, lo, hi) - percentAlong(u.p10, lo, hi)).toFixed(1)}%` }} />
        {u.mean !== null ? <i class="kn-mid" style={{ left: `${percentAlong(u.mean, lo, hi).toFixed(1)}%` }} /> : null}
      </div>
      <ul class="plain kn-why-list">
        {u.items.map((i, k) => (
          <li key={`${i.key}-${k.toString()}`} data-key={i.key}>
            {i.text}
          </li>
        ))}
      </ul>
      {share !== null ? (
        <Explain label="How the minutes share is worked out">
          <p class="small">{u.minutes.method}</p>
        </Explain>
      ) : null}
      <p class="small muted">Each line is a fact about him or the matchup with its sample size; none of them is a claim about what will happen tonight.</p>
    </div>
  );
}

/** "What the game knew": the tip-off timeline for one prop, and the uncertainty map beside it. */
export function PlayerKnowsPanel(props: { gameId: string; playerId: number; stat: string }) {
  const [state, reload] = useLoad(() => api.knowsPlayer(props.gameId, props.playerId, props.stat), [props.gameId, props.playerId, props.stat]);
  return (
    <section class="panel knows" aria-label="What the game knew">
      <h2>What the game knew, and when</h2>
      <View state={state} reload={reload}>
        {(d) => (
          <div class="stack">
            <Timeline d={d} />
            <Why d={d} />
          </div>
        )}
      </View>
    </section>
  );
}

function Movers({ d }: { d: GameKnows }) {
  if (d.movers.length === 0) return <p class="small muted">{d.note}</p>;
  return (
    <>
      <p class="small muted">{d.note}</p>
      <ul class="kn-movers">
        {d.movers.map((m) => (
          <li key={m.player_id}>
            <a href={legHref(d.game_id, m.player_id, m.stat)}>
              <strong>{m.name}</strong> <span class="muted small">{m.team}</span>
              <span class="num kn-move">
                {m.first_mean.toFixed(1)} to {m.last_mean.toFixed(1)} <b>({m.delta >= 0 ? "+" : "-"}
                {Math.abs(m.delta).toFixed(1)})</b>
              </span>
              <span class="small muted num">{m.n_snapshots} forecasts</span>
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The game's timeline: what the injury report said, and which forecasts moved most while it did. */
export function GameKnowsPanel(props: { gameId: string }) {
  const [state, reload] = useLoad(() => api.knowsGame(props.gameId), [props.gameId]);
  return (
    <section class="panel knows" aria-label="What the game knew">
      <h2>What the game knew, and when</h2>
      <View state={state} reload={reload}>
        {(d) => {
          const groups = groupNews(d.news);
          return (
            <div class="stack">
              <div>
                <h3>Forecasts that moved</h3>
                <Movers d={d} />
              </div>
              <div>
                <h3>Injury reports</h3>
                {groups.length === 0 ? (
                  <p class="small muted">No injury report is stored for this game, so there is no news to put beside the forecasts.</p>
                ) : (
                  <ol class="kn-rail" aria-label="Injury reports, oldest first">
                    {groups.map((g) => (
                      <NewsBlock key={g.at} g={g} />
                    ))}
                  </ol>
                )}
              </div>
              <p class="small muted">Open a player to see his forecasts and the reports on one timeline.</p>
            </div>
          );
        }}
      </View>
    </section>
  );
}
