"use client";

interface HeaderProps {
  activeTopic: string | null;
  drawerOpen: boolean;
  onToggleSidebar: () => void;
  onToggleDrawer: () => void;
}

export function Header({ activeTopic, drawerOpen, onToggleSidebar, onToggleDrawer }: HeaderProps) {
  function handleShare() {
    const shareData = {
      title: "AinShonglap Bangladesh Legal Brief",
      text: activeTopic ?? "AinShonglap legal consultation",
      url: window.location.href,
    };
    if (navigator.share) {
      navigator.share(shareData).catch(() => {});
    } else {
      navigator.clipboard.writeText(window.location.href);
    }
  }

  return (
    <header className="h-14 bg-white border-b border-slate-200 px-4 md:px-6 flex items-center justify-between flex-shrink-0 z-30">
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500 hover:text-slate-800 lg:hidden"
          onClick={onToggleSidebar}
          title="Toggle Sidebar"
        >
          <span className="material-symbols-outlined text-[20px]">menu</span>
        </button>
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-slate-900 text-white flex items-center justify-center font-serif font-bold text-base shadow-sm">
            আইন
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-serif font-semibold text-slate-900 text-sm tracking-tight">
                AinShonglap
              </h1>
              <span className="text-[11px] font-mono text-slate-400">
                Bangladesh criminal law
              </span>
            </div>
          </div>
        </div>
        {activeTopic && (
          <>
            <div className="h-4 w-px bg-slate-200 mx-2 hidden md:block" />
            <div className="hidden md:block truncate max-w-sm text-xs text-slate-500 min-w-0">
              <span className="font-medium text-slate-700">{activeTopic}</span>
            </div>
          </>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="p-2 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors"
          onClick={handleShare}
          title="Share Brief"
        >
          <span className="material-symbols-outlined text-[18px]">share</span>
        </button>
        <button
          type="button"
          className="p-2 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors"
          onClick={() => window.print()}
          title="Export Synopsis"
        >
          <span className="material-symbols-outlined text-[18px]">download</span>
        </button>
        <div className="h-4 w-px bg-slate-200 mx-1 hidden sm:block" />
        <button
          type="button"
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg text-slate-700 hover:bg-slate-100 border border-slate-200/80 transition-colors"
          onClick={onToggleDrawer}
        >
          <span className="material-symbols-outlined text-[17px] text-slate-600">book_2</span>
          <span className="hidden md:inline">
            {drawerOpen ? "References & Citations" : "Show References"}
          </span>
        </button>
      </div>
    </header>
  );
}
