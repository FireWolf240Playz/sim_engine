"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useTheme, type Theme } from "../state/ThemeContext";

/**
 * The token names a chart needs as real color strings.
 *
 * Recharts writes most of these into SVG presentation attributes and
 * inline styles, so they have to be resolved values rather than
 * `var(...)` references. Everything here is an existing token from
 * globals.css — this module reads them, it does not define them.
 */
const COLOR_TOKENS = [
  "--color-line",
  "--color-ink",
  "--color-ink-dim",
  "--color-surface-1",
  "--color-accent",
  "--color-sev-crit",
] as const;

const NUMBER_TOKENS = ["--chart-grid-opacity"] as const;

export type ColorToken = (typeof COLOR_TOKENS)[number];
export type NumberToken = (typeof NUMBER_TOKENS)[number];

export interface ThemeTokens {
  color: Record<ColorToken, string>;
  number: Record<NumberToken, number>;
}

function readTokens(): ThemeTokens {
  const style = getComputedStyle(document.documentElement);
  const color = {} as Record<ColorToken, string>;
  for (const token of COLOR_TOKENS) {
    color[token] = style.getPropertyValue(token).trim();
  }
  const number = {} as Record<NumberToken, number>;
  for (const token of NUMBER_TOKENS) {
    const parsed = Number.parseFloat(style.getPropertyValue(token));
    number[token] = Number.isFinite(parsed) ? parsed : 1;
  }
  return { color, number };
}

/**
 * One resolved set per theme. `useSyncExternalStore` requires a snapshot
 * that is referentially stable while the underlying state has not
 * changed — re-reading the stylesheet on every render would return a
 * fresh object each time and spin React forever.
 */
let cached: { theme: Theme; tokens: ThemeTokens } | null = null;

function snapshotFor(theme: Theme): ThemeTokens {
  if (cached !== null && cached.theme === theme) return cached.tokens;
  const tokens = readTokens();
  cached = { theme, tokens };
  return tokens;
}

/**
 * The stylesheet is a read-only external source for this hook's
 * purposes: the one thing that changes its values is the theme, and that
 * already arrives as a re-render from `useTheme` (which re-reads the
 * snapshot). So there is nothing to emit, and `subscribe` exists to
 * satisfy the store contract.
 */
function subscribe(): () => void {
  return () => {};
}

function getServerSnapshot(): null {
  return null;
}

/**
 * Resolves the design tokens a chart library needs into concrete values
 * for the ACTIVE theme, re-reading them whenever the theme flips.
 *
 * Returns `null` on the server and during hydration, because the values
 * can only come from the live stylesheet; a consumer renders nothing
 * until they arrive and should reserve its own height so that costs no
 * layout shift.
 *
 * This replaces the hand-copied light/dark hex palettes the chart
 * components used to carry: globals.css is now the only place a theme
 * color is written down.
 */
export function useThemeTokens(): ThemeTokens | null {
  const { theme } = useTheme();
  const getSnapshot = useCallback(() => snapshotFor(theme), [theme]);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
