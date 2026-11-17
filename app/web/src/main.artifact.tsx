// Entry of the single-file artifact page. No service worker, no manifest, no network: data is baked in (transport.static.ts)
// and shared picks light up through the platform's db and user capabilities when they resolve (sharedPicks.ts).
import { render } from "preact";
import { App } from "./App";
import { applyDensity, applyTheme, getPrefs } from "./prefs";
import { startShared, type ClaudeLike } from "./sharedPicks";
import { startUserStateSync } from "./userState";
import { startWatchSync } from "./watch";
import { startPhotoSync } from "./photos";
import "./theme.css";
import "./styles.css";
import "./leg.css";
import "./ui.css";
import "./desk.css";
import "./picks.css";
import "./modes.css";
import "./live.css";
import "./ux.css";
import "./kit.css";
import "./honesty.css";
import "./jarvis.css";

applyTheme(getPrefs().theme);
applyDensity(getPrefs().density);
void startUserStateSync();
void startWatchSync();
void startPhotoSync();
const root = document.getElementById("app");
// The static "Loading..." placeholder is replaced, not left behind under the app (Preact appends to existing children).
if (root) {
  root.textContent = "";
  render(<App />, root);
}

// Render first, light the capabilities up when they answer (never during this first synchronous run).
const w = window as unknown as { claude?: ClaudeLike };
void startShared({
  claude: w.claude,
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (h) => {
    window.clearTimeout(h as number);
  },
});
