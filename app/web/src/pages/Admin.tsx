import { useMemo, useState } from "preact/hooks";
import { api } from "../api";
import { Avatar } from "../Avatar";
import { deskOf, useDesk } from "../deskStore";
import { useIdentity } from "../identity";
import { clearViewer } from "../adminActions";
import { getPlatform, usePlatform } from "../platform";
import { removePhoto, savePhoto, usePhotos } from "../photos";
import { useIsAdmin } from "../shellData";
import { useShared, useLedger } from "../SharedUI";
import { stamp } from "../format";
import { IS_ARTIFACT } from "../env";
import { useLoad } from "../useLoad";
import { useWatch } from "../watch";
import { pushToast } from "../toast";
import { lineText } from "../format";

type Tab = "photos" | "activity" | "people" | "data";
const TABS: { v: Tab; l: string }[] = [
  { v: "photos", l: "Player photos" },
  { v: "activity", l: "Activity log" },
  { v: "people", l: "People" },
  { v: "data", l: "Data tools" },
];

interface Known {
  id: number;
  name: string;
}

function usePlayers(): Known[] {
  const desk = deskOf(useDesk());
  const shared = useShared();
  const mine = useLedger();
  return useMemo(() => {
    const m = new Map<number, string>();
    for (const p of desk?.plays ?? []) if (p.player_id !== null && p.player_name) m.set(p.player_id, p.player_name);
    for (const p of [...mine, ...Object.values(shared.others).flat()]) {
      const pid = Number(p.prop_id.split(":")[1]);
      if (Number.isInteger(pid)) m.set(pid, p.player);
    }
    return [...m.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [desk, shared.others, mine]);
}

function Photos() {
  const players = usePlayers();
  const photos = usePhotos();
  const platform = usePlatform();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const canUpload = IS_ARTIFACT ? platform?.assets != null : true;
  const view = players.filter((p) => p.name.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 40);
  const act = (id: number, f: () => Promise<{ ok: boolean; reason?: string }>): void => {
    setBusy(id);
    void f().then((r) => {
      setBusy(null);
      if (!r.ok) pushToast("Photo", r.reason ?? "That did not work.");
    });
  };
  return (
    <section class="panel" aria-labelledby="ph-h">
      <h2 id="ph-h">Player photos</h2>
      <p class="small muted">
        Photos exist only where you upload one; nothing ships with the app, and every player falls back to initials. Use images you have the
        right to show.
        {IS_ARTIFACT ? " Uploads go to this page's storage and are visible to everyone who opens it." : " Photos stay on this device."}
      </p>
      {!canUpload ? <p class="note-box small">Uploading is not available in this view: the platform grants it to the page's editors only.</p> : null}
      <label class="field" for="ph-q">
        <span class="label">Find a player</span>
        <input id="ph-q" type="search" value={q} onInput={(e) => { setQ(e.currentTarget.value); }} placeholder="Name" />
      </label>
      {players.length === 0 ? <p class="muted">No players are known yet: they appear once the desk or someone's picks name them.</p> : null}
      <ul class="photo-list">
        {view.map((p) => (
          <li key={p.id}>
            <Avatar name={p.name} playerId={p.id} size="md" />
            <span class="photo-name">
              <strong>{p.name}</strong>
              <span class="small muted num">player id {p.id}</span>
            </span>
            {canUpload ? (
              <label class="btn file-btn">
                {photos[p.id.toString()] ? "Replace" : "Upload"}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  class="sr"
                  disabled={busy === p.id}
                  onChange={(e) => {
                    const f = e.currentTarget.files?.[0];
                    if (f) act(p.id, () => savePhoto(p.id, f));
                    e.currentTarget.value = "";
                  }}
                />
              </label>
            ) : null}
            {canUpload && photos[p.id.toString()] ? (
              <button type="button" class="btn" disabled={busy === p.id} onClick={() => { act(p.id, () => removePhoto(p.id)); }}>
                Remove
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function useNames(): Record<string, string> {
  const shared = useShared();
  const watch = useWatch();
  return { ...watch.names, ...shared.names };
}

interface Act {
  who: string;
  at: string;
  text: string;
}

function Activity() {
  const shared = useShared();
  const watch = useWatch();
  const mine = useLedger();
  const id = useIdentity();
  const names = useNames();
  const rows = useMemo<Act[]>(() => {
    const out: Act[] = [];
    for (const p of mine) if (p.side !== "skip") out.push({ who: id.id ?? "me", at: p.made_at, text: `picked ${p.side} ${lineText(p.line)} on ${p.player}` });
    for (const [uid, ps] of Object.entries(shared.others)) for (const p of ps) out.push({ who: uid, at: p.made_at, text: `picked ${p.side} ${lineText(p.line)} on ${p.player}` });
    for (const i of watch.mine) out.push({ who: id.id ?? "me", at: i.at, text: `is watching ${i.label}` });
    for (const [uid, is] of Object.entries(watch.others)) for (const i of is) out.push({ who: uid, at: i.at, text: `is watching ${i.label}` });
    return out.sort((a, b) => b.at.localeCompare(a.at)).slice(0, 60);
  }, [mine, shared.others, watch, id.id]);
  const nameOf = (who: string): string => (who === (id.id ?? "me") ? "You" : names[who] || "Someone");
  return (
    <section class="panel" aria-labelledby="act-h">
      <h2 id="act-h">Activity log</h2>
      <p class="small muted">Shared picks and watchlist activity: who and when. Names are resolved when shown and never stored.</p>
      {rows.length === 0 ? <p class="muted">No activity yet.</p> : null}
      <ul class="act-list">
        {rows.map((r, i) => (
          <li key={`${r.who}-${r.at}-${i.toString()}`}>
            <span class="num small muted">{stamp(r.at)}</span>
            <span>
              <strong>{nameOf(r.who)}</strong> {r.text}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function People() {
  const shared = useShared();
  const watch = useWatch();
  const id = useIdentity();
  const names = useNames();
  const [confirm, setConfirm] = useState<string | null>(null);
  const ids = [...new Set([...Object.keys(shared.others), ...Object.keys(watch.others)])];
  const clear = (uid: string): void => {
    void getPlatform().then(async (p) => {
      try {
        if (!(await clearViewer(p, uid))) return;
        pushToast("People", "Their shared picks and watchlist were cleared.");
      } catch {
        pushToast("People", "Could not clear them (owner only).");
      }
      setConfirm(null);
    });
  };
  return (
    <section class="panel" aria-labelledby="ppl-h">
      <h2 id="ppl-h">People</h2>
      <p class="small muted">Everyone who has picks or a watchlist in this page. The platform tells us who they are; we keep only their ids.</p>
      <ul class="people">
        <li>
          <Avatar name={id.name || "You"} src={id.avatarUrl} size="sm" />
          <span>
            <strong>{id.name || "You"}</strong> <span class="chip">you</span>
          </span>
        </li>
        {ids.map((uid) => (
          <li key={uid}>
            <Avatar name={names[uid] || "Someone"} size="sm" />
            <span>
              <strong>{names[uid] || "Someone"}</strong>
              <span class="small muted num">
                {" "}
                {(shared.others[uid] ?? []).length} picks · {(watch.others[uid] ?? []).length} watching
              </span>
            </span>
            {id.isOwner && IS_ARTIFACT ? (
              confirm === uid ? (
                <span class="row">
                  <button type="button" class="btn primary" onClick={() => { clear(uid); }}>
                    Yes, clear their picks
                  </button>
                  <button type="button" class="btn" onClick={() => { setConfirm(null); }}>
                    Keep them
                  </button>
                </span>
              ) : (
                <button type="button" class="btn" onClick={() => { setConfirm(uid); }}>
                  Clear picks
                </button>
              )
            ) : null}
          </li>
        ))}
      </ul>
      {ids.length === 0 ? <p class="muted">Nobody else has picks or a watchlist yet.</p> : null}
      {id.isOwner && IS_ARTIFACT ? (
        <p class="small muted">Clearing removes someone's shared picks and watchlist now. Their own device keeps its copy and publishes it again the next time they pick, so this is a tidy-up, not a block.</p>
      ) : null}
    </section>
  );
}

function DataTools() {
  const [meta] = useLoad(() => api.meta(), []);
  const desk = deskOf(useDesk());
  return (
    <section class="panel" aria-labelledby="dt-h">
      <h2 id="dt-h">Data tools</h2>
      {meta.status === "ready" ? (
        <dl class="kv-list">
          <dt>Forecast data as of</dt>
          <dd class="num">{stamp(meta.data.data_as_of)}</dd>
          <dt>Data version</dt>
          <dd class="num">{meta.data.data_version}</dd>
          <dt>Nights held</dt>
          <dd class="num">{meta.data.slates.join(", ")}</dd>
          <dt>Source</dt>
          <dd>{meta.data.mode === "export" ? "The nightly export (read-only)" : "Replay fixture"}</dd>
        </dl>
      ) : null}
      {desk ? (
        <>
          <h3>Desk export</h3>
          <dl class="kv-list">
            <dt>Cut</dt>
            <dd>{desk.privacy}{desk.fixture ? " (synthetic fixture)" : ""}</dd>
            <dt>Built</dt>
            <dd class="num">{stamp(desk.built_at)}</dd>
            <dt>Data as of</dt>
            <dd class="num">{stamp(desk.data_as_of)}</dd>
            <dt>Rows</dt>
            <dd class="num">
              {desk.plays.length} plays, {desk.observations.length} observations, {desk.alerts.length} alerts, {desk.halftime.length} halftime rows
            </dd>
            {Object.entries(desk.sources).map(([k, v]) => (
              <>
                <dt key={`k-${k}`}>Source: {k}</dt>
                <dd key={`v-${k}`} class="small">{v}</dd>
              </>
            ))}
          </dl>
        </>
      ) : (
        <p class="muted">No desk export is loaded.</p>
      )}
    </section>
  );
}

export function Admin() {
  const isAdmin = useIsAdmin();
  const id = useIdentity();
  const [tab, setTab] = useState<Tab>("photos");
  if (id.loading) return <p class="muted">Checking who you are...</p>;
  if (!isAdmin) {
    return (
      <div class="stack">
        <header class="page-head">
          <h1>Admin studio</h1>
        </header>
        <section class="panel empty">
          <h2>These are the owner's tools</h2>
          <p class="muted">
            {IS_ARTIFACT
              ? "The platform decides who can edit this page; there is no password to enter here."
              : "Photos, the activity log and data tools are for whoever runs this page, so visitors do not see them. Everything else in the app is open to you."}
          </p>
          <a class="btn" href="#/">
            Back to Discover
          </a>
        </section>
      </div>
    );
  }
  return (
    <div class="stack">
      <header class="page-head">
        <div class="kicker">Admin studio</div>
        <h1 class="answer">Photos, activity, people and data tools</h1>
        <p class="muted answer-src">Visible to you because the platform says you can edit this page. No password is involved.</p>
        <nav class="tabs" aria-label="Admin sections">
          {TABS.map((t) => (
            <button key={t.v} type="button" class="tab" aria-current={tab === t.v ? "true" : undefined} onClick={() => { setTab(t.v); }}>
              {t.l}
            </button>
          ))}
        </nav>
      </header>
      {tab === "photos" ? <Photos /> : null}
      {tab === "activity" ? <Activity /> : null}
      {tab === "people" ? <People /> : null}
      {tab === "data" ? <DataTools /> : null}
    </div>
  );
}
