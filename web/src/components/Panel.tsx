import type { ReactNode } from "react";

interface Props {
  title: string;
  /** Small right-aligned context line (e.g. a formula note or run meta). */
  aside?: ReactNode;
  children: ReactNode;
}

/**
 * One instrument panel: a white card (hairline border + whisper shadow),
 * a short title and optional right-side context. The app is built from
 * these cards on the near-white canvas.
 */
export function Panel({ title, aside, children }: Props) {
  return (
    <section className="rounded-xl border border-line bg-surface-1 p-5 shadow-card">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        {aside ? <span className="text-xs text-ink-dim">{aside}</span> : null}
      </div>
      {children}
    </section>
  );
}
