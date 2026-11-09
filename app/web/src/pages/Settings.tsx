import { setPref, usePrefs, type Mode, type Screen, type Theme } from "../prefs";

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

const THEMES: readonly { value: Theme; label: string }[] = [
  { value: "system", label: "Match my device" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];
const MODES: readonly { value: Mode; label: string }[] = [
  { value: "simple", label: "Simple" },
  { value: "analyst", label: "Analyst" },
];
const SCREENS: readonly { value: Screen; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "swipe", label: "Swipe" },
  { value: "picks", label: "My picks" },
];

export function Settings() {
  const p = usePrefs();
  return (
    <div class="stack">
      <header class="page-head">
        <h1>Settings</h1>
        <p class="muted">These are saved on this device only. There are no accounts.</p>
      </header>
      <section class="panel settings">
        <Choice
          legend="Theme"
          help="Dark is the default. Match my device follows your system setting."
          value={p.theme}
          options={THEMES}
          onPick={(v) => {
            setPref("theme", v);
          }}
        />
        <Choice
          legend="Detail"
          help="Simple shows plain cards, one line per player. Analyst adds the line ladder, splits, calibration and the edge arithmetic."
          value={p.mode}
          options={MODES}
          onPick={(v) => {
            setPref("mode", v);
          }}
        />
        <Choice
          legend="Opens on"
          help="The screen you land on when you open the app without a link."
          value={p.screen}
          options={SCREENS}
          onPick={(v) => {
            setPref("screen", v);
          }}
        />
      </section>
    </div>
  );
}
