/**
 * The persisted-theme contract, shared by the client-side theme store
 * (`core/state/ThemeContext`) and the pre-paint script in the root layout.
 *
 * It lives in its own module — not in ThemeContext — because the root
 * layout is a server component and only needs these two constants, not
 * the client store.
 */

export const THEME_STORAGE_KEY = "eleven-theme";

/**
 * The script injected into `<head>` and executed BEFORE first paint.
 *
 * It resolves the same preference order the client store uses
 * (localStorage, then the OS color-scheme) and writes `data-theme` on
 * `<html>` so the very first painted frame already carries the right
 * palette. Without it, a dark-preference visitor sees a full white paint
 * on every load, because the store can only apply the attribute after
 * hydration.
 *
 * This does NOT desynchronize hydration: `useSyncExternalStore` renders
 * from `getServerSnapshot()` during hydration (so the React tree still
 * matches the server HTML, then re-renders once), and the one attribute
 * this script mutates is covered by `suppressHydrationWarning` on
 * `<html>`. Every statement is wrapped in try/catch because storage
 * throws in private-mode browsers.
 */
export const THEME_PREPAINT_SCRIPT = `(function(){try{var s=localStorage.getItem("${THEME_STORAGE_KEY}");var d=s==="dark"||(s!=="light"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.dataset.theme=d?"dark":"light";}catch(e){}})();`;
