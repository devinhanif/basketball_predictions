import { useEffect, useState } from "preact/hooks";
import { OnThisPage, SoWhat } from "../ui";
import { api } from "../api";
import { Avatar } from "../Avatar";
import { ErrorPanel, Explain, Loading } from "../components";
import { failureMessage, failureOf, type ApiFailure } from "../errors";
import { IS_ARTIFACT } from "../env";
import { useLens, useMode, type Lens } from "../prefs";
import { PageControls } from "../Lens";
import { ExplorePanel } from "../Explore";
import { PlayerKnowsPanel } from "../Knows";
import { OnItChip } from "../SharedUI";
import { lineText, longDate, tipTime } from "../format";
import { ladderAt } from "../leg";
import { gameHref, legHref } from "../router";
import { LEG_SHORT, type LegBreakdown, type LegStat, type Side } from "../legTypes";
import { Headline, LineCard, NoHistory } from "./LegLine";
import { Books, Games, InWords, Ladder, ModelPanel } from "./LegHistory";
import { DistributionPanel } from "../Distribution";
import { LegPlaceholders } from "../NeedsFields";
import { AddToSlipSlot } from "../BetSlots";
import { EdgePanel, HeadsUp, Opponent, RolePanel, Splits, Teammates } from "./LegContext";

const S = (n: number): string => n.toString();

/* ------------------------------------------------------------------ the page */
function Header({ data, line }: { data: LegBreakdown; line: number }) {
  return (
    <header class="leg-title">
      <p class="back-row">
        <a class="back-link" href={gameHref(data.game_id)}>
          Back to the game
        </a>
      </p>
      <div class="leg-head-row">
        <h1 class="who">
          <span class="who-photo" aria-hidden="true">
            <Avatar name={data.player} team={data.team} playerId={data.player_id} size="lg" remote />
          </span>
          {data.player}
        </h1>
        <PageControls lens detail />
      </div>
      <SoWhat>
        {data.player}, {data.label.toLowerCase()} at {lineText(line)}: his recent games and the forecast are below. A forecast is a range, and nothing here is a pick.
      </SoWhat>
      <p class="muted num" style={{ margin: 0 }}>
        {data.team} {data.home ? "vs" : "at"} {data.opponent} · tip-off {tipTime(data.tip_utc)} · {data.label} {lineText(line)}
      </p>
      <nav class="leg-tabs" aria-label="Stat">
        {data.tabs.map((t) => (
          <a
            key={t.stat}
            class="leg-tab"
            href={legHref(data.game_id, data.player_id, t.stat, data.side)}
            aria-current={t.stat === data.stat ? "true" : undefined}
            aria-label={`${t.label}${t.has_model ? "" : ", no model forecast yet"}`}
          >
            {LEG_SHORT[t.stat]}
            {t.has_model ? null : <i class="leg-dot" aria-hidden="true" />}
          </a>
        ))}
      </nav>
      <p class="small muted" style={{ margin: "4px 0 0" }}>
        A dot means no model forecast yet for that stat.
      </p>
      <OnItChip propId={`${data.game_id}:${data.player_id.toString()}:${data.stat}`} />
    </header>
  );
}

/** The what-if lab, only for the four stats the model forecasts; any other stat says why there is none. */
function LensExplore(props: { data: LegBreakdown; modelStat: boolean; lens: Lens }) {
  if (IS_ARTIFACT) {
    return (
      <section class="panel flat" aria-label="What if">
        <h2>What if?</h2>
        <p class="small muted">The what-ifs need the live app. This snapshot holds the posted line only.</p>
      </section>
    );
  }
  if (!props.modelStat) {
    return (
      <section class="panel flat" aria-label="What if">
        <h2>What if?</h2>
        <p class="small muted">There is no model forecast for this stat, so there is nothing to move. The what-ifs cover points, rebounds, assists and threes.</p>
      </section>
    );
  }
  return <ExplorePanel gameId={props.data.game_id} playerId={props.data.player_id} stat={props.data.stat} lens={props.lens} />;
}

