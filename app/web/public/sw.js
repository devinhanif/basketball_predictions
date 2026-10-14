/* parlayhitters service worker.
 * - Precaches the app shell listed in /precache.json (written at build time).
 * - Pages: network first, falling back to the cached shell (works offline, installable).
 * - GET /api/*: network first, falling back to the last data fetched; a cached answer is marked with
 *   the header X-From-Cache so the UI can say "saved copy". Nothing else is ever stored.
 * Only same-origin GET requests are handled. No credentials or user data pass through here.
 */
const SHELL = "ph-shell-";
const DATA = "ph-data-v1";
let version = "dev";

async function loadManifest() {
  const res = await fetch("/precache.json", { cache: "no-store" });
  if (!res.ok) throw new Error("no precache manifest");
  return res.json();
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const m = await loadManifest();
      version = m.version;
      const cache = await caches.open(SHELL + version);
      await cache.addAll(m.urls);
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      let current = version;
      try {
        current = (await loadManifest()).version;
      } catch (_e) {
        /* offline at activation: keep whatever shell cache exists */
        const keys = await caches.keys();
        const shells = keys.filter((k) => k.startsWith(SHELL));
        current = shells.length ? shells[shells.length - 1].slice(SHELL.length) : version;
      }
      for (const k of await caches.keys()) {
        if (k.startsWith(SHELL) && k !== SHELL + current) await caches.delete(k);
      }
      await self.clients.claim();
    })(),
  );
});

async function shellCache() {
  const keys = (await caches.keys()).filter((k) => k.startsWith(SHELL));
  return keys.length ? caches.open(keys[keys.length - 1]) : null;
}

async function apiNetworkFirst(request) {
  const cache = await caches.open(DATA);
  try {
    const res = await fetch(request);
    if (res.ok) await cache.put(request, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(request, { ignoreVary: true });
    if (!hit) throw e;
    const headers = new Headers(hit.headers);
    headers.set("X-From-Cache", "1");
    return new Response(await hit.blob(), { status: hit.status, headers });
  }
}

async function pageNetworkFirst(request) {
  try {
    return await fetch(request);
  } catch (e) {
    const shell = await shellCache();
    const hit = shell ? ((await shell.match("/", { ignoreVary: true })) ?? (await shell.match("/index.html", { ignoreVary: true }))) : undefined;
    if (hit) return hit;
    throw e;
  }
}

async function assetCacheFirst(request) {
  const shell = await shellCache();
  const hit = shell ? await shell.match(request, { ignoreVary: true }) : undefined;
  if (hit) return hit;
  return fetch(request);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(apiNetworkFirst(req));
  } else if (req.mode === "navigate") {
    event.respondWith(pageNetworkFirst(req));
  } else {
    event.respondWith(assetCacheFirst(req));
  }
});
