import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "./api";
import { Icon, Logo } from "./icons";
import { LensSwitch, LENS_LABEL, LENS_SAY } from "./Lens";
import { LENSES, setPref } from "./prefs";
import { legHref, recordHref } from "./router";
import { closeTour, useTourOpen } from "./tourState";

interface Step {
  title: string;
  body: ComponentChildren;
  action?: { label: string; go: () => void; needs?: "leg" };
}

/** The newest night's first key prop, as a leg hash, so the tour can end on something real. Null when the data has none. */
async function demoLeg(): Promise<string | null> {
  const list = (await api.slates()).data;
  for (const s of list.slice(0, 3)) {
    const slate = (await api.slate(s.date)).data;
    for (const g of slate.games) {
      const k = g.key_props[0];
      if (k) return legHref(g.game_id, k.player_id, k.stat);
    }
  }
  return null;
}

function go(hash: string): void {
  closeTour();
  window.location.hash = hash;
}

/** First-visit tour: six short steps, skippable, replayable from the profile menu and About. */
export function Tour() {
  const open = useTourOpen();
  const [i, setI] = useState(0);
  const [leg, setLeg] = useState<string | null>(null);
  const first = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setI(0);
    let live = true;
    demoLeg().then(
      (h) => {
        if (live) setLeg(h);
      },
      () => {
        /* no demo player: the step simply has no button */
      },
    );
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") closeTour();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      live = false;
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  useEffect(() => {
    if (open) first.current?.focus();
  }, [open, i]);
  if (!open) return null;

  const steps: Step[] = [
    {
      title: "What this is",
      body: (
        <>
          <p>
            parlayhitters forecasts NBA games and player stats and shows its work. A forecast here is a <b>range</b>: a typical night and how far either side most nights land. It is not a
            single number and not a promise.
          </p>
          <p>
            It does not take bets, sell picks or tell you what to bet. Until the forecasts have a long enough record, the honest answer to "what should I
            bet?" is <b>keep your money</b>. A clear "nothing worth betting tonight" is a result, not an error.
          </p>
        </>
      ),
    },
    {
      title: "Pick a lens",
      body: (
        <>
          <p>Every game and player page has a lens switch. Try it here; it is the real one.</p>
          <LensSwitch compact />
          <ul class="plain tour-lenses">
            {LENSES.map((l) => (
              <li key={l}>
                <b>{LENS_LABEL[l]}.</b> {LENS_SAY[l]}
              </li>
            ))}
          </ul>
          <p class="small muted">New here? Stay on Learn. Your choice is remembered on this device.</p>
        </>
      ),
    },
    {
      title: "Move the numbers",
      body: (
        <>
          <p>
            In <b>Explore</b>, open any player and drag the line: the chance of over and under moves, and so does the whole spread of outcomes. Change his minutes to
            see what that does, and see roughly how much of the uncertainty is minutes.
          </p>
          <p class="small muted">The minutes what-if is an approximation (his per-minute rate held fixed) and it says so on the screen.</p>
        </>
      ),
      action: leg
        ? {
            label: "Try it on a real player",
            go: () => {
              setPref("lens", "explore");
              go(leg);
            },
          }
        : undefined,
    },
    {
      title: "What the game knew",
      body: (
        <p>
          A game or player page shows <b>what the game knew, and when</b>: the forecasts stored before tip and the injury reports, in time order, so you can see how a
          range tightened or moved as news arrived. The page also says in words why a player's range is wide: his minutes, his role, the opponent.
        </p>
      ),
    },
    {
      title: "Check the record",
      body: (
        <>
          <p>The Track record has three tabs, none of them with a price:</p>
          <ul class="plain">
            <li>
              <b>Replay a night.</b> Forecasts as they stood before tip, results hidden until you tap.
            </li>
            <li>
              <b>Calibration.</b> When we said a chance, how often it happened, with how many cases and a range.
            </li>
            <li>
              <b>Guess against the model.</b> You call over or under on a played prop, then see what it did and what the model said.
            </li>
          </ul>
        </>
      ),
      action: {
        label: "Open the track record",
        go: () => {
          go(recordHref("replay", null));
        },
      },
    },
    {
      title: "Ground rules",
      body: (
        <>
          <p>
            Our forecasts can be wrong, and a forecast is a chance, not a promise. Analysis only, not advice. We do not take bets or sell picks. 21+ (US). If
            gambling is a problem, call <b class="nowrap">1-800-GAMBLER</b>.
          </p>
          <p class="small muted">You can replay this tour any time from the profile menu or About.</p>
        </>
      ),
    },
  ];
  const step = steps[i] ?? steps[0];
  if (!step) return null;
  const last = i === steps.length - 1;
  return (
    <div class="tour-back" role="presentation">
      <div class="tour" role="dialog" aria-modal="true" aria-labelledby="tour-title">
        <div class="tour-top">
          <span class="tour-brand">
            <Logo />
            <span class="small muted num" aria-live="polite">
              Step {i + 1} of {steps.length}
            </span>
          </span>
          <button type="button" class="linklike small" onClick={closeTour}>
            Skip tour
          </button>
        </div>
        <h2 id="tour-title">{step.title}</h2>
        <div class="tour-body">{step.body}</div>
        <div class="tour-dots" aria-hidden="true">
          {steps.map((s, k) => (
            <i key={s.title} class={k === i ? "on" : ""} />
          ))}
        </div>
        <div class="tour-actions">
          <button
            type="button"
            class="btn"
            disabled={i === 0}
            onClick={() => {
              setI((v) => Math.max(0, v - 1));
            }}
          >
            Back
          </button>
          {step.action ? (
            <button type="button" class="btn" onClick={step.action.go}>
              {step.action.label} <Icon name="arrow" />
            </button>
          ) : null}
          <button
            ref={first}
            type="button"
            class="btn primary"
            onClick={() => {
              if (last) closeTour();
              else setI((v) => v + 1);
            }}
          >
            {last ? "Start exploring" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
