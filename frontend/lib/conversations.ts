import { API_BASE_URL } from "./config";
import { normalizeAssistantMessage } from "./api";
import { accessToken } from "./supabase";
import type { ChatMessage, ConversationMemory, UserMessage } from "./types";

/**
 * Saved conversations.
 *
 * Two modes, and the difference is the whole point of the welcome screen:
 *
 *   guest      nothing is stored. Not on the server, and not in this browser
 *              either — no localStorage, no IndexedDB. The conversation lives
 *              in React state and is gone when the tab closes. Someone who
 *              chose that should be able to close the laptop and leave nothing
 *              behind, which is a real requirement for the questions people
 *              bring here.
 *   signed in  stored in Supabase through the API, which filters every
 *              statement by the user id in a verified token.
 *
 * The browser never queries Supabase directly. Isolation lives in one place —
 * `app/db/conversations.py` — rather than being split between a server filter
 * and a client-side policy that has to agree with it.
 */

export interface SavedConversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  memory: ConversationMemory | null;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function newConversationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * A conversation's name: the first thing the person asked, trimmed.
 *
 * Deliberately not model-generated. A title is glanceable metadata, and
 * spending a call — plus latency and quota — to rephrase a question the person
 * already wrote would be worse in every dimension than showing it.
 */
export function titleFor(messages: ChatMessage[]): string {
  const first = messages.find((m): m is UserMessage => m.role === "user");
  const text = first?.text?.trim() ?? "";
  if (!text) return "New conversation";
  return text.length > 60 ? `${text.slice(0, 57)}…` : text;
}

/**
 * Stored messages come back in the API's own shape — `quick_actions`, not
 * `quickActions` — because the server persists the response exactly as it sent
 * it. Reopening a conversation therefore has to run them through the same
 * normaliser a live answer goes through; skipping it left `quickActions`
 * undefined and crashed the renderer on the first reloaded session.
 */
function restoreMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((entry) => {
    const row = entry as Record<string, unknown>;
    if (row.role === "user") {
      return {
        id: (row.id as string) ?? `u-${Math.random().toString(36).slice(2)}`,
        role: "user",
        text: (row.text as string) ?? "",
      } as ChatMessage;
    }
    return normalizeAssistantMessage(row) as ChatMessage;
  });
}

function toSaved(row: Record<string, unknown>, messages: ChatMessage[] = []): SavedConversation {
  return {
    id: row.id as string,
    title: (row.title as string) ?? "New conversation",
    memory: (row.memory as ConversationMemory) ?? null,
    messages,
    createdAt: Date.parse((row.created_at as string) ?? "") || Date.now(),
    updatedAt: Date.parse((row.updated_at as string) ?? "") || Date.now(),
  };
}

/** Newest first. Empty for a guest — there is nothing stored to list. */
export async function listConversations(): Promise<SavedConversation[]> {
  const headers = await authHeaders();
  if (!headers.Authorization) return [];
  try {
    const response = await fetch(`${API_BASE_URL}/api/conversations`, { headers });
    if (!response.ok) return [];
    const rows = (await response.json()) as Array<Record<string, unknown>>;
    return rows.map((row) => toSaved(row));
  } catch {
    // History is a convenience; a storage outage must never break the chat.
    return [];
  }
}

export async function getConversation(id: string): Promise<SavedConversation | null> {
  const headers = await authHeaders();
  if (!headers.Authorization) return null;
  try {
    const response = await fetch(`${API_BASE_URL}/api/conversations/${id}`, { headers });
    if (!response.ok) return null;
    const row = (await response.json()) as Record<string, unknown>;
    return toSaved(row, restoreMessages(row.messages));
  } catch {
    return null;
  }
}

/**
 * Ask the server to name a conversation from its summary.
 *
 * A separate request on purpose: naming is not on the path to an answer, so it
 * runs after one has already been rendered rather than delaying it.
 *
 * Returns the server's `status` — the caller keeps asking after each answer
 * until it is "named" or "kept":
 *
 *   named     renamed now; `list` is the refreshed conversation list
 *   kept      the title is the reader's, or was named before — stop asking
 *   not_yet   too few answered turns — ask again after the next answer
 *   failed    the naming model was unreachable — ask again after the next one
 *
 * It used to be asked once, after the first answer, when the answer was always
 * "not yet" — and never again, so no conversation was ever named.
 */
export type NamingStatus = "named" | "kept" | "not_yet" | "failed";

export async function nameConversation(
  id: string,
  memory: ConversationMemory | null,
): Promise<{ status: NamingStatus; list: SavedConversation[] | null }> {
  const headers = await authHeaders();
  if (!headers.Authorization) return { status: "kept", list: null };
  try {
    const response = await fetch(`${API_BASE_URL}/api/conversations/${id}/name`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ memory }),
    });
    if (!response.ok) return { status: "failed", list: null };
    const body = (await response.json()) as { status: NamingStatus };
    if (body.status !== "named") return { status: body.status, list: null };
  } catch {
    // A name is cosmetic; a failure here must not disturb the conversation.
    return { status: "failed", list: null };
  }
  return { status: "named", list: await listConversations() };
}

/**
 * Retitle one conversation.
 *
 * Returns the refreshed list, like `deleteConversation`, so the caller sets
 * state once instead of patching a row locally and hoping it matches what the
 * server stored — the title is normalised server-side (trimmed, length-capped,
 * blank falling back to the default), so the local guess can differ.
 */
export async function renameConversation(
  id: string,
  title: string,
): Promise<SavedConversation[]> {
  const headers = await authHeaders();
  if (headers.Authorization) {
    try {
      await fetch(`${API_BASE_URL}/api/conversations/${id}`, {
        method: "PATCH",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
    } catch {
      /* the list is re-read below regardless */
    }
  }
  return listConversations();
}

export async function deleteConversation(id: string): Promise<SavedConversation[]> {
  const headers = await authHeaders();
  if (headers.Authorization) {
    try {
      await fetch(`${API_BASE_URL}/api/conversations/${id}`, { method: "DELETE", headers });
    } catch {
      /* the list is re-read below regardless */
    }
  }
  return listConversations();
}

/** "just now" / "14m" / "3h" / "2d" / a date beyond a week. */
export function relativeTime(ts: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
