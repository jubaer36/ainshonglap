"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Bounds {
  min: number;
  max: number;
  initial: number;
}

/**
 * A panel width the reader can drag, with its bounds.
 *
 * Held in React state only, never in localStorage. Guest mode promises that
 * nothing is written to the browser, and a stored width would be the one
 * exception to it — so panels return to their default on reload.
 */
export function useResizableWidth({ min, max, initial }: Bounds) {
  const [width, setRaw] = useState(initial);
  // Stable across renders: the handle subscribes window listeners with this in
  // their dependencies, and a fresh function each render would re-subscribe
  // them on every pointer move of a drag.
  const setWidth = useCallback(
    (w: number | ((prev: number) => number)) =>
      setRaw((prev) => {
        const next = typeof w === "function" ? w(prev) : w;
        return Math.min(max, Math.max(min, next));
      }),
    [min, max],
  );
  return { width, setWidth, min, max, initial };
}

interface ResizeHandleProps {
  /** Which edge of the panel the handle sits on. A left-edge handle widens the
   *  panel as it moves left, so the drag direction is inverted. */
  edge: "left" | "right";
  width: number;
  min: number;
  max: number;
  initial: number;
  onResize: (w: number | ((prev: number) => number)) => void;
  label: string;
  /** The breakpoint from which the panel is docked, and so resizable. */
  from?: "lg" | "xl";
}

/**
 * The draggable edge of a panel. Desktop only — below `lg` both panels are
 * overlays, where a width the reader chose would just cover more of the chat.
 *
 * Drag to resize, double-click to reset, arrow keys to step (Shift for bigger
 * steps). It is a `separator` with its value exposed, so it is reachable and
 * operable without a mouse.
 */
export function ResizeHandle({ edge, width, min, max, initial, onResize, label, from = "lg" }: ResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const startRef = useRef<{ x: number; width: number } | null>(null);
  const sign = edge === "right" ? 1 : -1;

  const onPointerMove = useCallback(
    (event: PointerEvent) => {
      const start = startRef.current;
      if (!start) return;
      onResize(start.width + sign * (event.clientX - start.x));
    },
    [onResize, sign],
  );

  const stop = useCallback(() => {
    startRef.current = null;
    setDragging(false);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    // Listening on the window, not the handle: the pointer leaves an 8px strip
    // almost immediately, and a drag that stops tracking when it does is one
    // that jumps. Text selection is suppressed for the same duration, or every
    // drag highlights half the chat.
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stop);
    const previous = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stop);
      document.body.style.userSelect = previous;
      document.body.style.cursor = "";
    };
  }, [dragging, onPointerMove, stop]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={(event) => {
        event.preventDefault();
        startRef.current = { x: event.clientX, width };
        setDragging(true);
      }}
      onDoubleClick={() => onResize(initial)}
      onKeyDown={(event) => {
        const step = (event.shiftKey ? 48 : 16) * sign;
        if (event.key === "ArrowRight") {
          event.preventDefault();
          onResize((w) => w + step);
        } else if (event.key === "ArrowLeft") {
          event.preventDefault();
          onResize((w) => w - step);
        }
      }}
      title="Drag to resize · double-click to reset"
      // Written out in full rather than built from `from`: Tailwind only
      // generates classes it can find as literal strings.
      className={`hidden ${from === "xl" ? "xl:block" : "lg:block"} absolute top-0 h-full w-2 z-30 cursor-col-resize group/handle focus:outline-none ${
        edge === "right" ? "-right-1" : "-left-1"
      }`}
    >
      <div
        className={`mx-auto h-full w-px transition-colors ${
          dragging
            ? "bg-slate-400"
            : "bg-transparent group-hover/handle:bg-slate-300 group-focus/handle:bg-slate-400"
        }`}
      />
    </div>
  );
}
