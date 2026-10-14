// Applies a saved theme before first paint. A separate file, not inline, so the CSP can ban inline script.
try {
  var t = localStorage.getItem("ph.theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {
  /* storage blocked: follow the system theme */
}
