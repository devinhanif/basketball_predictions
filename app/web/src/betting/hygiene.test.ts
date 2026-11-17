// This lane's own guard (another lane runs the app-wide one): no betting file names a credential, an outbound call, or
// an instruction to a provider; and no betting file imports a UI framework. The forbidden words are assembled at run
// time so this file does not contain them itself.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(here).filter((f) => f.endsWith(".ts"));
const w = (...parts: string[]): string => parts.join("");

describe("betting module hygiene", () => {
  it("no file contains a forbidden substring", () => {
    const substrings = [w("Author", "ization"), w("api", "_key"), w("api", "key"), w("sec", "ret"), w("place", "_order"), w("exe", "cute"), w("fet", "ch("), w("XMLHttp", "Request"), w("Web", "Socket"), w("local", "Storage")];
    for (const f of files) {
      const text = readFileSync(join(here, f), "utf8");
      for (const s of substrings) expect(text.toLowerCase().includes(s.toLowerCase()), `${f} contains ${s}`).toBe(false);
    }
  });

  it("no file contains the bare words for credentials or provider instructions", () => {
    const words = [w("to", "ken"), w("or", "der")];
    for (const f of files) {
      const text = readFileSync(join(here, f), "utf8");
      for (const word of words) expect(new RegExp(`\\b${word}s?\\b`, "i").test(text), `${f} contains ${word}`).toBe(false);
    }
  });

  it("no pure-logic file imports preact, react or any app module beyond the pure types and time helpers", () => {
    // index.ts and seam.ts are the registration seam (routes, nav, slots): they are the two files that may touch the UI.
    const seam = new Set(["index.ts", "seam.ts"]);
    for (const f of files.filter((x) => !seam.has(x))) {
      const imports = [...readFileSync(join(here, f), "utf8").matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1] ?? "");
      for (const i of imports) {
        expect(/^(preact|react)/.test(i), `${f} imports ${i}`).toBe(false);
        if (i.startsWith("..")) expect(["../types", "../time"], `${f} imports ${i}`).toContain(i);
      }
    }
  });
});
