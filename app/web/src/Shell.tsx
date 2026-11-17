import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Avatar } from "./Avatar";
import { Footer, ModeToggle, ThemeToggle } from "./components";
import { deskOf, useDesk } from "./deskStore";
import { IS_ARTIFACT } from "./env";
import { clearLocalIdentity, useIdentity } from "./identity";
import { Icon, Logo, type IconName } from "./icons";
import { moveSelection, search, KIND_LABEL, type SearchItem } from "./search";
import { MARKET_ONLY, useIsAdmin, useNotices, useSearchIndex } from "./shellData";
import { useLens } from "./prefs";
import { topHref, type Route, type TopName } from "./router";
import type { MetaLine } from "./metaWatch";
import { Banner, Dialog } from "./ui";
import { ShortcutsDialog, isTypingTarget } from "./Shortcuts";
import { dismissToast, useToasts } from "./toast";
import { openTour } from "./tourState";
import { HealthStrip } from "./HealthStrip";
import { BETTING } from "./features";
import { BETTING_MODULE, betCrumbs, betHref, type BettingNavEntry } from "./betting";
import { BetShellPanel } from "./BetSlots";
import { MenuDrawer } from "./MenuDrawer";
import { SidePanelHost } from "./ui/SidePanel";

interface NavDef {
  name: TopName;
  label: string;
  icon: IconName;
}
// Every top-level view, by name. The sidebar groups them below; the order inside a group is the order on screen.
const ALL_WORKSPACE: NavDef[] = [
  { name: "today", label: "Discover", icon: "discover" },
  { name: "live", label: "Live", icon: "live" },
  { name: "swipe", label: "Swipe", icon: "swipe" },
  { name: "picks", label: "My picks", icon: "picks" },
  { name: "record", label: "Track record", icon: "record" },
  { name: "scorecard", label: "Scorecard", icon: "same" },
  { name: "tests", label: "Forward tests", icon: "flag" },
  { name: "signals", label: "Signals", icon: "signals" },
  { name: "hedge", label: "Hedge Finder", icon: "hedge" },
  { name: "history", label: "Model History", icon: "history" },
  { name: "terms", label: "Key terms", icon: "book" },
  { name: "help", label: "Error codes", icon: "info" },
  { name: "alerts", label: "Alerts", icon: "alerts" },
];
// The track record reads the live API, which the single-file snapshot does not have.
const WORKSPACE: NavDef[] = ALL_WORKSPACE.filter((n) => !(IS_ARTIFACT && (n.name === "record" || n.name === "live")));
/**
 * The sidebar's sections, in the order a visitor uses them: tonight's slate first, then how the model has done, then the
 * Market lens's tools (the betting module's entries join that group), then reference. Preferences and Admin follow in
 * "You". A section with nothing visible in the current lens is not drawn.
 */
const GROUPS: readonly { label: string; names: readonly TopName[]; bet?: true }[] = [
  { label: "Tonight", names: ["today", "live", "swipe", "picks"] },
  { label: "Record and tests", names: ["record", "scorecard", "tests"] },
  { label: "Market tools", names: ["signals", "hedge", "history"], bet: true },
  { label: "Reference", names: ["terms", "help"] },
];
/** The phone's bottom bar: the five most used, the same in every lens (everything else is in the menu drawer). */
const TABS: TopName[] = ["today", "live", "record", "swipe", "picks", "terms"];
export { MARKET_ONLY };

// The nav entry marked current on each route (a game, a leg and the source page belong to Discover).
const ROUTE_TOP: Record<Route["name"], TopName> = {
  today: "today", game: "today", leg: "today", parlay: "today", swipe: "swipe", picks: "picks", settings: "settings",
  signals: "signals", hedge: "hedge", history: "history", record: "record", live: "live", alerts: "alerts", admin: "admin",
  terms: "terms", notfound: "today", source: "today", scorecard: "scorecard", tests: "tests", help: "help",
  // A betting screen has no TopName of its own (the module brings its own nav); crumbs() names it and `here` is null.
  bet: "today",
};
const TITLES: Record<TopName, string> = {
  today: "Discover", signals: "Signals", hedge: "Hedge Finder", history: "Model History", record: "Track record", live: "Live", alerts: "Alerts", swipe: "Swipe", picks: "My picks", admin: "Admin studio", settings: "Preferences", terms: "Key terms",
  scorecard: "Scorecard", tests: "Forward tests", help: "Error codes",
};
const CRUMB: Partial<Record<Route["name"], string>> = { game: "Game", leg: "Leg", parlay: "Parlay of the day", notfound: "Not found", source: "Where a number comes from" };

function crumbs(route: Route): string[] {
  if (BETTING && route.name === "bet") return betCrumbs(route);
  const top = TITLES[ROUTE_TOP[route.name]];
  const sub = CRUMB[route.name];
  return sub ? [top, sub] : [top];
}

