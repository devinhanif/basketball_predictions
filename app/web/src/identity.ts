// Who is looking. In the shared page: the platform's user (never a password, never a name we store). In the regular build:
// a display name typed once and kept on this device. Admin is gated by the platform's owner/editor check, or in the
// regular build by the private build (an API started with PH_PRIVATE=1), never by a password in the browser.
import { useEffect, useState } from "preact/hooks";
import { IS_ARTIFACT } from "./env";
import { getPlatform } from "./platform";
import { readKey, writeKey } from "./prefs";

export interface Identity {
  /** "platform" in the shared page, "local" in the regular build, "none" before either has answered. */
  source: "platform" | "local" | "none";
  id: string | null;
  /** Display text only (set as text, never as HTML). */
  name: string;
  avatarUrl: string | null;
  isOwner: boolean;
  canEdit: boolean;
  loading: boolean;
}

export const NAME_KEY = "ph.name";
export const WELCOME_KEY = "ph.welcomed";
export const MAX_NAME = 40;

/** A typed display name: trimmed, whitespace folded, capped. Empty means not set. */
export function cleanName(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
}

export function localName(): string {
  return cleanName(readKey(NAME_KEY) ?? "");
}
export function setLocalName(name: string): void {
  writeKey(NAME_KEY, cleanName(name));
  for (const l of listeners) l();
}
/** Regular build "sign out": forget the local name (and the first-run flag, so the welcome shows again). */
export function clearLocalIdentity(): void {
  writeKey(NAME_KEY, "");
  writeKey(WELCOME_KEY, "");
  for (const l of listeners) l();
}
export function hasWelcomed(): boolean {
  return readKey(WELCOME_KEY) === "1";
}
export function markWelcomed(): void {
  writeKey(WELCOME_KEY, "1");
  for (const l of listeners) l();
}

/** Initials for an avatar: up to two letters from a name; "?" when there is none. */
export function initials(name: string): string {
  const parts = name.replace(/[^\p{L}\p{N} '-]/gu, " ").split(/[ -]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1] ?? "") : "";
  return ((first[0] ?? "") + (last[0] ?? "")).toUpperCase() || "?";
}

/** Can this viewer see the Admin studio? Owner or editor in the shared page; the private build in the regular one. */
export function canSeeAdmin(id: Identity, privateDesk: boolean): boolean {
  if (IS_ARTIFACT) return id.isOwner || id.canEdit;
  return privateDesk;
}

const listeners = new Set<() => void>();
let platformIdentity: Partial<Identity> | null = null;

function current(): Identity {
  if (IS_ARTIFACT) {
    const p = platformIdentity;
    return {
      source: p ? "platform" : "none",
      id: p?.id ?? null,
      name: p?.name ?? "",
      avatarUrl: p?.avatarUrl ?? null,
      isOwner: p?.isOwner ?? false,
      canEdit: p?.canEdit ?? false,
      loading: p === null,
    };
  }
  return { source: "local", id: null, name: localName(), avatarUrl: null, isOwner: false, canEdit: false, loading: false };
}

export function useIdentity(): Identity {
  const [id, setId] = useState<Identity>(current);
  useEffect(() => {
    const on = (): void => {
      setId(current());
    };
    listeners.add(on);
    if (IS_ARTIFACT && platformIdentity === null) {
      void getPlatform().then(async (p) => {
        if (p.user) {
          try {
            const me = await p.user.me();
            platformIdentity = { id: me.id, name: me.name || "", avatarUrl: me.avatarUrl, isOwner: me.isOwner, canEdit: me.canEdit };
          } catch {
            platformIdentity = {};
          }
        } else {
          platformIdentity = {};
        }
        for (const l of listeners) l();
      });
    }
    return () => {
      listeners.delete(on);
    };
  }, []);
  return id;
}

/** Test hook. */
export function resetIdentity(): void {
  platformIdentity = null;
}
