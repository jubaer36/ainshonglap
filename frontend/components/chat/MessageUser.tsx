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
      <div className="flex max-w-xl flex-col items-end gap-1">
        <div className="bg-slate-100 rounded-2xl rounded-tr-sm px-4 py-3 text-slate-900 text-sm leading-relaxed whitespace-pre-wrap">
          {message.text}
        </div>
        {message.contextNote && (
          // This question went ahead before the previous answer's context
          // update landed, so it was read without that exchange. Said, because
          // a follow-up answered without its antecedent can look plain wrong.
          <p className="flex max-w-sm items-start gap-1 text-right text-[11px] leading-snug text-amber-700">
            <span className="material-symbols-outlined mt-px shrink-0 text-[13px]">history_toggle_off</span>
            {message.contextNote}
          </p>
        )}
      </div>
      <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-slate-700 flex-shrink-0 text-xs font-semibold">
        BA
      </div>
    </div>
  );
}
