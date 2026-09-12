"use client";

interface FirstTimeNoticeProps {
  onDismiss: () => void;
}

export function FirstTimeNotice({ onDismiss }: FirstTimeNoticeProps) {
  return (
    <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/90 flex items-start gap-3">
      <div className="w-6 h-6 rounded-md bg-slate-200/80 flex items-center justify-center flex-shrink-0 text-slate-700 mt-0.5">
        <span className="material-symbols-outlined text-[16px]">info</span>
      </div>
      <div className="flex-1 text-xs space-y-0.5">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-slate-900">Legal Intelligence Notice</span>
          <button
            type="button"
            className="text-slate-400 hover:text-slate-700 p-0.5 rounded"
            onClick={onDismiss}
            title="Dismiss notice"
          >
            <span className="material-symbols-outlined text-[15px]">close</span>
          </button>
        </div>
        <p className="text-slate-600 leading-relaxed">
          Clear plain-language answers verified against Bangladeshi codified law. Hover over any
          highlighted section pill to inspect the verbatim statute in the right panel.
        </p>
      </div>
    </div>
  );
}
