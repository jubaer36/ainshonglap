import { useSyncExternalStore } from "react";

/**
 * Whether a CSS media query matches, kept in sync as the window changes.
 *
 * `useSyncExternalStore` rather than state set in an effect: the browser is the
 * source of truth, and this reads it directly instead of copying it into React
 * a render late. The server has no window, so it answers `false`; components
 * should treat `false` as the narrow / touch case, which is the safe default to
 * render first.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
