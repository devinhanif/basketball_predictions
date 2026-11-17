// Local record ids and immutable snapshots. Ids name workspace records only; they are never credentials.

let counter = 0;

/** A random v4-style id; falls back to time + counter + Math.random where crypto is missing (old browsers). */
export function localId(prefix?: string): string {
  const c: Crypto | undefined = (globalThis as { crypto?: Crypto }).crypto;
  let id: string;
  if (c !== undefined && typeof c.randomUUID === "function") {
    id = c.randomUUID();
  } else if (c !== undefined && typeof c.getRandomValues === "function") {
    const b = c.getRandomValues(new Uint8Array(16));
    b[6] = ((b[6] ?? 0) & 0x0f) | 0x40;
    b[8] = ((b[8] ?? 0) & 0x3f) | 0x80;
    const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  } else {
    counter += 1;
    id = `local-${Date.now().toString(36)}-${counter.toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }
  return prefix === undefined ? id : `${prefix}-${id}`;
}

/** A JSON deep copy (the stored shapes are plain JSON; structuredClone is not assumed on old Safari). */
export function jsonCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Freezes a plain object graph in place and returns it. */
export function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const v of Object.values(value as Record<string, unknown>)) deepFreeze(v);
  }
  return value;
}

/** An independent frozen copy: what every snapshot (leg quote, leg market, saved idea) is. */
export function frozenCopy<T>(value: T): T {
  return deepFreeze(jsonCopy(value));
}

/** FNV-1a over a string, as 8 hex digits: binds an idempotency key to its payload. Not a security function. */
export function digest(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}
