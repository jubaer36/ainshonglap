"use client";

import { actListing, useActs } from "@/lib/useActs";

interface FirstTimeNoticeProps {
  onDismiss: () => void;
}

/**
 * What to know before the first question, in three lines — including the one
 * disclaimer the app shows. Answers no longer repeat it under every reply.
 */
export function FirstTimeNotice({ onDismiss }: FirstTimeNoticeProps) {
  const acts = useActs();
  const sources = acts && acts.length ? actListing(acts) : "the Acts indexed here";
  return (
    <div className="rounded-xl border border-slate-200/90 bg-slate-50 p-3.5 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-semibold text-slate-900">
          <span className="material-symbols-outlined text-[16px] text-slate-500">info</span>
          Before you start
        </span>
        <button
          type="button"
          className="rounded p-0.5 text-slate-400 hover:text-slate-700"
          onClick={onDismiss}
          title="Dismiss notice"
        >
          <span className="material-symbols-outlined text-[15px]">close</span>
        </button>
      </div>
      <ul className="space-y-1 leading-relaxed text-slate-600">
        <li>
          <span className="font-medium text-slate-800">Sources:</span> answers come only from{" "}
          {sources}.
        </li>
        <li>
          <span className="font-medium text-slate-800">Citations:</span> click a section pill to
          read the text it cites. If the Acts don&apos;t cover a question, it says so.
        </li>
        <li>
          <span className="font-medium text-slate-800">Not legal advice:</span> this is legal
          information from the text of the Acts. For your situation, consult a qualified lawyer.
        </li>
      </ul>
    </div>
  );
}
