// Entry of the single-file artifact page. No service worker, no manifest, no network: data is baked in (transport.static.ts)
// and shared picks light up through the platform's db and user capabilities when they resolve (sharedPicks.ts).
import { render } from "preact";
import { App } from "./App";
import { applyTheme, getPrefs } from "./prefs";
import { startShared, type ClaudeLike } from "./sharedPicks";
import { startUserStateSync } from "./userState";
import { startWatchSync } from "./watch";
import { startPhotoSync } from "./photos";
import "./theme.css";
import "./styles.css";
import "./leg.css";
import "./ui.css";

applyTheme(getPrefs().theme);
void startUserStateSync();
void startWatchSync();
void startPhotoSync();
const root = document.getElementById("app");
if (root) render(<App />, root);

// Render first, light the capabilities up when they answer (never during this first synchronous run).
const w = window as unknown as { claude?: ClaudeLike };
void startShared({
  claude: w.claude,
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (h) => {
    window.clearTimeout(h as number);
  },
});
