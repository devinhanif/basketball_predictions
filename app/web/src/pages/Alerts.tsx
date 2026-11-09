import { useMemo, useState } from "preact/hooks";
import { buildNotices, filterNotices, newestNight, NOTICE_FILTERS, unreadCount, withOlderRead, type Notice, type NoticeFilter } from "../alerts";
import { DeskGate, SectionNote } from "../DeskUI";
import { correctionLinks, type Desk } from "../desk";
import { longDate, stamp } from "../format";
import { Icon } from "../icons";
import { pushToast } from "../toast";
import { markIdsRead, useUserState } from "../userState";

const PAGE = 25;
const CAT_ICON = { risk: "info", foul: "flag", rotation: "swipe", correction: "reset", other: "alerts" } as const;
const CAT_WORD = { risk: "Risk", foul: "Fouls", rotation: "Rotation", correction: "Correction", other: "Update" } as const;

function Item({ n, read, onOpen }: { n: Notice; read: boolean; onOpen: () => void }) {
  return (
    <li class={`notice-row${read ? " read" : ""}`}>
      <button type="button" class="notice-btn" onClick={onOpen} aria-label={`${n.title}. ${read ? "Read" : "Unread. Mark as read"}`}>
        <span class={`notice-ico ${n.category}`} aria-hidden="true">
          <Icon name={CAT_ICON[n.category]} />
        </span>
        <span class="notice-main">
          <span class="notice-meta small muted num">
            <b>{CAT_WORD[n.category]}</b>
            {n.matchup ? ` · ${n.matchup}` : ""}
            {n.when ? ` · ${n.when}` : ""}
            {` · ${stamp(n.ts)}`}
            {n.certainty ? ` · ${n.certainty}` : ""}
          </span>
          <span class="notice-title">
            {n.title}
            {read ? null : (
              <span class="unread-dot">
                <span class="sr">unread</span>
              </span>
            )}
          </span>
          <span class={`notice-text small${n.text ? "" : " muted"}`}>{n.text ?? n.fallback}</span>
        </span>
      </button>
    </li>
  );
}

function Body({ desk }: { desk: Desk }) {
  const us = useUserState();
  const all = useMemo(() => buildNotices(desk), [desk]);
  const read = useMemo(() => withOlderRead(all, us.read), [all, us.read]);
  const newest = newestNight(all);
  const links = useMemo(() => correctionLinks(desk.observations), [desk]);
  const [filter, setFilter] = useState<NoticeFilter>("all");
  const [shown, setShown] = useState(PAGE);
  const list = useMemo(() => filterNotices(all, filter, read), [all, filter, read]);
  const unread = unreadCount(all, read, { fouls: true, risk: true, corrections: true });
  const unreadIn = (f: NoticeFilter): number => filterNotices(all, f, read).filter((n) => !read.has(n.id)).length;
  const linked = [...links.correctedBy.keys()].length;
  return (
    <div class="stack">
      <header class="page-head">
        <div class="row between wrap">
          <div>
            <div class="kicker">Notification center</div>
            <h1 class="answer">
              {all.length.toLocaleString("en-US")} alerts and observations · {unread.toLocaleString("en-US")} unread
            </h1>
            <p class="muted answer-src">
              What changed and what is still not known. Read marks are kept for you only; {newest ? `alerts older than ${longDate(newest).replace(/, \d{4}$/, "")} start as read.` : "older alerts start as read."}
            </p>
          </div>
          <button
            type="button"
            class="btn"
            onClick={() => {
              pushToast("Test alert", "This is a local test. Nothing was sent and no push is connected.");
            }}
          >
            <Icon name="plus" /> Test an alert
          </button>
        </div>
      </header>
      <div class="two-col wide-left">
        <section class="panel" aria-labelledby="ev-h">
          <div class="row between wrap">
            <div>
              <h2 id="ev-h">Event intelligence</h2>
              <p class="small muted num">
                {unread.toLocaleString("en-US")} unread · {all.length.toLocaleString("en-US")} total
              </p>
            </div>
            <button
              type="button"
              class="btn"
              disabled={list.every((n) => read.has(n.id))}
              onClick={() => {
                markIdsRead(list.map((n) => n.id));
              }}
            >
              <Icon name="check" /> Mark {filter === "all" ? "all" : "these"} read
            </button>
          </div>
          <div class="tabs filters" role="group" aria-label="Filter alerts">
            {NOTICE_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                class="tab"
                aria-current={filter === f.value ? "true" : undefined}
                onClick={() => {
                  setFilter(f.value);
                  setShown(PAGE);
                }}
              >
                {f.label}
                {f.value === "unread" ? <span class="tab-n num"> {unreadIn("unread")}</span> : null}
              </button>
            ))}
          </div>
          {list.length === 0 ? (
            <p class="muted empty-line">Nothing here{filter === "unread" ? ": you are all caught up" : " for this filter"}.</p>
          ) : (
            <ul class="notices">
              {list.slice(0, shown).map((n) => (
                <Item
                  key={n.id}
                  n={n}
                  read={read.has(n.id)}
                  onOpen={() => {
                    markIdsRead([n.id]);
                  }}
                />
              ))}
            </ul>
          )}
          {list.length > shown ? (
            <button
              type="button"
              class="btn"
              onClick={() => {
                setShown((s) => s + PAGE);
              }}
            >
              Show more ({(list.length - shown).toLocaleString("en-US")} left)
            </button>
          ) : null}
          <SectionNote desk={desk} k="alerts.text" />
        </section>
        <aside class="stack side-col">
          <section class="panel" aria-label="What is tracked">
            <div class="label">What this list is</div>
            <h2>Said as stored</h2>
            <p class="small">
              A foul count is an observation. Its effect on minutes is an inference. A later correction is linked to the original, not
              a replacement for it. Where no message text was stored, the alert is described from its kind and what it links.
            </p>
            <ul class="status-list">
              <li>
                <span>Player availability</span>
                <span class="chip ok">Stored flags</span>
              </li>
              <li>
                <span>Market changes</span>
                <span class="chip">Stored quotes only</span>
              </li>
              <li>
                <span>Corrections</span>
                <span class="chip ok num">{linked.toLocaleString("en-US")} linked</span>
              </li>
              <li>
                <span>Push notifications</span>
                <span class="chip off">Not connected</span>
              </li>
            </ul>
          </section>
          <section class="panel flat">
            <p class="small muted">
              Notification switches are in <a href="#/prefs">Preferences</a>. Data through {longDate(desk.data_as_of.slice(0, 10))}.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

export function Alerts() {
  return <DeskGate title="Alerts">{(desk) => <Body desk={desk} />}</DeskGate>;
}
