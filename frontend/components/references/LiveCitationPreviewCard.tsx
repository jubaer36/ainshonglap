import type { Citation } from "@/lib/types";

interface LiveCitationPreviewCardProps {
  citation: Citation | null;
}

export function LiveCitationPreviewCard({ citation }: LiveCitationPreviewCardProps) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm space-y-3">
      <div className="flex items-center justify-between pb-2 border-b border-slate-100">
        <div className="flex items-center gap-1.5">
          <span className="material-symbols-outlined text-amber-600 text-[18px]">
            find_in_page
          </span>
          <h3 className="font-medium text-xs text-slate-900">Live Citation Preview</h3>
        </div>
        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
          {citation ? citation.badge : "Interactive"}
        </span>
      </div>

      {citation ? (
        <>
          <div className="space-y-1">
            <h4 className="font-medium text-sm text-slate-900">{citation.title}</h4>
            <p className="text-[11px] font-mono text-slate-500">{citation.source}</p>
          </div>
          <div className="p-3 bg-amber-50/70 border border-amber-200/80 rounded-lg text-xs leading-relaxed text-amber-950 font-serif">
            {citation.text}
          </div>
          <div className="text-[11px] text-slate-500 leading-relaxed">{citation.note}</div>
        </>
      ) : (
        <div className="p-2.5 rounded-lg bg-slate-50 border border-dashed border-slate-200 text-[11px] text-slate-500 flex items-start gap-1.5">
          <span className="text-sm">💡</span>
          <span>
            Hover or click any underlined section in the chat, or a statute in the sidebar, to see
            its official text here.
          </span>
        </div>
      )}
    </div>
  );
}
