// What the Admin studio may write, kept in one place so each path can be asserted exactly. Owner-only paths are enforced by the
// platform's db rules; this file only names them.
import type { Platform } from "./platform";

/** Remove a viewer's shared documents (picks and watchlist). Returns false when there is no db. Their device republishes its own copy at their next change. */
export async function clearViewer(p: Platform, uid: string): Promise<boolean> {
  if (!p.db) return false;
  await p.db.doc(`picks/${uid}`).delete();
  await p.db.doc(`watch/${uid}`).delete();
  return true;
}
