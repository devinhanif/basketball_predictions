import { LEARNED } from "../learned";
import { BUILDS } from "../build";

const Tile = (p: { href: string; title: string; text: string; tag: string }) => (
  <a class="panel st-tile" href={p.href}>
    <span class="label">{p.tag}</span>
    <h2>{p.title}</h2>
    <p class="muted">{p.text}</p>
  </a>
);

export function StudioPage() {
  return (
    <div class="stack">
      <div class="page-head">
        <h1>Analysis studio</h1>
        <p class="muted">Tools for looking at basketball and at how well our forecasts do. Analysis only, not advice.</p>
      </div>
      <Tile
        href="#/court"
        tag="Court"
        title="Shot charts and replay"
        text="Where every shot was taken, who made it, and a step-by-step replay of a game. Four real games for now."
      />
      <Tile
        href="#/builds"
        tag="Players"
        title="Player builds"
        text={`A character sheet per player: level, skills, shooting zones, and a range for what comes next. ${BUILDS.cards.length.toString()} players for now.`}
      />
      <Tile
        href="#/learned"
        tag="Results"
        title="What we learned"
        text={`${LEARNED.length.toString()} tested ideas that moved something, each with its sample size and interval, plus where the market beat us.`}
      />
      <p class="small muted">
        Video: we link to the official NBA.com game page and nothing else. We do not copy, host or embed NBA video.
      </p>
    </div>
  );
}
