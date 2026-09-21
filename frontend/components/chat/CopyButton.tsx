"use client";

import { useEffect, useRef, useState } from "react";
import { copyText } from "@/lib/clipboard";

interface CopyButtonProps {
  /** Resolved lazily, so a long answer is only flattened when someone copies. */
  getText: () => string;
  label?: string;
  className?: string;
}

/**
 * Copy, with the outcome shown on the button itself.
 *
 * Clipboard writes fail quietly and for reasons the reader cannot see —
 * insecure context, no user gesture, a browser that refuses the API — so the
 * icon reports what actually happened rather than assuming success.
 */
export function CopyButton({ getText, label = "Copy", className = "" }: CopyButtonProps) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function handleClick() {
    const ok = await copyText(getText());
    setState(ok ? "done" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1600);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      title={label}
      aria-label={label}
      className={`flex items-center gap-1 rounded p-0.5 text-slate-400 transition-colors hover:text-slate-700 ${className}`}
    >
      <span
        className={`material-symbols-outlined text-[15px] ${
          state === "done" ? "text-emerald-600" : state === "failed" ? "text-rose-600" : ""
        }`}
      >
        {state === "done" ? "check" : state === "failed" ? "error" : "content_copy"}
      </span>
    </button>
  );
}
