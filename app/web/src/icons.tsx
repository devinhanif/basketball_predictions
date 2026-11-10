// Stroke icons (24-box paths) and the logo. Decorative: always aria-hidden; the text beside them carries the meaning.
export const ICONS = {
  discover: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15.5 8.5l-2 5-5 2 2-5z",
  signals: "M3 12h4l3-8 4 16 3-8h4",
  hedge: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 11.5v1",
  history: "M3 17l6-6 4 4 8-8M15 7h6v6",
  alerts: "M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7zM10 20a2 2 0 0 0 4 0",
  swipe: "M4 7h16v10H4zM8 3h8M8 21h8",
  picks: "M5 4h14v16H5zM8.5 9h7M8.5 13h7M8.5 17h4",
  admin: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4",
  prefs: "M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "M6 6l12 12M18 6L6 18",
  check: "M5 12l4 4 10-10",
  plus: "M12 5v14M5 12h14",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13M10 11v5M14 11v5",
  reset: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5",
  download: "M12 4v11M7 11l5 5 5-5M5 20h14",
  flag: "M6 21V4M6 5h11l-2 4 2 4H6",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  chevron: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5M12 8h.01",
  spark: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
  lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  sliders: "M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6",
  arrow: "M7 17L17 7M9 7h8v8",
  live: "M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14",
  record: "M4 20h16M7 20v-9M12 20V5M17 20v-6",
} as const;
export type IconName = keyof typeof ICONS;

export function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

export function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill="var(--accent)" />
      <rect x="14" y="36" width="9" height="14" rx="2" fill="var(--on-accent)" />
      <rect x="27.5" y="18" width="9" height="32" rx="2" fill="var(--on-accent)" />
      <rect x="41" y="28" width="9" height="22" rx="2" fill="var(--on-accent)" />
    </svg>
  );
}
