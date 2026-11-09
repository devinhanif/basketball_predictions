import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Avatar } from "./Avatar";
import { Footer, ModeToggle, ThemeToggle } from "./components";
import { deskOf, useDesk } from "./deskStore";
import { IS_ARTIFACT } from "./env";
import { clearLocalIdentity, useIdentity } from "./identity";
import { Icon, Logo, type IconName } from "./icons";
import { moveSelection, search, KIND_LABEL, type SearchItem } from "./search";
import { useIsAdmin, useNotices, useSearchIndex } from "./shellData";
import { topHref, type Route, type TopName } from "./router";
import { dismissToast, useToasts } from "./toast";

interface NavDef {
  name: TopName;
  label: string;
  icon: IconName;
}
// Track record and the Desk of earlier plans are now Model History and Signals; nothing else is listed until it exists.
const WORKSPACE: NavDef[] = [
  { name: "today", label: "Discover", icon: "discover" },
  { name: "signals", label: "Signals", icon: "signals" },
  { name: "hedge", label: "Hedge Finder", icon: "hedge" },
  { name: "history", label: "Model History", icon: "history" },
  { name: "alerts", label: "Alerts", icon: "alerts" },
  { name: "swipe", label: "Swipe", icon: "swipe" },
  { name: "picks", label: "My picks", icon: "picks" },
];
const TABS: TopName[] = ["today", "signals", "alerts", "swipe", "picks"];

const ROUTE_TOP: Record<Route["name"], TopName> = {
  today: "today", game: "today", leg: "today", parlay: "today", swipe: "swipe", picks: "picks", settings: "settings",
  signals: "signals", hedge: "hedge", history: "history", alerts: "alerts", admin: "admin",
};
const TITLES: Record<TopName, string> = {
  today: "Discover", signals: "Signals", hedge: "Hedge Finder", history: "Model History", alerts: "Alerts", swipe: "Swipe", picks: "My picks", admin: "Admin studio", settings: "Preferences",
};
const CRUMB: Partial<Record<Route["name"], string>> = { game: "Game", leg: "Leg", parlay: "Parlay of the day" };

function crumbs(route: Route): string[] {
  const top = TITLES[ROUTE_TOP[route.name]];
  const sub = CRUMB[route.name];
  return sub ? [top, sub] : [top];
}

