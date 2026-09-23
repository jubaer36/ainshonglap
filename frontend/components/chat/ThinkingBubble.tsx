"use client";

import { useEffect, useState } from "react";
import { BrandMark } from "@/components/brand/BrandMark";

/**
 * What the assistant is doing while you wait.
 *
 * "Searching the statutes…" was shown for every question, and for most of them
 * it was false. Nothing searches anything until a planner has decided what kind
 * of question it is — and for "who are you" or "thanks, that helped" nothing
 * searches at all. A wait message that misdescribes the work is worse than a
 * bare spinner: it tells someone their conversational aside is being run
 * against 1,247 sections of criminal law.
 *
 * **These phases are timed, not observed.** `POST /api/query` is one round trip
 * that returns when everything is finished, so the browser cannot see which
 * stage is running. The timings below come from the measured pipeline — planner
 * 1.3-1.8s (E16), retrieval ~1.4s, then generation and the citation gates — and
 * the order is fixed, so the phase shown is the stage most likely running. The
 * wording is chosen to stay true when a question finishes early: a `meta` or
 * `general` answer returns during the first phase, which is the only one that
 * applies to every question.
 *
 * Streaming the real stage boundaries from the server (the backend already
 * traces them) would make this observed rather than inferred. That is the
 * honest version; this is the cheap one.
 */
interface Phase {
  after: number;
  text: string;
}

const PHASES: Phase[] = [
  // True of every question: the planner runs first, on everything.
  { after: 0, text: "Working out what you're asking…" },
  // Retrieval or an exact catalog fetch, depending on what the planner decided.
  { after: 2500, text: "Finding the provisions that apply…" },
  // Generation, then C2/C3 — the longest stretch, and the one worth naming,
  // since checking each quote against the source is the point of the system.
  { after: 4500, text: "Cross checking with sources…" },
  // Past this something is slow: a cold model load, a rate limit, a long
  // document. Saying so beats a message that looks stuck.
  { after: 20000, text: "Taking longer than usual…" },
];

export function ThinkingBubble() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - started), 400);
    return () => clearInterval(timer);
  }, []);

  const phase = [...PHASES].reverse().find((p) => elapsed >= p.after) ?? PHASES[0];

  return (
    <div className="flex items-start gap-3.5 pr-2">
      <BrandMark size={32} className="rounded-lg shadow-sm mt-0.5" />
      <div className="flex items-center gap-2 text-xs text-slate-500 pt-1.5">
        <span className="flex gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.3s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.15s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" />
        </span>
        <span>{phase.text}</span>
        {elapsed > 8000 && (
          <span className="font-mono text-[10px] text-slate-300 tabular-nums">
            {Math.round(elapsed / 1000)}s
          </span>
        )}
      </div>
    </div>
  );
}
