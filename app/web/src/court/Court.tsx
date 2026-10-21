import {
  HOOP,
  LINES,
  VIEWBOX,
  ZONES,
  ZONE_LABEL,
  ZONE_PATH,
  nearestShot,
  sy,
  toCourt,
  zoneShade,
  type ShotRow,
  type ZoneStat,
} from "./geometry";

/** Half-court shot chart. Pure SVG, no dependency. Markers differ in shape as well as colour (made = disc, missed = cross),
 *  so the chart reads without colour and without hover: tap anywhere to pick the nearest shot. */
export function Court(props: {
  shots: ShotRow[];
  zones: ZoneStat[];
  selected: number;
  /** Replay mode: shots after this index are hidden. */
  upTo: number | null;
  onPick: (i: number) => void;
  label: string;
}) {
  const { shots, selected, upTo } = props;
  const visible = upTo === null ? shots.length : Math.min(shots.length, upTo + 1);
  const onTap = (e: MouseEvent): void => {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const [x, y] = toCourt(e.clientX - box.left, e.clientY - box.top, box.width, box.height);
    const i = nearestShot(shots.slice(0, visible), x, y, 40);
    if (i >= 0) props.onPick(i);
  };
  const sel = selected >= 0 && selected < visible ? shots[selected] : undefined;
  return (
    <svg class="court" viewBox={VIEWBOX} role="img" aria-label={props.label} onClick={onTap}>
      <g class="court-zones">
        {ZONES.map((z, i) => {
          const zs = props.zones[i];
          const o = zs ? zoneShade(zs) : 0;
          return <path key={z} d={ZONE_PATH[z]} fill-rule="evenodd" class="zone-fill" style={{ fillOpacity: o }} />;
        })}
      </g>
      <g class="court-lines" fill="none">
        {LINES.map((d) => (
          <path key={d} d={d} />
        ))}
        <circle cx={HOOP.cx} cy={HOOP.cy} r={HOOP.r} />
      </g>
      <g class="court-zone-text" aria-hidden="true">
        {ZONES.map((z, i) => {
          const zs = props.zones[i];
          const [x, y] = ZONE_LABEL[z];
          if (!zs) return null;
          return (
            <text key={z} x={x} y={sy(y)} text-anchor="middle">
              {zs.att === 0 ? "" : zs.att < 5 ? `n=${zs.att.toString()}` : `${Math.round((zs.made / zs.att) * 100).toString()}%`}
            </text>
          );
        })}
      </g>
      {sel ? <line class="court-ray" x1={0} y1={sy(0)} x2={sel.loc_x} y2={sy(sel.loc_y)} /> : null}
      <g aria-hidden="true">
        {shots.slice(0, visible).map((s, i) => {
          const cx = s.loc_x;
          const cy = sy(s.loc_y);
          const cls = `shot ${s.made ? "made" : "miss"}${i === selected ? " on" : ""}${upTo !== null && i === upTo ? " fresh" : ""}`;
          return s.made ? (
            <circle key={`${s.game_id}-${s.action_number.toString()}`} class={cls} cx={cx} cy={cy} r={i === selected ? 12 : 8.5} />
          ) : (
            <path key={`${s.game_id}-${s.action_number.toString()}`} class={cls} d={`M${(cx - 7.5).toString()} ${(cy - 7.5).toString()}l15 15m0 -15l-15 15`} />
          );
        })}
      </g>
      {sel ? <circle class="court-halo" cx={sel.loc_x} cy={sy(sel.loc_y)} r={19} /> : null}
    </svg>
  );
}
