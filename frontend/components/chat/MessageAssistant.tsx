import type { AssistantMessage } from "@/lib/types";
import { assistantMessageToText } from "@/lib/clipboard";
import { AnswerKindBadge } from "./AnswerKindBadge";
import { CopyButton } from "./CopyButton";
import { CopyableBlock } from "./CopyableBlock";
import { BlockRenderer } from "./BlockRenderer";
import { QuickActionButton } from "./QuickActionButton";
import { UnsupportedAnswer } from "./UnsupportedAnswer";
import { BrandMark } from "@/components/brand/BrandMark";

interface MessageAssistantProps {
  message: AssistantMessage;
  onActivateCitation: (key: string) => void;
  onFollowup: (question: string) => void;
  /** Re-run the question this answer came from, optionally on another model. */
  onRetry?: (withModel?: string) => void;
  canRetry?: boolean;
}

export function MessageAssistant({
  message,
  onActivateCitation,
  onFollowup,
  onRetry,
  canRetry = true,
}: MessageAssistantProps) {
  // Retry is offered where asking again could plausibly succeed: a transport
  // failure, a generator that was unreachable, a refusal that came from
  // retrieval missing rather than from the corpus genuinely not covering it.
  //
  // NOT on `out_of_scope` or `clarify`. Those are settled: asking the same
  // question again cannot put the Dowry Prohibition Act into the corpus, and a
  // clarification is waiting on the reader, not on the system. A retry button
  // there would invite pulling a different answer out of an unchanged pipeline.
  const retryable =
    Boolean(onRetry) && (message.kind === "refused" || Boolean(message.retryQuestion));
  return (
    <div className="flex items-start gap-3.5 pr-2">
      <BrandMark size={32} className="rounded-lg shadow-sm mt-0.5" />
      <div className="group flex-1 space-y-3">
        {/* Metadata only. The summary used to share this row and was squeezed
            into whatever width the badges left it — a one-line answer wrapped
            into a tall narrow column beside them. It now owns the full width
            below. */}
        <div className="flex items-center gap-2 flex-wrap text-xs text-slate-400">
          <AnswerKindBadge kind={message.kind} />
          <span className="text-[11px]">{message.subheading}</span>
          {message.generator && (
            // Which model wrote this — not always the one selected: a pasted
            // document suggests a larger one, and an exhausted route falls back
            // to another provider.
            <span
              className="font-mono text-[10px] ml-auto hidden sm:inline"
              title={`Answered by ${message.generator}`}
            >
              {message.generator}
            </span>
          )}
        </div>

        {message.heading && !message.unsupported && (
          // The answer in one line, across the full column. Darker than the
          // body so the eye lands on it first, and double-clickable because it
          // is the part most likely to be pasted somewhere else.
          <CopyableBlock
            getText={() => message.heading}
            className="rounded-lg border border-slate-200 bg-slate-100/70 px-3.5 py-2.5 hover:bg-slate-100"
          >
            <p className="text-sm font-medium leading-relaxed text-slate-900">
              {message.heading}
            </p>
          </CopyableBlock>
        )}

        {message.notice && (
          // A suggestion, not a substitution. The answer above is real; a
          // larger model would read a long passage better, and the button
          // re-asks with one rather than doing it unannounced.
          <div className="flex items-start gap-1.5 rounded-lg bg-slate-50 border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600">
            <span className="material-symbols-outlined text-[14px] mt-px">info</span>
            <span className="flex-1">
              {message.notice}
              {message.suggestedModel && onRetry && (
                <button
                  type="button"
                  onClick={() => onRetry(message.suggestedModel ?? undefined)}
                  disabled={!canRetry}
                  className="ml-1.5 underline underline-offset-2 font-medium text-slate-800 hover:text-slate-950 disabled:opacity-50 disabled:no-underline"
                >
                  Ask again with {message.suggestedModel}
                </button>
              )}
            </span>
          </div>
        )}

        {message.generatorSubstituted && (
          // The requested model was out of quota and another answered. Stated,
          // because "which model wrote this" changes how much to trust it and
          // a silent swap hides that.
          <div className="flex items-start gap-1.5 rounded-lg bg-amber-50 border border-amber-200/70 px-2.5 py-1.5 text-[11px] text-amber-900">
            <span className="material-symbols-outlined text-[14px] mt-px">swap_horiz</span>
            <span>
              <span className="font-medium">{message.generatorSubstituted}</span> is out of
              quota — answered by <span className="font-medium">{message.generator}</span>.
            </span>
          </div>
        )}

        {message.resolvedQuestion && (
          // A follow-up was resolved against the conversation summary, and the
          // rewrite is what was actually answered. Shown because the pipeline
          // cannot detect a correctly-executed WRONG question: a bad rewrite
          // produces a fully grounded, fully cited answer to something the
          // reader never asked. Seeing it is the only check there is.
          <div className="flex items-start gap-1.5 rounded-lg bg-sky-50 border border-sky-200/70 px-2.5 py-1.5 text-[11px] text-sky-900">
            <span className="material-symbols-outlined text-[14px] mt-px">history</span>
            <span>
              <span className="font-medium">Answered using earlier context: </span>
              {message.resolvedQuestion}
            </span>
          </div>
        )}

        {message.unsupported ? (
          <>
            <UnsupportedAnswer message={message} onActivateCitation={onActivateCitation} />
            {retryable && (
              <button
                type="button"
                onClick={() => onRetry?.()}
                disabled={!canRetry}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-200 text-[11px] text-slate-600 hover:bg-slate-50 hover:text-slate-900 disabled:opacity-50 disabled:cursor-not-allowed"
                title="Ask the same question again. The failed attempt is discarded and nothing from it is kept in the conversation's memory."
              >
                <span className="material-symbols-outlined text-[14px]">refresh</span>
                <span>Try again</span>
              </button>
            )}
          </>
        ) : (
          <>
            <div className="text-sm leading-relaxed text-slate-800 space-y-3 font-sans">
              {(message.blocks ?? []).map((block, index) => (
                <BlockRenderer
                  key={index}
                  block={block}
                  onActivateCitation={onActivateCitation}
                />
              ))}
            </div>

            {(message.quickActions?.length ?? 0) > 0 && (
              <div className="flex items-center gap-2 pt-1 text-xs text-slate-500">
                {(message.quickActions ?? []).map((action, index) => (
                  <QuickActionButton
                    key={index}
                    action={action}
                    onActivateCitation={onActivateCitation}
                    onFollowup={onFollowup}
                  />
                ))}
              </div>
            )}

            {/* Foot of the answer, where someone is once they have read it —
                rather than in the header, where it sat before they had. */}
            <div className="flex items-center gap-1 pt-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <CopyButton
                // Flattened on demand: the answer, its inline citations, and the
                // sources underneath — an answer without its provisions is just
                // confident prose.
                getText={() => assistantMessageToText(message)}
                label="Copy this answer"
              />
              <span className="text-[10px] text-slate-400">Copy answer</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
