import type { ReactNode } from "react";

const icons: Record<string, ReactNode> = {
  chat: <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 9 9 0 0 1-4-.9L3 21l1.9-5.5a9 9 0 0 1-.9-4A8.5 8.5 0 0 1 12.5 3 8.5 8.5 0 0 1 21 11.5Z" />,
  search: <><circle cx="10.8" cy="10.8" r="7.3"/><path d="m16 16 5 5"/></>,
  memory: <><rect x="6" y="3" width="14" height="17" rx="2"/><path d="M3 7v14h13M10 7h6M10 11h6M10 15h3"/></>,
  goals: <><path d="M9 17h6M9 21h6M8 13a6 6 0 1 1 8 0c-1.2 1-1 2-1 4H9c0-2 .2-3-1-4Z"/></>,
  tasks: <><rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8 12 3 3 5-6"/></>,
  workflows: <><rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="15" width="6" height="6" rx="1.5"/><path d="M18 4v5M15.5 6.5h5M6 15v5M3.5 17.5h5M9 6h4a5 5 0 0 1 5 5v4"/></>,
  activity: <path d="M3 12h4l3-8 4 16 3-8h4"/>,
  connections: <><path d="m9 7-3 3a4 4 0 0 0 6 6l3-3M15 17l3-3a4 4 0 0 0-6-6l-3 3M9 15l6-6"/></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
  settings: <><path d="m10 3 1-1h2l1 3 3 1 3-1 1 2-2 3v3l2 2-1 3-3-1-3 1-1 3h-3l-1-3-3-1-3 1-1-3 2-2v-3L2 7l2-2 3 1 3-1Z"/><circle cx="12" cy="11.5" r="3"/></>,
  logout: <><path d="M10 4H5v16h5M9 12h12m-4-4 4 4-4 4"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  sparkles: <><path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5L12 2Z"/><path d="m3 3 2 2m14 14 2 2"/></>,
  calendar: <><rect x="4" y="5" width="16" height="16" rx="3"/><path d="M8 2v6m8-6v6M4 11h16m-12 4h3"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  alert: <><path d="m12 3 10 17H2L12 3Z"/><path d="M12 9v5m0 3h.01"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
  "arrow-right": <path d="M4 12h16m-6-6 6 6-6 6"/>,
  panel: <><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M15 3v18"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
};

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icons[name] ?? icons.grid}</svg>;
}
