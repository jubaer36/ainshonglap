import type { AnswerKind, ChatMessage } from "./types";

export const DISCLAIMER =
  "This is legal information drawn from the text of the Acts, not legal advice. " +
  "For advice on a specific situation, consult a qualified lawyer.";

/** Answers to a legal question. Small talk (`general`), questions about the
 *  assistant (`meta`), law outside the Acts (`out_of_scope`) and a question
 *  back (`clarify`) do not get it. */
const LEGAL_KINDS: AnswerKind[] = ["legal", "lookup", "outline", "refused"];

/**
 * Which answer carries the disclaimer: the first answer to a legal question
 * in the conversation, once. Derived from the messages rather than stored, so
 * it lands in the same place after a reload, when a saved conversation is
 * reopened, and after a retry.
 */
export function disclaimerMessageId(messages: ChatMessage[]): string | null {
  const first = messages.find((m) => m.role === "assistant" && LEGAL_KINDS.includes(m.kind));
  return first ? first.id : null;
}
