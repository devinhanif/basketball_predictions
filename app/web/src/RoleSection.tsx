// The game page's role section for one player: steadiness, on/off and out teammates, folded
// under the player's name so the forecasts stay first.
import { MatesBlock, OnOffBlock, SteadyBlock } from "./RoleCards";
import type { PlayerRole } from "./roleTypes";

export function RoleSection({ role, player }: { role: PlayerRole; player: string }) {
  return (
    <details class="role-section">
      <summary>
        <span class="role-sum">Role, on/off and teammates out</span>
        <span class="small muted"> (descriptive, from games before tonight)</span>
      </summary>
      <SteadyBlock s={role.steadiness} />
      <OnOffBlock o={role.onoff} player={player} />
      <MatesBlock mates={role.teammates_out} player={player} />
    </details>
  );
}
