import { useEffect, useRef, useState } from "preact/hooks";
import { Button } from "./ui";
import { setPref, usePrefs, type Mode } from "./prefs";
import { IS_ARTIFACT } from "./env";
import { cleanName, hasWelcomed, markWelcomed, setLocalName, useIdentity } from "./identity";
import { Icon } from "./icons";
import { LENS_LABEL } from "./Lens";
import { recordHref } from "./router";
import { termsHref } from "./terms";
import { openTour } from "./tourState";

/** What each view does today (the pages that read the preference: Discover, Game, Leg). No price words. */
const MODE_COPY: Record<Mode, { name: string; says: string; tag: string }> = {
  simple: {
    name: "Simple",
    says: "The short answer first. One line per player on Discover, and the deeper sections fold away until you open them.",
    tag: "Clear and quick to read.",
  },
  analyst: {
    name: "Analyst",
    says: "Everything opened up. Each forecast with its typical night and range, every player row in a game open, and the deeper sections on a player page shown in full.",
    tag: "More context, already open.",
  },
};

/**
 * Step one of the first visit: Simple or Analyst, as two cards (a radio group: arrows move, Enter or the button starts).
 * It sits in the page like the intro; it never covers the page or takes focus. Skipping keeps Simple.
 */
function ModeChooser(props: { onPick: (m: Mode) => void; onSkip: () => void }) {
  const [sel, setSel] = useState<Mode>(usePrefs().mode);
  const group = useRef<HTMLDivElement>(null);
  const order: readonly Mode[] = ["simple", "analyst"];
  const move = (m: Mode): void => {
    setSel(m);
    group.current?.querySelector<HTMLElement>(`[data-mode="${m}"]`)?.focus();
  };
  return (
    <section class="intro firstrun panel" aria-labelledby="fr-title">
      <div class="intro-head">
        <div class="fr-titles">
          <span class="fr-spark" aria-hidden="true">
            <Icon name="spark" />
          </span>
          <div class="eyebrow">One app, two ways to read it</div>
          <h2 id="fr-title">How do you like to see the game?</h2>
          <p class="muted fr-sub">Pick a starting view. You can switch any time from the profile menu or Preferences.</p>
        </div>
        <button type="button" class="icon-btn" aria-label="Skip the introduction and start in Simple" onClick={props.onSkip}>
          <Icon name="close" />
        </button>
      </div>
      <div class="fr-options" role="radiogroup" aria-label="Choose your starting view" ref={group}>
        {order.map((m, i) => {
          const c = MODE_COPY[m];
          const on = sel === m;
          return (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={on}
              data-mode={m}
              tabIndex={on ? 0 : -1}
              class={`fr-option${on ? " fr-on" : ""}`}
              onClick={() => {
                setSel(m);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowUp") {
                  e.preventDefault();
                  move(order[(i + 1) % order.length] as Mode);
                }
              }}
            >
              <span class="fr-top">
                <span class="fr-icon" aria-hidden="true">
                  <Icon name={m === "simple" ? "layout" : "sliders"} />
                </span>
                <span class="fr-radio" aria-hidden="true">
                  {on ? <Icon name="check" /> : null}
                </span>
              </span>
              <span class={`fr-preview fr-preview-${m}`} aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
              </span>
              <span class="fr-name">{c.name}</span>
              <span class="fr-says">{c.says}</span>
              <span class="fr-tag">{c.tag}</span>
            </button>
          );
        })}
      </div>
      <div class="row wrap intro-actions">
        <Button
          kind="primary"
          onClick={() => {
            props.onPick(sel);
          }}
        >
          Start in {MODE_COPY[sel].name} <Icon name="arrow" />
        </Button>
        <Button kind="quiet" onClick={props.onSkip}>
          Skip, just let me look
        </Button>
      </div>
      <p class="small muted fr-note">Your choice is remembered on this device.</p>
    </section>
  );
}

/**
 * First visit, one flow in the page: step one picks Simple or Analyst (above), step two is five plain points. Neither
 * covers the page or takes focus, and once dismissed (at either step) it stays dismissed on this device. The longer tour
 * is one tap away (and in the profile menu). No price words: it shows in every lens.
 */
export function Welcome() {
  const id = useIdentity();
  const [name, setName] = useState("");
  const [step, setStep] = useState<"choose" | "intro">("choose");
  const introTitle = useRef<HTMLHeadingElement>(null);
  const picked = useRef(false);
  useEffect(() => {
    // After a pick the chooser's button is gone: focus goes to the next step's heading, not to the top of the page.
    if (step === "intro" && picked.current) introTitle.current?.focus();
  }, [step]);
  if (hasWelcomed()) return null;
  const done = (): void => {
    if (!IS_ARTIFACT && cleanName(name) !== "") setLocalName(name);
    markWelcomed();
  };
  if (step === "choose")
    return (
      <ModeChooser
        onPick={(m) => {
          setPref("mode", m);
          picked.current = true;
          setStep("intro");
        }}
        onSkip={done}
      />
    );
  return (
    <section class="intro panel" aria-labelledby="intro-title">
      <div class="intro-head">
        <div>
          <div class="kicker">New here? Five things first</div>
          <h2 id="intro-title" tabIndex={-1} ref={introTitle}>See how tonight's NBA games could go</h2>
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
