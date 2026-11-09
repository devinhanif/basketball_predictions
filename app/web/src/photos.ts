// Player photos exist only where an editor uploaded one: nothing ships with the app, and every avatar falls back to
// initials on a team colour. Shared page: the upload goes through the platform `assets` capability (editors only) and the
// asset id is stored in db photos/<player id> (everyone reads, only admins write). Regular private build: a small
// downsized image kept on this device. Asset ids from the db are checked before they become an <img> address.
import { useEffect, useState } from "preact/hooks";
import { IS_ARTIFACT } from "./env";
import { getPlatform, type Platform } from "./platform";
import { readKey, writeKey } from "./prefs";

// The contract says only "an opaque id" (32 characters today); accept any URL-safe token of a sane length.
const ASSET_RE = /^[A-Za-z0-9_-]{16,64}$/;
const LOCAL_KEY = "ph.photos.v1";
const MAX_LOCAL = 60;

export function validAssetId(id: unknown): id is string {
  return typeof id === "string" && ASSET_RE.test(id);
}
/** The display address for an asset id (the platform serves "/_blob/<id>"). */
export function assetUrl(id: string): string {
  return `/_blob/${id}`;
}

export type PhotoMap = Record<string, string>;
const listeners = new Set<() => void>();
let map: PhotoMap | null = null;

function loadLocal(): PhotoMap {
  try {
    const raw = readKey(LOCAL_KEY);
    const o: unknown = raw ? JSON.parse(raw) : {};
    if (typeof o !== "object" || o === null) return {};
    const out: PhotoMap = {};
    for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
      if (/^\d+$/.test(k) && typeof v === "string" && v.startsWith("data:image/") && v.length < 60_000) out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}
export function getPhotos(): PhotoMap {
  map ??= IS_ARTIFACT ? {} : loadLocal();
  return map;
}
function setPhotos(next: PhotoMap, persist: boolean): void {
  map = next;
  if (persist) writeKey(LOCAL_KEY, JSON.stringify(next));
  for (const l of listeners) l();
}
export function usePhotos(): PhotoMap {
  const [m, setM] = useState(getPhotos);
  useEffect(() => {
    const on = (): void => {
      setM(getPhotos());
    };
    listeners.add(on);
    return () => {
      listeners.delete(on);
    };
  }, []);
  return m;
}

let started = false;
/** Shared page: follow photos/<player id> documents. */
export async function startPhotoSync(): Promise<void> {
  if (!IS_ARTIFACT || started) return;
  started = true;
  const p = await getPlatform();
  if (!p.db) return;
  try {
    p.db.collection("photos").onSnapshot(
      (snap) => {
        const next: PhotoMap = {};
        for (const d of snap.docs) {
          if (!d.exists || !/^\d{1,12}$/.test(d.id)) continue;
          const a = d.data()?.["asset"];
          if (validAssetId(a)) next[d.id] = assetUrl(a);
        }
        setPhotos(next, false);
      },
      () => undefined,
    );
  } catch {
    /* initials only */
  }
}

export type PhotoResult = { ok: true } | { ok: false; reason: string };

/** Downsize an image file to a small square-ish data URL (regular private build only). */
function shrink(file: File, max = 96): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.width * k));
      c.height = Math.max(1, Math.round(img.height * k));
      c.getContext("2d")?.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.8));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("not an image"));
    };
    img.src = url;
  });
}

/** Save a photo for a player. Shared page: platform assets then the db pointer; regular build: on this device. */
export async function savePhoto(playerId: number, file: File, platform?: Platform): Promise<PhotoResult> {
  const pid = playerId.toString();
  if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) return { ok: false, reason: "Use a PNG, JPEG, WebP or GIF image." };
  if (IS_ARTIFACT || platform) {
    const p = platform ?? (await getPlatform());
    if (!p.assets || !p.db) return { ok: false, reason: "Uploading photos is not available in this view (editors only)." };
    try {
      const up = await p.assets.upload(file);
      await p.db.doc(`photos/${pid}`).set({ v: 1, asset: up.id });
      return { ok: true };
    } catch (e) {
      const code = typeof e === "object" && e !== null && "code" in e ? String(e.code) : "error";
      return { ok: false, reason: code === "too_large" ? "That image is too large." : code === "quota_or_state" ? "The page's storage is full." : "The upload failed. Try again." };
    }
  }
  try {
    const next = { ...getPhotos(), [pid]: await shrink(file) };
    if (Object.keys(next).length > MAX_LOCAL) return { ok: false, reason: "Too many photos on this device." };
    setPhotos(next, true);
    return { ok: true };
  } catch {
    return { ok: false, reason: "That file could not be read as an image." };
  }
}

export async function removePhoto(playerId: number, platform?: Platform): Promise<PhotoResult> {
  const pid = playerId.toString();
  if (IS_ARTIFACT || platform) {
    const p = platform ?? (await getPlatform());
    if (!p.db) return { ok: false, reason: "Not available in this view." };
    try {
      await p.db.doc(`photos/${pid}`).delete();
      return { ok: true };
    } catch {
      return { ok: false, reason: "Could not remove it. Try again." };
    }
  }
  const rest = { ...getPhotos() };
  Reflect.deleteProperty(rest, pid);
  setPhotos(rest, true);
  return { ok: true };
}

/** Test hook. */
export function resetPhotos(): void {
  map = null;
}