export function Leg(props: { gameId: string; playerId: number; stat: LegStat; side: Side; onStale: (s: boolean) => void }) {
  const [line, setLine] = useState<number | null>(null);
  const [side, setSide] = useState<Side>(props.side);
  const [data, setData] = useState<LegBreakdown | null>(null);
  const [bounds, setBounds] = useState<{ min: number; max: number } | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [pending, setPending] = useState(true);
  const [tick, setTick] = useState(0);
  const mode = useMode();
  const lens = useLens();
  const { onStale } = props;

  useEffect(() => {
    let live = true;
    setPending(true);
    const t = setTimeout(
      () => {
        api.breakdown(props.gameId, props.playerId, props.stat, line, side).then(
          (r) => {
            if (!live) return;
            const body = r.data as LegBreakdown | null;
            if (body === null || typeof body !== "object" || !Array.isArray(body.ladder)) {
              // An answer of the wrong shape is a failure on screen, not a spinner forever or a thrown error.
              setError(failureOf(new Error("the breakdown answer was not in the expected form")));
              setPending(false);
              return;
            }
            setData(r.data);
            setError(null);
            setPending(false);
            onStale(r.fromCache);
            const ladder = r.data.ladder;
            const first = ladder[0];
            const last = ladder[ladder.length - 1];
            if (first && last) setBounds((b) => b ?? { min: first.line, max: last.line });
          },
          (e: unknown) => {
            if (!live) return;
            setError(failureOf(e));
            setPending(false);
          },
        );
      },
      data === null ? 0 : 100,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [props.gameId, props.playerId, props.stat, line, side, tick]);

  const tabsReady = data !== null;
  useEffect(() => {
    // On a phone the stat strip scrolls sideways: keep the current stat in view.
    document.querySelector(".leg-tab[aria-current='true']")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [tabsReady, props.stat]);

  if (error && !data) {
    return (
      <ErrorPanel
        message={failureMessage(error)}
        failure={error}
        retry={() => {
          setTick((x) => x + 1);
        }}
      />
    );
  }
  if (!data || !bounds) return <Loading />;

  const shown = line ?? data.line;
  const row = ladderAt(data.ladder, shown);
  const fresh = !pending && data.line === shown && data.side === side;
  const market = lens === "market";
  const modelStat = data.model.available && (data.stat === "pts" || data.stat === "reb" || data.stat === "ast" || data.stat === "fg3m");
  const depth = (
    <>
      <Ladder data={data} line={shown} side={side} onLine={setLine} market={market} />
      {market ? <Books data={data} line={shown} /> : null}
      <Splits data={data} side={side} line={shown} />
      <Teammates data={data} side={side} line={shown} />
      <RolePanel data={data} />
      <Opponent data={data} />
    </>
  );
  return (
    <div class={`stack leg${fresh ? "" : " stale"}`} aria-busy={pending}>
      <Header data={data} line={shown} />
      <OnThisPage />
      {lens === "explore" ? <LensExplore data={data} modelStat={modelStat} lens={lens} /> : null}
      <LineCard
        data={data}
        line={shown}
        side={side}
        min={bounds.min}
        max={bounds.max}
        onLine={setLine}
        onSide={setSide}
      />
      <AddToSlipSlot gameId={data.game_id} playerId={data.player_id} stat={data.stat} side={side} line={shown} from="leg" />
      {data.prior_games === 0 ? (
        <>
          <NoHistory data={data} side={side} line={shown} row={row} market={market} />
          {market ? <Books data={data} line={shown} /> : null}
        </>
      ) : (
        <>
          <Headline data={data} row={row} side={side} line={shown} fresh={fresh} market={market} />
          {market ? <EdgePanel data={data} line={shown} /> : null}
          {market ? <LensExplore data={data} modelStat={modelStat} lens={lens} /> : null}
          <InWords data={data} />
          <HeadsUp data={data} />
          <Games data={data} side={side} line={shown} row={row} />
          <ModelPanel data={data} side={side} line={shown} row={row} />
          {modelStat ? <DistributionPanel gameId={data.game_id} playerId={data.player_id} stat={data.stat} line={shown} /> : null}
          <LegPlaceholders />
          {modelStat && !IS_ARTIFACT ? <PlayerKnowsPanel gameId={data.game_id} playerId={data.player_id} stat={data.stat} /> : null}
          {mode === "analyst" ? (
            depth
          ) : (
            <details class="panel more">
              <summary>More depth: other lines, {market ? "prices by book, " : ""}splits, teammates, role, opponent</summary>
              <div class="stack">{depth}</div>
            </details>
          )}
        </>
      )}
      {error ? (
        <p role="alert" class="small">
          Could not refresh: {error}{" "}
          <button
            type="button"
            class="linklike"
            onClick={() => {
              setTick((x) => x + 1);
            }}
          >
            Try again
          </button>
        </p>
      ) : null}
      <Explain label="How these numbers are made">
        <p>
          Everything here is counted from box scores of games before {longDate(data.as_of_date)}: no game on that night or later is
          used, so nothing leaks from the result. Hit means {side === "over" ? "above" : "below"} the line; landing exactly on a
          whole-number line is a push. A combo is the sum of its parts in each game. The range is a 95% interval for the true
          rate given {S(data.last10.n)} games.
        </p>
        <p>
          The model chance exists only for points, rebounds, assists and threes. Teammate effects compare his own games with
          that teammate out against games with him playing; they are small samples and can easily be noise. This is analysis, not
          advice.
        </p>
      </Explain>
    </div>
  );
}
