import { describe, expect, it } from "vitest";
import { errorCopy, httpErrorMessage } from "./errors";

describe("F14: error copy", () => {
  it("never shows developer instructions to a visitor", () => {
    const c = errorCopy("500 Internal Server Error for /api/slates", false);
    expect(c.devHint).toBe(false);
    expect(c.title).toBe("We can't reach our data right now");
    expect(JSON.stringify(c)).not.toMatch(/uvicorn|app\/api|start it/);
  });
  it("shows the dev hint only in a dev build", () => {
    expect(errorCopy("Failed to fetch", true).devHint).toBe(true);
  });
  it("a refused export says so and keeps the reason", () => {
    const m = httpErrorMessage(503, "Service Unavailable", "/api/slates", "the data export was refused: manifest hash mismatch");
    const c = errorCopy(m, false);
    expect(c.title).toBe("Today's data failed its checks");
    expect(c.reason).toBe("the data export was refused: manifest hash mismatch");
    expect(c.devHint).toBe(false);
  });
  it("a 503 with no reason is treated as unreachable", () => {
    expect(errorCopy(httpErrorMessage(503, "Service Unavailable", "/api/x", null), false).title).toBe("We can't reach our data right now");
  });
});
