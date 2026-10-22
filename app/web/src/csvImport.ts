// Parse the viewer's own Kalshi trade-history CSV, entirely in the browser. Unknown columns are ignored.
// Nothing here uploads, stores or sends anything.

export interface Trade {
  ticker: string;
  side: "yes" | "no" | "";
  action: "buy" | "sell" | "";
  count: number | null;
  /** Dollars per contract (cents in the file are converted). */
  price: number | null;
  time: string;
}

export interface Parsed {
  columns: Record<"ticker" | "side" | "action" | "count" | "price" | "time", string | null>;
  trades: Trade[];
  skipped: number;
  ignoredColumns: string[];
  notes: string[];
}

/** RFC 4180-ish: quoted fields, doubled quotes, commas or tabs, CRLF. */
export function parseCsv(text: string): string[][] {
  const t = text.startsWith(String.fromCharCode(0xfeff)) ? text.slice(1) : text;
  const delim = (t.split("\n", 1)[0] ?? "").includes("\t") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let q = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i] as string;
    if (q) {
      if (c === '"') {
        if (t[i + 1] === '"') {
          cur += '"';
          i++;
        } else q = false;
      } else cur += c;
    } else if (c === '"') q = true;
    else if (c === delim) {
      row.push(cur);
      cur = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && t[i + 1] === "\n") i++;
      row.push(cur);
      cur = "";
      if (row.some((x) => x.trim() !== "")) rows.push(row);
      row = [];
    } else cur += c;
  }
  row.push(cur);
  if (row.some((x) => x.trim() !== "")) rows.push(row);
  return rows;
}

const NAMES: Record<keyof Parsed["columns"], string[]> = {
  ticker: ["ticker", "market ticker", "market_ticker", "market", "contract", "market id"],
  side: ["side", "position", "outcome", "taker side", "taker_side"],
  action: ["action", "type", "order type", "trade type", "buy/sell", "direction"],
  count: ["count", "contracts", "quantity", "qty", "shares", "size", "filled"],
  price: ["price", "avg price", "average price", "yes price", "fill price", "price per contract", "yes_price"],
  time: ["time", "created", "created time", "created_time", "timestamp", "date", "executed", "filled at", "ts"],
};

function num(s: string): number | null {
  const n = Number(s.replace(/[$,%\s]/g, "").replace(/^\((.*)\)$/, "-$1"));
  return s.trim() === "" || !Number.isFinite(n) ? null : n;
}

export function parseKalshiCsv(text: string): Parsed {
  const rows = parseCsv(text);
  const empty: Parsed = {
    columns: { ticker: null, side: null, action: null, count: null, price: null, time: null },
    trades: [],
    skipped: 0,
    ignoredColumns: [],
    notes: [],
  };
  const header = rows[0];
  if (!header) return { ...empty, notes: ["The file is empty."] };
  const low = header.map((h) => h.trim().toLowerCase());
  const columns = { ...empty.columns };
  const used = new Set<number>();
  (Object.keys(NAMES) as (keyof Parsed["columns"])[]).forEach((k) => {
    for (const name of NAMES[k]) {
      const i = low.findIndex((h, j) => h === name && !used.has(j));
      if (i >= 0) {
        columns[k] = header[i]?.trim() ?? null;
        used.add(i);
        return;
      }
    }
  });
  const ignoredColumns = header.filter((_, i) => !used.has(i)).map((h) => h.trim()).filter((h) => h !== "");
  const notes: string[] = [];
  if (!columns.ticker) notes.push("No ticker column found, so nothing can be matched to a leg. Columns seen: " + header.join(", "));
  const at = (k: keyof Parsed["columns"]): number => (columns[k] ? header.findIndex((h) => h.trim() === columns[k]) : -1);
  const [iT, iS, iA, iC, iP, iM] = [at("ticker"), at("side"), at("action"), at("count"), at("price"), at("time")];
  const trades: Trade[] = [];
  let skipped = 0;
  for (const r of rows.slice(1)) {
    const ticker = (iT >= 0 ? r[iT] : "")?.trim() ?? "";
    if (ticker === "") {
      skipped++;
      continue;
    }
    const sideRaw = ((iS >= 0 ? r[iS] : "") ?? "").toLowerCase();
    const actRaw = ((iA >= 0 ? r[iA] : "") ?? "").toLowerCase();
    let price = iP >= 0 ? num(r[iP] ?? "") : null;
    if (price !== null && price > 1) price /= 100;
    trades.push({
      ticker,
      side: sideRaw.includes("no") ? "no" : sideRaw.includes("yes") ? "yes" : "",
      action: actRaw.includes("sell") ? "sell" : actRaw.includes("buy") ? "buy" : "",
      count: iC >= 0 ? num(r[iC] ?? "") : null,
      price,
      time: iM >= 0 ? (r[iM] ?? "").trim() : "",
    });
  }
  if (columns.price) notes.push("Prices above 1 are read as cents.");
  return { columns, trades, skipped, ignoredColumns, notes };
}

export interface Holding {
  ticker: string;
  side: "yes" | "no" | "";
  trades: number;
  contracts: number;
  /** Contract-weighted average price of the buys, dollars; null when counts or prices are missing. */
  avgPrice: number | null;
}

/** One row per ticker and side, from the buys (a row with no action column counts as a buy). */
export function holdings(trades: Trade[]): Holding[] {
  const m = new Map<string, { h: Holding; cost: number; n: number }>();
  for (const t of trades) {
    if (t.action === "sell") continue;
    const k = `${t.ticker}|${t.side}`;
    const e = m.get(k) ?? { h: { ticker: t.ticker, side: t.side, trades: 0, contracts: 0, avgPrice: null }, cost: 0, n: 0 };
    e.h.trades++;
    if (t.count !== null) e.h.contracts += t.count;
    if (t.count !== null && t.price !== null) {
      e.cost += t.count * t.price;
      e.n += t.count;
    }
    m.set(k, e);
  }
  return [...m.values()].map((e) => ({ ...e.h, avgPrice: e.n > 0 ? e.cost / e.n : null }));
}
