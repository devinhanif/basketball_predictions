import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { Shell } from "./components";
import { useRoute } from "./router";
import { Game } from "./pages/Game";
import { Leg } from "./pages/Leg";
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
        setNote("A replay of real 2024-25 games, not tonight's slate. Nothing here is advice.");
        if (m.fromCache) setStale(true);
      },
      () => {
        setNote("A replay of real 2024-25 games, not tonight's slate. The data server is not answering.");
      },
    );
  }, []);

  let page;
  switch (route.name) {
    case "game":
      page = <Game id={route.id} onStale={setStale} />;
      break;
    case "leg":
      page = (
        <Leg
          key={`${route.gameId}-${route.playerId.toString()}-${route.stat}`}
          gameId={route.gameId}
          playerId={route.playerId}
          stat={route.stat}
          side={route.side}
          onStale={setStale}
        />
      );
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
    default:
      page = <Today date={route.date} onStale={setStale} />;
  }
  return (
    <Shell route={route} replayNote={note} offline={stale}>
      {page}
    </Shell>
  );
}