/** The app's modal: the one Dialog (focus trap, Escape, focus returns to the opener). */
export function Modal(props: { title: string; onClose: () => void; children: ComponentChildren; wide?: boolean }) {
  return (
    <Dialog title={props.title} onClose={props.onClose} wide={props.wide}>
      {props.children}
    </Dialog>
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
    <Modal title="About parlayhitters" onClose={props.onClose}>
      <p>
        <strong>{props.private_ ? "Jarvis Desk (private)." : "Public."}</strong>{" "}
        {props.private_
          ? "This build can show the desk's paper book, including money fields. It runs on your machine only."
          : "This is the public view: forecasts from the nightly export and, where one exists, the desk's shared cut with every money field removed."}
      </p>
      <ul class="plain">
        <li>Every number comes from a stored row; where there is none, the screen says so.</li>
        <li>Rates carry how many bets they rest on and an interval, or say "not enough to say".</li>
        <li>
          Where a prop has a calibrated chance, only that is shown, at the lines it covers. The forecast distribution (the bars)
          is always shown, and a chance at any line can be read from it.
        </li>
        <li>Everything is a paper trade. Nothing here places an order, holds a credential or recommends a stake.</li>
        <li>Until the live log proves skill, the answer to "what should I bet?" is: keep your money.</li>
        {props.fixture ? <li>The desk numbers in this build are a synthetic fixture, not JARVIS data.</li> : null}
        {props.asOf ? <li>Desk data as of {props.asOf}.</li> : null}
      </ul>
      {IS_ARTIFACT ? null : (
        <p>
          <button
            type="button"
            class="btn"
            onClick={() => {
              props.onClose();
              openTour();
            }}
          >
            Take the tour
          </button>
        </p>
      )}
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

/** The profile popover: a labelled group of links (not an ARIA menu); Escape or a click outside closes it and focus goes back to the button that opened it. */
function ProfileMenu(props: { onClose: () => void; onAbout: () => void; onKeys: () => void }) {
  const id = useIdentity();
  const name = id.name || (id.source === "platform" ? "Someone" : "Guest");
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && document.querySelector('[role="dialog"]') === null) {
        e.preventDefault();
        if (opener?.isConnected) opener.focus();
        close.current();
      }
    };
    const onDown = (e: MouseEvent): void => {
      const t = e.target as Node | null;
      if (box.current && t && !box.current.contains(t) && !(t instanceof Element && t.closest('[aria-label="Profile menu"]'))) close.current();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, []);
  return (
    <div class="popover" role="group" aria-label="Profile" ref={box}>
      <div class="pop-who">
        <Avatar name={name} src={id.avatarUrl} size="md" />
        <div>
          <strong>{name}</strong>
          <div class="muted small">{id.source === "platform" ? "Signed in to your account" : "Name kept on this device"}</div>
        </div>
      </div>
      <div class="pop-row">
        <span class="small muted">Detail</span>
        <ModeToggle />
      </div>
      <a class="pop-link" href={topHref("settings")} onClick={props.onClose}>
        Preferences
      </a>
      <button
        type="button"
        class="pop-link"
        onClick={() => {
          props.onClose();
          props.onAbout();
        }}
      >
        About this app and how it works
      </button>
      <button
        type="button"
        class="pop-link"
        onClick={() => {
          props.onClose();
          props.onKeys();
        }}
      >
        Keyboard shortcuts <kbd class="kbd">?</kbd>
      </button>
      {IS_ARTIFACT ? null : (
        <button
          type="button"
          class="pop-link"
          onClick={() => {
            props.onClose();
            openTour();
          }}
        >
          Take the tour again
        </button>
      )}
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
          Forget my name on this device
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

export function Shell(props: { route: Route; meta: MetaLine; offline: boolean; children: ComponentChildren }) {
  const desk = useDesk();
  const id = useIdentity();
  const isAdmin = useIsAdmin();
  const { unread, list } = useNotices();
  const [searchOpen, setSearchOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);
  const index = useSearchIndex(searchOpen, isAdmin);
  const lens = useLens();
  const visible = WORKSPACE.filter((n) => lens === "market" || !MARKET_ONLY.includes(n.name));
  const tabs = TABS.filter((t) => visible.some((w) => w.name === t)).slice(0, 5);
  // On a betting screen no top-level view is current (the module's own nav entry is).
  const here: TopName | null = BETTING && props.route.name === "bet" ? null : ROUTE_TOP[props.route.name];
  const ready = deskOf(desk);
  const workspace = ready?.privacy === "private" ? "Jarvis Desk (private)" : "Public";
  const name = id.name || (id.source === "platform" ? "Someone" : "Guest");

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
        return;
      }
      // "?" opens the shortcut sheet, except while typing or inside an open dialog.
      if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTypingTarget(e.target) && document.querySelector('[role="dialog"]') === null) {
        e.preventDefault();
        setKeysOpen(true);
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
  const mgmt: NavDef[] = [
    ...visible.filter((n) => n.name === "alerts"),
    { name: "settings", label: "Preferences", icon: "prefs" },
    ...(isAdmin ? [{ name: "admin" as const, label: "Admin studio", icon: "admin" as const }] : []),
  ];
  const link = (n: NavDef, cls: string) => (
    <a key={n.name} class={cls} href={topHref(n.name)} aria-current={here === n.name ? "page" : undefined}>
      <Icon name={n.icon} />
      <span>{n.label}</span>
      {badge(n.name) > 0 ? <span class="nav-badge num">{badge(n.name) > 99 ? "99+" : badge(n.name)}</span> : null}
    </a>
  );
  const crumb = crumbs(props.route);
  // The betting module's nav entries (none in a build with the switch off).
  const betNav: readonly BettingNavEntry[] = BETTING ? BETTING_MODULE.nav.filter((n) => lens === "market" || !n.marketOnly) : [];
  const betLink = (n: BettingNavEntry) => (
    <a key={n.href} class="side-link" href={n.href} aria-current={props.route.name === "bet" && n.href === betHref(props.route.path.join("/")) ? "page" : undefined}>
      <Icon name={n.icon} />
      <span>{n.label}</span>
    </a>
  );

  // One list of sections for the sidebar and the phone drawer: a labelled group per section (heading + links).
  const sectionsFor = (where: "side" | "drawer") => [
    ...GROUPS.map((g) => ({ label: g.label, links: [...visible.filter((n) => g.names.includes(n.name)).sort((a, b) => g.names.indexOf(a.name) - g.names.indexOf(b.name)).map((n) => link(n, "side-link")), ...(g.bet && BETTING ? betNav.map(betLink) : [])] })),
    { label: "You", links: mgmt.map((n) => link(n, "side-link")) },
  ]
    .filter((g) => g.links.length > 0)
    .map((g) => {
      const gid = `${where}-nav-${g.label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
      return (
        <div key={g.label} class="side-group" role="group" aria-labelledby={gid}>
          <div class="side-label" id={gid}>
            {g.label}
          </div>
          {g.links}
        </div>
      );
    });

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
          <span class="brand-words">
            parlayhitters
            <small>NBA forecasts</small>
          </span>
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
        <nav class="side-nav side-groups" aria-label="Main">{sectionsFor("side")}</nav>
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
              <span class="small muted">{id.source === "platform" ? "signed in" : "this device"}</span>
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
                  <span class="crumb-sep" aria-hidden="true">
                    <Icon name="chevron" />
                  </span>
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
          {profileOpen ? <ProfileMenu onClose={() => { setProfileOpen(false); }} onAbout={() => { setAboutOpen(true); }} onKeys={() => { setKeysOpen(true); }} /> : null}
          {menuOpen ? (
            <MenuDrawer
              onClose={() => {
                setMenuOpen(false);
              }}
              note={
                <div class="side-card">
                  <strong class="small">Paper only</strong>
                  <p class="small muted">Nothing here places an order. Every number is from a stored row.</p>
                </div>
              }
            >
              {sectionsFor("drawer")}
            </MenuDrawer>
          ) : null}
        </header>
        {props.meta.text ? (
          <Banner tone={props.meta.kind === "problem" ? "notice" : "info"} class="dataline">
            {props.meta.text}
          </Banner>
        ) : null}
        {props.meta.text ? <HealthStrip /> : null}
        {props.meta.newer !== null ? (
          <Banner
            tone="fresh"
            role="alert"
            action={
              <button
                type="button"
                class="btn small"
                onClick={() => {
                  window.location.reload();
                }}
              >
                Reload
              </button>
            }
          >
            Newer data exists (built {props.meta.newer}). Reload to see it; nothing changes until you do.
          </Banner>
        ) : null}
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
        {BETTING ? <BetShellPanel onBetRoute={props.route.name === "bet"} /> : null}
        <Footer />
      </div>
      <SidePanelHost />
      <nav class="nav-bottom" aria-label="Main">
        {tabs.map((t) => {
          const n = visible.find((w) => w.name === t) as NavDef;
          return (
            <a key={t} href={topHref(t)} aria-current={here === t ? "page" : undefined}>
              <Icon name={n.icon} />
              {n.label}
              {badge(t) > 0 ? <span class="tab-badge num">{badge(t) > 99 ? "99+" : badge(t)}</span> : null}
            </a>
          );
        })}
      </nav>
      {keysOpen ? <ShortcutsDialog onClose={() => { setKeysOpen(false); }} /> : null}
      {searchOpen ? <SearchModal index={index} onClose={() => { setSearchOpen(false); }} /> : null}
      {aboutOpen ? <AboutModal onClose={() => { setAboutOpen(false); }} private_={ready?.privacy === "private"} fixture={ready?.fixture != null} asOf={ready?.data_as_of ?? null} /> : null}
      <Toasts />
      <span class="sr" aria-live="polite">{list.length > 0 && unread > 0 ? `${unread.toString()} unread alerts` : ""}</span>
    </div>
  );
}
