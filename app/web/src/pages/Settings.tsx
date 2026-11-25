import { useState } from "preact/hooks";
import { Avatar } from "../Avatar";
import { deskOf, useDesk } from "../deskStore";
import { IS_ARTIFACT } from "../env";
import { cleanName, setLocalName, useIdentity } from "../identity";
import { screenAllowed, setPref, usePrefs, useLens, type Density, type Mode, type Odds, type Screen, type Theme } from "../prefs";
import { useIsAdmin } from "../shellData";
import { useUserState, setNotify } from "../userState";
import { stamp } from "../format";
import { api } from "../api";
import { useLoad } from "../useLoad";
import { PageHead } from "../ui";

function Choice<T extends string>(props: {
  legend: string;
  help: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onPick: (v: T) => void;
}) {
  return (
    <fieldset class="setting">
      <legend>{props.legend}</legend>
      <p class="muted small">{props.help}</p>
      <div class="seg wide" role="group" aria-label={props.legend}>
        {props.options.map((o) => (
          <button
            key={o.value}
            type="button"
            class="seg-btn"
            aria-pressed={props.value === o.value}
            onClick={() => {
              props.onPick(o.value);
            }}
          >
            {o.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function Toggle(props: { id: string; label: string; help: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div class="toggle-row">
      <div>
        <label for={props.id}>
          <strong>{props.label}</strong>
        </label>
        <p class="small muted">{props.help}</p>
      </div>
      <button type="button" id={props.id} role="switch" aria-checked={props.on} class={`switch${props.on ? " on" : ""}`} onClick={() => { props.onChange(!props.on); }}>
        <span class="sr">{props.on ? "On" : "Off"}</span>
        <i />
      </button>
    </div>
  );
}

const THEMES: readonly { value: Theme; label: string }[] = [
  { value: "system", label: "Match my device" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];
const MODES: readonly { value: Mode; label: string }[] = [
  { value: "simple", label: "Simple" },
  { value: "analyst", label: "Analyst" },
];
const DENSITIES: readonly { value: Density; label: string }[] = [
  { value: "comfortable", label: "Comfortable" },
  { value: "compact", label: "Compact" },
];
const ODDS: readonly { value: Odds; label: string }[] = [
  { value: "american", label: "American (-110)" },
  { value: "decimal", label: "Decimal (1.91)" },
];
const SCREENS: readonly { value: Screen; label: string }[] = [
  { value: "today", label: "Discover" },
  { value: "signals", label: "Signals" },
  { value: "swipe", label: "Swipe" },
  { value: "picks", label: "My picks" },
];

function Status(props: { name: string; value: string; kind?: "ok" | "off" | "" }) {
  return (
    <li>
      <span>{props.name}</span>
      <span class={`chip ${props.kind ?? ""}`}>{props.value}</span>
    </li>
  );
}

export function Settings() {
  const p = usePrefs();
  const lens = useLens();
  const id = useIdentity();
  const us = useUserState();
  const desk = deskOf(useDesk());
  const isAdmin = useIsAdmin();
  const [meta] = useLoad(() => api.meta(), []);
  const [name, setName] = useState(id.name);
  const dataLine = meta.status === "ready" ? `Snapshot of ${stamp(meta.data.data_as_of)}` : "Loading";
  return (
    <div class="stack pb-page pb-prefs">
      <PageHead
        kicker="Make it yours"
        title="Preferences"
        description={`These are saved for you only${IS_ARTIFACT ? " (notification choices follow you between devices)" : ", on this device"}. There are no passwords.`}
      />
      <div class="two-col">
        <section class="panel settings" aria-label="Appearance and detail">
          <div class="kicker">Look and depth</div>
          <h2>Appearance and detail</h2>
          <Choice legend="Appearance" help="Match my device (the default) follows your phone. Light and Dark stay as you set them." value={p.theme} options={THEMES} onPick={(v) => { setPref("theme", v); }} />
          <Choice legend="Detail level" help={lens === "market" ? "Simple shows plain cards, one line per player. Analyst adds the line ladder, splits, calibration and the edge arithmetic." : "Simple shows plain cards, one line per player. Analyst opens every section: the line ladder, splits, calibration and the model behind each number."} value={p.mode} options={MODES} onPick={(v) => { setPref("mode", v); }} />
          <Choice legend="Opens on" help="The screen you land on when you open the app without a link." value={p.screen} options={SCREENS.filter((o) => screenAllowed(o.value, lens))} onPick={(v) => { setPref("screen", v); }} />
          <Choice legend="Density" help="Compact tightens the space inside panels and between rows, to fit more on a screen." value={p.density} options={DENSITIES} onPick={(v) => { setPref("density", v); }} />
          {lens === "market" ? (
            <Choice legend="Odds format" help="How a posted sportsbook price is written. Same price either way; decimal is the total return per 1 staked." value={p.odds} options={ODDS} onPick={(v) => { setPref("odds", v); }} />
          ) : null}
        </section>
        <section class="panel settings" aria-label="Notifications">
          <div class="kicker">The bell</div>
          <h2>Notifications</h2>
          <p class="small muted">Choose what counts toward the bell. Push notifications are not connected: nothing is sent to your phone or browser.</p>
          <Toggle id="n-fouls" label="Fouls and rotation" help="Foul trouble, benchings and returns." on={us.notify.fouls} onChange={(v) => { setNotify({ fouls: v }); }} />
          <Toggle id="n-risk" label="Model risk" help="Kill switch and model-review changes." on={us.notify.risk} onChange={(v) => { setNotify({ risk: v }); }} />
          <Toggle id="n-corr" label="Corrections" help="When an earlier observation is reversed." on={us.notify.corrections} onChange={(v) => { setNotify({ corrections: v }); }} />
        </section>
      </div>
      <div class="two-col">
        <section class="panel" aria-label="Your account">
          <div class="kicker">On this device</div>
          <h2>Your account</h2>
          <div class="acct">
            <Avatar name={id.name || "You"} src={id.avatarUrl} size="lg" />
            <div>
              <strong>{id.name || (id.source === "platform" ? "Someone" : "Guest")}</strong>
              <div class="small muted">
                {id.source === "platform" ? "Signed in to your account. The page's host tells us your name; we store only an id." : "A name kept on this device. It is never sent anywhere."}
              </div>
            </div>
          </div>
          {id.source === "local" ? (
            <form
              class="row wrap"
              onSubmit={(e) => {
                e.preventDefault();
                setLocalName(name);
              }}
            >
              <label class="field grow" for="acct-name">
                <span class="label">Display name</span>
                <input id="acct-name" type="text" maxLength={40} value={name} onInput={(e) => { setName(e.currentTarget.value); }} placeholder="Your name" />
              </label>
              <button type="submit" class="btn" disabled={cleanName(name) === id.name}>
                Save name
              </button>
            </form>
          ) : null}
          {isAdmin ? (
            <p>
              <a class="btn" href="#/admin">
                Open the Admin studio
              </a>
            </p>
          ) : null}
        </section>
        <section class="panel" aria-label="Ready to connect">
          <div class="kicker">Said honestly</div>
          <h2>Ready to connect</h2>
          <p class="small muted">What is connected in this view, said honestly.</p>
          <ul class="status-list">
            <Status name="Identity" value={id.source === "platform" ? "Signed-in account" : "Local name"} kind="ok" />
            <Status name="Data" value={dataLine} kind="ok" />
            <Status name="Desk" value={desk ? `${desk.privacy} cut${desk.fixture ? ", synthetic fixture" : ""}` : "Not in this build"} kind={desk ? "ok" : "off"} />
            <Status name="Market data" value="Stored quotes only" />
            <Status name="Push notifications" value="Not connected" kind="off" />
          </ul>
        </section>
      </div>
    </div>
  );
}
