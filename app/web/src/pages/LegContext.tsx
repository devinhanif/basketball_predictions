// Leg page, context: heads up, splits, teammates, role, opponent and the edge arithmetic (split from Leg.tsx).
import { api } from "../api";
import { edgeLine, nearestFirst, contractSide } from "../edges";
import { useLoad } from "../useLoad";
import { STATS } from "../types";
import { lineText, num, stamp } from "../format";
import { nText, rankPhrase, ratio } from "../leg";
import { BeyondBlock, MatePer36, OnOffBlock, SteadyBlock } from "../RoleCards";
import { type LegBreakdown, type Side, type Split, type Tally, type TeammateOut } from "../legTypes";

const S = (n: number): string => n.toString();

/* ------------------------------------------------------------------ heads up and notes */
export function HeadsUp({ data }: { data: LegBreakdown }) {
  if (!data.heads_up) return null;
  return (
    <p class="leg-notice" role="note">
      {data.heads_up}
    </p>
  );
}

/* ------------------------------------------------------------------ splits */
function Cell({ t, label }: { t: Tally; label: string }) {
  return (
    <div class={`leg-cell${t.small ? " thin" : ""}`}>
      <div class="label">{label}</div>
      {t.n === 0 ? (
        <div class="leg-cell-main muted">no games</div>
      ) : (
        <>
          <div class="leg-cell-main num">{ratio(t)}</div>
          <div class="small muted num">
            {t.avg !== null ? `avg ${num(t.avg)}` : ""}
            {t.avg_minutes !== null ? `, ${num(t.avg_minutes, 0)} min` : ""}
          </div>
        </>
      )}
      <div class="small muted num">{t.n === 0 ? "n = 0" : nText(t)}</div>
    </div>
  );
}

const SPLIT_GROUPS: { title: string; keys: string[] }[] = [
  { title: "Home or away", keys: ["home", "away"] },
  { title: "Rest", keys: ["b2b", "rest1", "rest2"] },
  { title: "This opponent", keys: ["vs_opp"] },
];

function SplitRow({ s }: { s: Split }) {
  return (
    <div class={`leg-split${s.tonight ? " tonight" : ""}`}>
      <div class="leg-split-label">
        {s.label}
        {s.tonight ? <span class="tag">Tonight</span> : null}
      </div>
      <div class="leg-cells">
        <Cell t={s.this_season} label="This season" />
        <Cell t={s.since_last_season} label="Since last season" />
      </div>
    </div>
  );
}

export function Splits({ data, side, line }: { data: LegBreakdown; side: Side; line: number }) {
  return (
    <section class="panel" aria-label="Splits">
      <h2>How he does in each situation</h2>
      <p class="small muted" style={{ marginTop: 0 }}>
        Times he went {side} {lineText(line)}, out of games in each situation. The number under each is the sample size:
        thin samples are dimmed and should not be trusted.
      </p>
      {SPLIT_GROUPS.map((g) => {
        const rows = data.splits.filter((s) => g.keys.includes(s.key));
        return (
          <div key={g.title} class="leg-group-block">
            <h3>{g.title}</h3>
            {rows.map((s) => (
              <SplitRow key={s.key} s={s} />
            ))}
          </div>
        );
      })}
    </section>
  );
}

