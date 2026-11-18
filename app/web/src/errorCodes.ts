// The shared error-code registry (app/error_codes.json, owned by the API lane), bundled into the page: every failure
// screen shows the registry's plain words for its code. Nothing here is fetched.
import registry from "../../error_codes.json";

export interface CodeCopy {
  code: string;
  status: number | null;
  side: string;
  title: string;
  what: string;
  why: string;
  todo: string;
}

const isCopy = (x: unknown): x is CodeCopy => {
  if (typeof x !== "object" || x === null) return false;
  const o = x as Record<string, unknown>;
  return ["code", "side", "title", "what", "why", "todo"].every((k) => typeof o[k] === "string");
};

const raw: unknown = (registry as { codes?: unknown }).codes;
const BY_CODE: ReadonlyMap<string, CodeCopy> = new Map((Array.isArray(raw) ? raw.filter(isCopy) : []).map((c) => [c.code, c]));

/** Codes the web app itself raises (seeded in the registry for it). */
export type WebCode = "network_unreachable" | "offline" | "stale_data" | "live_feed_stopped" | "bad_route" | "unexpected_error";

/** Used only if the bundled registry lacks a code: the screen still says something true and gives a way on. */
const FALLBACK: CodeCopy = {
  code: "unexpected_error",
  status: null,
  side: "web",
  title: "This page hit a problem",
  what: "Something on this page did not work as expected.",
  why: "We do not know more than that here.",
  todo: "Go back to Discover and try again.",
};

/** The registry's words for a code; an unknown code gets the generic copy, keeping the code it was given. */
export function codeCopy(code: string): CodeCopy {
  return BY_CODE.get(code) ?? BY_CODE.get("unexpected_error") ?? FALLBACK;
}

/** Is this code in the registry? */
export function knownCode(code: string): boolean {
  return BY_CODE.has(code);
}

/** Every code in the bundled registry (for tests). */
export function allCodes(): string[] {
  return [...BY_CODE.keys()];
}

/** Every code's copy, in registry order (the help page). */
export function allCopies(): CodeCopy[] {
  return [...BY_CODE.values()];
}

/** Codes whose words contain the search text (code, title, what, why or todo); empty text matches all. */
export function searchCodes(list: readonly CodeCopy[], q: string): CodeCopy[] {
  const t = q.trim().toLowerCase();
  if (t === "") return [...list];
  return list.filter((c) => [c.code, c.title, c.what, c.why, c.todo].some((s) => s.toLowerCase().includes(t)));
}
