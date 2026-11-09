// Saving a file the viewer asked for. Shared page: only through the platform's `downloads` capability (the viewer confirms);
// where it is absent we say so. Regular build: an ordinary browser download from a Blob.
import { IS_ARTIFACT } from "./env";
import { getPlatform } from "./platform";

export type SaveOutcome = { ok: true; how: "saved" | "delivered" | "downloaded" } | { ok: false; reason: string };

export async function saveTextFile(filename: string, text: string, mime = "text/csv"): Promise<SaveOutcome> {
  if (IS_ARTIFACT) {
    const p = await getPlatform();
    if (!p.downloads) return { ok: false, reason: "Saving files is not available in this view." };
    try {
      const r = await p.downloads.save({ filename, data: text });
      return { ok: true, how: r.status };
    } catch (e) {
      const code = typeof e === "object" && e !== null && "code" in e ? String(e.code) : "error";
      return { ok: false, reason: code === "declined" ? "Saving was cancelled." : "The file could not be saved here." };
    }
  }
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  return { ok: true, how: "downloaded" };
}
