// Screenshot harness only (never shipped): a stand-in for the platform's window.claude with an in-memory db holding one
// friend's picks, so the shared-picks screens can be seen and the write path exercised. The friend is "Sam".
(function () {
  const sam = __SAM__;
  const docs = { u_sam: sam };
  const listeners = new Set();
  const snap = () => ({
    docs: Object.entries(docs).map(([id, d]) => ({ id, exists: true, data: () => d })),
    metadata: { fromCache: false, hasPendingWrites: false },
  });
  const emit = () => { for (const l of listeners) l(snap()); };
  window.__writes = [];
  const watchSam = { v: 1, items: [{ key: "fx", label: "Sample watch", sub: "PHI @ CHI", at: "2026-11-08T18:00:00Z" }] };
  const store = { picks: docs, watch: { u_sam: watchSam }, photos: {} };
  const colls = {};
  const coll = (path) => {
    if (colls[path]) return colls[path];
    const ls = new Set();
    const rows = () => (store[path] = store[path] || {});
    const snap = () => ({ docs: Object.entries(rows()).map(([id, d]) => ({ id, exists: true, data: () => d })), metadata: { fromCache: false, hasPendingWrites: false } });
    const emit = () => { for (const l of ls) l(snap()); };
    colls[path] = {
      onSnapshot(next) { ls.add(next); setTimeout(() => next(snap()), 150); return () => ls.delete(next); },
      doc(id) { return { set(data) { window.__writes.push({ path, id }); rows()[id] = JSON.parse(JSON.stringify(data)); setTimeout(emit, 50); return Promise.resolve(); }, delete() { delete rows()[id]; setTimeout(emit, 50); return Promise.resolve(); } }; },
    };
    return colls[path];
  };
  const docs2 = {};
  const db = {
    collection: coll,
    doc(path) {
      return {
        get: () => Promise.resolve({ exists: path in docs2, data: () => docs2[path] }),
        set(d) { window.__writes.push({ path }); docs2[path] = JSON.parse(JSON.stringify(d)); return Promise.resolve(); },
        delete() { delete docs2[path]; return Promise.resolve(); },
        onSnapshot(next) { setTimeout(() => next({ exists: path in docs2, data: () => docs2[path], metadata: { fromCache: false } }), 100); return () => undefined; },
      };
    },
  };
  const user = {
    id: () => Promise.resolve("u_me"),
    me: () => Promise.resolve({ id: "u_me", name: "Devin", avatarUrl: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><rect width='40' height='40' fill='%23665'/></svg>", isOwner: true, canEdit: true }),
    isOwner: () => Promise.resolve(true),
    canEdit: () => Promise.resolve(true),
    profiles: (ids) => Promise.resolve(Object.fromEntries(ids.map((i) => [i, { id: i, name: i === "u_sam" ? "Sam" : "", avatarUrl: "" }]))),
  };
  const assets = { upload: () => Promise.resolve({ id: "a".repeat(32), url: "/_blob/" + "a".repeat(32) }), delete: () => Promise.resolve({ deleted: true }) };
  const downloads = { save: (r) => { window.__saved = r; return Promise.resolve({ status: "saved" }); } };
  window.claude = { use: (name) => Promise.resolve({ db, user, assets, downloads }[name] || null) };
})();
