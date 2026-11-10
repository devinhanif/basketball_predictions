import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { ageNote, stamp } from "./format";
import { Shell } from "./Shell";
import { useRoute } from "./router";
import { Game } from "./pages/Game";
import { Leg } from "./pages/Leg";
import { ParlayPage } from "./pages/Parlay";
import { Picks } from "./pages/Picks";
import { Swipe } from "./pages/Swipe";
import { Settings } from "./pages/Settings";
import { Signals } from "./pages/Signals";
import { Hedge } from "./pages/Hedge";
import { History } from "./pages/History";
import { Alerts } from "./pages/Alerts";
import { Admin } from "./pages/Admin";
import { Welcome } from "./Welcome";
import { Record } from "./pages/Record";
import { Tour } from "./Tour";
import { MarketOnly } from "./Lens";
import { useLens } from "./prefs";
import { openTour, tourSeen } from "./tourState";
import { hasWelcomed } from "./identity";
import { Today } from "./pages/Today";
import { IS_ARTIFACT } from "./env";

export function App() {
  const route = useRoute();
  const market = useLens() === "market";
  const [stale, setStale] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    // First visit: the tour opens once (after the welcome, which opens it itself when it finishes).
    if (!IS_ARTIFACT && hasWelcomed() && !tourSeen()) openTour();
  }, []);

  useEffect(() => {
    api.meta().then(
      (r) => {
        const m = r.data;
        setNote(
          m.mode === "fixture_replay"
            ? "A replay of real 2024-25 games, not tonight's slate. Nothing here is advice."
            : `${IS_ARTIFACT ? "A saved snapshot of the nightly export; it does not update. " : ""}Data as of ${stamp(m.data_as_of)}. ${ageNote(m.data_as_of) ?? ""} ${m.mode_note}`.replace(/\s{2,}/g, " "),
        );
        if (r.fromCache) setStale(true);
      },
      () => {
        setNote("The data server is not answering, so what you see may be old. Nothing here is advice.");
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
      page = <ParlayPage date={route.date} />;
      break;
    case "settings":
      page = <Settings />;
      break;
    case "signals":
      page = market ? <Signals /> : <MarketOnly name="Signals" what="Signals ranks stored contracts on the market side." />;
      break;
    case "hedge":
      page = market ? (
        <Hedge game={route.game} player={route.player} stat={route.stat} />
      ) : (
        <MarketOnly name="Hedge Finder" what="The Hedge Finder works on the market side." />
      );
      break;
    case "history":
      page = market ? <History /> : <MarketOnly name="Model History" what="Model History is the paper book of market plays." />;
      break;
    case "record":
      page = <Record tab={route.tab} date={route.date} />;
      break;
    case "alerts":
      page = <Alerts />;
      break;
    case "admin":
      page = <Admin />;
      break;
    default:
      page = <Today date={route.date} onStale={setStale} />;
  }
  return (
    <>
      <Shell route={route} replayNote={note} offline={stale}>
        {page}
      </Shell>
      <Welcome />
      {IS_ARTIFACT ? null : <Tour />}
    </>
  );
}
