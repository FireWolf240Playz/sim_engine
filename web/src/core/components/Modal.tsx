"use client";

import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Accessible dialog primitive — the "full report" surface for the verdict.
 *
 * Behavior contract (WAI-ARIA dialog pattern):
 * - `role="dialog"` + `aria-modal="true"`, labelled via `aria-labelledby`
 *   (pass the id of the panel's heading).
 * - Focus moves into the dialog on open (first focusable, or the panel),
 *   Tab/Shift+Tab are trapped inside, and focus returns to the trigger
 *   element on close.
 * - Escape and a click on the backdrop both close.
 * - The page behind stops scrolling while open.
 *
 * Rendered through a portal on `<body>` so card-level `overflow`/`transform`
 * contexts can never clip it. Animation lives in `globals.css`
 * (`.eleven-modal-*`) and freezes under `prefers-reduced-motion`.
 */
export function Modal({
  open,
  onClose,
  labelledBy,
  children,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  /** id of the element that names this dialog (its heading). */
  labelledBy: string;
  children: ReactNode;
  /** Extra classes for the panel (size, etc.). */
  className?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  const close = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    if (!open) return;

    restoreRef.current = (document.activeElement as HTMLElement | null) ?? null;

    const panel = panelRef.current;
    const focusables = () =>
      panel
        ? Array.from(
            panel.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
            ),
          )
        : [];
    // Set the initial focus after paint (the portal may still be mounting).
    const raf = requestAnimationFrame(() => {
      const first = focusables()[0];
      (first ?? panel)?.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      // Focus trap: wrap Tab at the panel's focusable boundaries.
      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        panel?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      restoreRef.current?.focus();
    };
  }, [open, close]);

  if (!open) return null;

  return createPortal(
    <div
      className="eleven-modal-backdrop fixed inset-0 z-50 flex items-end justify-center bg-surface-0/60 p-4 backdrop-blur-[2px] sm:items-center sm:p-6"
      onMouseDown={(event) => {
        // Backdrop click only — a drag that *starts* inside the panel and
        // releases outside must not close the dialog.
        if (event.target === event.currentTarget) close();
      }}
      aria-hidden="false"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={`eleven-modal-panel max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-line bg-surface-1 shadow-card outline-none ${className}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
