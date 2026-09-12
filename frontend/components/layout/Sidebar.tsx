const RECENT_CONSULTATIONS = [
  { label: "FIR Filing & Police Mandate", active: true },
  { label: "Anticipatory Bail under CrPC S. 498", active: false },
  { label: "Nari O Shishu Sec. 9(1) Rape Trial", active: false },
  { label: "Cyber Security Act & Electronic Evidence", active: false },
  { label: "Evidence Act S. 25 & Police Confessions", active: false },
];

const STATUTORY_CODES = [
  { key: "crpc-154", label: "Code of Criminal Procedure (1898)", short: "CrPC", icon: "balance" },
  { key: "penal-302", label: "The Penal Code (1860)", short: "PPC", icon: "gavel" },
  { key: "evidence-25", label: "The Evidence Act (1872)", short: "Evid.", icon: "fact_check" },
  { key: "csa-2023", label: "Cyber Security Act (2023)", short: "CSA", icon: "lock" },
];

interface SidebarProps {
  open: boolean;
  onNewChat: () => void;
  onActivateCitation: (key: string) => void;
}

export function Sidebar({ open, onNewChat, onActivateCitation }: SidebarProps) {
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
          <span>New Query</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <div>
          <div className="flex items-center justify-between px-1 mb-1.5">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
              Recent Consultations
            </span>
          </div>
          <div className="space-y-1">
            {RECENT_CONSULTATIONS.map((item) => (
              <button
                key={item.label}
                type="button"
                title="Demo placeholder — conversation history isn't wired up yet"
                className={`w-full flex items-center justify-between p-2 rounded-lg text-left text-xs transition-colors ${
                  item.active
                    ? "bg-slate-100/90 text-slate-900 font-medium"
                    : "hover:bg-slate-50 text-slate-600 hover:text-slate-900"
                }`}
              >
                <span className="flex items-center gap-2 min-w-0 flex-1">
                  <span
                    className={`material-symbols-outlined text-[16px] flex-shrink-0 ${
                      item.active ? "text-slate-600" : "text-slate-400"
                    }`}
                  >
                    {item.active ? "chat_bubble" : "forum"}
                  </span>
                  <span className="truncate">{item.label}</span>
                </span>
                {item.active && (
                  <span className="w-1.5 h-1.5 rounded-full bg-slate-900 flex-shrink-0" />
                )}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between px-1 mb-1.5">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
              Primary Statutory Enactments
            </span>
          </div>
          <div className="space-y-1 text-xs text-slate-600">
            {STATUTORY_CODES.map((code) => (
              <button
                key={code.key}
                type="button"
                className="w-full flex items-center justify-between p-2 rounded-lg hover:bg-slate-50 hover:text-slate-900 text-left transition-colors"
                onClick={() => onActivateCitation(code.key)}
              >
                <span className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="material-symbols-outlined text-[16px] text-slate-400 flex-shrink-0">
                    {code.icon}
                  </span>
                  <span className="truncate">{code.label}</span>
                </span>
                <span className="font-mono text-[10px] text-slate-400 flex-shrink-0 ml-2">
                  {code.short}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="p-3 border-t border-slate-200 bg-slate-50/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-slate-900 text-white flex items-center justify-center font-semibold text-xs">
              BA
            </div>
            <div className="overflow-hidden">
              <div className="font-medium text-xs text-slate-800 truncate">
                Barrister / Advocate
              </div>
              <div className="text-[11px] text-slate-500 truncate">
                Supreme Court of Bangladesh
              </div>
            </div>
          </div>
          <button type="button" className="p-1 rounded text-slate-400 hover:text-slate-700" title="Preferences">
            <span className="material-symbols-outlined text-[18px]">tune</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
