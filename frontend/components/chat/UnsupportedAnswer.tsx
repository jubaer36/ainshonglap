import type { AssistantMessage } from "@/lib/types";
import { Segments } from "./Segments";

interface UnsupportedAnswerProps {
  message: AssistantMessage;
  onActivateCitation: (key: string) => void;
}

/**
 * Deliberately distinct from a normal grounded answer: a warm warning
 * color, a dedicated icon, and no citation pills / references — so it
 * never reads as "just another answer" at a glance.
 */
export function UnsupportedAnswer({ message, onActivateCitation }: UnsupportedAnswerProps) {
  return (
    <div className="rounded-xl border border-amber-300/80 bg-amber-50 p-4 space-y-2.5">
      <div className="flex items-center gap-2 text-amber-800">
        <span className="material-symbols-outlined text-[18px]">report</span>
        <span className="text-xs font-semibold uppercase tracking-wide">
          Can&apos;t answer from available sources
        </span>
      </div>
      <div className="text-sm leading-relaxed text-amber-950 space-y-2 font-sans">
        {message.blocks.map((block, index) => (
          <p key={index}>
            <Segments
              segments={block.type === "paragraph" ? block.segments : []}
              onActivateCitation={onActivateCitation}
            />
          </p>
        ))}
      </div>
    </div>
  );
}
