import { useEffect, useState } from "preact/hooks";

export type Route =
  | { name: "today"; date: string | null }
  | { name: "game"; id: string }
  | { name: "swipe"; date: string | null }
  | { name: "picks" }
  | { name: "parlay" }
  | { name: "method" };

export function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const q = new URLSearchParams(query);
  switch (parts[0]) {
    case "game":
      return parts[1] ? { name: "game", id: parts[1] } : { name: "today", date: null };
    case "swipe":
      return { name: "swipe", date: q.get("date") };
    case "picks":
      return { name: "picks" };
    case "parlay":
      return { name: "parlay" };
    case "method":
      return { name: "method" };
    default:
      return { name: "today", date: q.get("date") };
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const on = (): void => {
      setRoute(parse(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => {
      window.removeEventListener("hashchange", on);
    };
  }, []);
  return route;
}
