import { initials } from "./identity";
import { usePhotos } from "./photos";
import { teamColor } from "./teams";

/** Initials on a team colour, or an uploaded photo when an editor added one. Never a stock photo or a logo; never text over a picture. */
export function Avatar(props: { name: string; team?: string | null; playerId?: number | null; size?: "sm" | "md" | "lg"; src?: string | null }) {
  const photos = usePhotos();
  const photo = props.src ?? (props.playerId !== null && props.playerId !== undefined ? (photos[props.playerId.toString()] ?? null) : null);
  const size = props.size ?? "md";
  if (photo) return <img class={`avatar ${size}`} src={photo} alt="" width={size === "lg" ? 72 : size === "sm" ? 28 : 40} height={size === "lg" ? 72 : size === "sm" ? 28 : 40} />;
  const bg = props.team ? teamColor(props.team) : "var(--surface-3)";
  return (
    <span class={`avatar initials ${size}${props.team ? " team" : ""}`} style={{ "--av": bg }} aria-hidden="true">
      {initials(props.name)}
    </span>
  );
}
