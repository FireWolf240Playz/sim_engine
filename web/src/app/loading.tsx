/**
 * Route-transition placeholder. The panels are the app's unit of layout,
 * so the skeleton is panel-shaped: navigating between views reserves the
 * same space the real content will take instead of collapsing to nothing.
 */
export default function Loading() {
  return (
    <div className="flex animate-pulse flex-col gap-5" aria-busy="true">
      <div className="h-7 w-48 rounded-md bg-surface-2" />
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="h-28 rounded-xl border border-line bg-surface-1" />
        <div className="h-28 rounded-xl border border-line bg-surface-1" />
        <div className="h-28 rounded-xl border border-line bg-surface-1" />
      </div>
      <div className="h-40 rounded-xl border border-line bg-surface-1" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
