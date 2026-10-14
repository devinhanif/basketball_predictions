import { render } from "preact";
import { App } from "./App";
import "./styles.css";
import "./leg.css";

const root = document.getElementById("app");
if (root) render(<App />, root);

// The service worker is registered for production builds only (it needs the build's precache list).
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* offline support is a convenience; the app works without it */
    });
  });
}
