// The artifact platform's capabilities (db, user, assets, downloads), each of which may resolve null: every caller designs
// the absent state. Typed by hand, only what is used (the .d.ts files in the artifact contract are authoritative).
// In the regular build there is no window.claude and everything is null at once.
import { useEffect, useState } from "preact/hooks";
import { IS_ARTIFACT } from "./env";
import type { DbLike } from "./sharedPicks";

export interface Viewer {
  id: string | null;
  name: string;
  avatarUrl: string;
  isOwner: boolean;
  canEdit: boolean;
}
export interface UserNs {
  id(): Promise<string | null>;
  me(): Promise<Viewer>;
  isOwner(): Promise<boolean>;
  canEdit(): Promise<boolean>;
  profiles(ids: readonly string[] | string): Promise<Record<string, { id: string; name: string; avatarUrl: string }>>;
}
export interface AssetsNs {
  upload(blob: Blob, options?: { type?: string }): Promise<{ id: string; url: string }>;
  delete(ref: string): Promise<{ deleted: boolean }>;
}
export interface DownloadsNs {
  save(req: { filename: string; data: string | Blob }): Promise<{ status: "saved" | "delivered" }>;
}
/** A db document reference as far as this app uses one. */
export interface DocRef {
  get(): Promise<{ exists: boolean; data(): Record<string, unknown> | undefined }>;
  set(data: Record<string, unknown>): Promise<void>;
  delete(): Promise<void>;
  onSnapshot(
    next: (s: { exists: boolean; data(): Record<string, unknown> | undefined; metadata?: { fromCache: boolean } }) => void,
    error?: (e: { code: string }) => void,
  ): () => void;
}
export interface FullDb extends DbLike {
  doc(path: string): DocRef;
  collection(path: string): ReturnType<DbLike["collection"]> & { doc(id: string): DocRef };
}

export interface Platform {
  db: FullDb | null;
  user: UserNs | null;
  assets: AssetsNs | null;
  downloads: DownloadsNs | null;
}

const NONE: Platform = { db: null, user: null, assets: null, downloads: null };
let cached: Promise<Platform> | null = null;

async function cap<T>(name: string): Promise<T | null> {
  try {
    const c = (window as unknown as { claude?: { use?: (n: string) => Promise<unknown> } }).claude;
    if (!c || typeof c.use !== "function") return null;
    return ((await c.use(name)) as T | null) ?? null;
  } catch {
    return null;
  }
}

/** Resolves once every capability has answered (each is null when absent; null after about 10 s when the host is silent). */
export function getPlatform(): Promise<Platform> {
  if (!IS_ARTIFACT) return Promise.resolve(NONE);
  cached ??= Promise.all([cap<FullDb>("db"), cap<UserNs>("user"), cap<AssetsNs>("assets"), cap<DownloadsNs>("downloads")]).then(
    ([db, user, assets, downloads]) => ({ db, user, assets, downloads }),
  );
  return cached;
}

export function usePlatform(): Platform | null {
  const [p, setP] = useState<Platform | null>(IS_ARTIFACT ? null : NONE);
  useEffect(() => {
    let live = true;
    void getPlatform().then((x) => {
      if (live) setP(x);
    });
    return () => {
      live = false;
    };
  }, []);
  return p;
}

/** Test hook. */
export function resetPlatform(): void {
  cached = null;
}
