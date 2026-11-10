import { useState } from "preact/hooks";
import { initials } from "./identity";
import { headshotUrl } from "./headshot";
import { usePhotos } from "./photos";
import { teamColor } from "./teams";

const PX = { sm: 28, md: 40, lg: 72 } as const;

/**
 * A player's picture, or initials on a team colour.
 * Order: an editor's uploaded photo, then the NBA's public headshot for the player id (the browser loads it straight from
 * cdn.nba.com: nothing is copied, proxied or stored here), then initials. A headshot that fails to load becomes initials.
 */
export function Avatar(props: { name: string; team?: string | null; playerId?: number | null; size?: "sm" | "md" | "lg"; src?: string | null; remote?: boolean }) {
  const photos = usePhotos();
  const [failed, setFailed] = useState<number | null>(null);
  const id = props.playerId ?? null;
  const uploaded = props.src ?? (id !== null ? (photos[id.toString()] ?? null) : null);
  const size = props.size ?? "md";
  const px = PX[size];
  if (uploaded) return <img class={`avatar ${size}`} src={uploaded} alt="" width={px} height={px} />;
  const remote = props.remote === true && id !== null && failed !== id ? headshotUrl(id) : null;
  if (remote)
    return (
      <img
        class={`avatar headshot ${size}`}
        src={remote}
        alt={props.name}
        width={px}
        height={px}
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        onError={() => {
          setFailed(id);
        }}
      />
    );
  const bg = props.team ? teamColor(props.team) : "var(--surface-3)";
  return (
    <span class={`avatar initials ${size}${props.team ? " team" : ""}`} style={{ "--av": bg }} role="img" aria-label={props.name}>
      {initials(props.name)}
    </span>
  );
}
