"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ConversationMemory } from "@/lib/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { ImageButton } from "./ImageButton";
import { InputStatus, type InputStatusState } from "./InputStatus";
import { MemoryPanel } from "./MemoryPanel";
import { MicButton } from "./MicButton";
import { ModelPicker } from "./ModelPicker";

interface InputDockProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  /** null = whatever the server has configured as default. */
  model: string | null;
  onModelChange: (alias: string | null) => void;
  /** The rolling conversation summary the next question will be read against. */
  memory: ConversationMemory | null;
  /** The last answer's context update is still running. */
  contextUpdating?: boolean;
  /** Stored conversation id, so an uploaded page is filed with this session. */
  conversationId?: string | null;
  onForgetMemory: () => void;
  onActivateCitation?: (key: string) => void;
}

export function InputDock({
  value,
  onChange,
  onSubmit,
  disabled,
  model,
  onModelChange,
  memory,
  contextUpdating = false,
  conversationId,
  onForgetMemory,
  onActivateCitation,
}: InputDockProps) {
  const [status, setStatus] = useState<InputStatusState | null>(null);
  // A real keyboard and room for the hint: a mouse-driven screen at least 640px
  // wide. Touch devices, and anything narrower, get the short placeholder.
  const keyboard = useMediaQuery("(pointer: fine) and (min-width: 640px)");

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // The box grows with what is typed, up to a limit, then scrolls inside.
  // A fixed two-row box hid most of a pasted case brief; an unlimited one
  // pushed the conversation off a phone screen once the keyboard was up. The
  // limit is 40% of the visible height, capped at 320px, and re-measured when
  // the viewport changes — which on a phone is the keyboard opening.
  const fitToContent = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    const limit = Math.max(96, Math.min(320, Math.round(window.innerHeight * 0.4)));
    el.style.height = "auto";
    const needed = el.scrollHeight;
    el.style.height = `${Math.min(needed, limit)}px`;
    el.style.overflowY = needed > limit ? "auto" : "hidden";
  }, []);

  // Layout effect, not a plain one: the height must be set before the browser
  // paints, or every keystroke flashes the old size for a frame.
  useLayoutEffect(fitToContent, [value, fitToContent]);

  useEffect(() => {
    window.addEventListener("resize", fitToContent);
    return () => window.removeEventListener("resize", fitToContent);
  }, [fitToContent]);
  const clearRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Roughly where a question stops being a question. Matches the backend's
  // document threshold so the hint and the response agree.
  const longDraft = value.trim().length > 700;

  /**
   * A busy state persists until it is replaced — reading a dense page can take
   * tens of seconds, and a message that vanished mid-wait is worse than none.
   * A finished one lingers long enough to be read, then clears itself.
   */
  function showStatus(next: InputStatusState | null) {
    if (clearRef.current) clearTimeout(clearRef.current);
    setStatus(next);
    if (next && !next.busy) {
      clearRef.current = setTimeout(() => setStatus(null), 6000);
    }
  }

  function handleSubmit() {
    if (!value.trim() || disabled) return;
    onSubmit();
  }

  return (
    <div className="p-2 sm:p-4 bg-white border-t border-slate-200 flex-shrink-0 z-20">
      <div className="max-w-3xl mx-auto space-y-2">
        <div className="relative bg-slate-50 hover:bg-slate-100/60 focus-within:bg-white border border-slate-200 focus-within:border-slate-800 focus-within:ring-2 focus-within:ring-slate-900/10 rounded-2xl transition-all shadow-sm">
          <textarea
            ref={textareaRef}
            className="w-full bg-transparent px-3 sm:px-4 pt-3 sm:pt-3.5 pb-2 text-sm text-slate-900 placeholder:text-slate-400 resize-none focus:outline-none leading-relaxed font-sans"
            // The key hint is for keyboards. On a phone it is meaningless and
            // it was the part being cut off, so touch and narrow screens get
            // the question alone.
            placeholder={
              keyboard
                ? "Ask a question about Bangladeshi laws, criminal procedure, bail, or civil remedies…  (Enter to send, Shift+Enter for a new line)"
                : "Ask a legal question…"
            }
            rows={2}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              // Shift+Enter inserts a newline; plain Enter sends. Cmd/Ctrl+Enter
              // keeps working because that is what the placeholder used to
              // imply and muscle memory is cheap to honour.
              if (event.shiftKey) return;
              // A composition (IME) Enter commits the candidate text rather
              // than the message — sending there would cut a word in half for
              // anyone typing Bangla or any other composed script.
              if (event.nativeEvent.isComposing) return;
              event.preventDefault();
              handleSubmit();
            }}
          />
          {!status && longDraft && (
            // Shown before sending, so the choice can be made without spending
            // a call first. Advisory only — nothing is blocked or switched.
            <div className="px-3 pt-1">
              <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600">
                <span className="material-symbols-outlined text-[14px] mt-px">info</span>
                <span>
                  That is a long passage ({value.trim().length.toLocaleString()}{" "}
                  characters). A larger model handles pasted documents better —
                  pick one from the model list if the answer looks thin.
                </span>
              </div>
            </div>
          )}
          {status && (
            <div className="px-3 pt-1">
              <InputStatus state={status} />
            </div>
          )}
          <div className="flex items-center justify-between gap-2 px-2 sm:px-3 pb-2 sm:pb-2.5 pt-1">
            <div className="flex items-center gap-1 text-slate-500 min-w-0">
              <ModelPicker value={model} onChange={onModelChange} disabled={disabled} />
              <MemoryPanel
                memory={memory}
                updating={contextUpdating}
                onForget={onForgetMemory}
                onActivateCitation={onActivateCitation}
              />
            </div>
            <div className="flex items-center gap-1 sm:gap-1.5 flex-shrink-0">
              <ImageButton
                disabled={disabled}
                conversationId={conversationId}
                onStatus={showStatus}
                // Read text goes in the box, never straight to the pipeline: a
                // vision model fails plausibly ("302A" -> "302") and nothing
                // downstream can catch a correctly-executed wrong question.
                onText={(text) => {
                  onChange(value ? `${value.trim()}\n\n${text}` : text);
                  textareaRef.current?.focus();
                }}
              />
              <MicButton
                disabled={disabled}
                onStatus={showStatus}
                // The transcript is placed in the box, never submitted. A
                // misheard section number would otherwise become a confidently
                // grounded answer about a different offence, and nothing
                // downstream can catch that.
                onTranscript={(text) => {
                  onChange(value ? `${value.trim()} ${text}` : text);
                  textareaRef.current?.focus();
                }}
              />
              <button
                type="button"
                className="flex items-center gap-1.5 px-2.5 sm:px-3.5 py-1.5 rounded-xl bg-slate-900 text-white hover:bg-slate-800 text-xs font-medium shadow-sm transition-transform active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={handleSubmit}
                disabled={disabled || !value.trim()}
                title="Ask"
                aria-label="Ask"
              >
                <span className="hidden sm:inline">Ask</span>
                <span className="material-symbols-outlined text-[15px]">arrow_upward</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
