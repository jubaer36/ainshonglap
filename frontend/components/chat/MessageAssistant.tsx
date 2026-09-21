import type { AssistantMessage } from "@/lib/types";
import { AnswerKindBadge } from "./AnswerKindBadge";
import { BlockRenderer } from "./BlockRenderer";
import { QuickActionButton } from "./QuickActionButton";
import { UnsupportedAnswer } from "./UnsupportedAnswer";

interface MessageAssistantProps {
  message: AssistantMessage;
  onActivateCitation: (key: string) => void;
  onFollowup: (question: string) => void;
}

export function MessageAssistant({
  message,
  onActivateCitation,
  onFollowup,
}: MessageAssistantProps) {
  return (
    <div className="flex items-start gap-3.5 pr-2">
      <div className="w-8 h-8 rounded-lg bg-slate-900 text-white flex items-center justify-center flex-shrink-0 font-serif font-bold text-xs shadow-sm mt-0.5">
        আইন
      </div>
      <div className="flex-1 space-y-3">
        <div className="flex items-center justify-between gap-3 pb-1 border-b border-slate-100 text-xs">
          <span className="text-xs font-medium text-slate-800">{message.heading}</span>
          <span className="flex items-center gap-2 flex-shrink-0">
            <AnswerKindBadge kind={message.kind} />
            <span className="text-[11px] text-slate-400">{message.subheading}</span>
          </span>
        </div>

        {message.unsupported ? (
          <UnsupportedAnswer message={message} onActivateCitation={onActivateCitation} />
        ) : (
          <>
            <div className="text-sm leading-relaxed text-slate-800 space-y-3 font-sans">
              {message.blocks.map((block, index) => (
                <BlockRenderer
                  key={index}
                  block={block}
                  onActivateCitation={onActivateCitation}
                />
              ))}
            </div>

            {message.quickActions.length > 0 && (
              <div className="flex items-center gap-2 pt-1 text-xs text-slate-500">
                {message.quickActions.map((action, index) => (
                  <QuickActionButton
                    key={index}
                    action={action}
                    onActivateCitation={onActivateCitation}
                    onFollowup={onFollowup}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
