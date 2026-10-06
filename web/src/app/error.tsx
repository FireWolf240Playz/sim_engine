"use client";

import { useEffect } from "react";

/**
 * Route-level error boundary. Without one, any thrown render error in a
 * page (a malformed imported config reaching a component, a bad engine
 * payload) replaced the whole app with a blank white document and no way
 * back short of a reload.
 *
 * `reset()` re-renders the segment, which is enough to recover from a
 * transient failure without losing the rest of the shell.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaced in the browser console for debugging; the UI stays calm.
    console.error("[eleven] route error:", error);
  }, [error]);

  return (
    <div
      role="alert"
      className="rounded-xl border border-sev-crit/40 bg-sev-crit-soft px-5 py-6"
    >
      <h1 className="text-[15px] font-semibold text-sev-crit">
        This view stopped rendering
      </h1>
      <p className="mt-2 max-w-xl text-sm leading-6 text-ink">
        Something in the last result was not shaped the way the UI expected.
        The rest of the app is fine — try the view again, and if it keeps
        failing, re-run the simulation.
      </p>
      <p className="mt-2 font-mono text-[11.5px] leading-5 text-ink-dim">
        {error.message || "unknown error"}
        {error.digest ? ` · ${error.digest}` : ""}
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-4 h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white shadow-card outline-none transition-colors hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0"
      >
        Try this view again
      </button>
    </div>
  );
}
