import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { Shell } from "./components";
import { useRoute } from "./router";
import { Game } from "./pages/Game";
import { Method } from "./pages/Method";
import { ParlayPage } from "./pages/Parlay";
import { Picks } from "./pages/Picks";
import { Swipe } from "./pages/Swipe";
import { Today } from "./pages/Today";

export function App() {
  const route = useRoute();
  const [stale, setStale] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.meta().then(
      (m) => {
        setNote(`Historical replay, not live: 2024-25 slates, data ${m.data.data_version}. Nothing here is advice.`);
        if (m.fromCache) setStale(true);
      },
      () => {
        setNote("Historical replay, not live. The API is unreachable.");
      },
    );
  }, []);

  let page;
  switch (route.name) {
    case "game":
      page = <Game id={route.id} onStale={setStale} />;
      break;
    case "swipe":
      page = <Swipe date={route.date} onStale={setStale} />;
      break;
    case "picks":
      page = <Picks />;
      break;
    case "parlay":
      page = <ParlayPage />;
      break;
    case "method":
      page = <Method onStale={setStale} />;
      break;
    default:
      page = <Today date={route.date} onStale={setStale} />;
  }
  return (
    <Shell route={route} replayNote={note} offline={stale}>
      {page}
    </Shell>
  );
}
