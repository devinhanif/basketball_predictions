// A small PNG decoder (and a tiny encoder for its self-test), plus the pixel comparison the visual check uses.
// Plain Node: node:zlib only. A developer tool; nothing here ships.
//
//   node scripts/png.mjs --self-test        encodes a small image with all five scanline filters, decodes it, compares
//
// Supported: 8-bit depth, colour types 0 (grey), 2 (RGB), 3 (palette), 4 (grey + alpha), 6 (RGBA), no interlace. That
// covers what headless Chrome's Page.captureScreenshot writes. Anything else is refused with a clear error.
import { crc32, deflateSync, inflateSync } from "node:zlib";

const SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

/** Decode a PNG buffer to { width, height, data } with data as RGBA bytes (row-major). */
export function decodePng(buf) {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(SIG)) throw new Error("not a PNG (bad signature)");
  let off = 8;
  let width = 0;
  let height = 0;
  let depth = 0;
  let ctype = -1;
  let interlace = 0;
  let palette = null;
  const idat = [];
  while (off + 12 <= buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString("latin1", off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    if (crc32(buf.subarray(off + 4, off + 8 + len)) !== buf.readUInt32BE(off + 8 + len)) throw new Error(`PNG chunk ${type}: CRC mismatch`);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      depth = data[8];
      ctype = data[9];
      interlace = data[12];
    } else if (type === "PLTE") palette = data;
    else if (type === "IDAT") idat.push(data);
    else if (type === "IEND") break;
    off += 12 + len;
  }
  const ch = CHANNELS[ctype];
  if (!ch) throw new Error(`PNG colour type ${ctype} is not supported`);
  if (depth !== 8) throw new Error(`PNG bit depth ${depth} is not supported (8 only)`);
  if (interlace !== 0) throw new Error("interlaced PNG is not supported");
  if (ctype === 3 && !palette) throw new Error("palette PNG without a PLTE chunk");
  const stride = width * ch;
  const raw = inflateSync(Buffer.concat(idat));
  if (raw.length < height * (stride + 1)) throw new Error("PNG data is shorter than its header says");
  const out = Buffer.alloc(width * height * 4);
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const at = y * (stride + 1);
    const filter = raw[at];
    const line = raw.subarray(at + 1, at + 1 + stride);
    const cur = Buffer.alloc(stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? cur[i - ch] : 0;
      const b = prev[i];
      const c = i >= ch ? prev[i - ch] : 0;
      let v = line[i];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) v += paeth(a, b, c);
      else if (filter !== 0) throw new Error(`PNG row ${y}: unknown filter type ${filter}`);
      cur[i] = v & 255;
    }
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      const s = x * ch;
      if (ctype === 6) cur.copy(out, o, s, s + 4);
      else if (ctype === 2) {
        out[o] = cur[s];
        out[o + 1] = cur[s + 1];
        out[o + 2] = cur[s + 2];
        out[o + 3] = 255;
      } else if (ctype === 0 || ctype === 4) {
        out[o] = out[o + 1] = out[o + 2] = cur[s];
        out[o + 3] = ctype === 4 ? cur[s + 1] : 255;
      } else {
        const p = cur[s] * 3;
        out[o] = palette[p];
        out[o + 1] = palette[p + 1];
        out[o + 2] = palette[p + 2];
        out[o + 3] = 255;
      }
    }
    prev = cur;
  }
  return { width, height, data: out };
}

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "latin1");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}

