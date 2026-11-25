import { render } from "preact";
import { App } from "./App";
import { applyDensity, applyMode, applyTheme, getPrefs } from "./prefs";
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
import "./pages-a.css";
import "./pages-b.css";
import "./combo/combo.css";
import "./colour.css";
import "./terminal.css";

applyTheme(getPrefs().theme);
applyDensity(getPrefs().density);
applyMode(getPrefs().mode);
void startUserStateSync();
void startWatchSync();
void startPhotoSync();
const root = document.getElementById("app");
// The static "Loading..." placeholder is replaced, not left behind under the app (Preact appends to existing children).
if (root) {
  root.textContent = "";
  render(<App />, root);
}

// The service worker is registered for production builds only (it needs the build's precache list).
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* offline support is a convenience; the app works without it */
    });
  });
}
