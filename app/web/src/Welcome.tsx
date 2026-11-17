import { useState } from "preact/hooks";
import { Button } from "./ui";
import { IS_ARTIFACT } from "./env";
import { cleanName, hasWelcomed, markWelcomed, setLocalName, useIdentity } from "./identity";
import { Icon } from "./icons";
import { LENS_LABEL } from "./Lens";
import { recordHref } from "./router";
import { termsHref } from "./terms";
import { openTour } from "./tourState";

/**
 * First visit: five plain points at the top of whatever page was opened. It does not cover the page or take focus, and
 * once dismissed it stays dismissed on this device. The longer tour is one tap away (and in the profile menu).
 * No price words: it shows in every lens.
 */
export function Welcome() {
  const id = useIdentity();
  const [name, setName] = useState("");
  if (hasWelcomed()) return null;
  const done = (): void => {
    if (!IS_ARTIFACT && cleanName(name) !== "") setLocalName(name);
    markWelcomed();
  };
  return (
    <section class="intro panel" aria-labelledby="intro-title">
      <div class="intro-head">
        <div>
          <div class="kicker">New here? Five things first</div>
          <h2 id="intro-title">See how tonight's NBA games could go</h2>
        </div>
        <button type="button" class="icon-btn" aria-label="Dismiss the introduction" onClick={done}>
          <Icon name="close" />
        </button>
      </div>
      <ol class="intro-list">
        <li>
          <b>What this is.</b> Forecasts for NBA games and players, with the working shown. We do not take bets or sell picks.
        </li>
        <li>
          <b>What a forecast is.</b> A range, not a promise: a typical night and how far either side most nights land.
        </li>
        <li>
          <b>Why "keep your money".</b> Until the live record is long enough to prove skill, that is the honest answer to "what should I bet?". A night with nothing
          worth taking is a result, not an error.
        </li>
        <li>
          <b>Lenses.</b> {LENS_LABEL.learn} explains the game and the forecast (the default), {LENS_LABEL.explore} lets you move the line and the minutes, and{" "}
          {LENS_LABEL.market} adds the market's side.
        </li>
        <li>
          <b>Where the record is.</b>{" "}
          {IS_ARTIFACT ? (
            <>
              The live track record is in the full app; <a href={termsHref()}>Key terms</a> explains every word used here.
            </>
          ) : (
            <>
              The <a href={recordHref("replay", null)}>Track record</a> replays past forecasts as they stood before tip. <a href={termsHref()}>Key terms</a>{" "}
              explains every word.
            </>
          )}
        </li>
      </ol>
      {IS_ARTIFACT ? (
        <p class="small muted">
          {id.source === "platform" && id.id !== null
            ? `${id.name || "Signed in"}: signed in to your account. There is no password here: the page's host tells us who you are.`
            : "No identity came from the platform in this view, so your picks stay on this device."}
        </p>
      ) : (
        <label class="field intro-name" for="w-name">
          <span class="label">Your name (optional, stays on this device)</span>
          <input id="w-name" type="text" maxLength={40} value={name} onInput={(e) => { setName(e.currentTarget.value); }} placeholder="e.g. Sam" autoComplete="off" />
        </label>
      )}
      <div class="row wrap intro-actions">
        <Button kind="primary" onClick={done}>
          See tonight's games <Icon name="arrow" />
        </Button>
        {IS_ARTIFACT ? null : (
          <Button
            onClick={() => {
              done();
              openTour();
            }}
          >
            Take the short tour
          </Button>
        )}
      </div>
    </section>
  );
}
