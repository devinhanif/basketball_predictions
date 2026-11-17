/** Placeholder blocks while a page loads. With a label it announces itself as busy; without, the caller does. */
export function Skeleton(props: { lines?: number; label?: string; tiles?: number }) {
  const lines = props.lines ?? 2;
  return (
    <div class="kskel" aria-busy={props.label ? "true" : undefined} aria-label={props.label} aria-hidden={props.label ? undefined : "true"}>
      {props.tiles ? (
        <div class="tiles">
          {Array.from({ length: props.tiles }, (_, i) => (
            <div key={i} class="skeleton skel-tile" />
          ))}
        </div>
      ) : null}
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} class="skeleton" />
      ))}
    </div>
  );
}
