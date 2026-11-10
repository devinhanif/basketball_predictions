import { useState } from "preact/hooks";
import { Avatar } from "./Avatar";
import { Footer } from "./components";
import { IS_ARTIFACT } from "./env";
import { cleanName, hasWelcomed, markWelcomed, setLocalName, useIdentity } from "./identity";
import { Icon, Logo } from "./icons";
import { setPref, usePrefs, type Mode } from "./prefs";
import { openTour, tourSeen } from "./tourState";

const CHOICES: { mode: Mode; title: string; body: string; tag: string }[] = [
  { mode: "simple", title: "Simple", body: "The bigger picture at a glance: tonight's answer, the games in plain words, one line per player.", tag: "Clear. Focused. Easy to explore." },
  { mode: "analyst", title: "Analyst", body: "Every number with its working: the line ladder, splits, calibration and the edge arithmetic after fees.", tag: "More context. More control." },
];

/** First run: who is looking (the platform's identity in the shared page; a name typed once in the regular build), then how to see the game. */
export function Welcome() {
  const id = useIdentity();
  const prefs = usePrefs();
  const [step, setStep] = useState<"who" | "mode">(IS_ARTIFACT ? "who" : "who");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>(prefs.mode);
  if (hasWelcomed()) return null;
  const display = id.name || cleanName(name);
  if (!IS_ARTIFACT) {
    // One screen, one action: the name is optional and the Simple/Analyst choice lives on Discover and in Preferences.
    return (
      <div class="welcome" role="dialog" aria-modal="true" aria-label="Welcome">
        <div class="welcome-in">
          <div class="welcome-brand">
            <Logo />
            <strong>parlayhitters</strong>
          </div>
          <section class="welcome-card">
            <h1>See how tonight's NBA games could go</h1>
            <p class="muted">
              Forecasts with their working shown. We do not sell picks or take bets. Until the record is long enough, the honest answer to
              "what should I bet?" is keep your money.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (cleanName(name) !== "") setLocalName(name);
                setPref("mode", mode);
                markWelcomed();
                if (!tourSeen()) openTour();
              }}
            >
              <label class="field" for="w-name">
                <span class="label">Your name (optional, stays on this phone)</span>
                <input id="w-name" type="text" maxLength={40} value={name} onInput={(e) => { setName(e.currentTarget.value); }} placeholder="e.g. Sam" autoComplete="off" />
              </label>
              <button type="submit" class="btn primary wide">
                See tonight's games <Icon name="arrow" />
              </button>
            </form>
          </section>
          <Footer />
        </div>
      </div>
    );
  }
  return (
    <div class="welcome" role="dialog" aria-modal="true" aria-label="Welcome">
      <div class="welcome-in">
        <div class="welcome-brand">
          <Logo />
          <strong>parlayhitters</strong>
        </div>
        {step === "who" ? (
          <section class="welcome-card">
            <div class="label">Welcome</div>
            <h1>Who is looking?</h1>
            {(
              <>
                <div class="acct">
                  <Avatar name={id.name || "You"} src={id.avatarUrl} size="lg" />
                  <div>
                    <strong>{id.loading ? "Checking..." : id.name || "Someone"}</strong>
                    <div class="small muted">
                      {id.source === "platform" && id.id !== null
                        ? "Signed in to your account. There is no password here: the page's host tells us who you are."
                        : "No identity came from the platform in this view, so your picks stay on this device."}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  class="btn primary wide"
                  onClick={() => {
                    setStep("mode");
                  }}
                >
                  Continue <Icon name="arrow" />
                </button>
              </>
            )}
          </section>
        ) : (
          <section class="welcome-card wide">
            <div class="label">One workspace, two perspectives</div>
            <h1>How do you like to see the game?</h1>
            <p class="muted">{display ? `${display}, choose` : "Choose"} your starting view. You can switch between both modes anytime.</p>
            <div class="mode-cards" role="radiogroup" aria-label="Starting view">
              {CHOICES.map((c) => (
                <button
                  key={c.mode}
                  type="button"
                  role="radio"
                  aria-checked={mode === c.mode}
                  class={`mode-card${mode === c.mode ? " on" : ""}`}
                  onClick={() => {
                    setMode(c.mode);
                  }}
                >
                  <span class="mode-top">
                    <Icon name={c.mode === "simple" ? "grid" : "sliders"} />
                    <span class="mode-radio" aria-hidden="true">{mode === c.mode ? <Icon name="check" /> : null}</span>
                  </span>
                  <strong class="mode-title">{c.title}</strong>
                  <span class="muted">{c.body}</span>
                  <span class="mode-tag">{c.tag}</span>
                </button>
              ))}
            </div>
            <div class="row wrap center">
              <button
                type="button"
                class="btn"
                onClick={() => {
                  setStep("who");
                }}
              >
                Back
              </button>
              <button
                type="button"
                class="btn primary"
                onClick={() => {
                  setPref("mode", mode);
                  markWelcomed();
                  if (!IS_ARTIFACT && !tourSeen()) openTour();
                }}
              >
                Start in {mode === "simple" ? "Simple" : "Analyst"} mode <Icon name="arrow" />
              </button>
            </div>
            <p class="small muted center">Your choice is remembered on this device.</p>
          </section>
        )}
        <Footer />
      </div>
    </div>
  );
}
