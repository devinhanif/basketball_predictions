import { useMemo, useState } from "preact/hooks";
import { api, type Loaded } from "../api";
import { View } from "../components";
import { longDate, tipTime } from "../format";
import { useLens, useMode } from "../prefs";
import { StateBlock, Tabs, TabPanel, PageHead, Card } from "../ui";
import { useLoad } from "../useLoad";
import { Door, DoorGroup } from "../Doors";
import { JOBS } from "../jobs";
import { propsHref, topHref } from "../router";
import type { ComboResultState } from "../api";
import { closedSentence, matchup, noLinesSentence, type Ctx } from "../combo/Bits";
import { BuildTab } from "../combo/ComboBuild";
import { LiftTab } from "../combo/ComboLift";
import { ScriptTab, type Sub } from "../combo/ComboScript";
import { TargetTab } from "../combo/ComboTarget";
import { SEPARATE_HISTORY, hasTipped, postedLineCount } from "../combo/script";
import type { ComboGame } from "../combo/types";

type TabKey = "script" | "lift" | "target" | "build";

/** The two faces of the combo file: EXPLORE (the scripts and the target search) and SLIP (lines that move together, and your own build). */
export type ComboView = "explore" | "slip";
const TABS_BY_VIEW: Record<ComboView, { key: TabKey; label: string }[]> = {
  explore: [
    { key: "script", label: "Scripts" },
    { key: "target", label: "Target" },
  ],
  slip: [
    { key: "lift", label: "Together" },
    { key: "build", label: "Build" },
  ],
};
const FIRST_TAB: Record<ComboView, TabKey> = { explore: "script", slip: "lift" };

interface Night {
  date: string | null;
  state: ComboResultState | null;
  teams: ReadonlyMap<number, string>;
}

/** The night's combo file and, when the app has the slate, the team abbreviations (the file carries team ids only). */
async function loadNight(date: string | null): Promise<Loaded<Night>> {
  let d = date;
  if (d === null) {
    // The newest night that has a combo file; with none, the newest night the app holds (its page says the file is absent).
    const have = await api.comboNights().then((r) => r.data.nights, () => [] as string[]);
    d = have.at(-1) ?? null;
  }
  if (d === null) {
    const slates = await api.slates();
    d = slates.data.map((s) => s.date).sort().at(-1) ?? null;
  }
  if (d === null) return { data: { date: null, state: null, teams: new Map() }, fromCache: false };
  const combo = await api.combo(d);
  const teams = new Map<number, string>();
  try {
    const slate = await api.slate(d);
    for (const g of slate.data.games) {
      teams.set(g.home.id, g.home.abbr);
      teams.set(g.away.id, g.away.abbr);
    }
  } catch {
    /* the file's own team ids are shown instead */
  }
  return { data: { date: d, state: combo.data, teams }, fromCache: combo.fromCache };
}

