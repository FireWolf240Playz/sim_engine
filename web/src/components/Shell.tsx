"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useSyncExternalStore, type ReactNode } from "react";
import { api } from "@/core/api/client";
import { useTheme } from "@/core/state/ThemeContext";

const NAV = [
  { href: "/", label: "Simulator", icon: "play" },
  { href: "/incidents", label: "Incidents", icon: "alert" },
  { href: "/import", label: "Import", icon: "upload" },
  { href: "/compare", label: "Compare", icon: "columns" },
  { href: "/presets", label: "Presets", icon: "layers" },
] as const;

type NavIcon = (typeof NAV)[number]["icon"];

const SIDEBAR_KEY = "eleven-sidebar";

/**
 * Sidebar collapse state, backed by localStorage (external state) and
 * subscribed with `useSyncExternalStore` — the same hydration-safe pattern
 * as the theme: the server renders "expanded", and the persisted choice is
 * applied right after hydration.
 */
const sidebarListeners = new Set<() => void>();

function emitSidebarChange(): void {
  for (const listener of sidebarListeners) listener();
}

function subscribeSidebar(listener: () => void): () => void {
  sidebarListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === SIDEBAR_KEY || event.key === null) emitSidebarChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    sidebarListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function getSidebarSnapshot(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function getSidebarServerSnapshot(): boolean {
  return false;
}

function setSidebarCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    // Storage unavailable (private mode) — the collapse still applies for
    // this tab via the store emit below.
  }
  emitSidebarChange();
}

function NavGlyph({ icon, className = "h-4 w-4" }: { icon: NavIcon; className?: string }) {
  const stroke = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (icon === "play") {
    return (
      <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" {...stroke} />
        <path d="M6.8 5.6 10.6 8 6.8 10.4Z" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (icon === "alert") {
    return (
      <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
        <path d="M8 2.2 14 13H2Z" {...stroke} />
        <path d="M8 6.4v3" {...stroke} />
        <circle cx="8" cy="11" r="0.7" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (icon === "columns") {
    return (
      <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
        <path d="M4 13V6M8 13V3M12 13V8" {...stroke} />
      </svg>
    );
  }
  if (icon === "upload") {
    return (
      <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
        <path d="M8 10V2.8M5.4 5.4 8 2.8l2.6 2.6" {...stroke} />
        <path d="M2.5 10.5v2A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5v-2" {...stroke} />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      <path d="m8 2 5.5 3L8 8 2.5 5Z" {...stroke} />
      <path d="m2.5 8 5.5 3 5.5-3" {...stroke} />
      <path d="m2.5 11 5.5 3 5.5-3" {...stroke} />
    </svg>
  );
}

function SunIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      <circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M12.6 3.4l-1.3 1.3M4.7 11.3l-1.3 1.3"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      <path
        d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconButton({
  label,
  onClick,
  children,
  hideOnMobile = false,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  hideOnMobile?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`${hideOnMobile ? "hidden md:flex" : "flex"} h-8 w-8 items-center justify-center rounded-lg border border-line text-ink-dim outline-none transition-colors hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-accent`}
    >
      {children}
    </button>
  );
}

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
  return (
    <IconButton label={label} onClick={toggle}>
      {theme === "light" ? <MoonIcon /> : <SunIcon />}
    </IconButton>
  );
}

function SidebarCollapseButton({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <IconButton label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggle} hideOnMobile>
      <svg
        viewBox="0 0 16 16"
        className={`h-4 w-4 transition-transform duration-200 ease-out ${collapsed ? "rotate-180" : ""}`}
        aria-hidden="true"
      >
        <path d="m9 4.5-3 3.5 3 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d="m12.5 4.5-3 3.5 3 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </IconButton>
  );
}

function EngineStatus({ collapsed }: { collapsed: boolean }) {
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });
  const up = data?.status === "ok";
  return (
    <div className={`flex ${collapsed ? "md:justify-center" : "flex-col gap-1.5"}`}>
      <div className="flex items-center gap-2 text-[12px]">
        <span
          aria-hidden="true"
          className={`inline-block h-1.5 w-1.5 rounded-full ${up ? "bg-sev-ok" : "bg-sev-crit"}`}
        />
        <span className={`${collapsed ? "md:hidden" : ""} ${up ? "text-ink-dim" : "font-medium text-sev-crit"}`}>
          {up ? "engine ok" : "engine offline"}
        </span>
      </div>
      <span className={`font-mono text-[10.5px] text-ink-dim/70 ${collapsed ? "md:hidden" : ""}`}>
        {api.base}
      </span>
    </div>
  );
}

