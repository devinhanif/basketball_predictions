// Applies a saved theme and detail level before first paint, so there is no flash of the wrong theme. A separate file, not
// inline, so the CSP can ban inline script. The stored values are ph.theme ("light" | "dark"; anything else is Auto, which
// follows the phone through prefers-color-scheme) and ph.mode.
try {
  var t = localStorage.getItem("ph.theme");
  if (t === "light" || t === "dark") {
    document.documentElement.dataset.theme = t;
    var bar = t === "light" ? "#fbfafd" : "#0d0b12";
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", bar);
  }
  document.documentElement.dataset.mode = localStorage.getItem("ph.mode") === "analyst" ? "analyst" : "simple";
} catch (e) {
  /* storage blocked: follow the system theme */
}
