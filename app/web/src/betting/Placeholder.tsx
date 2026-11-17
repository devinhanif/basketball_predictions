// #/bet: the one screen the betting module has so far. It says what the module is and is not, and shows both halves of
// the switch: this build's (set when the page was built) and the API's (asked of GET /api/betting/status).
import { getJson } from "../api";
import { BETTING } from "../features";
import { useLoad } from "../useLoad";

export const PLACEHOLDER_SENTENCE = "Betting tools: analysis only, not advice, no orders are placed. Nothing is built here yet.";

interface BettingStatus {
  enabled: boolean;
  note: string;
}

function ApiSwitch() {
  const [state] = useLoad(() => getJson<BettingStatus>("/api/betting/status"), []);
  if (state.status === "loading") return <span class="muted">asking the API...</span>;
  if (state.status === "error") {
    const why = state.failure.status === 404 ? "off, or this page has no API (the snapshot page)" : "not answering";
    return (
      <span>
        {why} <span class="small muted mono">({state.failure.code})</span>
      </span>
    );
  }
  return (
    <span>
      {state.data.enabled ? "on" : "off"} <span class="small muted">({state.data.note})</span>
    </span>
  );
}

export function BetPlaceholder() {
  return (
    <section class="ph-bet">
      <h1>Betting tools</h1>
      <p>{PLACEHOLDER_SENTENCE}</p>
      <ul class="plain">
        <li>
          This build's switch: <strong>{BETTING ? "on" : "off"}</strong> <span class="small muted">(fixed when the page was built)</span>
        </li>
        <li>
          The API's switch: <ApiSwitch />
        </li>
      </ul>
    </section>
  );
}
