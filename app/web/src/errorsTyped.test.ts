// The typed failure: both body shapes (the new contract and the old {detail}), statuses, network, and the registry.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { allCodes, codeCopy, knownCode } from "./errorCodes";
import { ApiError, codeForStatus, failureFromAnswer, failureFromMessage, failureFromNetwork, failureMessage, failureOf, reasonOf } from "./errors";

describe("failureFromAnswer", () => {
  it("reads the new contract: code, text, detail, field, request id", () => {
    const f = failureFromAnswer(503, { ok: false, code: "export_refused", text: "Today's data failed its checks", field: null, request_id: "abc123", detail: "manifest hash mismatch" }, "/api/slates");
    expect(f).toMatchObject({ ok: false, status: 503, code: "export_refused", text: "Today's data failed its checks", detail: "manifest hash mismatch", requestId: "abc123" });
    expect(reasonOf(f)).toBe("manifest hash mismatch");
  });
  it("falls back to the X-Request-Id header when the body has no request id", () => {
    expect(failureFromAnswer(404, { ok: false, code: "unknown_game" }, "/api/games/x", "hdr-1").requestId).toBe("hdr-1");
  });
  it("maps the old {detail} body by status and path", () => {
    expect(failureFromAnswer(404, { detail: "no such game" }, "/api/games/0022400252").code).toBe("unknown_game");
    expect(failureFromAnswer(404, { detail: "x" }, "/api/slates/2099-01-01").code).toBe("unknown_slate");
    expect(failureFromAnswer(404, { detail: "x" }, "/api/breakdown/g/1/pts?side=over").code).toBe("no_forecast");
    expect(failureFromAnswer(404, { detail: "x" }, "/api/live/zzz/stream").code).toBe("no_live_capture");
    expect(failureFromAnswer(404, null, "/api/whatever").code).toBe("not_found");
    expect(failureFromAnswer(422, { detail: [{ loc: ["x"] }] }, "/api/x").code).toBe("bad_request");
    expect(failureFromAnswer(500, "oops", "/api/x").code).toBe("internal_error");
    expect(failureFromAnswer(503, { detail: "refused: stale manifest" }, "/api/meta")).toMatchObject({ code: "export_refused", detail: "refused: stale manifest" });
  });
  it("never shows a 404's detail as a reason (only refusals carry one)", () => {
    expect(reasonOf(failureFromAnswer(404, { detail: "no such game" }, "/api/games/x"))).toBeNull();
  });
  it("tolerates any body: arrays, strings, numbers, null", () => {
    for (const b of [null, undefined, "x", 1, [], [1], { code: 5 }, { detail: 7 }]) {
      expect(() => failureFromAnswer(503, b, "/api/x")).not.toThrow();
      expect(failureFromAnswer(503, b, "/api/x").code).toBe("export_refused");
    }
  });
});

describe("network and thrown errors", () => {
  it("no answer is network_unreachable online, offline when the browser says so", () => {
    expect(failureFromNetwork("/api/x", true).code).toBe("network_unreachable");
    expect(failureFromNetwork("/api/x", false).code).toBe("offline");
    expect(codeForStatus(null, "/api/x", false)).toBe("offline");
  });
  it("an ApiError carries its failure; anything else becomes unexpected_error or is read back from its message", () => {
    const f = failureFromAnswer(503, { detail: "why" }, "/api/x");
    expect(failureOf(new ApiError(f))).toBe(f);
    expect(failureOf(new TypeError("boom")).code).toBe("unexpected_error");
    expect(failureOf(new Error("404 Not Found for /api/games/x")).code).toBe("unknown_game");
  });
  it("the message round-trips through the old one-line form", () => {
    const f = failureFromAnswer(503, { detail: "a :: b" }, "/api/slates");
    const back = failureFromMessage(failureMessage(f));
    expect(back.status).toBe(503);
    expect(back.code).toBe("export_refused");
    expect(back.detail).toBe("a :: b");
  });
});

describe("the registry", () => {
  it("every code the web app infers or raises is in app/error_codes.json", () => {
    const inferred = [
      "unknown_game", "unknown_slate", "no_forecast", "game_not_played", "no_live_capture", "no_desk", "no_top_parlays", "not_found",
      "bad_request", "method_not_allowed", "export_refused", "internal_error", "unexpected_error", "network_unreachable", "offline",
      "stale_data", "live_feed_stopped", "bad_route",
    ];
    for (const c of inferred) expect(knownCode(c), c).toBe(true);
  });
  it("each entry has title, what, why and todo, and no address or markup", () => {
    for (const c of allCodes()) {
      const copy = codeCopy(c);
      for (const k of ["title", "what", "why", "todo"] as const) {
        expect(copy[k].length, `${c}.${k}`).toBeGreaterThan(3);
        expect(copy[k], `${c}.${k}`).not.toMatch(/https?:|<[a-z]/i);
      }
    }
  });
  it("an unknown code gets the generic copy rather than nothing", () => {
    expect(codeCopy("no_such_code").title).toBe(codeCopy("unexpected_error").title);
  });
  it("the bundled file is the shared one (relative import, so the artifact carries it)", () => {
    const text = readFileSync(join(__dirname, "errorCodes.ts"), "utf8");
    expect(text).toContain('from "../../error_codes.json"');
  });
});
