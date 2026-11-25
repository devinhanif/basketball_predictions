// The home screen's question and its four doors, then the three-line legend. Facts come from the night's stored counts;
// with none, the doors still show (a door without a fact is fine, a door with an invented fact is not).
import { BigDoor } from "./Doors";
import { JOBS } from "./jobs";

export interface HomeFacts {
  games: number | null;
  props: number | null;
  /** The stored slate's own word for a prop count ("player lines" in a replay, "player forecasts" otherwise). */
  propsWord: string;
}

const plural = (n: number, one: string, many: string): string => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

export function HomeDoors({ facts }: { facts: HomeFacts | null }) {
  return (
    <section class="home" aria-labelledby="home-q">
      <h2 id="home-q" class="home-q">
        <span class="home-caret" aria-hidden="true">
          &gt;
        </span>{" "}
        What are you here for?
      </h2>
      <div class="home-doors">
        <BigDoor job="teams" name="Team picks" sentence={JOBS.teams.sentence} fact={facts?.games != null ? `${plural(facts.games, "game", "games")} on this night` : null} />
        <BigDoor job="props" name="Player props" sentence={JOBS.props.sentence} fact={facts?.props != null ? `${facts.props.toLocaleString("en-US")} ${facts.props === 1 ? facts.propsWord.replace(/s$/, "") : facts.propsWord}` : null} />
        <BigDoor job="slip" name="Slip check" sentence={JOBS.slip.sentence} />
        <BigDoor job="explore" name="Explore" sentence={JOBS.explore.sentence} fact="Four scripts: blowout, close game, shootout, grind" />
      </div>
      <div class="home-legend" aria-label="How to read the colours">
        <div class="home-legend-title">How to read the colours</div>
        <p>
          <span class="lg-sw lg-updown" aria-hidden="true">
            <i class="up" />
            <i class="down" />
          </span>
          Green and red: a chance went up or down. Nothing else.
        </p>
        <p>
          <span class="lg-sw lg-team" aria-hidden="true" />A team's colours: on its players' photos and its team chips only.
        </p>
        <p>
          <span class="lg-sw lg-door" aria-hidden="true" />A coloured box is a door to that colour's screen. Its name is written on it.
        </p>
      </div>
    </section>
  );
}
