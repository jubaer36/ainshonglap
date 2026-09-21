import type { AssistantMessage, ContentBlock, Segment } from "./types";

/**
 * Copy to the clipboard, with a fallback for contexts the async API refuses.
 *
 * `navigator.clipboard` needs a secure context and a user gesture, and throws
 * rather than returning false when it is unhappy. The legacy path keeps copy
 * working on plain http, which is where this is often run during development.
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

function segmentsToText(segments: Segment[]): string {
  return segments
    .map((segment) =>
      // A citation pill is a section reference on screen; in plain text it has
      // to read as one, or a pasted answer loses the thing that makes it
      // checkable.
      segment.type === "citation" ? `[${segment.label}]` : segment.text,
    )
    .join("");
}

function blockToText(block: ContentBlock): string {
  if (block.type === "paragraph") return segmentsToText(block.segments);
  const bullet = block.type === "steps";
  return block.items
    .map((item, index) =>
      bullet ? `${index + 1}. ${segmentsToText(item)}` : `- ${segmentsToText(item)}`,
    )
    .join("\n");
}

/**
 * An answer as plain text, suitable for pasting into a document or a message.
 *
 * Citations are kept inline and the sources are listed underneath with their
 * section text, because an answer's value here is that every claim points at a
 * provision — stripped of that it is just confident prose.
 *
 * The disclaimer travels with it for the same reason it is appended
 * server-side: legal information leaving this window should not shed the note
 * that it is not legal advice.
 */
export function assistantMessageToText(message: AssistantMessage): string {
  const parts: string[] = [];
  if (message.heading) parts.push(message.heading);
  const body = message.blocks.map(blockToText).filter(Boolean).join("\n\n");
  if (body) parts.push(body);

  const citations = Object.values(message.citations ?? {});
  if (citations.length > 0) {
    parts.push(
      "Sources:\n" +
        citations
          .map((citation) => `- ${citation.title}${citation.source ? ` (${citation.source})` : ""}`)
          .join("\n"),
    );
  }
  return parts.join("\n\n").trim();
}
