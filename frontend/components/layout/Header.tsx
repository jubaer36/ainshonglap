"use client";

interface HeaderProps {
  activeTopic: string | null;
  drawerOpen: boolean;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onToggleDrawer: () => void;
}

import { AuthButton } from "../auth/AuthButton";
import { BrandMark } from "@/components/brand/BrandMark";

export function Header({
  activeTopic,
  drawerOpen,
  sidebarOpen,
  onToggleSidebar,
  onToggleDrawer,
}: HeaderProps) {
  return (
    // Two groups that must never overlap: the left one (toggle, logo, name,
    // topic) is the one allowed to shrink, the right one (actions) keeps its
    // size. On a phone the name truncates before anything collides with it.
    <header className="h-14 bg-white border-b border-slate-200 px-2 sm:px-4 md:px-6 flex items-center justify-between gap-2 flex-shrink-0 z-30">
      <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
        <button
          type="button"
          // `lg:hidden` used to sit here, so the sidebar could only be
          // collapsed on small screens — on a desktop it was permanent.
          className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500 hover:text-slate-800"
          onClick={onToggleSidebar}
          title={sidebarOpen ? "Hide conversations" : "Show conversations"}
          aria-expanded={sidebarOpen}
        >
          <span className="material-symbols-outlined text-[20px]">
            {sidebarOpen ? "left_panel_close" : "left_panel_open"}
          </span>
        </button>
        <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
          <BrandMark size={32} eager className="rounded-lg shadow-sm" />
          <h1 className="truncate font-serif font-semibold text-slate-900 text-sm tracking-tight">
            আইনসংলাপ
          </h1>
        </div>
        {activeTopic && (
          <>
            <div className="h-4 w-px bg-slate-200 mx-2 hidden lg:block flex-shrink-0" />
            <div className="hidden lg:block truncate max-w-xs xl:max-w-sm text-xs text-slate-500 min-w-0">
              <span className="font-medium text-slate-700">{activeTopic}</span>
            </div>
          </>
        )}
      </div>
      <div className="flex items-center gap-1 sm:gap-2 flex-shrink-0">
        <AuthButton />
        <button
          type="button"
          // Printing is a desk action; on a phone it is the button to drop.
          className="hidden sm:block p-2 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors"
          onClick={() => window.print()}
          title="Export Synopsis"
        >
          <span className="material-symbols-outlined text-[18px]">download</span>
        </button>
        <div className="h-4 w-px bg-slate-200 mx-1 hidden sm:block" />
        <button
          type="button"
          className="flex items-center gap-1.5 whitespace-nowrap px-2 sm:px-2.5 py-1.5 text-xs font-medium rounded-lg text-slate-700 hover:bg-slate-100 border border-slate-200/80 transition-colors"
          onClick={onToggleDrawer}
          title={drawerOpen ? "Hide references" : "Show references"}
          aria-expanded={drawerOpen}
        >
          <span className="material-symbols-outlined text-[17px] text-slate-600">book_2</span>
          {/* The label only where the header has spare width; the icon and
              its tooltip carry it everywhere else. */}
          <span className="hidden xl:inline">
            {drawerOpen ? "Hide references" : "References"}
          </span>
        </button>
      </div>
    </header>
  );
}
