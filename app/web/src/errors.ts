/** Error copy a visitor can act on (F14). Developer instructions appear only in a dev build. */

const SEP = " :: ";

/** Message for a failed request: status line, plus the server's own reason when it gave one. */
export function httpErrorMessage(status: number, statusText: string, path: string, detail: string | null): string {
  const base = `${status.toString()} ${statusText} for ${path}`;
  return detail ? `${base}${SEP}${detail}` : base;
}

export interface ErrorCopy {
  title: string;
  body: string;
  /** The server's reason, shown only when it refused to serve (503). */
  reason: string | null;
  /** True only for a dev build, where the "start the API" hint is useful. */
  devHint: boolean;
}

export function errorCopy(message: string, dev: boolean): ErrorCopy {
  const [head = "", ...rest] = message.split(SEP);
  const detail = rest.join(SEP).trim() || null;
  const status = /^(\d{3}) /.exec(head)?.[1];
  if (status === "503" && detail) {
    return {
      title: "Today's data failed its checks",
      body: "We are not showing it until it passes. Nothing here is out of date or made up: there is simply nothing to show yet.",
      reason: detail,
      devHint: false,
    };
  }
  return {
    title: "We can't reach our data right now",
    body: "Try again in a minute. If you have used this page before, the last copy we saved may still open.",
    reason: null,
    devHint: dev,
  };
}

// ------------------------------------------------------------------------------------------------ typed failures

/**
 * One failed API answer, the same shape whichever server answered. The new contract sends
 * `{ok: false, code, text, field, request_id, detail}` plus an X-Request-Id header; an older server sends `{detail}` only,
 * and then the code is inferred from the status and the path. A request that never got an answer has status null.
 */
export interface ApiFailure {
  ok: false;
  status: number | null;
  code: string;
  /** The server's one-line text for the code, when it sent one. */
  text: string | null;
  /** The server's own reason (a refusal's "why"), shown verbatim. */
  detail: string | null;
  field: string | null;
  requestId: string | null;
  path: string;
}

const str = (x: unknown): string | null => (typeof x === "string" && x.length > 0 ? x : null);

/** The code an older server meant, from the status and the path it was asked. */
export function codeForStatus(status: number | null, path: string, online = true): string {
  if (status === null) return online ? "network_unreachable" : "offline";
  const p = path.split("?")[0] ?? "";
  if (status === 404) {
    if (/^\/api\/games\/[^/]+$/.test(p)) return "unknown_game";
    if (/^\/api\/slates\/[^/]+$/.test(p)) return "unknown_slate";
    if (/^\/api\/(breakdown|card|edges|props|knows)\/[^/]+\/[^/]+\/[^/]+$/.test(p)) return "no_forecast";
    if (/^\/api\/knows\/[^/]+$/.test(p)) return "unknown_game";
    if (/^\/api\/games\/[^/]+\/result$/.test(p) || /^\/api\/slates\/[^/]+\/results$/.test(p)) return "game_not_played";
    if (p.startsWith("/api/live/")) return "no_live_capture";
    if (p === "/api/desk") return "no_desk";
    if (p.startsWith("/api/top-parlays/")) return "no_top_parlays";
    return "not_found";
  }
  if (status === 422 || status === 400) return "bad_request";
  if (status === 405) return "method_not_allowed";
  if (status === 503) return "export_refused";
  if (status >= 500) return "internal_error";
  return "unexpected_error";
}

/** A failure from an HTTP answer: the body's own code and words when present, else inferred. */
export function failureFromAnswer(status: number, body: unknown, path: string, headerId: string | null = null): ApiFailure {
  const o = typeof body === "object" && body !== null && !Array.isArray(body) ? (body as Record<string, unknown>) : {};
  // A 500/502/504 with no JSON body is the dev proxy or a gateway saying the API did not answer, not the API failing.
  const proxyDown = body === null && (status === 500 || status === 502 || status === 504);
  const code = str(o["code"]) ?? (proxyDown ? "network_unreachable" : codeForStatus(status, path));
  return {
    ok: false,
    status,
    code,
    text: str(o["text"]),
    detail: str(o["detail"]),
    field: str(o["field"]),
    requestId: str(o["request_id"]) ?? headerId,
    path,
  };
}

/** A failure for a request that got no answer (the network, or the browser offline). */
export function failureFromNetwork(path: string, online: boolean, why: string | null = null): ApiFailure {
  return { ok: false, status: null, code: codeForStatus(null, path, online), text: null, detail: why, field: null, requestId: null, path };
}

/** The one-line message for a failure, in the old "status for path :: detail" form the copy helpers read. */
export function failureMessage(f: ApiFailure): string {
  const head = f.status === null ? `Failed to fetch ${f.path}` : `${f.status.toString()} for ${f.path}`;
  const detail = f.detail ?? f.text;
  return detail ? `${head}${SEP}${detail}` : head;
}

/** Read a failure back out of an old one-line message (for screens that only kept the message). */
export function failureFromMessage(message: string): ApiFailure {
  const [head = "", ...rest] = message.split(SEP);
  const detail = rest.join(SEP).trim() || null;
  const m = /^(\d{3}) .*?for (\/\S*)/.exec(head);
  if (!m) return failureFromNetwork("", true, detail);
  const status = Number(m[1]);
  return failureFromAnswer(status, { detail }, m[2] ?? "");
}

/** What the failure screen shows as the reason: only a refusal's own words (503), never a raw 404 detail. */
export function reasonOf(f: ApiFailure): string | null {
  if (f.status === 503 || f.code === "export_refused" || f.code === "desk_refused" || f.code === "top_parlays_refused") return f.detail ?? f.text;
  return null;
}

/** The developer line (status and path), shown behind "Why this happens". */
export function technicalOf(f: ApiFailure): string {
  return `${f.status === null ? "no answer" : f.status.toString()} for ${f.path || "(unknown)"}${f.field ? `, field ${f.field}` : ""}`;
}

/** A failure carried by a thrown error, so every caller of the API sees the same typed result. */
export class ApiError extends Error {
  readonly failure: ApiFailure;
  constructor(failure: ApiFailure) {
    super(failureMessage(failure));
    this.name = "ApiError";
    this.failure = failure;
  }
}

/** The typed failure for anything a load rejected with. */
export function failureOf(e: unknown): ApiFailure {
  if (e instanceof ApiError) return e.failure;
  const msg = e instanceof Error ? e.message : String(e);
  return /^\d{3} /.test(msg) ? failureFromMessage(msg) : { ...failureFromNetwork("", true, msg), code: "unexpected_error" };
}
