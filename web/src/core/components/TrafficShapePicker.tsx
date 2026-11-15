"use client";

import type { TrafficConfig, TrafficShape } from "../types";
import { DEFAULT_SHAPE_PEAK, TRAFFIC_SHAPES, peakRps, shapePath } from "../lib/trafficShape";

/** The preview's size, and the scale every shape is drawn to (the tallest default peak). */
const W = 56;
const H = 20;
const SCALE = 5;

function defaultPeak(shape: TrafficShape): number {
  return shape === "flat" ? 1 : DEFAULT_SHAPE_PEAK[shape];
}

function rps(value: number): string {
  return `${value.toFixed(1)} rps`;
}

/**
 * 3.5b — the shape of the day, as a segmented control that draws each curve
 * (the engine's own, `lib/trafficShape.ts`), all to one scale, so Black
 * Friday visibly towers over a flat day. Under it, the selected shape in
 * words and numbers. Picking a shape does not start a run.
 */
export function TrafficShapePicker({
  traffic,
  onChange,
  disabled,
}: {
  traffic: TrafficConfig;
  onChange: (shape: TrafficShape) => void;
  disabled?: boolean;
}) {
  const value = traffic.shape ?? "flat";
  const selected = TRAFFIC_SHAPES.find((s) => s.name === value) ?? TRAFFIC_SHAPES[0];
  const peak = peakRps(traffic);
  const numbers =
    value === "flat"
      ? `${rps(traffic.base_rps)} throughout.`
      : `${rps(traffic.base_rps)} quiet, peaks at ${rps(peak)}.`;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div
        role="radiogroup"
        aria-label="Traffic shape"
        className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-surface-2 p-1"
      >
        {TRAFFIC_SHAPES.map((shape) => {
          const active = shape.name === value;
          return (
            <button
              key={shape.name}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => onChange(shape.name)}
              className={`flex items-center gap-2 rounded-lg px-3 py-1 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50 ${
                active ? "bg-surface-1 font-semibold text-ink shadow-card" : "font-medium text-ink-dim hover:text-ink"
              }`}
            >
              <svg
                viewBox={`0 0 ${W} ${H}`}
                width={W}
                height={H}
                aria-hidden="true"
                focusable="false"
                className="overflow-visible"
              >
                <path
                  d={shapePath(shape.name, defaultPeak(shape.name), W, H, SCALE)}
                  fill="none"
                  strokeWidth={1.6}
                  strokeLinejoin="round"
                  className={active ? "stroke-accent" : "stroke-ink-dim"}
                />
              </svg>
              {shape.label}
            </button>
          );
        })}
      </div>
      <p className="min-h-5 min-w-0 flex-1 basis-72 text-[13px] leading-5 text-ink-dim">
        {selected.blurb} {numbers}
      </p>
    </div>
  );
}