/** Modal shell: Escape and the backdrop close it; focus moves in and returns. */
export function Modal(props: { title: string; onClose: () => void; children: ComponentChildren; wide?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("input, button, a")?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        props.onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, []);
  return (
    <div
      class="modal-back"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div class={`modal${props.wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={props.title} ref={box}>
        <div class="modal-head">
          <h2>{props.title}</h2>
          <button type="button" class="icon-btn" aria-label="Close" onClick={props.onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div class="modal-body">{props.children}</div>
      </div>
    </div>
  );
}

function SearchModal(props: { index: SearchItem[]; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const results = search(props.index, q);
  const go = (item: SearchItem | undefined): void => {
    if (!item) return;
    props.onClose();
    if (IS_ARTIFACT) {
      // The page's own router handles in-page links by click.
      const a = document.createElement("a");
      a.setAttribute("href", item.href);
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else {
      window.location.hash = item.href;
    }
  };
  let lastKind: string | null = null;
  return (
    <Modal title="Search" onClose={props.onClose}>
      <div class="search-box">
        <Icon name="search" />
        <input
          type="search"
          class="search-input"
          placeholder="Players, games, screens"
          aria-label="Search players, games and screens"
          aria-controls="search-results"
          value={q}
          onInput={(e) => {
            setQ(e.currentTarget.value);
            setSel(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSel((s) => moveSelection(s, 1, results.length));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setSel((s) => moveSelection(s, -1, results.length));
            } else if (e.key === "Enter") {
              e.preventDefault();
              go(results[sel]);
            }
          }}
        />
      </div>
      <ul class="search-results" id="search-results" role="listbox" aria-label="Results">
        {results.length === 0 ? <li class="muted search-none">Nothing matches "{q}".</li> : null}
        {results.map((r, i) => {
          const head = lastKind !== r.kind;
          lastKind = r.kind;
          return (
            <>
              {head ? <li class="search-group" role="presentation">{KIND_LABEL[r.kind]}</li> : null}
              <li key={`${r.kind}-${r.href}-${r.label}`} role="option" aria-selected={i === sel}>
                <button
                  type="button"
                  class="search-row"
                  data-active={i === sel ? "true" : undefined}
                  onMouseEnter={() => {
                    setSel(i);
                  }}
                  onClick={() => {
                    go(r);
                  }}
                >
                  <strong>{r.label}</strong>
                  <span class="muted small">{r.sub}</span>
                </button>
              </li>
            </>
          );
        })}
      </ul>
      <p class="muted small search-hint">Arrow keys to move, Enter to open, Esc to close.</p>
    </Modal>
  );
}

function AboutModal(props: { onClose: () => void; private_: boolean; fixture: boolean; asOf: string | null }) {
  return (
    <Modal title="About this workspace" onClose={props.onClose}>
      <p>
        <strong>{props.private_ ? "Jarvis Desk (private)." : "Public."}</strong>{" "}
        {props.private_
          ? "This build can show the desk's paper book, including money fields. It runs on your machine only."
          : "This is the public view: forecasts from the nightly export and, where one exists, the desk's shared cut with every money field removed."}
      </p>
      <ul class="plain">
        <li>Every number comes from a stored row; where there is none, the screen says so.</li>
        <li>Rates carry how many bets they rest on and an interval, or say "not enough to say".</li>
        <li>Everything is a paper trade. Nothing here places an order, holds a credential or recommends a stake.</li>
        <li>Until the live log proves skill, the answer to "what should I bet?" is: keep your money.</li>
        {props.fixture ? <li>The desk numbers in this build are a synthetic fixture, not JARVIS data.</li> : null}
        {props.asOf ? <li>Desk data as of {props.asOf}.</li> : null}
      </ul>
      <p class="small muted">
        Analysis only, not advice. We do not take bets or sell picks. 21+ (US). If gambling is a problem, call 1-800-GAMBLER.
      </p>
    </Modal>
  );
}

export function Bell({ unread }: { unread: number }) {
  return (
    <a class="icon-btn bell" href={topHref("alerts")} aria-label={unread > 0 ? `Alerts, ${unread.toString()} unread` : "Alerts, none unread"}>
      <Icon name="alerts" />
      {unread > 0 ? <span class="bell-n num">{unread > 99 ? "99+" : unread}</span> : null}
    </a>
  );
}

function ProfileMenu(props: { onClose: () => void }) {
  const id = useIdentity();
  const name = id.name || (id.source === "platform" ? "Someone" : "Guest");
  return (
    <div class="popover" role="menu" aria-label="Profile">
      <div class="pop-who">
        <Avatar name={name} src={id.avatarUrl} size="md" />
        <div>
          <strong>{name}</strong>
          <div class="muted small">{id.source === "platform" ? "Signed in through claude.ai" : "Name kept on this device"}</div>
        </div>
      </div>
      <div class="pop-row">
        <span class="small muted">Detail</span>
        <ModeToggle />
      </div>
      <a class="pop-link" href={topHref("settings")} onClick={props.onClose}>
        Preferences
      </a>
      {IS_ARTIFACT ? (
        <p class="small muted pop-note">Your identity comes from the platform. There is no password here.</p>
      ) : (
        <button
          type="button"
          class="pop-link"
          onClick={() => {
            clearLocalIdentity();
            props.onClose();
          }}
        >
          Sign out (forget my name on this device)
        </button>
      )}
    </div>
  );
}

export function Toasts() {
  const t = useToasts();
  return (
    <div class="toasts" aria-live="polite">
      {t.map((x) => (
        <div class="toast" key={x.id} role="status">
          <div>
            <strong>{x.title}</strong>
            <div class="small">{x.text}</div>
          </div>
          <button
            type="button"
            class="icon-btn"
            aria-label="Dismiss"
            onClick={() => {
              dismissToast(x.id);
            }}
          >
            <Icon name="close" />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Shell(props: { route: Route; replayNote: string | null; offline: boolean; children: ComponentChildren }) {
  const desk = useDesk();
  const id = useIdentity();
  const isAdmin = useIsAdmin();
  const { unread, list } = useNotices();
  const [searchOpen, setSearchOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const index = useSearchIndex(searchOpen, isAdmin);
  const here = ROUTE_TOP[props.route.name];
  const ready = deskOf(desk);
  const workspace = ready?.privacy === "private" ? "Jarvis Desk (private)" : "Public";
  const name = id.name || (id.source === "platform" ? "Someone" : "Guest");

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  useEffect(() => {
    setMenuOpen(false);
    setProfileOpen(false);
  }, [props.route]);

  const signalsToday = ready ? ready.observations.filter((o) => o.slate_date === (ready.observations.map((x) => x.slate_date).sort().at(-1) ?? "")).length : 0;
  const badge = (n: TopName): number => (n === "alerts" ? unread : n === "signals" ? signalsToday : 0);
  const mgmt: NavDef[] = [...(isAdmin ? [{ name: "admin" as const, label: "Admin studio", icon: "admin" as const }] : []), { name: "settings", label: "Preferences", icon: "prefs" }];
  const link = (n: NavDef, cls: string) => (
    <a key={n.name} class={cls} href={topHref(n.name)} aria-current={here === n.name ? "page" : undefined}>
      <Icon name={n.icon} />
      <span>{n.label}</span>
      {badge(n.name) > 0 ? <span class="nav-badge num">{badge(n.name) > 99 ? "99+" : badge(n.name)}</span> : null}
    </a>
  );
  const crumb = crumbs(props.route);

  return (
    <div class="shell">
      <a
        class="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside class="sidebar" aria-label="Sidebar">
        <a class="brand" href={topHref("today")} aria-label="parlayhitters, home">
          <Logo />
          parlayhitters
        </a>
        <button
          type="button"
          class="workspace"
          onClick={() => {
            setAboutOpen(true);
          }}
        >
          <span class="ws-mark" aria-hidden="true">
            <Icon name={ready?.privacy === "private" ? "lock" : "grid"} />
          </span>
          <span class="ws-text">
            <strong>{workspace}</strong>
            <span class="small muted">{ready?.fixture ? "synthetic desk fixture" : IS_ARTIFACT ? "shared page, snapshot" : "this machine"}</span>
          </span>
        </button>
        <div class="side-label">Workspace</div>
        <nav class="side-nav" aria-label="Main">
          {WORKSPACE.map((n) => link(n, "side-link"))}
        </nav>
        <div class="side-label">Management</div>
        <nav class="side-nav" aria-label="Management">
          {mgmt.map((n) => link(n, "side-link"))}
        </nav>
        <div class="side-foot">
          <div class="side-card">
            <strong class="small">Paper only</strong>
            <p class="small muted">Nothing here places an order. Every number is from a stored row.</p>
            <button
              type="button"
              class="linklike small"
              onClick={() => {
                setAboutOpen(true);
              }}
            >
              About this workspace
            </button>
          </div>
          <button
            type="button"
            class="side-me"
            aria-label="Profile menu"
            aria-expanded={profileOpen}
            onClick={() => {
              setProfileOpen((o) => !o);
            }}
          >
            <Avatar name={name} src={id.avatarUrl} size="md" />
            <span class="ws-text">
              <strong>{name}</strong>
              <span class="small muted">{id.source === "platform" ? "claude.ai" : "this device"}</span>
            </span>
          </button>
        </div>
      </aside>
      <div class="content">
        <header class="topbar">
          <div class="topbar-in">
            <a class="brand brand-small" href={topHref("today")} aria-label="parlayhitters, home">
              <Logo />
              <span class="brand-text">parlayhitters</span>
            </a>
            <nav class="crumbs" aria-label="Breadcrumb">
              <span class="muted">Workspace</span>
              {crumb.map((c, i) => (
                <span key={c}>
                  <span class="muted"> / </span>
                  <span aria-current={i === crumb.length - 1 ? "page" : undefined}>{c}</span>
                </span>
              ))}
            </nav>
            <span class="spacer" />
            <button
              type="button"
              class="icon-btn search-btn"
              aria-label="Search (Ctrl or Command K)"
              onClick={() => {
                setSearchOpen(true);
              }}
            >
              <Icon name="search" />
              <span class="icon-btn-text">Search</span>
              <kbd class="kbd">Ctrl K</kbd>
            </button>
            <ThemeToggle />
            <Bell unread={unread} />
            <button
              type="button"
              class="icon-btn menu-btn"
              aria-label="Menu"
              aria-expanded={menuOpen}
              onClick={() => {
                setMenuOpen((o) => !o);
              }}
            >
              <Icon name="menu" />
            </button>
            <button
              type="button"
              class="icon-btn avatar-btn"
              aria-label="Profile menu"
              aria-expanded={profileOpen}
              onClick={() => {
                setProfileOpen((o) => !o);
              }}
            >
              <Avatar name={name} src={id.avatarUrl} size="sm" />
            </button>
          </div>
          {profileOpen ? <ProfileMenu onClose={() => { setProfileOpen(false); }} /> : null}
          {menuOpen ? (
            <nav class="nav-sheet" aria-label="All screens">
              {[...WORKSPACE, ...mgmt].map((n) => link(n, "side-link"))}
            </nav>
          ) : null}
        </header>
        {props.replayNote ? <div class="datanote" role="status">{props.replayNote}</div> : null}
        {props.offline ? (
          <div class="banner" role="status">
            You are offline, or the data server is not answering. Showing the last copy we saved.
          </div>
        ) : null}
        {ready?.fixture ? (
          <div class="banner fixture" role="note">
            {ready.fixture}
          </div>
        ) : null}
        <main id="main" tabIndex={-1}>{props.children}</main>
        <Footer />
      </div>
      <nav class="nav-bottom" aria-label="Main">
        {TABS.map((t) => {
          const n = WORKSPACE.find((w) => w.name === t) as NavDef;
          return (
            <a key={t} href={topHref(t)} aria-current={here === t ? "page" : undefined}>
              <Icon name={n.icon} />
              {n.label}
              {badge(t) > 0 ? <span class="tab-badge num">{badge(t) > 99 ? "99+" : badge(t)}</span> : null}
            </a>
          );
        })}
      </nav>
      {searchOpen ? <SearchModal index={index} onClose={() => { setSearchOpen(false); }} /> : null}
      {aboutOpen ? <AboutModal onClose={() => { setAboutOpen(false); }} private_={ready?.privacy === "private"} fixture={ready?.fixture != null} asOf={ready?.data_as_of ?? null} /> : null}
      <Toasts />
      <span class="sr" aria-live="polite">{list.length > 0 && unread > 0 ? `${unread.toString()} unread alerts` : ""}</span>
    </div>
  );
}