function NavLinks({ vertical, collapsed }: { vertical: boolean; collapsed: boolean }) {
  const pathname = usePathname();
  const items = NAV.map((item) => {
    const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? "page" : undefined}
        title={collapsed ? item.label : undefined}
        className={`flex items-center rounded-lg py-2 text-[13px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent ${
          collapsed ? "md:justify-center md:px-0" : "gap-2.5 px-3"
        } ${
          active
            ? "bg-accent-soft text-accent"
            : "text-ink-dim hover:bg-surface-2 hover:text-ink"
        }`}
      >
        <NavGlyph
          icon={item.icon}
          className={`h-5 w-5 shrink-0 ${active ? "text-accent" : "text-ink-dim/80"}`}
        />
        <span
          className={`whitespace-nowrap transition-[max-width,opacity] duration-200 ease-out ${
            collapsed ? "md:max-w-0 md:opacity-0" : "md:max-w-40 opacity-100"
          }`}
        >
          {item.label}
        </span>
      </Link>
    );
  });
  return vertical ? (
    <nav aria-label="Primary" className="flex flex-col gap-1">
      {items}
    </nav>
  ) : (
    <nav aria-label="Primary" className="flex flex-wrap gap-1">
      {items}
    </nav>
  );
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={`min-w-0 items-center gap-2.5 ${collapsed ? "flex md:hidden" : "flex"}`}>
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent font-mono text-[13px] font-bold text-white">
        11
      </span>
      <div className={`min-w-0 leading-tight ${collapsed ? "md:hidden" : "flex flex-col"}`}>
        <span className="text-[15px] font-semibold tracking-tight text-ink">Eleven</span>
        <span className="text-[11px] text-ink-dim">resilience lab</span>
      </div>
    </div>
  );
}

/**
 * App shell: a persistent sidebar (Simulator / Incidents / Compare /
 * Presets) with the theme toggle and a sidebar collapse control in the
 * top-right beside the brand, plus live engine status at the bottom.
 * The collapse state persists across sessions and animates the sidebar
 * width; labels and secondary text hide when collapsed. On narrow
 * screens the sidebar becomes a top block — the same four functions.
 */
export function Shell({ children }: { children: ReactNode }) {
  const collapsed = useSyncExternalStore(
    subscribeSidebar,
    getSidebarSnapshot,
    getSidebarServerSnapshot,
  );

  return (
    <div className="flex min-h-full flex-1 flex-col md:flex-row">
      <aside
        className={`shrink-0 border-b border-line bg-surface-1 transition-[width] duration-200 ease-out md:overflow-hidden md:border-b-0 md:border-r ${
          collapsed ? "md:w-16" : "md:w-60"
        }`}
      >
        <div className="flex flex-col gap-5 px-4 py-4 md:h-full md:px-3 md:py-5">
          <div className={`flex items-center gap-2 ${collapsed ? "justify-between md:justify-center" : "justify-between"}`}>
            <Brand collapsed={collapsed} />
            <div className="flex items-center gap-1.5">
              <SidebarCollapseButton collapsed={collapsed} onToggle={() => setSidebarCollapsed(!collapsed)} />
              {/* Collapsed rail shows only the chevron (Clearcue-style icon
                  rail): the theme toggle lives in the expanded sidebar. */}
              <span className={collapsed ? "md:hidden" : ""}>
                <ThemeToggle />
              </span>
            </div>
          </div>
          <div className="md:hidden">
            <NavLinks vertical={false} collapsed={false} />
          </div>
          <div className="hidden md:block">
            <NavLinks vertical collapsed={collapsed} />
          </div>
          <div className="mt-auto hidden flex-col gap-4 md:flex">
            <div className="border-t border-line pt-4">
              <EngineStatus collapsed={collapsed} />
            </div>
            <p className={`text-[11px] leading-4 text-ink-dim/80 ${collapsed ? "md:hidden" : ""}`}>
              Pre-deployment digital-twin simulator. Stress-test an
              architecture before it is real infrastructure.
            </p>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 md:py-8">
          {children}
        </main>
        <footer className="border-t border-line bg-surface-1">
          <div className="mx-auto w-full max-w-6xl px-4 py-3 text-[11px] text-ink-dim sm:px-6">
            Cost projections assume the same load sustained 24/7 — a planning
            figure, not a bill.
          </div>
        </footer>
      </div>
    </div>
  );
}
