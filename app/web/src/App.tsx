import { useEffect, useState } from "preact/hooks";
import { Shell } from "./Shell";
import { useRoute } from "./router";
import { Game } from "./pages/Game";
import { Leg } from "./pages/Leg";
import { ParlayPage } from "./pages/Parlay";
import { Picks } from "./pages/Picks";
import { ComboPage } from "./pages/Combo";
import { Swipe } from "./pages/Swipe";
import { Teams } from "./pages/Teams";
import { Props } from "./pages/Props";
import { Settings } from "./pages/Settings";
import { Signals } from "./pages/Signals";
import { Hedge } from "./pages/Hedge";
import { History } from "./pages/History";
import { Alerts } from "./pages/Alerts";
import { Admin } from "./pages/Admin";
import { Welcome } from "./Welcome";
import { Record } from "./pages/Record";
import { Live } from "./pages/Live";
import { Terms } from "./pages/Terms";
import { SourcePage } from "./Provenance";
import { ScorecardPage } from "./pages/Scorecard";
import { TestsPage } from "./pages/Tests";
import { ErrorHelp } from "./pages/ErrorHelp";
import { Tour } from "./Tour";
import { MarketOnly } from "./Lens";
import { useLens } from "./prefs";
import { Today } from "./pages/Today";
import { IS_ARTIFACT } from "./env";
import { NotFound } from "./Problem";
import { Boundary } from "./Boundary";
import { useMetaLine } from "./metaWatch";
import { BETTING } from "./features";
import { renderBet } from "./betting";

export function App() {
  const route = useRoute();
  const market = useLens() === "market";
  const [stale, setStale] = useState(false);

  const meta = useMetaLine();
  useEffect(() => {
    if (meta.fromCache) setStale(true);
  }, [meta.fromCache]);

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
    case "teams":
      page = <Teams date={route.date} onStale={setStale} />;
      break;
    case "props":
      page = <Props date={route.date} game={route.game} onStale={setStale} />;
      break;
    case "picks":
      page = <Picks />;
      break;
    case "parlay":
      page = <ParlayPage date={route.date} />;
      break;
    case "combo":
      page = <ComboPage date={route.date} />;
      break;
    case "slip":
      page = <ComboPage date={route.date} view="slip" />;
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
    case "live":
      page = <Live key={route.id ?? "list"} id={route.id} date={route.date} />;
      break;
    case "alerts":
      page = <Alerts />;
      break;
    case "admin":
      page = <Admin />;
      break;
    case "terms":
      page = <Terms term={route.term} q={route.q} />;
      break;
    case "source":
      page = <SourcePage gameId={route.gameId} playerId={route.playerId} stat={route.stat} />;
      break;
    case "scorecard":
      page = <ScorecardPage />;
      break;
    case "tests":
      page = <TestsPage />;
      break;
    case "help":
      page = <ErrorHelp code={route.code} q={route.q} />;
      break;
    case "notfound":
      page = <NotFound hash={route.hash} />;
      break;
    case "bet":
      // The router parses a betting address only with the betting switch on; this line drops out of a build with it off.
      page = BETTING ? renderBet(route) : null;
      break;
    default:
      page = <Today date={route.date} onStale={setStale} />;
  }
  return (
    <Boundary outer>
      <Shell route={route} meta={meta} offline={stale}>
        {route.name === "combo" || route.name === "slip" ? null : <Welcome deep={route.name !== "today"} />}
        <Boundary resetKey={JSON.stringify(route)}>{page}</Boundary>
      </Shell>
      {IS_ARTIFACT ? null : <Tour />}
    </Boundary>
  );
}
