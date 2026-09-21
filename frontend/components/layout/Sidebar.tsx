import type { SavedConversation } from "@/lib/conversations";
import { ConversationList } from "./ConversationList";
import { StatuteBrowser } from "./StatuteBrowser";

interface SidebarProps {
  open: boolean;
  onNewChat: () => void;
  onActivateCitation: (key: string) => void;
  conversations: SavedConversation[];
  activeConversationId: string | null;
  onOpenConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
}

export function Sidebar({
  open,
  onNewChat,
  onActivateCitation,
  conversations,
  activeConversationId,
  onOpenConversation,
  onDeleteConversation,
}: SidebarProps) {
  return (
    <aside
      className={`w-72 bg-white border-r border-slate-200 flex-col justify-between flex-shrink-0 fixed top-14 bottom-0 left-0 z-40 lg:static lg:z-20 ${
        open ? "flex" : "hidden"
      }`}
    >
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

    </aside>
  );
}
