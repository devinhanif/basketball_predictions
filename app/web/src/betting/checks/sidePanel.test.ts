// The betting module's place in the shell with the switch off and on: with it off the shell's betting slot renders nothing
// and nothing fills the shared side panel. (A test beside the module that may import app code; the module's own pure-logic
// files are guarded by ../hygiene.test.ts.)
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BETTING } from "../../features";
import { BetShellPanel } from "../../BetSlots";
import { getSidePanel } from "../../ui/sidePanelStore";

const SRC = fileURLToPath(new URL("../..", import.meta.url));

describe(`the side-panel swap with the switch ${BETTING ? "ON" : "OFF"}`, () => {
  it("the shell's betting slot renders nothing with the switch off, and the shared side panel starts empty", () => {
    if (!BETTING) expect(BetShellPanel({ onBetRoute: false })).toBeNull();
    expect(getSidePanel()).toBeNull();
  });
  it("only the betting module fills the shared side panel (so an OFF build, which drops the module, never does)", () => {
    const src = SRC;
    const walk = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
    const callers = walk(src)
      .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.ts$/.test(f) && readFileSync(f, "utf8").includes("setSidePanel("))
      .map((f) => relative(src, f))
      .filter((f) => !f.startsWith("ui/"));
    expect(callers).toEqual(["betting/Slip.tsx"]);
  });
});
