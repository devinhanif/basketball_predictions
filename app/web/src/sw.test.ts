import { readFileSync } from "node:fs";
import vm from "node:vm";
import { describe, expect, it } from "vitest";

// Runs public/sw.js in a sandbox with a fake Cache API and drives its fetch handler.
type Handler = (e: { request: Request; respondWith: (p: Promise<Response>) => void }) => void;

function boot(network: () => Promise<Response>) {
  const store = new Map<string, Response>();
  const cache = {
    put: (r: Request, res: Response) => {
      store.set(r.url, res);
      return Promise.resolve();
    },
    match: (r: Request) => Promise.resolve(store.get(r.url)?.clone()),
  };
  let handler: Handler | null = null;
  const sandbox = {
    self: { location: { origin: "http://x.test" }, addEventListener: (t: string, h: Handler) => { if (t === "fetch") handler = h; } },
    caches: { open: () => Promise.resolve(cache), keys: () => Promise.resolve([]) },
    fetch: network,
    Response,
    Headers,
    URL,
  };
  vm.runInNewContext(readFileSync(new URL("../public/sw.js", import.meta.url), "utf8"), sandbox);
  return (path: string): Promise<Response> => {
    const request = new Request(`http://x.test${path}`);
    const box: { out?: Promise<Response> } = {};
    handler?.({ request, respondWith: (p) => { box.out = p; } });
    return box.out ?? Promise.reject(new Error("not handled"));
  };
}

describe("F15: the saved copy is used on a 5xx, not only when the network throws", () => {
  it("serves the last good copy, marked, when the origin answers 502 or 503", async () => {
    for (const status of [502, 503]) {
      let mode: "ok" | "bad" = "ok";
      const get = boot(() => Promise.resolve(mode === "ok" ? new Response('{"a":1}', { status: 200 }) : new Response('{"detail":"down"}', { status })));
      expect((await get("/api/slates")).headers.get("X-From-Cache")).toBeNull();
      mode = "bad";
      const res = await get("/api/slates");
      expect(res.status).toBe(200);
      expect(res.headers.get("X-From-Cache")).toBe("1");
      expect(await res.json()).toEqual({ a: 1 });
    }
  });
  it("with no saved copy the real 503 goes through, reason intact", async () => {
    const get = boot(() => Promise.resolve(new Response('{"detail":"the data export was refused: x"}', { status: 503 })));
    const res = await get("/api/slates");
    expect(res.status).toBe(503);
    expect(res.headers.get("X-From-Cache")).toBeNull();
    expect(await res.json()).toEqual({ detail: "the data export was refused: x" });
  });
  it("a 404 is never replaced by a saved copy", async () => {
    let status = 200;
    const get = boot(() => Promise.resolve(new Response("{}", { status })));
    await get("/api/games/1");
    status = 404;
    expect((await get("/api/games/1")).status).toBe(404);
  });
  it("still falls back when the network throws", async () => {
    let down = false;
    const get = boot(() => (down ? Promise.reject(new Error("offline")) : Promise.resolve(new Response("{}", { status: 200 }))));
    await get("/api/meta");
    down = true;
    expect((await get("/api/meta")).headers.get("X-From-Cache")).toBe("1");
  });
});
