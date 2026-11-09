// Runs in the page (see shots.mjs and audit-ui.mjs): finds visible text under 12px, visible text under 4.5:1 contrast
// (3:1 for large text), and horizontal overflow. Evaluated as an expression: `(function () {...})()`.
function () {
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const pageBg = parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
  const root = parse(getComputedStyle(document.documentElement).backgroundColor);
  const base = pageBg.a > 0 ? pageBg : root && root.a > 0 ? root : { r: 255, g: 255, b: 255, a: 1 };
  base.a = 1;

  // Every candidate background under an element: stacks solid layers from the root down; a gradient contributes each of its stops.
  const backgrounds = (el) => {
    const chain = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) chain.unshift(n);
    let cands = [base];
    for (const n of chain) {
      const cs = getComputedStyle(n);
      const bg = parse(cs.backgroundColor);
      const grad = cs.backgroundImage && cs.backgroundImage !== "none" ? (cs.backgroundImage.match(/rgba?\([^)]+\)/g) || []).map(parse) : [];
      const layers = grad.length ? grad : bg && bg.a > 0 ? [bg] : [];
      if (layers.length) cands = cands.flatMap((under) => layers.map((l) => over(l, under)));
    }
    return cands;
  };
  const opacityOf = (el) => { let o = 1; for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === "none" || cs.visibility === "hidden") return false;
      if (n.hasAttribute("aria-hidden") && n.getAttribute("aria-hidden") === "true" && !n.closest("svg")) { /* decorative glyphs still need to be legible; keep checking */ }
    }
    return true;
  };
  const sel = (el) => el.tagName.toLowerCase() + (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "");

  const small = [], lowContrast = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const text = (t.nodeValue || "").trim();
    if (!text) continue;
    const el = t.parentElement;
    if (!el || seen.has(el) && !text) continue;
    if (el.closest("script,style,noscript")) continue;
    if (el.closest(".sr, .skip-link")) continue;
    if (!visible(el)) continue;
    if (el.closest("[disabled], [aria-disabled='true']")) continue;
    const cs = getComputedStyle(el);
    let px = parseFloat(cs.fontSize);
    const svg = el.closest("svg");
    if (svg && el.ownerSVGElement) {
      const m = el.getScreenCTM();
      if (m) px *= Math.sqrt(m.a * m.a + m.b * m.b);
    }
    const bold = Number(cs.fontWeight) >= 700;
    const large = px >= 24 || (bold && px >= 18.66);
    if (px < 11.995) small.push({ px: Math.round(px * 10) / 10, text: text.slice(0, 40), sel: sel(el) });
    const colour = svg && el.ownerSVGElement ? cs.fill : cs.color;
    const fg0 = parse(colour);
    if (!fg0) continue;
    const alpha = fg0.a * opacityOf(el);
    let worst = Infinity, worstFg = "", worstBg = "";
    for (const bg of backgrounds(el)) {
      const fg = over({ ...fg0, a: alpha }, bg);
      const r = ratio(fg, bg);
      if (r < worst) { worst = r; worstFg = `${Math.round(fg.r)},${Math.round(fg.g)},${Math.round(fg.b)}`; worstBg = `${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)}`; }
    }
    const need = large ? 3 : 4.5;
    if (worst < need) lowContrast.push({ ratio: Math.round(worst * 100) / 100, text: text.slice(0, 40), sel: sel(el), fg: worstFg, bg: worstBg, px: Math.round(px) });
  }
  const dedupe = (xs) => { const k = new Set(); return xs.filter((x) => { const key = x.sel + x.text; if (k.has(key)) return false; k.add(key); return true; }); };
  const doc = document.documentElement;
  // Elements poking past the right edge, unless a scrolling container (tabs, tables) holds them.
  const poking = [];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.right <= window.innerWidth + 1) continue;
    let held = false;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if ((ox === "auto" || ox === "scroll" || ox === "hidden") && !n.matches(".welcome, .modal-back")) { held = true; break; }
    }
    if (!held && getComputedStyle(el).position !== "fixed") poking.push(sel(el) + " right=" + Math.round(r.right));
  }
  return {
    pokes: poking.slice(0, 8),
    overflowX: poking.length > 0 || Math.max(doc.scrollWidth, document.body.scrollWidth) > window.innerWidth + 1,
    scrollWidth: Math.max(doc.scrollWidth, document.body.scrollWidth),
    innerWidth: window.innerWidth,
    small: dedupe(small).slice(0, 40),
    lowContrast: dedupe(lowContrast).slice(0, 40),
  };
}
