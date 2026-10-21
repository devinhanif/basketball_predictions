import { useState } from "preact/hooks";
import {
  BUILDS,
  cap,
  STAT_NAME,
  draftText,
  isThin,
  leaguePct,
  ordinal,
  potentialText,
  zonePct,
  type BuildCard,
} from "../build";

const pctText = (x: number | null): string => (x === null ? "-" : `${(x * 100).toFixed(0)}%`);

function Sheet({ c }: { c: BuildCard }) {
  const { meta } = BUILDS;
  const cuts = meta.level_cut_points_minutes_share;
  const thin = isThin(c);
  const trend = c.minutes_trend;
  return (
    <article class="panel sheet" aria-label={`Build card for ${c.name}`}>
      <header class="sheet-head">
        <div>
          <h2>{c.name}</h2>
          <p class="small muted">
            {c.team}
            {c.position_listed ? `, listed ${c.position_listed.toLowerCase()}` : ""}
          </p>
        </div>
        <div class="sheet-level" aria-label={`Level ${c.level.toString()} of 5`}>
          <span class="label">Level</span>
          <b class="big-num">{c.level}</b>
          <span class="small muted">of 5</span>
        </div>
      </header>

      <section class="sheet-sec">
        <h3>Class</h3>
        <p class="muted">Not assigned yet. {cap(c.class_note)}.</p>
        <p class="small muted">Source: players_static (listed position only), as of {meta.as_of}.</p>
      </section>

      <section class="sheet-sec">
        <h3>Level {c.level}: how much he plays</h3>
        <p class="num">
          <b>{(c.minutes_share * 48).toFixed(1)}</b> minutes a game ({pctText(c.minutes_share)} of 48), starts{" "}
          <b>{pctText(c.starter_rate)}</b> of games. n = {c.n_games_window.toString()} games.
        </p>
        <div class="lvl" role="img" aria-label={`Minutes share ${pctText(c.minutes_share)} against level cut points`}>
          {[1, 2, 3, 4, 5].map((l) => (
            <span key={l} class={l === c.level ? "on" : l < c.level ? "below" : ""}>
              {l}
            </span>
          ))}
        </div>
        <p class="small muted">
          Level cut points, in share of 48 minutes: {cuts.map((x) => x.toFixed(3)).join(", ")}. {cap(meta.level_rule)}.
        </p>
        <p class="small muted">Source: player_game_stats, {meta.window}, as of {meta.as_of}.</p>
      </section>

      <section class="sheet-sec">
        <h3>Potential</h3>
        <p class="num">
          {draftText(c)}. {c.age !== null ? `Age ${c.age.toFixed(1)}. ` : ""}
          {c.seasons_played !== null ? `Season ${c.seasons_played.toString()} in the league. ` : ""}
        </p>
        <p class="num small">
          Minutes lately: <b>{trend.recent_mpg.toFixed(1)}</b> a game over his last {trend.recent_n.toString()}
          {trend.prior_mpg !== null ? `, against ${trend.prior_mpg.toFixed(1)} over the ${trend.prior_n.toString()} before` : ""}.
        </p>
        <p class="num">
          <b>Range:</b> {potentialText(c.potential)}.
        </p>
        <p class="small muted">
          A range of what happened to similar players, not a promise.{" "}
          {c.potential ? cap(c.potential.rule) + "." : ""} Sources: players_static, player_game_stats, as of {meta.as_of}.
        </p>
      </section>

      <section class="sheet-sec">
        <h3>Skills, per 36 minutes</h3>
        <p class="small muted">Bar length = rank among {meta.sources["percentile pool"]}.</p>
        <ul class="bars">
          {c.bars.map((b) => (
            <li key={b.stat}>
              <div class="row between">
                <span>{STAT_NAME[b.stat] ?? b.stat}</span>
                <span class="num">
                  <b>{b.per36.toFixed(1)}</b> · {ordinal(b.percentile)}
                </span>
              </div>
              <div class="bar" aria-hidden="true">
                <i style={{ width: `${Math.max(2, b.percentile).toString()}%` }} />
              </div>
              <div class="small muted num">
                n = {b.n_games.toString()} games, {b.n_minutes.toLocaleString("en-US")} minutes
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section class="sheet-sec">
        <h3>Shooting, by zone</h3>
        <p class="small muted">His field-goal % against the league's in the same zone (tick).</p>
        <ul class="bars">
          {c.zones.map((z) => {
            const p = zonePct(z);
            const lg = leaguePct(z);
            return (
              <li key={z.zone}>
                <div class="row between">
                  <span>{z.zone}</span>
                  <span class="num">
                    <b>{pctText(p)}</b> <span class="muted">league {pctText(lg)}</span>
                  </span>
                </div>
                <div class="bar" aria-hidden="true">
                  <i style={{ width: `${Math.max(1, (p ?? 0) * 100).toString()}%` }} />
                  {lg !== null ? <u style={{ left: `${(lg * 100).toString()}%` }} /> : null}
                </div>
                <div class="small muted num">
                  n = {z.made.toString()} of {z.att.toString()} shots{z.att < 30 ? " (small sample)" : ""}
                </div>
              </li>
            );
          })}
        </ul>
        <p class="small muted">Source: shot_events, {meta.window}, as of {meta.as_of}.</p>
      </section>

      {thin && c.borrowed ? (
        <section class="sheet-sec borrowed">
          <h3>Borrowed build</h3>
          <p>
            We have seen only <b>{c.n_games_window.toString()}</b> of his own games, so the card also shows what{" "}
            <b>{c.borrowed.n_peers.toString()}</b> comparable players did early in their careers. This stands in for
            archetype neighbours until the clustering exists.
          </p>
          <p class="small muted">Peers: {c.borrowed.rule}.</p>
          <ul class="kv num">
            {Object.entries(c.borrowed.per36).map(([k, v]) => (
              <li key={k}>
                {STAT_NAME[k] ?? k}: <b>{v.toFixed(1)}</b>
              </li>
            ))}
          </ul>
          <p class="small muted">Per 36 minutes, mean across the peers' first 20 games. Source: players_static, player_game_stats.</p>
        </section>
      ) : null}
    </article>
  );
}

export function BuildPage(props: { playerId: number | null }) {
  const first = BUILDS.cards[0];
  const [id, setId] = useState<number | null>(props.playerId ?? first?.player_id ?? null);
  const card = BUILDS.cards.find((c) => c.player_id === id) ?? first;
  if (!card) return <p class="muted">No build cards in this data.</p>;
  return (
    <div class="stack">
      <div class="page-head">
        <h1>Player builds</h1>
        <p class="muted">
          A character sheet for each player, made only from stored games, as of {BUILDS.meta.as_of}. {BUILDS.meta.note}
        </p>
      </div>
      <div class="st-seg-row" role="group" aria-label="Player">
        {BUILDS.cards.map((c) => (
          <button key={c.player_id} type="button" class="st-chip" aria-pressed={c.player_id === card.player_id} onClick={() => { setId(c.player_id); }}>
            {c.name}
          </button>
        ))}
      </div>
      <Sheet c={card} />
    </div>
  );
}
