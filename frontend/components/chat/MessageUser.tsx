"use client";

import type { UserMessage } from "@/lib/types";
import { CopyButton } from "./CopyButton";

export function MessageUser({ message }: { message: UserMessage }) {
  return (
    <div className="group flex items-start justify-end gap-3 pl-12">
      {/* Hidden until hover, and revealed on focus so it stays reachable by
          keyboard rather than being effectively mouse-only. */}
      <CopyButton
        getText={() => message.text}
        label="Copy this question"
        className="mt-3 opacity-0 group-hover:opacity-100 focus:opacity-100"
      />
      <div className="bg-slate-100 rounded-2xl rounded-tr-sm px-4 py-3 max-w-xl text-slate-900 text-sm leading-relaxed whitespace-pre-wrap">
        {message.text}
      </div>
      <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-slate-700 flex-shrink-0 text-xs font-semibold">
        BA
      </div>
    </div>
  );
}