function Body(props: { night: Night; view: ComboView }) {
  const { night, view } = props;
  const [gid, setGid] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>(FIRST_TAB[view]);
  const analyst = useMode() === "analyst";
  const [sub, setSub] = useState<Sub>("model");
  const st = night.state;
  const env = st?.kind === "ok" ? st.data : null;
  const ctx: Ctx | null = useMemo(
    () => (env === null ? null : { payload: env.combo, clock: env.clock_utc, teams: night.teams }),
    [env, night.teams],
  );
  if (night.date === null || st === null) {
    return (
      <StateBlock kind="empty" title="No night to show yet">
        The app holds no nights, so there is no combo file to read. Nothing is shown in its place.
      </StateBlock>
    );
  }
  if (st.kind === "missing") {
    return (
      <StateBlock kind="empty" title={`No combo file for ${longDate(night.date)}`}>
        The combo job has not written a file for this night, so combo mode has nothing to work from. We show nothing rather than a sample. Pick another night
        from Discover or check back before tip-off.
      </StateBlock>
    );
  }
  if (st.kind === "refused") {
    return (
      <StateBlock kind="empty" title="The combo file failed its checks">
        The file for {longDate(night.date)} did not match its contract, so it was refused rather than shown. Reason: {st.detail}
      </StateBlock>
    );
  }
  if (ctx === null || env === null) return null;
  const games = ctx.payload.games;
  const first = games[0];
  if (first === undefined) {
    return (
      <StateBlock kind="empty" title="No games in this file">
        The combo file for {longDate(night.date)} lists no games.
      </StateBlock>
    );
  }
  const game: ComboGame =
    games.find((g) => g.game_id === gid) ?? games.find((g) => !hasTipped(g, ctx.clock) && postedLineCount(g) > 0) ?? games.find((g) => !hasTipped(g, ctx.clock)) ?? first;
  const tipped = hasTipped(game, ctx.clock);
  return (
    <div class={`stack cb-page cb-term ${analyst ? "cb-dense" : "cb-simple"}`}>
      {ctx.payload.fixture ? (
        <p class="panel cb-fixture" role="note" title={ctx.payload.fixture.label}>
          <strong>Fixture night:</strong> synthetic numbers, not forecasts.
        </p>
      ) : null}
      <div class="cb-pick">
        <label class="field">
          <span>Game</span>
          <select
          value={game.game_id}
          onChange={(e) => {
            setGid(e.currentTarget.value);
          }}
        >
          {games.map((g) => (
            <option key={g.game_id} value={g.game_id}>
              {matchup(ctx, g)} · {tipTime(g.tip_utc)}
              {hasTipped(g, ctx.clock) ? " (tipped)" : postedLineCount(g) === 0 ? " (no posted lines)" : ""}
            </option>
          ))}
          </select>
        </label>
        {SEPARATE_HISTORY && tab === "script" && !tipped && postedLineCount(game) > 0 ? (
          <div class="seg cb-sub" role="group" aria-label="Which view">
            <button type="button" class="seg-btn" aria-pressed={sub === "model"} onClick={() => { setSub("model"); }}>
              Model
            </button>
            <button type="button" class="seg-btn" aria-pressed={sub === "history"} onClick={() => { setSub("history"); }}>
              History
            </button>
          </div>
        ) : null}
      </div>
      <Tabs label={view === "slip" ? "Slip check views" : "Combo mode views"} tabs={TABS_BY_VIEW[view]} value={tab} onChange={setTab} idBase="cb" class="cb-tabs" />
      <TabPanel idBase="cb" value={tab}>
        {tab === "build" ? (
          <BuildTab ctx={ctx} game={game} />
        ) : tipped ? (
          <Card tone="empty">
            <p role="note">{closedSentence(matchup(ctx, game))}</p>
          </Card>
        ) : postedLineCount(game) === 0 ? (
          <Card tone="empty">
            <p role="note">{noLinesSentence(matchup(ctx, game))}</p>
          </Card>
        ) : tab === "script" ? (
          <ScriptTab ctx={ctx} game={game} sub={sub} key={game.game_id} />
        ) : tab === "lift" ? (
          <LiftTab ctx={ctx} game={game} key={game.game_id} />
        ) : (
          <TargetTab ctx={ctx} game={game} key={game.game_id} />
        )}
      </TabPanel>
    </div>
  );
}

export function ComboPage(props: { date: string | null; view?: ComboView }) {
  const view = props.view ?? "explore";
  const lens = useLens();
  const market = lens === "market";
  const [state, reload] = useLoad(() => loadNight(props.date), [props.date]);
  return (
    <div class="stack">
      {view === "slip" ? (
        <PageHead
          kicker="Slip check"
          title="Do these legs hold together?"
          description="Lines from the same game move together, and the chance of all of them is not the product of the single chances."
          class="cb-head"
        />
      ) : (
        <PageHead kicker="Explore" title="Combo mode" description="Lines that move if the game goes a certain way." class="cb-head" />
      )}
      <DoorGroup label="Go to another screen">
        {view === "slip" ? (
          <>
            <Door to="explore" href={topHref("combo")}>
              what changes if the game goes a certain way
            </Door>
            <Door to="props" href={propsHref()}>
              pick the lines for a slip
            </Door>
            {market ? (
              <Door to="slip" href="#/parlay" label="Parlay of the day, market side">
                parlay of the day (market side)
              </Door>
            ) : null}
            {market ? (
              <Door to="slip" href="#/hedge" label="Hedge Finder, market side">
                hedge finder (market side)
              </Door>
            ) : null}
          </>
        ) : (
          <>
            <Door to="slip" href={JOBS.slip.href}>
              do these legs hold together?
            </Door>
            <Door to="props" href={propsHref()}>
              the lines in tonight's games
            </Door>
          </>
        )}
      </DoorGroup>
      <View state={state} reload={reload} what="the combo file" errorLevel={2}>
        {(night) => <Body night={night} view={view} />}
      </View>
    </div>
  );
}