/** Encode RGBA (ctype 6) or RGB (ctype 2) bytes; row y uses filter filters[y % filters.length]. For the self-test. */
export function encodePng(width, height, data, { ctype = 6, filters = [0, 1, 2, 3, 4] } = {}) {
  const ch = CHANNELS[ctype];
  const stride = width * ch;
  const rows = [];
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const cur = Buffer.alloc(stride);
    for (let x = 0; x < width; x++) for (let k = 0; k < ch; k++) cur[x * ch + k] = data[(y * width + x) * 4 + k];
    const f = filters[y % filters.length];
    const line = Buffer.alloc(stride + 1);
    line[0] = f;
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? cur[i - ch] : 0;
      const b = prev[i];
      const c = i >= ch ? prev[i - ch] : 0;
      const pred = f === 0 ? 0 : f === 1 ? a : f === 2 ? b : f === 3 ? (a + b) >> 1 : paeth(a, b, c);
      line[i + 1] = (cur[i] - pred) & 255;
    }
    rows.push(line);
    prev = cur;
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = ctype;
  return Buffer.concat([SIG, chunk("IHDR", ihdr), chunk("IDAT", deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
}

/**
 * Compare two decoded images. A pixel differs when any channel differs by more than `tolerance`. Returns the fraction of
 * differing pixels and the changed regions as bounding boxes (connected tiles of `tile` px, largest first).
 */
export function comparePixels(a, b, { tolerance = 4, tile = 32, maxRegions = 6 } = {}) {
  if (a.width !== b.width || a.height !== b.height) {
    return { sizeChanged: true, fraction: 1, changed: a.width * a.height, total: a.width * a.height, regions: [{ x: 0, y: 0, w: Math.max(a.width, b.width), h: Math.max(a.height, b.height) }], note: `size ${b.width}x${b.height} -> ${a.width}x${a.height}` };
  }
  const { width, height } = a;
  const tw = Math.ceil(width / tile);
  const th = Math.ceil(height / tile);
  const marks = new Uint8Array(tw * th);
  let changed = 0;
  const A = a.data;
  const B = b.data;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      if (Math.abs(A[o] - B[o]) > tolerance || Math.abs(A[o + 1] - B[o + 1]) > tolerance || Math.abs(A[o + 2] - B[o + 2]) > tolerance || Math.abs(A[o + 3] - B[o + 3]) > tolerance) {
        changed++;
        marks[Math.floor(y / tile) * tw + Math.floor(x / tile)] = 1;
      }
    }
  }
  const regions = [];
  const seen = new Uint8Array(tw * th);
  for (let i = 0; i < marks.length; i++) {
    if (!marks[i] || seen[i]) continue;
    let x0 = tw;
    let y0 = th;
    let x1 = -1;
    let y1 = -1;
    let n = 0;
    const stack = [i];
    seen[i] = 1;
    while (stack.length) {
      const j = stack.pop();
      const tx = j % tw;
      const ty = (j - tx) / tw;
      n++;
      x0 = Math.min(x0, tx);
      y0 = Math.min(y0, ty);
      x1 = Math.max(x1, tx);
      y1 = Math.max(y1, ty);
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = tx + dx;
          const ny = ty + dy;
          if (nx < 0 || ny < 0 || nx >= tw || ny >= th) continue;
          const k = ny * tw + nx;
          if (marks[k] && !seen[k]) {
            seen[k] = 1;
            stack.push(k);
          }
        }
      }
    }
    const x = x0 * tile;
    const y = y0 * tile;
    regions.push({ x, y, w: Math.min(width, (x1 + 1) * tile) - x, h: Math.min(height, (y1 + 1) * tile) - y, tiles: n });
  }
  regions.sort((r, s) => s.tiles - r.tiles);
  return { sizeChanged: false, fraction: changed / (width * height), changed, total: width * height, regions: regions.slice(0, maxRegions).map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h })), nRegions: regions.length };
}

/** Encode a deterministic test image with every filter type and both colour types, decode it, compare. Throws on failure. */
export function selfTest() {
  const w = 13;
  const h = 11;
  const img = Buffer.alloc(w * h * 4);
  let s = 7;
  for (let i = 0; i < img.length; i++) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    img[i] = (i % 4 === 3 ? 128 + (s % 128) : s >> 16) & 255;
  }
  const checks = [];
  for (const [ctype, filters] of [[6, [0, 1, 2, 3, 4]], [6, [4]], [6, [3]], [2, [0, 1, 2, 3, 4]], [2, [2, 4, 1]]]) {
    const png = encodePng(w, h, img, { ctype, filters });
    const d = decodePng(png);
    if (d.width !== w || d.height !== h) throw new Error(`self-test: size ${d.width}x${d.height}, want ${w}x${h}`);
    for (let i = 0; i < w * h; i++) {
      for (let k = 0; k < 4; k++) {
        const want = ctype === 2 && k === 3 ? 255 : img[i * 4 + k];
        if (d.data[i * 4 + k] !== want) throw new Error(`self-test: ctype ${ctype} filters ${filters.join("")}: pixel ${i} channel ${k} is ${d.data[i * 4 + k]}, want ${want}`);
      }
    }
    checks.push(`ctype ${ctype} filters ${filters.join("")}`);
  }
  // The comparison: identical images give 0; one changed pixel gives exactly 1 / (w*h) and one region around it.
  const a = decodePng(encodePng(w, h, img));
  const b = { ...a, data: Buffer.from(a.data) };
  if (comparePixels(a, b).changed !== 0) throw new Error("self-test: identical images differ");
  b.data[(5 * w + 6) * 4] ^= 0x80;
  const c = comparePixels(a, b, { tile: 4 });
  if (c.changed !== 1 || c.regions.length !== 1 || c.regions[0].x !== 4 || c.regions[0].y !== 4) throw new Error(`self-test: one changed pixel gave ${JSON.stringify(c)}`);
  b.data[(5 * w + 6) * 4] = a.data[(5 * w + 6) * 4] ^ 0x02; // a difference inside the tolerance is not a change
  if (comparePixels(a, b, { tolerance: 4 }).changed !== 0) throw new Error("self-test: a difference inside the tolerance counted");
  // A malformed file is refused, not decoded into garbage.
  const bad = encodePng(w, h, img);
  bad[bad.length - 20] ^= 0xff;
  let refused = false;
  try {
    decodePng(bad);
  } catch {
    refused = true;
  }
  if (!refused) throw new Error("self-test: a corrupted PNG was accepted");
  return checks;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href && process.argv.includes("--self-test")) {
  const checks = selfTest();
  console.log(`png self-test passed: ${checks.join("; ")}; comparison, tolerance and CRC checks`);
}
