const ET = "America/New_York";

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

/** P(over) shown with a floor/ceiling when the stored quantile grid does not reach that far. */
export function pctBounded(p: number, extrapolated: boolean): string {
  if (!extrapolated) return pct(p);
  if (p >= 0.9) return "95%+";
  if (p <= 0.1) return "<5%";
  return pct(p);
}

export function american(price: number): string {
  return price > 0 ? `+${price}` : `${price}`;
}

export function num(x: number, digits = 1): string {
  return x.toFixed(digits).replace(/\.0$/, "");
}

export function lineText(line: number): string {
  return Number.isInteger(line) ? line.toFixed(0) : line.toFixed(1);
}

export function tipTime(utc: string): string {
  const d = new Date(utc);
  return (
    new Intl.DateTimeFormat("en-US", {
      timeZone: ET,
      hour: "numeric",
      minute: "2-digit",
    }).format(d) + " ET"
  );
}

export function longDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12));
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(dt);
}

export function stamp(utc: string): string {
  return (
    new Intl.DateTimeFormat("en-US", {
      timeZone: ET,
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(utc)) + " ET"
  );
}

export function signed(x: number, digits = 4): string {
  return `${x >= 0 ? "+" : "-"}${Math.abs(x).toFixed(digits)}`;
}
