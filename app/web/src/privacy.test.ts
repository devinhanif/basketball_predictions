// Rule: desk money (balance, P&L) renders only in a private session. The API strips it from the shared cut; the screens
// check privacy too, so a file that slipped through is still not shown.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const files = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(dir, e.name)) : /\.tsx$/.test(e.name) ? [join(dir, e.name)] : []));

describe("desk money is guarded by privacy on screen", () => {
  it("every line that renders desk.balance or a day's pnl also checks privacy === private", () => {
    let seen = 0;
    for (const f of files(__dirname)) {
      const lines = readFileSync(f, "utf8").split("\n");
      lines.forEach((l, i) => {
        if (/desk\.balance\s*\?|day\.pnl\s*!==/.test(l)) {
          seen += 1;
          expect(l, `${f}:${(i + 1).toString()}`).toMatch(/privacy === "private"/);
        }
      });
    }
    expect(seen).toBeGreaterThanOrEqual(2);
  });
});
