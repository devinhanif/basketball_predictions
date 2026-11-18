import { useMemo, useState } from "preact/hooks";
import { api, type Loaded } from "../api";
import { View } from "../components";
import { longDate, stamp, tipTime } from "../format";
import { setPref, useLens, useMode } from "../prefs";
import { StateBlock, Tabs, TabPanel, PageHead, Card } from "../ui";
import { useLoad } from "../useLoad";
import type { ComboResultState } from "../api";
import { CLOSED_SENTENCE, matchup, type Ctx } from "../combo/Bits";
import { BuildTab } from "../combo/ComboBuild";
import { LiftTab } from "../combo/ComboLift";
import { ScriptTab } from "../combo/ComboScript";
import { TargetTab } from "../combo/ComboTarget";
import { hasTipped, postedLineCount } from "../combo/script";
import { statEntries } from "../combo/engine";
import type { ComboGame } from "../combo/types";

type TabKey = "script" | "lift" | "target" | "build";

const TABS: { key: TabKey; label: string }[] = [
  { key: "script", label: "If the game goes like this" },
  { key: "lift", label: "Lines that move together" },
  { key: "target", label: "Aim for a chance" },
  { key: "build", label: "Build your own" },
];

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

/** Learn lens: combo mode is an Explore screen. */
function ExploreOnly() {
  return (
    <section class="panel empty market-only" aria-labelledby="mo-combo-mode">
      <h1 id="mo-combo-mode" class="market-only-title">
        Combo mode is an Explore screen
      </h1>
      <p>It lets you move around the game's possible scripts and build your own combos, so it stays out of view in the Learn lens.</p>
      <button
        type="button"
        class="btn primary"
        onClick={() => {
          setPref("lens", "explore");
        }}
      >
        Switch to Explore
      </button>
      <p class="small muted">Nothing is lost by switching. You can switch back from the top of Discover.</p>
    </section>
  );
}

function Body(props: { night: Night }) {
  const { night } = props;
  const [gid, setGid] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("script");
  const analyst = useMode() === "analyst";
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
  const withLines = games.filter((g) => postedLineCount(g) > 0).length;
  const nStats = games.reduce((n, g) => n + g.players.reduce((m, p) => m + statEntries(p).length, 0), 0);
  const nPosted = games.reduce((n, g) => n + postedLineCount(g), 0);
  const tipped = hasTipped(game, ctx.clock);
  const madeAt = games.map((g) => g.made_at).sort().at(-1);
  return (
    <div class={`stack cb-page ${analyst ? "cb-term" : "cb-simple"}`}>
      {ctx.payload.fixture ? (
        <p class="panel cb-fixture" role="note">
          <strong>Fixture night.</strong> {ctx.payload.fixture.label}
        </p>
      ) : null}
      <section class="panel cb-intro" aria-label="About combo mode">
        <p>
          Pure probability. Every chance on this screen is a calibrated chance from the model for a game that has not tipped, or an estimate built from
          those. The only market number used is the posted line, as a number.
        </p>
        <p class="small muted">
          {longDate(night.date)} · forecasts made {madeAt ? stamp(madeAt) : "at an unknown time"} · {games.length.toString()} game{games.length === 1 ? "" : "s"}.
          Only each player's posted line is used, and only when it is inside the range the calibration was checked on. Other lines are left out until their calibration is tested.
        </p>
        <p class="small" role="note">
          {nPosted === 0 ? (
            <strong>No game in this file has a posted line yet, so there is nothing to show. Check back closer to tip-off.</strong>
          ) : (
            <>
              Posted lines are in <span class="num">{withLines.toString()}</span> of <span class="num">{games.length.toString()}</span> games (<span class="num">{nPosted.toString()}</span> of{" "}
              <span class="num">{nStats.toString()}</span> player stats). A game with none shows that plainly.
            </>
          )}
        </p>
      </section>
      <section class="panel cb-pick" aria-label="Pick a game">
        <label class="field grow">
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
                {hasTipped(g, ctx.clock) ? " (tipped)" : ""}
                {postedLineCount(g) === 0 ? " (no posted lines yet)" : ""}
              </option>
            ))}
          </select>
        </label>
      </section>
      <Tabs label="Combo mode views" tabs={TABS} value={tab} onChange={setTab} idBase="cb" />
      <TabPanel idBase="cb" value={tab}>
        {tab === "build" ? (
          <BuildTab ctx={ctx} game={game} />
        ) : tipped ? (
          <Card tone="empty">
            <p role="note">
              <strong>{matchup(ctx, game)} has tipped.</strong> {CLOSED_SENTENCE}
            </p>
          </Card>
        ) : tab === "script" ? (
          <ScriptTab ctx={ctx} game={game} key={game.game_id} />
        ) : tab === "lift" ? (
          <LiftTab ctx={ctx} game={game} key={game.game_id} />
        ) : (
          <TargetTab ctx={ctx} game={game} key={game.game_id} />
        )}
      </TabPanel>
    </div>
  );
}

export function ComboPage(props: { date: string | null }) {
  const lens = useLens();
  const [state, reload] = useLoad(() => loadNight(props.date), [props.date]);
  if (lens === "learn") return <ExploreOnly />;
  return (
    <div class="stack">
      <PageHead
        kicker="Explore"
        title="Combo mode"
        description="If the game goes like this, which lines get more likely and which get less. Probability only, before tip-off."
      />
      <View state={state} reload={reload} what="the combo file" errorLevel={2}>
        {(night) => <Body night={night} />}
      </View>
    </div>
  );
}
