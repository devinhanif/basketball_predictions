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
  const db = {
    collection(path) {
      if (path !== "picks") throw new Error("unexpected collection " + path);
      return {
        onSnapshot(next) { listeners.add(next); setTimeout(() => next(snap()), 150); return () => listeners.delete(next); },
        doc(id) { return { set(data) { window.__writes.push({ id, n: data.picks.length }); docs[id] = JSON.parse(JSON.stringify(data)); setTimeout(emit, 50); return Promise.resolve(); } }; },
      };
    },
  };
  const user = {
    id: () => Promise.resolve("u_me"),
    profiles: (ids) => Promise.resolve(Object.fromEntries(ids.map((i) => [i, { id: i, name: i === "u_sam" ? "Sam" : "" }]))),
  };
  window.claude = { use: (name) => Promise.resolve(name === "db" ? db : name === "user" ? user : null) };
})();
