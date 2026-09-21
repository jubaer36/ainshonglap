"use client";

export interface InputStatusState {
  /** One line, in plain words: what is happening and roughly how long it takes. */
  text: string;
  /** While true the strip stays put with a spinner. Transient results (a
   *  confidence score, an error) show without one and fade on their own. */
  busy?: boolean;
  /** 0-100 while bytes are still being sent. Absent once the server has them. */
  percent?: number;
  tone?: "info" | "recording" | "error";
}

/**
 * What the app is doing right now, above the input box.
 *
 * The previous version flashed a word in the corner for 3.5 seconds, which is
 * the wrong shape for these operations: reading a dense page with a vision
 * model takes tens of seconds, and a message that has already vanished leaves
 * the reader unsure whether anything is happening at all.
 *
 * The phases are also kept distinct rather than merged into "working…", because
 * they fail differently and take very different amounts of time. Sending a 4 MB
 * photo is bounded and depends on the connection; a model reading that page is
 * open-ended and depends on the provider. Telling someone which one they are
 * waiting on is the difference between patience and a reload.
 */
export function InputStatus({ state }: { state: InputStatusState | null }) {
  if (!state) return null;

  const tone = state.tone ?? "info";
  const palette =
    tone === "error"
      ? "border-rose-200 bg-rose-50 text-rose-800"
      : tone === "recording"
        ? "border-red-200 bg-red-50 text-red-800"
        : "border-slate-200 bg-slate-50 text-slate-600";

  return (
    <div
      // Announced to screen readers as it changes, since it is the only signal
      // that a long operation is under way.
      role="status"
      aria-live="polite"
      className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px] ${palette}`}
    >
      {state.busy &&
        (tone === "recording" ? (
          <span className="relative flex h-2 w-2 flex-shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-600" />
          </span>
        ) : (
          <span
            className="h-3 w-3 flex-shrink-0 rounded-full border-2 border-slate-300 border-t-slate-600 animate-spin"
            aria-hidden="true"
          />
        ))}
      <span className="flex-1 min-w-0 truncate">{state.text}</span>
      {typeof state.percent === "number" && state.percent < 100 && (
        <span className="font-mono text-[10px] text-slate-400 flex-shrink-0 tabular-nums">
          {state.percent}%
        </span>
      )}
    </div>
  );
}