/* ------------------------------------------------------------------ teammates out */
function cleanReason(r: string | null): string {
  if (!r || r === "-") return "";
  return r.replace(/^Injury\/Illness-/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function TeammateCard({ t, side, line }: { t: TeammateOut; side: Side; line: number }) {
  const wo = t.without;
  const wi = t.with_;
  return (
    <div class="leg-mate">
      <div class="row between wrap">
        <strong>{t.name}</strong>
        <span class={`tag leg-status ${t.status}`}>{t.status}</span>
      </div>
      <div class="small muted">
        {cleanReason(t.reason) ? `${cleanReason(t.reason)}. ` : ""}Plays {num(t.avg_minutes, 0)} minutes a game lately.
      </div>
      <BeyondBlock b={t.beyond} />
      <div class="past-games">
      <div class="label">Past games on this line</div>
      <div class="leg-cells">
        <div class={`leg-cell${wo.small ? " thin" : ""}`}>
          <div class="label">With him out</div>
          {wo.n === 0 ? (
            <div class="leg-cell-main muted">no games</div>
          ) : (
            <>
              <div class="leg-cell-main num">{ratio(wo)}</div>
              <div class="small muted num">
                {wo.avg !== null ? `avg ${num(wo.avg)}` : ""}
                {wo.avg_minutes !== null ? `, ${num(wo.avg_minutes, 0)} min` : ""}
              </div>
            </>
          )}
          <div class="small muted num">{nText(wo)}</div>
        </div>
        <div class={`leg-cell${wi.small ? " thin" : ""}`}>
          <div class="label">With him playing</div>
          {wi.n === 0 ? (
            <div class="leg-cell-main muted">no games</div>
          ) : (
            <>
              <div class="leg-cell-main num">{ratio(wi)}</div>
              <div class="small muted num">
                {wi.avg !== null ? `avg ${num(wi.avg)}` : ""}
                {wi.avg_minutes !== null ? `, ${num(wi.avg_minutes, 0)} min` : ""}
              </div>
            </>
          )}
          <div class="small muted num">{nText(wi)}</div>
        </div>
      </div>
      <p class="small muted" style={{ marginBottom: 0 }}>
        Games {side} {lineText(line)}. {t.note}
      </p>
      </div>
      <MatePer36 without={t.per36_without} with_={t.per36_with} name={t.name} />
      <p class="small muted" style={{ marginBottom: 0 }}>
        Per 36 minutes, the same games, whatever stat is on screen. Assists are from the box score.
      </p>
    </div>
  );
}

export function Teammates({ data, side, line }: { data: LegBreakdown; side: Side; line: number }) {
  const t = data.teammates;
  const own = t.player_status && t.player_status !== "available" ? t.player_status : null;
  return (
    <section class="panel" aria-label="Teammates out tonight">
      <h2>Teammates out tonight</h2>
      {own ? (
        <p class="leg-flag" role="note">
          <strong>
            {data.player} is listed {own}
          </strong>
          {cleanReason(t.player_status_reason) ? `: ${cleanReason(t.player_status_reason)}` : ""}. Every number here assumes he plays.
        </p>
      ) : null}
      {t.items.length === 0 ? (
        <p>No rotation teammates are listed out, doubtful or questionable on the injury report.</p>
      ) : (
        <div class="stack tight">
          {t.items.map((m) => (
            <TeammateCard key={m.player_id} t={m} side={side} line={line} />
          ))}
        </div>
      )}
      {t.omitted_unnamed > 0 ? (
        <p class="small muted">
          {S(t.omitted_unnamed)} more rotation {t.omitted_unnamed === 1 ? "player is" : "players are"} listed but not named in our data.
        </p>
      ) : null}
      <p class="small muted">
        {t.note}
        {t.report_as_of_utc ? ` Latest report used: ${stamp(t.report_as_of_utc)}.` : ""}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ role and on/off */
export function RolePanel({ data }: { data: LegBreakdown }) {
  return (
    <section class="panel" aria-label="Role and on/off">
      <h2>His role and the team with him on</h2>
      <SteadyBlock s={data.steadiness} />
      <OnOffBlock o={data.onoff} player={data.player} />
    </section>
  );
}

/* ------------------------------------------------------------------ opponent */
export function Opponent({ data }: { data: LegBreakdown }) {
  const o = data.opponent_allowed;
  return (
    <section class="panel" aria-label="The opponent">
      <h2>{data.opponent} against his position</h2>
      {o ? (
        <>
          <p class="lead num">
            {o.opp} allows <b>{num(o.avg_allowed)}</b> {data.label.toLowerCase()} a game to {o.position_noun}
          </p>
          <p class="num">
            <b>{rankPhrase(o.rank, o.of)}</b>. League average {num(o.league_avg)}. {S(o.n_games)} games this season.
          </p>
          <p class="small muted">{o.note}</p>
        </>
      ) : (
        <p class="muted">We do not have this comparison for this player.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ the edge arithmetic (Analyst) */
export function EdgePanel({ data, line }: { data: LegBreakdown; line: number }) {
  const known = (STATS as readonly string[]).includes(data.stat);
  const [state] = useLoad(
    () => (known ? api.edges(data.game_id, data.player_id, data.stat) : Promise.reject(new Error("no contracts for this stat"))),
    [data.game_id, data.player_id, data.stat],
  );
  if (!known) return null;
  return (
    <section class="panel" aria-label="Edge after fees">
      <h2>Edge after fees</h2>
      {state.status === "loading" ? <p class="muted">Loading...</p> : null}
      {state.status === "error" ? <p class="muted">No prediction-market price was found for this player and stat.</p> : null}
      {state.status === "ready" && state.data.contracts.length === 0 ? (
        <p class="muted">No prediction-market contract was priced for this player and stat, so there is no edge to show.</p>
      ) : null}
      {state.status === "ready" && state.data.contracts.length > 0 ? (
        <>
          <p class="small muted" style={{ marginTop: 0 }}>
            Each line is one stored contract: our model's chance with its range, against the ask plus the fee. The edge is what is
            left, per $1. Nearest to the line on screen first.
          </p>
          <ul class="edge-list">
            {nearestFirst(state.data.contracts, line)
              .slice(0, 6)
              .map((c) => (
                <li key={`${c.ticker}-${c.side}`} class={c.ev > 0 ? "pos" : "neg"}>
                  <div class="edge-what">
                    <strong>
                      {c.threshold.toString()}+ {data.label.toLowerCase()}
                    </strong>{" "}
                    <span class="muted">{contractSide(c)}</span>
                  </div>
                  <div class="edge-math num">{edgeLine(c)}</div>
                </li>
              ))}
          </ul>
          <p class="small muted">{state.data.note}</p>
        </>
      ) : null}
    </section>
  );
}
