"use client";

import { relativeTime, type SavedConversation } from "@/lib/conversations";

interface ConversationListProps {
  conversations: SavedConversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

/**
 * Saved conversations, newest first.
 *
 * Each row shows the first question asked and how long ago it was touched.
 * Titles are the question itself rather than a generated summary: the person
 * wrote it, they will recognise it, and a model call to rephrase what they
 * already typed would cost latency and quota to make recall harder.
 */
export function ConversationList({
  conversations,
  activeId,
  onOpen,
  onDelete,
}: ConversationListProps) {
  return (
    <div className="space-y-0.5">
      {conversations.map((conversation) => {
        const active = conversation.id === activeId;
        return (
          <div
            key={conversation.id}
            className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
              active ? "bg-slate-100" : "hover:bg-slate-50"
            }`}
          >
            <button
              type="button"
              onClick={() => onOpen(conversation.id)}
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
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onDelete(conversation.id);
              }}
              // Visible on hover only, and on focus so it stays reachable by
              // keyboard rather than being effectively mouse-only.
              className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-slate-400 hover:text-rose-600 p-0.5 rounded flex-shrink-0"
              title="Delete this conversation"
              aria-label={`Delete conversation: ${conversation.title}`}
            >
              <span className="material-symbols-outlined text-[15px]">delete</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}
