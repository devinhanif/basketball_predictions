import { useState } from "preact/hooks";
import { api } from "./api";
import { View } from "./components";
import {
  EVIDENCE_HEADLINE,
  arrow,
  playerSentence,
  ptsSentence,
  rebSentence,
  sittersFor,
  statusWord,
  type ScenarioSitter,
  type ScenariosView,
} from "./sits";
import { useLoad } from "./useLoad";

function Teammates({ s, playerId }: { s: ScenarioSitter; playerId: number }) {
  const likely = s.teammates.filter((t) => t.likely_plays && t.player_id !== playerId);
  if (likely.length === 0) return null;
  return (
    <ul class="sits-mates" aria-label={`Other teammates if ${s.name} sits`}>
      {likely.map((t) => (
        <li key={t.player_id}>
          <span class="sits-name">{t.name}</span>
          <span class="sits-dir" aria-label={`points ${t.pts}`}>
            pts {arrow(t.pts)}
          </span>
          {t.reb === null ? null : (
            <span class="sits-dir" aria-label={`rebounds ${t.reb}, uncertain size`}>
              reb {arrow(t.reb)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Picker({ view, playerId, playerName }: { view: ScenariosView; playerId: number; playerName: string }) {
  const sitters = sittersFor(view, playerId);
  const [pick, setPick] = useState<number | null>(null);
  if (!view.available || sitters.length === 0) {
    return (
      <p class="small muted">
        {view.available
          ? `No scenario for ${playerName}: none of his regular-starter teammates is available to sit in this data.`
          : (view.reason ?? "No scenarios in this data.")}
      </p>
    );
  }
  const s = sitters.find((x) => x.player_id === pick) ?? null;
  const reb = s ? rebSentence(s) : null;
  const mine = s ? playerSentence(s, playerId, playerName) : null;
  return (
    <>
      <p class="small muted">{EVIDENCE_HEADLINE}</p>
      <div class="sits-pick" role="group" aria-label="Which teammate sits">
        {sitters.map((x) => (
          <button
            key={x.player_id}
            type="button"
            class="seg-btn sits-btn"
            aria-pressed={pick === x.player_id}
            onClick={() => {
              setPick(pick === x.player_id ? null : x.player_id);
            }}
          >
            {x.name}
            <span class="sits-status small muted">{statusWord(x.status)}</span>
          </button>
        ))}
      </div>
      {s ? (
        <div class="sits-read" aria-live="polite">
          <p>{ptsSentence(s)}</p>
          {mine ? <p>{mine}</p> : null}
          {reb ? <p class="small">{reb}</p> : null}
          <Teammates s={s} playerId={playerId} />
        </div>
      ) : (
        <p class="small muted">Pick a teammate to see how the model's forecasts for the others move if he sits.</p>
      )}
      <details class="small sits-notes">
        <summary>What this is and is not</summary>
        <ul>
          {view.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
          {view.not_modelled.map((e) => (
            <li key={e}>Not modelled: {e}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

/** The Explore 'If a teammate sits' block: choose a sitter, read the model's forecast moves. A what-if on the
 * forecast, not a prediction that he sits; no price, no stake. Says plainly when the data has no scenarios. */
export function SitsPicker(props: { gameId: string; playerId: number; playerName: string }) {
  const [state, reload] = useLoad(() => api.scenarios(props.gameId), [props.gameId]);
  return (
    <div class="xp-block sits" aria-label="If a teammate sits">
      <div class="row between wrap">
        <h3>If a teammate sits</h3>
      </div>
      <View state={state} reload={reload} what="scenarios">
        {(v) => <Picker key={props.playerId} view={v} playerId={props.playerId} playerName={props.playerName} />}
      </View>
    </div>
  );
}
