"use client";

import { useEffect, useRef, useState } from "react";
import type { ConversationMemory } from "@/lib/types";

interface MemoryPanelProps {
  memory: ConversationMemory | null;
  /** The last answer is still being folded into the context. */
  updating?: boolean;
  onForget: () => void;
  onActivateCitation?: (key: string) => void;
}

/**
 * What the assistant remembers, on demand.
 *
 * The conversation memory is a rolling summary: after each turn, the previous
 * summary plus the new question and answer go to a small model and come back as
 * one short paragraph, which is the context every following question is read
 * against. That makes it the most consequential hidden state in the system: a
 * follow-up is resolved against this text, so if the summary drifts, the
 * assistant answers a question subtly different from the one asked, with full
 * confidence and real citations.
 *
 * Hidden state that steers answers should be inspectable, so this is shown
 * verbatim rather than described. Two things are worth noticing when reading it:
 *
 *   drift      the summary is a summary of a summary by turn three. Prose
 *              blurring is expected; a changed FACT is not.
 *   sections   these keys are copied from the answers' own citations by the
 *              server, never written by the summariser, so they are the part
 *              that cannot be paraphrased away. If the prose and the sections
 *              disagree, trust the sections.
 */
export function MemoryPanel({ memory, updating = false, onForget, onActivateCitation }: MemoryPanelProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocumentClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onDocumentClick);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  const turns = memory?.turns ?? 0;
  const hasMemory = Boolean(
    memory && (memory.summary || memory.sections.length || memory.last_question),
  );
  const stale = Boolean(memory?.error);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={
          hasMemory
            ? "What the assistant remembers from this conversation"
            : "Nothing remembered yet. Ask a question first"
        }
        className={`flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-md border text-[11px] transition-colors ${
          stale
            ? "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100"
            : hasMemory
              ? "border-slate-200 text-slate-600 hover:bg-slate-50"
              : "border-slate-200 text-slate-400 hover:bg-slate-50"
        }`}
      >
        <span className={`material-symbols-outlined text-[14px] ${updating ? "animate-spin motion-reduce:animate-none" : ""}`}>
          {updating ? "progress_activity" : stale ? "running_with_errors" : "psychology"}
        </span>
        {/* Full label from sm up. On a phone the icon stands alone, with the
            turn count once there is one; the label was the widest thing in a
            toolbar that did not fit. */}
        <span className="hidden sm:inline font-medium">
          {updating
            ? "updating context…"
            : hasMemory ? `context · ${turns} turn${turns === 1 ? "" : "s"}` : "no context"}
        </span>
        {hasMemory && <span className="sm:hidden font-medium">{turns}</span>}
      </button>

      {open && (
        // Phone: a sheet across the screen, above the input, so it never runs
        // off the edge. From sm up: the popover above the chip.
        <div className="fixed inset-x-3 bottom-36 z-50 max-h-[60vh] overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg sm:absolute sm:inset-x-auto sm:bottom-full sm:left-0 sm:mb-1 sm:w-[26rem] sm:max-h-96">
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100">
            <span className="text-xs font-semibold text-slate-800">
              What the assistant remembers
            </span>
            {hasMemory && (
              <button
                type="button"
                onClick={() => {
                  onForget();
                  setOpen(false);
                }}
                className="text-[11px] text-slate-500 hover:text-rose-600"
                title="Start the next question with no conversation context. The messages stay on screen."
              >
                Forget
              </button>
            )}
          </div>

          {!hasMemory ? (
            <div className="px-3 py-4 text-[11px] text-slate-500">
              Nothing yet. After your first answer, a short running summary
              appears here. It is what follow-up questions like “what if he had
              survived?” are resolved against.
            </div>
          ) : (
            <div className="p-3 space-y-3">
              {stale && (
                // The summary failed to update, so it describes an earlier turn
                // than the one on screen. Said plainly: a memory that has quietly
                // stopped moving is worse than one that is absent.
                <div className="rounded-md bg-amber-50 border border-amber-200 px-2 py-1.5 text-[11px] text-amber-900">
                  <span className="font-medium">Not up to date. </span>
                  The summariser failed on the last turn ({memory?.error}), so
                  the summary reflects the conversation before it. The previous
                  exchange below is current: it is copied, not summarised.
                </div>
              )}

              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                  Running summary
                </div>
                <p className="text-[12px] leading-relaxed text-slate-700 whitespace-pre-wrap">
                  {memory?.summary || "(empty)"}
                </p>
              </div>

              {memory?.last_question && (
                // Shown because the next question is read against it: the
                // exact previous question and answer, kept word for word
                // alongside the summary rather than folded into it.
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                    Previous exchange, word for word
                  </div>
                  <div className="space-y-1.5 text-[12px] leading-relaxed">
                    <p className="text-slate-700">
                      <span className="font-medium text-slate-900">You: </span>
                      {memory.last_question}
                    </p>
                    <p className="text-slate-600 whitespace-pre-wrap max-h-40 overflow-y-auto">
                      <span className="font-medium text-slate-900">Answer: </span>
                      {memory.last_answer || "(not answered)"}
                    </p>
                  </div>
                </div>
              )}

              {memory && memory.sections.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                    Sections cited so far
                    <span className="ml-1 normal-case tracking-normal text-slate-300">
                      (kept exactly, not summarised)
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {memory.sections.map((key) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => onActivateCitation?.(key)}
                        className="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 font-mono text-[10px] text-slate-600 hover:border-slate-400 hover:text-slate-900"
                        title={`Open ${key}`}
                      >
                        {key}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <p className="text-[10px] leading-relaxed text-slate-400 border-t border-slate-100 pt-2">
                This summary is what the assistant reads before answering your
                next question. It is rewritten after every turn, so details
                unrelated to the thread are dropped on purpose.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
