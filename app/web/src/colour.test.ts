// Colour as navigation: the tokens in colour.css, in BOTH themes, hold WCAG contrast for every pair the app draws, and the
// rules the design depends on are pinned: every route has exactly one job; the light block exists twice and agrees; team colours
// stay on photos and chips; the purple accent is gone; the five tabs carry an icon and a word.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { JOBS, JOBS_ORDER, ROUTE_NAMES, jobOfRoute, type Job } from "./jobs";
import type { Route } from "./router";

const dir = __dirname;
const css = readFileSync(join(dir, "colour.css"), "utf8");

/** The declarations of the first block whose selector line is `selector`, as name -> value. */
function block(source: string, selector: string, from = 0): Record<string, string> {
  const at = source.indexOf(selector + " {", from);
  if (at < 0) throw new Error(`no block ${selector}`);
  const end = source.indexOf("}", at);
  const body = source.slice(source.indexOf("{", at) + 1, end);
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1] ?? ""] = (m[2] ?? "").trim();
  return out;
}
const dark = block(css, ":root");
const light = block(css, ':root[data-theme="light"]');
const mediaStart = css.indexOf("@media (prefers-color-scheme: light)");
const lightMedia = block(css, ':root:not([data-theme="dark"])', mediaStart);

function lin(c: number): number {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}
function lum(hex: string): number {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => lin(parseInt(h.slice(i, i + 2), 16)));
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

const JOB_KEYS = ["tonight", "teams", "props", "slip", "explore"] as const;
const THEMES: [string, Record<string, string>][] = [
  ["dark", dark],
  ["light", light],
];
const GROUNDS = ["bg", "surface", "surface-2"] as const;

describe("colour tokens", () => {
  it("every token is a six-digit hex in both themes", () => {
    for (const [name, t] of THEMES) {
      for (const k of ["bg", "head", "surface", "surface-2", "surface-3", "ink", "muted", "line", "up", "down", ...JOB_KEYS.flatMap((j) => [`${j}-ink`, `${j}-fill`, `${j}-on`])]) {
        expect(t[k], `${name} --${k}`).toMatch(/^#[0-9a-f]{6}$/);
      }
    }
  });

  it("the dark values are the approved ones", () => {
    expect([dark["teams-ink"], dark["props-ink"], dark["slip-ink"], dark["explore-ink"]]).toEqual(["#4cc2ff", "#ffb224", "#ff6fd0", "#9d7bff"]);
  });

  it("the light ground is not pure white", () => {
    expect(light["bg"]).not.toBe("#ffffff");
    expect(light["surface"]).not.toBe("#ffffff");
  });

  for (const [theme, t] of THEMES) {
    describe(`${theme}: text on the ground is at least 4.5:1`, () => {
      const pairs: [string, string][] = [];
      for (const g of GROUNDS) {
        for (const fg of ["ink", "muted", "up", "down", ...JOB_KEYS.map((j) => `${j}-ink`)]) pairs.push([fg, g]);
      }
      for (const [fg, bg] of pairs) {
        it(`--${fg} on --${bg}`, () => {
          expect(contrast(t[fg] ?? "", t[bg] ?? "")).toBeGreaterThanOrEqual(4.5);
        });
      }
    });
    describe(`${theme}: text on a fill is at least 4.5:1`, () => {
      for (const j of JOB_KEYS) {
        it(`--${j}-on on --${j}-fill`, () => {
          expect(contrast(t[`${j}-on`] ?? "", t[`${j}-fill`] ?? "")).toBeGreaterThanOrEqual(4.5);
        });
      }
    });
    describe(`${theme}: rules and bars are at least 3:1 against the ground`, () => {
      for (const j of JOB_KEYS) {
        for (const g of GROUNDS) {
          it(`--${j}-fill on --${g}`, () => {
            expect(contrast(t[`${j}-fill`] ?? "", t[g] ?? "")).toBeGreaterThanOrEqual(3);
          });
        }
      }
    });
  }

  it("the light block for Auto (media query) equals the one for an explicit Light", () => {
    expect(lightMedia).toEqual(light);
  });

  it("the four job hues are four different colours in each theme", () => {
    for (const [, t] of THEMES) expect(new Set(["teams", "props", "slip", "explore"].map((j) => t[`${j}-ink`])).size).toBe(4);
  });

  it("colour.css holds hex colours only in its token blocks and the legend's team swatch", () => {
    const outside = css.replace(/:root \{[\s\S]*?\n\}/g, "").replace(/@media \(prefers-color-scheme: light\) \{[\s\S]*?\n {2}\}\n\}/, "").replace(/:root\[data-theme="light"\] \{[\s\S]*?\n\}/, "");
    const hexes = [...outside.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toLowerCase()).filter((h) => !["#1d42a6", "#c8102e"].includes(h));
    expect(hexes).toEqual([]);
  });
});

