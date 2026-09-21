"use client";

import { useEffect, useRef, useState } from "react";
import { copyText } from "@/lib/clipboard";

interface CopyableBlockProps {
  /** Resolved on demand, so nothing is flattened until someone copies. */
  getText: () => string;
  className?: string;
  children: React.ReactNode;
}

/**
 * A region that copies itself on double-click.
 *
 * Double-click rather than a button because this wraps the answer's summary —
 * the line most likely to be pasted into a note or a message — and a control
 * sitting on top of it would compete with the text for attention. The gesture
 * is discoverable through the title tooltip and confirmed visibly, since a
 * silent copy is indistinguishable from a click that did nothing.
 *
 * Selection is deliberately not suppressed: double-clicking a word to select it
 * is a normal thing to do, and this copies the block as well rather than
 * instead.
 */
export function CopyableBlock({ getText, className = "", children }: CopyableBlockProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function handleDoubleClick() {
    const ok = await copyText(getText());
    if (!ok) return;
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div
      onDoubleClick={handleDoubleClick}
      title="Double-click to copy"
      className={`relative transition-colors ${className}`}
    >
      {children}
      {copied && (
        <span className="absolute top-1.5 right-2 flex items-center gap-1 rounded bg-slate-900/85 px-1.5 py-0.5 text-[10px] font-medium text-white">
          <span className="material-symbols-outlined text-[12px]">check</span>
          Copied
        </span>
      )}
    </div>
  );
}
