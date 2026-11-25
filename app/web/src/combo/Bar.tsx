// The square bar for one leg: the chance as it stands, then the chance in the script, on a fixed 30% to 70% scale.
// Flat track, a thin tick at 50%, a band between the two chances (green when it rises, red when it falls; the signed change is
// printed beside it, so colour is never the only signal), a thin grey tick at "as it stands" and a solid square at "in the script".
// No rounded ends, no gradients, no rings. Values outside the scale are drawn at the edge and the label says so.
export const BAR_MIN = 0.3;
export const BAR_MAX = 0.7;

export function barLabel(now: number, script: number, scriptName: string): { text: string; clamped: boolean } {
  const clamped = now < BAR_MIN || now > BAR_MAX || script < BAR_MIN || script > BAR_MAX;
  const d = (script - now) * 100;
  const dir = d > 0 ? "up" : d < 0 ? "down" : "unchanged";
  const move = d === 0 ? "unchanged" : `${dir} ${Math.abs(d).toFixed(1)} points`;
  return {
    text:
      `Chance as it stands ${(now * 100).toFixed(1)} percent; if ${scriptName}, ${(script * 100).toFixed(1)} percent, ${move}. ` +
      `Bar scale 30 to 70 percent${clamped ? "; a value outside the scale is drawn at the edge" : ""}.`,
    clamped,
  };
}

const at = (p: number): number => (Math.min(BAR_MAX, Math.max(BAR_MIN, p)) - BAR_MIN) / (BAR_MAX - BAR_MIN);

export function Bar(props: { now: number; script: number; scriptName: string }) {
  const { now, script } = props;
  const a = at(now);
  const b = at(script);
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const l = barLabel(now, script, props.scriptName);
  const rise = script >= now;
  return (
    <span class={`cb-bar${l.clamped ? " off" : ""}`} role="img" aria-label={l.text} title={l.text}>
      <span class="cb-bar-track" />
      <span class="cb-bar-mid" style={{ left: "50%" }} />
      <span class={`cb-bar-band ${rise ? "up" : "down"}`} style={{ left: `${(lo * 100).toFixed(2)}%`, width: `${((hi - lo) * 100).toFixed(2)}%` }} />
      <span class="cb-bar-now" style={{ left: `${(a * 100).toFixed(2)}%` }} />
      <span class="cb-bar-script" style={{ left: `${(b * 100).toFixed(2)}%` }} />
      {l.clamped ? <span class="cb-bar-off small">off scale</span> : null}
    </span>
  );
}
