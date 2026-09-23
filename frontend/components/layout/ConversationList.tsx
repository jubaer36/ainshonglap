"use client";

import { useEffect, useRef, useState } from "react";
import { relativeTime, type SavedConversation } from "@/lib/conversations";

interface ConversationListProps {
  conversations: SavedConversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}

/**
 * Saved conversations, newest first.
 *
 * Each row shows its title and how long ago it was touched. The default title
 * is the first question asked rather than a generated summary: the person wrote
 * it, they will recognise it, and a model call to rephrase what they already
 * typed would cost latency and quota to make recall harder.
 *
 * That default is a guess, though, and often the wrong label for a conversation
 * that moved on — so a row can be retitled in place. Editing happens inside the
 * row rather than in a dialog, because renaming one item in a list is not worth
 * losing the list to.
 *
 * Deleting asks first, for the same reason and in the same place. A conversation
 * is not recoverable once gone — the messages cascade with it — and the delete
 * icon sits next to the rename icon, a few pixels from a button someone clicks
 * on purpose. The confirmation is inline rather than a `window.confirm`, which
 * blocks the tab and reads as a browser error to anyone not expecting it.
 */
export function ConversationList({
  conversations,
  activeId,
  onOpen,
  onDelete,
  onRename,
}: ConversationListProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editingId) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editingId]);

  function startEditing(conversation: SavedConversation) {
    setConfirmingId(null);
    setEditingId(conversation.id);
    setDraft(conversation.title);
  }

  function commit() {
    if (!editingId) return;
    const title = draft.trim();
    const previous = conversations.find((c) => c.id === editingId)?.title ?? "";
    setEditingId(null);
    // An unchanged or emptied title is not a rename. Sending it anyway would
    // spend a request to overwrite a value with itself, and an empty one would
    // silently become "New conversation" — losing the title to a stray
    // select-all rather than to an intention.
    if (!title || title === previous) return;
    onRename(editingId, title);
  }

  return (
    <div className="space-y-0.5">
      {conversations.map((conversation) => {
        const active = conversation.id === activeId;
        const editing = conversation.id === editingId;
        const confirming = conversation.id === confirmingId;
        return (
          <div
            key={conversation.id}
            className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
              active ? "bg-slate-100" : "hover:bg-slate-50"
            }`}
          >
            {editing ? (
              <input
                ref={inputRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    commit();
                  }
                  // Escape abandons the edit without writing anything, which is
                  // the only way out that does not require knowing the old
                  // title well enough to retype it.
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setEditingId(null);
                  }
                }}
                maxLength={200}
                aria-label="Conversation title"
                className="flex-1 min-w-0 bg-white border border-slate-300 rounded px-1.5 py-0.5 text-xs text-slate-900 focus:outline-none focus:ring-1 focus:ring-slate-400"
              />
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => onOpen(conversation.id)}
                  onDoubleClick={() => startEditing(conversation)}
                  className="flex-1 min-w-0 text-left"
                  title={conversation.title}
                >
                  <div
                    className={`truncate ${active ? "text-slate-900 font-medium" : "text-slate-700"}`}
                  >
                    {conversation.title}
                  </div>
                  <div className="text-[10px] text-slate-400">
                    {relativeTime(conversation.updatedAt)}
                    {conversation.memory?.turns
                      ? ` · ${conversation.memory.turns} turn${
                          conversation.memory.turns === 1 ? "" : "s"
                        }`
                      : ""}
                  </div>
                </button>
                {confirming ? (
                  // Asking in the row itself. Shown unconditionally rather than
                  // on hover: a question that hides when the pointer moves is
                  // one someone answers by accident.
                  <span
                    className="flex items-center gap-1 flex-shrink-0"
                    onKeyDown={(event) => {
                      if (event.key === "Escape") setConfirmingId(null);
                    }}
                    onBlur={(event) => {
                      // Dismiss only when focus leaves the pair entirely —
                      // tabbing from Cancel to Delete must not close the
                      // question before it can be answered.
                      if (
                        !event.currentTarget.contains(event.relatedTarget as Node)
                      ) {
                        setConfirmingId(null);
                      }
                    }}
                  >
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setConfirmingId(null);
                        onDelete(conversation.id);
                      }}
                      className="px-1.5 py-0.5 rounded bg-rose-600 text-white text-[10px] font-medium hover:bg-rose-700"
                      aria-label={`Confirm deleting: ${conversation.title}`}
                    >
                      Delete
                    </button>
                    {/* Focus lands here, not on Delete. A confirmation that
                        starts with the destructive button focused turns a
                        stray Enter into the thing it was meant to prevent. */}
                    <button
                      type="button"
                      autoFocus
                      onClick={(event) => {
                        event.stopPropagation();
                        setConfirmingId(null);
                      }}
                      className="px-1.5 py-0.5 rounded border border-slate-300 text-slate-600 text-[10px] hover:bg-slate-100"
                      aria-label="Keep this conversation"
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <>
                    {/* Both actions appear on hover, and on focus so they stay
                        reachable by keyboard rather than being mouse-only. */}
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        startEditing(conversation);
                      }}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-slate-400 hover:text-slate-700 p-0.5 rounded flex-shrink-0"
                      title="Rename this conversation"
                      aria-label={`Rename conversation: ${conversation.title}`}
                    >
                      <span className="material-symbols-outlined text-[15px]">edit</span>
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        // Asks rather than deletes. One more conversation is
                        // never restored by an undo that does not exist.
                        setEditingId(null);
                        setConfirmingId(conversation.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-slate-400 hover:text-rose-600 p-0.5 rounded flex-shrink-0"
                      title="Delete this conversation"
                      aria-label={`Delete conversation: ${conversation.title}`}
                    >
                      <span className="material-symbols-outlined text-[15px]">delete</span>
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
