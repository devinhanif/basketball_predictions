// PROPS: is this player's line good? Each game's key lines with the stored chance (the same fields and the same
// calibrated-or-model tag the Discover list shows), and a door back to the game and on to the slip check. A row opens the
// player's page. No new data and no price: a price never appears in the Learn or Explore lens, as everywhere.
import { api } from "../api";
import { Avatar } from "../Avatar";
import { CalTag, View } from "../components";
import { Door, DoorGroup } from "../Doors";
import { lineText, longDate, num, pctBounded, tipTime } from "../format";
import { chronological, defaultNight } from "../nights";
import { JOBS } from "../jobs";
import { gameHref, legHref, propsHref } from "../router";
import { useLoad } from "../useLoad";
import { PageHead, StateBlock } from "../ui";
import { STAT_LABEL, type GameSummary, type KeyProp } from "../types";
import { doesText, rangeSentence } from "../words";
import { JobNights } from "./Teams";

/** A bar from 30% to 70% with the 50% line marked; the marker is the stored chance, clamped to the scale. */
function ChanceBar({ p, label }: { p: number; label: string }) {
  const x = Math.min(0.7, Math.max(0.3, p));
  const pos = ((x - 0.3) / 0.4) * 200;
  return (
    <svg class="chance-bar" width="100%" height="12" viewBox="0 0 200 12" preserveAspectRatio="none" role="img" aria-label={label}>
      <rect x="0" y="0" width="200" height="12" class="cb-track" />
      <line x1="100" y1="0" x2="100" y2="12" class="cb-mid" />
      <rect x={Math.max(0, Math.min(195, pos - 2.5))} y="1" width="5" height="10" class="cb-mark" />
    </svg>
  );
}

function PropRow({ g, p }: { g: GameSummary; p: KeyProp }) {
  const reference = p.line_source === "reference";
  const say = reference ? `${rangeSentence(p.stat, num(p.q50), num(p.q25), num(p.q75))}.` : `${STAT_LABEL[p.stat]} over ${lineText(p.line)}: ${doesText(p.stat, p.line, "over")}`;
  const team = p.team === g.home.abbr ? g.home.abbr : g.away.abbr;
  return (
    <li>
      <a class="prop-row" href={legHref(g.game_id, p.player_id, p.stat)}>
        <Avatar name={p.name} team={team} playerId={p.player_id} size="md" remote />
        <span class="prop-row-main">
          <span class="prop-row-name">
            {p.name} <span class="muted">{p.team}</span>
          </span>
          <span class="prop-row-say">{say}</span>
          {reference ? null : <ChanceBar p={p.model_p_over} label={`Chance of the over: ${pctBounded(p.model_p_over, p.extrapolated)}, on a scale from 30 to 70 percent`} />}
        </span>
        <span class="prop-row-num num">
          {reference ? (
            <>
              <b>{num(p.q50)}</b>
              <span class="muted small">typical night</span>
            </>
          ) : (
            <>
              <b>{pctBounded(p.model_p_over, p.extrapolated)}</b>
              <CalTag source={p.model_p_source} />
            </>
          )}
        </span>
      </a>
    </li>
  );
}

function GameProps({ g }: { g: GameSummary }) {
  return (
    <section class="props-game" aria-labelledby={`pg-${g.game_id}`}>
      <div class="props-game-head">
        <h2 id={`pg-${g.game_id}`}>
          {g.away.abbr} at {g.home.abbr}
        </h2>
        <span class="muted num">{tipTime(g.tip_utc)}</span>
      </div>
      {g.key_props.length === 0 ? (
        <p class="muted small">No player lines for this game.</p>
      ) : (
        <ul class="prop-rows">
          {g.key_props.map((p) => (
            <PropRow key={`${p.player_id.toString()}-${p.stat}`} g={g} p={p} />
          ))}
        </ul>
      )}
      <DoorGroup label={`Go deeper on ${g.away.abbr} at ${g.home.abbr}`} class="inline">
        <Door to="teams" href={gameHref(g.game_id)}>
          the game, its win chances and every player
        </Door>
      </DoorGroup>
    </section>
  );
}

function PropsNight(props: { date: string; nights: string[]; game: string | null; onStale: (s: boolean) => void }) {
  const { date } = props;
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  return (
    <>
      <PageHead
        kicker={`${longDate(date)} · NBA`}
        title="Player props"
        description="One player, one line, one chance. Tap a row for the player's page."
      />
      <JobNights dates={props.nights} date={date} base="#/props" />
      <View state={slate} reload={reload} onStale={props.onStale} what="tonight's lines" errorLevel={2}>
        {(s) => {
          const games = props.game === null ? s.games : s.games.filter((g) => g.game_id === props.game);
          if (games.length === 0) {
            return (
              <StateBlock
                kind="empty"
                title={props.game === null ? "No games this night" : "That game is not on this night"}
                action={props.game === null ? undefined : <a class="btn" href={propsHref()}>All games</a>}
              >
                {props.game === null ? `The data holds no games for ${longDate(date)}.` : "Nothing is shown in its place."}
              </StateBlock>
            );
          }
          return (
            <>
              {props.game !== null ? (
                <p class="small">
                  <a href={propsHref()}>Show every game</a>
                </p>
              ) : null}
              <p class="small muted legend-line">
                <span class="chance-key" aria-hidden="true" /> the chance of the over. The scale runs from 30 to 70%, with the line at 50%.
              </p>
              {games.map((g) => (
                <GameProps key={g.game_id} g={g} />
              ))}
              <DoorGroup label="Check a slip">
                <Door to="slip" href={JOBS.slip.href}>
                  does a parlay hold together?
                </Door>
              </DoorGroup>
            </>
          );
        }}
      </View>
    </>
  );
}

export function Props(props: { date: string | null; game: string | null; onStale: (s: boolean) => void }) {
  const [list, reload] = useLoad(() => api.slates(), []);
  return (
    <div class="stack">
      <View state={list} reload={reload} onStale={props.onStale} what="the list of nights">
        {(raw) => {
          const slates = chronological(raw);
          const date = props.date ?? defaultNight(slates);
          return date ? (
            <PropsNight date={date} nights={slates.map((s) => s.date)} game={props.game} onStale={props.onStale} />
          ) : (
            <>
              <PageHead title="Player props" description="One player, one line, one chance." />
              <StateBlock kind="empty" title="No nights are loaded">
                This copy of the data holds no slates, so there is nothing to show yet. Nothing is broken; check back after the nightly run.
              </StateBlock>
            </>
          );
        }}
      </View>
    </div>
  );
}
