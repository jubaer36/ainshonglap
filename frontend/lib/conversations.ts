import type { ChatMessage, ConversationMemory, UserMessage } from "./types";

/**
 * Saved conversations, in the browser.
 *
 * Kept in `localStorage` rather than on the server, for the same reason the
 * conversation memory is client-held: these are people's legal questions —
 * "three men beat a man and he died", "my employer has not paid me" — and a
 * server that never receives them cannot leak, subpoena or mis-handle them.
 * The cost is that history does not follow you to another device, which is the
 * right trade for this kind of content.
 *
 * Each record carries its `memory` as well as its messages, so reopening a
 * conversation restores what the assistant remembered, not just what was said.
 * Without that, a follow-up after reopening would silently lose its context.
 */
export interface SavedConversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  memory: ConversationMemory | null;
}

const KEY = "ainshonglap.conversations.v1";

/** Oldest are dropped past this. Bounded because `localStorage` is a few MB per
 *  origin and a long legal answer with citations is not small. */
const MAX_CONVERSATIONS = 50;

export function newConversationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * A conversation's name: the first thing the person asked, trimmed.
 *
 * Deliberately not model-generated. A title is glanceable metadata, and
 * spending a call — plus the latency and the quota — to rephrase a question the
 * person already wrote would be worse in every dimension than showing it.
 */
export function titleFor(messages: ChatMessage[]): string {
  const first = messages.find((m): m is UserMessage => m.role === "user");
  const text = first?.text?.trim() ?? "";
  if (!text) return "New conversation";
  return text.length > 60 ? `${text.slice(0, 57)}…` : text;
}

/** Every read and write is guarded: storage throws in private windows, when
 *  site data is blocked, and when the quota is full. History is a convenience;
 *  losing it must never break the chat. */
function read(): SavedConversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as SavedConversation[]) : [];
  } catch {
    return [];
  }
}

function write(list: SavedConversation[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_CONVERSATIONS)));
  } catch {
    // Most likely the quota is full. Drop the oldest half and try once more
    // rather than losing the conversation in progress.
    try {
      window.localStorage.setItem(
        KEY,
        JSON.stringify(list.slice(0, Math.floor(MAX_CONVERSATIONS / 2))),
      );
    } catch {
      /* storage unavailable; history simply does not persist this session */
    }
  }
}

/** Newest first. */
export function listConversations(): SavedConversation[] {
  return read().sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getConversation(id: string): SavedConversation | null {
  return read().find((c) => c.id === id) ?? null;
}

/**
 * Insert or update. Conversations with no messages are not saved, so opening
 * the app and clicking "New" does not litter the list with empty entries.
 */
export function saveConversation(
  id: string,
  messages: ChatMessage[],
  memory: ConversationMemory | null,
): SavedConversation[] {
  if (messages.length === 0) return listConversations();
  const now = Date.now();
  const list = read();
  const existing = list.find((c) => c.id === id);
  const record: SavedConversation = {
    id,
    title: existing?.title || titleFor(messages),
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    messages,
    memory,
  };
  const next = [record, ...list.filter((c) => c.id !== id)].sort(
    (a, b) => b.updatedAt - a.updatedAt,
  );
  write(next);
  return next;
}

export function deleteConversation(id: string): SavedConversation[] {
  const next = read().filter((c) => c.id !== id);
  write(next);
  return next.sort((a, b) => b.updatedAt - a.updatedAt);
}

export function clearConversations(): SavedConversation[] {
  write([]);
  return [];
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
