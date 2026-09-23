"use client";

import type { SavedConversation } from "@/lib/conversations";
import { ConversationList } from "./ConversationList";
import { ResizeHandle, useResizableWidth } from "./ResizeHandle";
import { SidebarAccount } from "./SidebarAccount";
import { StatuteBrowser } from "./StatuteBrowser";

interface SidebarProps {
  /** null: follow the screen — docked from lg, collapsed below. */
  open: boolean | null;
  onNewChat: () => void;
  onActivateCitation: (key: string) => void;
  conversations: SavedConversation[];
  activeConversationId: string | null;
  onOpenConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
}

// Width bounds for the drag handle. The floor keeps conversation titles and the
// statute tree legible; the ceiling keeps the chat column the widest thing on
// screen, since that is where the answers are read.
const MIN_WIDTH = 220;
const MAX_WIDTH = 520;
const DEFAULT_WIDTH = 288; // w-72

export function Sidebar({
  open,
  onNewChat,
  onActivateCitation,
  conversations,
  activeConversationId,
  onOpenConversation,
  onDeleteConversation,
  onRenameConversation,
}: SidebarProps) {
  const panel = useResizableWidth({ min: MIN_WIDTH, max: MAX_WIDTH, initial: DEFAULT_WIDTH });

  return (
    <aside
      style={{ width: panel.width }}
      className={`bg-white border-r border-slate-200 flex-col justify-between flex-shrink-0 fixed top-14 bottom-0 left-0 z-40 lg:relative lg:top-auto lg:bottom-auto lg:z-20 ${
        open === null ? "hidden lg:flex" : open ? "flex" : "hidden"
      }`}
    >
      <ResizeHandle
        edge="right"
        width={panel.width}
        min={panel.min}
        max={panel.max}
        initial={panel.initial}
        onResize={panel.setWidth}
        label="Resize sidebar"
      />
      <div className="p-3 border-b border-slate-100">
        <button
          type="button"
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-slate-900 text-white hover:bg-slate-800 text-xs font-medium shadow-sm transition-all"
          onClick={onNewChat}
        >
          <span className="material-symbols-outlined text-[16px]">add</span>
          <span>New conversation</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {conversations.length > 0 && (
          <div>
            <div className="flex items-center justify-between px-1 mb-1.5">
              <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Conversations
              </span>
            </div>
            <ConversationList
              conversations={conversations}
              activeId={activeConversationId}
              onOpen={onOpenConversation}
              onDelete={onDeleteConversation}
              onRename={onRenameConversation}
            />
          </div>
        )}

        <div>
          <div className="flex items-center justify-between px-1 mb-1.5">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
              Indexed Statutes
            </span>
          </div>
          <StatuteBrowser onActivateCitation={onActivateCitation} />
        </div>
      </div>

      <SidebarAccount />
    </aside>
  );
}
