"use client";

import { useState } from "react";
import type { QuickAction } from "@/lib/types";

interface QuickActionButtonProps {
  action: QuickAction;
  onActivateCitation: (key: string) => void;
  onFollowup: (question: string) => void;
}

export function QuickActionButton({
  action,
  onActivateCitation,
  onFollowup,
}: QuickActionButtonProps) {
  const [copied, setCopied] = useState(false);

  if (action.kind === "copy") {
    return (
      <button
        type="button"
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded hover:bg-slate-100 text-slate-600 transition-colors"
        onClick={async () => {
          await navigator.clipboard.writeText(action.copyText);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
      >
        <span className="material-symbols-outlined text-[15px]">
          {copied ? "check" : "content_copy"}
        </span>
        <span>{copied ? "Copied!" : action.label}</span>
      </button>
    );
  }

  if (action.kind === "preview") {
    return (
      <button
        type="button"
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded hover:bg-slate-100 text-slate-600 transition-colors"
        onClick={() => onActivateCitation(action.citationKey)}
      >
        <span className="material-symbols-outlined text-[15px]">visibility</span>
        <span>{action.label}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
      onClick={() => onFollowup(action.question)}
    >
      <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
      <span>{action.label}</span>
    </button>
  );
}
