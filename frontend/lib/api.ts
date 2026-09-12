import type {
  AssistantMessage,
  ChatMessage,
  Citation,
  IconColorToken,
  QuickAction,
  Reference,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Full citation library, independent of any conversation (for the sidebar's statute browser). */
export async function fetchCitationLibrary(): Promise<Record<string, Citation>> {
  const response = await fetch(`${API_BASE_URL}/api/citations`);
  if (!response.ok) {
    throw new Error(`Fetching citations failed with status ${response.status}`);
  }
  return response.json();
}

/**
 * The only place in the frontend that knows about the backend. Components
 * call this and never fetch directly, so swapping the backend's internals
 * (mock responses -> real retrieval/LLM) never touches UI code.
 */
export async function sendQuery(
  question: string,
  history: ChatMessage[],
): Promise<AssistantMessage> {
  const response = await fetch(`${API_BASE_URL}/api/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      history: history.map((message) => ({
        id: message.id,
        role: message.role,
        text: message.role === "user" ? message.text : undefined,
      })),
    }),
  });

  if (!response.ok) {
    throw new Error(`Query failed with status ${response.status}`);
  }

  const raw = await response.json();
  return normalizeAssistantMessage(raw);
}

// The backend's Pydantic models serialize as snake_case; the frontend
// types are camelCase. This is the single conversion point.
function normalizeAssistantMessage(raw: Record<string, unknown>): AssistantMessage {
  return {
    id: raw.id as string,
    role: "assistant",
    heading: raw.heading as string,
    subheading: raw.subheading as string,
    unsupported: Boolean(raw.unsupported),
    blocks: raw.blocks as AssistantMessage["blocks"],
    citations: raw.citations as AssistantMessage["citations"],
    references: ((raw.references as Record<string, unknown>[]) ?? []).map(normalizeReference),
    quickActions: ((raw.quick_actions as Record<string, unknown>[]) ?? []).map(
      normalizeQuickAction,
    ),
  };
}

function normalizeReference(raw: Record<string, unknown>): Reference {
  return {
    title: raw.title as string,
    subtitle: raw.subtitle as string,
    url: raw.url as string,
    icon: raw.icon as string,
    iconColor: raw.icon_color as IconColorToken,
  };
}

function normalizeQuickAction(raw: Record<string, unknown>): QuickAction {
  switch (raw.kind) {
    case "copy":
      return { kind: "copy", label: raw.label as string, copyText: raw.copy_text as string };
    case "preview":
      return {
        kind: "preview",
        label: raw.label as string,
        citationKey: raw.citation_key as string,
      };
    case "followup":
      return { kind: "followup", label: raw.label as string, question: raw.question as string };
    default:
      throw new Error(`Unknown quick action kind: ${String(raw.kind)}`);
  }
}
