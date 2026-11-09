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

export const CAP_TIMEOUT_MS = 10_000;
export interface ClaudeUse {
  use?: (n: string) => Promise<unknown>;
}

/** One capability: null when absent, refused, or silent for 10 s (the host may never answer; the page renders without it). */
export async function cap<T>(c: ClaudeUse | undefined, name: string, ms = CAP_TIMEOUT_MS): Promise<T | null> {
  try {
    if (!c || typeof c.use !== "function") return null;
    const silent = new Promise<null>((resolve) => {
      setTimeout(() => {
        resolve(null);
      }, ms);
    });
    return ((await Promise.race([c.use(name), silent])) as T | null) ?? null;
  } catch {
    return null;
  }
}

export function resolvePlatform(c: ClaudeUse | undefined, ms = CAP_TIMEOUT_MS): Promise<Platform> {
  return Promise.all([cap<FullDb>(c, "db", ms), cap<UserNs>(c, "user", ms), cap<AssetsNs>(c, "assets", ms), cap<DownloadsNs>(c, "downloads", ms)]).then(
    ([db, user, assets, downloads]) => ({ db, user, assets, downloads }),
  );
}

/** Resolves once every capability has answered (each is null when absent; null after about 10 s when the host is silent). */
export function getPlatform(): Promise<Platform> {
  if (!IS_ARTIFACT) return Promise.resolve(NONE);
  cached ??= resolvePlatform((window as unknown as { claude?: ClaudeUse }).claude);
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
