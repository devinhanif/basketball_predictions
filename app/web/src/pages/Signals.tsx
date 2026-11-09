import { useMemo, useState } from "preact/hooks";
import { Avatar } from "../Avatar";
import { CertaintyChip, DeskGate, SectionNote } from "../DeskUI";
import { correctionLinks, newestFirst, type Desk, type DeskObservation } from "../desk";
import { longDate } from "../format";
import { cents } from "../desk";
import { pct } from "../format";
import { statLabelOf } from "./DiscoverParts";
import { Icon } from "../icons";

const KIND_WORD: Record<string, string> = {
  foul: "Foul trouble", rotation: "Rotation", halftime: "Halftime", exit: "Exit warning", correction: "Correction", risk: "Risk", other: "Note",
};
const PAGE = 20;

function Observation(props: { o: DeskObservation; links: ReturnType<typeof correctionLinks> }) {
  const { o, links } = props;
  const by = links.correctedBy.get(o.id);
  const target = links.target(o);
  return (
    <article class={`obs ${o.certainty}`}>
      <div class="obs-top">
        <span class="chip kind">{KIND_WORD[o.kind] ?? "Note"}</span>
        <CertaintyChip o={o} />
        {by ? <span class="chip revised">Corrected</span> : null}
      </div>
      <div class="obs-where small muted num">
        {[o.matchup, o.when_text, longDate(o.slate_date)].filter(Boolean).join(" · ")}
      </div>
      <h3>{o.title}</h3>
      <p class={o.text ? "" : "muted"}>{o.text ?? "No further text was stored; the title is the stored description."}</p>
      {o.certainty === "inference" ? (
        <p class="small muted">An inference is context, not a confirmed fact and not a priced betting edge.</p>
      ) : null}
      {target ? (
        <p class="small linked">
          <strong>Corrects:</strong> {target.title} <span class="muted num">({longDate(target.slate_date)})</span>. The original stays on record.
        </p>
      ) : null}
      {by ? (
        <p class="small linked">
          <strong>Corrected by:</strong> {by.title}
        </p>
      ) : null}
    </article>
  );
}

function Body({ desk }: { desk: Desk }) {
  const dates = useMemo(() => [...new Set(desk.observations.map((o) => o.slate_date))].sort().reverse(), [desk]);
  const [date, setDate] = useState<string | null>(dates[0] ?? null);
  const [shown, setShown] = useState(PAGE);
  const links = useMemo(() => correctionLinks(desk.observations), [desk]);
  const day = useMemo(() => newestFirst(desk.observations.filter((o) => o.slate_date === date)), [desk, date]);
  const featured = day.find((o) => o.certainty !== "correction") ?? day[0];
  const rest = day.filter((o) => o !== featured);
  const tally = {
    reported: day.filter((o) => o.certainty === "reported").length,
    inference: day.filter((o) => o.certainty === "inference").length,
    correction: day.filter((o) => o.certainty === "correction").length,
  };
  const games = new Set(day.map((o) => o.matchup).filter(Boolean)).size;
  const keep = useMemo(
    () =>
      desk.halftime
        .filter((h) => h.slate_date === date && h.edge_cents !== null && (featured?.matchup ? h.matchup === featured.matchup : true))
        .sort((a, b) => (b.edge_cents ?? 0) - (a.edge_cents ?? 0))
        .slice(0, 4),
    [desk, date, featured],
  );
  return (
    <div class="stack">
      <header class="page-head signals-head">
        <div class="kicker">Signal inbox</div>
        {date ? (
          <h1 class="answer">
            {day.length.toLocaleString("en-US")} observations on {longDate(date).replace(/, \d{4}$/, "")} · {tally.reported.toLocaleString("en-US")} reported,{" "}
            {tally.inference.toLocaleString("en-US")} inferred, {tally.correction.toLocaleString("en-US")} corrections
          </h1>
        ) : (
          <h1 class="answer">No observations are stored yet</h1>
        )}
        <p class="muted answer-src">
          What the desk noticed in games, from its stored in-game flags. {games > 0 ? `${games.toString()} game${games === 1 ? "" : "s"} that night.` : ""}
        </p>
        <nav class="tabs" aria-label="Choose a night">
          {dates.slice(0, 8).map((d) => (
            <button
              key={d}
              type="button"
              class="tab"
              aria-current={d === date ? "true" : undefined}
              onClick={() => {
                setDate(d);
                setShown(PAGE);
              }}
            >
              {longDate(d).replace(/, \d{4}$/, "")}
            </button>
          ))}
        </nav>
      </header>
      {desk.observations.length === 0 ? (
        <section class="panel empty">
          <h2>Nothing noticed yet</h2>
          <p class="muted">The desk has stored no observations. This is a normal state before games.</p>
          <SectionNote desk={desk} k="observations.names" />
        </section>
      ) : null}
      {featured ? (
        <div class="two-col">
          <section class="panel feature" aria-label="Newest observation">
            <Observation o={featured} links={links} />
            <a class="btn" href="#/alerts">
              View alert context <Icon name="arrow" />
            </a>
          </section>
          <section class="panel court" aria-label="The court">
            <div class="label">The court</div>
            <p class="court-match num">{featured.matchup ?? "No game stored"}</p>
            <p class="muted small">{featured.when_text ?? "Time of game not stored"}</p>
            <h2 class="keep-h">Props to keep in view</h2>
            <p class="small muted">Unproven model chances at stored prices. No stake suggested.</p>
            {keep.length === 0 ? (
              <p class="muted small">No priced props are stored for this game. {desk.section_notes["halftime"] ?? ""}</p>
            ) : (
              <ul class="keep-list">
                {keep.map((h) => (
                  <li key={`${h.game_id}-${h.player_id ?? 0}-${h.stat}-${h.threshold.toString()}`}>
                    <Avatar name={h.player_name ?? "Unnamed"} playerId={h.player_id} size="sm" />
                    <span class="keep-name">
                      <strong>{h.player_name ?? `Unnamed player (id ${(h.player_id ?? 0).toString()})`}</strong>
                      <span class="muted small">
                        {h.threshold.toString()}+ {statLabelOf(h.stat)}
                      </span>
                    </span>
                    <span class="num keep-nums">
                      <b>{h.model_prob !== null ? pct(h.model_prob) : "-"}</b>
                      <span class="muted small">{h.price_cents !== null ? `${cents(h.price_cents)} ask` : "no price stored"}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <a class="btn" href="#/hedge">
              Explore the position lab <Icon name="arrow" />
            </a>
          </section>
        </div>
      ) : null}
      <section class="obs-list" aria-label="Observations">
        {rest.slice(0, shown).map((o) => (
          <div class="panel" key={o.id}>
            <Observation o={o} links={links} />
          </div>
        ))}
        {rest.length > shown ? (
          <button
            type="button"
            class="btn"
            onClick={() => {
              setShown((n) => n + PAGE);
            }}
          >
            Show more ({(rest.length - shown).toLocaleString("en-US")} left)
          </button>
        ) : null}
      </section>
      <p class="small muted">
        Reported fact: what the stored flag says happened. Inference: what it might mean for minutes. Correction: a later flag that reverses an earlier one; both stay linked.
      </p>
    </div>
  );
}

export function Signals() {
  return <DeskGate title="Signals">{(desk) => <Body desk={desk} />}</DeskGate>;
}