describe("jobs", () => {
  it("has five jobs, each with a word, a title, a sentence, an icon and a screen", () => {
    expect(JOBS_ORDER).toEqual(["tonight", "teams", "props", "slip", "explore"]);
    for (const j of JOBS_ORDER) {
      const d = JOBS[j];
      expect(d.tab.length).toBeGreaterThan(2);
      expect(d.title.length).toBeGreaterThan(2);
      expect(d.sentence.length).toBeGreaterThan(10);
      expect(d.icon).toMatch(/^job/);
      expect(d.href).toMatch(/^#\//);
    }
  });

  const sample = (name: Route["name"]): Route => {
    switch (name) {
      case "source":
        return { name, gameId: "g", playerId: null, stat: null };
      case "leg":
        return { name, gameId: "g", playerId: 1, stat: "pts", side: "over" };
      case "props":
        return { name, date: null, game: null };
      case "hedge":
        return { name, game: null, player: null, stat: null };
      case "record":
        return { name, tab: "replay", date: null };
      case "live":
        return { name, id: null, date: null };
      case "game":
        return { name, id: "g" };
      case "terms":
        return { name, term: null, q: null };
      case "help":
        return { name, code: null, q: null };
      case "notfound":
        return { name, hash: "#x" };
      case "bet":
        return { name: "bet", path: [] } as unknown as Route;
      case "settings":
      case "picks":
      case "signals":
      case "history":
      case "alerts":
      case "admin":
      case "scorecard":
      case "tests":
        return { name };
      default:
        return { name, date: null };
    }
  };

  it("maps every route to exactly one of the five jobs", () => {
    expect(ROUTE_NAMES.length).toBeGreaterThanOrEqual(24);
    for (const n of ROUTE_NAMES) expect(JOBS_ORDER, n).toContain(jobOfRoute(sample(n)));
  });

  it("puts live tracking in TEAMS and the player card scope in PROPS; no job of its own", () => {
    expect(jobOfRoute(sample("live"))).toBe("teams");
    expect(jobOfRoute(sample("game"))).toBe("teams");
    expect(jobOfRoute(sample("leg"))).toBe("props");
    expect(jobOfRoute(sample("parlay"))).toBe("slip");
    expect(jobOfRoute(sample("combo"))).toBe("explore");
    expect(jobOfRoute(sample("today"))).toBe("tonight");
    expect(JOBS_ORDER).not.toContain("live" as Job);
    const live = readFileSync(join(dir, "pages", "Live.tsx"), "utf8") + readFileSync(join(dir, "LiveParts.tsx"), "utf8");
    expect((live.match(/data-job="props"/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });
});

describe("the rules in the source", () => {
  const files = (d: string): string[] =>
    readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(d, e.name)) : [join(d, e.name)]));
  const src = files(dir).filter((f) => /\.(tsx?|css)$/.test(f) && !f.endsWith(".test.ts") && !f.includes("golden"));

  it("team colours are used only by the player photo and the team chip", () => {
    const users = src.filter((f) => f.endsWith(".tsx") && /teamColor\(/.test(readFileSync(f, "utf8"))).map((f) => f.replace(dir + "/", ""));
    expect(users.sort()).toEqual(["Avatar.tsx", "components.tsx"]);
    const comps = readFileSync(join(dir, "components.tsx"), "utf8");
    expect(comps.match(/teamColor\(/g)?.length).toBe(1);
  });

  it("no hard-coded purple remains outside the token files (the old accent is the job colour now)", () => {
    for (const f of src.filter((x) => x.endsWith(".css") && !x.endsWith("colour.css"))) {
      const text = readFileSync(f, "utf8");
      for (const m of text.matchAll(/#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?\b/g)) {
        const h = m[1] ?? "";
        const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        if (max - min < 0.18 * max) continue;
        const hue = max === r ? ((g - b) / (max - min) + 6) % 6 : max === g ? (b - r) / (max - min) + 2 : (r - g) / (max - min) + 4;
        expect(hue >= 4.1 && hue <= 5.2, `${f.replace(dir + "/", "")} ${m[0]}`).toBe(false);
      }
    }
  });

  it("no hard-coded green or red hex outside the token file", () => {
    for (const f of src.filter((x) => x.endsWith(".css") && !x.endsWith("colour.css") && !x.endsWith("theme.css"))) {
      const text = readFileSync(f, "utf8");
      for (const m of text.matchAll(/#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?\b/g)) {
        const h = m[1] ?? "";
        const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        if (max - min < 0.25 * max) continue;
        const hue = max === r ? ((g - b) / (max - min) + 6) % 6 : max === g ? (b - r) / (max - min) + 2 : (r - g) / (max - min) + 4;
        expect(hue < 0.3 || hue > 5.6 || (hue > 1.3 && hue < 3), `${f.replace(dir + "/", "")} ${m[0]}`).toBe(false);
      }
    }
  });

  it("the five tabs each carry an icon and a word, and keep a bar of their colour when inactive", () => {
    const shell = readFileSync(join(dir, "Shell.tsx"), "utf8");
    expect(shell).toMatch(/<Icon name=\{JOBS\[j\]\.icon\} \/>[\s\S]*jt-word/);
    expect(shell).toContain('class="nav-bottom jobtabs"');
    expect(css).toMatch(/\.jobtabs \.jt \{[^}]*border-top: 3px solid var\(--to-ink\)/);
    expect(css).toMatch(/\.jobtabs \.jt\[aria-current="page"\] \{[^}]*background: var\(--to-fill\)/);
  });

  it("a door is drawn in the destination's colour and writes the destination's name", () => {
    const doors = readFileSync(join(dir, "Doors.tsx"), "utf8");
    expect(doors).toContain("data-to={props.to}");
    expect(doors).toContain("JOBS[props.to].tab");
    expect(css).toMatch(/\.door \{[^}]*border: 1px solid var\(--to-ink\)/);
  });

  it("touch targets: doors, tabs and the home doors are at least 44px tall", () => {
    expect(css).toMatch(/\.door \{[^}]*min-height: 44px/);
    expect(css).toMatch(/\.jobtabs \.jt \{[^}]*min-height: 56px/);
    expect(css).toMatch(/\.home-door \{[^}]*min-height: 96px/);
  });

  it("the theme preference is stored as ph.theme, read before first paint, with Auto, Light and Dark in the menu", () => {
    const init = readFileSync(join(dir, "..", "public", "theme-init.js"), "utf8");
    expect(init).toContain('"ph.theme"');
    expect(init).toContain("dataset.theme");
    expect(init).toContain("theme-color");
    const comps = readFileSync(join(dir, "components.tsx"), "utf8");
    expect(comps).toMatch(/\["system", "Auto"\]/);
    expect(readFileSync(join(dir, "Shell.tsx"), "utf8")).toContain("<ThemePicker />");
  });

  it("home asks the question, shows four doors and a three-line legend", () => {
    const home = readFileSync(join(dir, "Home.tsx"), "utf8");
    expect(home).toContain("What are you here for?");
    expect((home.match(/<BigDoor /g) ?? []).length).toBe(4);
    expect((home.match(/<p>/g) ?? []).length).toBe(3);
  });
});
