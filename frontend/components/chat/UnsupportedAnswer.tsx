import type { AnswerKind, AssistantMessage } from "@/lib/types";
import { Segments } from "./Segments";

interface UnsupportedAnswerProps {
  message: AssistantMessage;
  onActivateCitation: (key: string) => void;
}

/**
 * Everything that is not a grounded answer — but not all as the same thing.
 *
 * These used to share one amber "can't answer from available sources" panel,
 * which was wrong in both directions. "Which Act did you mean?" is not a
 * failure; it is one question away from an answer, and an alarm-coloured box
 * tells the reader to give up when they should type three more words. And "that
 * statute is not in my corpus" is not the same as "I searched and found
 * nothing" — the first is a boundary, the second is a miss.
 *
 * So: three treatments, distinguished by intent rather than severity.
 *
 *   clarify       blue, a question mark. An invitation, not a warning.
 *   out_of_scope  slate, a boundary marker. Neutral: nothing went wrong.
 *   refused       amber, a warning. Retrieval ran and nothing survived the
 *                 citation gates, which is the case worth flagging.
 */
const STYLES: Record<
  string,
  { wrap: string; head: string; body: string; icon: string; label: string }
> = {
  clarify: {
    wrap: "border-sky-300/80 bg-sky-50",
    head: "text-sky-800",
    body: "text-sky-950",
    icon: "help",
    label: "One more detail needed",
  },
  out_of_scope: {
    wrap: "border-slate-300 bg-slate-50",
    head: "text-slate-700",
    body: "text-slate-800",
    icon: "domain_disabled",
    label: "Outside the indexed Acts",
  },
  refused: {
    wrap: "border-amber-300/80 bg-amber-50",
    head: "text-amber-800",
    body: "text-amber-950",
    icon: "report",
    label: "Can't answer from available sources",
  },
};

export function UnsupportedAnswer({ message, onActivateCitation }: UnsupportedAnswerProps) {
  const style = STYLES[message.kind as AnswerKind] ?? STYLES.refused;

  return (
    <div className={`rounded-xl border p-4 space-y-2.5 ${style.wrap}`}>
      <div className={`flex items-center gap-2 ${style.head}`}>
        <span className="material-symbols-outlined text-[18px]">{style.icon}</span>
        <span className="text-xs font-semibold uppercase tracking-wide">
          {style.label}
        </span>
      </div>
      <div className={`text-sm leading-relaxed space-y-2 font-sans ${style.body}`}>
        {(message.blocks ?? []).map((block, index) => (
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
