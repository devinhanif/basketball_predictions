/** Placeholder blocks while a page loads; announced once as busy, then silent. */
export function Skeleton(props: { lines?: number; label?: string; tiles?: number }) {
  const lines = props.lines ?? 2;
  return (
    <div class="kskel" aria-busy="true" aria-label={props.label ?? "Loading"}>
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
