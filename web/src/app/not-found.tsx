import Link from "next/link";

/** 404 inside the shell, so the sidebar and engine status stay available. */
export default function NotFound() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface-1/60 px-6 py-14 text-center">
      <h1 className="text-[15px] font-semibold text-ink">No such view</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-ink-dim">
        That route is not part of the lab. The simulator, the incident
        catalogue, import, compare and the provider presets are all in the
        sidebar.
      </p>
      <Link
        href="/"
        className="mt-5 inline-flex h-9 items-center rounded-lg bg-accent px-5 text-[13px] font-semibold text-white shadow-card outline-none transition-colors hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0"
      >
        Back to the simulator
      </Link>
    </div>
  );
}
