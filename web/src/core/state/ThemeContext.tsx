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
import { THEME_STORAGE_KEY } from "../lib/themeStorage";

export type Theme = "light" | "dark";

const STORAGE_KEY = THEME_STORAGE_KEY;
const COLOR_SCHEME_QUERY = "(prefers-color-scheme: dark)";

/**
 * Light/dark theme owner. The theme is backed by external state —
 * localStorage plus the OS color-scheme preference — and the rendered
 * result is the `data-theme` attribute on <html> (so the CSS variable
 * overrides in globals.css re-skin everything, including the SVG
 * palettes that read it).
 *
 * Hydration stays clean by construction: the server always renders
 * `data-theme="light"` and `useSyncExternalStore` renders from
 * `getServerSnapshot()` during hydration, so the React tree matches the
 * server HTML and then re-renders once with the real preference. The
 * pre-paint script in the root layout has already written the correct
 * attribute by then, so there is no flash of the wrong palette.
 */
const listeners = new Set<() => void>();

function emitChange(): void {
  for (const listener of listeners) listener();
}

/**
 * Both external sources are subscribed:
 * - `storage`, so a theme change in another tab follows here;
 * - the `prefers-color-scheme` media query, so a visitor who has NOT
 *   picked a theme follows their OS when they flip it (without this,
 *   `resolveTheme`'s OS fallback was only re-read on an unrelated
 *   re-render).
 */
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === null) emitChange();
  };
  const media = window.matchMedia(COLOR_SCHEME_QUERY);
  const onMediaChange = () => emitChange();
  window.addEventListener("storage", onStorage);
  media.addEventListener("change", onMediaChange);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
    media.removeEventListener("change", onMediaChange);
  };
}

/**
 * In-memory fallback for browsers where localStorage throws (private
 * mode): the toggle still has to work for the current session, and
 * without this the store would keep re-resolving to the OS preference
 * and the theme would never change.
 */
let sessionTheme: Theme | null = null;

function resolveTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark") return "dark";
    if (stored === "light") return "light";
  } catch {
    // Storage unavailable (private mode) — fall through to the session
    // override, then the OS preference.
  }
  if (sessionTheme !== null) return sessionTheme;
  return window.matchMedia(COLOR_SCHEME_QUERY).matches ? "dark" : "light";
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
    sessionTheme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage unavailable — `sessionTheme` above carries the choice
      // for this session instead, so the toggle still works.
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
