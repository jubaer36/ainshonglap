"use client";

import { useEffect, useState } from "react";
import { fetchActs } from "./api";
import type { Act } from "./types";

/**
 * The Acts the backend actually holds, for any text that names them.
 *
 * The welcome screen and the "Before you start" notice used to spell out three
 * Acts by name. Loading a fourth left them wrong, and removing one would have
 * left them promising something the assistant no longer had. They now read the
 * same list the statute browser does.
 *
 * Fetched once per page load and shared: several components ask, and the list
 * only changes when an Act is loaded or removed, which a reload picks up.
 */
let pending: Promise<Act[]> | null = null;

export function useActs(): Act[] | null {
  const [acts, setActs] = useState<Act[] | null>(null);
  useEffect(() => {
    let live = true;
    pending ??= fetchActs().catch(() => {
      pending = null;          // let a later mount try again
      return [];
    });
    pending.then((list) => live && setActs(list));
    return () => {
      live = false;
    };
  }, []);
  return acts;
}

/** "the Arms Act, 1878, the Code of Criminal Procedure, 1898 and the Penal Code, 1860". */
export function actListing(acts: Act[]): string {
  const names = acts.map((a) => a.name.replace(/^The\s/, "the "));
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
