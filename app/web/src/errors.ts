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
