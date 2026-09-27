"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "eleven-theme";

/**
 * Light/dark theme owner. The theme is backed by external state —
 * localStorage plus the OS color-scheme preference — and the rendered
 * result is the `data-theme` attribute on <html> (so the CSS variable
 * overrides in globals.css re-skin everything, including SVG palettes
 * that read it).
 *
 * Hydration stays clean by construction: the server always renders
 * `data-theme="light"` (the deterministic default), so the DOM matches
 * the server HTML at hydration time for every user. Immediately after
 * hydration, `useSyncExternalStore` reads the persisted/OS preference
 * and re-renders if it differs — a single frame, no pre-paint script
 * that would desynchronize the DOM from the server HTML (React 19
 * rejects that), and no setState-in-effect.
 */
const listeners = new Set<() => void>();

function emitChange(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === null) emitChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function resolveTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark") return "dark";
    if (stored === "light") return "light";
  } catch {
    // Storage unavailable (private mode) — fall through to OS preference.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function getSnapshot(): Theme {
  return resolveTheme();
}

function getServerSnapshot(): Theme {
  return "light";
}

function applyToDom(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

interface ThemeValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // Keep the DOM attribute in sync with the resolved theme. Writing the
  // attribute (an external system) is the effect's job; the state itself
  // comes from the store, so there is no cascading render.
  useEffect(() => {
    applyToDom(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    const next: Theme = theme === "light" ? "dark" : "light";
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage unavailable — the toggle still applies for the session
      // via the store emit below, which is the behavior that matters.
    }
    emitChange();
  }, [theme]);

  const value = useMemo<ThemeValue>(() => ({ theme, toggle }), [theme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
