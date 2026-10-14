// Role facts shown on the game page and the leg screen: minutes steadiness, team on/off, and a
// player's numbers without a teammate who is out tonight. All numbers come from the API; every
// rate prints its n and every small sample says so.
import { num } from "./format";
import { badgeText, netText, onoffN, per36N, per36Text, possText, rangeOf, signed } from "./role";
import type { MateOut, OnOff, OnOffWindow, Per36, Steadiness } from "./roleTypes";

const cls = (s: Steadiness): string => `steady steady-${s.label === "Limited data" ? "limited" : s.label.toLowerCase()}`;

/** The compact badge. It always carries n and is described as a fact about his minutes. */
export function SteadyBadge({ s }: { s: Steadiness }) {
  return (
    <span class={cls(s)} title={s.say}>
      <span class="sr">Role steadiness: </span>
      {badgeText(s)}
    </span>
  );
}

/** Steadiness with its plain sentence and caveat. */
export function SteadyBlock({ s }: { s: Steadiness }) {
  return (
    <div class="role-block">
      <div class="row between wrap">
        <h3>Role steadiness</h3>
        <SteadyBadge s={s} />
      </div>
      <p class="small num" style={{ marginBottom: 4 }}>
        {s.say}
      </p>
      <p class="small muted">{s.caveat}</p>
    </div>
  );
}

function OnOffRow({ label, w }: { label: string; w: OnOffWindow }) {
  const range = rangeOf(w);
  return (
    <div class={`role-win${w.small ? " thin" : ""}`}>
      <div class="role-win-head">
        <strong>{label}</strong>
        <span class="small muted num">{onoffN(w)}</span>
      </div>
      <div class="role-win-cells num">
        <div>
          <div class="label">He is on</div>
          <div class="role-net">{netText(w.on)}</div>
          <div class="small muted">{possText(w.on)}</div>
        </div>
        <div>
          <div class="label">He is off</div>
          <div class="role-net">{netText(w.off)}</div>
          <div class="small muted">{possText(w.off)}</div>
        </div>
        <div>
          <div class="label">Gap</div>
          <div class="role-net">{w.diff === null ? <span class="muted">none</span> : signed(w.diff)}</div>
          {range ? <div class="small muted">95% range {range}</div> : null}
        </div>
      </div>
    </div>
  );
}

/** Team net points per 100 possessions with him on and off the floor. */
export function OnOffBlock({ o, player }: { o: OnOff; player: string }) {
  if (!o.available) {
    return (
      <div class="role-block">
        <h3>On the floor and off it</h3>
        <p class="small muted">{o.note}</p>
      </div>
    );
  }
  const small = o.this_season.small || o.since_last_season.small;
  return (
    <div class="role-block">
      <h3>On the floor and off it</h3>
      <p class="small muted" style={{ marginTop: 0 }}>
        Team points scored minus points allowed per 100 possessions, in games {player} played.
      </p>
      <OnOffRow label="This season" w={o.this_season} />
      <OnOffRow label="Since last season" w={o.since_last_season} />
      {small ? (
        <p class="leg-flag" role="note">
          <strong>Small sample.</strong> Dimmed rows rest on few games or fewer than 1,000 possessions on one side. Treat the gap as noise.
        </p>
      ) : null}
      <p class="small muted">{o.note}</p>
    </div>
  );
}

function Per36Cell({ label, p }: { label: string; p: Per36 }) {
  const text = per36Text(p);
  return (
    <div class={`leg-cell${p.small ? " thin" : ""}`}>
      <div class="label">{label}</div>
      {text === null ? (
        <div class="leg-cell-main muted">no games</div>
      ) : (
        <>
          <div class="role-36 num">{text}</div>
          <div class="small muted num">{p.avg_minutes !== null ? `${num(p.avg_minutes, 0)} min a game` : ""}</div>
        </>
      )}
      <div class="small muted num">{per36N(p)}</div>
    </div>
  );
}

/** His minutes and per-36 numbers with and without one out teammate. */
export function MatePer36({ without, with_, name }: { without: Per36; with_: Per36; name: string }) {
  return (
    <div class="leg-cells role-mate-cells">
      <Per36Cell label={`Without ${name}`} p={without} />
      <Per36Cell label={`With ${name}`} p={with_} />
    </div>
  );
}

function cleanReason(r: string | null): string {
  if (!r || r === "-") return "";
  return r.replace(/^Injury\/Illness-/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

/** Tonight's out teammates and how the player did in games without each (game-page version). */
export function MatesBlock({ mates, player }: { mates: MateOut[]; player: string }) {
  return (
    <div class="role-block">
      <h3>Without a teammate who is out tonight</h3>
      {mates.length === 0 ? (
        <p class="small muted">No rotation teammates are listed out, doubtful or questionable on the injury report.</p>
      ) : (
        <div class="stack tight">
          {mates.map((m) => (
            <div key={m.player_id} class="leg-mate">
              <div class="row between wrap">
                <strong>{m.name}</strong>
                <span class={`tag leg-status ${m.status}`}>{m.status}</span>
              </div>
              <div class="small muted">
                {cleanReason(m.reason) ? `${cleanReason(m.reason)}. ` : ""}Plays {num(m.avg_minutes, 0)} minutes a game lately.
              </div>
              <MatePer36 without={m.without} with_={m.with_} name={m.name} />
              <p class="small muted" style={{ marginBottom: 0 }}>
                {player} in games with and without him, per 36 minutes. {m.note}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
