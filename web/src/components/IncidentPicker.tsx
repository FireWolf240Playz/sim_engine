"use client";

import type { PlaybookInfo } from "@/core/types";

interface Props {
  /** null = clean run; otherwise a playbook key. */
  value: string | null;
  onChange: (playbook: string | null) => void;
  options: PlaybookInfo[];
  disabled?: boolean;
}

function labelOf(name: string): string {
  return name.replace(/_/g, " ");
}

/**
 * The incident selector as a segmented control: every option is visible
 * at a glance, and the selected one carries its description inline below
 * — you always know what you are about to do.
 */
export function IncidentPicker({ value, onChange, options, disabled }: Props) {
  const selected = options.find((p) => p.name === value);

  return (
    <div>
      <div
        role="radiogroup"
        aria-label="Incident"
        className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-surface-2 p-1"
      >
        <SegmentButton
          label="clean run"
          active={value === null}
          disabled={disabled}
          onSelect={() => onChange(null)}
        />
        {options.map((p) => (
          <SegmentButton
            key={p.name}
            label={labelOf(p.name)}
            active={value === p.name}
            disabled={disabled}
            onSelect={() => onChange(p.name)}
          />
        ))}
      </div>
      <p className="mt-2.5 min-h-5 text-[13px] leading-5 text-ink-dim">
        {selected
          ? selected.description
          : "No incident — the clean baseline run, so you know where the headroom is."}
      </p>
    </div>
  );
}

function SegmentButton({
  label,
  active,
  disabled,
  onSelect,
}: {
  label: string;
  active: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      disabled={disabled}
      onClick={onSelect}
      className={`rounded-lg px-3.5 py-1.5 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50 ${
        active
          ? "bg-surface-1 font-semibold text-ink shadow-card"
          : "font-medium text-ink-dim hover:text-ink"
      }`}
    >
      {label}
    </button>
  );
}
